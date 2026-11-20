"""GroqClient, the strict schema and provider selection, against a mocked HTTP transport."""

import json

import httpx
import pytest

from src.brief import llm as L
from src.brief.chat import Extraction, PrefExtraction


def _client(handler, model="openai/gpt-oss-120b", monkeypatch=None):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return L.GroqClient(model=model, api_key="test-key", http=http)


def _ok(content, tokens=1200):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": tokens}})


def _objects(node):
    if isinstance(node, dict):
        if "properties" in node:
            yield node
        for v in node.values():
            yield from _objects(v)
    elif isinstance(node, list):
        for v in node:
            yield from _objects(v)


@pytest.mark.parametrize("schema", [Extraction, PrefExtraction])
def test_strict_schema_lists_every_property_and_closes_objects(schema):
    s = L.strict_schema(schema)
    objs = list(_objects(s))
    assert objs, "schema has objects"
    for o in objs:
        assert sorted(o["required"]) == sorted(o["properties"])
        assert o["additionalProperties"] is False
    text = json.dumps(s)
    assert '"default"' not in text and '"minimum"' not in text and '"exclusiveMinimum"' not in text
    for node in _walk(s):
        if "$ref" in node:
            assert list(node) == ["$ref"]


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def test_groq_sends_strict_schema_at_temperature_zero_and_returns_content():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _ok('{"bedrooms": 3}')

    c = _client(handler)
    assert c.complete_json("sys", "three bedrooms", Extraction) == '{"bedrooms": 3}'
    body = seen["body"]
    assert seen["auth"] == "Bearer test-key"
    assert body["model"] == "openai/gpt-oss-120b" and body["temperature"] == 0
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["reasoning_effort"] == "low"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert c.limiter.calls[-1][1] == 1200


def test_groq_unlisted_model_uses_json_object_mode_with_schema_in_prompt():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _ok("{}")

    _client(handler, model="some/other-model").complete_json("sys", "hi", Extraction)
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert "JSON schema" in seen["body"]["messages"][0]["content"]


def test_groq_retries_429_after_the_server_pause(monkeypatch):
    sleeps, replies = [], [httpx.Response(429, headers={"retry-after": "3"}, json={"error": {"message": "slow down"}}), _ok("{}")]
    monkeypatch.setattr(L.time, "sleep", sleeps.append)
    c = _client(lambda r: replies.pop(0))
    assert c.complete_json("s", "p", Extraction) == "{}"
    assert sleeps == [3.0]


def test_groq_drops_the_reasoning_setting_if_the_model_refuses_it():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, json={"error": {"message": "reasoning_effort is not supported"}})
        return _ok("{}")

    assert _client(handler, model="qwen/qwen3.8-27b").complete_json("s", "p", Extraction) == "{}"
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]


def test_groq_other_client_errors_raise_llm_error():
    c = _client(lambda r: httpx.Response(401, json={"error": {"message": "Invalid API Key"}}))
    with pytest.raises(L.LLMError, match="401 Invalid API Key"):
        c.complete_json("s", "p", Extraction)


def test_groq_needs_a_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(L.LLMError, match="GROQ_API_KEY"):
        L.GroqClient()


def test_make_client_picks_the_provider(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.setenv("STRATA_LLM_PROVIDER", "groq")
    monkeypatch.delenv("STRATA_LLM_MODEL", raising=False)
    c = L.make_client()
    assert isinstance(c, L.GroqClient) and c.model == L.GROQ_DEFAULT_MODEL
    assert L.make_client(model="openai/gpt-oss-20b").model == "openai/gpt-oss-20b"
    with pytest.raises(L.LLMError, match="unknown LLM provider"):
        L.make_client("nope")


def test_rate_limiter_stops_at_the_daily_token_limit():
    t = [0.0]
    lim = L.RateLimiter(100, 10**9, 100, tpd=5_000, clock=lambda: t[0], sleep=lambda s: None)
    lim.wait(2_000); lim.record(2_000)
    lim.wait(2_000); lim.record(2_000)
    with pytest.raises(L.LLMError, match="daily token limit"):
        lim.wait(2_000)


def test_free_text_call_has_no_json_format_and_the_long_output_cap():
    """Phase 9 arm A on Groq: prose and code back, capped to fit the free plan's per-minute limit."""
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return _ok("```python\nx = 1\n```")

    c = _client(handler)
    assert c.complete_text(None, "write code") == "```python\nx = 1\n```"
    body = sent[0]
    assert "response_format" not in body and body["max_completion_tokens"] == c.long_output == L.GROQ_LONG_OUTPUT
    assert body["messages"] == [{"role": "user", "content": "write code"}]

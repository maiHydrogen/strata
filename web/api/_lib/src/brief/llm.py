"""LLM clients for the brief chatbot. The rest of STRATA sees only `complete_json`.

The LLM only ever returns JSON matching a given schema; the caller validates it with
pydantic and never executes anything it returns. Providers are swappable: Gemini
(Google AI Studio free tier, the default) and Groq (free Developer plan, open-weight models);
`make_client` picks one from STRATA_LLM_PROVIDER. `ScriptedClient` replays canned responses
so tests run offline.
"""

import json
import os
import time
from dataclasses import dataclass, field

import httpx


# On the free tier; some newer Flash models listed for the same key have a quota of 0.
DEFAULT_MODEL = "gemma-4-26b-a4b-it"
TIMEOUT_MS = 90_000
# Free-tier limits for Gemma 4 26B (AI Studio, Nov 2026): 30 requests/min, 16K tokens/min, 14.4K requests/day.
# We stay at about 80% of each so bursts never hit a 429.
LIMITS = {"gemma-4-26b-a4b-it": (24, 13_000, 14_000)}
DEFAULT_LIMITS = (4, 200_000, 18)  # unknown model: assume a small Flash-like quota


class RateLimiter:
    """Client-side pacing: waits before a call so the last minute stays under the request and
    token limits and the day under the daily request limit. Tokens are counted after each call."""

    def __init__(self, rpm, tpm, rpd, tpd=None, clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.tpm, self.rpd, self.tpd = rpm, tpm, rpd, tpd
        self.clock, self.sleep = clock, sleep
        self.calls = []  # (time, tokens) in the last 24 h

    def wait(self, expected_tokens):
        while True:
            now = self.clock()
            self.calls = [c for c in self.calls if now - c[0] < 86_400]
            if len(self.calls) >= self.rpd:
                raise LLMError(f"daily request limit ({self.rpd}) reached")
            if self.tpd and sum(t for _, t in self.calls) + expected_tokens > self.tpd:
                raise LLMError(f"daily token limit ({self.tpd}) reached")
            minute = [c for c in self.calls if now - c[0] < 60]
            if len(minute) < self.rpm and sum(t for _, t in minute) + expected_tokens <= self.tpm:
                return
            self.sleep(max(0.5, 60 - (now - minute[0][0])))

    def record(self, tokens):
        self.calls.append((self.clock(), tokens))

    def typical_tokens(self):
        recent = [t for _, t in self.calls[-5:] if t]
        return max(recent) if recent else 1500


class LLMError(RuntimeError):
    pass


@dataclass
class ScriptedClient:
    """Returns the queued responses in order (tests and dry runs)."""
    responses: list
    model: str = "scripted"
    calls: list = field(default_factory=list)

    def complete_json(self, system, prompt, schema, max_tokens=None):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise LLMError("scripted client has no responses left")
        r = self.responses.pop(0)
        return r if isinstance(r, str) else json.dumps(r)

    def complete_text(self, system, prompt, max_tokens=None):
        return self.complete_json(system, prompt, None)


# An extraction is a few hundred tokens; a cap stops a runaway answer in seconds instead of minutes.
MAX_OUTPUT = 1024
LONG_OUTPUT = 12_288  # Phase 9 arms that return a whole model or layout (Gemma free tier: ~20 min)
_BOUNDS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}


def model_schema(schema):
    """The JSON schema sent to the model, without numeric bounds.

    Gemma's constrained decoding stalls on bounded integers (it writes '"bedrooms":' and then
    only spaces until the token limit). The bounds stay in the pydantic model, which checks
    every answer and sends a failing one back with the error.
    """
    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in _BOUNDS}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    return strip(schema.model_json_schema())


class GeminiClient:
    """Google Gemini via the google-genai SDK; key from GEMINI_API_KEY, model from STRATA_LLM_MODEL.

    Temperature 0 and a pinned model, as the plan requires. Rate-limit (429) and server
    errors are retried with a growing pause, since the free tier allows few requests a minute.
    """

    name = "gemini"

    def __init__(self, model=None, api_key=None, retries=4, timeout_ms=TIMEOUT_MS):
        from google import genai  # imported here so offline tests do not need the SDK configured

        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set (put it in .env)")
        self._genai = genai
        # without a timeout a stalled free-tier request hangs forever
        self.client = genai.Client(api_key=key, http_options=genai.types.HttpOptions(timeout=timeout_ms))
        self.retries = retries
        self.model = model or os.environ.get("STRATA_LLM_MODEL") or DEFAULT_MODEL
        self.limiter = RateLimiter(*LIMITS.get(self.model, DEFAULT_LIMITS))
        self.on_retry = lambda message: None  # e.g. print, to see a long wait in a batch run
        self.long_output = LONG_OUTPUT

    def complete_json(self, system, prompt, schema, max_tokens=None):
        from google.genai import types

        return self._generate(prompt, types.GenerateContentConfig(
            system_instruction=system, temperature=0.0, response_mime_type="application/json",
            response_json_schema=model_schema(schema), max_output_tokens=max_tokens or MAX_OUTPUT))

    def complete_text(self, system, prompt, max_tokens=None):
        """Free text, for the Phase 9 baseline arm only (its agents answer in prose and code)."""
        from google.genai import types

        return self._generate(prompt, types.GenerateContentConfig(
            system_instruction=system or None, temperature=0.0, max_output_tokens=max_tokens or self.long_output))

    def _generate(self, prompt, config):
        from google.genai import errors

        for attempt in range(self.retries + 1):
            try:
                self.limiter.wait(self.limiter.typical_tokens())
                try:
                    response = self.client.models.generate_content(model=self.model, contents=prompt, config=config)
                except Exception:
                    self.limiter.record(0)  # a failed request still counts against the quota
                    raise
                usage = getattr(response, "usage_metadata", None)
                self.limiter.record(getattr(usage, "total_token_count", None) or self.limiter.typical_tokens())
                return response.text
            except errors.APIError as e:
                if e.code not in (429, 500, 503, 504) or attempt == self.retries:
                    raise LLMError(f"Gemini {self.model}: {e.code} {e.message}") from e
                self.on_retry(f"Gemini {e.code}, try {attempt + 2} of {self.retries + 1}")
                time.sleep(min(60, 5 * 2 ** attempt))
            except httpx.TimeoutException as e:
                if attempt == self.retries:
                    raise LLMError(f"Gemini {self.model}: timed out after {self.retries + 1} tries") from e
                self.on_retry(f"Gemini timed out, try {attempt + 2} of {self.retries + 1}")
                time.sleep(5)
        raise LLMError("unreachable")


# Groq free (Developer plan) limits, Nov 2026: 30 requests/min, 1K/day, 8K tokens/min, 200K tokens/day
# for each of these models, counted separately per model. About 80% of each, as for Gemini.
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"
GROQ_LIMITS = {m: (24, 6_500, 800, 160_000) for m in
               ("openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b")}
GROQ_DEFAULT_LIMITS = (10, 4_000, 300, 80_000)
# Models with strict structured outputs (constrained decoding to the schema); others get JSON object mode.
GROQ_STRICT = {"openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"}
# Keep the hidden reasoning short: it counts against the output cap and the token quota.
GROQ_EXTRA = {"openai/gpt-oss-120b": {"reasoning_effort": "low"},
              "openai/gpt-oss-20b": {"reasoning_effort": "low"},
              "qwen/qwen3.8-27b": {"reasoning_effort": "none"}}
GROQ_MAX_OUTPUT = 2048  # reasoning tokens share this cap
# Phase 9 arms that return a whole model or plan. On the free plan a request (prompt + output cap) above the
# 8K tokens/min limit is refused outright, so the cap stays near 5K; raise it on a paid plan.
GROQ_LONG_OUTPUT = int(os.environ.get("STRATA_GROQ_LONG_OUTPUT", "5000"))


def strict_schema(schema):
    """`model_schema` reshaped for strict structured outputs: every property listed in `required`
    (optional ones are already a union with null), `additionalProperties: false` on every object,
    no defaults, and nothing beside a `$ref`. Pydantic still checks the answer as before."""
    def fix(node):
        if isinstance(node, list):
            return [fix(v) for v in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return {"$ref": node["$ref"]}
        out = {k: fix(v) for k, v in node.items() if k not in ("default", "title")}
        if "properties" in node:
            out["properties"] = {k: fix(v) for k, v in node["properties"].items()}
            out["required"] = list(node["properties"])
            out["additionalProperties"] = False
        if "$defs" in node:
            out["$defs"] = {k: fix(v) for k, v in node["$defs"].items()}
        return out
    return fix(model_schema(schema))


class GroqClient:
    """Groq's OpenAI-compatible chat API over httpx; key from GROQ_API_KEY, model from
    STRATA_LLM_MODEL (default gpt-oss-120b). Temperature 0, strict schema output where the model
    supports it, the same retries and client-side pacing as GeminiClient."""

    name = "groq"

    def __init__(self, model=None, api_key=None, retries=4, http=None):
        self.key = api_key or os.environ.get("GROQ_API_KEY")
        if not self.key:
            raise LLMError("GROQ_API_KEY is not set (put it in .env)")
        self.model = model or os.environ.get("STRATA_LLM_MODEL") or GROQ_DEFAULT_MODEL
        self.retries = retries
        self.http = http or httpx.Client(timeout=TIMEOUT_MS / 1000)
        self.limiter = RateLimiter(*GROQ_LIMITS.get(self.model, GROQ_DEFAULT_LIMITS))
        self.extra = dict(GROQ_EXTRA.get(self.model, {}))
        self.long_output = GROQ_LONG_OUTPUT
        self.on_retry = lambda message: None

    def _body(self, system, prompt, schema, max_tokens=None):
        if self.model in GROQ_STRICT:
            fmt = {"type": "json_schema",
                   "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)}}
        else:
            fmt = {"type": "json_object"}
            system = f"{system}\n\nReturn one JSON object matching this JSON schema:\n{json.dumps(model_schema(schema))}"
        return {"model": self.model, "temperature": 0, "max_completion_tokens": max_tokens or GROQ_MAX_OUTPUT,
                "response_format": fmt, **self.extra,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}

    def complete_json(self, system, prompt, schema, max_tokens=None):
        return self._post(lambda: self._body(system, prompt, schema, max_tokens))

    def complete_text(self, system, prompt, max_tokens=None):
        """Free text, for the Phase 9 baseline arm only (its agents answer in prose and code)."""
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
        return self._post(lambda: {"model": self.model, "temperature": 0, **self.extra,
                                   "max_completion_tokens": max_tokens or self.long_output, "messages": messages})

    def _post(self, make_body):
        for attempt in range(self.retries + 1):
            self.limiter.wait(self.limiter.typical_tokens())
            try:
                r = self.http.post(GROQ_URL, json=make_body(), headers={"Authorization": f"Bearer {self.key}"})
            except httpx.TransportError as e:
                self.limiter.record(0)
                if attempt == self.retries:
                    raise LLMError(f"Groq {self.model}: {type(e).__name__} after {self.retries + 1} tries") from e
                self.on_retry(f"Groq {type(e).__name__}, try {attempt + 2} of {self.retries + 1}")
                time.sleep(5)
                continue
            if r.status_code == 200:
                data = r.json()
                self.limiter.record((data.get("usage") or {}).get("total_tokens") or self.limiter.typical_tokens())
                return data["choices"][0]["message"]["content"] or ""
            self.limiter.record(0)
            message = _groq_error(r)
            if r.status_code == 400 and self.extra and "reasoning" in message.lower():
                self.extra = {}  # this model does not take the reasoning setting: retry without it
                continue
            if r.status_code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                raise LLMError(f"Groq {self.model}: {r.status_code} {message}")
            self.on_retry(f"Groq {r.status_code}, try {attempt + 2} of {self.retries + 1}")
            wait = r.headers.get("retry-after")
            time.sleep(min(60, float(wait)) if wait and wait.replace(".", "", 1).isdigit() else min(60, 5 * 2 ** attempt))
        raise LLMError(f"Groq {self.model}: no answer after {self.retries + 1} tries")


def _groq_error(r):
    try:
        return r.json()["error"]["message"]
    except Exception:
        return r.text[:200]


PROVIDERS = {"gemini": GeminiClient, "groq": GroqClient}


def make_client(provider=None, model=None):
    """The LLM client named by `provider` or STRATA_LLM_PROVIDER (default gemini)."""
    name = (provider or os.environ.get("STRATA_LLM_PROVIDER") or "gemini").lower()
    if name not in PROVIDERS:
        raise LLMError(f"unknown LLM provider {name!r} (choose from {', '.join(PROVIDERS)})")
    return PROVIDERS[name](model=model)

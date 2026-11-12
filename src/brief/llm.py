"""LLM clients for the brief chatbot. The rest of STRATA sees only `complete_json`.

The LLM only ever returns JSON matching a given schema; the caller validates it with
pydantic and never executes anything it returns. Providers are swappable: Gemini
(Google AI Studio free tier) is the one in use; `ScriptedClient` replays canned
responses so tests run offline.
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

    def __init__(self, rpm, tpm, rpd, clock=time.monotonic, sleep=time.sleep):
        self.rpm, self.tpm, self.rpd = rpm, tpm, rpd
        self.clock, self.sleep = clock, sleep
        self.calls = []  # (time, tokens) in the last 24 h

    def wait(self, expected_tokens):
        while True:
            now = self.clock()
            self.calls = [c for c in self.calls if now - c[0] < 86_400]
            if len(self.calls) >= self.rpd:
                raise LLMError(f"daily request limit ({self.rpd}) reached")
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

    def complete_json(self, system, prompt, schema):
        self.calls.append({"system": system, "prompt": prompt, "schema": schema})
        if not self.responses:
            raise LLMError("scripted client has no responses left")
        r = self.responses.pop(0)
        return r if isinstance(r, str) else json.dumps(r)


# An extraction is a few hundred tokens; a cap stops a runaway answer in seconds instead of minutes.
MAX_OUTPUT = 1024
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

    def __init__(self, model=None, api_key=None, retries=4):
        from google import genai  # imported here so offline tests do not need the SDK configured

        key = api_key or os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY is not set (put it in .env)")
        self._genai = genai
        # without a timeout a stalled free-tier request hangs forever
        self.client = genai.Client(api_key=key, http_options=genai.types.HttpOptions(timeout=TIMEOUT_MS))
        self.retries = retries
        self.model = model or os.environ.get("STRATA_LLM_MODEL") or DEFAULT_MODEL
        self.limiter = RateLimiter(*LIMITS.get(self.model, DEFAULT_LIMITS))

    def complete_json(self, system, prompt, schema):
        from google.genai import errors, types

        config = types.GenerateContentConfig(
            system_instruction=system, temperature=0.0, response_mime_type="application/json",
            response_json_schema=model_schema(schema), max_output_tokens=MAX_OUTPUT)
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
                time.sleep(min(60, 5 * 2 ** attempt))
            except httpx.TimeoutException as e:
                if attempt == self.retries:
                    raise LLMError(f"Gemini {self.model}: timed out after {self.retries + 1} tries") from e
                time.sleep(5)
        raise LLMError("unreachable")

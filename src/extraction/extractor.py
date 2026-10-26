"""LLM-based intent extraction: few-shot prompted, not fine-tuned (see CLAUDE.md).

Import of the anthropic SDK is deferred to first use so the rest of the
package stays importable without it installed / without an API key set.
"""

from __future__ import annotations

from src.extraction.prompts import SYSTEM_PROMPT, build_prompt
from src.extraction.schema import ExtractionResult, Intent
from src.ingestion.schema import Document

DEFAULT_MODEL = "claude-opus-5"

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic

        _client = anthropic.Anthropic()
    return _client


def extract_intents(document: Document, model: str = DEFAULT_MODEL) -> list[Intent]:
    """Run intent extraction over an ingested Document's full text."""
    client = _get_client()

    response = client.messages.parse(
        model=model,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_prompt(document.full_text)}],
        output_format=ExtractionResult,
    )

    return response.parsed_output.intents

"""Tests the schema, prompt assembly, and extractor wiring with a mocked API response
(no real network call, no API key required)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.extraction.extractor import extract_intents
from src.extraction.prompts import build_prompt
from src.extraction.schema import ExtractionResult, Intent
from src.ingestion.schema import Block, Document, Page


def test_build_prompt_includes_examples_and_document_text():
    prompt = build_prompt("Construct a boundary wall of height 1.8m.")

    assert "Example 1:" in prompt
    assert "Construct a boundary wall of height 1.8m." in prompt


def test_intent_schema_defaults():
    intent = Intent(action="construct", object="wall")

    assert intent.specification == {}
    assert intent.quantity is None


def test_extract_intents_parses_mocked_response(monkeypatch):
    fake_result = ExtractionResult(
        intents=[
            Intent(
                action="construct",
                object="boundary wall",
                specification={"height": "1.8m"},
                quantity=None,
            )
        ]
    )
    fake_response = MagicMock(parsed_output=fake_result)

    fake_client = MagicMock()
    fake_client.messages.parse.return_value = fake_response

    import src.extraction.extractor as extractor_module

    monkeypatch.setattr(extractor_module, "_get_client", lambda: fake_client)

    doc = Document(
        source_path="fake.pdf",
        pages=[Page(number=1, width=612, height=792, blocks=[Block(text="Construct a boundary wall.", bbox=(0, 0, 1, 1))])],
    )

    intents = extract_intents(doc)

    assert len(intents) == 1
    assert intents[0].object == "boundary wall"
    fake_client.messages.parse.assert_called_once()

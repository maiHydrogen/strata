"""Tests the deterministic PyMuPDF path only — no OCR/LayoutLMv3 models required."""

from __future__ import annotations

import pymupdf as fitz
import pytest

from src.ingestion.loader import load_pdf, needs_ocr


@pytest.fixture
def sample_pdf(tmp_path):
    path = tmp_path / "sample.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Scope of Work: Construct a G+2 residential building.")
    doc.save(path)
    doc.close()
    return str(path)


def test_load_pdf_extracts_text(sample_pdf):
    doc = load_pdf(sample_pdf)

    assert len(doc.pages) == 1
    page = doc.pages[0]
    assert "Scope of Work" in page.raw_text
    assert page.source == "text"
    assert not needs_ocr(page)


def test_needs_ocr_flags_blank_page(tmp_path):
    path = tmp_path / "blank.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(path)
    doc.close()

    loaded = load_pdf(str(path))
    assert needs_ocr(loaded.pages[0])

from __future__ import annotations

from src.ingestion.loader import load_pdf, needs_ocr, render_page_image
from src.ingestion.ocr import ocr_page_image
from src.ingestion.layout import tag_page
from src.ingestion.schema import Block, Document, Page

__all__ = ["ingest", "Document", "Page", "Block"]


def ingest(path: str, tag_layout: bool = True) -> Document:
    """Load a PDF, filling in OCR for image-only pages, and tag block layout roles."""
    doc = load_pdf(path)

    for page in doc.pages:
        if needs_ocr(page):
            image_bytes = render_page_image(path, page.number)
            page.blocks = ocr_page_image(image_bytes)
            page.source = "ocr"

        if tag_layout:
            tag_page(page)

    return doc

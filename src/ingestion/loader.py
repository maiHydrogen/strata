"""PyMuPDF-based text/layout extraction, with a per-page emptiness check for the OCR fallback."""

from __future__ import annotations

import pymupdf as fitz

from src.ingestion.schema import Block, Document, Page

MIN_CHARS_PER_PAGE = 20  # below this, treat the page as scanned/image-only and defer to OCR


def load_pdf(path: str) -> Document:
    doc = Document(source_path=path)
    with fitz.open(path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            blocks = [
                Block(text=text.strip(), bbox=(x0, y0, x1, y1))
                for x0, y0, x1, y1, text, *_ in page.get_text("blocks")
                if text.strip()
            ]
            doc.pages.append(
                Page(
                    number=page_number,
                    width=page.rect.width,
                    height=page.rect.height,
                    blocks=blocks,
                    source="text",
                )
            )
    return doc


def render_page_image(path: str, page_number: int, dpi: int = 300):
    """Render a single page to a PIL Image, for handing to the OCR fallback."""
    with fitz.open(path) as pdf:
        page = pdf[page_number - 1]
        pix = page.get_pixmap(dpi=dpi)
        return pix.tobytes("png")


def needs_ocr(page: Page) -> bool:
    return len(page.raw_text) < MIN_CHARS_PER_PAGE

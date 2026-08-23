"""Data model shared by every ingestion component and consumed by stage 2 (extraction)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Block:
    text: str
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1)
    label: str | None = None  # layout role, e.g. "title", "paragraph", "table" — set by layout.py
    confidence: float | None = None


@dataclass
class Page:
    number: int
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)
    source: str = "text"  # "text" (PyMuPDF) or "ocr" (PaddleOCR fallback)

    @property
    def raw_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)


@dataclass
class Document:
    source_path: str
    pages: list[Page] = field(default_factory=list)

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.raw_text for p in self.pages)

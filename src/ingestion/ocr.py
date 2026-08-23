"""PaddleOCR fallback for pages where PyMuPDF found no extractable text (scanned/image pages).

Import of paddleocr is deferred to first use so the rest of the ingestion
pipeline stays importable in environments without it installed.
"""

from __future__ import annotations

from src.ingestion.schema import Block

_ocr_engine = None


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(use_angle_cls=True, lang="en")
    return _ocr_engine


def ocr_page_image(image_bytes: bytes) -> list[Block]:
    engine = _get_engine()
    result = engine.ocr(image_bytes, cls=True)

    blocks: list[Block] = []
    for line in result[0] if result else []:
        quad, (text, confidence) = line
        xs = [p[0] for p in quad]
        ys = [p[1] for p in quad]
        blocks.append(
            Block(
                text=text,
                bbox=(min(xs), min(ys), max(xs), max(ys)),
                confidence=confidence,
            )
        )
    return blocks

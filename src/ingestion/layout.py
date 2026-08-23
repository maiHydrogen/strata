"""LayoutLMv3 wrapper: tags each block with a layout role (title, paragraph, table, list, figure, ...).

Import of transformers/torch is deferred to first use so the rest of the
ingestion pipeline stays importable in environments without them installed.
"""

from __future__ import annotations

from src.ingestion.schema import Page

_model = None
_processor = None

LABELS = ["title", "paragraph", "table", "list", "figure", "caption", "header", "footer"]


def _get_model():
    global _model, _processor
    if _model is None:
        from transformers import AutoModelForTokenClassification, AutoProcessor

        _processor = AutoProcessor.from_pretrained("microsoft/layoutlmv3-base", apply_ocr=False)
        _model = AutoModelForTokenClassification.from_pretrained(
            "microsoft/layoutlmv3-base", num_labels=len(LABELS)
        )
    return _model, _processor


def tag_page(page: Page) -> Page:
    """Assign a `label` to every block on the page in place, and return it."""
    if not page.blocks:
        return page

    model, processor = _get_model()

    words = [b.text for b in page.blocks]
    boxes = [_normalize_bbox(b.bbox, page.width, page.height) for b in page.blocks]

    encoding = processor(text=words, boxes=boxes, return_tensors="pt", truncation=True)
    outputs = model(**encoding)
    predictions = outputs.logits.argmax(-1).squeeze().tolist()
    if isinstance(predictions, int):
        predictions = [predictions]

    for block, pred in zip(page.blocks, predictions):
        block.label = LABELS[pred] if pred < len(LABELS) else None

    return page


def _normalize_bbox(
    bbox: tuple[float, float, float, float], width: float, height: float
) -> list[int]:
    x0, y0, x1, y1 = bbox
    return [
        int(1000 * x0 / width),
        int(1000 * y0 / height),
        int(1000 * x1 / width),
        int(1000 * y1 / height),
    ]

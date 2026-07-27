"""Provider-neutral image OCR built on Learnova's existing OCR preprocessing."""

from __future__ import annotations

import base64
from typing import Any, Callable

from learnova.ocr.service import (
    normalize_recognition,
    preprocess_document_image,
    recognition_instructions,
)


def extract_image_text(
    data: bytes,
    *,
    subject: str,
    page_number: int,
    recognize: Callable[..., Any],
) -> dict[str, Any]:
    processed = preprocess_document_image(data)
    response = recognize(
        image_data=processed.data,
        image_mime=processed.mime_type,
        instructions=recognition_instructions(subject, page_number),
    )
    recognized = normalize_recognition(response)
    warnings = list(processed.warnings)
    if recognized.get("warning"):
        warnings.append(recognized["warning"])
    if any(block.get("type") == "handwriting" for block in recognized["blocks"]):
        warnings.append("Handwriting may be inaccurate; review the extracted text.")
    if any(block.get("type") == "formula" for block in recognized["blocks"]):
        warnings.append("Some formulas may not have been preserved; compare with the source.")
    if recognized["confidence"] < 0.58:
        warnings.append("Text could not be confidently recognized.")
    return {
        "text": recognized["text"], "confidence": recognized["confidence"],
        "confidence_status": recognized["confidence_status"], "warnings": warnings,
        "readable": recognized["readable"], "blocks": recognized["blocks"],
        "width": processed.width, "height": processed.height,
        "processed_data": processed.data, "processed_mime": processed.mime_type,
    }


def data_url(data: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"

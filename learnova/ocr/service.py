"""Reusable, conservative document validation, preprocessing, and recognition helpers."""

import io
import json
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError
import pymupdf  # pyright: ignore[reportMissingImports]


MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
ALLOWED_BLOCK_TYPES = {
    "printed_text", "handwriting", "formula", "table", "diagram",
    "heading", "annotation", "uncertain", "crossed_out",
}

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


@dataclass
class ProcessedImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    warnings: list[str]
    blur_score: float
    glare_ratio: float


def detected_mime_type(data: bytes) -> str | None:
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_document_upload(data: bytes, filename: str, declared_mime: str | None = None) -> str:
    if not data:
        raise ValueError("Empty files cannot be uploaded.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"{filename} exceeds the 15 MB per-file limit.")
    detected = detected_mime_type(data)
    if not detected:
        raise ValueError(f"{filename} is not a valid PDF, JPG, PNG, or WebP file.")
    if declared_mime and declared_mime not in {detected, "application/octet-stream"}:
        raise ValueError(f"{filename} has a file type that does not match its content.")
    return detected


def _safe_crop(image: Image.Image, transform: dict[str, Any]) -> Image.Image:
    crop = transform.get("crop") or {}
    try:
        left = max(0.0, min(0.4, float(crop.get("left", 0))))
        top = max(0.0, min(0.4, float(crop.get("top", 0))))
        right = max(0.0, min(0.4, float(crop.get("right", 0))))
        bottom = max(0.0, min(0.4, float(crop.get("bottom", 0))))
    except (TypeError, ValueError):
        left = top = right = bottom = 0
    x1, y1 = round(image.width * left), round(image.height * top)
    x2, y2 = round(image.width * (1 - right)), round(image.height * (1 - bottom))
    if x2 - x1 >= 200 and y2 - y1 >= 200:
        return image.crop((x1, y1, x2, y2))
    return image


def preprocess_document_image(data: bytes, transform: dict[str, Any] | None = None) -> ProcessedImage:
    """Improve legibility conservatively while retaining handwriting and mathematical marks."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("The image resolution exceeds the 40-megapixel safety limit.")
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The image is damaged, unsupported, or unreasonably large.") from error
    transposed = ImageOps.exif_transpose(image)
    if transposed is not None:
        image = transposed
    image = image.convert("RGB")
    transform = transform or {}
    image = _safe_crop(image, transform)
    try:
        rotation = int(transform.get("rotation", 0)) % 360
    except (TypeError, ValueError):
        rotation = 0
    if rotation in {90, 180, 270}:
        image = image.rotate(-rotation, expand=True)

    warnings = []
    if min(image.width, image.height) < 800:
        warnings.append("Resolution is low; small handwriting may need review.")
    sample = ImageOps.contain(image.convert("L"), (700, 700))
    mean_brightness = ImageStat.Stat(sample).mean[0]
    edges = sample.filter(ImageFilter.FIND_EDGES)
    blur_score = round(ImageStat.Stat(edges).var[0], 2)
    histogram = sample.histogram()
    glare_ratio = round(sum(histogram[246:]) / max(1, sample.width * sample.height), 4)
    if blur_score < 120:
        warnings.append("The page may be blurred; verify small text and symbols.")
    if glare_ratio > 0.18:
        warnings.append("Possible glare detected; verify washed-out regions.")

    if mean_brightness < 85:
        image = ImageEnhance.Brightness(image).enhance(1.14)
    elif mean_brightness > 215:
        image = ImageEnhance.Brightness(image).enhance(0.94)
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = ImageEnhance.Sharpness(image).enhance(1.08)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return ProcessedImage(
        data=output.getvalue(), mime_type="image/png", width=image.width, height=image.height,
        warnings=warnings, blur_score=blur_score, glare_ratio=glare_ratio,
    )


def crop_image_region(data: bytes, bbox: list[float]) -> bytes:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("The source region image is unavailable.") from error
    if len(bbox) == 4:
        image = image.crop((
            round(bbox[0] * image.width), round(bbox[1] * image.height),
            round(bbox[2] * image.width), round(bbox[3] * image.height),
        ))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_pdf_page(data: bytes, page_index: int, scale: float = 2.0) -> bytes:
    document = None
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
        if page_index < 0 or page_index >= len(document):
            raise ValueError("The requested PDF page does not exist.")
        pixmap = document[page_index].get_pixmap(  # pyright: ignore[reportAttributeAccessIssue]
            matrix=pymupdf.Matrix(scale, scale), alpha=False
        )
        rendered = pixmap.tobytes("png")
        return rendered
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("The PDF page could not be rendered safely.") from error
    finally:
        if document is not None:
            document.close()


def confidence_status(confidence: float) -> str:
    if confidence >= 0.86:
        return "high"
    if confidence >= 0.58:
        return "review"
    return "unclear"


def normalize_bbox(value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) != 4:
        return []
    try:
        result = [max(0.0, min(1.0, float(item))) for item in value]
    except (TypeError, ValueError):
        return []
    return result if result[2] > result[0] and result[3] > result[1] else []


def normalize_recognition(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate model output and calculate review state without guessing missing content."""
    normalized_blocks = []
    for order, raw in enumerate(payload.get("blocks", []), start=1):
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content", "")).strip()
        block_type = str(raw.get("type", "uncertain")).strip().lower()
        if block_type not in ALLOWED_BLOCK_TYPES:
            block_type = "uncertain"
        if not content and block_type != "diagram":
            continue
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0
        crossed_out = bool(raw.get("crossed_out")) or block_type == "crossed_out"
        normalized_blocks.append({
            "order": order,
            "type": block_type,
            "content": content,
            "bbox": normalize_bbox(raw.get("bbox")),
            "confidence": confidence,
            "confidence_status": confidence_status(confidence),
            "crossed_out": crossed_out,
            "important_candidate": bool(raw.get("important_candidate")),
            "teacher_highlight_candidate": bool(raw.get("teacher_highlight_candidate")),
            "nearby_text": str(raw.get("nearby_text", "")).strip(),
        })
    usable = [item for item in normalized_blocks if item["content"] and not item["crossed_out"]]
    overall = round(sum(item["confidence"] for item in usable) / len(usable), 3) if usable else 0.0
    text = "\n".join(
        item["content"] for item in usable if item["type"] not in {"diagram"}
    )
    formulas = [item for item in usable if item["type"] == "formula"]
    diagrams = [item for item in normalized_blocks if item["type"] == "diagram"]
    return {
        "text": text,
        "blocks": normalized_blocks,
        "formulas": formulas,
        "diagrams": diagrams,
        "headings": [item for item in usable if item["type"] == "heading"],
        "annotations": [item for item in normalized_blocks if item["type"] in {"annotation", "crossed_out"}],
        "confidence": overall,
        "confidence_status": confidence_status(overall),
        "detected_page_number": str(payload.get("detected_page_number", "")).strip()[:40],
        "readable": bool(usable),
        "warning": str(payload.get("warning", "")).strip(),
    }


def recognition_instructions(subject: str, page_order: int) -> str:
    schema = {
        "blocks": [{
            "type": "printed_text|handwriting|formula|table|diagram|heading|annotation|uncertain|crossed_out",
            "content": "exact visible content; diagram labels only for a diagram block",
            "bbox": [0.0, 0.0, 1.0, 1.0],
            "confidence": 0.0,
            "crossed_out": False,
            "important_candidate": False,
            "teacher_highlight_candidate": False,
            "nearby_text": "",
        }],
        "detected_page_number": "",
        "warning": "",
    }
    return (
        f"Recognize page {page_order} of a {subject} study document. Return JSON only: "
        f"{json.dumps(schema)}. Transcribe printed text and handwriting exactly. Preserve line order, accents, "
        "minus signs, decimals, fractions, exponents, subscripts, Greek letters, units, chemical charges, "
        "reaction arrows, corrections, underlining, highlighting, labels, and numbered lists. Mark uncertain "
        "content with low confidence instead of guessing. Mark crossed-out writing and do not treat it as "
        "reliable study text. Describe no diagram facts: store only visible labels and nearby text. Bounding boxes "
        "are normalized [left, top, right, bottom]. Teacher emphasis is only a candidate for student confirmation."
    )

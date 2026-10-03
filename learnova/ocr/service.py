"""Reusable, conservative document validation, preprocessing, and recognition helpers."""

import io
import json
import re
from dataclasses import dataclass
from typing import Any

from PIL import (
    Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps,
    ImageStat, UnidentifiedImageError,
)
import pymupdf  # pyright: ignore[reportMissingImports]

from learnova.ai_services.contracts import BLOCK_TYPES

try:  # OpenCV/NumPy power true perspective + deskew correction; degrade gracefully if absent.
    import cv2  # pyright: ignore[reportMissingImports]
    import numpy as _np
    _CV2_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only where the optional wheels are missing
    _CV2_AVAILABLE = False


MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
# One definition, shared with the gateway validator, so the prompt below can never ask
# for a type the validator would reject. See the note beside BLOCK_TYPES.
ALLOWED_BLOCK_TYPES = BLOCK_TYPES

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
    mode: str = "enhanced"
    source_width: int = 0
    source_height: int = 0
    upscaled: float = 1.0


# Recognition preprocessing variants tried against the vision OCR provider, in the
# order that most often succeeds on real photographed notebook pages.
RECOGNITION_VARIANTS = ("enhanced", "grayscale", "threshold")

# Handwriting-friendly labelling thresholds. These only affect how a block is
# *labelled* (high / review / unclear) for the reviewer — they never reject a page.
CONFIDENCE_HIGH = 0.80
CONFIDENCE_REVIEW = 0.45


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


def _otsu_threshold(gray: Image.Image) -> int:
    """Otsu's global threshold from an 8-bit grayscale histogram (no numpy dependency)."""
    histogram = gray.histogram()[:256]
    total = sum(histogram)
    if total == 0:
        return 128
    sum_all = sum(index * count for index, count in enumerate(histogram))
    weight_bg = 0.0
    sum_bg = 0.0
    best_variance = -1.0
    low = high = 128
    for level in range(256):
        weight_bg += histogram[level]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += level * histogram[level]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_all - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > best_variance:
            best_variance = between
            low = high = level
        elif between == best_variance:
            high = level  # extend the plateau so a flat gap resolves to its midpoint
    return (low + high) // 2


def _adaptive_threshold(gray: Image.Image, offset: int = 10) -> Image.Image:
    """Binarise by comparing each pixel to its own neighbourhood, not to one global cut.

    A global threshold assumes the page is evenly lit. Phone photos rarely are: a shadow
    across one side drags that side below the cut, so a whole region turns solid black and
    every faint pencil stroke inside it is lost. Measured on a page with a lighting
    gradient, the global cut produced 14x more "ink" on the shadowed half than on the lit
    half; this produces the same amount on both, which is what the page actually contains.

    Implemented with a box blur as the local mean so the per-pixel work stays inside
    Pillow's C loops - this runs on every scan, and OpenCV is an optional dependency here.
    """

    # Below roughly a postage stamp there is no meaningful "neighbourhood" to compare
    # against, so fall back to the global cut (this is the path small crops take).
    if min(gray.width, gray.height) < 120:
        cut = _otsu_threshold(gray)
        return gray.point([0] * cut + [255] * (256 - cut))
    radius = max(3, min(gray.width, gray.height) // 40)
    local_mean = gray.filter(ImageFilter.BoxBlur(radius))
    # subtract() clamps at 0, so this is "how much darker than its surroundings", which is
    # exactly what a pen or pencil stroke is regardless of how bright that area is.
    darker_than_surroundings = ImageChops.subtract(local_mean, gray)
    cut = max(0, min(255, offset))
    return darker_than_surroundings.point([255] * (cut + 1) + [0] * (255 - cut))


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


def _order_quad(points):
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    s = points.sum(axis=1)
    diff = _np.diff(points, axis=1).ravel()
    return _np.array([
        points[_np.argmin(s)], points[_np.argmin(diff)],
        points[_np.argmax(s)], points[_np.argmax(diff)],
    ], dtype="float32")


def _four_point_warp(rgb, quad):
    ordered = _order_quad(quad)
    (tl, tr, br, bl) = ordered
    width = int(max(_np.linalg.norm(br - bl), _np.linalg.norm(tr - tl)))
    height = int(max(_np.linalg.norm(tr - br), _np.linalg.norm(tl - bl)))
    if width < 200 or height < 200:
        return None
    dst = _np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype="float32")
    matrix = cv2.getPerspectiveTransform(ordered, dst)
    return cv2.warpPerspective(rgb, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def deskew_document_image(image: Image.Image) -> tuple[Image.Image, str]:
    """Straighten a photographed page using OpenCV.

    First tries true 4-point perspective correction on a detected page quadrilateral;
    otherwise falls back to rotation deskew from the dominant text orientation. Returns
    (image, note). A no-op (returns the input) when OpenCV is unavailable, when the tilt
    is negligible, or on any failure — recognition then proceeds on the original."""
    if not _CV2_AVAILABLE:
        return image, ""
    try:
        gray = _np.array(image.convert("L"))
        rgb = _np.array(image.convert("RGB"))
        height, width = gray.shape[:2]
        # 1) Perspective: largest convex 4-corner page contour covering most of the frame.
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 60, 180)
        edges = cv2.dilate(edges, _np.ones((3, 3), _np.uint8), iterations=1)
        contours, _hierarchy = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
            if cv2.contourArea(contour) < 0.4 * width * height:
                break
            approx = cv2.approxPolyDP(contour, 0.02 * cv2.arcLength(contour, True), True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                warped = _four_point_warp(rgb, approx.reshape(4, 2).astype("float32"))
                if warped is not None:
                    return Image.fromarray(warped), "perspective-corrected"
        # 2) Rotation deskew from the minimum-area rectangle of the text mask.
        mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        coords = cv2.findNonZero(mask)
        if coords is None:
            return image, ""
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle += 90
        elif angle > 45:
            angle -= 90
        if abs(angle) < 1.2 or abs(angle) > 30:
            return image, ""  # negligible or implausible tilt — leave the page untouched
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        rotated = cv2.warpAffine(
            rgb, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return Image.fromarray(rotated), f"deskewed {round(angle, 1)}°"
    except Exception:
        return image, ""


def _auto_paper_crop(image: Image.Image) -> Image.Image:
    """Trim a clearly darker surround (desk/background) down to the bright page.

    Conservative: only crops when a distinct bright region is meaningfully smaller
    than the frame, so full-bleed pages and dense text pages are left untouched.
    This is a lightweight stand-in for full 4-point perspective de-warping, which
    would require OpenCV/NumPy that this deployment does not ship."""
    gray = ImageOps.autocontrast(image.convert("L"), cutoff=2)
    paper = gray.point([0] * 118 + [255] * (256 - 118))  # bright paper -> white
    bbox = paper.getbbox()
    if not bbox:
        return image
    width, height = image.size
    margin_x, margin_y = int(width * 0.015), int(height * 0.015)
    x1 = max(0, bbox[0] - margin_x)
    y1 = max(0, bbox[1] - margin_y)
    x2 = min(width, bbox[2] + margin_x)
    y2 = min(height, bbox[3] + margin_y)
    crop_w, crop_h = x2 - x1, y2 - y1
    area = crop_w * crop_h
    if crop_w < 240 or crop_h < 240 or area < 0.30 * width * height:
        return image  # too aggressive — keep the original frame
    if crop_w > 0.96 * width and crop_h > 0.96 * height:
        return image  # nothing meaningful to trim
    return image.crop((x1, y1, x2, y2))


def _prepare_base_image(data: bytes, transform: dict[str, Any] | None = None):
    """Shared geometry + enhancement stage. Returns (RGB image, warnings, metrics, source_size, upscale)."""
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("The image resolution exceeds the 40-megapixel safety limit.")
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The image is damaged, unsupported, or unreasonably large.") from error
    source_width, source_height = image.width, image.height
    transposed = ImageOps.exif_transpose(image)  # correct camera EXIF orientation
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
    deskew_note = ""
    if transform.get("deskew", True):
        image, deskew_note = deskew_document_image(image)
    if transform.get("autocrop", True):
        image = _auto_paper_crop(image)

    # Upscale small captures 2x-4x so thin handwriting survives the vision model's own
    # downscaling. Large phone photos are never downscaled here (detail is preserved and
    # the provider receives them at "detail: high"). Aspect ratio + pixel cap preserved.
    min_side = min(image.width, image.height)
    upscale = 1.0
    if 0 < min_side < 1800:
        upscale = max(1.0, min(4.0, 1800 / min_side))
        target = (max(1, round(image.width * upscale)), max(1, round(image.height * upscale)))
        if target[0] * target[1] <= MAX_IMAGE_PIXELS:
            image = image.resize(target, Image.Resampling.LANCZOS)
        else:
            upscale = 1.0

    warnings: list[str] = []
    if deskew_note:
        warnings.append(f"Page angle corrected ({deskew_note}).")
    sample = ImageOps.contain(image.convert("L"), (700, 700))
    mean_brightness = ImageStat.Stat(sample).mean[0]
    edges = sample.filter(ImageFilter.FIND_EDGES)
    blur_score = round(ImageStat.Stat(edges).var[0], 2)
    histogram = sample.histogram()
    glare_ratio = round(sum(histogram[246:]) / max(1, sample.width * sample.height), 4)
    # These are advisory only — they never reject the page (small text != low quality).
    if blur_score < 120:
        warnings.append("The page may be blurred; verify small text and symbols.")
    if glare_ratio > 0.18:
        warnings.append("Possible glare detected; verify washed-out regions.")

    if mean_brightness < 85:
        image = ImageEnhance.Brightness(image).enhance(1.16)
    elif mean_brightness > 215:
        image = ImageEnhance.Brightness(image).enhance(0.92)
    # Adaptive contrast normalisation copes with shadows/low-contrast photos.
    image = ImageOps.autocontrast(image, cutoff=1, preserve_tone=True)
    image = ImageEnhance.Contrast(image).enhance(1.06)
    # Unsharp mask recovers thin pen strokes without global halos.
    image = image.filter(ImageFilter.UnsharpMask(radius=1.6, percent=120, threshold=3))
    return image, warnings, blur_score, glare_ratio, (source_width, source_height), round(upscale, 2)


def _finalize_variant(image: Image.Image, mode: str) -> Image.Image:
    """Apply the colour treatment for a recognition variant to an enhanced base image."""
    if mode == "enhanced":
        return image
    # Grayscale + a gentle median denoise removes colour noise and paper speckle.
    gray = image.convert("L").filter(ImageFilter.MedianFilter(3))
    if mode == "threshold":
        gray = _adaptive_threshold(gray)  # local, so shadows do not swallow faint strokes
    return gray.convert("RGB")


def _package(image: Image.Image, mode, warnings, blur_score, glare_ratio, source_size, upscale) -> ProcessedImage:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return ProcessedImage(
        data=output.getvalue(), mime_type="image/png", width=image.width, height=image.height,
        warnings=list(warnings), blur_score=blur_score, glare_ratio=glare_ratio, mode=mode,
        source_width=source_size[0], source_height=source_size[1], upscaled=upscale,
    )


def preprocess_document_image(data: bytes, transform: dict[str, Any] | None = None) -> ProcessedImage:
    """Improve legibility conservatively while retaining handwriting and mathematical marks.

    Returns a single processed image; the variant is chosen by ``transform`` flags
    (``mode``/``grayscale``/``binarize``) so existing callers keep their behaviour."""
    transform = transform or {}
    base, warnings, blur_score, glare_ratio, source_size, upscale = _prepare_base_image(data, transform)
    mode = str(transform.get("mode") or "").strip().lower()
    if mode not in RECOGNITION_VARIANTS:
        if transform.get("binarize"):
            mode = "threshold"
        elif transform.get("grayscale"):
            mode = "grayscale"
        else:
            mode = "enhanced"
    return _package(_finalize_variant(base, mode), mode, warnings, blur_score, glare_ratio, source_size, upscale)


def apply_recognition_variant(data: bytes, mode: str) -> ProcessedImage:
    """Apply only the colour treatment for a variant to an already-preprocessed PNG.

    Used to derive grayscale / adaptive-threshold attempts from the stored enhanced
    page image without repeating the (already-applied) geometry and upscale stages."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("The processed image could not be reopened.") from error
    image = image.convert("RGB")
    safe_mode = mode if mode in RECOGNITION_VARIANTS else "enhanced"
    finalized = _finalize_variant(image, safe_mode)
    output = io.BytesIO()
    finalized.save(output, format="PNG", optimize=True)
    return ProcessedImage(
        data=output.getvalue(), mime_type="image/png", width=finalized.width, height=finalized.height,
        warnings=[], blur_score=0.0, glare_ratio=0.0, mode=safe_mode,
        source_width=image.width, source_height=image.height, upscaled=1.0,
    )


def preprocess_variants(
    data: bytes, transform: dict[str, Any] | None = None, modes: tuple[str, ...] = RECOGNITION_VARIANTS,
) -> list[ProcessedImage]:
    """Produce several preprocessing variants (enhanced colour, grayscale, adaptive
    threshold) from one shared enhancement pass, for multi-attempt vision OCR."""
    transform = transform or {}
    forced = str(transform.get("mode") or "").strip().lower()
    if forced in RECOGNITION_VARIANTS:
        modes = (forced,)
    base, warnings, blur_score, glare_ratio, source_size, upscale = _prepare_base_image(data, transform)
    return [
        _package(_finalize_variant(base, mode), mode, warnings, blur_score, glare_ratio, source_size, upscale)
        for mode in modes
    ]


# ---- Second look: re-read the words the first pass was unsure about ----------------
#
# Messy handwriting is usually not unreadable, it is *small*. A whole notebook page sent
# to a vision model is downscaled before it is looked at, and a shaky word that occupies
# 30 pixels of that page survives as a smudge. Cropping those words out and sending them
# back enlarged is the difference between "illegible" and "legible", and stitching them
# into one sheet keeps it to a single extra request no matter how many words there were.

REGION_SHEET_MAX = 8          # fragments per sheet: one extra request, bounded cost
REGION_SHEET_WIDTH = 1100     # px, including the number gutter
REGION_LABEL_GUTTER = 90      # px reserved on the left for the fragment number
REGION_MIN_HEIGHT = 110       # px: every fragment is enlarged to at least this tall
REGION_MAX_HEIGHT = 320       # px: keeps one huge block from dominating the sheet
REGION_PADDING = 0.012        # of page width/height, so ascenders and accents survive


@dataclass
class RegionSheet:
    """One stitched close-up image plus the block order each numbered strip came from."""

    data: bytes
    mime_type: str
    orders: list[int]
    width: int
    height: int


def _label_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except (AttributeError, TypeError):  # Pillow < 10 has no size parameter
        return ImageFont.load_default()


def build_region_sheet(page_data: bytes, blocks: list[dict[str, Any]],
                       limit: int = REGION_SHEET_MAX) -> RegionSheet | None:
    """Stitch the uncertain blocks of one page into a single numbered close-up sheet.

    Returns None when there is nothing worth a second look - no blocks, or none of them
    carried a usable bounding box, which is the only thing that makes a crop possible.
    """

    if not blocks:
        return None
    try:
        page = Image.open(io.BytesIO(page_data))
        page.load()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None
    page = page.convert("RGB")

    strips: list[tuple[int, Image.Image]] = []
    for item in blocks[:limit]:
        bbox = item.get("bbox") or []
        if len(bbox) != 4:
            continue  # no box, no crop: the student edits this one by hand instead
        pad_x, pad_y = page.width * REGION_PADDING, page.height * REGION_PADDING
        left = max(0, round(bbox[0] * page.width - pad_x))
        top = max(0, round(bbox[1] * page.height - pad_y))
        right = min(page.width, round(bbox[2] * page.width + pad_x))
        bottom = min(page.height, round(bbox[3] * page.height + pad_y))
        if right - left < 8 or bottom - top < 8:
            continue
        crop = page.crop((left, top, right, bottom))
        usable_width = REGION_SHEET_WIDTH - REGION_LABEL_GUTTER
        # Enlarge to fill the sheet width, but never past the height cap, and never
        # shrink below the crop's own size - this pass exists to add pixels, not remove.
        scale = min(usable_width / crop.width, REGION_MAX_HEIGHT / crop.height)
        scale = max(scale, REGION_MIN_HEIGHT / crop.height)
        scale = min(scale, usable_width / crop.width)
        if scale > 1:
            crop = crop.resize((max(1, round(crop.width * scale)),
                                max(1, round(crop.height * scale))), Image.Resampling.LANCZOS)
        strips.append((int(item.get("order") or 0), crop))

    if not strips:
        return None

    gap = 18
    height = sum(crop.height for _order, crop in strips) + gap * (len(strips) + 1)
    sheet = Image.new("RGB", (REGION_SHEET_WIDTH, height), "white")
    draw = ImageDraw.Draw(sheet)
    font = _label_font(44)
    y = gap
    orders: list[int] = []
    for index, (order, crop) in enumerate(strips, start=1):
        sheet.paste(crop, (REGION_LABEL_GUTTER, y))
        draw.text((18, y + crop.height // 2 - 22), str(index), fill="black", font=font)
        if index < len(strips):  # a rule between fragments, so two never read as one line
            draw.line((10, y + crop.height + gap // 2,
                       REGION_SHEET_WIDTH - 10, y + crop.height + gap // 2), fill=(170, 170, 170))
        y += crop.height + gap
        orders.append(order)

    output = io.BytesIO()
    sheet.save(output, format="PNG", optimize=True)
    return RegionSheet(data=output.getvalue(), mime_type="image/png", orders=orders,
                       width=sheet.width, height=sheet.height)


def region_review_instructions(subject: str, fragments: list[dict[str, Any]]) -> str:
    """Prompt for the second look, carrying each fragment's first reading as context."""

    schema = {"regions": [{"index": 1, "content": "", "confidence": 0.0, "illegible": False}]}
    listed = "\n".join(
        f"{index}. first reading: \"{str(item.get('content') or '')[:120]}\""
        + (f" (nearby text: \"{str(item.get('nearby_text'))[:80]}\")" if item.get("nearby_text") else "")
        for index, item in enumerate(fragments, start=1)
    )
    return (
        f"The image contains {len(fragments)} numbered close-ups, each cropped from one "
        f"{subject} study page and enlarged. Each is a fragment the first pass could not read "
        f"confidently. Re-read each fragment from the image.\n\n"
        f"What the first pass thought each said (this is a hint, not the answer - it is often "
        f"wrong, which is why the fragment is here):\n{listed}\n\n"
        f"Return JSON only: {json.dumps(schema)}. One entry per numbered fragment, with 'index' "
        "matching the printed number. Put your best literal reading in 'content' and how sure you "
        "are in 'confidence'. Read what is actually written, including messy, joined-up or "
        "corrected handwriting; preserve accents, symbols, units, exponents and punctuation "
        "exactly. Do not translate, expand abbreviations, tidy up grammar, or complete a word "
        "the writer left unfinished. If a fragment genuinely cannot be read, set 'illegible' to "
        "true and leave 'content' empty rather than guessing something plausible."
    )


def normalize_region_review(payload: Any, orders: list[int]) -> dict[int, dict[str, Any]]:
    """Map a second-look response onto block orders, discarding anything unusable.

    Keyed by the block's ``order`` rather than the sheet index so a model that returns
    entries out of sequence, repeats one, or invents index 99 cannot corrupt the page.
    """

    readings: dict[int, dict[str, Any]] = {}
    if not isinstance(payload, dict):
        return readings
    for raw in payload.get("regions") or []:
        if not isinstance(raw, dict):
            continue
        index_value = raw.get("index")
        if index_value is None or isinstance(index_value, bool):
            continue
        try:
            index = int(index_value)
        except (TypeError, ValueError):
            continue
        if not 1 <= index <= len(orders):
            continue
        order = orders[index - 1]
        if order in readings:
            continue  # first entry for a fragment wins; a repeat is noise
        content = str(raw.get("content") or "").strip()
        if bool(raw.get("illegible")) or not content:
            continue
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
        except (TypeError, ValueError):
            confidence = 0.0
        readings[order] = {"content": content[:2000], "confidence": confidence}
    return readings


def merge_region_review(recognized: dict[str, Any], readings: dict[int, dict[str, Any]]) -> int:
    """Apply second-look readings to a page, but only where they are actually better.

    A close-up read that is *less* confident than the first pass is not an improvement, so
    it is dropped. The student still reviews everything either way; this only changes what
    they are shown first. Returns how many blocks were replaced.
    """

    improved = 0
    for block in recognized.get("blocks", []):
        reading = readings.get(block.get("order"))
        if not reading or reading["confidence"] <= block.get("confidence", 0):
            continue
        if reading["content"] == block.get("content"):
            # Same text, read again more confidently: promote the label, keep the text.
            block["confidence"] = reading["confidence"]
            block["confidence_status"] = confidence_status(reading["confidence"])
            continue
        block["previous_reading"] = block.get("content", "")
        block["content"] = reading["content"]
        block["confidence"] = reading["confidence"]
        block["confidence_status"] = confidence_status(reading["confidence"])
        block["second_look"] = True
        improved += 1
    _recompute_page_summary(recognized)
    return improved


def _recompute_page_summary(recognized: dict[str, Any]) -> None:
    """Rebuild the derived page fields after blocks change, so nothing goes out of step."""

    blocks = recognized.get("blocks", [])
    usable = [item for item in blocks if item["content"] and not item["crossed_out"]]
    recognized["text"] = "\n".join(item["content"] for item in usable if item["type"] != "diagram")
    overall = round(sum(item["confidence"] for item in usable) / len(usable), 3) if usable else 0.0
    recognized["confidence"] = overall
    recognized["confidence_status"] = confidence_status(overall)
    recognized["formulas"] = [item for item in usable if item["type"] == "formula"]
    recognized["headings"] = [item for item in usable if item["type"] == "heading"]
    recognized["uncertain_regions"] = [
        item for item in blocks if item["confidence_status"] != "high" and not item["crossed_out"]]
    # These copy a block's content rather than referencing it, so they would otherwise
    # keep showing the reading the second look just replaced.
    recognized["suggested_corrections"] = [
        {"order": item["order"], "content": item["content"],
         "suggestion": item["suggested_correction"], "confidence": item["confidence"]}
        for item in blocks if item["suggested_correction"]
    ]
    recognized["readable"] = bool(usable)


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


# ---- Free text: a PDF that already contains its own words -------------------------
#
# A born-digital PDF carries a text layer, and the upload already extracts it
# (learnova/uploads/service.py). Recognition then ignored it and paid the vision model to
# read the same page from a picture - up to four calls for text that was already sitting
# in the database. These helpers decide when that text can be trusted instead.
#
# The gate is deliberately strict in one direction: rejecting good text only loses the
# saving, while accepting a junk text layer would put garbage into a student's notes. So
# anything sparse, symbol-heavy or word-poor goes to the vision model as before.

NATIVE_TEXT_MIN_LETTERS = 200      # a page of real prose; a scanned page's junk layer has far less
NATIVE_TEXT_MIN_LETTER_RATIO = 0.5  # CID/ligature garbage is mostly punctuation and boxes
NATIVE_TEXT_MIN_WORDS = 20


def native_text_quality(text: Any) -> tuple[bool, str]:
    """Whether a PDF page's own text layer is good enough to skip recognition.

    Returns (usable, reason) so the caller can log why a page still cost a vision call.
    """

    content = str(text or "").strip()
    if not content:
        return False, "no_text_layer"
    letters = sum(1 for character in content if character.isalpha())
    if letters < NATIVE_TEXT_MIN_LETTERS:
        return False, "too_little_text"
    if letters / len(content) < NATIVE_TEXT_MIN_LETTER_RATIO:
        # Mostly punctuation or replacement characters: a broken embedded font.
        return False, "not_mostly_letters"
    words = [word for word in re.split(r"\s+", content) if len(word) >= 3]
    if len(words) < NATIVE_TEXT_MIN_WORDS:
        return False, "too_few_words"
    return True, "native_text"


def recognition_from_text(text: Any, limit: int = 400) -> dict[str, Any]:
    """Build a recognition result from text already in hand, with no AI call.

    Shaped by normalize_recognition so it is indistinguishable downstream from a page the
    model read. Confidence is 1.0 because the text is the document's own, not a reading of
    it - which also means no block is uncertain, so no close-up second look is triggered.
    """

    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    blocks = [{"type": "printed_text", "content": line, "confidence": 1.0, "bbox": []}
              for line in lines[:limit]]
    return normalize_recognition({"blocks": blocks, "detected_page_number": "", "warning": ""})


def confidence_status(confidence: float) -> str:
    if confidence >= CONFIDENCE_HIGH:
        return "high"
    if confidence >= CONFIDENCE_REVIEW:
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
            "suggested_correction": str(raw.get("suggested_correction", "")).strip(),
        })
    usable = [item for item in normalized_blocks if item["content"] and not item["crossed_out"]]
    overall = round(sum(item["confidence"] for item in usable) / len(usable), 3) if usable else 0.0
    text = "\n".join(
        item["content"] for item in usable if item["type"] not in {"diagram"}
    )
    formulas = [item for item in usable if item["type"] == "formula"]
    diagrams = [item for item in normalized_blocks if item["type"] == "diagram"]
    # Surface uncertain regions and any model-proposed corrections so the student can
    # review and edit them before cards/lessons are generated (we never auto-apply).
    uncertain_regions = [item for item in normalized_blocks
                         if item["confidence_status"] != "high" and not item["crossed_out"]]
    suggested_corrections = [
        {"order": item["order"], "content": item["content"],
         "suggestion": item["suggested_correction"], "confidence": item["confidence"]}
        for item in normalized_blocks if item["suggested_correction"]
    ]
    return {
        "text": text,
        "blocks": normalized_blocks,
        "formulas": formulas,
        "diagrams": diagrams,
        "headings": [item for item in usable if item["type"] == "heading"],
        "annotations": [item for item in normalized_blocks if item["type"] in {"annotation", "crossed_out"}],
        "uncertain_regions": uncertain_regions,
        "suggested_corrections": suggested_corrections,
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
            "nearby_text": "for a diagram block only: the caption or text beside it",
            "suggested_correction": "",
        }],
        "detected_page_number": "",
        "warning": "",
    }
    return (
        f"Recognize page {page_order} of a {subject} study document. Return JSON only: "
        f"{json.dumps(schema)}. Transcribe printed text and neat or messy handwriting exactly, including mixed "
        "printed/handwritten notes, vocabulary lists (source word, translation, example), tables, headings, "
        "underlined text, and direct/indirect speech notes. Preserve line order, accents, minus signs, decimals, "
        "fractions, exponents, subscripts, Greek letters, units, chemical charges, reaction arrows, corrections, "
        "underlining, highlighting, labels, and numbered lists. Never invent unreadable words: if a word is "
        "illegible, put your best literal reading in 'content' with a low 'confidence', and only when you have a "
        "plausible cleaner reading add it to 'suggested_correction' (leave it empty otherwise). Mark crossed-out "
        "writing and do not treat it as reliable study text. Describe no diagram facts: store only visible labels "
        "and nearby text. Bounding boxes are normalized [left, top, right, bottom]. Teacher emphasis is only a "
        "candidate for student confirmation. Leave nearby_text empty except on diagram "
        "blocks and suggested_correction empty unless you have a genuinely better "
        "reading: nothing else reads them, and every extra character is output you "
        "are charged for."
    )

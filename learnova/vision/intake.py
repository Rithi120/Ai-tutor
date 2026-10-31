"""Safe intake for every image a student sends: check, decode, orient, assess, size.

One path for every feature, so a photo is treated the same whether it arrives in a chat,
a lesson, a project or an answer. What it guarantees:

- The file is judged by its bytes, never by its name or the browser's MIME type.
- Pixel counts are checked from the header *before* decoding, so a decompression bomb is
  refused without being expanded.
- The pixels are re-encoded. Nothing of the original file survives except the picture:
  no EXIF (which carries GPS position and the phone model), no trailing payload.
- The camera's rotation tag is applied, and transparent screenshots are put on white -
  `convert("RGB")` on its own turns a transparent background black, and black text on
  it disappears.
- Quality problems (blur, darkness, low resolution, writing cut off at the edge) are
  reported, never used to refuse a photo: a slightly blurred page is usually readable.
- What goes to an AI provider is a copy sized to the provider's limit. Until now pages
  were sent at full resolution as PNG: a 1.6 MB phone photo became an 11 MB base64
  string, nearly three times Groq's documented 4 MB limit for base64 images.

Pure Pillow, Flask-free, so every rule is testable without an app.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
from dataclasses import dataclass, field
from typing import Any

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

from learnova.ocr.service import detected_mime_type

IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
# HEIF brands an iPhone writes. Recognised only to say so clearly: Pillow cannot decode
# them without an extra plugin, and Safari normally converts them to JPEG on upload.
_HEIF_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"avif"})


@dataclass(frozen=True)
class IntakeLimits:
    """Limits for one image. Defaults match config.py; the app passes its configured ones."""

    max_bytes: int = 15 * 1024 * 1024
    max_pixels: int = 40_000_000
    max_side: int = 12_000
    min_side: int = 64
    # What is kept: enough for an enlarged second look at one word on an A4 page.
    storage_edge: int = 2560
    # What a provider receives. Groq documents 4 MB for a base64 image; base64 is 4/3 of
    # the raw size, so 2.9 MB raw is 3.87 MB encoded, inside it with headroom.
    provider_edge: int = 2048
    provider_bytes: int = 2_900_000


class ImageProblem(ValueError):
    """A photo that cannot be used, with a stable code the interface can translate.

    `message` is a catalogue template; `values` fill its {placeholders}, so the same text
    can be translated whatever the configured limit is.
    """

    def __init__(self, code: str, message: str, **values: Any):
        super().__init__(message.format(**values) if values else message)
        self.code = code
        self.message = message
        self.values = values


@dataclass(frozen=True)
class QualityReport:
    """What may make a photo hard to read. Advisory only: nothing here rejects a photo."""

    issues: tuple[str, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)

    def has(self, issue: str) -> bool:
        return issue in self.issues


@dataclass(frozen=True)
class PreparedImage:
    """A validated, upright, metadata-free image ready to store and to send."""

    data: bytes
    mime_type: str
    width: int
    height: int
    source_sha256: str
    source_mime: str
    rotated_by_exif: int = 0
    quality: QualityReport = field(default_factory=QualityReport)

    def provider_copy(self, limits: IntakeLimits | None = None) -> tuple[bytes, str]:
        return provider_copy(self.data, limits)


# ---- checking the bytes ------------------------------------------------------------------

def sniff(data: bytes) -> str | None:
    """The real type of a file from its first bytes, including types we refuse."""

    detected = detected_mime_type(data)
    if detected:
        return detected
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return "image/heic"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    return None


def check_bytes(data: bytes, limits: IntakeLimits) -> str:
    """Refuse what cannot be an image we read; return the detected image type."""

    if not data:
        raise ImageProblem("empty_file", "The file is empty.")
    if len(data) > limits.max_bytes:
        raise ImageProblem(
            "file_too_large", "The photo is larger than {size} MB. Take it again or choose a smaller one.",
            size=limits.max_bytes // (1024 * 1024))
    kind = sniff(data)
    if kind == "image/heic":
        raise ImageProblem(
            "heic_unsupported",
            "This photo is in HEIC format, which cannot be read here. Choose it again from the "
            "photo picker (it is usually converted to JPEG), or set the camera to 'Most compatible'.")
    if kind == "application/pdf":
        raise ImageProblem("pdf_not_image", "This is a PDF, not a photo. Add PDFs in Projects instead.")
    if kind not in IMAGE_TYPES:
        raise ImageProblem("unsupported_type", "Use a JPG, PNG or WebP photo.")
    return kind


# ---- decoding ---------------------------------------------------------------------------

def decode(data: bytes, limits: IntakeLimits) -> tuple[Image.Image, int]:
    """Decode safely: dimensions from the header first, then the pixels, then upright.

    Returns (RGB image, degrees turned by the camera's rotation tag).
    """

    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
    except Image.DecompressionBombError as error:
        # Pillow refuses far-oversized headers itself, before we get to look at them.
        raise ImageProblem("too_many_pixels", "The photo's resolution is too high to process safely.") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ImageProblem("image_corrupt", "The photo is damaged and cannot be opened.") from error
    if width < 1 or height < 1:
        raise ImageProblem("image_corrupt", "The photo is damaged and cannot be opened.")
    if width * height > limits.max_pixels or max(width, height) > limits.max_side:
        raise ImageProblem("too_many_pixels", "The photo's resolution is too high to process safely.")
    if min(width, height) < limits.min_side:
        raise ImageProblem("image_too_small", "The photo is too small to read. Take it closer.")
    try:
        image = Image.open(io.BytesIO(data))
        image.seek(0)  # an animated WebP: the first frame is the picture
        image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, EOFError,
            Image.DecompressionBombError) as error:
        raise ImageProblem("image_corrupt", "The photo is damaged and cannot be opened.") from error

    # Clockwise degrees the tag asks for (6 = the phone was held upright, sensor sideways).
    rotated = {3: 180, 6: 90, 8: 270}.get(int(image.getexif().get(0x0112, 1) or 1), 0)
    upright = ImageOps.exif_transpose(image)
    if upright is not None:
        image = upright
    return to_rgb(image), rotated


def to_rgb(image: Image.Image) -> Image.Image:
    """RGB on white. Transparency becomes paper, not black; 16-bit scans keep their tones."""

    if image.mode in ("I;16", "I;16B", "I;16L", "I"):
        image = image.convert("I").point(lambda value: value * (1 / 256)).convert("L")
    if image.mode == "P":
        image = image.convert("RGBA")
    if image.mode in ("RGBA", "LA", "PA"):
        rgba = image.convert("RGBA")
        paper = Image.new("RGB", rgba.size, (255, 255, 255))
        paper.paste(rgba, mask=rgba.getchannel("A"))
        return paper
    return image.convert("RGB")


# ---- quality ------------------------------------------------------------------------------
#
# Thresholds were calibrated on the synthetic set in learnova/evaluation/vision_fixtures.py
# (scripts/vision_eval.py prints the measured confusion). They are deliberately
# conservative: a false "blurred" on a sharp page costs the student's trust in the hint.

QUALITY_WORKING_EDGE = 1024
DARK_MEAN = 70.0
# Ink against paper (90th percentile minus the darkest 0.3 %), not the spread of the page:
# a sparse page is mostly paper, and its spread is small even when every stroke is black.
LOW_CONTRAST = 45.0
BLUR_RATIO = 0.18
LOW_RESOLUTION_PIXELS = 450_000
LOW_RESOLUTION_LONG_SIDE = 800
EDGE_BAND = 0.015


def _edge_energy(gray: Image.Image) -> float:
    return ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0]


def _ink_mask(gray: Image.Image) -> Image.Image:
    """Pixels clearly darker than their surroundings: strokes, whatever the lighting."""

    radius = max(3, min(gray.size) // 40)
    darker = ImageChops.subtract(gray.filter(ImageFilter.BoxBlur(radius)), gray)
    return darker.point([0] * 26 + [255] * 230)


def _ink_fraction(mask: Image.Image) -> float:
    return ImageStat.Stat(mask).mean[0] / 255


def _percentile(histogram: list[int], fraction: float) -> int:
    target = sum(histogram) * fraction
    running = 0
    for level, count in enumerate(histogram):
        running += count
        if running >= target:
            return level
    return 255


def _runs(profile: list[bool], low: int, high: int) -> int:
    """How many stretches of consecutive True values have a length within [low, high]."""

    count, length = 0, 0
    for value in [*profile, False]:
        if value:
            length += 1
            continue
        if low <= length <= high:
            count += 1
        length = 0
    return count


def _writing_at_edge(mask: Image.Image) -> bool:
    """Lines of writing that run through the edge of the frame.

    Text cut at the left or right edge crosses it as several separate blobs, one per line
    and each about a letter high. A ruled line is far thinner, a page edge or a shadow is
    one long run, and a margin is empty, so none of those count. The same at the top or
    bottom, where a cut line shows as a row of letter-wide blobs.
    """

    width, height = mask.size
    band_w, band_h = max(3, round(width * EDGE_BAND)), max(3, round(height * EDGE_BAND))
    pixels = mask.load()
    if pixels is None:
        return False

    def column_band(x0: int, x1: int) -> list[bool]:
        return [sum(1 for x in range(x0, x1) if pixels[x, y]) >= max(1, (x1 - x0) // 6) for y in range(height)]

    def row_band(y0: int, y1: int) -> list[bool]:
        return [sum(1 for y in range(y0, y1) if pixels[x, y]) >= max(1, (y1 - y0) // 6) for x in range(width)]

    letter_low, letter_high = max(6, round(height * 0.008)), round(height * 0.12)
    for profile in (column_band(0, band_w), column_band(width - band_w, width)):
        if _runs(profile, letter_low, letter_high) >= 2:
            return True
    glyph_low, glyph_high = max(3, round(width * 0.004)), round(width * 0.08)
    for profile in (row_band(0, band_h), row_band(height - band_h, height)):
        if _runs(profile, glyph_low, glyph_high) >= 6:
            return True
    return False


def assess_quality(image: Image.Image) -> QualityReport:
    """Blur, darkness, contrast, resolution and cut-off writing, as advisory issue codes."""

    width, height = image.size
    gray = ImageOps.contain(image.convert("L"), (QUALITY_WORKING_EDGE, QUALITY_WORKING_EDGE))
    stats = ImageStat.Stat(gray)
    mean = stats.mean[0]
    histogram = gray.histogram()
    contrast = float(_percentile(histogram, 0.9) - _percentile(histogram, 0.003))
    issues: list[str] = []
    if mean < DARK_MEAN:
        issues.append("dark")
    elif contrast < LOW_CONTRAST:
        issues.append("low_contrast")
    if width * height < LOW_RESOLUTION_PIXELS or max(width, height) < LOW_RESOLUTION_LONG_SIDE:
        issues.append("low_resolution")

    # Blur as a ratio, so the amount of writing on the page does not decide it: a sharp
    # picture loses much of its edge energy when blurred once more, a blurred one barely
    # changes. An absolute edge measure called every sparse, sharp page blurred.
    energy = _edge_energy(gray)
    sharpness = 0.0
    if energy > 0.5:
        sharpness = max(0.0, 1.0 - _edge_energy(gray.filter(ImageFilter.GaussianBlur(1.2))) / energy)
        if sharpness < BLUR_RATIO:
            issues.append("blurred")

    mask = _ink_mask(gray)
    page_ink = _ink_fraction(mask)
    if page_ink > 0.002 and _writing_at_edge(mask):
        issues.append("content_at_edge")
    return QualityReport(tuple(issues), {
        "mean": round(mean, 1), "contrast": contrast, "sharpness": round(sharpness, 3),
        "page_ink": round(page_ink, 4), "width": float(width), "height": float(height),
    })


# ---- encoding -----------------------------------------------------------------------------

def _looks_like_line_art(image: Image.Image) -> bool:
    """Screenshots and scans with few colours compress better, and stay crisper, as PNG."""

    sample = ImageOps.contain(image, (400, 400))
    colours = sample.getcolors(maxcolors=4096)
    return colours is not None and len(colours) <= 256


def encode(image: Image.Image, *, max_edge: int, max_bytes: int | None = None) -> tuple[bytes, str]:
    """Resize to `max_edge` (never enlarge) and encode, stepping down until under `max_bytes`.

    JPEG uses 4:4:4 chroma: the default 4:2:0 halves colour resolution, which is exactly
    what a thin blue pen stroke on white is made of.
    """

    image = to_rgb(image)
    scale = min(1.0, max_edge / max(image.size))
    line_art = _looks_like_line_art(image)
    for attempt in range(8):
        working = image
        if scale < 1.0:
            working = image.resize(
                (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                Image.Resampling.LANCZOS)
        output = io.BytesIO()
        if line_art:
            working.save(output, format="PNG", optimize=True)
            mime = "image/png"
        else:
            quality = (92, 85, 78)[min(attempt, 2)]
            working.save(output, format="JPEG", quality=quality, subsampling=0, optimize=True)
            mime = "image/jpeg"
        data = output.getvalue()
        if max_bytes is None or len(data) <= max_bytes:
            return data, mime
        if line_art and attempt == 0:
            line_art = False  # a busy "line art" image: JPEG before shrinking it
            continue
        if not line_art and attempt < 2:
            continue  # lower the quality before touching the resolution
        scale *= 0.85
    return data, mime


def provider_copy(data: bytes, limits: IntakeLimits | None = None) -> tuple[bytes, str]:
    """The copy of a stored image an AI provider receives: within its size and edge limits."""

    limits = limits or IntakeLimits()
    try:
        with Image.open(io.BytesIO(data)) as probe:
            width, height = probe.size
            mime = Image.MIME.get(probe.format or "", "")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ImageProblem("image_corrupt", "The stored image could not be reopened.") from error
    if (max(width, height) <= limits.provider_edge and len(data) <= limits.provider_bytes
            and mime in IMAGE_TYPES):
        return data, mime
    image, _rotated = decode(data, IntakeLimits(max_bytes=max(limits.max_bytes, len(data)),
                                                max_pixels=limits.max_pixels, max_side=limits.max_side,
                                                min_side=1))
    return encode(image, max_edge=limits.provider_edge, max_bytes=limits.provider_bytes)


def image_size(data: bytes) -> tuple[int, int]:
    with Image.open(io.BytesIO(data)) as probe:
        return probe.size


def data_url(data: bytes, mime_type: str) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def fit_data_url(url: str, limits: IntakeLimits | None = None, *, max_characters: int | None = None) -> str:
    """Shrink an oversized image data URL to the provider limit; anything else is unchanged.

    The gateway's safety net, so no call site can send a picture a provider will refuse.
    """

    limits = limits or IntakeLimits()
    ceiling = max_characters or (limits.provider_bytes * 4 // 3 + 64)
    text = str(url or "")
    if len(text) <= ceiling or not text.startswith("data:") or ";base64," not in text:
        return text
    try:
        raw = base64.b64decode(text.split(";base64,", 1)[1], validate=True)
        data, mime = provider_copy(raw, limits)
    except (binascii.Error, ValueError):
        return text  # not an image we can read: the adapter reports it, not this
    return data_url(data, mime)


# ---- the whole intake ---------------------------------------------------------------------

def prepare(data: bytes, limits: IntakeLimits | None = None) -> PreparedImage:
    """Check, decode, orient, assess and re-encode one uploaded photo."""

    limits = limits or IntakeLimits()
    source_mime = check_bytes(data, limits)
    image, rotated = decode(data, limits)
    quality = assess_quality(image)
    stored, mime = encode(image, max_edge=limits.storage_edge)
    with Image.open(io.BytesIO(stored)) as probe:
        width, height = probe.size
    return PreparedImage(
        data=stored, mime_type=mime, width=width, height=height,
        source_sha256=hashlib.sha256(data).hexdigest(), source_mime=source_mime,
        rotated_by_exif=rotated, quality=quality,
    )


def rotate(data: bytes, degrees: int) -> tuple[bytes, str]:
    """Turn a stored image clockwise by 90/180/270 degrees, keeping its encoding rules."""

    image, _ = decode(data, IntakeLimits(min_side=1, max_bytes=max(len(data), 1)))
    turned = image.rotate(-int(degrees) % 360, expand=True)
    return encode(turned, max_edge=max(turned.size))


def limits_from_config(config: Any) -> IntakeLimits:
    """The configured limits; config.py validates the values at startup."""

    defaults = IntakeLimits()

    def number(name: str, default: int) -> int:
        try:
            return int(config.get(name, default))
        except (TypeError, ValueError):
            return default

    return IntakeLimits(
        max_bytes=number("VISION_MAX_UPLOAD_BYTES", defaults.max_bytes),
        max_pixels=number("VISION_MAX_PIXELS", defaults.max_pixels),
        storage_edge=number("VISION_STORAGE_EDGE_PX", defaults.storage_edge),
        provider_edge=number("VISION_PROVIDER_EDGE_PX", defaults.provider_edge),
        provider_bytes=number("VISION_PROVIDER_MAX_BYTES", defaults.provider_bytes),
    )

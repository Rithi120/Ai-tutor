"""Synthetic, project-owned images with known ground truth for the image-reading suite.

Every case is drawn here, so its exact text is known and nothing third-party (or a real
student's notebook) is needed in the repository. What these images are and are not:

- "Handwriting" is a font drawn glyph by glyph with jitter: uneven size, baseline and
  slant, pencil-grey ink, ruled or squared paper. It exercises the pipeline - sizing,
  orientation, lighting, cut-off detection - but it is NOT real student handwriting, and
  a result measured on it says nothing about how well a model reads a real cursive hand.
  scripts/vision_eval.py --live --cases DIR measures that on real photos you supply.
- Text is drawn with a system font when one with German letters exists; otherwise the
  German letters are transliterated (ä -> ae) and the transliteration is the ground
  truth. `truth` is always exactly what was drawn.
"""

from __future__ import annotations

import io
import math
import os
import random
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

A4 = (1240, 1754)  # A4 at 150 dpi
PAPER = (246, 244, 236)
DESK = (120, 98, 78)

_REGULAR_FONTS = (
    os.environ.get("VISION_FIXTURE_FONT", ""),
    "C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
)
_SCRIPT_FONTS = (
    os.environ.get("VISION_FIXTURE_SCRIPT_FONT", ""),
    "C:/Windows/Fonts/segoesc.ttf",
    "C:/Windows/Fonts/Inkfree.ttf",
    "C:/Windows/Fonts/comic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
)
_TRANSLITERATION = str.maketrans({
    "ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss",
    "²": "^2", "³": "^3", "−": "-", "·": "*", "×": "x", "÷": ":", "√": "sqrt", "π": "pi",
})


@dataclass(frozen=True)
class FontChoice:
    font: Any
    name: str
    covers_german: bool


@dataclass(frozen=True)
class VisionCase:
    """One test image and what is known about it."""

    name: str
    data: bytes
    truth: str = ""                       # the text drawn, lines joined by "\n"
    kind: str = "printed"                 # printed | handwriting | math | table | diagram | file
    pages: tuple[bytes, ...] = ()         # extra images for multi-image cases
    issues: frozenset[str] = frozenset()  # quality issues the intake should report
    rotation: int = 0                     # clockwise degrees needed to make it upright
    reject: str = ""                      # ImageProblem code the intake must raise
    math_truth: tuple[str, ...] = ()      # expressions, for structural comparison
    labels: tuple[str, ...] = ()          # diagram/table labels that must be found
    note: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _find(paths: tuple[str, ...]) -> str:
    for path in paths:
        if path and os.path.exists(path):
            return path
    return ""


@lru_cache(maxsize=32)
def font(size: int, script: bool = False) -> FontChoice:
    path = _find(_SCRIPT_FONTS if script else _REGULAR_FONTS) or (_find(_REGULAR_FONTS) if script else "")
    if path:
        loaded = ImageFont.truetype(path, size)
        return FontChoice(loaded, os.path.basename(path), _covers(loaded, "äöüßÄÖÜ²−·"))
    loaded = ImageFont.load_default(size=size)
    return FontChoice(loaded, "pillow-default", _covers(loaded, "äöüßÄÖÜ²−·"))


def _covers(face: Any, characters: str) -> bool:
    def signature(character: str) -> bytes:
        canvas = Image.new("L", (64, 64), 0)
        ImageDraw.Draw(canvas).text((4, 4), character, fill=255, font=face)
        return canvas.tobytes()

    missing = signature("\uffff")
    return all(signature(character) != missing for character in characters)


def drawable(text: str, choice: FontChoice) -> str:
    return text if choice.covers_german else text.translate(_TRANSLITERATION)


# ---- drawing ------------------------------------------------------------------------------

def page(lines: list[str], *, size: tuple[int, int] = A4, font_size: int = 46, script: bool = False,
         jitter: float = 0.0, ink: tuple[int, int, int] = (28, 30, 70), paper: tuple[int, int, int] = PAPER,
         ruled: bool = False, squared: bool = False, top: int = 150, left: int = 110,
         line_gap: float = 1.9, seed: int = 0, allow_overflow: bool = False) -> tuple[Image.Image, str]:
    """Draw lines of text on paper. Returns the image and the text actually drawn."""

    rng = random.Random(seed)
    choice = font(font_size, script)
    image = Image.new("RGB", size, paper)
    draw = ImageDraw.Draw(image)
    step = round(font_size * line_gap)
    if ruled:
        for y in range(top + font_size + 8, size[1] - 40, step):
            draw.line((0, y, size[0], y), fill=(176, 196, 226), width=2)
    if squared:
        cell = max(24, font_size // 2)
        for x in range(0, size[0], cell):
            draw.line((x, 0, x, size[1]), fill=(200, 212, 230), width=1)
        for y in range(0, size[1], cell):
            draw.line((0, y, size[0], y), fill=(200, 212, 230), width=1)
    drawn: list[str] = []
    y = top
    for line in lines:
        text = drawable(line, choice)
        drawn.append(text)
        if jitter <= 0:
            draw.text((left, y), text, fill=ink, font=choice.font)
            right = left + draw.textlength(text, font=choice.font)
        else:
            right = _jittered_line(image, (left, y), text, choice, ink, jitter, rng, font_size)
        if right > size[0] - 12 and not allow_overflow:
            # A line that runs off the page makes its own ground truth wrong: what is
            # "known" to be on the image is no longer there. Fail loudly instead.
            raise ValueError(f"fixture line does not fit the page: {text!r}")
        y += step
    return image, "\n".join(drawn)


def _jittered_line(image: Image.Image, origin: tuple[int, int], text: str, choice: FontChoice,
                   ink: tuple[int, int, int], jitter: float, rng: random.Random, font_size: int) -> int:
    """Glyph by glyph: size, slant, baseline and darkness vary like an unhurried hand.

    Returns the right edge of the drawn line."""

    x, y = origin
    right = x
    for character in text:
        if character == " ":
            x += round(font_size * (0.28 + rng.uniform(0, 0.18) * jitter))
            continue
        scale = 1 + rng.uniform(-0.12, 0.12) * jitter
        glyph_font = choice.font
        box = glyph_font.getbbox(character)
        glyph_w, glyph_h = max(1, box[2] - box[0] + 8), max(1, box[3] + 12)
        glyph = Image.new("L", (glyph_w, glyph_h), 0)
        ImageDraw.Draw(glyph).text((4 - box[0], 4), character, fill=255, font=glyph_font)
        if scale != 1:
            glyph = glyph.resize((max(1, round(glyph_w * scale)), max(1, round(glyph_h * scale))),
                                 Image.Resampling.BICUBIC)
        glyph = glyph.rotate(rng.uniform(-7, 7) * jitter, resample=Image.Resampling.BICUBIC, expand=True)
        shade = rng.uniform(0.75, 1.0)
        colour = tuple(round(255 - (255 - channel) * shade) for channel in ink)
        layer = Image.new("RGB", glyph.size, colour)
        # Bottom-aligned, so a smaller glyph sits on the line instead of floating above it.
        baseline_shift = round(glyph_h * (1 - scale)) + round(rng.uniform(-3, 3) * jitter)
        image.paste(layer, (x, y + baseline_shift), glyph)
        right = x + glyph.width
        x += round((box[2] - box[0]) * scale + rng.uniform(0, 3) * jitter + 1)
    return max(x, right)


def encode(image: Image.Image, fmt: str = "JPEG", **options: Any) -> bytes:
    output = io.BytesIO()
    if fmt == "JPEG":
        image.convert("RGB").save(output, format="JPEG", quality=options.pop("quality", 90), **options)
    else:
        image.save(output, format=fmt, **options)
    return output.getvalue()


def on_desk(sheet: Image.Image, size: tuple[int, int], *, angle: float = 0.0, seed: int = 0,
            noise: float = 0.06) -> Image.Image:
    """Photograph a sheet: on a desk, maybe at an angle, with a little sensor noise."""

    canvas = Image.new("RGB", size, DESK)
    fit = min(size[0] * 0.86 / sheet.width, size[1] * 0.9 / sheet.height)
    placed = sheet.resize((round(sheet.width * fit), round(sheet.height * fit)), Image.Resampling.LANCZOS)
    if angle:
        mask = Image.new("L", placed.size, 255).rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)
        placed = placed.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=DESK)
    else:
        mask = None
    canvas.paste(placed, ((size[0] - placed.width) // 2, (size[1] - placed.height) // 2), mask)
    if noise:
        grain = Image.effect_noise(size, 28).convert("RGB")
        canvas = Image.blend(canvas, grain, noise)
    return canvas


def darken(image: Image.Image, factor: float, *, seed: int = 0) -> Image.Image:
    dim = image.point([min(255, round(value * factor)) for value in range(256)] * len(image.getbands()))
    grain = Image.effect_noise(image.size, 20).convert("RGB")
    return Image.blend(dim, grain, 0.05)


def with_exif_orientation(upright: Image.Image, orientation: int = 6) -> bytes:
    """A JPEG whose pixels are sideways and whose EXIF tag says how to turn them."""

    turned = {6: upright.rotate(90, expand=True), 8: upright.rotate(-90, expand=True),
              3: upright.rotate(180)}[orientation]
    exif = Image.Exif()
    exif[0x0112] = orientation
    return encode(turned, "JPEG", exif=exif.tobytes())


def _png_header_only(width: int, height: int) -> bytes:
    """A PNG that claims a huge size: refused from the header, never decoded."""

    import struct
    import zlib
    raw = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    chunk = b"IHDR" + raw
    ihdr = struct.pack(">I", len(raw)) + chunk + struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
    idat_body = zlib.compress(b"\x00" * 64)
    idat = struct.pack(">I", len(idat_body)) + b"IDAT" + idat_body + struct.pack(
        ">I", zlib.crc32(b"IDAT" + idat_body) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


# ---- the cases ----------------------------------------------------------------------------

GERMAN_PRINTED = [
    "Die Photosynthese findet in den Chloroplasten statt.",
    "Aus Kohlenstoffdioxid und Wasser entstehen Glucose",
    "und Sauerstoff. Licht liefert die nötige Energie.",
    "Merke: Ohne Chlorophyll keine Photosynthese.",
]
GERMAN_NOTES = [
    "Hausaufgabe Mathe bis Freitag",
    "Seite 42 Nummer 3 und 5",
    "Wiederholen: binomische Formeln",
    "Test am Dienstag über Brüche",
]
ENGLISH_NOTES = [
    "The French Revolution began in 1789",
    "Causes: debt, hunger and new ideas",
    "Check the timeline on page 17",
]
MATH_LINES = [
    "3x + 5 = 20",
    "3x = 15",
    "x = 5",
    "a² + b² = c²",
    "−4,5 · 2 = −9",
]
MATH_TRUTH = ("3x+5=20", "3x=15", "x=5", "a^2+b^2=c^2", "-4,5*2=-9")


def build_cases(*, include_large: bool = True, seed: int = 7) -> list[VisionCase]:
    """Every case in the suite. Deterministic for a given seed and font set."""

    cases: list[VisionCase] = []
    # The distortions vary with the seed as well as the jitter, so a second seed is a
    # genuinely different set of photos for the quality thresholds, not the same one.
    knobs = random.Random(seed * 7919)
    tilt = round(knobs.uniform(4, 9), 1)
    darkness = round(knobs.uniform(0.22, 0.32), 2)
    blur = round(knobs.uniform(2.6, 3.8), 1)
    cut_at = knobs.randint(1020, 1240)
    printed, printed_truth = page(GERMAN_PRINTED, seed=seed)
    cases.append(VisionCase("printed_de_clear", encode(printed), printed_truth, "printed"))

    notes, notes_truth = page(GERMAN_NOTES, font_size=40, script=True, jitter=1.0, ruled=True, seed=seed)
    cases.append(VisionCase("handwriting_de_ruled", encode(on_desk(notes, (1600, 2133), seed=seed)),
                            notes_truth, "handwriting", note="simulated handwriting on ruled paper"))

    english, english_truth = page(ENGLISH_NOTES, font_size=40, script=True, jitter=1.3, squared=True, seed=seed + 1)
    cases.append(VisionCase("handwriting_en_squared", encode(english), english_truth, "handwriting",
                            note="simulated uneven handwriting on squared paper"))

    pencil, pencil_truth = page(GERMAN_NOTES[:3], font_size=40, script=True, jitter=1.0, ink=(150, 150, 150),
                                seed=seed + 2)
    cases.append(VisionCase("pencil_light", encode(pencil), pencil_truth, "handwriting",
                            issues=frozenset(), note="light pencil strokes"))

    math_page, math_drawn = page(MATH_LINES, font_size=52, script=True, jitter=0.8, squared=True, seed=seed + 3)
    cases.append(VisionCase("math_handwritten", encode(math_page), math_drawn, "math",
                            math_truth=MATH_TRUTH, note="calculation lines with exponents, decimal comma and minus signs"))

    tilted = on_desk(printed, (1600, 2133), angle=tilt, seed=seed)
    cases.append(VisionCase("tilted", encode(tilted), printed_truth, "printed", note=f"page photographed at {tilt}°"))

    low_light = darken(on_desk(notes, (1600, 2133), seed=seed), darkness)
    cases.append(VisionCase("low_light", encode(low_light), notes_truth, "handwriting", issues=frozenset({"dark"})))

    blurred = printed.filter(ImageFilter.GaussianBlur(blur))
    cases.append(VisionCase("blurred", encode(blurred), printed_truth, "printed", issues=frozenset({"blurred"})))

    cases.append(VisionCase("sideways_no_exif", encode(printed.rotate(90, expand=True)), printed_truth,
                            "printed", rotation=90, note="phone held sideways, no rotation tag"))
    cases.append(VisionCase("exif_rotated", with_exif_orientation(printed, 6), printed_truth, "printed",
                            note="pixels sideways, EXIF orientation 6 says how to turn them"))

    wide_lines = ["Die Summe der Innenwinkel im Dreieck beträgt 180 Grad und zwar immer",
                  "Ein rechter Winkel misst genau 90 Grad, ein gestreckter Winkel 180 Grad",
                  "Spitze Winkel sind kleiner als 90 Grad, stumpfe Winkel liegen darüber",
                  "Nebenwinkel ergänzen sich zu 180 Grad, Scheitelwinkel sind gleich groß",
                  "Im gleichseitigen Dreieck misst jeder Innenwinkel genau 60 Grad"]
    wide, wide_truth = page(wide_lines, size=(1700, 1000), font_size=44, left=60, top=120, seed=seed)
    # The truth of this case is the full lines; the photo then cuts them, which is the point.
    cut = wide.crop((0, 0, cut_at, 1000))
    cases.append(VisionCase("cut_off_right", encode(cut), wide_truth, "printed",
                            issues=frozenset({"content_at_edge"}), note="lines run off the right edge"))

    small, small_truth = page(GERMAN_NOTES[:2], size=(560, 420), font_size=26, left=30, top=60, seed=seed)
    cases.append(VisionCase("low_resolution", encode(small), small_truth, "printed",
                            issues=frozenset({"low_resolution"})))

    cases.append(_table_case(seed))
    cases.append(_diagram_case())

    first, first_truth = page(GERMAN_NOTES[:2], font_size=40, script=True, jitter=0.8, seed=seed + 4)
    second, second_truth = page(GERMAN_NOTES[2:], font_size=40, script=True, jitter=0.8, seed=seed + 5)
    cases.append(VisionCase("two_pages", encode(first), f"{first_truth}\n{second_truth}", "handwriting",
                            pages=(encode(second),), note="one question across two photos"))

    screenshot = Image.new("RGBA", (1200, 500), (0, 0, 0, 0))
    choice = font(48)
    shot_text = drawable("Aufgabe 4: Berechne den Flächeninhalt.", choice)
    ImageDraw.Draw(screenshot).text((40, 200), shot_text, fill=(0, 0, 0, 255), font=choice.font)
    cases.append(VisionCase("transparent_screenshot", encode(screenshot, "PNG"), shot_text, "printed",
                            note="black text on a transparent background"))

    if include_large:
        phone = on_desk(notes, (4032, 3024), seed=seed)
        cases.append(VisionCase("phone_12mp", encode(phone, quality=88), notes_truth, "handwriting",
                                note="a 12-megapixel phone photo"))

    cases.extend([
        VisionCase("corrupt_jpeg", encode(printed)[:4000], kind="file", reject="image_corrupt"),
        VisionCase("gif_unsupported", encode(printed.convert("P"), "GIF"), kind="file", reject="unsupported_type"),
        VisionCase("heic_header", b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic" + b"\x00" * 64,
                   kind="file", reject="heic_unsupported"),
        VisionCase("pdf_as_photo", b"%PDF-1.7\n" + b"0" * 200, kind="file", reject="pdf_not_image"),
        VisionCase("pixel_bomb", _png_header_only(30_000, 30_000), kind="file", reject="too_many_pixels"),
        VisionCase("empty_file", b"", kind="file", reject="empty_file"),
        VisionCase("text_renamed", b"just some text pretending to be a photo", kind="file",
                   reject="unsupported_type"),
    ])
    return cases


def _table_case(seed: int) -> VisionCase:
    rows = [["Stoff", "Formel", "Masse"], ["Wasser", "H2O", "18 g/mol"], ["Kohlendioxid", "CO2", "44 g/mol"]]
    choice = font(40)
    image = Image.new("RGB", (1240, 520), PAPER)
    draw = ImageDraw.Draw(image)
    widths, top, height = (430, 330, 330), 60, 120
    x0 = 70
    for row_index, row in enumerate(rows):
        x = x0
        for column, cell in enumerate(row):
            draw.rectangle((x, top + row_index * height, x + widths[column], top + (row_index + 1) * height),
                           outline=(40, 40, 40), width=3)
            draw.text((x + 20, top + row_index * height + 38), drawable(cell, choice), fill=(20, 20, 20),
                      font=choice.font)
            x += widths[column]
    truth = "\n".join(" | ".join(drawable(cell, choice) for cell in row) for row in rows)
    return VisionCase("table_3x3", encode(image, "PNG"), truth, "table",
                      labels=tuple(drawable(cell, choice) for row in rows for cell in row))


def _diagram_case() -> VisionCase:
    choice = font(44)
    image = Image.new("RGB", (1100, 900), PAPER)
    draw = ImageDraw.Draw(image)
    a, b, c = (150, 750), (950, 750), (150, 250)
    draw.line((a, b, c, a), fill=(20, 20, 20), width=5)
    draw.rectangle((150, 690, 210, 750), outline=(20, 20, 20), width=3)  # the right angle
    for label, point in (("A", (105, 760)), ("B", (965, 760)), ("C", (110, 200))):
        draw.text(point, label, fill=(20, 20, 20), font=choice.font)
    draw.text((520, 770), "c = 8 cm", fill=(20, 20, 20), font=choice.font)
    draw.text((40, 470), "b = 5 cm", fill=(20, 20, 20), font=choice.font)
    hyp = math.dist(b, c)
    draw.text((600, 420), "a = ?", fill=(20, 20, 20), font=choice.font)
    return VisionCase("right_triangle", encode(image, "PNG"), "", "diagram",
                      labels=("A", "B", "C", "c = 8 cm", "b = 5 cm", "a = ?"),
                      note="right triangle, legs labelled, hypotenuse unknown",
                      meta={"hypotenuse_px": round(hyp)})

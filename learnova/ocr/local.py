"""Local OCR: text with word positions, on this machine, in well under a second.

The vision-model route reads a page by sending the photo to a provider and waiting for
a reasoning model to type the text back - tens of seconds when it works, and minutes
of nothing when the provider is unreachable, which is how a scan came to take eight
minutes and fail. A page of printed text does not need a language model to be read.
RapidOCR (PaddleOCR's PP-OCR models on ONNX Runtime) reads a 1200px page in about 0.4 s
on a laptop CPU, returns every line with its box and, with `return_word_box`, every
word with its box - exactly what a tap-a-word scanner needs.

The engine is a seam: `recognize()` is the only function the application calls, and it
raises `LocalOcrUnavailable` with a plain reason when no engine can run, so the caller
can say so instead of crashing. The heavy import happens lazily and once.
"""

from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from learnova.vocabulary.layout import Box, Line, Word, words_from_text

ENGINES = ("rapidocr",)
# The multilingual PP-OCRv6 model covers every Latin-script language the trainer
# supports; the language code only chooses the dictionary the engine resolves, and the
# same model is downloaded whichever of these is given.
SUPPORTED_HINTS = frozenset({"en", "de", "fr", "es", "it", "pt", "nl"})
MAX_SIDE = 1600       # px: longer than this adds time, not words
MIN_SIDE = 320        # px: smaller than this is a thumbnail, not a page
_LOCK = threading.Lock()
_ENGINE: Any = None
_ENGINE_HINT = ""


class LocalOcrUnavailable(RuntimeError):
    """No local engine can run here; the message says why in one sentence."""


@dataclass
class LocalOcrResult:
    lines: list[Line]
    width: int
    height: int
    engine: str
    duration_ms: int
    warnings: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)


def engine_name(config: Any) -> str:
    """Which engine configuration asks for: "rapidocr", or "" for none."""

    wanted = str(_get(config, "LOCAL_OCR_ENGINE", "auto") or "auto").strip().lower()
    if wanted == "off":
        return ""
    if wanted in ("auto", "rapidocr"):
        return "rapidocr" if _rapidocr_importable() else ""
    return ""


def available(config: Any) -> bool:
    return bool(engine_name(config))


def language_hint(config: Any) -> str:
    hint = str(_get(config, "LOCAL_OCR_LANGUAGE", "de") or "de").strip().lower()
    return hint if hint in SUPPORTED_HINTS else "de"


def warm_up(config: Any) -> str:
    """Load the engine (downloading its models on first use) so the first scan is fast."""

    name = engine_name(config)
    if not name:
        raise LocalOcrUnavailable("No local OCR engine is installed (pip install rapidocr onnxruntime).")
    _engine(language_hint(config))
    return name


def recognize(data: bytes, config: Any) -> LocalOcrResult:
    """Read one photo: lines in reading order, each with its words and boxes (0..1)."""

    name = engine_name(config)
    if not name:
        raise LocalOcrUnavailable("No local OCR engine is installed (pip install rapidocr onnxruntime).")
    started = time.monotonic()
    image, warnings = _prepare(data)
    engine = _engine(language_hint(config))
    try:
        import numpy as np
    except ImportError as error:  # pragma: no cover - numpy ships with the engine
        raise LocalOcrUnavailable("numpy is not installed.") from error
    array = np.array(image)[:, :, ::-1]  # PIL RGB -> OpenCV BGR, which the models expect
    output = engine(array, return_word_box=True)
    width, height = image.size
    lines = _lines_from_output(output, width, height)
    return LocalOcrResult(lines, width, height, name, int((time.monotonic() - started) * 1000), warnings)


# ---- internals ------------------------------------------------------------------------

def _get(config: Any, key: str, default: Any = None) -> Any:
    if config is None:
        return default
    if hasattr(config, "get"):
        return config.get(key, default)
    return getattr(config, key, default)


def _rapidocr_importable() -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec("rapidocr") is not None and importlib.util.find_spec("onnxruntime") is not None
    except (ImportError, ValueError):
        return False


def _engine(hint: str) -> Any:
    global _ENGINE, _ENGINE_HINT
    with _LOCK:
        if _ENGINE is not None and _ENGINE_HINT == hint:
            return _ENGINE
        try:
            from rapidocr import RapidOCR  # pyright: ignore[reportMissingImports]
        except ImportError as error:
            raise LocalOcrUnavailable("rapidocr is not installed.") from error
        try:
            _ENGINE = RapidOCR(params={
                "Det.lang_type": hint, "Rec.lang_type": hint, "Global.log_level": "error",
            })
        except Exception as error:  # a failed model download, an unsupported combination
            raise LocalOcrUnavailable(f"The OCR engine could not start: {type(error).__name__}.") from error
        _ENGINE_HINT = hint
        return _ENGINE


def _prepare(data: bytes) -> tuple[Any, list[str]]:
    """Decode, honour the camera's rotation tag, and scale to a sensible working size."""

    from PIL import Image, ImageOps

    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as error:
        raise ValueError("The photo could not be decoded.") from error
    image = ImageOps.exif_transpose(image) or image
    image = image.convert("RGB")
    warnings: list[str] = []
    longest = max(image.size)
    if longest < MIN_SIDE:
        warnings.append("The photo is very small; words may be missed.")
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))))
    return image, warnings


def _lines_from_output(output: Any, width: int, height: int) -> list[Line]:
    texts = list(getattr(output, "txts", None) or [])
    boxes = getattr(output, "boxes", None)
    scores = list(getattr(output, "scores", None) or [])
    word_results = getattr(output, "word_results", None) or ()
    lines: list[Line] = []
    for index, text in enumerate(texts):
        text = " ".join(str(text or "").split())
        if not text:
            continue
        box = _normalise(boxes[index] if boxes is not None and index < len(boxes) else None, width, height)
        confidence = float(scores[index]) if index < len(scores) else 1.0
        words = _words(word_results[index] if index < len(word_results) else None, width, height, confidence)
        if not words or " ".join(word.text for word in words) != text:
            # The engine's word split did not reproduce the line; share the line box out
            # by characters instead, so the words and the text can never disagree.
            words = words_from_text(text, box, confidence)
        lines.append(Line(text, box, round(confidence, 4), words))
    return lines


def _words(raw: Any, width: int, height: int, fallback_confidence: float) -> tuple[Word, ...]:
    words: list[Word] = []
    for item in raw if isinstance(raw, (list, tuple)) else []:
        try:
            text, score, quad = item[0], item[1], item[2]
        except (TypeError, IndexError):
            continue
        text = str(text or "").strip()
        if not text:
            continue
        try:
            confidence = float(score)
        except (TypeError, ValueError):
            confidence = fallback_confidence
        words.append(Word(text, _normalise(quad, width, height), round(confidence, 4)))
    return tuple(words)


def _normalise(quad: Any, width: int, height: int) -> Box:
    """A 4-point polygon (pixels) to a normalised (x0, y0, x1, y1)."""

    try:
        points = [(float(point[0]), float(point[1])) for point in quad]
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0, 1.0, 1.0)
    if not points or width <= 0 or height <= 0:
        return (0.0, 0.0, 1.0, 1.0)
    xs = [max(0.0, min(1.0, x / width)) for x, _ in points]
    ys = [max(0.0, min(1.0, y / height)) for _, y in points]
    return (round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4))

"""The structured result of reading one or more images, shared by every feature.

A model's answer is untrusted: everything here is bounded, typed and defaulted, and
nothing is invented to fill a gap. In particular:

- `confidence` is only ever the model's own number. When the model gave none, it is None
  and `confidence_reported` is False - a made-up 0.5 would look like a measurement.
- Uncertain readings stay uncertain. The literal reading is kept, the alternatives are
  listed, and inline the reading is wrapped as "[?7]" so a student, a grader or a tutor
  can see which characters were guessed at.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from typing import Any

BLOCK_TYPES = frozenset({
    "printed_text", "handwriting", "formula", "table", "diagram", "heading",
    "annotation", "crossed_out", "uncertain",
})
IMAGE_KINDS = frozenset({
    "handwritten_notes", "printed_page", "worksheet", "mixed", "diagram", "graph", "table",
    "screenshot", "photo", "other",
})
AUTHORS = frozenset({"printed", "student", "teacher", "unknown"})
LEGIBILITY = frozenset({"clear", "partly_unclear", "unreadable"})
UNCLEAR_MARK = re.compile(r"\[\?([^\[\]]{0,80})\]")

MAX_BLOCKS = 200
MAX_TEXT = 4000


@dataclass(frozen=True)
class ImageInfo:
    index: int
    kind: str = "other"
    rotation: int = 0          # clockwise degrees the model says the content is turned
    cut_off: bool = False      # the model sees writing running off the edge
    legibility: str = "clear"


@dataclass(frozen=True)
class Block:
    image: int
    order: int
    type: str
    text: str
    latex: str = ""
    bbox: tuple[float, ...] = ()
    author: str = "unknown"
    confidence: float | None = None

    @property
    def unclear(self) -> bool:
        return bool(UNCLEAR_MARK.search(self.text)) or self.type == "uncertain"


@dataclass(frozen=True)
class Diagram:
    image: int
    description: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class Uncertainty:
    image: int
    text: str
    alternatives: tuple[str, ...] = ()
    reason: str = ""
    affects_answer: bool = False


@dataclass(frozen=True)
class VisualReading:
    images: tuple[ImageInfo, ...] = ()
    blocks: tuple[Block, ...] = ()
    diagrams: tuple[Diagram, ...] = ()
    uncertain: tuple[Uncertainty, ...] = ()
    clarification_question: str = ""
    summary: str = ""
    language: str = ""
    warnings: tuple[str, ...] = ()

    @property
    def confidence_reported(self) -> bool:
        return any(block.confidence is not None for block in self.blocks)

    @property
    def confidence(self) -> float | None:
        values = [block.confidence for block in self.blocks
                  if block.confidence is not None and block.type != "crossed_out"]
        return round(sum(values) / len(values), 3) if values else None

    @property
    def readable(self) -> bool:
        return any(block.text.strip() and block.type != "crossed_out" for block in self.blocks) \
            or any(diagram.description for diagram in self.diagrams)

    @property
    def needs_clarification(self) -> bool:
        return bool(self.clarification_question) or any(item.affects_answer for item in self.uncertain)

    def text(self, image: int | None = None, *, include_crossed_out: bool = False) -> str:
        """Readable text in reading order, uncertain parts still marked as [?…]."""

        return "\n".join(
            block.text for block in self.blocks
            if block.text and (image is None or block.image == image)
            and (include_crossed_out or block.type != "crossed_out") and block.type != "diagram")

    def student_text(self) -> str:
        """What the student wrote, separated from the printed task where the model could tell."""

        own = [block for block in self.blocks
               if block.author == "student" and block.type != "crossed_out" and block.text]
        return "\n".join(block.text for block in own) if own else self.text()

    def with_warnings(self, *extra: str) -> "VisualReading":
        merged = tuple(dict.fromkeys([*self.warnings, *[item for item in extra if item]]))
        return VisualReading(self.images, self.blocks, self.diagrams, self.uncertain,
                             self.clarification_question, self.summary, self.language, merged)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["confidence"] = self.confidence
        data["confidence_reported"] = self.confidence_reported
        data["needs_clarification"] = self.needs_clarification
        return data


# ---- normalising a model answer -----------------------------------------------------------

def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return str(value or "").replace("\r\n", "\n").strip()[:limit]


def _index(value: Any, count: int, default: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if 1 <= number <= max(1, count) else default


def _confidence(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _bbox(value: Any) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return ()
    try:
        numbers = tuple(max(0.0, min(1.0, float(item))) for item in value)
    except (TypeError, ValueError):
        return ()
    return numbers if numbers[2] > numbers[0] and numbers[3] > numbers[1] else ()


def _strings(value: Any, limit: int = 12, length: int = 120) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(_text(item, length) for item in value if _text(item, length)))[:limit]


def normalize_reading(payload: Any, image_count: int) -> VisualReading:
    """A safe VisualReading from whatever the model returned. Never raises."""

    if not isinstance(payload, dict):
        return VisualReading(warnings=("The image reading could not be interpreted.",))
    count = max(1, int(image_count or 1))
    images: dict[int, ImageInfo] = {}
    for raw in payload.get("images") or []:
        if not isinstance(raw, dict):
            continue
        index = _index(raw.get("index"), count, 0)
        if not index or index in images:
            continue
        try:
            rotation = int(raw.get("rotation") or 0) % 360
        except (TypeError, ValueError):
            rotation = 0
        kind = _text(raw.get("kind"), 40).lower()
        legibility = _text(raw.get("legibility"), 20).lower()
        images[index] = ImageInfo(
            index=index, kind=kind if kind in IMAGE_KINDS else "other",
            rotation=rotation if rotation in (0, 90, 180, 270) else 0,
            cut_off=bool(raw.get("cut_off")),
            legibility=legibility if legibility in LEGIBILITY else "clear")
    for index in range(1, count + 1):
        images.setdefault(index, ImageInfo(index=index))

    blocks: list[Block] = []
    for order, raw in enumerate((payload.get("blocks") or [])[:MAX_BLOCKS], start=1):
        if not isinstance(raw, dict):
            continue
        block_type = _text(raw.get("type"), 30).lower()
        block_type = block_type if block_type in BLOCK_TYPES else "uncertain"
        text = _text(raw.get("text") if raw.get("text") is not None else raw.get("content"))
        if not text and block_type != "diagram":
            continue
        author = _text(raw.get("author"), 20).lower()
        blocks.append(Block(
            image=_index(raw.get("image"), count), order=order, type=block_type, text=text,
            latex=_text(raw.get("latex"), 1000), bbox=_bbox(raw.get("bbox")),
            author=author if author in AUTHORS else "unknown",
            confidence=_confidence(raw.get("confidence"))))

    diagrams = tuple(
        Diagram(image=_index(raw.get("image"), count), description=_text(raw.get("description"), 1500),
                labels=_strings(raw.get("labels"), 30, 80))
        for raw in (payload.get("diagrams") or [])[:20]
        if isinstance(raw, dict) and (_text(raw.get("description")) or raw.get("labels")))

    uncertain: list[Uncertainty] = []
    for raw in (payload.get("uncertain") or [])[:40]:
        if not isinstance(raw, dict) or not _text(raw.get("text"), 200):
            continue
        uncertain.append(Uncertainty(
            image=_index(raw.get("image"), count), text=_text(raw.get("text"), 200),
            alternatives=_strings(raw.get("alternatives"), 5, 80), reason=_text(raw.get("reason"), 200),
            affects_answer=bool(raw.get("affects_answer"))))
    # An inline [?…] the model did not list is still an uncertainty; listing it keeps the
    # "what was unclear" summary complete without trusting the model to be consistent.
    listed = {item.text for item in uncertain}
    for block in blocks:
        for match in UNCLEAR_MARK.finditer(block.text):
            reading = match.group(1).strip()
            if reading and reading not in listed:
                uncertain.append(Uncertainty(image=block.image, text=reading))
                listed.add(reading)

    warnings: list[str] = []
    for info in images.values():
        if info.cut_off:
            warnings.append(f"Image {info.index}: some writing seems to run off the edge of the photo.")
        if info.legibility == "unreadable":
            warnings.append(f"Image {info.index} could not be read.")
    return VisualReading(
        images=tuple(images[index] for index in sorted(images)), blocks=tuple(blocks),
        diagrams=diagrams, uncertain=tuple(uncertain),
        clarification_question=_text(payload.get("clarification_question"), 400),
        summary=_text(payload.get("summary"), 600), language=_text(payload.get("language"), 10).lower(),
        warnings=tuple(warnings))


def reading_from_dict(data: Any) -> VisualReading:
    """Rebuild a stored reading (VisualReading.to_dict) without re-validating the model."""

    if not isinstance(data, dict):
        return VisualReading()

    def rows(name: str) -> list[dict[str, Any]]:
        return [item for item in data.get(name) or [] if isinstance(item, dict)]

    return VisualReading(
        images=tuple(ImageInfo(index=int(item.get("index") or 1), kind=str(item.get("kind") or "other"),
                               rotation=int(item.get("rotation") or 0), cut_off=bool(item.get("cut_off")),
                               legibility=str(item.get("legibility") or "clear")) for item in rows("images")),
        blocks=tuple(Block(image=int(item.get("image") or 1), order=int(item.get("order") or 0),
                           type=str(item.get("type") or "uncertain"), text=str(item.get("text") or ""),
                           latex=str(item.get("latex") or ""), bbox=tuple(item.get("bbox") or ()),
                           author=str(item.get("author") or "unknown"), confidence=item.get("confidence"))
                     for item in rows("blocks")),
        diagrams=tuple(Diagram(image=int(item.get("image") or 1), description=str(item.get("description") or ""),
                               labels=tuple(item.get("labels") or ())) for item in rows("diagrams")),
        uncertain=tuple(Uncertainty(image=int(item.get("image") or 1), text=str(item.get("text") or ""),
                                    alternatives=tuple(item.get("alternatives") or ()),
                                    reason=str(item.get("reason") or ""),
                                    affects_answer=bool(item.get("affects_answer"))) for item in rows("uncertain")),
        clarification_question=str(data.get("clarification_question") or ""),
        summary=str(data.get("summary") or ""), language=str(data.get("language") or ""),
        warnings=tuple(str(item) for item in data.get("warnings") or ()))


def split_reading(reading: VisualReading, count: int) -> list[VisualReading]:
    """One reading per image, each numbered as image 1, so each can be stored with its image.

    What concerns the whole request - the clarification question, the summary, the
    language and the warnings - is kept on every part.
    """

    parts = []
    for index in range(1, max(1, count) + 1):
        info = next((item for item in reading.images if item.index == index), ImageInfo(index=index))
        parts.append(VisualReading(
            images=(replace(info, index=1),),
            blocks=tuple(replace(block, image=1) for block in reading.blocks if block.image == index),
            diagrams=tuple(replace(item, image=1) for item in reading.diagrams if item.image == index),
            uncertain=tuple(replace(item, image=1) for item in reading.uncertain if item.image == index),
            clarification_question=reading.clarification_question, summary=reading.summary,
            language=reading.language, warnings=reading.warnings))
    return parts


# ---- handing a reading to another task -----------------------------------------------------

_KIND_LABELS = {
    "handwritten_notes": "handwritten notes", "printed_page": "printed page", "worksheet": "worksheet",
    "mixed": "printed and handwritten", "diagram": "diagram", "graph": "graph", "table": "table",
    "screenshot": "screenshot", "photo": "photo", "other": "image",
}


def reading_context(reading: VisualReading, *, max_characters: int = 6000, first_image: int = 1) -> str:
    """The reading as labelled text for a downstream prompt, uncertainty kept visible.

    `first_image` renumbers images when several messages' readings share one prompt.
    """

    sections: list[str] = []
    for info in reading.images or (ImageInfo(index=1),):
        number = first_image + info.index - 1
        lines = [f"[Image {number}: {_KIND_LABELS.get(info.kind, 'image')}"
                 + (", partly unclear" if info.legibility == "partly_unclear" else "")
                 + (", unreadable" if info.legibility == "unreadable" else "")
                 + (", writing may run off the edge" if info.cut_off else "") + "]"]
        for block in reading.blocks:
            if block.image != info.index or block.type == "diagram":
                continue
            prefix = {"crossed_out": "(crossed out) ", "annotation": "(note) ", "heading": "## "}.get(block.type, "")
            who = " (written by the student)" if block.author == "student" and block.type != "crossed_out" else ""
            line = f"{prefix}{block.text}{who}"
            if block.latex and block.latex not in block.text:
                line += f"   [LaTeX: {block.latex}]"
            lines.append(line)
        for diagram in reading.diagrams:
            if diagram.image == info.index:
                labels = f" Labels: {', '.join(diagram.labels)}." if diagram.labels else ""
                lines.append(f"(diagram) {diagram.description}{labels}")
        sections.append("\n".join(lines))
    unclear = [item for item in reading.uncertain]
    if unclear:
        rows = []
        for item in unclear[:15]:
            alternatives = f" - could also be {' / '.join(item.alternatives)}" if item.alternatives else ""
            weight = " (matters for the answer)" if item.affects_answer else ""
            rows.append(f"- \"{item.text}\"{alternatives}{weight}")
        sections.append("Unclear readings (marked [?…] above; never treat them as certain):\n" + "\n".join(rows))
    if reading.clarification_question:
        sections.append(f"Suggested question to the student: {reading.clarification_question}")
    text = "\n\n".join(sections).strip()
    if len(text) > max_characters:
        text = text[:max_characters].rsplit("\n", 1)[0] + "\n[… the rest of the reading was shortened]"
    return text

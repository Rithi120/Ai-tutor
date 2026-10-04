"""From OCR lines with positions to the sentence a tapped word sits in. Pure.

An OCR engine returns lines: text plus a box. A page is more than lines - it has
columns, paragraphs and sentences that wrap - and the student tapping "boulangerie"
wants the whole sentence it appears in, not the fragment of a line. Everything here is
geometry and punctuation, so it runs in microseconds and gives the same answer every
time.

Boxes are normalised to the page: (x0, y0, x1, y1) with every value in 0..1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

Box = tuple[float, float, float, float]

_WORD_CHARS = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
# A sentence ends at . ! ? or … followed by space and something that starts a sentence
# (a capital letter, a digit, an opening quote or bracket), or at the end of the text.
# Requiring the capital keeps "z. B." and "etc. the" from splitting.
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[\"'«„“(\[]?[A-ZÀ-ÝŒ0-9])")
_TRAILING_HYPHEN = re.compile(r"[^\W\d_](-|‐|‑)$")
# A full stop after one of these is not the end of a sentence: "z. B.", "Dr. Meier".
# Single letters ("z.", "B.", initials) are covered by the length rule in `sentences`.
ABBREVIATIONS = frozenset("""
    bzw usw vgl etc ca nr abs art bsp evtl ggf inkl zb dr prof mr mrs ms st vs
    ex adj adv subst sing pl fam fig
""".split())
_QUOTES = "\"'«»„“”()[]"
MAX_SENTENCE_CHARS = 320


@dataclass(frozen=True)
class Word:
    text: str          # as printed, punctuation included
    box: Box
    confidence: float = 1.0

    @property
    def clean(self) -> str:
        """The word to look up: letters, inner apostrophes and hyphens only."""

        match = _WORD_CHARS.search(self.text)
        return match.group(0) if match else ""


@dataclass(frozen=True)
class Line:
    text: str
    box: Box
    confidence: float = 1.0
    words: tuple[Word, ...] = ()


@dataclass
class Block:
    """Lines that belong together: one paragraph, or one cell of a table."""

    lines: list[int] = field(default_factory=list)
    box: Box = (0.0, 0.0, 0.0, 0.0)


def _height(box: Box) -> float:
    return max(0.0, box[3] - box[1])


def _overlap(a: Box, b: Box) -> float:
    """Horizontal overlap as a share of the narrower box."""

    left, right = max(a[0], b[0]), min(a[2], b[2])
    narrower = min(a[2] - a[0], b[2] - b[0])
    if right <= left or narrower <= 0:
        return 0.0
    return (right - left) / narrower


def _union(a: Box, b: Box) -> Box:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def words_from_text(text: str, box: Box, confidence: float = 1.0) -> tuple[Word, ...]:
    """Split a line into words, sharing the line's box out by character count.

    Used when the engine gives line boxes only. A word's box then marks roughly where
    it sits on the line, which is all a tap target needs.
    """

    parts = text.split()
    if not parts:
        return ()
    total = sum(len(part) for part in parts) + max(0, len(parts) - 1)
    width = box[2] - box[0]
    words: list[Word] = []
    cursor = 0
    for part in parts:
        x0 = box[0] + width * (cursor / total) if total else box[0]
        x1 = box[0] + width * ((cursor + len(part)) / total) if total else box[2]
        words.append(Word(part, (round(x0, 4), box[1], round(x1, 4), box[3]), confidence))
        cursor += len(part) + 1
    return tuple(words)


def group_blocks(lines: list[Line]) -> list[Block]:
    """Join vertically adjacent, horizontally overlapping lines into blocks.

    Two lines belong together when the gap between them is small against the line
    height and they share most of their width. A column break or a blank line starts a
    new block. The result is in reading order: left column first, then right, top to
    bottom within a column; a line wider than half the page is treated as full width.
    """

    order = sorted(range(len(lines)), key=lambda index: (round(lines[index].box[1], 3), lines[index].box[0]))
    heights = sorted(_height(lines[index].box) for index in order if _height(lines[index].box) > 0)
    typical = heights[len(heights) // 2] if heights else 0.02
    blocks: list[Block] = []
    for index in order:
        line = lines[index]
        home = None
        for block in blocks:
            last = lines[block.lines[-1]]
            gap = line.box[1] - last.box[3]
            if -0.5 * typical <= gap <= 1.4 * typical and _overlap(block.box, line.box) >= 0.5:
                home = block
                break
        if home is None:
            blocks.append(Block([index], line.box))
        else:
            home.lines.append(index)
            home.box = _union(home.box, line.box)

    def column(block: Block) -> int:
        width = block.box[2] - block.box[0]
        centre = (block.box[0] + block.box[2]) / 2
        if width >= 0.5:
            return 0
        return 0 if centre < 0.5 else 1

    blocks.sort(key=lambda block: (column(block), round(block.box[1], 3), block.box[0]))
    return blocks


def join_lines(texts: Iterable[str]) -> tuple[str, list[int]]:
    """Join lines into running text, mending words hyphenated at a line break.

    Returns the text and, for each line, the offset at which it starts in that text,
    so a word found on a line can be located in the joined sentence.
    """

    joined = ""
    starts: list[int] = []
    for text in texts:
        piece = " ".join(str(text or "").split())
        if not joined:
            starts.append(0)
            joined = piece
            continue
        if _TRAILING_HYPHEN.search(joined):
            joined = joined[:-1]
            starts.append(len(joined))
            joined += piece
        else:
            starts.append(len(joined) + 1)
            joined = f"{joined} {piece}"
    return joined, starts


def sentences(text: str) -> list[tuple[int, int]]:
    """Sentence spans (start, end) in the text."""

    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END.finditer(text):
        before = text[:match.start()].rstrip(".!?…").split()
        last = before[-1].strip(_QUOTES).casefold() if before else ""
        if text[match.start() - 1] == "." and (len(last) <= 1 or last in ABBREVIATIONS):
            continue
        spans.append((start, match.start()))
        start = match.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


@dataclass(frozen=True)
class Located:
    word: str
    clean: str
    sentence: str
    line_index: int
    word_index: int
    block_lines: tuple[int, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"word": self.word, "clean": self.clean, "sentence": self.sentence,
                "line_index": self.line_index, "word_index": self.word_index}


def locate(lines: list[Line], line_index: int, word_index: int,
           blocks: list[Block] | None = None) -> Located | None:
    """The sentence around one tapped word.

    The word's block is joined into running text, split into sentences, and the one
    covering the word's position is returned. A block with no sentence punctuation (a
    heading, a table cell, a caption) yields the block itself, capped in length around
    the word so a tap never returns a wall of text.
    """

    if not 0 <= line_index < len(lines):
        return None
    line = lines[line_index]
    words = line.words or words_from_text(line.text, line.box, line.confidence)
    if not 0 <= word_index < len(words):
        return None
    word = words[word_index]
    blocks = blocks if blocks is not None else group_blocks(lines)
    home = next((block for block in blocks if line_index in block.lines), Block([line_index], line.box))
    texts = [" ".join(w.text for w in (lines[i].words or words_from_text(lines[i].text, lines[i].box)))
             or lines[i].text for i in home.lines]
    joined, starts = join_lines(texts)
    position_in_line = len(" ".join(w.text for w in words[:word_index])) + (1 if word_index else 0)
    offset = starts[home.lines.index(line_index)] + position_in_line
    offset = min(offset, max(0, len(joined) - 1))
    for start, end in sentences(joined):
        if start <= offset < end:
            sentence = joined[start:end].strip()
            break
    else:
        sentence = joined.strip()
    if len(sentence) > MAX_SENTENCE_CHARS:
        relative = max(0, offset - joined.find(sentence))
        left = max(0, relative - MAX_SENTENCE_CHARS // 2)
        sentence = sentence[left:left + MAX_SENTENCE_CHARS].strip()
    return Located(word.text, word.clean, sentence, line_index, word_index, tuple(home.lines))


def lines_from_payload(payload: Any) -> list[Line]:
    """Rebuild Line objects from the JSON a scan stored."""

    result: list[Line] = []
    for raw in payload if isinstance(payload, list) else []:
        if not isinstance(raw, dict):
            continue
        box = _box(raw.get("box"))
        words = tuple(
            Word(str(item.get("text") or ""), _box(item.get("box")), float(item.get("confidence") or 1.0))
            for item in (raw.get("words") or []) if isinstance(item, dict) and str(item.get("text") or "").strip())
        result.append(Line(str(raw.get("text") or ""), box, float(raw.get("confidence") or 1.0), words))
    return result


def lines_to_payload(lines: list[Line]) -> list[dict[str, Any]]:
    return [{
        "text": line.text, "box": list(line.box), "confidence": round(line.confidence, 3),
        "words": [{"text": word.text, "clean": word.clean, "box": list(word.box),
                   "confidence": round(word.confidence, 3)} for word in line.words],
    } for line in lines]


def _box(value: Any) -> Box:
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return (0.0, 0.0, 0.0, 0.0)
    return (x0, y0, x1, y1)

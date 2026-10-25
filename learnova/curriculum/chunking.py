"""Split headed sections into retrieval-sized chunks that keep their source reference.

A chunk never crosses a heading, so every chunk can be cited as "document, heading
path, page". Each chunk inherits the grade band of the nearest band heading above it
("Klassen 7/8"), falling back to the band of the whole document.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .catalog import band_from_text
from .extract import Section

TARGET_CHARS = 1100
MAX_CHARS = 1500
MIN_CHARS = 60


@dataclass
class Chunk:
    ordinal: int
    heading_path: str
    text: str
    page: int | None
    grade_min: int
    grade_max: int

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.heading_path}\n{self.text}".encode()).hexdigest()


def _reflow(text: str) -> str:
    """Join PDF line breaks inside a paragraph; keep list items and blank-line paragraphs."""

    paragraphs = []
    for block in re.split(r"\n{2,}", text):
        lines = [line.strip() for line in block.split("\n") if line.strip()]
        merged: list[str] = []
        for line in lines:
            if merged and not re.match(r"^([-•–▪]|\(?\d{1,2}[.)])\s", line):
                merged[-1] = f"{merged[-1]} {line}"
            else:
                merged.append(line)
        paragraphs.append("\n".join(merged))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _pieces(text: str) -> list[str]:
    """Paragraphs, with an overlong paragraph split at sentence ends."""

    pieces: list[str] = []
    for paragraph in re.split(r"\n{2,}|\n(?=[-•–▪] )", text):
        paragraph = paragraph.strip()
        if len(paragraph) <= MAX_CHARS:
            if paragraph:
                pieces.append(paragraph)
            continue
        sentence_buffer = ""
        for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
            if len(sentence_buffer) + len(sentence) > TARGET_CHARS and sentence_buffer:
                pieces.append(sentence_buffer.strip())
                sentence_buffer = ""
            sentence_buffer += sentence + " "
            while len(sentence_buffer) > MAX_CHARS:  # one sentence longer than a chunk
                pieces.append(sentence_buffer[:MAX_CHARS].strip())
                sentence_buffer = sentence_buffer[MAX_CHARS:]
        if sentence_buffer.strip():
            pieces.append(sentence_buffer.strip())
    return pieces


def _band(headings: tuple[str, ...], default: tuple[int, int]) -> tuple[int, int]:
    for heading in reversed(headings):
        band = band_from_text(heading)
        if band:
            return band
    return default


def chunk_sections(sections: list[Section], *, default_band: tuple[int, int] = (0, 0)) -> list[Chunk]:
    chunks: list[Chunk] = []
    for section in sections:
        heading_path = " › ".join(section.headings)[:500]
        low, high = _band(section.headings, default_band)
        buffer = ""
        for piece in _pieces(_reflow(section.text)):
            if buffer and len(buffer) + len(piece) + 2 > TARGET_CHARS:
                chunks.append(Chunk(len(chunks), heading_path, buffer, section.page, low, high))
                buffer = ""
            buffer = f"{buffer}\n\n{piece}" if buffer else piece
        if buffer:
            if len(buffer) < MIN_CHARS and chunks and chunks[-1].heading_path == heading_path:
                chunks[-1].text = f"{chunks[-1].text}\n\n{buffer}"
            elif len(buffer) >= MIN_CHARS or section.headings:
                chunks.append(Chunk(len(chunks), heading_path, buffer, section.page, low, high))
    return chunks

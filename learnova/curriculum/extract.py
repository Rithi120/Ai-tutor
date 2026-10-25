"""Turn a curriculum document (HTML, PDF, plain text/Markdown) into headed sections.

The structure of a Bildungsplan is the useful part: "3.2.2 Funktionaler Zusammenhang"
under "Klassen 7/8" tells the tutor both the topic and the grade, so headings are kept
as a path on every section and page numbers are kept for PDFs. Cleaning removes what
would only add noise to retrieval: running page headers and footers, hyphenation at
line breaks, navigation chrome and repeated whitespace.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser

from .catalog import band_from_text


@dataclass
class Section:
    headings: tuple[str, ...]
    text: str
    page: int | None = None


def detect_format(filename: str = "", content_type: str = "", content: bytes = b"") -> str:
    name = (filename or "").casefold()
    kind = (content_type or "").casefold()
    if content[:5] == b"%PDF-" or name.endswith(".pdf") or "application/pdf" in kind:
        return "pdf"
    if name.endswith((".html", ".htm")) or "html" in kind or content.lstrip()[:15].lower().startswith((b"<!doctype", b"<html")):
        return "html"
    return "text"


def extract(content: bytes, fmt: str) -> list[Section]:
    if fmt == "pdf":
        return extract_pdf(content)
    text = content.decode("utf-8", errors="replace")
    if fmt == "html":
        return extract_html(text)
    return extract_text(text)


def clean_text(value: str) -> str:
    value = value.replace("­", "").replace(" ", " ").replace("\r", "")
    # "Potenz-\ngesetze" -> "Potenzgesetze", but keep "Ein- und Ausgabe" (lowercase "und").
    value = re.sub(r"(\w)-\n(?!und\b|oder\b|bzw\b)([a-zäöüß])", r"\1\2", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


# --- HTML -------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "form", "button", "svg", "aside"}
_BLOCK_TAGS = {"p", "li", "div", "section", "article", "tr", "td", "th", "dd", "dt", "br", "table", "ul", "ol"}


class _HTMLSections(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[Section] = []
        self.path: list[tuple[int, str]] = []
        self.buffer: list[str] = []
        self.heading: list[str] | None = None
        self.heading_level = 0
        self.skip_depth = 0

    def _flush(self) -> None:
        text = clean_text("".join(self.buffer))
        if text:
            self.sections.append(Section(tuple(title for _, title in self.path), text))
        self.buffer = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if re.fullmatch(r"h[1-6]", tag):
            self._flush()
            self.heading, self.heading_level = [], int(tag[1])
        elif tag in _BLOCK_TAGS:
            self.buffer.append("\n")
        if tag == "li":
            self.buffer.append("- ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if self.heading is not None and re.fullmatch(r"h[1-6]", tag):
            title = " ".join("".join(self.heading).split())
            if title:
                self.path = [item for item in self.path if item[0] < self.heading_level]
                self.path.append((self.heading_level, title))
            self.heading = None
        elif tag in _BLOCK_TAGS:
            self.buffer.append("\n")

    def handle_data(self, data):
        if self.skip_depth:
            return
        (self.heading if self.heading is not None else self.buffer).append(data)

    def close(self):
        super().close()
        self._flush()


def extract_html(markup: str) -> list[Section]:
    parser = _HTMLSections()
    parser.feed(markup)
    parser.close()
    return parser.sections


# --- Plain text, Markdown and PDF text ----------------------------------------------

# A numbered heading ("3.2.2 Funktionaler Zusammenhang", "2 Prozessbezogene Kompetenzen")
# or a band heading ("Klassen 7/8"). Short, no sentence end: body lines rarely look so.
_NUMBERED = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+([A-ZÄÖÜ].{1,90})$")
_BAND_LINE = re.compile(r"^(Klassen?\s+\d{1,2}(?:\s*/\s*\d{1,2})*|Kursstufe|Klasse\s+\d{1,2})(\s.*)?$", re.IGNORECASE)
_MARKDOWN = re.compile(r"^(#{1,6})\s+(.+)$")


def _heading_of(line: str) -> tuple[int, str] | None:
    line = line.strip()
    if not line or len(line) > 100 or line.endswith((".", ",", ";", ":")):
        return None
    markdown = _MARKDOWN.match(line)
    if markdown:
        return len(markdown.group(1)), markdown.group(2).strip()
    numbered = _NUMBERED.match(line)
    # "3 + 4 = 7" is a body line; "3.2 Klassen 7/8" is a heading although it has a slash.
    formula = re.search(r"\d\s*[+*=×÷]\s*\d", line) or (
        re.search(r"\d\s*[/-]\s*\d", line) and not band_from_text(line))
    if numbered and not formula:
        return numbered.group(1).count(".") + 1, line
    if _BAND_LINE.match(line) and len(line) <= 40:
        # A band sits under the numbered heading it belongs to, so it is one level deeper
        # than anything numbered above it; level 9 keeps it the innermost element.
        return 9, line
    return None


def _sections_from_lines(lines, *, page: int | None = None,
                         path: list[tuple[int, str]] | None = None) -> tuple[list[Section], list[tuple[int, str]]]:
    current = list(path or [])
    sections: list[Section] = []
    buffer: list[str] = []

    def flush():
        text = clean_text("\n".join(buffer))
        if text:
            sections.append(Section(tuple(title for _, title in current), text, page))
        buffer.clear()

    for line in lines:
        heading = _heading_of(line)
        if heading:
            flush()
            level, title = heading
            current = [item for item in current if item[0] < level]
            current.append((level, title))
        else:
            buffer.append(line)
    flush()
    return sections, current


def extract_text(text: str) -> list[Section]:
    sections, _ = _sections_from_lines(clean_text(text).split("\n"))
    return sections


def _strip_running_lines(pages: list[list[str]]) -> list[list[str]]:
    """Drop header/footer lines that repeat on most pages (title, "Seite 12", the URL)."""

    if len(pages) < 3:
        return pages
    # Digits are masked so "Seite 3" and "Seite 4" count as one line. Heading-shaped lines
    # are never running lines: masked, every "3.x Klassen x/y" would look alike, and a band
    # heading repeated on each page of its band is worth keeping anyway.
    edges = Counter()
    for lines in pages:
        candidates = {
            re.sub(r"\d+", "#", line.strip()) for line in lines[:2] + lines[-2:]
            if line.strip() and _heading_of(line) is None
        }
        edges.update(candidates)
    threshold = max(2, int(len(pages) * 0.5))
    running = {line for line, count in edges.items() if count >= threshold}
    return [[line for line in lines if re.sub(r"\d+", "#", line.strip()) not in running] for lines in pages]


def extract_pdf(content: bytes) -> list[Section]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    pages = [(page.extract_text() or "").replace("\r", "").split("\n") for page in reader.pages]
    pages = _strip_running_lines(pages)
    sections: list[Section] = []
    path: list[tuple[int, str]] = []
    for number, lines in enumerate(pages, start=1):
        page_sections, path = _sections_from_lines(lines, page=number, path=path)
        sections.extend(page_sections)
    return sections

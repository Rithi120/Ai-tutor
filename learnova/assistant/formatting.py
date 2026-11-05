"""Formatting repairs for chat replies (assistant and lesson tutor), Flask-free.

Doubled backslashes. A model that has seen JSON-escaped LaTeX in its context (the tutor
chat used to receive the lesson as ``json.dumps`` text, where every ``\\frac`` is written
``\\\\frac``) sometimes answers in the same spelling. KaTeX reads ``\\\\`` as a line break,
so ``$\\\\frac{1}{2}$`` reached the student as a break followed by the italic letters
"frac12". `normalize_math_backslashes` collapses the doubling where it is unambiguous:

(a) exactly two backslashes (no third before them) followed by a known LaTeX command
    name - ``\\\\frac``, ``\\\\cdot``, ``\\\\left`` - become one backslash. In a chat reply
    a row break directly followed by the letters of a command name is never meant;
(b) once (a) has fired, or the text contains a paired doubled delimiter (``\\\\( … \\\\)``,
    ``\\\\[ … \\\\]``, ``\\\\{ … \\\\}``), the reply is read as doubled throughout: doubled
    delimiters and punctuation escapes become single and ``\\\\\\\\`` becomes the row break
    ``\\\\``;
(c) ``\\\\`` followed by a space, a newline, ``&``, a digit, a spacing such as ``[2pt]``
    or the end - a real row break - is never touched, and nothing inside fenced or inline
    code is either.

The function is idempotent (applying it twice changes nothing more), so it is safe on
history that was stored before the fix as well as on new replies.
"""

from __future__ import annotations

import re

KNOWN_COMMANDS: frozenset[str] = frozenset({
    # structure and text
    "frac", "dfrac", "tfrac", "sqrt", "binom", "left", "right", "big", "Big", "text", "textbf",
    "mathrm", "mathbf", "mathit", "mathbb", "mathcal", "operatorname", "displaystyle",
    "begin", "end", "quad", "qquad", "boxed", "overline", "underline", "overbrace", "underbrace",
    "vec", "hat", "bar", "tilde", "dot", "ddot", "widehat", "overrightarrow", "stackrel",
    "xrightarrow", "ce", "pu",
    # operators and relations
    "cdot", "times", "div", "pm", "mp", "le", "leq", "ge", "geq", "neq", "ne", "approx", "equiv",
    "cong", "simeq", "sim", "propto", "ll", "gg", "leqslant", "geqslant", "infty", "sum", "prod",
    "int", "iint", "oint", "lim", "log", "ln", "lg", "exp", "sin", "cos", "tan", "cot", "sec",
    "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "max", "min", "det", "gcd",
    "partial", "nabla", "circ", "degree", "prime", "in", "notin", "ni", "subset", "subseteq",
    "supset", "supseteq", "cup", "cap", "setminus", "emptyset", "varnothing", "forall", "exists",
    "neg", "land", "lor", "angle", "triangle", "perp", "parallel", "mid", "lvert", "rvert",
    "lVert", "rVert", "langle", "rangle", "lfloor", "rfloor", "lceil", "rceil", "pmod", "bmod",
    # arrows and dots
    "to", "rightarrow", "leftarrow", "Rightarrow", "Leftarrow", "Leftrightarrow",
    "leftrightarrow", "implies", "iff", "uparrow", "downarrow", "mapsto", "cdots", "ldots",
    "dots", "vdots", "ddots", "therefore", "because",
    # Greek
    "alpha", "beta", "gamma", "delta", "epsilon", "varepsilon", "zeta", "eta", "theta",
    "vartheta", "iota", "kappa", "lambda", "mu", "nu", "xi", "omicron", "pi", "varpi", "rho",
    "varrho", "sigma", "varsigma", "tau", "upsilon", "phi", "varphi", "chi", "psi", "omega",
    "Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega",
})

_FENCE = re.compile(r"^\s*```(\w*)\s*$")
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_BACKSLASHES = re.compile(r"\\+")
_LETTERS = re.compile(r"[A-Za-z]+")
_DOUBLED_COMMAND = re.compile(r"(?<!\\)\\\\([A-Za-z]+)")
# "\\[2pt]" is a row break with extra space inside an environment, never a doubled "\[".
_ROW_SPACING = r"\[\s*[-+]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|pc|in|bp|cm|mm|dd|cc|sp|ex|em|mu|px)\s*\]"
_ROW_SPACING_AT = re.compile(_ROW_SPACING)
_PAIRED_DELIMITERS = (
    re.compile(r"(?<!\\)\\\\\(.*?(?<!\\)\\\\\)", re.S),
    re.compile(r"(?<!\\)\\\\(?!" + _ROW_SPACING + r")\[.*?(?<!\\)\\\\\]", re.S),
    re.compile(r"(?<!\\)\\\\\{.*?(?<!\\)\\\\\}", re.S),
)
# What a doubled reply escapes besides commands: delimiters and spacing/punctuation.
_DOUBLED_PUNCTUATION = frozenset("()[]{},;!|%")


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """[start, end) of fenced code blocks (markdown.js's fence rule) and inline code."""

    ranges: list[tuple[int, int]] = []
    offset = 0
    fence_start = -1
    for line in text.split("\n"):
        line_end = offset + len(line)
        if _FENCE.match(line):
            if fence_start == -1:
                fence_start = offset
            else:
                ranges.append((fence_start, min(line_end + 1, len(text))))
                fence_start = -1
        elif fence_start == -1:
            ranges.extend((offset + match.start(), offset + match.end())
                          for match in _INLINE_CODE.finditer(line))
        offset = line_end + 1
    if fence_start != -1:
        ranges.append((fence_start, len(text)))
    return sorted(ranges)


def _pieces(text: str) -> list[tuple[str, bool]]:
    """The text as (piece, is_code) parts, in order."""

    pieces: list[tuple[str, bool]] = []
    cursor = 0
    for start, end in _code_ranges(text):
        if start < cursor:
            continue
        if start > cursor:
            pieces.append((text[cursor:start], False))
        pieces.append((text[start:end], True))
        cursor = end
    if cursor < len(text):
        pieces.append((text[cursor:], False))
    return pieces


def _is_doubled(prose: str) -> bool:
    if any(match.group(1) in KNOWN_COMMANDS for match in _DOUBLED_COMMAND.finditer(prose)):
        return True
    return any(pattern.search(prose) for pattern in _PAIRED_DELIMITERS)


def _collapse(piece: str) -> str:
    """One prose piece of a reply already known to be doubled."""

    def replace(match: re.Match[str]) -> str:
        run = match.group(0)
        following = piece[match.end():match.end() + 1]
        if len(run) == 2:
            word = _LETTERS.match(piece, match.end())
            if word and word.group(0) in KNOWN_COMMANDS:
                return "\\"                                  # (a) \\frac -> \frac
            if following == "[" and _ROW_SPACING_AT.match(piece, match.end()):
                return run                                   # (c) \\[2pt] is a spaced row break
            if following and following in _DOUBLED_PUNCTUATION:
                return "\\"                                  # (b) \\( -> \(
            return run                                       # (c) a row break stays
        # (b) a doubled row break. Four backslashes before a letter or a delimiter are
        # left alone: collapsing them would create a new "\\frac" or "\\(" and make a
        # second pass change the text again.
        if len(run) == 4 and not (following.isalpha() or (following and following in "()[]{}")):
            return "\\\\"
        return run

    return _BACKSLASHES.sub(replace, piece)


def normalize_math_backslashes(text: str) -> str:
    """Collapse doubled LaTeX backslashes in a chat reply (see the module docstring)."""

    if not isinstance(text, str) or "\\\\" not in text:
        return text
    pieces = _pieces(text)
    prose = "\x00".join(piece for piece, is_code in pieces if not is_code)
    if not _is_doubled(prose):
        return text
    return "".join(piece if is_code else _collapse(piece) for piece, is_code in pieces)


# ---- Decimal commas inside formulas (CHAT_FIX2 D5) ----------------------------------------
#
# KaTeX spaces a comma in maths like a list separator, so the German decimal 0,34 written
# inside \( … \) reached the student as "0, 34" - which reads as two numbers (live
# 2026-11-02: "0, 34", "62, 8", "2, 6"). The prompts now ask for 0{,}34; this repairs a reply
# that still writes 0,34. Meaning-preserving by construction: the only edit is "d,d" ->
# "d{,}d" (the same comma glyph, without the list space), and only
#   - inside \( … \), \[ … \] or $$ … $$ (a single $ can be a price and stays text),
#   - between two digits with no space, in a number with exactly one comma ("1,2,3" is a
#     list),
#   - outside code, \text{…}-like groups and sub- or superscripts (x_{1,2}),
#   - and never when the number is all there is between brackets: (1,2), [1,5], \{1,2\}
#     can be a point, an interval or a set.
# Applied only to replies in a language that writes decimals with a comma.

DECIMAL_COMMA_LANGUAGES = frozenset({"German", "French", "Spanish", "Italian", "Portuguese", "Dutch"})
_MATH_DELIMITERS = (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"))
_DECIMAL = re.compile(r"(?<![\d,])(\d+),(\d+)(?![\d,])")
_SKIPPED_GROUP = re.compile(r"\\(?:text|textbf|textit|textrm|textsf|texttt|mbox|hbox)\s*\{|[_^]\s*\{")
_GROUP_OPENERS = ("(", "[", "\\{", "\\lbrace")
_GROUP_CLOSERS = (")", "]", "\\}", "\\rbrace", "\\right)", "\\right]", "\\right\\}")


def _escaped(text: str, index: int) -> bool:
    count = 0
    while index - count - 1 >= 0 and text[index - count - 1] == "\\":
        count += 1
    return count % 2 == 1


def _math_spans(prose: str) -> list[tuple[int, int]]:
    """[start, end) of the content of each delimited formula, left to right."""

    spans: list[tuple[int, int]] = []
    index = 0
    while index < len(prose):
        for opener, closer in _MATH_DELIMITERS:
            if prose.startswith(opener, index) and not _escaped(prose, index):
                search = index + len(opener)
                while True:
                    close = prose.find(closer, search)
                    if close < 0 or not _escaped(prose, close):
                        break
                    search = close + 1
                if close < 0:
                    index += len(opener)             # never closed: plain text
                else:
                    spans.append((index + len(opener), close))
                    index = close + len(closer)
                break
        else:
            index += 1
    return spans


def _skipped_ranges(tex: str) -> list[tuple[int, int]]:
    ranges = []
    for match in _SKIPPED_GROUP.finditer(tex):
        depth, position = 1, match.end()
        while position < len(tex) and depth:
            char = tex[position]
            if char in "{}" and not _escaped(tex, position):
                depth += 1 if char == "{" else -1
            position += 1
        ranges.append((match.start(), position))
    return ranges


def _braced_decimals(tex: str) -> str:
    skipped = _skipped_ranges(tex)

    def replace(match: re.Match[str]) -> str:
        if any(start <= match.start() < end for start, end in skipped):
            return match.group(0)
        if tex[:match.start()].endswith(_GROUP_OPENERS) and tex[match.end():].startswith(_GROUP_CLOSERS):
            return match.group(0)
        return f"{match.group(1)}{{,}}{match.group(2)}"

    return _DECIMAL.sub(replace, tex)


def decimal_commas_in_math(text: str) -> str:
    """Write "d,d" inside formulas as "d{,}d" (see above). Idempotent; prose and code untouched."""

    if not isinstance(text, str) or "," not in text:
        return text
    out: list[str] = []
    for piece, is_code in _pieces(text):
        if is_code:
            out.append(piece)
            continue
        cursor = 0
        for start, end in _math_spans(piece):
            out.append(piece[cursor:start])
            out.append(_braced_decimals(piece[start:end]))
            cursor = end
        out.append(piece[cursor:])
    return "".join(out)


# ---- Hidden reply trailers (WP4) ---------------------------------------------------------
#
# The assistant ends a reply with up to two lines the student never sees (presets.TUTORING_LAYER):
# "CHIPS: a | b | c" (tap answers for a closing question) and "TOPIC: <subject> | <topic>"
# (or "TOPIC: none"). They are cut off on the server before the reply is stored, so neither the
# transcript nor the page ever shows them. Models sometimes wrap the keyword in markdown
# ("**TOPIC:** ...", "`CHIPS: ...`"); the keyword itself must be upper case, so an ordinary
# closing line such as "Topic: revision" in an essay outline is never taken for a trailer.

MAX_CHIPS = 3
MAX_CHIP_CHARACTERS = 40
MAX_TRAILER_SUBJECT = 80
MAX_TRAILER_TOPIC = 160
# Optional markdown around the keyword: a list marker or a heading ("- TOPIC:", "### TOPIC:"),
# an HTML comment, emphasis, code or a quote ("**TOPIC:**", "`CHIPS: ...`", "> TOPIC:").
_TRAILER_LINE = re.compile(
    r"^\s*(?:<!--\s*)?(?:(?:#{1,6}|[-*+]|\d{1,2}[.)])\s+)?(?:[*_`>]+\s*)?(CHIPS|TOPIC)(?:[*_`]+)?\s*:\s*"
    r"(?:[*_`]+\s*)?(.*?)\s*(?:[*_`]+)?\s*(?:-->)?\s*$")
# The most trailer lines taken off one reply: a model that repeats a line ("TOPIC: ..." twice)
# loses every copy, while a long tail of look-alike lines is never eaten.
_MAX_TRAILER_LINES = 6
_SEPARATOR_LINE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_NONE_WORDS = frozenset({"none", "keins", "kein", "keine", "aucun", "aucune", "ninguno", "n/a", "-"})
_CHIP_WRAPPERS = " \t\"'“”„«»[]()*_`"


def _split_chips(value: str) -> list[str]:
    r"""Split a CHIPS value on "|", but never inside a formula ($|x|$, \(|x|\), $$...$$)."""

    parts: list[str] = []
    current: list[str] = []
    closing = ""                      # the delimiter that ends the formula we are in, if any
    index = 0
    while index < len(value):
        char, pair = value[index], value[index:index + 2]
        escaped = index > 0 and value[index - 1] == "\\"
        if closing:
            if value.startswith(closing, index) and not (closing.startswith("$") and escaped):
                current.append(closing)
                index += len(closing)
                closing = ""
                continue
        elif pair in ("\\(", "\\["):
            closing = "\\)" if pair == "\\(" else "\\]"
            current.append(pair)
            index += 2
            continue
        elif char == "$" and not escaped:
            closing = "$$" if pair == "$$" else "$"
            current.append(closing)
            index += len(closing)
            continue
        elif char == "|":
            parts.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if closing:                       # an unclosed "$" (a price): no formula to protect
        return value.split("|")
    parts.append("".join(current))
    return parts


def _strip_chip(chip: str) -> str:
    """Trim quotes, brackets and markdown around a chip, never a formula's own \\) or \\]."""

    start, end = 0, len(chip)
    while start < end and chip[start] in _CHIP_WRAPPERS:
        start += 1
    while end > start and chip[end - 1] in _CHIP_WRAPPERS and not (
            chip[end - 1] in ")]" and end - 2 >= start and chip[end - 2] == "\\"):
        end -= 1
    return chip[start:end]


def _chips(value: str) -> list[str]:
    chips: list[str] = []
    for raw in _split_chips(str(value or "")):
        chip = _strip_chip(" ".join(raw.split()))
        if not chip.strip("$ "):
            continue
        chip = chip[:MAX_CHIP_CHARACTERS].rstrip()
        if chip.casefold() not in {item.casefold() for item in chips}:
            chips.append(chip)
        if len(chips) == MAX_CHIPS:
            break
    return chips


def _topic(value: str) -> tuple[str, str]:
    """(subject as written, topic) - ("", "none") when the reply said it is no school topic."""

    text = " ".join(str(value or "").split())
    subject, _bar, topic = text.partition("|")
    subject = subject.strip(" .\"'“”")
    # Further bars belong to the topic ("Maths | Algebra | linear" -> "Algebra, linear").
    topic = ", ".join(piece.strip(" .\"'“”") for piece in topic.split("|") if piece.strip(" .\"'“”"))
    if subject.casefold() in _NONE_WORDS:
        subject = ""
    if topic.casefold() in _NONE_WORDS:
        topic = ""
    if not subject and not topic:
        return "", "none"
    return subject[:MAX_TRAILER_SUBJECT].rstrip(), topic[:MAX_TRAILER_TOPIC].rstrip()


def _well_formed_trailer(line: str) -> bool:
    """A trailer line in the exact shape the prompt asks for ("TOPIC: a | b", "TOPIC: none",
    "CHIPS: a | b"): only such lines are taken out of a code fence."""

    match = _TRAILER_LINE.match(line)
    if match is None:
        return False
    value = match.group(2)
    return "|" in value or (match.group(1) == "TOPIC" and value.strip().casefold() in _NONE_WORDS)


def _unwrap_trailer_fence(lines: list[str]) -> list[str]:
    """Drop a code fence that wraps nothing but well-formed trailer lines at the end of a reply."""

    if not lines or not _FENCE.match(lines[-1]):
        return lines
    for index in range(len(lines) - 2, -1, -1):
        line = lines[index]
        if _FENCE.match(line):
            inner = lines[index + 1:-1]
            if any(item.strip() for item in inner) and all(
                    not item.strip() or _well_formed_trailer(item) for item in inner):
                return lines[:index] + inner
            return lines
        if line.strip() and not _well_formed_trailer(line):
            return lines
    return lines


def split_reply_trailers(text: str) -> tuple[str, list[str], tuple[str, str] | None]:
    """Cut the hidden CHIPS/TOPIC lines off a reply: (visible text, chips, (subject, topic) or None).

    The final lines, in any order; a repeated line goes too (the last one written counts).
    Chips: split on "|" outside formulas, trimmed, at most three of at most 40 characters.
    Topic: ("", "none") for "TOPIC: none", else (subject as written, at most 80 characters;
    topic, at most 160). A code fence wrapping only the trailers, and a horizontal rule
    left above them, go with them. Anything else - a trailer in the middle of the reply -
    is left as it is.
    """

    if not isinstance(text, str) or not text:
        return text if isinstance(text, str) else "", [], None
    lines = _unwrap_trailer_fence(text.rstrip().split("\n"))
    chips: list[str] = []
    topic: tuple[str, str] | None = None
    seen: set[str] = set()
    taken = 0
    while lines and taken < _MAX_TRAILER_LINES:
        while lines and not lines[-1].strip():
            lines.pop()
        match = _TRAILER_LINE.match(lines[-1]) if lines else None
        if match is None:
            break
        key, value = match.group(1), match.group(2)
        if key not in seen:          # read bottom-up: the last line written wins
            seen.add(key)
            if key == "CHIPS":
                chips = _chips(value)
            else:
                topic = _topic(value)
        lines.pop()
        taken += 1
    if seen:
        while lines and (not lines[-1].strip() or _SEPARATOR_LINE.match(lines[-1])):
            lines.pop()
    return "\n".join(lines).rstrip(), chips, topic

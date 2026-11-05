"""Conversation history list helpers: date groups, search snippets and list cursors.

Flask-free and deterministic. Groups are computed on the student's local calendar,
not on UTC: a message sent at 00:30 in Berlin belongs to "today" there even though it
is still yesterday in UTC. The zone comes from the browser (validated here) or the
account, and every moment handled here is a UTC instant.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

GROUPS = ("today", "yesterday", "previous_7_days", "older")
MAX_ZONE_LENGTH = 64
# IANA names: letters, digits and a few separators ("America/Argentina/Buenos_Aires",
# "Etc/GMT+1"). Anything else - a path, a URL - is refused before ZoneInfo sees it.
_ZONE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-/]*$")
MAX_CURSOR_LENGTH = 120
MAX_CURSOR_ID_LENGTH = 36
ELLIPSIS = "…"


def _utc(moment: datetime) -> datetime:
    """A naive value is a UTC instant read back from SQLite."""

    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def resolve_zone(name: str | None, fallback: tzinfo) -> tzinfo:
    """``ZoneInfo(name)`` for a valid IANA name of at most 64 characters, else `fallback`."""

    value = str(name or "").strip()
    if not value or len(value) > MAX_ZONE_LENGTH or not _ZONE_NAME.match(value) or ".." in value:
        return fallback
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return fallback


def date_group(moment_utc: datetime, now_utc: datetime, zone: tzinfo) -> str:
    """Which history group a conversation last active at `moment_utc` belongs to.

    Local calendar dates in `zone`: the same date is ``today``, the day before is
    ``yesterday``, two to seven days back is ``previous_7_days``, anything earlier is
    ``older``. A moment in the future (clock skew) counts as ``today``.
    """

    day = _utc(moment_utc).astimezone(zone).date()
    today = _utc(now_utc).astimezone(zone).date()
    days = (today - day).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days <= 7:
        return "previous_7_days"
    return "older"


# ---- Plain text for search snippets --------------------------------------------------------
#
# A snippet is shown as text (textContent), two lines high: markdown and LaTeX source
# ("**Gut!** Bei \(x^2 - 2x\) …") must never show as markup. `plain_text` turns a stored
# message into readable text: emphasis, headings, list and quote markers, table pipes, links
# and code fences go; formulas lose their delimiters and read as text (x², a/b, √x, α, ≤).

_SUPERSCRIPT = str.maketrans("0123456789+-=()n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")
_SUBSCRIPT = str.maketrans("0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
_SCRIPTABLE_UP = frozenset("0123456789+-=()n,")
_SCRIPTABLE_DOWN = frozenset("0123456789+-=(),")
_TEX_SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "leqslant": "≤",
    "ge": "≥", "geq": "≥", "geqslant": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡", "sim": "∼",
    "infty": "∞", "to": "→", "rightarrow": "→", "Rightarrow": "⇒", "implies": "⇒", "leftarrow": "←",
    "Leftarrow": "⇐", "Leftrightarrow": "⇔", "leftrightarrow": "↔", "iff": "⇔", "mapsto": "↦", "in": "∈",
    "notin": "∉", "subset": "⊂", "subseteq": "⊆", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅",
    "forall": "∀", "exists": "∃", "partial": "∂", "nabla": "∇", "sum": "∑", "prod": "∏", "int": "∫",
    "degree": "°", "circ": "°", "angle": "∠", "perp": "⊥", "parallel": "∥", "cdots": "⋯", "ldots": "…",
    "dots": "…", "therefore": "∴", "neg": "¬", "propto": "∝",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
_TEX_SPACES = frozenset({"quad", "qquad", ",", ";", ":", "!", " "})
_TEX_DROPPED = frozenset({"left", "right", "big", "Big", "bigg", "Bigg", "displaystyle", "limits", "nolimits"})
_MATH_SPAN = re.compile(
    r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)|(?<![\\$\w])\$(?=[^\s$])([^$\n]{1,300}?)(?<=[^\s\\])\$(?![\w$])",
    re.S)
_INLINE_CODE_SPAN = re.compile(r"`+([^`\n]+?)`+")
_FRACTION = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_ROOT = re.compile(r"\\sqrt\s*(?:\[([^\]]*)\])?\s*\{([^{}]*)\}")
_TEXT_COMMAND = re.compile(
    r"\\(?:text|textbf|textit|mathrm|mathbf|mathit|mathbb|mathcal|operatorname|ce|pu|boxed|overline|underline|"
    r"vec|hat|bar|tilde|mbox)\s*\{([^{}]*)\}")
_ENVIRONMENT = re.compile(r"\\(?:begin|end)\s*\{[^{}]*\}")
_SCRIPT = re.compile(r"([\^_])\s*(?:\{([^{}]*)\}|(\\[A-Za-z]+|[^\s{}\\]))")
_COMMAND = re.compile(r"\\([A-Za-z]+|.)")
_SIMPLE_TERM = re.compile(r"^[\w.²³⁰¹⁴-⁹]+$")


def _grouped(text: str) -> str:
    text = text.strip()
    return text if _SIMPLE_TERM.match(text) else f"({text})"


def _tex_text(tex: str) -> str:
    """A formula's source as readable text (best effort, never markup)."""

    text = tex.replace("\\{", "\x02").replace("\\}", "\x03").replace("\\\\", " ; ").replace("&", " ")
    text = _ENVIRONMENT.sub(" ", text)
    for _ in range(4):  # innermost first; nested fractions and roots unwind a level per pass
        text = _TEXT_COMMAND.sub(lambda m: m.group(1), text)
        text = _FRACTION.sub(lambda m: f"{_grouped(m.group(1))}/{_grouped(m.group(2))}", text)
        text = _ROOT.sub(lambda m: (m.group(1) or "").translate(_SUPERSCRIPT) + "√" + _grouped(m.group(2)), text)

    def script(match: re.Match[str]) -> str:
        body = match.group(2) if match.group(2) is not None else match.group(3)
        body = body.strip()
        allowed, table = (_SCRIPTABLE_UP, _SUPERSCRIPT) if match.group(1) == "^" else (_SCRIPTABLE_DOWN, _SUBSCRIPT)
        if body and all(char in allowed for char in body):
            return body.translate(table)
        return f"{match.group(1)}{_grouped(body) if len(body) > 1 else body}"

    text = _SCRIPT.sub(script, text)

    def command(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _TEX_SYMBOLS:
            return _TEX_SYMBOLS[name]
        if name in _TEX_SPACES:
            return " "
        if name in _TEX_DROPPED:
            return ""
        return name                                # \sin -> sin, \% -> %

    text = _COMMAND.sub(command, text)
    text = text.replace("{", "").replace("}", "").replace("\x02", "{").replace("\x03", "}")
    return " ".join(text.split())


_MARKDOWN_LINE_RULES = (
    (re.compile(r"^\s*(?:```|~~~).*$", re.M), ""),                            # code fences
    (re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$", re.M), ""),  # table rules
    (re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$", re.M), ""),               # horizontal rules
    (re.compile(r"^\s*#{1,6}\s+", re.M), ""),                                 # headings
    (re.compile(r"^\s*(?:>\s?)+", re.M), ""),                                 # quotes
    (re.compile(r"^\s*(?:[-*+]|\d{1,3}[.)])\s+", re.M), ""),                  # list markers
)
_MARKDOWN_INLINE_RULES = (
    (re.compile(r"!?\[([^\]\n]+)\]\([^)\s]+\)"), r"\1"),                      # links and images
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*"), r"\1"),
    (re.compile(r"__(?=\S)(.+?)(?<=\S)__"), r"\1"),
    (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), r"\1"),
    (re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])"), r"\1"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"\1"),
)


def plain_text(text: str) -> str:
    """A stored chat message as plain readable text: no markdown, no LaTeX source."""

    source = str(text or "")
    kept: list[str] = []

    def keep(value: str) -> str:
        kept.append(value)
        return f"\x00{len(kept) - 1}\x01"

    # Code and formulas first, so their characters (|, *, _) are never read as markdown.
    source = _INLINE_CODE_SPAN.sub(lambda m: keep(m.group(1)), source)
    source = _MATH_SPAN.sub(lambda m: keep(_tex_text(next(group for group in m.groups() if group is not None))),
                            source)
    for pattern, replacement in _MARKDOWN_LINE_RULES:
        source = pattern.sub(replacement, source)
    lines = []
    for line in source.split("\n"):
        stripped = line.strip()
        if stripped.startswith("|") or stripped.endswith("|"):
            # A table row: its cells, separated.
            line = " · ".join(cell.strip() for cell in stripped.strip("|").split("|") if cell.strip())
        lines.append(line)
    source = "\n".join(lines)
    for pattern, replacement in _MARKDOWN_INLINE_RULES:
        source = pattern.sub(replacement, source)
    source = re.sub(r"\x00(\d+)\x01", lambda m: kept[int(m.group(1))], source)
    return " ".join(source.split())


def snippet(text: str, query: str, *, radius: int = 60) -> str:
    """The part of `text` around the first match of `query`, for a search result.

    Case-insensitive (Unicode: "Ärger" finds "ärger"), whitespace collapsed, "…" on every
    side that was cut. At most ``2 * radius + len(query) + 2`` characters. Without a match
    (the database may fold case differently) the start of the text is shown. The text is
    plain (`plain_text`): no markdown or LaTeX source ever shows in a search result.
    """

    flat = plain_text(text)
    needle = " ".join(str(query or "").split())
    if not flat:
        return ""
    radius = max(0, int(radius))
    match = re.search(re.escape(needle), flat, re.IGNORECASE) if needle else None
    if match is None:
        end = min(len(flat), 2 * radius + len(needle))
        return flat[:end] + (ELLIPSIS if end < len(flat) else "")
    start = max(0, match.start() - radius)
    end = min(len(flat), match.end() + radius)
    return (ELLIPSIS if start > 0 else "") + flat[start:end] + (ELLIPSIS if end < len(flat) else "")


def encode_cursor(moment: datetime, row_id: str) -> str:
    """``"<iso instant>|<id>"``: where the next page of a list starts."""

    return f"{_utc(moment).isoformat()}|{row_id}"


def decode_cursor(value: str) -> tuple[datetime, str] | None:
    """The ``(instant, id)`` of a cursor from `encode_cursor`, or ``None`` if it is not one."""

    text = str(value or "").strip()
    if not text or len(text) > MAX_CURSOR_LENGTH or "|" not in text:
        return None
    stamp, _separator, row_id = text.partition("|")
    row_id = row_id.strip()
    if not row_id or len(row_id) > MAX_CURSOR_ID_LENGTH:
        return None
    try:
        moment = datetime.fromisoformat(stamp.strip())
    except ValueError:
        return None
    return _utc(moment), row_id


__all__ = ["GROUPS", "date_group", "decode_cursor", "encode_cursor", "plain_text", "resolve_zone", "snippet"]

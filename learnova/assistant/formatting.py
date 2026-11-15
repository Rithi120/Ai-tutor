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
from dataclasses import dataclass, field
from typing import Any

from .study_format import ExamTopic, parse_exam_topics

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


# ---- LaTeX validity: check, repair, never reject (spec Phase 2) ------------------------------
#
# KaTeX shows a formula it cannot typeset as its source code, so a model's broken LaTeX
# ("x = \frac{-b \pm \sqrt{b^2-4ac}{2a}": one brace short) used to reach the student as raw
# source, and a bare "\frac{1}{2}" outside any delimiter was never typeset at all. Nothing
# checked either on the server. Now:
#
# * latex_problems(tex) names what is structurally wrong with one formula body: unbalanced
#   braces, \left/\right counts, \begin/\end nesting, a \frac without two arguments, an
#   empty \sqrt, a doubled backslash before a command, or an empty formula. It is a
#   structural check, not a KaTeX port: an unknown command is not reported.
# * math_problems(text) walks the delimited formulas of a text exactly as the browser
#   splits them (static/js/math-rules.js: $$, \[ \], \( \), and a single $ only when it
#   looks like maths, so "between $5 and $10" is prose) and also reports a bare command
#   outside any formula.
# * repair_math(text) -> (text, repairs): doubled backslashes collapsed; a formula that only
#   misses closing braces gets them, unmatched \left/\right are dropped; any other broken
#   formula becomes its readable plain form ("(a)/(b)", "·", "√(x)") without delimiters; a
#   bare command run is wrapped in \( \). Valid text is returned unchanged, and the function
#   is idempotent. Applied to stored lesson parts, generated questions and exam questions
#   (app.py): repair, not reject.
# * display_tex(name, latex) is how a formula item is shown: its checked LaTeX in \( \),
#   else the name as written.

LATEX_PROBLEM_CODES = ("empty", "unbalanced_braces", "left_right", "environment", "frac_arity", "sqrt_empty",
                       "doubled_backslash", "bare_command")
MAX_INLINE_MATH = 200
# math-rules.js MATH_SIGNAL: what makes a single-$ span mathematics rather than a price.
_MATH_SIGNAL = re.compile(r"[a-zA-Z\\^_{}=+<>/|]|[±×÷⁄←-⇿∀-⋿Α-ω]")
_ALL_DELIMITERS = (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$"))
_COMMAND = re.compile(r"\\([A-Za-z]+)")
_LEFT = re.compile(r"\\left(?![A-Za-z])")
_RIGHT = re.compile(r"\\right(?![A-Za-z])")
_ENVIRONMENT = re.compile(r"\\(begin|end)\s*\{([^{}]*)\}")
_FRAC = re.compile(r"\\[dt]?frac(?![A-Za-z])")
_SQRT = re.compile(r"\\sqrt(?![A-Za-z])")
_PROSE_END = ".,;:!?"
_READABLE_SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
    "neq": "≠", "ne": "≠", "approx": "≈", "equiv": "≡", "sim": "∼", "propto": "∝", "infty": "∞",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐", "longrightarrow": "→",
    "Leftrightarrow": "⇔", "leftrightarrow": "↔", "rightleftharpoons": "⇌", "cdots": "⋯", "ldots": "…",
    "dots": "…", "circ": "°", "degree": "°", "in": "∈", "notin": "∉", "subset": "⊂", "subseteq": "⊆",
    "cup": "∪", "cap": "∩", "forall": "∀", "exists": "∃", "angle": "∠", "triangle": "△", "perp": "⊥",
    "parallel": "∥", "partial": "∂", "nabla": "∇", "sum": "Σ", "prod": "Π", "int": "∫", "lvert": "|",
    "rvert": "|", "mid": "|", "vert": "|", "percent": "%",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "rho": "ρ", "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ",
    "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω", "sqrt": "√",
}
_READABLE_DROP = frozenset({"left", "right", "displaystyle", "textstyle", "big", "Big", "bigg", "Bigg", "quad",
                            "qquad", "begin", "end", "ce", "pu", "frac", "dfrac", "tfrac", "text", "mathrm"})


def _looks_like_math(tex: str) -> bool:
    """math-rules.js looksLikeMath for an inline single-$ span."""

    if not tex.strip() or len(tex) > MAX_INLINE_MATH:
        return False
    if tex[0].isspace() or tex[-1].isspace() or "\n" in tex or "\r" in tex:
        return False
    return bool(_MATH_SIGNAL.search(tex))


def math_spans(prose: str) -> list[tuple[int, int, int, int]]:
    """(start, content start, content end, end) of every formula of `prose` that the browser
    typesets (math-rules.js splitMath): $$ … $$, \\[ … \\], \\( … \\) with any non-empty
    content, and $ … $ only when it looks like maths."""

    spans: list[tuple[int, int, int, int]] = []
    index = 0
    while index < len(prose):
        if prose[index] == "\\" and index + 1 < len(prose) and prose[index + 1] in "$\\":
            index += 2                       # \$ is a dollar sign, \\ a row break
            continue
        found = None
        for opener, closer in _ALL_DELIMITERS:
            if not prose.startswith(opener, index):
                continue
            search = index + len(opener)
            while search < len(prose):
                close = prose.find(closer, search)
                if close < 0:
                    break
                if _escaped(prose, close):
                    search = close + 1
                    continue
                found = (opener, close, closer)
                break
            if found:
                break
        if found:
            opener, close, closer = found
            tex = prose[index + len(opener):close]
            if _looks_like_math(tex) if opener == "$" else bool(tex.strip()):
                spans.append((index, index + len(opener), close, close + len(closer)))
                index = close + len(closer)
                continue
        index += 1
    return spans


def _group_end(tex: str, start: int) -> int | None:
    """End (exclusive) of the balanced {…} group opening at `start`, or None."""

    depth = 0
    position = start
    while position < len(tex):
        char = tex[position]
        if char == "\\":
            position += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def _argument_end(tex: str, start: int) -> int | None:
    """End of one command argument at `start` (a {group}, a \\command or one character),
    after optional spaces; None when there is none."""

    position = start
    while position < len(tex) and tex[position] == " ":
        position += 1
    if position >= len(tex) or tex[position] in "}&":
        return None
    if tex[position] == "{":
        return _group_end(tex, position)
    if tex[position] == "\\":
        word = _LETTERS.match(tex, position + 1)
        return word.end() if word else min(len(tex), position + 2)
    return position + 1


def _brace_depths(tex: str) -> tuple[int, bool]:
    """(depth left open at the end, whether a "}" ever closed nothing)."""

    depth, negative = 0, False
    position = 0
    while position < len(tex):
        char = tex[position]
        if char == "\\":
            position += 2                    # \{ and \} are literal braces
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                negative = True
                depth = 0
        position += 1
    return depth, negative


def latex_problems(tex: Any) -> list[str]:
    """What is structurally wrong with one formula body (no delimiters); [] when nothing is."""

    if not isinstance(tex, str) or not tex.strip():
        return ["empty"]
    problems: list[str] = []
    depth, negative = _brace_depths(tex)
    if depth or negative:
        problems.append("unbalanced_braces")
    if len(_LEFT.findall(tex)) != len(_RIGHT.findall(tex)):
        problems.append("left_right")
    stack: list[str] = []
    nested = True
    for match in _ENVIRONMENT.finditer(tex):
        name = match.group(2).strip()
        if match.group(1) == "begin":
            stack.append(name)
        elif not stack or stack.pop() != name:
            nested = False
    if stack or not nested:
        problems.append("environment")
    for match in _FRAC.finditer(tex):
        first = _argument_end(tex, match.end())
        if first is None or _argument_end(tex, first) is None:
            problems.append("frac_arity")
            break
    for match in _SQRT.finditer(tex):
        position = match.end()
        while position < len(tex) and tex[position] == " ":
            position += 1
        if position < len(tex) and tex[position] == "[":
            close = tex.find("]", position)
            position = close + 1 if close >= 0 else len(tex)
        end = _argument_end(tex, position)
        if end is None or not tex[position:end].strip("{} "):
            problems.append("sqrt_empty")
            break
    if any(match.group(1) in KNOWN_COMMANDS for match in _DOUBLED_COMMAND.finditer(tex)):
        problems.append("doubled_backslash")
    return problems


# A token that is plainly mathematics: numbers, operators, one letter (with an index or power).
_MATH_TOKEN = re.compile(r"^(?:[-+*/=<>()^_|.,\d]+|[A-Za-z]|[A-Za-z][_^]\{?\w{1,4}\}?|\d+(?:[.,]\d+)?[A-Za-z]?)$")


def _token_end(prose: str, position: int) -> int:
    """End of the non-space token at `position`; a {group} counts whole, even with spaces;
    a following delimiter ends it."""

    end = position
    while end < len(prose) and not prose[end].isspace():
        if prose[end] == "{":
            group = _group_end(prose, end)
            end = group if group is not None else len(prose)
        elif (prose[end] == "$" or prose.startswith("\\(", end) or prose.startswith("\\[", end)):
            break
        else:
            end += 1
    return end


def _bare_runs(prose: str) -> list[tuple[int, int]]:
    """[start, end) of each run of LaTeX outside any formula: a known command with its
    {groups} and the non-space characters around it ("x=\\frac{1}{2}")."""

    covered = [(start, end) for start, _inner, _inner_end, end in math_spans(prose)]
    runs: list[tuple[int, int]] = []
    for match in _COMMAND.finditer(prose):
        if match.group(1) not in KNOWN_COMMANDS or _escaped(prose, match.start()):
            continue
        if any(start <= match.start() < end for start, end in covered + runs):
            continue
        start = match.start()
        while start > 0 and not prose[start - 1].isspace() and prose[start - 1] != "$":
            start -= 1
        # "Merke: U = R \cdot I" is one formula from its first symbol: the run also takes the
        # maths tokens before the command on the same line (never a word, never a formula).
        while True:
            before = start
            while before > 0 and prose[before - 1] in " \t":
                before -= 1
            if before == start or before == 0:
                break
            token_start = before
            while token_start > 0 and not prose[token_start - 1].isspace() and prose[token_start - 1] != "$":
                token_start -= 1
            if not _MATH_TOKEN.match(prose[token_start:before]) or any(
                    token_start < other_end and other_start < before for other_start, other_end in covered + runs):
                break
            start = token_start
        end = _token_end(prose, match.end())
        # "\frac{1}{2} \cdot 4 + 1 = 3" is one formula: the run goes on over the maths tokens
        # that follow it on the same line (never over a word).
        while True:
            following = end
            while following < len(prose) and prose[following] in " \t":
                following += 1
            if following == end or following >= len(prose):
                break
            token_end = _token_end(prose, following)
            token = prose[following:token_end].rstrip(_PROSE_END)
            if not token or not (_MATH_TOKEN.match(token) or (
                    _COMMAND.search(token) and all(name in KNOWN_COMMANDS for name in _COMMAND.findall(token)))):
                break
            end = token_end
        while end > match.end() and prose[end - 1] in _PROSE_END:
            end -= 1
        if any(start < other_end and other_start < end for other_start, other_end in covered):
            continue
        runs.append((start, end))
    return sorted(runs)


def math_problems(text: Any) -> list[str]:
    """The LaTeX problems of a text: those of each typeset formula, and "bare_command" for a
    command outside any formula. Code is ignored."""

    if not isinstance(text, str) or ("\\" not in text and "$" not in text):
        return []
    problems: list[str] = []
    for piece, is_code in _pieces(text):
        if is_code:
            continue
        for _start, inner_start, inner_end, _end in math_spans(piece):
            problems.extend(latex_problems(piece[inner_start:inner_end]))
        if _bare_runs(piece):
            problems.append("bare_command")
    return list(dict.fromkeys(problems))


def plain_tex(tex: Any) -> str:
    """A LaTeX body as readable plain text (math-rules.js readableTex, lenient): \\frac{a}{b}
    -> (a)/(b), \\sqrt{x} -> √(x), \\cdot -> ·, \\text{…} -> …; never a backslash left over."""

    value = str(tex or "")
    for _pass in range(8):
        before = value
        value = re.sub(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}", r"(\1)/(\2)", value)
        value = re.sub(r"\\sqrt\s*(?:\[([^\]]*)\])?\s*\{([^{}]*)\}",
                       lambda match: f"{match.group(1) or ''}√({match.group(2)})", value)
        value = re.sub(r"\\(?:text|textbf|textit|mathrm|mathbf|mathit|operatorname|ce|pu)\s*\{([^{}]*)\}",
                       r"\1", value)
        if value == before:
            break
    value = re.sub(r"\\begin\s*\{[^{}]*\}|\\end\s*\{[^{}]*\}", " ", value)
    value = value.replace("\\\\", " ")
    value = re.sub(r"\\[,;:! ]", " ", value)
    value = re.sub(r"\\([{}%$&#_|])", r"\1", value)

    def command(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in _READABLE_SYMBOLS:
            return _READABLE_SYMBOLS[name]
        return " " if name in _READABLE_DROP else name

    value = re.sub(r"\\([A-Za-z]+)", command, value)
    value = value.replace("\\", "").replace("&", " ").replace("$", "")
    value = re.sub(r"_\{([^{}]*)\}", r"_\1", value)
    value = re.sub(r"\^\{([^{}]*)\}",
                   lambda match: "^" + (match.group(1) if len(match.group(1)) == 1 else f"({match.group(1)})"), value)
    value = value.replace("{", "").replace("}", "")
    return " ".join(value.split())


def _fixed_tex(tex: str) -> str | None:
    """A broken formula body repaired without guessing, or None: missing closing braces
    appended, unmatched \\left/\\right dropped (KaTeX needs neither)."""

    candidate = tex
    if len(_LEFT.findall(candidate)) != len(_RIGHT.findall(candidate)):
        candidate = _RIGHT.sub("", _LEFT.sub("", candidate))
    depth, negative = _brace_depths(candidate)
    if depth == 1 and not negative:
        # One "}" missing. At the end, or - the usual slip, "\frac{-b \pm \sqrt{b^2-4ac}{2a}" -
        # before a later group: only a single place that makes the formula valid counts.
        places = [len(candidate.rstrip())] + [index for index, char in enumerate(candidate)
                                              if char == "{" and index and not _escaped(candidate, index)]
        valid = {trial for trial in (candidate[:at] + "}" + candidate[at:] for at in places)
                 if not latex_problems(trial)}
        candidate = valid.pop() if len(valid) == 1 else candidate
    elif depth and not negative:
        candidate = candidate.rstrip() + "}" * depth
    return candidate if candidate != tex and not latex_problems(candidate) else None


def repair_math(text: Any) -> tuple[Any, int]:
    """(text with every formula typesettable or readable, number of repairs). See above."""

    if not isinstance(text, str) or ("\\" not in text and "$" not in text):
        return text, 0
    collapsed = normalize_math_backslashes(text)
    repairs = int(collapsed != text)
    out: list[str] = []
    for piece, is_code in _pieces(collapsed):
        if is_code:
            out.append(piece)
            continue
        cursor = 0
        rebuilt: list[str] = []
        for start, inner_start, inner_end, end in math_spans(piece):
            tex = piece[inner_start:inner_end]
            rebuilt.append(piece[cursor:start])
            if latex_problems(tex):
                repairs += 1
                fixed = _fixed_tex(tex)
                rebuilt.append(piece[start:inner_start] + fixed + piece[inner_end:end] if fixed is not None
                               else plain_tex(tex))
            else:
                rebuilt.append(piece[start:end])
            cursor = end
        rebuilt.append(piece[cursor:])
        piece = "".join(rebuilt)
        runs = _bare_runs(piece)
        if runs:
            wrapped: list[str] = []
            cursor = 0
            for start, end in runs:
                wrapped.append(piece[cursor:start])
                run = piece[start:end]
                repairs += 1
                wrapped.append(f"\\({run}\\)" if not latex_problems(run) else plain_tex(run))
                cursor = end
            wrapped.append(piece[cursor:])
            piece = "".join(wrapped)
        out.append(piece)
    return "".join(out), repairs


def display_tex(name: Any, latex: Any = "") -> str:
    """How a formula item is shown: its checked LaTeX in \\( \\); a name that already has
    delimiters as it is; a name with a LaTeX command, when it checks, in \\( \\) (else its
    readable plain form); otherwise the name as written ("U = R · I" needs no typesetting)."""

    name = " ".join(str(name or "").split())
    latex = " ".join(str(latex or "").split())
    if latex and "$" not in latex and "\\(" not in latex and not latex_problems(latex):
        return f"\\({latex}\\)"
    if math_spans(name):
        return name
    if _COMMAND.search(name):
        return f"\\({name}\\)" if not latex_problems(name) else plain_tex(name)
    return name


# ---- Hidden reply trailers (WP4) ---------------------------------------------------------
#
# The assistant ends a reply with up to four lines the student never sees (presets.TUTORING_LAYER):
# "CHIPS: a | b | c" (tap answers for a closing question), "TOPIC: <subject> | <topic>"
# (or "TOPIC: none"), rarely "MEDIA: diagram|map|video | <Wikipedia title>" (a picture or
# video would teach more than words; learnova/assistant/resources.py decides whether to offer
# one) and "EXAM: a | b | c" for exam material (spec 1.2: the separate topics that decide
# between Short Lessons and Project Mode, study_format.py). They are cut off on the server before the reply is stored, so neither the
# transcript nor the page ever shows them. Models sometimes wrap the keyword in markdown
# ("**TOPIC:** ...", "`CHIPS: ...`"); the keyword itself must be upper case, so an ordinary
# closing line such as "Topic: revision" in an essay outline is never taken for a trailer.

MAX_CHIPS = 3
MAX_CHIP_CHARACTERS = 40
MAX_TRAILER_SUBJECT = 80
MAX_TRAILER_TOPIC = 160
MAX_TRAILER_MEDIA = 120
MEDIA_KINDS = frozenset({"diagram", "map", "video"})
# Optional markdown around the keyword: a list marker or a heading ("- TOPIC:", "### TOPIC:"),
# an HTML comment, emphasis, code or a quote ("**TOPIC:**", "`CHIPS: ...`", "> TOPIC:").
_TRAILER_LINE = re.compile(
    r"^\s*(?:<!--\s*)?(?:(?:#{1,6}|[-*+]|\d{1,2}[.)])\s+)?(?:[*_`>]+\s*)?(CHIPS|TOPIC|MEDIA|EXAM)(?:[*_`]+)?\s*:\s*"
    r"(?:[*_`]+\s*)?(.*?)\s*(?:[*_`]+)?\s*(?:-->)?\s*$")
# The keyword alone as a heading, its value on the next line ("**MEDIA**" / "Diagramm: ...",
# live 2026-11-14): the pair is one hidden line, never shown to the student.
_BARE_TRAILER_KEY = re.compile(
    r"^\s*(?:(?:#{1,6}|[-*+])\s+)?(?:[*_`>]+\s*)?(CHIPS|TOPIC|MEDIA|EXAM)(?:[*_`]+)?\s*:?\s*(?:[*_`]+)?\s*$")
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
    "CHIPS: a | b", "EXAM: a | b"): only such lines are taken out of a code fence."""

    match = _TRAILER_LINE.match(line)
    if match is None:
        return False
    value = match.group(2)
    return "|" in value or (match.group(1) in {"TOPIC", "EXAM"} and value.strip().casefold() in _NONE_WORDS)


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


def _media(value: str) -> tuple[str, str] | None:
    """(kind, term) from "diagram | Ohmsches Gesetz"; None for anything not in that shape."""

    kind, bar, term = " ".join(str(value or "").split()).partition("|")
    kind = kind.strip(" .\"'“”*_`").casefold()
    # A model that copies the template ("diagram|map|video | Ohmsches Gesetz") picked the
    # first kind; the other kinds are not part of the title, and a "|" never is (it would
    # be several titles in one Wikipedia lookup and an OR in a YouTube search).
    segments = [part.strip(" .\"'“”*_`<>") for part in term.split("|")]
    term = next((part for part in segments if part and part.casefold() not in MEDIA_KINDS), "")
    if not bar or kind not in MEDIA_KINDS or not term or term.casefold() in _NONE_WORDS:
        return None
    return kind, term[:MAX_TRAILER_MEDIA].rstrip()


@dataclass(frozen=True)
class ReplyTrailers:
    """A reply with its hidden lines taken off: what is shown, and what they said."""

    text: str
    chips: list[str] = field(default_factory=list)
    topic: tuple[str, str] | None = None
    media: tuple[str, str] | None = None
    # The separate topics of exam material ("EXAM: a | b (x, y) | c"): titles of at most 120
    # characters, deduplicated by topic key, at most 20, with any bracketed subtopics
    # (learnova/assistant/study_format.py); [] without.
    exam_topics: list[ExamTopic] = field(default_factory=list)


# The study-format lane's name for the same result.
Trailers = ReplyTrailers


def split_reply_trailers(text: str) -> tuple[str, list[str], tuple[str, str] | None]:
    """(visible text, chips, (subject, topic) or None) - reply_trailers without the media and exam lines."""

    parts = reply_trailers(text)
    return parts.text, parts.chips, parts.topic


def reply_trailers(text: str) -> ReplyTrailers:
    """Cut the hidden CHIPS/TOPIC/MEDIA/EXAM lines off a reply.

    The final lines, in any order; a repeated line goes too (the last one written counts).
    Chips: split on "|" outside formulas, trimmed, at most three of at most 40 characters.
    Topic: ("", "none") for "TOPIC: none", else (subject as written, at most 80 characters;
    topic, at most 160). Media: (kind, term) for "MEDIA: diagram|map|video | <title>", else None.
    Exam topics: split on "|", see study_format.parse_exam_topics. A code fence wrapping only
    the trailers, and a horizontal rule left above them, go with them. Anything else - a
    trailer in the middle of the reply - is left as it is.
    """

    if not isinstance(text, str) or not text:
        return ReplyTrailers(text if isinstance(text, str) else "")
    lines = _unwrap_trailer_fence(text.rstrip().split("\n"))
    chips: list[str] = []
    topic: tuple[str, str] | None = None
    media: tuple[str, str] | None = None
    exam: list[ExamTopic] = []
    seen: set[str] = set()
    taken = 0
    while lines and taken < _MAX_TRAILER_LINES:
        while lines and not lines[-1].strip():
            lines.pop()
        match = _TRAILER_LINE.match(lines[-1]) if lines else None
        if match is not None:
            key, value = match.group(1), match.group(2)
        else:
            bare = _BARE_TRAILER_KEY.match(lines[-2]) if len(lines) >= 2 and lines[-1].strip() else None
            if bare is None:
                break
            key, value = bare.group(1), lines[-1].strip().strip("*_`").strip()
            lines.pop()          # the value line; the keyword line goes below
        if key not in seen:          # read bottom-up: the last line written wins
            seen.add(key)
            if key == "CHIPS":
                chips = _chips(value)
            elif key == "MEDIA":
                media = _media(value)
            elif key == "EXAM":
                exam = parse_exam_topics(value)
            else:
                topic = _topic(value)
        lines.pop()
        taken += 1
    if seen:
        while lines and (not lines[-1].strip() or _SEPARATOR_LINE.match(lines[-1])):
            lines.pop()
    return ReplyTrailers("\n".join(lines).rstrip(), chips, topic, media, exam)

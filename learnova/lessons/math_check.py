"""Deterministic checking of the calculations in an AI-written lesson (Reliability V1).

Flask-free and pure. The lesson's text is read for calculation chains ("1200 € · 3 · 90 :
36000 = 9 €", "100·4·10 ÷ 36000 = 0,0111 €", "2,5 km = 2500 m"); every side that is a
complete arithmetic expression is evaluated by this module's own tokenizer and parser -
never eval(), exec() or ast - over exact fractions, and compared with what the lesson
claims: value, rounding, decimal places, sign, unit and scientific notation.

What cannot be read safely is never called verified: a side with an unknown word, a
variable without a value, a number whose reading is ambiguous in a way that changes the
result. Such a chain is "unverifiable", and a lesson made only of those is UNVERIFIED.

The states (spec Reliability V1, section 7):

    VERIFIED            at least one calculation checked, all correct, nothing unreadable
    PARTIALLY_VERIFIED  some checked and correct, some could not be checked
    UNVERIFIED          no calculation could be checked (or the lesson has none)
    NEEDS_REGENERATION  at least one calculation is wrong
"""

from __future__ import annotations

import itertools
import math
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from fractions import Fraction
from typing import Any, Iterable, Mapping, Sequence

VERIFIED = "VERIFIED"
PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
UNVERIFIED = "UNVERIFIED"
NEEDS_REGENERATION = "NEEDS_REGENERATION"
STATES = (VERIFIED, PARTIALLY_VERIFIED, UNVERIFIED, NEEDS_REGENERATION)

MAX_AMBIGUOUS = 4            # numbers with two readings in one chain (2**4 combinations)
MAX_EXPONENT = 64
MAX_TOKENS = 160             # a side longer than this is prose, not a calculation
SAMPLE_POINTS = (Fraction(37, 10), Fraction(59, 10), Fraction(113, 10), Fraction(7, 4))

# ----------------------------------------------------------------------------- units
# (factor to the base unit, dimension exponents). Base units: m, kg, s, A, K, EUR.
_D = dict[str, int]


def _dims(**exponents: int) -> _D:
    return {key: value for key, value in exponents.items() if value}


_UNITS: dict[str, tuple[Fraction, _D]] = {
    "mm": (Fraction(1, 1000), _dims(m=1)), "cm": (Fraction(1, 100), _dims(m=1)),
    "dm": (Fraction(1, 10), _dims(m=1)), "m": (Fraction(1), _dims(m=1)), "km": (Fraction(1000), _dims(m=1)),
    "mg": (Fraction(1, 1000000), _dims(kg=1)), "g": (Fraction(1, 1000), _dims(kg=1)),
    "kg": (Fraction(1), _dims(kg=1)), "t": (Fraction(1000), _dims(kg=1)),
    "ms": (Fraction(1, 1000), _dims(s=1)), "s": (Fraction(1), _dims(s=1)), "min": (Fraction(60), _dims(s=1)),
    "h": (Fraction(3600), _dims(s=1)),
    "ml": (Fraction(1, 1000000), _dims(m=3)), "l": (Fraction(1, 1000), _dims(m=3)),
    "L": (Fraction(1, 1000), _dims(m=3)), "cl": (Fraction(1, 100000), _dims(m=3)),
    "N": (Fraction(1), _dims(kg=1, m=1, s=-2)), "kN": (Fraction(1000), _dims(kg=1, m=1, s=-2)),
    "J": (Fraction(1), _dims(kg=1, m=2, s=-2)), "kJ": (Fraction(1000), _dims(kg=1, m=2, s=-2)),
    "W": (Fraction(1), _dims(kg=1, m=2, s=-3)), "kW": (Fraction(1000), _dims(kg=1, m=2, s=-3)),
    "Wh": (Fraction(3600), _dims(kg=1, m=2, s=-2)), "kWh": (Fraction(3600000), _dims(kg=1, m=2, s=-2)),
    "Pa": (Fraction(1), _dims(kg=1, m=-1, s=-2)), "hPa": (Fraction(100), _dims(kg=1, m=-1, s=-2)),
    "A": (Fraction(1), _dims(A=1)), "mA": (Fraction(1, 1000), _dims(A=1)),
    "V": (Fraction(1), _dims(kg=1, m=2, s=-3, A=-1)),
    "Ω": (Fraction(1), _dims(kg=1, m=2, s=-3, A=-2)), "Ohm": (Fraction(1), _dims(kg=1, m=2, s=-3, A=-2)),
    "kΩ": (Fraction(1000), _dims(kg=1, m=2, s=-3, A=-2)),
    "Hz": (Fraction(1), _dims(s=-1)),
    "€": (Fraction(1), _dims(EUR=1)), "EUR": (Fraction(1), _dims(EUR=1)), "Euro": (Fraction(1), _dims(EUR=1)),
    "ct": (Fraction(1, 100), _dims(EUR=1)), "Cent": (Fraction(1, 100), _dims(EUR=1)),
    "Sekunde": (Fraction(1), _dims(s=1)), "Sekunden": (Fraction(1), _dims(s=1)),
    "Minute": (Fraction(60), _dims(s=1)), "Minuten": (Fraction(60), _dims(s=1)),
    "Stunde": (Fraction(3600), _dims(s=1)), "Stunden": (Fraction(3600), _dims(s=1)),
    "Tag": (Fraction(86400), _dims(s=1)), "Tage": (Fraction(86400), _dims(s=1)),
    "Tagen": (Fraction(86400), _dims(s=1)), "Woche": (Fraction(604800), _dims(s=1)),
    "Wochen": (Fraction(604800), _dims(s=1)),
}
# Units with no fixed size here: a year has 360 days in a bank's interest and 365 in a
# calendar, a month 30 or 28-31. They are kept as their own dimension, never converted.
_OPAQUE_UNITS = {"Jahr": "year", "Jahre": "year", "Jahren": "year", "Monat": "month", "Monate": "month",
                 "Monaten": "month", "°C": "degC", "°": "deg", "K": "kelvin"}
_UNIT_WORDS = sorted(list(_UNITS) + list(_OPAQUE_UNITS), key=len, reverse=True)


@dataclass(frozen=True)
class Quantity:
    """A value with its dimension; `exact` False once a root or a fractional power is taken."""

    value: Fraction | float
    dims: tuple[tuple[str, int], ...] = ()
    exact: bool = True

    @staticmethod
    def of(value: Fraction | float, dims: Mapping[str, int] | None = None, exact: bool = True) -> "Quantity":
        return Quantity(value, tuple(sorted((key, power) for key, power in (dims or {}).items() if power)), exact)

    def dim_map(self) -> dict[str, int]:
        return dict(self.dims)


class Unverifiable(Exception):
    """This text is not a calculation the module can read safely."""


class MathFault(Exception):
    """A calculation that is wrong whatever its reading (division by zero, adding metres to euros)."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def _combine(left: Mapping[str, int], right: Mapping[str, int], sign: int) -> dict[str, int]:
    out = dict(left)
    for key, power in right.items():
        out[key] = out.get(key, 0) + sign * power
    return {key: power for key, power in out.items() if power}


# ----------------------------------------------------------------------------- text

_SUPERSCRIPT = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ⁿˣ", "0123456789-+nx")
_SUPERSCRIPT_RUN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺ⁿˣ]+")
_SUBSCRIPT = str.maketrans("₀₁₂₃₄₅₆₇₈₉ₙₓᵢ", "0123456789nxi")
_SUBSCRIPT_RUN = re.compile(r"[₀₁₂₃₄₅₆₇₈₉ₙₓᵢ]+")
_FRACTION_CHARS = {"½": "(1/2)", "¼": "(1/4)", "¾": "(3/4)", "⅓": "(1/3)", "⅔": "(2/3)", "⅕": "(1/5)",
                   "⅛": "(1/8)"}
_LATEX_SIMPLE = (
    (r"\\left\s*", ""), (r"\\right\s*", ""), (r"\\(?:cdot|times|ast)\b", " · "), (r"\\div\b", " ÷ "),
    (r"\\(?:approx|simeq)\b", " ≈ "), (r"\\(?:Rightarrow|rightarrow|to|implies|Longrightarrow|longrightarrow)\b", " ⇒ "),
    (r"\\(?:,|;|!|:|quad|qquad| )", " "), (r"\\%", "%"), (r"\\pi\b", "π"), (r"\\Omega\b", "Ω"),
    (r"\\(?:euro|EUR)\b", "€"), (r"\\(?:neq|ne)\b", " ≠ "), (r"\\(?:leq|le)\b", " ≤ "), (r"\\(?:geq|ge)\b", " ≥ "),
    (r"\{,\}", ","), (r"\\(?:displaystyle|textstyle)\b", ""),
)


def _strip_braced(text: str, start: int) -> tuple[str, int]:
    """The content of the {...} group that opens at text[start] and the index after it."""

    if start >= len(text) or text[start] != "{":
        raise Unverifiable("brace")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index], index + 1
    raise Unverifiable("unbalanced")


def _latex_groups(text: str) -> str:
    """\\frac{a}{b} → ((a)/(b)), \\sqrt[n]{x} → root, \\text{x} → x, ^{..} → ^(..), recursively."""

    out: list[str] = []
    index = 0
    while index < len(text):
        match = re.compile(r"\\(d?t?frac|sqrt|text|mathrm|mathit|operatorname|mbox|textrm)").match(text, index)
        if match:
            name = match.group(1)
            cursor = match.end()
            try:
                if name.endswith("frac"):
                    top, cursor = _strip_braced(text, cursor)
                    bottom, cursor = _strip_braced(text, cursor)
                    out.append(f"(({_latex_groups(top)})/({_latex_groups(bottom)}))")
                elif name == "sqrt":
                    degree = "2"
                    if cursor < len(text) and text[cursor] == "[":
                        close = text.find("]", cursor)
                        if close < 0:
                            raise Unverifiable("sqrt")
                        degree, cursor = text[cursor + 1:close], close + 1
                    body, cursor = _strip_braced(text, cursor)
                    out.append(f"root({_latex_groups(degree)};{_latex_groups(body)})")
                else:
                    body, cursor = _strip_braced(text, cursor)
                    out.append(f" {_latex_groups(body)} ")
            except Unverifiable:
                out.append(text[index:cursor])
            index = cursor
            continue
        if text[index] == "^" and index + 1 < len(text) and text[index + 1] == "{":
            try:
                body, cursor = _strip_braced(text, index + 1)
                out.append(f"^({_latex_groups(body)})")
                index = cursor
                continue
            except Unverifiable:
                pass
        if text[index] == "_" and index + 1 < len(text) and text[index + 1] == "{":
            try:
                body, cursor = _strip_braced(text, index + 1)
                out.append("_" + re.sub(r"\W", "", body))
                index = cursor
                continue
            except Unverifiable:
                pass
        out.append(text[index])
        index += 1
    return "".join(out)


def flatten(text: Any) -> str:
    """Lesson text (Markdown, LaTeX, Unicode maths) as plain calculation text, one formula
    per line: $$..$$ blocks and every line break stay line breaks."""

    value = str(text or "")
    value = value.replace("$$", "\n").replace("\\[", "\n").replace("\\]", "\n")
    value = value.replace("\\(", " ").replace("\\)", " ").replace("$", " ")
    for pattern, replacement in _LATEX_SIMPLE:
        value = re.sub(pattern, replacement, value)
    value = _latex_groups(value)
    value = value.replace("{", "(").replace("}", ")").replace("\\", " ")
    value = _SUPERSCRIPT_RUN.sub(lambda match: "^(" + match.group(0).translate(_SUPERSCRIPT) + ")", value)
    value = _SUBSCRIPT_RUN.sub(lambda match: "_" + match.group(0).translate(_SUBSCRIPT), value)
    for char, replacement in _FRACTION_CHARS.items():
        value = value.replace(char, replacement)
    value = value.replace("\u202f", " ").replace("\u00a0", " ").replace("\u2009", " ").replace("**", "^")
    value = value.replace("*", "·")
    return value


# ----------------------------------------------------------------------------- tokens

@dataclass(frozen=True)
class Tok:
    kind: str           # num, op, lp, rp, var, unit, pct, sep, rel, word, func
    text: str
    # num: the readings of the literal (more than one when "1.200" may be 1200 or 1.2), and
    # the decimal places it was written with (for rounding).
    readings: tuple[Fraction, ...] = ()
    places: int = 0


_NUMBER = re.compile(
    r"(?P<int>\d{1,3}(?:[ .]\d{3})+|\d+)(?:(?P<sep>[.,])(?P<frac>\d+))?(?:[eE](?P<exp>[-+]?\d+))?")
# A variable is one letter, with a subscript only after "_" or as digits ("K_0", "x1"):
# "also", "am", "zu" are words, not "a" with a subscript.
_VAR = re.compile(r"[A-Za-zα-ωΑ-Ω](?:_[0-9a-z]{1,3}|[0-9]{1,2})?")
_WORD = re.compile(r"[A-Za-zÄÖÜäöüßα-ωΑ-Ω][A-Za-zÄÖÜäöüßα-ωΑ-Ω_0-9]*")
_OPS = {"+": "+", "-": "-", "−": "-", "·": "*", "⋅": "*", "∙": "*", "×": "*", "÷": "/", "/": "/", ":": "/",
        "^": "^", "√": "√"}
_RELS = {"=": "=", "≈": "≈", "≠": "≠", "≤": "≤", "≥": "≥", "<": "<", ">": ">", "⇒": "⇒", "→": "⇒", "⟹": "⇒"}
_DASHES = {"–": "dash", "—": "sep"}


def _number_token(match: re.Match[str], language: str) -> Tok:
    whole, sep, frac, exp = match.group("int"), match.group("sep"), match.group("frac"), match.group("exp")
    readings: list[tuple[Fraction, int]] = []
    grouped = bool(re.search(r"[ .]", whole))
    integer_digits = re.sub(r"[ .]", "", whole)
    if grouped and "." in whole and sep is None:
        # "1.200": thousands in German, a decimal in English. Both readings are kept.
        readings.append((Fraction(int(integer_digits)), 0))
        first, rest = whole.split(".", 1)
        if "." not in rest and " " not in whole:
            readings.append((Fraction(int(first)) + Fraction(int(rest), 10 ** len(rest)), len(rest)))
    elif grouped and sep == "." and "." in whole:
        raise Unverifiable("number")
    else:
        base = Fraction(int(integer_digits))
        places = 0
        if sep is not None and frac is not None:
            if len(frac) == 3 and not grouped and ((sep == "." and language != "en") or
                                                   (sep == "," and language == "en")):
                # "1.200" in German text, "1,500" in English text: thousands, or the other
                # language's decimal separator. Both readings are kept.
                readings.append((Fraction(int(integer_digits + frac)), 0))
            base += Fraction(int(frac), 10 ** len(frac))
            places = len(frac)
        readings.append((base, places))
    scale = Fraction(10) ** int(exp) if exp else Fraction(1)
    values = tuple(dict.fromkeys(value * scale for value, _places in readings))
    places = readings[-1][1] if not exp else 0
    return Tok("num", match.group(0), values, places)


def tokenize(text: str, language: str = "de") -> list[Tok]:
    """Tokens of one flattened line. Words that are no unit and no single-letter variable
    stay "word" tokens: they end a calculation."""

    tokens: list[Tok] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        number = _NUMBER.match(text, index)
        if number and (index == 0 or not (text[index - 1].isalpha() or text[index - 1] == "_")):
            token = _number_token(number, language)
            # "1 200" is grouped only when the group is exactly three digits and the number
            # does not run on ("1 2000" is two numbers).
            tokens.append(token)
            index = number.end()
            # A unit right after the number: "5 m", "5m", "36 km/h", "12 €", "4 %".
            rest = text[index:]
            spaced = rest[:1] == " "
            stripped = rest.lstrip(" ")
            unit = _unit_at(stripped)
            if unit is not None and (not spaced or len(rest) - len(stripped) <= 1):
                tokens.append(Tok("unit", unit))
                index += (len(rest) - len(stripped)) + len(unit)
            elif stripped.startswith("%"):
                tokens.append(Tok("pct", "%"))
                index += (len(rest) - len(stripped)) + 1
            continue
        if char == ":":
            before = text[index - 1] if index else " "
            after = text[index + 1] if index + 1 < len(text) else " "
            if not before.isspace() and after.isspace():
                # "Teile durch 36000: 324 000 ÷ 36000": a sentence colon, not "÷".
                tokens.append(Tok("sep", ":"))
                index += 1
                continue
        if char in _OPS:
            tokens.append(Tok("op", _OPS[char]))
            index += 1
            continue
        if char in "([":
            tokens.append(Tok("lp", "("))
            index += 1
            continue
        if char in ")]":
            tokens.append(Tok("rp", ")"))
            index += 1
            continue
        if char in _RELS:
            tokens.append(Tok("rel", _RELS[char]))
            index += 1
            continue
        if char in _DASHES:
            tokens.append(Tok(_DASHES[char], char))
            index += 1
            continue
        if char == ";":
            tokens.append(Tok("semi", ";"))
            index += 1
            continue
        if char == "%":
            tokens.append(Tok("pct", "%"))
            index += 1
            continue
        if char == "€":
            tokens.append(Tok("unit", "€"))
            index += 1
            continue
        if text.startswith("root(", index):
            tokens.append(Tok("func", "root"))
            index += 4
            continue
        if char == "π":
            tokens.append(Tok("num", "π", (Fraction(0),), 0))   # handled as a constant below
            tokens[-1] = Tok("const", "π")
            index += 1
            continue
        word = _WORD.match(text, index)
        if word:
            name = word.group(0)
            if name in ("sqrt", "Wurzel"):
                tokens.append(Tok("op", "√"))
            elif _VAR.fullmatch(name):
                tokens.append(Tok("var", name))
            else:
                tokens.append(Tok("word", name))
            index = word.end()
            continue
        tokens.append(Tok("sep", char))
        index += 1
    return tokens


def _unit_at(text: str) -> str | None:
    """The unit (with "/unit" and an exponent) at the start of `text`, if it is one."""

    def atom(at: int) -> int:
        for name in _UNIT_WORDS:
            if text.startswith(name, at):
                end = at + len(name)
                if end < len(text) and (text[end].isalpha() and name not in ("€",)):
                    continue                      # "mit", "Meter" are words, not "m"
                return end
        return -1

    end = atom(0)
    if end < 0:
        return None
    while end < len(text):
        if text[end] in "²³":
            end += 1
            continue
        if text.startswith("^(", end):
            close = text.find(")", end)
            if close > 0 and re.fullmatch(r"-?\d", text[end + 2:close]):
                end = close + 1
                continue
        if text[end] == "/":
            nxt = atom(end + 1)
            if nxt > 0:
                end = nxt
                continue
        break
    return text[:end]


def _unit_quantity(unit: str) -> Quantity:
    """"km/h" → (1000/3600, m s^-1); "m²" → (1, m^2); "Jahre" → opaque year."""

    factor, dims = Fraction(1), {}
    for index, piece in enumerate(unit.split("/")):
        power = 1
        match = re.fullmatch(r"(.+?)(?:\^\((-?\d)\)|([²³]))?", piece)
        if not match:
            raise Unverifiable("unit")
        name = match.group(1)
        if match.group(2):
            power = int(match.group(2))
        elif match.group(3):
            power = 2 if match.group(3) == "²" else 3
        sign = 1 if index == 0 else -1
        if name in _UNITS:
            scale, base = _UNITS[name]
            factor *= scale ** (power * sign)
            dims = _combine(dims, {key: value * power for key, value in base.items()}, sign)
        elif name in _OPAQUE_UNITS:
            dims = _combine(dims, {_OPAQUE_UNITS[name]: power}, sign)
        else:
            raise Unverifiable("unit")
    return Quantity.of(factor, dims)


# ----------------------------------------------------------------------------- parser

class _Parser:
    """Recursive descent over one side of a calculation, in one reading of its numbers.

    expr   := term (("+"|"-") term)*
    term   := factor (("*"|"/") factor | implicit factor)*
    factor := ("-"|"+") factor | power
    power  := atom ("^" factor)?          (right-associative)
    atom   := number [unit|%] | const | var | "(" expr ")" | "√" atom | root(n;x)
    """

    def __init__(self, tokens: Sequence[Tok], choice: Mapping[int, int], env: Mapping[str, Quantity],
                 percent_as_hundredth: bool):
        self.tokens = tokens
        self.position = 0
        self.choice = choice
        self.env = env
        self.percent = percent_as_hundredth

    def peek(self) -> Tok | None:
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self) -> Tok:
        token = self.peek()
        if token is None:
            raise Unverifiable("end")
        self.position += 1
        return token

    def parse(self) -> Quantity:
        value = self.expr()
        if self.peek() is not None:
            raise Unverifiable("trailing")
        return value

    def expr(self) -> Quantity:
        value = self.term()
        while (token := self.peek()) is not None and token.kind == "op" and token.text in "+-":
            self.take()
            right = self.term()
            if value.dim_map() != right.dim_map():
                raise MathFault("unit_mismatch")
            sign = 1 if token.text == "+" else -1
            value = Quantity.of(value.value + sign * right.value, value.dim_map(), value.exact and right.exact)
        return value

    def term(self) -> Quantity:
        value = self.factor()
        while (token := self.peek()) is not None:
            if token.kind == "op" and token.text in "*/":
                self.take()
                right = self.factor()
                value = self._muldiv(value, right, token.text)
            elif token.kind in ("num", "const", "var", "lp", "func") or (token.kind == "op" and token.text == "√"):
                right = self.factor()          # implicit multiplication: 2(3+4), 2π
                value = self._muldiv(value, right, "*")
            else:
                break
        return value

    @staticmethod
    def _muldiv(left: Quantity, right: Quantity, op: str) -> Quantity:
        if op == "*":
            return Quantity.of(left.value * right.value, _combine(left.dim_map(), right.dim_map(), 1),
                               left.exact and right.exact)
        if right.value == 0:
            raise MathFault("division_by_zero")
        return Quantity.of(left.value / right.value, _combine(left.dim_map(), right.dim_map(), -1),
                           left.exact and right.exact)

    def factor(self) -> Quantity:
        token = self.peek()
        if token is not None and token.kind == "op" and token.text in "+-":
            self.take()
            inner = self.factor()
            return Quantity.of(-inner.value if token.text == "-" else inner.value, inner.dim_map(), inner.exact)
        return self.power()

    def power(self) -> Quantity:
        base = self.atom()
        token = self.peek()
        if token is not None and token.kind == "op" and token.text == "^":
            self.take()
            exponent = self.exponent()
            result = _power(base, exponent)
            following = self.peek()
            if following is not None and following.kind == "unit":
                self.take()
                unit = _unit_quantity(following.text)
                result = Quantity.of(result.value * unit.value, _combine(result.dim_map(), unit.dim_map(), 1),
                                     result.exact)
            return result
        return base

    def exponent(self) -> Quantity:
        """A sign and a number or a bracket: the unit after "10^8 m/s" is not the exponent's."""

        token = self.peek()
        sign = 1
        if token is not None and token.kind == "op" and token.text in "+-":
            self.take()
            sign = -1 if token.text == "-" else 1
            token = self.peek()
        if token is not None and token.kind == "num":
            self.take()
            if len(token.readings) > 1:
                raise Unverifiable("exponent")
            return Quantity.of(sign * token.readings[0])
        if token is not None and token.kind == "var":
            self.take()
            value = self.env.get(token.text)
            if value is None or value.dims:      # unknown, a unit, or two values (conflict)
                raise Unverifiable("variable")
            return Quantity.of(sign * value.value, None, value.exact)
        if token is not None and token.kind == "lp":
            self.take()
            inner = self.expr()
            if self.take().kind != "rp":
                raise Unverifiable("paren")
            if inner.dims:
                raise Unverifiable("exponent_unit")
            return Quantity.of(sign * inner.value, None, inner.exact)
        raise Unverifiable("exponent")

    def atom(self) -> Quantity:
        token = self.take()
        if token.kind == "num":
            index = self.position - 1
            value = token.readings[self.choice.get(index, 0)] if len(token.readings) > 1 else token.readings[0]
            quantity = Quantity.of(value)
            following = self.peek()
            if following is not None and following.kind == "unit":
                self.take()
                unit = _unit_quantity(following.text)
                quantity = Quantity.of(value * unit.value, unit.dim_map())
            elif following is not None and following.kind == "pct":
                self.take()
                quantity = Quantity.of(value / 100 if self.percent else value)
            return quantity
        if token.kind == "const":
            return Quantity.of(math.pi, exact=False)
        if token.kind == "var":
            value = self.env.get(token.text)
            if value is not None and "__conflict__" not in value.dim_map():
                return value
            raise Unverifiable("variable")
        if token.kind == "lp":
            inner = self.expr()
            closing = self.take()
            if closing.kind != "rp":
                raise Unverifiable("paren")
            return inner
        if token.kind == "op" and token.text == "√":
            return _root(self.power(), Quantity.of(Fraction(2)))
        if token.kind == "func":
            opening = self.take()
            if opening.kind != "lp":
                raise Unverifiable("root")
            degree = self.expr()
            if self.take().kind != "semi":
                raise Unverifiable("root")
            radicand = self.expr()
            if self.take().kind != "rp":
                raise Unverifiable("root")
            return _root(radicand, degree)
        raise Unverifiable(token.kind)


def _power(base: Quantity, exponent: Quantity) -> Quantity:
    value = exponent.value
    if isinstance(value, Fraction) and value.denominator == 1:
        power = int(value)
        if abs(power) > MAX_EXPONENT:
            raise Unverifiable("exponent")
        if base.value == 0 and power < 0:
            raise MathFault("division_by_zero")
        result: Fraction | float = base.value ** power if isinstance(base.value, Fraction) else float(base.value) ** power
        return Quantity.of(result, {key: dim * power for key, dim in base.dim_map().items()}, base.exact)
    if base.dims:
        raise Unverifiable("fractional_power_unit")
    if float(base.value) < 0:
        raise MathFault("negative_root")
    return Quantity.of(float(base.value) ** float(value), exact=False)


def _root(radicand: Quantity, degree: Quantity) -> Quantity:
    if degree.dims or not (isinstance(degree.value, Fraction) and degree.value.denominator == 1 and degree.value > 0):
        raise Unverifiable("root_degree")
    n = int(degree.value)
    dims = radicand.dim_map()
    if any(power % n for power in dims.values()):
        raise Unverifiable("root_unit")
    value = radicand.value
    if float(value) < 0:
        if n % 2 == 0:
            raise MathFault("negative_root")
        result = -((-float(value)) ** (1 / n))
    else:
        result = float(value) ** (1 / n)
    exact_root = round(result)
    if isinstance(value, Fraction) and Fraction(exact_root) ** n == value:
        return Quantity.of(Fraction(exact_root), {key: power // n for key, power in dims.items()}, radicand.exact)
    return Quantity.of(result, {key: power // n for key, power in dims.items()}, exact=False)


def evaluate(tokens: Sequence[Tok], *, env: Mapping[str, Quantity] | None = None) -> list[Quantity]:
    """Every value the side can have across the readings of its ambiguous numbers and of
    "%" (a percentage, or the number p of a formula that divides by 100 itself). Raises
    Unverifiable when it is not a complete calculation, MathFault when it is one that is
    wrong in every reading."""

    if not tokens or len(tokens) > MAX_TOKENS:
        raise Unverifiable("length")
    ambiguous = [index for index, token in enumerate(tokens) if token.kind == "num" and len(token.readings) > 1]
    if len(ambiguous) > MAX_AMBIGUOUS:
        raise Unverifiable("ambiguous")
    has_percent = any(token.kind == "pct" for token in tokens)
    results: list[Quantity] = []
    faults: list[MathFault] = []
    for combination in itertools.product(*[range(len(tokens[index].readings)) for index in ambiguous]):
        choice = dict(zip(ambiguous, combination))
        for percent in ((True, False) if has_percent else (True,)):
            try:
                results.append(_Parser(tokens, choice, env or {}, percent).parse())
            except MathFault as fault:
                faults.append(fault)
    if not results:
        if faults:
            raise faults[0]
        raise Unverifiable("no_reading")
    return results


# ----------------------------------------------------------------------------- comparison

@dataclass
class Check:
    """One comparison of a computed side with the side the lesson wrote after it."""

    location: str
    text: str
    status: str                      # ok, error, unverifiable, warning
    kind: str = ""                   # wrong_result, decimal_place, rounding, sign, unit_mismatch, ...
    expected: str = ""               # the correct value, formatted like the lesson writes it
    claimed: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value != ""}


def _to_decimal(value: Fraction | float) -> Decimal:
    with localcontext() as context:
        context.prec = 40
        if isinstance(value, Fraction):
            return Decimal(value.numerator) / Decimal(value.denominator)
        return Decimal(repr(float(value)))


def format_number(value: Fraction | float, places: int, language: str = "de") -> str:
    """`value` rounded half-up to `places` decimals, with a decimal comma in German."""

    quantum = Decimal(1).scaleb(-places)
    rounded = _to_decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
    text = f"{rounded:f}"
    return text.replace(".", ",") if language == "de" else text


def _matches(actual: Quantity, claimed: Fraction, places: int, *, approximate: bool) -> str:
    """"exact", "rounded" or "" (no match). A claimed value written with `places` decimals
    matches a longer exact value when it is that value rounded (half-up or half-even)."""

    exact = _to_decimal(actual.value)
    written = _to_decimal(claimed)
    if actual.exact and isinstance(actual.value, Fraction) and actual.value == claimed:
        return "exact"
    quantum = Decimal(1).scaleb(-places)
    try:
        for mode in (ROUND_HALF_UP, ROUND_HALF_EVEN):
            if exact.quantize(quantum, rounding=mode) == written.quantize(quantum):
                return "exact" if exact == written else "rounded"
    except InvalidOperation:
        return ""
    if not actual.exact:
        tolerance = Decimal("0.5") * quantum * Decimal("1.0000001")
        if abs(exact - written) <= tolerance:
            return "rounded"
    if approximate and written != 0:
        # "≈ 0,11" for 0.1111: an approximation to the precision written is not wrong.
        relative = abs((exact - written) / written)
        if relative < Decimal("0.005"):
            return "rounded"
    return ""


def _kind_of_error(actual: Fraction | float, claimed: Fraction, places: int) -> str:
    real, wrote = float(actual), float(claimed)
    if wrote != 0 and real != 0:
        if math.isclose(real, -wrote, rel_tol=1e-9):
            return "sign"
        ratio = real / wrote
        if ratio > 0:
            exponent = math.log10(ratio)
            if abs(exponent - round(exponent)) < 0.004 and round(exponent) != 0:
                return "decimal_place"
    if abs(real - wrote) < 10 ** -places and places > 0:
        return "rounding"
    return "wrong_result"


# ----------------------------------------------------------------------------- chains

_WRONG_MARKERS = re.compile(
    r"\b(falsch|fehler|fehlerhaft|wrong|incorrect|mistake|faux|erreur|incorrecto|error|nicht so)\b|[✗✘❌]",
    re.IGNORECASE)
_RIGHT_MARKERS = re.compile(r"\b(richtig|korrekt|correct|right|juste|correcto)\b|[✓✔]", re.IGNORECASE)


@dataclass
class _Side:
    tokens: list[Tok]
    rel: str = "="                   # the relation that led to this side ("=" or "≈")


def _chains(tokens: list[Tok]) -> list[list[_Side]]:
    """Runs of sides joined by "=" or "≈" inside one line. A side is the longest run of
    calculation tokens around the relation; a word, a sentence mark or an arrow ends it."""

    chains: list[list[_Side]] = []
    current: list[_Side] = []
    run: list[Tok] = []
    pending_rel: str | None = None
    after_word = False

    def close_run() -> list[Tok]:
        nonlocal run
        trimmed = _trim(run)
        run = []
        return trimmed

    for token in tokens + [Tok("sep", "\n")]:
        if token.kind == "rel" and token.text in ("=", "≈"):
            side = close_run()
            if not side:
                current = []
                pending_rel = token.text
                continue
            if current or pending_rel is None:
                if not current:
                    current = [_Side(side)]
                else:
                    current.append(_Side(side, pending_rel or "="))
            pending_rel = token.text
            continue
        if token.kind in ("num", "op", "lp", "rp", "var", "unit", "pct", "const", "func", "semi") or (
                token.kind == "dash" and run and run[-1].kind in ("num", "rp", "var", "unit", "const")):
            if token.kind == "dash":
                token = Tok("op", "-")
            if not run and after_word and token.kind == "op" and token.text in "+-*/^":
                # "5 Äpfel + 2 = 7": the calculation began before the word; this is its tail.
                run.append(Tok("word", "…"))
            after_word = False
            run.append(token)
            continue
        # Anything else ends the side and, after a relation, the chain.
        side = close_run()
        if pending_rel is not None and side and current:
            current.append(_Side(side, pending_rel))
        elif pending_rel is not None and side and not current:
            pass
        if len(current) >= 2:
            chains.append(current)
        current, pending_rel = [], None
        after_word = token.kind == "word"
        if token.kind == "rel":       # ⇒, ≠: a new chain may start after it
            continue
    return chains


def _trim(run: list[Tok]) -> list[Tok]:
    while run and run[0].kind in ("op",) and run[0].text not in "-√+":
        run = run[1:]
    while run and (run[-1].kind == "op" or run[-1].kind == "lp"):
        run = run[:-1]
    depth = 0
    for token in run:
        depth += 1 if token.kind == "lp" else -1 if token.kind == "rp" else 0
    while depth > 0 and run and run[0].kind == "lp":
        run, depth = run[1:], depth - 1
    while depth < 0 and run and run[-1].kind == "rp":
        run, depth = run[:-1], depth + 1
    return run


def _literal(side: list[Tok]) -> tuple[Fraction, int, str | None, bool] | None:
    """(value, decimal places, unit, ambiguous) when the side is one written number with an
    optional sign, unit or % - the form a claimed result has."""

    tokens = list(side)
    sign = 1
    if tokens and tokens[0].kind == "op" and tokens[0].text in "+-":
        sign = -1 if tokens[0].text == "-" else 1
        tokens = tokens[1:]
    if not tokens or tokens[0].kind != "num":
        return None
    number = tokens[0]
    unit = None
    scientific = None
    rest = tokens[1:]
    # "3 · 10^8 m/s" is still one written number.
    if (len(rest) >= 4 and rest[0].kind == "op" and rest[0].text == "*" and rest[1].kind == "num"
            and rest[1].readings == (Fraction(10),) and rest[2].kind == "op" and rest[2].text == "^"):
        exponent_tokens = rest[3:]
        exp_sign, exp_index = 1, 0
        if exponent_tokens and exponent_tokens[0].kind == "lp":
            exponent_tokens = exponent_tokens[1:]
        if exponent_tokens and exponent_tokens[0].kind == "op" and exponent_tokens[0].text in "+-":
            exp_sign = -1 if exponent_tokens[0].text == "-" else 1
            exp_index = 1
        if len(exponent_tokens) > exp_index and exponent_tokens[exp_index].kind == "num":
            scientific = exp_sign * int(exponent_tokens[exp_index].readings[0])
            consumed = 3 + (1 if rest[3].kind == "lp" else 0) + exp_index + 1
            if rest[3].kind == "lp":
                consumed += 1 if len(rest) > consumed - 1 and rest[consumed - 1].kind == "rp" else 0
            rest = [token for token in rest[consumed:] if token.kind != "rp"]
    if rest and rest[0].kind in ("unit", "pct"):
        unit = rest[0].text
        rest = rest[1:]
    if rest:
        return None
    value = number.readings[-1] * sign
    if scientific is not None:
        value *= Fraction(10) ** scientific
    return value, (0 if scientific is not None else number.places), unit, len(number.readings) > 1


def _equivalent_units(actual: Quantity, unit: str | None) -> tuple[bool, Quantity | None]:
    """(units compatible, the claimed unit as a quantity)."""

    if unit is None:
        return (not actual.dims), None
    if unit == "%":
        return (not actual.dims), None
    claimed = _unit_quantity(unit)
    return (claimed.dims == actual.dims), claimed


@dataclass
class Report:
    state: str = UNVERIFIED
    checks: list[Check] = field(default_factory=list)

    @property
    def errors(self) -> list[Check]:
        return [check for check in self.checks if check.status == "error"]

    @property
    def verified(self) -> list[Check]:
        return [check for check in self.checks if check.status == "ok"]

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "checked": len(self.verified), "errors": len(self.errors),
                "unverifiable": sum(1 for check in self.checks if check.status in ("unverifiable", "warning")),
                "checks": [check.as_dict() for check in self.checks[:40]]}


def _state(checks: Sequence[Check]) -> str:
    if any(check.status == "error" for check in checks):
        return NEEDS_REGENERATION
    ok = sum(1 for check in checks if check.status == "ok")
    open_ = sum(1 for check in checks if check.status in ("unverifiable", "warning"))
    if ok and not open_:
        return VERIFIED
    if ok:
        return PARTIALLY_VERIFIED
    return UNVERIFIED


_ASSIGNMENT = re.compile(r"^\s*([A-Za-zα-ωΑ-Ω](?:_[0-9a-z]{1,3}|[0-9]{1,2})?)\s*$")


def check_text(text: Any, *, location: str = "", language: str = "de",
               env: Mapping[str, Quantity] | None = None) -> list[Check]:
    """The checks of every calculation chain in `text`.

    Lines that present a mistake on purpose ("Falsch: ...") are not judged: a lesson shows
    wrong working as a warning. `env` gives variables known values ("K = 1200 €")."""

    checks: list[Check] = []
    known: dict[str, Quantity] = dict(env or {})
    for raw_line in flatten(text).split("\n"):
        line = raw_line.strip()
        if not line or not any(char.isdigit() for char in line):
            continue
        wrong_on_purpose = bool(_WRONG_MARKERS.search(line)) and not _RIGHT_MARKERS.search(line)
        try:
            tokens = tokenize(line, language)
        except Unverifiable:
            continue
        for chain in _chains(tokens):
            checks.extend(_check_chain(chain, line, location, language, known, wrong_on_purpose))
    return checks


def _side_text(side: _Side) -> str:
    return " ".join(token.text for token in side.tokens)


def _check_chain(chain: list[_Side], line: str, location: str, language: str,
                 known: dict[str, Quantity], skip_errors: bool) -> list[Check]:
    checks: list[Check] = []
    values: list[list[Quantity] | None] = []
    faulty: set[int] = set()
    for position, side in enumerate(chain):
        try:
            values.append(evaluate(side.tokens, env=known))
        except MathFault as fault:
            values.append(None)
            faulty.add(position)
            if not skip_errors and len(chain) > 1:
                checks.append(Check(location, line[:200], "error", fault.kind))
        except Unverifiable:
            values.append(None)
    # "K = 1200 €": a variable with a value, for the sides that use it later. A plain
    # "x = 2" is a statement, never judged on its own ("x = 2 oder x = -2" lists two
    # solutions); a variable given two different values is no longer known at all.
    statement = (len(chain) == 2 and len(chain[0].tokens) == 1 and chain[0].tokens[0].kind == "var"
                 and _literal(chain[1].tokens) is not None)
    if len(chain) >= 2 and len(chain[0].tokens) == 1 and chain[0].tokens[0].kind == "var":
        literal_values = values[-1]
        if literal_values is not None and len(literal_values) == 1:
            _remember(known, chain[0].tokens[0].text, literal_values[0])
    if statement:
        return checks
    for index in range(1, len(chain)):
        left, right = values[index - 1], values[index]
        if left is None and right is None or index - 1 in faulty or index in faulty:
            continue
        literal = _literal(chain[index].tokens)
        if left is None:
            if literal is None:
                continue
            # A symbol on the left ("Z = 9 €", "Z = K·p:100 = 60" with K unknown): nothing to compare.
            if any(token.kind == "var" for token in chain[index - 1].tokens):
                continue
            checks.append(Check(location, line[:200], "unverifiable", note="left side not readable"))
            continue
        if right is None:
            if literal is None and any(token.kind == "word" for token in chain[index].tokens):
                checks.append(Check(location, line[:200], "unverifiable"))
            continue
        approximate = chain[index].rel == "≈"
        if literal is not None:
            claimed, places, unit, ambiguous = literal
            outcome = _compare_literal(left, claimed, places, unit, approximate)
        else:
            # Two calculations joined by "=": they must agree in some reading of each.
            outcome = _compare_values(left, right)
        status, kind, expected = outcome
        if status == "error" and skip_errors:
            continue
        check = Check(location, line[:200], status, kind)
        if status == "error" and literal is not None and left:
            # Written like the wrong number: "0,0111" gets "0,1111", "0.0111" gets "0.1111".
            written = next((token.text for token in chain[index].tokens if token.kind == "num"), "")
            style = "de" if "," in written else "en" if "." in written else language
            check.expected = format_number(left[0].value / (_unit_scale(literal[2]) or 1), max(literal[1], 2), style)
            check.claimed = format_number(literal[0], literal[1], style)
        elif expected:
            check.expected = expected
        checks.append(check)
    return checks


_CONFLICT = Quantity.of(Fraction(0), {"__conflict__": 1})


def _remember(known: dict[str, Quantity], name: str, value: Quantity) -> None:
    """Remember a variable's value; two different values make it unknown for good."""

    previous = known.get(name)
    if previous is None:
        known[name] = value
    elif previous is _CONFLICT:
        return
    elif previous.dims != value.dims or not math.isclose(float(previous.value), float(value.value),
                                                         rel_tol=1e-12, abs_tol=1e-12):
        known[name] = _CONFLICT


def _unit_scale(unit: str | None) -> Fraction | float | None:
    if unit is None or unit == "%":
        return None
    try:
        return _unit_quantity(unit).value
    except Unverifiable:
        return None


def _compare_literal(candidates: list[Quantity], claimed: Fraction, places: int, unit: str | None,
                     approximate: bool) -> tuple[str, str, str]:
    """(status, kind, expected) of a calculation against the number written after "="."""

    unit_ok_any = False
    unit_missing = False
    for actual in candidates:
        if not actual.dims and unit not in (None, "%"):
            # "100·4·10 ÷ 36000 = 0,11 €": calculated with bare numbers, the unit written at
            # the end - the school way. The value decides.
            unit_ok_any = True
            if _matches(Quantity.of(actual.value, None, actual.exact), claimed, places, approximate=approximate):
                return "ok", "", ""
            continue
        compatible, claimed_unit = _equivalent_units(actual, unit)
        if not compatible:
            if unit is None and actual.dims:
                # "1200 € · 3 : 100 = 36": the value is right but the unit was left off.
                unit_missing = True
                bare = Quantity.of(actual.value, None, actual.exact)
                if _matches(bare, claimed, places, approximate=approximate):
                    continue
            continue
        unit_ok_any = True
        scale = claimed_unit.value if claimed_unit is not None else 1
        value = actual.value / scale if scale != 1 else actual.value
        target = claimed
        if unit == "%" and not actual.dims:
            # "= 4 %": either the percentage of a fraction (0.04 → 4 %) or the number p.
            if _matches(Quantity.of(value * 100, None, actual.exact), target, places, approximate=approximate):
                return "ok", "", ""
        result = _matches(Quantity.of(value, None, actual.exact), target, places, approximate=approximate)
        if result:
            return "ok", "", ""
    if unit_missing and not unit_ok_any:
        for actual in candidates:
            if _matches(Quantity.of(actual.value, None, actual.exact), claimed, places, approximate=approximate):
                return "warning", "unit_missing", ""
    if not unit_ok_any:
        return "error", "unit_mismatch", ""
    first = next((actual for actual in candidates if _equivalent_units(actual, unit)[0]), None)
    if first is None:
        return "error", _kind_of_error(candidates[0].value, claimed, places), ""
    scale = _unit_scale(unit) or 1
    return "error", _kind_of_error(first.value / scale, claimed, places), ""


def _compare_values(left: list[Quantity], right: list[Quantity]) -> tuple[str, str, str]:
    for one in left:
        for other in right:
            if one.dims != other.dims:
                continue
            if one.exact and other.exact and one.value == other.value:
                return "ok", "", ""
            if math.isclose(float(one.value), float(other.value), rel_tol=1e-9, abs_tol=1e-12):
                return "ok", "", ""
    if all(one.dims != other.dims for one in left for other in right):
        return "error", "unit_mismatch", ""
    return "error", "wrong_result", ""


# ----------------------------------------------------------------------------- formulas

def formula_variables(text: str) -> set[str]:
    return {token.text for token in tokenize(flatten(text)) if token.kind == "var"}


def _equation_sides(text: str) -> tuple[list[Tok], list[Tok]] | None:
    tokens = tokenize(flatten(text).split("\n")[0])
    relations = [index for index, token in enumerate(tokens) if token.kind == "rel" and token.text == "="]
    if len(relations) != 1:
        return None
    left, right = _trim(tokens[:relations[0]]), _trim(tokens[relations[0] + 1:])
    if not left or not right or any(token.kind == "word" for token in left + right):
        return None
    return left, right


def formulas_equivalent(first: str, second: str) -> bool | None:
    """Whether two formulas over the same variables say the same thing ("W = G · p : 100"
    and "G = W · 100 : p"), by values at fixed sample points; None when that cannot be
    decided (different variables, a formula this module cannot read)."""

    a, b = _equation_sides(first), _equation_sides(second)
    if a is None or b is None:
        return None
    names_a = {token.text for token in a[0] + a[1] if token.kind == "var"}
    names_b = {token.text for token in b[0] + b[1] if token.kind == "var"}
    if not names_a or names_a != names_b:
        return None
    # The first formula must solve for one variable: "W = ..." gives W from the others.
    if not (len(a[0]) == 1 and a[0][0].kind == "var"):
        return None
    target = a[0][0].text
    others = sorted(names_a - {target})
    checked = 0
    for offset in range(len(SAMPLE_POINTS)):
        env = {name: Quantity.of(SAMPLE_POINTS[(offset + position) % len(SAMPLE_POINTS)])
               for position, name in enumerate(others)}
        try:
            env[target] = evaluate(a[1], env=env)[0]
            left = evaluate(b[0], env=env)[0]
            right = evaluate(b[1], env=env)[0]
        except (Unverifiable, MathFault):
            continue
        checked += 1
        if not math.isclose(float(left.value), float(right.value), rel_tol=1e-9, abs_tol=1e-12):
            return False
    return True if checked else None


# ----------------------------------------------------------------------------- lessons

def lesson_texts(lesson: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(location, text) of every place a lesson states calculations. Question options are
    left out: a multiple-choice question offers wrong values on purpose."""

    out: list[tuple[str, str]] = []
    for index, part in enumerate(lesson.get("parts") or []):
        if not isinstance(part, Mapping):
            continue
        out.append((f"parts[{index}].body", str(part.get("body") or "")))
        for number, item in enumerate(part.get("items") or []):
            if isinstance(item, Mapping):
                out.append((f"parts[{index}].items[{number}]",
                            f"{item.get('label') or ''} {item.get('text') or ''}".strip()))
        if part.get("answer"):
            out.append((f"parts[{index}].answer", str(part.get("answer"))))
    if lesson.get("explanation"):
        out.append(("explanation", str(lesson.get("explanation"))))
    worked = lesson.get("worked_example")
    if isinstance(worked, Mapping):
        out.append(("worked_example.problem", str(worked.get("problem") or "")))
        for number, step in enumerate(worked.get("steps") or []):
            out.append((f"worked_example.steps[{number}]", str(step or "")))
        out.append(("worked_example.answer", str(worked.get("answer") or "")))
    for key in ("teacher_tips", "exceptions"):
        for number, entry in enumerate(lesson.get(key) or []):
            text = entry.get("text") if isinstance(entry, Mapping) else entry
            out.append((f"{key}[{number}]", str(text or "")))
    return [(location, text) for location, text in out if text.strip()]


def _part_group(location: str) -> str:
    match = re.match(r"(parts\[\d+\]|worked_example|explanation|teacher_tips\[\d+\]|exceptions\[\d+\])", location)
    return match.group(1) if match else location


def check_lesson(lesson: Mapping[str, Any], *, language: str = "de",
                 teacher_formulas: Iterable[str] = ()) -> Report:
    """Every calculation of a lesson checked; values a part assigns ("K = 1200 €") are known
    in the rest of that part. A worked example's answer must agree with its last result.
    A formula over the same variables as one of the teacher's must be a rearrangement of it."""

    checks: list[Check] = []
    envs: dict[str, dict[str, Quantity]] = {}
    for location, text in lesson_texts(lesson):
        group = _part_group(location)
        env = envs.setdefault(group, {})
        checks.extend(check_text(text, location=location, language=language, env=env))
        _collect_assignments(text, env, language)
    checks.extend(_answer_agreement(lesson, language))
    checks.extend(_rearrangements(lesson, teacher_formulas))
    return Report(_state(checks), checks)


def _collect_assignments(text: str, env: dict[str, Quantity], language: str) -> None:
    for line in flatten(text).split("\n"):
        for piece in re.split(r"[,;]\s+|\s+(?:und|and|bei|für)\s+", line):
            if "=" not in piece or piece.count("=") != 1:
                continue
            name, value = piece.split("=")
            if not _ASSIGNMENT.match(name):
                continue
            try:
                tokens = _trim(tokenize(value.strip(), language))
                literal = _literal(tokens)
                if literal is not None and not literal[3]:
                    results = evaluate(tokens)
                    if len(results) == 1:
                        _remember(env, name.strip(), results[0])
            except (Unverifiable, MathFault):
                continue


def _answer_agreement(lesson: Mapping[str, Any], language: str) -> list[Check]:
    """The worked example's stated answer against the result its last step computed."""

    worked = lesson.get("worked_example")
    if not isinstance(worked, Mapping):
        return []
    steps = [str(step or "") for step in worked.get("steps") or []]
    answer = str(worked.get("answer") or "")
    if not steps or not answer:
        return []
    try:
        answer_tokens = _trim(tokenize(flatten(answer).split("\n")[0].split("=")[-1].strip(), language))
    except Unverifiable:
        return []
    literal = _literal(answer_tokens)
    if literal is None:
        return []
    for step in reversed(steps):
        lines = [line for line in flatten(step).split("\n") if "=" in line]
        if not lines:
            continue
        try:
            last = _trim(tokenize(lines[-1].split("=")[-1].strip(), language))
        except Unverifiable:
            return []
        stated = _literal(last)
        if stated is None:
            return []
        value, places, unit, _ambiguous = stated
        answer_value, answer_places, answer_unit, _ = literal
        same_unit = (unit or "") == (answer_unit or "") or not unit or not answer_unit
        if same_unit and (value == answer_value or _matches(Quantity.of(value), answer_value, answer_places,
                                                             approximate=True)):
            return [Check("worked_example.answer", answer[:200], "ok")]
        return [Check("worked_example.answer", answer[:200], "error", "answer_mismatch",
                      expected=format_number(value, max(places, answer_places), language),
                      claimed=format_number(answer_value, answer_places, language))]
    return []


def _rearrangements(lesson: Mapping[str, Any], teacher_formulas: Iterable[str]) -> list[Check]:
    formulas = [formula for formula in teacher_formulas if formula and "=" in formula]
    if not formulas:
        return []
    checks: list[Check] = []
    for location, text in lesson_texts(lesson):
        for line in flatten(text).split("\n"):
            if line.count("=") != 1 or any(char.isdigit() for char in line.split("=")[0]):
                continue
            if _WRONG_MARKERS.search(line) and not _RIGHT_MARKERS.search(line):
                continue
            candidate = _formula_in(line)
            if candidate is None:
                continue
            for teacher in formulas:
                verdict = formulas_equivalent(teacher, candidate)
                if verdict is None:
                    continue
                checks.append(Check(location, candidate[:200], "ok" if verdict else "error",
                                    "" if verdict else "rearrangement"))
                break
    return checks


def _formula_in(line: str) -> str | None:
    """The "X = ..." formula a line states, if its sides are only symbols and numbers."""

    tokens = tokenize(line)
    relation = next((index for index, token in enumerate(tokens) if token.kind == "rel" and token.text == "="), None)
    if relation is None:
        return None
    left = _trim(tokens[:relation])
    right_run: list[Tok] = []
    for token in tokens[relation + 1:]:
        if token.kind in ("word", "rel", "sep") and token.text not in (",",):
            break
        right_run.append(token)
    right = _trim(right_run)
    while left and left[0].kind != "var":
        left = left[1:]
    if len(left) != 1 or not right or not any(token.kind == "var" for token in right):
        return None
    return f"{left[0].text} = " + " ".join(token.text for token in right)


def teacher_formula_used(lesson: Mapping[str, Any], formula: str) -> bool | None:
    """Whether the lesson states the teacher's formula (any rearrangement of it counts)."""

    for _location, text in lesson_texts(lesson):
        for line in flatten(text).split("\n"):
            candidate = _formula_in(line)
            if candidate is None:
                continue
            verdict = formulas_equivalent(formula, candidate)
            if verdict:
                return True
    return False


__all__ = [
    "NEEDS_REGENERATION", "PARTIALLY_VERIFIED", "STATES", "UNVERIFIED", "VERIFIED", "Check", "MathFault", "Quantity",
    "Report", "Unverifiable", "check_lesson", "check_text", "evaluate", "flatten", "format_number",
    "formulas_equivalent", "lesson_texts", "teacher_formula_used", "tokenize",
]

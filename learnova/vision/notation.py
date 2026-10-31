"""Canonical forms of written mathematics, so two readings can be compared by structure.

"3x + 5 = 20", "3x+5=20" and "$3x + 5 = 20$" are the same expression; "3x + 5 = 2O" is
not, and neither is "-4.5" for a German "−4,5" - the decimal separator is kept, because
reading a comma as a point is exactly the kind of error being measured. The LaTeX that
readers produce for fractions, roots and exponents is folded into one linear notation:

    \\frac{2}{3}  -> (2)/(3)        x^{2}, x²  -> x^2        \\sqrt{x} -> sqrt(x)
    \\cdot, ·, ×  -> *              \\div, ÷   -> :          −, –      -> -

Used by the evaluation suite and the tests; nothing at run time rewrites what a student
wrote.
"""

from __future__ import annotations

import re

_SUPERSCRIPTS = str.maketrans({"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5",
                               "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9", "⁻": "-", "⁺": "+"})
_SYMBOLS = {
    "−": "-", "–": "-", "—": "-", "·": "*", "⋅": "*", "×": "*", "∙": "*", "÷": ":",
    "≤": "<=", "≥": ">=", "≠": "!=", "√": "sqrt", "π": "pi",
}
_LATEX = (
    (r"\\left|\\right", ""), (r"\\cdot|\\times|\\ast", "*"), (r"\\div", ":"),
    (r"\\leq?", "<="), (r"\\geq?", ">="), (r"\\neq", "!="), (r"\\pi", "pi"),
    (r"\\,|\\;|\\!|\\quad|~", ""),
)


def _frac(text: str) -> str:
    pattern = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
    while pattern.search(text):
        text = pattern.sub(r"(\1)/(\2)", text)
    return text


def canonical_math(expression: str) -> str:
    text = str(expression or "").strip()
    text = re.sub(r"^\$+|\$+$", "", text).strip()
    text = text.replace("\\(", "").replace("\\)", "").replace("\\[", "").replace("\\]", "")
    text = _frac(text)
    text = re.sub(r"\\sqrt\s*\{([^{}]*)\}", r"sqrt(\1)", text)
    for pattern, replacement in _LATEX:
        text = re.sub(pattern, replacement, text)
    # Superscript digits become ^n; a run like x²³ is one exponent.
    text = re.sub(r"([⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+)", lambda match: "^" + match.group(1).translate(_SUPERSCRIPTS), text)
    for symbol, replacement in _SYMBOLS.items():
        text = text.replace(symbol, replacement)
    text = re.sub(r"\^\{([^{}]*)\}", r"^\1", text)
    text = re.sub(r"_\{([^{}]*)\}", r"_\1", text)
    text = text.replace("{", "(").replace("}", ")")
    text = re.sub(r"\s+", "", text)
    # A single-character group says nothing: (2)/(3) and 2/3 are the same fraction.
    text = re.sub(r"\(([A-Za-z0-9]+)\)/", r"\1/", text)
    text = re.sub(r"/\(([A-Za-z0-9]+)\)", r"/\1", text)
    return text


def same_math(expected: str, actual: str) -> bool:
    return canonical_math(expected) == canonical_math(actual)

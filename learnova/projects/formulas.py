"""Formulas of the student's material: found by structure, not by words (spec Phase 2).

The topic extraction lists formulas as items of the topic map. They used to be checked
against the pages like any other item - as a bag of words - so "y = m \\cdot x + b" was
dropped on a page that says "y = mx + b" (different words), while "U = \\frac{R}{I}" passed
on a page that says "U = R · I" (the same letters) and became a flashcard "from your notes".
Here a formula is compared by its structure:

* `match_key` folds one formula into a linear form: LaTeX and Unicode spellings of the same
  symbols become one ("\\frac{a}{b}" = "a/b", "x^{2}" = "x²", "\\cdot" / "·" / implicit
  multiplication, "x_1" = "x₁" = "x1", "\\rightarrow" = "→" = "->", "\\ce{…}" unwrapped),
  case and spacing do not matter. Everything else does: "U = R/I" is not "U = R·I", and
  "I = U/R" is not on a page that only writes "U = R · I" (a rearrangement is a different
  formula until a person has written it down).
* `page_formulas` cuts each line of a page into its formula runs (prose words around a
  formula are not part of it, "Ohmsches Gesetz: U = R · I (in V)" holds "U = R · I").
* `find_on_pages` looks for a formula on the topic's pages and returns the page and the
  formula exactly as the page writes it (the teacher's notation, shown and taught first).
* `enrich_formula` adds what the lesson and the notes page need to a topic-map formula item;
  `inventory` and `prompt_lines` list the formulas of the notes for lessons, practice and
  exams.

Pure: no database, no AI, no Flask. Flashcard identity (terms.item_key) is NOT derived from
`match_key`: it is part of every card's origin key, and changing it would duplicate cards.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

MAX_VARIABLES = 6
MAX_LATEX = 400
MAX_NOTATION = 200
PROMPT_LIMIT = 900

_SUBSCRIPTS = str.maketrans({"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7",
                             "₈": "8", "₉": "9", "₊": "+", "₋": "-"})
# A page's "s = ½ a t²" is the model's "s = \frac{1}{2} a t^2".
_VULGAR_FRACTIONS = {"½": " 1/2 ", "⅓": " 1/3 ", "⅔": " 2/3 ", "¼": " 1/4 ", "¾": " 3/4 "}
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "phi": "φ", "varphi": "φ", "omega": "ω", "Delta": "Δ", "Sigma": "Σ",
    "Omega": "Ω", "Phi": "Φ", "Lambda": "Λ",
}
# LaTeX operators and arrows in the spelling the pages use, so a page and a LaTeX formula
# are cut into the same runs.
_OPERATORS = (
    (r"\\rightleftharpoons(?![A-Za-z])|⇌", " <=> "),
    (r"\\(?:long)?rightarrow(?![A-Za-z])|\\to(?![A-Za-z])|→|⟶", " -> "),
    (r"\\Rightarrow(?![A-Za-z])|⇒", " => "),
    (r"\\(?:cdot|times|ast)(?![A-Za-z])|[⋅∙×]", " · "),
    (r"\\div(?![A-Za-z])|÷", " : "),
    (r"\\pm(?![A-Za-z])", " ± "),
    (r"\\leq?(?![A-Za-z])", " ≤ "), (r"\\geq?(?![A-Za-z])", " ≥ "), (r"\\neq?(?![A-Za-z])", " ≠ "),
    (r"\\approx(?![A-Za-z])", " ≈ "),
    (r"\\(?:left|right|displaystyle)(?![A-Za-z])|\\[,;!]|\\q?quad(?![A-Za-z])", " "),
    (r"[−–—]", "-"),
)
_UNWRAP = re.compile(r"\\(?:ce|pu|text|textrm|mathrm|mathit|mathbf|operatorname)\s*\{")
_INNER_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_INNER_FRAC = re.compile(r"\\[dt]?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_INNER_POWER = re.compile(r"\^\s*\{([^{}]*)\}")
_INNER_INDEX = re.compile(r"_\s*\{([^{}]*)\}")
# Characters that join two whitespace-separated pieces into one formula ("R · I", "s : t").
_JOINERS = frozenset("=+-*/·^_<>±≤≥≈≠")
_RELATION = re.compile(r"(<=>|->|=>|<=|>=|!=|=|<|>|≈)")
_PROSE_WORD = re.compile(r"^[^\W\d_]{4,}[.,;:!?]*$")
# One symbol or number ("m", "x1", "2"): "y = m x + b" is implicit multiplication inside a formula.
_SYMBOL = re.compile(r"^(?:[^\W\d_][\d₀-₉²³¹⁰⁴-⁹]?|\d+)$")
# "2 H2 + O2 -> 2 H2O": a coefficient written apart from its chemical formula belongs to it.
_NUMBER = re.compile(r"^\d+$")
# "1/2 a t²": a number (or a fraction of numbers) before a symbol is implicit multiplication too.
_NUMERIC = re.compile(r"^[\d.,/()]*\d[\d.,/()]*$")
_CHEMICAL = re.compile(r"^(?:[A-Z][a-z]?[\d₀-₉]*)+$")
_EDGE_PUNCTUATION = ".,;!?"


def _unwrap_groups(text: str) -> str:
    """\\ce{…}, \\text{…} and friends replaced by their (balanced) content."""

    while True:
        match = _UNWRAP.search(text)
        if not match:
            return text
        depth, position = 1, match.end()
        while position < len(text) and depth:
            if text[position] == "{":
                depth += 1
            elif text[position] == "}":
                depth -= 1
            position += 1
        inner = text[match.end():position - 1] if depth == 0 else text[match.end():]
        text = text[:match.start()] + " " + inner + " " + text[position:]


def linear(text: Any) -> str:
    """A formula (or a page line) in one linear spelling, spacing kept: delimiters removed,
    nested \\frac / \\sqrt / ^{…} folded from the inside out, LaTeX operators and arrows as
    the symbols a page writes."""

    value = str(text or "")
    value = value.replace("\\(", " ").replace("\\)", " ").replace("\\[", " ").replace("\\]", " ")
    value = value.replace("$", " ")
    for fraction, spelled in _VULGAR_FRACTIONS.items():
        value = value.replace(fraction, spelled)
    value = _unwrap_groups(value)
    for _pass in range(12):
        before = value
        value = _INNER_SQRT.sub(lambda match: f"√({match.group(1)})", value)
        value = _INNER_FRAC.sub(r"(\1)/(\2)", value)
        value = _INNER_POWER.sub(lambda match: "^" + (match.group(1).strip() if len(match.group(1).strip()) == 1
                                                      else f"({match.group(1)})"), value)
        value = _INNER_INDEX.sub(r"_\1", value)
        if value == before:
            break
    for pattern, replacement in _OPERATORS:
        value = re.sub(pattern, replacement, value)
    value = re.sub(r"\\([A-Za-z]+)", lambda match: _GREEK.get(match.group(1), match.group(0)), value)
    return value.translate(_SUBSCRIPTS)


def _key(piece: str) -> str:
    # Imported here: the learnova.vision package pulls in the image reader (Pillow, the AI
    # service), and this module must stay importable without them (test_project_engine_contracts).
    from learnova.vision.notation import canonical_math  # noqa: PLC0415

    key = canonical_math(piece).casefold()
    # "s : t" is the German way to write s/t.
    key = key.replace("*", "").replace("_", "").replace("·", "").replace(":", "/")
    key = re.sub(r"\s+", "", key)
    # A one-character group says nothing: "√(x)" = "√x", "(2)/(3)" = "2/3".
    for _pass in range(3):
        key = re.sub(r"\(([^\W_]|\d+)\)", r"\1", key)
    return key.strip(_EDGE_PUNCTUATION)


def _joins(left: str, right: str) -> bool:
    # A spaced ":" is division ("v = s : t"); a colon after a word is a label ("Gesetz: U = R · I").
    return left[-1] in _JOINERS or right[0] in _JOINERS or ":" in (left, right)


def _trim(tokens: list[str]) -> list[str]:
    """Prose at the edges of a run is not part of the formula: "Gesetz: U = R" starts at U,
    but "Arbeit = Kraft · Weg" (a word equation) keeps its words."""

    while len(tokens) > 1 and _PROSE_WORD.match(tokens[0]) and not _RELATION.match(_key(tokens[1]) or "x"):
        tokens = tokens[1:]
        while len(tokens) > 1 and all(char in _JOINERS for char in tokens[0]):
            tokens = tokens[1:]
    while len(tokens) > 1 and _PROSE_WORD.match(tokens[-1]) and tokens[-2][-1] not in _JOINERS:
        tokens = tokens[:-1]
    return tokens


@dataclass(frozen=True)
class PageFormula:
    text: str        # the formula as the page writes it (in linear spelling where it used LaTeX)
    key: str         # match_key of it


def _is_formula_key(key: str) -> bool:
    parts = _RELATION.split(key)
    return len(parts) >= 3 and len(key) >= 3 and all(part for part in parts[::2])


@lru_cache(maxsize=4096)
def page_formulas(line: str) -> tuple[PageFormula, ...]:
    """The formula runs of one line: whitespace-separated pieces joined where an operator
    connects them, prose trimmed off the edges; only runs with a relation (=, <, >, ->)."""

    tokens = linear(line).split()
    runs: list[list[str]] = []
    for token in tokens:
        if runs and (_joins(runs[-1][-1], token) or (
                (_SYMBOL.match(runs[-1][-1]) or _NUMERIC.match(runs[-1][-1])) and _SYMBOL.match(token)
                and any(_RELATION.search(_key(piece) or "") for piece in runs[-1]))
                or (_NUMBER.match(runs[-1][-1]) and _CHEMICAL.match(token))):
            runs[-1].append(token)
        else:
            runs.append([token])
    found: list[PageFormula] = []
    for run in runs:
        trimmed = _trim(run)
        text = " ".join(trimmed).strip(_EDGE_PUNCTUATION + ":")
        if text.count("(") < text.count(")") and text.endswith(")"):
            text = text[:-1]
        if text.count("(") > text.count(")") and text.startswith("("):
            text = text[1:]
        key = _key(text)
        if _is_formula_key(key):
            found.append(PageFormula(text.strip(), key))
    return tuple(found)


def match_key(text: Any) -> str:
    """The structural key of one formula ("" when it is no formula: no relation sign). A
    name with words around the formula ("Ohmsches Gesetz: U = R \\cdot I") gives the key
    of its longest formula run."""

    text = " ".join(str(text or "").split())
    if text and not any(_PROSE_WORD.match(token) and not token.startswith("\\") for token in linear(text).split()):
        # Nothing but mathematics (a LaTeX field, "y = m x + b"): spaces mean nothing in it.
        whole = _key(linear(text))
        if _is_formula_key(whole):
            return whole
    runs = page_formulas(text)
    return max((run.key for run in runs), key=len) if runs else ""


def _same(formula: str, page: str) -> bool:
    """`formula` is `page`, or a link of its chain: "v = s/t" is on "v = s/t = 10 m/s"."""

    if formula == page:
        return True
    mine, theirs = _RELATION.split(formula), _RELATION.split(page)
    if len(mine) >= len(theirs):
        return False
    return any(theirs[start:start + len(mine)] == mine for start in range(0, len(theirs) - len(mine) + 1, 2))


@dataclass(frozen=True)
class FoundFormula:
    page_id: int | None
    notation: str


def find_on_pages(name: Any, latex: Any, page_text: Mapping[int, str], *,
                  prefer: int | None = None) -> FoundFormula:
    """The page that writes this formula (name or latex) and how it writes it there;
    FoundFormula(None, "") when no page does. The preferred page (the model's page_id) is
    tried first, then the pages in the given order."""

    keys = {key for key in (match_key(name), match_key(latex)) if key}
    if not keys:
        return FoundFormula(None, "")
    order = list(page_text)
    if prefer in page_text:
        order.remove(prefer)
        order.insert(0, prefer)
    for page_id in order:
        for line in str(page_text.get(page_id) or "").splitlines():
            if "=" not in line and "<" not in line and ">" not in line and "→" not in line and "\\" not in line \
                    and "⇌" not in line and "≤" not in line and "≥" not in line and "⟶" not in line:
                continue
            for formula in page_formulas(line.strip()):
                if any(_same(key, formula.key) for key in keys):
                    return FoundFormula(page_id, verbatim(line.strip(), formula)[:MAX_NOTATION])
    return FoundFormula(None, "")


@lru_cache(maxsize=1024)
def verbatim(line: str, formula: PageFormula) -> str:
    """`formula` exactly as `line` writes it: the shortest run of the line's own words with
    the same key ("2 H₂ + O₂ → 2 H₂O", not the linear "2 H2 + O2 -> 2 H2O"), else its linear
    text (a formula glued to a label, "Gesetz:U=R·I")."""

    tokens = line.split()
    width = len(formula.text.split())
    best = ""
    for start in range(len(tokens)):
        for end in range(start + 1, min(len(tokens), start + width) + 1):
            text = " ".join(tokens[start:end]).strip(_EDGE_PUNCTUATION + ":")
            if (not best or len(text) < len(best)) and text.count("(") == text.count(")") \
                    and _key(linear(text)) == formula.key:
                best = text
    return best or formula.text


def is_symbolic(name: Any, latex: Any = "") -> bool:
    """Whether the formula is written with a relation sign (it can be checked by structure);
    a formula listed by name only ("Satz des Pythagoras") cannot."""

    return bool(match_key(name) or match_key(latex))


def _fold(text: Any) -> str:
    return " ".join(str(text or "").casefold().split())


def clean_variables(value: Any, formula_key: str, page_text: str = "") -> list[dict[str, Any]]:
    """At most six {symbol, meaning, unit, in_notes} entries of a formula: only symbols that
    occur in the formula; in_notes when the page gives the meaning or the unit."""

    if not isinstance(value, list):
        return []
    page = _fold(page_text)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            continue
        fields = {str(key).strip().casefold(): entry[key] for key in entry}
        symbol = " ".join(str(fields.get("symbol") or fields.get("zeichen") or fields.get("variable")
                              or fields.get("symbol_name") or "").split())[:24]
        meaning = " ".join(str(fields.get("meaning") or fields.get("bedeutung") or fields.get("name")
                               or fields.get("quantity") or fields.get("groesse") or "").split())[:120]
        unit = " ".join(str(fields.get("unit") or fields.get("einheit") or "").split())[:24]
        folded = _key(linear(symbol)) if symbol else ""
        if not folded or folded in seen or not meaning or (formula_key and folded not in formula_key):
            continue
        seen.add(folded)
        on_page = bool(page) and (_fold(meaning) in page or (len(unit) >= 1 and unit != "-" and
                                                              re.search(r"(?<!\w)" + re.escape(_fold(unit)) +
                                                                        r"(?!\w)", page) is not None))
        out.append({"symbol": symbol, "meaning": meaning, "unit": unit, "in_notes": on_page})
        if len(out) >= MAX_VARIABLES:
            break
    return out


def checked_latex(value: Any) -> str:
    """The model's LaTeX for a formula, without delimiters, when it is valid; else ""."""

    from learnova.assistant.formatting import latex_problems  # noqa: PLC0415 - avoids an import cycle

    text = " ".join(str(value or "").split())[:MAX_LATEX]
    text = re.sub(r"^\$+|\$+$", "", text).strip()
    if text.startswith("\\(") and text.endswith("\\)"):
        text = text[2:-2].strip()
    return text if text and not latex_problems(text) else ""


def enrich_formula(item: Mapping[str, Any], page_text: Mapping[int, str], *,
                   teacher_pages: set[int] | frozenset[int] = frozenset()) -> dict[str, Any]:
    """A topic-map formula item with what lessons and the notes page need:

    notation      the formula exactly as the page writes it ("" when no page writes it)
    match         "exact" (found by structure), "name" (a named formula without symbols,
                  found by its name), "words" (only its words are on the page: a
                  rearrangement or another form - shown as added, never a card)
    origin        "notes" for exact/name, else "added"
    in_notes      origin == "notes"
    latex         the item's LaTeX when it is valid, else ""
    variables     cleaned (clean_variables)
    teacher_marked the formula's page is a Kompetenzraster or teacher-highlighted page

    An item already enriched keeps its match (the read-time enrichment of an old map
    never re-judges a new one)."""

    out = dict(item)
    name, latex = str(item.get("name") or ""), checked_latex(item.get("latex"))
    out["latex"] = latex
    if item.get("match") not in ("exact", "name", "words"):
        prefer = item.get("page_id") if isinstance(item.get("page_id"), int) else None
        if is_symbolic(name, latex):
            found = find_on_pages(name, latex, page_text, prefer=prefer)
            if found.page_id is not None:
                out.update(match="exact", notation=found.notation, page_id=found.page_id)
            else:
                out.update(match="words", notation="")
        else:
            out.update(match="name" if item.get("in_notes") else "words", notation="")
    out["origin"] = "notes" if out["match"] in ("exact", "name") else "added"
    out["in_notes"] = out["origin"] == "notes" and bool(item.get("in_notes", True))
    if not out["in_notes"]:
        out["origin"] = "added"
    out.setdefault("notation", "")
    page_id = out.get("page_id")
    # A symbol's meaning may be on another page of the topic (the worksheet explains what the
    # Kompetenzraster only names).
    out["variables"] = clean_variables(item.get("variables"), match_key(out["notation"] or name) or match_key(latex),
                                       "\n".join(str(text or "") for text in page_text.values()))
    marked = bool(out["in_notes"] and isinstance(page_id, int) and page_id in teacher_pages)
    if out["in_notes"] and not marked and out["match"] == "exact" and teacher_pages:
        # The worksheet writes the formula and the Kompetenzraster lists it too: the teacher
        # marked it, whichever of the two pages the model cited.
        marked_pages = {key: text for key, text in page_text.items() if key in teacher_pages}
        marked = bool(marked_pages) and find_on_pages(out["notation"] or name, latex,
                                                      marked_pages).page_id is not None
    out["teacher_marked"] = marked
    return out


def needs_enrichment(topic_map: Mapping[str, Any] | None) -> bool:
    """A stored map with formula items analysed before structural matching (no "match")."""

    items = topic_map.get("items") if isinstance(topic_map, Mapping) else None
    return isinstance(items, list) and any(isinstance(item, Mapping) and item.get("kind") == "formula"
                                           and "match" not in item for item in items)


def enrich_map(topic_map: Mapping[str, Any], page_text: Mapping[int, str], *,
               teacher_pages: set[int] | frozenset[int] = frozenset()) -> dict[str, Any]:
    """A copy of a stored topic map whose formula items are enriched (read time, never
    committed: the next analysis stores enriched items itself)."""

    items = []
    for item in topic_map.get("items") or []:
        if isinstance(item, Mapping) and item.get("kind") == "formula":
            items.append(enrich_formula(item, page_text, teacher_pages=teacher_pages))
        else:
            items.append(item)
    return {**topic_map, "items": items}


def inventory(topic_maps: Sequence[Mapping[str, Any]], *,
              page_order: Mapping[int, int] | None = None) -> list[dict[str, Any]]:
    """The formulas of the notes across topic maps: in-notes formula items, one per
    structure (match_key, else the name), teacher-marked first, then in page order."""

    order = page_order or {}
    found: dict[str, dict[str, Any]] = {}
    position = 0
    for index, topic_map in enumerate(topic_maps):
        if not isinstance(topic_map, Mapping):
            continue
        for item in topic_map.get("items") or []:
            if not isinstance(item, Mapping) or item.get("kind") != "formula" or not item.get("in_notes"):
                continue
            if item.get("origin") == "added":
                continue
            name = str(item.get("notation") or item.get("name") or "").strip()
            if not name:
                continue
            key = match_key(name) or match_key(item.get("latex")) or _fold(name)
            if key in found:
                found[key]["teacher_marked"] = found[key]["teacher_marked"] or bool(item.get("teacher_marked"))
                continue
            page_id = item.get("page_id") if isinstance(item.get("page_id"), int) else None
            found[key] = {
                "name": name, "latex": str(item.get("latex") or ""), "detail": str(item.get("detail") or ""),
                "page_id": page_id, "teacher_marked": bool(item.get("teacher_marked")),
                "variables": [variable for variable in item.get("variables") or [] if isinstance(variable, Mapping)],
                "section": index, "position": position,
            }
            position += 1
    return sorted(found.values(), key=lambda entry: (
        not entry["teacher_marked"], order.get(entry["page_id"], 10 ** 6) if entry["page_id"] is not None else 10 ** 6,
        entry["position"]))


def prompt_lines(items: Sequence[Mapping[str, Any]], *, labels: Mapping[int, str] | None = None,
                 limit: int = PROMPT_LIMIT) -> list[str]:
    """One line per formula of the notes, as the prompts receive them, within `limit`
    characters in all: 'U = R · I [Physik.pdf p. 2, teacher-marked] — U: Spannung (V); …'."""

    labels = labels or {}
    lines: list[str] = []
    used = 0
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        where = [labels.get(item.get("page_id"), "")] if item.get("page_id") is not None else []
        if item.get("teacher_marked"):
            where.append("teacher-marked")
        tag = f" [{', '.join(part for part in where if part)}]" if any(where) else ""
        variables = "; ".join(
            f"{variable.get('symbol')}: {variable.get('meaning')}" + (f" ({variable.get('unit')})"
                                                                       if variable.get("unit") else "")
            for variable in item.get("variables") or [] if variable.get("in_notes", True))
        latex = str(item.get("latex") or "")
        line = name + (f" (LaTeX: {latex})" if latex and latex != name else "") + tag
        line += f" — {variables}" if variables else ""
        if used + len(line) + 1 > limit:
            break
        lines.append(line)
        used += len(line) + 1
    return lines

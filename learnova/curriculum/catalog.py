"""Baden-Württemberg school structure: school types, Bildungsplan families, grade bands, subjects.

Baden-Württemberg publishes one Bildungsplan per *plan family*, not per school type:
Grundschule (Klassen 1-4), the shared Sekundarstufe I plan used by Werkrealschule,
Realschule and Gemeinschaftsschule (Klassen 5-10, with the niveaus G/M/E), the
Gymnasium plan (Klassen 5-12/13), and the plans of the vocational schools. A learner's
school type therefore selects a plan family, and the grade selects the band inside it.

Everything here is structure, not curriculum content: the content itself is only ever
what an operator imported from a cleared source (see `sources.py`).
"""

from __future__ import annotations

import re

# School-type tokens stored on the user. "" = not chosen yet; "outside_bw" switches the
# Bildungsplan grounding off for learners who follow another state's curriculum.
SCHOOL_TYPES = (
    "grundschule", "werkrealschule", "realschule", "gemeinschaftsschule",
    "gymnasium", "berufliche_schule", "outside_bw",
)
SCHOOL_TYPE_SET = frozenset(SCHOOL_TYPES)

# English source labels; they are catalogue keys, so they translate with the interface.
SCHOOL_TYPE_LABELS_EN = {
    "grundschule": "Primary school (Grundschule)",
    "werkrealschule": "Werkrealschule / Hauptschule",
    "realschule": "Realschule",
    "gemeinschaftsschule": "Gemeinschaftsschule",
    "gymnasium": "Gymnasium",
    "berufliche_schule": "Vocational school (berufliche Schule)",
    "outside_bw": "School outside Baden-Württemberg",
}

# Plan families as stored on imported documents.
PLANS = ("grundschule", "sek1", "gymnasium", "berufliche")
PLAN_LABELS = {
    "grundschule": "Grundschule",
    "sek1": "Sekundarstufe I (Werkrealschule, Realschule, Gemeinschaftsschule)",
    "gymnasium": "Gymnasium",
    "berufliche": "Berufliche Schulen",
}

_SCHOOL_PLAN = {
    "grundschule": "grundschule",
    "werkrealschule": "sek1",
    "realschule": "sek1",
    "gemeinschaftsschule": "sek1",
    "gymnasium": "gymnasium",
    "berufliche_schule": "berufliche",
}

# The Sekundarstufe I plan states every standard on three niveaus. Which one a school
# type aims at is general knowledge about the school system, not curriculum content.
_SCHOOL_NIVEAU = {
    "werkrealschule": "Niveau G (grundlegend)",
    "realschule": "Niveau M (mittel); from Klasse 7 some classes work on Niveau G",
    "gemeinschaftsschule": "Niveaus G, M and E side by side; pitch to the learner, not to one niveau",
}


def normalize_school_type(value: str | None) -> str:
    token = str(value or "").strip().casefold()
    return token if token in SCHOOL_TYPE_SET else ""


def school_type_label(value: str) -> str:
    return SCHOOL_TYPE_LABELS_EN.get(normalize_school_type(value), "Not set")


def niveau_hint(school_type: str) -> str:
    return _SCHOOL_NIVEAU.get(normalize_school_type(school_type), "")


def plans_for(school_type: str, grade: int | None) -> tuple[str, ...]:
    """Plan families to search for this learner; () switches grounding off.

    An unset school type is not a reason to drop the grounding: grades 1-4 can only be
    the Grundschule, and for 5-10 both secondary plans are searched and the band label on
    every excerpt keeps the model honest about which plan it came from."""

    school_type = normalize_school_type(school_type)
    if school_type == "outside_bw":
        return ()
    if school_type:
        return (_SCHOOL_PLAN[school_type],)
    if grade is None:
        return ()
    if grade <= 4:
        return ("grundschule",)
    if grade <= 10:
        return ("gymnasium", "sek1")
    return ("gymnasium",)


# Learnova subject names (the lesson form offers English names; free text is allowed)
# mapped to the Bildungsplan subject keys they correspond to.
_SUBJECT_KEYS = {
    "mathematics": "mathematik", "mathematik": "mathematik", "math": "mathematik", "maths": "mathematik",
    "german": "deutsch", "deutsch": "deutsch",
    "english": "englisch", "englisch": "englisch",
    "french": "franzoesisch", "französisch": "franzoesisch", "franzoesisch": "franzoesisch",
    "latin": "latein", "latein": "latein",
    "physics": "physik", "physik": "physik",
    "chemistry": "chemie", "chemie": "chemie",
    "biology": "biologie", "biologie": "biologie",
    "history": "geschichte", "geschichte": "geschichte",
    "geography": "geographie", "geographie": "geographie", "erdkunde": "geographie",
    "computer science": "informatik", "informatik": "informatik",
}
SUBJECT_KEYS = frozenset(_SUBJECT_KEYS.values()) | {"sachunterricht", "bnt"}
_SCIENCE = {"physik", "chemie", "biologie"}


def subject_keys(subject: str | None, grade: int | None) -> tuple[str, ...]:
    """Bildungsplan subjects whose standards apply to a lesson in `subject` at `grade`.

    Primary school teaches science and history inside Sachunterricht, and Klassen 5/6
    teach the sciences as BNT (Biologie, Naturphänomene und Technik); a biology question
    in Klasse 6 is answered by the BNT plan, so both are searched."""

    key = _SUBJECT_KEYS.get(str(subject or "").strip().casefold())
    if not key:
        return ()
    keys = [key]
    if grade is not None and grade <= 4 and (key in _SCIENCE or key in {"geschichte", "geographie"}):
        keys.append("sachunterricht")
    if grade is not None and 5 <= grade <= 6 and key in _SCIENCE:
        keys.append("bnt")
    return tuple(keys)


def grade_number(grade: str | None) -> int | None:
    token = str(grade or "").strip()
    return int(token) if token.isdigit() and 1 <= int(token) <= 13 else None


# Band headings as the plans print them: "Klassen 7/8", "Klassen 7/8/9", "Klasse 10",
# "Klassen 5 und 6", "Kursstufe". Returns the inclusive grade range or None.
_BAND = re.compile(r"\bKlassen?(?:stufen?)?\s+(\d{1,2}(?:\s*(?:/|-|–|und|bis)\s*\d{1,2})*)", re.IGNORECASE)
_UPPER = re.compile(r"\b(Kursstufe|Oberstufe|Jahrgangsstufen?\s*1\s*/\s*2)\b", re.IGNORECASE)


def band_from_text(text: str) -> tuple[int, int] | None:
    match = _BAND.search(text or "")
    if match:
        numbers = [int(value) for value in re.findall(r"\d{1,2}", match.group(1))]
        numbers = [value for value in numbers if 1 <= value <= 13]
        if numbers:
            return min(numbers), max(numbers)
    if _UPPER.search(text or ""):
        # The Kursstufe is 11/12 under G8 and 12/13 under G9; the band covers both.
        return 11, 13
    return None


def band_label(low: int, high: int) -> str:
    if not low or not high:
        return ""
    if low == high:
        return f"Klasse {low}"
    if low >= 11:
        return "Kursstufe"
    if high - low >= 3:
        return f"Klassen {low}–{high}"
    return "Klassen " + "/".join(str(value) for value in range(low, high + 1))

"""Canonical school subjects.

The project form used to offer nine English names and a free-text "Other" field, so the
stored subject was whatever the student typed: "Ethik", "Mathe", "Bio LK", "Gemeinschafts-
kunde". Every subject-aware rule (LaTeX for maths, the teaching strategy, grading rules)
matched exact English keys and silently fell back to the generic behaviour. Here every
spelling is folded to one canonical English name, which is what gets stored and what the
rule tables look up; the display label goes through the translation catalogue.

Pure module: no database, no Flask.
"""

from __future__ import annotations

import re
import unicodedata

SUBJECT_CHOICES: tuple[str, ...] = (
    "Mathematics", "German", "English", "French", "Spanish", "Latin", "Physics", "Chemistry", "Biology",
    "Science and Technology", "History", "Geography", "Politics", "Economics", "Ethics", "Philosophy",
    "Religion", "Computer Science", "Technology", "Music", "Art", "Physical Education", "Other",
)
OTHER = "Other"
MAX_SUBJECT_CHARS = 80

# Subjects that are themselves a language: their example material stays in that language.
# German counts (the app teaches German to non-German speakers too); which teaching
# strategy a language gets is decided by the strategy registry, not here.
LANGUAGE_SUBJECTS = frozenset({
    "English", "German", "German as a Foreign Language", "French", "Spanish", "Latin", "Italian", "Portuguese", "Russian", "Chinese",
    "Japanese", "Korean", "Turkish", "Greek", "Arabic", "Hebrew", "Dutch", "Polish", "Swedish", "Danish",
    "Norwegian", "Finnish", "Czech", "Slovak", "Hungarian", "Romanian", "Croatian", "Serbian", "Ukrainian",
    "Hindi",
})

# Canonical name -> spellings. Written naturally (umlauts, accents); `_keys` folds them.
_ALIASES: dict[str, tuple[str, ...]] = {
    "Mathematics": ("mathematics", "mathematik", "mathe", "math", "maths", "mathématiques", "matemáticas",
                    "matematica",
                    # Areas of school maths: the assistant's TOPIC line names them as the subject
                    # ("TOPIC: Algebra | Gleichung lösen", live 2026-11-02), and so do students.
                    "algebra", "lineare algebra", "linear algebra", "algèbre", "álgebra", "geometrie", "geometry",
                    "analytische geometrie", "analytic geometry", "géométrie", "geometría", "stochastik",
                    "stochastics", "wahrscheinlichkeitsrechnung", "probability", "probabilités", "probabilidad",
                    "trigonometrie", "trigonometry", "trigonométrie", "trigonometría", "arithmetik", "arithmetic",
                    "arithmétique", "aritmética", "calculus", "infinitesimalrechnung", "differentialrechnung",
                    "integralrechnung", "bruchrechnung", "prozentrechnung"),
    "German": ("german", "deutsch", "allemand", "alemán"),
    # Learning German as a language (not German lessons on texts and literature): the
    # language strategy, like English or French.
    "German as a Foreign Language": ("deutsch als fremdsprache", "daf", "deutsch als zweitsprache", "daz",
                                     "german as a second language", "allemand langue étrangère",
                                     "alemán como lengua extranjera"),
    "English": ("english", "englisch", "anglais", "inglés"),
    "French": ("french", "französisch", "français", "francés"),
    "Spanish": ("spanish", "spanisch", "español", "espagnol"),
    "Latin": ("latin", "latein", "latín"),
    "Physics": ("physics", "physik", "physique", "física", "ph", "mechanik", "mechanics", "optik", "optics",
                "elektrizitätslehre", "elektrodynamik", "electrodynamics", "thermodynamik", "thermodynamics",
                "kinematik", "kinematics", "akustik", "acoustics", "wärmelehre", "kernphysik", "nuclear physics"),
    "Chemistry": ("chemistry", "chemie", "chimie", "química", "ch", "organische chemie", "anorganische chemie",
                  "organic chemistry", "inorganic chemistry", "stöchiometrie", "stoichiometry"),
    "Biology": ("biology", "biologie", "bio", "biología", "genetik", "genetics", "zellbiologie", "cell biology",
                "zoologie", "zoology", "botanik", "botany", "humanbiologie", "human biology", "neurobiologie"),
    "Science and Technology": ("science and technology", "nwt", "naturwissenschaft und technik",
                               "naturwissenschaft technik", "nwt naturwissenschaft und technik",
                               "naturwissenschaften", "naturwissenschaft", "science", "sciences", "bnt",
                               "biologie naturphänomene und technik", "naturphänomene", "nawi",
                               "sciences et technologie", "ciencia y tecnología"),
    # "Historique"/"Historial" are what the catalogue currently shows for "History".
    "History": ("history", "geschichte", "histoire", "historia", "historique", "historial"),
    "Geography": ("geography", "geographie", "geografie", "erdkunde", "geo", "géographie", "geografía", "ek"),
    "Politics": ("politics", "politik", "gemeinschaftskunde", "sozialkunde", "gk", "politics and society",
                 "political science", "politikwissenschaft", "social studies", "sozialwissenschaften",
                 "politique", "política", "civics", "éducation civique"),
    "Economics": ("economics", "wirtschaft", "wbs", "ökonomie", "wirtschaftslehre", "volkswirtschaft",
                  "économie", "economía", "business studies"),
    "Ethics": ("ethics", "ethik", "werte und normen", "éthique", "ética", "ler"),
    "Philosophy": ("philosophy", "philosophie", "filosofía", "filosofia"),
    "Religion": ("religion", "reli", "religionslehre", "evangelische religion", "katholische religion",
                 "evangelische religionslehre", "katholische religionslehre", "islamische religion",
                 "ev religion", "kath religion", "religious education", "religionsunterricht", "religión"),
    "Computer Science": ("computer science", "informatik", "it", "computing", "informatique", "informática",
                         "inf"),
    "Technology": ("technology", "technik", "technologie", "tecnología"),
    "Music": ("music", "musik", "musique", "música", "mu"),
    "Art": ("art", "kunst", "bildende kunst", "bk", "arts", "arts plastiques", "arte", "kunsterziehung",
            "educación artística"),
    "Physical Education": ("physical education", "sport", "sportunterricht", "pe", "p e", "education physique",
                           "éducation physique", "educación física", "éducation physique et sportive", "eps"),
    "Other": ("other", "anderes", "sonstiges", "autre", "otro"),
    # Further languages: canonical English name, family "languages".
    "Italian": ("italian", "italienisch", "italiano", "italien"),
    "Portuguese": ("portuguese", "portugiesisch", "português"),
    "Russian": ("russian", "russisch", "russe", "ruso"),
    "Chinese": ("chinese", "chinesisch", "mandarin", "chinois", "chino"),
    "Japanese": ("japanese", "japanisch", "japonais", "japonés"),
    "Korean": ("korean", "koreanisch"),
    "Turkish": ("turkish", "türkisch", "turc", "turco"),
    "Greek": ("greek", "griechisch", "altgriechisch", "ancient greek", "grec", "griego"),
    "Arabic": ("arabic", "arabisch", "arabe", "árabe"),
    "Hebrew": ("hebrew", "hebräisch"),
    "Dutch": ("dutch", "niederländisch", "holländisch", "néerlandais"),
    "Polish": ("polish", "polnisch", "polonais"),
    "Swedish": ("swedish", "schwedisch"),
    "Danish": ("danish", "dänisch"),
    "Norwegian": ("norwegian", "norwegisch"),
    "Finnish": ("finnish", "finnisch"),
    "Czech": ("czech", "tschechisch"),
    "Slovak": ("slovak", "slowakisch"),
    "Hungarian": ("hungarian", "ungarisch"),
    "Romanian": ("romanian", "rumänisch"),
    "Croatian": ("croatian", "kroatisch"),
    "Serbian": ("serbian", "serbisch"),
    "Ukrainian": ("ukrainian", "ukrainisch"),
    "Hindi": ("hindi",),
}

# Spellings that name a subject only on their own: inside a longer name they usually mean
# something else ("Text analysis", "Statistik der Wirtschaft"), so _phrase_match skips them.
_EXACT_ONLY: dict[str, tuple[str, ...]] = {
    "Mathematics": ("analysis", "statistik", "statistics", "statistique", "estadística"),
    "Biology": ("ökologie", "ecology", "écologie", "ecología"),
}

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_NON_WORD = re.compile(r"[^0-9a-z]+")
_MAX_NGRAM = 5


def _strip_marks(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def _keys(text: str) -> tuple[str, ...]:
    """Lookup keys for `text`: umlauts folded to ae/oe/ue and, separately, to plain a/o/u.

    Both are needed because students write "Französisch", "Franzoesisch" and
    "Franzosisch"; punctuation and repeated spaces never matter ("Geo/Wi" == "geo wi")."""

    lowered = unicodedata.normalize("NFC", str(text or "")).casefold()
    out = []
    for variant in (lowered.translate(_UMLAUTS), lowered):
        key = " ".join(_NON_WORD.sub(" ", _strip_marks(variant)).split())
        if key and key not in out:
            out.append(key)
    return tuple(out)


def _build_lookup(aliases: dict[str, tuple[str, ...]], *, names: bool = True,
                  into: dict[str, str] | None = None) -> dict[str, str]:
    lookup: dict[str, str] = dict(into or {})
    for canonical, spellings in aliases.items():
        for spelling in ((canonical, *spellings) if names else spellings):
            for key in _keys(spelling):
                previous = lookup.setdefault(key, canonical)
                if previous != canonical:     # an alias may belong to exactly one subject
                    raise ValueError(f"subject alias {key!r} maps to {previous!r} and {canonical!r}")
    return lookup


# Phrases ("Ethik Klasse 10") are matched against _PHRASES; a whole name against _LOOKUP.
_PHRASES = _build_lookup(_ALIASES)
_LOOKUP = _build_lookup(_EXACT_ONLY, names=False, into=_PHRASES)


# Words that make the next subject the topic of the one before ("Geschichte der
# Philosophie" is philosophy, "Histoire de la philosophie" too).
_OF_WORDS = frozenset({"der", "des", "of", "de", "du", "del"})
_ARTICLES = frozenset({"the", "la", "le", "les", "l"})
# An alias this short is a course level or an abbreviation at least as often as a subject
# ("GK" Grundkurs or Gemeinschaftskunde, "Bio LK"): a full subject name beats it.
_WEAK_ALIAS = 3


def _phrase_match(key: str) -> str | None:
    """The subject named inside a longer phrase ("Ethik Klasse 10", "Mathe GK", "Bio LK").

    Overlapping matches keep the longest ("Naturwissenschaft und Technik" is NwT, not
    Technik). Of the rest, the leading subject wins - "Musik Theorie und Geschichte" is
    music, "Mathe für Physik" maths - except that a full subject name beats a short alias
    ("GK Geschichte" and "Mathe GK": "GK" is a course level), and a subject after "der" /
    "of" is the topic of the one before ("Geschichte der Philosophie" is philosophy)."""

    words = key.split()
    found: list[tuple[int, int, str]] = []          # (start, end, canonical)
    for start in range(len(words)):
        for size in range(1, min(_MAX_NGRAM, len(words) - start) + 1):
            canonical = _PHRASES.get(" ".join(words[start:start + size]))
            if canonical is not None:
                found.append((start, start + size, canonical))
    kept: list[tuple[int, int, str]] = []
    for match in sorted(found, key=lambda item: (-(len(" ".join(words[item[0]:item[1]]))), item[0])):
        if all(match[1] <= other[0] or match[0] >= other[1] for other in kept):
            kept.append(match)
    if not kept:
        return None
    kept.sort()
    strong = [match for match in kept if len(" ".join(words[match[0]:match[1]])) > _WEAK_ALIAS]
    chosen = (strong or kept)[0]
    for later in (strong or kept)[1:]:
        between = words[chosen[1]:later[0]]
        if between and between[0] in _OF_WORDS and all(word in _OF_WORDS | _ARTICLES for word in between):
            chosen = later
        else:
            break
    return chosen[2]


def _clip(text: str) -> str:
    """`text` cut to MAX_SUBJECT_CHARS without leaving a partial word at the end.

    The stored value is looked up again later (`strategy_for(project.subject)`). A cut
    through a word can leave a fragment that is itself an alias ("… Chronik" → "… Ch",
    Chemistry), so the stored subject would suddenly resolve to a different subject than
    the one the student typed."""

    if len(text) <= MAX_SUBJECT_CHARS:
        return text
    cut = text[:MAX_SUBJECT_CHARS]
    if text[MAX_SUBJECT_CHARS].isalnum() and cut[-1].isalnum():
        cut = re.sub(r"[^\W_]+$", "", cut) or cut      # one 80-letter word cannot be an alias
    return cut.rstrip()


def canonical_subject(raw: str | None) -> str:
    """The canonical English subject name for whatever the student or an old row says.

    Known spellings map to their canonical name; an unknown subject is kept as typed
    (whitespace tidied, at most 80 characters) so nothing the student wrote is lost;
    empty or punctuation-only means "Other". Idempotent: the result maps to itself."""

    text = " ".join(str(raw or "").split())
    if not any(ch.isalnum() for ch in text):
        return OTHER
    keys = _keys(text)
    for key in keys:
        if key in _LOOKUP:
            return _LOOKUP[key]
    for key in keys:
        found = _phrase_match(key)
        if found:
            return found
    return _clip(text)


# Subjects taught as one that combine several (Baden-Württemberg: Sachunterricht in primary
# school, the Fächerverbünde GWG/EWG, the IMP profile), as their canonical parts.
COMPOSITE_SUBJECTS: dict[str, tuple[str, ...]] = {
    "sachunterricht": ("Science and Technology", "Geography", "History"),
    "gwg": ("Geography", "Economics", "Politics"),
    "geographie wirtschaft gemeinschaftskunde": ("Geography", "Economics", "Politics"),
    "ewg": ("Geography", "Economics", "Politics"),
    "erdkunde wirtschaft gemeinschaftskunde": ("Geography", "Economics", "Politics"),
    "imp": ("Computer Science", "Mathematics", "Physics"),
    "informatik mathematik physik": ("Computer Science", "Mathematics", "Physics"),
}
# What joins the subjects of a combined one as typed: "Geschichte/Gemeinschaftskunde",
# "Physik und Mathematik", "Informatik, Mathematik, Physik", "Bio-Chemie".
_JOINERS = re.compile(r"\s*(?:[/,;+&]|-|\bund\b|\band\b|\bet\b|\by\b)\s*", re.IGNORECASE)


def subject_parts(raw: str | None) -> tuple[str, ...]:
    """The canonical subjects a combined subject is made of, or () for a single subject.

    A known composite (Sachunterricht, GWG, IMP) gives its parts. Otherwise the text is
    split where it joins subjects ("und", "/", ",", "-"); only segments that are a subject
    on their own and longer than two letters count, so "Mathe GK" (a course level) and
    "Geschichte der Philosophie" (one subject) stay single, and a spelling that is itself
    one subject ("Naturwissenschaft und Technik") is never split."""

    text = " ".join(str(raw or "").split())
    keys = _keys(text)
    for key in keys:
        if key in COMPOSITE_SUBJECTS:
            return COMPOSITE_SUBJECTS[key]
    if any(key in _LOOKUP for key in keys):
        return ()
    found: list[str] = []
    for segment in _JOINERS.split(text):
        segment_keys = _keys(segment)
        if not segment_keys or len(segment_keys[0]) <= 2:
            continue
        canonical = next((_LOOKUP[key] for key in segment_keys if key in _LOOKUP), None)
        if canonical and canonical != OTHER and canonical not in found:
            found.append(canonical)
    return tuple(found) if len(found) >= 2 else ()


def is_language_subject(canonical: str | None) -> bool:
    """True when the subject is itself a language (English, German, Latin, Italian …)."""

    return canonical_subject(canonical) in LANGUAGE_SUBJECTS

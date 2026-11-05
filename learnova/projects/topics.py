"""Topic extraction for Project Mode: batching, normalisation, merging and reconciliation.

A 20-page document can hold 15 topics, and the same topic often runs over several pages
or is repeated on a revision sheet. The model reads the pages in batches that fit its
budgets; this module turns the batch results into one clean topic map and reconciles it
with what the student already has.

Everything here is deterministic (no database, no AI):

* `page_batches` packs consecutive pages into small batches. A long page is split at a
  paragraph or sentence break, never cut: a 28k-character batch used to overflow the
  4000-token answer, and the whole batch was then lost.
* `normalize_topic` validates one model topic leniently: an unusable term, item, card or
  list entry is dropped, never the whole topic. A topic must cite at least one real page.
  Fachbegriffe and items are kept only when the student's pages really mention them.
* `topic_key` is the stable identity of a topic ("Newton's laws" == "Newtonsche Gesetze"
  is a translation the key cannot see, but "1. Newton's Laws" == "Newton's laws" is).
* `same_topic` decides whether two keys are one topic. It is deliberately strict: merging
  "Utilitarismus nach Bentham" into "Utilitarismus nach Mill" loses a topic silently,
  while two topics that should be one cost the student a tap on a suggestion
  (`overlap_suggestions`).
* `merge_topics` merges duplicates across batches; `cap_topics` enforces the topic limit
  and says how many it dropped.
* `reconcile` decides, for a re-analysis, which stored topics are updated in place, which
  are new, and which are kept although the new scan no longer mentions them (a topic the
  student renamed, added, or already learned is never deleted).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from learnova.vocabulary.service import headword

MAX_BATCH_CHARS = 9_000           # ~2.5k tokens of page text: the answer for it fits the output budget
MAX_BATCH_PAGES = 4               # more pages per call means more topics per answer, and truncation
MAX_TOPICS = 60
MAX_TERMS = 40
MAX_ITEMS = 40
MAX_EXAM_QUESTIONS = 12
MAX_RELATED = 8
MAX_RECALL_CARDS = 20
MIN_SPLIT_CHARS = 400             # below this a failed call is not an answer-size problem
DEFAULT_RECALL_PROMPT = "What does “{term}” mean?"
LIST_FIELDS = ("learning_goals", "important_facts", "definitions", "formulas", "examples",
               "vocabulary", "relationships", "likely_exam_questions")
LIST_LIMITS = {"learning_goals": 10, "important_facts": 20, "definitions": 20, "formulas": 15,
               "examples": 15, "vocabulary": 25, "relationships": 15, "likely_exam_questions": 12}
DIFFICULTY_WORDS = {"easy": 1, "leicht": 1, "medium": 2, "mittel": 2, "hard": 3, "schwer": 3, "difficult": 3}
IMPORTANCE_WORDS = {"low": 1, "niedrig": 1, "gering": 1, "medium": 2, "mittel": 2, "normal": 2,
                    "high": 3, "hoch": 3, "wichtig": 3}

# Shared with learnova/lessons/strategies/base.py (identical literal; a test asserts equality).
ITEM_KINDS = ("term", "example", "experiment", "theory", "person", "argument", "formula", "process",
              "diagram", "event", "date", "rule", "thought_experiment", "work", "method", "source",
              "place", "institution", "position", "cause_effect", "structure")
PART_KINDS = ("objective", "why", "explanation", "terms", "rule", "structure", "example",
              "worked_example", "experiment", "thought_experiment", "theory", "argument",
              "perspectives", "timeline", "cause_effect", "process", "diagram", "source_analysis",
              "model_analysis", "mistakes", "guided_exercise", "summary")
SERVER_PART_KINDS = ("flashcards",)   # inserted by the server, never requested from the model
KNOWLEDGE_STATES = ("new", "developing", "secure")
TOPIC_KINDS = ("main", "subtopic")

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
# The stop words of the first key version. Kept apart so `reconcile` can still recognise
# keys stored before "nach" and digits started to count.
_LEGACY_STOP = frozenset({
    "der", "die", "das", "und", "oder", "von", "mit", "im", "in", "am", "an", "zu", "zum", "zur",
    "ein", "eine", "einer", "des", "dem", "den", "fuer", "auf", "the", "and", "of", "to", "a",
    "for", "with", "on", "kapitel", "chapter", "thema", "topic", "teil", "part", "einfuehrung",
    "introduction", "grundlagen", "basics",
})
# "nach"/"according to" carry no topic of their own: "Utilitarismus nach Bentham" is about
# Bentham's utilitarianism, so the key is {bentham, utilitarismus}.
_STOP = _LEGACY_STOP | frozenset({
    "nach", "bei", "laut", "gemaess", "according", "ueber", "about", "vs", "versus",
})
# Key words (stemmed) that turn a topic into a different one: "Kritik am kategorischen
# Imperativ" is a lesson of its own next to "Kategorischer Imperativ", so `same_topic`
# never lets one of these be the single word a title adds.
_ASPECT_WORDS = frozenset({
    "kritik", "einwand", "einwaend", "gegenargument", "gegenposition", "widerleg", "grenz", "problem",
    "vergleich", "criticism", "critiqu", "objection", "counterargument", "limit", "limitation",
    "comparison",
})
_SUFFIXES = ("ungen", "ung", "en", "er", "es", "e", "s")

# A leading "1." is usually the notes' numbering ("3. Newtonsche Gesetze"), but in these
# titles it is an ordinal that names the topic: "1. Weltkrieg" and "2. Weltkrieg" are two
# topics, as are the 1. and 2. binomische Formel. Exact folded words, not stems: the plural
# "3. Newtonsche Gesetze" is numbering, the singular "3. Newtonsches Gesetz" an ordinal.
_ORDINAL_NOUNS = frozenset({
    "weltkrieg", "weltkrieges", "weltkriegs", "krieg", "krieges", "kriegs", "jahrhundert",
    "jahrhunderts", "jh", "jahrtausend", "reich", "reiches", "republik", "kreuzzug", "konzil",
    "revolution", "ableitung", "strahlensatz", "hauptsatz", "formel", "gesetz", "regel", "fall",
    "person", "klasse", "stand", "world", "war", "century", "law", "amendment", "derivative",
    "republic", "crusade",
})
_CHAPTER_WORDS = r"(?:kapitel|chapter|abschnitt|section|lektion|lesson|unit|einheit|thema|topic)"
_ENUMERATIONS = (
    # "Kapitel 3: Kant", "Thema 2 – Kant", "Lektion 4 Kant"
    re.compile(rf"^{_CHAPTER_WORDS}\s*[0-9]+[a-z]?(?:\.[0-9]+)*\s*(?:[:.)\]\-–—]\s*|\s+)", re.IGNORECASE),
    # "1.2 Kant", "1.2.3. Kant" (a date such as "9.11.1989 Mauerfall" is not numbering)
    re.compile(r"^[0-9]{1,3}(?:\.[0-9]{1,3})+\.?(?=[\s:)\-–—])[\s:)\-–—]*"),
    # "2) Kant", "(2) Kant", "[2] Kant", "3: Kant", "3 – Kant"
    re.compile(r"^(?:[(\[]?[0-9]{1,3}[)\]]|[0-9]{1,3}\s*:|[0-9]{1,3}\s+[-–—])\s*"),
    # "a) Kant", "(a) Kant"
    re.compile(r"^\(?[a-z]\)\s*", re.IGNORECASE),
)
# "3. Kant" and Roman "II. Kant" (an initial, "I. Kant", carries no identity either).
_ORDINAL_DOT = re.compile(r"^(?:[0-9]{1,3}|[IVXLC]{1,5})\.\s+(?P<rest>.+)$")


def _fold(value: Any) -> str:
    """Casefolded, umlauts spelled out, other accents dropped: "Révolution" -> "revolution"."""

    text = unicodedata.normalize("NFC", str(value or "")).casefold().translate(_FOLD)
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _words(text: Any) -> list[str]:
    """Folded, stemmed content words (Latin letters: the key alphabet). Digits count:
    "Text 1" is not "Text 2"."""

    return [_stem(word) for word in re.findall(r"[a-z0-9]+", _fold(text)) if word not in _STOP]


def _tokens(text: Any) -> list[str]:
    """Folded words in any script ("Квадратное уравнение", "范畴"), for matching text
    against the pages; keys keep using `_words`."""

    return re.findall(r"[^\W_]+", _fold(text))


def _content(text: Any) -> list[str]:
    """Stemmed content words in any script: what grounding compares."""

    return [_stem(word) for word in _tokens(text) if word not in _STOP]


def _script_words(text: Any, *, stop: bool) -> list[str]:
    """The key's fallback when a text has no Latin content word: its own words, accents
    kept ("Λόγος" -> "λόγος", the spelling flashcard keys use), without (stop=True) or
    with the stop words."""

    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", str(text or "")).casefold().translate(_FOLD))
    return [word for word in words if not stop or word not in _STOP]


def _strip_enumeration(title: Any) -> str:
    text = " ".join(str(title or "").split())
    for _ in range(2):            # "1. a) Kant"
        stripped = text
        for pattern in _ENUMERATIONS:
            match = pattern.match(text)
            if match:
                stripped = text[match.end():]
                break
        else:
            match = _ORDINAL_DOT.match(text)
            if match:
                following = re.findall(r"[a-z0-9]+", _fold(match.group("rest")))[:3]
                if not _ORDINAL_NOUNS.intersection(following):
                    stripped = match.group("rest")
        stripped = stripped.strip()
        # Nothing but stop words left ("3. Grundlagen"): the number is all that tells it apart.
        if stripped == text or not (_words(stripped) or _script_words(stripped, stop=True)):
            break
        text = stripped
    return text


def topic_key(title: str, main_topic: str = "") -> str:
    """Stable identity: the folded, stemmed content words of the title, sorted.

    The notes' own numbering ("3.", "2)", "Kapitel 3:") is not part of the identity; any
    other number is ("Text 1" != "Text 2", "Utilitarismus Teil 1" != "Teil 2").

    A title without Latin content words falls back to its own words in its own script,
    then to the main topic, then to its stop words ("Einführung" != "Grundlagen"); only an
    empty title becomes "topic". Every such title used to get the key "topic", and
    `same_topic` then merged all of them into one."""

    title, main_topic = _strip_enumeration(title), _strip_enumeration(main_topic)
    words = (_words(title) or _script_words(title, stop=True) or _words(main_topic)
             or _script_words(main_topic, stop=True) or _script_words(title, stop=False)
             or _script_words(main_topic, stop=False))
    return "-".join(sorted(set(words)))[:120] or "topic"


def _legacy_key(title: str, main_topic: str = "") -> str:
    """The key as the first version computed it (digits dropped, fewer stop words)."""

    def words(text: str) -> list[str]:
        folded = str(text or "").casefold().translate(_FOLD)
        return [_stem(word) for word in re.findall(r"[a-z0-9]+", folded)
                if word not in _LEGACY_STOP and not word.isdigit()]

    found = sorted(set(words(title))) or sorted(set(words(main_topic)))
    return "-".join(found)[:120] or "topic"


def term_key(term: Any) -> str:
    """Identity of a Fachbegriff: "Der kategorische Imperativ" == "Kategorischer Imperativ".

    The same key as `learnova.projects.terms.term_key` (flashcard identity; a test keeps
    them equal): a slash joins two concepts ("Ursache/Wirkung"), and a term made only of
    stop words ("Grundlagen") keeps its folded spelling instead of the shared key "topic"."""

    head = headword(str(term or "").replace("/", " "))
    key = topic_key(head)
    if key and key != "topic":
        return key[:120]
    return "-".join(re.findall(r"\w+", head.casefold().translate(_FOLD)))[:120] or "term"


def same_topic(a_key: str, b_key: str) -> bool:
    """Two topic keys name one topic: equal, or one adds a single word to a 2+ word key.

    "Kategorischer Imperativ" == "Kants kategorischer Imperativ", but "Ethik" != "Ethik
    nach Kant" (a one-word key is too general to absorb anything), "Utilitarismus nach
    Bentham" != "Utilitarismus nach Mill" (neither contains the other), and "Kritik am
    kategorischen Imperativ" != "Kategorischer Imperativ" (the added word is an aspect
    that makes a lesson of its own: `_ASPECT_WORDS`)."""

    if not a_key or not b_key:
        return False
    if a_key == b_key:
        return True
    first, second = set(a_key.split("-")), set(b_key.split("-"))
    if first == second:
        return True
    small, large = (first, second) if len(first) <= len(second) else (second, first)
    return (len(small) >= 2 and small < large and len(large) - len(small) <= 1
            and not (large - small) & _ASPECT_WORDS)


# --------------------------------------------------------------------------- batching

_BREAKS = (
    (re.compile(r"\n[ \t]*\n"), "start"),                       # paragraph
    (re.compile(r"\n"), "start"),                               # line
    (re.compile(r"[.!?…;:][\"'»«“”’)\]]*(?=\s)"), "end"),       # sentence
    (re.compile(r"\s"), "start"),                               # word
)


def _break_point(text: str, low: int, high: int, target: int) -> int:
    """Where to cut `text`: the best kind of break between low and high, nearest target."""

    for pattern, side in _BREAKS:
        best = None
        for match in pattern.finditer(text, 0, min(len(text), high + 1)):
            cut = match.start() if side == "start" else match.end()
            if low <= cut <= high and (best is None or abs(cut - target) < abs(best - target)):
                best = cut
        if best is not None:
            return best
    return high


def _split_text(text: str, max_chars: int) -> list[str]:
    """A long page in chunks of at most max_chars; only whitespace at the cuts is lost."""

    chunks: list[str] = []
    rest = text.strip()
    while len(rest) > max_chars:
        cut = _break_point(rest, max(1, max_chars // 2), max_chars, max_chars)
        chunk, rest = rest[:cut].rstrip(), rest[cut:].lstrip()
        if chunk:
            chunks.append(chunk)
    if rest:
        chunks.append(rest)
    return chunks


def page_batches(pages: Iterable[dict[str, Any]], *, max_chars: int = MAX_BATCH_CHARS,
                 max_pages: int = MAX_BATCH_PAGES) -> list[list[dict[str, Any]]]:
    """Consecutive pages packed into batches of at most `max_chars` and `max_pages` entries.

    Nothing is cut off. A page longer than `max_chars` becomes several entries with the
    same `page_id` plus `"part": i, "parts": n` (1-based), so the model knows it reads a
    fragment and the topics it finds still cite the real page.

    Batch boundaries are anchored to `page_order` (pages 1-4, 5-8, ...) rather than to a
    running count. Batch results are cached by their content, and with a running count a
    page corrected, excluded or read late in the middle would shift every later batch and
    re-read all of them; anchored, only its own batch changes."""

    max_chars, max_pages = max(1, int(max_chars)), max(1, int(max_pages))
    entries: list[tuple[int, dict[str, Any]]] = []
    for index, page in enumerate(pages):
        order = page.get("page_order")
        position = order if isinstance(order, int) and not isinstance(order, bool) and order > 0 else index + 1
        slot = (position - 1) // max_pages
        text = str(page.get("text") or "")
        parts = (_split_text(text, max_chars) or [""]) if len(text) > max_chars else [text]
        if len(parts) == 1:
            entries.append((slot, {**page, "text": parts[0]}))
            continue
        for number, part in enumerate(parts, start=1):
            entries.append((slot, {**page, "text": part, "part": number, "parts": len(parts)}))
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size, current_slot = 0, None
    for slot, entry in entries:
        length = len(entry["text"])
        if current and (slot != current_slot or size + length > max_chars or len(current) >= max_pages):
            batches.append(current)
            current, size = [], 0
        current.append(entry)
        size += length
        current_slot = slot
    if current:
        batches.append(current)
    return batches


def split_batch(batch: Sequence[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Halve a batch whose call failed, so each half can be retried on its own.

    Several entries are split between them; a single long entry is cut at the break
    nearest its middle (a whole page becomes part 1 and 2 of 2; a part that is already a
    fragment keeps its part numbers and gets `"half": 1|2`). The second list is empty when
    the batch cannot be halved any further."""

    entries = list(batch)
    if len(entries) >= 2:
        middle = (len(entries) + 1) // 2
        return entries[:middle], entries[middle:]
    if not entries:
        return [], []
    entry = entries[0]
    text = str(entry.get("text") or "").strip()
    if len(text) < MIN_SPLIT_CHARS:
        return entries, []
    cut = _break_point(text, len(text) // 4, (3 * len(text)) // 4, len(text) // 2)
    first, second = text[:cut].rstrip(), text[cut:].lstrip()
    if not first or not second:
        return entries, []
    if "part" in entry:
        return [{**entry, "text": first, "half": 1}], [{**entry, "text": second, "half": 2}]
    return ([{**entry, "text": first, "part": 1, "parts": 2}],
            [{**entry, "text": second, "part": 2, "parts": 2}])


# --------------------------------------------------------------------------- normalising

def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if not isinstance(value, (dict, list)) else ""


def _items(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, str):
            item = item.strip()
            if item:
                items.append(item[:1200])
        elif isinstance(item, dict) and item:
            items.append({str(k)[:60]: (v if isinstance(v, (int, float, bool)) else _text(v, 1200)) for k, v in item.items()})
        if len(items) >= limit:
            break
    return items


def _number(value: Any) -> float | None:
    """A finite number, or None. JSON from the model may hold NaN or Infinity (Python's
    parser accepts both), and int() of either raises: one odd field must not sink the batch."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else None
    text = str(value or "").strip()
    if not re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]+)?", text):
        return None
    return float(text)


def _level(value: Any, default: int = 2, words: dict[str, int] | None = None) -> int:
    number = _number(value)
    if number is not None:
        return max(1, min(3, int(number)))
    if isinstance(value, (bool, int, float)):
        return default
    return (words or DIFFICULTY_WORDS).get(str(value or "").strip().casefold(), default)


def _field_name(name: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _fold(name)).strip("_")


def _pick(record: dict[str, Any], names: Sequence[str], limit: int) -> str:
    """The first non-empty text among the given field names ("Erklärung" == "erklaerung")."""

    folded = {_field_name(key): value for key, value in record.items()}
    for name in names:
        text = _text(folded.get(name), limit)
        if text:
            return text
    return ""


def _page_ref(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, dict):              # [{"page_id": 201}]
        folded = {_field_name(key): item for key, item in value.items()}
        return next((_page_ref(folded[name]) for name in (*_PAGES, "id") if folded.get(name) is not None), None)
    numbers = re.findall(r"[0-9]+", str(value or ""))
    # "201", "page 201", "Seite 201"; "201-203" or "2/6" is not one page id.
    return int(numbers[0]) if len(numbers) == 1 and len(numbers[0]) <= 9 else None


def _page_refs(value: Any) -> list[int]:
    if isinstance(value, (list, tuple)):
        values = list(value)
    elif isinstance(value, str):
        values = re.findall(r"[0-9]+", value)
    else:
        values = [value]
    out = []
    for item in values:
        page_id = _page_ref(item)
        if page_id is not None and page_id not in out:
            out.append(page_id)
    return out


def _listed(value: Any) -> Any:
    """A single string where a list was asked for is a one-entry list."""

    return [value] if isinstance(value, str) else value


def _records(value: Any) -> list[Any]:
    """A list as the model meant it: {"Pflicht": "..."} mappings become one-entry dicts."""

    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [{key: item} for key, item in value.items()]
    if isinstance(value, str) and value.strip():
        return [value]
    return []


_QUOTE_CHARS = str.maketrans({"„": '"', "“": '"', "”": '"', "«": '"', "»": '"', "‚": "'", "‘": "'",
                              "’": "'", "–": "-", "—": "-", "\u00ad": None})


# Quotation marks and "..." around an excerpt are the model's, not the page's.
_QUOTE_EDGES = re.compile(r"^(?:[\s\"'„“”«»‚‘’…]|\.\.\.)+|(?:[\s\"'„“”«»‚‘’…]|\.\.\.)+$")


def _clean_quote(text: Any, limit: int = 1000) -> str:
    return _QUOTE_EDGES.sub("", _text(text, limit))


def _quote_form(text: Any) -> str:
    """Verbatim comparison form: case, whitespace and typographic quotes do not count."""

    text = _QUOTE_EDGES.sub("", unicodedata.normalize("NFC", str(text or "")))
    return " ".join(text.casefold().translate(_QUOTE_CHARS).split())


def _compact(text: Any) -> str:
    return "".join(_tokens(text))


# Scripts written without spaces between words: a term is a run of characters inside a
# longer run on the page, so only the compact form can find it.
_SPACELESS = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")
# LaTeX commands are notation, not words the page has to contain: "\frac{s}{t}" is "s/t".
_LATEX_COMMAND = re.compile(r"\\[A-Za-z]+")


@dataclass(frozen=True)
class _Page:
    id: int
    words: frozenset[str]
    compact: str
    quote_form: str


def _evidence(pages: Sequence[int], page_text: dict[int, str] | None) -> list[_Page] | None:
    if page_text is None:
        return None
    out = []
    for page_id in pages:
        text = _LATEX_COMMAND.sub(" ", page_text.get(page_id, "") or "")
        out.append(_Page(page_id, frozenset(_content(text)), _compact(text), _quote_form(page_text.get(page_id, ""))))
    return out


def _mentions(page: _Page, text: str, *, strict: bool) -> bool:
    """Does the page mention `text`? All content words (strict), or 80% of a longer text.

    Words in any script count ("Квадратное уравнение"), so a Russian or Greek class keeps
    its terms; LaTeX commands do not ("v = \\frac{s}{t}" is on a page that says "v = s/t")."""

    text = _LATEX_COMMAND.sub(" ", text)
    words = set(_content(text))
    compact = _compact(text)
    if not words:
        return len(compact) >= 3 and compact in page.compact
    if words <= page.words:
        return True
    # "Gedanken-Experiment" vs "Gedankenexperiment"; "范畴" inside a run of Chinese text.
    if compact in page.compact and (len(compact) >= 8 or (_SPACELESS.search(compact) and len(compact) >= 2)):
        return True
    if strict or len(words) < 3:
        return False
    return len(words & page.words) / len(words) >= 0.8


_TERM_SOURCES = ("terms", "fachbegriffe", "key_terms", "begriffe", "glossary", "definitions", "vocabulary")
_TERM_NAMES = ("term", "begriff", "fachbegriff", "name", "word", "wort", "headword", "title", "titel")
_TERM_DEFINITIONS = ("definition", "bedeutung", "erklaerung", "meaning", "explanation", "beschreibung",
                     "description", "detail", "erlaeuterung", "text", "content", "inhalt")
_TERM_SIMPLE = ("simple_definition", "einfache_definition", "simple", "einfach", "in_simple_words",
                "simple_explanation", "einfache_erklaerung")
_EXAMPLES = ("example", "beispiel")
_PAGES = ("page_id", "source_page_id", "page", "seite")
_QUOTES = ("quote", "source_text", "zitat", "excerpt", "textstelle")
# Field names: a one-entry dict keyed by one of these is a record, not {"Term": "definition"}.
_TERM_FIELDS = frozenset((*_TERM_NAMES, *_TERM_DEFINITIONS, *_TERM_SIMPLE, *_EXAMPLES, *_PAGES, *_QUOTES,
                          "kind", "type"))
# A verified excerpt supports a definition only when it says something beyond the term
# itself: "Pflicht ist" is on the page, but it backs no definition of "Pflicht".
_MIN_QUOTE_WORDS = 2
# "Term: definition", "Term – definition", "Term - definition"; not "10:30" or "3:2".
_SEPARATOR = re.compile(r"(?<![0-9])\s*:\s*|:\s+|\s+[–—-]\s+")
_DASH_SEPARATOR = re.compile(r"\s+[–—]\s+")
_MAX_TERM_WORDS = 8
# A definition written as a sentence: "Unter Pflicht versteht man ...", "Pflicht ist ...".
_DEFINITION_SENTENCES = (
    re.compile(r"^unter\s+(?P<term>.{1,60}?)\s+versteh(?:t|en)\s+man\s", re.IGNORECASE),
    re.compile(r"^(?P<term>.{1,60}?)\s+(?:ist|sind|bezeichnet|bedeutet|beschreibt|hei(?:ß|ss)t|meint|is|are|means"
               r"|refers\s+to|describes)\s+\S", re.IGNORECASE),
)


def _says_more(quote: str, term: str) -> bool:
    """Does the excerpt hold at least _MIN_QUOTE_WORDS content words besides the term's own?
    (In a script without spaces: at least four characters more than the term.)"""

    if _SPACELESS.search(quote):
        return len(_compact(quote)) - len(_compact(term)) >= 4
    return len(set(_content(quote)) - set(_content(term))) >= _MIN_QUOTE_WORDS


def _split_entry(text: str, separator: re.Pattern[str] = _SEPARATOR) -> tuple[str, str]:
    """ "Term: definition" / "Term – definition" / "Term - definition" -> (term, definition)."""

    parts = separator.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0].strip() and parts[1].strip():
        return parts[0].strip(), parts[1].strip()
    return text.strip(), ""


def _raw_term(entry: Any, *, sentences: bool = True) -> dict[str, Any] | None:
    if isinstance(entry, str):
        text = " ".join(entry.split())
        term, definition = _split_entry(text)
        # A vocabulary list holds phrases to learn ("What is your name?"), not definitions.
        if sentences and not definition and len(term.split()) >= 3:
            for pattern in _DEFINITION_SENTENCES:
                match = pattern.match(text)
                if match and len(match.group("term").split()) <= 4:
                    term, definition = match.group("term").strip(" ,;"), text
                    break
        record: dict[str, Any] = {"term": term, "definition": definition}
    elif isinstance(entry, dict) and entry:
        record = {"term": _pick(entry, _TERM_NAMES, 120), "definition": _pick(entry, _TERM_DEFINITIONS, 1200),
                  "simple_definition": _pick(entry, _TERM_SIMPLE, 600), "example": _pick(entry, _EXAMPLES, 600),
                  "quote": _pick(entry, _QUOTES, 1000)}
        folded = {_field_name(key): value for key, value in entry.items()}
        record["page_id"] = next((_page_ref(folded[name]) for name in _PAGES if folded.get(name) is not None), None)
        if not record["term"] and len(entry) == 1:
            key, value = next(iter(entry.items()))
            if _field_name(key) in _TERM_FIELDS:            # {"definition": "Pflicht ist ..."}
                return _raw_term(value, sentences=sentences) if isinstance(value, str) else None
            # {"Pflicht": "Notwendigkeit einer Handlung ..."}
            record["term"], record["definition"] = _text(key, 120), _text(value, 1200)
    else:
        return None
    term = _text(record.get("term"), 120)
    if not term or len(term.split()) > _MAX_TERM_WORDS:
        return None
    record["term"] = term
    return record


def _normalize_terms(raw: dict[str, Any], pages: list[int], evidence: list[_Page] | None) -> list[dict[str, Any]]:
    terms: dict[str, dict[str, Any]] = {}
    for source in _TERM_SOURCES:
        for entry in _records(raw.get(source)):
            record = _raw_term(entry, sentences=source != "vocabulary")
            if record is None:
                continue
            term = record["term"]
            definition = _text(record.get("definition"), 1200)
            quote = _clean_quote(record.get("quote"))
            wanted = record.get("page_id")
            if evidence is None:
                # Nothing to check against: kept, but never claimed to be from the notes.
                page_id = wanted if wanted in pages else None
                found, quoted, in_notes = ([page_id] if page_id is not None else []), None, False
            else:
                found = [page.id for page in evidence if _mentions(page, headword(term) or term, strict=True)]
                if not found:
                    continue            # not in the student's material: not a Fachbegriff of it
                quote_form = _quote_form(quote) if _says_more(quote, term) else ""
                quoted_on = [page.id for page in evidence
                             if quote_form and page.id in found and quote_form in page.quote_form]
                quoted = wanted if wanted in quoted_on else (quoted_on[0] if quoted_on else None)
                page_id = quoted if quoted is not None else (wanted if wanted in found else found[0])
                in_notes = True
                if quoted is not None and not definition:
                    definition = quote      # the notes' own sentence is the definition
            item = {
                "term": term, "definition": definition,
                "simple_definition": _text(record.get("simple_definition"), 600),
                "example": _text(record.get("example"), 600),
                "page_id": page_id, "page_ids": found,
                # An excerpt that is not on the page is a paraphrase: it is not shown as a quote.
                "quote": quote if quoted is not None else "",
                "in_notes": in_notes, "definition_from_notes": quoted is not None,
                "key": term_key(term),
            }
            if item["key"] in terms:
                _merge_term(terms[item["key"]], item)
            elif len(terms) < MAX_TERMS:
                terms[item["key"]] = item
    return list(terms.values())


def _merge_term(into: dict[str, Any], other: dict[str, Any]) -> None:
    """Fill `into` from a duplicate; a definition backed by a verified quote wins."""

    if other.get("definition_from_notes") and not into.get("definition_from_notes"):
        for name in ("definition", "simple_definition", "quote"):
            if other.get(name):
                into[name] = other[name]
        into["page_id"], into["definition_from_notes"] = other.get("page_id"), True
    for name in ("definition", "simple_definition", "example"):
        if not into.get(name) and other.get(name):
            into[name] = other[name]
    if into.get("page_id") is None:
        into["page_id"] = other.get("page_id")
    into["page_ids"] = list(dict.fromkeys([*(into.get("page_ids") or []), *(other.get("page_ids") or [])]))
    into["in_notes"] = bool(into.get("in_notes") or other.get("in_notes"))


_KIND_ALIASES = {
    "beispiel": "example", "beispiele": "example", "examples": "example",
    "versuch": "experiment", "versuche": "experiment", "experimente": "experiment", "experiments": "experiment",
    "theorie": "theory", "theorien": "theory", "theories": "theory",
    "people": "person", "persons": "person", "personen": "person", "persoenlichkeit": "person",
    "philosoph": "person", "philosophin": "person", "philosophen": "person", "philosopher": "person",
    "philosophers": "person",
    "argumente": "argument", "arguments": "argument",
    "formel": "formula", "formeln": "formula", "formulas": "formula", "formulae": "formula",
    "prozess": "process", "prozesse": "process", "processes": "process", "ablauf": "process",
    "diagramm": "diagram", "diagramme": "diagram", "diagrams": "diagram", "grafik": "diagram",
    "schaubild": "diagram", "karte": "diagram", "map": "diagram",
    "ereignis": "event", "ereignisse": "event", "events": "event",
    "datum": "date", "daten": "date", "jahr": "date", "jahreszahl": "date", "dates": "date",
    "regel": "rule", "regeln": "rule", "rules": "rule", "grammatik": "rule", "grammar": "rule",
    "gedankenexperiment": "thought_experiment", "gedankenexperimente": "thought_experiment",
    "thought_experiments": "thought_experiment", "gleichnis": "thought_experiment",
    "allegorie": "thought_experiment", "allegory": "thought_experiment", "parable": "thought_experiment",
    # A dilemma or parable to think through is retold like a thought experiment; a case or
    # scenario stays an example (the Ethics retelling covers examples too).
    "parabel": "thought_experiment", "gedankenspiel": "thought_experiment", "dilemma": "thought_experiment",
    "dilemmata": "thought_experiment", "dilemmas": "thought_experiment", "fallbeispiel": "example",
    "szenario": "example", "scenario": "example",
    "werk": "work", "werke": "work", "works": "work", "text": "work", "roman": "work", "gedicht": "work",
    "methode": "method", "methoden": "method", "methods": "method", "verfahren": "method",
    "quelle": "source", "quellen": "source", "sources": "source",
    "ort": "place", "orte": "place", "places": "place", "region": "place",
    "institutionen": "institution", "institutions": "institution", "organisation": "institution",
    "standpunkt": "position", "standpunkte": "position", "positionen": "position", "positions": "position",
    "begriff": "term", "begriffe": "term", "terms": "term",
    # Causes and consequences (history, geography) and sentence patterns (languages) are
    # kinds of their own, so they become their own flashcards.
    "ursache": "cause_effect", "ursachen": "cause_effect", "folge": "cause_effect", "folgen": "cause_effect",
    "ursachen_und_folgen": "cause_effect", "ursache_und_folge": "cause_effect", "ursache_und_wirkung": "cause_effect",
    "cause": "cause_effect", "causes": "cause_effect", "consequence": "cause_effect", "consequences": "cause_effect",
    "causes_and_consequences": "cause_effect", "cause_and_effect": "cause_effect",
    "satzbau": "structure", "satzmuster": "structure", "satzstruktur": "structure", "sentence_structure": "structure",
    "sentence_pattern": "structure", "sentence_patterns": "structure", "structures": "structure",
}
# Older answers listed items per kind at topic level ("examples": [...], "people": [...]).
_ITEM_LISTS = ("examples", "experiments", "theories", "people", "persons", "arguments", "formulas",
               "processes", "diagrams", "events", "dates", "thought_experiments", "methods", "rules",
               "works", "sources", "places", "institutions", "positions")
_ITEM_KIND_FIELDS = ("kind", "type", "art", "category", "kategorie", "typ")
_ITEM_NAMES = ("name", "title", "titel", "term", "begriff", "label", "formula", "formel", "expression",
               "person", "theory", "theorie", "event", "ereignis", "date", "datum", "work", "werk")
_ITEM_DETAILS = ("detail", "details", "description", "beschreibung", "explanation", "erklaerung", "meaning",
                 "bedeutung", "text", "content", "inhalt", "summary", "statement", "aussage", "what")
_ITEM_FIELDS = frozenset((*_ITEM_KIND_FIELDS, *_ITEM_NAMES, *_ITEM_DETAILS, *_PAGES))


def item_kind(value: Any, default: str = "example") -> str:
    """One of ITEM_KINDS for a German or English kind label; unknown labels -> default."""

    name = _field_name(value)
    if name in ITEM_KINDS:
        return name
    return _KIND_ALIASES.get(name, default)


def _name_and_detail(text: str, kind: str) -> tuple[str, str]:
    # "v = s : t" is one formula, not a name and a detail.
    name, detail = _split_entry(text, _DASH_SEPARATOR if kind == "formula" else _SEPARATOR)
    if len(name) > 200:         # a sentence, not a name: kept whole, as the old lists showed it
        return text, ""
    return name, detail


def _raw_item(entry: Any, kind: str | None) -> dict[str, Any] | None:
    if isinstance(entry, str):
        text = " ".join(entry.split())
        resolved = kind or "example"
        name, detail = _name_and_detail(text, resolved)
        label = item_kind(name, "") if detail and len(name.split()) <= 2 else ""
        if label:
            # "Beispiel: Lügen", "Versuch: Kerze im Glas": the label is the kind, not the
            # name; as a name, every "Beispiel: ..." would merge into one item.
            resolved = label if kind in (None, "example") else resolved
            name, detail = _name_and_detail(detail, resolved)
    elif isinstance(entry, dict) and entry:
        resolved = kind or item_kind(_pick(entry, _ITEM_KIND_FIELDS, 60))
        name, detail = _pick(entry, _ITEM_NAMES, 200), _pick(entry, _ITEM_DETAILS, 1200)
        if not name and len(entry) == 1:
            key, value = next(iter(entry.items()))
            if _field_name(key) not in _ITEM_FIELDS:    # {"Kant": "Philosoph der Aufklärung"}
                name, detail = _text(key, 200), _text(value, 1200)
        if not name and detail:
            name = " ".join(detail.split()[:8])[:200]
        folded = {_field_name(key): value for key, value in entry.items()}
        page_id = next((_page_ref(folded[field]) for field in _PAGES if folded.get(field) is not None), None)
        return {"kind": resolved, "name": name, "detail": detail, "page_id": page_id} if name else None
    else:
        return None
    return {"kind": resolved, "name": name[:1200], "detail": detail[:1200], "page_id": None} if name else None


def _normalize_items(raw: dict[str, Any], pages: list[int], evidence: list[_Page] | None) -> list[dict[str, Any]]:
    sources: list[tuple[Any, str | None]] = []
    listed = raw.get("items")
    if isinstance(listed, dict):              # {"theories": [...], "people": [...]}
        sources += [(entries, item_kind(kind)) for kind, entries in listed.items()]
    else:
        sources.append((listed, None))
    sources += [(raw.get(name), item_kind(name)) for name in _ITEM_LISTS]
    items: dict[tuple[str, str], dict[str, Any]] = {}
    for entries, kind in sources:
        for entry in entries if isinstance(entries, list) else []:
            record = _raw_item(entry, kind)
            if record is None:
                continue
            wanted = record["page_id"]
            if evidence is None:
                page_id, in_notes = (wanted if wanted in pages else None), False
            else:
                found = [page.id for page in evidence
                         if _mentions(page, record["name"], strict=len(_content(record["name"])) <= 3)
                         or (len(_content(record["detail"])) >= 3 and _mentions(page, record["detail"], strict=False))]
                in_notes = bool(found)
                if not in_notes and record["kind"] != "example":
                    continue        # a theory or person the notes never mention is not part of them
                page_id = (wanted if wanted in found else found[0]) if found else None
            marker = (record["kind"], " ".join(_tokens(record["name"])))
            item = {"kind": record["kind"], "name": record["name"], "detail": record["detail"],
                    "page_id": page_id, "in_notes": in_notes}
            if marker in items:
                _merge_item(items[marker], item)
            elif len(items) < MAX_ITEMS:
                items[marker] = item
    return list(items.values())


def _merge_item(into: dict[str, Any], other: dict[str, Any]) -> None:
    if len(other.get("detail") or "") > len(into.get("detail") or ""):
        into["detail"] = other["detail"]
    if into.get("page_id") is None:
        into["page_id"] = other.get("page_id")
    into["in_notes"] = bool(into.get("in_notes") or other.get("in_notes"))


def _strings(value: Any, names: Sequence[str], limit: int, size: int = 500) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for entry in _records(value):
        text = _pick(entry, names, size) if isinstance(entry, dict) else _text(entry, size)
        marker = _quote_form(text)
        if text and marker not in seen:
            seen.add(marker)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _first_sentence(text: str, limit: int) -> str:
    return re.split(r"(?<=[.!?…])\s+", text.strip(), maxsplit=1)[0][:limit] if text else ""


def _entry(name: str, detail: str) -> str:
    return f"{name}: {detail}" if detail else name


_TITLE_FIELDS = ("titel", "topic", "thema", "name", "heading", "ueberschrift", "topic_title")


def _format_prompt(template: str, term: str) -> str:
    try:
        return template.format(term=term)
    except (KeyError, IndexError, ValueError):
        return DEFAULT_RECALL_PROMPT.format(term=term)


_PAGE_FIELDS = ("source_page_ids", "page_ids", "pages")


def repair_page_refs(raw: Any, batch_page_ids: Sequence[int],
                     page_order: Mapping[int, int] | None = None) -> Any:
    """A topic of one batch answer whose page references name no page of that batch, repaired.

    Repair, not reject: the payload shows each page's `page_order` next to its
    `page_id`, and a model that cites the order numbers used to lose the topic silently
    (normalize_topic returns None for a topic without a real page). References that are
    the page_order of a batch page become that page's id; when the batch holds a single
    page, the topic can only come from it. Anything else is returned unchanged (and is
    then dropped by normalize_topic, which the caller counts)."""

    if not isinstance(raw, dict):
        return raw
    ids = [page_id for page_id in batch_page_ids if isinstance(page_id, int)]
    if not ids:
        return raw
    field = next((name for name in _PAGE_FIELDS if raw.get(name) is not None), "source_page_ids")
    refs = _page_refs(raw.get(field)) if raw.get(field) is not None else []
    if any(ref in ids for ref in refs):
        return raw
    by_order = {int(order): page_id for page_id, order in (page_order or {}).items()
                if page_id in ids and isinstance(order, int) and not isinstance(order, bool)}
    mapped = list(dict.fromkeys(by_order[ref] for ref in refs if ref in by_order))
    if not mapped and len(set(ids)) == 1:
        mapped = [ids[0]]
    if not mapped:
        return raw
    repaired = dict(raw)
    repaired[field] = mapped
    return repaired


def normalize_topic(raw: Any, valid_page_ids: set[int], *, page_text: dict[int, str] | None = None,
                    recall_prompt: str = DEFAULT_RECALL_PROMPT) -> dict[str, Any] | None:
    """One model topic, made safe; None when it cites no real page or has no title.

    Accepts the v4 topic-map shape (summary, terms, items, exam_questions, ...) and the
    older v3 shape (three explanations, definitions/vocabulary/examples lists), so cached
    batches and old projects keep working. Terms and items are checked against the
    topic's pages in `page_text`; without page_text nothing can be checked, so terms and
    items are kept but never marked `in_notes`. `recall_prompt` (already translated by
    the caller) is the front of the recall cards built from verified definitions."""

    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), 255) or _pick(raw, _TITLE_FIELDS, 255)
    pages: list[int] = []
    for name in ("source_page_ids", "page_ids", "pages"):
        if raw.get(name) is not None:
            pages = [page_id for page_id in _page_refs(raw.get(name)) if page_id in valid_page_ids]
            break
    if not title or not pages:
        return None
    summary = (_text(raw.get("summary"), 3000) or _text(raw.get("standard_explanation"), 3000)
               or _text(raw.get("simple_explanation"), 3000))
    simple_summary = (_text(raw.get("simple_summary"), 1200) or _text(raw.get("simple_explanation"), 1200)
                      or _first_sentence(summary, 1200))
    kind = _field_name(raw.get("kind"))
    topic: dict[str, Any] = {
        "title": title,
        "main_topic": _text(raw.get("main_topic"), 255) or title,
        "kind": "subtopic" if kind in ("subtopic", "sub", "sub_topic", "unterthema", "teilthema") else "main",
        "source_page_ids": pages,
        "summary": summary,
        "simple_summary": simple_summary,
        "simple_explanation": _text(raw.get("simple_explanation"), 6000) or simple_summary,
        "standard_explanation": _text(raw.get("standard_explanation"), 8000) or summary,
        "detailed_explanation": _text(raw.get("detailed_explanation"), 12000) or summary,
        "difficulty": _level(raw.get("difficulty")),
        "importance": _level(raw.get("importance"), words=IMPORTANCE_WORDS),
        "prerequisites": _strings(raw.get("prerequisites"), ("title", "titel", "name", "topic", "thema"), 6, 255),
    }
    minutes = _number(raw.get("estimated_minutes"))
    topic["estimated_minutes"] = max(5, min(45, int(minutes or 15)))
    evidence = _evidence(pages, page_text)
    terms = _normalize_terms(raw, pages, evidence)
    items = _normalize_items(raw, pages, evidence)
    topic["terms"], topic["items"] = terms, items
    topic["exam_questions"] = _strings(
        [*_records(raw.get("exam_questions")), *_records(raw.get("likely_exam_questions"))],
        ("question", "frage", "prompt", "text"), MAX_EXAM_QUESTIONS)
    own_key = topic_key(title, topic["main_topic"])
    topic["related_topics"] = [related for related in _strings(
        raw.get("related_topics"), ("title", "titel", "name", "topic", "thema"), MAX_RELATED + 1, 255)
        if topic_key(related) != own_key][:MAX_RELATED]
    topic["related"] = []
    # The old columns and the Notes page read these lists; they are filled from the map.
    topic["learning_goals"] = _items(_listed(raw.get("learning_goals")), LIST_LIMITS["learning_goals"])
    facts = _items(_listed(raw.get("important_facts")), LIST_LIMITS["important_facts"])
    topic["important_facts"] = facts or [_entry(item["name"], item["detail"]) for item in items
                                         if item["kind"] in ("theory", "argument", "process") and item["detail"]
                                         ][:LIST_LIMITS["important_facts"]]
    topic["definitions"] = [_entry(term["term"], term["definition"]) for term in terms
                            if term["definition"]][:LIST_LIMITS["definitions"]]
    topic["formulas"] = [_entry(item["name"], item["detail"]) for item in items
                         if item["kind"] == "formula"][:LIST_LIMITS["formulas"]]
    # The old Notes page prints this list with no marker, so an example Learnova added
    # (checked, and not in the notes) stays out of it; `items` keeps it, marked in_notes False.
    topic["examples"] = [_entry(item["name"], item["detail"]) for item in items
                         if item["kind"] in ("example", "experiment") and (item["in_notes"] or evidence is None)
                         ][:LIST_LIMITS["examples"]]
    topic["vocabulary"] = [term["term"] for term in terms][:LIST_LIMITS["vocabulary"]]
    topic["relationships"] = _merge_lists(topic["related_topics"], _items(raw.get("relationships"), 15),
                                          LIST_LIMITS["relationships"])
    topic["likely_exam_questions"] = topic["exam_questions"][:LIST_LIMITS["likely_exam_questions"]]
    # A recall card is kept only when its quote is really on one of its pages: the model
    # must not be able to put words in the teacher's mouth.
    source = _quote_form("\n".join((page_text or {}).get(page_id) or "" for page_id in pages))
    cards = []
    for card in _records(raw.get("recall_cards")):
        if not isinstance(card, dict):
            continue
        prompt, answer = _text(card.get("prompt"), 1000), _text(card.get("answer"), 1000)
        quote = _text(card.get("source_text"), 1000)
        # "..." or "“”" is not an excerpt; it would pass the substring check on any page.
        if not prompt or not answer or not re.search(r"\w", quote):
            continue
        if page_text is not None and _quote_form(quote) not in source:
            continue
        cards.append({"kind": _text(card.get("kind"), 40) or "recall", "prompt": prompt, "answer": answer, "source_text": quote})
    # A definition whose excerpt was found on the page is a card without another AI call.
    cards += [{"kind": "definition", "prompt": _text(_format_prompt(recall_prompt, term["term"]), 1000),
               "answer": term["definition"], "source_text": term["quote"]}
              for term in terms if term["definition_from_notes"] and term["definition"]]
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for card in cards:
        unique.setdefault((card["prompt"].casefold(), card["answer"].casefold()), card)
    topic["recall_cards"] = list(unique.values())[:MAX_RECALL_CARDS]
    topic["key"] = own_key
    return topic


# --------------------------------------------------------------------------- merging

def _merge_lists(first: list[Any], second: list[Any], limit: int) -> list[Any]:
    seen, out = set(), []
    for item in first + second:
        marker = " ".join(str(item).casefold().split())
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out[:limit]


def _merge_terms(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for term in [*first, *second]:
        key = term.get("key") or term_key(term.get("term"))
        if key in merged:
            _merge_term(merged[key], term)
        elif len(merged) < MAX_TERMS:
            merged[key] = {**term, "page_ids": list(term.get("page_ids") or [])}
    return list(merged.values())


def _merge_items(first: list[dict[str, Any]], second: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for item in [*first, *second]:
        marker = (str(item.get("kind")), " ".join(_tokens(item.get("name"))))
        if marker in merged:
            _merge_item(merged[marker], item)
        elif len(merged) < MAX_ITEMS:
            merged[marker] = dict(item)
    return list(merged.values())


def _absorb(into: dict[str, Any], other: dict[str, Any]) -> None:
    into["source_page_ids"] = sorted(set(into["source_page_ids"]) | set(other["source_page_ids"]),
                                     key=lambda page: (into["_order"].get(page, 0), page))
    for name in LIST_FIELDS:
        into[name] = _merge_lists(into.get(name) or [], other.get(name) or [], LIST_LIMITS[name])
    for name in ("simple_explanation", "standard_explanation", "detailed_explanation", "summary", "simple_summary"):
        if len(other.get(name) or "") > len(into.get(name) or ""):
            into[name] = other[name]
    into["terms"] = _merge_terms(into.get("terms") or [], other.get("terms") or [])
    into["items"] = _merge_items(into.get("items") or [], other.get("items") or [])
    into["exam_questions"] = _merge_lists(into.get("exam_questions") or [], other.get("exam_questions") or [],
                                          MAX_EXAM_QUESTIONS)
    into["related_topics"] = _merge_lists(into.get("related_topics") or [], other.get("related_topics") or [],
                                          MAX_RELATED)
    if "main" in (into.get("kind"), other.get("kind")):
        into["kind"] = "main"
    into["recall_cards"] = _merge_lists(into["recall_cards"], other["recall_cards"], MAX_RECALL_CARDS)
    into["prerequisites"] = _merge_lists(into["prerequisites"], other["prerequisites"], 6)
    into["estimated_minutes"] = min(45, max(into["estimated_minutes"], other["estimated_minutes"]) + 5)
    into["difficulty"] = max(into["difficulty"], other["difficulty"])
    into["importance"] = max(into["importance"], other["importance"])


def _member_keys(topic: dict[str, Any]) -> list[str]:
    keys = topic.get("_keys")
    if keys:
        return list(keys)
    return list(dict.fromkeys([topic["key"], topic_key(topic.get("title") or "", topic.get("main_topic") or "")]))


def _resolve(titles: Iterable[str], topics: Sequence[dict[str, Any]], own_key: str, limit: int) -> list[str]:
    """Titles mapped to the keys of these topics; a title that is none of them is dropped."""

    members = [(topic["key"], _member_keys(topic)) for topic in topics]
    resolved: list[str] = []
    for title in titles:
        wanted = topic_key(title)
        key = next((key for key, keys in members if wanted in keys), None) or next(
            (key for key, keys in members if any(same_topic(member, wanted) for member in keys)), None)
        if key and key != own_key and key not in resolved:
            resolved.append(key)
    return resolved[:limit]


def resolve_related(topics: Sequence[dict[str, Any]]) -> None:
    """Set each topic's `related` to the keys of its `related_topics` titles (in place)."""

    for topic in topics:
        topic["related"] = _resolve(topic.get("related_topics") or [], topics, topic["key"], MAX_RELATED)


def merge_topics(topics: Iterable[dict[str, Any]], *, page_order: dict[int, int]) -> list[dict[str, Any]]:
    """Merge duplicates: the same key, or keys `same_topic` accepts for every topic merged so far.

    Requiring a match with every member (not just one) stops chains: "Utilitarismus nach
    Bentham" and "... nach Mill" stay apart even when a topic "Bentham und Mill" would
    match each of them. Order is the order of the first page each topic appears on, so
    the list follows the notes. Prerequisite titles are mapped to the merged topics' keys
    and `related` is resolved; a title that is not one of the topics is dropped (it is not
    evidence about this material). Nothing is capped here: see `cap_topics`."""

    merged: list[dict[str, Any]] = []
    for arrival, topic in enumerate(topics):
        topic = {**topic, "_order": page_order, "_arrival": arrival, "_keys": [topic["key"]]}
        match = next((group for group in merged if topic["key"] in group["_keys"]), None) or next(
            (group for group in merged if all(same_topic(key, topic["key"]) for key in group["_keys"])), None)
        if match:
            _absorb(match, topic)
            if topic["key"] not in match["_keys"]:
                match["_keys"].append(topic["key"])
        else:
            merged.append(topic)
    # Note order: the first page a topic appears on, then the order the notes present
    # topics that start on the same page.
    merged.sort(key=lambda item: (min(page_order.get(page, 0) for page in item["source_page_ids"]), item["_arrival"]))
    for position, topic in enumerate(merged, start=1):
        topic["position"] = position
        topic["prerequisites"] = _resolve(topic["prerequisites"], merged, topic["key"], 6)
    resolve_related(merged)
    for topic in merged:
        for name in ("_order", "_arrival", "_keys"):
            topic.pop(name, None)
    return merged


def cap_topics(topics: Sequence[dict[str, Any]], limit: int = MAX_TOPICS) -> tuple[list[dict[str, Any]], int]:
    """At most `limit` merged topics, and how many were dropped (the app reports it).

    The least important go first (then subtopics before main topics, then the ones on the
    fewest pages); the kept ones stay in note order. Prerequisites and related keys that
    pointed at a dropped topic are removed."""

    topics = list(topics)
    limit = max(0, int(limit))
    if len(topics) <= limit:
        return topics, 0
    ranked = sorted(range(len(topics)), key=lambda index: (
        -int(topics[index].get("importance") or 2), topics[index].get("kind") == "subtopic",
        -len(topics[index].get("source_page_ids") or []), index))
    kept = [dict(topics[index]) for index in sorted(ranked[:limit])]
    keys = {topic["key"] for topic in kept}
    for position, topic in enumerate(kept, start=1):
        topic["position"] = position
        topic["prerequisites"] = [key for key in topic.get("prerequisites") or [] if key in keys]
        topic["related"] = [key for key in topic.get("related") or [] if key in keys]
    return kept, len(topics) - len(kept)


def overlap_suggestions(topics: Sequence[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    """Pairs of (unmerged) topics that may be one topic, for the student to decide.

    A pair qualifies by a similar title (key Jaccard >= 0.34), by one title's key words all
    being in the other's ("Utilitarismus" and "Utilitarismus – Folgenorientierte Ethik":
    Jaccard 1/3, so this pair and "Höhlengleichnis" / "Platons Höhlengleichnis –
    Erkenntnistheorie" were missed in the live run of 2026-11-01, D6), by 3+ shared
    Fachbegriffe, or by sharing half the pages of the smaller topic plus at least one term.
    The score only orders the list: title similarity (+ 0.2 for a contained title) + 0.1
    per shared term (max 5) + half the page share when a term is shared too. A one-word
    title inside a longer one that is a different topic ("Utilitarismus" / "Vergleich von
    Utilitarismus und Pflichtethik") may be suggested too; the student decides."""

    topics = list(topics)
    found: list[tuple[float, int, dict[str, Any]]] = []
    for first_index, first in enumerate(topics):
        first_words = set(first["key"].split("-"))
        first_terms = {term.get("key") or term_key(term.get("term")): term.get("term") for term in first.get("terms") or []}
        first_pages = list(first.get("source_page_ids") or [])
        for second in topics[first_index + 1:]:
            second_words = set(second["key"].split("-"))
            second_terms = {term.get("key") or term_key(term.get("term")) for term in second.get("terms") or []}
            second_pages = set(second.get("source_page_ids") or [])
            similarity = len(first_words & second_words) / max(1, len(first_words | second_words))
            shared_terms = [term for key, term in first_terms.items() if key in second_terms]
            shared_pages = [page for page in first_pages if page in second_pages]
            smaller = min(len(set(first_pages)), len(second_pages))
            page_share = len(shared_pages) / smaller if smaller else 0.0
            first_set, second_set = first_words - {""}, second_words - {""}
            contained = bool(first_set) and bool(second_set) and (
                first_set <= second_set or second_set <= first_set)
            reasons = []
            if similarity >= 0.34:
                reasons.append("similar_title")
            if contained:
                reasons.append("contained_title")
            if shared_terms and page_share >= 0.5:
                reasons.append("shared_pages")
            if len(shared_terms) >= 3:
                reasons.append("shared_terms")
            if not reasons:
                continue
            score = (similarity + (0.2 if contained else 0.0) + 0.1 * min(len(shared_terms), 5)
                     + (0.5 * page_share if shared_terms else 0.0))
            found.append((round(score, 3), len(found), {
                "a": first["key"], "b": second["key"], "reasons": reasons,
                "shared_terms": shared_terms[:5], "shared_pages": shared_pages, "score": round(score, 3),
            }))
    found.sort(key=lambda entry: (-entry[0], entry[1]))
    return [entry[2] for entry in found[:limit]]


def uncovered_pages(content_page_ids: Iterable[int], topics: Iterable[dict[str, Any]]) -> list[int]:
    """Content pages no topic cites, in the order given."""

    covered = {page for topic in topics for page in topic.get("source_page_ids") or []}
    return list(dict.fromkeys(page for page in content_page_ids if page not in covered))


# --------------------------------------------------------------------------- reconciling

@dataclass(frozen=True)
class StoredTopic:
    id: int
    key: str
    title: str
    user_edited: bool = False      # renamed, merged, or added by the student
    has_progress: bool = False     # lessons, attempts or recall answers exist


@dataclass
class Reconciliation:
    update: list[tuple[int, dict[str, Any]]] = field(default_factory=list)   # (stored id, new content)
    create: list[dict[str, Any]] = field(default_factory=list)
    keep: list[int] = field(default_factory=list)       # not in the new scan, but kept
    delete: list[int] = field(default_factory=list)


def reconcile(stored: Iterable[StoredTopic], extracted: Iterable[dict[str, Any]]) -> Reconciliation:
    """Match a new extraction against the stored topics without losing anything.

    Matching runs in passes from strong to weak, so a weak match can never take a stored
    topic that another extracted topic matches exactly: the same key; then the stored
    title's key; then, for a topic the student renamed, the key the first key version
    would have given the new title (projects analysed before digits and "nach" counted);
    then `same_topic`.

    The stored title is the identity whenever the stored key was computed from it: the
    student did not rename the topic, or typed it in. A stored key that only the first
    key function explains (it ignored digits: "Text 1" was stored as "text") is not
    compared as a current key, or an extracted "Text 2" or "Text" would take the stored
    "Text 1". Only a renamed topic keeps a key its title no longer explains, and only
    then is that key compared with what the first key function makes of the new title."""

    stored = list(stored)
    extracted = list(extracted)
    claims: dict[int, int] = {}
    claimed: set[int] = set()

    title_keys: dict[int, str] = {}
    live_keys: dict[int, str] = {}
    for item in stored:
        own, legacy = topic_key(item.title), _legacy_key(item.title)
        derived = item.key in (own, legacy)
        title_keys[item.id] = own if not item.user_edited or derived else ""
        live_keys[item.id] = "" if item.key == legacy != own else item.key

    def title_key(item: StoredTopic) -> str:
        return title_keys.get(item.id, "")

    def live_key(item: StoredTopic) -> str:
        return live_keys.get(item.id, "")

    passes: list[Callable[[StoredTopic, dict[str, Any]], bool]] = [
        lambda item, topic: live_key(item) == topic["key"],
        lambda item, topic: title_key(item) == topic["key"],
        lambda item, topic: not title_key(item)
        and item.key == _legacy_key(topic["title"], topic.get("main_topic") or ""),
        lambda item, topic: same_topic(live_key(item), topic["key"]) or same_topic(title_key(item), topic["key"]),
    ]
    for matches in passes:
        for index, topic in enumerate(extracted):
            if index in claims:
                continue
            match = next((item for item in stored if item.id not in claimed and matches(item, topic)), None)
            if match:
                claims[index] = match.id
                claimed.add(match.id)
    result = Reconciliation()
    for index, topic in enumerate(extracted):
        if index in claims:
            result.update.append((claims[index], topic))
        else:
            result.create.append(topic)
    for item in stored:
        if item.id in claimed:
            continue
        if item.user_edited or item.has_progress:
            result.keep.append(item.id)
        else:
            result.delete.append(item.id)
    return result

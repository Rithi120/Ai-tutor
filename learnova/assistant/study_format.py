"""Short Lessons or Project Mode: the format decision for exam material in the chat (spec 1.2).

Flask-free and deterministic. The assistant reports the separate topics of exam material
in a hidden trailer line (``EXAM: topic | topic | ...``, presets.TUTORING_LAYER); this
module turns that line, the amount of material the student sent, what Learnova already
knows about each topic and the exam date into one decision:

* ``lessons``  - a few manageable topics: one independent Short Lesson per topic;
* ``project``  - many topics or a large/difficult volume: Project Mode through the existing
  creation path (all topics, schedule, lessons, practice, revision, mock exam);
* ``choice``   - in between: both, the recommended one first;
* ``none``     - not exam material with two or more topics: the chat keeps its single
  Continue Learning button.

The thresholds are named constants (owner decision 2026-11-13) and parameters of
`decide_format`, so a test or a later tuning never has to edit the rules themselves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

from learnova.projects.topics import topic_key

# --- thresholds (owner decision 2026-11-13) ------------------------------------------
SMALL_MAX_TOPICS = 3          # <= this many manageable topics: separate Short Lessons
LARGE_MIN_TOPICS = 10         # >= this many topics: Project Mode
EXAM_SOON_DAYS = 2            # an exam within this many days ...
EXAM_SOON_MAX_TOPICS = 6      # ... with at most this many topics: recommend lessons
EXAM_FAR_DAYS = 7             # an exam at least this many days away ...
EXAM_FAR_MIN_TOPICS = 6       # ... with at least this many topics: recommend a project
# A large or difficult volume makes even a few topics a project.
HEAVY_PHOTOS = 6
HEAVY_READING_CHARACTERS = 12000
HEAVY_MINUTES = 180
# In a choice without an exam-date rule: more learning time than this recommends a project.
LONG_MINUTES = 90
# Estimated learning time of one topic by what Learnova knows of it (15 min matches a typed
# project topic, add_manual_topics), plus time for each subtopic beyond the first two.
MINUTES_BY_STATE = {"new": 20, "developing": 15, "secure": 8}
MINUTES_PER_EXTRA_SUBTOPIC = 5
MAX_EXAM_TOPICS = 20
MAX_TOPIC_CHARACTERS = 120
MAX_SUBTOPICS = 8
# How far ahead a date found in the chat may be to count as the exam date.
MAX_EXAM_DAYS_AHEAD = 180

KINDS = ("none", "lessons", "project", "choice")
PROJECT_KEY = "__project__"   # the conversation_lesson row of a chat-made project


@dataclass(frozen=True)
class ExamTopic:
    title: str
    key: str
    subtopics: tuple[str, ...] = ()


@dataclass(frozen=True)
class MaterialSignals:
    topics: tuple[ExamTopic, ...]
    photo_count: int = 0
    reading_chars: int = 0
    exam_date: date | None = None
    today: date | None = None
    # topic key -> "new" | "developing" | "secure" (lesson_plan.knowledge_state), from the
    # student's ConceptMastery; a topic without an entry counts as new.
    knowledge: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class FormatDecision:
    kind: str
    recommended: str = ""
    topics: tuple[ExamTopic, ...] = ()
    estimated_minutes: int = 0
    reasons: tuple[str, ...] = ()
    days_left: int | None = None
    linked_topics: int = 0


# --------------------------------------------------------------------------- trailer
_SUBTOPIC_SPLIT = re.compile(r"\s*[,;/]\s*")
_PARENTHESES = re.compile(r"^(.*?)\s*[(\[](.*)[)\]]\s*$")


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split()).strip(" .,;:-–—\"'“”„*_`")


def _split_topic(raw: str) -> tuple[str, tuple[str, ...]]:
    """'Lineare Funktionen (Steigung, y-Achsenabschnitt)' -> title + subtopics."""

    text = _one_line(raw)
    match = _PARENTHESES.match(text)
    if match and _one_line(match.group(1)):
        title = _one_line(match.group(1))
        subs = tuple(item for item in (_one_line(part) for part in _SUBTOPIC_SPLIT.split(match.group(2))) if item)
        return title, subs[:MAX_SUBTOPICS]
    return text, ()


def parse_exam_topics(value: str | Iterable[str]) -> list[ExamTopic]:
    """The topics of an EXAM trailer value (or a list of titles), deduplicated by topic key.

    Each topic is trimmed to MAX_TOPIC_CHARACTERS; subtopics in brackets are kept apart
    ("Brüche (kürzen, erweitern)"); "none" and empty pieces are dropped; at most
    MAX_EXAM_TOPICS topics."""

    pieces = value.split("|") if isinstance(value, str) else list(value or ())
    topics: list[ExamTopic] = []
    seen: set[str] = set()
    for piece in pieces:
        title, subs = _split_topic(str(piece or ""))
        if not title or title.casefold() in {"none", "keins", "keine", "n/a", "-"}:
            continue
        title = title[:MAX_TOPIC_CHARACTERS].rstrip()
        key = topic_key(title)
        if key in seen:
            continue
        seen.add(key)
        topics.append(ExamTopic(title=title, key=key, subtopics=subs))
        if len(topics) >= MAX_EXAM_TOPICS:
            break
    return topics


def topics_to_json(topics: Sequence[ExamTopic]) -> list[dict[str, object]]:
    return [{"title": item.title, "key": item.key, "subtopics": list(item.subtopics)} for item in topics]


def topics_from_json(raw: object) -> list[ExamTopic]:
    """Stored topics back (study_json), tolerant of anything malformed."""

    topics: list[ExamTopic] = []
    seen: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, Mapping):
            continue
        title = _one_line(entry.get("title"))[:MAX_TOPIC_CHARACTERS]
        if not title:
            continue
        key = str(entry.get("key") or "") or topic_key(title)
        if key in seen:
            continue
        seen.add(key)
        subs = entry.get("subtopics")
        topics.append(ExamTopic(title=title, key=key[:160], subtopics=tuple(
            _one_line(item)[:MAX_TOPIC_CHARACTERS] for item in (subs if isinstance(subs, list) else [])
            if _one_line(item))[:MAX_SUBTOPICS]))
        if len(topics) >= MAX_EXAM_TOPICS:
            break
    return topics


# --------------------------------------------------------------------------- exam date
# German compounds count too ("Deutscharbeit", "Mathetest", "Englischklausur",
# "Lernkontrolle"), but not the work forms that are no test ("Hausarbeit", "Gruppenarbeit").
_EXAM_WORDS = re.compile(
    r"\b(?:(?!(?:haus|gruppen|partner|einzel|projekt|still|frei|team|fach)arbeit)\w*"
    r"(?:arbeit|klausur|pr[üu]fung|test|kontrolle|schulaufgabe)|ex|abfrage|"
    r"exam|quiz|examen|contr[ôo]le|prueba)\b", re.IGNORECASE)
_ISO = re.compile(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b")
_DOTTED = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.(?:\s?(\d{4}|\d{2})\b)?")
# "Kapitel 3.4.", "Aufgabe 2.3." or "1.2.3." is a section number, never a date.
_SECTION_BEFORE = re.compile(
    r"(?:kapitel|aufgaben?|seiten?|s\.|nr\.|abschnitt|lektion|übung|uebung|unit|chapter|section|pages?|p\.|"
    r"exercises?|§)\s*$|[\d.]$", re.IGNORECASE)
_SECTION_JOIN = re.compile(r"^\s*(?:,|;|&|und|bis|sowie|oder|and|to|or|-|–)?\s*$", re.IGNORECASE)
_IN_DAYS = re.compile(r"\bin\s+(\d{1,3})\s+(tagen|tage|days?)\b", re.IGNORECASE)
_IN_WEEKS = re.compile(r"\bin\s+(\d{1,2}|einer|zwei|drei|one|two|three|a)\s+(wochen?|weeks?)\b", re.IGNORECASE)
_NUMBER_WORDS = {"einer": 1, "a": 1, "one": 1, "zwei": 2, "two": 2, "drei": 3, "three": 3}
_RELATIVE = (
    (re.compile(r"\b(übermorgen|uebermorgen|day after tomorrow)\b", re.IGNORECASE), 2),
    # Not the greeting "Guten Morgen", nor "heute Morgen" / "am Morgen" (in the morning).
    (re.compile(r"(?<!guten\s)(?<!heute\s)(?<!am\s)\b(morgen|tomorrow)\b", re.IGNORECASE), 1),
    (re.compile(r"\b(heute|today)\b", re.IGNORECASE), 0),
    (re.compile(r"\b(nächste woche|naechste woche|next week)\b", re.IGNORECASE), 7),
)


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def exam_date_from_text(text: str, today: date) -> date | None:
    """The exam date a student's message states, or None.

    Only a message that names a test or an exam is read, and only a date from today up to
    MAX_EXAM_DAYS_AHEAD days ahead counts (a historical date in the material never does).
    Understood: 2026-12-01, 1.12.2026, 1.12.26, 1.12. (the next such day), morgen/tomorrow,
    übermorgen, heute, in 3 Tagen / in 3 days, in zwei Wochen / in 2 weeks, nächste Woche."""

    text = str(text or "")
    if not _EXAM_WORDS.search(text):
        return None
    latest = today + timedelta(days=MAX_EXAM_DAYS_AHEAD)

    def ok(value: date | None) -> date | None:
        return value if value is not None and today <= value <= latest else None

    for match in _ISO.finditer(text):
        found = ok(_valid(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        if found:
            return found
    section_end = -1
    for match in _DOTTED.finditer(text):
        if (_SECTION_BEFORE.search(text[max(0, match.start() - 16):match.start()])
                or text[match.end():match.end() + 1].isdigit()
                # "Kapitel 3.4. und 5.2." / "Aufgabe 2.3. bis 4.5.": the list goes on.
                or (section_end >= 0 and _SECTION_JOIN.match(text[section_end:match.start()]))):
            section_end = match.end()
            continue
        day, month, year = int(match.group(1)), int(match.group(2)), match.group(3)
        if year:
            number = int(year)
            found = ok(_valid(number + 2000 if number < 100 else number, month, day))
        else:
            found = ok(_valid(today.year, month, day)) or ok(_valid(today.year + 1, month, day))
        if found:
            return found
    match = _IN_DAYS.search(text)
    if match:
        return ok(today + timedelta(days=int(match.group(1))))
    match = _IN_WEEKS.search(text)
    if match:
        raw = match.group(1).casefold()
        weeks = int(raw) if raw.isdigit() else _NUMBER_WORDS.get(raw, 1)
        return ok(today + timedelta(days=7 * weeks))
    for pattern, days in _RELATIVE:
        if pattern.search(text):
            return ok(today + timedelta(days=days))
    return None


_LIST_SPLIT = re.compile(r"\s*(?:\n|;|,|\u2022|(?:^|\s)[-*]\s)\s*")
_LABEL_PREFIX = re.compile(r"^[^:\n]{0,60}:\s*")


def goal_topics(text: str) -> list[ExamTopic]:
    """The topics a typed study goal lists for a test ("Klassenarbeit: Brüche, Prozente,
    Zinsen"), or [] when it names no test or exam. Only short pieces count as topics."""

    text = str(text or "")
    if not _EXAM_WORDS.search(text):
        return []
    body = "\n".join(_LABEL_PREFIX.sub("", line, count=1) for line in text.splitlines())
    pieces = [piece for piece in _LIST_SPLIT.split(body) if 0 < len(piece.split()) <= 8]
    return parse_exam_topics(pieces) if len(pieces) >= 2 else []


def material_hint(*, headings: Sequence[str], goal: str, photo_count: int, reading_chars: int,
                  exam_date: date | None = None, today: date | None = None) -> FormatDecision | None:
    """For the Short Lesson builder: the decision for what was sent, when Project Mode is
    the better format (a project, or a choice recommending one); None otherwise. Topics
    are the typed goal's list, else the distinct headings the photos show."""

    topics = goal_topics(goal) or parse_exam_topics(list(headings))
    decision = decide_format(MaterialSignals(topics=tuple(topics), photo_count=photo_count,
                                             reading_chars=reading_chars, exam_date=exam_date, today=today))
    if decision.kind == "project" or (decision.kind == "choice" and decision.recommended == "project"):
        return decision
    return None


# --------------------------------------------------------------------------- decision
def topic_minutes(topic: ExamTopic, state: str = "new") -> int:
    base = MINUTES_BY_STATE.get(state, MINUTES_BY_STATE["new"])
    return base + MINUTES_PER_EXTRA_SUBTOPIC * max(0, len(topic.subtopics) - 2)


def linked_topic_count(topics: Sequence[ExamTopic]) -> int:
    """Topics that share a content word with another topic ("Lineare Funktionen" and
    "Quadratische Funktionen"): they build on each other, which a project orders and
    separate lessons do not."""

    words = [set(item.key.split("-")) - {"topic", ""} for item in topics]
    linked = 0
    for index, own in enumerate(words):
        if own and any(own & other for position, other in enumerate(words) if position != index):
            linked += 1
    return linked


def decide_format(signals: MaterialSignals, *, small_max: int = SMALL_MAX_TOPICS,
                  large_min: int = LARGE_MIN_TOPICS, soon_days: int = EXAM_SOON_DAYS,
                  soon_max_topics: int = EXAM_SOON_MAX_TOPICS, far_days: int = EXAM_FAR_DAYS,
                  far_min_topics: int = EXAM_FAR_MIN_TOPICS, heavy_photos: int = HEAVY_PHOTOS,
                  heavy_characters: int = HEAVY_READING_CHARACTERS, heavy_minutes: int = HEAVY_MINUTES,
                  long_minutes: int = LONG_MINUTES) -> FormatDecision:
    """Lessons, project, a choice, or none - with the reasons, for the chat card.

    Rules, in order: no topics -> none; at least `large_min` topics -> project
    (many_topics); a large volume (more than `heavy_photos` photos, `heavy_characters` of
    reading, or `heavy_minutes` of estimated learning time) -> project (large_volume); one
    topic -> none (the plain Continue Learning lesson is that lesson); at most `small_max`
    topics -> lessons (few_topics). In between -> choice, recommending lessons for an exam
    within `soon_days` days with at most `soon_max_topics` topics (exam_soon), a project
    for an exam at least `far_days` away with at least `far_min_topics` topics (exam_far),
    otherwise a project when most topics build on each other (linked_topics) or the
    estimated time exceeds `long_minutes` (long), else lessons (short)."""

    topics = tuple(signals.topics)
    today = signals.today or date.today()
    days_left = (signals.exam_date - today).days if signals.exam_date else None
    if not topics:
        return FormatDecision("none", days_left=days_left)
    knowledge = signals.knowledge or {}
    minutes = sum(topic_minutes(item, knowledge.get(item.key, "new")) for item in topics)
    linked = linked_topic_count(topics)
    count = len(topics)

    def decision(kind: str, recommended: str, *reasons: str) -> FormatDecision:
        return FormatDecision(kind, recommended, topics, minutes, tuple(reasons), days_left, linked)

    heavy = []
    if int(signals.photo_count or 0) > heavy_photos:
        heavy.append("many_photos")
    if int(signals.reading_chars or 0) > heavy_characters:
        heavy.append("much_material")
    if minutes > heavy_minutes:
        heavy.append("long")
    if count >= large_min:
        return decision("project", "project", "many_topics", *heavy)
    if heavy:
        return decision("project", "project", "large_volume", *heavy)
    if count < 2:
        return FormatDecision("none", days_left=days_left, estimated_minutes=minutes, topics=topics)
    if count <= small_max:
        return decision("lessons", "lessons", "few_topics")
    if days_left is not None and days_left <= soon_days and count <= soon_max_topics:
        return decision("choice", "lessons", "exam_soon")
    if days_left is not None and days_left >= far_days and count >= far_min_topics:
        return decision("choice", "project", "exam_far")
    if linked * 2 > count:
        return decision("choice", "project", "linked_topics")
    if minutes > long_minutes:
        return decision("choice", "project", "long")
    return decision("choice", "lessons", "short")


__all__ = [
    "EXAM_FAR_DAYS", "EXAM_FAR_MIN_TOPICS", "EXAM_SOON_DAYS", "EXAM_SOON_MAX_TOPICS", "HEAVY_MINUTES",
    "HEAVY_PHOTOS", "HEAVY_READING_CHARACTERS", "KINDS", "LARGE_MIN_TOPICS", "LONG_MINUTES", "MAX_EXAM_TOPICS",
    "MINUTES_BY_STATE", "PROJECT_KEY", "SMALL_MAX_TOPICS", "ExamTopic", "FormatDecision", "MaterialSignals",
    "decide_format", "exam_date_from_text", "goal_topics", "linked_topic_count", "material_hint",
    "parse_exam_topics", "topic_minutes",
    "topics_from_json", "topics_to_json",
]

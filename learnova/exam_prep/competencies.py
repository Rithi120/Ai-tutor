"""Competencies: what the exam requires, read from the Kompetenzraster, tracked one by one.

A German Kompetenzraster is a grid of "Ich kann ..." statements, usually grouped by topic
and graded by level (Grundanforderung / erweiterte Anforderung, or three columns). The
model extracts them as rows; this module makes the rows safe to store and useful to plan
with (spec Phase 1.1):

* every statement is grounded on its own page (`ground_page`): a row the page does not
  say is never stored as verified - the model's judgement is used, its word is not taken;
* rows are deduplicated by their exact `statement_key` only, never by similarity: "Ich
  kann lineare Funktionen zeichnen" and "... quadratische Funktionen zeichnen" are two
  competencies;
* `expected_rows` counts the "Ich kann / I can" rows of a page deterministically, so an
  extraction that found 12 of 15 rows is a visible gap (`page_gap`), never silent;
* `attach_sections` gives each competency the one topic it belongs to (stemmed words,
  ties stay unassigned), without ever merging two competencies;
* `competency_state` derives the learning status from evidence only - it is never
  stored - and mastering a topic never marks a competency mastered on its own.

Everything here is pure (no database, no AI). The old `normalize_competencies` /
`coverage_summary` API is kept for its callers.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field, fields, replace
from typing import Any, Iterable, Mapping, Sequence

from learnova.projects import topics as project_topics

LEVELS = ("basic", "intermediate", "advanced")
COVERAGE_STATES = ("covered", "partial", "missing")
IMPORTANCE_RANGE = (1, 3)
# Per project, and per grid page: a long Kompetenzraster has 40-80 rows. Rows over a cap
# are counted (`dropped`) and shown as a gap, never cut off silently.
MAX_COMPETENCIES = 150
MAX_PER_PAGE = 60
# The student's decision on a row (the only thing stored about its state): extracted and
# not looked at yet, kept, or "not in this exam".
DECISIONS = ("identified", "confirmed", "removed")
# The learning status, derived from evidence every time (spec 1.1).
COMPETENCY_STATES = ("identified", "not_started", "teaching", "practised", "needs_revision", "mastered")
ADDRESSED_STATES = frozenset({"practised", "mastered"})
REMOVED = "not_in_exam"
MASTERED_CORRECT = 2            # correct answers tagged to the competency itself
REVISION_ACCURACY = 0.5         # below this share of correct answers (from 2 answers on)
TAUGHT_SHARE = 0.75             # share of a statement's content words a lesson must say

# A row of a Kompetenzraster, as its page writes it: a bullet, a number or a checkbox is
# allowed before the "Ich kann"; a new row also starts after a column break (a tab, a "|"
# or a run of three spaces, as a PDF text layer joins the level columns of one table row).
_MARKER = r"(?:ich\s+kann|du\s+kannst|ich\s+kenne|ich\s+wei(?:ss|ß)|i\s+can|you\s+can)\b"
_LEAD = r"(?:[-*>•◦▪▫■□☐☑☒◻◼○●✓✔–—]+|\(?\d{1,3}[.)]|\(?[a-z]\))?"
_BREAK = r"(?:\t|\||\s{3,})"
_ROW_START = re.compile(rf"(?:^|{_BREAK})\s*{_LEAD}\s*(?={_MARKER})", re.IGNORECASE)
_LEADING_CAN = re.compile(rf"^\s*{_LEAD}\s*{_MARKER}[\s,:]*", re.IGNORECASE)
_TRAILING_BREAK = re.compile(rf"{_BREAK}\s*{_LEAD}\s*$", re.IGNORECASE)
# A wrapped row goes on in the next line when this line ends in one of these.
_CONTINUES = frozenset({
    "einer", "eines", "einem", "einen", "ein", "eine", "der", "die", "das", "den", "dem", "des", "und",
    "oder", "von", "mit", "zu", "zum", "zur", "in", "im", "an", "am", "auf", "aus", "bei", "für", "über",
    "unter", "sowie", "als", "wie", "the", "a", "of", "and", "or", "to", "with", "for", "on", "from",
})


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def strip_can(statement: Any) -> str:
    """The statement without its "Ich kann" / "I can" and any bullet in front."""

    text = " ".join(str(statement or "").split())
    return _LEADING_CAN.sub("", text, count=1).strip() or text


def statement_key(statement: Any) -> str:
    """Identity of a competency: the folded, stemmed content words of the statement
    without its "Ich kann", in order. Exact, never a similarity: two rows that differ in one
    content word ("lineare" / "quadratische") are two competencies."""

    key = "-".join(project_topics._content(strip_can(statement)))
    if len(key) > 160:
        key = key[:143] + "-" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return key or "-".join(project_topics._tokens(statement))[:160]


def _content_set(text: Any) -> set[str]:
    return {word for word in project_topics._content(text) if len(word) >= 3}


# ----------------------------------------------------------------- counting the rows

def expected_rows(page_text: Any) -> list[str]:
    """The "Ich kann / I can" rows of one page, as the page writes them, in order.

    Deterministic: the count is what an extraction is checked against, and the rows are
    the fallback inventory when the AI call fails. A row runs to the next row or the end
    of its line; a wrapped row goes on into the next line when that line starts in lower
    case or the row ends in an article, a preposition or a comma ("... einer" /
    "Parabel bestimmen")."""

    rows: list[str] = []
    open_row = False
    for raw_line in str(page_text or "").splitlines():
        line = raw_line.rstrip()
        starts = [match.end() for match in _ROW_START.finditer(line)]
        if not starts:
            text = " ".join(line.split())
            last = rows[-1].split()[-1].casefold() if rows and rows[-1].split() else ""
            if open_row and text and (text[:1].islower() or last in _CONTINUES or rows[-1].endswith((",", "-"))):
                rows[-1] = f"{rows[-1]} {text}"
                open_row = not text.endswith((".", "!", "?"))
            else:
                open_row = False
            continue
        for index, start in enumerate(starts):
            segment = line[start:starts[index + 1]] if index + 1 < len(starts) else line[start:]
            if index + 1 < len(starts):
                trailing = _TRAILING_BREAK.search(segment)
                if trailing:
                    segment = segment[:trailing.start()]
            text = " ".join(segment.strip(" \t|").split())
            if text:
                rows.append(text)
        open_row = bool(rows) and not rows[-1].endswith((".", "!", "?"))
    return rows


def page_gap(*, expected: int, found: int, rejected: int = 0, dropped: int = 0) -> dict[str, int] | None:
    """{expected, found, missing, rejected, dropped} when a page's inventory is incomplete:
    fewer verified rows than the page lists, a row the page does not say, or rows over the
    cap. None when the page is complete."""

    expected, found = max(0, int(expected)), max(0, int(found))
    rejected, dropped = max(0, int(rejected)), max(0, int(dropped))
    missing = max(0, expected - found)
    if not (missing or rejected or dropped):
        return None
    return {"expected": expected, "found": found, "missing": missing, "rejected": rejected, "dropped": dropped}


# ----------------------------------------------------------------- the model's rows

def _group_path(item: Mapping[str, Any]) -> list[str]:
    raw = item.get("group_path")
    if isinstance(raw, str):
        raw = [part for part in re.split(r"\s*(?:>|›|»)\s*", raw) if part]
    path = [_clean(part, 120) for part in raw if isinstance(part, (str, int, float))] if isinstance(raw, list) else []
    path = [part for part in path if part]
    if not path:
        path = [part for part in (_clean(item.get("topic"), 120), _clean(item.get("subtopic"), 120)) if part]
    return path[:4]


def _importance(value: Any) -> int:
    if isinstance(value, str) and value.strip().casefold() in ("low", "niedrig", "medium", "mittel", "high", "hoch"):
        return {"low": 1, "niedrig": 1, "medium": 2, "mittel": 2, "high": 3, "hoch": 3}[value.strip().casefold()]
    if isinstance(value, bool) or value in (None, ""):
        return 2
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return 2
    return max(IMPORTANCE_RANGE[0], min(IMPORTANCE_RANGE[1], number))


def _level(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if text in LEVELS:
        return text
    aliases = (("grund", "basic"), ("basis", "basic"), ("einfach", "basic"), ("easy", "basic"),
               ("erweitert", "advanced"), ("vertieft", "advanced"), ("hoch", "advanced"), ("hard", "advanced"),
               ("advanced", "advanced"), ("mittel", "intermediate"), ("medium", "intermediate"))
    return next((level for word, level in aliases if word in text), "intermediate")


def raw_rows(payload: Any) -> list[Any]:
    """The rows of an answer as the model meant them: {"competencies": [...]}, a bare list,
    or "rows" / "kompetenzen" instead of "competencies" (repaired, not rejected)."""

    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []
    for name in ("competencies", "kompetenzen", "rows", "items"):
        value = payload.get(name)
        if isinstance(value, list):
            return value
    return []


def normalize_competencies(
    payload: Mapping[str, Any] | list,
    *,
    valid_page_ids: Iterable[int],
    notes_text: str = "",
    limit: int = MAX_COMPETENCIES,
) -> list[dict[str, Any]]:
    """Turn the model's `{"competencies": [...]}` into clean rows (no grounding).

    A row needs a statement. Levels and importance fall back to the middle value. Coverage
    "covered"/"partial" must come with an evidence quote that appears in `notes_text`;
    otherwise the row is recorded as "missing" with the evidence dropped - an unsupported
    "covered" is the most expensive mistake this module can let through, because it hides
    a gap until the exam. Duplicates are the same `statement_key` only. `ground_page` is
    the grounded, counted version the app stores."""

    rows, _dropped = _normalized(payload, valid_page_ids=valid_page_ids, notes_text=notes_text, limit=limit)
    return rows


def _normalized(payload: Any, *, valid_page_ids: Iterable[int], notes_text: str = "",
                limit: int = MAX_COMPETENCIES, default_page: int | None = None) -> tuple[list[dict[str, Any]], int]:
    allowed = {int(value) for value in valid_page_ids}
    haystack = " ".join(str(notes_text or "").casefold().split())
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    dropped = 0
    for item in raw_rows(payload):
        if isinstance(item, str) and _LEADING_CAN.match(item):
            # A bare "Ich kann ..." where a row object was asked for is that row.
            item = {"statement": item}
        if not isinstance(item, Mapping):
            continue
        statement = _clean(item.get("statement") or item.get("text") or item.get("competency"), 400)
        if not statement:
            continue
        key = statement_key(statement)
        if not key or key in seen:
            continue
        if len(rows) >= max(0, int(limit)):
            dropped += 1
            continue
        seen.add(key)
        path = _group_path(item)
        coverage = str(item.get("coverage") or "").strip().lower()
        evidence = _clean(item.get("evidence"), 400)
        page_ids: list[int] = []
        values = item.get("source_page_ids")
        if values is None and item.get("page_id") is not None:
            values = [item.get("page_id")]
        for value in values if isinstance(values, list) else [values]:
            if isinstance(value, bool):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number in allowed and number not in page_ids:
                page_ids.append(number)
        if not page_ids and default_page is not None:
            page_ids = [default_page]
        if coverage in ("covered", "partial"):
            quote = " ".join(evidence.casefold().split())
            if not quote or quote not in haystack:
                coverage, evidence = "missing", ""
        elif coverage != "missing":
            coverage, evidence = "missing", ""
        rows.append({
            "statement": statement,
            "statement_key": key,
            "topic": _clean(item.get("topic"), 120) or (path[0] if path else ""),
            "subtopic": _clean(item.get("subtopic"), 120) or (path[-1] if len(path) > 1 else ""),
            "group_path": path,
            "section_title": _clean(item.get("section_title") or item.get("topic_title"), 255),
            "level": _level(item.get("level")),
            "importance": _importance(item.get("importance")),
            "coverage": coverage,
            "evidence": evidence,
            "source_page_ids": page_ids,
        })
    return rows, dropped


@dataclass
class PageInventory:
    """One grid page's competencies: the verified rows, the rows its page does not say
    (`rejected`, kept for the count only), rows over the cap, and the deterministic count."""

    page_id: int
    rows: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    dropped: int = 0
    expected: int = 0
    source: str = "ai"          # "ai" or "parser" (the fallback when no answer could be used)

    @property
    def gap(self) -> dict[str, int] | None:
        return page_gap(expected=self.expected, found=len(self.rows), rejected=len(self.rejected),
                        dropped=self.dropped)


def ground_page(payload: Any, *, page_id: int, page_text: str, limit: int = MAX_PER_PAGE) -> PageInventory:
    """The model's answer for one grid page, grounded on that page.

    A row is verified only when the page says it (`topics.text_on_page`, the grounding the
    topic map uses): an invented "Ich kann Integrale berechnen" on a page about functions
    is rejected and counted, never stored. Rows cite this page."""

    rows, dropped = _normalized(payload, valid_page_ids=[page_id], limit=limit, default_page=page_id)
    inventory = PageInventory(page_id=page_id, dropped=dropped, expected=len(expected_rows(page_text)))
    for row in rows:
        if project_topics.text_on_page(strip_can(row["statement"]), page_text):
            inventory.rows.append({**row, "source_page_ids": [page_id], "verified": True})
        else:
            inventory.rejected.append(row["statement"])
    return inventory


def parsed_page(page_id: int, page_text: str, *, limit: int = MAX_PER_PAGE) -> PageInventory:
    """The fallback inventory of one page: its "Ich kann" rows as the page writes them
    (verified by construction, no group path, middle level). Used when the AI call failed
    or its answer could not be used - the rows are never lost because a model was busy."""

    found = expected_rows(page_text)
    payload = {"competencies": [{"statement": row} for row in found]}
    rows, dropped = _normalized(payload, valid_page_ids=[page_id], limit=limit, default_page=page_id)
    return PageInventory(page_id=page_id, rows=[{**row, "verified": True} for row in rows], dropped=dropped,
                         expected=len(found), source="parser")


# ----------------------------------------------------------------- topics

def attach_sections(
    competencies: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Give each competency the one section (topic) it belongs to. Never merges two
    competencies; a section can hold many.

    1. The model's `section_title` (one of the listed topics), when it names a section by
       its key and no other section matches the competency better.
    2. Otherwise stemmed, folded word overlap ("quadratischen Funktion" ==
       "Quadratische Funktionen"): the last heading of its group path counts double, the
       statement and other headings once, a section's main topic half. A tie between a
       compound section ("Lineare und quadratische Funktionen") and one narrower section
       goes to the narrower one; any other tie, or no overlap, stays unassigned
       (`section_id=None`): shown, with a one-tap "Make it a topic", never guessed.

    `sections` rows need `id`, `title` and `main_topic`."""

    indexed = []
    for section in sections:
        indexed.append((int(section["id"]), _content_set(section.get("title")), _content_set(section.get("main_topic")),
                        project_topics.is_compound(section.get("title") or ""),
                        project_topics.topic_key(str(section.get("title") or ""))))
    out: list[dict[str, Any]] = []
    for row in competencies:
        path = row.get("group_path") if isinstance(row.get("group_path"), list) else []
        heading = str(path[-1]) if path else str(row.get("subtopic") or row.get("topic") or "")
        others = " ".join(str(part) for part in path[:-1]) if path else str(row.get("topic") or "")
        own = _content_set(heading)
        broad = _content_set(strip_can(row.get("statement"))) | _content_set(others)
        scores: dict[int, float] = {}
        for section_id, title_words, main_words, _compound, _key in indexed:
            if title_words or main_words:
                scores[section_id] = (2 * len(own & title_words) + len(broad & title_words)
                                      + 0.5 * len((own | broad) & main_words))
        best = max(scores.values(), default=0.0)
        chosen = None
        hint = str(row.get("section_title") or "").strip()
        if hint and best > 0:
            wanted = project_topics.topic_key(hint)
            named = [entry[0] for entry in indexed if entry[4] == wanted]
            if len(named) == 1 and scores.get(named[0], 0.0) >= best:
                chosen = named[0]
        if chosen is None and best > 0:
            top = [entry for entry in indexed if scores.get(entry[0], 0.0) == best]
            narrow = [entry for entry in top if not entry[3]]
            if len(top) == 1:
                chosen = top[0][0]
            elif len(narrow) == 1:
                chosen = narrow[0][0]
        out.append({**dict(row), "section_id": chosen})
    return out


def match_competency(text: Any, competencies: Sequence[Mapping[str, Any]]) -> int | None:
    """The one competency (of a section) a question is about, or None.

    The question's words (prompt, concept, answer) against each statement: at least two
    shared content words or half of the statement's, and a unique best - a tie is no tag.
    Server-side only: the result is never taken from the request."""

    words = _content_set(text)
    if not words:
        return None
    scored = []
    for row in competencies:
        own = _content_set(strip_can(row.get("statement")))
        shared = len(own & words)
        if own and (shared >= 2 or shared / len(own) >= 0.5):
            scored.append((round(shared / len(own) + 0.01 * shared, 6), row.get("id")))
    if not scored:
        return None
    scored.sort(key=lambda entry: -entry[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None
    try:
        return int(scored[0][1])
    except (TypeError, ValueError):
        return None


def taught_competencies(text: Any, competencies: Sequence[Mapping[str, Any]]) -> list[int]:
    """Ids of the competencies a lesson's parts teach: at least TAUGHT_SHARE of the
    statement's content words are in the lesson. Explaining "Quadratische Funktionen" does
    not teach "die Steigung einer linearen Funktion bestimmen" (only "Funktion" is shared)."""

    words = _content_set(text)
    taught = []
    for row in competencies:
        own = _content_set(strip_can(row.get("statement")))
        if own and len(own & words) / len(own) >= TAUGHT_SHARE:
            taught.append(int(row["id"]))
    return taught


# ----------------------------------------------------------------- learning status

@dataclass(frozen=True)
class CompetencyEvidence:
    """What the app knows about one competency (a `Competency` row and its topic)."""

    competency_id: int
    decision: str = "identified"        # DECISIONS: the student's choice
    section_id: int | None = None
    section_included: bool = True       # its topic is in the plan
    section_state: str = "not_started"  # coverage.topic_state of its topic
    section_reteach: bool = False
    review_due: bool = False            # its topic's spaced review is due or overdue
    taught_count: int = 0               # lessons whose parts taught it
    answers: int = 0                    # graded answers to questions tagged with it
    correct_answers: int = 0            # of those: score >= 80, no hints

    def __post_init__(self) -> None:
        for item in fields(self):
            if item.name in ("taught_count", "answers", "correct_answers"):
                try:
                    value = max(0, int(getattr(self, item.name) or 0))
                except (TypeError, ValueError):
                    value = 0
                object.__setattr__(self, item.name, value)
        if self.decision not in DECISIONS:
            object.__setattr__(self, "decision", "identified")
        object.__setattr__(self, "correct_answers", min(self.correct_answers, self.answers))


def competency_state(e: CompetencyEvidence) -> str:
    """One of COMPETENCY_STATES (or REMOVED for "not in this exam"), from evidence only.

    identified - extracted, not confirmed yet, or not attached to a topic;
    not_started - confirmed, nothing taught or answered;
    teaching - a lesson taught it, no answer yet;
    practised - answered;
    needs_revision - answered, and its topic is flagged for another explanation or its
      review is due, or fewer than half of two or more answers were right;
    mastered - its topic is mastered AND at least two answers tagged to this competency
      were right. Mastery is never inherited from the topic alone."""

    if e.decision == "removed":
        return REMOVED
    if e.decision == "identified" or e.section_id is None:
        return "identified"
    if e.taught_count == 0 and e.answers == 0:
        return "not_started"
    weak = e.answers >= 2 and e.correct_answers / e.answers < REVISION_ACCURACY
    if weak or (e.answers and (e.section_reteach or e.review_due)):
        return "needs_revision"
    if e.section_state == "mastered" and e.correct_answers >= MASTERED_CORRECT:
        return "mastered"
    if e.answers:
        return "practised"
    return "teaching"


def counts_as_addressed(e: CompetencyEvidence) -> bool:
    """Does this competency count for the coverage check? Practised or mastered - and also
    "needs revision" when the only reason is its topic's spaced review coming due: it was
    practised, and a due review is the plan's job, not a gap in coverage (the topic checks
    do not drop on a due review either, so "complete" never flickers with the schedule).
    Weak answers or a reteach flag still hold the check open."""

    state = competency_state(e)
    if state in ADDRESSED_STATES:
        return True
    return state == "needs_revision" and competency_state(replace(e, review_due=False)) in ADDRESSED_STATES


def competency_check(states: Sequence[str], *, missing_rows: int = 0, open_gaps: int = 0) -> dict[str, Any] | None:
    """The coverage check "competencies_addressed" ({code, ok, done, total, gaps}), or None
    for a project without a Kompetenzraster inventory.

    Complete only when every competency not marked "not in this exam" is practised or
    mastered, no grid page lists more rows than were found (`missing_rows`, counted into
    the total) and no page's inventory is still open (`open_gaps`: rows the page does not
    say, rows over the cap). A competency of a topic the student left out of the plan
    still counts."""

    counted = [state for state in states if state != REMOVED]
    missing_rows, open_gaps = max(0, int(missing_rows)), max(0, int(open_gaps))
    if not counted and not missing_rows and not open_gaps:
        return None
    done = sum(1 for state in counted if state in ADDRESSED_STATES)
    return {"code": "competencies_addressed", "ok": done == len(counted) and not missing_rows and not open_gaps,
            "done": done, "total": len(counted) + missing_rows, "gaps": open_gaps}


# ----------------------------------------------------------------- the old API

def coverage_summary(competencies: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How much of the exam's requirements the notes actually cover."""

    counts = Counter(str(row.get("coverage") or "missing") for row in competencies)
    by_topic: dict[str, dict[str, int]] = {}
    for row in competencies:
        bucket = by_topic.setdefault(str(row.get("topic") or ""), {state: 0 for state in COVERAGE_STATES})
        bucket[str(row.get("coverage") or "missing")] += 1
    total = len(competencies)
    return {
        "total": total,
        "covered": counts.get("covered", 0),
        "partial": counts.get("partial", 0),
        "missing": counts.get("missing", 0),
        "covered_percent": round(100 * (counts.get("covered", 0) + 0.5 * counts.get("partial", 0)) / total) if total else 0,
        "by_topic": by_topic,
        "unassigned": sum(1 for row in competencies if row.get("section_id") is None),
    }


def section_importance(competencies: Sequence[Mapping[str, Any]], section_id: int | None) -> float:
    """The weight a topic carries in the exam: the mean importance of its competencies."""

    values = [int(row.get("importance") or 2) for row in competencies if row.get("section_id") == section_id]
    return round(sum(values) / len(values), 2) if values else 2.0


def missing_for_section(competencies: Sequence[Mapping[str, Any]], section_id: int | None) -> list[str]:
    return [
        str(row.get("statement") or "") for row in competencies
        if row.get("section_id") == section_id and row.get("coverage") == "missing"
    ]

"""Learning state of a project's topics and the checks that say a project is complete.

Before this module a topic counted as "learned" after any finished session, even one
with every answer wrong, and a project could never be complete. Here a topic's state
comes only from evidence:

* opening or clicking through a lesson makes a topic "taught", never more;
* answers make it "practising", whatever their score;
* "understood" needs the topic marked learned (by demonstrated performance), at least
  two correct answers given without hints and a mastery of 60 or more;
* "mastered" additionally needs mastery 85 and two spaced reviews passed.

`project_coverage` combines the topics with the material report of
`document_analysis.material_report`: a project is complete only when every document
was read, every page belongs to a topic, the terms and typed items were taught or
studied, and every included topic was understood. Pure: no database, no AI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any, Mapping, Sequence

TOPIC_STATES = ("not_started", "taught", "practising", "understood", "mastered")
ACTIVITIES = ("start_lesson", "continue_practice", "reteach", "review_due", "study_terms", "exam_practice", "done")
CHECK_CODES = ("documents_processed", "topics_extracted", "terms_explained", "objectives_covered",
               "items_explained", "understanding_demonstrated")

UNDERSTOOD_MASTERY = 60.0
MASTERED_MASTERY = 85.0
UNDERSTOOD_CORRECT = 2         # correct answers (score >= 80, no hints) needed for "understood"
MASTERED_REVIEW_STEP = 2       # spaced reviews passed needed for "mastered"
EXPLAINED_SHARE = 0.8          # share of terms / items that must be taught or studied
IMPORTANT = 3                  # importance of a topic the overview must never hide
# Learning checks behind "percent"; understanding is what the project is for, so it weighs
# as much as having been taught everything plus knowing the terms.
PERCENT_WEIGHTS = {"terms_explained": 1.0, "objectives_covered": 1.0, "items_explained": 1.0,
                   "understanding_demonstrated": 2.0}

_PROCESSED_FILE_STATES = frozenset({"analysed", "needs_ocr_review"})
_EXTRACTION_ISSUES = frozenset({"analysis_failed", "not_analysed", "uncovered_pages"})


def _as_date(value: Any) -> date | None:
    """A calendar day. `datetime` is a `date` subclass that cannot be compared with one,
    so a DateTime column (ConceptMastery.next_review_at) passed in is cut to its day."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return None
    return None


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


@dataclass(frozen=True)
class TopicEvidence:
    """What the app knows about one topic of a project (a `LearningSection`)."""

    topic_id: int
    title: str
    importance: int = 2
    included: bool = True
    lessons_started: int = 0
    lessons_finished: int = 0
    answers: int = 0
    correct_answers: int = 0          # score >= 80 without hints
    mastery_score: float = 0.0        # 0-100
    learned: bool = False             # learned_at set by demonstrated performance
    review_step: int = 0              # spaced reviews passed
    reteach: bool = False             # a test or review showed it is not understood
    terms_total: int = 0
    terms_taught: int = 0             # distinct terms a lesson used
    terms_studied: int = 0            # distinct terms studied on flashcards and not already taught
    items_total: int = 0              # typed items from the notes (in_notes)
    items_taught: int = 0
    next_review_on: date | None = None
    has_source: bool = True           # False for a topic the student typed without notes

    def __post_init__(self) -> None:
        # Built straight from DB rows: a NULL count, a mastery of None or a DateTime
        # review date must not crash the workspace, so each field is coerced to its type.
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in ("topic_id", "title"):
                continue
            if field.name == "next_review_on":
                coerced: Any = _as_date(value)
            elif value is None:
                coerced = field.default
            elif field.name == "mastery_score":
                coerced = max(0.0, min(100.0, _number(value)))
            elif isinstance(field.default, bool):
                coerced = (value.strip().casefold() not in ("", "0", "false", "no")
                           if isinstance(value, str) else bool(value))
            else:
                coerced = max(0, int(_number(value)))
            object.__setattr__(self, field.name, coerced)


def topic_state(e: TopicEvidence) -> str:
    """One of TOPIC_STATES, from evidence only.

    A reteach flag keeps a topic at "practising" whatever its score: something showed the
    student has not understood it yet, and an old mastery must not hide that.
    """

    answers = max(0, e.answers)
    correct = max(0, min(e.correct_answers, answers))
    if e.reteach:
        return "practising"
    if answers == 0:
        return "taught" if e.lessons_started > 0 or e.lessons_finished > 0 else "not_started"
    understood = e.learned and correct >= UNDERSTOOD_CORRECT and e.mastery_score >= UNDERSTOOD_MASTERY
    if not understood:
        return "practising"
    if e.mastery_score >= MASTERED_MASTERY and e.review_step >= MASTERED_REVIEW_STEP:
        return "mastered"
    return "understood"


def _terms_covered(e: TopicEvidence) -> int:
    return min(max(0, e.terms_total), max(0, e.terms_taught) + max(0, e.terms_studied))


def _terms_explained(e: TopicEvidence) -> bool:
    return e.terms_total <= 0 or _terms_covered(e) >= EXPLAINED_SHARE * e.terms_total


def next_activity(e: TopicEvidence, state: str, *, today: date) -> str:
    """The one thing to do next for a topic (one of ACTIVITIES).

    Not started -> start the lesson; taught or practising -> continue practising (or
    reteach when flagged); understood or mastered -> a due review first, then the key
    terms when too few were taught or studied, then exam-style practice (understood) or
    nothing (mastered).
    """

    if e.reteach:
        return "reteach"
    if state == "not_started":
        return "start_lesson"
    if state in ("taught", "practising"):
        return "continue_practice"
    day = _as_date(today)
    if e.next_review_on is not None and day is not None and e.next_review_on <= day:
        return "review_due"
    if not _terms_explained(e):
        return "study_terms"
    return "done" if state == "mastered" else "exam_practice"


def _check(code: str, ok: bool, done: int, total: int) -> dict[str, Any]:
    return {"code": code, "ok": bool(ok), "done": max(0, int(done)), "total": max(0, int(total))}


def _int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _document_check(report: Mapping[str, Any]) -> dict[str, Any]:
    files = report.get("files")
    if isinstance(files, list) and files:
        # A file the student excluded is not part of what must be read.
        counted = [file for file in files if isinstance(file, Mapping) and file.get("state") != "excluded"]
        done = sum(1 for file in counted if file.get("state") in _PROCESSED_FILE_STATES)
        total = len(counted)
    else:
        done, total = _int(report.get("documents_analysed")), _int(report.get("documents_uploaded"))
    return _check("documents_processed", done >= total, min(done, total), total)


def _extraction_check(report: Mapping[str, Any]) -> dict[str, Any]:
    # The pages whose text could reach a topic: read, needing an OCR check, or a duplicate
    # of such a page (`material_report` "pages_readable"). "pages_read" alone leaves out
    # handwritten pages that need a check, so it is only the fallback for older reports.
    if report.get("pages_readable") is not None:
        total = _int(report.get("pages_readable"))
    else:
        total = sum(_int(report.get(key)) for key in ("pages_read", "pages_review", "pages_duplicate"))
    # Disjoint subsets of the readable pages: never analysed, analysis failed, no topic.
    missing = sum(_int(report.get(key)) for key in ("pages_uncovered", "pages_not_analysed",
                                                    "pages_analysis_failed"))
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    failed = any(isinstance(issue, Mapping) and issue.get("code") in _EXTRACTION_ISSUES for issue in issues or [])
    total = max(total, missing)
    # Topics the cap left out are found but not in the project: "do not silently discard
    # topics" means the check stays open until the student adds them back or they go.
    dropped = _int(report.get("topics_dropped"))
    check = _check("topics_extracted", missing == 0 and not failed and not dropped, total - missing, total)
    if dropped:
        check["dropped"] = dropped
    return check


def project_coverage(report: Mapping[str, Any], topics: Sequence[TopicEvidence]) -> dict[str, Any]:
    """Whether a project's material is covered and learned, check by check.

    `report` is `document_analysis.material_report(...)` ({} for a project without
    uploaded material); `topics` holds every topic, excluded ones too (they are left out
    of the checks). Returns {"checks": [{"code", "ok", "done", "total"}, ...] in
    CHECK_CODES order, "complete": every check ok, "percent": 0-100, "counts": {state: n}
    over included topics, "not_taught_important": [ids of included importance-3 topics
    not started], "not_taught": [ids of included topics not started]}.

    A project with no included topic is never complete: nothing learned is not
    everything learned.

    "percent" is learning progress, so a project whose papers were all read but where
    nothing was learned yet shows 0, not "two of six checks": the weighted mean of the
    learning checks (PERCENT_WEIGHTS; a passed check counts fully, otherwise done/total;
    terms and items only when the project has any). The material checks gate it instead:
    it reaches 100 only when every check, material included, passes.
    """

    report = report if isinstance(report, Mapping) else {}
    included = [topic for topic in topics or () if topic.included]
    states = {topic.topic_id: topic_state(topic) for topic in included}
    counts = {state: 0 for state in TOPIC_STATES}
    for state in states.values():
        counts[state] += 1
    started = sum(1 for state in states.values() if state != "not_started")
    understood = sum(1 for state in states.values() if state in ("understood", "mastered"))
    terms_total = sum(max(0, topic.terms_total) for topic in included)
    terms_done = sum(_terms_covered(topic) for topic in included)
    items_total = sum(max(0, topic.items_total) for topic in included)
    items_done = sum(min(max(0, topic.items_total), max(0, topic.items_taught)) for topic in included)
    checks = [
        _document_check(report),
        _extraction_check(report),
        _check("terms_explained", terms_total == 0 or terms_done >= EXPLAINED_SHARE * terms_total,
               terms_done, terms_total),
        _check("objectives_covered", bool(included) and started == len(included), started, len(included)),
        _check("items_explained", items_total == 0 or items_done >= EXPLAINED_SHARE * items_total,
               items_done, items_total),
        _check("understanding_demonstrated", bool(included) and understood == len(included),
               understood, len(included)),
    ]
    complete = all(check["ok"] for check in checks)
    weighted = total_weight = 0.0
    for check in checks:
        weight = PERCENT_WEIGHTS.get(check["code"], 0.0)
        if not weight or (check["code"] in ("terms_explained", "items_explained") and not check["total"]):
            continue
        share = 1.0 if check["ok"] else min(0.99, check["done"] / check["total"]) if check["total"] else 0.0
        weighted += weight * share
        total_weight += weight
    percent = int(100 * weighted / total_weight) if total_weight else 0
    return {
        "checks": checks,
        "complete": complete,
        "percent": 100 if complete else min(99, percent),
        "counts": counts,
        "not_taught_important": [topic.topic_id for topic in included
                                 if states[topic.topic_id] == "not_started" and topic.importance >= IMPORTANT],
        "not_taught": [topic.topic_id for topic in included if states[topic.topic_id] == "not_started"],
    }

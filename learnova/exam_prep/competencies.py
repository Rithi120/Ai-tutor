"""Competencies: what the exam requires, read from the Kompetenzraster, checked against the notes.

A German Kompetenzraster is a grid of "Ich kann ..." statements, usually grouped by topic
and graded by level (Grundanforderung / erweiterte Anforderung, or three columns). The
model extracts them as rows; this module makes the rows safe to store and useful to plan
with: every statement is text, every level and coverage value is from a fixed vocabulary,
every page reference exists, and a claim that the notes cover a competency is only kept
when the quoted evidence really appears in the notes. The model's judgement is used; its
word is not taken.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

LEVELS = ("basic", "intermediate", "advanced")
COVERAGE_STATES = ("covered", "partial", "missing")
IMPORTANCE_RANGE = (1, 3)
MAX_COMPETENCIES = 80
_WORD = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
_STOPWORDS = frozenset({
    "ich", "kann", "und", "oder", "die", "der", "das", "den", "dem", "des", "ein", "eine", "einen",
    "mit", "von", "aus", "für", "auf", "bei", "the", "and", "can", "with", "from", "for", "into",
    "einer", "eines", "einem", "sich", "sowie", "auch", "zum", "zur", "über", "unter",
})


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _words(text: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(str(text or "")) if word.casefold() not in _STOPWORDS}


def normalize_competencies(
    payload: Mapping[str, Any],
    *,
    valid_page_ids: Iterable[int],
    notes_text: str = "",
    limit: int = MAX_COMPETENCIES,
) -> list[dict[str, Any]]:
    """Turn the model's `{"competencies": [...]}` into clean rows.

    A row needs a statement and a topic. Levels, importance and coverage fall back to the
    middle/cautious value. Coverage "covered"/"partial" must come with an evidence quote
    that appears in `notes_text`; otherwise the row is recorded as "missing" with the
    evidence dropped - an unsupported "covered" is the most expensive mistake this module
    can let through, because it hides a gap until the exam.
    """

    allowed = {int(value) for value in valid_page_ids}
    haystack = " ".join(str(notes_text or "").casefold().split())
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in list(payload.get("competencies") or [])[: limit * 2]:
        if not isinstance(item, Mapping):
            continue
        statement = _clean(item.get("statement"), 400)
        topic = _clean(item.get("topic"), 120)
        if not statement or not topic:
            continue
        key = statement.casefold()
        if key in seen:
            continue
        seen.add(key)
        level = str(item.get("level") or "").strip().lower()
        coverage = str(item.get("coverage") or "").strip().lower()
        evidence = _clean(item.get("evidence"), 400)
        try:
            importance = int(item.get("importance") or 2)
        except (TypeError, ValueError):
            importance = 2
        importance = max(IMPORTANCE_RANGE[0], min(IMPORTANCE_RANGE[1], importance))
        page_ids: list[int] = []
        for value in item.get("source_page_ids") or []:
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number in allowed and number not in page_ids:
                page_ids.append(number)
        if coverage in ("covered", "partial"):
            quote = " ".join(evidence.casefold().split())
            if not quote or quote not in haystack:
                coverage, evidence = "missing", ""
        elif coverage != "missing":
            coverage, evidence = "missing", ""
        rows.append({
            "statement": statement,
            "topic": topic,
            "subtopic": _clean(item.get("subtopic"), 120),
            "level": level if level in LEVELS else "intermediate",
            "importance": importance,
            "coverage": coverage,
            "evidence": evidence,
            "source_page_ids": page_ids,
        })
        if len(rows) >= limit:
            break
    return rows


def attach_sections(
    competencies: Sequence[Mapping[str, Any]],
    sections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Give each competency the section (topic) it belongs to, by word overlap.

    `sections` rows need `id`, `title` and `main_topic`. A competency whose words match no
    section keeps `section_id=None`: it is a requirement the material has no section for,
    which the plan shows rather than hides.
    """

    indexed = [
        (int(section["id"]), _words(f"{section.get('title', '')} {section.get('main_topic', '')}"))
        for section in sections
    ]
    out: list[dict[str, Any]] = []
    for row in competencies:
        own = _words(f"{row.get('topic', '')} {row.get('subtopic', '')}")
        broad = own | _words(row.get("statement", ""))
        best_id, best_score = None, 0
        for section_id, words in indexed:
            if not words:
                continue
            # Topic words count double: "Quadratische Gleichungen" in the topic column is a
            # stronger signal than the same words buried in a long statement.
            score = 2 * len(own & words) + len(broad & words)
            if score > best_score:
                best_id, best_score = section_id, score
        out.append({**dict(row), "section_id": best_id if best_score > 0 else None})
    return out


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

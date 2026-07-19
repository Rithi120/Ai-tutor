"""Pure scheduling rules for Learnova's exam study planner.

The planner deliberately uses deterministic application data.  AI may explain a
recommendation, but it never decides ownership, dates, mastery, or completion.
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any, Iterable


WEEKDAYS = tuple(range(7))
DIFFICULTIES = ("easy", "medium", "hard")


def normalize_preferred_days(values: Iterable[Any]) -> list[int]:
    """Return unique Python weekday numbers, rejecting unusable values."""

    normalized: list[int] = []
    for value in values:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if day in WEEKDAYS and day not in normalized:
            normalized.append(day)
    return sorted(normalized) or list(WEEKDAYS)


def exam_countdown(exam_date: date, today: date | None = None) -> int:
    return max(0, (exam_date - (today or date.today())).days)


def _study_dates(start: date, exam_date: date, preferred_days: list[int]) -> list[date]:
    last_day = exam_date - timedelta(days=1) if exam_date > start else start
    dates: list[date] = []
    cursor = start
    while cursor <= last_day:
        if cursor.weekday() in preferred_days:
            dates.append(cursor)
        cursor += timedelta(days=1)
    if not dates and start <= exam_date:
        dates.append(start)
    return dates


def _difficulty(mastery: float, preference: str) -> str:
    if preference in DIFFICULTIES:
        preferred = preference
    else:
        preferred = "medium"
    if mastery < 40:
        return "easy"
    if mastery > 70 and preferred != "easy":
        return "hard"
    return "medium" if preferred != "easy" else "easy"


def _task(kind: str, minutes: int, **values: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "minutes": max(3, int(minutes)),
        "completed": False,
        **values,
    }


def _dedupe_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for task in tasks:
        key = (
            task.get("kind"), task.get("section_id"), task.get("concept"),
            task.get("exam_id"), task.get("source_attempt_id"),
            task.get("difficulty") if task.get("kind") == "mock_exam" else None,
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(task)
    return result


def _assign_tasks(
    dates: list[date], tasks: list[dict[str, Any]], daily_minutes: int
) -> list[dict[str, Any]]:
    if not dates:
        return []
    buckets = [{"date": value, "tasks": [], "minutes": 0} for value in dates]
    for index, task in enumerate(tasks):
        eligible = [bucket for bucket in buckets if bucket["minutes"] + task["minutes"] <= daily_minutes]
        if eligible:
            bucket = min(eligible, key=lambda item: item["date"])
        else:
            bucket = min(buckets, key=lambda item: (item["minutes"], item["date"]))
            remaining = max(3, daily_minutes - bucket["minutes"])
            task = {**task, "minutes": min(task["minutes"], remaining)}
        bucket["tasks"].append(task)
        bucket["minutes"] += task["minutes"]
    return [bucket for bucket in buckets if bucket["tasks"]]


def build_plan_schedule(
    *,
    today: date,
    exam_date: date,
    daily_minutes: int,
    preferred_days: Iterable[Any],
    difficulty_preference: str,
    sections: Iterable[dict[str, Any]],
    masteries: Iterable[dict[str, Any]],
    mistakes: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build a balanced schedule from saved material and learning evidence."""

    days = normalize_preferred_days(preferred_days)
    dates = _study_dates(today, exam_date, days)
    if not dates:
        return []
    daily_minutes = max(10, min(480, int(daily_minutes)))
    mastery_rows = list(masteries)
    now_floor = datetime.combine(today, datetime.min.time())
    ordered_masteries = sorted(
        mastery_rows,
        key=lambda item: (
            0 if item.get("next_review_at") and item["next_review_at"].replace(tzinfo=None) <= now_floor else 1,
            -int(item.get("recent_mistake_count") or 0),
            float(item.get("mastery_score") or 0),
            str(item.get("concept") or "").casefold(),
        ),
    )
    tasks: list[dict[str, Any]] = []
    for row in ordered_masteries:
        score = float(row.get("mastery_score") or 0)
        due = row.get("next_review_at") is None or row["next_review_at"].replace(tzinfo=None) <= now_floor
        if due or score < 70 or int(row.get("recent_mistake_count") or 0) > 0:
            tasks.append(_task(
                "review", 8, mastery_id=row.get("id"), subject=row.get("subject"),
                concept=row.get("concept"), mastery=score,
                reason="overdue" if due else ("repeated_mistakes" if row.get("recent_mistake_count") else "weak"),
                difficulty=_difficulty(score, difficulty_preference),
            ))

    seen_mistakes: set[str] = set()
    for mistake in mistakes:
        concept = str(mistake.get("concept") or "").strip()
        if not concept or concept.casefold() in seen_mistakes:
            continue
        seen_mistakes.add(concept.casefold())
        tasks.append(_task(
            "mistakes", 8, subject=mistake.get("subject"), concept=concept,
            source_attempt_id=mistake.get("id"), difficulty="easy", reason="recent_mistake",
        ))

    active_sections = [row for row in sections if not row.get("excluded")]
    for row in sorted(active_sections, key=lambda item: (int(item.get("position") or 0), int(item.get("id") or 0))):
        if row.get("status") in {"completed", "exam_ready"}:
            continue
        minutes = max(8, min(30, int(row.get("estimated_minutes") or 10)))
        tasks.append(_task(
            "learn", minutes, section_id=row.get("id"), section_title=row.get("title"),
            difficulty=difficulty_preference if difficulty_preference in DIFFICULTIES else "medium",
        ))
        tasks.append(_task(
            "quiz", 10, section_id=row.get("id"), section_title=row.get("title"),
            difficulty=difficulty_preference if difficulty_preference in DIFFICULTIES else "medium",
        ))

    stronger = sorted(
        (row for row in mastery_rows if float(row.get("mastery_score") or 0) >= 70),
        key=lambda item: (-float(item.get("mastery_score") or 0), str(item.get("concept") or "")),
    )
    for row in stronger[:2]:
        tasks.append(_task(
            "retention", 6, mastery_id=row.get("id"), subject=row.get("subject"),
            concept=row.get("concept"), mastery=float(row.get("mastery_score") or 0),
            difficulty=_difficulty(float(row.get("mastery_score") or 0), difficulty_preference),
        ))

    days_left = exam_countdown(exam_date, today)
    if days_left >= 2:
        tasks.append(_task("mock_exam", min(30, daily_minutes), difficulty="medium"))
    if days_left >= 10:
        tasks.append(_task("mock_exam", min(40, daily_minutes), difficulty="hard"))
    if not tasks:
        tasks.append(_task("review", min(15, daily_minutes), concept="General review", reason="retention", difficulty="medium"))

    buckets = _assign_tasks(dates, _dedupe_tasks(tasks), daily_minutes)
    # Mock exams belong late in the plan; swap whole task entries without extending it.
    mock_tasks = [task for bucket in buckets for task in bucket["tasks"] if task["kind"] == "mock_exam"]
    if mock_tasks:
        for bucket in buckets:
            bucket["tasks"] = [task for task in bucket["tasks"] if task["kind"] != "mock_exam"]
            bucket["minutes"] = sum(task["minutes"] for task in bucket["tasks"])
        targets = buckets[-len(mock_tasks):]
        for bucket, task in zip(targets, mock_tasks):
            bucket["tasks"].append(task)
            bucket["minutes"] += task["minutes"]
    return buckets


def session_task_ids(tasks: Iterable[dict[str, Any]]) -> dict[str, list[int]]:
    fields = {"lesson_ids": [], "quiz_ids": [], "review_ids": [], "exam_ids": []}
    for task in tasks:
        value = task.get("section_id") or task.get("mastery_id") or task.get("exam_id")
        if value is None:
            continue
        field = {
            "learn": "lesson_ids", "quiz": "quiz_ids", "review": "review_ids",
            "retention": "review_ids", "mistakes": "review_ids", "mock_exam": "exam_ids",
        }.get(str(task.get("kind") or ""))
        if field and int(value) not in fields[field]:
            fields[field].append(int(value))
    return fields


def _future_dates(sessions: list[dict[str, Any]], after: date) -> list[date]:
    return sorted({row["date"] for row in sessions if row["date"] > after and row.get("status") != "completed"})


def redistribute_after_skip(
    sessions: list[dict[str, Any]], *, skipped_date: date, daily_minutes: int
) -> dict[date, list[dict[str, Any]]]:
    """Rebalance incomplete work across existing future dates, never append it."""

    future = _future_dates(sessions, skipped_date)
    if not future:
        return {}
    tasks: list[dict[str, Any]] = []
    for row in sessions:
        if row["date"] == skipped_date or row["date"] in future:
            tasks.extend(task for task in row.get("tasks", []) if not task.get("completed"))
    buckets = _assign_tasks(future, _dedupe_tasks(tasks), max(10, daily_minutes))
    return {row["date"]: row["tasks"] for row in buckets}


def redistribute_overdue_sessions(
    sessions: list[dict[str, Any]], *, today: date, daily_minutes: int
) -> tuple[list[date], dict[date, list[dict[str, Any]]]]:
    """Move every uncompleted past day into existing current/future days."""

    missed = sorted(
        row["date"] for row in sessions
        if row["date"] < today and row.get("status") == "planned"
    )
    if not missed:
        return [], {}
    future = sorted({
        row["date"] for row in sessions
        if row["date"] >= today and row.get("status") != "completed"
    })
    if not future:
        return missed, {}
    tasks = [
        task for row in sessions
        if row["date"] in missed or row["date"] in future
        for task in row.get("tasks", []) if not task.get("completed")
    ]
    buckets = _assign_tasks(future, _dedupe_tasks(tasks), max(10, daily_minutes))
    return missed, {row["date"]: row["tasks"] for row in buckets}


def adapt_future_schedule(
    sessions: list[dict[str, Any]], *, today: date, score: float,
    subject: str, concepts: Iterable[str], daily_minutes: int,
) -> dict[date, list[dict[str, Any]]]:
    """Adjust affected future tasks after performance without rebuilding history."""

    concept_names = {str(value).strip().casefold() for value in concepts if str(value).strip()}
    future = [row for row in sessions if row["date"] > today and row.get("status") != "completed"]
    if not future:
        return {}
    for row in future:
        row["tasks"] = [dict(task) for task in row.get("tasks", [])]
    if score < 60 and concept_names:
        concept = sorted(concept_names)[0]
        first = min(future, key=lambda item: item["date"])
        exists = any(
            task.get("kind") in {"review", "mistakes"}
            and str(task.get("concept", "")).casefold() == concept
            for row in future for task in row["tasks"]
        )
        if not exists:
            first["tasks"].insert(0, _task(
                "review", 8, subject=subject, concept=concept,
                reason="performance_drop", difficulty="easy",
            ))
        for row in future:
            for task in row["tasks"]:
                if str(task.get("concept", "")).casefold() in concept_names:
                    task["difficulty"] = "easy"
    elif score >= 85:
        seen_matching_review = False
        for row in future:
            kept = []
            for task in row["tasks"]:
                matches = str(task.get("concept", "")).casefold() in concept_names
                if matches:
                    task["difficulty"] = "hard" if score >= 95 else "medium"
                if matches and task.get("kind") == "review":
                    if seen_matching_review:
                        continue
                    seen_matching_review = True
                kept.append(task)
            row["tasks"] = kept
    tasks = [task for row in future for task in row["tasks"]]
    buckets = _assign_tasks(sorted(row["date"] for row in future), _dedupe_tasks(tasks), daily_minutes)
    return {row["date"]: row["tasks"] for row in buckets}


def planner_metrics(
    *, exam_date: date, sessions: Iterable[dict[str, Any]],
    masteries: Iterable[dict[str, Any]], sections: Iterable[dict[str, Any]],
    today: date,
) -> dict[str, Any]:
    rows = sorted(list(sessions), key=lambda item: item["date"])
    mastery_rows = list(masteries)
    section_rows = [row for row in sections if not row.get("excluded")]
    completed = [row for row in rows if row.get("status") == "completed"]
    completed_sections = [row for row in section_rows if row.get("status") in {"completed", "exam_ready"}]
    session_progress = len(completed) / max(1, len(rows))
    section_progress = len(completed_sections) / max(1, len(section_rows))
    mastery_average = sum(float(row.get("mastery_score") or 0) for row in mastery_rows) / max(1, len(mastery_rows))
    readiness = round(min(100, session_progress * 25 + section_progress * 30 + mastery_average * .45))
    completed_dates = {row["date"] for row in completed}
    streak = 0
    cursor = today
    if cursor not in completed_dates:
        cursor -= timedelta(days=1)
    while cursor in completed_dates:
        streak += 1
        cursor -= timedelta(days=1)
    next_quiz = next((row for row in rows if row["date"] >= today and any(task.get("kind") == "quiz" for task in row.get("tasks", []))), None)
    next_mock = next((row for row in rows if row["date"] >= today and any(task.get("kind") == "mock_exam" for task in row.get("tasks", []))), None)
    return {
        "countdown": exam_countdown(exam_date, today),
        "progress": round(session_progress * 100),
        "completed_sessions": len(completed),
        "hours_studied": round(sum(int(row.get("completed_minutes") or 0) for row in rows) / 60, 1),
        "streak": streak,
        "readiness": readiness,
        "remaining_lessons": max(0, len(section_rows) - len(completed_sections)),
        "next_quiz": next_quiz,
        "next_mock": next_mock,
    }


def calendar_days(*, year: int, month: int, sessions: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = {row["date"]: row for row in sessions}
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    return [{
        "date": value,
        "in_month": value.month == month,
        "session": lookup.get(value),
    } for week in weeks for value in week]

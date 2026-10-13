"""Pure deterministic gamification rules shared by every Learnova activity."""

from __future__ import annotations

from datetime import date
from math import sqrt
from typing import Any


XP_VALUES = {
    "basic_answer": 2, "written_answer": 3, "difficult_answer": 4,
    "flashcard_mastered": 10, "flashcard_session_completed": 15,
    "learn_session_completed": 20, "test_completed": 20,
    "test_perfect_score": 25, "match_completed": 15,
    "blast_completed": 15, "blocks_completed": 15,
    "quiz_completed": 10, "lesson_completed": 10,
    "practice_answer": 3, "mistake_corrected": 5,
    "study_plan_task_completed": 10, "daily_goal_completed": 20,
}

# Every repeatable event belongs to a cap group with a daily XP budget. Users own their
# sets and grade themselves, so answer validation alone cannot stop farming - a fresh
# session always mints fresh item ids. The budget keeps a long honest study day fully
# rewarded while making an endless replay loop pointless. Goal, mission and badge XP
# are not listed: they are already once-per-period by construction.
DAILY_XP_CAPS = {
    "flashcards": 200,   # answers, mastery, session/test/game completions
    "practice": 150,     # lesson questions, quizzes, recall, corrected mistakes
    "vocabulary": 100,   # vocabulary answers, sessions, created lists
    "planning": 50,      # study-plan tasks
}

_CAP_GROUPS = {
    "flashcard_reviewed": "flashcards", "flashcard_mastered": "flashcards",
    "flashcard_session_completed": "flashcards", "learn_session_completed": "flashcards",
    "test_completed": "flashcards", "test_perfect_score": "flashcards",
    "match_completed": "flashcards", "blast_completed": "flashcards",
    "blocks_completed": "flashcards",
    "practice_question_completed": "practice", "quiz_completed": "practice",
    "mistake_corrected": "practice",
    "vocabulary_reviewed": "vocabulary", "vocabulary_session_completed": "vocabulary",
    "vocabulary_list_created": "vocabulary",
    "study_plan_task_completed": "planning",
}


def xp_cap_group(event_type: str) -> str | None:
    """The daily-cap group an event belongs to, or None when it is uncapped."""
    return _CAP_GROUPS.get(event_type)


def capped_group_events(group: str) -> tuple[str, ...]:
    return tuple(sorted(event for event, name in _CAP_GROUPS.items() if name == group))


def level_for_xp(total_xp: int) -> int:
    """Scalable curve matching 0, 100, 250, 450, 700… XP thresholds."""
    value = max(0, int(total_xp))
    return max(1, int((-1 + sqrt(9 + value / 6.25)) / 2))


def level_start(level: int) -> int:
    level = max(1, int(level))
    return 25 * (level - 1) * (level + 2)


def level_progress(total_xp: int) -> dict[str, int]:
    level = level_for_xp(total_xp)
    start, next_start = level_start(level), level_start(level + 1)
    return {
        "level": level, "total_xp": max(0, int(total_xp)),
        "current_level_xp": max(0, int(total_xp) - start),
        "next_level_xp": next_start - start,
        "xp_to_next_level": max(0, next_start - int(total_xp)),
    }


def bounded_answer_xp(*, correct: bool, written: bool, difficult: bool) -> int:
    if not correct:
        return 0
    if difficult:
        return XP_VALUES["difficult_answer"]
    return XP_VALUES["written_answer" if written else "basic_answer"]


def effective_streak(current: int, last_date: date | None, today: date) -> int:
    """The streak as it truly stands today.

    The stored value only changes when the user studies, so after missed days the
    dashboard kept showing the old number. A streak survives yesterday's activity;
    anything older has ended."""
    if not last_date or (today - last_date).days > 1:
        return 0
    return max(0, int(current))


def update_streak(
    current: int, longest: int, last_date: date | None, activity_date: date,
) -> tuple[int, int, bool]:
    if last_date == activity_date:
        return current, longest, False
    if last_date and (activity_date - last_date).days == 1:
        current += 1
    else:
        current = 1
    return current, max(longest, current), True


def mission_progress(event_type: str, metadata: dict[str, Any]) -> dict[str, int]:
    return {
        "flashcard_reviews": 1 if event_type == "flashcard_reviewed" else 0,
        "correct_answers": 1 if metadata.get("correct") else 0,
        "sessions": 1 if event_type.endswith("_session_completed") else 0,
        "tests": 1 if event_type == "test_completed" else 0,
        "mastered_cards": 1 if event_type == "flashcard_mastered" else 0,
        "minutes": max(0, int(metadata.get("active_seconds", 0)) // 60),
    }

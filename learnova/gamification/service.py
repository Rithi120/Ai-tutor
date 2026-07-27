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

"""Deterministic mastery, scheduling, and adaptive-practice rules."""

from datetime import datetime, timedelta, timezone
import re


DIFFICULTY_LABELS = {1: "easy", 2: "medium", 3: "hard"}


def clamp(value, minimum=0, maximum=100):
    return max(minimum, min(maximum, value))


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def review_is_due(value: datetime | None, now: datetime) -> bool:
    review = ensure_utc(value)
    return review is None or review <= now


def mastery_status(score):
    if score < 30:
        return "weak"
    if score < 70:
        return "learning"
    if score < 85:
        return "strong"
    return "mastered"


def review_interval_days(score):
    if score < 30:
        return 1
    if score < 50:
        return 3
    if score < 70:
        return 7
    if score < 85:
        return 14
    return 30


def preferred_difficulty(score):
    if score < 40:
        return 1
    if score <= 70:
        return 2
    return 2


def normalized_question_text(value):
    """Normalize wording for deterministic recent-question duplicate checks."""
    return re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()


def is_recent_duplicate(question, recent_questions):
    candidate = normalized_question_text(question)
    return bool(candidate) and candidate in {
        normalized_question_text(item) for item in recent_questions if item
    }


def normalize_confidence(value):
    if value is None or value == "":
        return 50.0
    return float(clamp(float(value)))


def update_mastery(
    state,
    score,
    hints_used=False,
    practised_at=None,
    *,
    difficulty=None,
    retry_count=0,
    response_confidence=None,
):
    """Return updated mastery fields without mutating the supplied state."""
    now = ensure_utc(practised_at) or datetime.now(timezone.utc)
    old_score = float(state.get("mastery_score") or 0)
    total_attempts = int(state.get("attempts") or 0) + 1
    correct_attempts = int(state.get("correct_attempts") or 0)
    incorrect_attempts = int(state.get("incorrect_attempts") or 0)
    consecutive_correct = int(state.get("consecutive_correct") or 0)
    consecutive_incorrect = int(state.get("consecutive_incorrect") or 0)
    difficulty = int(state.get("difficulty_level")
                     or preferred_difficulty(old_score)) if difficulty is None else int(clamp(int(difficulty), 1, 3))
    answered_difficulty = difficulty
    retry_count = int(clamp(int(retry_count or 0), 0, 10))
    confidence = normalize_confidence(response_confidence)
    previous_confidence = normalize_confidence(state.get("confidence_trend"))
    recent_mistakes = int(state.get("recent_mistake_count") or 0)
    previous_practice = ensure_utc(state.get("last_practised_at"))
    score = int(clamp(int(score)))

    if score >= 80:
        correct_attempts += 1
        consecutive_correct += 1
        consecutive_incorrect = 0
        delta = 8 if hints_used else 12
        outcome = "correct"
    elif score >= 50:
        consecutive_correct = 0
        consecutive_incorrect = 0
        delta = 1 if hints_used else (2 if score >= 65 else 0)
        outcome = "partial"
    else:
        incorrect_attempts += 1
        consecutive_incorrect += 1
        consecutive_correct = 0
        delta = -(8 + min(consecutive_incorrect - 1, 3) * 3)
        outcome = "incorrect"

    # Difficulty rewards genuinely harder correct work without penalizing easy
    # foundational practice. Retries and hints reduce positive evidence.
    if outcome == "correct":
        delta += max(0, answered_difficulty - 1) * 2
        delta -= min(4, retry_count * 2)
        if confidence >= 75:
            delta += 1
        recent_mistakes = max(0, recent_mistakes - 1)
    elif outcome == "incorrect":
        delta -= max(0, answered_difficulty - 2)
        delta -= min(4, retry_count)
        if confidence >= 75:
            delta -= 2
        recent_mistakes = min(20, recent_mistakes + 1)

    # Correct recall after a long gap is strong retention evidence. A repeated
    # error shortly after practice is stronger evidence that the concept is weak.
    if previous_practice:
        gap = now - previous_practice
        if outcome == "correct" and gap >= timedelta(days=14):
            delta += 2
        elif outcome == "incorrect" and gap <= timedelta(days=2):
            delta -= 2

    new_score = float(clamp(round(old_score + delta, 2)))
    if outcome == "correct" and consecutive_correct == 2:
        difficulty = min(3, difficulty + 1)
    elif outcome == "incorrect" and consecutive_incorrect == 2:
        difficulty = max(1, difficulty - 1)
    elif new_score < 40 and consecutive_correct < 2:
        difficulty = 1
    elif 40 <= new_score <= 70 and consecutive_incorrect < 2:
        difficulty = 2
    elif new_score > 70:
        difficulty = max(2, difficulty)

    interval = 1 if outcome == "incorrect" else review_interval_days(new_score)
    confidence_trend = round(previous_confidence * 0.7 + confidence * 0.3, 2)
    return {
        "mastery_score": new_score,
        "attempts": total_attempts,
        "correct_attempts": correct_attempts,
        "incorrect_attempts": incorrect_attempts,
        "consecutive_correct": consecutive_correct,
        "consecutive_incorrect": consecutive_incorrect,
        "last_practised_at": now,
        "next_review_at": now + timedelta(days=interval),
        "difficulty_level": difficulty,
        "status": mastery_status(new_score),
        "recent_mistake_count": recent_mistakes,
        "confidence_trend": confidence_trend,
        "response_confidence": confidence,
        "retry_count": retry_count,
        "answered_difficulty": answered_difficulty,
        "outcome": outcome,
        "delta": new_score - old_score,
    }


def estimated_question_count(concepts, now=None):
    now = ensure_utc(now) or datetime.now(timezone.utc)
    due = sum(
        1 for item in concepts
        if review_is_due(item.get("next_review_at"), now)
    )
    repeated = sum(1 for item in concepts if int(
        item.get("consecutive_incorrect") or 0) >= 2)
    weak = sum(1 for item in concepts if float(
        item.get("mastery_score") or 0) < 50)
    return min(10, max(5, due + repeated + min(weak, 3)))


def prioritize_concepts(concepts, question_count=None, now=None):
    """Build a diversified plan ordered by due, repeated, weak, stale, then strong."""
    if not concepts:
        return []
    now = ensure_utc(now) or datetime.now(timezone.utc)
    question_count = question_count or estimated_question_count(concepts, now)

    def next_review(item):
        return ensure_utc(item.get("next_review_at")) or datetime.min.replace(tzinfo=timezone.utc)

    def last_practised(item):
        return ensure_utc(item.get("last_practised_at")) or datetime.min.replace(tzinfo=timezone.utc)

    overdue = sorted(
        [item for item in concepts if next_review(item) <= now],
        key=lambda item: (next_review(item), float(
            item.get("mastery_score") or 0)),
    )
    repeated = sorted(
        [item for item in concepts if max(
            int(item.get("consecutive_incorrect") or 0),
            int(item.get("recent_mistake_count") or 0),
        ) >= 2],
        key=lambda item: (-max(
            int(item.get("consecutive_incorrect") or 0),
            int(item.get("recent_mistake_count") or 0),
        ), float(item.get("mastery_score") or 0), str(item.get("concept") or "")),
    )
    weakest = sorted(concepts, key=lambda item: float(
        item.get("mastery_score") or 0))
    low_confidence = sorted(
        [item for item in concepts if float(item.get("confidence_trend") or 50) < 50],
        key=lambda item: (float(item.get("confidence_trend") or 50), float(item.get("mastery_score") or 0), str(item.get("concept") or "")),
    )
    stale = sorted(concepts, key=lambda item: (last_practised(item), str(item.get("concept") or "")))
    stronger = sorted(concepts, key=lambda item: -
                      float(item.get("mastery_score") or 0))

    ordered = []
    seen = set()
    for group in (overdue, weakest, repeated, low_confidence, stale):
        for item in group:
            key = item.get("id") or (item.get("subject"), item.get("concept"))
            if key not in seen:
                ordered.append(item)
                seen.add(key)
    strong_revisions = [
        item for item in stronger if float(item.get("mastery_score") or 0) >= 70
    ][:2 if question_count >= 7 else 1]
    for strong_revision in strong_revisions:
        key = strong_revision.get("id") or (strong_revision.get(
            "subject"), strong_revision.get("concept"))
        if key not in seen:
            ordered.append(strong_revision)
            seen.add(key)

    plan = []
    while len(plan) < question_count:
        for item in ordered:
            if len(plan) >= question_count:
                break
            if len(ordered) > 1 and plan and item is plan[-1]:
                continue
            plan.append(item)
    for offset, strong_revision in enumerate(reversed(strong_revisions), start=1):
        if all(item is not strong_revision for item in plan) and offset <= len(plan):
            plan[-offset] = strong_revision
    return plan


def difficulty_label(level):
    return DIFFICULTY_LABELS.get(int(level or 1), "easy")

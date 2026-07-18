"""Deterministic mastery, scheduling, and adaptive-practice rules."""

from datetime import datetime, timedelta, timezone


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


def update_mastery(state, score, hints_used=False, practised_at=None):
    """Return updated mastery fields without mutating the supplied state."""
    now = ensure_utc(practised_at) or datetime.now(timezone.utc)
    old_score = float(state.get("mastery_score") or 0)
    total_attempts = int(state.get("attempts") or 0) + 1
    correct_attempts = int(state.get("correct_attempts") or 0)
    incorrect_attempts = int(state.get("incorrect_attempts") or 0)
    consecutive_correct = int(state.get("consecutive_correct") or 0)
    consecutive_incorrect = int(state.get("consecutive_incorrect") or 0)
    difficulty = int(state.get("difficulty_level")
                     or preferred_difficulty(old_score))
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
        [item for item in concepts if int(
            item.get("consecutive_incorrect") or 0) >= 2],
        key=lambda item: (-int(item.get("consecutive_incorrect")
                          or 0), float(item.get("mastery_score") or 0)),
    )
    weakest = sorted(concepts, key=lambda item: float(
        item.get("mastery_score") or 0))
    stale = sorted(concepts, key=last_practised)
    stronger = sorted(concepts, key=lambda item: -
                      float(item.get("mastery_score") or 0))

    ordered = []
    seen = set()
    for group in (overdue, repeated, weakest, stale):
        for item in group:
            key = item.get("id") or (item.get("subject"), item.get("concept"))
            if key not in seen:
                ordered.append(item)
                seen.add(key)
    strong_revision = next((item for item in stronger if float(
        item.get("mastery_score") or 0) >= 70), None)
    if strong_revision:
        key = strong_revision.get("id") or (strong_revision.get(
            "subject"), strong_revision.get("concept"))
        if key not in seen:
            ordered.append(strong_revision)

    plan = []
    while len(plan) < question_count:
        for item in ordered:
            if len(plan) >= question_count:
                break
            if len(ordered) > 1 and plan and item is plan[-1]:
                continue
            plan.append(item)
    if strong_revision and all(item is not strong_revision for item in plan):
        plan[-1] = strong_revision
    return plan


def difficulty_label(level):
    return DIFFICULTY_LABELS.get(int(level or 1), "easy")

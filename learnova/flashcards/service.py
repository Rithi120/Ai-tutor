"""Pure, framework-free helpers for AI flashcards.

Two responsibilities, both deterministic and unit-testable without Flask or a database:
- normalize/validate AI-generated cards into a safe stored shape (de-duplicated).
- an SM-2-style spaced-repetition scheduler for study reviews.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any


CARD_TYPES = {
    "question_answer", "term_definition", "formula_explanation",
    "fill_blank", "true_false", "image", "multiple_choice",
}
DIFFICULTIES = {"easy", "medium", "hard"}
GENERATION_DIFFICULTIES = DIFFICULTIES | {"adaptive"}
REVIEW_GRADES = {"again", "hard", "good", "easy"}
SOURCE_KINDS = {"text", "topic", "lesson", "pdf", "image"}

MIN_CARDS = 1
MAX_CARDS = 30
DEFAULT_CARDS = 10


def clamp_count(value: Any) -> int:
    try:
        return max(MIN_CARDS, min(MAX_CARDS, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_CARDS


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def normalize_card(raw: Any) -> dict[str, Any]:
    """Validate/normalize one AI card into the stored shape. Raise ValueError if unusable."""

    if not isinstance(raw, dict):
        raise ValueError("card must be an object")
    card_type = str(raw.get("type") or "question_answer").strip()
    if card_type not in CARD_TYPES:
        card_type = "question_answer"
    front = _text(raw.get("front"), 1000)
    back = _text(raw.get("back"), 2000)
    if not front or not back:
        raise ValueError("card needs both a non-empty front and back")
    difficulty = str(raw.get("difficulty") or "medium").strip()
    if difficulty not in DIFFICULTIES:
        difficulty = "medium"
    tags = []
    if isinstance(raw.get("tags"), list):
        tags = [tag for tag in (_text(item, 40) for item in raw["tags"]) if tag][:8]
    options = []
    if card_type == "multiple_choice" and isinstance(raw.get("options"), list):
        options = [opt for opt in (_text(item, 300) for item in raw["options"]) if opt][:6]
    return {
        "type": card_type,
        "front": front,
        "back": back,
        "explanation": _text(raw.get("explanation"), 1000),
        "hint": _text(raw.get("hint"), 500),
        "tags": tags,
        "options": options,
        "source_reference": _text(raw.get("sourceReference") or raw.get("source_reference"), 200),
        "image_query": _text(raw.get("imageQuery") or raw.get("image_query"), 120),
        "difficulty": difficulty,
    }


def _dedupe_key(card: dict[str, Any]) -> str:
    return re.sub(r"\W+", " ", card["front"]).strip().casefold()


SUGGESTION_STYLES = ("short", "detailed", "example")
MAX_SUGGESTIONS = 3
MAX_SUGGESTION_TERM = 200
MIN_SUGGESTION_TERM = 2


def normalize_suggestions(raw: Any, limit: int = MAX_SUGGESTIONS) -> list[dict[str, str]]:
    """Clamp, de-duplicate and cap AI-proposed definitions for one card front.

    Never trusts the model's count or its style label: a response with twenty entries,
    an unknown style, or a definition longer than a card can hold still yields at most
    `limit` usable suggestions. Returns [] rather than raising, because a student who
    gets no suggestion should simply type their own answer.
    """

    result: list[dict[str, str]] = []
    seen: set[str] = set()
    if not isinstance(raw, list) or limit <= 0:
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        back = _text(item.get("back"), 2000)
        if not back:
            continue
        key = re.sub(r"\W+", " ", back).strip().casefold()
        if not key or key in seen:
            continue
        style = str(item.get("style") or "").strip().lower()
        seen.add(key)
        result.append({"back": back, "style": style if style in SUGGESTION_STYLES else "short"})
        if len(result) >= limit:
            break
    return result


def normalize_cards(raw_cards: Any, limit: int) -> list[dict[str, Any]]:
    """Normalize a list of AI cards, dropping unusable and duplicate (same-front) cards."""

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(raw_cards, list):
        return result
    for raw in raw_cards:
        try:
            card = normalize_card(raw)
        except ValueError:
            continue
        key = _dedupe_key(card)
        if key and key not in seen:
            seen.add(key)
            result.append(card)
        if len(result) >= limit:
            break
    return result


# --------------------------------------------------------------------------- #
# SM-2-style spaced repetition
# --------------------------------------------------------------------------- #

def new_schedule(now: datetime | None = None) -> dict[str, Any]:
    """Initial scheduling state for a freshly created card (due immediately)."""

    now = now or datetime.now(timezone.utc)
    return {
        "interval": 0,
        "repetition_count": 0,
        "ease_factor": 2.5,
        "next_review_at": now,
        "last_reviewed_at": None,
        "correct_count": 0,
        "incorrect_count": 0,
        "mastery_level": "new",
    }


def _mastery_level(repetition_count: int, interval: int) -> str:
    if repetition_count <= 0:
        return "new"
    if interval >= 21:
        return "mastered"
    if repetition_count >= 3:
        return "review"
    return "learning"


def review(state: dict[str, Any], grade: str, now: datetime | None = None) -> dict[str, Any]:
    """Apply one SM-2-style review for grade in {again, hard, good, easy}.

    "again" resets the card and schedules it for the same session (interval 0);
    hard/good/easy grow the interval by the ease factor, hard shorter and easy longer.
    """

    now = now or datetime.now(timezone.utc)
    grade = grade if grade in REVIEW_GRADES else "good"
    ease = float(state.get("ease_factor", 2.5) or 2.5)
    reps = int(state.get("repetition_count", 0) or 0)
    interval = int(state.get("interval", 0) or 0)
    correct = int(state.get("correct_count", 0) or 0)
    incorrect = int(state.get("incorrect_count", 0) or 0)

    if grade == "again":
        reps = 0
        interval = 0  # re-review within the same session
        ease = max(1.3, ease - 0.2)
        incorrect += 1
    else:
        correct += 1
        quality = {"hard": 3, "good": 4, "easy": 5}[grade]
        ease = max(1.3, ease + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
        reps += 1
        if reps == 1:
            interval = 1
        elif reps == 2:
            interval = 6
        else:
            interval = max(1, round(interval * ease))
        if grade == "hard":
            interval = max(1, round(interval * 0.5))
        elif grade == "easy":
            interval = round(interval * 1.3)

    next_review = now + timedelta(days=interval) if interval > 0 else now
    return {
        "interval": interval,
        "repetition_count": reps,
        "ease_factor": round(ease, 3),
        "next_review_at": next_review,
        "last_reviewed_at": now,
        "correct_count": correct,
        "incorrect_count": incorrect,
        "mastery_level": _mastery_level(reps, interval),
    }

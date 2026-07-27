"""Pure question-building, checking, scoring and mastery helpers for flashcard modes."""

from __future__ import annotations

import random
import re
from typing import Any


MODES = {"flashcards", "learn", "test", "match", "blast", "blocks"}
WRITTEN_TYPES = {"written", "fill_blank", "front_to_back", "back_to_front"}


def normalize_answer(value: Any, *, punctuation_tolerant: bool = True) -> str:
    text = " ".join(str(value or "").strip().casefold().split())
    if punctuation_tolerant:
        text = re.sub(r"[.!?,;:]+$", "", text)
    return text


def answer_is_correct(student: Any, expected: str, question_type: str) -> bool:
    strict = question_type in {"formula", "date", "technical"}
    return normalize_answer(student, punctuation_tolerant=not strict) == normalize_answer(
        expected, punctuation_tolerant=not strict)


def mastery_state(repetitions: int, interval: int, consecutive_correct: int) -> str:
    if repetitions <= 0:
        return "new"
    if interval >= 21 and consecutive_correct >= 3:
        return "mastered"
    if consecutive_correct >= 3:
        return "strong"
    if consecutive_correct >= 1:
        return "familiar"
    return "learning"


def weakness_score(correct: int, incorrect: int, consecutive_incorrect: int, ease: float) -> float:
    attempts = correct + incorrect
    error_rate = incorrect / attempts if attempts else 0.5
    return round(min(100.0, error_rate * 65 + consecutive_incorrect * 10 + max(0, 2.5 - ease) * 20), 2)


def select_cards(cards: list[Any], objective: str, now, limit: int) -> list[Any]:
    selected = list(cards)
    if objective == "due":
        selected = [card for card in selected if card.next_review_at <= now]
    elif objective == "weak":
        selected = [card for card in selected if card.weakness_score >= 35 or card.incorrect_count > card.correct_count]
    elif objective == "starred":
        selected = [card for card in selected if card.starred]
    elif objective == "new":
        selected = [card for card in selected if card.repetition_count == 0]
    selected.sort(key=lambda card: (-float(card.weakness_score or 0), card.id))
    return selected[:max(1, limit)]


def build_items(
    cards: list[Any], mode: str, seed: int, count: int, *,
    direction: str = "mixed", question_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    chosen = list(cards)
    rng.shuffle(chosen)
    chosen = chosen[:max(1, count)]
    items = []
    for position, card in enumerate(chosen):
        item_direction = direction
        if item_direction == "mixed":
            item_direction = "back_to_front" if mode in {"learn", "test"} and position % 4 == 3 else "front_to_back"
        prompt, expected = (
            (card.back, card.front) if item_direction == "back_to_front"
            else (card.front, card.back)
        )
        all_answers = [
            candidate.front if item_direction == "back_to_front" else candidate.back
            for candidate in cards
        ]
        if mode == "flashcards":
            question_type, options = "self_grade", []
        elif mode == "learn":
            question_type = "multiple_choice" if card.repetition_count == 0 or position % 3 == 0 else "written"
            options = _options(expected, all_answers, rng) if question_type == "multiple_choice" else []
        elif mode == "test":
            configured = question_types or ["multiple_choice", "written", "true_false", "fill_blank"]
            question_type = configured[position % len(configured)]
            options = _options(expected, all_answers, rng) if question_type == "multiple_choice" else (
                ["true", "false"] if question_type == "true_false" else [])
            if question_type == "true_false":
                shown = expected if position % 2 == 0 else next((x for x in all_answers if x != expected), expected)
                prompt = f"{prompt}\n{shown}"
                expected = "true" if shown == expected else "false"
        elif mode == "match":
            question_type, options = "matching", []
        else:
            question_type, options = "target", _options(expected, all_answers, rng)
        items.append({
            "card_id": card.id, "position": position, "direction": item_direction,
            "question_type": question_type, "prompt": prompt, "correct_answer": expected,
            "options": options,
        })
    if mode == "learn":
        weak = [item for item in items if next(c for c in chosen if c.id == item["card_id"]).weakness_score >= 35]
        for item in weak[:max(0, min(3, len(items) // 3))]:
            repeated = dict(item)
            repeated["position"] = len(items)
            repeated["question_type"] = "written"
            repeated["options"] = []
            items.append(repeated)
    return items


def _options(expected: str, answers: list[str], rng: random.Random) -> list[str]:
    distractors = [answer for answer in dict.fromkeys(answers) if answer != expected]
    rng.shuffle(distractors)
    values = [expected, *distractors[:3]]
    rng.shuffle(values)
    return values


def game_score(mode: str, correct: int, incorrect: int, active_seconds: int, combo: int = 0) -> int:
    base = correct * (120 if mode == "match" else 100)
    penalty = incorrect * 25
    speed = max(0, 300 - max(0, active_seconds)) if correct else 0
    return max(0, base - penalty + speed + min(500, combo * 20))

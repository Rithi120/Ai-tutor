"""Pure question-building, checking, scoring and mastery helpers for flashcard modes."""

from __future__ import annotations

import json
import random
import re
from typing import Any


MODES = {"flashcards", "learn", "test", "match", "blast", "blocks"}
WRITTEN_TYPES = {"written", "fill_blank", "front_to_back", "back_to_front"}
# A Projektmodus key-terms card (learnova/projects/terms.py) asks a question on its front
# ("What does the term “Pflicht” mean?"). Back to front it must ask for the bare term, never
# for that sentence: `reverse_answer` holds the term. Cards tagged ONE_WAY (gap texts,
# "which term fits this example?") have no sensible reverse and are always asked front to
# back. Cards a student made have neither and behave exactly as before.
ONE_WAY = "one_way"
# A card whose answer is the student's own wording ("Explain X in your own words"): an exact
# match grades a correct paraphrase wrong, so it is self-graded where a mode grades, and
# left out of the modes that need one exact answer (test, match, blast, blocks). Project
# cards synced before the tag existed are recognised by their origin variant.
OWN_WORDS = "own_words"
_OWN_WORDS_VARIANT = "term_own_words"
# Project card variants whose back is a bare term (gap text, "which term fits this example?"):
# their answers are terms, the others' are explanations. Options are drawn from one kind.
_TERM_ANSWER_VARIANTS = frozenset({"term_fill", "term_example"})
# A vocabulary entry's forms ("go – went – gone"): neither a term nor an explanation. Any
# other entry's forms as a choice are given away by the prompt ("Give the forms of “go”"),
# so these cards are always asked in writing.
_FORMS_VARIANT = "term_forms"


def card_variant(card: Any) -> str:
    """The project card variant ("term", "term_choice", ...) from "<topic>:<variant>:<key>", or ""."""

    parts = str(getattr(card, "origin_key", "") or "").split(":")
    return parts[1] if len(parts) >= 3 else ""


def own_words(card: Any) -> bool:
    return OWN_WORDS in card_tags(card) or card_variant(card) == _OWN_WORDS_VARIANT


def _answer_kind(card: Any) -> str:
    variant = card_variant(card)
    if not variant:
        return ""
    if variant == _FORMS_VARIANT:
        return "forms"
    return "term" if variant in _TERM_ANSWER_VARIANTS else "explanation"


def _own_options(card: Any, expected: str) -> list[str]:
    """A multiple-choice card's own options (the definitions of the same topic's terms that
    the card was built with), when they hold the expected answer."""

    if getattr(card, "type", "") != "multiple_choice":
        return []
    try:
        options = json.loads(getattr(card, "options_json", None) or "[]")
    except (TypeError, ValueError):
        return []
    values = [str(option) for option in options if isinstance(option, str) and option.strip()] \
        if isinstance(options, list) else []
    return values if expected in values and len(values) >= 2 else []


def reverse_side(card: Any) -> str:
    """What answers a card asked back to front: its reverse_answer, else its front."""

    return str(getattr(card, "reverse_answer", "") or "").strip() or card.front


def one_way(card: Any) -> bool:
    return ONE_WAY in card_tags(card)


def normalize_answer(value: Any, *, punctuation_tolerant: bool = True) -> str:
    text = " ".join(str(value or "").strip().casefold().split())
    if punctuation_tolerant:
        text = re.sub(r"[.!?,;:]+$", "", text)
    return text


# A list of forms ("go – went – gone", "1914 – 1918"): spaced dashes or en/em dashes between
# at most three-word parts. A phone keyboard has no en dash, so such an answer is compared
# form by form, whatever the student typed between them ("go went gone", "go, went, gone").
# Forms are words (or years): "x = –3" and "a² – b²" hold a minus sign, which is meaning.
_FORM_DASHES = re.compile(r"\s+[-–—]\s+|\s*[–—]\s*")
_TYPED_SEPARATORS = re.compile(r"[\s,;/–—-]+")
_YEAR = re.compile(r"\d{3,4}")


# Alternatives of one form joined by a slash ("was/were", "he/she/it"): words, never a
# fraction or a unit ("a/b", "km/h").
_SLASHED_WORDS = re.compile(r"[^\W\d_]{2,}(?:/[^\W\d_]{2,})+")


def _is_form(part: str) -> bool:
    if _YEAR.fullmatch(part):
        return True
    if not part[:1].isalpha() or any(char.isdigit() or char in "=+*^<>≤≥±·×÷" for char in part):
        return False
    return all(_SLASHED_WORDS.fullmatch(word) for word in part.split() if "/" in word)


def _forms(expected: str) -> list[str]:
    parts = [part.strip() for part in _FORM_DASHES.split(str(expected or "")) if part.strip()]
    if len(parts) < 2 or not all(len(part.split()) <= 3 for part in parts):
        return []
    # Lone letters on every side of a dash are algebra ("a – b", "x – y"); a one-letter
    # word among longer ones is a form ("I – me – my").
    if all(len(part) == 1 for part in parts):
        return []
    if not (all(_is_form(part) for part in parts)
            and (all(_YEAR.fullmatch(part) for part in parts) or not any(_YEAR.fullmatch(part) for part in parts))):
        return []
    return parts


def answer_is_correct(student: Any, expected: str, question_type: str) -> bool:
    strict = question_type in {"formula", "date", "technical"}
    if normalize_answer(student, punctuation_tolerant=not strict) == normalize_answer(
            expected, punctuation_tolerant=not strict):
        return True
    if strict or not _forms(expected):
        return False
    words = [normalize_answer(word) for word in _TYPED_SEPARATORS.split(str(expected)) if word]
    typed = [normalize_answer(word) for word in _TYPED_SEPARATORS.split(str(student or "")) if word]
    return bool(words) and typed == words


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


def card_tags(card: Any) -> list[str]:
    try:
        tags = json.loads(getattr(card, "tags_json", None) or "[]")
    except (TypeError, ValueError):
        return []
    return [str(tag) for tag in tags] if isinstance(tags, list) else []


def select_cards(cards: list[Any], objective: str, now, limit: int, *, mode: str | None = None) -> list[Any]:
    """The cards of a session, weakest first, at most `limit`.

    With `mode`, the cards that mode would leave out (build_items) are left out before the
    limit, not after it: a project set's first cards are one term's definition and its
    practice variants, and Match on the first six used to start with two pairs."""

    selected = list(cards)
    if objective == "due":
        selected = [card for card in selected if card.next_review_at <= now]
    elif objective == "weak":
        selected = [card for card in selected if card.weakness_score >= 35 or card.incorrect_count > card.correct_count]
    elif objective == "starred":
        selected = [card for card in selected if card.starred]
    elif objective == "new":
        selected = [card for card in selected if card.repetition_count == 0]
    elif objective == "words":
        # Vocabulary cards carry their kind as a tag (see vocabulary.card_variants):
        # "words" is everything that is not a sentence, so untagged cards count as words.
        selected = [card for card in selected if "sentence" not in card_tags(card)]
    elif objective == "sentences":
        selected = [card for card in selected if "sentence" in card_tags(card)]
    selected.sort(key=lambda card: (-float(card.weakness_score or 0), card.id))
    if mode in {"test", "match", "blast", "blocks"}:
        selected = [card for card in selected if not own_words(card)] or selected
    if mode == "match":
        selected = _distinct_pairs(selected)
    return selected[:max(1, limit)]


def build_items(
    cards: list[Any], mode: str, seed: int, count: int, *,
    direction: str = "mixed", question_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    chosen = list(cards)
    rng.shuffle(chosen)
    if mode in {"test", "match", "blast", "blocks"}:
        # One exact answer is needed here; an own-words card has none.
        exact = [card for card in chosen if not own_words(card)]
        chosen = exact or chosen
    if mode == "match":
        chosen = _distinct_pairs(chosen)
    chosen = chosen[:max(1, count)]
    items = []
    for position, card in enumerate(chosen):
        item_direction = direction
        if one_way(card):
            item_direction = "front_to_back"
        elif item_direction == "mixed":
            if mode == "flashcards":
                # The student chose "Mixed" in the session settings: alternate the sides
                # evenly, so a vocabulary set is asked in both directions.
                item_direction = "back_to_front" if position % 2 else "front_to_back"
            else:
                item_direction = "back_to_front" if mode in {"learn", "test"} and position % 4 == 3 else "front_to_back"
        prompt, expected = (
            (card.back, reverse_side(card)) if item_direction == "back_to_front"
            else (card.front, card.back)
        )
        if mode == "match" and item_direction == "front_to_back" and not one_way(card):
            # Matching pairs a term with its meaning; the question sentence on the front of
            # a key-terms card would make every tile start with "What does the term ...".
            prompt = reverse_side(card)
        # Options are answers of the same kind: bare terms when the terms are asked for, and
        # front to back a project card's definition among definitions, never among terms.
        kind = _answer_kind(card)
        if item_direction == "back_to_front":
            all_answers = [reverse_side(candidate) for candidate in cards]
        else:
            same = [candidate.back for candidate in cards if _answer_kind(candidate) == kind]
            all_answers = same if len(set(same) - {expected}) >= 3 else [candidate.back for candidate in cards]
        own_options = _own_options(card, expected) if item_direction == "front_to_back" else []
        if mode == "flashcards" or (mode == "learn" and own_words(card)):
            question_type, options = "self_grade", []
        elif mode in {"learn", "test"} and item_direction == "front_to_back" and kind == "forms":
            question_type, options = "written", []
        elif mode == "learn":
            question_type = "multiple_choice" if card.repetition_count == 0 or position % 3 == 0 else "written"
            options = (own_options or _options(expected, all_answers, rng)) if question_type == "multiple_choice" else []
        elif mode == "test":
            configured = question_types or ["multiple_choice", "written", "true_false", "fill_blank"]
            question_type = configured[position % len(configured)]
            options = (own_options or _options(expected, all_answers, rng)) if question_type == "multiple_choice" else (
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
            if item["question_type"] != "self_grade":
                repeated["question_type"] = "written"
            repeated["options"] = []
            items.append(repeated)
    return items


def _match_tiles(card: Any) -> tuple[str, str]:
    prompt = card.front if one_way(card) else reverse_side(card)
    return normalize_answer(prompt), normalize_answer(card.back)


def _distinct_pairs(cards: list[Any]) -> list[Any]:
    """Cards whose match tiles are all different. A project set holds, per term, the
    definition card and practice variants with the same term or the same definition
    ("Werte" twice, its definition three times); two identical tiles make a correct match
    count as a miss. The first card of each tile wins, in the session's shuffled order,
    a term's own definition card before its variants."""

    ordered = sorted(cards, key=lambda card: card_variant(card) not in ("", "term"))
    seen: set[str] = set()
    kept = []
    for card in ordered:
        prompt, answer = _match_tiles(card)
        if prompt in seen or answer in seen or prompt == answer:
            continue
        seen.update((prompt, answer))
        kept.append(card)
    if not kept:
        return cards
    order = {id(card): index for index, card in enumerate(cards)}
    return sorted(kept, key=lambda card: order[id(card)])


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

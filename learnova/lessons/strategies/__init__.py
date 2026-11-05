"""Subject teaching strategies: which teaching method a lesson in a subject follows.

`strategy_for(subject)` accepts anything a project row may hold ("Ethik", "Mathe",
"Science and Technology", free text) and always returns a strategy: unknown subjects get
`general`, other languages get `languages`. `tutor_rules_block` is the short subject
method that every tutor prompt (lesson chat, grading) carries; the full teaching sequence
for a lesson prompt is built by `learnova.lessons.lesson_plan`.
"""

from __future__ import annotations

from ..subjects import canonical_subject, is_language_subject, subject_parts
from .base import (
    ALL_MODES, EXPLANATION_KINDS, ITEM_KINDS, KNOWLEDGE_STATES, PART_KINDS, PLAN_MODES, QUESTION_TYPES,
    SERVER_PART_KINDS, LessonStep, SubjectStrategy, combine,
)
from .ethics import ETHICS, RELIGION
from .geography import GEOGRAPHY
from .german import GERMAN
from .history import HISTORY
from .languages import LANGUAGES, LATIN
from .mathematics import MATHEMATICS
from .other_subjects import ART, COMPUTER_SCIENCE, GENERAL, MUSIC, PHYSICAL_EDUCATION, TECHNOLOGY
from .politics import POLITICS
from .sciences import PHYSICS, SCIENCES

__all__ = [
    "ALL_MODES", "EXPLANATION_KINDS", "ITEM_KINDS", "KNOWLEDGE_STATES", "PART_KINDS", "PLAN_MODES",
    "QUESTION_TYPES", "REGISTRY", "SERVER_PART_KINDS", "SUBJECT_STRATEGY_KEYS", "LessonStep", "SubjectStrategy",
    "combine", "strategy_by_key", "strategy_for", "strategy_key_for", "tutor_rules_block",
]

# NwT is taught as one subject but is both: experiments and principles from the sciences,
# function, safety and application from technology.
NWT = combine("nwt", "Science and Technology", SCIENCES, TECHNOLOGY)

REGISTRY: dict[str, SubjectStrategy] = {
    strategy.key: strategy
    for strategy in (LANGUAGES, LATIN, MATHEMATICS, SCIENCES, PHYSICS, NWT, ETHICS, RELIGION, GERMAN, HISTORY,
                     GEOGRAPHY, POLITICS, COMPUTER_SCIENCE, TECHNOLOGY, MUSIC, ART, PHYSICAL_EDUCATION,
                     GENERAL)
}

# Canonical subject (learnova.lessons.subjects) -> strategy key.
SUBJECT_STRATEGY_KEYS: dict[str, str] = {
    "Mathematics": "mathematics",
    "German": "german",
    "German as a Foreign Language": "languages",
    "English": "languages",
    "French": "languages",
    "Spanish": "languages",
    "Latin": "latin",
    "Physics": "physics",
    "Chemistry": "sciences",
    "Biology": "sciences",
    "Science and Technology": "nwt",
    "History": "history",
    "Geography": "geography",
    "Politics": "politics",
    "Economics": "politics",
    "Ethics": "ethics",
    "Philosophy": "ethics",
    "Religion": "religion",
    "Computer Science": "computer_science",
    "Technology": "technology",
    "Music": "music",
    "Art": "art",
    "Physical Education": "physical_education",
    "Other": "general",
}

TUTOR_RULES_LIMIT = 900


def _single_key(subject: str | None) -> str:
    canonical = canonical_subject(subject)
    key = SUBJECT_STRATEGY_KEYS.get(canonical)
    if key:
        return key
    return "languages" if is_language_subject(canonical) else "general"


def strategy_key_for(subject: str | None) -> str:
    """The strategy key of a subject; a combined subject ("Geschichte/Gemeinschaftskunde",
    Sachunterricht, GWG) gets its parts' keys joined with "+" ("history+politics")."""

    keys = [key for key in dict.fromkeys(_single_key(part) for part in subject_parts(subject))
            if key != "general"]
    if len(keys) >= 2:
        return "+".join(keys)
    return keys[0] if keys else _single_key(subject)


_COMBINED: dict[str, SubjectStrategy] = {}


def strategy_by_key(key: str | None) -> SubjectStrategy | None:
    """A registered strategy, or the combination a "+" key names; None for an unknown key.

    Combinations are built once; combine() keeps the first strategy's order and slots the
    others' extra parts in before the practice phase, as for NwT."""

    key = str(key or "")
    if key in REGISTRY:
        return REGISTRY[key]
    parts = key.split("+")
    if len(parts) < 2 or any(part not in REGISTRY for part in parts):
        return None
    if key not in _COMBINED:
        strategies = [REGISTRY[part] for part in parts]
        _COMBINED[key] = combine(key, " / ".join(strategy.name for strategy in strategies), *strategies)
    return _COMBINED[key]


def strategy_for(subject: str | None) -> SubjectStrategy:
    """The teaching strategy for a subject as stored or typed; never fails."""

    return strategy_by_key(strategy_key_for(subject)) or REGISTRY["general"]


def _fit(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return cut + "…"


def tutor_rules_block(strategy: SubjectStrategy) -> str:
    """The subject method in at most 900 characters, for the tutor's system instructions.

    The wrong-answer rule is never cut, so a long subject still tells the tutor how to
    react to a mistake. When space runs out, the priorities are shortened first (down to
    two; a combined subject like NwT has nine), then rules are dropped from the end:
    concrete rules steer the tutor more than a list of focus phrases."""

    tail = _fit(f" Wrong answers: {strategy.feedback_rule}", TUTOR_RULES_LIMIT // 2)
    priorities, rules = list(strategy.priorities), list(strategy.rules)
    while True:
        head = f"SUBJECT METHOD ({strategy.name}): priorities: {'; '.join(priorities)}."
        middle = f" Rules: {' '.join(rules)}" if rules else ""
        if len(head) + len(middle) + len(tail) <= TUTOR_RULES_LIMIT:
            return head + middle + tail
        if len(priorities) > 2:
            priorities.pop()
        elif rules:
            rules.pop()
        elif len(priorities) > 1:
            priorities.pop()
        else:
            return _fit(head, TUTOR_RULES_LIMIT - len(tail)) + tail

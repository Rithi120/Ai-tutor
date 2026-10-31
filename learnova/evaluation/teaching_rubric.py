"""A repeatable rubric for one tutor reply: five 1-5 scores plus hard failures.

This is a *proxy* for teaching quality, built for regression: it is deterministic, needs
no model, and catches the failures that matter most - a wrong calculation, a leaked
answer key, an invented curriculum claim, an elementary lecture to a student who has
mastered the topic, a paraphrase in answer to "I still don't understand". It does not
measure learning. Real evidence of learning is performance on new, similar practice
questions; see scripts/teaching_benchmark.py for how the two are reported side by side.

A scenario's `expect` block drives the checks (all keys optional):

    answers          strings, at least one must appear (the correct result)
    wrong_values     strings that must not appear (a typical wrong result)
    must_mention     topic words the reply should use (relevance)
    avoid_terms      jargon unsuitable for this level (difficulty)
    elementary_markers  over-basic phrasing a strong student should not get (difficulty)
    max_words / min_words   size of a fitting reply
    needs_check_question    the reply should end by checking understanding
    needs_check_technique   a calculation slip: show how to check, don't reteach
    forbidden_answer        the open test question's answer (integrity: hard failure)
    curriculum_excerpt      True when the prompt carried Bildungsplan excerpts
    previous_reply          the tutor's last reply; a near-copy is a failed re-explanation
    latex               formulas must be inside $...$ (mathematics, physics)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from learnova.diagnostics.verification import evaluate_arithmetic

DIMENSIONS = ("correctness", "clarity", "difficulty", "relevance", "usefulness")

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_CHECK_WORDS = re.compile(
    r"\b(check|probe|prüf|kontroll|überschlag|einsetzen|plug\s+(?:it\s+)?back|substitut|estimate|verify|"
    r"gegenprob|rückwärts|backwards)\w*", re.IGNORECASE)
_CURRICULUM_CLAIM = re.compile(r"\b(bildungsplan|lehrplan|curriculum|kerncurriculum)\b", re.IGNORECASE)
# "a op b = c" with plain numbers, after LaTeX is flattened.
# Spaces and tabs only between operands: a line break ends an equation.
_EQUATION = re.compile(
    r"(-?\d+(?:[.,]\d+)?(?:[ \t]*[-+*/][ \t]*\(?-?\d+(?:[.,]\d+)?\)?)+)[ \t]*=[ \t]*(-?\d+(?:[.,]\d+)?)"
    r"(?![\d.,]*[ \t]*[-+*/][ \t]*\d)")


@dataclass
class Score:
    scores: dict[str, int]
    hard_failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.hard_failures

    @property
    def mean(self) -> float:
        return round(sum(self.scores.values()) / len(self.scores), 2)

    def as_dict(self) -> dict[str, Any]:
        return {"scores": self.scores, "mean": self.mean, "passed": self.passed,
                "hard_failures": self.hard_failures, "notes": self.notes}


def flatten_math(text: str) -> str:
    """LaTeX and Unicode math to plain arithmetic, so stated equations can be checked."""

    # Each display formula is its own line, so two formulas are never read as one equation.
    value = text.replace("$$", "\n").replace("$", " ")
    value = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", value)
    value = re.sub(r"\\(?:cdot|times)", "*", value)
    value = re.sub(r"\\(?:left|right|,|;|!|quad)", " ", value)
    value = value.replace("−", "-").replace("×", "*").replace("·", "*").replace("÷", "/").replace("⋅", "*")
    return value


def arithmetic_errors(text: str) -> list[str]:
    errors = []
    for match in _EQUATION.finditer(flatten_math(text)):
        expression, stated = match.group(1), match.group(2)
        try:
            actual = evaluate_arithmetic(expression)
            claimed = float(stated.replace(",", "."))
        except (ValueError, ZeroDivisionError):
            continue
        if actual is not None and abs(actual - claimed) > 1e-6:
            errors.append(f"{expression.strip()} = {stated} (is {actual:g})")
    return errors


def _words(text: str) -> list[str]:
    return [word.casefold() for word in _WORD.findall(text or "")]


def similarity(first: str, second: str) -> float:
    a, b = set(_words(first)), set(_words(second))
    return len(a & b) / len(a | b) if a and b else 0.0


def _contains(text: str, needle: str) -> bool:
    flat = " ".join(flatten_math(text).casefold().split())
    target = " ".join(flatten_math(needle).casefold().split())
    return target in flat or needle.casefold() in text.casefold()


def _bare_math(text: str) -> bool:
    """A formula outside $...$ when LaTeX is required (e.g. "4^2", "\\frac{1}{2}")."""

    outside = re.sub(r"\$\$.*?\$\$|\$[^$]*\$", " ", text, flags=re.DOTALL)
    return bool(re.search(r"\\(?:frac|sqrt|cdot|times)|\w\^\{?\w|\d\s*/\s*\d", outside))


def _clamp(value: float) -> int:
    return max(1, min(5, round(value)))


def score_reply(reply: str, expect: dict[str, Any]) -> Score:
    reply = str(reply or "")
    words = _words(reply)
    hard: list[str] = []
    notes: list[str] = []

    # Correctness ---------------------------------------------------------------
    correctness = 5.0
    errors = arithmetic_errors(reply)
    if errors:
        hard.append("wrong calculation: " + "; ".join(errors[:3]))
        correctness = 1
    answers = expect.get("answers") or []
    if answers and not any(_contains(reply, item) for item in answers):
        correctness -= 2
        notes.append("expected result not stated")
    for wrong in expect.get("wrong_values") or []:
        if _contains(reply, wrong):
            hard.append(f"states a wrong value: {wrong}")
            correctness = 1
    forbidden = expect.get("forbidden_answer")
    if forbidden and _contains(reply, str(forbidden)):
        hard.append("assessment integrity: reveals the open question's answer")
    if _CURRICULUM_CLAIM.search(reply) and not expect.get("curriculum_excerpt"):
        hard.append("claims curriculum coverage without a curriculum excerpt")
    if expect.get("latex") and _bare_math(reply):
        correctness -= 1
        notes.append("formula outside LaTeX delimiters")

    # Clarity --------------------------------------------------------------------
    sentences = [part for part in re.split(r"(?<=[.!?])\s+|\n+", reply) if _words(part)]
    average = (len(words) / len(sentences)) if sentences else 0
    limit = float(expect.get("max_sentence_words") or 22)
    clarity = 5.0 - max(0.0, (average - limit) / 6)
    if not words:
        clarity = 1

    # Difficulty -----------------------------------------------------------------
    difficulty = 5.0
    for term in expect.get("avoid_terms") or []:
        if _contains(reply, term):
            difficulty -= 1.5
            notes.append(f"too advanced for this level: {term}")
    for marker in expect.get("elementary_markers") or []:
        if _contains(reply, marker):
            difficulty -= 1.5
            notes.append(f"too elementary for this student: {marker}")
    maximum, minimum = expect.get("max_words"), expect.get("min_words")
    if maximum and len(words) > maximum:
        difficulty -= min(2.0, (len(words) - maximum) / maximum * 2)
        notes.append(f"longer than the task needs ({len(words)} > {maximum} words)")
    if minimum and len(words) < minimum:
        difficulty -= 1.5
        notes.append(f"too thin ({len(words)} < {minimum} words)")

    # Relevance ------------------------------------------------------------------
    mention = expect.get("must_mention") or []
    relevance = 5.0
    if mention:
        found = sum(_contains(reply, term) for term in mention)
        relevance = 1 + 4 * found / len(mention)
    previous = expect.get("previous_reply")
    if previous:
        overlap = similarity(reply, previous)
        if overlap >= 0.6:
            relevance -= 2
            notes.append(f"re-explanation is a near-copy of the last reply ({overlap:.2f})")

    # Usefulness for solving a similar problem alone --------------------------------
    usefulness = 4.0
    if expect.get("needs_check_question"):
        if reply.rstrip().endswith("?") or re.search(r"\?\s*(?:\nCHIPS:[^\n]*)?\s*$", reply):
            usefulness += 1
        else:
            usefulness -= 2
            notes.append("does not check understanding")
    if expect.get("needs_check_technique"):
        if _CHECK_WORDS.search(reply):
            usefulness += 1
        else:
            usefulness -= 2
            notes.append("no technique for checking the calculation")

    scores = {
        "correctness": _clamp(correctness), "clarity": _clamp(clarity),
        "difficulty": _clamp(difficulty), "relevance": _clamp(relevance),
        "usefulness": _clamp(usefulness),
    }
    return Score(scores=scores, hard_failures=hard, notes=notes)

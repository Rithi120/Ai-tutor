"""The machine-readable question specification and its deterministic validator.

A generated question is never shown until code has checked it. The validator is
subject-agnostic: it enforces structure, alignment with what the planner asked for,
answerability and non-duplication for every subject, and additionally verifies the
answer key arithmetically when the item is a closed numeric calculation.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from typing import Any

from .planner import MAX_DIFFICULTY, MIN_DIFFICULTY, similarity
from .taxonomy import COGNITIVE_DEMAND_SET
from .verification import evaluate_arithmetic, numbers_match, parse_number

# Two prompts this similar are treated as the same question.
DUPLICATE_SIMILARITY = 0.75

# Question types that must ship a fixed option set, and how many options are sensible.
CHOICE_TYPES = {"multiple_choice", "dropdown", "checkboxes", "ordering", "matching", "true_false"}
OPEN_TYPES = {"text", "short_answer", "explanation", "calculation", "fill_blank"}
ALL_TYPES = CHOICE_TYPES | OPEN_TYPES

# Wording that makes an item unanswerable on its own because it points at context the
# student cannot see. Checked as whole words so "the value of x" is not flagged.
_DANGLING_REFERENCES = (
    "the above", "the following diagram", "as shown", "the previous question",
    "the passage above", "see figure", "the attached", "refer to the image",
)
_MIN_PROMPT_WORDS = 4
_MAX_PROMPT_CHARACTERS = 1200


@dataclass(frozen=True)
class QuestionSpec:
    """Everything a question must declare before it can be rendered."""

    learning_objective: str
    concept: str
    prerequisites: list[str] = field(default_factory=list)
    difficulty: int = 1
    cognitive_demand: str = "apply"
    question_type: str = "short_answer"
    target_misconception: str = ""
    diagnostic_purpose: str = ""
    requires_working_steps: bool = False
    avoid_prompts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "learning_objective": self.learning_objective,
            "concept": self.concept,
            "prerequisites": self.prerequisites,
            "difficulty": self.difficulty,
            "cognitive_demand": self.cognitive_demand,
            "question_type": self.question_type,
            "target_misconception": self.target_misconception,
            "diagnostic_purpose": self.diagnostic_purpose,
            "requires_working_steps": self.requires_working_steps,
        }


@dataclass(frozen=True)
class QuestionValidation:
    """The outcome of validating one generated question."""

    valid: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        return "; ".join(self.failures)[:200] or "ok"


def spec_from_constraints(
    constraints: dict[str, Any],
    *,
    fallback_concept: str = "",
    fallback_type: str = "short_answer",
) -> QuestionSpec:
    """Build the specification the generator must satisfy from the planner's constraints."""

    constraints = constraints or {}
    demand = str(constraints.get("cognitive_demand") or "").strip().lower()
    question_type = str(constraints.get("question_type") or "").strip().lower()
    difficulty = _as_int(constraints.get("difficulty"))
    if difficulty is None:
        difficulty = MIN_DIFFICULTY
    prerequisite = str(constraints.get("prerequisite_focus") or "").strip()
    return QuestionSpec(
        learning_objective=str(constraints.get("learning_objective") or constraints.get("purpose") or "").strip()[:400],
        concept=str(constraints.get("concept") or fallback_concept).strip()[:255],
        prerequisites=[prerequisite] if prerequisite else [],
        difficulty=max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, difficulty)),
        cognitive_demand=demand if demand in COGNITIVE_DEMAND_SET else "apply",
        question_type=question_type if question_type in ALL_TYPES else fallback_type,
        target_misconception=str(constraints.get("target_misconception") or "").strip()[:400],
        diagnostic_purpose=str(constraints.get("purpose") or "").strip()[:400],
        requires_working_steps=bool(constraints.get("requires_working_steps")),
        avoid_prompts=[str(item) for item in (constraints.get("avoid_prompts") or []) if str(item).strip()][:8],
    )


def _as_int(value: Any) -> int | None:
    """Coerce a model-supplied value to an int, or None when it is not one."""

    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _option_ids(options: Any) -> list[str]:
    ids: list[str] = []
    for index, option in enumerate(options if isinstance(options, list) else []):
        if isinstance(option, dict):
            ids.append(str(option.get("id") or index))
        else:
            ids.append(str(option))
    return ids


def _answer_tokens(expected: Any) -> list[str]:
    if isinstance(expected, (list, tuple)):
        return [str(item).strip() for item in expected if str(item).strip()]
    text = str(expected if expected is not None else "").strip()
    return [text] if text else []


def validate_question(
    question: Any,
    spec: QuestionSpec,
    *,
    recent_prompts: list[str] | None = None,
    strict_type: bool = True,
) -> QuestionValidation:
    """Check a generated question against its specification before it reaches a student.

    Failures block the question (the caller regenerates or falls back); warnings are
    recorded for telemetry but do not block, because over-strict rejection wastes calls.
    """

    failures: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}
    recent_prompts = list(recent_prompts or []) + list(spec.avoid_prompts)

    if not isinstance(question, dict):
        return QuestionValidation(False, ["question is not an object"], [], {"structure": False})

    prompt = str(question.get("prompt") or "").strip()
    checks["prompt_present"] = bool(prompt)
    if not prompt:
        failures.append("prompt is empty")
    elif len(prompt) > _MAX_PROMPT_CHARACTERS:
        failures.append("prompt is too long to be answerable in one step")
    elif len(prompt.split()) < _MIN_PROMPT_WORDS:
        failures.append("prompt is too short to be answerable")

    lowered = prompt.casefold()
    dangling = [phrase for phrase in _DANGLING_REFERENCES if phrase in lowered]
    checks["self_contained"] = not dangling
    if dangling:
        failures.append(f"prompt refers to material the student cannot see ({dangling[0]})")

    question_type = str(question.get("type") or question.get("question_type") or "").strip().lower()
    checks["type_supported"] = question_type in ALL_TYPES
    if question_type not in ALL_TYPES:
        failures.append(f"unsupported question type {question_type!r}")
    elif strict_type and spec.question_type and question_type != spec.question_type:
        # The session's question sequence fixes the type; a mismatch breaks the UI control.
        failures.append(f"question type {question_type!r} does not match the requested {spec.question_type!r}")

    raw_difficulty = question.get("difficulty")
    if isinstance(raw_difficulty, str):
        raw_difficulty = {"easy": 1, "medium": 2, "hard": 3}.get(raw_difficulty.strip().lower())
    difficulty = _as_int(raw_difficulty)
    checks["difficulty_in_bounds"] = (
        difficulty is not None and MIN_DIFFICULTY <= difficulty <= MAX_DIFFICULTY)
    if difficulty is None or not checks["difficulty_in_bounds"]:
        failures.append("difficulty is missing or out of bounds")
    elif abs(difficulty - spec.difficulty) > 1:
        failures.append(
            f"difficulty {difficulty} is more than one level from the requested {spec.difficulty}")

    concept = str(question.get("concept") or "").strip()
    checks["concept_aligned"] = bool(concept) and (
        not spec.concept or similarity(concept, spec.concept) > 0 or concept.casefold() == spec.concept.casefold()
    )
    if not concept:
        failures.append("concept tag is missing")
    elif spec.concept and not checks["concept_aligned"]:
        failures.append(f"concept {concept!r} is not the requested {spec.concept!r}")

    expected = question.get("expected_answer")
    tokens = _answer_tokens(expected)
    checks["answer_present"] = bool(tokens)
    if not tokens:
        failures.append("expected_answer is missing")

    options = question.get("options")
    if question_type in CHOICE_TYPES:
        ids = _option_ids(options)
        checks["options_present"] = len(ids) >= 2
        if len(ids) < 2:
            failures.append("a choice question needs at least two options")
        elif len(ids) != len(set(ids)):
            failures.append("options contain duplicate identifiers")
        labels = [
            str(option.get("label") if isinstance(option, dict) else option).strip().casefold()
            for option in (options if isinstance(options, list) else [])
        ]
        if labels and len(labels) != len(set(labels)):
            failures.append("options contain duplicate labels")
        known = {value.casefold() for value in ids} | {value for value in labels if value}
        unmatched = [token for token in tokens if token.casefold() not in known]
        checks["answer_in_options"] = not unmatched
        if unmatched:
            failures.append("expected_answer does not identify one of the options")
        if question_type == "checkboxes" and len(tokens) < 2:
            warnings.append("a checkbox question normally has more than one correct option")
    else:
        checks["options_present"] = True
        if isinstance(options, list) and options:
            warnings.append("an open question should not ship options")

    # Answer-key verification: when the prompt is a bare calculation, recompute it.
    checks["answer_key_verified"] = True
    if question_type in ("calculation", "short_answer", "text", "fill_blank"):
        computed = evaluate_arithmetic(_calculation_expression(prompt))
        stated = parse_number(tokens[0]) if tokens else None
        if computed is not None and stated is not None:
            checks["answer_key_verified"] = numbers_match(stated, computed)
            if not checks["answer_key_verified"]:
                failures.append("the stated answer does not match the arithmetic in the prompt")

    duplicate_of = next(
        (item for item in recent_prompts if similarity(prompt, item) >= DUPLICATE_SIMILARITY), ""
    )
    checks["not_duplicate"] = not duplicate_of
    if duplicate_of:
        failures.append("question repeats a recently answered question")

    checks["hint_safe"] = not _hint_leaks_answer(
        str(question.get("hint") or ""), tokens, question_type, options)
    if not checks["hint_safe"]:
        failures.append("the hint gives away the answer")

    if spec.requires_working_steps and question_type in CHOICE_TYPES:
        warnings.append("working steps were requested but the question type cannot show them")

    return QuestionValidation(not failures, failures, warnings, checks)


def _correct_option_labels(tokens: list[str], options: Any) -> list[str]:
    """The labels of the options the expected answer selects, by id or by label text."""

    wanted = {token.casefold() for token in tokens}
    labels: list[str] = []
    for index, option in enumerate(options if isinstance(options, list) else []):
        if isinstance(option, dict):
            identifier = str(option.get("id") or index).strip().casefold()
            label = str(option.get("label") or "").strip()
        else:
            identifier = str(option).strip().casefold()
            label = str(option).strip()
        if label and (identifier in wanted or label.casefold() in wanted):
            labels.append(label)
    return labels


def _hint_leaks_answer(hint: str, tokens: list[str], question_type: str, options: Any) -> bool:
    """Whether a hint hands over the answer instead of a way to reach it.

    Matched on whole tokens, so a hint that happens to contain the characters of the
    answer inside a longer word or number is not flagged.
    """

    hint = hint.strip()
    if not hint or not tokens:
        return False
    if question_type in CHOICE_TYPES:
        # Quoting the correct option's wording is a leak. Naming its bare id is not,
        # because single letters appear in ordinary phrasing ("a circle has ...").
        return any(
            len(label) >= 5 and _re.search(rf"\b{_re.escape(label)}\b", hint, flags=_re.IGNORECASE)
            for label in _correct_option_labels(tokens, options)
        )
    answer = str(tokens[0]).strip()
    if len(answer) < 2 and not answer.isdigit():
        return False
    return bool(_re.search(rf"(?<!\w){_re.escape(answer)}(?!\w)", hint, flags=_re.IGNORECASE))


def _calculation_expression(prompt: str) -> str:
    """Pull a bare arithmetic expression out of a prompt, or return an empty string.

    Only used to double-check a generated answer key; anything with words in it is left
    to the model, because a worded problem is not decidable here.
    """

    candidates = _re.findall(r"[-+]?[\d.,]+(?:\s*[-+*/×÷^]\s*[-+]?[\d.,]+)+", prompt)
    if not candidates:
        return ""
    expression = max(candidates, key=len).strip()
    return expression if len(expression) >= 3 else ""


def annotate_question(question: dict[str, Any], spec: QuestionSpec, validation: QuestionValidation) -> dict[str, Any]:
    """Attach the machine-readable specification and the validation record to a question.

    The annotation lives under `spec`/`validation` so existing consumers that read
    `prompt`, `options` and `expected_answer` are unaffected.
    """

    annotated = dict(question)
    annotated["spec"] = spec.as_dict()
    annotated["validation"] = {
        "valid": validation.valid,
        "checks": validation.checks,
        "failures": validation.failures,
        "warnings": validation.warnings,
    }
    return annotated


def public_question(question: dict[str, Any]) -> dict[str, Any]:
    """Strip everything a student must not see before sending a question to the browser."""

    hidden = {"expected_answer", "solution_steps", "rubric", "validation"}
    visible = {key: value for key, value in question.items() if key not in hidden}
    spec = visible.get("spec")
    if isinstance(spec, dict):
        # The purpose is useful to show; the misconception being probed is not.
        visible["spec"] = {
            "learning_objective": spec.get("learning_objective", ""),
            "cognitive_demand": spec.get("cognitive_demand", ""),
            "difficulty": spec.get("difficulty", 1),
        }
    return visible

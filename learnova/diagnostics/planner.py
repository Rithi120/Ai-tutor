"""The adaptive policy: diagnosis + knowledge state + history -> one next learning action.

Pure and deterministic on purpose. The LLM interprets and generates; it never decides
what happens next. Every branch below is reachable from a unit test, and the two rules
the brief calls out explicitly are enforced structurally:

* difficulty never rises just because the last answer was correct, and
* difficulty never falls just because the last answer was wrong.

A promotion needs a streak, no hints, enough mastery and low enough uncertainty. A
demotion needs a streak *and* an understanding-level cause, never an execution slip.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .knowledge import due_for_retrieval, is_unassessed, recurring_misconception_count
from .taxonomy import DIFFICULTY_ACTIONS, EXECUTION_TAGS, UNDERSTANDING_TAGS

MIN_DIFFICULTY = 1
MAX_DIFFICULTY = 3
MAX_DIFFICULTY_STEP = 1

# A correct answer only promotes after this many consecutive correct answers.
PROMOTION_STREAK = 2
# A wrong answer only demotes after this many consecutive incorrect answers.
DEMOTION_STREAK = 2
# Mastery and uncertainty gates for a promotion.
PROMOTION_MASTERY = 65.0
PROMOTION_MAX_UNCERTAINTY = 0.5
# A misconception seen at least this many times is entrenched, not a one-off.
ENTRENCHED_REPEATS = 2
# After this many consecutive undiagnosable responses on one concept, ask for a human.
INSUFFICIENT_EVIDENCE_ESCALATION = 2

_COGNITIVE_ORDER = ("recall", "apply", "analyze", "evaluate", "transfer")


@dataclass(frozen=True)
class NextAction:
    """The planner's complete decision about what the student should do next."""

    action: str
    difficulty: int
    difficulty_delta: int
    reason: str
    constraints: dict[str, Any] = field(default_factory=dict)
    update_mastery: bool = True
    penalise: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "difficulty": self.difficulty,
            "difficulty_delta": self.difficulty_delta,
            "reason": self.reason,
            "constraints": self.constraints,
            "update_mastery": self.update_mastery,
            "penalise": self.penalise,
        }


def _clamp_difficulty(value: Any, fallback: int = 1) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = fallback
    return max(MIN_DIFFICULTY, min(MAX_DIFFICULTY, number))


def _bounded(current: int, delta: int) -> int:
    step = max(-MAX_DIFFICULTY_STEP, min(MAX_DIFFICULTY_STEP, delta))
    return _clamp_difficulty(current + step, current)


def _demand_for(action: str, tag: str, mastery: float) -> str:
    if action in ("prerequisite_reteach", "diagnostic_check", "clarify_instruction"):
        return "recall"
    if action == "transfer_question":
        return "transfer"
    if action == "misconception_contrast":
        return "analyze"
    if action == "increase_difficulty":
        return "analyze" if mastery >= 80 else "apply"
    if tag in EXECUTION_TAGS:
        return "apply"
    return "apply" if mastery >= 40 else "recall"


def _question_type_for(action: str, tag: str) -> str:
    if action == "diagnostic_check":
        # A short constructed response reveals reasoning; a multiple choice invites a guess.
        return "short_answer"
    if action == "misconception_contrast":
        return "multiple_choice"
    if action in ("clarify_instruction", "prerequisite_reteach"):
        return "short_answer"
    if tag == "incomplete_reasoning":
        return "explanation"
    if tag == "arithmetic_or_transcription_error":
        return "calculation"
    return ""


def plan_next_action(
    diagnosis: dict[str, Any],
    state: dict[str, Any],
    *,
    now: datetime,
    history: list[dict[str, Any]] | None = None,
    recent_prompts: list[str] | None = None,
    confirmed_prerequisites: list[str] | None = None,
    hints_used: bool = False,
    objective: str = "",
) -> NextAction:
    """Choose the single most useful next learning action.

    `state` is a `ConceptMastery`-shaped dict (see `knowledge.evidence_summary`).
    `history` holds recent attempts on this concept, newest first, each with at least
    `primary_diagnosis`, `concept` and optionally `next_action`.
    """

    history = history or []
    recent_prompts = recent_prompts or []
    confirmed_prerequisites = confirmed_prerequisites or []

    status = diagnosis.get("correctness_status", "insufficient_evidence")
    primary = diagnosis.get("primary_diagnosis", {})
    tag = primary.get("tag", "insufficient_evidence")
    missing_evidence = bool(diagnosis.get("missing_evidence"))
    model_constraints = diagnosis.get("candidate_question_constraints", {}) or {}

    concept = (
        model_constraints.get("concept")
        or (diagnosis.get("concepts_assessed") or [None])[0]
        or state.get("concept", "")
    )
    current = _clamp_difficulty(state.get("difficulty_level"), 1)
    mastery = float(state.get("mastery_score") or 0.0)
    raw_uncertainty = state.get("uncertainty")
    uncertainty = 1.0 if raw_uncertainty is None else float(raw_uncertainty)
    consecutive_correct = int(state.get("consecutive_correct") or 0)
    consecutive_incorrect = int(state.get("consecutive_incorrect") or 0)

    action, delta, reason = _select(
        tag=tag,
        status=status,
        missing_evidence=missing_evidence,
        state=state,
        history=history,
        concept=concept,
        now=now,
        current=current,
        mastery=mastery,
        uncertainty=uncertainty,
        consecutive_correct=consecutive_correct,
        consecutive_incorrect=consecutive_incorrect,
        hints_used=hints_used,
    )

    # Oscillation guard: never reverse the previous difficulty move on the same concept.
    previous_action = next(
        (str(item.get("next_action") or "") for item in history
         if str(item.get("concept") or "").casefold() == str(concept or "").casefold()),
        "",
    )
    if action in DIFFICULTY_ACTIONS and previous_action in DIFFICULTY_ACTIONS and previous_action != action:
        action, delta = "maintain_difficulty", 0
        reason = "Held the level to avoid swinging the difficulty back and forth."

    difficulty = _bounded(current, delta)
    if difficulty == current and action == "increase_difficulty":
        action, reason = "transfer_question", "Already at the hardest level; applying it somewhere new instead."
    elif difficulty == current and action == "reduce_difficulty":
        action, reason = "prerequisite_reteach", "Already at the easiest level; revisiting the foundation instead."

    prerequisite_focus = ""
    if action == "prerequisite_reteach":
        gaps = [item.get("concept", "") for item in diagnosis.get("prerequisite_gaps", []) if item.get("concept")]
        prerequisite_focus = next((item for item in gaps + confirmed_prerequisites if item), "")

    constraints = {
        "concept": str(concept or "")[:255],
        "prerequisite_focus": prerequisite_focus[:255],
        "difficulty": difficulty,
        "cognitive_demand": _demand_for(action, tag, mastery),
        "question_type": model_constraints.get("question_type") or _question_type_for(action, tag),
        "target_misconception": (
            diagnosis.get("misconception_description", "")
            if action in ("misconception_contrast", "worked_example_then_practice") else ""
        )[:400],
        "purpose": _purpose_for(action, tag, concept, prerequisite_focus, objective)[:400],
        "avoid_prompts": [p for p in recent_prompts if p][:8],
        "learning_objective": (objective or _purpose_for(action, tag, concept, prerequisite_focus, ""))[:400],
        "requires_working_steps": action in ("targeted_practice", "worked_example_then_practice")
        and tag in ("incomplete_reasoning", "procedural_error", "arithmetic_or_transcription_error"),
    }

    # A defective item must not move the knowledge model: the student did nothing wrong.
    # An undiagnosable response is different - the graded score is still real evidence
    # about the concept, so it still counts, just at a heavily reduced evidence weight
    # (see knowledge.observation_weight). Only the *diagnosis* is withheld.
    non_evidential = tag == "question_or_key_flawed"
    return NextAction(
        action=action,
        difficulty=difficulty,
        difficulty_delta=difficulty - current,
        reason=reason,
        constraints=constraints,
        update_mastery=not non_evidential,
        penalise=not non_evidential,
    )


def _select(
    *,
    tag: str,
    status: str,
    missing_evidence: bool,
    state: dict[str, Any],
    history: list[dict[str, Any]],
    concept: str,
    now: datetime,
    current: int,
    mastery: float,
    uncertainty: float,
    consecutive_correct: int,
    consecutive_incorrect: int,
    hints_used: bool,
) -> tuple[str, int, str]:
    """The ordered policy. Returns (action, difficulty delta, reason)."""

    # 1. A defective question is the item's fault, never the student's.
    if tag == "question_or_key_flawed":
        return ("human_review_or_insufficient_evidence", 0,
                "The question or its answer key looks wrong, so this attempt is set aside for review.")

    # 2. Nothing trustworthy to act on: gather evidence, then escalate if it keeps failing.
    if tag == "insufficient_evidence" or (missing_evidence and status == "insufficient_evidence"):
        repeats = recurring_misconception_count(history, concept=concept, tag="insufficient_evidence")
        if repeats >= INSUFFICIENT_EVIDENCE_ESCALATION:
            return ("human_review_or_insufficient_evidence", 0,
                    "Several responses in a row gave too little to work with.")
        return ("diagnostic_check", 0,
                "Not enough in the response to identify a cause, so one short check comes next.")

    # 3. The question was read differently from how it was meant.
    if tag == "interpretation_error":
        return ("clarify_instruction", 0,
                "The response answers a different question from the one asked.")

    # 4. An earlier skill is missing: go back to it rather than repeating this task.
    if tag == "prerequisite_gap":
        return ("prerequisite_reteach", -1,
                "The gap is in an earlier skill, so the next step targets that first.")

    # 5. A misconception: contrast it once it recurs, otherwise model the correct idea.
    if tag == "conceptual_misconception":
        repeats = recurring_misconception_count(history, concept=concept, tag=tag)
        if repeats >= ENTRENCHED_REPEATS:
            return ("misconception_contrast", 0,
                    "The same misunderstanding keeps coming back, so the two ideas are compared directly.")
        return ("worked_example_then_practice", 0,
                "A worked example makes the correct idea visible before practising it.")

    # 6. The method is wrong but the understanding may be intact.
    if tag == "procedural_error":
        repeats = recurring_misconception_count(history, concept=concept, tag=tag)
        if repeats >= ENTRENCHED_REPEATS:
            return ("targeted_practice", 0, "The same step keeps going wrong, so it is practised on its own.")
        return ("worked_example_then_practice", 0, "One worked example should fix the order of the steps.")

    # 7. A slip. Explicitly never a reason to make the work easier.
    if tag == "arithmetic_or_transcription_error":
        return ("targeted_practice", 0,
                "A calculation slip, not a gap in understanding, so the level stays the same.")

    # 8. The reasoning stopped early.
    if tag == "incomplete_reasoning":
        return ("targeted_practice", 0, "The next task asks for the reasoning to be written out in full.")

    # 9. The answer looks like a guess: check it at a lower cognitive demand.
    if tag == "guessing_or_uncertain":
        return ("diagnostic_check", 0, "The answer looks uncertain, so a short recall check comes next.")

    # 10. Partly right: consolidate at the same level.
    if status == "partially_correct" or tag == "partially_correct":
        return ("targeted_practice", 0, "Part of the answer was right; the next task consolidates the rest.")

    # 11. Correct. A promotion has to be earned by a streak, not by one answer.
    if status == "correct" or tag == "correct":
        if consecutive_incorrect >= DEMOTION_STREAK and _understanding_failure(history):
            return ("reduce_difficulty", -1, "Recent conceptual errors outweigh this single correct answer.")
        promotable = (
            consecutive_correct >= PROMOTION_STREAK
            and not hints_used
            and mastery >= PROMOTION_MASTERY
            and uncertainty <= PROMOTION_MAX_UNCERTAINTY
        )
        if promotable and current < MAX_DIFFICULTY:
            return ("increase_difficulty", 1,
                    f"{consecutive_correct} correct in a row without hints, with enough evidence to move up.")
        if promotable and current >= MAX_DIFFICULTY:
            return ("transfer_question", 0, "Secure at the hardest level, so the next task changes context.")
        if due_for_retrieval(state, now) and mastery >= 50:
            return ("spaced_retrieval", 0, "This concept is due for a spaced review.")
        if is_unassessed(state):
            return ("maintain_difficulty", 0,
                    "One correct answer is not yet enough evidence to change the level.")
        return ("maintain_difficulty", 0, "Correct, but not yet a streak that justifies a harder question.")

    # 12. Incorrect with a cause that is neither understanding nor execution-specific.
    if consecutive_incorrect >= DEMOTION_STREAK and (
        tag in UNDERSTANDING_TAGS or _understanding_failure(history)
    ):
        return ("reduce_difficulty", -1,
                "Repeated conceptual errors at this level; stepping back to rebuild.")
    return ("targeted_practice", 0, "The next task practises exactly the step that failed.")


def _understanding_failure(history: list[dict[str, Any]]) -> bool:
    """Whether the recent errors are about understanding rather than execution."""

    recent = [str(item.get("primary_diagnosis") or item.get("tag") or "") for item in history[:3]]
    return any(tag in UNDERSTANDING_TAGS for tag in recent)


def _purpose_for(action: str, tag: str, concept: str, prerequisite: str, objective: str) -> str:
    concept = concept or "this concept"
    if objective:
        return objective
    return {
        "clarify_instruction": f"Check that the wording of a {concept} question is read correctly.",
        "diagnostic_check": f"Find out what is actually known about {concept}.",
        "prerequisite_reteach": f"Rebuild {prerequisite or 'the underlying skill'} before returning to {concept}.",
        "targeted_practice": f"Practise the exact {concept} step that failed.",
        "worked_example_then_practice": f"Show the correct {concept} method, then practise it.",
        "misconception_contrast": f"Contrast the mistaken and correct ideas about {concept}.",
        "spaced_retrieval": f"Retrieve {concept} after a gap to strengthen retention.",
        "transfer_question": f"Apply {concept} in an unfamiliar context.",
        "increase_difficulty": f"Extend {concept} to a harder case.",
        "maintain_difficulty": f"Consolidate {concept} at the current level.",
        "reduce_difficulty": f"Rebuild confidence in {concept} at a simpler level.",
        "human_review_or_insufficient_evidence": f"Have a teacher look at this {concept} attempt.",
    }.get(action, f"Practise {concept}.")


def lower_demand(demand: str) -> str:
    """One step down the cognitive ladder, for a diagnostic check after a guess."""

    try:
        index = _COGNITIVE_ORDER.index(demand)
    except ValueError:
        return "recall"
    return _COGNITIVE_ORDER[max(0, index - 1)]


def normalized_prompt(value: Any) -> str:
    """The normalization used for repetition control; mirrors the quizzes duplicate check."""

    return re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).strip()


def similarity(left: str, right: str) -> float:
    """Token Jaccard similarity of two prompts, used to reject near-duplicate questions."""

    left_tokens = set(normalized_prompt(left).split())
    right_tokens = set(normalized_prompt(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)

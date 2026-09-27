"""The concept knowledge model: evidence over time, uncertainty, and prerequisites.

This layer sits beside the existing `learnova.quizzes.adaptive.update_mastery` rules
rather than replacing them. `update_mastery` still owns the mastery score and the SRS
schedule; this module owns how much the accumulated evidence is actually worth, how
uncertain the estimate is, why it moved, and which prerequisites are confirmed.

Everything here is a pure function of the state passed in, so it is testable without a
database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# How long it takes for one observation's weight to halve. Chosen to match the longest
# SRS interval (30 days) so a concept that is never revisited decays to genuine
# uncertainty over roughly one review cycle.
HALF_LIFE_DAYS = 30.0

# A concept needs this much accumulated evidence before the engine will call it mastered.
MASTERY_EVIDENCE_FLOOR = 2.5

# Prerequisite edges are only acted on after this many independent, evidence-backed
# observations, so one bad answer never rewires a student's learning path.
PREREQUISITE_MIN_EVIDENCE = 2

# Uncertainty above this means "we do not know yet" rather than "weak".
UNASSESSED_UNCERTAINTY = 0.75


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def decayed_weight(weight: float, last_seen: datetime | None, now: datetime) -> float:
    """Exponentially decay accumulated evidence toward zero as it ages."""

    previous = _utc(last_seen)
    if previous is None or weight <= 0:
        return max(0.0, float(weight or 0.0))
    age_days = max(0.0, (now - previous).total_seconds() / 86400.0)
    return round(float(weight) * (0.5 ** (age_days / HALF_LIFE_DAYS)), 4)


def observation_weight(
    *,
    hints_used: bool = False,
    missing_evidence: bool = False,
    diagnosis_confidence: float = 0.5,
    difficulty: int = 1,
    ocr_confidence: float | None = None,
    validation_status: str = "validated",
) -> float:
    """How much one answer is worth as evidence about the concept.

    Weak signals are discounted rather than discarded: a hinted answer, an unreadable
    scan, a low-confidence diagnosis and a conflicted validation all still count, just
    for less. A response the engine could not diagnose at all is worth almost nothing.
    """

    weight = 1.0
    if hints_used:
        weight *= 0.5
    if missing_evidence:
        weight *= 0.4
    if ocr_confidence is not None and ocr_confidence < 0.7:
        weight *= 0.6
    if validation_status in ("deterministic_conflict", "rejected"):
        weight *= 0.35
    elif validation_status == "unverified":
        weight *= 0.85
    weight *= max(0.3, min(1.25, 0.5 + float(diagnosis_confidence or 0.0)))
    weight *= 1.0 + 0.1 * max(0, min(2, int(difficulty or 1) - 1))
    return round(max(0.05, min(1.5, weight)), 4)


def uncertainty_from_weight(weight: float) -> float:
    """Map accumulated evidence weight to a 0..1 uncertainty. No evidence means 1.0."""

    return round(1.0 / (1.0 + max(0.0, float(weight or 0.0))), 4)


def update_evidence(
    state: dict[str, Any],
    *,
    now: datetime,
    hints_used: bool = False,
    missing_evidence: bool = False,
    diagnosis_confidence: float = 0.5,
    difficulty: int = 1,
    ocr_confidence: float | None = None,
    validation_status: str = "validated",
) -> dict[str, Any]:
    """Return the new evidence weight and uncertainty for one concept after one answer.

    `state` accepts the `ConceptMastery` fields (`evidence_weight`, `last_practised_at`)
    so the caller can pass `mastery_state(record)` straight through.
    """

    carried = decayed_weight(state.get("evidence_weight", 0.0), state.get("last_practised_at"), now)
    added = observation_weight(
        hints_used=hints_used,
        missing_evidence=missing_evidence,
        diagnosis_confidence=diagnosis_confidence,
        difficulty=difficulty,
        ocr_confidence=ocr_confidence,
        validation_status=validation_status,
    )
    total = round(min(20.0, carried + added), 4)
    return {
        "evidence_weight": total,
        "uncertainty": uncertainty_from_weight(total),
        "observation_weight": added,
        "carried_weight": carried,
    }


def is_unassessed(state: dict[str, Any]) -> bool:
    """A concept nobody has real evidence about, as opposed to one that is genuinely weak."""

    if int(state.get("attempts") or 0) == 0:
        return True
    uncertainty = state.get("uncertainty")
    if uncertainty is None:
        return False
    return float(uncertainty) >= UNASSESSED_UNCERTAINTY


def confident_status(state: dict[str, Any], status: str) -> str:
    """Downgrade an over-confident status when the evidence does not support it yet.

    A single good answer must not produce a permanent 'mastered' conclusion, so the
    engine refuses to report mastery until enough non-decayed evidence exists.
    """

    if status != "mastered":
        return status
    weight = float(state.get("evidence_weight") or 0.0)
    return "mastered" if weight >= MASTERY_EVIDENCE_FLOOR else "strong"


def mastery_reason(
    *,
    outcome: str,
    delta: float,
    diagnosis_tag: str,
    hints_used: bool,
    missing_evidence: bool,
    observation_weight_value: float,
    uncertainty: float,
) -> str:
    """A short, human-readable explanation of why mastery moved, stored with the history.

    This is the explainability requirement: every mastery change can be justified to the
    student without exposing the model's internals.
    """

    direction = "increased" if delta > 0 else "decreased" if delta < 0 else "held steady"
    parts = [f"Mastery {direction} by {abs(delta):.1f} after a {outcome} answer"]
    if diagnosis_tag and diagnosis_tag not in ("correct", ""):
        parts.append(f"diagnosed as {diagnosis_tag.replace('_', ' ')}")
    if hints_used:
        parts.append("counted for less because a hint was used")
    if missing_evidence:
        parts.append("counted for less because the evidence was incomplete")
    if observation_weight_value < 0.6:
        parts.append("treated as weak evidence")
    if uncertainty >= UNASSESSED_UNCERTAINTY:
        parts.append("the estimate is still provisional")
    return ("; ".join(parts) + ".")[:400]


# ----------------------------------------------------------------- prerequisites

def merge_prerequisite(
    edges: dict[str, dict[str, Any]],
    *,
    concept: str,
    prerequisite: str,
    now: datetime,
    confidence: float = 0.5,
) -> dict[str, dict[str, Any]]:
    """Accumulate one evidence-backed prerequisite observation into an edge map.

    Self-edges and empty names are ignored. The map is keyed by the prerequisite name so
    repeated observations of the same gap reinforce one edge instead of creating many.
    """

    concept = str(concept or "").strip()[:255]
    prerequisite = str(prerequisite or "").strip()[:255]
    if not concept or not prerequisite or concept.casefold() == prerequisite.casefold():
        return edges
    key = prerequisite.casefold()
    edge = edges.setdefault(key, {
        "concept": concept, "prerequisite": prerequisite,
        "evidence_count": 0, "confidence": 0.0, "last_seen_at": now,
    })
    edge["evidence_count"] += 1
    # Running mean keeps one over-confident observation from dominating the edge.
    edge["confidence"] = round(
        (edge["confidence"] * (edge["evidence_count"] - 1) + max(0.0, min(1.0, confidence)))
        / edge["evidence_count"], 3)
    edge["last_seen_at"] = now
    return edges


def confirmed_prerequisites(
    edges: list[dict[str, Any]],
    *,
    minimum_evidence: int = PREREQUISITE_MIN_EVIDENCE,
) -> list[dict[str, Any]]:
    """Only prerequisites seen enough times to act on, strongest first."""

    confirmed = [
        edge for edge in edges
        if int(edge.get("evidence_count") or 0) >= minimum_evidence
        and str(edge.get("prerequisite") or "").strip()
    ]
    confirmed.sort(
        key=lambda edge: (int(edge.get("evidence_count") or 0), float(edge.get("confidence") or 0.0)),
        reverse=True,
    )
    return confirmed


def prerequisite_chain(
    edges: list[dict[str, Any]],
    concept: str,
    *,
    max_depth: int = 3,
) -> list[str]:
    """Walk the prerequisite graph from a concept, breadth-first, without cycling.

    Returns the prerequisite names in teaching order (nearest prerequisite first). The
    depth bound and the visited set make this safe on a graph the model contributed to.
    """

    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        source = str(edge.get("concept") or "").strip().casefold()
        target = str(edge.get("prerequisite") or "").strip()
        if source and target:
            adjacency.setdefault(source, []).append(target)
    chain: list[str] = []
    visited = {str(concept or "").strip().casefold()}
    frontier = [str(concept or "").strip().casefold()]
    for _ in range(max_depth):
        nxt: list[str] = []
        for node in frontier:
            for target in adjacency.get(node, []):
                key = target.casefold()
                if key in visited:
                    continue
                visited.add(key)
                chain.append(target)
                nxt.append(key)
        if not nxt:
            break
        frontier = nxt
    return chain


def recurring_misconception_count(
    history: list[dict[str, Any]],
    *,
    concept: str,
    tag: str,
) -> int:
    """How many recent attempts on this concept carried the same diagnosis tag.

    The planner uses this to tell a first mistake from an entrenched one, which is the
    difference between showing a worked example and contrasting two competing ideas.
    """

    concept_key = str(concept or "").strip().casefold()
    return sum(
        1 for item in history
        if str(item.get("primary_diagnosis") or item.get("tag") or "") == tag
        and (not concept_key or str(item.get("concept") or "").strip().casefold() == concept_key)
    )


def evidence_summary(state: dict[str, Any]) -> dict[str, Any]:
    """A compact, safe view of what is known about one concept, for prompts and the UI."""

    uncertainty = state.get("uncertainty")
    uncertainty = 1.0 if uncertainty is None else float(uncertainty)
    return {
        "concept": state.get("concept", ""),
        "subject": state.get("subject", ""),
        "mastery_score": round(float(state.get("mastery_score") or 0.0), 1),
        "status": state.get("status", "weak"),
        "attempts": int(state.get("attempts") or 0),
        "consecutive_correct": int(state.get("consecutive_correct") or 0),
        "consecutive_incorrect": int(state.get("consecutive_incorrect") or 0),
        "evidence_weight": round(float(state.get("evidence_weight") or 0.0), 2),
        "uncertainty": round(uncertainty, 2),
        "assessed": not is_unassessed(state),
        "difficulty_level": int(state.get("difficulty_level") or 1),
    }


def due_for_retrieval(state: dict[str, Any], now: datetime, *, grace: timedelta = timedelta(hours=6)) -> bool:
    """Whether spaced retrieval is worth spending the next question on."""

    review = _utc(state.get("next_review_at"))
    return review is not None and review <= now + grace

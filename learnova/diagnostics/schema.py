"""The versioned diagnostic contract: strict validation, safe normalization, presentation.

The project validates AI output with deterministic Python validators rather than a
runtime model library (see `learnova.ai_services.contracts`); this module follows that
convention so no new dependency is introduced.

Two entry points matter:

* `validate_diagnosis` raises on a response that cannot be trusted at all. The AI gateway
  turns that into one corrective retry.
* `normalize_diagnosis` coerces a merely imperfect response into the canonical shape and,
  crucially, **drops every claim that is not linked to quoted evidence**. A model cannot
  assert a misconception, a prerequisite gap or a student intention here without pointing
  at the text it came from.
"""

from __future__ import annotations

from typing import Any

from .taxonomy import (
    COGNITIVE_DEMAND_SET,
    CORRECTNESS_STATUSES,
    DIAGNOSIS_TAG_SET,
    EVIDENCE_SOURCE_SET,
    INTERVENTION_SET,
    NEXT_ACTION_SET,
    NON_EVIDENTIAL_TAGS,
    VALIDATION_STATUS_SET,
)

DIAGNOSIS_VERSION = "diagnosis:v2"

# Tags that are a statement about the student's understanding and therefore require at
# least one resolvable evidence reference. `correct`/`partially_correct` describe the
# answer itself and are already backed by the score, so they are exempt.
EVIDENCE_REQUIRED_TAGS = DIAGNOSIS_TAG_SET - {"correct", "partially_correct", "insufficient_evidence"}

_MAX_EVIDENCE = 8
_MAX_QUOTE = 400
_MAX_TEXT = 1200
_MAX_SUMMARY = 800
_MAX_CONCEPTS = 5
_MAX_SECONDARY = 3
_MAX_RUBRIC = 8

QUESTION_TYPES = (
    "multiple_choice", "checkboxes", "dropdown", "ordering", "text", "true_false",
    "matching", "fill_blank", "short_answer", "explanation", "calculation",
    "photo_response", "photo_ordering",
)
QUESTION_TYPE_SET = frozenset(QUESTION_TYPES)


class DiagnosisSchemaError(ValueError):
    """Raised when a model diagnosis cannot be trusted even after normalization."""


def _text(value: Any, limit: int = _MAX_TEXT) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _number(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return max(low, min(high, number))


def _string_list(value: Any, limit: int, item_limit: int = 240) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _text(item, item_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _normalize_evidence(value: Any) -> list[dict[str, str]]:
    """Canonicalize the evidence list and assign stable ids where the model omitted them."""

    items = value if isinstance(value, list) else []
    evidence: list[dict[str, str]] = []
    used: set[str] = set()
    for index, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            continue
        quote = _text(raw.get("quote", raw.get("text", "")), _MAX_QUOTE)
        if not quote:
            continue
        source = _text(raw.get("source"), 40).lower().replace(" ", "_")
        if source not in EVIDENCE_SOURCE_SET:
            source = "student_answer"
        identifier = _text(raw.get("id"), 24) or f"e{index}"
        while identifier in used:
            identifier = f"{identifier}x"
        used.add(identifier)
        evidence.append({"id": identifier, "source": source, "quote": quote})
        if len(evidence) >= _MAX_EVIDENCE:
            break
    return evidence


def _normalize_claim(value: Any, known_ids: set[str]) -> dict[str, Any] | None:
    """One diagnosis claim, or None when its tag is unknown or its evidence unresolvable."""

    if isinstance(value, str):
        value = {"tag": value}
    if not isinstance(value, dict):
        return None
    tag = _text(value.get("tag", value.get("category")), 60).lower().replace(" ", "_")
    if tag not in DIAGNOSIS_TAG_SET:
        return None
    raw_ids = value.get("evidence_ids", value.get("evidence", []))
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    evidence_ids = [
        _text(item, 24) for item in raw_ids if isinstance(item, (str, int))
    ] if isinstance(raw_ids, (list, tuple)) else []
    resolved = [item for item in dict.fromkeys(evidence_ids) if item in known_ids]
    if tag in EVIDENCE_REQUIRED_TAGS and not resolved:
        return None
    return {
        "tag": tag,
        "statement": _text(value.get("statement", value.get("description", "")), 600),
        "evidence_ids": resolved,
    }


def _normalize_score(value: Any, status: str, known_ids: set[str]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    default_points = {"correct": 1.0, "partially_correct": 0.5}.get(status, 0.0)
    max_points = _number(raw.get("max_points"), 1.0, 0.01, 1000.0)
    points = _number(raw.get("points", raw.get("score")), default_points * max_points, 0.0, max_points)
    rubric: list[dict[str, Any]] = []
    raw_rubric = raw.get("rubric_evidence")
    for item in (raw_rubric if isinstance(raw_rubric, list) else []):
        if not isinstance(item, dict):
            continue
        criterion = _text(item.get("criterion"), 240)
        if not criterion:
            continue
        ids = item.get("evidence_ids", [])
        ids = [ids] if isinstance(ids, str) else (ids if isinstance(ids, list) else [])
        rubric.append({
            "criterion": criterion,
            "met": bool(item.get("met")),
            "evidence_ids": [_text(i, 24) for i in ids if _text(i, 24) in known_ids],
        })
        if len(rubric) >= _MAX_RUBRIC:
            break
    return {
        "points": round(points, 3),
        "max_points": round(max_points, 3),
        "fraction": round(points / max_points, 3) if max_points else 0.0,
        "rubric_evidence": rubric,
    }


def _normalize_constraints(value: Any, concepts: list[str]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    demand = _text(raw.get("cognitive_demand"), 20).lower()
    question_type = _text(raw.get("question_type"), 30).lower()
    return {
        "concept": _text(raw.get("concept"), 255) or (concepts[0] if concepts else ""),
        "prerequisite_focus": _text(raw.get("prerequisite_focus"), 255),
        "difficulty": int(_number(raw.get("difficulty"), 0, 0, 3)),
        "cognitive_demand": demand if demand in COGNITIVE_DEMAND_SET else "",
        "question_type": question_type if question_type in QUESTION_TYPE_SET else "",
        "target_misconception": _text(raw.get("target_misconception"), 400),
        "purpose": _text(raw.get("purpose"), 400),
        "avoid_prompts": _string_list(raw.get("avoid_prompts"), 8, 400),
    }


def validate_diagnosis(payload: Any) -> dict[str, Any]:
    """Strictly validate a model diagnosis; raise when it is unusable.

    Only hard failures raise, because the gateway spends one corrective retry on each:
    a non-object response, an unrecognised correctness status, or a non-correct verdict
    with no usable diagnosis at all. Everything softer is repaired by the normalizer and
    surfaces through `validation_status`.
    """

    if not isinstance(payload, dict):
        raise DiagnosisSchemaError("diagnosis is not a JSON object")
    status = _text(payload.get("correctness_status", payload.get("verdict")), 40).lower()
    if status not in CORRECTNESS_STATUSES:
        raise DiagnosisSchemaError(f"invalid correctness_status: {status!r}")
    normalized = normalize_diagnosis(payload)
    if (
        normalized["correctness_status"] in ("incorrect", "partially_correct")
        and normalized["primary_diagnosis"]["tag"] == "insufficient_evidence"
        and not normalized["evidence"]
    ):
        raise DiagnosisSchemaError("non-correct verdict without any quoted evidence")
    return normalized


def normalize_diagnosis(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a diagnosis into the canonical shape, dropping unsupported claims.

    `validation_status` reports what happened: `validated` when nothing had to be
    removed, `repaired` when an unsupported claim was dropped or a field was rebuilt.
    """

    repaired = False
    status = _text(payload.get("correctness_status", payload.get("verdict")), 40).lower()
    if status not in CORRECTNESS_STATUSES:
        status, repaired = "insufficient_evidence", True

    evidence = _normalize_evidence(payload.get("evidence"))
    known_ids = {item["id"] for item in evidence}

    primary = _normalize_claim(payload.get("primary_diagnosis"), known_ids)
    if primary is None:
        # The model named no tag, an unknown tag, or a tag with no resolvable evidence.
        # Falling back to the status keeps a correct answer correct, and degrades an
        # unsupported accusation to "not enough to judge" rather than inventing a cause.
        fallback = status if status in ("correct", "partially_correct") else "insufficient_evidence"
        if payload.get("primary_diagnosis") is not None or fallback == "insufficient_evidence":
            repaired = True
        primary = {"tag": fallback, "statement": "", "evidence_ids": []}
    if primary["tag"] == "insufficient_evidence":
        status = "insufficient_evidence"

    secondary: list[dict[str, Any]] = []
    raw_secondary = payload.get("secondary_diagnoses")
    for item in (raw_secondary if isinstance(raw_secondary, list) else []):
        claim = _normalize_claim(item, known_ids)
        if claim is None:
            repaired = True
            continue
        if claim["tag"] == primary["tag"] or any(c["tag"] == claim["tag"] for c in secondary):
            continue
        secondary.append(claim)
        if len(secondary) >= _MAX_SECONDARY:
            break

    concepts = _string_list(payload.get("concepts_assessed"), _MAX_CONCEPTS, 255)

    prerequisites: list[dict[str, Any]] = []
    raw_prerequisites = payload.get("prerequisite_gaps")
    for item in (raw_prerequisites if isinstance(raw_prerequisites, list) else []):
        entry = {"concept": item} if isinstance(item, str) else item
        if not isinstance(entry, dict):
            continue
        concept = _text(entry.get("concept", entry.get("name")), 255)
        if not concept:
            continue
        ids = entry.get("evidence_ids", [])
        ids = [ids] if isinstance(ids, str) else (ids if isinstance(ids, list) else [])
        resolved = [_text(i, 24) for i in ids if _text(i, 24) in known_ids]
        if not resolved:
            # A prerequisite gap is a durable claim about the student; without evidence
            # it is dropped rather than persisted into the knowledge model.
            repaired = True
            continue
        prerequisites.append({"concept": concept, "evidence_ids": resolved})
        if len(prerequisites) >= 4:
            break

    misconception = _text(payload.get("misconception_description"), 600)
    if misconception and primary["tag"] != "conceptual_misconception" and not any(
        claim["tag"] == "conceptual_misconception" for claim in secondary
    ):
        misconception, repaired = "", True

    missing_evidence = bool(payload.get("missing_evidence")) or status == "insufficient_evidence"
    missing_reason = _text(payload.get("missing_evidence_reason"), 400)
    if missing_evidence and not missing_reason:
        missing_reason = "The response does not show enough working to identify a cause."

    raw_confidence = payload.get("confidence")
    if isinstance(raw_confidence, dict):
        confidence_value = _number(raw_confidence.get("value"), 0.5, 0.0, 1.0)
        confidence_basis = _text(raw_confidence.get("basis"), 300)
    else:
        confidence_value = _number(raw_confidence, 0.5, 0.0, 1.0)
        confidence_basis = ""
    if missing_evidence:
        confidence_value = min(confidence_value, 0.4)

    intervention = _text(payload.get("recommended_intervention"), 60).lower().replace(" ", "_")
    if intervention not in INTERVENTION_SET:
        intervention = _default_intervention(primary["tag"], status)
    next_action = _text(payload.get("next_action"), 60).lower().replace(" ", "_")
    if next_action not in NEXT_ACTION_SET:
        next_action = ""  # the planner is authoritative; an unusable hint is simply ignored

    validation_status = _text(payload.get("validation_status"), 40).lower()
    if validation_status not in VALIDATION_STATUS_SET:
        validation_status = "repaired" if repaired else "validated"
    elif repaired and validation_status == "validated":
        validation_status = "repaired"

    return {
        "analysis_version": DIAGNOSIS_VERSION,
        "correctness_status": status,
        "score": _normalize_score(payload.get("score"), status, known_ids),
        "concepts_assessed": concepts,
        "primary_diagnosis": primary,
        "secondary_diagnoses": secondary,
        "evidence": evidence,
        "misconception_description": misconception,
        "prerequisite_gaps": prerequisites,
        "missing_evidence": missing_evidence,
        "missing_evidence_reason": missing_reason if missing_evidence else "",
        "confidence": {"value": round(confidence_value, 3), "basis": confidence_basis},
        "recommended_intervention": intervention,
        "next_action": next_action,
        "candidate_question_constraints": _normalize_constraints(
            payload.get("candidate_question_constraints"), concepts
        ),
        "student_facing_explanation": _text(payload.get("student_facing_explanation"), _MAX_TEXT),
        "internal_diagnostic_summary": _text(payload.get("internal_diagnostic_summary"), _MAX_SUMMARY),
        "validation_status": validation_status,
    }


def _default_intervention(tag: str, status: str) -> str:
    if status == "correct":
        return "confirm_and_extend"
    return {
        "conceptual_misconception": "contrast_misconception",
        "prerequisite_gap": "reteach_prerequisite",
        "procedural_error": "worked_example",
        "interpretation_error": "clarify_question",
        "arithmetic_or_transcription_error": "practice_similar",
        "incomplete_reasoning": "request_more_work",
        "guessing_or_uncertain": "retrieval_practice",
        "insufficient_evidence": "request_more_work",
        "question_or_key_flawed": "human_review",
    }.get(tag, "explain_correction")


def insufficient_evidence_diagnosis(reason: str = "", concepts: list[str] | None = None) -> dict[str, Any]:
    """A well-formed result used when no trustworthy diagnosis could be produced."""

    return normalize_diagnosis({
        "correctness_status": "insufficient_evidence",
        "concepts_assessed": concepts or [],
        "primary_diagnosis": {"tag": "insufficient_evidence", "statement": reason},
        "missing_evidence": True,
        "missing_evidence_reason": reason or "The detailed diagnosis is not available right now.",
        "confidence": {"value": 0.0, "basis": "No diagnosis was produced."},
        "next_action": "diagnostic_check",
        "validation_status": "rejected",
    })


def student_view(diagnosis: dict[str, Any], *, include_detail: bool = True) -> dict[str, Any]:
    """The only mapping used to send a diagnosis to a browser.

    Internal fields never cross this boundary: `internal_diagnostic_summary` is dropped,
    and evidence quotes are limited to the student's own response and the task in front
    of them, so a prior attempt's text is never replayed into a different page.
    """

    visible_sources = {"question", "rubric", "expected_answer", "student_answer", "work_step"}
    view: dict[str, Any] = {
        "analysis_version": diagnosis.get("analysis_version", DIAGNOSIS_VERSION),
        "correctness_status": diagnosis.get("correctness_status", "insufficient_evidence"),
        "score_fraction": diagnosis.get("score", {}).get("fraction", 0.0),
        "explanation": diagnosis.get("student_facing_explanation", ""),
        "primary_tag": diagnosis.get("primary_diagnosis", {}).get("tag", "insufficient_evidence"),
        "missing_evidence": bool(diagnosis.get("missing_evidence")),
        "missing_evidence_reason": diagnosis.get("missing_evidence_reason", ""),
        "next_action": diagnosis.get("next_action", ""),
        "confidence": diagnosis.get("confidence", {}).get("value", 0.0),
        "validation_status": diagnosis.get("validation_status", "unverified"),
    }
    if not include_detail:
        return view
    view["detail"] = {
        "statement": diagnosis.get("primary_diagnosis", {}).get("statement", ""),
        "secondary_tags": [claim.get("tag", "") for claim in diagnosis.get("secondary_diagnoses", [])],
        "misconception": diagnosis.get("misconception_description", ""),
        "prerequisite_gaps": [item.get("concept", "") for item in diagnosis.get("prerequisite_gaps", [])],
        "concepts_assessed": diagnosis.get("concepts_assessed", []),
        "evidence": [
            {"source": item.get("source", ""), "quote": item.get("quote", "")}
            for item in diagnosis.get("evidence", [])
            if item.get("source") in visible_sources
        ],
        "rubric": diagnosis.get("score", {}).get("rubric_evidence", []),
        "recommended_intervention": diagnosis.get("recommended_intervention", ""),
    }
    return view


# ------------------------------------------------------- legacy interoperability

def to_legacy_analysis(diagnosis: dict[str, Any]) -> dict[str, Any]:
    """Project a diagnosis:v2 result onto the `learnova.analysis` shape.

    Saved attempts, the Mistake Intelligence page and existing API consumers keep
    working unchanged; this is a pure projection with no extra provider call.
    """

    from learnova.analysis.service import normalize_analysis  # local import: avoids a cycle

    from .taxonomy import STATUS_TO_LEGACY_VERDICT, TAG_TO_LEGACY_CATEGORY

    status = diagnosis.get("correctness_status", "insufficient_evidence")
    primary = diagnosis.get("primary_diagnosis", {})
    tags = [primary.get("tag", "")] + [c.get("tag", "") for c in diagnosis.get("secondary_diagnoses", [])]
    categories = [
        TAG_TO_LEGACY_CATEGORY[tag] for tag in dict.fromkeys(tags) if tag in TAG_TO_LEGACY_CATEGORY
    ]
    constraints = diagnosis.get("candidate_question_constraints", {})
    root_cause = primary.get("statement", "") or diagnosis.get("misconception_description", "")
    if not root_cause and status != "correct":
        root_cause = diagnosis.get("missing_evidence_reason", "")
    non_evidential = primary.get("tag", "") in NON_EVIDENTIAL_TAGS
    return normalize_analysis({
        "verdict": STATUS_TO_LEGACY_VERDICT.get(status, "ambiguous"),
        "score_fraction": diagnosis.get("score", {}).get("fraction", 0.0),
        "confidence": diagnosis.get("confidence", {}).get("value", 0.0),
        "question_intent": constraints.get("purpose", ""),
        "student_approach": primary.get("statement", ""),
        "correct_parts": [
            item.get("criterion", "") for item in diagnosis.get("score", {}).get("rubric_evidence", [])
            if item.get("met")
        ],
        "mistake_categories": categories,
        "root_cause": root_cause,
        "exact_error_step": next(
            (item.get("quote", "") for item in diagnosis.get("evidence", [])
             if item.get("source") in ("student_answer", "work_step")), ""),
        "improvement_advice": diagnosis.get("student_facing_explanation", ""),
        "prerequisites_to_review": [
            item.get("concept", "") for item in diagnosis.get("prerequisite_gaps", [])
        ],
        "next_question": {
            "question": constraints.get("purpose", ""),
            "purpose": constraints.get("target_misconception", "") or constraints.get("purpose", ""),
            "difficulty_change": {
                "increase_difficulty": "harder",
                "reduce_difficulty": "easier",
            }.get(diagnosis.get("next_action", ""), "same"),
        },
        "should_create_mistake_record": status in ("incorrect", "partially_correct") and not non_evidential,
        "should_reduce_mastery": status == "incorrect" and not non_evidential,
        "analysis_limitations": (
            [diagnosis.get("missing_evidence_reason", "")] if diagnosis.get("missing_evidence") else []
        ),
    })

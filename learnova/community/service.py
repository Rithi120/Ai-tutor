"""Pure, framework-free logic for the community flashcard library.

Everything here is deterministic and unit-testable without Flask or a database:
- normalizing/validating an AI quality review into a safe stored shape,
- mapping an AI score to a 1-5 star display,
- the publication decision bands (with a hard safety override),
- a Bayesian student-rating average (so one 5-star cannot beat hundreds of reviews),
- a weighted ranking score with penalties.

Student rating and AI review are kept strictly separate - they measure different things.
"""

from __future__ import annotations

from typing import Any


# "pending_moderation" and "changes_requested" were added with the safety gate: a
# submission now waits for moderation before the quality review runs, and a fixable
# moderation outcome is a distinct state from a rejection so the UI can offer an edit
# instead of a dead end.
PUBLICATION_STATES = {
    "draft", "pending_moderation", "pending_ai_review", "pending_manual_review",
    "approved", "changes_requested", "rejected", "hidden", "unpublished",
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
AUTHOR_DISPLAY = {"username", "nickname", "anonymous"}
REPORT_REASONS = {
    "incorrect", "spam", "offensive", "copyright", "personal_information", "duplicate", "other",
}
# Any of these in an AI review overrides the numeric score and forces rejection.
SAFETY_FLAGS = {"unsafe", "offensive", "copyright", "personal_information", "spam"}

SCORE_FIELDS = (
    "overallScore", "accuracyScore", "clarityScore", "usefulnessScore",
    "coverageScore", "difficultyScore", "originalityScore",
)

# Bayesian prior for student ratings: assume a neutral-ish 3.5 backed by 10 pseudo-votes.
RATING_PRIOR_MEAN = 3.5
RATING_PRIOR_WEIGHT = 10


def _clamp_score(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(max(0.0, min(5.0, number)), 2)


def _string_list(value: Any, limit: int = 12, length: int = 300) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for text in (str(item).strip()[:length] for item in value) if text][:limit]


def _flagged(value: Any, limit: int = 50) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    result: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            reference = str(item.get("reference") or item.get("card") or item.get("front") or "").strip()[:300]
            issue = str(item.get("issue") or item.get("reason") or item.get("problem") or "").strip()[:300]
        else:
            reference, issue = "", str(item).strip()[:300]
        if reference or issue:
            result.append({"reference": reference, "issue": issue})
        if len(result) >= limit:
            break
    return result


def normalize_ai_review(raw: Any) -> dict[str, Any]:
    """Validate/normalize a raw AI review into the stored shape. Raise ValueError if unusable."""

    if not isinstance(raw, dict):
        raise ValueError("AI review must be an object")
    scores = {field: _clamp_score(raw.get(field)) for field in SCORE_FIELDS}
    if scores["overallScore"] <= 0:
        others = [scores[field] for field in SCORE_FIELDS[1:] if scores[field] > 0]
        scores["overallScore"] = round(sum(others) / len(others), 2) if others else 0.0
    if scores["overallScore"] <= 0:
        raise ValueError("AI review has no usable scores")
    confidence = str(raw.get("confidence", "medium")).strip().lower()
    if confidence not in CONFIDENCE_LEVELS:
        confidence = "medium"
    safety_source = raw.get("safetyFlags") or raw.get("safety_flags") or []
    safety_flags = sorted({
        str(flag).strip().lower() for flag in safety_source if str(flag).strip().lower() in SAFETY_FLAGS
    }) if isinstance(safety_source, list) else []
    return {
        "scores": scores,
        "confidence": confidence,
        "summary": str(raw.get("summary") or "").strip()[:1000],
        "strengths": _string_list(raw.get("strengths")),
        "improvements": _string_list(raw.get("improvements")),
        "flagged": _flagged(raw.get("flaggedCards") or raw.get("flagged")),
        "safety_flags": safety_flags,
    }


def ai_stars(overall_score: float) -> tuple[int, str]:
    """Map a 0-5 AI overall score to a 1-5 star count and its label."""

    if overall_score >= 4.5:
        return 5, "Excellent"
    if overall_score >= 3.5:
        return 4, "Good"
    if overall_score >= 2.5:
        return 3, "Acceptable"
    if overall_score >= 1.5:
        return 2, "Needs Improvement"
    return 1, "Poor Quality"


def publication_decision(overall_score: float, safety_flags: list[str] | None = None) -> dict[str, Any]:
    """Decide publication status from the AI score; safety/copyright/privacy always override."""

    if safety_flags:
        return {"status": "rejected", "reason": "safety_violation", "correctable": False}
    if overall_score >= 4.0:
        return {"status": "approved", "reason": "auto_approved", "correctable": False}
    if overall_score >= 3.0:
        return {"status": "pending_manual_review", "reason": "approved_with_suggestions", "correctable": True}
    if overall_score >= 2.0:
        return {"status": "rejected", "reason": "needs_correction", "correctable": True}
    return {"status": "rejected", "reason": "poor_quality", "correctable": False}


def bayesian_average(
    rating_sum: float, rating_count: int,
    prior_mean: float = RATING_PRIOR_MEAN, prior_weight: int = RATING_PRIOR_WEIGHT,
) -> float:
    """Bayesian mean so a set with one 5-star cannot outrank one with many strong reviews."""

    denominator = prior_weight + max(0, rating_count)
    if denominator <= 0:
        return round(prior_mean, 3)
    return round((prior_weight * prior_mean + rating_sum) / denominator, 3)


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _saturating(count: int, half: int = 10) -> float:
    count = max(0, int(count))
    return count / (count + half)


def ranking_score(
    *,
    ai_overall: float,
    student_bayesian: float,
    completion_rate: float = 0.0,
    helpful_votes: int = 0,
    save_count: int = 0,
    recency: float = 0.0,
    penalty: float = 0.0,
) -> float:
    """Composite 0-1 ranking: 35% AI, 25% student, 15% completion, 10% helpful, 10% saves, 5% recency."""

    score = (
        0.35 * (_clamp_score(ai_overall) / 5)
        + 0.25 * (_clamp_score(student_bayesian) / 5)
        + 0.15 * _clamp01(completion_rate)
        + 0.10 * _saturating(helpful_votes)
        + 0.10 * _saturating(save_count)
        + 0.05 * _clamp01(recency)
    )
    return round(max(0.0, score - max(0.0, penalty)), 4)

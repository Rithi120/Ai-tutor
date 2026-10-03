"""Stage E contract: the strict schema for one AI moderation classification.

Raw model output is untrusted input. Nothing reaches the policy engine until it has
survived `validate_classification`, and nothing is stored until `normalize_classification`
has coerced it into exactly the documented shape.

The rule that matters most is enforced here in code rather than requested in the prompt:

    **A flag must quote the content it is about.** Every dimension the model marks
    `flag` has to cite a short span that actually occurs in the submitted text. A flag
    whose quote cannot be found is downgraded to `unknown` - the uncertainty is kept, the
    unsupported accusation is dropped, and the item escalates instead of being rejected
    on something the model invented.

Quote matching runs against the folded comparison form from `normalize`, so a model that
re-spaces, re-cases or de-punctuates its quote still matches, while a model that made the
span up does not.
"""

from __future__ import annotations

from typing import Any

from .normalize import fold_text
from .taxonomy import (
    DIMENSION_SET,
    DIMENSION_STATUS_SET,
    DIMENSIONS,
    EVIDENCE_LEVEL_SET,
    REASON_CODE_SET,
    SAFETY_DIMENSIONS,
)


# Bumped whenever the contract changes shape, so cached responses and stored records can
# never be read under the wrong assumptions.
MODERATION_SCHEMA_VERSION = "moderation:v1"

# What the model is allowed to suggest. It is a recommendation, never a decision: the
# field is named for what it is so no caller can mistake it for authority.
RECOMMENDATIONS = ("allow", "reject", "revision_required", "review")
RECOMMENDATION_SET = frozenset(RECOMMENDATIONS)

MAX_SUMMARY_LENGTH = 600
MAX_REVISION_LENGTH = 400
MAX_QUOTE_LENGTH = 200
MAX_QUOTES = 12
MAX_REASON_CODES = 10
# A quote shorter than this carries no information and matches almost any text.
MIN_QUOTE_FOLDED_LENGTH = 4


class ModerationSchemaError(ValueError):
    """Raised when model output cannot be read as a moderation classification."""


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _status(value: Any) -> str:
    candidate = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    # Models reach for these synonyms; accepting them costs nothing and avoids a retry
    # that would spend tokens to get the same meaning back in the documented spelling.
    candidate = {
        "ok": "pass", "passed": "pass", "clean": "pass", "no": "pass", "none": "pass",
        "flagged": "flag", "fail": "flag", "failed": "flag", "violation": "flag",
        "yes": "flag", "n_a": "not_applicable", "na": "not_applicable",
        "notapplicable": "not_applicable", "unsure": "unknown", "uncertain": "unknown",
    }.get(candidate, candidate)
    return candidate if candidate in DIMENSION_STATUS_SET else "unknown"


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    # A model that answers on a 0-100 scale means the same thing; rescale rather than
    # clamping a 91 to 1.0 and calling it certainty.
    if 1.0 < number <= 100.0:
        number /= 100.0
    return round(max(0.0, min(1.0, number)), 4)


def validate_classification(data: Any) -> None:
    """Structural validation. Raises ModerationSchemaError on anything unusable.

    Strict about what must exist and be the right type; tolerant about spelling, which
    `normalize_classification` repairs. This is the function the AI gateway calls, so a
    failure here becomes a safe validation category and one bounded corrective retry.
    """

    if not isinstance(data, dict):
        raise ModerationSchemaError("moderation result must be an object")
    dimensions = data.get("dimensions")
    if not isinstance(dimensions, dict):
        raise ModerationSchemaError("dimensions must be an object")
    missing = [name for name in DIMENSIONS if name not in dimensions]
    if missing:
        raise ModerationSchemaError(
            f"dimensions is missing {len(missing)} required field(s): {missing[0]}")
    unknown_keys = [key for key in dimensions if key not in DIMENSION_SET]
    if unknown_keys:
        raise ModerationSchemaError(f"unknown dimension {unknown_keys[0]!r}")
    recommendation = str(data.get("recommendation") or data.get("decision") or "").strip().casefold()
    if recommendation not in RECOMMENDATION_SET:
        raise ModerationSchemaError("recommendation must be allow, reject, revision_required or review")
    if not isinstance(data.get("confidence"), (int, float)) or isinstance(data.get("confidence"), bool):
        raise ModerationSchemaError("confidence must be a number")
    evidence_sufficiency = str(data.get("evidence_sufficiency") or "").strip().casefold()
    if evidence_sufficiency not in EVIDENCE_LEVEL_SET:
        raise ModerationSchemaError(
            "evidence_sufficiency must be sufficient, limited or insufficient")
    if not _text(data.get("evidence_summary"), MAX_SUMMARY_LENGTH):
        raise ModerationSchemaError("evidence_summary must be non-empty text")
    for name in ("reason_codes", "quotes"):
        if name in data and data[name] is not None and not isinstance(data[name], list):
            raise ModerationSchemaError(f"{name} must be a list")


def _quotes(raw: Any) -> list[dict[str, str]]:
    """Read the cited spans, keeping only entries that name a real dimension."""

    if not isinstance(raw, list):
        return []
    result: list[dict[str, str]] = []
    for item in raw[: MAX_QUOTES * 2]:
        if isinstance(item, dict):
            dimension = str(item.get("dimension") or "").strip().casefold()
            text = _text(item.get("quote") or item.get("text") or item.get("span"), MAX_QUOTE_LENGTH)
        else:
            dimension, text = "", _text(item, MAX_QUOTE_LENGTH)
        if text and dimension in DIMENSION_SET:
            result.append({"dimension": dimension, "quote": text})
        if len(result) >= MAX_QUOTES:
            break
    return result


def normalize_classification(raw: Any, *, content: str = "") -> dict[str, Any]:
    """Coerce validated output into the stored shape and enforce evidence linking.

    `content` is the text that was actually submitted. When it is supplied, every flag
    must quote a span found in it; a flag that cannot be grounded becomes `unknown` and
    is recorded in `unsupported_flags` so a reviewer can see what the model claimed and
    why it was not acted on.

    Passing `content=""` disables grounding. That is only correct for replaying a stored
    result whose grounding already happened - never for a fresh classification.
    """

    data = raw if isinstance(raw, dict) else {}
    haystack = fold_text(content) if content else ""
    quotes = _quotes(data.get("quotes"))
    quoted_dimensions: set[str] = set()
    grounded_quotes: list[dict[str, str]] = []
    for item in quotes:
        folded = fold_text(item["quote"])
        if not haystack:
            grounded_quotes.append(item)
            quoted_dimensions.add(item["dimension"])
        elif len(folded) >= MIN_QUOTE_FOLDED_LENGTH and folded in haystack:
            grounded_quotes.append(item)
            quoted_dimensions.add(item["dimension"])

    supplied = data.get("dimensions")
    raw_dimensions: dict[str, Any] = supplied if isinstance(supplied, dict) else {}
    dimensions: dict[str, str] = {}
    unsupported: list[str] = []
    for name in DIMENSIONS:
        status = _status(raw_dimensions.get(name))
        # Only a *safety* flag has to be quoted. Fit judgements ("this is off-topic for
        # mathematics", "there is not enough here to learn from") are statements about
        # the content as a whole, and demanding a span for them would discard exactly the
        # holistic reading they depend on. Their consequence is a revision request, so an
        # ungrounded one costs an edit rather than a rejection.
        if status == "flag" and name in SAFETY_DIMENSIONS and haystack and name not in quoted_dimensions:
            unsupported.append(name)
            status = "unknown"
        dimensions[name] = status

    reason_codes = [
        code for code in dict.fromkeys(
            str(item).strip().upper() for item in (data.get("reason_codes") or [])
            if isinstance(item, (str, int))
        ) if code in REASON_CODE_SET
    ][:MAX_REASON_CODES]

    recommendation = str(data.get("recommendation") or data.get("decision") or "review").strip().casefold()
    evidence_sufficiency = str(data.get("evidence_sufficiency") or "limited").strip().casefold()
    # Dropped rather than truncated: a revision instruction cut off mid-sentence is worse
    # than no instruction, and an over-long one is a sign the model ignored the contract.
    raw_revision = " ".join(str(data.get("suggested_revision") or "").split())
    revision = raw_revision if len(raw_revision) <= MAX_REVISION_LENGTH else ""
    return {
        "schema_version": MODERATION_SCHEMA_VERSION,
        "recommendation": recommendation if recommendation in RECOMMENDATION_SET else "review",
        "dimensions": dimensions,
        "confidence": _confidence(data.get("confidence")),
        "evidence_sufficiency": (
            evidence_sufficiency if evidence_sufficiency in EVIDENCE_LEVEL_SET else "limited"),
        "reason_codes": reason_codes,
        "evidence_summary": _text(data.get("evidence_summary"), MAX_SUMMARY_LENGTH),
        "suggested_revision": revision or None,
        "requires_review": bool(data.get("requires_review")),
        "quotes": grounded_quotes,
        "unsupported_flags": unsupported,
        "available": True,
    }


def unavailable_classification(reason: str = "") -> dict[str, Any]:
    """The safe stand-in when no usable classification exists.

    Every dimension is `unknown` rather than `pass`: a check that did not happen is not a
    check that succeeded. The policy engine reads `available=False` and holds the content
    unpublished, which is what makes a provider outage fail closed.
    """

    return {
        "schema_version": MODERATION_SCHEMA_VERSION,
        "recommendation": "review",
        "dimensions": {name: "unknown" for name in DIMENSIONS},
        "confidence": 0.0,
        "evidence_sufficiency": "insufficient",
        "reason_codes": ["MODERATION_UNAVAILABLE"],
        "evidence_summary": _text(reason or "The automatic check could not be completed.",
                                  MAX_SUMMARY_LENGTH),
        "suggested_revision": None,
        "requires_review": True,
        "quotes": [],
        "unsupported_flags": [],
        "available": False,
    }


def flagged_dimensions(classification: dict[str, Any]) -> list[str]:
    """Dimensions the classification marks as flagged, in declaration order."""

    dimensions = classification.get("dimensions") or {}
    return [name for name in DIMENSIONS if dimensions.get(name) == "flag"]


def unknown_dimensions(classification: dict[str, Any]) -> list[str]:
    """Dimensions the classification could not judge, in declaration order."""

    dimensions = classification.get("dimensions") or {}
    return [name for name in DIMENSIONS if dimensions.get(name) == "unknown"]

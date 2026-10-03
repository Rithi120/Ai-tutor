"""Context-aware moderation for community-published study material.

Flask-independent by design: every rule here is a pure function, so the whole policy can
be unit-tested without a request context, a database or a provider call. The app layer
(`app.py`) builds the content bundle, calls the AI gateway with these prompts, then
validates, decides and persists through this package.

The pipeline, in the order it runs:

1. `normalize.inspect`  - preprocessing, raw-character signals, obfuscation analysis
2. `prompts.*`          - the versioned prompt, with submitted text quarantined as data
3. the AI gateway       - one `content_moderation` task through the existing boundary
4. `schema.*`           - strict validation, then evidence grounding for safety flags
5. `policy.decide`      - the only place a decision is made

The classifier never decides. Its output is one input to `policy.decide`, alongside the
deterministic findings, the publication context and any reader reports.
"""

from .evaluation import THRESHOLDS, CaseResult, format_report, load_cases, run_suite
from .normalize import (
    DeterministicFindings,
    TextSignals,
    analyze,
    fold_text,
    inspect,
    normalize_text,
)
from .policy import (
    UNAVAILABLE_MESSAGE,
    DEFAULT_THRESHOLDS,
    MIN_ALLOW_CONFIDENCE,
    POLICY_VERSION,
    REJECT_CONFIDENCE,
    REJECT_CONFIDENCE_SEVERE,
    ModerationContext,
    PolicyDecision,
    Thresholds,
    decide,
    moderator_decision,
    stale_decision,
)
from .prompts import (
    MODERATION_SCHEMA_SUMMARY,
    build_content_bundle,
    flashcard_items,
    moderation_system_prompt,
    moderation_text,
    moderation_user_prompt,
)
from .schema import (
    MODERATION_SCHEMA_VERSION,
    ModerationSchemaError,
    flagged_dimensions,
    normalize_classification,
    unavailable_classification,
    unknown_dimensions,
    validate_classification,
)
from .taxonomy import (
    UNAVAILABLE_LABEL,
    status_label,
    DECISIONS,
    DIMENSIONS,
    PUBLISHABLE_DECISIONS,
    REASON_CODES,
    REPORT_REASONS,
    SAFETY_DIMENSIONS,
    SAFETY_REPORT_REASONS,
    author_visible_reasons,
    decision_label,
    dimension_label,
    dimension_status_label,
    reason_label,
    report_reason_label,
)

__all__ = [
    "DECISIONS",
    "DIMENSIONS",
    "MIN_ALLOW_CONFIDENCE",
    "MODERATION_SCHEMA_SUMMARY",
    "MODERATION_SCHEMA_VERSION",
    "POLICY_VERSION",
    "PUBLISHABLE_DECISIONS",
    "REASON_CODES",
    "REJECT_CONFIDENCE",
    "REJECT_CONFIDENCE_SEVERE",
    "REPORT_REASONS",
    "SAFETY_DIMENSIONS",
    "SAFETY_REPORT_REASONS",
    "THRESHOLDS",
    "CaseResult",
    "DEFAULT_THRESHOLDS",
    "DeterministicFindings",
    "ModerationContext",
    "ModerationSchemaError",
    "PolicyDecision",
    "TextSignals",
    "Thresholds",
    "analyze",
    "author_visible_reasons",
    "build_content_bundle",
    "decide",
    "decision_label",
    "dimension_label",
    "dimension_status_label",
    "flagged_dimensions",
    "flashcard_items",
    "fold_text",
    "format_report",
    "inspect",
    "load_cases",
    "moderation_system_prompt",
    "moderation_text",
    "moderation_user_prompt",
    "moderator_decision",
    "normalize_classification",
    "normalize_text",
    "reason_label",
    "report_reason_label",
    "run_suite",
    "stale_decision",
    "unavailable_classification",
    "unknown_dimensions",
    "validate_classification",
    "UNAVAILABLE_MESSAGE",
    "UNAVAILABLE_LABEL",
    "status_label",
]

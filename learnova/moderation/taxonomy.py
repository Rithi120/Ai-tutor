"""The single source of truth for community-moderation vocabulary.

Every enum here is extensible: add the value to its tuple and a label to each label
map. The schema, the prompt, the policy engine, the stored record and the UI all read
from this module, so a new dimension or reason code never needs a change in more than
one place.

Labels are English source strings so `learnova.translations.translate` localises them
exactly like every other interface string; GERMAN_LABELS documents the reference German
wording the catalogue must carry.

Two deliberate distinctions run through this file:

* **Safety is not relevance.** Content can be perfectly safe and still off-topic, and a
  sensitive topic can be entirely legitimate. They are separate dimensions with separate
  reason codes and separate outcomes, so nothing is ever rejected for being *about*
  something.
* **No dimension decides on its own.** Each dimension carries a role
  (SAFETY_DIMENSIONS, FIT_DIMENSIONS, SIGNAL_DIMENSIONS) that tells
  `learnova.moderation.policy` how much weight a flag may carry. Only the policy engine
  turns dimensions into a decision.
"""

from __future__ import annotations


# ----------------------------------------------------------------------- decisions

# What happens to a submission. "pending" is the state before moderation finishes; it is
# never a result, and content in it is never publicly visible.
DECISIONS = (
    "allow",
    "reject",
    "revision_required",
    "review",
    "pending",
)
DECISION_SET = frozenset(DECISIONS)

# Decisions that permit public visibility. Exactly one, on purpose.
PUBLISHABLE_DECISIONS = frozenset({"allow"})

# Decisions the author can fix and resubmit from.
CORRECTABLE_DECISIONS = frozenset({"revision_required"})

# Decisions that put an item in front of a human.
QUEUED_DECISIONS = frozenset({"review"})


# ---------------------------------------------------------------------- dimensions

# The classified dimensions. Each is judged independently; the policy engine combines
# them. Confidence and evidence sufficiency are reported alongside these but are not
# themselves pass/flag judgements, so they have their own enums below.
DIMENSIONS = (
    "subject_relevance",
    "educational_value",
    "harassment_or_insult",
    "hate_or_targeted_abuse",
    "threats_or_violence",
    "sexual_safety",
    "sexual_content_context",
    "self_harm_or_dangerous_content",
    "privacy_or_personal_information",
    "deception_or_manipulation",
    "obfuscation_risk",
    "age_appropriateness",
    "language_quality",
)
DIMENSION_SET = frozenset(DIMENSIONS)

# Status of one dimension. "unknown" is a first-class answer meaning the content does
# not give enough to judge this dimension; it is never read as a pass or as a violation.
# "not_applicable" means the dimension does not arise for this content at all.
DIMENSION_STATUSES = ("pass", "flag", "unknown", "not_applicable")
DIMENSION_STATUS_SET = frozenset(DIMENSION_STATUSES)

# How much the classifier had to work with. Treated as an ordered signal, never as a
# calibrated probability.
EVIDENCE_LEVELS = ("sufficient", "limited", "insufficient")
EVIDENCE_LEVEL_SET = frozenset(EVIDENCE_LEVELS)

# Dimensions where a confident flag is a safety violation. These are the only ones that
# can produce a rejection.
SAFETY_DIMENSIONS = frozenset({
    "harassment_or_insult",
    "hate_or_targeted_abuse",
    "threats_or_violence",
    "sexual_safety",
    "self_harm_or_dangerous_content",
})

# Safety dimensions severe enough to reject at moderate confidence too, because the cost
# of publishing one of these even once is far higher than the cost of a wrong rejection.
SEVERE_DIMENSIONS = frozenset({
    "hate_or_targeted_abuse",
    "threats_or_violence",
    "sexual_safety",
})

# Dimensions about whether the content fits where it is being published. A flag here is
# a request to revise, never a rejection: off-topic is not dangerous.
FIT_DIMENSIONS = frozenset({
    "subject_relevance",
    "educational_value",
    "age_appropriateness",
    "language_quality",
    "privacy_or_personal_information",
    "sexual_content_context",
})

# Dimensions that are evidence about *how* something was written rather than about what
# it says. A flag here may escalate to human review but can never decide on its own:
# obfuscation is a reason to look closer, not proof of intent.
SIGNAL_DIMENSIONS = frozenset({
    "obfuscation_risk",
    "deception_or_manipulation",
})

assert SAFETY_DIMENSIONS | FIT_DIMENSIONS | SIGNAL_DIMENSIONS == DIMENSION_SET


# -------------------------------------------------------------------- reason codes

# Why a decision was made. Stored on the record and shown to reviewers; only the subset
# in AUTHOR_VISIBLE_REASONS is ever shown to the author, so detection detail that would
# help someone iterate around the system is not handed back to them.
REASON_CODES = (
    # safety
    "HARASSMENT",
    "TARGETED_ABUSE",
    "THREAT",
    "SEXUAL_EXPLOITATION",
    "UNSAFE_SEXUAL_CONTENT",
    "SELF_HARM_OR_DANGEROUS",
    # fit and quality
    "PRIVACY_RISK",
    "OFF_TOPIC_CONTENT",
    "INSUFFICIENT_EDUCATIONAL_CONTEXT",
    "AGE_INAPPROPRIATE",
    "LOW_LANGUAGE_QUALITY",
    "MISLEADING_EDUCATIONAL_CONTENT",
    # signals and process
    "SUSPICIOUS_OBFUSCATION",
    "DECEPTION_OR_MANIPULATION",
    "PROMPT_INJECTION_ATTEMPT",
    "LOW_CONFIDENCE",
    "CONFLICTING_SIGNALS",
    "NEEDS_HUMAN_REVIEW",
    "MODERATION_UNAVAILABLE",
    "REPEATED_USER_REPORTS",
    "CONTENT_CHANGED",
    "MODERATOR_OVERRIDE",
    # explicitly positive: recorded so a reviewer can see the system considered a
    # sensitive topic and judged it legitimate, rather than never having noticed it.
    "LEGITIMATE_SENSITIVE_EDUCATIONAL_CONTENT",
)
REASON_CODE_SET = frozenset(REASON_CODES)

# The reason a flagged dimension contributes when the policy acts on it.
DIMENSION_REASONS = {
    "harassment_or_insult": "HARASSMENT",
    "hate_or_targeted_abuse": "TARGETED_ABUSE",
    "threats_or_violence": "THREAT",
    "sexual_safety": "UNSAFE_SEXUAL_CONTENT",
    "self_harm_or_dangerous_content": "SELF_HARM_OR_DANGEROUS",
    "privacy_or_personal_information": "PRIVACY_RISK",
    "subject_relevance": "OFF_TOPIC_CONTENT",
    "educational_value": "INSUFFICIENT_EDUCATIONAL_CONTEXT",
    "age_appropriateness": "AGE_INAPPROPRIATE",
    "language_quality": "LOW_LANGUAGE_QUALITY",
    "sexual_content_context": "OFF_TOPIC_CONTENT",
    "obfuscation_risk": "SUSPICIOUS_OBFUSCATION",
    "deception_or_manipulation": "DECEPTION_OR_MANIPULATION",
}
assert set(DIMENSION_REASONS) == DIMENSION_SET

# Reasons an author may see. Everything else is reviewer-only: naming the exact detector
# that fired is a bypass instruction.
AUTHOR_VISIBLE_REASONS = frozenset({
    "HARASSMENT",
    "TARGETED_ABUSE",
    "THREAT",
    "SEXUAL_EXPLOITATION",
    "UNSAFE_SEXUAL_CONTENT",
    "SELF_HARM_OR_DANGEROUS",
    "PRIVACY_RISK",
    "OFF_TOPIC_CONTENT",
    "INSUFFICIENT_EDUCATIONAL_CONTEXT",
    "AGE_INAPPROPRIATE",
    "LOW_LANGUAGE_QUALITY",
    "MISLEADING_EDUCATIONAL_CONTENT",
    "CONTENT_CHANGED",
})


# ------------------------------------------------------------------ content types

# What kind of community item is being moderated. The list is open: a new publishable
# surface adds its type here and nothing else changes.
CONTENT_TYPES = (
    "flashcard_set",
    "flashcard",
    "note",
    "explanation",
    "question",
    "answer",
    "title",
    "description",
)
CONTENT_TYPE_SET = frozenset(CONTENT_TYPES)

# Who or what produced a decision.
DECISION_SOURCES = ("deterministic", "classifier", "policy", "moderator", "system")
DECISION_SOURCE_SET = frozenset(DECISION_SOURCES)

# Why a reader reported an item. Extends the existing community.REPORT_REASONS
# vocabulary with the safety reasons this pipeline understands.
REPORT_REASONS = (
    "incorrect",
    "spam",
    "offensive",
    "harassment",
    "sexual",
    "dangerous",
    "copyright",
    "personal_information",
    "duplicate",
    "other",
)
REPORT_REASON_SET = frozenset(REPORT_REASONS)

# Reports that describe a possible safety problem rather than a quality complaint.
SAFETY_REPORT_REASONS = frozenset({
    "offensive", "harassment", "sexual", "dangerous", "personal_information",
})


# ------------------------------------------------------------------------- labels

DECISION_LABELS = {
    # Not "Published": the quality review still runs after an allow and may hold or
    # reject, and the status badge beside this label shows that outcome.
    "allow": "Passed the safety check",
    "reject": "Not published",
    "revision_required": "Changes needed",
    "review": "Waiting for a reviewer",
    "pending": "Being checked",
}

DIMENSION_LABELS = {
    "subject_relevance": "Fits the chosen subject",
    "educational_value": "Educational value",
    "harassment_or_insult": "Insults or harassment",
    "hate_or_targeted_abuse": "Hate or targeted abuse",
    "threats_or_violence": "Threats or violence",
    "sexual_safety": "Sexual safety",
    "sexual_content_context": "Context of sensitive terminology",
    "self_harm_or_dangerous_content": "Self-harm or dangerous instructions",
    "privacy_or_personal_information": "Personal information",
    "deception_or_manipulation": "Deception or manipulation",
    "obfuscation_risk": "Hidden or disguised text",
    "age_appropriateness": "Suitable for the chosen level",
    "language_quality": "Language quality",
}
assert set(DIMENSION_LABELS) == DIMENSION_SET

DIMENSION_STATUS_LABELS = {
    "pass": "No problem found",
    "flag": "Problem found",
    "unknown": "Not enough to judge",
    "not_applicable": "Does not apply",
}

EVIDENCE_LEVEL_LABELS = {
    "sufficient": "Enough evidence",
    "limited": "Limited evidence",
    "insufficient": "Not enough evidence",
}

# Author-facing wording. Respectful, specific enough to act on, and deliberately vague
# about detection: it says what to change, not what tripped.
REASON_LABELS = {
    "HARASSMENT": "Insulting or hurtful language about a person or group",
    "TARGETED_ABUSE": "Content that attacks a person or group",
    "THREAT": "Threatening or violent content",
    "SEXUAL_EXPLOITATION": "Sexual content involving or directed at minors",
    "UNSAFE_SEXUAL_CONTENT": "Sexual content that is not part of the lesson",
    "SELF_HARM_OR_DANGEROUS": "Content that could lead to harm",
    "PRIVACY_RISK": "Personal information about a real person",
    "OFF_TOPIC_CONTENT": "Content that does not match the chosen subject",
    "INSUFFICIENT_EDUCATIONAL_CONTEXT": "Not enough educational content to publish",
    "AGE_INAPPROPRIATE": "Not suitable for the chosen level",
    "LOW_LANGUAGE_QUALITY": "Unclear wording or formatting",
    "MISLEADING_EDUCATIONAL_CONTENT": "Statements that may be inaccurate or misleading",
    "SUSPICIOUS_OBFUSCATION": "Text that could not be read reliably",
    "DECEPTION_OR_MANIPULATION": "Content that appears designed to mislead readers",
    "PROMPT_INJECTION_ATTEMPT": "Text addressed to the review system rather than to learners",
    "LOW_CONFIDENCE": "The automatic check was not confident enough",
    "CONFLICTING_SIGNALS": "The automatic checks disagreed",
    "NEEDS_HUMAN_REVIEW": "A person will look at this",
    "MODERATION_UNAVAILABLE": "The check could not be completed",
    "REPEATED_USER_REPORTS": "Several readers reported this",
    "CONTENT_CHANGED": "The content changed after it was checked",
    "MODERATOR_OVERRIDE": "Decided by a reviewer",
    "LEGITIMATE_SENSITIVE_EDUCATIONAL_CONTENT": "Sensitive topic treated as legitimate teaching material",
}
assert set(REASON_LABELS) == REASON_CODE_SET

REPORT_REASON_LABELS = {
    "incorrect": "Incorrect information",
    "spam": "Spam or advertising",
    "offensive": "Offensive content",
    "harassment": "Harassment or bullying",
    "sexual": "Sexual content",
    "dangerous": "Dangerous content",
    "copyright": "Copyright problem",
    "personal_information": "Personal information",
    "duplicate": "Duplicate of another set",
    "other": "Something else",
}
assert set(REPORT_REASON_LABELS) == REPORT_REASON_SET

# Reference German wording. tests/test_i18n.py keeps the catalogue in step; this map
# documents the intended phrasing so a translator has a fixed target.
GERMAN_LABELS = {
    "Passed the safety check": "Sicherheitsprüfung bestanden",
    "Not published": "Nicht veröffentlicht",
    "Changes needed": "Änderungen erforderlich",
    "Waiting for a reviewer": "Wartet auf eine Prüfung",
    "Being checked": "Wird geprüft",
    "Check could not run": "Prüfung konnte nicht laufen",
    "Fits the chosen subject": "Passt zum gewählten Fach",
    "Educational value": "Lernwert",
    "Insults or harassment": "Beleidigungen oder Belästigung",
    "Hate or targeted abuse": "Hass oder gezielte Angriffe",
    "Threats or violence": "Drohungen oder Gewalt",
    "Sexual safety": "Sexuelle Sicherheit",
    "Context of sensitive terminology": "Kontext sensibler Begriffe",
    "Self-harm or dangerous instructions": "Selbstverletzung oder gefährliche Anleitungen",
    "Personal information": "Persönliche Daten",
    "Deception or manipulation": "Täuschung oder Manipulation",
    "Hidden or disguised text": "Versteckter oder getarnter Text",
    "Suitable for the chosen level": "Für die gewählte Stufe geeignet",
    "Language quality": "Sprachliche Qualität",
}


# A held set whose check never ran is not "waiting for a reviewer": nobody was asked.
UNAVAILABLE_LABEL = 'Check could not run'


def status_label(decision: str, reason_codes) -> str:
    """The headline for a decision, given why it was made.

    Same as `decision_label` except for one case: a hold caused by the check failing is
    labelled as such, because the author can clear it themselves by resubmitting, and
    "Waiting for a reviewer" tells them to do nothing.
    """

    if decision in {"review", "pending"} and "MODERATION_UNAVAILABLE" in set(reason_codes or ()):
        return UNAVAILABLE_LABEL
    return decision_label(decision)


def decision_label(decision: str) -> str:
    """English source label for a decision. Wrap in translate() to localise."""

    return DECISION_LABELS.get(decision, "Being checked")


def dimension_label(dimension: str) -> str:
    """English source label for a dimension. Wrap in translate() to localise."""

    return DIMENSION_LABELS.get(dimension, dimension.replace("_", " ").capitalize())


def dimension_status_label(status: str) -> str:
    """English source label for a dimension status."""

    return DIMENSION_STATUS_LABELS.get(status, "Not enough to judge")


def reason_label(code: str) -> str:
    """English source label for a reason code. Wrap in translate() to localise."""

    return REASON_LABELS.get(code, "A reviewer will look at this")


def report_reason_label(reason: str) -> str:
    """English source label for a report reason."""

    return REPORT_REASON_LABELS.get(reason, "Something else")


def author_visible_reasons(codes: list[str] | tuple[str, ...]) -> list[str]:
    """Filter reason codes to the subset an author may see, preserving order."""

    return [code for code in dict.fromkeys(codes) if code in AUTHOR_VISIBLE_REASONS]

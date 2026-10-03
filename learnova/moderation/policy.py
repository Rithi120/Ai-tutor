"""Stage G: the decision engine. Nothing else in the system decides anything.

Pure functions over plain data, so every rule below can be unit-tested without a request
context, a database or a provider call. The classifier proposes; this module disposes.

Four commitments shape the rules:

* **A sensitive topic is not a violation.** Safety and relevance are separate inputs with
  separate outcomes. Nothing is rejected for being *about* something - only for what it
  does. Content that handles a sensitive subject legitimately is allowed and says so, in
  the record, via LEGITIMATE_SENSITIVE_EDUCATIONAL_CONTENT.
* **Off-topic is not dangerous.** Fit problems produce a revision request. They can never
  produce a rejection, no matter how many of them stack up.
* **Uncertainty escalates; it never rejects and it never publishes.** Unknown statuses,
  thin evidence, low confidence and conflicting signals all route to a human.
* **Rejection needs grounded, confident evidence in a safety dimension.** No score
  threshold, no accumulation of weak signals and no obfuscation finding can reject on its
  own.

Conflict resolution between the deterministic pass and the classifier is explicit in
`_resolve_conflicts`, because "the two layers disagreed" is the case most likely to be
handled by accident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .normalize import DeterministicFindings
from .taxonomy import (
    AUTHOR_VISIBLE_REASONS,
    DIMENSION_REASONS,
    FIT_DIMENSIONS,
    SAFETY_DIMENSIONS,
    SEVERE_DIMENSIONS,
    reason_label,
)

# Bumped on every change to the rules below, stored with each decision, so an audit can
# always reconstruct which policy produced a given outcome.
POLICY_VERSION = "moderation-policy:v1"

# Confidence needed before a safety flag becomes a rejection rather than an escalation.
# Severe categories sit lower because the cost of publishing one of them even once is far
# higher than the cost of a wrong rejection an author can appeal.
#
# These are the numbers most likely to need adjusting once real decisions exist to measure,
# so they are defaults for `Thresholds` rather than constants baked into the rules. The app
# layer builds a `Thresholds` from configuration; the policy itself stays pure and takes it
# as an argument, so a deployment can retune without a code change and a test can pin exact
# values without monkey-patching a module global.
REJECT_CONFIDENCE = 0.75
REJECT_CONFIDENCE_SEVERE = 0.55

# Below this, the classifier is not trusted to clear content on its own.
MIN_ALLOW_CONFIDENCE = 0.40

# How many safety-flavoured reports on live content force a re-check by a human.
SAFETY_REPORT_THRESHOLD = 2
TOTAL_REPORT_THRESHOLD = 5


@dataclass(frozen=True)
class Thresholds:
    """The tunable numbers in the policy, in one place.

    Only thresholds live here. Which dimensions can reject, that fit problems never
    reject, and that uncertainty escalates are properties of the rules, not settings, and
    are deliberately not configurable.
    """

    reject_confidence: float = REJECT_CONFIDENCE
    reject_confidence_severe: float = REJECT_CONFIDENCE_SEVERE
    min_allow_confidence: float = MIN_ALLOW_CONFIDENCE
    safety_reports: int = SAFETY_REPORT_THRESHOLD
    total_reports: int = TOTAL_REPORT_THRESHOLD

    def __post_init__(self) -> None:
        # A severe threshold above the ordinary one would invert the intent: the
        # categories that should reject soonest would end up rejecting last.
        if self.reject_confidence_severe > self.reject_confidence:
            raise ValueError(
                "reject_confidence_severe must not exceed reject_confidence")
        for name in ("reject_confidence", "reject_confidence_severe", "min_allow_confidence"):
            value = getattr(self, name)
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.safety_reports < 1 or self.total_reports < 1:
            raise ValueError("report thresholds must be at least 1")


DEFAULT_THRESHOLDS = Thresholds()

# What the author reads when the automatic check itself failed. Shared with the
# taxonomy label so the two never drift.
UNAVAILABLE_MESSAGE = 'The automatic check could not run, so your set is held unpublished. Try publishing it again.'

# A suggested revision longer than this, or containing markup, is not shown to the author.
MAX_SAFE_REVISION_LENGTH = 400


@dataclass(frozen=True)
class ModerationContext:
    """What the moderator knows about where this content is going.

    Every field is optional. Missing context makes judgements *less* confident, never
    more severe: an unknown grade means age suitability is unknown, not unsuitable.
    """

    content_type: str = "flashcard_set"
    subject: str = ""
    topic: str = ""
    learning_objective: str = ""
    grade: str = ""
    language: str = "en"
    is_public: bool = True
    content_version: int = 1

    @property
    def has_subject(self) -> bool:
        return bool(self.subject.strip()) and self.subject.strip().casefold() != "other"

    @property
    def has_learner_level(self) -> bool:
        return bool(self.grade.strip())


@dataclass(frozen=True)
class PolicyDecision:
    """One decision, everything needed to audit it, and nothing else.

    `rationale` is reviewer-facing and may name which layer fired. `author_message` is
    author-facing and deliberately may not: telling someone which detector caught them is
    a bypass instruction.
    """

    decision: str
    reason_codes: tuple[str, ...] = ()
    requires_review: bool = False
    confidence: float = 0.0
    author_message: str = ""
    suggested_revision: str | None = None
    rationale: tuple[str, ...] = ()
    policy_version: str = POLICY_VERSION
    source: str = "policy"

    @property
    def publishable(self) -> bool:
        return self.decision == "allow"

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_codes": list(self.reason_codes),
            "requires_review": self.requires_review,
            "confidence": self.confidence,
            "author_message": self.author_message,
            "suggested_revision": self.suggested_revision,
            "rationale": list(self.rationale),
            "policy_version": self.policy_version,
            "source": self.source,
        }


@dataclass
class _Working:
    """Mutable scratch space while the rules run; never leaves this module."""

    reasons: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)
    escalate: bool = False

    def add(self, code: str, explanation: str = "") -> None:
        if code not in self.reasons:
            self.reasons.append(code)
        if explanation and explanation not in self.rationale:
            self.rationale.append(explanation)


def _safe_revision(classification: dict[str, Any], findings: DeterministicFindings) -> str | None:
    """Pass the model's revision suggestion through only when it is safe to show.

    The suggestion is model output shown directly to a person, which makes it a path out
    of the sandbox: content that tries to talk to the reviewer could try to talk to the
    author through it too. It is withheld whenever the submission contained text aimed at
    the review system, and whenever it carries markup or a link.
    """

    suggestion = classification.get("suggested_revision")
    if not suggestion or findings.injection_detected:
        return None
    text = " ".join(str(suggestion).split())
    if len(text) > MAX_SAFE_REVISION_LENGTH:
        return None
    if any(marker in text.casefold() for marker in ("<", ">", "http://", "https://", "](")):
        return None
    return text or None


def _resolve_conflicts(
    classification: dict[str, Any], findings: DeterministicFindings, work: _Working,
) -> dict[str, str]:
    """Reconcile the deterministic pass with the classifier and return final statuses.

    The layers see different things, so disagreement is expected rather than exceptional:

    * The deterministic pass reads the *raw bytes*. It sees invisible characters, mixed
      scripts and encoded runs that the classifier never receives, because the classifier
      is given normalized text. Where it reports obfuscation and the classifier reports
      none, the deterministic pass wins and the item escalates.
    * The classifier reads *meaning*. A plainly written insult trips no pattern at all.
      Where it flags a safety dimension and the deterministic pass is silent, the
      classifier wins - silence from a pattern matcher is not evidence of safety.
    * For personal information both layers are competent, and a literal address or phone
      number is a fact rather than a judgement. A deterministic hit stands even when the
      classifier passed the dimension, because the consequence is one edit.

    In every case the more cautious of the two outcomes is taken, and disagreement is
    recorded. Disagreement can escalate; it can never reject.
    """

    statuses = dict(classification.get("dimensions") or {})
    signals = findings.signals

    if findings.privacy_markers and statuses.get("privacy_or_personal_information") == "pass":
        statuses["privacy_or_personal_information"] = "flag"
        work.add("CONFLICTING_SIGNALS",
                 "deterministic contact-pattern match overrode a passing privacy judgement")

    if signals.risk_level != "low" and statuses.get("obfuscation_risk") in {"pass", "not_applicable"}:
        statuses["obfuscation_risk"] = "flag"
        work.add("CONFLICTING_SIGNALS",
                 f"deterministic obfuscation risk {signals.obfuscation_risk:.2f} "
                 "overrode a passing obfuscation judgement")
    elif signals.risk_level != "low":
        statuses["obfuscation_risk"] = "flag"

    if classification.get("unsupported_flags"):
        work.add("NEEDS_HUMAN_REVIEW",
                 "safety flag(s) dropped for citing text not present in the submission: "
                 + ", ".join(classification["unsupported_flags"]))
        work.escalate = True

    return statuses


def _author_message(decision: str, reasons: list[str]) -> str:
    """Build the author-facing explanation from author-visible reasons only.

    English source strings; the caller localises them. Says what to change, never what
    tripped.
    """

    visible = [code for code in reasons if code in AUTHOR_VISIBLE_REASONS]
    if decision == "allow":
        return "Your set passed the safety check."
    if decision == "review":
        # Held because the check *failed*, not because a person is looking. The author
        # can fix this one themselves by resubmitting, so the message says so.
        if "MODERATION_UNAVAILABLE" in reasons:
            return UNAVAILABLE_MESSAGE
        return "Your set is being checked and will appear once the check is complete."
    if decision == "revision_required":
        if not visible:
            return "Please review and update your set before publishing it again."
        return "Please update your set before publishing it again: " + "; ".join(
            reason_label(code) for code in visible[:3]) + "."
    if not visible:
        return "Your set could not be published to the community library."
    return "Your set could not be published: " + "; ".join(
        reason_label(code) for code in visible[:3]) + "."


def decide(
    classification: dict[str, Any],
    findings: DeterministicFindings,
    context: ModerationContext | None = None,
    *,
    safety_reports: int = 0,
    total_reports: int = 0,
    thresholds: Thresholds | None = None,
) -> PolicyDecision:
    """Combine every signal into one decision. The only place a decision is made.

    Rules apply in priority order and the first terminal one wins, so the outcome for any
    given input is reproducible by reading this function top to bottom.
    """

    context = context or ModerationContext()
    thresholds = thresholds or DEFAULT_THRESHOLDS
    work = _Working()
    confidence = float(classification.get("confidence") or 0.0)
    evidence_level = str(classification.get("evidence_sufficiency") or "limited")
    available = bool(classification.get("available", True))

    # -- 1. No usable classification. Fail closed: hold it, do not publish it. --------
    if not available:
        work.add("MODERATION_UNAVAILABLE", "no usable classification was produced")
        work.add("NEEDS_HUMAN_REVIEW")
        return PolicyDecision(
            decision="review", reason_codes=tuple(work.reasons), requires_review=True,
            confidence=0.0, author_message=_author_message("review", work.reasons),
            rationale=tuple(work.rationale))

    # -- 2. Nothing to moderate. Not a safety problem; ask for more content. ----------
    if findings.empty or findings.too_short:
        work.add("INSUFFICIENT_EDUCATIONAL_CONTEXT", "submission is empty or too short to assess")
        return PolicyDecision(
            decision="revision_required", reason_codes=tuple(work.reasons),
            confidence=confidence, author_message=_author_message("revision_required", work.reasons),
            rationale=tuple(work.rationale))

    statuses = _resolve_conflicts(classification, findings, work)
    flagged_safety = [name for name in SAFETY_DIMENSIONS if statuses.get(name) == "flag"]
    flagged_severe = [name for name in flagged_safety if name in SEVERE_DIMENSIONS]
    unknown_safety = [name for name in SAFETY_DIMENSIONS if statuses.get(name) == "unknown"]
    flagged_fit = [name for name in FIT_DIMENSIONS if statuses.get(name) == "flag"]

    # -- 3. A grounded, confident safety flag. The only route to a rejection. ---------
    if flagged_safety and evidence_level != "insufficient":
        threshold = (thresholds.reject_confidence_severe if flagged_severe
                     else thresholds.reject_confidence)
        if confidence >= threshold:
            for name in flagged_safety:
                work.add(DIMENSION_REASONS[name], f"{name} flagged with confidence {confidence:.2f}")
            # Sexual-safety violations aimed at a learner audience are recorded under
            # their own code so they are separable in an audit and in any later report.
            if "sexual_safety" in flagged_safety and context.has_learner_level:
                work.add("SEXUAL_EXPLOITATION", "sexual-safety flag on content for a known learner level")
            return PolicyDecision(
                decision="reject", reason_codes=tuple(work.reasons), confidence=confidence,
                author_message=_author_message("reject", work.reasons),
                rationale=tuple(work.rationale))
        # Flagged but not confidently enough. A person looks; the system does not guess.
        for name in flagged_safety:
            work.add(DIMENSION_REASONS[name], f"{name} flagged with confidence {confidence:.2f}")
        work.add("LOW_CONFIDENCE", f"confidence {confidence:.2f} below the {threshold:.2f} needed to reject")
        work.escalate = True

    # -- 4. A safety flag the evidence does not support. Never reject on this. --------
    if flagged_safety and evidence_level == "insufficient":
        for name in flagged_safety:
            work.add(DIMENSION_REASONS[name], f"{name} flagged on insufficient evidence")
        work.add("NEEDS_HUMAN_REVIEW", "safety concern raised without sufficient evidence")
        work.escalate = True

    # -- 5. Safety dimensions the classifier could not judge at all. ------------------
    if unknown_safety and evidence_level != "sufficient":
        work.add("NEEDS_HUMAN_REVIEW",
                 "unjudged safety dimension(s): " + ", ".join(sorted(unknown_safety)))
        work.escalate = True

    # -- 6. Text addressed to the review system. A signal, never a verdict. -----------
    # A computer-science set may legitimately teach these phrases, so this escalates to a
    # person instead of rejecting. It is treated as data throughout: the prompt quarantines
    # it and `_safe_revision` refuses to echo anything back to the author.
    if findings.injection_detected:
        work.add("PROMPT_INJECTION_ATTEMPT", "submission contains text addressed to the review system")
        work.escalate = True

    # -- 7. Obfuscation. Raises suspicion, decides nothing. ---------------------------
    signals = findings.signals
    if signals.risk_level == "high":
        work.add("SUSPICIOUS_OBFUSCATION",
                 f"obfuscation risk {signals.obfuscation_risk:.2f}: " + "; ".join(signals.evidence[:3]))
        work.escalate = True
    elif signals.risk_level == "medium":
        work.add("SUSPICIOUS_OBFUSCATION",
                 f"obfuscation risk {signals.obfuscation_risk:.2f}: " + "; ".join(signals.evidence[:2]))
        # Medium risk alone is not enough. Combined with any safety doubt it is.
        if flagged_safety or unknown_safety or signals.normalization_changed_meaning:
            work.escalate = True

    # -- 8. Readers reported live content. -------------------------------------------
    if safety_reports >= thresholds.safety_reports or total_reports >= thresholds.total_reports:
        work.add("REPEATED_USER_REPORTS",
                 f"{safety_reports} safety report(s), {total_reports} report(s) in total")
        work.escalate = True

    # -- 9. The classifier is not confident enough to clear this on its own. ----------
    if confidence < thresholds.min_allow_confidence:
        work.add("LOW_CONFIDENCE",
                 f"confidence {confidence:.2f} below the "
                 f"{thresholds.min_allow_confidence:.2f} floor")
        work.escalate = True

    # -- 10. Anything escalated goes to a person, unpublished. ------------------------
    if work.escalate or classification.get("requires_review"):
        work.add("NEEDS_HUMAN_REVIEW")
        return PolicyDecision(
            decision="review", reason_codes=tuple(work.reasons), requires_review=True,
            confidence=confidence, author_message=_author_message("review", work.reasons),
            rationale=tuple(work.rationale))

    # -- 11. Safe, but it does not fit. A revision request, never a rejection. --------
    if flagged_fit:
        for name in flagged_fit:
            work.add(DIMENSION_REASONS[name], f"{name} flagged")
        if "subject_relevance" in flagged_fit and not context.has_subject:
            # No subject was declared, so "off-topic" is not something the author can be
            # held to. Ask for the subject rather than implying they wrote the wrong thing.
            work.add("INSUFFICIENT_EDUCATIONAL_CONTEXT", "no subject declared to judge relevance against")
        return PolicyDecision(
            decision="revision_required", reason_codes=tuple(work.reasons),
            confidence=confidence,
            author_message=_author_message("revision_required", work.reasons),
            suggested_revision=_safe_revision(classification, findings),
            rationale=tuple(work.rationale))

    # -- 12. Clear. Record *why* a sensitive topic was allowed, so the audit shows it. -
    if statuses.get("sexual_content_context") == "pass":
        work.add("LEGITIMATE_SENSITIVE_EDUCATIONAL_CONTENT",
                 "sensitive terminology judged legitimate for the declared subject")
    return PolicyDecision(
        decision="allow", reason_codes=tuple(work.reasons), confidence=confidence,
        author_message=_author_message("allow", work.reasons), rationale=tuple(work.rationale))


def moderator_decision(
    decision: str, *, note: str = "", reviewer: str = "",
) -> PolicyDecision:
    """Wrap a human reviewer's choice in the same shape as an automatic one.

    A person can reach any outcome, including publishing something the classifier
    rejected. The record keeps the source so an audit can tell the two apart.
    """

    normalized = decision if decision in {"allow", "reject", "revision_required", "review"} else "review"
    rationale = [f"decided by reviewer {reviewer}" if reviewer else "decided by a reviewer"]
    if note:
        rationale.append(note[:300])
    return PolicyDecision(
        decision=normalized, reason_codes=("MODERATOR_OVERRIDE",),
        requires_review=normalized == "review", confidence=1.0,
        author_message=_author_message(normalized, ["MODERATOR_OVERRIDE"]),
        rationale=tuple(rationale), source="moderator")


def stale_decision(previous: PolicyDecision | None = None) -> PolicyDecision:
    """The decision that applies when content changed after it was checked.

    Nothing inherits approval across an edit: a stale result is replaced by a pending
    re-check, never reused.
    """

    return PolicyDecision(
        decision="pending", reason_codes=("CONTENT_CHANGED",), requires_review=False,
        confidence=0.0, author_message=_author_message("review", ["CONTENT_CHANGED"]),
        rationale=("content changed since the last decision"
                   + (f"; previous decision was {previous.decision}" if previous else ""),),
        source="system")

"""Which model answers which task, and what to try when it cannot.

Pure and Flask-free. The gateway builds a `RoutingContext` and a `ProviderAvailability`
map, hands them in with the configured `RoutingPolicy`, and gets back an ordered list of
candidates and a hard bound on provider calls. Nothing here talks to a provider.

The policy in one paragraph: Groq is the default for everything. A *premium* model (at
OpenAI, Anthropic or Gemini) leads the list only for a task that is premium-eligible,
only when the request's own signals justify it, and only while that provider has a key,
budget headroom and no recent rate limit. The list always ends with a Groq model, so a
failing premium call falls back to Groq - never the other way round unless the owner has
named an emergency `fallback_model` and that provider is permitted.

Why a validation failure moves to the *next* candidate instead of retrying the same
model: the usage log showed answer_evaluation failing validation on 7 of 21 live calls on
gpt-oss-20b, and the same-model corrective retry rescued 0 of those 7. A different model
with the corrective instructions has a chance; the same one has shown it does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

DEFAULT_PROVIDER = "groq"
DIFFICULTY_RANK = {"easy": 1, "medium": 2, "hard": 3, "expert": 4}


class Tier(str, Enum):
    fast = "fast"
    standard = "standard"
    strong = "strong"
    vision = "vision"


# Every supported task, by what it needs. A task that is not here raises, so a new task
# type cannot drift into premium routing by omission.
TASK_TIERS: dict[str, Tier] = {
    "tutor_chat": Tier.fast,
    "flashcard_back_suggestion": Tier.fast,
    "translation": Tier.fast,
    "diagnosis_verification": Tier.fast,
    "lesson_generation": Tier.standard,
    "quiz_generation": Tier.standard,
    "question_generation": Tier.standard,
    "adaptive_practice": Tier.standard,
    "project_section_generation": Tier.standard,
    "final_exam_generation": Tier.standard,
    "flashcard_generation": Tier.standard,
    "flashcard_review": Tier.standard,
    "answer_evaluation": Tier.standard,
    "content_moderation": Tier.standard,
    "assistant_chat": Tier.standard,
    "competency_extraction": Tier.standard,
    "final_exam_evaluation": Tier.strong,
    "answer_diagnosis": Tier.strong,
    "mistake_analysis": Tier.strong,
    "ocr_document_recognition": Tier.vision,
    "handwriting_region_review": Tier.vision,
    "vocabulary_page_extraction": Tier.vision,
}

# Which premium slot a task may draw on, if its signals justify it.
PREMIUM_SLOTS: dict[str, str] = {
    "answer_evaluation": "grading",
    "final_exam_evaluation": "grading",
    "answer_diagnosis": "reasoning",
    "mistake_analysis": "reasoning",
    "assistant_chat": "reasoning",
}
PREMIUM_ELIGIBLE = frozenset(PREMIUM_SLOTS)

# When a tier's own model fails, which Groq tier to try next. Vision has none: the other
# tiers cannot see the page, so handing them an image would fail somewhere less obvious.
GROQ_FALLBACK_TIER: dict[Tier, Tier | None] = {
    Tier.fast: Tier.strong,
    Tier.standard: Tier.strong,
    Tier.strong: Tier.standard,
    Tier.vision: None,
}

# Failure categories that mean "this provider, right now", so the next candidate should
# be at a different provider; anything else is specific to the call.
PROVIDER_WIDE_FAILURES = frozenset({
    "provider_unavailable", "provider_timeout", "network_failure", "authentication_failure",
    "model_not_found", "provider_quota_exhausted",
})
# Failure categories that mean "this *model*, right now". Providers meter rate limits per
# model, so a 429 on gpt-oss-20b says nothing about gpt-oss-120b at the same provider -
# which is exactly the slower model the student should get instead of an error.
MODEL_SCOPED_FAILURES = frozenset({"provider_rate_limit", "provider_overloaded"})
# Hops that mean a limit was hit on the way to the answer: the student is told that a
# slower model answered (see GatewayResponse.degraded).
LIMIT_FAILURES = frozenset({
    "provider_rate_limit", "provider_overloaded", "provider_quota_exhausted", "model_cooldown",
})
# Categories after which nothing further should be tried at all.
TERMINAL_FAILURES = frozenset({"request_limit_reached", "budget_exhausted", "token_limit_exceeded"})


@dataclass(frozen=True)
class RoutingContext:
    task_type: str
    signals: Mapping[str, Any] = field(default_factory=dict)
    deep: bool = False
    preset: str | None = None
    previous_failure: str | None = None
    requested_model: str | None = None


@dataclass(frozen=True)
class RoutingPolicy:
    """The models each tier resolves to, and the owner's premium choices.

    `premium_models` maps a slot ("reasoning", "grading") to a `provider:model` string;
    an unset slot means premium is disabled for the tasks that would use it. No default
    names a model at the three paid providers: their ids change faster than this code.
    """

    tier_models: Mapping[Tier, str]
    premium_models: Mapping[str, str] = field(default_factory=dict)
    fallback_model: str | None = None
    # The owner's "slower model": tried after the tier's own fallback when a limit was hit
    # upstream, before any emergency provider. Unset = the tier fallback is the slow model.
    slow_model: str | None = None
    max_calls: int = 3

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "RoutingPolicy":
        tier_models = {
            Tier.fast: str(config.get("GROQ_FAST_MODEL") or ""),
            Tier.standard: str(config.get("GROQ_TUTOR_MODEL") or ""),
            Tier.strong: str(config.get("GROQ_ANALYSIS_MODEL") or ""),
            Tier.vision: str(config.get("GROQ_VISION_MODEL") or ""),
        }
        reasoning = str(config.get("AI_PREMIUM_REASONING_MODEL") or "").strip()
        grading = str(config.get("AI_PREMIUM_GRADING_MODEL") or "").strip() or reasoning
        premium = {slot: value for slot, value in (("reasoning", reasoning), ("grading", grading)) if value}
        fallback = str(config.get("AI_FALLBACK_MODEL") or "").strip() or None
        slow = str(config.get("AI_SLOW_MODEL") or "").strip() or None
        return cls(tier_models=tier_models, premium_models=premium, fallback_model=fallback,
                   slow_model=slow,
                   max_calls=max(1, min(5, int(config.get("AI_MAX_PROVIDER_CALLS_PER_REQUEST") or 3))))


@dataclass(frozen=True)
class ProviderAvailability:
    has_key: bool = False
    budget_ok: bool = False
    rate_ok: bool = True
    cooling_down: bool = False

    @property
    def usable(self) -> bool:
        return self.has_key and self.budget_ok and self.rate_ok and not self.cooling_down


@dataclass(frozen=True)
class Candidate:
    model: str
    provider: str
    reason: str


@dataclass(frozen=True)
class RoutePlan:
    tier: Tier
    candidates: tuple[Candidate, ...]
    max_calls: int
    premium_slot: str | None = None
    premium_reason: str | None = None

    @property
    def models(self) -> list[str]:
        return [candidate.model for candidate in self.candidates]


def split_model(model: str | None, *, default_provider: str = DEFAULT_PROVIDER) -> tuple[str, str]:
    """`provider:model` → pair; a bare name belongs to the default provider.

    Deliberately simpler than the gateway's `split_model`: this one never raises, because
    a routing plan must be computable for any string the owner typed. The gateway still
    validates the provider before a call is made.
    """

    text = str(model or "").strip()
    if ":" in text:
        provider, _, name = text.partition(":")
        provider = provider.strip().casefold()
        if provider and provider.isidentifier():
            return provider, name.strip()
    return default_provider, text


def tier_for(task_type: str, context: RoutingContext | None = None) -> Tier:
    try:
        tier = TASK_TIERS[task_type]
    except KeyError as error:
        raise ValueError(f"no routing tier for task {task_type!r}") from error
    if task_type == "assistant_chat" and context is not None and context.deep:
        return Tier.strong
    if task_type == "lesson_generation" and context is not None and context.signals.get("has_images"):
        return Tier.vision
    return tier


def premium_justified(context: RoutingContext) -> str | None:
    """The signal that justifies a premium model for this request, or None.

    Premium is for the hard cases, named here task by task. Everything not named is a
    routine task and never leaves Groq, however much budget there is.
    """

    task = context.task_type
    if task not in PREMIUM_ELIGIBLE:
        return None
    if task in {"final_exam_evaluation", "answer_diagnosis", "mistake_analysis"}:
        return "task_is_premium_grade"
    if task == "answer_evaluation":
        if context.signals.get("is_final"):
            return "final_answer"
        difficulty = str(context.signals.get("difficulty") or "").casefold()
        if DIFFICULTY_RANK.get(difficulty, 0) >= DIFFICULTY_RANK["hard"]:
            return "hard_question"
        return None
    if task == "assistant_chat":
        if context.deep:
            return "think_harder"
        if context.preset == "research":
            return "research_preset"
        return None
    return None


def plan_route(
    context: RoutingContext,
    policy: RoutingPolicy,
    availability: Mapping[str, ProviderAvailability],
) -> RoutePlan:
    """Order the candidates for one request. The list is never empty of Groq."""

    tier = tier_for(context.task_type, context)
    candidates: list[Candidate] = []
    premium_slot = premium_reason = None

    def add(model: str | None, reason: str) -> None:
        if not model:
            return
        provider, _bare = split_model(model)
        if any(existing.model == model for existing in candidates):
            return
        candidates.append(Candidate(model=model, provider=provider, reason=reason))

    # 1. Premium, when - and only when - everything lines up.
    reason = premium_justified(context)
    slot = PREMIUM_SLOTS.get(context.task_type)
    premium_model = policy.premium_models.get(slot or "", "") if reason else ""
    if premium_model:
        provider, _ = split_model(premium_model)
        state = availability.get(provider, ProviderAvailability())
        if provider != DEFAULT_PROVIDER and state.usable:
            add(premium_model, f"premium:{reason}")
            premium_slot, premium_reason = slot, reason
        elif provider == DEFAULT_PROVIDER:
            add(premium_model, f"premium:{reason}")      # the owner chose a Groq model as premium
            premium_slot, premium_reason = slot, reason

    # 2. What the call site asked for (usually the tier's own Groq model).
    requested = context.requested_model or policy.tier_models.get(tier, "")
    tier_model = policy.tier_models.get(tier, "")
    # A previous failure on the same model says: lead with the stronger Groq model.
    fallback_tier = GROQ_FALLBACK_TIER.get(tier)
    fallback_model = policy.tier_models.get(fallback_tier, "") if fallback_tier else ""
    if context.previous_failure and fallback_model:
        add(fallback_model, f"after_failure:{context.previous_failure}")
    add(requested, "requested")
    if requested != tier_model:
        add(tier_model, f"tier:{tier.value}")
    # 3. The Groq fallback for this tier.
    add(fallback_model, f"fallback_tier:{fallback_tier.value}" if fallback_tier else "")
    # 3b. The owner's slower model, where its provider is permitted.
    if policy.slow_model and tier != Tier.vision:
        provider, _ = split_model(policy.slow_model)
        state = availability.get(provider, ProviderAvailability())
        if provider == DEFAULT_PROVIDER or state.usable:
            add(policy.slow_model, "slow_model")
    # 4. The owner's emergency candidate, only where it is permitted.
    if policy.fallback_model:
        provider, _ = split_model(policy.fallback_model)
        state = availability.get(provider, ProviderAvailability())
        if provider == DEFAULT_PROVIDER or state.usable:
            add(policy.fallback_model, "emergency_fallback")

    # Groq candidates that the provider's own state rules out are still kept: Groq is the
    # floor, and the gateway reports a clear error if it fails rather than guessing.
    return RoutePlan(tier=tier, candidates=tuple(candidates), max_calls=policy.max_calls,
                     premium_slot=premium_slot, premium_reason=premium_reason)


def next_candidate(plan: RoutePlan, index: int, failure_category: str) -> int | None:
    """Where to go after candidate `index` failed with `failure_category`.

    A provider-wide failure skips every remaining candidate at that provider; a
    model-scoped one (a rate limit) skips only that model, so the slower model at the same
    provider gets its turn. A terminal failure (a limit, a budget) stops the whole request.
    Anything else - a validation failure, a refusal - simply moves to the next candidate.
    """

    if failure_category in TERMINAL_FAILURES:
        return None
    failed = plan.candidates[index] if 0 <= index < len(plan.candidates) else None
    for position in range(index + 1, len(plan.candidates)):
        candidate = plan.candidates[position]
        if failed is not None and failure_category in PROVIDER_WIDE_FAILURES and candidate.provider == failed.provider:
            continue
        if failed is not None and failure_category in MODEL_SCOPED_FAILURES and candidate.model == failed.model:
            continue
        return position
    return None


def routing_reason(plan: RoutePlan, chosen: int | None, hops: list[str]) -> str:
    """A compact, content-free trace for the ledger: tier, premium, which candidate, hops."""

    premium = plan.premium_reason or "none"
    which = f"{chosen + 1}/{len(plan.candidates)}" if chosen is not None else f"none/{len(plan.candidates)}"
    trace = f"tier={plan.tier.value};premium={premium};candidate={which}"
    if hops:
        trace += ";hops=" + ",".join(hops[:5])
    return trace[:200]

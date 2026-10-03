"""Token budgets, provider caps and spending limits for the AI gateway.

Pure and Flask-free: the gateway hands this module the configured policy and a way to
look up usage totals; it hands back a decision. Nothing here reads a request, a file or
a database.

Why budgets exist at all. The gateway already counted *requests* per user, and that is
abuse protection, not cost control: one request can cost 50 tokens or 20 000. Providers
bill tokens, so budgets are enforced in tokens. Currency is derived from tokens through
per-provider rates and is only shown and capped when rates are actually configured -
an unpriced cap would be a number nobody can check.

Windows are calendar UTC days and months, not rolling periods. "Today's budget is used
up, it resets at midnight UTC" is something a student can act on; "resets some time in
the next 24 hours depending on when you first asked" is not.

The one asymmetric rule: a provider other than the default (Groq) is **not permitted
unless it has a configured cap**. Leaving the cap unset must never mean "unlimited" for
a paid provider; it means "off". For Groq and for the user/global caps, unset keeps the
behaviour the app has always had: no limit.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

DEFAULT_PROVIDER = "groq"
BUDGET_PROVIDERS: tuple[str, ...] = ("groq", "openai", "anthropic", "gemini")
Window = Literal["day", "month"]
WINDOWS: tuple[Window, ...] = ("day", "month")
Unit = Literal["tokens", "requests", "currency"]

# Scope names are part of the error contract: `AIRequestLimitError.scope` carries one of
# these, and `app.ai_failure_message` decides the student-facing wording from it.
USER_SCOPES = frozenset({"user_tokens_day", "user_tokens_month"})
SITE_SCOPES = frozenset({
    "global_tokens_day", "global_tokens_month",
    "provider_tokens_day", "provider_tokens_month",
    "provider_rate", "global_spend_month", "provider_not_permitted",
})


def window_bounds(now: datetime, window: Window) -> tuple[datetime, datetime]:
    """The start of the current calendar window and the moment it resets, both UTC."""

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if window == "day":
        return start, start + timedelta(days=1)
    if window == "month":
        start = start.replace(day=1)
        year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
        return start, start.replace(year=year, month=month)
    raise ValueError(f"unknown window {window!r}")


def retry_after_seconds(resets_at: datetime, now: datetime) -> int:
    """Whole seconds until a window resets; never negative, never zero."""

    if resets_at.tzinfo is None:
        resets_at = resets_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(1, int((resets_at - now).total_seconds()) + 1)


@dataclass(frozen=True)
class UsageTotals:
    requests: int = 0
    tokens: int = 0
    cost: float = 0.0


@dataclass(frozen=True)
class LimitSpec:
    """One configured ceiling: what it applies to, over which window, in which unit."""

    scope: str
    window: Window | None
    configured: float
    unit: Unit
    provider: str | None = None

    @property
    def label(self) -> str:
        who = self.provider or ("everyone" if self.scope.startswith("global") else "you")
        when = f" per {self.window}" if self.window else " per minute"
        return f"{who}{when} ({self.unit})"


@dataclass(frozen=True)
class LimitState:
    spec: LimitSpec
    used: float
    resets_at: datetime | None

    @property
    def remaining(self) -> float:
        return max(0.0, float(self.spec.configured) - float(self.used))

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.spec.scope, "provider": self.spec.provider,
            "window": self.spec.window, "unit": self.spec.unit,
            "configured": self.spec.configured, "used": self.used,
            "remaining": self.remaining,
            "resets_at": self.resets_at.isoformat() if self.resets_at else None,
        }


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    blocking: LimitState | None
    states: tuple[LimitState, ...] = ()


@dataclass(frozen=True)
class BudgetPolicy:
    """Every configured ceiling, read once from configuration.

    `None` means "not configured". For the user and global caps that is "unlimited";
    for a non-default provider's token caps it is "this provider is not permitted".
    """

    user_tokens: Mapping[Window, int | None] = field(default_factory=dict)
    global_tokens: Mapping[Window, int | None] = field(default_factory=dict)
    provider_tokens: Mapping[str, Mapping[Window, int | None]] = field(default_factory=dict)
    provider_requests_per_minute: Mapping[str, int | None] = field(default_factory=dict)
    rates: Mapping[str, tuple[float, float]] = field(default_factory=dict)
    global_spend_month: float | None = None
    default_provider: str = DEFAULT_PROVIDER

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> "BudgetPolicy":
        def optional_int(name: str) -> int | None:
            value = config.get(name)
            return None if value in (None, "") else int(value)

        def optional_float(name: str) -> float | None:
            value = config.get(name)
            return None if value in (None, "") else float(value)

        rates: dict[str, tuple[float, float]] = {}
        fallback_in = float(config.get("AI_INPUT_COST_PER_MILLION") or 0)
        fallback_out = float(config.get("AI_OUTPUT_COST_PER_MILLION") or 0)
        provider_tokens: dict[str, dict[Window, int | None]] = {}
        per_minute: dict[str, int | None] = {}
        for provider in BUDGET_PROVIDERS:
            upper = provider.upper()
            provider_tokens[provider] = {
                "day": optional_int(f"AI_BUDGET_{upper}_TOKENS_PER_DAY"),
                "month": optional_int(f"AI_BUDGET_{upper}_TOKENS_PER_MONTH"),
            }
            per_minute[provider] = optional_int(f"AI_RATE_{upper}_REQUESTS_PER_MINUTE")
            rate_in = optional_float(f"AI_COST_{upper}_INPUT_PER_MILLION")
            rate_out = optional_float(f"AI_COST_{upper}_OUTPUT_PER_MILLION")
            if rate_in is not None or rate_out is not None or fallback_in or fallback_out:
                rates[provider] = (
                    fallback_in if rate_in is None else rate_in,
                    fallback_out if rate_out is None else rate_out,
                )
        return cls(
            user_tokens={"day": optional_int("AI_BUDGET_USER_TOKENS_PER_DAY"),
                         "month": optional_int("AI_BUDGET_USER_TOKENS_PER_MONTH")},
            global_tokens={"day": optional_int("AI_BUDGET_GLOBAL_TOKENS_PER_DAY"),
                           "month": optional_int("AI_BUDGET_GLOBAL_TOKENS_PER_MONTH")},
            provider_tokens=provider_tokens,
            provider_requests_per_minute=per_minute,
            rates=rates,
            global_spend_month=optional_float("AI_BUDGET_GLOBAL_SPEND_PER_MONTH"),
            default_provider=str(config.get("AI_DEFAULT_PROVIDER") or DEFAULT_PROVIDER),
        )


def provider_permitted(policy: BudgetPolicy, provider: str) -> bool:
    """The default provider always is; any other needs at least one configured token cap."""

    if provider == policy.default_provider:
        return True
    caps = policy.provider_tokens.get(provider, {})
    return any(caps.get(window) is not None for window in WINDOWS)


def has_rates(policy: BudgetPolicy, provider: str) -> bool:
    rate = policy.rates.get(provider)
    return bool(rate) and (rate[0] > 0 or rate[1] > 0)


def cost_for(policy: BudgetPolicy, provider: str, input_tokens: int, output_tokens: int) -> float:
    """Estimated cost in the configured currency; 0 when the provider has no rates."""

    rate_in, rate_out = policy.rates.get(provider, (0.0, 0.0))
    return round(input_tokens * rate_in / 1_000_000 + output_tokens * rate_out / 1_000_000, 8)


def applicable_limits(policy: BudgetPolicy, *, provider: str, has_user: bool) -> list[LimitSpec]:
    """The ceilings that apply to one call, in the order they are checked.

    Order is the priority a student would expect to hear about: their own budget first,
    then the site's, then the provider's. The first one that blocks is the one reported.
    """

    specs: list[LimitSpec] = []
    if has_user:
        for window in WINDOWS:
            value = policy.user_tokens.get(window)
            if value is not None:
                specs.append(LimitSpec(f"user_tokens_{window}", window, value, "tokens"))
    for window in WINDOWS:
        value = policy.global_tokens.get(window)
        if value is not None:
            specs.append(LimitSpec(f"global_tokens_{window}", window, value, "tokens"))
    for window in WINDOWS:
        value = policy.provider_tokens.get(provider, {}).get(window)
        if value is not None:
            specs.append(LimitSpec(f"provider_tokens_{window}", window, value, "tokens", provider))
    per_minute = policy.provider_requests_per_minute.get(provider)
    if per_minute is not None:
        specs.append(LimitSpec("provider_rate", None, per_minute, "requests", provider))
    if policy.global_spend_month is not None and has_rates(policy, provider):
        specs.append(LimitSpec("global_spend_month", "month", policy.global_spend_month, "currency"))
    return specs


def evaluate(
    policy: BudgetPolicy,
    totals_for: Callable[[LimitSpec, datetime], UsageTotals],
    *,
    provider: str,
    has_user: bool,
    anticipated_tokens: int,
    anticipated_cost: float = 0.0,
    now: datetime | None = None,
) -> BudgetDecision:
    """Decide whether one more call fits under every applicable ceiling.

    Headroom rule: a call is allowed only if `used + anticipated <= configured` for each
    ceiling. The anticipated amount is the input estimate plus the full output budget,
    so a call that *might* use its whole budget is only admitted when that would still
    fit - the reservation the gateway then holds is for the same amount.
    """

    now = now or datetime.now(timezone.utc)
    states: list[LimitState] = []
    blocking: LimitState | None = None
    for spec in applicable_limits(policy, provider=provider, has_user=has_user):
        if spec.window is not None:
            window_start, resets_at = window_bounds(now, spec.window)
        else:
            window_start, resets_at = now - timedelta(minutes=1), now + timedelta(minutes=1)
        totals = totals_for(spec, window_start)
        if spec.unit == "tokens":
            used, extra = float(totals.tokens), float(anticipated_tokens)
        elif spec.unit == "requests":
            used, extra = float(totals.requests), 1.0
        else:
            used, extra = float(totals.cost), float(anticipated_cost)
        state = LimitState(spec, used, resets_at)
        states.append(state)
        if blocking is None and used + extra > float(spec.configured):
            blocking = state
    return BudgetDecision(allowed=blocking is None, blocking=blocking, states=tuple(states))

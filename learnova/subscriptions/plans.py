"""Subscription settings: the two plans and their limits, read once at startup.

Every value has a safe default and is validated when the app starts, so a typo in an
environment variable fails the deploy instead of silently granting or blocking access.

The chat allowance is measured in *credits*, never shown to students. One credit is one
input token on a weight-1 model; an output token costs CHAT_OUTPUT_WEIGHT credits
(providers bill output at several times the input rate), and a model's total is
multiplied by its weight from FREE_CHAT_MODEL_WEIGHTS. So a more expensive model uses
the free allowance up faster, and the allowance tracks cost rather than message count.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from .periods import PERIODS, Period, resolve_timezone

FREE = "free"
PREMIUM = "premium"
# The AI tasks the free chat allowance covers. Everything else (grading an answer,
# reading a page) belongs to a lesson or project the student is already allowed.
CHAT_TASK_TYPES = frozenset({"tutor_chat", "assistant_chat"})
QUOTA_KINDS = ("project", "lesson", "chat")


@dataclass(frozen=True)
class SubscriptionSettings:
    premium_price_eur: Decimal = Decimal("3.00")
    code_duration_months: int = 1
    code_max_duration_months: int = 12
    code_validity_days: int = 90
    free_projects_per_window: int = 1
    free_project_window_days: int = 7
    free_lessons_per_day: int = 2
    free_chat_credits_per_period: int = 150_000
    free_chat_period: Period = "day"
    chat_output_weight: float = 4.0
    chat_model_weights: Mapping[str, float] = field(default_factory=dict)
    timezone_name: str = "Europe/Berlin"
    reservation_ttl_minutes: int = 15
    redeem_attempts_per_account: int = 5
    redeem_attempts_per_source: int = 20
    redeem_window_minutes: int = 15
    payment_instructions: str = ""

    @property
    def zone(self):
        return resolve_timezone(self.timezone_name)

    def model_weight(self, provider: str, model: str) -> float:
        """`provider:model` first, then the provider, then 1."""

        weights = self.chat_model_weights
        bare = model.split(":", 1)[1] if ":" in model else model
        for key in (f"{provider}:{bare}".casefold(), provider.casefold()):
            if key in weights:
                return weights[key]
        return 1.0

    def chat_credits(self, provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
        raw = max(0, input_tokens) + self.chat_output_weight * max(0, output_tokens)
        return round(raw * self.model_weight(provider, model), 2)

    def max_model_weight(self) -> float:
        return max([1.0, *self.chat_model_weights.values()])


def _int(env: Mapping[str, str], name: str, default: int, *, minimum: int, maximum: int) -> int:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a whole number") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _float(env: Mapping[str, str], name: str, default: float, *, minimum: float, maximum: float) -> float:
    raw = str(env.get(name, "")).strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a number") from error
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _weights(raw: str) -> dict[str, float]:
    """`groq=1, openai=5, anthropic:claude-x=8` -> {"groq": 1.0, ...}."""

    weights: dict[str, float] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        key, separator, value = item.partition("=")
        try:
            weight = float(value)
        except ValueError:
            weight = -1.0
        if not separator or not key.strip() or not 0 < weight <= 1000:
            raise RuntimeError("FREE_CHAT_MODEL_WEIGHTS entries look like provider=weight or provider:model=weight")
        weights[key.strip().casefold()] = weight
    return weights


def load_settings(env: Mapping[str, str] | None = None) -> SubscriptionSettings:
    env = os.environ if env is None else env
    raw_price = str(env.get("PREMIUM_MONTHLY_PRICE_EUR", "")).strip() or "3.00"
    try:
        price = Decimal(raw_price.replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation as error:
        raise RuntimeError("PREMIUM_MONTHLY_PRICE_EUR must be an amount such as 3.00") from error
    if not Decimal("0") < price <= Decimal("1000"):
        raise RuntimeError("PREMIUM_MONTHLY_PRICE_EUR must be between 0.01 and 1000")
    period = str(env.get("FREE_CHAT_PERIOD", "")).strip().lower() or "day"
    if period not in PERIODS:
        raise RuntimeError(f"FREE_CHAT_PERIOD must be one of {', '.join(PERIODS)}")
    zone_name = str(env.get("APP_TIMEZONE", "")).strip() or "Europe/Berlin"
    try:
        resolve_timezone(zone_name)
    except ValueError as error:
        raise RuntimeError(f"APP_TIMEZONE {zone_name!r} is not a known timezone (install tzdata)") from error
    max_months = _int(env, "PREMIUM_CODE_MAX_DURATION_MONTHS", 12, minimum=1, maximum=24)
    return SubscriptionSettings(
        premium_price_eur=price,
        code_duration_months=_int(env, "PREMIUM_CODE_DURATION_MONTHS", 1, minimum=1, maximum=max_months),
        code_max_duration_months=max_months,
        code_validity_days=_int(env, "PREMIUM_CODE_VALIDITY_DAYS", 90, minimum=1, maximum=730),
        free_projects_per_window=_int(env, "FREE_PROJECTS_PER_WINDOW", 1, minimum=0, maximum=1000),
        free_project_window_days=_int(env, "FREE_PROJECT_WINDOW_DAYS", 7, minimum=1, maximum=90),
        free_lessons_per_day=_int(env, "FREE_LESSONS_PER_DAY", 2, minimum=0, maximum=1000),
        free_chat_credits_per_period=_int(env, "FREE_CHAT_CREDITS_PER_PERIOD", 150_000,
                                          minimum=0, maximum=1_000_000_000),
        free_chat_period=period,  # pyright: ignore[reportArgumentType]
        chat_output_weight=_float(env, "FREE_CHAT_OUTPUT_WEIGHT", 4.0, minimum=1.0, maximum=50.0),
        chat_model_weights=_weights(str(env.get("FREE_CHAT_MODEL_WEIGHTS", ""))),
        timezone_name=zone_name,
        reservation_ttl_minutes=_int(env, "QUOTA_RESERVATION_TTL_MINUTES", 15, minimum=2, maximum=240),
        redeem_attempts_per_account=_int(env, "PREMIUM_REDEEM_ATTEMPTS_PER_ACCOUNT", 5, minimum=1, maximum=100),
        redeem_attempts_per_source=_int(env, "PREMIUM_REDEEM_ATTEMPTS_PER_SOURCE", 20, minimum=1, maximum=1000),
        redeem_window_minutes=_int(env, "PREMIUM_REDEEM_WINDOW_MINUTES", 15, minimum=1, maximum=1440),
        payment_instructions=str(env.get("PREMIUM_PAYMENT_INSTRUCTIONS", "")).strip()[:500],
    )


def settings_from_config(config: Mapping[str, Any]) -> SubscriptionSettings:
    """The settings the app loaded at startup (tests may replace them on app.config)."""

    value = config.get("SUBSCRIPTION_SETTINGS")
    return value if isinstance(value, SubscriptionSettings) else SubscriptionSettings()

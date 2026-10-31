"""Subscriptions: the Free and Premium plans, activation codes and usage allowances.

See docs/SUBSCRIPTIONS.md for the operator workflow (cash payment -> code -> student).
"""

from .activation import ActivationService, IssuedCode, RedeemResult
from .entitlements import Allowance, EntitlementService, Entitlements, QuotaExceededError, Reservation
from .plans import CHAT_TASK_TYPES, FREE, PREMIUM, SubscriptionSettings, load_settings, settings_from_config

__all__ = [
    "ActivationService", "Allowance", "CHAT_TASK_TYPES", "EntitlementService", "Entitlements",
    "FREE", "IssuedCode", "PREMIUM", "QuotaExceededError", "RedeemResult", "Reservation",
    "SubscriptionSettings", "load_settings", "settings_from_config",
]

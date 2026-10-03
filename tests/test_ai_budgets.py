"""Token budgets: the pure rules the gateway enforces.

Budgets exist because the gateway only ever counted *requests* per user, and a request
can cost 50 tokens or 20 000. These tests pin the rules that make a budget trustworthy:
windows are calendar UTC, an unset cap means what it always did (no limit) except for a
paid provider (off), and the headroom rule admits a call only if its whole anticipated
cost still fits.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from flask import Flask

from learnova.ai_services import budgets
from learnova.ai_services.budgets import BudgetPolicy, LimitSpec, UsageTotals
from learnova.config import configure_app

UTC = timezone.utc


def policy_from_env(**environment):
    cleared = {name: "" for name in (
        "AI_BUDGET_USER_TOKENS_PER_DAY", "AI_BUDGET_USER_TOKENS_PER_MONTH",
        "AI_BUDGET_GLOBAL_TOKENS_PER_DAY", "AI_BUDGET_GLOBAL_TOKENS_PER_MONTH",
        "AI_BUDGET_GLOBAL_SPEND_PER_MONTH", "AI_INPUT_COST_PER_MILLION", "AI_OUTPUT_COST_PER_MILLION")}
    for provider in ("GROQ", "OPENAI", "ANTHROPIC", "GEMINI"):
        for suffix in ("TOKENS_PER_DAY", "TOKENS_PER_MONTH"):
            cleared[f"AI_BUDGET_{provider}_{suffix}"] = ""
        cleared[f"AI_RATE_{provider}_REQUESTS_PER_MINUTE"] = ""
        cleared[f"AI_COST_{provider}_INPUT_PER_MILLION"] = ""
        cleared[f"AI_COST_{provider}_OUTPUT_PER_MILLION"] = ""
    with patch.dict(os.environ, {"APP_ENV": "development", "AI_MODE": "cached", **cleared, **environment}):
        for name, value in cleared.items():
            if name not in environment:
                os.environ.pop(name, None)
        application = Flask("budget-test", instance_path=tempfile.mkdtemp())
        configure_app(application, "development")
        return BudgetPolicy.from_config(application.config)


class WindowTests(unittest.TestCase):
    def test_a_day_window_is_the_calendar_utc_day(self):
        now = datetime(2026, 10, 1, 15, 42, tzinfo=UTC)
        start, resets = budgets.window_bounds(now, "day")
        self.assertEqual(start, datetime(2026, 10, 1, tzinfo=UTC))
        self.assertEqual(resets, datetime(2026, 10, 2, tzinfo=UTC))

    def test_a_month_window_rolls_over_december(self):
        start, resets = budgets.window_bounds(datetime(2026, 12, 31, 23, 59, tzinfo=UTC), "month")
        self.assertEqual(start, datetime(2026, 12, 1, tzinfo=UTC))
        self.assertEqual(resets, datetime(2027, 1, 1, tzinfo=UTC))

    def test_a_naive_timestamp_is_taken_as_utc(self):
        start, _ = budgets.window_bounds(datetime(2026, 10, 1, 3, 0), "day")
        self.assertEqual(start, datetime(2026, 10, 1, tzinfo=UTC))

    def test_a_non_utc_timestamp_is_converted(self):
        # 01:00 in Berlin on the 2nd is still the 1st in UTC.
        berlin = timezone(timedelta(hours=2))
        start, _ = budgets.window_bounds(datetime(2026, 10, 2, 1, 0, tzinfo=berlin), "day")
        self.assertEqual(start, datetime(2026, 10, 1, tzinfo=UTC))

    def test_retry_after_is_whole_seconds_and_never_zero(self):
        now = datetime(2026, 10, 1, 23, 59, 59, 500000, tzinfo=UTC)
        self.assertEqual(budgets.retry_after_seconds(datetime(2026, 10, 2, tzinfo=UTC), now), 1)
        self.assertEqual(budgets.retry_after_seconds(now, now), 1)
        self.assertEqual(budgets.retry_after_seconds(now + timedelta(minutes=2), now), 121)


class PolicyFromConfigTests(unittest.TestCase):
    def test_everything_unset_means_no_limits_and_only_groq_permitted(self):
        policy = policy_from_env()
        self.assertEqual(dict(policy.user_tokens), {"day": None, "month": None})
        self.assertEqual(dict(policy.global_tokens), {"day": None, "month": None})
        self.assertIsNone(policy.global_spend_month)
        self.assertEqual(policy.rates, {})
        self.assertTrue(budgets.provider_permitted(policy, "groq"))
        for provider in ("openai", "anthropic", "gemini"):
            self.assertFalse(budgets.provider_permitted(policy, provider), provider)

    def test_every_documented_key_is_read(self):
        policy = policy_from_env(
            AI_BUDGET_USER_TOKENS_PER_DAY="1000", AI_BUDGET_USER_TOKENS_PER_MONTH="20000",
            AI_BUDGET_GLOBAL_TOKENS_PER_DAY="50000", AI_BUDGET_GLOBAL_TOKENS_PER_MONTH="900000",
            AI_BUDGET_ANTHROPIC_TOKENS_PER_MONTH="300000", AI_RATE_GROQ_REQUESTS_PER_MINUTE="30",
            AI_COST_ANTHROPIC_INPUT_PER_MILLION="3", AI_COST_ANTHROPIC_OUTPUT_PER_MILLION="15",
            AI_BUDGET_GLOBAL_SPEND_PER_MONTH="25")
        self.assertEqual(policy.user_tokens["day"], 1000)
        self.assertEqual(policy.user_tokens["month"], 20000)
        self.assertEqual(policy.global_tokens["day"], 50000)
        self.assertEqual(policy.global_tokens["month"], 900000)
        self.assertEqual(policy.provider_tokens["anthropic"]["month"], 300000)
        self.assertIsNone(policy.provider_tokens["anthropic"]["day"])
        self.assertEqual(policy.provider_requests_per_minute["groq"], 30)
        self.assertEqual(policy.rates["anthropic"], (3.0, 15.0))
        self.assertEqual(policy.global_spend_month, 25.0)
        self.assertTrue(budgets.provider_permitted(policy, "anthropic"))

    def test_a_provider_cap_of_zero_is_a_configured_cap(self):
        # "Permitted, with nothing to spend" is distinct from "not configured": the
        # provider shows up on the limits page with 0 remaining instead of vanishing.
        policy = policy_from_env(AI_BUDGET_OPENAI_TOKENS_PER_DAY="0")
        self.assertTrue(budgets.provider_permitted(policy, "openai"))
        self.assertEqual(policy.provider_tokens["openai"]["day"], 0)

    def test_the_legacy_global_rate_pair_is_every_providers_fallback(self):
        policy = policy_from_env(AI_INPUT_COST_PER_MILLION="1", AI_OUTPUT_COST_PER_MILLION="2",
                                 AI_COST_GEMINI_OUTPUT_PER_MILLION="9")
        self.assertEqual(policy.rates["groq"], (1.0, 2.0))
        self.assertEqual(policy.rates["gemini"], (1.0, 9.0), "input falls back, output is its own")

    def test_a_malformed_value_fails_at_startup_not_at_request_time(self):
        with self.assertRaises(RuntimeError):
            policy_from_env(AI_BUDGET_GLOBAL_TOKENS_PER_DAY="lots")


class CostTests(unittest.TestCase):
    def test_cost_is_per_million_tokens_per_direction(self):
        policy = BudgetPolicy(rates={"openai": (2.0, 8.0)})
        self.assertAlmostEqual(budgets.cost_for(policy, "openai", 500_000, 250_000), 1.0 + 2.0)

    def test_no_rates_means_zero_cost_not_an_error(self):
        self.assertEqual(budgets.cost_for(BudgetPolicy(), "groq", 1000, 1000), 0.0)


class ApplicableLimitTests(unittest.TestCase):
    def test_order_is_user_then_site_then_provider_then_rate_then_spend(self):
        policy = BudgetPolicy(
            user_tokens={"day": 10, "month": None}, global_tokens={"day": None, "month": 100},
            provider_tokens={"groq": {"day": 50, "month": None}},
            provider_requests_per_minute={"groq": 5}, rates={"groq": (1.0, 1.0)},
            global_spend_month=9.0)
        scopes = [spec.scope for spec in budgets.applicable_limits(policy, provider="groq", has_user=True)]
        self.assertEqual(scopes, ["user_tokens_day", "global_tokens_month", "provider_tokens_day",
                                  "provider_rate", "global_spend_month"])

    def test_anonymous_callers_have_no_user_limits(self):
        policy = BudgetPolicy(user_tokens={"day": 10, "month": 10})
        self.assertEqual(budgets.applicable_limits(policy, provider="groq", has_user=False), [])

    def test_a_spend_cap_only_applies_to_a_priced_provider(self):
        policy = BudgetPolicy(global_spend_month=5.0, rates={"openai": (1.0, 1.0)})
        self.assertEqual([s.scope for s in budgets.applicable_limits(policy, provider="groq", has_user=False)], [])
        self.assertEqual([s.scope for s in budgets.applicable_limits(policy, provider="openai", has_user=False)],
                         ["global_spend_month"])


def totals(**by_scope):
    def lookup(spec: LimitSpec, _window_start):
        return by_scope.get(spec.scope, UsageTotals())
    return lookup


class EvaluateTests(unittest.TestCase):
    NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)

    def test_headroom_rule_admits_a_call_that_exactly_fits(self):
        policy = BudgetPolicy(global_tokens={"day": 1000, "month": None})
        decision = budgets.evaluate(
            policy, totals(global_tokens_day=UsageTotals(tokens=600)),
            provider="groq", has_user=False, anticipated_tokens=400, now=self.NOW)
        self.assertTrue(decision.allowed)

    def test_headroom_rule_blocks_a_call_that_would_go_one_over(self):
        policy = BudgetPolicy(global_tokens={"day": 1000, "month": None})
        decision = budgets.evaluate(
            policy, totals(global_tokens_day=UsageTotals(tokens=600)),
            provider="groq", has_user=False, anticipated_tokens=401, now=self.NOW)
        self.assertFalse(decision.allowed)
        assert decision.blocking is not None
        self.assertEqual(decision.blocking.spec.scope, "global_tokens_day")
        self.assertEqual(decision.blocking.resets_at, datetime(2026, 10, 2, tzinfo=UTC))
        self.assertEqual(decision.blocking.remaining, 400)

    def test_the_first_blocking_limit_in_order_is_the_one_reported(self):
        policy = BudgetPolicy(user_tokens={"day": 100, "month": None},
                              global_tokens={"day": 100, "month": None})
        decision = budgets.evaluate(
            policy, totals(user_tokens_day=UsageTotals(tokens=100), global_tokens_day=UsageTotals(tokens=100)),
            provider="groq", has_user=True, anticipated_tokens=1, now=self.NOW)
        assert decision.blocking is not None
        self.assertEqual(decision.blocking.spec.scope, "user_tokens_day")
        self.assertEqual(len(decision.states), 2, "every applicable limit is still reported")

    def test_a_rate_limit_counts_requests_not_tokens(self):
        policy = BudgetPolicy(provider_requests_per_minute={"groq": 2})
        allowed = budgets.evaluate(policy, totals(provider_rate=UsageTotals(requests=1)),
                                   provider="groq", has_user=False, anticipated_tokens=99999, now=self.NOW)
        blocked = budgets.evaluate(policy, totals(provider_rate=UsageTotals(requests=2)),
                                   provider="groq", has_user=False, anticipated_tokens=1, now=self.NOW)
        self.assertTrue(allowed.allowed)
        self.assertFalse(blocked.allowed)

    def test_a_spend_cap_uses_anticipated_cost(self):
        policy = BudgetPolicy(global_spend_month=10.0, rates={"openai": (1.0, 1.0)})
        decision = budgets.evaluate(policy, totals(global_spend_month=UsageTotals(cost=9.5)),
                                    provider="openai", has_user=False, anticipated_tokens=1,
                                    anticipated_cost=0.6, now=self.NOW)
        self.assertFalse(decision.allowed)

    def test_no_limits_configured_always_allows(self):
        decision = budgets.evaluate(BudgetPolicy(), totals(), provider="groq", has_user=True,
                                    anticipated_tokens=10**9, now=self.NOW)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.states, ())

    def test_limit_state_serialises_for_the_admin_page(self):
        policy = BudgetPolicy(global_tokens={"day": 10, "month": None})
        state = budgets.evaluate(policy, totals(global_tokens_day=UsageTotals(tokens=4)),
                                 provider="groq", has_user=False, anticipated_tokens=1, now=self.NOW).states[0]
        self.assertEqual(state.as_dict(), {
            "scope": "global_tokens_day", "provider": None, "window": "day", "unit": "tokens",
            "configured": 10, "used": 4.0, "remaining": 6.0, "resets_at": "2026-10-02T00:00:00+00:00"})


class ScopeContractTests(unittest.TestCase):
    def test_site_and_user_scopes_are_disjoint_and_cover_every_evaluated_scope(self):
        self.assertFalse(budgets.USER_SCOPES & budgets.SITE_SCOPES)
        policy = BudgetPolicy(
            user_tokens={"day": 1, "month": 1}, global_tokens={"day": 1, "month": 1},
            provider_tokens={"groq": {"day": 1, "month": 1}}, provider_requests_per_minute={"groq": 1},
            rates={"groq": (1.0, 1.0)}, global_spend_month=1.0)
        for spec in budgets.applicable_limits(policy, provider="groq", has_user=True):
            self.assertIn(spec.scope, budgets.USER_SCOPES | budgets.SITE_SCOPES, spec.scope)


if __name__ == "__main__":
    unittest.main()

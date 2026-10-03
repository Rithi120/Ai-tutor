"""Task-based routing: Groq by default, premium only where it is justified and affordable.

Pure tests of learnova.ai_services.routing. The policy they pin: every task has a tier;
every plan is Groq unless a premium-eligible task has a justifying signal *and* the premium
provider has a key, budget and no recent rate limit; at most one premium candidate; the
list always ends in Groq; a validation failure moves to the next candidate (the usage log
showed the same-model retry rescued 0 of 7 failed gradings); vision never falls back to a
model that cannot see.
"""

import unittest

from learnova.ai_services import routing
from learnova.ai_services.routing import (
    Candidate, ProviderAvailability, RoutePlan, RoutingContext, RoutingPolicy, Tier,
)
from learnova.ai_services.service import SUPPORTED_TASK_TYPES

GROQ = {Tier.fast: "openai/gpt-oss-20b", Tier.standard: "openai/gpt-oss-20b",
        Tier.strong: "openai/gpt-oss-120b", Tier.vision: "qwen/qwen3.8-27b"}
USABLE = ProviderAvailability(has_key=True, budget_ok=True, rate_ok=True, cooling_down=False)
AVAILABLE = {"groq": USABLE, "anthropic": USABLE, "openai": USABLE, "gemini": USABLE}
PREMIUM = RoutingPolicy(tier_models=GROQ, premium_models={"reasoning": "anthropic:claude-x", "grading": "openai:gpt-x"})
GROQ_ONLY = RoutingPolicy(tier_models=GROQ)


def plan(task, policy=GROQ_ONLY, availability=AVAILABLE, **context):
    return routing.plan_route(RoutingContext(task_type=task, **context), policy, availability)


class TierTableTests(unittest.TestCase):
    def test_every_supported_task_has_a_tier(self):
        self.assertEqual(set(routing.TASK_TIERS), SUPPORTED_TASK_TYPES)

    def test_an_unknown_task_cannot_drift_into_a_tier(self):
        with self.assertRaises(ValueError):
            routing.tier_for("brand_new_task")

    def test_the_simple_tasks_are_fast_and_the_hard_ones_strong(self):
        for task in ("tutor_chat", "flashcard_back_suggestion", "translation", "diagnosis_verification"):
            self.assertEqual(routing.TASK_TIERS[task], Tier.fast, task)
        for task in ("final_exam_evaluation", "answer_diagnosis", "mistake_analysis"):
            self.assertEqual(routing.TASK_TIERS[task], Tier.strong, task)
        self.assertEqual(routing.TASK_TIERS["ocr_document_recognition"], Tier.vision)

    def test_premium_eligibility_is_an_explicit_short_list(self):
        self.assertEqual(routing.PREMIUM_ELIGIBLE, {
            "answer_evaluation", "final_exam_evaluation", "answer_diagnosis",
            "mistake_analysis", "assistant_chat"})

    def test_the_assistant_moves_up_a_tier_when_asked_to_think_harder(self):
        self.assertEqual(routing.tier_for("assistant_chat", RoutingContext("assistant_chat", deep=True)), Tier.strong)
        self.assertEqual(routing.tier_for("assistant_chat", RoutingContext("assistant_chat")), Tier.standard)

    def test_a_lesson_from_photos_is_a_vision_task(self):
        self.assertEqual(routing.tier_for("lesson_generation",
                                          RoutingContext("lesson_generation", signals={"has_images": True})), Tier.vision)


class DefaultPlanTests(unittest.TestCase):
    def test_a_routine_task_is_groq_then_stronger_groq(self):
        result = plan("flashcard_generation", requested_model="openai/gpt-oss-20b")
        self.assertEqual(result.models, ["openai/gpt-oss-20b", "openai/gpt-oss-120b"])
        self.assertEqual([c.provider for c in result.candidates], ["groq", "groq"])
        self.assertIsNone(result.premium_reason)

    def test_every_plan_ends_in_groq(self):
        for task in SUPPORTED_TASK_TYPES:
            result = plan(task, PREMIUM, signals={"is_final": True}, deep=True)
            self.assertEqual(result.candidates[-1].provider, "groq", task)

    def test_the_call_sites_model_leads_when_it_differs_from_the_tier_model(self):
        result = plan("answer_diagnosis", requested_model="groq:some-special-model")
        self.assertEqual(result.candidates[0].model, "groq:some-special-model")
        self.assertIn("openai/gpt-oss-120b", result.models)

    def test_vision_has_no_cross_model_fallback(self):
        result = plan("ocr_document_recognition", requested_model="qwen/qwen3.8-27b")
        self.assertEqual(result.models, ["qwen/qwen3.8-27b"])

    def test_a_previous_failure_leads_with_the_stronger_groq_model(self):
        result = plan("question_generation", requested_model="openai/gpt-oss-20b", previous_failure="validation")
        self.assertEqual(result.models[0], "openai/gpt-oss-120b")
        self.assertTrue(result.candidates[0].reason.startswith("after_failure"))

    def test_candidates_are_never_duplicated(self):
        result = plan("mistake_analysis", requested_model="openai/gpt-oss-120b")
        self.assertEqual(len(result.models), len(set(result.models)))

    def test_the_call_bound_comes_from_the_policy(self):
        self.assertEqual(plan("tutor_chat").max_calls, 3)
        self.assertEqual(plan("tutor_chat", RoutingPolicy(tier_models=GROQ, max_calls=1)).max_calls, 1)


class PremiumTests(unittest.TestCase):
    def test_premium_needs_a_justifying_signal(self):
        routine = plan("answer_evaluation", PREMIUM, signals={"is_final": False, "difficulty": "easy"})
        self.assertNotIn("openai:gpt-x", routine.models)
        final = plan("answer_evaluation", PREMIUM, signals={"is_final": True})
        self.assertEqual(final.models[0], "openai:gpt-x")
        self.assertEqual(final.premium_reason, "final_answer")
        hard = plan("answer_evaluation", PREMIUM, signals={"difficulty": "hard"})
        self.assertEqual(hard.premium_reason, "hard_question")

    def test_premium_grade_tasks_always_qualify(self):
        for task in ("final_exam_evaluation", "answer_diagnosis", "mistake_analysis"):
            result = plan(task, PREMIUM)
            self.assertEqual(result.premium_reason, "task_is_premium_grade", task)
            self.assertEqual(result.candidates[0].provider, "anthropic" if task != "final_exam_evaluation" else "openai")

    def test_the_assistant_uses_premium_for_deep_or_research_only(self):
        self.assertIsNone(plan("assistant_chat", PREMIUM).premium_reason)
        self.assertEqual(plan("assistant_chat", PREMIUM, deep=True).premium_reason, "think_harder")
        self.assertEqual(plan("assistant_chat", PREMIUM, preset="research").premium_reason, "research_preset")
        self.assertIsNone(plan("assistant_chat", PREMIUM, preset="general").premium_reason)

    def test_a_routine_task_never_goes_premium_however_much_budget_there_is(self):
        for task in ("tutor_chat", "flashcard_generation", "lesson_generation", "translation", "content_moderation"):
            result = plan(task, PREMIUM, signals={"is_final": True}, deep=True)
            self.assertTrue(all(c.provider == "groq" for c in result.candidates), task)

    def test_premium_is_skipped_without_a_key_budget_or_rate_headroom(self):
        for broken in (ProviderAvailability(has_key=False, budget_ok=True),
                       ProviderAvailability(has_key=True, budget_ok=False),
                       ProviderAvailability(has_key=True, budget_ok=True, rate_ok=False),
                       ProviderAvailability(has_key=True, budget_ok=True, cooling_down=True)):
            result = plan("mistake_analysis", PREMIUM, {**AVAILABLE, "anthropic": broken})
            self.assertTrue(all(c.provider == "groq" for c in result.candidates), broken)
            self.assertIsNone(result.premium_reason)

    def test_an_unknown_provider_in_availability_is_treated_as_unusable(self):
        result = plan("mistake_analysis", PREMIUM, {"groq": USABLE})
        self.assertEqual(result.candidates[0].provider, "groq")

    def test_at_most_one_premium_candidate_and_groq_fallbacks_follow(self):
        result = plan("final_exam_evaluation", PREMIUM, requested_model="openai/gpt-oss-120b")
        premium = [c for c in result.candidates if c.provider != "groq"]
        self.assertEqual(len(premium), 1)
        self.assertEqual(result.models[1:], ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])

    def test_grading_falls_back_to_the_reasoning_model_when_unset(self):
        policy = RoutingPolicy.from_config({**{f"GROQ_{n}_MODEL": m for n, m in
                                               (("FAST", "f"), ("TUTOR", "t"), ("ANALYSIS", "a"), ("VISION", "v"))},
                                            "AI_PREMIUM_REASONING_MODEL": "anthropic:claude-x"})
        self.assertEqual(policy.premium_models, {"reasoning": "anthropic:claude-x", "grading": "anthropic:claude-x"})

    def test_no_premium_configured_means_no_premium_anywhere(self):
        policy = RoutingPolicy.from_config({"GROQ_FAST_MODEL": "f", "GROQ_TUTOR_MODEL": "t",
                                            "GROQ_ANALYSIS_MODEL": "a", "GROQ_VISION_MODEL": "v"})
        self.assertEqual(policy.premium_models, {})
        self.assertIsNone(plan("mistake_analysis", policy).premium_reason)


class EmergencyFallbackTests(unittest.TestCase):
    def test_an_emergency_model_is_added_last_only_when_its_provider_is_usable(self):
        policy = RoutingPolicy(tier_models=GROQ, fallback_model="openai:gpt-x")
        with_key = plan("tutor_chat", policy)
        self.assertEqual(with_key.candidates[-1].model, "openai:gpt-x")
        self.assertEqual(with_key.candidates[-1].reason, "emergency_fallback")
        without = plan("tutor_chat", policy, {"groq": USABLE, "openai": ProviderAvailability(has_key=True, budget_ok=False)})
        self.assertNotIn("openai:gpt-x", without.models)


class NextCandidateTests(unittest.TestCase):
    PLAN = RoutePlan(tier=Tier.standard, max_calls=3, candidates=(
        Candidate("anthropic:claude-x", "anthropic", "premium"),
        Candidate("openai/gpt-oss-20b", "groq", "requested"),
        Candidate("openai/gpt-oss-120b", "groq", "fallback")))

    def test_a_validation_failure_moves_to_the_next_candidate(self):
        self.assertEqual(routing.next_candidate(self.PLAN, 0, "schema_validation"), 1)
        self.assertEqual(routing.next_candidate(self.PLAN, 1, "invalid_json"), 2)
        self.assertIsNone(routing.next_candidate(self.PLAN, 2, "invalid_json"))

    GROQ_FIRST = RoutePlan(tier=Tier.standard, max_calls=3, candidates=(
        Candidate("openai/gpt-oss-20b", "groq", "requested"),
        Candidate("openai/gpt-oss-120b", "groq", "fallback"),
        Candidate("openai:gpt-x", "openai", "emergency_fallback")))

    def test_a_provider_wide_failure_skips_the_rest_of_that_provider(self):
        self.assertEqual(routing.next_candidate(self.GROQ_FIRST, 0, "provider_quota_exhausted"), 2)
        self.assertEqual(routing.next_candidate(self.GROQ_FIRST, 0, "authentication_failure"), 2)
        self.assertEqual(routing.next_candidate(self.GROQ_FIRST, 0, "schema_validation"), 1)

    def test_a_rate_limit_is_about_one_model_so_the_slower_groq_model_gets_its_turn(self):
        # Providers meter rate limits per model. A 429 on gpt-oss-20b used to be treated as
        # provider-wide, which skipped gpt-oss-120b too and ended in "the provider is busy"
        # - the exact moment a student should have been handed the slower model instead.
        self.assertEqual(routing.next_candidate(self.GROQ_FIRST, 0, "provider_rate_limit"), 1)
        self.assertEqual(routing.next_candidate(self.GROQ_FIRST, 0, "provider_overloaded"), 1)
        same_model_twice = RoutePlan(tier=Tier.standard, max_calls=3, candidates=(
            Candidate("openai/gpt-oss-20b", "groq", "requested"),
            Candidate("openai/gpt-oss-20b", "groq", "again"),
            Candidate("openai/gpt-oss-120b", "groq", "fallback")))
        self.assertEqual(routing.next_candidate(same_model_twice, 0, "provider_rate_limit"), 2,
                         "the limited model itself is skipped")
        self.assertIn("provider_rate_limit", routing.LIMIT_FAILURES)
        self.assertNotIn("provider_rate_limit", routing.PROVIDER_WIDE_FAILURES)

    def test_the_slow_model_is_planned_after_the_tier_fallback_and_before_the_emergency(self):
        policy = RoutingPolicy(tier_models={Tier.fast: "fast", Tier.standard: "tutor", Tier.strong: "strong",
                                            Tier.vision: "vision"},
                               slow_model="slow-groq", fallback_model="openai:gpt-x")
        result = routing.plan_route(RoutingContext("lesson_generation"), policy, {"openai": USABLE})
        self.assertEqual(result.models, ["tutor", "strong", "slow-groq", "openai:gpt-x"])
        self.assertEqual([c.reason for c in result.candidates][2], "slow_model")
        vision = routing.plan_route(RoutingContext("ocr_document_recognition"), policy, {})
        self.assertNotIn("slow-groq", vision.models, "a model that cannot see the page is no fallback")
        self.assertEqual(RoutingPolicy.from_config({"AI_SLOW_MODEL": " x "}).slow_model, "x")

    def test_a_limit_or_budget_stops_everything(self):
        for category in ("request_limit_reached", "budget_exhausted", "token_limit_exceeded"):
            self.assertIsNone(routing.next_candidate(self.PLAN, 0, category), category)

    def test_an_exhausted_account_is_provider_wide(self):
        self.assertIn("provider_quota_exhausted", routing.PROVIDER_WIDE_FAILURES)

    def test_a_refusal_is_not_provider_wide(self):
        self.assertEqual(routing.next_candidate(self.PLAN, 0, "provider_refusal"), 1)


class RoutingReasonTests(unittest.TestCase):
    def test_the_trace_is_compact_and_has_no_content(self):
        result = plan("answer_evaluation", PREMIUM, signals={"is_final": True})
        trace = routing.routing_reason(result, 1, ["openai:provider_rate_limit"])
        self.assertEqual(trace, "tier=standard;premium=final_answer;candidate=2/3;hops=openai:provider_rate_limit")
        self.assertLessEqual(len(trace), 200)

    def test_no_candidate_answered_is_said_plainly(self):
        self.assertTrue(routing.routing_reason(plan("tutor_chat"), None, []).endswith("candidate=none/2"))


if __name__ == "__main__":
    unittest.main()

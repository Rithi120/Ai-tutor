"""No default may name a model the provider has withdrawn.

Groq retired llama-3.1-8b-instant and llama-3.3-70b-versatile at the end of July 2026
and qwen/qwen3.6-27b by October. Six of the nine configured GROQ_*_MODEL settings pointed
at one of them, so moderation, OCR, diagnostics and the fast tier all answered 404 on
every call - and the gateway filed each 404 as a generic internal error, which is why it
went unnoticed for two months while the community queue silently filled.

`RETIRED_GROQ_MODELS` in learnova.config is the list of ids this has happened to. These
tests keep the defaults off it and make sure a deployment that still pins one through
the environment is told so at startup rather than discovering it one 404 at a time.
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from learnova import config as configuration
from learnova.config import RETIRED_GROQ_MODELS, _bare_model, configure_app

MODEL_SETTINGS = ("GROQ_VISION_MODEL", "GROQ_TUTOR_MODEL", "GROQ_ANALYSIS_MODEL",
                  "GROQ_FAST_MODEL", "GROQ_DIAGNOSIS_MODEL", "GROQ_DIAGNOSIS_VERIFY_MODEL",
                  "GROQ_QUESTION_MODEL", "GROQ_MODERATION_MODEL",
                  "GROQ_MODERATION_ESCALATION_MODEL")
# Clearing these makes configure_app fall back to its coded defaults, whatever the
# developer's own .env says.
NO_OVERRIDES = {name: "" for name in MODEL_SETTINGS}


def fresh_app(**environment):
    with patch.dict(os.environ, {"APP_ENV": "development", "AI_MODE": "cached",
                                 **NO_OVERRIDES, **environment}, clear=False):
        for name, value in environment.items():
            if value == "":
                os.environ.pop(name, None)
        for name in MODEL_SETTINGS:
            if name not in environment:
                os.environ.pop(name, None)
        application = Flask("model-config-test", instance_path=tempfile.mkdtemp())
        configure_app(application, "development")
        return application


class DefaultModelTests(unittest.TestCase):
    def test_no_default_names_a_retired_model(self):
        application = fresh_app()
        for name in MODEL_SETTINGS:
            value = application.config[name]
            self.assertNotIn(_bare_model(value), RETIRED_GROQ_MODELS, f"{name}={value}")

    def test_the_retired_list_records_what_actually_went_away(self):
        for retired in ("llama-3.1-8b-instant", "llama-3.3-70b-versatile",
                        "qwen/qwen3.6-27b", "meta-llama/llama-4-scout-17b-16e-instruct"):
            self.assertIn(retired, RETIRED_GROQ_MODELS)

    def test_moderation_runs_on_the_safety_classifier_first(self):
        # A policy-following classifier, measured against this app's prompt: 1.7 s,
        # confidence 0.95, schema-valid. The general strong model is the second opinion.
        application = fresh_app()
        self.assertEqual(application.config["GROQ_MODERATION_MODEL"], "openai/gpt-oss-safeguard-20b")
        self.assertEqual(application.config["GROQ_MODERATION_ESCALATION_MODEL"], "openai/gpt-oss-120b")
        self.assertNotEqual(application.config["GROQ_MODERATION_MODEL"],
                            application.config["GROQ_MODERATION_ESCALATION_MODEL"],
                            "escalation to the same model would be a paid no-op")

    def test_the_vision_model_is_the_one_the_account_is_served(self):
        self.assertEqual(fresh_app().config["GROQ_VISION_MODEL"], "qwen/qwen3.8-27b")

    def test_nothing_is_pinned_at_startup_when_defaults_are_used(self):
        self.assertEqual(fresh_app().config["RETIRED_MODEL_SETTINGS"], {})


class StalePinWarningTests(unittest.TestCase):
    def test_an_environment_that_pins_a_retired_model_is_named_at_startup(self):
        application = fresh_app(GROQ_FAST_MODEL="llama-3.1-8b-instant")
        stale = application.config["RETIRED_MODEL_SETTINGS"]
        self.assertIn("GROQ_FAST_MODEL", stale)
        # Settings that default *from* the fast tier inherit the stale pin and are named too.
        self.assertIn("GROQ_DIAGNOSIS_VERIFY_MODEL", stale)
        self.assertNotIn("GROQ_MODERATION_MODEL", stale,
                         "moderation no longer follows the fast tier, so it must stay clean")

    def test_assistant_pins_are_scanned_too(self):
        # The assistant's models were skipped by the scan while every other setting was
        # checked - and .env.example pinned a retired one for it.
        application = fresh_app(ASSISTANT_DEEP_MODEL="llama-3.3-70b-versatile")
        self.assertIn("ASSISTANT_DEEP_MODEL", application.config["RETIRED_MODEL_SETTINGS"])

    def test_a_provider_prefix_does_not_hide_a_retired_id(self):
        application = fresh_app(GROQ_ANALYSIS_MODEL="groq:llama-3.3-70b-versatile")
        self.assertIn("GROQ_ANALYSIS_MODEL", application.config["RETIRED_MODEL_SETTINGS"])

    def test_the_warning_is_logged_not_raised(self):
        # A stale pin must not stop the app starting: the page that does not use that
        # model still works, and the log says exactly which variable to change.
        with self.assertLogs("model-config-test", level="WARNING") as captured:
            fresh_app(GROQ_FAST_MODEL="llama-3.1-8b-instant")
        joined = "\n".join(captured.output)
        self.assertIn("GROQ_FAST_MODEL=llama-3.1-8b-instant", joined)
        self.assertIn("--task models", joined)

    def test_bare_model_strips_only_a_routing_prefix(self):
        self.assertEqual(_bare_model("groq:openai/gpt-oss-20b"), "openai/gpt-oss-20b")
        self.assertEqual(_bare_model("openai/gpt-oss-20b"), "openai/gpt-oss-20b")
        self.assertEqual(_bare_model(None), "")


class RetiredListShapeTests(unittest.TestCase):
    def test_the_list_is_a_frozenset_of_bare_ids(self):
        self.assertIsInstance(RETIRED_GROQ_MODELS, frozenset)
        for name in RETIRED_GROQ_MODELS:
            self.assertEqual(name, _bare_model(name), "store the provider's id, not a routed one")
            self.assertEqual(name, name.strip())

    def test_the_module_exposes_what_the_smoke_test_imports(self):
        for attribute in ("RETIRED_GROQ_MODELS", "_bare_model"):
            self.assertTrue(hasattr(configuration, attribute), attribute)


class BudgetSettingTests(unittest.TestCase):
    """Budget keys are optional, unset means unset, and bad values fail at startup."""

    BUDGET_KEYS = (
        "AI_BUDGET_USER_TOKENS_PER_DAY", "AI_BUDGET_USER_TOKENS_PER_MONTH",
        "AI_BUDGET_GLOBAL_TOKENS_PER_DAY", "AI_BUDGET_GLOBAL_TOKENS_PER_MONTH",
        "AI_BUDGET_GROQ_TOKENS_PER_DAY", "AI_BUDGET_OPENAI_TOKENS_PER_MONTH",
        "AI_BUDGET_ANTHROPIC_TOKENS_PER_DAY", "AI_BUDGET_GEMINI_TOKENS_PER_MONTH",
        "AI_RATE_GROQ_REQUESTS_PER_MINUTE", "AI_COST_OPENAI_INPUT_PER_MILLION",
        "AI_COST_OPENAI_OUTPUT_PER_MILLION", "AI_BUDGET_GLOBAL_SPEND_PER_MONTH")

    def test_every_budget_key_defaults_to_unset_not_zero(self):
        application = fresh_app()
        for name in self.BUDGET_KEYS:
            self.assertIn(name, application.config, name)
            self.assertIsNone(application.config[name], name)

    def test_budget_keys_parse_as_numbers(self):
        application = fresh_app(AI_BUDGET_GLOBAL_TOKENS_PER_DAY="123",
                                AI_COST_OPENAI_INPUT_PER_MILLION="2.5")
        self.assertEqual(application.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"], 123)
        self.assertEqual(application.config["AI_COST_OPENAI_INPUT_PER_MILLION"], 2.5)

    def test_a_malformed_budget_fails_at_startup(self):
        with self.assertRaisesRegex(RuntimeError, "AI_BUDGET_GLOBAL_TOKENS_PER_DAY"):
            fresh_app(AI_BUDGET_GLOBAL_TOKENS_PER_DAY="plenty")

    def test_the_provider_call_bound_is_clamped_to_a_sane_range(self):
        self.assertEqual(fresh_app().config["AI_MAX_PROVIDER_CALLS_PER_REQUEST"], 3)
        self.assertEqual(fresh_app(AI_MAX_PROVIDER_CALLS_PER_REQUEST="9").config["AI_MAX_PROVIDER_CALLS_PER_REQUEST"], 5)
        self.assertEqual(fresh_app(AI_MAX_PROVIDER_CALLS_PER_REQUEST="0").config["AI_MAX_PROVIDER_CALLS_PER_REQUEST"], 1)

    def test_the_other_providers_keys_default_to_empty(self):
        # The developer's own .env may hold real keys; "" asks fresh_app to unset them so
        # the test reads the default, not whatever this machine happens to have.
        application = fresh_app(ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
        self.assertEqual(application.config["ANTHROPIC_API_KEY"], "")
        self.assertEqual(application.config["GEMINI_API_KEY"], "")
        self.assertTrue(application.config["GEMINI_BASE_URL"].endswith("/openai/"))


class TaskBudgetDefaultTests(unittest.TestCase):
    def test_exam_grading_gets_the_tokens_its_call_site_asks_for(self):
        # It grades every open answer in one call and asked for 5000; the cap was 600.
        self.assertEqual(fresh_app().config["AI_FINAL_EXAM_EVALUATION_MAX_OUTPUT_TOKENS"], 5000)

    def test_translation_gets_the_tokens_its_endpoint_asks_for(self):
        self.assertEqual(fresh_app().config["AI_TRANSLATION_MAX_OUTPUT_TOKENS"], 2500)


if __name__ == "__main__":
    unittest.main()

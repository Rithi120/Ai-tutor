"""Gateway behaviour: caching, provider permission, accounting, and failure mapping.

There is no mock AI mode. Tests that need the gateway to produce a response patch
`service._provider_response`, which is the single place a real network call is made, and
replay the sample responses in `tests/fixtures/ai/` through it.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from flask import Flask

import app as application
from learnova.ai_services import service
from learnova.config import configure_app
from tests.provider_stub import StubResponse, stub_provider


class ProviderUsage:
    input_tokens = 12
    output_tokens = 7
    total_tokens = 19


class ProviderResponse:
    output_text = '{"provider":"response"}'
    model = "provider-test-model"
    usage = ProviderUsage()


class AIGatewayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.original_config = dict(application.app.config)
        application.app.config.update(
            TESTING=False,
            ENV_NAME="development",
            AI_MODE="live",
            AI_CACHE_DIR=str(root / "cache"),
            AI_USAGE_PATH=str(root / "usage.jsonl"),
            ALLOW_LIVE_AI=True,
            RUN_LIVE_AI_TEST=False,
            AI_INPUT_COST_PER_MILLION=1.0,
            AI_OUTPUT_COST_PER_MILLION=2.0,
        )
        self.context = application.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        application.app.config.clear()
        application.app.config.update(self.original_config)
        self.temporary.cleanup()

    def request(self, **overrides):
        values: dict[str, Any] = {
            "task_type": "tutor_chat",
            "language": "English",
            "prompt_version": "test-v1",
            "private_scope": "user-1",
            "model": "fixture-model",
            "input": "sanitized student question",
        }
        values.update(overrides)
        return service.create_response(**values)

    # ------------------------------------------------------------------ sample corpus

    def test_english_and_german_samples_cover_every_task(self):
        """Every supported task has a schema-valid sample in both content languages."""
        for language in ("English", "German"):
            for task_type in sorted(service.SUPPORTED_TASK_TYPES):
                with stub_provider("valid"):
                    response = self.request(task_type=task_type, language=language)
                self.assertTrue(response.output_text, f"{task_type}/{language}")
                self.assertEqual(response.validation, "valid")

    def test_validation_failures_map_to_stable_categories(self):
        with stub_provider("malformed_json"):
            with self.assertRaises(service.AIValidationError) as malformed:
                self.request(task_type="answer_evaluation")
        self.assertEqual(malformed.exception.category, "invalid_json")
        with stub_provider("empty_response"):
            with self.assertRaises(service.AIValidationError):
                self.request()
        with stub_provider("missing_required_fields"):
            with self.assertRaises(service.AIValidationError):
                self.request(task_type="answer_evaluation")

    def test_provider_timeout_and_rate_limit_are_categorized_safely(self):
        """A real provider failure must surface as a safe category, not a raw payload."""
        with stub_provider("timeout"):
            with self.assertRaises(service.AIProviderError) as timeout:
                self.request()
        self.assertEqual(timeout.exception.category, "provider_timeout")
        self.assertNotIn("Simulated", timeout.exception.safe_summary)
        with stub_provider("rate_limit"):
            with self.assertRaises(service.AIProviderError) as limited:
                self.request(input="a different question")
        self.assertEqual(limited.exception.category, "provider_rate_limit")

    def test_broken_samples_are_rejected_in_both_languages(self):
        for language in ("English", "German"):
            for scenario, task_type, context in (
                ("duplicate_questions", "quiz_generation", {}),
                ("invalid_source_references", "project_section_generation", {"source_page_ids": [1]}),
                ("incorrect_exam_question_count", "final_exam_generation", {"question_count": 2}),
            ):
                with stub_provider(scenario):
                    with self.assertRaises(service.AIValidationError, msg=f"{scenario}/{language}"):
                        self.request(language=language, task_type=task_type,
                                     validation_context=context)

    # ------------------------------------------------------------------------ caching

    def test_cached_mode_hits_provider_once_and_partitions_private_users(self):
        application.app.config.update(AI_MODE="cached", ALLOW_LIVE_AI=True)
        with patch.object(service, "_provider_response", return_value=ProviderResponse()) as provider:
            first = self.request(private_scope="user-1")
            second = self.request(private_scope="user-1")
            third = self.request(private_scope="user-2")
        self.assertEqual(first.output_text, second.output_text)
        self.assertEqual(first.output_text, third.output_text)
        self.assertEqual(provider.call_count, 2)
        records = [json.loads(line) for line in Path(
            application.app.config["AI_USAGE_PATH"]
        ).read_text(encoding="utf-8").splitlines()]
        self.assertFalse(records[0]["cache_hit"])
        self.assertTrue(records[1]["cache_hit"])
        self.assertFalse(records[2]["cache_hit"])

    def test_live_and_cached_miss_require_explicit_development_permission(self):
        for mode in ("live", "cached"):
            application.app.config.update(AI_MODE=mode, ALLOW_LIVE_AI=False)
            with patch.object(service, "_provider_response") as provider:
                with self.assertRaises(service.AIConfigurationError):
                    self.request(input=f"unique-{mode}")
                provider.assert_not_called()

    def test_unsupported_mode_is_rejected(self):
        """The retired mock mode must not be reachable through configuration either."""
        application.app.config["AI_MODE"] = "mock"
        with patch.object(service, "_provider_response") as provider:
            with self.assertRaises(service.AIConfigurationError):
                self.request()
            provider.assert_not_called()

    # ------------------------------------------------------------------- accounting

    def test_accounting_is_sanitized_and_hashes_are_normalized(self):
        secret_text = "PRIVATE-UPLOAD-CONTENT"
        api_key = "secret-api-key"
        application.app.config["GROQ_API_KEY"] = api_key
        with patch.object(service, "_provider_response",
                          return_value=StubResponse("a plain chat reply")):
            self.request(input=secret_text)
        usage_text = Path(application.app.config["AI_USAGE_PATH"]).read_text(encoding="utf-8")
        self.assertNotIn(secret_text, usage_text)
        self.assertNotIn(api_key, usage_text)
        record = json.loads(usage_text.splitlines()[0])
        self.assertEqual(record["task_type"], "tutor_chat")
        self.assertIn("duration_ms", record)
        self.assertIn("estimated_or_reported_cost", record)
        self.assertIn("request_id", record)
        self.assertEqual(record["validation_result"], "valid")
        common = dict(
            task_type="tutor_chat", model="m", language="en", prompt_version="v1",
            instructions="rules", private_scope="one",
        )
        first = service.request_hash(provider_input="hello\r\n", **common)
        second = service.request_hash(provider_input="hello", **common)
        other_user = service.request_hash(provider_input="hello", **{**common, "private_scope": "two"})
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_user)

    def test_provider_boundary_is_centralized(self):
        root = Path(application.app.root_path)
        offenders = []
        for path in [root / "app.py", *sorted((root / "learnova").rglob("*.py"))]:
            if path == root / "learnova" / "ai_services" / "service.py":
                continue
            text = path.read_text(encoding="utf-8")
            if "from openai import" in text or ".responses.create(" in text:
                offenders.append(str(path.relative_to(root)))
        self.assertEqual(offenders, [])

    def test_development_badge_reflects_mode_and_is_hidden_in_production(self):
        client = application.app.test_client()
        for mode, label in (("cached", b"Cached AI"), ("live", b"Live AI")):
            application.app.config.update(ENV_NAME="development", AI_MODE=mode)
            self.assertIn(label, client.get("/login").data)
        application.app.config.update(ENV_NAME="production", AI_MODE="live")
        self.assertNotIn(b"Development AI mode", client.get("/login").data)
        self.assertNotIn(b"Mock AI", client.get("/login").data)


class AIConfigurationTests(unittest.TestCase):
    def test_non_production_defaults_to_cached(self):
        with patch.dict(os.environ, {"APP_ENV": "testing", "AI_MODE": ""}, clear=False):
            test_app = Flask("test-ai-config", instance_path=tempfile.mkdtemp())
            configure_app(test_app, "testing")
            self.assertEqual(test_app.config["AI_MODE"], "cached")

    def test_production_requires_explicit_live(self):
        with patch.dict(os.environ, {"APP_ENV": "production", "AI_MODE": "cached",
                                     "SECRET_KEY": "x"}, clear=False):
            production_app = Flask("production-ai-config", instance_path=tempfile.mkdtemp())
            with self.assertRaisesRegex(RuntimeError, "AI_MODE=live"):
                configure_app(production_app, "production")

    def test_mock_mode_is_no_longer_configurable(self):
        with patch.dict(os.environ, {"APP_ENV": "development", "AI_MODE": "mock"}, clear=False):
            stale_app = Flask("stale-ai-config", instance_path=tempfile.mkdtemp())
            with self.assertRaisesRegex(RuntimeError, "AI_MODE must be cached or live"):
                configure_app(stale_app, "development")

    def test_retired_mock_settings_are_gone(self):
        with patch.dict(os.environ, {"APP_ENV": "development", "AI_MODE": "cached"}, clear=False):
            fresh = Flask("fresh-ai-config", instance_path=tempfile.mkdtemp())
            configure_app(fresh, "development")
        for retired in ("AI_MOCK_SCENARIO", "AI_MOCK_LATENCY_MS", "AI_FIXTURE_DIR"):
            self.assertNotIn(retired, fresh.config)
        self.assertFalse(hasattr(service, "_mock_response"))
        self.assertFalse(hasattr(service, "AIMockTimeout"))
        self.assertEqual(service.AI_MODES, {"cached", "live"})


if __name__ == "__main__":
    unittest.main()

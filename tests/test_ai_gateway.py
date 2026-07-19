import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask

import app as application
from learnova.ai_services import service
from learnova.config import configure_app


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
            AI_MODE="mock",
            AI_MOCK_SCENARIO="valid",
            AI_MOCK_LATENCY_MS=0,
            AI_CACHE_DIR=str(root / "cache"),
            AI_USAGE_PATH=str(root / "usage.jsonl"),
            AI_FIXTURE_DIR=str(Path(application.app.root_path) / "tests" / "fixtures" / "ai"),
            ALLOW_LIVE_AI=False,
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
        values = {
            "task_type": "tutor_chat",
            "language": "English",
            "prompt_version": "test-v1",
            "private_scope": "user-1",
            "model": "fixture-model",
            "input": "sanitized student question",
        }
        values.update(overrides)
        return service.create_response(**values)

    def test_mock_is_deterministic_and_never_calls_provider(self):
        with patch.object(service, "_provider_response") as provider:
            first = self.request()
            second = self.request()
        self.assertEqual(first.output_text, second.output_text)
        self.assertIn("multiplier", first.output_text)
        provider.assert_not_called()

    def test_english_and_german_valid_fixtures_cover_every_task(self):
        for language in ("English", "German"):
            for task_type in sorted(service.SUPPORTED_TASK_TYPES):
                response = self.request(task_type=task_type, language=language)
                self.assertTrue(response.output_text)

    def test_valid_fixtures_adapt_to_structural_request_context(self):
        lesson = service.parse_json(self.request(
            task_type="adaptive_practice",
            fixture_context={
                "lesson": True, "subject": "Physics", "concept": "Acceleration",
            },
        ).output_text)
        self.assertEqual(lesson["concepts"][0]["name"], "Acceleration")
        self.assertIn("question", lesson)

        translated = service.parse_json(self.request(
            task_type="translation",
            fixture_context={"texts": ["one", "two", "three"]},
        ).output_text)
        self.assertEqual(translated["translations"], ["one", "two", "three"])

        exam = service.parse_json(self.request(
            task_type="final_exam_generation",
            fixture_context={
                "question_count": 5,
                "section_allocation": {"11": 3, "12": 2},
                "difficulty_distribution": {"easy": 2, "medium": 2, "hard": 1},
                "question_types": ["short_answer", "true_false"],
                "source_page_ids": {"11": [101], "12": [102]},
                "supporting_text": {"11": "source eleven", "12": "source twelve"},
            },
        ).output_text)["questions"]
        self.assertEqual(len(exam), 5)
        self.assertEqual([item["section_id"] for item in exam].count(11), 3)
        self.assertEqual([item["difficulty"] for item in exam].count("hard"), 1)
        self.assertTrue(all(item["source_page_ids"] for item in exam))

    def test_failure_fixtures_and_optional_latency(self):
        application.app.config["AI_MOCK_SCENARIO"] = "malformed_json"
        with self.assertRaises(service.AIValidationError) as malformed:
            self.request(task_type="answer_evaluation")
        self.assertEqual(malformed.exception.category, "invalid_json")
        application.app.config["AI_MOCK_SCENARIO"] = "empty_response"
        with self.assertRaises(service.AIValidationError):
            self.request()
        application.app.config["AI_MOCK_SCENARIO"] = "missing_required_fields"
        with self.assertRaises(service.AIValidationError):
            self.request(task_type="answer_evaluation")
        application.app.config["AI_MOCK_SCENARIO"] = "timeout"
        with self.assertRaises(service.AIMockTimeout):
            self.request()
        application.app.config["AI_MOCK_SCENARIO"] = "rate_limit"
        with self.assertRaises(service.AIMockRateLimit):
            self.request()
        application.app.config.update(AI_MOCK_SCENARIO="valid", AI_MOCK_LATENCY_MS=15)
        started = time.perf_counter()
        self.request()
        self.assertGreaterEqual(time.perf_counter() - started, 0.01)

    def test_special_failure_fixtures_are_available_in_both_languages(self):
        for language in ("English", "German"):
            for scenario, task_type, context in (
                ("duplicate_questions", "quiz_generation", {}),
                ("invalid_source_references", "project_section_generation", {"source_page_ids": [1]}),
                ("incorrect_exam_question_count", "final_exam_generation", {"question_count": 2}),
            ):
                application.app.config["AI_MOCK_SCENARIO"] = scenario
                with self.assertRaises(service.AIValidationError):
                    self.request(language=language, task_type=task_type, validation_context=context)

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

    def test_accounting_is_sanitized_and_hashes_are_normalized(self):
        secret_text = "PRIVATE-UPLOAD-CONTENT"
        api_key = "secret-api-key"
        application.app.config["GROQ_API_KEY"] = api_key
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
        for mode, label in (("mock", b"Mock AI"), ("cached", b"Cached AI"), ("live", b"Live AI")):
            application.app.config.update(ENV_NAME="development", AI_MODE=mode)
            self.assertIn(label, client.get("/login").data)
        application.app.config.update(ENV_NAME="production", AI_MODE="live")
        self.assertNotIn(b"Development AI mode", client.get("/login").data)


class AIConfigurationTests(unittest.TestCase):
    def test_testing_defaults_to_mock_and_production_requires_explicit_live(self):
        with patch.dict(os.environ, {"APP_ENV": "testing", "AI_MODE": ""}, clear=False):
            test_app = Flask("test-ai-config", instance_path=tempfile.mkdtemp())
            configure_app(test_app, "testing")
            self.assertEqual(test_app.config["AI_MODE"], "mock")
        with patch.dict(os.environ, {"APP_ENV": "production", "AI_MODE": "mock", "SECRET_KEY": "x"}, clear=False):
            production_app = Flask("production-ai-config", instance_path=tempfile.mkdtemp())
            with self.assertRaisesRegex(RuntimeError, "AI_MODE=live"):
                configure_app(production_app, "production")

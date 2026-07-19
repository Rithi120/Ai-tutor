import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as application
from learnova.ai_services import service
from learnova.ai_services.contracts import AIValidationError, validate_output
from learnova.ai_services.prompts import PROMPT_VERSIONS, STRUCTURED_TASKS, output_contract


class FakeUsage:
    input_tokens = 10
    output_tokens = 8
    total_tokens = 18


class FakeResponse:
    model = "fake-model"
    usage = FakeUsage()

    def __init__(self, output_text):
        self.output_text = output_text


VALID_LESSON = {
    "lesson_title": "Small lesson", "concepts": [{"name": "Addition"}],
    "explanation": "Add the values one step at a time.",
    "worked_example": {"problem": "1 + 1", "steps": ["Add one and one"], "answer": "2"},
    "question": {"id": "q1", "concept": "Addition", "difficulty": 1, "type": "text",
                 "prompt": "What is 1 + 1?", "hint": "Count once.", "options": [],
                 "expected_answer": "2"},
}


class AIObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=False, ENV_NAME="development", AI_MODE="mock", AI_MOCK_SCENARIO="valid",
            AI_MOCK_LATENCY_MS=0, AI_CACHE_DIR=str(root / "cache"),
            AI_USAGE_PATH=str(root / "usage.jsonl"),
            AI_FIXTURE_DIR=str(Path(application.app.root_path) / "tests" / "fixtures" / "ai"),
            ALLOW_LIVE_AI=True, RUN_LIVE_AI_TEST=False, AI_ENFORCE_LIMITS=False,
            AI_MAX_OUTPUT_CHARACTERS=200000,
        )
        self.context = application.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temp.cleanup()

    def call(self, task="lesson_generation", **values):
        defaults = dict(task_type=task, language="English", private_scope="student-1",
                        session_scope="session-1", model="fake-model", input="small input")
        defaults.update(values)
        return service.create_response(**defaults)

    def test_valid_fixtures_use_current_versions_and_production_schemas(self):
        fixture_root = Path(application.app.config["AI_FIXTURE_DIR"])
        for language in ("en", "de"):
            fixture = json.loads((fixture_root / language / "valid.json").read_text(encoding="utf-8"))
            self.assertEqual(fixture["_meta"]["prompt_versions"], PROMPT_VERSIONS)
            for task, version in PROMPT_VERSIONS.items():
                output = fixture[task]["output_text"]
                text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
                validate_output(task, text, version, {})

    def test_invalid_fixture_categories(self):
        cases = [
            ("malformed_json", "answer_evaluation", {}, "invalid_json"),
            ("missing_required_fields", "answer_evaluation", {}, "schema_validation"),
            ("wrong_types", "answer_evaluation", {}, "schema_validation"),
            ("duplicate_question_ids", "quiz_generation", {}, "schema_validation"),
            ("invalid_source_references", "project_section_generation", {"source_page_ids": [1]}, "source_reference_validation"),
            ("incorrect_exam_question_count", "final_exam_generation", {"question_count": 2}, "schema_validation"),
            ("invalid_scores", "answer_evaluation", {}, "schema_validation"),
            ("unsupported_difficulty", "adaptive_practice", {}, "schema_validation"),
        ]
        for scenario, task, context, category in cases:
            application.app.config["AI_MOCK_SCENARIO"] = scenario
            with self.assertRaises(AIValidationError) as caught:
                self.call(task, validation_context=context)
            self.assertEqual(caught.exception.category, category)
            self.assertEqual(caught.exception.report.prompt_version, PROMPT_VERSIONS[task])
            self.assertFalse(caught.exception.report.valid)
        application.app.config.update(AI_MOCK_SCENARIO="oversized_output", AI_MAX_OUTPUT_CHARACTERS=20)
        with self.assertRaises(AIValidationError) as caught:
            self.call("tutor_chat")
        self.assertEqual(caught.exception.category, "token_limit_exceeded")

    def test_one_corrective_retry_then_success_is_measured(self):
        application.app.config["AI_MODE"] = "live"
        malformed = FakeResponse('{"lesson_title":"incomplete"}')
        valid = FakeResponse(json.dumps(VALID_LESSON))
        with patch.object(service, "_provider_response", side_effect=[malformed, valid]) as provider:
            response = self.call()
        self.assertEqual(provider.call_count, 2)
        self.assertEqual(response.validation, "valid")
        records = service._read_usage_records()
        self.assertEqual(records[-1]["retry_count"], 1)
        self.assertEqual(records[-1]["total_tokens"], 36)
        corrective = provider.call_args_list[1].kwargs["instructions"]
        self.assertIn("previous response failed validation", corrective)
        self.assertNotIn(malformed.output_text, corrective)

    def test_second_invalid_response_stops_and_is_not_cached(self):
        application.app.config["AI_MODE"] = "cached"
        invalid = FakeResponse('{"lesson_title":"still incomplete"}')
        with patch.object(service, "_provider_response", side_effect=[invalid, invalid]) as provider:
            with self.assertRaises(AIValidationError):
                self.call()
        self.assertEqual(provider.call_count, 2)
        self.assertFalse(list(Path(application.app.config["AI_CACHE_DIR"]).rglob("*.json")))
        record = service._read_usage_records()[-1]
        self.assertEqual(record["validation_result"], "invalid")
        self.assertEqual(record["error_category"], "schema_validation")

    def test_token_and_request_limits_block_before_provider(self):
        application.app.config.update(AI_MODE="live", AI_ENFORCE_LIMITS=True,
                                      AI_LESSON_GENERATION_MAX_INPUT_TOKENS=1)
        with patch.object(service, "_provider_response") as provider:
            with self.assertRaises(service.AITokenLimitError):
                self.call()
            provider.assert_not_called()
        Path(application.app.config["AI_USAGE_PATH"]).unlink(missing_ok=True)
        application.app.config.update(AI_MODE="mock", AI_LESSON_GENERATION_MAX_INPUT_TOKENS=9000,
                                      AI_MAX_REQUESTS_PER_USER_HOUR=1)
        self.call()
        with self.assertRaises(service.AIRequestLimitError):
            self.call()

    def test_metadata_is_complete_and_sanitized(self):
        secret = "PRIVATE STUDENT MATERIAL"
        self.call(input=secret)
        raw = Path(application.app.config["AI_USAGE_PATH"]).read_text(encoding="utf-8")
        self.assertNotIn(secret, raw)
        record = json.loads(raw.splitlines()[-1])
        required = {"request_id", "timestamp", "user_reference", "task_type", "selected_model",
                    "language", "prompt_version", "ai_mode", "input_tokens", "output_tokens",
                    "total_tokens", "duration_ms", "cache_hit", "retry_count",
                    "validation_result", "success", "error_category"}
        self.assertTrue(required <= record.keys())
        self.assertNotEqual(record["user_reference"], "student-1")


class PromptContractTests(unittest.TestCase):
    def test_every_prompt_has_language_version_and_structured_rules(self):
        for task, version in PROMPT_VERSIONS.items():
            contract = output_contract(task, "German", {"question_count": 5})
            self.assertIn(version, contract)
            self.assertIn("OUTPUT_LANGUAGE: German", contract)
            if task in STRUCTURED_TASKS:
                self.assertIn("exactly one JSON value", contract)
                self.assertIn("REQUIRED_JSON_STRUCTURE:", contract)
                self.assertIn("Do not add Markdown", contract)
        exam = output_contract("final_exam_generation", "English", {"question_count": 5})
        self.assertIn("Return exactly 5 questions", exam)
        self.assertIn("Do not invent", exam)
        self.assertIn("Allowed difficulty values", exam)


class AIDiagnosticsAccessTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        self.temp = tempfile.TemporaryDirectory()
        application.app.config.update(
            TESTING=True, ENV_NAME="development", AI_DIAGNOSTICS_ADMINS={"admin"},
            AI_USAGE_PATH=str(Path(self.temp.name) / "usage.jsonl"),
        )
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temp.cleanup()

    def register(self, username, email):
        return self.client.post("/register", data={
            "username": username, "email": email, "password": "correct-horse-battery",
        })

    def test_diagnostics_is_hidden_from_students_and_available_to_allowlisted_developer(self):
        self.register("student", "student@example.com")
        self.assertEqual(self.client.get("/internal/ai-diagnostics").status_code, 404)
        self.client.post("/logout")
        self.register("admin", "admin@example.com")
        response = self.client.get("/internal/ai-diagnostics")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"sanitized metadata only", response.data)
        self.assertNotIn(b"prompt content", response.data)
        application.app.config["ENV_NAME"] = "production"
        self.assertEqual(self.client.get("/internal/ai-diagnostics").status_code, 404)


if __name__ == "__main__":
    unittest.main()

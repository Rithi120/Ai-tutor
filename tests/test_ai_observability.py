import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import app as application
from learnova.ai_services import service
from learnova.ai_services.contracts import AIValidationError, validate_output
from learnova.ai_services.prompts import PROMPT_VERSIONS, STRUCTURED_TASKS, output_contract
from tests.provider_stub import FIXTURE_ROOT, stub_provider


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
            TESTING=False, ENV_NAME="development", AI_MODE="live",
            AI_CACHE_DIR=str(root / "cache"),
            AI_USAGE_PATH=str(root / "usage.jsonl"),
            ALLOW_LIVE_AI=True, RUN_LIVE_AI_TEST=False, AI_ENFORCE_LIMITS=False,
            AI_MAX_OUTPUT_CHARACTERS=200000,
        )
        self.context = application.app.app_context()
        self.context.push()
        # Budgets are checked against the database ledger, so every test starts from an
        # empty ledger rather than whatever the previous test module left behind.
        application.db.drop_all()
        application.db.create_all()
        service._RESERVATIONS.clear()
        service._PROVIDER_COOLDOWNS.clear()
        service._MODEL_COOLDOWNS.clear()
        service._RECENT_RESULTS.clear()

    def tearDown(self):
        self.context.pop()
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temp.cleanup()

    def reset_ledger(self):
        service.usage_ledger().flush()
        application.db.session.execute(application.db.delete(application.AIUsageEvent))
        application.db.session.commit()
        Path(application.app.config["AI_USAGE_PATH"]).unlink(missing_ok=True)
        # A fresh start also forgets recent answers, or the next identical stub request
        # would be served from the deduplication window instead of reaching the stub.
        service._RECENT_RESULTS.clear()

    def call(self, task="lesson_generation", **values):
        defaults: dict[str, Any] = dict(task_type=task, language="English", private_scope="student-1",
                                        session_scope="session-1", model="fake-model", input="small input")
        defaults.update(values)
        return service.create_response(**defaults)

    def test_valid_samples_use_current_versions_and_production_schemas(self):
        """Every shipped sample response still satisfies the live validator it documents."""
        for language in ("en", "de"):
            fixture = json.loads((FIXTURE_ROOT / language / "valid.json").read_text(encoding="utf-8"))
            self.assertEqual(fixture["_meta"]["prompt_versions"], PROMPT_VERSIONS)
            for task, version in PROMPT_VERSIONS.items():
                output = fixture[task]["output_text"]
                text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
                validate_output(task, text, version, {})

    def test_invalid_samples_map_to_stable_categories(self):
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
            with stub_provider(scenario):
                with self.assertRaises(AIValidationError) as caught:
                    self.call(task, validation_context=context)
            self.assertEqual(caught.exception.category, category, scenario)
            self.assertEqual(caught.exception.report.prompt_version, PROMPT_VERSIONS[task])
            self.assertFalse(caught.exception.report.valid)
        application.app.config["AI_MAX_OUTPUT_CHARACTERS"] = 20
        with stub_provider("oversized_output"):
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
        self.reset_ledger()
        application.app.config.update(AI_LESSON_GENERATION_MAX_INPUT_TOKENS=9000,
                                      AI_MAX_REQUESTS_PER_USER_HOUR=1)
        with stub_provider("valid") as provider:
            self.call()
            with self.assertRaises(service.AIRequestLimitError) as caught:
                self.call()
            self.assertEqual(provider.call_count, 1)
        self.assertEqual(caught.exception.scope, "user_hour")
        self.assertFalse(caught.exception.site_wide)

    # ---- token budgets: never silently exceeded ----------------------------------------
    def test_cache_hits_do_not_consume_token_budget(self):
        application.app.config.update(AI_MODE="cached")
        with stub_provider("valid") as provider:
            self.call()                                     # a miss: one call, 18 tokens billed
            used = service.usage_ledger().totals().tokens
            self.assertEqual(used, 18)
            application.app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = used   # nothing left
            self.call()                                     # a hit: must still be served
            self.assertEqual(provider.call_count, 1)
            with self.assertRaises(service.AIRequestLimitError) as caught:
                self.call(input="a different question")    # a miss: refused, not billed
        self.assertEqual(caught.exception.scope, "global_tokens_day")
        self.assertTrue(caught.exception.site_wide)
        self.assertIsNotNone(caught.exception.resets_at)
        self.assertGreater(caught.exception.retry_after_seconds or 0, 0)
        self.assertEqual(service.usage_ledger().totals().tokens, used)

    def test_refused_requests_are_recorded_with_zero_tokens(self):
        application.app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = 1
        with stub_provider("valid") as provider:
            with self.assertRaises(service.AIRequestLimitError):
                self.call()
        provider.assert_not_called()
        record = service._read_usage_records()[-1]
        self.assertEqual(record["event_kind"], "refused")
        self.assertEqual(record["total_tokens"], 0)
        self.assertEqual(record["estimated_or_reported_cost"], 0.0)
        self.assertEqual(record["error_category"], "budget_exhausted")
        self.assertEqual(service.usage_ledger().totals().tokens, 0)

    def test_a_reservation_is_released_when_the_provider_fails(self):
        with patch.object(service, "_provider_response", side_effect=TimeoutError("slow")):
            with self.assertRaises(service.AIProviderError):
                self.call()
        open_now = service._RESERVATIONS.open_tokens(
            provider=None, user_reference=None, session_reference=None, since=None)
        self.assertEqual(open_now.tokens, 0)
        self.assertEqual(service.usage_ledger().totals().tokens, 0)

    def test_concurrent_requests_cannot_overshoot_a_global_budget(self):
        # Learn what one call reserves, then allow one and a half of them.
        anticipated = []
        real_evaluate = service.budgets.evaluate

        def spy(policy, totals_for, **kwargs):
            anticipated.append(kwargs["anticipated_tokens"])
            return real_evaluate(policy, totals_for, **kwargs)

        with patch.object(service.budgets, "evaluate", side_effect=spy), stub_provider("valid"):
            self.call()
        self.reset_ledger()
        application.app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = int(anticipated[0] * 1.5)

        entered, release = threading.Event(), threading.Event()

        def slow_provider(**_kwargs):
            entered.set()
            release.wait(timeout=5)
            return FakeResponse(json.dumps(VALID_LESSON))

        outcomes: dict[str, str] = {}

        def worker(name):
            # Two *different* requests: identical ones would be deduplicated into one
            # call, which is the other mechanism, tested separately below.
            with application.app.app_context():
                try:
                    self.call(input=f"small input from {name}")
                    outcomes[name] = "ok"
                except service.AIRequestLimitError as error:
                    outcomes[name] = error.scope

        with patch.object(service, "_provider_response", side_effect=slow_provider) as provider:
            first = threading.Thread(target=worker, args=("first",))
            first.start()
            self.assertTrue(entered.wait(timeout=5), "the first call never reached the provider")
            second = threading.Thread(target=worker, args=("second",))
            second.start()
            second.join(timeout=5)
            release.set()
            first.join(timeout=5)
        self.assertEqual(outcomes, {"first": "ok", "second": "global_tokens_day"})
        self.assertEqual(provider.call_count, 1)

    def test_abandoned_reservations_expire_on_the_next_check(self):
        stale = service.UsageEntry(
            request_id="dead", task_type="lesson_generation", provider="groq", model="m",
            user_reference="u", session_reference="s", reserved_tokens=5000,
            timestamp=datetime.now(timezone.utc) - timedelta(hours=1))
        service._RESERVATIONS.add(stale)
        application.app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = 6000
        with stub_provider("valid") as provider:
            self.call()      # would be refused if the dead reservation still counted
        self.assertEqual(provider.call_count, 1)

    def test_the_session_token_default_is_one_number_everywhere(self):
        self.assertEqual(application.app.config["AI_MAX_TOKENS_PER_SESSION"], 60000)
        source = Path(service.__file__).read_text(encoding="utf-8")
        self.assertIn('"AI_MAX_TOKENS_PER_SESSION", 60000', source)
        self.assertIn('"AI_MAX_LIVE_REQUESTS_DEVELOPMENT", 1000', source)

    # ---- what the student is told ---------------------------------------------------------
    def test_budget_exhaustion_is_a_503_with_a_reset_time(self):
        resets = datetime.now(timezone.utc) + timedelta(hours=2)
        error = service.AIRequestLimitError(
            "x", scope="global_tokens_day", resets_at=resets, retry_after_seconds=7200)
        with application.app.test_request_context():
            response, status = application.ai_failure_response(error)
            payload = response.get_json()
        self.assertEqual(status, 503)
        self.assertEqual(payload["code"], "ai_budget_exhausted")
        self.assertEqual(payload["details"]["retry_after"], 7200)
        self.assertEqual(response.headers["Retry-After"], "7200")
        self.assertIn("UTC", payload["error"])
        self.assertIn("saved work", payload["error"])

    def test_a_users_own_limit_stays_a_429(self):
        error = service.AIRequestLimitError("x", scope="user_hour", retry_after_seconds=3600)
        with application.app.test_request_context():
            response, status = application.ai_failure_response(error)
            payload = response.get_json()
        self.assertEqual(status, 429)
        self.assertEqual(payload["code"], "ai_limit_reached")
        self.assertEqual(response.headers["Retry-After"], "3600")

    def test_a_busy_provider_has_its_own_message(self):
        error = service.AIProviderError("provider_rate_limit", "429")
        with application.app.test_request_context():
            message, status, code = application.ai_failure_message(error)
        self.assertEqual((status, code), (503, "ai_provider_busy"))
        self.assertIn("Max limit reached", message)

    def test_a_groq_rate_limit_hands_the_student_the_slower_groq_model_and_says_so(self):
        from tests.provider_stub import StubRateLimit
        valid = FakeResponse(json.dumps(VALID_LESSON))
        service._MODEL_COOLDOWNS.clear()
        with patch.object(service, "_provider_response", side_effect=[StubRateLimit("429"), valid]) as provider:
            with application.app.test_request_context():
                response = self.call()
                degraded = dict(application.g.get("ai_degraded") or {})
        models = [call.kwargs["model"] for call in provider.call_args_list]
        # The plan behind a non-tier request is [requested, tier model, tier fallback]; the
        # point is that the next *Groq* model answers rather than the request failing.
        self.assertEqual(models, ["fake-model", application.app.config["GROQ_TUTOR_MODEL"]],
                         "another model at the same provider, not an error")
        self.assertEqual(response.degraded, "provider_rate_limit")
        self.assertEqual(degraded.get("reason"), "provider_rate_limit")
        self.assertEqual(degraded.get("requested"), "fake-model")
        record = service._read_usage_records()[-1]
        self.assertIn("hops=groq:provider_rate_limit", record["routing_reason"])
        # Moments later the limited model is still cooling down: it is skipped without a
        # call, the slower model answers again, and the student is told again.
        service._RECENT_RESULTS.clear()
        with patch.object(service, "_provider_response", return_value=valid) as provider:
            with application.app.test_request_context():
                again = self.call()
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(provider.call_args.kwargs["model"], application.app.config["GROQ_TUTOR_MODEL"])
        self.assertEqual(again.degraded, "model_cooldown")
        self.assertIn("hops=groq:model_cooldown", service._read_usage_records()[-1]["routing_reason"])
        service._MODEL_COOLDOWNS.clear()

    def test_a_clean_answer_carries_no_degraded_flag(self):
        service._MODEL_COOLDOWNS.clear()
        with patch.object(service, "_provider_response", return_value=FakeResponse(json.dumps(VALID_LESSON))):
            with application.app.test_request_context():
                response = self.call()
                self.assertIsNone(application.g.get("ai_degraded"))
        self.assertEqual(response.degraded, "")

    # ---- routing: Groq by default, premium when justified, fallback to Groq ------------
    def premium(self, **overrides):
        settings = {"AI_PREMIUM_REASONING_MODEL": "anthropic:claude-x", "ANTHROPIC_API_KEY": "k",
                    "AI_BUDGET_ANTHROPIC_TOKENS_PER_DAY": 1_000_000}
        settings.update(overrides)
        application.app.config.update(settings)

    def test_a_validation_failure_moves_to_the_next_candidate_not_the_same_model(self):
        malformed = FakeResponse('{"lesson_title":"incomplete"}')
        valid = FakeResponse(json.dumps(VALID_LESSON))
        with patch.object(service, "_provider_response", side_effect=[malformed, valid]) as provider:
            self.call()
        first, second = [call.kwargs["model"] for call in provider.call_args_list]
        self.assertEqual(first, "fake-model")
        self.assertEqual(second, application.app.config["GROQ_TUTOR_MODEL"], "the tier's model, with corrective instructions")
        self.assertIn("previous response failed validation", provider.call_args_list[1].kwargs["instructions"])
        record = service._read_usage_records()[-1]
        self.assertEqual(record["retry_count"], 1)
        self.assertIn("candidate=2/", record["routing_reason"])
        self.assertIn("hops=groq:schema_validation", record["routing_reason"])

    def test_provider_calls_per_request_never_exceed_the_bound(self):
        class BadRequestError(RuntimeError):
            status_code = 400

        with patch.object(service, "_provider_response", side_effect=BadRequestError("no")) as provider:
            with self.assertRaises(service.AIProviderError):
                self.call()
        self.assertLessEqual(provider.call_count, 3)
        self.assertEqual(provider.call_count, 3, "three Groq candidates, all tried, then stop")
        application.app.config["AI_MAX_PROVIDER_CALLS_PER_REQUEST"] = 2
        with patch.object(service, "_provider_response", side_effect=BadRequestError("no")) as provider:
            with self.assertRaises(service.AIProviderError):
                self.call()
        self.assertEqual(provider.call_count, 2)

    def test_a_premium_failure_falls_back_to_groq_and_is_recorded(self):
        self.premium()
        from tests.provider_stub import StubRateLimit, fixture_text
        valid = FakeResponse(fixture_text("mistake_analysis"))
        with patch.object(service, "_provider_response", side_effect=[StubRateLimit("429"), valid]) as provider:
            self.call("mistake_analysis", model="openai/gpt-oss-120b")
        models = [call.kwargs["model"] for call in provider.call_args_list]
        self.assertEqual(models, ["anthropic:claude-x", "openai/gpt-oss-120b"])
        record = service._read_usage_records()[-1]
        self.assertEqual(record["provider"], "groq")
        self.assertIn("premium=task_is_premium_grade", record["routing_reason"])
        self.assertIn("hops=anthropic:provider_rate_limit", record["routing_reason"])
        # The 429 put anthropic in a cooldown, so the next plan skips it without a call.
        # (The dedup window is cleared so this identical request reaches the stub at all.)
        service._RECENT_RESULTS.clear()
        with patch.object(service, "_provider_response", return_value=valid) as provider:
            self.call("mistake_analysis", model="openai/gpt-oss-120b")
        self.assertEqual(provider.call_args.kwargs["model"], "openai/gpt-oss-120b")

    def test_a_premium_candidate_is_sent_the_shape_its_provider_accepts(self):
        self.premium()
        from tests.provider_stub import fixture_text
        valid = FakeResponse(fixture_text("mistake_analysis"))
        with patch.object(service, "_provider_response", return_value=valid) as provider:
            self.call("mistake_analysis", model="openai/gpt-oss-120b", temperature=0)
        sent = provider.call_args.kwargs
        self.assertEqual(sent["model"], "anthropic:claude-x")
        self.assertNotIn("temperature", sent, "Anthropic rejects it")
        self.assertNotIn("reasoning", sent)

    def test_routine_tasks_never_leave_groq_even_with_premium_configured(self):
        self.premium()
        with stub_provider("valid") as provider:
            self.call("tutor_chat", model="openai/gpt-oss-20b")
            self.call("flashcard_generation", model="openai/gpt-oss-20b")
        for call in provider.call_args_list:
            self.assertNotIn("anthropic", call.kwargs["model"])

    def test_a_paid_model_named_directly_is_refused_until_it_has_a_cap(self):
        # The router skips an unbudgeted paid provider; an owner naming one in a setting
        # must get the same answer, not a quiet bill.
        application.app.config.update(OPENAI_API_KEY="k", AI_BUDGET_OPENAI_TOKENS_PER_DAY=None)
        with stub_provider("valid") as provider:
            with self.assertRaises(service.AIRequestLimitError) as caught:
                self.call("tutor_chat", model="openai:gpt-4.1-nano")
        provider.assert_not_called()
        self.assertEqual(caught.exception.scope, "provider_not_permitted")
        self.assertTrue(caught.exception.site_wide)
        application.app.config["AI_BUDGET_OPENAI_TOKENS_PER_DAY"] = 100_000
        service._RECENT_RESULTS.clear()
        with stub_provider("valid") as provider:
            self.call("tutor_chat", model="openai:gpt-4.1-nano")
        self.assertEqual(provider.call_count, 1)

    def test_premium_without_a_budget_is_not_used(self):
        self.premium(AI_BUDGET_ANTHROPIC_TOKENS_PER_DAY=None)
        from tests.provider_stub import fixture_text
        with patch.object(service, "_provider_response", return_value=FakeResponse(fixture_text("mistake_analysis"))) as provider:
            self.call("mistake_analysis", model="openai/gpt-oss-120b")
        self.assertEqual(provider.call_args.kwargs["model"], "openai/gpt-oss-120b")

    def test_a_groq_outage_with_no_permitted_alternative_is_a_clear_unavailable_error(self):
        class InternalServerError(RuntimeError):
            status_code = 503

        with patch.object(service, "_provider_response", side_effect=InternalServerError("down")) as provider:
            with self.assertRaises(service.AIProviderError) as caught:
                self.call()
        self.assertEqual(caught.exception.category, "provider_unavailable")
        self.assertEqual(provider.call_count, 1, "a provider-wide failure does not retry the same provider")
        with application.app.test_request_context():
            message, status, code = application.ai_failure_message(caught.exception)
        self.assertEqual((status, code), (503, "ai_unavailable"))

    # ---- deduplication: identical requests that overlap share one call ------------------
    def test_two_concurrent_identical_requests_make_one_provider_call(self):
        entered, release = threading.Event(), threading.Event()

        def slow_provider(**_kwargs):
            entered.set()
            release.wait(timeout=5)
            return FakeResponse(json.dumps(VALID_LESSON))

        outcomes: dict[str, str] = {}

        def worker(name):
            with application.app.app_context():
                outcomes[name] = self.call().output_text

        with patch.object(service, "_provider_response", side_effect=slow_provider) as provider:
            first = threading.Thread(target=worker, args=("first",))
            first.start()
            self.assertTrue(entered.wait(timeout=5))
            second = threading.Thread(target=worker, args=("second",))
            second.start()
            release.set()
            first.join(timeout=5)
            second.join(timeout=5)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(outcomes["first"], outcomes["second"])
        kinds = sorted(record["event_kind"] for record in service._read_usage_records()[-2:])
        self.assertEqual(kinds, ["call", "coalesced"])
        self.assertEqual(service.usage_ledger().totals().tokens, 18, "the shared answer is billed once")

    def test_a_repeat_within_the_window_is_coalesced_and_outside_it_is_not(self):
        with stub_provider("valid") as provider:
            self.call()
            self.call()
            self.assertEqual(provider.call_count, 1)
            service._RECENT_RESULTS.clear()           # the window has passed
            self.call()
            self.assertEqual(provider.call_count, 2)

    def test_a_dedup_window_of_zero_switches_it_off(self):
        application.app.config["AI_DEDUP_WINDOW_SECONDS"] = 0
        with stub_provider("valid") as provider:
            self.call()
            self.call()
        self.assertEqual(provider.call_count, 2)

    def test_the_diagnostics_summary_breaks_down_by_provider_and_shows_the_routing_table(self):
        with stub_provider("valid"):
            self.call()
        summary = service.diagnostics_summary()
        self.assertIn("groq", summary["by_provider"])
        self.assertEqual(summary["by_provider"]["groq"]["requests"], 1)
        routing_rows = {row["task_type"]: row for row in summary["routing"]}
        self.assertEqual(routing_rows["mistake_analysis"]["tier"], "strong")
        self.assertEqual(routing_rows["mistake_analysis"]["premium_slot"], "reasoning")
        self.assertEqual(routing_rows["tutor_chat"]["premium_slot"], "")
        self.assertEqual(routing_rows["ocr_document_recognition"]["fallback"], "")
        self.assertIn("coalesced_requests", summary)

    def test_quality_options_follow_the_model_actually_called(self):
        malformed = FakeResponse('{"lesson_title":"incomplete"}')
        valid = FakeResponse(json.dumps(VALID_LESSON))
        with patch.object(service, "_provider_response", side_effect=[malformed, valid]) as provider:
            self.call(model="plain-model")
        second = provider.call_args_list[1].kwargs
        if second["model"].startswith("openai/gpt-oss"):
            self.assertEqual(second["reasoning"], {"effort": "low"})
        self.assertNotIn("reasoning", provider.call_args_list[0].kwargs)

    def test_metadata_is_complete_and_sanitized(self):
        secret = "PRIVATE STUDENT MATERIAL"
        with stub_provider("valid"):
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
        # Budgets need watching where the money is spent, so the allowlist alone gates
        # the page - in production too.
        application.app.config["ENV_NAME"] = "production"
        self.assertEqual(self.client.get("/internal/ai-diagnostics").status_code, 200)

    def test_an_empty_allowlist_hides_diagnostics_everywhere(self):
        self.register("admin", "admin@example.com")
        application.app.config["AI_DIAGNOSTICS_ADMINS"] = set()
        for environment in ("development", "production"):
            application.app.config["ENV_NAME"] = environment
            self.assertEqual(self.client.get("/internal/ai-diagnostics").status_code, 404, environment)

    def test_the_limits_table_shows_configured_used_remaining_and_reset(self):
        self.register("admin", "admin@example.com")
        application.app.config.update(AI_BUDGET_GLOBAL_TOKENS_PER_DAY=1000)
        page = self.client.get("/internal/ai-diagnostics").get_data(as_text=True)
        self.assertIn("Budgets and limits", page)
        self.assertIn("global_tokens_day", page)
        self.assertIn("1000 tokens", page)
        self.assertIn("Requests by provider", page)
        self.assertIn("Estimated cost", page)

    def test_an_unconfigured_site_says_so_instead_of_an_empty_table(self):
        self.register("admin", "admin@example.com")
        for name in ("AI_BUDGET_GLOBAL_TOKENS_PER_DAY", "AI_BUDGET_GLOBAL_TOKENS_PER_MONTH",
                     "AI_BUDGET_USER_TOKENS_PER_DAY", "AI_BUDGET_USER_TOKENS_PER_MONTH",
                     "AI_BUDGET_GROQ_TOKENS_PER_DAY", "AI_BUDGET_GROQ_TOKENS_PER_MONTH",
                     "AI_RATE_GROQ_REQUESTS_PER_MINUTE", "AI_BUDGET_GLOBAL_SPEND_PER_MONTH"):
            application.app.config[name] = None
        page = self.client.get("/internal/ai-diagnostics").get_data(as_text=True)
        self.assertIn("No budgets configured", page)


if __name__ == "__main__":
    unittest.main()

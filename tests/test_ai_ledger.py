"""The usage ledger on the database: what budgets are actually checked against.

The JSONL log that used to serve this purpose was re-parsed on every request, capped at
its last 10 000 lines, invisible to any other process and gone on every deploy. These
tests pin the ledger's contract: every provider call is reserved before and settled after,
cache hits and refused requests are recorded but never counted, rows survive the request
that made them, and a request sees its own calls before they are flushed.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

import app as application
from learnova.ai_services import service
from tests.provider_stub import StubResponse

UTC = timezone.utc

VALID_LESSON = {
    "lesson_title": "Small lesson", "concepts": [{"name": "Addition"}],
    "explanation": "Add the values one step at a time.",
    "worked_example": {"problem": "1 + 1", "steps": ["Add one and one"], "answer": "2"},
    "question": {"id": "q1", "concept": "Addition", "difficulty": 1, "type": "text",
                 "prompt": "What is 1 + 1?", "hint": "Count once.", "options": [],
                 "expected_answer": "2"},
}


def lesson_response():
    return StubResponse(json.dumps(VALID_LESSON), model="stub-model")


class LedgerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=False, ENV_NAME="development", AI_MODE="live",
            AI_CACHE_DIR=str(root / "cache"), AI_USAGE_PATH=str(root / "usage.jsonl"),
            ALLOW_LIVE_AI=True, RUN_LIVE_AI_TEST=False, AI_ENFORCE_LIMITS=True,
            AI_BUDGET_GLOBAL_TOKENS_PER_DAY=None, AI_BUDGET_USER_TOKENS_PER_DAY=None,
        )
        self.context = application.app.app_context()
        self.context.push()
        application.db.drop_all()
        application.db.create_all()
        service._RESERVATIONS.clear()
        service._RECENT_RESULTS.clear()
        self.ledger = service.usage_ledger()

    def tearDown(self):
        self.context.pop()
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temp.cleanup()

    def call(self, **values):
        defaults: dict[str, Any] = dict(task_type="lesson_generation", language="English",
                                        private_scope="student-1", session_scope="session-1",
                                        model="stub-model", input="small input")
        defaults.update(values)
        return service.create_response(**defaults)

    def rows(self):
        self.ledger.flush()
        return application.db.session.scalars(
            application.db.select(application.AIUsageEvent).order_by(application.AIUsageEvent.id)).all()


class DatabaseLedgerTests(LedgerTestCase):
    def test_the_database_ledger_is_the_one_installed(self):
        self.assertIsInstance(self.ledger, application.DatabaseLedger)

    def test_a_provider_call_becomes_one_settled_row_with_its_real_tokens(self):
        with patch.object(service, "_provider_response", return_value=lesson_response()):
            self.call()
        rows = self.rows()
        self.assertEqual([row.event_kind for row in rows], ["call"])
        row = rows[0]
        self.assertTrue(row.settled)
        self.assertEqual(row.reserved_tokens, 0, "the reservation is replaced by actuals")
        self.assertEqual((row.input_tokens, row.output_tokens, row.total_tokens), (10, 8, 18))
        self.assertEqual(row.provider, "groq")
        self.assertEqual(row.task_type, "lesson_generation")
        self.assertTrue(row.success)
        self.assertNotEqual(row.user_reference, "student-1", "hashed, never the raw id")

    def test_a_request_sees_its_own_calls_before_they_are_flushed(self):
        with patch.object(service, "_provider_response", return_value=lesson_response()):
            self.call()
        # Nothing flushed yet: the buffer alone must account for it.
        self.assertEqual(self.ledger.totals().tokens, 18)
        self.assertEqual(self.ledger.count_requests(
            user_reference=service._anonymous_reference("student-1", label="user"),
            since=datetime.now(UTC) - timedelta(hours=1)), 1)

    def test_rows_are_flushed_when_the_context_ends(self):
        with patch.object(service, "_provider_response", return_value=lesson_response()):
            self.call()
        self.context.pop()
        try:
            with application.app.app_context():
                stored = application.db.session.scalar(application.db.select(
                    application.db.func.count()).select_from(application.AIUsageEvent))
                self.assertEqual(stored, 1)
        finally:
            self.context = application.app.app_context()
            self.context.push()

    def test_a_corrective_retry_is_two_rows_in_one_request(self):
        malformed = StubResponse('{"lesson_title":"incomplete"}')
        with patch.object(service, "_provider_response", side_effect=[malformed, lesson_response()]):
            self.call()
        rows = self.rows()
        self.assertEqual([row.attempt for row in rows], [1, 2])
        self.assertEqual(len({row.request_id for row in rows}), 1)
        totals = self.ledger.totals()
        self.assertEqual(totals.tokens, 36, "both calls were billed")
        self.assertEqual(totals.requests, 1, "but it was one request")

    def test_a_cache_hit_is_recorded_and_never_counted(self):
        application.app.config["AI_MODE"] = "cached"
        with patch.object(service, "_provider_response", return_value=lesson_response()) as provider:
            self.call()
            self.call()
        self.assertEqual(provider.call_count, 1)
        kinds = [row.event_kind for row in self.rows()]
        self.assertEqual(kinds, ["call", "cache_hit"])
        self.assertEqual(self.ledger.totals().tokens, 18, "the hit's 18 saved tokens are not added")

    def test_a_refused_request_is_recorded_with_zero_tokens(self):
        application.app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = 5
        with patch.object(service, "_provider_response", return_value=lesson_response()) as provider:
            with self.assertRaises(service.AIRequestLimitError) as caught:
                self.call()
        provider.assert_not_called()
        self.assertEqual(caught.exception.scope, "global_tokens_day")
        rows = self.rows()
        self.assertEqual([row.event_kind for row in rows], ["refused"])
        self.assertEqual(rows[0].total_tokens, 0)
        self.assertEqual(rows[0].error_category, "budget_exhausted")
        self.assertEqual(self.ledger.totals().tokens, 0)

    def test_a_failed_provider_call_releases_its_reservation(self):
        with patch.object(service, "_provider_response", side_effect=TimeoutError("slow")):
            with self.assertRaises(service.AIProviderError):
                self.call()
        self.assertEqual(service._RESERVATIONS.open_tokens(
            provider=None, user_reference=None, session_reference=None, since=None).tokens, 0)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0].success)
        self.assertEqual(rows[0].error_category, "provider_timeout")
        self.assertEqual(rows[0].total_tokens, 0, "nothing was reported, nothing is charged")

    def test_totals_filter_by_provider_user_session_and_window(self):
        with patch.object(service, "_provider_response", return_value=lesson_response()):
            self.call()
            self.call(private_scope="student-2", session_scope="session-2")
        self.ledger.flush()
        user_one = service._anonymous_reference("student-1", label="user")
        self.assertEqual(self.ledger.totals(user_reference=user_one).tokens, 18)
        self.assertEqual(self.ledger.totals(provider="groq").tokens, 36)
        self.assertEqual(self.ledger.totals(provider="openai").tokens, 0)
        self.assertEqual(self.ledger.totals(since=datetime.now(UTC) + timedelta(days=1)).tokens, 0)

    def test_the_migration_marker_is_recorded(self):
        application.ensure_database()
        self.assertIsNotNone(application.db.session.get(application.SchemaMigration, "023_ai_usage_events"))


class JsonlLedgerTests(LedgerTestCase):
    """The default ledger, for a checkout with no database wiring, keeps the old rules."""

    def setUp(self):
        super().setUp()
        service.set_usage_ledger(service.JsonlLedger())
        self.ledger = service.usage_ledger()

    def tearDown(self):
        service.set_usage_ledger(application.DatabaseLedger())
        super().tearDown()

    def test_only_provider_calls_count_toward_tokens(self):
        application.app.config["AI_MODE"] = "cached"
        with patch.object(service, "_provider_response", return_value=lesson_response()):
            self.call()
            self.call()
        self.assertEqual(self.ledger.totals().tokens, 18)
        self.assertEqual(self.ledger.count_requests(
            user_reference=service._anonymous_reference("student-1", label="user"),
            since=datetime.now(UTC) - timedelta(hours=1)), 2, "request caps still count the hit")

    def test_legacy_lines_without_event_kind_are_counted_by_provider_called(self):
        path = Path(application.app.config["AI_USAGE_PATH"])
        path.write_text(json.dumps({
            "timestamp": datetime.now(UTC).isoformat(), "provider_called": True,
            "cache_hit": False, "total_tokens": 7, "user_reference": "u", "session_reference": "s",
        }) + "\n" + json.dumps({
            "timestamp": datetime.now(UTC).isoformat(), "provider_called": False,
            "cache_hit": True, "total_tokens": 99, "user_reference": "u", "session_reference": "s",
        }) + "\n", encoding="utf-8")
        self.assertEqual(self.ledger.totals().tokens, 7)


if __name__ == "__main__":
    unittest.main()

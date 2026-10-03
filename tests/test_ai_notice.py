"""The student-facing side of a limit: "Max limit reached - using a slower AI model".

The gateway leaves `g.ai_degraded` on the request when a slower candidate answered after
a limit; `inject_ai_notice` turns that into an `ai_notice` on JSON responses and a flash
on redirects, and leaves errors alone.
"""

import json
import os
import tempfile
import unittest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.gettempdir(), "learnova-notice-test.db"))

import app as application  # noqa: E402
from flask import g, jsonify, make_response, redirect  # noqa: E402


class AiNoticeHookTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)

    def degraded(self):
        g.ai_degraded = {"reason": "provider_rate_limit", "model": "openai/gpt-oss-120b", "requested": "openai/gpt-oss-20b"}

    def test_a_json_answer_gets_the_notice(self):
        with application.app.test_request_context("/api/anything"):
            self.degraded()
            response = application.inject_ai_notice(jsonify(ok=True, answer="42"))
            payload = json.loads(response.get_data(as_text=True))
        self.assertEqual(payload["answer"], "42")
        self.assertEqual(payload["ai_notice"]["code"], "ai_slow_model")
        self.assertIn("Max limit reached", payload["ai_notice"]["message"])
        self.assertIn("slower AI model", payload["ai_notice"]["message"])
        self.assertEqual(payload["ai_notice"]["model"], "openai/gpt-oss-120b")

    def test_the_notice_is_translated(self):
        with application.app.test_request_context("/api/anything", headers={"Accept-Language": "de"}):
            application.flask_session["language"] = "de"
            self.degraded()
            payload = json.loads(application.inject_ai_notice(jsonify(ok=True)).get_data(as_text=True))
        self.assertIn("Maximales Limit erreicht", payload["ai_notice"]["message"])

    def test_errors_and_clean_answers_are_left_alone(self):
        with application.app.test_request_context("/api/anything"):
            self.degraded()
            error = application.inject_ai_notice(make_response(jsonify(error="no"), 422))
            self.assertNotIn("ai_notice", json.loads(error.get_data(as_text=True)))
        with application.app.test_request_context("/api/anything"):
            clean = application.inject_ai_notice(jsonify(ok=True))
            self.assertNotIn("ai_notice", json.loads(clean.get_data(as_text=True)))

    def test_a_redirect_gets_a_warning_flash_instead(self):
        with application.app.test_request_context("/practice-weak"):
            self.degraded()
            application.inject_ai_notice(redirect("/"))
            from flask import get_flashed_messages
            flashes = get_flashed_messages(with_categories=True)
        self.assertEqual(len(flashes), 1)
        self.assertEqual(flashes[0][0], "warning")
        self.assertIn("Max limit reached", flashes[0][1])

    def test_hard_limits_say_max_limit_reached(self):
        from learnova.ai_services import service
        with application.app.test_request_context():
            user_limit, status, code = application.ai_failure_message(
                service.AIRequestLimitError("x", scope="user_hour", retry_after_seconds=60))
            busy, busy_status, busy_code = application.ai_failure_message(
                service.AIProviderError("provider_rate_limit", "429"))
        self.assertEqual((status, code), (429, "ai_limit_reached"))
        self.assertTrue(user_limit.startswith("Max limit reached"), user_limit)
        self.assertEqual((busy_status, busy_code), (503, "ai_provider_busy"))
        self.assertTrue(busy.startswith("Max limit reached"), busy)


if __name__ == "__main__":
    unittest.main()

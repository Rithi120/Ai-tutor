"""A student's own AI cap: one plain sentence, a six-hour cooldown, study modes untouched.

The gateway runs for real here (only the provider is stubbed), because the limit, the
cooldown and the message are all decided on that path.
"""

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_cooldown_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import service  # noqa: E402
from tests.provider_stub import stub_provider  # noqa: E402
from tests.test_flashcard_modes_gamification import CARDS  # noqa: E402

SETTINGS = ("AI_MAX_REQUESTS_PER_USER_HOUR", "AI_USER_COOLDOWN_HOURS", "RUN_LIVE_AI_TEST", "ALLOW_LIVE_AI", "AI_MODE")


class CooldownTests(unittest.TestCase):
    def setUp(self):
        self.saved = {name: application.app.config.get(name) for name in SETTINGS}
        application.app.config.update(TESTING=True, AI_MODE="cached", RUN_LIVE_AI_TEST=True, ALLOW_LIVE_AI=True,
                                      AI_MAX_REQUESTS_PER_USER_HOUR=1, AI_USER_COOLDOWN_HOURS=6)
        application.SESSIONS.clear()
        service._RECENT_RESULTS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "amy", "email": "amy@example.com", "password": "correct-horse-battery"})

    def tearDown(self):
        application.app.config.update(self.saved)

    def analyze(self, client, goal):
        with stub_provider("valid"):
            return client.post("/api/analyze", data={"study_goal": goal, "subject": "Mathematics"})

    def user(self, name="amy"):
        with application.app.app_context():
            return application.db.session.scalar(application.db.select(application.User).where(application.User.username == name))

    def test_hitting_the_cap_starts_a_six_hour_cooldown_with_one_plain_sentence(self):
        first = self.analyze(self.client, "Fractions")
        self.assertEqual(first.status_code, 200, first.get_data(as_text=True))
        second = self.analyze(self.client, "Decimals")
        self.assertEqual(second.status_code, 429, second.get_data(as_text=True))
        body = second.get_json()
        self.assertEqual(body["code"], "ai_limit_reached")
        self.assertTrue(body["error"].startswith("You have reached your limit."), body["error"])
        self.assertIn("UTC", body["error"], "says when help is back")
        for jargon in ("API", "provider", "model", "Groq", "token"):
            self.assertNotIn(jargon, body["error"])
        retry_after = int(second.headers["Retry-After"])
        self.assertTrue(6 * 3600 - 60 <= retry_after <= 6 * 3600, retry_after)
        until = self.user().ai_cooldown_until
        self.assertIsNotNone(until)
        remaining = application.as_utc(until) - application.utcnow()
        self.assertTrue(timedelta(hours=5, minutes=58) < remaining <= timedelta(hours=6), remaining)
        # The cooldown, not the cap, now blocks: even with the cap lifted the answer is the same.
        application.app.config.update(AI_MAX_REQUESTS_PER_USER_HOUR=1000)
        third = self.analyze(self.client, "Percentages")
        self.assertEqual(third.status_code, 429)
        self.assertTrue(third.get_json()["error"].startswith("You have reached your limit."))

    def test_the_cooldown_does_not_cost_a_provider_call(self):
        self.analyze(self.client, "Fractions")
        self.analyze(self.client, "Decimals")
        with stub_provider("valid") as provider:
            self.client.post("/api/analyze", data={"study_goal": "Percentages", "subject": "Mathematics"})
        self.assertEqual(provider.call_count, 0)

    def test_study_modes_are_never_blocked_by_the_cooldown(self):
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(application.User).where(application.User.username == "amy"))
            assert user is not None
            user.ai_cooldown_until = application.utcnow() + timedelta(hours=6)
            application.db.session.commit()
        self.assertEqual(self.analyze(self.client, "Fractions").status_code, 429, "AI is paused ...")
        created = self.client.post("/api/flashcards/sets", json={"title": "Biology", "subject": "Biology", "cards": CARDS})
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        set_id = created.get_json()["id"]
        for mode in ("study", "learn", "test"):
            self.assertEqual(self.client.get(f"/flashcards/{set_id}/{mode}").status_code, 200, mode)
        started = self.client.post(f"/api/flashcards/sets/{set_id}/sessions", json={
            "mode": "learn", "count": 4, "resume": False, "idempotency_key": "cooldown-learn"})
        self.assertEqual(started.status_code, 201, started.get_data(as_text=True))
        session = started.get_json()["session"]
        item = session["items"][0]
        with application.app.app_context():
            row = application.db.session.get(application.FlashcardSessionItem, item["id"])
            assert row is not None
            expected = row.correct_answer
        answered = self.client.post(f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer", json={
            "answer": expected, "response_ms": 900, "request_id": "cooldown-answer-1"})
        self.assertEqual(answered.status_code, 200, answered.get_data(as_text=True))
        self.assertTrue(answered.get_json()["correct"], "... but studying flashcards works exactly as before")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_another_account_is_not_affected(self):
        self.analyze(self.client, "Fractions")
        self.assertEqual(self.analyze(self.client, "Decimals").status_code, 429)
        other = application.app.test_client()
        other.post("/register", data={"username": "ben", "email": "ben@example.com", "password": "correct-horse-battery"})
        self.assertEqual(self.analyze(other, "Fractions").status_code, 200)
        self.assertIsNone(self.user("ben").ai_cooldown_until)

    def test_the_cooldown_can_be_switched_off(self):
        application.app.config.update(AI_USER_COOLDOWN_HOURS=0)
        self.analyze(self.client, "Fractions")
        second = self.analyze(self.client, "Decimals")
        self.assertEqual(second.status_code, 429)
        self.assertTrue(second.get_json()["error"].startswith("You have reached your limit."))
        self.assertIsNone(self.user().ai_cooldown_until, "no cooldown stored; the cap is simply re-checked next time")

    def test_a_scope_that_is_not_an_account_keeps_the_ordinary_limit(self):
        with application.app.test_request_context():
            with stub_provider("valid"):
                service.create_response(task_type="lesson_generation", language="English", private_scope="student-x",
                                        model="fake-model", input="first")
                with self.assertRaises(service.AIRequestLimitError) as caught:
                    service.create_response(task_type="lesson_generation", language="English", private_scope="student-x",
                                            model="fake-model", input="second")
        self.assertEqual(caught.exception.scope, "user_hour")


if __name__ == "__main__":
    unittest.main()

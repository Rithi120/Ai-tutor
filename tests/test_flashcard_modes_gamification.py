import os
import tempfile
import unittest
from datetime import date
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_modes_gamification_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.flashcards import modes  # noqa: E402
from learnova.gamification import service as gamification  # noqa: E402


CARDS = [
    {"front": "Cell", "back": "Basic unit of life", "difficulty": "easy"},
    {"front": "Nucleus", "back": "Stores DNA", "difficulty": "medium"},
    {"front": "Mitochondria", "back": "Produces ATP", "difficulty": "hard"},
    {"front": "Ribosome", "back": "Builds proteins", "difficulty": "medium"},
]


class GamificationRuleTests(unittest.TestCase):
    def test_level_curve_streak_answer_and_game_rules(self):
        self.assertEqual(gamification.level_for_xp(0), 1)
        self.assertEqual(gamification.level_for_xp(100), 2)
        self.assertEqual(gamification.level_for_xp(250), 3)
        self.assertEqual(gamification.level_progress(260)["current_level_xp"], 10)
        streak = gamification.update_streak(2, 4, date(2026, 7, 26), date(2026, 7, 27))
        self.assertEqual(streak, (3, 4, True))
        self.assertTrue(modes.answer_is_correct("  PARIS. ", "Paris", "written"))
        self.assertFalse(modes.answer_is_correct("related", "Paris", "written"))
        self.assertGreater(modes.game_score("blast", 5, 1, 30, 4), 0)
        self.assertGreater(modes.weakness_score(1, 4, 2, 1.8), 50)


class FlashcardModeWorkflowTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_PRIVATE_FLASHCARDS=True,
            FEATURE_FLASHCARD_LEARN_MODE=True, FEATURE_FLASHCARD_TEST_MODE=True,
            FEATURE_FLASHCARD_MATCH_GAME=True, FEATURE_FLASHCARD_BLAST_GAME=True,
            FEATURE_FLASHCARD_BLOCKS_GAME=True, FEATURE_GAMIFICATION=True,
            FEATURE_MISSIONS=True, FEATURE_BADGES=True, FEATURE_DAILY_GOALS=True,
        )
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
            application.ensure_database()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "player", "email": "player@example.com",
            "password": "correct-horse-battery",
        })
        self.set_id = self.client.post("/api/flashcards/sets", json={
            "title": "Biology", "subject": "Biology", "cards": CARDS,
        }).get_json()["id"]

    def start(self, mode, **extra):
        response = self.client.post(f"/api/flashcards/sets/{self.set_id}/sessions", json={
            "mode": mode, "count": 4, "resume": False,
            "idempotency_key": f"{mode}-{extra.pop('key', 'one')}", **extra,
        })
        self.assertEqual(response.status_code, 201, response.data)
        return response.get_json()["session"]

    def expected(self, session_id, item_id):
        with application.app.app_context():
            item = application.db.session.get(application.FlashcardSessionItem, item_id)
            assert item is not None
            return item.correct_answer

    def test_routes_session_resume_ownership_and_inactive_time_cap(self):
        for mode in ("study", "learn", "test", "match", "blast", "blocks"):
            self.assertEqual(self.client.get(
                f"/flashcards/{self.set_id}/{mode}").status_code, 200)
        session = self.start("learn")
        resumed = self.client.post(f"/api/flashcards/sets/{self.set_id}/sessions", json={
            "mode": "learn", "resume": True,
        })
        self.assertTrue(resumed.get_json()["resumed"])
        self.assertEqual(resumed.get_json()["session"]["id"], session["id"])
        with application.app.app_context():
            row = application.db.session.get(application.FlashcardStudySession, session["id"])
            assert row is not None
            row.last_activity_at = application.utcnow() - application.timedelta(hours=2)
            application.db.session.commit()
        refreshed = self.client.get(f"/api/flashcards/sessions/{session['id']}").get_json()["session"]
        self.assertLessEqual(refreshed["active_seconds"], 120)
        stranger = application.app.test_client()
        stranger.post("/register", data={
            "username": "other", "email": "other@example.com",
            "password": "correct-horse-battery",
        })
        self.assertEqual(stranger.get(
            f"/api/flashcards/sessions/{session['id']}").status_code, 404)

    def test_learn_answer_mastery_star_xp_duplicate_and_completion(self):
        session = self.start("learn")
        item = session["items"][0]
        expected = self.expected(session["id"], item["id"])
        answer = self.client.post(
            f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
            json={"answer": expected, "response_ms": 900, "request_id": "answer-1"})
        self.assertTrue(answer.get_json()["correct"])
        duplicate = self.client.post(
            f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
            json={"answer": expected, "response_ms": 900, "request_id": "answer-1"})
        self.assertTrue(duplicate.get_json()["duplicate"])
        starred = self.client.put(
            f"/api/flashcards/cards/{item['card_id']}/star", json={"starred": True})
        self.assertTrue(starred.get_json()["starred"])
        complete = self.client.post(
            f"/api/flashcards/sessions/{session['id']}/complete",
            json={"max_combo": 1})
        self.assertEqual(complete.status_code, 200)
        completed = complete.get_json()["session"]
        self.assertEqual(completed["status"], "completed")
        self.assertGreater(completed["xp_earned"], 0)
        again = self.client.post(f"/api/flashcards/sessions/{session['id']}/complete")
        self.assertTrue(again.get_json()["duplicate"])
        profile = self.client.get("/api/gamification/profile").get_json()
        self.assertGreater(profile["profile"]["total_xp"], 0)

    def test_test_answers_hidden_autosave_grading_history_and_perfect_bonus(self):
        session = self.start("test")
        self.assertTrue(all("correct_answer" not in item for item in session["items"]))
        for item in session["items"]:
            expected = self.expected(session["id"], item["id"])
            saved = self.client.post(
                f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
                json={"answer": expected, "response_ms": 1200,
                      "request_id": f"test-{item['id']}"})
            self.assertEqual(saved.get_json(), {"ok": True, "saved": True})
        completed = self.client.post(
            f"/api/flashcards/sessions/{session['id']}/complete").get_json()["session"]
        self.assertEqual(completed["accuracy"], 100)
        self.assertTrue(all("correct_answer" in item for item in completed["items"]))
        self.assertEqual(self.client.get(
            f"/flashcards/{self.set_id}/test/results/{session['id']}").status_code, 200)
        with application.app.app_context():
            transactions = application.db.session.scalars(application.db.select(
                application.XPTransaction).where(
                    application.XPTransaction.session_id == session["id"])).all()
            self.assertEqual(len({row.idempotency_key for row in transactions}), len(transactions))

    def test_each_game_persists_result_personal_best_and_rejects_empty_completion(self):
        for mode in ("match", "blast", "blocks"):
            session = self.start(mode, key=mode)
            if mode == "match":
                self.assertLessEqual(session["total_items"], 6)
                miss = self.client.post(
                    f"/api/flashcards/sessions/{session['id']}/miss")
                self.assertEqual(miss.get_json()["incorrect_count"], 1)
            empty = self.client.post(f"/api/flashcards/sessions/{session['id']}/complete")
            self.assertEqual(empty.status_code, 400)
            item = session["items"][0]
            expected = self.expected(session["id"], item["id"])
            answered = self.client.post(
                f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
                json={"answer": expected, "response_ms": 1000,
                      "request_id": f"{mode}-answer"})
            self.assertEqual(answered.status_code, 200)
            result = self.client.post(
                f"/api/flashcards/sessions/{session['id']}/complete",
                json={"max_combo": 2}).get_json()["session"]
            self.assertEqual(result["status"], "completed")
            self.assertIsNotNone(result["summary"]["personal_best"])
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(application.db.select(
                application.db.func.count(application.GamePersonalBest.id))), 3)

    def test_legacy_review_uses_shared_mastery_and_xp_pipeline(self):
        session = self.start("flashcards")
        card_id = session["items"][0]["card_id"]
        reviewed = self.client.post(
            f"/api/flashcards/cards/{card_id}/review",
            json={"grade": "good", "response_ms": 800, "request_id": "legacy-one"})
        self.assertEqual(reviewed.status_code, 200)
        self.assertGreater(reviewed.get_json()["xp_earned"], 0)
        with application.app.app_context():
            card = application.db.session.get(application.Flashcard, card_id)
            assert card is not None
            self.assertEqual(card.consecutive_correct, 1)
            self.assertTrue(card.learned)

    def test_goals_missions_badges_progress_dashboard_and_german(self):
        goal = self.client.put("/api/gamification/goals/today", json={
            "type": "questions", "target": 1})
        self.assertEqual(goal.status_code, 200)
        session = self.start("flashcards")
        item = session["items"][0]
        self.client.post(
            f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
            json={"answer": "good", "response_ms": 500, "request_id": "goal-answer"})
        profile = self.client.get("/api/gamification/profile").get_json()
        self.assertTrue(profile["goal"]["completed"])
        self.assertTrue(profile["badges"])
        self.assertIn(b"Current streak", self.client.get("/dashboard").data)
        self.assertIn(b"Learning totals", self.client.get("/progress").data)
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(
                application.User).where(application.User.username == "player"))
            assert user is not None
            user.preferred_language = "de"
            application.db.session.commit()
        self.assertIn("Dein Fortschritt".encode(), self.client.get("/progress").data)

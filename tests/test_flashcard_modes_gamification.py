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

    def test_effective_streak_shows_zero_after_missed_days(self):
        self.assertEqual(gamification.effective_streak(5, date(2026, 7, 26), date(2026, 7, 27)), 5)
        self.assertEqual(gamification.effective_streak(5, date(2026, 7, 27), date(2026, 7, 27)), 5)
        self.assertEqual(gamification.effective_streak(5, date(2026, 7, 24), date(2026, 7, 27)), 0)
        self.assertEqual(gamification.effective_streak(5, None, date(2026, 7, 27)), 0)

    def test_every_repeatable_event_has_a_cap_group(self):
        self.assertEqual(gamification.xp_cap_group("flashcard_reviewed"), "flashcards")
        self.assertEqual(gamification.xp_cap_group("practice_question_completed"), "practice")
        self.assertEqual(gamification.xp_cap_group("vocabulary_reviewed"), "vocabulary")
        self.assertEqual(gamification.xp_cap_group("study_plan_task_completed"), "planning")
        # Once-per-period rewards stay uncapped by construction.
        for uncapped in ("badge_awarded", "mission_completed", "daily_goal_completed"):
            self.assertIsNone(gamification.xp_cap_group(uncapped))
        self.assertIn("match_completed", gamification.capped_group_events("flashcards"))


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

    def test_daily_group_caps_stop_xp_farming(self):
        # Fresh sessions always have fresh idempotency keys, so without a daily budget a
        # replayed set minted XP forever. The budget pays honest studying in full and
        # then stops; the events themselves still count as activity.
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(application.User))
            total = 0
            for index in range(60):
                _, earned = application.record_learning_event(
                    user.id, "flashcard_session_completed", f"cap-test-{index}", xp=15)
                total += earned
            application.db.session.commit()
            self.assertEqual(total, gamification.DAILY_XP_CAPS["flashcards"])

    def test_legacy_review_pays_once_per_card_per_day(self):
        with application.app.app_context():
            card_id = application.db.session.scalar(
                application.db.select(application.Flashcard.id))
        first = self.client.post(f"/api/flashcards/cards/{card_id}/review",
                                 json={"grade": "good", "request_id": "replay-1"}).get_json()
        self.assertGreater(first["xp_earned"], 0)
        # The old key trusted the client's request_id, so a fresh id paid again forever.
        replay = self.client.post(f"/api/flashcards/cards/{card_id}/review",
                                  json={"grade": "good", "request_id": "replay-2"}).get_json()
        self.assertEqual(replay["xp_earned"], 0)

    def test_award_responses_carry_totals_and_the_navbar_shows_the_chip(self):
        session = self.start("flashcards", key="chip")
        item = session["items"][0]
        answer = self.client.post(
            f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
            json={"answer": "good", "response_ms": 700, "request_id": "chip-1"}).get_json()
        self.assertGreater(answer["xp_earned"], 0)
        self.assertGreaterEqual(answer["xp"]["total_xp"], answer["xp_earned"])
        self.assertGreaterEqual(answer["xp"]["level"], 1)
        page = self.client.get(f"/flashcards/{self.set_id}/study").data.decode("utf-8")
        self.assertIn("data-xp-chip", page)
        self.assertIn("xp-chip-bar", page)

    def test_the_flag_hides_the_chip_and_the_totals(self):
        application.app.config.update(FEATURE_GAMIFICATION=False)
        try:
            page = self.client.get(f"/flashcards/{self.set_id}/study").data.decode("utf-8")
            self.assertNotIn("data-xp-chip", page)
            session = self.start("flashcards", key="noflag")
            item = session["items"][0]
            answer = self.client.post(
                f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
                json={"answer": "good", "response_ms": 700, "request_id": "noflag-1"}).get_json()
            self.assertIsNone(answer["xp"])
        finally:
            application.app.config.update(FEATURE_GAMIFICATION=True)

    def test_the_xp_notifier_dedupes_and_respects_the_flag(self):
        # Source pins for behavior no Python test can execute: the shared notifier
        # no-ops without the flag, never re-announces a seen key or a server duplicate,
        # persists the one-time explainer, and the client stops counting replays.
        root = Path(application.app.root_path)
        notifier = (root / "static/js/xp.js").read_text(encoding="utf-8")
        self.assertIn("FLAGS.gamification_enabled", notifier)
        self.assertIn("duplicate", notifier)
        self.assertIn("learnova:xpIntroSeen", notifier)
        self.assertIn("prefers-reduced-motion", notifier)
        engine = (root / "static/js/flashcards/mode-engine.js").read_text(encoding="utf-8")
        self.assertIn("announceXp(data", engine)
        self.assertIn("if (data.duplicate)", engine)
        css = (root / "static/css/learnova-components.css").read_text(encoding="utf-8")
        self.assertIn(".xp-toast", css)
        self.assertIn(".xp-intro", css)

    def test_count_zero_means_every_card_and_direction_is_honoured(self):
        # The session settings offer "All cards" (sent as count 0) and, for the
        # flashcards mode, a card direction - back_to_front asks the German side first.
        session = self.start("flashcards", count=0, direction="back_to_front", key="all")
        self.assertEqual(session["total_items"], len(CARDS))
        prompts = {item["prompt"] for item in session["items"]}
        self.assertEqual(prompts, {card["back"] for card in CARDS})

    def test_mixed_direction_alternates_sides_in_flashcards_mode(self):
        session = self.start("flashcards", count=0, direction="mixed", key="mixed")
        fronts = {card["front"] for card in CARDS}
        sides = ["front" if item["prompt"] in fronts else "back" for item in session["items"]]
        self.assertIn("front", sides)
        self.assertIn("back", sides, "mixed must actually ask both directions")

    def test_the_flashcards_page_offers_direction_all_cards_and_two_grades(self):
        page = self.client.get(f"/flashcards/{self.set_id}/study").data.decode("utf-8")
        self.assertIn('id="modeDirection"', page)
        self.assertIn('id="modeCountChoice"', page)
        self.assertIn('id="modeTotal"', page)
        self.assertIn('data-answer="again"', page)
        self.assertIn('data-answer="good"', page)
        # The simplified card keeps Previous/Show answer/Shuffle/Next plus Wrong/Correct;
        # the four-step grade row, full screen and hint button are gone.
        for removed in ('data-answer="hard"', 'data-answer="easy"',
                        'id="fullscreenMode"', 'id="showModeHint"'):
            self.assertNotIn(removed, page)
        for kept in ('id="previousCard"', 'id="flipCard"', 'id="nextCard"', 'id="shuffleCards"'):
            self.assertIn(kept, page)

    def test_the_engine_waits_after_grading_and_lights_up_next(self):
        # After Wrong/Correct the page stays on the card - no auto-advance timer - and
        # "Next" takes the Learnova violet; once every card is graded it completes the
        # session instead of looping.
        engine = (Path(application.app.root_path) / "static/js/flashcards/mode-engine.js").read_text(encoding="utf-8")
        self.assertIn('classList.add("next-ready")', engine)
        self.assertIn('items.every(item => item.answered)', engine)
        css = (Path(application.app.root_path) / "static/css/flashcards.css").read_text(encoding="utf-8")
        self.assertIn("#nextCard.next-ready", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_the_library_loading_skeleton_actually_hides_when_sets_arrive(self):
        # .cards-skeleton/.cards-grid set an explicit display, which beats the browser's
        # [hidden] rule - the three shimmer tiles stayed visible next to the loaded sets.
        css = (Path(application.app.root_path) / "static/css/flashcards.css").read_text(encoding="utf-8")
        self.assertIn(".cards-grid[hidden], .cards-skeleton[hidden] { display: none; }", css)

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

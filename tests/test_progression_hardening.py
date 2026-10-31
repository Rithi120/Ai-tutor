"""Production hardening: hints, challenge integrity, usernames, league fairness, CLI, avatars."""

import importlib.util
import os
import tempfile
import threading
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_progression_hardening_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.gamification import challenges, usernames  # noqa: E402
from tests.test_profiles_leagues import Base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
db = application.db


def mission(user_id, key, target, **fields):
    row = application.UserMission(
        user_id=user_id, mission_key=key, period=challenges.CHALLENGE_BY_KEY[key].period, target=target,
        reward_xp=challenges.CHALLENGE_BY_KEY[key].reward_xp,
        starts_at=application.utcnow() - timedelta(hours=1), expires_at=application.utcnow() + timedelta(hours=5),
        metric=challenges.CHALLENGE_BY_KEY[key].metric, difficulty="easy", **fields)
    db.session.add(row)
    db.session.commit()
    return row


# ------------------------------------------------------------------ hints


class HintTests(Base):
    def setUp(self):
        super().setUp()
        self.alice = self.user("alice")
        application.SESSIONS["lesson-1"] = {
            "user_id": self.alice.id,
            "current_question": {"id": "q1", "prompt": "2 + 2?", "hint": "Count on your fingers.",
                                 "expected_answer": "4"}}

    def tearDown(self):
        application.SESSIONS.pop("lesson-1", None)
        super().tearDown()

    def test_the_browser_never_receives_the_hint_or_the_answer(self):
        view = application.student_question(application.SESSIONS["lesson-1"]["current_question"])
        self.assertNotIn("hint", view)
        self.assertNotIn("expected_answer", view)
        self.assertTrue(view["has_hint"])
        self.assertFalse(application.student_question({"prompt": "x", "hint": " "})["has_hint"])

    def test_revealing_a_hint_is_recorded_on_the_server(self):
        client = self.client_for("alice")
        response = client.post("/api/hint", json={"session_id": "lesson-1"})
        self.assertEqual(response.get_json()["hint"], "Count on your fingers.")
        self.assertTrue(application.SESSIONS["lesson-1"]["current_question"]["hint_revealed"])

    def test_other_accounts_and_hintless_questions_get_nothing(self):
        self.user("mallory")
        self.assertEqual(self.client_for("mallory").post("/api/hint", json={"session_id": "lesson-1"}).status_code, 404)
        application.SESSIONS["lesson-1"]["current_question"]["hint"] = ""
        self.assertEqual(self.client_for("alice").post("/api/hint", json={"session_id": "lesson-1"}).status_code, 404)

    def test_asking_the_tutor_during_a_question_counts_as_help(self):
        failure = application.ai_service.AIProviderError("provider_timeout", "timed out")
        client = self.client_for("alice")
        with patch.object(application, "create_response", side_effect=failure):
            try:
                client.post("/api/chat", json={"session_id": "lesson-1", "message": "help?"})
            except KeyError:
                pass  # the bare test session lacks lesson content; the flag is set first
        self.assertTrue(application.SESSIONS["lesson-1"].get("help_used"))

    def test_a_client_false_cannot_hide_a_recorded_hint(self):
        # The no-hints challenge only counts an explicit False on the lesson event.
        definition = challenges.CHALLENGE_BY_KEY["daily_no_hints_1"]
        self.assertIsNone(challenges.contribution(definition, "quiz_completed", {"hints_used": True}))
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn('bool(payload.get("hints_used", False)) or bool(', source)


# ------------------------------------------------------------------ challenge integrity


class ChallengeIntegrityTests(Base):
    def test_self_graded_answers_are_reviews_not_correct_answers(self):
        alice = self.user("alice")
        correct = mission(alice.id, "daily_correct_10", 3)
        reviews = mission(alice.id, "daily_flashcards_10", 3)
        for index in range(3):
            application.record_learning_event(alice.id, "flashcard_reviewed", f"self{index}",
                                              metadata={"correct": True, "self_graded": True}, xp=2)
        db.session.commit()
        db.session.refresh(correct)
        db.session.refresh(reviews)
        self.assertEqual((correct.progress, reviews.progress), (0, 3))
        self.assertIsNone(correct.completed_at)
        self.assertIsNotNone(reviews.completed_at)

    def test_improvement_only_counts_for_server_scored_tests(self):
        definition = challenges.CHALLENGE_BY_KEY["daily_improve_1"]
        self.assertEqual(challenges.contribution(definition, "test_completed", {"improved": True}), 1)
        self.assertIsNone(challenges.contribution(definition, "flashcard_session_completed", {"improved": True}))

    def test_repeated_submissions_do_not_add_progress(self):
        alice = self.user("alice")
        row = mission(alice.id, "daily_correct_10", 5)
        for _ in range(4):
            application.record_learning_event(alice.id, "practice_question_completed", "same-attempt",
                                              metadata={"correct": True}, xp=3)
        db.session.commit()
        db.session.refresh(row)
        self.assertEqual(row.progress, 1)

    def test_a_stale_read_cannot_pay_a_challenge_twice(self):
        alice = self.user("alice")
        row = mission(alice.id, "daily_correct_10", 1)
        # Another request finished it a moment ago; this session still holds the old row.
        db.session.execute(
            application.update(application.UserMission).where(application.UserMission.id == row.id)
            .values(completed_at=application.utcnow()).execution_options(synchronize_session=False))
        application.record_learning_event(alice.id, "practice_question_completed", "late",
                                          metadata={"correct": True}, xp=3)
        db.session.commit()
        self.assertEqual(application.XPTransaction.query.filter_by(
            idempotency_key=f"mission:{row.id}").count(), 0)

    def test_concurrent_answers_both_count_and_pay_once(self):
        alice = self.user("alice")
        row = mission(alice.id, "daily_correct_10", 2)
        barrier = threading.Barrier(2)
        errors = []

        def answer(key):
            with application.app.app_context():
                try:
                    barrier.wait(5)
                    application.record_learning_event(alice.id, "practice_question_completed", key,
                                                      metadata={"correct": True}, xp=3)
                    db.session.commit()
                except Exception as error:  # noqa: BLE001 - surfaced below
                    errors.append(error)
                finally:
                    db.session.remove()

        threads = [threading.Thread(target=answer, args=(f"c{index}",)) for index in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertEqual(errors, [])
        db.session.expire_all()
        refreshed = db.session.get(application.UserMission, row.id)
        self.assertEqual(refreshed.progress, 2)
        self.assertIsNotNone(refreshed.completed_at)
        self.assertEqual(application.XPTransaction.query.filter_by(
            idempotency_key=f"mission:{row.id}").count(), 1)

    def test_no_endpoint_accepts_xp_from_the_client(self):
        self.user("alice")
        client = self.client_for("alice")
        for path in ("/api/challenges", "/api/leaderboard", "/api/gamification/profile"):
            self.assertIn(client.post(path, json={"xp": 500, "progress": 99}).status_code, {404, 405})
        self.assertEqual(application.XPTransaction.query.count(), 0)


# ------------------------------------------------------------------ usernames


class UsernameHardeningTests(Base):
    def test_substitutions_separators_and_stretching_are_caught(self):
        for name in ("f4ck_you", "fuuuuck", "f.u.c.k", "sh1t", "b1tch", "arschl0ch", "hurens0hn", "n4zi", "5ex"):
            self.assertEqual(usernames.problem(name), "inappropriate", name)

    def test_impersonation_with_lookalike_digits_is_caught(self):
        for name in ("4dmin", "adm1n", "1earnova", "l34rnova_team", "m0derator", "supp0rt", "m0d"):
            self.assertEqual(usernames.problem(name), "reserved", name)

    def test_unicode_lookalikes_never_reach_the_name(self):
        self.assertEqual(usernames.problem("аdmin"), "format")          # Cyrillic a
        self.assertEqual(usernames.problem("ａｄｍｉｎ"), "reserved")  # full-width, NFKC
        self.assertEqual(usernames.problem("anna​"), "format")           # zero-width space

    def test_ordinary_names_are_not_false_positives(self):
        for name in ("anna.k", "sebastian", "classic", "assassin", "scunthorpe", "therapist", "cockpit",
                     "dickens", "kassel", "sussex", "penistone", "arsenal", "hanna2009", "glass_half_full",
                     "badminton_fan", "analyst", "heilbronn", "sauerkraut", "titanic", "shakespeare",
                     "mohammed", "nguyen", "leon_2010", "mia.s", "emma-04", "cassie", "sextant"):
            self.assertIsNone(usernames.problem(name), name)

    def test_near_copies_of_staff_names_are_refused(self):
        alice = self.user("alice")
        with application.app.test_request_context():
            for candidate in ("mod_anna", "m0d.anna", "modanna", "mod-annna"):
                self.assertFalse(application.change_username(alice, candidate)[0], candidate)
            self.assertTrue(application.change_username(alice, "anna.mod")[0])

    def test_cooldown_and_hold_still_apply(self):
        alice, bob = self.user("alice"), self.user("bobby")
        with application.app.test_request_context():
            self.assertTrue(application.change_username(alice, "alice.two")[0])
            self.assertFalse(application.change_username(alice, "alice.three")[0])
            self.assertFalse(application.change_username(bob, "alice")[0])


# ------------------------------------------------------------------ league fairness


class LeagueFairnessTests(Base):
    def past_week(self):
        starts, ends, week_start = application.league_week_bounds()
        row = application.LeagueWeek(week_start=week_start - timedelta(days=7), starts_at=starts - timedelta(days=7),
                                     ends_at=ends - timedelta(days=7), status="open")
        db.session.add(row)
        db.session.commit()
        return row

    def enter(self, week, account, tier, xp):
        db.session.merge(application.LeagueMembership(user_id=account.id, tier=tier))
        db.session.add(application.LeagueEntry(week_start=week.week_start, user_id=account.id, tier=tier,
                                               group_number=1, joined_at=application.as_utc(week.starts_at)))
        if xp:
            db.session.add(application.XPTransaction(
                user_id=account.id, event_type="test", amount=xp, reason="test",
                idempotency_key=f"x:{account.id}", created_at=application.as_utc(week.starts_at) + timedelta(hours=2)))
        db.session.commit()

    def test_leaving_before_processing_means_no_move_and_no_zone(self):
        week = self.past_week()
        accounts = [self.user(f"p{index}") for index in range(3)]
        for index, account in enumerate(accounts):
            self.enter(week, account, 3, xp=10 * (index + 1))
        accounts[0].leaderboard_enabled = False      # would have been demoted
        db.session.commit()
        application.process_due_league_weeks()
        entries = {row.user_id: row for row in application.LeagueEntry.query.all()}
        self.assertEqual(entries[accounts[0].id].outcome, "withdrawn")
        self.assertEqual(db.session.get(application.LeagueMembership, accounts[0].id).tier, 3)
        # Two remaining learners: one promotion, no demotion.
        self.assertEqual(entries[accounts[2].id].outcome, "promoted")
        self.assertEqual(entries[accounts[1].id].outcome, "stayed")

    def test_each_learner_has_one_entry_and_one_transition_per_week(self):
        alice = self.user("alice")
        for key in ("a", "b", "c"):
            application.award_xp(alice.id, "test", 10, key)
        db.session.commit()
        self.assertEqual(application.LeagueEntry.query.filter_by(user_id=alice.id).count(), 1)
        week = self.past_week()
        self.enter(week, self.user("bob1"), 2, xp=40)
        self.assertEqual(len(application.process_due_league_weeks()), 1)
        self.assertEqual(application.process_due_league_weeks(), [])

    def test_daily_xp_cap_uses_the_server_day(self):
        alice = self.user("alice")
        for index in range(120):
            application.record_learning_event(alice.id, "flashcard_reviewed", f"cap{index}",
                                              metadata={"correct": True}, xp=4)
        db.session.commit()
        earned = sum(row.amount for row in application.XPTransaction.query.filter(
            application.XPTransaction.event_type == "flashcard_reviewed").all())
        self.assertLessEqual(earned, 200)


# ------------------------------------------------------------------ CLI


class LeagueCliTests(Base):
    def run_cli(self, *args):
        return application.app.test_cli_runner().invoke(args=list(args))

    def test_process_is_idempotent_and_reports(self):
        first = self.run_cli("leagues", "process")
        self.assertEqual(first.exit_code, 0, first.output)
        self.assertIn("Nothing due", first.output)
        self.assertEqual(self.run_cli("leagues", "process").exit_code, 0)

    def test_failures_exit_non_zero_and_overdue_weeks_are_flagged(self):
        with patch.object(application, "process_due_league_weeks", side_effect=RuntimeError("db down")):
            result = self.run_cli("leagues", "process")
        self.assertEqual(result.exit_code, 1)
        starts, ends, week_start = application.league_week_bounds()
        db.session.add(application.LeagueWeek(week_start=week_start - timedelta(days=7), starts_at=starts - timedelta(days=7),
                                              ends_at=ends - timedelta(days=7), status="open"))
        db.session.commit()
        self.assertEqual(self.run_cli("leagues", "status", "--check").exit_code, 2)
        self.run_cli("leagues", "process")
        self.assertEqual(self.run_cli("leagues", "status", "--check").exit_code, 0)


# ------------------------------------------------------------------ avatar generator


class AvatarGeneratorTests(unittest.TestCase):
    def test_the_generator_reproduces_the_committed_artwork(self):
        spec = importlib.util.spec_from_file_location("build_avatars", ROOT / "scripts" / "avatars" / "build_avatars.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        drawn = module.build()
        self.assertEqual(len(drawn), 20)
        for identifier, text in drawn.items():
            committed = (ROOT / "static" / "avatars" / f"{identifier}.svg").read_bytes()
            self.assertEqual(text.encode("utf-8"), committed, identifier)


if __name__ == "__main__":
    unittest.main()

"""Leagues, completed (2026-11): tiers, withdrawal, gaps, history, fail-soft transition and XP integrity.

Spec "Test 8": XP earning, weekly ranking, promotion, demotion, weekly reset, opt-out
(withdrawn + demotion in the zone, rejoin), duplicate XP, a manipulated client request,
the fail-soft transition and the gap/history view.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_profiles_leagues_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.gamification import leagues  # noqa: E402
from learnova.gamification import service as gamification  # noqa: E402
from learnova.translations import catalog  # noqa: E402
from tests.test_profiles_leagues import Base  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
db = application.db
S = leagues.Standing


# ------------------------------------------------------------------ pure rules


class TierAndZoneRuleTests(unittest.TestCase):
    T0 = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)

    def test_the_seven_leagues_are_bronze_to_diamond(self):
        self.assertEqual([item.name for item in leagues.LEAGUES], [
            "Bronze League", "Silver League", "Gold League", "Sapphire League", "Ruby League",
            "Emerald League", "Diamond League"])
        self.assertEqual([item.tier for item in leagues.LEAGUES], list(range(1, 8)))
        self.assertEqual((leagues.league(6).key, leagues.league(7).key), ("emerald", "diamond"))
        self.assertEqual(leagues.HIGHEST, 7)
        self.assertEqual(catalog.GERMAN["Emerald League"], "Smaragd-Liga")
        self.assertEqual(catalog.GERMAN["Diamond League"], "Diamant-Liga")
        css = (ROOT / "static/css/profile.css").read_text(encoding="utf-8")
        self.assertIn(".league-diamond { animation: crest-glow", css)
        self.assertNotIn(".league-legendary", css)

    def test_withdrawn_learners_count_toward_the_zones(self):
        # 12 in Silver (4 / 4). One left from the demotion zone: 11 still ranked, the zones
        # are sized for 12 and the leaver keeps the demotion place they took.
        self.assertEqual(leagues.zone_sizes(11, 2, withdrawn=1, withdrawn_demoted=1), (4, 3))
        # Left from the safe middle: nobody's zone changes.
        self.assertEqual(leagues.zone_sizes(11, 2, withdrawn=1), (4, 4))
        # Without withdrawals the old rules hold exactly.
        for size in range(0, 40):
            for tier in range(1, 8):
                self.assertEqual(leagues.zone_sizes(size, tier, withdrawn=0),
                                 leagues.zone_sizes(size, tier))
        # The rest of a group of two is one learner who can still move up.
        self.assertEqual(leagues.zone_sizes(1, 3, withdrawn=1), (1, 0))
        # Zones never overlap among the learners still ranked; promotion wins.
        up, down = leagues.zone_sizes(15, 3, withdrawn=5)
        self.assertLessEqual(up + down, 15)
        self.assertEqual(up, 10)

    def test_xp_gaps_to_next_rank_promotion_and_out_of_demotion(self):
        order = leagues.ranked([S(1, 50, self.T0), S(2, 30, self.T0), S(3, 10, self.T0)])
        self.assertEqual(leagues.xp_gaps(order, 2, 1, 1),
                         {"to_next_rank": 21, "to_promotion": 41, "to_leave_demotion": 21})
        self.assertEqual(leagues.xp_gaps(order, 0, 1, 1),
                         {"to_next_rank": 0, "to_promotion": 0, "to_leave_demotion": 0})
        self.assertEqual(leagues.xp_gaps(order, 1, 1, 1)["to_promotion"], 21)
        # A tie goes to whoever got there first, so passing them needs one XP more.
        tie = leagues.ranked([S(1, 30, self.T0), S(2, 30, self.T0 + timedelta(hours=1))])
        self.assertEqual(leagues.xp_gaps(tie, 1, 1, 0)["to_next_rank"], 1)
        # No promotion zone (top league): nothing to reach.
        self.assertEqual(leagues.xp_gaps(order, 2, 0, 1)["to_promotion"], 0)


# ------------------------------------------------------------------ integration


class LeagueLifecycleTests(Base):
    def week_rows(self, weeks_ago=1, status="open"):
        starts, ends, week_start = application.league_week_bounds()
        delta = timedelta(days=7 * weeks_ago)
        row = application.LeagueWeek(week_start=week_start - delta, starts_at=starts - delta,
                                     ends_at=ends - delta, status=status)
        db.session.add(row)
        db.session.commit()
        return row

    def enter(self, week, account, tier, xp, group=1, offset_minutes=0):
        db.session.merge(application.LeagueMembership(user_id=account.id, tier=tier))
        db.session.add(application.LeagueEntry(week_start=week.week_start, user_id=account.id, tier=tier,
                                               group_number=group, joined_at=application.as_utc(week.starts_at)))
        if xp:
            self.xp_at(account.id, xp, application.as_utc(week.starts_at) + timedelta(hours=1, minutes=offset_minutes),
                       f"w{week.week_start}:{account.id}")
        db.session.commit()

    def tier(self, account):
        return db.session.get(application.LeagueMembership, account.id).tier

    def other_client(self, name):
        # Base pushes one app context for the whole test, so Flask-Login's cached user in
        # `g` would carry over from the previous client's request.
        application.g.pop("_login_user", None)
        return self.client_for(name)

    # -- XP earning, ranking, promotion, demotion

    def test_full_group_of_30_promotes_10_and_demotes_10(self):
        week = self.week_rows()
        accounts = [self.user(f"s{index:02d}") for index in range(30)]
        for index, account in enumerate(accounts):
            self.enter(week, account, 2, xp=10 * (index + 1))
        self.assertEqual(application.process_due_league_weeks(), [week.week_start])
        promoted = sorted(a.username for a in accounts if self.tier(a) == 3)
        demoted = sorted(a.username for a in accounts if self.tier(a) == 1)
        self.assertEqual(promoted, [f"s{index:02d}" for index in range(20, 30)])
        self.assertEqual(demoted, [f"s{index:02d}" for index in range(0, 10)])
        best = application.LeagueEntry.query.filter_by(user_id=accounts[-1].id).one()
        self.assertEqual((best.final_rank, best.final_xp, best.group_size, best.outcome, best.tier_after),
                         (1, 300, 30, "promoted", 3))

    def test_xp_earned_through_learning_ranks_the_week(self):
        alice, bob = self.user("alice"), self.user("bobby")
        application.record_learning_event(alice.id, "practice_question_completed", "a1",
                                          metadata={"correct": True}, xp=3)
        application.record_learning_event(bob.id, "quiz_completed", "b1", metadata={"correct": True}, xp=10)
        db.session.commit()
        with application.app.test_request_context():
            view = application.league_view(alice)
        self.assertEqual([row["weekly_xp"] for row in view["standings"]], [10, 3])
        self.assertEqual(view["me"]["rank"], 2)
        self.assertEqual(view["me"]["xp_to_next_rank"], 8)

    def test_weekly_xp_resets_and_lifetime_xp_is_intact(self):
        last = self.week_rows()
        alice = self.user("alice", xp=1000)
        self.enter(last, alice, 2, xp=120)
        application.process_due_league_weeks()
        self.assertEqual(db.session.get(application.UserGamificationProfile, alice.id).total_xp, 1000)
        application.award_xp(alice.id, "test", 15, "new-week")
        db.session.commit()
        with application.app.test_request_context():
            view = application.league_view(alice)
        self.assertTrue(view["joined"])
        self.assertEqual(view["me"]["weekly_xp"], 15)      # last week's 120 is not carried over
        self.assertEqual(db.session.get(application.UserGamificationProfile, alice.id).total_xp, 1015)
        self.assertEqual(view["history"][0]["xp"], 120)

    # -- opting out mid-week

    def silver_week(self, count=12):
        accounts = [self.user(f"s{index:02d}") for index in range(count)]
        for index, account in enumerate(accounts):
            db.session.add(application.LeagueMembership(user_id=account.id, tier=2))
            db.session.commit()
            application.award_xp(account.id, "test", 10 * (index + 1), f"silver:{account.id}")
            db.session.commit()
        return accounts

    def test_opting_out_in_the_demotion_zone_withdraws_and_demotes(self):
        accounts = self.silver_week()
        client = self.client_for("s00")                  # rank 12 of 12: demotion zone
        response = client.post("/settings/profile/preferences", data={}, follow_redirects=True)
        self.assertIn("demotion zone", response.get_data(as_text=True))
        db.session.expire_all()
        entry = application.LeagueEntry.query.filter_by(user_id=accounts[0].id).one()
        self.assertEqual((entry.outcome, entry.final_rank, entry.group_size, entry.final_xp, entry.tier_after),
                         ("withdrawn", 12, 12, 10, 1))
        self.assertEqual(self.tier(accounts[0]), 1)
        # Hidden from everyone at once; the zones of the rest are still sized for 12.
        data = self.other_client("s08").get("/api/leaderboard").get_json()
        self.assertEqual(len(data["standings"]), 11)
        self.assertNotIn("s00", [row["username"] for row in data["standings"]])
        self.assertEqual((data["promote"], data["demote"]), (4, 3))
        self.assertEqual(data["me"]["zone"], "promotion")
        # At the end of the week: s08 is still promoted (11 would have promoted only 3),
        # the leaver is not processed again, and only three more are demoted.
        _starts, ends, _week_start = application.league_week_bounds()
        application.process_due_league_weeks(ends + timedelta(hours=1))
        db.session.expire_all()
        self.assertEqual(sorted(a.username for a in accounts if self.tier(a) == 3), ["s08", "s09", "s10", "s11"])
        self.assertEqual(sorted(a.username for a in accounts if self.tier(a) == 1), ["s00", "s01", "s02", "s03"])
        entry = application.LeagueEntry.query.filter_by(user_id=accounts[0].id).one()
        self.assertEqual((entry.outcome, entry.tier_after), ("withdrawn", 1))

    def test_opting_out_in_the_safe_zone_keeps_the_league_and_rejoins_next_week(self):
        accounts = self.silver_week()
        middle = accounts[6]                              # rank 6 of 12: safe
        client = self.client_for(middle.username)
        client.post("/settings/profile/preferences", data={})
        db.session.expire_all()
        entry = application.LeagueEntry.query.filter_by(user_id=middle.id).one()
        self.assertEqual((entry.outcome, entry.tier_after), ("withdrawn", 2))
        self.assertEqual(self.tier(middle), 2)
        # Leaving twice changes nothing; coming back waits for next week.
        client.post("/settings/profile/preferences", data={"leaderboard_enabled": "on"})
        db.session.expire_all()
        _starts, ends, week_start = application.league_week_bounds()
        self.assertGreater(db.session.get(application.LeagueMembership, middle.id).rejoin_from, week_start)
        application.award_xp(middle.id, "test", 50, "after-rejoin")
        db.session.commit()
        self.assertEqual(application.LeagueEntry.query.filter_by(user_id=middle.id).count(), 1)
        data = client.get("/api/leaderboard").get_json()
        self.assertFalse(data["joined"])
        # The withdrawal is this week's result for them, listed at once.
        self.assertEqual([(item["outcome"], item["rank"], item["size"], item["moved"]) for item in data["history"]],
                         [("withdrawn", 6, 12, "same")])
        later = ends + timedelta(hours=1)
        application.process_due_league_weeks(later)
        joined = application.ensure_league_entry(middle.id, later)
        db.session.commit()
        self.assertIsNotNone(joined)
        self.assertEqual((joined.tier, joined.outcome), (2, ""))

    def test_two_leavers_from_the_demotion_zone_change_nobody_elses_result(self):
        accounts = self.silver_week()
        for name in ("s00", "s01"):                       # the last, then the new last
            self.other_client(name).post("/settings/profile/preferences", data={})
        db.session.expire_all()
        withdrawn = application.LeagueEntry.query.filter_by(outcome="withdrawn").all()
        self.assertEqual(sorted((row.final_rank, row.group_size, row.tier_after) for row in withdrawn),
                         [(11, 11, 1), (12, 12, 1)])
        data = self.other_client("s05").get("/api/leaderboard").get_json()
        self.assertEqual((len(data["standings"]), data["promote"], data["demote"]), (10, 4, 2))
        _starts, ends, _week_start = application.league_week_bounds()
        application.process_due_league_weeks(ends + timedelta(hours=1))
        db.session.expire_all()
        # Exactly the result of the week had both stayed: 4 up, 4 down.
        self.assertEqual(sorted(a.username for a in accounts if self.tier(a) == 3), ["s08", "s09", "s10", "s11"])
        self.assertEqual(sorted(a.username for a in accounts if self.tier(a) == 1), ["s00", "s01", "s02", "s03"])

    def test_leaving_bronze_from_the_bottom_cannot_demote(self):
        accounts = [self.user(f"b{index}") for index in range(3)]
        for index, account in enumerate(accounts):
            application.award_xp(account.id, "test", 10 * (index + 1), f"bronze:{account.id}")
            db.session.commit()
        response = self.other_client("b0").post("/settings/profile/preferences", data={}, follow_redirects=True)
        self.assertNotIn("demotion zone, so you will be", response.get_data(as_text=True))
        db.session.expire_all()
        entry = application.LeagueEntry.query.filter_by(user_id=accounts[0].id).one()
        self.assertEqual((entry.outcome, entry.tier_after), ("withdrawn", leagues.LOWEST))
        self.assertEqual(self.tier(accounts[0]), leagues.LOWEST)

    def test_history_renders_a_legacy_withdrawal_without_a_result(self):
        # Before 2026-11 a learner who left after the week ended was stored without a rank,
        # weekly XP or group size.
        week = self.week_rows(status="closed")
        alice = self.user("alice")
        db.session.add(application.LeagueMembership(user_id=alice.id, tier=3))
        db.session.add(application.LeagueEntry(
            week_start=week.week_start, user_id=alice.id, tier=3, group_number=1,
            joined_at=application.as_utc(week.starts_at), outcome="withdrawn", tier_after=3,
            result_seen_at=application.utcnow()))
        db.session.commit()
        client = self.client_for("alice")
        history = client.get("/api/leaderboard").get_json()["history"]
        self.assertEqual([(item["outcome"], item["rank"], item["size"], item["xp"], item["moved"])
                          for item in history], [("withdrawn", None, None, 0, "same")])
        html = client.get("/leaderboard").get_data(as_text=True)
        self.assertIn("Left the leagues", html)
        self.assertNotIn("Rank None", html)

    # -- the transition on the request path

    def test_a_failing_transition_never_breaks_a_page(self):
        week = self.week_rows()
        for name in ("alice", "bobby"):
            self.enter(week, self.user(name), 2, xp=40 if name == "alice" else 20)
        client = self.client_for("alice")
        application.app.config["PROPAGATE_EXCEPTIONS"] = False
        with patch.object(application.league_rules, "outcomes", side_effect=RuntimeError("poisoned week")):
            for path in ("/leaderboard", "/profile", "/api/leaderboard"):
                self.assertEqual(client.get(path).status_code, 200, path)
            db.session.expire_all()
            self.assertEqual(db.session.get(application.LeagueWeek, week.week_start).status, "open")
            result = application.app.test_cli_runner().invoke(args=["leagues", "process"])
            self.assertEqual(result.exit_code, 1)
        self.assertEqual(application.app.test_cli_runner().invoke(args=["leagues", "process"]).exit_code, 0)
        db.session.expire_all()
        self.assertEqual(db.session.get(application.LeagueWeek, week.week_start).status, "closed")

    def test_a_week_closes_only_after_the_grace_period(self):
        _starts, ends, week_start = application.league_week_bounds()
        alice = self.user("alice")
        application.award_xp(alice.id, "test", 10, "in-week")
        db.session.commit()
        just_after = ends + timedelta(seconds=30)
        self.assertEqual(application.process_due_league_weeks(just_after), [])
        self.assertIsNone(application.ensure_league_entry(alice.id, just_after))   # no joins meanwhile
        with application.app.test_request_context():
            self.assertTrue(application.league_view(alice, just_after)["results_pending"])
        self.assertEqual(application.process_due_league_weeks(ends + timedelta(seconds=121)), [week_start])

    def test_xp_earned_before_the_transition_joins_the_new_week(self):
        _starts, ends, _week_start = application.league_week_bounds()
        alice, bob = self.user("alice"), self.user("bobby")
        for account in (alice, bob):
            application.award_xp(account.id, "test", 10, f"old:{account.id}")
        db.session.commit()
        # Monday 00:01: Bob learns while last week is still waiting to close.
        early = ends + timedelta(minutes=1)
        self.xp_at(bob.id, 10, early, "new:bob")
        db.session.commit()
        self.assertIsNone(application.ensure_league_entry(bob.id, early))      # joining is blocked
        _s, _e, next_week = application.league_week_bounds(early)
        self.assertEqual(application.LeagueEntry.query.filter_by(week_start=next_week).count(), 0)
        application.process_due_league_weeks(ends + timedelta(minutes=5))
        entries = application.LeagueEntry.query.filter_by(week_start=next_week).all()
        self.assertEqual([row.user_id for row in entries], [bob.id])     # Alice earned nothing new

    def test_joining_takes_the_group_lock_on_postgresql_only(self):
        executed = []
        real_execute = db.session.execute

        def record(statement, *args, **kwargs):
            executed.append((str(statement), args))
            if "pg_advisory_xact_lock" in str(statement):
                return None
            return real_execute(statement, *args, **kwargs)

        _starts, _ends, week_start = application.league_week_bounds()
        with patch.object(db.session, "execute", side_effect=record):
            application.lock_league_group_assignment(week_start, 3)
        self.assertEqual(executed, [])                    # SQLite: one writer, no lock needed
        with patch.object(db.engine.dialect, "name", "postgresql"),                 patch.object(db.session, "execute", side_effect=record):
            application.lock_league_group_assignment(week_start, 3)
        self.assertEqual(len(executed), 1)
        self.assertIn("pg_advisory_xact_lock", executed[0][0])
        self.assertEqual(executed[0][1][0]["key"], week_start.toordinal() * 8 + 3)

    # -- lifetime XP

    def test_lifetime_xp_increment_survives_a_concurrent_award(self):
        alice = self.user("alice", xp=100)
        profile = db.session.get(application.UserGamificationProfile, alice.id)
        self.assertEqual(profile.total_xp, 100)          # loaded, now stale for this session
        with db.engine.begin() as connection:            # another request's award commits meanwhile
            connection.execute(application.update(application.UserGamificationProfile).where(
                application.UserGamificationProfile.user_id == alice.id).values(total_xp=150))
        self.assertEqual(application.award_xp(alice.id, "test", 10, "late"), 10)
        db.session.commit()
        db.session.expire_all()
        self.assertEqual(db.session.get(application.UserGamificationProfile, alice.id).total_xp, 160)

    # -- the view: gaps and history

    def test_gaps_and_history_are_shown_and_in_the_api(self):
        alice, bob, cara = self.user("alice"), self.user("bobby"), self.user("carla")
        for weeks_ago in (10, 9, 8, 7, 6, 5, 4, 3, 2, 1):
            week = self.week_rows(weeks_ago)
            self.enter(week, alice, 2, xp=weeks_ago)
            self.enter(week, bob, 2, xp=100 + weeks_ago)
            application.process_due_league_weeks()
        for account, amount in ((alice, 10), (bob, 50), (cara, 30)):
            db.session.merge(application.LeagueMembership(user_id=account.id, tier=2))
            db.session.commit()
            application.award_xp(account.id, "test", amount, f"now:{account.id}")
            db.session.commit()
        client = self.client_for("alice")
        data = client.get("/api/leaderboard").get_json()
        self.assertEqual((data["me"]["rank"], data["me"]["zone"]), (3, "demotion"))
        self.assertEqual((data["me"]["xp_to_next_rank"], data["me"]["xp_to_promotion"],
                          data["me"]["xp_to_leave_demotion"]), (21, 41, 21))
        history = data["history"]
        self.assertEqual(len(history), application.LEAGUE_HISTORY_WEEKS)
        self.assertEqual([item["week_start"] for item in history],
                         sorted((item["week_start"] for item in history), reverse=True))
        self.assertEqual(history[0]["outcome"], "stayed")    # second of two: no promotion, no demotion
        self.assertTrue(all(item["xp"] <= 10 for item in history))   # Alice's weeks only, never Bob's
        self.assertTrue(all(set(item) >= {"league", "league_after", "rank", "size", "xp", "outcome_label"}
                            for item in history))
        self.assertNotIn("user_id", json.dumps(data))
        html = client.get("/leaderboard").get_data(as_text=True)
        self.assertIn("21 XP to pass #2", html)
        self.assertIn("41 XP to reach the promotion zone", html)
        self.assertIn("21 XP to leave the demotion zone", html)
        self.assertIn("Your last weeks", html)
        self.assertEqual(html.count('class="league-history-row'), application.LEAGUE_HISTORY_WEEKS)
        self.assertRegex(html, r'data-countdown-ended="[^"]+"')
        self.assertRegex(html, r'data-countdown-reload>Week ends in \d')      # readable without JavaScript

    def test_the_leaderboard_reads_in_german(self):
        alice = self.user("alice", preferred_language="de")
        application.award_xp(alice.id, "test", 10, "de")
        db.session.commit()
        html = self.client_for("alice").get("/leaderboard").get_data(as_text=True)
        self.assertIn("Deine letzten Wochen", html)
        self.assertIn("Woche endet in", html)
        self.assertIn("XP diese Woche", html)

    def test_every_league_msgid_is_translated(self):
        import re
        pattern = re.compile(r"""_\(\s*'((?:[^'\\]|\\.)*)'""")
        names = ("leaderboard.html", "components/_profile_settings.html")
        found = set()
        for name in names:
            found |= set(pattern.findall((ROOT / "templates" / name).read_text(encoding="utf-8")))
        found |= {"Promoted", "Demoted", "Stayed", "Left the leagues", "Week of {date}",
                  "You left from the demotion zone, so you will be in the {league} when you come back."}
        found |= {item.name for item in leagues.LEAGUES}
        for code in ("es", "fr"):
            table = json.loads((ROOT / f"learnova/translations/data/{code}.json").read_text(encoding="utf-8"))
            for msgid in found:
                self.assertIn(msgid, table, f"{code}: {msgid}")
        for msgid in sorted(found):
            self.assertIn(msgid, catalog.GERMAN, msgid)


# ------------------------------------------------------------------ XP integrity


class XpIntegrityTests(Base):
    CARDS = [{"front": f"Wort {index}", "back": f"word {index}", "difficulty": "medium"} for index in range(8)]

    def setUp(self):
        super().setUp()
        application.app.config.update(FEATURE_FLASHCARD_LEARN_MODE=True, FEATURE_FLASHCARD_TEST_MODE=True,
                                      FEATURE_FLASHCARD_MATCH_GAME=True)
        self.account = self.user("alice")
        self.client = self.client_for("alice")

    def make_set(self, cards=None):
        response = self.client.post("/api/flashcards/sets", json={
            "title": "Vokabeln", "subject": "English", "cards": cards or self.CARDS})
        self.assertIn(response.status_code, {200, 201}, response.data)
        return response.get_json()["id"]

    def start(self, set_id, mode="learn", count=8, key="one"):
        response = self.client.post(f"/api/flashcards/sets/{set_id}/sessions", json={
            "mode": mode, "count": count, "resume": False, "idempotency_key": f"{mode}-{key}"})
        self.assertEqual(response.status_code, 201, response.data)
        return response.get_json()["session"]

    def expected(self, item_id):
        return db.session.get(application.FlashcardSessionItem, item_id).correct_answer

    def ledger(self):
        db.session.expire_all()
        return application.XPTransaction.query.order_by(application.XPTransaction.id).all()

    def test_the_same_event_pays_once(self):
        first = application.record_learning_event(self.account.id, "practice_question_completed", "same",
                                                  metadata={"correct": True}, xp=3)[1]
        second = application.record_learning_event(self.account.id, "practice_question_completed", "same",
                                                   metadata={"correct": True}, xp=3)[1]
        self.assertEqual((first, second), (3, 0))
        self.assertEqual(application.award_xp(self.account.id, "test", 5, "k" * 200), 5)
        self.assertEqual(application.award_xp(self.account.id, "test", 5, "k" * 200), 0)   # long keys too
        db.session.commit()
        session = self.start(self.make_set())
        item = session["items"][0]
        url = f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer"
        paid = self.client.post(url, json={"answer": self.expected(item["id"]), "request_id": "r1"}).get_json()
        replay = self.client.post(url, json={"answer": self.expected(item["id"]), "request_id": "r2"}).get_json()
        self.assertGreater(paid["xp_earned"], 0)
        self.assertTrue(replay["duplicate"])
        keys = [row.idempotency_key for row in self.ledger()]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(sum(1 for key in keys if key.startswith(f"xp:fc-answer:{session['id']}")), 1)

    def test_a_manipulated_request_cannot_change_xp_rank_or_league(self):
        session = self.start(self.make_set())
        forged = {"xp": 9999, "amount": 9999, "xp_earned": 9999, "idempotency_key": "forged",
                  "key": "forged", "tier": 7, "league": "diamond", "weekly_xp": 9999, "rank": 1,
                  "event_type": "badge_awarded", "request_id": "same-id"}
        earned = []
        for item in session["items"][:5]:
            response = self.client.post(f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
                                        json={**forged, "answer": self.expected(item["id"]), "response_ms": 2000})
            earned.append(response.get_json()["xp_earned"])
        # Each correct answer pays the server's amount, even with the same client request id.
        self.assertTrue(all(0 < value <= 4 for value in earned), earned)
        completed = self.client.post(f"/api/flashcards/sessions/{session['id']}/complete",
                                     json={**forged, "max_combo": 999}).get_json()["session"]
        rows = self.ledger()
        allowed = {"flashcard_reviewed": {2, 3, 4},
                   "flashcard_mastered": {gamification.XP_VALUES["flashcard_mastered"]},
                   "learn_session_completed": {gamification.XP_VALUES["learn_session_completed"]}}
        for row in rows:
            if row.session_id == session["id"]:
                self.assertIn(row.amount, allowed.get(row.event_type, set()), (row.event_type, row.amount))
        self.assertIn("learn_session_completed", {row.event_type for row in rows})
        self.assertNotIn("forged", " ".join(row.idempotency_key for row in rows))
        self.assertEqual(completed["xp_earned"], sum(row.amount for row in rows if row.session_id == session["id"]))
        for path in ("/api/leaderboard", "/api/leaderboard/result-seen"):
            self.client.post(path, json=forged)
        self.client.post("/settings/profile/preferences", data={"leaderboard_enabled": "on", **{
            key: str(value) for key, value in forged.items()}})
        db.session.expire_all()
        membership = db.session.get(application.LeagueMembership, self.account.id)
        self.assertEqual(membership.tier, leagues.LOWEST)
        data = self.client.get("/api/leaderboard").get_json()
        self.assertEqual(data["me"]["weekly_xp"], sum(row.amount for row in self.ledger()))

    def test_a_short_session_pays_its_answers_but_no_completion_xp(self):
        set_id = self.make_set()
        session = self.start(set_id)
        self.assertGreaterEqual(session["total_items"], 5)
        for item in session["items"][:4]:                 # 4 of 8: below min(5, 8)
            self.client.post(f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
                             json={"answer": self.expected(item["id"]), "response_ms": 1500})
        done = self.client.post(f"/api/flashcards/sessions/{session['id']}/complete").get_json()["session"]
        self.assertEqual(done["status"], "completed")
        events = {row.event_type for row in self.ledger()}
        self.assertNotIn("learn_session_completed", events)
        self.assertGreater(done["xp_earned"], 0)          # the four answers still paid
        self.assertEqual(application.LearningEvent.query.filter_by(event_type="learn_session_completed").count(), 1)
        # Five answered items: the completion is paid.
        session = self.start(set_id, key="two")
        for item in session["items"][:5]:
            self.client.post(f"/api/flashcards/sessions/{session['id']}/items/{item['id']}/answer",
                             json={"answer": self.expected(item["id"]), "response_ms": 1500})
        self.client.post(f"/api/flashcards/sessions/{session['id']}/complete")
        paid = [row for row in self.ledger() if row.event_type == "learn_session_completed"]
        self.assertEqual([row.amount for row in paid], [gamification.XP_VALUES["learn_session_completed"]])
        # A set shorter than five needs every item.
        self.assertTrue(gamification.session_completion_pays(2, 2))
        self.assertFalse(gamification.session_completion_pays(1, 2))
        self.assertFalse(gamification.session_completion_pays(4, 30))

    def test_a_project_exam_is_paid_from_the_practice_budget(self):
        for index in range(20):                           # the flashcard budget is used up today
            application.record_learning_event(self.account.id, "flashcard_session_completed", f"fc{index}", xp=15)
        db.session.commit()
        self.assertEqual(sum(row.amount for row in self.ledger()), gamification.DAILY_XP_CAPS["flashcards"])
        project = application.LearningProject(user_id=self.account.id, title="Biologie", subject="Biology")
        db.session.add(project)
        db.session.flush()
        section = application.LearningSection(project_id=project.id, position=1, title="Zellen")
        db.session.add(section)
        db.session.flush()
        exam = application.FinalExam(
            project_id=project.id, question_count=1, duration_minutes=10, difficulty_mode="mixed",
            included_section_ids_json=json.dumps([section.id]), question_types_json='["multiple_choice"]',
            expires_at=application.utcnow() + timedelta(hours=1))
        db.session.add(exam)
        db.session.flush()
        question = application.ExamQuestion(
            exam_id=exam.id, section_id=section.id, position=1, difficulty="medium",
            question_type="multiple_choice", prompt="Was ist eine Zelle?", concepts_json='["Zelle"]',
            options_json=json.dumps([{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]),
            expected_answer="a", supporting_text="Zellen")
        db.session.add(question)
        db.session.commit()
        response = self.client.post(f"/exams/{exam.id}/submit", data={f"question_{question.id}": "a"})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        paid = [row for row in self.ledger() if row.idempotency_key == f"xp:final-exam:{exam.id}"]
        self.assertEqual([(row.event_type, row.amount) for row in paid],
                         [("project_exam_completed", gamification.XP_VALUES["test_completed"])])
        self.assertEqual(gamification.xp_cap_group("project_exam_completed"), "practice")
        # Still a test for challenges, badges and statistics.
        self.assertEqual(application.LearningEvent.query.filter_by(
            idempotency_key=f"final-exam:{exam.id}").one().event_type, "test_completed")


if __name__ == "__main__":
    unittest.main()

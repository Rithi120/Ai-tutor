"""Simulated league rivals (learnova/gamification/rivals.py and the league code in app.py).

The rules (counts, profiles, schedules, limits, names) are tested as pure functions; the
league integration on a real database: filling groups through the real join, the page, the
rival profile, the weekly transition and the cleanup.
"""

import json
import re
import unittest
from collections import Counter
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash

import app as application
from learnova.authentication.service import USERNAME_PATTERN
from learnova.gamification import leagues
from learnova.gamification import rivals
from tests.test_profiles_leagues import Base

db = application.db
ROOT = Path(__file__).resolve().parents[1]
BERLIN = ZoneInfo("Europe/Berlin")
PASSWORD_HASH = generate_password_hash("correct-horse-battery")


class RivalRuleTests(unittest.TestCase):
    MONDAY = __import__("datetime").datetime(2026, 11, 9, tzinfo=BERLIN)

    def test_how_many_rivals_a_group_gets(self):
        for real, expected in ((3, 27), (10, 20), (20, 10), (26, 4), (27, 0), (28, 0), (29, 0), (30, 0)):
            self.assertEqual(rivals.rival_count(real), expected, real)
            self.assertLessEqual(real + rivals.rival_count(real), 30)
        self.assertEqual(rivals.rival_count(0), 30)

    def test_seats_are_dealt_40_40_15_5(self):
        dealt = Counter(rivals.behaviour_for("2026-11-09", 2, 4, seat) for seat in range(1, 31))
        self.assertEqual(dealt, {"casual": 12, "consistent": 12, "competitive": 4, "strong": 2})

    def test_names_look_like_learners_names(self):
        names = [rivals.name_for(rivals.seed_for("2026-11-09", 1, 1, seat))[0] for seat in range(1, 400)]
        for name in names:
            self.assertTrue(USERNAME_PATTERN.fullmatch(name), name)
            self.assertIsNone(re.search(r"\d{3,}", name), f"no number garbage: {name}")
            self.assertRegex(name, r"^[a-z]+(\d{1,2}|_\d|\.[a-z]{2,})?[a-z]*$")
        self.assertGreater(len(set(names)), 300, "enough combinations for many leagues")
        username, display = rivals.name_for(12345)
        self.assertTrue(display[0].isupper())

    def test_the_week_target_and_events_stay_within_the_limits(self):
        limits = rivals.Limits()
        for tier in range(1, 8):
            for base in (20, 60, 150, 400):
                for seat in range(1, 30):
                    plan = rivals.plan_rival(self.MONDAY, tier, 1, seat, baseline_xp=base)
                    self.assertEqual(plan.target_xp, sum(event.xp for event in plan.events))
                    self.assertLessEqual(plan.target_xp, limits.max_weekly_xp)
                    self.assertLessEqual(plan.target_xp, round(base * 1.8) + 1, "the strongest stay beatable")
                    daily = Counter()
                    for event in plan.events:
                        daily[event.at.date()] += event.xp
                        self.assertIn(event.kind, rivals.EVENT_KINDS)
                        self.assertGreater(event.xp, 0)
                        self.assertLessEqual(event.xp, rivals.EVENT_XP[event.kind][1] + 6, "no score jumps")
                        self.assertTrue(self.MONDAY <= event.at < self.MONDAY + timedelta(days=7))
                    self.assertLessEqual(max(daily.values(), default=0), limits.max_daily_xp)

    def test_profiles_learn_as_described(self):
        plans = [rivals.plan_rival(self.MONDAY, 1, 1, seat, baseline_xp=100) for seat in range(1, 30)]
        by = {key: [plan for plan in plans if plan.behaviour == key] for key in rivals.BEHAVIOUR_KEYS}
        days = {key: [len({event.at.date() for event in plan.events}) for plan in items] for key, items in by.items()}
        self.assertTrue(all(count <= 4 for count in days["casual"]), "casual: irregular")
        self.assertTrue(all(count >= 5 for count in days["consistent"]), "consistent: most days")
        mean = {key: sum(plan.target_xp for plan in items) / max(1, len(items)) for key, items in by.items()}
        self.assertLess(mean["casual"], mean["consistent"])
        self.assertLess(mean["consistent"], mean["competitive"])
        self.assertLessEqual(mean["competitive"], mean["strong"])

    def test_calibration_uses_last_weeks_median_or_the_default(self):
        self.assertEqual(rivals.baseline(1, [10, 50, 80, 90, 200]), 80)
        self.assertEqual(rivals.baseline(1, [10, 50, 80, 90]), 60, "too few results: the default")
        self.assertEqual(rivals.baseline(3, []), 84, "the default grows with the league")
        self.assertEqual(rivals.baseline(1, [0] * 9), 20, "bounded below")
        self.assertEqual(rivals.baseline(1, [5000] * 9), 400, "bounded above")

    def test_a_plan_is_reproducible_and_seeded(self):
        first = rivals.plan_rival(self.MONDAY, 2, 3, 7, baseline_xp=90)
        self.assertEqual(first, rivals.plan_rival(self.MONDAY, 2, 3, 7, baseline_xp=90))
        renamed = rivals.plan_rival(self.MONDAY, 2, 3, 7, baseline_xp=90, name_attempt=1)
        self.assertNotEqual(renamed.username, first.username)
        self.assertEqual(renamed.events, first.events, "a new name never changes the score")

    def test_no_ai_and_no_network(self):
        source = (ROOT / "learnova/gamification/rivals.py").read_text(encoding="utf-8")
        for forbidden in ("ai_services", "openai", "anthropic", "groq", "requests", "urllib", "http"):
            self.assertNotIn(f"import {forbidden}", source)
            self.assertNotIn(f"from {forbidden}", source)


class RivalLeagueTests(Base):
    def setUp(self):
        super().setUp()
        application.app.config.update(LEAGUE_RIVALS=True)
        starts, ends, self.week_start = application.league_week_bounds()
        self.week = application.LeagueWeek(week_start=self.week_start, starts_at=starts, ends_at=ends, status="open")
        db.session.add(self.week)
        db.session.commit()
        self.counter = 0

    def learners(self, count, tier=1, group=1, prefix="real"):
        accounts = []
        for _ in range(count):
            self.counter += 1
            account = application.User(username=f"{prefix}{self.counter}", email=f"{prefix}{self.counter}@example.com")
            account.password_hash = PASSWORD_HASH
            db.session.add(account)
            db.session.flush()
            db.session.add(application.LeagueMembership(user_id=account.id, tier=tier))
            db.session.add(application.LeagueEntry(week_start=self.week_start, user_id=account.id, tier=tier,
                                                   group_number=group, joined_at=application.as_utc(self.week.starts_at)))
            accounts.append(account)
        db.session.flush()
        return accounts

    def fill(self, tier=1, group=1):
        application.sync_group_rivals(self.week_start, tier, group)
        db.session.commit()
        return application.group_rivals(self.week_start, tier, group)

    def at(self, hours_from_start):
        return application.as_utc(self.week.starts_at) + timedelta(hours=hours_from_start)

    # -- population

    def test_groups_are_filled_to_30_only_below_27_real_learners(self):
        for group, (real, expected) in enumerate(((3, 27), (10, 20), (20, 10), (26, 4), (27, 0), (28, 0), (29, 0),
                                                  (30, 0)), start=1):
            self.learners(real, group=group)
            found = self.fill(group=group)
            self.assertEqual(len(found), expected, real)
            self.assertEqual([rival.seat for rival in found], list(range(1, expected + 1)))
            self.assertLessEqual(real + len(found), 30)

    def test_filling_twice_creates_nothing_new(self):
        self.learners(5)
        first = [(rival.id, rival.username) for rival in self.fill()]
        events = db.session.scalar(db.select(db.func.count(application.LeagueRivalEvent.id)))
        for _ in range(3):
            self.assertEqual([(rival.id, rival.username) for rival in self.fill()], first)
        self.assertEqual(db.session.scalar(db.select(db.func.count(application.LeagueRivalEvent.id))), events)

    def test_a_duplicate_seat_or_name_is_refused_by_the_database(self):
        self.learners(2)
        rival = self.fill()[0]
        for field in ({"seat": rival.seat, "username": "someone.else"}, {"seat": 29, "username": rival.username}):
            with self.assertRaises(IntegrityError):
                with db.session.begin_nested():
                    db.session.add(application.LeagueRival(
                        week_start=self.week_start, tier=1, group_number=1, display_name="X", avatar_id=rival.avatar_id,
                        level=3, behaviour="casual", seed=1, baseline_xp=60, target_xp=10, **field))
        self.assertEqual(len(application.group_rivals(self.week_start, 1, 1)), 28)

    def test_joining_through_learning_fills_and_a_new_learner_takes_a_rivals_seat(self):
        alice = self.user("alice")
        application.record_learning_event(alice.id, "quiz_completed", "a1", metadata={"correct": True}, xp=10)
        db.session.commit()
        self.assertEqual(len(application.group_rivals(self.week_start, 1, 1)), 29)
        last = application.group_rivals(self.week_start, 1, 1)[-1]
        bob = self.user("bobby")
        application.record_learning_event(bob.id, "quiz_completed", "b1", metadata={"correct": True}, xp=10)
        db.session.commit()
        now = application.group_rivals(self.week_start, 1, 1)
        self.assertEqual(len(now), 28)
        self.assertNotIn(last.id, [rival.id for rival in now], "the last seat's rival left")
        self.assertIsNone(db.session.scalar(db.select(application.LeagueRivalEvent.id).where(
            application.LeagueRivalEvent.rival_id == last.id)), "with its events")

    def test_thirty_joins_never_exceed_30_participants(self):
        for index in range(30):
            account = self.learners(1)[0]
            found = self.fill()
            self.assertLessEqual(index + 1 + len(found), 30)
            self.assertEqual(len(found), rivals.rival_count(index + 1), account.username)
        self.assertEqual(self.fill(), [])

    def test_names_are_unique_and_never_a_real_learners_name(self):
        planned = rivals.name_for(rivals.seed_for(self.week_start.isoformat(), 1, 1, 1))[0]
        self.user(planned)            # a real learner already has the name seat 1 would get
        self.learners(1)
        self.learners(4, group=2)
        names = [rival.username for rival in db.session.scalars(db.select(application.LeagueRival)).all()
                 ] if self.fill() and self.fill(group=2) else []
        self.assertNotIn(planned, names)
        self.assertEqual(len(names), len(set(names)), "unique within the week")
        real = set(db.session.scalars(db.select(application.User.username)).all())
        self.assertFalse(real & set(names))

    def test_two_groups_never_share_rivals(self):
        self.learners(3, group=1)
        self.learners(3, tier=2, group=1)
        one, two = self.fill(), self.fill(tier=2)
        self.assertFalse({rival.id for rival in one} & {rival.id for rival in two})
        self.assertTrue(all((rival.tier, rival.group_number) == (1, 1) for rival in one))
        self.assertTrue(all((rival.tier, rival.group_number) == (2, 1) for rival in two))

    # -- scores and ranks

    def test_scores_come_only_from_events_that_happened(self):
        self.learners(1)
        found = self.fill()
        early = application.rival_standings(found, self.at(0))
        self.assertTrue(all(item.weekly_xp == 0 for item in early))
        end = application.rival_standings(found, application.as_utc(self.week.ends_at))
        totals = {rival.id: rival.target_xp for rival in found}
        self.assertEqual({-item.user_id: item.weekly_xp for item in end}, totals)
        mid = application.rival_standings(found, self.at(84))
        for before, after in zip(mid, end):
            self.assertLessEqual(before.weekly_xp, after.weekly_xp, "never goes down")

    def test_rivals_overtake_an_inactive_learner_and_an_active_one_overtakes_them(self):
        idle = self.learners(1)[0]
        self.xp_at(idle.id, 15, self.at(1), "idle")
        db.session.commit()
        self.fill()
        ranks = []
        for hours in (2, 60, 167):
            order = leagues.ranked(application.group_standings(self.week, 1, 1, self.at(hours)))
            ranks.append([item.user_id for item in order].index(idle.id) + 1)
        self.assertEqual(ranks[0], 1, "first while the rivals have not learned yet")
        self.assertGreater(ranks[2], ranks[1], "an idle week slides down, gradually")
        self.assertGreater(ranks[1], 1)
        # Studying more than the strongest rival's whole week puts the learner on top.
        best = max(rival.target_xp for rival in application.group_rivals(self.week_start, 1, 1))
        self.xp_at(idle.id, best, self.at(100), "comeback")
        db.session.commit()
        order = leagues.ranked(application.group_standings(self.week, 1, 1, self.at(167)))
        self.assertEqual(order[0].user_id, idle.id)

    def test_ties_are_broken_the_same_way_every_time(self):
        moment = self.at(10)
        real = leagues.Standing(7, 40, moment)
        rival_later = leagues.Standing(-3, 40, moment + timedelta(minutes=5))
        rival_same = leagues.Standing(-4, 40, moment)
        rival_other = leagues.Standing(-9, 40, moment)
        order = leagues.ranked([rival_later, rival_other, real, rival_same])
        self.assertEqual([item.user_id for item in order], [-9, -4, 7, -3])
        for _ in range(5):
            self.assertEqual(leagues.ranked(reversed(order)), order)
        # A rival with no XP yet (no moment) never beats a learner with the same XP.
        self.assertEqual(leagues.ranked([leagues.Standing(-1, 0, None), leagues.Standing(5, 0, None)])[0].user_id, -1)

    def test_the_page_shows_rivals_marked_and_linked_to_their_profile(self):
        alice = self.user("alice")
        application.record_learning_event(alice.id, "quiz_completed", "a1", metadata={"correct": True}, xp=10)
        db.session.commit()
        with application.app.test_request_context():
            view = application.league_view(alice)
        self.assertEqual(view["size"], 30)
        self.assertEqual((view["promote"], view["demote"]), (10, 0), "Bronze: 10 up, nobody down")
        rows = view["standings"]
        self.assertEqual(sum(row["rival"] for row in rows), 29)
        self.assertEqual([row["username"] for row in rows if row["me"]], ["alice"])
        for row in rows:
            if row["rival"]:
                self.assertRegex(row["profile_url"], r"^/leaderboard/rival/\d+$")
                self.assertIsInstance(row["level"], int)
        html = self.client_for("alice").get("/leaderboard").get_data(as_text=True)
        self.assertEqual(html.count('class="league-rival-chip"'), 29)

    def test_a_rival_profile_shows_level_achievements_activity_and_says_simulated(self):
        # The badge catalogue row ensure_database seeds (the test database starts empty).
        db.session.add(application.BadgeDefinition(
            id="first_steps", name="First Steps", description="Complete your first meaningful learning activity.",
            icon="spark", category="general", requirement=1, tier="bronze", xp_reward=10))
        self.learners(1)
        self.user("viewer")
        rival = max(self.fill(), key=lambda item: item.target_xp)
        client = self.client_for("viewer")
        with patch.object(application, "utcnow", return_value=application.as_utc(self.week.ends_at) - timedelta(minutes=1)):
            html = client.get(f"/leaderboard/rival/{rival.id}").get_data(as_text=True)
        self.assertIn("Simulated rival", html)
        self.assertIn(f"@{rival.username}", html)
        self.assertIn(rival.display_name, html)
        self.assertIn(f"<b>{rival.level}</b>", html)
        self.assertIn("first_steps", json.loads(rival.achievements_json), "every rival has its first badge")
        self.assertIn("First Steps", html)
        self.assertIn("Complete your first meaningful learning activity.", html)
        self.assertIn(f"+{db.session.scalars(db.select(application.LeagueRivalEvent.xp).where(application.LeagueRivalEvent.rival_id == rival.id)).first()} XP", html)
        self.assertIn(f"<b>{rival.target_xp}</b>", html, "its weekly XP")
        application.g.pop("_login_user", None)      # Base keeps one app context: no cached viewer
        self.assertEqual(application.app.test_client().get(f"/leaderboard/rival/{rival.id}").status_code, 302,
                         "only for signed-in learners")
        application.g.pop("_login_user", None)
        client = self.client_for("viewer")
        self.assertEqual(client.get("/leaderboard/rival/999999").status_code, 404)
        self.assertEqual(client.get(f"/u/{rival.username}").status_code, 404, "never shown as a registered learner")

    def test_rivals_cannot_log_in(self):
        self.learners(1)
        rival = self.fill()[0]
        for secret in ("correct-horse-battery", "", rival.username):
            client = application.app.test_client()
            client.post("/login", data={"identifier": rival.username, "password": secret})
            self.assertEqual(client.get("/leaderboard").status_code, 302)
        self.assertIsNone(db.session.scalar(db.select(application.User.id).where(
            application.User.username == rival.username)))

    # -- real data and the weekly transition

    def counts(self):
        models = (application.User, application.XPTransaction, application.LearningEvent,
                  application.UserGamificationProfile, application.UserBadge, application.Lesson,
                  application.LeagueEntry, application.LeagueMembership)
        return {model.__name__: db.session.scalar(db.select(db.func.count()).select_from(model)) for model in models}

    def test_rivals_never_touch_real_xp_or_statistics(self):
        alice = self.user("alice", xp=500)
        application.record_learning_event(alice.id, "quiz_completed", "a1", metadata={"correct": True}, xp=10)
        db.session.commit()
        before = self.counts()
        stats = application.learning_stats(alice.id)
        profile = db.session.get(application.UserGamificationProfile, alice.id)
        total = profile.total_xp
        self.fill()
        with application.app.test_request_context():
            application.league_view(alice)
        self.client_for("alice").get(f"/leaderboard/rival/{application.group_rivals(self.week_start, 1, 1)[0].id}")
        self.assertEqual(self.counts(), before)
        self.assertEqual(application.learning_stats(alice.id), stats)
        self.assertEqual(db.session.get(application.UserGamificationProfile, alice.id).total_xp, total)

    def test_the_transition_ranks_with_rivals_then_deletes_them(self):
        # Silver: 3 real learners and 27 rivals, a full group: 10 up, 10 down.
        top, middle, idle = self.learners(3, tier=2)
        self.xp_at(top.id, 2000, self.at(5), "top")
        self.xp_at(middle.id, 60, self.at(5), "middle")
        db.session.commit()
        found = self.fill(tier=2)
        self.assertEqual(len(found), 27)
        everyone = application.group_standings(self.week, 2, 1, application.as_utc(self.week.ends_at))
        expected = {standing.user_id: (rank, outcome) for standing, rank, outcome in leagues.outcomes(everyone, 2)}
        before = self.counts()
        later = application.as_utc(self.week.ends_at) + timedelta(minutes=10)
        self.assertEqual(application.process_due_league_weeks(later), [self.week_start])
        for account in (top, middle, idle):
            entry = db.session.scalar(db.select(application.LeagueEntry).where(
                application.LeagueEntry.user_id == account.id))
            self.assertEqual((entry.final_rank, entry.outcome), expected[account.id], account.username)
            self.assertEqual(entry.group_size, 30)
        self.assertEqual(expected[top.id], (1, "promoted"))
        self.assertEqual(expected[idle.id][1], "demoted", "no XP at all: the bottom of a full group")
        self.assertEqual(db.session.get(application.LeagueMembership, top.id).tier, 3)
        self.assertEqual(db.session.get(application.LeagueMembership, idle.id).tier, 1)
        # The rivals are gone with their events; every real row is still there.
        self.assertEqual(db.session.scalar(db.select(db.func.count(application.LeagueRival.id))), 0)
        self.assertEqual(db.session.scalar(db.select(db.func.count(application.LeagueRivalEvent.id))), 0)
        self.assertEqual(self.counts(), before)
        self.assertEqual(application.purge_ended_rivals(), 0, "cleanup is safe to run again")
        viewer = self.client_for(top.username)
        self.assertEqual(viewer.get(f"/leaderboard/rival/{found[0].id}").status_code, 404, "ended profiles are gone")

    def test_cleanup_keeps_the_rivals_of_a_week_that_is_still_running(self):
        self.learners(2)
        self.fill()
        old_start = self.week_start - timedelta(days=7)
        db.session.add(application.LeagueWeek(week_start=old_start, starts_at=self.at(-168), ends_at=self.at(0),
                                              status="closed"))
        db.session.add(application.LeagueRival(
            week_start=old_start, tier=1, group_number=1, seat=1, username="left.over", display_name="Left O.",
            avatar_id="scout", level=3, behaviour="casual", seed=1, baseline_xp=60, target_xp=10))
        db.session.commit()
        self.assertEqual(application.purge_ended_rivals(), 1)
        self.assertEqual(application.purge_ended_rivals(), 0)
        self.assertEqual(len(application.group_rivals(self.week_start, 1, 1)), 28)

    def test_calibration_reads_last_weeks_real_results(self):
        old_start = self.week_start - timedelta(days=7)
        db.session.add(application.LeagueWeek(week_start=old_start, starts_at=self.at(-168), ends_at=self.at(0),
                                              status="closed"))
        for index, xp in enumerate((40, 90, 100, 120, 300)):
            account = self.user(f"old{index}")
            db.session.add(application.LeagueEntry(week_start=old_start, user_id=account.id, tier=1, group_number=1,
                                                   joined_at=self.at(-100), final_xp=xp, final_rank=index + 1,
                                                   outcome="stayed"))
        db.session.commit()
        self.assertEqual(application.rival_baseline(self.week_start, 1), 100)
        self.assertEqual(application.rival_baseline(self.week_start, 2), 72, "no history in Silver: the default (60 x 1.2)")

    def test_switched_off_there_are_no_rivals(self):
        application.app.config.update(LEAGUE_RIVALS=False)
        self.learners(3)
        self.assertEqual(self.fill(), [])
        self.assertEqual(len(application.group_standings(self.week, 1, 1)), 3)


if __name__ == "__main__":
    unittest.main()

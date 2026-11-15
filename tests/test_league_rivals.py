"""Simulated league rivals (learnova/gamification/rivals.py and the league code in app.py).

The rules (counts, profiles, schedules, limits, names) are tested as pure functions; the
league integration on a real database: filling groups through the real join, the page, the
rival profile, the weekly transition and the cleanup.
"""

import json
import re
import unittest
from collections import Counter
from datetime import datetime, timedelta
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

    WEEK_END = MONDAY + timedelta(days=7)

    def pace(self, seed, behaviour, target, start, until, earned=0):
        return rivals.advance(seed=seed, behaviour=behaviour, earned=earned, target=target,
                              after=rivals.slot_floor(start), until=until, week_end=self.WEEK_END, zone=BERLIN)

    def test_a_rival_starts_at_zero_and_earns_only_in_slots_that_have_passed(self):
        start = self.MONDAY + timedelta(days=2, hours=15)          # a seat filled on Wednesday
        self.assertEqual(self.pace(11, "strong", 900, start, start), [], "nothing before any time passed")
        events = self.pace(11, "strong", 900, start, start + timedelta(hours=6))
        self.assertTrue(events, "six waking hours: it learned")
        for event in events:
            self.assertGreaterEqual(event.at, start, "nothing from before its seat existed")
            self.assertLess(event.at, start + timedelta(hours=6))
            self.assertIn(event.kind, rivals.EVENT_KINDS)
            self.assertTrue(rivals.EVENT_XP_BOUNDS[0] <= event.xp <= rivals.EVENT_XP_BOUNDS[1], "never a jump")

    def test_pacing_in_steps_equals_pacing_at_once_and_never_goes_down(self):
        start, end = self.MONDAY, self.WEEK_END
        whole = self.pace(5, "consistent", 400, start, end)
        stepped, earned, after = [], 0, start
        for hours in range(6, 24 * 7 + 1, 6):
            until = start + timedelta(hours=hours)
            new = self.pace(5, "consistent", 400, after, until, earned)
            stepped += new
            earned += sum(event.xp for event in new)
            after = rivals.slot_floor(until)
        self.assertEqual(stepped, whole, "the same slots, the same events, however often the page is opened")
        self.assertTrue(all(event.xp > 0 for event in whole))

    def test_a_rival_reaches_about_its_target_within_the_daily_limit(self):
        limits = rivals.Limits()
        for behaviour in rivals.BEHAVIOUR_KEYS:
            for target in ((40, 300) if behaviour == "casual" else (40, 300, 1500)):
                events = self.pace(hash((behaviour, target)) % 10_000, behaviour, target, self.MONDAY, self.WEEK_END)
                total = sum(event.xp for event in events)
                self.assertLessEqual(total, int(target * rivals.KEEP_GOING_LIMIT), "light sessions past the target only")
                self.assertGreaterEqual(total, target * 0.8, (behaviour, target, total))
                daily = Counter()
                for event in events:
                    daily[event.at.astimezone(BERLIN).date()] += event.xp
                self.assertLessEqual(max(daily.values(), default=0), limits.max_daily_xp)

    def test_a_rival_past_its_target_keeps_learning_lightly_then_stops(self):
        start = self.MONDAY + timedelta(days=1)
        events = self.pace(21, "strong", 100, start, start + timedelta(days=5), earned=100)
        self.assertTrue(events, "the board does not freeze")
        self.assertTrue(all(rivals.KEEP_GOING_XP[0] <= event.xp <= rivals.KEEP_GOING_XP[1] for event in events))
        self.assertLessEqual(sum(event.xp for event in events), 15, "at most 15 % above the target")
        self.assertEqual(self.pace(21, "strong", 100, start, self.WEEK_END, earned=115), [], "then it stops")

    def test_profiles_learn_at_their_own_hours(self):
        casual = self.pace(3, "casual", 300, self.MONDAY, self.WEEK_END)
        strong = self.pace(3, "strong", 300, self.MONDAY, self.WEEK_END)
        self.assertLess(len({event.at.astimezone(BERLIN).date() for event in casual}),
                        len({event.at.astimezone(BERLIN).date() for event in strong}) + 1)
        for event in casual:
            local = event.at.astimezone(BERLIN)
            first = rivals.WAKING_HOURS["casual"][0] + (2 if local.weekday() >= 5 else 0)
            self.assertTrue(first <= local.hour < rivals.WAKING_HOURS["casual"][1], local)

    def test_the_rivals_follow_the_groups_pace(self):
        start, end = self.MONDAY, self.WEEK_END
        day3 = start + timedelta(days=3)
        slow = rivals.reference_xp([30], day3, start, end, 60)
        fast = rivals.reference_xp([900], day3, start, end, 60)
        self.assertAlmostEqual(slow, 70, delta=1, msg="30 XP in 3 days: on course for 70")
        self.assertAlmostEqual(fast, 2100, delta=1)
        self.assertEqual(rivals.reference_xp([], day3, start, end, 60), 60, "nobody learned yet: the baseline")
        early = rivals.reference_xp([500], start + timedelta(hours=2), start, end, 60)
        self.assertLess(early, 2000, "one early burst is not projected over the week (14000)")
        self.assertGreaterEqual(early, 500, "never below what the learner already has")
        for role in ("pacesetter", "chaser", *rivals.BEHAVIOUR_KEYS):
            self.assertGreater(rivals.target_for(role, 9, fast), rivals.target_for(role, 9, slow), role)
        self.assertLessEqual(rivals.target_for("pacesetter", 9, 100_000), rivals.Limits().max_weekly_xp)

    def test_the_three_strongest_aim_above_the_pace_and_two_level_with_it(self):
        seats = [(seat, rivals.seed_for("2026-11-09", 1, 1, seat), rivals.behaviour_for("2026-11-09", 1, 1, seat))
                 for seat in range(1, 30)]
        role = rivals.roles(seats)
        self.assertEqual(Counter(role.values())["pacesetter"], 3)
        self.assertEqual(Counter(role.values())["chaser"], 2)
        strongest = sorted(seats, key=lambda item: (rivals.STRENGTH[item[2]], item[1]))[:3]
        self.assertEqual({role[seat] for seat, _seed, _behaviour in strongest}, {"pacesetter"})
        for seat, seed, _behaviour in seats:
            target = rivals.target_for(role[seat], seed, 1000)
            if role[seat] == "pacesetter":
                self.assertGreater(target, 1000, "a steady learner does not reach the top 3")
                self.assertLessEqual(target, 1220)
            elif role[seat] == "chaser":
                self.assertTrue(900 <= target <= 1020)
            else:
                self.assertLess(target, 1000)
        self.assertEqual(rivals.roles(seats[:4]), rivals.roles(seats[:4]), "stable")
        self.assertEqual(Counter(rivals.roles(seats[:4]).values())["pacesetter"], 3, "a small group: 3 and 1")

    def test_a_strong_last_day_beats_the_pacesetters(self):
        """Steady 80 XP a day ends behind the top 3; the same week plus a 600 XP sprint on
        Sunday afternoon wins, because the rivals answer a day's pace only the next day."""

        def week(sprint):
            seats = [(seat, rivals.seed_for("2026-11-09", 1, 1, seat), rivals.behaviour_for("2026-11-09", 1, 1, seat))
                     for seat in range(1, 30)]
            role = rivals.roles(seats)
            state = {seat: [rivals.slot_floor(self.MONDAY + timedelta(hours=10)), 0] for seat, _s, _b in seats}
            me, at_day_start, now = 0.0, {}, self.MONDAY + timedelta(hours=10)
            while now < self.WEEK_END:
                now = min(self.WEEK_END, now + timedelta(minutes=30))
                local = now.astimezone(BERLIN)
                day = max(self.MONDAY, datetime.combine(local.date(), datetime.min.time(), BERLIN))
                at_day_start.setdefault(day, me)
                if 16 <= local.hour < 19:
                    me += 80 / 6 + (sprint / 6 if local.weekday() == 6 else 0)
                reference = rivals.reference_xp([int(at_day_start[day])], day, self.MONDAY, self.WEEK_END, 60)
                for seat, seed, behaviour in seats:
                    after, earned = state[seat]
                    new = rivals.advance(seed=seed, behaviour=behaviour, earned=earned,
                                         target=rivals.target_for(role[seat], seed, reference), after=after, until=now,
                                         week_end=self.WEEK_END, zone=BERLIN)
                    state[seat] = [max(after, rivals.slot_floor(now)), earned + sum(event.xp for event in new)]
            return 1 + sum(1 for _after, earned in state.values() if earned > me)

        self.assertEqual(week(0), 4, "steady: just behind the 3 pacesetters")
        self.assertEqual(week(600), 1, "a strong day wins")

    def test_calibration_uses_last_weeks_median_or_the_default(self):
        self.assertEqual(rivals.baseline(1, [10, 50, 80, 90, 200]), 80)
        self.assertEqual(rivals.baseline(1, [10, 50, 80, 90]), 60, "too few results: the default")
        self.assertEqual(rivals.baseline(3, []), 84, "the default grows with the league")
        self.assertEqual(rivals.baseline(1, [0] * 9), 20, "bounded below")
        self.assertEqual(rivals.baseline(1, [5000] * 9), 400, "bounded above")

    def test_a_plan_is_reproducible_and_seeded(self):
        first = rivals.plan_rival(self.MONDAY, 2, 3, 7)
        self.assertEqual(first, rivals.plan_rival(self.MONDAY, 2, 3, 7))
        renamed = rivals.plan_rival(self.MONDAY, 2, 3, 7, name_attempt=1)
        self.assertNotEqual(renamed.username, first.username)
        self.assertEqual((renamed.behaviour, renamed.level, renamed.seed), (first.behaviour, first.level, first.seed),
                         "a new name changes nothing else")

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

    def fill(self, tier=1, group=1, hours=0):
        """Fill a group as if its first learner joined `hours` after the week started."""
        application.sync_group_rivals(self.week_start, tier, group, self.at(hours))
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
        self.assertTrue(all(item.weekly_xp == 0 for item in application.group_standings(self.week, 1, 1, self.at(0))))
        mid = {item.user_id: item.weekly_xp for item in application.group_standings(self.week, 1, 1, self.at(84))}
        end = {item.user_id: item.weekly_xp for item in application.group_standings(
            self.week, 1, 1, application.as_utc(self.week.ends_at))}
        for rival in found:
            self.assertLessEqual(mid[-rival.id], end[-rival.id], "never goes down")
            total = db.session.scalar(db.select(db.func.sum(application.LeagueRivalEvent.xp)).where(
                application.LeagueRivalEvent.rival_id == rival.id)) or 0
            self.assertEqual(end[-rival.id], total, "its score is its events")
        self.assertGreater(sum(end.values()), sum(mid.values()))

    def test_rivals_created_mid_week_start_at_zero_and_grow_as_time_passes(self):
        self.learners(1)
        found = self.fill(hours=80)                # the group got its rivals on Thursday
        scores = []
        for hours in (80, 90, 110, 160):
            standings = application.group_standings(self.week, 1, 1, self.at(hours))
            scores.append(sum(item.weekly_xp for item in standings if item.user_id < 0))
        self.assertEqual(scores[0], 0, "a new league: every rival at 0")
        self.assertEqual(scores, sorted(scores))
        self.assertGreater(scores[-1], scores[1])
        first = db.session.scalar(db.select(db.func.min(application.LeagueRivalEvent.occurs_at)).where(
            application.LeagueRivalEvent.rival_id.in_([rival.id for rival in found])))
        self.assertGreaterEqual(application.as_utc(first), self.at(80), "nothing before the seat existed")

    def test_pacing_twice_in_the_same_slot_adds_nothing(self):
        self.learners(1)
        self.fill()
        application.group_standings(self.week, 1, 1, self.at(60))
        db.session.commit()
        count = db.session.scalar(db.select(db.func.count(application.LeagueRivalEvent.id)))
        for _ in range(3):
            self.assertEqual(application.advance_group_rivals(self.week, 1, 1, self.at(60)), 0)
        self.assertEqual(db.session.scalar(db.select(db.func.count(application.LeagueRivalEvent.id))), count)

    def test_a_fast_group_meets_faster_rivals_than_a_slow_one(self):
        fast, slow = self.learners(1, group=1)[0], self.learners(1, group=2)[0]
        self.xp_at(fast.id, 700, self.at(20), "fast")
        self.xp_at(slow.id, 20, self.at(20), "slow")
        db.session.commit()
        self.fill(group=1)
        self.fill(group=2)

        def rivals_total(group):
            standings = application.group_standings(self.week, 1, group, self.at(100))
            return sum(item.weekly_xp for item in standings if item.user_id < 0)

        self.assertGreater(rivals_total(1), 3 * rivals_total(2))
        targets = sorted((rival.target_xp for rival in application.group_rivals(self.week_start, 1, 1)), reverse=True)
        self.assertGreater(targets[2], 700, "the top 3 aim above the fast learner's pace")

    def test_a_rival_from_before_pacing_keeps_its_xp_and_is_paced_from_now_on(self):
        self.learners(1)
        db.session.add(application.LeagueRival(
            week_start=self.week_start, tier=1, group_number=1, seat=1, username="old.plan", display_name="Old P.",
            avatar_id="scout", level=3, behaviour="strong", seed=1, baseline_xp=60, target_xp=300))
        db.session.flush()
        old = application.group_rivals(self.week_start, 1, 1)[0]
        db.session.add_all([application.LeagueRivalEvent(rival_id=old.id, occurs_at=self.at(5), kind="quiz", xp=30),
                            application.LeagueRivalEvent(rival_id=old.id, occurs_at=self.at(150), kind="quiz", xp=99)])
        db.session.commit()

        def events():
            return db.session.scalars(db.select(application.LeagueRivalEvent).where(
                application.LeagueRivalEvent.rival_id == old.id).order_by(application.LeagueRivalEvent.occurs_at)).all()

        application.advance_group_rivals(self.week, 1, 1, self.at(100))
        db.session.commit()
        self.assertEqual([(event.xp, application.as_utc(event.occurs_at)) for event in events()], [(30, self.at(5))],
                         "what it earned stays; the planned future session is gone")
        self.assertEqual(application.as_utc(db.session.get(application.LeagueRival, old.id).advanced_until),
                         application.league_rivals.slot_floor(self.at(100)))
        application.advance_group_rivals(self.week, 1, 1, self.at(130))
        db.session.commit()
        later = events()
        self.assertGreater(len(later), 1, "it learns live from now on")
        self.assertTrue(all(application.as_utc(event.occurs_at) >= self.at(100) for event in later[1:]))

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
        self.assertGreater(ranks[1], 1, "the rivals overtake a learner who stopped")
        self.assertGreaterEqual(ranks[2], ranks[1], "and the learner never climbs back without learning")
        # Studying more than any rival can earn in a week puts the learner on top.
        best = rivals.Limits().max_weekly_xp + 1
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
        found = self.fill()
        client = self.client_for("viewer")
        with patch.object(application, "utcnow", return_value=application.as_utc(self.week.ends_at) - timedelta(minutes=1)):
            client.get(f"/leaderboard/rival/{found[0].id}")       # paces the whole group
            totals = {rival.id: db.session.scalar(db.select(db.func.coalesce(db.func.sum(
                application.LeagueRivalEvent.xp), 0)).where(application.LeagueRivalEvent.rival_id == rival.id))
                for rival in found}
            rival = max(found, key=lambda item: totals[item.id])
            html = client.get(f"/leaderboard/rival/{rival.id}").get_data(as_text=True)
        self.assertIn("Simulated rival", html)
        self.assertIn(f"@{rival.username}", html)
        self.assertIn(rival.display_name, html)
        self.assertIn(f"<b>{rival.level}</b>", html)
        self.assertIn("first_steps", json.loads(rival.achievements_json), "every rival has its first badge")
        self.assertIn("First Steps", html)
        self.assertIn("Complete your first meaningful learning activity.", html)
        self.assertIn(f"+{db.session.scalars(db.select(application.LeagueRivalEvent.xp).where(application.LeagueRivalEvent.rival_id == rival.id)).first()} XP", html)
        self.assertIn(f"<b>{totals[rival.id]}</b>", html, "its weekly XP: the sum of its events")
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
        # Ranked: the 2 learners with XP and the 27 rivals. The seated member with no XP all
        # week is not ranked (enroll_league_members): away is never demoted.
        ranked = application.ranked_part(everyone)
        self.assertEqual(len(ranked), 29)
        expected = {standing.user_id: (rank, outcome) for standing, rank, outcome in leagues.outcomes(ranked, 2)}
        expected[idle.id] = (None, "inactive")
        before = self.counts()
        later = application.as_utc(self.week.ends_at) + timedelta(minutes=10)
        self.assertEqual(application.process_due_league_weeks(later), [self.week_start])
        for account in (top, middle, idle):
            entry = db.session.scalar(db.select(application.LeagueEntry).where(
                application.LeagueEntry.user_id == account.id))
            self.assertEqual((entry.final_rank, entry.outcome), expected[account.id], account.username)
            self.assertEqual(entry.group_size, 29)
        # 2000 XP on Monday, then nothing: the 3 pacesetters aim just above that pace and pass.
        self.assertEqual(expected[top.id], (4, "promoted"))
        self.assertEqual(db.session.get(application.LeagueMembership, top.id).tier, 3)
        self.assertEqual(db.session.get(application.LeagueMembership, idle.id).tier, 2, "no XP all week: league kept")
        idle_entry = db.session.scalar(db.select(application.LeagueEntry).where(
            application.LeagueEntry.user_id == idle.id))
        self.assertIsNotNone(idle_entry.result_seen_at, "no result card for a week not taken part in")
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



class MembersBeforeRivalsTests(Base):
    """Everyone of a league who learned recently is seated before rivals (owner request
    2026-11-15: classmates in the same league were missing while rivals filled the group)."""

    def setUp(self):
        super().setUp()
        application.app.config.update(LEAGUE_RIVALS=True, LEAGUE_MEMBER_ACTIVE_DAYS=28)
        starts, ends, self.week_start = application.league_week_bounds()
        self.week = application.LeagueWeek(week_start=self.week_start, starts_at=starts, ends_at=ends, status="open")
        db.session.add(self.week)
        db.session.commit()

    def at(self, hours_from_start):
        return application.as_utc(self.week.starts_at) + timedelta(hours=hours_from_start)

    def member(self, name, *, tier=1, learned_hours_from_start=-48, membership=True, **fields):
        """A learner of the league who learned before this week (no XP this week)."""
        account = self.user(name, **fields)
        if membership:
            db.session.add(application.LeagueMembership(user_id=account.id, tier=tier))
        self.xp(account.id, 20, self.at(learned_hours_from_start), f"before:{name}")
        return account

    def xp(self, user_id, amount, moment, key):
        db.session.add(application.XPTransaction(
            user_id=user_id, event_type="test", amount=amount, reason="test", idempotency_key=key, created_at=moment))
        db.session.commit()

    def learn(self, account, key):
        application.record_learning_event(account.id, "quiz_completed", key, metadata={"correct": True}, xp=10)
        db.session.commit()

    def entries(self, tier=1, group=1):
        return set(db.session.scalars(db.select(application.LeagueEntry.user_id).where(
            application.LeagueEntry.week_start == self.week_start, application.LeagueEntry.tier == tier,
            application.LeagueEntry.group_number == group)).all())

    def view(self, account):
        with application.app.test_request_context():
            return application.league_view(account)

    def test_everyone_of_the_league_is_seated_before_rivals(self):
        friends = [self.member("lena"), self.member("jonas"), self.member("emre", membership=False)]
        others = [self.member("silver.kid", tier=2), self.member("opted.out", leaderboard_enabled=False),
                  self.member("long.gone", learned_hours_from_start=-24 * 40)]
        me = self.user("me")
        self.learn(me, "m1")
        seated = self.entries()
        self.assertEqual(seated, {me.id} | {friend.id for friend in friends},
                         "the 3 classmates are in the group; another league, opted out and long gone are not")
        self.assertFalse(seated & {other.id for other in others})
        self.assertEqual(len(application.group_rivals(self.week_start, 1, 1)), 26, "rivals only fill what is left")
        self.assertEqual(db.session.get(application.LeagueMembership, friends[2].id).tier, 1, "never joined: Bronze")
        view = self.view(me)
        rows = {row["username"]: row for row in view["standings"]}
        for name in ("lena", "jonas", "emre"):
            self.assertEqual((rows[name]["rank"], rows[name]["zone"]), (None, "waiting"), name)
        self.assertEqual(view["size"], 27, "ranked: me and 26 rivals")
        self.assertEqual(len(view["standings"]), 30)
        self.assertEqual({row["username"] for row in view["standings"][-3:]}, {"lena", "jonas", "emre"},
                         "not started: listed below the ranking")
        html = self.client_for("me").get("/leaderboard").get_data(as_text=True)
        self.assertEqual(html.count('class="league-waiting-chip"'), 3)
        self.assertEqual(html.count("league-divider-waiting"), 1)

    def test_a_seated_member_enters_the_ranking_with_their_first_xp(self):
        lena = self.member("lena")
        me = self.user("me")
        self.learn(me, "m1")
        rivals_before = [rival.id for rival in application.group_rivals(self.week_start, 1, 1)]
        self.learn(lena, "l1")
        self.assertEqual(self.entries(), {me.id, lena.id}, "no second entry")
        self.assertEqual([rival.id for rival in application.group_rivals(self.week_start, 1, 1)], rivals_before)
        row = next(row for row in self.view(me)["standings"] if row["username"] == "lena")
        self.assertIsNotNone(row["rank"])
        self.assertNotEqual(row["zone"], "waiting")

    def test_a_viewer_who_has_not_started_sees_their_league(self):
        lena, jonas = self.member("lena"), self.member("jonas")
        view = self.view(lena)
        self.assertTrue(view["joined"])
        self.assertEqual(self.entries(), {lena.id, jonas.id})
        self.assertEqual((view["me"]["rank"], view["me"]["zone"]), (None, "waiting"))
        self.assertEqual(len(view["standings"]), 30, "2 members and 28 rivals")
        page = self.client_for("lena").get("/leaderboard")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Earn XP to enter this week\u2019s ranking", page.get_data(as_text=True))

    def test_not_started_members_keep_their_league_even_when_they_leave(self):
        active = self.user("active.one")
        waiting, leaver = self.member("waiting.one", tier=2), self.member("leaver.one", tier=2)
        db.session.add(application.LeagueMembership(user_id=active.id, tier=2))
        self.xp(active.id, 50, self.at(5), "active")
        application.ensure_league_entry(active.id)
        db.session.commit()
        self.assertEqual(self.entries(tier=2), {active.id, waiting.id, leaver.id})
        application.leave_leagues(db.session.get(application.User, leaver.id))
        db.session.commit()
        left = db.session.scalar(db.select(application.LeagueEntry).where(
            application.LeagueEntry.user_id == leaver.id))
        self.assertEqual((left.outcome, left.final_rank, left.tier_after), ("withdrawn", None, 2))
        self.assertEqual(application.group_withdrawals(self.week_start, 2, 1)["withdrawn"], 0,
                         "a leaver who never started does not change anyone's zones")
        later = application.as_utc(self.week.ends_at) + timedelta(minutes=10)
        self.assertEqual(application.process_due_league_weeks(later), [self.week_start])
        entry = db.session.scalar(db.select(application.LeagueEntry).where(
            application.LeagueEntry.user_id == waiting.id))
        self.assertEqual((entry.outcome, entry.final_rank, entry.tier_after), ("inactive", None, 2))
        self.assertIsNotNone(entry.result_seen_at)
        for account in (waiting, leaver):
            self.assertEqual(db.session.get(application.LeagueMembership, account.id).tier, 2, account.username)
        self.assertEqual(application.league_history(waiting.id), [], "a week not taken part in is not history")

    def test_zero_days_seats_only_this_weeks_learners(self):
        application.app.config.update(LEAGUE_MEMBER_ACTIVE_DAYS=0)
        lena = self.member("lena")
        me = self.user("me")
        self.learn(me, "m1")
        self.assertEqual(self.entries(), {me.id})
        self.assertNotIn(lena.id, self.entries())
        self.assertEqual(len(application.group_rivals(self.week_start, 1, 1)), 29)

if __name__ == "__main__":
    unittest.main()

"""Profiles, usernames, avatars, challenges and Learnova Leagues."""

import json
import os
import re
import tempfile
import unittest
import xml.etree.ElementTree as ElementTree
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_profiles_leagues_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.gamification import avatars, challenges, leagues, usernames  # noqa: E402
from learnova.gamification import service as gamification  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
db = application.db


class Base(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, WTF_CSRF_ENABLED=False, FEATURE_GAMIFICATION=True, FEATURE_LEAGUES=True,
            FEATURE_COMMUNITY_LIBRARY=True, FEATURE_COMMUNITY_PUBLISHING=True,
            FEATURE_COMMUNITY_MODERATION=False, FEATURE_PRIVATE_FLASHCARDS=True,
            LEAGUE_PROMOTE=10, LEAGUE_DEMOTE=10, LEAGUE_GROUP_SIZE=30,
            USERNAME_CHANGE_COOLDOWN_DAYS=30, USERNAME_HOLD_DAYS=90,
            COMMUNITY_MODERATORS={"mod.anna"}, PREMIUM_ADMIN_IDENTIFIERS=set(), AI_DIAGNOSTICS_ADMINS=set())
        application.SESSIONS.clear()
        self.context = application.app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        self.context.pop()
        application.app.config.clear()
        application.app.config.update(self.original)

    def user(self, name, xp=0, **fields):
        account = application.User(username=name, email=f"{name}@example.com", **fields)
        account.set_password("correct-horse-battery")
        db.session.add(account)
        db.session.flush()
        if xp:
            db.session.add(application.UserGamificationProfile(user_id=account.id, total_xp=xp))
        db.session.commit()
        return account

    def client_for(self, name):
        client = application.app.test_client()
        response = client.post("/login", data={"identifier": name, "password": "correct-horse-battery"})
        self.assertIn(response.status_code, {200, 302})
        return client

    def xp_at(self, user_id, amount, moment, key):
        db.session.add(application.XPTransaction(
            user_id=user_id, event_type="test", amount=amount, reason="test",
            idempotency_key=key, created_at=moment))


# ------------------------------------------------------------------ usernames


class UsernameRuleTests(unittest.TestCase):
    def test_format_rules(self):
        for bad in ("ab", "x" * 31, "Hello World", "tom.", "a..b", "_lead", "ümlaut"):
            self.assertEqual(usernames.problem(bad), "format", bad)
        for good in ("anna.k", "lena_2009", "max-mustermann", "kai"):
            self.assertIsNone(usernames.problem(good), good)

    def test_impersonation_names_are_reserved(self):
        for name in ("admin", "learnova", "learnova_team", "moderator", "official.help", "admin2", "support"):
            self.assertEqual(usernames.problem(name), "reserved", name)
        self.assertIsNone(usernames.problem("badminton_fan"))

    def test_inappropriate_names_are_refused_without_scunthorpe_errors(self):
        for name in ("sh1t_happens", "arschloch99", "big_ass"):
            self.assertEqual(usernames.problem(name), "inappropriate", name)
        for name in ("classic", "therapist", "nutella99", "kassel", "sussex"):
            self.assertIsNone(usernames.problem(name), name)


class UsernameChangeTests(Base):
    def test_change_updates_login_and_every_display(self):
        alice = self.user("alice")
        client = self.client_for("alice")
        response = client.post("/settings/profile/username",
                               data={"username": "alice.k", "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 302)
        db.session.expire_all()
        self.assertEqual(db.session.get(application.User, alice.id).username, "alice.k")
        self.assertEqual(len(application.UsernameChange.query.all()), 1)
        fresh = application.app.test_client()
        self.assertEqual(fresh.post("/login", data={"identifier": "alice.k", "password": "correct-horse-battery"}
                                    ).status_code, 302)
        with application.app.test_request_context():
            self.assertEqual(application.identity_card(alice.id)["username"], "alice.k")

    def test_password_is_required(self):
        alice = self.user("alice")
        self.client_for("alice").post("/settings/profile/username", data={"username": "bob2", "password": "nope"})
        db.session.expire_all()
        self.assertEqual(db.session.get(application.User, alice.id).username, "alice")

    def test_uniqueness_restrictions_cooldown_and_hold(self):
        alice, bob = self.user("alice"), self.user("bobby")
        with application.app.test_request_context():
            self.assertFalse(application.change_username(alice, "BOBBY")[0])           # taken, case-folded
            self.assertFalse(application.change_username(alice, "admin")[0])           # reserved
            self.assertFalse(application.change_username(alice, "mod.anna")[0])        # moderator allowlist
            self.assertTrue(application.change_username(alice, "alice.new")[0])
            changed, message = application.change_username(alice, "alice.again")      # cooldown
            self.assertFalse(changed)
            self.assertIn("again on", message)
            # Bob cannot pick up the name Alice just released.
            self.assertFalse(application.change_username(bob, "alice")[0])
            alice.username_changed_at = application.utcnow() - timedelta(days=31)
            db.session.commit()
            self.assertTrue(application.change_username(alice, "alice.again")[0])

    def test_held_name_frees_up_after_the_hold(self):
        alice, bob = self.user("alice"), self.user("bobby")
        with application.app.test_request_context():
            application.change_username(alice, "alice.new")
            change = application.UsernameChange.query.one()
            change.changed_at = application.utcnow() - timedelta(days=91)
            db.session.commit()
            self.assertTrue(application.change_username(bob, "alice")[0])


# ------------------------------------------------------------------ avatars


class AvatarAssetTests(unittest.TestCase):
    def test_all_twenty_assets_exist_and_are_safe_svg(self):
        self.assertEqual(len(avatars.AVATARS), 20)
        for avatar in avatars.AVATARS:
            path = ROOT / "static" / avatar.asset
            with self.subTest(avatar=avatar.id):
                self.assertTrue(path.is_file(), path)
                text = path.read_text(encoding="utf-8")
                self.assertLess(len(text.encode("utf-8")), 16_000)
                root = ElementTree.fromstring(text)
                self.assertTrue(root.tag.endswith("svg"))
                self.assertEqual(root.get("viewBox"), "0 0 256 256")
                lowered = text.lower()
                for forbidden in ("<script", "foreignobject", "<image", "javascript:", "@import", " onload=", "<text"):
                    self.assertNotIn(forbidden, lowered)
                self.assertIsNone(re.search(r'href="(?!#)', text), "external reference")
                self.assertIn("<title", lowered)

    def test_assets_are_distinct(self):
        contents = {(ROOT / "static" / avatar.asset).read_bytes() for avatar in avatars.AVATARS}
        self.assertEqual(len(contents), 20)

    def test_schedule_covers_levels_one_to_twenty(self):
        self.assertEqual([item.unlock_level for item in avatars.AVATARS], list(range(1, 21)))
        self.assertEqual(avatars.unlocked_for_level(1), ("explorer",))
        self.assertEqual(len(avatars.unlocked_for_level(20)), 20)
        self.assertEqual(avatars.AVATARS[-1].rarity, "legendary")


class AvatarSelectionTests(Base):
    def test_unlocks_follow_level_and_are_permanent(self):
        alice = self.user("alice")
        application.award_xp(alice.id, "test", 100, "a1")      # level 2
        application.award_xp(alice.id, "test", 100, "a2")
        application.award_xp(alice.id, "test", 60, "a3")       # 260 XP: level 3
        db.session.commit()
        unlocked = {row.avatar_id for row in application.UserAvatarUnlock.query.filter_by(user_id=alice.id)}
        self.assertEqual(unlocked, {"explorer", "scientist", "inventor"})

    def test_select_unlocked_refuse_locked_and_keep_progress(self):
        alice = self.user("alice", xp=300)
        client = self.client_for("alice")
        self.assertEqual(client.post("/api/profile/avatar", json={"avatar": "inventor"}).status_code, 200)
        locked = client.post("/api/profile/avatar", json={"avatar": "founder"})
        self.assertEqual((locked.status_code, locked.get_json()["code"]), (403, "avatar_locked"))
        self.assertEqual(client.post("/api/profile/avatar", json={"avatar": "../etc"}).status_code, 400)
        db.session.expire_all()
        self.assertEqual(db.session.get(application.User, alice.id).avatar_id, "inventor")
        self.assertEqual(db.session.get(application.UserGamificationProfile, alice.id).total_xp, 300)

    def test_a_stale_choice_never_shows_a_locked_avatar(self):
        alice = self.user("alice", avatar_id="founder")
        with application.app.test_request_context():
            self.assertEqual(application.identity_card(alice.id)["avatar"], "explorer")


# ------------------------------------------------------------------ community identity


class CommunityIdentityTests(Base):
    def publish_as(self, name, display="username"):
        account = db.session.scalar(db.select(application.User).where(application.User.username == name))
        public = application.PublicFlashcardSet(
            creator_id=account.id, title="Cells", subject="Biology", status="approved",
            moderation_decision="allow", author_display=display, cards_json="[]", card_count=0)
        db.session.add(public)
        db.session.commit()
        return public

    def library(self):
        return application.app.test_client().get("/api/community/library").get_json()["sets"]

    def test_username_display_shows_avatar_level_and_no_private_data(self):
        self.user("alice", xp=300)
        self.publish_as("alice")
        [item] = self.library()
        identity = item["author_identity"]
        self.assertEqual((identity["username"], identity["level"], identity["premium"]), ("alice", 3, False))
        self.assertTrue(identity["avatar_url"].endswith("/static/avatars/explorer.svg"))
        self.assertNotIn("alice@example.com", json.dumps(item))

    def test_nickname_and_anonymous_sets_never_reveal_the_account(self):
        self.user("alice", xp=300)
        self.publish_as("alice", "anonymous")
        self.assertIsNone(self.library()[0]["author_identity"])

    def test_premium_badge_follows_the_live_entitlement(self):
        alice = self.user("alice")
        self.publish_as("alice")
        entitlement = application.PremiumEntitlement(
            user_id=alice.id, status="active", expires_at=application.utcnow() + timedelta(days=10))
        db.session.add(entitlement)
        db.session.commit()
        self.assertTrue(self.library()[0]["author_identity"]["premium"])
        entitlement.expires_at = application.utcnow() - timedelta(minutes=1)
        db.session.commit()
        self.assertFalse(self.library()[0]["author_identity"]["premium"])
        entitlement.expires_at, entitlement.status = application.utcnow() + timedelta(days=10), "revoked"
        db.session.commit()
        self.assertFalse(self.library()[0]["author_identity"]["premium"])

    def test_a_private_profile_hides_level_and_link(self):
        self.user("alice", xp=300, profile_public=False)
        self.publish_as("alice")
        identity = self.library()[0]["author_identity"]
        self.assertIsNone(identity["level"])
        self.assertIsNone(identity["profile_url"])

    def test_public_profile_respects_privacy_and_never_shows_leagues(self):
        self.user("alice", xp=300)
        self.user("bobby", xp=300, profile_public=False)
        viewer = application.app.test_client()
        public = viewer.get("/u/alice").get_data(as_text=True)
        self.assertIn("alice", public)
        self.assertIn("Lessons completed", public)
        self.assertNotIn("League", public.split("<main", 1)[1])
        private = viewer.get("/u/bobby").get_data(as_text=True)
        self.assertIn("keeps their progress private", private)
        self.assertNotIn("Lessons completed", private)
        self.assertEqual(viewer.get("/u/nobody").status_code, 404)

    def test_preferences_only_change_the_signed_in_account(self):
        alice, bob = self.user("alice"), self.user("bobby")
        self.client_for("alice").post("/settings/profile/preferences", data={"user_id": bob.id})
        db.session.expire_all()
        self.assertFalse(db.session.get(application.User, alice.id).profile_public)
        self.assertTrue(db.session.get(application.User, bob.id).profile_public)


# ------------------------------------------------------------------ challenges


class ChallengeTests(Base):
    def test_generation_is_one_per_difficulty_stable_and_unique(self):
        alice = self.user("alice")
        first = application.ensure_user_missions(alice.id)
        again = application.ensure_user_missions(alice.id)
        db.session.commit()
        self.assertEqual([item.id for item in first], [item.id for item in again])
        for period in ("daily", "weekly"):
            items = [item for item in first if item.period == period]
            self.assertEqual(sorted(item.difficulty for item in items), ["easy", "hard", "medium"])

    def test_daily_resets_at_server_midnight_and_weekly_on_monday(self):
        alice = self.user("alice")
        zone = application.app_zone()
        monday = datetime(2026, 11, 2, 9, 0, tzinfo=zone).astimezone(timezone.utc)
        application.ensure_user_missions(alice.id, monday)
        application.ensure_user_missions(alice.id, monday + timedelta(days=1))
        application.ensure_user_missions(alice.id, monday + timedelta(days=7))
        rows = application.UserMission.query.filter_by(user_id=alice.id).all()
        self.assertEqual(len({row.starts_at for row in rows if row.period == "daily"}), 3)
        self.assertEqual(len({row.starts_at for row in rows if row.period == "weekly"}), 2)

    def test_a_profile_timezone_cannot_move_the_reset(self):
        alice = self.user("alice")
        db.session.add(application.UserGamificationProfile(user_id=alice.id, timezone="Pacific/Kiritimati"))
        db.session.commit()
        moment = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        starts, _ends, _day = challenges.period_bounds(moment, application.app_zone(), "daily")
        application.ensure_user_missions(alice.id, moment)
        rows = application.UserMission.query.filter_by(user_id=alice.id, period="daily").all()
        self.assertEqual({application.as_utc(row.starts_at) for row in rows}, {starts})

    def test_progress_completes_and_rewards_exactly_once(self):
        alice = self.user("alice")
        mission = application.UserMission(
            user_id=alice.id, mission_key="daily_correct_10", period="daily", target=3, reward_xp=15,
            starts_at=application.utcnow() - timedelta(hours=1), expires_at=application.utcnow() + timedelta(hours=5),
            metric="correct_answers", difficulty="easy")
        db.session.add(mission)
        db.session.commit()
        for index in range(6):
            application.record_learning_event(alice.id, "practice_question_completed", f"q{index}",
                                              metadata={"correct": True}, xp=3)
        # A replayed event is not a second answer.
        application.record_learning_event(alice.id, "practice_question_completed", "q0",
                                          metadata={"correct": True}, xp=3)
        db.session.commit()
        db.session.refresh(mission)
        self.assertIsNotNone(mission.completed_at)
        rewards = application.XPTransaction.query.filter_by(idempotency_key=f"mission:{mission.id}").all()
        self.assertEqual([row.amount for row in rewards], [15])

    def test_an_already_claimed_challenge_is_not_paid_again(self):
        alice = self.user("alice")
        mission = application.UserMission(
            user_id=alice.id, mission_key="daily_correct_10", period="daily", target=1, reward_xp=15,
            starts_at=application.utcnow() - timedelta(hours=1), expires_at=application.utcnow() + timedelta(hours=5),
            completed_at=application.utcnow())
        db.session.add(mission)
        db.session.commit()
        application.record_learning_event(alice.id, "practice_question_completed", "q1",
                                          metadata={"correct": True}, xp=3)
        db.session.commit()
        self.assertEqual(application.XPTransaction.query.filter_by(
            idempotency_key=f"mission:{mission.id}").count(), 0)

    def test_challenges_count_learning_not_clicks(self):
        definition = challenges.CHALLENGE_BY_KEY["daily_correct_10"]
        self.assertEqual(challenges.contribution(definition, "practice_question_completed", {"correct": True}), 1)
        self.assertIsNone(challenges.contribution(definition, "practice_question_completed", {"correct": False}))
        hints = challenges.CHALLENGE_BY_KEY["daily_no_hints_1"]
        self.assertEqual(challenges.contribution(hints, "quiz_completed", {"hints_used": False}), 1)
        self.assertIsNone(challenges.contribution(hints, "quiz_completed", {"hints_used": True}))
        self.assertIsNone(challenges.contribution(hints, "quiz_completed", {}))
        days = challenges.CHALLENGE_BY_KEY["weekly_active_days_4"]
        self.assertEqual(challenges.contribution(days, "flashcard_reviewed", {}, local_date=date(2026, 11, 3)),
                         "2026-11-03")

    def test_distinct_days_count_once_per_day(self):
        alice = self.user("alice")
        mission = application.UserMission(
            user_id=alice.id, mission_key="weekly_active_days_4", period="weekly", target=4, reward_xp=60,
            starts_at=application.utcnow() - timedelta(hours=1), expires_at=application.utcnow() + timedelta(days=3),
            metric="active_days", difficulty="easy")
        db.session.add(mission)
        db.session.commit()
        for index in range(5):
            application.record_learning_event(alice.id, "flashcard_reviewed", f"f{index}", metadata={"correct": True}, xp=2)
        db.session.commit()
        db.session.refresh(mission)
        self.assertEqual(mission.progress, 1)

    def test_selection_prefers_features_the_learner_uses(self):
        picked = challenges.choose("daily", 7, date(2026, 11, 3), recent_events=set())
        self.assertTrue(all(not item.requires for item in picked))


# ------------------------------------------------------------------ league rules


class LeagueRuleTests(unittest.TestCase):
    T0 = datetime(2026, 11, 3, 10, tzinfo=timezone.utc)

    def test_ties_break_by_who_got_there_first_then_id(self):
        order = leagues.ranked([
            leagues.Standing(3, 50, self.T0 + timedelta(hours=2)),
            leagues.Standing(2, 50, self.T0),
            leagues.Standing(1, 50, self.T0 + timedelta(hours=2)),
            leagues.Standing(4, 80, self.T0 + timedelta(hours=5)),
        ])
        self.assertEqual([item.user_id for item in order], [4, 2, 1, 3])

    def test_zone_sizes_for_full_small_and_edge_leagues(self):
        self.assertEqual(leagues.zone_sizes(25, 3), (10, 10))
        self.assertEqual(leagues.zone_sizes(12, 3), (4, 4))
        self.assertEqual(leagues.zone_sizes(2, 3), (1, 0))
        self.assertEqual(leagues.zone_sizes(1, 3), (0, 0))
        self.assertEqual(leagues.zone_sizes(25, leagues.HIGHEST), (0, 10))
        self.assertEqual(leagues.zone_sizes(25, leagues.LOWEST), (10, 0))
        self.assertEqual(leagues.zone_sizes(25, 3, promote=3, demote=5), (3, 5))

    def test_each_learner_gets_one_outcome_and_zero_xp_never_promotes(self):
        standings = [leagues.Standing(index, 0, None) for index in range(1, 4)]
        results = leagues.outcomes(standings, 3)
        self.assertEqual(len({item.user_id for item, _rank, _outcome in results}), 3)
        self.assertNotIn("promoted", [outcome for _item, _rank, outcome in results])
        mixed = leagues.outcomes([leagues.Standing(1, 30, self.T0), leagues.Standing(2, 10, self.T0),
                                  leagues.Standing(3, 5, self.T0)], 3)
        self.assertEqual([outcome for _item, _rank, outcome in mixed], ["promoted", "stayed", "demoted"])
        self.assertEqual(leagues.tier_after(leagues.HIGHEST, "promoted"), leagues.HIGHEST)
        self.assertEqual(leagues.tier_after(leagues.LOWEST, "demoted"), leagues.LOWEST)


# ------------------------------------------------------------------ league integration


class LeagueTests(Base):
    def past_week(self, weeks_ago=1):
        starts, ends, week_start = application.league_week_bounds()
        delta = timedelta(days=7 * weeks_ago)
        start_date = week_start - delta
        row = application.LeagueWeek(week_start=start_date, starts_at=starts - delta, ends_at=ends - delta, status="open")
        db.session.add(row)
        db.session.commit()
        return row

    def enter(self, week, user, tier, xp, group=1, offset_minutes=0):
        db.session.merge(application.LeagueMembership(user_id=user.id, tier=tier))
        db.session.add(application.LeagueEntry(week_start=week.week_start, user_id=user.id, tier=tier,
                                               group_number=group, joined_at=application.as_utc(week.starts_at)))
        if xp:
            self.xp_at(user.id, xp, application.as_utc(week.starts_at) + timedelta(hours=1, minutes=offset_minutes),
                       f"w{week.week_start}:{user.id}")
        db.session.commit()

    def test_first_xp_of_the_week_enters_bronze_and_weekly_xp_is_separate(self):
        alice = self.user("alice", xp=5000)
        application.award_xp(alice.id, "test", 20, "first")
        db.session.commit()
        entry = application.LeagueEntry.query.filter_by(user_id=alice.id).one()
        self.assertEqual(entry.tier, leagues.LOWEST)
        with application.app.test_request_context():
            view = application.league_view(alice)
        self.assertEqual(view["me"]["weekly_xp"], 20)
        self.assertEqual(db.session.get(application.UserGamificationProfile, alice.id).total_xp, 5020)

    def test_transition_promotes_demotes_and_never_touches_lifetime_xp(self):
        week = self.past_week()
        silver = [self.user(f"silver{index:02d}", xp=1000) for index in range(12)]
        for index, account in enumerate(silver):
            self.enter(week, account, 2, xp=10 * (index + 1))
        done = application.process_due_league_weeks()
        self.assertEqual(done, [week.week_start])
        tiers = {account.username: db.session.get(application.LeagueMembership, account.id).tier for account in silver}
        promoted = [name for name, tier in tiers.items() if tier == 3]
        demoted = [name for name, tier in tiers.items() if tier == 1]
        self.assertEqual(sorted(promoted), ["silver08", "silver09", "silver10", "silver11"])
        self.assertEqual(sorted(demoted), ["silver00", "silver01", "silver02", "silver03"])
        self.assertTrue(all(db.session.get(application.UserGamificationProfile, account.id).total_xp == 1000
                            for account in silver))

    def test_bronze_has_no_demotion_and_legendary_no_promotion(self):
        week = self.past_week()
        bronze = [self.user(f"b{index:02d}") for index in range(5)]
        legend = [self.user(f"l{index:02d}") for index in range(5)]
        for index, account in enumerate(bronze):
            self.enter(week, account, leagues.LOWEST, xp=index + 1, group=1)
        for index, account in enumerate(legend):
            self.enter(week, account, leagues.HIGHEST, xp=index + 1, group=1)
        application.process_due_league_weeks()
        self.assertFalse(any(db.session.get(application.LeagueMembership, a.id).tier < leagues.LOWEST for a in bronze))
        self.assertEqual(sorted(db.session.get(application.LeagueMembership, a.id).tier for a in bronze),
                         [1, 1, 1, 1, 2])
        self.assertEqual(sorted(db.session.get(application.LeagueMembership, a.id).tier for a in legend),
                         [6, 7, 7, 7, 7])

    def test_transitions_run_exactly_once(self):
        week = self.past_week()
        accounts = [self.user(f"u{index}") for index in range(3)]
        for index, account in enumerate(accounts):
            self.enter(week, account, 2, xp=index + 1)
        self.assertEqual(application.process_due_league_weeks(), [week.week_start])
        before = {a.id: db.session.get(application.LeagueMembership, a.id).tier for a in accounts}
        self.assertEqual(application.process_due_league_weeks(), [])
        self.assertEqual(before, {a.id: db.session.get(application.LeagueMembership, a.id).tier for a in accounts})

    def test_a_week_claimed_elsewhere_is_left_alone(self):
        week = self.past_week()
        account = self.user("solo")
        self.enter(week, account, 2, xp=5)
        week.status = "processing"
        db.session.commit()
        self.assertEqual(application.process_due_league_weeks(), [])

    def test_no_new_entry_until_last_week_is_processed(self):
        self.past_week()
        alice = self.user("alice")
        application.award_xp(alice.id, "test", 20, "x")
        db.session.commit()
        self.assertEqual(application.LeagueEntry.query.filter_by(user_id=alice.id).count(), 0)
        with application.app.test_request_context():
            view = application.league_view(alice)      # processes, then joins with this week's XP
        self.assertTrue(view["joined"])
        self.assertEqual(view["me"]["weekly_xp"], 20)

    def test_inactive_learners_are_not_ranked_or_demoted(self):
        week = self.past_week()
        active, idle = self.user("active"), self.user("idle")
        self.enter(week, active, 3, xp=50)
        db.session.add(application.LeagueMembership(user_id=idle.id, tier=3))
        db.session.commit()
        application.process_due_league_weeks()
        self.assertEqual(db.session.get(application.LeagueMembership, idle.id).tier, 3)

    def test_opt_out_removes_ranking_but_keeps_learning(self):
        alice = self.user("alice", xp=500)
        application.award_xp(alice.id, "test", 20, "x1")
        db.session.commit()
        client = self.client_for("alice")
        client.post("/settings/profile/preferences", data={"profile_public": "on"})   # leagues unticked
        db.session.expire_all()
        self.assertEqual(application.LeagueEntry.query.filter_by(user_id=alice.id).count(), 0)
        application.award_xp(alice.id, "test", 20, "x2")
        application.record_learning_event(alice.id, "practice_question_completed", "q", metadata={"correct": True}, xp=3)
        db.session.commit()
        self.assertEqual(application.LeagueEntry.query.filter_by(user_id=alice.id).count(), 0)
        self.assertEqual(db.session.get(application.UserGamificationProfile, alice.id).total_xp, 543)
        self.assertEqual(client.get("/leaderboard").status_code, 200)

    def test_rejoining_starts_next_week_never_this_one(self):
        alice = self.user("alice")
        client = self.client_for("alice")
        client.post("/settings/profile/preferences", data={})
        client.post("/settings/profile/preferences", data={"leaderboard_enabled": "on"})
        db.session.expire_all()
        membership = db.session.get(application.LeagueMembership, alice.id)
        _starts, ends, week_start = application.league_week_bounds()
        self.assertGreater(membership.rejoin_from, week_start)
        application.award_xp(alice.id, "test", 20, "now")
        db.session.commit()
        self.assertEqual(application.LeagueEntry.query.filter_by(user_id=alice.id).count(), 0)
        later = ends + timedelta(hours=1)
        application.process_due_league_weeks(later)
        application.ensure_league_entry(alice.id, later)
        db.session.commit()
        self.assertEqual(application.LeagueEntry.query.filter_by(user_id=alice.id).count(), 1)

    def test_groups_fill_up_to_the_configured_size(self):
        application.app.config["LEAGUE_GROUP_SIZE"] = 2
        accounts = [self.user(f"g{index}") for index in range(5)]
        for account in accounts:
            application.award_xp(account.id, "test", 10, f"g:{account.id}")
            db.session.commit()
        groups = [row.group_number for row in application.LeagueEntry.query.order_by(application.LeagueEntry.id)]
        self.assertEqual(groups, [1, 1, 2, 2, 3])

    def test_leaderboard_shows_real_people_without_private_data(self):
        alice, bob = self.user("alice"), self.user("bobby")
        db.session.add(application.PremiumEntitlement(
            user_id=bob.id, status="active", expires_at=application.utcnow() + timedelta(days=5)))
        db.session.commit()
        application.award_xp(alice.id, "test", 30, "a")
        application.award_xp(bob.id, "test", 50, "b")
        db.session.commit()
        data = self.client_for("alice").get("/api/leaderboard").get_json()
        self.assertEqual([row["username"] for row in data["standings"]], ["bobby", "alice"])
        self.assertEqual([row["premium"] for row in data["standings"]], [True, False])
        self.assertTrue(data["standings"][1]["me"])
        self.assertEqual(data["me"]["rank"], 2)
        text = json.dumps(data)
        self.assertNotIn("@example.com", text)
        self.assertNotIn("user_id", text)

    def test_users_cannot_write_xp_or_rank(self):
        alice = self.user("alice")
        client = self.client_for("alice")
        for path in ("/api/leaderboard", "/api/challenges"):
            self.assertIn(client.post(path, json={"weekly_xp": 9999, "rank": 1}).status_code, {404, 405})
        self.assertEqual(application.XPTransaction.query.count(), 0)
        del alice


class PageTests(Base):
    def test_pages_render_for_owner(self):
        self.user("alice", xp=800)
        client = self.client_for("alice")
        for path in ("/profile", "/leaderboard", "/settings", "/progress"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
        profile = client.get("/profile").get_data(as_text=True)
        self.assertIn("Avatar collection", profile)
        self.assertEqual(profile.count('class="avatar-tile '), 20)
        self.assertIn("Daily challenges", profile)
        self.assertIn("Weekly challenges", profile)
        settings = client.get("/settings").get_data(as_text=True)
        self.assertIn("Take part in weekly leagues", settings)
        self.assertIn("If you turn leagues off", settings)

    def test_premium_badge_renders_on_html_pages(self):
        alice, bob = self.user("alice"), self.user("bobby")
        for account in (alice, bob):
            db.session.add(application.PremiumEntitlement(
                user_id=account.id, status="active", expires_at=application.utcnow() + timedelta(days=5)))
        db.session.commit()
        application.award_xp(alice.id, "test", 30, "a")
        application.award_xp(bob.id, "test", 50, "b")
        db.session.commit()
        client = self.client_for("alice")
        for path in ("/leaderboard", "/u/bobby", "/profile"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("premium-badge", response.get_data(as_text=True), path)
        free = self.user("carol")
        anonymous = application.app.test_client()
        self.assertNotIn("premium-badge", anonymous.get(f"/u/{free.username}").get_data(as_text=True))

    def test_leagues_can_be_switched_off_entirely(self):
        self.user("alice")
        application.app.config["FEATURE_LEAGUES"] = False
        client = self.client_for("alice")
        self.assertEqual(client.get("/leaderboard").status_code, 404)
        self.assertNotIn("Take part in weekly leagues", client.get("/settings").get_data(as_text=True))

    def test_dark_theme_overrides_exist_for_new_surfaces(self):
        css = (ROOT / "static" / "css" / "profile.css").read_text(encoding="utf-8")
        # Surfaces come from theme tokens, so both themes follow without fixed colours.
        for selector in (".profile-hero", ".challenge-panel", ".avatar-tile", ".league-table", ".avatar-preview"):
            block = css.split(selector + " {", 1)[1].split("}", 1)[0]
            self.assertIn("var(--ln-", block, selector)
        self.assertIn("@media (max-width: 760px)", css)


if __name__ == "__main__":
    unittest.main()

"""Subscriptions: plans, activation codes, allowances and the Settings -> Subscription page.

Pure parts (codes, calendar months, settings) are tested directly; everything that touches
the database runs through the real app on a throwaway SQLite file, with every AI call
mocked. Quotas are switched on explicitly (SUBSCRIPTION_QUOTAS_ENFORCED) - the rest of the
suite runs with them off so its many lessons per account keep working.
"""

import io
import json
import logging
import os
import re
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_subscriptions_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.typing_helpers import fetch, match_group, present  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.subscriptions import codes, periods  # noqa: E402
from learnova.subscriptions.plans import SubscriptionSettings, load_settings  # noqa: E402
from learnova.version import APP_VERSION  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
PASSWORD = "correct-horse-battery"


class FakeResponse:
    model = "test-model"
    provider = "groq"
    degraded = ""

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload)
        self.usage = ai_service.GatewayUsage(0, 0, 0)


def lesson_payload(title="Brüche"):
    return {"lesson_title": title, "detected_level": "Klasse 7", "concepts": [{"name": title, "evidence": "notes"}],
            "explanation": "Erklärung.", "worked_example": {"problem": "Beispiel", "steps": ["Schritt"], "answer": "A"},
            "teacher_tips": [], "exceptions": [],
            "question": {"id": "q1", "concept": title, "difficulty": 1, "type": "multiple_choice", "prompt": "Frage?",
                         "hint": "Tipp", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                         "expected_answer": "a"}}


def png():
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="PNG")
    return output.getvalue()


# =================================================================== pure modules

class CodeTests(unittest.TestCase):
    def test_generated_codes_are_long_random_and_readable(self):
        generated = {codes.generate_code() for _ in range(300)}
        self.assertEqual(len(generated), 300)
        for code in list(generated)[:20]:
            self.assertRegex(code, r"^LN(-[0-9A-HJKMNP-TV-Z]{5}){4}$")
            self.assertNotRegex(code[3:], r"[ILOU]", "no look-alike letters")
        # 20 characters from a 32-letter alphabet: 100 bits.
        self.assertEqual(codes.BODY_LENGTH * 5, 100)

    def test_input_is_forgiving_about_case_spaces_dashes_and_look_alikes(self):
        code = "LN-7KQ2M-X9TAD-4HNWE-R1CZP"
        body = present(codes.normalize_code(code))
        for typed in ("ln-7kq2m-x9tad-4hnwe-r1czp", " LN 7KQ2M X9TAD 4HNWE R1CZP ", "7KQ2MX9TAD4HNWER1CZP",
                      "LN-7KQ2M-X9TAD-4HNWE-RICZP", "LN-7KQ2M-X9TAD-4HNWE-RlCZP"):
            self.assertEqual(codes.normalize_code(typed), body, typed)
        self.assertEqual(codes.normalize_code("LN-7KQ2M-X9TAD-4HNWE-R1CZO"), codes.normalize_code("LN-7KQ2M-X9TAD-4HNWE-R1CZ0"))

    def test_malformed_input_is_rejected_before_any_lookup(self):
        for typed in ("", "PREMIUM2026", "LN-7KQ2M", "LN-7KQ2M-X9TAD-4HNWE-R1CZP-EXTRA", "x" * 500, None, 12345,
                      "LN-7KQ2M-X9TAD-4HNWE-R1CZ!"):
            self.assertIsNone(codes.normalize_code(typed), typed)
            self.assertEqual(codes.candidate_hashes(typed), [])

    def test_hash_is_stable_domain_separated_and_never_the_code(self):
        body = present(codes.normalize_code(codes.generate_code()))
        self.assertEqual(codes.code_hash(body), codes.code_hash(body))
        self.assertNotIn(body, codes.code_hash(body))
        self.assertEqual(len(codes.code_hash(body)), 64)
        self.assertEqual(codes.code_hint(body), body[-4:])

    def test_legacy_keys_are_still_matched_exactly(self):
        import hashlib

        legacy = "LN-PREM-abcDEF_123-xyz"
        self.assertIn(hashlib.sha256(legacy.encode()).hexdigest(), codes.candidate_hashes(f"  {legacy} "))
        self.assertNotIn(hashlib.sha256(legacy.lower().encode()).hexdigest(), codes.candidate_hashes(legacy))


class CalendarTests(unittest.TestCase):
    def at(self, *args):
        return datetime(*args, tzinfo=BERLIN)

    def test_a_month_is_a_calendar_month_clamped_at_month_end(self):
        cases = [
            (self.at(2026, 1, 31, 10), self.at(2026, 2, 28, 10)),
            (self.at(2028, 1, 31, 10), self.at(2028, 2, 29, 10)),   # leap year
            (self.at(2028, 2, 29, 10), self.at(2028, 3, 29, 10)),
            (self.at(2026, 12, 15, 10), self.at(2027, 1, 15, 10)),
            (self.at(2026, 3, 31, 10), self.at(2026, 4, 30, 10)),
        ]
        for start, expected in cases:
            self.assertEqual(periods.add_months(start, 1, BERLIN), expected.astimezone(timezone.utc), start)
        self.assertEqual(periods.add_months(self.at(2026, 1, 31, 10), 12, BERLIN),
                         self.at(2027, 1, 31, 10).astimezone(timezone.utc))

    def test_local_time_survives_a_daylight_saving_change(self):
        # 10:00 CET on 1 March is 10:00 CEST on 1 April - one hour less in UTC.
        result = periods.add_months(self.at(2026, 3, 1, 10), 1, BERLIN)
        self.assertEqual(result.astimezone(BERLIN).hour, 10)
        self.assertEqual(result, datetime(2026, 4, 1, 8, tzinfo=timezone.utc))

    def test_a_day_is_the_local_calendar_day(self):
        # 23:30 in Berlin on 26 October (CET, after the change on the 25th).
        now = datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)
        start, end = periods.period_bounds(now, "day", BERLIN)
        self.assertEqual(start, datetime(2026, 10, 25, 23, 0, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc))
        week_start, week_end = periods.period_bounds(now, "week", BERLIN)
        self.assertEqual(week_start.astimezone(BERLIN).date().isoformat(), "2026-10-26")   # a Monday
        self.assertEqual((week_end - week_start), timedelta(days=7))
        month_start, month_end = periods.period_bounds(now, "month", BERLIN)
        self.assertEqual(month_start.astimezone(BERLIN).day, 1)
        self.assertEqual(month_end.astimezone(BERLIN).month, 11)


class SettingsTests(unittest.TestCase):
    def test_defaults_match_the_plans(self):
        settings = load_settings({})
        self.assertEqual(settings.premium_price_eur, Decimal("3.00"))
        self.assertEqual((settings.free_projects_per_window, settings.free_project_window_days), (1, 7))
        self.assertEqual(settings.free_lessons_per_day, 2)
        self.assertEqual(settings.code_duration_months, 1)
        self.assertEqual(settings.timezone_name, "Europe/Berlin")

    def test_bad_values_stop_the_app_at_startup(self):
        for env in ({"FREE_LESSONS_PER_DAY": "two"}, {"FREE_LESSONS_PER_DAY": "-1"},
                    {"PREMIUM_MONTHLY_PRICE_EUR": "free"}, {"PREMIUM_MONTHLY_PRICE_EUR": "0"},
                    {"FREE_CHAT_PERIOD": "fortnight"}, {"APP_TIMEZONE": "Mars/Olympus"},
                    {"FREE_CHAT_MODEL_WEIGHTS": "openai"}, {"FREE_CHAT_MODEL_WEIGHTS": "openai=-2"},
                    {"PREMIUM_CODE_DURATION_MONTHS": "13"}):
            with self.assertRaises(RuntimeError, msg=env):
                load_settings(env)

    def test_chat_credits_weigh_output_and_model_cost(self):
        settings = load_settings({"FREE_CHAT_MODEL_WEIGHTS": "openai=5, anthropic:claude-x=8"})
        self.assertEqual(settings.chat_credits("groq", "groq:llama", 1000, 100), 1400.0)
        self.assertEqual(settings.chat_credits("openai", "openai:gpt-x", 1000, 100), 7000.0)
        self.assertEqual(settings.chat_credits("anthropic", "anthropic:claude-x", 1000, 100), 11200.0)
        self.assertEqual(settings.chat_credits("anthropic", "anthropic:other", 1000, 100), 1400.0)


# =================================================================== app-level

class AppTestCase(unittest.TestCase):
    def setUp(self):
        application.app.config.update(
            TESTING=True, AI_MODE="cached", PREMIUM_ADMIN_IDENTIFIERS={"admin"},
            SUBSCRIPTION_QUOTAS_ENFORCED=True, SUBSCRIPTION_SETTINGS=SubscriptionSettings(),
            RATELIMIT_ENABLED=False)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def tearDown(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=False)

    def register(self, username="student", client=None):
        client = client or self.client
        client.post("/register", data={"username": username, "email": f"{username}@example.com",
                                       "password": PASSWORD, "grade": "8"})
        return client

    def login(self, username):
        client = application.app.test_client()
        client.post("/login", data={"identifier": username, "password": PASSWORD})
        return client

    def user_id(self, username="student"):
        with application.app.app_context():
            return application.db.session.scalar(application.db.select(application.User.id).where(
                application.User.username == username))

    def issue(self, months=1):
        """A code issued by the admin through the real page; returns the plaintext."""

        admin = self.login("admin") if self.user_id("admin") else self.register("admin", application.app.test_client())
        response = admin.post("/admin/premium-keys", data={
            "months": str(months), "payment_amount": "3,00",
            "payment_date": datetime.now(BERLIN).date().isoformat(), "payment_confirmed": "yes"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Cache-Control"), "no-store, private")
        return match_group(r'id="issued-premium-key">([^<]+)<', response.get_data(as_text=True)).strip()

    def redeem(self, code, client=None):
        return (client or self.client).post("/settings/premium/redeem", data={"activation_code": code},
                                            follow_redirects=True).get_data(as_text=True)

    def entitlement(self, username="student"):
        with application.app.app_context():
            return application.db.session.scalar(application.db.select(application.PremiumEntitlement).where(
                application.PremiumEntitlement.user_id == self.user_id(username)))

    def make_premium(self, username="student", *, days=10):
        with application.app.app_context():
            application.db.session.add(application.PremiumEntitlement(
                user_id=self.user_id(username), starts_at=application.utcnow() - timedelta(days=5),
                expires_at=application.utcnow() + timedelta(days=days), status="active"))
            application.db.session.commit()


class SubscriptionPageTests(AppTestCase):
    def test_free_account_sees_its_plan_the_price_the_cash_process_and_the_version(self):
        self.register()
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('id="subscription"', page)
        self.assertIn('class="sub-badge">Free<', page)
        self.assertIn("3,00 € / month", page)
        self.assertIn("Premium costs 3,00 € per month. Pay in cash directly to the Learnova administrator at school, "
                      "then enter the activation code you receive here. No online payment is required.", page)
        self.assertIn("Learnova does not accept online payments at this time.", page)
        self.assertIn(f"Learnova version {APP_VERSION}", page)
        self.assertIn("Activate Premium", page)
        self.assertIn("0 of 2", page)
        self.assertNotIn("Renew Premium", page)
        section = page[page.index('id="subscription"'):]
        visible = re.sub(r"<[^>]+>", " ", section).casefold()
        for word in ("token", "credit"):
            self.assertNotIn(word, visible, "no internal budget terms for students")

    def test_premium_account_sees_the_real_expiry_and_manual_renewal(self):
        self.register()
        self.make_premium(days=10)
        page = self.client.get("/settings").get_data(as_text=True)
        expiry = application.as_utc(self.entitlement().expires_at).astimezone(BERLIN).strftime("%d.%m.%Y %H:%M")
        self.assertIn(f"Premium is active until {expiry}.", page)
        self.assertIn("sub-badge is-premium", page)
        self.assertIn("Premium does not renew automatically", page)
        self.assertIn("Renew Premium", page)

    def test_an_expired_subscription_is_free_and_recorded_once(self):
        self.register()
        self.make_premium(days=-1)
        with application.app.app_context():
            self.assertFalse(application.has_active_premium(application.db.session.get(application.User, self.user_id())))
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('class="sub-badge">Free<', page)
        self.assertIn("Your Premium ended on", page)
        self.client.get("/settings")
        with application.app.app_context():
            self.assertEqual(self.entitlement().status, "expired")
            events = application.db.session.scalars(application.db.select(application.SubscriptionAuditEvent.action)).all()
            self.assertEqual(events.count("subscription_expired"), 1)

    def test_the_section_is_translated(self):
        self.register()
        self.client.post("/settings/language", data={"language": "de"})
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn(">Abo<", page)
        self.assertIn("3,00 € / Monat", page)
        self.assertIn("Learnova nimmt derzeit keine Online-Zahlungen an.", page)
        self.assertIn("Premium aktivieren", page)

    def test_the_page_works_in_both_themes_and_on_a_phone(self):
        # The section uses theme tokens only, so dark mode is the token swap; no
        # hardcoded light surfaces to override.
        css = Path("static/css/subscription.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,6}\b", "colours come from tokens")
        self.assertIn("minmax(min(100%, 220px), 1fr)", css, "plans stack on a phone")
        self.register()
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("css/subscription.css", page)
        self.assertIn('autocapitalize="characters"', page)


class ActivationCodeTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register("admin", application.app.test_client())
        self.register()

    def test_a_valid_code_activates_one_calendar_month(self):
        code = self.issue()
        before = application.utcnow()
        page = self.redeem(code.lower().replace("-", " "))
        self.assertIn("Premium is active until", page)
        record = self.entitlement()
        expected = periods.add_months(before, 1, BERLIN)
        self.assertLess(abs((application.as_utc(record.expires_at) - expected).total_seconds()), 60)
        with application.app.app_context():
            self.assertTrue(application.has_active_premium(application.db.session.get(application.User, self.user_id())))
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("…" + present(codes.normalize_code(code))[-4:], page, "the hint, not the code")
        self.assertNotIn(code, page)

    def test_invalid_expired_revoked_and_used_codes_all_fail_the_same_way(self):
        expired, revoked, used = self.issue(), self.issue(), self.issue()
        with application.app.app_context():
            keys = {key.code_hint: key for key in application.db.session.scalars(application.db.select(application.PremiumKey))}
            keys[present(codes.normalize_code(expired))[-4:]].expires_at = application.utcnow() - timedelta(seconds=1)
            revoked_id = keys[present(codes.normalize_code(revoked))[-4:]].id
            application.db.session.commit()
        admin = self.login("admin")
        admin.post(f"/admin/premium-keys/{revoked_id}/revoke")
        other = self.register("other", application.app.test_client())
        self.assertIn("Premium is active until", self.redeem(used, other))
        generic = "This code could not be activated. Check it and try again."
        for attempt in (expired, revoked, used, "LN-00000-00000-00000-00000", "nonsense"):
            page = self.redeem(attempt)
            self.assertIn(generic, page, attempt)
            for telltale in ("has been revoked", "already been redeemed", "has expired", "is invalid"):
                self.assertNotIn(telltale, page, "the answer never says which codes exist")
        self.assertIsNone(self.entitlement())
        with application.app.app_context():
            reasons = [json.loads(row.detail_json)["reason"] for row in application.db.session.scalars(
                application.db.select(application.SubscriptionAuditEvent).where(
                    application.SubscriptionAuditEvent.action == "code_redeem_failed"))]
            self.assertEqual(sorted(reasons), sorted(["expired", "revoked", "already_redeemed", "unknown", "malformed"]))

    def test_resubmitting_your_own_code_extends_nothing(self):
        code = self.issue()
        self.redeem(code)
        first = application.as_utc(self.entitlement().expires_at)
        page = self.redeem(code)
        self.assertIn("This code is already active on your account", page)
        self.assertEqual(application.as_utc(self.entitlement().expires_at), first)
        with application.app.app_context():
            self.assertEqual(application.db.session.query(application.PremiumRedemption).count(), 1)

    def test_renewal_extends_from_the_current_end_date(self):
        self.make_premium(days=10)
        current_end = application.as_utc(self.entitlement().expires_at)
        self.redeem(self.issue(months=2))
        self.assertEqual(application.as_utc(self.entitlement().expires_at), periods.add_months(current_end, 2, BERLIN))
        with application.app.app_context():
            redemption = application.db.session.scalars(application.db.select(application.PremiumRedemption)).one()
            self.assertEqual(application.as_utc(redemption.previous_expires_at), current_end)

    def test_a_lapsed_subscription_restarts_from_now(self):
        self.make_premium(days=-3)
        before = application.utcnow()
        self.redeem(self.issue())
        expires = application.as_utc(self.entitlement().expires_at)
        self.assertLess(abs((expires - periods.add_months(before, 1, BERLIN)).total_seconds()), 60)

    def test_guessing_is_stopped_after_a_few_attempts_even_with_a_valid_code(self):
        valid = self.issue()
        for index in range(5):
            self.redeem(f"LN-00000-00000-00000-0000{index}")
        page = self.redeem(valid)
        self.assertIn("Too many attempts. Try again in", page)
        self.assertIsNone(self.entitlement())
        # A fresh account from the same address is still limited by the source count.
        application.app.config["SUBSCRIPTION_SETTINGS"] = SubscriptionSettings(redeem_attempts_per_source=6)
        neighbour = self.register("neighbour", application.app.test_client())
        self.redeem("LN-00000-00000-00000-00009", neighbour)
        self.assertIn("Too many attempts", self.redeem(valid, neighbour))

    def test_the_limit_counts_from_the_database_and_lifts_after_the_window(self):
        valid = self.issue()
        for index in range(5):
            self.redeem(f"LN-00000-00000-00000-0000{index}")
        later = application.utcnow() + timedelta(minutes=16)
        with patch.object(application, "utcnow", return_value=later):
            self.assertIn("Premium is active until", self.redeem(valid))

    def test_codes_are_stored_only_as_hashes_and_never_logged(self):
        records: list[str] = []

        class Collect(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Collect(level=logging.DEBUG)
        logging.getLogger().addHandler(handler)
        application.app.logger.addHandler(handler)
        try:
            code = self.issue()
            self.redeem(code)
            self.redeem("LN-00000-00000-00000-00000")
        finally:
            logging.getLogger().removeHandler(handler)
            application.app.logger.removeHandler(handler)
        body = present(codes.normalize_code(code))
        with application.app.app_context():
            connection = application.db.session.connection()
            for table in ("premium_key", "premium_redemption", "premium_entitlement", "subscription_audit_event",
                          "usage_quota_event", "ai_usage_event"):
                for row in connection.execute(application.text(f"SELECT * FROM {table}")):
                    dumped = json.dumps([str(value) for value in row])
                    self.assertNotIn(body, dumped.replace("-", ""), table)
            key = application.db.session.scalars(application.db.select(application.PremiumKey)).one()
            self.assertEqual(key.key_hash, codes.code_hash(body))
        for message in records:
            self.assertNotIn(body, message.replace("-", ""))
        # The admin page shows the code once; a reload does not.
        self.assertNotIn(code, self.login("admin").get("/admin/premium-keys").get_data(as_text=True))

    def test_ordinary_users_cannot_issue_list_or_revoke_codes(self):
        self.assertEqual(self.client.get("/admin/premium-keys").status_code, 404)
        self.assertEqual(self.client.post("/admin/premium-keys", data={
            "months": "12", "payment_amount": "0", "payment_date": "2026-01-01", "payment_confirmed": "yes"}).status_code, 404)
        self.assertEqual(self.client.post("/admin/premium-keys/1/revoke").status_code, 404)
        with application.app.app_context():
            self.assertEqual(application.db.session.query(application.PremiumKey).count(), 0)

    def test_the_plan_cannot_be_set_from_the_browser(self):
        for data in ({"plan": "premium"}, {"plan": "premium", "activation_code": ""},
                     {"premium": "1", "expires_at": "2099-01-01", "user_id": str(self.user_id("admin"))}):
            self.client.post("/settings/premium/redeem", data=data)
            self.client.post("/settings", data=data)
        self.assertIsNone(self.entitlement())
        self.assertIsNone(self.entitlement("admin"))

    def test_simultaneous_redemptions_activate_one_account(self):
        code = self.issue()
        clients = [self.register(f"twin{index}", application.app.test_client()) for index in range(4)]
        barrier = threading.Barrier(len(clients))

        def attempt(client):
            barrier.wait(timeout=10)
            return self.redeem(code, client)

        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            pages = list(pool.map(attempt, clients))
        self.assertEqual(sum("Premium is active until" in page for page in pages), 1)
        with application.app.app_context():
            self.assertEqual(application.db.session.query(application.PremiumEntitlement).count(), 1)
            self.assertEqual(application.db.session.query(application.PremiumRedemption).count(), 1)

    def test_a_code_issued_before_calendar_months_still_grants_thirty_days(self):
        import hashlib

        legacy = "LN-PREM-legacyKey_0123456789abcdef"
        with application.app.app_context():
            application.db.session.add(application.PremiumKey(
                key_hash=hashlib.sha256(legacy.encode()).hexdigest(), duration_months=0, duration_days=30,
                expires_at=application.utcnow() + timedelta(days=100), created_by_id=self.user_id("admin")))
            application.db.session.commit()
        self.assertIn("Premium is active until", self.redeem(legacy))
        record = self.entitlement()
        self.assertEqual((application.as_utc(record.expires_at) - application.as_utc(record.starts_at)).days, 30)

    def test_the_command_line_issues_lists_and_revokes_without_storing_the_code(self):
        runner = application.app.test_cli_runner()
        result = runner.invoke(args=["premium", "issue", "--admin", "admin", "--amount", "3.00"])
        self.assertEqual(result.exit_code, 0, result.output)
        code = match_group(r"(LN(?:-[0-9A-Z]{5}){4})", result.output)
        listing = runner.invoke(args=["premium", "list"]).output
        self.assertNotIn(code, listing)
        self.assertIn(present(codes.normalize_code(code))[-4:], listing)
        key_id = match_group(r"Record id (\d+)", result.output)
        self.assertEqual(runner.invoke(args=["premium", "revoke", "--admin", "admin", key_id]).exit_code, 0)
        self.assertIn("could not be activated", self.redeem(code))
        refused = runner.invoke(args=["premium", "issue", "--admin", "student", "--amount", "3.00"])
        self.assertNotEqual(refused.exit_code, 0, "only allowlisted accounts")


# =================================================================== allowances

class AllowanceTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def reserve(self, kind, *, amount=1.0, reference="", commit=True):
        with application.app.app_context():
            reservation = application.entitlements.reserve(self.user_id(), kind, amount=amount, reference=reference)
            if commit:
                reservation.commit()
            return reservation

    def test_two_lessons_a_day_then_blocked_until_local_midnight(self):
        # 23:30 in Berlin: the reset is 30 minutes away, not at UTC midnight.
        now = datetime(2026, 10, 26, 22, 30, tzinfo=timezone.utc)
        with patch.object(application, "utcnow", return_value=now):
            self.reserve("lesson")
            self.reserve("lesson")
            with self.assertRaises(application.subscriptions.QuotaExceededError) as raised:
                self.reserve("lesson")
            self.assertEqual(raised.exception.resets_at, datetime(2026, 10, 26, 23, 0, tzinfo=timezone.utc))
            self.assertEqual(raised.exception.scope, "free_lessons")
        with patch.object(application, "utcnow", return_value=now + timedelta(minutes=31)):
            self.reserve("lesson")

    def test_one_project_per_rolling_week(self):
        start = datetime(2026, 10, 1, 9, tzinfo=timezone.utc)
        with patch.object(application, "utcnow", return_value=start):
            self.reserve("project", reference="project:1")
        with patch.object(application, "utcnow", return_value=start + timedelta(days=3)):
            with self.assertRaises(application.subscriptions.QuotaExceededError) as raised:
                self.reserve("project", reference="project:2")
            self.assertEqual(raised.exception.resets_at, start + timedelta(days=7))
        with patch.object(application, "utcnow", return_value=start + timedelta(days=7, seconds=1)):
            self.reserve("project", reference="project:2")
            with self.assertRaises(application.subscriptions.QuotaExceededError):
                self.reserve("project", reference="project:3")

    def test_a_released_or_abandoned_reservation_gives_the_allowance_back(self):
        with application.app.test_request_context():
            reservation = application.entitlements.reserve(self.user_id(), "lesson")
            reservation.release()
            application.db.session.remove()
            application.entitlements.flush_deferred(application.db.engine.begin)
        self.reserve("lesson")
        self.reserve("lesson", commit=False)   # a worker that died mid-call
        later = application.utcnow() + timedelta(minutes=16)
        with patch.object(application, "utcnow", return_value=later):
            self.reserve("lesson")
            with self.assertRaises(application.subscriptions.QuotaExceededError):
                self.reserve("lesson")

    def test_premium_has_no_ordinary_limits(self):
        self.make_premium()
        for _ in range(6):
            self.reserve("lesson")
            self.reserve("project")
            self.reserve("chat", amount=10_000_000)
        with application.app.app_context():
            account = application.entitlements.entitlements(self.user_id())
            self.assertTrue(account.lessons.unlimited and account.projects.unlimited and account.chat.unlimited)
            self.assertEqual(application.db.session.query(application.UsageQuotaEvent).count(), 0)

    def test_parallel_requests_cannot_exceed_the_allowance(self):
        results = []

        def attempt(_):
            with application.app.app_context():
                try:
                    application.entitlements.reserve(self.user_id(), "lesson").commit()
                    return "ok"
                except application.subscriptions.QuotaExceededError:
                    return "blocked"
                finally:
                    application.db.session.remove()

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(results.count("ok"), 2, results)
        self.assertEqual(results.count("blocked"), 4)

    def test_a_reference_is_counted_once_even_when_requested_twice(self):
        self.reserve("project", reference="project:7")
        self.reserve("project", reference="project:7")
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1)


class LessonQuotaRouteTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def analyze(self, side_effect=None):
        with patch.object(application, "create_response", side_effect=side_effect or (
                lambda **_: FakeResponse(lesson_payload()))):
            return self.client.post("/api/analyze", data={"study_goal": "Brüche addieren", "subject": "Math"})

    def committed(self, kind="lesson"):
        with application.app.app_context():
            return application.db.session.query(application.UsageQuotaEvent).filter_by(
                kind=kind, status="committed").count()

    def test_third_lesson_of_the_day_is_refused_with_reset_time_and_upgrade_link(self):
        self.assertEqual(self.analyze().status_code, 200)
        self.assertEqual(self.analyze().status_code, 200)
        refused = self.analyze()
        self.assertEqual(refused.status_code, 429)
        body = refused.get_json()
        self.assertEqual(body["code"], "free_lessons_used")
        self.assertIn("You have used today's free lessons", body["error"])
        self.assertIn("upgrade_url", body["details"])
        self.assertIn("resets_at", body["details"])

    def test_failed_generation_does_not_use_up_a_lesson(self):
        def failing(**_):
            raise ai_service.AIProviderError("provider_timeout", "timeout")

        for _ in range(3):
            self.assertNotEqual(self.analyze(failing).status_code, 200)
        self.assertEqual(self.committed(), 0)
        self.assertEqual(self.analyze().status_code, 200)
        self.assertEqual(self.analyze().status_code, 200)

    def test_opening_saved_lessons_uses_nothing(self):
        session_id = self.analyze().get_json()["session_id"]
        with application.app.app_context():
            lesson_id = application.db.session.scalar(application.db.select(application.Lesson.id))
        for _ in range(5):
            self.client.get(f"/lessons/{lesson_id}")
            self.client.get(f"/?session_id={session_id}")
        self.assertEqual(self.committed(), 1)

    def test_every_lesson_route_shares_the_same_allowance(self):
        # /api/analyze and start_saved_practice are the only two places a lesson is
        # generated; every practice, review and project-topic route goes through the second.
        self.analyze()
        with application.app.test_request_context(), \
                patch.object(application, "create_response", side_effect=lambda **_: FakeResponse(lesson_payload())):
            application.login_user(application.db.session.get(application.User, self.user_id()))
            application.start_saved_practice("Übe Brüche", "Math", "weak-practice", test_total=3)
            with self.assertRaises(application.subscriptions.QuotaExceededError):
                application.start_saved_practice("Übe Brüche", "Math", "weak-practice", test_total=3)
            application.db.session.remove()
        self.assertEqual(self.committed(), 2)
        self.assertEqual(self.analyze().status_code, 429)

    def test_a_refused_lesson_in_a_page_flow_links_to_premium(self):
        with application.app.test_request_context():
            application.flash_ai_failure(application.subscriptions.QuotaExceededError("lesson", resets_at=None))
            from flask import get_flashed_messages
            flashed = get_flashed_messages(with_categories=True)
        self.assertEqual(flashed[0][0], "limit")
        with self.client.session_transaction() as session:
            session["_flashes"] = flashed
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn("You have used today", page)
        self.assertIn("See Premium", page)

    def test_premium_generates_lessons_without_a_daily_limit(self):
        self.make_premium()
        for _ in range(4):
            self.assertEqual(self.analyze().status_code, 200)


class ProjectQuotaRouteTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def respond(self, *, task_type, **kwargs):
        if task_type == "ocr_document_recognition":
            return FakeResponse({"blocks": [{"type": "printed_text", "content": "Brüche werden addiert. " * 30,
                                             "bbox": [0.05, 0.05, 0.95, 0.9], "confidence": 0.95, "crossed_out": False,
                                             "important_candidate": False, "teacher_highlight_candidate": False,
                                             "nearby_text": ""}], "detected_page_number": "1", "warning": ""})
        if task_type == "project_section_generation":
            ids = [int(value) for value in re.findall(r'"page_id": (\d+)', str(kwargs.get("input", "")))]
            return FakeResponse({"sections": [{"title": "Brüche", "source_page_ids": ids[:1], "learning_goals": ["addieren"],
                                               "standard_explanation": "Brüche.", "estimated_minutes": 15}]})
        if task_type in ("lesson_generation", "adaptive_practice"):
            return FakeResponse(lesson_payload())
        raise AssertionError(task_type)

    def create(self):
        # A distinct title per project: the same file, title and subject twice within
        # two minutes is treated as a double submit and opens the first project instead.
        self.created = getattr(self, "created", 0) + 1
        with patch.object(application, "create_response", side_effect=self.respond):
            return self.client.post("/projects", data={"subject": "Math", "title": f"Project {self.created}",
                                                       "materials": [(io.BytesIO(png()), "p.png", "image/png")]},
                                    content_type="multipart/form-data")

    def latest_project(self):
        with application.app.app_context():
            return application.db.session.scalar(application.db.select(application.LearningProject.id).order_by(
                application.LearningProject.id.desc()))

    def analyse(self, project_id):
        with patch.object(application, "create_response", side_effect=self.respond):
            return self.client.post(f"/projects/{project_id}/quick-start")

    def test_one_generated_project_a_week_then_a_clear_refusal_before_upload(self):
        self.assertEqual(self.create().status_code, 302)
        self.analyse(self.latest_project())
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1)
            projects_before = application.db.session.query(application.LearningProject).count()
        refused = self.create()
        self.assertEqual(refused.status_code, 200)
        page = refused.get_data(as_text=True)
        self.assertIn("You have used your free projects for this week", page)
        self.assertIn("See Premium", page)
        with application.app.app_context():
            self.assertEqual(application.db.session.query(application.LearningProject).count(), projects_before)

    def test_a_project_of_typed_topics_counts_too(self):
        # Typed topics are the project's topics at once, so no analysis would ever count it,
        # and lessons inside projects are unlimited.
        created = self.client.post("/projects", data={"subject": "Math", "title": "Typed", "topics": "Brüche"},
                                   content_type="multipart/form-data")
        self.assertEqual(created.status_code, 302)
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1)
        refused = self.client.post("/projects", data={"subject": "Math", "title": "Second", "topics": "Gleichungen"},
                                   content_type="multipart/form-data")
        self.assertIn("You have used your free projects for this week", refused.get_data(as_text=True))
        with application.app.app_context():
            self.assertEqual(application.db.session.query(application.LearningProject).count(), 1)

    def test_lessons_and_tests_inside_a_project_are_unlimited(self):
        self.client.post("/projects", data={"subject": "Math", "title": "Typed",
                                            "topics": "Brüche, Gleichungen, Prozente, Terme"},
                         content_type="multipart/form-data")
        project_id = self.latest_project()
        with application.app.app_context():
            sections = [section.id for section in
                        application.db.session.get(application.LearningProject, project_id).sections]
        self.assertEqual(len(sections), 4)
        with patch.object(application, "create_response", side_effect=self.respond):
            for section_id in sections:     # four lessons: twice the free daily lessons
                started = self.client.post(f"/projects/{project_id}/topics/{section_id}/start")
                self.assertIn("session_id=", started.headers.get("Location", ""), started.headers.get("Location"))
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "lesson").used, 0)
        # Lessons outside a project keep their daily allowance.
        with application.app.test_request_context(),                 patch.object(application, "create_response", side_effect=lambda **_: FakeResponse(lesson_payload())):
            application.login_user(application.db.session.get(application.User, self.user_id()))
            application.start_saved_practice("Übe Brüche", "Math", "weak-practice", test_total=3)
            application.db.session.remove()
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "lesson").used, 1)

    def test_reanalysing_viewing_or_reopening_a_project_counts_nothing_more(self):
        self.create()
        project_id = self.latest_project()
        self.analyse(project_id)
        for _ in range(3):
            self.client.get(f"/projects/{project_id}")
        with patch.object(application, "create_response", side_effect=self.respond):
            self.client.post(f"/projects/{project_id}/reanalyze")
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1)

    def test_a_failed_analysis_gives_the_project_back(self):
        self.create()
        project_id = self.latest_project()

        def broken(*, task_type, **kwargs):
            if task_type == "project_section_generation":
                raise ai_service.AIProviderError("provider_timeout", "timeout")
            return self.respond(task_type=task_type, **kwargs)

        with patch.object(application, "create_response", side_effect=broken):
            self.client.post(f"/projects/{project_id}/quick-start")
        self.client.get("/")   # a later request; settlements are applied after each request
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 0)
        self.analyse(project_id)
        with application.app.app_context():
            self.assertEqual(application.entitlements.allowance(self.user_id(), "project").used, 1)


class ChatAllowanceTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.register()

    def chat(self, *, usages=(("groq", "groq:llama", 1000, 200),), fail=False, task="assistant_chat"):
        """One call through the app's create_response, with the gateway replaced by a stub
        that reports the given provider calls to the observer."""

        def gateway(**kwargs):
            observer = kwargs.get("usage_observer")
            for provider, model, tokens_in, tokens_out in usages:
                if observer is not None:   # Premium calls are not metered
                    observer(provider, model, ai_service.GatewayUsage(tokens_in, tokens_out, tokens_in + tokens_out))
            if fail:
                raise ai_service.AIProviderError("provider_timeout", "timeout")
            return FakeResponse("Antwort")

        with application.app.test_request_context(), patch.object(ai_service, "create_response", side_effect=gateway):
            application.login_user(application.db.session.get(application.User, self.user_id()))
            try:
                application.create_response(task_type=task, model="groq:llama", input="Hallo", instructions="Sei nett",
                                            max_output_tokens=500, usage_observer=lambda *a: None)
            finally:
                application.db.session.remove()
                application.entitlements.flush_deferred(application.db.engine.begin)

    def events(self):
        with application.app.app_context():
            return [(row.status, row.amount) for row in application.db.session.scalars(
                application.db.select(application.UsageQuotaEvent).where(application.UsageQuotaEvent.kind == "chat"))]

    def test_actual_usage_replaces_the_reservation(self):
        self.chat(usages=(("groq", "groq:llama", 1000, 200),))
        self.assertEqual(self.events(), [("committed", 1800.0)])

    def test_retries_and_fallbacks_are_all_charged_at_their_model_weight(self):
        application.app.config["SUBSCRIPTION_SETTINGS"] = SubscriptionSettings(chat_model_weights={"openai": 5.0})
        self.chat(usages=(("groq", "groq:llama", 1000, 0), ("openai", "openai:gpt-x", 1000, 100)))
        self.assertEqual(self.events(), [("committed", 1000.0 + 7000.0)])

    def test_a_failure_before_any_provider_call_costs_nothing(self):
        with self.assertRaises(ai_service.AIProviderError):
            self.chat(usages=(), fail=True)
        self.assertEqual(self.events()[0][0], "released")

    def test_the_budget_runs_out_with_a_clear_safe_error(self):
        application.app.config["SUBSCRIPTION_SETTINGS"] = SubscriptionSettings(free_chat_credits_per_period=5000)
        self.chat(usages=(("groq", "groq:llama", 2000, 500),))   # 4000 credits used
        with self.assertRaises(application.subscriptions.QuotaExceededError) as raised:
            self.chat()
        with application.app.test_request_context():
            message, status, code = application.ai_failure_message(raised.exception)
        self.assertEqual((status, code), (429, "free_chat_used"))
        self.assertIn("free AI assistant allowance", message)
        self.assertNotIn("token", message.casefold())

    def test_premium_chats_without_the_allowance_but_keeps_abuse_protection(self):
        self.make_premium()
        application.app.config["SUBSCRIPTION_SETTINGS"] = SubscriptionSettings(free_chat_credits_per_period=1)
        for _ in range(3):
            self.chat(task="tutor_chat")
        self.assertEqual(self.events(), [])

        class FullHour:
            def count_requests(self, *, user_reference, since):
                return 10_000

            def count_provider_calls(self, *, provider, since):
                return 0

        application.app.config.update(AI_ENFORCE_LIMITS=True, AI_MAX_REQUESTS_PER_USER_HOUR=60)
        try:
            with application.app.app_context(), patch.object(ai_service, "usage_ledger", return_value=FullHour()):
                with self.assertRaises(ai_service.AIRequestLimitError):
                    ai_service._assert_usage_limits("someone", "live", premium=True)
        finally:
            application.app.config.update(AI_ENFORCE_LIMITS=False)

    def test_the_browser_cannot_lower_its_own_charge(self):
        # A caller-supplied observer, plan or token count is ignored: the charge comes
        # from the gateway's measured calls only.
        self.chat(usages=(("groq", "groq:llama", 1000, 200),))
        self.assertEqual(self.events(), [("committed", 1800.0)])


class GatewayObserverTests(unittest.TestCase):
    def test_every_provider_call_is_reported_and_cache_hits_are_not(self):
        saved = dict(application.app.config)
        temporary = tempfile.TemporaryDirectory()
        application.app.config.update(TESTING=True, AI_MODE="cached", AI_CACHE_DIR=temporary.name,
                                      AI_USAGE_PATH=str(Path(temporary.name) / "usage.jsonl"))
        seen = []

        class Raw:
            output_text = "Hallo!"
            usage = type("U", (), {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15})()

        def observe(provider, model, usage):
            seen.append((provider, usage.total_tokens))

        try:
            with application.app.app_context(), \
                    patch.object(ai_service, "_assert_provider_allowed", return_value=None), \
                    patch.object(ai_service, "_provider_response", return_value=Raw()), \
                    patch.object(ai_service, "validate_output", return_value=None):
                for _ in range(2):   # the second identical request is a cache hit
                    ai_service.create_response(task_type="assistant_chat", model="groq:test", input="hi",
                                               usage_observer=observe)
        finally:
            application.app.config.clear()
            application.app.config.update(saved)
            temporary.cleanup()
        self.assertEqual(seen, [("groq", 15)])


class MigrationTests(unittest.TestCase):
    """A database from before subscriptions gets the new columns without losing data."""

    def test_existing_data_survives_and_legacy_keys_keep_their_days(self):
        from sqlalchemy.schema import CreateTable

        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            engine = application.db.engine
            new_columns = ("usage_lock_version", "duration_months", "code_hint", "revoked_by_id",
                           "previous_expires_at", "new_expires_at")
            with engine.begin() as connection:
                for table_name in ("user", "premium_key", "premium_redemption"):
                    ddl = str(CreateTable(application.db.metadata.tables[table_name]).compile(engine))
                    kept = [line for line in ddl.split("\n") if not any(name in line for name in new_columns)]
                    ddl = re.sub(r",\s*\)\s*$", "\n)", "\n".join(kept).rstrip())
                    connection.execute(application.text(ddl))
                connection.execute(application.text(
                    "INSERT INTO \"user\" (id, username, email, password_hash, preferred_language, grade, school_type, created_at) "
                    "VALUES (1, 'old', 'old@example.com', 'x', 'de', '8', '', '2025-01-01 00:00:00')"))
                connection.execute(application.text(
                    "INSERT INTO premium_key (id, key_hash, duration_days, created_at, expires_at, created_by_id, payment_amount, payment_reference) "
                    "VALUES (1, 'abc', 30, '2025-01-01 00:00:00', '2099-01-01 00:00:00', 1, '10.00', 'cash')"))
            application.db.session.remove()
            application.ensure_database()
            columns = {column["name"] for column in application.inspect(engine).get_columns("premium_key")}
            self.assertTrue({"duration_months", "code_hint", "revoked_by_id"} <= columns)
            self.assertIn("usage_lock_version", {c["name"] for c in application.inspect(engine).get_columns("user")})
            self.assertIn("usage_quota_event", application.inspect(engine).get_table_names())
            self.assertIn("subscription_audit_event", application.inspect(engine).get_table_names())
            key = fetch(application.db.session, application.PremiumKey, 1)
            self.assertEqual((key.duration_months, key.duration_days), (0, 30))
            self.assertEqual(fetch(application.db.session, application.User, 1).username, "old")
            self.assertIsNotNone(application.db.session.get(application.SchemaMigration, "032_subscriptions"))
            application.ensure_database()   # idempotent
            application.db.drop_all()
            application.db.create_all()


if __name__ == "__main__":
    unittest.main()

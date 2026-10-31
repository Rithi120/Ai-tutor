import hashlib
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from datetime import timedelta
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_premium_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class PremiumTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, PREMIUM_ADMIN_IDENTIFIERS={"admin"})
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def register(self, username="student"):
        self.client.post("/register", data={
            "username": username, "email": f"{username}@example.com",
            "password": "correct-horse-battery", "grade": "8",
        })

    def make_key(self, *, expires=None, revoked=False):
        raw = "LN-PREM-test-key-" + str(application.uuid.uuid4())
        record = application.PremiumKey(
            key_hash=hashlib.sha256(raw.encode()).hexdigest(),
            created_by_id=application.User.query.filter_by(username="admin").one().id,
            created_at=application.utcnow(),
            expires_at=expires or application.utcnow() + timedelta(days=365),
            revoked_at=application.utcnow() if revoked else None,
        )
        application.db.session.add(record)
        application.db.session.commit()
        return raw, record

    def test_only_allowlisted_admin_can_open_key_management(self):
        self.register()
        self.assertEqual(self.client.get("/admin/premium-keys").status_code, 404)
        self.client.post("/logout")
        self.register("admin")
        self.assertEqual(self.client.get("/admin/premium-keys").status_code, 200)

    def test_key_issue_requires_recorded_payment_confirmation(self):
        self.register("admin")
        with application.app.app_context():
            before = application.PremiumKey.query.count()
        self.client.post("/admin/premium-keys", data={
            "payment_amount": "10.00", "payment_date": "2026-10-18",
            "payment_reference": "cash-001",
        })
        with application.app.app_context():
            self.assertEqual(application.PremiumKey.query.count(), before)

    def test_redeem_grants_one_calendar_month_and_duplicate_is_rejected(self):
        self.register("admin")
        with application.app.app_context():
            raw, key = self.make_key()
        self.client.post("/logout")
        self.register()
        first = self.client.post("/settings/premium/redeem", data={"premium_key": raw})
        self.assertEqual(first.status_code, 302)
        with application.app.app_context():
            entitlement = application.PremiumEntitlement.query.one()
            self.assertTrue(application.has_active_premium(entitlement.user))
            # A calendar month in the app's timezone (learnova/subscriptions/periods.py),
            # not a flat 30 days.
            from learnova.subscriptions.periods import add_months
            zone = application.subscription_settings().zone
            self.assertEqual(application.as_utc(entitlement.expires_at),
                             add_months(application.as_utc(entitlement.starts_at), 1, zone))
            self.assertEqual(application.PremiumRedemption.query.count(), 1)
        self.client.post("/settings/premium/redeem", data={"premium_key": raw})
        with application.app.app_context():
            self.assertEqual(application.PremiumRedemption.query.count(), 1)

    def test_expired_and_revoked_keys_are_not_redeemed(self):
        self.register("admin")
        with application.app.app_context():
            expired, _ = self.make_key(expires=application.utcnow() - timedelta(seconds=1))
            revoked, _ = self.make_key(revoked=True)
        self.client.post("/logout")
        self.register()
        for raw in (expired, revoked, "not-a-key"):
            self.client.post("/settings/premium/redeem", data={"premium_key": raw})
        with application.app.app_context():
            self.assertEqual(application.PremiumRedemption.query.count(), 0)
            self.assertEqual(application.PremiumEntitlement.query.count(), 0)

    def test_concurrent_redemption_commits_only_one_grant(self):
        self.register("admin")
        with application.app.app_context():
            raw, _ = self.make_key()
        self.client.post("/logout")
        self.register()
        second_client = application.app.test_client()
        second_client.post("/login", data={
            "identifier": "student", "password": "correct-horse-battery",
        })
        barrier = threading.Barrier(2)

        def redeem(client):
            barrier.wait(timeout=5)
            return client.post("/settings/premium/redeem", data={"premium_key": raw}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(redeem, (self.client, second_client)))
        self.assertEqual(statuses, [302, 302])
        with application.app.app_context():
            self.assertEqual(application.PremiumRedemption.query.count(), 1)
            self.assertEqual(application.PremiumEntitlement.query.count(), 1)

    def test_active_subscription_is_extended_and_expiry_disables_access(self):
        self.register("admin")
        with application.app.app_context():
            raw, _ = self.make_key()
            student = application.User(username="student", email="student@example.com", grade="8")
            student.set_password("correct-horse-battery")
            application.db.session.add(student)
            application.db.session.flush()
            entitlement = application.PremiumEntitlement(
                user_id=student.id, starts_at=application.utcnow(),
                expires_at=application.utcnow() + timedelta(days=8))
            application.db.session.add(entitlement)
            application.db.session.commit()
        self.client.post("/logout")
        self.client.post("/login", data={"identifier": "student", "password": "correct-horse-battery"})
        self.client.post("/settings/premium/redeem", data={"premium_key": raw})
        with application.app.app_context():
            entitlement = application.PremiumEntitlement.query.one()
            self.assertGreaterEqual((application.as_utc(entitlement.expires_at) - application.utcnow()).days, 37)
            entitlement.expires_at = application.utcnow() - timedelta(seconds=1)
            application.db.session.commit()
            student = application.User.query.filter_by(username="student").first()
            self.assertFalse(application.has_active_premium(student))

    def test_free_daily_request_cap_applies_and_premium_bypasses_it(self):
        from learnova.ai_services import service

        class FullDailyLedger:
            def count_requests(self, *, user_reference, since):
                return 1000 if since < application.utcnow() - timedelta(hours=2) else 0

            def count_provider_calls(self, *, provider, since):
                return 0

        application.app.config.update(AI_ENFORCE_LIMITS=True, AI_MAX_REQUESTS_PER_USER_DAY=2,
                                      AI_MAX_REQUESTS_PER_USER_HOUR=60)
        with application.app.app_context(), patch.object(service, "usage_ledger", return_value=FullDailyLedger()):
            with self.assertRaises(service.AIRequestLimitError):
                service._assert_usage_limits("test-user", "live", premium=False)
            service._assert_usage_limits("test-user", "live", premium=True)

    def test_adsense_loader_is_present_for_free_and_absent_for_premium(self):
        # With a consent message confirmed and ads for students switched on, a free
        # student gets the loader; Premium never does (tests/test_ads_csp.py has the rest).
        application.app.config.update(ADSENSE_CMP="google_privacy_messaging", ADSENSE_SHOW_TO_STUDENTS=True)
        self.addCleanup(application.app.config.update, ADSENSE_CMP="", ADSENSE_SHOW_TO_STUDENTS=False)
        self.register()
        free_page = self.client.get("/about").data
        self.assertEqual(free_page.count(b"pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"), 1)
        with application.app.app_context():
            user = application.User.query.filter_by(username="student").one()
            application.db.session.add(application.PremiumEntitlement(
                user_id=user.id, starts_at=application.utcnow(),
                expires_at=application.utcnow() + timedelta(days=30)))
            application.db.session.commit()
        premium_page = self.client.get("/settings").data
        self.assertEqual(premium_page.count(b"pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"), 0)


if __name__ == "__main__":
    unittest.main()

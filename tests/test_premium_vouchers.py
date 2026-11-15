"""Bulk Premium vouchers (2026-11-14): generation, storage, redemption, administration and
security, through the real app on a throwaway SQLite file. No AI is involved."""

import hashlib
import hmac
import logging
import os
import re
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vouchers_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.subscriptions import activation as activation_module, codes, periods  # noqa: E402
from tests.test_subscriptions import AppTestCase  # noqa: E402

BERLIN = ZoneInfo("Europe/Berlin")
PEPPER = "test-pepper-" + "x" * 40
CODE = re.compile(r"LN-PREM(?:-[0-9A-Z]{4}){7}")


class VoucherCase(AppTestCase):
    def setUp(self):
        super().setUp()
        self._pepper = application.app.config.get("PREMIUM_VOUCHER_PEPPER")
        application.app.config["PREMIUM_VOUCHER_PEPPER"] = PEPPER
        self.addCleanup(application.app.config.update, PREMIUM_VOUCHER_PEPPER=self._pepper)
        self.admin = self.register("admin", application.app.test_client())
        self.register()

    def generate(self, quantity="10", custom="", months="1", client=None):
        response = (client or self.admin).post("/admin/premium-vouchers/generate", data={
            "quantity": quantity, "custom_quantity": custom, "months": months, "note": "Klasse 8b"})
        return response

    def codes_from(self, response):
        return CODE.findall(response.get_data(as_text=True).split('id="voucherExport"')[0])

    def keys(self):
        with application.app.app_context():
            return application.db.session.scalars(application.db.select(application.PremiumKey)).all()


# ------------------------------------------------------------------------ pure
class FormatTests(unittest.TestCase):
    def test_a_voucher_has_140_random_bits_and_a_readable_form(self):
        code = codes.generate_voucher_code()
        self.assertRegex(code, CODE)
        self.assertGreaterEqual(codes.VOUCHER_BODY_LENGTH * 5, 128)
        self.assertNotRegex(code[8:], r"[ILOU]", "no look-alike letters")
        self.assertEqual(len({codes.generate_voucher_code() for _ in range(2000)}), 2000)

    def test_normalisation(self):
        code = codes.generate_voucher_code()
        body = codes.normalize_voucher(code)
        self.assertEqual(codes.normalize_voucher(f"  {code.lower()}  "), body)
        self.assertEqual(codes.normalize_voucher(code.replace("-", " ")), body)
        self.assertEqual(codes.normalize_voucher(code[8:]), body, "without the prefix")
        self.assertEqual(codes.normalize_voucher(code.replace("0", "O").replace("1", "I")), body)
        self.assertIsNone(codes.normalize_voucher(code[:-1]))
        self.assertIsNone(codes.normalize_voucher("x" * 500))

    def test_hmac_with_the_pepper_and_never_plain_sha256(self):
        body = codes.normalize_voucher(codes.generate_voucher_code())
        digest = codes.voucher_hash(body, PEPPER)
        self.assertEqual(digest, hmac.new(PEPPER.encode(), b"learnova-voucher:v3:" + body.encode(), hashlib.sha256).hexdigest())
        self.assertNotEqual(digest, codes.voucher_hash(body, PEPPER + "other"))
        self.assertNotIn(digest, (hashlib.sha256(body.encode()).hexdigest(), codes.code_hash(body)))
        with self.assertRaises(ValueError):
            codes.voucher_hash(body, "short")

    def test_calendar_months(self):
        cases = [((2026, 1, 15), (2026, 2, 15)), ((2026, 3, 31), (2026, 4, 30)), ((2026, 8, 31), (2026, 9, 30)),
                 ((2026, 1, 31), (2026, 2, 28)), ((2028, 1, 31), (2028, 2, 29)), ((2026, 12, 15), (2027, 1, 15))]
        for start, end in cases:
            with self.subTest(start=start):
                moment = datetime(*start, 10, 0, tzinfo=BERLIN)
                self.assertEqual(periods.add_months(moment, 1, BERLIN).date(), datetime(*end).date())


# ------------------------------------------------------------------------ generation
class GenerationTests(VoucherCase):
    def test_batches_of_10_50_100_and_a_custom_quantity(self):
        for quantity, custom, expected in (("10", "", 10), ("50", "", 50), ("100", "", 100), ("custom", "37", 37)):
            with self.subTest(quantity=quantity):
                response = self.generate(quantity, custom)
                self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
                self.assertEqual(response.headers.get("Cache-Control"), "no-store, private")
                plain = self.codes_from(response)
                self.assertEqual(len(plain), expected)
                self.assertEqual(len(set(plain)), expected)
        with application.app.app_context():
            batches = application.db.session.scalars(application.db.select(application.PremiumCodeBatch)).all()
            self.assertEqual(sorted(batch.code_count for batch in batches), [10, 37, 50, 100])
            self.assertTrue(all(batch.created_by_id == self.user_id("admin") for batch in batches))
        self.assertEqual(len(self.keys()), 197)

    def test_25_is_a_preset_too(self):
        self.assertEqual(len(self.codes_from(self.generate("25"))), 25)

    def test_invalid_and_too_large_quantities_are_refused(self):
        for quantity, custom in (("0", ""), ("custom", "0"), ("custom", "501"), ("custom", "abc"), ("custom", ""),
                                 ("7", ""), ("-5", ""), ("custom", "1e3")):
            with self.subTest(quantity=quantity, custom=custom):
                response = self.generate(quantity, custom)
                self.assertEqual(self.codes_from(response), [])
        self.assertEqual(self.keys(), [])
        self.assertEqual(len(self.codes_from(self.generate("custom", "500"))), 500, "500 is the maximum")

    def test_a_duplicate_draw_is_drawn_again(self):
        first = codes.generate_voucher_code()
        draws = iter([first, first, first] + [codes.generate_voucher_code() for _ in range(20)])
        with patch.object(codes, "generate_voucher_code", side_effect=lambda: next(draws)):
            plain = self.codes_from(self.generate("10"))
        self.assertEqual(len(set(plain)), 10)
        self.assertEqual(len({key.key_hash for key in self.keys()}), 10)

    def test_only_keyed_hashes_are_stored(self):
        plain = self.codes_from(self.generate("10"))
        keys = self.keys()
        for code in plain:
            body = codes.normalize_voucher(code)
            self.assertIn(codes.voucher_hash(body, PEPPER), {key.key_hash for key in keys})
        database = TEST_DATABASE.read_bytes() if TEST_DATABASE.exists() else b""
        for code in plain:
            self.assertNotIn(codes.normalize_voucher(code).encode(), database)
            self.assertNotIn(code.encode(), database)
        with application.app.app_context():
            audit = " ".join(row.detail_json for row in application.db.session.scalars(
                application.db.select(application.SubscriptionAuditEvent)))
        for code in plain:
            self.assertNotIn(codes.normalize_voucher(code), audit)

    def test_the_codes_are_shown_once_and_never_again(self):
        plain = self.codes_from(self.generate("10"))
        page = self.admin.get("/admin/premium-vouchers").get_data(as_text=True)
        stats = self.admin.get("/admin/premium-vouchers/stats.csv").get_data(as_text=True)
        for code in plain:
            self.assertNotIn(code, page)
            self.assertNotIn(code, stats)
            self.assertNotIn(codes.normalize_voucher(code), page)

    def test_without_a_pepper_no_batch_is_generated(self):
        application.app.config["PREMIUM_VOUCHER_PEPPER"] = ""
        response = self.generate("10")
        self.assertEqual(self.codes_from(response), [])
        self.assertEqual(self.keys(), [])
        self.assertIn("PREMIUM_VOUCHER_PEPPER", self.admin.get("/admin/premium-vouchers").get_data(as_text=True))

    def test_generation_is_audited_without_codes(self):
        plain = self.codes_from(self.generate("10"))
        with application.app.app_context():
            rows = application.db.session.scalars(application.db.select(application.SubscriptionAuditEvent).where(
                application.SubscriptionAuditEvent.action == "voucher_batch_issued")).all()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].actor_user_id, self.user_id("admin"))
        self.assertIn('"count": 10', rows[0].detail_json)
        self.assertFalse(any(code[-9:] in rows[0].detail_json for code in plain))


# ------------------------------------------------------------------------ redemption
class RedemptionTests(VoucherCase):
    def batch(self, count="10"):
        return self.codes_from(self.generate(count))

    def test_a_voucher_activates_one_calendar_month(self):
        code = self.batch()[0]
        before = application.utcnow()
        page = self.redeem(code.lower())
        self.assertIn("Premium activated!", page)
        self.assertIn("Premium is active until", page)
        expected = periods.add_months(before, 1, BERLIN)
        self.assertLess(abs((application.as_utc(self.entitlement().expires_at) - expected).total_seconds()), 60)
        with application.app.app_context():
            key = application.db.session.scalar(application.db.select(application.PremiumKey).where(
                application.PremiumKey.redeemed_by_id == self.user_id()))
            redemption = application.db.session.scalar(application.db.select(application.PremiumRedemption).where(
                application.PremiumRedemption.premium_key_id == key.id))
        self.assertIsNotNone(key.redeemed_at)
        self.assertIsNotNone(redemption.new_expires_at)

    def test_an_active_subscription_is_extended_not_replaced(self):
        self.make_premium(days=10)
        before = application.as_utc(self.entitlement().expires_at)
        self.redeem(self.batch()[0])
        after = application.as_utc(self.entitlement().expires_at)
        self.assertEqual(after, periods.add_months(before, 1, BERLIN))

    def test_invalid_redeemed_and_revoked_vouchers_fail_the_same_way(self):
        plain = self.batch()
        other = self.register("other", application.app.test_client())
        self.assertIn("Premium activated!", self.redeem(plain[0], other))
        with application.app.app_context():
            revoked_id = application.db.session.scalar(application.db.select(application.PremiumKey.id).where(
                application.PremiumKey.code_hint == codes.code_hint(codes.normalize_voucher(plain[1]))))
        self.admin.post(f"/admin/premium-vouchers/{revoked_id}/revoke")
        bogus = "LN-PREM-" + "-".join(["ABCD"] * 7)
        generic = "This code could not be activated. Check it and try again."
        for attempt in (plain[0], plain[1], bogus):
            self.assertIn(generic, self.redeem(attempt), attempt)
        self.assertIsNone(self.entitlement())

    def test_the_same_student_twice_extends_once(self):
        code = self.batch()[0]
        self.redeem(code)
        first = self.entitlement().expires_at
        page = self.redeem(code)
        self.assertIn("already active on your account", page)
        self.assertEqual(self.entitlement().expires_at, first)

    def test_two_students_at_once_only_one_wins(self):
        code = self.batch()[0]
        clients = [self.register(f"racer{index}", application.app.test_client()) for index in range(6)]
        barrier = threading.Barrier(len(clients))

        def attempt(client):
            barrier.wait()
            return self.redeem(code, client)
        with ThreadPoolExecutor(max_workers=len(clients)) as pool:
            pages = list(pool.map(attempt, clients))
        self.assertEqual(sum("Premium activated!" in page for page in pages), 1)
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(application.db.select(
                application.db.func.count(application.PremiumRedemption.id))), 1)
            self.assertEqual(application.db.session.scalar(application.db.select(
                application.db.func.count(application.PremiumEntitlement.id))), 1)

    def test_unauthenticated_redemption_changes_nothing(self):
        code = self.batch()[0]
        response = application.app.test_client().post("/settings/premium/redeem", data={"activation_code": code})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assertTrue(all(key.redeemed_at is None for key in self.keys()))

    def test_a_failure_rolls_everything_back(self):
        code = self.batch()[0]
        with patch.object(application.PremiumRedemption, "__init__", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                with application.app.test_request_context():
                    application.activation.redeem(self.user_id(), code)
        with application.app.app_context():
            application.db.session.rollback()
        self.assertIsNone(self.entitlement())
        self.assertTrue(all(key.redeemed_at is None for key in self.keys()), "the claim was rolled back too")
        self.assertIn("Premium activated!", self.redeem(code), "and the voucher still works")

    def test_brute_force_guessing_is_stopped(self):
        for number in range(5):
            self.redeem("LN-PREM-" + "-".join([f"ABC{number}"] * 7))
        page = self.redeem(self.batch()[0])
        self.assertIn("Too many attempts", page, "even a valid voucher waits once the limit is hit")
        self.assertIsNone(self.entitlement())

    def test_old_single_codes_still_work_with_the_pepper_set(self):
        application.app.config["PREMIUM_VOUCHER_PEPPER"] = ""
        old = self.issue()
        self.assertRegex(old, r"^LN-[0-9A-Z]{5}(?:-[0-9A-Z]{5}){3}$", "a v2 code without the pepper")
        application.app.config["PREMIUM_VOUCHER_PEPPER"] = PEPPER
        self.assertIn("Premium activated!", self.redeem(old))

    def test_a_single_code_issued_with_the_pepper_is_a_voucher(self):
        self.assertRegex(self.issue(), CODE)

    def test_the_settings_form_takes_a_whole_voucher(self):
        page = self.client.get("/settings").get_data(as_text=True)
        length = int(re.search(r'id="activation-code"[^>]*maxlength="(\d+)"', page, re.S).group(1))
        self.assertGreaterEqual(length, len(self.batch()[0]))
        self.assertIn("Redeem a Premium code", page)


# ------------------------------------------------------------------------ administration
class AdminTests(VoucherCase):
    ROUTES = (("get", "/admin/premium-vouchers"), ("post", "/admin/premium-vouchers/generate"),
              ("get", "/admin/premium-vouchers/stats.csv"), ("post", "/admin/premium-vouchers/1/revoke"),
              ("post", "/admin/premium-vouchers/batches/1/revoke-unused"))

    def test_students_and_moderators_get_404_everywhere(self):
        self.generate("10")
        application.app.config["COMMUNITY_MODERATORS"] = {"moderator"}
        self.addCleanup(application.app.config.update, COMMUNITY_MODERATORS=set())
        moderator = self.register("moderator", application.app.test_client())
        for client in (self.client, moderator):
            for method, path in self.ROUTES:
                with self.subTest(path=path):
                    self.assertEqual(getattr(client, method)(path).status_code, 404)
        self.assertTrue(all(key.revoked_at is None for key in self.keys()))
        anonymous = application.app.test_client()
        self.assertEqual(anonymous.get("/admin/premium-vouchers").status_code, 302, "to the login page")

    def test_an_admin_sees_batch_statistics_and_filters(self):
        plain = self.codes_from(self.generate("10"))
        self.redeem(plain[0])
        page = self.admin.get("/admin/premium-vouchers").get_data(as_text=True)
        with application.app.app_context():
            batch = application.db.session.scalar(application.db.select(application.PremiumCodeBatch))
        self.assertIn(batch.public_id, page)
        self.assertRegex(page, r"<td>10</td><td>9</td><td>1</td><td>0</td>")
        redeemed = self.admin.get(f"/admin/premium-vouchers?batch={batch.public_id}&status=redeemed").get_data(as_text=True)
        self.assertIn("student", redeemed, "who redeemed it")
        self.assertEqual(redeemed.count("<td>Redeemed</td>"), 1)
        unused = self.admin.get("/admin/premium-vouchers?status=unused").get_data(as_text=True)
        self.assertEqual(unused.count("<td>Unused</td>"), 9)
        csv = self.admin.get("/admin/premium-vouchers/stats.csv")
        self.assertEqual(csv.status_code, 200)
        self.assertIn(batch.public_id, csv.get_data(as_text=True))
        self.assertIn("text/csv", csv.headers["Content-Type"])

    def test_revoking_unused_never_touches_redeemed(self):
        plain = self.codes_from(self.generate("10"))
        self.redeem(plain[0])
        with application.app.app_context():
            batch = application.db.session.scalar(application.db.select(application.PremiumCodeBatch))
            redeemed = application.db.session.scalar(application.db.select(application.PremiumKey).where(
                application.PremiumKey.redeemed_at.is_not(None)))
        self.admin.post(f"/admin/premium-vouchers/{redeemed.id}/revoke")
        self.admin.post(f"/admin/premium-vouchers/batches/{batch.id}/revoke-unused")
        keys = self.keys()
        self.assertEqual(sum(1 for key in keys if key.revoked_at is not None), 9)
        self.assertTrue(all(key.revoked_at is None for key in keys if key.redeemed_at is not None))
        self.assertTrue(application.has_active_premium_for(self.user_id()) if hasattr(application, "has_active_premium_for")
                        else self.entitlement().status == "active")
        with application.app.app_context():
            actions = [row.action for row in application.db.session.scalars(
                application.db.select(application.SubscriptionAuditEvent))]
        self.assertIn("voucher_batch_revoked", actions)

    def test_csrf_is_required(self):
        # As tests/test_dashboard_delete.py: CSRF is enforced only outside TESTING.
        saved = {key: application.app.config.get(key) for key in ("TESTING", "WTF_CSRF_ENABLED")}
        self.addCleanup(application.app.config.update, **saved)
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True)
        # A form without its token is sent back ("Your form expired", 302) and does nothing.
        self.assertEqual(self.generate("10").status_code, 302)
        self.assertEqual(self.keys(), [])
        self.assertEqual(self.client.post("/settings/premium/redeem", data={"activation_code": "x"}).status_code, 302)
        with application.app.app_context():
            failures = application.db.session.scalar(application.db.select(application.db.func.count(
                application.SubscriptionAuditEvent.id)).where(application.SubscriptionAuditEvent.action == "code_redeem_failed"))
        self.assertEqual(failures, 0, "the redemption never ran")
        page = self.admin.get("/admin/premium-vouchers").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        accepted = self.admin.post("/admin/premium-vouchers/generate", data={
            "quantity": "10", "months": "1", "csrf_token": token})
        self.assertEqual(len(self.codes_from(accepted)), 10)

    def test_the_pepper_never_reaches_a_page_or_a_log(self):
        with self.assertLogs(application.app.logger, level="DEBUG") as logs:
            application.app.logger.debug("start")
            plain = self.codes_from(self.generate("10"))
            self.redeem(plain[0])
            self.redeem("LN-PREM-" + "-".join(["ZZZZ"] * 7))
        pages = "".join(client.get(path).get_data(as_text=True) for client, path in (
            (self.admin, "/admin/premium-vouchers"), (self.client, "/settings"), (self.admin, "/settings")))
        text = "\n".join(logs.output)
        self.assertNotIn(PEPPER, pages + text)
        for code in plain:
            self.assertNotIn(codes.normalize_voucher(code), text)
            self.assertNotIn(code, text)

    def test_a_student_sees_only_their_own_codes(self):
        plain = self.codes_from(self.generate("10"))
        other = self.register("other", application.app.test_client())
        self.redeem(plain[0], other)
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertNotIn("…" + codes.code_hint(codes.normalize_voucher(plain[0])), page)

    def test_generation_is_rate_limited(self):
        # As tests/test_analysis_steps.py: limits apply outside TESTING.
        saved = application.limiter.enabled
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=False)
        application.limiter.enabled = True
        application.limiter.reset()
        try:
            statuses = [self.generate("10").status_code for _ in range(21)]
        finally:
            application.app.config.update(TESTING=True)
            application.limiter.enabled = saved
            application.limiter.reset()
        self.assertEqual(statuses[:20], [200] * 20)
        # The limiter's refusal of a form is "Too many requests" and a redirect (app 429 handler).
        self.assertEqual(statuses[20], 302, "20 generations an hour per admin")
        self.assertEqual(len(self.keys()), 200, "the 21st generated nothing")


class MigrationTests(unittest.TestCase):
    def test_an_old_database_gets_the_batch_column_once(self):
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
            # SQLite cannot drop a column with a foreign key: rebuild the table without it.
            names = ", ".join(column["name"] for column in application.inspect(application.db.engine).get_columns(
                "premium_key") if column["name"] != "batch_id")
            with application.db.engine.begin() as connection:
                connection.execute(application.text("PRAGMA foreign_keys=OFF"))
                connection.execute(application.text(f"CREATE TABLE premium_key_old AS SELECT {names} FROM premium_key"))
                connection.execute(application.text("DROP TABLE premium_key"))
                connection.execute(application.text("ALTER TABLE premium_key_old RENAME TO premium_key"))
                connection.execute(application.text("DELETE FROM schema_migration WHERE version = '049_premium_vouchers'"))
            application.db.session.remove()
            application.db.engine.dispose()
            application.ensure_database()
            columns = {column["name"] for column in application.inspect(application.db.engine).get_columns("premium_key")}
            self.assertIn("batch_id", columns)
            recorded = application.db.session.scalars(application.db.select(application.SchemaMigration.version).where(
                application.SchemaMigration.version == "049_premium_vouchers")).all()
            self.assertEqual(len(recorded), 1)
            application.ensure_database()


if __name__ == "__main__":
    unittest.main()

"""Login & security: Google, Apple, phone codes, onboarding, linking, notifications.

The providers are simulated, not skipped: ID tokens are really signed (RS256, a key made
here) and really verified by learnova/authentication/oidc.py; Apple's client secret is
really signed (ES256). Only the network is replaced - the token endpoint answer
(`requests.post`), the provider's published key (`oidc.signing_key`) and the SMS send.
End-to-end tests against Google, Apple and an SMS provider need real credentials
(docs/AUTHENTICATION.md).
"""

import json
import os
import re
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_auth_providers_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import jwt  # noqa: E402
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, rsa  # noqa: E402

import app as application  # noqa: E402
from learnova.authentication import notices, oidc, phone  # noqa: E402

from tests.test_diagnostics_integration import keep_app_config  # noqa: E402

RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_PEM = ec.generate_private_key(ec.SECP256R1()).private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
CONFIG = {
    "GOOGLE_CLIENT_ID": "google-client.apps.googleusercontent.com", "GOOGLE_CLIENT_SECRET": "google-secret",
    "APPLE_CLIENT_ID": "com.learnova.web", "APPLE_TEAM_ID": "TEAM123456", "APPLE_KEY_ID": "KEY1234567",
    "APPLE_PRIVATE_KEY": EC_PEM, "SMS_PROVIDER": "twilio", "SMS_API_KEY": "AC123", "SMS_API_SECRET": "sms-secret",
    "SMS_SENDER": "+4915100000000", "MAIL_SMTP_HOST": "", "MAIL_FROM": "", "PUBLIC_BASE_URL": "",
    "AUTH_NEW_DEVICE_ALERTS": True, "AUTH_WELCOME_SMS": False, "WTF_CSRF_ENABLED": False,
}
ISSUER = {"google": "https://accounts.google.com", "apple": "https://appleid.apple.com"}


def id_token(provider, *, nonce, sub="sub-1", email="", verified=True, audience=None, issued=None, **extra):
    now = int(issued if issued is not None else time.time())
    claims = {"iss": ISSUER[provider], "aud": audience or CONFIG["GOOGLE_CLIENT_ID" if provider == "google" else
                                                                 "APPLE_CLIENT_ID"],
              "sub": sub, "iat": now, "exp": now + 600, "nonce": nonce, **extra}
    if email:
        claims.update(email=email, email_verified=verified if provider == "google" else ("true" if verified else "false"))
    return jwt.encode(claims, RSA_KEY, algorithm="RS256", headers={"kid": "test-key"})


class TokenEndpoint:
    """requests.post stand-in for the token endpoint: hands back the prepared ID token."""

    def __init__(self):
        self.token = ""
        self.calls = []

    def __call__(self, url, data=None, **kwargs):
        self.calls.append({"url": url, "data": dict(data or {})})
        response = mock.Mock(status_code=200)
        response.json.return_value = {"id_token": self.token, "access_token": "never-stored", "refresh_token": "nope"}
        return response


class SmsBox:
    def __init__(self, accept=True):
        self.sent = []
        self.accept = accept

    def __call__(self, config, to, text, **kwargs):
        self.sent.append((to, text))
        return phone.SmsResult("sent" if self.accept else "failed", "test")

    def last_code(self):
        return re.search(r"\b(\d{6})\b", self.sent[-1][1]).group(1)


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        keep_app_config(self, *CONFIG, "TESTING", "AI_MODE")
        application.app.config.update(TESTING=True, **CONFIG)
        application.limiter.enabled = False
        self.addCleanup(setattr, application.limiter, "enabled", True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.endpoint = TokenEndpoint()
        self.sms = SmsBox()
        for patcher in (mock.patch("requests.post", self.endpoint),
                        mock.patch.object(oidc, "signing_key", lambda provider, token: RSA_KEY.public_key()),
                        mock.patch.object(phone, "send_sms", self.sms)):
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------- helpers
    def register_password_user(self, name="anna", client=None):
        client = client or self.client
        response = client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                                   "password": "correct-horse-battery", "language": "de", "grade": "8"})
        self.assertEqual(response.status_code, 302)
        return self.user(name)

    def user(self, name):
        with application.app.app_context():
            return application.db.session.scalar(application.db.select(application.User).where(
                application.User.username == name))

    def start(self, provider, client=None, **params):
        client = client or self.client
        response = client.get(f"/auth/{provider}/start", query_string=params)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        query = parse_qs(urlsplit(response.headers["Location"]).query)
        return query["state"][0], query["nonce"][0], query

    def sign_in(self, provider, *, client=None, sub="sub-1", email="", verified=True, user_field=None,
                params=None, token_kwargs=None):
        """Start, come back from the provider, finish. Returns the final redirect response."""

        client = client or self.client
        state, nonce, _query = self.start(provider, client, **(params or {}))
        self.endpoint.token = id_token(provider, nonce=nonce, sub=sub, email=email, verified=verified,
                                       **(token_kwargs or {}))
        if provider == "apple":
            data = {"state": state, "code": "auth-code"}
            if user_field:
                data["user"] = json.dumps(user_field)
            back = client.post("/auth/apple/callback", data=data)
        else:
            back = client.get("/auth/google/callback", query_string={"state": state, "code": "auth-code"})
        self.assertEqual(back.status_code, 303, back.get_data(as_text=True))
        return client.get(back.headers["Location"])

    def onboard(self, client=None, *, name="Mia", username="mia.study", grade="8", language="de"):
        client = client or self.client
        client.post("/auth/onboarding", data={"step": "1", "display_name": name, "username": username})
        client.post("/auth/onboarding", data={"step": "2", "grade": grade, "language": language,
                                              "interests": ["Mathematics", "Biology"]})
        return client.post("/auth/onboarding", data={"step": "3"})

    def phone_login(self, number="0151 23456789", client=None):
        client = client or self.client
        response = client.post("/auth/phone", data={"region": "DE", "phone": number})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        return client.post("/auth/phone/code", data={"code": self.sms.last_code()})

    def identities(self, user_id):
        with application.app.app_context():
            return {row.provider: row.provider_subject for row in application.db.session.scalars(
                application.db.select(application.UserAuthIdentity).where(
                    application.UserAuthIdentity.user_id == user_id))}

    def logged_in_as(self, client=None):
        response = (client or self.client).get("/settings")
        if response.status_code != 200:
            return None
        match = re.search(r"@([a-z0-9_.-]+) · ", response.get_data(as_text=True))
        return match.group(1) if match else None

    def notifications(self, user_id=None):
        with application.app.app_context():
            query = application.db.select(application.AccountNotification)
            if user_id is not None:
                query = query.where(application.AccountNotification.user_id == user_id)
            return [(row.kind, row.channel, row.status) for row in application.db.session.scalars(
                query.order_by(application.AccountNotification.id))]


# --------------------------------------------------------------------- password
class PasswordLoginTests(AuthTestCase):
    def test_existing_password_login_still_works_and_regenerates_the_session(self):
        self.register_password_user()
        self.client.post("/logout")
        with self.client.session_transaction() as session:
            session["planted"] = "attacker-chosen"
        response = self.client.post("/login", data={"identifier": "anna", "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.assertNotIn("planted", session)          # nothing from before the sign-in survives
            self.assertIn("auth_at", session)
        self.assertEqual(self.logged_in_as(), "anna")

    def test_wrong_password_and_unusable_hash(self):
        user = self.register_password_user()
        self.client.post("/logout")
        response = self.client.post("/login", data={"identifier": "anna", "password": "wrong-password"})
        self.assertIsNone(self.logged_in_as())
        self.assertEqual(response.status_code, 200)
        with application.app.app_context():
            account = application.db.session.get(application.User, user.id)
            account.clear_password()
            self.assertFalse(account.check_password("!unusable"))
            self.assertFalse(account.check_password(""))
            self.assertFalse(account.has_password())

    def test_registration_sends_one_welcome_and_leaves_the_email_unconfirmed(self):
        user = self.register_password_user()
        self.assertIsNone(user.email_verified_at)
        # Email is not configured here: stored as not_configured, never reported as sent.
        self.assertEqual(self.notifications(user.id), [("welcome", "email", "not_configured")])
        with application.app.app_context():
            account = application.db.session.get(application.User, user.id)
            application.queue_account_notice(account, "welcome", f"welcome:{account.id}")   # a second attempt
        self.assertEqual(len(self.notifications(user.id)), 1)

    def test_the_confirmation_link_verifies_the_email(self):
        user = self.register_password_user()
        with application.app.test_request_context():
            account = application.db.session.get(application.User, user.id)
            link = application.verification_link(account)
        self.client.get(urlsplit(link).path)
        self.assertIsNotNone(self.user("anna").email_verified_at)

    def test_redirect_after_login_stays_on_this_site(self):
        self.register_password_user()
        self.client.post("/logout")
        response = self.client.post("/login", data={"identifier": "anna", "password": "correct-horse-battery",
                                                    "next": "https://evil.example/steal"})
        self.assertNotIn("evil.example", response.headers["Location"])


# --------------------------------------------------------------------- Google & Apple
class GoogleTests(AuthTestCase):
    def test_the_authorization_request_is_complete(self):
        state, nonce, query = self.start("google")
        self.assertEqual(query["scope"], ["openid email profile"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertTrue(state and nonce)
        self.assertIn("learnova_oauth", self.client.get_cookie("learnova_oauth", path="/auth/").key)

    def test_first_time_google_sign_in_goes_through_onboarding(self):
        response = self.sign_in("google", sub="g-100", email="mia@gmail.com", token_kwargs={"given_name": "Mia"})
        self.assertIn("/auth/onboarding", response.headers["Location"])
        self.assertIsNone(self.logged_in_as())                    # no account yet
        page = self.client.get("/auth/onboarding").get_data(as_text=True)
        self.assertIn('value="Mia"', page)                        # name from Google, not asked again
        finished = self.onboard(username="mia.study")
        self.assertIn("/dashboard", finished.headers["Location"])
        user = self.user("mia.study")
        self.assertEqual((user.email, user.display_name, user.grade), ("mia@gmail.com", "Mia", "8"))
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(user.has_password())
        self.assertEqual(self.identities(user.id), {"google": "g-100"})
        self.assertEqual(self.notifications(user.id), [("welcome", "email", "not_configured")])
        # The token endpoint got the PKCE verifier; the access/refresh tokens were thrown away.
        self.assertIn("code_verifier", self.endpoint.calls[-1]["data"])
        with application.app.app_context():
            self.assertNotIn("never-stored", json.dumps([row.result_json for row in application.db.session.scalars(
                application.db.select(application.OAuthRequest))]))

    def test_returning_google_user_signs_in_to_the_same_account(self):
        self.sign_in("google", sub="g-100", email="mia@gmail.com")
        self.onboard()
        user_id = self.user("mia.study").id
        self.client.post("/logout")
        response = self.sign_in("google", sub="g-100", email="mia@gmail.com")
        self.assertNotIn("onboarding", response.headers["Location"])
        self.assertEqual(self.logged_in_as(), "mia.study")
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(application.db.select(
                application.db.func.count(application.User.id))), 1)
            self.assertEqual(self.user("mia.study").id, user_id)

    def test_a_matching_email_is_never_linked_silently(self):
        owner = self.register_password_user("anna")
        self.client.post("/logout")
        response = self.sign_in("google", sub="g-anna", email="anna@example.com")
        self.assertIn("/login", response.headers["Location"])
        self.assertIsNone(self.logged_in_as())
        self.assertEqual(self.identities(owner.id), {})
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(application.db.select(
                application.db.func.count(application.PendingSignup.id))), 0)

    def test_tampered_or_replayed_returns_are_refused(self):
        state, nonce, _ = self.start("google")
        # A state this browser never started.
        bad = self.client.get("/auth/google/callback", query_string={"state": "forged", "code": "x"})
        self.assertIn("/login", bad.headers["Location"])
        # Another browser (no binding cookie) cannot use this browser's state.
        stranger = application.app.test_client()
        self.endpoint.token = id_token("google", nonce=nonce, sub="g-1", email="x@gmail.com")
        other = stranger.get("/auth/google/callback", query_string={"state": state, "code": "x"})
        self.assertIn("/login", other.headers["Location"])
        # The right browser once - and never twice.
        first = self.client.get("/auth/google/callback", query_string={"state": state, "code": "x"})
        self.assertEqual(first.status_code, 303)
        again = self.client.get("/auth/google/callback", query_string={"state": state, "code": "x"})
        self.assertIn("/login", again.headers["Location"])

    def test_tokens_that_do_not_hold_up_are_refused(self):
        cases = {
            "nonce": {"nonce_override": "someone-else"},
            "audience": {"audience": "another-client"},
            "expired": {"issued": time.time() - 7200},
        }
        for name, change in cases.items():
            with self.subTest(name):
                state, nonce, _ = self.start("google")
                kwargs = {key: value for key, value in change.items() if key != "nonce_override"}
                self.endpoint.token = id_token("google", nonce=change.get("nonce_override", nonce), sub="g-x",
                                               email="x@gmail.com", **kwargs)
                response = self.client.get("/auth/google/callback", query_string={"state": state, "code": "c"})
                self.assertIn("/login", response.headers["Location"])
                self.assertIsNone(self.logged_in_as())

    def test_unsigned_tokens_are_refused(self):
        token = jwt.encode({"iss": ISSUER["google"], "aud": CONFIG["GOOGLE_CLIENT_ID"], "sub": "x", "nonce": "n",
                            "iat": int(time.time()), "exp": int(time.time()) + 60}, "secret", algorithm="HS256")
        with self.assertRaises(oidc.OIDCError) as raised:
            oidc.verify_id_token("google", token, audience=CONFIG["GOOGLE_CLIENT_ID"], nonce="n",
                                 key_resolver=lambda provider, token: RSA_KEY.public_key())
        self.assertEqual(raised.exception.code, "unexpected_algorithm")

    def test_a_provider_without_credentials_is_not_offered(self):
        application.app.config["GOOGLE_CLIENT_SECRET"] = ""
        self.assertEqual(self.client.get("/auth/google/start").status_code, 404)
        self.assertNotIn("Continue with Google", self.client.get("/login").get_data(as_text=True))
        self.assertIn("Continue with Apple", self.client.get("/login").get_data(as_text=True))


class AppleTests(AuthTestCase):
    def test_first_time_apple_keeps_the_relay_address_and_the_name_from_the_first_authorisation(self):
        response = self.sign_in("apple", sub="a-1", email="x1y2@privaterelay.appleid.com",
                                user_field={"name": {"firstName": "Emanuel", "lastName": "S."}})
        self.assertIn("/auth/onboarding", response.headers["Location"])
        self.assertIn('value="Emanuel"', self.client.get("/auth/onboarding").get_data(as_text=True))
        self.onboard(name="Emanuel", username="emanuel.sc")
        user = self.user("emanuel.sc")
        self.assertEqual(user.email, "x1y2@privaterelay.appleid.com")
        self.assertEqual(self.identities(user.id), {"apple": "a-1"})
        # The client secret Apple got is a real ES256 token for this team and key.
        secret = self.endpoint.calls[-1]["data"]["client_secret"]
        header = jwt.get_unverified_header(secret)
        self.assertEqual((header["alg"], header["kid"]), ("ES256", CONFIG["APPLE_KEY_ID"]))

    def test_returning_apple_user_without_name_or_email(self):
        self.sign_in("apple", sub="a-1", email="x1y2@privaterelay.appleid.com",
                     user_field={"name": {"firstName": "Emanuel"}})
        self.onboard(name="Emanuel", username="emanuel.sc")
        self.client.post("/logout")
        self.sign_in("apple", sub="a-1")                         # Apple sends neither name nor email again
        self.assertEqual(self.logged_in_as(), "emanuel.sc")


# --------------------------------------------------------------------- phone
class PhoneTests(AuthTestCase):
    def test_phone_registration_and_return(self):
        response = self.phone_login()
        self.assertIn("/auth/onboarding", response.headers["Location"])
        self.assertEqual(self.sms.sent[-1][0], "+4915123456789")           # E.164
        self.onboard(name="Nathan", username="nathan7")
        user = self.user("nathan7")
        self.assertTrue(user.email.endswith("@users.invalid"))
        self.assertFalse(user.has_real_email())
        self.assertEqual(self.identities(user.id), {"phone": "+4915123456789"})
        self.assertEqual(self.notifications(user.id), [])            # no email; welcome SMS is off by default
        self.client.post("/logout")
        self.client.post("/auth/phone", data={"region": "DE", "phone": "+49 151 23456789"})
        with application.app.app_context():
            application.db.session.execute(application.update(application.PhoneVerification).values(
                created_at=application.utcnow() - timedelta(minutes=2)))   # past the resend cooldown
            application.db.session.commit()
        self.client.post("/auth/phone", data={"region": "DE", "phone": "+49 151 23456789"})
        self.client.post("/auth/phone/code", data={"code": self.sms.last_code()})
        self.assertEqual(self.logged_in_as(), "nathan7")

    def test_the_code_is_stored_only_as_a_keyed_hash_and_never_logged(self):
        with self.assertLogs(application.app.logger, level="DEBUG") as logs:
            application.app.logger.debug("start")
            self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"})
        code = self.sms.last_code()
        with application.app.app_context():
            row = application.db.session.scalar(application.db.select(application.PhoneVerification))
            self.assertNotIn(code, row.code_hash)
            self.assertEqual(len(row.code_hash), 64)
        self.assertFalse(any(code in line for line in logs.output))
        self.assertNotIn(code, self.client.get("/auth/phone/code").get_data(as_text=True))

    def test_a_code_expires(self):
        self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"})
        code = self.sms.last_code()
        later = application.utcnow() + timedelta(seconds=301)
        with mock.patch.object(application, "utcnow", lambda: later):
            self.client.post("/auth/phone/code", data={"code": code})
        self.assertIsNone(self.logged_in_as())
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(application.db.select(
                application.db.func.count(application.PendingSignup.id))), 0)

    def test_a_code_works_once(self):
        self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"})
        code = self.sms.last_code()
        with self.client.session_transaction() as session:
            key = session["phone_challenge"]
        self.client.post("/auth/phone/code", data={"code": code})
        with self.client.session_transaction() as session:
            session["phone_challenge"] = key                     # replaying the same challenge
        replay = self.client.post("/auth/phone/code", data={"code": code})
        self.assertIn("/auth/phone", replay.headers["Location"])

    def test_wrong_guesses_lock_the_code(self):
        self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"})
        code = self.sms.last_code()
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.client.post("/auth/phone/code", data={"code": wrong})
        self.client.post("/auth/phone/code", data={"code": code})          # the right one, too late
        self.assertIsNone(self.logged_in_as())
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(application.db.select(
                application.PhoneVerification.attempts)), 5)

    def test_resend_cooldown_and_hourly_limit(self):
        self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"})
        again = self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"},
                                 follow_redirects=True)
        self.assertEqual(len(self.sms.sent), 1)
        self.assertIn("wait a minute", again.get_data(as_text=True))

    def test_the_answer_does_not_reveal_whether_a_number_has_an_account(self):
        self.phone_login("0151 23456789")
        self.onboard(username="nathan7")
        self.client.post("/logout")
        with application.app.app_context():
            application.db.session.execute(application.update(application.PhoneVerification).values(
                created_at=application.utcnow() - timedelta(minutes=2)))   # past the resend cooldown
            application.db.session.commit()
        known = self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"})
        unknown = self.client.post("/auth/phone", data={"region": "DE", "phone": "0152 34567890"})
        self.assertEqual(urlsplit(known.headers["Location"]).path, urlsplit(unknown.headers["Location"]).path)

    def test_a_failed_sms_is_reported_and_its_code_is_dead(self):
        self.sms.accept = False
        response = self.client.post("/auth/phone", data={"region": "DE", "phone": "0151 23456789"},
                                    follow_redirects=True)
        self.assertIn("could not be sent", response.get_data(as_text=True))
        with application.app.app_context():
            self.assertIsNotNone(application.db.session.scalar(application.db.select(
                application.PhoneVerification.consumed_at)))

    def test_invalid_numbers_are_refused(self):
        response = self.client.post("/auth/phone", data={"region": "DE", "phone": "12"}, follow_redirects=True)
        self.assertIn("valid mobile number", response.get_data(as_text=True))
        self.assertEqual(self.sms.sent, [])
        self.assertEqual(phone.normalize("0151 23456789", "DE"), "+4915123456789")
        self.assertEqual(phone.normalize("+33 6 12 34 56 78", "DE"), "+33612345678")

    def test_without_an_sms_provider_phone_login_is_not_offered(self):
        application.app.config["SMS_PROVIDER"] = ""
        self.assertEqual(self.client.get("/auth/phone").status_code, 404)
        self.assertNotIn("Continue with phone number", self.client.get("/login").get_data(as_text=True))
        self.assertEqual(phone.sms_provider(application.app.config), "")


# --------------------------------------------------------------------- onboarding
class OnboardingTests(AuthTestCase):
    def test_the_dashboard_cannot_be_reached_before_the_profile_is_finished(self):
        self.sign_in("google", sub="g-1", email="mia@gmail.com")
        for path in ("/dashboard", "/", "/settings", "/projects"):
            with self.subTest(path=path):
                self.assertIn("/auth/onboarding", self.client.get(path).headers["Location"])

    def test_progress_survives_a_closed_browser(self):
        self.sign_in("google", sub="g-1", email="mia@gmail.com")
        self.client.post("/auth/onboarding", data={"step": "1", "display_name": "Mia", "username": "mia.study"})
        fresh = application.app.test_client()                    # the browser was closed
        self.sign_in("google", client=fresh, sub="g-1", email="mia@gmail.com")
        page = fresh.get("/auth/onboarding").get_data(as_text=True)
        self.assertIn("How do you learn?", page)                 # step 2, name and username kept

    def test_usernames_are_unique_ignoring_case_and_checked_live(self):
        self.register_password_user("nathan7")
        self.client.post("/logout")
        self.sign_in("google", sub="g-2", email="n@gmail.com")
        taken = self.client.get("/auth/username-check", query_string={"name": "Nathan7"}).get_json()
        self.assertFalse(taken["ok"])
        self.assertTrue(self.client.get("/auth/username-check", query_string={"name": "nathan.7"}).get_json()["ok"])
        self.assertFalse(self.client.get("/auth/username-check", query_string={"name": "a"}).get_json()["ok"])
        response = self.client.post("/auth/onboarding", data={"step": "1", "display_name": "N", "username": "NATHAN7"})
        self.assertIn("already", response.get_data(as_text=True).lower())

    def test_nothing_is_created_until_the_last_step(self):
        self.sign_in("google", sub="g-1", email="mia@gmail.com")
        self.client.post("/auth/onboarding", data={"step": "1", "display_name": "Mia", "username": "mia.study"})
        self.client.post("/auth/onboarding", data={"step": "2", "grade": "8", "language": "de"})
        self.assertIsNone(self.user("mia.study"))
        self.client.post("/auth/onboarding", data={"step": "3"})
        self.assertIsNotNone(self.user("mia.study"))


# --------------------------------------------------------------------- linking
class LinkingTests(AuthTestCase):
    def seed_progress(self, user_id):
        with application.app.app_context():
            db = application.db
            application.record_learning_event(user_id, "quiz_completed", "seed-1", metadata={"correct": True}, xp=40)
            db.session.add(application.LearningProject(user_id=user_id, title="Bio", subject="Biology"))
            now = application.utcnow()
            db.session.add(application.PremiumEntitlement(user_id=user_id, status="active", starts_at=now,
                                                          expires_at=now + timedelta(days=30), granted_by="code"))
            membership = db.session.get(application.LeagueMembership, user_id)   # the XP above may have made it
            if membership is None:
                membership = application.LeagueMembership(user_id=user_id)
                db.session.add(membership)
            membership.tier = 3
            db.session.commit()

    def progress(self, user_id):
        with application.app.app_context():
            db, select, count = application.db, application.db.select, application.db.func.count
            return {
                "xp": db.session.scalar(select(db.func.coalesce(db.func.sum(application.XPTransaction.amount), 0)).where(
                    application.XPTransaction.user_id == user_id)),
                "projects": db.session.scalar(select(count(application.LearningProject.id)).where(
                    application.LearningProject.user_id == user_id)),
                "badges": db.session.scalar(select(count(application.UserBadge.id)).where(
                    application.UserBadge.user_id == user_id)),
                "premium": db.session.scalar(select(application.PremiumEntitlement.status).where(
                    application.PremiumEntitlement.user_id == user_id)),
                "league": db.session.scalar(select(application.LeagueMembership.tier).where(
                    application.LeagueMembership.user_id == user_id)),
                "username": db.session.get(application.User, user_id).username,
            }

    def test_connecting_google_keeps_the_account_and_all_its_progress(self):
        user = self.register_password_user("anna")
        self.seed_progress(user.id)
        before = self.progress(user.id)
        connect = self.client.post("/settings/security/google/connect")
        self.assertIn("/auth/google/start", connect.headers["Location"])
        self.sign_in("google", sub="g-anna", email="anna.private@gmail.com", params={"purpose": "link"})
        self.assertEqual(self.identities(user.id), {"google": "g-anna"})
        self.assertEqual(self.progress(user.id), before)
        self.assertIn(("provider_linked", "email", "not_configured"), self.notifications(user.id))
        self.client.post("/logout")
        self.sign_in("google", sub="g-anna", email="anna.private@gmail.com")
        self.assertEqual(self.logged_in_as(), "anna")             # Google now opens the same account

    def test_apple_and_phone_link_too(self):
        user = self.register_password_user("anna")
        self.sign_in("apple", sub="a-anna", params={"purpose": "link"})
        self.client.post("/settings/security/phone", data={"region": "DE", "phone": "0151 23456789"})
        self.client.post("/auth/phone/code", data={"code": self.sms.last_code()})
        self.assertEqual(self.identities(user.id), {"apple": "a-anna", "phone": "+4915123456789"})

    def test_an_identity_of_another_account_is_refused(self):
        self.sign_in("google", sub="g-shared", email="mia@gmail.com")
        self.onboard(username="mia.study")
        self.client.post("/logout")
        anna = self.register_password_user("anna")
        page = self.sign_in("google", sub="g-shared", params={"purpose": "link"})
        self.assertIn("#security", page.headers["Location"])
        self.assertEqual(self.identities(anna.id), {})
        self.assertEqual(self.identities(self.user("mia.study").id), {"google": "g-shared"})

    def test_linking_needs_a_recent_sign_in_or_the_password(self):
        self.register_password_user("anna")
        with self.client.session_transaction() as session:
            session["auth_at"] = time.time() - 3600
        refused = self.client.post("/settings/security/google/connect")
        self.assertIn("#security", refused.headers["Location"])
        allowed = self.client.post("/settings/security/google/connect", data={"password": "correct-horse-battery"})
        self.assertIn("/auth/google/start", allowed.headers["Location"])

    def test_disconnecting_needs_confirmation_and_never_removes_the_last_way_in(self):
        self.sign_in("google", sub="g-1", email="mia@gmail.com")
        self.onboard(username="mia.study")
        user = self.user("mia.study")
        self.client.post("/settings/security/google/disconnect", data={"confirm": "1"})
        self.assertEqual(self.identities(user.id), {"google": "g-1"})   # the only way in
        self.client.post("/settings/security/password", data={"new_password": "another-good-password"})
        self.client.post("/settings/security/google/disconnect")          # no confirmation
        self.assertEqual(self.identities(user.id), {"google": "g-1"})
        self.client.post("/settings/security/google/disconnect", data={"confirm": "1"})
        self.assertEqual(self.identities(user.id), {})
        kinds = [kind for kind, _channel, _status in self.notifications(user.id)]
        self.assertIn("password_changed", kinds)
        self.assertIn("provider_unlinked", kinds)
        with application.app.app_context():
            actions = [row.action for row in application.db.session.scalars(application.db.select(
                application.AuthAuditEvent).where(application.AuthAuditEvent.user_id == user.id))]
        self.assertEqual(actions, ["signup_google", "password_set", "unlink_google"])

    def test_one_identity_can_never_belong_to_two_accounts(self):
        a, b = self.register_password_user("anna"), None
        self.client.post("/logout")
        b = self.register_password_user("bert")
        with application.app.app_context():
            db = application.db
            db.session.add(application.UserAuthIdentity(user_id=a.id, provider="google", provider_subject="same"))
            db.session.commit()
            db.session.add(application.UserAuthIdentity(user_id=b.id, provider="google", provider_subject="same"))
            with self.assertRaises(application.IntegrityError):
                db.session.commit()
            db.session.rollback()

    def test_settings_shows_every_method(self):
        self.register_password_user("anna")
        page = self.client.get("/settings").get_data(as_text=True)
        # Anna registered in German, so the page is in German.
        for text in ("Login &amp; Sicherheit", "Mit Google verbinden", "Mit Apple verbinden", "Telefonnummer hinzufügen",
                     "Passwort ändern", "Noch nicht bestätigt"):
            self.assertIn(text, page)

    def test_state_changing_requests_need_the_csrf_token(self):
        self.register_password_user("anna")
        keep_app_config(self, "COMMUNITY_BACKGROUND_JOBS")
        # Flask-WTF skips the check while TESTING is on (as tests/test_about_contact.py does).
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True, COMMUNITY_BACKGROUND_JOBS=False)
        for path in ("/settings/security/google/connect", "/settings/security/google/disconnect",
                     "/settings/security/password", "/settings/security/phone"):
            with self.subTest(path=path):
                # The app's CSRF handler sends a form back with "Your form expired" (not a 400 page).
                response = self.client.post(path, data={"confirm": "1", "new_password": "hijacked-password",
                                                        "region": "DE", "phone": "0151 23456789"})
                self.assertEqual(response.status_code, 302)
                self.assertNotIn("/auth/", response.headers.get("Location", ""))
        self.assertTrue(self.user("anna").check_password("correct-horse-battery"))
        self.assertEqual(self.sms.sent, [])


# --------------------------------------------------------------------- notifications
class NotificationTests(AuthTestCase):
    def test_a_new_device_alerts_once_and_an_ordinary_login_does_not(self):
        user = self.register_password_user("anna")
        self.client.post("/logout")
        self.client.post("/login", data={"identifier": "anna", "password": "correct-horse-battery"})   # same browser
        self.assertNotIn("new_device", [kind for kind, *_ in self.notifications(user.id)])
        laptop = application.app.test_client()
        laptop.post("/login", data={"identifier": "anna", "password": "correct-horse-battery"},
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/130.0 Safari/537.36"})
        laptop.post("/logout")
        laptop.post("/login", data={"identifier": "anna", "password": "correct-horse-battery"})
        self.assertEqual([kind for kind, *_ in self.notifications(user.id)].count("new_device"), 1)

    def test_delivery_records_what_really_happened(self):
        application.app.config.update(MAIL_SMTP_HOST="smtp.example.com", MAIL_FROM="hello@learnova.example")
        outcomes = iter([application.notifications.DeliveryResult("retry", "timeout"),
                         application.notifications.DeliveryResult("sent", message_id="<m@x>")])
        sent = []

        def fake_send(settings, recipient, subject, body, **kwargs):
            sent.append((recipient, subject, body))
            return next(outcomes)

        with mock.patch.object(application.notifications, "send_email", fake_send):
            user = self.register_password_user("anna")
            self.assertEqual(self.notifications(user.id), [("welcome", "email", "retry")])
            with application.app.app_context():
                application.db.session.execute(application.update(application.AccountNotification).values(
                    next_attempt_at=application.utcnow() - timedelta(seconds=1)))
                application.db.session.commit()
                application.deliver_due_account_notifications()
        self.assertEqual(self.notifications(user.id), [("welcome", "email", "sent")])
        recipient, subject, body = sent[-1]
        self.assertEqual(recipient, "anna@example.com")
        self.assertIn("/auth/verify-email/", body)              # the confirmation link, made at send time
        self.assertNotIn("correct-horse-battery", body)
        with application.app.app_context():
            stored = application.db.session.scalar(application.db.select(application.AccountNotification.values_json))
        self.assertNotIn("verify-email", stored)                 # no working link sits in the database

    def test_security_messages_carry_no_secrets(self):
        values = {"name": "Mia", "username": "mia.study", "when": "15.11.2026 10:00", "device": "Chrome · Android",
                  "provider": "Google", "phone": "+49 ••• ••• 89", "link": "https://learnova.example/x"}
        for kind in notices.TEMPLATES:
            for language in ("de", "en"):
                subject, body = notices.render_email(kind, language, values)
                with self.subTest(kind=kind, language=language):
                    self.assertFalse(notices.contains_code_like_number(subject + body))
                    self.assertNotIn("{", body)
        self.assertEqual(notices.device_label("Mozilla/5.0 (Linux; Android 14) Chrome/130.0 Mobile Safari/537.36"),
                         "Chrome · Android")

    def test_security_messages_are_rate_limited(self):
        user = self.register_password_user("anna")
        with application.app.app_context():
            account = application.db.session.get(application.User, user.id)
            for number in range(10):
                application.queue_account_notice(account, "password_changed", f"password:{account.id}:{number}")
        statuses = [status for kind, _channel, status in self.notifications(user.id) if kind == "password_changed"]
        self.assertEqual(statuses.count("suppressed"), 4)        # 6 an hour are kept for delivery

    def test_phone_only_accounts_get_security_messages_by_sms(self):
        self.phone_login()
        self.onboard(username="nathan7")
        user = self.user("nathan7")
        self.client.post("/settings/security/password", data={"new_password": "another-good-password"})
        self.assertEqual(self.notifications(user.id), [("password_changed", "sms", "sent")])
        self.assertNotRegex(self.sms.sent[-1][1], r"\b\d{6}\b")

    def test_forgot_password_answers_the_same_and_resets_once(self):
        application.app.config.update(MAIL_SMTP_HOST="smtp.example.com", MAIL_FROM="hello@learnova.example")
        sent = []
        with mock.patch.object(application.notifications, "send_email",
                               lambda settings, recipient, subject, body, **kw: sent.append(body) or
                               application.notifications.DeliveryResult("sent")):
            self.register_password_user("anna")
            self.client.post("/logout")
            known = self.client.post("/auth/forgot", data={"identifier": "anna"}, follow_redirects=True)
            unknown = self.client.post("/auth/forgot", data={"identifier": "nobody"}, follow_redirects=True)
            self.assertEqual(known.status_code, unknown.status_code)
            link = re.search(r"(/auth/reset/\S+)", sent[-1]).group(1)
            self.client.post(link, data={"password": "brand-new-password"})
            self.assertEqual(self.logged_in_as(), "anna")
            self.client.post("/logout")
            self.assertEqual(self.client.get(link).status_code, 302)     # used: the link is dead
        kinds = [kind for kind, *_ in self.notifications(self.user("anna").id)]
        self.assertIn("account_recovered", kinds)


# --------------------------------------------------------------------- migration
class MigrationTests(unittest.TestCase):
    def test_an_existing_database_gains_the_columns_and_keeps_its_rows(self):
        with application.app.app_context():
            db = application.db
            db.drop_all()
            db.create_all()
            user = application.User(username="old.user", email="old@example.com", preferred_language="de", grade="9")
            user.set_password("correct-horse-battery")
            db.session.add(user)
            db.session.commit()
            with db.engine.begin() as connection:
                for column in ("display_name", "subject_interests_json", "email_verified_at"):
                    connection.execute(application.text(f'ALTER TABLE "user" DROP COLUMN {column}'))
                connection.execute(application.text(
                    "DELETE FROM schema_migration WHERE version = '051_login_security'"))
            db.session.remove()
            application.ensure_database()
            columns = {column["name"] for column in application.inspect(db.engine).get_columns("user")}
            self.assertTrue({"display_name", "subject_interests_json", "email_verified_at"} <= columns)
            account = db.session.scalar(db.select(application.User).where(application.User.username == "old.user"))
            self.assertTrue(account.check_password("correct-horse-battery"))
            self.assertEqual((account.display_name, account.email_verified_at), ("", None))
            for table in ("user_auth_identity", "user_phone_verification", "auth_oauth_request", "auth_pending_signup",
                          "account_notification", "auth_known_device", "auth_audit_event"):
                self.assertIn(table, application.inspect(db.engine).get_table_names())


if __name__ == "__main__":
    unittest.main()

"""Content Security Policy, consent-gated AdSense loading, and the Premium no-ads rule.

See learnova/web/security.py and docs/ADSENSE.md. Nothing here contacts Google: what is
tested is what Learnova sends - the headers, and whether the ad code is on the page.
"""

import os
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_ads_csp_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from flask import Flask  # noqa: E402

import app as application  # noqa: E402
from learnova.config import configure_app  # noqa: E402
from learnova.web.security import content_security_policy  # noqa: E402
from tests.typing_helpers import match_group  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
AD_SCRIPT = "pagead2.googlesyndication.com/pagead/js/adsbygoogle.js"
CMP = "google_privacy_messaging"


def directives(policy: str) -> dict[str, str]:
    return {part.split(" ", 1)[0]: part.split(" ", 1)[1] if " " in part else ""
            for part in (item.strip() for item in policy.split(";")) if part}


class AdsTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(TESTING=True, ADSENSE_CMP="", ADSENSE_SHOW_TO_STUDENTS=False,
                                      ADSENSE_CSP_UNSAFE_EVAL=True, CSP_REPORT_URI="")
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def sign_in(self, name="student"):
        self.client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                            "password": "correct-horse-battery", "grade": "8"})

    def make_premium(self, name="student"):
        with application.app.app_context():
            user = application.User.query.filter_by(username=name).one()
            application.db.session.add(application.PremiumEntitlement(
                user_id=user.id, starts_at=application.utcnow(),
                expires_at=application.utcnow() + timedelta(days=30)))
            application.db.session.commit()

    def page(self, path):
        response = self.client.get(path)
        policy = directives(response.headers["Content-Security-Policy"])
        return response, response.get_data(as_text=True), policy


class PolicyTests(AdsTestCase):
    def test_every_script_on_a_page_carries_this_requests_nonce(self):
        for path in ("/about", "/login", "/register"):   # signed out: /login sends members home
            self.check_nonces(path)
        self.sign_in()
        for path in ("/", "/dashboard", "/assistant", "/settings", "/projects"):
            self.check_nonces(path)

    def check_nonces(self, path):
        with self.subTest(path):
            response, html, policy = self.page(path)
            self.assertEqual(response.status_code, 200, f"{path} -> {response.headers.get('Location')}")
            nonce = match_group(r"'nonce-([^']+)'", policy["script-src"])
            tags = re.findall(r"<script[^>]*>", html)
            self.assertTrue(tags)
            self.assertEqual([tag for tag in tags if f'nonce="{nonce}"' not in tag], [])

    def test_inline_scripts_are_no_longer_allowed_and_the_rest_is_unchanged(self):
        _response, _html, policy = self.page("/about")
        self.assertNotIn("'unsafe-inline'", policy["script-src"])
        self.assertNotIn("'unsafe-eval'", policy["script-src"])
        self.assertIn("'strict-dynamic'", policy["script-src"])
        self.assertNotIn("https:", policy["script-src"].replace("https://cdn.jsdelivr.net", ""))
        for name, value in (("default-src", "'self'"), ("object-src", "'none'"), ("frame-ancestors", "'none'"),
                            ("base-uri", "'self'"), ("form-action", "'self'"), ("connect-src", "'self'")):
            self.assertEqual(policy[name], value, name)
        self.assertNotIn("frame-src", policy)

    def test_camera_and_microphone_are_ours_only_and_location_is_off(self):
        # microphone=() used to switch speech input off on every page.
        policy = self.client.get("/about").headers["Permissions-Policy"]
        self.assertEqual(policy, "camera=(self), microphone=(self), geolocation=()")

    def test_the_nonce_changes_on_every_request(self):
        first = self.client.get("/about").headers["Content-Security-Policy"]
        second = self.client.get("/about").headers["Content-Security-Policy"]
        self.assertNotEqual(match_group(r"('nonce-[^']+')", first), match_group(r"('nonce-[^']+')", second))

    def test_api_responses_get_the_strict_policy(self):
        self.sign_in()
        application.app.config.update(ADSENSE_CMP=CMP, ADSENSE_SHOW_TO_STUDENTS=True)
        response = self.client.get("/api/assistant/conversations")
        policy = directives(response.headers["Content-Security-Policy"])
        self.assertNotIn("frame-src", policy)
        self.assertEqual(response.headers["Referrer-Policy"], "same-origin")

    def test_a_report_uri_is_added_only_when_configured(self):
        self.assertNotIn("report-uri", self.client.get("/about").headers["Content-Security-Policy"])
        application.app.config["CSP_REPORT_URI"] = "https://reports.example.org/csp"
        self.assertIn("report-uri https://reports.example.org/csp",
                      self.client.get("/about").headers["Content-Security-Policy"])

    def test_the_ad_additions_are_exactly_what_google_documents(self):
        strict = directives(content_security_policy("n"))
        ads = directives(content_security_policy("n", ads=True))
        self.assertEqual(ads["script-src"], strict["script-src"] + " https: 'unsafe-eval'")
        self.assertEqual(ads["frame-src"], "'self' https:")
        self.assertEqual(ads["img-src"], strict["img-src"] + " https:")
        self.assertEqual(ads["connect-src"], "'self' https:")
        for name in ("default-src", "object-src", "frame-ancestors", "base-uri", "form-action", "style-src",
                     "font-src", "media-src"):
            self.assertEqual(ads[name], strict[name], name)
        self.assertNotIn("'unsafe-eval'", directives(content_security_policy("n", ads=True, unsafe_eval=False))["script-src"])
        self.assertNotIn("'unsafe-inline'", ads["script-src"])
        self.assertNotIn("http:", ads["script-src"].split())


class ConsentGateTests(AdsTestCase):
    def test_no_ad_code_loads_until_a_certified_consent_message_is_confirmed(self):
        for path in ("/about", "/login", "/register"):
            with self.subTest(path):
                response, html, policy = self.page(path)
                self.assertNotIn(AD_SCRIPT, html)
                self.assertIn('<meta name="google-adsense-account" content="ca-pub-5205912301427741">', html)
                self.assertNotIn("frame-src", policy)
                self.assertEqual(response.headers["Referrer-Policy"], "same-origin")

    def test_with_the_consent_message_confirmed_visitors_get_the_ad_code(self):
        application.app.config["ADSENSE_CMP"] = CMP
        response, html, policy = self.page("/about")
        nonce = match_group(r"'nonce-([^']+)'", policy["script-src"])
        self.assertEqual(html.count(AD_SCRIPT), 1)
        tag = match_group(r'(<script[^>]*adsbygoogle[^>]*>)', html)
        self.assertIn(f'nonce="{nonce}"', tag)
        self.assertIn("client=ca-pub-5205912301427741", tag)
        self.assertEqual(policy["frame-src"], "'self' https:")
        self.assertIn("'unsafe-eval'", policy["script-src"])
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")

    def test_unsafe_eval_can_be_switched_off(self):
        application.app.config.update(ADSENSE_CMP=CMP, ADSENSE_CSP_UNSAFE_EVAL=False)
        _response, _html, policy = self.page("/about")
        self.assertNotIn("'unsafe-eval'", policy["script-src"])

    def test_signed_in_students_get_no_ads_unless_the_operator_switches_them_on(self):
        application.app.config["ADSENSE_CMP"] = CMP
        self.sign_in()
        _response, html, policy = self.page("/dashboard")
        self.assertNotIn(AD_SCRIPT, html)
        self.assertNotIn("frame-src", policy)
        application.app.config["ADSENSE_SHOW_TO_STUDENTS"] = True
        _response, html, policy = self.page("/dashboard")
        self.assertEqual(html.count(AD_SCRIPT), 1)
        self.assertIn("frame-src", policy)

    def test_operator_pages_never_carry_ads(self):
        application.app.config.update(ADSENSE_CMP=CMP, ADSENSE_SHOW_TO_STUDENTS=True,
                                      PREMIUM_ADMIN_IDENTIFIERS={"boss"})
        self.sign_in("boss")
        response, html, policy = self.page("/admin/premium-keys")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(AD_SCRIPT, html)
        self.assertNotIn("frame-src", policy)

    def test_ads_txt_and_the_meta_tag_follow_the_configured_publisher(self):
        self.assertEqual(self.client.get("/ads.txt").get_data(as_text=True),
                         "google.com, pub-5205912301427741, DIRECT, f08c47fec0942fa0\n")
        application.app.config["ADSENSE_CLIENT_ID"] = ""
        self.assertEqual(self.client.get("/ads.txt").status_code, 404)
        self.assertNotIn("google-adsense-account", self.client.get("/about").get_data(as_text=True))


class PremiumNoAdsTests(AdsTestCase):
    def test_premium_never_gets_ads_or_the_ad_policy(self):
        application.app.config.update(ADSENSE_CMP=CMP, ADSENSE_SHOW_TO_STUDENTS=True)
        self.sign_in()
        self.make_premium()
        for path in ("/", "/dashboard", "/about", "/settings", "/assistant"):
            with self.subTest(path):
                response, html, policy = self.page(path)
                self.assertNotIn(AD_SCRIPT, html)
                self.assertNotIn("frame-src", policy)
                self.assertEqual(response.headers["Referrer-Policy"], "same-origin")

    def test_an_expired_premium_gets_the_free_behaviour_again(self):
        application.app.config.update(ADSENSE_CMP=CMP, ADSENSE_SHOW_TO_STUDENTS=True)
        self.sign_in()
        with application.app.app_context():
            user = application.User.query.filter_by(username="student").one()
            application.db.session.add(application.PremiumEntitlement(
                user_id=user.id, starts_at=application.utcnow() - timedelta(days=40),
                expires_at=application.utcnow() - timedelta(days=10)))
            application.db.session.commit()
        self.assertEqual(self.client.get("/dashboard").get_data(as_text=True).count(AD_SCRIPT), 1)


class ConfigurationTests(unittest.TestCase):
    def configure(self, **environment):
        with patch.dict(os.environ, environment):
            flask_app = Flask(__name__)
            configure_app(flask_app, "testing")
            return flask_app.config

    def test_defaults_load_no_ad_code_and_keep_the_existing_publisher(self):
        config = self.configure()
        self.assertEqual(config["ADSENSE_CLIENT_ID"], "ca-pub-5205912301427741")
        self.assertEqual(config["ADSENSE_CMP"], "")
        self.assertFalse(config["ADSENSE_SHOW_TO_STUDENTS"])

    def test_invalid_values_stop_the_start(self):
        for environment in ({"ADSENSE_CMP": "some-banner"}, {"ADSENSE_CLIENT_ID": "pub-123"},
                            {"CSP_REPORT_URI": "http://insecure.example.org"}):
            with self.subTest(environment), self.assertRaises(RuntimeError):
                self.configure(**environment)


class TemplateRuleTests(unittest.TestCase):
    """Static guards, so a new template cannot quietly break the nonce policy."""

    def test_every_template_script_tag_has_the_nonce(self):
        for path in (ROOT / "templates").rglob("*.html"):
            for tag in re.findall(r"<script[\s>][^>]*", path.read_text(encoding="utf-8")):
                with self.subTest(str(path.name)):
                    self.assertIn('nonce="{{ csp_nonce() }}"', tag)

    def test_no_inline_event_handlers_or_javascript_urls(self):
        for path in (ROOT / "templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(str(path.name)):
                self.assertIsNone(re.search(r"\son(click|change|submit|input|load|error|keydown|keyup)\s*=", text))
                self.assertNotIn("javascript:", text)

    def test_no_script_rewrites_the_document(self):
        for path in (ROOT / "static").rglob("*.js"):
            with self.subTest(str(path.name)):
                self.assertNotIn("document.write(", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

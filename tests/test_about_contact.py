"""About and Contact pages: public access, honest contact handling, spam limits, admin inbox.

Every SMTP connection is a mock (`learnova.notifications.mailer.smtplib.SMTP` is patched),
and MAIL_* is emptied in setUp, so nothing here can send a real email.
"""

import json
import os
import re
import smtplib
import tempfile
import time
import unittest
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import MagicMock, patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_about_contact_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import contact as contact_form  # noqa: E402
from learnova import notifications  # noqa: E402
from learnova.config import DEFAULT_COMMUNITY_ALERT_EMAIL, DEFAULT_CONTACT_EMAIL  # noqa: E402
from learnova.translations import catalog  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CONTACT_EMAIL = "contact.studylearnova@gmail.com"
# Built from two halves so a repository-wide search for the retired address stays empty.
OLD_ADDRESS = ("maheswari" + "skillbridge").encode()
PASSWORD = "correct-horse-battery"
FR = json.loads((ROOT / "learnova/translations/data/fr.json").read_text(encoding="utf-8"))
ES = json.loads((ROOT / "learnova/translations/data/es.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{[^{}\s]*\}")
# What /contact/sent must never say: only the operator inbox knows whether a forward worked.
FORWARDING_CLAIMS = ("sent to", "emailed", "forwarded", "a copy is forwarded")


class ContactTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        self.saved_limiter = application.limiter.enabled
        application.app.config.update(
            TESTING=True, WTF_CSRF_ENABLED=False, CONTACT_MIN_FILL_SECONDS=0,
            COMMUNITY_BACKGROUND_JOBS=False, CONTACT_EMAIL=CONTACT_EMAIL,
            CONTACT_MAX_PER_EMAIL_DAY=5, CONTACT_MAX_PER_HOUR=30,
            CONTACT_DUPLICATE_WINDOW_MINUTES=10, CONTACT_MESSAGE_RETENTION_DAYS=90,
            CONTACT_RATE_LIMIT="10 per hour", PREMIUM_ADMIN_IDENTIFIERS={"opsadmin"},
            FOUNDER_NAME="Rithivik Velusamy", FOUNDER_INTRO="",
            MAIL_SMTP_HOST="", MAIL_FROM="", MAIL_SMTP_USERNAME="", MAIL_SMTP_PASSWORD="",
            MAIL_SMTP_PORT="", MAIL_SMTP_SECURITY="starttls", MAIL_MAX_ATTEMPTS=3, PUBLIC_BASE_URL="")
        application.limiter.reset()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.counter = 0

    def tearDown(self):
        application.limiter.enabled = self.saved_limiter
        application.limiter.reset()
        application.app.config.clear()
        application.app.config.update(self.original)

    # ------------------------------------------------------------- helpers
    def register(self, username, client=None, grade="8"):
        client = client or self.client
        client.post("/register", data={
            "username": username, "email": f"{username}@example.com",
            "password": PASSWORD, "grade": grade})
        return client

    def token(self, client=None):
        page = (client or self.client).get("/contact").data.decode("utf-8")
        return re.search(r'name="form_started" value="([^"]+)"', page).group(1)

    def valid(self, **overrides):
        self.counter += 1
        data = {"name": "Mia", "email": f"mia{self.counter}@example.org", "category": "bug",
                "message": f"The lesson page does not load on my phone ({self.counter}).", "website": ""}
        data.update(overrides)
        return data

    def post_contact(self, client=None, token=None, **overrides):
        client = client or self.client
        data = self.valid(**overrides)
        if "form_started" not in overrides:
            data["form_started"] = token or self.token(client)
        return client.post("/contact", data=data)

    def nonce(self, client=None):
        """The form nonce in this client's session (rendering the form creates it)."""
        client = client or self.client
        client.get("/contact")
        with client.session_transaction() as session:
            return session[application.CONTACT_NONCE_KEY]

    def aged_token(self, seconds, client=None):
        """A form token for this client's session, issued `seconds` ago."""
        return contact_form.form_token(application.app.config["SECRET_KEY"], int(time.time()) - seconds,
                                       self.nonce(client))

    def set_rows(self, **values):
        """Write columns of every stored message directly (ages, statuses)."""
        with application.app.app_context():
            application.db.session.execute(application.update(application.ContactMessage).values(**values))
            application.db.session.commit()

    def admin(self):
        return self.register("opsadmin", client=application.app.test_client())

    def rows(self):
        with application.app.app_context():
            return application.db.session.scalars(
                application.db.select(application.ContactMessage).order_by(application.ContactMessage.id)).all()

    def configure_mail(self):
        application.app.config.update(
            MAIL_SMTP_HOST="smtp.example.com", MAIL_SMTP_PORT="587", MAIL_FROM="forms@example.com",
            MAIL_SMTP_USERNAME="forms@example.com", MAIL_SMTP_PASSWORD="s3cret-pass",
            MAIL_SMTP_SECURITY="starttls")

    def smtp(self, **behaviour):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.send_message.return_value = {}
        for name, value in behaviour.items():
            getattr(connection, name).side_effect = value
        return patch("learnova.notifications.mailer.smtplib.SMTP", return_value=connection), connection


class PublicPagesTests(ContactTestCase):
    def test_about_public_200_with_story_mission_founder(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        page = response.data.decode("utf-8")
        for text in ("Helping every student learn with a personal AI tutor.", "Our story", "Our mission",
                     "What makes Learnova different", "Meet the founder", "Student founder",
                     "Rithivik Velusamy", "is a school student", "High-quality education for everyone",
                     "A personal AI tutor for every student", "Better grades through understanding",
                     "Learning you enjoy", "Explanations at your level",
                     "Learn smarter. Understand deeper.", 'href="/contact"', 'href="/register"'):
            self.assertIn(text, page)
        # The unverifiable statistic is gone; the founder has no invented photo.
        self.assertNotIn("Most study time is lost", page)
        self.assertNotIn("about-founder-photo", page)
        self.assertIn('aria-labelledby="about-founder"', page)

    def test_contact_public_200_has_form_and_mailto(self):
        response = self.client.get("/contact")
        self.assertEqual(response.status_code, 200)
        page = response.data.decode("utf-8")
        self.assertIn(f'href="mailto:{CONTACT_EMAIL}"', page)
        for name in ("name", "email", "category", "message", "website", "form_started", "csrf_token"):
            self.assertIn(f'name="{name}"', page)
        for attribute in ('type="email"', 'autocomplete="email"', 'inputmode="email"',
                          'autocomplete="name"', 'maxlength="5000"', 'maxlength="100"', 'maxlength="254"'):
            self.assertIn(attribute, page)
        self.assertIn("<title>Contact us — Learnova</title>", page)
        self.assertIn('<meta name="description"', page)
        self.assertIn('id="main-content"', page)
        self.assertIn('<label for="contact-message">', page)
        self.assertIn("no-store", response.headers["Cache-Control"])
        # The eight topics, in order, with an empty first choice.
        keys = re.findall(r'<option value="([a-z]*)"', page)
        self.assertEqual(keys, ["", "bug", "technical", "suggestion", "account", "subscription",
                                "general", "partnership", "other"])

    def test_topic_query_preselects_a_category_and_ignores_unknown(self):
        page = self.client.get("/contact?topic=account").data.decode("utf-8")
        self.assertIn('<option value="account" selected>', page)
        page = self.client.get("/contact?topic=<script>").data.decode("utf-8")
        self.assertIn('<option value="" selected>', page)

    def test_logged_in_user_sees_about_and_contact(self):
        self.register("erik")
        about = self.client.get("/about", headers={"Accept": "text/html"})
        self.assertEqual(about.status_code, 200)
        self.assertIn(b"Open your dashboard", about.data)
        contact = self.client.get("/contact", headers={"Accept": "text/html"})
        self.assertEqual(contact.status_code, 200)
        self.assertIn(b"Send us a message", contact.data)

    def test_user_without_grade_is_not_gated_from_contact(self):
        with application.app.app_context():
            user = application.User(username="bob", email="bob@example.com", preferred_language="en", grade="")
            user.set_password(PASSWORD)
            application.db.session.add(user)
            application.db.session.commit()
        self.client.post("/login", data={"identifier": "bob", "password": PASSWORD})
        gated = self.client.get("/dashboard", headers={"Accept": "text/html"})
        self.assertEqual(gated.status_code, 302, "the gate itself must still work")
        self.assertEqual(self.client.get("/contact", headers={"Accept": "text/html"}).status_code, 200)

    def test_logged_in_email_is_prefilled_and_user_id_stored(self):
        self.register("lena")
        page = self.client.get("/contact").data.decode("utf-8")
        self.assertIn('value="lena@example.com"', page)
        self.assertNotIn('value="lena"', page, "the name is never prefilled")
        response = self.post_contact(email="lena@example.com")
        self.assertEqual(response.status_code, 303)
        [row] = self.rows()
        with application.app.app_context():
            user = application.User.query.filter_by(username="lena").one()
            self.assertEqual(row.user_id, user.id)

    def test_privacy_note_says_a_signed_in_message_is_linked_to_the_account(self):
        # Review content-2: the row stores user_id and the inbox shows the username.
        sentence = "Because you are signed in, your message is linked to your account."
        self.assertNotIn(sentence, self.client.get("/contact").data.decode("utf-8"))
        self.register("nina")
        self.assertIn(sentence, self.client.get("/contact").data.decode("utf-8"))
        self.assertIn(sentence, self.post_contact(email="broken").data.decode("utf-8"))

    def test_an_invalid_rate_limit_is_refused_at_start_up(self):
        # Review tests-05: Flask-Limiter would otherwise log an error and apply no limit at all.
        from flask import Flask

        from learnova.config import configure_app
        for value in ("banana", "10/hourly", "ten per hour"):
            with self.subTest(value=value), patch.dict(os.environ, {"CONTACT_RATE_LIMIT": value}):
                with self.assertRaisesRegex(RuntimeError, "CONTACT_RATE_LIMIT"):
                    configure_app(Flask("contact-limit-test", instance_path=tempfile.mkdtemp()), "testing")
        with patch.dict(os.environ, {"CONTACT_RATE_LIMIT": "5 per minute; 20 per day"}):
            configured = Flask("contact-limit-test", instance_path=tempfile.mkdtemp())
            configure_app(configured, "testing")
        self.assertEqual(configured.config["CONTACT_RATE_LIMIT"], "5 per minute; 20 per day")

    def test_footer_links_about_and_contact(self):
        def footer(page):
            match = re.search(r'<footer class="global-footer">.*?</footer>', page, re.S)
            self.assertIsNotNone(match)
            return match.group(0)

        for path in ("/about", "/contact"):
            part = footer(self.client.get(path).data.decode("utf-8"))
            self.assertIn('href="/about"', part)
            self.assertIn('href="/contact"', part)
            self.assertIn('<nav class="global-footer-links"', part)
        self.register("carol")
        for path in ("/dashboard", "/settings", "/about", "/contact"):
            response = self.client.get(path, headers={"Accept": "text/html"})
            self.assertEqual(response.status_code, 200, path)
            part = footer(response.data.decode("utf-8"))
            self.assertIn('href="/about"', part)
            self.assertIn('href="/contact"', part)
        # The navigation menus carry Contact too (no second navigation system).
        page = self.client.get("/dashboard", headers={"Accept": "text/html"}).data.decode("utf-8")
        self.assertGreaterEqual(page.count('href="/contact"'), 3)

    def test_auth_pages_link_contact(self):
        for path in ("/login", "/register"):
            page = self.client.get(path).data.decode("utf-8")
            self.assertIn('href="/contact"', page, path)

    def test_settings_help_section_links(self):
        self.register("dana")
        page = self.client.get("/settings").data.decode("utf-8")
        self.assertIn('id="help"', page)
        self.assertIn('id="main-content"', page)
        section = page[page.index('id="help"'):]
        section = section[:section.index("</section>")]
        self.assertIn('href="/about"', section)
        self.assertIn('href="/contact"', section)
        self.assertNotIn("/admin/contact-messages", section)

    def test_admin_inbox_link_only_for_premium_admin(self):
        self.register("opsadmin")
        page = self.client.get("/settings").data.decode("utf-8")
        self.assertIn('href="/admin/contact-messages"', page)

    def test_old_address_never_rendered(self):
        pages = [self.client.get(path).data for path in ("/about", "/contact", "/login", "/register")]
        pages.append(self.post_contact(email="broken").data)
        self.post_contact()
        pages.append(self.client.get("/contact/sent").data)
        self.register("finn")
        for path in ("/dashboard", "/settings", "/about", "/contact"):
            pages.append(self.client.get(path, headers={"Accept": "text/html"}).data)
        for page in pages:
            self.assertNotIn(OLD_ADDRESS, page)
        self.assertEqual(DEFAULT_COMMUNITY_ALERT_EMAIL, CONTACT_EMAIL)
        self.assertEqual(DEFAULT_CONTACT_EMAIL, CONTACT_EMAIL)

    def test_no_old_address_in_source_files(self):
        checked = 0
        for folder in ("templates", "static", "learnova", "docs", "tests", "scripts"):
            for path in (ROOT / folder).rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts and path.suffix in {
                        ".py", ".html", ".js", ".css", ".md", ".json", ".txt"}:
                    self.assertNotIn(OLD_ADDRESS, path.read_bytes(), str(path))
                    checked += 1
        for name in ("app.py", "render.yaml", ".env.example", "README.md"):
            self.assertNotIn(OLD_ADDRESS, (ROOT / name).read_bytes(), name)
        self.assertGreater(checked, 100)

    def test_contact_email_is_configurable(self):
        application.app.config["CONTACT_EMAIL"] = "help@example.org"
        page = self.client.get("/contact").data.decode("utf-8")
        self.assertIn('href="mailto:help@example.org"', page)

    def test_defaults(self):
        expected = {"CONTACT_EMAIL": CONTACT_EMAIL, "FOUNDER_NAME": "Rithivik Velusamy",
                    "CONTACT_MESSAGE_RETENTION_DAYS": 90, "CONTACT_RATE_LIMIT": "10 per hour",
                    "CONTACT_MAX_PER_EMAIL_DAY": 5, "CONTACT_MIN_FILL_SECONDS": 3,
                    "COMMUNITY_ALERT_EMAIL": CONTACT_EMAIL, "FOUNDER_INTRO": ""}
        for name, value in expected.items():
            if not os.getenv(name):  # a developer's own .env may override a default
                self.assertEqual(self.original[name], value, name)

    def test_founder_name_and_intro_override(self):
        application.app.config.update(FOUNDER_NAME="Alex", FOUNDER_INTRO="Built in the evenings after school.")
        page = self.client.get("/about").data.decode("utf-8")
        self.assertIn("<h3>Alex</h3>", page)
        self.assertIn("Built in the evenings after school.", page)
        self.assertNotIn("Rithivik", page)
        self.assertIn('aria-hidden="true">A</span>', page)

    def test_german_pages(self):
        with self.client.session_transaction() as session:
            session["language"] = "de"
        contact = self.client.get("/contact").data.decode("utf-8")
        self.assertIn("Kontakt", contact)
        self.assertIn("Nachricht senden", contact)
        about = self.client.get("/about").data.decode("utf-8")
        self.assertIn("Lerne den Gründer kennen", about)
        self.assertIn("Schüler", about)
        self.assertIn("Lerne schlauer", about)

    def test_supported_languages_unchanged(self):
        self.assertEqual(catalog.SUPPORTED_LANGUAGES, ("en", "de", "fr", "es"))

    def test_every_new_msgid_is_translated(self):
        pattern = re.compile(r"""_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
        msgids = set()
        for name in ("about.html", "contact.html", "admin_contact_messages.html",
                     "components/_footer.html", "settings.html"):
            text = (ROOT / "templates" / name).read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                msgids.add(match.group(1) if match.group(1) is not None else match.group(2))
        msgids |= set(contact_form.ERROR_MESSAGES.values())
        msgids |= {label for _key, label in contact_form.CATEGORIES}
        msgids |= set(application.CONTACT_FORM_ERRORS.values())
        msgids |= set(application.CONTACT_STATUS_LABELS.values())
        msgids |= {application.FOUNDER_INTRO_DEFAULT, "Forwarding result: {status}.",
                   "Tick the box to confirm the deletion.", "The message was deleted."}
        msgids.discard("")
        self.assertGreater(len(msgids), 150)
        for msgid in sorted(msgids):
            for language, table in (("de", catalog.GERMAN), ("fr", FR), ("es", ES)):
                self.assertIn(msgid, table, f"{language}: {msgid!r}")
                self.assertEqual(sorted(PLACEHOLDER.findall(msgid)), sorted(PLACEHOLDER.findall(table[msgid])),
                                 f"{language} placeholders: {msgid!r}")

    def test_vocabulary_card_hidden_when_flag_off(self):
        application.app.config["FEATURE_VOCABULARY_TRAINER"] = True
        self.assertIn(b"Vocabulary Trainer", self.client.get("/about").data)
        application.app.config["FEATURE_VOCABULARY_TRAINER"] = False
        self.assertNotIn(b"Vocabulary Trainer", self.client.get("/about").data)

    def test_feature_cards_follow_their_flags(self):
        application.app.config.update(FEATURE_ASSISTANT_CHAT=False, FEATURE_GAMIFICATION=False,
                                      FEATURE_PRIVATE_FLASHCARDS=False)
        page = self.client.get("/about").data.decode("utf-8")
        self.assertNotIn("An assistant for quick questions", page)
        self.assertNotIn("XP, challenges and weekly leagues", page)
        self.assertNotIn("Flashcards and community sets", page)
        application.app.config.update(FEATURE_PRIVATE_FLASHCARDS=True, FEATURE_COMMUNITY_LIBRARY=False)
        page = self.client.get("/about").data.decode("utf-8")
        self.assertIn("let AI generate them from your notes or a topic", page)
        self.assertNotIn("browse sets other students published", page)

    def test_no_inaccurate_claims(self):
        page = self.client.get("/about").data.decode("utf-8")
        for claim in ("several more languages", "upload documents", "always based on your material",
                      "Bildungsplan", "teacher-verified"):
            self.assertNotIn(claim, page)

    def test_pages_carry_theme_toggle_and_tokens(self):
        for path in ("/about", "/contact"):
            page = self.client.get(path).data.decode("utf-8")
            self.assertIn("data-theme-toggle", page)
            self.assertIn("/static/css/learnova-tokens.css", page)
            self.assertIn("/static/css/learnova-dark.css", page)
        self.assertIn("/static/css/contact.css", self.client.get("/contact").data.decode("utf-8"))

    def test_contact_css_uses_tokens_only(self):
        css = (ROOT / "static/css/contact.css").read_text(encoding="utf-8")
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        # Fallbacks inside var(--token, fallback) are allowed; nothing else may name a colour.
        without_fallbacks = re.sub(r"var\([^()]*\)", "var()", css)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", without_fallbacks))
        self.assertNotIn("rgb(", without_fallbacks)
        self.assertNotIn("hsl(", without_fallbacks)
        self.assertNotIn("outline: 0", css)
        self.assertNotIn("outline:0", css)
        self.assertIn(".contact-hp { display: none; }", css)
        self.assertIn("prefers-reduced-motion", css)

    def test_contact_scripts_carry_nonce(self):
        self.post_contact()
        for page in (self.client.get("/contact").data.decode("utf-8"),
                     self.post_contact(name="").data.decode("utf-8")):
            scripts = re.findall(r"<script\b[^>]*>", page)
            self.assertTrue(scripts)
            for tag in scripts:
                self.assertIn("nonce=", tag)
            self.assertIn("/static/js/contact.js", page)
            self.assertNotRegex(page, r"\son[a-z]+=")

    def test_contact_pages_carry_no_ads(self):
        for endpoint in ("contact", "contact_submit", "contact_sent", "admin_contact_messages"):
            self.assertIn(endpoint, application.AD_FREE_ENDPOINTS)


class SubmissionTests(ContactTestCase):
    def test_valid_submission_is_stored_and_redirects_to_sent(self):
        response = self.post_contact(name="  Mia \t Müller ", email=" Mia@Example.ORG ",
                                     message="Hello,\r\nthe quiz\r\ndoes not open.  ")
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["Location"].endswith("/contact/sent"))
        [row] = self.rows()
        self.assertEqual((row.name, row.email, row.category), ("Mia Müller", "mia@example.org", "bug"))
        self.assertEqual(row.message, "Hello,\nthe quiz\ndoes not open.")
        self.assertEqual(row.message_digest, contact_form.message_digest(row.message))
        self.assertEqual(row.language, "en")
        self.assertIsNone(row.user_id)
        sent = self.client.get("/contact/sent")
        self.assertEqual(sent.status_code, 200)
        self.assertIn("Thank you — your message has arrived.".encode(), sent.data)
        self.assertIn(b"data-contact-sent", sent.data)
        self.assertIn("no-store", sent.headers["Cache-Control"])
        again = self.client.get("/contact/sent")
        self.assertEqual(again.status_code, 302)
        self.assertTrue(again.headers["Location"].endswith("/contact"))

    def test_sent_page_without_submission_redirects(self):
        response = self.client.get("/contact/sent")
        self.assertEqual(response.status_code, 302)

    def test_invalid_emails_rejected(self):
        for email in ("", "no-at", "a@b", "a@@b.com", "a b@c.de", "x@y.z", "a" * 245 + "@example.com",
                      "a@b.co\r\nBcc: e@x.com", "ü@example.com"):
            with self.subTest(email=email):
                response = self.post_contact(email=email)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Please enter a valid email address.", response.data)
                self.assertIn(b'aria-invalid="true"', response.data)
        self.assertEqual(self.rows(), [])

    def test_the_whole_address_may_be_at_most_254_characters(self):
        # Review tests-06: a short local part with a very long domain still matches the pattern.
        def address(tld_length):
            return "a@" + ".".join(["b" * 63] * 3) + "." + "c" * tld_length

        self.assertEqual(len(address(61)), 255)
        self.assertTrue(contact_form.EMAIL_PATTERN.fullmatch(address(61)))
        response = self.post_contact(email=address(61))
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"Please enter a valid email address.", response.data)
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(address(60)), 254)
        self.assertEqual(self.post_contact(email=address(60)).status_code, 303)
        self.assertEqual([row.email for row in self.rows()], [address(60)])

    def test_german_contact_errors_read_naturally(self):
        # Review content-4.
        with self.client.session_transaction() as session:
            session["language"] = "de"
        too_long = self.post_contact(message="d" * 5001).data.decode("utf-8")
        self.assertIn("Deine Nachricht ist zu lang. Bitte kürze sie auf höchstens 5000 Zeichen.", too_long)
        self.assertNotIn("bleib bei", too_long)
        application.app.config["CONTACT_MIN_FILL_SECONDS"] = 3
        too_fast = self.post_contact().data.decode("utf-8")
        self.assertIn("Das ging schnell! Bitte prüfe deine Nachricht und sende sie noch einmal.", too_fast)
        self.assertEqual(self.rows(), [])

    def test_empty_and_whitespace_message_rejected(self):
        for message in ("", "   \r\n\t  "):
            with self.subTest(message=message):
                response = self.post_contact(message=message)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Please write a message.", response.data)
        self.assertEqual(self.rows(), [])

    def test_message_length_limits(self):
        short = self.post_contact(message="a" * 9)
        self.assertEqual(short.status_code, 400)
        self.assertIn(b"Your message is too short. Please write at least 10 characters.", short.data)
        self.assertEqual(self.post_contact(message="b" * 10).status_code, 303)
        self.assertEqual(self.post_contact(message="c" * 5000).status_code, 303)
        long = self.post_contact(message="d" * 5001)
        self.assertEqual(long.status_code, 400)
        self.assertIn(b"Your message is too long. Please keep it to 5000 characters or fewer.", long.data)
        # Browsers submit textarea newlines as CRLF: counted after normalisation.
        self.assertEqual(self.post_contact(message="e\r\n" * 2500).status_code, 303)
        self.assertEqual(len(self.rows()), 3)
        self.assertEqual(len(self.rows()[-1].message), 4999)

    def test_name_limits(self):
        missing = self.post_contact(name="   ")
        self.assertEqual(missing.status_code, 400)
        self.assertIn(b"Please enter your name.", missing.data)
        long = self.post_contact(name="n" * 101)
        self.assertEqual(long.status_code, 400)
        self.assertIn(b"Your name can be at most 100 characters long.", long.data)
        self.assertEqual(self.post_contact(name="n" * 100).status_code, 303)

    def test_unknown_category_rejected(self):
        for category in ("", "admin", "BUG", "<b>"):
            with self.subTest(category=category):
                response = self.post_contact(category=category)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"Please choose a topic for your message.", response.data)
        self.assertEqual(self.rows(), [])

    def test_values_are_kept_and_escaped_on_error(self):
        response = self.post_contact(name="<script>x</script>", email="kept@example.org",
                                     category="suggestion", message="short")
        self.assertEqual(response.status_code, 400)
        page = response.data.decode("utf-8")
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", page)
        self.assertNotIn("<script>x</script>", page)
        self.assertIn('value="kept@example.org"', page)
        self.assertIn('<option value="suggestion" selected>', page)
        self.assertIn(">short</textarea>", page)
        self.assertIn('id="contact-website" name="website" type="text" tabindex="-1" autocomplete="off" value=""', page)
        self.assertIn("Your message was not sent yet", page)
        self.assertIn('href="#contact-message"', page)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_name_with_newlines_is_flattened(self):
        self.assertEqual(self.post_contact(name="Ann\r\nBcc: x@y.z​").status_code, 303)
        [row] = self.rows()
        self.assertEqual(row.name, "Ann Bcc: x@y.z")

    def test_honeypot_blocks_and_stores_nothing(self):
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            response = self.post_contact(website="http://spam.example")
        self.assertEqual(response.status_code, 400)
        self.assertIn(f"We could not accept this message. Please try again, or email us at {CONTACT_EMAIL}.".encode(),
                      response.data)
        self.assertNotIn(b"http://spam.example", response.data)
        self.assertEqual(self.rows(), [])
        connection.send_message.assert_not_called()

    def test_missing_or_forged_form_token_rejected(self):
        secret = application.app.config["SECRET_KEY"]
        nonce = self.nonce()
        now = int(time.time())
        for token in ("", "garbage", f"{now}.{'0' * 32}", contact_form.form_token("other-secret", now, nonce),
                      contact_form.form_token(secret, now - 2 * 86400, nonce),
                      contact_form.form_token(secret, now + 3600, nonce),
                      contact_form.form_token(secret, now - 10, "another-session")):
            with self.subTest(token=token):
                response = self.post_contact(form_started=token)
                self.assertEqual(response.status_code, 400)
                self.assertIn(b"We could not accept this message.", response.data)
        self.assertEqual(self.rows(), [])

    def test_too_fast_submission_asks_to_resend(self):
        application.app.config["CONTACT_MIN_FILL_SECONDS"] = 3
        response = self.post_contact()
        self.assertEqual(response.status_code, 400)
        self.assertIn(b"That was quick! Please check your message and send it again.", response.data)
        self.assertIn(b"The lesson page does not load", response.data, "the message is kept")
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.post_contact(form_started=self.aged_token(10)).status_code, 303)
        self.assertEqual(len(self.rows()), 1)

    def test_a_rendered_form_can_be_sent_once(self):
        # Review security-3: one form token used to be good for 24 hours of posts.
        token = self.aged_token(10)
        self.assertEqual(self.post_contact(token=token).status_code, 303)
        replay = self.post_contact(token=token)
        self.assertEqual(replay.status_code, 400)
        self.assertIn(b"We could not accept this message.", replay.data)
        self.assertIn(b"The lesson page does not load", replay.data, "the text is kept")
        # The refusal renders a fresh form, and that one goes through.
        fresh = re.search(r'name="form_started" value="([^"]+)"', replay.data.decode("utf-8")).group(1)
        self.assertEqual(self.post_contact(token=fresh).status_code, 303)
        self.assertEqual(len(self.rows()), 2)

    def test_a_form_token_from_another_session_is_refused(self):
        other = application.app.test_client()
        self.nonce()  # this client has a form of its own
        response = self.post_contact(token=self.aged_token(10, client=other))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.rows(), [])
        fresh_client = application.app.test_client()  # no session at all
        self.assertEqual(self.post_contact(client=fresh_client, token=self.aged_token(10, client=other)).status_code,
                         400)
        self.assertEqual(self.rows(), [])

    def test_duplicate_within_window_is_not_stored_twice(self):
        data = {"email": "same@example.org", "message": "Exactly the same message twice."}
        first = self.post_contact(**data)
        second = self.post_contact(**data, name="Mia again")
        self.assertEqual((first.status_code, second.status_code), (303, 303))
        self.assertEqual(len(self.rows()), 1)
        # Whitespace and case do not make it a different message.
        self.assertEqual(self.post_contact(email="same@example.org",
                                           message="exactly  the same\nmessage TWICE.").status_code, 303)
        self.assertEqual(len(self.rows()), 1)

    def test_the_duplicate_guard_ends_with_its_window(self):
        # Review tests-01: a resend after the window is a new message, never silently dropped.
        data = {"email": "again@example.org", "message": "The same bug, reported again later."}
        self.assertEqual(self.post_contact(**data).status_code, 303)
        self.set_rows(created_at=application.utcnow() - timedelta(minutes=9))
        self.assertEqual(self.post_contact(**data).status_code, 303)
        self.assertEqual(len(self.rows()), 1, "still inside the 10-minute window")
        self.set_rows(created_at=application.utcnow() - timedelta(minutes=11))
        self.assertEqual(self.post_contact(**data).status_code, 303)
        self.assertEqual(len(self.rows()), 2)

    def test_duplicate_guard_sees_through_plus_tags(self):
        text = "Exactly the same message from one mailbox."
        self.assertEqual(self.post_contact(email="amy+a@example.org", message=text).status_code, 303)
        self.assertEqual(self.post_contact(email="amy+b@example.org", message=text).status_code, 303)
        self.assertEqual(len(self.rows()), 1)

    def test_per_email_cap_counts_one_mailbox_once_whatever_its_spelling(self):
        # Review security-2: bob+1@, bob+2@ ... all reach one inbox and must share one cap.
        application.app.config["CONTACT_MAX_PER_EMAIL_DAY"] = 2
        self.assertEqual(self.post_contact(email="Bob+1@Example.org").status_code, 303)
        self.assertEqual(self.post_contact(email="bob+2@example.org").status_code, 303)
        self.assertEqual(self.post_contact(email="bob+3@example.org").status_code, 429)
        self.assertEqual(self.post_contact(email="bob@example.org").status_code, 429)
        # The address is stored as written, so a reply still reaches the tagged address.
        self.assertEqual([row.email for row in self.rows()], ["bob+1@example.org", "bob+2@example.org"])
        # Gmail ignores dots and treats googlemail.com as gmail.com.
        self.assertEqual(self.post_contact(email="j.doe@gmail.com").status_code, 303)
        self.assertEqual(self.post_contact(email="jdoe+x@googlemail.com").status_code, 303)
        self.assertEqual(self.post_contact(email="J.D.O.E@gmail.com").status_code, 429)
        # Elsewhere a dot is part of the mailbox name: another person, another cap.
        self.assertEqual(self.post_contact(email="b.ob@example.org").status_code, 303)
        self.assertEqual(self.post_contact(email="bob@example.com").status_code, 303)

    def test_per_email_daily_cap(self):
        application.app.config["CONTACT_MAX_PER_EMAIL_DAY"] = 2
        for _ in range(2):
            self.assertEqual(self.post_contact(email="busy@example.org").status_code, 303)
        response = self.post_contact(email="busy@example.org", name="Busy Bee")
        self.assertEqual(response.status_code, 429)
        self.assertIn(b"Many messages have been sent recently.", response.data)
        self.assertIn(f"or email us at {CONTACT_EMAIL}".encode(), response.data)
        self.assertIn(b'value="Busy Bee"', response.data)
        self.assertEqual(len(self.rows()), 2)

    def test_global_hourly_cap(self):
        application.app.config["CONTACT_MAX_PER_HOUR"] = 2
        self.assertEqual(self.post_contact().status_code, 303)
        self.assertEqual(self.post_contact().status_code, 303)
        self.assertEqual(self.post_contact().status_code, 429)
        self.assertEqual(len(self.rows()), 2)

    def test_storage_failure_is_reported_honestly(self):
        with patch.object(application.db.session, "commit",
                          side_effect=application.SQLAlchemyError("disk full")):
            response = self.post_contact(name="Kept Name")
        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Your message could not be saved right now.", response.data)
        self.assertIn(b'value="Kept Name"', response.data)
        self.assertNotIn(b"disk full", response.data)
        self.assertNotIn(b"Traceback", response.data)
        self.assertNotIn(b"Thank you", response.data)


class CsrfAndRateLimitTests(ContactTestCase):
    def test_csrf_missing_token_rejected(self):
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True, COMMUNITY_BACKGROUND_JOBS=False)
        application.limiter.enabled = False
        token = self.token()
        data = self.valid()
        data["form_started"] = token
        rejected = self.client.post("/contact", data=data, headers={"Referer": "http://localhost/contact"})
        self.assertEqual(rejected.status_code, 302)
        self.assertEqual(self.rows(), [])
        with self.client.session_transaction() as session:
            flashes = [message for _category, message in session.get("_flashes", [])]
        self.assertIn("Your form expired. Refresh the page and try again.", flashes)
        page = self.client.get("/contact").data.decode("utf-8")
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        data = self.valid()
        data["form_started"] = re.search(r'name="form_started" value="([^"]+)"', page).group(1)
        data["csrf_token"] = csrf
        accepted = self.client.post("/contact", data=data)
        self.assertEqual(accepted.status_code, 303)
        self.assertEqual(len(self.rows()), 1)

    def test_flask_limiter_blocks_eleventh_post(self):
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=False, COMMUNITY_BACKGROUND_JOBS=False)
        application.limiter.enabled = True
        application.limiter.reset()
        for number in range(10):
            # Each post renders its own form first: a form can be sent once (the GET is not limited).
            response = self.post_contact()
            self.assertEqual(response.status_code, 303, number)
        blocked = self.client.post(
            "/contact", data={**self.valid(), "form_started": self.token()},
            headers={"Referer": "http://localhost/contact"})
        self.assertEqual(blocked.status_code, 302)
        with self.client.session_transaction() as session:
            flashes = [message for _category, message in session.get("_flashes", [])]
        self.assertIn("Too many requests. Please wait a moment and try again.", flashes)
        self.assertEqual(len(self.rows()), 10)
        # The page itself (GET) is not limited.
        self.assertEqual(self.client.get("/contact").status_code, 200)


class DeliveryTests(ContactTestCase):
    def test_forwarding_not_configured_is_honest(self):
        response = self.post_contact()
        self.assertEqual(response.status_code, 303)
        [row] = self.rows()
        self.assertEqual(row.status, "not_configured")
        self.assertEqual(row.attempts, 0)
        self.assertIn("MAIL_SMTP_HOST", row.last_error)
        self.assert_sent_page_claims_no_forwarding()
        form = self.client.get("/contact").data.decode("utf-8")
        self.assertNotIn("A copy is forwarded", form)
        self.assertIn("deleted from Learnova after 90 days", form)

    def assert_sent_page_claims_no_forwarding(self):
        sent = self.client.get("/contact/sent").data.decode("utf-8").lower()
        self.assertIn("we have saved your message", sent)
        for claim in FORWARDING_CLAIMS:
            self.assertNotIn(claim, sent)

    def test_privacy_note_mentions_possible_forwarding_before_email_is_configured(self):
        # Review content-3: messages stored while email is off are forwarded once it is on.
        sentence = f"A copy may be forwarded to our inbox at {CONTACT_EMAIL}. That copy is not deleted automatically."
        self.assertIn(sentence, self.client.get("/contact").data.decode("utf-8"))
        self.post_contact()
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher, application.app.app_context():
            self.assertEqual(application.deliver_due_contact_messages(), {"sent": 1})
            self.assertIn(sentence, self.client.get("/contact").data.decode("utf-8"))
        connection.send_message.assert_called_once()

    def test_retries_stop_at_mail_max_attempts(self):
        # Review tests-02: a server that keeps answering 4xx ends in "failed", not in endless retries.
        self.configure_mail()
        application.app.config["MAIL_MAX_ATTEMPTS"] = 3
        patcher, connection = self.smtp(send_message=smtplib.SMTPResponseException(451, b"later"))
        with patcher:
            self.assertEqual(self.post_contact().status_code, 303)
            self.assertEqual((self.rows()[0].status, self.rows()[0].attempts), ("retry", 1))
            for attempt in (2, 3):
                self.set_rows(next_attempt_at=application.utcnow() - timedelta(minutes=1))
                with application.app.app_context():
                    application.deliver_due_contact_messages()
                self.assertEqual(self.rows()[0].attempts, attempt)
            with application.app.app_context():
                self.assertEqual(application.deliver_due_contact_messages(), {})
        [row] = self.rows()
        self.assertEqual((row.status, row.attempts), ("failed", 3))
        self.assertEqual(connection.send_message.call_count, 3)

    def test_retry_job_waits_until_a_retry_is_due(self):
        # Review tests-07: the 1/5/30-minute backoff is kept.
        self.configure_mail()
        patcher, _connection = self.smtp(send_message=smtplib.SMTPResponseException(451, b"later"))
        with patcher:
            self.post_contact()
        self.set_rows(next_attempt_at=application.utcnow() + timedelta(minutes=10))
        patcher, connection = self.smtp()
        with patcher, application.app.app_context():
            self.assertEqual(application.deliver_due_contact_messages(), {})
        connection.send_message.assert_not_called()
        [row] = self.rows()
        self.assertEqual((row.status, row.attempts), ("retry", 1))

    def test_forwarding_success_sets_reply_to(self):
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            response = self.post_contact(name="Mia", email="mia.sender@example.org", category="bug",
                                         message="The quiz button does nothing on iPhone.")
        self.assertEqual(response.status_code, 303)
        [row] = self.rows()
        self.assertEqual(row.status, "sent")
        self.assertIsNotNone(row.sent_at)
        self.assertTrue(row.smtp_message_id)
        message = connection.send_message.call_args[0][0]
        self.assertIsInstance(message, EmailMessage)
        self.assertEqual(message["To"], CONTACT_EMAIL)
        self.assertEqual(message["Reply-To"], "mia.sender@example.org")
        self.assertEqual(message["Subject"], f"[Learnova contact] Bug report #{row.id}")
        body = message.get_content()
        self.assertIn("The quiz button does nothing on iPhone.", body)
        self.assertIn("Name: Mia", body)
        self.assertIn("Signed in: no", body)
        self.assertIn("Inbox: /admin/contact-messages", body)
        self.assertNotIn("s3cret-pass", body)
        with patcher:
            form = self.client.get("/contact").data.decode("utf-8")
        self.assertIn(f"A copy may be forwarded to our inbox at {CONTACT_EMAIL}.", form)

    def test_user_input_never_reaches_the_subject(self):
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            self.post_contact(name="Evil\r\nSubject: hacked", message="Line one\r\nBcc: victim@example.com")
        message = connection.send_message.call_args[0][0]
        self.assertNotIn("Evil", message["Subject"])
        self.assertIsNone(message["Bcc"])
        self.assertEqual(message.get_all("Subject"), [message["Subject"]])

    def test_smtp_auth_failure_marks_failed_but_message_kept(self):
        self.configure_mail()
        patcher, _connection = self.smtp(login=smtplib.SMTPAuthenticationError(535, b"no"))
        with patcher:
            response = self.post_contact()
        self.assertEqual(response.status_code, 303)
        [row] = self.rows()
        self.assertEqual(row.status, "failed")
        self.assertIn("authentication rejected", row.last_error)
        # Review tests-08: the same honesty list as when email is not configured.
        self.assert_sent_page_claims_no_forwarding()

    def test_smtp_4xx_schedules_retry(self):
        self.configure_mail()
        patcher, _connection = self.smtp(send_message=smtplib.SMTPResponseException(451, b"later"))
        with patcher:
            self.assertEqual(self.post_contact().status_code, 303)
        [row] = self.rows()
        self.assertEqual(row.status, "retry")
        self.assertIsNotNone(row.next_attempt_at)
        self.assertEqual(row.attempts, 1)
        self.assert_sent_page_claims_no_forwarding()

    def test_smtp_connection_error_does_not_500(self):
        self.configure_mail()
        with patch("learnova.notifications.mailer.smtplib.SMTP", side_effect=OSError("unreachable")):
            response = self.post_contact()
        self.assertEqual(response.status_code, 303)
        [row] = self.rows()
        self.assertEqual(row.status, "retry")
        self.assertNotIn("unreachable", row.last_error)

    def test_unexpected_forwarding_error_still_confirms_the_stored_message(self):
        with patch.object(application, "deliver_contact_message", side_effect=RuntimeError("boom")):
            response = self.post_contact()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.rows()), 1)

    def test_retry_job_forwards_stored_messages_once_mail_is_configured(self):
        self.post_contact()
        self.assertEqual(self.rows()[0].status, "not_configured")
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher, application.app.app_context():
            outcome = application.deliver_due_contact_messages()
        self.assertEqual(outcome, {"sent": 1})
        self.assertEqual(self.rows()[0].status, "sent")
        connection.send_message.assert_called_once()

    def test_mailer_ignores_unsafe_reply_to(self):
        settings = notifications.MailSettings(host="smtp.example.com", port=587, sender="forms@example.com")
        for reply_to in ("a@b.co\r\nBcc: x@y.z", "Name <a@b.co>", "a@b.co, c@d.de", "ü@example.com", 1234):
            with self.subTest(reply_to=reply_to):
                connection = MagicMock()
                connection.__enter__.return_value = connection
                connection.send_message.return_value = {}
                result = notifications.send_email(settings, "ops@example.com", "Subject", "Body",
                                                  reply_to=reply_to, smtp_factory=lambda *a, **k: connection)
                self.assertEqual(result.status, "sent")
                message = connection.send_message.call_args[0][0]
                self.assertIsNone(message["Reply-To"])
                self.assertIsNone(message["Bcc"])
        # A recipient with a line break cannot be built into a header: failed, no exception.
        result = notifications.send_email(settings, "ops@example.com\r\nBcc: x@y.z", "Subject", "Body",
                                          smtp_factory=lambda *a, **k: MagicMock())
        self.assertEqual(result.status, "failed")


class AdminInboxTests(ContactTestCase):
    def test_admin_inbox_access(self):
        self.post_contact(name="Private Person", message="My account email changed, please help.")
        anonymous = self.client.get("/admin/contact-messages")
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login", anonymous.headers["Location"])
        student = self.register("student", client=application.app.test_client())
        self.assertEqual(student.get("/admin/contact-messages").status_code, 404)
        admin = self.register("opsadmin", client=application.app.test_client())
        response = admin.get("/admin/contact-messages")
        self.assertEqual(response.status_code, 200)
        page = response.data.decode("utf-8")
        self.assertIn("Private Person", page)
        self.assertIn("My account email changed, please help.", page)
        self.assertIn("Stored only (email not configured)", page)
        self.assertIn("Email forwarding is not configured", page)
        self.assertIn("mailto:mia", page)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_admin_retry_and_delete(self):
        self.post_contact()
        [row] = self.rows()
        student = self.register("student", client=application.app.test_client())
        self.assertEqual(student.post(f"/admin/contact-messages/{row.id}/retry").status_code, 404)
        self.assertEqual(student.post(f"/admin/contact-messages/{row.id}/delete",
                                      data={"confirm": "yes"}).status_code, 404)
        admin = self.register("opsadmin", client=application.app.test_client())
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            retried = admin.post(f"/admin/contact-messages/{row.id}/retry")
        self.assertEqual(retried.status_code, 302)
        self.assertEqual(self.rows()[0].status, "sent")
        connection.send_message.assert_called_once()
        kept = admin.post(f"/admin/contact-messages/{row.id}/delete", data={})
        self.assertEqual(kept.status_code, 302)
        self.assertEqual(len(self.rows()), 1)
        gone = admin.post(f"/admin/contact-messages/{row.id}/delete", data={"confirm": "yes"})
        self.assertEqual(gone.status_code, 302)
        self.assertEqual(self.rows(), [])
        self.assertEqual(admin.post("/admin/contact-messages/99999/retry").status_code, 404)

    def test_admin_retry_forwards_a_failed_message_again(self):
        # Review tests-04: after an SMTP login problem is fixed, the button really resends.
        admin = self.admin()
        self.configure_mail()
        patcher, _connection = self.smtp(login=smtplib.SMTPAuthenticationError(535, b"no"))
        with patcher:
            self.post_contact()
        [row] = self.rows()
        self.assertEqual(row.status, "failed")
        patcher, connection = self.smtp()
        with patcher:
            self.assertEqual(admin.post(f"/admin/contact-messages/{row.id}/retry").status_code, 302)
        self.assertEqual(self.rows()[0].status, "sent")
        connection.send_message.assert_called_once()

    def test_admin_retry_recovers_a_message_stuck_in_sending(self):
        # Review tests-03: a restart mid-send left the row in "sending"; the button was a no-op.
        admin = self.admin()
        self.post_contact()
        [row] = self.rows()
        self.set_rows(status="sending", updated_at=application.utcnow() - timedelta(days=2))
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            self.assertEqual(admin.post(f"/admin/contact-messages/{row.id}/retry").status_code, 302)
        self.assertEqual(self.rows()[0].status, "sent")
        connection.send_message.assert_called_once()

    def test_admin_retry_leaves_a_send_in_progress_alone(self):
        admin = self.admin()
        self.post_contact()
        [row] = self.rows()
        self.set_rows(status="sending", updated_at=application.utcnow() - timedelta(minutes=2))
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            admin.post(f"/admin/contact-messages/{row.id}/retry")
        self.assertEqual(self.rows()[0].status, "sending")
        connection.send_message.assert_not_called()

    def test_moderators_cannot_read_contact_messages(self):
        application.app.config["COMMUNITY_MODERATORS"] = {"moddy"}
        moderator = self.register("moddy", client=application.app.test_client())
        self.assertEqual(moderator.get("/admin/contact-messages").status_code, 404)


class RetentionTests(ContactTestCase):
    def test_cleanup_removes_expired_rows(self):
        self.post_contact(message="An old message that should expire.")
        self.post_contact(message="A recent message that should stay.")
        with application.app.app_context():
            old, recent = application.db.session.scalars(
                application.db.select(application.ContactMessage).order_by(application.ContactMessage.id)).all()
            old.created_at = application.utcnow() - timedelta(days=91)
            recent.created_at = application.utcnow() - timedelta(days=89)
            application.db.session.commit()
            self.assertEqual(application.cleanup_contact_messages(), 1)
            application.db.session.commit()
        [left] = self.rows()
        self.assertEqual(left.message, "A recent message that should stay.")

    def test_a_new_message_triggers_cleanup(self):
        self.post_contact(message="An old message that should expire.")
        with application.app.app_context():
            row = application.db.session.scalars(application.db.select(application.ContactMessage)).one()
            row.created_at = application.utcnow() - timedelta(days=120)
            application.db.session.commit()
        self.post_contact(message="A brand new message arrives today.")
        [left] = self.rows()
        self.assertEqual(left.message, "A brand new message arrives today.")

    def test_expired_messages_are_deleted_when_the_inbox_opens(self):
        # Review security-1 / content-1: nobody submits for months; the promise still holds.
        admin = self.admin()
        self.post_contact(name="Old Sender", message="A message from long ago that must be gone.")
        self.post_contact(name="New Sender", message="A message from this week that stays.")
        with application.app.app_context():
            old = application.db.session.scalars(application.db.select(application.ContactMessage).order_by(
                application.ContactMessage.id)).first()
            old.created_at = application.utcnow() - timedelta(days=200)
            application.db.session.commit()
        page = admin.get("/admin/contact-messages").data.decode("utf-8")
        self.assertNotIn("Old Sender", page)
        self.assertNotIn("must be gone", page)
        self.assertIn("New Sender", page)
        self.assertEqual([row.name for row in self.rows()], ["New Sender"])

    def test_inbox_never_shows_an_expired_message_even_if_deleting_failed(self):
        admin = self.admin()
        self.post_contact(name="Old Sender", message="A message from long ago that must be hidden.")
        self.set_rows(created_at=application.utcnow() - timedelta(days=200))
        with patch.object(application, "cleanup_contact_messages", return_value=0):
            page = admin.get("/admin/contact-messages").data.decode("utf-8")
            self.assertEqual(admin.post(f"/admin/contact-messages/{self.rows()[0].id}/retry").status_code, 404)
        self.assertNotIn("Old Sender", page)
        self.assertIn("No messages yet.", page)

    def test_an_idle_site_still_deletes_expired_messages(self):
        # Opening the contact page (or starting the app) is enough; no new message is needed.
        self.post_contact(message="An old message nobody followed up.")
        self.set_rows(created_at=application.utcnow() - timedelta(days=120))
        self.assertEqual(self.client.get("/contact").status_code, 200)
        self.assertEqual(self.rows(), [])
        self.post_contact(message="Another old message, removed at start-up.")
        self.set_rows(created_at=application.utcnow() - timedelta(days=120))
        with application.app.app_context():
            self.assertEqual(application.expire_contact_messages(batches=10), 1)
        self.assertEqual(self.rows(), [])

    def test_retry_job_and_cli_never_forward_or_list_expired_messages(self):
        self.post_contact(name="Expired Sender", message="An expired message waiting to be forwarded.")
        [row] = self.rows()
        self.assertEqual(row.status, "not_configured")
        self.set_rows(created_at=application.utcnow() - timedelta(days=120))
        runner = application.app.test_cli_runner()
        with patch.object(application, "cleanup_contact_messages", return_value=0):
            listed = runner.invoke(args=["contact", "list"])
            self.assertEqual(listed.exit_code, 0, listed.output)
            self.assertNotIn(f"#{row.id} ", listed.output)
            self.configure_mail()
            patcher, connection = self.smtp()
            with patcher, application.app.app_context():
                self.assertEqual(application.deliver_due_contact_messages(), {})
            connection.send_message.assert_not_called()
        with patcher, application.app.app_context():
            self.assertEqual(application.deliver_due_contact_messages(), {})
        connection.send_message.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_cli_contact_commands(self):
        self.post_contact(name="Secret Name", email="secret.sender@example.org",
                          message="A body that must never be printed.")
        runner = application.app.test_cli_runner()
        listed = runner.invoke(args=["contact", "list"])
        self.assertEqual(listed.exit_code, 0, listed.output)
        self.assertIn("NOT configured", listed.output)
        self.assertIn(CONTACT_EMAIL, listed.output)
        self.assertIn("status=not_configured", listed.output)
        for private in ("Secret Name", "secret.sender@example.org", "must never be printed"):
            self.assertNotIn(private, listed.output)
        retried = runner.invoke(args=["contact", "retry"])
        self.assertEqual(retried.exit_code, 0, retried.output)
        self.assertIn("not_configured: 1", retried.output)
        cleaned = runner.invoke(args=["contact", "cleanup"])
        self.assertEqual(cleaned.exit_code, 0, cleaned.output)
        self.assertIn("Deleted 0 contact message(s)", cleaned.output)
        self.assertEqual(len(self.rows()), 1)

    def test_stale_sending_rows_are_retried(self):
        self.post_contact()
        with application.app.app_context():
            row = application.db.session.scalars(application.db.select(application.ContactMessage)).one()
            row.status = "sending"
            application.db.session.commit()
            application.db.session.execute(application.update(application.ContactMessage).values(
                updated_at=application.utcnow() - timedelta(hours=2)))
            application.db.session.commit()
            self.assertEqual(application.deliver_due_contact_messages(), {"not_configured": 1})

    def test_migration_is_recorded(self):
        with application.app.app_context():
            application.ensure_database()
            version = application.db.session.get(application.SchemaMigration, "039_contact_messages")
            self.assertIsNotNone(version)


class ContactModuleTests(unittest.TestCase):
    def test_token_round_trip(self):
        token = contact_form.form_token("s", 1000, "n1")
        self.assertEqual(contact_form.form_token_age("s", token, 1010, nonce="n1"), 10)
        self.assertIsNone(contact_form.form_token_age("t", token, 1010, nonce="n1"))
        self.assertIsNone(contact_form.form_token_age("s", token, 1010, nonce="n2"))
        self.assertIsNone(contact_form.form_token_age("s", token, 1010, nonce=""))
        self.assertIsNone(contact_form.form_token_age("s", token, 1000 + 86401, nonce="n1"))
        self.assertIsNone(contact_form.form_token_age("s", None, 1010, nonce="n1"))
        self.assertNotEqual(contact_form.new_nonce(), contact_form.new_nonce())

    def test_email_key_groups_one_mailbox(self):
        key = contact_form.email_key
        self.assertEqual(key("bob+tag@example.org"), "bob@example.org")
        self.assertEqual(key("Bob+a+b@Example.org"), "bob@example.org")
        self.assertEqual(key("j.o.e+x@googlemail.com"), "joe@gmail.com")
        self.assertEqual(key("J.O.E@gmail.com"), "joe@gmail.com")
        self.assertEqual(key("j.o.e@example.org"), "j.o.e@example.org")
        self.assertEqual(key("+only@example.org"), "+only@example.org")
        self.assertEqual(contact_form.mailbox_domains("joe@gmail.com"), ("gmail.com", "googlemail.com"))
        self.assertEqual(contact_form.mailbox_domains("bob@example.org"), ("example.org",))

    def test_digest_ignores_case_and_spacing(self):
        self.assertEqual(contact_form.message_digest("Hello  World\n"), contact_form.message_digest("hello world"))

    def test_categories_are_the_eight_of_the_spec(self):
        self.assertEqual([key for key, _ in contact_form.CATEGORIES],
                         ["bug", "technical", "suggestion", "account", "subscription", "general",
                          "partnership", "other"])

    def test_compose_forward_keeps_input_out_of_the_subject(self):
        from datetime import datetime, timezone
        subject, body = contact_form.compose_forward(
            message_id=7, category="nope", name="X\nY", email="a@b.co", message="Hi there",
            user_id=3, language="de", created_at=datetime(2026, 11, 1, 9, 30, tzinfo=timezone.utc),
            base_url="https://learnova.example.org/", retention_days=90)
        self.assertEqual(subject, "[Learnova contact] Other #7")
        self.assertIn("Signed in: yes (account #3)", body)
        self.assertIn("Received: 2026-11-01 09:30 UTC", body)
        self.assertIn("Inbox: https://learnova.example.org/admin/contact-messages", body)


if __name__ == "__main__":
    unittest.main()

"""Tests for learner grade selection and multi-language interface support."""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_grade_lang_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import profiles  # noqa: E402
from learnova.translations import catalog  # noqa: E402
from learnova.translations import (  # noqa: E402
    LANGUAGES, SUPPORTED_LANGUAGES, is_rtl, language_direction, language_options, translate,
)

HTML = {"Accept": "text/html"}


class ProfileGradeTests(unittest.TestCase):
    def test_grade_choices_cover_1_to_13_plus_levels(self):
        self.assertEqual(profiles.NUMERIC_GRADES, tuple(str(n) for n in range(1, 14)))
        for level in ("university", "vocational", "other"):
            self.assertIn(level, profiles.GRADE_CHOICES)
        self.assertEqual(len(profiles.GRADE_CHOICES), 16)

    def test_validation_and_normalization(self):
        self.assertTrue(profiles.is_valid_grade("7"))
        self.assertTrue(profiles.is_valid_grade("university"))
        self.assertFalse(profiles.is_valid_grade("14"))
        self.assertEqual(profiles.normalize_grade(" University "), "university")
        self.assertEqual(profiles.normalize_grade("99"), "")
        self.assertEqual(profiles.normalize_grade(None), "")

    def test_descriptor_matches_level_and_is_empty_when_unset(self):
        self.assertIn("grade 7", profiles.grade_descriptor("7").lower())
        self.assertIn("university", profiles.grade_descriptor("university").lower())
        self.assertIn("vocational", profiles.grade_descriptor("vocational").lower())
        self.assertEqual(profiles.grade_descriptor(""), "")
        self.assertEqual(profiles.grade_descriptor("other"), "")

    def test_labels(self):
        self.assertEqual(profiles.grade_label("7"), "Grade 7")
        self.assertEqual(profiles.grade_label("university"), "University")
        self.assertEqual(profiles.grade_label(""), "Not set")


class LanguageRegistryTests(unittest.TestCase):
    def test_registry_lists_all_required_languages(self):
        codes = {entry["code"] for entry in LANGUAGES}
        for code in ("en", "de", "fr", "es", "it", "pt", "nl", "pl", "tr", "ar", "hi",
                     "sv", "da", "no", "fi", "cs", "sk", "hu", "ro", "el", "hr", "sr", "uk"):
            self.assertIn(code, codes)
        self.assertGreaterEqual(len(LANGUAGES), 23)

    def test_arabic_is_rtl_others_ltr(self):
        self.assertTrue(is_rtl("ar"))
        self.assertEqual(language_direction("ar"), "rtl")
        self.assertFalse(is_rtl("en"))
        self.assertEqual(language_direction("fr"), "ltr")

    def test_only_complete_languages_are_selectable(self):
        # English + German are always complete; French was fully generated.
        self.assertIn("en", SUPPORTED_LANGUAGES)
        self.assertIn("de", SUPPORTED_LANGUAGES)
        self.assertIn("fr", SUPPORTED_LANGUAGES)
        offered = {opt["code"] for opt in language_options()}
        self.assertEqual(offered, set(SUPPORTED_LANGUAGES))

    def test_enabled_languages_have_zero_english_leakage(self):
        # Every enabled non-English language must translate every required key.
        for code in SUPPORTED_LANGUAGES:
            if code == "en":
                continue
            missing = [k for k in catalog.REQUIRED_KEYS if not str(catalog.CATALOGS.get(code, {}).get(k, "")).strip()]
            self.assertEqual(missing, [], f"{code} leaks English for {len(missing)} keys")

    def test_translate_returns_target_language_not_english(self):
        self.assertEqual(translate("Cancel", "en"), "Cancel")
        self.assertNotEqual(translate("Cancel", "de"), "Cancel")
        self.assertNotEqual(translate("Cancel", "fr"), "Cancel")


class RegistrationAndSettingsTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def _register(self, username="alice", language="en", grade="8"):
        return self.client.post("/register", data={
            "username": username, "email": f"{username}@example.com",
            "password": "correct-horse-battery", "language": language, "grade": grade,
        }, follow_redirects=True)

    def _user(self, username="alice"):
        with application.app.app_context():
            return application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == username))

    def test_registration_stores_grade_and_language(self):
        self._register(language="fr", grade="10")
        user = self._user()
        self.assertEqual(user.grade, "10")
        self.assertEqual(user.preferred_language, "fr")

    def test_registration_form_offers_grade_and_language(self):
        page = self.client.get("/register", headers=HTML).data
        self.assertIn(b'name="grade"', page)
        self.assertIn(b'name="language"', page)
        self.assertIn("Français".encode(), page)  # French offered (enabled)

    def test_settings_updates_grade_without_overwriting_automatically(self):
        self._register(grade="8")
        # An unrelated page load must never change the stored grade.
        self.client.get("/dashboard", headers=HTML)
        self.assertEqual(self._user().grade, "8")
        # Explicit update changes it.
        self.client.post("/settings/grade", data={"grade": "university"}, follow_redirects=True)
        self.assertEqual(self._user().grade, "university")

    def test_settings_rejects_disabled_language(self):
        self._register()
        response = self.client.post("/settings/language", data={"language": "zz"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._user().preferred_language, "en")
        # An enabled language is accepted.
        self.client.post("/settings/language", data={"language": "fr"}, follow_redirects=True)
        self.assertEqual(self._user().preferred_language, "fr")

    def test_existing_user_without_grade_is_prompted_once(self):
        # Simulate an existing account created before grades existed.
        with application.app.app_context():
            user = application.User(username="bob", email="bob@example.com", preferred_language="en", grade="")
            user.set_password("correct-horse-battery")
            application.db.session.add(user)
            application.db.session.commit()
        self.client.post("/login", data={"identifier": "bob", "password": "correct-horse-battery"})
        # A normal HTML page GET redirects to the one-time grade prompt.
        gated = self.client.get("/dashboard", headers=HTML)
        self.assertEqual(gated.status_code, 302)
        self.assertIn("/onboarding/grade", gated.headers["Location"])
        # Skipping dismisses the prompt for the session (never forces a value).
        self.client.post("/onboarding/grade/skip", follow_redirects=False)
        self.assertEqual(self.client.get("/dashboard", headers=HTML).status_code, 200)
        self.assertEqual(self._user("bob").grade, "")

    def test_onboarding_gate_never_breaks_api_calls(self):
        # API/JSON requests (no text/html Accept) must not be redirected by the gate.
        with application.app.app_context():
            user = application.User(username="carol", email="carol@example.com", preferred_language="en", grade="")
            user.set_password("correct-horse-battery")
            application.db.session.add(user)
            application.db.session.commit()
        self.client.post("/login", data={"identifier": "carol", "password": "correct-horse-battery"})
        self.assertEqual(self.client.get("/api/flashcards/sets").status_code, 200)


class RtlRenderingTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def test_rtl_language_renders_dir_rtl(self):
        # Force an RTL language active regardless of catalogue completeness for this check.
        original = application.SUPPORTED_LANGUAGES
        application.SUPPORTED_LANGUAGES = tuple(sorted(set(original) | {"ar"}))
        try:
            self.client.post("/register", data={
                "username": "rtluser", "email": "rtl@example.com",
                "password": "correct-horse-battery", "language": "ar", "grade": "8"},
                follow_redirects=True)
            page = self.client.get("/settings", headers=HTML).data
            self.assertIn(b'dir="rtl"', page)
            self.assertIn(b'lang="ar"', page)
        finally:
            application.SUPPORTED_LANGUAGES = original

    def test_ltr_language_renders_dir_ltr(self):
        self.client.post("/register", data={
            "username": "ltruser", "email": "ltr@example.com",
            "password": "correct-horse-battery", "language": "en", "grade": "8"},
            follow_redirects=True)
        page = self.client.get("/settings", headers=HTML).data
        self.assertIn(b'dir="ltr"', page)


class GradeInPromptTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def test_grade_descriptor_reaches_tutor_instructions(self):
        self.client.post("/register", data={
            "username": "dan", "email": "dan@example.com",
            "password": "correct-horse-battery", "language": "en", "grade": "5"},
            follow_redirects=True)
        with self.client:
            self.client.get("/dashboard", headers=HTML)  # establish request/user context
            instructions = application.tutor_instructions("Mathematics")
        self.assertIn("grade 5", instructions.lower())


if __name__ == "__main__":
    unittest.main()

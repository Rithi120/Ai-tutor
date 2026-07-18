import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_i18n_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        import json
        self.output_text = json.dumps(payload)


LESSON = {
    "lesson_title": "Brüche",
    "detected_level": "Anfang",
    "concepts": [{"name": "Brüche", "evidence": "Lernziel"}],
    "explanation": "Erklärung",
    "worked_example": {"problem": "1/2", "steps": ["Schritt"], "answer": "1/2"},
    "teacher_tips": [],
    "exceptions": [],
    "question": {
        "id": "q1", "concept": "Brüche", "difficulty": 1,
        "type": "multiple_choice", "prompt": "Frage", "hint": "Hinweis",
        "options": [{"id": "a", "label": "A"}], "expected_answer": "a",
    },
}


class LanguageSystemTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def register(self, username="alice", email="alice@example.com", language="en"):
        return self.client.post(
            "/register",
            data={
                "username": username,
                "email": email,
                "password": "correct-horse-battery",
                "language": language,
            },
            follow_redirects=True,
        )

    def test_default_browser_and_onboarding_language(self):
        default = self.client.get("/login")
        self.assertIn(b'<html lang="en">', default.data)
        self.assertIn(b"Welcome back", default.data)

        browser_client = application.app.test_client()
        german = browser_client.get("/login", headers={"Accept-Language": "de-DE,de;q=0.9"})
        self.assertIn(b'<html lang="de">', german.data)
        self.assertIn("Willkommen zurück".encode(), german.data)

        response = self.register(language="de")
        self.assertIn(b'<html lang="de">', response.data)
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            self.assertEqual(user.preferred_language, "de")

    def test_switch_persists_refresh_logout_login_and_new_client(self):
        self.register()
        switched = self.client.post(
            "/settings/language",
            data={"language": "de", "next": "/dashboard"},
            follow_redirects=True,
        )
        self.assertIn("Lernübersicht".encode(), switched.data)
        self.assertNotIn(b"language-switch", switched.data)
        self.assertNotIn(b"?lang=", switched.data)

        refreshed = self.client.get("/settings")
        self.assertIn("Einstellungen".encode(), refreshed.data)
        self.assertIn(b'<option value="de" selected>', refreshed.data)

        logged_out = self.client.post("/logout", follow_redirects=True)
        self.assertIn("Willkommen zurück".encode(), logged_out.data)
        logged_in = self.client.post(
            "/login",
            data={"identifier": "alice", "password": "correct-horse-battery"},
            follow_redirects=True,
        )
        self.assertIn(b'<html lang="de">', logged_in.data)

        application.SESSIONS.clear()
        restarted_client = application.app.test_client()
        restarted = restarted_client.post(
            "/login",
            data={"identifier": "alice", "password": "correct-horse-battery"},
            follow_redirects=True,
        )
        self.assertIn(b'<html lang="de">', restarted.data)

        english = restarted_client.post(
            "/settings/language",
            data={"language": "en"},
            follow_redirects=True,
        )
        self.assertIn(b"Settings", english.data)
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            self.assertEqual(user.preferred_language, "en")

    def test_validation_is_translated_and_unsupported_value_is_rejected(self):
        with self.client.session_transaction() as session:
            session["language"] = "de"
        invalid_registration = self.client.post(
            "/register",
            data={
                "username": "validname",
                "email": "not-an-email",
                "password": "correct-horse-battery",
                "language": "de",
            },
            follow_redirects=True,
        )
        self.assertIn("Gib eine gültige E-Mail-Adresse ein.".encode(), invalid_registration.data)

        self.register(language="de")
        rejected = self.client.post(
            "/settings/language", data={"language": "fr"}, follow_redirects=False
        )
        self.assertEqual(rejected.status_code, 400)
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            self.assertEqual(user.preferred_language, "de")

    def test_preference_update_is_current_user_only_and_redirect_is_safe(self):
        self.register()
        self.client.post("/logout")
        self.register("bob", "bob@example.com", "en")
        unsafe = self.client.post(
            "/settings/language",
            data={"language": "de", "next": "https://attacker.example/steal"},
            follow_redirects=False,
        )
        self.assertEqual(unsafe.headers["Location"], "/settings")
        with application.app.app_context():
            alice = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            bob = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "bob")
            )
            self.assertEqual(alice.preferred_language, "en")
            self.assertEqual(bob.preferred_language, "de")

    def test_shared_navigation_and_frontend_catalog_cover_learning_surfaces(self):
        self.register(language="de")
        for path in ("/", "/dashboard", "/projects", "/practice/today", "/settings"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'class="settings-link"', response.data)
            self.assertIn(b'class="mobile-nav-toggle"', response.data)
            self.assertIn("Einstellungen".encode(), response.data)

        lesson = self.client.get("/")
        self.assertIn(b"window.LEARNOVA_LANGUAGE = \"de\"", lesson.data)
        self.assertIn(b'"checkAnswer": "Antwort pr\\u00fcfen"', lesson.data)
        self.assertIn("Prüfungsmodus".encode(), lesson.data)
        dashboard = self.client.get("/dashboard")
        self.assertIn("Fehlernotizbuch".encode(), dashboard.data)
        self.assertNotIn(b"language-switch", dashboard.data)

    def test_ai_learning_content_uses_account_language_not_request_payload(self):
        self.register(language="de")
        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)) as mocked:
            response = self.client.post(
                "/api/analyze",
                data={"study_goal": "Brüche", "subject": "Mathematics", "language": "English"},
            )
        self.assertEqual(response.status_code, 200)
        instructions = mocked.call_args.kwargs["instructions"]
        prompt = mocked.call_args.kwargs["input"][0]["content"][0]["text"]
        self.assertIn("Antworte vollständig auf Deutsch.", instructions)
        self.assertIn("Write all student-facing content in German.", prompt)
        session_id = response.get_json()["session_id"]
        self.assertEqual(application.SESSIONS[session_id]["language"], "German")


if __name__ == "__main__":
    unittest.main()

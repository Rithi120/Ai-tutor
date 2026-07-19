import os
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_auth_ui_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class AuthenticationUiTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def test_login_and_registration_render_accessible_motion_component(self):
        for path, mode in (("/login", "login"), ("/register", "register")):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'class="auth-background" aria-hidden="true"', response.data)
            self.assertEqual(response.data.count(b'class="auth-ellipse '), 6)
            self.assertIn(f'data-auth-mode="{mode}"'.encode(), response.data)
            self.assertIn(b'name="csrf_token"', response.data)
            self.assertIn(b'type="button" aria-controls="authPassword"', response.data)
            self.assertIn(b'/static/css/auth.css', response.data)
            self.assertIn(b'/static/js/auth.js', response.data)

    def test_german_labels_and_loading_messages_use_central_catalogue(self):
        with self.client.session_transaction() as session:
            session["language"] = "de"
        login = self.client.get("/login")
        self.assertIn("Willkommen zurück".encode(), login.data)
        self.assertIn("Benutzername oder E-Mail".encode(), login.data)
        self.assertIn("Passwort anzeigen".encode(), login.data)
        self.assertIn(br'"signingIn": "Anmeldung l\u00e4uft\u2026"', login.data)
        registration = self.client.get("/register")
        self.assertIn("Erstelle dein Konto".encode(), registration.data)
        self.assertIn(br'"creatingAccount": "Konto wird erstellt\u2026"', registration.data)

    def test_failed_login_preserves_identifier_and_error_is_inside_card(self):
        response = self.client.post(
            "/login",
            data={"identifier": "student@example.com", "password": "wrong-password"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'value="student@example.com"', response.data)
        card_position = response.data.index(b'class="auth-card"')
        error_position = response.data.index(b'Invalid username, email, or password.')
        self.assertGreater(error_position, card_position)

    def test_authentication_still_submits_without_javascript(self):
        registered = self.client.post(
            "/register",
            data={
                "username": "student",
                "email": "student@example.com",
                "password": "correct-horse-battery",
                "language": "en",
            },
            follow_redirects=False,
        )
        self.assertEqual(registered.status_code, 302)
        self.client.post("/logout")
        logged_in = self.client.post(
            "/login",
            data={"identifier": "student", "password": "correct-horse-battery", "remember": "on"},
            follow_redirects=False,
        )
        self.assertEqual(logged_in.status_code, 302)

    def test_auth_css_has_reduced_motion_and_mobile_overflow_guards(self):
        css = Path("static/css/auth.css").read_text(encoding="utf-8")
        self.assertIn("@media (prefers-reduced-motion: reduce)", css)
        self.assertIn("overflow-x: hidden", css)
        self.assertIn("@media (max-width: 390px)", css)
        self.assertIn("@media (max-width: 320px)", css)
        self.assertIn("pointer-events: none", css)


if __name__ == "__main__":
    unittest.main()

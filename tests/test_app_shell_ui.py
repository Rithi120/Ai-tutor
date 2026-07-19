import os
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_app_shell_ui_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class AuthenticatedAppShellTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def register(self, language="en"):
        return self.client.post(
            "/register",
            data={
                "username": "student",
                "email": "student@example.com",
                "password": "correct-horse-battery",
                "language": language,
            },
            follow_redirects=False,
        )

    def test_desktop_navigation_uses_compact_more_and_profile_menus(self):
        self.register()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        html = response.data
        self.assertIn(b'class="desktop-app-nav global-nav-links"', html)
        self.assertIn(b'aria-controls="moreMenu"', html)
        self.assertIn(b'id="moreMenu" class="app-dropdown" role="menu"', html)
        self.assertIn(b'Mistake Notebook', html)
        self.assertIn(b"Today&#39;s Practice", html)
        self.assertIn(b'Exam Mode', html)
        self.assertIn(b'aria-controls="profileMenu"', html)
        self.assertIn(b'id="profileMenu"', html)
        self.assertIn(b'class="settings-link', html)
        self.assertIn(b'action="/logout"', html)
        desktop = html.split(b'<nav class="desktop-app-nav', 1)[1].split(b"</nav>", 1)[0]
        self.assertEqual(desktop.count(b'class="app-nav-link'), 3)
        self.assertIn(b'href="/"', desktop)
        self.assertIn(b'New Lesson', desktop)
        self.assertIn(b'id="moreMenu"', desktop)

    def test_mobile_menu_has_all_links_and_accessibility_controls(self):
        self.register()
        html = self.client.get("/dashboard").data
        self.assertIn(b'class="mobile-nav-toggle"', html)
        self.assertIn(b'aria-controls="mobileAppMenu"', html)
        self.assertIn(b'aria-expanded="false"', html)
        self.assertIn(b'id="mobileAppMenu"', html)
        mobile = html.split(b'id="mobileAppMenu"', 1)[1].split(b"</nav>", 1)[0]
        for label in (
            b"Overview", b"New Lesson", b"Projects", b"Mistake Notebook", b"Today&#39;s Practice",
            b"Exam Mode", b"Settings", b"Language", b"Log out",
        ):
            self.assertIn(label, mobile)
        self.assertLess(mobile.index(b"Overview"), mobile.index(b"New Lesson"))
        self.assertLess(mobile.index(b"New Lesson"), mobile.index(b"Projects"))

    def test_active_state_and_pages_work_without_javascript(self):
        self.register()
        for path in ("/dashboard", "/projects", "/practice/today", "/settings"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"authenticated-app", response.data)
        dashboard = self.client.get("/dashboard").data
        self.assertIn(b'class="app-nav-link is-active" href="/dashboard" aria-current="page"', dashboard)
        projects = self.client.get("/projects").data
        self.assertIn(b'href="/projects" aria-current="page"', projects)

    def test_new_lesson_link_uses_existing_route_and_has_separate_active_state(self):
        self.register()
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "student")
            )
            self.assertIsNotNone(user)
            lesson = application.Lesson(
                user_id=user.id,
                session_id="navigation-lesson",
                subject="Mathematics",
                title="Navigation lesson",
                content_json="{}",
            )
            project = application.LearningProject(
                user_id=user.id,
                title="Navigation project",
                subject="Mathematics",
            )
            application.db.session.add_all([lesson, project])
            application.db.session.commit()
            lesson_id = lesson.id
            project_id = project.id

        pages = (
            "/dashboard",
            "/projects",
            "/settings",
            f"/lessons/{lesson_id}",
            f"/projects/{project_id}/exam/new",
        )
        for path in pages:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'New Lesson', response.data)
            self.assertIn(b'href="/"', response.data)

        lesson_creation = self.client.get("/")
        self.assertEqual(lesson_creation.status_code, 200)
        self.assertIn(
            b'class="app-nav-link nav-primary-action is-active" href="/" aria-current="page"',
            lesson_creation.data,
        )
        self.assertNotIn(b'href="/dashboard" aria-current="page"', lesson_creation.data)
        self.assertEqual(lesson_creation.data.count(b'id="uploadForm"'), 1)

        dashboard = self.client.get("/dashboard").data
        self.assertIn(b'href="/dashboard" aria-current="page"', dashboard)
        self.assertNotIn(b'href="/" aria-current="page"', dashboard)
        self.assertNotIn(b'id="uploadForm"', dashboard)
        index_rules = [rule for rule in application.app.url_map.iter_rules() if rule.endpoint == "index"]
        self.assertEqual(len(index_rules), 1)
        self.assertEqual(index_rules[0].rule, "/")

    def test_german_navigation_uses_central_translations(self):
        self.register(language="de")
        html = self.client.get("/dashboard").data
        for label in ("Übersicht", "Neue Lektion", "Projekte", "Mehr", "Konto", "Fehlernotizbuch", "Heutige Übung", "Prüfungsmodus", "Einstellungen", "Sprache", "Abmelden"):
            self.assertIn(label.encode(), html)

    def test_motion_navigation_and_mobile_overflow_safeguards_exist(self):
        motion = Path("static/css/motion.css").read_text(encoding="utf-8")
        navigation = Path("static/css/navigation.css").read_text(encoding="utf-8")
        script = Path("static/js/navigation.js").read_text(encoding="utf-8")
        self.assertIn("--motion-fast: 160ms", motion)
        self.assertIn("@media (prefers-reduced-motion: reduce)", motion)
        self.assertIn("overflow-x: hidden", motion)
        self.assertIn("@media (max-width: 390px)", navigation)
        self.assertIn("calc(100vw - 16px)", navigation)
        self.assertIn('event.key === "Escape"', script)
        self.assertIn('event.key === "Tab"', script)
        self.assertIn("mobile-menu-open", script)


if __name__ == "__main__":
    unittest.main()

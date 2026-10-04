"""The first-time walkthrough: when it offers itself, how it is remembered, what it says.

The steps live in static/js/tour-rules.js and run in the browser; what the server owns is
the shell markup on every signed-in page, the per-account "finished" flag behind
POST /api/tour/complete, and the translated step texts in the frontend catalogue.
"""

import os
import tempfile
import unittest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.gettempdir(), "learnova-tour-test.db"))

import app as application  # noqa: E402
from learnova.translations import catalog  # noqa: E402

TOUR_KEYS = ("tourWelcomeTitle", "tourWelcomeText", "tourLetsGo", "tourOpenMenu", "tourGoNewLesson",
             "tourSubject", "tourGoal", "tourBuild", "tourLesson", "tourStartTest", "tourChat",
             "tourGoOverview", "tourDoneTitle", "tourDoneText", "tourFinish", "tourStepOf")


class TourTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def register(self, language="en"):
        return self.client.post("/register", data={
            "username": "alice", "email": "alice@example.com",
            "password": "correct-horse-battery", "language": language}, follow_redirects=True)

    def test_a_new_account_is_offered_the_tour_wherever_it_lands(self):
        landing = self.register().data.decode()
        # Registration ends on the New Lesson page, so that is where the tour must begin.
        self.assertIn('id="tourRoot"', landing)
        self.assertIn('data-tour-autostart="true"', landing)
        self.assertIn('data-tour-page="index"', landing)
        dashboard = self.client.get("/dashboard").data.decode()
        self.assertIn('data-tour-autostart="true"', dashboard)
        self.assertIn('data-tour-page="dashboard"', dashboard)
        self.assertIn('data-tour-page="other"', self.client.get("/projects").data.decode())

    def test_finishing_or_skipping_is_remembered_per_account_not_per_browser(self):
        self.register()
        response = self.client.post("/api/tour/complete", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice"))
            self.assertIsNotNone(user.tour_completed_at)
        fresh_browser = application.app.test_client()
        fresh_browser.post("/login", data={"identifier": "alice", "password": "correct-horse-battery"})
        page = fresh_browser.get("/").data.decode()
        self.assertIn('data-tour-autostart="false"', page)
        self.assertIn("data-tour-open", page, "it can still be restarted by hand")

    def test_completing_twice_keeps_the_first_time(self):
        self.register()
        self.client.post("/api/tour/complete", json={})
        with application.app.app_context():
            first = application.db.session.scalar(
                application.db.select(application.User.tour_completed_at))
        self.client.post("/api/tour/complete", json={})
        with application.app.app_context():
            second = application.db.session.scalar(
                application.db.select(application.User.tour_completed_at))
        self.assertEqual(first, second)

    def test_completion_needs_a_signed_in_student(self):
        response = self.client.post("/api/tour/complete", json={})
        self.assertIn(response.status_code, (302, 401))

    def test_the_shell_points_at_the_real_navigation_and_can_be_reopened(self):
        self.register()
        page = self.client.get("/dashboard").data.decode()
        self.assertEqual(page.count("data-tour-open"), 3, "profile menu, mobile menu, dashboard invite")
        for target in ("overview", "new-lesson", "projects"):
            self.assertIn(f'data-tour-target="{target}"', page)
        for mask in ("top", "bottom", "left", "right"):
            self.assertIn(f'data-mask="{mask}"', page)
        self.assertIn('src="/static/js/tour.js"', page)
        self.assertIn('href="/static/css/tour.css', page, "linked, with or without a cache-busting version")

    def test_signed_out_pages_have_no_tour(self):
        page = self.client.get("/login").data.decode()
        self.assertNotIn("tourRoot", page)
        self.assertNotIn("tour.js", page)

    def test_the_lesson_page_still_has_everything_the_steps_point_at(self):
        # tour-rules.js names these selectors; if the page changes, the tour must follow.
        self.register()
        page = self.client.get("/").data.decode()
        for marker in ('class="subject-picker"', 'class="goal-field"', 'class="prompt-ideas"',
                       'begin-button', 'class="content-card explanation-card"', 'id="startTest"',
                       'id="chatToggle"'):
            self.assertIn(marker, page, marker)

    def test_every_step_text_reaches_the_browser_in_the_students_language(self):
        self.register(language="de")
        page = self.client.get("/").data.decode()
        self.assertIn('"tourGoNewLesson": "Tippe auf Neue Lektion', page)
        self.assertIn('"tourStepOf": "Schritt {current} von {total}"', page)
        self.assertNotIn("Tap New Lesson", page)
        for code in catalog.SUPPORTED_LANGUAGES:
            if code == "en":
                continue
            strings = catalog.frontend_catalog(code)
            for key in TOUR_KEYS:
                self.assertNotEqual(strings[key], catalog.FRONTEND_MESSAGES[key], f"{code} leaks English for {key}")

    def test_the_old_card_tour_strings_are_gone(self):
        self.assertNotIn("Start my first lesson", catalog.GERMAN)
        self.assertNotIn("Where", catalog.GERMAN)


if __name__ == "__main__":
    unittest.main()

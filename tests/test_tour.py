"""The first-time tutorial offer: when it offers itself, how it is remembered, what it says.

The steps live in the Python registry (learnova/tutorial/registry.py) and run in the
browser (static/js/tour.js); what the server owns is the shell markup on every signed-in
page, the per-account "offer settled" flag behind POST /api/tour/complete, and the
translated steps (inline in #tourData while the offer is open). The sections, progress API
and hooks are tested in tests/test_tutorial.py.
"""

import os
import tempfile
import unittest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(tempfile.gettempdir(), "learnova-tour-test.db"))

import app as application  # noqa: E402
from learnova.translations import catalog  # noqa: E402
from learnova.tutorial import registry  # noqa: E402


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
        # Registration ends on the home page - the chat - where the first-time offer opens;
        # the lesson builder (/lesson) is the "index" page.
        self.assertIn('id="tourRoot"', landing)
        self.assertIn('data-tour-autostart="true"', landing)
        self.assertIn('data-tour-page="chat"', landing)
        self.assertIn('data-tour-page="index"', self.client.get("/lesson").data.decode())
        dashboard = self.client.get("/dashboard").data.decode()
        self.assertIn('data-tour-autostart="true"', dashboard)
        self.assertIn('data-tour-page="dashboard"', dashboard)
        self.assertIn('data-tour-page="projects"', self.client.get("/projects").data.decode())

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
        # Afterwards the tutorial is opened by hand from its hub (/tutorial, PLAN 7.3): the
        # interim [data-tour-open] links that re-showed the welcome card are gone.
        self.assertIn('href="/tutorial" data-tutorial-link', page, "it can still be opened by hand")
        self.assertNotIn("data-tour-open", page)

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
        # Every entry point opens the hub (PLAN 7.3): More menu, Account menu, phone menu,
        # the Overview invite and the footer.
        self.assertEqual(page.count("data-tutorial-link"), 5, "More, Account, mobile menu, dashboard invite, footer")
        self.assertNotIn("data-tour-open", page)
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
        page = self.client.get("/lesson").data.decode()
        for marker in ('class="subject-picker"', 'class="goal-field"', 'class="prompt-ideas"',
                       'begin-button', 'class="content-card explanation-card"', 'id="startTest"',
                       'id="chatToggle"', 'id="lessonParts"', 'data-tour-target="lesson-explanation"'):
            self.assertIn(marker, page, marker)

    def test_every_step_text_reaches_the_browser_in_the_students_language(self):
        self.register(language="de")
        page = self.client.get("/").data.decode()
        data = page.split('id="tourData"', 1)[1].split("</script>", 1)[0]
        # The lesson chapter keeps the old lesson tour's sentences (tojson escapes non-ASCII).
        self.assertIn("Tippe auf Meine Lektion erstellen", data)
        self.assertIn("Bau eine kurze Lektion mit Erkl", data, "the quick tour's New Lesson step")
        self.assertIn("Schritt {current} von {total}", data)
        self.assertNotIn("Tap Build my lesson", page)
        self.assertNotIn("Das erscheint, sobald du es zum ersten Mal benutzt hast", data)
        for code in catalog.SUPPORTED_LANGUAGES:
            if code == "en":
                continue
            for msgid in sorted(registry.all_msgids()):
                self.assertIn(msgid, catalog.CATALOGS[code], f"{code} has no translation for {msgid!r}")

    def test_the_old_card_tour_strings_are_gone(self):
        self.assertNotIn("Start my first lesson", catalog.GERMAN)
        self.assertNotIn("Where", catalog.GERMAN)


if __name__ == "__main__":
    unittest.main()

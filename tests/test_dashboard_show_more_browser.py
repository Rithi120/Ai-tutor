""""Show more" and the delete confirmation in a real browser (owner request 2026-11-13).

The app runs on a local port (scripts/mobile_audit.start_server: scratch database, the AI
patched out); a student with long lists is written straight into its database
(tests/dashboard_seed.py). At 390 and 320 px, light and dark: the dashboard's Mistake
Notebook and lessons and Settings' code history fold behind a 44 px "Show more (N)" button
that reveals the next items, moves the focus to the first of them and becomes "Show
less"; nothing scrolls sideways; the delete buttons are 44 px and red in both themes.
Without JavaScript every item stays visible. The delete forms ask once: cancelling keeps
the lesson, confirming deletes it. Skipped when Playwright or a Chromium-based browser is
missing (run with R:/ai_tutor/.venv/Scripts/python).
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_show_more_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parents[1]

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

PASSWORD = "correct-horse-battery"
MISTAKE_LESSONS = 8          # one mistake each: the notebook shows 3, "Show more (5)"
CODES = 6                    # Settings shows 3, "Show more (3)"
PROJECTS = 5                 # the dashboard shows 3, "Show more (2)"

VISIBLE_JS = """(selector) => [...document.querySelector(selector).children]
  .filter(item => !item.hidden && getComputedStyle(item).display !== 'none').length"""
SIDEWAYS_JS = "() => document.documentElement.scrollWidth - window.innerWidth"


def _audit_module():
    spec = importlib.util.spec_from_file_location("mobile_audit", ROOT / "scripts" / "mobile_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["mobile_audit"] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class ShowMoreBrowserTests(unittest.TestCase):
    PORT = 5087

    @classmethod
    def setUpClass(cls):
        cls.audit = _audit_module()
        cls.application, cls.server = cls.audit.start_server(cls.PORT)
        cls.base = f"http://127.0.0.1:{cls.PORT}"
        assert sync_playwright is not None
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.audit.launch(cls.playwright)
        except SystemExit as error:
            cls.playwright.stop()
            cls.server.shutdown()
            raise unittest.SkipTest(str(error))
        context = cls.browser.new_context()
        cls.audit.block_external(context, cls.base)
        page = context.new_page()
        page.goto(f"{cls.base}/register")
        page.request.post(f"{cls.base}/register", form={
            "username": "showmore", "email": "showmore@example.com", "password": PASSWORD, "grade": "8"})
        page.request.post(f"{cls.base}/api/tour/complete")
        page.request.post(f"{cls.base}/tutorial/tips", form={"back": "hub"})
        # English unless a test switches (a new account may start in German).
        page.request.post(f"{cls.base}/settings/language", form={"language": "en"})
        cls.state = context.storage_state()
        context.close()
        cls.seed()

    @classmethod
    def seed(cls):
        from tests import dashboard_seed as seed

        application = cls.application
        db = application.db
        with application.app.app_context():
            user_id = db.session.scalar(db.select(application.User.id).where(application.User.username == "showmore"))
            cls.user_id = user_id
            for number in range(MISTAKE_LESSONS):
                seed.short_lesson(application, user_id, title=f"Lektion über ein ziemlich langes Thema Nummer {number}",
                                  mistakes=1, created_offset=number)
            for number in range(PROJECTS):
                project = application.LearningProject(user_id=user_id, subject="Physics",
                                                      title=f"Physik – Mechanik und Energieerhaltung {number}")
                db.session.add(project)
            seed.premium_codes(application, user_id, CODES)
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()

    def page(self, width=390, height=844, *, theme="light", javascript=True):
        context = self.browser.new_context(
            viewport={"width": width, "height": height}, is_mobile=True, has_touch=True,
            storage_state=self.state, java_script_enabled=javascript)
        context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, [], "script errors"))
        return page

    def open(self, page, path):
        response = page.goto(self.base + path, wait_until="load")
        self.assertEqual(response.status, 200)
        page.wait_for_timeout(500)       # the cards' entrance animation

    def open_subscription_row(self, page):
        # Settings is a list of collapsed rows; the plan comparison and the code history
        # live in the "Abo" row, which the student taps first (a native <details>, so
        # this works with and without JavaScript).
        row = page.locator("#subscription-group")
        self.assertIsNone(row.get_attribute("open"), "the Abo row starts closed")
        page.locator("#subscription-group > summary").click()
        page.wait_for_timeout(200)
        self.assertIsNotNone(row.get_attribute("open"), "tapping the row opens it")

    def count(self, model):
        application = self.application
        with application.app.app_context():
            return application.db.session.scalar(application.db.select(application.db.func.count(model.id)).where(
                model.user_id == self.user_id))

    def assert_button(self, page, selector, *, label, controls=None):
        button = page.locator(selector)
        self.assertEqual(button.count(), 1, selector)
        self.assertEqual(button.inner_text().strip(), label)
        box = button.bounding_box()
        self.assertGreaterEqual(box["height"], 44)
        viewport = page.viewport_size
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], viewport["width"] + 1)
        if controls:
            self.assertEqual(button.get_attribute("aria-controls"), controls)
        return button

    def test_lists_fold_and_unfold_at_phone_widths_in_both_themes(self):
        for width, height in ((390, 844), (320, 568)):
            for theme in ("light", "dark"):
                with self.subTest(width=width, theme=theme):
                    page = self.page(width, height, theme=theme)
                    self.open(page, "/dashboard")
                    self.assertLessEqual(page.evaluate(SIDEWAYS_JS), 1, "the dashboard scrolls sideways")
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#mistakeList"), 3)
                    more = self.assert_button(page, "#mistakeList + .show-more-button",
                                              label=f"Show more ({MISTAKE_LESSONS - 3})", controls="mistakeList")
                    self.assertEqual(more.get_attribute("aria-expanded"), "false")
                    more.click()
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#mistakeList"), MISTAKE_LESSONS)
                    self.assertTrue(page.evaluate(
                        "() => document.activeElement === document.querySelector('#mistakeList').children[3]"),
                        "the focus moves to the first item revealed")
                    self.assertEqual(more.inner_text().strip(), "Show less")
                    self.assertEqual(more.get_attribute("aria-expanded"), "true")
                    self.assertLessEqual(page.evaluate(SIDEWAYS_JS), 1)
                    more.click()
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#mistakeList"), 3)
                    # Saved lessons: five, then the rest; projects: three, then the rest.
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#dashboardLessons"), 5)
                    self.assert_button(page, "#dashboardLessons + .show-more-button",
                                       label=f"Show more ({MISTAKE_LESSONS - 5})")
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#dashboardProjects"), 3)
                    projects = self.assert_button(page, "#dashboardProjects + .show-more-button",
                                                  label=f"Show more ({PROJECTS - 3})")
                    projects.click()
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#dashboardProjects"), PROJECTS)
                    # Every visible delete control: a 44 px target inside the screen, in red.
                    buttons = page.locator(".item-delete-button:visible")
                    self.assertGreaterEqual(buttons.count(), 5 + PROJECTS)
                    for index in range(buttons.count()):
                        box = buttons.nth(index).bounding_box()
                        self.assertGreaterEqual(min(box["width"], box["height"]), 44)
                        self.assertLessEqual(box["x"] + box["width"], width + 1)
                    red, green, blue = page.evaluate("""() => {
                        const paint = document.createElement('canvas').getContext('2d');
                        paint.fillStyle = getComputedStyle(document.querySelector('.saved-actions .item-delete-button')).color;
                        paint.fillRect(0, 0, 1, 1);
                        return [...paint.getImageData(0, 0, 1, 1).data].slice(0, 3); }""")
                    self.assertGreater(red, green + 60, f"the delete label is red ({red}, {green}, {blue})")
                    self.assertGreater(red, blue + 30)
                    self.assertLessEqual(page.evaluate(SIDEWAYS_JS), 1)

                    self.open(page, "/settings")
                    self.assertLessEqual(page.evaluate(SIDEWAYS_JS), 1, "Settings scrolls sideways")
                    self.open_subscription_row(page)
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#redemptionHistory"), 3)
                    codes = self.assert_button(page, "#redemptionHistory + .show-more-button",
                                               label=f"Show more ({CODES - 3})", controls="redemptionHistory")
                    codes.click()
                    self.assertEqual(page.evaluate(VISIBLE_JS, "#redemptionHistory"), CODES)
                    self.assertTrue(page.evaluate(
                        "() => document.activeElement === document.querySelector('#redemptionHistory').children[3]"))
                    self.assertLessEqual(page.evaluate(SIDEWAYS_JS), 1)

    @staticmethod
    def settle(page):
        """Wait until the page stops scrolling (html has scroll-behavior: smooth)."""

        last = None
        for _ in range(40):
            page.wait_for_timeout(100)
            now = page.evaluate("() => window.scrollY")
            if now == last:
                return
            last = now

    def test_show_less_keeps_its_button_below_the_top_navigation(self):
        # The student scrolls down to "Show less" and taps it: the list folds above the
        # button, which the script then scrolls back into view. It must land below the
        # sticky navigation (scroll-margin-top), never under it (WCAG 2.4.11).
        for width, height in ((390, 844), (320, 568)):
            with self.subTest(width=width):
                page = self.page(width, height)
                self.open(page, "/dashboard")
                button = page.locator("#dashboardLessons + .show-more-button")
                button.click()
                self.settle(page)
                self.assertEqual(button.inner_text().strip(), "Show less")
                page.evaluate("""() => {
                    const button = document.querySelector('#dashboardLessons + .show-more-button');
                    window.scrollTo({top: window.scrollY + button.getBoundingClientRect().top - 120,
                                     behavior: 'instant'}); }""")
                self.settle(page)
                button.click()
                self.settle(page)
                self.assertEqual(page.evaluate(VISIBLE_JS, "#dashboardLessons"), 5)
                where = page.evaluate("""() => {
                    const button = document.querySelector('#dashboardLessons + .show-more-button');
                    const nav = document.querySelector('.app-nav').getBoundingClientRect();
                    const box = button.getBoundingClientRect();
                    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
                    return {top: box.top, bottom: box.bottom, navBottom: nav.bottom, focused: document.activeElement === button,
                            hit: hit === button || button.contains(hit)}; }""")
                self.assertTrue(where["focused"], where)
                self.assertGreaterEqual(where["top"], where["navBottom"] - 1, where)
                self.assertLessEqual(where["bottom"], height, where)
                self.assertTrue(where["hit"], f"the focused button is covered: {where}")

    def test_the_plan_comparison_stays_open(self):
        page = self.page(320, 568)
        self.open(page, "/settings")
        self.open_subscription_row(page)
        self.assertEqual(page.locator(".sub-plans .show-more-button").count(), 0)
        self.assertTrue(page.get_by_text("Shows ads", exact=True).is_visible())
        self.assertTrue(page.get_by_text("No ads", exact=True).is_visible())

    def test_without_javascript_every_item_is_shown(self):
        page = self.page(javascript=False)
        self.open(page, "/dashboard")
        self.assertEqual(page.locator(".show-more-button").count(), 0)
        self.assertEqual(page.locator("#mistakeList > .mistake:visible").count(), MISTAKE_LESSONS)
        self.assertEqual(page.locator("#dashboardLessons > .saved-lesson:visible").count(), MISTAKE_LESSONS)
        self.open(page, "/settings")
        self.open_subscription_row(page)
        self.assertEqual(page.locator("#redemptionHistory > li:visible").count(), CODES)

    def test_the_button_speaks_german(self):
        page = self.page()
        page.request.post(f"{self.base}/settings/language", form={"language": "de"})
        try:
            self.open(page, "/dashboard")
            button = page.locator("#mistakeList + .show-more-button")
            self.assertEqual(button.inner_text().strip(), f"Mehr anzeigen ({MISTAKE_LESSONS - 3})")
            button.click()
            self.assertEqual(button.inner_text().strip(), "Weniger anzeigen")
        finally:
            page.request.post(f"{self.base}/settings/language", form={"language": "en"})

    def test_a_delete_asks_once_and_cancelling_keeps_the_lesson(self):
        # Its own newest lesson and project, so the other tests' counts hold in any order.
        from tests import dashboard_seed as seed

        application = self.application
        with application.app.app_context():
            seed.short_lesson(application, self.user_id, title="Diese Lektion wird gelöscht", created_offset=-5)
            application.db.session.add(application.LearningProject(
                user_id=self.user_id, subject="Physics", title="Dieses Projekt wird gelöscht"))
            application.db.session.commit()
        page = self.page()
        self.open(page, "/dashboard")
        lessons = self.count(application.Lesson)
        first = page.locator("#dashboardLessons > .saved-lesson").first
        title = first.locator("b").inner_text()
        questions = []

        def answer(accept):
            def handle(dialog):
                questions.append(dialog.message)
                dialog.accept() if accept else dialog.dismiss()
            return handle

        page.once("dialog", answer(False))
        first.locator(".item-delete-button").click()
        page.wait_for_timeout(300)
        self.assertEqual(len(questions), 1)
        self.assertIn(title, questions[0])
        self.assertIn("cannot be undone", questions[0])
        self.assertIn("XP is kept", questions[0])
        self.assertEqual(self.count(application.Lesson), lessons, "cancelled: nothing deleted")
        self.assertTrue(first.locator(".item-delete-button").is_enabled(), "the button is not left busy")

        page.once("dialog", answer(True))
        with page.expect_navigation():
            first.locator(".item-delete-button").click()
        page.wait_for_load_state("load")
        self.assertEqual(self.count(application.Lesson), lessons - 1)
        self.assertIn("/dashboard", page.url)
        self.assertIn("Lesson deleted.", page.locator(".global-alerts").inner_text())
        self.assertNotIn(title, page.locator("#dashboardLessons").inner_text())

        projects = self.count(application.LearningProject)
        card = page.locator("#dashboardProjects > .dashboard-project-item").first
        page.once("dialog", answer(True))
        with page.expect_navigation():
            card.locator(".item-delete-button").click()
        page.wait_for_load_state("load")
        self.assertEqual(self.count(application.LearningProject), projects - 1)
        self.assertIn("Project deleted.", page.locator(".global-alerts").inner_text())
        self.assertIn("Your XP and your flashcards are kept.", questions[-1])


if __name__ == "__main__":
    unittest.main()

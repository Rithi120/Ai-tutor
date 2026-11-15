"""Community flashcard filters in a real browser (COMMUNITY_FLASHCARD_FILTER_FIX.md).

The app runs on a local port (scripts/mobile_audit.start_server: scratch database); the
library of tests/test_community_filters.py plus 30 filler sets is written straight into
it. At 360, 390, 430, 768 and 1440 px every filter changes the cards actually shown, the
Filter button opens and closes the panel, the count and the chips follow, reset brings
everything back, nothing scrolls sideways; search is debounced; Back/Forward walk the
filter states; "Load more" keeps the filters; "View all" keeps them too. Skipped when
Playwright or a Chromium-based browser is missing (run with R:/ai_tutor/.venv/Scripts/python).
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_community_filters_browser.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parents[1]

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from tests.test_community_filters import LIBRARY  # noqa: E402

PASSWORD = "correct-horse-battery"
FILLERS = 30
WIDTHS = (360, 390, 430, 768, 1440)
TITLES_JS = "() => [...document.querySelectorAll('#commGrid .community-card h3')].map(node => node.textContent.trim())"
SIDEWAYS_JS = "() => document.documentElement.scrollWidth - window.innerWidth"


def _audit_module():
    spec = importlib.util.spec_from_file_location("mobile_audit", ROOT / "scripts" / "mobile_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["mobile_audit"] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class CommunityFilterBrowserTests(unittest.TestCase):
    PORT = 5089

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
            "username": "sammler", "email": "sammler@example.com", "password": PASSWORD, "grade": "9"})
        page.request.post(f"{cls.base}/api/tour/complete")
        page.request.post(f"{cls.base}/tutorial/tips", form={"back": "hub"})
        page.request.post(f"{cls.base}/settings/language", form={"language": "en"})
        cls.state = context.storage_state()
        context.close()
        cls.seed()

    @classmethod
    def seed(cls):
        application = cls.application
        db = application.db
        with application.app.app_context():
            author_id = db.session.scalar(db.select(application.User.id).where(application.User.username == "sammler"))
            now = application.utcnow()
            rows = list(LIBRARY) + [(f"Filler set {number:02d}", "Other", "", "medium", "en", "flashcards", "", [], "",
                                     2.0, 1, 0, 0, 30 + number) for number in range(FILLERS)]
            for (title, subject, grade, difficulty, language, kind, topic, tags, description, ai, rank, studied,
                 saved, age) in rows:
                db.session.add(application.PublicFlashcardSet(
                    creator_id=author_id, title=title, subject=subject, grade=grade, difficulty=difficulty,
                    language=language, set_kind=kind, topic=topic, tags_json=json.dumps(tags),
                    description=description, ai_overall=ai, ranking_score=rank, study_count=studied,
                    save_count=saved, status="approved", moderation_decision="allow", card_count=10,
                    author_display="nickname", nickname="Sammler", cards_json="[]",
                    created_at=now - timedelta(days=age), published_at=now - timedelta(days=age)))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()

    # ---- helpers
    def page(self, width=390, height=860):
        context = self.browser.new_context(viewport={"width": width, "height": height}, is_mobile=width < 768,
                                           has_touch=width < 768, storage_state=self.state)
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(errors, [], "script errors"))
        return page

    def settle(self, page):
        page.wait_for_function("() => !document.querySelector('#commGrid').hasAttribute('aria-busy')")
        page.wait_for_timeout(150)

    def open(self, page, query=""):
        response = page.goto(f"{self.base}/community{query}", wait_until="load")
        self.assertEqual(response.status, 200)
        self.settle(page)

    def titles(self, page):
        return page.evaluate(TITLES_JS)

    def choose(self, page, selector, value):
        page.select_option(selector, value)
        self.settle(page)

    # ---- tests
    def test_every_width_filters_the_shown_cards(self):
        for width in WIDTHS:
            with self.subTest(width=width):
                page = self.page(width)
                self.open(page)
                self.assertEqual(len(self.titles(page)), 30, "the first page")
                self.assertTrue(page.is_visible(".community-discovery"), "highlights without filters")
                self.assertTrue(page.is_hidden("#commMoreFilters"), "the extra filters start folded")
                self.choose(page, "#commSubject", "English")
                self.assertEqual(sorted(self.titles(page)), ["Irregular verbs", "Present perfect drill",
                                                             "Reading strategies", "Vocabulary unit 4"])
                self.assertEqual(page.inner_text("#commCount").strip(), "4 sets")
                self.assertTrue(page.is_hidden(".community-discovery"), "the results come first while filtering")
                page.click("#commFilterToggle")
                self.assertEqual(page.get_attribute("#commFilterToggle", "aria-expanded"), "true")
                self.assertTrue(page.is_visible("#commMoreFilters"))
                self.choose(page, "#commGrade", "9")
                self.choose(page, "#commDifficulty", "easy")
                self.assertEqual(sorted(self.titles(page)), ["Irregular verbs", "Present perfect drill"])
                self.assertEqual(page.inner_text("#commCount").strip(), "2 sets")
                self.assertEqual(page.inner_text("#commFilterBadge").strip(), "2")
                self.assertEqual(page.locator("#commActive .comm-chip").count(), 3)
                self.assertLessEqual(page.evaluate(SIDEWAYS_JS), 0, "nothing scrolls sideways")
                for selector in ("#commFilterToggle", "#commSubject", "#commSort", "#commSearch", "#commGrade"):
                    self.assertGreaterEqual(page.locator(selector).bounding_box()["height"], 43.5, selector)
                page.click("#commFilterToggle")
                self.assertTrue(page.is_hidden("#commMoreFilters"), "the button closes the panel again")
                self.choose(page, "#commLanguage", "de") if page.is_visible("#commLanguage") else None
                page.click("#commReset")
                self.settle(page)
                self.assertEqual(len(self.titles(page)), 30)
                self.assertEqual(page.input_value("#commSubject"), "")
                self.assertEqual(page.locator("#commActive .comm-chip").count(), 0)
                self.assertTrue(page.is_visible(".community-discovery"))

    def test_language_filter_finds_sets_stored_as_names(self):
        page = self.page(1440)
        self.open(page)
        page.click("#commFilterToggle")
        self.choose(page, "#commLanguage", "de")
        self.assertEqual(sorted(self.titles(page)), ["Brüche kürzen", "Lineare Funktionen", "Photosynthese",
                                                     "Zinsrechnung"])

    def test_search_is_debounced_and_combines_with_filters(self):
        page = self.page(390)
        requests = []
        page.on("request", lambda request: requests.append(request.url) if "/api/community/library?" in request.url
                and "limit=30" in request.url else None)
        self.open(page)
        requests.clear()
        page.type("#commSearch", "steigung", delay=40)
        page.wait_for_timeout(700)
        self.settle(page)
        self.assertEqual(self.titles(page), ["Lineare Funktionen"])
        self.assertLessEqual(len(requests), 2, f"typing 8 letters made {len(requests)} requests")
        self.choose(page, "#commSubject", "English")
        self.assertEqual(self.titles(page), [])
        self.assertTrue(page.is_visible("#commEmpty"))
        page.click("#commEmpty [data-reset-filters]")
        self.settle(page)
        self.assertEqual(len(self.titles(page)), 30)
        self.assertEqual(page.input_value("#commSearch"), "")

    def test_back_and_forward_walk_the_filter_states(self):
        page = self.page(768)
        self.open(page)
        self.choose(page, "#commSubject", "English")
        page.click("#commFilterToggle")
        self.choose(page, "#commGrade", "9")
        self.assertIn("grade=9", page.url)
        self.assertEqual(len(self.titles(page)), 3)
        page.go_back()
        self.settle(page)
        self.assertEqual(page.input_value("#commGrade"), "")
        self.assertEqual(len(self.titles(page)), 4)
        page.go_back()
        self.settle(page)
        self.assertEqual(page.input_value("#commSubject"), "")
        self.assertEqual(len(self.titles(page)), 30)
        page.go_forward()
        self.settle(page)
        self.assertEqual(page.input_value("#commSubject"), "English")
        self.assertEqual(len(self.titles(page)), 4)
        # A shared link opens with the same filters and results.
        fresh = self.page(390)
        self.open(fresh, "?subject=English&grade=9&difficulty=easy")
        self.assertEqual(sorted(self.titles(fresh)), ["Irregular verbs", "Present perfect drill"])
        self.assertTrue(fresh.is_visible("#commMoreFilters"), "the panel opens when it holds a filter")

    def test_load_more_keeps_the_filters_and_view_all_keeps_them_too(self):
        page = self.page(1440)
        self.open(page)
        self.assertEqual(page.inner_text("#commCount").strip(), f"{len(LIBRARY) + FILLERS} sets")
        self.assertTrue(page.is_visible("#commMore"))
        page.click("#commMore")
        self.settle(page)
        shown = self.titles(page)
        self.assertEqual((len(shown), len(set(shown))), (len(LIBRARY) + FILLERS, len(LIBRARY) + FILLERS))
        self.assertTrue(page.is_hidden("#commMore"), "nothing more to load")
        # "View all" on the Newest shelf: newest first, over every set
        self.open(page)
        page.click("[data-apply-sort='newest']")
        self.settle(page)
        self.assertEqual(page.input_value("#commSort"), "newest")
        self.assertEqual(self.titles(page)[0], "Zinsrechnung")
        self.choose(page, "#commSubject", "English")
        self.assertEqual(page.input_value("#commSort"), "newest", "a filter keeps the order")
        self.assertEqual(self.titles(page), ["Irregular verbs", "Reading strategies", "Present perfect drill",
                                             "Vocabulary unit 4"])
        self.assertIn("sort=newest", page.url)

    def test_dark_mode_controls_are_not_light_boxes(self):
        page = self.page(390)
        page.add_init_script("try { localStorage.setItem('learnova:theme', 'dark') } catch (e) {}")
        self.open(page)
        background = page.evaluate("() => getComputedStyle(document.querySelector('#commSubject')).backgroundColor")
        self.assertNotIn(background, ("rgb(255, 255, 255)", "rgba(0, 0, 0, 0)"), background)


if __name__ == "__main__":
    unittest.main()

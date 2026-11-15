"""The shared publication badge (static/js/flashcards/common.js) run in a real browser.

library.js, overview.js and publish.js all draw their badge through `publicationBadge`,
fed with the server's `publication_summary`. This runs the module as it ships and checks
the label and class for every server state, in English and with the German catalogue.

Skipped when Playwright or a Chromium-based browser is missing (Store Python has no
Playwright: run with R:/ai_tutor/.venv/Scripts/python).
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_publication_badge_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from learnova.translations import catalog  # noqa: E402
from tests.js_browser import harness  # noqa: E402

BROWSER = {}
COMMON = "/static/js/flashcards/common.js"


def page_html(messages):
    return f"""<!doctype html>
<html><head><meta charset="utf-8">
<script nonce="{harness.NONCE}">window.LEARNOVA_I18N = {json.dumps(messages)}; window.__ready = true;</script>
</head><body></body></html>"""


def setUpModule():
    if sync_playwright is None:
        raise unittest.SkipTest("Playwright is not installed (run with R:/ai_tutor/.venv/Scripts/python)")
    playwright = sync_playwright().start()
    try:
        BROWSER["browser"] = harness.launch(playwright)
    except SystemExit as error:
        playwright.stop()
        raise unittest.SkipTest(str(error))
    BROWSER["playwright"] = playwright


def tearDownModule():
    if BROWSER:
        BROWSER["browser"].close()
        BROWSER["playwright"].stop()
        BROWSER.clear()


class PublicationBadgeTests(unittest.TestCase):
    def badge(self, page, publication):
        return harness.eval_module(page, COMMON, f"(m) => m.publicationBadge({json.dumps(publication)})")

    def open(self, language):
        page = harness.open_page(BROWSER["browser"], page_html(catalog.frontend_catalog(language)), katex="none")
        self.addCleanup(page.context.close)
        return page

    def test_every_state_in_english(self):
        page = self.open("en")
        cases = [
            (None, "private", "Private", "visibility-private"),
            ({"visibility": "private"}, "private", "Private", "visibility-private"),
            ({"visibility": "published"}, "published", "Published", "visibility-published"),
            ({"visibility": "pending"}, "pending", "Being checked", "visibility-pending"),
            ({"visibility": "action_needed"}, "action_needed", "Changes requested", "visibility-action_needed"),
            ({"visibility": "hidden"}, "hidden", "Hidden", "visibility-hidden"),
            ({"visibility": "published", "update_state": "pending"}, "published",
             "Published · update being checked", "visibility-published visibility-update-pending"),
            ({"visibility": "published", "update_state": "action_needed"}, "published",
             "Published · update not accepted", "visibility-published visibility-update-action_needed"),
            # An update label never attaches to a set that is not public.
            ({"visibility": "private", "update_state": "pending"}, "private", "Private", "visibility-private"),
            # An unknown value from an older server falls back to Private, not to a raw word.
            ({"visibility": "mystery"}, "private", "Private", "visibility-private"),
        ]
        for publication, visibility, text, class_name in cases:
            with self.subTest(publication=publication):
                self.assertEqual(self.badge(page, publication),
                                 {"visibility": visibility, "text": text, "className": class_name})

    def test_german_labels(self):
        page = self.open("de")
        self.assertEqual(self.badge(page, {"visibility": "published"})["text"], "Veröffentlicht")
        self.assertEqual(self.badge(page, {"visibility": "private"})["text"], "Privat")
        self.assertEqual(self.badge(page, {"visibility": "published", "update_state": "pending"})["text"],
                         "Veröffentlicht · Änderung wird geprüft")


if __name__ == "__main__":
    unittest.main()

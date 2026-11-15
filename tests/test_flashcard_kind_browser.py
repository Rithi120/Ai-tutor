"""The card Type (Word / Sentence / Other) in a real browser at phone width (spec Phase 4).

Two parts: the pure editor rules (static/js/flashcards/editor-rules.js) run in the page
harness, and the real editor - template, create.js, the Flask app (scripts/mobile_audit
.start_server) - where a student opens a card's three-dot menu, taps Sentence or Other,
saves, reloads, and finds the same Type selected. A card made before the Type existed
shows Word and is saved without a kind tag.

Skipped when Playwright or a Chromium-based browser is missing (Store Python has no
Playwright: run with R:/ai_tutor/.venv/Scripts/python).
"""

import os
import socket
import tempfile
import unittest
import uuid
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_flashcard_kind_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from tests.js_browser import harness  # noqa: E402

BROWSER = {}
RULES = "/static/js/flashcards/editor-rules.js"
PAGE = f"""<!doctype html>
<html><head><meta charset="utf-8">
<script nonce="{harness.NONCE}">window.__ready = true;</script></head><body></body></html>"""

# The open menu's Type buttons: box, pressed state, and what is painted at each centre.
KIND_BUTTONS_JS = """() => {
  const menu = document.querySelector('.card-menu[open] .card-menu-body');
  if (!menu) return null;
  const vw = document.documentElement.clientWidth, vh = window.innerHeight;
  return [...menu.querySelectorAll('[data-kind-choice]')].map((el) => {
    const b = el.getBoundingClientRect();
    const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return {kind: el.dataset.kindChoice, pressed: el.getAttribute('aria-pressed'), label: el.textContent.trim(),
            x: b.left, y: b.top, r: b.right, b: b.bottom, h: b.height, vw, vh, hit: !!hit && el.contains(hit),
            sideways: document.documentElement.scrollWidth > vw + 1};
  });
}"""


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


class EditorRulesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.page = harness.open_page(BROWSER["browser"], PAGE, katex="none")

    @classmethod
    def tearDownClass(cls):
        cls.page.context.close()

    def run_js(self, expression):
        return harness.eval_module(self.page, RULES, expression)

    def test_split_kind_takes_the_kind_out_of_the_tags(self):
        self.assertEqual(self.run_js("(m) => m.splitKind({tags: ['vocabulary', 'fr', 'sentence']})"),
                         {"kind": "sentence", "tags": ["vocabulary", "fr"]})
        # An explicit kind (the API's, the editor's own model) wins and is idempotent.
        self.assertEqual(self.run_js("(m) => m.splitKind(m.splitKind({kind: 'other', tags: ['word', 'x']}))"),
                         {"kind": "other", "tags": ["x"]})
        self.assertEqual(self.run_js("(m) => m.splitKind({kind: '', tags: ['cells']})"), {"kind": "", "tags": ["cells"]})
        self.assertEqual(self.run_js("(m) => m.splitKind({tags: []})"), {"kind": "", "tags": []})
        self.assertEqual(self.run_js("(m) => m.splitKind({kind: 'banana', tags: []})"), {"kind": "", "tags": []})

    def test_word_is_shown_for_words_phrases_and_untagged_cards(self):
        self.assertEqual(self.run_js("(m) => ['', 'word', 'phrase', 'sentence', 'other'].map(m.displayKind)"),
                         ["word", "word", "word", "sentence", "other"])

    def test_a_tap_changes_the_kind_only_when_it_is_a_different_type(self):
        self.assertEqual(self.run_js("(m) => [m.tapKind('', 'word'), m.tapKind('phrase', 'word'),"
                                     " m.tapKind('', 'sentence'), m.tapKind('sentence', 'other'),"
                                     " m.tapKind('other', 'word'), m.tapKind('word', 'nonsense')]"),
                         ["", "phrase", "sentence", "other", "word", "word"])

    def test_typed_type_words_are_not_tags(self):
        self.assertEqual(self.run_js("(m) => m.typedTags('cells, sentence, , biology,other')"), ["cells", "biology"])
        # Exactly as the server matches: a capitalised word is an ordinary topic tag.
        self.assertEqual(self.run_js("(m) => m.typedTags('Sentence')"), ["Sentence"])
        self.assertEqual(self.run_js("(m) => m.splitKind({tags: ['Sentence']})"), {"kind": "", "tags": ["Sentence"]})


class TypeMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.audit = harness._audit_module()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.application, cls.server = cls.audit.start_server(port)
        cls.base = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def student(self, width, height, theme):
        context = BROWSER["browser"].new_context(viewport={"width": width, "height": height},
                                                 is_mobile=width <= 430, has_touch=width <= 430)
        context.add_init_script(harness.KATEX_STUB)
        context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        name = f"k{uuid.uuid4().hex[:10]}"
        response = page.request.post(f"{self.base}/register", form={
            "username": name, "email": f"{name}@example.com", "password": "correct-horse-battery",
            "grade": "8", "language": "de"})
        self.assertLess(response.status, 400)
        page.request.post(f"{self.base}/api/tour/complete")
        page.request.post(f"{self.base}/tutorial/tips", form={"back": "hub"})
        created = page.request.post(f"{self.base}/api/flashcards/sets", data={
            "title": "Französisch", "subject": "French", "cards": [
                {"front": "Il pleut, je reste à la maison.", "back": "Es regnet, ich bleibe zu Hause."},
                {"front": "E = mc²", "back": "Energie und Masse"},
                {"front": "la maison", "back": "das Haus"}]})
        self.assertEqual(created.status, 201, created.text())
        return page, created.json()["id"]

    def kinds(self, page, set_id):
        cards = page.request.get(f"{self.base}/api/flashcards/sets/{set_id}").json()["set"]["cards"]
        return {card["front"]: (card["kind"], card["tags"]) for card in cards}

    def open_menu(self, page, row):
        page.locator(".card-row").nth(row).locator(".card-menu-trigger").click()
        page.wait_for_selector(".card-menu[open] .card-menu-body", state="visible", timeout=4000)
        page.wait_for_timeout(300)   # the toggle event places the menu after layout
        buttons = page.evaluate(KIND_BUTTONS_JS)
        self.assertEqual([b["kind"] for b in buttons], ["word", "sentence", "other"])
        for button in buttons:
            self.assertGreaterEqual(button["h"], 44, button)
            self.assertGreaterEqual(button["x"], 0, button)
            self.assertLessEqual(button["r"], button["vw"], button)
            self.assertGreaterEqual(button["y"], 0, button)
            self.assertLessEqual(button["b"], button["vh"], button)
            self.assertTrue(button["hit"], button)
            self.assertFalse(button["sideways"], button)
        for first, second in zip(buttons, buttons[1:]):
            self.assertLessEqual(first["r"], second["x"] + 0.5, (first, second))
        return {b["kind"]: b for b in buttons}

    def tap(self, page, row, kind):
        page.locator(".card-row").nth(row).locator(f'[data-kind-choice="{kind}"]').click()
        page.wait_for_selector(".card-menu[open]", state="detached", timeout=4000)

    def test_the_type_is_set_with_one_tap_and_survives_save_and_reload(self):
        for width, height, theme in ((390, 844, "light"), (320, 568, "dark")):
            with self.subTest(width=width, theme=theme):
                page, set_id = self.student(width, height, theme)
                page.goto(f"{self.base}/flashcards/{set_id}/edit", wait_until="load")
                page.wait_for_selector(".card-row .card-menu-trigger", timeout=8000)
                self.audit.settle_page(page)
                # Cards made before the Type existed: Word is shown, nothing is written.
                buttons = self.open_menu(page, 0)
                self.assertEqual({k: b["pressed"] for k, b in buttons.items()},
                                 {"word": "true", "sentence": "false", "other": "false"})
                self.assertEqual([b["label"] for b in buttons.values()], ["Wort", "Satz", "Anderes"])
                self.tap(page, 0, "sentence")
                self.open_menu(page, 1)
                self.tap(page, 1, "other")
                badges = page.locator(".card-row .card-kind-badge").all_inner_texts()
                self.assertEqual([text.strip() for text in badges], ["Satz", "Anderes"])
                page.locator("#saveButton").click()
                page.wait_for_url(f"**/flashcards/{set_id}", timeout=8000)
                saved = self.kinds(page, set_id)
                self.assertEqual(saved["Il pleut, je reste à la maison."], ("sentence", ["sentence"]))
                self.assertEqual(saved["E = mc²"], ("other", ["other"]))
                self.assertEqual(saved["la maison"], ("", []))
                # Reload the editor: the same Type is selected.
                page.goto(f"{self.base}/flashcards/{set_id}/edit", wait_until="load")
                page.wait_for_selector(".card-row .card-menu-trigger", timeout=8000)
                self.audit.settle_page(page)
                pressed = {k: b["pressed"] for k, b in self.open_menu(page, 0).items()}
                self.assertEqual(pressed, {"word": "false", "sentence": "true", "other": "false"})
                page.locator("#setTitle").click()   # closes the menu like any outside tap
                page.wait_for_selector(".card-menu[open]", state="detached", timeout=4000)
                pressed = {k: b["pressed"] for k, b in self.open_menu(page, 1).items()}
                self.assertEqual(pressed["other"], "true")
                self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

"""Rendered-layout tests: real Chrome/Edge via Playwright at phone, tablet and desktop sizes.

They render the actual templates and stylesheets (scripts/mobile_audit.py does the
measuring) and fail on what a student would see: a page that scrolls sideways, a control
pushed off-screen, a menu button that is missing or does not open, duplicated step
numbers, a caret left in a formula. Skipped when Playwright or a Chromium-based browser
is not installed, so the rest of the suite never depends on one.
"""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_mobile_layout_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

ROOT = Path(__file__).resolve().parents[1]

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None


def _audit_module():
    spec = importlib.util.spec_from_file_location("mobile_audit", ROOT / "scripts" / "mobile_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["mobile_audit"] = module
    spec.loader.exec_module(module)
    return module


@unittest.skipIf(sync_playwright is None, "Playwright is not installed")
class MobileLayoutTests(unittest.TestCase):
    PORT = 5079
    WIDTHS = ((320, 568), (390, 844), (768, 1024), (1440, 900))

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
        cls.urls = cls.audit.login_and_create_lesson(context, cls.base)
        cls.lesson_url = cls.urls["LESSON"]
        cls.student_state = context.storage_state()
        context.close()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()

    def context(self, width, height, *, logged_in, theme="light"):
        mobile = width <= 430
        context = self.browser.new_context(
            viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile,
            storage_state=self.student_state if logged_in else None)
        context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        return context

    def check(self, path, *, logged_in, theme="light", open_chat=False):
        for width, height in self.WIDTHS:
            with self.subTest(page=path, width=width, theme=theme):
                context = self.context(width, height, logged_in=logged_in, theme=theme)
                result = self.audit.audit_page(context, self.base + path, mobile_menu=width < 1024,
                                               open_chat=open_chat, screenshot=None)
                self.assertEqual(self.audit.blocking(result, width, logged_in=logged_in), [], result.get("menu"))

    def test_landing_page_and_guest_menu(self):
        self.check("/about", logged_in=False)

    def test_logged_in_header_and_menu_light_and_dark(self):
        self.check("/dashboard", logged_in=True)
        self.check("/dashboard", logged_in=True, theme="dark")

    def test_lesson_page_with_tutor_chat(self):
        self.check(self.lesson_url, logged_in=True, open_chat=True)

    def test_project_topic_review_and_workspace(self):
        # The two Project Mode screens: confirming topics, then learning with one Next button.
        self.check(self.urls["PROJECT_REVIEW"], logged_in=True)
        self.check(self.urls["PROJECT"], logged_in=True)
        self.check(self.urls["PROJECT"], logged_in=True, theme="dark")

    def test_project_next_button_is_large_and_on_screen_on_a_small_phone(self):
        page = self.context(320, 568, logged_in=True).new_page()
        page.goto(self.base + self.urls["PROJECT"], wait_until="load")
        button = page.locator(".pm-next .pm-primary")
        box = button.bounding_box()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box["height"], 48)
        self.assertGreaterEqual(box["x"], 0)
        self.assertLessEqual(box["x"] + box["width"], 320)

    def test_settings_form(self):
        self.check("/settings", logged_in=True)

    def test_lesson_steps_are_numbered_once_and_powers_are_raised(self):
        page = self.context(390, 844, logged_in=True).new_page()
        page.goto(self.base + self.lesson_url, wait_until="load")
        page.wait_for_timeout(600)
        steps = page.eval_on_selector_all("#exampleSteps li", "items => items.map(item => item.textContent.trim())")
        self.assertEqual(len(steps), 4)
        for step in steps:
            self.assertNotRegex(step, r"^(?:Schritt\s*)?\d{1,2}\s*[.):]\s", "the <ol> already numbers steps")
        # "a^m·a^n = a^{m+n}" and "4^2·4^3" were written without $...$: raised, no caret left.
        visible = page.evaluate("""() => {
            const clone = document.querySelector('#exampleSteps').cloneNode(true);
            clone.querySelectorAll('.katex, [data-ln-math]').forEach(node => node.remove());
            return clone.textContent; }""")
        self.assertNotIn("^", visible)
        self.assertGreaterEqual(page.locator("#exampleSteps sup").count(), 4)

    def test_tutor_reply_keeps_its_markdown_when_read_aloud_is_attached(self):
        page = self.context(390, 844, logged_in=True).new_page()
        page.goto(self.base + self.lesson_url, wait_until="load")
        page.click("#chatToggle")
        page.fill("#chatInput", "Warum?")
        page.click("#chatForm [type=submit]")
        page.wait_for_selector(".tutor-message table", timeout=6000)
        self.assertGreaterEqual(page.locator(".tutor-message table td").count(), 4)
        self.assertEqual(page.locator(".tutor-message strong").first.inner_text(), "Gut gemacht!")
        # The listen control is still attached and the panel's close button is reachable.
        self.assertGreaterEqual(page.locator(".tutor-message .listen-control").count(), 1)
        self.assertTrue(page.locator("#chatClose").is_visible())
        page.click("#chatClose")

    def test_read_aloud_speaks_formulas_from_their_source(self):
        context = self.context(390, 844, logged_in=True)
        # A recording stand-in for the speech engine, installed before the page's scripts run.
        context.add_init_script("""
            window.__spoken = [];
            window.SpeechSynthesisUtterance = function (text) { this.text = text; };
            Object.defineProperty(window, 'speechSynthesis', { configurable: true, value: {
              speak(utterance) { window.__spoken.push(utterance.text); utterance.onend && utterance.onend(); },
              cancel() {}, pause() {}, resume() {}, getVoices() { return []; },
              speaking: false, paused: false, addEventListener() {} } });
        """)
        page = context.new_page()
        page.goto(self.base + self.lesson_url, wait_until="load")
        page.wait_for_function("document.querySelector('#explanation .katex') !== null", timeout=8000)
        play = page.locator("[data-speech-target='#explanation'] [data-speech-play], #explanation ~ * [data-speech-play]").first
        play.click()
        page.wait_for_function("window.__spoken.length > 0", timeout=4000)
        spoken = " ".join(page.evaluate("window.__spoken"))
        self.assertRegex(" ".join(spoken.split()),
                         r"a (hoch|to the power of) m · a (hoch|to the power of) n = a (hoch|to the power of) m\+n")
        self.assertNotIn("m+nam", spoken.replace(" ", ""), "the KaTeX markup and its MathML copy were read too")

    def test_math_rule_helpers_in_the_browser(self):
        page = self.context(390, 844, logged_in=False).new_page()
        page.goto(self.base + "/about", wait_until="load")
        result = page.evaluate("""async () => {
            const rules = await import('/static/js/math-rules.js');
            return {
              powers: rules.powerSegments('4^2·4^3 = a^{m+n}'),
              caretInProse: rules.hasBarePower('see ^ above'),
              readable: rules.readableTex('\\\\frac{1}{2} \\\\cdot 2^{3}'),
            }; }""")
        self.assertEqual([seg["value"] for seg in result["powers"] if seg["type"] == "sup"], ["2", "3", "m+n"])
        self.assertFalse(result["caretInProse"])
        self.assertEqual(result["readable"], "(1)/(2) · 2^{3}")


if __name__ == "__main__":
    unittest.main()

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


# Contrast checks: the text colour as the browser paints it (a canvas resolves color-mix()),
# then the element photographed with its text hidden, so every background pixel counts.
TEXT_COLOUR_JS = """(selector) => {
  const el = document.querySelector(selector);
  if (!el) return null;
  if (document.activeElement) document.activeElement.blur();
  el.scrollIntoView({block: 'center'});
  const paint = document.createElement('canvas').getContext('2d', {willReadFrequently: true});
  paint.fillStyle = getComputedStyle(el).color;
  paint.fillRect(0, 0, 1, 1);
  const [r, g, b] = paint.getImageData(0, 0, 1, 1).data;
  for (const node of [el, ...el.querySelectorAll('*')]) {
    for (const property of ['color', '-webkit-text-fill-color', 'text-decoration-color', 'text-shadow']) {
      node.style.setProperty(property, property === 'text-shadow' ? 'none' : 'transparent', 'important');
    }
  }
  return [r, g, b];
}"""
SHOW_TEXT_JS = """(selector) => {
  const el = document.querySelector(selector);
  for (const node of [el, ...el.querySelectorAll('*')]) {
    for (const property of ['color', '-webkit-text-fill-color', 'text-decoration-color', 'text-shadow']) {
      node.style.removeProperty(property);
    }
  }
}"""


def contrast_ratio(first, second):
    def luminance(rgb):
        channels = [value / 255 for value in rgb[:3]]
        linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


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

    def check(self, path, *, logged_in, theme="light", open_chat=False, widths=None):
        for width, height in widths or self.WIDTHS:
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

    def test_tutorial_hub_light_and_dark(self):
        # The hub's section cards and their buttons (docs/TUTORIAL.md), one column on phones.
        self.check("/tutorial", logged_in=True)
        self.check("/tutorial", logged_in=True, theme="dark")

    SMALL_PHONES = ((320, 568), (375, 667))

    def test_about_and_contact_small_phones_light_and_dark(self):
        for path in ("/about", "/contact"):
            for logged_in in (False, True):
                for theme in ("light", "dark"):
                    self.check(path, logged_in=logged_in, theme=theme, widths=self.SMALL_PHONES)

    def test_contact_page_guest_light_and_dark(self):
        self.check("/contact", logged_in=False)
        self.check("/contact", logged_in=False, theme="dark")

    def test_contact_page_logged_in(self):
        self.check("/contact", logged_in=True)

    def test_about_logged_in_dark(self):
        self.check("/about", logged_in=True, theme="dark")

    def test_contact_form_fields_do_not_zoom_on_iphone(self):
        for width, height in ((320, 568), (375, 667), (390, 844)):
            with self.subTest(width=width):
                page = self.context(width, height, logged_in=False).new_page()
                page.goto(self.base + "/contact", wait_until="load")
                sizes = page.evaluate("""() => [...document.querySelectorAll(
                    '.contact-form input:not([type=hidden]):not(#contact-website), .contact-form select, .contact-form textarea')]
                    .map((el) => parseFloat(getComputedStyle(el).fontSize))""")
                self.assertEqual(len(sizes), 4)
                for size in sizes:
                    self.assertGreaterEqual(size, 16)
                box = page.locator(".contact-submit").bounding_box()
                self.assertIsNotNone(box)
                self.assertGreaterEqual(box["height"], 44)
                self.assertGreaterEqual(box["x"], 0)
                self.assertLessEqual(box["x"] + box["width"], width)
                link = page.locator(".contact-email-link").bounding_box()
                self.assertIsNotNone(link)
                self.assertGreaterEqual(link["height"], 44)
                self.assertLessEqual(link["x"] + link["width"], width)
                self.assertFalse(page.evaluate(
                    "document.documentElement.scrollWidth > document.documentElement.clientWidth"))
                self.assertFalse(page.locator("#contact-website").is_visible())

    # ------------------------------------------------ contact form states (review fixes)
    def contact_config(self):
        """Let the browser submit at once (no minimum fill time) and forward inline."""
        config = self.application.app.config
        saved = {key: config.get(key) for key in ("CONTACT_MIN_FILL_SECONDS", "COMMUNITY_BACKGROUND_JOBS")}
        config.update(CONTACT_MIN_FILL_SECONDS=0, COMMUNITY_BACKGROUND_JOBS=False)
        self.addCleanup(config.update, saved)

    def submit_contact(self, page, *, email, message="The quiz button does nothing on my phone."):
        page.goto(self.base + "/contact", wait_until="load")
        page.fill("#contact-name", "Mia")
        page.fill("#contact-email", email)
        page.select_option("#contact-category", "bug")
        page.fill("#contact-message", message)
        with page.expect_navigation(wait_until="load"):
            page.click(".contact-submit")
        page.wait_for_timeout(300)

    def contrast(self, page, selector):
        """Lowest contrast between the text colour and any pixel painted behind the text."""
        import io

        from PIL import Image
        colour = page.evaluate(TEXT_COLOUR_JS, selector)
        self.assertIsNotNone(colour, selector)
        try:
            shot = page.locator(selector).first.screenshot(animations="disabled")
        finally:
            page.evaluate(SHOW_TEXT_JS, selector)
        background = Image.open(io.BytesIO(shot)).convert("RGB").getcolors(maxcolors=1 << 24)
        return round(min(contrast_ratio(colour, pixel) for _count, pixel in background), 2)

    def test_contact_fields_are_comfortable_to_tap_and_type_in(self):
        # Review design-input-padding: the fields were 25px tall with 2px padding above 820px.
        for width, height in ((390, 844), (1024, 768), (1440, 900)):
            with self.subTest(width=width):
                page = self.context(width, height, logged_in=False).new_page()
                page.goto(self.base + "/contact", wait_until="load")
                fields = page.evaluate("""() => ['#contact-name', '#contact-email', '#contact-category',
                    '#contact-message'].map((selector) => {
                      const el = document.querySelector(selector), style = getComputedStyle(el);
                      return {selector, height: el.getBoundingClientRect().height,
                              left: parseFloat(style.paddingLeft), right: parseFloat(style.paddingRight)}; })""")
                for field in fields:
                    self.assertGreaterEqual(field["height"], 44, field)
                    self.assertGreaterEqual(field["left"], 10, field)
                    self.assertGreaterEqual(field["right"], 10, field)

    def test_error_summary_links_keep_label_and_error_in_view(self):
        # Review design-error-link-hidden-under-header.
        self.contact_config()
        for width, height in ((320, 568), (375, 667), (1280, 800)):
            with self.subTest(width=width):
                page = self.context(width, height, logged_in=False).new_page()
                self.submit_contact(page, email="mia@school")  # the browser accepts it; the server does not
                self.assertTrue(page.locator(".contact-error-summary").is_visible())
                page.click(".contact-error-summary a[href='#contact-email']")
                # Scrolling is smooth (html scroll-behavior): wait until the page stops moving.
                page.evaluate("""() => new Promise((done) => {
                    let last = -1, still = 0, frames = 0;
                    const tick = () => {
                      still = window.scrollY === last ? still + 1 : 0;
                      last = window.scrollY;
                      if (still >= 5 || ++frames > 180) done(); else requestAnimationFrame(tick);
                    };
                    requestAnimationFrame(tick); })""")
                where = page.evaluate("""() => {
                    const box = (selector) => document.querySelector(selector).getBoundingClientRect();
                    return {header: box('.app-nav').bottom, label: box("label[for='contact-email']").top,
                            error: box('#contact-email-error').top, input: box('#contact-email').bottom,
                            viewport: window.innerHeight, active: document.activeElement.id}; }""")
                self.assertEqual(where["active"], "contact-email", where)
                self.assertGreaterEqual(where["label"], where["header"], where)
                self.assertGreaterEqual(where["error"], where["header"], where)
                self.assertLessEqual(where["input"], where["viewport"], where)

    def test_contact_error_and_success_states_light_and_dark(self):
        # Review tests-09 and design-error-text-contrast: the states students see after sending.
        self.contact_config()
        for theme in ("light", "dark"):
            for width, height in ((320, 568), (390, 844)):
                with self.subTest(theme=theme, width=width):
                    page = self.context(width, height, logged_in=False, theme=theme).new_page()
                    errors = []
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    self.submit_contact(page, email="mia@school")
                    self.assertTrue(page.locator(".contact-error-summary").is_visible())
                    result = page.evaluate(self.audit.AUDIT_JS)
                    self.assertEqual(self.audit.blocking(result, width, logged_in=False), [])
                    for selector in (".contact-error", ".contact-error-summary a", ".contact-error-summary p"):
                        self.assertGreaterEqual(self.contrast(page, selector), 4.5, selector)
                    self.submit_contact(page, email=f"mia.{theme}.{width}@example.org")
                    self.assertTrue(page.url.endswith("/contact/sent"), page.url)
                    self.assertTrue(page.locator(".contact-success").is_visible())
                    result = page.evaluate(self.audit.AUDIT_JS)
                    self.assertEqual(self.audit.blocking(result, width, logged_in=False), [])
                    for selector in (".contact-success h2", ".contact-success p"):
                        self.assertGreaterEqual(self.contrast(page, selector), 4.5, selector)
                    self.assertEqual(errors, [])

    def test_violet_text_is_readable_on_phones(self):
        # Review design-violet-small-text-contrast: 17px bold is not "large text".
        for theme in ("light", "dark"):
            for width, height in ((320, 568), (375, 667)):
                with self.subTest(theme=theme, width=width):
                    page = self.context(width, height, logged_in=False, theme=theme).new_page()
                    page.goto(self.base + "/contact", wait_until="load")
                    self.assertGreaterEqual(self.contrast(page, ".contact-email-link"), 4.5)
                    page.goto(self.base + "/about", wait_until="load")
                    for selector in (".about-hero-tagline", ".about-secondary-link", ".about-fine-print a",
                                     ".about-contact-line a"):
                        self.assertGreaterEqual(self.contrast(page, selector), 4.5, selector)

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

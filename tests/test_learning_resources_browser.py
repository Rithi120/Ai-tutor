"""Learning resources in a real browser: static/js/resources.js and the markdown.js link rule.

Node is not installed, so these run through the Playwright harness (tests/js_browser/harness.py)
like tests/test_js_browser.py. Skipped when Playwright or a Chromium-based browser is missing.

Pinned here: a model-written YouTube link is never clickable on any chat surface (assistant
thread, lesson tutor chat, lesson history); video cards and credited pictures are built from
text only, and a link or picture of an unexpected shape is not shown at all.
"""

import io
import json
import os
import socket
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_js_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from learnova.translations.catalog import frontend_catalog  # noqa: E402
from tests.js_browser import harness  # noqa: E402

BROWSER = {}
MODULE = "/static/js/resources.js"


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


class _Case(unittest.TestCase):
    def page(self):
        page = harness.open_page(BROWSER["browser"])
        self.addCleanup(page.context.close)
        return page


class MarkdownVideoLinkTests(_Case):
    def test_a_model_written_youtube_link_is_text_on_every_surface(self):
        result = harness.render_markdown(self.page(), (
            "Video: [Ohm erklärt](https://www.youtube.com/watch?v=abcdefghijk), "
            "https://youtu.be/abcdefghijk und https://m.youtube.com/watch?v=abcdefghijk.\n"
            "Mehr: [Ohmsches Gesetz](https://de.wikipedia.org/wiki/Ohmsches_Gesetz)"))
        for surface in ("assistant", "tutor", "history"):
            with self.subTest(surface=surface):
                hrefs = [anchor["href"] for anchor in result[surface]["anchors"]]
                self.assertEqual(hrefs, ["https://de.wikipedia.org/wiki/Ohmsches_Gesetz"])


CARD = {
    "video_id": "abc_def-123", "url": "https://www.youtube.com/watch?v=abc_def-123",
    "thumbnail": "/media/yt-thumb/abc_def-123", "title": "Ohmsches Gesetz <img src=x onerror=window.__pwned=1>",
    "channel": "Physik & Co", "duration_seconds": 390, "supplementary": True,
}
IMAGE = {
    "url": "https://upload.wikimedia.org/wikipedia/commons/a/ab/Ohm.png", "alt": "Schaltplan mit Widerstand",
    "title": "Ohmsches Gesetz", "author": "Ann <b>Example</b>", "licence": "CC BY-SA 4.0",
    "licence_url": "https://creativecommons.org/licenses/by-sa/4.0",
    "file_url": "https://commons.wikimedia.org/wiki/File:Ohm.png", "source": "Wikimedia Commons",
}


class ResourceNodeTests(_Case):
    def evaluate(self, expression, argument):
        """Run `expression`, a JS function (mod, argument), against static/js/resources.js,
        with the German interface catalogue loaded as the app loads it."""
        page = self.page()
        page.evaluate("(messages) => { window.LEARNOVA_I18N = messages; }", frontend_catalog("de"))
        return harness.eval_module(page, MODULE, f"(mod) => ({expression})(mod, {json.dumps(argument)})")

    def test_a_video_card_links_out_and_shows_text_only(self):
        view = self.evaluate("""(mod, card) => {
            const node = mod.videoCardNode(card);
            return { tag: node.tagName, href: node.getAttribute("href"), target: node.target, rel: node.rel,
                     img: node.querySelector("img").getAttribute("src"), referrer: node.querySelector("img").referrerPolicy,
                     title: node.querySelector(".resource-video-title").textContent,
                     meta: node.querySelector(".resource-video-meta").textContent,
                     tags: [...node.querySelectorAll("*")].map((el) => el.tagName.toLowerCase()),
                     iframes: node.querySelectorAll("iframe").length, pwned: window.__pwned === 1 };
        }""", CARD)
        self.assertEqual((view["tag"], view["href"], view["target"]), ("A", CARD["url"], "_blank"))
        self.assertIn("noopener", view["rel"])
        self.assertEqual((view["img"], view["referrer"]), ("/media/yt-thumb/abc_def-123", "no-referrer"))
        self.assertEqual(view["title"], CARD["title"])            # shown as text, exactly as returned
        self.assertEqual(view["meta"], "Physik & Co · 6:30")
        self.assertEqual(view["tags"].count("img"), 1)            # the title's "<img" never became markup
        self.assertEqual(view["iframes"], 0)
        self.assertFalse(view["pwned"])

    def test_cards_of_an_unexpected_shape_are_not_shown(self):
        for bad in ({"url": "https://evil.example/watch?v=abcdefghijk"},
                    {"url": "javascript:alert(1)"},
                    {"thumbnail": "https://i.ytimg.com/vi/abc_def-123/mqdefault.jpg"},
                    {"title": "  "}):
            with self.subTest(bad=bad):
                self.assertIsNone(self.evaluate("(mod, card) => mod.videoCardNode(card)", {**CARD, **bad}))

    def test_a_picture_carries_its_credit_and_an_uncredited_one_is_not_shown(self):
        view = self.evaluate("""(mod, image) => {
            const node = mod.imageFigureNode(image);
            return { img: node.querySelector("img").getAttribute("src"), alt: node.querySelector("img").alt,
                     caption: node.querySelector("figcaption").textContent,
                     links: [...node.querySelectorAll("a")].map((a) => [a.getAttribute("href"), a.textContent]),
                     bold: node.querySelectorAll("b").length };
        }""", IMAGE)
        self.assertEqual(view["img"], IMAGE["url"])
        self.assertEqual(view["alt"], IMAGE["alt"])
        self.assertIn("Bild: Ann <b>Example</b>, CC BY-SA 4.0 (Wikimedia Commons)", view["caption"])
        self.assertEqual([href for href, _text in view["links"]], [IMAGE["file_url"], IMAGE["licence_url"]])
        self.assertEqual(view["bold"], 0)
        for missing in ({"licence": ""}, {"file_url": "https://evil.example/x"}, {"url": "https://evil.example/x.png"}):
            with self.subTest(missing=missing):
                self.assertIsNone(self.evaluate("(mod, image) => mod.imageFigureNode(image)", {**IMAGE, **missing}))

    def test_durations(self):
        self.assertEqual(self.evaluate("(mod, values) => values.map(mod.formatDuration)", [65, 3723, 0, None]),
                         ["1:05", "1:02:03", "", ""])


def tiny_image(fmt):
    from PIL import Image  # noqa: PLC0415

    buffer = io.BytesIO()
    Image.new("RGB", (32, 18), (40, 90, 160)).save(buffer, format=fmt)
    return buffer.getvalue()


class LivePageTests(unittest.TestCase):
    """The real chat and lesson pages against the real app: AI, Wikipedia, YouTube and the
    thumbnail host stubbed in-process (scripts/mobile_audit.start_server)."""

    @classmethod
    def setUpClass(cls):
        if not BROWSER:
            raise unittest.SkipTest("no browser")
        from learnova import media_enrichment as media  # noqa: PLC0415
        from learnova import youtube_videos  # noqa: PLC0415
        from tests.test_learning_resources import LESSON, OHM_REPLY, ohm_videos, wikipedia  # noqa: PLC0415
        from tests.test_youtube_videos import KEY  # noqa: PLC0415

        cls.audit = harness._audit_module()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.application, cls.server = cls.audit.start_server(port)
        cls.base = f"http://127.0.0.1:{port}"
        cls.saved = {key: cls.application.app.config.get(key) for key in (
            "YOUTUBE_API_KEY", "FEATURE_LEARNING_RESOURCES", "FEATURE_ASSISTANT_CHAT", "FEATURE_LESSON_MEDIA")}
        cls.application.app.config.update(YOUTUBE_API_KEY=KEY, FEATURE_LEARNING_RESOURCES=True,
                                          FEATURE_ASSISTANT_CHAT=True, FEATURE_LESSON_MEDIA=True)
        cls.application._YOUTUBE_THUMBNAILS.clear()
        media._fetch_page.cache_clear()
        media._fetch_file_info.cache_clear()
        cls.lesson, cls.reply = LESSON, OHM_REPLY
        cls.application.create_response.side_effect = cls.fake
        cls.patches = [wikipedia(), patch.object(youtube_videos, "_http_json", ohm_videos()),
                       patch.object(youtube_videos, "_http_bytes",
                                    MagicMock(return_value=(200, tiny_image("JPEG"), "image/jpeg")))]
        for item in cls.patches:
            item.start()

    @classmethod
    def tearDownClass(cls):
        for item in cls.patches:
            item.stop()
        cls.server.shutdown()
        cls.application.app.config.update(cls.saved)

    @classmethod
    def fake(cls, **kwargs):
        task = kwargs.get("task_type")
        text_out = cls.reply if task == "assistant_chat" else (
            json.dumps(cls.lesson) if task == "lesson_generation" else None)
        if text_out is None:
            return cls.audit._fake_response(**kwargs)

        class Usage:
            input_tokens = output_tokens = total_tokens = 10

        class Reply:
            usage, model, output_text = Usage(), "test-model", text_out
        return Reply()

    def student(self, *, theme="light", width=390, height=844):
        context = BROWSER["browser"].new_context(viewport={"width": width, "height": height},
                                                 is_mobile=width <= 430, has_touch=width <= 430)
        context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
        context.add_init_script(harness.KATEX_STUB)
        self.audit.block_external(context, self.base)
        # Registered after the blocker, so it wins: the stubbed Wikimedia picture "loads".
        png = tiny_image("PNG")
        context.route("https://upload.wikimedia.org/**",
                      lambda route: route.fulfill(status=200, body=png, headers={"Content-Type": "image/png"}))
        self.addCleanup(context.close)
        page = context.new_page()
        self.errors, self.outside = [], []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("request", lambda request: self.outside.append(request.url)
                if any(host in request.url for host in ("youtube.com", "ytimg.com", "googleapis.com/youtube"))
                else None)
        name = f"r{uuid.uuid4().hex[:10]}"
        response = page.request.post(f"{self.base}/register", form={
            "username": name, "email": f"{name}@example.com", "password": "correct-horse-battery", "grade": "8"})
        self.assertLess(response.status, 400)
        page.request.post(f"{self.base}/api/tour/complete")
        page.request.post(f"{self.base}/tutorial/tips", form={"back": "hub"})
        return page

    def test_the_chat_shows_a_credited_diagram_and_videos_after_one_tap(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                page = self.student(theme=theme)
                status = page.request.post(f"{self.base}/api/assistant/conversations",
                                           data="{}", headers={"Content-Type": "application/json"})
                cid = status.json()["conversation"]["id"]
                sent = page.request.post(f"{self.base}/api/assistant/conversations/{cid}/messages",
                                         data=json.dumps({"message": "Ohmsches Gesetz?"}),
                                         headers={"Content-Type": "application/json"})
                self.assertEqual(sent.status, 201)
                page.goto(f"{self.base}/assistant/c/{cid}", wait_until="load")
                page.wait_for_function("() => window.__asstReady === true")
                figure = page.wait_for_selector(".asst-resources figure.asst-resource-image")
                self.assertIn("Ann Example", figure.inner_text())
                self.assertIn("CC BY-SA 4.0", figure.inner_text())
                self.assertEqual(page.locator(".resource-video").count(), 0, "videos wait for the tap")
                page.click(".asst-resources-button")
                card = page.wait_for_selector(".asst-resources .resource-video")
                self.assertEqual(card.get_attribute("href"), "https://www.youtube.com/watch?v=aaaaaaaaaaa")
                self.assertIn("Ohmsches Gesetz – Versuch mit Widerständen", card.inner_text())
                page.wait_for_function(
                    "() => document.querySelector('.resource-video-thumb')?.naturalWidth > 0")
                colours = page.evaluate("""() => {
                    const title = document.querySelector('.resource-video-title');
                    const box = document.querySelector('.resource-video');
                    return [getComputedStyle(title).color, getComputedStyle(box).backgroundColor];
                }""")
                self.assertNotEqual(colours[0], colours[1])
                overflow = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 0)
                self.assertEqual(self.outside, [], "the browser itself never contacted YouTube or Google")
                self.assertEqual(self.errors, [])

    def test_a_lesson_labels_searches_honestly_and_offers_checked_videos(self):
        page = self.student(theme="dark")
        response = page.request.post(f"{self.base}/api/analyze", multipart={
            "study_goal": "Ohmsches Gesetz verstehen", "subject": "Physics"})
        self.assertEqual(response.status, 200, response.text())
        session_id = response.json()["session_id"]
        page.goto(f"{self.base}/lesson?session_id={session_id}", wait_until="load")
        row = page.wait_for_selector("#lessonVideos .media-video-search")
        self.assertIn("Search YouTube for “Ohmsches Gesetz einfach erklärt”", row.inner_text())
        colours = page.evaluate("""() => {
            const row = document.querySelector('.media-video');
            return [getComputedStyle(row).color, getComputedStyle(row).backgroundColor];
        }""")
        self.assertNotEqual(colours[1], "rgb(248, 246, 255)", "the light row background is overridden in dark")
        self.assertNotEqual(colours[0], colours[1])
        self.assertIn("Supplementary", page.inner_text("#mediaKicker"))
        page.click(".media-video-button")
        card = page.wait_for_selector("#lessonVideos .resource-video")
        self.assertEqual(card.get_attribute("href"), "https://www.youtube.com/watch?v=aaaaaaaaaaa")
        self.assertEqual(self.outside, [])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

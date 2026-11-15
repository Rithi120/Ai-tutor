"""Forty images in a real browser (spec Phase 3, "Test 4"): Project Mode and Vocabulary Mode.

The real templates, image-collection.js, project-create.js, scanner.js and vocabulary.js
against the real Flask app (scripts/mobile_audit.start_server, AI patched; the vocabulary
page reader patched to read a page's number from its colour, so the local OCR engine is
not needed). For each mode: forty generated photos are picked (in two picks, which must
add up), all forty thumbnails appear while a main-thread probe and the Long Tasks API
show the page stays responsive, pages are reordered, one is deleted, the full-screen
preview steps and closes, and the collection is saved - in batches - with exactly the
order shown on the screen.

Skipped when Playwright or a Chromium-based browser is missing (Store Python has no
Playwright: run with R:/ai_tutor/.venv/Scripts/python).
"""

import io
import os
import socket
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_multi_image_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from PIL import Image, ImageDraw

from tests.js_browser import harness  # noqa: E402

BROWSER = {}
COUNT = 40
# Responsiveness budget while forty thumbnails are made: the longest gap between two
# probe ticks (a 16 ms interval) and the longest single task the Long Tasks API reports.
MAX_PROBE_GAP_MS = 500
MAX_LONG_TASK_MS = 400

PROBE = """
window.__longTasks = [];
try {
  new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) window.__longTasks.push(Math.round(entry.duration));
  }).observe({ type: "longtask", buffered: true });
} catch (_) {}
window.__probe = { last: performance.now(), maxGap: 0, ticks: 0, on: false };
setInterval(() => {
  const now = performance.now();
  if (window.__probe.on) {
    window.__probe.maxGap = Math.max(window.__probe.maxGap, now - window.__probe.last);
    window.__probe.ticks += 1;
  }
  window.__probe.last = now;
}, 16);
"""


def page_image(number: int) -> bytes:
    """A 1600x1200 noisy 'photo' (about 1 MB, so decoding costs something) whose top-left
    block's red channel is number * 5, the page's identity for the patched reader."""

    image = Image.effect_noise((1600, 1200), 60).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 96, 96), fill=(number * 5, 200, 200))
    draw.text((140, 40), f"Page {number}", fill=(0, 0, 0))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=92)
    return buffer.getvalue()


def number_of(data: bytes) -> int:
    pixel = Image.open(io.BytesIO(data)).convert("RGB").getpixel((40, 40))
    return round(pixel[0] / 5)


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


class FortyImagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BROWSER:
            raise unittest.SkipTest("no browser")
        cls.audit = harness._audit_module()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.application, cls.server = cls.audit.start_server(port)
        cls.base = f"http://127.0.0.1:{port}"
        config = cls.application.app.config
        keys = ("AI_MODE", "FEATURE_VOCABULARY_TRAINER", "FEATURE_PRIVATE_FLASHCARDS",
                "FEATURE_FLASHCARD_IMAGE_IMPORT", "FEATURE_FLASHCARD_PDF_IMPORT", "PROJECT_MAX_PAGES",
                "VOCABULARY_MAX_PAGES")
        cls.saved = {key: config.get(key) for key in keys}
        config.update(AI_MODE="cached", FEATURE_VOCABULARY_TRAINER=True, FEATURE_PRIVATE_FLASHCARDS=True,
                      FEATURE_FLASHCARD_IMAGE_IMPORT=True, FEATURE_FLASHCARD_PDF_IMPORT=True,
                      PROJECT_MAX_PAGES=40, VOCABULARY_MAX_PAGES=40)
        cls.reader = patch.object(cls.application, "read_vocabulary_page_safely", side_effect=cls.read_page)
        cls.reader.start()
        cls.images = [page_image(number) for number in range(1, COUNT + 1)]
        # Real files on disk: a picker hands the browser files natively. (Playwright's
        # in-memory buffers are rebuilt by a script inside the page, a multi-second main-
        # thread task of the harness's own that the probe would blame on the page.)
        cls.folder = Path(tempfile.mkdtemp(prefix="learnova-forty-"))
        for number, data in enumerate(cls.images, start=1):
            (cls.folder / f"page-{number:02d}.jpg").write_bytes(data)

    @classmethod
    def tearDownClass(cls):
        cls.reader.stop()
        cls.application.app.config.update(cls.saved)
        cls.server.shutdown()
        for path in cls.folder.glob("*.jpg"):
            path.unlink()
        cls.folder.rmdir()

    @classmethod
    def read_page(cls, data, page_number, _source, _target):
        number = number_of(data)
        return cls.application.vocabulary.parse_vocabulary_text(f"mot{number} | Wort{number}", page_number)

    # ------------------------------------------------------------------ helpers
    def student(self, width, height):
        context = BROWSER["browser"].new_context(viewport={"width": width, "height": height},
                                                 is_mobile=width <= 430, has_touch=width <= 430)
        context.add_init_script(harness.KATEX_STUB)
        context.add_init_script(PROBE)
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        self.name = f"s{uuid.uuid4().hex[:10]}"
        response = page.request.post(f"{self.base}/register", form={
            "username": self.name, "email": f"{self.name}@example.com", "password": "correct-horse-battery",
            "grade": "10"})
        self.assertLess(response.status, 400)
        page.request.post(f"{self.base}/api/tour/complete")
        page.request.post(f"{self.base}/tutorial/tips", form={"back": "hub"})
        return page

    def files(self, numbers):
        return [str(self.folder / f"page-{number:02d}.jpg") for number in numbers]

    def add_forty(self, page, selector, root):
        """Two picks (25 + 15), then wait for all forty thumbnails, measuring responsiveness."""

        page.evaluate("() => { window.__probe.on = true; window.__probe.maxGap = 0; window.__longTasks = []; }")
        started = time.monotonic()
        page.set_input_files(selector, files=self.files(range(1, 26)))
        page.set_input_files(selector, files=self.files(range(26, COUNT + 1)))
        page.wait_for_function(
            f"""() => [...document.querySelectorAll('{root} .imgcol-thumb img')]
                .filter((img) => !img.hidden && img.complete && img.naturalWidth > 0).length === {COUNT}""",
            timeout=90000)
        elapsed = time.monotonic() - started
        probe = page.evaluate("() => { window.__probe.on = false; return { gap: window.__probe.maxGap, "
                              "ticks: window.__probe.ticks, long: window.__longTasks }; }")
        self.assertEqual(page.evaluate(f"() => document.querySelectorAll('{root} li.imgcol-item').length"), COUNT)
        self.assertLess(probe["gap"], MAX_PROBE_GAP_MS, f"the page froze while thumbnailing: {probe} in {elapsed:.1f}s")
        self.assertLess(max(probe["long"] or [0]), MAX_LONG_TASK_MS, f"a long main-thread task: {probe}")
        sizes = page.evaluate(f"""() => [...document.querySelectorAll('{root} .imgcol-thumb img')]
            .map((img) => Math.max(img.naturalWidth, img.naturalHeight))""")
        self.assertTrue(all(0 < size <= 320 for size in sizes), sizes)
        self.timing = {"thumbnails_s": round(elapsed, 2), "max_gap_ms": round(probe["gap"]), "long_tasks": probe["long"]}

    def order_on_screen(self, page, collection):
        return page.evaluate(f"() => window.{collection}.items().map((item) => item.name)")

    def edit(self, page, collection, root):
        """Reorder (button, then drag on a desktop or buttons on a phone), delete one, preview."""

        names = [f"page-{number:02d}.jpg" for number in range(1, COUNT + 1)]
        page.click(f"{root} li:nth-child(1) .imgcol-later")
        names[0], names[1] = names[1], names[0]
        if page.viewport_size["width"] > 700:
            # On a desktop: drag the seventh page onto the first.
            page.drag_and_drop(f"{root} li:nth-child(7)", f"{root} li:nth-child(1)")
            names.insert(0, names.pop(6))
        else:
            # On a phone: the "earlier" button, 39 taps, the last page climbing to the front.
            for position in range(COUNT, 1, -1):
                page.click(f"{root} li:nth-child({position}) .imgcol-earlier")
            names.insert(0, names.pop())
        self.assertEqual(self.order_on_screen(page, collection), names)
        page.click(f"{root} li:nth-child(5) .imgcol-remove")
        del names[4]
        self.assertEqual(self.order_on_screen(page, collection), names)
        page.click(f"{root} li:nth-child(3) .imgcol-thumb")
        page.wait_for_selector("dialog.imgcol-viewer[open] img[src^='blob:']")
        first = page.get_attribute("dialog.imgcol-viewer img", "src")
        page.keyboard.press("ArrowRight")
        page.wait_for_function("(src) => document.querySelector('dialog.imgcol-viewer img').src !== src", arg=first)
        page.keyboard.press("Escape")
        page.wait_for_selector("dialog.imgcol-viewer", state="hidden")
        return names

    # ------------------------------------------------------------------ tests
    def test_forty_photos_in_project_mode_are_saved_in_the_order_shown(self):
        page = self.student(1280, 900)
        page.goto(f"{self.base}/projects")
        page.wait_for_selector("#projectImages .imgcol")
        self.assertIsNotNone(page.query_selector('label.document-input-option[data-tour-target="project-upload"] '
                                                '#imageUploadInput'), "the tutorial's anchor survives")
        self.add_forty(page, "#imageUploadInput", "#projectImages")
        expected = self.edit(page, "__projectImages", "#projectImages")
        page.fill("input[name=title]", "Biologie 40")
        page.fill("#projectImages li:nth-child(1) .imgcol-label", "Deckblatt")
        posts = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        page.click("#createProjectButton")
        page.wait_for_url(lambda url: "/start" in url, timeout=120000)
        uploads = [url for url in posts if url.endswith("/projects") or url.endswith("/pages/add")]
        self.assertGreaterEqual(len(uploads), 7, f"39 photos in batches of at most six: {uploads}")
        application, db = self.application, self.application.db
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == self.name))
            project = db.session.scalar(db.select(application.LearningProject).where(
                application.LearningProject.user_id == user.id))
            pages = sorted(project.pages, key=lambda item: item.page_order)
            stored = [page_row.source_file.original_filename for page_row in pages]
            first_label = pages[0].label
            stored_numbers = [number_of(page_row.source_file.original_data) for page_row in pages]
        self.assertEqual(stored, expected)
        self.assertEqual(stored_numbers, [int(name[5:7]) for name in expected], "the bytes match the names")
        self.assertEqual(first_label, "Deckblatt")
        self.assertEqual(self.errors, [])
        print(f"\nproject mode, 40 photos: {self.timing}")

    def test_forty_pages_in_vocabulary_mode_are_saved_and_read_in_the_order_shown(self):
        page = self.student(390, 844)
        page.goto(f"{self.base}/vocabulary/import")
        page.wait_for_selector("#vocabularyImages .imgcol")
        self.assertIsNone(page.get_attribute("#vocabularyFileField input[name=file]", "capture"),
                          "the gallery input offers the photo library, not only the camera")
        self.assertIsNotNone(page.get_attribute("#vocabularyFileField input[name=file]", "multiple"))
        self.assertEqual(page.get_attribute("#vocabularyCameraInput", "capture"), "environment")
        self.add_forty(page, "#vocabularyFileField input[name=file]", "#vocabularyImages")
        expected = self.edit(page, "__vocabularyPages", "#vocabularyImages")
        page.click("#vocabularyImportForm button[type=submit]")
        page.wait_for_url(lambda url: "/flashcards" in url, timeout=180000)
        application, db = self.application, self.application.db
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == self.name))
            item = db.session.scalar(db.select(application.VocabularyImport).where(
                application.VocabularyImport.owner_user_id == user.id))
            rows = db.session.scalars(db.select(application.VocabularyImportPage).where(
                application.VocabularyImportPage.import_id == item.id).order_by(
                application.VocabularyImportPage.position)).all()
            names = [db.session.get(application.FlashcardImport, row.flashcard_import_id).original_filename
                     for row in rows]
            entries = application.json_value(item.entries_json, [])
        self.assertEqual(names, expected)
        self.assertEqual([entry["source_term"] for entry in entries if entry.get("entry_kind") != "sentence"],
                         [f"mot{int(name[5:7])}" for name in expected], "the words keep the page order")
        self.assertEqual([entry["page_number"] for entry in entries], list(range(1, COUNT)))
        self.assertEqual(self.errors, [])
        print(f"\nvocabulary mode, 40 photos: {self.timing}")


if __name__ == "__main__":
    unittest.main()

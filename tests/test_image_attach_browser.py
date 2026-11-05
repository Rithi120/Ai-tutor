"""static/js/image-attach.js in a real browser: the photo limit, duplicates, restore, errors.

The attachment tray as it ships, on a page with the app's CSP (tests/js_browser/harness.py),
uploading to a fake /api/chat-attachments answered by Playwright. Covers what the server
tests cannot: the tray takes its limit from data-max-photos (the server's effective limit,
not a hard-coded 4), the same photo chosen twice stays one thumbnail and removing it never
deletes the attachment another thumbnail holds, photos still waiting on the server come back
after a refresh (restore), and an upload that gets HTML instead of JSON says why.

Skipped without Playwright or a Chromium-based browser (Store Python has no Playwright:
run with R:/ai_tutor/.venv/Scripts/python).
"""

import json
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from tests.js_browser import harness

BROWSER = {}
PNG_1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c63f8cfc0f01f0005000201a3d2b44f0000000049454e44ae426082")
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="csrf-token" content="test-token">
<script nonce="__NONCE__">window.LEARNOVA_I18N = {};</script></head>
<body>
<div id="tray" class="attach-tray hidden" data-max-photos="2"></div>
<input id="gallery" type="file" accept="image/*" multiple>
<script type="module" nonce="__NONCE__">
import { createAttachmentTray, uploadErrorText } from "/static/js/image-attach.js";
window.__errors = [];
window.__tray = createAttachmentTray({
  tray: document.querySelector("#tray"), inputs: [document.querySelector("#gallery")], purpose: "chat",
  onError: (message) => window.__errors.push(message),
});
window.__uploadErrorText = uploadErrorText;
window.__ready = true;
</script>
</body></html>
""".replace("__NONCE__", harness.NONCE)


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


class TrayTests(unittest.TestCase):
    def open(self, uploads):
        """The tray page; `uploads` are (status, body, content_type) answers to the POSTs, in order."""

        page = harness.open_page(BROWSER["browser"], PAGE)
        self.addCleanup(page.context.close)
        self.deleted = []
        self.posted = 0
        answers = list(uploads)

        def handle(route):
            request = route.request
            if request.method == "DELETE":
                self.deleted.append(request.url.rsplit("/", 1)[-1])
                route.fulfill(status=200, body=json.dumps({"ok": True, "deleted": True}),
                              headers={"Content-Type": "application/json"})
                return
            self.posted += 1
            status, body, content_type = answers.pop(0) if answers else (500, "{}", "application/json")
            route.fulfill(status=status, body=body if isinstance(body, str) else json.dumps(body),
                          headers={"Content-Type": content_type})

        page.route("**/api/chat-attachments**", handle)
        return page

    @staticmethod
    def attachment(identifier, duplicate=False):
        return (200 if duplicate else 201, {"ok": True, "duplicate": duplicate, "attachment": {
            "id": identifier, "url": f"/api/chat-attachments/{identifier}/image", "hints": [],
            "status": "pending", "available": True}}, "application/json")

    def choose(self, page, *names):
        page.set_input_files("#gallery", files=[
            {"name": name, "mimeType": "image/png", "buffer": PNG_1} for name in names])
        page.wait_for_function("() => !window.__tray.busy()", timeout=10000)

    def state(self, page):
        return page.evaluate("""() => ({
            count: window.__tray.count(), ids: window.__tray.ids(), max: window.__tray.max(),
            failed: window.__tray.failed(), errors: window.__errors,
            captions: [...document.querySelectorAll('#tray .attach-status')].map((node) => node.textContent),
            images: [...document.querySelectorAll('#tray img')].map((node) => node.getAttribute('src')),
        })""")

    def test_the_tray_takes_its_limit_from_the_page(self):
        page = self.open([self.attachment("a1"), self.attachment("a2"), self.attachment("a3")])
        self.choose(page, "one.png", "two.png", "three.png")
        state = self.state(page)
        self.assertEqual(state["max"], 2)
        self.assertEqual((state["count"], sorted(state["ids"])), (2, ["a1", "a2"]))
        self.assertIn("imgLimit", state["errors"])
        self.assertEqual(self.posted, 2, "the third photo is never uploaded")

    def test_the_same_photo_twice_stays_one_thumbnail_and_its_removal_deletes_once(self):
        page = self.open([self.attachment("same"), self.attachment("same", duplicate=True)])
        self.choose(page, "first.png", "again.png")
        state = self.state(page)
        self.assertEqual((state["count"], state["ids"]), (1, ["same"]))
        self.assertIn("imgDuplicate", state["errors"])
        self.assertEqual(self.deleted, [], "dropping the duplicate thumbnail must not delete the shared photo")
        page.click("#tray button[aria-label='imgRemove']")
        page.wait_for_function("() => window.__tray.count() === 0")
        page.wait_for_timeout(100)
        self.assertEqual(self.deleted, ["same"])

    def test_waiting_photos_come_back_after_a_refresh(self):
        page = self.open([])
        page.evaluate("""() => window.__tray.restore([
            {id: "w1", url: "/api/chat-attachments/w1/image", hints: []},
            {id: "w2", url: "/api/chat-attachments/w2/image", hints: ["The photo looks blurred."]},
            {id: "w3", url: "/api/chat-attachments/w3/image", hints: []},
            {id: "w1", url: "/api/chat-attachments/w1/image", hints: []}])""")
        state = self.state(page)
        self.assertEqual(state["ids"], ["w1", "w2"], "within the limit, each photo once")
        self.assertEqual(state["images"], ["/api/chat-attachments/w1/image", "/api/chat-attachments/w2/image"])
        self.assertIn("The photo looks blurred.", page.inner_text("#tray"))
        page.click("#tray figure:first-child button[aria-label='imgRemove']")
        page.wait_for_function("() => window.__tray.count() === 1")
        page.wait_for_timeout(100)
        self.assertEqual(self.deleted, ["w1"])

    def test_an_upload_answered_with_html_says_why_it_failed(self):
        page = self.open([(413, "<html><body>413 Request Entity Too Large</body></html>", "text/html")])
        self.choose(page, "big.png")
        state = self.state(page)
        self.assertTrue(state["failed"])
        self.assertEqual(state["captions"], ["imgTooLarge"])

    def test_upload_error_texts(self):
        page = self.open([])
        cases = [
            ([401, None, ""], "asstSessionExpired"),
            ([200, None, "http://learnova.test/login?next=%2Fassistant"], "asstSessionExpired"),
            ([413, None, ""], "imgTooLarge"),
            ([502, None, ""], "imgUploadFailed"),
            ([0, None, ""], "imgUploadFailed"),
            ([400, {"error": "Choose a photo first."}, ""], "Choose a photo first."),
            ([413, {"error": "This photo is larger than 15 MB."}, ""], "This photo is larger than 15 MB."),
        ]
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                self.assertEqual(page.evaluate("(args) => window.__uploadErrorText(...args)", arguments), expected)


if __name__ == "__main__":
    unittest.main()

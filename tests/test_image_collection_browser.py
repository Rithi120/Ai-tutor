"""static/js/image-collection.js in a real browser: the multi-image collection on its own.

The component as it ships, on a page with the app's CSP and stylesheets
(tests/js_browser/harness.py), uploading to a fake endpoint answered by Playwright. Covers
what the server tests cannot: picks accumulate (a native input forgets the first pick),
gallery and camera photos share one order, thumbnails are small and every object URL is
revoked, reorder by button, drag and keyboard, replace keeps the place, the full-screen
preview opens and closes (Escape, swipe) and frees its picture, the limit is said, and a
failed batch is retried alone without the page navigating away.

Skipped without Playwright or a Chromium-based browser (Store Python has no Playwright:
run with R:/ai_tutor/.venv/Scripts/python).
"""

import io
import json
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from PIL import Image

from tests.js_browser import harness

BROWSER = {}
PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="csrf-token" content="test-token">
<link rel="stylesheet" href="/static/css/learnova-tokens.css">
<link rel="stylesheet" href="/static/css/attachments.css">
<script nonce="__NONCE__">window.LEARNOVA_I18N = {imgcolPreviewTitle: "Page {number} of {total}"};</script></head>
<body>
<div id="drop"><input id="gallery" type="file" accept="application/pdf,image/jpeg,image/png,image/webp" multiple>
<input id="camera" type="file" accept="image/*" capture="environment"></div>
<div id="root"></div>
<script type="module" nonce="__NONCE__">
window.__revoked = [];
window.__created = [];
const create = URL.createObjectURL.bind(URL);
const revoke = URL.revokeObjectURL.bind(URL);
URL.createObjectURL = (blob) => { const url = create(blob); window.__created.push(url); return url; };
URL.revokeObjectURL = (url) => { window.__revoked.push(url); revoke(url); };
import { createImageCollection, materialsForm, uploadCollection, xhrUpload } from "/static/js/image-collection.js";
window.__errors = [];
window.__col = createImageCollection({
  root: document.querySelector("#root"), inputs: [document.querySelector("#gallery")],
  cameraInputs: [document.querySelector("#camera")], dropZone: document.querySelector("#drop"),
  max: Number(new URLSearchParams(location.search).get("max") || 40), maxFileBytes: 15 * 1024 * 1024,
  accept: document.querySelector("#gallery").accept, labels: true,
  onError: (message) => window.__errors.push(message),
});
window.__upload = () => uploadCollection(window.__col, {
  batch: { maxFiles: 2, maxBytes: 24 * 1024 * 1024 },
  send: async (entries, { onProgress }) => (await xhrUpload("/upload", materialsForm(entries), { onProgress })).data,
}).then((outcome) => { window.__outcome = { ok: outcome.ok, failed: outcome.failed, error: outcome.error?.message || "" }; });
window.__uploadCollection = uploadCollection;
window.__materialsForm = materialsForm;
window.__xhrUpload = xhrUpload;
window.__ready = true;
</script>
</body></html>
""".replace("__NONCE__", harness.NONCE)


def png(width=1200, height=1600, shade=200):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (shade, 255 - shade, 120)).save(buffer, format="PNG")
    return buffer.getvalue()


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


class CollectionTests(unittest.TestCase):
    def open(self, answers=(), *, max_items=40, viewport=(390, 844)):
        html = PAGE
        page = harness.open_page(BROWSER["browser"], html, viewport=viewport)
        self.addCleanup(page.context.close)
        if max_items != 40:
            page.goto(f"{harness.ORIGIN}/__page?max={max_items}")
            page.wait_for_function("() => window.__ready === true")
        self.posts = []
        queue = list(answers)

        def handle(route):
            body = route.request.post_data_buffer or b""
            names = [part.split(b'filename="', 1)[1].split(b'"', 1)[0].decode() for part in body.split(b"\r\n--")
                     if b'name="materials"' in part and b'filename="' in part]
            meta = []
            if b'name="materials_meta"' in body:
                chunk = body.split(b'name="materials_meta"', 1)[1].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
                meta = json.loads(chunk.decode())
            self.posts.append({"names": names, "meta": meta, "accept": route.request.headers.get("accept", ""),
                               "csrf": route.request.headers.get("x-csrftoken", "")})
            status, payload = queue.pop(0) if queue else (200, None)
            if payload is None:
                payload = {"ok": True, "results": [{"index": index, "status": "added"} for index in range(len(names))]}
            route.fulfill(status=status, body=json.dumps(payload), headers={"Content-Type": "application/json"})

        page.route("**/upload", handle)
        return page

    def pick(self, page, *names, selector="#gallery", size=(1200, 1600)):
        page.set_input_files(selector, files=[
            {"name": name, "mimeType": "image/png", "buffer": png(*size, shade=40 + 7 * index)}
            for index, name in enumerate(names)])

    def names(self, page):
        return page.evaluate("() => window.__col.items().map((item) => item.name)")

    def wait_thumbs(self, page, count):
        page.wait_for_function(f"() => document.querySelectorAll('#root .imgcol-thumb img:not([hidden])').length >= {count}",
                               timeout=20000)

    def test_picks_accumulate_and_camera_photos_join_the_same_order(self):
        page = self.open()
        self.pick(page, "a.png", "b.png")
        self.pick(page, "c.png")
        self.pick(page, "scan.png", selector="#camera")
        self.assertEqual(self.names(page), ["a.png", "b.png", "c.png", "scan.png"])
        kinds = page.evaluate("() => window.__col.items().map((item) => item.kind)")
        self.assertEqual(kinds, ["upload", "upload", "upload", "camera"])
        self.assertEqual(page.evaluate("() => document.querySelector('#gallery').value"), "",
                         "the input is emptied, so the next pick adds instead of replacing")
        self.wait_thumbs(page, 4)
        widths = page.evaluate("""() => Promise.all([...document.querySelectorAll('#root .imgcol-thumb img')].map(
            (img) => img.decode().then(() => Math.max(img.naturalWidth, img.naturalHeight))))""")
        self.assertTrue(all(0 < width <= 320 for width in widths), widths)

    def test_remove_revokes_the_thumbnail_and_the_limit_is_said(self):
        page = self.open(max_items=3)
        self.pick(page, "a.png", "b.png", "c.png", "d.png")
        self.assertEqual(self.names(page), ["a.png", "b.png", "c.png"])
        self.assertIn("imgcolLimit", page.evaluate("() => window.__errors"))
        self.wait_thumbs(page, 3)
        thumb = page.evaluate("() => document.querySelector('#root li:nth-child(2) .imgcol-thumb img').src")
        page.click("#root li:nth-child(2) .imgcol-remove")
        self.assertEqual(self.names(page), ["a.png", "c.png"])
        self.assertIn(thumb, page.evaluate("() => window.__revoked"))

    def test_reorder_with_buttons_keyboard_and_drag(self):
        page = self.open(viewport=(1280, 900))
        self.pick(page, "a.png", "b.png", "c.png", "d.png")
        page.click("#root li:nth-child(3) .imgcol-earlier")
        self.assertEqual(self.names(page), ["a.png", "c.png", "b.png", "d.png"])
        page.focus("#root li:nth-child(1) .imgcol-later")
        page.keyboard.press("Enter")
        self.assertEqual(self.names(page), ["c.png", "a.png", "b.png", "d.png"])
        self.assertEqual(page.evaluate("() => document.activeElement.closest('li').dataset.key"),
                         page.evaluate("() => window.__col.items()[1].key"), "the keyboard stays on the moved page")
        page.drag_and_drop("#root li:nth-child(4)", "#root li:nth-child(1)")
        self.assertEqual(self.names(page), ["d.png", "c.png", "a.png", "b.png"])
        numbers = page.evaluate("() => [...document.querySelectorAll('#root .imgcol-number')].map((n) => n.textContent)")
        self.assertEqual(numbers, ["1", "2", "3", "4"])

    def test_replace_keeps_the_place_and_frees_the_old_thumbnail(self):
        page = self.open()
        self.pick(page, "a.png", "b.png", "c.png")
        self.wait_thumbs(page, 3)
        old = page.evaluate("() => document.querySelector('#root li:nth-child(2) .imgcol-thumb img').src")
        key = page.evaluate("() => window.__col.items()[1].key")
        with page.expect_file_chooser() as chooser:
            page.click("#root li:nth-child(2) .imgcol-replace")
        chooser.value.set_files([{"name": "b-better.png", "mimeType": "image/png", "buffer": png(shade=99)}])
        page.wait_for_function("() => window.__col.items()[1].name === 'b-better.png'")
        self.assertEqual(self.names(page), ["a.png", "b-better.png", "c.png"])
        self.assertEqual(page.evaluate("() => window.__col.items()[1].key"), key)
        self.assertIn(old, page.evaluate("() => window.__revoked"))

    def test_the_full_screen_preview_opens_steps_and_closes(self):
        page = self.open()
        self.pick(page, "a.png", "b.png", "c.png")
        page.click("#root li:nth-child(2) .imgcol-thumb")
        page.wait_for_function("() => document.querySelector('dialog.imgcol-viewer').open")
        self.assertIn("2", page.inner_text("dialog.imgcol-viewer .imgcol-viewer-title"))
        first = page.evaluate("() => document.querySelector('dialog.imgcol-viewer img').src")
        page.keyboard.press("ArrowRight")
        self.assertIn("3", page.inner_text("dialog.imgcol-viewer .imgcol-viewer-title"))
        self.assertIn(first, page.evaluate("() => window.__revoked"), "the previous full picture is freed")
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('dialog.imgcol-viewer').open")
        self.assertFalse(page.evaluate("() => window.__col.previewOpen()"))
        self.assertEqual(page.evaluate("() => document.activeElement.closest('li')?.dataset.key"),
                         page.evaluate("() => window.__col.items()[2].key"), "the focus returns to the page")
        # A swipe down closes it on a phone.
        page.click("#root li:nth-child(1) .imgcol-thumb")
        page.wait_for_function("() => document.querySelector('dialog.imgcol-viewer').open")
        page.evaluate("""() => {
            const stage = document.querySelector('dialog.imgcol-viewer .imgcol-viewer-stage');
            stage.dispatchEvent(new PointerEvent('pointerdown', {clientX: 100, clientY: 100, bubbles: true}));
            stage.dispatchEvent(new PointerEvent('pointerup', {clientX: 104, clientY: 260, bubbles: true}));
        }""")
        page.wait_for_function("() => !document.querySelector('dialog.imgcol-viewer').open")
        created = page.evaluate("() => window.__created")
        revoked = set(page.evaluate("() => window.__revoked"))
        thumbs = set(page.evaluate("() => window.__col.items().map((item) => item.thumbUrl)"))
        self.assertEqual([url for url in created if url not in revoked and url not in thumbs], [],
                         "every full-size picture URL was revoked")

    def test_batches_send_in_order_with_meta_and_a_failed_batch_is_retried_alone(self):
        page = self.open([(200, None), (500, {"ok": False, "error": "Server busy"})])
        self.pick(page, "a.png", "b.png", "c.png")
        self.pick(page, "scan.png", selector="#camera")
        page.evaluate("() => window.__col.setLabel(window.__col.items()[0].key, 'Blatt 1')")
        page.evaluate("() => window.__upload()")
        page.wait_for_function("() => window.__outcome !== undefined", timeout=20000)
        outcome = page.evaluate("() => window.__outcome")
        self.assertEqual((outcome["ok"], outcome["failed"]), (False, 2))
        self.assertEqual(outcome["error"], "Server busy")
        self.assertEqual([post["names"] for post in self.posts], [["a.png", "b.png"], ["c.png", "scan.png"]])
        self.assertEqual(self.posts[0]["meta"], [{"kind": "upload", "label": "Blatt 1"}, {"kind": "upload"}])
        self.assertEqual(self.posts[1]["meta"], [{"kind": "upload"}, {"kind": "camera"}])
        self.assertTrue(all(post["accept"].startswith("application/json") for post in self.posts))
        self.assertTrue(all(post["csrf"] == "test-token" for post in self.posts))
        statuses = page.evaluate("() => window.__col.items().map((item) => item.status)")
        self.assertEqual(statuses, ["done", "done", "failed", "failed"])
        self.assertEqual(page.url.split("?")[0], f"{harness.ORIGIN}/__page", "a failure never navigates away")
        # Saved pages are fixed; the failed ones can still be removed.
        self.assertTrue(page.evaluate("() => document.querySelector('#root li:nth-child(1) .imgcol-remove').disabled"))
        page.evaluate("() => { window.__outcome = undefined; return window.__upload(); }")
        page.wait_for_function("() => window.__outcome !== undefined", timeout=20000)
        self.assertTrue(page.evaluate("() => window.__outcome.ok"))
        self.assertEqual(self.posts[2]["names"], ["c.png", "scan.png"], "only the failed pages are sent again")

    def test_a_page_added_while_the_upload_runs_is_sent_in_the_same_run(self):
        # Review fix: the scanner can hand over a page while batches are being sent; the run
        # used to finish without it and the form navigated away from an unsent page.
        page = self.open()
        self.pick(page, "a.png", "b.png")
        page.evaluate("""() => {
            let added = false;
            return window.__uploadCollection(window.__col, {
              batch: { maxFiles: 2, maxBytes: 24 * 1024 * 1024 },
              send: async (entries, { onProgress }) => {
                if (!added) {
                  added = true;
                  const source = window.__col.items()[0].file;
                  window.__col.add([new File([source], "late.png", { type: "image/png" })], { kind: "camera" });
                }
                return (await window.__xhrUpload("/upload", window.__materialsForm(entries), { onProgress })).data;
              },
            }).then((outcome) => { window.__outcome = { ok: outcome.ok, failed: outcome.failed }; });
        }""")
        page.wait_for_function("() => window.__outcome !== undefined", timeout=20000)
        self.assertTrue(page.evaluate("() => window.__outcome.ok"))
        self.assertEqual([post["names"] for post in self.posts], [["a.png", "b.png"], ["late.png"]])
        self.assertEqual(self.posts[1]["meta"], [{"kind": "camera"}])
        self.assertEqual(page.evaluate("() => window.__col.items().map((item) => item.status)"),
                         ["done", "done", "done"])

    def test_a_json_413_marks_the_batch_failed_with_the_servers_reason(self):
        page = self.open([(413, {"ok": False, "error": "The upload is too large.", "code": "upload_too_large"})])
        self.pick(page, "a.png")
        page.evaluate("() => window.__upload()")
        page.wait_for_function("() => window.__outcome !== undefined", timeout=20000)
        self.assertEqual(page.evaluate("() => window.__col.items()[0].error"), "The upload is too large.")
        self.assertIn("The upload is too large.", page.inner_text("#root .imgcol-status"))

    def test_large_photos_are_reencoded_but_camera_scans_stay_lossless(self):
        page = self.open()
        self.pick(page, "huge.png", size=(5000, 3000))
        self.pick(page, "scan.png", selector="#camera", size=(5000, 3000))
        page.evaluate("() => window.__upload()")
        page.wait_for_function("() => window.__outcome !== undefined", timeout=30000)
        self.assertEqual(self.posts[0]["names"], ["huge.jpg", "scan.png"])


if __name__ == "__main__":
    unittest.main()

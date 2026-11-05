"""The start page's analysis loop in a real browser (FIX_PLAN PF2): the real template,
project-start.js and the real Flask app (scripts/mobile_audit.start_server) with the AI
patched.

What the server tests cannot show: the page really loops POST /analysis/step until the
server says finished, shows "part x of y", then opens the topics; a reload in the middle
carries on by itself without paying for a batch twice; a proxy that answers 504 while the
server still works is waited out ("This takes a little longer"), not turned into "Try
again"; and while another tab holds the analysis the page says so calmly and continues
once it is free.

Skipped when Playwright or a Chromium-based browser is missing (Store Python has no
Playwright: run with R:/ai_tutor/.venv/Scripts/python).
"""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
import uuid
from datetime import timedelta
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_start_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from tests.js_browser import harness  # noqa: E402

BROWSER = {}
SLOW = "This takes a little longer. Learnova keeps working – please keep this page open."
BUSY = "Learnova is already working out the topics of this project. This page updates by itself."
# Every text #startBuildDetail shows, in order (the loop rewrites it), handed to Python as it
# appears: the page navigates to the topics at the end, which would lose a page variable.
RECORD_DETAILS = """
document.addEventListener("DOMContentLoaded", () => {
  const node = document.getElementById("startBuildDetail");
  if (!node) return;
  new MutationObserver(() => {
    if (node.textContent) window.__recordDetail(node.textContent);
  }).observe(node, { childList: true, characterData: true, subtree: true });
});
"""


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


class StartPageLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not BROWSER:
            raise unittest.SkipTest("no browser")
        from tests import test_project_extraction as extraction

        cls.extraction = extraction
        cls.audit = harness._audit_module()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.application, cls.server = cls.audit.start_server(port)
        cls.base = f"http://127.0.0.1:{port}"
        cls.lock = threading.Lock()
        cls.prompts = []
        cls.delay = 0.0
        cls.ocr_busy = False
        cls.ocr_calls = 0
        cls.application.create_response.side_effect = cls.fake
        cls.saved = {key: cls.application.app.config.get(key) for key in ("ANALYSIS_STEP_CALLS",)}

    @classmethod
    def tearDownClass(cls):
        cls.application.app.config.update(cls.saved)
        cls.server.shutdown()

    @classmethod
    def fake(cls, **kwargs):
        if kwargs.get("task_type") == "ocr_document_recognition" and cls.ocr_busy:
            with cls.lock:
                cls.ocr_calls += 1
            raise cls.application.ai_service.AIProviderError(
                "provider_rate_limit", "The AI provider rate limit was reached.")
        if kwargs.get("task_type") != "project_section_generation":
            return cls.audit._fake_response(**kwargs)
        prompt = str(kwargs.get("input", ""))
        with cls.lock:
            cls.prompts.append(prompt)
        if cls.delay:
            time.sleep(cls.delay)
        batch = cls.extraction.batch_of(prompt)
        topics = [topic for topic in (cls.extraction.topic_for(entry) for entry in batch) if topic]
        return cls.extraction.FakeResponse({"sections": topics})

    def setUp(self):
        with self.lock:
            self.prompts.clear()
        type(self).delay = 0.0
        type(self).ocr_busy = False
        type(self).ocr_calls = 0
        self.application.app.config["ANALYSIS_STEP_CALLS"] = 1

    # ------------------------------------------------------------------ helpers
    def student(self, width=1280, height=800):
        context = BROWSER["browser"].new_context(viewport={"width": width, "height": height},
                                                 is_mobile=width <= 430, has_touch=width <= 430)
        context.add_init_script(harness.KATEX_STUB)
        self.details = []
        context.expose_binding("__recordDetail", lambda _source, text: self.details.append(text))
        context.add_init_script(RECORD_DETAILS)
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.name = f"s{uuid.uuid4().hex[:10]}"
        response = page.request.post(f"{self.base}/register", form={
            "username": self.name, "email": f"{self.name}@example.com", "password": "correct-horse-battery",
            "grade": "10"})
        self.assertLess(response.status, 400)
        page.request.post(f"{self.base}/api/tour/complete")
        # Project Mode's feature tips would start by themselves over the page (docs/TUTORIAL.md).
        page.request.post(f"{self.base}/tutorial/tips", form={"back": "hub"})
        return page

    def project(self, documents):
        """A project of read pages (twelve Ethics pages = three batches) for this student."""

        application, db = self.application, self.application.db
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == self.name))
            project = application.LearningProject(user_id=user.id, title="Ethik", subject="Ethics", status="uploaded")
            db.session.add(project)
            db.session.flush()
            order = 0
            for number, indexes in enumerate(documents, start=1):
                file = application.ProjectFile(project_id=project.id, original_filename=f"ethik-{number}.pdf",
                                               mime_type="application/pdf", original_data=b"%PDF", sha256=f"{number}")
                db.session.add(file)
                db.session.flush()
                for page_number, index in enumerate(indexes, start=1):
                    order += 1
                    db.session.add(application.ProjectPage(
                        project_id=project.id, file_id=file.id, page_number=page_number, page_order=order,
                        extracted_text=self.extraction.page_text(index), extraction_status="ready",
                        confidence_status="high", recognition_confidence=0.95, review_status="pending",
                        recognition_json=json.dumps({"text_source": "text_layer"})))
            db.session.commit()
            return project.id

    def watch_steps(self, page, project_id):
        statuses = []
        step_url = f"/projects/{project_id}/analysis/step"

        def record(response):
            if response.url.endswith(step_url):
                statuses.append(response.status)
        page.on("response", record)
        return statuses

    def wait_for_topics(self, page, project_id, timeout=45000):
        # Matched in Python: the app's CSP (no unsafe-eval) refuses Playwright's in-page polling.
        page.wait_for_url(lambda url: url.endswith(f"/projects/{project_id}#topics"), timeout=timeout)

    def batches_asked(self):
        with self.lock:
            return [tuple(entry["page_id"] for entry in self.extraction.batch_of(prompt)) for prompt in self.prompts]

    def sections(self, project_id):
        application, db = self.application, self.application.db
        with application.app.app_context():
            return db.session.scalar(db.select(db.func.count(application.LearningSection.id)).where(
                application.LearningSection.project_id == project_id))

    # ------------------------------------------------------------------ tests
    def test_the_page_works_the_topics_out_in_steps_then_opens_them(self):
        page = self.student()
        project_id = self.project(self.extraction.SIX_DOCUMENTS)
        statuses = self.watch_steps(page, project_id)
        type(self).delay = 0.4
        page.goto(f"{self.base}/projects/{project_id}/start?auto=1")
        self.wait_for_topics(page, project_id)
        self.assertEqual(statuses, [200, 200, 200], "three one-call steps, the third says finished")
        asked = self.batches_asked()
        self.assertEqual(len(asked), 3)
        self.assertEqual(len(set(asked)), 3, "no batch is paid twice")
        self.assertEqual(self.sections(project_id), 12)
        self.assertIn("Working out the topics: part 2 of 3", self.details)
        self.assertEqual(self.errors, [])

    def test_a_reload_mid_analysis_carries_on_by_itself_without_paying_twice(self):
        page = self.student()
        project_id = self.project(self.extraction.SIX_DOCUMENTS)
        type(self).delay = 1.0
        with page.expect_response(lambda response: response.url.endswith(f"/projects/{project_id}/analysis/step")):
            page.goto(f"{self.base}/projects/{project_id}/start?auto=1")
        # The student reloads (without ?auto=1) while the second step may still be running.
        page.goto(f"{self.base}/projects/{project_id}/start")
        self.assertEqual(page.get_attribute("#projectStart", "data-auto"), "true",
                         "an unfinished round goes on by itself")
        self.wait_for_topics(page, project_id)
        asked = self.batches_asked()
        self.assertEqual(len(asked), 3, f"three batches, none twice: {asked}")
        self.assertEqual(len(set(asked)), 3)
        self.assertEqual(self.sections(project_id), 12)
        self.assertEqual(self.errors, [])

    def test_a_proxy_timeout_is_waited_out_and_never_shown_as_a_failure(self):
        page = self.student()
        project_id = self.project(self.extraction.SIX_DOCUMENTS)
        cut = {"done": False}

        def gateway_timeout(route):
            # The proxy gives up on the first step while the server finishes it.
            response = route.fetch()
            if not cut["done"]:
                cut["done"] = True
                route.fulfill(status=504, content_type="text/html", body="<h1>504 Gateway Time-out</h1>")
            else:
                route.fulfill(response=response)
        page.route(f"**/projects/{project_id}/analysis/step", gateway_timeout)
        page.goto(f"{self.base}/projects/{project_id}/start?auto=1")
        self.wait_for_topics(page, project_id, timeout=60000)
        self.assertTrue(cut["done"])
        self.assertIn(SLOW, self.details)
        asked = self.batches_asked()
        self.assertEqual(len(asked), 3, f"the step behind the 504 was not paid again: {asked}")
        self.assertEqual(len(set(asked)), 3)
        self.assertEqual(self.errors, [])

    def test_while_another_tab_holds_the_analysis_the_page_waits_calmly(self):
        page = self.student(width=375, height=740)
        project_id = self.project(self.extraction.SIX_DOCUMENTS)
        self.application.app.config["ANALYSIS_STEP_CALLS"] = 2
        application, db = self.application, self.application.db
        with application.app.app_context():
            held = db.session.get(application.LearningProject, project_id)
            held.analysis_lease_token = "another-tab"
            held.analysis_lease_until = application.utcnow() + timedelta(seconds=4)
            db.session.commit()
        statuses = self.watch_steps(page, project_id)
        page.goto(f"{self.base}/projects/{project_id}/start?auto=1")
        self.wait_for_topics(page, project_id, timeout=60000)
        self.assertIn(202, statuses, "the other tab's analysis was waited for")
        self.assertEqual(statuses[-2:], [200, 200])
        self.assertIn(BUSY, self.details)
        self.assertEqual(len(self.batches_asked()), 3)
        self.assertEqual(self.errors, [])

    def test_the_steps_own_hourly_limit_stops_the_page_without_the_one_long_request(self):
        # Review A F6: the limiter's 429 ({"code": "rate_limited"}, no "stopped") fell through
        # to quick-start, which then worked out every remaining topic in one long request.
        page = self.student()
        project_id = self.project(self.extraction.SIX_DOCUMENTS)
        quick_starts = []
        page.on("request", lambda request: quick_starts.append(request.url)
                if request.url.endswith(f"/projects/{project_id}/quick-start") else None)
        answered = {"steps": 0}

        def limited(route):
            answered["steps"] += 1
            if answered["steps"] == 1:
                route.continue_()
                return
            route.fulfill(status=429, content_type="application/json", headers={"Retry-After": "3599"},
                          body=json.dumps({"ok": False, "code": "rate_limited",
                                           "error": "Too many requests. Please wait a moment and try again."}))
        page.route(f"**/projects/{project_id}/analysis/step", limited)
        page.goto(f"{self.base}/projects/{project_id}/start?auto=1")
        page.wait_for_selector("#startError:not([hidden])", timeout=30000)
        self.assertIn("Too many requests", page.inner_text("#startError"))
        self.assertTrue(page.is_enabled("#startButton"), "the student can try again later")
        self.assertEqual(quick_starts, [], "no one-request analysis behind the student's back")
        self.assertEqual(answered["steps"], 2, "the loop stopped at the limit")
        self.assertEqual(len(self.batches_asked()), 1, "only the first step was paid")
        self.assertEqual(self.errors, [])

    def photo_project(self, count):
        """A project of `count` photos nobody has read yet."""

        import io

        from PIL import Image, ImageDraw

        application, db = self.application, self.application.db
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == self.name))
            project = application.LearningProject(user_id=user.id, title="Ethik", subject="Ethics", status="uploaded")
            db.session.add(project)
            db.session.flush()
            page_ids = []
            for number in range(1, count + 1):
                image = Image.new("RGB", (900, 1200), "white")
                ImageDraw.Draw(image).text((60, 80), f"Tafelbild {number}", fill="black")
                data = io.BytesIO()
                image.save(data, format="PNG")
                file = application.ProjectFile(project_id=project.id, original_filename=f"tafel-{number}.png",
                                               mime_type="image/png", original_data=data.getvalue(),
                                               sha256=f"tafel-{number}-{uuid.uuid4().hex}")
                db.session.add(file)
                db.session.flush()
                page = application.ProjectPage(project_id=project.id, file_id=file.id, page_number=1,
                                               page_order=number, extracted_text="", extraction_status="pending",
                                               review_status="pending")
                db.session.add(page)
                db.session.flush()
                page_ids.append(page.id)
            db.session.commit()
            return project.id, page_ids

    def test_a_page_read_offline_because_the_ai_is_busy_stops_the_reading(self):
        # Review B B-1: the provisional offline reading counted as read, so the page went on
        # and asked the rate-limited vision model again for every following photo.
        from unittest.mock import patch

        from learnova.ocr.local import LocalOcrResult
        from learnova.vocabulary.layout import Line

        offline = LocalOcrResult(lines=[Line("Die Wuerde des Menschen ist unantastbar.", (0.1, 0.1, 0.9, 0.15), 0.93),
                                        Line("Artikel 1 Grundgesetz", (0.1, 0.2, 0.6, 0.25), 0.88)],
                                 width=900, height=1200, engine="rapidocr", duration_ms=5)
        application = self.application
        type(self).ocr_busy = True
        saved = application.app.config.get("PROJECT_LOCAL_OCR_IN_TESTS")
        application.app.config["PROJECT_LOCAL_OCR_IN_TESTS"] = True
        self.addCleanup(application.app.config.update, PROJECT_LOCAL_OCR_IN_TESTS=saved)
        page = self.student()
        notices = []
        page.expose_binding("__recordNotice", lambda _source, text: notices.append(text))
        page.add_init_script("""
document.addEventListener("DOMContentLoaded", () => {
  const node = document.getElementById("startNotice");
  if (!node) return;
  new MutationObserver(() => { if (node.textContent) window.__recordNotice(node.textContent); })
    .observe(node, { childList: true, characterData: true, subtree: true });
});
""")
        project_id, page_ids = self.photo_project(3)
        recognized = []
        page.on("response", lambda response: recognized.append(response.url)
                if response.url.endswith("/recognize") else None)
        with patch.object(application.local_ocr, "available", return_value=True), \
                patch.object(application.local_ocr, "recognize", return_value=offline), \
                patch.object(application.ai_service, "remaining_hourly_requests", return_value=40):
            # The chain goes on with what was read (the topics step, then quick-start).
            with page.expect_response(lambda response: response.url.endswith(f"/projects/{project_id}/quick-start"),
                                      timeout=60000):
                page.goto(f"{self.base}/projects/{project_id}/start?auto=1")
        self.assertEqual(self.ocr_calls, 1, "one refused request, not one per photo")
        self.assertEqual(len(recognized), 1, "the page stopped sending pages")
        self.assertTrue(any("Learnova read this page offline for now" in text for text in notices), notices)
        with application.app.app_context():
            statuses = [application.db.session.get(application.ProjectPage, page_id).extraction_status
                        for page_id in page_ids]
        self.assertEqual(statuses, ["ready", "pending", "pending"])
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()

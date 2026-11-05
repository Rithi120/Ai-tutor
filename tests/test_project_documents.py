"""Every uploaded document is read, accounted for and shown - Projektmodus track DOC1.

The user's six papers became one: vision was rate-limited, every failing page still tried
all three image modes, quick-start read the failed pages again, the hourly cap tripped a
six-hour cooldown, and the flow went on as soon as any page had text. These tests pin the
fix end to end through the real routes (every model call patched, no provider reached):

* six uploaded documents - two born-digital PDFs (one page of the second repeats a page
  of the first), a handwritten photo, a scanned page without a text layer, a partly
  unreadable PDF and a byte-identical copy - all appear in the material report, each
  with its own state ("Document 3: Needs OCR review");
* a rate-limited page costs one call and waits (or is read from its own text layer)
  instead of failing; a free account low on hourly requests is not read at all;
* the text-layer and offline-OCR fallbacks;
* quick-start never re-reads failed pages, and a read page is never re-read;
* the analysis route is the owner's only; migration 037; the subject list.
"""

import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_documents_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pymupdf  # noqa: E402  pyright: ignore[reportMissingImports]
from PIL import Image, ImageDraw  # noqa: E402

import app as application  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.contracts import AIValidationError  # noqa: E402
from learnova.lessons.subjects import SUBJECT_CHOICES  # noqa: E402
from learnova.ocr.local import LocalOcrResult  # noqa: E402
from learnova.vocabulary.layout import Line  # noqa: E402

db = application.db


def fetch(model, ident):
    """db.session.get that fails the test instead of returning None."""

    row = db.session.get(model, ident)
    assert row is not None, f"{model.__name__} {ident} does not exist"
    return row

UTILITARISMUS = (
    "Der Utilitarismus bewertet Handlungen nach ihren Folgen. Eine Handlung ist moralisch richtig, "
    "wenn sie das groesste Glueck der groessten Zahl bewirkt. Jeremy Bentham schlug dafuer einen "
    "Kalkuel vor, mit dem Freude und Leid gemessen werden sollen. John Stuart Mill unterschied "
    "spaeter hoehere und niedere Freuden und verteidigte damit den Utilitarismus gegen Kritik.")
KANT = (
    "Immanuel Kant begruendet die Moral nicht mit den Folgen, sondern mit der Pflicht. Der "
    "kategorische Imperativ verlangt, nur nach der Maxime zu handeln, von der man zugleich wollen "
    "kann, dass sie ein allgemeines Gesetz werde. Der Mensch darf nie nur als Mittel gebraucht "
    "werden, sondern immer zugleich als Zweck an sich selbst.")
SPARSE = "Arbeitsblatt Ethik: Name und Datum eintragen"   # a title line: 30+ letters, < 200


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = json.dumps(payload)


def reading(text, confidence=0.9, bbox=None):
    return FakeResponse({"blocks": [{
        "type": "handwriting", "content": text, "confidence": confidence, "bbox": bbox or [],
        "crossed_out": False, "important_candidate": False, "teacher_highlight_candidate": False,
        "nearby_text": ""}], "detected_page_number": "", "warning": ""})


EMPTY = FakeResponse({"blocks": [], "detected_page_number": "", "warning": "blank"})


def rate_limited(**_kwargs):
    raise ai_service.AIProviderError("provider_rate_limit", "The AI provider rate limit was reached.")


def png(label="", colour="white", size=(900, 1200)):
    image = Image.new("RGB", size, colour)
    if label:
        ImageDraw.Draw(image).text((60, 80), label, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def text_pdf(*pages):
    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    for text in pages:
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        if text:
            page.insert_textbox(pymupdf.Rect(60, 60, 540, 760), text, fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def scanned_pdf(pages=1):
    """Pages that are one full-page picture each, with no text layer (a scanner app)."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    for index in range(pages):
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        page.insert_image(page.rect, stream=png(f"scan {index + 1}", "#f4f1ea", (850, 1100)))
    data = document.tobytes()
    document.close()
    return data


class Base(unittest.TestCase):
    def setUp(self):
        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "AI_MODE", "WTF_CSRF_ENABLED")
        application.app.config.update(TESTING=True, AI_MODE="cached", WTF_CSRF_ENABLED=False)
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("dora")

    @staticmethod
    def register(name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery"})
        return client

    def upload(self, files, *, client=None, **fields):
        data = {"title": "Ethik", "subject": "Other", "subject_other": "Ethik", **fields,
                "materials": [(io.BytesIO(content), name, mime) for name, content, mime in files]}
        return (client or self.client).post("/projects", data=data, content_type="multipart/form-data")

    def project(self):
        project = db.session.scalars(db.select(application.LearningProject).order_by(
            application.LearningProject.id.desc())).first()
        assert project is not None, "no project was created"
        return project

    def pages(self, project_id):
        return db.session.scalars(db.select(application.ProjectPage).where(
            application.ProjectPage.project_id == project_id).order_by(application.ProjectPage.page_order)).all()

    def recognize(self, project_id, page_id, responses=(), *, body=None, side_effect=None):
        effect = side_effect if side_effect is not None else list(responses)
        with patch.object(application, "create_response", side_effect=effect) as mocked:
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json=body or {})
        return response, mocked

    def report(self, project_id, client=None):
        response = (client or self.client).get(f"/projects/{project_id}/analysis")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        return response.get_json()


class SixDocumentsTests(Base):
    """The user's case: six documents in, six documents accounted for."""

    def test_every_document_appears_in_the_report_with_its_own_state(self):
        second = text_pdf(UTILITARISMUS)         # repeats page 1 of the first PDF
        uploaded = self.upload([
            ("ethik-1.pdf", text_pdf(UTILITARISMUS, KANT), "application/pdf"),
            ("ethik-2.pdf", second, "application/pdf"),
            ("handschrift.png", png("Pflicht und Neigung"), "image/png"),
            ("scan.pdf", scanned_pdf(1), "application/pdf"),
            ("teilweise.pdf", scanned_pdf(2), "application/pdf"),
            ("ethik-2-kopie.pdf", second, "application/pdf"),
        ])
        self.assertEqual(uploaded.status_code, 302, uploaded.get_data(as_text=True)[:300])
        self.assertIn("/start", uploaded.headers["Location"])
        flashed = self.client.get("/projects").get_data(as_text=True)
        self.assertIn("“ethik-2-kopie.pdf” was uploaded twice; Learnova kept one copy.", flashed)
        with application.app.app_context():
            project = self.project()
            project_id = project.id
            self.assertEqual(project.subject, "Ethics", "Ethik is stored by its canonical name")
            self.assertEqual(json.loads(project.analysis_json)["skipped_duplicate_files"], ["ethik-2-kopie.pdf"])
            ids = [page.id for page in self.pages(project_id)]
            self.assertEqual(len(ids), 7, "2 + 1 + 1 + 1 + 2 pages, the copy adds none")
        a1, a2, b1, photo, scan, part1, part2 = ids

        start = self.client.get(f"/projects/{project_id}/start?auto=1").get_data(as_text=True)
        self.assertEqual(start.count('class="start-file" data-file-id='), 5, "one row per stored document")
        for name in ("ethik-1.pdf", "ethik-2.pdf", "handschrift.png", "scan.pdf", "teilweise.pdf"):
            self.assertIn(name, start)
        # ...and the copy that added no pages is still listed by name (review DOC1r).
        self.assertEqual(start.count('class="start-file" data-state="duplicate"'), 1)
        self.assertIn("“ethik-2-kopie.pdf” was uploaded twice; Learnova kept one copy.",
                      start.split('class="start-file" data-state="duplicate"', 1)[1])
        self.assertIn(f'data-page-ids="{json.dumps(ids)}"'.replace(", ", ","), start.replace(", ", ","))
        self.assertIn('data-analysis-url="/projects/', start)

        # The start page's script: one request per page.
        for page_id in (a1, a2, b1):
            response, mocked = self.recognize(project_id, page_id)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mocked.call_count, 0, "a born-digital page is read from its text layer")
            self.assertEqual(response.get_json()["state"], "read")
        response, mocked = self.recognize(project_id, photo, [reading("Pflicht und Neigung", 0.4)] * 3)
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"]), (200, "needs_review"))
        self.assertEqual(body["file"]["reading_message"], "Document 3: Needs OCR review")
        self.assertEqual(body["file_id"], body["file"]["file_id"])
        self.recognize(project_id, scan, [reading("Gerechtigkeit nach Rawls: der Schleier des Nichtwissens")])
        self.recognize(project_id, part1, [reading("Freiheit und Verantwortung im Alltag")])
        response, mocked = self.recognize(project_id, part2, [EMPTY] * 3)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["state"], "unreadable")
        self.assertIn("teilweise.pdf", response.get_json()["message"])

        report = self.report(project_id)
        files = report["files"]
        self.assertEqual([file["filename"] for file in files],
                         ["ethik-1.pdf", "ethik-2.pdf", "handschrift.png", "scan.pdf", "teilweise.pdf"])
        self.assertEqual([file["reading_state"] for file in files],
                         ["read", "read", "needs_review", "read", "partly_read"])
        self.assertEqual(report["skipped_duplicate_files"], ["ethik-2-kopie.pdf"])
        self.assertIn("duplicate_file_skipped", {issue["code"] for issue in report["issues"]})
        # The partly read document counts as read and says so (FIX1, P1-05).
        self.assertEqual(report["summary"], "5 of 5 documents read (1 partly) · 2 need(s) checking")
        self.assertFalse(report["reading"]["complete"])
        self.assertEqual(files[4]["unreadable_page_numbers"], [2])
        self.assertEqual({page["text_source"] for page in files[0]["pages"]}, {"text_layer"})
        self.assertEqual(files[2]["pages"][0]["text_source"], "vision")
        # Nothing is analysed yet, and the report does not pretend otherwise.
        self.assertFalse(report["complete"])
        self.assertTrue(all(file["state"] != "analysed" for file in files))

        # What the topic analysis (track DOC2) stores: analysed pages, the duplicate page,
        # and topics citing their pages.
        with application.app.app_context():
            project = fetch(application.LearningProject, project_id)
            analysis = json.loads(project.analysis_json)
            analysis.update(analysed_page_ids=[a1, a2, photo, scan, part1], duplicates={str(b1): a1})
            project.analysis_json = json.dumps(analysis)
            for position, (title, cited) in enumerate((("Utilitarismus", [a1]), ("Kant", [a2]),
                                                       ("Pflicht", [photo]), ("Gerechtigkeit", [scan, part1])), 1):
                db.session.add(application.LearningSection(
                    project_id=project_id, position=position, title=title,
                    source_page_ids_json=json.dumps(cited),
                    topic_map_json=json.dumps({"terms": [{"term": title}]})))
            db.session.commit()
        report = self.report(project_id)
        files = report["files"]
        self.assertEqual([file["state"] for file in files],
                         ["analysed", "analysed", "needs_ocr_review", "analysed", "partly_analysed"])
        self.assertEqual(files[1]["pages"][0]["reading"], "duplicate")
        self.assertEqual(files[2]["message"], "Document 3: Needs OCR review")
        self.assertEqual(files[4]["message"], "Document 5: Partly analysed")
        self.assertEqual(report["documents_analysed"], 4)
        self.assertEqual(report["terms_total"], 4)
        self.assertFalse(report["complete"], "a page that could not be read keeps it incomplete")
        unreadable = next(issue for issue in report["issues"] if issue["code"] == "unreadable_pages")
        self.assertEqual(unreadable["message"], "Document 5 (“teilweise.pdf”): page(s) 2 could not be read.")

        # The student leaves the unreadable page out: now everything left is read and covered.
        with application.app.app_context():
            fetch(application.ProjectPage, part2).excluded = True
            db.session.commit()
        report = self.report(project_id)
        self.assertTrue(report["nothing_missing"])
        # Review A F2: still not "complete" while the handwritten page waits for its check.
        self.assertFalse(report["complete"])
        self.assertEqual([issue["code"] for issue in report["issues"] if issue["code"] == "needs_review"],
                         ["needs_review"])

    def test_a_page_the_student_confirmed_on_the_review_page_no_longer_needs_checking(self):
        # Review DOC1r: "Confirm" on the recognition review is the student's check. The
        # document must not keep saying "Needs OCR review" after it.
        self.upload([("foto.png", png("Pflicht"), "image/png")])
        with application.app.app_context():
            project = self.project()
            project_id, page_id = project.id, self.pages(project.id)[0].id
        self.recognize(project_id, page_id, [reading("Pflicht und Neigung", 0.4)] * 3)
        self.assertEqual(self.report(project_id)["files"][0]["reading_state"], "needs_review")
        confirmed = self.client.post(f"/projects/{project_id}/review", data={"action": "confirm"})
        self.assertEqual(confirmed.status_code, 302)
        report = self.report(project_id)
        self.assertEqual(report["files"][0]["reading_state"], "read")
        page = report["files"][0]["pages"][0]
        self.assertEqual((page["text_source"], page["checked"]), ("vision", True),
                         "the stored source stays what it was")
        with application.app.app_context():
            issues = {warning["issue"] for warning in application.project_material_warnings(self.project())}
        self.assertNotIn("uncertain", issues)

    def test_the_workspace_warnings_name_the_document_and_the_page_in_it(self):
        self.upload([("eins.png", png("eins"), "image/png"),
                     ("zwei.pdf", scanned_pdf(2), "application/pdf")])
        with application.app.app_context():
            project = self.project()
            page = self.pages(project.id)[2]
            page.extraction_status = "waiting"
            db.session.commit()
            warnings = application.project_material_warnings(self.project())
        self.assertEqual(len(warnings), 3, "pages nobody has read are reported, not skipped")
        self.assertEqual({warning["issue"] for warning in warnings}, {"pending"},
                         "a page not read yet is 'not read yet', not 'partly unclear'")
        last = warnings[-1]
        self.assertEqual((last["document"], last["filename"], last["page_number"], last["waiting"]),
                         ("Document 2", "zwei.pdf", 2, True))


class RateLimitAndBudgetTests(Base):
    def photos(self, count):
        self.upload([(f"foto-{index}.png", png(f"Seite {index}"), "image/png") for index in range(1, count + 1)])
        with application.app.app_context():
            project = self.project()
            return project.id, [page.id for page in self.pages(project.id)]

    def test_a_rate_limit_costs_one_call_per_page_and_leaves_the_page_waiting(self):
        project_id, page_ids = self.photos(3)
        for page_id in page_ids:
            response, mocked = self.recognize(project_id, page_id, side_effect=rate_limited)
            self.assertEqual(mocked.call_count, 1, "no second and third image mode after a rate limit")
            self.assertEqual(response.status_code, 202)
            body = response.get_json()
            self.assertEqual((body["state"], body["status"], body["ok"]), ("waiting", "waiting", False))
            self.assertIn("busy", body["message"])
        with application.app.app_context():
            states = [(page.extraction_status, page.retry_count) for page in self.pages(project_id)]
        self.assertEqual(states, [("waiting", 1)] * 3, "waiting, not failed")
        report = self.report(project_id)
        self.assertTrue(report["waiting"])
        self.assertEqual({file["reading_state"] for file in report["files"]}, {"waiting"})
        self.assertFalse(report["complete"])
        # Quick-start right after does not knock again: nothing to read yet, said plainly.
        with patch.object(application, "create_response", side_effect=AssertionError("no call")) as mocked:
            started = self.client.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual(mocked.call_count, 0)
        self.assertEqual((started.status_code, started.get_json()["code"]), (429, "reading_paused"))

    def test_a_rate_limited_pdf_page_falls_back_to_its_own_text_layer(self):
        self.upload([("titel.pdf", text_pdf(SPARSE), "application/pdf")])
        with application.app.app_context():
            project = self.project()
            project_id, page_id = project.id, self.pages(project.id)[0].id
        response, mocked = self.recognize(project_id, page_id, side_effect=rate_limited)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["state"], "read")
        with application.app.app_context():
            page = fetch(application.ProjectPage, page_id)
            self.assertEqual((page.extraction_status, page.confidence_status), ("ready", "review"))
            self.assertEqual(json.loads(page.recognition_json)["text_source"], "text_layer")
            self.assertIn("Arbeitsblatt", page.extracted_text)
            self.assertIn("text layer", page.warning)
            self.assertNotIn("glare", page.warning, "a born-digital page has no photo advisories")

    def test_a_page_that_keeps_meeting_a_busy_provider_ends_as_failed(self):
        project_id, page_ids = self.photos(1)
        with application.app.app_context():
            fetch(application.ProjectPage, page_ids[0]).retry_count = application.MAX_PROVIDER_WAITS
            db.session.commit()
        response, mocked = self.recognize(project_id, page_ids[0], side_effect=rate_limited)
        self.assertEqual((response.status_code, mocked.call_count), (422, 1))
        with application.app.app_context():
            self.assertEqual(fetch(application.ProjectPage, page_ids[0]).extraction_status, "failed")

    def test_an_empty_first_mode_then_a_rate_limit_waits_instead_of_unreadable(self):
        # Review DOC1r: the first image mode came back empty, the second was rate-limited.
        # Only one mode was really tried, so the page is not "unreadable" (which needs the
        # student's Retry) - the provider was out, and the page waits.
        project_id, page_ids = self.photos(1)
        busy = ai_service.AIProviderError("provider_rate_limit", "The AI provider rate limit was reached.")
        response, mocked = self.recognize(project_id, page_ids[0], side_effect=[EMPTY, busy])
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual((response.status_code, response.get_json()["state"]), (202, "waiting"))
        with application.app.app_context():
            self.assertEqual(fetch(application.ProjectPage, page_ids[0]).extraction_status, "waiting")

    def test_an_empty_first_mode_then_a_low_budget_waits_instead_of_unreadable(self):
        project_id, page_ids = self.photos(1)
        with patch.object(application, "vision_budget_low", side_effect=[False, True, True, True]):
            response, mocked = self.recognize(project_id, page_ids[0], [EMPTY] * 3)
        self.assertEqual(mocked.call_count, 1, "the budget check stops the second image mode")
        self.assertEqual((response.status_code, response.get_json()["state"]), (202, "waiting"))
        with application.app.app_context():
            page = fetch(application.ProjectPage, page_ids[0])
            self.assertEqual(page.extraction_status, "waiting")
            self.assertEqual(json.loads(page.recognition_json)["waiting_reason"], "hourly_limit")

    def test_a_free_account_low_on_hourly_requests_waits_before_any_call(self):
        project_id, page_ids = self.photos(2)
        with patch.object(application.ai_service, "remaining_hourly_requests", return_value=3) as budget:
            response, mocked = self.recognize(project_id, page_ids[0], [reading("nie gelesen")])
        self.assertTrue(budget.called)
        self.assertEqual(mocked.call_count, 0, "the request that would trip the cap is never sent")
        self.assertEqual((response.status_code, response.get_json()["state"]), (202, "waiting"))
        self.assertIn("hourly", response.get_json()["message"])
        with application.app.app_context():
            page = fetch(application.ProjectPage, page_ids[0])
            self.assertEqual((page.extraction_status, page.retry_count), ("waiting", 0))
            self.assertEqual(json.loads(page.recognition_json)["waiting_reason"], "hourly_limit")
        # With requests to spare the same page is read normally, and its old notice is gone.
        with patch.object(application.ai_service, "remaining_hourly_requests", return_value=40):
            response, mocked = self.recognize(project_id, page_ids[0], [reading("Jetzt gelesen")])
        self.assertEqual((response.status_code, mocked.call_count), (200, 1))
        with application.app.app_context():
            page = fetch(application.ProjectPage, page_ids[0])
            self.assertEqual(page.extraction_status, "ready")
            self.assertNotIn("paused", page.warning)


class FallbackTests(Base):
    def test_vision_reading_nothing_falls_back_to_the_text_layer(self):
        self.upload([("titel.pdf", text_pdf(SPARSE), "application/pdf")])
        with application.app.app_context():
            project = self.project()
            project_id, page_id = project.id, self.pages(project.id)[0].id
        response, mocked = self.recognize(project_id, page_id, [EMPTY] * 3)
        self.assertEqual((response.status_code, mocked.call_count), (200, 3))
        self.assertEqual(response.get_json()["text_source"], "text_layer")

    def test_the_offline_ocr_reads_a_photo_the_vision_model_could_not(self):
        self.upload([("tafel.png", png("Tafelbild"), "image/png")])
        with application.app.app_context():
            project = self.project()
            project_id, page_id = project.id, self.pages(project.id)[0].id
        offline = LocalOcrResult(lines=[
            Line("Die Wuerde des Menschen ist unantastbar.", (0.1, 0.1, 0.9, 0.15), 0.93),
            Line("Artikel 1 Grundgesetz", (0.1, 0.2, 0.6, 0.25), 0.88)], width=900, height=1200,
            engine="rapidocr", duration_ms=5)
        invalid = AIValidationError("schema_validation", "response was empty")
        application.app.config["PROJECT_LOCAL_OCR_IN_TESTS"] = True
        try:
            with patch.object(application.local_ocr, "available", return_value=True), \
                    patch.object(application.local_ocr, "recognize", return_value=offline) as engine:
                response, mocked = self.recognize(project_id, page_id, side_effect=[invalid] * 3)
        finally:
            application.app.config.pop("PROJECT_LOCAL_OCR_IN_TESTS", None)
        self.assertEqual(engine.call_count, 1)
        self.assertEqual(mocked.call_count, 3, "a validation error is about this answer, so every mode is tried")
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"], body["text_source"]), (200, "needs_review", "local_ocr"))
        self.assertEqual(body["file"]["reading_message"], "Document 1: Needs OCR review")
        with application.app.app_context():
            page = fetch(application.ProjectPage, page_id)
            self.assertEqual((page.extraction_status, page.confidence_status), ("ready", "review"))
            self.assertIn("Wuerde des Menschen", page.extracted_text)
            self.assertIn("offline text recognition", page.warning)
            self.assertLess(page.recognition_confidence, 0.8, "never presented as a confident reading")

    def test_without_any_fallback_the_page_fails_as_before(self):
        self.upload([("leer.png", png(), "image/png")])
        with application.app.app_context():
            project = self.project()
            project_id, page_id = project.id, self.pages(project.id)[0].id
        response, _ = self.recognize(project_id, page_id, side_effect=[AIValidationError("schema_validation", "x")] * 3)
        self.assertEqual((response.status_code, response.get_json()["state"]), (422, "unreadable"))
        with application.app.app_context():
            self.assertEqual(fetch(application.ProjectPage, page_id).extraction_status, "failed")


class RereadingTests(Base):
    def two_pages_one_failed(self):
        self.upload([("gut.png", png("gut"), "image/png"), ("kaputt.png", png("kaputt"), "image/png")])
        with application.app.app_context():
            project = self.project()
            project_id = project.id
            good, broken = [page.id for page in self.pages(project_id)]
        self.recognize(project_id, good, [reading("Pflicht ist die Notwendigkeit einer Handlung")])
        response, _ = self.recognize(project_id, broken, side_effect=[AIValidationError("schema_validation", "x")] * 3)
        self.assertEqual(response.status_code, 422)
        return project_id, good, broken

    def test_quick_start_never_rereads_a_failed_or_a_read_page(self):
        project_id, _good, _broken = self.two_pages_one_failed()
        with patch.object(application, "create_response", side_effect=AssertionError("no model call")) as mocked, \
                patch.object(application, "ensure_project_sections", return_value=[object()]):
            started = self.client.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual(mocked.call_count, 0, "a failed page waits for the student's Retry")
        self.assertEqual(started.status_code, 200, started.get_data(as_text=True))
        self.assertTrue(started.get_json()["review"])
        start = self.client.get(f"/projects/{project_id}/start?auto=1").get_data(as_text=True)
        self.assertIn('data-page-ids="[]"', start, "the start page does not read them again either")

    def test_a_read_page_is_not_read_again_unless_the_student_asks(self):
        project_id, good, broken = self.two_pages_one_failed()
        response, mocked = self.recognize(project_id, good, side_effect=AssertionError("no model call"))
        self.assertEqual((response.status_code, mocked.call_count), (200, 0))
        self.assertTrue(response.get_json()["already_read"])
        response, mocked = self.recognize(project_id, good, [reading("Neu gelesen", 0.95)], body={"retry": True})
        self.assertEqual((response.status_code, mocked.call_count), (200, 1))
        # The explicit Retry of the failed page is what reads it again.
        response, mocked = self.recognize(project_id, broken, [reading("Jetzt lesbar")], body={"retry": True})
        self.assertEqual((response.status_code, mocked.call_count), (200, 1))

    def test_quick_start_reads_waiting_and_stuck_pages_but_not_freshly_waiting_ones(self):
        self.upload([("a.png", png("a"), "image/png"), ("b.png", png("b"), "image/png")])
        with application.app.app_context():
            project = self.project()
            project_id = project.id
            first, second = self.pages(project_id)
            old = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            first.extraction_status = "waiting"
            first.recognition_json = json.dumps({"waiting_since": old, "waiting_reason": "provider_busy"})
            second.extraction_status = "recognizing"         # a request that died mid-read
            second.recognition_json = json.dumps({"recognizing_since": old})
            db.session.commit()
            self.assertTrue(application.page_reading_due(first) and application.page_reading_due(second))
        report = self.report(project_id)
        self.assertEqual([file["reading_state"] for file in report["files"]], ["waiting", "pending"],
                         "a stuck page is 'not read yet', never processing forever")
        with patch.object(application, "create_response",
                          side_effect=[reading("Seite a"), reading("Seite b")]) as mocked, \
                patch.object(application, "ensure_project_sections", return_value=[object()]):
            started = self.client.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual((started.status_code, mocked.call_count), (200, 2))
        with application.app.app_context():
            self.assertEqual({page.extraction_status for page in self.pages(project_id)}, {"ready"})

        with application.app.app_context():
            page = self.pages(project_id)[0]
            page.extraction_status = "waiting"
            page.recognition_json = json.dumps({"waiting_since": datetime.now(timezone.utc).isoformat()})
            db.session.commit()
        with patch.object(application, "create_response", side_effect=AssertionError("no call")) as mocked, \
                patch.object(application, "ensure_project_sections", return_value=[object()]):
            self.client.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual(mocked.call_count, 0, "a page that waited a minute ago is not knocked on again")


def scanned_pdf_with_text_layer(text):
    """A scanner app's page: one full-page picture plus the OCR text layer of its print."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
    page.insert_image(page.rect, stream=png("Handschrift", "#f4f1ea", (850, 1100)))
    page.insert_textbox(pymupdf.Rect(60, 60, 540, 200), text, fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def faint_scan_pdf(grey, lines, *, noise=0, gradient=False):
    """A scanned A4 page at 150 dpi (one JPEG picture, no text layer): paper at grey 250
    with `lines` written in `grey` (None: nothing written), optional sensor noise or a
    shading gradient across the sheet."""

    from PIL import ImageChops, ImageFont
    width, height = 1240, 1754
    image = (Image.linear_gradient("L").resize((width, height)).point([200 + value * 50 // 255 for value in range(256)])
             if gradient else Image.new("L", (width, height), 250))
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 22)
    except OSError:
        font = ImageFont.load_default(size=22)
    for index, line in enumerate(lines):
        draw.text((150, 200 + 40 * index), line, fill=grey, font=font)
    if noise:
        # Gaussian sensor noise around 128, added back as a signed offset.
        image = ImageChops.add(image, Image.effect_noise((width, height), noise), 1.0, -128)
    output = io.BytesIO()
    image.convert("RGB").save(output, format="JPEG", quality=75)
    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    page = document.new_page(width=595, height=842)  # pyright: ignore[reportAttributeAccessIssue]
    page.insert_image(page.rect, stream=output.getvalue())
    data = document.tobytes()
    document.close()
    return data


class AuditFixTests(Base):
    """Spec audit (FIX1): honest page states for scans, blank pages, retakes and retries."""

    def one_page(self, name, data, mime="application/pdf"):
        self.upload([(name, data, mime)])
        with application.app.app_context():
            project = self.project()
            return project.id, [page.id for page in self.pages(project.id)]

    def test_a_rate_limited_scan_waits_for_vision_instead_of_keeping_only_its_text_layer(self):
        # P1-03: a scanned worksheet with a printed header and handwriting. The header's
        # text layer used to be stored as the page ("read"), and vision never read the
        # handwriting again.
        project_id, (page_id,) = self.one_page("blatt.pdf", scanned_pdf_with_text_layer(SPARSE))
        response, mocked = self.recognize(project_id, page_id, side_effect=rate_limited)
        self.assertEqual((response.status_code, mocked.call_count), (202, 1))
        self.assertEqual(response.get_json()["state"], "waiting")
        with application.app.app_context():
            self.assertEqual(fetch(application.ProjectPage, page_id).extraction_status, "waiting")
        response, _ = self.recognize(project_id, page_id, [reading(SPARSE + "\nPflicht heisst: aus Achtung handeln")])
        self.assertEqual((response.status_code, response.get_json()["state"]), (200, "read"))
        self.assertEqual(response.get_json()["text_source"], "vision")

    def test_a_scans_text_layer_fallback_needs_review_never_read(self):
        # Concern c: when vision gives nothing at all, the text layer is still used, but a
        # scan's layer lacks the handwriting: "Needs OCR review", not "read".
        project_id, (page_id,) = self.one_page("blatt.pdf", scanned_pdf_with_text_layer(SPARSE))
        response, _ = self.recognize(project_id, page_id, [EMPTY] * 3)
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"], body["text_source"]), (200, "needs_review", "text_layer"))
        self.assertEqual(body["file"]["reading_message"], "Document 1: Needs OCR review")
        with application.app.app_context():
            self.assertIn("Handwriting on it is missing", fetch(application.ProjectPage, page_id).warning)

    def test_a_blank_back_page_costs_no_request_and_does_not_block_the_project(self):
        # P1-05: the empty back of a sheet cost three vision calls, said "rescan it", and
        # kept the document "partly analysed".
        project_id, (first, blank) = self.one_page("blatt.pdf", text_pdf(UTILITARISMUS, ""))
        self.recognize(project_id, first)
        response, mocked = self.recognize(project_id, blank, side_effect=AssertionError("no vision call"))
        self.assertEqual(mocked.call_count, 0)
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"]), (200, "read"))
        self.assertIn("is blank", body["message"])
        report = self.report(project_id)
        self.assertEqual(report["files"][0]["reading_state"], "read")
        self.assertEqual(report["files"][0]["blank_page_numbers"], [2])
        self.assertEqual(report["files"][0]["unreadable_page_numbers"], [])
        self.assertEqual(report["summary"], "1 of 1 documents read")
        with application.app.app_context():
            project = fetch(application.LearningProject, project_id)
            project.analysis_json = json.dumps({"analysis_version": 4, "analysed_page_ids": [first],
                                                "failed_page_ids": [], "not_analysed_page_ids": []})
            db.session.add(application.LearningSection(project_id=project_id, position=1, title="Utilitarismus",
                                                       source_page_ids_json=json.dumps([first])))
            db.session.commit()
        report = self.report(project_id)
        self.assertTrue(report["complete"], report["issues"])
        self.assertEqual(report["files"][0]["state"], "analysed")
        # "Try reading again" still asks vision: the student knows better.
        response, mocked = self.recognize(project_id, blank, [reading("Bleistiftnotiz am Rand")], body={"retry": True})
        self.assertEqual((response.status_code, mocked.call_count), (200, 1))

    def test_a_retaken_page_stays_in_its_document(self):
        # P1-06: three 2-page PDFs, page 2 of the first retaken: still three documents,
        # numbered as before, and the page stays "ethik-0.pdf p. 2".
        self.upload([(f"ethik-{n}.pdf", text_pdf(UTILITARISMUS + f" Blatt {n}a", KANT + f" Blatt {n}b"),
                      "application/pdf") for n in range(3)])
        with application.app.app_context():
            project_id = self.project().id
            ids = [page.id for page in self.pages(project_id)]
        for page_id in ids:
            self.recognize(project_id, page_id)
        before = [(file["label"], file["filename"], file["pages_total"]) for file in self.report(project_id)["files"]]
        retake = self.client.post(f"/projects/{project_id}/pages/{ids[1]}/replace", data={
            "next": "workspace", f"replacement_{ids[1]}": (io.BytesIO(png("neu")), "neu.png", "image/png")},
            content_type="multipart/form-data")
        self.assertEqual(retake.status_code, 302)
        report = self.report(project_id)
        self.assertEqual([(file["label"], file["filename"], file["pages_total"]) for file in report["files"]], before)
        with application.app.app_context():
            page = fetch(application.ProjectPage, ids[1])
            self.assertEqual((page.source_file.original_filename, page.page_number, page.extraction_status),
                             ("ethik-0.pdf", 2, "pending"))
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.ProjectFile.id))), 3)
        # The retake is read by vision (not the old page's text layer) and stays retaken.
        response, mocked = self.recognize(project_id, ids[1], [reading("Neu fotografierte Seite")])
        self.assertEqual((response.status_code, mocked.call_count), (200, 1))
        with application.app.app_context():
            page = fetch(application.ProjectPage, ids[1])
            self.assertEqual(page.extracted_text, "Neu fotografierte Seite")
            self.assertTrue(application.page_replacement(page))
        original = self.client.get(f"/projects/{project_id}/pages/{ids[1]}/image/original")
        self.assertEqual(original.mimetype, "image/png", "the retake is the page's own picture now")

    def test_try_reading_again_keeps_the_reading_resets_the_waits_and_comes_back(self):
        # P1-09: the workspace's "Try reading again".
        self.upload([("a.png", png("a"), "image/png")])
        with application.app.app_context():
            project_id = self.project().id
            (page_id,) = [page.id for page in self.pages(project_id)]
        self.recognize(project_id, page_id, [reading("Pflicht und Neigung")])
        with patch.object(application, "create_response", side_effect=rate_limited):
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/retry", data={"next": "workspace"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith(f"/projects/{project_id}#materials"))
        with application.app.app_context():
            page = fetch(application.ProjectPage, page_id)
            self.assertEqual((page.extraction_status, page.extracted_text), ("ready", "Pflicht und Neigung"),
                             "a failed re-read keeps the reading")
            page.extraction_status, page.retry_count = "failed", application.MAX_PROVIDER_WAITS
            db.session.commit()
        with patch.object(application, "create_response", side_effect=rate_limited):
            self.client.post(f"/projects/{project_id}/pages/{page_id}/retry", data={"next": "start"})
        with application.app.app_context():
            page = fetch(application.ProjectPage, page_id)
            self.assertEqual(page.extraction_status, "waiting", "an explicit retry starts the provider waits again")
        self.client.post("/settings/language", data={"language": "de"})
        with patch.object(application, "create_response", side_effect=[reading("Jetzt gelesen")]):
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/retry",
                                        data={"next": "https://evil.example/"}, follow_redirects=True)
        page = response.get_data(as_text=True)
        self.assertIn("/review", response.request.path, "an unknown target falls back to the review page")
        self.assertNotIn("is ready for review", page, "the message is translated, not an English f-string")

    def test_a_damaged_pdf_is_named_in_the_students_language_and_the_valid_file_is_kept(self):
        # P1-15: the f-string errors ("Could not read PDF x: EOF marker not found") could
        # never match a catalogue entry and stayed English, with the parser's words.
        # PF1 (DR-01): the valid file is no longer thrown away with the damaged one; the
        # damaged file is its own document row with no pages.
        self.client.post("/settings/language", data={"language": "de"})
        response = self.upload([("gut.png", png("gut"), "image/png"),
                                ("kaputt.pdf", b"%PDF-1.4 kaputt", "application/pdf")])
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("kaputt.pdf", page)
        self.assertNotIn("EOF", page)
        self.assertNotIn("Could not read PDF", page, "no English f-string with the parser's words")
        self.assertIn("„kaputt.pdf“ konnte nicht als PDF gelesen werden", page.replace("&#34;", '"'),
                      "translated, with the file's name")
        with application.app.app_context():
            files = db.session.scalars(db.select(application.ProjectFile).order_by(application.ProjectFile.id)).all()
            self.assertEqual([(item.original_filename, len(item.pages)) for item in files],
                             [("gut.png", 1), ("kaputt.pdf", 0)])
        response = self.upload([("leer.png", b"", "image/png")])
        self.assertNotIn("Empty files cannot be uploaded.", response.get_data(as_text=True))

    def test_page_status_messages_are_in_the_students_language(self):
        # P1-17: the unreadable-page advice was hardcoded English inside a German row.
        self.client.post("/settings/language", data={"language": "de"})
        project_id, (page_id,) = self.one_page("tafel.png", png("Tafel"), "image/png")
        response, _ = self.recognize(project_id, page_id, [EMPTY] * 3)
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"]), (422, "unreadable"))
        self.assertNotIn("We tried enhanced", body["message"])
        self.assertIn("Graustufen", body["message"])

    def test_a_scan_with_a_faint_pencil_note_is_never_skipped_as_blank(self):
        # FIXR review: the blank check skipped vision for a scanned page whose only
        # content was light pencil (grey 205 on paper 250): its handwriting was stored as
        # "blank - nothing to learn on it" and never read.
        project_id, (page_id,) = self.one_page("notiz.pdf", faint_scan_pdf(205, ["Hoehlengleichnis Platon"]))
        response, mocked = self.recognize(project_id, page_id, [reading("Höhlengleichnis Platon")])
        self.assertEqual(mocked.call_count, 1, "a faint pencil note goes to vision")
        self.assertEqual((response.status_code, response.get_json()["state"]), (200, "read"))
        with application.app.app_context():
            self.assertNotEqual(json.loads(fetch(application.ProjectPage, page_id).recognition_json).get("text_source"),
                                "blank")

    def test_blank_scans_with_noise_or_shading_still_cost_no_request(self):
        from learnova.ocr.service import image_is_blank, render_pdf_page
        for label, data in (("empty", faint_scan_pdf(None, [])), ("noise", faint_scan_pdf(None, [], noise=8)),
                            ("shading", faint_scan_pdf(None, [], gradient=True))):
            self.assertTrue(image_is_blank(render_pdf_page(data, 0)), label)
        for grey in (160, 190, 205, 215):
            self.assertFalse(image_is_blank(render_pdf_page(faint_scan_pdf(grey, ["Hoehlengleichnis Platon"]), 0)),
                             grey)

    def test_a_small_hourly_cap_still_lets_pages_be_read(self):
        # Concern g: with AI_MAX_REQUESTS_PER_USER_HOUR below 4 the fixed threshold made
        # every page wait forever.
        previous = application.app.config.get("AI_MAX_REQUESTS_PER_USER_HOUR")
        application.app.config["AI_MAX_REQUESTS_PER_USER_HOUR"] = 3
        try:
            project_id, (page_id,) = self.one_page("a.png", png("a"), "image/png")
            with patch.object(application.ai_service, "remaining_hourly_requests", return_value=3):
                response, mocked = self.recognize(project_id, page_id, [reading("gelesen")])
            self.assertEqual((response.status_code, mocked.call_count), (200, 1))
            with patch.object(application.ai_service, "remaining_hourly_requests", return_value=0):
                response, mocked = self.recognize(project_id, page_id, [], body={"retry": True})
            self.assertEqual(mocked.call_count, 0, "an exhausted budget still waits")
        finally:
            application.app.config["AI_MAX_REQUESTS_PER_USER_HOUR"] = previous


class AccessAndUploadTests(Base):
    def test_the_analysis_route_is_the_owners_only(self):
        self.upload([("eins.png", png("eins"), "image/png")])
        with application.app.app_context():
            project_id = self.project().id
        self.assertEqual(self.report(project_id)["documents_uploaded"], 1)
        other = self.register("mallory")
        self.assertEqual(other.get(f"/projects/{project_id}/analysis").status_code, 404)
        anonymous = application.app.test_client()
        self.assertEqual(anonymous.get(f"/projects/{project_id}/analysis").status_code, 302)
        with application.app.app_context():
            page_id = self.pages(project_id)[0].id
        self.assertEqual(other.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={}).status_code, 404)

    def test_the_form_offers_every_subject_and_says_the_limits(self):
        html = self.client.get("/projects").get_data(as_text=True)
        for subject in SUBJECT_CHOICES:
            self.assertIn(f'<option value="{subject}">', html)
        self.assertIn("Up to 40 pages per project, 15 MB per file.", html)

    def test_typed_subjects_are_stored_by_their_canonical_name(self):
        for chosen, typed, expected in (("Other", "Ethik", "Ethics"), ("Other", "NwT", "Science and Technology"),
                                        ("Physics", "ignored", "Physics"), ("Other", "Astronomie", "Astronomie"),
                                        ("Other", "", "Other"), ("Mathe", "", "Mathematics")):
            self.client.post("/projects", data={"title": f"T {chosen} {typed}", "subject": chosen,
                                                "subject_other": typed, "topics": "Thema"})
            with application.app.app_context():
                self.assertEqual(self.project().subject, expected, (chosen, typed))

    def test_over_the_page_limit_names_the_file_and_keeps_what_fits(self):
        # PF1 (DR-01): the files before the one that crosses the limit are kept; that file
        # is a failed document row. A single file over the limit still saves nothing.
        application.app.config["PROJECT_MAX_PAGES"] = 2
        try:
            response = self.upload([(f"seite-{index}.png", png(str(index)), "image/png") for index in range(3)])
            self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
            page = self.client.get(response.headers["Location"]).get_data(as_text=True)
            self.assertIn("“seite-2.png” would take this project over 2 pages.", page)
            with application.app.app_context():
                files = db.session.scalars(db.select(application.ProjectFile).order_by(application.ProjectFile.id)).all()
                self.assertEqual([len(item.pages) for item in files], [1, 1, 0])
            response = self.upload([("drei.pdf", text_pdf(KANT, UTILITARISMUS, SPARSE), "application/pdf")],
                                   title="Zu lang")
        finally:
            application.app.config["PROJECT_MAX_PAGES"] = 40
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("None of the files could be used, so nothing was saved.", page)
        self.assertIn("“drei.pdf” would take this project over 2 pages.", page)
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.LearningProject.id))), 1)
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.ProjectFile.id))), 3)

    def test_a_kompetenzraster_of_duplicates_is_said_not_a_crash(self):
        notes = png("Notizen")
        response = self.client.post("/projects", data={
            "title": "Bio", "subject": "Biology",
            "materials": [(io.BytesIO(notes), "notizen.png", "image/png")],
            "competency_grid": [(io.BytesIO(notes), "raster.png", "image/png")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        with application.app.app_context():
            project = self.project()
            self.assertEqual(len(project.files), 1)
            self.assertEqual([page.teacher_highlighted for page in project.pages], [False])
            self.assertEqual(json.loads(project.analysis_json)["skipped_duplicate_files"], ["raster.png"])
        self.assertIn("“raster.png” was uploaded twice", self.client.get("/projects").get_data(as_text=True))

    def test_adding_only_files_already_in_the_project_is_translated_and_said(self):
        notes = png("Notizen")
        self.upload([("notizen.png", notes, "image/png")])
        with application.app.app_context():
            project_id = self.project().id
        response = self.client.post(f"/projects/{project_id}/pages/add", data={
            "materials": [(io.BytesIO(notes), "nochmal.png", "image/png")]}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        page = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("Every file you chose is already in this project, so no pages were added.", page)
        self.assertNotIn("No unique supported pages were uploaded", page)


class GatewayHelperTests(unittest.TestCase):
    """The read-only budget helper and the JSON-parsing fix in the gateway."""

    def setUp(self):
        self.saved = {key: application.app.config.get(key) for key in (
            "AI_ENFORCE_LIMITS", "AI_MAX_REQUESTS_PER_USER_HOUR", "AI_MAX_REQUESTS_PER_USER_DAY")}
        self.ledger = ai_service.usage_ledger()
        self.store = ai_service._USER_COOLDOWN_STORE
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            db.create_all()     # the usage ledger writes its rows to the database

    def tearDown(self):
        application.app.config.update(self.saved)
        ai_service.set_usage_ledger(self.ledger)
        ai_service._USER_COOLDOWN_STORE = self.store

    def test_remaining_requests_are_read_from_the_same_ledger_without_counting(self):
        class Ledger(ai_service.UsageLedger):
            asked: list = []

            def count_requests(self, *, user_reference, since):
                self.asked.append(user_reference)
                hours = (datetime.now(timezone.utc) - since).total_seconds() / 3600
                return 57 if hours <= 1.01 else 120

        ledger = Ledger()
        ai_service.set_usage_ledger(ledger)
        application.app.config.update(AI_ENFORCE_LIMITS=True, AI_MAX_REQUESTS_PER_USER_HOUR=60,
                                      AI_MAX_REQUESTS_PER_USER_DAY=250)
        with application.app.app_context():
            self.assertEqual(ai_service.remaining_hourly_requests(7), 3)
            self.assertEqual(ai_service.remaining_hourly_requests("7"), 3, "the same reference as the gateway's")
            reference = ai_service._anonymous_reference(7, label="user")
            self.assertEqual(set(ledger.asked), {reference})
            application.app.config["AI_MAX_REQUESTS_PER_USER_DAY"] = 121
            self.assertEqual(ai_service.remaining_hourly_requests(7), 1, "a free account's daily cap counts too")
            self.assertEqual(ai_service.remaining_hourly_requests(7, premium=True), 3)
            self.assertIsNone(ai_service.remaining_hourly_requests(None))
            ai_service.set_user_cooldown_store(
                lambda ref: datetime.now(timezone.utc) + timedelta(hours=2), lambda ref, until: True)
            self.assertEqual(ai_service.remaining_hourly_requests(7), 0, "nothing is left during a cooldown")
            application.app.config["AI_ENFORCE_LIMITS"] = False
            ai_service._USER_COOLDOWN_STORE = None
            self.assertIsNone(ai_service.remaining_hourly_requests(7), "no cap applies under the test runner")

    def test_output_json_cannot_crash_the_gateway(self):
        for hostile in ("[" * 50000 + "]" * 50000, "{\"blocks\": " + "1" * 5000 + "}"):
            with self.assertRaises(AIValidationError) as caught:
                ai_service._validate_output("ocr_document_recognition", hostile, "v", {}, max_characters=10_000_000)
            self.assertEqual(caught.exception.category, "invalid_json")
            self.assertIsNotNone(caught.exception.report)

    def test_the_corrective_retry_runs_after_unparseable_output(self):
        changed = ("TESTING", "RUN_LIVE_AI_TEST", "AI_USAGE_PATH", "ALLOW_LIVE_AI", "AI_CACHE_DIR", "AI_MODE")
        saved = {key: application.app.config.get(key) for key in changed}
        application.app.config.update(TESTING=True, RUN_LIVE_AI_TEST=True, AI_USAGE_PATH="", ALLOW_LIVE_AI=True,
                                      AI_CACHE_DIR=tempfile.mkdtemp(), AI_MODE="live")

        def answer(text):
            class Response:
                output_text = text
                model = "m"

                class usage:  # noqa: N801
                    input_tokens = 10
                    output_tokens = 10
                    total_tokens = 20
            return Response()

        valid = json.dumps({"blocks": [{"type": "printed_text", "content": "Pflicht", "confidence": 0.9,
                                        "bbox": [0.1, 0.1, 0.5, 0.2]}], "detected_page_number": "", "warning": ""})
        try:
            with application.app.test_request_context("/"):
                with patch.object(ai_service, "_provider_response",
                                  side_effect=[answer("[" * 50000 + "]" * 50000), answer(valid)]) as provider, \
                        patch.object(ai_service, "_assert_usage_limits"):
                    ai_service._RECENT_RESULTS.clear()
                    response = ai_service.create_response(
                        task_type="ocr_document_recognition", language="German", model="m",
                        instructions="read the page", input="hostile page", max_output_tokens=100,
                        private_scope=None)
        finally:
            application.app.config.update(saved)
        self.assertEqual(provider.call_count, 2, "the deep nesting bought the corrective retry, not a crash")
        self.assertIn("Pflicht", response.output_text)


class MigrationTests(unittest.TestCase):
    """037_project_teaching_engine on a database from before it: columns added, data kept."""

    NEW = {"learning_section": ("topic_map_json", "coverage_json"),
           "flashcard_set": ("project_id",),
           "flashcard": ("origin_key", "topic_key", "source_page_ids_json", "reverse_answer")}

    @staticmethod
    def legacy_ddl(table_name, drop):
        from sqlalchemy.schema import CreateTable

        engine = db.engine
        ddl = str(CreateTable(db.metadata.tables[table_name]).compile(engine))
        kept = [line for line in ddl.split("\n")
                if not any(re.search(rf"\b{name}\b", line) for name in drop)]
        return re.sub(r",\s*\)\s*$", "\n)", "\n".join(kept).rstrip())

    @staticmethod
    def legacy_insert(connection, table_name, drop, **values):
        """A raw INSERT with every NOT NULL column filled (Core would add the new columns' defaults)."""

        row = {}
        for column in db.metadata.tables[table_name].columns:
            if column.name in drop:
                continue
            if column.name in values:
                row[column.name] = values[column.name]
                continue
            if column.nullable or column.primary_key:
                continue
            try:
                python_type = column.type.python_type
            except NotImplementedError:
                python_type = str
            row[column.name] = {int: 0, float: 0.0, bool: False, bytes: b"", str: "", Decimal: 0}.get(
                python_type, "2025-01-01 00:00:00")
        names = ", ".join(f'"{name}"' for name in row)
        marks = ", ".join(f":{name}" for name in row)
        connection.execute(application.text(f'INSERT INTO "{table_name}" ({names}) VALUES ({marks})'), row)

    def test_old_rows_survive_and_a_second_run_changes_nothing(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            db.drop_all()
            engine = db.engine
            try:
                with engine.begin() as connection:
                    for table_name in ("user", "learning_project", "learning_section", "flashcard_set", "flashcard"):
                        connection.execute(application.text(self.legacy_ddl(table_name, self.NEW.get(table_name, ()))))
                    self.legacy_insert(connection, "user", (), id=1, username="old", email="old@example.com",
                                       password_hash="x", preferred_language="de")
                    self.legacy_insert(connection, "learning_project", (), id=1, user_id=1, title="Ethik",
                                       subject="Ethik", analysis_json="{}")
                    self.legacy_insert(connection, "learning_section", self.NEW["learning_section"], id=1,
                                       project_id=1, position=1, title="Kant", source_page_ids_json="[3]")
                    self.legacy_insert(connection, "flashcard_set", self.NEW["flashcard_set"], id=1, user_id=1,
                                       title="Meine Karten", subject="Ethics")
                    for card_id in (1, 2):
                        self.legacy_insert(connection, "flashcard", self.NEW["flashcard"], id=card_id, set_id=1,
                                           front=f"Frage {card_id}", back="Antwort", position=card_id)
                db.session.remove()
                application.ensure_database()
                inspector = application.inspect(engine)
                for table_name, names in self.NEW.items():
                    self.assertTrue(set(names) <= {column["name"] for column in inspector.get_columns(table_name)})
                indexes = {index["name"] for index in inspector.get_indexes("flashcard")}
                self.assertIn("uq_flashcard_origin", indexes)
                self.assertIn("ix_flashcard_set_project_id",
                              {index["name"] for index in inspector.get_indexes("flashcard_set")})
                self.assertIsNotNone(fetch(application.SchemaMigration, "037_project_teaching_engine"))
                section = fetch(application.LearningSection, 1)
                assert section is not None
                self.assertEqual((section.title, section.source_page_ids_json, section.topic_map_json,
                                  section.coverage_json), ("Kant", "[3]", "{}", "{}"))
                card_set = fetch(application.FlashcardSet, 1)
                assert card_set is not None
                self.assertEqual((card_set.title, card_set.project_id), ("Meine Karten", None))
                card = fetch(application.Flashcard, 1)
                assert card is not None
                self.assertEqual((card.front, card.origin_key, card.topic_key, card.source_page_ids_json,
                                  card.reverse_answer), ("Frage 1", "", "", "[]", ""))
                # Student-made cards carry no key and never collide; a synced key exists once per set.
                card.origin_key = "kant:term:pflicht"
                db.session.commit()
                twin = fetch(application.Flashcard, 2)
                assert twin is not None
                twin.origin_key = "kant:term:pflicht"
                with self.assertRaises(application.IntegrityError):
                    db.session.commit()
                db.session.rollback()
                application.ensure_database()          # idempotent: a second run is a no-op
                self.assertEqual(fetch(application.Flashcard, 1).origin_key, "kant:term:pflicht")
                self.assertEqual(db.session.scalar(db.select(db.func.count(application.SchemaMigration.version)).where(
                    application.SchemaMigration.version == "037_project_teaching_engine")), 1)
                calls = re.findall(r'apply_schema_migration\(\s*"037_project_teaching_engine"',
                                   (Path(application.__file__)).read_text(encoding="utf-8"))
                self.assertEqual(len(calls), 2, "the add-it and the already-there branch")
            finally:
                db.session.remove()
                db.drop_all()
                db.create_all()


if __name__ == "__main__":
    unittest.main()

import inspect
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from PIL import Image, ImageDraw
from werkzeug.datastructures import FileStorage


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_document_processing_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402
import pymupdf  # noqa: E402  # pyright: ignore[reportMissingImports]
from document_processing import (  # noqa: E402
    normalize_recognition,
    preprocess_document_image,
    validate_document_upload,
)
from learnova.config import DEFAULT_PROJECT_MAX_PAGES, configure_app  # noqa: E402
from learnova.ocr.service import pdf_page_pictures  # noqa: E402
from learnova.uploads import (  # noqa: E402
    NoNewPagesError,
    ProjectPageLimitError,
    UploadResult,
    add_uploads_to_project,
    create_project_from_uploads,
    create_project_with_uploads,
    ingest_uploads,
)


FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "recognition_cases.json").read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


def page_image(label, size=(1200, 1600), background="white"):
    image = Image.new("RGB", size, background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((45, 45, size[0] - 45, size[1] - 45), outline="black", width=4)
    draw.text((90, 110), label, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def payload(*fixture_names):
    blocks = []
    for index, name in enumerate(fixture_names):
        item = dict(FIXTURES[name])
        item.update({
            "bbox": [0.05, 0.05 + index * 0.1, 0.95, 0.14 + index * 0.1],
            "crossed_out": False,
            "important_candidate": bool(item.pop("teacher_highlight_candidate", False)),
            "teacher_highlight_candidate": name == "highlighted_text",
            "nearby_text": item.get("nearby_text", ""),
        })
        blocks.append(item)
    return {"blocks": blocks, "detected_page_number": "7", "warning": ""}


class DocumentServiceTests(unittest.TestCase):
    def test_validation_rotation_formula_confidence_and_no_temporary_files(self):
        original = page_image("x² + √x; H₂O; Ca²⁺", size=(1200, 800))
        self.assertEqual(validate_document_upload(original, "formula.png", "image/png"), "image/png")
        with self.assertRaises(ValueError):
            validate_document_upload(b"<script>malicious</script>", "notes.png", "image/png")
        # SQLite may lazily create an etilqs_* database scratch file while the
        # full suite shares an engine; this assertion targets image temp files.
        before = {
            item for item in Path(tempfile.gettempdir()).iterdir()
            if not item.name.startswith("etilqs_")
        }
        processed = preprocess_document_image(original, {"rotation": 90, "crop": {"left": 0.05}})
        after = {
            item for item in Path(tempfile.gettempdir()).iterdir()
            if not item.name.startswith("etilqs_")
        }
        self.assertGreater(processed.height, processed.width)
        self.assertEqual(before, after)
        normalized = normalize_recognition(payload(
            "printed_notes", "difficult_handwriting", "formula", "highlighted_text", "diagram"
        ))
        self.assertIn("2H₂ + O₂ → 2H₂O", normalized["text"])
        self.assertEqual(normalized["formulas"][0]["content"], "2H₂ + O₂ → 2H₂O")
        self.assertTrue(any(item["confidence_status"] == "unclear" for item in normalized["blocks"]))
        self.assertEqual(normalized["diagrams"][0]["content"], "cell wall; nucleus; vacuole")


class DocumentWorkflowTests(unittest.TestCase):
    def setUp(self):
        # These tests drive the upload -> recognise -> review -> correct -> ownership
        # workflow with a hand-built sequence of page responses. The handwriting second
        # look adds a close-up call after any page that came back uncertain, which would
        # consume entries from that sequence and obscure what is being tested here; it
        # has its own end-to-end coverage in tests/test_handwriting_second_look.py.
        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "FEATURE_HANDWRITING_SECOND_LOOK")
        application.app.config.update(TESTING=True, FEATURE_HANDWRITING_SECOND_LOOK=False)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "alice", "email": "alice@example.com", "password": "correct-horse-battery",
        })

    def create_combined_project(self):
        response = self.client.post("/projects", data={
            "title": "Mixed notes", "subject": "Chemistry",
            "materials": [(io.BytesIO(page_image("Printed CO₂", background="#fffde8")), "print.png", "image/png")],
            "camera_scans": [
                (io.BytesIO(page_image("Handwritten H₂O")), "camera-page-1.png", "image/png"),
                (io.BytesIO(page_image("Diagram labels", background="#eef8ff")), "camera-page-2.png", "image/png"),
            ],
            "scan_metadata": json.dumps([
                {"rotation": 90, "crop": {"left": 0.02, "right": 0.02}},
                {"rotation": 0, "crop": {}},
            ]),
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        # An upload now lands on the one-tap start page; the detailed review is a link from there.
        self.assertIn("/start", response.headers["Location"])
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            assert project is not None
            return project.id

    def test_camera_fallback_combined_capture_review_correction_restart_and_ownership(self):
        projects_page = self.client.get("/projects")
        # One file input takes photos and PDFs together; the camera scanner sits beside it.
        self.assertIn(b"Photos or PDFs", projects_page.data)
        self.assertIn(b"application/pdf,image/jpeg", projects_page.data)
        self.assertIn(b"Scan with Camera", projects_page.data)
        scanner = self.client.get("/static/scanner.js")
        self.assertIn(b"getUserMedia", scanner.data)
        # Without a live camera the scanner falls back to the phone's own camera app.
        self.assertIn(b"nativeCameraInput", scanner.data)
        self.assertIn(b"cameraUnavailableNative", scanner.data)
        self.assertIn(b'id="nativeCameraInput"', projects_page.data)
        self.assertIn(b"getTracks", scanner.data)
        scanner.close()

        project_id = self.create_combined_project()
        gated = self.client.post(f"/projects/{project_id}/process")
        self.assertIn("/review", gated.headers["Location"])
        with application.app.app_context():
            files = application.db.session.scalars(
                application.db.select(application.ProjectFile).order_by(application.ProjectFile.id)
            ).all()
            pages = application.db.session.scalars(
                application.db.select(application.ProjectPage).order_by(application.ProjectPage.page_order)
            ).all()
            self.assertEqual([item.source_kind for item in files], ["upload", "camera", "camera"])
            # PF1 (DR-08): the upload renders nothing; each picture is made on first read.
            self.assertTrue(all(item.processed_data is None for item in pages))
            page_ids = [item.id for item in pages]

        recognition = [
            FakeResponse(payload("printed_notes", "formula")),
            FakeResponse(payload("neat_handwriting", "difficult_handwriting")),
            FakeResponse(payload("diagram", "highlighted_text", "mixed_print_handwriting")),
        ]
        with patch.object(application, "create_response", side_effect=recognition):
            recognized = self.client.post(f"/projects/{project_id}/recognize")
        self.assertEqual(recognized.status_code, 302)
        with application.app.app_context():
            pages = application.db.session.scalars(
                application.db.select(application.ProjectPage).order_by(application.ProjectPage.page_order)
            ).all()
            self.assertTrue(all(item.processed_data for item in pages))
            self.assertNotEqual(pages[0].source_file.original_data, pages[0].processed_data)
        review = self.client.get(f"/projects/{project_id}/review")
        # The redesigned review page replaced the old "Verify uncertain regions" list
        # with a region editor. The guarantee under test is unchanged: low-confidence
        # regions are surfaced for the student to confirm, never silently corrected.
        self.assertIn(b"Fix the words Learnova was unsure about", review.data)
        self.assertIn(b"data-region-editor", review.data)
        self.assertIn("2H₂ + O₂ → 2H₂O", review.get_data(as_text=True))
        self.assertIn(b"Detected diagrams", review.data)
        with application.app.app_context():
            unclear = application.db.session.scalar(application.db.select(application.DocumentBlock).where(
                application.DocumentBlock.confidence_status == "unclear"
            ))
            assert unclear is not None
            unclear_id = unclear.id
            source = json.loads(unclear.source_json)
            self.assertEqual(source["project_id"], project_id)
            self.assertIn("bbox", source)

        corrected = self.client.post(f"/projects/{project_id}/review", data={
            "action": "save", f"block_{unclear_id}": "mitochondria",
            f"text_{page_ids[1]}": "v = s / t\nmitochondria",
        })
        self.assertEqual(corrected.status_code, 200)
        confirmed = self.client.post(f"/projects/{project_id}/review", data={"action": "confirm"})
        self.assertEqual(confirmed.status_code, 302)
        reordered = self.client.post(
            f"/projects/{project_id}/pages/reorder", json={"page_ids": list(reversed(page_ids))}
        )
        self.assertEqual(reordered.status_code, 200)
        with application.app.app_context():
            unclear = application.db.session.get(application.DocumentBlock, unclear_id)
            project = application.db.session.get(application.LearningProject, project_id)
            assert unclear is not None and project is not None
            self.assertEqual(unclear.content, "mitochondria")
            self.assertEqual(project.status, "confirmed")

        application.SESSIONS.clear()
        self.assertEqual(self.client.get(f"/projects/{project_id}/review").status_code, 200)
        private_image = self.client.get(f"/projects/{project_id}/pages/{page_ids[0]}/image/processed")
        self.assertIn("private", private_image.headers["Cache-Control"])
        self.assertIn("no-store", private_image.headers["Cache-Control"])
        self.client.post("/logout")
        self.client.post("/register", data={
            "username": "bob", "email": "bob@example.com", "password": "correct-horse-battery",
        })
        self.assertEqual(self.client.get(f"/projects/{project_id}/review").status_code, 404)
        self.assertEqual(self.client.get(f"/projects/{project_id}/pages/{page_ids[0]}/image/processed").status_code, 404)
        self.assertEqual(self.client.get(f"/projects/{project_id}/blocks/{unclear_id}/region").status_code, 404)

    def test_pdf_is_rendered_into_independent_processed_pages(self):
        document = pymupdf.open()
        first = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        first.insert_text((72, 72), "Printed formula: E = m * g * h")
        document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        pdf_data = document.tobytes()
        document.close()
        uploaded = self.client.post("/projects", data={
            "title": "PDF notes", "subject": "Physics",
            "materials": [(io.BytesIO(pdf_data), "notes.pdf", "application/pdf")],
        }, content_type="multipart/form-data")
        self.assertEqual(uploaded.status_code, 302)
        with application.app.app_context():
            pages = application.db.session.scalars(
                application.db.select(application.ProjectPage).order_by(application.ProjectPage.page_order)
            ).all()
            self.assertEqual(len(pages), 2)
            self.assertEqual([page.page_number for page in pages], [1, 2])
            self.assertIn("Printed formula", pages[0].extracted_text)
            project_id, page_ids = pages[0].project_id, [page.id for page in pages]
        # PF1 (DR-08): each page is rendered on its first view, independently, and kept.
        for page_id in page_ids:
            image = self.client.get(f"/projects/{project_id}/pages/{page_id}/image/processed")
            self.assertEqual(image.status_code, 200)
        with application.app.app_context():
            pages = application.db.session.scalars(
                application.db.select(application.ProjectPage).order_by(application.ProjectPage.page_order)
            ).all()
            self.assertTrue(all(page.processed_data for page in pages))
            self.assertNotEqual(pages[0].processed_data, pages[1].processed_data)

    def test_failed_page_retry_and_rotation_are_persistent(self):
        project_id = self.create_combined_project()
        with application.app.app_context():
            pages = application.db.session.scalars(
                application.db.select(application.ProjectPage).order_by(application.ProjectPage.page_order)
            ).all()
            first_id = pages[0].id
        with patch.object(application, "create_response", side_effect=[
            RuntimeError("temporary recognition outage"),
            FakeResponse(payload("neat_handwriting")),
            FakeResponse(payload("diagram")),
        ]):
            self.client.post(f"/projects/{project_id}/recognize")
        with application.app.app_context():
            failed = application.db.session.get(application.ProjectPage, first_id)
            assert failed is not None
            self.assertEqual(failed.processing_stage, "failed")
            self.assertEqual(failed.retry_count, 1)
        with patch.object(application, "create_response", return_value=FakeResponse(payload("printed_notes"))):
            retried = self.client.post(f"/projects/{project_id}/pages/{first_id}/retry")
        self.assertEqual(retried.status_code, 302)
        with application.app.app_context():
            recovered = application.db.session.get(application.ProjectPage, first_id)
            assert recovered is not None
            self.assertEqual(recovered.processing_stage, "ready_for_review")
        rotated = self.client.post(f"/projects/{project_id}/pages/{first_id}/rotate")
        self.assertEqual(rotated.status_code, 302)
        with application.app.app_context():
            rotated_page = application.db.session.get(application.ProjectPage, first_id)
            assert rotated_page is not None
            self.assertEqual(rotated_page.rotation, 90)
            self.assertEqual(rotated_page.processing_stage, "improved")
            self.assertFalse(rotated_page.blocks)


def upload_file(data, filename, mime_type="image/png"):
    return (FileStorage(stream=io.BytesIO(data), filename=filename, content_type=mime_type), "upload", {})


def pdf_with_pages(count):
    document = pymupdf.open()
    for index in range(count):
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        page.insert_text((72, 72), f"Seite {index + 1}")
    data = document.tobytes()
    document.close()
    return data


class UploadIngestTests(unittest.TestCase):
    """learnova.uploads: what an upload leaves out is reported, and limits name the file."""

    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        client = application.app.test_client()
        client.post("/register", data={
            "username": "uma", "email": "uma@example.com", "password": "correct-horse-battery"})
        self.context = application.app.app_context()
        self.context.push()
        user = application.db.session.scalar(application.db.select(application.User))
        assert user is not None
        self.user_id = user.id
        self.project = application.LearningProject(user_id=user.id, title="Bio", subject="Biology")
        application.db.session.add(self.project)
        application.db.session.commit()

    def tearDown(self):
        application.db.session.rollback()
        self.context.pop()

    def ingest(self, files, **kwargs):
        return ingest_uploads(application.db, self.project, application.ProjectFile,
                              application.ProjectPage, uploads=files, **kwargs)

    def test_duplicates_are_reported_instead_of_skipped_silently(self):
        first, second = page_image("Zelle"), page_image("Kern")
        result = self.ingest([upload_file(first, "zelle.png"), upload_file(second, "kern.png"),
                              upload_file(first, "zelle (1).png")])
        self.assertIsInstance(result, UploadResult)
        self.assertEqual((result.pages_added, result.files_added), (2, 2))
        self.assertEqual(result.skipped_duplicates, ["zelle_1.png"], "the stored, sanitised name")
        # A file already in the project is a duplicate too.
        later = self.ingest([upload_file(second, "kern.png"), upload_file(page_image("Membran"), "membran.png")])
        self.assertEqual((later.pages_added, later.skipped_duplicates), (1, ["kern.png"]))
        self.assertEqual(len(self.project.pages), 3)

    def test_an_upload_of_only_duplicates_names_them(self):
        data = page_image("Zelle")
        self.ingest([upload_file(data, "zelle.png")])
        with self.assertRaises(NoNewPagesError) as caught:
            self.ingest([upload_file(data, "zelle.png"), upload_file(data, "nochmal.png")])
        self.assertIsInstance(caught.exception, ValueError, "old callers catch ValueError")
        self.assertEqual(str(caught.exception), "No unique supported pages were uploaded")
        self.assertEqual(caught.exception.skipped_duplicates, ("zelle.png", "nochmal.png"))

    def test_the_old_entry_points_keep_their_return_values(self):
        added = add_uploads_to_project(application.db, self.project, application.ProjectFile,
                                       application.ProjectPage, uploads=[upload_file(page_image("A"), "a.png")])
        self.assertEqual(added, 1)
        data = page_image("B")
        project, result = create_project_with_uploads(
            application.db, application.LearningProject, application.ProjectFile, application.ProjectPage,
            user_id=self.user_id, title="Chemie", subject="Chemistry", exam_date=None,
            uploads=[upload_file(data, "b.png"), upload_file(data, "b-copy.png")])
        self.assertEqual((len(project.pages), result.skipped_duplicates), (1, ["b-copy.png"]))

    def test_the_page_limit_names_the_file_that_crosses_it(self):
        files = [upload_file(page_image(label), f"{label}.png") for label in ("eins", "zwei", "drei")]
        with self.assertRaises(ProjectPageLimitError) as caught:
            self.ingest(files, max_pages=2)
        error = caught.exception
        self.assertIsInstance(error, ValueError, "app.py rolls back on ValueError")
        self.assertEqual((error.filename, error.limit), ("drei.png", 2))
        self.assertEqual(error.params, {"filename": "drei.png", "limit": 2})
        self.assertEqual(str(error), "“drei.png” would take this project over 2 pages. "
                                     "Split the material into two projects or remove some pages.")
        self.assertEqual(str(error), error.msgid.format(**error.params), "one msgid for tr()")

    def test_a_pdf_that_crosses_the_limit_is_named_before_any_page_is_rendered(self):
        self.ingest([upload_file(page_image("eins"), "eins.png")])
        with self.assertRaises(ProjectPageLimitError) as caught:
            self.ingest([upload_file(pdf_with_pages(3), "Arbeitsblatt.pdf", "application/pdf")], max_pages=3)
        self.assertEqual((caught.exception.filename, caught.exception.limit), ("Arbeitsblatt.pdf", 3))

    def test_the_limit_is_forty_pages_and_comes_from_config(self):
        self.assertEqual(DEFAULT_PROJECT_MAX_PAGES, 40)
        self.assertEqual(application.app.config["PROJECT_MAX_PAGES"], 40)
        for function in (ingest_uploads, add_uploads_to_project, create_project_from_uploads,
                         create_project_with_uploads):
            self.assertEqual(inspect.signature(function).parameters["max_pages"].default,
                             DEFAULT_PROJECT_MAX_PAGES, function.__name__)
        with patch.dict(os.environ, {"PROJECT_MAX_PAGES": "12"}):
            configured = Flask("page-limit-test", instance_path=tempfile.mkdtemp())
            configure_app(configured, "testing")
        self.assertEqual(configured.config["PROJECT_MAX_PAGES"], 12)
        with patch.dict(os.environ, {"PROJECT_MAX_PAGES": "viele"}):
            with self.assertRaises(RuntimeError):
                configure_app(Flask("page-limit-test", instance_path=tempfile.mkdtemp()), "testing")

    def test_only_a_scanned_page_carries_photo_advisories(self):
        prose = ("Die Zellmembran umgibt die Zelle und regelt, welche Stoffe hinein- und "
                 "hinausgelangen. Der Zellkern enthält die Erbinformation.")
        scan = page_image("", size=(800, 1100))   # white paper: a real photo of it shows glare
        document = pymupdf.open()
        document.new_page().insert_textbox(  # pyright: ignore[reportAttributeAccessIssue]
            pymupdf.Rect(60, 60, 540, 700), prose, fontsize=13)
        document.new_page()  # pyright: ignore[reportAttributeAccessIssue]  # blank back page
        figure_page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        figure_page.insert_image(pymupdf.Rect(100, 100, 300, 300), stream=scan)  # a small figure
        scanned = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        scanned.insert_image(scanned.rect, stream=scan)   # what a scanner app writes
        ocr_scan = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        ocr_scan.insert_image(ocr_scan.rect, stream=scan)
        ocr_scan.insert_textbox(pymupdf.Rect(60, 60, 540, 700), prose, fontsize=13)  # its OCR layer
        data = document.tobytes()
        document.close()
        self.assertEqual(pdf_page_pictures(data), [False, False, False, True, True])
        self.assertEqual(pdf_page_pictures(b"%PDF-1.4 broken"), [], "unknown means: treat as a picture")
        # The rule holds where pictures are made: at upload behind the rollback switch
        # (preprocess=True, PROJECT_PREPROCESS_ON_UPLOAD) and on first use (PF1, DR-08).
        self.ingest([upload_file(data, "skript.pdf", "application/pdf")], preprocess=True)
        pages = sorted(self.project.pages, key=lambda page: page.page_order)
        # Born-digital pages, with words or without, render as crisp white paper: no
        # glare or blur wording. The scan without a text layer is still judged as a photo.
        for page in pages[:3] + pages[4:]:
            self.assertNotIn("glare", page.warning, page.page_number)
            self.assertNotIn("blurred", page.warning, page.page_number)
        self.assertIn("glare", pages[3].warning)
        lazy = application.LearningProject(user_id=self.user_id, title="Bio lazy", subject="Biology")
        application.db.session.add(lazy)
        application.db.session.commit()
        ingest_uploads(application.db, lazy, application.ProjectFile, application.ProjectPage,
                       uploads=[upload_file(data, "skript.pdf", "application/pdf")])
        pages = sorted(lazy.pages, key=lambda page: page.page_order)
        self.assertTrue(all(page.processed_data is None and not page.warning for page in pages))
        for page in pages:
            application.ensure_processed_page_image(page)
        for page in pages[:3] + pages[4:]:
            self.assertNotIn("glare", page.warning, page.page_number)
            self.assertNotIn("blurred", page.warning, page.page_number)
        self.assertIn("glare", pages[3].warning)

    def test_an_upload_with_nothing_new_has_a_translatable_reason(self):
        data = page_image("Zelle")
        self.ingest([upload_file(data, "zelle.png")])
        with self.assertRaises(NoNewPagesError) as caught:
            self.ingest([upload_file(data, "zelle.png")])
        self.assertEqual(caught.exception.msgid,
                         "Every file you chose is already in this project, so no pages were added.")
        self.assertEqual(caught.exception.params, {})
        with self.assertRaises(NoNewPagesError) as caught:
            self.ingest([])
        self.assertEqual(caught.exception.msgid, "These files contain no page Learnova can add.")
        self.assertEqual(str(caught.exception), "No unique supported pages were uploaded",
                         "str() stays what app.py flashes today")

    def test_nothing_is_stored_when_an_upload_is_refused(self):
        # The limit refuses the whole upload: no file row, no page row survives it.
        files = [upload_file(page_image(label), f"{label}.png") for label in ("eins", "zwei", "drei")]
        with self.assertRaises(ProjectPageLimitError):
            self.ingest(files, max_pages=2)
        application.db.session.rollback()
        self.assertEqual(application.db.session.scalar(
            application.db.select(application.db.func.count(application.ProjectFile.id))), 0)
        self.assertEqual(application.db.session.scalar(
            application.db.select(application.db.func.count(application.ProjectPage.id))), 0)


if __name__ == "__main__":
    unittest.main()

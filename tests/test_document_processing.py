import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_document_processing_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
import pymupdf  # noqa: E402  # pyright: ignore[reportMissingImports]
from document_processing import (  # noqa: E402
    normalize_recognition,
    preprocess_document_image,
    validate_document_upload,
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
        application.app.config.update(TESTING=True)
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
        self.assertIn("/review", response.headers["Location"])
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            assert project is not None
            return project.id

    def test_camera_fallback_combined_capture_review_correction_restart_and_ownership(self):
        projects_page = self.client.get("/projects")
        self.assertIn(b"Upload PDF", projects_page.data)
        self.assertIn(b"Upload Images", projects_page.data)
        self.assertIn(b"Scan with Camera", projects_page.data)
        scanner = self.client.get("/static/scanner.js")
        self.assertIn(b"getUserMedia", scanner.data)
        self.assertIn(b"Upload Images", scanner.data)
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
            self.assertTrue(all(item.processed_data for item in pages))
            self.assertNotEqual(pages[0].source_file.original_data, pages[0].processed_data)
            page_ids = [item.id for item in pages]

        recognition = [
            FakeResponse(payload("printed_notes", "formula")),
            FakeResponse(payload("neat_handwriting", "difficult_handwriting")),
            FakeResponse(payload("diagram", "highlighted_text", "mixed_print_handwriting")),
        ]
        with patch.object(application, "create_response", side_effect=recognition):
            recognized = self.client.post(f"/projects/{project_id}/recognize")
        self.assertEqual(recognized.status_code, 302)
        review = self.client.get(f"/projects/{project_id}/review")
        self.assertIn(b"Verify uncertain regions", review.data)
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
            self.assertTrue(all(page.processed_data for page in pages))
            self.assertEqual([page.page_number for page in pages], [1, 2])
            self.assertIn("Printed formula", pages[0].extracted_text)

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


if __name__ == "__main__":
    unittest.main()

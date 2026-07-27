import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfWriter


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_flashcard_imports_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
import pymupdf  # noqa: E402  # pyright: ignore[reportMissingImports]
from learnova.flashcards import imports  # noqa: E402
from learnova.flashcards.image_extraction import extract_image_text  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


def image_bytes(fmt="PNG", size=(1000, 700), orientation=None):
    image = Image.new("RGB", size, "white")
    output = io.BytesIO()
    kwargs = {}
    if orientation:
        exif = image.getexif()
        exif[274] = orientation
        kwargs["exif"] = exif
    image.save(output, format=fmt, **kwargs)
    return output.getvalue()


def pdf_bytes(page_texts):
    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    for text in page_texts:
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


OCR_PAYLOAD = {
    "blocks": [{
        "type": "printed_text", "content": "Photosynthesis converts light energy.",
        "bbox": [0.1, 0.1, 0.9, 0.2], "confidence": 0.91,
        "crossed_out": False, "important_candidate": False,
        "teacher_highlight_candidate": False, "nearby_text": "",
    }],
    "detected_page_number": "1", "warning": "",
}
GENERATED = {
    "title": "Reviewed biology",
    "cards": [
        {"type": "question_answer", "front": "What converts light energy?",
         "back": "Photosynthesis.", "sourceReference": "Page 1", "difficulty": "easy"},
        {"type": "term_definition", "front": "Define ATP", "back": "An energy carrier.",
         "sourceReference": "Page 2", "difficulty": "medium"},
    ],
}


class FlashcardImportServiceTests(unittest.TestCase):
    def test_signature_extension_corruption_size_and_pixels(self):
        valid = imports.validate_upload(
            image_bytes("PNG"), "notes.png", "image/png",
            max_pdf_size=1000000, max_image_size=1000000,
            max_pdf_pages=3, max_image_pixels=1_000_000,
        )
        self.assertEqual(valid.source_type, "image")
        for fmt, extension, mime in (
            ("JPEG", "jpg", "image/jpeg"), ("PNG", "png", "image/png"),
            ("WEBP", "webp", "image/webp"),
        ):
            result = imports.validate_upload(
                image_bytes(fmt), f"x.{extension}", mime,
                max_pdf_size=2_000_000, max_image_size=2_000_000,
                max_pdf_pages=3, max_image_pixels=1_000_000,
            )
            self.assertEqual(result.mime_type, mime)
        cases = (
            (b"", "x.png", "empty_file"),
            (image_bytes(), "x.exe", "unsupported_extension"),
            (image_bytes(), "x.pdf", "type_mismatch"),
            (b"not an image", "x.png", "unsupported_file"),
        )
        for data, name, code in cases:
            with self.subTest(code=code), self.assertRaises(imports.ImportProblem) as caught:
                imports.validate_upload(
                    data, name, None, max_pdf_size=100, max_image_size=2_000_000,
                    max_pdf_pages=3, max_image_pixels=1_000_000)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(imports.ImportProblem) as caught:
            imports.validate_upload(
                image_bytes(size=(1001, 1000)), "x.png", "image/png",
                max_pdf_size=2_000_000, max_image_size=2_000_000,
                max_pdf_pages=3, max_image_pixels=1_000_000)
        self.assertEqual(caught.exception.code, "image_too_large")

    def test_pdf_password_page_limit_and_page_extraction(self):
        data = pdf_bytes(["A long native text page about cells and organelles for extraction.", "", "tiny"])
        valid = imports.validate_upload(
            data, "notes.pdf", "application/pdf", max_pdf_size=2_000_000,
            max_image_size=1, max_pdf_pages=3, max_image_pixels=1,
        )
        self.assertEqual(valid.page_count, 3)
        pages = imports.extract_pdf_pages(data)
        self.assertEqual([page["page_number"] for page in pages], [1, 2, 3])
        self.assertEqual(pages[0]["status"], "success")
        self.assertEqual(pages[1]["status"], "needs_ocr")
        self.assertEqual(pages[2]["status"], "low_text")
        with self.assertRaises(imports.ImportProblem) as caught:
            imports.validate_upload(
                data, "notes.pdf", "application/pdf", max_pdf_size=2_000_000,
                max_image_size=1, max_pdf_pages=2, max_image_pixels=1)
        self.assertEqual(caught.exception.code, "too_many_pages")

        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt("secret")
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaises(imports.ImportProblem) as caught:
            imports.validate_upload(
                output.getvalue(), "locked.pdf", "application/pdf",
                max_pdf_size=2_000_000, max_image_size=1,
                max_pdf_pages=3, max_image_pixels=1)
        self.assertEqual(caught.exception.code, "pdf_password_protected")

    def test_private_storage_keys_and_orientation_ocr_boundary(self):
        document_id = "501db4fd-2f9d-4fd9-bf19-0dfbc00658ec"
        first = imports.private_storage_key(7, document_id, ".png")
        second = imports.private_storage_key(7, document_id, ".png")
        self.assertNotEqual(first, second)
        self.assertNotIn("notes", first)
        seen = {}

        def recognize(**kwargs):
            seen.update(kwargs)
            return OCR_PAYLOAD

        result = extract_image_text(
            image_bytes("JPEG", size=(400, 900), orientation=6),
            subject="Biology", page_number=1, recognize=recognize,
        )
        self.assertTrue(result["readable"])
        self.assertGreater(result["width"], result["height"])
        self.assertEqual(seen["image_mime"], "image/png")


class FlashcardImportWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        application.app.config.update(
            TESTING=True, FEATURE_PRIVATE_FLASHCARDS=True,
            FEATURE_FLASHCARD_PDF_IMPORT=True, FEATURE_FLASHCARD_IMAGE_IMPORT=True,
            FLASHCARD_IMPORT_STORAGE_DIR=self.storage.name,
            MAX_FLASHCARD_PDF_SIZE=2_000_000, MAX_FLASHCARD_IMAGE_SIZE=2_000_000,
            MAX_FLASHCARD_PDF_PAGES=5, MAX_FLASHCARD_IMAGE_PIXELS=2_000_000,
            MAX_FLASHCARD_EXTRACTED_TEXT_LENGTH=10_000,
        )
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "owner", "email": "owner@example.com",
            "password": "correct-horse-battery",
        })

    def tearDown(self):
        self.storage.cleanup()

    def upload(self, data, filename, mime):
        return self.client.post("/api/flashcards/imports", data={
            "file": (io.BytesIO(data), filename, mime),
        }, content_type="multipart/form-data")

    def test_pdf_review_selection_generation_draft_and_no_auto_save(self):
        response = self.upload(pdf_bytes([
            "KEEP this reviewed page with enough biology source material for cards.",
            "DROP this second page with irrelevant history material.",
        ]), "biology.pdf", "application/pdf")
        self.assertEqual(response.status_code, 201)
        document = response.get_json()["document"]
        self.assertNotIn("storage", document)
        document_id = document["id"]
        extracted = self.client.post(f"/api/flashcards/imports/{document_id}/extract")
        self.assertEqual(extracted.status_code, 200)
        pages = extracted.get_json()["document"]["pages"]
        with patch.object(application, "create_response", return_value=FakeResponse(OCR_PAYLOAD)):
            ocr_page = self.client.post(
                f"/api/flashcards/imports/{document_id}/ocr-page",
                json={"page_number": 2},
            )
        self.assertEqual(ocr_page.status_code, 200)
        pages = ocr_page.get_json()["document"]["pages"]
        self.assertEqual(pages[1]["source"], "ocr")
        pages[0].update(text="EDITED KEEP SOURCE", selected=True)
        pages[1].update(text="EDITED DROP SOURCE", selected=False)
        reviewed = self.client.put(
            f"/api/flashcards/imports/{document_id}/content", json={"pages": pages})
        self.assertEqual(reviewed.get_json()["selected_pages"], [1])

        with application.app.app_context():
            owner = application.db.session.scalar(
                application.db.select(application.User).where(
                    application.User.username == "owner"))
            assert owner is not None
            owner.preferred_language = "de"
            application.db.session.commit()
        with patch.object(application, "create_response", return_value=FakeResponse(GENERATED)) as mocked:
            generated = self.client.post(
                f"/api/flashcards/imports/{document_id}/generate", json={
                    "subject": "Biology", "grade": "8", "difficulty": "medium",
                    "card_type": "mixed", "count": 2, "content_language": "en",
                })
        self.assertEqual(generated.status_code, 200)
        prompt = mocked.call_args.kwargs["input"]
        self.assertIn("EDITED KEEP SOURCE", prompt)
        self.assertNotIn("DROP", prompt)
        self.assertIn("Write all student-facing text in English", prompt)
        draft = self.client.get(f"/api/flashcards/imports/{document_id}/draft").get_json()["draft"]
        self.assertEqual(len(draft["cards"]), 2)
        self.assertEqual(draft["cards"][0]["source_reference"], "Page 1")
        creator = self.client.get(generated.get_json()["creator_url"])
        self.assertIn(document_id.encode(), creator.data)
        self.assertEqual(self.client.get("/api/flashcards/sets").get_json()["sets"], [])

    def test_image_ocr_edit_duplicate_ownership_expiry_cleanup_and_flags(self):
        image = image_bytes()
        with patch.object(application, "create_response", return_value=FakeResponse(OCR_PAYLOAD)):
            created = self.upload(image, "phone.png", "image/png")
            document_id = created.get_json()["document"]["id"]
            duplicate = self.upload(image, "phone.png", "image/png")
            self.assertEqual(duplicate.status_code, 409)
            extracted = self.client.post(f"/api/flashcards/imports/{document_id}/extract")
        self.assertEqual(extracted.status_code, 200)
        page = extracted.get_json()["document"]["pages"][0]
        self.assertEqual(page["source"], "ocr")
        page["text"] = "Student corrected OCR text"
        saved = self.client.put(
            f"/api/flashcards/imports/{document_id}/content", json={"pages": [page]})
        self.assertEqual(saved.status_code, 200)

        stranger = application.app.test_client()
        stranger.post("/register", data={
            "username": "stranger", "email": "stranger@example.com",
            "password": "correct-horse-battery",
        })
        self.assertEqual(stranger.get(
            f"/api/flashcards/imports/{document_id}").status_code, 404)
        self.assertEqual(stranger.delete(
            f"/api/flashcards/imports/{document_id}").status_code, 404)

        with application.app.app_context():
            record = application.db.session.get(application.FlashcardImport, document_id)
            assert record is not None
            stored = Path(application.app.config["FLASHCARD_IMPORT_STORAGE_DIR"]) / record.storage_key
            self.assertTrue(stored.is_file())
            record.expires_at = application.utcnow() - timedelta(minutes=1)
            application.db.session.commit()
            self.assertEqual(application.cleanup_expired_flashcard_imports(), 1)
            self.assertFalse(stored.exists())
            self.assertIsNone(application.db.session.get(application.FlashcardImport, document_id))

        application.app.config["FEATURE_FLASHCARD_IMAGE_IMPORT"] = False
        try:
            self.assertEqual(self.upload(image_bytes(), "new.png", "image/png").status_code, 404)
        finally:
            application.app.config["FEATURE_FLASHCARD_IMAGE_IMPORT"] = True

    def test_import_page_gating_german_ui_and_unauthenticated(self):
        self.assertIn(b"PDF upload", self.client.get("/flashcards/import").data)
        with application.app.app_context():
            owner = application.db.session.scalar(
                application.db.select(application.User).where(
                    application.User.username == "owner"))
            owner.preferred_language = "de"
            application.db.session.commit()
        german = self.client.get("/flashcards/import")
        self.assertIn("Lernmaterial importieren".encode(), german.data)
        anonymous = application.app.test_client()
        self.assertEqual(anonymous.get("/flashcards/import").status_code, 302)
        application.app.config.update(
            FEATURE_FLASHCARD_PDF_IMPORT=False, FEATURE_FLASHCARD_IMAGE_IMPORT=False)
        try:
            self.assertEqual(self.client.get("/flashcards/import").status_code, 404)
            self.assertNotIn(b"Import material", self.client.get("/flashcards").data)
        finally:
            application.app.config.update(
                FEATURE_FLASHCARD_PDF_IMPORT=True, FEATURE_FLASHCARD_IMAGE_IMPORT=True)

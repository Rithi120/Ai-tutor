import io
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocabulary_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.vocabulary import service  # noqa: E402


class VocabularyServiceTests(unittest.TestCase):
    def test_tables_examples_sections_and_unrecognized_lines(self):
        result = service.parse_vocabulary_text(
            "Unit 4\n1. environment | die Umwelt | We protect the environment.\n"
            "house — das Haus\nunreadable")
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["entries"][0]["section"], "Unit 4")
        self.assertEqual(result["entries"][0]["source_example_sentence"],
                         "We protect the environment.")
        self.assertIn("unreadable", result["unrecognized_lines"])

    def test_validation_ocr_typo_duplicate_and_missing_translation(self):
        typo = service.validate_entry({
            "source_term": "environment", "target_translation": "Umweit"}, "en", "de")
        self.assertEqual(typo["status"], "likely_ocr_error")
        self.assertEqual(typo["suggested_translation"], "Umwelt")
        seen = {("house", "das haus")}
        duplicate = service.validate_entry({
            "source_term": "house", "target_translation": "das Haus"}, "en", "de", seen)
        self.assertEqual(duplicate["status"], "duplicate")
        missing = service.validate_entry({
            "source_term": "friend", "target_translation": ""}, "en", "de")
        self.assertEqual(missing["status"], "missing_translation")

    def test_answer_strictness_articles_accents_and_alternatives(self):
        self.assertFalse(service.check_answer(
            "umwelt", "Umwelt", strictness="exact", language="de")["correct"])
        self.assertTrue(service.check_answer(
            "Umwelt.", "Umwelt", strictness="normal", language="de")["correct"])
        self.assertTrue(service.check_answer(
            "Haus", "das Haus", strictness="flexible", language="de")["correct"])
        self.assertTrue(service.check_answer(
            "colour", "color", ["colour"], strictness="flexible", language="en")["correct"])
        self.assertFalse(service.check_answer(
            "nature", "environment", strictness="flexible", language="en")["correct"])

    def test_card_variants_preserve_example_and_reference(self):
        cards = service.card_variants({
            "source_term": "environment", "target_translation": "die Umwelt",
            "source_example_sentence": "Protect the environment.",
            "source_page": 12, "source_language": "en",
        }, ["source_to_target", "target_to_source", "source_to_blank"], {
            "include_examples": True, "include_hints": True, "difficulty": "medium"})
        self.assertEqual(len(cards), 3)
        self.assertIn("________", cards[2]["front"])
        self.assertEqual(cards[0]["source_reference"], "Page 12")


class VocabularyWorkflowTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_VOCABULARY_TRAINER=True,
            FEATURE_PRIVATE_FLASHCARDS=True, FEATURE_FLASHCARD_IMAGE_IMPORT=True,
            FEATURE_FLASHCARD_PDF_IMPORT=True, FEATURE_GAMIFICATION=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
            application.ensure_database()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "vocab", "email": "vocab@example.com",
            "password": "correct-horse-battery"})

    def create_text_import(self):
        response = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "text", "source_language": "en", "target_language": "de",
            "title": "Unit 4",
            "text": "environment | Umweit | We protect the environment.\nhouse | das Haus",
        }, headers={"Idempotency-Key": "vocabulary-text-one"})
        self.assertEqual(response.status_code, 201, response.data)
        return response.get_json()["vocabulary_import"]["id"]

    def test_text_extract_validate_review_generate_editor_save_and_practice(self):
        import_id = self.create_text_import()
        self.assertEqual(self.client.post(
            f"/api/vocabulary/imports/{import_id}/extract").status_code, 200)
        validated = self.client.post(
            f"/api/vocabulary/imports/{import_id}/validate").get_json()["vocabulary_import"]
        self.assertEqual(validated["entries"][0]["status"], "likely_ocr_error")
        entries = validated["entries"]
        entries[0]["target_translation"] = entries[0]["suggested_translation"]
        for entry in entries:
            entry["user_confirmed"] = True
        self.client.put(
            f"/api/vocabulary/imports/{import_id}/entries", json={"entries": entries})
        generated = self.client.post(
            f"/api/vocabulary/imports/{import_id}/generate",
            json={"directions": ["source_to_target", "target_to_source", "source_to_blank"],
                  "include_examples": True},
            headers={"Idempotency-Key": "generate-one"})
        self.assertEqual(generated.status_code, 200, generated.data)
        payload = generated.get_json()
        draft = self.client.get(
            f"/api/vocabulary/imports/{import_id}/draft").get_json()["draft"]
        self.assertGreaterEqual(len(draft["cards"]), 4)
        saved = self.client.post("/api/flashcards/sets", json={
            **draft, "cards": draft["cards"]})
        self.assertEqual(saved.status_code, 201, saved.data)
        list_id = payload["list_id"]
        vocabulary_list = self.client.get(
            f"/api/vocabulary/lists/{list_id}").get_json()["vocabulary_list"]
        self.assertEqual(vocabulary_list["flashcard_set_id"], saved.get_json()["id"])
        practice = self.client.get(
            f"/api/vocabulary/lists/{list_id}/practice?direction=source_to_target").get_json()
        first = practice["items"][0]
        answer = self.client.post(
            f"/api/vocabulary/lists/{list_id}/practice/{first['entry_id']}/answer",
            json={"direction": "source_to_target", "answer": "Umwelt",
                  "strictness": "normal", "request_id": "practice-one"})
        self.assertTrue(answer.get_json()["correct"])
        reverse = self.client.get(
            f"/api/vocabulary/lists/{list_id}/practice?direction=target_to_source").get_json()
        self.assertEqual(reverse["items"][0]["mastery"], "new")
        duplicate = self.client.post(
            f"/api/vocabulary/lists/{list_id}/practice/{first['entry_id']}/answer",
            json={"direction": "source_to_target", "answer": "Umwelt",
                  "strictness": "normal", "request_id": "practice-one"})
        self.assertTrue(duplicate.get_json()["duplicate"])

    def test_image_upload_ownership_and_private_preview(self):
        image = Image.new("RGB", (800, 600), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        response = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "file", "source_language": "fr", "target_language": "de",
            "file": (io.BytesIO(buffer.getvalue()), "vocab.png"),
        }, content_type="multipart/form-data", headers={"Idempotency-Key": "image-one"})
        self.assertEqual(response.status_code, 201, response.data)
        imported = response.get_json()["vocabulary_import"]
        self.assertTrue(imported["preview_url"])
        self.assertEqual(self.client.get(imported["preview_url"]).status_code, 200)
        stranger = application.app.test_client()
        stranger.post("/register", data={
            "username": "stranger", "email": "stranger@example.com",
            "password": "correct-horse-battery"})
        self.assertEqual(stranger.get(
            f"/api/vocabulary/imports/{imported['id']}").status_code, 404)

    def test_pages_navigation_dashboard_and_german_are_real(self):
        for url in ("/vocabulary", "/vocabulary/import", "/dashboard", "/flashcards"):
            self.assertEqual(self.client.get(url).status_code, 200)
        self.assertIn(b"Vocabulary Trainer", self.client.get("/dashboard").data)
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(
                application.User).where(application.User.username == "vocab"))
            assert user is not None
            user.preferred_language = "de"
            application.db.session.commit()
        page = self.client.get("/vocabulary")
        self.assertIn("Vokabeltrainer".encode(), page.data)
        self.assertIn("Vokabeln importieren".encode(), page.data)

"""Vocabulary imports with many pages (spec Phase 3, migration 045).

One import used to hold exactly one photo or PDF, and a rescan replaced it. Now an import
holds up to VOCABULARY_MAX_PAGES pages (vocabulary_import_page), uploaded one per request
and read one per request. These tests drive the real routes; the page reader is patched
to answer by the colour of the photo (each test page has its own), so nothing depends on
the local OCR engine and nothing reaches an AI provider. They pin:

* pages keep the order the student gave them, whatever order they are read in, and every
  entry carries its import-wide page number;
* reading a page again replaces exactly its entries; the page limit; duplicates; places;
* a rescan of one page replaces that page's document and merges only its entries;
* deleting an import removes every page's document and file; the expiry clean-up removes
  page rows before their documents (foreign keys enforced, as PostgreSQL does:
  tests/test_pg_fk_integrity.ForeignKeysEnforced);
* an import from before pages existed still reads, rescans and can grow;
* the AI second opinion is asked in chunks of 40, not in one call that could never hold
  hundreds of words.
"""

import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocabulary_multi_page_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.config_isolation import keep_attribute, keep_config  # noqa: E402
from learnova.vocabulary import service as vocabulary  # noqa: E402
from tests.test_pg_fk_integrity import ForeignKeysEnforced  # noqa: E402

db = application.db


def page_photo(number: int, shade_extra: int = 0) -> bytes:
    """A photo whose red channel says which page it is (the fake reader reads it)."""

    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), (number * 5 + shade_extra, 200, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def fake_reader(words_per_page=2, variant=None):
    """read_vocabulary_page_safely without OCR: page N of the book holds motN-a/WortN-a ..."""

    def read(data, page_number, _source, _target):
        red = Image.open(io.BytesIO(data)).convert("RGB").getpixel((0, 0))[0]
        number = red // 5
        suffix = (variant or {}).get(number, "")
        text = "\n".join(f"mot{number}{chr(97 + index)}{suffix} | Wort{number}{chr(97 + index)}"
                         for index in range(words_per_page))
        parsed = vocabulary.parse_vocabulary_text(text, page_number)
        parsed["warnings"] = [f"page {number} read"]
        return parsed
    return read


def no_model(**kwargs):
    raise AssertionError(f"the import called the AI gateway for {kwargs.get('task_type')}")


class Base(unittest.TestCase):
    def setUp(self):
        keep_config(self, application.app.config, "FEATURE_VOCABULARY_TRAINER", "VOCABULARY_MAX_PAGES",
                    "RATELIMIT_ENABLED", "WTF_CSRF_ENABLED")
        keep_attribute(self, application.limiter, "enabled")
        application.app.config.update(TESTING=True, FEATURE_VOCABULARY_TRAINER=True, VOCABULARY_MAX_PAGES=40,
                                      RATELIMIT_ENABLED=False, WTF_CSRF_ENABLED=False)
        application.limiter.enabled = False
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "vera", "email": "vera@example.com",
                                            "password": "correct-horse-battery"})
        for name, value in (("read_vocabulary_page_safely", fake_reader()), ("create_response", no_model)):
            patcher = patch.object(application, name, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def create(self, number=1, *, position=None, label="", key="first"):
        data = {"source_kind": "file", "source_language": "fr", "target_language": "de", "title": "Vocabulaire",
                "file": (io.BytesIO(page_photo(number)), f"page-{number}.png")}
        if position is not None:
            data["position"] = str(position)
        if label:
            data["label"] = label
        response = self.client.post("/api/vocabulary/imports", data=data, content_type="multipart/form-data",
                                    headers={"Idempotency-Key": key})
        self.assertIn(response.status_code, (200, 201), response.data[:300])
        return response.get_json()["vocabulary_import"]

    def add(self, import_id, number, *, position=None, label="", shade_extra=0):
        data = {"file": (io.BytesIO(page_photo(number, shade_extra)), f"page-{number}.png")}
        if position is not None:
            data["position"] = str(position)
        if label:
            data["label"] = label
        return self.client.post(f"/api/vocabulary/imports/{import_id}/pages", data=data,
                                content_type="multipart/form-data")

    def extract_page(self, import_id, page_id):
        return self.client.post(f"/api/vocabulary/imports/{import_id}/pages/{page_id}/extract")

    def terms(self, imported):
        return [entry["source_term"] for entry in imported["entries"]]

    @staticmethod
    def counts():
        with application.app.app_context():
            return {model.__name__: db.session.scalar(db.select(db.func.count()).select_from(model))
                    for model in (application.VocabularyImport, application.VocabularyImportPage,
                                  application.FlashcardImport)}


class MultiPageImportTests(ForeignKeysEnforced, Base):
    def build(self, count=3):
        imported = self.create(1, position=1)
        for number in range(2, count + 1):
            response = self.add(imported["id"], number, position=number)
            self.assertEqual(response.status_code, 201, response.data[:300])
        body = self.client.get(f"/api/vocabulary/imports/{imported['id']}").get_json()["vocabulary_import"]
        return body

    def test_pages_are_added_one_at_a_time_and_kept_in_order(self):
        imported = self.build(3)
        self.assertEqual([page["position"] for page in imported["pages"]], [1, 2, 3])
        self.assertEqual([page["filename"] for page in imported["pages"]], ["page-1.png", "page-2.png", "page-3.png"])
        self.assertTrue(all(page["preview_url"] for page in imported["pages"]))
        self.assertEqual(imported["max_pages"], 40)
        self.assertTrue(imported["can_rescan"])
        # Read out of order: the list is still in page order.
        pages = {page["position"]: page["id"] for page in imported["pages"]}
        for position in (3, 1, 2):
            response = self.extract_page(imported["id"], pages[position])
            self.assertEqual(response.status_code, 200, response.data[:300])
            self.assertEqual(response.get_json()["page"]["status"], "read")
        body = response.get_json()["vocabulary_import"]
        self.assertEqual(self.terms(body), ["mot1a", "mot1b", "mot2a", "mot2b", "mot3a", "mot3b"])
        self.assertEqual([entry["page_number"] for entry in body["entries"]], [1, 1, 2, 2, 3, 3])
        self.assertEqual(body["status"], "ready_for_validation")
        self.assertEqual(body["warnings"], ["page 1 read", "page 2 read", "page 3 read"])

    def test_reading_a_page_again_replaces_exactly_its_entries(self):
        imported = self.build(2)
        first, second = (page["id"] for page in imported["pages"])
        self.extract_page(imported["id"], first)
        self.extract_page(imported["id"], second)
        again = self.extract_page(imported["id"], first).get_json()["vocabulary_import"]
        self.assertEqual(self.terms(again), ["mot1a", "mot1b", "mot2a", "mot2b"])

    def test_the_whole_import_extract_reads_every_page_in_order(self):
        imported = self.build(3)
        body = self.client.post(f"/api/vocabulary/imports/{imported['id']}/extract").get_json()["vocabulary_import"]
        self.assertEqual(self.terms(body), ["mot1a", "mot1b", "mot2a", "mot2b", "mot3a", "mot3b"])
        self.assertTrue(all(page["status"] == "read" for page in body["pages"]))

    def test_a_page_given_an_earlier_place_is_read_into_that_place(self):
        imported = self.create(1, position=2)
        self.assertEqual(self.add(imported["id"], 2, position=1).status_code, 201)
        body = self.client.post(f"/api/vocabulary/imports/{imported['id']}/extract").get_json()["vocabulary_import"]
        self.assertEqual([page["filename"] for page in body["pages"]], ["page-2.png", "page-1.png"])
        self.assertEqual(self.terms(body), ["mot2a", "mot2b", "mot1a", "mot1b"])
        self.assertEqual([entry["page_number"] for entry in body["entries"]], [1, 1, 2, 2])

    def test_labels_places_duplicates_and_the_page_limit(self):
        imported = self.create(1, position=1, label="  Unité\n 3 ")
        self.assertEqual(imported["pages"][0]["label"], "Unité 3")
        taken = self.add(imported["id"], 2, position=1)
        self.assertEqual(taken.status_code, 409)
        self.assertEqual(taken.get_json()["code"], "position_taken")
        same = self.add(imported["id"], 1, position=2)
        self.assertEqual(same.status_code, 200)
        self.assertTrue(same.get_json()["duplicate"])
        self.assertEqual(self.counts()["VocabularyImportPage"], 1)
        application.app.config["VOCABULARY_MAX_PAGES"] = 2
        self.assertEqual(self.add(imported["id"], 2).status_code, 201)
        full = self.add(imported["id"], 3)
        self.assertEqual(full.status_code, 409)
        self.assertEqual(full.get_json()["code"], "page_limit")
        self.assertEqual(self.counts(), {"VocabularyImport": 1, "VocabularyImportPage": 2, "FlashcardImport": 2},
                         "a refused page leaves no document behind")

    def test_only_a_file_import_takes_pages_and_not_after_the_cards_are_made(self):
        text = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "text", "source_language": "fr", "target_language": "de", "text": "le chat | die Katze"},
            headers={"Idempotency-Key": "text"}).get_json()["vocabulary_import"]
        self.assertEqual(self.add(text["id"], 1).get_json()["code"], "pages_not_possible")
        imported = self.create(1)
        with application.app.app_context():
            item = db.session.get(application.VocabularyImport, imported["id"])
            item.status = "generated"
            db.session.commit()
        self.assertEqual(self.add(imported["id"], 2).status_code, 409)

    def test_a_rescan_of_one_page_changes_only_that_page(self):
        imported = self.build(3)
        pages = [page["id"] for page in imported["pages"]]
        self.client.post(f"/api/vocabulary/imports/{imported['id']}/extract")
        old_preview = imported["pages"][1]["preview_url"]
        with patch.object(application, "read_vocabulary_page_safely", side_effect=fake_reader(3, {2: "x"})):
            response = self.client.post(f"/api/vocabulary/imports/{imported['id']}/rescan", data={
                "page_id": pages[1], "file": (io.BytesIO(page_photo(2, shade_extra=1)), "page-2-again.png")},
                content_type="multipart/form-data")
        self.assertEqual(response.status_code, 200, response.data[:300])
        body = response.get_json()
        self.assertEqual(body["page_id"], pages[1])
        # Page 2's open rows are replaced (matched by their translation), its new find is
        # added after them, and pages 1 and 3 are untouched.
        self.assertEqual(self.terms(body["vocabulary_import"]),
                         ["mot1a", "mot1b", "mot2ax", "mot2bx", "mot2cx", "mot3a", "mot3b"])
        self.assertEqual((body["replaced"], body["added"]), (2, 1))
        self.assertEqual([entry["page_number"] for entry in body["vocabulary_import"]["entries"]],
                         [1, 1, 2, 2, 2, 3, 3])
        page_two = body["vocabulary_import"]["pages"][1]
        self.assertEqual(page_two["filename"], "page-2-again.png")
        self.assertEqual(self.client.get(old_preview).status_code, 404, "the old photo of that page is gone")
        self.assertEqual(self.client.get(body["vocabulary_import"]["pages"][0]["preview_url"]).status_code, 200)
        self.assertEqual(self.counts()["FlashcardImport"], 3)

    def test_deleting_the_import_removes_every_page_and_file(self):
        imported = self.build(3)
        storage = Path(application.app.config["FLASHCARD_IMPORT_STORAGE_DIR"])
        with application.app.app_context():
            keys = [document.storage_key for document in db.session.scalars(db.select(application.FlashcardImport))]
        self.assertTrue(all((storage / key).exists() for key in keys))
        response = self.client.delete(f"/api/vocabulary/imports/{imported['id']}")
        self.assertEqual(response.status_code, 200, response.data[:300])
        self.assertEqual(self.counts(), {"VocabularyImport": 0, "VocabularyImportPage": 0, "FlashcardImport": 0})
        self.assertFalse(any((storage / key).exists() for key in keys))

    def test_the_expiry_cleanup_removes_page_rows_before_their_documents(self):
        imported = self.build(3)
        with application.app.app_context():
            pages = db.session.scalars(db.select(application.VocabularyImportPage).order_by(
                application.VocabularyImportPage.position)).all()
            # Only the last page's photo expires: that page goes, the import stays.
            document = db.session.get(application.FlashcardImport, pages[2].flashcard_import_id)
            document.expires_at = application.utcnow() - timedelta(hours=1)
            db.session.commit()
            self.assertEqual(application.cleanup_expired_flashcard_imports(), 1)
        self.assertEqual(self.counts(), {"VocabularyImport": 1, "VocabularyImportPage": 2, "FlashcardImport": 2})
        with application.app.app_context():
            for document in db.session.scalars(db.select(application.FlashcardImport)):
                document.expires_at = application.utcnow() - timedelta(hours=1)
            db.session.commit()
            self.assertEqual(application.cleanup_expired_flashcard_imports(), 2)
        self.assertEqual(self.counts(), {"VocabularyImport": 0, "VocabularyImportPage": 0, "FlashcardImport": 0})
        self.assertEqual(self.client.get(f"/api/vocabulary/imports/{imported['id']}").status_code, 404)

    def test_deleting_one_document_from_the_flashcard_imports_drops_its_page(self):
        imported = self.build(2)
        second = imported["pages"][1]
        document_id = second["preview_url"].split("/imports/")[1].split("/")[0]
        response = self.client.delete(f"/api/flashcards/imports/{document_id}")
        self.assertEqual(response.status_code, 200, response.data[:300])
        self.assertEqual(self.counts(), {"VocabularyImport": 1, "VocabularyImportPage": 1, "FlashcardImport": 1})


class LegacyImportTests(ForeignKeysEnforced, Base):
    """An import made before pages existed: its one document, no page rows."""

    def legacy(self):
        imported = self.create(1)
        with application.app.app_context():
            db.session.execute(db.delete(application.VocabularyImportPage))
            db.session.commit()
        return imported["id"]

    def test_a_legacy_import_still_reads_and_rescans_as_before(self):
        import_id = self.legacy()
        body = self.client.post(f"/api/vocabulary/imports/{import_id}/extract").get_json()["vocabulary_import"]
        self.assertEqual(body["pages"], [])
        self.assertEqual(self.terms(body), ["mot1a", "mot1b"])
        self.assertNotIn("import_page_id", body["entries"][0])
        with patch.object(application, "read_vocabulary_page_safely", side_effect=fake_reader(3)):
            rescanned = self.client.post(f"/api/vocabulary/imports/{import_id}/rescan", data={
                "file": (io.BytesIO(page_photo(1, shade_extra=1)), "again.png")}, content_type="multipart/form-data")
        self.assertEqual(rescanned.status_code, 200, rescanned.data[:300])
        self.assertEqual(self.terms(rescanned.get_json()["vocabulary_import"]), ["mot1a", "mot1b", "mot1c"])
        self.assertEqual(self.counts(), {"VocabularyImport": 1, "VocabularyImportPage": 0, "FlashcardImport": 1})

    def test_a_legacy_import_can_grow(self):
        import_id = self.legacy()
        self.client.post(f"/api/vocabulary/imports/{import_id}/extract")
        added = self.add(import_id, 2)
        self.assertEqual(added.status_code, 201, added.data[:300])
        body = added.get_json()["vocabulary_import"]
        self.assertEqual([page["position"] for page in body["pages"]], [1, 2])
        self.assertEqual(body["pages"][0]["status"], "read", "the words already read belong to page 1")
        read = self.extract_page(import_id, body["pages"][1]["id"]).get_json()["vocabulary_import"]
        self.assertEqual(self.terms(read), ["mot1a", "mot1b", "mot2a", "mot2b"])
        self.assertEqual([entry["page_number"] for entry in read["entries"]], [1, 1, 2, 2])

    def test_the_same_photo_again_on_a_legacy_import_is_its_page_1(self):
        # Review fix: the duplicate answer rolled back the page 1 row it had just made for
        # the old import and then indexed an empty page list (a 500).
        import_id = self.legacy()
        again = self.add(import_id, 1)
        self.assertEqual(again.status_code, 200, again.data[:300])
        body = again.get_json()
        self.assertTrue(body["duplicate"])
        self.assertEqual(body["page"]["position"], 1)
        self.assertEqual([page["position"] for page in body["vocabulary_import"]["pages"]], [1])
        self.assertEqual(self.counts(), {"VocabularyImport": 1, "VocabularyImportPage": 1, "FlashcardImport": 1})


class SecondOpinionChunkTests(Base):
    def test_the_second_opinion_is_asked_in_chunks_of_forty(self):
        imported = self.create(1)
        entries = [{"source_term": f"mot{index}", "target_translation": f"Wort{index}", "confidence": 0.5,
                    "included": True, "user_confirmed": False, "status": "needs_review"} for index in range(90)]
        self.assertEqual(self.client.put(f"/api/vocabulary/imports/{imported['id']}/entries",
                                         json={"entries": entries}).status_code, 200)
        calls = []

        class Answer:
            usage = None
            model = "test"

            def __init__(self, texts):
                self.output_text = json.dumps({"translations": [f"x-{text}" for text in texts]})

        def respond(**kwargs):
            texts = kwargs["validation_context"]["texts"]
            calls.append(len(texts))
            return Answer(texts)
        with patch.object(application, "create_response", side_effect=respond):
            body = self.client.post(f"/api/vocabulary/imports/{imported['id']}/validate",
                                    json={"ai_validation": True}).get_json()
        self.assertEqual(calls, [40, 40, 10])
        suggested = [entry.get("suggested_translation") for entry in body["vocabulary_import"]["entries"]]
        self.assertEqual(suggested[0], "x-mot0")
        self.assertEqual(suggested[89], "x-mot89", "every chunk's answer lands on its own words")


if __name__ == "__main__":
    unittest.main()

"""Delete paths with foreign keys enforced, the way PostgreSQL runs them (FIX_PLAN PF5, R2/R3).

PostgreSQL always enforces foreign keys; SQLite, which the suite runs on, only when asked on
each connection. Without asking, deleting a flashcard set somebody had studied, or cleaning up
an expired scanned photo, passed every test here and answered 503 (or raised) in production.
Each class below switches enforcement on for its own tests (`ForeignKeysEnforced`); the whole
suite can be run the same way with LEARNOVA_TEST_SQLITE_FK=1 (tests/conftest.py).
"""

import io
import tempfile
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import event, func, select, text
from sqlalchemy.exc import SQLAlchemyError

import app as application
from tests.config_isolation import keep_config
from learnova.flashcards import imports as flashcard_import_store

from . import test_vocabulary_scanner as scanner_tests
from . import test_vocabulary_textbook as textbook_tests
from .conftest import sqlite_foreign_keys_on

M = application
db = application.db


class ForeignKeysEnforced:
    """Enforce SQLite foreign keys on every connection of the app's engine during the test."""

    def setUp(self):
        with application.app.app_context():
            engine = db.engine
            event.listen(engine, "connect", sqlite_foreign_keys_on)
            engine.dispose()
        super().setUp()
        with application.app.app_context():
            self.assertEqual(db.session.execute(text("PRAGMA foreign_keys")).scalar(), 1,
                             "foreign keys must be enforced for this test")

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            with application.app.app_context():
                engine = db.engine
                if event.contains(engine, "connect", sqlite_foreign_keys_on):
                    event.remove(engine, "connect", sqlite_foreign_keys_on)
                engine.dispose()


def count(model, *criteria) -> int:
    return db.session.scalar(select(func.count()).select_from(model).where(*criteria)) or 0


def committed_count(model, *criteria) -> int:
    """Rows visible to another connection, i.e. what is committed right now."""

    with db.engine.connect() as connection:
        return connection.execute(select(func.count()).select_from(model).where(*criteria)).scalar() or 0


class AppTestCase(ForeignKeysEnforced, unittest.TestCase):
    def setUp(self):
        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "FEATURE_PRIVATE_FLASHCARDS", "FEATURE_VOCABULARY_TRAINER",
                    "FLASHCARD_IMPORT_STORAGE_DIR")
        application.app.config.update(
            TESTING=True, FEATURE_PRIVATE_FLASHCARDS=True, FEATURE_VOCABULARY_TRAINER=True)
        self.storage = tempfile.TemporaryDirectory()
        self.previous_storage = application.app.config.get("FLASHCARD_IMPORT_STORAGE_DIR")
        application.app.config["FLASHCARD_IMPORT_STORAGE_DIR"] = self.storage.name
        super().setUp()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
            application.ensure_database()
        self.client = self.registered("owner")

    def tearDown(self):
        try:
            super().tearDown()
        finally:
            application.app.config["FLASHCARD_IMPORT_STORAGE_DIR"] = self.previous_storage
            self.storage.cleanup()

    def registered(self, username):
        client = application.app.test_client()
        client.post("/register", data={"username": username, "email": f"{username}@example.com",
                                       "password": "correct-horse-battery"})
        return client

    def user_id(self, username="owner") -> int:
        with application.app.app_context():
            return db.session.scalar(select(M.User.id).where(M.User.username == username))


class FlashcardDeletionTests(AppTestCase):
    """R2: a studied set or card can be deleted; other students' data stays."""

    def studied_set(self) -> dict:
        saved = self.client.post("/api/flashcards/sets", json={
            "title": "Die Zelle", "subject": "Biology", "cards": [
                {"type": "question_answer", "front": "Mitochondrium", "back": "Kraftwerk der Zelle"},
                {"type": "question_answer", "front": "Zellkern", "back": "Steuerzentrale"},
                {"type": "question_answer", "front": "Ribosom", "back": "Proteinsynthese"}]})
        self.assertEqual(saved.status_code, 201, saved.data)
        set_id = saved.get_json()["id"]
        active = self.client.post(f"/api/flashcards/sets/{set_id}/sessions", json={"mode": "flashcards"})
        self.assertEqual(active.status_code, 201, active.data)
        user_id = self.user_id()
        with application.app.app_context():
            cards = db.session.scalars(select(M.Flashcard).where(M.Flashcard.set_id == set_id)
                                       .order_by(M.Flashcard.position)).all()
            completed = M.FlashcardStudySession(
                user_id=user_id, flashcard_set_id=set_id, mode="test", status="completed", random_seed=7,
                total_items=len(cards), completed_at=M.utcnow())
            db.session.add(completed)
            db.session.flush()
            for position, card in enumerate(cards):
                db.session.add(M.FlashcardSessionItem(
                    session_id=completed.id, card_id=card.id, position=position, question_type="written",
                    prompt=card.front, correct_answer=card.back, answer_key=f"done:{completed.id}:{position}",
                    correct=True))
            db.session.add(M.GamePersonalBest(user_id=user_id, flashcard_set_id=set_id, mode="match", best_score=900))
            vocabulary_list = M.VocabularyList(owner_user_id=user_id, title="Die Zelle", source_language="de",
                                               target_language="en", flashcard_set_id=set_id)
            public = M.PublicFlashcardSet(creator_id=user_id, source_set_id=set_id, title="Die Zelle (public)",
                                          status="published")
            db.session.add_all([vocabulary_list, public])
            db.session.commit()
            ids = {"set": set_id, "cards": [card.id for card in cards], "completed": completed.id,
                   "active": active.get_json()["session"]["id"], "list": vocabulary_list.id,
                   "public": public.id, "public_updated": public.updated_at}
            self.assertGreater(count(M.FlashcardSessionItem, M.FlashcardSessionItem.session_id == ids["active"]), 0)
        return ids

    def test_delete_studied_set(self):
        ids = self.studied_set()
        response = self.client.delete(f"/api/flashcards/sets/{ids['set']}")
        self.assertEqual(response.status_code, 200, response.data)
        with application.app.app_context():
            self.assertIsNone(db.session.get(M.FlashcardSet, ids["set"]))
            self.assertEqual(count(M.Flashcard, M.Flashcard.set_id == ids["set"]), 0)
            self.assertEqual(count(M.FlashcardStudySession, M.FlashcardStudySession.flashcard_set_id == ids["set"]), 0)
            self.assertEqual(count(M.FlashcardSessionItem, M.FlashcardSessionItem.session_id.in_(
                [ids["active"], ids["completed"]])), 0)
            self.assertEqual(count(M.GamePersonalBest, M.GamePersonalBest.flashcard_set_id == ids["set"]), 0)
            vocabulary_list = db.session.get(M.VocabularyList, ids["list"])
            self.assertIsNotNone(vocabulary_list, "the vocabulary list stays")
            self.assertIsNone(vocabulary_list.flashcard_set_id)
            public = db.session.get(M.PublicFlashcardSet, ids["public"])
            self.assertIsNotNone(public, "the published copy is other students' data and stays")
            self.assertIsNone(public.source_set_id)
            self.assertEqual(M.as_utc(public.updated_at), M.as_utc(ids["public_updated"]),
                             "unlinking does not make the copy look edited")

    def test_remove_studied_card(self):
        ids = self.studied_set()
        cards = self.client.get(f"/api/flashcards/sets/{ids['set']}").get_json()["set"]["cards"]
        removed, kept = cards[0], cards[1:]
        with application.app.app_context():
            others_before = count(M.FlashcardSessionItem, M.FlashcardSessionItem.card_id.in_(
                [card["id"] for card in kept]))
        response = self.client.put(f"/api/flashcards/sets/{ids['set']}", json={"cards": kept})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([card["front"] for card in response.get_json()["set"]["cards"]], ["Zellkern", "Ribosom"])
        with application.app.app_context():
            self.assertIsNone(db.session.get(M.Flashcard, removed["id"]))
            self.assertEqual(count(M.FlashcardSessionItem, M.FlashcardSessionItem.card_id == removed["id"]), 0)
            self.assertEqual(count(M.FlashcardSessionItem, M.FlashcardSessionItem.card_id.in_(
                [card["id"] for card in kept])), others_before)
            self.assertIsNotNone(db.session.get(M.FlashcardStudySession, ids["completed"]), "the session stays")

    def test_another_user_gets_404_and_nothing_is_deleted(self):
        ids = self.studied_set()
        with application.app.app_context():
            before = (count(M.Flashcard), count(M.FlashcardStudySession), count(M.FlashcardSessionItem),
                      count(M.GamePersonalBest))
        stranger = self.registered("stranger")
        self.assertEqual(stranger.delete(f"/api/flashcards/sets/{ids['set']}").status_code, 404)
        self.assertEqual(stranger.put(f"/api/flashcards/sets/{ids['set']}", json={"cards": []}).status_code, 404)
        with application.app.app_context():
            self.assertIsNotNone(db.session.get(M.FlashcardSet, ids["set"]))
            self.assertEqual((count(M.Flashcard), count(M.FlashcardStudySession), count(M.FlashcardSessionItem),
                              count(M.GamePersonalBest)), before)
            self.assertEqual(db.session.get(M.VocabularyList, ids["list"]).flashcard_set_id, ids["set"])

    def test_delete_practised_vocabulary_list(self):
        # Same pattern, found by the PF5 audit: practice sessions and the import that created
        # the list reference it with no cascade.
        user_id = self.user_id()
        with application.app.app_context():
            vocabulary_list = M.VocabularyList(owner_user_id=user_id, title="Unité 1", source_language="fr",
                                               target_language="de")
            db.session.add(vocabulary_list)
            db.session.flush()
            entry = M.VocabularyEntry(list_id=vocabulary_list.id, source_language="fr", target_language="de",
                                      source_term="le chat", target_translation="die Katze")
            db.session.add(entry)
            db.session.flush()
            db.session.add(M.VocabularyStudyState(entry_id=entry.id, direction="source_to_target"))
            db.session.add(M.VocabularyPracticeSession(user_id=user_id, vocabulary_list_id=vocabulary_list.id,
                                                       direction="source_to_target"))
            created_by = M.VocabularyImport(owner_user_id=user_id, source_kind="text", source_language="fr",
                                            target_language="de", idempotency_key="list-import",
                                            status="generated", vocabulary_list_id=vocabulary_list.id)
            db.session.add(created_by)
            db.session.commit()
            list_id, entry_id, import_id = vocabulary_list.id, entry.id, created_by.id
        self.assertEqual(self.registered("stranger").delete(f"/api/vocabulary/lists/{list_id}").status_code, 404)
        response = self.client.delete(f"/api/vocabulary/lists/{list_id}")
        self.assertEqual(response.status_code, 200, response.data)
        with application.app.app_context():
            self.assertIsNone(db.session.get(M.VocabularyList, list_id))
            self.assertEqual(count(M.VocabularyEntry, M.VocabularyEntry.list_id == list_id), 0)
            self.assertEqual(count(M.VocabularyStudyState, M.VocabularyStudyState.entry_id == entry_id), 0)
            self.assertEqual(count(M.VocabularyPracticeSession,
                                   M.VocabularyPracticeSession.vocabulary_list_id == list_id), 0)
            self.assertIsNone(db.session.get(M.VocabularyImport, import_id).vocabulary_list_id)


class ImportCleanupTests(AppTestCase):
    """R3, R3b: expired and deleted photos take the rows that point at them, files last."""

    def document(self, user_id, *, expired=True, name="page.png") -> tuple[str, Path]:
        document_id = str(uuid.uuid4())
        storage_key = f"{user_id}/{document_id}/{name}"
        path = Path(self.storage.name) / storage_key
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\x89PNG fake")
        now = M.utcnow()
        with application.app.app_context():
            db.session.add(M.FlashcardImport(
                id=document_id, owner_user_id=user_id, source_type="image", original_filename=name,
                sanitized_filename=name, detected_mime_type="image/png", file_size=9, storage_key=storage_key,
                sha256=uuid.uuid4().hex * 2, expires_at=now - timedelta(minutes=1) if expired else now + timedelta(days=1)))
            db.session.commit()
        return document_id, path

    def vocabulary_rows(self, user_id, document_id, key) -> tuple[str, str]:
        with application.app.app_context():
            vocabulary_import = M.VocabularyImport(
                owner_user_id=user_id, flashcard_import_id=document_id, source_kind="file", source_language="fr",
                target_language="de", idempotency_key=key, status="ready_for_review")
            scan = M.VocabularyScan(owner_user_id=user_id, flashcard_import_id=document_id, source_language="fr",
                                    target_language="de")
            db.session.add_all([vocabulary_import, scan])
            db.session.commit()
            return vocabulary_import.id, scan.id

    def files_deleted_after_commit(self, document_id):
        """Patch the file store so each deletion first checks its row is committed away."""

        original = flashcard_import_store.delete_private
        checked = []

        def delete_private(root, storage_key):
            if document_id in storage_key:
                checked.append(committed_count(M.FlashcardImport, M.FlashcardImport.id == document_id))
            return original(root, storage_key)

        return patch.object(flashcard_import_store, "delete_private", side_effect=delete_private), checked

    def test_expired_imports_cleanup_with_fk_on(self):
        user_id = self.user_id()
        scanned, scanned_file = self.document(user_id)
        import_id, scan_id = self.vocabulary_rows(user_id, scanned, "expired")
        plain, plain_file = self.document(user_id)
        current, current_file = self.document(user_id, expired=False)
        current_import, current_scan = self.vocabulary_rows(user_id, current, "current")
        with application.app.app_context():
            self.assertEqual(application.cleanup_expired_flashcard_imports(), 2)
        with application.app.app_context():
            for document_id in (scanned, plain):
                self.assertIsNone(db.session.get(M.FlashcardImport, document_id))
            self.assertIsNone(db.session.get(M.VocabularyImport, import_id))
            self.assertIsNone(db.session.get(M.VocabularyScan, scan_id))
            self.assertIsNotNone(db.session.get(M.FlashcardImport, current))
            self.assertIsNotNone(db.session.get(M.VocabularyImport, current_import))
            self.assertIsNotNone(db.session.get(M.VocabularyScan, current_scan))
        self.assertFalse(scanned_file.exists())
        self.assertFalse(plain_file.exists())
        self.assertTrue(current_file.exists())

    def test_expired_files_are_deleted_only_after_the_commit(self):
        # No rows point at this photo, so only the order of file and commit is under test.
        user_id = self.user_id()
        document_id, path = self.document(user_id)
        patcher, checked = self.files_deleted_after_commit(document_id)
        with patcher, application.app.app_context():
            self.assertEqual(application.cleanup_expired_flashcard_imports(), 1)
        self.assertEqual(checked, [0], "the file went before its row was committed away")
        self.assertFalse(path.exists())

    def test_one_bad_document_does_not_stop_the_cleanup_for_everyone(self):
        user_id = self.user_id()
        bad, bad_file = self.document(user_id)
        good, good_file = self.document(user_id)
        original = application.remove_flashcard_import_dependents

        def dependents(document_id):
            if document_id == bad:
                raise SQLAlchemyError("simulated database error")
            return original(document_id)

        with patch.object(application, "remove_flashcard_import_dependents", side_effect=dependents), \
                application.app.app_context(), \
                self.assertLogs(application.app.logger, level="WARNING") as logs:
            self.assertEqual(application.cleanup_expired_flashcard_imports(), 1)
        self.assertIn(f"flashcard_import.cleanup_failed id={bad}", "\n".join(logs.output))
        with application.app.app_context():
            self.assertIsNotNone(db.session.get(M.FlashcardImport, bad), "rolled back, retried next time")
            self.assertIsNone(db.session.get(M.FlashcardImport, good))
        self.assertTrue(bad_file.exists())
        self.assertFalse(good_file.exists())

    def test_delete_vocabulary_import_with_fk_on(self):
        user_id = self.user_id()
        document_id, path = self.document(user_id, expired=False)
        import_id, scan_id = self.vocabulary_rows(user_id, document_id, "to-delete")
        self.assertEqual(self.registered("stranger").delete(f"/api/vocabulary/imports/{import_id}").status_code, 404)
        patcher, checked = self.files_deleted_after_commit(document_id)
        with patcher:
            response = self.client.delete(f"/api/vocabulary/imports/{import_id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(checked, [0], "the file went before its row was committed away")
        with application.app.app_context():
            self.assertIsNone(db.session.get(M.VocabularyImport, import_id))
            self.assertIsNone(db.session.get(M.VocabularyScan, scan_id))
            self.assertIsNone(db.session.get(M.FlashcardImport, document_id))
        self.assertFalse(path.exists())

    def test_deleting_an_import_document_keeps_the_rows_that_used_it(self):
        user_id = self.user_id()
        document_id, path = self.document(user_id, expired=False)
        import_id, scan_id = self.vocabulary_rows(user_id, document_id, "photo-only")
        patcher, checked = self.files_deleted_after_commit(document_id)
        with patcher:
            response = self.client.delete(f"/api/flashcards/imports/{document_id}")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(checked, [0])
        with application.app.app_context():
            self.assertIsNone(db.session.get(M.FlashcardImport, document_id))
            self.assertIsNone(db.session.get(M.VocabularyImport, import_id).flashcard_import_id)
            self.assertIsNone(db.session.get(M.VocabularyScan, scan_id).flashcard_import_id)
        self.assertFalse(path.exists())


class ScannerFlowWithForeignKeysTests(ForeignKeysEnforced, scanner_tests.ScannerFlowTests):
    """The scanner flow, including test_expired_photos_take_their_scans_with_them, under FK."""


class TextbookImportWithForeignKeysTests(ForeignKeysEnforced, textbook_tests.TextbookImportTests):
    """The photo import, including its rescan, under FK."""

    def test_the_old_photo_of_a_rescan_is_deleted_only_after_the_commit(self):
        created = self.upload()
        import_id = created["vocabulary_import"]["id"]
        first = {"rows": [textbook_tests.row("à travers", "durch"), textbook_tests.row("le vilage", "das Dorf")]}
        self.assertEqual(self.extract(import_id, textbook_tests.page_lines(first)).status_code, 200)
        with application.app.app_context():
            old_document = db.session.get(M.VocabularyImport, import_id).flashcard_import_id
        second = {"rows": [textbook_tests.row("à travers", "durch"), textbook_tests.row("le village", "das Dorf")]}
        original = flashcard_import_store.delete_private
        checked = []

        def delete_private(root, storage_key):
            checked.append(committed_count(M.FlashcardImport, M.FlashcardImport.id == old_document))
            return original(root, storage_key)

        with patch.object(application, "create_response", self.no_model), \
                patch.object(textbook_tests.local_ocr, "available", return_value=True), \
                patch.object(textbook_tests.local_ocr, "recognize",
                             return_value=textbook_tests.reading(textbook_tests.page_lines(second))), \
                patch.object(flashcard_import_store, "delete_private", side_effect=delete_private):
            rescanned = self.client.post(f"/api/vocabulary/imports/{import_id}/rescan", data={
                "file": (io.BytesIO(textbook_tests.photo_bytes("gray")), "page-again.png")},
                content_type="multipart/form-data")
        self.assertEqual(rescanned.status_code, 200, rescanned.data)
        self.assertEqual(checked, [0], "the old photo went before the switch was committed")
        with application.app.app_context():
            self.assertIsNone(db.session.get(M.FlashcardImport, old_document))
            self.assertNotEqual(db.session.get(M.VocabularyImport, import_id).flashcard_import_id, old_document)


if __name__ == "__main__":
    unittest.main()

"""Six problems reported from the phone, each pinned by a test.

1. A slot's answer format is applied to a generated question only when it has choices.
2. From the minimum onwards the student can finish the test and gets an honest summary.
3. A saved community copy opens as the student's own set (edit, modes, games).
4. Publishing needs only a title and the confirmation; vocabulary sets are marked, the
   library can filter by kind, and the reviewer is told the language pair.
5. A recall card the pages do not support is dropped, not fatal for the whole plan.
6. Confirming the recognition review continues into the lesson, not to a dashboard.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_release_fixes_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.test_community import REVIEW_HIGH, SET_CARDS, ai_responder  # noqa: E402
from tests.test_knowledge_gate_integration import KnowledgeGateIntegrationTests  # noqa: E402
from tests.test_quick_start import FakeResponse, image_bytes, recognition_payload, section_payload  # noqa: E402
from tests.test_quick_start import LESSON as PROJECT_LESSON  # noqa: E402


class FormatGuardTests(unittest.TestCase):
    def test_a_closed_format_without_choices_becomes_a_written_answer(self):
        bare = {"prompt": "Order the steps", "options": [], "expected_answer": ["a", "b"]}
        application.coerce_question_type(bare, "ordering")
        self.assertEqual((bare["type"], bare["options"], bare["expected_answer"]), ("text", [], "a, b"))
        with_choices = {"prompt": "Pick", "options": [{"id": "a", "label": "x"}, {"id": "b", "label": "y"}], "expected_answer": "a"}
        application.coerce_question_type(with_choices, "dropdown")
        self.assertEqual(with_choices["type"], "dropdown")
        written = {"prompt": "Explain"}
        application.coerce_question_type(written, "text")
        self.assertEqual((written["type"], written["options"]), ("text", []))


class LessonLoopFixTests(unittest.TestCase):
    """Driven through the knowledge-gate suite's helpers (every model call patched)."""

    def setUp(self):
        self.gate = KnowledgeGateIntegrationTests("test_a_student_who_knows_it_finishes_early_but_never_before_three")
        self.gate.setUp()
        self.client = self.gate.client

    def test_the_slot_format_is_not_forced_onto_a_question_without_choices(self):
        session_id = self.gate.start_lesson()
        # Slot 2 asks for checkboxes; the stubbed model writes a plain question with no options.
        data = self.gate.answer(session_id, score=100)
        self.assertEqual(data["next_question"]["type"], "text")
        self.assertEqual(data["next_question"]["options"], [])

    def test_the_student_can_finish_after_the_minimum_and_gets_an_honest_summary(self):
        session_id = self.gate.start_lesson()
        too_early = self.client.post("/api/finish", json={"session_id": session_id})
        self.assertEqual(too_early.status_code, 400)
        self.assertEqual(too_early.get_json()["code"], "finish_too_early")
        for _ in range(3):
            data = self.gate.answer(session_id, score=20)
            self.assertFalse(data["complete"])
        self.assertEqual(data["progress"]["knowledge"]["minimum"], 3)
        finished = self.client.post("/api/finish", json={"session_id": session_id})
        self.assertEqual(finished.status_code, 200, finished.get_data(as_text=True))
        body = finished.get_json()
        self.assertTrue(body["complete"])
        self.assertEqual(body["progress"]["knowledge"]["reason"], "student_finished")
        self.assertIn("You stopped after 3 questions", body["summary"]["overall"])
        self.assertTrue(body["summary"]["weaknesses"], "what the answers so far show, honestly")
        self.assertTrue(any("Today's Practice" in step for step in body["summary"]["next_steps"]))
        again = self.client.post("/api/finish", json={"session_id": session_id})
        self.assertEqual(again.status_code, 409)
        with application.app.app_context():
            saved = application.db.session.scalar(application.db.select(application.StudySession))
            self.assertTrue(json.loads(saved.state_json)["knowledge_gate"]["complete"])

    def test_a_finished_test_is_not_offered_for_resuming(self):
        session_id = self.gate.start_lesson()
        for _ in range(3):
            self.gate.answer(session_id, score=20)
        self.client.post("/api/finish", json={"session_id": session_id})
        self.assertNotIn("CONTINUE WHERE YOU LEFT OFF", self.client.get("/dashboard").data.decode())


class CommunityFixTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, AI_MODE="cached", FEATURE_COMMUNITY_LIBRARY=True,
                                      FEATURE_COMMUNITY_PUBLISHING=True, FEATURE_PRIVATE_FLASHCARDS=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.alice = application.app.test_client()
        self.alice.post("/register", data={"username": "alice", "email": "alice@example.com", "password": "correct-horse-battery"})
        self.bob = application.app.test_client()
        self.bob.post("/register", data={"username": "bob", "email": "bob@example.com", "password": "correct-horse-battery"})

    def create_set(self, client, **extra):
        saved = client.post("/api/flashcards/sets", json={"title": "Bio", "subject": "Biology", "difficulty": "medium",
                                                          "cards": SET_CARDS, **extra})
        self.assertEqual(saved.status_code, 201, saved.get_data(as_text=True))
        return saved.get_json()["id"]

    def publish(self, client, set_id, **fields):
        with ai_responder(REVIEW_HIGH) as mocked:
            response = client.post("/api/community/publish", json={"source_set_id": set_id, "confirm": True, **fields})
        return response, mocked

    def test_publishing_needs_only_a_title_and_the_confirmation(self):
        set_id = self.create_set(self.alice)
        response, _ = self.publish(self.alice, set_id, title="Cells")
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        with application.app.app_context():
            public = application.db.session.get(application.PublicFlashcardSet, body["id"])
            assert public is not None
            self.assertEqual((public.subject, public.difficulty, public.set_kind), ("Biology", "medium", "flashcards"),
                             "subject and level come from the set itself")

    def test_a_saved_copy_opens_as_your_own_set_with_edit_and_modes(self):
        set_id = self.create_set(self.alice)
        response, _ = self.publish(self.alice, set_id, title="Cells")
        public_id = response.get_json()["id"]
        saved = self.bob.post(f"/api/community/sets/{public_id}/save")
        self.assertEqual(saved.status_code, 201, saved.get_data(as_text=True))
        body = saved.get_json()
        self.assertEqual(body["redirect"], f"/flashcards/{body['id']}")
        page = self.bob.get(body["redirect"])
        self.assertEqual(page.status_code, 200)
        html = page.data.decode()
        self.assertIn(f"/flashcards/{body['id']}/edit", html, "the copy is editable")
        self.assertIn(f"/flashcards/{body['id']}/study", html, "and playable")
        self.assertEqual(self.alice.get(body["redirect"]).status_code, 404, "it is bob's set now, not alice's")

    def test_vocabulary_sets_are_marked_filterable_and_the_reviewer_knows_the_languages(self):
        set_id = self.create_set(self.alice, language="fr")
        with application.app.app_context():
            source = application.db.session.get(application.FlashcardSet, set_id)
            assert source is not None
            source.source_kind = "vocabulary"
            application.db.session.commit()
        response, mocked = self.publish(self.alice, set_id, title="Französisch Vokabeln")
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        review_call = next(call for call in mocked.call_args_list if call.kwargs.get("task_type") == "flashcard_review")
        prompt = review_call.kwargs["input"]
        self.assertIn('"set_kind": "vocabulary"', prompt)
        self.assertIn("judge every translation in that direction", prompt)
        self.assertIn("never assume English", prompt)
        plain_id = self.create_set(self.alice, title="Plain")
        self.publish(self.alice, plain_id, title="Plain flashcards")
        everything = self.alice.get("/api/community/library").get_json()["sets"]
        vocabulary = self.alice.get("/api/community/library?kind=vocabulary").get_json()["sets"]
        flashcards = self.alice.get("/api/community/library?kind=flashcards").get_json()["sets"]
        self.assertEqual(len(everything), 2)
        self.assertEqual([item["set_kind"] for item in vocabulary], ["vocabulary"])
        self.assertEqual([item["set_kind"] for item in flashcards], ["flashcards"])
        self.assertIn('name="kind"', self.alice.get("/community").data.decode())


class ProjectFlowFixTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, AI_MODE="cached")
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "zoe", "email": "zoe@example.com", "password": "correct-horse-battery"})

    def upload(self):
        response = self.client.post("/projects", data={
            "subject": "Biology", "materials": [(io.BytesIO(image_bytes("Mitochondria produce ATP")), "notes.png", "image/png")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            return project.id, [page.id for page in project.pages]

    def test_an_unsupported_recall_card_is_dropped_not_fatal(self):
        project_id, page_ids = self.upload()
        payload = section_payload(page_ids)
        payload["sections"][0]["recall_cards"] = [
            {"kind": "recall", "prompt": "Invented?", "answer": "Yes", "source_text": "THIS IS NOT ON THE PAGE"},
            {"kind": "recall", "prompt": "What do mitochondria produce?", "answer": "ATP", "source_text": "Mitochondria produce ATP"},
        ]
        with patch.object(application, "create_response", side_effect=[
                FakeResponse(recognition_payload("Mitochondria produce ATP")), FakeResponse(payload), FakeResponse(PROJECT_LESSON)]):
            response = self.client.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertIn("/?session_id=", response.headers["Location"], "the lesson still opens")
        with application.app.app_context():
            cards = application.db.session.scalars(application.db.select(application.RecallCard)).all()
            self.assertEqual([card.prompt for card in cards], ["What do mitochondria produce?"], "only the grounded card survives")

    def test_confirming_the_review_continues_into_the_lesson(self):
        project_id, page_ids = self.upload()
        with patch.object(application, "create_response", return_value=FakeResponse(recognition_payload("Mitochondria produce ATP"))):
            self.client.post(f"/projects/{project_id}/recognize")
        confirmed = self.client.post(f"/projects/{project_id}/review", data={"action": "confirm"})
        self.assertEqual(confirmed.status_code, 302)
        self.assertTrue(confirmed.headers["Location"].endswith(f"/projects/{project_id}/start?auto=1"), confirmed.headers["Location"])
        unreviewed = self.client.post(f"/projects/{project_id}/review", data={"action": "continue_unreviewed"})
        self.assertTrue(unreviewed.headers["Location"].endswith(f"/projects/{project_id}/start?auto=1"))


if __name__ == "__main__":
    unittest.main()

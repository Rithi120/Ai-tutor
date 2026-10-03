"""The one-tap project flow: upload -> start page -> (read, build, open) -> lesson, and
coming back to an unfinished lesson instead of starting over.

Every model call is patched. What is checked is the chain itself: the upload lands on the
start page, one request reads the pages, accepts the recognition, builds the sections and
opens a lesson on the first section; the same request later resumes the saved session;
and the Overview / New Lesson page offer that session back.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_quick_start_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = json.dumps(payload)


def image_bytes(label):
    image = Image.new("RGB", (1200, 1600), "white")
    ImageDraw.Draw(image).text((80, 100), label, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def recognition_payload(text):
    return {"blocks": [{
        "type": "handwriting", "content": text, "bbox": [0.05, 0.05, 0.95, 0.35], "confidence": 0.9,
        "crossed_out": False, "important_candidate": False, "teacher_highlight_candidate": False, "nearby_text": "",
    }], "detected_page_number": "1", "warning": ""}


def section_payload(page_ids):
    return {"sections": [{
        "title": "Mitochondria", "main_topic": "Mitochondria", "learning_goals": ["Explain ATP"],
        "important_facts": ["Mitochondria produce ATP"], "definitions": [], "formulas": [], "examples": [],
        "vocabulary": ["ATP"], "relationships": [], "likely_exam_questions": ["Why powerhouse?"],
        "source_page_ids": page_ids, "simple_explanation": "They make energy.",
        "standard_explanation": "Mitochondria produce ATP by aerobic respiration.",
        "detailed_explanation": "Double membrane organelles that produce ATP.", "estimated_minutes": 8,
        "recall_cards": [{"kind": "recall", "prompt": "What do mitochondria produce?", "answer": "ATP",
                          "source_text": "Mitochondria produce ATP"}],
    }]}


LESSON = {
    "lesson_title": "Mitochondria lesson", "detected_level": "standard",
    "concepts": [{"name": "Mitochondria", "evidence": "uploaded page"}],
    "explanation": "Mitochondria produce ATP.", "worked_example": {"problem": "Why powerhouse?", "steps": ["ATP"], "answer": "ATP"},
    "teacher_tips": [], "exceptions": [],
    "question": {"id": "q1", "concept": "Mitochondria", "difficulty": 1, "type": "multiple_choice",
                 "prompt": "What do mitochondria produce?", "hint": "Energy currency.",
                 "options": [{"id": "a", "label": "ATP"}, {"id": "b", "label": "DNA"}], "expected_answer": "a"},
}


class QuickStartTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, AI_MODE="cached")
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "zoe", "email": "zoe@example.com", "password": "correct-horse-battery"})

    def upload(self, **fields):
        data = {"materials": [(io.BytesIO(image_bytes("Mitochondria produce ATP")), "notes.png", "image/png")]}
        data.update(fields)
        return self.client.post("/projects", data=data, content_type="multipart/form-data")

    def project_and_pages(self):
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            return project.id, [page.id for page in project.pages], project.title, project.subject

    def quick_start(self, responses, **headers):
        with patch.object(application, "create_response", side_effect=responses) as mocked:
            response = self.client.post(f"/projects/{self.project_and_pages()[0]}/quick-start", headers=headers)
        return response, mocked

    def test_an_upload_needs_nothing_but_pages_and_lands_on_the_start_page(self):
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 302)
        project_id, page_ids, title, subject = self.project_and_pages()
        self.assertEqual(uploaded.headers["Location"].split("?")[0], f"/projects/{project_id}/start")
        self.assertIn("auto=1", uploaded.headers["Location"])
        self.assertEqual(subject, "Other")
        self.assertTrue(title.startswith("Notes ·"), title)
        page = self.client.get(f"/projects/{project_id}/start?auto=1").data.decode()
        self.assertIn('data-auto="true"', page)
        self.assertIn(f'data-page-ids="[{page_ids[0]}]"', page)
        self.assertIn("Start lesson", page)
        self.assertIn("Check the scan first (optional)", page)
        self.assertIn("project-start.js", page)

    def test_one_request_reads_builds_and_opens_the_lesson(self):
        self.upload(title="Biology notes", subject="Biology")
        project_id, page_ids, _, _ = self.project_and_pages()
        response, mocked = self.quick_start([
            FakeResponse(recognition_payload("Mitochondria produce ATP")),
            FakeResponse(section_payload(page_ids)),
            FakeResponse(LESSON),
        ])
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertIn("/?session_id=", response.headers["Location"])
        self.assertEqual([call.kwargs["task_type"] for call in mocked.call_args_list],
                         ["ocr_document_recognition", "project_section_generation", "lesson_generation"])
        session_id = response.headers["Location"].split("session_id=", 1)[1]
        state = application.SESSIONS[session_id]
        self.assertEqual(state["session_kind"], "section_test")
        self.assertEqual((state["min_questions"], state["max_questions"]), (3, 15))
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject).where(
                application.LearningProject.id == project_id))
            assert project is not None
            sections, pages = list(project.sections), list(project.pages)
            self.assertEqual(project.status, "planned")
            self.assertEqual(len(sections), 1)
            self.assertEqual(pages[0].review_status, "unreviewed", "accepted as it stands, marked honestly")
            lesson = application.db.session.scalar(application.db.select(application.Lesson))
            assert lesson is not None
            self.assertEqual(lesson.section_id, sections[0].id)
        lesson_page = self.client.get(response.headers["Location"]).data.decode()
        self.assertIn("Mitochondria lesson", lesson_page)

    def test_the_second_tap_resumes_the_unfinished_lesson_instead_of_starting_over(self):
        self.upload(subject="Biology")
        project_id, page_ids, _, _ = self.project_and_pages()
        first, _ = self.quick_start([
            FakeResponse(recognition_payload("Mitochondria produce ATP")),
            FakeResponse(section_payload(page_ids)), FakeResponse(LESSON)])
        session_id = first.headers["Location"].split("session_id=", 1)[1]
        application.SESSIONS.clear()          # a new process, a closed tab
        second, mocked = self.quick_start([])  # any model call would raise StopIteration
        self.assertEqual(second.status_code, 302, second.get_data(as_text=True))
        self.assertEqual(second.headers["Location"], f"/?session_id={session_id}")
        self.assertEqual(mocked.call_count, 0, "nothing was regenerated")
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.func.count(application.Lesson.id))), 1)

    def test_a_finished_lesson_is_not_resumed(self):
        self.upload(subject="Biology")
        project_id, page_ids, _, _ = self.project_and_pages()
        first, _ = self.quick_start([
            FakeResponse(recognition_payload("Mitochondria produce ATP")),
            FakeResponse(section_payload(page_ids)), FakeResponse(LESSON)])
        session_id = first.headers["Location"].split("session_id=", 1)[1]
        with application.app.app_context():
            saved = application.db.session.scalar(application.db.select(application.StudySession))
            state = json.loads(saved.state_json)
            state["knowledge_gate"] = {"complete": True}
            saved.state_json = json.dumps(state)
            application.db.session.commit()
        application.SESSIONS.clear()
        second, mocked = self.quick_start([FakeResponse(LESSON)])
        self.assertEqual(second.status_code, 302, second.get_data(as_text=True))
        self.assertNotEqual(second.headers["Location"], f"/?session_id={session_id}")
        self.assertEqual(mocked.call_count, 1, "only the new lesson was generated; sections were kept")

    def test_the_start_page_script_gets_json(self):
        self.upload(subject="Biology")
        project_id, page_ids, _, _ = self.project_and_pages()
        response, _ = self.quick_start([
            FakeResponse(recognition_payload("Mitochondria produce ATP")),
            FakeResponse(section_payload(page_ids)), FakeResponse(LESSON)], Accept="application/json")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertIn("/?session_id=", body["redirect"])

    def test_unreadable_pages_give_a_clear_error_not_a_lesson_about_nothing(self):
        self.upload(subject="Biology")
        response, _ = self.quick_start([
            FakeResponse({"blocks": [], "detected_page_number": "", "warning": "blank"})], Accept="application/json")
        self.assertEqual(response.status_code, 422)
        self.assertIn("None of the pages could be read", response.get_json()["error"])

    def test_the_overview_and_new_lesson_page_offer_the_unfinished_lesson_back(self):
        self.upload(subject="Biology")
        project_id, page_ids, _, _ = self.project_and_pages()
        first, _ = self.quick_start([
            FakeResponse(recognition_payload("Mitochondria produce ATP")),
            FakeResponse(section_payload(page_ids)), FakeResponse(LESSON)])
        session_id = first.headers["Location"].split("session_id=", 1)[1]
        overview = self.client.get("/dashboard").data.decode()
        self.assertIn("CONTINUE WHERE YOU LEFT OFF", overview)
        self.assertIn("Mitochondria lesson", overview)
        self.assertIn("Not started yet", overview)
        fresh = self.client.get("/").data.decode()
        self.assertIn("CONTINUE WHERE YOU LEFT OFF", fresh)
        # The banner is a plain link, so resuming must work as a GET (it used to be POST-only: 405).
        link = fresh.split('class="primary-button" href="')[1].split('"')[0]
        self.assertTrue(link.endswith("/resume"), link)
        followed = self.client.get(link)
        self.assertEqual(followed.status_code, 302, followed.get_data(as_text=True))
        self.assertEqual(followed.headers["Location"], f"/?session_id={session_id}")
        resumed = self.client.get(f"/?session_id={session_id}").data.decode()
        self.assertNotIn("CONTINUE WHERE YOU LEFT OFF", resumed, "not while that very lesson is open")
        project_page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("Continue lesson", project_page)
        self.assertIn(f"/projects/{project_id}/quick-start", project_page)


if __name__ == "__main__":
    unittest.main()

"""The exam autopilot inside the app, every model call patched.

Scan -> exam date -> start: the upload with an exam date lands on the start page, the
quick start builds sections, reads the competencies (and refuses an unsupported "covered"),
plans the days and opens the first lesson. The card says what today is, estimates the
grade honestly, and its button does the next optimal thing without being asked.
"""

import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_exam_autopilot_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

NOTES = "Mitochondria produce ATP by aerobic respiration. Mitochondria have a double membrane."


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


def recognition(text):
    return {"blocks": [{"type": "handwriting", "content": text, "bbox": [0.05, 0.05, 0.95, 0.35], "confidence": 0.9,
                        "crossed_out": False, "important_candidate": False, "teacher_highlight_candidate": False,
                        "nearby_text": ""}], "detected_page_number": "1", "warning": ""}


def sections(page_ids):
    def section(position, title, topic):
        return {"title": title, "main_topic": topic, "learning_goals": [f"Explain {topic}"],
                "important_facts": [NOTES], "definitions": [], "formulas": [], "examples": [], "vocabulary": [],
                "relationships": [], "likely_exam_questions": [f"Why {topic}?"], "source_page_ids": page_ids,
                "simple_explanation": f"{topic} simply.", "standard_explanation": f"{topic} in steps.",
                "detailed_explanation": f"{topic} in depth.", "estimated_minutes": 8,
                "recall_cards": [{"kind": "recall", "prompt": f"What about {topic}?", "answer": "ATP",
                                  "source_text": "Mitochondria produce ATP"}]}
    return {"sections": [section(1, "Mitochondria", "Mitochondria"), section(2, "Cell membrane", "Cell membrane")]}


def competencies(page_ids):
    return {"competencies": [
        {"statement": "I can explain how mitochondria produce ATP.", "topic": "Mitochondria", "subtopic": "ATP",
         "level": "intermediate", "importance": 3, "source_page_ids": page_ids, "coverage": "covered",
         "evidence": "Mitochondria produce ATP by aerobic respiration."},
        {"statement": "I can describe the structure of the cell membrane.", "topic": "Cell membrane", "subtopic": "",
         "level": "basic", "importance": 2, "source_page_ids": page_ids, "coverage": "covered",
         "evidence": "The membrane is a phospholipid bilayer."},     # not in the notes: must become missing
        {"statement": "I can compare osmosis and diffusion.", "topic": "Transport", "subtopic": "",
         "level": "advanced", "importance": 1, "source_page_ids": page_ids, "coverage": "missing", "evidence": ""},
    ]}


LESSON = {
    "lesson_title": "Mitochondria lesson", "detected_level": "standard",
    "concepts": [{"name": "Mitochondria", "evidence": "uploaded page"}],
    "explanation": "Mitochondria produce ATP.", "worked_example": {"problem": "Why?", "steps": ["ATP"], "answer": "ATP"},
    "teacher_tips": [], "exceptions": [],
    "question": {"id": "q1", "concept": "Mitochondria", "difficulty": 1, "type": "multiple_choice",
                 "prompt": "What do mitochondria produce?", "hint": "Energy.",
                 "options": [{"id": "a", "label": "ATP"}, {"id": "b", "label": "DNA"}], "expected_answer": "a"},
}


class ExamAutopilotTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, AI_MODE="cached")
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "nia", "email": "nia@example.com", "password": "correct-horse-battery"})
        self.exam_date = date.today() + timedelta(days=12)

    def upload(self, with_date=True):
        data = {"subject": "Biology", "title": "Cells",
                "materials": [(io.BytesIO(image_bytes(NOTES)), "cells.png", "image/png")]}
        if with_date:
            data["exam_date"] = self.exam_date.isoformat()
        response = self.client.post("/projects", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            return project.id, [page.id for page in project.pages]

    def route(self, page_ids, extra=None):
        payloads = {"ocr_document_recognition": recognition(NOTES), "project_section_generation": sections(page_ids),
                    "competency_extraction": competencies(page_ids), "lesson_generation": LESSON,
                    "adaptive_practice": LESSON}
        payloads.update(extra or {})
        self.calls = []

        def respond(*, task_type, **_kwargs):
            self.calls.append(task_type)
            if task_type not in payloads:
                raise AssertionError(f"unexpected AI task {task_type}")
            return FakeResponse(payloads[task_type])
        return respond

    def test_scan_exam_date_start_builds_the_whole_preparation(self):
        project_id, page_ids = self.upload()
        with patch.object(application, "create_response", self.route(page_ids)):
            response = self.client.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        self.assertIn("/?session_id=", response.headers["Location"], "the first lesson opens straight away")
        self.assertEqual(self.calls.count("competency_extraction"), 1)
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            rows = sorted(project.competencies, key=lambda item: item.statement)
            self.assertEqual(len(rows), 3)
            by_topic = {row.topic: row for row in rows}
            self.assertEqual(by_topic["Mitochondria"].coverage, "covered")
            self.assertEqual(by_topic["Cell membrane"].coverage, "missing",
                             "a covered claim whose quote is not in the notes is not believed")
            self.assertEqual(by_topic["Cell membrane"].evidence, "")
            self.assertIsNotNone(by_topic["Mitochondria"].section_id)
            self.assertIsNone(by_topic["Transport"].section_id, "a requirement with no section stays visible")
            self.assertIsNotNone(project.exam_prep)
            plan = [p for p in project.study_plans if p.status == "active"]
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0].exam_date, self.exam_date)
            self.assertTrue(plan[0].sessions, "a day-by-day schedule exists for the reminders and calendar")

    def test_the_card_says_what_today_is_and_estimates_honestly(self):
        project_id, page_ids = self.upload()
        with patch.object(application, "create_response", self.route(page_ids)):
            self.client.post(f"/projects/{project_id}/quick-start")
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("EXAM AUTOPILOT", page)
        self.assertIn("12 days remaining", page)
        self.assertIn("Estimated grade", page)
        self.assertIn("not a guarantee", page)
        self.assertIn("Your notes cover", page)
        overview = self.client.get("/dashboard").data.decode()
        self.assertIn("EXAM AUTOPILOT", overview)
        self.assertIn("Cells", overview)
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            card = application.exam_prep_card(project)
            assert card is not None
        self.assertEqual(card["grade_label"], "–", "no answers yet means no estimate, not a guess")
        # The first lesson on topic 1 exists but nothing is known yet: finish topic 1 first.
        self.assertEqual((card["action"]["kind"], card["action"]["title"]), ("practice", "Mitochondria"))
        self.assertEqual(card["topics"][0]["title"], "Mitochondria")
        self.assertTrue(card["topics"][0]["learned"])
        self.assertFalse(card["topics"][1]["learned"])
        self.assertEqual(card["coverage"]["covered_percent"], 33)

    def test_the_button_does_the_next_thing_and_the_estimate_explains_itself(self):
        project_id, page_ids = self.upload()
        with patch.object(application, "create_response", self.route(page_ids)):
            first = self.client.post(f"/projects/{project_id}/quick-start")
        session_id = first.headers["Location"].split("session_id=", 1)[1]
        # The first lesson is open and unfinished: "Start" resumes it rather than starting another.
        application.SESSIONS.clear()
        with patch.object(application, "create_response", self.route(page_ids)):
            resumed = self.client.post(f"/projects/{project_id}/autopilot/next")
        self.assertEqual(resumed.status_code, 302, resumed.get_data(as_text=True))
        self.assertEqual(resumed.headers["Location"], f"/?session_id={session_id}")
        self.assertEqual(self.calls, [], "nothing regenerated")
        # Mark topic 1 known in the knowledge model and finish its lesson: the next action moves on.
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            mastery = application.get_or_create_mastery(project.user_id, "Biology", "Mitochondria")
            mastery.mastery_score, mastery.attempts, mastery.evidence_weight = 92, 4, 4.0
            mastery.last_practised_at = application.utcnow()
            saved = application.db.session.scalar(application.db.select(application.StudySession))
            state = json.loads(saved.state_json)
            state["knowledge_gate"] = {"complete": True, "reached": True, "reason": "target_reached", "concepts": [
                    {"concept": "Mitochondria", "knowledge": 90, "confidence": 0.8, "known": True, "status": "known"},
                    {"concept": "Cell membrane", "knowledge": 90, "confidence": 0.8, "known": True, "status": "known"}]}
            saved.state_json = json.dumps(state)
            application.db.session.commit()
            card = application.exam_prep_card(project)
            assert card is not None
            self.assertTrue(card["topics"][0]["known"])
            self.assertEqual((card["action"]["kind"], card["action"]["title"]), ("learn", "Cell membrane"))
            self.assertNotEqual(card["grade_label"], "–")
            self.assertIn("untested_topics", card["grade"]["basis"], "one untested topic widens the range")
            self.assertTrue(card["reasons"], "the first estimate says it is the first")
            self.assertIsNone(card["weakness"], "a topic never taught is not a weakness yet - it is simply next")
        with patch.object(application, "create_response", self.route(page_ids)):
            second = self.client.post(f"/projects/{project_id}/autopilot/next")
        self.assertEqual(second.status_code, 302, second.get_data(as_text=True))
        self.assertIn("/?session_id=", second.headers["Location"])
        self.assertIn("lesson_generation", self.calls, "topic 2 is taught before it is tested")
        with application.app.app_context():
            lessons = application.db.session.scalars(application.db.select(application.Lesson)).all()
            self.assertEqual(sorted(lesson.section_id for lesson in lessons), sorted(
                [s.id for s in application.db.session.scalars(application.db.select(application.LearningSection)).all()]))

    def test_a_project_without_an_exam_date_offers_the_one_field_form(self):
        project_id, page_ids = self.upload(with_date=False)
        with patch.object(application, "create_response", self.route(page_ids)):
            self.client.post(f"/projects/{project_id}/quick-start")
        self.assertNotIn("competency_extraction", self.calls, "no exam date, no autopilot - just the lesson")
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("Enter the exam date. Learnova plans the rest.", page)
        self.assertIn(f"/projects/{project_id}/autopilot", page)
        with patch.object(application, "create_response", self.route(page_ids)):
            started = self.client.post(f"/projects/{project_id}/autopilot", data={"exam_date": self.exam_date.isoformat()},
                                       follow_redirects=True)
        self.assertEqual(started.status_code, 200)
        self.assertIn("EXAM AUTOPILOT", started.get_data(as_text=True))
        self.assertIn("12 days remaining", started.get_data(as_text=True))
        rejected = self.client.post(f"/projects/{project_id}/autopilot", data={"exam_date": "2001-01-01"}, follow_redirects=True)
        self.assertIn("Choose a future exam date.", rejected.get_data(as_text=True))

    def test_final_days_lead_to_a_mock_exam(self):
        project_id, page_ids = self.upload()
        with patch.object(application, "create_response", self.route(page_ids)):
            self.client.post(f"/projects/{project_id}/quick-start")
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            project.exam_date = date.today() + timedelta(days=1)
            application.db.session.commit()
            card = application.exam_prep_card(project)
            assert card is not None
        self.assertEqual(card["phase"], "final")
        self.assertEqual(card["action"]["kind"], "mock_exam")
        self.assertIn("mock exam", card["next_label"])


if __name__ == "__main__":
    unittest.main()

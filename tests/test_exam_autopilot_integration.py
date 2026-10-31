"""Exam preparation in Project Mode v2, every model call patched.

Scan -> exam date -> start: the upload with an exam date lands on the start page, the
quick start reads the pages and finds the topics, the student confirms them, and the
first lesson opens with a day-by-day plan behind it (no Kompetenzraster needed). The
workspace says what today is without inventing a grade, its Next button does the next
planned thing, a project without an exam date gets an ongoing plan, and the last days
before an exam lead to a practice exam.
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

    def begin(self, project_id, page_ids):
        with patch.object(application, "create_response", self.route(page_ids)):
            found = self.client.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(found.headers["Location"], f"/projects/{project_id}#topics")
        with patch.object(application, "create_response", self.route(page_ids)):
            started = self.client.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        self.assertEqual(started.status_code, 302, started.get_data(as_text=True))
        return started

    def test_scan_exam_date_start_builds_the_whole_preparation(self):
        project_id, page_ids = self.upload()
        response = self.begin(project_id, page_ids)
        self.assertIn("/?session_id=", response.headers["Location"], "the first lesson opens after confirming")
        self.assertNotIn("competency_extraction", self.calls, "notes alone are enough")
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            self.assertTrue(project.topics_confirmed)
            self.assertEqual([section.title for section in sorted(project.sections, key=lambda s: s.position)],
                             ["Mitochondria", "Cell membrane"])
            plan = [p for p in project.study_plans if p.status == "active"]
            self.assertEqual(len(plan), 1)
            self.assertEqual(plan[0].exam_date, self.exam_date)
            self.assertFalse(plan[0].flexible)
            self.assertTrue(plan[0].sessions, "a day-by-day schedule exists for the reminders and calendar")
            kinds = {task["kind"] for row in plan[0].sessions for task in json.loads(row.tasks_json)}
            self.assertLessEqual({"learn", "review", "mock_exam"}, kinds)
            minutes = [row.planned_minutes for row in plan[0].sessions]
            self.assertTrue(all(value <= 45 for value in minutes), "never more than the daily budget")

    def test_the_workspace_says_what_today_is_without_inventing_a_grade(self):
        project_id, page_ids = self.upload()
        self.begin(project_id, page_ids)
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("12 days left", page)
        self.assertIn("Learn: Mitochondria", page, "the open first lesson is the next thing")
        self.assertIn("0 of 2 topics learned", page)
        self.assertNotIn("Estimated grade", page, "no grade without a documented grading scale")
        overview = self.client.get("/dashboard").data.decode()
        self.assertIn("YOUR LEARNING PLAN", overview)
        self.assertIn("Cells", overview)

    def test_the_button_does_the_next_thing(self):
        project_id, page_ids = self.upload()
        first = self.begin(project_id, page_ids)
        session_id = first.headers["Location"].split("session_id=", 1)[1]
        # The first lesson is open and unfinished: Next resumes it rather than starting another.
        application.SESSIONS.clear()
        with patch.object(application, "create_response", self.route(page_ids)):
            resumed = self.client.post(f"/projects/{project_id}/next")
        self.assertEqual(resumed.status_code, 302, resumed.get_data(as_text=True))
        self.assertEqual(resumed.headers["Location"], f"/?session_id={session_id}")
        self.assertEqual(self.calls, [], "nothing regenerated")
        # Topic 1 is learned (its session finished): the next action moves on to topic 2.
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            first_topic = min(project.sections, key=lambda item: item.position)
            first_topic.learned_at = application.utcnow()
            first_topic.next_review_on = date.today() + timedelta(days=1)
            saved = application.db.session.scalar(application.db.select(application.StudySession))
            state = json.loads(saved.state_json)
            state["knowledge_gate"] = {"complete": True, "reached": True}
            saved.state_json = json.dumps(state)
            application.refresh_project_plans(project.user_id)
            application.db.session.commit()
            _, task = application.project_next_task(project)
            assert task is not None, "a next task is planned"
            self.assertEqual((task["kind"], task["title"]), ("learn", "Cell membrane"))
        with patch.object(application, "create_response", self.route(page_ids)):
            second = self.client.post(f"/projects/{project_id}/next")
        self.assertEqual(second.status_code, 302, second.get_data(as_text=True))
        self.assertIn("/?session_id=", second.headers["Location"])
        self.assertIn("lesson_generation", self.calls, "topic 2 is taught before it is tested")
        with application.app.app_context():
            lessons = application.db.session.scalars(application.db.select(application.Lesson)).all()
            self.assertEqual(sorted(lesson.section_id for lesson in lessons), sorted(
                [s.id for s in application.db.session.scalars(application.db.select(application.LearningSection)).all()]))

    def test_a_project_without_an_exam_date_gets_an_ongoing_plan_and_one_date_field(self):
        project_id, page_ids = self.upload(with_date=False)
        self.begin(project_id, page_ids)
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("No exam date: ongoing plan", page)
        self.assertIn(f"/projects/{project_id}/plan-settings", page)
        with application.app.app_context():
            plan = application.db.session.scalar(application.db.select(application.StudyPlan))
            self.assertTrue(plan.flexible)
            self.assertFalse([task for row in plan.sessions for task in json.loads(row.tasks_json)
                              if task["kind"] == "mock_exam"], "no practice exam without an exam")
        updated = self.client.post(f"/projects/{project_id}/plan-settings",
                                   data={"exam_date": self.exam_date.isoformat()}, follow_redirects=True)
        self.assertEqual(updated.status_code, 200)
        self.assertIn("12 days left", updated.get_data(as_text=True))
        rejected = self.client.post(f"/projects/{project_id}/plan-settings", data={"exam_date": "2001-01-01"},
                                    follow_redirects=True)
        self.assertIn("Choose a future exam date.", rejected.get_data(as_text=True))
        # The former autopilot route still works and leads to the same single plan.
        with patch.object(application, "create_response", self.route(page_ids)):
            self.client.post(f"/projects/{project_id}/autopilot", data={"exam_date": self.exam_date.isoformat()})
        with application.app.app_context():
            plans = application.db.session.scalars(application.db.select(application.StudyPlan).where(
                application.StudyPlan.status == "active")).all()
            self.assertEqual(len(plans), 1, "never a second plan for one project")

    def test_the_last_days_lead_to_a_practice_exam(self):
        project_id, page_ids = self.upload()
        self.begin(project_id, page_ids)
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            project.exam_date = date.today() + timedelta(days=2)
            application.refresh_project_plans(project.user_id)
            application.db.session.commit()
            rows = application.project_plan_rows(project)
            mock = [(day, task) for day, task in rows if task["kind"] == "mock_exam"]
            self.assertEqual(len(mock), 1)
            self.assertEqual(mock[0][0], date.today() + timedelta(days=1), "the day before the exam")


if __name__ == "__main__":
    unittest.main()

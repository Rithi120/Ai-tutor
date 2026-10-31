"""Pre-release checks: the complete flow end to end, and data isolation on the new routes.

Complete flow (every model call patched, Project Mode v2): upload with an exam date ->
one tap reads the pages and finds the topics -> the student confirms the topics and the
first lesson opens -> exercises with a wrong answer (diagnosis, re-teaching on the same
concept) and right answers until the knowledge gate is satisfied -> the topic moves onto
the review ladder and Next teaches topic 2 -> a practice exam is generated, taken and
submitted -> the results show the knowledge check and every topic's next review date.
No grade is invented: there is no documented grading scale for a practice exam.
"""

import io
import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_release_flow_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.test_exam_autopilot_integration import (  # noqa: E402
    LESSON, NOTES, FakeResponse, competencies, image_bytes, recognition, sections,
)
from tests.test_knowledge_gate_integration import diagnosis  # noqa: E402

A = "Mitochondria"


def question_about(number, concept=A):
    return {"id": f"q{number}", "subject": "Biology", "concept": concept, "difficulty": 2, "type": "text",
            "prompt": f"Explain part {number} of {concept}.", "hint": "One step.", "options": [],
            "expected_answer": f"ATP {number}"}


def evaluation(score, number):
    return {"evaluation": {"is_correct": score >= 80, "score": score, "feedback": "Checked.",
                           "correction": "" if score >= 80 else "Mitochondria produce ATP.",
                           "teacher_tip": "Name the organelle.", "exception_note": "",
                           "skill_status": "mastered" if score >= 80 else "needs_practice"},
            "next_question": question_about(number), "summary": None}


class ReleaseFlowTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, AI_MODE="cached", FEATURE_ADAPTIVE_DIAGNOSTICS=True,
                                      FEATURE_DIAGNOSTIC_VERIFICATION=True, TEST_MIN_QUESTIONS=3,
                                      TEST_MAX_QUESTIONS=15, EXAM_DIAGNOSIS_LIMIT=3)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.register(self.client, "alice")
        self.calls = []

    @staticmethod
    def register(client, name):
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery"})

    def upload(self, client=None):
        client = client or self.client
        response = client.post("/projects", data={
            "subject": "Biology", "title": "Cells", "exam_date": (date.today() + timedelta(days=10)).isoformat(),
            "materials": [(io.BytesIO(image_bytes(NOTES)), "cells.png", "image/png")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject).order_by(
                application.LearningProject.id.desc()))
            return project.id, [page.id for page in project.pages]

    def router(self, page_ids, **extra):
        payloads = {"ocr_document_recognition": recognition(NOTES), "project_section_generation": sections(page_ids),
                    "competency_extraction": competencies(page_ids), "lesson_generation": LESSON,
                    "diagnosis_verification": {"agrees": True, "reason": "supported", "better_tag": ""}}
        payloads.update(extra)

        def respond(*, task_type, **_kwargs):
            self.calls.append(task_type)
            payload = payloads.get(task_type)
            if payload is None:
                raise AssertionError(f"unexpected AI task {task_type}")
            return FakeResponse(payload() if callable(payload) else payload)
        return respond

    def answer(self, session_id, score):
        state = application.SESSIONS[session_id]
        current = state["current_question"]
        number = len(state["history"]) + 2
        counter = {"n": 100}

        def generated():
            counter["n"] += 1
            return {"question": question_about(counter["n"])}
        route = self.router([], answer_evaluation=evaluation(score, number), answer_diagnosis=diagnosis(score >= 80),
                            question_generation=generated, adaptive_practice=generated)
        given = current["expected_answer"] if score >= 80 else "nonsense"
        with patch.object(application, "create_response", route):
            response = self.client.post("/api/answer", json={"session_id": session_id, "answer": given,
                                                             "hints_used": False, "retry_count": 0,
                                                             "response_confidence": 50})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def start(self, project_id, page_ids):
        """Scan -> topics -> 'Start learning': returns the first lesson's session id."""
        with patch.object(application, "create_response", self.router(page_ids)):
            found = self.client.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(found.status_code, 302, found.get_data(as_text=True))
        self.assertEqual(found.headers["Location"], f"/projects/{project_id}#topics")
        with patch.object(application, "create_response", self.router(page_ids)):
            started = self.client.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        self.assertEqual(started.status_code, 302, started.get_data(as_text=True))
        return started.headers["Location"].split("session_id=", 1)[1]

    def test_the_complete_flow_from_upload_to_mock_exam(self):
        project_id, page_ids = self.upload()
        # 1. Scan -> topics -> start. No Kompetenzraster and no competency call are needed.
        session_id = self.start(project_id, page_ids)
        self.assertNotIn("competency_extraction", self.calls)

        # 2. Exercises: a wrong answer is diagnosed and re-taught on the same concept ...
        wrong = self.answer(session_id, 20)
        self.assertFalse(wrong["complete"])
        self.assertEqual(wrong["diagnosis"]["primary_tag"], "procedural_error")
        self.assertIn("correction", wrong["evaluation"])
        self.assertEqual(wrong["progress"]["knowledge"]["next_concept"], A, "remediation stays on the concept")
        # ... then right answers until the knowledge gate is satisfied - never after one.
        results = []
        for _ in range(15):
            data = self.answer(session_id, 100)
            results.append(data)
            if data["complete"]:
                break
        self.assertTrue(results[-1]["complete"], "the gated test finished")
        self.assertGreaterEqual(len(results) + 1, 3)
        self.assertTrue(results[-1]["progress"]["knowledge"]["reached"])
        self.assertTrue({"question_generation", "adaptive_practice"} & set(self.calls),
                        "after the mistake the confirming question was regenerated as a transfer task")

        # 3. The finished topic is on the review ladder; Next teaches topic 2.
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject).where(
                application.LearningProject.id == project_id))
            assert project is not None
            first = min(project.sections, key=lambda item: item.position)
            self.assertIsNotNone(first.learned_at)
            self.assertIsNotNone(first.next_review_on, "a spaced review is scheduled")
            _, task = application.project_next_task(project)
            assert task is not None, "a next task is planned"
            self.assertEqual((task["kind"], task["title"]), ("learn", "Cell membrane"))
        with patch.object(application, "create_response", self.router(page_ids)):
            second = self.client.post(f"/projects/{project_id}/next")
        self.assertIn("/?session_id=", second.headers["Location"])

        # 4. A practice exam over the chosen topics - generated, grounded, taken, submitted.
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject).where(
                application.LearningProject.id == project_id))
            assert project is not None
            included = sorted([s for s in project.sections if not s.excluded], key=lambda s: s.position)
            questions = []
            for section in included:
                for number in range(3):
                    questions.append({
                        "id": f"q{len(questions) + 1}", "section_id": section.id, "concepts": [section.main_topic],
                        "source_page_ids": application.json_value(section.source_page_ids_json),
                        "supporting_text": "Mitochondria produce ATP", "difficulty": ["easy", "medium", "hard"][number],
                        "question_type": "multiple_choice", "prompt": f"{section.title}: question {number + 1}",
                        # plain-string options and a label as the key: normalised to ids
                        "options": ["ATP", "DNA"], "expected_answer": "ATP", "explanation": "ATP.",
                    })
        with patch.object(application, "create_response", self.router(page_ids, final_exam_generation={"questions": questions})):
            exam_redirect = self.client.post(f"/projects/{project_id}/practice-exam")
        self.assertEqual(exam_redirect.status_code, 302, exam_redirect.get_data(as_text=True))
        self.assertIn("/exams/", exam_redirect.headers["Location"], exam_redirect.headers["Location"])
        exam_id = int(exam_redirect.headers["Location"].rstrip("/").split("/")[-1])
        take = self.client.get(f"/exams/{exam_id}").data.decode()
        self.assertNotIn("Expected answer", take, "the key never reaches the exam page")
        with application.app.app_context():
            ids = application.db.session.scalars(application.db.select(application.ExamQuestion.id).where(
                application.ExamQuestion.exam_id == exam_id)).all()
            keys = set(application.db.session.scalars(application.db.select(application.ExamQuestion.expected_answer).where(
                application.ExamQuestion.exam_id == exam_id)).all())
        self.assertEqual(keys, {"a"}, "the answer key is the option id the page submits")
        for index, question_id in enumerate(ids):
            self.client.post(f"/exams/{exam_id}/autosave", json={"question_id": question_id, "answer": "a" if index % 4 else "b"})
        submitted = self.client.post(f"/exams/{exam_id}/submit")
        self.assertEqual(submitted.status_code, 302)
        results_page = self.client.get(f"/exams/{exam_id}/results").data.decode()
        self.assertIn("KNOWLEDGE CHECK", results_page)
        with application.app.test_request_context():
            exam = application.db.session.get(application.FinalExam, exam_id)
            assert exam is not None
            result = json.loads(exam.result_json)
            self.assertEqual(len(result["section_scores"]), 2, "a result per topic")
            self.assertGreater(exam.score, 50)
            for section in exam.project.sections:
                self.assertIsNotNone(section.next_review_on, "the exam result moved every topic's review")

    def test_the_new_routes_are_isolated_between_accounts(self):
        project_id, page_ids = self.upload()
        session_id = self.start(project_id, page_ids)
        with application.app.app_context():
            section_id = application.db.session.scalars(application.db.select(application.LearningSection.id)).first()
        # A second account must see nothing of it.
        other = application.app.test_client()
        self.register(other, "bob")
        self.assertEqual(other.get(f"/projects/{project_id}/start").status_code, 404)
        self.assertEqual(other.get(f"/projects/{project_id}").status_code, 404)
        self.assertEqual(other.post(f"/projects/{project_id}/autopilot", data={"exam_date": "2031-01-01"}).status_code, 404)
        self.assertEqual(other.post(f"/projects/{project_id}/autopilot/next").status_code, 404)
        blocked = other.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual(blocked.status_code, 404)
        self.assertEqual(other.post(f"/projects/{project_id}/pages/{page_ids[0]}/recognize").status_code, 404)
        # Every Project Mode v2 route refuses a project that is not yours.
        for path, data in (
            (f"/projects/{project_id}/next", {}),
            (f"/projects/{project_id}/tasks/start", {"task_id": "learn:section:1:1"}),
            (f"/projects/{project_id}/topics/confirm", {"choice": "auto"}),
            (f"/projects/{project_id}/topics/{section_id}/start", {}),
            (f"/projects/{project_id}/topics/{section_id}/rename", {"title": "Hacked"}),
            (f"/projects/{project_id}/topics/{section_id}/toggle", {}),
            (f"/projects/{project_id}/topics/add", {"title": "Hacked"}),
            (f"/projects/{project_id}/pages/{page_ids[0]}/correct", {"text": "Hacked"}),
            (f"/projects/{project_id}/reanalyze", {}),
            (f"/projects/{project_id}/plan-settings", {"daily_minutes": "30"}),
            (f"/projects/{project_id}/practice-exam", {}),
        ):
            self.assertEqual(other.post(path, data=data).status_code, 404, path)
        with application.app.app_context():
            titles = set(application.db.session.scalars(application.db.select(application.LearningSection.title)).all())
            page_text = application.db.session.scalars(application.db.select(application.ProjectPage.extracted_text)).first()
        self.assertNotIn("Hacked", titles)
        self.assertNotEqual(page_text, "Hacked")
        self.assertEqual(other.get(f"/?session_id={session_id}").status_code, 200, "the page loads ...")
        self.assertNotIn("Mitochondria lesson", other.get(f"/?session_id={session_id}").data.decode(),
                         "... but another account's session is not resumed into it")
        overview = other.get("/dashboard").data.decode()
        self.assertNotIn("YOUR LEARNING PLAN", overview)
        self.assertNotIn("CONTINUE WHERE YOU LEFT OFF", overview)
        with application.app.app_context():
            bob = application.db.session.scalar(application.db.select(application.User).where(application.User.username == "bob"))
            assert bob is not None
            self.assertEqual(application.db.session.scalar(application.db.select(application.db.func.count(
                application.LearningProject.id)).where(application.LearningProject.user_id == bob.id)), 0)
        self.assertEqual(other.post("/api/answer", json={"session_id": session_id, "answer": "a"}).status_code, 404,
                         "answers to another account's session are refused")


if __name__ == "__main__":
    unittest.main()

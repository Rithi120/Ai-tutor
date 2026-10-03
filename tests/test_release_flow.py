"""Pre-release checks: the complete flow end to end, and data isolation on the new routes.

Complete flow (every model call patched): upload with an exam date -> one-tap start builds
sections, competencies and the plan and opens the lesson -> exercises with a wrong answer
(diagnosis, re-teaching on the same concept) and right answers until the knowledge gate is
satisfied -> the autopilot moves to the next topic -> once everything is known it generates
a mock exam -> the exam is taken and submitted -> the results page shows the knowledge
check and the grade estimate now rests on the mock exam too.
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
from study_projects import difficulty_distribution, proportional_section_counts  # noqa: E402
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

    def test_the_complete_flow_from_upload_to_mock_exam(self):
        project_id, page_ids = self.upload()
        # 1. Scan -> exam date -> start.
        with patch.object(application, "create_response", self.router(page_ids)):
            started = self.client.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(started.status_code, 302, started.get_data(as_text=True))
        session_id = started.headers["Location"].split("session_id=", 1)[1]
        self.assertEqual(self.calls.count("competency_extraction"), 1)

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

        # 3. The autopilot moves on to topic 2 and teaches it before testing it.
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject).where(
                application.LearningProject.id == project_id))
            assert project is not None
            card = application.exam_prep_card(project)
            assert card is not None
            self.assertTrue(card["topics"][0]["known"], card["topics"])
            self.assertEqual((card["action"]["kind"], card["action"]["title"]), ("learn", "Cell membrane"))
            self.assertNotEqual(card["grade_label"], "–")
        with patch.object(application, "create_response", self.router(page_ids)):
            second = self.client.post(f"/projects/{project_id}/autopilot/next")
        self.assertIn("/?session_id=", second.headers["Location"])

        # 4. With every topic known, the next action is a mock exam - generated, grounded, taken.
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject).where(
                application.LearningProject.id == project_id))
            assert project is not None
            for section in project.sections:
                mastery = application.get_or_create_mastery(project.user_id, "Biology", section.main_topic)
                mastery.mastery_score, mastery.attempts, mastery.evidence_weight = 90, 4, 4.0
                mastery.last_practised_at = application.utcnow()
            for saved in application.db.session.scalars(application.db.select(application.StudySession)).all():
                state = json.loads(saved.state_json)
                state["knowledge_gate"] = {"complete": True, "reached": True, "reason": "target_reached", "concepts": [
                    {"concept": "Mitochondria", "knowledge": 90, "confidence": 0.8, "known": True, "status": "known"},
                    {"concept": "Cell membrane", "knowledge": 90, "confidence": 0.8, "known": True, "status": "known"}]}
                saved.state_json = json.dumps(state)
            application.db.session.commit()
            card = application.exam_prep_card(project)
            assert card is not None
            self.assertEqual(card["action"]["kind"], "mock_exam", card["action"])
            included = sorted([s for s in project.sections if not s.excluded], key=lambda s: s.position)
            count = max(5, min(50, 4 * len(included) + 4))
            allocation = proportional_section_counts([{
                "id": s.id, "mastery_score": s.mastery_score,
                "importance": len(application.json_value(s.important_facts_json)) + len(application.json_value(s.formulas_json)),
            } for s in included], count)
            distribution = difficulty_distribution(count, "mixed")
            questions, position = [], 0
            difficulties = [d for d, n in distribution.items() for _ in range(n)]
            for section in included:
                for _ in range(allocation[section.id]):
                    questions.append({
                        "id": f"q{position + 1}", "section_id": section.id, "concepts": [section.main_topic],
                        "source_page_ids": application.json_value(section.source_page_ids_json),
                        "supporting_text": "Mitochondria produce ATP", "difficulty": difficulties[position],
                        "question_type": "multiple_choice", "prompt": f"Question {position + 1}",
                        "options": [{"id": "a", "label": "ATP"}, {"id": "b", "label": "DNA"}],
                        "expected_answer": "a", "explanation": "ATP.",
                    })
                    position += 1
        with patch.object(application, "create_response", self.router(page_ids, final_exam_generation={"questions": questions})):
            exam_redirect = self.client.post(f"/projects/{project_id}/autopilot/next")
        self.assertEqual(exam_redirect.status_code, 302, exam_redirect.get_data(as_text=True))
        self.assertIn("/exams/", exam_redirect.headers["Location"], exam_redirect.headers["Location"])
        exam_id = int(exam_redirect.headers["Location"].rstrip("/").split("/")[-1])
        take = self.client.get(f"/exams/{exam_id}")
        self.assertEqual(take.status_code, 200)
        with application.app.app_context():
            ids = application.db.session.scalars(application.db.select(application.ExamQuestion.id).where(
                application.ExamQuestion.exam_id == exam_id)).all()
        for index, question_id in enumerate(ids):
            self.client.post(f"/exams/{exam_id}/autosave", json={"question_id": question_id, "answer": "a" if index % 4 else "b"})
        submitted = self.client.post(f"/exams/{exam_id}/submit")
        self.assertEqual(submitted.status_code, 302)
        results_page = self.client.get(f"/exams/{exam_id}/results").data.decode()
        self.assertIn("KNOWLEDGE CHECK", results_page)
        with application.app.test_request_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject).where(
                application.LearningProject.id == project_id))
            assert project is not None
            card = application.exam_prep_card(project)
            assert card is not None
            self.assertIn("mock_exam", card["grade"]["basis"], "the estimate now rests on the mock exam too")
            self.assertIsNotNone(card["grade"]["mock_percent"])

    def test_the_new_routes_are_isolated_between_accounts(self):
        project_id, page_ids = self.upload()
        with patch.object(application, "create_response", self.router(page_ids)):
            first = self.client.post(f"/projects/{project_id}/quick-start")
        session_id = first.headers["Location"].split("session_id=", 1)[1]
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
        self.assertEqual(other.get(f"/?session_id={session_id}").status_code, 200, "the page loads ...")
        self.assertNotIn("Mitochondria lesson", other.get(f"/?session_id={session_id}").data.decode(),
                         "... but another account's session is not resumed into it")
        overview = other.get("/dashboard").data.decode()
        self.assertNotIn("EXAM AUTOPILOT", overview)
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

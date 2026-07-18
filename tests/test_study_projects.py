import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_study_project_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from study_projects import (  # noqa: E402
    clean_extracted_pages,
    deterministic_question_score,
    difficulty_distribution,
    normalize_section,
    proportional_section_counts,
)


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


LESSON = {
    "lesson_title": "Forces section test",
    "detected_level": "standard",
    "concepts": [{"name": "Newton's second law", "evidence": "uploaded page"}],
    "explanation": "Force equals mass times acceleration.",
    "worked_example": {"problem": "Find F", "steps": ["F = m × a", "F = 2 × 3"], "answer": "6 N"},
    "teacher_tips": ["Keep the unit."],
    "exceptions": [],
    "question": {
        "id": "q1", "concept": "Newton's second law", "difficulty": 1,
        "type": "multiple_choice", "prompt": "What is the force?", "hint": "Use F = m × a.",
        "options": [
            {"id": "a", "label": "6 N"}, {"id": "b", "label": "5 N"},
            {"id": "c", "label": "2 N"}, {"id": "d", "label": "3 N"},
        ],
        "expected_answer": "a",
    },
}


EVALUATION = {
    "evaluation": {
        "is_correct": True, "score": 100, "feedback": "Correct.", "correction": "",
        "teacher_tip": "Check the unit.", "exception_note": "", "skill_status": "mastered",
    },
    "next_question": None,
    "summary": {"overall": "Strong", "strengths": ["Formula"], "weaknesses": [], "next_steps": []},
}


def image_bytes(label, color="white"):
    image = Image.new("RGB", (1200, 1600), color)
    draw = ImageDraw.Draw(image)
    draw.text((80, 100), label, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def recognition_payload(text, block_type="handwriting", confidence=0.93):
    return {
        "blocks": [{
            "type": block_type, "content": text, "bbox": [0.05, 0.05, 0.95, 0.35],
            "confidence": confidence, "crossed_out": False,
            "important_candidate": block_type == "formula",
            "teacher_highlight_candidate": False, "nearby_text": "",
        }],
        "detected_page_number": "1", "warning": "",
    }


class StudyProjectHelperTests(unittest.TestCase):
    def test_cleanup_validation_distribution_and_deterministic_scoring(self):
        pages = clean_extracted_pages([
            "School header\nForce is measured in newtons.\nPage footer",
            "School header\nMass is measured in kilograms.\nPage footer",
            "School header\nAcceleration is m/s².\nPage footer",
        ])
        self.assertNotIn("School header", pages[0])
        self.assertNotIn("Page footer", pages[2])
        self.assertEqual(difficulty_distribution(10, "mixed"), {"easy": 3, "medium": 5, "hard": 2})
        allocation = proportional_section_counts([
            {"id": 1, "mastery_score": 10, "importance": 3},
            {"id": 2, "mastery_score": 90, "importance": 1},
        ], 8)
        self.assertEqual(sum(allocation.values()), 8)
        self.assertGreater(allocation[1], allocation[2])
        self.assertEqual(deterministic_question_score("calculation", "6 N", "F = 6 N"), 100)
        self.assertEqual(deterministic_question_score("true_false", "True", "false"), 0)
        with self.assertRaises(ValueError):
            normalize_section({"title": "Unsupported", "source_page_ids": [99]}, 1, {1, 2})


class StudyProjectIntegrationTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "alice", "email": "alice@example.com", "password": "correct-horse-battery",
        })

    def upload_and_process(self):
        uploaded = self.client.post("/projects", data={
            "title": "Mechanics test", "subject": "Physics", "exam_date": "2030-06-01",
            "materials": [
                (io.BytesIO(image_bytes("Newton's second law F = m × a")), "page-1.png", "image/png"),
                (io.BytesIO(image_bytes("Example 2 kg × 3 m/s² = 6 N", "#fffbe8")), "page-2.png", "image/png"),
            ],
        }, content_type="multipart/form-data")
        self.assertEqual(uploaded.status_code, 302)
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            page_ids = [item.id for item in sorted(project.pages, key=lambda item: item.page_order)]
            project_id = project.id
        section_payload = {"sections": [{
            "title": "Forces", "main_topic": "Newton's second law",
            "learning_goals": ["Calculate force"], "important_facts": ["F = m × a"],
            "definitions": ["Force: a push or pull"], "formulas": ["F = m × a"],
            "examples": ["2 kg × 3 m/s² = 6 N"], "vocabulary": ["force", "mass"],
            "relationships": ["More acceleration means more force at fixed mass"],
            "likely_exam_questions": ["Calculate F"], "source_page_ids": page_ids,
            "simple_explanation": "Force tells us how strongly motion changes.",
            "standard_explanation": "Step 1: identify mass.\nStep 2: identify acceleration.\nStep 3: multiply.",
            "detailed_explanation": "F = m × a follows the uploaded definition and example.",
            "estimated_minutes": 10,
            "recall_cards": [{
                "kind": "formula", "prompt": "State the force formula", "answer": "F = m × a",
                "source_text": "F = m × a",
            }],
        }]}
        with patch.object(application, "create_response", side_effect=[
            FakeResponse(recognition_payload("Newton's second law\nF = m × a", "formula")),
            FakeResponse(recognition_payload("Example: 2 kg × 3 m/s² = 6 N")),
        ]):
            recognized = self.client.post(f"/projects/{project_id}/recognize")
        self.assertEqual(recognized.status_code, 302)
        confirmed = self.client.post(f"/projects/{project_id}/review", data={"action": "confirm"})
        self.assertEqual(confirmed.status_code, 302)
        with patch.object(application, "create_response", return_value=FakeResponse(section_payload)):
            processed = self.client.post(f"/projects/{project_id}/process")
        self.assertEqual(processed.status_code, 302)
        with application.app.app_context():
            section = application.db.session.scalar(application.db.select(application.LearningSection))
            return project_id, page_ids, section.id

    def test_upload_reorder_process_section_test_persistence_and_user_isolation(self):
        project_id, page_ids, section_id = self.upload_and_process()
        project_page = self.client.get(f"/projects/{project_id}")
        self.assertEqual(project_page.status_code, 200)
        self.assertIn(b"Forces", project_page.data)
        reordered = self.client.post(
            f"/projects/{project_id}/pages/reorder", json={"page_ids": list(reversed(page_ids))}
        )
        self.assertEqual(reordered.status_code, 200)
        learning = self.client.get(f"/projects/{project_id}/sections/{section_id}?level=detailed")
        self.assertIn(b"F = m", learning.data)
        recall = self.client.post(
            f"/projects/{project_id}/sections/{section_id}/recall/1", data={"answer": "F = m × a"}
        )
        self.assertEqual(recall.status_code, 200)
        self.assertTrue(recall.get_json()["correct"])

        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            started = self.client.post(f"/projects/{project_id}/sections/{section_id}/test")
        session_id = started.headers["Location"].split("session_id=", 1)[1]
        self.assertIn("uploaded_source", json.dumps({"uploaded_source": application.SESSIONS[session_id]["source_context"]}))
        application.SESSIONS[session_id]["test_total"] = 1
        with patch.object(application, "create_response", return_value=FakeResponse(EVALUATION)):
            answered = self.client.post("/api/answer", json={"session_id": session_id, "answer": "a"})
        self.assertEqual(answered.status_code, 200, answered.get_data(as_text=True))
        with application.app.app_context():
            section = application.db.session.get(application.LearningSection, section_id)
            assert section is not None
            self.assertEqual(section.mastery_score, 100)
            self.assertEqual(section.status, "exam_ready")
            self.assertIsNotNone(section.completed_at)
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.func.count(application.Attempt.id))
            ), 1)

        self.client.post("/logout")
        self.client.post("/register", data={
            "username": "bob", "email": "bob@example.com", "password": "correct-horse-battery",
        })
        self.assertEqual(self.client.get(f"/projects/{project_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/projects/{project_id}/sections/{section_id}").status_code, 404)

    def test_exam_hides_answers_autosaves_is_idempotent_and_isolated(self):
        project_id, page_ids, section_id = self.upload_and_process()
        questions = []
        difficulties = ["easy", "easy", "medium", "medium", "hard"]
        for position in range(1, 6):
            questions.append({
                "section_id": section_id, "source_page_ids": page_ids,
                "supporting_text": "F = m × a", "difficulty": difficulties[position - 1],
                "question_type": "multiple_choice", "prompt": f"Force question {position}",
                "options": [
                    {"id": "a", "label": "6 N"}, {"id": "b", "label": "5 N"},
                    {"id": "c", "label": "3 N"}, {"id": "d", "label": "2 N"},
                ],
                "expected_answer": "a", "explanation": "The uploaded formula gives 6 N.",
            })
        with patch.object(application, "create_response", return_value=FakeResponse({"questions": questions})):
            created = self.client.post(f"/projects/{project_id}/exam/new", data={
                "question_count": "5", "duration_minutes": "20", "difficulty": "mixed",
                "section_ids": [str(section_id)], "question_types": ["multiple_choice"],
            })
        self.assertEqual(created.status_code, 302)
        exam_id = int(created.headers["Location"].rstrip("/").split("/")[-1])
        take_page = self.client.get(f"/exams/{exam_id}")
        self.assertEqual(take_page.status_code, 200)
        self.assertNotIn(b"Expected answer", take_page.data)
        self.assertNotIn(b"uploaded formula gives", take_page.data)
        with application.app.app_context():
            exam = application.db.session.get(application.FinalExam, exam_id)
            assert exam is not None
            first_question_id = application.db.session.scalar(
                application.db.select(application.ExamQuestion.id).where(
                    application.ExamQuestion.exam_id == exam.id
                ).order_by(application.ExamQuestion.position).limit(1)
            )
            assert first_question_id is not None
        saved = self.client.post(
            f"/exams/{exam_id}/autosave", json={"question_id": first_question_id, "answer": "a"}
        )
        self.assertEqual(saved.status_code, 200)

        submitted = self.client.post(f"/exams/{exam_id}/submit")
        self.assertEqual(submitted.status_code, 302)
        submitted_again = self.client.post(f"/exams/{exam_id}/submit")
        self.assertEqual(submitted_again.status_code, 302)
        result_page = self.client.get(f"/exams/{exam_id}/results")
        self.assertIn(b"Expected answer", result_page.data)
        with application.app.app_context():
            exam = application.db.session.get(application.FinalExam, exam_id)
            assert exam is not None
            self.assertEqual(exam.status, "submitted")
            self.assertEqual(exam.score, 20)
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.func.count(application.ExamAnswer.id)).where(
                    application.ExamAnswer.exam_id == exam.id
                )
            ), 5)
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.func.count(application.Lesson.id)).where(
                    application.Lesson.title.like("Exam review:%")
                )
            ), 1)

        application.SESSIONS.clear()
        self.assertEqual(self.client.get(f"/exams/{exam_id}/results").status_code, 200)
        self.client.post("/logout")
        self.client.post("/register", data={
            "username": "bob", "email": "bob@example.com", "password": "correct-horse-battery",
        })
        self.assertEqual(self.client.get(f"/exams/{exam_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/exams/{exam_id}/results").status_code, 404)

    def test_server_expiry_submits_saved_answers(self):
        project_id, page_ids, section_id = self.upload_and_process()
        difficulties = ["easy", "easy", "medium", "medium", "hard"]
        questions = [{
            "section_id": section_id, "source_page_ids": page_ids, "supporting_text": "F = m × a",
            "difficulty": difficulties[index], "question_type": "true_false", "prompt": f"Statement {index}",
            "options": ["True", "False"], "expected_answer": "True", "explanation": "Supported.",
        } for index in range(5)]
        with patch.object(application, "create_response", return_value=FakeResponse({"questions": questions})):
            created = self.client.post(f"/projects/{project_id}/exam/new", data={
                "question_count": "5", "duration_minutes": "5", "section_ids": [str(section_id)],
                "question_types": ["true_false"],
            })
        exam_id = int(created.headers["Location"].rstrip("/").split("/")[-1])
        with application.app.app_context():
            exam = application.db.session.get(application.FinalExam, exam_id)
            assert exam is not None
            exam.expires_at = application.utcnow() - timedelta(seconds=1)
            application.db.session.commit()
        expired = self.client.get(f"/exams/{exam_id}")
        self.assertEqual(expired.status_code, 302)
        self.assertIn("/results", expired.headers["Location"])


if __name__ == "__main__":
    unittest.main()

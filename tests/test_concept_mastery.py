import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_concept_mastery_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


LESSON = {
    "lesson_title": "Linear equations",
    "detected_level": "middle school",
    "concepts": [
        {"name": "Inverse operations", "evidence": "study goal"},
        {"name": "Balancing equations", "evidence": "study goal"},
    ],
    "explanation": "Use inverse operations on both sides.",
    "worked_example": {"problem": "x + 2 = 5", "steps": ["Subtract 2"], "answer": "3"},
    "teacher_tips": ["Check both sides."],
    "exceptions": [],
    "question": {
        "id": "q1",
        "subject": "Mathematics",
        "concept": "Inverse operations",
        "concepts": ["Inverse operations", "Balancing equations"],
        "difficulty": 3,
        "type": "multiple_choice",
        "prompt": "What keeps x + 2 = 5 balanced?",
        "hint": "Do the same operation on both sides.",
        "options": [
            {"id": "a", "label": "Subtract 2 from both sides"},
            {"id": "b", "label": "Add 2 to the left only"},
            {"id": "c", "label": "Multiply the right only"},
            {"id": "d", "label": "Change x"},
        ],
        "expected_answer": "a",
    },
}

EVALUATION = {
    "evaluation": {
        "is_correct": True,
        "score": 100,
        "feedback": "Correct.",
        "correction": "",
        "teacher_tip": "Check both sides.",
        "exception_note": "",
        "skill_status": "mastered",
    },
    "next_question": None,
    "summary": {"overall": "Good", "strengths": [], "weaknesses": [], "next_steps": []},
}


class FakeResponse:
    def __init__(self, value):
        self.output_text = json.dumps(value)
        self.usage = None


class ConceptMasteryIntegrationTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "alice",
            "email": "alice@example.com",
            "password": "correct-horse-battery",
            "language": "en",
        })

    def test_multi_concept_answer_history_dashboard_detail_and_isolation(self):
        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            analyzed = self.client.post(
                "/api/analyze",
                data={"study_goal": "Learn equations", "subject": "Mathematics"},
            )
        self.assertEqual(analyzed.status_code, 200)
        session_id = analyzed.get_json()["session_id"]
        application.SESSIONS[session_id]["test_total"] = 1
        with patch.object(application, "create_response", return_value=FakeResponse(EVALUATION)):
            answered = self.client.post("/api/answer", json={
                "session_id": session_id,
                "answer": "a",
                "hints_used": True,
                "retry_count": 2,
                "response_confidence": 85,
            })
        self.assertEqual(answered.status_code, 200, answered.get_data(as_text=True))

        with application.app.app_context():
            masteries = application.db.session.scalars(
                application.db.select(application.ConceptMastery)
                .order_by(application.ConceptMastery.concept)
            ).all()
            self.assertEqual([item.concept for item in masteries], [
                "Balancing equations", "Inverse operations"
            ])
            self.assertTrue(all(item.attempts == 1 for item in masteries))
            self.assertTrue(all(item.correct_attempts == 1 for item in masteries))
            self.assertTrue(all(item.confidence_trend > 50 for item in masteries))
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.func.count(application.MasteryHistory.id))
            ), 2)
            attempt = application.db.session.scalar(application.db.select(application.Attempt))
            self.assertEqual(json.loads(attempt.concepts_json), [
                "Inverse operations", "Balancing equations"
            ])
            self.assertEqual(attempt.retry_count, 2)
            self.assertEqual(attempt.response_confidence, 85)
            mastery_id = masteries[0].id

        dashboard = self.client.get("/dashboard")
        self.assertIn(b"Recent mastery improvements", dashboard.data)
        self.assertIn(b"Concepts due today", dashboard.data)
        detail = self.client.get(f"/concepts/{mastery_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Mastery history", detail.data)
        self.assertIn(b"Start targeted practice", detail.data)

        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            targeted = self.client.post(f"/concepts/{mastery_id}/practice")
        self.assertEqual(targeted.status_code, 302)
        self.assertIn("session_id=", targeted.headers["Location"])

        self.client.post("/logout")
        self.client.post("/register", data={
            "username": "bob",
            "email": "bob@example.com",
            "password": "correct-horse-battery",
            "language": "en",
        })
        self.assertEqual(self.client.get(f"/concepts/{mastery_id}").status_code, 404)
        self.assertEqual(self.client.post(f"/concepts/{mastery_id}/practice").status_code, 404)
        self.assertNotIn(b"Balancing equations", self.client.get("/dashboard").data)


def tearDownModule():
    with application.app.app_context():
        application.db.session.remove()
        application.db.engine.dispose()
    if TEST_DATABASE.exists():
        TEST_DATABASE.unlink()


if __name__ == "__main__":
    unittest.main()

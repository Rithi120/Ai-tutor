import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_reliability_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DATABASE.as_posix()}"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["GROQ_API_KEY"] = "test-key"

import app as application  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


LESSON = {
    "lesson_title": "Fractions reliability lesson",
    "detected_level": "beginner",
    "concepts": [{"name": "Equivalent fractions", "evidence": "study goal"}],
    "explanation": "Multiply numerator and denominator by the same number.",
    "worked_example": {
        "problem": "Make 1/2 equivalent",
        "steps": ["Multiply both parts by 2"],
        "answer": "2/4",
    },
    "teacher_tips": ["Check both parts use the same multiplier."],
    "exceptions": [],
    "question": {
        "id": "q1",
        "concept": "Equivalent fractions",
        "difficulty": 1,
        "type": "multiple_choice",
        "prompt": "Which fraction equals 1/2?",
        "hint": "Multiply both parts equally.",
        "options": [
            {"id": "a", "label": "2/4"},
            {"id": "b", "label": "2/3"},
            {"id": "c", "label": "3/4"},
            {"id": "d", "label": "1/3"},
        ],
        "expected_answer": "a",
    },
}


EVALUATION = {
    "evaluation": {
        "is_correct": False,
        "score": 40,
        "feedback": "Use the same multiplier for both parts.",
        "correction": "1/2 becomes 2/4.",
        "teacher_tip": "Use the same multiplier.",
        "exception_note": "",
        "skill_status": "mastered",
    },
    "next_question": {
        "id": "q2",
        "concept": "Equivalent fractions",
        "difficulty": 2,
        "type": "checkboxes",
        "prompt": "Select equivalents.",
        "hint": "Compare values.",
        "options": [
            {"id": "a", "label": "2/4"},
            {"id": "b", "label": "3/6"},
            {"id": "c", "label": "2/3"},
            {"id": "d", "label": "3/4"},
        ],
        "expected_answer": ["a", "b"],
    },
    "summary": None,
}


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def register(self, username, email, password="correct-horse-battery"):
        return self.client.post(
            "/register",
            data={"username": username, "email": email, "password": password},
            follow_redirects=True,
        )

    def logout(self):
        return self.client.post("/logout", follow_redirects=True)

    def login(self, identifier, password="correct-horse-battery"):
        return self.client.post(
            "/login",
            data={"identifier": identifier, "password": password},
            follow_redirects=True,
        )

    def test_tables_registration_hash_login_logout_and_duplicates(self):
        with application.app.app_context():
            tables = set(application.inspect(application.db.engine).get_table_names())
        self.assertTrue({
            "user", "lesson", "attempt", "concept_mastery", "study_session", "chat_message",
            "learning_project", "project_file", "project_page", "learning_section", "recall_card",
            "document_block", "final_exam", "exam_question", "exam_answer", "mastery_history",
        } <= tables)

        response = self.register("alice", "alice@example.com")
        self.assertEqual(response.status_code, 200)
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            assert user is not None
            self.assertNotEqual(user.password_hash, "correct-horse-battery")
            self.assertTrue(user.check_password("correct-horse-battery"))

        self.logout()
        duplicate_username = self.register("alice", "other@example.com")
        self.assertIn(b"username is already registered", duplicate_username.data)
        duplicate_email = self.register("other", "alice@example.com")
        self.assertIn(b"email already exists", duplicate_email.data)

        login = self.login("alice")
        self.assertIn(b"Learnova", login.data)
        logout = self.logout()
        self.assertIn(b"Welcome back", logout.data)
        protected = self.client.get("/dashboard")
        self.assertEqual(protected.status_code, 302)
        self.assertIn("/login", protected.headers["Location"])

    def test_existing_database_is_upgraded_with_unique_usernames(self):
        with application.app.app_context():
            application.db.drop_all()
            with application.db.engine.begin() as connection:
                connection.execute(application.text(
                    'CREATE TABLE "user" ('
                    'id INTEGER PRIMARY KEY, email VARCHAR(255) UNIQUE NOT NULL, '
                    'password_hash VARCHAR(255) NOT NULL, created_at DATETIME NOT NULL)'
                ))
                connection.execute(application.text(
                    'CREATE TABLE lesson ('
                    'id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, session_id VARCHAR(32) NOT NULL, '
                    'subject VARCHAR(80) NOT NULL, title VARCHAR(255) NOT NULL, content_json TEXT NOT NULL, '
                    'created_at DATETIME NOT NULL)'
                ))
                connection.execute(application.text(
                    'CREATE TABLE attempt ('
                    'id INTEGER PRIMARY KEY, lesson_id INTEGER NOT NULL, question TEXT NOT NULL, '
                    'concept VARCHAR(255) NOT NULL, student_answer TEXT NOT NULL, score INTEGER NOT NULL, '
                    'feedback TEXT NOT NULL, difficulty INTEGER NOT NULL, timestamp DATETIME NOT NULL)'
                ))
                connection.execute(application.text(
                    'CREATE TABLE concept_mastery ('
                    'id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject VARCHAR(80) NOT NULL, '
                    'concept VARCHAR(255) NOT NULL, attempts INTEGER NOT NULL, total_score INTEGER NOT NULL, '
                    'mastery_score FLOAT NOT NULL, updated_at DATETIME NOT NULL)'
                ))
                connection.execute(application.text(
                    'CREATE TABLE learning_project ('
                    'id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, title VARCHAR(255) NOT NULL, '
                    'subject VARCHAR(80) NOT NULL, exam_date DATE, status VARCHAR(30) NOT NULL, '
                    'created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)'
                ))
                connection.execute(application.text(
                    'CREATE TABLE project_file ('
                    'id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, original_filename VARCHAR(255) NOT NULL, '
                    'mime_type VARCHAR(100) NOT NULL, original_data BLOB NOT NULL, uploaded_at DATETIME NOT NULL)'
                ))
                connection.execute(application.text(
                    'CREATE TABLE project_page ('
                    'id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, file_id INTEGER NOT NULL, '
                    'page_number INTEGER NOT NULL, page_order INTEGER NOT NULL, extracted_text TEXT NOT NULL, '
                    'extraction_status VARCHAR(30) NOT NULL, warning TEXT NOT NULL, created_at DATETIME NOT NULL)'
                ))
                connection.execute(
                    application.text(
                        'INSERT INTO "user" (id, email, password_hash, created_at) '
                        'VALUES (1, :email, :password_hash, :created_at)'
                    ),
                    {
                        "email": "legacy@example.com",
                        "password_hash": application.generate_password_hash("legacy-password"),
                        "created_at": application.utcnow(),
                    },
                )
            application.ensure_database()
            columns = {column["name"] for column in application.inspect(application.db.engine).get_columns("user")}
            attempt_columns = {
                column["name"] for column in application.inspect(application.db.engine).get_columns("attempt")
            }
            mastery_columns = {
                column["name"] for column in application.inspect(application.db.engine).get_columns("concept_mastery")
            }
            lesson_columns = {
                column["name"] for column in application.inspect(application.db.engine).get_columns("lesson")
            }
            project_file_columns = {
                column["name"] for column in application.inspect(application.db.engine).get_columns("project_file")
            }
            project_page_columns = {
                column["name"] for column in application.inspect(application.db.engine).get_columns("project_page")
            }
            legacy = application.db.session.get(application.User, 1)
            self.assertTrue({"username", "preferred_language"} <= columns)
            self.assertTrue({
                "subject", "hints_used", "mastery_before", "mastery_after",
                "concepts_json", "retry_count", "response_confidence",
            } <= attempt_columns)
            self.assertTrue({
                "correct_attempts", "incorrect_attempts", "consecutive_correct",
                "consecutive_incorrect", "last_practised_at", "next_review_at",
                "difficulty_level", "status",
                "recent_mistake_count", "confidence_trend",
            } <= mastery_columns)
            for version in (
                "001_add_username", "002_add_understood_at", "003_add_adaptive_attempt_fields",
                "004_add_attempt_subject", "005_add_adaptive_mastery_fields",
                "006_link_lessons_to_sections", "007_add_document_recognition",
                "008_add_user_preferred_language",
                "009_add_query_path_indexes", "010_add_concept_level_tracking",
            ):
                self.assertIsNotNone(application.db.session.get(application.SchemaMigration, version))
            assert legacy is not None
            self.assertEqual(legacy.username, "user_1")
            self.assertEqual(legacy.preferred_language, "en")
            self.assertIn("section_id", lesson_columns)
            self.assertTrue({"source_kind", "sha256"} <= project_file_columns)
            self.assertTrue({
                "processed_data", "recognition_json", "recognition_confidence", "review_status",
                "processing_stage", "retry_count", "important", "teacher_highlighted", "excluded",
            } <= project_page_columns)
        response = self.login("legacy@example.com", "legacy-password")
        self.assertEqual(response.status_code, 200)

    def test_lesson_attempt_chat_translation_ownership_and_restart_persistence(self):
        self.register("alice", "alice@example.com")
        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            analyzed = self.client.post("/api/analyze", data={"study_goal": "Learn fractions", "subject": "Math"})
        self.assertEqual(analyzed.status_code, 200, analyzed.get_data(as_text=True))
        session_id = analyzed.get_json()["session_id"]

        with patch.object(application, "create_response", return_value=FakeResponse(EVALUATION)):
            answered = self.client.post("/api/answer", json={"session_id": session_id, "answer": "a"})
        self.assertEqual(answered.status_code, 200, answered.get_data(as_text=True))

        with patch.object(application, "create_response", return_value=FakeResponse("Ask yourself what multiplies both parts.")):
            chatted = self.client.post("/api/chat", json={"session_id": session_id, "message": "Why?"})
        self.assertEqual(chatted.status_code, 200, chatted.get_data(as_text=True))

        with patch.object(application, "create_response", return_value=FakeResponse({"translations": ["Brüche"]})):
            translated = self.client.post(
                "/api/translate", json={"session_id": session_id, "language": "German", "texts": ["Fractions"]}
            )
        self.assertEqual(translated.status_code, 200, translated.get_data(as_text=True))

        with application.app.app_context():
            alice = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            lesson = application.db.session.scalar(application.db.select(application.Lesson))
            attempt = application.db.session.scalar(application.db.select(application.Attempt))
            self.assertEqual(lesson.user_id, alice.id)
            self.assertEqual(attempt.lesson.user_id, alice.id)
            self.assertEqual(len(lesson.chat_messages), 2)
            lesson_id = lesson.id
            attempt_id = attempt.id

        alice_dashboard = self.client.get("/dashboard")
        self.assertIn(b"Mistake Notebook", alice_dashboard.data)
        self.assertIn(b"Which fraction equals 1/2?", alice_dashboard.data)
        self.assertIn(b"Use the same multiplier for both parts.", alice_dashboard.data)
        self.assertIn(b"40%", alice_dashboard.data)

        self.logout()
        self.register("bob", "bob@example.com")
        bob_dashboard = self.client.get("/dashboard")
        self.assertNotIn(b"Fractions reliability lesson", bob_dashboard.data)
        self.assertNotIn(b"Which fraction equals 1/2?", bob_dashboard.data)
        self.assertEqual(self.client.get(f"/lessons/{lesson_id}").status_code, 404)
        self.assertEqual(self.client.post(f"/mistakes/{attempt_id}/understood").status_code, 404)
        self.assertEqual(self.client.post(f"/mistakes/{attempt_id}/similar").status_code, 404)
        self.assertEqual(
            self.client.post("/api/answer", json={"session_id": session_id, "answer": "a"}).status_code,
            404,
        )

        self.logout()
        application.SESSIONS.clear()  # Simulate a new Flask worker/process.
        self.login("alice@example.com")
        dashboard = self.client.get("/dashboard")
        self.assertIn(b"Fractions reliability lesson", dashboard.data)
        resumed = self.client.post(f"/lessons/{lesson_id}/resume", follow_redirects=True)
        self.assertEqual(resumed.status_code, 200)
        self.assertIn(b"Fractions reliability lesson", resumed.data)

        marked = self.client.post(f"/mistakes/{attempt_id}/understood", follow_redirects=True)
        self.assertIn(b"original attempt remains", marked.data)
        with application.app.app_context():
            saved_attempt = application.db.session.get(application.Attempt, attempt_id)
            assert saved_attempt is not None
            self.assertIsNotNone(saved_attempt.understood_at)
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.func.count(application.Attempt.id))
            ), 1)
        understood = self.client.get("/dashboard?status=understood")
        self.assertIn(b"Which fraction equals 1/2?", understood.data)

        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            similar = self.client.post(f"/mistakes/{attempt_id}/similar")
        self.assertEqual(similar.status_code, 302)
        self.assertIn("session_id=", similar.headers["Location"])

        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            weakest = self.client.post("/practice-weak")
        self.assertEqual(weakest.status_code, 302)
        weak_session_id = weakest.headers["Location"].split("session_id=", 1)[1]
        self.assertEqual(application.SESSIONS[weak_session_id]["test_total"], 5)
        with patch.object(application, "create_response", return_value=FakeResponse(EVALUATION)):
            practice_answer = self.client.post(
                "/api/answer", json={"session_id": weak_session_id, "answer": "a"}
            )
        self.assertEqual(practice_answer.status_code, 200, practice_answer.get_data(as_text=True))
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.func.count(application.Attempt.id))
            ), 2)

    def test_validation_and_ai_error_responses_are_clear(self):
        bad_registration = self.register("x", "not-an-email", "short")
        self.assertIn(b"Username must be", bad_registration.data)
        self.register("alice", "alice@example.com")
        self.assertEqual(self.client.post("/api/analyze", data={}).status_code, 400)
        self.assertEqual(self.client.post("/api/chat", json={"session_id": "missing", "message": ""}).status_code, 404)
        with patch.object(application, "create_response", return_value=FakeResponse({"unexpected": True})):
            failed = self.client.post("/api/analyze", data={"study_goal": "test"})
        self.assertEqual(failed.status_code, 422)
        self.assertIn("could not be read reliably", failed.get_json()["error"])

    def test_adaptive_mastery_today_practice_isolation_and_restart(self):
        self.register("alice", "alice@example.com")
        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            analyzed = self.client.post(
                "/api/analyze", data={"study_goal": "Learn fractions", "subject": "Math"}
            )
        session_id = analyzed.get_json()["session_id"]
        correct_result = json.loads(json.dumps(EVALUATION))
        correct_result["evaluation"].update({"is_correct": True, "score": 100})
        with patch.object(application, "create_response", return_value=FakeResponse(correct_result)):
            answered = self.client.post(
                "/api/answer",
                json={"session_id": session_id, "answer": "a", "hints_used": False},
            )
        self.assertEqual(answered.status_code, 200, answered.get_data(as_text=True))
        with application.app.app_context():
            mastery = application.db.session.scalar(application.db.select(application.ConceptMastery))
            attempt = application.db.session.scalar(application.db.select(application.Attempt))
            self.assertEqual(mastery.mastery_score, 12)
            self.assertEqual(mastery.correct_attempts, 1)
            self.assertEqual(mastery.incorrect_attempts, 0)
            self.assertEqual(mastery.status, "weak")
            self.assertEqual(attempt.mastery_before, 0)
            self.assertEqual(attempt.mastery_after, 12)
            self.assertFalse(attempt.hints_used)

        today = self.client.get("/practice/today")
        self.assertIn(b"Today", today.data)
        self.assertIn(b"Equivalent fractions", today.data)
        with patch.object(application, "create_response", return_value=FakeResponse(LESSON)):
            started = self.client.post("/practice/today/start")
        self.assertEqual(started.status_code, 302)
        adaptive_session_id = started.headers["Location"].split("session_id=", 1)[1]
        self.assertGreaterEqual(application.SESSIONS[adaptive_session_id]["test_total"], 5)
        self.assertLessEqual(application.SESSIONS[adaptive_session_id]["test_total"], 10)
        application.SESSIONS[adaptive_session_id]["test_total"] = 1
        with patch.object(application, "create_response", return_value=FakeResponse(correct_result)):
            completed = self.client.post(
                "/api/answer", json={"session_id": adaptive_session_id, "answer": "a"}
            )
        self.assertEqual(completed.status_code, 200, completed.get_data(as_text=True))
        practice_results = completed.get_json()["practice_results"]
        self.assertEqual(practice_results["concepts_practised"], ["Equivalent fractions"])
        self.assertTrue(practice_results["mastery_changes"])
        self.assertIsNotNone(practice_results["next_recommended_review_date"])
        self.assertTrue(practice_results["recommended_next_action"])

        self.logout()
        self.register("bob", "bob@example.com")
        self.assertNotIn(b"Equivalent fractions", self.client.get("/practice/today").data)
        self.assertEqual(
            self.client.post(
                "/api/answer", json={"session_id": adaptive_session_id, "answer": "a"}
            ).status_code,
            404,
        )

        self.logout()
        application.SESSIONS.clear()
        self.login("alice@example.com")
        persisted = self.client.get("/dashboard")
        self.assertIn(b"Equivalent fractions", persisted.data)
        with application.app.app_context():
            mastery = application.db.session.scalar(application.db.select(application.ConceptMastery))
            self.assertEqual(mastery.mastery_score, 24)
            self.assertEqual(mastery.attempts, 2)


def tearDownModule():
    with application.app.app_context():
        application.db.session.remove()
        application.db.engine.dispose()
    if TEST_DATABASE.exists():
        TEST_DATABASE.unlink()


if __name__ == "__main__":
    unittest.main()

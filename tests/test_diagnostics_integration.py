"""End-to-end wiring tests for the diagnostics engine inside the Flask app.

These exercise the real /api/answer path with every AI call patched, so they cover the
parts unit tests cannot: persistence of the new columns, the prerequisite graph, the
progressive-disclosure endpoint, ownership checks, and backward compatibility of the
existing response shape.
"""

import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_diagnostics_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

LESSON = {
    "lesson_title": "Linear equations",
    "detected_level": "starting",
    "concepts": [{"name": "Linear equations", "evidence": "goal"}],
    "explanation": "Undo the addition, then the multiplication.",
    "worked_example": {"problem": "2x + 3 = 11", "steps": ["subtract 3"], "answer": "x = 4"},
    "teacher_tips": [], "exceptions": [],
    "question": {
        "id": "q1", "concept": "Linear equations", "difficulty": 1,
        "type": "multiple_choice", "prompt": "Solve 2x + 3 = 11.", "hint": "Subtract 3 first.",
        "options": [{"id": "a", "label": "4"}, {"id": "b", "label": "7"}],
        "expected_answer": "a",
    },
}

EVALUATION = {
    "evaluation": {"is_correct": False, "score": 40, "feedback": "Check the sign.",
                   "correction": "2x = 8, so x = 4.", "teacher_tip": "Write the step out.",
                   "exception_note": "", "skill_status": "needs_practice"},
    "next_question": {
        "id": "q2", "concept": "Linear equations", "difficulty": 1, "type": "checkboxes",
        "prompt": "Which steps solve 3x + 2 = 14?", "hint": "Two steps.",
        "options": [{"id": "a", "label": "subtract 2"}, {"id": "b", "label": "divide by 3"}],
        "expected_answer": ["a", "b"],
    },
    "summary": None,
}

DIAGNOSIS = {
    "analysis_version": "diagnosis:v2",
    "correctness_status": "incorrect",
    "score": {"points": 0.4, "max_points": 1.0, "rubric_evidence": [
        {"criterion": "Chose to undo the addition", "met": True, "evidence_ids": ["e1"]}]},
    "concepts_assessed": ["Linear equations"],
    "primary_diagnosis": {"tag": "prerequisite_gap",
                          "statement": "Signed subtraction is the step that failed.",
                          "evidence_ids": ["e2"]},
    "secondary_diagnoses": [],
    "evidence": [
        {"id": "e1", "source": "student_answer", "quote": "remove the 3 first"},
        {"id": "e2", "source": "student_answer", "quote": "11 + 3 = 7"},
    ],
    "prerequisite_gaps": [
        {"concept": "Adding and subtracting signed integers", "evidence_ids": ["e2"]}],
    "missing_evidence": False,
    "confidence": {"value": 0.85, "basis": "The step is written out."},
    "recommended_intervention": "reteach_prerequisite",
    "candidate_question_constraints": {"concept": "Linear equations", "difficulty": 1},
    "student_facing_explanation": "Moving 3 across the equals sign makes it minus 3.",
    "internal_diagnostic_summary": "Teacher-only note about signed subtraction.",
}


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload)


class DiagnosticsIntegrationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        # Every AI call in this suite is patched at app.create_response, so the mode
        # only has to be one the gateway accepts.
        application.app.config.update(
            TESTING=True, FEATURE_ADAPTIVE_DIAGNOSTICS=True,
            FEATURE_DIAGNOSTIC_VERIFICATION=True, AI_MODE="cached")
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "diag", "email": "diag@example.com",
            "password": "correct-horse-battery", "language": "en",
        }, follow_redirects=True)
        self.responses = {}

    def route(self, *, task_type, **_kwargs):
        """Stand in for the gateway, returning the payload registered for each task."""
        if task_type not in self.responses:
            raise AssertionError(f"unexpected AI task type: {task_type}")
        return FakeResponse(self.responses[task_type])

    def start_lesson(self):
        self.responses = {"lesson_generation": LESSON}
        with mock.patch.object(application, "create_response", self.route):
            response = self.client.post("/api/analyze", data={
                "study_goal": "Solve linear equations", "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["session_id"]

    def answer(self, session_id, answer="b", diagnosis=None, evaluation=None):
        self.responses = {
            "answer_evaluation": evaluation or EVALUATION,
            "answer_diagnosis": diagnosis or DIAGNOSIS,
            "diagnosis_verification": {"agrees": True, "reason": "supported", "better_tag": ""},
            "question_generation": {"question": {
                "id": "q2", "subject": "Mathematics", "concept": "Linear equations",
                "difficulty": 1, "cognitive_demand": "recall", "type": "checkboxes",
                "prompt": "Which two steps isolate x in 3x + 2 = 14?", "hint": "Two steps.",
                "options": [{"id": "a", "label": "subtract 2"}, {"id": "b", "label": "divide by 3"}],
                "expected_answer": ["a", "b"], "solution_steps": ["subtract 2", "divide by 3"],
                "rubric": [{"criterion": "Both steps named", "points": 1}],
            }},
        }
        with mock.patch.object(application, "create_response", self.route):
            return self.client.post("/api/answer", json={
                "session_id": session_id, "answer": answer, "hints_used": False,
                "retry_count": 0, "response_confidence": 50,
            })

    # ------------------------------------------------------------------ response shape

    def test_existing_response_fields_are_unchanged(self):
        response = self.answer(self.start_lesson())
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        for key in ("ok", "evaluation", "analysis", "next_question", "complete", "progress"):
            self.assertIn(key, data)
        self.assertIn("feedback", data["evaluation"])
        self.assertEqual(data["analysis"]["verdict"], "incorrect")
        self.assertIn("missing_prerequisite", data["analysis"]["mistake_categories"])

    def test_response_carries_the_student_safe_diagnosis_and_plan(self):
        data = self.answer(self.start_lesson()).get_json()
        diagnosis = data["diagnosis"]
        self.assertEqual(diagnosis["primary_tag"], "prerequisite_gap")
        self.assertTrue(diagnosis["primary_label"])
        self.assertTrue(diagnosis["explanation"])
        self.assertEqual(data["plan"]["action"], "prerequisite_reteach")
        self.assertIn(data["plan"]["difficulty"], (1, 2, 3))

    def test_internal_fields_never_reach_the_browser(self):
        body = self.answer(self.start_lesson()).get_data(as_text=True)
        self.assertNotIn("Teacher-only note", body)
        self.assertNotIn("internal_diagnostic_summary", body)

    def test_next_question_hides_the_expected_answer(self):
        data = self.answer(self.start_lesson()).get_json()
        self.assertIsNotNone(data["next_question"])
        self.assertNotIn("expected_answer", data["next_question"])

    # ---------------------------------------------------------------------- persistence

    def test_attempt_stores_the_versioned_diagnosis(self):
        self.answer(self.start_lesson())
        with application.app.app_context():
            attempt = application.db.session.scalar(
                application.db.select(application.Attempt))
            self.assertEqual(attempt.diagnosis_version, "diagnosis:v2")
            self.assertEqual(attempt.primary_diagnosis, "prerequisite_gap")
            self.assertEqual(attempt.next_action, "prerequisite_reteach")
            self.assertFalse(attempt.missing_evidence)
            stored = json.loads(attempt.diagnosis_json)
            self.assertEqual(stored["primary_diagnosis"]["tag"], "prerequisite_gap")
            # The legacy column is still populated for existing readers.
            self.assertEqual(attempt.verdict, "incorrect")
            self.assertTrue(attempt.root_cause)

    def test_evidence_weight_and_uncertainty_are_maintained(self):
        session_id = self.start_lesson()
        self.answer(session_id)
        with application.app.app_context():
            mastery = application.db.session.scalar(
                application.db.select(application.ConceptMastery))
            self.assertGreater(mastery.evidence_weight, 0)
            self.assertLess(mastery.uncertainty, 1.0)
            self.assertEqual(mastery.last_action, "prerequisite_reteach")

    def test_mastery_history_records_an_explanation(self):
        self.answer(self.start_lesson())
        with application.app.app_context():
            history = application.db.session.scalar(
                application.db.select(application.MasteryHistory))
            self.assertTrue(history.reason)
            self.assertIn("Mastery", history.reason)

    def test_prerequisite_edges_accumulate_and_need_repeat_evidence(self):
        session_id = self.start_lesson()
        self.answer(session_id)
        with application.app.app_context():
            edge = application.db.session.scalar(
                application.db.select(application.ConceptPrerequisite))
            self.assertEqual(edge.prerequisite, "Adding and subtracting signed integers")
            self.assertEqual(edge.evidence_count, 1)
            from learnova.diagnostics import confirmed_prerequisites
            self.assertEqual(confirmed_prerequisites(
                [{"concept": edge.concept, "prerequisite": edge.prerequisite,
                  "evidence_count": edge.evidence_count, "confidence": edge.confidence}]), [])
        self.answer(session_id)
        with application.app.app_context():
            edge = application.db.session.scalar(
                application.db.select(application.ConceptPrerequisite))
            self.assertEqual(edge.evidence_count, 2)

    def test_flawed_question_does_not_reduce_mastery(self):
        session_id = self.start_lesson()
        flawed = dict(DIAGNOSIS)
        flawed["correctness_status"] = "correct"
        flawed["primary_diagnosis"] = {
            "tag": "question_or_key_flawed", "statement": "The stored answer is wrong.",
            "evidence_ids": ["e2"]}
        flawed["prerequisite_gaps"] = []
        self.answer(session_id, diagnosis=flawed)
        with application.app.app_context():
            mastery = application.db.session.scalar(
                application.db.select(application.ConceptMastery))
            self.assertEqual(mastery.mastery_score, 0)
            self.assertEqual(mastery.attempts, 0)
            history = application.db.session.scalar(
                application.db.select(application.MasteryHistory))
            self.assertIn("not changed", history.reason)

    # ------------------------------------------------------- progressive disclosure API

    def test_detail_endpoint_returns_the_safe_view(self):
        self.answer(self.start_lesson())
        with application.app.app_context():
            attempt_id = application.db.session.scalar(
                application.db.select(application.Attempt.id))
        response = self.client.get(f"/api/diagnosis/{attempt_id}")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["legacy"])
        detail = payload["diagnosis"]["detail"]
        self.assertIn("Adding and subtracting signed integers", detail["prerequisite_gaps"])
        self.assertTrue(detail["evidence"])
        self.assertNotIn("Teacher-only note", response.get_data(as_text=True))

    def test_detail_endpoint_requires_login_and_ownership(self):
        self.answer(self.start_lesson())
        with application.app.app_context():
            attempt_id = application.db.session.scalar(
                application.db.select(application.Attempt.id))
        self.client.post("/logout")
        anonymous = self.client.get(f"/api/diagnosis/{attempt_id}")
        self.assertIn(anonymous.status_code, (302, 401))
        self.client.post("/register", data={
            "username": "other", "email": "other@example.com",
            "password": "correct-horse-battery", "language": "en"}, follow_redirects=True)
        self.assertEqual(self.client.get(f"/api/diagnosis/{attempt_id}").status_code, 404)

    def test_detail_endpoint_falls_back_for_pre_v2_attempts(self):
        self.answer(self.start_lesson())
        with application.app.app_context():
            attempt = application.db.session.scalar(
                application.db.select(application.Attempt))
            attempt.diagnosis_json = "{}"
            application.db.session.commit()
            attempt_id = attempt.id
        payload = self.client.get(f"/api/diagnosis/{attempt_id}").get_json()
        self.assertTrue(payload["legacy"])
        self.assertIn("detail", payload["diagnosis"])

    # ------------------------------------------------------------------ resilience

    def test_diagnosis_outage_still_grades_the_answer(self):
        session_id = self.start_lesson()

        def failing(*, task_type, **_kwargs):
            if task_type == "answer_diagnosis":
                raise application.ai_service.AIProviderError("provider_timeout", "timeout")
            return FakeResponse(self.responses[task_type])

        self.responses = {
            "answer_evaluation": EVALUATION,
            "question_generation": {"question": {
                "id": "q2", "subject": "Mathematics", "concept": "Linear equations",
                "difficulty": 1, "type": "checkboxes",
                "prompt": "Which two steps isolate x in 3x + 2 = 14?", "hint": "Two steps.",
                "options": [{"id": "a", "label": "subtract 2"}, {"id": "b", "label": "divide by 3"}],
                "expected_answer": ["a", "b"]}},
        }
        with mock.patch.object(application, "create_response", failing):
            response = self.client.post("/api/answer", json={
                "session_id": session_id, "answer": "b", "hints_used": False,
                "retry_count": 0, "response_confidence": 50})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["evaluation"]["score"], 40)
        self.assertEqual(data["diagnosis"]["correctness_status"], "insufficient_evidence")
        self.assertTrue(data["diagnosis"]["missing_evidence"])

    def test_feature_flag_off_keeps_the_previous_path(self):
        application.app.config["FEATURE_ADAPTIVE_DIAGNOSTICS"] = False
        session_id = self.start_lesson()
        self.responses = {
            "answer_evaluation": EVALUATION,
            "mistake_analysis": {
                "verdict": "incorrect", "score_fraction": 0.4, "confidence": 0.8,
                "mistake_categories": ["sign_error"], "root_cause": "dropped a sign",
                "next_question": {"question": "q", "purpose": "p", "difficulty_change": "same"}},
        }
        with mock.patch.object(application, "create_response", self.route):
            response = self.client.post("/api/answer", json={
                "session_id": session_id, "answer": "b", "hints_used": False,
                "retry_count": 0, "response_confidence": 50})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["analysis"]["root_cause"], "dropped a sign")
        self.assertIn("sign_error", data["analysis"]["mistake_categories"])

    # ------------------------------------------------------------- mistake intelligence

    def test_mistake_intelligence_shows_the_new_diagnosis(self):
        self.answer(self.start_lesson())
        page = self.client.get("/insights/mistakes")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Missing earlier skill", body)
        self.assertIn("Revisit the earlier skill first", body)
        self.assertNotIn("Teacher-only note", body)

    # ------------------------------------------------- spec-driven question generation

    def practice_session(self):
        """An adaptive-practice session, which is the path that generates questions.

        Exactly one weak concept is seeded so the planner's next target is the concept it
        just diagnosed; with several, the next question belongs to a different concept
        and correctly keeps that concept's own stored difficulty instead of the plan.
        """
        with application.app.app_context():
            user_id = application.db.session.scalar(
                application.db.select(application.User.id))
            application.db.session.add(application.ConceptMastery(
                user_id=user_id, subject="Mathematics", concept="Linear equations",
                mastery_score=40, attempts=3, total_score=120, correct_attempts=1,
                incorrect_attempts=2, consecutive_correct=0, consecutive_incorrect=1,
                recent_mistake_count=1, confidence_trend=50, difficulty_level=2,
                status="learning", evidence_weight=2.0, uncertainty=0.33))
            application.db.session.commit()
        self.responses = {"adaptive_practice": {
            **LESSON, "question": dict(LESSON["question"], concept="Linear equations")}}
        with mock.patch.object(application, "create_response", self.route):
            started = self.client.post("/practice-weak", data={"subject": "Mathematics"},
                                       follow_redirects=False)
        self.assertEqual(started.status_code, 302, started.get_data(as_text=True))
        return started.headers["Location"].rsplit("session_id=", 1)[-1]

    def test_generated_question_is_validated_before_it_is_shown(self):
        session_id = self.practice_session()
        response = self.answer(session_id)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertIsNotNone(data["next_question"])
        self.assertNotIn("expected_answer", data["next_question"])
        with application.app.app_context():
            state = application.SESSIONS[session_id]
        stored = state["current_question"]
        # The machine-readable specification travels with the question.
        self.assertIn("spec", stored)
        self.assertEqual(stored["spec"]["concept"], "Linear equations")
        self.assertIn(stored["spec"]["cognitive_demand"],
                      ("recall", "apply", "analyze", "evaluate", "transfer"))
        self.assertTrue(stored["validation"]["valid"])
        self.assertTrue(stored["validation"]["checks"]["not_duplicate"])

    def test_invalid_generated_question_is_regenerated_then_falls_back(self):
        session_id = self.practice_session()
        attempts = []

        def generator(*, task_type, **kwargs):
            if task_type == "question_generation":
                attempts.append(kwargs.get("input", ""))
                # Always unanswerable, so every attempt is rejected by the validator.
                return FakeResponse({"question": {
                    "id": "q2", "subject": "Mathematics", "concept": "Linear equations",
                    "difficulty": 2, "type": "checkboxes",
                    "prompt": "Using the following diagram, pick the correct steps.",
                    "hint": "Look closely.",
                    "options": [{"id": "a", "label": "subtract 2"},
                                {"id": "b", "label": "divide by 3"}],
                    "expected_answer": ["a", "b"]}})
            if task_type == "adaptive_practice":
                return FakeResponse({"question": {
                    "id": "q2", "subject": "Mathematics", "concept": "Linear equations",
                    "difficulty": 2, "type": "checkboxes",
                    "prompt": "Which two steps isolate x in 5x + 1 = 26?", "hint": "Two steps.",
                    "options": [{"id": "a", "label": "subtract 1"},
                                {"id": "b", "label": "divide by 5"}],
                    "expected_answer": ["a", "b"]}})
            return FakeResponse(self.responses[task_type])

        self.responses = {"answer_evaluation": EVALUATION, "answer_diagnosis": DIAGNOSIS,
                          "diagnosis_verification": {"agrees": True, "reason": "ok",
                                                     "better_tag": ""}}
        application.app.config["AI_QUESTION_MAX_REGENERATIONS"] = 1
        with mock.patch.object(application, "create_response", generator):
            response = self.client.post("/api/answer", json={
                "session_id": session_id, "answer": "b", "hints_used": False,
                "retry_count": 0, "response_confidence": 50})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        # One initial attempt plus one regeneration, then the previous generator.
        self.assertEqual(len(attempts), 2)
        self.assertIn("rejected", attempts[1])
        data = response.get_json()
        self.assertIn("isolate x", data["next_question"]["prompt"])

    def test_migrations_created_the_new_columns(self):
        from sqlalchemy import inspect
        with application.app.app_context():
            inspector = inspect(application.db.engine)
            attempt = {c["name"] for c in inspector.get_columns("attempt")}
            self.assertTrue({"diagnosis_json", "diagnosis_version", "primary_diagnosis",
                             "next_action", "diagnosis_validation",
                             "missing_evidence"} <= attempt)
            mastery = {c["name"] for c in inspector.get_columns("concept_mastery")}
            self.assertTrue({"evidence_weight", "uncertainty", "last_action"} <= mastery)
            history = {c["name"] for c in inspector.get_columns("mastery_history")}
            self.assertIn("reason", history)
            self.assertIn("concept_prerequisite", inspector.get_table_names())


if __name__ == "__main__":
    unittest.main()

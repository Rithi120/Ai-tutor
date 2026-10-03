"""The knowledge gate inside the real answer loop, with every model call patched.

What these cover that the pure tests cannot: that a lesson test now ends when the student
knows the concepts rather than at question five, never before three, never after the
maximum; that a planned practice session keeps going past its plan while knowledge is
below target; that the response carries the gate's view; and that a submitted exam gets
diagnosed, judged by knowledge, and can hand its gaps to a gated practice test.
"""

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_knowledge_gate_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

A, B = "Inverse operations", "Balancing equations"

LESSON = {
    "lesson_title": "Linear equations",
    "detected_level": "starting",
    "concepts": [{"name": A, "evidence": "goal"}, {"name": B, "evidence": "goal"}],
    "explanation": "Undo the addition, then the multiplication.",
    "worked_example": {"problem": "2x + 3 = 11", "steps": ["subtract 3"], "answer": "x = 4"},
    "teacher_tips": [], "exceptions": [],
    "question": {
        "id": "q1", "subject": "Mathematics", "concept": A, "difficulty": 2,
        "type": "multiple_choice", "prompt": "Solve 2x + 3 = 11.", "hint": "Subtract 3 first.",
        "options": [{"id": "a", "label": "4"}, {"id": "b", "label": "7"}],
        "expected_answer": "a",
    },
}


def question_about(concept, number, difficulty=2):
    # A written question with a one-line key: whatever answer format the engine forces on
    # it (dropdown, ordering, ...) the deterministic checker still compares the texts, so a
    # stubbed student can answer it exactly right or exactly wrong.
    return {
        "id": f"q{number}", "subject": "Mathematics", "concept": concept, "difficulty": difficulty,
        "type": "text", "prompt": f"Explain step {number} of {concept}.", "hint": "One step.",
        "options": [], "expected_answer": f"x = {number}",
    }


def evaluation(score, next_concept, number):
    return {
        "evaluation": {"is_correct": score >= 80, "score": score,
                       "feedback": "Checked." if score >= 80 else "Check the sign.",
                       "correction": "" if score >= 80 else "2x = 8, so x = 4.",
                       "teacher_tip": "Write the step out.", "exception_note": "",
                       "skill_status": "mastered" if score >= 80 else "needs_practice"},
        "next_question": question_about(next_concept, number),
        "summary": {"overall": "Model summary.", "strengths": [], "weaknesses": [], "next_steps": []},
    }


def diagnosis(correct):
    return {
        "analysis_version": "diagnosis:v2",
        "correctness_status": "correct" if correct else "incorrect",
        "score": {"points": 1.0 if correct else 0.2, "max_points": 1.0, "rubric_evidence": [
            {"criterion": "Undo the addition", "met": correct, "evidence_ids": ["e1"]}]},
        "concepts_assessed": [A],
        "primary_diagnosis": {"tag": "correct" if correct else "procedural_error",
                              "statement": "Both steps shown." if correct else "The sign was lost.",
                              "evidence_ids": ["e1"]},
        "secondary_diagnoses": [],
        "evidence": [{"id": "e1", "source": "student_answer", "quote": "subtract 3, divide by 2"}],
        "prerequisite_gaps": [],
        "missing_evidence": False,
        "confidence": {"value": 0.9, "basis": "The working is written out."},
        "recommended_intervention": "increase_difficulty" if correct else "worked_example_then_practice",
        "candidate_question_constraints": {"concept": A, "difficulty": 2},
        "student_facing_explanation": "You undid the addition first - right order." if correct else "Moving 3 across makes it minus 3.",
        "internal_diagnostic_summary": "note",
    }


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload)


class KnowledgeGateIntegrationTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_ADAPTIVE_DIAGNOSTICS=True, FEATURE_DIAGNOSTIC_VERIFICATION=True,
            AI_MODE="cached", TEST_MIN_QUESTIONS=3, TEST_MAX_QUESTIONS=15, KNOWLEDGE_TARGET=80,
            EXAM_DIAGNOSIS_LIMIT=3)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "gate", "email": "gate@example.com",
            "password": "correct-horse-battery", "language": "en"}, follow_redirects=True)
        self.calls = []
        self.responses = {}

    def route(self, *, task_type, **_kwargs):
        self.calls.append(task_type)
        payload = self.responses.get(task_type)
        if payload is None:
            raise AssertionError(f"unexpected AI task type: {task_type}")
        return FakeResponse(payload() if callable(payload) else payload)

    def start_lesson(self):
        self.responses = {"lesson_generation": LESSON}
        with mock.patch.object(application, "create_response", self.route):
            response = self.client.post("/api/analyze", data={
                "study_goal": "Solve linear equations", "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["test_range"], {"minimum": 3, "maximum": 15, "target": 80})
        return body["session_id"]

    def answer(self, session_id, score):
        """Answer the current question exactly right (score 100) or exactly wrong."""

        state = application.SESSIONS[session_id]
        current = state["current_question"]
        number = len(state["history"]) + 2
        self.generated_count = getattr(self, "generated_count", 100)

        def generated():
            self.generated_count += 1
            return {"question": question_about(A, self.generated_count)}

        self.responses = {
            "answer_evaluation": evaluation(score, A, number),
            "answer_diagnosis": diagnosis(score >= 80),
            "diagnosis_verification": {"agrees": True, "reason": "supported", "better_tag": ""},
            "question_generation": generated,
            "adaptive_practice": generated,
        }
        given = current["expected_answer"] if score >= 80 else "x = 999"
        with mock.patch.object(application, "create_response", self.route):
            response = self.client.post("/api/answer", json={
                "session_id": session_id, "answer": given, "hints_used": False,
                "retry_count": 0, "response_confidence": 50})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def run_until_complete(self, session_id, score, limit=20):
        results = []
        for _ in range(limit):
            data = self.answer(session_id, score)
            results.append(data)
            if data["complete"]:
                return results
        self.fail(f"the test did not finish within {limit} answers")

    # ------------------------------------------------------------------ lessons

    def test_a_student_who_knows_it_finishes_early_but_never_before_three(self):
        session_id = self.start_lesson()
        results = self.run_until_complete(session_id, score=100)
        self.assertGreaterEqual(len(results), 3)
        # Two fresh concepts, every answer right: each needs one easy and one harder correct
        # answer, so the gate should be done in four or five, never anywhere near fifteen.
        self.assertLessEqual(len(results), 6, [r["progress"]["knowledge"] for r in results])
        asked = [r["next_question"]["difficulty"] for r in results if r["next_question"]]
        self.assertIn(2, asked, "the gate asked for a harder confirmation question")
        final = results[-1]
        knowledge = final["progress"]["knowledge"]
        self.assertTrue(knowledge["reached"])
        self.assertEqual(knowledge["reason"], "target_reached")
        self.assertEqual(knowledge["known"], knowledge["total"], knowledge)
        self.assertEqual({item["concept"] for item in knowledge["concepts"]}, {A, B},
                         "both lesson concepts had to be shown, not just the one the model kept asking")
        self.assertIn("knowledge target", final["summary"]["overall"])
        self.assertEqual(len(final["summary"]["strengths"]), 2)
        self.assertEqual(final["summary"]["weaknesses"], [])
        for data in results[:-1]:
            self.assertFalse(data["complete"])
            self.assertIsNotNone(data["next_question"])
            self.assertIn("knowledge", data["progress"])

    def test_the_second_question_stays_on_a_concept_that_was_just_wrong(self):
        session_id = self.start_lesson()
        first = self.answer(session_id, score=20)
        self.assertFalse(first["complete"])
        self.assertEqual(first["progress"]["knowledge"]["next_concept"], A)
        self.assertEqual(first["next_question"]["concept"], A)
        self.assertEqual(first["progress"]["knowledge"]["concepts"][0]["status"], "learning")
        self.assertLess(first["progress"]["knowledge"]["concepts"][0]["knowledge"], 80)

    def test_a_struggling_student_stops_at_the_maximum_with_an_honest_summary(self):
        session_id = self.start_lesson()
        application.SESSIONS[session_id]["max_questions"] = 5
        results = self.run_until_complete(session_id, score=20)
        self.assertEqual(len(results), 5)
        final = results[-1]
        self.assertEqual(final["progress"]["knowledge"]["reason"], "max_questions")
        self.assertFalse(final["progress"]["knowledge"]["reached"])
        self.assertIn("longest test", final["summary"]["overall"])
        self.assertIn("Model summary.", final["summary"]["overall"],
                      "the model wrote a summary at the maximum and it is kept")
        self.assertTrue(final["summary"]["weaknesses"])
        self.assertTrue(any("Today's Practice" in step for step in final["summary"]["next_steps"]))

    def test_a_question_spent_on_a_known_concept_is_replaced_by_one_the_gate_wants(self):
        # The model keeps proposing questions about A even once A is known while B is open;
        # the gate regenerates the question for B instead of wasting the slot.
        session_id = self.start_lesson()
        results = self.run_until_complete(session_id, score=100)
        concepts_asked = [data["next_question"]["concept"] for data in results if data["next_question"]]
        self.assertIn(B, concepts_asked)
        self.assertIn("adaptive_practice", self.calls, "the regeneration path was used")

    def test_the_resume_bootstrap_carries_the_gate_view(self):
        session_id = self.start_lesson()
        self.answer(session_id, score=100)
        page = self.client.get(f"/?session_id={session_id}").data.decode()
        self.assertIn('"test_range"', page)
        self.assertIn('"knowledge"', page)
        self.assertIn('"target": 80', page)

    # ------------------------------------------------------------------ planned practice

    def test_weak_practice_continues_past_its_plan_until_the_concept_is_known(self):
        session_id = self.start_lesson()
        self.answer(session_id, score=20)     # creates mastery rows for A (and B via the lesson)
        self.responses = {"adaptive_practice": LESSON}
        with mock.patch.object(application, "create_response", self.route):
            started = self.client.post("/practice-weak")
        self.assertEqual(started.status_code, 302, started.headers)
        practice_id = started.headers["Location"].split("session_id=", 1)[1]
        state = application.SESSIONS[practice_id]
        self.assertEqual(state["test_total"], 5, "the plan length is still recorded")
        self.assertEqual((state["min_questions"], state["max_questions"]), (3, 15))
        results = self.run_until_complete(practice_id, score=100)
        final = results[-1]
        self.assertTrue(final["progress"]["knowledge"]["reached"])
        self.assertIsNotNone(final["practice_results"])
        self.assertGreaterEqual(len(results), 3)

    # ------------------------------------------------------------------ exams

    def make_exam(self):
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(application.User))
            project = application.LearningProject(user_id=user.id, title="Forces", subject="Physics", status="ready")
            application.db.session.add(project)
            application.db.session.flush()
            section = application.LearningSection(project_id=project.id, position=1, title="Newton",
                                                  main_topic="Newton's second law", status="learning")
            application.db.session.add(section)
            application.db.session.flush()
            now = application.utcnow()
            exam = application.FinalExam(
                project_id=project.id, question_count=2, duration_minutes=20, difficulty_mode="mixed",
                included_section_ids_json=json.dumps([section.id]), question_types_json=json.dumps(["short_answer"]),
                status="in_progress", started_at=now, expires_at=now + timedelta(minutes=20))
            application.db.session.add(exam)
            application.db.session.flush()
            for position, (prompt, expected) in enumerate((
                    ("State Newton's second law.", "F = m × a"),
                    ("A 2 kg mass accelerates at 3 m/s². Force?", "6 N")), start=1):
                application.db.session.add(application.ExamQuestion(
                    exam_id=exam.id, section_id=section.id, position=position, difficulty="medium",
                    question_type="short_answer", prompt=prompt, expected_answer=expected,
                    concepts_json=json.dumps(["Newton's second law"]), supporting_text="F = m × a"))
            application.db.session.commit()
            return exam.id

    def test_a_submitted_exam_is_diagnosed_judged_by_knowledge_and_can_close_its_gaps(self):
        exam_id = self.make_exam()
        with application.app.app_context():
            ids = application.db.session.scalars(application.db.select(application.ExamQuestion.id).where(
                application.ExamQuestion.exam_id == exam_id).order_by(application.ExamQuestion.position)).all()
        self.client.post(f"/exams/{exam_id}/autosave", json={"question_id": ids[0], "answer": "Force equals mass times acceleration"})
        self.client.post(f"/exams/{exam_id}/autosave", json={"question_id": ids[1], "answer": "5 N because 2 + 3"})
        self.responses = {
            "final_exam_evaluation": {"results": [
                {"question_id": ids[0], "score": 100, "evaluation": "Correct."},
                {"question_id": ids[1], "score": 10, "evaluation": "Added instead of multiplying."}]},
            "answer_diagnosis": {**diagnosis(False), "concepts_assessed": ["Newton's second law"],
                                 "primary_diagnosis": {"tag": "procedural_error", "statement": "Added the numbers.", "evidence_ids": ["e1"]}},
            "diagnosis_verification": {"agrees": True, "reason": "supported", "better_tag": ""},
        }
        with mock.patch.object(application, "create_response", self.route):
            submitted = self.client.post(f"/exams/{exam_id}/submit")
        self.assertEqual(submitted.status_code, 302)
        self.assertIn("answer_diagnosis", self.calls, "the wrong open answer was diagnosed")
        with application.app.app_context():
            exam = application.db.session.get(application.FinalExam, exam_id)
            assert exam is not None
            result = json.loads(exam.result_json)
            self.assertEqual(result["knowledge_target"], 80)
            self.assertEqual(len(result["knowledge"]), 1)
            row = result["knowledge"][0]
            self.assertEqual(row["concept"], "Newton's second law")
            self.assertFalse(row["known"], row)
            self.assertLess(row["knowledge"], 80)
            wrong = application.db.session.scalar(application.db.select(application.Attempt).where(
                application.Attempt.score < 50))
            self.assertEqual(wrong.primary_diagnosis, "procedural_error")
            self.assertTrue(wrong.root_cause)
            mastery = application.db.session.scalar(application.db.select(application.ConceptMastery))
            self.assertGreater(mastery.evidence_weight, 0, "exam answers now feed the evidence model")
        page = self.client.get(f"/exams/{exam_id}/results").data.decode()
        self.assertIn("KNOWLEDGE CHECK", page)
        self.assertIn("not yet", page)
        self.assertIn("Close the gaps", page)

        self.responses = {"adaptive_practice": {**LESSON, "concepts": [{"name": "Newton's second law", "evidence": "gap"}],
                                                "question": {**LESSON["question"], "concept": "Newton's second law", "subject": "Physics"}}}
        with mock.patch.object(application, "create_response", self.route):
            started = self.client.post(f"/exams/{exam_id}/close-gaps")
        self.assertEqual(started.status_code, 302, started.headers)
        self.assertIn("session_id=", started.headers["Location"])
        session_id = started.headers["Location"].split("session_id=", 1)[1]
        state = application.SESSIONS[session_id]
        self.assertEqual([item["concept"] for item in state["focus_concepts"]], ["Newton's second law"])
        self.assertEqual((state["min_questions"], state["max_questions"]), (3, 15))

    def test_exam_diagnosis_is_bounded_and_skips_closed_questions(self):
        application.app.config.update(EXAM_DIAGNOSIS_LIMIT=1)
        exam_id = self.make_exam()
        with application.app.app_context():
            ids = application.db.session.scalars(application.db.select(application.ExamQuestion.id).where(
                application.ExamQuestion.exam_id == exam_id)).all()
        for question_id in ids:
            self.client.post(f"/exams/{exam_id}/autosave", json={"question_id": question_id, "answer": "no idea"})
        self.responses = {
            "final_exam_evaluation": {"results": [{"question_id": qid, "score": 5, "evaluation": "Wrong."} for qid in ids]},
            "answer_diagnosis": diagnosis(False),
            "diagnosis_verification": {"agrees": True, "reason": "supported", "better_tag": ""},
        }
        with mock.patch.object(application, "create_response", self.route):
            self.client.post(f"/exams/{exam_id}/submit")
        self.assertEqual(self.calls.count("answer_diagnosis"), 1, "two wrong answers, a budget of one")


if __name__ == "__main__":
    unittest.main()

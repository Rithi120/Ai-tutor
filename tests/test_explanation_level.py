"""Adaptive explanations: grade + per-topic evidence + the turn decide the explanation level.

Unit tests pin the decision rules in learnova/diagnostics/explanation_level.py; the
integration tests drive the real /api/analyze, /api/answer and /api/chat routes with
every AI call patched, and inspect the instructions the tutor model would receive.
"""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_explanation_level_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.diagnostics import explanation_level as el  # noqa: E402

from tests.test_diagnostics_integration import DIAGNOSIS, EVALUATION, LESSON, FakeResponse  # noqa: E402


def evidence(score, *, attempts=4, weight=3.0, assessed=True, consecutive_correct=0):
    return {"mastery_score": score, "attempts": attempts, "evidence_weight": weight,
            "assessed": assessed, "consecutive_correct": consecutive_correct}


def plan_for(grade, ev=None, **kwargs):
    gaps = kwargs.pop("prerequisite_gaps", None)
    pattern = el.learner_pattern(ev, diagnoses=kwargs.pop("diagnoses", None),
                                 prerequisite_gaps=gaps, hint_rate=kwargs.pop("hint_rate", None))
    return el.choose_level(grade=grade, evidence=ev, pattern=pattern, prerequisites=gaps, **kwargs)


class MasteryLabelTests(unittest.TestCase):
    def test_labels_cover_the_five_states(self):
        self.assertEqual(el.mastery_label(None), "not_assessed")
        self.assertEqual(el.mastery_label(evidence(90, assessed=False)), "not_assessed")
        self.assertEqual(el.mastery_label(evidence(20)), "beginner")
        self.assertEqual(el.mastery_label(evidence(55)), "developing")
        self.assertEqual(el.mastery_label(evidence(75)), "proficient")
        self.assertEqual(el.mastery_label(evidence(92)), "advanced")

    def test_one_correct_answer_is_never_mastery(self):
        self.assertEqual(el.mastery_label(evidence(95, attempts=1, weight=1.0)), "developing")
        self.assertEqual(el.mastery_label(evidence(5, attempts=1, weight=1.0)), "developing")
        # Two answers, but not yet the evidence weight that "advanced" needs.
        self.assertEqual(el.mastery_label(evidence(95, attempts=2, weight=1.6)), "proficient")

    def test_patterns_separate_the_kinds_of_struggle(self):
        self.assertEqual(el.learner_pattern(None), "no_evidence")
        ev = evidence(45)
        self.assertEqual(el.learner_pattern(ev, prerequisite_gaps=["Signed numbers"]), "prerequisite_gap")
        self.assertEqual(el.learner_pattern(ev, diagnoses=["conceptual_misconception", "conceptual_misconception"]),
                         "conceptual_gap")
        self.assertEqual(el.learner_pattern(ev, diagnoses=["arithmetic_or_transcription_error"] * 2),
                         "execution_errors")
        self.assertEqual(el.learner_pattern(ev, hint_rate=0.8), "needs_independence")
        self.assertEqual(el.learner_pattern(evidence(92, consecutive_correct=3)), "mastered")
        self.assertEqual(el.learner_pattern(ev), "progressing")


class LevelSelectionTests(unittest.TestCase):
    def test_klasse_2_addition_is_concrete(self):
        plan = plan_for("2")
        self.assertEqual(plan.level, 1)
        block = el.instruction(plan, concept="Addition bis 20")
        self.assertIn("Concrete foundations", block)
        self.assertIn("young child", block)

    def test_klasse_9_negative_numbers_get_standard_reasoning(self):
        plan = plan_for("9")
        self.assertEqual(plan.level, 3)
        block = el.instruction(plan, concept="Negative Zahlen")
        self.assertIn("Standard school level", block)
        self.assertIn("proper terminology", block)
        self.assertIn("one short diagnostic question", block)

    def test_klasse_11_weak_fractions_get_foundations_without_childish_register(self):
        plan = plan_for("11", evidence(20))
        self.assertEqual(plan.level, 2)
        block = el.instruction(plan, concept="Brüche addieren")
        self.assertIn("Guided basics", block)
        self.assertIn("adult language", block)
        self.assertNotIn("young child", block)

    def test_mastery_is_not_held_back_by_grade_and_strong_students_get_extension(self):
        self.assertEqual(plan_for("5", evidence(92)).level, 4)
        self.assertEqual(plan_for("5", evidence(95, consecutive_correct=3)).level, 4)  # Klasse 5 ceiling
        mastered = plan_for("9", evidence(95, consecutive_correct=3))
        self.assertEqual(mastered.level, 5)
        self.assertIn("skip elementary steps", el.instruction(mastered))

    def test_grade_ceiling_caps_a_harder_request(self):
        self.assertEqual(plan_for("2", evidence(95, consecutive_correct=3), request="harder").level, 2)

    def test_requests_move_the_level_and_name_the_new_strategy(self):
        self.assertEqual(plan_for("9", request="simpler").level, 2)
        self.assertEqual(plan_for("9", request="harder").level, 4)
        confused = plan_for("9", request="confused")
        self.assertEqual(confused.level, 2)
        self.assertIn("Do NOT repeat", el.instruction(confused))
        self.assertIn("NEW example", el.instruction(plan_for("9", request="another_example")))
        self.assertEqual(plan_for("9", request="step_by_step").level, 3)

    def test_prerequisite_gap_lowers_and_limits_harder(self):
        plan = plan_for("9", evidence(45), prerequisite_gaps=["Vorzeichenregeln"], request="harder")
        self.assertEqual(plan.level, 2)
        self.assertIn("Vorzeichenregeln", el.instruction(plan))

    def test_unset_grade_and_missing_evidence_still_yield_a_plan(self):
        plan = plan_for("", None)
        self.assertEqual(plan.level, 2)
        self.assertTrue(el.instruction(plan))

    def test_detect_request_in_german_and_english(self):
        self.assertEqual(el.detect_request("Ich verstehe das nicht"), "confused")
        self.assertEqual(el.detect_request("I don't understand"), "confused")
        self.assertEqual(el.detect_request("Kannst du es einfacher erklären?"), "simpler")
        self.assertEqual(el.detect_request("Make it harder please"), "harder")
        self.assertEqual(el.detect_request("Bitte Schritt für Schritt"), "step_by_step")
        self.assertEqual(el.detect_request("Noch ein Beispiel?"), "another_example")
        self.assertIsNone(el.detect_request("Was ist 7 + 5?"))


class SessionStateTests(unittest.TestCase):
    def test_simpler_request_persists_for_later_turns(self):
        state = el.record_request(None, "simpler")
        self.assertEqual(plan_for("9", state=state).level, 2)
        state = el.record_request(state, "harder")
        self.assertEqual(plan_for("9", state=state).level, 3)

    def test_answer_kinds_change_the_strategy_differently(self):
        slip = el.after_answer(None, score=40, tag="arithmetic_or_transcription_error")
        self.assertEqual((slip["offset"], slip["focus"]), (0, "calculation_check"))
        self.assertIn("do not reteach the whole topic", el.instruction(plan_for("9", state=slip)))
        concept = el.after_answer(None, score=30, tag="conceptual_misconception")
        self.assertEqual((concept["offset"], concept["focus"]), (-1, "reteach_concept"))
        self.assertEqual(plan_for("9", state=concept).level, 2)
        repeated = el.after_answer(el.after_answer(None, score=30, tag=""), score=20, tag="")
        self.assertEqual(repeated["focus"], "simplify_and_diagnose")
        unsure = el.after_answer(None, score=90, tag="guessing_or_uncertain")
        self.assertEqual(unsure["focus"], "check_concept")

    def test_correct_answers_earn_the_level_back_gradually(self):
        state = el.record_request(None, "simpler")
        state = el.after_answer(state, score=90)
        self.assertEqual(state["offset"], -1)  # one correct answer is not enough
        state = el.after_answer(state, score=90)
        self.assertEqual(state["offset"], 0)
        state = el.after_answer(state, score=95)
        self.assertEqual(state["offset"], 1)
        self.assertEqual(state["focus"], "fade_support")


class AdaptiveTutorIntegrationTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_ADAPTIVE_DIAGNOSTICS=True,
            FEATURE_DIAGNOSTIC_VERIFICATION=True, AI_MODE="cached")
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.captured = []
        self.responses = {}

    def register(self, grade):
        self.client.post("/register", data={
            "username": "mia", "email": "mia@example.com", "password": "correct-horse-battery",
            "language": "en", "grade": grade}, follow_redirects=True)
        self.client.post("/settings/grade", data={"grade": grade})

    def route(self, *, task_type, **kwargs):
        self.captured.append({"task_type": task_type, **kwargs})
        if task_type not in self.responses:
            raise AssertionError(f"unexpected AI task type: {task_type}")
        return FakeResponse(self.responses[task_type])

    def start_lesson(self, goal="Solve linear equations"):
        self.responses = {"lesson_generation": LESSON}
        with mock.patch.object(application, "create_response", self.route):
            response = self.client.post("/api/analyze", data={"study_goal": goal, "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["session_id"]

    def chat(self, session_id, message="", control=None):
        self.responses = {"tutor_chat": "Let's look at it differently.\nCHIPS: yes | no"}
        payload = {"session_id": session_id, "message": message}
        if control:
            payload["control"] = control
        with mock.patch.object(application, "create_response", self.route):
            response = self.client.post("/api/chat", json=payload)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json(), self.captured[-1]["instructions"]

    def answer(self, session_id, diagnosis):
        self.responses = {
            "answer_evaluation": EVALUATION, "answer_diagnosis": diagnosis,
            "diagnosis_verification": {"agrees": True, "reason": "supported", "better_tag": ""},
            "question_generation": {"question": {
                "id": "q2", "subject": "Mathematics", "concept": "Linear equations", "difficulty": 1,
                "cognitive_demand": "recall", "type": "checkboxes",
                "prompt": "Which two steps isolate x in 3x + 2 = 14?", "hint": "Two steps.",
                "options": [{"id": "a", "label": "subtract 2"}, {"id": "b", "label": "divide by 3"}],
                "expected_answer": ["a", "b"], "solution_steps": ["subtract 2", "divide by 3"],
                "rubric": [{"criterion": "Both steps named", "points": 1}]}},
        }
        with mock.patch.object(application, "create_response", self.route):
            response = self.client.post("/api/answer", json={
                "session_id": session_id, "answer": "b", "hints_used": False,
                "retry_count": 0, "response_confidence": 50})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def store_mastery(self, subject, concept, score, **fields):
        with application.app.app_context():
            user = application.db.session.execute(application.db.select(application.User)).scalar_one()
            values = dict(attempts=5, total_score=score * 5, mastery_score=score, evidence_weight=3.0,
                          uncertainty=0.25, correct_attempts=4 if score >= 70 else 1,
                          consecutive_correct=3 if score >= 85 else 0)
            values.update(fields)
            application.db.session.add(application.ConceptMastery(
                user_id=user.id, subject=subject, concept=concept, **values))
            application.db.session.commit()

    def test_no_mastery_data_starts_at_the_grade_and_chat_reports_the_level(self):
        self.register("9")
        data, instructions = self.chat(self.start_lesson(), "How do I solve this?")
        self.assertIn("EXPLANATION LEVEL for 'Linear equations': 3 of 5", instructions)
        self.assertEqual(data["teaching"]["topic_mastery"], "not_assessed")
        self.assertEqual(data["teaching"]["level"], 3)
        # The block sits before the subject notation rules, which stay last.
        self.assertLess(instructions.index("EXPLANATION LEVEL"), instructions.index("MATH FORMATTING"))

    def test_weak_topic_in_klasse_11_gets_foundations(self):
        self.register("11")
        self.store_mastery("Mathematics", "Linear equations", 20)
        _, instructions = self.chat(self.start_lesson(), "Explain it")
        self.assertIn(": 2 of 5 - Guided basics", instructions)
        self.assertIn("adult language", instructions)

    def test_mastery_differs_across_subjects_and_topics(self):
        self.register("9")
        self.store_mastery("Mathematics", "Linear equations", 20)
        self.store_mastery("Mathematics", "Geometry", 95)
        self.store_mastery("Physics", "Linear equations", 95)
        with application.app.test_request_context():
            with self.client:
                self.client.get("/dashboard", headers={"Accept": "text/html"})
                levels = {
                    key: application.topic_teaching_plan(*key)[0].level
                    for key in (("Mathematics", "Linear equations"), ("Mathematics", "Geometry"),
                                ("Physics", "Linear equations"), ("Mathematics", "Unseen topic"))
                }
        self.assertEqual(levels[("Mathematics", "Linear equations")], 2)
        self.assertEqual(levels[("Mathematics", "Geometry")], 5)
        self.assertEqual(levels[("Physics", "Linear equations")], 5)
        self.assertEqual(levels[("Mathematics", "Unseen topic")], 3)

    def test_strong_student_lesson_prompt_is_not_elementary(self):
        self.register("9")
        self.store_mastery("Mathematics", "Linear equations", 95)
        self.start_lesson("Linear equations with parameters")
        lesson_call = next(item for item in self.captured if item["task_type"] == "lesson_generation")
        self.assertIn(": 5 of 5 - Extension", lesson_call["instructions"])

    def test_simpler_control_changes_this_and_the_next_response(self):
        self.register("9")
        session_id = self.start_lesson()
        _, before = self.chat(session_id, "Explain the second step")
        data, simpler = self.chat(session_id, "", control="simpler")
        self.assertIn(": 3 of 5", before)
        self.assertIn(": 2 of 5", simpler)
        self.assertIn("asked for a simpler explanation", simpler)
        self.assertEqual(data["teaching"]["request"], "simpler")
        # The visible label became the stored student message.
        self.assertEqual(application.SESSIONS[session_id]["chat_history"][-2]["content"], "Simpler explanation")
        _, after = self.chat(session_id, "ok, and then?")
        self.assertIn(": 2 of 5", after)

    def test_typed_confusion_is_not_answered_with_the_same_explanation(self):
        self.register("9")
        _, instructions = self.chat(self.start_lesson(), "Ich verstehe das nicht")
        self.assertIn("Do NOT repeat or rephrase the previous explanation", instructions)

    def test_unknown_control_is_ignored_and_empty_message_still_rejected(self):
        self.register("9")
        session_id = self.start_lesson()
        response = self.client.post("/api/chat", json={"session_id": session_id, "message": "", "control": "teleport"})
        self.assertEqual(response.status_code, 400)

    def test_incorrect_answers_update_the_teaching_strategy(self):
        self.register("9")
        session_id = self.start_lesson()
        self.answer(session_id, DIAGNOSIS)  # prerequisite gap
        state = el.topic_state(application.SESSIONS[session_id]["teaching"], "Linear equations")
        self.assertEqual((state["offset"], state["focus"]), (-1, "prerequisite"))
        _, instructions = self.chat(session_id, "What now?")
        self.assertIn("rebuild that briefly first", instructions)
        self.assertIn(": 2 of 5", instructions)

        slip = copy.deepcopy(DIAGNOSIS)
        slip["primary_diagnosis"] = {"tag": "arithmetic_or_transcription_error",
                                     "statement": "11 - 3 was miscalculated.", "evidence_ids": ["e2"]}
        slip["prerequisite_gaps"] = []
        slip["recommended_intervention"] = "worked_example"
        session_id = self.start_lesson()
        self.answer(session_id, slip)
        self.assertEqual(el.topic_state(application.SESSIONS[session_id]["teaching"], "Linear equations")["focus"], "calculation_check")

    def test_answer_evaluation_feedback_carries_the_level(self):
        self.register("2")
        self.answer(self.start_lesson(), DIAGNOSIS)
        evaluation_call = next(item for item in self.captured if item["task_type"] == "answer_evaluation")
        self.assertIn("EXPLANATION LEVEL for 'Linear equations': 1 of 5", evaluation_call["instructions"])

    def test_unreadable_mastery_data_does_not_break_chat(self):
        self.register("9")
        session_id = self.start_lesson()
        with mock.patch.object(application, "topic_mastery_record",
                               side_effect=application.SQLAlchemyError("boom")):
            data, instructions = self.chat(session_id, "Help")
        self.assertEqual(data["teaching"]["topic_mastery"], "not_assessed")
        self.assertIn("EXPLANATION LEVEL", instructions)


if __name__ == "__main__":
    unittest.main()

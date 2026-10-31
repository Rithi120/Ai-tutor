"""Regression tests for defects found in the adaptive tutoring path (audit of 2026-10-25).

Each test names the defect it pins. They drive the real /api/analyze, /api/answer and
/api/chat routes with the AI patched, and inspect what the tutor model would receive.
"""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_teaching_regressions_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.diagnostics import explanation_level as el  # noqa: E402

from tests import test_explanation_level as harness  # noqa: E402


class _Base(harness.AdaptiveTutorIntegrationTests):
    """Reuse the integration harness without re-running its tests."""

    def level_in(self, instructions):
        marker = " of 5 - "
        index = instructions.index(marker)
        return int(instructions[index - 1])


for _name in [name for name in dir(harness.AdaptiveTutorIntegrationTests) if name.startswith("test_")]:
    setattr(_Base, _name, None)


class TopicIsolationRegressions(_Base):
    def test_message_words_never_borrow_another_topics_mastery(self):
        # Defect: with an exact concept that had no record yet, the lookup fell back to
        # fuzzy-matching the chat message, so a weak "Fractions" record lowered the level
        # of an unassessed "Linear equations" lesson.
        self.register("9")
        self.store_mastery("Mathematics", "Fractions", 10)
        data, instructions = self.chat(self.start_lesson(), "Is this like adding fractions?")
        self.assertEqual(data["teaching"]["topic_mastery"], "not_assessed")
        self.assertEqual(self.level_in(instructions), 3)

    def test_session_adjustment_belongs_to_the_topic_it_was_made_for(self):
        # Defect: session["teaching"] was one flat state per lesson session, so "simpler"
        # on one concept also simplified the next concept in a multi-concept practice.
        self.register("9")
        session_id = self.start_lesson()
        self.chat(session_id, "", control="simpler")
        application.SESSIONS[session_id]["current_question"]["concept"] = "Quadratic equations"
        _, instructions = self.chat(session_id, "And this one?")
        self.assertEqual(self.level_in(instructions), 3)
        application.SESSIONS[session_id]["current_question"]["concept"] = "Linear equations"
        _, instructions = self.chat(session_id, "Back to this one")
        self.assertEqual(self.level_in(instructions), 2)

    def test_chat_uses_the_open_questions_subject(self):
        # Defect: /api/answer keys mastery by the question's subject, /api/chat by the
        # session subject; an adaptive-practice question in another subject missed its record.
        self.register("9")
        self.store_mastery("Physics", "Linear equations", 95)
        session_id = self.start_lesson()
        application.SESSIONS[session_id]["current_question"]["subject"] = "Physics"
        data, _ = self.chat(session_id, "Help")
        self.assertEqual(data["teaching"]["topic_mastery"], "advanced")


class ContextRegressions(_Base):
    def test_follow_ups_can_see_the_worked_example_and_objective(self):
        # Defect: the chat context carried the explanation but not the worked example, so
        # "why is step 2 like that?" referred to text the model never received.
        self.register("9")
        session_id = self.start_lesson("Solve linear equations")
        self.chat(session_id, "why?")
        sent = self.captured[-1]["input"]
        self.assertIn('"worked_example"', sent)
        self.assertIn("subtract 3", sent)
        self.assertIn('"learning_objective": "Solve linear equations"', sent)

    def test_answer_key_still_never_reaches_the_chat_model(self):
        self.register("9")
        session_id = self.start_lesson()
        self.chat(session_id, "just tell me the answer")
        sent = self.captured[-1]["input"]
        for key in ("expected_answer", "solution_steps", "rubric"):
            self.assertNotIn(f'"{key}"', sent)

    def test_direct_answer_request_is_routed_through_integrity(self):
        self.register("9")
        _, instructions = self.chat(self.start_lesson(), "Just tell me the answer")
        self.assertIn("asked for the answer directly", instructions)


class StrategyRegressions(_Base):
    def test_repeated_confusion_changes_the_representation_each_time(self):
        # Defect: every "I still don't understand" produced the identical directive, so
        # nothing pushed the model off the representation it had already used.
        self.register("9")
        session_id = self.start_lesson()
        _, first = self.chat(session_id, "I don't understand")
        _, second = self.chat(session_id, "I still don't understand")
        _, third = self.chat(session_id, "I still don't get it")
        directives = [line for text in (first, second, third) for line in text.splitlines()
                      if line.startswith("Try this representation")]
        self.assertEqual(len(directives), 3)
        self.assertEqual(len(set(directives)), 3)

    def test_success_with_hints_is_not_independent_mastery(self):
        ev = {"mastery_score": 92, "attempts": 6, "evidence_weight": 3.0, "assessed": True,
              "consecutive_correct": 4}
        self.assertEqual(el.learner_pattern(ev, hint_rate=0.8), "needs_independence")
        self.assertEqual(el.learner_pattern(ev, hint_rate=0.0), "mastered")


class PromptConflictRegressions(_Base):
    def test_level_five_student_is_not_forced_into_baby_steps(self):
        # Defect: TUTOR_RULES demanded "baby steps" and "never skip transformations" for
        # everyone, and the grade descriptor demanded grade-level difficulty, contradicting
        # the per-topic level the same instructions carried.
        self.register("9")
        self.store_mastery("Mathematics", "Linear equations", 95)
        _, instructions = self.chat(self.start_lesson(), "Show me something harder")
        self.assertNotIn("baby steps", instructions)
        self.assertNotIn("Match question difficulty to that grade", instructions)
        self.assertIn("EXPLANATION LEVEL", instructions)

    def test_chat_coaching_allows_a_direct_answer_to_a_simple_question(self):
        # Defect: every chat turn had to teach a named 2-5 step method on an everyday example.
        self.register("2")
        _, instructions = self.chat(self.start_lesson(), "What is 7 + 5?")
        self.assertIn("simple factual question", instructions)
        self.assertNotIn("Teach a reusable method before drilling", instructions)


class DeploymentBudgetRegressions(unittest.TestCase):
    def test_render_pins_are_not_below_the_code_defaults(self):
        # Evidence (instance/ai_usage.jsonl): lesson outputs reached 1867 tokens and answer
        # evaluations 1287, above the 1800/1100 pinned in render.yaml.
        text = (Path(application.app.root_path) / "render.yaml").read_text(encoding="utf-8")
        lines = text.splitlines()
        pins = {}
        for index, line in enumerate(lines):
            if line.strip().startswith("- key:"):
                key = line.split(":", 1)[1].strip()
                value_line = lines[index + 1].strip() if index + 1 < len(lines) else ""
                if value_line.startswith("value:"):
                    pins[key] = value_line.split(":", 1)[1].strip().strip('"')
        self.assertGreaterEqual(int(pins["LESSON_TOKEN_LIMIT"]), 2200)
        self.assertGreaterEqual(int(pins["ANSWER_TOKEN_LIMIT"]), 2200)


if __name__ == "__main__":
    unittest.main()

"""PF4 L2/L5/L7/L10: the prompt rules of lesson v8, answer evaluation v7, diagnosis v3 and
question generation v2 (offline; the behaviour itself is only confirmable live, FIX_PLAN §6.4).

Live evidence (scratchpad prod/live_lessons.md §2): wrong answer keys ("1) lost"), a grader that
called the student's source-backed answer false (History, Art. 48), the first question reusing
the guided exercise (Math), "Der Student"/"Der Schüler" and the formal "Sie", English labels
("Step 1", "additional interpretation") in German lessons, and a hidden model `explanation`
that is never guarded and wastes output when the parts carry the lesson.
"""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_lesson_prompts_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services.prompts import PROMPT_VERSIONS, SCHEMA_SUMMARIES, STUDENT_ADDRESS_RULE  # noqa: E402
from learnova.diagnostics.prompts import diagnosis_system_prompt, question_system_prompt  # noqa: E402
from tests import test_lesson_teaching_engine as teaching  # noqa: E402

KEY_CHECK = ("every guided-exercise answer and the question's expected_answer follow the rule the parts teach "
             "and agree with the source material")
DISTRACTOR_CHECK = "if a sentence of the source supports an option, that option is correct or must be rewritten"
FIRST_QUESTION = "The first question must not reuse the numbers, sentences or example of the guided exercise"
GRADER_SOURCE = ("An answer that a sentence of uploaded_source supports is not wrong, even when it differs from "
                 "expected_answer")


class VersionTests(unittest.TestCase):
    def test_the_bumped_versions(self):
        self.assertEqual(PROMPT_VERSIONS["lesson_generation"], "lesson_generation:v9")
        self.assertEqual(PROMPT_VERSIONS["answer_evaluation"], "answer_evaluation:v8")
        self.assertEqual(PROMPT_VERSIONS["answer_diagnosis"], "answer_diagnosis:v4")
        self.assertEqual(PROMPT_VERSIONS["question_generation"], "question_generation:v2")

    def test_the_cached_page_readings_and_topic_batches_keep_their_versions(self):
        # A bump would invalidate every project's topic cache and every cached page reading.
        self.assertEqual(PROMPT_VERSIONS["project_section_generation"], "project_section_generation:v4")
        self.assertEqual(PROMPT_VERSIONS["ocr_document_recognition"], "ocr_document_recognition:v2")
        self.assertEqual(PROMPT_VERSIONS["image_understanding"], "image_understanding:v2")


class RuleTextTests(unittest.TestCase):
    def test_the_address_rule(self):
        for words in ("informal second person", "German du", "French tu", "Spanish tú", "der Student",
                      "der Schüler", "'Sie'", "teacher_tips speak to the student", "Step 1",
                      "additional interpretation"):
            self.assertIn(words, STUDENT_ADDRESS_RULE)

    def test_the_lesson_shape_and_notes_rule(self):
        self.assertIn(KEY_CHECK, application.LESSON_NOTES_RULE)
        self.assertIn(DISTRACTOR_CHECK, application.LESSON_NOTES_RULE)
        shape = application.LESSON_JSON_SHAPE
        self.assertIn(FIRST_QUESTION, shape)
        self.assertIn(STUDENT_ADDRESS_RULE, shape)
        self.assertIn("With a TEACHING METHOD leave out worked_example", shape)
        self.assertIn("the server rebuilds it from the parts", shape)

    def test_the_schema_summary_marks_the_worked_example_optional(self):
        self.assertIn('"worked_example":{"problem":str,"steps":list[str],"answer":str}|absent',
                      SCHEMA_SUMMARIES["lesson_generation"])

    def test_the_diagnosis_rule(self):
        prompt = diagnosis_system_prompt("History", "German")
        self.assertIn("An answer the source material supports is never classified as an error.", prompt)
        self.assertIn("informal second person", prompt)

    def test_the_question_rule(self):
        prompt = question_system_prompt("Ethics", "German")
        self.assertIn(STUDENT_ADDRESS_RULE, prompt)


class PromptsSentTests(teaching.TeachingEngineCase):
    """What the real lesson and grading calls send."""

    def setUp(self):
        super().setUp()
        _, self.section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [teaching.GYGES])],
                                                  teaching.ethics_topic)
        self.lesson_parts = [teaching.part("objective", "Ziel."), teaching.part("thought_experiment", "Gyges ..."),
                             teaching.part("guided_exercise", "Erkläre."), teaching.part("summary", "Kurz.")]
        self.session_id = self.start(self.section_id)

    def test_the_lesson_prompt_carries_the_v8_rules(self):
        call = self.lesson_call()
        self.assertEqual(call.get("prompt_version", PROMPT_VERSIONS["lesson_generation"]), "lesson_generation:v9")
        for rule in (KEY_CHECK, DISTRACTOR_CHECK, FIRST_QUESTION, STUDENT_ADDRESS_RULE):
            self.assertIn(rule, call["input"])

    def test_the_grading_prompt_carries_the_v7_rules(self):
        self.answers = [teaching.evaluation(20, retry={"misunderstanding": "x", "reteach": "y",
                                                       "simpler_example": "z"})]
        self.answer(self.session_id, "b")
        grading = next(call for call in self.calls if call["task_type"] == "answer_evaluation")
        self.assertIn(GRADER_SOURCE, grading["input"])
        self.assertIn("Never state as a fact anything uploaded_source contradicts.", grading["input"])
        self.assertNotIn("never introduce facts outside it", grading["input"])
        self.assertIn("what you misunderstood or left out, said to the student in one or two sentences",
                      grading["input"])
        self.assertIn(STUDENT_ADDRESS_RULE, grading["input"])


if __name__ == "__main__":
    unittest.main()

"""Deterministic teaching-quality benchmark (scenarios A-H of the 2026-10-25 audit).

For every scenario this checks two things:

1. What the app sends: the explanation level chosen in Python and the directives that
   reach the model (prompt_expect). This is the part the code controls, so it is exact.
2. The rubric: the hand-written good reply passes with high scores and the weak reply
   fails or scores clearly lower, so the rubric used for live runs is known to separate
   them. Model wording is never asserted.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_teaching_benchmark_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import contracts  # noqa: E402
from learnova.ai_services.prompts import PROMPT_VERSIONS  # noqa: E402
from learnova.evaluation.teaching_rubric import arithmetic_errors, score_reply  # noqa: E402

from tests import teaching_benchmark as bench  # noqa: E402

DATA = bench.load()


def fixture_reply(kind):
    return lambda scenario, call: scenario["replies"][kind]


class BenchmarkScenarioTests(unittest.TestCase):
    maxDiff = None

    def test_every_scenario_sends_the_expected_prompt_and_the_rubric_separates_replies(self):
        report = []
        for scenario in DATA["scenarios"]:
            with self.subTest(scenario=scenario["id"]):
                good = bench.run_scenario(application, DATA, scenario, fixture_reply("good"))
                self.assertEqual(bench.prompt_failures(good, scenario), [], scenario["id"])
                weak_score = score_reply(scenario["replies"]["weak"], {
                    **scenario.get("expect", {}),
                    **({"previous_reply": scenario["turns"][-1]["reply"]}
                       if scenario.get("turns") and scenario.get("expect", {}).get("compare_previous") else {}),
                })
                self.assertTrue(good["score"].passed, (scenario["id"], good["score"].as_dict()))
                self.assertGreaterEqual(good["score"].mean, 4.2, (scenario["id"], good["score"].as_dict()))
                self.assertGreaterEqual(min(good["score"].scores.values()), 3, (scenario["id"], good["score"].as_dict()))
                # Separated = a hard failure, a clearly lower mean, or one dimension at 1-2
                # (an off-topic answer is fluent and correct, and fails only on relevance).
                self.assertTrue(
                    not weak_score.passed or weak_score.mean <= good["score"].mean - 1.0
                    or min(weak_score.scores.values()) <= 2,
                    (scenario["id"], weak_score.as_dict()))
                report.append((scenario["id"], good["score"].mean, weak_score.mean, weak_score.passed))
        self.assertEqual(len(report), len(DATA["scenarios"]))

    def test_groups_cover_the_audit_matrix(self):
        groups = {scenario["group"] for scenario in DATA["scenarios"]}
        self.assertLessEqual({"A", "B", "C", "D", "E", "F", "G"}, groups)
        controls = {scenario["measure"].get("control") for scenario in DATA["scenarios"]}
        self.assertLessEqual({"simpler", "step_by_step", "another_example", "harder"}, controls)


class RubricUnitTests(unittest.TestCase):
    def test_wrong_calculations_are_hard_failures_in_latex_and_plain_text(self):
        self.assertEqual(arithmetic_errors("$$6 \\times 3 = 18$$ and $$9 - 18 = -9$$"), [])
        self.assertTrue(arithmetic_errors("6 × 3 = 16"))
        self.assertTrue(arithmetic_errors("$$-7 - (-3) = -10$$"))
        self.assertEqual(arithmetic_errors("$$-7 - (-3) = -7 + 3$$ $$-7 + 3 = -4$$"), [])
        # Not equations: an x-solution and a fraction chain are not mis-parsed.
        self.assertEqual(arithmetic_errors("so $x = 4$ and $\\frac{1}{2} + \\frac{1}{4} = \\frac{3}{4}$"), [])

    def test_integrity_and_curriculum_claims_are_hard_failures(self):
        self.assertFalse(score_reply("The answer is x = 4.", {"forbidden_answer": "x = 4"}).passed)
        self.assertFalse(score_reply("Laut Bildungsplan musst du das können.", {}).passed)
        self.assertTrue(score_reply("Laut Bildungsplan musst du das können.", {"curriculum_excerpt": True}).passed)

    def test_near_copy_re_explanation_is_penalised(self):
        previous = "Think of a balance scale and take 3 from both sides."
        copy = score_reply(previous, {"previous_reply": previous})
        fresh = score_reply("Try a tiny case: x + 3 = 5. Which number plus 3 is 5?", {"previous_reply": previous})
        self.assertLess(copy.scores["relevance"], fresh.scores["relevance"])


class RegressionContractTests(unittest.TestCase):
    def test_benchmark_lessons_still_validate_against_the_lesson_contract(self):
        for name, lesson in DATA["lessons"].items():
            with self.subTest(lesson=name):
                report = contracts.validate_output(
                    "lesson_generation", json.dumps(lesson), PROMPT_VERSIONS["lesson_generation"])
                self.assertTrue(report.valid, report.summary)

    def test_chat_response_contract_is_unchanged_plus_teaching(self):
        scenario = DATA["scenarios"][0]
        result = bench.run_scenario(application, DATA, scenario, fixture_reply("good"))
        self.assertEqual(result["status"], 200)
        self.assertEqual(set(result["teaching"]), {"level", "level_name", "topic_mastery", "pattern", "request", "focus"})
        self.assertEqual(result["model"], application.TUTOR_CHAT_MODEL)


if __name__ == "__main__":
    unittest.main()

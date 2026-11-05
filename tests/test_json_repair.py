"""PF4 L9: a JSON syntax slip is repaired instead of paying a full corrective call (offline).

Live evidence (scratchpad prod/live_lessons.md §2.3, §4 L9): 2 of 17 lesson and answer-evaluation
calls failed validation on syntax alone - an answer evaluation without its final "}" and a
lesson with a stray '"' after "]". Each bought a corrective call on the 120b model, which writes
about 1.8x longer (a 2200 cap would cut it off). The two raw outputs are copied into
tests/fixtures/ai/repair/ (synthetic notes written for the live test, no student data).

`repair_json_syntax` makes exactly two repairs outside JSON strings and nothing else: the stray
quote, and one or two missing closers after a complete value. A truncation inside a string or
after a comma is not repaired; valid JSON is never touched.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_json_repair_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.contracts import AIValidationError, repair_json_syntax, validate_output  # noqa: E402

REPAIR = Path(__file__).parent / "fixtures" / "ai" / "repair"
MISSING_BRACE = (REPAIR / "answer_evaluation_missing_brace.txt").read_text(encoding="utf-8")
STRAY_QUOTE = (REPAIR / "lesson_stray_quote.txt").read_text(encoding="utf-8")
EVALUATION_CONTEXT = {"is_final": False, "concepts": ["Present perfect vs Simple Past"]}


class RepairFunctionTests(unittest.TestCase):
    def test_the_two_live_slips_are_invalid_as_they_came(self):
        for text in (MISSING_BRACE, STRAY_QUOTE):
            with self.assertRaises(json.JSONDecodeError):
                json.loads(text)

    def test_a_missing_final_brace_is_closed(self):
        repaired = repair_json_syntax(MISSING_BRACE)
        self.assertIsNotNone(repaired)
        payload = json.loads(repaired)
        self.assertTrue(payload["retry"]["misunderstanding"].startswith('Du hast das Signalwort "yesterday"'))
        self.assertEqual(repaired, MISSING_BRACE + "}")

    def test_a_stray_quote_after_a_closing_bracket_is_dropped(self):
        repaired = repair_json_syntax(STRAY_QUOTE)
        self.assertIsNotNone(repaired)
        payload = json.loads(repaired)
        self.assertEqual(payload["question"]["expected_answer"], "c")
        self.assertEqual(len(repaired), len(STRAY_QUOTE) - 1)

    def test_what_is_not_repaired(self):
        cut_in_string = STRAY_QUOTE.replace(']","answer"', '],"answer"')[:2000]
        self.assertTrue(cut_in_string.endswith('{"la'), "the cut falls inside a key")
        for text in (
            cut_in_string,                                  # cut inside a string
            '{"a": [1, 2],',                                 # cut after a comma
            '{"a": ',                                        # cut after a colon
            '{"a": {"b": {"c": {"d": 1',                     # three closers missing
            '{"a": "x"} trailing prose',                     # not a syntax slip of this kind
            '{"a"',                                          # a key without its value
        ):
            with self.subTest(text=text[-40:]):
                self.assertIsNone(repair_json_syntax(text))
        # A quote inside a string is data: only the missing closer is added.
        inside = '{"text": "a ]\\", inside a string"'
        self.assertEqual(repair_json_syntax(inside), inside + "}")

    def test_valid_json_is_left_alone(self):
        valid = json.dumps({"a": [1, {"b": "c ]\" d"}], "e": None}, ensure_ascii=False)
        self.assertIsNone(repair_json_syntax(valid), "nothing to repair")
        self.assertEqual(ai_service.parse_json(valid), json.loads(valid))

    def test_parse_json_uses_the_repair(self):
        self.assertEqual(ai_service.parse_json(MISSING_BRACE)["evaluation"]["score"], 0)
        self.assertEqual(ai_service.parse_json(STRAY_QUOTE)["question"]["id"], "q1")

    def test_the_validator_accepts_the_repaired_output_and_logs_it(self):
        with self.assertLogs("learnova.ai_services.contracts", level="INFO") as logs:
            validate_output("answer_evaluation", MISSING_BRACE, "answer_evaluation:test", EVALUATION_CONTEXT)
            validate_output("lesson_generation", STRAY_QUOTE, "lesson_generation:test", {"subject": "Ethics"})
        self.assertTrue(any("ai.json_repaired task=answer_evaluation kind=closers" in line for line in logs.output))
        self.assertTrue(any("ai.json_repaired task=lesson_generation kind=stray_quote" in line
                            for line in logs.output))
        with self.assertRaises(AIValidationError) as caught:
            validate_output("lesson_generation", STRAY_QUOTE[:2000], "lesson_generation:test", {})
        self.assertEqual(caught.exception.category, "invalid_json")


class SlipProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def __call__(self, **_options):
        provider = self

        class Responses:
            def create(self, **kwargs):
                provider.calls.append(kwargs)
                usage = SimpleNamespace(input_tokens=1000, output_tokens=400, total_tokens=1400)
                return SimpleNamespace(output_text=provider.outputs.pop(0), model=kwargs.get("model"), usage=usage)

        return SimpleNamespace(responses=Responses())


class GatewayRepairTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        application.app.config.update(
            TESTING=True, RUN_LIVE_AI_TEST=True, AI_MODE="live", ALLOW_LIVE_AI=True, GROQ_API_KEY="test-key",
            AI_USAGE_PATH=str(root / "usage.jsonl"), AI_CACHE_DIR=str(root / "cache"), AI_FALLBACK_MODEL="",
            AI_SLOW_MODEL="", AI_PREMIUM_REASONING_MODEL="", AI_PREMIUM_GRADING_MODEL="")
        for store in (ai_service._MODEL_COOLDOWNS, ai_service._PROVIDER_COOLDOWNS, ai_service._RECENT_RESULTS):
            store.clear()

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temporary.cleanup()

    def run_task(self, task_type, outputs, context):
        provider = SlipProvider(outputs)
        with patch.object(ai_service, "OpenAI", provider), application.app.app_context():
            response = ai_service.create_response(
                task_type=task_type, language="de", model=application.TUTOR_MODEL, instructions="Bewerte.",
                input=f"Aufgabe {task_type}", validation_context=context, max_output_tokens=2600)
        return response, provider

    def test_the_live_answer_slip_costs_one_call(self):
        response, provider = self.run_task("answer_evaluation", [MISSING_BRACE, "{}"], EVALUATION_CONTEXT)
        self.assertEqual(len(provider.calls), 1, "no corrective call for a missing brace")
        self.assertEqual(response.output_text, MISSING_BRACE)
        self.assertEqual(application.parse_json(response.output_text)["evaluation"]["skill_status"], "needs_practice")

    def test_the_live_lesson_slip_costs_one_call(self):
        response, provider = self.run_task("lesson_generation", [STRAY_QUOTE, "{}"], {"subject": "Ethics"})
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(application.parse_json(response.output_text)["question"]["expected_answer"], "c")

    def test_a_lesson_cut_mid_string_still_gets_its_corrective_call(self):
        valid = STRAY_QUOTE.replace(']","answer"', '],"answer"')
        _response, provider = self.run_task("lesson_generation", [STRAY_QUOTE[:2000], valid], {"subject": "Ethics"})
        self.assertEqual(len(provider.calls), 2)


if __name__ == "__main__":
    unittest.main()

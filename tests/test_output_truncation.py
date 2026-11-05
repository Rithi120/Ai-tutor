"""An answer the output cap cut off is recorded and logged, and not retried the same way (PF6.1).

Before this, a provider response that ended because it reached max_output_tokens looked like
any other invalid answer: the usage record said "invalid_json" and nothing more, nothing was
logged in production (INFO is off there), and the gateway's one corrective retry went to the
same model with the same cap - which stops in the same place (learnova/config.py notes the
section generation and exam calls that did exactly that before their caps were raised).

Pinned here with the providers' own SDK types (openai 1.x `Response` and `ChatCompletion`,
anthropic `Message`), shaped as Groq returned them on 2026-11-02 (tests/fixtures/
live_chat_2026-11-02.json: status "incomplete", IncompleteDetails(reason='max_output_tokens')):
* the usage record carries output_truncated and the provider's finish reason;
* one WARNING line `ai.output_truncated task=... model=... max_output_tokens=...`, no content;
* a cut-off answer that fails validation is not sent again to the same model with the same cap;
  a different candidate (the fallback model) still gets the corrective retry, because another
  model can answer within the same cap;
* a cut-off answer that still validates (a repaired missing closer, a salvaged photo reading)
  is used, and recorded as cut off.
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from anthropic.types import Message
from openai.types.chat import ChatCompletion
from openai.types.responses import Response

import app as application
from learnova.ai_services import adapters
from learnova.ai_services import service as ai_service
from learnova.ai_services.contracts import AIValidationError
from tests.config_isolation import keep_config

MODEL = "openai/gpt-oss-20b"
FALLBACK = "openai/gpt-oss-120b"
LESSON = {
    "lesson_title": "Small lesson", "concepts": [{"name": "Addition"}],
    "explanation": "Add the values one step at a time.",
    "worked_example": {"problem": "1 + 1", "steps": ["Add one and one"], "answer": "2"},
    "question": {"id": "q1", "concept": "Addition", "difficulty": 1, "type": "text",
                 "prompt": "What is 1 + 1?", "hint": "Count once.", "options": [],
                 "expected_answer": "2"},
}
CUT_OFF = '{"lesson_title": "Small lesson", "concepts": [{"name": "Addi'
SECRET_WORDS = "Small lesson"          # model content: never in a log line


def sdk_response(text, *, status="completed", reason=None, model=MODEL, output_tokens=120):
    """An openai `Response` exactly as the SDK builds it from Groq's Responses API JSON."""

    return Response.model_validate({
        "id": "resp_test", "created_at": 1762100000, "model": model, "object": "response",
        "output": [{"type": "message", "id": "msg_test", "role": "assistant", "status": status,
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
        "status": status, "incomplete_details": {"reason": reason} if reason else None,
        "usage": {"input_tokens": 400, "output_tokens": output_tokens, "total_tokens": 400 + output_tokens,
                  "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
    })


def cut_off(text=CUT_OFF, **values):
    return sdk_response(text, status="incomplete", reason="max_output_tokens", output_tokens=5000, **values)


class FakeOpenAI:
    """Stands in for `openai.OpenAI` inside the gateway: answers from a queue, records requests."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.requests = []

    def __call__(self, **_client_options):
        fake = self

        class Responses:
            def create(self, **request):
                fake.requests.append(request)
                return fake.answers.pop(0)

        return SimpleNamespace(responses=Responses())


class TruncationTestCase(unittest.TestCase):
    def setUp(self):
        config = application.app.config
        scratch = Path(tempfile.mkdtemp(prefix="learnova-truncation-"))
        names = ("TESTING", "ENV_NAME", "AI_MODE", "ALLOW_LIVE_AI", "RUN_LIVE_AI_TEST", "AI_ENFORCE_LIMITS",
                 "AI_CACHE_DIR", "AI_USAGE_PATH", "GROQ_API_KEY", "GROQ_FAST_MODEL", "GROQ_TUTOR_MODEL",
                 "GROQ_ANALYSIS_MODEL", "AI_FALLBACK_MODEL", "AI_SLOW_MODEL", "AI_PREMIUM_REASONING_MODEL",
                 "AI_PREMIUM_GRADING_MODEL", "AI_TEXT_REQUEST_TOKEN_CEILING", "AI_MAX_OUTPUT_CHARACTERS")
        keep_config(self, config, *names)
        config.update(TESTING=False, ENV_NAME="development", AI_MODE="live", ALLOW_LIVE_AI=True,
                      RUN_LIVE_AI_TEST=False, AI_ENFORCE_LIMITS=False, AI_CACHE_DIR=str(scratch / "cache"),
                      AI_USAGE_PATH=str(scratch / "usage.jsonl"), GROQ_API_KEY="test-key",
                      # One Groq model for every tier: the plan has a single candidate.
                      GROQ_FAST_MODEL=MODEL, GROQ_TUTOR_MODEL=MODEL, GROQ_ANALYSIS_MODEL=MODEL,
                      AI_FALLBACK_MODEL="", AI_SLOW_MODEL="", AI_PREMIUM_REASONING_MODEL="",
                      AI_PREMIUM_GRADING_MODEL="", AI_TEXT_REQUEST_TOKEN_CEILING=0,
                      AI_MAX_OUTPUT_CHARACTERS=200000)
        for state in (ai_service._RESERVATIONS, ai_service._PROVIDER_COOLDOWNS, ai_service._MODEL_COOLDOWNS,
                      ai_service._RECENT_RESULTS):
            state.clear()
            self.addCleanup(state.clear)
        self.context = application.app.app_context()
        self.context.push()
        self.addCleanup(self.context.pop)

    def call(self, answers, **values):
        fake = FakeOpenAI(answers)
        request = dict(task_type="lesson_generation", language="English", private_scope="student-1",
                       session_scope="session-1", model=MODEL, input="Notes about adding numbers.")
        request.update(values)
        with patch.object(ai_service, "OpenAI", fake):
            try:
                return ai_service.create_response(**request), fake
            except AIValidationError as error:
                return error, fake

    def record(self):
        return ai_service._read_usage_records()[-1]


class RecordAndLogTests(TruncationTestCase):
    def test_a_cut_off_answer_is_recorded_with_its_finish_reason(self):
        with self.assertLogs(application.app.logger, level="WARNING") as logs:
            result, fake = self.call([cut_off()])
        self.assertIsInstance(result, AIValidationError)
        record = self.record()
        self.assertIs(record["output_truncated"], True)
        self.assertEqual(record["finish_reason"], "max_output_tokens")
        self.assertEqual(record["error_category"], "invalid_json", "the category callers branch on is unchanged")
        lines = [line for line in logs.output if "ai.output_truncated" in line]
        self.assertEqual(len(lines), 1, logs.output)
        self.assertIn(f"ai.output_truncated task=lesson_generation model={MODEL} max_output_tokens=5000", lines[0])
        self.assertIn("finish_reason=max_output_tokens", lines[0])
        self.assertNotIn(SECRET_WORDS, "\n".join(logs.output), "no model content in the log")
        self.assertEqual(fake.requests[0]["max_output_tokens"], 5000)

    def test_a_complete_answer_is_recorded_as_complete(self):
        with patch.object(application.app.logger, "warning") as warning:
            result, _fake = self.call([sdk_response(json.dumps(LESSON))])
        self.assertEqual(result.validation, "valid")
        record = self.record()
        self.assertIs(record["output_truncated"], False)
        self.assertEqual(record["finish_reason"], "completed")
        self.assertFalse([call for call in warning.call_args_list if "ai.output_truncated" in str(call.args[0])])

    def test_a_cut_off_answer_that_still_validates_is_used_and_recorded(self):
        # The model stopped one closer short; the gateway's syntax repair completes it.
        text = json.dumps(LESSON)[:-1]
        with self.assertLogs(application.app.logger, level="WARNING") as logs:
            result, fake = self.call([cut_off(text)])
        self.assertEqual(result.validation, "valid")
        self.assertEqual(len(fake.requests), 1)
        self.assertIs(self.record()["output_truncated"], True)
        self.assertTrue(any("ai.output_truncated" in line for line in logs.output))


class RetryTests(TruncationTestCase):
    def test_no_corrective_retry_to_the_same_model_with_the_same_cap(self):
        result, fake = self.call([cut_off(), sdk_response(json.dumps(LESSON))])
        self.assertIsInstance(result, AIValidationError)
        self.assertEqual(len(fake.requests), 1, "the same cap would stop in the same place")
        record = self.record()
        self.assertEqual((record["provider_calls"], record["retry_count"]), (1, 0))

    def test_an_answer_that_is_merely_invalid_still_gets_its_retry(self):
        # Not cut off: a complete but wrong-shaped answer keeps the corrective retry.
        result, fake = self.call([sdk_response('{"lesson_title": "only a title"}'),
                                  sdk_response(json.dumps(LESSON))])
        self.assertEqual(result.validation, "valid")
        self.assertEqual(len(fake.requests), 2)
        self.assertIs(self.record()["output_truncated"], False)

    def test_a_different_fallback_model_still_gets_the_retry(self):
        # Kept on purpose: another model can fit the same answer into the same cap.
        application.app.config.update(GROQ_ANALYSIS_MODEL=FALLBACK)
        result, fake = self.call([cut_off(), sdk_response(json.dumps(LESSON), model=FALLBACK)])
        self.assertEqual(result.validation, "valid")
        self.assertEqual([request["model"] for request in fake.requests], [MODEL, FALLBACK])
        record = self.record()
        self.assertIs(record["output_truncated"], True, "the cut-off first call is still on the record")
        self.assertEqual(record["retry_count"], 1)


class ShapeTests(unittest.TestCase):
    """Every provider's way of saying "the output cap ended this answer"."""

    def test_responses_api(self):
        self.assertEqual(adapters.stop_reason_of(cut_off()), "max_output_tokens")
        self.assertEqual(adapters.stop_reason_of(sdk_response("{}")), "completed")
        filtered = sdk_response("", status="incomplete", reason="content_filter")
        self.assertEqual(adapters.stop_reason_of(filtered), "content_filter")
        self.assertTrue(adapters.stopped_at_output_cap(adapters.stop_reason_of(cut_off())))
        self.assertFalse(adapters.stopped_at_output_cap("content_filter"))
        self.assertFalse(adapters.stopped_at_output_cap("completed"))

    def test_chat_completions_and_messages(self):
        completion = ChatCompletion.model_validate({
            "id": "c1", "created": 1, "model": "gemini-x", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "length", "message": {"role": "assistant", "content": "{"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 400, "total_tokens": 410}})
        message = Message.model_validate({
            "id": "m1", "type": "message", "role": "assistant", "model": "claude-x",
            "content": [{"type": "text", "text": "{"}], "stop_reason": "max_tokens", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1024}})
        for raw, canonical in ((completion, adapters.chat_completion_to_canonical(completion, "gemini-x")),
                               (message, adapters.anthropic_message_to_canonical(message, "claude-x")),
                               (cut_off(), adapters.responses_to_canonical(cut_off(), MODEL))):
            with self.subTest(kind=type(raw).__name__):
                self.assertTrue(adapters.stopped_at_output_cap(adapters.stop_reason_of(raw)))
                self.assertTrue(adapters.stopped_at_output_cap(adapters.stop_reason_of(canonical)))

    def test_fakes_without_a_reason_say_nothing(self):
        self.assertEqual(adapters.stop_reason_of(SimpleNamespace(output_text="{}")), "")
        from unittest.mock import MagicMock
        self.assertEqual(adapters.stop_reason_of(MagicMock()), "")


if __name__ == "__main__":
    unittest.main()

"""Photo readings against the vision model's real limits, through the real gateway.

Every other photo test patches `app.create_response`, so the gateway, the routing and the
provider limits never ran on the chat-photo path - which is how a reading request too
large for qwen/qwen3.8-27b on Groq (8000 tokens per minute, at most 3 images, 2048 tokens
per image) shipped while the suite was green. Here only `OpenAI` inside
learnova.ai_services.service is replaced (the harness pattern of the image audit), so the
routes, claim_attachments, the reader, `create_response`, the preflight, the failure
mapping and the student-facing errors all run, and the exact provider request is captured.

Error bodies are the shapes Groq returned in the live test of 2026-11-01 (status, code,
type, "Limit N ... Requested M", retry-after), with the organisation id removed.
"""

import io
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vision_limits_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import httpx  # noqa: E402
import openai  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import routing  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.evaluation import vision_fixtures  # noqa: E402
from learnova.vision import intake  # noqa: E402
from learnova.vision import prompts as vision_prompts  # noqa: E402
from learnova.vision.reader import ImageSource, read_images  # noqa: E402

db = application.db
CASES = {case.name: case for case in vision_fixtures.build_cases(include_large=False)}
PHOTOS = ("printed_de_clear", "math_handwritten", "table_3x3", "handwriting_de_ruled")

READING = {
    "images": [{"index": 1, "kind": "handwritten_notes", "rotation": 0, "cut_off": False, "legibility": "clear"}],
    "blocks": [{"image": 1, "type": "formula", "text": "3x + 5 = 20", "latex": "3x + 5 = 20",
                "author": "student", "confidence": 0.95}],
    "diagrams": [], "uncertain": [], "clarification_question": "",
    "summary": "Eine Gleichung.", "language": "de",
}
SECRET_TEXT = "3x + 5 = 20"
REQUEST = httpx.Request("POST", "http://127.0.0.1:9/openai/v1/responses")


def groq_error(status, message, *, retry_after=None, kind="tokens", code="rate_limit_exceeded"):
    """An SDK error exactly as openai 1.93 builds it from a Groq error response."""

    headers = {"retry-after": str(retry_after)} if retry_after is not None else {}
    response = httpx.Response(status, request=REQUEST, headers=headers)
    body = {"message": message, "type": kind, "code": code}
    text = f"Error code: {status} - {{'error': {body!r}}}"
    error_class = {400: openai.BadRequestError, 429: openai.RateLimitError}.get(status, openai.APIStatusError)
    return error_class(text, response=response, body=body)


TPM_413 = ("Request too large for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on "
           "tokens per minute (TPM): Limit 8000, Requested 9105, please reduce your message size and try again. "
           "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing")
OTPM_429 = ("Request too large for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on "
            "output tokens per minute (OTPM): Limit 1000, Requested 1057. The request's expected output tokens "
            "exceed the enforced limit; reduce max_tokens (or the request's expected output) and try again. "
            "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing")
TPM_429 = ("Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on "
           "tokens per minute (TPM): Limit 8000, Used 6801, Requested 3099. Please try again in 14.25s. "
           "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing")
TPD_429 = ("Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on "
           "tokens per day (TPD): Limit 200000, Used 199671, Requested 3593. Please try again in 7m12.5s. "
           "Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing")
RPD_429 = ("Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` on "
           "requests per day (RPD): Limit 1000, Used 1000, Requested 1. Please try again in 1m26.4s.")


def count_images(value):
    if isinstance(value, dict):
        return (1 if value.get("type") == "input_image" else 0) + sum(count_images(item) for item in value.values())
    if isinstance(value, list):
        return sum(count_images(item) for item in value)
    return 0


class FakeProvider:
    """Stands in for the OpenAI SDK client at the one place the gateway builds it."""

    def __init__(self):
        self.calls = []
        self.clients = []
        self.image_failures = []     # raised in order by requests that carry images
        self.reply_failure = None
        self.reading = READING
        self.reply = "Gern. Der erste Schritt: subtrahiere 5."

    def __call__(self, **client_options):
        self.clients.append(client_options)
        provider = self

        class Responses:
            def create(self, **kwargs):
                provider.calls.append(kwargs)
                images = count_images(kwargs.get("input"))
                if images and provider.image_failures:
                    failure = provider.image_failures.pop(0)
                    if failure is not None:
                        raise failure
                if not images and provider.reply_failure is not None:
                    raise provider.reply_failure
                instructions = str(kwargs.get("instructions") or "")
                if "careful reader of school material" in instructions:
                    text = provider.reading if isinstance(provider.reading, str) else json.dumps(provider.reading)
                elif "careful handwriting reader" in instructions:
                    text = json.dumps({"regions": []})
                else:
                    text = provider.reply
                usage = SimpleNamespace(input_tokens=1000, output_tokens=200, total_tokens=1200)
                return SimpleNamespace(output_text=text, model=kwargs.get("model"), usage=usage)

        return SimpleNamespace(responses=Responses())

    def readings(self):
        return [call for call in self.calls if count_images(call.get("input"))]

    def replies(self):
        return [call for call in self.calls if not count_images(call.get("input"))]


class GatewayCase(unittest.TestCase):
    """The real routes and gateway, with only the SDK client faked."""

    SETTINGS = dict(
        TESTING=True, RUN_LIVE_AI_TEST=True, AI_MODE="live", ALLOW_LIVE_AI=True,
        FEATURE_ASSISTANT_CHAT=True, FEATURE_CHAT_IMAGES=True, GROQ_API_KEY="test-key",
        AI_VISION_REQUEST_TOKEN_CEILING=8000, AI_VISION_OUTPUT_TOKEN_CEILING=0, AI_REQUEST_TOKEN_CEILINGS="",
        AI_IMAGE_TOKEN_ESTIMATE=2048, CHAT_IMAGES_PER_MESSAGE=3, VISION_MAX_IMAGES_PER_REQUEST=3,
        AI_IMAGE_UNDERSTANDING_MAX_OUTPUT_TOKENS=3500, AI_IMAGE_UNDERSTANDING_MIN_OUTPUT_TOKENS=1500,
        AI_VISION_FALLBACK_MODEL="", VISION_TIME_BUDGET_SECONDS=100, VISION_OPTIONAL_PASS_MIN_SECONDS=15,
        FEATURE_HANDWRITING_SECOND_LOOK=True,
    )

    def setUp(self):
        self.original = dict(application.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.usage_path = root / "usage.jsonl"
        application.app.config.update(self.SETTINGS, AI_USAGE_PATH=str(self.usage_path),
                                      AI_CACHE_DIR=str(root / "cache"))
        self.reset_gateway_state()
        self.provider = FakeProvider()
        self.openai_patch = patch.object(ai_service, "OpenAI", self.provider)
        self.openai_patch.start()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")

    def tearDown(self):
        self.openai_patch.stop()
        self.reset_gateway_state()
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temporary.cleanup()

    @staticmethod
    def reset_gateway_state():
        for store in (ai_service._MODEL_COOLDOWNS, ai_service._PROVIDER_COOLDOWNS,
                      ai_service._LEARNED_OUTPUT_CAPS, ai_service._RECENT_RESULTS):
            store.clear()

    def register(self, name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery"})
        return client

    def attachment(self, name="math_handwritten", client=None):
        response = (client or self.client).post("/api/chat-attachments", data={
            "file": (io.BytesIO(CASES[name].data), f"{name}.jpg"), "purpose": "chat"},
            content_type="multipart/form-data")
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        return response.get_json()["attachment"]["id"]

    def conversation(self):
        return self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]["id"]

    def send(self, thread, **payload):
        return self.client.post(f"/api/assistant/conversations/{thread}/messages", json=payload)

    def rows(self, model):
        with application.app.app_context():
            return db.session.scalars(db.select(model)).all()

    def usage_records(self):
        if not self.usage_path.exists():
            return []
        return [json.loads(line) for line in self.usage_path.read_text(encoding="utf-8").splitlines() if line]

    def gateway_estimate(self, call):
        """The gateway's own input estimate for a captured provider request."""

        with application.app.app_context():
            return ai_service._estimate_tokens({"input": call["input"], "instructions": call["instructions"]})


# ---------------------------------------------------------------------------- the estimate
class EstimateAndLimitTests(GatewayCase):
    def test_an_image_counts_as_2048_tokens(self):
        with application.app.app_context():
            self.assertEqual(ai_service._estimate_tokens("data:image/jpeg;base64,AAA"), 2048)
            application.app.config["AI_IMAGE_TOKEN_ESTIMATE"] = 1024
            self.assertEqual(ai_service._estimate_tokens("data:image/jpeg;base64,AAA"), 1024)
        self.assertEqual(ai_service.IMAGE_TOKEN_ESTIMATE, 2048)

    def test_the_effective_limit_follows_the_ceiling_and_the_model(self):
        with application.app.app_context():
            self.assertEqual(application.chat_image_limit(), 2, "2 photos fit 8000 tokens with their minimum output")
            application.app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = 16000
            self.assertEqual(application.chat_image_limit(), 3)
            application.app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = 1_000_000
            self.assertEqual(application.vision_images_that_fit(), 4)
            self.assertEqual(application.chat_image_limit(), 3, "the model takes at most 3 images")
            application.app.config["VISION_MAX_IMAGES_PER_REQUEST"] = 2
            self.assertEqual(application.chat_image_limit(), 2)
            application.app.config.update(VISION_MAX_IMAGES_PER_REQUEST=3, CHAT_IMAGES_PER_MESSAGE=1)
            self.assertEqual(application.chat_image_limit(), 1, "the owner's cap still applies")
            application.app.config.update(CHAT_IMAGES_PER_MESSAGE=3, AI_VISION_REQUEST_TOKEN_CEILING=0)
            self.assertEqual(application.chat_image_limit(), 3, "no ceiling: the model's own limit")
            application.app.config["AI_REQUEST_TOKEN_CEILINGS"] = f"{application.VISION_MODEL}=8000"
            self.assertEqual(application.chat_image_limit(), 2, "a per-model ceiling counts like the global one")

    def test_the_arithmetic_behind_two_photos(self):
        """Reproduces the plan's arithmetic: 2 photos plus a 1500-token reading fit 8000, 3 do not."""

        with application.app.app_context():
            for count, fits in ((1, True), (2, True), (3, False)):
                content = [{"type": "input_text", "text": vision_prompts.reading_instructions(
                    "chat", image_count=count, question="x" * 600)}]
                for number in range(1, count + 1):
                    if count > 1:
                        content.append({"type": "input_text", "text": f"Image {number}:"})
                    content.append({"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"})
                estimate, _ = ai_service.estimate_request_tokens(
                    "image_understanding", input=[{"role": "user", "content": content}],
                    instructions=vision_prompts.SYSTEM)
                with self.subTest(count=count, estimate=estimate):
                    self.assertEqual(estimate + 1500 + 400 <= 8000, fits)

    def test_the_v2_reading_prompt_is_much_shorter_and_keeps_the_rules(self):
        text = vision_prompts.reading_instructions("chat", image_count=2)
        self.assertLessEqual(len(text), 2600, "v1 was 3666 characters")
        self.assertIn(vision_prompts.HANDWRITING_RULES, text)
        self.assertIn(vision_prompts.MATH_RULES, text)
        for field in ("images", "blocks", "diagrams", "uncertain", "clarification_question", "summary", "bbox",
                      "latex", "affects_answer"):
            self.assertIn(f'"{field}"', text)
        self.assertIn("only if confidence < 0.8", text, "a bbox only where the close-up needs one")
        self.assertEqual(ai_service.PROMPT_VERSIONS["image_understanding"], "image_understanding:v2")

    def test_n_photos_fit_the_ceiling_in_one_request(self):
        with application.app.app_context():
            limit = application.chat_image_limit()
        for count in range(1, limit + 1):
            with self.subTest(count=count):
                self.provider.calls.clear()
                photos = [self.attachment(name) for name in PHOTOS[:count]]
                response = self.send(self.conversation(), message="Stimmt das?", attachments=photos)
                self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
                readings = self.provider.readings()
                self.assertEqual(len(readings), 1, "one reading request for all photos of a message")
                reading = readings[0]
                self.assertEqual(count_images(reading["input"]), count)
                self.assertLessEqual(reading["max_output_tokens"], 3500)
                self.assertLessEqual(self.gateway_estimate(reading) + reading["max_output_tokens"], 8000)
                self.assertEqual(count_images(self.provider.replies()[0]["input"]), 0)

    def test_three_photos_at_the_default_ceiling_are_refused_before_any_call(self):
        photos = [self.attachment(name) for name in PHOTOS[:3]]
        response = self.send(self.conversation(), message="Drei Fotos", attachments=photos)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "attachments_invalid")
        self.assertIn("Attach at most 2 images", response.get_json()["error"])
        self.assertEqual(self.provider.calls, [])

    def test_the_lesson_builder_takes_the_same_limit(self):
        files = [(io.BytesIO(CASES[name].data), f"{name}.jpg") for name in PHOTOS[:3]]
        response = self.client.post("/api/analyze", data={"study_goal": "Gleichungen", "subject": "Mathematics",
                                                          "images": files}, content_type="multipart/form-data")
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "too_many_images"))
        self.assertIn("at most 2 photos", response.get_json()["error"])
        self.assertEqual(response.get_json()["details"]["max_photos"], 2)
        self.assertEqual(self.provider.calls, [])


# ---------------------------------------------------------------------------- the preflight
class PreflightTests(GatewayCase):
    def image_request(self, text="Lies das.", images=1):
        content = [{"type": "input_text", "text": text}]
        content += [{"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA"}] * images
        return [{"role": "user", "content": content}]

    def test_a_request_that_cannot_fit_is_refused_without_a_call(self):
        application.app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = 3000
        with application.app.test_request_context("/"):
            with self.assertRaises(ai_service.AIProviderError) as raised:
                ai_service.create_response(task_type="image_understanding", model=application.VISION_MODEL,
                                           instructions=vision_prompts.SYSTEM, input=self.image_request(),
                                           max_output_tokens=3500)
        self.assertEqual(raised.exception.category, "request_too_large")
        self.assertEqual(self.provider.calls, [])
        record = self.usage_records()[-1]
        self.assertEqual((record["event_kind"], record["error_category"], record["provider_called"]),
                         ("refused", "request_too_large", False))

    def test_the_output_cap_is_lowered_to_fit_and_never_raised(self):
        with application.app.test_request_context("/"):
            ai_service.create_response(task_type="image_understanding", model=application.VISION_MODEL,
                                       instructions=vision_prompts.SYSTEM, input=self.image_request(images=2),
                                       max_output_tokens=3500)
            sent = self.provider.calls[-1]
            estimate = self.gateway_estimate(sent)
            self.assertEqual(sent["max_output_tokens"], 8000 - estimate - 400)
            self.assertLess(sent["max_output_tokens"], 3500)
            self.assertGreaterEqual(sent["max_output_tokens"], 1500)
            ai_service.create_response(task_type="image_understanding", model=application.VISION_MODEL,
                                       instructions=vision_prompts.SYSTEM, input=self.image_request(images=1),
                                       max_output_tokens=900)
        self.assertEqual(self.provider.calls[-1]["max_output_tokens"], 900)

    def test_text_requests_are_never_clamped(self):
        """Orchestrator amendment: Groq admits text on input plus a small output estimate, so a
        lesson's output cap must never be lowered to fit a ceiling (it would truncate lessons)."""

        application.app.config.update(AI_VISION_REQUEST_TOKEN_CEILING=3000,
                                      AI_REQUEST_TOKEN_CEILINGS=f"{application.TUTOR_MODEL}=3000")
        # PF_FIX2 F1: the admission now also counts 0.4 of the output cap (800 here), so the input
        # that still fits under 3000 is smaller than the 9000 characters it was.
        with application.app.test_request_context("/"):
            ai_service.create_response(task_type="assistant_chat", model=application.TUTOR_MODEL,
                                       instructions="Hilf.", input="x" * 6000, max_output_tokens=2000)
        self.assertEqual(self.provider.calls[-1]["max_output_tokens"], 2000)
        with application.app.app_context():
            self.assertEqual(ai_service.request_ceiling(application.TUTOR_MODEL, has_images=False), 3000,
                             "the ceiling is known for text (PF4 builds the text admission check on it)")

    def test_ceilings_resolve_per_model(self):
        with application.app.app_context():
            self.assertEqual(ai_service.request_ceiling(application.VISION_MODEL, has_images=True), 8000)
            self.assertIsNone(ai_service.request_ceiling(application.VISION_MODEL, has_images=False))
            application.app.config["AI_REQUEST_TOKEN_CEILINGS"] = "groq:qwen/qwen3.8-27b=12000,gemini:see=30000"
            self.assertEqual(ai_service.request_ceiling("qwen/qwen3.8-27b", has_images=True), 12000)
            self.assertEqual(ai_service.request_ceiling("groq:qwen/qwen3.8-27b", has_images=True), 12000)
            self.assertEqual(ai_service.request_ceiling("gemini:see", has_images=True), 30000)
            application.app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = 0
            self.assertIsNone(ai_service.request_ceiling("other-model", has_images=True))

    def test_a_pinned_output_ceiling_lowers_every_image_request(self):
        application.app.config["AI_VISION_OUTPUT_TOKEN_CEILING"] = 1000
        with application.app.test_request_context("/"):
            ai_service.create_response(task_type="image_understanding", model=application.VISION_MODEL,
                                       instructions=vision_prompts.SYSTEM, input=self.image_request(),
                                       max_output_tokens=3500)
        self.assertEqual(self.provider.calls[-1]["max_output_tokens"], 1000)

    def test_a_time_budget_bounds_the_provider_timeout(self):
        with application.app.test_request_context("/"):
            ai_service.create_response(task_type="image_understanding", model=application.VISION_MODEL,
                                       instructions=vision_prompts.SYSTEM, input=self.image_request(),
                                       max_output_tokens=900, timeout_seconds=12)
            self.assertLessEqual(self.provider.clients[-1]["timeout"], 12)
            self.assertNotIn("timeout_seconds", self.provider.calls[-1], "never forwarded to the provider")
            ai_service.create_response(task_type="image_understanding", model=application.VISION_MODEL,
                                       instructions=vision_prompts.SYSTEM, input=self.image_request("Noch einmal."),
                                       max_output_tokens=900)
            self.assertEqual(self.provider.clients[-1]["timeout"], 75.0, "the budget ended with its call")


# ---------------------------------------------------------------------------- failures
class ProviderFailureTests(GatewayCase):
    # The gateway's refusal handling as written for the full 3500-token reading; the chat
    # photo's own 900-token reading (CHAT_FIX2) is in tests/test_chat_photo_live_fixes.py.
    SETTINGS = dict(GatewayCase.SETTINGS, AI_CHAT_PHOTO_READING_MAX_OUTPUT_TOKENS=3500)

    def failed_send(self, *failures, photos=1):
        self.provider.image_failures = list(failures)
        attachments = [self.attachment(name) for name in PHOTOS[:photos]]
        thread = self.conversation()
        response = self.send(thread, message="Was steht da?", attachments=attachments)
        return response, thread, attachments

    def assert_nothing_stored_and_photo_still_sendable(self, thread, attachments):
        self.assertEqual(self.rows(application.ConversationMessage), [])
        with application.app.app_context():
            conversation = db.session.get(application.Conversation, thread)
            self.assertIsNone(conversation.reply_started_at, "the send claim was released")
            self.assertTrue(all(row.sent_at is None and row.read_status != "ready"
                                for row in db.session.scalars(db.select(application.ChatAttachment))))
        self.provider.image_failures = []
        retried = self.send(thread, message="Was steht da?", attachments=attachments)
        self.assertEqual(retried.status_code, 201, retried.get_data(as_text=True))

    def test_each_provider_refusal_gets_its_own_honest_answer(self):
        cases = (
            # (failures, status, code, calls, retry-after)
            ((groq_error(413, "Request too large"),), 413, "photos_too_large", 1, None),
            ((groq_error(429, TPM_429, retry_after=15),), 503, "photo_reader_busy", 1, "15"),
            ((groq_error(429, TPM_429),), 503, "photo_reader_busy", 1, "60"),
            ((groq_error(429, TPD_429, retry_after=432),), 503, "photo_reader_daily_limit", 1, None),
            ((groq_error(429, RPD_429),), 503, "photo_reader_daily_limit", 1, None),
            ((groq_error(400, "image could not be decoded", kind="invalid_request_error",
                         code="invalid_image"),), 400, "image_rejected", 1, None),
            ((openai.APITimeoutError(request=REQUEST),), 504, "photo_read_timeout", 1, None),
            ((openai.APIConnectionError(request=REQUEST),), 503, "photo_reader_unavailable", 1, None),
            ((groq_error(404, "The model does not exist", kind="invalid_request_error",
                         code="model_not_found"),), 503, "photo_reader_unavailable", 1, None),
        )
        for failures, status, code, calls, retry_after in cases:
            with self.subTest(code=code, failure=type(failures[0]).__name__, retry_after=retry_after):
                self.setUp_case()
                with self.assertLogs(application.app.logger, level=logging.WARNING) as logs:
                    response, thread, attachments = self.failed_send(*failures)
                self.assertEqual((response.status_code, response.get_json()["code"]), (status, code),
                                 response.get_data(as_text=True))
                self.assertEqual(len(self.provider.readings()), calls, "a timeout or a refusal is not retried")
                self.assertEqual(response.headers.get("Retry-After"), retry_after)
                self.assertNotIn("temporarily unavailable", response.get_json()["error"])
                logged = "\n".join(logs.output)
                self.assertIn("vision.read_failed purpose=chat images=1", logged)
                self.assertNotIn(SECRET_TEXT, logged)
                self.assertNotIn("org_x", logged, "the provider's message text is never logged")
                if getattr(failures[0], "status_code", None):
                    self.assertIn(f"status={failures[0].status_code}", logged)
                self.assert_nothing_stored_and_photo_still_sendable(thread, attachments)

    def setUp_case(self):
        """A fresh database and gateway state for one subTest."""

        self.reset_gateway_state()
        self.provider.calls.clear()
        self.provider.image_failures = []
        # A fresh cache too: the previous case's successful retry cached this photo's reading.
        application.app.config["AI_CACHE_DIR"] = tempfile.mkdtemp(dir=self.temporary.name)
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")

    def test_the_failure_facts_are_logged_and_recorded_without_content(self):
        with self.assertLogs(application.app.logger, level=logging.WARNING) as logs:
            response, _thread, _attachments = self.failed_send(groq_error(429, TPM_429, retry_after=15))
        self.assertEqual(response.status_code, 503)
        logged = "\n".join(logs.output)
        self.assertIn("ai.provider_failure task=image_understanding", logged)
        self.assertIn("status=429 code=rate_limit_exceeded type=tokens category=provider_rate_limit", logged)
        self.assertIn("limit_scope=tpm limit=8000 requested=3099 retry_after=15", logged)
        record = next(item for item in self.usage_records() if item["task_type"] == "image_understanding")
        self.assertEqual((record["provider_status"], record["provider_error_code"], record["provider_error_type"]),
                         (429, "rate_limit_exceeded", "tokens"))
        self.assertTrue(record["prompt_version"].startswith("image_understanding:chat1+chat"),
                        "usage is recorded per feature")
        self.assertNotIn("org_x", self.usage_path.read_text(encoding="utf-8"))

    def test_a_tokens_per_minute_413_is_retried_once_with_a_smaller_output(self):
        response, _thread, _attachments = self.failed_send(groq_error(413, TPM_413))
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        first, second = self.provider.readings()
        self.assertEqual(second["max_output_tokens"], first["max_output_tokens"] - (9105 - 8000) - 400)

    def test_a_413_that_stays_is_too_large_after_one_smaller_try(self):
        response, _thread, _attachments = self.failed_send(groq_error(413, TPM_413), groq_error(413, TPM_413))
        self.assertEqual((response.status_code, response.get_json()["code"]), (413, "photos_too_large"))
        self.assertEqual(len(self.provider.readings()), 2, "one smaller try, then the honest answer")
        self.assertEqual(response.get_json()["details"]["max_photos"], 1)
        self.assertIn("Crop it to the task", response.get_json()["error"])

    def test_two_photos_too_large_are_told_to_send_one_at_a_time(self):
        response, _thread, _attachments = self.failed_send(groq_error(413, "Request too large"), photos=2)
        self.assertEqual((response.status_code, response.get_json()["code"]), (413, "photos_too_large"))
        self.assertEqual(response.get_json()["details"]["max_photos"], 1)
        self.assertIn("Send one photo at a time", response.get_json()["error"])
        application.app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = 16000
        self.setUp_case()
        response, _thread, _attachments = self.failed_send(groq_error(413, "Request too large"), photos=3)
        self.assertEqual(response.get_json()["details"]["max_photos"], 2)
        self.assertIn("Send at most 2 photos per message", response.get_json()["error"])

    def test_the_output_per_minute_limit_is_learned_from_the_refusal(self):
        """Groq's on_demand tier: qwen3.8-27b may produce 1000 output tokens per minute."""

        response, thread, _attachments = self.failed_send(groq_error(429, OTPM_429))
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        first, second = self.provider.readings()
        self.assertEqual((first["max_output_tokens"], second["max_output_tokens"]), (3500, 950))
        self.provider.calls.clear()
        again = self.send(thread, message="Und das?", attachments=[self.attachment("table_3x3")])
        self.assertEqual(again.status_code, 201)
        self.assertEqual([call["max_output_tokens"] for call in self.provider.readings()], [950],
                         "the next reading starts below the limit instead of paying a refusal")

    def test_a_full_output_minute_is_busy_not_too_large(self):
        full = ("Rate limit reached for model `qwen/qwen3.8-27b` in organization `org_x` service tier `on_demand` "
                "on output tokens per minute (OTPM): Limit 1000, Used 901, Requested 900. Please try again in 48.06s.")
        response, _thread, _attachments = self.failed_send(groq_error(429, full, retry_after=49))
        self.assertEqual((response.status_code, response.get_json()["code"]), (503, "photo_reader_busy"))
        self.assertEqual(response.headers["Retry-After"], "49")
        self.assertEqual(len(self.provider.readings()), 1, "waiting helps here; a smaller request would not")
        self.assertEqual(ai_service._LEARNED_OUTPUT_CAPS, {})

    def test_an_invalid_reading_is_not_read_again_by_the_same_model(self):
        self.provider.reading = '{"blocks": [{"type": "formula", "text": "3x + 5'
        response, thread, attachments = self.failed_send()
        self.assertEqual((response.status_code, response.get_json()["code"]), (422, "photo_read_failed"))
        self.assertEqual(len(self.provider.readings()), 1, "no same-model corrective retry for a reading")
        self.provider.reading = READING
        self.assert_nothing_stored_and_photo_still_sendable(thread, attachments)

    def test_the_lesson_and_answer_photo_routes_say_the_same(self):
        self.provider.image_failures = [groq_error(429, TPD_429)]
        files = [(io.BytesIO(CASES["math_handwritten"].data), "photo.jpg")]
        response = self.client.post("/api/analyze", data={"study_goal": "Gleichungen", "subject": "Mathematics",
                                                          "images": files}, content_type="multipart/form-data")
        self.assertEqual((response.status_code, response.get_json()["code"]), (503, "photo_reader_daily_limit"))
        self.assertEqual(self.provider.replies(), [], "no lesson is written from photos that were not read")


class FailureCategoryTests(unittest.TestCase):
    """_failure_details on the provider error shapes seen live."""

    def category(self, error):
        return ai_service._failure_details(error)[0]

    def test_groq_shapes(self):
        self.assertEqual(self.category(groq_error(413, TPM_413)), "request_too_large")
        # Kept a rate limit (Project Mode's pages wait on it, as before); an image reading
        # is retried below the output limit by the gateway itself.
        self.assertEqual(self.category(groq_error(429, OTPM_429)), "provider_rate_limit")
        self.assertEqual(self.category(groq_error(429, TPM_429)), "provider_rate_limit")
        self.assertEqual(self.category(groq_error(429, TPD_429)), "provider_daily_limit",
                         "the billing link in every Groq 429 body does not make a daily limit an empty account")
        self.assertEqual(self.category(groq_error(429, RPD_429)), "provider_daily_limit")
        self.assertEqual(self.category(groq_error(429, "You exceeded your current quota. insufficient_quota",
                                                  code="insufficient_quota")), "provider_quota_exhausted")
        self.assertEqual(self.category(openai.APITimeoutError(request=REQUEST)), "provider_timeout")

    def test_failure_facts_are_integers_and_tokens_only(self):
        facts = ai_service._provider_failure_facts(groq_error(429, OTPM_429, retry_after="48.06"))
        self.assertEqual(facts, {"status_code": 429, "provider_code": "rate_limit_exceeded", "provider_type": "tokens",
                                 "retry_after_seconds": 49, "limit_tokens": 1000, "requested_tokens": 1057,
                                 "limit_scope": "otpm"})
        error = ai_service.AIProviderError("request_too_large", "x", provider_code="Bad Code!<script>", **{
            key: value for key, value in facts.items() if key != "provider_code"})
        self.assertEqual(error.provider_code, "badcodescript")

    def test_routing_treats_the_new_categories_as_model_limits(self):
        self.assertIn("provider_daily_limit", routing.MODEL_SCOPED_FAILURES)
        self.assertIn("provider_daily_limit", routing.LIMIT_FAILURES)
        # About this request: a smaller one (half a topic batch, a smaller output) can fit,
        # so Project Mode must not read it as provider-wide.
        self.assertNotIn("request_too_large", routing.TERMINAL_FAILURES | routing.PROVIDER_WIDE_FAILURES
                         | routing.LIMIT_FAILURES)
        self.assertNotIn("request_too_large", application.PROVIDER_WIDE_CATEGORIES)
        self.assertNotIn("image_rejected", routing.TERMINAL_FAILURES | routing.PROVIDER_WIDE_FAILURES)
        policy = routing.RoutingPolicy(tier_models={routing.Tier.vision: "groq-eyes"},
                                       vision_fallback_model="groq:other-eyes")
        plan = routing.plan_route(routing.RoutingContext("image_understanding"), policy, {})
        self.assertEqual(routing.next_candidate(plan, 0, "request_too_large"), 1,
                         "a refused request still tries the vision fallback")
        self.assertIsNone(routing.next_candidate(plan, 1, "provider_timeout"))

    def test_a_daily_limit_cools_only_that_model_down(self):
        ai_service._MODEL_COOLDOWNS.clear()
        try:
            ai_service._note_provider_failure("groq", "provider_daily_limit", "qwen/qwen3.8-27b")
            self.assertTrue(ai_service._model_cooling_down("groq", "qwen/qwen3.8-27b"))
            self.assertFalse(ai_service._model_cooling_down("groq", "openai/gpt-oss-20b"))
        finally:
            ai_service._MODEL_COOLDOWNS.clear()


# ---------------------------------------------------------------------------- persistence
class ReadingCommittedTests(GatewayCase):
    def test_the_reading_survives_a_failed_reply(self):
        self.provider.reply_failure = groq_error(500, "internal", kind="server_error", code="internal")
        photo = self.attachment()
        response = self.send(self.conversation(), message="Was steht da?", attachments=[photo])
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["reply"]["error"])
        row = self.rows(application.ChatAttachment)[0]
        self.assertEqual(row.read_status, "ready")
        self.assertIn(SECRET_TEXT, row.reading_json)

    def test_the_reading_is_committed_before_the_reply_starts(self):
        photo, thread = self.attachment(), self.conversation()
        with patch.object(application, "generate_assistant_reply", side_effect=RuntimeError("worker killed")):
            try:
                response = self.send(thread, message="Was steht da?", attachments=[photo])
            except RuntimeError:
                response = None   # the test app propagates exceptions; production answers JSON 500
            if response is not None:
                self.assertEqual(response.status_code, 500)
        with application.app.app_context():
            db.session.remove()
            row = db.session.get(application.ChatAttachment, photo)
            self.assertEqual(row.read_status, "ready", "a killed reply does not throw the paid reading away")
            self.assertIn(SECRET_TEXT, row.reading_json)


# ---------------------------------------------------------------------------- the reader's clock
class ReaderTimeBudgetTests(unittest.TestCase):
    UNSURE = {**READING, "blocks": [{"image": 1, "type": "handwriting", "text": "x = [?7]",
                                     "bbox": [0.1, 0.1, 0.5, 0.2], "confidence": 0.4}]}
    SIDEWAYS = {**READING, "images": [{"index": 1, "rotation": 90}]}

    def read(self, answers, *, elapsed_per_call, budget, reserve=40.0, minimum=15.0):
        now = [0.0]
        calls = []

        def clock():
            return now[0]

        def call(**kwargs):
            calls.append(kwargs)
            now[0] += elapsed_per_call
            answer = answers[min(len(calls), len(answers)) - 1]
            return SimpleNamespace(output_text=json.dumps(answer))

        source = ImageSource(intake.prepare(CASES["math_handwritten"].data).data, "image/jpeg")
        outcome = read_images([source], call=call, parse=ai_service.parse_json, task_type="image_understanding",
                              model="vision", time_budget_seconds=budget, reserve_seconds=reserve,
                              min_optional_seconds=minimum, clock=clock)
        return outcome, calls

    def test_optional_passes_run_while_the_budget_allows(self):
        outcome, calls = self.read([self.UNSURE, {"regions": []}], elapsed_per_call=5, budget=100)
        self.assertEqual([call["task_type"] for call in calls], ["image_understanding", "handwriting_region_review"])
        self.assertEqual(outcome.skipped, ())
        self.assertEqual(calls[0]["timeout_seconds"], 60.0, "budget 100 less the 40 s kept for the reply")
        self.assertEqual(calls[1]["timeout_seconds"], 55.0)

    def test_the_close_up_is_skipped_when_the_budget_is_short(self):
        outcome, calls = self.read([self.UNSURE], elapsed_per_call=50, budget=100)
        self.assertEqual(len(calls), 1)
        self.assertEqual(outcome.skipped, ("second_look",))
        self.assertEqual(outcome.reading.text(), "x = [?7]", "the unclear part stays marked unclear")

    def test_the_turned_reread_is_skipped_when_the_budget_is_short(self):
        outcome, calls = self.read([self.SIDEWAYS], elapsed_per_call=50, budget=100)
        self.assertEqual(len(calls), 1)
        self.assertEqual((outcome.skipped, outcome.rotated), (("rotate",), ()))

    def test_without_a_budget_nothing_is_timed_or_skipped(self):
        source = ImageSource(intake.prepare(CASES["math_handwritten"].data).data, "image/jpeg")
        calls = []

        def call(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text=json.dumps(READING))

        outcome = read_images([source], call=call, parse=ai_service.parse_json, task_type="image_understanding",
                              model="vision")
        self.assertNotIn("timeout_seconds", calls[0])
        self.assertEqual(outcome.skipped, ())

    def test_the_model_image_limit_is_enforced(self):
        source = ImageSource(intake.prepare(CASES["table_3x3"].data).data, "image/jpeg")
        with self.assertRaises(ValueError):
            read_images([source] * 4, call=lambda **kwargs: None, parse=ai_service.parse_json,
                        task_type="image_understanding", model="vision", max_images=3)


# ---------------------------------------------------------------------------- page reading cooldown
PAGE_READING = json.dumps({"blocks": [{"type": "handwriting", "content": "Pflicht und Neigung", "confidence": 0.9,
                                       "bbox": []}], "detected_page_number": "", "warning": ""})


class PageReadingCooldownTests(GatewayCase):
    """Review B B-1: a Project Mode page reading has the offline OCR behind it, so a vision
    model that refused a moment ago is not asked again for the next page while it cools down
    (the plan has no second model: the cooldown used to be ignored). A chat photo keeps WP2's
    behaviour and still asks."""

    def call(self, task_type):
        with application.app.app_context():
            return ai_service.create_response(
                task_type=task_type, language="de", model=application.VISION_MODEL,
                instructions="Lies die Seite.", temperature=0,
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": f"Seite {len(self.provider.calls)}"},
                    {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "high"}]}])

    def test_a_rate_limited_vision_model_is_not_asked_again_for_the_next_page(self):
        self.provider.image_failures = [groq_error(429, TPM_429, retry_after=15)]
        with self.assertRaises(ai_service.AIProviderError) as first:
            self.call("ocr_document_recognition")
        self.assertEqual((first.exception.category, len(self.provider.readings())), ("provider_rate_limit", 1))
        with self.assertRaises(ai_service.AIProviderError) as second:
            self.call("ocr_document_recognition")
        self.assertEqual(second.exception.category, "provider_rate_limit")
        self.assertEqual(len(self.provider.readings()), 1, "no second refused request while it cools down")
        self.assertGreater(second.exception.retry_after_seconds or 0, 0)
        # Once the cooldown is over the model is asked again.
        ai_service._MODEL_COOLDOWNS.clear()
        self.provider.reply = PAGE_READING
        self.assertIn("Pflicht", self.call("ocr_document_recognition").output_text)
        self.assertEqual(len(self.provider.readings()), 2)

    def test_a_daily_limit_cooldown_says_daily_limit(self):
        self.provider.image_failures = [groq_error(429, RPD_429)]
        with self.assertRaises(ai_service.AIProviderError):
            self.call("ocr_document_recognition")
        with self.assertRaises(ai_service.AIProviderError) as second:
            self.call("ocr_document_recognition")
        self.assertEqual(second.exception.category, "provider_daily_limit")
        self.assertEqual(len(self.provider.readings()), 1)

    def test_a_chat_photo_still_asks_the_cooling_model(self):
        self.provider.image_failures = [groq_error(429, TPM_429, retry_after=15)]
        with self.assertRaises(ai_service.AIProviderError):
            self.call("image_understanding")
        try:
            self.call("image_understanding")
        except (ai_service.AIProviderError, ai_service.AIValidationError):
            pass
        self.assertEqual(len(self.provider.readings()), 2, "WP2's chat behaviour is unchanged")


if __name__ == "__main__":
    unittest.main()

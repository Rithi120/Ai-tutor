"""Shared image understanding: intake, reading, routing, chat photos and every image path.

Offline throughout. Model calls are patched at `app.create_response` (or, where the free
allowance must run, at the gateway behind it), so these exercise the genuine routes, the
database, ownership checks and failure paths without a key. The images are the synthetic,
project-owned cases in learnova/evaluation/vision_fixtures.py.
"""

import io
import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vision_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import vision  # noqa: E402
from learnova.ai_services import routing  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.evaluation import vision_eval, vision_fixtures  # noqa: E402
from learnova.evaluation.vision_metrics import cer, label_recall, math_exact, wer  # noqa: E402
from learnova.ocr.service import preprocess_document_image  # noqa: E402
from learnova.subscriptions import SubscriptionSettings  # noqa: E402
from learnova.vision import intake, notation  # noqa: E402
from learnova.vision import reading as vision_reading  # noqa: E402
from learnova.vision.reader import ImageSource, read_images  # noqa: E402
from tests.test_diagnostics_integration import LESSON  # noqa: E402

CASES = {case.name: case for case in vision_fixtures.build_cases(include_large=True)}
GROQ_BASE64_LIMIT = vision_eval.GROQ_BASE64_LIMIT

READING = {
    "images": [{"index": 1, "kind": "handwritten_notes", "rotation": 0, "cut_off": False, "legibility": "partly_unclear"}],
    "blocks": [
        {"image": 1, "type": "printed_text", "text": "Löse die Gleichung.", "author": "printed", "confidence": 0.97},
        {"image": 1, "type": "formula", "text": "3x + 5 = [?20]", "latex": "3x + 5 = 20", "author": "student",
         "confidence": 0.9},
        {"image": 1, "type": "formula", "text": "3x = 15", "latex": "3x = 15", "author": "student", "confidence": 0.95},
    ],
    "diagrams": [],
    "uncertain": [{"image": 1, "text": "20", "alternatives": ["26"], "reason": "smudged", "affects_answer": True}],
    "clarification_question": "Steht da 20 oder 26?",
    "summary": "Eine Gleichung mit dem ersten Umformungsschritt.",
    "language": "de",
}


class Usage:
    input_tokens = 100
    output_tokens = 40
    total_tokens = 140


class Response:
    def __init__(self, payload, model="test-model"):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.model = model
        self.usage = Usage()


class FakeAI:
    """Answers app.create_response by task type and records every call."""

    def __init__(self, **responses):
        self.responses = {"image_understanding": READING, "assistant_chat": "Gern. Steht da 20 oder 26?",
                          "tutor_chat": "Schauen wir uns den ersten Schritt an.", "lesson_generation": LESSON,
                          **responses}
        self.calls = []
        self._patch = patch.object(application, "create_response", side_effect=self)

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.responses.get(kwargs["task_type"])
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise AssertionError(f"unexpected AI task {kwargs['task_type']}")
        return Response(answer)

    def tasks(self):
        return [call["task_type"] for call in self.calls]

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def image_parts(value):
    found = []
    if isinstance(value, dict):
        if value.get("type") == "input_image":
            found.append(value)
        for item in value.values():
            found.extend(image_parts(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(image_parts(item))
    return found


def png(size, colour="white"):
    output = io.BytesIO()
    Image.new("RGB", size, colour).save(output, format="PNG")
    return output.getvalue()


# ---------------------------------------------------------------------------- intake
class IntakeTests(unittest.TestCase):
    def test_every_bad_file_is_refused_by_its_bytes_with_its_own_code(self):
        for case in CASES.values():
            if not case.reject:
                continue
            with self.subTest(case.name), self.assertRaises(intake.ImageProblem) as raised:
                intake.prepare(case.data)
            self.assertEqual(raised.exception.code, case.reject)

    def test_the_header_is_checked_before_anything_is_decoded(self):
        bomb = CASES["pixel_bomb"].data
        with patch.object(Image.Image, "load", side_effect=AssertionError("decoded a bomb")):
            with self.assertRaises(intake.ImageProblem):
                intake.prepare(bomb)

    def test_the_camera_rotation_tag_is_applied_and_metadata_is_dropped(self):
        prepared = intake.prepare(CASES["exif_rotated"].data)
        upright = intake.prepare(CASES["printed_de_clear"].data)
        self.assertEqual((prepared.width, prepared.height), (upright.width, upright.height))
        self.assertEqual(prepared.rotated_by_exif, 90)
        with Image.open(io.BytesIO(prepared.data)) as image:
            self.assertEqual(len(image.getexif()), 0, "EXIF (GPS, phone model) must not be kept")

    def test_a_transparent_screenshot_is_put_on_white_not_black(self):
        prepared = intake.prepare(CASES["transparent_screenshot"].data)
        with Image.open(io.BytesIO(prepared.data)) as image:
            self.assertEqual(image.convert("RGB").getpixel((3, 3)), (255, 255, 255))
        self.assertEqual(prepared.mime_type, "image/png", "line art stays lossless")
        # The old page pipeline turned the same screenshot black on black.
        with Image.open(io.BytesIO(preprocess_document_image(CASES["transparent_screenshot"].data).data)) as old:
            corner = old.convert("L").getpixel((3, 3))
            assert isinstance(corner, int)
            self.assertLess(corner, 40)

    def test_quality_issues_are_reported_and_never_refuse_a_photo(self):
        for case in CASES.values():
            if case.reject:
                continue
            with self.subTest(case.name):
                prepared = intake.prepare(case.data)
                self.assertEqual(set(prepared.quality.issues), set(case.issues))

    def test_a_phone_photo_is_sent_within_the_provider_limit(self):
        phone = CASES["phone_12mp"].data
        old = preprocess_document_image(phone).data
        self.assertGreater(len(old) * 4 / 3, GROQ_BASE64_LIMIT, "the root cause: pages went out over the limit")
        sized, mime = intake.provider_copy(intake.prepare(phone).data)
        self.assertLessEqual(len(sized) * 4 / 3, GROQ_BASE64_LIMIT)
        with Image.open(io.BytesIO(sized)) as image:
            self.assertLessEqual(max(image.size), intake.IntakeLimits().provider_edge)
        self.assertEqual(mime, "image/jpeg")

    def test_the_gateway_safety_net_shrinks_only_oversized_images(self):
        small = intake.data_url(png((400, 300)), "image/png")
        self.assertEqual(intake.fit_data_url(small), small)
        big = intake.data_url(preprocess_document_image(CASES["phone_12mp"].data).data, "image/png")
        fitted = intake.fit_data_url(big)
        self.assertLess(len(fitted), GROQ_BASE64_LIMIT)
        self.assertTrue(fitted.startswith("data:image/"))
        self.assertEqual(intake.fit_data_url("not a data url"), "not a data url")

    def test_rotate_turns_clockwise_and_keeps_the_picture(self):
        data, _mime = intake.rotate(intake.prepare(CASES["sideways_no_exif"].data).data, 270)
        self.assertEqual(intake.image_size(data), (1240, 1754))

    def test_limits_come_from_configuration(self):
        limits = intake.limits_from_config({"VISION_PROVIDER_EDGE_PX": 1024, "VISION_PROVIDER_MAX_BYTES": 500_000})
        sized, _ = intake.provider_copy(intake.prepare(CASES["phone_12mp"].data).data, limits)
        self.assertLessEqual(max(intake.image_size(sized)), 1024)
        self.assertLessEqual(len(sized), 500_000)

    def test_the_fixtures_never_draw_a_line_off_the_page(self):
        with self.assertRaises(ValueError):
            vision_fixtures.page(["x" * 400], size=(400, 300))


# ---------------------------------------------------------------------------- reading
class ReadingTests(unittest.TestCase):
    def test_garbage_yields_an_empty_reading_not_an_exception(self):
        for payload in (None, "text", [], {"blocks": "nope"}, {"blocks": [1, None, {"text": ""}, {"type": "handwriting"}]}):
            reading = vision.normalize_reading(payload, 1)
            self.assertFalse(reading.readable)

    def test_confidence_is_never_invented(self):
        reading = vision.normalize_reading({"blocks": [{"type": "handwriting", "text": "Hallo"}]}, 1)
        self.assertIsNone(reading.confidence)
        self.assertFalse(reading.confidence_reported)
        reading = vision.normalize_reading({"blocks": [{"type": "handwriting", "text": "Hallo", "confidence": 7}]}, 1)
        self.assertEqual(reading.confidence, 1.0)

    def test_inline_unclear_marks_are_kept_and_listed(self):
        reading = vision.normalize_reading({"blocks": [{"type": "formula", "text": "x = [?7] + 2"}]}, 1)
        self.assertIn("[?7]", reading.text())
        self.assertEqual([item.text for item in reading.uncertain], ["7"])
        self.assertFalse(reading.needs_clarification)

    def test_an_unclear_part_that_matters_asks_for_clarification(self):
        reading = vision.normalize_reading(READING, 1)
        self.assertTrue(reading.needs_clarification)
        self.assertEqual(reading.student_text(), "3x + 5 = [?20]\n3x = 15")

    def test_values_are_bounded_and_typed(self):
        reading = vision.normalize_reading({"images": [{"index": 9, "rotation": 45}], "blocks": [
            {"image": 9, "type": "poetry", "text": "a" * 9000, "bbox": [0.5, 0.5, 0.1, 0.1]}]}, 2)
        block = reading.blocks[0]
        self.assertEqual((block.image, block.type, block.bbox, len(block.text)), (1, "uncertain", (), 4000))
        self.assertEqual([info.rotation for info in reading.images], [0, 0])

    def test_split_and_round_trip_keep_each_images_part(self):
        payload = {"blocks": [{"image": 1, "type": "handwriting", "text": "eins"},
                              {"image": 2, "type": "handwriting", "text": "zwei [?drei]"}],
                   "clarification_question": "Welche Zahl?"}
        first, second = vision_reading.split_reading(vision.normalize_reading(payload, 2), 2)
        self.assertEqual((first.text(), second.text()), ("eins", "zwei [?drei]"))
        self.assertEqual(second.uncertain[0].image, 1)
        again = vision.reading_from_dict(json.loads(json.dumps(second.to_dict())))
        self.assertEqual(again.text(), second.text())
        self.assertEqual(again.clarification_question, "Welche Zahl?")

    def test_the_context_for_a_model_keeps_uncertainty_formulas_and_diagrams(self):
        reading = vision.normalize_reading({
            **READING,
            "blocks": [*READING["blocks"], {"image": 1, "type": "crossed_out", "text": "3x = 25"}],
            "diagrams": [{"image": 1, "description": "A right triangle", "labels": ["A", "c = 8 cm"]}]}, 1)
        context = vision.reading_context(reading)
        for expected in ("[?20]", "could also be 26", "(matters for the answer)", "[LaTeX: 3x + 5 = 20]",
                         "(crossed out) 3x = 25", "Labels: A, c = 8 cm", "(written by the student)"):
            self.assertIn(expected, context)
        self.assertLessEqual(len(vision.reading_context(reading, max_characters=80)), 130)


class NotationAndMetricTests(unittest.TestCase):
    def test_the_same_expression_written_differently_is_the_same(self):
        for left, right in (("3x + 5 = 20", "$3x+5=20$"), ("a² + b² = c²", "a^{2}+b^2=c^2"),
                            ("\\frac{2}{3} + \\frac{1}{4}", "2/3+1/4"), ("−4,5 · 2 = −9", "-4,5 \\cdot 2 = -9"),
                            ("\\sqrt{16} = 4", "sqrt(16)=4"), ("x ≤ 3", "x \\leq 3")):
            with self.subTest(left):
                self.assertTrue(notation.same_math(left, right))

    def test_a_different_reading_is_different(self):
        for left, right in (("3x + 5 = 20", "3x + 5 = 26"), ("-4,5", "-4.5"), ("x^2", "x2"), ("-9", "9")):
            with self.subTest(left):
                self.assertFalse(notation.same_math(left, right))

    def test_error_rates(self):
        self.assertEqual(cer("Hallo Welt", "Hallo Welt"), 0)
        self.assertAlmostEqual(cer("abcd", "abed"), 0.25)
        self.assertAlmostEqual(wer("eins zwei drei", "eins zwo drei"), 1 / 3)
        self.assertEqual(cer("x = [?7]", "x = 7"), 0, "markers are not errors")
        self.assertEqual(math_exact(("3x=15", "x=5"), ["3x = 15", "x = 6"]), 0.5)
        self.assertEqual(label_recall(("A", "c = 8 cm"), "Labels: A, c=8 cm"), 1.0)


# ---------------------------------------------------------------------------- reader
class ReaderTests(unittest.TestCase):
    def sources(self, *names):
        return [ImageSource(intake.prepare(CASES[name].data).data, "image/jpeg") for name in names]

    def read(self, responses, images, **options):
        calls = []

        def call(**kwargs):
            calls.append(kwargs)
            answer = responses[min(len(calls), len(responses)) - 1]
            if isinstance(answer, Exception):
                raise answer
            return Response(answer)

        outcome = read_images(images, call=call, parse=ai_service.parse_json, task_type="image_understanding",
                              model="vision-model", **options)
        return outcome, calls

    def test_all_images_of_a_request_are_read_together_and_sized(self):
        outcome, calls = self.read([READING], self.sources("phone_12mp", "printed_de_clear"))
        self.assertEqual(len(calls), 1)
        parts = image_parts(calls[0]["input"])
        self.assertEqual(len(parts), 2)
        self.assertTrue(all(len(part["image_url"]) <= GROQ_BASE64_LIMIT for part in parts))
        texts = [part["text"] for part in calls[0]["input"][0]["content"] if part["type"] == "input_text"]
        self.assertIn("Image 1:", texts)
        self.assertIn("Image 2:", texts)
        self.assertEqual((calls[0]["temperature"], calls[0]["model"]), (0, "vision-model"))
        self.assertEqual(outcome.provider_calls, 1)

    def test_a_sideways_image_is_turned_and_read_once_more(self):
        sideways = {**READING, "images": [{"index": 1, "rotation": 90}]}
        outcome, calls = self.read([sideways, READING], self.sources("sideways_no_exif"))
        self.assertEqual((len(calls), outcome.rotated), (2, (1,)))
        self.assertEqual(intake.image_size(outcome.images[0].data), (1240, 1754))
        self.assertIn("turned upright", " ".join(outcome.reading.warnings))

    def test_a_model_that_keeps_saying_turn_it_is_not_believed_twice(self):
        sideways = {**READING, "images": [{"index": 1, "rotation": 90}]}
        _outcome, calls = self.read([sideways], self.sources("printed_de_clear"))
        self.assertEqual(len(calls), 2)

    def test_the_second_look_replaces_only_a_more_confident_reading(self):
        unsure = {**READING, "blocks": [{"image": 1, "type": "handwriting", "text": "x = [?7]",
                                         "bbox": [0.1, 0.1, 0.5, 0.2], "confidence": 0.4}],
                  "uncertain": [{"image": 1, "text": "7", "alternatives": ["1"], "affects_answer": True}]}
        confident = {"regions": [{"index": 1, "content": "x = 1", "confidence": 0.92, "illegible": False}]}
        outcome, calls = self.read([unsure, confident], self.sources("math_handwritten"))
        self.assertEqual([call["task_type"] for call in calls], ["image_understanding", "handwriting_region_review"])
        self.assertEqual(outcome.reading.text(), "x = 1")
        self.assertEqual(outcome.reading.uncertain, ())
        self.assertEqual(outcome.reading.clarification_question, "")
        doubtful = {"regions": [{"index": 1, "content": "x = 1", "confidence": 0.3, "illegible": False}]}
        outcome, _ = self.read([unsure, doubtful], self.sources("math_handwritten"))
        self.assertEqual(outcome.reading.text(), "x = [?7]")

    def test_a_failed_second_look_keeps_the_first_reading(self):
        unsure = {**READING, "blocks": [{"image": 1, "type": "handwriting", "text": "x = [?7]",
                                         "bbox": [0.1, 0.1, 0.5, 0.2], "confidence": 0.4}]}
        outcome, _ = self.read([unsure, ai_service.AIProviderError("provider_timeout", "t")],
                               self.sources("math_handwritten"))
        self.assertEqual(outcome.reading.text(), "x = [?7]")

    def test_the_second_look_can_be_switched_off(self):
        unsure = {**READING, "blocks": [{"image": 1, "type": "handwriting", "text": "x = [?7]",
                                         "bbox": [0.1, 0.1, 0.5, 0.2], "confidence": 0.4}]}
        _outcome, calls = self.read([unsure], self.sources("math_handwritten"), second_look=False)
        self.assertEqual(len(calls), 1)

    def test_a_failed_reading_is_reported_not_hidden(self):
        with self.assertRaises(ai_service.AIProviderError):
            self.read([ai_service.AIProviderError("provider_timeout", "t")], self.sources("printed_de_clear"))
        with self.assertRaises(ValueError):
            self.read([READING], self.sources(*["printed_de_clear"] * 5))


# ---------------------------------------------------------------------------- routing
class RoutingTests(unittest.TestCase):
    POLICY = routing.RoutingPolicy(tier_models={
        routing.Tier.fast: "fast", routing.Tier.standard: "standard", routing.Tier.strong: "strong",
        routing.Tier.vision: "vision"}, premium_models={"reasoning": "openai:gpt-x"})

    def plan(self, task, policy=None, availability=None, **context):
        return routing.plan_route(routing.RoutingContext(task, **context), policy or self.POLICY, availability or {})

    def test_a_request_with_a_picture_only_reaches_models_that_can_see(self):
        usable = {"openai": routing.ProviderAvailability(has_key=True, budget_ok=True)}
        for task in ("assistant_chat", "tutor_chat", "lesson_generation", "answer_evaluation"):
            with self.subTest(task):
                plan = self.plan(task, availability=usable, signals={"has_images": True},
                                 requested_model="standard", deep=True)
                self.assertEqual(plan.models, ["vision"])
                self.assertIsNone(plan.premium_slot)

    def test_the_vision_fallback_is_used_only_when_configured_and_usable(self):
        policy = routing.RoutingPolicy(tier_models=self.POLICY.tier_models, vision_fallback_model="gemini:see")
        self.assertEqual(self.plan("image_understanding", policy).models, ["vision"])
        usable = {"gemini": routing.ProviderAvailability(has_key=True, budget_ok=True)}
        self.assertEqual(self.plan("image_understanding", policy, usable).models, ["vision", "gemini:see"])

    def test_an_owner_vouched_vision_model_may_be_requested(self):
        policy = routing.RoutingPolicy(tier_models=self.POLICY.tier_models, vision_models=frozenset({"big-eyes"}))
        self.assertEqual(self.plan("image_understanding", policy, requested_model="big-eyes").models,
                         ["big-eyes", "vision"])

    def test_text_requests_route_as_before(self):
        self.assertEqual(self.plan("lesson_generation").tier, routing.Tier.standard)


class GatewayImageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=False, ENV_NAME="development", AI_MODE="live", ALLOW_LIVE_AI=True,
            AI_CACHE_DIR=str(root / "cache"), AI_USAGE_PATH=str(root / "usage.jsonl"),
            AI_MAX_REQUESTS_PER_USER_HOUR=10_000, AI_MAX_REQUESTS_PER_USER_DAY=10_000)
        ai_service._RECENT_RESULTS.clear()
        self.context = application.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temporary.cleanup()

    def test_an_oversized_picture_is_shrunk_and_sent_to_the_vision_model(self):
        sent = []

        def provider(**kwargs):
            sent.append(kwargs)
            return Response("Antwort")

        big = intake.data_url(preprocess_document_image(CASES["phone_12mp"].data).data, "image/png")
        with patch.object(ai_service, "_provider_response", side_effect=provider):
            ai_service.create_response(
                task_type="assistant_chat", model=application.TUTOR_MODEL, language="German",
                input=[{"role": "user", "content": [{"type": "input_text", "text": "Was steht da?"},
                                                    {"type": "input_image", "image_url": big}]}])
        self.assertEqual(sent[0]["model"], application.VISION_MODEL.partition(":")[2] or application.VISION_MODEL)
        self.assertLessEqual(len(image_parts(sent[0]["input"])[0]["image_url"]), GROQ_BASE64_LIMIT)


# ---------------------------------------------------------------------------- routes
class RouteTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(TESTING=True, FEATURE_ASSISTANT_CHAT=True, FEATURE_CHAT_IMAGES=True,
                                      AI_MODE="cached")
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = self.register("alice")

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def register(self, name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery"})
        return client

    def upload(self, client=None, name="math_handwritten", purpose="chat", data=None):
        response = (client or self.client).post("/api/chat-attachments", data={
            "file": (io.BytesIO(data if data is not None else CASES[name].data), "photo.jpg"), "purpose": purpose},
            content_type="multipart/form-data")
        return response

    def attachment(self, client=None, **options):
        response = self.upload(client, **options)
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        return response.get_json()["attachment"]["id"]

    def conversation(self, client=None):
        return (client or self.client).post("/api/assistant/conversations", json={}).get_json()["conversation"]["id"]

    def rows(self):
        with application.app.app_context():
            return application.db.session.scalars(application.db.select(application.ChatAttachment)).all()

    def start_lesson(self, client=None):
        with FakeAI():
            response = (client or self.client).post("/api/analyze", data={"study_goal": "Gleichungen", "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["session_id"]


class ChatAttachmentRouteTests(RouteTestCase):
    def test_an_upload_is_stored_re_encoded_and_costs_no_ai_call(self):
        with FakeAI() as ai:
            response = self.upload()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ai.calls, [])
        data = response.get_json()["attachment"]
        self.assertEqual((data["status"], data["available"]), ("pending", True))
        image = self.client.get(data["url"])
        self.assertEqual((image.status_code, image.mimetype), (200, "image/jpeg"))
        self.assertIn("no-store", image.headers["Cache-Control"])
        self.assertNotEqual(image.data, CASES["math_handwritten"].data, "the upload itself is never kept")

    def test_bad_files_get_their_own_clear_answer(self):
        for name, status, code in (("heic_header", 400, "heic_unsupported"), ("corrupt_jpeg", 400, "image_corrupt"),
                                   ("pdf_as_photo", 400, "pdf_not_image"), ("text_renamed", 400, "unsupported_type")):
            with self.subTest(name):
                response = self.upload(name=name)
                self.assertEqual((response.status_code, response.get_json()["code"]), (status, code))
        # 118 kB against a 50 kB limit: over the limit, and small enough that the test
        # client keeps the request in memory instead of spooling it to a temporary file.
        application.app.config["VISION_MAX_UPLOAD_BYTES"] = 50_000
        response = self.upload(name="printed_de_clear")
        self.assertEqual((response.status_code, response.get_json()["code"]), (413, "file_too_large"))

    def test_the_same_photo_twice_is_one_attachment(self):
        first, second = self.upload(), self.upload()
        self.assertEqual(first.get_json()["attachment"]["id"], second.get_json()["attachment"]["id"])
        self.assertTrue(second.get_json()["duplicate"])
        self.assertEqual(len(self.rows()), 1)

    def test_signed_out_and_switched_off_are_refused(self):
        anonymous = application.app.test_client().post(
            "/api/chat-attachments", data={"file": (io.BytesIO(CASES["printed_de_clear"].data), "a.jpg")},
            content_type="multipart/form-data")
        self.assertIn(anonymous.status_code, (302, 401))
        application.app.config["FEATURE_CHAT_IMAGES"] = False
        self.assertEqual(self.upload().status_code, 404)

    def test_nobody_else_can_see_delete_or_send_your_photo(self):
        photo = self.attachment()
        bob = self.register("bob")
        self.assertEqual(bob.get(f"/api/chat-attachments/{photo}/image").status_code, 404)
        self.assertEqual(bob.delete(f"/api/chat-attachments/{photo}").status_code, 404)
        with FakeAI() as ai:
            sent = bob.post(f"/api/assistant/conversations/{self.conversation(bob)}/messages",
                            json={"message": "Was ist das?", "attachments": [photo]})
            session_id = self.start_lesson(bob)
            chat = bob.post("/api/chat", json={"session_id": session_id, "message": "Hi", "attachments": [photo]})
            answer = bob.post("/api/answer/photo", json={"session_id": session_id, "attachments": [photo]})
        self.assertEqual([sent.status_code, chat.status_code, answer.status_code], [400, 400, 400])
        self.assertNotIn("image_understanding", ai.tasks(), "a refused photo is never read")

    def test_an_unsent_photo_can_be_removed_but_a_sent_one_cannot(self):
        photo = self.attachment()
        self.assertEqual(self.client.delete(f"/api/chat-attachments/{photo}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/chat-attachments/{photo}/image").status_code, 404)
        photo = self.attachment()
        with FakeAI():
            self.client.post(f"/api/assistant/conversations/{self.conversation()}/messages",
                             json={"message": "Hilf mir", "attachments": [photo]})
        self.assertEqual(self.client.delete(f"/api/chat-attachments/{photo}").status_code, 409)

    def test_retention_deletes_unsent_photos_and_drops_old_pictures_but_keeps_the_reading(self):
        stale, sent = self.attachment(name="printed_de_clear"), self.attachment()
        with FakeAI():
            self.client.post(f"/api/assistant/conversations/{self.conversation()}/messages",
                             json={"message": "Hilf mir", "attachments": [sent]})
        with application.app.app_context():
            for row in application.db.session.scalars(application.db.select(application.ChatAttachment)):
                row.created_at = application.utcnow() - timedelta(days=2)
                if row.sent_at:
                    row.sent_at = application.utcnow() - timedelta(days=31)
            application.db.session.commit()
            application.cleanup_chat_attachments()
            application.db.session.commit()
            self.assertIsNone(application.db.session.get(application.ChatAttachment, stale))
            kept = application.db.session.get(application.ChatAttachment, sent)
            assert kept is not None
            self.assertIsNone(kept.data)
            self.assertIn("3x + 5", kept.reading_json)
        self.assertEqual(self.client.get(f"/api/chat-attachments/{sent}/image").status_code, 410)


class UprightPhotoTests(RouteTestCase):
    """Sideways photos are turned upright locally, before any model sees them."""

    def stored_size(self, photo_id):
        with application.app.app_context():
            row = application.db.session.get(application.ChatAttachment, photo_id)
            assert row is not None and row.data is not None
            return vision.image_size(row.data)

    def test_switched_off_a_sideways_photo_is_kept_as_it_is(self):
        application.app.config["VISION_LOCAL_ORIENTATION"] = False
        self.assertEqual(self.stored_size(self.attachment(name="sideways_no_exif")), (1754, 1240))

    def test_the_probe_turns_only_what_it_reports_as_sideways(self):
        application.app.config["VISION_LOCAL_ORIENTATION"] = True
        sideways = SimpleNamespace(rotation=270)
        with patch.object(application.local_ocr, "available", return_value=True), \
                patch.object(application.local_ocr, "recognize", return_value=sideways) as probe:
            turned = self.attachment(name="sideways_no_exif")
            portrait = self.attachment(name="printed_de_clear")
        self.assertEqual(self.stored_size(turned), (1240, 1754))
        self.assertEqual(self.stored_size(portrait), (1240, 1754))
        self.assertEqual(probe.call_count, 1, "portrait photos are never probed")

    def test_a_failing_probe_never_fails_the_upload(self):
        application.app.config["VISION_LOCAL_ORIENTATION"] = True
        with patch.object(application.local_ocr, "available", return_value=True), \
                patch.object(application.local_ocr, "recognize", side_effect=RuntimeError("engine")):
            photo = self.attachment(name="table_3x3")
        self.assertEqual(self.stored_size(photo), (1240, 520))

    @unittest.skipUnless(vision_eval.local_reader() is not None, "RapidOCR is not installed")
    def test_with_the_real_engine_sideways_turns_and_upright_landscape_stays(self):
        application.app.config["VISION_LOCAL_ORIENTATION"] = True
        self.assertEqual(self.stored_size(self.attachment(name="sideways_no_exif")), (1240, 1754))
        self.assertEqual(self.stored_size(self.attachment(name="table_3x3")), (1240, 520))


class AssistantImageTests(RouteTestCase):
    def send(self, conversation, **payload):
        return self.client.post(f"/api/assistant/conversations/{conversation}/messages", json=payload)

    def test_the_photo_reaches_the_vision_model_and_its_reading_reaches_the_reply(self):
        photo, thread = self.attachment(), self.conversation()
        with FakeAI() as ai:
            response = self.send(thread, message="Ist mein Rechenweg richtig?", attachments=[photo])
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(ai.tasks(), ["image_understanding", "assistant_chat"])
        read, reply = ai.calls
        self.assertEqual(read["model"], application.VISION_MODEL)
        self.assertTrue(image_parts(read["input"])[0]["image_url"].startswith("data:image/"))
        self.assertIn("Ist mein Rechenweg richtig?", json.dumps(read["input"], ensure_ascii=False))
        self.assertEqual(image_parts(reply["input"]), [], "the chat model gets the reading, not the picture")
        self.assertIn("3x + 5 = [?20]", reply["input"])
        self.assertIn("IMAGES:", reply["instructions"])
        data = response.get_json()
        self.assertEqual(data["message"]["attachments"][0]["id"], photo)
        self.assertEqual(data["image_notes"]["unclear"], ["20"])

    def test_a_photo_alone_is_a_message(self):
        with FakeAI():
            response = self.send(self.conversation(), message="", attachments=[self.attachment()])
        self.assertEqual(response.get_json()["message"]["content"], "Please look at my photo.")

    def test_later_turns_reuse_the_reading_instead_of_reading_again(self):
        thread = self.conversation()
        with FakeAI() as ai:
            self.send(thread, message="Was steht da?", attachments=[self.attachment()])
            self.send(thread, message="Und der nächste Schritt?")
        self.assertEqual(ai.tasks().count("image_understanding"), 1)
        self.assertIn("3x + 5 = [?20]", ai.calls[-1]["input"])
        self.assertIn("IMAGES:", ai.calls[-1]["instructions"])

    def test_a_failed_reading_stores_nothing_and_the_photo_can_be_sent_again(self):
        photo, thread = self.attachment(), self.conversation()
        with FakeAI(image_understanding=ai_service.AIProviderError("provider_timeout", "t")):
            failed = self.send(thread, message="Was steht da?", attachments=[photo])
        self.assertGreaterEqual(failed.status_code, 400)
        with application.app.app_context():
            self.assertEqual(application.db.session.scalar(
                application.db.select(application.db.func.count(application.ConversationMessage.id))), 0)
        with FakeAI() as ai:
            retried = self.send(thread, message="Was steht da?", attachments=[photo])
        self.assertEqual(retried.status_code, 201)
        self.assertEqual(ai.tasks(), ["image_understanding", "assistant_chat"])

    def test_a_sent_photo_cannot_be_attached_again_and_limits_hold(self):
        photo, thread = self.attachment(), self.conversation()
        with FakeAI():
            self.send(thread, message="Eins", attachments=[photo])
            again = self.send(thread, message="Zwei", attachments=[photo])
        self.assertEqual(again.status_code, 400)
        application.app.config["CHAT_IMAGES_PER_MESSAGE"] = 1
        two = [self.attachment(name="printed_de_clear"), self.attachment(name="table_3x3")]
        self.assertEqual(self.send(thread, message="Drei", attachments=two).status_code, 400)
        self.assertEqual(self.send(thread, message="Vier", attachments="nope").status_code, 400)

    def test_deleting_the_conversation_deletes_its_photos(self):
        thread = self.conversation()
        with FakeAI():
            self.send(thread, message="Eins", attachments=[self.attachment()])
        self.assertEqual(self.client.delete(f"/api/assistant/conversations/{thread}").status_code, 200)
        self.assertEqual(self.rows(), [])

    def test_text_only_chat_is_unchanged(self):
        with FakeAI() as ai:
            response = self.send(self.conversation(), message="Erkläre Brüche.")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(ai.tasks(), ["assistant_chat"])
        self.assertNotIn("IMAGES:", ai.calls[0]["instructions"])
        self.assertNotIn("attachments", response.get_json()["message"])


class TutorChatImageTests(RouteTestCase):
    def test_a_photo_in_the_lesson_chat_is_read_and_used(self):
        session_id = self.start_lesson()
        photo = self.attachment()
        with FakeAI() as ai:
            response = self.client.post("/api/chat", json={"session_id": session_id, "message": "Stimmt das?",
                                                           "attachments": [photo]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(ai.tasks(), ["image_understanding", "tutor_chat"])
        self.assertIn("3x + 5 = [?20]", ai.calls[1]["input"])
        self.assertIn("IMAGES:", ai.calls[1]["instructions"])
        self.assertEqual(response.get_json()["attachments"][0]["id"], photo)
        self.assertIsNotNone(self.rows()[0].sent_at)

    def test_a_failed_reply_keeps_the_reading_for_the_retry(self):
        session_id = self.start_lesson()
        photo = self.attachment()
        with FakeAI(tutor_chat=ai_service.AIProviderError("provider_timeout", "t")):
            failed = self.client.post("/api/chat", json={"session_id": session_id, "message": "Stimmt das?",
                                                         "attachments": [photo]})
        self.assertGreaterEqual(failed.status_code, 400)
        row = self.rows()[0]
        self.assertEqual((row.read_status, row.sent_at), ("ready", None))
        with FakeAI() as ai:
            retried = self.client.post("/api/chat", json={"session_id": session_id, "message": "Stimmt das?",
                                                          "attachments": [photo]})
        self.assertEqual(retried.status_code, 200)
        self.assertEqual(ai.tasks(), ["tutor_chat"], "no second reading of the same photo")


class AnswerPhotoTests(RouteTestCase):
    def test_a_photographed_solution_is_read_into_the_box_not_graded(self):
        session_id = self.start_lesson()
        application.SESSIONS[session_id]["current_question"]["expected_answer"] = "GEHEIM-42"
        photo = self.attachment(purpose="answer")
        with FakeAI() as ai:
            response = self.client.post("/api/answer/photo", json={"session_id": session_id, "attachments": [photo]})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        data = response.get_json()
        self.assertEqual(data["transcription"], "3x + 5 = [?20]\n3x = 15", "the student's working, not the task")
        self.assertEqual(data["unclear"][0]["alternatives"], ["26"])
        self.assertEqual(data["clarification_question"], "Steht da 20 oder 26?")
        self.assertEqual(ai.tasks(), ["image_understanding"], "nothing is graded from a photo")
        self.assertNotIn("GEHEIM-42", json.dumps(ai.calls[0]["input"]), "the expected answer never reaches the reader")
        self.assertIn("STUDENT'S WORKING", json.dumps(ai.calls[0]["input"]))

    def test_only_an_answer_photo_can_be_used_as_an_answer(self):
        session_id = self.start_lesson()
        chat_photo = self.attachment()
        response = self.client.post("/api/answer/photo", json={"session_id": session_id, "attachments": [chat_photo]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.post("/api/answer/photo", json={"session_id": session_id}).status_code, 400)

    def test_the_grader_is_told_the_answer_came_from_a_photo(self):
        session_id = self.start_lesson()
        with FakeAI(answer_evaluation=ai_service.AIProviderError("provider_timeout", "t"),
                    answer_diagnosis=ai_service.AIProviderError("provider_timeout", "t")) as ai:
            self.client.post("/api/answer", json={"session_id": session_id, "answer": "3x = 15\nx = 5",
                                                  "answer_source": "photo"})
        graded = json.dumps([call.get("input") for call in ai.calls], ensure_ascii=False)
        self.assertIn("read from their photo", graded)


class LessonFromPhotoTests(RouteTestCase):
    def analyze(self, *names, ai=None, goal="Gleichungen"):
        files = [(io.BytesIO(CASES[name].data), f"{name}.jpg") for name in names]
        with (ai or FakeAI()) as fake:
            response = self.client.post("/api/analyze", data={"study_goal": goal, "subject": "Mathematics",
                                                              "images": files}, content_type="multipart/form-data")
        return response, fake

    def test_photos_are_read_first_then_the_tutor_model_writes_the_lesson(self):
        response, ai = self.analyze("math_handwritten", "printed_de_clear")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(ai.tasks(), ["image_understanding", "lesson_generation"])
        self.assertEqual(len(image_parts(ai.calls[0]["input"])), 2)
        lesson_call = ai.calls[1]
        self.assertEqual(lesson_call["model"], application.TUTOR_MODEL)
        self.assertEqual(image_parts(lesson_call["input"]), [], "the lesson model is no longer the image reader")
        self.assertIn("3x + 5 = [?20]", json.dumps(lesson_call["input"], ensure_ascii=False))
        data = response.get_json()
        self.assertEqual(data["material_notes"]["unclear"], ["20"])
        self.assertIn("3x + 5", application.SESSIONS[data["session_id"]]["source_context"])

    def test_unreadable_photos_are_said_so_and_no_lesson_is_made(self):
        response, ai = self.analyze("blurred", ai=FakeAI(image_understanding={"blocks": []}))
        self.assertEqual((response.status_code, response.get_json()["code"]), (422, "images_unreadable"))
        self.assertEqual(ai.tasks(), ["image_understanding"])

    def test_a_broken_file_is_refused_before_any_ai_call(self):
        response, ai = self.analyze("corrupt_jpeg")
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "image_corrupt"))
        self.assertEqual(ai.calls, [])

    def test_a_lesson_without_photos_is_unchanged(self):
        response, ai = self.analyze()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ai.tasks(), ["lesson_generation"])
        self.assertIsNone(response.get_json()["material_notes"])


class AllowanceTests(RouteTestCase):
    def test_reading_a_chat_photo_counts_against_the_free_chat_allowance(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True,
                                      SUBSCRIPTION_SETTINGS=SubscriptionSettings(free_chat_credits_per_period=1))
        photo, thread = self.attachment(), self.conversation()
        with patch.object(ai_service, "create_response", side_effect=AssertionError("must not be called")) as gateway:
            response = self.client.post(f"/api/assistant/conversations/{thread}/messages",
                                        json={"message": "Was steht da?", "attachments": [photo]})
        self.assertEqual((response.status_code, response.get_json()["code"]), (429, "free_chat_used"))
        gateway.assert_not_called()


class ProjectAndImportImageTests(unittest.TestCase):
    def test_a_project_page_is_sent_to_the_provider_within_its_limit(self):
        page_image = preprocess_document_image(CASES["phone_12mp"].data)
        captured = []

        def fake(**kwargs):
            captured.append(kwargs)
            return Response({"blocks": [], "detected_page_number": "", "warning": ""})

        with application.app.test_request_context(), patch.object(application, "create_response", side_effect=fake):
            application._recognize_variant(SimpleNamespace(subject="Mathematics"), SimpleNamespace(page_order=1),
                                           page_image.data, page_image.mime_type)
        self.assertGreater(len(page_image.data) * 4 / 3, GROQ_BASE64_LIMIT)
        self.assertLessEqual(len(image_parts(captured[0]["input"])[0]["image_url"]), GROQ_BASE64_LIMIT)

    def test_the_page_prompt_shares_the_confusable_characters_rule(self):
        from learnova.ocr.service import recognition_instructions
        text = recognition_instructions("Mathematics", 1)
        self.assertIn("1/7/9", text)
        self.assertIn("decimal comma stays a comma", text)


class PageMarkupTests(RouteTestCase):
    def test_both_chats_offer_the_native_camera_and_the_photo_picker(self):
        for path in ("/assistant", "/"):
            with self.subTest(path):
                page = self.client.get(path).get_data(as_text=True)
                self.assertIn('capture="environment"', page)
                self.assertIn('accept="image/*" multiple', page)
        self.assertIn('data-photo-answers="1"', self.client.get("/").get_data(as_text=True))

    def test_switched_off_the_buttons_are_gone(self):
        application.app.config["FEATURE_CHAT_IMAGES"] = False
        self.assertNotIn("asstCamera", self.client.get("/assistant").get_data(as_text=True))
        self.assertNotIn("chatCamera", self.client.get("/").get_data(as_text=True))


class EvaluationSuiteTests(unittest.TestCase):
    """The offline evaluation as a regression gate (scripts/vision_eval.py prints it)."""

    @classmethod
    def setUpClass(cls):
        cls.report = vision_eval.run_offline(include_large=True, with_ocr=False)

    def test_intake_judges_every_case_correctly(self):
        self.assertEqual([row["case"] for row in self.report.intake if not row["correct"]], [])

    def test_every_case_now_fits_the_provider_limit_and_some_did_not_before(self):
        self.assertTrue(all(row["new_within_limit"] for row in self.report.payload))
        self.assertTrue(any(not row["old_within_limit"] for row in self.report.payload))

    def test_quality_hints_raise_no_false_alarm_on_any_seed(self):
        for seed, result in self.report.quality.items():
            with self.subTest(seed):
                self.assertEqual(result["false_positive"], 0)
                self.assertGreaterEqual(result["recall"], 0.75)

    @unittest.skipUnless(vision_eval.local_reader() is not None, "RapidOCR is not installed")
    def test_the_provider_copy_is_as_legible_as_the_full_image(self):
        read = vision_eval.local_reader()
        assert read is not None
        cases = [CASES[name] for name in ("printed_de_clear", "phone_12mp", "handwriting_de_ruled")]
        for row in vision_eval.evaluate_legibility(cases, intake.IntakeLimits(), read):
            with self.subTest(row["case"]):
                self.assertLessEqual(row["cer_provider"], row["cer_full"] + 0.02)
        screenshot = vision_eval.evaluate_old_preprocessing([CASES["transparent_screenshot"]], read)[0]
        self.assertLess(screenshot["cer_new_intake"], screenshot["cer_old_page_pipeline"])


if __name__ == "__main__":
    unittest.main()

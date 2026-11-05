"""Chat photos end to end through the routes (spec section 12, image tests).

Model calls are patched at `app.create_response` (as tests/test_vision.py does), so these
exercise the genuine upload route, intake, ownership, the send route, the stored readings
and the student-facing answers offline. The provider limits themselves are tested through
the real gateway in tests/test_vision_limits.py.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_chat_images_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.evaluation import vision_fixtures  # noqa: E402
from tests.test_diagnostics_integration import LESSON  # noqa: E402

db = application.db
CASES = {case.name: case for case in vision_fixtures.build_cases(include_large=False)}

READING = {
    "images": [{"index": 1, "kind": "worksheet", "rotation": 0, "cut_off": False, "legibility": "clear"}],
    "blocks": [{"image": 1, "type": "printed_text", "text": "Löse 3x + 5 = 20.", "author": "printed", "confidence": 0.97},
               {"image": 1, "type": "formula", "text": "3x = 15", "latex": "3x = 15", "author": "student",
                "confidence": 0.93}],
    "diagrams": [], "uncertain": [], "clarification_question": "",
    "summary": "Eine Gleichung mit einem ersten Schritt.", "language": "de",
}
EMPTY_READING = {"images": [{"index": 1, "legibility": "unreadable"}], "blocks": [], "diagrams": [],
                 "uncertain": [], "summary": "", "language": "de"}


class Usage:
    input_tokens = 100
    output_tokens = 40
    total_tokens = 140


class Response:
    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self.model = "test-model"
        self.usage = Usage()


class FakeAI:
    """Answers app.create_response by task type and records every call."""

    def __init__(self, **responses):
        self.responses = {"image_understanding": READING, "assistant_chat": "Gut. Der nächste Schritt: teile durch 3.",
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
    if isinstance(value, dict):
        return ([value] if value.get("type") == "input_image" else []) + [
            part for item in value.values() for part in image_parts(item)]
    if isinstance(value, list):
        return [part for item in value for part in image_parts(item)]
    return []


def worksheet(image_format, size=(1200, 900)):
    """A page with a few lines of 'writing', saved in the given format."""

    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for row in range(6):
        draw.rectangle((80, 120 + row * 110, 80 + 900 - row * 90, 150 + row * 110), fill="black")
    output = io.BytesIO()
    image.save(output, format=image_format, **({"quality": 90} if image_format in {"JPEG", "WEBP"} else {}))
    return output.getvalue()


class RouteCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, FEATURE_ASSISTANT_CHAT=True, FEATURE_CHAT_IMAGES=True, AI_MODE="cached",
            AI_VISION_REQUEST_TOKEN_CEILING=8000, AI_REQUEST_TOKEN_CEILINGS="", CHAT_IMAGES_PER_MESSAGE=3,
            VISION_MAX_IMAGES_PER_REQUEST=3, AI_IMAGE_TOKEN_ESTIMATE=2048)
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def register(self, name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery"})
        return client

    def upload(self, data=None, *, name="math_handwritten", filename="photo.jpg", client=None, purpose="chat"):
        payload = data if data is not None else CASES[name].data
        return (client or self.client).post("/api/chat-attachments", data={
            "file": (io.BytesIO(payload), filename), "purpose": purpose}, content_type="multipart/form-data")

    def attachment(self, **options):
        response = self.upload(**options)
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        return response.get_json()["attachment"]["id"]

    def conversation(self, client=None):
        return (client or self.client).post("/api/assistant/conversations", json={}).get_json()["conversation"]["id"]

    def send(self, thread, client=None, **payload):
        return (client or self.client).post(f"/api/assistant/conversations/{thread}/messages", json=payload)

    def start_lesson(self):
        with FakeAI():
            response = self.client.post("/api/analyze", data={"study_goal": "Gleichungen", "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["session_id"]


class UploadFormatTests(RouteCase):
    def test_jpeg_png_and_webp_are_accepted_and_cost_no_ai_call(self):
        for image_format, extension in (("JPEG", "jpg"), ("PNG", "png"), ("WEBP", "webp")):
            with self.subTest(image_format), FakeAI() as ai:
                response = self.upload(worksheet(image_format), filename=f"page.{extension}")
                self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
                attachment = response.get_json()["attachment"]
                self.assertTrue(attachment["available"])
                self.assertEqual((attachment["width"], attachment["height"]), (1200, 900))
                image = self.client.get(attachment["url"])
                self.assertEqual(image.status_code, 200)
                self.assertIn(image.mimetype, {"image/jpeg", "image/png"}, "re-encoded, never served as uploaded")
                self.assertIn("no-store", image.headers.get("Cache-Control", ""))
                self.assertEqual(ai.calls, [])

    def test_gif_text_and_heic_are_refused_by_their_bytes(self):
        for name, code in (("gif_unsupported", "unsupported_type"), ("text_renamed", "unsupported_type"),
                           ("heic_header", "heic_unsupported"), ("corrupt_jpeg", "image_corrupt"),
                           ("empty_file", "empty_file")):
            with self.subTest(name):
                response = self.upload(name=name, filename="photo.jpg")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["code"], code)
                self.assertTrue(response.get_json()["error"])
        self.assertEqual(self.client.get("/api/chat-attachments?waiting=1").get_json()["attachments"], [])

    def test_oversized_files_and_requests_are_413_json(self):
        application.app.config["VISION_MAX_UPLOAD_BYTES"] = 50_000
        response = self.upload(name="math_handwritten")
        self.assertEqual((response.status_code, response.get_json()["code"]), (413, "file_too_large"))
        application.app.config.update(VISION_MAX_UPLOAD_BYTES=15 * 1024 * 1024, MAX_CONTENT_LENGTH=20_000)
        response = self.upload(name="math_handwritten")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json()["code"], "upload_too_large")

    def test_the_same_photo_twice_is_one_attachment_flagged_as_duplicate(self):
        first = self.upload(name="table_3x3")
        second = self.upload(name="table_3x3")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["attachment"]["id"], first.get_json()["attachment"]["id"])
        self.assertTrue(second.get_json()["duplicate"])


class SendTests(RouteCase):
    def test_two_photos_in_one_message_are_read_together(self):
        photos = [self.attachment(name="printed_de_clear"), self.attachment(name="table_3x3")]
        with FakeAI() as ai:
            response = self.send(self.conversation(), message="Hilf mir bei beiden.", attachments=photos)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(ai.tasks(), ["image_understanding", "assistant_chat"])
        self.assertEqual(len(image_parts(ai.calls[0]["input"])), 2)
        # The compact chat reading (CHAT_FIX2 D1a): its own label, apart from lessons' v2.
        self.assertEqual(ai.calls[0]["prompt_version"], "image_understanding:chat1+chat")
        self.assertIn("timeout_seconds", ai.calls[0], "the reading runs inside the photo time budget")
        self.assertLessEqual(ai.calls[0]["timeout_seconds"], 60.0)
        self.assertEqual([item["id"] for item in response.get_json()["message"]["attachments"]], photos)

    def test_more_photos_than_the_limit_are_refused_before_any_call(self):
        photos = [self.attachment(name=name) for name in ("printed_de_clear", "table_3x3", "right_triangle")]
        with FakeAI() as ai:
            response = self.send(self.conversation(), message="Drei", attachments=photos)
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "attachments_invalid"))
        self.assertIn("at most 2", response.get_json()["error"])
        self.assertEqual(ai.calls, [])

    def test_an_unreadable_photo_gets_a_plain_answer_and_no_model_reply(self):
        thread = self.conversation()
        with FakeAI(image_understanding=EMPTY_READING) as ai:
            response = self.send(thread, message="", attachments=[self.attachment(name="blurred")])
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(ai.tasks(), ["image_understanding"], "nothing is guessed about an unreadable photo")
        reply = response.get_json()["reply"]
        self.assertTrue(reply["content"].startswith("I could not read anything in your photo."))
        self.assertIn("The photo looks blurred.", reply["content"], "the photo's own quality hint")
        self.assertTrue(reply["error"])
        with application.app.app_context():
            conversation = db.session.get(application.Conversation, thread)
            self.assertEqual(conversation.message_count, 1, "a failed turn counts like any failed turn")
            self.assertIsNone(conversation.reply_started_at)
            stored = db.session.scalars(db.select(application.ConversationMessage).where(
                application.ConversationMessage.role == "assistant")).one()
            self.assertEqual(stored.error_category, "image_unreadable")
        # It is never replayed to the model as if the assistant had said it.
        with FakeAI() as ai:
            self.send(thread, message="Was soll ich jetzt tun?")
        self.assertNotIn("I could not read anything", ai.calls[0]["input"])
        self.assertIn("nothing could be read from this image", ai.calls[0]["input"])

    def test_regenerate_on_an_unreadable_photo_never_asks_the_model(self):
        thread = self.conversation()
        with FakeAI(image_understanding=EMPTY_READING):
            first = self.send(thread, message="", attachments=[self.attachment(name="blurred")])
        reply = first.get_json()["reply"]
        self.assertEqual(reply["error_code"], "image_unreadable")
        with FakeAI() as ai:
            again = self.client.post(f"/api/assistant/conversations/{thread}/regenerate",
                                     json={"message_id": reply["id"]})
        self.assertEqual((again.status_code, again.get_json()["code"]), (409, "image_unreadable"),
                         again.get_data(as_text=True))
        self.assertEqual(ai.calls, [], "a model asked about a photo nothing could be read from can only guess")
        self.assertTrue(again.get_json()["error"].startswith("I could not read anything in your photo."))
        with application.app.app_context():
            conversation = db.session.get(application.Conversation, thread)
            self.assertIsNone(conversation.reply_started_at, "the claim is given back")
            rows = db.session.scalars(db.select(application.ConversationMessage).where(
                application.ConversationMessage.conversation_id == thread).order_by(
                application.ConversationMessage.id)).all()
            self.assertEqual([(row.role, row.error_category) for row in rows],
                             [("user", ""), ("assistant", "image_unreadable")])
            self.assertEqual(rows[-1].id, reply["id"], "nothing was replaced")

    def test_a_lost_reply_to_an_unreadable_photo_is_answered_the_same_way(self):
        thread = self.conversation()
        with FakeAI(image_understanding=EMPTY_READING):
            first = self.send(thread, message="", attachments=[self.attachment(name="blurred")],
                              client_message_id="photo-1")
        reply_id = first.get_json()["reply"]["id"]
        with application.app.app_context():   # the request died before its reply was stored
            db.session.execute(db.delete(application.ConversationMessage).where(
                application.ConversationMessage.id == reply_id))
            db.session.commit()
        with FakeAI() as ai:
            replayed = self.send(thread, message="", client_message_id="photo-1")
        self.assertEqual(replayed.status_code, 200, replayed.get_data(as_text=True))
        self.assertEqual(ai.calls, [])
        self.assertTrue(replayed.get_json()["reply"]["content"].startswith("I could not read anything"))

    def test_two_unreadable_photos_are_answered_in_the_plural(self):
        photos = [self.attachment(name="blurred"), self.attachment(name="low_light")]
        with FakeAI(image_understanding=EMPTY_READING) as ai:
            response = self.send(self.conversation(), message="", attachments=photos)
        self.assertEqual(ai.tasks(), ["image_understanding"])
        self.assertTrue(response.get_json()["reply"]["content"].startswith("I could not read anything in your photos."))

    def test_an_unreadable_photo_with_a_question_gets_a_real_reply(self):
        with FakeAI(image_understanding=EMPTY_READING) as ai:
            response = self.send(self.conversation(), message="Ist das richtig?",
                                 attachments=[self.attachment(name="blurred")])
        self.assertEqual(ai.tasks(), ["image_understanding", "assistant_chat"])
        self.assertIn("nothing could be read from this image", ai.calls[1]["input"])
        self.assertIn("When an image could not be read, say so plainly", ai.calls[1]["instructions"])
        self.assertFalse(response.get_json()["reply"].get("error", False))

    def test_a_follow_up_question_reuses_the_reading(self):
        thread = self.conversation()
        with FakeAI() as ai:
            self.send(thread, message="Was steht da?", attachments=[self.attachment()])
            follow_up = self.send(thread, message="Und wie geht es weiter?")
        self.assertEqual(follow_up.status_code, 201)
        self.assertEqual(ai.tasks(), ["image_understanding", "assistant_chat", "assistant_chat"])
        self.assertIn("3x = 15", ai.calls[-1]["input"])
        self.assertEqual(image_parts(ai.calls[-1]["input"]), [])

    def test_the_photo_is_still_there_after_a_reload(self):
        thread, photo = self.conversation(), self.attachment()
        with FakeAI():
            self.send(thread, message="Was steht da?", attachments=[photo])
        reloaded = self.register("alice-again")  # a second session must not see it
        self.assertEqual(reloaded.get(f"/api/assistant/conversations/{thread}").status_code, 404)
        data = self.client.get(f"/api/assistant/conversations/{thread}").get_json()["conversation"]
        attachment = data["messages"][0]["attachments"][0]
        self.assertEqual(attachment["id"], photo)
        image = self.client.get(attachment["url"])
        self.assertEqual(image.status_code, 200)
        self.assertIn("no-store", image.headers["Cache-Control"])
        self.assertEqual(self.client.get("/api/chat-attachments?waiting=1").get_json()["attachments"], [],
                         "a sent photo no longer waits in the tray")

    def test_a_photo_added_but_not_sent_comes_back_after_a_refresh(self):
        photo = self.attachment()
        waiting = self.client.get("/api/chat-attachments?waiting=1").get_json()["attachments"]
        self.assertEqual([item["id"] for item in waiting], [photo])
        self.assertTrue(waiting[0]["url"])

    def test_nobody_else_can_see_delete_or_send_your_photo(self):
        photo = self.attachment()
        bob = self.register("bob")
        self.assertEqual(bob.get(f"/api/chat-attachments/{photo}/image").status_code, 404)
        self.assertEqual(bob.delete(f"/api/chat-attachments/{photo}").status_code, 404)
        with FakeAI() as ai:
            response = self.send(self.conversation(bob), client=bob, message="Meins?", attachments=[photo])
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "attachments_invalid"))
        self.assertEqual(ai.calls, [])
        anonymous = application.app.test_client()
        upload = anonymous.post("/api/chat-attachments", data={
            "file": (io.BytesIO(CASES["table_3x3"].data), "photo.jpg"), "purpose": "chat"},
            content_type="multipart/form-data")
        self.assertEqual((upload.status_code, upload.get_json()["code"]), (401, "login_required"))
        self.assertIn(anonymous.get(f"/api/chat-attachments/{photo}/image").status_code, (302, 401, 404))


class OtherPhotoRouteTests(RouteCase):
    def test_the_lesson_builder_refuses_more_photos_than_fit(self):
        files = [(io.BytesIO(CASES[name].data), f"{name}.jpg") for name in ("printed_de_clear", "table_3x3",
                                                                            "right_triangle")]
        with FakeAI() as ai:
            response = self.client.post("/api/analyze", data={"study_goal": "Gleichungen", "subject": "Mathematics",
                                                              "images": files}, content_type="multipart/form-data")
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "too_many_images"))
        self.assertEqual(ai.calls, [])

    def test_the_lesson_chat_and_the_answer_photo_say_what_went_wrong(self):
        session_id = self.start_lesson()
        photo = self.attachment()
        with FakeAI(image_understanding=ai_service.AIProviderError("provider_daily_limit", "daily")):
            response = self.client.post("/api/chat", json={"session_id": session_id, "message": "Stimmt das?",
                                                           "attachments": [photo]})
        self.assertEqual((response.status_code, response.get_json()["code"]), (503, "photo_reader_daily_limit"))
        answer_photo = self.attachment(name="table_3x3", purpose="answer")
        with FakeAI(image_understanding=ai_service.AIProviderError("image_rejected", "rejected")):
            response = self.client.post("/api/answer/photo", json={"session_id": session_id,
                                                                   "attachments": [answer_photo]})
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "image_rejected"))
        with FakeAI(image_understanding=ai_service.AIProviderError("provider_rate_limit", "busy")):
            response = self.client.post("/api/chat", json={"session_id": session_id, "message": "Stimmt das?",
                                                           "attachments": [photo]})
        self.assertEqual((response.status_code, response.get_json()["code"]), (503, "photo_reader_busy"))
        self.assertEqual(response.headers["Retry-After"], "60")

    def test_allowance_errors_keep_their_own_message(self):
        error = ai_service.AIRequestLimitError("limit", scope="free_chat")
        with FakeAI(image_understanding=error):
            response = self.send(self.conversation(), message="Was steht da?", attachments=[self.attachment()])
        self.assertEqual((response.status_code, response.get_json()["code"]), (429, "free_chat_used"))


class PhotoLimitMarkupTests(RouteCase):
    def test_trays_and_the_lesson_builder_show_the_effective_limit(self):
        page = self.client.get("/assistant").get_data(as_text=True)
        self.assertIn('id="asstAttachTray" class="attach-tray hidden" data-max-photos="2"', page)
        lesson_page = self.client.get("/lesson").get_data(as_text=True)   # the lesson builder (index.html)
        self.assertIn('id="images"', lesson_page)
        if 'id="images"' in lesson_page:
            self.assertIn('data-max-photos="2"', lesson_page)
            self.assertIn("<small>Optional · up to 2 images</small>", lesson_page)
            # Without data-i18n: the browser re-translation would bring the old "4" back.
            self.assertNotIn('data-i18n="optionalMaterial"', lesson_page)
        application.app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = 16000
        self.assertIn('data-max-photos="3"', self.client.get("/assistant").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

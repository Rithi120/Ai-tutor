"""Learning resources in the chat and in Short Lessons (spec Phase 7, Test 9; docs/LEARNING_RESOURCES.md).

Everything external is stubbed at its single seam: the AI (`app.create_response`), Wikipedia
(`media._fetch_page`, `media._fetch_file_info`), the YouTube Data API
(`youtube_videos._http_json`) and the thumbnail host (`youtube_videos._http_bytes`). The
conftest network guard turns any unstubbed call into a failure.

What is pinned here:
- a physics "Ohm's law" reply with a MEDIA line gets a licence-checked diagram and, on tap,
  video cards built only from the stubbed API answers (unavailable videos dropped);
- a vocabulary-definition reply gets nothing (the veto), whatever the model proposed;
- the API key never appears in a response, a page or a log line;
- no key: no video cards, and lessons keep honest search links;
- German lessons search in German (subject, class, relevanceLanguage=de);
- the daily cap, the quota marker, the cache and its re-check are honoured;
- the thumbnail proxy rejects bad ids and ids it did not find itself; other students' messages
  and lessons are refused.
"""

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_learning_resources_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from sqlalchemy import inspect, text  # noqa: E402

import app as application  # noqa: E402
from learnova import media_enrichment as media  # noqa: E402
from learnova import youtube_videos  # noqa: E402
from learnova.assistant import conversation as conversation_module  # noqa: E402
from learnova.assistant import presets  # noqa: E402
from learnova.assistant.formatting import reply_trailers, split_reply_trailers  # noqa: E402
from learnova.assistant.resources import media_offer, strip_video_links  # noqa: E402
from tests.test_media_enrichment import UPLOAD, file_info, file_payload, page  # noqa: E402
from tests.test_youtube_videos import KEY, FakeHttp, video  # noqa: E402

db = application.db
PASSWORD = "correct-horse-battery"

OHM_REPLY = (
    "Das **ohmsche Gesetz** beschreibt, wie Spannung, Stromstärke und Widerstand zusammenhängen: "
    "\\(U = R \\cdot I\\). Stell dir einen Wasserschlauch vor: Die Spannung ist der Druck, die Stromstärke "
    "die Wassermenge pro Sekunde, und der Widerstand ist eine enge Stelle im Schlauch. Verdoppelst du die "
    "Spannung bei gleichem Widerstand, verdoppelt sich auch die Stromstärke. Im Versuch misst man dazu "
    "mit einem Voltmeter und einem Amperemeter an einem Widerstand und trägt die Werte in ein Diagramm ein.\n"
    "MEDIA: diagram | Ohmsches Gesetz\n"
    "TOPIC: Physics | Ohmsches Gesetz")
VOCAB_REPLY = ("**bewildered** heißt *verwirrt* oder *ratlos*. Beispiel: She looked bewildered.\n"
               "MEDIA: diagram | Bewildered\nTOPIC: English | Vokabel bewildered")


class FakeAI:
    """Patches app.create_response; answers assistant_chat and lesson_generation."""

    def __init__(self, chat: str = "", lesson: dict[str, Any] | None = None):
        self.chat, self.lesson, self.calls = chat, lesson, []
        self._patch = patch.object(application, "create_response", side_effect=self)

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        task = kwargs["task_type"]
        if task == "assistant_chat":
            text_out = self.chat
        elif task == "lesson_generation" and self.lesson is not None:
            text_out = json.dumps(self.lesson)
        else:
            raise AssertionError(f"unexpected AI task {task}")

        class Usage:
            input_tokens, output_tokens, total_tokens = 100, 50, 150

        class Response:
            output_text, model, usage = text_out, "test-model", Usage()

        return Response()

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


def wikipedia(title="Ohmsches Gesetz", info=None):
    """Stub both Wikipedia lookups: the article and its file's licence."""
    found = page(title, thumb=UPLOAD + "Ohms_law_circuit.svg.png", url=f"https://de.wikipedia.org/wiki/{title}")
    return patch.multiple(
        media,
        _fetch_page=MagicMock(return_value=json.dumps({"query": {"pages": [found]}})),
        _fetch_file_info=MagicMock(return_value=file_payload(info or file_info())))


def youtube(http):
    return patch.object(youtube_videos, "_http_json", http)


def ohm_videos():
    """Three search hits: one good, one deleted (absent from videos.list), one age-restricted."""
    return FakeHttp(["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"], [
        video("aaaaaaaaaaa", title="Ohmsches Gesetz – Versuch mit Widerständen", channel="Physik mit Herz"),
        video("ccccccccccc", rating="ytAgeRestricted")])


class ResourcesCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, FEATURE_ASSISTANT_CHAT=True, FEATURE_LEARNING_RESOURCES=True, FEATURE_LESSON_MEDIA=True,
            ASSISTANT_MODEL="openai/gpt-oss-20b", ASSISTANT_DEEP_MODEL="openai/gpt-oss-120b",
            YOUTUBE_API_KEY=KEY, YOUTUBE_REGION_CODE="DE", YOUTUBE_DAILY_SEARCH_LIMIT=80, YOUTUBE_CACHE_DAYS=7,
            YOUTUBE_TIMEOUT_SECONDS=4, YOUTUBE_TRUSTED_CHANNELS="", AI_MODE="live",
            AI_CACHE_DIR=tempfile.mkdtemp(prefix="learnova-resources-cache-"))
        application.SESSIONS.clear()
        application._YOUTUBE_THUMBNAILS.clear()
        media._fetch_page.cache_clear()
        media._fetch_file_info.cache_clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)
        application._YOUTUBE_THUMBNAILS.clear()

    def register(self, name, grade="8", language="de"):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com", "password": PASSWORD,
                                       "grade": grade, "language": language})
        return client

    # ---- chat
    def chat_reply(self, reply_text, client=None):
        client = client or self.client
        created = client.post("/api/assistant/conversations", json={})
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        conversation_id = created.get_json()["conversation"]["id"]
        with FakeAI(chat=reply_text):
            sent = client.post(f"/api/assistant/conversations/{conversation_id}/messages",
                               json={"message": "Erklär mir das ohmsche Gesetz"})
        self.assertEqual(sent.status_code, 201, sent.get_data(as_text=True))
        return sent.get_json()["reply"], sent

    def resources(self, message_id, part=None, client=None):
        suffix = f"?part={part}" if part else ""
        return (client or self.client).get(f"/api/assistant/messages/{message_id}/resources{suffix}")

    def cache_rows(self):
        with application.app.app_context():
            return db.session.scalars(db.select(application.LearningResourceCache)).all()


# ---------------------------------------------------------------------- pure: trailer + veto
class TrailerAndVetoTests(unittest.TestCase):
    def test_the_media_line_is_parsed_in_any_order_and_never_shown(self):
        for tail in ("MEDIA: video | Ohmsches Gesetz Versuch\nCHIPS: Ja | Nein\nTOPIC: Physics | Ohm",
                     "TOPIC: Physics | Ohm\nMEDIA: video | Ohmsches Gesetz Versuch",
                     "**MEDIA:** video | Ohmsches Gesetz Versuch\nTOPIC: Physics | Ohm"):
            with self.subTest(tail=tail):
                parts = reply_trailers(f"Erklärung.\n{tail}")
                self.assertEqual(parts.text, "Erklärung.")
                self.assertEqual(parts.media, ("video", "Ohmsches Gesetz Versuch"))
                self.assertEqual(parts.topic, ("Physics", "Ohm"))
        # The 3-tuple wrapper the rest of the app and the older tests use is unchanged.
        self.assertEqual(split_reply_trailers("Hi.\nMEDIA: map | Rhein\nTOPIC: none"), ("Hi.", [], ("", "none")))

    def test_a_media_line_in_the_middle_or_of_another_shape_is_not_an_offer(self):
        text = "Satz.\nMEDIA: video | Ohm\nNoch ein Satz."
        self.assertEqual(reply_trailers(text).text, text)
        self.assertIsNone(reply_trailers(text).media)
        for line in ("MEDIA: photo | Ohm", "MEDIA: none", "MEDIA: video |  "):
            self.assertIsNone(reply_trailers(f"A\n{line}").media, line)

    def offer(self, media=("diagram", "Ohmsches Gesetz"), subject="Physics", topic="Ohmsches Gesetz",
              reply=OHM_REPLY):
        return media_offer(media, subject=subject, topic=topic, reply_text=reply)

    def test_a_real_explanation_in_a_visual_subject_is_offered(self):
        self.assertEqual(self.offer(), {"kind": "diagram", "term": "Ohmsches Gesetz", "subject": "Physics"})
        self.assertEqual(self.offer(subject="Physik")["subject"], "Physics")

    def test_the_veto(self):
        long_english = "x" * 1000
        self.assertIsNone(self.offer(media=None))
        self.assertIsNone(self.offer(topic="none"))
        self.assertIsNone(self.offer(subject=""))
        self.assertIsNone(self.offer(subject="Other"))
        self.assertIsNone(self.offer(reply="Ein kurzer Satz."))                       # a simple answer
        self.assertIsNone(self.offer(media=("diagram", "https://evil.example/x")))      # a link, not a title
        # A vocabulary definition never gets media, whatever the model proposed.
        self.assertIsNone(self.offer(media=("diagram", "Bewildered"), subject="English", reply=VOCAB_REPLY))
        self.assertIsNone(self.offer(media=("video", "Bewildered"), subject="English", reply=VOCAB_REPLY))
        # A language subject: never a picture; a video only under a long explanation.
        self.assertIsNone(self.offer(media=("diagram", "Present perfect"), subject="English", reply=long_english))
        self.assertEqual(self.offer(media=("video", "Present perfect"), subject="English",
                                    reply=long_english)["kind"], "video")
        # A long answer about one word's meaning is still a vocabulary definition.
        for topic in ("Vokabel bewildered", "Bedeutung von bewildered", "meaning of bewildered",
                      "Übersetzung: bewildered"):
            self.assertIsNone(self.offer(media=("video", "Bewildered"), subject="English", topic=topic,
                                         reply=long_english), topic)

    def test_a_copied_template_keeps_one_kind_and_one_title(self):
        # The model copied "diagram|map|video" instead of picking: the first kind counts and
        # no "|" reaches the Wikipedia lookup (several titles) or the YouTube query (an OR).
        parts = reply_trailers("Erklärung.\nMEDIA: diagram|map|video | Ohmsches Gesetz\nTOPIC: Physics | Ohm")
        self.assertEqual(parts.media, ("diagram", "Ohmsches Gesetz"))
        self.assertIsNone(reply_trailers("A\nMEDIA: diagram|map|video").media)
        self.assertEqual(youtube_videos.clean_query("Ohm | Physik"), "Ohm Physik")

    def test_model_written_video_links_are_taken_out(self):
        text = ("Schau dir [dieses Video](https://www.youtube.com/watch?v=abcdefghijk) an.\n"
                "Oder https://youtu.be/abcdefghijk und (https://m.youtube.com/watch?v=x).\n"
                "Mehr: [Wikipedia](https://de.wikipedia.org/wiki/Ohmsches_Gesetz)\n"
                "```\nurl = 'https://youtu.be/keep'\n```")
        cleaned = strip_video_links(text)
        self.assertNotIn("youtube.com", cleaned.split("```")[0])
        self.assertNotIn("youtu.be/abc", cleaned)
        self.assertIn("Schau dir dieses Video an.", cleaned)
        self.assertIn("[Wikipedia](https://de.wikipedia.org/wiki/Ohmsches_Gesetz)", cleaned)
        self.assertIn("url = 'https://youtu.be/keep'", cleaned)      # code is left as written
        self.assertEqual(strip_video_links("Kein Link."), "Kein Link.")

    def test_the_prompt_asks_for_the_line_cheaply_and_forbids_links(self):
        line = next(item for item in presets.TUTORING_LAYER.split("\n") if "MEDIA:" in item)
        self.assertLessEqual(conversation_module.estimate_tokens("\n" + line), 22)
        self.assertLessEqual(conversation_module.estimate_tokens(presets.TUTORING_LAYER), 420)
        self.assertIn("Never write links, URLs, video titles or channel names", presets.SHARED_RULES)


# ---------------------------------------------------------------------- chat (Test 9)
class ChatResourcesTests(ResourcesCase):
    def test_a_student_asking_for_a_picture_gets_an_offer_without_a_media_line(self):
        # Live 2026-11-14: three explicit picture requests, no MEDIA line from the model.
        created = self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]["id"]
        explanation = "Das ohmsche Gesetz verbindet Spannung, Stromstärke und Widerstand. " * 6
        with FakeAI(chat=f"{explanation}\nTOPIC: Physics | Ohmsches Gesetz (Spannung, Stromstärke)"):
            sent = self.client.post(f"/api/assistant/conversations/{created}/messages",
                                    json={"message": "Kannst du mir das ohmsche Gesetz mit einem Bild zeigen?"})
        reply = sent.get_json()["reply"]
        self.assertEqual(reply["resources"]["image_pending"], True)
        with application.app.app_context():
            stored = json.loads(db.session.get(application.ConversationMessage, reply["id"]).resources_json)
        self.assertEqual((stored["offer"]["kind"], stored["offer"]["term"]), ("diagram", "Ohmsches Gesetz"))
        with FakeAI(chat=f"{explanation}\nTOPIC: Physics | Ohmsches Gesetz"):
            plain = self.client.post(f"/api/assistant/conversations/{created}/messages",
                                     json={"message": "Und was ist der Widerstand?"}).get_json()["reply"]
        self.assertNotIn("resources", plain, "no picture asked for, no MEDIA line: no offer")

    def test_ohms_law_gets_a_credited_diagram_and_on_tap_checked_videos(self):
        reply, sent = self.chat_reply(OHM_REPLY)
        self.assertNotIn("MEDIA", reply["content"])
        self.assertEqual(reply["resources"], {"image": None, "image_pending": True, "video": True})
        self.assertNotIn("Ohmsches Gesetz\"", json.dumps(reply["resources"]))   # the term stays on the server
        self.assertNotIn(KEY, sent.get_data(as_text=True))

        with wikipedia():
            image = self.resources(reply["id"]).get_json()["image"]
        self.assertEqual((image["author"], image["licence"]), ("Ann Example", "CC BY-SA 4.0"))
        self.assertEqual(image["file_url"], "https://commons.wikimedia.org/wiki/File:Diagram.png")
        self.assertTrue(image["url"].startswith("https://upload.wikimedia.org/"))
        # Settled and stored: the next view asks Wikipedia nothing.
        with patch.object(media, "_fetch_page", side_effect=AssertionError("asked again")):
            self.assertEqual(self.resources(reply["id"]).get_json()["image"], image)
        conversation = self.client.get(f"/api/assistant/conversations/{self.conversation_id_of(reply)}").get_json()
        self.assertEqual(conversation["conversation"]["messages"][-1]["resources"]["image"], image)

        http = ohm_videos()
        with youtube(http), self.assertLogs(application.app.logger, level="INFO") as logs:
            response = self.resources(reply["id"], "videos")
        body = response.get_data(as_text=True)
        items = response.get_json()["items"]
        self.assertEqual([item["video_id"] for item in items], ["aaaaaaaaaaa"])   # deleted + age-restricted dropped
        self.assertEqual(items[0]["title"], "Ohmsches Gesetz – Versuch mit Widerständen")
        self.assertEqual(items[0]["channel"], "Physik mit Herz")
        self.assertEqual(items[0]["url"], "https://www.youtube.com/watch?v=aaaaaaaaaaa")
        self.assertEqual(items[0]["thumbnail"], "/media/yt-thumb/aaaaaaaaaaa")
        self.assertTrue(items[0]["supplementary"])
        self.assertNotIn(KEY, body)
        self.assertNotIn(KEY, "\n".join(logs.output))
        search = http.calls[0]["params"]
        self.assertEqual((search["relevanceLanguage"], search["regionCode"]), ("de", "DE"))
        self.assertIn("Ohmsches Gesetz", search["q"])
        self.assertIn("Physik", search["q"])
        self.assertIn("Klasse 8", search["q"])

        # Another tap, another student, the same topic: served from the shared cache.
        bob = self.register("bob")
        bob_reply, _ = self.chat_reply(OHM_REPLY, client=bob)
        silent = FakeHttp(error=AssertionError("searched again"))
        with youtube(silent):
            self.assertEqual(self.resources(reply["id"], "videos").get_json()["items"], items)
            self.assertEqual(self.resources(bob_reply["id"], "videos", client=bob).get_json()["items"], items)
        self.assertEqual(silent.calls, [])

    def conversation_id_of(self, reply):
        with application.app.app_context():
            return db.session.get(application.ConversationMessage, reply["id"]).conversation_id

    def test_one_student_cannot_spend_the_whole_days_searches(self):
        # The chat term comes from the model, which a student can steer to ever new topics:
        # only requests that really searched count against the student's daily share, and
        # pictures never do.
        first, _ = self.chat_reply(OHM_REPLY)
        second, _ = self.chat_reply(OHM_REPLY.replace("MEDIA: diagram | Ohmsches Gesetz",
                                                      "MEDIA: diagram | Elektrischer Widerstand"))
        bob = self.register("bob")
        bob_reply, _ = self.chat_reply(OHM_REPLY.replace("MEDIA: diagram | Ohmsches Gesetz",
                                                         "MEDIA: diagram | Spannung"), client=bob)
        was_enabled = application.limiter.enabled
        application.app.config.update(TESTING=False, YOUTUBE_SEARCHES_PER_STUDENT=1)
        application.limiter.enabled = True
        application.limiter.reset()
        try:
            with youtube(ohm_videos()):
                self.assertEqual(self.resources(first["id"], "videos").status_code, 200)
            silent = FakeHttp(error=AssertionError("searched over the share"))
            with youtube(silent):
                self.assertEqual(self.resources(second["id"], "videos").status_code, 429)
            self.assertEqual(silent.calls, [])
            with wikipedia("Elektrischer Widerstand"):
                self.assertEqual(self.resources(second["id"]).status_code, 200)      # the picture
            with youtube(ohm_videos()):
                self.assertEqual(self.resources(bob_reply["id"], "videos", client=bob).status_code, 200)
        finally:
            application.app.config.update(TESTING=True)
            application.limiter.enabled = was_enabled
            application.limiter.reset()

    def test_a_vocabulary_definition_gets_no_resources(self):
        reply, _ = self.chat_reply(VOCAB_REPLY)
        self.assertNotIn("resources", reply)
        self.assertNotIn("MEDIA", reply["content"])
        response = self.resources(reply["id"])
        self.assertEqual((response.status_code, response.get_json()["code"]), (404, "no_resources"))

    def test_other_students_and_user_turns_are_refused(self):
        reply, _ = self.chat_reply(OHM_REPLY)
        bob = self.register("bob")
        for part in (None, "videos"):
            response = self.resources(reply["id"], part, client=bob)
            self.assertEqual((response.status_code, response.get_json()["code"]), (404, "message_not_found"))
        with application.app.app_context():
            user_turn = db.session.scalar(db.select(application.ConversationMessage.id).where(
                application.ConversationMessage.role == "user"))
        self.assertEqual(self.resources(user_turn).status_code, 404)
        guest = application.app.test_client()
        self.assertNotEqual(guest.get(f"/api/assistant/messages/{reply['id']}/resources").status_code, 200)

    def test_a_model_written_youtube_link_is_never_stored_as_a_link(self):
        reply, _ = self.chat_reply("Hier ein Tipp: [Video](https://www.youtube.com/watch?v=abcdefghijk) "
                                   "und https://youtu.be/abcdefghijk\nTOPIC: Physics | Ohm")
        self.assertNotIn("youtu", reply["content"])
        self.assertIn("Hier ein Tipp: Video", reply["content"])

    def test_without_a_key_there_are_no_video_cards(self):
        application.app.config["YOUTUBE_API_KEY"] = ""
        reply, _ = self.chat_reply(OHM_REPLY)
        self.assertEqual(reply["resources"]["video"], False)
        http = FakeHttp(["aaaaaaaaaaa"], [video("aaaaaaaaaaa")])
        with youtube(http):
            data = self.resources(reply["id"], "videos").get_json()
        self.assertEqual((data["items"], data["reason"]), ([], "videos_unavailable"))
        self.assertEqual(http.calls, [])

    def test_the_feature_switch_hides_everything(self):
        reply, _ = self.chat_reply(OHM_REPLY)
        application.app.config["FEATURE_LEARNING_RESOURCES"] = False
        self.assertEqual(self.resources(reply["id"]).status_code, 404)
        conversation = self.client.get(f"/api/assistant/conversations/{self.conversation_id_of(reply)}").get_json()
        self.assertNotIn("resources", conversation["conversation"]["messages"][-1])

    def test_the_key_never_reaches_a_page(self):
        for path in ("/", "/assistant", "/lesson"):
            response = self.client.get(path)
            self.assertNotIn(KEY, response.get_data(as_text=True), path)

    def test_the_csp_needs_no_new_image_host_and_no_frames(self):
        policy = self.client.get("/assistant").headers.get("Content-Security-Policy", "")
        img_src = next(part for part in policy.split(";") if part.strip().startswith("img-src"))
        self.assertNotIn("ytimg", img_src)
        self.assertNotIn("youtube", policy)
        self.assertNotIn("frame-src", policy)


# ---------------------------------------------------------------------- Short Lessons
LESSON = {
    "lesson_title": "Ohmsches Gesetz",
    "detected_level": "Klasse 8",
    "concepts": [{"name": "Ohmsches Gesetz", "evidence": "Ziel"}],
    "explanation": "U = R · I.",
    "worked_example": {"problem": "R = 10 Ω, I = 2 A", "steps": ["U = 10 · 2"], "answer": "U = 20 V"},
    "teacher_tips": [], "exceptions": [],
    "video_usefulness": 6,
    "videos": [{"query": "Ohmsches Gesetz einfach erklärt", "why": "Der Versuch zeigt den Zusammenhang."}],
    "image_usefulness": 0, "image_search_terms": [],
    "question": {"id": "q1", "concept": "Ohmsches Gesetz", "difficulty": 1, "type": "multiple_choice",
                 "prompt": "Wie groß ist U bei R = 5 Ω und I = 2 A?", "hint": "U = R · I",
                 "options": [{"id": "a", "label": "10 V"}, {"id": "b", "label": "2,5 V"}], "expected_answer": "a"},
}


class LessonResourcesTests(ResourcesCase):
    def start_lesson(self, client=None):
        with FakeAI(lesson=LESSON):
            response = (client or self.client).post(
                "/api/analyze", data={"study_goal": "Ohmsches Gesetz verstehen", "subject": "Physics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response

    def videos(self, session_id, client=None, query=""):
        return (client or self.client).get(f"/api/lessons/{session_id}/videos{query}")

    def test_a_german_lesson_searches_in_german(self):
        data = self.start_lesson().get_json()
        link = data["lesson"]["video_links"][0]
        self.assertIn("Physik", link["youtube"])
        self.assertIn("Klasse+8", link["youtube"])
        self.assertNotIn("Physics", link["youtube"])
        self.assertNotIn("Grade", link["youtube"])
        self.assertEqual((link["kind"], link["query"]), ("search", "Ohmsches Gesetz einfach erklärt"))
        self.assertIn("studyflix", link)
        self.assertTrue(data["videos_available"])

        http = ohm_videos()
        with youtube(http):
            response = self.videos(data["session_id"], query="?q=something+else&part=evil")
        items = response.get_json()["items"]
        self.assertEqual([item["video_id"] for item in items], ["aaaaaaaaaaa"])
        params = http.calls[0]["params"]
        self.assertEqual(params["q"], "Ohmsches Gesetz einfach erklärt Physik Klasse 8")   # from the stored lesson
        self.assertEqual((params["relevanceLanguage"], params["safeSearch"]), ("de", "strict"))
        self.assertNotIn(KEY, response.get_data(as_text=True))

        silent = FakeHttp(error=AssertionError("searched again"))
        with youtube(silent):
            self.assertEqual(self.videos(data["session_id"]).get_json()["items"], items)
        self.assertEqual(silent.calls, [])

    def test_without_a_key_lessons_keep_honest_search_links_and_no_cards(self):
        application.app.config["YOUTUBE_API_KEY"] = ""
        data = self.start_lesson().get_json()
        self.assertFalse(data["videos_available"])
        self.assertTrue(data["lesson"]["video_links"][0]["youtube"].startswith("https://www.youtube.com/results?"))
        response = self.videos(data["session_id"])
        self.assertEqual((response.status_code, response.get_json()["code"]), (404, "videos_unavailable"))

    def test_other_students_lessons_are_refused(self):
        session_id = self.start_lesson().get_json()["session_id"]
        bob = self.register("bob")
        response = self.videos(session_id, client=bob)
        self.assertEqual((response.status_code, response.get_json()["code"]), (404, "lesson_expired"))

    def test_a_lesson_without_a_video_suggestion_spends_nothing(self):
        session_id = self.start_lesson().get_json()["session_id"]
        application.SESSIONS[session_id]["lesson"]["video_links"] = []
        http = FakeHttp(error=AssertionError("searched"))
        with youtube(http):
            response = self.videos(session_id)
        self.assertEqual((response.status_code, response.get_json()["code"]), (404, "no_video_offer"))

    def test_the_bootstrap_says_only_whether_videos_exist(self):
        session_id = self.start_lesson().get_json()["session_id"]
        page_html = self.client.get(f"/lesson?session_id={session_id}").get_data(as_text=True)
        self.assertIn('"videos_available": true', page_html)
        self.assertNotIn(KEY, page_html)


# ---------------------------------------------------------------------- quota, cache, re-check
class QuotaAndCacheTests(ResourcesCase):
    def search(self, topic="Ohmsches Gesetz", query="Ohmsches Gesetz Physik Klasse 8"):
        with application.app.test_request_context():
            return application.youtube_videos_for(topic, query=query, language="German", limit=2)

    def test_the_daily_cap_stops_searching(self):
        application.app.config["YOUTUBE_DAILY_SEARCH_LIMIT"] = 1
        with youtube(ohm_videos()):
            self.assertEqual(self.search()[1], "ok")
        http = FakeHttp(error=AssertionError("over the cap"))
        with youtube(http):
            self.assertEqual(self.search(topic="Widerstand", query="Widerstand Physik Klasse 8"), ([], "daily_limit"))
            # A cached query is still answered: the cap limits searches, not views.
            self.assertEqual(len(self.search()[0]), 1)
        self.assertEqual(http.calls, [])

    def test_quota_exhaustion_pauses_searches_for_the_day(self):
        body = json.dumps({"error": {"errors": [{"reason": "quotaExceeded"}]}})
        with youtube(FakeHttp(search_status=403, search_body=body)):
            self.assertEqual(self.search(), ([], "quota"))
        http = FakeHttp(error=AssertionError("searched despite the quota"))
        with youtube(http):
            self.assertEqual(self.search(topic="Widerstand", query="Widerstand Physik Klasse 8"), ([], "quota"))
        self.assertEqual(http.calls, [])

    def test_empty_results_are_cached_too(self):
        with youtube(FakeHttp([], [])):
            self.assertEqual(self.search(), ([], "no_results"))
        http = FakeHttp(error=AssertionError("searched a dead topic again"))
        with youtube(http):
            self.assertEqual(self.search(), ([], "no_results"))

    def age(self, *, created_days=0.0, checked_days=0.0):
        with application.app.app_context():
            now = application.utcnow()
            for row in db.session.scalars(db.select(application.LearningResourceCache)).all():
                row.created_at = now - timedelta(days=created_days)
                row.checked_at = now - timedelta(days=checked_days)
            db.session.commit()

    def test_cards_older_than_a_day_are_rechecked_and_deleted_videos_dropped(self):
        two = FakeHttp(["aaaaaaaaaaa", "ddddddddddd"], [video("aaaaaaaaaaa"), video("ddddddddddd", title="Ohmsches Gesetz Teil 2")])
        with youtube(two):
            self.assertEqual(len(self.search()[0]), 2)
        self.age(created_days=2, checked_days=2)
        recheck = FakeHttp([], [video("aaaaaaaaaaa")])        # ddddddddddd was deleted meanwhile
        with youtube(recheck):
            cards, _reason = self.search()
        self.assertEqual([card["video_id"] for card in cards], ["aaaaaaaaaaa"])
        self.assertEqual([call["url"] for call in recheck.calls], [youtube_videos.VIDEOS_URL])

    def test_an_expired_entry_is_searched_again_and_old_rows_are_deleted(self):
        with youtube(ohm_videos()):
            self.search()
        self.age(created_days=8, checked_days=8)
        http = ohm_videos()
        with youtube(http):
            self.search()
        self.assertEqual(http.calls[0]["url"], youtube_videos.SEARCH_URL)
        self.age(created_days=31, checked_days=31)
        with youtube(ohm_videos()):
            self.search(topic="Widerstand", query="Widerstand Physik Klasse 8")
        self.assertEqual(len(self.cache_rows()), 1, "rows older than 30 days are deleted")

    def test_the_cache_stores_no_key(self):
        with youtube(ohm_videos()):
            self.search()
        for row in self.cache_rows():
            self.assertNotIn(KEY, row.payload_json + row.search_query)


# ---------------------------------------------------------------------- thumbnail proxy
class ThumbnailProxyTests(ResourcesCase):
    def seed(self):
        with youtube(ohm_videos()), application.app.test_request_context():
            application.youtube_videos_for("Ohmsches Gesetz", query="Ohmsches Gesetz Physik", language="German")

    def test_bad_and_unknown_ids_are_refused_without_a_fetch(self):
        self.seed()
        fetch = MagicMock(side_effect=AssertionError("fetched"))
        with patch.object(youtube_videos, "_http_bytes", fetch):
            for bad in ("short", "abcdefghijk.png", "abc%2Fdefghij", "zzzzzzzzzzz"):
                self.assertEqual(self.client.get(f"/media/yt-thumb/{bad}").status_code, 404, bad)
        fetch.assert_not_called()

    def test_a_found_video_thumbnail_is_served_by_the_app(self):
        self.seed()
        fetch = MagicMock(return_value=(200, b"\xff\xd8\xffjpeg", "image/jpeg"))
        with patch.object(youtube_videos, "_http_bytes", fetch):
            first = self.client.get("/media/yt-thumb/aaaaaaaaaaa")
            second = self.client.get("/media/yt-thumb/aaaaaaaaaaa")
        self.assertEqual((first.status_code, first.mimetype, first.data), (200, "image/jpeg", b"\xff\xd8\xffjpeg"))
        self.assertEqual(second.status_code, 200)
        fetch.assert_called_once()
        self.assertEqual(fetch.call_args.args[0], "https://i.ytimg.com/vi/aaaaaaaaaaa/mqdefault.jpg")
        self.assertIn("private", first.headers["Cache-Control"])

    def test_guests_get_no_thumbnail(self):
        self.seed()
        guest = application.app.test_client()
        with patch.object(youtube_videos, "_http_bytes", MagicMock(side_effect=AssertionError("fetched"))):
            self.assertNotEqual(guest.get("/media/yt-thumb/aaaaaaaaaaa").status_code, 200)


# ---------------------------------------------------------------------- migration
class MigrationTests(ResourcesCase):
    def test_an_old_database_gets_the_column_and_the_record_once(self):
        reply, _ = self.chat_reply("Kurz.\nTOPIC: none")
        with application.app.app_context():
            application.ensure_database()
            with db.engine.begin() as connection:
                connection.execute(text("ALTER TABLE conversation_message DROP COLUMN resources_json"))
                connection.execute(text("DROP TABLE learning_resource_cache"))
                connection.execute(text("DELETE FROM schema_migration WHERE version = '046_learning_resources'"))
            # An old database is met by a starting process with fresh connections. A pooled SQLite
            # connection that is idle since before the DROP keeps its cached schema and would
            # refuse the ADD COLUMN as a duplicate (seen only when earlier tests left pooled
            # connections behind, e.g. test_analysis_steps' migration test).
            db.session.remove()
            db.engine.dispose()
            application.ensure_database()
            application.ensure_database()
            columns = {column["name"] for column in inspect(db.engine).get_columns("conversation_message")}
            self.assertIn("resources_json", columns)
            self.assertIn("learning_resource_cache", inspect(db.engine).get_table_names())
            count = db.session.scalar(text("SELECT COUNT(*) FROM schema_migration WHERE version = '046_learning_resources'"))
            self.assertEqual(count, 1)
            stored = db.session.get(application.ConversationMessage, reply["id"])
            self.assertEqual(stored.resources_json, "{}")
            self.assertEqual(stored.content, "Kurz.")


if __name__ == "__main__":
    unittest.main()

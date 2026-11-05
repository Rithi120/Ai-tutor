"""Continue Learning (WP4): one click turns a conversation into a lesson of the existing
lesson system.

The gate and prompt are pure (learnova/assistant/continue_learning.py) and tested directly;
the route is tested through Flask with `app.create_response` patched, so no AI call is
made. What is checked: the gate refuses greetings and asks for a subject with one tap;
the lesson prompt carries the whole relevant conversation (first question, the student's
wrong answer, the photo reading - never read again - with assistant turns shortened) and
the subject's own teaching method; the lesson is saved like any lesson, opens at its
normal address after a restart, passes the project lesson's guards and keeps the chat
evidence; a double click builds one lesson; a failure keeps the conversation and can be
retried; the free lesson allowance applies (Premium unaffected); ownership and flags hold.
"""

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_continue_learning_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import assistant  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.prompts import PROMPT_VERSIONS  # noqa: E402
from learnova.assistant import continue_learning as cl  # noqa: E402
from learnova.lessons.strategies import strategy_for  # noqa: E402
from learnova.subscriptions.plans import SubscriptionSettings  # noqa: E402
from tests.typing_helpers import fetch, present  # noqa: E402

db = application.db
PASSWORD = "correct-horse-battery"

FIRST_QUESTION = "Ich verstehe nicht, wie man quadratische Gleichungen wie x^2 - 5x + 6 = 0 löst."
WRONG_ANSWER = "Ist die Lösung x = 4? Ich habe einfach 5 - 1 gerechnet."
LONG_REPLY = "Gute Frage. " + "Erklärung " * 250 + "ENDE-DER-LANGEN-ANTWORT"
PHOTO_READING = {
    "images": [{"index": 1, "kind": "worksheet", "rotation": 0, "cut_off": False, "legibility": "clear"}],
    "blocks": [{"image": 1, "type": "printed_text", "text": "Aufgabe 3: x^2 - 7x + 10 = [?0]",
                "author": "printed", "confidence": 0.9}],
    "diagrams": [], "uncertain": [{"image": 1, "text": "0", "alternatives": ["6"], "reason": "blurred",
                                   "affects_answer": True}],
    "clarification_question": "", "summary": "Arbeitsblatt zu quadratischen Gleichungen.", "language": "de",
}


def lesson_payload(title="Quadratische Gleichungen lösen", **extra):
    payload = {
        "lesson_title": title, "detected_level": "Klasse 9",
        "concepts": [{"name": "Quadratische Gleichungen", "evidence": "aus dem Chat"}],
        "parts": [
            {"kind": "objective", "title": "Ziel", "body": "Du lernst, quadratische Gleichungen zu lösen.",
             "items": [], "answer": "", "from_notes": True, "source_pages": [1]},
            # Placed before any explanation on purpose: the guard moves it after the first one.
            {"kind": "guided_exercise", "title": "Übung", "body": "Löse x^2 - 4 = 0.", "items": [],
             "answer": "x = 2 oder x = -2", "from_notes": True, "source_pages": [1]},
            {"kind": "explanation", "title": "So geht es", "body": "Bringe die Gleichung in Normalform ...",
             "items": [], "answer": "", "from_notes": True, "source_pages": [1]},
            {"kind": "mistakes", "title": "Typische Fehler", "body": "5 - 1 ist keine Lösungsformel.",
             "items": [], "answer": "", "from_notes": False, "source_pages": []},
            {"kind": "summary", "title": "Zusammenfassung", "body": "Normalform, dann pq-Formel.", "items": [],
             "answer": "", "from_notes": False, "source_pages": []},
        ],
        "explanation": "Kurzfassung.", "worked_example": {"problem": "x^2 - 9 = 0", "steps": ["x^2 = 9"],
                                                          "answer": "x = ±3"},
        "teacher_tips": ["Probe machen."], "exceptions": [],
        "question": {"id": "q1", "concept": "Quadratische Gleichungen", "difficulty": 1, "type": "multiple_choice",
                     "prompt": "Welche Lösungen hat x^2 - 5x + 6 = 0?", "hint": "Faktorisiere.",
                     "options": [{"id": "a", "label": "2 und 3"}, {"id": "b", "label": "1 und 4"}],
                     "expected_answer": "a"},
        "chat_evidence": {"misunderstandings": ["rechnet 5 - 1 statt die Gleichung zu lösen", "  "],
                          "wrong_answers": ["x = 4"] * 3 + ["y" * 400], "known": ["Normalform"],
                          "ignored": ["not kept"]},
    }
    payload.update(extra)
    return payload


class Usage:
    input_tokens, output_tokens, total_tokens = 300, 900, 1200


class Response:
    def __init__(self, text):
        self.output_text, self.model, self.usage = text, "test-model", Usage()


class FakeAI:
    """Patches app.create_response: lesson_generation returns `lessons` in turn (an exception
    is raised), assistant_chat returns `chat`. With `hold`, the first lesson call waits until
    `proceed` is set and sets `started` as it begins. Thread-safe call log."""

    def __init__(self, *lessons: Any, chat: str = "Antwort.\nTOPIC: none", hold: bool = False):
        self.lessons = list(lessons) or [lesson_payload()]
        self.chat = chat
        self.hold = hold
        self.started, self.proceed = threading.Event(), threading.Event()
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._patch = patch.object(application, "create_response", side_effect=self)

    def __call__(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
            lesson_number = len(self.lesson_calls())
        task = kwargs["task_type"]
        if task == "assistant_chat":
            return Response(self.chat)
        if task != "lesson_generation":
            raise AssertionError(f"unexpected AI task {task}")
        if self.hold and lesson_number == 1:
            self.started.set()
            if not self.proceed.wait(timeout=20):
                raise AssertionError("the held lesson was never released")
        with self._lock:
            outcome = self.lessons.pop(0) if len(self.lessons) > 1 else self.lessons[0]
        if isinstance(outcome, BaseException):
            raise outcome
        return Response(outcome if isinstance(outcome, str) else json.dumps(outcome, ensure_ascii=False))

    def lesson_calls(self):
        return [call for call in self.calls if call["task_type"] == "lesson_generation"]

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self.proceed.set()
        self._patch.stop()


class InThread:
    def __init__(self, call):
        self.response, self.error = None, None
        self.thread = threading.Thread(target=self._run, args=(call,), daemon=True)

    def _run(self, call):
        try:
            self.response = call()
        except BaseException as error:  # noqa: BLE001 - reported by join
            self.error = error

    def start(self):
        self.thread.start()
        return self

    @property
    def done(self):
        return not self.thread.is_alive()

    def join(self, test) -> Any:
        self.thread.join(timeout=30)
        test.assertFalse(self.thread.is_alive(), "request thread did not finish")
        if self.error is not None:
            raise self.error
        return present(self.response, "response")


# ------------------------------------------------------------------------ pure
class GateTests(unittest.TestCase):
    def gate(self, **overrides: Any) -> cl.Gate:
        values: dict[str, Any] = dict(user_texts=[FIRST_QUESTION], successful_replies=1, readable_photo=False,
                                      subject="Mathematics", topic="Quadratische Gleichungen")
        values.update(overrides)
        return cl.lesson_gate(**values)

    def test_a_real_question_with_a_reply_and_a_subject_passes(self):
        self.assertEqual(self.gate(), cl.Gate(True, ""))

    def test_no_successful_reply(self):
        self.assertEqual(self.gate(successful_replies=0).reason, "no_reply")

    def test_greetings_and_the_photo_placeholder_are_not_context(self):
        self.assertEqual(self.gate(user_texts=["hi", "hallo", "danke"], successful_replies=3).reason, "too_little")
        self.assertEqual(self.gate(user_texts=["Please look at my photo."],
                                   placeholders=["Please look at my photo."]).reason, "too_little")
        self.assertEqual(self.gate(user_texts=["Brüche?"]).reason, "too_little")

    def test_a_readable_photo_or_two_real_messages_are_enough(self):
        self.assertTrue(self.gate(user_texts=["Please look at my photo."], readable_photo=True,
                                  placeholders=["Please look at my photo."]).ok)
        self.assertTrue(self.gate(user_texts=["Brüche?", "Und kürzen?"]).ok)

    def test_not_a_school_topic(self):
        self.assertEqual(self.gate(topic="none").reason, "not_educational")
        self.assertEqual(self.gate(topic="none", subject="").reason, "not_educational")

    def test_an_unknown_subject_asks_for_one_tap(self):
        self.assertEqual(self.gate(subject="", topic="").reason, "subject_needed")

    def test_subject_choices_put_recent_subjects_first(self):
        choices = ("Mathematics", "German", "English", "History", "Biology", "Physics", "Chemistry", "Other")
        self.assertEqual(cl.subject_choice_order(["History", "History", "Other", "Klingon"], choices),
                         ["History", "Mathematics", "German", "English", "Biology", "Physics"])


class TranscriptTests(unittest.TestCase):
    def history(self, middle_pairs=0):
        turns = [{"role": "user", "content": FIRST_QUESTION}, {"role": "assistant", "content": LONG_REPLY}]
        for index in range(middle_pairs):
            turns += [{"role": "user", "content": f"Zwischenfrage {index} " + "wort " * 200},
                      {"role": "assistant", "content": "Antwort " * 200}]
        return turns + [{"role": "user", "content": WRONG_ANSWER}, {"role": "assistant", "content": "Fast."}]

    def test_student_turns_are_verbatim_and_assistant_turns_shortened(self):
        text = cl.select_transcript(self.history())
        self.assertTrue(text.startswith(f"Student: {FIRST_QUESTION}"))
        self.assertIn(f"Student: {WRONG_ANSWER}", text)
        self.assertIn("Assistant: Gute Frage.", text)
        self.assertNotIn("ENDE-DER-LANGEN-ANTWORT", text)
        assistant_line = next(line for line in text.split("\n\n") if line.startswith("Assistant: Gute"))
        self.assertLessEqual(len(assistant_line), len("Assistant: ") + cl.ASSISTANT_CLIP + 2)

    def test_the_oldest_middle_turns_go_first_and_the_first_question_stays(self):
        text = cl.select_transcript(self.history(middle_pairs=30), budget_tokens=2500)
        self.assertTrue(text.startswith(f"Student: {FIRST_QUESTION}"))
        self.assertIn(WRONG_ANSWER, text)
        self.assertRegex(text, r"\[… \d+ earlier messages left out\]")
        self.assertNotIn("Zwischenfrage 0 ", text)
        self.assertLessEqual(assistant.estimate_tokens(text), 2500 + 50)

    def test_long_student_turns_are_bounded(self):
        text = cl.select_transcript([{"role": "user", "content": "x " * 5000}, {"role": "assistant", "content": "a"}])
        self.assertLessEqual(len(text.split("\n\n")[0]), len("Student: ") + cl.USER_CLIP + 2)


class EvidenceTests(unittest.TestCase):
    def test_evidence_is_bounded_and_only_three_lists_survive(self):
        evidence = cl.sanitize_evidence(lesson_payload()["chat_evidence"])
        self.assertEqual(set(evidence), {"misunderstandings", "wrong_answers", "known"})
        self.assertEqual(evidence["misunderstandings"], ["rechnet 5 - 1 statt die Gleichung zu lösen"])
        self.assertEqual(evidence["wrong_answers"], ["x = 4", "y" * 200])
        self.assertEqual(cl.sanitize_evidence("nonsense"), {"misunderstandings": [], "wrong_answers": [], "known": []})
        many = cl.sanitize_evidence({"known": [f"item {n}" for n in range(9)] + [True, None, {"a": 1}]})
        self.assertEqual(len(many["known"]), cl.MAX_EVIDENCE_ITEMS)


# ------------------------------------------------------------------------ routes
class ContinueLearningTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, FEATURE_ASSISTANT_CHAT=True, FEATURE_CONTINUE_LEARNING=True, FEATURE_CHAT_IMAGES=True,
            FEATURE_LESSON_MEDIA=False, ASSISTANT_MODEL="openai/gpt-oss-20b",
            AI_CACHE_DIR=tempfile.mkdtemp(prefix="learnova-wp4-cl-cache-"),
            SUBSCRIPTION_SETTINGS=SubscriptionSettings())
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    # helpers ---------------------------------------------------------------------
    def register(self, name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": PASSWORD, "grade": "9"})
        return client

    def login(self, name):
        client = application.app.test_client()
        self.assertEqual(client.post("/login", data={"identifier": name, "password": PASSWORD}).status_code, 302)
        return client

    def user_id(self, name="alice"):
        with application.app.app_context():
            return db.session.scalar(db.select(application.User.id).where(application.User.username == name))

    def conversation(self, *turns, subject="Mathematics", topic="Quadratische Gleichungen", photo=False):
        """A conversation with these (role, content[, error]) turns, stored directly."""

        response = self.client.post("/api/assistant/conversations", json={})
        self.assertEqual(response.status_code, 201)
        conversation_id = response.get_json()["conversation"]["id"]
        turns = turns or (("user", FIRST_QUESTION), ("assistant", LONG_REPLY),
                          ("user", WRONG_ANSWER), ("assistant", "Fast - prüfe die Probe."))
        with application.app.app_context():
            row = fetch(db.session, application.Conversation, conversation_id)
            row.subject, row.topic = subject, topic
            for number, turn in enumerate(turns):
                role, content = turn[0], turn[1]
                message = application.ConversationMessage(
                    conversation_id=conversation_id, role=role, content=content,
                    error_category=turn[2] if len(turn) > 2 else "",
                    created_at=application.utcnow() - timedelta(minutes=len(turns) - number))
                db.session.add(message)
                db.session.flush()
                if photo and role == "user" and number == 0:
                    db.session.add(application.ChatAttachment(
                        user_id=self.user_id(), conversation_id=conversation_id, message_id=message.id,
                        purpose="chat", reading_json=json.dumps(PHOTO_READING, ensure_ascii=False),
                        read_status="ready", sent_at=application.utcnow(), data=b"\x89PNG not loaded"))
            row.message_count = len(turns)
            db.session.commit()
        return conversation_id

    def lesson(self, conversation_id, client=None, **body):
        return (client or self.client).post(f"/api/assistant/conversations/{conversation_id}/lesson", json=body)

    def status(self, conversation_id, client=None):
        return (client or self.client).get(f"/api/assistant/conversations/{conversation_id}/lesson")

    def lesson_rows(self, conversation_id):
        with application.app.app_context():
            rows = db.session.scalars(db.select(application.ConversationLesson).where(
                application.ConversationLesson.conversation_id == conversation_id)).all()
            for row in rows:
                db.session.expunge(row)
            return rows

    def messages(self, conversation_id):
        with application.app.app_context():
            return [(row.role, row.content) for row in db.session.scalars(
                db.select(application.ConversationMessage).where(
                    application.ConversationMessage.conversation_id == conversation_id,
                ).order_by(application.ConversationMessage.id)).all()]

    def committed_lessons(self):
        with application.app.app_context():
            return db.session.query(application.UsageQuotaEvent).filter_by(kind="lesson", status="committed").count()

    def lesson_allowance_used(self, name="alice"):
        with application.app.app_context():
            return application.entitlements.allowance(self.user_id(name), "lesson").used

    def use_free_lessons(self, count=2, name="alice"):
        with application.app.app_context():
            for _ in range(count):
                application.entitlements.reserve(self.user_id(name), "lesson").commit(reference="seeded")

    def make_premium(self, name="alice"):
        with application.app.app_context():
            db.session.add(application.PremiumEntitlement(
                user_id=self.user_id(name), starts_at=application.utcnow() - timedelta(days=1),
                expires_at=application.utcnow() + timedelta(days=20), status="active"))
            db.session.commit()


class GateRouteTests(ContinueLearningTestCase):
    def test_a_greeting_only_thread_is_refused_with_guidance(self):
        conversation_id = self.conversation(("user", "hi"), ("assistant", "Hallo! Wobei kann ich helfen?"),
                                            subject="", topic="")
        with FakeAI() as ai:
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 422)
        body = response.get_json()
        self.assertEqual(body["code"], "not_enough_context")
        self.assertEqual(body["details"]["reason"], "too_little")
        self.assertIn("Tell me which topic you are working on", body["error"])
        self.assertEqual(ai.calls, [], "no AI call for a greeting")
        self.assertEqual(self.lesson_rows(conversation_id), [])

    def test_the_state_says_what_to_add_when_there_is_too_little(self):
        short = self.conversation(("user", "Was ist x?"), ("assistant", "Eine Variable."), topic="Variablen")
        state = self.status(short).get_json()["continue_learning"]
        self.assertEqual((state["available"], state["reason"]), (False, "too_little"))
        self.assertIn("Tell me which topic you are working on", state["guidance"])
        chat = self.conversation(("user", "hi"), ("assistant", "Hallo!"), subject="", topic="none")
        self.assertNotIn("guidance", self.status(chat).get_json()["continue_learning"],
                         "a chat the assistant called no school topic gets no nudge")
        ready = self.conversation()
        self.assertNotIn("guidance", self.status(ready).get_json()["continue_learning"])

    def test_no_successful_reply_is_refused(self):
        conversation_id = self.conversation(("user", FIRST_QUESTION),
                                            ("assistant", "AI is temporarily unavailable.", "provider_timeout"))
        response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["details"]["reason"], "no_reply")

    def test_an_unknown_subject_asks_for_one_tap_with_recent_subjects_first(self):
        with application.app.app_context():
            db.session.add(application.Lesson(user_id=self.user_id(), session_id="h" * 32, subject="Geschichte",
                                              title="Weimar", content_json="{}"))
            db.session.commit()
        conversation_id = self.conversation(subject="", topic="")
        response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 422)
        body = response.get_json()
        self.assertEqual(body["code"], "subject_needed")
        self.assertIn("Which subject is this?", body["error"])
        choices = body["details"]["subject_choices"]
        self.assertEqual(choices[0], {"value": "History", "label": "History"})
        self.assertLessEqual(len(choices), 6)
        self.assertNotIn("Other", [item["value"] for item in choices])
        state = self.client.get(f"/api/assistant/conversations/{conversation_id}").get_json()[
            "conversation"]["continue_learning"]
        self.assertTrue(state["available"])
        self.assertEqual(state["reason"], "subject_needed")
        self.assertEqual(state["subject_choices"][0]["value"], "History")

    def test_a_tapped_subject_is_kept_and_builds_the_lesson(self):
        conversation_id = self.conversation(subject="", topic="")
        with FakeAI() as ai:
            response = self.lesson(conversation_id, subject="Mathe")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        with application.app.app_context():
            self.assertEqual(fetch(db.session, application.Conversation, conversation_id).subject, "Mathematics")
        self.assertIn("Topic: derive the main topic from the conversation", ai.lesson_calls()[0]["input"])

    def test_a_tapped_subject_outside_the_list_is_ignored(self):
        conversation_id = self.conversation(subject="", topic="")
        response = self.lesson(conversation_id, subject="Klingonisch")
        self.assertEqual(response.get_json()["code"], "subject_needed")

    def test_not_a_school_topic_is_refused(self):
        conversation_id = self.conversation(subject="", topic="none")
        response = self.lesson(conversation_id, subject="Mathematics")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["details"]["reason"], "not_educational")


class PromptTests(ContinueLearningTestCase):
    def test_the_prompt_carries_the_relevant_conversation_and_the_photo_without_reading_it_again(self):
        conversation_id = self.conversation(photo=True)
        with FakeAI() as ai, \
                patch.object(application, "read_images_for", side_effect=AssertionError("read again")), \
                patch.object(application, "ensure_attachment_readings", side_effect=AssertionError("read again")):
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        call = ai.lesson_calls()[0]
        prompt = call["input"]
        self.assertIn(f"Student: {FIRST_QUESTION}", prompt)
        self.assertIn(f"Student: {WRONG_ANSWER}", prompt)
        self.assertIn("Assistant: Gute Frage.", prompt)
        self.assertNotIn("ENDE-DER-LANGEN-ANTWORT", prompt, "assistant turns are shortened")
        self.assertIn("Aufgabe 3: x^2 - 7x + 10 = [?0]", prompt)
        self.assertIn("parts marked [?...] were not read reliably", prompt)
        self.assertEqual(prompt.count("Aufgabe 3: x^2 - 7x + 10"), 1, "the reading is in the photos block only")
        for piece in (cl.LESSON_OPENING, cl.SESSION_GOAL, cl.EVIDENCE_RULE, cl.DATA_RULE,
                      application.LESSON_JSON_SHAPE, application.LESSON_NO_NOTES_RULE,
                      "Subject: Mathematics", "Topic: Quadratische Gleichungen"):
            self.assertIn(piece, prompt)
        self.assertEqual(call["prompt_version"], PROMPT_VERSIONS["lesson_generation"] + "+chat1")
        self.assertEqual(call["model"], application.TUTOR_MODEL)
        self.assertIn("EXPLANATION LEVEL", call["instructions"])
        for identifier in ("alice", "alice@example.com"):
            self.assertNotIn(identifier, f"{call['instructions']}\n{prompt}")

    def test_the_request_stays_inside_the_configured_budgets(self):
        application.app.config.update(CONTINUE_LEARNING_TRANSCRIPT_TOKENS=600, CONTINUE_LEARNING_PHOTO_CHARACTERS=60)
        middle = []
        for index in range(12):
            middle += [("user", f"Zwischenfrage {index} " + "wort " * 60), ("assistant", "Antwort " * 80)]
        conversation_id = self.conversation(("user", FIRST_QUESTION), ("assistant", LONG_REPLY), *middle,
                                            ("user", WRONG_ANSWER), ("assistant", "Fast."), photo=True)
        with FakeAI() as ai:
            self.assertEqual(self.lesson(conversation_id).status_code, 201)
        prompt = ai.lesson_calls()[0]["input"]
        transcript = prompt.split("CONVERSATION (oldest first; assistant turns shortened):\n<<<\n", 1)[1].split(
            "\n>>>", 1)[0]
        self.assertLessEqual(assistant.estimate_tokens(transcript), 600 + 60)
        self.assertTrue(transcript.startswith(f"Student: {FIRST_QUESTION}"))
        self.assertIn(WRONG_ANSWER, transcript)
        self.assertRegex(transcript, r"\[… \d+ earlier messages left out\]")
        photos = prompt.split(cl.PHOTOS_RULE + "\n<<<\n", 1)[1].split("\n>>>", 1)[0]
        self.assertLessEqual(len(photos), 60 + len("\n[… the rest of the reading was shortened]"))

    def test_the_default_budgets_fit_a_small_provider_tier(self):
        self.assertEqual(application.app.config["CONTINUE_LEARNING_TRANSCRIPT_TOKENS"], 3000)
        self.assertEqual(application.app.config["CONTINUE_LEARNING_PHOTO_CHARACTERS"], 4000)

    def test_previous_wrong_answers_on_the_topic_are_included(self):
        with application.app.app_context():
            lesson = application.Lesson(user_id=self.user_id(), session_id="p" * 32, subject="Mathematics",
                                        title="Alt", content_json="{}")
            db.session.add(lesson)
            db.session.flush()
            db.session.add(application.Attempt(
                lesson_id=lesson.id, question="Löse x^2 = 16", subject="Mathematics",
                concept="Quadratische Gleichungen", student_answer="x = 8", score=0, feedback="Wurzel, nicht halbieren",
                difficulty=1))
            db.session.add(application.ConceptMastery(
                user_id=self.user_id(), subject="Mathematics", concept="Quadratische Gleichungen", attempts=1,
                mastery_score=20, total_score=20))
            db.session.commit()
        conversation_id = self.conversation()
        with FakeAI() as ai:
            self.assertEqual(self.lesson(conversation_id).status_code, 201)
        prompt = ai.lesson_calls()[0]["input"]
        self.assertIn("Mistakes the student made on this topic before", prompt)
        self.assertIn('their answer "x = 8"', prompt)
        self.assertIn("TEACHING METHOD", prompt)
        self.assertIn("developing", prompt.casefold())

    def test_each_subject_gets_its_own_teaching_method(self):
        for subject in ("Mathematics", "English", "Science and Technology", "Ethics", "History"):
            with self.subTest(subject=subject):
                strategy = strategy_for(subject)
                conversation_id = self.conversation(subject=subject, topic="Thema")
                with FakeAI() as ai:
                    response = self.lesson(conversation_id)
                self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
                prompt = ai.lesson_calls()[0]["input"]
                self.assertIn(f"TEACHING METHOD — {strategy.name}.", prompt)
                self.assertIn(f"Subject: {subject}", prompt)
                with application.app.app_context():
                    lesson = fetch(db.session, application.Lesson, response.get_json()["lesson_id"])
                    self.assertEqual(json.loads(lesson.content_json)["strategy"]["key"], strategy.key)
                    self.assertEqual(lesson.subject, subject)
        self.assertEqual(len({strategy_for(s).key for s in ("Mathematics", "English", "Science and Technology",
                                                             "Ethics", "History")}), 5)


class LessonTests(ContinueLearningTestCase):
    def test_the_lesson_is_saved_guarded_and_opens_at_its_address_after_a_restart(self):
        conversation_id = self.conversation(photo=True)
        before = self.messages(conversation_id)
        with FakeAI():
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual(body["status"], "ready")
        self.assertEqual(body["title"], "Quadratische Gleichungen lösen")
        self.assertTrue(body["url"].startswith("/?session_id="))
        session_id = body["url"].split("=", 1)[1]
        with application.app.app_context():
            lesson = fetch(db.session, application.Lesson, body["lesson_id"])
            self.assertEqual((lesson.session_id, lesson.subject), (session_id, "Mathematics"))
            content = json.loads(lesson.content_json)
            state = json.loads(present(lesson.study_session, "study session").state_json)
        # The project lesson's guards: teach before testing, nothing "from your notes".
        kinds = [part["kind"] for part in content["parts"]]
        self.assertLess(kinds.index("explanation"), kinds.index("guided_exercise"))
        self.assertTrue(all(part["from_notes"] is False and part["source_pages"] == [] for part in content["parts"]))
        self.assertTrue(content["teaching_order_ok"])
        self.assertNotIn("chat_evidence", content, "the evidence is not sent to the page")
        # The same /api/answer behaviour as a project topic lesson.
        self.assertTrue(state["teaching_first"])
        self.assertEqual(state["strategy_key"], strategy_for("Mathematics").key)
        self.assertEqual(state["session_kind"], "chat_lesson")
        self.assertEqual(state["origin"]["kind"], "assistant")
        self.assertEqual(state["origin"]["conversation_id"], conversation_id)
        self.assertEqual(state["chat_evidence"]["wrong_answers"], ["x = 4", "y" * 200])
        self.assertIn("Aufgabe 3", state["source_context"])
        self.assertEqual(state["learning_objective"], "Quadratische Gleichungen")
        # The conversation itself is untouched.
        self.assertEqual(self.messages(conversation_id), before)
        row = self.lesson_rows(conversation_id)[0]
        self.assertEqual((row.status, row.lesson_id, row.session_id, row.attempts), ("ready", lesson.id, session_id, 1))
        # Opens after a restart (in-memory sessions gone).
        application.SESSIONS.clear()
        page = self.client.get(body["url"])
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn(session_id, html, "the lesson bootstrap is rendered")
        self.assertIn("Quadratische Gleichungen l", html)
        # And shows in the state the chat page reads.
        state = self.status(conversation_id).get_json()["continue_learning"]
        self.assertEqual((state["status"], state["url"], state["title"]),
                         ("ready", body["url"], "Quadratische Gleichungen lösen"))
        self.assertEqual(state["lessons"][0]["lesson_id"], body["lesson_id"])

    def test_a_second_click_after_ready_returns_the_same_lesson_without_a_new_call(self):
        conversation_id = self.conversation()
        with FakeAI() as ai:
            first = self.lesson(conversation_id)
            second = self.lesson(conversation_id)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.get_json()["url"], second.get_json()["url"])
        self.assertEqual(len(ai.lesson_calls()), 1)

    def test_a_new_reply_makes_a_new_lesson_possible(self):
        conversation_id = self.conversation()
        with FakeAI(chat="Weiter geht's.\nTOPIC: Mathematics | Quadratische Gleichungen") as ai:
            first = self.lesson(conversation_id)
            sent = self.client.post(f"/api/assistant/conversations/{conversation_id}/messages",
                                    json={"message": "Und wie geht die pq-Formel genau?"})
            self.assertEqual(sent.status_code, 201)
            self.assertEqual(sent.get_json()["conversation"]["continue_learning"]["status"], "none")
            second = self.lesson(conversation_id)
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.get_json()["url"], second.get_json()["url"])
        self.assertEqual(len(ai.lesson_calls()), 2)
        self.assertIn("Und wie geht die pq-Formel genau?", ai.lesson_calls()[1]["input"])

    def test_a_ready_row_whose_lesson_was_deleted_builds_again(self):
        conversation_id = self.conversation()
        with FakeAI():
            first = self.lesson(conversation_id).get_json()
        with application.app.app_context():
            lesson = db.session.get(application.Lesson, first["lesson_id"])
            db.session.delete(lesson)
            db.session.commit()
        with FakeAI():
            again = self.lesson(conversation_id)
        self.assertEqual(again.status_code, 201)
        self.assertEqual(self.lesson_rows(conversation_id)[0].attempts, 2)

    def test_deleting_the_conversation_keeps_the_lesson(self):
        conversation_id = self.conversation()
        with FakeAI():
            body = self.lesson(conversation_id).get_json()
        self.assertEqual(self.client.delete(f"/api/assistant/conversations/{conversation_id}").status_code, 200)
        self.assertEqual(self.lesson_rows(conversation_id), [])
        with application.app.app_context():
            self.assertIsNotNone(db.session.get(application.Lesson, body["lesson_id"]))


class DuplicateClickTests(ContinueLearningTestCase):
    def test_two_clicks_at_once_build_one_lesson_and_count_one(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)
        conversation_id = self.conversation()
        second_tab = self.login("alice")
        barrier = threading.Barrier(2)

        def click(client):
            barrier.wait(timeout=10)
            return self.lesson(conversation_id, client=client)

        with FakeAI(hold=True) as fake:
            clicks = {"first": InThread(lambda: click(self.client)).start(),
                      "second": InThread(lambda: click(second_tab)).start()}
            self.assertTrue(fake.started.wait(15), "no lesson call started")
            deadline = time.monotonic() + 15
            while not any(item.done for item in clicks.values()) and time.monotonic() < deadline:
                time.sleep(0.02)
            early_name = next(name for name, item in clicks.items() if item.done)
            early = clicks[early_name].join(self)
            self.assertEqual(early.status_code, 202, early.get_data(as_text=True))
            self.assertEqual(early.get_json(), {"ok": True, "status": "pending"})
            self.assertEqual(early.headers.get("Retry-After"), "3")
            polled = self.status(conversation_id).get_json()["continue_learning"]
            self.assertEqual(polled["status"], "pending")
            fake.proceed.set()
            late = clicks["second" if early_name == "first" else "first"].join(self)
        self.assertEqual(late.status_code, 201, late.get_data(as_text=True))
        early_client = self.client if early_name == "first" else second_tab
        again = self.lesson(conversation_id, client=early_client)
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.get_json()["url"], late.get_json()["url"])
        self.assertEqual(len(fake.lesson_calls()), 1)
        self.assertEqual(self.committed_lessons(), 1)
        with application.app.app_context():
            self.assertEqual(db.session.query(application.Lesson).count(), 1)

    def test_a_stale_pending_build_may_be_retried(self):
        conversation_id = self.conversation()
        with FakeAI(hold=True) as fake:
            fake.proceed.set()
            source_id = self.client.get(f"/api/assistant/conversations/{conversation_id}").get_json()[
                "conversation"]["continue_learning"]["source_message_id"]
            with application.app.app_context():
                db.session.add(application.ConversationLesson(
                    user_id=self.user_id(), conversation_id=conversation_id, source_message_id=source_id,
                    status="pending", updated_at=application.utcnow() - timedelta(minutes=10)))
                db.session.commit()
            polled = self.status(conversation_id).get_json()["continue_learning"]
            self.assertEqual((polled["status"], polled["failure_reason"]), ("failed", "interrupted"))
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(self.lesson_rows(conversation_id)[0].attempts, 2)


class FailureTests(ContinueLearningTestCase):
    def test_a_failure_keeps_the_conversation_releases_the_lesson_and_can_be_retried(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)
        conversation_id = self.conversation()
        before = self.messages(conversation_id)
        with FakeAI(ai_service.AIProviderError("provider_timeout", "timeout"), lesson_payload()) as ai:
            failed = self.lesson(conversation_id)
            self.assertEqual(failed.status_code, 504)
            self.assertEqual(failed.get_json()["code"], "ai_timeout")
            row = self.lesson_rows(conversation_id)[0]
            self.assertEqual((row.status, row.error_category), ("failed", "provider_timeout"))
            self.assertEqual(self.lesson_allowance_used(), 0)
            self.assertEqual(self.messages(conversation_id), before)
            state = self.status(conversation_id).get_json()["continue_learning"]
            self.assertEqual((state["status"], state["available"], state["failure_reason"]),
                             ("failed", True, "provider_timeout"))
            retried = self.lesson(conversation_id)
        self.assertEqual(retried.status_code, 201, retried.get_data(as_text=True))
        row = self.lesson_rows(conversation_id)[0]
        self.assertEqual((row.status, row.attempts, row.error_category), ("ready", 2, ""))
        self.assertEqual(len(ai.lesson_calls()), 2)
        self.assertEqual(self.lesson_allowance_used(), 1)
        self.assertEqual(self.messages(conversation_id), before)

    def test_a_failure_after_the_lesson_was_saved_keeps_it_linked_and_never_charges_twice(self):
        """The lesson is saved and counted once start_saved_practice returns; whatever fails in
        the finishing touches after that, the chat links that lesson and a retry opens it."""

        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)
        conversation_id = self.conversation()
        with FakeAI(lesson_payload(), lesson_payload()) as ai, \
                patch.object(application.lesson_plan, "teaching_order_ok", side_effect=RuntimeError("late failure")), \
                patch.object(application.lesson_context, "guard_parts", side_effect=RuntimeError("guard failed")):
            built = self.lesson(conversation_id)
            self.assertEqual(built.status_code, 201, built.get_data(as_text=True))
            again = self.lesson(conversation_id)
        self.assertEqual(again.status_code, 200, again.get_data(as_text=True))
        self.assertEqual(again.get_json()["url"], built.get_json()["url"])
        self.assertEqual(len(ai.lesson_calls()), 1, "the retry opens the saved lesson, no second build")
        row = self.lesson_rows(conversation_id)[0]
        self.assertEqual((row.status, row.lesson_id), ("ready", built.get_json()["lesson_id"]))
        self.assertTrue(row.session_id)
        self.assertEqual(self.lesson_allowance_used(), 1, "one lesson, counted once")
        with application.app.app_context():
            self.assertEqual(db.session.query(application.Lesson).count(), 1)
        state = self.status(conversation_id).get_json()["continue_learning"]
        self.assertEqual((state["status"], state["url"]), ("ready", built.get_json()["url"]))
        lesson_state = application.SESSIONS[row.session_id]
        self.assertEqual((lesson_state["session_kind"], lesson_state["teaching_first"]), ("chat_lesson", True),
                         "the chat-lesson behaviour does not depend on the guards that failed")
        self.assertNotIn("chat_evidence", lesson_state["lesson"])

    def test_unusable_output_is_a_clean_422_and_nothing_is_counted(self):
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)
        conversation_id = self.conversation()
        with FakeAI("this is not json"):
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.get_json()["code"], "invalid_ai_output")
        self.assertEqual(self.lesson_rows(conversation_id)[0].status, "failed")
        self.assertEqual(self.lesson_allowance_used(), 0)

    def test_a_lesson_without_concepts_fails_cleanly(self):
        conversation_id = self.conversation()
        with FakeAI(lesson_payload(concepts=[])):
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.lesson_rows(conversation_id)[0].error_category, "invalid_ai_output")


class AllowanceTests(ContinueLearningTestCase):
    def setUp(self):
        super().setUp()
        application.app.config.update(SUBSCRIPTION_QUOTAS_ENFORCED=True)

    def test_a_free_account_with_todays_lessons_used_is_refused_before_any_call(self):
        self.use_free_lessons(2)
        conversation_id = self.conversation()
        with FakeAI() as ai:
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 429)
        body = response.get_json()
        self.assertEqual(body["code"], "free_lessons_used")
        self.assertIn("upgrade_url", body["details"])
        self.assertEqual(ai.lesson_calls(), [])
        row = self.lesson_rows(conversation_id)[0]
        self.assertEqual((row.status, row.error_category), ("failed", "free_lessons_used"))
        state = self.status(conversation_id).get_json()["continue_learning"]
        self.assertEqual((state["status"], state["failure_reason"]), ("failed", "free_lessons_used"))

    def test_premium_is_not_limited(self):
        self.use_free_lessons(2)
        self.make_premium()
        conversation_id = self.conversation()
        with FakeAI():
            response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))

    def test_a_built_lesson_counts_one_free_lesson(self):
        conversation_id = self.conversation()
        with FakeAI():
            self.assertEqual(self.lesson(conversation_id).status_code, 201)
        self.assertEqual(self.lesson_allowance_used(), 1)
        self.assertEqual(self.committed_lessons(), 1)


class AccessTests(ContinueLearningTestCase):
    def test_another_account_gets_404(self):
        conversation_id = self.conversation()
        bob = self.register("bob")
        with FakeAI() as ai:
            self.assertEqual(self.lesson(conversation_id, client=bob).status_code, 404)
            self.assertEqual(self.status(conversation_id, client=bob).status_code, 404)
        self.assertEqual(ai.calls, [])

    def test_the_flag_off_hides_the_routes_and_the_action(self):
        conversation_id = self.conversation()
        application.app.config["FEATURE_CONTINUE_LEARNING"] = False
        self.assertEqual(self.lesson(conversation_id).status_code, 404)
        self.assertEqual(self.status(conversation_id).status_code, 404)
        state = self.client.get(f"/api/assistant/conversations/{conversation_id}").get_json()[
            "conversation"]["continue_learning"]
        self.assertEqual((state["enabled"], state["available"]), (False, False))

    def test_a_conversation_with_a_reply_in_progress_is_busy(self):
        conversation_id = self.conversation()
        with application.app.app_context():
            db.session.execute(application.update(application.Conversation).where(
                application.Conversation.id == conversation_id).values(reply_started_at=application.utcnow()))
            db.session.commit()
        response = self.lesson(conversation_id)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["code"], "conversation_busy")

    def test_anonymous_requests_are_refused(self):
        conversation_id = self.conversation()
        anonymous = application.app.test_client()
        self.assertIn(anonymous.post(f"/api/assistant/conversations/{conversation_id}/lesson").status_code, {302, 401})

    def test_the_routes_are_limited_per_account(self):
        source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
        for route in ('@app.post("/api/assistant/conversations/<conversation_id>/lesson")',
                      '@app.get("/api/assistant/conversations/<conversation_id>/lesson")'):
            limit_line = source.split(route + "\n", 1)[1].split("\n", 1)[0]
            self.assertTrue(limit_line.startswith("@limiter.limit("), limit_line)
            self.assertIn("key_func=user_rate_key", limit_line)
        self.assertIn("CONTINUE_LEARNING_PER_HOUR", source.split(
            '@app.post("/api/assistant/conversations/<conversation_id>/lesson")\n', 1)[1].split("\n", 1)[0])


class EndToEndTests(ContinueLearningTestCase):
    def test_a_chat_through_the_real_routes_becomes_a_lesson_on_its_topic(self):
        created = self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]["id"]
        with FakeAI(chat="Fast! Prüfe die Probe.\nCHIPS: Ja | Nein\nTOPIC: Mathematik | Quadratische Gleichungen") as ai:
            for text in (FIRST_QUESTION, WRONG_ANSWER):
                sent = self.client.post(f"/api/assistant/conversations/{created}/messages", json={"message": text})
                self.assertEqual(sent.status_code, 201, sent.get_data(as_text=True))
            state = sent.get_json()["conversation"]["continue_learning"]
            self.assertTrue(state["available"])
            self.assertEqual((state["subject"], state["topic"]), ("Mathematics", "Quadratische Gleichungen"))
            response = self.lesson(created)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        prompt = ai.lesson_calls()[0]["input"]
        self.assertIn("Topic: Quadratische Gleichungen", prompt)
        self.assertIn(FIRST_QUESTION, prompt)
        self.assertIn(WRONG_ANSWER, prompt)
        self.assertNotIn("CHIPS:", prompt)
        self.assertNotIn("TOPIC:", prompt.split("CONVERSATION (oldest first", 1)[1])


if __name__ == "__main__":
    unittest.main()

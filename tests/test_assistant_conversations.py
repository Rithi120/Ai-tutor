"""Conversation persistence through the real routes: titles, the send claim, search,
archive/restore, deletion, idempotent re-sends, regenerate, waiting photos, deep links.

Every AI call is patched at `app.create_response` (FakeAI below), so these run offline
while exercising the genuine routes, the database, ownership and the failure paths.
Concurrency is tested with real threads on the SQLite test database: the provider call
of one request is held open on an Event while the other requests run.
"""

import io
import itertools
import json
import os
import re
import tempfile
import threading
import time
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_assistant_conversations_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.translations import catalog  # noqa: E402

db = application.db
ROOT = Path(__file__).resolve().parents[1]

READING = {
    "images": [{"index": 1, "kind": "worksheet", "rotation": 0, "cut_off": False, "legibility": "clear"}],
    "blocks": [
        {"image": 1, "type": "printed_text", "text": "Löse die Gleichung 3x + 5 = 20.", "author": "printed",
         "confidence": 0.97},
    ],
    "diagrams": [],
    "uncertain": [],
    "clarification_question": "",
    "summary": "Ein Arbeitsblatt zu linearen Gleichungen. Drei Aufgaben.",
    "language": "de",
}


class Usage:
    input_tokens = 120
    output_tokens = 80
    total_tokens = 200


class Response:
    def __init__(self, text: str, model: str = "test-model"):
        self.output_text = text
        self.model = model
        self.usage = Usage()


class FakeAI:
    """Answers app.create_response by task type and records every call (thread-safe).

    With `hold=True` the assistant reply waits until `proceed` is set, and `started` is
    set as it begins - the request is then "in flight" for as long as the test wants.
    """

    def __init__(self, *, reply: str = "Here is a clear answer.", replies: list[str] | None = None,
                 reading: Any = READING, errors: dict[str, Exception] | None = None, hold: bool = False):
        self.reply, self.replies, self.reading = reply, list(replies or []), reading
        self.errors = dict(errors or {})
        self.hold = hold
        self.started, self.proceed = threading.Event(), threading.Event()
        self.calls: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._patch = patch.object(application, "create_response", side_effect=self)

    def __call__(self, **kwargs):
        with self._lock:
            self.calls.append(kwargs)
        task = kwargs["task_type"]
        if task in self.errors:
            raise self.errors[task]
        if task == "image_understanding":
            return Response(json.dumps(self.reading, ensure_ascii=False))
        if task != "assistant_chat":
            raise AssertionError(f"unexpected AI task {task}")
        if self.hold:
            self.started.set()
            if not self.proceed.wait(timeout=15):
                raise AssertionError("the test never released the held reply")
        with self._lock:
            text = self.replies.pop(0) if self.replies else self.reply
        return Response(text)

    def tasks(self):
        return [call["task_type"] for call in self.calls]

    def chat_calls(self):
        return [call for call in self.calls if call["task_type"] == "assistant_chat"]

    def __enter__(self):
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self.proceed.set()
        self._patch.stop()


class InThread:
    """Run one request in a thread and keep its response (or the exception it raised)."""

    def __init__(self, call):
        self.response = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, args=(call,), daemon=True)

    def _run(self, call):
        try:
            self.response = call()
        except BaseException as error:  # noqa: BLE001 - reported by the test
            self.error = error

    def start(self):
        self.thread.start()
        return self

    def join(self, test):
        self.thread.join(timeout=30)
        test.assertFalse(self.thread.is_alive(), "request thread did not finish")
        if self.error is not None:
            raise self.error
        return self.response


def png(colour=(255, 255, 255), size=(480, 640)):
    output = io.BytesIO()
    Image.new("RGB", size, colour).save(output, format="PNG")
    return output.getvalue()


class ConversationTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        self.cache_dir = tempfile.mkdtemp(prefix="learnova-wp1-cache-")
        application.app.config.update(
            TESTING=True, FEATURE_ASSISTANT_CHAT=True, FEATURE_CHAT_IMAGES=True, VISION_LOCAL_ORIENTATION=False,
            ASSISTANT_MODEL="openai/gpt-oss-20b", ASSISTANT_DEEP_MODEL="openai/gpt-oss-120b",
            AI_CACHE_DIR=self.cache_dir)
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")
        self.colour = 0

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    # -- helpers --------------------------------------------------------------------
    def register(self, name):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery"})
        return client

    def login(self, name):
        client = application.app.test_client()
        response = client.post("/login", data={"identifier": name, "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 302)
        return client

    def conversation(self, client=None, **body):
        response = (client or self.client).post("/api/assistant/conversations", json=body)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["conversation"]["id"]

    def send(self, conversation_id, message="A question", client=None, **body):
        return (client or self.client).post(
            f"/api/assistant/conversations/{conversation_id}/messages", json={"message": message, **body})

    def opened(self, conversation_id, client=None):
        response = (client or self.client).get(f"/api/assistant/conversations/{conversation_id}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()["conversation"]

    def listing(self, client=None, **params):
        response = (client or self.client).get("/api/assistant/conversations", query_string=params)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def upload(self, client=None, purpose="chat"):
        self.colour += 1
        response = (client or self.client).post("/api/chat-attachments", data={
            "file": (io.BytesIO(png((self.colour * 7 % 255, 80, 160))), "photo.png"), "purpose": purpose},
            content_type="multipart/form-data")
        self.assertIn(response.status_code, (200, 201), response.get_data(as_text=True))
        return response.get_json()["attachment"]["id"]

    def row(self, model, identifier):
        with application.app.app_context():
            found = db.session.get(model, identifier)
            if found is not None:
                db.session.expunge(found)
            return found

    def count(self, model, *criteria):
        with application.app.app_context():
            return int(db.session.scalar(db.select(db.func.count()).select_from(model).where(*criteria)) or 0)

    def roles(self, conversation_id):
        return [item["role"] for item in self.opened(conversation_id)["messages"]]

    def set_columns(self, conversation_id, **values):
        with application.app.app_context():
            db.session.execute(application.update(application.Conversation).where(
                application.Conversation.id == conversation_id).values(**values))
            db.session.commit()


# ---------------------------------------------------------------------------- titles
class TitleTests(ConversationTestCase):
    def test_a_rename_before_the_first_message_survives_it(self):
        conversation_id = self.conversation()
        self.client.patch(f"/api/assistant/conversations/{conversation_id}", json={"title": "My algebra notes"})
        with FakeAI():
            self.assertEqual(self.send(conversation_id, "hi").status_code, 201)
            self.send(conversation_id, "Explain photosynthesis. I keep forgetting it.")
        opened = self.opened(conversation_id)
        self.assertEqual((opened["title"], opened["title_source"]), ("My algebra notes", "user"))

    def test_a_title_given_at_creation_survives_a_photo_only_send(self):
        conversation_id = self.conversation(title="Chosen at creation")
        self.assertEqual(self.opened(conversation_id)["title_source"], "user")
        with FakeAI():
            sent = self.send(conversation_id, "", attachments=[self.upload()])
        self.assertEqual(sent.status_code, 201, sent.get_data(as_text=True))
        self.assertEqual(sent.get_json()["conversation"]["title"], "Chosen at creation")

    def test_a_greeting_does_not_name_the_thread_and_the_next_real_question_does(self):
        conversation_id = self.conversation()
        with FakeAI():
            first = self.send(conversation_id, "hi").get_json()["conversation"]
            second = self.send(conversation_id, "Explain photosynthesis").get_json()["conversation"]
            self.send(conversation_id, "And what about respiration?")
        self.assertEqual((first["title"], first["title_source"]), ("New conversation", "default"))
        self.assertEqual((second["title"], second["title_source"]), ("Explain photosynthesis", "auto"))
        self.assertEqual(self.opened(conversation_id)["title"], "Explain photosynthesis")

    def test_a_generic_request_is_replaced_by_the_first_telling_message(self):
        conversation_id = self.conversation()
        with FakeAI():
            weak = self.send(conversation_id, "Can you help me?").get_json()["conversation"]
            good = self.send(conversation_id, "Quadratic equations with fractions").get_json()["conversation"]
        self.assertEqual((weak["title"], weak["title_source"]), ("Can you help me?", "weak"))
        self.assertEqual((good["title"], good["title_source"]), ("Quadratic equations with fractions", "auto"))

    def test_a_photo_only_first_message_is_named_after_the_reading_summary(self):
        conversation_id = self.conversation()
        with FakeAI():
            sent = self.send(conversation_id, "", attachments=[self.upload()])
        self.assertEqual(sent.get_json()["message"]["content"], "Please look at my photo.")
        conversation = sent.get_json()["conversation"]
        self.assertEqual((conversation["title"], conversation["title_source"]),
                         ("Ein Arbeitsblatt zu linearen Gleichungen.", "auto"))

    def test_rename_cleans_the_title_and_refuses_an_empty_one(self):
        conversation_id = self.conversation()
        renamed = self.client.patch(f"/api/assistant/conversations/{conversation_id}",
                                    json={"title": "  Brüche\n\n kürzen \t" + "x" * 200})
        title = renamed.get_json()["conversation"]["title"]
        self.assertTrue(title.startswith("Brüche kürzen x"))
        self.assertLessEqual(len(title), 120)
        for empty in ("", "   ", "\n\t", None):
            response = self.client.patch(f"/api/assistant/conversations/{conversation_id}", json={"title": empty})
            self.assertEqual((response.status_code, response.get_json()["code"]), (400, "title_required"))

    def test_creating_with_the_placeholder_title_is_not_a_chosen_title(self):
        conversation_id = self.conversation(title="New conversation")
        self.assertEqual(self.opened(conversation_id)["title_source"], "default")


# ---------------------------------------------------------------------------- the claim
class ClaimTests(ConversationTestCase):
    def test_two_sends_to_one_conversation_one_answers_and_the_other_is_told_to_wait(self):
        conversation_id = self.conversation()
        second_tab = self.login("alice")
        with FakeAI(hold=True) as fake:
            first = InThread(lambda: self.send(conversation_id, "First question")).start()
            self.assertTrue(fake.started.wait(10), "the first reply never started")
            busy = self.send(conversation_id, "Second question", client=second_tab)
            listed = self.listing(second_tab)["conversations"][0]
            fake.proceed.set()
            answered = first.join(self)
        self.assertEqual(answered.status_code, 201, answered.get_data(as_text=True))
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.get_json()["code"], "conversation_busy")
        self.assertEqual(busy.get_json()["details"], {"retry_after": 5})
        self.assertEqual(busy.headers.get("Retry-After"), "5")
        self.assertTrue(listed["busy"], "the list shows the reply in progress")
        self.assertEqual(len(fake.chat_calls()), 1)
        self.assertEqual(self.roles(conversation_id), ["user", "assistant"])
        row = self.row(application.Conversation, conversation_id)
        self.assertEqual((row.message_count, row.input_tokens, row.output_tokens), (2, 120, 80))
        self.assertIsNone(row.reply_started_at)
        self.assertFalse(self.opened(conversation_id)["busy"])

    def test_different_conversations_answer_in_parallel(self):
        first_id, second_id = self.conversation(), self.conversation()
        second_tab = self.login("alice")
        with FakeAI(hold=True) as fake:
            first = InThread(lambda: self.send(first_id, "One")).start()
            self.assertTrue(fake.started.wait(10))
            second = InThread(lambda: self.send(second_id, "Two", client=second_tab)).start()
            deadline = time.monotonic() + 10
            while len(fake.chat_calls()) < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertEqual(len(fake.chat_calls()), 2, "both replies were in flight at once")
            fake.proceed.set()
            responses = [first.join(self), second.join(self)]
        self.assertEqual([item.status_code for item in responses], [201, 201])
        for conversation_id in (first_id, second_id):
            self.assertEqual(self.roles(conversation_id), ["user", "assistant"])
            self.assertEqual(self.row(application.Conversation, conversation_id).message_count, 2)

    def test_an_unrelated_write_is_not_blocked_while_a_reply_is_being_written(self):
        """repro_lock.py: the SQLite write lock used to be held for the whole provider call."""

        conversation_id = self.conversation()
        bob = self.register("bob")
        with FakeAI(hold=True) as fake:
            alice = InThread(lambda: self.send(conversation_id, "A slow question")).start()
            self.assertTrue(fake.started.wait(10))
            started = time.perf_counter()
            created = bob.post("/api/assistant/conversations", json={})
            elapsed = time.perf_counter() - started
            renamed = self.login("alice").patch(f"/api/assistant/conversations/{conversation_id}",
                                                json={"title": "Renamed while answering"})
            fake.proceed.set()
            answered = alice.join(self)
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        self.assertLess(elapsed, 2.0)
        self.assertEqual(renamed.status_code, 200)
        self.assertEqual(answered.status_code, 201)
        opened = self.opened(conversation_id)
        self.assertEqual((opened["title"], opened["title_source"]), ("Renamed while answering", "user"),
                         "storing the reply does not write back a stale title")
        self.assertEqual(opened["message_count"], 2)

    def test_a_failed_reply_releases_the_claim(self):
        conversation_id = self.conversation()
        with FakeAI(errors={"assistant_chat": ai_service.AIProviderError("provider_timeout", "timed out")}):
            failed = self.send(conversation_id, "A question that fails")
        self.assertEqual(failed.status_code, 201)
        self.assertTrue(failed.get_json()["reply"]["error"])
        self.assertIsNone(self.row(application.Conversation, conversation_id).reply_started_at)
        with FakeAI():
            self.assertEqual(self.send(conversation_id, "Again").status_code, 201)
        self.assertEqual(self.row(application.Conversation, conversation_id).message_count, 3)

    def test_a_failed_reading_releases_the_claim_and_stores_nothing(self):
        conversation_id, photo = self.conversation(), self.upload()
        with FakeAI(errors={"image_understanding": ai_service.AIProviderError("provider_timeout", "t")}):
            failed = self.send(conversation_id, "Was steht da?", attachments=[photo])
        self.assertGreaterEqual(failed.status_code, 400)
        self.assertEqual(self.count(application.ConversationMessage), 0)
        row = self.row(application.Conversation, conversation_id)
        self.assertEqual((row.message_count, row.reply_started_at), (0, None))
        with FakeAI() as fake:
            retried = self.send(conversation_id, "Was steht da?", attachments=[photo])
        self.assertEqual(retried.status_code, 201, retried.get_data(as_text=True))
        self.assertEqual(fake.tasks(), ["image_understanding", "assistant_chat"])

    def test_the_reading_carries_the_conversation_as_its_session(self):
        conversation_id = self.conversation()
        with FakeAI() as fake:
            self.send(conversation_id, "Was steht da?", attachments=[self.upload()])
        reading = next(call for call in fake.calls if call["task_type"] == "image_understanding")
        self.assertEqual(reading["session_scope"], conversation_id)
        self.assertEqual(fake.chat_calls()[0]["session_scope"], conversation_id)

    def test_a_stale_claim_is_taken_over_and_a_fresh_one_is_respected(self):
        conversation_id = self.conversation()
        now = application.utcnow()
        self.set_columns(conversation_id, reply_started_at=now)
        self.assertTrue(self.opened(conversation_id)["busy"])
        with FakeAI():
            self.assertEqual(self.send(conversation_id, "Blocked").status_code, 409)
        self.set_columns(conversation_id, reply_started_at=now - timedelta(seconds=181))
        self.assertFalse(self.opened(conversation_id)["busy"])
        with FakeAI():
            self.assertEqual(self.send(conversation_id, "Taken over").status_code, 201)

    def test_a_photo_already_sent_elsewhere_is_never_attached_twice(self):
        """The race the checks before the claim cannot see: the attach itself is conditional."""

        photo = self.upload()
        first, second = self.conversation(), self.conversation()
        with FakeAI():
            self.assertEqual(self.send(first, "Eins", attachments=[photo]).status_code, 201)
        with application.app.app_context():
            sent_row = db.session.get(application.ChatAttachment, photo)
            sent_message = sent_row.message_id
        # As if the second send had passed its checks just before the first one attached it.
        with patch.object(application, "claim_attachments", side_effect=lambda ids, purpose: [
                db.session.get(application.ChatAttachment, item) for item in ids]), FakeAI() as fake:
            response = self.send(second, "Zwei", attachments=[photo])
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "attachments_invalid"))
        self.assertEqual(fake.chat_calls(), [])
        self.assertEqual(self.row(application.ChatAttachment, photo).message_id, sent_message)
        row = self.row(application.Conversation, second)
        self.assertEqual((row.message_count, row.reply_started_at), (0, None))
        self.assertEqual(self.count(application.ConversationMessage,
                                    application.ConversationMessage.conversation_id == second), 0)

    def test_a_full_conversation_releases_the_claim(self):
        application.app.config["ASSISTANT_MAX_MESSAGES_PER_CONVERSATION"] = 2
        conversation_id = self.conversation()
        with FakeAI():
            self.send(conversation_id, "First")
            full = self.send(conversation_id, "Second")
        self.assertEqual((full.status_code, full.get_json()["code"]), (409, "conversation_full"))
        self.assertIsNone(self.row(application.Conversation, conversation_id).reply_started_at)

    def test_deleting_during_a_reply_discards_the_reply_and_leaves_no_orphans(self):
        conversation_id = self.conversation()
        second_tab = self.login("alice")
        with FakeAI(hold=True) as fake:
            sending = InThread(lambda: self.send(conversation_id, "Something to forget")).start()
            self.assertTrue(fake.started.wait(10))
            deleted = second_tab.delete(f"/api/assistant/conversations/{conversation_id}")
            fake.proceed.set()
            answered = sending.join(self)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual((answered.status_code, answered.get_json()["code"]), (404, "conversation_not_found"))
        self.assertEqual(self.count(application.ConversationMessage), 0)
        self.assertEqual(self.count(application.Conversation), 0)


# ---------------------------------------------------------------------------- search, groups, pages
class ListAndSearchTests(ConversationTestCase):
    def converse(self, text, client=None, reply="Here is a clear answer."):
        conversation_id = self.conversation(client)
        with FakeAI(reply=reply):
            response = self.send(conversation_id, text, client=client)
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return conversation_id

    def found(self, query, **params):
        return [item["id"] for item in self.listing(q=query, **params)["conversations"]]

    def test_a_title_hit_and_a_message_hit_with_its_snippet(self):
        titled = self.conversation(title="Quadratic equations")
        body = self.converse("Tell me how plants use sunlight. " + "Filler words go here. " * 8
                             + "Photosynthesis converts light energy into chemical energy.")
        data = self.listing(q="light energy")
        self.assertEqual(data["query"], "light energy")
        self.assertEqual([item["id"] for item in data["conversations"]], [body])
        self.assertIn("light energy", data["conversations"][0]["snippet"])
        self.assertTrue(data["conversations"][0]["snippet"].startswith("…"))
        self.assertEqual(self.found("QUADRATIC"), [titled])
        self.assertEqual(self.listing(q="quadratic")["conversations"][0]["snippet"], "",
                         "a title hit has no message snippet")
        self.assertNotIn("snippet", self.listing()["conversations"][0], "no search, no snippet")

    def test_the_reply_text_is_searched_but_failed_turns_are_not(self):
        answered = self.converse("Zebra question", reply="Zebras are striped equids.")
        conversation_id = self.conversation()
        with FakeAI(errors={"assistant_chat": ai_service.AIProviderError("provider_timeout", "timed out")}):
            self.send(conversation_id, "Giraffe question")
        self.assertEqual(self.found("equids"), [answered])
        self.assertEqual(self.found("Generation timed out"), [], "the stored failure text is not content")
        self.assertEqual(self.found("Giraffe"), [conversation_id])

    def test_like_wildcards_and_escape_characters_match_literally(self):
        cases = {"0%": ("Save 50% today", "Save 500 today"), "a_b": ("call a_b now", "call axb now"),
                 "1/2": ("add 1/2 cup", "add 12 cup"), "C:\\t": ("open C:\\temp first", "open C:temp first")}
        for query, (hit, miss) in cases.items():
            with self.subTest(query=query):
                wanted, other = self.converse(hit), self.converse(miss)
                found = self.found(query)
                self.assertIn(wanted, found)
                self.assertNotIn(other, found)

    def test_another_accounts_identical_text_is_never_found(self):
        mine = self.converse("Mitochondria are the powerhouse")
        bob = self.register("bob")
        theirs = self.converse("Mitochondria are the powerhouse", client=bob)
        self.assertEqual(self.found("powerhouse"), [mine])
        self.assertEqual([item["id"] for item in self.listing(bob, q="powerhouse")["conversations"]], [theirs])

    def test_a_query_shorter_than_two_characters_is_ignored(self):
        first, second = self.converse("Alpha"), self.converse("Beta")
        data = self.listing(q="a")
        self.assertEqual(data["query"], "")
        self.assertEqual({item["id"] for item in data["conversations"]}, {first, second})

    def test_archived_threads_are_listed_only_in_the_archive_view(self):
        kept, archived = self.converse("Cells recent"), self.converse("Cells archived")
        self.client.patch(f"/api/assistant/conversations/{archived}", json={"archived": True})
        self.assertEqual(self.found("Cells"), [kept])
        archive = self.listing(view="archived", q="Cells")
        self.assertEqual(archive["view"], "archived")
        self.assertEqual([item["id"] for item in archive["conversations"]], [archived])
        self.assertEqual([item["id"] for item in self.listing(view="archived")["conversations"]], [archived])

    def test_pages_follow_the_cursor_without_gaps_or_repeats(self):
        base = application.utcnow() - timedelta(days=1)
        identifiers = [self.conversation() for _ in range(5)]
        # Two share one timestamp: the id breaks the tie, so the order is total.
        moments = [base, base - timedelta(minutes=1), base - timedelta(minutes=1), base - timedelta(minutes=2),
                   base - timedelta(minutes=3)]
        for identifier, moment in zip(identifiers, moments):
            self.set_columns(identifier, last_message_at=moment)
        expected = [item["id"] for item in self.listing(limit=100)["conversations"]]
        self.assertEqual(set(expected), set(identifiers))
        seen, cursor, pages = [], None, 0
        while True:
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            data = self.listing(**params)
            seen += [item["id"] for item in data["conversations"]]
            pages += 1
            if pages == 1:
                self.conversation()   # a new thread arrives between pages: later pages do not shift
            cursor = data["next_cursor"]
            if not cursor:
                break
        self.assertEqual(seen, expected)
        self.assertEqual(pages, 3)
        self.assertEqual(self.listing(cursor="garbage")["conversations"][0]["id"], self.listing()["conversations"][0]["id"])
        self.assertEqual(len(self.listing(limit=0)["conversations"]), 1, "limit is bounded to at least one")

    def test_each_row_carries_its_date_group_in_the_requested_zone(self):
        now = application.utcnow()
        ages = {"today": timedelta(0), "yesterday": timedelta(days=1), "previous_7_days": timedelta(days=3),
                "older": timedelta(days=30)}
        made = {}
        for group, age in ages.items():
            identifier = self.conversation()
            self.set_columns(identifier, last_message_at=now - age)
            made[identifier] = group
        rows = self.listing(tz="UTC")["conversations"]
        self.assertEqual({item["id"]: item["group"] for item in rows}, made)
        self.assertEqual([item["group"] for item in rows], ["today", "yesterday", "previous_7_days", "older"])
        self.assertTrue(all("group" in item for item in self.listing(tz="Not/AZone")["conversations"]))

    def test_rows_carry_the_new_fields(self):
        conversation_id = self.conversation()
        row = self.listing()["conversations"][0]
        self.assertEqual(row["id"], conversation_id)
        for key, value in (("title_source", "default"), ("archived_at", None), ("busy", False), ("subject", ""),
                           ("topic", "")):
            self.assertEqual(row[key], value, key)
        data = self.listing()
        self.assertEqual((data["view"], data["query"], data["next_cursor"]), ("recent", "", None))


# ---------------------------------------------------------------------------- archive, restore, drafts
class ArchiveTests(ConversationTestCase):
    def archive(self, conversation_id, value=True):
        response = self.client.patch(f"/api/assistant/conversations/{conversation_id}", json={"archived": value})
        self.assertEqual(response.status_code, 200)
        return response.get_json()["conversation"]

    def test_archive_sets_the_time_once_and_restore_clears_it(self):
        conversation_id = self.conversation()
        archived = self.archive(conversation_id)
        self.assertTrue(archived["archived"])
        self.assertIsNotNone(archived["archived_at"])
        self.assertEqual(self.archive(conversation_id)["archived_at"], archived["archived_at"],
                         "archiving twice keeps the first time")
        restored = self.archive(conversation_id, False)
        self.assertEqual((restored["archived"], restored["archived_at"]), (False, None))
        self.assertEqual([item["id"] for item in self.listing()["conversations"]], [conversation_id])

    def test_archiving_keeps_every_message_and_photo(self):
        conversation_id = self.conversation()
        with FakeAI():
            self.send(conversation_id, "Keep this", attachments=[self.upload()])
        self.archive(conversation_id)
        opened = self.opened(conversation_id)
        self.assertEqual([item["role"] for item in opened["messages"]], ["user", "assistant"])
        self.assertTrue(opened["messages"][0]["attachments"][0]["available"])

    def test_sending_into_an_archived_conversation_restores_it(self):
        conversation_id = self.conversation()
        self.archive(conversation_id)
        self.assertEqual(self.listing()["conversations"], [])
        with FakeAI():
            sent = self.send(conversation_id, "Back to this")
        self.assertEqual(sent.status_code, 201)
        conversation = sent.get_json()["conversation"]
        self.assertEqual((conversation["archived"], conversation["archived_at"]), (False, None))
        self.assertEqual([item["id"] for item in self.listing()["conversations"]], [conversation_id])

    def test_the_legacy_archived_flag_still_lists_both(self):
        recent, archived = self.conversation(), self.conversation()
        self.archive(archived)
        data = self.listing(archived="1")
        self.assertEqual(data["view"], "all")
        self.assertEqual({item["id"] for item in data["conversations"]}, {recent, archived})

    def test_the_archive_is_ordered_by_when_it_was_archived(self):
        first, second = self.conversation(), self.conversation()
        self.archive(second)
        self.archive(first)
        self.assertEqual([item["id"] for item in self.listing(view="archived")["conversations"]], [first, second])

    def test_untouched_drafts_older_than_a_day_are_removed_on_the_next_create(self):
        stale, named, used, fresh = (self.conversation(), self.conversation(title="Named draft"),
                                     self.conversation(), self.conversation())
        with FakeAI():
            self.send(used, "A real question")
        old = application.utcnow() - timedelta(days=2)
        for identifier in (stale, named, used):
            self.set_columns(identifier, created_at=old)
        bob = self.register("bob")
        bob_stale = self.conversation(bob)
        self.set_columns(bob_stale, created_at=old)
        self.conversation()
        remaining = {item["id"] for item in self.listing(limit=100)["conversations"]}
        self.assertNotIn(stale, remaining)
        self.assertTrue({named, used, fresh} <= remaining)
        self.assertIsNotNone(self.row(application.Conversation, bob_stale), "only the caller's drafts")


# ---------------------------------------------------------------------------- delete
class DeleteTests(ConversationTestCase):
    def cache_file(self, session_scope, task_type="image_understanding", name="a"):
        with application.app.app_context():
            key = (name * 64)[:64]
            ai_service._write_cache(key, ai_service.GatewayResponse(
                output_text="{}", model="m", usage=ai_service.GatewayUsage(1, 1, 2)), {
                "task_type": task_type,
                "session_reference": ai_service._anonymous_reference(session_scope, label="session")})
            return ai_service._cache_path(key)

    def test_delete_removes_messages_photos_and_lesson_links_and_purges_cached_readings(self):
        conversation_id = self.conversation()
        with FakeAI():
            self.send(conversation_id, "With a photo", attachments=[self.upload()])
        with application.app.app_context():
            user_id = db.session.scalar(db.select(application.User.id).where(application.User.username == "alice"))
            message_id = db.session.scalar(db.select(application.ConversationMessage.id).where(
                application.ConversationMessage.role == "assistant"))
            db.session.add(application.ConversationLesson(
                user_id=user_id, conversation_id=conversation_id, source_message_id=message_id))
            db.session.commit()
        own = self.cache_file(conversation_id, name="a")
        other_session = self.cache_file("another-conversation", name="b")
        own_chat = self.cache_file(conversation_id, task_type="assistant_chat", name="c")
        waiting = self.upload()   # an unsent photo of the same account is not part of the thread
        with patch.object(ai_service, "purge_cached", wraps=ai_service.purge_cached) as purge:
            deleted = self.client.delete(f"/api/assistant/conversations/{conversation_id}")
        self.assertEqual((deleted.status_code, deleted.get_json()), (200, {"ok": True, "deleted": True}))
        purge.assert_called_once_with(session_scope=conversation_id)
        for model in (application.Conversation, application.ConversationMessage, application.ConversationLesson):
            self.assertEqual(self.count(model), 0, model.__name__)
        self.assertEqual(self.count(application.ChatAttachment), 1)
        self.assertIsNotNone(self.row(application.ChatAttachment, waiting))
        self.assertFalse(own.exists(), "the deleted conversation's reading is purged")
        self.assertTrue(other_session.exists(), "another conversation's reading is kept")
        self.assertTrue(own_chat.exists(), "only deterministic results are purged")

    def test_purge_never_raises_and_ignores_an_empty_scope(self):
        with application.app.app_context():
            self.assertEqual(ai_service.purge_cached(session_scope=""), 0)
            application.app.config["AI_CACHE_DIR"] = str(Path(self.cache_dir) / "missing")
            self.assertEqual(ai_service.purge_cached(session_scope="x"), 0)

    def test_the_gateway_writes_the_session_reference_into_live_cache_entries(self):
        changed = {"RUN_LIVE_AI_TEST": True, "AI_USAGE_PATH": "", "ALLOW_LIVE_AI": True, "AI_MODE": "live"}
        application.app.config.update(changed)

        class Reply:
            output_text = json.dumps({"blocks": [{"type": "printed_text", "content": "Pflicht", "confidence": 0.9,
                                                  "bbox": [0.1, 0.1, 0.5, 0.2]}],
                                      "detected_page_number": "", "warning": ""})
            model = "m"

            class usage:  # noqa: N801
                input_tokens = 10
                output_tokens = 10
                total_tokens = 20

        with application.app.test_request_context("/"):
            with patch.object(ai_service, "_provider_response", return_value=Reply()), \
                    patch.object(ai_service, "_assert_usage_limits"):
                ai_service._RECENT_RESULTS.clear()
                ai_service.create_response(
                    task_type="ocr_document_recognition", language="German", model="m",
                    instructions="read the page", input="a page", max_output_tokens=100,
                    private_scope=None, session_scope="conversation-1")
            files = list(Path(self.cache_dir).glob("*/*.json"))
            self.assertEqual(len(files), 1)
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["session_reference"],
                             ai_service._anonymous_reference("conversation-1", label="session"))
            self.assertNotIn("conversation-1", files[0].read_text(encoding="utf-8"), "only the hashed reference")
            self.assertEqual(ai_service.purge_cached(session_scope="conversation-2"), 0)
            self.assertEqual(ai_service.purge_cached(session_scope="conversation-1"), 1)
            self.assertEqual(list(Path(self.cache_dir).glob("*/*.json")), [])

    def test_another_account_cannot_delete(self):
        conversation_id = self.conversation()
        bob = self.register("bob")
        self.assertEqual(bob.delete(f"/api/assistant/conversations/{conversation_id}").status_code, 404)
        self.assertIsNotNone(self.row(application.Conversation, conversation_id))


# ---------------------------------------------------------------------------- re-sends and regenerate
class ReplayTests(ConversationTestCase):
    def test_the_same_client_message_id_is_answered_from_what_was_stored(self):
        conversation_id = self.conversation()
        with FakeAI() as fake:
            first = self.send(conversation_id, "Once only", client_message_id="c0ffee-42")
            again = self.send(conversation_id, "Once only", client_message_id="c0ffee-42")
        self.assertEqual((first.status_code, again.status_code), (201, 200))
        self.assertTrue(again.get_json()["replayed"])
        self.assertEqual(again.get_json()["message"]["id"], first.get_json()["message"]["id"])
        self.assertEqual(again.get_json()["reply"]["id"], first.get_json()["reply"]["id"])
        self.assertEqual(first.get_json()["message"]["client_message_id"], "c0ffee-42")
        self.assertEqual(len(fake.chat_calls()), 1)
        self.assertEqual(self.roles(conversation_id), ["user", "assistant"])
        self.assertEqual(self.row(application.Conversation, conversation_id).message_count, 2)

    def test_a_replayed_photo_message_is_not_refused_for_its_sent_photo(self):
        conversation_id, photo = self.conversation(), self.upload()
        with FakeAI():
            first = self.send(conversation_id, "", attachments=[photo], client_message_id="photo-1")
            again = self.send(conversation_id, "", attachments=[photo], client_message_id="photo-1")
        self.assertEqual((first.status_code, again.status_code), (201, 200), again.get_data(as_text=True))
        self.assertEqual(again.get_json()["message"]["attachments"][0]["id"], photo)

    def test_a_re_send_while_its_reply_is_being_written_is_told_to_wait(self):
        conversation_id = self.conversation()
        with FakeAI(hold=True) as fake:
            sending = InThread(lambda: self.send(conversation_id, "Slow", client_message_id="slow-1")).start()
            self.assertTrue(fake.started.wait(10))
            again = self.send(conversation_id, "Slow", client=self.login("alice"), client_message_id="slow-1")
            fake.proceed.set()
            self.assertEqual(sending.join(self).status_code, 201)
        self.assertEqual((again.status_code, again.get_json()["code"]), (409, "conversation_busy"))

    def test_a_stored_question_whose_reply_was_lost_is_answered_on_the_re_send(self):
        conversation_id = self.conversation()
        with application.app.app_context():
            db.session.add(application.ConversationMessage(
                conversation_id=conversation_id, role="user", content="Lost reply", client_message_id="lost-1"))
            db.session.execute(application.update(application.Conversation).where(
                application.Conversation.id == conversation_id).values(message_count=1))
            db.session.commit()
        with FakeAI(reply="Here it is after all.") as fake:
            again = self.send(conversation_id, "Lost reply", client_message_id="lost-1")
        self.assertEqual(again.status_code, 200, again.get_data(as_text=True))
        self.assertEqual(again.get_json()["reply"]["content"], "Here it is after all.")
        self.assertEqual(fake.chat_calls()[0]["input"].count("Lost reply"), 1)
        self.assertEqual(self.roles(conversation_id), ["user", "assistant"])
        self.assertEqual(self.row(application.Conversation, conversation_id).message_count, 2)

    def test_an_invalid_client_message_id_is_ignored(self):
        conversation_id = self.conversation()
        with FakeAI():
            for value in ("x" * 41, "has space", "<script>", "ümlaut", ["list"]):
                with self.subTest(value=value):
                    self.assertEqual(self.send(conversation_id, f"Q {value}", client_message_id=value).status_code, 201)
        self.assertEqual(self.count(application.ConversationMessage,
                                    application.ConversationMessage.client_message_id != ""), 0)

    def test_another_accounts_client_message_id_is_not_replayed(self):
        conversation_id = self.conversation()
        with FakeAI():
            self.send(conversation_id, "Mine", client_message_id="shared-id")
        bob = self.register("bob")
        bob_conversation = self.conversation(bob)
        with FakeAI():
            response = self.send(bob_conversation, "Bob's", client=bob, client_message_id="shared-id")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["message"]["content"], "Bob's")


class RegenerateTests(ConversationTestCase):
    def regenerate(self, conversation_id, message_id, client=None, **body):
        return (client or self.client).post(f"/api/assistant/conversations/{conversation_id}/regenerate",
                                            json={"message_id": message_id, **body})

    def test_the_newest_reply_is_replaced_and_the_counts_stay(self):
        conversation_id = self.conversation()
        with FakeAI(reply="First answer."):
            old = self.send(conversation_id, "Explain fractions").get_json()["reply"]
        with FakeAI(reply="Second answer.") as fake:
            response = self.regenerate(conversation_id, old["id"])
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        body = response.get_json()
        self.assertEqual((body["replaced_id"], body["reply"]["content"]), (old["id"], "Second answer."))
        self.assertNotIn("First answer.", fake.chat_calls()[0]["input"], "the replaced reply is not replayed")
        self.assertEqual(fake.chat_calls()[0]["input"].count("Explain fractions"), 1)
        messages = self.opened(conversation_id)["messages"]
        self.assertEqual([(item["role"], item["content"]) for item in messages],
                         [("user", "Explain fractions"), ("assistant", "Second answer.")])
        row = self.row(application.Conversation, conversation_id)
        self.assertEqual((row.message_count, row.input_tokens, row.output_tokens, row.reply_started_at),
                         (2, 240, 160, None))
        # Once the thread has moved on, the replaced reply's id (and the new one's: SQLite
        # may reuse an id) is no longer the newest.
        with FakeAI():
            self.send(conversation_id, "And decimals?")
        for stale_id in {old["id"], body["reply"]["id"]}:
            stale = self.regenerate(conversation_id, stale_id)
            self.assertEqual((stale.status_code, stale.get_json()["code"]), (409, "not_latest"))

    def test_only_the_newest_message_and_only_a_reply(self):
        conversation_id = self.conversation()
        with FakeAI():
            first = self.send(conversation_id, "One").get_json()
            self.send(conversation_id, "Two")
        for message_id in (first["reply"]["id"], first["message"]["id"], 99999, None, "abc"):
            with self.subTest(message_id=message_id):
                response = self.regenerate(conversation_id, message_id)
                self.assertEqual((response.status_code, response.get_json()["code"]), (409, "not_latest"))

    def test_retry_on_a_failed_turn_answers_without_repeating_the_question(self):
        conversation_id = self.conversation()
        with FakeAI(errors={"assistant_chat": ai_service.AIProviderError("provider_timeout", "timed out")}):
            failed = self.send(conversation_id, "A question that failed").get_json()
        self.assertEqual(failed["conversation"]["message_count"], 1)
        with FakeAI(reply="Now it works.") as fake:
            retried = self.regenerate(conversation_id, failed["reply"]["id"])
        self.assertEqual(retried.status_code, 201)
        self.assertNotIn("error", retried.get_json()["reply"])
        self.assertEqual(fake.chat_calls()[0]["input"].count("A question that failed"), 1)
        self.assertEqual(self.roles(conversation_id), ["user", "assistant"])
        self.assertEqual(self.row(application.Conversation, conversation_id).message_count, 2)

    def test_a_regenerate_that_fails_keeps_the_good_reply(self):
        conversation_id = self.conversation()
        with FakeAI(reply="A good answer."):
            reply = self.send(conversation_id, "Question").get_json()["reply"]
        for error, status, code in (
                (ai_service.AIProviderError("provider_timeout", "timed out"), 504, "ai_timeout"),
                (ai_service.AIProviderError("provider_unavailable", "down"), 503, "ai_unavailable")):
            with self.subTest(code=code):
                with FakeAI(errors={"assistant_chat": error}) as fake:
                    response = self.regenerate(conversation_id, reply["id"])
                self.assertEqual(len(fake.chat_calls()), 1)
                self.assertEqual((response.status_code, response.get_json()["code"]), (status, code),
                                 response.get_data(as_text=True))
                messages = self.opened(conversation_id)["messages"]
                self.assertEqual([(item["role"], item["content"], bool(item.get("error"))) for item in messages],
                                 [("user", "Question", False), ("assistant", "A good answer.", False)])
                self.assertEqual(messages[-1]["id"], reply["id"], "the good reply was neither deleted nor replaced")
                row = self.row(application.Conversation, conversation_id)
                self.assertEqual((row.message_count, row.reply_started_at), (2, None), "counts kept, claim given back")
        # The kept reply can still be regenerated, and an empty answer keeps it too.
        with FakeAI(reply="   ") as fake:
            empty = self.regenerate(conversation_id, reply["id"])
        self.assertEqual((empty.status_code, empty.get_json()["code"]), (502, "empty_response"))
        with FakeAI(reply="A better answer."):
            better = self.regenerate(conversation_id, reply["id"])
        self.assertEqual((better.status_code, better.get_json()["reply"]["content"]), (201, "A better answer."))
        self.assertEqual(self.row(application.Conversation, conversation_id).message_count, 2)

    def test_a_failed_retry_on_a_failed_turn_still_replaces_it(self):
        conversation_id = self.conversation()
        timeout = ai_service.AIProviderError("provider_timeout", "timed out")
        with FakeAI(errors={"assistant_chat": timeout}):
            failed = self.send(conversation_id, "Question").get_json()["reply"]
        with FakeAI(errors={"assistant_chat": timeout}):
            response = self.regenerate(conversation_id, failed["id"])
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["reply"]["error"])
        self.assertEqual(self.roles(conversation_id), ["user", "assistant"])
        self.assertEqual(self.row(application.Conversation, conversation_id).message_count, 1)

    def test_regenerate_respects_ownership_and_the_claim(self):
        conversation_id = self.conversation()
        with FakeAI():
            reply = self.send(conversation_id, "Mine").get_json()["reply"]
        bob = self.register("bob")
        self.assertEqual(self.regenerate(conversation_id, reply["id"], client=bob).status_code, 404)
        self.set_columns(conversation_id, reply_started_at=application.utcnow())
        busy = self.regenerate(conversation_id, reply["id"])
        self.assertEqual((busy.status_code, busy.get_json()["code"]), (409, "conversation_busy"))


class RegenerateThroughTheGatewayTests(ConversationTestCase):
    """Regenerate with the real wrapper and gateway: only the SDK client is faked.

    The gateway hands an identical request repeated within AI_DEDUP_WINDOW_SECONDS the
    result it just produced (and, in AI_MODE=cached, the cached one). A regenerate is the
    same request as the send it follows, so it came back byte-identical, with no provider
    call - even with the provider down. It must really ask again.
    """

    def setUp(self):
        super().setUp()
        scratch = Path(tempfile.mkdtemp(prefix="learnova-regenerate-"))
        application.app.config.update(
            RUN_LIVE_AI_TEST=True, ALLOW_LIVE_AI=True, GROQ_API_KEY="test-key", AI_DEDUP_WINDOW_SECONDS=15,
            AI_DEDUP_IN_TESTS=True,   # the window is off under the test runner unless asked for
            AI_USAGE_PATH=str(scratch / "usage.jsonl"), AI_CACHE_DIR=str(scratch / "cache"))
        ai_service._RECENT_RESULTS.clear()
        self.addCleanup(ai_service._RECENT_RESULTS.clear)
        self.provider_calls: list[dict[str, Any]] = []
        self.numbers = itertools.count(1)
        test = self

        class Responses:
            def create(self, **kwargs):
                test.provider_calls.append(kwargs)
                usage = SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120)
                return SimpleNamespace(output_text=f"Answer number {next(test.numbers)}.",
                                       model=kwargs.get("model"), usage=usage)

        patcher = patch.object(ai_service, "OpenAI", lambda **_options: SimpleNamespace(responses=Responses()))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_regenerate_right_after_the_reply_asks_the_provider_again(self):
        for mode in ("live", "cached"):
            with self.subTest(mode=mode):
                application.app.config["AI_MODE"] = mode
                conversation_id = self.conversation()
                first = self.send(conversation_id, f"Why is the sky blue? ({mode})")
                self.assertEqual(first.status_code, 201, first.get_data(as_text=True))
                old = first.get_json()["reply"]
                before = len(self.provider_calls)
                response = self.client.post(f"/api/assistant/conversations/{conversation_id}/regenerate",
                                            json={"message_id": old["id"]})
                self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
                new = response.get_json()["reply"]
                self.assertEqual(len(self.provider_calls), before + 1, "the provider is really asked again")
                self.assertNotEqual(new["content"], old["content"])
                self.assertEqual([item["content"] for item in self.opened(conversation_id)["messages"]][-1],
                                 new["content"])


# ---------------------------------------------------------------------------- waiting photos
class WaitingAttachmentTests(ConversationTestCase):
    def waiting(self, client=None, **params):
        return (client or self.client).get("/api/chat-attachments", query_string={"waiting": "1", **params})

    def test_only_the_callers_unsent_photos_are_listed_oldest_first(self):
        first, second = self.upload(), self.upload()
        bob = self.register("bob")
        bobs = self.upload(bob)
        listed = self.waiting().get_json()["attachments"]
        self.assertEqual([item["id"] for item in listed], [first, second])
        for key in ("id", "url", "width", "height", "purpose", "status", "available", "hints", "created_at"):
            self.assertIn(key, listed[0])
        self.assertTrue(listed[0]["available"])
        self.assertEqual([item["id"] for item in self.waiting(bob).get_json()["attachments"]], [bobs])
        with FakeAI():
            self.send(self.conversation(), "Send one", attachments=[first])
        self.assertEqual([item["id"] for item in self.waiting().get_json()["attachments"]], [second])
        self.assertEqual(self.waiting(purpose="answer").get_json()["attachments"], [])
        answer = self.upload(purpose="answer")
        self.assertEqual([item["id"] for item in self.waiting(purpose="answer").get_json()["attachments"]], [answer])

    def test_the_list_needs_the_waiting_flag_and_the_feature(self):
        response = self.client.get("/api/chat-attachments")
        self.assertEqual((response.status_code, response.get_json()["code"]), (400, "invalid_request"))
        self.assertEqual(self.waiting(purpose="other").status_code, 400)
        application.app.config["FEATURE_CHAT_IMAGES"] = False
        self.assertEqual(self.waiting().status_code, 404)

    def test_too_many_waiting_says_how_many(self):
        with application.app.app_context():
            user_id = db.session.scalar(db.select(application.User.id).where(application.User.username == "alice"))
            for index in range(12):
                db.session.add(application.ChatAttachment(user_id=user_id, data=b"x", source_sha256=f"s{index}"))
            db.session.commit()
        response = self.client.post("/api/chat-attachments", data={
            "file": (io.BytesIO(png()), "photo.png"), "purpose": "chat"}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 409)
        self.assertEqual((response.get_json()["code"], response.get_json()["details"]),
                         ("too_many_waiting", {"waiting": 12}))
        self.assertEqual(len(self.waiting().get_json()["attachments"]), 12)

    def test_the_retention_command_runs_without_an_upload(self):
        old_unsent = self.upload()
        kept = self.upload()
        with application.app.app_context():
            db.session.execute(application.update(application.ChatAttachment).where(
                application.ChatAttachment.id == old_unsent).values(
                created_at=application.utcnow() - timedelta(days=2)))
            db.session.commit()
        result = application.app.test_cli_runner().invoke(args=["chat-attachments", "cleanup", "--limit", "5"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Deleted 1 unsent photo(s)", result.output)
        self.assertIsNone(self.row(application.ChatAttachment, old_unsent))
        self.assertIsNotNone(self.row(application.ChatAttachment, kept))


# ---------------------------------------------------------------------------- pages, auth, errors
class DeepLinkAndAuthTests(ConversationTestCase):
    def test_the_conversation_page_is_its_owners_only(self):
        conversation_id = self.conversation()
        page = self.client.get(f"/assistant/c/{conversation_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"asstComposer", page.data)
        self.assertEqual(self.register("bob").get(f"/assistant/c/{conversation_id}").status_code, 404)
        self.assertEqual(self.client.get("/assistant/c/" + "x" * 40).status_code, 404)
        anonymous = application.app.test_client().get(f"/assistant/c/{conversation_id}")
        self.assertEqual(anonymous.status_code, 302)
        self.assertTrue(anonymous.headers["Location"].endswith(f"/login?next=/assistant/c/{conversation_id}"),
                        anonymous.headers["Location"])
        application.app.config["FEATURE_ASSISTANT_CHAT"] = False
        self.assertEqual(self.client.get(f"/assistant/c/{conversation_id}").status_code, 404)

    def test_signed_out_api_calls_get_a_json_401_not_a_login_page(self):
        anonymous = application.app.test_client()
        for method, path in (
            ("get", "/api/assistant/conversations"), ("post", "/api/assistant/conversations"),
            ("get", "/api/assistant/conversations/abc"), ("patch", "/api/assistant/conversations/abc"),
            ("delete", "/api/assistant/conversations/abc"), ("post", "/api/assistant/conversations/abc/messages"),
            ("post", "/api/assistant/conversations/abc/regenerate"), ("get", "/api/chat-attachments?waiting=1"),
            ("post", "/api/chat-attachments"),
        ):
            with self.subTest(method=method, path=path):
                response = getattr(anonymous, method)(path)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json()["code"], "login_required")
                self.assertFalse(response.get_json()["ok"])
        self.assertEqual(anonymous.get("/assistant").status_code, 302, "pages still redirect to the login")

    def test_an_unhandled_error_on_an_api_route_is_json(self):
        application.app.config["PROPAGATE_EXCEPTIONS"] = False

        def boom(*_args, **_kwargs):
            raise RuntimeError("secret detail")

        with patch.dict(application.app.view_functions, {"list_conversations": boom}):
            response = self.client.get("/api/assistant/conversations")
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertEqual((body["ok"], body["code"]), (False, "internal_error"))
        self.assertNotIn("secret", response.get_data(as_text=True))
        with patch.dict(application.app.view_functions, {"assistant_page": boom}):
            page = self.client.get("/assistant")
        self.assertEqual(page.status_code, 500)
        self.assertIsNone(page.get_json(silent=True), "a browser page keeps the HTML error page")

    def test_conversations_survive_logging_out_and_in_again(self):
        conversation_id = self.conversation()
        with FakeAI(reply="Photosynthesis turns light into sugar."):
            self.send(conversation_id, "What is photosynthesis?")
        self.client.patch(f"/api/assistant/conversations/{conversation_id}", json={"title": "Biology notes"})
        archived_id = self.conversation()
        with FakeAI():
            self.send(archived_id, "Archive me")
        self.client.patch(f"/api/assistant/conversations/{archived_id}", json={"archived": True})
        self.assertEqual(self.client.post("/logout").status_code, 302)
        self.assertEqual(self.client.get("/api/assistant/conversations").status_code, 401, "really signed out")
        login = self.client.post("/login", data={"identifier": "alice", "password": "correct-horse-battery"})
        self.assertEqual(login.status_code, 302)
        recent = self.listing()["conversations"]
        self.assertEqual([(row["id"], row["title"]) for row in recent], [(conversation_id, "Biology notes")])
        self.assertEqual([row["id"] for row in self.listing(view="archived")["conversations"]], [archived_id])
        messages = self.opened(conversation_id)["messages"]
        self.assertEqual([(item["role"], item["content"]) for item in messages],
                         [("user", "What is photosynthesis?"), ("assistant", "Photosynthesis turns light into sugar.")])
        self.assertEqual(self.client.get(f"/assistant/c/{conversation_id}").status_code, 200)

    def test_the_rate_limit_key_is_the_account(self):
        with application.app.test_request_context("/"):
            self.assertEqual(application.user_rate_key(), application.get_remote_address())
            user = db.session.scalar(db.select(application.User).where(application.User.username == "alice"))
            application.login_user(user)
            self.assertEqual(application.user_rate_key(), f"user:{user.id}")

    def test_limits_count_per_account_not_per_address(self):
        if not application.limiter.enabled:
            self.skipTest("rate limiting is disabled in this configuration")
        bob = self.register("bob")
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=False, ASSISTANT_CONVERSATIONS_PER_HOUR=2)
        application.limiter.reset()
        try:
            statuses = [self.client.post("/api/assistant/conversations", json={}).status_code for _ in range(3)]
            bobs = bob.post("/api/assistant/conversations", json={}).status_code
        finally:
            application.app.config.update(TESTING=True)
            application.limiter.reset()
        self.assertEqual(statuses, [201, 201, 429])
        self.assertEqual(bobs, 201, "another account behind the same address has its own bucket")

    def test_send_and_regenerate_share_one_hourly_limit(self):
        """Each is a billed reply: a regenerate must not open a second hourly allowance."""

        if not application.limiter.enabled:
            self.skipTest("rate limiting is disabled in this configuration")
        bob = self.register("bob")
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=False, ASSISTANT_MESSAGES_PER_HOUR=3)
        application.limiter.reset()
        try:
            with FakeAI() as fake:
                conversation_id = self.conversation()
                sent = [self.send(conversation_id, f"Question {n}") for n in range(2)]
                reply_id = sent[-1].get_json()["reply"]["id"]
                regenerated = self.client.post(f"/api/assistant/conversations/{conversation_id}/regenerate",
                                               json={"message_id": reply_id})
                newest = regenerated.get_json()["reply"]["id"] if regenerated.status_code == 201 else reply_id
                again = self.client.post(f"/api/assistant/conversations/{conversation_id}/regenerate",
                                         json={"message_id": newest})
                more = self.send(conversation_id, "Question 3")
                calls = len(fake.chat_calls())
                bobs = self.send(self.conversation(bob), "Bob's question", client=bob).status_code
        finally:
            application.app.config.update(TESTING=True)
            application.limiter.reset()
        self.assertEqual([item.status_code for item in sent] + [regenerated.status_code], [201, 201, 201])
        self.assertEqual((again.status_code, again.get_json()["code"]), (429, "rate_limited"))
        self.assertEqual(more.status_code, 429, "the regenerate used the send allowance too")
        self.assertEqual(calls, 3, "three AI replies in the hour, not three sends plus three regenerates")
        self.assertEqual(bobs, 201, "another account keeps its own bucket")

    def test_every_assistant_route_limit_is_keyed_per_account(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for route in ('@app.get("/api/assistant/conversations")', '@app.post("/api/assistant/conversations")',
                      '@app.post("/api/assistant/conversations/<conversation_id>/messages")',
                      '@app.post("/api/assistant/conversations/<conversation_id>/regenerate")',
                      '@app.get("/api/chat-attachments")', '@app.post("/api/chat-attachments")',
                      '@app.post("/api/chat")'):
            with self.subTest(route=route):
                limit_line = source.split(route + "\n", 1)[1].split("\n", 1)[0]
                self.assertTrue(limit_line.startswith(("@limiter.limit(", "@limiter.shared_limit(")), limit_line)
                self.assertIn("key_func=user_rate_key", limit_line)
        # Send and regenerate: one shared bucket (the same scope), not one each.
        reply_routes = ('@app.post("/api/assistant/conversations/<conversation_id>/messages")',
                        '@app.post("/api/assistant/conversations/<conversation_id>/regenerate")')
        lines = {source.split(route + "\n", 1)[1].split("\n", 1)[0] for route in reply_routes}
        self.assertEqual(len(lines), 1, lines)
        self.assertIn('scope="assistant-reply"', lines.pop())


class TranslationTests(unittest.TestCase):
    MESSAGES = ("Your session has expired. Log in again.", "Something went wrong. Please try again.",
                "An answer is still being written for this conversation. Wait a moment.",
                "This reply can no longer be regenerated.", "This request could not be understood.",
                "Send or remove the photos you already added first.")

    def test_every_new_message_is_translated(self):
        data = ROOT / "learnova" / "translations" / "data"
        french = json.loads((data / "fr.json").read_text(encoding="utf-8"))
        spanish = json.loads((data / "es.json").read_text(encoding="utf-8"))
        placeholder = re.compile(r"\{[^{}\s]*\}")
        for message in self.MESSAGES:
            for language, table in (("de", catalog.GERMAN), ("fr", french), ("es", spanish)):
                with self.subTest(language=language, message=message):
                    self.assertIn(message, table)
                    self.assertEqual(placeholder.findall(message), placeholder.findall(table[message]))


# ---------------------------------------------------------------------------- migration 040
class MigrationTests(unittest.TestCase):
    """040_assistant_chat_upgrade on a database from before it: columns added, data kept."""

    NEW = {"conversation": ("archived_at", "title_source", "reply_started_at", "subject", "topic", "teaching_json"),
           "conversation_message": ("chips_json", "client_message_id")}

    @staticmethod
    def legacy_ddl(table_name, drop):
        from sqlalchemy.schema import CreateTable

        ddl = str(CreateTable(db.metadata.tables[table_name]).compile(db.engine))
        kept = [line for line in ddl.split("\n") if not any(re.search(rf"\b{name}\b", line) for name in drop)]
        return re.sub(r",\s*\)\s*$", "\n)", "\n".join(kept).rstrip())

    @staticmethod
    def legacy_insert(connection, table_name, drop, **values):
        row = {}
        for column in db.metadata.tables[table_name].columns:
            if column.name in drop:
                continue
            if column.name in values:
                row[column.name] = values[column.name]
                continue
            if column.nullable or column.primary_key:
                continue
            try:
                python_type = column.type.python_type
            except NotImplementedError:
                python_type = str
            row[column.name] = {int: 0, float: 0.0, bool: False, bytes: b"", str: ""}.get(
                python_type, "2026-10-01 10:00:00.000000")
        names = ", ".join(f'"{name}"' for name in row)
        marks = ", ".join(f":{name}" for name in row)
        connection.execute(application.text(f'INSERT INTO "{table_name}" ({names}) VALUES ({marks})'), row)

    def test_old_rows_are_upgraded_and_a_second_run_changes_nothing(self):
        original = dict(application.app.config)
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            db.drop_all()
            try:
                with db.engine.begin() as connection:
                    connection.execute(application.text(self.legacy_ddl("user", ())))
                    for table_name, drop in self.NEW.items():
                        connection.execute(application.text(self.legacy_ddl(table_name, drop)))
                    self.legacy_insert(connection, "user", (), id=1, username="old", email="old@example.com",
                                       password_hash="x", preferred_language="de")
                    drop = self.NEW["conversation"]
                    self.legacy_insert(connection, "conversation", drop, id="archived-1", user_id=1,
                                       title="Old archived", archived=True, message_count=2,
                                       updated_at="2026-10-02 08:30:00.000000")
                    self.legacy_insert(connection, "conversation", drop, id="draft-1", user_id=1,
                                       title="New conversation", message_count=0)
                    self.legacy_insert(connection, "conversation", drop, id="named-1", user_id=1,
                                       title="Explain fractions", message_count=2)
                    self.legacy_insert(connection, "conversation_message", self.NEW["conversation_message"], id=1,
                                       conversation_id="named-1", role="user", content="Explain fractions")
                db.session.remove()
                application.ensure_database()
                inspector = application.inspect(db.engine)
                for table_name, names in self.NEW.items():
                    self.assertTrue(set(names) <= {column["name"] for column in inspector.get_columns(table_name)})
                self.assertIn("conversation_lesson", inspector.get_table_names())
                self.assertIn("uq_conversation_lesson_source",
                              {item["name"] for item in inspector.get_unique_constraints("conversation_lesson")}
                              | {item["name"] for item in inspector.get_indexes("conversation_lesson")})

                def state():
                    db.session.expire_all()
                    rows = {row.id: row for row in db.session.scalars(db.select(application.Conversation))}
                    message = db.session.get(application.ConversationMessage, 1)
                    return ({key: (row.title, row.title_source, row.archived,
                                   application.as_utc(row.archived_at).isoformat() if row.archived_at else None,
                                   row.reply_started_at, row.subject, row.topic, row.teaching_json)
                             for key, row in rows.items()},
                            (message.content, message.chips_json, message.client_message_id))

                upgraded = state()
                self.assertEqual(upgraded[0]["archived-1"][:4],
                                 ("Old archived", "auto", True, "2026-10-02T08:30:00+00:00"))
                self.assertEqual(upgraded[0]["draft-1"][:4], ("New conversation", "default", False, None))
                self.assertEqual(upgraded[0]["named-1"][1:], ("auto", False, None, None, "", "", "{}"))
                self.assertEqual(upgraded[1], ("Explain fractions", "[]", ""))
                self.assertIsNotNone(db.session.get(application.SchemaMigration, "040_assistant_chat_upgrade"))
                db.session.remove()
                application.ensure_database()          # idempotent: a second run is a no-op
                self.assertEqual(state(), upgraded)
                self.assertEqual(db.session.scalar(db.select(db.func.count(application.SchemaMigration.version)).where(
                    application.SchemaMigration.version == "040_assistant_chat_upgrade")), 1)
                calls = re.findall(r'apply_schema_migration\(\s*"040_assistant_chat_upgrade"',
                                   (ROOT / "app.py").read_text(encoding="utf-8"))
                self.assertEqual(len(calls), 2, "the add-it and the already-there branch")
            finally:
                db.session.remove()
                db.drop_all()
                db.create_all()
                application.app.config.clear()
                application.app.config.update(original)


if __name__ == "__main__":
    unittest.main()

"""End-to-end assistant chat through the real Flask routes.

Every AI call is patched at `app.create_response`, so these run offline while exercising
the genuine routes, the database, ownership checks and the failure paths.
"""

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_assistant_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class FakeUsage:
    input_tokens = 120
    output_tokens = 80
    total_tokens = 200


class FakeResponse:
    def __init__(self, text="Here is a clear answer.", model="test-model"):
        self.output_text = text
        self.model = model
        self.usage = FakeUsage()


class ai:
    """Patch the AI boundary for one exchange and record what it was sent.

    A context manager rather than a bare `patch`, so `captured` is a real attribute the
    test can read afterwards instead of something bolted onto a mock.
    """

    def __init__(self, text: str = "Here is a clear answer.", error: Exception | None = None):
        self.text = text
        self.error = error
        self.captured: dict[str, Any] = {}
        self._patcher = patch.object(
            application, "create_response", side_effect=self._respond)

    def _respond(self, **kwargs: Any) -> FakeResponse:
        self.captured.update(kwargs)
        if self.error is not None:
            raise self.error
        return FakeResponse(self.text)

    def __enter__(self) -> "ai":
        self._patcher.start()
        return self

    def __exit__(self, *exception: Any) -> None:
        self._patcher.stop()


class AssistantTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, FEATURE_ASSISTANT_CHAT=True,
            ASSISTANT_MODEL="openai/gpt-oss-20b",
            ASSISTANT_DEEP_MODEL="llama-3.3-70b-versatile")
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = self._register("alice")

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def _register(self, name):
        client = application.app.test_client()
        client.post("/register", data={
            "username": name, "email": f"{name}@example.com",
            "password": "correct-horse-battery"})
        return client

    def _conversation(self, client=None, preset="general"):
        response = (client or self.client).post(
            "/api/assistant/conversations", json={"preset": preset})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        return response.get_json()["conversation"]["id"]

    def _send(self, conversation_id, message, client=None, **body):
        return (client or self.client).post(
            f"/api/assistant/conversations/{conversation_id}/messages",
            json={"message": message, **body})


class ConversationLifecycleTests(AssistantTestCase):
    def test_the_page_renders_and_is_linked_in_navigation(self):
        page = self.client.get("/assistant")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"asstComposer", page.data)
        self.assertIn(b'href="/assistant"', self.client.get("/dashboard").data)

    def test_creating_listing_and_opening_a_conversation(self):
        conversation_id = self._conversation()
        listed = self.client.get("/api/assistant/conversations").get_json()
        self.assertEqual([item["id"] for item in listed["conversations"]], [conversation_id])
        opened = self.client.get(f"/api/assistant/conversations/{conversation_id}").get_json()
        self.assertEqual(opened["conversation"]["messages"], [])
        self.assertEqual(opened["conversation"]["preset"], "general")

    def test_a_full_exchange_is_stored_and_returned(self):
        conversation_id = self._conversation()
        with ai("The derivative of x squared is 2x."):
            sent = self._send(conversation_id, "What is the derivative of x squared?")
        self.assertEqual(sent.status_code, 201)
        body = sent.get_json()
        self.assertEqual(body["message"]["role"], "user")
        self.assertEqual(body["reply"]["role"], "assistant")
        self.assertIn("2x", body["reply"]["content"])
        self.assertEqual(body["conversation"]["message_count"], 2)

        opened = self.client.get(
            f"/api/assistant/conversations/{conversation_id}").get_json()
        self.assertEqual([item["role"] for item in opened["conversation"]["messages"]],
                         ["user", "assistant"])

    def test_the_title_comes_from_the_first_message(self):
        conversation_id = self._conversation()
        with ai():
            self._send(conversation_id, "Explain photosynthesis. I keep forgetting it.")
        listed = self.client.get("/api/assistant/conversations").get_json()
        self.assertEqual(listed["conversations"][0]["title"], "Explain photosynthesis.")

    def test_later_messages_do_not_rename_the_conversation(self):
        conversation_id = self._conversation()
        with ai():
            self._send(conversation_id, "First question")
            self._send(conversation_id, "A completely different second question")
        listed = self.client.get("/api/assistant/conversations").get_json()
        self.assertEqual(listed["conversations"][0]["title"], "First question")

    def test_history_is_carried_into_the_next_turn(self):
        conversation_id = self._conversation()
        with ai():
            self._send(conversation_id, "My favourite number is seventeen.")
        patcher = ai()
        with patcher:
            self._send(conversation_id, "What did I say my favourite number was?")
        sent = patcher.captured["input"]
        self.assertIn("seventeen", sent)
        self.assertIn("Learner:", sent)
        self.assertIn("Assistant:", sent)

    def test_rename_archive_and_delete(self):
        conversation_id = self._conversation()
        renamed = self.client.patch(f"/api/assistant/conversations/{conversation_id}",
                                    json={"title": "Thermodynamics"})
        self.assertEqual(renamed.get_json()["conversation"]["title"], "Thermodynamics")

        self.client.patch(f"/api/assistant/conversations/{conversation_id}",
                          json={"archived": True})
        self.assertEqual(
            self.client.get("/api/assistant/conversations").get_json()["conversations"], [])
        self.assertEqual(len(self.client.get(
            "/api/assistant/conversations?archived=1").get_json()["conversations"]), 1)

        deleted = self.client.delete(f"/api/assistant/conversations/{conversation_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/assistant/conversations/{conversation_id}").status_code, 404)

    def test_deleting_a_conversation_removes_its_messages(self):
        conversation_id = self._conversation()
        with ai():
            self._send(conversation_id, "Something to remember")
        self.client.delete(f"/api/assistant/conversations/{conversation_id}")
        with application.app.app_context():
            remaining = application.db.session.scalars(application.db.select(
                application.ConversationMessage)).all()
            self.assertEqual(remaining, [])

    def test_an_empty_title_is_refused(self):
        conversation_id = self._conversation()
        self.assertEqual(self.client.patch(
            f"/api/assistant/conversations/{conversation_id}",
            json={"title": "  "}).status_code, 400)


class PresetAndModelTests(AssistantTestCase):
    def test_the_preset_shapes_the_system_prompt(self):
        conversation_id = self._conversation(preset="research")
        patcher = ai()
        with patcher:
            self._send(conversation_id, "Is this claim well supported?")
        instructions = patcher.captured["instructions"]
        self.assertIn("what the evidence shows", instructions)
        self.assertIn("Never invent a source", instructions)

    def test_changing_the_preset_applies_to_later_turns_only(self):
        conversation_id = self._conversation(preset="general")
        with ai():
            self._send(conversation_id, "First turn")
        self.client.patch(f"/api/assistant/conversations/{conversation_id}",
                          json={"preset": "study_coach"})
        patcher = ai()
        with patcher:
            self._send(conversation_id, "Second turn")
        self.assertIn("Teach rather than solve", patcher.captured["instructions"])

    def test_an_unknown_preset_falls_back_rather_than_failing(self):
        created = self.client.post("/api/assistant/conversations",
                                   json={"preset": "no-such-preset"})
        self.assertEqual(created.get_json()["conversation"]["preset"], "general")

    def test_think_harder_selects_the_deep_model(self):
        conversation_id = self._conversation()
        normal, deep = ai(), ai()
        with normal:
            self._send(conversation_id, "An easy question")
        with deep:
            self._send(conversation_id, "A hard question", deep=True)
        self.assertEqual(normal.captured["model"], "openai/gpt-oss-20b")
        self.assertEqual(deep.captured["model"], "llama-3.3-70b-versatile")

    def test_the_model_and_provider_are_recorded_on_the_reply(self):
        conversation_id = self._conversation()
        with ai():
            reply = self._send(conversation_id, "Hello").get_json()["reply"]
        self.assertEqual(reply["provider"], "groq")
        self.assertTrue(reply["model"])

    def test_a_provider_prefixed_model_is_recorded_as_that_provider(self):
        application.app.config["ASSISTANT_MODEL"] = "openai:gpt-5"
        conversation_id = self._conversation()
        patcher = ai()
        with patcher:
            reply = self._send(conversation_id, "Hello").get_json()["reply"]
        self.assertEqual(reply["provider"], "openai")
        # The gateway is handed the prefixed name; stripping happens at the boundary.
        self.assertEqual(patcher.captured["model"], "openai:gpt-5")


class StyleRoutingTests(AssistantTestCase):
    """The owner maps styles to models; the student only ever picks a style."""

    def test_a_mapped_style_routes_its_replies_and_records_the_provider(self):
        application.app.config.update(ASSISTANT_MODEL_RESEARCH="anthropic:claude-x")
        conversation_id = self._conversation(preset="research")
        with ai() as captured:
            reply = self._send(conversation_id, "Compare two sources.")
        self.assertEqual(captured.captured["model"], "anthropic:claude-x")
        self.assertEqual(captured.captured["preset"], "research")
        self.assertFalse(captured.captured["deep"])
        self.assertEqual(reply.get_json()["reply"]["provider"], "anthropic")
        with ai() as general:
            self._send(self._conversation(preset="general"), "Hello")
        self.assertEqual(general.captured["model"], "openai/gpt-oss-20b", "other styles keep the global model")

    def test_think_harder_reaches_the_gateway_as_a_routing_signal(self):
        conversation_id = self._conversation(preset="explain")
        with ai() as captured:
            self._send(conversation_id, "Why is the sky blue?", deep=True)
        self.assertTrue(captured.captured["deep"])
        self.assertEqual(captured.captured["preset"], "explain")
        self.assertEqual(captured.captured["model"], "llama-3.3-70b-versatile")

    def test_a_student_cannot_name_a_model_or_provider(self):
        conversation_id = self._conversation(preset="general")
        with ai() as captured:
            response = self._send(conversation_id, "Hello",
                                  model="anthropic:claude-opus-5-5", provider="anthropic")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        self.assertEqual(captured.captured["model"], "openai/gpt-oss-20b")
        self.assertEqual(captured.captured["preset"], "general")


class ContextWindowTests(AssistantTestCase):
    def test_a_long_thread_is_trimmed_and_the_trim_is_reported(self):
        application.app.config.update(
            ASSISTANT_CONTEXT_TOKEN_BUDGET=400, ASSISTANT_REPLY_TOKEN_RESERVE=100)
        conversation_id = self._conversation()
        with ai("A reply. " * 30):
            for index in range(8):
                self._send(conversation_id, f"Question {index}. " + "padding " * 60)
        with ai():
            reply = self._send(conversation_id, "Final question").get_json()["reply"]
        self.assertGreater(reply["context_dropped"], 0)

    def test_the_newest_question_always_reaches_the_model(self):
        application.app.config.update(
            ASSISTANT_CONTEXT_TOKEN_BUDGET=300, ASSISTANT_REPLY_TOKEN_RESERVE=50)
        conversation_id = self._conversation()
        with ai():
            for index in range(6):
                self._send(conversation_id, f"Filler {index} " + "padding " * 80)
        patcher = ai()
        with patcher:
            self._send(conversation_id, "THE FINAL QUESTION")
        self.assertIn("THE FINAL QUESTION", patcher.captured["input"])


class FailureTests(AssistantTestCase):
    def test_a_provider_failure_is_stored_as_a_turn_not_lost(self):
        conversation_id = self._conversation()
        failure = application.ai_service.AIProviderError("provider_timeout", "timed out")
        with ai(error=failure):
            sent = self._send(conversation_id, "A question that fails")
        self.assertEqual(sent.status_code, 201)
        reply = sent.get_json()["reply"]
        self.assertTrue(reply["error"])
        # The learner's own question is kept, so retrying does not mean retyping.
        opened = self.client.get(
            f"/api/assistant/conversations/{conversation_id}").get_json()
        roles = [item["role"] for item in opened["conversation"]["messages"]]
        self.assertEqual(roles, ["user", "assistant"])

    def test_a_failed_turn_is_not_replayed_to_the_model(self):
        """Replaying an error as if the assistant said it teaches it to apologise."""

        conversation_id = self._conversation()
        failure = application.ai_service.AIProviderError("provider_timeout", "timed out")
        with ai(error=failure):
            self._send(conversation_id, "First attempt")
        patcher = ai()
        with patcher:
            self._send(conversation_id, "Second attempt")
        self.assertNotIn("timed out", patcher.captured["input"])
        self.assertNotIn("temporarily unavailable", patcher.captured["input"].casefold())

    def test_an_empty_reply_is_reported_rather_than_stored_as_an_answer(self):
        conversation_id = self._conversation()
        with ai(""):
            reply = self._send(conversation_id, "A question").get_json()["reply"]
        self.assertTrue(reply["error"])

    def test_a_failed_turn_does_not_consume_the_conversation_budget_twice(self):
        conversation_id = self._conversation()
        failure = application.ai_service.AIProviderError("provider_timeout", "timed out")
        with ai(error=failure):
            body = self._send(conversation_id, "A question").get_json()
        self.assertEqual(body["conversation"]["message_count"], 1)

    def test_an_empty_message_is_refused(self):
        conversation_id = self._conversation()
        self.assertEqual(self._send(conversation_id, "   ").status_code, 400)

    def test_an_over_long_message_is_bounded_not_rejected(self):
        application.app.config["ASSISTANT_MAX_MESSAGE_CHARACTERS"] = 100
        conversation_id = self._conversation()
        with ai():
            sent = self._send(conversation_id, "x" * 5000)
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(len(sent.get_json()["message"]["content"]), 100)

    def test_a_full_conversation_is_refused_with_a_clear_code(self):
        application.app.config["ASSISTANT_MAX_MESSAGES_PER_CONVERSATION"] = 2
        conversation_id = self._conversation()
        with ai():
            self._send(conversation_id, "First")
            full = self._send(conversation_id, "Second")
        self.assertEqual(full.status_code, 409)
        self.assertEqual(full.get_json()["code"], "conversation_full")

    def test_the_conversation_ceiling_is_enforced(self):
        application.app.config["ASSISTANT_MAX_CONVERSATIONS"] = 1
        self._conversation()
        blocked = self.client.post("/api/assistant/conversations", json={})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.get_json()["code"], "conversation_limit_reached")


class IsolationTests(AssistantTestCase):
    def test_a_conversation_is_invisible_to_another_account(self):
        conversation_id = self._conversation()
        with ai():
            self._send(conversation_id, "Something private")
        bob = self._register("bob")
        self.assertEqual(bob.get(
            f"/api/assistant/conversations/{conversation_id}").status_code, 404)
        self.assertEqual(bob.patch(
            f"/api/assistant/conversations/{conversation_id}",
            json={"title": "stolen"}).status_code, 404)
        self.assertEqual(bob.delete(
            f"/api/assistant/conversations/{conversation_id}").status_code, 404)
        self.assertEqual(self._send(conversation_id, "hi", client=bob).status_code, 404)
        self.assertEqual(
            bob.get("/api/assistant/conversations").get_json()["conversations"], [])

    def test_every_route_requires_authentication(self):
        anonymous = application.app.test_client()
        for method, path in (
            ("get", "/assistant"),
            ("get", "/api/assistant/conversations"),
            ("post", "/api/assistant/conversations"),
            ("get", "/api/assistant/conversations/abc"),
            ("post", "/api/assistant/conversations/abc/messages"),
            ("delete", "/api/assistant/conversations/abc"),
        ):
            response = getattr(anonymous, method)(path)
            self.assertIn(response.status_code, {302, 401}, f"{method} {path}")

    def test_a_malformed_identifier_is_a_clean_404(self):
        for bad in ("x" * 80, "../../etc/passwd", "'; DROP TABLE conversation;--"):
            self.assertEqual(
                self.client.get(f"/api/assistant/conversations/{bad}").status_code, 404)

    def test_no_account_identifier_is_sent_to_the_provider(self):
        conversation_id = self._conversation()
        patcher = ai()
        with patcher:
            self._send(conversation_id, "A question")
        payload = f"{patcher.captured['instructions']}\n{patcher.captured['input']}"
        for identifier in ("alice", "alice@example.com"):
            self.assertNotIn(identifier, payload)


class FeatureFlagTests(AssistantTestCase):
    def test_the_whole_surface_disappears_when_the_flag_is_off(self):
        application.app.config["FEATURE_ASSISTANT_CHAT"] = False
        self.assertEqual(self.client.get("/assistant").status_code, 404)
        self.assertEqual(self.client.get("/api/assistant/conversations").status_code, 404)
        self.assertEqual(self.client.post("/api/assistant/conversations").status_code, 404)
        self.assertNotIn(b'href="/assistant"', self.client.get("/dashboard").data)


class GatewayIntegrationTests(AssistantTestCase):
    def test_the_task_goes_through_the_shared_gateway_with_its_own_budget(self):
        conversation_id = self._conversation()
        patcher = ai()
        with patcher:
            self._send(conversation_id, "A question")
        self.assertEqual(patcher.captured["task_type"], "assistant_chat")
        self.assertEqual(
            patcher.captured["max_output_tokens"],
            application.app.config["AI_ASSISTANT_CHAT_MAX_OUTPUT_TOKENS"])
        # Partitioned per learner so one person's cached reply is never served to another.
        self.assertTrue(patcher.captured["private_scope"])
        self.assertEqual(patcher.captured["session_scope"], conversation_id)

    def test_token_use_is_accumulated_on_the_conversation(self):
        conversation_id = self._conversation()
        with ai():
            self._send(conversation_id, "One")
            self._send(conversation_id, "Two")
        with application.app.app_context():
            conversation = application.db.session.get(
                application.Conversation, conversation_id)
            assert conversation is not None
            self.assertEqual(conversation.input_tokens, 240)
            self.assertEqual(conversation.output_tokens, 160)

    def test_the_assistant_task_is_registered_everywhere_it_must_be(self):
        from learnova.ai_services import prompts, service
        self.assertIn("assistant_chat", service.SUPPORTED_TASK_TYPES)
        self.assertIn("assistant_chat", prompts.PROMPT_VERSIONS)
        # Conversational prose, so there is no JSON contract to demand or validate.
        self.assertNotIn("assistant_chat", prompts.STRUCTURED_TASKS)


if __name__ == "__main__":
    unittest.main()

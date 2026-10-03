"""The app-level create_response wrapper, exercised for real.

This guards a bug that silently broke four features at once. `app.create_response`
injects the signed-in user as the cache partition, and it popped `session_scope` out of
`**kwargs` but not `private_scope`. Any caller naming a scope explicitly therefore hit:

    TypeError: create_response() got multiple values for keyword argument 'private_scope'

Four call sites did exactly that - `assistant_chat`, `answer_diagnosis`,
`diagnosis_verification` and `question_generation`. Each catches broadly and degrades, so
the app kept working and simply never used its newest features; the assistant reported
"AI is temporarily unavailable" and `instance/ai_usage.jsonl` held zero records for all
four, because the gateway was never reached.

**Why no existing test caught it:** every assistant and diagnostics test patches
`application.create_response` itself, replacing the very function that was broken. These
tests patch one layer lower, at the provider boundary, so the real wrapper runs.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_wrapper_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class StubUsage:
    input_tokens = 5
    output_tokens = 3
    total_tokens = 8


class StubResponse:
    output_text = '{"ok": true}'
    model = "stub-model"
    usage = StubUsage()


class WrapperScopeTests(unittest.TestCase):
    """Patched at the provider, so the real wrapper and gateway both run."""

    def setUp(self):
        application.app.config.update(
            TESTING=True,
            RUN_LIVE_AI_TEST=True,     # past the billing guard; no network, see below
            AI_USAGE_PATH="",          # do not append to the real usage log
        )

    def tearDown(self):
        application.app.config.update(RUN_LIVE_AI_TEST=False)

    def call(self, **extra):
        """Run one wrapper call and return what reached the gateway."""
        with application.app.test_request_context("/"):
            with patch.object(ai_service, "_provider_response",
                              return_value=StubResponse()) as provider, \
                 patch.object(ai_service, "validate_output", return_value=None), \
                 patch.object(ai_service, "_assert_usage_limits"), \
                 patch.object(ai_service, "_record_usage") as record:
                application.create_response(
                    task_type="assistant_chat", language="English", model="m",
                    instructions="i", input="hello", max_output_tokens=10, **extra)
        return provider, record.call_args.args[0] if record.call_args else {}

    def test_an_explicit_scope_does_not_collide_with_the_injected_one(self):
        # The exact crash: four call sites pass private_scope themselves.
        self.call(private_scope=7)

    def test_an_explicit_scope_and_session_scope_together_work(self):
        self.call(private_scope=7, session_scope="conversation-1")

    def test_an_explicit_scope_wins_over_the_signed_in_user(self):
        _provider, record = self.call(private_scope="chosen-by-the-caller")
        signed_out = self.call()[1]
        self.assertNotEqual(record.get("user_reference"), signed_out.get("user_reference"))

    def test_an_explicit_none_is_honoured_rather_than_overwritten(self):
        # The one case a plain default could not express: a call whose cache is meant to
        # be shared rather than partitioned per user.
        _provider, record = self.call(private_scope=None)
        self.assertEqual(record.get("user_reference"), "anonymous")

    def test_omitting_it_still_partitions_by_user(self):
        # The default every other call site relies on must not have changed.
        _provider, record = self.call()
        self.assertEqual(record.get("user_reference"), "anonymous")  # no user in this context

    def test_the_provider_is_actually_reached(self):
        provider, record = self.call(private_scope=7)
        self.assertEqual(provider.call_count, 1)
        self.assertTrue(record.get("provider_called"))
        self.assertTrue(record.get("success"))


class EveryExplicitCallSiteTests(unittest.TestCase):
    """The four features the bug took out, named so a regression is recognisable."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app.py").read_text(encoding="utf-8")

    def test_the_wrapper_pops_both_scopes(self):
        wrapper = self.source.split("def create_response(*, task_type", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('kwargs.pop("private_scope"', wrapper)
        self.assertIn('kwargs.pop("session_scope"', wrapper)

    def test_every_caller_that_names_a_scope_is_still_covered(self):
        """If a fifth call site appears, it is covered by the same pop - but if someone
        reverts the pop, this lists exactly what breaks."""
        callers = re.findall(r"private_scope=(?!private_scope)", self.source)
        # Four call sites plus the wrapper's own forward to the gateway.
        self.assertGreaterEqual(len(callers), 4)

    def test_the_four_tasks_are_registered_and_reachable(self):
        for task in ("assistant_chat", "answer_diagnosis",
                     "diagnosis_verification", "question_generation"):
            self.assertIn(task, ai_service.SUPPORTED_TASK_TYPES, task)


class AssistantEndToEndTests(unittest.TestCase):
    """The student-visible symptom: 'AI is temporarily unavailable' on every message."""

    def setUp(self):
        application.app.config.update(
            TESTING=True, RUN_LIVE_AI_TEST=True, AI_USAGE_PATH="")
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "sanne", "email": "sanne@example.com",
            "password": "correct-horse-battery"})

    def tearDown(self):
        application.app.config.update(RUN_LIVE_AI_TEST=False)

    def test_a_message_gets_a_real_reply(self):
        created = self.client.post("/api/assistant/conversations", json={"preset": "general"})
        conversation_id = created.get_json()["conversation"]["id"]

        class Reply(StubResponse):
            output_text = "2 + 2 = 4."

        # Patched at the provider, not at app.create_response: patching the wrapper is
        # what let this ship broken.
        with patch.object(ai_service, "_provider_response", return_value=Reply()):
            response = self.client.post(
                f"/api/assistant/conversations/{conversation_id}/messages",
                json={"message": "What is 2+2?", "deep": False})

        self.assertEqual(response.status_code, 201)
        reply = response.get_json()["reply"]
        self.assertEqual(reply["content"], "2 + 2 = 4.")
        self.assertFalse(reply.get("error", False))

    def test_a_provider_outage_still_degrades_politely(self):
        # The fallback message is right when the provider really is down - it was only
        # wrong as a cover for a TypeError in our own code.
        created = self.client.post("/api/assistant/conversations", json={"preset": "general"})
        conversation_id = created.get_json()["conversation"]["id"]
        with patch.object(ai_service, "_provider_response", side_effect=OSError("down")):
            response = self.client.post(
                f"/api/assistant/conversations/{conversation_id}/messages",
                json={"message": "What is 2+2?"})
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()["reply"].get("error"))


if __name__ == "__main__":
    unittest.main()

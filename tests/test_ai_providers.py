"""The registered providers, exercised through the gateway with stubbed SDK clients.

Gemini has been called live from this codebase (gemini-3.8-flash and gemini-3.5-flash-lite,
2026-10-01); OpenAI's key was valid but the account had no credit, so only its 429 shape is
confirmed; Anthropic has never been called - no key. What these tests verify is everything
*around* the network call: which SDK method each provider reaches, exactly what it is
handed, how its answer and its failures come back, and that a provider without a key is
not offered. docs/AI_ROUTING.md keeps the live-verification status current.
"""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from flask import Flask

from learnova.ai_services import service
from tests.provider_stub import StubNotFoundError


def completion(text='{"ok": true}', finish="stop"):
    return SimpleNamespace(model="served-model", choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason=finish)],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5, total_tokens=16))


def anthropic_message(text='{"ok": true}', stop_reason="end_turn"):
    return SimpleNamespace(model="claude-served", stop_reason=stop_reason,
                           content=[SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=21, output_tokens=7))


def responses_object(text='{"ok": true}'):
    return SimpleNamespace(output_text=text, model="gpt-served",
                           usage=SimpleNamespace(input_tokens=9, output_tokens=4, total_tokens=13))


class ProviderTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask("provider-test", instance_path=tempfile.mkdtemp())
        self.app.config.update(
            SECRET_KEY="k", GROQ_TUTOR_MODEL="openai/gpt-oss-20b",
            GROQ_API_KEY="groq-key", GROQ_BASE_URL="https://api.groq.com/openai/v1",
            OPENAI_API_KEY="openai-key", OPENAI_BASE_URL="https://api.openai.com/v1",
            ANTHROPIC_API_KEY="anthropic-key", ANTHROPIC_BASE_URL="https://api.anthropic.com",
            GEMINI_API_KEY="gemini-key",
            GEMINI_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/")
        self.context = self.app.app_context()
        self.context.push()

    def tearDown(self):
        self.context.pop()

    @staticmethod
    def request(model, **overrides):
        values = dict(model=model, instructions="Answer as JSON.", input="What is 2 + 2?",
                      max_output_tokens=50, temperature=0.2)
        values.update(overrides)
        return values


class RegistryTests(ProviderTestCase):
    def test_all_four_providers_are_registered_and_reachable(self):
        self.assertEqual(sorted(service.PROVIDERS), ["anthropic", "gemini", "groq", "openai"])
        for name, profile in service.PROVIDERS.items():
            self.assertEqual(profile.name, name)
            self.assertTrue(profile.default_base_url, name)
            self.assertTrue(callable(profile.call), name)

    def test_a_provider_without_a_key_is_not_offered(self):
        self.app.config.update(ANTHROPIC_API_KEY="", GEMINI_API_KEY="")
        self.assertEqual(service.available_providers(), ["groq", "openai"])
        with self.assertRaises(service.AIConfigurationError):
            service._provider_response(**self.request("anthropic:claude-x"))

    def test_google_is_a_pointer_to_gemini_not_a_silent_fallback(self):
        with self.assertRaisesRegex(service.AIConfigurationError, "gemini:gemini-x"):
            service.split_model("google:gemini-x")
        self.assertEqual(service.split_model("gemini:gemini-x"), ("gemini", "gemini-x"))

    def test_the_still_planned_providers_keep_their_clear_error(self):
        with self.assertRaisesRegex(service.AIConfigurationError, "not registered"):
            service.split_model("mistral:mistral-large")


class GeminiTests(ProviderTestCase):
    def test_gemini_goes_through_chat_completions_not_responses(self):
        client = MagicMock()
        client.chat.completions.create.return_value = completion()
        with patch.object(service, "OpenAI", return_value=client) as constructor:
            result = service._provider_response(**self.request("gemini:gemini-x", reasoning={"effort": "low"}))
        constructor.assert_called_once_with(
            api_key="gemini-key", base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
        client.responses.create.assert_not_called()
        sent = client.chat.completions.create.call_args.kwargs
        self.assertEqual(sent["model"], "gemini-x", "the provider prefix never reaches the wire")
        self.assertEqual(sent["messages"][0], {"role": "system", "content": "Answer as JSON."})
        self.assertEqual(sent["messages"][1], {"role": "user", "content": "What is 2 + 2?"})
        self.assertEqual(sent["max_tokens"], 50)
        self.assertEqual(sent["reasoning_effort"], "low")
        self.assertEqual(result.output_text, '{"ok": true}')
        self.assertEqual((result.usage.input_tokens, result.usage.output_tokens), (11, 5))

    def test_a_content_filter_finish_is_a_refusal_not_an_empty_success(self):
        client = MagicMock()
        client.chat.completions.create.return_value = completion(text="", finish="content_filter")
        with patch.object(service, "OpenAI", return_value=client):
            with self.assertRaises(service.AIProviderError) as caught:
                service._provider_response(**self.request("gemini:gemini-x"))
        self.assertEqual(caught.exception.category, "provider_refusal")


class AnthropicTests(ProviderTestCase):
    def test_anthropic_gets_system_messages_and_max_tokens_but_never_temperature(self):
        client = MagicMock()
        client.messages.create.return_value = anthropic_message()
        with patch.object(service, "Anthropic", return_value=client) as constructor:
            result = service._provider_response(**self.request("anthropic:claude-x"))
        constructor.assert_called_once_with(api_key="anthropic-key", base_url="https://api.anthropic.com")
        sent = client.messages.create.call_args.kwargs
        self.assertEqual(sent["model"], "claude-x")
        self.assertEqual(sent["system"], "Answer as JSON.")
        self.assertEqual(sent["max_tokens"], 50)
        self.assertEqual(sent["messages"], [{"role": "user", "content": [{"type": "text", "text": "What is 2 + 2?"}]}])
        self.assertNotIn("temperature", sent)
        self.assertEqual(result.output_text, '{"ok": true}')
        self.assertEqual(result.model, "claude-served")
        self.assertEqual(result.usage.total_tokens, 28)

    def test_a_refusal_stop_reason_is_a_named_failure(self):
        client = MagicMock()
        client.messages.create.return_value = anthropic_message(text="", stop_reason="refusal")
        with patch.object(service, "Anthropic", return_value=client):
            with self.assertRaises(service.AIProviderError) as caught:
                service._provider_response(**self.request("anthropic:claude-x"))
        self.assertEqual(caught.exception.category, "provider_refusal")


class OpenAITests(ProviderTestCase):
    def test_openai_keeps_the_responses_api_and_drops_temperature_for_reasoning_models(self):
        client = MagicMock()
        client.responses.create.return_value = responses_object()
        with patch.object(service, "OpenAI", return_value=client) as constructor:
            service._provider_response(**self.request("openai:gpt-5", reasoning={"effort": "low"}))
            service._provider_response(**self.request("openai:gpt-4.1"))
        constructor.assert_called_with(api_key="openai-key", base_url="https://api.openai.com/v1")
        reasoning_call, plain_call = [call.kwargs for call in client.responses.create.call_args_list]
        self.assertNotIn("temperature", reasoning_call)
        self.assertEqual(reasoning_call["reasoning"], {"effort": "low"})
        self.assertEqual(plain_call["temperature"], 0.2)
        client.chat.completions.create.assert_not_called()

    def test_groq_is_untouched(self):
        client = MagicMock()
        client.responses.create.return_value = responses_object()
        with patch.object(service, "OpenAI", return_value=client) as constructor:
            service._provider_response(**self.request("openai/gpt-oss-20b"))
        constructor.assert_called_once_with(api_key="groq-key", base_url="https://api.groq.com/openai/v1")
        self.assertEqual(client.responses.create.call_args.kwargs["temperature"], 0.2)


class FailureCategoryTests(ProviderTestCase):
    def test_overload_and_server_errors_are_the_providers_problem(self):
        class InternalServerError(RuntimeError):
            status_code = 500

        class OverloadedError(RuntimeError):
            status_code = 529

        self.assertEqual(service._failure_details(OverloadedError("529"))[0], "provider_overloaded")
        self.assertEqual(service._failure_details(InternalServerError("500"))[0], "provider_unavailable")
        self.assertEqual(service._failure_details(StubNotFoundError("404"))[0], "model_not_found")

    def test_no_credit_is_a_billing_state_not_a_rate_limit(self):
        # OpenAI answers "You have no credits remaining" with a 429 and type
        # insufficient_quota. Waiting a minute does not help; the category says so.
        class RateLimitError(RuntimeError):
            status_code = 429

        broke = RateLimitError("Error code: 429 - {'error': {'type': 'insufficient_quota', "
                               "'message': 'You have no credits remaining.'}}")
        self.assertEqual(service._failure_details(broke)[0], "provider_quota_exhausted")
        busy = RateLimitError("Error code: 429 - Rate limit reached for requests")
        self.assertEqual(service._failure_details(busy)[0], "provider_rate_limit")
        # Gemini's free tier (seen live, 2026-10-01) uses billing words for a per-minute
        # cap: "exceeded your current quota, please check your plan and billing details
        # ... Quota exceeded for metric: ...free_tier_requests, limit: 5 ... retry in 41s".
        # Ten minutes of cooldown for a 60-second limit would waste the provider.
        gemini_pace = RateLimitError(
            "Error code: 429 - You exceeded your current quota, please check your plan and "
            "billing details. Quota exceeded for metric: generativelanguage.googleapis.com/"
            "generate_content_free_tier_requests, limit: 5. Please retry in 41.5s.")
        self.assertEqual(service._failure_details(gemini_pace)[0], "provider_rate_limit")
        service._PROVIDER_COOLDOWNS.clear()
        service._note_provider_failure("openai", "provider_quota_exhausted")
        self.assertTrue(service._cooling_down("openai"))

    def test_an_anthropic_style_exception_maps_like_an_openai_one(self):
        class RateLimitError(RuntimeError):
            status_code = 429

        class AuthenticationError(RuntimeError):
            status_code = 401

        self.assertEqual(service._failure_details(RateLimitError("x"))[0], "provider_rate_limit")
        self.assertEqual(service._failure_details(AuthenticationError("x"))[0], "authentication_failure")


class QualityOptionTests(ProviderTestCase):
    def test_low_effort_for_every_thinking_model_the_budgets_cannot_afford(self):
        low = {"reasoning": {"effort": "low"}}
        self.assertEqual(service.quality_options("openai/gpt-oss-120b"), low)
        self.assertEqual(service.quality_options("openai:gpt-5"), low)
        self.assertEqual(service.quality_options("gemini:gemini-x"), low)
        self.assertEqual(service.quality_options("openai:gpt-4.1"), {})
        self.assertEqual(service.quality_options("anthropic:claude-x"), {})


class CacheKeyTests(ProviderTestCase):
    def key(self, model):
        return service.request_hash(task_type="tutor_chat", model=model, language="English",
                                    prompt_version="v", provider_input="q", instructions="i")

    def test_a_bare_model_and_its_default_prefixed_form_share_a_key(self):
        self.assertEqual(self.key("openai/gpt-oss-20b"), self.key("groq:openai/gpt-oss-20b"))

    def test_the_same_model_name_at_two_providers_never_shares_a_key(self):
        self.assertNotEqual(self.key("openai:gpt-5"), self.key("gemini:gpt-5"))


if __name__ == "__main__":
    unittest.main()

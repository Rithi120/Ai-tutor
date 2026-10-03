"""Provider adapters: the canonical request reshaped for each provider, and back.

Pure tests, no keys, no network. What they pin is the *shape* each provider receives -
the thing a live test would otherwise be the first to discover. None of these providers
has been called live from this codebase yet; the module and docs say so.
"""

import base64
import json
import unittest
from types import SimpleNamespace

from learnova.ai_services import adapters

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()
TEXT_REQUEST = {
    "model": "some-model", "instructions": "Be brief.\n\nPROMPT_VERSION: x",
    "input": "What is 2 + 2?", "max_output_tokens": 300, "temperature": 0.2,
}
IMAGE_REQUEST = {
    **TEXT_REQUEST,
    "input": [{"role": "user", "content": [
        {"type": "input_text", "text": "Read this page."},
        {"type": "input_image", "image_url": PNG, "detail": "high"},
    ]}],
}


class DataUrlTests(unittest.TestCase):
    def test_a_data_url_is_split_into_media_type_and_payload(self):
        media_type, payload = adapters.parse_data_url(PNG)
        self.assertEqual(media_type, "image/png")
        self.assertEqual(base64.b64decode(payload)[:4], b"\x89PNG")

    def test_non_data_urls_and_bad_payloads_are_rejected(self):
        for bad in ("https://example.com/a.png", "data:image/png,plain", "data:image/png;base64,###",
                    "data:text/plain;base64,aGk="):
            with self.assertRaises(ValueError, msg=bad):
                adapters.parse_data_url(bad)


class InputPartsTests(unittest.TestCase):
    def test_a_string_is_one_text_part(self):
        self.assertEqual(adapters.input_parts("hello"), [adapters.Part("text", text="hello")])
        self.assertEqual(adapters.input_parts(""), [])

    def test_the_one_user_message_form_yields_text_and_image_parts(self):
        parts = adapters.input_parts(IMAGE_REQUEST["input"])
        self.assertEqual([p.kind for p in parts], ["text", "image"])
        self.assertEqual(parts[1].data_url, PNG)

    def test_anything_the_call_sites_never_send_is_refused_not_dropped(self):
        for bad in ([{"role": "assistant", "content": "x"}],
                    [{"role": "user", "content": [{"type": "input_audio", "data": "..."}]}],
                    [{"role": "user", "content": 7}], "not-a-list-but-wrapped" and {"texts": []}):
            with self.assertRaises(ValueError, msg=repr(bad)):
                adapters.input_parts(bad)


class OpenAIResponsesTests(unittest.TestCase):
    def test_a_non_reasoning_model_passes_the_request_through_untouched(self):
        self.assertEqual(adapters.openai_responses_request_from({**TEXT_REQUEST, "model": "gpt-4.1"}),
                         {**TEXT_REQUEST, "model": "gpt-4.1"})

    def test_reasoning_models_lose_temperature_and_keep_reasoning(self):
        for model in ("gpt-5", "gpt-5.5", "gpt-6-astra", "o3", "o4-mini"):
            out = adapters.openai_responses_request_from(
                {**TEXT_REQUEST, "model": model, "reasoning": {"effort": "low"}})
            self.assertNotIn("temperature", out, model)
            self.assertEqual(out["reasoning"], {"effort": "low"})
        self.assertFalse(adapters.supports_temperature("openai", "gpt-5"))
        self.assertTrue(adapters.supports_temperature("openai", "gpt-4.1"))
        self.assertTrue(adapters.supports_temperature("groq", "openai/gpt-oss-20b"))


class ChatCompletionTests(unittest.TestCase):
    def test_instructions_become_a_system_message_and_text_a_plain_user_string(self):
        out = adapters.chat_completion_request_from(TEXT_REQUEST)
        self.assertEqual(out["messages"], [
            {"role": "system", "content": TEXT_REQUEST["instructions"]},
            {"role": "user", "content": "What is 2 + 2?"},
        ])
        self.assertEqual(out["max_tokens"], 300)
        self.assertEqual(out["temperature"], 0.2)
        self.assertNotIn("max_output_tokens", out)
        self.assertNotIn("instructions", out)

    def test_an_image_turn_uses_image_url_parts_with_the_data_url(self):
        out = adapters.chat_completion_request_from(IMAGE_REQUEST)
        user = out["messages"][-1]["content"]
        self.assertEqual(user[0], {"type": "text", "text": "Read this page."})
        self.assertEqual(user[1], {"type": "image_url", "image_url": {"url": PNG}})

    def test_reasoning_effort_is_the_flat_chat_completions_field(self):
        out = adapters.chat_completion_request_from({**TEXT_REQUEST, "reasoning": {"effort": "low"}})
        self.assertEqual(out["reasoning_effort"], "low")
        self.assertNotIn("reasoning", out)

    def test_a_bad_image_fails_here_not_at_the_provider(self):
        broken = {**TEXT_REQUEST, "input": [{"role": "user", "content": [
            {"type": "input_image", "image_url": "https://example.com/x.png"}]}]}
        with self.assertRaises(ValueError):
            adapters.chat_completion_request_from(broken)

    def test_usage_names_are_translated_so_nothing_is_estimated(self):
        completion = SimpleNamespace(
            model="gemini-x", choices=[SimpleNamespace(
                message=SimpleNamespace(content='{"ok": true}'), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30, total_tokens=150))
        result = adapters.chat_completion_to_canonical(completion, "requested")
        self.assertEqual(result.output_text, '{"ok": true}')
        self.assertEqual((result.usage.input_tokens, result.usage.output_tokens, result.usage.total_tokens), (120, 30, 150))
        self.assertEqual(result.model, "gemini-x")
        self.assertFalse(result.refusal)

    def test_a_content_filter_finish_is_a_refusal(self):
        completion = SimpleNamespace(model="m", choices=[SimpleNamespace(
            message=SimpleNamespace(content=""), finish_reason="content_filter")], usage=None)
        self.assertTrue(adapters.chat_completion_to_canonical(completion, "m").refusal)


class AnthropicTests(unittest.TestCase):
    def test_system_is_top_level_and_temperature_is_never_sent(self):
        out = adapters.anthropic_request_from(TEXT_REQUEST)
        self.assertEqual(out["system"], TEXT_REQUEST["instructions"])
        self.assertEqual(out["max_tokens"], 300)
        self.assertEqual(out["messages"], [{"role": "user", "content": [{"type": "text", "text": "What is 2 + 2?"}]}])
        self.assertNotIn("temperature", out)
        self.assertFalse(adapters.supports_temperature("anthropic", "claude-anything"))

    def test_an_image_becomes_a_base64_block_with_its_media_type(self):
        out = adapters.anthropic_request_from(IMAGE_REQUEST)
        blocks = out["messages"][0]["content"]
        self.assertEqual(blocks[0], {"type": "text", "text": "Read this page."})
        self.assertEqual(blocks[1]["type"], "image")
        self.assertEqual(blocks[1]["source"]["type"], "base64")
        self.assertEqual(blocks[1]["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(blocks[1]["source"]["data"])[:4], b"\x89PNG")

    def test_text_blocks_are_joined_thinking_is_skipped_and_usage_is_mapped(self):
        message = SimpleNamespace(
            model="claude-x", stop_reason="end_turn",
            content=[SimpleNamespace(type="thinking", thinking="..."),
                     SimpleNamespace(type="text", text='{"a":'),
                     SimpleNamespace(type="text", text=' 1}')],
            usage=SimpleNamespace(input_tokens=200, output_tokens=40))
        result = adapters.anthropic_message_to_canonical(message, "requested")
        self.assertEqual(json.loads(result.output_text), {"a": 1})
        self.assertEqual((result.usage.input_tokens, result.usage.output_tokens, result.usage.total_tokens), (200, 40, 240))
        self.assertEqual(result.model, "claude-x")
        self.assertFalse(result.refusal)

    def test_a_refusal_stop_reason_is_surfaced(self):
        message = SimpleNamespace(model="m", stop_reason="refusal", content=[], usage=None)
        result = adapters.anthropic_message_to_canonical(message, "m")
        self.assertTrue(result.refusal)
        self.assertEqual(result.output_text, "")


class BoundaryTests(unittest.TestCase):
    def test_the_adapter_module_imports_no_sdk(self):
        import inspect
        source = inspect.getsource(adapters)
        for forbidden in ("import openai", "from openai", "import anthropic", "from anthropic", "from flask", "import flask"):
            self.assertNotIn(forbidden, source, forbidden)


if __name__ == "__main__":
    unittest.main()

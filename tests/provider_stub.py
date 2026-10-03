"""A stand-in for the Groq provider, backed by the sample responses in fixtures/ai/.

The application has no mock AI mode: `learnova.ai_services.service` only knows `cached`
and `live`, and both go through `_provider_response`. Tests that need to exercise the
gateway itself therefore patch that one function, which is also the only place the real
network call happens.

The JSON files under `tests/fixtures/ai/<language>/<scenario>.json` are kept as a
conformance corpus: `valid.json` is one schema-correct sample per task type in English
and German, and each other file is a deliberately broken response used to prove the
validators reject it with the right category.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from learnova.ai_services import service

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "ai"
LANGUAGE_CODES = {"english": "en", "german": "de", "en": "en", "de": "de"}


class StubUsage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 8):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens


class StubResponse:
    """The shape `learnova.ai_services.service._gateway_response` reads."""

    def __init__(self, output_text: str, model: str = "stub-model", usage: Any = None):
        self.output_text = output_text
        self.model = model
        self.usage = usage if usage is not None else StubUsage()


class StubTimeout(TimeoutError):
    """A provider timeout. `_failure_details` categorises it by name: provider_timeout."""


class StubRateLimit(RuntimeError):
    """A provider rate limit. `_failure_details` reads `status_code` to categorise it."""

    status_code = 429


class StubNotFoundError(RuntimeError):
    """A withdrawn model id: the provider answers 404. Named like openai.NotFoundError
    so both the status and the class name route it to `model_not_found`."""

    status_code = 404


class StubBadRequestError(RuntimeError):
    """A request the provider will not accept (a dict where it wants text): 400."""

    status_code = 400


def load_fixture(scenario: str, language: str = "en") -> dict[str, Any]:
    """Read one scenario file, falling back to English when a language has no copy."""

    code = LANGUAGE_CODES.get(str(language).strip().casefold(), "en")
    path = FIXTURE_ROOT / code / f"{scenario}.json"
    if not path.exists():
        path = FIXTURE_ROOT / "en" / f"{scenario}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_entry(task_type: str, scenario: str = "valid", language: str = "en") -> dict[str, Any]:
    """The entry a scenario provides for one task, or its `default` entry."""

    fixture = load_fixture(scenario, language)
    entry = fixture.get(task_type, fixture.get("default", {}))
    return entry if isinstance(entry, dict) else {"output_text": entry}


def fixture_text(task_type: str, scenario: str = "valid", language: str = "en") -> str:
    """The raw response text a scenario would have the provider return."""

    output = fixture_entry(task_type, scenario, language).get("output_text", "")
    return output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)


def _responder(scenario: str):
    """Build a `_provider_response` replacement that replays one scenario.

    The task type and language are recovered from the call itself: the gateway stamps
    `PROMPT_VERSION: <task>:<version>` and `OUTPUT_LANGUAGE: <name>` into the
    instructions, so the stub can serve the right sample without the test repeating it.
    """

    def respond(**kwargs: Any) -> StubResponse:
        instructions = str(kwargs.get("instructions") or "")
        task_type = ""
        language = "en"
        for line in instructions.splitlines():
            if line.startswith("PROMPT_VERSION: "):
                task_type = line.removeprefix("PROMPT_VERSION: ").split(":", 1)[0].strip()
            elif line.startswith("OUTPUT_LANGUAGE: "):
                language = line.removeprefix("OUTPUT_LANGUAGE: ").strip()
        entry = fixture_entry(task_type, scenario, language)
        error = entry.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "Simulated provider failure"))
            if error.get("type") == "timeout":
                raise StubTimeout(message)
            if error.get("type") == "rate_limit":
                raise StubRateLimit(message)
            if error.get("type") == "model_not_found":
                raise StubNotFoundError(message)
            if error.get("type") == "bad_request":
                raise StubBadRequestError(message)
            raise RuntimeError(message)
        output = entry.get("output_text", "")
        usage = entry.get("usage") or {}
        return StubResponse(
            output if isinstance(output, str) else json.dumps(output, ensure_ascii=False),
            model=str(entry.get("model") or "stub-model"),
            usage=StubUsage(int(usage.get("input_tokens") or 10),
                            int(usage.get("output_tokens") or 8)),
        )

    return respond


def stub_provider(scenario: str = "valid"):
    """Patch the single provider boundary to replay one fixture scenario.

    Usage:
        with stub_provider("malformed_json") as provider:
            ...
        provider.call_count  # the stub is a Mock, so calls are still countable
    """

    return patch.object(service, "_provider_response", side_effect=_responder(scenario))

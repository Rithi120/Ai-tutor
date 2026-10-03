"""Cost-safe gateway for every Learnova AI provider request.

Feature modules build prompts. This module exclusively owns provider access,
response caching, private cache partitioning, and sanitized usage accounting.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from flask import current_app, g, has_request_context
from anthropic import Anthropic
from openai import OpenAI

from . import adapters, budgets, routing
from .budgets import BudgetPolicy, LimitSpec, LimitState, UsageTotals
from .contracts import AIValidationError, repair_latex_json, validate_output
from .prompts import PROMPT_VERSIONS, corrective_instruction, output_contract


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
AI_MODES = {"cached", "live"}
SUPPORTED_TASK_TYPES = {
    "lesson_generation",
    "quiz_generation",
    "answer_evaluation",
    "mistake_analysis",
    "answer_diagnosis",
    "diagnosis_verification",
    "question_generation",
    "tutor_chat",
    "translation",
    "ocr_document_recognition",
    "handwriting_region_review",
    "project_section_generation",
    "adaptive_practice",
    "final_exam_generation",
    "final_exam_evaluation",
    "flashcard_generation",
    "flashcard_back_suggestion",
    "flashcard_review",
    "content_moderation",
    "assistant_chat",
    "competency_extraction",
}
_ACCOUNTING_LOCK = threading.Lock()

# Tasks whose answer is a fact about a fixed input rather than a fresh piece of teaching.
# Reading the same page of a scan twice must give the same words, so paying twice for it
# is waste; a tutor reply or a generated lesson is the opposite - repeating it would be a
# bug. Only these two are cached in live mode. (In "cached" mode everything is cached,
# which is that mode's purpose.)
DETERMINISTIC_TASKS = frozenset({"ocr_document_recognition", "handwriting_region_review"})


class AIGatewayError(RuntimeError):
    """Base error for safe, mode-independent AI failures."""


class AIConfigurationError(AIGatewayError):
    """Raised when a potentially billable request is not explicitly allowed."""


class AIRequestLimitError(AIGatewayError):
    """Raised before a call when a request cap or a token budget would be exceeded.

    `scope` names which ceiling: the `user_*`/`session_*`/`development_*` scopes are the
    caller's own allowance and surface as a 429; the `global_*`/`provider_*` scopes mean
    the site or a provider is out of budget and surface as a 503 with a reset time.
    """

    def __init__(self, message: str, *, scope: str = "user_hour",
                 resets_at: datetime | None = None, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.scope = scope
        self.resets_at = resets_at
        self.retry_after_seconds = retry_after_seconds

    @property
    def site_wide(self) -> bool:
        return self.scope in budgets.SITE_SCOPES


class AITokenLimitError(AIGatewayError):
    """Raised before a call when relevant input exceeds its configured task budget."""


class AIProviderError(AIGatewayError):
    """Provider failure with a safe, stable category and no provider payload."""

    def __init__(self, category: str, summary: str):
        self.category = category
        self.safe_summary = summary[:160]
        super().__init__(self.safe_summary)


@dataclass(frozen=True)
class GatewayUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass(frozen=True)
class GatewayResponse:
    output_text: str
    model: str
    usage: GatewayUsage
    request_id: str = ""
    validation: str = "valid"
    # Which provider actually answered, and the router's trace. A fallback is deliberate,
    # but a caller - a smoke test aimed at one provider most of all - must be able to see
    # that it happened rather than read a Groq answer as that provider's.
    provider: str = ""
    routing_reason: str = ""
    # Set when a limit was hit on the way here and a later - slower - candidate answered.
    # The app turns it into "Max limit reached - using a slower AI model" for the student.
    degraded: str = ""


DEFAULT_OUTPUT_TOKEN_BUDGETS = {
    "tutor_chat": 200,
    "answer_evaluation": 250,
    "mistake_analysis": 3000,
    "answer_diagnosis": 2600,
    "diagnosis_verification": 300,
    "question_generation": 1200,
    "translation": 400,
    "lesson_generation": 700,
    "quiz_generation": 600,
    "project_section_generation": 4000,
    "final_exam_generation": 6000,
    "ocr_document_recognition": 1400,
    # Short readings for at most 8 cropped fragments, not a whole page.
    "handwriting_region_review": 900,
    "adaptive_practice": 600,
    "final_exam_evaluation": 600,
    "flashcard_generation": 4000,
    # Three short definitions for one term - deliberately the smallest budget here,
    # because this task runs once per card typed rather than once per set.
    "flashcard_back_suggestion": 400,
    "flashcard_review": 900,
    "content_moderation": 900,
    "assistant_chat": 2000,
    # One row per "Ich kann" statement; a full Kompetenzraster has 20-60 of them.
    "competency_extraction": 3500,
}

DEFAULT_INPUT_TOKEN_BUDGETS = {
    "tutor_chat": 1800,
    "answer_evaluation": 5000,
    "mistake_analysis": 6000,
    "answer_diagnosis": 7000,
    "diagnosis_verification": 4000,
    "question_generation": 6000,
    "translation": 9000,
    "lesson_generation": 9000,
    "quiz_generation": 6000,
    "project_section_generation": 16000,
    "final_exam_generation": 20000,
    "ocr_document_recognition": 8000,
    "handwriting_region_review": 6000,
    "adaptive_practice": 6000,
    "final_exam_evaluation": 8000,
    "flashcard_generation": 10000,
    "flashcard_back_suggestion": 800,
    "flashcard_review": 12000,
    "content_moderation": 12000,
    "assistant_chat": 12000,
    "competency_extraction": 16000,
}


@dataclass(frozen=True)
class ProviderProfile:
    """How to reach one AI provider.

    Adding a provider means adding an entry here and, if its API is not
    Responses-shaped, one `call` function. Nothing outside this module changes: every
    caller names a model, and the model names its provider.
    """

    name: str
    api_key_setting: str
    base_url_setting: str = ""
    default_base_url: str = ""
    # Translates the canonical request (see `_provider_response`) into a real call and
    # returns the provider's own response object. `_gateway_response` normalizes it.
    call: Callable[["ProviderProfile", dict[str, Any]], Any] | None = None


def _openai_compatible_call(profile: "ProviderProfile", request: dict[str, Any]) -> Any:
    """Providers that speak the OpenAI Responses API verbatim: Groq today, OpenAI itself.

    The canonical request shape *is* this API's shape, so there is nothing to translate.
    """

    api_key, base_url = _client_settings(profile)
    return OpenAI(api_key=api_key, base_url=base_url).responses.create(**request)


def _client_settings(profile: "ProviderProfile") -> tuple[str, str]:
    api_key = current_app.config.get(profile.api_key_setting, "")
    if not api_key:
        raise AIConfigurationError(f"{profile.api_key_setting} is not configured.")
    base_url = (current_app.config.get(profile.base_url_setting)
                if profile.base_url_setting else None) or profile.default_base_url
    return api_key, base_url


def _refused(result: adapters.ProviderResult) -> adapters.ProviderResult:
    """A provider that declined to answer is a named failure, never a blank success."""

    if result.refusal:
        raise AIProviderError("provider_refusal", "The AI provider declined to answer this request.")
    return result


def _openai_responses_call(profile: "ProviderProfile", request: dict[str, Any]) -> Any:
    """OpenAI itself: the Responses API, minus what its reasoning models reject."""

    api_key, base_url = _client_settings(profile)
    response = OpenAI(api_key=api_key, base_url=base_url).responses.create(
        **adapters.openai_responses_request_from(request))
    return adapters.responses_to_canonical(response, request["model"])


def _openai_chat_completions_call(profile: "ProviderProfile", request: dict[str, Any]) -> Any:
    """Providers whose OpenAI-compatible endpoint speaks Chat Completions only (Gemini)."""

    api_key, base_url = _client_settings(profile)
    completion = OpenAI(api_key=api_key, base_url=base_url).chat.completions.create(
        **adapters.chat_completion_request_from(request))
    return _refused(adapters.chat_completion_to_canonical(completion, request["model"]))


def _anthropic_messages_call(profile: "ProviderProfile", request: dict[str, Any]) -> Any:
    """Anthropic's Messages API. The SDK's exception classes share the openai SDK's names
    and `.status_code`, so `_failure_details` reads them without special cases."""

    api_key, base_url = _client_settings(profile)
    message = Anthropic(api_key=api_key, base_url=base_url).messages.create(
        **adapters.anthropic_request_from(request))
    return _refused(adapters.anthropic_message_to_canonical(message, request["model"]))


# The provider registry. `groq` stays the default so nothing about existing behaviour
# changes; `openai` is registered because it costs nothing to support - the same SDK, a
# different key and base URL.
#
# A provider whose API is not Responses-shaped (Anthropic's Messages API, for example)
# supplies its own `call`, which receives the canonical request and is responsible for
# translating it: `instructions` -> system prompt, `input` -> the user turn,
# `max_output_tokens` -> that API's output cap, and for returning an object exposing
# `output_text`, `model` and `usage`. `_gateway_response` already tolerates a missing or
# differently-shaped `usage`, so an adapter does not have to fabricate token counts.
PROVIDERS: dict[str, ProviderProfile] = {
    "groq": ProviderProfile(
        name="groq", api_key_setting="GROQ_API_KEY",
        base_url_setting="GROQ_BASE_URL",
        default_base_url="https://api.groq.com/openai/v1",
        call=_openai_compatible_call),
    "openai": ProviderProfile(
        name="openai", api_key_setting="OPENAI_API_KEY",
        base_url_setting="OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        call=_openai_responses_call),
    "anthropic": ProviderProfile(
        name="anthropic", api_key_setting="ANTHROPIC_API_KEY",
        base_url_setting="ANTHROPIC_BASE_URL",
        default_base_url="https://api.anthropic.com",
        call=_anthropic_messages_call),
    # Google's OpenAI-compatible endpoint; it speaks Chat Completions, not Responses.
    "gemini": ProviderProfile(
        name="gemini", api_key_setting="GEMINI_API_KEY",
        base_url_setting="GEMINI_BASE_URL",
        default_base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        call=_openai_chat_completions_call),
}

DEFAULT_PROVIDER = "groq"

# Provider names the project expects to gain but has not registered yet. Naming one in a
# model string is a configuration mistake worth a clear error, not a silent fall back to
# the default provider with an unusable model name attached.
PLANNED_PROVIDERS = frozenset({"azure", "google", "mistral", "ollama"})


def split_model(model: str | None) -> tuple[str, str]:
    """Split "provider:model" into its parts, defaulting to the configured provider.

    A bare model name keeps working and keeps going to the default provider, so every
    existing call site is unaffected. Only a caller that wants a specific provider has to
    say so, and it says so in the one place it already names a model.

    Groq model ids contain "/" but not ":" (openai/gpt-oss-20b), so the separator is
    unambiguous.
    """

    text = str(model or "").strip()
    if ":" in text:
        provider, _, name = text.partition(":")
        provider = provider.strip().casefold()
        if provider in PROVIDERS:
            return provider, name.strip()
        if provider == "google":
            raise AIConfigurationError(
                "Provider 'google' is not registered; Gemini is registered as 'gemini:'. "
                f"Write the model as gemini:{name.strip()}.")
        if provider in PLANNED_PROVIDERS:
            raise AIConfigurationError(
                f"Provider {provider!r} is not registered. Add it to PROVIDERS in "
                "learnova.ai_services.service with a client adapter.")
    return DEFAULT_PROVIDER, text


def _provider_client() -> OpenAI:
    """Kept for callers that want a raw default-provider client."""

    profile = PROVIDERS[DEFAULT_PROVIDER]
    api_key = current_app.config.get(profile.api_key_setting, "")
    if not api_key:
        raise AIConfigurationError(f"{profile.api_key_setting} is not configured.")
    return OpenAI(api_key=api_key, base_url=current_app.config[profile.base_url_setting])


def _provider_response(**kwargs: Any) -> Any:
    """The only external AI network call in the application.

    `kwargs` is the canonical request shape, which is the OpenAI Responses API's shape
    because that is what every call site already speaks. A provider whose API differs
    translates from it rather than the other way round, so adding one never touches a
    caller.
    """

    provider, model = split_model(kwargs.get("model"))
    profile = PROVIDERS[provider]
    if profile.call is None:
        raise AIConfigurationError(
            f"Provider {provider!r} has no client adapter configured.")
    # The provider is addressed by the registry, so the prefix is stripped before the
    # request goes out: the remote side only knows its own model names.
    return profile.call(profile, {**kwargs, "model": model})


def available_providers() -> list[str]:
    """Providers that are registered *and* have a key configured."""

    return sorted(
        name for name, profile in PROVIDERS.items()
        if current_app.config.get(profile.api_key_setting)
    )


def quality_options(model: str | None = None) -> dict[str, Any]:
    """Reasoning options for the model actually being called.

    Every task budget here is 900-4000 tokens and reasoning tokens count against it, so a
    thinking model is asked for low effort or it returns truncated or empty output. Groq's
    gpt-oss ids, OpenAI's reasoning ids and Gemini all take the setting; the adapters
    translate the key where the API names it differently.
    """

    provider, selected = split_model(model or current_app.config["GROQ_TUTOR_MODEL"])
    if selected.startswith("openai/gpt-oss"):
        return {"reasoning": {"effort": "low"}}
    if provider == "openai" and adapters.is_reasoning_model(selected):
        return {"reasoning": {"effort": "low"}}
    if provider == "gemini":
        return {"reasoning": {"effort": "low"}}
    return {}


def parse_json(payload: str) -> Any:
    text = payload.strip()
    # Reasoning models (e.g. Qwen3) may prepend a <think>…</think> block; drop it.
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    cleaned = repair_latex_json(text)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the outermost JSON object/array if the model wrapped it in prose.
        match = re.search(r"[\{\[].*[\}\]]", cleaned, flags=re.DOTALL)
        if match is None:
            raise
        return json.loads(repair_latex_json(match.group(0)))


def image_data_url(upload: Any) -> str:
    if upload.mimetype not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Please upload a JPG, PNG, or WebP image.")
    payload = upload.read()
    if not payload:
        raise ValueError("The uploaded image is empty.")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{upload.mimetype};base64,{encoded}"


# AI content languages the gateway can be asked to generate in. Interface language can
# be any of the app's 23 locales, but content generation is validated for this set; new
# entries here follow the interface language once their prompts are trusted.
_CONTENT_LANGUAGES = {
    "en": ("English", {"en", "english"}),
    "de": ("German", {"de", "deutsch", "german"}),
    "fr": ("French", {"fr", "french", "français", "francais"}),
    "es": ("Spanish", {"es", "spanish", "español", "espanol"}),
    "it": ("Italian", {"it", "italian", "italiano"}),
    "pt": ("Portuguese", {"pt", "portuguese", "português", "portugues"}),
    "nl": ("Dutch", {"nl", "dutch", "nederlands"}),
    "ar": ("Arabic", {"ar", "arabic", "العربية"}),
}
_LANGUAGE_ALIAS_TO_CODE = {
    alias: code for code, (_name, aliases) in _CONTENT_LANGUAGES.items() for alias in aliases
}


def _normalized_language(value: str | None) -> str:
    normalized = str(value or "en").strip().casefold()
    code = _LANGUAGE_ALIAS_TO_CODE.get(normalized)
    if code:
        return code
    raise AIProviderError("unsupported_language", "The requested AI language is not supported.")


def _language_display_name(code: str) -> str:
    return _CONTENT_LANGUAGES.get(code, ("English", set()))[0]


def _clean_text(value: str) -> str:
    """Compress harmless repetition while retaining formulas and meaningful line breaks."""

    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if normalized.startswith("data:") and ";base64," in normalized:
        return normalized
    lines: list[str] = []
    previous = None
    for raw in normalized.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        fingerprint = line.casefold()
        if fingerprint == previous and len(line) >= 24:
            continue
        lines.append(line)
        previous = fingerprint
    return "\n".join(lines).strip()


def _compress_input(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _compress_input(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_compress_input(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_compress_input(item) for item in value)
    if isinstance(value, str):
        return _clean_text(value)
    return value


def _normalize_for_hash(value: Any) -> Any:
    """Canonicalize requests without ever persisting the canonical input."""

    if isinstance(value, dict):
        return {str(key): _normalize_for_hash(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, bytes):
        return {"binary_sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}
    if isinstance(value, str):
        cleaned = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        if cleaned.startswith("data:") and ";base64," in cleaned:
            return {
                "data_url_sha256": hashlib.sha256(cleaned.encode("utf-8")).hexdigest(),
                "characters": len(cleaned),
            }
        return cleaned
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _private_partition(private_scope: str | int | None) -> str:
    if private_scope is None:
        return "shared"
    secret = str(current_app.config.get("SECRET_KEY", "learnova-local-only")).encode("utf-8")
    return hmac.new(secret, str(private_scope).encode("utf-8"), hashlib.sha256).hexdigest()


def _anonymous_reference(value: str | int | None, *, label: str) -> str:
    if value is None:
        return "anonymous"
    secret = str(current_app.config.get("SECRET_KEY", "learnova-local-only")).encode("utf-8")
    digest = hmac.new(secret, f"{label}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:20]


def request_hash(
    *,
    task_type: str,
    model: str,
    language: str,
    prompt_version: str,
    provider_input: Any,
    instructions: Any = None,
    private_scope: str | int | None = None,
    validation_context: Any = None,
) -> str:
    # "x" and "groq:x" are the same request, and a future change of default provider must
    # not serve another provider's cached answer - so the key carries both halves.
    provider, bare_model = split_model(model)
    canonical = {
        "task_type": task_type,
        "model": f"{provider}:{bare_model}",
        "language": _normalized_language(language),
        "prompt_version": prompt_version,
        "input": _normalize_for_hash(provider_input),
        "instructions": _normalize_for_hash(instructions),
        "validation_context": _normalize_for_hash(validation_context),
        "private_partition": _private_partition(private_scope),
    }
    serialized = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> Path:
    root = Path(current_app.config["AI_CACHE_DIR"])
    return root / key[:2] / f"{key}.json"


def _cache_expired(payload: dict[str, Any]) -> bool:
    """Whether a cache entry is older than AI_CACHE_TTL_DAYS. 0 means entries never expire.

    `created_at` was written from the start and never read; a prompt change already
    invalidates entries through the key, so the TTL only has to cover the case the key
    cannot see - the world moving on under an unchanged prompt.
    """

    days = int(current_app.config.get("AI_CACHE_TTL_DAYS", 30) or 0)
    if days <= 0:
        return False
    try:
        created = datetime.fromisoformat(str(payload.get("created_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return True
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - created > timedelta(days=days)


def _read_cache(key: str) -> GatewayResponse | None:
    path = _cache_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if _cache_expired(payload):
            return None
        usage = payload.get("usage") or {}
        return GatewayResponse(
            output_text=str(payload["output_text"]),
            model=str(payload.get("model") or "cached"),
            usage=GatewayUsage(
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
            ),
        )
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_cache(key: str, response: GatewayResponse, metadata: dict[str, Any]) -> None:
    path = _cache_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
        "output_text": response.output_text,
        "model": response.model,
        "usage": asdict(response.usage),
    }
    # Unique per writer: two threads finishing the same key at once used to share one
    # ".tmp" name, and the second replace() could fail or swap in a half-written file.
    temporary = path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _estimate_tokens(value: Any) -> int:
    if isinstance(value, str) and value.startswith("data:") and ";base64," in value:
        # Images are provider-tokenized differently; a conservative fixed estimate prevents
        # a multi-megabyte base64 string from being treated as free while avoiding false blocks.
        return 1500
    if isinstance(value, dict):
        return max(1, sum(_estimate_tokens(key) + _estimate_tokens(item) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return max(1, sum(_estimate_tokens(item) for item in value))
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return max(1, (len(serialized) + 3) // 4)


def _task_budget(task_type: str, direction: str) -> int:
    defaults = DEFAULT_INPUT_TOKEN_BUDGETS if direction == "input" else DEFAULT_OUTPUT_TOKEN_BUDGETS
    name = f"AI_{task_type.upper()}_MAX_{direction.upper()}_TOKENS"
    return max(1, int(current_app.config.get(name, defaults[task_type])))


def _read_usage_records() -> list[dict[str, Any]]:
    path_value = str(current_app.config.get("AI_USAGE_PATH", "")).strip()
    if not path_value:
        return []
    try:
        lines = Path(path_value).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    records = []
    for line in lines[-10000:]:
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                records.append(value)
        except json.JSONDecodeError:
            continue
    return records


def _record_timestamp(record: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


# ---- the usage ledger ------------------------------------------------------------------
#
# Budgets can only be as good as the record they are checked against. The JSONL log that
# used to be that record is re-parsed on every request, capped at its last 10 000 lines,
# shared by nothing outside this process, and lives on a disk that does not survive a
# deploy. So the gateway now talks to a *ledger* - an interface with two implementations:
# the JSONL file (the default, so nothing changes for tests or a bare checkout) and a
# database table that the application registers at startup.
#
# Whichever ledger is in use, every provider call is bracketed by reserve() and settle():
# the anticipated tokens are counted against the budgets *before* the call, so two
# requests racing for the last of a budget cannot both pass, and the actual tokens replace
# the reservation afterwards. Cache hits and refused requests are recorded for the
# diagnostics page but are never counted - they cost nothing.

@dataclass
class UsageEntry:
    request_id: str
    task_type: str
    provider: str
    model: str
    user_reference: str
    session_reference: str
    event_kind: str = "call"          # call | cache_hit | refused | coalesced
    attempt: int = 1
    reserved_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    success: bool = False
    error_category: str = ""
    duration_ms: float = 0.0
    routing_reason: str = ""
    ai_mode: str = ""
    prompt_version: str = ""
    language: str = ""
    request_hash: str = ""
    settled: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def counts_toward_budgets(self) -> bool:
        return self.event_kind == "call"

    @property
    def effective_tokens(self) -> int:
        return self.total_tokens if self.settled else self.reserved_tokens


class _Reservations:
    """Open reservations held in memory, shared by every ledger in this process.

    This is what makes the budget check atomic against concurrent threads: a reservation
    is visible to the next check the moment it is made, long before anything is written
    to a file or committed to a database.
    """

    def __init__(self) -> None:
        self._open: dict[str, UsageEntry] = {}

    def add(self, entry: UsageEntry) -> str:
        handle = uuid.uuid4().hex
        self._open[handle] = entry
        return handle

    def pop(self, handle: str) -> UsageEntry | None:
        return self._open.pop(handle, None)

    def expire(self, older_than: timedelta, now: datetime) -> list[UsageEntry]:
        stale = [key for key, item in self._open.items() if now - item.timestamp > older_than]
        expired = []
        for key in stale:
            item = self._open.pop(key)
            item.settled, item.reserved_tokens = True, 0
            item.error_category = item.error_category or "abandoned_reservation"
            expired.append(item)
        return expired

    def open_tokens(self, *, provider: str | None, user_reference: str | None,
                    session_reference: str | None, since: datetime | None) -> UsageTotals:
        tokens = requests = 0
        for item in self._open.values():
            if not item.counts_toward_budgets:
                continue
            if provider and item.provider != provider:
                continue
            if user_reference and item.user_reference != user_reference:
                continue
            if session_reference and item.session_reference != session_reference:
                continue
            if since and item.timestamp < since:
                continue
            tokens += item.reserved_tokens
            requests += 1
        return UsageTotals(requests=requests, tokens=tokens)

    def clear(self) -> None:
        self._open.clear()


_RESERVATIONS = _Reservations()


class UsageLedger:
    """What the gateway needs from a usage store. Subclass and register with set_usage_ledger."""

    def reserve(self, entry: UsageEntry) -> str:
        return _RESERVATIONS.add(entry)

    def settle(self, handle: str, *, usage: GatewayUsage, success: bool,
               error_category: str = "", duration_ms: float = 0.0, cost: float = 0.0) -> UsageEntry | None:
        entry = _RESERVATIONS.pop(handle)
        if entry is None:
            return None
        entry.input_tokens, entry.output_tokens = usage.input_tokens, usage.output_tokens
        entry.total_tokens = usage.total_tokens
        entry.reserved_tokens = 0
        entry.success, entry.error_category = success, error_category
        entry.duration_ms, entry.cost, entry.settled = duration_ms, cost, True
        self.persist(entry)
        return entry

    def record(self, entry: UsageEntry) -> None:
        entry.settled = True
        self.persist(entry)

    def persist(self, entry: UsageEntry) -> None:
        """Write one settled entry. The default ledger relies on the JSONL line instead."""

    def flush(self) -> None:
        """Write anything buffered. The default ledger buffers nothing."""

    def expire_stale_reservations(self, older_than: timedelta) -> None:
        for entry in _RESERVATIONS.expire(older_than, datetime.now(timezone.utc)):
            self.persist(entry)

    def stored_totals(self, *, provider: str | None, user_reference: str | None,
                      session_reference: str | None, since: datetime | None) -> UsageTotals:
        raise NotImplementedError

    def totals(self, *, provider: str | None = None, user_reference: str | None = None,
               session_reference: str | None = None, since: datetime | None = None) -> UsageTotals:
        stored = self.stored_totals(provider=provider, user_reference=user_reference,
                                    session_reference=session_reference, since=since)
        open_now = _RESERVATIONS.open_tokens(provider=provider, user_reference=user_reference,
                                             session_reference=session_reference, since=since)
        return UsageTotals(requests=stored.requests + open_now.requests,
                           tokens=stored.tokens + open_now.tokens, cost=stored.cost)

    def count_requests(self, *, user_reference: str, since: datetime) -> int:
        raise NotImplementedError

    def count_provider_calls(self, *, provider: str | None, since: datetime) -> int:
        raise NotImplementedError


class JsonlLedger(UsageLedger):
    """The usage log as it has always been: one line per request, read back on demand.

    Only lines that reached a provider count toward token budgets; a cache hit records the
    tokens it *saved*, and counting them would charge the student for not spending them.
    """

    def _records(self) -> list[dict[str, Any]]:
        return _read_usage_records()

    @staticmethod
    def _counts(record: dict[str, Any]) -> bool:
        kind = record.get("event_kind")
        if kind is not None:
            return kind == "call"
        return bool(record.get("provider_called")) and not record.get("cache_hit")

    def stored_totals(self, *, provider, user_reference, session_reference, since) -> UsageTotals:
        requests = tokens = 0
        cost = 0.0
        for record in self._records():
            if not self._counts(record):
                continue
            if provider and str(record.get("provider") or "") != provider:
                continue
            if user_reference and record.get("user_reference") != user_reference:
                continue
            if session_reference and record.get("session_reference") != session_reference:
                continue
            if since and _record_timestamp(record) < since:
                continue
            requests += 1
            tokens += int(record.get("total_tokens") or 0)
            cost += float(record.get("estimated_or_reported_cost") or 0)
        return UsageTotals(requests=requests, tokens=tokens, cost=round(cost, 8))

    def count_requests(self, *, user_reference: str, since: datetime) -> int:
        return sum(
            record.get("user_reference") == user_reference and _record_timestamp(record) >= since
            for record in self._records())

    def count_provider_calls(self, *, provider: str | None, since: datetime) -> int:
        return sum(
            bool(record.get("provider_called"))
            and (not provider or str(record.get("provider") or "") == provider)
            and _record_timestamp(record) >= since
            for record in self._records())


_LEDGER: UsageLedger = JsonlLedger()


def set_usage_ledger(ledger: UsageLedger | None) -> None:
    """Install the ledger budgets are checked against. None restores the JSONL default."""

    global _LEDGER
    _LEDGER = ledger or JsonlLedger()


def usage_ledger() -> UsageLedger:
    return _LEDGER


def budget_policy() -> BudgetPolicy:
    return BudgetPolicy.from_config(current_app.config)


def _limits_enforced() -> bool:
    return not (current_app.testing and not current_app.config.get("AI_ENFORCE_LIMITS", False))


def _assert_usage_limits(user_ref: str, mode: str) -> None:
    """The per-user request counters and the development live cap, as before.

    These run before the cache is consulted, exactly as they always have: they are abuse
    protection, so a cache hit still counts as a request against them.
    """

    if not _limits_enforced():
        return
    ledger = usage_ledger()
    now = datetime.now(timezone.utc)
    hour_ago, day_ago = now - timedelta(hours=1), now - timedelta(days=1)
    hourly_cap = int(current_app.config.get("AI_MAX_REQUESTS_PER_USER_HOUR", 60))
    if ledger.count_requests(user_reference=user_ref, since=hour_ago) >= hourly_cap:
        raise AIRequestLimitError(
            "Hourly AI request limit reached.", scope="user_hour", resets_at=now + timedelta(hours=1),
            retry_after_seconds=3600)
    daily_cap = int(current_app.config.get("AI_MAX_REQUESTS_PER_USER_DAY", 250))
    if ledger.count_requests(user_reference=user_ref, since=day_ago) >= daily_cap:
        raise AIRequestLimitError(
            "Daily AI request limit reached.", scope="user_day", resets_at=now + timedelta(days=1),
            retry_after_seconds=86400)
    if mode in AI_MODES and current_app.config.get("ENV_NAME") == "development":
        live_cap = int(current_app.config.get("AI_MAX_LIVE_REQUESTS_DEVELOPMENT", 1000))
        if ledger.count_provider_calls(provider=None, since=day_ago) >= live_cap:
            raise AIRequestLimitError(
                "Development live-request limit reached.", scope="development_live")


def _reserve_for_call(entry: UsageEntry, *, anticipated_cost: float) -> str:
    """Check every token ceiling for one provider call and reserve its anticipated tokens.

    Under the accounting lock, so the check and the reservation are one step: between
    them no other thread can reserve. This is the whole of "never silently exceed".
    """

    ledger = usage_ledger()
    with _ACCOUNTING_LOCK:
        ttl = timedelta(minutes=int(current_app.config.get("AI_BUDGET_RESERVATION_TTL_MINUTES", 10)))
        ledger.expire_stale_reservations(ttl)
        if _limits_enforced():
            now = datetime.now(timezone.utc)
            policy = budget_policy()
            # The router already refuses to *plan* a paid provider without a cap; this is
            # the same rule for a model an owner named directly in a setting. A provider
            # with no budget has no budget - the call is refused, not quietly billed.
            if not budgets.provider_permitted(policy, entry.provider):
                raise AIRequestLimitError(
                    f"Provider {entry.provider!r} has no budget configured.",
                    scope="provider_not_permitted")
            if entry.session_reference != "anonymous":
                session_cap = int(current_app.config.get("AI_MAX_TOKENS_PER_SESSION", 60000))
                session_used = ledger.totals(session_reference=entry.session_reference).tokens
                if session_used + entry.reserved_tokens > session_cap:
                    raise AIRequestLimitError(
                        "Study-session AI token limit reached.", scope="session_tokens")

            def totals_for(spec: LimitSpec, window_start: datetime) -> UsageTotals:
                if spec.scope.startswith("user_"):
                    return ledger.totals(user_reference=entry.user_reference, since=window_start)
                if spec.scope.startswith("provider_"):
                    return ledger.totals(provider=spec.provider, since=window_start)
                return ledger.totals(since=window_start)

            decision = budgets.evaluate(
                policy, totals_for, provider=entry.provider,
                has_user=entry.user_reference != "anonymous",
                anticipated_tokens=entry.reserved_tokens, anticipated_cost=anticipated_cost, now=now)
            if not decision.allowed and decision.blocking is not None:
                blocking = decision.blocking
                resets_at = blocking.resets_at
                raise AIRequestLimitError(
                    f"AI budget reached: {blocking.spec.label}.", scope=blocking.spec.scope,
                    resets_at=resets_at,
                    retry_after_seconds=budgets.retry_after_seconds(resets_at, now) if resets_at else None)
        return ledger.reserve(entry)


# Identical requests arriving together - a double-click, a retried fetch - share one
# provider call. The second waits on the first's lock, then takes its result if it is
# still fresh. Per process, like the reservations; one gunicorn worker today.
_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Lock] = {}
_RECENT_RESULTS: dict[str, tuple[float, GatewayResponse]] = {}


def _inflight_lock(key: str) -> threading.Lock:
    with _INFLIGHT_LOCK:
        if len(_INFLIGHT) > 1000:
            _INFLIGHT.clear()
        return _INFLIGHT.setdefault(key, threading.Lock())


def _dedup_window() -> float:
    # Off under the test runner unless a test asks for it: hundreds of tests send the
    # same stub request within seconds of each other and expect each to reach the stub.
    if current_app.testing and not current_app.config.get("AI_DEDUP_IN_TESTS", False):
        return 0.0
    return float(current_app.config.get("AI_DEDUP_WINDOW_SECONDS", 15) or 0)


def _recent_result(key: str) -> GatewayResponse | None:
    with _INFLIGHT_LOCK:
        item = _RECENT_RESULTS.get(key)
        if item is None:
            return None
        expires, response = item
        if expires < time.monotonic():
            _RECENT_RESULTS.pop(key, None)
            return None
        return response


def _remember_result(key: str, response: GatewayResponse) -> None:
    window = _dedup_window()
    if window <= 0:
        return
    with _INFLIGHT_LOCK:
        if len(_RECENT_RESULTS) > 1000:
            _RECENT_RESULTS.clear()
        _RECENT_RESULTS[key] = (time.monotonic() + window, response)


# A provider that just answered 429 or 529 is left alone for a minute. Per process, like
# the reservations: a cooldown is a hint that saves a call, not a correctness guarantee.
_PROVIDER_COOLDOWNS: dict[str, float] = {}
# Rate limits are metered per model, so their cooldowns are kept per (provider, model):
# gpt-oss-20b being out of tokens must not take gpt-oss-120b down with it.
_MODEL_COOLDOWNS: dict[tuple[str, str], float] = {}
PROVIDER_COOLDOWN_SECONDS = 60.0
# One corrective retry per request, as before - but on the next candidate when there is
# one (the same-model retry rescued 0 of 7 failed gradings in the usage log).
MAX_CORRECTIVE_RETRIES = 1


PROVIDER_QUOTA_COOLDOWN_SECONDS = 600.0


def _note_provider_failure(provider: str, category: str, model: str = "") -> None:
    if category in {"provider_rate_limit", "provider_overloaded"}:
        if model:
            _MODEL_COOLDOWNS[(provider, model)] = time.monotonic() + PROVIDER_COOLDOWN_SECONDS
        else:
            _PROVIDER_COOLDOWNS[provider] = time.monotonic() + PROVIDER_COOLDOWN_SECONDS
    elif category == "provider_quota_exhausted":
        # Credit does not come back by waiting a minute; stop asking for ten.
        _PROVIDER_COOLDOWNS[provider] = time.monotonic() + PROVIDER_QUOTA_COOLDOWN_SECONDS


def _note_degraded(reason: str, served_model: str, requested_model: str) -> None:
    """Leave a note on the request that a slower model answered because of a limit, so
    the app can tell the student in whatever response shape it is about to send."""

    if not has_request_context():
        return
    g.ai_degraded = {"reason": reason, "model": served_model, "requested": requested_model}


def _model_cooling_down(provider: str, model: str) -> bool:
    return _MODEL_COOLDOWNS.get((provider, model), 0.0) > time.monotonic()


def _cooling_down(provider: str) -> bool:
    """Is anything at this provider cooling down - the account, or any of its models?

    Used for planning paid providers, which are named by one model each, so a model's
    rate limit and the provider's quota both mean "not now".
    """

    now = time.monotonic()
    if _PROVIDER_COOLDOWNS.get(provider, 0.0) > now:
        return True
    return any(until > now for (name, _model), until in _MODEL_COOLDOWNS.items() if name == provider)


def _provider_availability(
    route_policy: routing.RoutingPolicy, anticipated_tokens: int,
) -> dict[str, routing.ProviderAvailability]:
    """Key, budget headroom, rate headroom and cooldown for every provider a plan may use.

    Only the providers named by the premium and emergency settings are evaluated; Groq is
    the floor and is always planned regardless, so there is nothing to look up for it.
    """

    policy = budget_policy()
    ledger = usage_ledger()
    now = datetime.now(timezone.utc)
    named = {routing.split_model(model)[0] for model in route_policy.premium_models.values()}
    if route_policy.fallback_model:
        named.add(routing.split_model(route_policy.fallback_model)[0])
    availability: dict[str, routing.ProviderAvailability] = {}
    for provider in named:
        profile = PROVIDERS.get(provider)
        has_key = bool(profile and current_app.config.get(profile.api_key_setting))
        budget_ok = budgets.provider_permitted(policy, provider)
        if budget_ok and provider != policy.default_provider:
            def totals_for(spec: LimitSpec, window_start: datetime) -> UsageTotals:
                return ledger.totals(provider=spec.provider, since=window_start)
            provider_specs = [spec for spec in budgets.applicable_limits(policy, provider=provider, has_user=False)
                              if spec.scope.startswith("provider_") and spec.unit == "tokens"]
            scoped = budgets.BudgetPolicy(provider_tokens={provider: policy.provider_tokens.get(provider, {})},
                                          default_provider=policy.default_provider)
            budget_ok = budgets.evaluate(
                scoped, totals_for, provider=provider, has_user=False,
                anticipated_tokens=anticipated_tokens, now=now).allowed if provider_specs else True
        per_minute = policy.provider_requests_per_minute.get(provider)
        rate_ok = per_minute is None or ledger.count_provider_calls(
            provider=provider, since=now - timedelta(minutes=1)) < per_minute
        availability[provider] = routing.ProviderAvailability(
            has_key=has_key, budget_ok=budget_ok, rate_ok=rate_ok, cooling_down=_cooling_down(provider))
    return availability


def routing_table() -> list[dict[str, Any]]:
    """Task -> tier -> model, with the premium slot each task may draw on, for the admin page."""

    policy = routing.RoutingPolicy.from_config(current_app.config)
    rows: list[dict[str, Any]] = []
    for task in sorted(SUPPORTED_TASK_TYPES):
        tier = routing.TASK_TIERS[task]
        fallback_tier = routing.GROQ_FALLBACK_TIER.get(tier)
        slot = routing.PREMIUM_SLOTS.get(task)
        rows.append({
            "task_type": task, "tier": tier.value, "model": policy.tier_models.get(tier, ""),
            "premium_slot": slot or "",
            "premium_model": policy.premium_models.get(slot or "", "") if slot else "",
            "fallback": policy.tier_models.get(fallback_tier, "") if fallback_tier else "",
        })
    return rows


def limits_overview(*, user_reference: str | None = None) -> list[dict[str, Any]]:
    """Every configured ceiling with its current usage, for the read-only admin view."""

    policy = budget_policy()
    ledger = usage_ledger()
    now = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for provider in budgets.BUDGET_PROVIDERS:
        for spec in budgets.applicable_limits(policy, provider=provider, has_user=bool(user_reference)):
            if spec.scope.startswith("global") and provider != policy.default_provider:
                continue   # a site-wide ceiling is listed once, not once per provider
            if spec.window is not None:
                window_start, resets_at = budgets.window_bounds(now, spec.window)
            else:
                window_start, resets_at = now - timedelta(minutes=1), None
            if spec.scope.startswith("user_"):
                totals = ledger.totals(user_reference=user_reference, since=window_start)
            elif spec.scope.startswith("provider_"):
                totals = ledger.totals(provider=provider, since=window_start)
            else:
                totals = ledger.totals(since=window_start)
            used = {"tokens": totals.tokens, "requests": totals.requests, "currency": totals.cost}[spec.unit]
            rows.append(LimitState(spec, used, resets_at).as_dict())
    return rows


def _usage_from_response(response: Any, provider_input: Any) -> GatewayUsage:
    usage = getattr(response, "usage", None)
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    if not input_tokens:
        input_tokens = _estimate_tokens(provider_input)
    if not output_tokens:
        output_tokens = _estimate_tokens(getattr(response, "output_text", ""))
    total = int(getattr(usage, "total_tokens", 0) or input_tokens + output_tokens)
    return GatewayUsage(input_tokens, output_tokens, total)


def _gateway_response(response: Any, model: str, provider_input: Any) -> GatewayResponse:
    return GatewayResponse(
        output_text=str(getattr(response, "output_text", "")),
        model=str(getattr(response, "model", "") or model),
        usage=_usage_from_response(response, provider_input),
    )


def _assert_provider_allowed(mode: str) -> None:
    environment = current_app.config.get("ENV_NAME", "development")
    if current_app.testing and not current_app.config.get("RUN_LIVE_AI_TEST", False):
        raise AIConfigurationError("External AI calls are disabled during automated tests.")
    if environment == "development" and not current_app.config.get("ALLOW_LIVE_AI", False):
        raise AIConfigurationError(
            f"AI_MODE={mode} may call the provider. Set ALLOW_LIVE_AI=true explicitly."
        )


def _record_usage(record: dict[str, Any]) -> None:
    path_value = str(current_app.config.get("AI_USAGE_PATH", "")).strip()
    if not path_value:
        return
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ACCOUNTING_LOCK:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    current_app.logger.info("AI request metadata %s", json.dumps(record, separators=(",", ":")))


def _estimated_cost(usage: GatewayUsage, provider: str = budgets.DEFAULT_PROVIDER) -> float:
    return budgets.cost_for(budget_policy(), provider, usage.input_tokens, usage.output_tokens)


def _is_billing_state(text: str) -> bool:
    """Is this 429 about money rather than pace?

    OpenAI says `insufficient_quota` / "You have no credits remaining" - waiting does not
    help. Gemini's free tier says "You exceeded your current quota, please check your plan
    and billing details ... Please retry in 41s" for its 5-requests-per-minute cap - the
    very same words, but a minute later it works. The retry hint and the per-minute metric
    name are the tell; without them, quota/credit/billing wording means billing.
    """

    if "retry in" in text or "per_minute" in text or "per minute" in text             or "free_tier_requests" in text:
        return False
    return "insufficient_quota" in text or "credit" in text or "billing" in text


def _failure_details(error: Exception) -> tuple[str, str]:
    """Map provider/internal exceptions to safe diagnostics without leaking payloads."""

    if isinstance(error, AIValidationError):
        return error.category, error.safe_summary
    if isinstance(error, AITokenLimitError):
        return "token_limit_exceeded", "The request exceeded the configured token budget."
    if isinstance(error, AIRequestLimitError):
        if error.site_wide:
            return "budget_exhausted", "The AI budget for this period has been used up."
        return "request_limit_reached", "The configured AI usage limit was reached."
    if isinstance(error, AIProviderError):
        return error.category, error.safe_summary
    if isinstance(error, AIConfigurationError):
        return "authentication_failure", "The AI provider is not configured or permitted."
    name = type(error).__name__.casefold()
    status = getattr(error, "status_code", None)
    # A withdrawn model id is the most common way a working deployment stops working,
    # and the provider says so plainly with a 404. Mapping it to the generic category
    # below hid exactly that for every call on a retired llama model.
    if status == 404 or "notfound" in name:
        return "model_not_found", "The configured AI model is not available from the provider."
    if status == 529 or "overloaded" in name:
        return "provider_overloaded", "The AI provider is temporarily overloaded."
    if (isinstance(status, int) and status >= 500) or "internalserver" in name:
        return "provider_unavailable", "The AI provider reported an internal error."
    if status in {400, 422} or "badrequest" in name or "unprocessable" in name:
        return "invalid_request", "The AI provider rejected the request as malformed."
    if "timeout" in name:
        return "provider_timeout", "The AI request timed out."
    text = str(error).casefold()
    if status == 429 and _is_billing_state(text):
        # The provider answers "no credits" with a 429 too, but it is a billing state,
        # not a burst to wait out: treating it as a rate limit would retry it every minute
        # and tell students the provider is "busy".
        return "provider_quota_exhausted", "The AI provider account has no credit left."
    if status == 429 or "ratelimit" in name or "rate_limit" in name:
        return "provider_rate_limit", "The AI provider rate limit was reached."
    if status in {401, 403} or "authentication" in name or "permission" in name:
        return "authentication_failure", "The AI provider rejected its credentials."
    if "connection" in name or "network" in name:
        return "network_failure", "The AI provider could not be reached."
    return "internal_application_error", "The AI request failed safely."


def diagnostics_summary() -> dict[str, Any]:
    """Aggregate sanitized accounting data for the protected internal page."""

    records = _read_usage_records()
    total = len(records)
    successful = [record for record in records if record.get("success")]

    def spent(record: dict[str, Any]) -> bool:
        # A cache hit or a coalesced request records the tokens it saved; they were not spent.
        return bool(record.get("provider_called")) and not record.get("cache_hit") \
            and record.get("event_kind") != "coalesced"

    by_task: dict[str, dict[str, Any]] = {}
    by_provider: dict[str, dict[str, Any]] = {}
    for record in records:
        task = str(record.get("task_type") or "unknown")
        item = by_task.setdefault(task, {"requests": 0, "tokens": 0, "cost": 0.0, "failures": 0})
        item["requests"] += 1
        item["tokens"] += int(record.get("total_tokens") or 0) if spent(record) else 0
        item["cost"] = round(item["cost"] + float(record.get("estimated_or_reported_cost") or 0), 8)
        item["failures"] += not bool(record.get("success"))
        name = str(record.get("provider") or "groq")
        bucket = by_provider.setdefault(name, {"requests": 0, "tokens": 0, "cost": 0.0, "failures": 0})
        bucket["requests"] += 1
        bucket["tokens"] += int(record.get("total_tokens") or 0) if spent(record) else 0
        bucket["cost"] = round(bucket["cost"] + float(record.get("estimated_or_reported_cost") or 0), 8)
        bucket["failures"] += not bool(record.get("success"))
    modes = {mode: sum(record.get("ai_mode", record.get("mode")) == mode for record in records) for mode in sorted(AI_MODES)}
    prompt_versions = sorted({str(record.get("prompt_version")) for record in records if record.get("prompt_version")})
    failed = [{
        "request_id": record.get("request_id"), "timestamp": record.get("timestamp"),
        "task_type": record.get("task_type"), "error_category": record.get("error_category"),
        "error_summary": record.get("error_summary"),
    } for record in records if not record.get("success")][-30:]
    return {
        "total_requests": total,
        "cache_hit_rate": round(100 * sum(bool(record.get("cache_hit")) for record in records) / total, 1) if total else 0,
        "validation_failure_rate": round(100 * sum(record.get("validation_result") == "invalid" for record in records) / total, 1) if total else 0,
        "average_duration_ms": round(sum(float(record.get("duration_ms", record.get("request_duration_ms", 0)) or 0) for record in records) / total, 1) if total else 0,
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in records if spent(record)),
        "total_cost": round(sum(float(record.get("estimated_or_reported_cost") or 0) for record in records), 8),
        "failed_requests": total - len(successful),
        "retries": sum(int(record.get("retry_count") or 0) for record in records),
        "modes": modes, "by_task": dict(sorted(by_task.items())),
        "by_provider": dict(sorted(by_provider.items())),
        "limits": limits_overview(),
        "routing": routing_table(),
        "coalesced_requests": sum(record.get("event_kind") == "coalesced" for record in records),
        "most_expensive_tasks": sorted(
            ({"task_type": task, **values} for task, values in by_task.items()),
            key=lambda item: (-item["cost"], -item["tokens"], item["task_type"]),
        )[:10],
        "prompt_versions": prompt_versions, "recent_failures": failed,
    }


def create_response(
    *,
    task_type: str,
    language: str = "en",
    prompt_version: str | None = None,
    private_scope: str | int | None = None,
    validation_context: dict[str, Any] | None = None,
    session_scope: str | int | None = None,
    signals: dict[str, Any] | None = None,
    deep: bool = False,
    preset: str | None = None,
    previous_failure: str | None = None,
    **provider_kwargs: Any,
) -> GatewayResponse:
    """Execute, measure, validate, and cost-control one centralized AI task.

    `signals`, `deep`, `preset` and `previous_failure` describe the request to the router
    (learnova.ai_services.routing) and are never forwarded to a provider.
    """

    if task_type not in SUPPORTED_TASK_TYPES:
        raise ValueError(f"Unsupported AI task type: {task_type}")
    mode = str(current_app.config.get("AI_MODE", "cached")).strip().lower()
    if mode not in AI_MODES:
        raise AIConfigurationError(f"Unsupported AI_MODE {mode!r}")
    prompt_version = prompt_version or PROMPT_VERSIONS[task_type]
    model = str(provider_kwargs.get("model") or "unspecified")
    provider, _bare_model = split_model(model)
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    user_ref = _anonymous_reference(private_scope, label="user")
    session_ref = _anonymous_reference(session_scope, label="session")
    try:
        normalized_language = _normalized_language(language)
    except AIProviderError as error:
        _record_usage({
            "request_id": request_id, "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_hash": hashlib.sha256(f"{request_id}:unsupported-language".encode()).hexdigest(),
            "user_reference": user_ref, "session_reference": session_ref,
            "task_type": task_type, "selected_model": model, "language": "unsupported",
            "prompt_version": prompt_version, "ai_mode": mode, "input_tokens": 0,
            "output_tokens": 0, "total_tokens": 0, "cache_hit": False,
            "cache_status": "miss", "retry_count": 0, "validation_result": "not_run",
            "provider_called": False, "estimated_or_reported_cost": 0,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "success": False, "error_category": error.category,
            "error_summary": error.safe_summary,
        })
        raise
    language_name = _language_display_name(normalized_language)
    validation_context = dict(validation_context or {})
    provider_kwargs = dict(provider_kwargs)
    provider_input = _compress_input(provider_kwargs.get("input"))
    if isinstance(provider_input, dict):
        # The Responses API takes a string or a list of messages. A dict is this
        # application's own shorthand for "here is structured data", and it was going
        # to the provider raw - which rejected it, so every call shaped this way failed.
        # Serialised here, once, rather than at each call site that might forget.
        provider_input = json.dumps(provider_input, ensure_ascii=False)
    provider_kwargs["input"] = provider_input
    contract = output_contract(task_type, language_name, validation_context)
    original_instructions = _clean_text(str(provider_kwargs.get("instructions") or ""))
    provider_kwargs["instructions"] = f"{original_instructions}\n\n{contract}".strip()
    requested_output = int(provider_kwargs.get("max_output_tokens") or _task_budget(task_type, "output"))
    provider_kwargs["max_output_tokens"] = min(requested_output, _task_budget(task_type, "output"))
    input_tokens_estimate = _estimate_tokens({
        "input": provider_input, "instructions": provider_kwargs["instructions"],
    })
    key = request_hash(
        task_type=task_type,
        model=model,
        language=normalized_language,
        prompt_version=prompt_version,
        provider_input=provider_input,
        instructions=provider_kwargs.get("instructions"),
        private_scope=private_scope,
        validation_context=validation_context,
    )
    route_policy = routing.RoutingPolicy.from_config(current_app.config)
    plan = routing.plan_route(
        routing.RoutingContext(
            task_type=task_type, signals=dict(signals or {}), deep=deep, preset=preset,
            previous_failure=previous_failure, requested_model=model),
        route_policy,
        _provider_availability(route_policy, input_tokens_estimate + int(provider_kwargs["max_output_tokens"])),
    )
    cache_hit = False
    provider_called = False
    success = False
    error_category = ""
    error_summary = ""
    validation_result = "not_run"
    retry_count = 0
    response: GatewayResponse | None = None
    accumulated_usage = GatewayUsage()
    answered_by: routing.Candidate | None = None
    routing_trace = routing.routing_reason(plan, None, [])
    coalesced = False

    def add_usage(item: GatewayUsage) -> None:
        nonlocal accumulated_usage
        accumulated_usage = GatewayUsage(
            accumulated_usage.input_tokens + item.input_tokens,
            accumulated_usage.output_tokens + item.output_tokens,
            accumulated_usage.total_tokens + item.total_tokens,
        )

    def validated(item: GatewayResponse) -> GatewayResponse:
        nonlocal validation_result
        try:
            validate_output(
                task_type, item.output_text, prompt_version, validation_context,
                max_characters=int(current_app.config.get("AI_MAX_OUTPUT_CHARACTERS", 200000)),
            )
        except AIValidationError:
            validation_result = "invalid"
            raise
        validation_result = "valid"
        return GatewayResponse(item.output_text, item.model, item.usage, request_id, "valid")

    calls_made = 0

    def ledger_entry(kind: str) -> UsageEntry:
        return UsageEntry(
            request_id=request_id, task_type=task_type, provider=provider, model=model,
            user_reference=user_ref, session_reference=session_ref, event_kind=kind,
            ai_mode=mode, prompt_version=prompt_version, language=normalized_language,
            request_hash=key)

    def options_for(kwargs: dict[str, Any], candidate: routing.Candidate) -> dict[str, Any]:
        """The request as this candidate's provider will accept it."""

        shaped = dict(kwargs)
        shaped["model"] = candidate.model
        shaped.pop("reasoning", None)
        shaped.update(quality_options(candidate.model))
        _, bare = split_model(candidate.model)
        if not adapters.supports_temperature(candidate.provider, bare):
            shaped.pop("temperature", None)
        return shaped

    def provider_call(kwargs: dict[str, Any], candidate: routing.Candidate, position: int) -> GatewayResponse:
        nonlocal provider_called, calls_made
        anticipated = input_tokens_estimate + int(kwargs.get("max_output_tokens") or 0)
        calls_made += 1
        entry = ledger_entry("call")
        entry.provider, entry.model = candidate.provider, candidate.model
        entry.attempt, entry.reserved_tokens = calls_made, anticipated
        entry.routing_reason = f"candidate={position + 1}/{len(plan.candidates)}:{candidate.reason}"[:200]
        # Outside the try below on purpose: a refused reservation is a limit error the
        # caller must see as one, not a provider failure.
        handle = _reserve_for_call(
            entry, anticipated_cost=_estimated_cost(
                GatewayUsage(input_tokens_estimate, anticipated - input_tokens_estimate, anticipated),
                candidate.provider))
        provider_called = True
        call_started = time.perf_counter()
        try:
            item = _gateway_response(_provider_response(**kwargs), candidate.model, kwargs.get("input"))
        except Exception as provider_error:
            category, summary = _failure_details(provider_error)
            usage_ledger().settle(
                handle, usage=GatewayUsage(), success=False, error_category=category,
                duration_ms=round((time.perf_counter() - call_started) * 1000, 2))
            raise AIProviderError(category, summary) from provider_error
        add_usage(item.usage)
        usage_ledger().settle(
            handle, usage=item.usage, success=True,
            duration_ms=round((time.perf_counter() - call_started) * 1000, 2),
            cost=_estimated_cost(item.usage, candidate.provider))
        return item

    def coalesced_or_run() -> GatewayResponse:
        """One provider call for identical requests that overlap or repeat within seconds."""

        nonlocal coalesced
        if _dedup_window() <= 0:
            return run_candidates()
        with _inflight_lock(key):
            recent = _recent_result(key)
            if recent is not None:
                coalesced = True
                shared = ledger_entry("coalesced")
                shared.success = True
                usage_ledger().record(shared)
                return GatewayResponse(recent.output_text, recent.model, recent.usage, request_id, recent.validation)
            result = run_candidates()
            _remember_result(key, result)
            return result

    def run_candidates() -> GatewayResponse:
        """Try the plan in order under the call bound. The whole retry policy lives here.

        A provider failure moves to the next candidate - at another provider when the
        failure was provider-wide. A validation failure spends the request's single
        corrective retry on the next candidate, or on the same model only when nothing
        else is left. A limit or budget error stops everything at once.
        """

        nonlocal retry_count, answered_by, routing_trace
        kwargs = dict(provider_kwargs)
        hops: list[str] = []
        last_error: Exception | None = None
        index: int | None = 0
        while index is not None and index < len(plan.candidates) and calls_made < plan.max_calls:
            candidate = plan.candidates[index]
            if _model_cooling_down(candidate.provider, candidate.model) and index + 1 < len(plan.candidates):
                # This model hit its rate limit moments ago; do not spend a call finding
                # that out again while a slower model is waiting right behind it.
                hops.append(f"{candidate.provider}:model_cooldown")
                index += 1
                continue
            try:
                item = provider_call(options_for(kwargs, candidate), candidate, index)
            except AIProviderError as error:
                last_error = error
                hops.append(f"{candidate.provider}:{error.category}")
                _note_provider_failure(candidate.provider, error.category, candidate.model)
                index = routing.next_candidate(plan, index, error.category)
                continue
            try:
                result = validated(item)
            except AIValidationError as error:
                last_error = error
                hops.append(f"{candidate.provider}:{error.category}")
                if retry_count >= MAX_CORRECTIVE_RETRIES:
                    break
                retry_count += 1
                kwargs["instructions"] = corrective_instruction(
                    task_type, language_name, error.safe_summary, validation_context)
                following = routing.next_candidate(plan, index, error.category)
                index = following if following is not None else index
                continue
            answered_by = candidate
            routing_trace = routing.routing_reason(plan, index, hops)
            degraded = next((hop.split(":", 1)[1] for hop in hops
                             if hop.split(":", 1)[1] in routing.LIMIT_FAILURES), "")
            if degraded:
                _note_degraded(degraded, candidate.model, plan.candidates[0].model)
            return GatewayResponse(result.output_text, result.model, result.usage, result.request_id,
                                   result.validation, provider=candidate.provider, routing_reason=routing_trace,
                                   degraded=degraded)
        routing_trace = routing.routing_reason(plan, None, hops)
        if last_error is not None:
            raise last_error
        raise AIProviderError("provider_unavailable", "No permitted AI provider is available right now.")

    try:
        if input_tokens_estimate > _task_budget(task_type, "input"):
            raise AITokenLimitError("The relevant input is too large for this AI task.")
        _assert_usage_limits(user_ref, mode)
        if mode == "cached":
            response = _read_cache(key)
            cache_hit = response is not None
            if response is not None:
                try:
                    response = validated(response)
                except AIValidationError:
                    # An old/invalid cache entry is never returned or overwritten until repaired.
                    cache_hit = False
                    response = None
            if cache_hit and response is not None:
                hit = ledger_entry("cache_hit")
                hit.total_tokens, hit.success = response.usage.total_tokens, True
                usage_ledger().record(hit)
            if response is None:
                _assert_provider_allowed(mode)
                response = coalesced_or_run()
                _write_cache(key, response, {
                    "request_hash": key, "task_type": task_type,
                    "language": normalized_language, "prompt_version": prompt_version,
                    "private": private_scope is not None, "validation": "valid",
                })
        else:
            reusable = task_type in DETERMINISTIC_TASKS
            response = None
            if reusable:
                # A re-scan of an unchanged page used to re-pay in full: live mode never
                # read the cache. Failures are never written, so a genuine retry after a
                # failure still reaches the provider, which is what a retry is for.
                response = _read_cache(key)
                if response is not None:
                    try:
                        response = validated(response)
                        cache_hit = True
                        hit = ledger_entry("cache_hit")
                        hit.total_tokens, hit.success = response.usage.total_tokens, True
                        usage_ledger().record(hit)
                    except AIValidationError:
                        response = None
            # Same shape as the cached-mode branch above: a None here means "ask the
            # provider", and everything after this block sees a real response.
            if response is None:
                _assert_provider_allowed(mode)
                response = coalesced_or_run()
                if reusable:
                    _write_cache(key, response, {
                        "request_hash": key, "task_type": task_type,
                        "language": normalized_language, "prompt_version": prompt_version,
                        "private": private_scope is not None, "validation": "valid",
                    })
        success = True
        return response
    except Exception as error:
        error_category, error_summary = _failure_details(error)
        if isinstance(error, AIValidationError):
            validation_result = "invalid"
        if isinstance(error, (AIRequestLimitError, AITokenLimitError)) and not provider_called:
            refused = ledger_entry("refused")
            refused.error_category = error_category
            usage_ledger().record(refused)
        raise
    finally:
        usage = accumulated_usage if provider_called else (response.usage if response else GatewayUsage())
        _record_usage({
            "request_id": request_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "request_hash": key,
            "user_reference": user_ref,
            "session_reference": session_ref,
            "task_type": task_type,
            "selected_model": response.model if response else model,
            "provider": answered_by.provider if answered_by else provider,
            "routing_reason": routing_trace,
            "event_kind": "refused" if (not provider_called and not cache_hit and not coalesced and not success)
            else ("cache_hit" if cache_hit and not provider_called
                  else ("coalesced" if coalesced and not provider_called else "call")),
            "provider_calls": calls_made,
            "language": normalized_language,
            "prompt_version": prompt_version,
            "ai_mode": mode,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "input_token_estimate": input_tokens_estimate,
            "output_token_budget": provider_kwargs.get("max_output_tokens"),
            "cache_hit": cache_hit,
            "cache_status": "hit" if cache_hit else "miss",
            "retry_count": retry_count,
            "validation_result": validation_result,
            "provider_called": provider_called,
            "estimated_or_reported_cost": _estimated_cost(usage, provider) if provider_called else 0.0,
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "success": success,
            "error_category": error_category,
            "error_summary": error_summary,
        })

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
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from flask import current_app
from openai import OpenAI

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
    "project_section_generation",
    "adaptive_practice",
    "final_exam_generation",
    "final_exam_evaluation",
    "flashcard_generation",
    "flashcard_review",
    "content_moderation",
    "assistant_chat",
}
_ACCOUNTING_LOCK = threading.Lock()


class AIGatewayError(RuntimeError):
    """Base error for safe, mode-independent AI failures."""


class AIConfigurationError(AIGatewayError):
    """Raised when a potentially billable request is not explicitly allowed."""


class AIRequestLimitError(AIGatewayError):
    """Raised before a call when an hourly, daily, development, or session limit is reached."""


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
    "project_section_generation": 1200,
    "final_exam_generation": 1200,
    "ocr_document_recognition": 1400,
    "adaptive_practice": 600,
    "final_exam_evaluation": 600,
    "flashcard_generation": 4000,
    "flashcard_review": 900,
    "content_moderation": 900,
    "assistant_chat": 2000,
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
    "adaptive_practice": 6000,
    "final_exam_evaluation": 8000,
    "flashcard_generation": 10000,
    "flashcard_review": 12000,
    "content_moderation": 12000,
    "assistant_chat": 12000,
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

    api_key = current_app.config.get(profile.api_key_setting, "")
    if not api_key:
        raise AIConfigurationError(f"{profile.api_key_setting} is not configured.")
    base_url = (current_app.config.get(profile.base_url_setting)
                if profile.base_url_setting else None) or profile.default_base_url
    return OpenAI(api_key=api_key, base_url=base_url).responses.create(**request)


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
        call=_openai_compatible_call),
}

DEFAULT_PROVIDER = "groq"

# Provider names the project expects to gain but has not registered yet. Naming one in a
# model string is a configuration mistake worth a clear error, not a silent fall back to
# the default provider with an unusable model name attached.
PLANNED_PROVIDERS = frozenset({"anthropic", "azure", "google", "mistral", "ollama"})


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
    _provider, selected = split_model(model or current_app.config["GROQ_TUTOR_MODEL"])
    return {"reasoning": {"effort": "low"}} if selected.startswith("openai/gpt-oss") else {}


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
    canonical = {
        "task_type": task_type,
        "model": model,
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


def _read_cache(key: str) -> GatewayResponse | None:
    path = _cache_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
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
    temporary = path.with_suffix(".tmp")
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


def _assert_usage_limits(user_ref: str, session_ref: str, mode: str, anticipated_tokens: int = 0) -> None:
    if current_app.testing and not current_app.config.get("AI_ENFORCE_LIMITS", False):
        return
    now = datetime.now(timezone.utc)
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    records = _read_usage_records()

    def timestamp(record: dict[str, Any]) -> datetime:
        try:
            return datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)

    user_records = [record for record in records if record.get("user_reference") == user_ref]
    hourly = sum(timestamp(record) >= hour_ago for record in user_records)
    daily = sum(timestamp(record) >= day_ago for record in user_records)
    if hourly >= int(current_app.config.get("AI_MAX_REQUESTS_PER_USER_HOUR", 60)):
        raise AIRequestLimitError("Hourly AI request limit reached.")
    if daily >= int(current_app.config.get("AI_MAX_REQUESTS_PER_USER_DAY", 250)):
        raise AIRequestLimitError("Daily AI request limit reached.")
    if session_ref != "anonymous":
        session_total = sum(
            int(record.get("total_tokens") or 0)
            for record in records
            if record.get("session_reference") == session_ref
        )
        if session_total + anticipated_tokens > int(current_app.config.get("AI_MAX_TOKENS_PER_SESSION", 20000)):
            raise AIRequestLimitError("Study-session AI token limit reached.")
    if mode in {"live", "cached"} and current_app.config.get("ENV_NAME") == "development":
        live_today = sum(
            record.get("provider_called") and timestamp(record) >= day_ago
            for record in records
        )
        if live_today >= int(current_app.config.get("AI_MAX_LIVE_REQUESTS_DEVELOPMENT", 20)):
            raise AIRequestLimitError("Development live-request limit reached.")


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


def _estimated_cost(usage: GatewayUsage) -> float:
    input_rate = float(current_app.config.get("AI_INPUT_COST_PER_MILLION", 0) or 0)
    output_rate = float(current_app.config.get("AI_OUTPUT_COST_PER_MILLION", 0) or 0)
    return round(
        usage.input_tokens * input_rate / 1_000_000
        + usage.output_tokens * output_rate / 1_000_000,
        8,
    )


def _failure_details(error: Exception) -> tuple[str, str]:
    """Map provider/internal exceptions to safe diagnostics without leaking payloads."""

    if isinstance(error, AIValidationError):
        return error.category, error.safe_summary
    if isinstance(error, AITokenLimitError):
        return "token_limit_exceeded", "The request exceeded the configured token budget."
    if isinstance(error, AIRequestLimitError):
        return "request_limit_reached", "The configured AI usage limit was reached."
    if isinstance(error, AIProviderError):
        return error.category, error.safe_summary
    if isinstance(error, AIConfigurationError):
        return "authentication_failure", "The AI provider is not configured or permitted."
    name = type(error).__name__.casefold()
    status = getattr(error, "status_code", None)
    if "timeout" in name:
        return "provider_timeout", "The AI request timed out."
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
    by_task: dict[str, dict[str, Any]] = {}
    for record in records:
        task = str(record.get("task_type") or "unknown")
        item = by_task.setdefault(task, {"requests": 0, "tokens": 0, "cost": 0.0, "failures": 0})
        item["requests"] += 1
        item["tokens"] += int(record.get("total_tokens") or 0)
        item["cost"] = round(item["cost"] + float(record.get("estimated_or_reported_cost") or 0), 8)
        item["failures"] += not bool(record.get("success"))
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
        "total_tokens": sum(int(record.get("total_tokens") or 0) for record in records),
        "total_cost": round(sum(float(record.get("estimated_or_reported_cost") or 0) for record in records), 8),
        "failed_requests": total - len(successful),
        "retries": sum(int(record.get("retry_count") or 0) for record in records),
        "modes": modes, "by_task": dict(sorted(by_task.items())),
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
    **provider_kwargs: Any,
) -> GatewayResponse:
    """Execute, measure, validate, and cost-control one centralized AI task."""

    if task_type not in SUPPORTED_TASK_TYPES:
        raise ValueError(f"Unsupported AI task type: {task_type}")
    mode = str(current_app.config.get("AI_MODE", "cached")).strip().lower()
    if mode not in AI_MODES:
        raise AIConfigurationError(f"Unsupported AI_MODE {mode!r}")
    prompt_version = prompt_version or PROMPT_VERSIONS[task_type]
    model = str(provider_kwargs.get("model") or "unspecified")
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
    cache_hit = False
    provider_called = False
    success = False
    error_category = ""
    error_summary = ""
    validation_result = "not_run"
    retry_count = 0
    response: GatewayResponse | None = None
    accumulated_usage = GatewayUsage()

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

    def provider_call(kwargs: dict[str, Any]) -> GatewayResponse:
        nonlocal provider_called
        provider_called = True
        try:
            item = _gateway_response(_provider_response(**kwargs), model, kwargs.get("input"))
            add_usage(item.usage)
            return item
        except Exception as provider_error:
            category, summary = _failure_details(provider_error)
            raise AIProviderError(category, summary) from provider_error

    try:
        if input_tokens_estimate > _task_budget(task_type, "input"):
            raise AITokenLimitError("The relevant input is too large for this AI task.")
        _assert_usage_limits(
            user_ref, session_ref, mode,
            input_tokens_estimate + int(provider_kwargs.get("max_output_tokens") or 0),
        )
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
            if response is None:
                _assert_provider_allowed(mode)
                response = provider_call(provider_kwargs)
                try:
                    response = validated(response)
                except AIValidationError as validation_error:
                    retry_count = 1
                    repair_kwargs = dict(provider_kwargs)
                    repair_kwargs["instructions"] = corrective_instruction(
                        task_type, language_name, validation_error.safe_summary, validation_context
                    )
                    response = validated(provider_call(repair_kwargs))
                _write_cache(key, response, {
                    "request_hash": key, "task_type": task_type,
                    "language": normalized_language, "prompt_version": prompt_version,
                    "private": private_scope is not None, "validation": "valid",
                })
        else:
            _assert_provider_allowed(mode)
            response = provider_call(provider_kwargs)
            try:
                response = validated(response)
            except AIValidationError as validation_error:
                retry_count = 1
                repair_kwargs = dict(provider_kwargs)
                repair_kwargs["instructions"] = corrective_instruction(
                    task_type, language_name, validation_error.safe_summary, validation_context
                )
                response = validated(provider_call(repair_kwargs))
        success = True
        return response
    except Exception as error:
        error_category, error_summary = _failure_details(error)
        if isinstance(error, AIValidationError):
            validation_result = "invalid"
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
            "estimated_or_reported_cost": _estimated_cost(usage),
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            "success": success,
            "error_category": error_category,
            "error_summary": error_summary,
        })

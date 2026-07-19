"""Cost-safe gateway for every Learnova AI provider request.

Feature modules build prompts. This module exclusively owns provider access,
mock fixtures, private cache partitioning, and sanitized usage accounting.
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
from typing import Any

from flask import current_app
from openai import OpenAI

from .contracts import AIValidationError, validate_output
from .prompts import PROMPT_VERSIONS, corrective_instruction, output_contract


ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
AI_MODES = {"mock", "cached", "live"}
SUPPORTED_TASK_TYPES = {
    "lesson_generation",
    "quiz_generation",
    "answer_evaluation",
    "tutor_chat",
    "translation",
    "ocr_document_recognition",
    "project_section_generation",
    "adaptive_practice",
    "final_exam_generation",
    "final_exam_evaluation",
}
_ACCOUNTING_LOCK = threading.Lock()


class AIGatewayError(RuntimeError):
    """Base error for safe, mode-independent AI failures."""


class AIConfigurationError(AIGatewayError):
    """Raised when a potentially billable request is not explicitly allowed."""


class AIMockTimeout(AIGatewayError):
    """Deterministic timeout fixture."""


class AIMockRateLimit(AIGatewayError):
    """Deterministic rate-limit fixture."""


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
    "translation": 400,
    "lesson_generation": 700,
    "quiz_generation": 600,
    "project_section_generation": 1200,
    "final_exam_generation": 1200,
    "ocr_document_recognition": 1400,
    "adaptive_practice": 600,
    "final_exam_evaluation": 600,
}

DEFAULT_INPUT_TOKEN_BUDGETS = {
    "tutor_chat": 1800,
    "answer_evaluation": 5000,
    "translation": 9000,
    "lesson_generation": 9000,
    "quiz_generation": 6000,
    "project_section_generation": 16000,
    "final_exam_generation": 20000,
    "ocr_document_recognition": 8000,
    "adaptive_practice": 6000,
    "final_exam_evaluation": 8000,
}


def _provider_client() -> OpenAI:
    api_key = current_app.config.get("GROQ_API_KEY", "")
    if not api_key:
        raise AIConfigurationError("GROQ_API_KEY is not configured.")
    return OpenAI(api_key=api_key, base_url=current_app.config["GROQ_BASE_URL"])


def _provider_response(**kwargs: Any) -> Any:
    """The only external AI network call in the application."""

    return _provider_client().responses.create(**kwargs)


def quality_options(model: str | None = None) -> dict[str, Any]:
    selected = model or current_app.config["GROQ_TUTOR_MODEL"]
    return {"reasoning": {"effort": "low"}} if selected.startswith("openai/gpt-oss") else {}


def parse_json(payload: str) -> Any:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", payload.strip())
    return json.loads(cleaned)


def image_data_url(upload: Any) -> str:
    if upload.mimetype not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Please upload a JPG, PNG, or WebP image.")
    payload = upload.read()
    if not payload:
        raise ValueError("The uploaded image is empty.")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:{upload.mimetype};base64,{encoded}"


def _normalized_language(value: str | None) -> str:
    normalized = str(value or "en").strip().casefold()
    if normalized in {"de", "deutsch", "german"}:
        return "de"
    if normalized in {"en", "english"}:
        return "en"
    raise AIProviderError("unsupported_language", "The requested AI language is not supported.")


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
    fixture_context: Any = None,
    validation_context: Any = None,
) -> str:
    canonical = {
        "task_type": task_type,
        "model": model,
        "language": _normalized_language(language),
        "prompt_version": prompt_version,
        "input": _normalize_for_hash(provider_input),
        "instructions": _normalize_for_hash(instructions),
        "fixture_context": _normalize_for_hash(fixture_context),
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


def _fixture_path(language: str, scenario: str) -> Path:
    root = Path(current_app.config["AI_FIXTURE_DIR"])
    return root / _normalized_language(language) / f"{scenario}.json"


def _apply_fixture_context(
    task_type: str, output: Any, fixture_context: dict[str, Any] | None
) -> Any:
    """Fill sanitized fixture templates with non-secret structural identifiers."""

    context = fixture_context or {}
    if not isinstance(output, dict):
        return output
    if task_type == "translation" and isinstance(context.get("texts"), list):
        output["translations"] = [str(item) for item in context["texts"]]
    elif task_type == "project_section_generation":
        sections = output.get("sections")
        if isinstance(sections, list) and sections:
            wanted = max(1, min(2, int(context.get("section_count") or len(sections))))
            while len(sections) < wanted:
                sections.append(json.loads(json.dumps(sections[0])))
            output["sections"] = sections[:wanted]
            page_ids = [int(item) for item in context.get("source_page_ids", [])]
            for index, section in enumerate(output["sections"], start=1):
                section["title"] = f"{section.get('title', 'Study section')} {index}"
                if page_ids:
                    section["source_page_ids"] = page_ids
    elif task_type == "final_exam_generation":
        questions = output.get("questions")
        if isinstance(questions, list) and questions:
            count = max(1, min(50, int(context.get("question_count") or len(questions))))
            allocation = {
                int(key): int(value) for key, value in (context.get("section_allocation") or {}).items()
            }
            section_ids = [
                section_id for section_id, amount in allocation.items() for _ in range(amount)
            ] or [int(item) for item in context.get("section_ids", [])] or [1]
            page_ids = context.get("source_page_ids") or {}
            supporting = context.get("supporting_text") or {}
            difficulties = [
                difficulty
                for difficulty, amount in (context.get("difficulty_distribution") or {}).items()
                for _ in range(int(amount))
            ] or ["medium"]
            question_types = context.get("question_types") or ["short_answer"]
            template = questions[0]
            generated = []
            for index in range(count):
                item = json.loads(json.dumps(template))
                section_id = section_ids[index % len(section_ids)]
                item["id"] = f"q{index + 1}"
                item["section_id"] = section_id
                item["prompt"] = f"{item.get('prompt', 'Practice question')} ({index + 1})"
                item["source_page_ids"] = page_ids.get(str(section_id), page_ids.get(section_id, [1]))
                item["supporting_text"] = supporting.get(
                    str(section_id), supporting.get(section_id, item.get("supporting_text", "Fixture source"))
                )
                item["difficulty"] = difficulties[index % len(difficulties)]
                item["question_type"] = question_types[index % len(question_types)]
                if item["question_type"] in {"multiple_choice", "matching"}:
                    item["options"] = ["A", "B", "C", "D"]
                    item["expected_answer"] = "A"
                elif item["question_type"] == "true_false":
                    item["options"] = ["True", "False"]
                    item["expected_answer"] = "True"
                else:
                    item["options"] = []
                generated.append(item)
            output["questions"] = generated
    elif task_type == "final_exam_evaluation":
        results = output.get("results")
        if isinstance(results, list) and results:
            template = results[0]
            output["results"] = [
                {**template, "question_id": int(question_id)}
                for question_id in context.get("question_ids", [template.get("question_id", 1)])
            ]
    elif task_type == "adaptive_practice":
        question = output.get("question") or output.get("next_question")
        if isinstance(question, dict):
            if context.get("concept"):
                question["concept"] = str(context["concept"])
                question["concepts"] = [str(context["concept"])]
            if context.get("subject"):
                question["subject"] = str(context["subject"])
            if context.get("question_number") and not context.get("lesson"):
                question["prompt"] = (
                    f"{question.get('prompt', 'Practice question')} "
                    f"({int(context['question_number'])})"
                )
            if context.get("lesson"):
                concept = str(context.get("concept") or question.get("concept") or "Practice concept")
                subject = str(context.get("subject") or question.get("subject") or "General studies")
                return {
                    "lesson_title": f"Adaptive practice: {concept}",
                    "detected_level": "adaptive",
                    "concepts": [{"name": concept, "evidence": "scheduled mastery review"}],
                    "explanation": f"Review {concept} using the saved {subject} learning context.",
                    "worked_example": {
                        "problem": f"Review one {concept} example",
                        "steps": ["Identify the relevant rule.", "Apply it one step at a time."],
                        "answer": "Check each step against the saved material.",
                    },
                    "teacher_tips": ["Explain why each step is valid."],
                    "exceptions": [],
                    "question": question,
                }
    return output


def _mock_response(
    task_type: str,
    language: str,
    model: str,
    fixture_context: dict[str, Any] | None = None,
) -> GatewayResponse:
    latency = max(0, min(5000, int(current_app.config.get("AI_MOCK_LATENCY_MS", 0))))
    if latency:
        time.sleep(latency / 1000)
    scenario = str(current_app.config.get("AI_MOCK_SCENARIO", "valid")).strip() or "valid"
    path = _fixture_path(language, scenario)
    try:
        fixture = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise AIConfigurationError(f"AI mock fixture not found: {path}") from error
    entry = fixture.get(task_type, fixture.get("default", fixture))
    if not isinstance(entry, dict):
        entry = {"output_text": entry}
    error = entry.get("error")
    if isinstance(error, dict):
        error_type = str(error.get("type", "mock_error"))
        message = str(error.get("message", "Simulated AI failure"))
        if error_type == "timeout":
            raise AIMockTimeout(message)
        if error_type == "rate_limit":
            raise AIMockRateLimit(message)
        raise AIGatewayError(message)
    output = entry.get("output_text", "")
    if scenario == "valid":
        output = _apply_fixture_context(task_type, output, fixture_context)
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False)
    usage = entry.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or _estimate_tokens(output))
    return GatewayResponse(
        output_text=output,
        model=str(entry.get("model") or f"mock/{model}"),
        usage=GatewayUsage(input_tokens, output_tokens, input_tokens + output_tokens),
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
    if isinstance(error, AIMockTimeout):
        return "provider_timeout", "The AI request timed out."
    if isinstance(error, AIMockRateLimit):
        return "provider_rate_limit", "The AI provider rate limit was reached."
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
    fixture_context: dict[str, Any] | None = None,
    validation_context: dict[str, Any] | None = None,
    session_scope: str | int | None = None,
    **provider_kwargs: Any,
) -> GatewayResponse:
    """Execute, measure, validate, and cost-control one centralized AI task."""

    if task_type not in SUPPORTED_TASK_TYPES:
        raise ValueError(f"Unsupported AI task type: {task_type}")
    mode = str(current_app.config.get("AI_MODE", "mock")).strip().lower()
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
    language_name = "German" if normalized_language == "de" else "English"
    validation_context = {**(fixture_context or {}), **(validation_context or {})}
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
        fixture_context=fixture_context,
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
        if mode == "mock":
            response = _mock_response(task_type, normalized_language, model, fixture_context)
            if not response.usage.input_tokens:
                estimated_usage = GatewayUsage(
                    input_tokens_estimate, response.usage.output_tokens,
                    input_tokens_estimate + response.usage.output_tokens,
                )
                response = GatewayResponse(response.output_text, response.model, estimated_usage)
            add_usage(response.usage)
            response = validated(response)
        elif mode == "cached":
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
        usage = accumulated_usage if provider_called or mode == "mock" else (response.usage if response else GatewayUsage())
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

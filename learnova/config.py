"""Environment-specific application configuration."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Type


class BaseConfig:
    """Safe defaults shared by every environment."""

    MAX_CONTENT_LENGTH = 40 * 1024 * 1024
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14
    JSON_SORT_KEYS = False
    WTF_CSRF_TIME_LIMIT = 60 * 60 * 4
    RATELIMIT_STORAGE_URI = os.getenv("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_HEADERS_ENABLED = True


class DevelopmentConfig(BaseConfig):
    ENV_NAME = "development"
    DEBUG = os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes"}


class TestingConfig(BaseConfig):
    ENV_NAME = "testing"
    TESTING = True
    WTF_CSRF_ENABLED = False
    RATELIMIT_ENABLED = False


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"
    SESSION_COOKIE_SECURE = True


CONFIGS: dict[str, Type[BaseConfig]] = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def _default_database_uri(instance_path: str) -> str:
    instance = Path(instance_path)
    legacy = instance / "numeri.db"
    branded = instance / "learnova.db"
    return "sqlite:///numeri.db" if legacy.exists() and not branded.exists() else "sqlite:///learnova.db"


def _database_uri(instance_path: str) -> str:
    value = os.getenv("DATABASE_URL") or _default_database_uri(instance_path)
    # Some providers still expose the retired postgres:// alias.
    return "postgresql://" + value[len("postgres://"):] if value.startswith("postgres://") else value


def configure_app(app, environment: str | None = None) -> str:
    """Load one explicit profile and environment-backed runtime values."""

    selected = (environment or os.getenv("APP_ENV") or "development").strip().lower()
    if selected not in CONFIGS:
        raise RuntimeError(f"Unsupported APP_ENV {selected!r}; use development, testing, or production")
    app.config.from_object(CONFIGS[selected])
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri(app.instance_path)
    configured_secret = os.getenv("SECRET_KEY")
    if selected == "production" and not configured_secret:
        raise RuntimeError("SECRET_KEY must be configured in production")
    app.config["SECRET_KEY"] = configured_secret or secrets.token_urlsafe(32)
    app.config["GROQ_API_KEY"] = os.getenv("GROQ_API_KEY", "")
    app.config["GROQ_BASE_URL"] = os.getenv(
        "GROQ_BASE_URL", "https://api.groq.com/openai/v1"
    )
    # Vision/OCR model. Must be a multimodal model the Groq account can access; a
    # decommissioned or unavailable id makes every recognition call 404 and rejects
    # pages. qwen/qwen3.6-27b is the verified image-capable model on this account.
    app.config["GROQ_VISION_MODEL"] = os.getenv(
        "GROQ_VISION_MODEL", "qwen/qwen3.6-27b"
    )
    app.config["GROQ_TUTOR_MODEL"] = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-20b")
    # Dedicated model for deep student-answer analysis; quality is prioritised over cost.
    app.config["GROQ_ANALYSIS_MODEL"] = os.getenv("GROQ_ANALYSIS_MODEL", "llama-3.3-70b-versatile")
    app.config["GROQ_FAST_MODEL"] = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
    # Adaptive diagnostics. Each stage names its own model so a deployment can trade
    # quality for cost per stage; all default to models already proven on this account
    # and none is hardcoded at a call site.
    app.config["GROQ_DIAGNOSIS_MODEL"] = os.getenv(
        "GROQ_DIAGNOSIS_MODEL", app.config["GROQ_ANALYSIS_MODEL"])
    app.config["GROQ_DIAGNOSIS_VERIFY_MODEL"] = os.getenv(
        "GROQ_DIAGNOSIS_VERIFY_MODEL", app.config["GROQ_FAST_MODEL"])
    app.config["GROQ_QUESTION_MODEL"] = os.getenv(
        "GROQ_QUESTION_MODEL", app.config["GROQ_TUTOR_MODEL"])
    # Community moderation runs the cheapest capable model on every submission and
    # re-runs the strongest one only when the deterministic pass or the first
    # classification says the case is genuinely hard. Both are named here so a
    # deployment can trade cost for quality per stage and neither is hardcoded at a
    # call site.
    app.config["GROQ_MODERATION_MODEL"] = os.getenv(
        "GROQ_MODERATION_MODEL", app.config["GROQ_FAST_MODEL"])
    app.config["GROQ_MODERATION_ESCALATION_MODEL"] = os.getenv(
        "GROQ_MODERATION_ESCALATION_MODEL", app.config["GROQ_ANALYSIS_MODEL"])
    # Additional providers for the direct assistant. A model may be written as
    # "provider:model" (openai:gpt-5); a bare name goes to Groq as it always has. A
    # provider with no key configured is simply not offered.
    app.config["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
    app.config["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    # The assistant's default and its optional "think harder" model. Both accept the
    # provider prefix, so switching the assistant to another provider is one env var.
    app.config["ASSISTANT_MODEL"] = os.getenv(
        "ASSISTANT_MODEL", app.config["GROQ_TUTOR_MODEL"])
    app.config["ASSISTANT_DEEP_MODEL"] = os.getenv(
        "ASSISTANT_DEEP_MODEL", app.config["GROQ_ANALYSIS_MODEL"])
    requested_ai_mode = os.getenv("AI_MODE", "").strip().lower()
    if selected == "production":
        if requested_ai_mode != "live":
            raise RuntimeError("Production requires explicit AI_MODE=live")
        ai_mode = "live"
    else:
        # Cached is the safe default outside production: a repeated request is served
        # from disk, and a miss still refuses to reach the provider unless ALLOW_LIVE_AI
        # is set, so no request is billable by accident.
        ai_mode = requested_ai_mode or "cached"
    if ai_mode not in {"cached", "live"}:
        raise RuntimeError("AI_MODE must be cached or live")
    app.config["AI_MODE"] = ai_mode
    app.config["ALLOW_LIVE_AI"] = os.getenv("ALLOW_LIVE_AI", "").lower() in {
        "1", "true", "yes",
    }
    app.config["RUN_LIVE_AI_TEST"] = os.getenv("RUN_LIVE_AI_TEST", "").lower() in {
        "1", "true", "yes",
    }
    app.config["AI_CACHE_DIR"] = os.getenv(
        "AI_CACHE_DIR", str(Path(app.instance_path) / "ai_cache")
    )
    app.config["AI_USAGE_PATH"] = os.getenv(
        "AI_USAGE_PATH", str(Path(app.instance_path) / "ai_usage.jsonl")
    )
    for name in ("AI_INPUT_COST_PER_MILLION", "AI_OUTPUT_COST_PER_MILLION"):
        try:
            app.config[name] = max(0.0, float(os.getenv(name, "0")))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a non-negative number") from error
    integer_settings = {
        "AI_MAX_REQUESTS_PER_USER_HOUR": 60,
        "AI_MAX_REQUESTS_PER_USER_DAY": 250,
        "AI_MAX_LIVE_REQUESTS_DEVELOPMENT": 1000,
        "AI_MAX_TOKENS_PER_SESSION": 60000,
        "AI_MAX_OUTPUT_CHARACTERS": 200000,
    }
    for name, default in integer_settings.items():
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    output_budgets = {
        "TUTOR_CHAT": 350, "ANSWER_EVALUATION": 2200, "MISTAKE_ANALYSIS": 3000, "TRANSLATION": 400,
        "ANSWER_DIAGNOSIS": 2600, "DIAGNOSIS_VERIFICATION": 300, "QUESTION_GENERATION": 1200,
        "LESSON_GENERATION": 2200, "QUIZ_GENERATION": 600,
        "PROJECT_SECTION_GENERATION": 1200, "FINAL_EXAM_GENERATION": 1200,
        # Vision models (e.g. Qwen3) spend hidden reasoning tokens against this budget in
        # the Responses API; too small a cap leaves zero visible output ("response was
        # empty") and rejects readable pages. 4000 fits reasoning + JSON within Groq's
        # free-tier per-request TPM (image input ≈ 2k tokens).
        "OCR_DOCUMENT_RECOGNITION": 4000, "ADAPTIVE_PRACTICE": 2200,
        "FINAL_EXAM_EVALUATION": 600, "FLASHCARD_GENERATION": 4000,
        "FLASHCARD_REVIEW": 900, "CONTENT_MODERATION": 900,
        # Generous on purpose: this is the one surface where a learner asks an open
        # question and expects a real answer rather than a graded snippet.
        "ASSISTANT_CHAT": 2000,
    }
    input_budgets = {
        "TUTOR_CHAT": 1800, "ANSWER_EVALUATION": 5000, "MISTAKE_ANALYSIS": 6000, "TRANSLATION": 9000,
        "ANSWER_DIAGNOSIS": 7000, "DIAGNOSIS_VERIFICATION": 4000, "QUESTION_GENERATION": 6000,
        "LESSON_GENERATION": 9000, "QUIZ_GENERATION": 6000,
        "PROJECT_SECTION_GENERATION": 16000, "FINAL_EXAM_GENERATION": 20000,
        "OCR_DOCUMENT_RECOGNITION": 8000, "ADAPTIVE_PRACTICE": 6000,
        "FINAL_EXAM_EVALUATION": 8000, "FLASHCARD_GENERATION": 10000,
        "FLASHCARD_REVIEW": 12000, "CONTENT_MODERATION": 12000,
        "ASSISTANT_CHAT": 12000,
    }
    for task, default in output_budgets.items():
        name = f"AI_{task}_MAX_OUTPUT_TOKENS"
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    for task, default in input_budgets.items():
        name = f"AI_{task}_MAX_INPUT_TOKENS"
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    app.config["AI_ENFORCE_LIMITS"] = selected != "testing"

    # Diagnostics tuning. The verification threshold buys a second opinion only when the
    # deterministic checks say the first one is genuinely risky; 1.01 disables it entirely.
    try:
        app.config["AI_DIAGNOSIS_VERIFY_RISK_THRESHOLD"] = max(
            0.0, min(1.01, float(os.getenv("AI_DIAGNOSIS_VERIFY_RISK_THRESHOLD", "0.6"))))
    except ValueError as error:
        raise RuntimeError("AI_DIAGNOSIS_VERIFY_RISK_THRESHOLD must be a number from 0 to 1.01") from error
    try:
        app.config["AI_QUESTION_MAX_REGENERATIONS"] = max(
            0, min(3, int(os.getenv("AI_QUESTION_MAX_REGENERATIONS", "1"))))
    except ValueError as error:
        raise RuntimeError("AI_QUESTION_MAX_REGENERATIONS must be an integer from 0 to 3") from error

    # Community moderation tuning. The escalation threshold buys the stronger model
    # only when the cheap pass says the case is genuinely hard; 1.01 disables escalation
    # entirely and 0.0 escalates everything.
    try:
        app.config["MODERATION_ESCALATION_RISK_THRESHOLD"] = max(
            0.0, min(1.01, float(os.getenv("MODERATION_ESCALATION_RISK_THRESHOLD", "0.25"))))
    except ValueError as error:
        raise RuntimeError(
            "MODERATION_ESCALATION_RISK_THRESHOLD must be a number from 0 to 1.01") from error
    # Policy thresholds. These are the numbers most likely to need retuning once real
    # decisions exist to measure, so they are configurable; which dimensions may reject,
    # and that off-topic never rejects, are rules rather than settings and are not.
    moderation_confidences = {
        "MODERATION_REJECT_CONFIDENCE": 0.75,
        "MODERATION_REJECT_CONFIDENCE_SEVERE": 0.55,
        "MODERATION_MIN_ALLOW_CONFIDENCE": 0.40,
    }
    for name, default_value in moderation_confidences.items():
        try:
            app.config[name] = max(0.0, min(1.0, float(os.getenv(name, str(default_value)))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a number from 0 to 1") from error
    if (app.config["MODERATION_REJECT_CONFIDENCE_SEVERE"]
            > app.config["MODERATION_REJECT_CONFIDENCE"]):
        raise RuntimeError(
            "MODERATION_REJECT_CONFIDENCE_SEVERE must not exceed MODERATION_REJECT_CONFIDENCE")
    for name, default in {
        "MODERATION_SAFETY_REPORT_THRESHOLD": 2,
        "MODERATION_TOTAL_REPORT_THRESHOLD": 5,
        # How long a moderation record keeps its quoted spans. The decision, the reason
        # codes and the audit trail are kept; the quotes are what get redacted, because
        # they are the only copy of submitted content the record holds.
        "MODERATION_QUOTE_RETENTION_DAYS": 90,
        "MODERATION_MAX_CONTENT_CHARACTERS": 40000,
        "MODERATION_MAX_REPORTS_PER_USER_DAY": 20,
    }.items():
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    # Direct assistant chat. The context budget is what the conversation history is
    # trimmed to; the reply reserve is held back out of it so the model has room to
    # answer a question that arrives at the very edge of the window.
    for name, default in {
        "ASSISTANT_CONTEXT_TOKEN_BUDGET": 8000,
        "ASSISTANT_REPLY_TOKEN_RESERVE": 2000,
        "ASSISTANT_MAX_MESSAGE_CHARACTERS": 16000,
        "ASSISTANT_MAX_CONVERSATIONS": 200,
        "ASSISTANT_MAX_MESSAGES_PER_CONVERSATION": 400,
    }.items():
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error

    # Reviewer allowlist for the moderation queue, matched against username or email.
    # Empty means nobody can reach the queue, which is the safe default: an unstaffed
    # queue holds content unpublished rather than exposing it to whoever asks.
    app.config["COMMUNITY_MODERATORS"] = {
        item.strip().casefold()
        for item in os.getenv("COMMUNITY_MODERATORS", "").split(",")
        if item.strip()
    }

    import_limits = {
        "MAX_FLASHCARD_PDF_SIZE": 10 * 1024 * 1024,
        "MAX_FLASHCARD_IMAGE_SIZE": 8 * 1024 * 1024,
        "MAX_FLASHCARD_PDF_PAGES": 30,
        "MAX_FLASHCARD_IMAGE_PIXELS": 24_000_000,
        "MAX_FLASHCARD_EXTRACTED_TEXT_LENGTH": 30_000,
        "FLASHCARD_IMPORT_RETENTION_HOURS": 24,
        "FLASHCARD_EXTRACTION_TIMEOUT_SECONDS": 45,
        "MAX_FLASHCARD_IMPORTS_PER_HOUR": 10,
    }
    for name, default in import_limits.items():
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    app.config["FLASHCARD_IMPORT_STORAGE_DIR"] = os.getenv(
        "FLASHCARD_IMPORT_STORAGE_DIR",
        str(Path(app.instance_path) / "flashcard_imports"),
    )

    def _feature_flag(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    # Feature flags.
    is_production = selected == "production"
    app.config["FEATURE_PRIVATE_FLASHCARDS"] = _feature_flag("FEATURE_PRIVATE_FLASHCARDS", True)
    # The evidence-based diagnostics engine. When off, answers fall back to the previous
    # mistake-analysis path, so the feature can be disabled without losing grading.
    app.config["FEATURE_ADAPTIVE_DIAGNOSTICS"] = _feature_flag("FEATURE_ADAPTIVE_DIAGNOSTICS", True)
    app.config["FEATURE_DIAGNOSTIC_VERIFICATION"] = _feature_flag("FEATURE_DIAGNOSTIC_VERIFICATION", True)
    # The direct assistant. On by default: it is a first-class surface, not an
    # experiment, and it degrades safely when no provider key is configured.
    app.config["FEATURE_ASSISTANT_CHAT"] = _feature_flag("FEATURE_ASSISTANT_CHAT", True)
    app.config["FEATURE_COMMUNITY_LIBRARY"] = _feature_flag(
        "FEATURE_COMMUNITY_LIBRARY", not is_production)
    # Publishing is available in development and testing so the full workflow can be
    # exercised locally. Production stays off unless FEATURE_COMMUNITY_PUBLISHING is
    # set explicitly (render.yaml sets it).
    app.config["FEATURE_COMMUNITY_PUBLISHING"] = _feature_flag(
        "FEATURE_COMMUNITY_PUBLISHING", not is_production)
    # Moderation is on by default: a check that can be switched off by omission is not a
    # check. In production it cannot be switched off at all while publishing is enabled,
    # because the alternative is a public library with no safety gate. Turning it off
    # outside production is allowed so the pre-moderation publish path stays testable.
    app.config["FEATURE_COMMUNITY_MODERATION"] = _feature_flag(
        "FEATURE_COMMUNITY_MODERATION", True)
    if is_production and app.config["FEATURE_COMMUNITY_PUBLISHING"]:
        app.config["FEATURE_COMMUNITY_MODERATION"] = True
    if (app.config["FEATURE_COMMUNITY_PUBLISHING"]
            and app.config["FEATURE_COMMUNITY_MODERATION"]
            and not app.config["COMMUNITY_MODERATORS"]):
        # Not fatal: holding content unpublished is the safe failure, and a deployment
        # may legitimately start before reviewers are named. But an unstaffed queue grows
        # without bound and nobody notices, so it is said out loud at startup.
        app.logger.warning(
            "COMMUNITY_MODERATORS is empty: escalated community content will be held "
            "unpublished and no one can review it. Set COMMUNITY_MODERATORS.")
    app.config["FEATURE_FLASHCARD_PDF_IMPORT"] = (
        _feature_flag("FEATURE_FLASHCARD_PDF_IMPORT", not is_production))
    app.config["FEATURE_FLASHCARD_IMAGE_IMPORT"] = (
        _feature_flag("FEATURE_FLASHCARD_IMAGE_IMPORT", not is_production))
    completed_feature_defaults = not is_production
    for config_name in (
        "FEATURE_FLASHCARD_LEARN_MODE", "FEATURE_FLASHCARD_TEST_MODE",
        "FEATURE_FLASHCARD_MATCH_GAME", "FEATURE_FLASHCARD_BLAST_GAME",
        "FEATURE_FLASHCARD_BLOCKS_GAME", "FEATURE_GAMIFICATION",
        "FEATURE_MISSIONS", "FEATURE_BADGES", "FEATURE_DAILY_GOALS",
        "FEATURE_VOCABULARY_TRAINER",
    ):
        app.config[config_name] = _feature_flag(config_name, completed_feature_defaults)
    app.config["FEATURE_FLASHCARD_GAMES"] = all(app.config[name] for name in (
        "FEATURE_FLASHCARD_MATCH_GAME", "FEATURE_FLASHCARD_BLAST_GAME",
        "FEATURE_FLASHCARD_BLOCKS_GAME",
    ))
    app.config["GAMIFICATION_MIN_DAILY_EVENTS"] = max(
        1, int(os.getenv("GAMIFICATION_MIN_DAILY_EVENTS", "3")))
    app.config["AI_DIAGNOSTICS_ADMINS"] = {
        item.strip().casefold()
        for item in os.getenv("AI_DIAGNOSTICS_ADMINS", "").split(",")
        if item.strip()
    }
    for name, default in {
        "LESSON_TOKEN_LIMIT": 2200,
        "ANSWER_TOKEN_LIMIT": 2200,
        "CHAT_TOKEN_LIMIT": 350,
        "TRANSLATE_TOKEN_LIMIT": 2500,
        "PROJECT_TOKEN_LIMIT": 5000,
        "FLASHCARD_TOKEN_LIMIT": 4000,
        "REVIEW_TOKEN_LIMIT": 900,
    }.items():
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    return selected

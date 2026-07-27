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
    app.config["GROQ_VISION_MODEL"] = os.getenv(
        "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"
    )
    app.config["GROQ_TUTOR_MODEL"] = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-20b")
    app.config["GROQ_FAST_MODEL"] = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
    requested_ai_mode = os.getenv("AI_MODE", "").strip().lower()
    if selected == "production":
        if requested_ai_mode != "live":
            raise RuntimeError("Production requires explicit AI_MODE=live")
        ai_mode = "live"
    else:
        ai_mode = requested_ai_mode or "mock"
    if ai_mode not in {"mock", "cached", "live"}:
        raise RuntimeError("AI_MODE must be mock, cached, or live")
    app.config["AI_MODE"] = ai_mode
    app.config["ALLOW_LIVE_AI"] = os.getenv("ALLOW_LIVE_AI", "").lower() in {
        "1", "true", "yes",
    }
    app.config["RUN_LIVE_AI_TEST"] = os.getenv("RUN_LIVE_AI_TEST", "").lower() in {
        "1", "true", "yes",
    }
    app.config["AI_MOCK_SCENARIO"] = os.getenv("AI_MOCK_SCENARIO", "valid")
    try:
        app.config["AI_MOCK_LATENCY_MS"] = max(
            0, min(5000, int(os.getenv("AI_MOCK_LATENCY_MS", "0")))
        )
    except ValueError as error:
        raise RuntimeError("AI_MOCK_LATENCY_MS must be an integer from 0 to 5000") from error
    app.config["AI_CACHE_DIR"] = os.getenv(
        "AI_CACHE_DIR", str(Path(app.instance_path) / "ai_cache")
    )
    app.config["AI_USAGE_PATH"] = os.getenv(
        "AI_USAGE_PATH", str(Path(app.instance_path) / "ai_usage.jsonl")
    )
    app.config["AI_FIXTURE_DIR"] = os.getenv(
        "AI_FIXTURE_DIR", str(Path(app.root_path) / "tests" / "fixtures" / "ai")
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
        "TUTOR_CHAT": 350, "ANSWER_EVALUATION": 2200, "TRANSLATION": 400,
        "LESSON_GENERATION": 2200, "QUIZ_GENERATION": 600,
        "PROJECT_SECTION_GENERATION": 1200, "FINAL_EXAM_GENERATION": 1200,
        "OCR_DOCUMENT_RECOGNITION": 1400, "ADAPTIVE_PRACTICE": 2200,
        "FINAL_EXAM_EVALUATION": 600, "FLASHCARD_GENERATION": 4000,
        "FLASHCARD_REVIEW": 900,
    }
    input_budgets = {
        "TUTOR_CHAT": 1800, "ANSWER_EVALUATION": 5000, "TRANSLATION": 9000,
        "LESSON_GENERATION": 9000, "QUIZ_GENERATION": 6000,
        "PROJECT_SECTION_GENERATION": 16000, "FINAL_EXAM_GENERATION": 20000,
        "OCR_DOCUMENT_RECOGNITION": 8000, "ADAPTIVE_PRACTICE": 6000,
        "FINAL_EXAM_EVALUATION": 8000, "FLASHCARD_GENERATION": 10000,
        "FLASHCARD_REVIEW": 12000,
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

    # Feature flags. Community publishing stays disabled until reporting, auto-hide,
    # moderation, and content checks exist; it is never enabled in production yet.
    is_production = selected == "production"
    app.config["FEATURE_PRIVATE_FLASHCARDS"] = _feature_flag("FEATURE_PRIVATE_FLASHCARDS", True)
    app.config["FEATURE_COMMUNITY_LIBRARY"] = _feature_flag(
        "FEATURE_COMMUNITY_LIBRARY", not is_production)
    app.config["FEATURE_COMMUNITY_PUBLISHING"] = _feature_flag(
        "FEATURE_COMMUNITY_PUBLISHING", selected == "testing")
    app.config["FEATURE_COMMUNITY_MODERATION"] = (
        False if is_production else _feature_flag("FEATURE_COMMUNITY_MODERATION", False))
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

"""Environment-specific application configuration."""

from __future__ import annotations

import os
import re
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


def _configure_database(app) -> None:
    """Engine options and boot-time migration safety. The pool options are PostgreSQL only;
    hidden bound parameters apply to every dialect.

    Must run before `db.init_app(app)`, which reads SQLALCHEMY_ENGINE_OPTIONS once. Nothing
    here connects to the database.
    """

    def bounded(name: str, default: int, low: int, high: int) -> int:
        raw = os.getenv(name, str(default)).strip() or str(default)
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError(f"{name} must be a whole number") from error
        if not low <= value <= high:
            raise RuntimeError(f"{name} must be between {low} and {high}")
        return value

    # Every migration transaction in ensure_database() starts with SET LOCAL lock_timeout,
    # so a boot that would queue behind a long request gives up (rolled back, retried by the
    # next start) instead of freezing the table for everyone. 0 = off. learnova/db_ops.py.
    app.config["DB_MIGRATION_LOCK_TIMEOUT_MS"] = bounded("DB_MIGRATION_LOCK_TIMEOUT_MS", 10000, 0, 600_000)
    # Hosted PostgreSQL drops idle connections; the first request after a quiet spell then
    # failed on a dead pooled connection. Pre-ping revalidates on checkout, recycle replaces
    # connections older than this many seconds. 0 = no recycling (pre-ping stays on).
    app.config["DB_POOL_RECYCLE_SECONDS"] = bounded("DB_POOL_RECYCLE_SECONDS", 280, 0, 86_400)
    options = dict(app.config.get("SQLALCHEMY_ENGINE_OPTIONS") or {})
    # Every dialect: a failed statement's error - and every log line or error report made from
    # it - shows the SQL but not its bound parameters, which carry student content (answers,
    # notes, names, e-mail addresses).
    options["hide_parameters"] = True
    if str(app.config.get("SQLALCHEMY_DATABASE_URI", "")).startswith("postgresql"):
        options["pool_pre_ping"] = True
        if app.config["DB_POOL_RECYCLE_SECONDS"]:
            options["pool_recycle"] = app.config["DB_POOL_RECYCLE_SECONDS"]
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = options


# Model ids Groq has withdrawn from this account, as observed. Append, never remove: a
# name that comes back can be un-retired by the next measurement, but a name that is
# gone and not listed here fails silently for every student.
RETIRED_GROQ_MODELS = frozenset({
    "llama-3.1-8b-instant",            # 404 model_not_found from 2026-07-31
    "llama-3.3-70b-versatile",         # 404 model_not_found from 2026-07-31
    "qwen/qwen3.6-27b",                # withdrawn by 2026-10-01; qwen3.8-27b replaced it
    "meta-llama/llama-4-scout-17b-16e-instruct",  # 404 model_not_found from 2026-07-27
})


def _bare_model(value):
    """The provider's own model id, with any "provider:" routing prefix removed."""

    text = str(value or "").strip()
    return text.partition(":")[2].strip() if ":" in text else text


def _configure_vision(app, selected: str) -> None:
    """Image intake and reading (learnova/vision, docs/VISION.md). All optional."""

    def bounded(name: str, default: int, low: int, high: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError(f"{name} must be a whole number") from error
        if not low <= value <= high:
            raise RuntimeError(f"{name} must be between {low} and {high}")
        return value

    # What a student may upload, per image, before anything is decoded.
    app.config["VISION_MAX_UPLOAD_BYTES"] = bounded("VISION_MAX_UPLOAD_BYTES", 15 * 1024 * 1024, 1_000_000, 40 * 1024 * 1024)
    app.config["VISION_MAX_PIXELS"] = bounded("VISION_MAX_PIXELS", 40_000_000, 1_000_000, 80_000_000)
    # What is kept (enough for a close-up re-read of one word) and what a provider gets.
    # Groq documents 4 MB for a base64 image; 2.9 MB raw is 3.87 MB once encoded.
    app.config["VISION_STORAGE_EDGE_PX"] = bounded("VISION_STORAGE_EDGE_PX", 2560, 1024, 6000)
    app.config["VISION_PROVIDER_EDGE_PX"] = bounded("VISION_PROVIDER_EDGE_PX", 2048, 768, 4096)
    app.config["VISION_PROVIDER_MAX_BYTES"] = bounded("VISION_PROVIDER_MAX_BYTES", 2_900_000, 300_000, 15_000_000)
    # Optional second vision model ("provider:model") for when the Groq one fails, and
    # any further models the owner vouches can see images. Both empty by default.
    app.config["AI_VISION_FALLBACK_MODEL"] = os.getenv("AI_VISION_FALLBACK_MODEL", "").strip()
    app.config["AI_VISION_MODELS"] = os.getenv("AI_VISION_MODELS", "").strip()
    # Images in chats and photographed answers.
    app.config["FEATURE_CHAT_IMAGES"] = os.getenv("FEATURE_CHAT_IMAGES", "true").strip().lower() in {
        "1", "true", "yes", "on"}
    # The owner's cap on photos per message. The limit students actually get is lower when
    # the vision model takes fewer images or the request would not fit its token ceiling
    # (app.chat_image_limit): at the default 8000-token ceiling that is 2.
    app.config["CHAT_IMAGES_PER_MESSAGE"] = bounded("CHAT_IMAGES_PER_MESSAGE", 3, 1, 4)
    # How many images GROQ_VISION_MODEL accepts in one request. Groq documents "a maximum of
    # 3 images" for qwen/qwen3.8-27b; a fourth one was refused with a 400.
    app.config["VISION_MAX_IMAGES_PER_REQUEST"] = bounded("VISION_MAX_IMAGES_PER_REQUEST", 3, 1, 4)
    # Tokens one image counts as in every estimate. Groq: "Each image counts as 2048 input
    # tokens"; the old fixed 1500 under-reserved every photo request.
    app.config["AI_IMAGE_TOKEN_ESTIMATE"] = bounded("AI_IMAGE_TOKEN_ESTIMATE", 2048, 256, 16384)
    # Input + output ceiling for a request that carries images (0 = off). 8000 is the
    # tokens-per-minute limit Groq applies to qwen/qwen3.8-27b on the on_demand tier
    # (measured 2026-11-01): a larger request is refused with "Request too large". The
    # gateway lowers the reading's output cap to fit and refuses, before any call, a
    # request that cannot fit its minimum output. Text requests are never clamped.
    app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = bounded("AI_VISION_REQUEST_TOKEN_CEILING", 8000, 0, 2_000_000)
    # Output tokens one image request may ask for (0 = learn it from the provider). Groq's
    # on_demand tier allows qwen/qwen3.8-27b only 1000 output tokens per minute and refuses
    # a request whose expected output is larger; the gateway learns that limit from the
    # refusal and retries once below it, so this is only needed to skip that first refusal.
    app.config["AI_VISION_OUTPUT_TOKEN_CEILING"] = bounded("AI_VISION_OUTPUT_TOKEN_CEILING", 0, 0, 200_000)
    # Admission ceiling for TEXT requests (0 = off): the gateway refuses, before anything is
    # sent, a text request whose input estimate plus its output allowance (below) plus a 5 %
    # margin exceeds it, and lessons fit their notes to it. The output cap of a text request
    # is never lowered. Recommended: the tier's tokens per minute (Groq on_demand: 8000).
    # Off, a lesson refused with HTTP 413 is rebuilt once, smaller by the overshoot the
    # provider named, at the price of that one refused request.
    app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = bounded("AI_TEXT_REQUEST_TOKEN_CEILING", 0, 0, 2_000_000)
    # The share of a text request's output cap (max_output_tokens) counted against the
    # ceiling at admission and when notes are fitted (0..1; 0 = input only, the old rule).
    # Groq on_demand counted input + about 2,350 tokens at max_output_tokens 5000 on
    # 2026-11-02 ("Limit 8000, Requested 10363" for an input estimate of 8,619 and "Requested
    # 9151" for 7,316: Requested ≈ 0.93 × estimate + 2,346, about 47 % of the cap), against
    # input + 300-470 the day before. 0.4 on top of the estimate (which runs about 8 % above
    # Groq's input count) plus the 5 % margin covers that.
    try:
        allowance = float(os.getenv("AI_TEXT_REQUEST_OUTPUT_ALLOWANCE", "0.4"))
    except ValueError as error:
        raise RuntimeError("AI_TEXT_REQUEST_OUTPUT_ALLOWANCE must be a number between 0 and 1") from error
    if not 0.0 <= allowance <= 1.0:
        raise RuntimeError("AI_TEXT_REQUEST_OUTPUT_ALLOWANCE must be a number between 0 and 1")
    app.config["AI_TEXT_REQUEST_OUTPUT_ALLOWANCE"] = allowance
    # Optional per-model ceilings, "provider:model=tokens,..." (a bare model is Groq's). An
    # entry overrides AI_VISION_REQUEST_TOKEN_CEILING for image requests and
    # AI_TEXT_REQUEST_TOKEN_CEILING for text requests to that model. Empty by default: set
    # only measured values.
    raw_ceilings = os.getenv("AI_REQUEST_TOKEN_CEILINGS", "").strip()
    for entry in filter(None, (item.strip() for item in raw_ceilings.split(","))):
        model, separator, tokens = entry.rpartition("=")
        if not separator or not model.strip() or not tokens.strip().isdigit() or int(tokens) < 1:
            raise RuntimeError(
                "AI_REQUEST_TOKEN_CEILINGS must look like provider:model=tokens,provider:model=tokens")
    app.config["AI_REQUEST_TOKEN_CEILINGS"] = raw_ceilings
    # A reading that cannot get at least this many output tokens is refused before any
    # call: a truncated reading is invalid JSON and would only fail after being paid for.
    app.config["AI_IMAGE_UNDERSTANDING_MIN_OUTPUT_TOKENS"] = bounded(
        "AI_IMAGE_UNDERSTANDING_MIN_OUTPUT_TOKENS", 1500, 200, 16_000)
    # Output tokens per photo for a CHAT photo's reading (the compact image_understanding:chat1
    # shape; lessons and answer photos keep AI_IMAGE_UNDERSTANDING_MAX_OUTPUT_TOKENS). 900 is
    # under Groq on_demand's 1000 output tokens per minute for qwen/qwen3.8-27b, so the first
    # reading is never refused for it; a 10-line worksheet takes about 360. Photos of one
    # message are read together with this much each, or one per request once a per-minute
    # output limit is known (AI_VISION_OUTPUT_TOKEN_CEILING or learned) that they would exceed.
    app.config["AI_CHAT_PHOTO_READING_MAX_OUTPUT_TOKENS"] = bounded(
        "AI_CHAT_PHOTO_READING_MAX_OUTPUT_TOKENS", 900, 300, 16_000)
    # Wall clock for reading a message's photos and writing the reply (gunicorn kills a
    # request at 120 s). The optional passes (a turned photo read again, close-ups of
    # unclear words) start only while this many seconds of that budget are left.
    app.config["VISION_TIME_BUDGET_SECONDS"] = bounded("VISION_TIME_BUDGET_SECONDS", 100, 20, 600)
    app.config["VISION_OPTIONAL_PASS_MIN_SECONDS"] = bounded("VISION_OPTIONAL_PASS_MIN_SECONDS", 15, 1, 120)
    # Unsent attachments are deleted after a day; sent ones keep their picture this many
    # days (the reading stays with the conversation until it is deleted).
    app.config["CHAT_ATTACHMENT_RETENTION_DAYS"] = bounded("CHAT_ATTACHMENT_RETENTION_DAYS", 30, 1, 365)
    app.config["CHAT_ATTACHMENT_UPLOADS_PER_HOUR"] = bounded("CHAT_ATTACHMENT_UPLOADS_PER_HOUR", 60, 1, 1000)
    # Turn sideways photos upright with the local OCR engine before any model sees them
    # (landscape photos without a rotation tag only; about half a second each). Off under
    # the test runner, where tests switch it on explicitly. No effect without RapidOCR.
    app.config["VISION_LOCAL_ORIENTATION"] = os.getenv(
        "VISION_LOCAL_ORIENTATION", "false" if selected == "testing" else "true").strip().lower() in {
        "1", "true", "yes", "on"}


ADSENSE_CMPS = {"google_privacy_messaging"}

# Where community reports and review findings are emailed. The address is not a secret;
# the SMTP credentials that send to it are, and they only ever come from the environment.
DEFAULT_COMMUNITY_ALERT_EMAIL = "contact.studylearnova@gmail.com"

# Pages per project, across every file and later additions (learnova/uploads/service.py).
# Twenty was already exceeded by six four-page worksheets, and the whole upload was
# refused. Forty keeps one exam's material in one project. Env: PROJECT_MAX_PAGES.
DEFAULT_PROJECT_MAX_PAGES = 40


def _configure_assistant_chat(app) -> None:
    """Conversation persistence and per-account limits for the assistant chat (docs/ASSISTANT.md)."""

    def bounded(name: str, default: int, low: int, high: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError(f"{name} must be a whole number") from error
        if not low <= value <= high:
            raise RuntimeError(f"{name} must be between {low} and {high}")
        return value

    # How long a send holds its conversation before another send may take it over. Longer
    # than a photo reading (75 s) plus a reply (40 s); a crashed request frees it after this.
    app.config["ASSISTANT_REPLY_CLAIM_SECONDS"] = bounded("ASSISTANT_REPLY_CLAIM_SECONDS", 180, 30, 1800)
    # Per signed-in account (not per address: a school shares one), every assistant route.
    app.config["ASSISTANT_MESSAGES_PER_HOUR"] = bounded("ASSISTANT_MESSAGES_PER_HOUR", 40, 1, 10000)
    app.config["ASSISTANT_CONVERSATIONS_PER_HOUR"] = bounded("ASSISTANT_CONVERSATIONS_PER_HOUR", 30, 1, 10000)
    app.config["ASSISTANT_SEARCH_PER_MINUTE"] = bounded("ASSISTANT_SEARCH_PER_MINUTE", 60, 1, 10000)
    app.config["ASSISTANT_LIST_PAGE_SIZE"] = bounded("ASSISTANT_LIST_PAGE_SIZE", 50, 1, 100)
    # Trusted reverse proxies in front of the app (Werkzeug ProxyFix x_for/x_proto). 0 = off:
    # X-Forwarded-For is only believed when the operator says exactly how many hops set it.
    app.config["PROXY_FIX_X_FOR"] = bounded("PROXY_FIX_X_FOR", 0, 0, 5)
    # Continue Learning (a lesson built from a conversation): how long a build in progress
    # blocks a second build of the same reply (a crashed request frees it after this; longer
    # than a lesson call with its fallback), and how many builds an account may start per hour.
    app.config["CONTINUE_LEARNING_PENDING_SECONDS"] = bounded("CONTINUE_LEARNING_PENDING_SECONDS", 180, 30, 1800)
    app.config["CONTINUE_LEARNING_PER_HOUR"] = bounded("CONTINUE_LEARNING_PER_HOUR", 10, 1, 1000)
    # What of the conversation a Continue Learning lesson request carries: the transcript
    # (estimated tokens) and the stored photo readings (characters). With the lesson
    # instructions and teaching method (~3.5k tokens) the defaults keep one request near
    # 7.5k tokens, inside the 8000 tokens-per-minute a free Groq tier admits per model
    # (measured 2026-11-01); raise them on a paid tier.
    app.config["CONTINUE_LEARNING_TRANSCRIPT_TOKENS"] = bounded("CONTINUE_LEARNING_TRANSCRIPT_TOKENS", 3000, 500, 20000)
    app.config["CONTINUE_LEARNING_PHOTO_CHARACTERS"] = bounded("CONTINUE_LEARNING_PHOTO_CHARACTERS", 4000, 0, 24000)


def _configure_community_alerts(app, selected: str) -> None:
    """Email delivery for administrator alerts, and the cost limits on report re-reviews.

    Email is off until MAIL_SMTP_HOST and MAIL_FROM are set. Without them every alert is
    still stored (status not_configured) and listed on the moderation page, so a report
    is never lost because nobody configured a mail server.
    """

    app.config["COMMUNITY_ALERT_EMAIL"] = (
        os.getenv("COMMUNITY_ALERT_EMAIL", DEFAULT_COMMUNITY_ALERT_EMAIL).strip()
        or DEFAULT_COMMUNITY_ALERT_EMAIL)
    app.config["MAIL_SMTP_HOST"] = os.getenv("MAIL_SMTP_HOST", "").strip()
    app.config["MAIL_SMTP_PORT"] = os.getenv("MAIL_SMTP_PORT", "").strip()
    app.config["MAIL_SMTP_USERNAME"] = os.getenv("MAIL_SMTP_USERNAME", "").strip()
    app.config["MAIL_SMTP_PASSWORD"] = os.getenv("MAIL_SMTP_PASSWORD", "")
    app.config["MAIL_SMTP_SECURITY"] = os.getenv("MAIL_SMTP_SECURITY", "starttls").strip().lower()
    app.config["MAIL_FROM"] = os.getenv("MAIL_FROM", "").strip()
    # Links in alert emails need an absolute address, and a background thread has no
    # request to take one from. Without it the email names the set id and the page path.
    app.config["PUBLIC_BASE_URL"] = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    for name, default in (("MAIL_TIMEOUT_SECONDS", "10"),):
        try:
            app.config[name] = max(1.0, float(os.getenv(name, default)))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive number") from error
    for name, default in (
        ("MAIL_MAX_ATTEMPTS", "3"),
        # Paid re-reviews: per set per day, and across the whole site per hour.
        ("REPORT_REVIEW_MAX_PER_SET_DAY", "5"),
        ("REPORT_REVIEW_MAX_PER_HOUR", "60"),
        # A failed re-review is retried this many times in total before an administrator
        # is told it keeps failing.
        ("REPORT_REVIEW_MAX_ATTEMPTS", "3"),
        # Automatic re-checks of a set whose moderation could not run, before it waits
        # for a person instead.
        ("MODERATION_RETRY_MAX_ATTEMPTS", "3"),
    ):
        try:
            app.config[name] = max(1, int(os.getenv(name, default)))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    # Report re-reviews and email run on a background thread so a report or a publish
    # never waits on a provider or a mail server. Tests run them inline.
    raw = os.getenv("COMMUNITY_BACKGROUND_JOBS")
    app.config["COMMUNITY_BACKGROUND_JOBS"] = (
        selected != "testing" if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"})


# The public contact address (shown on /contact, and where contact form messages are
# forwarded once MAIL_SMTP_HOST and MAIL_FROM are set). Not a secret. docs/CONTACT.md.
DEFAULT_CONTACT_EMAIL = "contact.studylearnova@gmail.com"
# Shown on /about under "Meet the founder". FOUNDER_NAME and FOUNDER_INTRO override it.
DEFAULT_FOUNDER_NAME = "Rithivik Velusamy"


def _configure_contact(app, selected: str) -> None:
    """The contact form (docs/CONTACT.md) and the founder shown on /about."""

    from learnova.contact import is_valid_email

    def bounded(name: str, default: int, low: int, high: int) -> int:
        raw = os.getenv(name, str(default))
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError(f"{name} must be a whole number") from error
        if not low <= value <= high:
            raise RuntimeError(f"{name} must be between {low} and {high}")
        return value

    contact_email = os.getenv("CONTACT_EMAIL", DEFAULT_CONTACT_EMAIL).strip() or DEFAULT_CONTACT_EMAIL
    if not is_valid_email(contact_email):
        raise RuntimeError("CONTACT_EMAIL must be an email address")
    app.config["CONTACT_EMAIL"] = contact_email
    founder = " ".join(os.getenv("FOUNDER_NAME", "").split())[:60].strip()
    app.config["FOUNDER_NAME"] = founder or DEFAULT_FOUNDER_NAME
    # Empty means the translated default introduction. A value set here is shown as
    # written, in every interface language.
    app.config["FOUNDER_INTRO"] = os.getenv("FOUNDER_INTRO", "").strip()[:600]
    # Messages are personal data, often of minors: deleted after this many days whatever
    # their status (on each new message and by `flask contact cleanup`).
    app.config["CONTACT_MESSAGE_RETENTION_DAYS"] = bounded("CONTACT_MESSAGE_RETENTION_DAYS", 90, 7, 730)
    # Steady-abuse ceilings kept in the database, on top of the per-IP limiter (which
    # may see one shared proxy address for everybody).
    app.config["CONTACT_MAX_PER_EMAIL_DAY"] = bounded("CONTACT_MAX_PER_EMAIL_DAY", 5, 1, 100)
    app.config["CONTACT_MAX_PER_HOUR"] = bounded("CONTACT_MAX_PER_HOUR", 30, 1, 1000)
    app.config["CONTACT_DUPLICATE_WINDOW_MINUTES"] = bounded("CONTACT_DUPLICATE_WINDOW_MINUTES", 10, 1, 1440)
    # A form sent sooner than this after it was rendered is treated as automated.
    app.config["CONTACT_MIN_FILL_SECONDS"] = bounded("CONTACT_MIN_FILL_SECONDS", 3, 0, 60)
    # Flask-Limiter string for POST /contact, per client address. Checked here: a value
    # Flask-Limiter cannot parse is only logged at request time and then limits nothing.
    rate_limit = os.getenv("CONTACT_RATE_LIMIT", "10 per hour").strip() or "10 per hour"
    try:
        from limits import parse_many

        parse_many(rate_limit)
    except ValueError as error:
        raise RuntimeError('CONTACT_RATE_LIMIT must be a rate limit such as "10 per hour"') from error
    app.config["CONTACT_RATE_LIMIT"] = rate_limit
    if selected != "testing" and not (app.config["MAIL_SMTP_HOST"] and app.config["MAIL_FROM"]):
        # Not fatal: every message is stored first and listed for the operator.
        app.logger.warning(
            "Contact form email forwarding is not configured (MAIL_SMTP_HOST, MAIL_FROM): "
            "messages are stored and listed at /admin/contact-messages.")


def _configure_ads(app) -> None:
    """Google AdSense (docs/ADSENSE.md). Ads load only once a certified consent message is
    confirmed; the defaults load no ad code at all."""

    def flag(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}

    # The publisher id already used in ads.txt and the original ad tag; not a new one.
    client = os.getenv("ADSENSE_CLIENT_ID", "ca-pub-5205912301427741").strip()
    if client and not re.fullmatch(r"ca-pub-\d{10,20}", client):
        raise RuntimeError("ADSENSE_CLIENT_ID must look like ca-pub-<digits>, or be empty")
    app.config["ADSENSE_CLIENT_ID"] = client
    # Which certified consent management platform shows the EEA/UK/CH consent message.
    # Only Google's own "Privacy & messaging" European regulations message is supported,
    # and only the operator can confirm it is set up in the AdSense account for this site.
    # Empty = no ad code is loaded anywhere.
    cmp = os.getenv("ADSENSE_CMP", "").strip().lower()
    if cmp and cmp not in ADSENSE_CMPS:
        raise RuntimeError(f"ADSENSE_CMP must be one of {sorted(ADSENSE_CMPS)} or empty")
    app.config["ADSENSE_CMP"] = cmp
    # Learnova's students are often under 16, the age of digital consent in Germany. Ads
    # for signed-in students stay off until the operator has decided how to handle that.
    app.config["ADSENSE_SHOW_TO_STUDENTS"] = flag("ADSENSE_SHOW_TO_STUDENTS", False)
    # Part of Google's only supported AdSense policy; off only to test without it.
    app.config["ADSENSE_CSP_UNSAFE_EVAL"] = flag("ADSENSE_CSP_UNSAFE_EVAL", True)
    report_uri = os.getenv("CSP_REPORT_URI", "").strip()
    if report_uri and not report_uri.startswith("https://"):
        raise RuntimeError("CSP_REPORT_URI must be an https:// URL, or empty")
    app.config["CSP_REPORT_URI"] = report_uri


def configure_app(app, environment: str | None = None) -> str:
    """Load one explicit profile and environment-backed runtime values."""

    selected = (environment or os.getenv("APP_ENV") or "development").strip().lower()
    if selected not in CONFIGS:
        raise RuntimeError(f"Unsupported APP_ENV {selected!r}; use development, testing, or production")
    app.config.from_object(CONFIGS[selected])
    app.config["SQLALCHEMY_DATABASE_URI"] = _database_uri(app.instance_path)
    _configure_database(app)
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
    # pages. qwen/qwen3.8-27b is the image-capable model this account was served on
    # 2026-10-01 (qwen3.6 had been withdrawn); RETIRED_GROQ_MODELS below is the list of
    # ids that have already gone this way, so a stale .env is warned about at startup.
    app.config["GROQ_VISION_MODEL"] = os.getenv(
        "GROQ_VISION_MODEL", "qwen/qwen3.8-27b"
    )
    app.config["GROQ_TUTOR_MODEL"] = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-20b")
    # Dedicated model for deep student-answer analysis; quality is prioritised over cost.
    app.config["GROQ_ANALYSIS_MODEL"] = os.getenv("GROQ_ANALYSIS_MODEL", "openai/gpt-oss-120b")
    app.config["GROQ_FAST_MODEL"] = os.getenv("GROQ_FAST_MODEL", "openai/gpt-oss-20b")
    # Adaptive diagnostics. Each stage names its own model so a deployment can trade
    # quality for cost per stage; all default to models already proven on this account
    # and none is hardcoded at a call site.
    app.config["GROQ_DIAGNOSIS_MODEL"] = os.getenv(
        "GROQ_DIAGNOSIS_MODEL", app.config["GROQ_ANALYSIS_MODEL"])
    app.config["GROQ_DIAGNOSIS_VERIFY_MODEL"] = os.getenv(
        "GROQ_DIAGNOSIS_VERIFY_MODEL", app.config["GROQ_FAST_MODEL"])
    app.config["GROQ_QUESTION_MODEL"] = os.getenv(
        "GROQ_QUESTION_MODEL", app.config["GROQ_TUTOR_MODEL"])
    # The lesson tutor chat: the one conversational surface inside a lesson. Teaching
    # quality beats cost here, so it defaults to the strong Groq model rather than the
    # fast one; accepts "provider:model" like the assistant models. Routing still puts
    # the fast model behind it, so a rate limit degrades instead of failing.
    app.config["AI_TUTOR_CHAT_MODEL"] = os.getenv(
        "AI_TUTOR_CHAT_MODEL", app.config["GROQ_ANALYSIS_MODEL"]).strip()
    # Community moderation runs the cheapest capable model on every submission and
    # re-runs the strongest one only when the deterministic pass or the first
    # classification says the case is genuinely hard. Both are named here so a
    # deployment can trade cost for quality per stage and neither is hardcoded at a
    # call site.
    # gpt-oss-safeguard-20b is a policy-following safety classifier rather than a general
    # chat model. Measured against this application's 13-dimension prompt it returned a
    # schema-valid, correctly grounded classification in 1.7 s at confidence 0.95, so it
    # is the first pass; the general 120b model is the second opinion on hard cases.
    app.config["GROQ_MODERATION_MODEL"] = os.getenv(
        "GROQ_MODERATION_MODEL", "openai/gpt-oss-safeguard-20b")
    app.config["GROQ_MODERATION_ESCALATION_MODEL"] = os.getenv(
        "GROQ_MODERATION_ESCALATION_MODEL", app.config["GROQ_ANALYSIS_MODEL"])
    # Additional providers for the direct assistant. A model may be written as
    # "provider:model" (openai:gpt-5); a bare name goes to Groq as it always has. A
    # provider with no key configured is simply not offered.
    app.config["OPENAI_API_KEY"] = os.getenv("OPENAI_API_KEY", "")
    app.config["OPENAI_BASE_URL"] = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    app.config["ANTHROPIC_API_KEY"] = os.getenv("ANTHROPIC_API_KEY", "")
    app.config["ANTHROPIC_BASE_URL"] = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    app.config["GEMINI_API_KEY"] = os.getenv("GEMINI_API_KEY", "")
    # Gemini is reached through Google's OpenAI-compatible endpoint, which speaks Chat
    # Completions (not the Responses API); the adapter translates.
    app.config["GEMINI_BASE_URL"] = os.getenv(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
    # The assistant's default and its optional "think harder" model. Both accept the
    # provider prefix, so switching the assistant to another provider is one env var.
    app.config["ASSISTANT_MODEL"] = os.getenv(
        "ASSISTANT_MODEL", app.config["GROQ_TUTOR_MODEL"])
    app.config["ASSISTANT_DEEP_MODEL"] = os.getenv(
        "ASSISTANT_DEEP_MODEL", app.config["GROQ_ANALYSIS_MODEL"])
    # Per-style assistant models (learnova.assistant.routing): ASSISTANT_MODEL_<PRESET> and
    # ASSISTANT_DEEP_MODEL_<PRESET>, e.g. ASSISTANT_MODEL_RESEARCH=anthropic:claude-sonnet-5.
    # Unset means the global setting above. Students pick the style, never the model.
    from learnova.assistant.routing import preset_model_settings
    for name in preset_model_settings():
        app.config[name] = os.getenv(name, "").strip()
    # Premium models for the hard tasks (learnova.ai_services.routing): grading of final
    # answers and exams, mistake diagnosis, the assistant's "think harder" and research
    # styles. All "provider:model"; unset means premium is off and those tasks stay on
    # Groq. No default names a model at a paid provider - their ids change too often, and
    # a wrong one fails every call. `--task models` in scripts/live_ai_smoke_test.py lists
    # what a key is actually served.
    app.config["AI_PREMIUM_REASONING_MODEL"] = os.getenv("AI_PREMIUM_REASONING_MODEL", "").strip()
    app.config["AI_PREMIUM_GRADING_MODEL"] = os.getenv("AI_PREMIUM_GRADING_MODEL", "").strip()
    # An emergency candidate tried last, after every Groq model, when its provider has a
    # key and budget. Unset means Groq is the floor and a Groq outage is reported as such.
    app.config["AI_FALLBACK_MODEL"] = os.getenv("AI_FALLBACK_MODEL", "").strip()
    # The "slower model" a student is handed when a model's rate limit is hit: tried after
    # the tier's own fallback, before the emergency candidate. Rate limits are metered per
    # model, so another model at the same provider is a real alternative. Unset means the
    # tier fallback (e.g. gpt-oss-120b for gpt-oss-20b) is the slower model.
    app.config["AI_SLOW_MODEL"] = os.getenv("AI_SLOW_MODEL", "").strip()

    # Model ids this account has been served and then lost. A deployment that still pins
    # one (render.yaml, .env) gets every call on it answered 404, which the gateway used
    # to report as a generic internal error; this names the problem at startup instead.
    retired = {
        name: app.config[name] for name in sorted(app.config)
        if name.startswith(("GROQ_", "ASSISTANT_", "AI_PREMIUM_", "AI_FALLBACK_", "AI_SLOW_", "AI_TUTOR_")) and name.endswith("_MODEL")
        and _bare_model(app.config[name]) in RETIRED_GROQ_MODELS
    }
    for name, value in retired.items():
        app.logger.warning(
            "%s=%s names a model Groq no longer serves; every call on it will fail. "
            "Pick one from `python scripts/live_ai_smoke_test.py --task models`.",
            name, value)
    app.config["RETIRED_MODEL_SETTINGS"] = retired
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
        "AI_MAX_OUTPUT_CHARACTERS": 200000,
    }
    for name, default in integer_settings.items():
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    output_budgets = {
        # /api/translate asks for 2500 (a whole lesson's strings) and was cut to 400.
        # TUTOR_CHAT was 350, which forced one-hint answers; a coaching turn (method +
        # worked micro-example + modeled rewrite of the student's attempt) needs room,
        # and gpt-oss models also spend hidden reasoning tokens against this budget.
        # ANSWER_EVALUATION 2600: the retry fields of a first wrong answer (answer_evaluation:v6).
        # LESSON_GENERATION 5000: typed teaching parts per subject (lesson_generation:v7).
        "TUTOR_CHAT": 1200, "ANSWER_EVALUATION": 2600, "MISTAKE_ANALYSIS": 3000, "TRANSLATION": 2500,
        "ANSWER_DIAGNOSIS": 2600, "DIAGNOSIS_VERIFICATION": 300, "QUESTION_GENERATION": 1200,
        "LESSON_GENERATION": 5000, "QUIZ_GENERATION": 600,
        # Both were capped below what their own prompts demand, so the model ran out
        # mid-JSON, failed invalid_json and paid a corrective retry that truncated in
        # the same place. Section generation (v4) returns a topic map with terms, items and
        # exam questions for up to four pages; a 20-question exam had 60 tokens per question.
        "PROJECT_SECTION_GENERATION": 6000, "FINAL_EXAM_GENERATION": 6000,
        # Vision models (e.g. Qwen3) spend hidden reasoning tokens against this budget in
        # the Responses API; too small a cap leaves zero visible output ("response was
        # empty") and rejects readable pages. 4000 fits reasoning + JSON within Groq's
        # free-tier per-request TPM (image input ≈ 2k tokens).
        "OCR_DOCUMENT_RECOGNITION": 4000, "HANDWRITING_REGION_REVIEW": 900,
        # A whole textbook page as rows (word, translation, examples) in one call.
        "VOCABULARY_PAGE_EXTRACTION": 3000,
        # The shared image reader (learnova/vision): every line, LaTeX for formulas and an
        # uncertainty list. 3500 since image_understanding:v2 (bbox only where a close-up
        # needs it); the request ceiling may lower it further per request. 5000 plus
        # ~3.3k input per photo did not fit Groq's 8000 tokens per minute.
        "IMAGE_UNDERSTANDING": 3500,
        "ADAPTIVE_PRACTICE": 2200,
        # Grades every open exam answer in one call, with partial credit; the call site
        # asks for 5000 and was being cut to 600, i.e. a few words per answer.
        "FINAL_EXAM_EVALUATION": 5000, "FLASHCARD_GENERATION": 4000,
        # Three short definitions for one term. Kept tight because this task fires
        # once per card a student types, not once per set.
        "FLASHCARD_BACK_SUGGESTION": 400,
        "FLASHCARD_REVIEW": 900, "CONTENT_MODERATION": 900,
        # Generous on purpose: this is the one surface where a learner asks an open
        # question and expects a real answer rather than a graded snippet.
        "ASSISTANT_CHAT": 2000,
    }
    input_budgets = {
        # TUTOR_CHAT carries the lesson state (~2k tokens), a 4k-char source excerpt,
        # a 1.5k-token history window and a 4k-char student message; 1800 rejected
        # ordinary turns as "material too large". 8000 since a turn may also carry what
        # was read from up to four photos (capped at 6000 characters, ~1500 tokens).
        "TUTOR_CHAT": 8000, "ANSWER_EVALUATION": 5000, "MISTAKE_ANALYSIS": 6000, "TRANSLATION": 9000,
        "ANSWER_DIAGNOSIS": 7000, "DIAGNOSIS_VERIFICATION": 4000, "QUESTION_GENERATION": 6000,
        # LESSON_GENERATION: 16 000 characters of pages + the topic map + the teaching method.
        "LESSON_GENERATION": 12000, "QUIZ_GENERATION": 6000,
        "PROJECT_SECTION_GENERATION": 16000, "FINAL_EXAM_GENERATION": 20000,
        "OCR_DOCUMENT_RECOGNITION": 8000, "HANDWRITING_REGION_REVIEW": 6000,
        "VOCABULARY_PAGE_EXTRACTION": 8000,
        "IMAGE_UNDERSTANDING": 10000,
        "ADAPTIVE_PRACTICE": 6000,
        "FINAL_EXAM_EVALUATION": 8000, "FLASHCARD_GENERATION": 10000,
        "FLASHCARD_BACK_SUGGESTION": 800,
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

    # Token budgets and provider caps (learnova.ai_services.budgets). All optional. An
    # unset user or site cap means what it always has - no limit. An unset cap for a
    # provider other than Groq means that provider is *not permitted*: a paid provider
    # must be switched on by giving it a budget, never on by omission.
    def optional_int(name: str) -> None:
        raw = os.getenv(name, "").strip()
        if not raw:
            app.config[name] = None
            return
        try:
            app.config[name] = max(0, int(raw))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a non-negative integer") from error

    def optional_float(name: str) -> None:
        raw = os.getenv(name, "").strip()
        if not raw:
            app.config[name] = None
            return
        try:
            app.config[name] = max(0.0, float(raw))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a non-negative number") from error

    for name in ("AI_BUDGET_USER_TOKENS_PER_DAY", "AI_BUDGET_USER_TOKENS_PER_MONTH",
                 "AI_BUDGET_GLOBAL_TOKENS_PER_MONTH"):
        optional_int(name)
    # Emergency global ceiling applies to free and Premium accounts alike. An operator
    # may tune it in production; an explicit blank disables it only by choice.
    raw_global_daily = os.getenv("AI_BUDGET_GLOBAL_TOKENS_PER_DAY", "2000000").strip()
    if raw_global_daily:
        try:
            app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = max(0, int(raw_global_daily))
        except ValueError as error:
            raise RuntimeError("AI_BUDGET_GLOBAL_TOKENS_PER_DAY must be a non-negative integer") from error
    else:
        app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = None
    for provider in ("GROQ", "OPENAI", "ANTHROPIC", "GEMINI"):
        optional_int(f"AI_BUDGET_{provider}_TOKENS_PER_DAY")
        optional_int(f"AI_BUDGET_{provider}_TOKENS_PER_MONTH")
        optional_int(f"AI_RATE_{provider}_REQUESTS_PER_MINUTE")
        optional_float(f"AI_COST_{provider}_INPUT_PER_MILLION")
        optional_float(f"AI_COST_{provider}_OUTPUT_PER_MILLION")
    optional_float("AI_BUDGET_GLOBAL_SPEND_PER_MONTH")
    # Cache entries older than this are treated as misses (0 = never expire); identical
    # requests within this many seconds share one provider call (0 = off).
    try:
        app.config["AI_CACHE_TTL_DAYS"] = max(0, int(os.getenv("AI_CACHE_TTL_DAYS", "30")))
        app.config["AI_DEDUP_WINDOW_SECONDS"] = max(0, int(os.getenv("AI_DEDUP_WINDOW_SECONDS", "15")))
    except ValueError as error:
        raise RuntimeError("AI_CACHE_TTL_DAYS and AI_DEDUP_WINDOW_SECONDS must be integers") from error
    try:
        app.config["AI_BUDGET_RESERVATION_TTL_MINUTES"] = max(
            1, int(os.getenv("AI_BUDGET_RESERVATION_TTL_MINUTES", "10")))
        app.config["AI_MAX_PROVIDER_CALLS_PER_REQUEST"] = max(
            1, min(5, int(os.getenv("AI_MAX_PROVIDER_CALLS_PER_REQUEST", "3"))))
    except ValueError as error:
        raise RuntimeError("AI_BUDGET_RESERVATION_TTL_MINUTES and AI_MAX_PROVIDER_CALLS_PER_REQUEST "
                           "must be integers") from error

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
    _configure_community_alerts(app, selected)
    _configure_contact(app, selected)

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
    try:
        app.config["PROJECT_MAX_PAGES"] = max(
            1, int(os.getenv("PROJECT_MAX_PAGES", str(DEFAULT_PROJECT_MAX_PAGES))))
    except ValueError as error:
        raise RuntimeError("PROJECT_MAX_PAGES must be a positive integer") from error

    def _feature_flag(name: str, default: bool) -> bool:
        raw = os.getenv(name)
        if raw is None:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    # ---- Project uploads (FIX_PLAN PF1) ----
    # The upload request no longer renders or preprocesses pages (a 40-page PDF took 55 s,
    # 40 phone photos 96 s, in one request); each page's picture is made when it is first
    # read or shown. true restores rendering at upload: the rollback switch.
    app.config["PROJECT_PREPROCESS_ON_UPLOAD"] = _feature_flag("PROJECT_PREPROCESS_ON_UPLOAD", False)
    # Share of a PDF page covered by raster images (0..1) from which its text layer counts
    # as partial: the page is read by vision, not stored from the text layer alone (a pasted
    # photo of a handwritten note, a scanner app's OCR layer over a photo).
    try:
        share = float(os.getenv("PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE", "0.10"))
    except ValueError as error:
        raise RuntimeError("PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE must be a number between 0 and 1") from error
    if not 0.0 <= share <= 1.0:
        raise RuntimeError("PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE must be a number between 0 and 1")
    app.config["PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE"] = share

    # ---- Topic analysis in steps (FIX_PLAN PF2) ----
    # The start page works out the topics in short requests (POST /projects/<id>/analysis/step),
    # each making at most ANALYSIS_STEP_CALLS extraction calls, so no request runs into a proxy
    # timeout. One analysis per project at a time: the per-project lease lasts this many
    # seconds and is renewed before every extraction call (one call can take up to ~120 s:
    # 40 s provider timeout, a corrective retry, a fallback model), so a killed request's
    # lease expires by itself and the next step takes over.
    def _bounded(name: str, default: int, low: int, high: int) -> int:
        raw = os.getenv(name, str(default)).strip() or str(default)
        try:
            value = int(raw)
        except ValueError as error:
            raise RuntimeError(f"{name} must be a whole number between {low} and {high}") from error
        if not low <= value <= high:
            raise RuntimeError(f"{name} must be a whole number between {low} and {high}")
        return value

    app.config["ANALYSIS_STEP_CALLS"] = _bounded("ANALYSIS_STEP_CALLS", 2, 1, 6)
    app.config["ANALYSIS_LEASE_SECONDS"] = _bounded("ANALYSIS_LEASE_SECONDS", 150, 30, 900)

    # ---- The tap-a-word scanner: local OCR plus translation APIs, no language model ----
    # LOCAL_OCR_ENGINE: auto (use RapidOCR when it is installed) | rapidocr | off.
    app.config["LOCAL_OCR_ENGINE"] = os.getenv("LOCAL_OCR_ENGINE", "auto").strip().lower() or "auto"
    # A dictionary hint for the multilingual model; the same model serves every Latin script.
    app.config["LOCAL_OCR_LANGUAGE"] = os.getenv("LOCAL_OCR_LANGUAGE", "de").strip().lower() or "de"
    # Load the models in a background thread at start-up so the first scan is not the slow one.
    app.config["LOCAL_OCR_WARMUP"] = _feature_flag("LOCAL_OCR_WARMUP", selected != "testing")
    # Translation providers, tried in this order; one without its credentials is skipped.
    # MyMemory needs no key, so a fresh install can translate.
    app.config["TRANSLATION_PROVIDERS"] = os.getenv("TRANSLATION_PROVIDERS", "deepl,libretranslate,mymemory")
    app.config["DEEPL_API_KEY"] = os.getenv("DEEPL_API_KEY", "").strip()
    app.config["LIBRETRANSLATE_URL"] = os.getenv("LIBRETRANSLATE_URL", "").strip()
    app.config["LIBRETRANSLATE_API_KEY"] = os.getenv("LIBRETRANSLATE_API_KEY", "").strip()
    app.config["MYMEMORY_EMAIL"] = os.getenv("MYMEMORY_EMAIL", "").strip()
    for name, default in (("TRANSLATION_TIMEOUT_SECONDS", "4"),
                          # Bounded waits for the AI providers. The SDKs default to 600 s and two
                          # silent retries; one unreachable vision endpoint made a scan take eight
                          # minutes. A text answer has 40 s, a page read 75 s, and no SDK retries.
                          ("AI_PROVIDER_TIMEOUT_SECONDS", "40"), ("AI_VISION_TIMEOUT_SECONDS", "75")):
        try:
            app.config[name] = max(1.0, float(os.getenv(name, default)))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive number") from error

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
    # "Continue learning": one click turns a chat into a lesson (counts as a free lesson).
    app.config["FEATURE_CONTINUE_LEARNING"] = _feature_flag("FEATURE_CONTINUE_LEARNING", True)
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
    if (app.config["FEATURE_COMMUNITY_PUBLISHING"] and selected != "testing"
            and not (app.config["MAIL_SMTP_HOST"] and app.config["MAIL_FROM"])):
        # Also not fatal: alerts are stored and listed on /internal/moderation either way.
        app.logger.warning(
            "Community alert email is not configured (MAIL_SMTP_HOST, MAIL_FROM): reports and "
            "review findings for %s are stored but not emailed.", app.config["COMMUNITY_ALERT_EMAIL"])
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
        "FEATURE_VOCABULARY_TRAINER", "FEATURE_LEAGUES",
    ):
        app.config[config_name] = _feature_flag(config_name, completed_feature_defaults)
    # Learnova Leagues (learnova/gamification/leagues.py). Places that promote and demote
    # per group at the end of each week, and the most learners in one group.
    for name, default, low in (("LEAGUE_PROMOTE", "10", 0), ("LEAGUE_DEMOTE", "10", 0),
                               ("LEAGUE_GROUP_SIZE", "30", 2),
                               # Days between two username changes, and how long a released
                               # name stays unavailable to everyone else.
                               ("USERNAME_CHANGE_COOLDOWN_DAYS", "30", 0),
                               ("USERNAME_HOLD_DAYS", "90", 0)):
        try:
            app.config[name] = max(low, int(os.getenv(name, default)))
        except ValueError as error:
            raise RuntimeError(f"{name} must be an integer") from error
    app.config["FEATURE_FLASHCARD_GAMES"] = all(app.config[name] for name in (
        "FEATURE_FLASHCARD_MATCH_GAME", "FEATURE_FLASHCARD_BLAST_GAME",
        "FEATURE_FLASHCARD_BLOCKS_GAME",
    ))
    # The handwriting second look: one extra vision call per page, spent only on pages
    # that actually came back uncertain. On by default everywhere, including production,
    # because a page a student cannot read back is worse than one extra request - but it
    # is a flag so it can be turned off if the provider bill or rate limit demands it.
    app.config["FEATURE_HANDWRITING_SECOND_LOOK"] = _feature_flag(
        "FEATURE_HANDWRITING_SECOND_LOOK", True)
    # Wikipedia illustrations and video search links on generated lessons. On by
    # default; a switch exists because it is the one feature that reaches a third
    # party on the request path.
    app.config["FEATURE_LESSON_MEDIA"] = _feature_flag("FEATURE_LESSON_MEDIA", True)
    # Baden-Württemberg Bildungsplan grounding (learnova/curriculum). On by default but a
    # no-op until documents are imported; the cleared-hosts list starts empty on purpose,
    # because confirming a source's usage terms is the operator's decision.
    app.config["FEATURE_CURRICULUM"] = _feature_flag("FEATURE_CURRICULUM", True)
    app.config["CURRICULUM_CLEARED_SOURCES"] = os.getenv("CURRICULUM_CLEARED_SOURCES", "")
    app.config["CURRICULUM_CONTEXT_MAX_CHARS"] = max(
        400, min(4000, int(os.getenv("CURRICULUM_CONTEXT_MAX_CHARS", "1800"))))
    app.config["HANDWRITING_SECOND_LOOK_MAX_REGIONS"] = max(
        1, min(12, int(os.getenv("HANDWRITING_SECOND_LOOK_MAX_REGIONS", "8"))))
    _configure_vision(app, selected)
    _configure_assistant_chat(app)
    _configure_ads(app)
    app.config["GAMIFICATION_MIN_DAILY_EVENTS"] = max(
        1, int(os.getenv("GAMIFICATION_MIN_DAILY_EVENTS", "3")))
    app.config["AI_DIAGNOSTICS_ADMINS"] = {
        item.strip().casefold()
        for item in os.getenv("AI_DIAGNOSTICS_ADMINS", "").split(",")
        if item.strip()
    }
    app.config["PREMIUM_ADMIN_IDENTIFIERS"] = {
        item.strip().casefold()
        for item in os.getenv("PREMIUM_ADMIN_IDENTIFIERS", "").split(",")
        if item.strip()
    }
    # Plans, prices, free allowances and activation-code policy (learnova/subscriptions,
    # docs/SUBSCRIPTIONS.md). Validated here so a bad value stops the deploy.
    from learnova.subscriptions.plans import load_settings
    app.config["SUBSCRIPTION_SETTINGS"] = load_settings()
    for name, default in {
        # The knowledge gate (learnova/quizzes/mastery_gate.py): a test runs between MIN and
        # MAX questions and stops once every concept is known at KNOWLEDGE_TARGET percent.
        "TEST_MIN_QUESTIONS": 3,
        "TEST_MAX_QUESTIONS": 15,
        "KNOWLEDGE_TARGET": 80,
        # Wrong open exam answers that get a full diagnosis at submission (lowest scores
        # first); each one is one or two model calls made while the student waits.
        "EXAM_DIAGNOSIS_LIMIT": 3,
        # The call-site caps for lessons (lesson_generation:v7 parts) and answer evaluation
        # (retry fields). An environment pin (render.yaml, .env) below these cuts lessons off.
        "LESSON_TOKEN_LIMIT": 5000,
        "ANSWER_TOKEN_LIMIT": 2600,
        # The smallest share of a topic's notes (characters) a lesson fitted to
        # AI_TEXT_REQUEST_TOKEN_CEILING keeps; below it the request is refused honestly.
        "LESSON_SOURCE_MIN_CHARS": 4000,
        # Chat replies were capped at 350 tokens ("at most 120 words"), which made the
        # tutor a hint widget; a teaching turn needs room for a method + one example.
        "CHAT_TOKEN_LIMIT": 1100,
        # How much of the tutor-chat input budget the conversation history may use;
        # the newest turns always survive (learnova/assistant/conversation.py).
        "TUTOR_CHAT_HISTORY_TOKEN_BUDGET": 1500,
        "TRANSLATE_TOKEN_LIMIT": 2500,
        "FLASHCARD_TOKEN_LIMIT": 4000,
        "FLASHCARD_SUGGESTION_TOKEN_LIMIT": 400,
        "REGION_REVIEW_TOKEN_LIMIT": 900,
        "REVIEW_TOKEN_LIMIT": 900,
    }.items():
        try:
            app.config[name] = max(1, int(os.getenv(name, str(default))))
        except ValueError as error:
            raise RuntimeError(f"{name} must be a positive integer") from error
    # Hours a student is paused after hitting their own AI cap (0 disables the cooldown and
    # the cap is simply re-checked on the next request, as before).
    try:
        app.config["AI_USER_COOLDOWN_HOURS"] = max(0.0, float(os.getenv("AI_USER_COOLDOWN_HOURS", "6")))
    except ValueError as error:
        raise RuntimeError("AI_USER_COOLDOWN_HOURS must be a number of hours") from error
    return selected

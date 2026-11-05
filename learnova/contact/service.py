"""The contact form's rules, without Flask. See docs/CONTACT.md.

Everything here is pure so it can be unit-tested and reused by the route, the CLI and
the delivery job alike:

- ``normalize`` cleans the four fields (control characters out, whitespace collapsed in
  the name, CRLF folded to LF in the message). Lengths are counted *after* this, so the
  browser's ``maxlength`` counter and the server agree even though browsers submit
  textarea newlines as CRLF.
- ``validate`` returns ``{field: error code}``; the route turns codes into translated text
  through ``ERROR_MESSAGES`` (English msgids).
- ``form_token`` / ``form_token_age`` sign the moment the form was rendered, for the
  minimum fill time, together with a random nonce kept in the visitor's session. The
  route drops the nonce once a message is accepted, so a rendered form is sent once: a
  bot cannot reuse one token for a day of instant posts. (Sessions are signed cookies, so
  a bot that replays its old cookie can still reuse a token; the per-IP limiter and the
  database ceilings carry the volume protection.) Nothing personal goes into the token.
- ``email_key`` names the mailbox behind an address (``bob+1@`` and ``bob+2@`` are one
  inbox; Gmail ignores dots), for the per-address ceiling and the duplicate guard. The
  address itself is stored as written, for the reply.
- ``message_digest`` identifies a resubmission of the same text (duplicate guard).
- ``compose_forward`` writes the plain-text email for the operator. User input never
  goes into the subject: it carries a fixed English label and the row id only.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import datetime

# (key, English label). The label is a msgid; the forward email uses it untranslated.
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("bug", "Bug report"),
    ("technical", "Technical problem"),
    ("suggestion", "Suggestion or feature request"),
    ("account", "Account problem"),
    ("subscription", "Subscription or Premium problem"),
    ("general", "General question"),
    ("partnership", "Partnership or business enquiry"),
    ("other", "Other"),
)
CATEGORY_KEYS = frozenset(key for key, _label in CATEGORIES)
CATEGORY_LABELS: dict[str, str] = dict(CATEGORIES)

NAME_MAX = 100
EMAIL_MAX = 254  # the SMTP path limit
MESSAGE_MIN = 10
MESSAGE_MAX = 5000

# One plain ASCII address. ASCII only keeps Reply-To free of SMTPUTF8; anyone with an
# international address can still write through the mailto link.
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}"
    r"@(?:[A-Za-z0-9](?:-?[A-Za-z0-9]){0,62}\.)+[A-Za-z]{2,63}")

ERROR_MESSAGES: dict[str, str] = {
    "name_missing": "Please enter your name.",
    "name_too_long": "Your name can be at most {max} characters long.",
    "email_invalid": "Please enter a valid email address.",
    "category_invalid": "Please choose a topic for your message.",
    "message_missing": "Please write a message.",
    "message_too_short": "Your message is too short. Please write at least {min} characters.",
    "message_too_long": "Your message is too long. Please keep it to {max} characters or fewer.",
}
# Values each message needs, so the caller can format the translated text.
ERROR_VALUES: dict[str, dict[str, int]] = {
    "name_too_long": {"max": NAME_MAX},
    "message_too_short": {"min": MESSAGE_MIN},
    "message_too_long": {"max": MESSAGE_MAX},
}

TOKEN_PURPOSE = "contact-form"
TOKEN_MAX_AGE = 24 * 60 * 60


@dataclass(frozen=True)
class ContactInput:
    name: str
    email: str
    category: str
    message: str


def _drop_controls(text: str, keep: str = "") -> str:
    return "".join(
        character for character in text
        if character in keep or unicodedata.category(character) not in {"Cc", "Cf"})


def is_valid_email(value: str) -> bool:
    return bool(value) and len(value) <= EMAIL_MAX and EMAIL_PATTERN.fullmatch(value) is not None


# Gmail delivers j.doe@, jdoe@ and jdoe@googlemail.com to one inbox.
GMAIL_DOMAINS = ("gmail.com", "googlemail.com")


def email_key(email: str) -> str:
    """The mailbox an address reaches, for rate limits: lower case, without a +tag, and
    for Gmail also without dots. Grouping a little too much only makes one shared cap."""

    local, _, domain = email.strip().lower().rpartition("@")
    if not local:
        return email.strip().lower()
    local = local.split("+", 1)[0] or local
    if domain in GMAIL_DOMAINS:
        local = local.replace(".", "") or local
        domain = GMAIL_DOMAINS[0]
    return f"{local}@{domain}"


def mailbox_domains(key: str) -> tuple[str, ...]:
    """Domains whose stored addresses can share this email_key (narrows the query)."""

    domain = key.rpartition("@")[2]
    return GMAIL_DOMAINS if domain in GMAIL_DOMAINS else (domain,)


def normalize(name: object, email: object, category: object, message: object) -> ContactInput:
    def text(value: object) -> str:
        return value if isinstance(value, str) else ""

    clean_name = _drop_controls(" ".join(text(name).split())).strip()
    clean_email = text(email).strip().lower()
    clean_category = text(category).strip()
    body = text(message).replace("\r\n", "\n").replace("\r", "\n")
    clean_message = _drop_controls(body, keep="\n\t").strip()
    return ContactInput(clean_name, clean_email, clean_category, clean_message)


def validate(data: ContactInput) -> dict[str, str]:
    errors: dict[str, str] = {}
    if not data.name:
        errors["name"] = "name_missing"
    elif len(data.name) > NAME_MAX:
        errors["name"] = "name_too_long"
    if not is_valid_email(data.email):
        errors["email"] = "email_invalid"
    if data.category not in CATEGORY_KEYS:
        errors["category"] = "category_invalid"
    if not data.message:
        errors["message"] = "message_missing"
    elif len(data.message) < MESSAGE_MIN:
        errors["message"] = "message_too_short"
    elif len(data.message) > MESSAGE_MAX:
        errors["message"] = "message_too_long"
    return errors


def message_digest(message: str) -> str:
    folded = " ".join(message.split()).casefold()
    return hashlib.sha256(folded.encode("utf-8")).hexdigest()


def new_nonce() -> str:
    """A fresh per-session form nonce (random, nothing personal)."""

    return secrets.token_urlsafe(16)


def _signature(secret: str, issued_at: int, nonce: str) -> str:
    payload = f"{TOKEN_PURPOSE}:{nonce}:{issued_at}".encode()
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()[:32]


def form_token(secret: str, issued_at: int, nonce: str) -> str:
    return f"{issued_at}.{_signature(secret, issued_at, nonce)}"


def form_token_age(secret: str, token: object, now: float, *, nonce: str,
                   max_age: int = TOKEN_MAX_AGE) -> float | None:
    """Seconds since the form was rendered, or None for a missing, forged or stale token,
    or one rendered for another session (or before this session's last accepted message)."""

    if not nonce or not isinstance(nonce, str):
        return None
    if not isinstance(token, str) or token.count(".") != 1 or len(token) > 64:
        return None
    raw_time, signature = token.split(".")
    if not raw_time.isdigit():
        return None
    issued_at = int(raw_time)
    if not hmac.compare_digest(signature, _signature(secret, issued_at, nonce)):
        return None
    age = now - issued_at
    if age < -5 or age > max_age:  # a little clock skew is fine; the future is not
        return None
    return max(0.0, age)


def forward_subject(category: str, message_id: int) -> str:
    label = CATEGORY_LABELS.get(category, "Other")
    return f"[Learnova contact] {label} #{int(message_id)}"


def compose_forward(*, message_id: int, category: str, name: str, email: str, message: str,
                    user_id: int | None, language: str, created_at: datetime,
                    base_url: str, retention_days: int) -> tuple[str, str]:
    """(subject, body) of the email to the operator. Plain text, English."""

    label = CATEGORY_LABELS.get(category, "Other")
    inbox_path = "/admin/contact-messages"
    inbox = f"{base_url.rstrip('/')}{inbox_path}" if base_url else inbox_path
    signed_in = f"yes (account #{int(user_id)})" if user_id else "no"
    body = "\n".join((
        f"New message from the Learnova contact form (#{int(message_id)})",
        f"Topic: {label}",
        f"Name: {name}",
        f"Email: {email}   (Reply-To is set to this address)",
        f"Signed in: {signed_in}",
        f"Interface language: {language}",
        f"Received: {created_at:%Y-%m-%d %H:%M} UTC",
        "----",
        message,
        "----",
        f"Stored in Learnova for {int(retention_days)} days. Inbox: {inbox}",
        "",
    ))
    return forward_subject(category, message_id), body

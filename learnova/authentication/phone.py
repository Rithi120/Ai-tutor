"""Phone login: E.164 numbers, one-time codes and SMS delivery. Flask-free.

* `normalize` - an international number (country selector, Germany by default) to E.164,
  or "" when it is not a valid number (Google's libphonenumber via `phonenumbers`).
* `new_code` / `code_hash` / `code_matches` - a 6-digit code, stored only as an HMAC keyed
  with the server secret: a database copy alone cannot be brute-forced offline.
* `send_sms` - one message through the configured provider (SMS_PROVIDER: twilio or
  vonage), over HTTPS with the credentials from the environment. Never raises; the result
  says truthfully what the provider accepted, and is never "sent" without a provider.

The code itself is never logged, returned to a browser, or put in an error message.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any, Callable, Mapping

SMS_PROVIDERS = ("twilio", "vonage")
CODE_DIGITS = 6
DEFAULT_REGION = "DE"
HTTP_TIMEOUT_SECONDS = 10

# The country selector: (region, calling code, label). Germany first (the default), then
# the neighbours and the most common countries of origin in German schools, then the rest.
COUNTRIES = (
    ("DE", "+49", "Deutschland"), ("AT", "+43", "Österreich"), ("CH", "+41", "Schweiz"),
    ("FR", "+33", "France"), ("NL", "+31", "Nederland"), ("BE", "+32", "Belgique"), ("LU", "+352", "Luxembourg"),
    ("PL", "+48", "Polska"), ("CZ", "+420", "Česko"), ("DK", "+45", "Danmark"), ("IT", "+39", "Italia"),
    ("ES", "+34", "España"), ("PT", "+351", "Portugal"), ("GB", "+44", "United Kingdom"), ("IE", "+353", "Ireland"),
    ("TR", "+90", "Türkiye"), ("GR", "+30", "Ελλάδα"), ("HR", "+385", "Hrvatska"), ("RS", "+381", "Srbija"),
    ("BA", "+387", "Bosna i Hercegovina"), ("RO", "+40", "România"), ("BG", "+359", "България"),
    ("HU", "+36", "Magyarország"), ("UA", "+380", "Україна"), ("RU", "+7", "Россия"), ("SY", "+963", "سوريا"),
    ("AF", "+93", "افغانستان"), ("IQ", "+964", "العراق"), ("IN", "+91", "India"), ("US", "+1", "United States"),
)
REGIONS = frozenset(region for region, _code, _label in COUNTRIES)


def available() -> bool:
    try:
        import phonenumbers  # noqa: F401
    except ImportError:
        return False
    return True


def normalize(raw: str, region: str = DEFAULT_REGION) -> str:
    """E.164 ("+4915112345678") or "" for anything that is not a valid, dialable number."""

    import phonenumbers

    text = " ".join(str(raw or "").split())[:40]
    if not text:
        return ""
    region = region if region in REGIONS else DEFAULT_REGION
    try:
        number = phonenumbers.parse(text, region)
    except phonenumbers.NumberParseException:
        return ""
    if not phonenumbers.is_valid_number(number):
        return ""
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


def masked(e164: str) -> str:
    """"+49 ••• ••• 78" - enough for the student to recognise their number, no more."""

    value = str(e164 or "")
    if len(value) < 6:
        return "•••"
    return f"{value[:3]} ••• ••• {value[-2:]}"


def new_code() -> str:
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def code_hash(secret: str, challenge_key: str, phone_number: str, code: str) -> str:
    """HMAC-SHA256 of the code, bound to its challenge and number."""

    message = f"{challenge_key}|{phone_number}|{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def code_matches(secret: str, challenge_key: str, phone_number: str, code: str, stored: str) -> bool:
    candidate = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(candidate) != CODE_DIGITS:
        return False
    return hmac.compare_digest(code_hash(secret, challenge_key, phone_number, candidate), str(stored or ""))


# --------------------------------------------------------------------------- SMS
@dataclass(frozen=True)
class SmsResult:
    status: str          # sent | failed | not_configured
    detail: str = ""     # provider status code or error type; never the message text

    @property
    def accepted(self) -> bool:
        return self.status == "sent"


def sms_provider(config: Mapping[str, Any]) -> str:
    """The configured provider, or "" when phone login cannot send codes."""

    name = str(config.get("SMS_PROVIDER") or "").strip().lower()
    if name not in SMS_PROVIDERS:
        return ""
    if not all(str(config.get(key) or "").strip() for key in ("SMS_API_KEY", "SMS_API_SECRET", "SMS_SENDER")):
        return ""
    return name


def send_sms(config: Mapping[str, Any], to: str, text: str, *, post: Callable[..., Any] | None = None) -> SmsResult:
    """Send one SMS. Twilio: SMS_API_KEY = Account SID, SMS_API_SECRET = Auth Token, SMS_SENDER =
    the sending number or Messaging Service SID (MG...). Vonage: key, secret and the sender name/number."""

    provider = sms_provider(config)
    if not provider:
        return SmsResult("not_configured", "SMS_PROVIDER, SMS_API_KEY, SMS_API_SECRET or SMS_SENDER is not set")
    if post is None:
        import requests
        post = requests.post
    key, secret, sender = (str(config[name]).strip() for name in ("SMS_API_KEY", "SMS_API_SECRET", "SMS_SENDER"))
    try:
        if provider == "twilio":
            data = {"To": to, "Body": text}
            data["MessagingServiceSid" if sender.startswith("MG") else "From"] = sender
            response = post(f"https://api.twilio.com/2010-04-01/Accounts/{key}/Messages.json", data=data,
                            auth=(key, secret), timeout=HTTP_TIMEOUT_SECONDS)
            ok = getattr(response, "status_code", 0) in (200, 201)
            return SmsResult("sent" if ok else "failed", f"twilio_{getattr(response, 'status_code', 0)}")
        response = post("https://rest.nexmo.com/sms/json",
                        data={"api_key": key, "api_secret": secret, "from": sender, "to": to.lstrip("+"),
                              "text": text, "type": "unicode"}, timeout=HTTP_TIMEOUT_SECONDS)
        status = ""
        try:
            messages = response.json().get("messages") or [{}]
            status = str(messages[0].get("status", ""))
        except (ValueError, AttributeError, IndexError):
            status = "unreadable"
        return SmsResult("sent" if status == "0" else "failed", f"vonage_{status or getattr(response, 'status_code', 0)}")
    except Exception as error:  # noqa: BLE001 - network: report, never raise
        return SmsResult("failed", type(error).__name__)


__all__ = ["CODE_DIGITS", "COUNTRIES", "DEFAULT_REGION", "REGIONS", "SMS_PROVIDERS", "SmsResult", "available",
           "code_hash", "code_matches", "masked", "new_code", "normalize", "send_sms", "sms_provider"]

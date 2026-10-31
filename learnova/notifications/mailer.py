"""Send one plain-text email over SMTP and say truthfully what happened.

There was no email integration in Learnova before this module, so it is the smallest
one that is safe: the standard library's smtplib, configured entirely from the
environment (no credential ever lives in code), and a result that distinguishes

- ``sent``: the SMTP server accepted the message for every recipient. That is what the
  server confirms - not that it reached the inbox - and it is the only case reported
  as sent;
- ``retry``: a temporary failure (connection, timeout, a 4xx reply). Safe to try again;
- ``failed``: a permanent failure (authentication, a 5xx reply, a refused recipient,
  or a configuration that would send credentials in clear text). Retrying will not help;
- ``not_configured``: no server or sender is set. Nothing was attempted.

Error details are reduced to the exception type and the SMTP reply code. Server reply
text can echo the login name, so it is never stored or logged.
"""

from __future__ import annotations

import smtplib
import socket
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Callable, Mapping

SECURITY_MODES = ("starttls", "ssl", "none")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    sender: str
    username: str = ""
    password: str = ""
    security: str = "starttls"
    timeout: float = 10.0

    def __repr__(self) -> str:  # never print the password, even in a traceback
        return (f"MailSettings(host={self.host!r}, port={self.port}, sender={self.sender!r}, "
                f"username={'set' if self.username else 'unset'}, security={self.security!r})")


@dataclass(frozen=True)
class DeliveryResult:
    status: str  # sent | retry | failed | not_configured
    detail: str = ""
    message_id: str = ""

    @property
    def accepted(self) -> bool:
        return self.status == "sent"


def mail_settings(config: Mapping[str, Any]) -> MailSettings | None:
    """Settings from app config, or None when email is not configured."""

    host = str(config.get("MAIL_SMTP_HOST") or "").strip()
    sender = str(config.get("MAIL_FROM") or "").strip()
    if not host or not sender:
        return None
    security = str(config.get("MAIL_SMTP_SECURITY") or "starttls").strip().lower()
    if security not in SECURITY_MODES:
        security = "starttls"
    default_port = 465 if security == "ssl" else 587
    try:
        port = int(config.get("MAIL_SMTP_PORT") or default_port)
    except (TypeError, ValueError):
        port = default_port
    try:
        timeout = max(1.0, float(config.get("MAIL_TIMEOUT_SECONDS") or 10))
    except (TypeError, ValueError):
        timeout = 10.0
    return MailSettings(
        host=host, port=port, sender=sender,
        username=str(config.get("MAIL_SMTP_USERNAME") or "").strip(),
        password=str(config.get("MAIL_SMTP_PASSWORD") or ""),
        security=security, timeout=timeout)


def _message(settings: MailSettings, recipient: str, subject: str, body: str) -> EmailMessage:
    message = EmailMessage()
    message["From"] = settings.sender
    message["To"] = recipient
    # Header injection is impossible with EmailMessage, but a subject is also kept to
    # one line so a title with a newline cannot reshape the header block.
    message["Subject"] = " ".join(subject.split())[:200]
    message["Date"] = formatdate(localtime=False)
    domain = settings.sender.rpartition("@")[2] or None
    message["Message-ID"] = make_msgid(domain=domain)
    message.set_content(body)
    return message


def _reply_code(error: BaseException) -> int:
    code = getattr(error, "smtp_code", 0)
    return code if isinstance(code, int) else 0


def send_email(
    settings: MailSettings | None, recipient: str, subject: str, body: str, *,
    smtp_factory: Callable[..., Any] | None = None,
    ssl_factory: Callable[..., Any] | None = None,
) -> DeliveryResult:
    """Send one message. Never raises; the result says what the server confirmed."""

    if settings is None:
        return DeliveryResult("not_configured", "MAIL_SMTP_HOST or MAIL_FROM is not set")
    if not recipient or "@" not in recipient:
        return DeliveryResult("failed", "no valid recipient")
    if settings.security == "none" and settings.username and settings.host not in LOCAL_HOSTS:
        # Logging in over an unencrypted connection would put the password on the wire.
        return DeliveryResult("failed", "refusing to send credentials without TLS")
    message = _message(settings, recipient, subject, body)
    try:
        if settings.security == "ssl":
            factory = ssl_factory or smtplib.SMTP_SSL
            client = factory(settings.host, settings.port, timeout=settings.timeout,
                             context=ssl.create_default_context())
        else:
            factory = smtp_factory or smtplib.SMTP
            client = factory(settings.host, settings.port, timeout=settings.timeout)
        with client as connection:
            if settings.security == "starttls":
                connection.starttls(context=ssl.create_default_context())
            if settings.username:
                connection.login(settings.username, settings.password)
            refused = connection.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        return DeliveryResult("failed", f"authentication rejected ({_reply_code(error)})")
    except smtplib.SMTPRecipientsRefused:
        return DeliveryResult("failed", "recipient refused")
    except smtplib.SMTPResponseException as error:
        code = _reply_code(error)
        status = "retry" if 400 <= code < 500 else "failed"
        return DeliveryResult(status, f"{type(error).__name__} ({code})")
    except smtplib.SMTPServerDisconnected as error:
        return DeliveryResult("retry", type(error).__name__)
    except smtplib.SMTPException as error:
        # Before OSError on purpose: SMTPException subclasses it, and an unsupported
        # STARTTLS or a malformed exchange will not fix itself on a retry.
        return DeliveryResult("failed", type(error).__name__)
    except ssl.SSLCertVerificationError:
        return DeliveryResult("failed", "the server's TLS certificate could not be verified")
    except (socket.timeout, TimeoutError, ConnectionError, OSError) as error:
        return DeliveryResult("retry", type(error).__name__)
    if refused:
        # send_message returns the recipients the server refused while accepting others.
        return DeliveryResult("failed", "recipient refused")
    return DeliveryResult("sent", "accepted by the SMTP server", str(message["Message-ID"]))

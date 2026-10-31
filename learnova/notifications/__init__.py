"""Administrator notifications: an SMTP mailer with honest delivery results."""

from .mailer import (
    DeliveryResult,
    MailSettings,
    mail_settings,
    send_email,
)

__all__ = ["DeliveryResult", "MailSettings", "mail_settings", "send_email"]

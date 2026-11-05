"""Contact form: validation, categories, form timing token and the forward email."""

from .service import (
    CATEGORIES,
    CATEGORY_KEYS,
    CATEGORY_LABELS,
    EMAIL_MAX,
    EMAIL_PATTERN,
    ERROR_MESSAGES,
    ERROR_VALUES,
    MESSAGE_MAX,
    MESSAGE_MIN,
    NAME_MAX,
    ContactInput,
    compose_forward,
    email_key,
    form_token,
    form_token_age,
    forward_subject,
    is_valid_email,
    mailbox_domains,
    message_digest,
    new_nonce,
    normalize,
    validate,
)

__all__ = [
    "CATEGORIES", "CATEGORY_KEYS", "CATEGORY_LABELS", "EMAIL_MAX", "EMAIL_PATTERN",
    "ERROR_MESSAGES", "ERROR_VALUES", "MESSAGE_MAX", "MESSAGE_MIN", "NAME_MAX",
    "ContactInput", "compose_forward", "email_key", "form_token", "form_token_age",
    "forward_subject", "is_valid_email", "mailbox_domains", "message_digest", "new_nonce",
    "normalize", "validate",
]

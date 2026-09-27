"""Direct assistant chat: presets and conversation mechanics.

Flask-independent by design. Choosing what the model sees, and what it is told to be,
are both decisions worth testing without a request context, a database or a provider
call. The app layer (`app.py`) owns persistence, authorization and the gateway call.

This is deliberately separate from the lesson tutor (`/api/chat`), which is scoped to one
lesson's state and dies with it. A conversation here is a durable, user-owned thread with
no lesson behind it.
"""

from .conversation import (
    ASSISTANT,
    MAX_MESSAGE_CHARACTERS,
    ROLES,
    USER,
    ConversationWindow,
    build_window,
    clean_message,
    derive_title,
    estimate_tokens,
    render_transcript,
)
from .presets import (
    DEFAULT_PRESET,
    PRESET_NAMES,
    PRESETS,
    SHARED_RULES,
    normalize_preset,
    options_for_ui,
    preset_description,
    preset_label,
    system_prompt,
)

__all__ = [
    "ASSISTANT",
    "DEFAULT_PRESET",
    "MAX_MESSAGE_CHARACTERS",
    "PRESETS",
    "PRESET_NAMES",
    "ROLES",
    "SHARED_RULES",
    "USER",
    "ConversationWindow",
    "build_window",
    "clean_message",
    "derive_title",
    "estimate_tokens",
    "normalize_preset",
    "options_for_ui",
    "preset_description",
    "preset_label",
    "render_transcript",
    "system_prompt",
]

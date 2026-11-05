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
    STUDENT_CONTEXT_HEADER,
    TUTORING_LAYER,
    normalize_preset,
    options_for_ui,
    preset_label,
    student_context,
    system_prompt,
)
from . import continue_learning, history, titles
from .continue_learning import Gate, lesson_gate, lesson_prompt, sanitize_evidence, select_transcript
from .formatting import DECIMAL_COMMA_LANGUAGES, decimal_commas_in_math, split_reply_trailers
from .history import GROUPS, date_group, decode_cursor, encode_cursor, resolve_zone, snippet
from .titles import GENERIC_REQUESTS, GREETINGS, next_title, title_quality

__all__ = [
    "ASSISTANT",
    "DECIMAL_COMMA_LANGUAGES",
    "DEFAULT_PRESET",
    "GENERIC_REQUESTS",
    "GREETINGS",
    "GROUPS",
    "MAX_MESSAGE_CHARACTERS",
    "PRESETS",
    "PRESET_NAMES",
    "ROLES",
    "SHARED_RULES",
    "STUDENT_CONTEXT_HEADER",
    "TUTORING_LAYER",
    "USER",
    "ConversationWindow",
    "Gate",
    "build_window",
    "clean_message",
    "continue_learning",
    "date_group",
    "decimal_commas_in_math",
    "decode_cursor",
    "derive_title",
    "encode_cursor",
    "estimate_tokens",
    "history",
    "lesson_gate",
    "lesson_prompt",
    "next_title",
    "normalize_preset",
    "options_for_ui",
    "preset_label",
    "render_transcript",
    "resolve_zone",
    "sanitize_evidence",
    "select_transcript",
    "snippet",
    "split_reply_trailers",
    "student_context",
    "system_prompt",
    "title_quality",
    "titles",
]

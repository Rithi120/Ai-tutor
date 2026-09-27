"""Conversation mechanics for the direct assistant chat.

Flask-free and deterministic. A conversation grows without limit while a model's context
does not, so something has to decide what the model sees. That decision is here, as pure
functions over plain dicts, because "which turns were dropped" is exactly the kind of
thing that should be asserted in a test rather than discovered in production.

The rules, in order of precedence:

1. The newest user turn is always sent. Dropping the question to fit the history would
   make the reply answer nothing.
2. Turns are dropped oldest-first, in whole messages. A half-message is worse than a
   missing one: it reads as if the speaker trailed off, and the model answers the
   fragment.
3. A single message too large to fit is truncated rather than dropped, with a visible
   marker, because silently discarding what someone just typed is the worst option.
4. What was dropped is reported, so the interface can say so instead of letting the
   learner wonder why the assistant forgot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ROLES = ("user", "assistant", "system")
USER, ASSISTANT = "user", "assistant"

MAX_MESSAGE_CHARACTERS = 16_000
MAX_TITLE_LENGTH = 80
# Rough and deliberately pessimistic: the gateway uses the same ratio, and over-counting
# costs a little context while under-counting costs a refused request.
CHARACTERS_PER_TOKEN = 4
TRUNCATION_MARKER = "\n[… this message was shortened to fit the context window]"


@dataclass(frozen=True)
class ConversationWindow:
    """What the model will be shown, and what it will not."""

    messages: tuple[dict[str, str], ...]
    dropped_messages: int = 0
    truncated_messages: int = 0
    estimated_tokens: int = 0

    @property
    def complete(self) -> bool:
        return not self.dropped_messages and not self.truncated_messages

    def as_dict(self) -> dict[str, object]:
        return {
            "dropped_messages": self.dropped_messages,
            "truncated_messages": self.truncated_messages,
            "estimated_tokens": self.estimated_tokens,
            "complete": self.complete,
        }


def estimate_tokens(text: str) -> int:
    """Pessimistic token estimate for a string."""

    return (len(str(text or "")) + CHARACTERS_PER_TOKEN - 1) // CHARACTERS_PER_TOKEN


def clean_message(text: str | None, *, limit: int = MAX_MESSAGE_CHARACTERS) -> str:
    """Normalize one submitted message without changing what it says.

    Strips control characters that would corrupt the transcript, collapses runaway blank
    lines, and bounds the length. Wording, punctuation, case and code indentation are all
    preserved - this is a transcript, not something to tidy.
    """

    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        character for character in text
        if character in "\t\n" or character.isprintable()
    )
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()[:limit]


def build_window(
    history: list[dict[str, str]], *, token_budget: int,
    reserve_for_reply: int = 0,
) -> ConversationWindow:
    """Choose the turns to send, newest-first, within a token budget.

    `history` is the full conversation oldest-first, each entry `{"role", "content"}`.
    `reserve_for_reply` is subtracted from the budget so the model has room to answer.
    """

    usable = max(0, token_budget - max(0, reserve_for_reply))
    turns = [
        {"role": str(item.get("role") or USER), "content": str(item.get("content") or "")}
        for item in history
        if str(item.get("role") or "") in ROLES and str(item.get("content") or "").strip()
    ]
    if not turns:
        return ConversationWindow(messages=(), estimated_tokens=0)

    selected: list[dict[str, str]] = []
    truncated = 0
    spent = 0
    # Newest first, so the turns nearest the question are the ones that survive.
    for index, turn in enumerate(reversed(turns)):
        cost = estimate_tokens(turn["content"])
        if spent + cost <= usable:
            selected.append(turn)
            spent += cost
            continue
        if index == 0:
            # The newest turn does not fit on its own. Truncate rather than drop it: the
            # learner just wrote this, and answering nothing is worse than answering a
            # shortened version of it, as long as the shortening is visible.
            room = max(0, usable - estimate_tokens(TRUNCATION_MARKER))
            keep = room * CHARACTERS_PER_TOKEN
            if keep > 0:
                selected.append({
                    "role": turn["role"],
                    "content": turn["content"][:keep] + TRUNCATION_MARKER,
                })
                truncated += 1
                spent = usable
        break

    selected.reverse()
    # A conversation that opens on an assistant turn reads as if the model is answering
    # something the learner cannot see; drop the orphan.
    while selected and selected[0]["role"] == ASSISTANT:
        selected.pop(0)
    return ConversationWindow(
        messages=tuple(selected),
        dropped_messages=len(turns) - len(selected),
        truncated_messages=truncated,
        estimated_tokens=spent,
    )


def render_transcript(window: ConversationWindow, *, speaker: str = "Learner",
                      responder: str = "Assistant") -> str:
    """Render the window as the labelled transcript sent to the model.

    A single text block rather than a message array, because the gateway's canonical
    request carries one `input` string and every task already speaks that shape. The
    labels are explicit so the model cannot mistake who said what.
    """

    lines = []
    for turn in window.messages:
        label = speaker if turn["role"] == USER else responder
        lines.append(f"{label}: {turn['content']}")
    return "\n\n".join(lines)


def derive_title(first_message: str, *, limit: int = MAX_TITLE_LENGTH) -> str:
    """Name a conversation from its opening message.

    Derived locally rather than asked of a model: a title is not worth a round trip, and
    the first line of what someone typed is usually a better name than a summary of it.
    """

    text = clean_message(first_message)
    if not text:
        return "New conversation"
    # Prefer the first sentence or line, whichever ends sooner.
    first_line = text.splitlines()[0].strip()
    sentence = re.split(r"(?<=[.!?])\s", first_line, maxsplit=1)[0].strip()
    title = sentence or first_line
    if len(title) <= limit:
        return title
    # Cut on a word boundary so the title does not end mid-word.
    clipped = title[:limit].rsplit(" ", 1)[0].strip()
    return (clipped or title[:limit]).rstrip(",;:.") + "…"

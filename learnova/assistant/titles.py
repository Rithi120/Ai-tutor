"""Conversation titles: who set the title, and whether a message is worth naming a thread after.

Flask-free and deterministic. A title has a source (`Conversation.title_source`):

* ``default`` - the placeholder ("New conversation"); nothing has named the thread yet.
* ``weak``    - named after a generic request ("Can you help me?"); a later, more telling
                message may still replace it.
* ``auto``    - named after a meaningful first message or a photo's reading summary.
* ``topic``   - named after the topic the assistant reported (WP4).
* ``user``    - renamed by the student. Never touched again by anything automatic.

Only ``default`` and ``weak`` titles ever change automatically, so a rename - or a title
given when the conversation was created - survives every later message.

The filters are deliberately narrow. A greeting ("hi", "hallo", "merci") or the photo
placeholder says nothing about the thread; anything else that is not one of a handful
of whole-message generic requests counts as meaningful, so a short real question
("First question", "2+2?") still names its conversation.
"""

from __future__ import annotations

import re
from typing import Iterable

from .conversation import clean_message, derive_title

SOURCES = ("default", "weak", "auto", "topic", "user")
# The sources an automatic rule may still replace.
REPLACEABLE_SOURCES = frozenset({"default", "weak"})

# Casefolded, punctuation stripped (see `_normalise`). A message made only of these words,
# or equal to one of the phrases, is a greeting or a filler.
GREETINGS: frozenset[str] = frozenset({
    # English
    "hi", "hello", "hey", "yo", "ok", "okay", "thanks", "thank you", "thx", "test",
    "good morning", "good evening", "good afternoon",
    # German
    "hallo", "moin", "servus", "huhu", "danke", "guten morgen", "guten tag", "guten abend",
    # French
    "merci", "bonjour", "salut", "bonsoir",
    # Spanish
    "gracias", "hola", "buenos dias", "buenos días", "buenas tardes",
    # A lone question mark normalises to the empty string and is caught there.
    "?",
})
_GREETING_WORDS = frozenset(word for phrase in GREETINGS for word in phrase.split() if " " not in phrase)

# Whole-message generic requests (matched against the normalised text). They name no
# subject, so a title made from them is only `weak`.
GENERIC_REQUESTS: tuple[re.Pattern[str], ...] = tuple(re.compile(rf"^(?:{pattern})$") for pattern in (
    r"(?:please )?help(?: me)?(?: please)?",
    r"(?:can|could|would) you (?:please )?help(?: me)?(?: please)?",
    r"i need help(?: please)?",
    r"(?:kannst|könntest|kannste) du mir (?:bitte )?helfen(?: bitte)?",
    r"(?:bitte )?hilfe(?: bitte)?",
    r"hilf mir(?: bitte)?",
    r"ich (?:verstehe|versteh) (?:das|es) nicht",
    r"i (?:don t|do not|dont) (?:understand|get)(?: this| that| it)?",
    r"(?:please )?explain (?:this|that|it)(?: please)?",
    r"(?:bitte )?(?:erklär|erkläre|erklaer|erklaere)(?: mir)? (?:das|es)(?: bitte)?",
    r"(?:please )?look at this(?: please)?",
    r"(?:bitte )?schau (?:dir )?(?:das|es) (?:mal )?an(?: bitte)?",
    r"what is (?:this|that)",
    r"was ist das",
    r"(?:aide moi|aidez moi|tu peux m aider|peux tu m aider|pouvez vous m aider)"
    r"(?: s il te pla[iî]t| s il vous pla[iî]t)?",
    r"(?:explique|expliquez)(?: moi)? (?:ça|ca|cela)",
    r"qu est ce que c est",
    r"(?:ayuda|ayúdame|ayudame|puedes ayudarme|me puedes ayudar)(?: por favor)?",
    r"(?:explica|explícame|explicame) (?:esto|eso)",
    r"qué es esto|que es esto",
))


def _normalise(text: str) -> str:
    """Casefolded words only: punctuation and apostrophes become spaces, runs collapse."""

    folded = str(text or "").casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", folded).split())


def _is_greeting(normalised: str) -> bool:
    if not normalised:
        return True
    if normalised in GREETINGS:
        return True
    words = normalised.split()
    return all(word in _GREETING_WORDS for word in words)


def _without_leading_greeting(normalised: str) -> str:
    words = normalised.split()
    while words and words[0] in _GREETING_WORDS:
        words.pop(0)
    return " ".join(words)


def title_quality(text: str, *, placeholders: Iterable[str] = ()) -> str:
    """How well a message names a conversation: ``"none"``, ``"weak"`` or ``"good"``.

    ``none``: empty, a greeting or filler, or one of the photo `placeholders` (the text a
    photo-only message is stored with). ``weak``: one of the whole-message generic
    requests, optionally after a greeting ("Hi, can you help me?"). Everything else is
    ``good``.
    """

    cleaned = clean_message(text)
    if not cleaned:
        return "none"
    folded = cleaned.casefold()
    if any(folded == str(item or "").strip().casefold() for item in placeholders if str(item or "").strip()):
        return "none"
    normalised = _normalise(cleaned)
    if _is_greeting(normalised):
        return "none"
    remainder = _without_leading_greeting(normalised)
    if any(pattern.match(remainder) for pattern in GENERIC_REQUESTS):
        return "weak"
    return "good"


def next_title(*, current: str, source: str, message: str = "", reading_summary: str = "",
               topic: str = "", placeholders: Iterable[str] = ()) -> tuple[str, str] | None:
    """The conversation's new ``(title, source)``, or ``None`` to leave it as it is.

    * ``user``, ``auto`` and ``topic`` titles are never changed.
    * A reported `topic` names a ``default`` or ``weak`` thread (source ``topic``).
    * A ``default`` thread is named after a good message (``auto``) or a generic one
      (``weak``); a greeting or photo-only message leaves it unnamed unless the photo's
      `reading_summary` can name it (``auto``).
    * A ``weak`` title is replaced by the first good message (``auto``).
    * Whenever the message itself is not good, a reading summary beats it: "Erklär mir
      das" with a photo of a worksheet is named after the worksheet.
    """

    del current  # the rules depend on who set the title, not on its wording
    if source not in REPLACEABLE_SOURCES:
        return None
    topic_text = clean_message(topic)
    if topic_text:
        return derive_title(topic_text), "topic"
    quality = title_quality(message, placeholders=placeholders)
    if quality == "good":
        return derive_title(message), "auto"
    summary = clean_message(reading_summary)
    if summary:
        return derive_title(summary), "auto"
    if quality == "weak" and source == "default":
        return derive_title(message), "weak"
    return None


__all__ = ["GENERIC_REQUESTS", "GREETINGS", "REPLACEABLE_SOURCES", "SOURCES", "next_title", "title_quality"]

"""Learning resources under a chat reply: whether to offer a picture or a video at all.

The model proposes (the hidden "MEDIA: diagram|map|video | <Wikipedia title>" line in
presets.TUTORING_LAYER); this module disposes. `media_offer` is a deterministic veto, so a
model that adds a MEDIA line to every reply still cannot put a video under "what does
'bewildered' mean?". Pure and Flask-free: everything here is tested without the app.

It also keeps the reply honest about links. The assistant is told never to write URLs
(presets.SHARED_RULES); `strip_video_links` takes any YouTube link it writes anyway out of
the stored text, so the only video a student can open from a chat reply is a card the
server found and checked (learnova/youtube_videos.py). The browser renderer refuses to link
those hosts too (static/js/markdown.js), which covers replies stored before this.
"""

from __future__ import annotations

import re
from typing import Any

from learnova.lessons.subjects import OTHER, canonical_subject, is_language_subject

# A reply shorter than this answered a simple question: a definition, a fact, a yes/no.
# Pictures and videos supplement an explanation; they never stand in for a short answer.
MIN_REPLY_CHARACTERS = 350
# A language subject gets no picture (a vocabulary word or a grammar rule is learned from
# text); a video only under a real, long explanation - never under a word definition.
MIN_LANGUAGE_VIDEO_CHARACTERS = 900
MAX_TERM_CHARACTERS = 120

_URLISH = re.compile(r"(https?:|www\.|\.(?:com|de|org|net)\b|[<>{}\[\]])", re.IGNORECASE)
_VIDEO_HOSTS = r"(?:(?:www\.|m\.|music\.)?youtube(?:-nocookie)?\.com|youtu\.be)"
_MARKDOWN_VIDEO_LINK = re.compile(
    r"\[([^\[\]\n]{1,300})\]\(\s*(?:https?:)?//" + _VIDEO_HOSTS + r"[^\s()]*(?:\([^\s()]*\)[^\s()]*)*\s*\)",
    re.IGNORECASE)
_BARE_VIDEO_LINK = re.compile(r"<?\b(?:https?://)?" + _VIDEO_HOSTS + r"/[^\s<>()\"'`]*>?", re.IGNORECASE)
_BRACKETED_VIDEO_LINK = re.compile(r"\(\s*" + _BARE_VIDEO_LINK.pattern + r"\s*\)", re.IGNORECASE)
_FENCE = re.compile(r"^\s*(```|~~~)")
# What a language-subject topic is called when it is about a word, not a grammar or text topic.
_VOCABULARY = re.compile(
    r"(vokabel|vocab|wortschatz|bedeutung|meaning|übersetz|translat|definition|signification|significado)",
    re.IGNORECASE)


# A student asking for a picture in so many words ("mit einem Bild", "eine Skizze", "show me a
# diagram"). Nouns only: "zeig mir, wie das geht" asks for an explanation, not a picture.
_PICTURE_REQUEST = re.compile(
    r"\b(?:bild(?:er|chen)?|abbildung(?:en)?|diagramm(?:e|en)?|skizze(?:n)?|schaubild(?:er)?|zeichnung(?:en)?|"
    r"grafik(?:en)?|pictures?|images?|diagrams?|sketch(?:es)?|drawings?|illustrations?|sch[ée]mas?|dessins?|"
    r"imagen(?:es)?|dibujos?|esquemas?)\b", re.IGNORECASE)


def asks_for_picture(text: Any) -> bool:
    """The student's message asks for a picture (a reply without a MEDIA line still offers one,
    resources.media_offer and the exact-title lookup deciding as for any MEDIA line)."""

    return bool(_PICTURE_REQUEST.search(str(text or "")))


def media_offer(media: tuple[str, str] | None, *, subject: str, topic: str,
                reply_text: str) -> dict[str, Any] | None:
    """What may be offered under a reply: {"kind", "term", "subject"} or None (vetoed).

    `media` is the reply's MEDIA line (kind, term); `subject`/`topic` what the reply (or
    the conversation) is about. Vetoed: no MEDIA line; a term that looks like a link; no
    school subject or "not a school topic"; a short reply; any picture in a language
    subject; a video in a language subject unless the explanation is long and its topic is
    not a word's meaning (vocabulary, translation, definition).
    """

    if not media:
        return None
    kind, term = media
    kind = str(kind or "").casefold()
    term = " ".join(str(term or "").split())[:MAX_TERM_CHARACTERS].strip()
    if kind not in {"diagram", "map", "video"} or len(term) < 3 or _URLISH.search(term):
        return None
    canonical = canonical_subject(subject) if str(subject or "").strip() else ""
    if not canonical or canonical == OTHER or str(topic or "").strip().casefold() == "none":
        return None
    length = len(str(reply_text or "").strip())
    if length < MIN_REPLY_CHARACTERS:
        return None
    if is_language_subject(canonical):
        if kind != "video" or length < MIN_LANGUAGE_VIDEO_CHARACTERS:
            return None
        # A long answer about one word (many examples, a word family) is still a vocabulary
        # definition: the topic or term says so.
        if _VOCABULARY.search(f"{topic} {term}"):
            return None
    return {"kind": kind, "term": term, "subject": canonical}


def strip_video_links(text: str) -> str:
    """Take model-written YouTube links out of a reply: "[label](youtube...)" keeps its label,
    a bare youtube.com / youtu.be address goes. Code blocks are left exactly as written."""

    if not isinstance(text, str) or ("youtu" not in text.casefold()):
        return text
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence or "youtu" not in line.casefold():
            out.append(line)
            continue
        changed = _MARKDOWN_VIDEO_LINK.sub(lambda match: match.group(1), line)
        changed = _BRACKETED_VIDEO_LINK.sub("", changed)
        changed = _BARE_VIDEO_LINK.sub("", changed)
        changed = re.sub(r"[ \t]{2,}", " ", changed).rstrip()
        out.append(changed)
    return "\n".join(out)

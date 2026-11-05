"""Fachbegriff flashcards for Project Mode: stable term keys and deterministic cards.

A project's topic map already holds the Fachbegriffe the model found on the student's
pages, each checked against the page it came from. Turning them into flashcards needs
no further AI call: this module builds the cards from the topic map, and `plan_sync`
decides which of them a project's flashcard set still lacks.

Rules that shape everything here:

* Only content from the notes becomes a card. A term the notes do not contain
  (`in_notes` false) gets no card; a definition the model wrote itself (not verbatim
  from the page) is still useful, but its card says so ("Explanation added by Learnova").
* A page whose reading is flagged for review (a doubtful vision reading, the offline OCR)
  and not checked by the student gives no card yet (`unverified_pages`): a misread word
  would otherwise become a card "from your notes". The first sync after the check makes it.
* Every card passes `flashcards.service.normalize_card` unchanged, so the app can store
  it through the same path as any other card and the editor round-trips it.
* A card's front is a question ("What does the term “X” mean?"), which nobody should
  have to type when the card is studied back to front: `reverse_answer` is the bare term
  or name, the answer expected in that direction. Gap, example and own-words cards only
  work one way and carry the tag ONE_WAY (`flashcards.modes.build_items` honours both).
* Text the notes do not contain is marked: a definition the model wrote, an example or
  an item detail not found on the page (when the caller passes `page_text`) carries
  ADDED_MARKER in the card explanation.
* A card is identified by its `origin_key` ("<topic key>:<variant>:<term or item key>").
  The part after the topic key is the card's identity across topics and re-analyses: the
  same term in two topics is one card, and a re-analysis that moves a term to another
  topic does not add it again.
* Syncing is add-only. A card the student edited keeps their edit and its spaced-
  repetition state; a card the student deleted is not brought back.

Card front texts are English msgids filled with `str.format`; the caller passes its
`tr` as `translate` so they appear in the student's language. Pure: no database, no AI.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from learnova.projects.topics import topic_key
from learnova.vocabulary.service import headword

# What `flashcards.service.normalize_card` keeps of each field. Cards are clipped to these
# limits here, at a word boundary, so normalisation never cuts a card mid-word.
FRONT_LIMIT = 1000
BACK_LIMIT = 2000
EXPLANATION_LIMIT = 1000
HINT_LIMIT = 500
OPTION_LIMIT = 300
SOURCE_LIMIT = 200       # normalize_card keeps 200 (the column holds 255)
ORIGIN_LIMIT = 160       # flashcard.origin_key VARCHAR(160)
TOPIC_KEY_LIMIT = 120    # flashcard.topic_key VARCHAR(120)
TERM_LIMIT = 200
CHOICE_OPTIONS = 4       # the correct definition and three distractors
BLANK = "____"

# Msgids, one per item kind or term card variant. "{name}" is the term or item as written
# in the notes; "{example}" the example text.
CARD_PROMPTS: dict[str, str] = {
    "term": "What does the term “{name}” mean?",
    "term_example": "Which term fits this example? {example}",
    "term_choice": "Which definition fits “{name}”?",
    "theory": "What does the theory “{name}” say?",
    "thought_experiment": "What is the thought experiment “{name}” meant to show?",
    "argument": "What is the argument “{name}”?",
    "position": "What is the position “{name}”?",
    "person": "Who was {name} and what is important about them here?",
    "event": "What happened: {name}?",
    "date": "What happened in {name}?",
    "formula": "What does the formula {name} mean and when do you use it?",
    "rule": "Explain the rule: {name}",
    "method": "How do you use {name}?",
    "process": "Describe the process: {name}",
    "experiment": "What does the experiment “{name}” show?",
    "work": "What is “{name}” about?",
    "place": "What do your notes say about {name}?",
    "institution": "What is the role of {name}?",
    "diagram": "What does the diagram “{name}” show?",
    "example": "What does the example “{name}” show?",
    "source": "What does the source “{name}” say?",
    "term_own_words": "Explain “{name}” in your own words.",
    "cause_effect": "What are the causes and consequences of {name}?",
    "structure": "How is the sentence pattern “{name}” built?",
    # A vocabulary entry with several forms ("go – went – gone"): the forms, not the meaning.
    "term_forms": "Give the forms of “{name}”.",
}
EXAMPLE_LINE = "Example: {example}"
ADDED_MARKER = "Explanation added by Learnova"
# Tag of a card that is only ever asked front to back (gap texts, "which term fits this
# example?", explain in your own words): its back is not a question one could ask back.
ONE_WAY = "one_way"
# Tag of a card whose answer is the student's own wording ("Explain X in your own words"):
# no exact-match check can grade it, so study modes let the student grade it themselves.
OWN_WORDS = "own_words"
# Subjects whose Fachbegriffe are vocabulary: "explain it in your own words" is the
# translation card there, so the own-words variant is left out.
_NO_OWN_WORDS = frozenset({"languages", "latin"})
# Share of an example's or item detail's content words that must be on its pages for the
# text to count as the notes' own (paraphrase and inflection allowed).
VERIFIED_SHARE = 0.8

# Which item kinds of a topic map become flashcards, per subject strategy (the keys of
# `learnova.lessons.strategies.REGISTRY`). Terms always come first. The lists are what a
# student of that subject is examined on: dates and events in history, formulas and
# methods in maths, theories, positions and thought experiments in ethics. Kept here as
# data rather than imported, so this module stays independent of the lesson engine.
CARD_KINDS_BY_STRATEGY: dict[str, tuple[str, ...]] = {
    "languages": ("term", "rule", "structure", "example"),
    "latin": ("term", "rule", "structure", "work", "person", "example"),
    "mathematics": ("term", "formula", "rule", "method", "theory"),
    "sciences": ("term", "formula", "experiment", "process", "theory", "diagram"),
    "physics": ("term", "formula", "experiment", "theory", "process", "rule"),
    "nwt": ("term", "formula", "experiment", "process", "theory", "diagram", "method", "rule"),
    "ethics": ("term", "theory", "position", "argument", "thought_experiment", "person", "work"),
    "religion": ("term", "person", "work", "source", "event", "position", "theory"),
    "german": ("term", "work", "person", "method", "rule"),
    "history": ("term", "event", "date", "person", "cause_effect", "institution", "source", "place"),
    "geography": ("term", "place", "process", "cause_effect", "diagram", "example"),
    "politics": ("term", "institution", "position", "argument", "process", "theory"),
    "computer_science": ("term", "method", "process", "rule", "formula"),
    "technology": ("term", "process", "method", "diagram", "rule"),
    "music": ("term", "work", "person", "rule"),
    "art": ("term", "work", "person", "method"),
    "physical_education": ("term", "rule", "method", "process"),
    "general": ("term", "theory", "formula", "rule", "process", "method", "person", "event"),
}



def card_kinds_for(strategy_key: str | None) -> tuple[str, ...]:
    """The card kinds of a strategy key; a combined key ("history+politics") gets the
    union of its parts' kinds, terms first."""

    lists = [CARD_KINDS_BY_STRATEGY[key] for key in str(strategy_key or "").split("+")
             if key in CARD_KINDS_BY_STRATEGY]
    return tuple(dict.fromkeys(kind for kinds in lists for kind in kinds)) or CARD_KINDS_BY_STRATEGY["general"]


_DIFFICULTY = {1: "easy", 2: "medium", 3: "hard"}
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
# Articles a headword may carry ("Der kategorische Imperativ"); a lesson that says
# "den kategorischen Imperativ" still mentions it.
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer",
                       "eines", "the", "a", "an", "le", "la", "les", "l", "un", "une", "el", "los", "las"})


# --------------------------------------------------------------------------- keys

def term_key(term: str) -> str:
    """Stable identity of a Fachbegriff, the same in every topic and every re-analysis.

    The headword without its vocabulary bookkeeping, folded and stemmed like a topic key:
    "Der kategorische Imperativ" == "Kategorischer Imperativ", "Pflichten" == "Pflicht".
    A slash joins two concepts in a Fachbegriff ("Ursache/Wirkung"), so it is read as a
    space; `headword` alone would drop the second word as a vocabulary comparative. A
    term made only of words a topic key ignores ("Grundlagen") falls back to its folded
    text instead of the shared placeholder "topic".
    """

    head = headword(str(term or "").replace("/", " "))
    key = topic_key(head)
    if key and key != "topic":
        return key[:TOPIC_KEY_LIMIT]
    folded = "-".join(re.findall(r"\w+", head.casefold().translate(_FOLD)))
    return folded[:TOPIC_KEY_LIMIT] or "term"


def origin_identity(origin_key: str) -> str:
    """The topic-independent part of an origin key ("<variant>:<key>")."""

    text = str(origin_key or "")
    return text.split(":", 1)[1] if ":" in text else text


def reverse_answer_after_edit(old_front: str, old_reverse: str, new_front: str) -> str:
    """A card's reverse_answer after the student edited its front.

    The bare term sits inside the front's question ("What does the term “Pflicht” mean?",
    in whatever language the card was made): an edit that keeps the words around it gives
    the new term ("Pflichtgefühl"); any other edit gives "", so reverse study asks for the
    front itself rather than for a term the student replaced. An unchanged front keeps it."""

    old_front, old_reverse, new_front = str(old_front or ""), str(old_reverse or ""), str(new_front or "")
    if " ".join(new_front.split()) == " ".join(old_front.split()):
        return old_reverse
    # Every place the term occurs, not only the first: "a" is in "What", "term" in "the
    # term"; the one whose surrounding words the edit kept is where the term sits.
    at = old_front.find(old_reverse) if old_reverse else -1
    while at >= 0:
        before, after = old_front[:at], old_front[at + len(old_reverse):]
        if ((before or after) and new_front.startswith(before) and new_front.endswith(after)
                and len(new_front) > len(before) + len(after)):
            return new_front[len(before):len(new_front) - len(after)].strip()
        at = old_front.find(old_reverse, at + 1)
    return ""


# A formula is identified by its symbols, not by its words: as words "(a+b)² = a² + 2ab + b²"
# and "(a−b)² = a² − 2ab + b²" are the same set {a2, b2, 2ab}, and "F = m · a" loses its "a"
# as an English article, so the second and third binomial formula would share one card.
_FORMULA_SIGNS = re.compile(r"[=+*/·×÷^²³√<>≤≥≈∑∫−]")
_FORMULA_NOISE = re.compile(r"\\(?:cdot|times|left|right)|[\s$\\{}^*·×⋅∙]")
_FORMULA_FOLD = str.maketrans({"−": "-", "–": "-", "—": "-", ":": "/", "÷": "/"})


def _formula_key(name: str) -> str:
    """Spacing, multiplication signs, "^" and the spelling of minus/division do not matter
    ("F = m · a" == "F=m*a", "a^2" == "a²"); every other symbol does."""

    text = unicodedata.normalize("NFKC", name).casefold().translate(_FOLD).translate(_FORMULA_FOLD)
    key = _FORMULA_NOISE.sub("", text)
    if len(key) > TOPIC_KEY_LIMIT:
        key = key[: TOPIC_KEY_LIMIT - 11] + "~" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
    return key


def item_key(kind: str, name: str) -> str:
    """Identity of a typed item (theory, person, formula ...): its `term_key`, except that a
    formula written in symbols keeps its symbols (see `_formula_key`)."""

    text = str(name or "")
    if kind != "term" and ("=" in text or (kind == "formula" and _FORMULA_SIGNS.search(text))):
        key = _formula_key(text)
        if re.search(r"\w", key):
            return key
    return term_key(text)


def _origin_key(topic: str, variant: str, key: str) -> str:
    identity = f"{variant}:{key}"
    if len(identity) > ORIGIN_LIMIT - 1:
        # Very long keys keep a hash tail so two of them never collide when clipped.
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]
        identity = f"{variant}:{key[:ORIGIN_LIMIT - len(variant) - 13]}~{digest}"
    # The topic part only says where the card was first found; clipping it is harmless.
    room = ORIGIN_LIMIT - len(identity) - 1
    return f"{topic[:max(0, room)]}:{identity}"


# --------------------------------------------------------------------------- text helpers

def _clean(value: Any) -> str:
    if isinstance(value, (dict, list, tuple, set)) or value is None:
        return ""
    return " ".join(str(value).split())


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def _fold(text: str) -> str:
    return " ".join(str(text or "").casefold().translate(_FOLD).split())


def _fill(translate: Callable[[str], str], msgid: str, **values: str) -> str:
    """A translated msgid with its placeholders filled; the English one if the
    translation's placeholders do not match (a broken catalogue entry must not lose a card)."""

    try:
        return translate(msgid).format(**values)
    except (KeyError, IndexError, ValueError, AttributeError, TypeError):
        return msgid.format(**values)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"true", "yes", "1"}


def _page_ids(*values: Any) -> list[int]:
    out: list[int] = []
    for value in values:
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            if isinstance(item, bool):
                continue
            try:
                page = int(item)
            except (TypeError, ValueError):
                continue
            if page not in out:
                out.append(page)
    return out


def _source_reference(page_ids: Sequence[int], source_label: Callable[[int], str]) -> str:
    labels: list[str] = []
    for page in page_ids:
        try:
            label = _clean(source_label(page))
        except Exception:  # a missing label is cosmetic; it must not cost the card
            label = ""
        if label and label not in labels:
            labels.append(label)
    text = ""
    for label in labels:
        joined = f"{text}; {label}" if text else label
        if len(joined) > SOURCE_LIMIT:
            if not text:
                return _clip(label, SOURCE_LIMIT)
            # Say that more pages exist rather than cutting a label in half.
            return text + "; …" if len(text) + 3 <= SOURCE_LIMIT else text
        text = joined
    return text


_INFLECTIONS = ("en", "er", "es", "em", "e", "n", "s")


def _term_pattern(term: str) -> re.Pattern[str]:
    """The term as a whole-word pattern that tolerates inflection: a German sentence says
    "Der kategorische Imperativ" for the term "Kategorischer Imperativ"."""

    parts = []
    for word in term.split():
        if word.isalpha() and len(word) >= 5:
            suffix = next((end for end in _INFLECTIONS
                           if word.casefold().endswith(end) and len(word) - len(end) >= 4), "")
            parts.append(re.escape(word[: len(word) - len(suffix)]) + r"\w{0,3}")
        else:
            parts.append(re.escape(word))
    return re.compile(r"(?<!\w)" + r"\s+".join(parts) + r"(?!\w)", re.IGNORECASE)


def _blank_sentence(quote: str, term: str) -> tuple[str, str] | None:
    """The sentence of `quote` that contains `term`, with the term blanked out.

    Returns (front, the term as the quote writes it), or None when the quote does not
    contain the term as a whole word or too little would be left around the gap."""

    pattern = _term_pattern(term)
    sentence = next((part for part in _SENTENCE_END.split(quote) if pattern.search(part)), "")
    match = pattern.search(sentence)
    if not match:
        return None
    if len(sentence) > 400:
        start = max(0, match.start() - 180)
        end = min(len(sentence), match.end() + 180)
        sentence = ("…" if start else "") + sentence[start:end].strip() + ("…" if end < len(sentence) else "")
        match = pattern.search(sentence)
        if not match:
            return None
    blanked = pattern.sub(BLANK, sentence)
    if len(re.findall(r"\w+", blanked)) < 3:
        return None
    return blanked, match.group(0)


def _hash_order(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:12], 16)


# --------------------------------------------------------------------------- reading topics

def _topic_key_of(topic: Mapping[str, Any]) -> str:
    key = _clean(topic.get("topic_key") or topic.get("key"))
    if not key:
        key = topic_key(_clean(topic.get("title")), _clean(topic.get("main_topic")))
    return key.replace(":", "-")[:TOPIC_KEY_LIMIT]


def _difficulty(topic: Mapping[str, Any]) -> str:
    value = topic.get("difficulty")
    if isinstance(value, str) and value.strip().casefold() in {"easy", "medium", "hard"}:
        return value.strip().casefold()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _DIFFICULTY.get(max(1, min(3, int(value))), "medium")
    return "medium"


def _terms_of(topic: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The topic's terms that are in the notes, plus items of kind "term" (the same thing
    under another heading). Unusable entries are skipped, never the topic."""

    topic_pages = _page_ids(topic.get("source_page_ids") or [])
    raw_terms = list(topic.get("terms") or []) if isinstance(topic.get("terms"), list) else []
    raw_items = topic.get("items") if isinstance(topic.get("items"), list) else []
    for item in raw_items or []:
        if isinstance(item, Mapping) and _clean(item.get("kind")).casefold() == "term":
            raw_terms.append({"term": item.get("name"), "definition": item.get("detail"),
                              "page_id": item.get("page_id"), "page_ids": item.get("page_ids"),
                              "in_notes": item.get("in_notes"),
                              "definition_from_notes": False})
    terms: list[dict[str, Any]] = []
    for raw in raw_terms:
        if not isinstance(raw, Mapping) or not _truthy(raw.get("in_notes")):
            continue
        term = _clip(_clean(raw.get("term")), TERM_LIMIT)
        if not term:
            continue
        definition = _clean(raw.get("definition"))
        simple = _clean(raw.get("simple_definition"))
        terms.append({
            "term": term,
            "key": term_key(term),
            "definition": definition,
            "simple_definition": simple,
            "back": simple or definition,
            "example": _clean(raw.get("example")),
            "quote": _clean(raw.get("quote")),
            "from_notes": _truthy(raw.get("definition_from_notes")),
            "pages": _page_ids(raw.get("page_id"), raw.get("page_ids") or []) or topic_pages,
        })
    return terms


def _items_of(topic: Mapping[str, Any], kinds: Iterable[str]) -> list[dict[str, Any]]:
    wanted = set(kinds) - {"term"}
    topic_pages = _page_ids(topic.get("source_page_ids") or [])
    raw_items = topic.get("items") if isinstance(topic.get("items"), list) else []
    items: list[dict[str, Any]] = []
    for raw in raw_items or []:
        if not isinstance(raw, Mapping) or not _truthy(raw.get("in_notes")):
            continue
        kind = _clean(raw.get("kind")).casefold()
        name = _clip(_clean(raw.get("name")), TERM_LIMIT)
        detail = _clean(raw.get("detail"))
        if kind not in wanted or kind not in CARD_PROMPTS or not name or not detail:
            continue
        items.append({"kind": kind, "name": name, "key": item_key(kind, name), "detail": detail,
                      "pages": _page_ids(raw.get("page_id"), raw.get("page_ids") or []) or topic_pages})
    return items


# --------------------------------------------------------------------------- building cards

class _Deck:
    """The cards built so far, deduplicated by identity and by front text."""

    def __init__(self, *, translate: Callable[[str], str], source_label: Callable[[int], str], per_topic: int,
                 unverified: frozenset[int] = frozenset()):
        self.translate = translate
        self.source_label = source_label
        self.per_topic = per_topic
        self.cards: dict[str, dict[str, Any]] = {}     # identity -> card, in creation order
        self.fronts: set[str] = set()                  # the set editor dedupes on the front, so must we
        self.topic = ""
        self.difficulty = "medium"
        self.made = 0
        self.unverified = unverified
        self.withheld: dict[str, str] = {}             # identity -> origin key, waiting for a check

    def only_unverified(self, pages: Sequence[int]) -> bool:
        return bool(self.unverified) and bool(pages) and set(pages) <= self.unverified

    def start_topic(self, topic: Mapping[str, Any]) -> None:
        self.topic = _topic_key_of(topic)
        self.difficulty = _difficulty(topic)
        self.made = 0

    def text(self, msgid: str, **values: str) -> str:
        return _fill(self.translate, msgid, **values)

    def added_marker(self, term: Mapping[str, Any]) -> str:
        return "" if term["from_notes"] else self.text(ADDED_MARKER)

    def offer(self, variant: str, key: str, card_type: str, front: str, back: str, pages: list[int], *,
              explanation: str = "", options: Sequence[str] = (), extra: bool = False, reverse: str = "",
              one_way: bool = False, own_words: bool = False) -> None:
        """Add one card. `extra` marks a practice variant (gap, example, multiple choice):
        only those count against `per_topic`. A Fachbegriff's or a subject item's own card
        is never capped away; the topic map already bounds how many of them a topic has."""

        identity = f"{variant}:{key}"
        existing = self.cards.get(identity)
        if existing is not None:
            # Found again under a later topic: the first topic's card gains these pages,
            # even when this topic has no room left for new cards.
            existing["source_page_ids"] = _page_ids(existing["source_page_ids"], pages)
            existing["source_reference"] = _source_reference(existing["source_page_ids"], self.source_label)
            return
        if self.only_unverified(pages):
            # Every page it comes from has a reading flagged for review that the student has
            # not checked (D2): a misreading must never become a card "from your notes". It
            # is made by the first sync after the check (sync is add-only, and a card never
            # made was never synced, so nothing counts it as deleted).
            self.withheld.setdefault(identity, _origin_key(self.topic, variant, key))
            return
        front, back = _clip(front, FRONT_LIMIT), _clip(back, BACK_LIMIT)
        marker = front.casefold()
        if (extra and self.made >= self.per_topic) or not front or not back or marker in self.fronts:
            return
        self.fronts.add(marker)
        self.made += 1 if extra else 0
        self.cards[identity] = {
            "origin_key": _origin_key(self.topic, variant, key),
            "topic_key": self.topic,
            "type": card_type,
            "front": front,
            "back": back,
            "explanation": _clip(explanation, EXPLANATION_LIMIT),
            "hint": "",
            "source_reference": _source_reference(pages, self.source_label),
            "source_page_ids": list(pages),
            "tags": ([ONE_WAY] if one_way else []) + ([OWN_WORDS] if own_words else []),
            "options": list(options),
            "image_query": "",
            "difficulty": self.difficulty,
            # The bare term or name; "" for a one-way card (asked front to back only).
            "reverse_answer": "" if one_way else _clip(reverse, BACK_LIMIT),
        }


def _content_words(text: str) -> list[str]:
    return [word for word in _stem_words(text) if len(word) >= 4 and not word.isdigit()]


class _Notes:
    """The page texts a card's wording is checked against (None: nothing to check with)."""

    def __init__(self, page_text: Mapping[int, str] | None):
        self.page_text = page_text
        self.words: dict[int, set[str]] = {}
        self.folded: dict[int, str] = {}

    def _page(self, page: int) -> tuple[set[str], str]:
        if page not in self.words:
            text = str((self.page_text or {}).get(page) or "")
            self.words[page] = set(_stem_words(text))
            self.folded[page] = _fold(text)
        return self.words[page], self.folded[page]

    def has(self, text: str, pages: Sequence[int]) -> bool:
        """Whether `text` is what the notes say on `pages` (all pages when none is known):
        verbatim, or with at least 80% of its content words on those pages."""

        if self.page_text is None or not text.strip():
            return True
        known = [page for page in pages if page in self.page_text] or list(self.page_text)
        words: set[str] = set()
        for page in known:
            page_words, folded = self._page(page)
            if _fold(text) in folded:
                return True
            words |= page_words
        wanted = _content_words(text)
        if not wanted:
            return False
        return sum(word in words for word in wanted) / len(wanted) >= VERIFIED_SHARE


def build_cards(
    topics: Sequence[Mapping[str, Any]],
    *,
    strategy_key: str,
    translate: Callable[[str], str] = lambda message: message,
    source_label: Callable[[int], str] = str,
    per_topic: int = 25,
    card_kinds: Iterable[str] | None = None,
    page_text: Mapping[int, str] | None = None,
    unverified_pages: Iterable[int] = (),
    withheld: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Flashcards for a project's Fachbegriffe and subject items, deterministically.

    `topics` are topic maps in note order: `topics.normalize_topic` output, or a section's
    `topic_map_json` with its "key" (or "topic_key"), "title", "source_page_ids" and
    "difficulty" added. `strategy_key` picks the item kinds that become cards
    (`CARD_KINDS_BY_STRATEGY`, unknown -> "general"); a caller holding the subject strategy
    can pass its `card_kinds` instead, so lessons and cards agree. `source_label(page_id)`
    names a page for the student ("Ethik.pdf, S. 3"). `page_text` (page id -> text) lets
    examples and item details be checked against the notes; what is not found there is
    marked with ADDED_MARKER. Without it nothing is checked (and nothing marked).

    Inside a topic: one definition card per term, then one card per subject item
    (theories, events, formulas ...), then per term a gap card from the verified quote, an
    example card, a multiple-choice card and an own-words card. `per_topic` caps only
    those practice variants, so no Fachbegriff and no item of the notes is silently left
    without a card.

    Each card is {"origin_key", "topic_key", "type", "front", "back", "explanation",
    "hint", "source_reference", "source_page_ids", "tags", "options", "image_query",
    "difficulty", "reverse_answer"} and passes `flashcards.service.normalize_card`
    unchanged (it keeps every field but the four storage keys origin_key, topic_key,
    source_page_ids and reverse_answer, which the caller stores itself).

    `unverified_pages`: page ids whose reading is flagged for review and not checked by the
    student (D2). A card whose every source page is one of them is not made (its origin key
    goes to `withheld`, when given), and their definitions are no distractors: nothing a
    misreading may have produced is shown as the notes' own until the page is checked.
    """

    kinds = tuple(card_kinds) if card_kinds is not None else card_kinds_for(strategy_key)
    notes = _Notes(page_text)
    unverified = frozenset(_page_ids(list(unverified_pages or ())))
    topic_terms = [(topic, _terms_of(topic)) for topic in topics if isinstance(topic, Mapping)]
    # A theory that is also listed as a term gets one card: the term's.
    term_keys = {term["key"] for _, terms in topic_terms for term in terms if term["back"]}
    # Distractors for multiple-choice cards: one definition per term, first sighting wins.
    pool: dict[str, tuple[str, str]] = {}
    for topic, terms in topic_terms:
        for term in terms:
            if unverified and term["pages"] and set(term["pages"]) <= unverified:
                continue
            if term["back"] and len(term["back"]) <= OPTION_LIMIT and term["key"] not in pool:
                pool[term["key"]] = (_topic_key_of(topic), term["back"])

    deck = _Deck(translate=translate, source_label=source_label, per_topic=per_topic, unverified=unverified)
    marker = deck.text(ADDED_MARKER)
    for topic, terms in topic_terms:
        deck.start_topic(topic)
        for term in terms:
            term["example_from_notes"] = notes.has(term["example"], term["pages"]) if term["example"] else True
        for term in terms:
            if not term["back"]:
                continue
            parts = []
            if term["definition"] and _fold(term["definition"]) != _fold(term["back"]):
                parts.append(term["definition"])
            if term["example"]:
                line = deck.text(EXAMPLE_LINE, example=term["example"])
                # An added example under a definition from the notes: only the example is marked.
                parts.append(f"{line} ({marker})" if term["from_notes"] and not term["example_from_notes"] else line)
            parts.append(deck.added_marker(term))
            deck.offer("term", term["key"], "term_definition", deck.text(CARD_PROMPTS["term"], name=term["term"]),
                       term["back"], term["pages"], explanation="\n".join(part for part in parts if part),
                       reverse=term["term"])

        for item in _items_of(topic, kinds):
            if item["key"] in term_keys:
                continue
            card_type = "formula_explanation" if item["kind"] == "formula" else "question_answer"
            deck.offer(item["kind"], item["key"], card_type, deck.text(CARD_PROMPTS[item["kind"]], name=item["name"]),
                       item["detail"], item["pages"],
                       explanation="" if notes.has(item["detail"], item["pages"]) else marker,
                       reverse=item["name"])

        for term in terms:
            # Only a quote verified on the page becomes a gap text: it is the notes' own sentence.
            blank = _blank_sentence(term["quote"], term["term"]) if term["from_notes"] and term["quote"] else None
            if blank and term["back"]:
                deck.offer("term_fill", term["key"], "fill_blank", blank[0], blank[1], term["pages"], extra=True,
                           one_way=True)
        for term in terms:
            if not term["example"] or not term["back"]:
                continue
            example = _term_pattern(term["term"]).sub(BLANK, term["example"])
            if len(re.findall(r"\w+", example.replace(BLANK, " "))) < 2:
                continue    # the example is little more than the term itself
            added = not term["from_notes"] or not term["example_from_notes"]
            deck.offer("term_example", term["key"], "question_answer",
                       deck.text(CARD_PROMPTS["term_example"], example=example), term["term"], term["pages"],
                       explanation="\n".join(part for part in (term["back"], marker if added else "") if part),
                       extra=True, one_way=True)
        for term in terms:
            choice = pool.get(term["key"])
            options = _choice_options(term["key"], deck.topic, choice[1], pool) if choice else []
            if not choice or not options:
                continue
            precise = term["definition"] if _fold(term["definition"]) != _fold(choice[1]) else ""
            deck.offer("term_choice", term["key"], "multiple_choice",
                       deck.text(CARD_PROMPTS["term_choice"], name=term["term"]), choice[1], term["pages"],
                       explanation="\n".join(part for part in (precise, deck.added_marker(term)) if part),
                       options=options, extra=True, reverse=term["term"])
        if set(str(strategy_key or "").split("+")) <= _NO_OWN_WORDS:
            for term in terms:
                # An irregular verb or noun with its forms ("go – went – gone") is practised
                # by its forms; the definition card already asks for its meaning.
                forms = _forms(term["term"])
                if forms and term["back"]:
                    deck.offer("term_forms", term["key"], "question_answer",
                               deck.text(CARD_PROMPTS["term_forms"], name=forms[0]), term["term"], term["pages"],
                               extra=True, one_way=True)
            continue
        for term in terms:
            # Explaining a Fachbegriff in one's own words is checked against the notes'
            # definition, so only a definition from the notes makes this card.
            if not term["from_notes"] or not term["back"]:
                continue
            deck.offer("term_own_words", term["key"], "question_answer",
                       deck.text(CARD_PROMPTS["term_own_words"], name=term["term"]), term["back"], term["pages"],
                       explanation=term["definition"] if _fold(term["definition"]) != _fold(term["back"]) else "",
                       extra=True, one_way=True, own_words=True)
    if withheld is not None:
        # Made under another topic from a checked page after all: not waiting.
        withheld.extend(origin for identity, origin in deck.withheld.items() if identity not in deck.cards)
    return list(deck.cards.values())


_DASH_FORMS = re.compile(r"\s+[-–—]\s+|\s*[–—]\s*")
_LIST_FORMS = re.compile(r"\s*[/,;]\s*")


def _forms(name: str) -> list[str]:
    """The forms of a vocabulary entry written with its forms ("go – went – gone",
    "child – children", "lego, legi, lectum"), or [] for a single word or phrase. A list
    with commas needs three forms: "however, nevertheless" is two words, not forms."""

    text = str(name or "")
    for pattern, least in ((_DASH_FORMS, 2), (_LIST_FORMS, 3)):
        forms = [form.strip() for form in pattern.split(text) if form.strip()]
        if len(forms) >= least and all(len(form.split()) <= 3 for form in forms):
            return forms
    return []


def _choice_options(key: str, topic: str, correct: str, pool: Mapping[str, tuple[str, str]]) -> list[str]:
    """The correct definition among three other terms' definitions, or [] when the project
    has fewer than three. Distractors from the same topic come first (they are the ones a
    student actually confuses); the choice and the correct position are hash-ordered, so a
    re-sync builds the same card."""

    seen = {_fold(correct)}
    candidates = []
    for other, (other_topic, text) in pool.items():
        if other == key or _fold(text) in seen:
            continue
        seen.add(_fold(text))
        candidates.append((other_topic != topic, _hash_order(key, other), text))
    if len(candidates) < CHOICE_OPTIONS - 1:
        return []
    distractors = [text for _, _, text in sorted(candidates)[: CHOICE_OPTIONS - 1]]
    position = _hash_order(key) % CHOICE_OPTIONS
    return distractors[:position] + [correct] + distractors[position:]


# --------------------------------------------------------------------------- syncing

def _existing_pages(value: Any) -> list[int]:
    if isinstance(value, Mapping):
        value = value.get("source_page_ids", value.get("source_page_ids_json", []))
    elif hasattr(value, "source_page_ids_json"):        # a stored card row
        value = getattr(value, "source_page_ids_json")
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except ValueError:
            value = []
    return _page_ids(value if isinstance(value, (list, tuple, set)) else [])


def plan_sync(
    existing: Mapping[str, Any],
    synced_before: Iterable[str] | None,
    cards: Iterable[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, list[int]]]]:
    """What a sync adds to a project's set, without touching what is there.

    `existing`: origin key -> that card's page ids (a list, its `source_page_ids_json`
    string, a mapping with "source_page_ids", or the card row itself) for the set's cards
    that have an origin key. `synced_before`: every origin key synced earlier
    (`analysis_json["flashcard_keys"]`).

    Returns (cards to add, [(existing origin key, its page ids united with the new ones)]).
    A card is added only when neither its origin key nor its identity (the same term
    found under another topic) is in the set, and the student did not delete it: a key
    synced before that is no longer in the set was deleted on purpose. A page union is
    listed only when it adds pages. Content and spaced-repetition state of existing cards
    are never part of the plan. Afterwards the caller stores
    `set(synced_before) | set(existing) | {card["origin_key"] for card in added}`.
    """

    # `analysis_json["flashcard_keys"]` comes back from JSON as a list, or missing.
    existing = existing if isinstance(existing, Mapping) else {}
    synced = {str(origin) for origin in (synced_before or ()) if origin}
    by_identity: dict[str, str] = {}
    for origin in existing:
        by_identity.setdefault(origin_identity(origin), origin)
    deleted = {origin_identity(origin) for origin in synced} - set(by_identity)
    unions: dict[str, list[int]] = {}
    added: list[dict[str, Any]] = []
    planned: set[str] = set()
    for card in cards or ():
        if not isinstance(card, Mapping):
            continue
        origin = str(card.get("origin_key") or "")
        if not origin:
            continue
        identity = origin_identity(origin)
        target = origin if origin in existing else by_identity.get(identity)
        if target is not None:
            current = unions.get(target) or _existing_pages(existing[target])
            merged = _page_ids(current, card.get("source_page_ids") or [])
            if len(merged) > len(_existing_pages(existing[target])):
                unions[target] = merged
            continue
        if identity in deleted or origin in synced or identity in planned:
            continue
        planned.add(identity)
        added.append(dict(card))
    return added, list(unions.items())


# --------------------------------------------------------------------------- lesson evidence

def _stem_words(text: str) -> list[str]:
    words = re.findall(r"\w+", _fold(text))
    out = []
    for word in words:
        if word in _ARTICLES:
            continue
        for suffix in ("ungen", "ung", "en", "er", "es", "em", "e", "n", "s"):
            if len(word) - len(suffix) >= 4 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        out.append(word)
    return out


def terms_mentioned(terms: Iterable[Mapping[str, Any]], text: str) -> list[str]:
    """Keys of the terms a lesson text actually uses (evidence for "terms taught").

    A term counts when every word of its headword occurs in the text, allowing German and
    English inflection ("kategorischen Imperativs" mentions "kategorischer Imperativ").
    Entries are term dicts ({"term"}) or item dicts ({"kind", "name"}); keys are the
    cards' keys (`term_key`, `item_key` for items), so two formulas stay two."""

    present = set(_stem_words(str(text or "")))
    found: list[str] = []
    for entry in terms or ():
        if not isinstance(entry, Mapping):
            continue
        name = _clean(entry.get("term") or entry.get("name"))
        words = _stem_words(headword(name.replace("/", " ")))
        if not words:
            continue
        key = term_key(name) if entry.get("term") else item_key(_clean(entry.get("kind")).casefold(), name)
        if key not in found and all(word in present for word in words):
            found.append(key)
    return found

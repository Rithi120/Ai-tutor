"""Topic extraction for Project Mode: batching, normalisation, merging and reconciliation.

A 20-page document can hold 15 topics, and the same topic often runs over several pages
or is repeated on a revision sheet. The model reads the pages in batches that fit its
input budget (one call for everything used to overflow it); this module turns the batch
results into one clean topic list and reconciles it with what the student already has.

Everything here is deterministic:

* `page_batches` packs consecutive pages into prompt-sized batches.
* `normalize_topic` validates one model topic leniently: an unusable recall card or list
  item is dropped, never the whole plan. A topic must cite at least one real page.
* `topic_key` is the stable identity of a topic ("Newton's laws" == "Newtonsche Gesetze"
  is a translation the key cannot see, but "1. Newton's Laws" == "Newton's laws" is).
* `merge_topics` merges duplicates across batches and pages by key and word overlap.
* `reconcile` decides, for a re-analysis, which stored topics are updated in place, which
  are new, and which are kept although the new scan no longer mentions them (a topic the
  student renamed, added, or already learned is never deleted).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

MAX_BATCH_CHARS = 28_000          # ~7k tokens of page text per extraction call
MAX_PAGE_CHARS = 9_000
MAX_TOPICS = 30
MERGE_OVERLAP = 0.6
LIST_FIELDS = ("learning_goals", "important_facts", "definitions", "formulas", "examples",
               "vocabulary", "relationships", "likely_exam_questions")
LIST_LIMITS = {"learning_goals": 10, "important_facts": 20, "definitions": 20, "formulas": 15,
               "examples": 15, "vocabulary": 25, "relationships": 15, "likely_exam_questions": 12}
DIFFICULTY_WORDS = {"easy": 1, "leicht": 1, "medium": 2, "mittel": 2, "hard": 3, "schwer": 3, "difficult": 3}

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_STOP = frozenset({
    "der", "die", "das", "und", "oder", "von", "mit", "im", "in", "am", "an", "zu", "zum", "zur",
    "ein", "eine", "einer", "des", "dem", "den", "fuer", "auf", "the", "and", "of", "to", "a", "an",
    "for", "with", "on", "kapitel", "chapter", "thema", "topic", "teil", "part", "einfuehrung",
    "introduction", "grundlagen", "basics",
})


def _words(text: str) -> list[str]:
    text = str(text or "").casefold().translate(_FOLD)
    words = re.findall(r"[a-z0-9]+", text)
    out = []
    for word in words:
        if word in _STOP or word.isdigit():
            continue
        for suffix in ("ungen", "ung", "en", "er", "es", "e", "s"):
            if len(word) - len(suffix) >= 4 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        out.append(word)
    return out


def topic_key(title: str, main_topic: str = "") -> str:
    """Stable identity: the folded, stemmed content words of the title, sorted."""

    words = sorted(set(_words(title))) or sorted(set(_words(main_topic)))
    return "-".join(words)[:120] or "topic"


def _overlap(first: str, second: str) -> float:
    a, b = set(first.split("-")), set(second.split("-"))
    return len(a & b) / max(1, min(len(a), len(b))) if a and b else 0.0


# --------------------------------------------------------------------------- batching

def page_batches(pages: Iterable[dict[str, Any]], *, max_chars: int = MAX_BATCH_CHARS) -> list[list[dict[str, Any]]]:
    """Consecutive pages packed into batches of at most `max_chars` of text."""

    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    size = 0
    for page in pages:
        text = str(page.get("text") or "")[:MAX_PAGE_CHARS]
        page = {**page, "text": text}
        if current and size + len(text) > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(page)
        size += len(text)
    if current:
        batches.append(current)
    return batches


# --------------------------------------------------------------------------- normalising

def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit] if not isinstance(value, (dict, list)) else ""


def _items(value: Any, limit: int) -> list[Any]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, str):
            item = item.strip()
            if item:
                items.append(item[:1200])
        elif isinstance(item, dict) and item:
            items.append({str(k)[:60]: (v if isinstance(v, (int, float, bool)) else _text(v, 1200)) for k, v in item.items()})
        if len(items) >= limit:
            break
    return items


def _level(value: Any, default: int = 2) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return max(1, min(3, int(value)))
    return DIFFICULTY_WORDS.get(str(value or "").strip().casefold(), default)


def normalize_topic(raw: Any, valid_page_ids: set[int], *, page_text: dict[int, str] | None = None) -> dict[str, Any] | None:
    """One model topic, made safe; None when it cites no real page or has no title."""

    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title"), 255)
    pages: list[int] = []
    for value in raw.get("source_page_ids") or []:
        try:
            page_id = int(value)
        except (TypeError, ValueError):
            continue
        if page_id in valid_page_ids and page_id not in pages:
            pages.append(page_id)
    if not title or not pages:
        return None
    topic: dict[str, Any] = {
        "title": title,
        "main_topic": _text(raw.get("main_topic"), 255) or title,
        "source_page_ids": pages,
        "simple_explanation": _text(raw.get("simple_explanation"), 6000),
        "standard_explanation": _text(raw.get("standard_explanation"), 8000),
        "detailed_explanation": _text(raw.get("detailed_explanation"), 12000),
        "difficulty": _level(raw.get("difficulty")),
        "importance": _level(raw.get("importance")),
        "prerequisites": [_text(item, 255) for item in raw.get("prerequisites") or [] if _text(item, 255)][:6],
    }
    try:
        minutes = int(raw.get("estimated_minutes") or 15)
    except (TypeError, ValueError):
        minutes = 15
    topic["estimated_minutes"] = max(5, min(45, minutes))
    for name in LIST_FIELDS:
        topic[name] = _items(raw.get(name), LIST_LIMITS[name])
    # A recall card is kept only when its quote is really on one of its pages: the model
    # must not be able to put words in the teacher's mouth.
    source = "\n".join((page_text or {}).get(page_id, "") for page_id in pages).casefold()
    cards = []
    for card in raw.get("recall_cards") or []:
        if not isinstance(card, dict):
            continue
        prompt, answer = _text(card.get("prompt"), 1000), _text(card.get("answer"), 1000)
        quote = _text(card.get("source_text"), 1000)
        if not prompt or not answer or not quote:
            continue
        if page_text is not None and " ".join(quote.casefold().split()) not in " ".join(source.split()):
            continue
        cards.append({"kind": _text(card.get("kind"), 40) or "recall", "prompt": prompt, "answer": answer, "source_text": quote})
    topic["recall_cards"] = cards[:20]
    topic["key"] = topic_key(title, topic["main_topic"])
    return topic


# --------------------------------------------------------------------------- merging

def _merge_lists(first: list[Any], second: list[Any], limit: int) -> list[Any]:
    seen, out = set(), []
    for item in first + second:
        marker = " ".join(str(item).casefold().split())
        if marker in seen:
            continue
        seen.add(marker)
        out.append(item)
    return out[:limit]


def _absorb(into: dict[str, Any], other: dict[str, Any]) -> None:
    into["source_page_ids"] = sorted(set(into["source_page_ids"]) | set(other["source_page_ids"]),
                                     key=lambda page: (into["_order"].get(page, 0), page))
    for name in LIST_FIELDS:
        into[name] = _merge_lists(into[name], other[name], LIST_LIMITS[name])
    for name in ("simple_explanation", "standard_explanation", "detailed_explanation"):
        if len(other[name]) > len(into[name]):
            into[name] = other[name]
    into["recall_cards"] = _merge_lists(into["recall_cards"], other["recall_cards"], 20)
    into["prerequisites"] = _merge_lists(into["prerequisites"], other["prerequisites"], 6)
    into["estimated_minutes"] = min(45, max(into["estimated_minutes"], other["estimated_minutes"]) + 5)
    into["difficulty"] = max(into["difficulty"], other["difficulty"])
    into["importance"] = max(into["importance"], other["importance"])


def merge_topics(topics: Iterable[dict[str, Any]], *, page_order: dict[int, int]) -> list[dict[str, Any]]:
    """Merge duplicates (same key, or a title whose words largely overlap another's).

    Order is the order of the first page each topic appears on, so the list follows the
    notes. Prerequisite titles are mapped to the merged topics' keys; a prerequisite that
    is not one of the topics is dropped (it is not evidence about this material)."""

    merged: list[dict[str, Any]] = []
    for arrival, topic in enumerate(topics):
        topic = {**topic, "_order": page_order, "_arrival": arrival}
        match = next((existing for existing in merged
                      if existing["key"] == topic["key"] or _overlap(existing["key"], topic["key"]) >= MERGE_OVERLAP), None)
        if match:
            _absorb(match, topic)
        else:
            merged.append(topic)
    # Note order: the first page a topic appears on, then the order the notes present
    # topics that start on the same page.
    merged.sort(key=lambda item: (min(page_order.get(page, 0) for page in item["source_page_ids"]), item["_arrival"]))
    merged = merged[:MAX_TOPICS]
    keys_by_title = {}
    for topic in merged:
        keys_by_title[topic_key(topic["title"])] = topic["key"]
    for position, topic in enumerate(merged, start=1):
        topic.pop("_order", None)
        topic.pop("_arrival", None)
        topic["position"] = position
        resolved = []
        for title in topic["prerequisites"]:
            wanted = topic_key(title)
            key = keys_by_title.get(wanted) or next(
                (other["key"] for other in merged if _overlap(other["key"], wanted) >= MERGE_OVERLAP), None)
            if key and key != topic["key"] and key not in resolved:
                resolved.append(key)
        topic["prerequisites"] = resolved
    return merged


# --------------------------------------------------------------------------- reconciling

@dataclass(frozen=True)
class StoredTopic:
    id: int
    key: str
    title: str
    user_edited: bool = False      # renamed, merged, or added by the student
    has_progress: bool = False     # lessons, attempts or recall answers exist


@dataclass
class Reconciliation:
    update: list[tuple[int, dict[str, Any]]] = field(default_factory=list)   # (stored id, new content)
    create: list[dict[str, Any]] = field(default_factory=list)
    keep: list[int] = field(default_factory=list)       # not in the new scan, but kept
    delete: list[int] = field(default_factory=list)


def reconcile(stored: Iterable[StoredTopic], extracted: Iterable[dict[str, Any]]) -> Reconciliation:
    """Match a new extraction against the stored topics without losing anything."""

    stored = list(stored)
    result = Reconciliation()
    claimed: set[int] = set()
    for topic in extracted:
        match = next((item for item in stored if item.id not in claimed and item.key == topic["key"]), None) or next(
            (item for item in stored if item.id not in claimed and _overlap(item.key, topic["key"]) >= MERGE_OVERLAP), None)
        if match:
            claimed.add(match.id)
            result.update.append((match.id, topic))
        else:
            result.create.append(topic)
    for item in stored:
        if item.id in claimed:
            continue
        if item.user_edited or item.has_progress:
            result.keep.append(item.id)
        else:
            result.delete.append(item.id)
    return result

"""Turn AI-suggested topics into reliable study media.

The model only ever supplies short topics / search phrases. Every URL here is either
built deterministically (video search links) or fetched from Wikipedia (real images) —
never invented by the model, so links and pictures are always valid.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any


_WIKI_TIMEOUT = 4.0
_USER_AGENT = "LearnovaTutor/1.0 (educational study app; contact via app)"


def _clean_terms(terms: Any, limit: int) -> list[str]:
    """Coerce whatever the model returned into a short, de-duplicated list of strings."""

    if limit < 1:
        return []
    if isinstance(terms, str):
        terms = [terms]
    if not isinstance(terms, (list, tuple)):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        text = str(term).strip()[:120]
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def youtube_search_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)


def studyflix_search_url(query: str) -> str:
    return "https://studyflix.de/suche?q=" + urllib.parse.quote_plus(query)


def _video_entries(items: Any, limit: int) -> list[tuple[str, str]]:
    """Normalize model output into (query, why) pairs; accepts objects or plain strings."""

    if limit < 1:
        return []
    if isinstance(items, (str, dict)):
        items = [items]
    if not isinstance(items, (list, tuple)):
        return []
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            query = str(item.get("query") or item.get("title") or item.get("term") or "").strip()
            why = str(item.get("why") or item.get("reason") or "").strip()
        else:
            query, why = str(item).strip(), ""
        query, why = query[:120], why[:200]
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            result.append((query, why))
        if len(result) >= limit:
            break
    return result


def video_links(items: Any, limit: int = 3) -> list[dict[str, str]]:
    """Build guaranteed-valid YouTube + Studyflix search links from AI video topics."""

    return [
        {
            "title": query,
            "why": why,
            "youtube": youtube_search_url(query),
            "studyflix": studyflix_search_url(query),
        }
        for query, why in _video_entries(items, limit)
    ]


def _wikipedia_image(query: str, language: str) -> dict[str, str] | None:
    """Fetch one real, free-licensed image for a topic from Wikipedia's API."""

    lang = "de" if str(language).lower().startswith("de") else "en"
    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "prop": "pageimages|info",
        "piprop": "thumbnail", "pithumbsize": "640", "inprop": "url",
        "generator": "search", "gsrsearch": query, "gsrlimit": "1", "redirects": "1",
    })
    url = f"https://{lang}.wikipedia.org/w/api.php?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_WIKI_TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))
    pages = (payload.get("query") or {}).get("pages") or {}
    for page in pages.values():
        thumb = (page.get("thumbnail") or {}).get("source")
        if isinstance(thumb, str) and thumb.startswith("https://") and thumb.split("/")[2].endswith("wikimedia.org"):
            return {
                "url": thumb,
                "title": str(page.get("title") or query),
                "page_url": str(page.get("fullurl") or ""),
                "source": "Wikimedia Commons",
            }
    return None


def lesson_images(terms: Any, language: str, limit: int = 2) -> list[dict[str, str]]:
    """Resolve AI image topics to real Wikimedia images, skipping any that fail."""

    images: list[dict[str, str]] = []
    seen: set[str] = set()
    for term in _clean_terms(terms, limit):
        try:
            image = _wikipedia_image(term, language)
        except Exception:
            image = None  # a failed lookup must never break lesson creation
        if image and image["url"] not in seen:
            seen.add(image["url"])
            images.append(image)
    return images

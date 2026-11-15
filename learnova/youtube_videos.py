"""Real educational videos from the YouTube Data API v3 - found, checked, never invented.

Built like learnova/vocabulary/translate.py: HTTP is a seam (`http`), nothing here raises at
the caller, and every decision about a video is a pure function of a recorded API response,
so it is tested without a network (tests/test_youtube_videos.py).

What a card is made of, and where each part comes from:

- the video id - from search.list, then confirmed by videos.list (a video that is deleted,
  private, unprocessed, age-restricted or blocked in the student's region is dropped there);
- title, channel name, duration, language - exactly as videos.list returns them;
- the watch link, channel link and thumbnail - built from the validated ids, never from model
  text and never from a URL in the response. The thumbnail is served through the app's own
  proxy (/media/yt-thumb/<id>), so a student's browser never contacts Google for it.

The model only ever supplies a topic. A search query is assembled on the server from stored
lesson/chat data in the content language; the browser never sends search text.

The API key travels only in the X-Goog-Api-Key header. It is never put in a URL (requests and
urllib quote the URL in their exceptions, and those reach the logs) and no reason string here
is built from an exception's text.

Quota (docs/LEARNING_RESOURCES.md): search.list costs 100 units, videos.list 1, of a default
10,000 units per day that resets at midnight US Pacific time. The app caches every search and
caps searches per day; this module only reports `reason="quota"` when Google says so.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
THUMBNAIL_URL = "https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
CHANNEL_URL = "https://www.youtube.com/channel/{channel_id}"

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
REGION_CODE = re.compile(r"^[A-Z]{2}$")

DEFAULT_TIMEOUT = 4.0
SEARCH_RESULTS = 8               # candidates per search; most are dropped by the checks
MAX_QUERY_CHARS = 160
MAX_TITLE_CHARS = 200
MAX_CHANNEL_CHARS = 100
MIN_SECONDS = 60                 # below this it is a Short, not an explanation
MAX_SECONDS = 30 * 60            # above this it is a lecture recording, not a supplement
MAX_THUMBNAIL_BYTES = 300_000

# Titles that announce something other than teaching.
NOT_EDUCATIONAL = re.compile(
    r"(#shorts?\b|\bshorts\b|\breaction\b|\breagier|\bprank\b|\blyrics?\b|\bsongtext\b"
    r"|official\s+(?:music\s+)?video|\bmusikvideo\b|\btiktok\b|\bmeme|\bcompilation\b|\basmr\b)",
    re.IGNORECASE)

# Words that say nothing about the topic, so they never decide relevance.
STOPWORDS = frozenset("""
a an and are as at be by das dem den der des die ein eine einer eines einfach erklaert erklart erklärt
explained for from how im in ist mit of on or the to und von was what wie with zu zum zur
klasse grade class video lernen learn tutorial
""".split())

HttpCall = Callable[..., tuple[int, str]]
BytesCall = Callable[..., tuple[int, bytes, str]]


@dataclass(frozen=True)
class VideoResult:
    """Cards that passed, every id that did not (with the reason), and why nothing came back."""

    cards: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)
    reason: str = "ok"           # ok | no_results | quota | http_<status> | <ExceptionName> | no_key
    searched: bool = False       # a search.list call was made (100 units)


def _http_json(method: str, url: str, *, params: dict[str, Any] | None = None,
               headers: dict[str, str] | None = None, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """The one place the API is reached (the same requests call the translation providers use)."""

    from learnova.vocabulary.translate import _http_requests  # noqa: PLC0415 - one network seam

    return _http_requests(method, url, params=params, headers=headers, timeout=timeout)


def _http_bytes(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, bytes, str]:
    """GET a thumbnail: (status, body, content type). No cookies, no referrer."""

    import requests  # pyright: ignore[reportMissingImports]  # noqa: PLC0415

    response = requests.get(url, timeout=timeout, allow_redirects=False,
                            headers={"User-Agent": "LearnovaTutor/1.0"})
    return response.status_code, response.content[:MAX_THUMBNAIL_BYTES + 1], response.headers.get("Content-Type", "")


# --------------------------------------------------------------------------- #
# Pure parts
# --------------------------------------------------------------------------- #

def fold(text: Any) -> str:
    """Case, accents and punctuation do not matter when comparing words."""

    folded = unicodedata.normalize("NFKD", str(text or "").replace("ß", "ss"))
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", folded.casefold()).strip()


def topic_tokens(topic: Any) -> list[str]:
    """The words of a topic that must appear in a relevant video ("Ohmsches Gesetz" -> 2)."""

    tokens: list[str] = []
    for word in fold(topic).split():
        if len(word) >= 3 and word not in STOPWORDS and not word.isdigit() and word not in tokens:
            tokens.append(word)
    return tokens[:8]


def _word_matches(token: str, words: list[str]) -> bool:
    """A token matches a word with the same stem: "ohmsches" ~ "ohmsche", "gesetz" ~ "gesetze"."""

    stem = token[:max(4, len(token) - 2)]
    return any(word.startswith(stem) or (len(word) >= 4 and token.startswith(word)) for word in words)


def relevance(tokens: list[str], title: str, context: str) -> tuple[bool, float]:
    """(relevant, share): one topic word in the title and half of them anywhere."""

    if not tokens:
        return False, 0.0
    title_words = fold(title).split()
    all_words = title_words + fold(context).split()
    in_title = sum(1 for token in tokens if _word_matches(token, title_words))
    anywhere = sum(1 for token in tokens if _word_matches(token, all_words))
    share = anywhere / len(tokens)
    return in_title >= 1 and share >= 0.5, share


def parse_duration(value: Any) -> int | None:
    """ISO 8601 duration ("PT12M3S", "P0D", "PT1H") in seconds, or None."""

    match = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?", str(value or ""))
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def language_code(content_language: Any) -> str:
    """Two-letter code from a language name ("German") or code ("de")."""

    from learnova.media_enrichment import wiki_language  # noqa: PLC0415 - shared mapping

    return wiki_language(str(content_language or ""))


def clean_query(query: Any) -> str:
    """A search query is plain words: no URLs, no control characters, bounded."""

    text = re.sub(r"https?://\S+|www\.\S+", " ", str(query or ""))
    # "|" is YouTube's OR operator: "Ohm | Physik" would search for either word.
    text = re.sub(r"[\x00-\x1f<>\"`|]", " ", text)
    return " ".join(text.split())[:MAX_QUERY_CHARS]


def search_params(query: str, *, language: str, region: str, limit: int = SEARCH_RESULTS) -> dict[str, Any]:
    """search.list parameters. The key is NOT here: it goes in the X-Goog-Api-Key header."""

    params: dict[str, Any] = {
        "part": "snippet", "type": "video", "q": clean_query(query),
        "maxResults": max(1, min(25, int(limit))), "safeSearch": "strict",
        "relevanceLanguage": language_code(language),
        "videoEmbeddable": "any",
    }
    if REGION_CODE.match(str(region or "")):
        params["regionCode"] = region
    return params


def videos_params(ids: list[str]) -> dict[str, Any]:
    """videos.list parameters (1 unit): existence, status, duration, rating, region."""

    return {"part": "snippet,status,contentDetails", "id": ",".join(ids[:50]), "maxResults": 50}


def key_headers(key: str) -> dict[str, str]:
    return {"X-Goog-Api-Key": key, "Accept": "application/json"}


def search_ids(payload: Any) -> list[str]:
    """Video ids from a search.list answer, in ranking order, validated, without duplicates."""

    items = payload.get("items") if isinstance(payload, dict) else None
    ids: list[str] = []
    for item in items if isinstance(items, list) else []:
        identifier = (item.get("id") or {}) if isinstance(item, dict) else {}
        video_id = identifier.get("videoId") if isinstance(identifier, dict) else None
        if isinstance(video_id, str) and VIDEO_ID.match(video_id) and video_id not in ids:
            ids.append(video_id)
    return ids


def judge_video(item: Any, *, tokens: list[str], region: str = "",
                check_relevance: bool = True) -> tuple[bool, str]:
    """Whether one videos.list item may be recommended. Pure: every refusal is named.

    `check_relevance=False` (a re-check of a card already chosen) asks only whether the
    video is still available, public, unrestricted and still a teaching-length video.
    """

    if not isinstance(item, dict) or not VIDEO_ID.match(str(item.get("id") or "")):
        return False, "unavailable"
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    if status.get("privacyStatus") != "public":
        return False, "not_public"
    if status.get("uploadStatus") not in (None, "processed"):
        return False, "not_processed"
    rating = details.get("contentRating") if isinstance(details.get("contentRating"), dict) else {}
    if rating.get("ytRating") == "ytAgeRestricted":
        return False, "age_restricted"
    restriction = details.get("regionRestriction") if isinstance(details.get("regionRestriction"), dict) else {}
    if region and (region in (restriction.get("blocked") or [])
                   or ("allowed" in restriction and region not in (restriction.get("allowed") or []))):
        return False, "region_blocked"
    if snippet.get("liveBroadcastContent") in ("live", "upcoming"):
        return False, "live"
    seconds = parse_duration(details.get("duration"))
    if seconds is None:
        return False, "no_duration"
    if seconds < MIN_SECONDS:
        return False, "too_short"
    if seconds > MAX_SECONDS:
        return False, "too_long"
    title = str(snippet.get("title") or "").strip()
    channel = str(snippet.get("channelTitle") or "").strip()
    if not title or not channel or not CHANNEL_ID.match(str(snippet.get("channelId") or "")):
        return False, "incomplete"
    if NOT_EDUCATIONAL.search(title):
        return False, "not_educational"
    if not check_relevance:
        return True, "ok"
    tags = " ".join(str(tag) for tag in (snippet.get("tags") or []) if isinstance(tag, str))
    relevant, _share = relevance(tokens, title, f"{snippet.get('description') or ''} {tags} {channel}")
    if not relevant:
        return False, "not_relevant"
    return True, "ok"


def item_language(item: dict[str, Any]) -> str:
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    value = str(snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "")
    return value.split("-")[0].casefold()[:5]


def rank(items: list[dict[str, Any]], *, language: str, trusted: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    """Videos in the lesson's language first, then trusted channels (a boost, never a filter),
    then the search order YouTube gave."""

    code = language_code(language)

    def order(pair: tuple[int, dict[str, Any]]) -> tuple[int, int, int]:
        index, item = pair
        channel = str((item.get("snippet") or {}).get("channelId") or "")
        return (0 if item_language(item) == code else 1, 0 if channel in trusted else 1, index)

    return [item for _index, item in sorted(enumerate(items), key=order)]


def card(item: dict[str, Any]) -> dict[str, Any] | None:
    """What the page shows for one checked video. Links are built from validated ids only."""

    video_id = str(item.get("id") or "")
    snippet = item.get("snippet") if isinstance(item.get("snippet"), dict) else {}
    channel_id = str(snippet.get("channelId") or "")
    if not VIDEO_ID.match(video_id) or not CHANNEL_ID.match(channel_id):
        return None
    details = item.get("contentDetails") if isinstance(item.get("contentDetails"), dict) else {}
    return {
        "video_id": video_id,
        "url": WATCH_URL.format(video_id=video_id),
        "thumbnail": f"/media/yt-thumb/{video_id}",
        "title": " ".join(str(snippet.get("title") or "").split())[:MAX_TITLE_CHARS],
        "channel": " ".join(str(snippet.get("channelTitle") or "").split())[:MAX_CHANNEL_CHARS],
        "channel_url": CHANNEL_URL.format(channel_id=channel_id),
        "duration_seconds": parse_duration(details.get("duration")) or 0,
        "language": item_language(item),
        "source": "YouTube",
        "supplementary": True,
    }


def _quota_exhausted(status: int, body: str) -> bool:
    return status in (403, 429) and any(word in str(body or "") for word in (
        "quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"))


def _json(body: str) -> dict[str, Any]:
    try:
        data = json.loads(body)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------- #
# Network parts (never raise)
# --------------------------------------------------------------------------- #

def _call(http: HttpCall, url: str, params: dict[str, Any], key: str, timeout: float) -> tuple[int, str, str]:
    """(status, body, failure reason). The reason never quotes the exception: it may carry the URL."""

    try:
        status, body = http("GET", url, params=params, headers=key_headers(key), timeout=timeout)
    except Exception as error:  # noqa: BLE001 - a timeout, a DNS failure, anything
        return 0, "", type(error).__name__
    if _quota_exhausted(status, body):
        return status, body, "quota"
    if status != 200:
        return status, body, f"http_{status}"
    return status, body, ""


def lookup_items(ids: list[str], *, key: str, http: HttpCall | None = None,
                 timeout: float = DEFAULT_TIMEOUT) -> tuple[dict[str, dict[str, Any]] | None, str]:
    """videos.list for `ids`: ({id: item} of the videos that still exist, reason). None on failure."""

    ids = [video_id for video_id in ids if VIDEO_ID.match(str(video_id))]
    if not ids:
        return {}, "ok"
    _status, body, reason = _call(http or _http_json, VIDEOS_URL, videos_params(ids), key, timeout)
    if reason:
        return None, reason
    items = _json(body).get("items")
    found = {str(item.get("id")): item for item in items if isinstance(item, dict)} if isinstance(items, list) else {}
    return found, "ok"


def find_videos(query: str, *, topic: str, key: str, language: str, region: str = "",
                limit: int = 2, http: HttpCall | None = None, timeout: float = DEFAULT_TIMEOUT,
                trusted: frozenset[str] = frozenset()) -> VideoResult:
    """Search, confirm and rank: up to `limit` cards. Never raises.

    `query` is what is searched (topic plus subject and class, in the content language);
    `topic` alone decides relevance, so "Physik Klasse 8" in every title proves nothing.
    """

    if not key:
        return VideoResult(reason="no_key")
    query = clean_query(query)
    tokens = topic_tokens(topic) or topic_tokens(query)
    if not query or not tokens:
        return VideoResult(reason="no_results")
    http = http or _http_json
    _status, body, reason = _call(http, SEARCH_URL, search_params(query, language=language, region=region),
                                  key, timeout)
    if reason:
        return VideoResult(reason=reason, searched=True)
    ids = search_ids(_json(body))
    if not ids:
        return VideoResult(reason="no_results", searched=True)
    found, reason = lookup_items(ids, key=key, http=http, timeout=timeout)
    if found is None:
        return VideoResult(reason=reason, searched=True)
    rejected: list[tuple[str, str]] = []
    accepted: list[dict[str, Any]] = []
    for video_id in ids:
        item = found.get(video_id)
        if item is None:                         # in the search index, but gone or private now
            rejected.append((video_id, "unavailable"))
            continue
        ok, why = judge_video(item, tokens=tokens, region=region)
        if ok:
            accepted.append(item)
        else:
            rejected.append((video_id, why))
    cards = [made for made in (card(item) for item in rank(accepted, language=language, trusted=trusted))
             if made is not None][:max(0, limit)]
    return VideoResult(cards=cards, rejected=rejected, reason="ok" if cards else "no_results", searched=True)


def recheck(cards: list[dict[str, Any]], *, key: str, region: str = "", http: HttpCall | None = None,
            timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]] | None:
    """Cards whose videos still exist and are still public (one videos.list call, 1 unit).

    None when the check itself failed - the caller keeps what it had rather than showing
    nothing because the network blinked.
    """

    ids = [str(item.get("video_id")) for item in cards if isinstance(item, dict)]
    found, _reason = lookup_items(ids, key=key, http=http, timeout=timeout)
    if found is None:
        return None
    kept: list[dict[str, Any]] = []
    for item in cards:
        video = found.get(str(item.get("video_id")))
        if video is None:
            continue
        # Relevance was decided when the card was made; here only availability can change.
        ok, _why = judge_video(video, tokens=[], region=region, check_relevance=False)
        if ok:
            refreshed = card(video)
            if refreshed is not None:
                kept.append(refreshed)
    return kept


def fetch_thumbnail(video_id: str, *, get: BytesCall | None = None,
                    timeout: float = DEFAULT_TIMEOUT) -> tuple[bytes, str] | None:
    """The medium thumbnail of a validated id, fetched server-side: (jpeg bytes, type) or None."""

    if not VIDEO_ID.match(str(video_id or "")):
        return None
    try:
        status, body, content_type = (get or _http_bytes)(THUMBNAIL_URL.format(video_id=video_id), timeout=timeout)
    except Exception:  # noqa: BLE001
        return None
    content_type = str(content_type or "").split(";")[0].strip().casefold()
    if status != 200 or content_type not in ("image/jpeg", "image/webp") or not body \
            or len(body) > MAX_THUMBNAIL_BYTES:
        return None
    return body, content_type

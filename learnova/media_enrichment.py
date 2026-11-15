"""Turn AI-suggested topics into study media a student can trust.

The model only ever supplies short topics and article titles. Every URL here is either
built deterministically (video search links) or fetched from Wikipedia (real images) —
never invented by the model.

The rule this module is built around: **an image is shown only when the code can point at
the evidence that it is the right one.** Anything it cannot prove is dropped, with a named
reason, and the lesson simply has no picture there. An unillustrated lesson is a small
loss; a portrait of Georg Ohm captioned as an explanation of resistance is a real one.

That failure was not hypothetical. The previous version asked the model for "the exact
title of a real Wikipedia article" and then ran that title through *full-text search*,
taking whatever ranked first and never comparing it to what it asked for. "Ohm's law"
ranks the biography of Georg Ohm, whose lead image is an oil painting. The same mechanism
sent "cell", "root" and "power" wherever the search ranker happened to point.

Everything that decides whether a picture is usable is a pure function of a recorded API
response, so the judgement can be tested without a network (see tests/test_media_enrichment.py).

Licences (docs/LEARNING_RESOURCES.md): a picture that passed the relevance checks is shown
only when its file carries a free licence the code can name (CC0, public domain, CC BY,
CC BY-SA) and, where the licence asks for it, an author. The caption then credits author
and licence with a link to the file page - the attribution CC BY and CC BY-SA require.
"""

from __future__ import annotations

import html
import json
import re
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any


_WIKI_TIMEOUT = 4.0
_USER_AGENT = "LearnovaTutor/1.0 (educational study app; contact via app)"

# Wikipedias for every content language the app supports, so a French lesson is not
# looked up in English - which is what happened to fr/es/it/pt/nl/ar before.
WIKI_LANGUAGES = frozenset({"en", "de", "fr", "es", "it", "pt", "nl", "ar"})

# The only image host the CSP allows (learnova/web/security.py). The previous check
# accepted any *.wikimedia.org, so an image from another Wikimedia host passed the server
# and was then blocked by the browser - a broken picture under a confident caption.
ALLOWED_IMAGE_HOST = "upload.wikimedia.org"

# What a teaching picture can be. A term that does not claim one of these is not shown:
# this is what makes "only when the thing is visual" a rule the app enforces rather than
# an instruction the model may quietly ignore. Abstract topics have no valid kind.
IMAGE_KINDS = frozenset({
    "diagram", "map", "anatomy", "apparatus", "artwork", "artifact", "graph", "structure",
})

# Free licences whose terms are met by a credit line (author, licence, link). Anything
# else - GFDL-only, "fair use" local files, unknown - is not shown. Matched against the
# file's LicenseShortName, which Commons fills from the licence template.
FREE_LICENCE = re.compile(
    r"^(?:cc0(?:\s*1\.0)?|cc[ -]zero|public\s+domain|pd(?:[ -].*)?|no\s+restrictions"
    r"|cc[ -]by(?:[ -]sa)?(?:[ -]\d(?:\.\d)?)?(?:[ -][a-z]{2,3})?)$", re.IGNORECASE)
PUBLIC_DOMAIN = re.compile(r"^(?:cc0|cc[ -]zero|public\s+domain|pd\b|no\s+restrictions)", re.IGNORECASE)
# Where a credit line may link to: the file page and the licence deed, nothing else.
FILE_PAGE_HOSTS = ("commons.wikimedia.org", "wikipedia.org")
LICENCE_URL_HOSTS = ("creativecommons.org",)
MAX_AUTHOR_LENGTH = 120

MIN_THUMBNAIL_WIDTH = 200          # below this it is an icon, not an illustration
MAX_TERM_LENGTH = 120
MAX_ALT_LENGTH = 200

# Categories that mark an article as being about a person. A portrait is never the
# explanation of a law, and the biography is exactly what search used to return for
# "Ohm's law", "Boyle's law" and "Gauss elimination".
PERSON_CATEGORY_MARKERS = (
    "living people", "births", "deaths",                    # en
    "geboren", "gestorben", "mann", "frau",                 # de
    "naissance", "décès", "nacimiento", "fallecidos",       # fr / es
    "nati nel", "morti nel", "nascidos", "mortos",          # it / pt
    "geboren in", "overleden in",                           # nl
)

# Files that are decoration or identity marks, never an explanation.
NON_TEACHING_FILENAME = re.compile(
    r"(flag|flagge|bandera|drapeau|coat[_ ]of[_ ]arms|wappen|escudo|blason"
    r"|logo|seal|emblem|icon|symbol)", re.IGNORECASE)


@dataclass(frozen=True)
class ImageRequest:
    """One picture the model asked for, after the fields have been cleaned."""

    term: str
    kind: str
    alt: str = ""


@dataclass(frozen=True)
class ImageDecision:
    """Whether a fetched article may illustrate a lesson, and why."""

    term: str
    accepted: bool
    reason: str
    image: dict[str, str] | None = None


@dataclass(frozen=True)
class MediaResult:
    """Images that passed, and every term that did not, with the reason it failed."""

    images: list[dict[str, str]] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Videos
# --------------------------------------------------------------------------- #

def youtube_search_url(query: str) -> str:
    return "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)


def studyflix_search_url(query: str) -> str:
    return "https://studyflix.de/suche?q=" + urllib.parse.quote_plus(query)


def search_phrase(topic: str, subject: str = "", grade: str = "") -> str:
    """Build the phrase a student would actually type, not just the bare topic.

    A search for "Photosynthese" returns everything ever made about it; "Photosynthese
    Biologie Klasse 8" returns lessons pitched at the person watching. The context is
    already known at the call site and was simply not being used.
    """

    parts = [str(topic or "").strip()]
    for extra in (subject, grade):
        text = str(extra or "").strip()
        if text and text.casefold() not in parts[0].casefold():
            parts.append(text)
    return " ".join(part for part in parts if part)[:160]


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
        query, why = query[:MAX_TERM_LENGTH], why[:200]
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            result.append((query, why))
        if len(result) >= limit:
            break
    return result


def video_links(items: Any, limit: int = 3, *, subject: str = "", grade: str = "",
                language: str = "en") -> list[dict[str, str]]:
    """Build guaranteed-valid search links from AI video topics.

    Search links rather than picked videos: a search cannot be factually wrong the way a
    chosen video can, and it needs no API key or quota. Studyflix is German-only, so it is
    offered only for German content instead of to every language as it was before.
    """

    # wiki_language handles both a code ("de") and a name ("German"); a bare
    # startswith("de") check quietly fails on the latter, which is what the call site
    # now passes.
    german = wiki_language(language) == "de"
    links: list[dict[str, str]] = []
    for query, why in _video_entries(items, limit):
        phrase = search_phrase(query, subject, grade)
        # A search, not a video: "kind" and "query" let the page say "Search YouTube for ..."
        # instead of showing the phrase as if it were a video title. "title" stays for
        # lessons stored before this and for the API video lookup (learnova/youtube_videos.py).
        entry = {"kind": "search", "query": query, "title": query, "why": why,
                 "youtube": youtube_search_url(phrase)}
        if german:
            entry["studyflix"] = studyflix_search_url(phrase)
        links.append(entry)
    return links


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #

def wiki_language(content_language: str) -> str:
    """The Wikipedia to search, from a content language name or code."""

    text = str(content_language or "").strip().casefold()
    names = {"english": "en", "german": "de", "french": "fr", "spanish": "es",
             "italian": "it", "portuguese": "pt", "dutch": "nl", "arabic": "ar"}
    if text in names:
        return names[text]
    code = text[:2]
    return code if code in WIKI_LANGUAGES else "en"


def _comparable(title: str) -> str:
    """Fold a title for comparison: case, accents and punctuation do not matter."""

    folded = unicodedata.normalize("NFKD", str(title or ""))
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", " ", folded.casefold()).strip()


def titles_match(requested: str, resolved: str) -> bool:
    """Whether the article we got back is the one we asked for.

    A redirect to a *differently named* article is where the wrong picture comes from, so
    it is refused. Wikipedia's own parenthetical qualifier is allowed, because
    "Cell (biology)" really is the article for "Cell (biology)".
    """

    wanted, got = _comparable(requested), _comparable(resolved)
    if not wanted or not got:
        return False
    return got == wanted or got.startswith(f"{wanted} ")


def normalize_image_requests(raw: Any, limit: int) -> tuple[list[ImageRequest], list[tuple[str, str]]]:
    """Clean what the model returned, dropping anything that is not a visual kind.

    Returns (requests, rejected) so the caller can log why a term never got as far as a
    lookup. A bare string carries no kind and is refused: under the old schema every term
    was a string, and accepting them would reopen the decoration problem.
    """

    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)) or limit < 1:
        return [], []

    requests: list[ImageRequest] = []
    rejected: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, dict):
            term = str(item.get("term") or item.get("title") or "").strip()[:MAX_TERM_LENGTH]
            kind = str(item.get("kind") or item.get("image_kind") or "").strip().casefold()
            alt = str(item.get("alt") or item.get("description") or "").strip()[:MAX_ALT_LENGTH]
        else:
            term, kind, alt = str(item).strip()[:MAX_TERM_LENGTH], "", ""
        if not term:
            continue
        key = _comparable(term)
        if not key or key in seen:
            continue
        seen.add(key)
        if kind not in IMAGE_KINDS:
            rejected.append((term, "kind_not_visual"))
            continue
        requests.append(ImageRequest(term=term, kind=kind, alt=alt))
        if len(requests) >= limit:
            break
    return requests, rejected


def judge_page(request: ImageRequest, page: Any, wiki: str = "en") -> ImageDecision:
    """Decide whether one Wikipedia page may illustrate a lesson. Pure, no network.

    Every rejection names its reason so the call site can log it; before this, a dropped
    image and a network failure were indistinguishable and neither was recorded.
    """

    def no(reason: str) -> ImageDecision:
        return ImageDecision(term=request.term, accepted=False, reason=reason)

    if not isinstance(page, dict) or page.get("missing") is not None and page.get("missing") is not False:
        return no("no_such_article")
    if not page.get("title"):
        return no("no_such_article")
    if (page.get("pageprops") or {}).get("disambiguation") is not None:
        return no("disambiguation")

    categories = " | ".join(
        str((item or {}).get("title", "")).casefold()
        for item in (page.get("categories") or []) if isinstance(item, dict))
    if any(marker in categories for marker in PERSON_CATEGORY_MARKERS):
        return no("about_a_person")

    if not titles_match(request.term, str(page.get("title"))):
        return no("title_mismatch")

    thumbnail = page.get("thumbnail") or {}
    source = thumbnail.get("source")
    if not isinstance(source, str) or not source.startswith("https://"):
        return no("no_image")
    try:
        host = urllib.parse.urlsplit(source).hostname or ""
    except ValueError:
        return no("no_image")
    if host != ALLOWED_IMAGE_HOST:
        return no("host_not_allowed")
    try:
        width = int(thumbnail.get("width") or 0)
    except (TypeError, ValueError):
        width = 0
    if width < MIN_THUMBNAIL_WIDTH:
        return no("image_too_small")
    if NON_TEACHING_FILENAME.search(urllib.parse.unquote(source.rsplit("/", 1)[-1])):
        return no("not_a_teaching_image")

    title = str(page.get("title"))
    file_name = str(page.get("pageimage") or "").strip()
    return ImageDecision(term=request.term, accepted=True, reason="ok", image={
        "url": source,
        "title": title,
        # The alt text describes the picture; the caption names where it came from. The
        # old code used the article title for both and claimed a licence it never checked.
        "alt": request.alt or title,
        "kind": request.kind,
        "page_url": str(page.get("fullurl") or f"https://{wiki}.wikipedia.org/wiki/{urllib.parse.quote(title)}"),
        "source": f"Wikipedia · {title}",
        **({"file_name": file_name[:240]} if file_name else {}),
    })


# --------------------------------------------------------------------------- #
# Licences
# --------------------------------------------------------------------------- #

def _meta_value(metadata: Any, name: str) -> str:
    entry = metadata.get(name) if isinstance(metadata, dict) else None
    value = entry.get("value") if isinstance(entry, dict) else entry
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def plain_author(markup: str) -> str:
    """The Artist field is HTML (links, spans); the credit line needs the words only."""

    text = re.sub(r"<[^>]*>", " ", str(markup or ""))
    text = " ".join(html.unescape(text).split())
    return text[:MAX_AUTHOR_LENGTH].rstrip()


def _https_url(value: str, hosts: tuple[str, ...]) -> str:
    """`value` when it is an https URL on one of `hosts` (or a subdomain of one), else ""."""

    url = str(value or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return ""
    host = (parts.hostname or "").casefold()
    if parts.scheme != "https" or not any(host == item or host.endswith("." + item) for item in hosts):
        return ""
    return url


def judge_licence(info: Any) -> tuple[bool, str, dict[str, str]]:
    """Whether a file may be shown, and the credit it must carry. Pure, no network.

    `info` is one `imageinfo` entry (prop=imageinfo&iiprop=url|extmetadata). Returns
    (ok, reason, credit) with credit = {author, licence, licence_url, file_url}.
    """

    if not isinstance(info, dict):
        return False, "licence_unknown", {}
    raw_metadata = info.get("extmetadata")
    metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
    licence = " ".join(_meta_value(metadata, "LicenseShortName").split())[:60]
    if not licence:
        return False, "licence_unknown", {}
    if _meta_value(metadata, "NonFree").casefold() in {"true", "1", "yes"}:
        return False, "licence_not_free", {}
    if not FREE_LICENCE.match(licence):
        return False, "licence_not_free", {}
    file_url = _https_url(str(info.get("descriptionurl") or ""), FILE_PAGE_HOSTS)
    if not file_url:
        return False, "no_file_page", {}
    author = plain_author(_meta_value(metadata, "Artist"))
    required = _meta_value(metadata, "AttributionRequired").casefold()
    public_domain = bool(PUBLIC_DOMAIN.match(licence))
    if not author and not public_domain and required != "false":
        # CC BY and CC BY-SA require the author's name; without it the terms cannot be met.
        return False, "author_unknown", {}
    licence_url = "" if public_domain else _https_url(_meta_value(metadata, "LicenseUrl"), LICENCE_URL_HOSTS)
    return True, "ok", {"author": author, "licence": licence, "licence_url": licence_url,
                        "file_url": file_url}


def _api_url(term: str, wiki: str) -> str:
    """Exact-title lookup. Deliberately not a search: search is what went wrong."""

    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": "2",
        "titles": term, "redirects": "1",
        "prop": "pageimages|info|pageprops|categories",
        # The file name is needed for the licence lookup; pilicense=free pins what
        # PageImages already defaults to, so a non-free local file is never even offered.
        "piprop": "thumbnail|name", "pithumbsize": "800", "pilicense": "free",
        "inprop": "url", "ppprop": "disambiguation",
        "cllimit": "60", "clshow": "!hidden",
    })
    return f"https://{wiki}.wikipedia.org/w/api.php?{params}"


@lru_cache(maxsize=512)
def _fetch_page(term: str, wiki: str) -> str:
    """The page lookup (the licence lookup is the other network call). Cached: a term is looked up once."""

    request = urllib.request.Request(_api_url(term, wiki), headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_WIKI_TIMEOUT) as response:
        return response.read().decode("utf-8")


def _file_info_url(file_name: str, wiki: str) -> str:
    """The licence, author and file page of one file (Commons files resolve through any wiki)."""

    params = urllib.parse.urlencode({
        "action": "query", "format": "json", "formatversion": "2",
        "titles": f"File:{file_name}", "prop": "imageinfo", "iiprop": "url|extmetadata",
        "iiextmetadatafilter": "LicenseShortName|LicenseUrl|Artist|AttributionRequired|NonFree",
        "iiextmetadatalanguage": wiki,
    })
    return f"https://{wiki}.wikipedia.org/w/api.php?{params}"


@lru_cache(maxsize=512)
def _fetch_file_info(file_name: str, wiki: str) -> str:
    """The second network call: the file's licence. Cached like the page lookup."""

    request = urllib.request.Request(_file_info_url(file_name, wiki), headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=_WIKI_TIMEOUT) as response:
        return response.read().decode("utf-8")


def first_imageinfo(payload: Any) -> dict[str, Any] | None:
    """The one imageinfo entry of a File: lookup."""

    page = first_page(payload)
    infos = page.get("imageinfo") if isinstance(page, dict) else None
    return infos[0] if isinstance(infos, list) and infos and isinstance(infos[0], dict) else None


def first_page(payload: Any) -> dict[str, Any] | None:
    """The single page a titles= lookup returns, under formatversion=2."""

    pages = (payload or {}).get("query", {}).get("pages") if isinstance(payload, dict) else None
    if isinstance(pages, list) and pages:
        return pages[0] if isinstance(pages[0], dict) else None
    if isinstance(pages, dict) and pages:                      # formatversion=1 fallback
        first = next(iter(pages.values()))
        return first if isinstance(first, dict) else None
    return None


def lookup_image(request: ImageRequest, wiki: str) -> ImageDecision:
    """Fetch one article and judge it. Never raises."""

    try:
        payload = json.loads(_fetch_page(request.term, wiki))
    except Exception:
        # A lookup that could not happen is not the same as an article that failed the
        # checks, and the call site logs the difference.
        return ImageDecision(term=request.term, accepted=False, reason="lookup_failed")
    decision = judge_page(request, first_page(payload), wiki)
    if not decision.accepted or not decision.image:
        return decision
    # Relevant is not enough: the file must carry a licence whose terms a caption can meet.
    image = dict(decision.image)
    file_name = image.pop("file_name", "")
    if not file_name:
        return ImageDecision(term=request.term, accepted=False, reason="licence_unknown")
    try:
        info = first_imageinfo(json.loads(_fetch_file_info(file_name, wiki)))
    except Exception:
        return ImageDecision(term=request.term, accepted=False, reason="licence_lookup_failed")
    ok, reason, credit = judge_licence(info)
    if not ok:
        return ImageDecision(term=request.term, accepted=False, reason=reason)
    image.update(credit)
    image["source"] = "Wikimedia Commons" if "commons.wikimedia.org" in credit["file_url"] else "Wikipedia"
    return ImageDecision(term=request.term, accepted=True, reason="ok", image=image)


def lesson_images(terms: Any, language: str, limit: int = 2) -> MediaResult:
    """Resolve AI image requests to verified Wikipedia images.

    Returns the images that proved themselves and every term that did not, so the caller
    can log how often this drops one. Silence was the previous behaviour and it is why
    nobody could tell how bad the problem was.
    """

    requests, rejected = normalize_image_requests(terms, limit)
    images: list[dict[str, str]] = []
    seen: set[str] = set()
    wiki = wiki_language(language)
    # At most two pictures, each two lookups with a 4 s timeout: in parallel, the request
    # path waits for the slowest picture, not for all of them in a row.
    if len(requests) > 1:
        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(pool.map(lambda item: lookup_image(item, wiki), requests))
    else:
        decisions = [lookup_image(item, wiki) for item in requests]
    for request, decision in zip(requests, decisions):
        if not decision.accepted or not decision.image:
            rejected.append((request.term, decision.reason))
            continue
        if decision.image["url"] in seen:
            rejected.append((request.term, "duplicate_image"))
            continue
        seen.add(decision.image["url"])
        images.append(decision.image)
    return MediaResult(images=images, rejected=rejected)

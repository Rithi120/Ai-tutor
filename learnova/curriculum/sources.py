"""Which curriculum sources may be processed, and fetching them politely.

Nothing is fetched or imported from a host until the operator has checked that host's
usage terms and listed it in CURRICULUM_CLEARED_SOURCES. The registry below records
what was known about the official hosts when this module was written, so the CLI can
say *why* a source is refused instead of failing silently. Clearing a host is a human
decision; no code path flips it.

Even for a cleared host, robots.txt is obeyed. bildungsplaene-bw.de disallows every
`*.pdf` path for all agents, so its PDFs are imported from a file the operator
downloaded in a browser (`flask curriculum import`), never crawled.
"""

from __future__ import annotations

import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlsplit

USER_AGENT = "LearnovaCurriculumImporter/1.0 (+https://studylearnova.com/about)"
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
REQUEST_TIMEOUT = 30


@dataclass(frozen=True)
class KnownSource:
    host: str
    status: str  # "terms_unconfirmed" | "offline"
    note: str


# Checked 2026-10-18. Update the note when the facts change; never change behaviour here.
KNOWN_SOURCES = {
    "bildungsplaene-bw.de": KnownSource(
        "bildungsplaene-bw.de", "terms_unconfirmed",
        "Official Bildungsplan portal (ZSL Baden-Württemberg). robots.txt disallows *.pdf for "
        "every agent (Crawl-Delay 2); the Impressum returned HTTP 500, so the usage terms could "
        "not be confirmed. Confirm them (or ask the ZSL) before clearing this host.",
    ),
    "schule-bw.de": KnownSource(
        "schule-bw.de", "offline",
        "The Landesbildungsserver was shut down (notice dated 2026-10-12, redirects to "
        "lehrerfortbildung-bw.de). The Beispielcurricula links no longer resolve.",
    ),
    "km.baden-wuerttemberg.de": KnownSource(
        "km.baden-wuerttemberg.de", "terms_unconfirmed",
        "Kultusministerium Baden-Württemberg. Usage terms not reviewed.",
    ),
}


class SourceNotCleared(Exception):
    """The source's usage terms have not been confirmed by the operator."""


class RobotsDisallowed(Exception):
    """robots.txt forbids fetching this URL."""


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host[4:] if host.startswith("www.") else host


def parse_cleared(value: str | None) -> frozenset[str]:
    hosts = (part.strip().casefold() for part in str(value or "").split(","))
    return frozenset(host[4:] if host.startswith("www.") else host for host in hosts if host)


def is_web_url(value: str) -> bool:
    return urlsplit(value or "").scheme in {"http", "https"}


def check_provenance(source_url: str, cleared: frozenset[str], *, rights_note: str = "") -> None:
    """Refuse a source whose terms nobody confirmed.

    A web source must be on a cleared host. Anything else (a teacher's own notes, a
    school's internal plan) is accepted only with a rights note saying who may use it."""

    source_url = (source_url or "").strip()
    if not source_url:
        raise SourceNotCleared("Every curriculum document needs a source reference (URL or description).")
    if not is_web_url(source_url):
        if not rights_note.strip():
            raise SourceNotCleared(
                "A non-web source needs --rights-note stating who owns it and that it may be processed.")
        return
    host = host_of(source_url)
    if host in cleared or any(host.endswith("." + item) for item in cleared):
        return
    known = KNOWN_SOURCES.get(host)
    detail = f" {known.note}" if known else ""
    raise SourceNotCleared(
        f"{host} is not in CURRICULUM_CLEARED_SOURCES.{detail} Add the host only after checking "
        "that its usage terms permit storing and processing the text.")


def robots_allows(url: str, *, session, user_agent: str = USER_AGENT) -> tuple[bool, float]:
    """(allowed, crawl delay in seconds). An unreachable robots.txt is treated as a refusal
    for safety, except a 404, which by convention means "no restrictions"."""

    parts = urlsplit(url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        response = session.get(robots_url, headers={"User-Agent": user_agent}, timeout=REQUEST_TIMEOUT)
    except Exception:
        return False, 0.0
    if response.status_code == 404:
        return True, 0.0
    if response.status_code >= 400:
        return False, 0.0
    parser = urllib.robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    delay = parser.crawl_delay(user_agent) or 0
    return parser.can_fetch(user_agent, url), float(delay)


@dataclass(frozen=True)
class Download:
    url: str
    content: bytes
    content_type: str


def fetch(url: str, *, cleared: frozenset[str], session=None, sleep=time.sleep) -> Download:
    """Download one cleared, robots-permitted document."""

    if session is None:
        import requests
        session = requests.Session()
    check_provenance(url, cleared)
    allowed, delay = robots_allows(url, session=session)
    if not allowed:
        raise RobotsDisallowed(
            f"robots.txt of {host_of(url)} does not allow fetching {url}. Download it in a browser "
            "if the terms allow, and use `flask curriculum import` with --source-url.")
    if delay:
        sleep(min(delay, 30.0))
    response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT, stream=True)
    response.raise_for_status()
    chunks, size = [], 0
    for chunk in response.iter_content(64 * 1024):
        size += len(chunk)
        if size > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"{url} is larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return Download(url, b"".join(chunks), str(response.headers.get("Content-Type") or ""))

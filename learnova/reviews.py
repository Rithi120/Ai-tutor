"""Public reviews of Learnova (About page): rules, text checks, ordering and the summary.

Flask-free and deterministic. Nothing here ever invents a review or a rating: the summary
is computed only from rows the database hands in, and an empty list is reported as
"no reviews yet", never as 0.0 stars.

Workflow (decided after the community library's moderation, docs/COMMUNITY_MODERATION.md:
quality never blocks, reports trigger a human look, they never auto-hide):

* a review is published at once, unless `hold_reason` finds links or contact details or
  obvious spam patterns - then it waits as "pending" for a moderator;
* a negative review is never held or hidden for being negative;
* reports are stored and alert a moderator; only a moderator hides, rejects or restores.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Mapping

MIN_LEVEL = 3
MAX_TEXT = 1000
PAGE_SIZE = 6
STATUSES = ("pending", "published", "rejected", "hidden")
REPORT_REASONS = ("spam", "harassment", "offensive", "personal_info", "off_topic", "other")
REPORT_REASON_LABELS = {
    "spam": "Spam or advertising", "harassment": "Harassment or hate", "offensive": "Offensive language",
    "personal_info": "Personal information", "off_topic": "Not about Learnova", "other": "Something else",
}
MODERATION_ACTIONS = {"publish": "published", "restore": "published", "hide": "hidden", "reject": "rejected"}
SORTS = ("recommended", "newest", "oldest", "highest", "lowest")
SORT_LABELS = {"recommended": "Recommended", "newest": "Newest first", "oldest": "Oldest first",
               "highest": "Highest rated", "lowest": "Lowest rated"}

_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f​-‏‪-‮⁦-⁩]")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w-]+\.(?:com|de|net|org|io|app|ru|xyz|info|biz|shop)\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?:\+|00)\d[\d\s/().-]{7,}\d")
_REPEAT = re.compile(r"(.)\1{9,}")


def clean_text(raw: object) -> str:
    """Trimmed, control and direction-override characters removed, at most two blank
    lines in a row. HTML is kept as typed: the page escapes it on output."""

    text = unicodedata.normalize("NFC", str(raw or "")).replace("\r\n", "\n").replace("\r", "\n")
    text = _CONTROL.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def parse_rating(raw: object) -> int | None:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if 1 <= value <= 5 else None


def text_problem(text: str) -> str | None:
    if len(text) > MAX_TEXT:
        return "too_long"
    return None


def hold_reason(text: str) -> str:
    """Why a review should wait for a moderator before it is public ("" = publish now).

    Only things that are never part of honest feedback: links, email addresses, phone
    numbers, a character repeated ten times, or shouting a long text in capitals. A low
    rating or harsh criticism is never a reason."""

    if not text:
        return ""
    if _URL.search(text):
        return "link"
    if _EMAIL.search(text) or _PHONE.search(text):
        return "contact_details"
    if _REPEAT.search(text):
        return "repetition"
    letters = [char for char in text if char.isalpha()]
    if len(letters) >= 40 and sum(char.isupper() for char in letters) / len(letters) > 0.8:
        return "shouting"
    return ""


def normalize_sort(value: object) -> str:
    value = str(value or "").strip().lower()
    return value if value in SORTS else "recommended"


def normalize_filter(value: object) -> int | None:
    return parse_rating(value) if str(value or "").strip() not in ("", "all") else None


@dataclass(frozen=True)
class Summary:
    total: int
    average: float | None              # None when there is nothing to average
    counts: Mapping[int, int]          # stars -> published reviews
    percentages: Mapping[int, int]     # stars -> share in whole percent (rounded)

    @property
    def empty(self) -> bool:
        return self.total == 0

    @property
    def rounded_average(self) -> str:
        return "" if self.average is None else f"{self.average:.1f}"

    @property
    def full_stars(self) -> int:
        return 0 if self.average is None else int(self.average + 0.25)


def summarize(rows: Iterable[tuple[int, int]]) -> Summary:
    """`rows`: (stars, count) of PUBLISHED reviews only, as the GROUP BY returns them."""

    counts = {stars: 0 for stars in range(1, 6)}
    for stars, count in rows:
        if 1 <= int(stars) <= 5:
            counts[int(stars)] += int(count)
    total = sum(counts.values())
    average = round(sum(stars * count for stars, count in counts.items()) / total, 2) if total else None
    percentages = {stars: round(100 * count / total) if total else 0 for stars, count in counts.items()}
    return Summary(total=total, average=average, counts=counts, percentages=percentages)


__all__ = ["MAX_TEXT", "MIN_LEVEL", "MODERATION_ACTIONS", "PAGE_SIZE", "REPORT_REASONS", "REPORT_REASON_LABELS",
           "SORTS", "SORT_LABELS", "STATUSES", "Summary", "clean_text", "hold_reason", "normalize_filter",
           "normalize_sort", "parse_rating", "summarize", "text_problem"]

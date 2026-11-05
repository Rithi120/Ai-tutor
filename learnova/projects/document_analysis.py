"""Material understanding for Project Mode: what was read, analysed and covered.

A student who uploads six papers has to be told about all six. Before this module the
only status lived on single pages, nothing was rolled up per file, and the flow went on
as soon as *any* page had text, so five failed papers could vanish behind one that was
read. Everything here is pure and deterministic (no database, no AI): the app turns its
rows into `PageFacts`/`FileFacts` and gets plain dicts back for the start page, the
workspace and the topic check.

* `page_state` is the one reading state of a page.
* `file_status` rolls the pages of one uploaded document up into a file state, counts
  and the page numbers a student has to look at.
* `material_report` covers the whole project: counts, issues, and whether the material
  is completely understood (every page read, analysed and covered by a topic).
* `find_duplicate_pages` / `find_overlaps` spot the same sheet uploaded twice (a photo
  and a scan of one handout) and a revision sheet that repeats part of a handout.

Page numbers in the output are numbers inside the file (`page_number`, the PDF page),
because that is what a student finds on paper; page ids are in each file's `pages` list
for retry and fix actions.

The bookkeeping comes from stored JSON (`analysis_json`), so ids are read leniently: a
JSON object turns the int keys of `find_duplicate_pages` into strings ("12"), and
`duplicate_links` / the id parsing here repair that instead of reporting a false gap.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

PAGE_STATES = ("pending", "waiting", "recognizing", "ready", "unreadable", "failed")   # extraction_status
# "blank": the page renders as empty paper (the back of a sheet) and was not sent to the
# AI. It is read - there is nothing on it - and holds no learning content.
TEXT_SOURCES = ("text_layer", "vision", "local_ocr", "manual", "blank", "")
ANALYSIS_STATES = ("analysed", "failed", "not_analysed", "duplicate", "no_content")
FILE_STATES = ("analysed", "partly_analysed", "needs_ocr_review", "unreadable", "processing",
               "not_analysed", "excluded")
READING_STATES = ("read", "needs_review", "unreadable", "processing", "excluded", "duplicate")
ISSUE_CODES = ("processing", "unreadable_pages", "needs_review", "analysis_failed", "not_analysed",
               "uncovered_pages", "duplicate_file_skipped", "topics_excluded")

# The same band as learnova.ocr.service.CONFIDENCE_REVIEW. That module needs PIL and
# PyMuPDF; this one has to stay importable anywhere, so the literal is repeated.
REVIEW_CONFIDENCE = 0.45
# Strict on purpose: a duplicate page is left out of topic extraction, so a false match
# loses content while a missed one only costs a little extra analysis. 0.9 on 5-grams
# allows about one differing word per hundred; messier copies land in find_overlaps.
DUPLICATE_THRESHOLD = 0.9
OVERLAP_LOW = 0.5
# Below this many words two pages share too few phrases for a fair comparison: two short
# worksheets that both say "Name: ... Datum: ..." are not the same sheet.
MIN_COMPARE_WORDS = 40
SHINGLE_WORDS = 5

_PROCESSING = frozenset({"pending", "waiting", "recognizing"})
_FAILED_READING = frozenset({"unreadable", "failed"})
_READABLE = frozenset({"read", "needs_review", "duplicate"})
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_HYPHEN_BREAK = re.compile(r"(?<=[^\W\d_])-[ \t]*\r?\n[ \t]*(?=[^\W\d_])")
_COUNT_KEYS = ("pages_total", "pages_read", "pages_review", "pages_unreadable", "pages_processing",
               "pages_duplicate", "pages_excluded", "pages_readable", "pages_uncovered",
               "pages_not_analysed", "pages_analysis_failed", "pages_no_topic")


@dataclass(frozen=True)
class PageFacts:
    """What the app knows about one page (a ProjectPage row plus the analysis bookkeeping).

    `text_chars` and `words` must be filled from the page text: a ready page with neither
    counts as unreadable, because nothing from it can reach a topic.
    """

    page_id: int
    file_id: int
    page_number: int           # position inside its file (PDF page); 1 for a photo
    page_order: int            # project-wide order
    extraction_status: str     # one of PAGE_STATES
    confidence_status: str = "unclear"
    confidence: float | None = None
    text_source: str = ""      # one of TEXT_SOURCES
    excluded: bool = False
    text_chars: int = 0
    words: int = 0             # content_words(text)
    analysis: str = "not_analysed"   # one of ANALYSIS_STATES
    covered: bool = False      # at least one topic cites this page
    duplicate_of: int | None = None
    # The analysis kept explicit page lists (analysis_json v4): `analysis` is what the run
    # recorded and a citation never upgrades it. A topic the student typed or added on a
    # page the run never reached is no evidence that the page was analysed.
    analysis_listed: bool = False
    # A page of a PDF that PyMuPDF had to repair (a cut-off download): its text layer may
    # have lost the end of the page, so the reading needs the student's check (review A F1).
    pdf_repaired: bool = False


@dataclass(frozen=True)
class FileFacts:
    file_id: int
    filename: str
    source_kind: str
    pages: tuple[PageFacts, ...]


@dataclass(frozen=True)
class _Resolved:
    """A page's effective state once a duplicate borrows its original's analysis."""

    state: str
    analysis: str
    covered: bool
    no_topic: bool


# --------------------------------------------------------------------------- values

def _page_id(value: Any) -> int | None:
    """A page id from stored JSON: 12 and "12" are page 12; True, "", "x" are no id."""

    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _confidence(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ids(values: Any) -> set[int]:
    """Page ids from stored JSON, leniently: "12" counts, junk is skipped.

    A lone id (12 or "12") is one id; iterating the string would read it as pages 1 and 2.
    """

    if values is None:
        return set()
    if isinstance(values, (str, bytes, int, float)):
        values = (values,)
    try:
        items = list(values)
    except TypeError:
        return set()
    return {page_id for page_id in map(_page_id, items) if page_id is not None}


def _names(values: Any) -> list[str]:
    """File names from stored JSON, in order, without blanks or repeats."""

    if values is None:
        return []
    if isinstance(values, str):
        values = (values,)
    try:
        items = list(values)
    except TypeError:
        return []
    names: list[str] = []
    for value in items:
        if isinstance(value, Mapping):
            value = value.get("filename") or value.get("name") or ""
        name = str(value if value is not None else "").strip()
        if name and name not in names:
            names.append(name)
    return names


def duplicate_links(raw: Any) -> dict[int, int]:
    """`analysis_json["duplicates"]` back as {duplicate page id: original page id}.

    `find_duplicate_pages` returns int keys, JSON stores them as strings, so a lookup with
    the int page id misses and the copy looks unanalysed. Self-links and junk are dropped.
    """

    out: dict[int, int] = {}
    if not isinstance(raw, Mapping):
        return out
    for key, value in raw.items():
        duplicate, original = _page_id(key), _page_id(value)
        if duplicate is not None and original is not None and duplicate != original:
            out[duplicate] = original
    return out


# --------------------------------------------------------------------------- pages

def _analysis(page: PageFacts) -> str:
    if _is_blank(page) and str(page.extraction_status or "").strip().lower() == "ready":
        return "no_content"         # nothing on it: never "not analysed", never uncovered
    value = str(page.analysis or "").strip().lower()
    if value not in ANALYSIS_STATES:
        value = "not_analysed"
    if value in ("not_analysed", "failed") and page.covered and not page.analysis_listed:
        # A topic cites the page, so its text reached the topic extraction. Projects
        # analysed before the page lists were kept have only these citations; without
        # this every existing project would report every page as never analysed.
        return "analysed"
    return value


def _original_id(page: PageFacts) -> int | None:
    original = _page_id(page.duplicate_of)
    return None if original is None or original == _page_id(page.page_id) else original


def _is_duplicate(page: PageFacts) -> bool:
    # A "duplicate" mark without an original is bad bookkeeping, not proof that the
    # content reached a topic through another page; such a page stands alone.
    return _original_id(page) is not None


def _is_blank(page: PageFacts) -> bool:
    return str(page.text_source or "").strip().lower() == "blank"


def _needs_review(page: PageFacts) -> bool:
    source = str(page.text_source or "").strip().lower()
    if source == "manual":
        return False        # the student typed it, so it is what the page says
    if source == "local_ocr":
        return True         # the offline fallback misreads handwriting, so a person checks it
    if page.pdf_repaired:
        return True         # a repaired PDF may have lost the end of a page
    if str(page.confidence_status or "").strip().lower() == "unclear":
        return True
    confidence = _confidence(page.confidence)
    return confidence is not None and confidence < REVIEW_CONFIDENCE


def _reading_state(page: PageFacts) -> str:
    """The reading state, ignoring duplication."""

    if page.excluded:
        return "excluded"
    status = str(page.extraction_status or "").strip().lower()
    if status in _FAILED_READING:
        return "unreadable"
    if status != "ready":
        # pending / waiting / recognizing, and any status this module does not know: not
        # read yet is the only claim that cannot be wrong.
        return "processing"
    if _is_blank(page):
        return "read"
    if _count(page.text_chars) <= 0 and _count(page.words) <= 0:
        return "unreadable"
    return "needs_review" if _needs_review(page) else "read"


def page_state(page: PageFacts) -> str:
    """One of READING_STATES: read | needs_review | unreadable | processing | excluded | duplicate."""

    state = _reading_state(page)
    if state in ("read", "needs_review") and _is_duplicate(page):
        return "duplicate"
    return state


def _alone(page: PageFacts, state: str, no_topic: set[int]) -> _Resolved:
    # A page that was skipped as a duplicate but has no usable original any more still
    # needs its own analysis (unless a topic cites it anyway).
    analysis = _analysis(page)
    if analysis == "duplicate":
        analysis = "analysed" if page.covered else "not_analysed"
    return _Resolved(state, analysis, bool(page.covered), _page_id(page.page_id) in no_topic)


def _resolve(page: PageFacts, pages_by_id: Mapping[int, PageFacts], no_topic: set[int], *,
             strict: bool, seen: frozenset[int] = frozenset()) -> _Resolved:
    """Duplicate pages count as read and covered when their original is.

    `strict` means `pages_by_id` holds every page of the project, so an original that is
    missing was deleted and the duplicate stands on its own. Without it (one file looked
    at alone) the original may sit in another file, and the caller's match is trusted.
    """

    state = _reading_state(page)
    original_id = _original_id(page)
    if state not in ("read", "needs_review") or original_id is None:
        return _alone(page, state, no_topic)
    original = pages_by_id.get(original_id)
    page_id = _page_id(page.page_id)
    if original is None:
        if strict:
            return _alone(page, state, no_topic)
        return _Resolved("duplicate", "analysed", True, page_id in no_topic)
    if original_id in seen:
        return _alone(page, state, no_topic)       # a cycle in bad bookkeeping
    root = _resolve(original, pages_by_id, no_topic, strict=strict,
                    seen=seen | ({page_id} if page_id is not None else set()))
    if root.state not in _READABLE:
        # The original was excluded, replaced or failed: its content no longer reaches
        # the topics through it, so this copy has to be analysed itself.
        return _alone(page, state, no_topic)
    return _Resolved("duplicate", root.analysis, bool(page.covered) or root.covered,
                     page_id in no_topic or root.no_topic)


# --------------------------------------------------------------------------- files

def _by_id(pages: Iterable[PageFacts]) -> dict[int, PageFacts]:
    out: dict[int, PageFacts] = {}
    for page in pages:
        page_id = _page_id(page.page_id)
        if page_id is not None:
            out.setdefault(page_id, page)
    return out


def _summary(file: FileFacts, pages_by_id: Mapping[int, PageFacts] | None,
             no_topic: set[int]) -> dict[str, Any]:
    strict = pages_by_id is not None
    lookup: Mapping[int, PageFacts] = (
        _by_id(pages_by_id.values()) if pages_by_id is not None else _by_id(file.pages))
    pages = sorted(file.pages or (), key=lambda item: (
        _count(item.page_order), _count(item.page_number), _page_id(item.page_id) or 0))
    resolved = [(page, _resolve(page, lookup, no_topic, strict=strict)) for page in pages]
    counts = Counter(item.state for _, item in resolved)
    readable = [(page, item) for page, item in resolved if item.state in _READABLE]
    failed = [page for page, item in readable if item.analysis == "failed"]
    not_analysed = [page for page, item in readable if item.analysis == "not_analysed"]
    open_pages = [(page, item) for page, item in readable
                  if item.analysis == "analysed" and not item.covered]
    no_topic_pages = [page for page, item in open_pages if item.no_topic]
    uncovered = [page for page, item in open_pages if not item.no_topic]

    def numbers(selected: Iterable[PageFacts]) -> list[int]:
        return sorted(_count(page.page_number) for page in selected)

    def in_state(state: str) -> list[PageFacts]:
        return [page for page, item in resolved if item.state == state]

    considered = len(pages) - counts["excluded"]
    if not pages:
        state = "unreadable"            # a file that produced no page gave the student nothing
    elif not considered:
        state = "excluded"
    elif counts["processing"]:
        state = "processing"
    elif counts["unreadable"] == considered:
        state = "unreadable"
    elif readable and len(failed) + len(not_analysed) == len(readable):
        state = "not_analysed"
    elif counts["unreadable"] or failed or not_analysed:
        state = "partly_analysed"
    elif counts["needs_review"]:
        state = "needs_ocr_review"
    else:
        state = "analysed"

    return {
        "file_id": file.file_id,
        "filename": file.filename,
        "source_kind": file.source_kind,
        "state": state,
        "pages_total": len(pages),
        "pages_read": counts["read"],
        "pages_review": counts["needs_review"],
        "pages_unreadable": counts["unreadable"],
        "pages_processing": counts["processing"],
        "pages_duplicate": counts["duplicate"],
        "pages_excluded": counts["excluded"],
        "pages_readable": len(readable),
        "pages_uncovered": len(uncovered),
        "pages_not_analysed": len(not_analysed),
        "pages_analysis_failed": len(failed),
        "pages_no_topic": len(no_topic_pages),
        "unreadable_page_numbers": numbers(in_state("unreadable")),
        "review_page_numbers": numbers(in_state("needs_review")),
        "uncovered_page_numbers": numbers(uncovered),
        "processing_page_numbers": numbers(in_state("processing")),
        "duplicate_page_numbers": numbers(in_state("duplicate")),
        "excluded_page_numbers": numbers(in_state("excluded")),
        "not_analysed_page_numbers": numbers(not_analysed),
        "failed_page_numbers": numbers(failed),
        "no_topic_page_numbers": numbers(no_topic_pages),
        "blank_page_numbers": numbers(page for page, item in resolved if item.state == "read" and _is_blank(page)),
        "pages": [{
            "page_id": page.page_id,
            "page_number": page.page_number,
            "page_order": page.page_order,
            "state": item.state,
            # "waiting" (hourly AI budget low) needs another message than "recognizing".
            "extraction_status": str(page.extraction_status or ""),
            "analysis": item.analysis,
            "covered": item.covered,
            "no_topic": item.no_topic and not item.covered,
            "duplicate_of": _original_id(page) if item.state == "duplicate" else None,
            "text_source": page.text_source,
            "blank": item.state == "read" and _is_blank(page),
        } for page, item in resolved],
    }


def file_status(file: FileFacts, *, no_topic_page_ids: Any = (),
                originals: Mapping[int, PageFacts] | None = None) -> dict[str, Any]:
    """The status of one uploaded document.

    State (first match): no page at all -> unreadable; every page excluded -> excluded;
    any page still processing -> processing; every other page unreadable -> unreadable;
    every readable page without analysis -> not_analysed; some unreadable page or some
    readable page whose analysis failed or has not run -> partly_analysed; any page
    needing an OCR check (offline OCR, unclear, confidence < 0.45, a page of a repaired
    PDF) -> needs_ocr_review; else analysed.

    Page counts are disjoint by reading state (read + review + unreadable + processing +
    duplicate + excluded = total); `pages_readable` is read + review + duplicate, and
    `pages_not_analysed` / `pages_analysis_failed` / `pages_uncovered` / `pages_no_topic`
    are disjoint subsets of it. Pass `originals` (every page of the project by id) so a
    duplicate whose original is in another file borrows that original's analysis.
    A page that a topic cites counts as analysed whatever the page lists say, and a
    "duplicate" mark without an original id is ignored (the page stands alone).
    """

    return _summary(file, originals, _ids(no_topic_page_ids))


def material_report(files: Sequence[FileFacts], *, topics_total: int = 0, topics_included: int = 0,
                    terms_total: int = 0, skipped_duplicate_files: Sequence[Any] | None = (),
                    no_topic_page_ids: Any = ()) -> dict[str, Any]:
    """Everything a student needs to trust that the material was fully understood.

    `nothing_missing` holds when no page is processing or unreadable, every readable page
    was analysed, and every analysed content page is covered by a topic. Pages in
    `no_topic_page_ids` (the model confirmed they hold no learning topic) and
    `no_content` pages are not uncovered. Excluded pages never count against it.
    `complete` additionally needs every page's reading to be verified: a page flagged
    "needs review" (offline OCR, an unclear or low-confidence reading, a page of a repaired
    PDF) keeps it false until the student checks it (review A F2) - its topics are in the
    report all the same, so nothing that uses the topics waits for that check.
    `skipped_duplicate_files` and `no_topic_page_ids` may be passed straight from
    `analysis_json` (missing -> None, a lone string, string ids).
    """

    files = list(files or ())
    no_topic = _ids(no_topic_page_ids)
    every_page = _by_id(page for file in files for page in (file.pages or ()))
    statuses = [_summary(file, every_page, no_topic) for file in files]
    totals = {key: sum(int(status[key]) for status in statuses) for key in _COUNT_KEYS}
    skipped = _names(skipped_duplicate_files)
    topics_total = _count(topics_total)
    topics_included = _count(topics_included)

    issues: list[dict[str, Any]] = []
    per_file = (("processing", "processing_page_numbers"),
                ("unreadable_pages", "unreadable_page_numbers"),
                ("needs_review", "review_page_numbers"),
                ("analysis_failed", "failed_page_numbers"),
                ("not_analysed", "not_analysed_page_numbers"),
                ("uncovered_pages", "uncovered_page_numbers"))
    for code, key in per_file:
        for status in statuses:
            page_numbers = status[key]
            empty_file = code == "unreadable_pages" and status["state"] == "unreadable"
            if page_numbers or empty_file:
                issues.append({"code": code, "file_id": status["file_id"],
                               "filename": status["filename"], "page_numbers": list(page_numbers)})
    for name in skipped:
        issues.append({"code": "duplicate_file_skipped", "file_id": None, "filename": name,
                       "page_numbers": []})
    if topics_included < topics_total:
        issues.append({"code": "topics_excluded", "file_id": None, "filename": None,
                       "page_numbers": [], "count": topics_total - topics_included})

    states = Counter(status["state"] for status in statuses)
    nothing_missing = (not states["processing"] and not states["unreadable"]
                       and not totals["pages_processing"] and not totals["pages_unreadable"]
                       and not totals["pages_not_analysed"] and not totals["pages_analysis_failed"]
                       and not totals["pages_uncovered"])
    complete = nothing_missing and not totals["pages_review"]
    return {
        "files": statuses,
        "documents_uploaded": len(files),
        "documents_analysed": states["analysed"] + states["needs_ocr_review"],
        "documents_by_state": {state: states[state] for state in FILE_STATES},
        **totals,
        "topics_total": topics_total,
        "topics_included": topics_included,
        "terms_total": _count(terms_total),
        "skipped_duplicate_files": skipped,
        "complete": complete,
        "nothing_missing": nothing_missing,
        "issues": issues,
    }


# --------------------------------------------------------------------------- duplicates

def _tokens(text: str) -> list[str]:
    """Folded words: case, umlaut spelling ("Muenchen"/"München"), accents and a word
    hyphenated across a line break ("Utilita-\\nrismus") do not matter, because two
    readings of one sheet differ in exactly these ways."""

    text = _HYPHEN_BREAK.sub("", str(text or "").replace("­", ""))   # soft hyphen
    folded = unicodedata.normalize("NFKC", text).casefold().translate(_FOLD)
    folded = "".join(character for character in unicodedata.normalize("NFKD", folded)
                     if not unicodedata.combining(character))
    return re.findall(r"[^\W_]+", folded)


def content_words(text: str) -> int:
    """Words with at least two letters: page numbers, bullets and OCR specks are not words."""

    return sum(1 for token in _tokens(text) if sum(character.isalpha() for character in token) >= 2)


def _shingles(tokens: list[str], size: int = SHINGLE_WORDS) -> set[tuple[str, ...]]:
    if len(tokens) < size:
        return {tuple(tokens)} if tokens else set()
    return {tuple(tokens[index:index + size]) for index in range(len(tokens) - size + 1)}


def _fingerprints(pages: Sequence[tuple[int, str]], min_words: int) -> list[tuple[int, set[tuple[str, ...]]]]:
    prints: list[tuple[int, set[tuple[str, ...]]]] = []
    seen: set[int] = set()
    for page_id, text in pages or ():
        if page_id in seen:
            continue
        seen.add(page_id)
        if content_words(text) < max(1, min_words):
            continue
        shingles = _shingles(_tokens(text))
        if shingles:
            prints.append((page_id, shingles))
    return prints


def _jaccard(first: set[tuple[str, ...]], second: set[tuple[str, ...]]) -> float:
    union = len(first | second)
    return len(first & second) / union if union else 0.0


def _containment(first: set[tuple[str, ...]], second: set[tuple[str, ...]]) -> float:
    smaller = min(len(first), len(second))
    return len(first & second) / smaller if smaller else 0.0


def find_duplicate_pages(pages: Sequence[tuple[int, str]], *, threshold: float = DUPLICATE_THRESHOLD,
                         min_words: int = MIN_COMPARE_WORDS) -> dict[int, int]:
    """Duplicate page id -> the earlier original page id.

    `pages` is (page_id, text) in page order. Two pages are the same sheet when the
    Jaccard similarity of their folded word 5-gram sets is at least `threshold`, so a
    second photo of a handout matches although OCR read a few words differently. A page
    is only compared with originals, so a chain of copies points at the first one.
    """

    duplicates: dict[int, int] = {}
    originals: list[tuple[int, set[tuple[str, ...]]]] = []
    for page_id, shingles in _fingerprints(pages, min_words):
        match = next((original_id for original_id, other in originals
                      if _jaccard(shingles, other) >= threshold), None)
        if match is None:
            originals.append((page_id, shingles))
        else:
            duplicates[page_id] = match
    return duplicates


def find_overlaps(pages: Sequence[tuple[int, str]], *, low: float = OVERLAP_LOW,
                  high: float = DUPLICATE_THRESHOLD,
                  min_words: int = MIN_COMPARE_WORDS) -> list[tuple[int, int, float]]:
    """Partly overlapping pages: (earlier id, later id, share), informational only.

    The share is the part of the *smaller* page's 5-grams found in the other page: a
    one-page revision sheet that repeats half of a long handout page shares 0.5 of its
    phrases, although the Jaccard similarity of the two is far lower. Pairs that are
    duplicates (Jaccard >= `high`) are left to `find_duplicate_pages`. Pass pages without
    their duplicates to keep the list short.
    """

    prints = _fingerprints(pages, min_words)
    overlaps: list[tuple[int, int, float]] = []
    for index, (first_id, first) in enumerate(prints):
        for second_id, second in prints[index + 1:]:
            if _jaccard(first, second) >= high:
                continue
            share = _containment(first, second)
            if share >= low:
                overlaps.append((first_id, second_id, round(share, 3)))
    return overlaps

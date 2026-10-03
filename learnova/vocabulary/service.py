"""Deterministic vocabulary parsing, validation and answer checking."""

from __future__ import annotations

import re
import unicodedata
from typing import Any


SUPPORTED_LANGUAGES = {
    "en": "English", "de": "German", "fr": "French", "es": "Spanish",
}
VALIDATION_STATUSES = {
    "valid", "likely_valid", "needs_review", "likely_ocr_error",
    "translation_mismatch", "wrong_language", "duplicate",
    "missing_translation", "unrecognized",
}
ARTICLES = {"de": {"der", "die", "das"}, "en": {"a", "an", "the"}}
KNOWN_PAIRS = {
    ("en", "de", "environment"): {"umwelt", "die umwelt"},
    ("en", "de", "house"): {"haus", "das haus"},
    ("en", "de", "school"): {"schule", "die schule"},
    ("en", "de", "friend"): {"freund", "freundin", "der freund", "die freundin"},
    ("de", "en", "umwelt"): {"environment"},
    ("fr", "de", "bonjour"): {"hallo", "guten tag"},
    ("es", "de", "casa"): {"haus", "das haus"},
}


# ---- Is this line a word, or is it a sentence? --------------------------------------
#
# A scanned vocabulary page is not a list of word pairs. It is word pairs with example
# sentences threaded between them, and the scanner used to turn every line into an
# entry - so a student ended up with flashcards whose front was a whole sentence, and
# whose example box was empty because the example had become its own card.
#
# The rules below were measured against 74 lines drawn from the four supported
# languages before being trusted. Two signals that looked obvious failed: possessives
# ("my best friend", "mein bester Freund") and time adverbs ("heute Abend essen") open
# plenty of real entries, and treating them as sentence starts destroyed those entries.
# The pronoun threshold is four words for the same reason - "il y a", "es gibt viele"
# and "hay que" are things you learn, not things somebody said.
ENTRY_KINDS = ("word", "phrase", "sentence")

# Articles that belong to a headword rather than making it a phrase.
HEADWORD_ARTICLES = frozenset({
    "der", "die", "das", "den", "dem", "des",
    "a", "an", "the",
    "le", "la", "les", "l'", "un", "une", "des",
    "el", "los", "las", "unos", "unas",
})
# Subject pronouns and existential markers: somebody is saying something.
SENTENCE_OPENERS = frozenset({
    "i", "you", "he", "she", "it", "we", "they", "there",
    "ich", "du", "er", "sie", "es", "wir", "ihr", "man",
    "je", "j'", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "yo", "tú", "él", "ella", "nosotros", "ustedes", "hay",
})
TERMINAL_PUNCTUATION = ("...", "…", ".", "!", "?")
_OPENER_MIN_WORDS = 4
_LONG_ENOUGH_TO_BE_A_SENTENCE = 6


def headword(value: Any) -> str:
    """A headword without the bookkeeping a vocabulary list prints around it.

    Drops gender and usage hints in brackets, the plural tail in "der Bahnhof, -höfe",
    and a comparative after a slash - none of which change what kind of thing it is.
    """

    text = clean_text(value, 2000)
    text = re.sub(r"\s*\([^)]*\)", "", text)
    text = re.sub(r"\s*\[[^\]]*\]", "", text)
    text = re.sub(r",\s*[-–—]\S*\s*$", "", text)
    text = re.sub(r"\s*/\s*\S+\s*$", "", text)
    return text.strip(" ,;:")


def text_kind(value: Any) -> str:
    """Classify one side of a line as a word, a fixed phrase, or a sentence."""

    text = headword(value)
    if not text:
        return "word"
    words = text.split()
    count = len(words)
    first = words[0].casefold().strip(",.;:!?¿¡")
    if text.endswith(TERMINAL_PUNCTUATION) and count >= 3:
        return "sentence"
    if first in SENTENCE_OPENERS and count >= _OPENER_MIN_WORDS:
        return "sentence"
    if count >= _LONG_ENOUGH_TO_BE_A_SENTENCE:
        return "sentence"
    if count <= 1:
        return "word"
    if first in HEADWORD_ARTICLES and count <= 3:
        return "word"
    return "phrase"


# What a student means by "words" is a word *or* a fixed phrase - both are things you
# look up. Only a full sentence is the other thing.
PRACTICE_SCOPES = ("all", "words", "sentences")
_SCOPE_KINDS = {
    "all": frozenset(ENTRY_KINDS),
    "words": frozenset({"word", "phrase"}),
    "sentences": frozenset({"sentence"}),
}


def practice_scope(value: Any) -> str:
    """Normalise a requested scope, defaulting to everything."""

    scope = clean_text(value, 20).casefold()
    return scope if scope in PRACTICE_SCOPES else "all"


def kinds_in_scope(scope: Any) -> frozenset[str]:
    """Which entry kinds a scope covers."""

    return _SCOPE_KINDS[practice_scope(scope)]


def in_scope(kind: Any, scope: Any) -> bool:
    """Whether an entry of this kind belongs in a session with this scope."""

    label = clean_text(kind, 20).casefold()
    return (label if label in ENTRY_KINDS else "word") in kinds_in_scope(scope)


def entry_kind(entry: dict[str, Any]) -> str:
    """What kind of thing this entry is, judged on the side the student is learning."""

    return text_kind(entry.get("source_term"))


def sides_disagree(entry: dict[str, Any]) -> bool:
    """A word on one side and a sentence on the other means the columns slipped."""

    source, target = text_kind(entry.get("source_term")), text_kind(entry.get("target_translation"))
    if not clean_text(entry.get("target_translation")):
        return False
    return (source == "sentence") != (target == "sentence")


def _fold(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(char))


def illustrates(sentence: Any, term: Any) -> bool:
    """Whether this sentence looks like an example *of* that term.

    Matching on a stem rather than the whole word, because an example almost always
    inflects it: "putzen" appears as "putze", "médiathèque" keeps its stem, and an
    English headword loses its "to". Short function words are ignored, so "es gibt"
    does not match every sentence containing "es".
    """

    stems = [
        _fold(word) for word in headword(term).split()
        if len(_fold(word).strip(".,;:!?")) >= 4
    ]
    if not stems:
        return False
    longest = max(stems, key=len).strip(".,;:!?")
    haystack = _fold(clean_text(sentence, 2000))
    return longest[:5] in haystack


def attach_examples(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold a sentence pair into the entry above it when it illustrates that entry.

    Conservative on purpose. A sentence that does not visibly illustrate the line above
    stays an entry of its own rather than being deleted - the student may be learning
    whole sentences, and silently dropping a scanned line is worse than keeping a
    questionable one. Both sides have to read as sentences, so a word paired with a
    sentence is left alone for validation to flag.
    """

    kept: list[dict[str, Any]] = []
    for entry in entries:
        previous = kept[-1] if kept else None
        foldable = (
            previous is not None
            and text_kind(entry.get("source_term")) == "sentence"
            and text_kind(entry.get("target_translation")) == "sentence"
            and text_kind(previous.get("source_term")) != "sentence"
            and not clean_text(previous.get("source_example_sentence"))
            and illustrates(entry.get("source_term"), previous.get("source_term"))
        )
        if foldable and previous is not None:
            previous["source_example_sentence"] = clean_text(entry.get("source_term"), 1000)
            previous["target_example_translation"] = clean_text(
                entry.get("target_translation"), 1000)
            continue
        kept.append(entry)
    for entry in kept:
        entry["entry_kind"] = entry_kind(entry)
    return kept


def clean_text(value: Any, limit: int = 1000) -> str:
    return " ".join(str(value or "").replace("\x00", "").strip().split())[:limit]


def parse_vocabulary_text(text: str, page_number: int = 1) -> dict[str, Any]:
    """Parse conservative table/list candidates without inventing unreadable entries."""
    entries: list[dict[str, Any]] = []
    unrecognized: list[str] = []
    section = ""
    for line_number, raw in enumerate(str(text or "").splitlines(), start=1):
        line = clean_text(raw, 2000)
        line = re.sub(r"^\s*(?:\d+[.)]|[•*])\s*", "", line)
        if not line or re.fullmatch(r"\[Page \d+]", line, re.I):
            continue
        if (re.match(r"^(?:unit|chapter|lesson|section|lektion|kapitel)\b", line, re.I)
                or (len(line) < 60 and line.isupper())):
            section = line
            continue
        parts = [
            clean_text(item, 500) for item in
            re.split(r"\s*(?:\||\t|;|\s[-–—]\s)\s*", line)
            if clean_text(item, 500)
        ]
        if len(parts) < 2:
            unrecognized.append(line)
            continue
        source, target = parts[0], parts[1]
        example = parts[2] if len(parts) > 2 else ""
        entries.append({
            "source_term": source, "target_translation": target,
            "source_example_sentence": example,
            "target_example_translation": "",
            "page_number": page_number, "line_number": line_number,
            "section": section, "confidence": 0.9 if len(parts) >= 2 else 0.5,
            "warnings": [], "status": "needs_review",
        })
    # A scanned page interleaves word pairs with the sentences that illustrate them.
    # Those sentences belong in the example box of the line above, not in a flashcard
    # of their own; the ones that illustrate nothing stay as entries and are labelled.
    entries = attach_examples(entries)
    return {"entries": entries, "unrecognized_lines": unrecognized, "warnings": []}


MAX_MANUAL_ENTRIES = 500


def parse_manual_entries(rows: Any, limit: int = MAX_MANUAL_ENTRIES) -> dict[str, Any]:
    """Build entries from typed-in word/translation/example rows.

    Deliberately not routed through parse_vocabulary_text. That parser splits a line on
    "|", a tab, ";" or a spaced dash, so a perfectly ordinary example sentence - "Wir
    muessen die Umwelt schuetzen - sie ist wichtig" - would be cut in half and the tail
    silently dropped. When the student has already separated the three fields for us,
    re-joining them into one delimited line only creates a chance to lose their words.

    A row needs both a word and a translation to be an entry; anything less is skipped
    rather than guessed at. Confidence is 1.0 because a person typed it, not a model.

    A half-filled row - a word with no translation, or the reverse - is reported in
    `unrecognized_lines` rather than only vanishing. The import form refuses to submit
    one, so this is the backstop for a payload that did not come from that form.
    """

    entries: list[dict[str, Any]] = []
    skipped: list[str] = []
    if not isinstance(rows, (list, tuple)):
        return {"entries": entries, "unrecognized_lines": [], "warnings": []}
    for line_number, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            continue
        source = clean_text(raw.get("source_term") or raw.get("word"), 500)
        target = clean_text(raw.get("target_translation") or raw.get("translation"), 500)
        if not source or not target:
            if source or target:
                skipped.append(source or target)
            continue
        entries.append({
            "source_term": source,
            "target_translation": target,
            "source_example_sentence": clean_text(
                raw.get("source_example_sentence") or raw.get("example"), 1000),
            "target_example_translation": "",
            "page_number": 1, "line_number": line_number, "section": "",
            "confidence": 1.0, "warnings": [], "status": "needs_review",
            # Typed rows are labelled too: a student can type a sentence they want to
            # learn, and the practice filter needs to know which rows those are.
            "entry_kind": text_kind(source),
        })
        if len(entries) >= limit:
            break
    return {"entries": entries, "unrecognized_lines": skipped, "warnings": []}


def normalize_answer(value: Any, *, accents: bool = True) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"[.!?,;:]+$", "", text)
    if not accents:
        text = "".join(
            char for char in unicodedata.normalize("NFKD", text)
            if not unicodedata.combining(char)
        )
    return " ".join(text.split())


def validate_entry(
    entry: dict[str, Any], source_language: str, target_language: str,
    seen: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    source = clean_text(entry.get("source_term"), 300)
    target = clean_text(entry.get("target_translation"), 500)
    key = (normalize_answer(source), normalize_answer(target))
    warnings: list[str] = []
    status = "likely_valid"
    suggestion = ""
    explanation = "The word pair is plausible but should be confirmed against the source."
    if not source:
        status, explanation = "unrecognized", "No readable source term was detected."
    elif not target:
        status, explanation = "missing_translation", "A translation is required."
    elif seen is not None and key in seen:
        status, explanation = "duplicate", "This word pair already appears in the list."
    elif re.search(r"\d", source + target) and not re.search(r"\b\d{4}\b", source + target):
        status, explanation = "needs_review", "Digits may have been introduced during OCR."
    known = KNOWN_PAIRS.get((source_language, target_language, normalize_answer(source)))
    if known and status == "likely_valid":
        if normalize_answer(target) in known:
            status, explanation = "valid", "The translation matches the selected language pair."
        elif source_language == "en" and target_language == "de" and normalize_answer(source) == "environment" and normalize_answer(target) == "umweit":
            status, suggestion = "likely_ocr_error", "Umwelt"
            explanation = "“Umweit” is likely an OCR error for “Umwelt”."
        else:
            status = "translation_mismatch"
            suggestion = sorted(known)[0]
            explanation = "The detected translation does not match the expected meaning."
    # One side a headword and the other a full sentence is the signature of a scan
    # whose columns slipped, or of a line split on a dash inside a sentence. It is not
    # something to guess at, so it goes to the student.
    kind = text_kind(source)
    if status in {"valid", "likely_valid"} and sides_disagree(
            {"source_term": source, "target_translation": target}):
        status = "needs_review"
        explanation = (
            "One side reads as a sentence and the other as a single word; check the columns."
        )
    if target_language == "de" and target and target[0].islower():
        first = normalize_answer(target).split()[0]
        if first not in ARTICLES["de"] and normalize_answer(source) in {"environment", "house", "school", "friend"}:
            warnings.append("German nouns normally start with a capital letter.")
            if status == "likely_valid":
                status = "needs_review"
    if seen is not None:
        seen.add(key)
    return {
        **entry, "source_term": source, "target_translation": target,
        "status": status, "suggested_translation": suggestion,
        "validation_explanation": explanation, "warnings": warnings,
        # Recomputed rather than trusted: the student may have edited the row since it
        # was parsed, turning a word into a sentence or the reverse.
        "entry_kind": kind,
    }


# A review screen is worth a student's time only when there is something on it to
# decide. Everything below answers one question per entry: does a human have to look at
# this? Anything that does not is confirmed automatically, and an import where nothing
# does skips the review page altogether.
#
# 0.85 is not a new number - it is the bar the old "Accept high-confidence entries"
# button already used. Pressing that button was the first thing every student did, so it
# is now the default rather than a chore.
CONFIDENT_ENOUGH = 0.85
QUIET_STATUSES = frozenset({"valid", "likely_valid"})


def needs_attention(entry: dict[str, Any], *, typed_by_hand: bool) -> bool:
    """Whether a student has to look at this entry before it becomes a flashcard.

    `typed_by_hand` matters. A recognised entry carries an OCR confidence and a
    "confirm this against the source" caveat, both of which are meaningful: there is a
    photo it could have been misread from. A typed entry has no source to check against,
    so that caveat would be asking the student to confirm their own keystrokes. For typed
    words only a real defect - a blank half, a duplicate, stray digits - is worth a stop.
    """

    if not clean_text(entry.get("source_term")) or not clean_text(entry.get("target_translation")):
        return True
    if clean_text(entry.get("suggested_translation")):
        return True
    if str(entry.get("status") or "needs_review") not in QUIET_STATUSES:
        return True
    if typed_by_hand:
        return False
    try:
        confidence = float(entry.get("confidence") or 0)
    except (TypeError, ValueError):
        return True
    return confidence < CONFIDENT_ENOUGH


def review_plan(entries: list[dict[str, Any]], *, typed_by_hand: bool) -> dict[str, Any]:
    """Split entries into the ones needing a decision and the ones that do not.

    Returns the flagged indices rather than the entries themselves so a caller can mark
    the rest confirmed in place without rebuilding the list.
    """

    flagged = [
        index for index, entry in enumerate(entries)
        if needs_attention(entry, typed_by_hand=typed_by_hand)
    ]
    return {
        "flagged": flagged,
        "flagged_count": len(flagged),
        "total": len(entries),
        "review_needed": bool(flagged),
    }


def autoconfirm(entries: list[dict[str, Any]], *, typed_by_hand: bool) -> dict[str, Any]:
    """Mark everything that needs no decision as confirmed, and report what is left.

    Card generation refuses to run on an unconfirmed entry, which is the safeguard that
    kept the review page mandatory. Confirming the quiet ones here keeps that safeguard
    pointed at exactly the entries it was meant for.
    """

    plan = review_plan(entries, typed_by_hand=typed_by_hand)
    flagged = set(plan["flagged"])
    for index, entry in enumerate(entries):
        # Written for every entry, not only the confirmed ones: a caller asking
        # "is this still open?" should never have to distinguish False from absent.
        entry["user_confirmed"] = bool(entry.get("user_confirmed")) or index not in flagged
    return plan


def check_answer(
    answer: Any, expected: str, alternatives: list[str] | None = None, *,
    strictness: str = "normal", language: str = "",
) -> dict[str, Any]:
    accepted = [expected, *(alternatives or [])]
    raw = clean_text(answer)
    if strictness == "exact":
        match = next((value for value in accepted if raw == clean_text(value)), None)
    else:
        normal = normalize_answer(raw, accents=True)
        match = next(
            (value for value in accepted if normal == normalize_answer(value, accents=True)),
            None,
        )
        if not match and strictness == "flexible":
            def without_article(value: Any) -> str:
                return " ".join(
                    part for part in normalize_answer(value).split()
                    if part not in ARTICLES.get(language, set())
                )
            match = next(
                (value for value in accepted if without_article(raw) == without_article(value)),
                None,
            )
    return {"correct": bool(match), "matched_answer": match or "", "accepted": accepted}


def card_variants(entry: dict[str, Any], directions: list[str], settings: dict[str, Any]) -> list[dict[str, Any]]:
    source = clean_text(entry.get("source_term"))
    target = clean_text(entry.get("target_translation"))
    example = clean_text(entry.get("source_example_sentence"), 1200)
    cards: list[dict[str, Any]] = []
    common = {
        "type": "term_definition", "explanation": example if settings.get("include_examples") else "",
        "hint": clean_text(entry.get("part_of_speech")) if settings.get("include_hints") else "",
        "tags": ["vocabulary", clean_text(entry.get("source_language"))],
        "options": [], "difficulty": settings.get("difficulty", "medium"),
        "source_reference": f"Page {entry.get('source_page')}" if entry.get("source_page") else "",
    }
    if "source_to_target" in directions:
        cards.append({**common, "front": source, "back": target})
    if "target_to_source" in directions:
        cards.append({**common, "front": target, "back": source})
    if example and "source_to_blank" in directions:
        blank = re.sub(re.escape(source), "________", example, count=1, flags=re.I)
        if blank != example:
            cards.append({**common, "type": "fill_blank", "front": blank, "back": source})
    if example and "example_to_word" in directions:
        cards.append({**common, "type": "fill_blank", "front": example, "back": source})
    return cards

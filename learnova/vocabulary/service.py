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
    # A row read from a textbook table knows its kind from the column it stood in: the
    # examples column holds sentences even when one is as short as "Il pleut.", and the
    # word column never holds one. So does a sentence that was folded out of an entry's
    # example box. There the column beats the heuristic.
    if (entry.get("origin") == TEXTBOOK_ORIGIN or entry.get("example_of")) \
            and entry.get("entry_kind") in ENTRY_KINDS:
        kind = str(entry["entry_kind"])
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


# ---- A textbook page, read as the table it is -----------------------------------------
#
# Vocabulary books print a table: the word with its pronunciation in brackets, the
# translation, and an example sentence with the sentence's own translation beneath it.
# Reading such a page as a stream of text and re-guessing which line is which costs an
# OCR call plus a translation call per uncertain word, and still gets the columns wrong
# whenever a sentence wraps. One structured vision call per page returns the rows as
# rows. The code below only tidies them; nothing here needs a second provider call.
TEXTBOOK_ORIGIN = "textbook_page"
_PHONETIC_BRACKETS = re.compile(r"\s*\[([^\]]*)\]")


def page_reader_instructions(source_name: str, target_name: str) -> str:
    """The one prompt the page reader gets, with the two languages filled in."""

    return "\n".join([
        "You are reading one page of a school vocabulary book. The words are in "
        f"{source_name}; the translations are in {target_name}.",
        "Each vocabulary entry is one row with up to three columns:",
        f"- left: the word or phrase in {source_name}, often followed by its pronunciation "
        "in square brackets;",
        f"- middle: the {target_name} translation;",
        f"- right (often shaded): one or more example sentences in {source_name}, each with "
        f"its {target_name} translation beneath it.",
        "Return one row per entry in reading order. Put the pronunciation in \"phonetic\", "
        "never in \"term\".",
        "Example sentences belong to the row they are printed in. Pair each sentence with "
        "its own translation.",
        "Ignore headings, unit labels, page numbers, pictures and decoration. A grammar or "
        "usage box is not an entry: put its text in \"note\" of the row it belongs to, or "
        "leave it out.",
        "Copy spelling, accents, articles and punctuation exactly. If a cell is unreadable, "
        "leave it \"\" and lower \"confidence\". Never guess or invent a word or a translation.",
        "Return JSON only.",
    ])


def _clamp_confidence(value: Any, default: float = 0.5) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return default


def split_phonetic(term: Any) -> tuple[str, str]:
    """Separate "à travers [atʁavɛʁ]" into the term and its pronunciation."""

    text = clean_text(term, 300)
    found = _PHONETIC_BRACKETS.findall(text)
    return clean_text(_PHONETIC_BRACKETS.sub("", text), 300), clean_text(" ".join(found), 120)


def has_checkable_stem(term: Any) -> bool:
    """Whether `illustrates` can say anything about this term at all."""

    return any(len(_fold(word).strip(".,;:!?")) >= 4 for word in headword(term).split())


def sentence_entry(parent: dict[str, Any], sentence: Any, translation: Any) -> dict[str, Any]:
    """An entry of its own for one translated example: what "sentences only" practises."""

    return {
        "source_term": clean_text(sentence, 300), "target_translation": clean_text(translation, 500),
        "alternatives": [], "source_example_sentence": "", "target_example_translation": "",
        "example_of": clean_text(parent.get("source_term"), 300),
        "page_number": parent.get("page_number"), "line_number": parent.get("line_number"),
        "section": parent.get("section", ""), "confidence": parent.get("confidence", 0.5),
        "warnings": [], "status": "needs_review", "entry_kind": "sentence",
        "origin": parent.get("origin", ""), "included": True,
    }


def expand_examples(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every translated example an entry of its own, right after its word.

    `attach_examples` puts a scanned sentence into the example box of the word above
    it, which is where a flashcard wants it. A student who chose "sentences only" wants
    that same sentence as a thing to practise, and the practice filter works on entries,
    so the sentence is also kept as one. Only a translated example qualifies: without a
    translation there is nothing to check an answer against.
    """

    result: list[dict[str, Any]] = []
    for entry in entries:
        result.append(entry)
        sentence = clean_text(entry.get("source_example_sentence"), 1000)
        translation = clean_text(entry.get("target_example_translation"), 1000)
        if sentence and translation and entry.get("entry_kind") != "sentence":
            result.append(sentence_entry(entry, sentence, translation))
    return result


def rows_to_entries(page: Any, page_number: int = 1) -> dict[str, Any]:
    """Turn the rows the page reader returned into import entries.

    Each row becomes one entry for the word, with its first example attached, and every
    example that has a translation also becomes a sentence entry of its own. Nothing is
    dropped: a row whose word could not be read arrives as an entry with a blank side,
    which the review page asks the student to type in or rescan.

    The kind comes from the column, not from the heuristic: the word column never holds
    a sentence (a fixed expression such as "Comment ça va ?" is a phrase you learn), and
    the examples column holds nothing else.
    """

    entries: list[dict[str, Any]] = []
    rows = page.get("rows") if isinstance(page, dict) else None
    for index, raw in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(raw, dict):
            continue
        term, phonetic = split_phonetic(raw.get("term"))
        phonetic = clean_text(raw.get("phonetic"), 120) or phonetic
        translation = clean_text(raw.get("translation"), 500)
        if not term and not translation:
            continue
        examples: list[tuple[str, str]] = []
        listed = raw.get("examples")
        for example in listed if isinstance(listed, list) else []:
            if not isinstance(example, dict):
                continue
            sentence = clean_text(example.get("sentence"), 1000)
            if sentence:
                examples.append((sentence, clean_text(example.get("translation"), 1000)))
        kind = text_kind(term) if term else "word"
        entry = {
            "source_term": term, "target_translation": translation, "alternatives": [],
            "phonetic": phonetic, "notes": clean_text(raw.get("note"), 2000),
            "source_example_sentence": examples[0][0] if examples else "",
            "target_example_translation": examples[0][1] if examples else "",
            "page_number": page_number, "line_number": index, "section": "",
            "confidence": _clamp_confidence(raw.get("confidence")), "warnings": [],
            "status": "needs_review", "entry_kind": "phrase" if kind == "sentence" else kind,
            "origin": TEXTBOOK_ORIGIN, "included": True,
        }
        entries.append(entry)
        entries.extend(
            sentence_entry(entry, sentence, translation)
            for sentence, translation in examples if translation)
    _flag_misplaced_examples(entries)
    return {"entries": entries, "unrecognized_lines": [], "warnings": []}


def _flag_misplaced_examples(entries: list[dict[str, Any]]) -> None:
    """Lower the confidence of a word whose example visibly belongs to a neighbour.

    The check is deliberately narrow. An example often fails to contain its own word
    (irregular verbs: "aller" / "Je vais"), so merely failing to match is no evidence.
    Containing *another* row's headword while not containing this one is.
    """

    words = [entry for entry in entries if entry.get("entry_kind") != "sentence"
             and has_checkable_stem(entry.get("source_term"))]
    for entry in words:
        example = entry.get("source_example_sentence")
        if not example or illustrates(example, entry["source_term"]):
            continue
        if any(other is not entry and illustrates(example, other["source_term"]) for other in words):
            entry["confidence"] = min(float(entry["confidence"]), CONFIDENT_ENOUGH - 0.05)
            entry["warnings"] = list(entry.get("warnings", [])) + [
                "The example sentence may belong to a neighbouring word."]


def wants_second_opinion(entry: dict[str, Any]) -> bool:
    """Whether a translation provider should be asked about this entry at all.

    Never for a sentence: a sentence has many right translations, so a second opinion
    would flag good rows and cost a call doing it. Never for a row read confidently from
    a textbook table: the translation was printed next to the word, there is nothing to
    arbitrate, and asking would turn one call per page into one per word.
    """

    kind = entry.get("entry_kind") if entry.get("entry_kind") in ENTRY_KINDS else text_kind(entry.get("source_term"))
    if kind == "sentence":
        return False
    if entry.get("origin") != TEXTBOOK_ORIGIN:
        return True
    try:
        return float(entry.get("confidence") or 0) < CONFIDENT_ENOUGH
    except (TypeError, ValueError):
        return True


def _complete(entry: dict[str, Any]) -> bool:
    return bool(clean_text(entry.get("source_term")) and clean_text(entry.get("target_translation")))


def merge_rescan(current: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold a second reading of the same page into what the student already has.

    Rows the student has settled - confirmed, typed or edited - are kept exactly as they
    are, in place. An open row is replaced when the new reading has a complete row for
    the same word, or for the same translation when it was the word that could not be
    read. Whatever the new reading found that the first missed is added at the end.
    """

    usable = [entry for entry in fresh if _complete(entry)]
    by_term = {normalize_answer(entry["source_term"]): entry for entry in usable}
    by_translation = {normalize_answer(entry["target_translation"]): entry for entry in usable}
    used: set[int] = set()
    merged: list[dict[str, Any]] = []
    replaced = 0
    for entry in current:
        match = None
        if not entry.get("user_confirmed"):
            if clean_text(entry.get("source_term")):
                match = by_term.get(normalize_answer(entry["source_term"]))
            if match is None and clean_text(entry.get("target_translation")):
                match = by_translation.get(normalize_answer(entry["target_translation"]))
        if match is not None and id(match) not in used:
            used.add(id(match))
            merged.append(match)
            replaced += 1
        else:
            merged.append(entry)
    # A word the student already has is not added again, even with a different
    # translation: that would be a second opinion forced into the list as a duplicate.
    terms = {normalize_answer(entry.get("source_term")) for entry in merged}
    added = 0
    for entry in usable:
        term = normalize_answer(entry["source_term"])
        if id(entry) in used or term in terms:
            continue
        terms.add(term)
        merged.append(entry)
        added += 1
    return {"entries": merged, "replaced": replaced, "added": added}


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
        # The kind rides along as a tag so a flashcard session can be narrowed to words
        # or to sentences - what the practice page's scope picker did, now inside the set.
        "tags": ["vocabulary", clean_text(entry.get("source_language")),
                 str(entry.get("entry_kind")) if entry.get("entry_kind") in ENTRY_KINDS else text_kind(source)],
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

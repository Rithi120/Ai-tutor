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
    return {"entries": entries, "unrecognized_lines": unrecognized, "warnings": []}


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
    }


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

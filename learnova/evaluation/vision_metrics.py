"""Transcription metrics for the image-reading suite: CER, WER and structural maths match."""

from __future__ import annotations

import re
import unicodedata

from learnova.vision.notation import canonical_math


def _distance(reference: list[str] | str, hypothesis: list[str] | str) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, expected in enumerate(reference, start=1):
        current = [row]
        for column, actual in enumerate(hypothesis, start=1):
            current.append(min(previous[column] + 1, current[column - 1] + 1,
                               previous[column - 1] + (expected != actual)))
        previous = current
    return previous[-1]


def normalize_text(text: str) -> str:
    """Compare what was written, not how it was laid out: one space, no unclear markers."""

    text = unicodedata.normalize("NFC", str(text or ""))
    text = re.sub(r"\[\?([^\[\]]*)\]", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate: edits needed per reference character (0 is perfect)."""

    expected, actual = normalize_text(reference), normalize_text(hypothesis)
    if not expected:
        return 0.0 if not actual else 1.0
    return _distance(expected, actual) / len(expected)


def wer(reference: str, hypothesis: str) -> float:
    expected, actual = normalize_text(reference).split(), normalize_text(hypothesis).split()
    if not expected:
        return 0.0 if not actual else 1.0
    return _distance(expected, actual) / len(expected)


def math_exact(expected: list[str] | tuple[str, ...], found: list[str]) -> float:
    """Share of expected expressions that appear, structurally identical, among those found."""

    if not expected:
        return 1.0
    canonical = {canonical_math(item) for item in found}
    return sum(1 for item in expected if canonical_math(item) in canonical) / len(expected)


def label_recall(expected: list[str] | tuple[str, ...], text: str) -> float:
    """Share of expected labels that occur in a reading (spacing and case ignored)."""

    if not expected:
        return 1.0
    haystack = re.sub(r"\s+", "", str(text or "")).casefold()
    return sum(1 for label in expected if re.sub(r"\s+", "", label).casefold() in haystack) / len(expected)

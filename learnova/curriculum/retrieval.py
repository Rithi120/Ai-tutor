"""Lexical retrieval (BM25) over curriculum chunks.

BM25 rather than embeddings: the Bildungsplan is written in a fixed technical vocabulary
("Potenzgesetze", "lineare Funktionen", "Erörterung") that students and lessons use too,
the corpus is small, and BM25 needs no model, no extra dependency and no AI call per
lesson. It is deterministic, so tests pin exact rankings. German words are folded
(umlauts, ß) and lightly stemmed, and the heading path counts twice, because a heading
names the topic its whole section is about.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable

_WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)
_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
STOPWORDS = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines",
    "und", "oder", "aber", "mit", "von", "aus", "fuer", "auf", "bei", "zum", "zur", "ueber",
    "unter", "nach", "vor", "sich", "sowie", "auch", "als", "wie", "ist", "sind", "werden",
    "wird", "kann", "koennen", "sie", "es", "er", "ich", "du", "wir", "ihr", "in", "im", "an",
    "am", "zu", "um", "so", "dass", "nicht", "the", "and", "for", "with", "from", "into", "of",
    "to", "a", "an", "is", "are", "on", "in", "how", "what", "why", "this", "that", "be",
    "schuelerinnen", "schueler", "students", "lernen", "learn", "bitte", "please", "erklaere",
    "explain", "help", "hilfe", "aufgabe", "task",
})
_SUFFIXES = ("ungen", "ung", "heit", "keit", "ern", "en", "er", "es", "e", "n", "s")


def stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    words = (word.casefold().translate(_FOLD) for word in _WORD.findall(str(text or "")))
    return [stem(word) for word in words if word not in STOPWORDS and not word.isdigit()]


@dataclass
class IndexedChunk:
    id: int
    document_id: int
    plan: str
    subject: str
    grade_min: int
    grade_max: int
    heading_path: str
    text: str
    page: int | None
    citation: str
    terms: Counter = field(default_factory=Counter)
    length: int = 0


@dataclass(frozen=True)
class Hit:
    chunk: IndexedChunk
    score: float
    at_grade: bool


class CurriculumIndex:
    K1 = 1.4
    B = 0.75

    def __init__(self, chunks: Iterable[IndexedChunk]):
        self.chunks: list[IndexedChunk] = []
        document_frequency: Counter = Counter()
        for chunk in chunks:
            terms = tokens(chunk.text) + tokens(chunk.heading_path) * 2
            chunk.terms, chunk.length = Counter(terms), len(terms)
            document_frequency.update(set(terms))
            self.chunks.append(chunk)
        count = len(self.chunks) or 1
        self.average_length = sum(chunk.length for chunk in self.chunks) / count or 1.0
        self.idf = {
            term: math.log(1 + (count - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    def __len__(self) -> int:
        return len(self.chunks)

    def _bm25(self, chunk: IndexedChunk, query: list[str]) -> float:
        score = 0.0
        for term in query:
            frequency = chunk.terms.get(term)
            if not frequency:
                continue
            norm = frequency + self.K1 * (1 - self.B + self.B * chunk.length / self.average_length)
            score += self.idf.get(term, 0.0) * frequency * (self.K1 + 1) / norm
        return score

    def search(self, query: str, *, plans: Iterable[str], subjects: Iterable[str],
               grade: int | None, limit: int = 3, min_score: float = 0.5) -> list[Hit]:
        """Best chunks for `query` within the learner's plans and subjects.

        Every grade of the plan is searched, not only the learner's: when a Klasse 7
        student asks about a Klasse 9/10 topic the tutor needs to see that it is a later
        topic. Chunks at the learner's grade are preferred, and a chunk is only kept when
        it shares enough distinctive words with the query (`min_score`)."""

        plans, subjects = set(plans), set(subjects)
        query_terms = list(dict.fromkeys(tokens(query)))
        if not query_terms or not plans or not subjects:
            return []
        hits = []
        for chunk in self.chunks:
            if chunk.plan not in plans or chunk.subject not in subjects:
                continue
            score = self._bm25(chunk, query_terms)
            if score < min_score:
                continue
            at_grade = bool(grade and chunk.grade_min and chunk.grade_min <= grade <= chunk.grade_max)
            unknown_band = not chunk.grade_min
            if at_grade:
                score *= 1.5
            elif grade and not unknown_band:
                distance = chunk.grade_min - grade if grade < chunk.grade_min else grade - chunk.grade_max
                score *= max(0.4, 1 - 0.15 * distance)
            hits.append(Hit(chunk, score, at_grade))
        hits.sort(key=lambda hit: (-hit.score, hit.chunk.id))
        return hits[:limit]

"""Baden-Württemberg Bildungsplan grounding: ingestion, chunking, retrieval, prompt context.

Pure functions only; the database models and the Flask CLI live in app.py, like every
other feature. See docs/CURRICULUM.md for the operator workflow and the source policy.
"""

from .catalog import (
    PLANS, SCHOOL_TYPE_LABELS_EN, SCHOOL_TYPES, SUBJECT_KEYS, band_from_text, band_label,
    grade_number, normalize_school_type, plans_for, school_type_label, subject_keys,
)
from .chunking import Chunk, chunk_sections
from .context import build_block
from .extract import Section, detect_format, extract
from .retrieval import CurriculumIndex, Hit, IndexedChunk, tokens
from .sources import (
    KNOWN_SOURCES, RobotsDisallowed, SourceNotCleared, check_provenance, fetch, parse_cleared,
)

__all__ = [
    "PLANS", "SCHOOL_TYPE_LABELS_EN", "SCHOOL_TYPES", "SUBJECT_KEYS", "band_from_text", "band_label",
    "grade_number", "normalize_school_type", "plans_for", "school_type_label", "subject_keys",
    "Chunk", "chunk_sections", "build_block", "Section", "detect_format", "extract",
    "CurriculumIndex", "Hit", "IndexedChunk", "tokens", "KNOWN_SOURCES", "RobotsDisallowed",
    "SourceNotCleared", "check_provenance", "fetch", "parse_cleared",
]

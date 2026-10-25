"""The curriculum block added to tutor instructions.

The block tells the model what the Bildungsplan expects at this learner's grade, quoted
from imported documents with their citation. The rules around it matter as much as the
excerpts: the model must not cite a plan reference it was not given, must not claim a
topic is or is not in the Bildungsplan when nothing was retrieved, and must flag a topic
that the plan places in a later grade instead of silently teaching above level.
"""

from __future__ import annotations

from .catalog import PLAN_LABELS, band_label, niveau_hint, school_type_label
from .retrieval import Hit

MAX_BLOCK_CHARS = 1800
MAX_EXCERPT_CHARS = 520


def _excerpt(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return f"{cut} …"


def build_block(hits: list[Hit], *, grade: int | None, school_type: str, max_chars: int = MAX_BLOCK_CHARS) -> str:
    if not hits:
        return ""
    learner = []
    if school_type:
        learner.append(school_type_label(school_type))
    if grade:
        learner.append(f"Klasse {grade}")
    niveau = niveau_hint(school_type)
    header = (
        "BADEN-WÜRTTEMBERG BILDUNGSPLAN - excerpts retrieved for this learner"
        + (f" ({', '.join(learner)})" if learner else "") + ".\n"
        "Use them to pitch depth, vocabulary, notation and exercise types to what the plan expects "
        "at this grade, and shape practice tasks the way the competency statements describe them "
        "(for example 'Die Schülerinnen und Schüler können ...').\n"
        "If an excerpt places the topic in a LATER grade than the learner's, say so in one short "
        "sentence, teach the prerequisites from the learner's grade first, and keep exercises at "
        "the learner's level. Content at or below the learner's grade can be taught directly.\n"
        "Cite the Bildungsplan only with a reference given in [brackets] below; never invent "
        "section numbers, and never claim a topic is or is not part of the Bildungsplan beyond "
        "what these excerpts show. The student's own material still decides the lesson content."
    )
    if niveau:
        header += f"\nSekundarstufe I niveau: {niveau}."
    parts = [header]
    used = len(header)
    for number, hit in enumerate(hits, start=1):
        chunk = hit.chunk
        band = band_label(chunk.grade_min, chunk.grade_max) or "grade band not stated"
        relation = "at the learner's grade" if hit.at_grade else (
            "LATER than the learner's grade" if grade and chunk.grade_min and chunk.grade_min > grade else
            "earlier than the learner's grade" if grade and chunk.grade_max and chunk.grade_max < grade else
            "grade not determined")
        reference = " · ".join(filter(None, [
            chunk.citation, PLAN_LABELS.get(chunk.plan, chunk.plan), band, chunk.heading_path,
            f"p. {chunk.page}" if chunk.page else "",
        ]))
        room = max_chars - used - len(reference) - 40
        if room < 160:
            break
        text = _excerpt(chunk.text, min(MAX_EXCERPT_CHARS, room))
        entry = f"[{number}] {reference} ({relation})\n{text}"
        parts.append(entry)
        used += len(entry) + 2
    if len(parts) == 1:
        return ""
    return "\n\n".join(parts)

"""The image-reading contract, and the handwriting and maths rules every reader shares.

`CONFUSABLE_CHARACTERS` and `MATH_RULES` live in learnova/ocr/service.py, beside the
Project Mode page prompt that uses them too, so a page, a chat photo and a photographed
answer are read by the same rules rather than by three paraphrases of them.
"""

from __future__ import annotations

import json

from learnova.ocr.service import CONFUSABLE_CHARACTERS, MATH_RULES

HANDWRITING_RULES = (
    "Read handwriting as carefully as print: joined-up or cursive writing, uneven letter sizes and "
    "spacing, light pencil strokes, lined or squared paper, German and English. Transcribe literally: "
    "do not correct spelling, grammar or calculations, do not complete an unfinished word, do not "
    "translate. Crossed-out writing is marked crossed out and is not the student's final answer; arrows, "
    "underlining and margin notes are annotations. Characters that are easily confused - "
    f"{CONFUSABLE_CHARACTERS} - are read from the stroke itself. When the image does not settle which one "
    "it is, keep your literal reading, mark it, and name the alternatives. Never replace an unclear word or "
    "number with one that merely makes sense.")

__all__ = ["COMPACT_FIELD_RULES", "COMPACT_TYPES", "FIELD_RULES", "HANDWRITING_RULES", "MATH_RULES", "PURPOSES",
           "READING_SCHEMA", "SYSTEM", "compact_schema", "reading_instructions"]

PURPOSES = {
    "chat": (
        "A student attached these images to a chat message. Read everything visible; a tutor answers "
        "from your reading alone."),
    "lesson": (
        "A student uploaded these images as lesson material. Read everything teachable: text, "
        "definitions, examples, formulas, exercises, tables, diagrams."),
    "solution": (
        "A student photographed their own written work. Separate the TASK (author 'printed' or "
        "'teacher') from the STUDENT'S WORKING (author 'student'); transcribe the working line by line "
        "in the order written, with every step, sign and unit - it is checked step by step."),
    "notes": "Read these study notes completely and in reading order.",
}

READING_SCHEMA = {
    "images": [{"index": 1, "kind": "handwritten_notes|printed_page|worksheet|mixed|diagram|graph|table|screenshot|photo|other",
                "rotation": 0, "cut_off": False, "legibility": "clear|partly_unclear|unreadable"}],
    "blocks": [{"image": 1, "type": "printed_text|handwriting|formula|table|diagram|heading|annotation|crossed_out|uncertain",
                "text": "", "latex": "", "bbox": [0.0, 0.0, 1.0, 1.0],
                "author": "printed|student|teacher|unknown", "confidence": 0.0}],
    "diagrams": [{"image": 1, "description": "", "labels": [""]}],
    "uncertain": [{"image": 1, "text": "", "alternatives": [""], "reason": "", "affects_answer": False}],
    "clarification_question": "",
    "summary": "",
    "language": "de",
}

# image_understanding:v2. The rule for each field, as short as it can be without losing one:
# the whole request has to fit the vision model's per-minute token limit (docs/VISION.md),
# and every four characters here cost a token on every photo. A bbox is asked for only where
# the close-up second look needs it (confidence below 0.8 or an unclear mark) and latex only
# on formula blocks; v1 asked for both on every line, which made readings long and slow.
FIELD_RULES = (
    "blocks: one per line, reading order; unsure parts as [?reading], alternatives in uncertain. "
    "latex (no $) only on formula blocks; a table row is one block, cells joined by ' | '. "
    "bbox (0-1: left, top, right, bottom) only if confidence < 0.8 or text has [?]. "
    "diagrams: what is drawn, labels as written, axes, units; add nothing. "
    "rotation: degrees clockwise to upright. affects_answer: it changes a task, number or step. "
    "clarification_question: one short question in the student's language only then, else \"\". "
    "summary: one sentence.")

SYSTEM = (
    "You are a careful reader of school material for a tutoring app: printed pages, worksheets, "
    "handwritten notes and calculations, diagrams, graphs and tables. You report what is visible, "
    "never what would be plausible. Return structured JSON only.")

# image_understanding:chat1 (ai_services/prompts.PROMPT_VARIANTS["image_understanding+chat"]).
# What a chat reply needs from a photo and nothing more: the lines in reading order as text
# and LaTeX, which lines are a task and which are the student's working, the unclear parts,
# the diagrams and one summary sentence. No bbox, author or per-line confidence - Project
# Mode's close-up second look and the graded answer photo need those, a chat reply does not -
# and minified. Live 2026-11-02 the v2 reading of a 10-line worksheet stopped after 7 complete
# blocks at Groq's 950 (about 110 tokens a block: pretty-printed, bbox, author and confidence
# on every block); the same worksheet in this shape is about 360 tokens.
COMPACT_TYPES = ("task", "text", "formula", "working", "table", "heading", "note", "crossed_out", "unclear")


def compact_schema(image_count: int = 1) -> dict:
    """The chat reading's shape; "image" on each item only when there are several images."""

    line: dict = {"type": "|".join(COMPACT_TYPES), "text": "", "latex": ""}
    diagram: dict = {"description": "", "labels": [""]}
    unsure: dict = {"text": "", "alternatives": [""], "affects_answer": False}
    if image_count > 1:
        line, diagram, unsure = ({"image": 1, **item} for item in (line, diagram, unsure))
    return {
        "images": [{"index": 1, "kind": READING_SCHEMA["images"][0]["kind"], "rotation": 0, "cut_off": False,
                    "legibility": "clear|partly_unclear|unreadable"}],
        "lines": [line], "diagrams": [diagram], "uncertain": [unsure],
        "clarification_question": "", "summary": "", "language": "de",
    }


COMPACT_FIELD_RULES = (
    "Minified JSON on one line, no code fence. "
    "lines: one per written line, in reading order. type task = an exercise as set, with its number; "
    "working = the student's own solving, one step per line; table = one row, cells joined by ' | '; "
    "note = a margin note or arrow. Maths in latex (no $), words in text; omit an empty text or latex. "
    "Unsure characters as [?reading], alternatives in uncertain. "
    "diagrams: what is drawn, labels as written, axes, units; add nothing. "
    "rotation: degrees clockwise to upright. affects_answer: it changes a task, number or step. "
    "clarification_question: one short question in the student's language only then, else \"\". "
    "summary: one sentence naming the tasks.")


def reading_instructions(purpose: str, *, subject: str = "", image_count: int = 1, question: str = "",
                         compact: bool = False) -> str:
    """The user-turn instructions for one reading call (image_understanding:v2, or with
    `compact` the chat reading image_understanding:chat1)."""

    focus = PURPOSES.get(purpose, PURPOSES["notes"])
    parts = [
        focus,
        "There is 1 image." if image_count == 1 else f"There are {image_count} images, in order; they belong together.",
    ]
    if subject and subject.casefold() not in {"other", "general"}:
        parts.append(f"Subject: {subject[:80]}.")
    if question:
        parts.append(
            "The student's message, for focus only (read everything; do not answer it): "
            f"\"{question[:600]}\"")
    schema = compact_schema(image_count) if compact else READING_SCHEMA
    parts.extend([
        HANDWRITING_RULES,
        MATH_RULES,
        "Return JSON only, exactly this shape: "
        + json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
        COMPACT_FIELD_RULES if compact else FIELD_RULES,
    ])
    return "\n\n".join(parts)

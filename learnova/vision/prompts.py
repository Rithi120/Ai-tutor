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

__all__ = ["HANDWRITING_RULES", "MATH_RULES", "PURPOSES", "READING_SCHEMA", "SYSTEM", "reading_instructions"]

PURPOSES = {
    "chat": (
        "A student attached these images to a chat message. Read everything that is visible, so a tutor "
        "can answer their question without seeing the images."),
    "lesson": (
        "A student uploaded these images as study material for a lesson. Read everything that could be "
        "taught from: text, definitions, examples, formulas, exercises, tables and diagrams."),
    "solution": (
        "A student photographed their own written work. Separate the TASK (usually printed, author "
        "'printed' or 'teacher') from the STUDENT'S WORKING (author 'student'). Transcribe the working "
        "line by line in the order it was written, keeping every intermediate step, sign and unit - it "
        "will be checked step by step, so a missing or reordered line changes the verdict."),
    "notes": "Read these study notes completely and in reading order.",
}

READING_SCHEMA = {
    "images": [{"index": 1, "kind": "handwritten_notes|printed_page|worksheet|mixed|diagram|graph|table|screenshot|photo|other",
                "rotation": 0, "cut_off": False, "legibility": "clear|partly_unclear|unreadable"}],
    "blocks": [{"image": 1, "type": "printed_text|handwriting|formula|table|diagram|heading|annotation|crossed_out|uncertain",
                "text": "the exact visible content, unclear parts as [?reading]", "latex": "",
                "bbox": [0.0, 0.0, 1.0, 1.0], "author": "printed|student|teacher|unknown", "confidence": 0.0}],
    "diagrams": [{"image": 1, "description": "", "labels": [""]}],
    "uncertain": [{"image": 1, "text": "", "alternatives": [""], "reason": "", "affects_answer": False}],
    "clarification_question": "",
    "summary": "",
    "language": "de",
}

SYSTEM = (
    "You are a careful reader of school material for a tutoring app: printed pages, worksheets, "
    "handwritten notes and calculations, diagrams, graphs and tables. You report what is visible, "
    "never what would be plausible. Return structured JSON only.")


def reading_instructions(purpose: str, *, subject: str = "", image_count: int = 1, question: str = "") -> str:
    """The user-turn instructions for one reading call."""

    focus = PURPOSES.get(purpose, PURPOSES["notes"])
    parts = [
        focus,
        f"There {'is 1 image' if image_count == 1 else f'are {image_count} images'}, numbered in the order "
        "given; they belong together, so keep the reading order across them.",
    ]
    if subject and subject.casefold() not in {"other", "general"}:
        parts.append(f"Subject: {subject[:80]}.")
    if question:
        parts.append(
            "The student's message, for focus only (read everything anyway, and do not answer it): "
            f"\"{question[:600]}\"")
    parts.extend([
        HANDWRITING_RULES,
        MATH_RULES,
        "Return JSON only, in exactly this shape: " + json.dumps(READING_SCHEMA, ensure_ascii=False),
        "blocks: one per line or short paragraph, in reading order across all images. 'text' is the "
        "readable transcription; inside it, wrap every word, number or symbol you are not sure of as "
        "[?your reading] and also list it in 'uncertain' with its alternatives. A formula block also gets "
        "'latex', the same expression in LaTeX without $ delimiters. A table block is one row, cells "
        "separated by ' | '. 'author' says who wrote it when you can tell: printed text, the student's "
        "handwriting, or a teacher's correction. 'confidence' is your own estimate from 0 to 1. 'bbox' is "
        "the block's normalized [left, top, right, bottom] on its image.",
        "diagrams: for each diagram, graph or chart, describe what it shows - the objects, the labels "
        "exactly as written, axes and units, and the relationships drawn - without adding facts that are "
        "not drawn. A label you cannot read goes into 'uncertain'.",
        "images: 'rotation' is how many degrees clockwise the image must be turned to read it upright "
        "(0 when it already is). 'cut_off' is true when writing runs off the edge of the photo. "
        "'legibility' is your overall judgement.",
        "uncertain: set 'affects_answer' to true when the unclear part changes the meaning of a task, a "
        "number or a step. clarification_question: one short question to the student, in the language "
        "of their writing, ONLY when an unclear part matters; otherwise an empty string. Do not warn about "
        "anything that is clear.",
        "summary: one sentence saying what the images show.",
    ])
    return "\n\n".join(parts)

"""German as a school subject (Deutsch): texts, literature and language reflection.

A text lesson explains what the text or concept is, gives the terms needed to talk about
it, lays out the structure and central message, teaches the analysis method, and shows a
short model analysis before the student analyses a passage themselves. Every claim about
a text needs evidence from the text.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

GERMAN = SubjectStrategy(
    key="german",
    name="German (texts, literature and language)",
    priorities=(
        "understanding the text and its central message",
        "correct literary and grammar terms",
        "a clear analysis method",
        "claims backed by evidence from the text",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which text, literary concept or language skill this "
                   "lesson is about and what the student will be able to do with it.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the text or the literary or linguistic concept: what it is about or what "
                   "it means, with its context (author, period, genre) where the notes give it.",
                   states=NOT_SECURE),
        LessonStep("explanation", "Explain it again differently: break the passage or concept into a few small "
                   "questions (who? what? how? why?) and answer each in plain words.", modes=RETEACH_ONLY),
        LessonStep("terms", "List the literary and grammar terms and difficult words from the notes as items: label "
                   "= the term exactly as in the notes, text = a simple definition and where it appears in the text.",
                   modes=ALL_MODES),
        LessonStep("structure", "Show the structure of the text (parts, turning points, with line or verse "
                   "references) and state its central message in one sentence."),
        LessonStep("rule", "Explain the analysis or interpretation method step by step, for example how to find a "
                   "stylistic device, name it, and describe its effect in context."),
        LessonStep("model_analysis", "Write a short model analysis of one passage from the notes: the claim, the "
                   "quotation or line reference as evidence, and the explanation of its effect."),
        LessonStep("example", "Give one very short, simple example (a single sentence or line) where the concept or "
                   "device is easy to see, and explain its effect.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the typical mistakes (retelling instead of analysing, naming a device without "
                   "its effect, quoting without line references, confusing narrator and author) with the correct "
                   "approach."),
        LessonStep("guided_exercise", "Let the student practise one analysis step on a short passage, with hints "
                   "in items and a model answer in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the content, the structure, the method and the central message in a few "
                   "short lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "work", "person", "method", "rule", "example", "source"),
    question_types=("multiple_choice", "text", "checkboxes", "text", "dropdown", "text"),
    card_kinds=("term", "work", "person", "method", "rule"),
    rules=(
        "Back every claim about a text with a quotation or line reference.",
        "Keep apart content (what), form (how) and effect (why it matters).",
        "Use the correct literary and grammar terms.",
        "Teach the method, not only the result.",
        "Quote only what is in the notes; mark added context as an addition.",
    ),
    feedback_rule="Show which claim lacks evidence or which device or term was misidentified, and ask the student "
                  "to support the answer with a passage from the text.",
    exam_style="Text analysis and interpretation with operators such as 'Analysiere…', 'Interpretiere…' and "
               "'Erörtere…', characterising figures, describing language and its effect, and argumentative "
               "writing.",
)

"""Geography: concept, terms, process, reading maps and diagrams, human–environment links.

Geography lessons locate first, explain a process step by step, teach how to read the
map, climate chart or diagram (title, legend, scale before interpretation), connect
natural factors with human activity, and transfer the concept to a new place. Like
history, the learn sequence has no separate mistakes part; a reteach adds one.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

GEOGRAPHY = SubjectStrategy(
    key="geography",
    name="Geography",
    priorities=(
        "locating places and processes",
        "explaining processes step by step",
        "reading maps, charts and diagrams correctly",
        "links between people and the environment",
        "transferring a concept to new places",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which place, region or geographical process this "
                   "lesson is about and what the student will be able to explain.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the geographical concept: what it is, where on Earth it occurs, and "
                   "which natural and human factors are involved.", states=NOT_SECURE),
        LessonStep("explanation", "Explain it again differently: draw it as a simple chain of arrows (factor → "
                   "effect → consequence) or compare it with a familiar place.", modes=RETEACH_ONLY),
        LessonStep("terms", "List the geographical terms from the notes as items: label = the term exactly as in the "
                   "notes, text = a simple definition and where it can be observed.", modes=ALL_MODES),
        LessonStep("process", "Explain the process step by step in items in the correct order, saying what "
                   "happens and what causes the next step."),
        LessonStep("diagram", "Explain how to read the map, chart or diagram from the notes: title, legend, axes "
                   "or scale, then what it shows. When the notes only describe a map in words, retell that "
                   "description place by place; never describe a chart, axes or legend the notes do not contain.",
                   when_items=("diagram", "place")),
        LessonStep("cause_effect", "Show the causes and consequences, including how people and the environment "
                   "affect each other, as items (label = cause, text = effect)."),
        LessonStep("example", "Apply the concept to a new place or example and explain what is the same and what "
                   "is different."),
        LessonStep("example", "Give one simple, familiar example (a well-known place or the student's own region) "
                   "where the concept can be seen.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "Name the typical mistakes (mixing up cause and effect, misreading scale, legend or "
                   "axes, describing without explaining) with the correct approach.", modes=RETEACH_ONLY),
        LessonStep("guided_exercise", "Give one task that asks the student to describe and explain a map, chart or "
                   "process, with hints in items and a model answer in answer that uses only what the notes say "
                   "about it.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the concept, the process and the main causes and consequences in a few short "
                   "lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "place", "process", "cause_effect", "diagram", "example", "institution"),
    question_types=("multiple_choice", "ordering", "text", "checkboxes", "dropdown", "text"),
    card_kinds=("term", "place", "process", "cause_effect", "diagram", "example"),
    rules=(
        "Locate first: where on Earth and at what scale.",
        "Read every map or diagram with title, legend and scale before interpreting it.",
        "Describe, then explain, then evaluate.",
        "Connect natural factors with human activity.",
    ),
    feedback_rule="Point to the map detail, factor or step that was misread, explain the correct link between "
                  "cause and effect, and ask the student to try again with that hint.",
    exam_style="Describing and interpreting maps, climate charts and diagrams, explaining processes and "
               "human–environment interactions, and evaluating measures or conflicts of use.",
)

"""Politics (Gemeinschaftskunde) and Economics (Wirtschaft).

These lessons explain a system, concept or institution, then look at it through the eyes
of everyone affected, weigh the arguments, and end with the student's own justified
judgement. Facts, interests and opinions are kept apart, and the tutor stays politically
neutral. Like history, the learn sequence has no separate mistakes part; a reteach adds
one.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

POLITICS = SubjectStrategy(
    key="politics",
    name="Politics and Economics",
    priorities=(
        "how systems and institutions work",
        "stakeholders and their interests",
        "weighing arguments",
        "a judgement based on criteria and reasons",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which political or economic question, system or "
                   "institution this lesson is about and what the student will be able to judge.",
                   modes=ALL_MODES),
        LessonStep("terms", "List the key terms from the notes as items: label = the term exactly as in the notes, "
                   "text = a simple definition and an everyday example; an example the notes do not give is "
                   "marked as an additional example.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the system, concept or institution: its purpose, how it works, who "
                   "decides what, and its rules or legal basis where the notes give them.", states=NOT_SECURE),
        LessonStep("explanation", "Explain it again differently with a familiar comparison (a school, a club or a "
                   "household budget) and say where the comparison stops fitting.", modes=RETEACH_ONLY),
        LessonStep("perspectives", "Present the stakeholders (people, groups, institutions) who are affected, what "
                   "each of them wants, and why."),
        LessonStep("argument", "Weigh the advantages and disadvantages as items: label = pro or contra and the "
                   "claim, text = the reason and who would make this argument."),
        LessonStep("example", "Show the topic with a current or historical example only as the notes give it; do "
                   "not add facts or figures that are not in the notes.", when_items=("example", "event")),
        LessonStep("example", "Give one simple everyday situation that shows the concept.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "Name the typical mistakes (confusing institutions and their powers, stating an "
                   "opinion as a fact, judging without arguments) with the correct approach.", modes=RETEACH_ONLY),
        LessonStep("guided_exercise", "Give one task that asks the student to evaluate a measure or decision and "
                   "justify the judgement with arguments from different perspectives, with hints in items and a "
                   "model answer in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up how it works, the main interests and the key arguments in a few short lines.",
                   modes=ALL_MODES),
    ),
    extraction_focus=("term", "institution", "position", "argument", "process", "example", "event", "person",
                      "theory"),
    question_types=("multiple_choice", "checkboxes", "text", "ordering", "dropdown", "text"),
    card_kinds=("term", "institution", "position", "argument", "process", "theory"),
    rules=(
        "Keep facts, interests and opinions apart.",
        "Name the stakeholders and their interests before judging.",
        "Judge with criteria (for example effectiveness, fairness, legitimacy) and reasons.",
        "Stay neutral; never favour a party or political view.",
        "Use only figures and current events from the notes; mark added background as an addition.",
    ),
    feedback_rule="Point to the fact, institution or argument that was confused, explain it correctly, and ask the "
                  "student for a judgement with a reason.",
    exam_style="Describing a system or institution, analysing a conflict from several perspectives, and judging a "
               "political or economic decision with criteria ('Beurteile…', 'Bewerte…', 'Erörtere…').",
)

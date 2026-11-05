"""History: context, chronology, causes and consequences, perspectives, sources.

A history lesson places the topic in time and space first, then orders the events,
explains why they happened and what followed, shows how different people saw them, and
works with a source when the notes contain one. The learn sequence has no separate
mistakes part (the subject's sequence does not ask for one); a reteach lesson adds one
because a reteach exists precisely because something was misunderstood.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

HISTORY = SubjectStrategy(
    key="history",
    name="History",
    priorities=(
        "placing events in time and space",
        "causes and consequences",
        "different perspectives, then and now",
        "working critically with sources",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which period, event or development this lesson is "
                   "about and which historical question it answers.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the historical context: when and where it took place, the situation "
                   "before, and which groups and people were involved.", states=NOT_SECURE),
        LessonStep("explanation", "Explain it again differently: tell the development as a short story from the "
                   "view of a person living at the time, with a few clear dates.", modes=RETEACH_ONLY),
        LessonStep("timeline", "List the events in chronological order as items: label = the date, text = what "
                   "happened and why it matters for the topic.", when_items=("event", "date")),
        LessonStep("terms", "List the key people and historical terms from the notes as items: label = the name or "
                   "term exactly as in the notes, text = who they were or what it meant in this period.",
                   modes=ALL_MODES),
        LessonStep("cause_effect", "Explain the causes and consequences as items (label = cause, text = effect), "
                   "separating long-term causes, the immediate occasion, and short- and long-term consequences."),
        LessonStep("perspectives", "Show how different groups at the time saw the events and how they are judged "
                   "today, keeping the contemporary view apart from today's judgement."),
        LessonStep("source_analysis", "Analyse one source from the notes in steps: author, date, type of source, "
                   "addressee and intention, content, and how far it can be trusted for the question.",
                   when_items=("source", "work")),
        LessonStep("example", "Give one simple, concrete example (a single event or the experience of one person "
                   "at the time) that shows the main point.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "Name the typical mistakes for this topic (confusing the occasion with the causes, "
                   "mixing up the order of events, judging the past only by today's values) with the correct "
                   "view.", modes=RETEACH_ONLY),
        LessonStep("guided_exercise", "Give one task that asks the student to explain a cause, a consequence or a "
                   "source statement, with hints in items and a model answer in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the chronology, the main causes and consequences and the key terms in a few "
                   "short lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "event", "date", "person", "cause_effect", "source", "institution", "place", "work"),
    question_types=("multiple_choice", "ordering", "text", "checkboxes", "dropdown", "text"),
    card_kinds=("term", "event", "date", "person", "cause_effect", "institution", "source", "place"),
    rules=(
        "Always place events in time and space.",
        "Distinguish long-term causes, the occasion and the consequences.",
        "Keep apart the facts, contemporary perspectives and today's judgement.",
        "Ask of every source: who, when, for whom, and why.",
        "Use only dates and names from the notes; mark added background as an addition.",
    ),
    feedback_rule="Point to the date, cause or source detail that was mixed up, explain the correct connection, "
                  "and ask the student to place it in the chronology again.",
    exam_style="Describe ('Nenne', 'Beschreibe'), explain ('Erkläre', 'Erläutere') and judge ('Beurteile', "
               "'Bewerte') tasks, source and picture analysis, and putting events in chronological order.",
)

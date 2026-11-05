"""Ethics and Philosophy (`ethics`) and Religion (`religion`).

These lessons are about texts, thought experiments and positions. Students fail when a
lesson argues about a text they never fully understood, so the text or thought
experiment is retold completely first (who appears, what happens, what each element
stands for where the notes say so), then the positions and arguments are reconstructed
and compared, and only then is the student asked for a justified judgement. An
interpretation the notes do not give is always labelled as an additional one.

Nothing here is tied to a particular text: the same steps serve any thought experiment,
parable, dilemma or philosophical position the student's notes contain.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

_THEORY_ITEMS = ("theory", "person", "position", "argument")
# What can be retold from the notes: a thought experiment, a text or source, and an
# example. Extraction does not always label a parable or dilemma "thought_experiment"
# (an unknown label becomes "example"), and the retelling must not depend on the label.
_RETOLD_ITEMS = ("thought_experiment", "work", "source", "example")
_TEXT_STAGES = ("comprehension", "comprehension", "interpretation", "discussion", "exam")

ETHICS = SubjectStrategy(
    key="ethics",
    name="Ethics and Philosophy",
    priorities=(
        "understanding the text or thought experiment completely",
        "precise Fachbegriffe",
        "reconstructing arguments",
        "comparing positions fairly",
        "a justified own judgement",
    ),
    steps=(
        LessonStep("objective", "Name in one or two sentences the philosophical question of this topic, what "
                   "the student will be able to explain or judge afterwards, and why it matters (for the exam, "
                   "for everyday decisions, for later topics).", modes=ALL_MODES),
        LessonStep("terms", "Explain every Fachbegriff from the notes in simple words as items: label = the term "
                   "exactly as in the notes, text = a simple definition and a short everyday example; an example "
                   "the notes do not give is marked as an additional example.",
                   modes=ALL_MODES),
        # The notes' own thought experiment, text or example (a cave, a ring, a dilemma) is
        # retold before anything is argued about it: what happens, what it stands for, what
        # it is meant to show, and how it serves the learning objective.
        LessonStep("thought_experiment", "Retell the thought experiment, text or example from the notes completely "
                   "and in order, naming every person, object and place. Say what each element stands for only "
                   "where the notes do (mark any other reading as an additional interpretation), what it is meant "
                   "to show, and how it serves the learning objective.",
                   states=NOT_SECURE, when_items=_RETOLD_ITEMS),
        LessonStep("explanation", "Explain the central idea again in a different way: retell the text or thought "
                   "experiment as a very short simple story or an everyday comparison, then give its message in "
                   "one sentence.", modes=RETEACH_ONLY),
        # A theory the notes teach through a story or image (an allegory, a parable) is that
        # story: the extraction may have called it a "theory", so this step retells it too.
        LessonStep("theory", "Present each theory or position from the notes: who holds it, its core thesis, and "
                   "the argument from premises to conclusion in items; link it to the learning objective. If the "
                   "notes tell it as a story or image, first retell that completely, in order, and what it shows.",
                   states=NOT_SECURE, when_items=_THEORY_ITEMS),
        LessonStep("argument", "State the central philosophical question and the arguments for the possible "
                   "answers, each as items: label = the claim, text = the reason behind it."),
        LessonStep("perspectives", "Compare the different positions and interpretations side by side: what they "
                   "agree on, where they differ, and which reasons support each, without deciding for the "
                   "student; an interpretation the notes do not give is marked as an additional one."),
        LessonStep("example", "Connect the topic to one everyday dilemma a student could face, show how the "
                   "question and the positions apply to it, and link it back to the learning objective.",
                   states=NOT_SECURE),
        LessonStep("example", "Give one simple situation from a student's everyday life that raises the same "
                   "question, and say how each position would answer it.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the typical misunderstandings of this topic (reading an image literally, "
                   "confusing describing with judging, mixing up similar terms or positions) with the correct "
                   "understanding."),
        LessonStep("guided_exercise", "Give one task that first checks comprehension (what happens or what is "
                   "claimed) and then asks for an interpretation or justified judgement, with hints in items and a "
                   "model answer in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the question, the key terms, the positions and the central message in a few "
                   "short lines, and how they answer the learning objective.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "thought_experiment", "theory", "person", "position", "argument", "work",
                      "source", "example"),
    question_types=("multiple_choice", "text", "checkboxes", "text", "ordering", "text"),
    card_kinds=("term", "theory", "position", "argument", "thought_experiment", "person", "work"),
    rules=(
        "Retell what the notes say before interpreting it.",
        "Keep apart what the text says, how it can be interpreted, and the student's own judgement.",
        "Label every interpretation the notes do not give as an additional interpretation.",
        "Present every position fairly; never push the student towards one view.",
        "Ask for reasons, not memorised facts.",
    ),
    feedback_rule="Point to the passage or argument that was misread and explain the correct reading; then ask "
                  "the student to justify the answer rather than recall a fact.",
    exam_style="Tasks that require a justified answer, with operators such as 'Begründe…', 'Erläutere…' and "
               "'Nimm Stellung…': reproduce a position, explain it with the text, and judge it with reasons.",
    # The graded questions after the lesson (the user's steps 11-13): comprehension, then
    # interpretation and discussion, then an exam task that needs a justified answer.
    question_stages=_TEXT_STAGES,
)


RELIGION = SubjectStrategy(
    key="religion",
    name="Religion",
    priorities=(
        "understanding the text or tradition in its context",
        "precise religious and theological terms",
        "comparing traditions and worldviews fairly",
        "a justified own position, kept apart from what a tradition teaches",
    ),
    steps=(
        LessonStep("objective", "Name in one or two sentences the question or tradition of this topic, what the "
                   "student will be able to explain or judge afterwards, and why it matters.", modes=ALL_MODES),
        LessonStep("terms", "Explain every religious or theological term from the notes in simple words as items: "
                   "label = the term exactly as in the notes, text = a simple definition and the tradition it "
                   "belongs to.", modes=ALL_MODES),
        LessonStep("source_analysis", "Retell the complete text, story or ritual from the notes in order, naming "
                   "every person, place and symbol; say what each means only where the notes do, mark any other "
                   "reading as an additional interpretation, and give the central message.",
                   states=NOT_SECURE, when_items=("source", "work", "event", "thought_experiment", "example")),
        LessonStep("explanation", "Explain the central idea again in a different way: retell the text or teaching "
                   "as a very short simple story or an everyday comparison, then give its message in one "
                   "sentence.", modes=RETEACH_ONLY),
        LessonStep("theory", "Present each teaching, tradition or position from the notes: who holds it, what it "
                   "says, and on which texts or reasons it is based, in items.",
                   states=NOT_SECURE, when_items=_THEORY_ITEMS),
        LessonStep("argument", "State the central question and the answers the traditions or thinkers in the notes "
                   "give, each as items: label = the answer, text = the reason or source behind it."),
        LessonStep("perspectives", "Compare the traditions, denominations or worldviews side by side: what they "
                   "share, where they differ, and why, respectfully and without ranking them."),
        LessonStep("example", "Connect the topic to one situation from a student's life, showing what the "
                   "tradition says about it and what questions it raises for the student.", states=NOT_SECURE),
        LessonStep("example", "Give one simple everyday situation that shows the same question, and say how the "
                   "tradition would answer it.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the typical misunderstandings (reading a symbolic text literally, mixing up "
                   "traditions or terms, presenting an opinion as the teaching of a tradition) with the correct "
                   "understanding."),
        LessonStep("guided_exercise", "Give one task that first checks comprehension of the text or teaching and "
                   "then asks for an explanation or a justified position, with hints in items and a model answer "
                   "in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the question, the key terms, the teachings and the central message in a few "
                   "short lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "source", "work", "person", "position", "theory", "event", "example"),
    question_types=("multiple_choice", "text", "checkboxes", "text", "ordering", "text"),
    card_kinds=("term", "person", "work", "source", "event", "position", "theory"),
    rules=(
        "Be respectful of every religion and worldview; never proselytise.",
        "Keep apart what a tradition teaches and what the student thinks; never grade a belief.",
        "Present a tradition in its own terms and sources.",
        "Label every interpretation the notes do not give as an additional interpretation.",
        "Ask for reasons, not memorised facts.",
    ),
    feedback_rule="Point to the passage, term or teaching that was misread and explain it correctly; then ask the "
                  "student to justify the answer with the text, never to change a personal belief.",
    exam_style="Explaining a text or tradition in its context, comparing positions, and taking a reasoned stand "
               "that separates the tradition's teaching from one's own view ('Erläutere…', 'Vergleiche…', "
               "'Nimm Stellung…').",
    question_stages=_TEXT_STAGES,
)

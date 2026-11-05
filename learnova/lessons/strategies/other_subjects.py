"""Computer Science, Technology, Music, Art, Physical Education and the general fallback.

Each keeps the shared frame (objective first, something taught before any exercise,
mistakes, a guided exercise, a summary) around the method of its subject: tracing code
like a worked maths example, explaining a device by its function and safety rules,
linking musical terms to how they sound, describing a work of art before interpreting
it, and breaking a movement into phases with its typical technique errors. `general`
serves "Other" and any subject Learnova does not know.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

COMPUTER_SCIENCE = SubjectStrategy(
    key="computer_science",
    name="Computer Science",
    priorities=(
        "understanding the concept behind the code",
        "algorithms as precise steps",
        "tracing code step by step",
        "edge cases",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which concept, algorithm or programming skill this "
                   "lesson is about and what the student will be able to do.", modes=ALL_MODES),
        LessonStep("terms", "List the technical terms from the notes as items: label = the term exactly as in the "
                   "notes, text = a simple definition and a short example; an example the notes do not give is "
                   "marked as an additional example.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the concept: what problem it solves, how it works in principle, and how "
                   "it connects to what the student already knows.", states=NOT_SECURE),
        LessonStep("explanation", "Explain it again with an everyday analogy or by acting out the algorithm on a "
                   "tiny concrete example, then link each step back to the code.", modes=RETEACH_ONLY),
        LessonStep("process", "Describe the algorithm or process step by step in items (input, each step, "
                   "output), in plain language or short pseudocode."),
        LessonStep("worked_example", "Work through one example step by step: show short code or pseudocode and "
                   "trace it in items (label = step or line, text = the values of the variables), then the result."),
        LessonStep("example", "Give one tiny example with as few steps as possible and trace it completely.",
                   modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the typical mistakes and misconceptions for this topic (in code for example "
                   "off-by-one errors, wrong conditions, unhandled edge cases) with the correct version."),
        LessonStep("guided_exercise", "Give one task (complete or correct a short program, trace an algorithm, or "
                   "apply the concept) with hints in items and the solution in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the concept, the steps of the algorithm and the edge cases to remember in a "
                   "few short lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "method", "process", "rule", "formula", "example", "diagram"),
    question_types=("multiple_choice", "ordering", "text", "dropdown", "text", "checkboxes"),
    card_kinds=("term", "method", "process", "rule", "formula"),
    rules=(
        "Trace code step by step like a worked maths example.",
        "Use small concrete inputs before the general case.",
        "Name the edge cases.",
        "Never just reveal a solution; guide with hints.",
    ),
    feedback_rule="Find the first step or line where the student's trace or code goes wrong, explain why, and let "
                  "the student trace that step again.",
    exam_style="Tracing algorithms, completing or correcting code, explaining concepts, and modelling data or "
               "processes.",
)


TECHNOLOGY = SubjectStrategy(
    key="technology",
    name="Technology",
    priorities=(
        "the function of each part",
        "working and manufacturing processes in order",
        "reading technical drawings and diagrams",
        "safety",
        "applications",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which device, system or technical process this lesson "
                   "is about and what the student will be able to explain or make.", modes=ALL_MODES),
        LessonStep("terms", "List the technical terms, components and materials from the notes as items: label = "
                   "the term exactly as in the notes, text = what it is and its function.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the function: what the device or system does, which components it has, "
                   "and how they work together.", states=NOT_SECURE),
        LessonStep("explanation", "Explain the function again with a simple everyday comparison or by following one "
                   "input through the system step by step.", modes=RETEACH_ONLY),
        LessonStep("process", "Explain the working or manufacturing process step by step in items, in the correct "
                   "order."),
        LessonStep("diagram", "Explain how to read the technical drawing, circuit diagram or flow chart from the "
                   "notes: its symbols, its parts, and what it shows.", when_items=("diagram",)),
        LessonStep("rule", "State the safety rules and standards that apply, each with the reason behind it."),
        LessonStep("example", "Show an application: where this is used in everyday life or industry and why this "
                   "solution was chosen."),
        LessonStep("example", "Give one simple, familiar device that works on the same principle.",
                   modes=RETEACH_ONLY),
        LessonStep("mistakes", "List typical mistakes (in assembling, measuring, wiring or using tools) and how to "
                   "avoid them."),
        LessonStep("guided_exercise", "Give one task (plan a work step, read a drawing, or choose a material or "
                   "component with a reason) with hints in items and the solution in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the function, the process steps and the safety rules in a few short lines.",
                   modes=ALL_MODES),
    ),
    extraction_focus=("term", "process", "diagram", "rule", "method", "example", "experiment"),
    question_types=("multiple_choice", "ordering", "checkboxes", "text", "dropdown", "text"),
    card_kinds=("term", "process", "method", "diagram", "rule"),
    rules=(
        "Explain the function before construction details.",
        "Give every safety rule with its reason.",
        "Use the correct technical terms and symbols.",
        "Link each part to the job it does.",
    ),
    feedback_rule="Point to the component, step or symbol that was mixed up, explain its function, and let the "
                  "student try again.",
    exam_style="Explaining how a device works, reading and completing technical drawings or circuit diagrams, "
               "planning work steps, and justifying the choice of a material or design.",
)


MUSIC = SubjectStrategy(
    key="music",
    name="Music",
    priorities=(
        "terms linked to how they sound",
        "reading notation",
        "hearing and describing structure",
        "placing works in context",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which musical concept, piece or skill this lesson is "
                   "about and what the student will be able to recognise or do.", modes=ALL_MODES),
        LessonStep("terms", "List the musical terms and notation from the notes as items: label = the term or symbol "
                   "exactly as in the notes, text = what it means and how it sounds or is written.", modes=ALL_MODES),
        LessonStep("explanation", "Explain it again through sound and movement: describe how it sounds, how to clap "
                   "or sing it, or compare it with something familiar.", modes=RETEACH_ONLY),
        LessonStep("example", "Connect the concept to the piece or listening example from the notes (or a "
                   "well-known one, marked as an addition): what to listen for and where it happens."),
        LessonStep("example", "Give one very simple, familiar song or pattern where the concept is easy to hear.",
                   modes=RETEACH_ONLY),
        LessonStep("structure", "Explain the structure or form (sections, repetitions, themes, rhythmic or "
                   "harmonic patterns) as items in order."),
        LessonStep("mistakes", "List typical confusions (similar terms, note values, intervals or forms) and how to "
                   "tell them apart."),
        LessonStep("guided_exercise", "Give one task (find a term in notation, complete a rhythm, or describe what "
                   "a passage does) with hints in items and the solution in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the terms, the structure and what to listen for in a few short lines.",
                   modes=ALL_MODES),
    ),
    extraction_focus=("term", "work", "person", "rule", "example", "method"),
    question_types=("multiple_choice", "dropdown", "checkboxes", "text", "ordering", "text"),
    card_kinds=("term", "work", "person", "rule"),
    rules=(
        "Link every term to how it sounds.",
        "Read notation step by step.",
        "Mark listening examples that are not in the notes as additions.",
    ),
    feedback_rule="Point to the symbol, term or passage that was misread, explain it by how it sounds, and let the "
                  "student try again.",
    exam_style="Recognising terms in notation, analysing the form of a piece, describing musical effects, and "
               "placing works in their period.",
)


ART = SubjectStrategy(
    key="art",
    name="Art",
    priorities=(
        "precise art terms and techniques",
        "describing before interpreting",
        "interpretations backed by what is visible",
        "trying techniques in one's own work",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which technique, work or artistic idea this lesson is "
                   "about and what the student will be able to describe, analyse or make.", modes=ALL_MODES),
        LessonStep("terms", "List the art terms and techniques from the notes as items: label = the term exactly as "
                   "in the notes, text = what it means and how it is done or seen in a work.", modes=ALL_MODES),
        LessonStep("explanation", "Explain it again by looking at one detail at a time and answering simple "
                   "questions (what do you see? where? what effect does it have?).", modes=RETEACH_ONLY),
        LessonStep("model_analysis", "Write a short model analysis of a work from the notes in steps: description "
                   "(what is shown), analysis (composition, colour, form, technique), interpretation (effect and "
                   "meaning)."),
        LessonStep("example", "Show how the technique or idea appears in another work or how the student can try "
                   "it in their own work."),
        LessonStep("example", "Give one simple everyday image or object where the technique or effect is easy to "
                   "see.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "List typical mistakes (interpreting before describing, vague words instead of art "
                   "terms, ignoring the technique) with the correct approach."),
        LessonStep("guided_exercise", "Give one task to describe and analyse a detail of a work or to plan a short "
                   "practical exercise, with hints in items and a model answer in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the terms, the technique and the steps of a work analysis in a few short "
                   "lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "work", "person", "method", "example"),
    question_types=("multiple_choice", "text", "checkboxes", "text", "dropdown", "text"),
    card_kinds=("term", "work", "person", "method"),
    rules=(
        "Describe before interpreting.",
        "Use precise art terms.",
        "Support every interpretation with something visible in the work.",
        "Mark works that are not in the notes as additions.",
    ),
    feedback_rule="Point to the visible detail or term that was missed or misused, and ask the student to support "
                  "the reading with what can be seen.",
    exam_style="Describing, analysing and interpreting works of art, comparing works, and explaining techniques.",
)


PHYSICAL_EDUCATION = SubjectStrategy(
    key="physical_education",
    name="Physical Education",
    priorities=(
        "safety",
        "the key points of a technique",
        "movement phases in order",
        "recognising and correcting errors",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which technique, rule or training topic this lesson is "
                   "about and what the student will be able to do or explain.", modes=ALL_MODES),
        LessonStep("terms", "List the sport terms from the notes as items: label = the term exactly as in the notes, "
                   "text = a simple explanation.", modes=ALL_MODES),
        LessonStep("rule", "Explain the rules or the key points of the technique, each with the reason it matters "
                   "(safety, fairness or efficiency)."),
        LessonStep("explanation", "Explain the movement again with a simple comparison and one focus point at a "
                   "time.", modes=RETEACH_ONLY),
        LessonStep("process", "Describe the movement or tactic step by step in items (preparation, main phase, "
                   "finish), with what to focus on in each phase."),
        LessonStep("example", "Give one simple preparatory exercise that trains only the key point.",
                   modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the common technique errors, how to recognise each one, and a correction tip."),
        LessonStep("guided_exercise", "Give one practice task with clear steps and what to watch for, with hints in "
                   "items and the success criteria in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the key points, the phases and the most common error in a few short lines.",
                   modes=ALL_MODES),
    ),
    extraction_focus=("term", "rule", "method", "process"),
    question_types=("multiple_choice", "ordering", "checkboxes", "text", "dropdown", "text"),
    card_kinds=("term", "rule", "method", "process"),
    rules=(
        "Safety first.",
        "One focus point per practice step.",
        "Explain why a technique works (balance, force, timing).",
    ),
    feedback_rule="Name the phase or rule that was mixed up, explain the correct key point and why it matters, and "
                  "let the student try again.",
    exam_style="Explaining rules and techniques, putting movement phases in order, recognising and correcting "
               "errors, and theory questions on training and health.",
)


GENERAL = SubjectStrategy(
    key="general",
    name="General",
    priorities=(
        "understanding the core idea",
        "precise terms",
        "examples from the notes",
        "applying the idea",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences what this topic is about and what the student will be "
                   "able to do afterwards.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the core idea of the topic clearly and simply, building on what the "
                   "student already knows.", states=NOT_SECURE),
        LessonStep("explanation", "Explain the core idea again in a different way: a comparison, a picture in words, "
                   "or smaller steps.", modes=RETEACH_ONLY),
        LessonStep("terms", "List the important terms from the notes as items: label = the term exactly as in the "
                   "notes, text = a simple definition.", modes=ALL_MODES),
        LessonStep("example", "Give a concrete example from the notes (or a clearly marked additional one) that "
                   "shows the idea."),
        LessonStep("example", "Give one simpler example than before.", modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the typical mistakes or misunderstandings with the correct idea."),
        LessonStep("guided_exercise", "Give one guided task that applies the idea, with hints in items and the "
                   "solution in answer.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the key points in a few short lines.", modes=ALL_MODES),
    ),
    extraction_focus=("term", "rule", "example", "person", "event", "theory", "formula", "process", "method"),
    question_types=("multiple_choice", "checkboxes", "text", "dropdown", "ordering", "text"),
    card_kinds=("term", "theory", "formula", "rule", "process", "method", "person", "event"),
    rules=(
        "Teach first, then test.",
        "Build on what the student already knows.",
        "Use the student's notes first; mark additions.",
    ),
    feedback_rule="Explain what was misunderstood, show the idea again with a simpler example, and let the student "
                  "try again.",
    exam_style="Explaining terms and ideas, applying them to examples, and short justified answers.",
)

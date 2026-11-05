"""Natural sciences: Biology and Chemistry (`sciences`) and Physics (`physics`).

Science lessons name the Fachbegriffe first, explain the principle as cause and effect,
and treat an experiment the way the exam does: question, hypothesis, setup, procedure,
observation, result, explanation, with the observation kept strictly apart from its
interpretation. Experiment, process, diagram and calculation parts appear only when the
topic map has such an item, so a lesson never invents an experiment the notes lack.
Physics keeps the same shape with quantities, symbols and units stressed throughout.
A student who is already secure skips the long principle explanation; the experiment,
process or calculation parts (or, without them, the explanation as a short recap) still
come before the exercise.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

_OBJECTIVE = LessonStep("objective", "Say in one or two sentences which phenomenon or principle the student will "
                        "be able to explain after this lesson.", modes=ALL_MODES)
_EXPERIMENT = LessonStep("experiment", "Describe the experiment from the notes in items in this order: question, "
                         "hypothesis, materials and setup, procedure, observation, result, scientific explanation; "
                         "keep the observation strictly separate from its interpretation.",
                         when_items=("experiment",))
_PROCESS = LessonStep("process", "Explain the process step by step in items in the correct order, saying what "
                      "changes at each step and why.", when_items=("process",))
_DIAGRAM = LessonStep("diagram", "Explain how to read the diagram or model from the notes: what each part, axis or "
                      "symbol shows, then what the diagram as a whole tells you.", when_items=("diagram",))
_RETEACH_EXAMPLE = LessonStep("example", "Give one simple everyday example where the principle can be seen and "
                              "explain it in three short steps.", modes=RETEACH_ONLY)
_GUIDED = LessonStep("guided_exercise", "Give one application question (explain a new everyday observation or "
                     "predict an experiment's result) with hints in items and a model answer in answer.",
                     modes=ALL_MODES)
_SUMMARY = LessonStep("summary", "Sum up the principle, the key terms and the cause-and-effect chain in a few "
                      "short lines.", modes=ALL_MODES)

SCIENCES = SubjectStrategy(
    key="sciences",
    name="Natural Sciences",
    priorities=(
        "precise Fachbegriffe",
        "explaining phenomena by cause and effect",
        "separating observation from interpretation",
        "applying the principle to new situations",
    ),
    steps=(
        _OBJECTIVE,
        LessonStep("terms", "List every important Fachbegriff (technical term) of this topic from the notes as "
                   "items: label = the term exactly as in the notes, text = a simple definition.", modes=ALL_MODES),
        LessonStep("explanation", "Explain the scientific principle as cause and effect: what happens, why it "
                   "happens, and which conditions change the result.", states=NOT_SECURE),
        LessonStep("explanation", "Explain the principle again with a different representation (a simple model, an "
                   "everyday comparison or a cause → effect chain) and say where the comparison stops fitting.",
                   modes=RETEACH_ONLY),
        _EXPERIMENT,
        _PROCESS,
        _DIAGRAM,
        LessonStep("worked_example", "Solve one calculation with the formula step by step in items: given values, "
                   "formula, substitution, result with unit, and a sentence that answers the question.",
                   when_items=("formula",)),
        _RETEACH_EXAMPLE,
        LessonStep("mistakes", "List the typical misconceptions (everyday ideas that contradict the science, "
                   "confused terms, observation mixed up with explanation), each with the correct idea."),
        _GUIDED,
        _SUMMARY,
    ),
    extraction_focus=("term", "experiment", "process", "formula", "diagram", "theory", "example"),
    question_types=("multiple_choice", "checkboxes", "text", "ordering", "dropdown", "text"),
    card_kinds=("term", "formula", "experiment", "process", "theory", "diagram"),
    # Order matters: NwT keeps only the first three when it is combined with technology
    # (six rules at most), and the grounding rule must be among them.
    rules=(
        "Use the exact Fachbegriffe from the notes and define each one simply.",
        "Keep observation and interpretation strictly apart.",
        "Describe experiments only as the notes give them; mark anything added.",
        "Explain with cause and effect, never with purpose ('so that') alone.",
    ),
    feedback_rule="Name the term or causal link that was wrong, explain the correct cause and effect, and ask the "
                  "student to explain the observation again.",
    exam_style="Explaining phenomena with Fachbegriffe, planning and evaluating experiments (hypothesis, "
               "observation, interpretation), describing processes, and reading diagrams.",
)


PHYSICS = SubjectStrategy(
    key="physics",
    name="Physics",
    priorities=(
        "physical quantities with symbols and units",
        "explaining phenomena by cause and effect",
        "using formulas correctly and checking units",
        "separating observation from interpretation",
    ),
    steps=(
        _OBJECTIVE,
        LessonStep("terms", "List every Fachbegriff and physical quantity from the notes as items: label = the term "
                   "or quantity exactly as in the notes (with symbol and unit), text = a simple definition.",
                   modes=ALL_MODES),
        LessonStep("explanation", "Explain the physical principle as cause and effect and name the quantities "
                   "involved, how they depend on each other and in which units they are measured.",
                   states=NOT_SECURE),
        LessonStep("explanation", "Explain the principle again with a different representation (a sketch, a "
                   "simple model or an everyday comparison) and say where the comparison stops fitting.",
                   modes=RETEACH_ONLY),
        _EXPERIMENT,
        _PROCESS,
        _DIAGRAM,
        LessonStep("worked_example", "Solve one calculation step by step in items: given values with units, "
                   "formula, unit conversion, substitution with units, result with unit, answer sentence.",
                   when_items=("formula",)),
        _RETEACH_EXAMPLE,
        LessonStep("mistakes", "List the typical mistakes (wrong or missing units, confused quantities, formula "
                   "used outside its conditions, everyday ideas that contradict physics) with the correction."),
        _GUIDED,
        _SUMMARY,
    ),
    extraction_focus=("term", "formula", "experiment", "theory", "diagram", "process", "example", "rule"),
    question_types=("multiple_choice", "checkboxes", "text", "ordering", "dropdown", "text"),
    card_kinds=("term", "formula", "experiment", "theory", "process", "rule"),
    rules=(
        "Give every quantity with its symbol and unit, and carry units through every calculation.",
        "State when a formula is valid before using it.",
        "Explain with cause and effect and the exact Fachbegriffe.",
        "Keep observation and interpretation strictly apart.",
    ),
    feedback_rule="Name the quantity, unit or causal link that was wrong, explain the correct relation, and let "
                  "the student redo that step.",
    exam_style="Explaining phenomena with physical terms, calculations with formula, units and an answer "
               "sentence, planning and evaluating experiments, and reading graphs.",
)

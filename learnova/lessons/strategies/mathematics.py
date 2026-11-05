"""Mathematics: understand why the method works, then see it, then do it.

The meaning of a concept comes before its formula, a full worked example before the
student's own attempt, and a wrong answer is traced to its first wrong step instead of
being replaced by the solution.
"""

from __future__ import annotations

from .base import ALL_MODES, NOT_SECURE, RETEACH_ONLY, LessonStep, SubjectStrategy

MATHEMATICS = SubjectStrategy(
    key="mathematics",
    name="Mathematics",
    priorities=(
        "understanding why a method works",
        "the correct method and when it may be used",
        "complete step-by-step solutions",
        "efficient strategies",
        "difficulty that rises step by step",
    ),
    steps=(
        LessonStep("objective", "Say in one or two sentences which kind of problem the student will be able to "
                   "solve after this lesson.", modes=ALL_MODES),
        # The maths learn sequence has no terms part; a review still recalls the key terms
        # and formulas before the student practises.
        LessonStep("terms", "List the key terms and formulas of this topic from the notes as items: label = the "
                   "term or formula exactly as in the notes, text = what it means and when it is used.",
                   modes=frozenset({"review"})),
        LessonStep("why", "Show in one or two sentences where this is needed: a typical problem it solves or a "
                   "later topic that builds on it.", states=NOT_SECURE),
        LessonStep("explanation", "Explain what the concept means and why the method works, using a sketch, a "
                   "table or small numbers before the general formula.", states=NOT_SECURE),
        LessonStep("explanation", "Explain the idea again with a different representation than the formula (a "
                   "sketch, a number line, a table or an everyday quantity) and link it to the method step by "
                   "step.", modes=RETEACH_ONLY),
        LessonStep("rule", "State the formula or method precisely: what every symbol means and the conditions "
                   "under which it may be used (special cases included)."),
        LessonStep("worked_example", "Solve one typical problem step by step in items (label = what you do, text = "
                   "the calculation), then give the result and a quick check."),
        LessonStep("example", "Give a simpler example with small, easy numbers and show every single step.",
                   modes=RETEACH_ONLY),
        LessonStep("mistakes", "List the typical mistakes for this topic (sign errors, wrong order of steps, "
                   "forgotten conditions or special cases), each as a wrong line and the right line."),
        LessonStep("guided_exercise", "Give one similar problem with one hint per step in items and the full "
                   "step-by-step solution in answer; do not reveal the solution in the body.", modes=ALL_MODES),
        LessonStep("summary", "Sum up the method as a short checklist of steps plus the conditions to remember.",
                   modes=ALL_MODES),
    ),
    extraction_focus=("term", "formula", "rule", "method", "example", "theory"),
    question_types=("multiple_choice", "text", "text", "dropdown", "text", "ordering"),
    card_kinds=("term", "formula", "rule", "method", "theory"),
    rules=(
        "Never just reveal an answer; guide with hints and questions.",
        "Teach the efficient method where appropriate and say when a shortcut is allowed.",
        "Increase difficulty gradually: one new difficulty per task.",
        "Show every transformation as its own step.",
        "Check a result by substituting it or estimating.",
    ),
    feedback_rule="Find the first wrong step, explain why it is wrong, and show the idea again with a simpler "
                  "example before the next try.",
    exam_style="Calculation tasks with a complete solution path, word problems that must be turned into "
               "mathematics, statements to justify or disprove, and interpreting results and graphs.",
)

"""Exam autopilot: the proactive layer on top of Learnova's learning loop.

The loop itself already exists elsewhere - document processing and sections
(`learnova.projects`), the evidence-based knowledge model and diagnosis
(`learnova.diagnostics`), the knowledge-gated test (`learnova.quizzes.mastery_gate`),
spaced practice (`learnova.quizzes.adaptive`), the day-by-day schedule
(`learnova.study_planner`) and mock exams. What this package adds is the part a student
should never have to do themselves: read the Kompetenzraster, see what the notes do not
cover, decide what the next optimal action is, and say honestly what grade the evidence
points to. Everything here is pure; `app.py` wires it to the database and the routes.
"""

from .autopilot import Action, TopicState, next_action, phase, plan_summary
from .competencies import (
    COVERAGE_STATES,
    LEVELS,
    attach_sections,
    coverage_summary,
    normalize_competencies,
)
from .grade import GradeEstimate, TopicEvidence, estimate_grade, explain_change, grade_for_percent

__all__ = [
    "Action", "TopicState", "next_action", "phase", "plan_summary",
    "COVERAGE_STATES", "LEVELS", "attach_sections", "coverage_summary", "normalize_competencies",
    "GradeEstimate", "TopicEvidence", "estimate_grade", "explain_change", "grade_for_percent",
]

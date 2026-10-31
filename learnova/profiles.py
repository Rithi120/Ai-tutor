"""Learner-profile helpers: education grade validation, labels, and AI descriptors.

Grade is stored on the user as a short token: the strings "1".."13", or one of the
non-numeric levels below. An empty string means "not chosen yet" (existing users are
asked once after login; a chosen grade is never overwritten automatically)."""

from __future__ import annotations

NUMERIC_GRADES = tuple(str(number) for number in range(1, 14))  # "1" .. "13"
NON_NUMERIC_GRADES = ("university", "vocational", "other")
GRADE_CHOICES = NUMERIC_GRADES + NON_NUMERIC_GRADES
GRADE_SET = frozenset(GRADE_CHOICES)


def is_valid_grade(grade: str) -> bool:
    return grade in GRADE_SET


def normalize_grade(value: str | None) -> str:
    """Return a valid grade token, or "" when unset/invalid."""
    token = str(value or "").strip().casefold()
    return token if token in GRADE_SET else ""


# English source label for each grade token. These strings are translatable via the
# catalogue (English source string as the key), so labels localise with the interface.
GRADE_LABELS_EN = {number: f"Grade {number}" for number in NUMERIC_GRADES}
GRADE_LABELS_EN.update({
    "university": "University",
    "vocational": "Vocational training",
    "other": "Other",
})


def grade_label(grade: str) -> str:
    """English source label for a grade token ("Not set" when unset). Wrap in _()/translate() to localise."""
    return GRADE_LABELS_EN.get(normalize_grade(grade), "Not set")


def grade_descriptor(grade: str | None) -> str:
    """Neutral English descriptor injected into AI prompts to calibrate depth/difficulty.

    Always returns guidance: an unknown level gets a calibration fallback, because
    injecting nothing makes models default to adult-level explanations."""
    grade = normalize_grade(grade)
    if grade in NUMERIC_GRADES:
        number = int(grade)
        approx_age = number + 5  # rough school-year to age mapping
        return (
            f"The learner is in grade {number} (approximately {approx_age} years old). "
            f"Use words, sentence length and everyday examples that fit a {approx_age}-year-old's "
            "world (school, friends, phone, sports, family), and define any term above that level "
            "the first time it appears. The grade sets the curriculum range; how deep to go on a "
            "topic follows the EXPLANATION LEVEL when one is given (a gap in an earlier topic is "
            "taught at its own level, an older student's mastery is not held back), otherwise the "
            "grade. When you name the student's level, write it in the response language - for "
            f"German say 'Klasse {number}', never 'grade {number}'."
        )
    if grade == "university":
        return (
            "The learner is a university/higher-education student. Use rigorous, "
            "in-depth explanations and advanced difficulty; technical vocabulary needs no softening."
        )
    if grade == "vocational":
        return (
            "The learner is in vocational training. Prefer practical, applied examples from work "
            "and daily life, and clear, concrete explanations over theory."
        )
    # "other" or unset: calibrate instead of guessing high.
    return (
        "The learner's level is not set. Start simple - short sentences, concrete everyday "
        "examples - and calibrate upward from the student's own messages (their vocabulary, "
        "spelling, and the questions they ask). Raise the level as soon as the student shows "
        "it; never condescend."
    )

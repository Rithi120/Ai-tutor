"""The single source of truth for diagnostic vocabulary.

Every enum here is extensible: add the tag to its tuple and a label to each label map.
The schema, the prompts, the planner and the UI all read from this module, so a new tag
never needs a change in more than one place.

Labels are English source strings so `learnova.translations.translate` localises them
exactly like every other interface string; GERMAN_LABELS documents the reference German
wording that the catalogue must carry.
"""

from __future__ import annotations


# How the response as a whole is judged. `insufficient_evidence` is a first-class
# outcome, not an error: it means the material does not support any verdict yet.
CORRECTNESS_STATUSES = (
    "correct",
    "partially_correct",
    "incorrect",
    "insufficient_evidence",
)

# The diagnosis taxonomy. Deliberately contains no "careless error": a one-off slip is
# `arithmetic_or_transcription_error`, which the planner refuses to treat as a reason to
# lower difficulty. `question_or_key_flawed` is the project's extension tag for a
# defective question or answer key, so the student is never blamed for a bad item.
DIAGNOSIS_TAGS = (
    "correct",
    "partially_correct",
    "conceptual_misconception",
    "procedural_error",
    "prerequisite_gap",
    "interpretation_error",
    "arithmetic_or_transcription_error",
    "incomplete_reasoning",
    "guessing_or_uncertain",
    # The words were the obstacle - a term, the phrasing, or the language itself - rather
    # than the idea. Distinct from interpretation_error, where the question was read as a
    # different question; here it could not be read well enough to answer.
    "language_or_vocabulary",
    # The idea is there in the familiar form but did not carry to this situation: the
    # student can solve the textbook case and not the new one.
    "transfer_or_application",
    "insufficient_evidence",
    "question_or_key_flawed",
)
DIAGNOSIS_TAG_SET = frozenset(DIAGNOSIS_TAGS)

# Tags that describe a genuine gap in understanding rather than execution. Only these
# justify reducing difficulty or recording a prerequisite gap.
UNDERSTANDING_TAGS = frozenset({
    "conceptual_misconception",
    "prerequisite_gap",
})

# Tags that must never be used to reduce difficulty on their own.
EXECUTION_TAGS = frozenset({
    "arithmetic_or_transcription_error",
    "incomplete_reasoning",
    "procedural_error",
})

# Tags that mean the response itself carries no usable signal about the student.
NON_EVIDENTIAL_TAGS = frozenset({
    "insufficient_evidence",
    "question_or_key_flawed",
})

# Where a piece of evidence may come from. The model may only quote these sources.
EVIDENCE_SOURCES = (
    "question",
    "rubric",
    "expected_answer",
    "student_answer",
    "work_step",
    "prior_attempt",
    "ocr",
    "source_material",
)
EVIDENCE_SOURCE_SET = frozenset(EVIDENCE_SOURCES)

# The teaching move recommended for this diagnosis.
INTERVENTIONS = (
    "confirm_and_extend",
    "explain_correction",
    "worked_example",
    "contrast_misconception",
    "reteach_prerequisite",
    "practice_similar",
    "retrieval_practice",
    "clarify_question",
    "request_more_work",
    "human_review",
)
INTERVENTION_SET = frozenset(INTERVENTIONS)

# The next learning action the planner may choose. This is the complete policy space.
NEXT_ACTIONS = (
    "clarify_instruction",
    "diagnostic_check",
    "prerequisite_reteach",
    "targeted_practice",
    "worked_example_then_practice",
    "misconception_contrast",
    "spaced_retrieval",
    "transfer_question",
    "increase_difficulty",
    "maintain_difficulty",
    "reduce_difficulty",
    "human_review_or_insufficient_evidence",
)
NEXT_ACTION_SET = frozenset(NEXT_ACTIONS)

# Actions that change how hard the next question is, used by the oscillation guard.
DIFFICULTY_ACTIONS = frozenset({
    "increase_difficulty",
    "reduce_difficulty",
})

COGNITIVE_DEMANDS = ("recall", "apply", "analyze", "evaluate", "transfer")
COGNITIVE_DEMAND_SET = frozenset(COGNITIVE_DEMANDS)

VALIDATION_STATUSES = (
    "validated",            # schema valid and deterministic checks agreed
    "repaired",             # normalization had to drop or downgrade unsupported claims
    "deterministic_conflict",  # a deterministic check contradicted the model
    "unverified",           # no deterministic check was possible
    "rejected",             # unusable; a safe fallback result was substituted
)
VALIDATION_STATUS_SET = frozenset(VALIDATION_STATUSES)


# --------------------------------------------------------------------------- labels

# English source strings. Wrap in translate()/_() to localise, exactly like grade labels.
DIAGNOSIS_LABELS_EN = {
    "correct": "Correct",
    "partially_correct": "Partly correct",
    "conceptual_misconception": "Misunderstood concept",
    "procedural_error": "Procedure applied incorrectly",
    "prerequisite_gap": "Missing earlier skill",
    "interpretation_error": "Question read differently",
    "arithmetic_or_transcription_error": "Calculation or copying slip",
    "incomplete_reasoning": "Reasoning left unfinished",
    "guessing_or_uncertain": "Answer looks uncertain",
    "language_or_vocabulary": "Wording or vocabulary got in the way",
    "transfer_or_application": "Idea known, not yet applied to a new case",
    "insufficient_evidence": "Not enough to judge yet",
    "question_or_key_flawed": "Problem with the question",
}

NEXT_ACTION_LABELS_EN = {
    "clarify_instruction": "Re-read what the question asks",
    "diagnostic_check": "Answer one short check question",
    "prerequisite_reteach": "Revisit the earlier skill first",
    "targeted_practice": "Practise this exact step",
    "worked_example_then_practice": "Study a worked example, then practise",
    "misconception_contrast": "Compare the two competing ideas",
    "spaced_retrieval": "Recall this again after a break",
    "transfer_question": "Apply this in a new situation",
    "increase_difficulty": "Move on to a harder question",
    "maintain_difficulty": "Stay at this level",
    "reduce_difficulty": "Step back to an easier question",
    "human_review_or_insufficient_evidence": "Ask a teacher to look at this",
}

INTERVENTION_LABELS_EN = {
    "confirm_and_extend": "Confirm and extend",
    "explain_correction": "Explain the correction",
    "worked_example": "Show a worked example",
    "contrast_misconception": "Contrast with the correct idea",
    "reteach_prerequisite": "Reteach the prerequisite",
    "practice_similar": "Practise a similar task",
    "retrieval_practice": "Retrieval practice",
    "clarify_question": "Clarify the question",
    "request_more_work": "Ask for the working steps",
    "human_review": "Human review",
}

# Reference German wording. `learnova.translations.catalog` carries these as catalogue
# entries; keeping them here documents the intended translation next to the tag.
GERMAN_LABELS = {
    "Correct": "Richtig",
    "Partly correct": "Teilweise richtig",
    "Misunderstood concept": "Konzept missverstanden",
    "Procedure applied incorrectly": "Verfahren falsch angewendet",
    "Missing earlier skill": "Fehlende Vorkenntnis",
    "Question read differently": "Frage anders gelesen",
    "Calculation or copying slip": "Rechen- oder Übertragungsfehler",
    "Reasoning left unfinished": "Begründung unvollständig",
    "Answer looks uncertain": "Antwort wirkt unsicher",
    "Wording or vocabulary got in the way": "Sprache oder Fachwörter waren das Hindernis",
    "Idea known, not yet applied to a new case": "Idee gewusst, aber noch nicht auf den neuen Fall angewendet",
    "Not enough to judge yet": "Noch nicht beurteilbar",
    "Problem with the question": "Problem mit der Frage",
    "Re-read what the question asks": "Lies noch einmal, was gefragt ist",
    "Answer one short check question": "Beantworte eine kurze Prüffrage",
    "Revisit the earlier skill first": "Wiederhole zuerst die Vorkenntnis",
    "Practise this exact step": "Übe genau diesen Schritt",
    "Study a worked example, then practise": "Lies ein Beispiel, dann übe",
    "Compare the two competing ideas": "Vergleiche die beiden Vorstellungen",
    "Recall this again after a break": "Rufe es nach einer Pause erneut ab",
    "Apply this in a new situation": "Wende es in einer neuen Situation an",
    "Move on to a harder question": "Weiter mit einer schwereren Frage",
    "Stay at this level": "Bleib auf diesem Niveau",
    "Step back to an easier question": "Zurück zu einer leichteren Frage",
    "Ask a teacher to look at this": "Lass das eine Lehrkraft ansehen",
}


def diagnosis_label(tag: str) -> str:
    """English source label for a diagnosis tag; wrap in translate() to localise."""

    return DIAGNOSIS_LABELS_EN.get(tag, DIAGNOSIS_LABELS_EN["insufficient_evidence"])


def next_action_label(action: str) -> str:
    """English source label for a planner action; wrap in translate() to localise."""

    return NEXT_ACTION_LABELS_EN.get(action, NEXT_ACTION_LABELS_EN["maintain_difficulty"])


# Every translatable string this module can emit, so a catalogue test can assert coverage.
TRANSLATABLE_LABELS = frozenset(
    tuple(DIAGNOSIS_LABELS_EN.values())
    + tuple(NEXT_ACTION_LABELS_EN.values())
)


# Mapping from the legacy `learnova.analysis` categories to the new taxonomy, so saved
# attempts recorded before diagnosis:v2 still group and display correctly.
LEGACY_CATEGORY_TO_TAG = {
    "conceptual_misunderstanding": "conceptual_misconception",
    "missing_prerequisite": "prerequisite_gap",
    "procedural_mistake": "procedural_error",
    "calculation_mistake": "arithmetic_or_transcription_error",
    "sign_error": "arithmetic_or_transcription_error",
    "unit_error": "procedural_error",
    "formula_selection_error": "procedural_error",
    "reading_comprehension_error": "interpretation_error",
    "language_error": "interpretation_error",
    "memory_failure": "prerequisite_gap",
    "incomplete_answer": "incomplete_reasoning",
    "correct_with_incomplete_explanation": "incomplete_reasoning",
    "correct_alternative_solution": "correct",
    "typographical_error": "arithmetic_or_transcription_error",
    "guessing": "guessing_or_uncertain",
    "careless_mistake": "arithmetic_or_transcription_error",
    "ambiguous_question": "question_or_key_flawed",
    "ai_generated_question_error": "question_or_key_flawed",
}

# The reverse direction, used to keep the legacy `Attempt.mistake_categories` column and
# the existing Mistake Intelligence page populated from a diagnosis:v2 result.
TAG_TO_LEGACY_CATEGORY = {
    "conceptual_misconception": "conceptual_misunderstanding",
    "procedural_error": "procedural_mistake",
    "prerequisite_gap": "missing_prerequisite",
    "interpretation_error": "reading_comprehension_error",
    "arithmetic_or_transcription_error": "calculation_mistake",
    "incomplete_reasoning": "incomplete_answer",
    "guessing_or_uncertain": "guessing",
    "question_or_key_flawed": "ambiguous_question",
}

# The legacy verdict vocabulary (`learnova.analysis.VERDICTS`) this status maps onto.
STATUS_TO_LEGACY_VERDICT = {
    "correct": "correct",
    "partially_correct": "partially_correct",
    "incorrect": "incorrect",
    "insufficient_evidence": "ambiguous",
}

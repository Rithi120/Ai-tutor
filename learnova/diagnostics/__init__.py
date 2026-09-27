"""Evidence-based mistake diagnosis, knowledge modelling and adaptive planning.

Flask-independent by design: every rule here is a pure function, so the policy can be
unit-tested without a request context, a database or a provider call. The app layer
(`app.py`) builds the evidence, calls the AI gateway with these prompts, then validates,
verifies, plans and persists through this package.
"""

from .knowledge import (
    HALF_LIFE_DAYS,
    PREREQUISITE_MIN_EVIDENCE,
    confirmed_prerequisites,
    confident_status,
    due_for_retrieval,
    evidence_summary,
    is_unassessed,
    mastery_reason,
    merge_prerequisite,
    prerequisite_chain,
    recurring_misconception_count,
    update_evidence,
)
from .planner import MAX_DIFFICULTY, MIN_DIFFICULTY, NextAction, plan_next_action, similarity
from .prompts import (
    build_evidence_bundle,
    diagnosis_system_prompt,
    diagnosis_user_prompt,
    question_system_prompt,
    question_user_prompt,
    verification_system_prompt,
    verification_user_prompt,
)
from .question_spec import (
    QuestionSpec,
    QuestionValidation,
    annotate_question,
    public_question,
    spec_from_constraints,
    validate_question,
)
from .schema import (
    DIAGNOSIS_VERSION,
    DiagnosisSchemaError,
    insufficient_evidence_diagnosis,
    normalize_diagnosis,
    student_view,
    to_legacy_analysis,
    validate_diagnosis,
)
from .taxonomy import (
    CORRECTNESS_STATUSES,
    DIAGNOSIS_TAGS,
    INTERVENTIONS,
    NEXT_ACTIONS,
    diagnosis_label,
    next_action_label,
)
from .verification import apply_second_opinion, check_answer, verify_diagnosis

__all__ = [
    "CORRECTNESS_STATUSES",
    "DIAGNOSIS_TAGS",
    "DIAGNOSIS_VERSION",
    "HALF_LIFE_DAYS",
    "INTERVENTIONS",
    "MAX_DIFFICULTY",
    "MIN_DIFFICULTY",
    "NEXT_ACTIONS",
    "PREREQUISITE_MIN_EVIDENCE",
    "DiagnosisSchemaError",
    "NextAction",
    "QuestionSpec",
    "QuestionValidation",
    "annotate_question",
    "apply_second_opinion",
    "build_evidence_bundle",
    "check_answer",
    "confident_status",
    "confirmed_prerequisites",
    "diagnosis_label",
    "diagnosis_system_prompt",
    "diagnosis_user_prompt",
    "due_for_retrieval",
    "evidence_summary",
    "insufficient_evidence_diagnosis",
    "is_unassessed",
    "mastery_reason",
    "merge_prerequisite",
    "next_action_label",
    "normalize_diagnosis",
    "plan_next_action",
    "prerequisite_chain",
    "public_question",
    "question_system_prompt",
    "question_user_prompt",
    "recurring_misconception_count",
    "similarity",
    "spec_from_constraints",
    "student_view",
    "to_legacy_analysis",
    "update_evidence",
    "validate_diagnosis",
    "validate_question",
    "verification_system_prompt",
    "verification_user_prompt",
    "verify_diagnosis",
]

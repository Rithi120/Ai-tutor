"""Prompts for diagnosis, second-opinion verification and spec-driven question generation.

Three rules shape all of them:

1. Never ask for hidden reasoning. The model returns findings, not deliberation.
2. Never let a claim stand without a quote. Evidence ids are part of the contract and
   `learnova.diagnostics.schema` drops any claim whose ids do not resolve.
3. Never invent. `insufficient_evidence` is an explicitly rewarded answer, so the model
   has somewhere honest to go when the response does not support a diagnosis.
"""

from __future__ import annotations

import json
from typing import Any

from .schema import DIAGNOSIS_VERSION
from .taxonomy import (
    COGNITIVE_DEMANDS,
    CORRECTNESS_STATUSES,
    DIAGNOSIS_TAGS,
    EVIDENCE_SOURCES,
    INTERVENTIONS,
)

DIAGNOSIS_SCHEMA_SUMMARY = (
    '{"analysis_version":"' + DIAGNOSIS_VERSION + '",'
    '"correctness_status":"correct|partially_correct|incorrect|insufficient_evidence",'
    '"score":{"points":num,"max_points":num,"rubric_evidence":[{"criterion":str,"met":bool,"evidence_ids":[str]}]},'
    '"concepts_assessed":[str],'
    '"primary_diagnosis":{"tag":enum,"statement":str,"evidence_ids":[str]},'
    '"secondary_diagnoses":[{"tag":enum,"statement":str,"evidence_ids":[str]}],'
    '"evidence":[{"id":str,"source":enum,"quote":str}],'
    '"misconception_description":str,'
    '"prerequisite_gaps":[{"concept":str,"evidence_ids":[str]}],'
    '"missing_evidence":bool,"missing_evidence_reason":str,'
    '"confidence":{"value":0.0..1.0,"basis":str},'
    '"recommended_intervention":enum,'
    '"candidate_question_constraints":{"concept":str,"prerequisite_focus":str,"difficulty":1..3,'
    '"cognitive_demand":enum,"question_type":str,"target_misconception":str,"purpose":str},'
    '"student_facing_explanation":str,"internal_diagnostic_summary":str}'
)

VERIFICATION_SCHEMA_SUMMARY = '{"agrees":bool,"reason":str,"better_tag":str}'

QUESTION_SCHEMA_SUMMARY = (
    '{"question":{"id":str,"subject":str,"concept":str,"prerequisites":[str],'
    '"difficulty":1..3,"cognitive_demand":enum,"type":str,"prompt":str,"hint":str,'
    '"options":[{"id":str,"label":str}],"expected_answer":value,"solution_steps":[str],'
    '"rubric":[{"criterion":str,"points":num}],"target_misconception":str,'
    '"diagnostic_purpose":str}}'
)


_SUBJECT_RULES = {
    "mathematics": (
        "Verify the chosen method, every substitution, each sign and each arithmetic step. Accept any "
        "mathematically equivalent form (1/2, 0.5, 50%) and any valid alternative method. A single "
        "arithmetic or sign slip is arithmetic_or_transcription_error, not a misconception."
    ),
    "physics": (
        "Check the formula, the substitutions, the signs and especially the units and dimensional "
        "consistency. A missing or wrong unit is a procedural_error unless the response shows the "
        "quantity itself was misunderstood. Accept equivalent numeric forms and reasonable rounding."
    ),
    "chemistry": (
        "Check balancing, states, charges, significant figures and units. A balancing slip is procedural; "
        "confusing two processes (oxidation with reduction) is conceptual. Accept equivalent notations."
    ),
    "biology": (
        "Accept synonyms and valid paraphrases of a mechanism. A missing detail is incomplete_reasoning; "
        "a wrong mechanism is conceptual_misconception."
    ),
    "english": (
        "Judge meaning, grammar, vocabulary and spelling separately. If the meaning is conveyed, a small "
        "slip is arithmetic_or_transcription_error, never a misconception."
    ),
    "german": (
        "Judge meaning, grammar, vocabulary and spelling separately. If the meaning is conveyed, a small "
        "slip is arithmetic_or_transcription_error, never a misconception."
    ),
    "history": (
        "Judge factual accuracy, use of evidence and argument separately. Accept defensible interpretations; "
        "only diagnose an error on a genuine factual or logical failure."
    ),
    "geography": (
        "Judge factual accuracy, evidence and reasoning separately; accept multiple valid formulations."
    ),
    "computer science": (
        "Accept any correct algorithm and equivalent code; judge logic and complexity, not exact syntax. "
        "An off-by-one is a procedural_error, a wrong algorithm is conceptual."
    ),
}


def subject_rule(subject: str) -> str:
    """Subject-specific grading guidance, with a neutral default for unlisted subjects."""

    key = str(subject or "").strip().casefold()
    return _SUBJECT_RULES.get(key, (
        "Judge accuracy and reasoning fairly, accept valid alternative answers and equivalent wording, "
        "and only name a cause you can quote evidence for."
    ))


def diagnosis_system_prompt(subject: str, language: str, grade: str = "") -> str:
    """The diagnostician's instructions. Returns findings only - never deliberation."""

    grade_line = f"The student is at this level: {grade}. Calibrate expectations to it.\n" if grade else ""
    return (
        "You are an educational diagnostician. Your job is not to grade: it is to say precisely what the "
        "student's response shows, what it does not show, and what would be the most useful next step.\n"
        "\n"
        "EVIDENCE IS MANDATORY. Build the `evidence` list first, by quoting short fragments of the material "
        f"you were given. Each quote has an id and a source from: {', '.join(EVIDENCE_SOURCES)}. "
        "Every diagnosis, every prerequisite gap and every rubric judgement must reference the ids of the "
        "quotes that support it. A claim with no quote will be discarded automatically, so do not make one.\n"
        "\n"
        "NEVER INVENT. Do not state what the student was thinking, intended or felt. Do not name a "
        "misconception, a prerequisite gap or a confidence value you cannot support with a quote. If the "
        "response is too short, too ambiguous, unreadable, or shows no working, that is a real and useful "
        f"answer: set correctness_status to insufficient_evidence, set missing_evidence true, explain what "
        "is missing, and propose a check question that would supply it.\n"
        "\n"
        "TAXONOMY. `primary_diagnosis.tag` and every `secondary_diagnoses[].tag` must come from: "
        f"{', '.join(DIAGNOSIS_TAGS)}. There is no 'careless' category: a one-off calculation or copying slip "
        "is arithmetic_or_transcription_error, and it is not a gap in understanding. Use "
        "conceptual_misconception only when the response shows a specific wrong idea you can quote, and then "
        "describe that idea in misconception_description. Use prerequisite_gap only when the failure is in an "
        "earlier skill, and name that skill. Give secondary diagnoses only when the evidence supports more "
        "than one cause.\n"
        "\n"
        "CHECK THE KEY. For any objectively decidable question, work out the answer yourself first. If your "
        "own result differs from the supplied expected answer, the key is wrong: set the tag to "
        "question_or_key_flawed, treat a response matching your result as correct, and never penalise the "
        "student. Do the same when the question omits information needed to answer it.\n"
        "\n"
        "BE FAIR. A brief but logically valid justification is fully correct. A correct answer reached by a "
        "different valid method is correct. Only downgrade a correct answer when the stated reasoning is "
        "clearly invalid, and then use incomplete_reasoning.\n"
        "\n"
        "CONFIDENCE is a calibrated estimate of how likely your primary diagnosis is to be right given only "
        "this evidence: near 0.9 when a quote settles it, near 0.5 when the response is consistent with two "
        "causes, below 0.4 when you are mostly inferring. Say what it rests on in `confidence.basis`.\n"
        "\n"
        f"{grade_line}"
        f"SUBJECT GUIDANCE. {subject_rule(subject)}\n"
        "\n"
        f"Write `student_facing_explanation` in {language}, addressed to the student, at most three "
        "sentences: what was right, the one thing that went wrong, and the single next step. No praise "
        "padding, no generic advice such as 'review the topic' or 'be more careful'.\n"
        "`internal_diagnostic_summary` is one or two sentences of findings for the teacher record. Do not "
        "write your reasoning process anywhere; report conclusions and the quotes behind them.\n"
        f"`recommended_intervention` is one of: {', '.join(INTERVENTIONS)}.\n"
        f"`candidate_question_constraints.cognitive_demand` is one of: {', '.join(COGNITIVE_DEMANDS)}.\n"
        f"`correctness_status` is one of: {', '.join(CORRECTNESS_STATUSES)}.\n"
        "\n"
        "Return ONLY one JSON object matching this schema exactly, with no markdown and no commentary:\n"
        f"{DIAGNOSIS_SCHEMA_SUMMARY}"
    )


def diagnosis_user_prompt(evidence: dict[str, Any]) -> str:
    """The evidence bundle handed to the diagnostician."""

    return (
        "Diagnose this response. Quote from the material below and return the JSON object.\n"
        + json.dumps(evidence, ensure_ascii=False, default=str)
    )


def verification_system_prompt(language: str) -> str:
    """The independent critic. It may only weaken a diagnosis, never extend it."""

    return (
        "You are an independent reviewer checking another assessor's diagnosis of a student response.\n"
        "You did not see the student work the first assessor saw beyond what is quoted below, so you may "
        "only agree or object. You must not propose a new misconception, a new prerequisite gap, or a "
        "higher confidence.\n"
        "Object when: the stated cause is not supported by the quoted evidence; the correctness judgement "
        "contradicts the expected answer; the response is too thin to support any cause; or a simple slip "
        "has been described as a conceptual failure.\n"
        "Agree when the quoted evidence plainly supports the stated cause.\n"
        f"Write `reason` in {language}, one sentence, no reasoning process.\n"
        "Return ONLY one JSON object: "
        f"{VERIFICATION_SCHEMA_SUMMARY}"
    )


def verification_user_prompt(bundle: dict[str, Any]) -> str:
    return (
        "Review this diagnosis against its evidence and return the JSON object.\n"
        + json.dumps(bundle, ensure_ascii=False, default=str)
    )


def question_system_prompt(subject: str, language: str, grade: str = "") -> str:
    """Instructions for generating one question that satisfies a specification."""

    grade_line = f"The student is at this level: {grade}. Match vocabulary and depth to it.\n" if grade else ""
    return (
        "You write one diagnostic practice question that satisfies an exact specification.\n"
        "The specification is not a suggestion: the concept, difficulty, cognitive demand and question type "
        "must be matched exactly, and the question must serve the stated diagnostic purpose.\n"
        "\n"
        "The question must stand alone. Never refer to 'the diagram above', 'the previous question', an "
        "attachment, or anything the student cannot see in the prompt itself.\n"
        "Never repeat or lightly reword a question in avoid_prompts.\n"
        "Solve your own question before answering: `expected_answer` must be the correct answer, and "
        "`solution_steps` must be the steps that reach it. The hint must help without revealing the answer.\n"
        "For a multiple choice or dropdown question give exactly four options with one correct answer; for "
        "checkboxes give four or five with two or three correct; for ordering give four shuffled items; for "
        "an open question return an empty options list.\n"
        "When a misconception is targeted, build the distractors so that the misconception leads to a "
        "specific wrong option, and say which one in `target_misconception`.\n"
        "`rubric` lists the criteria an answer is judged against, which is what makes partial credit "
        "possible for an open question.\n"
        f"{grade_line}"
        f"Write every student-facing string in {language}. Use only facts the supplied source material "
        "supports when source material is present.\n"
        "Return ONLY one JSON object matching this schema exactly, with no markdown and no commentary:\n"
        f"{QUESTION_SCHEMA_SUMMARY}"
    )


def question_user_prompt(spec: dict[str, Any], context: dict[str, Any]) -> str:
    """The specification plus the grounding context for one generated question."""

    return (
        "Write one question that satisfies this specification exactly.\n"
        "SPECIFICATION: " + json.dumps(spec, ensure_ascii=False, default=str) + "\n"
        "CONTEXT: " + json.dumps(context, ensure_ascii=False, default=str)
    )


def build_evidence_bundle(
    *,
    question: Any,
    expected_answer: Any,
    student_answer: Any,
    subject: str,
    concept: str = "",
    grade: str = "",
    language: str = "English",
    question_type: str = "",
    options: Any = None,
    rubric: Any = None,
    solution_steps: Any = None,
    work_steps: Any = None,
    learning_objectives: Any = None,
    previous_attempts: Any = None,
    knowledge_state: Any = None,
    hints_used: bool = False,
    time_seconds: float | None = None,
    answer_changes: int | None = None,
    response_confidence: float | None = None,
    ocr_confidence: float | None = None,
    source_context: str = "",
) -> dict[str, Any]:
    """Assemble stage A: everything the diagnostician is allowed to quote from.

    Only fields with content are included, so the token budget is spent on real evidence.
    No student identity, email or account data is ever part of this bundle.
    """

    bundle: dict[str, Any] = {
        "subject": subject,
        "concept": concept,
        "language": language,
        "question": question,
        "question_type": question_type,
        "expected_answer": expected_answer,
        "student_answer": student_answer,
        "hints_used": bool(hints_used),
    }
    if grade:
        bundle["grade"] = grade
    if options:
        bundle["options"] = options
    if rubric:
        bundle["rubric"] = rubric
    if solution_steps:
        bundle["solution_steps"] = solution_steps
    if work_steps:
        bundle["work_steps"] = work_steps
    if learning_objectives:
        bundle["learning_objectives"] = learning_objectives
    if previous_attempts:
        bundle["previous_attempts"] = previous_attempts
    if knowledge_state:
        bundle["knowledge_state"] = knowledge_state
    if time_seconds is not None:
        bundle["time_taken_seconds"] = round(float(time_seconds), 1)
    if answer_changes is not None:
        bundle["answer_changes"] = int(answer_changes)
    if response_confidence is not None:
        # How sure the student said they were, 0-100. A wrong answer given confidently
        # points at a misconception; a wrong answer given hesitantly points at guessing.
        # It is a signal, never a verdict: it still needs a quote to support a tag.
        bundle["student_reported_confidence"] = round(float(response_confidence), 1)
    if ocr_confidence is not None:
        bundle["ocr_confidence"] = round(float(ocr_confidence), 2)
        bundle["ocr_note"] = (
            "The response was read by OCR at this confidence. Treat an unreadable fragment as "
            "missing evidence, never as a student mistake."
        )
    if source_context:
        bundle["source_material"] = str(source_context)[:1800]
    return bundle

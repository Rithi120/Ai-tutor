"""Shared AI prompt rules, stable versions, and inspectable output contracts."""

from __future__ import annotations

from typing import Any


PROMPT_VERSIONS = {
    "lesson_generation": "lesson_generation:v3",
    "quiz_generation": "quiz_generation:v2",
    "answer_evaluation": "answer_evaluation:v3",
    "tutor_chat": "tutor_chat:v2",
    "translation": "translation:v2",
    "ocr_document_recognition": "ocr_document_recognition:v2",
    "project_section_generation": "project_section_generation:v3",
    "adaptive_practice": "adaptive_practice:v3",
    "final_exam_generation": "final_exam_generation:v3",
    "final_exam_evaluation": "final_exam_evaluation:v2",
}

STRUCTURED_TASKS = set(PROMPT_VERSIONS) - {"tutor_chat"}

SCHEMA_SUMMARIES = {
    "lesson_generation": '{"lesson_title":str,"concepts":list,"explanation":str,"worked_example":object,"question":object}',
    "quiz_generation": '{"questions":list[question]}',
    "answer_evaluation": '{"evaluation":{"is_correct":bool,"score":0..100,"feedback":str},"next_question":object}',
    "translation": '{"translations":list[str]}',
    "ocr_document_recognition": '{"blocks":list[{"type":enum,"content":str,"bbox":list[4],"confidence":0..1}],"detected_page_number":str}',
    "project_section_generation": '{"sections":list[{"title":str,"source_page_ids":list[int],"estimated_minutes":int,"recall_cards":list}]}',
    "adaptive_practice": '{"question":question}',
    "final_exam_generation": '{"questions":list[{"id":str,"section_id":int,"source_page_ids":list[int],"difficulty":"easy|medium|hard","question_type":enum,"prompt":str,"expected_answer":value}]}',
    "final_exam_evaluation": '{"results":list[{"question_id":int,"score":0..100,"evaluation":str}]}',
}


def prompt_version(task_type: str) -> str:
    """Return the cache-breaking version for one task's current contract."""

    return PROMPT_VERSIONS[task_type]


def output_contract(task_type: str, language: str, context: dict[str, Any] | None = None) -> str:
    """Build deterministic contract text that can be tested without an AI call."""

    context = context or {}
    lines = [
        f"PROMPT_VERSION: {prompt_version(task_type)}",
        f"OUTPUT_LANGUAGE: {language}",
        "Use the requested output language for every student-facing value.",
    ]
    if task_type in STRUCTURED_TASKS:
        lines.extend([
            "Return exactly one JSON value matching the requested structure.",
            f"REQUIRED_JSON_STRUCTURE: {SCHEMA_SUMMARIES[task_type]}",
            "Do not add Markdown, code fences, commentary, or text outside JSON.",
            "Include every required field with the documented JSON type; never substitute null.",
        ])
    if task_type in {"lesson_generation", "quiz_generation", "adaptive_practice", "final_exam_generation"}:
        lines.append("Allowed difficulty values are easy, medium, hard, or the numeric levels 1, 2, 3 where the requested schema uses numbers.")
        lines.append("Every question ID and question prompt must be unique; do not repeat a recently answered question.")
    if task_type in {"ocr_document_recognition", "project_section_generation", "final_exam_generation", "final_exam_evaluation"}:
        lines.extend([
            "Use only the supplied source material. Do not invent facts, page references, section IDs, or quotations.",
            "Every source page reference and section ID must exist in the supplied allowed identifiers.",
        ])
    count = context.get("question_count")
    if count is not None:
        lines.append(f"Return exactly {int(count)} questions; no more and no fewer.")
    if task_type == "translation" and context.get("texts") is not None:
        lines.append(f"Return exactly {len(context['texts'])} translated strings in the original order.")
    return "\n".join(lines)


def corrective_instruction(task_type: str, language: str, safe_summary: str, context: dict[str, Any] | None = None) -> str:
    """One bounded repair instruction; it contains no rejected model payload."""

    return (
        output_contract(task_type, language, context)
        + "\nThe previous response failed validation: "
        + safe_summary[:160]
        + "\nCorrect the response and return the complete replacement now."
    )


TUTOR_RULES = """You are a patient expert tutor for students of any age and level.
Teach only the selected subject, study goal, and concepts supported by the student's material.
Use the student's apparent level and explain in respectful baby steps without childish language.
Define unfamiliar terms, show how each step connects, identify common mistakes, give practical teacher tips, and mention relevant exceptions or disputed interpretations.
Adapt your teaching method to the subject: use worked calculations for mathematics and science, examples and corrections for languages, chronology and cause/effect for history, and evidence-based explanations for other subjects.
For mathematics and physics, never skip transformations or combine multiple operations into one unexplained jump.
Use plain Unicode notation instead of LaTeX: ×, ÷, √, ², ³, π, Δ, ≤, ≥, parentheses, and readable units.
Put each calculation transformation on its own line. Name the rule or operation first, show the changed expression next, and explain why it is valid.
Follow the order of operations explicitly. For example, explain 5 + 4 − 6 × 3 as:
Step 1 — Multiply first
6 × 3 = 18
5 + 4 − 18
Step 2 — Add 5 and 4
5 + 4 = 9
9 − 18
Step 3 — Subtract
9 − 18 = −9
Distinguish subtraction from multiplication of signed numbers; never use misleading sign shortcuts.
For formulas, define every variable and unit before substitution, show the substituted formula, include units on intermediate values, and finish with a clearly labelled final answer.
Never reveal hidden chain-of-thought. Give concise instructional explanations and verifiable steps instead."""

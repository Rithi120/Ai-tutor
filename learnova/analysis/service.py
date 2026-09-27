"""Student-answer mistake analysis: schema, validation, prompts, and aggregation.

This module is Flask-independent so it can be unit-tested and validated against real
Groq responses without a request context. The app layer (app.py) builds the evidence,
calls the AI gateway with these prompts, then validates/normalizes the result here.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

VERDICTS = ("correct", "partially_correct", "incorrect", "ambiguous")

# The distinct diagnostic categories the model may assign (evidence-based only).
MISTAKE_CATEGORIES = (
    "conceptual_misunderstanding",
    "missing_prerequisite",
    "procedural_mistake",
    "calculation_mistake",
    "sign_error",
    "unit_error",
    "formula_selection_error",
    "reading_comprehension_error",
    "language_error",
    "memory_failure",
    "incomplete_answer",
    "correct_with_incomplete_explanation",
    "correct_alternative_solution",
    "typographical_error",
    "guessing",
    "careless_mistake",
    "ambiguous_question",
    "ai_generated_question_error",
)
_CATEGORY_SET = frozenset(MISTAKE_CATEGORIES)
_DIFFICULTY_CHANGES = ("easier", "same", "harder")

# Every field of the required structured result, with its default.
ANALYSIS_FIELDS: dict[str, Any] = {
    "verdict": "incorrect",
    "score_fraction": 0.0,
    "confidence": 0.0,
    "question_intent": "",
    "student_approach": "",
    "correct_parts": [],
    "mistake_categories": [],
    "root_cause": "",
    "likely_student_thought": "",
    "exact_error_step": "",
    "correct_reasoning": [],
    "final_answer": "",
    "improvement_advice": "",
    "prerequisites_to_review": [],
    "next_question": {"question": "", "purpose": "", "difficulty_change": "same"},
    "should_create_mistake_record": False,
    "should_reduce_mastery": False,
    "analysis_limitations": [],
}

# Compact schema string advertised to the model (kept in sync with ANALYSIS_FIELDS).
SCHEMA_SUMMARY = (
    '{"verdict":"correct|partially_correct|incorrect|ambiguous","score_fraction":0.0..1.0,'
    '"confidence":0.0..1.0,"question_intent":str,"student_approach":str,"correct_parts":[str],'
    '"mistake_categories":[enum],"root_cause":str,"likely_student_thought":str,'
    '"exact_error_step":str,"correct_reasoning":[str],"final_answer":str,"improvement_advice":str,'
    '"prerequisites_to_review":[str],"next_question":{"question":str,"purpose":str,'
    '"difficulty_change":"easier|same|harder"},"should_create_mistake_record":bool,'
    '"should_reduce_mastery":bool,"analysis_limitations":[str]}'
)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _str(value: Any, limit: int = 4000) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _str_list(value: Any, limit: int = 12, item_limit: int = 600) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        text = _str(item, item_limit)
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


class AnalysisSchemaError(ValueError):
    """Raised when a model analysis cannot be coerced into the required schema."""


def validate_analysis(payload: Any) -> dict[str, Any]:
    """Strictly validate the model output, raising AnalysisSchemaError on hard failures.

    Hard failures (missing object, no verdict) trigger a corrective retry upstream;
    soft issues (extra/blank optional fields) are normalized rather than rejected.
    """
    if not isinstance(payload, dict):
        raise AnalysisSchemaError("analysis is not a JSON object")
    verdict = _str(payload.get("verdict")).lower()
    if verdict not in VERDICTS:
        raise AnalysisSchemaError(f"invalid verdict: {verdict!r}")
    categories = [c for c in _str_list(payload.get("mistake_categories"), 8, 60)
                  if c in _CATEGORY_SET]
    # A non-correct verdict with no recognized category and no root cause is unusable.
    if verdict in ("incorrect", "partially_correct") and not categories and not _str(payload.get("root_cause")):
        raise AnalysisSchemaError("incorrect verdict without a category or root cause")
    return normalize_analysis(payload)


def normalize_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    """Coerce a (possibly imperfect) analysis dict into the canonical, safe schema."""
    verdict = _str(payload.get("verdict")).lower()
    if verdict not in VERDICTS:
        verdict = "incorrect"
    score = max(0.0, min(1.0, _num(payload.get("score_fraction"),
                                   1.0 if verdict == "correct" else 0.0)))
    confidence = max(0.0, min(1.0, _num(payload.get("confidence"), 0.5)))
    categories = []
    for cat in _str_list(payload.get("mistake_categories"), 8, 60):
        cat = cat.strip().lower().replace(" ", "_")
        if cat in _CATEGORY_SET and cat not in categories:
            categories.append(cat)

    raw_next = payload.get("next_question")
    raw_next = raw_next if isinstance(raw_next, dict) else {}
    diff_change = _str(raw_next.get("difficulty_change")).lower()
    if diff_change not in _DIFFICULTY_CHANGES:
        diff_change = "same"
    next_question = {
        "question": _str(raw_next.get("question"), 800),
        "purpose": _str(raw_next.get("purpose"), 400),
        "difficulty_change": diff_change,
    }

    # Sensible defaults for the persistence/mastery flags when the model omits them.
    default_record = verdict in ("incorrect", "partially_correct")
    return {
        "verdict": verdict,
        "score_fraction": round(score, 3),
        "confidence": round(confidence, 3),
        "question_intent": _str(payload.get("question_intent")),
        "student_approach": _str(payload.get("student_approach")),
        "correct_parts": _str_list(payload.get("correct_parts")),
        "mistake_categories": categories,
        "root_cause": _str(payload.get("root_cause")),
        "likely_student_thought": _str(payload.get("likely_student_thought")),
        "exact_error_step": _str(payload.get("exact_error_step")),
        "correct_reasoning": _str_list(payload.get("correct_reasoning")),
        "final_answer": _str(payload.get("final_answer")),
        "improvement_advice": _str(payload.get("improvement_advice")),
        "prerequisites_to_review": _str_list(payload.get("prerequisites_to_review")),
        "next_question": next_question,
        "should_create_mistake_record": bool(payload.get("should_create_mistake_record", default_record)),
        "should_reduce_mastery": bool(payload.get("should_reduce_mastery", default_record)),
        "analysis_limitations": _str_list(payload.get("analysis_limitations")),
    }


def empty_analysis(verdict: str = "incorrect", note: str = "") -> dict[str, Any]:
    """A safe, well-formed analysis used only when the AI is unavailable."""
    result = normalize_analysis({"verdict": verdict})
    if note:
        result["analysis_limitations"] = [note]
    return result


# --------------------------------------------------------------------------- prompts

_SUBJECT_RULES = {
    "mathematics": (
        "MATHEMATICS: Verify the chosen formula, every substitution, each sign, each arithmetic step, "
        "and the units. Accept any mathematically equivalent form (e.g. 1/2, 0.5, 50%); compare values "
        "symbolically, never by raw string match. A correct value reached by a valid alternative method "
        "is correct. Distinguish a single arithmetic/sign slip (calculation_mistake / sign_error) from a "
        "wrong method (formula_selection_error) or a real conceptual gap."
    ),
    "physics": (
        "PHYSICS: Check the formula, substitutions, signs, and especially UNITS and dimensional consistency. "
        "A missing/incorrect unit is unit_error, not a conceptual failure. Accept equivalent numeric forms and "
        "reasonable rounding. Distinguish speed vs velocity, mass vs weight, etc. as conceptual only with evidence."
    ),
    "chemistry": (
        "CHEMISTRY: Verify balanced equations, states, charges, significant figures and units. A balancing slip "
        "is procedural; confusing concepts (e.g. oxidation vs reduction) is conceptual. Accept equivalent notations."
    ),
    "biology": (
        "BIOLOGY: Reward correct mechanisms and terminology; accept synonyms and valid paraphrases. Distinguish a "
        "missing detail (incomplete_answer) from a wrong mechanism (conceptual_misunderstanding)."
    ),
    "english": (
        "LANGUAGE: Separately judge grammar, vocabulary, spelling, punctuation and MEANING. Accept valid paraphrases "
        "and register variants. If the answer communicates the intended meaning, do not fail it for a minor slip; "
        "classify slips as language_error/typographical_error, not conceptual_misunderstanding."
    ),
    "german": (
        "LANGUAGE: Separately judge grammar, vocabulary, spelling, punctuation and MEANING. Accept valid paraphrases. "
        "A correct meaning with a small grammar/spelling slip is language_error, not a conceptual failure."
    ),
    "history": (
        "HUMANITIES: Judge factual accuracy, use of evidence, interpretation and argument quality separately. Accept "
        "multiple valid wordings and defensible interpretations; only mark incorrect on genuine factual/logical error."
    ),
    "geography": (
        "HUMANITIES: Judge factual accuracy, evidence and reasoning separately; accept multiple valid formulations."
    ),
    "computer science": (
        "COMPUTER SCIENCE: Accept any correct algorithm/output and equivalent code; judge logic and complexity, not "
        "exact syntax. Distinguish an off-by-one/typo from a wrong algorithm (conceptual)."
    ),
}


def _subject_rule(subject: str) -> str:
    key = str(subject or "").strip().casefold()
    return _SUBJECT_RULES.get(key, (
        "GENERAL: Judge accuracy and reasoning fairly; accept valid alternative correct answers and equivalent "
        "wordings; only diagnose a misconception with concrete evidence from the student's response."
    ))


def analysis_system_prompt(subject: str, language: str, grade: str = "") -> str:
    """The dedicated diagnostician system prompt (B), with subject-specific rules."""
    grade_line = f"The student is at this level: {grade}. Calibrate expectations to it.\n" if grade else ""
    return (
        "You are an expert educational diagnostician, subject teacher and learning-science specialist.\n"
        "Your goal is NOT merely to grade the answer. Your goal is to reconstruct the student's reasoning, "
        "identify the SMALLEST meaningful mistake, distinguish careless errors from conceptual misunderstandings, "
        "and produce the single most useful next learning action.\n"
        "Analyze from the student's perspective.\n"
        "Before judging, identify: the learning objective; the expected grade-level knowledge; valid alternative "
        "answers; possible ambiguity; and whether formatting, wording, translation or notation affected the response.\n"
        "Do not invent reasoning the student did not demonstrate. Use evidence from: the question, accepted answers, "
        "solution steps, the student response, previous mistakes, current mastery, grade, language, time taken, hints "
        "used, answer changes, and confidence input when available.\n"
        "Be precise, fair and constructive. A correct alternative method MUST be accepted. A correct answer with weak "
        "reasoning must NOT be treated the same as a fully demonstrated answer. A small arithmetic slip must NOT be "
        "described as a complete conceptual failure. If the question is flawed, ambiguous or generated incorrectly, say "
        "so (verdict 'ambiguous' and category ambiguous_question or ai_generated_question_error) instead of blaming the "
        "student.\n"
        "Never label a small typing error as a major misconception. Never invent a misconception without evidence. "
        "Never give generic advice ('review the topic', 'try again', 'be more careful', 'almost correct') without precise, "
        "actionable detail naming exactly what to do.\n"
        "CRITICAL RULES:\n"
        "1. For any question with an OBJECTIVE answer (arithmetic, facts, formulas, units), FIRST compute/verify the "
        "answer yourself. If your independently verified answer DIFFERS from the provided accepted/expected answer, trust "
        "YOUR verification: the provided key is wrong. Then set verdict 'ambiguous', add category "
        "'ai_generated_question_error', treat a student answer that matches your correct verification as right, and never "
        "penalize the student for the key's error. (E.g. the key says 7x8=54 — the key is wrong; 56 is correct.)\n"
        "2. A brief but logically VALID justification is fully correct — do NOT downgrade it. Only when the final answer is "
        "correct but the reasoning is clearly invalid, circular, or unrelated, set verdict 'partially_correct' with category "
        "'correct_with_incomplete_explanation' (or 'conceptual_misunderstanding' if the reasoning shows a real misconception).\n"
        "3. If the student correctly computed a DIFFERENT quantity than the one asked (e.g. area when perimeter was asked), "
        "prefer 'reading_comprehension_error' or 'formula_selection_error' over a generic conceptual label.\n"
        "4. If the question omits essential information needed to answer it (e.g. refers to 'the war', 'it', or 'the value' "
        "without specifying which), the QUESTION is at fault: set verdict 'ambiguous' with category 'ambiguous_question' and "
        "do not blame the student.\n"
        f"{grade_line}"
        f"{_subject_rule(subject)}\n"
        f"Write every student-facing string in {language}. Do not reveal hidden chain-of-thought; give concise, "
        "verifiable educational reasoning only.\n"
        "Return ONLY one JSON object matching this schema exactly (no markdown, no commentary):\n"
        f"{SCHEMA_SUMMARY}\n"
        f"'mistake_categories' values must come from: {', '.join(MISTAKE_CATEGORIES)}. "
        "For a fully correct, well-justified answer use verdict 'correct', score_fraction 1.0, empty mistake_categories, "
        "should_create_mistake_record false and should_reduce_mastery false."
    )


def analysis_user_prompt(evidence: dict[str, Any]) -> str:
    import json
    return (
        "Analyze the student's answer using this evidence and return the JSON object.\n"
        + json.dumps(evidence, ensure_ascii=False)
    )


def build_evidence(*, question: Any, expected_answer: Any, student_answer: str, subject: str,
                   grade: str = "", language: str = "English", concept: str = "",
                   solution_steps: Any = None, previous_mistakes: Any = None,
                   mastery: Any = None, hints_used: bool = False, time_seconds: float | None = None,
                   response_confidence: float | None = None, answer_changes: int | None = None,
                   source_context: str = "") -> dict[str, Any]:
    """Assemble the evidence object handed to the diagnostician."""
    evidence: dict[str, Any] = {
        "subject": subject,
        "grade": grade,
        "language": language,
        "concept": concept,
        "question": question,
        "accepted_answer": expected_answer,
        "student_answer": student_answer,
        "hints_used": bool(hints_used),
    }
    if solution_steps:
        evidence["solution_steps"] = solution_steps
    if previous_mistakes:
        evidence["previous_mistakes"] = previous_mistakes
    if mastery is not None:
        evidence["current_mastery"] = mastery
    if time_seconds is not None:
        evidence["time_taken_seconds"] = round(float(time_seconds), 1)
    if response_confidence is not None:
        evidence["student_confidence"] = response_confidence
    if answer_changes is not None:
        evidence["answer_changes"] = answer_changes
    if source_context:
        evidence["source_material"] = str(source_context)[:1800]
    return evidence


# ------------------------------------------------------------------ aggregation (C)

_WORD = re.compile(r"[a-zA-ZÀ-ɏ]{4,}")


_STOPWORDS = frozenset((
    "the", "and", "with", "that", "this", "when", "into", "from", "student", "answer",
    "used", "using", "than", "then", "because", "confuse", "confuses", "confused",
    "confusing", "mistake", "wrong", "incorrect", "error", "instead", "does", "your",
))


def _significant_words(text: str) -> list[str]:
    return sorted({
        w.lower().rstrip("s") for w in _WORD.findall(text or "")
        if w.lower() not in _STOPWORDS
    })[:4]


def _misconception_key(root_cause: str, categories: list[str], concept: str = "") -> str:
    """Group repeated mistakes by underlying misconception, not identical question text.

    Keyed on the primary category plus either the concept (when known) or the salient
    content words of the root cause, so wording variants of the same error merge.
    """
    cat = categories[0] if categories else "unspecified"
    concept = (concept or "").strip().lower()
    if concept:
        return f"{cat}:{concept}"
    words = _significant_words(root_cause)
    return f"{cat}:{'-'.join(words)}" if words else cat


def repeated_misconceptions(records: list[dict[str, Any]], min_count: int = 2) -> list[dict[str, Any]]:
    """Cluster mistake records into repeated misconceptions with counts and evidence.

    Each record needs at least: root_cause (str), mistake_categories (list), subject,
    concept, and last_seen. Returns clusters ordered by frequency then recency.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for record in records:
        categories = record.get("mistake_categories") or []
        key = _misconception_key(record.get("root_cause", ""), categories, record.get("concept", ""))
        bucket = buckets.setdefault(key, {
            "key": key, "count": 0, "categories": Counter(), "subjects": Counter(),
            "concepts": Counter(), "example_root_cause": record.get("root_cause", ""),
            "last_seen": record.get("last_seen"), "resolved": True,
        })
        bucket["count"] += 1
        for c in categories:
            bucket["categories"][c] += 1
        if record.get("subject"):
            bucket["subjects"][record["subject"]] += 1
        if record.get("concept"):
            bucket["concepts"][record["concept"]] += 1
        if record.get("root_cause") and len(record["root_cause"]) > len(bucket["example_root_cause"]):
            bucket["example_root_cause"] = record["root_cause"]
        if not record.get("resolved", False):
            bucket["resolved"] = False
        last = record.get("last_seen")
        if last and (bucket["last_seen"] is None or last > bucket["last_seen"]):
            bucket["last_seen"] = last
    clusters = [{
        "key": b["key"], "count": b["count"],
        "top_category": (b["categories"].most_common(1)[0][0] if b["categories"] else ""),
        "subjects": [s for s, _ in b["subjects"].most_common(3)],
        "concepts": [c for c, _ in b["concepts"].most_common(3)],
        "root_cause": b["example_root_cause"], "resolved": b["resolved"], "last_seen": b["last_seen"],
    } for b in buckets.values() if b["count"] >= min_count]
    clusters.sort(key=lambda c: (c["count"], str(c["last_seen"] or "")), reverse=True)
    return clusters

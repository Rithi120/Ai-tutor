"""Task-specific validation for every Learnova AI response.

Validators intentionally return plain Python data so existing routes keep their current
behaviour. Invalid data never crosses the gateway boundary.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable


FAILURE_CATEGORIES = {
    "provider_timeout", "provider_rate_limit", "invalid_json", "schema_validation",
    "source_reference_validation", "unsupported_language", "token_limit_exceeded",
    "request_limit_reached", "network_failure", "authentication_failure",
    "internal_application_error",
}
QUESTION_TYPES = {
    "multiple_choice", "checkboxes", "dropdown", "ordering", "text", "true_false",
    "matching", "fill_blank", "short_answer", "explanation", "calculation",
}
DIFFICULTY_WORDS = {"easy", "medium", "hard"}
SKILL_STATUSES = {"needs_practice", "developing", "mastered", "weak", "learning", "strong"}
BLOCK_TYPES = {
    "printed_text", "handwriting", "heading", "formula", "diagram", "table", "list",
    "annotation", "unknown",
}


@dataclass(frozen=True)
class ValidationReport:
    task_type: str
    prompt_version: str
    valid: bool
    category: str = ""
    summary: str = ""


@dataclass(frozen=True)
class TaskSchema:
    """Named production contract backed by one deterministic validator."""

    name: str
    validator: Callable[[Any, dict[str, Any]], None]


class AIValidationError(ValueError):
    def __init__(self, category: str, summary: str):
        self.category = category
        self.safe_summary = summary[:160]
        self.prompt_version = ""
        self.report: ValidationReport | None = None
        super().__init__(self.safe_summary)


def repair_latex_json(text: str) -> str:
    """Double any lone backslash that is not a valid JSON escape so single-backslash LaTeX
    (\\frac, \\sqrt, \\pm, ...) survives json.loads. Already-escaped backslashes, real newline
    escapes (\\n), and \\uXXXX sequences are left untouched."""

    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c != "\\":
            out.append(c)
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < n else ""
        after = text[i + 2] if i + 2 < n else ""
        if nxt in "\\\"/":
            out.append(c + nxt); i += 2                 # already-escaped  \  "  /
        elif nxt == "n":
            out.append(c + nxt); i += 2                 # newline - always preserve
        elif nxt == "u" and len(text[i + 2:i + 6]) == 4 and all(ch in "0123456789abcdefABCDEF" for ch in text[i + 2:i + 6]):
            out.append(c + nxt); i += 2                 # \uXXXX unicode escape
        elif nxt in "bfrt" and after.isalpha():
            out.append("\\\\"); i += 1                  # LaTeX word (\frac \times \beta \right) -> double
        elif nxt in "bfrtu":
            out.append(c + nxt); i += 2                 # genuine control escape (\b \f \r \t)
        else:
            out.append("\\\\"); i += 1                  # lone LaTeX backslash (\sqrt \pm \cdot) -> double
    return "".join(out)


def _fail(summary: str, category: str = "schema_validation") -> None:
    raise AIValidationError(category, summary)


def _dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(f"{name} must be an object")
    return value


def _list(value: Any, name: str, *, nonempty: bool = False) -> list[Any]:
    if not isinstance(value, list) or (nonempty and not value):
        _fail(f"{name} must be a{' non-empty' if nonempty else ''} list")
    return value


def _text(value: Any, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(f"{name} must be non-empty text")
    return value


def _number(value: Any, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{name} must be a number")
    if not minimum <= float(value) <= maximum:
        _fail(f"{name} must be between {minimum:g} and {maximum:g}")
    return float(value)


def _ids(context: dict[str, Any], key: str) -> set[int]:
    values = context.get(key) or []
    if isinstance(values, dict):
        values = values.keys()
    result = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            pass
    return result


def _validate_references(values: Any, allowed: set[int], name: str) -> None:
    refs = _list(values, name, nonempty=True)
    try:
        normalized = {int(value) for value in refs}
    except (TypeError, ValueError):
        _fail(f"{name} contains an invalid identifier", "source_reference_validation")
    if any(value <= 0 for value in normalized) or (allowed and not normalized <= allowed):
        _fail(f"{name} contains an unknown identifier", "source_reference_validation")


def _question(value: Any, name: str = "question", *, require_subject: bool = False) -> dict[str, Any]:
    question = _dict(value, name)
    _text(question.get("id"), f"{name}.id")
    _text(question.get("prompt"), f"{name}.prompt")
    _text(question.get("concept"), f"{name}.concept")
    if require_subject:
        _text(question.get("subject"), f"{name}.subject")
    difficulty = question.get("difficulty")
    if isinstance(difficulty, str):
        if difficulty not in DIFFICULTY_WORDS:
            _fail(f"{name}.difficulty is unsupported")
    elif isinstance(difficulty, int) and not isinstance(difficulty, bool):
        if difficulty not in {1, 2, 3}:
            _fail(f"{name}.difficulty is unsupported")
    else:
        _fail(f"{name}.difficulty is unsupported")
    qtype = _text(question.get("type", question.get("question_type")), f"{name}.type")
    if qtype not in QUESTION_TYPES:
        _fail(f"{name}.type is unsupported")
    _text(question.get("hint", "not provided"), f"{name}.hint", allow_empty=True)
    if "expected_answer" not in question:
        _fail(f"{name}.expected_answer is required")
    if qtype in {"multiple_choice", "checkboxes", "dropdown", "ordering", "matching", "true_false"}:
        options = _list(question.get("options"), f"{name}.options", nonempty=True)
        option_ids = []
        for index, option in enumerate(options):
            if isinstance(option, dict):
                option_ids.append(_text(option.get("id"), f"{name}.options[{index}].id"))
                _text(option.get("label"), f"{name}.options[{index}].label")
            elif isinstance(option, str) and option.strip():
                option_ids.append(option)
            else:
                _fail(f"{name}.options[{index}] is invalid")
        if len(option_ids) != len(set(option_ids)):
            _fail(f"{name} has duplicate option IDs")
    else:
        _list(question.get("options", []), f"{name}.options")
    return question


def _unique_questions(questions: list[Any], *, exam: bool = False) -> None:
    ids: list[str] = []
    prompts: list[str] = []
    for index, item in enumerate(questions):
        question = _dict(item, f"questions[{index}]")
        identifier = question.get("id")
        if exam and identifier is None:
            _fail(f"questions[{index}].id is required")
        if identifier is not None:
            ids.append(str(identifier).strip().casefold())
        prompts.append(re.sub(r"\W+", " ", _text(question.get("prompt"), f"questions[{index}].prompt")).casefold().strip())
    if len(ids) != len(set(ids)):
        _fail("question IDs must be unique")
    if len(prompts) != len(set(prompts)):
        _fail("question prompts must be unique")


def _lesson(data: Any, context: dict[str, Any]) -> None:
    root = _dict(data, "lesson")
    for field in ("lesson_title", "explanation"):
        _text(root.get(field), field)
    concepts = _list(root.get("concepts"), "concepts", nonempty=True)
    for index, concept in enumerate(concepts):
        _text(_dict(concept, f"concepts[{index}]").get("name"), f"concepts[{index}].name")
    worked = _dict(root.get("worked_example"), "worked_example")
    _text(worked.get("problem"), "worked_example.problem")
    _list(worked.get("steps"), "worked_example.steps", nonempty=True)
    _text(worked.get("answer"), "worked_example.answer")
    _question(root.get("question"))


def _quiz(data: Any, context: dict[str, Any]) -> None:
    questions = _list(_dict(data, "quiz").get("questions"), "questions", nonempty=True)
    expected = context.get("question_count")
    if expected is not None and len(questions) != int(expected):
        _fail(f"question count must be exactly {int(expected)}")
    for index, item in enumerate(questions):
        _question(item, f"questions[{index}]")
    _unique_questions(questions)


def _answer(data: Any, context: dict[str, Any]) -> None:
    root = _dict(data, "answer evaluation")
    evaluation = _dict(root.get("evaluation"), "evaluation")
    if not isinstance(evaluation.get("is_correct"), bool):
        _fail("evaluation.is_correct must be a boolean")
    _number(evaluation.get("score"), "evaluation.score", 0, 100)
    _text(evaluation.get("feedback"), "evaluation.feedback")
    status = evaluation.get("skill_status")
    if status is not None and status not in SKILL_STATUSES:
        _fail("evaluation.skill_status is unsupported")
    if not context.get("is_final", False):
        _question(root.get("next_question"), "next_question")


def _project(data: Any, context: dict[str, Any]) -> None:
    sections = _list(_dict(data, "project").get("sections"), "sections", nonempty=True)
    expected = context.get("section_count")
    if expected and context.get("exact_section_count") and len(sections) != int(expected):
        _fail(f"section count must be exactly {int(expected)}")
    allowed = _ids(context, "source_page_ids")
    for index, value in enumerate(sections):
        section = _dict(value, f"sections[{index}]")
        _text(section.get("title"), f"sections[{index}].title")
        if context.get("operation") == "split":
            continue
        for field in ("main_topic", "simple_explanation", "standard_explanation", "detailed_explanation"):
            _text(section.get(field), f"sections[{index}].{field}")
        for field in ("learning_goals", "important_facts", "definitions", "formulas", "examples", "vocabulary", "relationships", "likely_exam_questions", "recall_cards"):
            _list(section.get(field), f"sections[{index}].{field}")
        for card_index, value in enumerate(section["recall_cards"]):
            card = _dict(value, f"sections[{index}].recall_cards[{card_index}]")
            for field in ("kind", "prompt", "answer", "source_text"):
                _text(card.get(field), f"sections[{index}].recall_cards[{card_index}].{field}")
        _validate_references(section.get("source_page_ids"), allowed, f"sections[{index}].source_page_ids")
        minutes = section.get("estimated_minutes")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 120:
            _fail(f"sections[{index}].estimated_minutes is invalid")


def _ocr(data: Any, context: dict[str, Any]) -> None:
    root = _dict(data, "recognition")
    blocks = _list(root.get("blocks"), "blocks")
    for index, value in enumerate(blocks):
        block = _dict(value, f"blocks[{index}]")
        if _text(block.get("type"), f"blocks[{index}].type") not in BLOCK_TYPES:
            _fail(f"blocks[{index}].type is unsupported")
        _text(block.get("content"), f"blocks[{index}].content")
        _number(block.get("confidence"), f"blocks[{index}].confidence", 0, 1)
        bbox = _list(block.get("bbox"), f"blocks[{index}].bbox")
        if len(bbox) != 4:
            _fail(f"blocks[{index}].bbox must contain four coordinates")
        for position, coordinate in enumerate(bbox):
            _number(coordinate, f"blocks[{index}].bbox[{position}]", 0, 1)
    page_number = root.get("detected_page_number")
    if page_number not in (None, ""):
        try:
            if int(page_number) <= 0:
                raise ValueError
        except (TypeError, ValueError):
            _fail("detected_page_number is invalid", "source_reference_validation")


def _adaptive(data: Any, context: dict[str, Any]) -> None:
    if context.get("lesson"):
        _lesson(data, context)
        return
    root = _dict(data, "adaptive practice")
    question = _question(root.get("question", root.get("next_question")), require_subject=True)
    recent = {re.sub(r"\W+", " ", str(item)).casefold().strip() for item in context.get("recent_questions", [])}
    prompt = re.sub(r"\W+", " ", question["prompt"]).casefold().strip()
    if prompt in recent:
        _fail("question duplicates a recently answered question")


def _exam(data: Any, context: dict[str, Any]) -> None:
    questions = _list(_dict(data, "exam").get("questions"), "questions", nonempty=True)
    count = context.get("question_count")
    if count is not None and len(questions) != int(count):
        _fail(f"question count must be exactly {int(count)}")
    _unique_questions(questions, exam=True)
    section_ids = _ids(context, "section_ids")
    page_map = context.get("source_page_ids") or {}
    actual_sections: Counter[int] = Counter()
    actual_difficulties: Counter[str] = Counter()
    allowed_types = set(context.get("question_types") or [])
    for index, value in enumerate(questions):
        item = _dict(value, f"questions[{index}]")
        section_id = item.get("section_id")
        try:
            section_id = int(section_id)
        except (TypeError, ValueError):
            _fail(f"questions[{index}].section_id is invalid", "source_reference_validation")
        if section_ids and section_id not in section_ids:
            _fail(f"questions[{index}].section_id is unknown", "source_reference_validation")
        actual_sections[section_id] += 1
        if item.get("difficulty") not in DIFFICULTY_WORDS:
            _fail(f"questions[{index}].difficulty is unsupported")
        actual_difficulties[str(item["difficulty"])] += 1
        qtype = item.get("question_type")
        if qtype not in QUESTION_TYPES:
            _fail(f"questions[{index}].question_type is unsupported")
        if allowed_types and qtype not in allowed_types:
            _fail(f"questions[{index}].question_type was not requested")
        concepts = _list(item.get("concepts"), f"questions[{index}].concepts", nonempty=True)
        for concept_index, concept in enumerate(concepts):
            _text(concept, f"questions[{index}].concepts[{concept_index}]")
        _text(item.get("supporting_text"), f"questions[{index}].supporting_text")
        allowed_pages = _ids({"ids": page_map.get(str(section_id), page_map.get(section_id, []))}, "ids")
        _validate_references(item.get("source_page_ids"), allowed_pages, f"questions[{index}].source_page_ids")
        if "expected_answer" not in item:
            _fail(f"questions[{index}].expected_answer is required")
        _list(item.get("options", []), f"questions[{index}].options")
    expected_sections = {
        int(key): int(value) for key, value in (context.get("section_allocation") or {}).items()
        if int(value) > 0
    }
    if expected_sections and dict(actual_sections) != expected_sections:
        _fail("exam section allocation does not match the request")
    expected_difficulties = {
        str(key): int(value) for key, value in (context.get("difficulty_distribution") or {}).items()
        if int(value) > 0
    }
    if expected_difficulties and dict(actual_difficulties) != expected_difficulties:
        _fail("exam difficulty distribution does not match the request")


def _exam_evaluation(data: Any, context: dict[str, Any]) -> None:
    results = _list(_dict(data, "exam evaluation").get("results"), "results", nonempty=True)
    expected = _ids(context, "question_ids")
    seen = set()
    for index, value in enumerate(results):
        item = _dict(value, f"results[{index}]")
        try:
            question_id = int(item.get("question_id"))
        except (TypeError, ValueError):
            _fail(f"results[{index}].question_id is invalid", "source_reference_validation")
        seen.add(question_id)
        _number(item.get("score"), f"results[{index}].score", 0, 100)
        _text(item.get("evaluation"), f"results[{index}].evaluation")
    if len(seen) != len(results) or (expected and seen != expected):
        _fail("exam evaluation question IDs do not match", "source_reference_validation")


def _translation(data: Any, context: dict[str, Any]) -> None:
    values = _list(_dict(data, "translation").get("translations"), "translations")
    if context.get("texts") is not None and len(values) != len(context["texts"]):
        _fail("translation count does not match input")
    for index, value in enumerate(values):
        _text(value, f"translations[{index}]", allow_empty=True)


VALIDATORS: dict[str, Callable[[Any, dict[str, Any]], None]] = {
    "lesson_generation": _lesson, "quiz_generation": _quiz,
    "answer_evaluation": _answer, "translation": _translation,
    "ocr_document_recognition": _ocr, "project_section_generation": _project,
    "adaptive_practice": _adaptive, "final_exam_generation": _exam,
    "final_exam_evaluation": _exam_evaluation,
}
TASK_SCHEMAS = {
    task_type: TaskSchema(name=f"{task_type}_schema", validator=validator)
    for task_type, validator in VALIDATORS.items()
}


def _validate_output(task_type: str, output_text: str, prompt_version: str, context: dict[str, Any] | None = None, *, max_characters: int = 200_000) -> ValidationReport:
    if not isinstance(output_text, str) or not output_text.strip():
        raise AIValidationError("schema_validation", "response was empty")
    if len(output_text) > max_characters:
        raise AIValidationError("token_limit_exceeded", "response exceeded the configured size limit")
    if task_type == "tutor_chat":
        return ValidationReport(task_type, prompt_version, True)
    cleaned = repair_latex_json(re.sub(r"^```(?:json)?\s*|\s*```$", "", output_text.strip()))
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise AIValidationError("invalid_json", f"invalid JSON near character {error.pos}") from error
    schema = TASK_SCHEMAS.get(task_type)
    if not schema:
        _fail("no validation schema is registered for this task")
    schema.validator(payload, context or {})
    return ValidationReport(task_type, prompt_version, True)


def validate_output(task_type: str, output_text: str, prompt_version: str, context: dict[str, Any] | None = None, *, max_characters: int = 200_000) -> ValidationReport:
    """Validate and attach a versioned, safe report to rejected responses."""

    try:
        return _validate_output(
            task_type, output_text, prompt_version, context,
            max_characters=max_characters,
        )
    except AIValidationError as error:
        error.prompt_version = prompt_version
        error.report = ValidationReport(
            task_type=task_type, prompt_version=prompt_version, valid=False,
            category=error.category, summary=error.safe_summary,
        )
        raise

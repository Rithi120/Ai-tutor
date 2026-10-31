"""Task-specific validation for every Learnova AI response.

Validators intentionally return plain Python data so existing routes keep their current
behaviour. Invalid data never crosses the gateway boundary.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, NoReturn

from .prompts import STRUCTURED_TASKS


FAILURE_CATEGORIES = {
    "provider_timeout", "provider_rate_limit", "invalid_json", "schema_validation",
    "source_reference_validation", "unsupported_language", "token_limit_exceeded",
    "request_limit_reached", "network_failure", "authentication_failure",
    "internal_application_error",
    # A withdrawn model id, a request the provider will not accept, an overloaded or
    # failing provider, a provider that declined to answer, and a spent site budget.
    "model_not_found", "invalid_request", "provider_overloaded", "provider_unavailable",
    "provider_refusal", "budget_exhausted", "provider_quota_exhausted",
}
QUESTION_TYPES = {
    "multiple_choice", "checkboxes", "dropdown", "ordering", "text", "true_false",
    "matching", "fill_blank", "short_answer", "explanation", "calculation",
    # Exercises built on a picture the student can actually see. A question of either
    # type is only accepted with verified media attached (learnova/projects/media.py),
    # so neither can produce "describe the diagram" with no diagram.
    "photo_response", "photo_ordering",
}
# The types whose pictures are part of the question rather than decoration.
PHOTO_QUESTION_TYPES = {"photo_response", "photo_ordering"}
DIFFICULTY_WORDS = {"easy", "medium", "hard"}
# What a model actually writes for a question's level. All of it means 1-3 and the app
# clamps it (app.question_level); refusing "2" or "Medium" failed whole lessons.
_LEVEL_WORDS = DIFFICULTY_WORDS | {"leicht", "einfach", "mittel", "schwer", "facile", "moyen", "difficile",
                                   "fácil", "medio", "difícil", "low", "high"}
SKILL_STATUSES = {"needs_practice", "developing", "mastered", "weak", "learning", "strong"}
# The block types a recognition response may use. This is the single definition:
# learnova/ocr/service.py imports it, and the OCR prompt is built from it, so the thing
# the model is asked for and the thing this validator accepts cannot drift apart.
#
# They did drift, and it was the most expensive bug in the app. The prompt tells the model
# to mark illegible fragments "uncertain" and to mark "crossed_out" writing; this list had
# neither, and carried "list" and "unknown" that nothing ever produced. So the model
# followed its instructions, one block failed the check, the whole page was rejected, and
# the gateway paid for a corrective retry. On handwriting that is most pages: measured in
# instance/ai_usage.jsonl, two of five real OCR calls failed this way and burned 59% of
# all OCR output tokens for zero characters.
BLOCK_TYPES = frozenset({
    "printed_text", "handwriting", "formula", "table", "diagram",
    "heading", "annotation", "uncertain", "crossed_out",
})


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
            out.append(c + nxt)                 # already-escaped  \  "  /
            i += 2
        elif nxt == "n":
            out.append(c + nxt)                 # newline - always preserve
            i += 2
        elif nxt == "u" and len(text[i + 2:i + 6]) == 4 and all(ch in "0123456789abcdefABCDEF" for ch in text[i + 2:i + 6]):
            out.append(c + nxt)                 # \uXXXX unicode escape
            i += 2
        elif nxt in "bfrt" and after.isalpha():
            out.append("\\\\")                  # LaTeX word (\frac \times \beta \right) -> double
            i += 1
        elif nxt in "bfrtu":
            out.append(c + nxt)                 # genuine control escape (\b \f \r \t)
            i += 2
        else:
            out.append("\\\\")                  # lone LaTeX backslash (\sqrt \pm \cdot) -> double
            i += 1
    return "".join(out)


def _fail(summary: str, category: str = "schema_validation") -> NoReturn:
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


def _is_level(value: Any) -> bool:
    """1-5 as a number or numeric text, or a level word; never a boolean."""

    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        text = value.strip().casefold()
        if text in _LEVEL_WORDS:
            return True
        try:
            value = float(text)
        except ValueError:
            return False
    return isinstance(value, (int, float)) and 0 <= value <= 5


_TYPE_ALIASES = {
    "multiple choice": "multiple_choice", "multiple-choice": "multiple_choice", "mcq": "multiple_choice",
    "single_choice": "multiple_choice", "single choice": "multiple_choice", "choice": "multiple_choice",
    "true/false": "true_false", "truefalse": "true_false", "true-false": "true_false", "boolean": "true_false",
    "open": "short_answer", "open_question": "short_answer", "free_text": "short_answer",
    "fill-in-the-blank": "fill_blank", "fill_in_the_blank": "fill_blank", "cloze": "fill_blank",
}
_ANSWER_ALIASES = ("answer", "correct_answer", "correctAnswer", "correct_option", "correct", "solution",
                   "right_answer")


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
            return str(value).strip()
    return ""


def repair_question(value: Any, concepts: Any = (), subject: Any = None) -> Any:
    """A practice question in the one shape the app reads, from the variants models write.

    Seen live on 2026-10-31 (gpt-oss-20b, Projektmodus): "answer": "B" instead of
    expected_answer, options as {"option": "A", "text": ...} or bare strings, and no
    concept, difficulty or hint - on the retry too, so the student could not start the
    lesson. Names are mapped, a missing level becomes 1, a missing concept the lesson's
    first one, and an answer written as the option's text or letter becomes its id.
    Nothing is guessed: a choice question whose correct option cannot be identified
    keeps no expected_answer and still fails validation. Returns a repaired copy.
    """

    if not isinstance(value, dict):
        return value
    question = dict(value)
    if not _first_text(question, ("id",)):
        question["id"] = "q1"
    if not _first_text(question, ("prompt",)):
        prompt = _first_text(question, ("question", "text", "stem"))
        if prompt:
            question["prompt"] = prompt
    qtype = str(question.get("type") or question.get("question_type") or "").strip().casefold()
    qtype = _TYPE_ALIASES.get(qtype, qtype.replace(" ", "_").replace("-", "_"))
    if not qtype:
        qtype = "multiple_choice" if question.get("options") else "short_answer"
    question["type"] = qtype
    question.pop("question_type", None)
    if not _is_level(question.get("difficulty")):
        question["difficulty"] = 1
    if not _first_text(question, ("concept",)):
        for concept in concepts if isinstance(concepts, (list, tuple)) else ():
            name = concept.get("name") if isinstance(concept, dict) else concept
            if isinstance(name, str) and name.strip():
                question["concept"] = name.strip()
                break
    if not isinstance(question.get("hint"), str):
        question["hint"] = ""
    if isinstance(subject, str) and subject.strip() and not _first_text(question, ("subject",)):
        question["subject"] = subject.strip()

    options = question.get("options")
    correct_ids: list[str] = []
    if isinstance(options, list):
        repaired = []
        for index, option in enumerate(options):
            if isinstance(option, dict):
                identifier = _first_text(option, ("id", "key", "option", "letter", "choice"))
                label = _first_text(option, ("label", "text", "content", "value", "answer", "option_text"))
                if not identifier and "label" in option and _first_text(option, ("text", "content")):
                    identifier, label = _first_text(option, ("label",)), _first_text(option, ("text", "content"))
                identifier = identifier or "abcdefghij"[index % 10]
                if option.get("correct") is True or option.get("is_correct") is True:
                    correct_ids.append(identifier)
                option = {**option, "id": identifier, "label": label or identifier}
            repaired.append(option)
        question["options"] = options = repaired
    else:
        options = []

    if "expected_answer" not in question or question["expected_answer"] in (None, ""):
        for key in _ANSWER_ALIASES:
            if key in question and question[key] not in (None, "") and not isinstance(question[key], bool):
                question["expected_answer"] = question[key]
                break
        else:
            if correct_ids:
                question["expected_answer"] = correct_ids[0] if len(correct_ids) == 1 else correct_ids

    if options and "expected_answer" in question:
        def option_id(answer: Any) -> Any:
            if not isinstance(answer, (str, int)) or isinstance(answer, bool):
                return answer
            text = str(answer).strip().casefold()
            for option in options:
                if isinstance(option, dict):
                    if text in (option["id"].casefold(), str(option["label"]).strip().casefold()):
                        return option["id"]
                elif isinstance(option, str) and text == option.strip().casefold():
                    return option
            # A letter for bare-string options: "B" is the second one.
            if len(text) == 1 and "a" <= text <= "j" and all(isinstance(item, str) for item in options):
                position = ord(text) - ord("a")
                if position < len(options):
                    return options[position]
            return answer

        expected = question["expected_answer"]
        question["expected_answer"] = (
            [option_id(item) for item in expected] if isinstance(expected, list) else option_id(expected))
    return question


def _question(value: Any, name: str = "question", *, require_subject: bool = False) -> dict[str, Any]:
    question = _dict(value, name)
    identifier = question.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)) or not str(identifier).strip():
        _fail(f"{name}.id must be non-empty text")
    _text(question.get("prompt"), f"{name}.prompt")
    _text(question.get("concept"), f"{name}.concept")
    if require_subject:
        _text(question.get("subject"), f"{name}.subject")
    if not _is_level(question.get("difficulty")):
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
    # A concept is {"name": ...}; models also send the bare name ("concepts": ["Brüche"]),
    # which app.repair_lesson turns into the object. Seven lessons failed on that alone.
    # A blank entry is dropped there too; at least one usable concept must remain.
    usable = 0
    for index, concept in enumerate(concepts):
        if isinstance(concept, str):
            usable += bool(concept.strip())
        else:
            _text(_dict(concept, f"concepts[{index}]").get("name"), f"concepts[{index}].name")
            usable += 1
    if not usable:
        _fail("concepts must name at least one concept")
    # The worked example supports the lesson; the explanation and the question are the
    # lesson. A model that leaves the example's problem empty (common for topics where a
    # "worked problem" is unnatural, and repeated on the corrective retry) used to fail
    # the whole lesson, so a student could not learn the topic at all. The example is
    # therefore never validated here: repair_worked_example keeps what is usable and
    # drops an empty or malformed one, and the lesson page hides the missing card.
    _question(repair_question(root.get("question"), root.get("concepts"), context.get("subject")))


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
        _question(repair_question(root.get("next_question"), context.get("concepts", ())), "next_question")


def _project(data: Any, context: dict[str, Any]) -> None:
    sections = _list(_dict(data, "project").get("sections"), "sections",
                     nonempty=not context.get("allow_empty"))
    expected = context.get("section_count")
    if expected and context.get("exact_section_count") and len(sections) != int(expected):
        _fail(f"section count must be exactly {int(expected)}")
    allowed = _ids(context, "source_page_ids")
    for index, value in enumerate(sections):
        section = _dict(value, f"sections[{index}]")
        _text(section.get("title"), f"sections[{index}].title")
        if context.get("operation") == "split":
            continue
        if context.get("lenient_topics"):
            # Project Mode topic extraction: the structure is checked here; content is
            # checked item by item by learnova.projects.topics.normalize_topic, which drops
            # an unusable card or a topic without a real page instead of failing the batch.
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


def _competencies(data: Any, context: dict[str, Any]) -> None:
    """competency_extraction: rows read from a Kompetenzraster, each pointing at pages.

    The vocabulary is fixed so the planner can rely on it; a covered/partial claim must
    quote evidence (the app then checks the quote really appears in the notes).
    """

    rows = _list(_dict(data, "competencies").get("competencies"), "competencies", nonempty=True)
    allowed = _ids(context, "source_page_ids")
    for index, value in enumerate(rows):
        row = _dict(value, f"competencies[{index}]")
        _text(row.get("statement"), f"competencies[{index}].statement")
        _text(row.get("topic"), f"competencies[{index}].topic")
        if str(row.get("level") or "") not in {"basic", "intermediate", "advanced"}:
            _fail(f"competencies[{index}].level is unsupported")
        importance = row.get("importance")
        if isinstance(importance, bool) or not isinstance(importance, int) or not 1 <= importance <= 3:
            _fail(f"competencies[{index}].importance must be 1, 2 or 3")
        coverage = str(row.get("coverage") or "")
        if coverage not in {"covered", "partial", "missing"}:
            _fail(f"competencies[{index}].coverage is unsupported")
        _text(row.get("evidence"), f"competencies[{index}].evidence", allow_empty=coverage == "missing")
        if allowed:
            _validate_references(row.get("source_page_ids"), allowed, f"competencies[{index}].source_page_ids")


def _vocabulary_page(data: Any, context: dict[str, Any]) -> None:
    """vocabulary_page_extraction: the rows of one textbook page, as the table prints them.

    A row may have an unreadable side - that is how the student learns which word to
    type in - but never two. `rows` may be empty: a page with no table is a fact the
    caller handles by falling back to line-by-line reading, not a broken answer.
    """

    root = _dict(data, "page")
    rows = _list(root.get("rows"), "rows")
    for index, value in enumerate(rows):
        row = _dict(value, f"rows[{index}]")
        term = _text(row.get("term") or "", f"rows[{index}].term", allow_empty=True)
        translation = _text(row.get("translation") or "", f"rows[{index}].translation", allow_empty=True)
        if not term.strip() and not translation.strip():
            _fail(f"rows[{index}] has neither a term nor a translation")
        _text(row.get("phonetic") or "", f"rows[{index}].phonetic", allow_empty=True)
        _text(row.get("note") or "", f"rows[{index}].note", allow_empty=True)
        _number(row.get("confidence"), f"rows[{index}].confidence", 0, 1)
        examples = _list(row.get("examples") if row.get("examples") is not None else [], f"rows[{index}].examples")
        for position, item in enumerate(examples):
            example = _dict(item, f"rows[{index}].examples[{position}]")
            _text(example.get("sentence"), f"rows[{index}].examples[{position}].sentence")
            _text(example.get("translation") or "", f"rows[{index}].examples[{position}].translation",
                  allow_empty=True)


def _ocr(data: Any, context: dict[str, Any]) -> None:
    root = _dict(data, "recognition")
    blocks = _list(root.get("blocks"), "blocks")
    for index, value in enumerate(blocks):
        block = _dict(value, f"blocks[{index}]")
        block_type = _text(block.get("type"), f"blocks[{index}].type")
        if block_type not in BLOCK_TYPES:
            _fail(f"blocks[{index}].type is unsupported")
        # A diagram often has no readable caption, and normalize_recognition keeps such a
        # block on purpose (ocr/service.py: `if not content and block_type != "diagram"`).
        # Demanding text here rejected a whole page the app would have accepted, and paid
        # for a retry to be told the same thing again.
        _text(block.get("content"), f"blocks[{index}].content",
              allow_empty=block_type == "diagram")
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


def _visual_reading(data: Any, context: dict[str, Any]) -> None:
    """image_understanding: lenient on purpose. learnova.vision.reading normalises every
    field; only a shape it cannot use is a failure worth paying a corrective retry for."""

    root = _dict(data, "reading")
    blocks = _list(root.get("blocks") if root.get("blocks") is not None else [], "blocks")
    for index, value in enumerate(blocks):
        block = _dict(value, f"blocks[{index}]")
        text = block.get("text", block.get("content", ""))
        if not isinstance(text, str):
            _fail(f"blocks[{index}].text must be a string")
    for name in ("images", "diagrams", "uncertain"):
        if root.get(name) is not None:
            _list(root.get(name), name)


def _adaptive(data: Any, context: dict[str, Any]) -> None:
    if context.get("lesson"):
        _lesson(data, context)
        return
    root = _dict(data, "adaptive practice")
    question = _question(repair_question(root.get("question", root.get("next_question")), root.get("concepts"),
                                         context.get("subject")), require_subject=True)
    recent = {re.sub(r"\W+", " ", str(item)).casefold().strip() for item in context.get("recent_questions", [])}
    prompt = re.sub(r"\W+", " ", question["prompt"]).casefold().strip()
    if prompt in recent:
        _fail("question duplicates a recently answered question")


def _exam(data: Any, context: dict[str, Any]) -> None:
    questions = _list(_dict(data, "exam").get("questions"), "questions", nonempty=True)
    if context.get("lenient_exam"):
        # Project Mode practice exams: the structure is checked here; each question is
        # checked by the app, which drops an unusable one and keeps the exam (one wrong
        # count or one unquotable excerpt used to throw the whole exam away).
        for index, value in enumerate(questions):
            _dict(value, f"questions[{index}]")
        return
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
            section_id = int(section_id if section_id is not None else "")  # None -> ValueError
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
            raw_question_id = item.get("question_id")
            question_id = int(raw_question_id if raw_question_id is not None else "")  # None -> ValueError
        except (TypeError, ValueError):
            _fail(f"results[{index}].question_id is invalid", "source_reference_validation")
        seen.add(question_id)
        _number(item.get("score"), f"results[{index}].score", 0, 100)
        _text(item.get("evaluation"), f"results[{index}].evaluation")
    if len(seen) != len(results) or (expected and seen != expected):
        _fail("exam evaluation question IDs do not match", "source_reference_validation")


def _flashcards(data: Any, context: dict[str, Any]) -> None:
    # Lenient by design: individual weak cards are dropped during normalization, so the
    # response only needs at least one usable card rather than every card being perfect.
    root = _dict(data, "flashcard set")
    cards = _list(root.get("cards"), "cards", nonempty=True)
    usable = sum(
        1 for card in cards
        if isinstance(card, dict)
        and isinstance(card.get("front"), str) and card["front"].strip()
        and isinstance(card.get("back"), str) and card["back"].strip()
    )
    if usable == 0:
        _fail("no flashcard has both a front and a back")


def _region_review(data: Any, context: dict[str, Any]) -> None:
    # Lenient like the page recogniser: normalize_region_review drops entries with an
    # unusable index, and a fragment the model honestly marks illegible is a valid answer,
    # so the response only has to be a list of objects carrying an index.
    root = _dict(data, "region review")
    regions = _list(root.get("regions"), "regions", nonempty=True)
    if not any(isinstance(item, dict) and item.get("index") is not None for item in regions):
        _fail("no region carries an index")


def _flashcard_suggestions(data: Any, context: dict[str, Any]) -> None:
    # Same leniency as _flashcards: normalize_suggestions drops weak entries, so one
    # usable definition is enough. The model's count and style label are never trusted.
    root = _dict(data, "definition suggestions")
    items = _list(root.get("suggestions"), "suggestions", nonempty=True)
    usable = sum(
        1 for item in items
        if isinstance(item, dict)
        and isinstance(item.get("back"), str) and item["back"].strip()
    )
    if usable == 0:
        _fail("no suggestion has a definition")


def _flashcard_review(data: Any, context: dict[str, Any]) -> None:
    root = _dict(data, "AI review")
    fields = ("overallScore", "accuracyScore", "clarityScore", "usefulnessScore",
              "coverageScore", "difficultyScore", "originalityScore")
    numeric = [
        value for value in (root.get(field) for field in fields)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 5
    ]
    if not numeric:
        _fail("AI review must include at least one 0-5 score")
    _text(root.get("summary"), "summary")


def _mistake_analysis(data: Any, context: dict[str, Any]) -> None:
    from learnova.analysis.service import AnalysisSchemaError, validate_analysis
    try:
        validate_analysis(data)
    except AnalysisSchemaError as error:
        _fail(f"analysis schema: {error}")


def _answer_diagnosis(data: Any, context: dict[str, Any]) -> None:
    """The evidence-linked diagnosis:v2 contract (learnova.diagnostics.schema).

    Only unusable output raises here, because each rejection costs one corrective retry.
    Claims that are merely unsupported are dropped by the normalizer and reported through
    the result's own `validation_status`.
    """

    from learnova.diagnostics.schema import DiagnosisSchemaError, validate_diagnosis
    try:
        validate_diagnosis(data)
    except DiagnosisSchemaError as error:
        _fail(f"diagnosis schema: {error}")


def _diagnosis_verification(data: Any, context: dict[str, Any]) -> None:
    root = _dict(data, "verification")
    if not isinstance(root.get("agrees"), bool):
        _fail("verification.agrees must be a boolean")
    if not root["agrees"]:
        _text(root.get("reason"), "verification.reason")


def _question_generation(data: Any, context: dict[str, Any]) -> None:
    """Structural validation only; alignment, answerability and duplicates are checked
    deterministically by learnova.diagnostics.question_spec once the object is parsed."""

    root = _dict(data, "question generation")
    question = _question(root.get("question", root.get("next_question")), require_subject=False)
    for field in ("solution_steps", "rubric"):
        if field in question:
            _list(question.get(field), f"question.{field}")
    recent = {
        re.sub(r"\W+", " ", str(item)).casefold().strip()
        for item in (context.get("avoid_prompts") or [])
    }
    prompt = re.sub(r"\W+", " ", question["prompt"]).casefold().strip()
    if prompt and prompt in recent:
        _fail("question duplicates a recently answered question")


def _content_moderation(data: Any, context: dict[str, Any]) -> None:
    """The context-aware moderation:v1 contract (learnova.moderation.schema).

    Structural validation only. Evidence grounding - checking that a flagged safety
    dimension quotes text that actually occurs in the submission - needs the submitted
    content, which the gateway does not have, so it runs in `normalize_classification`
    at the call site.
    """

    from learnova.moderation.schema import ModerationSchemaError, validate_classification
    try:
        validate_classification(data)
    except ModerationSchemaError as error:
        _fail(str(error))


def _report_review(data: Any, context: dict[str, Any]) -> None:
    """The strict report-review contract (learnova.community.reports).

    Structural validation here; grounding the affected span in the reported set needs
    the content, so it runs in `normalize_report_review` at the call site.
    """

    from learnova.community.reports import ReportReviewSchemaError, validate_report_review
    try:
        validate_report_review(data)
    except ReportReviewSchemaError as error:
        _fail(str(error))


def _translation(data: Any, context: dict[str, Any]) -> None:
    values = _list(_dict(data, "translation").get("translations"), "translations")
    if context.get("texts") is not None and len(values) != len(context["texts"]):
        _fail("translation count does not match input")
    for index, value in enumerate(values):
        _text(value, f"translations[{index}]", allow_empty=True)


VALIDATORS: dict[str, Callable[[Any, dict[str, Any]], None]] = {
    "lesson_generation": _lesson, "quiz_generation": _quiz,
    "answer_evaluation": _answer, "translation": _translation,
    "mistake_analysis": _mistake_analysis,
    "answer_diagnosis": _answer_diagnosis,
    "diagnosis_verification": _diagnosis_verification,
    "question_generation": _question_generation,
    "ocr_document_recognition": _ocr, "handwriting_region_review": _region_review,
    "project_section_generation": _project,
    "adaptive_practice": _adaptive, "final_exam_generation": _exam,
    "final_exam_evaluation": _exam_evaluation, "flashcard_generation": _flashcards,
    "flashcard_back_suggestion": _flashcard_suggestions,
    "flashcard_review": _flashcard_review,
    "content_moderation": _content_moderation,
    "report_review": _report_review,
    "competency_extraction": _competencies,
    "vocabulary_page_extraction": _vocabulary_page,
    "image_understanding": _visual_reading,
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
    if task_type not in STRUCTURED_TASKS:
        # Conversational tasks return prose. There is no JSON contract to parse, and
        # attempting to parse one would reject every valid answer. `STRUCTURED_TASKS` is
        # the single source of truth for which tasks have a contract - naming the tasks
        # here as well is how a new conversational task gets validated as if it were JSON.
        return ValidationReport(task_type, prompt_version, True)
    # Reasoning models prepend a <think>...</think> block. parse_json strips it
    # (service.py) but this validator did not, so a reasoning model's ordinary output
    # failed as invalid_json and bought a corrective retry that returned the same
    # shape again. The vision model used for OCR is one of these.
    text = re.sub(r"<think>.*?</think>", "", output_text.strip(), flags=re.DOTALL).strip()
    cleaned = repair_latex_json(re.sub(r"^```(?:json)?\s*|\s*```$", "", text))
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

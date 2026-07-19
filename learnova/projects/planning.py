"""Pure helpers for source-grounded study projects and final exams."""

import re
from collections import Counter
from datetime import date


ALLOWED_QUESTION_TYPES = {
    "multiple_choice", "true_false", "matching", "fill_blank",
    "short_answer", "explanation", "calculation",
}


def clean_extracted_pages(pages):
    """Remove repeated short header/footer lines without changing source meaning."""
    page_lines = []
    candidates = []
    for text in pages:
        lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
        page_lines.append(lines)
        if lines:
            candidates.extend([line for line in (lines[0], lines[-1]) if len(line) <= 100])
    repeated = {line for line, count in Counter(candidates).items() if count >= 3}
    return ["\n".join(line for line in lines if line not in repeated) for lines in page_lines]


def normalize_section(section, position, valid_page_ids):
    source_ids = []
    for value in section.get("source_page_ids", []):
        try:
            page_id = int(value)
        except (TypeError, ValueError):
            continue
        if page_id in valid_page_ids and page_id not in source_ids:
            source_ids.append(page_id)
    if not source_ids:
        raise ValueError("Every section needs at least one valid source page")
    return {
        "position": position,
        "title": str(section.get("title", "Untitled section")).strip()[:255] or "Untitled section",
        "main_topic": str(section.get("main_topic", "")).strip()[:255],
        "learning_goals": list(section.get("learning_goals", []))[:10],
        "important_facts": list(section.get("important_facts", []))[:20],
        "definitions": list(section.get("definitions", []))[:20],
        "formulas": list(section.get("formulas", []))[:15],
        "examples": list(section.get("examples", []))[:15],
        "vocabulary": list(section.get("vocabulary", []))[:25],
        "relationships": list(section.get("relationships", []))[:15],
        "likely_exam_questions": list(section.get("likely_exam_questions", []))[:12],
        "source_page_ids": source_ids,
        "simple_explanation": str(section.get("simple_explanation", "")),
        "standard_explanation": str(section.get("standard_explanation", "")),
        "detailed_explanation": str(section.get("detailed_explanation", "")),
        "estimated_minutes": max(5, min(15, int(section.get("estimated_minutes", 10)))),
        "recall_cards": list(section.get("recall_cards", []))[:20],
    }


def difficulty_distribution(count, mode):
    count = max(1, int(count))
    if mode in {"easy", "medium", "hard"}:
        return {mode: count}
    easy = round(count * 0.30)
    hard = round(count * 0.20)
    medium = count - easy - hard
    return {"easy": easy, "medium": medium, "hard": hard}


def proportional_section_counts(sections, count):
    if not sections:
        return {}
    weights = []
    for section in sections:
        weakness = max(0, 100 - float(section.get("mastery_score", 0)))
        importance = max(1, int(section.get("importance", 1)))
        weights.append(max(1, importance * 10 + weakness))
    allocation = {section["id"]: 0 for section in sections}
    for _ in range(count):
        selected = max(
            range(len(sections)),
            key=lambda index: weights[index] / (allocation[sections[index]["id"]] + 1),
        )
        allocation[sections[selected]["id"]] += 1
    return allocation


def deterministic_question_score(question_type, expected, answer):
    expected_text = str(expected or "").strip()
    answer_text = str(answer or "").strip()
    if not answer_text:
        return 0.0
    if question_type in {"multiple_choice", "true_false", "fill_blank"}:
        return 100.0 if answer_text.casefold() == expected_text.casefold() else 0.0
    if question_type == "matching":
        expected_parts = {part.strip().casefold() for part in re.split(r"[,;\n]+", expected_text) if part.strip()}
        answer_parts = {part.strip().casefold() for part in re.split(r"[,;\n]+", answer_text) if part.strip()}
        return round(100 * len(expected_parts & answer_parts) / max(1, len(expected_parts)), 2)
    if question_type == "calculation":
        expected_numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", expected_text)
        answer_numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", answer_text)
        if expected_numbers and answer_numbers:
            target = float(expected_numbers[-1].replace(",", "."))
            actual = float(answer_numbers[-1].replace(",", "."))
            tolerance = max(0.01, abs(target) * 0.01)
            return 100.0 if abs(actual - target) <= tolerance else 0.0
    return None


def preparation_plan(exam_date, total_sections, completed_sections):
    if not exam_date:
        return None
    remaining_days = max(0, (exam_date - date.today()).days)
    sections_remaining = max(0, total_sections - completed_sections)
    review_days = min(3, max(1, remaining_days // 4)) if remaining_days else 0
    learning_days = max(1, remaining_days - review_days)
    per_day = 0 if not sections_remaining else max(1, -(-sections_remaining // learning_days))
    suggested_exam_offset = max(0, remaining_days - 1)
    return {
        "days_remaining": remaining_days,
        "sections_remaining": sections_remaining,
        "sections_per_day": per_day,
        "review_days": review_days,
        "suggested_final_exam_in_days": suggested_exam_offset,
    }

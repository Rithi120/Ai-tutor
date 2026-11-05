"""Projektmodus teaching engine end to end - the spec's section 10 ("Testing"), every model call mocked.

The tests below go through the real routes (upload, start page, per-page reading, quick-start,
topic confirmation, the workspace, lessons opened from it, /api/answer) with FakeModel standing
in for every provider call.

COVERAGE MATRIX - for each item of section 10, the tests that genuinely cover it.
"flow" = real routes or the app's own entry function (start_section_lesson,
_plan_project_sections, sync_project_flashcards) with a mocked provider. "unit" = a pure helper,
listed as support only, never as coverage of what the student sees.
Files: docs = test_project_documents, extr = test_project_extraction, dash = test_project_dashboard,
eng = test_lesson_teaching_engine, sync = test_project_flashcard_sync, terms = test_project_terms,
strat = test_subject_strategies, cov = test_project_coverage, da = test_document_analysis,
sched = test_project_scheduler, lvl = test_explanation_level, here = this file.

DOCUMENT PROCESSING
* Six uploaded documents
    docs::test_every_document_appears_in_the_report_with_its_own_state  (flow to the report; topics inserted by hand)
    extr::test_six_documents_twelve_pages_give_every_page_a_topic_with_page_references  (quick-start, seeded pages)
    dash::test_the_material_check_shows_each_of_six_documents_with_its_state  (seeded)
    here::test_six_ethics_papers_to_a_learned_topic  (upload to a learned topic)
* Multiple PDFs
    docs::test_every_document_appears_in_the_report_with_its_own_state
    here::test_six_ethics_papers_to_a_learned_topic  (two typed PDFs, two scanned PDFs, a copy)
    unit: da::test_multiple_pdfs_report_page_numbers_inside_each_file
* Handwritten notes
    docs::test_every_document_appears_in_the_report_with_its_own_state  ("Needs OCR review")
    docs::test_the_offline_ocr_reads_a_photo_the_vision_model_could_not
    docs::test_a_page_the_student_confirmed_on_the_review_page_no_longer_needs_checking
    here::test_six_ethics_papers_to_a_learned_topic  (the photo waits after a rate limit, is read on
        "Continue reading" and gives a topic and cards)
* Scanned documents
    docs::test_a_rate_limited_scan_waits_for_vision_instead_of_keeping_only_its_text_layer
    docs::test_a_scans_text_layer_fallback_needs_review_never_read
    docs::test_a_scan_with_a_faint_pencil_note_is_never_skipped_as_blank
    here::test_six_ethics_papers_to_a_learned_topic  (the scan's thought experiment is the lesson taught)
* Multiple pages
    extr::test_six_documents_twelve_pages_give_every_page_a_topic_with_page_references  (batches of four)
    eng::test_the_lesson_prompt_carries_the_last_of_six_files
    here::test_six_ethics_papers_to_a_learned_topic  (7 pages of 5 stored files, all kept in order)
* Partially unreadable pages
    docs::test_every_document_appears_in_the_report_with_its_own_state  (partly read; excluding the page completes it)
    extr::test_a_page_that_could_not_be_read_never_feeds_the_extraction
    here::test_six_ethics_papers_to_a_learned_topic  ("Document 5: Partly analysed"; the page is never analysed)
* Duplicate content
    docs::test_every_document_appears_in_the_report_with_its_own_state
    extr::test_duplicate_pages_are_left_out_and_reported
    here::test_six_ethics_papers_to_a_learned_topic  (the copy is named in the material check, the repeated
        page adds no topic)
    unit: da::test_exact_duplicate, da::test_near_duplicate_with_an_ocr_slip_hyphenation_and_another_page_number
* Missing topics
    extr::test_one_coverage_repair_pass_and_pages_that_hold_no_topic
    extr::test_a_topic_citing_no_page_of_its_batch_is_counted_not_lost_silently
    extr::test_topics_dropped_over_the_cap_keep_their_pages_for_the_one_tap_add
    dash::test_a_missing_topic_is_added_with_the_pages_the_student_picks
    dash::test_topics_dropped_over_the_cap_are_stated_and_can_be_added_back
    dash::test_a_coverage_check_names_topics_left_out_by_the_cap
    here::test_six_ethics_papers_to_a_learned_topic  (topics of pages read later are added; none dropped)
* Topic completeness
    dash::test_a_project_is_complete_only_when_every_check_passes
    dash::test_an_unread_document_keeps_the_project_incomplete_and_says_what_is_missing
    dash::test_select_none_and_select_all
    here::test_six_ethics_papers_to_a_learned_topic  (5 of 5 topics, all ticked by default, no page uncovered;
        incomplete while topics are untaught)
    unit: cov::test_every_included_topic_must_be_taught_and_understood
* Source references
    extr::test_the_topic_map_is_stored_with_terms_carrying_page_ids_and_in_notes_flags
    dash::test_every_topic_shows_status_importance_terms_source_pages_and_next_activity
    eng::test_parts_name_the_file_of_the_page_they_cite
    sync::test_one_set_per_project_with_source_pages
    here::test_six_ethics_papers_to_a_learned_topic  (topic -> its page; "[scan.pdf, p. 1]" in the lesson;
        "handschrift.png p. 1" on the card and in the workspace)

SUBJECT TEACHING
* Ethics explanations
    eng::test_an_ethics_lesson_retells_the_thought_experiment_from_the_notes_and_marks_additions
    here::test_six_ethics_papers_to_a_learned_topic  (opened from the workspace: the whole Ethics sequence)
* Ethics terminology extraction
    extr::test_the_v4_prompt_shows_the_shape_and_the_subject_focus
    extr::test_the_topic_map_is_stored_with_terms_carrying_page_ids_and_in_notes_flags
    eng::test_a_planned_terms_part_the_model_left_out_is_built_from_the_topic_map
    here::test_six_ethics_papers_to_a_learned_topic  (the scan's Fachbegriffe reach the prompt, the key-terms
        part and the cards)
* Thought experiment interpretation
    eng::test_the_notes_example_is_retold_when_the_extraction_called_it_an_example
    eng::test_the_notes_story_is_retold_when_the_extraction_called_it_a_theory
    eng::test_interpretations_the_notes_do_not_support_are_marked_as_added
    eng::test_an_ethics_topic_without_a_thought_experiment_gets_none
    here::test_six_ethics_papers_to_a_learned_topic  (retold in order, what it shows, how it serves the
        objective; a reading the notes do not give stays an addition)
    unit: strat::test_a_reading_the_notes_do_not_give_is_not_from_the_notes
* English grammar exercises
    eng::test_an_english_grammar_lesson_teaches_rule_structure_examples_and_mistakes  (question 2 a dropdown,
        feedback names the broken rule)
    here::test_language_cards_ask_for_forms_patterns_and_rules
* Mathematics worked examples
    eng::test_a_maths_lesson_shows_a_worked_example_before_the_guided_exercise
    eng::test_coverage_and_the_flashcards_part_are_stored
    unit: test_lesson_worked_example::test_a_normal_example_still_passes  (the worked-example contract)
* NWT experiment explanations
    eng::test_an_nwt_experiment_is_split_into_the_lab_report_phases
    eng::test_nwt_combines_the_science_and_technology_methods
* History chronology and cause/effect
    eng::test_a_history_lesson_puts_the_chronology_before_causes_and_effects  (the plan's order only)
    here::test_a_history_lesson_builds_the_chronology_then_causes_and_consequences  (a timeline the model gives
        out of order is shown in chronological order - defect fixed with this test; question 2 is an ordering
        task; mistakes are placed back in the chronology)
    here::test_history_cards_ask_for_events_dates_and_causes
    unit: here::test_timeline_order_is_repaired_only_when_every_date_is_clear
* Geography map and process understanding
    eng::test_a_geography_lesson_explains_the_process_and_reads_the_diagram  (the plan only)
    here::test_a_geography_lesson_reads_the_map_and_explains_the_process_step_by_step
    here::test_a_geography_topic_without_a_map_gets_no_map_reading
* German text interpretation
    eng::test_a_german_lesson_includes_a_model_analysis  (the plan only)
    here::test_a_german_lesson_interprets_the_text_with_evidence_before_the_student_tries
* Other available subjects
    eng::test_music_and_computer_science_get_their_own_methods
    eng::test_a_typed_topic_without_notes_uses_the_method_but_nothing_is_from_the_notes  (Politics)
    here::test_other_subjects_get_their_own_method  (Religion, Art, Physical Education, Technology)
    unit: strat::test_every_choice_resolves_to_itself_and_a_strategy

FLASHCARDS
* Automatic vocabulary extraction
    sync::test_the_analysis_syncs_the_cards_and_a_failing_sync_never_costs_the_topics
    here::test_six_ethics_papers_to_a_learned_topic, here SubjectCardTests  (upload -> quick-start makes the
        set before any lesson or request)
* Correct definitions
    sync::test_one_set_per_project_with_source_pages
    test_project_engine_contracts::test_every_term_in_the_notes_gets_its_definition_card_and_nothing_invented
    sync::test_an_item_detail_the_notes_do_not_contain_is_marked_as_added
    here::test_six_ethics_papers_to_a_learned_topic  (the spec's "Werte" card: simple definition, notes' example)
    unit: terms::test_definition_card_from_notes, terms::test_terms_not_in_the_notes_or_without_definition_get_no_card
* Source references
    sync::test_one_set_per_project_with_source_pages
    here::test_six_ethics_papers_to_a_learned_topic, here::test_history_cards_ask_for_events_dates_and_causes
* Subject-specific card formats
    sync::test_two_formulas_stay_two_cards  (Mathematics)
    sync::test_one_set_per_project_with_source_pages  (Ethics person card)
    here::test_history_cards_ask_for_events_dates_and_causes, here::test_language_cards_ask_for_forms_patterns_and_rules
    unit: terms::test_history_cards_events_and_dates, terms::test_sentence_patterns_and_irregular_forms_become_language_cards
* Duplicate prevention
    sync::test_a_front_the_set_already_has_is_not_added_twice
    sync::test_a_resync_adds_nothing_and_keeps_the_students_edits_and_deletions
    sync::test_two_formulas_stay_two_cards
    sync::test_the_analysis_syncs_the_cards_and_a_failing_sync_never_costs_the_topics  (a second analysis adds none)
    unit: terms::test_same_term_in_two_topics_is_one_card_with_both_sources

ADAPTIVE LEARNING
* Different class levels
    eng::test_a_project_for_another_class_is_pitched_at_that_class
    here::test_a_lower_class_gets_the_simpler_level_and_the_class_label_reaches_the_prompt
    unit: lvl::test_klasse_2_addition_is_concrete, lvl::test_klasse_9_negative_numbers_get_standard_reasoning
* Different knowledge levels
    eng::test_a_beginner_and_a_secure_student_get_different_plans
    lvl::test_strong_student_lesson_prompt_is_not_elementary
    here::test_six_ethics_papers_to_a_learned_topic  (a learned topic reopens as a short review)
* Incorrect answers
    eng::test_a_first_wrong_answer_gets_a_reteach_and_the_same_question_again
    eng::test_the_page_cannot_ask_for_a_second_retry
    dash::test_a_session_with_every_answer_wrong_does_not_mark_the_topic_learned
    lvl::test_incorrect_answers_update_the_teaching_strategy
    here::test_six_ethics_papers_to_a_learned_topic, here::test_a_german_lesson_interprets_the_text_with_evidence_before_the_student_tries
* Correct answers
    eng::test_a_correct_first_answer_moves_on
    dash::test_a_correct_session_marks_the_topic_learned  (seeded attempts)
    here::test_six_ethics_papers_to_a_learned_topic
* Topic mastery
    dash::test_a_wrong_first_answer_that_is_retaught_and_then_answered_right_counts
    dash::test_learned_counts_and_revision_need_demonstrated_understanding
    dash::test_an_exam_score_below_60_does_not_mark_a_topic_learned_and_exam_answers_are_evidence
    here::test_six_ethics_papers_to_a_learned_topic  (not learned after opening the lesson, the wrong answer or
        the right retry; learned and understood only when the session ends with shown performance)
    unit: cov::test_opening_or_clicking_through_a_lesson_is_only_taught
* Revision scheduling
    dash::test_learned_counts_and_revision_need_demonstrated_understanding  (seeded)
    here::test_six_ethics_papers_to_a_learned_topic  (after a real session: a later revision date, a review task
        in the plan, "Revision dd.mm." on the topic and the topic in the revision list)
    unit: sched::test_independent_success_lengthens_hints_keep_and_errors_reset,
        sched::test_day_four_revisits_day_one_topics_at_the_three_day_interval
"""

import io
import json
import os
import re
import tempfile
import unittest
from datetime import date
from html import unescape
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_teaching_engine_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pymupdf  # noqa: E402  pyright: ignore[reportMissingImports]
from PIL import Image, ImageDraw  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.lessons import lesson_plan  # noqa: E402
from learnova.lessons.strategies import REGISTRY  # noqa: E402
from learnova.projects.terms import term_key  # noqa: E402

db = application.db
_MISSING = object()
PASSWORD = "correct-horse-battery"


# ---------------------------------------------------------------------------- material

class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def png(label="", colour="white", size=(900, 1200)):
    image = Image.new("RGB", size, colour)
    if label:
        ImageDraw.Draw(image).text((60, 80), label, fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def text_pdf(*pages):
    """A born-digital PDF: every page has a text layer."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    for text in pages:
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        page.insert_textbox(pymupdf.Rect(60, 60, 540, 760), text, fontsize=12)
    data = document.tobytes()
    document.close()
    return data


def scanned_pdf(pages=1):
    """Pages that are one full-page picture each, with no text layer (a scanner app)."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    for index in range(pages):
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        page.insert_image(page.rect, stream=png(f"scan {index + 1}", "#f4f1ea", (850, 1100)))
    data = document.tobytes()
    document.close()
    return data


def reading(text, confidence=0.9):
    """What the vision model returns for one page."""

    return FakeResponse({"blocks": [{
        "type": "handwriting", "content": text, "confidence": confidence, "bbox": [],
        "crossed_out": False, "important_candidate": False, "teacher_highlight_candidate": False,
        "nearby_text": ""}], "detected_page_number": "", "warning": ""})


EMPTY_READING = FakeResponse({"blocks": [], "detected_page_number": "", "warning": "blank"})
RATE_LIMIT = ai_service.AIProviderError("provider_rate_limit", "The AI provider rate limit was reached.")

# Six Ethik papers. The PDFs are typed (no umlauts: the PDF base font), the photo and the
# scans are read by the (mocked) vision model.
UTILITARISMUS = (
    "Utilitarismus nach Bentham. Der Utilitarismus bewertet Handlungen nach ihren Folgen. Eine Handlung ist "
    "moralisch richtig, wenn sie das groesste Glueck der groessten Zahl bewirkt. Hedonistisches Kalkuel: eine "
    "Rechnung, die Freude und Leid gegeneinander abwaegt. John Stuart Mill unterschied spaeter hoehere und "
    "niedere Freuden und verteidigte damit den Utilitarismus gegen Kritik.")
KANT = (
    "Pflichtethik nach Kant. Immanuel Kant begruendet die Moral nicht mit den Folgen, sondern mit der Pflicht. "
    "Maxime: der subjektive Grundsatz des Wollens. Der kategorische Imperativ verlangt, nur nach der Maxime zu "
    "handeln, von der man zugleich wollen kann, dass sie ein allgemeines Gesetz werde. Der Mensch darf nie nur "
    "als Mittel gebraucht werden.")
WERTE = (
    "Werte und Normen\nWerte sind Vorstellungen davon, was Menschen wichtig und richtig finden.\n"
    "Beispiel: Freiheit, Gerechtigkeit und Respekt.\n"
    "Normen sind Regeln, die sagen, wie man in einer Gruppe handeln soll.")
RAWLS = (
    "Gerechtigkeit nach Rawls\nGedankenexperiment: der Schleier des Nichtwissens. Menschen legen die Regeln "
    "einer Gesellschaft fest, ohne zu wissen, ob sie später arm oder reich, gesund oder krank sein werden.\n"
    "Urzustand: eine gedachte Lage vor jeder Gesellschaftsordnung.\n"
    "Differenzprinzip: Ungleichheiten sind nur gerecht, wenn sie den am wenigsten Begünstigten nützen.")
FREIHEIT = (
    "Freiheit und Verantwortung\nWillensfreiheit: die Fähigkeit, sich selbst für eine Handlung zu entscheiden.\n"
    "Wer frei handelt, trägt Verantwortung für die Folgen seines Handelns.")

# What a well-behaved extraction model finds on a page, keyed by the page's heading.
# (title, [(term, definition, simple definition, quote on the page, example)], [(kind, name, detail)])
TOPIC_TABLE = [
    ("Utilitarismus nach Bentham",
     [("Hedonistisches Kalkuel", "eine Rechnung, die Freude und Leid gegeneinander abwaegt",
       "Freude und Leid werden verrechnet",
       "Hedonistisches Kalkuel: eine Rechnung, die Freude und Leid gegeneinander abwaegt", "")],
     [("person", "John Stuart Mill", "unterschied hoehere und niedere Freuden")]),
    ("Pflichtethik nach Kant",
     [("Maxime", "der subjektive Grundsatz des Wollens", "die Regel, nach der ich handeln will",
       "Maxime: der subjektive Grundsatz des Wollens", "")],
     [("theory", "Kategorischer Imperativ", "nur nach der Maxime handeln, die ein allgemeines Gesetz werden kann")]),
    ("Werte und Normen",
     [("Werte", "Vorstellungen davon, was Menschen wichtig und richtig finden",
       "Werte sind Vorstellungen davon, was Menschen wichtig und richtig finden.",
       "Werte sind Vorstellungen davon, was Menschen wichtig und richtig finden",
       "Freiheit, Gerechtigkeit und Respekt"),
      ("Normen", "Regeln, die sagen, wie man in einer Gruppe handeln soll", "Regeln für das Handeln",
       "Normen sind Regeln, die sagen, wie man in einer Gruppe handeln soll", "")],
     []),
    ("Gerechtigkeit nach Rawls",
     [("Urzustand", "eine gedachte Lage vor jeder Gesellschaftsordnung", "eine ausgedachte Lage vor allen Regeln",
       "Urzustand: eine gedachte Lage vor jeder Gesellschaftsordnung", ""),
      ("Differenzprinzip", "Ungleichheiten sind nur gerecht, wenn sie den am wenigsten Begünstigten nützen",
       "Ungleichheit nur, wenn sie den Schwächsten hilft",
       "Differenzprinzip: Ungleichheiten sind nur gerecht, wenn sie den am wenigsten Begünstigten nützen", "")],
     [("thought_experiment", "Schleier des Nichtwissens",
       "Menschen legen die Regeln einer Gesellschaft fest, ohne zu wissen, ob sie später arm oder reich sein werden"),
      ("person", "Rawls", "begründet Gerechtigkeit mit dem Schleier des Nichtwissens")]),
    ("Freiheit und Verantwortung",
     [("Willensfreiheit", "die Fähigkeit, sich selbst für eine Handlung zu entscheiden",
       "selbst entscheiden können", "Willensfreiheit: die Fähigkeit, sich selbst für eine Handlung zu entscheiden",
       "")],
     []),
]
TITLES = [title for title, _terms, _items in TOPIC_TABLE]


def batch_of(prompt):
    """The pages one extraction call was given (the JSON between "Pages: " and the shape)."""

    match = re.search(r"\nPages: (\[.*\])\nReturn JSON exactly as ", prompt, flags=re.DOTALL)
    assert match is not None, "the extraction prompt carries the pages as JSON"
    return json.loads(match.group(1))


def topic_for(entry, table=None):
    text = " ".join(str(entry["text"]).split())
    for title, terms, items in (TOPIC_TABLE if table is None else table):
        if title not in text:
            continue
        page_id = entry["page_id"]
        return {
            "title": title, "main_topic": "Ethik", "kind": "main",
            "summary": f"{title}: was die Notizen dazu sagen.", "simple_summary": f"{title} kurz erklärt.",
            "learning_goals": [f"{title} erklären"], "source_page_ids": [page_id],
            "terms": [{"term": term, "definition": definition, "simple_definition": simple, "example": example,
                       "page_id": page_id, "quote": quote} for term, definition, simple, quote, example in terms],
            "items": [{"kind": kind, "name": name, "detail": detail, "page_id": page_id}
                      for kind, name, detail in items],
            "exam_questions": [f"Erkläre {title}."], "related_topics": [], "prerequisites": [],
            "difficulty": "medium", "importance": "high" if "Rawls" in title else "medium", "estimated_minutes": 15}
    return None


def question(concept, *, qid="q1"):
    return {"id": qid, "concept": concept, "difficulty": 1, "type": "multiple_choice",
            "prompt": "Was soll das Gedankenexperiment sicherstellen?", "hint": "Denk an die eigene Stellung.",
            "options": [{"id": "a", "label": "Niemand bevorzugt die eigene Stellung"},
                        {"id": "b", "label": "Die Reichen bestimmen die Regeln"},
                        {"id": "c", "label": "Alle bekommen gleich viel Geld"},
                        {"id": "d", "label": "Regeln gelten nur für Arme"}],
            "expected_answer": "a"}


def part(kind, body="", *, items=(), title="", from_notes=True, pages=(1,), answer=""):
    return {"kind": kind, "title": title or kind, "body": body, "items": list(items), "answer": answer,
            "from_notes": from_notes, "source_pages": list(pages)}


def lesson_payload(parts, concept):
    return {"lesson_title": concept, "detected_level": "Klasse 9", "concepts": [{"name": concept, "evidence": "notes"}],
            "parts": parts, "explanation": "", "worked_example": None, "teacher_tips": [], "exceptions": [],
            "question": question(concept)}


RAWLS_RETELLING = ("Menschen legen die Regeln einer Gesellschaft fest, ohne zu wissen, ob sie später arm oder reich, "
                   "gesund oder krank sein werden. Diese gedachte Lage vor jeder Gesellschaftsordnung heißt Urzustand.")


def rawls_parts():
    """An Ethics lesson as the method asks: terms, the thought experiment retold from the
    notes, what it shows, the question behind it, a reading the notes do not give (marked),
    then practice."""

    return [
        part("objective", "Du kannst erklären, was das Gedankenexperiment vom Schleier des Nichtwissens über "
                          "Gerechtigkeit zeigt und warum es für die Klausur wichtig ist."),
        part("terms", items=[{"label": "Urzustand", "text": "eine gedachte Lage vor jeder Gesellschaftsordnung"},
                             {"label": "Differenzprinzip", "text": "Ungleichheiten sind nur gerecht, wenn sie den am "
                                                                   "wenigsten Begünstigten nützen"}]),
        part("thought_experiment", RAWLS_RETELLING, title="Der Schleier des Nichtwissens"),
        part("theory", "Rawls folgert daraus das Differenzprinzip: Ungleichheiten sind nur gerecht, wenn sie den am "
                       "wenigsten Begünstigten nützen."),
        part("argument", "Die zentrale Frage: Welche Regeln für eine Gesellschaft sind gerecht?"),
        part("perspectives", "Man kann den Schleier auch als Bild für einen unparteiischen Richter lesen.",
             from_notes=False, title="Zusätzliche Deutung (nicht aus deinen Notizen)"),
        part("guided_exercise", "Welche Regel würdest du hinter dem Schleier wählen? Begründe.",
             answer="Eine Regel, die auch dann fair ist, wenn ich zu den am wenigsten Begünstigten gehöre."),
        part("summary", "Der Schleier des Nichtwissens zeigt, wie man Regeln ohne Eigennutz prüfen kann."),
    ]


def generic_parts(title):
    return [part("objective", f"Du kannst {title} erklären."), part("explanation", "Erklärung aus den Notizen."),
            part("guided_exercise", "Erkläre es mit eigenen Worten.", answer="..."), part("summary", "Kurz.")]


def evaluation(score, concept, *, feedback="Geprüft.", correction="Richtig ist a.", retry=None, qid="q2"):
    payload: dict[str, Any] = {
        "evaluation": {"is_correct": score >= 80, "score": score, "feedback": feedback,
                       "correction": "" if score >= 80 else correction, "teacher_tip": "Lies die Stelle noch einmal.",
                       "exception_note": "", "skill_status": "mastered" if score >= 80 else "needs_practice"},
        "next_question": {"id": qid, "concept": concept, "difficulty": 1, "type": "text",
                          "prompt": "Erkläre das Gedankenexperiment mit eigenen Worten.", "hint": "Wer weiß was?",
                          "options": [], "expected_answer": "Niemand kennt seine spätere Stellung."},
        "summary": None,
    }
    if retry is not None:
        payload["retry"] = retry
    return payload


def diagnosis(correct, concept):
    return {
        "analysis_version": "diagnosis:v2",
        "correctness_status": "correct" if correct else "incorrect",
        "score": {"points": 1.0 if correct else 0.2, "max_points": 1.0, "rubric_evidence": [
            {"criterion": "Kernaussage", "met": correct, "evidence_ids": ["e1"]}]},
        "concepts_assessed": [concept],
        "primary_diagnosis": {"tag": "correct" if correct else "interpretation_error",
                              "statement": "Richtig gelesen." if correct else "Die Pointe wurde verfehlt.",
                              "evidence_ids": ["e1"]},
        "secondary_diagnoses": [],
        "evidence": [{"id": "e1", "source": "student_answer", "quote": "b"}],
        "prerequisite_gaps": [],
        "missing_evidence": False,
        "confidence": {"value": 0.9, "basis": "Antwort liegt vor."},
        "recommended_intervention": "increase_difficulty" if correct else "worked_example_then_practice",
        "candidate_question_constraints": {"concept": concept, "difficulty": 1},
        "student_facing_explanation": "Gut gelesen." if correct else "Schau dir an, wer was weiß.",
        "internal_diagnostic_summary": "note",
    }


class FakeModel:
    """Every model call of the app, answered like a well-behaved provider (nothing is reached).

    `readings` is the queue of vision answers (an exception in it is raised), `evaluations`
    the queue of grading answers (empty: a correct answer). `lessons` maps a topic title to
    the parts its lesson returns; other topics get generic parts."""

    def __init__(self):
        self.calls: list[dict[str, Any]] = []
        self.readings: list[Any] = []
        self.evaluations: list[dict[str, Any]] = []
        self.lessons: dict[str, Any] = {}
        self.topic_table = TOPIC_TABLE
        self.last_score = 100

    def count(self, task_type):
        return sum(1 for call in self.calls if call["task_type"] == task_type)

    def last(self, task_type):
        return next(call for call in reversed(self.calls) if call["task_type"] == task_type)

    def lesson_call(self, title):
        return next(call for call in reversed(self.calls)
                    if call["task_type"] == "lesson_generation" and f"\nTopic: {title}\n" in call["input"])

    def __call__(self, *, task_type, **kwargs):
        self.calls.append({"task_type": task_type, **kwargs})
        if task_type == "ocr_document_recognition":
            if not self.readings:
                raise AssertionError("an unexpected vision call")
            answer = self.readings.pop(0)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        if task_type == "project_section_generation":
            batch = batch_of(str(kwargs.get("input", "")))
            topics = [topic_for(entry, self.topic_table) for entry in batch]
            return FakeResponse({"sections": [topic for topic in topics if topic]})
        if task_type == "lesson_generation":
            match = re.search(r"^Topic: (.+)$", str(kwargs.get("input", "")), flags=re.MULTILINE)
            title = match.group(1).strip() if match else "Thema"
            parts = self.lessons.get(title)
            parts = parts() if callable(parts) else parts
            return FakeResponse(lesson_payload(parts if parts is not None else generic_parts(title), title))
        if task_type == "answer_evaluation":
            payload = self.evaluations.pop(0) if self.evaluations else evaluation(100, "Thema", feedback="Richtig.")
            self.last_score = payload["evaluation"]["score"]
            return FakeResponse(payload)
        if task_type == "answer_diagnosis":
            return FakeResponse(diagnosis(self.last_score >= 80, "Thema"))
        if task_type == "diagnosis_verification":
            return FakeResponse({"agrees": True, "reason": "supported", "better_tag": ""})
        if task_type in ("question_generation", "adaptive_practice"):
            number = len(self.calls)
            return FakeResponse({"question": {**question("Thema", qid=f"g{number}"), "type": "text",
                                              "prompt": f"Frage {number}: Erkläre einen Gedanken aus der Stunde.",
                                              "options": [], "expected_answer": "Niemand kennt seine Stellung."}})
        raise AssertionError(f"unexpected AI task {task_type}")


class EngineCase(unittest.TestCase):
    maxDiff = None
    CONFIG_KEYS = ("TESTING", "AI_MODE", "WTF_CSRF_ENABLED", "CURRICULUM_CLEARED_SOURCES",
                   "SUBSCRIPTION_QUOTAS_ENFORCED", "RATELIMIT_ENABLED", "FEATURE_PRIVATE_FLASHCARDS",
                   "FEATURE_ADAPTIVE_DIAGNOSTICS", "FEATURE_DIAGNOSTIC_VERIFICATION", "TEST_MIN_QUESTIONS",
                   "TEST_MAX_QUESTIONS", "KNOWLEDGE_TARGET")

    def setUp(self):
        self.saved_config = {key: application.app.config.get(key, _MISSING) for key in self.CONFIG_KEYS}
        self.saved_limiter = application.limiter.enabled
        application.app.config.update(
            TESTING=True, AI_MODE="cached", WTF_CSRF_ENABLED=False, CURRICULUM_CLEARED_SOURCES="",
            SUBSCRIPTION_QUOTAS_ENFORCED=False, RATELIMIT_ENABLED=False, FEATURE_PRIVATE_FLASHCARDS=True,
            FEATURE_ADAPTIVE_DIAGNOSTICS=True, FEATURE_DIAGNOSTIC_VERIFICATION=True, TEST_MIN_QUESTIONS=3,
            TEST_MAX_QUESTIONS=15, KNOWLEDGE_TARGET=80)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        application._curriculum_index_cache.update(key=None, index=None)
        ai_service._RECENT_RESULTS.clear()
        ai_service._RESERVATIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lea", "email": "lea@example.com", "password": PASSWORD,
                                            "grade": "10", "language": "en"})
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == "lea"))
            assert user is not None
            self.user_id = user.id
        self.model = FakeModel()

    def tearDown(self):
        # Later test modules in the same process expect the app's own settings.
        for key, value in self.saved_config.items():
            if value is _MISSING:
                application.app.config.pop(key, None)
            else:
                application.app.config[key] = value
        application.limiter.enabled = self.saved_limiter
        application.SESSIONS.clear()

    # ------------------------------------------------------------------ requests
    def post(self, path, **kwargs):
        with patch.object(application, "create_response", side_effect=self.model):
            return self.client.post(path, **kwargs)

    def get(self, path):
        with patch.object(application, "create_response", side_effect=AssertionError("a page view called a model")):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return response

    def page(self, project_id):
        return unescape(self.get(f"/projects/{project_id}").get_data(as_text=True))

    def section_of(self, html, anchor, end="</section>"):
        start = html.index(f'id="{anchor}"')
        return html[start:html.index(end, start)]

    def answer(self, session_id, given, *, retry_count=0):
        response = self.post("/api/answer", json={"session_id": session_id, "answer": given, "hints_used": False,
                                                  "retry_count": retry_count, "response_confidence": 60})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        return response.get_json()

    def start_topic(self, project_id, section_id):
        """The student taps a topic's button in the workspace; returns the lesson's session id."""

        response = self.post(f"/projects/{project_id}/topics/{section_id}/start")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:400])
        location = response.headers["Location"]
        self.assertIn("session_id=", location, location)
        return location.rsplit("session_id=", 1)[1]

    def context(self, project_id):
        with application.app.test_request_context():
            project = db.session.get(application.LearningProject, project_id)
            return application.project_workspace_context(project)

    def sections(self, project_id):
        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            return {section.title: section.id for section in project.sections}

    def section_row(self, section_id):
        section = db.session.get(application.LearningSection, section_id)
        assert section is not None
        return section

    def planned_kinds(self, prompt):
        return re.findall(r"^\d+\. ([a-z_]+) — ", prompt, flags=re.MULTILINE)

    # ------------------------------------------------------------------ seeded projects
    def seed_topic(self, subject, files, topic, *, grade="", title="Projekt"):
        """A confirmed project with one topic on these pages. files: [(filename, [page text])];
        topic: a topic map (title, goals and the map's keys) or a callable(page_ids) giving one."""

        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id, title=title, subject=subject, grade=grade,
                                                  status="planned", topics_confirmed=True)
            db.session.add(project)
            db.session.flush()
            order, page_ids = 0, []
            for filename, texts in files:
                upload = application.ProjectFile(project_id=project.id, original_filename=filename,
                                                 mime_type="application/pdf", original_data=b"%PDF", sha256=filename)
                db.session.add(upload)
                db.session.flush()
                for number, text in enumerate(texts, 1):
                    order += 1
                    page = application.ProjectPage(
                        project_id=project.id, file_id=upload.id, page_number=number, page_order=order,
                        extracted_text=text, extraction_status="ready", confidence_status="high",
                        recognition_confidence=0.95, review_status="confirmed", processing_stage="ready",
                        recognition_json=json.dumps({"text_source": "vision"}))
                    db.session.add(page)
                    db.session.flush()
                    page_ids.append(page.id)
            raw = topic(page_ids) if callable(topic) else topic
            spec: dict[str, Any] = dict(cast(dict[str, Any], raw))
            name = str(spec.pop("title"))
            goals = spec.pop("goals", [f"{name} erklären"])
            section = application.LearningSection(
                project_id=project.id, position=1, title=name, main_topic=name,
                learning_goals_json=json.dumps(goals), source_page_ids_json=json.dumps(page_ids),
                topic_map_json=json.dumps(spec, ensure_ascii=False), topic_key=term_key(name) or "thema",
                estimated_minutes=15)
            db.session.add(section)
            db.session.commit()
            return project.id, section.id, page_ids


def term_entry(name, definition, page_id, *, example=""):
    return {"term": name, "definition": definition, "simple_definition": definition, "example": example,
            "page_id": page_id, "page_ids": [page_id], "quote": "", "in_notes": True, "definition_from_notes": True,
            "key": term_key(name)}


def item_entry(kind, name, detail, page_id):
    return {"kind": kind, "name": name, "detail": detail, "page_id": page_id, "in_notes": True}


# ---------------------------------------------------------------------------- the journey

class EthicsProjectJourneyTests(EngineCase):
    """One student, six Ethik papers, from the upload to a learned topic - through the real
    routes, every model call answered by FakeModel."""

    def upload(self):
        second = text_pdf(UTILITARISMUS)        # its only page repeats page 1 of ethik-1.pdf
        files = [
            ("ethik-1.pdf", text_pdf(UTILITARISMUS, KANT), "application/pdf"),
            ("ethik-2.pdf", second, "application/pdf"),
            ("handschrift.png", png("Werte und Normen"), "image/png"),
            ("scan.pdf", scanned_pdf(1), "application/pdf"),
            ("teilweise.pdf", scanned_pdf(2), "application/pdf"),
            ("ethik-2-kopie.pdf", second, "application/pdf"),       # a byte-identical copy
        ]
        return self.post("/projects", data={
            "title": "Ethik Klausur", "subject": "Other", "subject_other": "Ethik", "grade": "9",
            "materials": [(io.BytesIO(content), name, mime) for name, content, mime in files]},
            content_type="multipart/form-data")

    def read(self, project_id, page_id, readings=()):
        self.model.readings = list(readings)
        before = self.model.count("ocr_document_recognition")
        response = self.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={})
        return response, self.model.count("ocr_document_recognition") - before

    def start_page_ids(self, project_id):
        html = self.get(f"/projects/{project_id}/start?auto=1").get_data(as_text=True)
        match = re.search(r'data-page-ids="([^"]*)"', html)
        assert match is not None, "the start page lists the pages its script reads"
        return json.loads(unescape(match.group(1))), html

    def quick_start(self, project_id):
        response = self.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return response.get_json()

    def report(self, project_id):
        return self.get(f"/projects/{project_id}/analysis").get_json()

    def test_six_ethics_papers_to_a_learned_topic(self):
        # ---- upload: six documents, the copy is kept once and said
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 302, uploaded.get_data(as_text=True)[:300])
        self.assertIn("/start?auto=1", uploaded.headers["Location"])
        with application.app.app_context():
            project = db.session.scalars(db.select(application.LearningProject)).one()
            project_id = project.id
            self.assertEqual((project.subject, project.grade), ("Ethics", "9"))
            pages = sorted(project.pages, key=lambda item: item.page_order)
            self.assertEqual([(page.source_file.original_filename, page.page_number) for page in pages], [
                ("ethik-1.pdf", 1), ("ethik-1.pdf", 2), ("ethik-2.pdf", 1), ("handschrift.png", 1),
                ("scan.pdf", 1), ("teilweise.pdf", 1), ("teilweise.pdf", 2)], "every page of every file is kept")
            a1, a2, b1, photo, scan, part1, part2 = [page.id for page in pages]

        # ---- start page, first pass: one request per page; the provider is busy at the photo
        page_ids, start_html = self.start_page_ids(project_id)
        self.assertEqual(page_ids, [a1, a2, b1, photo, scan, part1, part2])
        self.assertEqual(start_html.count('class="start-file" data-file-id='), 5)
        self.assertIn("“ethik-2-kopie.pdf” was uploaded twice; Learnova kept one copy.", start_html)
        for page_id in (a1, a2, b1):
            response, calls = self.read(project_id, page_id)
            self.assertEqual((response.status_code, response.get_json()["state"], calls), (200, "read", 0),
                             "a born-digital page is read from its text layer")
        response, calls = self.read(project_id, photo, [RATE_LIMIT])
        self.assertEqual((response.status_code, response.get_json()["state"], calls), (202, "waiting", 1),
                         "a rate limit costs one call and the page waits instead of failing")
        # The script stops reading, refreshes the summary and asks for the topics of what was read.
        report = self.report(project_id)
        self.assertEqual([file["reading_state"] for file in report["files"]],
                         ["read", "read", "waiting", "pending", "pending"])
        started = self.quick_start(project_id)
        self.assertEqual(self.model.count("ocr_document_recognition"), 1, "quick-start does not knock again")
        self.assertTrue(started["review"])
        self.assertTrue(started["redirect"].endswith(f"/projects/{project_id}#topics"))
        self.assertEqual(list(self.sections(project_id)), TITLES[:2], "the duplicate page adds no topic")
        html = self.page(project_id)
        banner = self.section_of(html, "readingPaused")
        self.assertIn("4 page(s) in 3 document(s) are not read yet", banner)
        self.assertIn(f"/projects/{project_id}/start?auto=1", banner)

        # ---- "Continue reading": the start page reads the rest
        page_ids, _ = self.start_page_ids(project_id)
        self.assertEqual(page_ids, [photo, scan, part1, part2], "only the pages not read yet")
        response, _ = self.read(project_id, photo, [reading(WERTE, 0.4)] * 3)
        self.assertEqual((response.status_code, response.get_json()["state"]), (200, "needs_review"))
        self.assertEqual(response.get_json()["file"]["reading_message"], "Document 3: Needs OCR review")
        response, calls = self.read(project_id, scan, [reading(RAWLS)])
        self.assertEqual((response.status_code, response.get_json()["state"], calls), (200, "read", 1),
                         "the scanned page is read by vision")
        response, _ = self.read(project_id, part1, [reading(FREIHEIT)])
        self.assertEqual(response.get_json()["state"], "read")
        response, _ = self.read(project_id, part2, [EMPTY_READING] * 3)
        self.assertEqual((response.status_code, response.get_json()["state"]), (422, "unreadable"))
        self.assertIn("teilweise.pdf", response.get_json()["message"])
        report = self.report(project_id)
        self.assertEqual([file["reading_state"] for file in report["files"]],
                         ["read", "read", "needs_review", "read", "partly_read"])
        started = self.quick_start(project_id)
        self.assertTrue(started["review"])

        # ---- material check: six documents with their states, above every topic, all included
        html = self.page(project_id)
        check = html[html.index('id="materialCheck"'):html.index('id="topics"')]
        context = self.context(project_id)
        rows = context["material_rows"]
        self.assertEqual([row["filename"] for row in rows],
                         ["ethik-1.pdf", "ethik-2.pdf", "handschrift.png", "scan.pdf", "teilweise.pdf"])
        self.assertEqual([row["state"] for row in rows],
                         ["analysed", "analysed", "needs_ocr_review", "analysed", "partly_analysed"])
        for row in rows:
            self.assertIn(row["message"], check)
            self.assertIn(row["filename"], check)
        self.assertIn("Document 3: Needs OCR review", check)
        self.assertIn("Document 5: Partly analysed", check)
        self.assertIn("“ethik-2-kopie.pdf” was uploaded twice; Learnova kept one copy.", check,
                      "the sixth document, a copy, is named too")
        self.assertNotIn('id="readingPaused"', html, "nothing is left unread")
        titles = list(self.sections(project_id))
        self.assertEqual(titles, TITLES, "every topic of every read page, none dropped")
        self.assertIn(f"We found {len(TITLES)} topics in your notes", html)
        self.assertEqual(html.count('name="topic_ids"'), len(TITLES))
        self.assertEqual(len(re.findall(r'name="topic_ids" value="\d+" checked', html)), len(TITLES),
                         "every topic is included by default")
        analysis = self.analysis(project_id)
        self.assertEqual(analysis["duplicates"], {str(b1): a1})
        self.assertEqual(analysis["topics_dropped"], 0)
        self.assertEqual(analysis["uncovered_page_ids"], [])
        self.assertNotIn(part2, analysis["analysed_page_ids"], "the unreadable page never fed the extraction")
        sections = self.sections(project_id)
        rawls = sections["Gerechtigkeit nach Rawls"]
        with application.app.app_context():
            source = json.loads(self.section_row(rawls).source_page_ids_json)
            self.assertEqual(source, [scan], "the topic cites the page it came from")
            flashcards = db.session.scalar(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id))
            assert flashcards is not None, "the project's key-term cards are made without being asked for"
            cards = {card.front: card for card in flashcards.cards}
            # PF3 (D2): the handwritten page was read at confidence 0.4 and needs review, so its
            # cards wait for the student's check instead of being shown as the notes' own words.
            self.assertNotIn("What does the term “Werte” mean?", cards)
            self.assertEqual(cards["What does the term “Urzustand” mean?"].source_reference, "scan.pdf p. 1")
            set_id = flashcards.id
            photo_text = db.session.get(application.ProjectPage, photo).extracted_text
        # The student checks the page and saves its text as it stands: the cards follow.
        response = self.post(f"/projects/{project_id}/pages/{photo}/correct", data={"text": photo_text})
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            flashcards = db.session.get(application.FlashcardSet, set_id)
            assert flashcards is not None
            cards = {card.front: card for card in flashcards.cards}
            werte = cards["What does the term “Werte” mean?"]
            # The user's own example: the simple definition on the back, the notes' example below it.
            self.assertEqual(werte.back, "Werte sind Vorstellungen davon, was Menschen wichtig und richtig finden.")
            self.assertIn("Freiheit, Gerechtigkeit und Respekt", werte.explanation)
            self.assertNotIn("added by Learnova", werte.explanation, "nothing on this card is an addition")
            self.assertEqual(werte.source_reference, "handschrift.png p. 1")

        # ---- confirm every topic: the first planned lesson opens
        self.model.lessons["Gerechtigkeit nach Rawls"] = rawls_parts
        response = self.post(f"/projects/{project_id}/topics/confirm",
                             data={"choice": "manual", "topic_ids": [str(value) for value in sections.values()]})
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        self.assertIn("session_id=", response.headers["Location"])

        # ---- the Rawls lesson: Ethics method, teaching first, the notes' Fachbegriffe, the cards
        session_id = self.start_topic(project_id, rawls)
        call = self.model.lesson_call("Gerechtigkeit nach Rawls")
        prompt = call["input"]
        self.assertIn("TEACHING METHOD — Ethics and Philosophy", prompt)
        self.assertIn("SUBJECT METHOD (Ethics and Philosophy)", call["instructions"])
        self.assertIn("Teach first", prompt)
        # The spec's Ethics sequence: terms, the thought experiment (what happens, what it
        # shows), the theory, the central question and arguments, other readings, then practice.
        self.assertEqual(self.planned_kinds(prompt), [
            "objective", "terms", "thought_experiment", "theory", "argument", "perspectives", "example",
            "mistakes", "guided_exercise", "summary"])
        step = next(line for line in prompt.splitlines() if re.match(r"\d+\. thought_experiment — ", line))
        for asked in ("completely and in order", "what it is meant to show", "serves the learning objective",
                      "mark any other reading as an additional interpretation"):
            self.assertIn(asked, step)
        self.assertIn("- Urzustand (in the notes, [scan.pdf, p. 1])", prompt)
        self.assertIn("- Differenzprinzip (in the notes, [scan.pdf, p. 1])", prompt)
        self.assertIn("thought_experiment: Schleier des Nichtwissens (in the notes", prompt)
        self.assertIn("Menschen legen die Regeln einer Gesellschaft fest", prompt, "the scan's text reaches the lesson")
        lesson = application.SESSIONS[session_id]["lesson"]
        self.assertEqual(lesson["strategy"], {"key": "ethics", "name": "Ethics and Philosophy"})
        self.assertTrue(lesson["teaching_order_ok"])
        parts = lesson["parts"]
        got = [entry["kind"] for entry in parts]
        self.assertEqual(got[:4], ["objective", "terms", "flashcards", "thought_experiment"],
                         "the project's cards follow the key terms")
        self.assertLess(max(got.index(kind) for kind in ("terms", "thought_experiment", "theory", "argument")),
                        got.index("guided_exercise"), "every teaching part comes before the practice")
        terms = next(entry for entry in parts if entry["kind"] == "terms")
        self.assertEqual([item["label"] for item in terms["items"]], ["Urzustand", "Differenzprinzip"])
        self.assertTrue(terms["from_notes"])
        retold = next(entry for entry in parts if entry["kind"] == "thought_experiment")
        self.assertTrue(retold["from_notes"], "the retelling is the notes' own thought experiment")
        reading_added = next(entry for entry in parts if entry["kind"] == "perspectives")
        self.assertFalse(reading_added["from_notes"], "a reading the notes do not give stays an addition")
        cards_part = next(entry for entry in parts if entry["kind"] == "flashcards")
        self.assertEqual(cards_part["study_url"], f"/flashcards/{set_id}/study")
        labels = [item["label"] for item in cards_part["items"]]
        self.assertIn("What does the term “Urzustand” mean?", labels)
        self.assertNotIn("What does the term “Werte” mean?", labels, "only this topic's cards")
        page = self.get(f"/?session_id={session_id}").get_data(as_text=True)
        self.assertIn('id="lessonBootstrap"', page)
        self.assertNotIn('"expected_answer"', page, "the answer key never reaches the page")
        with application.app.app_context():
            self.assertIsNone(self.section_row(rawls).learned_at, "opening a lesson is not learning it")
        states = {item["section"].title: item["state"] for item in self.context(project_id)["topics"]}
        self.assertEqual(states["Gerechtigkeit nach Rawls"], "taught")

        # ---- a wrong answer: Try again, without the solution
        retry = {"misunderstanding": "Du hast übersehen, dass niemand seine spätere Stellung kennt.",
                 "reteach": "Stell dir vor, du weißt nicht, ob du arm oder reich sein wirst.",
                 "simpler_example": "Wer einen Kuchen teilt und als Letzter wählt, schneidet gerecht."}
        self.model.evaluations = [evaluation(20, "Gerechtigkeit nach Rawls",
                                             feedback="Die richtige Antwort ist: Niemand bevorzugt die eigene Stellung.",
                                             correction="Richtig ist a: Niemand bevorzugt die eigene Stellung.",
                                             retry=retry)]
        first = self.answer(session_id, "b")
        self.assertTrue(first["retry"]["allowed"])
        self.assertEqual(first["retry"]["misunderstanding"], retry["misunderstanding"])
        self.assertEqual(first["retry"]["simpler_example"], retry["simpler_example"])
        self.assertEqual(first["retry"]["question"]["id"], "q1", "the same question again")
        self.assertNotIn("correction", first["evaluation"])
        self.assertNotIn("Niemand bevorzugt die eigene Stellung", json.dumps(
            [first["evaluation"], first["diagnosis"]], ensure_ascii=False), "the answer is not given away")
        self.assertFalse(first["complete"])
        with application.app.app_context():
            self.assertIsNone(self.section_row(rawls).learned_at)

        # ---- the second try is right - one right answer is not yet "learned"
        self.model.evaluations = [evaluation(100, "Gerechtigkeit nach Rawls", feedback="Richtig.")]
        second = self.answer(session_id, "a", retry_count=1)
        self.assertFalse(second.get("retry"))
        self.assertFalse(second["complete"])
        self.assertTrue(second["next_question"])
        with application.app.app_context():
            self.assertIsNone(self.section_row(rawls).learned_at, "not learned before the session shows it")
        states = {item["section"].title: item["state"] for item in self.context(project_id)["topics"]}
        self.assertNotIn(states["Gerechtigkeit nach Rawls"], ("understood", "mastered"))

        # ---- the student keeps answering right until the knowledge gate ends the session
        for _ in range(15):
            data = self.answer(session_id, "a")
            if data["complete"]:
                break
        self.assertTrue(data["complete"], "the session ends once the topic is shown")
        with application.app.app_context():
            section = self.section_row(rawls)
            self.assertIsNotNone(section.learned_at, "demonstrated performance makes the topic learned")
            review_on = section.next_review_on
            self.assertIsNotNone(review_on)
            assert review_on is not None
            self.assertGreater(review_on, date.today(), "a revision is scheduled for a later day")
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            reviews = [day for day, task in application.project_plan_rows(project)
                       if task.get("kind") == "review" and task.get("section_id") == rawls]
            self.assertTrue(reviews, "the plan holds a revision of the learned topic")
            self.assertGreaterEqual(min(reviews), review_on)

        # ---- the workspace: every topic with its facts; the project is not complete yet
        context = self.context(project_id)
        by_title = {item["section"].title: item for item in context["topics"]}
        self.assertEqual(list(by_title), TITLES)
        self.assertIn(by_title["Gerechtigkeit nach Rawls"]["state"], ("understood", "mastered"))
        html = self.page(project_id)
        topics_html = self.section_of(html, "topics")
        for title, item in by_title.items():
            row = topics_html[topics_html.index(f'id="topic-{item["section"].id}"'):]
            row = row[:row.index("</li>")]
            self.assertIn(title, row)
            self.assertIn(item["state_label"], row, title)
            self.assertIn(item["importance_label"], row, title)
            self.assertIn(f"{item['terms_count']} key term(s)", row, title)
            self.assertTrue(item["terms_count"] >= 1, title)
            self.assertTrue(item["source_labels"], title)
            self.assertIn(item["source_labels"][0], row, title)
            self.assertIn(item["next_label"], row, title)
        self.assertEqual(by_title["Gerechtigkeit nach Rawls"]["source_labels"], ["scan.pdf p. 1"])
        self.assertEqual(by_title["Werte und Normen"]["source_labels"], ["handschrift.png p. 1"])
        self.assertIn(f"Revision {review_on.strftime('%d.%m.')}", topics_html)
        self.assertIn("Gerechtigkeit nach Rawls", self.section_of(html, "revision"))
        self.assertFalse(context["project_complete"])
        self.assertNotIn("Project complete", html)
        open_checks = {check["code"] for check in context["coverage_checks"] if not check["ok"]}
        self.assertIn("objectives_covered", open_checks, "untaught topics keep the project open")
        self.assertIn("understanding_demonstrated", open_checks)
        self.assertIn("Missing before your project is complete:", html)

        # ---- a student who has shown the topic is not lectured again: opening it now is a
        # short review built on what they know, not the whole lesson
        review_id = self.start_topic(project_id, rawls)
        self.assertNotEqual(review_id, session_id)
        review = self.model.lesson_call("Gerechtigkeit nach Rawls")["input"]
        self.assertEqual(self.planned_kinds(review), ["objective", "terms", "guided_exercise", "summary"])
        self.assertNotEqual(application.SESSIONS[review_id]["lesson"]["knowledge_state"], "new")

    def analysis(self, project_id):
        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            return json.loads(project.analysis_json)


# ---------------------------------------------------------------------------- subjects through the routes

MONSUN = ("Der Monsun in Südasien. Die Karte zeigt die Windrichtung im Sommer und im Winter; die Legende erklärt die "
          "Pfeile. Im Sommer erwärmt sich das Land stärker als das Meer. Über dem Land steigt warme Luft auf, feuchte "
          "Meeresluft strömt nach. Beim Aufsteigen kühlt die Luft ab, und es regnet stark. Folgen: Überschwemmungen, "
          "aber auch Wasser für den Reisanbau.")
EROSION = ("Bodenerosion. Regen und Wind tragen den Boden ab, wenn keine Pflanzen ihn festhalten. Folge: Die Felder "
           "verlieren fruchtbaren Boden.")
ERLKOENIG = ("Der Erlkönig (Johann Wolfgang von Goethe, 1782) ist eine Ballade. V. 1-2: „Wer reitet so spät durch "
             "Nacht und Wind? Es ist der Vater mit seinem Kind.“ Aufbau: Die erste und die letzte Strophe erzählt ein "
             "Erzähler, dazwischen sprechen Vater, Sohn und Erlkönig. Ballade: ein Gedicht, das eine spannende "
             "Geschichte erzählt. Die rhetorische Frage in Vers 1 erzeugt Spannung. Am Ende ist das Kind tot.")


class SubjectLessonRouteTests(EngineCase):
    """Lessons opened from the workspace (POST /projects/<id>/topics/<id>/start) follow the
    subject's method - the structure and the graded formats change, not only the wording."""

    def lesson_of(self, session_id):
        return application.SESSIONS[session_id]["lesson"]

    def test_a_geography_lesson_reads_the_map_and_explains_the_process_step_by_step(self):
        project_id, section_id, _ = self.seed_topic("Erdkunde", [("Geo-Monsun.pdf", [MONSUN])], lambda ids: {
            "title": "Sommermonsun in Südasien", "summary": "Warum es im Sommer in Südasien stark regnet.",
            "terms": [term_entry("Monsun", "ein Wind, der mit den Jahreszeiten seine Richtung wechselt", ids[0])],
            "items": [item_entry("diagram", "Karte: Windrichtung im Sommer und im Winter",
                                 "die Legende erklärt die Pfeile", ids[0]),
                      item_entry("place", "Südasien", "Land und Meer erwärmen sich unterschiedlich", ids[0]),
                      item_entry("process", "Sommermonsun", "Land erwärmt sich, warme Luft steigt auf, feuchte "
                                 "Meeresluft strömt nach, es regnet", ids[0]),
                      item_entry("cause_effect", "Starkregen", "Folgen: Überschwemmungen, Wasser für den Reisanbau",
                                 ids[0])],
            "exam_questions": ["Erkläre die Entstehung des Sommermonsuns mithilfe der Karte."]})
        steps = [{"label": "1", "text": "Das Land erwärmt sich stärker als das Meer."},
                 {"label": "2", "text": "Über dem Land steigt warme Luft auf."},
                 {"label": "3", "text": "Feuchte Meeresluft strömt nach."},
                 {"label": "4", "text": "Die Luft kühlt beim Aufsteigen ab, es regnet stark."}]
        self.model.lessons["Sommermonsun in Südasien"] = [
            part("objective", "Du kannst den Sommermonsun mit der Karte erklären."),
            part("explanation", "Im Sommer erwärmt sich das Land stärker als das Meer."),
            part("terms", items=[{"label": "Monsun", "text": "ein Wind, der mit den Jahreszeiten seine Richtung "
                                                             "wechselt"}]),
            part("process", items=steps, title="Wie der Sommermonsun entsteht"),
            part("diagram", "Lies zuerst Titel und Legende der Karte: Die Pfeile zeigen die Windrichtung im Sommer "
                            "und im Winter."),
            part("cause_effect", items=[{"label": "Starkregen", "text": "Überschwemmungen, aber auch Wasser für den "
                                                                        "Reisanbau"}]),
            part("example", "Übertrage es auf Westafrika: Auch dort bringt feuchte Meeresluft Regen.", from_notes=False),
            part("guided_exercise", "Beschreibe mit der Karte, woher der Wind im Sommer weht.", answer="Vom Meer."),
            part("summary", "Ungleiche Erwärmung → aufsteigende Luft → feuchte Meeresluft → Regen."),
        ]
        session_id = self.start_topic(project_id, section_id)
        call = self.model.lesson_call("Sommermonsun in Südasien")
        prompt = call["input"]
        self.assertIn("TEACHING METHOD — Geography", prompt)
        self.assertEqual(self.planned_kinds(prompt), ["objective", "explanation", "terms", "process", "diagram",
                                                      "cause_effect", "example", "guided_exercise", "summary"])
        diagram_step = next(line for line in prompt.splitlines() if re.match(r"\d+\. diagram — ", line))
        for asked in ("map", "legend", "what it shows"):
            self.assertIn(asked, diagram_step)
        process_step = next(line for line in prompt.splitlines() if re.match(r"\d+\. process — ", line))
        self.assertIn("step by step", process_step)
        self.assertIn("diagram: Karte: Windrichtung im Sommer und im Winter (in the notes", prompt)
        self.assertIn("process: Sommermonsun (in the notes", prompt)
        lesson = self.lesson_of(session_id)
        self.assertEqual(lesson["strategy"]["key"], "geography")
        kinds = [entry["kind"] for entry in lesson["parts"]]
        self.assertLess(kinds.index("process"), kinds.index("diagram"))
        self.assertLess(kinds.index("diagram"), kinds.index("cause_effect"))
        self.assertLess(kinds.index("cause_effect"), kinds.index("guided_exercise"))
        process = next(entry for entry in lesson["parts"] if entry["kind"] == "process")
        self.assertEqual([item["text"] for item in process["items"]], [step["text"] for step in steps],
                         "the steps stay in their order")
        transfer = next(entry for entry in lesson["parts"] if entry["kind"] == "example")
        self.assertFalse(transfer["from_notes"], "a new place the notes do not name is an addition")
        # The graded formats are geography's: question 2 puts the steps in order, and wrong
        # answers are corrected by pointing at the map detail or step that was misread.
        self.model.evaluations = [evaluation(100, "Sommermonsun in Südasien", feedback="Richtig.")]
        self.answer(session_id, "a")
        grading = self.model.last("answer_evaluation")
        self.assertIn('"type": "ordering"', grading["input"], "question 2 of a geography lesson is an ordering task")
        self.assertIn("point to the map detail, factor or step that was misread", grading["input"].casefold())

    def test_a_geography_topic_without_a_map_gets_no_map_reading(self):
        project_id, section_id, _ = self.seed_topic("Geographie", [("Geo-Boden.pdf", [EROSION])], lambda ids: {
            "title": "Bodenerosion", "terms": [],
            "items": [item_entry("process", "Bodenerosion", "Regen und Wind tragen den Boden ab", ids[0])],
            "exam_questions": []})
        self.start_topic(project_id, section_id)
        kinds = self.planned_kinds(self.model.lesson_call("Bodenerosion")["input"])
        self.assertIn("process", kinds)
        self.assertNotIn("diagram", kinds, "no map is read when the notes have none")

    def test_a_german_lesson_interprets_the_text_with_evidence_before_the_student_tries(self):
        project_id, section_id, _ = self.seed_topic("Deutsch", [("Deutsch-Ballade.pdf", [ERLKOENIG])], lambda ids: {
            "title": "Goethes Erlkönig", "summary": "Eine Ballade über einen nächtlichen Ritt.",
            "terms": [term_entry("Ballade", "ein Gedicht, das eine spannende Geschichte erzählt", ids[0]),
                      term_entry("rhetorische Frage", "eine Frage, die Spannung erzeugt", ids[0])],
            "items": [item_entry("work", "Der Erlkönig", "Ballade von Goethe, 1782", ids[0]),
                      item_entry("person", "Johann Wolfgang von Goethe", "schrieb den Erlkönig", ids[0])],
            "exam_questions": ["Interpretiere die erste Strophe."]})
        model_analysis = ("These: Die rhetorische Frage in V. 1 erzeugt Spannung. Beleg: „Wer reitet so spät durch "
                          "Nacht und Wind?“ (V. 1). Wirkung: Der Leser will wissen, wer dort reitet.")
        self.model.lessons["Goethes Erlkönig"] = [
            part("objective", "Du kannst die erste Strophe des Erlkönigs mit Belegen deuten."),
            part("explanation", "Der Erlkönig ist eine Ballade von Goethe aus dem Jahr 1782."),
            part("terms", items=[{"label": "Ballade", "text": "ein Gedicht, das eine spannende Geschichte erzählt"},
                                 {"label": "rhetorische Frage", "text": "eine Frage, die Spannung erzeugt"}]),
            part("structure", "Rahmen: Die erste und die letzte Strophe erzählt ein Erzähler. Dazwischen sprechen "
                              "Vater, Sohn und Erlkönig. Kernaussage: Am Ende ist das Kind tot."),
            part("rule", "So deutest du ein Stilmittel: finden, benennen, Wirkung im Zusammenhang beschreiben."),
            part("model_analysis", model_analysis),
            part("mistakes", items=[{"label": "Nacherzählen statt deuten", "text": "Nenne immer die Wirkung."}]),
            part("guided_exercise", "Deute Vers 2 mit Beleg.", answer="Die Antwort nennt Vater und Kind (V. 2)."),
            part("summary", "Inhalt, Aufbau, Methode und Kernaussage."),
        ]
        session_id = self.start_topic(project_id, section_id)
        prompt = self.model.lesson_call("Goethes Erlkönig")["input"]
        self.assertIn("TEACHING METHOD — German (texts, literature and language)", prompt)
        self.assertEqual(self.planned_kinds(prompt), ["objective", "explanation", "terms", "structure", "rule",
                                                      "model_analysis", "mistakes", "guided_exercise", "summary"])
        lines = {kind: next(line for line in prompt.splitlines() if re.match(rf"\d+\. {kind} — ", line))
                 for kind in ("structure", "rule", "model_analysis")}
        self.assertIn("central message", lines["structure"])
        self.assertIn("interpretation method", lines["rule"])
        self.assertIn("quotation or line reference as evidence", lines["model_analysis"])
        self.assertIn("Wer reitet so spät durch Nacht und Wind?", prompt, "the text itself reaches the lesson")
        lesson = self.lesson_of(session_id)
        self.assertEqual(lesson["strategy"]["key"], "german")
        kinds = [entry["kind"] for entry in lesson["parts"]]
        self.assertLess(kinds.index("model_analysis"), kinds.index("guided_exercise"),
                        "the model analysis comes before the student's own try")
        analysis = next(entry for entry in lesson["parts"] if entry["kind"] == "model_analysis")
        self.assertIn("(V. 1)", analysis["body"])
        # A wrong interpretation: the reason is named (a claim without evidence), no solution.
        self.model.evaluations = [evaluation(
            30, "Goethes Erlkönig", feedback="Deine Deutung hat keinen Beleg.", correction="Richtig ist a.",
            retry={"misunderstanding": "Du behauptest etwas, ohne eine Textstelle zu nennen.",
                   "reteach": "Suche den Vers, der deine Aussage stützt.",
                   "simpler_example": "„Es ist der Vater mit seinem Kind“ (V. 2) zeigt, wer reitet."})]
        data = self.answer(session_id, "b")
        self.assertTrue(data["retry"]["allowed"])
        self.assertNotIn("correction", data["evaluation"])
        grading = self.model.last("answer_evaluation")
        self.assertIn("show which claim lacks evidence", grading["input"].casefold())

    def test_other_subjects_get_their_own_method(self):
        cases = [
            ("Religion", "religion", "Das Gleichnis vom barmherzigen Samariter (Lk 10,25-37) erzählt von einem "
             "Mann, der einem Verletzten hilft. Nächstenliebe: dem Mitmenschen helfen, auch einem Fremden.",
             [("source", "Lk 10,25-37", "Gleichnis vom barmherzigen Samariter")], "source_analysis"),
            ("Kunst", "art", "Die Sternennacht von Vincent van Gogh (1889) zeigt einen Nachthimmel mit Wirbeln. "
             "Komplementärkontrast: Farben, die sich im Farbkreis gegenüberliegen.",
             [("work", "Die Sternennacht", "Gemälde von van Gogh, 1889")], "model_analysis"),
            ("Sport", "physical_education", "Der Korbleger beim Basketball: zwei Schritte, dann Absprung vom Bein "
             "gegenüber der Wurfhand. Schrittfehler: mehr als zwei Schritte mit dem Ball.",
             [("rule", "Schrittregel", "höchstens zwei Schritte mit dem Ball"),
              ("process", "Korbleger", "zwei Schritte, Absprung, Wurf")], "process"),
            ("Technik", "technology", "Ein Elektromotor wandelt elektrische Energie in Bewegungsenergie um. "
             "Die Skizze zeigt Spule, Magnet und Kommutator.",
             [("diagram", "Skizze Elektromotor", "Spule, Magnet und Kommutator"),
              ("process", "Energieumwandlung", "elektrische Energie wird zu Bewegungsenergie")], "diagram"),
        ]
        general = [step.kind for step in lesson_plan.plan_steps(REGISTRY["general"], mode="learn", state="new",
                                                                item_kinds=None)]
        for subject, key, notes, items, distinctive in cases:
            with self.subTest(subject=subject):
                title = f"Thema {subject}"
                project_id, section_id, _ = self.seed_topic(subject, [(f"{subject}.pdf", [notes])], lambda ids: {
                    "title": title, "terms": [], "exam_questions": [],
                    "items": [item_entry(kind, name, detail, ids[0]) for kind, name, detail in items]})
                session_id = self.start_topic(project_id, section_id)
                call = self.model.lesson_call(title)
                strategy = REGISTRY[key]
                self.assertEqual(self.lesson_of(session_id)["strategy"], {"key": key, "name": strategy.name})
                self.assertIn(f"TEACHING METHOD — {strategy.name}", call["input"])
                self.assertIn(f"SUBJECT METHOD ({strategy.name})", call["instructions"])
                kinds = self.planned_kinds(call["input"])
                self.assertIn(distinctive, kinds)
                self.assertNotEqual(kinds, general, "not the generic sequence")
                self.assertEqual(kinds[0], "objective")
                self.assertEqual(kinds[-2:], ["guided_exercise", "summary"])


REVOLUTION = ("Die Französische Revolution. 5. Mai 1789: Die Generalstände treten zusammen. 14. Juli 1789: Sturm auf "
              "die Bastille. 26. August 1789: Erklärung der Menschen- und Bürgerrechte. 1793: Hinrichtung Ludwigs "
              "XVI. Ursachen: Hunger, hohe Steuern und die Schulden des Königs. Folgen: Ende des Absolutismus.")


class HistoryLessonTests(EngineCase):
    def test_a_history_lesson_builds_the_chronology_then_causes_and_consequences(self):
        project_id, section_id, _ = self.seed_topic("Geschichte", [("Revolution.pdf", [REVOLUTION])], lambda ids: {
            "title": "Französische Revolution", "summary": "Wie die Revolution 1789 begann und was sie veränderte.",
            "terms": [term_entry("Absolutismus", "eine Herrschaft, in der der König allein regiert", ids[0])],
            "items": [item_entry("event", "Sturm auf die Bastille", "14. Juli 1789", ids[0]),
                      item_entry("date", "1793", "Hinrichtung Ludwigs XVI.", ids[0]),
                      item_entry("cause_effect", "Französische Revolution", "Ursachen: Hunger, Steuern, Schulden; "
                                 "Folgen: Ende des Absolutismus", ids[0])],
            "exam_questions": ["Erläutere die Ursachen der Französischen Revolution."]})
        # The model lists the dates out of order; the student must still see a chronology.
        shuffled = [{"label": "1793", "text": "Hinrichtung Ludwigs XVI."},
                    {"label": "14. Juli 1789", "text": "Sturm auf die Bastille"},
                    {"label": "26. August 1789", "text": "Erklärung der Menschen- und Bürgerrechte"},
                    {"label": "5. Mai 1789", "text": "Die Generalstände treten zusammen"}]
        self.model.lessons["Französische Revolution"] = [
            part("objective", "Du kannst den Beginn der Revolution zeitlich ordnen und ihre Ursachen erklären."),
            part("explanation", "Frankreich war 1789 hoch verschuldet, viele Menschen hungerten."),
            part("timeline", items=shuffled),
            part("terms", items=[{"label": "Absolutismus", "text": "eine Herrschaft, in der der König allein regiert"}]),
            part("cause_effect", items=[{"label": "Hunger, hohe Steuern, Schulden", "text": "Sturm auf die Bastille"},
                                        {"label": "Revolution", "text": "Ende des Absolutismus"}]),
            part("guided_exercise", "Ordne die Ereignisse und nenne eine Ursache.", answer="Mai 1789 …"),
            part("summary", "Erst die Ereignisse, dann ihre Ursachen und Folgen."),
        ]
        session_id = self.start_topic(project_id, section_id)
        prompt = self.model.lesson_call("Französische Revolution")["input"]
        self.assertIn("TEACHING METHOD — History", prompt)
        # No source in these notes, so no source analysis is planned.
        self.assertEqual(self.planned_kinds(prompt), ["objective", "explanation", "timeline", "terms", "cause_effect",
                                                      "perspectives", "guided_exercise", "summary"])
        timeline_step = next(line for line in prompt.splitlines() if re.match(r"\d+\. timeline — ", line))
        self.assertIn("chronological order", timeline_step)
        cause_step = next(line for line in prompt.splitlines() if re.match(r"\d+\. cause_effect — ", line))
        self.assertIn("long-term causes", cause_step)
        lesson = application.SESSIONS[session_id]["lesson"]
        self.assertEqual(lesson["strategy"]["key"], "history")
        kinds = [entry["kind"] for entry in lesson["parts"]]
        self.assertLess(kinds.index("timeline"), kinds.index("cause_effect"), "chronology before causes")
        timeline = next(entry for entry in lesson["parts"] if entry["kind"] == "timeline")
        self.assertEqual([item["label"] for item in timeline["items"]],
                         ["5. Mai 1789", "14. Juli 1789", "26. August 1789", "1793"],
                         "the timeline is shown in chronological order whatever order the model used")
        # Question 2 is an ordering task (the chronology), and mistakes are placed back in it.
        self.model.evaluations = [evaluation(100, "Französische Revolution", feedback="Richtig.")]
        self.answer(session_id, "a")
        grading = self.model.last("answer_evaluation")
        self.assertIn('"type": "ordering"', grading["input"])
        self.assertIn("place it in the chronology again", grading["input"].casefold())

    def test_timeline_order_is_repaired_only_when_every_date_is_clear(self):
        from learnova.lessons.lesson_context import order_timeline_items

        def labels(*values):
            return [item["label"] for item in order_timeline_items([{"label": value, "text": "x"} for value in values])]

        self.assertEqual(labels("1945", "1939", "1. September 1939", "8. Mai 1945", "um 1900"),
                         ["um 1900", "1939", "1. September 1939", "1945", "8. Mai 1945"],
                         "a bare year comes before the days of that year, stably")
        self.assertEqual(labels("476 n. Chr.", "44 v. Chr.", "753 v. Chr."), ["753 v. Chr.", "44 v. Chr.", "476 n. Chr."])
        self.assertEqual(labels("14.07.1789", "05.05.1789"), ["05.05.1789", "14.07.1789"])
        self.assertEqual(labels("July 4, 1776", "1914–1918", "1871"), ["July 4, 1776", "1871", "1914–1918"])
        # Anything not clearly a date keeps the model's order.
        self.assertEqual(labels("1945", "Nachkriegszeit", "1939"), ["1945", "Nachkriegszeit", "1939"])
        self.assertEqual(labels("19. Jahrhundert", "1789"), ["19. Jahrhundert", "1789"])
        self.assertEqual(labels("Phase 2", "Phase 1"), ["Phase 2", "Phase 1"])
        self.assertEqual(order_timeline_items([]), [])


class ClassLevelTests(EngineCase):
    """The project's class reaches the lesson; a lower class gets a simpler explanation level."""

    def test_a_lower_class_gets_the_simpler_level_and_the_class_label_reaches_the_prompt(self):
        seen = {}
        for grade in ("4", "11"):
            project_id, section_id, _ = self.seed_topic(
                "Ethik", [(f"Werte-{grade}.pdf", [WERTE])], lambda ids: {
                    "title": "Werte und Normen", "terms": [term_entry(
                        "Werte", "Vorstellungen davon, was Menschen wichtig und richtig finden", ids[0])],
                    "items": [], "exam_questions": []}, grade=grade, title=f"Klasse {grade}")
            self.start_topic(project_id, section_id)
            call = self.model.last("lesson_generation")
            seen[grade] = call
            self.assertIn(f"The learner is in grade {grade} (", call["instructions"])
            self.assertIn(f"Grade {grade}", call["input"], "the class label reaches the lesson plan")
            self.assertNotIn("The learner is in grade 10 (", call["instructions"],
                             "the profile's class 10 does not override the project's class")

        def level(call):
            match = re.search(r"EXPLANATION LEVEL for 'Werte und Normen': (\d) of 5", call["instructions"])
            assert match is not None, call["instructions"][:2000]
            return int(match.group(1))

        self.assertLess(level(seen["4"]), level(seen["11"]), "class 4 starts at a simpler level than class 11")
        self.assertIn("a primary-school child", seen["4"]["instructions"])
        self.assertIn("an upper-secondary student", seen["11"]["instructions"])


# ---------------------------------------------------------------------------- subject card formats

HISTORY_NOTES = ("Franzoesische Revolution. Sturm auf die Bastille am 14. Juli 1789 in Paris. Ursachen: Hunger, hohe "
                 "Steuern und Schulden des Koenigs. Folgen: Ende des Absolutismus und Erklaerung der Menschenrechte. "
                 "Absolutismus: eine Herrschaft, in der der Koenig allein regiert.")
ENGLISH_NOTES = ("Irregular verbs and conditionals. go - went - gone: gehen. If-clause type 1: if + simple present, "
                 "will + infinitive. Example: If it rains, we will stay at home. Relative pronoun: who for people, "
                 "which for things. Example: The girl who lives next door is my friend. The book which I read was "
                 "exciting. Learn the irregular verbs by heart for the test next week.")
HISTORY_TABLE = [("Franzoesische Revolution",
                  [("Absolutismus", "eine Herrschaft, in der der Koenig allein regiert", "der Koenig regiert allein",
                    "Absolutismus: eine Herrschaft, in der der Koenig allein regiert", "")],
                  [("event", "Sturm auf die Bastille", "am 14. Juli 1789 in Paris"),
                   ("date", "1789", "Sturm auf die Bastille in Paris"),
                   ("cause_effect", "Franzoesische Revolution",
                    "Ursachen: Hunger, hohe Steuern und Schulden des Koenigs. Folgen: Ende des Absolutismus")])]
ENGLISH_TABLE = [("Irregular verbs and conditionals",
                  [("go - went - gone", "gehen", "gehen", "go - went - gone: gehen", "")],
                  [("structure", "If-clause type 1", "if + simple present, will + infinitive"),
                   ("rule", "Relative pronoun", "who for people, which for things")])]


class SubjectCardTests(EngineCase):
    """The key-term cards are made from the upload without being asked for, in the formats
    of the project's subject (Track LESSON-BE sync, through the real routes)."""

    def project_cards(self, subject, filename, notes, table):
        self.model.topic_table = table
        response = self.post("/projects", data={
            "title": subject, "subject": "Other", "subject_other": subject,
            "materials": [(io.BytesIO(text_pdf(notes)), filename, "application/pdf")]},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        with application.app.app_context():
            project = db.session.scalars(db.select(application.LearningProject).order_by(
                application.LearningProject.id.desc())).first()
            assert project is not None
            project_id, page_id = project.id, project.pages[0].id
        self.assertEqual(self.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={}).status_code, 200)
        started = self.post(f"/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        self.assertEqual(started.status_code, 200, started.get_data(as_text=True)[:300])
        with application.app.app_context():
            flashcard_set = db.session.scalar(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id))
            assert flashcard_set is not None, "the cards exist before any lesson or request"
            return {card.front: card for card in flashcard_set.cards}

    def test_history_cards_ask_for_events_dates_and_causes(self):
        cards = self.project_cards("Geschichte", "Revolution.pdf", HISTORY_NOTES, HISTORY_TABLE)
        self.assertIn("What happened: Sturm auf die Bastille?", cards)
        self.assertIn("What happened in 1789?", cards)
        causes = cards["What are the causes and consequences of Franzoesische Revolution?"]
        self.assertIn("Hunger", causes.back)
        term = cards["What does the term “Absolutismus” mean?"]
        self.assertEqual(term.source_reference, "Revolution.pdf p. 1")
        self.assertNotIn("added by Learnova", term.explanation)

    def test_language_cards_ask_for_forms_patterns_and_rules(self):
        cards = self.project_cards("Englisch", "Grammar.pdf", ENGLISH_NOTES, ENGLISH_TABLE)
        self.assertEqual(cards["Give the forms of “go”."].back, "go - went - gone")
        self.assertEqual(cards["How is the sentence pattern “If-clause type 1” built?"].back,
                         "if + simple present, will + infinitive")
        self.assertIn("Explain the rule: Relative pronoun", cards)
        self.assertFalse([front for front in cards if front.startswith("Explain “")],
                         "vocabulary gets no 'explain in your own words' card")


if __name__ == "__main__":
    unittest.main()

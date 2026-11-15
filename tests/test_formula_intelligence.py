"""Spec Phase 2, acceptance Test 3, through the real app with every model call patched.

A maths worksheet writes "y = mx + b"; the Kompetenzraster page lists it too. Pinned:

* the topic extraction asks for the page notation and a separate "latex" (maths only), keeps
  the formula with its source page and notation, marks it as teacher-marked (Kompetenzraster
  page), and keeps a model-written wrong rearrangement out of the notes and out of the cards;
* the lesson prompt lists "Formulas from the notes" first with the teaching rule; a point-slope
  form the model adds is stored only as an "alternative" part that is never from the notes;
* broken LaTeX in the model's lesson, questions and exam questions is repaired before it is
  stored; follow-up questions get the notes' formulas and a rotating formula task; the exam
  prompt carries each section's formulas; the notes page shows the formula with its label;
* an old stored map is enriched at read time without being written back.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_formula_intelligence_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from flask_login import login_user  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.assistant.formatting import math_problems  # noqa: E402
from learnova.diagnostics.prompts import FORMULA_PRACTICE_RULE, question_system_prompt  # noqa: E402
from learnova.lessons import lesson_context  # noqa: E402
from learnova.projects import formulas as project_formulas  # noqa: E402
from learnova.projects import terms as project_terms  # noqa: E402

db = application.db
PASSWORD = "correct-horse-battery"
_MISSING = object()

WORKSHEET = ("Lineare Funktionen\nGeradengleichung: y = mx + b\n"
             "m ist die Steigung, b ist der y-Achsenabschnitt.\nBeispiel: Ein Taxi kostet 3 Euro Grundpreis.")
GRID = "Kompetenzraster Mathematik\nIch kann die Geradengleichung y = mx + b aufstellen und deuten."
BROKEN = "Es gilt \\(x = \\frac{-b \\pm \\sqrt{b^2-4ac}{2a}\\) und halb ist \\frac{1}{2}."


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def part(kind, body="", *, items=(), title="", from_notes=True, pages=(1,), answer=""):
    return {"kind": kind, "title": title or kind, "body": body, "items": list(items), "answer": answer,
            "from_notes": from_notes, "source_pages": list(pages)}


QUESTION = {"id": "q1", "concept": "Geradengleichung", "difficulty": 1, "type": "short_answer",
            "prompt": "Berechne y für \\(m = \\frac{1}{2\\) und x = 4, b = 1.", "hint": "Setze ein.",
            "options": [], "expected_answer": "y = 3"}


class FormulaIntelligenceTests(unittest.TestCase):
    maxDiff = None
    CONFIG_KEYS = ("TESTING", "AI_MODE", "CURRICULUM_CLEARED_SOURCES", "SUBSCRIPTION_QUOTAS_ENFORCED",
                   "RATELIMIT_ENABLED", "FEATURE_PRIVATE_FLASHCARDS")

    def setUp(self):
        self.saved = {key: application.app.config.get(key, _MISSING) for key in self.CONFIG_KEYS}
        self.saved_limiter = application.limiter.enabled
        application.app.config.update(TESTING=True, AI_MODE="cached", CURRICULUM_CLEARED_SOURCES="",
                                      SUBSCRIPTION_QUOTAS_ENFORCED=False, RATELIMIT_ENABLED=False,
                                      FEATURE_PRIVATE_FLASHCARDS=True)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        application._curriculum_index_cache.update(key=None, index=None)
        ai_service._RECENT_RESULTS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lea", "email": "lea@example.com", "password": PASSWORD,
                                            "grade": "9", "language": "en"})
        with application.app.app_context():
            self.user_id = db.session.scalar(db.select(application.User.id).where(application.User.username == "lea"))
        self.calls: list[dict] = []
        self.lesson_parts: list = []

    def tearDown(self):
        for key, value in self.saved.items():
            if value is _MISSING:
                application.app.config.pop(key, None)
            else:
                application.app.config[key] = value
        application.limiter.enabled = self.saved_limiter

    # ------------------------------------------------------------------ fake model
    def respond(self, *, task_type, **kwargs):
        self.calls.append({"task_type": task_type, **kwargs})
        if task_type == "project_section_generation":
            prompt = str(kwargs.get("input", ""))
            ids = sorted(int(value) for value in set(__import__("re").findall(r'"page_id": (\d+)', prompt)))
            worksheet, grid = ids[0], ids[-1]
            return FakeResponse({"sections": [{
                "title": "Lineare Funktionen", "main_topic": "Lineare Funktionen", "kind": "main",
                "summary": "Geraden mit Steigung und Achsenabschnitt.", "simple_summary": "Geraden.",
                "learning_goals": ["Ich kann die Geradengleichung y = mx + b aufstellen und deuten."],
                "source_page_ids": [worksheet, grid],
                "terms": [{"term": "Steigung", "definition": "m", "page_id": worksheet, "quote": "m ist die Steigung"}],
                "items": [
                    # The notes' formula, written by the model in LaTeX and cited on the grid page.
                    {"kind": "formula", "name": "y = m \\cdot x + b", "latex": "y = m \\cdot x + b",
                     "detail": "Geradengleichung", "page_id": grid,
                     "variables": [{"symbol": "m", "meaning": "Steigung", "unit": ""},
                                   {"symbol": "b", "meaning": "y-Achsenabschnitt", "unit": ""}]},
                    # A rearrangement the notes do not write, with a broken latex field.
                    {"kind": "formula", "name": "x = \\frac{y}{m} - b", "latex": "x = \\frac{y}{m",
                     "detail": "nach x umgestellt", "page_id": worksheet},
                ],
                "exam_questions": ["Stelle die Geradengleichung auf."], "related_topics": [], "prerequisites": [],
                "difficulty": "medium", "importance": "high", "estimated_minutes": 15}]})
        if task_type == "lesson_generation":
            return FakeResponse({"lesson_title": "Lineare Funktionen", "detected_level": "Klasse 9",
                                 "concepts": [{"name": "Geradengleichung", "evidence": "notes"}],
                                 "parts": self.lesson_parts, "explanation": "", "worked_example": None,
                                 "teacher_tips": ["Prüfe mit \\frac{1}{2}."], "exceptions": [], "question": QUESTION})
        if task_type == "question_generation":
            return FakeResponse({"question": {**QUESTION, "id": "g1"}})
        if task_type == "final_exam_generation":
            return FakeResponse({"questions": []})
        raise AssertionError(f"unexpected AI task {task_type}")

    # ------------------------------------------------------------------ material
    def make_project(self):
        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id, title="Mathe", subject="Mathematik",
                                                  status="uploaded")
            db.session.add(project)
            db.session.flush()
            page_ids = []
            for order, (name, text, kind) in enumerate((("Arbeitsblatt.pdf", WORKSHEET, "text"),
                                                         ("Kompetenzraster.pdf", GRID, application.GRID_SOURCE_KIND)),
                                                        start=1):
                upload = application.ProjectFile(project_id=project.id, original_filename=name,
                                                 mime_type="application/pdf", original_data=b"%PDF", sha256=name,
                                                 source_kind=kind)
                db.session.add(upload)
                db.session.flush()
                page = application.ProjectPage(
                    project_id=project.id, file_id=upload.id, page_number=1, page_order=order, extracted_text=text,
                    extraction_status="ready", confidence_status="high", recognition_confidence=0.95,
                    review_status="confirmed", processing_stage="ready", teacher_highlighted=kind != "text",
                    recognition_json=json.dumps({"text_source": "text_layer"}))
                db.session.add(page)
                db.session.flush()
                page_ids.append(page.id)
            db.session.commit()
            return project.id, page_ids

    def analyse(self, project_id):
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            with patch.object(application, "create_response", side_effect=self.respond):
                application.plan_project_sections(project)
            section = project.sections[0]
            return section.id, json.loads(section.topic_map_json)

    def start_lesson(self, section_id):
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            section = db.session.get(application.LearningSection, section_id)
            with patch.object(application, "create_response", side_effect=self.respond):
                return application.start_section_lesson(section, "learn")

    def lesson_prompt(self):
        return next(call for call in reversed(self.calls) if call["task_type"] == "lesson_generation")["input"]

    # ------------------------------------------------------------------ tests
    def test_extraction_keeps_the_notes_formula_and_keeps_the_rearrangement_out(self):
        project_id, (worksheet, grid) = self.make_project()
        section_id, topic_map = self.analyse(project_id)
        prompt = next(call["input"] for call in self.calls if call["task_type"] == "project_section_generation")
        self.assertIn(application.PROJECT_FORMULA_FOCUS, prompt)
        self.assertIn('the notation rule for answers does not apply to "name"', prompt)
        self.assertIn(application.PROJECT_FORMULA_FOCUS, application.project_extraction_focus("Physik"))
        self.assertNotIn(application.PROJECT_FORMULA_FOCUS, application.project_extraction_focus("Ethik"))
        formulas = [item for item in topic_map["items"] if item["kind"] == "formula"]
        notes = [item for item in formulas if item["in_notes"]]
        self.assertEqual(len(notes), 1)
        [formula] = notes
        self.assertEqual((formula["notation"], formula["page_id"], formula["match"], formula["origin"]),
                         ("y = mx + b", grid, "exact", "notes"))
        self.assertTrue(formula["teacher_marked"])
        self.assertEqual(formula["latex"], "y = m \\cdot x + b")
        self.assertEqual([variable["symbol"] for variable in formula["variables"]], ["m", "b"])
        for other in formulas:
            if other is not formula:
                self.assertFalse(other["in_notes"])
                self.assertEqual(other["origin"], "added")
                self.assertEqual(other["latex"], "")             # broken LaTeX is not stored
        with application.app.app_context():
            section = db.session.get(application.LearningSection, section_id)
            self.assertEqual(json.loads(section.formulas_json), ["y = mx + b: Geradengleichung"])
            project = db.session.get(application.LearningProject, project_id)
            topics_for_cards, page_text, labels = application.project_card_topics(project)
            cards = project_terms.build_cards(topics_for_cards, strategy_key="mathematics", page_text=page_text,
                                              source_label=lambda page: labels.get(page, ""))
        formula_cards = [card for card in cards if card["type"] == "formula_explanation"]
        self.assertEqual(len(formula_cards), 1)
        self.assertIn("y = mx + b", formula_cards[0]["front"])
        self.assertNotIn("frac{y}", json.dumps(cards))

        # The notes page: the formula as the page writes it, with its teacher label.
        self.client.post("/login", data={"username": "lea", "password": PASSWORD})
        html = self.client.get(f"/projects/{project_id}/sections/{section_id}").get_data(as_text=True)
        self.assertIn("y = mx + b", html)
        self.assertIn("Marked by your teacher", html)
        self.assertIn("pm-formula-variables", html)

    def test_the_lesson_teaches_the_notes_formula_first_and_labels_the_tip(self):
        project_id, _ = self.make_project()
        section_id, _ = self.analyse(project_id)
        self.lesson_parts = [
            part("objective", "Du lernst die Geradengleichung."),
            part("rule", "Die Geradengleichung aus deinen Notizen: \\(y = mx + b\\)."),
            part("worked_example", BROKEN, items=[{"label": "Einsetzen", "text": "\\(y = \\frac{1}{2\\cdot 4 + 1\\)"}]),
            part("alternative", "Zusatztipp: die Punkt-Steigungs-Form \\(y - y_1 = m(x - x_1)\\) hilft, wenn du die "
                 "Steigung und einen Punkt kennst.", from_notes=True),
            part("guided_exercise", "Bestimme b.", answer="\\(b = 1\\)"),
            part("summary", "Kurz."),
        ]
        session_id = self.start_lesson(section_id)
        prompt = self.lesson_prompt()
        heading = prompt.index(lesson_context.FORMULA_GROUP_HEADING)
        self.assertIn("- y = mx + b (LaTeX: y = m \\cdot x + b) [[Kompetenzraster.pdf, p. 1], teacher-marked]",
                      prompt)
        self.assertLess(heading, prompt.index("Fachbegriffe (use them exactly"))
        self.assertIn(application.FORMULA_TEACHING_RULE, prompt)
        self.assertIn("alternative — Only if it really helps", prompt)
        lesson = application.SESSIONS[session_id]["lesson"]
        kinds = {entry["kind"]: entry for entry in lesson["parts"]}
        self.assertFalse(kinds["alternative"]["from_notes"])
        self.assertTrue(kinds["rule"]["from_notes"])
        for entry in lesson["parts"]:
            self.assertEqual(math_problems(json.dumps(entry, ensure_ascii=False).replace("\\\\", "\\")), [], entry)
        self.assertIn("\\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}", kinds["worked_example"]["body"])
        self.assertEqual(lesson["teacher_tips"], ["Prüfe mit \\(\\frac{1}{2}\\)."])
        self.assertEqual(lesson["question"]["prompt"], "Berechne y für \\(m = \\frac{1}{2}\\) und x = 4, b = 1.")
        state = application.SESSIONS[session_id]
        self.assertTrue(any(line.startswith("y = mx + b") and "teacher-marked" in line
                            for line in state["notes_formulas"]))
        with application.app.app_context():
            stored = db.session.scalar(db.select(application.Lesson).where(application.Lesson.session_id == session_id))
            # Stored repaired: the missing "}" of the substitution step was added.
            stored_parts = json.loads(stored.content_json)["parts"]
            step = next(entry for entry in stored_parts if entry["kind"] == "worked_example")["items"][0]["text"]
            self.assertEqual(step, "\\(y = \\frac{1}{2\\cdot 4 + 1}\\)")

        # Follow-up questions: the notes' formulas and the formula task of the slot.
        self.assertEqual(application.formula_practice_context(state, 2, "text")["formula_task"], "rearrangement")
        self.assertEqual([application.formula_practice_context(state, n, "text")["formula_task"] for n in range(1, 7)],
                         ["substitution", "rearrangement", "word_problem", "application", "transfer", "substitution"])
        choice = application.formula_practice_context(state, 3, "multiple_choice")
        self.assertNotIn("formula_task", choice)
        self.assertEqual(choice["notes_formulas"], state["notes_formulas"])
        self.assertEqual(application.formula_practice_context({}, 1, "text"), {})
        self.assertIn(FORMULA_PRACTICE_RULE, question_system_prompt("Mathematics", "German"))

        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            plan = SimpleNamespace(constraints={}, difficulty=1, action="practice", reason="next")
            target = {"subject": "Mathematics", "concept": "Geradengleichung", "mastery_score": 40,
                      "status": "learning"}
            with patch.object(application, "create_response", side_effect=self.respond):
                try:
                    application.generate_targeted_question(state, target, 2, "text", plan, [])
                except ValueError:
                    pass        # the stub question may not pass the validator; only the request matters here
        call = next(call for call in reversed(self.calls) if call["task_type"] == "question_generation")
        context = json.loads(call["input"].split("CONTEXT: ", 1)[1])
        self.assertEqual(context["formula_task"], "rearrangement")
        self.assertEqual(context["notes_formulas"], state["notes_formulas"])

    def test_questions_and_exam_questions_are_repaired_not_rejected(self):
        question = application.repair_question_fields({
            "id": 3, "prompt": "Rechne \\(\\frac{1}{2\\) aus.", "hint": "\\frac{1}{4} hilft",
            "options": [{"id": "a", "label": "\\(\\frac{1}\\)"}, {"id": "b", "label": "2"}],
            "expected_answer": "a", "explanation": "Weil \\(\\left( 1 \\)."})
        self.assertEqual(question["prompt"], "Rechne \\(\\frac{1}{2}\\) aus.")
        self.assertEqual(question["hint"], "\\(\\frac{1}{4}\\) hilft")
        self.assertEqual(question["options"], [{"id": "a", "label": "1"}, {"id": "b", "label": "2"}])
        self.assertEqual(question["expected_answer"], "a")
        self.assertEqual(question["explanation"], "Weil \\(( 1 \\).")

        project_id, _ = self.make_project()
        section_id, _ = self.analyse(project_id)
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            section = db.session.get(application.LearningSection, section_id)
            page_ids = json.loads(section.source_page_ids_json)
            normalized = application.normalize_exam_question({
                "section_id": section_id, "source_page_ids": page_ids[:1], "question_type": "calculation",
                "difficulty": "medium", "prompt": "Berechne \\(y = \\frac{1}{2\\cdot 4 + 1\\).",
                "expected_answer": "3", "supporting_text": "y = mx + b", "explanation": "\\frac{1}{2} \\cdot 4 + 1 = 3"},
                {section_id: section}, ["calculation"], project_id)
            self.assertIsNotNone(normalized)
            self.assertEqual(math_problems(normalized["prompt"]), [])
            self.assertEqual(normalized["explanation"], "\\(\\frac{1}{2} \\cdot 4 + 1 = 3\\)")
            self.assertEqual(normalized["supporting_text"], "y = mx + b")

            lines = application.project_section_formula_lines(project, [section])
            self.assertTrue(lines[section_id][0].startswith("y = mx + b"))
            with patch.object(application, "create_response", side_effect=self.respond):
                with self.assertRaises(ValueError):
                    application.generate_final_exam(project, [section], 5, 30, "mixed", ["calculation"])
        exam_prompt = next(call["input"] for call in self.calls if call["task_type"] == "final_exam_generation")
        self.assertIn('"formulas": ["y = mx + b', exam_prompt)
        self.assertIn(application.EXAM_FORMULA_RULE.strip(), exam_prompt)

    def test_an_old_map_is_enriched_at_read_time_and_never_written_back(self):
        project_id, (worksheet, grid) = self.make_project()
        old = {"summary": "", "terms": [], "items": [
            {"kind": "formula", "name": "y = mx + b", "detail": "Gerade", "page_id": grid, "in_notes": True},
            {"kind": "formula", "name": "b = y - m x + 1", "detail": "falsch", "page_id": worksheet, "in_notes": True}]}
        with application.app.app_context():
            section = application.LearningSection(
                project_id=project_id, position=1, title="Geraden", main_topic="Geraden",
                learning_goals_json="[]", source_page_ids_json=json.dumps([worksheet, grid]),
                topic_map_json=json.dumps(old), topic_key="geraden", estimated_minutes=15)
            db.session.add(section)
            db.session.commit()
            pages = application.section_source_pages(section)
            enriched = application.section_topic_map(section, pages)
            self.assertFalse(project_formulas.needs_enrichment(enriched))
            first, second = enriched["items"]
            self.assertEqual((first["match"], first["in_notes"], first["teacher_marked"]), ("exact", True, True))
            self.assertEqual(second["in_notes"], False)
            db.session.commit()
            self.assertEqual(json.loads(db.session.get(application.LearningSection, section.id).topic_map_json), old)


if __name__ == "__main__":
    unittest.main()

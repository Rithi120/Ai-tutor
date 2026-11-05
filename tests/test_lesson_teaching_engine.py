"""Subject-aware topic lessons and "Try again" in Projektmodus (track LESSON-BE), every model call mocked.

What is pinned here, through the real start_section_lesson and /api/answer:

* the lesson prompt follows the subject's teaching method (learnova/lessons): Ethics
  retells a thought experiment only when the notes contain one, languages teach rule,
  structure, examples and mistakes, maths shows a worked example, NwT experiments are
  split into question, hypothesis, setup, procedure, observation, result, explanation,
  history has a timeline before causes and effects, geography reads its diagram, German
  analyses a model text, other subjects and combined subjects get their own order;
* teaching comes before testing, a beginner and a secure student get different plans;
* the model's parts are held to the notes: Fachbegriffe not in the notes and readings
  the notes do not give are marked as added, an invented thought experiment is dropped;
* every file reaches the lesson (a fair share per page, labelled "[file, p. n]") and
  grading stays within its budget on a large topic;
* a first wrong answer gets the misunderstanding, a reteach and a simpler example
  instead of the solution, the same question again, and the second try is graded and
  shows the solution;
* mixed practice uses the lesson contract; coverage and the flashcards part are stored.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_lesson_teaching_engine_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from flask_login import login_user  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.contracts import VALIDATORS  # noqa: E402
from learnova.lessons import lesson_plan  # noqa: E402
from learnova.lessons.strategies import REGISTRY  # noqa: E402
from learnova.projects.terms import term_key  # noqa: E402
from learnova.quizzes import mastery_gate  # noqa: E402

db = application.db
_MISSING = object()
PASSWORD = "correct-horse-battery"


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


QUESTION = {"id": "q1", "concept": "Gerechtigkeit", "difficulty": 1, "type": "multiple_choice",
            "prompt": "Was zeigt das Gedankenexperiment?", "hint": "Denk an den Ring.",
            "options": [{"id": "a", "label": "Niemand ist freiwillig gerecht"},
                        {"id": "b", "label": "Gerechtigkeit lohnt sich nie"},
                        {"id": "c", "label": "Ringe machen unsichtbar"},
                        {"id": "d", "label": "Hirten sind ehrlich"}],
            "expected_answer": "a"}


def lesson_payload(parts, *, concept="Gerechtigkeit", explanation=""):
    return {"lesson_title": "Thema", "detected_level": "Klasse 10",
            "concepts": [{"name": concept, "evidence": "notes"}], "parts": parts, "explanation": explanation,
            "worked_example": None, "teacher_tips": [], "exceptions": [],
            "question": {**QUESTION, "concept": concept}}


def part(kind, body="", *, items=(), title="", from_notes=True, pages=(1,), answer=""):
    return {"kind": kind, "title": title or kind, "body": body, "items": list(items), "answer": answer,
            "from_notes": from_notes, "source_pages": list(pages)}


def term_entry(name, definition, page_id, *, in_notes=True):
    return {"term": name, "definition": definition, "simple_definition": definition, "example": "",
            "page_id": page_id, "page_ids": [page_id], "quote": "", "in_notes": in_notes,
            "definition_from_notes": in_notes, "key": term_key(name)}


def item_entry(kind, name, detail, page_id, *, in_notes=True):
    return {"kind": kind, "name": name, "detail": detail, "page_id": page_id, "in_notes": in_notes}


def evaluation(score, *, feedback="Geprüft.", correction="Richtig ist a.", retry=None):
    payload = {
        "evaluation": {"is_correct": score >= 80, "score": score, "feedback": feedback,
                       "correction": "" if score >= 80 else correction, "teacher_tip": "Lies die Stelle noch einmal.",
                       "exception_note": "", "skill_status": "mastered" if score >= 80 else "needs_practice"},
        "next_question": {"id": "q2", "concept": "Gerechtigkeit", "difficulty": 1, "type": "text",
                          "prompt": "Erkläre den Ring des Gyges mit eigenen Worten.", "hint": "Wer findet ihn?",
                          "options": [], "expected_answer": "Ein Hirte findet einen Ring, der unsichtbar macht."},
        "summary": None,
    }
    if retry is not None:
        payload["retry"] = retry
    return payload


def diagnosis(correct, explanation=None):
    return {
        "analysis_version": "diagnosis:v2",
        "correctness_status": "correct" if correct else "incorrect",
        "score": {"points": 1.0 if correct else 0.2, "max_points": 1.0, "rubric_evidence": [
            {"criterion": "Kernaussage", "met": correct, "evidence_ids": ["e1"]}]},
        "concepts_assessed": ["Gerechtigkeit"],
        "primary_diagnosis": {"tag": "correct" if correct else "interpretation_error",
                              "statement": "Richtig gelesen." if correct else "Die Pointe wurde verfehlt.",
                              "evidence_ids": ["e1"]},
        "secondary_diagnoses": [],
        "evidence": [{"id": "e1", "source": "student_answer", "quote": "b"},
                     {"id": "e2", "source": "expected_answer", "quote": "Niemand ist freiwillig gerecht"}],
        "prerequisite_gaps": [],
        "missing_evidence": False,
        "confidence": {"value": 0.9, "basis": "Antwort liegt vor."},
        "recommended_intervention": "increase_difficulty" if correct else "worked_example_then_practice",
        "candidate_question_constraints": {"concept": "Gerechtigkeit", "difficulty": 1},
        "student_facing_explanation": explanation if explanation is not None else (
            "Gut gelesen." if correct else "Schau dir an, was der Hirte tut."),
        "internal_diagnostic_summary": "note",
    }


class TeachingEngineCase(unittest.TestCase):
    maxDiff = None

    CONFIG_KEYS = ("TESTING", "AI_MODE", "CURRICULUM_CLEARED_SOURCES", "SUBSCRIPTION_QUOTAS_ENFORCED",
                   "RATELIMIT_ENABLED", "FEATURE_PRIVATE_FLASHCARDS", "FEATURE_ADAPTIVE_DIAGNOSTICS",
                   "FEATURE_DIAGNOSTIC_VERIFICATION", "TEST_MIN_QUESTIONS", "TEST_MAX_QUESTIONS", "KNOWLEDGE_TARGET")

    def tearDown(self):
        # Later test modules in the same process expect the app's own settings.
        for key, value in self.saved_config.items():
            if value is _MISSING:
                application.app.config.pop(key, None)
            else:
                application.app.config[key] = value
        application.limiter.enabled = self.saved_limiter

    def setUp(self):
        self.saved_config = {key: application.app.config.get(key, _MISSING) for key in self.CONFIG_KEYS}
        self.saved_limiter = application.limiter.enabled
        application.app.config.update(
            TESTING=True, AI_MODE="cached", CURRICULUM_CLEARED_SOURCES="", SUBSCRIPTION_QUOTAS_ENFORCED=False,
            RATELIMIT_ENABLED=False, FEATURE_PRIVATE_FLASHCARDS=True, FEATURE_ADAPTIVE_DIAGNOSTICS=True,
            FEATURE_DIAGNOSTIC_VERIFICATION=True, TEST_MIN_QUESTIONS=3, TEST_MAX_QUESTIONS=15,
            KNOWLEDGE_TARGET=80)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        application._curriculum_index_cache.update(key=None, index=None)
        ai_service._RECENT_RESULTS.clear()
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
        self.calls: list[dict] = []
        self.lesson_parts: list = []
        self.answers: list = []

    # ------------------------------------------------------------------ fixtures
    def make_project(self, subject, files, topic: Any, *, title="Projekt", typed=False):
        """files: [(filename, [page text, ...]), ...]; topic: the section's topic map."""

        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id, title=title, subject=subject,
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
            topic = dict(cast(dict[str, Any], raw))
            section = application.LearningSection(
                project_id=project.id, position=1, title=topic.get("title", "Thema"),
                main_topic=topic.get("title", "Thema"),
                learning_goals_json=json.dumps(topic.get("goals", ["Das Thema erklären"])),
                source_page_ids_json=json.dumps([] if typed else page_ids),
                topic_map_json=json.dumps({key: value for key, value in topic.items() if key not in ("title", "goals")}),
                topic_key=re.sub(r"\W+", "-", topic.get("title", "thema").casefold()), estimated_minutes=15)
            db.session.add(section)
            db.session.commit()
            return project.id, section.id, page_ids

    def respond(self, *, task_type, **kwargs):
        self.calls.append({"task_type": task_type, **kwargs})
        if task_type == "lesson_generation":
            parts = self.lesson_parts(kwargs) if callable(self.lesson_parts) else self.lesson_parts
            return FakeResponse(lesson_payload(parts))
        if task_type == "answer_evaluation":
            return FakeResponse(self.answers.pop(0))
        if task_type == "answer_diagnosis":
            return FakeResponse(diagnosis(self.next_correct, getattr(self, "diagnosis_explanation", None)))
        if task_type == "diagnosis_verification":
            return FakeResponse({"agrees": True, "reason": "supported", "better_tag": ""})
        if task_type == "question_generation":
            return FakeResponse({"question": {**QUESTION, "id": f"g{len(self.calls)}", "type": "text", "options": [],
                                              "expected_answer": "Niemand ist freiwillig gerecht"}})
        raise AssertionError(f"unexpected AI task {task_type}")

    def start(self, section_id, mode="learn"):
        with application.app.test_request_context():
            user = db.session.get(application.User, self.user_id)
            login_user(user)
            section = db.session.get(application.LearningSection, section_id)
            with patch.object(application, "create_response", side_effect=self.respond):
                session_id = application.start_section_lesson(section, mode)
        return session_id

    def lesson_call(self):
        return next(call for call in reversed(self.calls) if call["task_type"] == "lesson_generation")

    def prompt(self):
        return self.lesson_call()["input"]

    def planned_kinds(self, prompt):
        return re.findall(r"^\d+\. ([a-z_]+) — ", prompt, flags=re.MULTILINE)

    def lesson(self, session_id):
        return application.SESSIONS[session_id]["lesson"]

    def answer(self, session_id, given, *, retry_count=0, correct=False):
        self.next_correct = correct
        with patch.object(application, "create_response", side_effect=self.respond):
            response = self.client.post("/api/answer", json={
                "session_id": session_id, "answer": given, "hints_used": False, "retry_count": retry_count,
                "response_confidence": 50})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:500])
        return response.get_json()


# ---------------------------------------------------------------------- subject structures

GYGES = ("Der Ring des Gyges. Der Hirte Gyges findet einen Ring, der ihn unsichtbar macht. Er nutzt ihn, um "
         "ungestraft Unrecht zu tun. Glaukon fragt: Wäre jemand gerecht, wenn niemand ihn sehen könnte? "
         "Gerechtigkeit bedeutet, jedem das Seine zu geben.")
KANT = ("Pflichtethik nach Kant. Pflicht ist die Notwendigkeit einer Handlung aus Achtung vor dem Gesetz. "
        "Eine Maxime ist ein subjektiver Grundsatz des Wollens.")


def ethics_topic(page_ids):
    return {"title": "Ring des Gyges", "summary": "Platon lässt Glaukon die Geschichte vom Ring erzählen.",
            "terms": [term_entry("Gerechtigkeit", "jedem das Seine geben", page_ids[0])],
            "items": [item_entry("thought_experiment", "Ring des Gyges", "Ein Hirte wird unsichtbar.", page_ids[0]),
                      item_entry("person", "Glaukon", "stellt die Frage nach der Gerechtigkeit", page_ids[0])],
            "exam_questions": ["Erläutere die Pointe des Gedankenexperiments."]}


def kant_topic(page_ids):
    return {"title": "Pflichtethik", "summary": "Kant begründet Moral aus Pflicht.",
            "terms": [term_entry("Pflicht", "Notwendigkeit einer Handlung aus Achtung vor dem Gesetz", page_ids[0]),
                      term_entry("Maxime", "subjektiver Grundsatz des Wollens", page_ids[0])],
            "items": [item_entry("theory", "Pflichtethik", "Moral aus Achtung vor dem Gesetz", page_ids[0])],
            "exam_questions": []}


class SubjectLessonTests(TeachingEngineCase):
    def test_an_ethics_lesson_retells_the_thought_experiment_from_the_notes_and_marks_additions(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.lesson_parts = [
            part("objective", "Du kannst erklären, was das Gedankenexperiment über Gerechtigkeit zeigt."),
            part("terms", items=[{"label": "Gerechtigkeit", "text": "jedem das Seine geben"},
                                 {"label": "Tugend", "text": "eine gute Haltung"}]),
            part("thought_experiment", "Der Hirte Gyges findet einen Ring, der ihn unsichtbar macht ..."),
            part("perspectives", "Man kann den Ring auch als Bild für Macht lesen.", from_notes=False,
                 title="Zusätzliche Deutung (nicht aus deinen Notizen)"),
            part("guided_exercise", "Was würdest du mit dem Ring tun? Begründe.", answer="Musterantwort ..."),
            part("summary", "Gerechtigkeit ohne Zuschauer ist die Frage."),
        ]
        session_id = self.start(section_id)
        prompt = self.prompt()

        self.assertIn("TEACHING METHOD — Ethics and Philosophy", prompt)
        kinds = self.planned_kinds(prompt)
        self.assertEqual(kinds[:3], ["objective", "terms", "thought_experiment"])
        self.assertLess(kinds.index("thought_experiment"), kinds.index("guided_exercise"))
        self.assertEqual(kinds[-1], "summary")
        # The terms come from the notes, with their in-notes flag and page.
        self.assertIn("- Gerechtigkeit (in the notes, [Ethik-Blatt.pdf, p. 1]): jedem das Seine geben", prompt)
        self.assertIn("thought_experiment: Ring des Gyges (in the notes", prompt)
        self.assertIn("mark any other reading as an additional interpretation", prompt)
        self.assertIn("never present it as what the notes say", prompt)
        self.assertIn("Der Hirte Gyges findet einen Ring", prompt, "the page text reaches the lesson")
        # Nothing about a cave: the method is generic, the example is the student's.
        self.assertNotIn("Höhle", prompt)
        self.assertNotIn("cave", prompt.casefold())
        self.assertIn("SUBJECT METHOD (Ethics and Philosophy)", self.lesson_call()["instructions"])

        lesson = self.lesson(session_id)
        self.assertEqual(lesson["strategy"], {"key": "ethics", "name": "Ethics and Philosophy"})
        self.assertEqual(lesson["knowledge_state"], "new")
        terms_parts = [entry for entry in lesson["parts"] if entry["kind"] == "terms"]
        self.assertEqual([item["label"] for item in terms_parts[0]["items"]], ["Gerechtigkeit"])
        self.assertTrue(terms_parts[0]["from_notes"])
        self.assertEqual([item["label"] for item in terms_parts[1]["items"]], ["Tugend"])
        self.assertFalse(terms_parts[1]["from_notes"], "a term the notes do not contain is marked as added")
        reading = next(entry for entry in lesson["parts"] if entry["kind"] == "perspectives")
        self.assertFalse(reading["from_notes"], "an interpretation the notes do not give stays an addition")
        self.assertTrue(any(entry["kind"] == "thought_experiment" for entry in lesson["parts"]))
        self.assertTrue(lesson["explanation"], "the short explanation is filled from the parts")
        self.assertTrue(lesson["teaching_order_ok"])
        state = application.SESSIONS[session_id]
        self.assertEqual((state["strategy_key"], state["teaching_first"]), ("ethics", True))

    def test_an_ethics_topic_without_a_thought_experiment_gets_none(self):
        _, section_id, _ = self.make_project("Ethik", [("Kant.pdf", [KANT])], kant_topic)
        self.lesson_parts = [part("objective", "Ziel."), part("terms", items=[{"label": "Pflicht", "text": "..."}]),
                             part("thought_experiment", "Stell dir eine Höhle vor ..."),
                             part("theory", "Kant sagt ..."), part("guided_exercise", "Erkläre."),
                             part("summary", "Kurz.")]
        session_id = self.start(section_id)
        kinds = self.planned_kinds(self.prompt())
        self.assertNotIn("thought_experiment", kinds)
        self.assertIn("theory", kinds)
        lesson = self.lesson(session_id)
        self.assertNotIn("thought_experiment", [entry["kind"] for entry in lesson["parts"]],
                         "a thought experiment the notes do not contain is not presented as theirs")

    def test_an_english_grammar_lesson_teaches_rule_structure_examples_and_mistakes(self):
        notes = ("Relative clauses. We use who for people, which for things and that for both. "
                 "Structure: noun + who/which/that + clause. Example: The girl who lives next door is my friend.")
        topic = {"title": "Relative clauses", "summary": "Defining relative clauses.",
                 "terms": [], "items": [item_entry("rule", "who/which/that", "who for people, which for things", 0)],
                 "exam_questions": []}
        _, section_id, page_ids = self.make_project("Englisch", [("Grammar.pdf", [notes])], topic)
        with application.app.app_context():
            # A German-speaking student learning English: the examples stay in English.
            user = db.session.get(application.User, self.user_id)
            assert user is not None
            user.preferred_language = "de"
            db.session.commit()
        self.lesson_parts = [part("objective", "Use relative clauses."), part("rule", "who for people ..."),
                             part("structure", "noun + who + clause"), part("example", "The girl who ..."),
                             part("mistakes", "Not: the car who"), part("guided_exercise", "Combine the sentences."),
                             part("summary", "who/which/that")]
        session_id = self.start(section_id)
        prompt = self.prompt()
        kinds = self.planned_kinds(prompt)
        self.assertEqual([kind for kind in kinds if kind in ("rule", "structure", "example", "mistakes")],
                         ["rule", "structure", "example", "mistakes"])
        self.assertIn("languages", self.lesson(session_id)["strategy"]["key"])
        self.assertIn("language lesson teaching English", self.lesson_call()["instructions"],
                      "'Englisch' is recognised as the English language subject")
        # The graded formats are the language rotation, not the maths slot table.
        self.assertEqual([mastery_gate.question_type_for(n, preferred=REGISTRY["languages"].question_types)
                          for n in range(1, 7)],
                         [lesson_plan.exercise_type(REGISTRY["languages"], n) for n in range(1, 7)])
        self.answers = [evaluation(100, feedback="Correct.")]
        self.answer(session_id, "a", correct=True)
        grading = next(call for call in self.calls if call["task_type"] == "answer_evaluation")
        self.assertIn('"type": "dropdown"', grading["input"], "question 2 of a language lesson is a dropdown")
        self.assertIn("name the rule that was broken", grading["input"].casefold())

    def test_a_maths_lesson_shows_a_worked_example_before_the_guided_exercise(self):
        notes = "Binomische Formeln. (a+b)^2 = a^2 + 2ab + b^2. Beispiel: (x+3)^2 = x^2 + 6x + 9."
        topic = {"title": "Binomische Formeln", "summary": "Die erste binomische Formel.", "terms": [],
                 "items": [item_entry("formula", "(a+b)^2 = a^2 + 2ab + b^2", "erste binomische Formel", 0)],
                 "exam_questions": []}
        _, section_id, page_ids = self.make_project("Mathematik", [("Mathe.pdf", [notes])], topic)
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Warum ..."),
                             part("rule", "$$(a+b)^2 = a^2 + 2ab + b^2$$"),
                             part("worked_example", items=[{"label": "Schritt 1", "text": "a = x, b = 3"}]),
                             part("guided_exercise", "Berechne (y+2)^2.", answer="y^2 + 4y + 4"),
                             part("summary", "Merke.")]
        session_id = self.start(section_id)
        kinds = self.planned_kinds(self.prompt())
        self.assertLess(kinds.index("worked_example"), kinds.index("guided_exercise"))
        self.assertIn("MATH FORMATTING IS MANDATORY", self.lesson_call()["instructions"],
                      "'Mathematik' gets the LaTeX rule of mathematics")
        parts = self.lesson(session_id)["parts"]
        self.assertTrue(any(entry["kind"] == "worked_example" for entry in parts))
        guided = next(entry for entry in parts if entry["kind"] == "guided_exercise")
        self.assertEqual(guided["answer"], "y^2 + 4y + 4", "the guided solution stays behind its button")

    def test_an_nwt_experiment_is_split_into_the_lab_report_phases(self):
        notes = ("Versuch: Wasserpest im Licht. Frage: Bildet die Pflanze im Licht Sauerstoff? Vermutung: Ja. "
                 "Aufbau: Wasserpest im Reagenzglas unter einer Lampe. Durchführung: Lampe an, Blasen zählen. "
                 "Beobachtung: Es steigen Blasen auf. Ergebnis: Mehr Licht, mehr Blasen. Erklärung: Fotosynthese.")
        topic = {"title": "Fotosynthese", "summary": "Pflanzen bilden im Licht Sauerstoff.",
                 "terms": [term_entry("Fotosynthese", "Aufbau von Zucker mit Lichtenergie", 0)],
                 "items": [item_entry("experiment", "Wasserpest im Licht", "Im Licht steigen Blasen auf.", 0)],
                 "exam_questions": []}
        _, section_id, _ = self.make_project("NwT", [("NwT.pdf", [notes])],
                                             lambda ids: {**topic, "terms": [term_entry("Fotosynthese", "x", ids[0])],
                                                          "items": [{**topic["items"][0], "page_id": ids[0]}]})
        shuffled = [{"label": "Beobachtung", "text": "Blasen steigen auf"}, {"label": "Frage", "text": "Sauerstoff?"},
                    {"label": "Erklärung", "text": "Fotosynthese"}, {"label": "Hypothese", "text": "Ja"},
                    {"label": "Ergebnis", "text": "Mehr Licht, mehr Blasen"}, {"label": "Durchführung", "text": "Zählen"},
                    {"label": "Aufbau", "text": "Reagenzglas, Lampe"}]
        self.lesson_parts = [part("objective", "Ziel."), part("terms", items=[{"label": "Fotosynthese", "text": "x"}]),
                             part("explanation", "Licht ..."), part("experiment", items=shuffled),
                             part("guided_exercise", "Was passiert ohne Licht?"), part("summary", "Merke.")]
        session_id = self.start(section_id)
        prompt = self.prompt()
        self.assertIn("TEACHING METHOD — Science and Technology", prompt)
        kinds = self.planned_kinds(prompt)
        self.assertIn("experiment", kinds)
        experiment_line = next(line for line in prompt.splitlines() if re.match(r"\d+\. experiment — ", line))
        for phase in ("question", "hypothesis", "procedure", "observation", "result"):
            self.assertIn(phase, experiment_line.casefold())
        experiment = next(entry for entry in self.lesson(session_id)["parts"] if entry["kind"] == "experiment")
        self.assertEqual([item["label"] for item in experiment["items"]],
                         ["Frage", "Hypothese", "Aufbau", "Durchführung", "Beobachtung", "Ergebnis", "Erklärung"])

    def test_nwt_combines_the_science_and_technology_methods(self):
        _, section_id, _ = self.make_project("Naturwissenschaft und Technik", [("NwT.pdf", ["Ein Motor wandelt Energie um."])],
                                             {"title": "Motor", "terms": [], "items": [], "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Energie."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        kinds = self.planned_kinds(self.prompt())
        combined = REGISTRY["nwt"]
        self.assertEqual(self.lesson(session_id)["strategy"]["key"], "nwt")
        technology_only = set(REGISTRY["technology"].step_kinds()) - set(REGISTRY["sciences"].step_kinds())
        self.assertTrue(technology_only & set(kinds), f"a technology part is planned: {kinds}")
        self.assertEqual(kinds, [step.kind for step in lesson_plan.plan_steps(combined, mode="learn", state="new",
                                                                              item_kinds=[])])

    def test_a_history_lesson_puts_the_chronology_before_causes_and_effects(self):
        notes = "1789: Sturm auf die Bastille. Ursachen: Hunger, Schulden. Folgen: Ende des Absolutismus."
        _, section_id, _ = self.make_project("Geschichte", [("Geschichte.pdf", [notes])], lambda ids: {
            "title": "Französische Revolution", "terms": [],
            "items": [item_entry("event", "Sturm auf die Bastille", "1789 in Paris", ids[0]),
                      item_entry("date", "1789", "Beginn der Revolution", ids[0])], "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("timeline", items=[{"label": "1789", "text": "Bastille"}]),
                             part("cause_effect", items=[{"label": "Hunger", "text": "Aufstand"}]),
                             part("guided_exercise", "Ordne."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        kinds = self.planned_kinds(self.prompt())
        self.assertLess(kinds.index("timeline"), kinds.index("cause_effect"))
        self.assertEqual(self.lesson(session_id)["strategy"]["key"], "history")

    def test_a_geography_lesson_explains_the_process_and_reads_the_diagram(self):
        notes = "Der Wasserkreislauf: Verdunstung, Kondensation, Niederschlag. Das Schaubild zeigt die Pfeile."
        _, section_id, _ = self.make_project("Erdkunde", [("Geo.pdf", [notes])], lambda ids: {
            "title": "Wasserkreislauf", "terms": [],
            "items": [item_entry("diagram", "Schaubild Wasserkreislauf", "zeigt die Pfeile", ids[0]),
                      item_entry("process", "Wasserkreislauf", "Verdunstung, Kondensation, Niederschlag", ids[0])],
            "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("process", "Verdunstung ..."),
                             part("diagram", "Lies die Pfeile."), part("guided_exercise", "Beschreibe."),
                             part("summary", "Kurz.")]
        self.start(section_id)
        kinds = self.planned_kinds(self.prompt())
        self.assertIn("process", kinds)
        self.assertIn("diagram", kinds)
        self.assertIn("TEACHING METHOD — Geography", self.prompt())

    def test_a_german_lesson_includes_a_model_analysis(self):
        notes = "Der Erlkönig von Goethe ist eine Ballade. Ein Vater reitet mit seinem Kind durch die Nacht."
        _, section_id, _ = self.make_project("Deutsch", [("Deutsch.pdf", [notes])], lambda ids: {
            "title": "Ballade", "terms": [term_entry("Ballade", "Gedicht, das eine Geschichte erzählt", ids[0])],
            "items": [item_entry("work", "Der Erlkönig", "Ballade von Goethe", ids[0])], "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("model_analysis", "Musteranalyse ..."),
                             part("guided_exercise", "Analysiere Strophe 2."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        self.assertIn("model_analysis", self.planned_kinds(self.prompt()))
        self.assertEqual(self.lesson(session_id)["strategy"]["key"], "german")

    def test_music_and_computer_science_get_their_own_methods(self):
        for subject, key, expected in (("Musik", "music", "structure"), ("Informatik", "computer_science",
                                                                          "worked_example")):
            with self.subTest(subject=subject):
                self.calls.clear()
                _, section_id, _ = self.make_project(subject, [(f"{subject}.pdf", ["Notizen zum Thema."])],
                                                     {"title": subject, "terms": [], "items": [], "exam_questions": []})
                self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Erklärung."),
                                     part("guided_exercise", "Übung."), part("summary", "Kurz.")]
                session_id = self.start(section_id)
                self.assertIn(expected, self.planned_kinds(self.prompt()))
                self.assertEqual(self.lesson(session_id)["strategy"]["key"], key)

    def test_a_typed_topic_without_notes_uses_the_method_but_nothing_is_from_the_notes(self):
        _, section_id, _ = self.make_project("Politik", [("leer.pdf", ["x"])],
                                             {"title": "Gewaltenteilung"}, typed=True)
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Legislative ..."),
                             part("guided_exercise", "Ordne zu."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        prompt = self.prompt()
        self.assertIn("There are no notes", prompt)
        self.assertIn("TEACHING METHOD — Politics and Economics", prompt)
        self.assertTrue(all(not entry["from_notes"] for entry in self.lesson(session_id)["parts"]))


# ---------------------------------------------------------------------- teaching order and adaptivity

class TeachingOrderTests(TeachingEngineCase):
    def test_teaching_comes_before_testing(self):
        _, section_id, _ = self.make_project("Ethik", [("Kant.pdf", [KANT])], kant_topic)
        self.lesson_parts = [part("guided_exercise", "Erkläre die Maxime."), part("objective", "Ziel."),
                             part("terms", items=[{"label": "Pflicht", "text": "..."}]),
                             part("theory", "Kant begründet ..."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        kinds = [entry["kind"] for entry in self.lesson(session_id)["parts"]]
        self.assertLess(kinds.index("theory"), kinds.index("guided_exercise"), kinds)
        self.assertTrue(self.lesson(session_id)["teaching_order_ok"])
        self.assertIn("Teach first", self.prompt())
        page = self.client.get(f"/?session_id={session_id}").get_data(as_text=True)
        self.assertNotIn('"expected_answer"', page, "the answer key never reaches the page")

    def test_a_beginner_and_a_secure_student_get_different_plans(self):
        notes = "Lineare Funktionen: f(x) = mx + b. m ist die Steigung, b der y-Achsenabschnitt."
        _, section_id, _ = self.make_project("Mathe", [("Mathe.pdf", [notes])],
                                             {"title": "Lineare Funktionen", "terms": [], "items": [],
                                              "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Steigung ..."),
                             part("guided_exercise", "Bestimme m."), part("summary", "Kurz.")]
        first = self.start(section_id)
        new_prompt = self.prompt()
        self.assertEqual(self.lesson(first)["knowledge_state"], "new")
        self.assertIn("(state new)", new_prompt)
        self.assertIn("why", self.planned_kinds(new_prompt))
        with application.app.app_context():
            lesson = db.session.scalar(db.select(application.Lesson).where(application.Lesson.session_id == first))
            assert lesson is not None
            for _ in range(3):
                db.session.add(application.Attempt(lesson_id=lesson.id, question="q", subject="Mathematics",
                                                   concept="Lineare Funktionen", student_answer="a", score=95,
                                                   feedback="ok", difficulty=1))
            section = db.session.get(application.LearningSection, section_id)
            assert section is not None
            section.mastery_score = 92
            db.session.add(application.Attempt(lesson_id=lesson.id, question="q", subject="Mathematics",
                                               concept="Lineare Funktionen", student_answer="b", score=20,
                                               feedback="no", difficulty=1, root_cause="Vorzeichen von m vertauscht"))
            db.session.commit()
        second = self.start(section_id)
        secure_prompt = self.prompt()
        self.assertEqual(self.lesson(second)["knowledge_state"], "secure")
        self.assertIn("(state secure)", secure_prompt)
        self.assertNotIn("why", self.planned_kinds(secure_prompt), "a secure student is not lectured on why again")
        self.assertIn("Vorzeichen von m vertauscht", secure_prompt, "earlier mistakes shape the lesson")


# ---------------------------------------------------------------------- sources and budgets

class SourceTests(TeachingEngineCase):
    def test_the_lesson_prompt_carries_the_last_of_six_files(self):
        files = [(f"Datei-{number}.pdf", [f"Marker-{number}-{page} " + "Text über Ethik. " * 180
                                         for page in (1, 2)]) for number in range(1, 7)]
        _, section_id, _ = self.make_project("Ethik", files, {"title": "Alles", "terms": [], "items": [],
                                                              "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Erklärung."),
                             part("guided_exercise", "Übung."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        prompt = self.prompt()
        for number in range(1, 7):
            for page in (1, 2):
                self.assertIn(f"[Datei-{number}.pdf, p. {page}]", prompt)
                self.assertIn(f"Marker-{number}-{page}", prompt)
        source = application.SESSIONS[session_id]["source_context"]
        self.assertLessEqual(len(source), 16_000)
        self.assertIn("Marker-6-2", source)
        with application.app.app_context():
            estimated, _ = ai_service.estimate_request_tokens(
                "lesson_generation", input=prompt, instructions=self.lesson_call()["instructions"])
        self.assertLessEqual(estimated, ai_service.DEFAULT_INPUT_TOKEN_BUDGETS["lesson_generation"])

    def test_grading_a_24k_character_topic_stays_within_the_budget(self):
        files = [(f"Blatt-{number}.pdf", [f"Abschnitt {number}. " + "Der Hirte und der Ring. " * 125])
                 for number in range(1, 9)]
        self.assertGreater(sum(len(text) for _, pages in files for text in pages), 24_000)
        _, section_id, _ = self.make_project("Ethik", files, {"title": "Ring", "terms": [], "items": [],
                                                              "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Erklärung."),
                             part("guided_exercise", "Übung."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        self.answers = [evaluation(100, feedback="Richtig.")]
        self.answer(session_id, "a", correct=True)
        grading = next(call for call in self.calls if call["task_type"] == "answer_evaluation")
        with application.app.app_context():
            estimated, _ = ai_service.estimate_request_tokens(
                "answer_evaluation", input=grading["input"], instructions=grading["instructions"])
        self.assertLessEqual(estimated, ai_service.DEFAULT_INPUT_TOKEN_BUDGETS["answer_evaluation"])
        context = json.loads(grading["input"].split("Lesson data: ", 1)[1].split("\n\nReturn this exact JSON", 1)[0])
        self.assertTrue(context["uploaded_source"], "the grader still sees the notes")
        self.assertLessEqual(len(context["uploaded_source"]), 4000)
        self.assertIn("[Blatt-", context["uploaded_source"], "passages keep their page labels")

    def test_mixed_practice_uses_the_lesson_contract(self):
        project_id, section_id, _ = self.make_project("Ethik", [("Kant.pdf", [KANT])], kant_topic)
        self.lesson_parts = []
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            section = db.session.get(application.LearningSection, section_id)
            with patch.object(application, "create_response", side_effect=self.respond):
                application.start_mixed_project_practice(project, [section], "mixed_practice")
        self.assertEqual([call["task_type"] for call in self.calls], ["lesson_generation"])


# ---------------------------------------------------------------------- coverage and cards

class CoverageAndCardsTests(TeachingEngineCase):
    def test_coverage_and_the_flashcards_part_are_stored(self):
        notes = "Binomische Formeln. (a+b)^2 = a^2 + 2ab + b^2. Ein Binom ist eine Summe aus zwei Gliedern."
        _, section_id, page_ids = self.make_project("Mathematik", [("Mathe.pdf", [notes])], lambda ids: {
            "title": "Binome", "summary": "Binomische Formeln.",
            "terms": [{**term_entry("Binom", "eine Summe aus zwei Gliedern", ids[0]),
                       "quote": "Ein Binom ist eine Summe aus zwei Gliedern."}],
            "items": [item_entry("formula", "(a+b)^2 = a^2 + 2ab + b^2", "erste binomische Formel", ids[0])],
            "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Ein Binom hat zwei Glieder."),
                             part("rule", "$$ (a+b)^2 $$", pages=[1]), part("worked_example", "x + 3"),
                             part("guided_exercise", "Rechne."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        with application.app.app_context():
            section = db.session.get(application.LearningSection, section_id)
            assert section is not None
            coverage = json.loads(section.coverage_json)
            flashcard_set = db.session.scalar(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == section.project_id))
            assert flashcard_set is not None, "an old project's set is synced on its first lesson"
            set_id = flashcard_set.id
        self.assertEqual(coverage["lessons_started"], 1)
        self.assertIn(term_key("Binom"), coverage["terms_taught"])
        self.assertEqual(len(coverage["items_taught"]), 1, "the formula counts through the rule part on its page")
        self.assertTrue(coverage["last_taught_at"])
        parts = self.lesson(session_id)["parts"]
        cards = next(entry for entry in parts if entry["kind"] == "flashcards")
        self.assertEqual(cards["set_id"], set_id)
        self.assertEqual(cards["study_url"], f"/flashcards/{set_id}/study")
        # Each card keeps its own origin: the term's definition is the notes' own, the
        # formula's detail ("erste binomische Formel") is not on the page, so that card
        # and therefore the part as a whole are not "from your notes".
        self.assertIn({"label": "What does the term “Binom” mean?", "text": "eine Summe aus zwei Gliedern",
                       "from_notes": True}, cards["items"])
        formula = next(item for item in cards["items"] if "formula" in item["label"])
        self.assertFalse(formula["from_notes"])
        self.assertFalse(cards["from_notes"])
        kinds = [entry["kind"] for entry in parts]
        self.assertEqual(kinds.index("flashcards"), kinds.index("explanation") + 1,
                         "without a terms part the cards follow the first explanation")
        self.start(section_id)
        with application.app.app_context():
            section = db.session.get(application.LearningSection, section_id)
            assert section is not None
            self.assertEqual(json.loads(section.coverage_json)["lessons_started"], 2)


# ---------------------------------------------------------------------- try again

class TryAgainTests(TeachingEngineCase):
    def setUp(self):
        super().setUp()
        _, self.section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", "Gyges ..."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        self.session_id = self.start(self.section_id)

    def test_a_first_wrong_answer_gets_a_reteach_and_the_same_question_again(self):
        retry = {"misunderstanding": "Du hast übersehen, dass Gyges ungestraft handeln kann.",
                 "reteach": "Stell dir vor, niemand sieht dich: Würdest du dich anders verhalten?",
                 "simpler_example": "Ein Kind nimmt Süßigkeiten, wenn die Mutter nicht hinsieht."}
        self.answers = [evaluation(20, feedback="Die Antwort ist: Niemand ist freiwillig gerecht.",
                                   correction="Richtig ist a: Niemand ist freiwillig gerecht.", retry=retry)]
        data = self.answer(self.session_id, "b")
        grading = next(call for call in self.calls if call["task_type"] == "answer_evaluation")
        self.assertIn('"retry"', grading["input"], "the retry fields are asked for when a retry is possible")
        self.assertIn("WITHOUT giving the correct answer", grading["input"])

        self.assertTrue(data["retry"]["allowed"])
        self.assertEqual(data["retry"]["misunderstanding"], retry["misunderstanding"])
        self.assertEqual(data["retry"]["reteach"], retry["reteach"])
        self.assertEqual(data["retry"]["simpler_example"], retry["simpler_example"])
        self.assertEqual(data["retry"]["question"]["id"], "q1", "the same question again")
        self.assertNotIn("expected_answer", data["retry"]["question"])
        self.assertNotIn("withheld_correction", data["retry"]["question"])
        self.assertNotIn("correction", data["evaluation"])
        shown = json.dumps([data["evaluation"], data["diagnosis"], data["analysis"],
                            {key: value for key, value in data["retry"].items() if key != "question"}],
                           ensure_ascii=False)
        self.assertNotIn("Niemand ist freiwillig gerecht", shown, "neither feedback nor diagnosis gives the answer away")
        self.assertIsNone(data["next_question"])
        self.assertFalse(data["complete"])
        state = application.SESSIONS[self.session_id]
        self.assertEqual(state["current_question"]["id"], "q1")
        self.assertEqual(len(state["history"]), 1, "the first try still counts as evidence")
        with application.app.app_context():
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.Attempt.id))), 1)
        page = self.client.get(f"/?session_id={self.session_id}").get_data(as_text=True)
        self.assertNotIn("withheld_correction", page)

        # The second try is graded normally and shows the solution.
        self.answers = [evaluation(30, feedback="Noch nicht.", correction="")]
        second = self.answer(self.session_id, "c", retry_count=1)
        self.assertFalse(second.get("retry"))
        self.assertIn("Niemand ist freiwillig gerecht", second["evaluation"]["correction"],
                      "the kept solution is shown after the second try")
        grading = [call for call in self.calls if call["task_type"] == "answer_evaluation"][-1]
        self.assertNotIn('"retry": {', grading["input"])
        self.assertNotEqual(application.SESSIONS[self.session_id]["current_question"]["id"], "q1")

    def test_the_page_cannot_ask_for_a_second_retry(self):
        self.answers = [evaluation(20, retry={"misunderstanding": "x", "reteach": "y", "simpler_example": "z"})]
        self.assertTrue(self.answer(self.session_id, "b")["retry"]["allowed"])
        self.answers = [evaluation(20, retry={"misunderstanding": "x", "reteach": "y", "simpler_example": "z"})]
        again = self.answer(self.session_id, "b", retry_count=0)
        self.assertFalse(again.get("retry"), "a question gets one retry, whatever the page reports")
        self.assertTrue(again["evaluation"]["correction"])

    def test_a_retry_with_nothing_to_teach_is_graded_normally(self):
        # Review LESSONBEr: the retry fields are optional. When the model left them out and
        # its feedback gave the answer away (so it is replaced), the student saw "Read the
        # explanation below" above nothing and could only guess again; grade it normally.
        self.diagnosis_explanation = ""
        self.answers = [evaluation(20, feedback="Die Antwort ist: Niemand ist freiwillig gerecht.",
                                   correction="Richtig ist a: Niemand ist freiwillig gerecht.")]
        data = self.answer(self.session_id, "b")
        self.assertFalse(data.get("retry"))
        self.assertIn("Niemand ist freiwillig gerecht", data["evaluation"]["correction"])
        self.assertTrue(data["next_question"])
        question = application.SESSIONS[self.session_id]["current_question"]
        self.assertNotIn("withheld_correction", question)

    def test_feedback_without_retry_fields_is_graded_as_usual(self):
        # PF4 L1 (deliberate change): the model's feedback is never sent before the second try
        # (it gave the answer away in 4 of 6 live retries), so feedback alone is no retry: the
        # answer is graded as usual and the correction shown.
        self.answers = [evaluation(20, feedback="Schau dir an, was Gyges tut, wenn ihn niemand sieht.")]
        data = self.answer(self.session_id, "b")
        self.assertFalse(data.get("retry"))
        self.assertEqual(data["evaluation"]["feedback"], "Schau dir an, was Gyges tut, wenn ihn niemand sieht.")
        self.assertTrue(data["evaluation"]["correction"])

    def test_a_correct_first_answer_moves_on(self):
        self.answers = [evaluation(100, feedback="Richtig.")]
        data = self.answer(self.session_id, "a", correct=True)
        self.assertFalse(data.get("retry"))
        self.assertTrue(data["next_question"])


# ---------------------------------------------------------------------- FIX2 (spec audit)

CAVE = ("Das Höhlengleichnis. Menschen sitzen ihr Leben lang gefesselt in einer Höhle vor einer Wand und sehen nur "
        "Schatten. Ein Mensch wird befreit, verlässt die Höhle und sieht draußen die Sonne.")


def cave_topic(kind):
    def build(page_ids):
        return {"title": "Höhlengleichnis", "summary": "Platons Gleichnis.",
                "terms": [term_entry("Schatten", "Abbild der Dinge an der Wand", page_ids[0])],
                "items": [item_entry(kind, "Höhlengleichnis", "Menschen sehen nur Schatten an der Wand", page_ids[0])],
                "exam_questions": []}
    return build


RETELLING = "Menschen sitzen ihr Leben lang gefesselt in einer Höhle vor einer Wand und sehen nur Schatten."


class FixTwoTeachingTests(TeachingEngineCase):
    def respond(self, *, task_type, **kwargs):
        if task_type == "tutor_chat":
            self.calls.append({"task_type": task_type, **kwargs})
            return FakeResponse("Gute Frage.")
        return super().respond(task_type=task_type, **kwargs)

    def cave_parts(self):
        return [part("objective", "Du kannst das Gleichnis erklären."),
                part("terms", items=[{"label": "Schatten", "text": "Abbild der Dinge an der Wand"}]),
                part("thought_experiment", RETELLING), part("argument", "Was ist wirklich?"),
                part("guided_exercise", "Erkläre.", answer="..."), part("summary", "Kurz.")]

    # P2-ETH-1: the retelling must not depend on the kind label of the notes' example.
    def test_the_notes_example_is_retold_when_the_extraction_called_it_an_example(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-1.pdf", [CAVE])], cave_topic("example"))
        self.lesson_parts = self.cave_parts()
        session_id = self.start(section_id)
        self.assertIn("thought_experiment", self.planned_kinds(self.prompt()))
        kinds = [entry["kind"] for entry in self.lesson(session_id)["parts"]]
        self.assertIn("thought_experiment", kinds, "the model's retelling is kept")

    def test_the_notes_story_is_retold_when_the_extraction_called_it_a_theory(self):
        # FIXR review (P2-ETH-1 "same item as theory"): the retelling was dropped because
        # the map had a "theory" item, although every sentence of it is on the page.
        _, section_id, _ = self.make_project("Ethik", [("Ethik-1.pdf", [CAVE])], cave_topic("theory"))
        retold = ("Stell dir vor: Menschen sitzen ihr ganzes Leben lang gefesselt in einer Höhle und blicken auf "
                  "eine Wand. Dort sehen sie nur Schatten. Ein Mensch wird befreit, verlässt die Höhle und sieht "
                  "draußen zum ersten Mal die Sonne.")
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", retold),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        parts = self.lesson(session_id)["parts"]
        kept = [entry for entry in parts if entry["kind"] == "thought_experiment"]
        self.assertEqual([(entry["body"], entry["from_notes"]) for entry in kept], [(retold, True)])

    def test_an_old_topic_without_a_topic_map_keeps_the_retelling(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-1.pdf", [CAVE])], cave_topic("example"))
        with application.app.app_context():
            section = db.session.get(application.LearningSection, section_id)
            assert section is not None
            section.topic_map_json = ""
            section.examples_json = json.dumps([f"Höhlengleichnis: {RETELLING}"])
            db.session.commit()
        self.lesson_parts = self.cave_parts()
        session_id = self.start(section_id)
        prompt = self.prompt()
        self.assertIn("thought_experiment", self.planned_kinds(prompt))
        self.assertIn("Leave this part out if the material has nothing for it.", prompt)
        self.assertIn("thought_experiment", [entry["kind"] for entry in self.lesson(session_id)["parts"]])

    # P2-NOINV-4: a reading the notes do not give never carries the "from your notes" badge.
    def test_interpretations_the_notes_do_not_support_are_marked_as_added(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-1.pdf", [CAVE])], cave_topic("thought_experiment"))
        self.lesson_parts = [
            part("objective", "Ziel."),
            part("thought_experiment", RETELLING + " Das Feuer in der Höhle steht für die sozialen Medien von heute."),
            part("perspectives", "Die Höhle steht eigentlich für Social Media und das Internet."),
            part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        parts = self.lesson(session_id)["parts"]
        retold = [entry for entry in parts if entry["kind"] == "thought_experiment"]
        self.assertEqual([entry["from_notes"] for entry in retold], [True, False])
        self.assertNotIn("Medien", retold[0]["body"])
        self.assertIn("Medien", retold[1]["body"])
        reading = next(entry for entry in parts if entry["kind"] == "perspectives")
        self.assertFalse(reading["from_notes"])

    # P4-07: a planned key-terms part the model left out comes from the topic map.
    def test_a_planned_terms_part_the_model_left_out_is_built_from_the_topic_map(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", "Der Hirte Gyges findet einen "
                                                                                    "Ring."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        self.assertIn("terms", self.planned_kinds(self.prompt()))
        parts = self.lesson(session_id)["parts"]
        self.assertEqual([entry["kind"] for entry in parts][:2], ["objective", "terms"])
        self.assertEqual(parts[1]["items"], [{"label": "Gerechtigkeit", "text": "jedem das Seine geben",
                                              "from_notes": True}])

    # P2-ETH-2 / P4-09: graded questions move to an exam-style task with justified answers.
    def test_the_graded_questions_move_from_comprehension_to_the_exam_task(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", "Gyges ..."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        application.SESSIONS[session_id]["test_total"] = 3
        self.answers = [evaluation(100, feedback="Richtig."), evaluation(100, feedback="Richtig.")]
        self.answer(session_id, "a", correct=True)
        self.answer(session_id, "Ein Hirte findet einen Ring.", correct=True)
        first, second = [call["input"] for call in self.calls if call["task_type"] == "answer_evaluation"]
        self.assertIn("NEXT QUESTION STAGE: if the answer is correct, the next question is an interpretation "
                      "question", first)
        self.assertNotIn("Nimm Stellung", first)
        self.assertIn("the next question is an exam-style task that requires a justified answer", second)
        self.assertIn("Exam style in Ethics and Philosophy:", second)
        self.assertIn("Nimm Stellung", second)

    # P4-02: the "Why?" panel cannot show the expected answer before the second try.
    def test_the_why_panel_cannot_reveal_the_answer_during_a_retry(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", "Gyges ..."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        self.answers = [evaluation(20, retry={"misunderstanding": "Du hast übersehen, was Gyges tut.",
                                              "reteach": "Stell dir vor, niemand sieht dich.",
                                              "simpler_example": "Ein Kind nimmt Süßigkeiten."})]
        data = self.answer(session_id, "b")
        self.assertTrue(data["retry"]["allowed"])
        self.assertNotIn("attempt_id", data["diagnosis"], "no detail panel before the second try")
        with application.app.app_context():
            attempt_id = db.session.scalar(db.select(application.Attempt.id))
        detail = self.client.get(f"/api/diagnosis/{attempt_id}").get_json()["diagnosis"]
        shown = json.dumps(detail, ensure_ascii=False)
        self.assertNotIn("Niemand ist freiwillig gerecht", shown)
        self.assertEqual(detail["detail"]["rubric"], [])
        self.answers = [evaluation(30, feedback="Noch nicht.", correction="")]
        second = self.answer(session_id, "c", retry_count=1)
        self.assertIn("attempt_id", second["diagnosis"], "the graded second try has its panel")
        after = self.client.get(f"/api/diagnosis/{attempt_id}").get_json()["diagnosis"]
        self.assertIn("expected_answer", [item.get("source") for item in after["detail"]["evidence"]],
                      "once the retry is answered, the first try's detail is complete again")

    # P4-03: the reteach and the simpler example are checked for the answer too.
    def test_a_reteach_or_example_that_states_the_answer_is_left_out(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", "Gyges ..."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        self.answers = [evaluation(20, retry={
            "misunderstanding": "Du hast übersehen, was Gyges tut, wenn ihn niemand sieht.",
            "reteach": "Die richtige Antwort ist: Niemand ist freiwillig gerecht.",
            "simpler_example": "Glaukon sagt: Niemand ist freiwillig gerecht."})]
        data = self.answer(session_id, "b")
        self.assertTrue(data["retry"]["allowed"])
        self.assertEqual((data["retry"]["reteach"], data["retry"]["simpler_example"]), ("", ""))
        self.assertTrue(data["retry"]["misunderstanding"])
        shown = {key: value for key, value in data["retry"].items() if key != "question"}
        self.assertNotIn("Niemand ist freiwillig gerecht", json.dumps([shown, data["evaluation"]], ensure_ascii=False))

    # r: a two-option question is solved by elimination on a second try.
    def test_a_two_option_question_is_graded_without_a_retry(self):
        _, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", "Gyges ..."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        for question in ({**QUESTION, "type": "true_false", "options": [], "expected_answer": "true"},
                         {**QUESTION, "options": QUESTION["options"][:2]}):
            with self.subTest(question=question["type"]):
                application.SESSIONS[session_id]["current_question"] = dict(question)
                application.SESSIONS[session_id]["history"] = []
                self.answers = [evaluation(20, retry={"misunderstanding": "x y z", "reteach": "y", "simpler_example": "z"})]
                data = self.answer(session_id, "b")
                self.assertIsNone(data["retry"])
                self.assertTrue(data["evaluation"]["correction"])

    # P2-EXAM-3: exams and mixed practice follow the subject.
    def test_an_ethics_practice_exam_uses_the_subject_method_and_its_exam_style(self):
        project_id, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        self.assertNotIn("calculation", application.mock_exam_types("Ethik"))
        self.assertIn("calculation", application.mock_exam_types("Mathe"))
        self.assertIn("fill_blank", application.mock_exam_types("Englisch"))
        captured = {}

        def capture(**kwargs):
            captured.update(kwargs)
            raise RuntimeError("stop after the prompt")

        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            section = db.session.get(application.LearningSection, section_id)
            assert project is not None and section is not None
            with patch.object(application, "create_response", side_effect=capture):
                with self.assertRaises(RuntimeError):
                    application.generate_final_exam(project, [section], 6, 30, "mixed",
                                                    application.mock_exam_types(project.subject))
            with patch.object(application, "create_response", side_effect=self.respond):
                session_id = application.start_mixed_project_practice(project, [section], "mixed_practice")
        self.assertIn("SUBJECT METHOD (Ethics and Philosophy)", captured["instructions"])
        self.assertIn("Exam style in Ethics and Philosophy:", captured["input"])
        self.assertIn("Nimm Stellung", captured["input"])
        self.assertEqual(application.SESSIONS[session_id]["strategy_key"], "ethics")

    # P4-04: a project for another class is taught at that class's level everywhere.
    def test_a_project_for_another_class_is_pitched_at_that_class(self):
        project_id, section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            project.grade = "5"
            db.session.commit()
        self.lesson_parts = [part("objective", "Ziel."), part("thought_experiment", "Gyges ..."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        instructions = self.lesson_call()["instructions"]
        self.assertIn("The learner is in grade 5 (", instructions)
        self.assertNotIn("grade 10 (", instructions, "the profile's class 10 does not contradict the project")
        self.assertIn("Grade 5", self.prompt())
        self.answers = [evaluation(100, feedback="Richtig.")]
        self.answer(session_id, "a", correct=True)
        grading = next(call for call in self.calls if call["task_type"] == "answer_evaluation")
        self.assertIn("The learner is in grade 5 (", grading["instructions"])

    # P2-CHAT-8 / P4-11 / p: the lesson chat sees the parts and the passages that matter.
    def test_the_chat_sees_the_parts_and_the_passage_of_a_later_file(self):
        first = "Pflichtethik. " + "Kant begründet die Moral aus der Pflicht und aus Achtung vor dem Gesetz. " * 70
        second = "Utilitarismus. Bentham sagt: Gut ist, was das Glück aller Betroffenen vergrößert."
        _, section_id, _ = self.make_project("Ethik", [("A.pdf", [first]), ("B.pdf", [second])], {
            "title": "Ethik", "terms": [], "items": [], "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel."),
                             part("theory", "Der Utilitarismus misst Handlungen an ihren Folgen.", pages=[1]),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        with patch.object(application, "create_response", side_effect=self.respond):
            response = self.client.post("/api/chat", json={"session_id": session_id,
                                                           "message": "Was sagt Bentham über das Glück?"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        chat = next(call for call in self.calls if call["task_type"] == "tutor_chat")
        # The lesson state is plain text (app.lesson_state_text): one "key:\n<<<\n…\n>>>"
        # block per text field, so the model never sees JSON-escaped LaTeX.
        state = chat["input"].split("lesson state:\n", 1)[1].split("\n\nConversation so far", 1)[0]
        context = dict(re.findall(r"^(\w+):\n<<<\n(.*?)\n>>>$", state, re.S | re.M))
        self.assertIn("[B.pdf, p. 1]", context["source_excerpt"])
        self.assertIn("Glück aller Betroffenen", context["source_excerpt"])
        self.assertIn("Der Utilitarismus misst Handlungen an ihren Folgen.", context["lesson_parts"])
        with application.app.app_context():
            estimated, _ = ai_service.estimate_request_tokens("tutor_chat", input=chat["input"],
                                                              instructions=chat["instructions"])
        self.assertLessEqual(estimated, ai_service.DEFAULT_INPUT_TOKEN_BUDGETS["tutor_chat"])

    # P4-12 / q: parts name the file of the page they cite.
    def test_parts_name_the_file_of_the_page_they_cite(self):
        duty = "Pflicht ist die Notwendigkeit einer Handlung aus Achtung vor dem Gesetz."
        use = "Der Utilitarismus fragt nach dem Nutzen einer Handlung für das Glück aller."
        _, section_id, page_ids = self.make_project("Ethik", [("A.pdf", [duty]), ("B.pdf", [use])], lambda ids: {
            "title": "Ethik", "terms": [],
            "items": [item_entry("theory", "Nutzenprinzip", "Glück aller", ids[1])], "exam_questions": []})
        self.lesson_parts = [part("objective", "Ziel.", pages=()),
                             part("theory", "Es zählt der Nutzen einer Handlung für das Glück aller.", pages=[1]),
                             part("guided_exercise", "Erkläre.", pages=()), part("summary", "Kurz.", pages=[1])]
        session_id = self.start(section_id)
        parts = {entry["kind"]: entry for entry in self.lesson(session_id)["parts"]}
        self.assertEqual(parts["theory"]["source_labels"], ["B.pdf p. 1"])
        self.assertEqual(parts["theory"]["source_page_ids"], [page_ids[1]])
        self.assertEqual(parts["summary"]["source_labels"], ["A.pdf p. 1 / B.pdf p. 1"])
        with application.app.app_context():
            section = db.session.get(application.LearningSection, section_id)
            assert section is not None
            coverage = json.loads(section.coverage_json)
        self.assertEqual(len(coverage["items_taught"]), 1, "the theory part resolved to the item's own page")

    # P4-13 / u: a topic whose pages cannot be used says so.
    def test_a_topic_whose_pages_are_all_excluded_says_it_is_taught_from_school_content(self):
        _, section_id, page_ids = self.make_project("Ethik", [("Ethik-Blatt.pdf", [GYGES])], ethics_topic)
        with application.app.app_context():
            for page_id in page_ids:
                page = db.session.get(application.ProjectPage, page_id)
                assert page is not None
                page.excluded = True
            db.session.commit()
        self.lesson_parts = [part("objective", "Ziel."), part("explanation", "Erklärung."),
                             part("guided_exercise", "Erkläre."), part("summary", "Kurz.")]
        session_id = self.start(section_id)
        self.assertIn("There are no notes", self.prompt())
        lesson = self.lesson(session_id)
        self.assertEqual(lesson["notice"], "This topic has no readable pages, so Learnova explains it from "
                                           "standard school content.")
        self.assertFalse(any(entry["from_notes"] for entry in lesson["parts"]))

    # P4-05: a diagram, process or experiment part shows the diagram of the student's page.
    def test_a_process_part_shows_the_diagram_from_the_students_own_page(self):
        notes = ("Fotosynthese. Pflanzen bilden mit Licht aus Wasser und Kohlenstoffdioxid Traubenzucker und "
                 "Sauerstoff.")
        project_id, section_id, page_ids = self.make_project("Biologie", [("Bio.pdf", [notes])], lambda ids: {
            "title": "Fotosynthese", "terms": [], "items": [item_entry("process", "Fotosynthese", "Licht", ids[0])],
            "exam_questions": []})
        with application.app.app_context():
            db.session.add(application.DocumentBlock(page_id=page_ids[0], block_order=1, block_type="diagram",
                                                     content="Blatt Licht CO2 O2", confidence_status="high"))
            db.session.commit()
            block_id = db.session.scalar(db.select(application.DocumentBlock.id))
        self.lesson_parts = [part("objective", "Ziel."),
                             part("process", "Pflanzen bilden mit Licht Traubenzucker und Sauerstoff.", pages=[1]),
                             part("summary", "Licht wird zu Zucker.", pages=[1]),
                             part("guided_exercise", "Erkläre.")]
        session_id = self.start(section_id)
        parts = {entry["kind"]: entry for entry in self.lesson(session_id)["parts"]}
        self.assertEqual(parts["process"]["media"], [{
            "block_id": block_id, "page_id": page_ids[0], "labels": "Blatt Licht CO2 O2",
            "url": f"/projects/{project_id}/blocks/{block_id}/region"}])
        self.assertNotIn("media", parts["summary"], "only parts a picture explains get one")

    # P3-05: the extraction keeps only Fachbegriffe that matter for the topic.
    def test_the_topic_extraction_asks_for_subject_terms_not_everyday_words(self):
        prompt = application.project_topic_prompt("Ethik", [])
        self.assertIn("Fachbegriffe of this subject a student must know for the topic's learning goals or exam",
                      prompt)
        self.assertIn("no everyday words that need no explanation", prompt)

    # P2-NWT-7 / P4-10 / t: NwT and chemistry get formula formatting and a grading rule.
    def test_nwt_and_the_other_subjects_have_formula_and_grading_rules(self):
        from learnova.analysis.service import _subject_rule
        from learnova.diagnostics.prompts import subject_rule

        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            for subject in ("NwT", "Chemie", "Mathe", "Physik"):
                with self.subTest(subject=subject):
                    self.assertIn("MATH FORMATTING IS MANDATORY", application.tutor_instructions(subject))
            self.assertNotIn("MATH FORMATTING IS MANDATORY", application.tutor_instructions("Ethik"))
        general_analysis, general_diagnosis = _subject_rule("Klingonisch"), subject_rule("Klingonisch")
        for subject in ("NwT", "Musik", "Kunst", "Sport", "Technik", "Informatik", "Ethik", "Philosophie",
                        "Religion", "Gemeinschaftskunde", "Wirtschaft", "Latein", "Französisch", "Spanisch"):
            with self.subTest(subject=subject):
                self.assertNotEqual(_subject_rule(subject), general_analysis)
                self.assertNotEqual(subject_rule(subject), general_diagnosis)


class LessonContextTests(unittest.TestCase):
    """The pure helpers behind the prompts (learnova/lessons/lesson_context.py)."""

    def test_fair_shares_serve_short_pages_fully_and_never_exceed_the_room(self):
        from learnova.lessons.lesson_context import fair_shares
        self.assertEqual(fair_shares([100, 5000, 5000], 4100), [100, 2000, 2000])
        shares = fair_shares([9000] * 7, 16000)
        self.assertEqual(sum(shares), 16000)
        self.assertLessEqual(max(shares) - min(shares), 7, "long pages share the room evenly")
        self.assertEqual(fair_shares([], 100), [])

    def test_bounded_pages_keep_every_label_within_the_limit(self):
        from learnova.lessons.lesson_context import SourcePage, bounded_pages, page_label, source_block
        pages = [SourcePage(index, page_label(f"Datei {index}.pdf", 1), "Wort " * 2000) for index in range(40)]
        block = source_block(bounded_pages(pages))
        self.assertLessEqual(len(block), 16_000)
        self.assertTrue(all(f"[Datei {index}.pdf, p. 1]" in block for index in range(40)))

    def test_the_excerpt_picks_the_passages_of_the_question(self):
        from learnova.lessons.lesson_context import SourcePage, relevant_excerpt
        pages = [SourcePage(1, "[A.pdf, p. 1]", "Kant und die Pflicht.\n\n" + "Fülltext ohne Bezug. " * 300),
                 SourcePage(2, "[B.pdf, p. 1]", "Der Hirte Gyges findet einen Ring, der unsichtbar macht.")]
        excerpt = relevant_excerpt(pages, "Was macht der Ring des Gyges?", 1000)
        self.assertIn("[B.pdf, p. 1] Der Hirte Gyges", excerpt)
        self.assertLessEqual(len(excerpt), 1000)
        self.assertNotIn("Fülltext", excerpt)
        self.assertEqual(excerpt, relevant_excerpt(pages, "Was macht der Ring des Gyges?", 1000), "deterministic")
        spread = relevant_excerpt(pages, "zzz", 4000)
        self.assertIn("[A.pdf, p. 1]", spread)
        self.assertIn("[B.pdf, p. 1]", spread, "without overlap every page still contributes")

    def test_the_topic_map_block_is_bounded_and_says_what_it_left_out(self):
        from learnova.lessons.lesson_context import topic_map_block
        topic_map = {"summary": "S" * 2000,
                     "terms": [term_entry(f"Begriff{index}", "Definition " * 30, 1) for index in range(40)],
                     "items": [item_entry("example", f"Beispiel{index}", "Detail " * 40, 1) for index in range(40)],
                     "exam_questions": ["Frage?"] * 10}
        block = topic_map_block(topic_map, goals=["Ziel"])
        self.assertLessEqual(len(block), 5000)
        self.assertIn("more not listed here", block)
        self.assertIn("Learning goals: Ziel", block)
        self.assertEqual(topic_map_block({}), "")

    def test_plan_item_kinds_count_only_what_is_in_the_notes(self):
        from learnova.lessons.lesson_context import plan_item_kinds
        self.assertIsNone(plan_item_kinds({}))
        kinds = plan_item_kinds({"terms": [term_entry("Pflicht", "x", 1)],
                                 "items": [item_entry("example", "Erfunden", "x", 1, in_notes=False),
                                           item_entry("theory", "Pflichtethik", "x", 1)]})
        self.assertEqual(kinds, ["theory", "term"])

    def test_reveals_answer(self):
        from learnova.lessons.lesson_context import reveals_answer
        self.assertTrue(reveals_answer("Richtig wäre: niemand ist freiwillig gerecht.", QUESTION))
        self.assertFalse(reveals_answer("Schau dir an, was Gyges mit dem Ring tut.", QUESTION))
        written = {"type": "text", "options": [], "expected_answer": "Photosynthese"}
        self.assertTrue(reveals_answer("Die Antwort heißt Photosynthese.", written))
        self.assertFalse(reveals_answer("x = 3 ist falsch", {"type": "text", "options": [], "expected_answer": "3"}))

    def test_reveals_a_short_numeric_answer(self):
        # Review LESSONBEr: a calculation's answer is usually a short number ("42", "x = 3"),
        # and feedback like "Das richtige Ergebnis ist 42." passed the four-character rule,
        # so a first wrong answer in maths could be shown the solution before the retry.
        from learnova.lessons.lesson_context import reveals_answer
        calculation = {"type": "calculation", "options": [], "prompt": "Berechne 6 · 7.", "expected_answer": "42"}
        self.assertTrue(reveals_answer("Das richtige Ergebnis ist 42.", calculation))
        self.assertTrue(reveals_answer("Richtig wäre $$6 \\cdot 7 = 42$$", calculation))
        self.assertFalse(reveals_answer("Du hast 6 · 7 als 48 gerechnet.", calculation))
        self.assertFalse(reveals_answer("Prüfe 420 noch einmal.", calculation), "a number inside another is not it")
        self.assertTrue(reveals_answer("Die Lösung ist x=3.", {"type": "text", "options": [], "prompt": "Löse 2x = 6.",
                                                              "expected_answer": "x = 3"}))
        choice = {"type": "multiple_choice", "prompt": "Wie viel ist 6 · 4?", "expected_answer": "b",
                  "options": [{"id": "a", "label": "12"}, {"id": "b", "label": "24"}]}
        self.assertTrue(reveals_answer("Die richtige Antwort ist 24.", choice))
        given = {"type": "calculation", "options": [], "prompt": "Ist 12 durch 4 teilbar? Gib 12 : 4 an.",
                 "expected_answer": "12"}
        self.assertFalse(reveals_answer("Schau dir die 12 noch einmal an.", given),
                         "a number the question itself shows gives nothing away")


class ContractTests(unittest.TestCase):
    def test_a_lesson_with_parts_but_no_explanation_is_accepted_and_filled(self):
        lesson = lesson_payload([part("objective", "Ziel."), part("explanation", "Die Erklärung.")])
        VALIDATORS["lesson_generation"](lesson, {})
        repaired = application.repair_lesson(json.loads(json.dumps(lesson)), "Ethics")
        self.assertIn("Die Erklärung.", repaired["explanation"])
        self.assertEqual([entry["kind"] for entry in repaired["parts"]], ["objective", "explanation"])

    def test_a_lesson_without_parts_or_explanation_is_still_refused(self):
        with self.assertRaises(ai_service.AIValidationError):
            VALIDATORS["lesson_generation"](lesson_payload([]), {})

    def test_the_shape_shows_the_parts(self):
        self.assertIn('"parts":[{"kind"', application.LESSON_JSON_SHAPE)
        self.assertIn('"source_pages"', application.LESSON_JSON_SHAPE)



class TaughtEvidenceTests(unittest.TestCase):
    """Spec audit (FIX1, P5-02): "p. 1" names a page per document, so in a topic built from
    two documents a part citing p. 1 proves nothing about either page."""

    def test_a_page_number_two_documents_share_does_not_count_an_item_as_taught(self):
        from learnova.lessons import lesson_context

        topic_map = {"terms": [], "items": [
            {"kind": "experiment", "name": "Fallversuch", "page_id": 11, "in_notes": True},
            {"kind": "experiment", "name": "Pendelversuch", "page_id": 22, "in_notes": True},
            {"kind": "experiment", "name": "Kugelversuch", "page_id": 23, "in_notes": True}]}
        parts = [{"kind": "experiment", "title": "Ein Versuch", "body": "Wir lassen etwas fallen.",
                  "source_pages": [1, 2]}]
        # Page 11 is p. 1 of file A, page 22 is p. 1 of file B, page 23 is p. 2 of file B.
        _terms, items = lesson_context.taught_evidence(parts, topic_map, {11: 1, 22: 1, 23: 2})
        self.assertEqual(items, ["kugelversuch"],
                         "only the unambiguous page counts; the shared p. 1 proves nothing")
        _terms, items = lesson_context.taught_evidence(parts, topic_map, {11: 1, 23: 2})
        self.assertIn("fallversuch", items, "one document: p. 1 is that page")


if __name__ == "__main__":
    unittest.main()

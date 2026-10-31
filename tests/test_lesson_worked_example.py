"""A weak worked example must never stop a student from learning the topic.

Regression: Project Mode lessons failed with "worked_example.problem must be non-empty
text" when the model left the example's problem empty - on the first try and again on
the corrective retry - so the whole lesson was refused ("Die KI-Antwort konnte nicht
geprüft werden") and the student could not learn in the study planner at all.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_worked_example_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services.contracts import VALIDATORS  # noqa: E402
from learnova.ai_services.service import AIValidationError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
QUESTION = {"id": "q1", "concept": "Brüche", "difficulty": 1, "type": "multiple_choice",
            "prompt": "Was ist 1/2 + 1/2?", "hint": "Gleicher Nenner.",
            "options": [{"id": "a", "label": "1"}, {"id": "b", "label": "2"}], "expected_answer": "a"}


def lesson(**worked):
    data = {"lesson_title": "Brüche addieren", "concepts": [{"name": "Brüche"}],
            "explanation": "Bei gleichem Nenner addiert man die Zähler.", "question": QUESTION}
    if worked:
        data["worked_example"] = worked.get("value", worked)
    return data


class WorkedExampleContractTests(unittest.TestCase):
    validate = staticmethod(VALIDATORS["lesson_generation"])

    def test_an_empty_problem_with_steps_is_accepted(self):
        self.validate(lesson(problem="", steps=["1/2 + 1/2 = 2/2", "2/2 = 1"], answer="1"), {})

    def test_a_missing_example_is_accepted(self):
        self.validate(lesson(), {})

    def test_an_example_with_nothing_in_it_never_fails_the_lesson(self):
        # Both the model's first try and its retry can come back like this; the lesson
        # must still start (repair_worked_example then drops the example).
        for worked in ({"problem": "", "steps": ["", "  "], "answer": ""}, {}, "nonsense", []):
            self.validate(lesson(value=worked), {})

    def test_a_lesson_without_a_question_is_still_refused(self):
        data = lesson()
        del data["question"]
        with self.assertRaises(AIValidationError):
            self.validate(data, {})

    def test_a_normal_example_still_passes(self):
        self.validate(lesson(problem="Berechne 1/4 + 2/4.", steps=["Zähler addieren: 3/4"], answer="3/4"), {})


class LevelTests(unittest.TestCase):
    def test_every_form_of_a_level_becomes_one_to_three(self):
        for value, expected in ((1, 1), (2.0, 2), ("3", 3), ("Medium", 2), ("schwer", 3), ("leicht", 1),
                                (5, 3), (0, 1), (None, 1), (True, 1), ("banana", 1)):
            self.assertEqual(application.question_level(value), expected, value)

    def test_the_validator_takes_what_the_app_can_repair_and_nothing_else(self):
        validate = VALIDATORS["lesson_generation"]
        validate({**lesson(), "concepts": ["Brüche"], "question": {**QUESTION, "id": 7, "difficulty": "2"}}, {})
        for broken in ({"concepts": [""]}, {"concepts": [3]}):
            with self.subTest(broken=broken), self.assertRaises(AIValidationError):
                validate({**lesson(), **broken}, {})
        # An unreadable level or a missing id is a default, not a reason to refuse a lesson.
        for odd in ({"difficulty": "banana"}, {"difficulty": True}, {"id": ""}):
            with self.subTest(odd=odd):
                validate({**lesson(), "question": {**QUESTION, **odd}}, {})
                repaired = application.repair_lesson({**lesson(), "question": {**QUESTION, **odd}})["question"]
                self.assertEqual((repaired["id"] or "", repaired["difficulty"]),
                                 (odd.get("id", "q1") or "q1", 1 if "difficulty" in odd else QUESTION["difficulty"]))


class QuestionShapeTests(unittest.TestCase):
    """The question shapes gpt-oss-20b sent live on 2026-10-31, on the first try and the
    retry, which failed every Projektmodus lesson ("question.expected_answer is required",
    then "question.difficulty is unsupported")."""

    FIRST_TRY = {"id": "q1", "type": "multiple_choice", "prompt": "Welche Aussage beschreibt die Cristae?",
                 "options": [{"option": "A", "text": "Sie speichern Wasser."},
                             {"option": "B", "text": "Sie vergrößern die Oberfläche der inneren Membran."}],
                 "answer": "B"}
    RETRY = {"id": "q1", "type": "multiple_choice", "prompt": "Was bildet die innere Membran?",
             "options": ["Sie schützt das Zellinnere.", "Sie bildet Cristae."],
             "answer": "Sie bildet Cristae.", "concept": "Innere Membran und Cristae"}

    def test_both_live_shapes_are_accepted_and_repaired(self):
        validate = VALIDATORS["lesson_generation"]
        for question in (self.FIRST_TRY, self.RETRY):
            with self.subTest(question=question["prompt"]):
                validate({**lesson(), "concepts": ["Mitochondrien"], "question": question}, {})
        first = application.repair_lesson({**lesson(), "concepts": ["Mitochondrien"], "question": dict(self.FIRST_TRY)})
        self.assertEqual(first["question"]["expected_answer"], "B")
        self.assertEqual(first["question"]["options"][1]["label"], "Sie vergrößern die Oberfläche der inneren Membran.")
        self.assertEqual((first["question"]["concept"], first["question"]["difficulty"], first["question"]["hint"]),
                         ("Mitochondrien", 1, ""))
        retry = application.repair_lesson({**lesson(), "question": dict(self.RETRY)})
        self.assertEqual(retry["question"]["expected_answer"], "Sie bildet Cristae.")
        self.assertEqual(retry["question"]["concept"], "Innere Membran und Cristae")

    def test_answers_become_option_ids(self):
        from learnova.ai_services.contracts import repair_question
        options = [{"id": "a", "label": "ATP"}, {"id": "b", "label": "DNA"}]
        self.assertEqual(repair_question({**QUESTION, "options": options, "expected_answer": "DNA"})["expected_answer"], "b")
        self.assertEqual(repair_question({**QUESTION, "options": options, "expected_answer": "B"})["expected_answer"], "b")
        flagged = [{"id": "a", "label": "ATP", "correct": True}, {"id": "b", "label": "DNA"}]
        question = {key: value for key, value in QUESTION.items() if key != "expected_answer"}
        self.assertEqual(repair_question({**question, "options": flagged})["expected_answer"], "a")
        self.assertEqual(repair_question({**question, "options": ["ATP", "DNA"], "answer": "b"})["expected_answer"], "DNA")

    def test_a_choice_question_without_any_answer_is_still_refused(self):
        question = {key: value for key, value in QUESTION.items() if key != "expected_answer"}
        with self.assertRaises(AIValidationError):
            VALIDATORS["lesson_generation"]({**lesson(), "question": question}, {})

    def test_the_lesson_prompt_shows_the_question_fields(self):
        from learnova.ai_services.prompts import SCHEMA_SUMMARIES
        for text in (SCHEMA_SUMMARIES["lesson_generation"], application.LESSON_JSON_SHAPE):
            for field in ('"expected_answer"', '"options"', '"label"', '"difficulty"', '"concept"'):
                self.assertIn(field, text)
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("Return the normal lesson JSON shape", source)


class RepairTests(unittest.TestCase):
    def test_repair_keeps_what_is_usable(self):
        repaired = application.repair_worked_example(
            lesson(problem="  ", steps=["1/2 + 1/2", "", None, "= 1"], answer=None))["worked_example"]
        self.assertEqual(repaired, {"problem": "", "steps": ["1/2 + 1/2", "= 1"], "answer": ""})

    def test_repair_drops_an_unusable_example(self):
        self.assertIsNone(application.repair_worked_example(lesson(value="nonsense"))["worked_example"])
        self.assertIsNone(application.repair_worked_example(lesson())["worked_example"])
        self.assertIsNone(application.repair_worked_example(
            lesson(problem="", steps=[], answer="1"))["worked_example"])

    def test_the_lesson_page_hides_a_missing_example(self):
        script = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('document.querySelector(".example-card")?.classList.toggle("hidden"', script)
        self.assertNotIn("lesson.worked_example.problem", script)


class ProjectLessonStartTests(unittest.TestCase):
    """The real click: Projektmodus topic -> lesson, through the AI service's validator,
    with the provider answering an empty example on the first try AND on the retry."""

    def setUp(self):
        from learnova.ai_services import service

        self.service = service
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=False, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False, AI_MODE="live",
            ALLOW_LIVE_AI=True, RUN_LIVE_AI_TEST=False, AI_ENFORCE_LIMITS=False,
            AI_CACHE_DIR=str(root / "cache"), AI_USAGE_PATH=str(root / "usage.jsonl"))
        application.limiter.enabled = False
        application.SESSIONS.clear()
        service._RESERVATIONS.clear()
        service._PROVIDER_COOLDOWNS.clear()
        service._MODEL_COOLDOWNS.clear()
        # Identical lesson requests are coalesced for a few seconds; each case must reach
        # the stub provider, not reuse the previous case's lesson.
        service._RECENT_RESULTS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lena", "email": "lena@example.com",
                                            "password": "correct-horse-battery", "language": "de", "grade": "8"})
        self.client.post("/projects", data={"subject": "Mathematics", "title": "Brüche", "topics": "Brüche addieren"},
                         content_type="multipart/form-data")
        with application.app.app_context():
            project = application.db.session.scalars(application.db.select(application.LearningProject)).one()
            self.project_id, self.section_id = project.id, project.sections[0].id

    def tearDown(self):
        application.limiter.enabled = True
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temp.cleanup()

    def test_bare_concept_names_a_numeric_id_and_a_word_level_still_start_the_lesson(self):
        # "concepts[0] must be an object" failed seven real lessons on 2026-10-30.
        question = {**QUESTION, "id": 1, "difficulty": "Medium"}
        response = self.start({"problem": "1/2 + 1/2", "steps": ["= 1"], "answer": "1"},
                              concepts=["Brüche", "  ", {"name": "Nenner"}], question=question)
        location = response.headers["Location"]
        self.assertIn("session_id=", location, f"the lesson did not start: {location}")
        state = application.SESSIONS[location.split("session_id=")[1].split("&")[0]]
        self.assertEqual([item["name"] for item in state["lesson"]["concepts"]], ["Brüche", "Nenner"])
        self.assertEqual((state["lesson"]["question"]["id"], state["lesson"]["question"]["difficulty"]), ("1", 2))

    def start(self, worked, **fields):
        class Usage:
            input_tokens, output_tokens, total_tokens = 10, 8, 18

        class Reply:
            model, usage = "fake-model", Usage()

            def __init__(self, text):
                self.output_text = text

        import json
        reply = Reply(json.dumps({**lesson(value=worked), **fields}))
        self.service._RECENT_RESULTS.clear()
        # A fresh topic per case: an unfinished lesson on a topic is reopened, not regenerated.
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, self.project_id)
            section = application.LearningSection(project_id=project.id, position=len(project.sections) + 1,
                                                  title=f"Thema {len(project.sections) + 1}",
                                                  main_topic=f"Brüche {len(project.sections) + 1}", estimated_minutes=15)
            application.db.session.add(section)
            application.db.session.commit()
            section_id = section.id
        with patch.object(self.service, "_provider_response", return_value=reply) as provider:
            response = self.client.post(f"/projects/{self.project_id}/topics/{section_id}/start")
        self.assertGreaterEqual(provider.call_count, 1, "the stub provider was not reached")
        return response

    def test_the_live_answer_and_option_names_still_start_the_lesson(self):
        response = self.start({"problem": "1/2 + 1/2", "steps": ["= 1"], "answer": "1"},
                              concepts=["Mitochondrien"], question=QuestionShapeTests.FIRST_TRY)
        location = response.headers["Location"]
        self.assertIn("session_id=", location, f"the lesson did not start: {location}")
        question = application.SESSIONS[location.split("session_id=")[1].split("&")[0]]["lesson"]["question"]
        self.assertEqual((question["expected_answer"], question["difficulty"], question["concept"]),
                         ("B", 1, "Mitochondrien"))

    def test_an_empty_example_on_both_tries_still_starts_the_lesson(self):
        # Two cases: a free account generates two lessons a day (a missing example is
        # covered by the contract tests above).
        for worked in ({"problem": "", "steps": [], "answer": ""}, {"problem": ""}):
            with self.subTest(worked=worked):
                response = self.start(worked)
                self.assertEqual(response.status_code, 302)
                location = response.headers["Location"]
                self.assertIn("session_id=", location, f"the lesson did not start: {location}")
                session_id = location.split("session_id=")[1].split("&")[0]
                state = application.SESSIONS[session_id]
                self.assertIsNone(state["lesson"]["worked_example"], "the empty example is dropped")
                self.assertEqual(state["lesson"]["question"]["prompt"], QUESTION["prompt"])
                application.SESSIONS.clear()


if __name__ == "__main__":
    unittest.main()

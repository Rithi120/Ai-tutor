"""Review findings of the Projektmodus rebuild (phase 4), each pinned by a regression test.

Every model call is mocked. The test cases reuse the harnesses of the rebuild's own test
files (imported as modules, so none of their tests run twice here):

* documents: a page waiting to be read keeps its topics through an update; a corrected
  page is "not analysed" until the topics include it; handwriting drawn as vector ink is
  never stored as "read" from the printed heading; a diagram-only page is read; a page
  being read is not read twice; a ready page without text has its correction card;
* an old project (no page lists in analysis_json) is not re-analysed on Continue;
* id lists in forms cannot crash a route with a 5000-digit number;
* teaching: no solution steps or rubric reach the browser; option letters and the
  correct order count as giving the answer away; retry fields inside the evaluation are
  dropped; the exam stage asks a written question; a retry does not skip a stage;
  Mistake Intelligence keeps a pending retry's answer back;
* subjects: typed combinations keep their combined strategy; the leading subject of a
  phrase wins; DaF is a foreign language;
* flashcards: an edited term front updates its reverse answer; one set per project;
  sign errors are not forms; match and test get their full count; forms cards are
  asked in writing.
"""

import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_review_fixes_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pymupdf  # noqa: E402  pyright: ignore[reportMissingImports]
from PIL import Image  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.flashcards import modes  # noqa: E402
from learnova.lessons import lesson_context  # noqa: E402
from learnova.lessons.strategies import strategy_for  # noqa: E402
from learnova.lessons.subjects import canonical_subject  # noqa: E402
from learnova.moderation import prompts as moderation_prompts  # noqa: E402
from learnova.projects import terms as project_terms  # noqa: E402
from tests import test_lesson_teaching_engine as teaching  # noqa: E402
from tests import test_project_documents as documents  # noqa: E402
from tests import test_project_extraction as extraction  # noqa: E402
from tests import test_project_flashcard_sync as sync  # noqa: E402

db = application.db
GENERIC_RETRY = "Not quite yet. Read the explanation below and try the same question again."


def png_bytes(size=(400, 500)):
    image = Image.new("RGB", size, "white")
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def rate_limited_vision(fallback):
    """create_response: vision is rate-limited, everything else goes to `fallback`."""

    def respond(*, task_type, **kwargs):
        if task_type == "ocr_document_recognition":
            raise ai_service.AIProviderError("provider_rate_limit", "rate limited")
        return fallback(task_type=task_type, **kwargs)
    return respond


# ---------------------------------------------------------------------- documents and analysis

class StoredTopicTests(extraction.ExtractionTestCase):
    def report(self, project_id):
        with application.app.app_context(), application.app.test_request_context():
            return application.project_material_report(self.project(project_id))

    def page_analysis(self, project_id):
        return {page["page_id"]: page["analysis"] for file in self.report(project_id)["files"]
                for page in file["pages"]}

    def test_a_rotated_page_waiting_to_be_read_keeps_its_topic_through_an_update(self):
        # documents-01: rotate page 2, vision is busy, the student taps "Update topics".
        project_id, page_ids = self.make_project([[0, 1]])
        self.quick_start(project_id)
        before = {section["title"]: section for section in self.sections(project_id)}
        kept = before[extraction.ETHICS[1][0]]
        with application.app.app_context():
            section = db.session.get(application.LearningSection, kept["id"])
            assert section is not None
            section.excluded = True          # left out through the confirm form (not user_edited)
            page = db.session.get(application.ProjectPage, page_ids[1])
            assert page is not None
            page.processed_data, page.processed_mime_type = png_bytes(), "image/png"
            db.session.commit()
        rotated = self.client.post(f"/projects/{project_id}/pages/{page_ids[1]}/rotate")
        self.assertEqual(rotated.status_code, 302)
        with patch.object(application, "create_response", side_effect=rate_limited_vision(self.respond)), \
                patch.object(application, "page_is_blank", return_value=False):
            response = self.client.post(f"/projects/{project_id}/reanalyze")
        self.assertIn(response.status_code, (200, 302))
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_ids[1])
            assert page is not None
            self.assertEqual(page.extraction_status, "waiting")
        after = {section["id"]: section for section in self.sections(project_id)}
        self.assertIn(kept["id"], after, "the topic of a page that is not read yet is not deleted")
        self.assertEqual(after[kept["id"]]["title"], kept["title"])
        self.assertTrue(after[kept["id"]]["excluded"], "the student's exclusion survives")

    def test_a_corrected_page_is_not_analysed_until_the_topics_include_it(self):
        # documents-02: "Save correction" on an analysed page.
        project_id, page_ids = self.make_project([[0, 1]])
        self.quick_start(project_id)
        self.assertEqual(set(self.page_analysis(project_id).values()), {"analysed"})
        response = self.post(f"/projects/{project_id}/pages/{page_ids[1]}/correct",
                             data={"text": extraction.page_text(5)})
        self.assertEqual(response.status_code, 302)
        report = self.report(project_id)
        self.assertFalse(report["complete"], "text the analysis never saw is not 'fully analysed'")
        analysis = self.page_analysis(project_id)
        self.assertEqual((analysis[page_ids[0]], analysis[page_ids[1]]), ("analysed", "not_analysed"))
        with application.app.app_context():
            self.assertEqual(application.project_pages_to_analyse(self.project(project_id)), [page_ids[1]])

    def test_text_edited_on_the_review_page_is_not_analysed_yet(self):
        project_id, page_ids = self.make_project([[0, 1]])
        self.quick_start(project_id)
        response = self.post(f"/projects/{project_id}/review", data={
            "action": "save", f"text_{page_ids[0]}": extraction.page_text(0),
            f"text_{page_ids[1]}": extraction.page_text(6)})
        self.assertIn(response.status_code, (200, 302))
        analysis = self.page_analysis(project_id)
        self.assertEqual((analysis[page_ids[0]], analysis[page_ids[1]]), ("analysed", "not_analysed"),
                         "only the page whose text changed")

    def test_a_project_planned_before_the_page_lists_is_not_reanalysed_on_continue(self):
        # migration-01: analysis_json is the column default "{}" for every older project.
        project_id, page_ids = self.make_project([[0, 1], [2, 3]])
        self.quick_start(project_id)
        before = self.sections(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            project.analysis_json = "{}"
            db.session.commit()
            self.assertEqual(application.project_pages_to_analyse(self.project(project_id)), [])
        calls = self.extraction_calls()
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls() - calls, 0, "Continue does not rebuild the old topics")
        self.assertEqual(self.sections(project_id), before)

    def test_an_old_project_whose_last_page_has_no_topic_is_not_reanalysed(self):
        # migration-01, review: a page uploaded with the others that no topic cites (an
        # exercise sheet, a back page) was created after the cited ones, yet the analysis
        # that made the topics read it. It is no new page.
        project_id, page_ids = self.make_project([[0, 1, 2]])
        self.empty_pages = {page_ids[2]}
        self.quick_start(project_id)
        before = self.sections(project_id)
        self.assertNotIn(page_ids[2], {page for section in before for page in section["pages"]})
        with application.app.app_context():
            project = self.project(project_id)
            project.analysis_json = "{}"
            db.session.commit()
            self.assertEqual(application.project_pages_to_analyse(self.project(project_id)), [])
        calls = self.extraction_calls()
        self.quick_start(project_id)
        self.assertEqual(self.extraction_calls() - calls, 0, "Continue does not rebuild the old topics")
        self.assertEqual(self.sections(project_id), before)

    def test_a_page_added_to_an_old_project_is_still_analysed(self):
        project_id, page_ids = self.make_project([[0, 1]])
        self.quick_start(project_id)
        with application.app.app_context():
            project = self.project(project_id)
            project.analysis_json = "{}"
            file = project.files[0]
            newest = max(page.created_at for page in project.pages)
            page = application.ProjectPage(
                project_id=project_id, file_id=file.id, page_number=3, page_order=3,
                extracted_text=extraction.page_text(4), extraction_status="ready", confidence_status="high",
                recognition_confidence=0.95, review_status="pending",
                recognition_json=json.dumps({"text_source": "text_layer"}),
                created_at=newest + timedelta(minutes=5))
            db.session.add(page)
            db.session.commit()
            added = page.id
            self.assertEqual(application.project_pages_to_analyse(self.project(project_id)), [added])

    def test_every_type_or_retake_link_has_its_correction_card(self):
        # ui_i18n-2: a ready page whose text the student cleared on the review page.
        project_id, page_ids = self.make_project([[0, 1, 2, 3, 4]])
        self.quick_start(project_id)
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_ids[1])
            assert page is not None
            page.extracted_text = ""
            page.review_status = "confirmed"
            db.session.commit()
        html = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        links = set(re.findall(r'href="#pagefix-(\d+)"', html))
        targets = set(re.findall(r'id="pagefix-(\d+)"', html))
        self.assertIn(str(page_ids[1]), links)
        self.assertLessEqual(links, targets, "a link to a card that is not there does nothing")

    def test_a_5000_digit_id_in_a_form_is_ignored_not_a_crash(self):
        # security-01
        project_id, page_ids = self.make_project([[0, 1]])
        self.quick_start(project_id)
        huge = "9" * 5000
        response = self.client.post(f"/projects/{project_id}/topics/add",
                                    data={"title": "Neues Thema", "page_ids": [huge, str(page_ids[0])]})
        self.assertNotEqual(response.status_code, 500)
        added = [section for section in self.sections(project_id) if section["title"] == "Neues Thema"]
        self.assertEqual([section["pages"] for section in added], [[page_ids[0]]])
        response = self.client.post(f"/projects/{project_id}/topics/confirm",
                                    data={"choice": "manual", "topic_ids": [huge]})
        self.assertNotEqual(response.status_code, 500)
        self.assertIsNone(application._form_id(huge))
        self.assertIsNone(application._form_id("١٢"), "non-ASCII digits are no id")
        self.assertEqual(application._form_id(" 12 "), 12)


def vector_ink_pdf():
    """A GoodNotes/Notability export: one printed heading, handwriting as vector strokes."""

    document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
    page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
    page.insert_text((60, 60), "Arbeitsblatt Biologie: Die Fotosynthese", fontsize=14)
    shape = page.new_shape()
    for row in range(12):
        y = 120 + row * 40
        for x in range(60, 520, 18):
            shape.draw_bezier((x, y), (x + 4, y - 10), (x + 10, y + 10), (x + 16, y))
    shape.finish(color=(0, 0, 0.6), width=1.2)
    shape.commit()
    data = document.tobytes()
    document.close()
    return data


DIAGRAM_ONLY = documents.FakeResponse({"blocks": [{
    "type": "diagram", "content": "Zellkern, Mitochondrium, Zellmembran, Ribosomen", "confidence": 0.9,
    "bbox": [0.1, 0.1, 0.9, 0.9], "crossed_out": False, "important_candidate": False,
    "teacher_highlight_candidate": False, "nearby_text": "Abbildung 1: Aufbau einer Tierzelle"}],
    "detected_page_number": "", "warning": ""})


class PageReadingTests(documents.Base):
    def one_page(self, name, data, mime):
        self.upload([(name, data, mime)])
        with application.app.app_context():
            project = self.project()
            return project.id, self.pages(project.id)[0].id

    def test_handwriting_drawn_as_vector_ink_waits_for_a_busy_provider(self):
        # documents-03: the printed heading is not stored as the whole page.
        project_id, page_id = self.one_page("goodnotes.pdf", vector_ink_pdf(), "application/pdf")
        response, mocked = self.recognize(project_id, page_id, side_effect=documents.rate_limited)
        self.assertEqual((response.status_code, mocked.call_count), (202, 1))
        self.assertEqual(response.get_json()["state"], "waiting")
        with application.app.app_context():
            page = documents.fetch(application.ProjectPage, page_id)
            self.assertEqual(page.extraction_status, "waiting")
            self.assertTrue(application.page_reading_due(page), "it is read again automatically")

    def test_the_text_layer_of_a_vector_ink_page_needs_review_never_read(self):
        project_id, page_id = self.one_page("goodnotes.pdf", vector_ink_pdf(), "application/pdf")
        response, _ = self.recognize(project_id, page_id, [documents.EMPTY] * 3)
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"], body["text_source"]),
                         (200, "needs_review", "text_layer"))
        report = self.report(project_id)
        self.assertNotEqual(report["files"][0]["reading_state"], "read")

    def test_a_born_digital_title_page_still_falls_back_to_its_text_layer(self):
        project_id, page_id = self.one_page("titel.pdf", documents.text_pdf(documents.SPARSE), "application/pdf")
        response, _ = self.recognize(project_id, page_id, side_effect=documents.rate_limited)
        self.assertEqual((response.status_code, response.get_json()["state"]), (200, "read"))

    def test_a_page_read_as_one_labelled_diagram_is_read_and_feeds_the_topics(self):
        # documents-04
        project_id, page_id = self.one_page("zelle.png", documents.png("Zelle"), "image/png")
        with patch.object(application, "page_is_blank", return_value=False):
            response, _ = self.recognize(project_id, page_id, [DIAGRAM_ONLY] * 4)
        self.assertEqual((response.status_code, response.get_json()["state"]), (200, "read"))
        with application.app.app_context():
            page = documents.fetch(application.ProjectPage, page_id)
            self.assertTrue(application.page_ready_for_topics(page))
            self.assertIn("Mitochondrium", page.extracted_text)
            self.assertIn("Aufbau einer Tierzelle", page.extracted_text)

    def test_a_page_being_read_is_not_read_again_by_a_second_start_page(self):
        # documents-05
        project_id, page_id = self.one_page("foto.png", documents.png("Seite"), "image/png")
        with application.app.app_context():
            page = documents.fetch(application.ProjectPage, page_id)
            page.processing_stage = "recognizing"
            application.recognition_note(page, recognizing_since=application.utcnow().isoformat())
            db.session.commit()
            page = documents.fetch(application.ProjectPage, page_id)
            self.assertFalse(application.page_reading_due(page))
            self.assertEqual(application.project_pages_needing_recognition(page.project), [])
        response, mocked = self.recognize(project_id, page_id, [documents.reading("Zweimal gelesen")])
        self.assertEqual(mocked.call_count, 0, "the second request does not pay for the same page")
        self.assertEqual(response.status_code, 202)
        with application.app.app_context():
            page = documents.fetch(application.ProjectPage, page_id)
            application.recognition_note(page, recognizing_since=(
                application.utcnow() - application.RECOGNIZING_STALE_AFTER - timedelta(minutes=1)).isoformat())
            db.session.commit()
            self.assertTrue(application.page_reading_due(documents.fetch(application.ProjectPage, page_id)),
                            "a read that died is read again")


# ---------------------------------------------------------------------- teaching

ORDER_Q = {"id": "q1", "concept": "Gerechtigkeit", "difficulty": 1, "type": "ordering",
           "prompt": "Bringe die Schritte der Geschichte in die richtige Reihenfolge.", "hint": "",
           "options": [{"id": "a", "label": "Gyges wird unsichtbar"}, {"id": "b", "label": "Gyges findet den Ring"},
                       {"id": "c", "label": "Gyges tut ungestraft Unrecht"},
                       {"id": "d", "label": "Glaukon stellt seine Frage"}],
           "expected_answer": ["b", "a", "c", "d"]}
RETRY = {"misunderstanding": "Du hast die Pointe verfehlt.", "reteach": "Stell dir vor, niemand sieht dich.",
         "simpler_example": "Ein Kind nimmt Süßigkeiten, wenn niemand hinsieht."}


class TeachingFixTests(teaching.TeachingEngineCase):
    def setUp(self):
        super().setUp()
        _, self.section_id, _ = self.make_project("Ethik", [("Ethik-Blatt.pdf", [teaching.GYGES])],
                                                  teaching.ethics_topic)
        self.lesson_parts = [teaching.part("objective", "Ziel."), teaching.part("thought_experiment", "Gyges ..."),
                             teaching.part("guided_exercise", "Erkläre."), teaching.part("summary", "Kurz.")]
        self.session_id = self.start(self.section_id)
        self.generated = 0

    def respond(self, *, task_type, **kwargs):
        if task_type == "question_generation":
            self.calls.append({"task_type": task_type, **kwargs})
            self.generated += 1
            # The format the gate asked for (the spec in the prompt names it).
            asked = re.search(r'"question_type":\s*"(\w+)"', str(kwargs.get("input") or ""))
            kind = asked.group(1) if asked else "text"
            shape = ({"type": "text", "options": [], "expected_answer": "Niemand ist freiwillig gerecht"}
                     if kind not in ("multiple_choice", "dropdown", "checkboxes") else
                     {"type": kind, "expected_answer": ["a", "b"] if kind == "checkboxes" else "a"})
            return teaching.FakeResponse({"question": {
                **teaching.QUESTION, "id": f"gx{self.generated}", **shape,
                "prompt": f"Neue Frage {self.generated}: Was zeigt Gyges?",
                "solution_steps": ["Gyges handelt ungestraft", "Also: Niemand ist freiwillig gerecht"],
                "rubric": ["nennt: Niemand ist freiwillig gerecht"]}})
        return super().respond(task_type=task_type, **kwargs)

    def gradings(self):
        return [call for call in self.calls if call["task_type"] == "answer_evaluation"]

    def test_solution_steps_and_rubric_never_reach_the_browser(self):
        # teaching-01: wrong, retry, correct (a generated transfer question), wrong again.
        self.answers = [teaching.evaluation(20, retry=dict(RETRY))]
        self.assertTrue(self.answer(self.session_id, "b")["retry"])
        self.answers = [teaching.evaluation(100, feedback="Richtig.")]
        second = self.answer(self.session_id, "a", retry_count=1, correct=True)
        self.assertGreaterEqual(self.generated, 1, "the gate generated the next question")
        for key in ("solution_steps", "rubric", "validation"):
            self.assertNotIn(key, second["next_question"] or {})
        self.answers = [teaching.evaluation(20, retry=dict(RETRY))]
        third = self.answer(self.session_id, "c")
        self.assertTrue(third["retry"])
        for key in ("solution_steps", "rubric", "validation"):
            self.assertNotIn(key, third["retry"]["question"])
        self.assertNotIn("misconception", json.dumps(third["retry"]["question"].get("spec") or {}))
        page = self.client.get(f"/?session_id={self.session_id}").get_data(as_text=True)
        self.assertNotIn("solution_steps", page)
        self.assertNotIn('"rubric"', page)

    def test_naming_the_correct_option_letter_gives_the_answer_away(self):
        # teaching-02
        question = teaching.QUESTION
        for text in ("Nicht b, sondern a ist richtig.", "Antwort a ist richtig, denn Gyges ...",
                     "Richtig ist a.", "Option (a) is correct.", "The right answer is a."):
            with self.subTest(text=text):
                self.assertTrue(lesson_context.reveals_answer(text, question))
        for text in ("Schau dir an, was Gyges tut.", "Das ist richtig gedacht, aber unvollständig.",
                     "b ist leider falsch.", "The correct answer is a statement about justice."):
            with self.subTest(text=text):
                self.assertFalse(lesson_context.reveals_answer(text, question))
        raw = {"type": "multiple_choice", "options": question["options"], "expected_answer": "Option a"}
        self.assertTrue(lesson_context.reveals_answer("Die Lösung ist Option a.", raw),
                        "an expected value that is no option id is checked as written")
        retry = {**RETRY, "reteach": "Antwort a ist richtig, denn niemand sieht Gyges."}
        self.answers = [teaching.evaluation(20, feedback="Nicht b, sondern a ist richtig.", retry=retry)]
        data = self.answer(self.session_id, "b")
        self.assertTrue(data["retry"]["allowed"])
        self.assertEqual(data["evaluation"]["feedback"], GENERIC_RETRY)
        self.assertEqual(data["retry"]["reteach"], "")

    def test_feedback_that_states_the_correct_order_gives_the_answer_away(self):
        # teaching-03
        application.SESSIONS[self.session_id]["current_question"] = json.loads(json.dumps(ORDER_Q))
        feedback = ("Fast. Die richtige Reihenfolge ist: Gyges findet den Ring, Gyges wird unsichtbar, "
                    "Gyges tut ungestraft Unrecht, Glaukon stellt seine Frage.")
        retry = {"misunderstanding": "Reihenfolge vertauscht.",
                 "reteach": "Zuerst: Gyges findet den Ring. Dann: Gyges wird unsichtbar. Danach: Gyges tut "
                            "ungestraft Unrecht.",
                 "simpler_example": "Erst Schuhe an, dann los."}
        self.answers = [teaching.evaluation(20, feedback=feedback, correction="b, a, c, d", retry=retry)]
        data = self.answer(self.session_id, ["a", "b", "c", "d"])
        self.assertTrue(data["retry"]["allowed"])
        self.assertEqual(data["evaluation"]["feedback"], GENERIC_RETRY)
        self.assertEqual(data["retry"]["reteach"], "")
        self.assertFalse(lesson_context.reveals_answer(
            "Du hast „Gyges wird unsichtbar“ vor „Gyges findet den Ring“ gesetzt.", ORDER_Q),
            "two items named in the student's order give nothing away")

    def test_retry_fields_inside_the_evaluation_are_not_sent(self):
        # teaching-06
        payload = teaching.evaluation(20, feedback="Schau dir an, was Gyges tut.", correction="Richtig ist a.")
        payload["evaluation"]["reteach"] = "Die richtige Antwort ist: Niemand ist freiwillig gerecht."
        payload["evaluation"]["misunderstanding"] = "Du hast die Pointe verfehlt."
        payload["evaluation"]["retry"] = {"simpler_example": "Die richtige Antwort ist: Niemand ist freiwillig gerecht."}
        self.answers = [payload]
        data = self.answer(self.session_id, "b")
        self.assertTrue(data["retry"]["allowed"])
        for key in ("reteach", "misunderstanding", "retry", "simpler_example", "correction"):
            self.assertNotIn(key, data["evaluation"])
        self.assertNotIn("Niemand ist freiwillig gerecht", json.dumps(data["evaluation"], ensure_ascii=False))
        self.assertEqual(data["retry"]["misunderstanding"], "Du hast die Pointe verfehlt.")

    def grading_example(self):
        prompt = self.gradings()[-1]["input"]
        line = next(line for line in prompt.splitlines() if line.lstrip().startswith('"next_question"'))
        return json.loads(line.split(":", 1)[1].rstrip().rstrip(","))

    def test_the_exam_stage_asks_a_written_justified_answer(self):
        # teaching-05: an Ethics session of three questions; question 3 is the exam task.
        application.SESSIONS[self.session_id]["test_total"] = 3
        self.answers = [teaching.evaluation(100, feedback="Richtig.")]
        self.answer(self.session_id, "a", correct=True)
        self.answers = [teaching.evaluation(100, feedback="Richtig.")]
        self.answer(self.session_id, "Ein Hirte wird unsichtbar und tut Unrecht.", correct=True)
        prompt = self.gradings()[-1]["input"]
        self.assertIn("an exam-style task", prompt)
        self.assertEqual(self.grading_example()["type"], "text")

    def test_a_retried_question_keeps_its_number_so_no_stage_is_skipped(self):
        # teaching-07
        application.SESSIONS[self.session_id]["test_total"] = 3
        self.answers = [teaching.evaluation(20, retry=dict(RETRY))]
        self.assertTrue(self.answer(self.session_id, "b")["retry"])
        self.answers = [teaching.evaluation(100, feedback="Richtig.")]
        self.answer(self.session_id, "a", retry_count=1, correct=True)
        prompt = self.gradings()[-1]["input"]
        self.assertIn('"question_number": 1', prompt, "the second try is still question 1")
        self.assertIn("the next question is an interpretation question", prompt)
        self.assertEqual(self.grading_example()["id"], "q2")

    def test_mistake_intelligence_keeps_a_pending_retry_back(self):
        # teaching-09
        def respond(*, task_type, **kwargs):
            if task_type == "answer_diagnosis":
                self.calls.append({"task_type": task_type})
                found = teaching.diagnosis(False)
                found["primary_diagnosis"]["statement"] = "Gewählt wurde b; richtig ist: Niemand ist freiwillig gerecht."
                return teaching.FakeResponse(found)
            return self.respond(task_type=task_type, **kwargs)

        self.answers = [teaching.evaluation(20, retry=dict(RETRY))]
        self.next_correct = False
        with patch.object(application, "create_response", side_effect=respond):
            response = self.client.post("/api/answer", json={
                "session_id": self.session_id, "answer": "b", "hints_used": False, "retry_count": 0,
                "response_confidence": 50})
        self.assertTrue(response.get_json()["retry"])
        page = self.client.get("/insights/mistakes").get_data(as_text=True)
        self.assertNotIn("Niemand ist freiwillig gerecht", page)


class SubjectTests(unittest.TestCase):
    def test_the_leading_subject_of_a_phrase_wins(self):
        # teaching-10
        self.assertEqual(canonical_subject("Musik Theorie und Geschichte"), "Music")
        self.assertEqual(canonical_subject("Mathe für Physik"), "Mathematics")
        self.assertEqual(canonical_subject("GK Geschichte"), "History", "a course level is no subject")
        self.assertEqual(canonical_subject("Mathe GK"), "Mathematics")
        self.assertEqual(canonical_subject("Geschichte der Philosophie"), "Philosophy")
        self.assertEqual(canonical_subject("Naturwissenschaft und Technik Klasse 8"), "Science and Technology")

    def test_german_as_a_foreign_language_is_taught_as_a_language(self):
        # teaching-11
        for name in ("DaF", "Deutsch als Fremdsprache", "Deutsch als Zweitsprache", "DaZ"):
            with self.subTest(name=name):
                self.assertEqual(strategy_for(name).key, "languages")
        self.assertEqual(canonical_subject("DaF"), "German as a Foreign Language")
        self.assertEqual(strategy_for("Deutsch").key, "german")


class CombinedSubjectFormTests(documents.Base):
    def test_a_typed_combination_keeps_its_combined_strategy(self):
        # teaching-04 / spec-03
        cases = {"Geschichte/Gemeinschaftskunde": "history+politics", "Ethik und Religion": "ethics+religion",
                 "Physik und Mathematik": "physics+mathematics", "Sachunterricht": "nwt+geography+history",
                 "Ethik": "ethics"}
        for typed, key in cases.items():
            with self.subTest(typed=typed):
                response = self.client.post("/projects", data={
                    "title": typed, "subject": "Other", "subject_other": typed, "topics": "Thema eins"})
                self.assertIn(response.status_code, (200, 302))
                with application.app.app_context():
                    project = db.session.scalar(db.select(application.LearningProject).where(
                        application.LearningProject.title == typed))
                    assert project is not None
                    self.assertEqual(strategy_for(project.subject).key, key)
        with application.app.app_context():
            project = db.session.scalar(db.select(application.LearningProject).where(
                application.LearningProject.title == "Ethik"))
            assert project is not None
            self.assertEqual(project.subject, "Ethics", "a single subject is still stored canonically")


# ---------------------------------------------------------------------- flashcards

def many_terms(ids):
    names = [("Pflicht", "die Notwendigkeit einer Handlung"), ("Maxime", "ein subjektiver Grundsatz"),
             ("Nutzen", "das Glück aller"), ("Konsequenz", "die Folge einer Handlung"),
             ("Hedonismus", "Lust als höchstes Gut"), ("Kalkül", "eine Berechnung"),
             ("Tugend", "eine gute Haltung"), ("Glück", "das gelingende Leben")]
    return [("Pflichtethik", {"summary": "Kant.", "terms": [sync.term(name, definition, ids[0], quote=f"{name} ist "
                                                                   f"{definition}.")
                                                         for name, definition in names[:2]],
                              "items": [], "exam_questions": []}, [0]),
            ("Utilitarismus", {"summary": "Bentham.", "terms": [sync.term(name, definition, ids[1])
                                                               for name, definition in names[2:]],
                               "items": [], "exam_questions": []}, [1])]


class FlashcardFixTests(sync.SyncCase):
    def set_id(self, project_id):
        with application.app.app_context():
            flashcard_set = db.session.scalar(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id))
            assert flashcard_set is not None
            return flashcard_set.id

    def test_editing_the_term_in_a_front_updates_what_reverse_study_expects(self):
        # migration-02 / flashcards_xp-01
        project_id = self.make_project(topics=sync.kant_topics)
        self.sync(project_id)
        set_id = self.set_id(project_id)
        cards = self.client.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]["cards"]
        duty = next(card for card in cards if card["front"] == "What does the term “Pflicht” mean?")
        maxim = next(card for card in cards if card["front"] == "What does the term “Maxime” mean?")
        duty["front"] = "What does the term “Pflichtgefühl” mean?"
        maxim["back"] = "ein subjektiver Grundsatz des Wollens (korrigiert)"
        response = self.client.put(f"/api/flashcards/sets/{set_id}", json={"cards": cards})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        stored = {card.id: card for card in self.cards(project_id)}
        self.assertEqual(stored[duty["id"]].reverse_answer, "Pflichtgefühl")
        self.assertEqual(stored[maxim["id"]].reverse_answer, "Maxime", "a back-only edit keeps the term")
        items = modes.build_items([stored[duty["id"]]], "test", seed=1, count=1, direction="back_to_front",
                                  question_types=["written"])
        self.assertEqual(items[0]["correct_answer"], "Pflichtgefühl")
        self.assertEqual(project_terms.reverse_answer_after_edit(
            "What does the term “Pflicht” mean?", "Pflicht", "Something else entirely?"), "")

    def test_moderation_sees_the_reverse_answer(self):
        items = moderation_prompts.flashcard_items("Set", "", [
            {"front": "What does the term “Pflicht” mean?", "back": "Notwendigkeit", "reverse_answer": "Pflict"}])
        self.assertIn("Pflict", moderation_prompts.moderation_text(items))

    def test_one_key_terms_set_per_project_even_when_two_syncs_race(self):
        # migration-03 / flashcards_xp-06
        project_id = self.make_project(topics=sync.kant_topics)
        with application.app.app_context():
            first = application.FlashcardSet(user_id=self.user_id, project_id=project_id, title="Ethik – key terms",
                                             source_kind="project")
            db.session.add(first)
            db.session.commit()
            first_id = first.id
            db.session.add(application.FlashcardSet(user_id=self.user_id, project_id=project_id, title="twin"))
            with self.assertRaises(application.IntegrityError):
                db.session.commit()
            db.session.rollback()
        real = application.project_flashcard_set
        looked: list[int] = []

        def lookup(project):
            # The first look ran before the other request's set existed.
            looked.append(project.id)
            return None if len(looked) == 1 else real(project)

        with patch.object(application, "project_flashcard_set", side_effect=lookup):
            summary = self.sync(project_id)
        self.assertEqual(summary["set_id"], first_id)
        self.assertGreater(summary["added"], 0, "the cards go into the set that won")
        self.assertTrue(self.cards(project_id))

    def test_the_migration_keeps_one_set_per_project_and_adds_the_unique_index(self):
        project_id = self.make_project(topics=sync.kant_topics)
        with application.app.app_context():
            with db.engine.begin() as connection:
                connection.execute(application.text("DROP INDEX IF EXISTS uq_flashcard_set_project"))
            for title in ("first", "second"):
                db.session.add(application.FlashcardSet(user_id=self.user_id, project_id=project_id, title=title))
                db.session.commit()
            migration = db.session.get(application.SchemaMigration, "038_one_project_flashcard_set")
            if migration is not None:
                db.session.delete(migration)
                db.session.commit()
            application.ensure_database()
            indexes = {index["name"] for index in application.inspect(db.engine).get_indexes("flashcard_set")}
            self.assertIn("uq_flashcard_set_project", indexes)
            sets = db.session.scalars(db.select(application.FlashcardSet).order_by(application.FlashcardSet.id)).all()
            self.assertEqual([(item.title, item.project_id) for item in sets],
                             [("first", project_id), ("second", None)], "the extra set is kept, unlinked")

    def test_a_sign_is_never_a_separator_between_forms(self):
        # flashcards_xp-02
        for typed, expected in (("x = 3", "x = –3"), ("a² b²", "a² – b²"), ("Kälte 18 °C", "Kälte: –18 °C"),
                                ("5 3", "5 – 3"), ("a b", "a – b")):
            with self.subTest(expected=expected):
                self.assertFalse(modes.answer_is_correct(typed, expected, "written"))
                self.assertFalse(modes.answer_is_correct(typed, expected, "fill_blank"))
        self.assertTrue(modes.answer_is_correct("go went gone", "go – went – gone", "written"))
        self.assertTrue(modes.answer_is_correct("child children", "child – children", "written"))
        self.assertTrue(modes.answer_is_correct("1914 1918", "1914 – 1918", "written"))

    def test_forms_with_a_slash_or_a_one_letter_word_are_still_forms(self):
        # flashcards_xp-02, review: "be – was/were – been" is the commonest irregular verb,
        # and a phone keyboard has no en dash: typed without it, it is still right.
        for typed, expected in (("be was were been", "be – was/were – been"),
                                ("be, was/were, been", "be – was/were – been"),
                                ("I me my", "I – me – my"), ("he she it they", "he/she/it – they")):
            with self.subTest(expected=expected):
                self.assertTrue(modes.answer_is_correct(typed, expected, "written"))
        for typed, expected in (("a b", "a – b"), ("x y", "x – y"), ("a/b c/d", "a/b – c/d"),
                                ("a b c d", "a/b – c/d"), ("km/h m/s", "km/h – m/s")):
            with self.subTest(expected=expected):
                self.assertFalse(modes.answer_is_correct(typed, expected, "written"))

    def test_an_edited_short_or_repeated_term_is_found_inside_its_quotes(self):
        # migration-02, review: the term is found where the front quotes it, not at its
        # first occurrence ("a" is in "What", "term" in "the term").
        after_edit = project_terms.reverse_answer_after_edit
        self.assertEqual(after_edit("What does the term “a” mean?", "a", "What does the term “b” mean?"), "b")
        self.assertEqual(after_edit("What does the term “term” mean?", "term",
                                    "What does the term “Begriff” mean?"), "Begriff")
        self.assertEqual(after_edit("Was bedeutet der Begriff „Begriff“?", "Begriff",
                                    "Was bedeutet der Begriff „Wort“?"), "Wort")

    def rows(self, cards):
        now = datetime(2026, 11, 1)
        return [SimpleNamespace(id=index, front=card["front"], back=card["back"],
                                reverse_answer=card["reverse_answer"], origin_key=card["origin_key"],
                                tags_json=json.dumps(card["tags"]), type=card["type"],
                                options_json=json.dumps(card["options"]), weakness_score=0, repetition_count=0,
                                next_review_at=now, starred=False, incorrect_count=0, correct_count=0)
                for index, card in enumerate(cards, 1)]

    def test_match_and_test_get_their_full_count_from_a_project_set(self):
        # flashcards_xp-03
        topics = [{"key": key, "title": title, "source_page_ids": [index], "difficulty": "medium",
                   "terms": topic_map["terms"], "items": []}
                  for index, (title, topic_map, _pages) in enumerate(many_terms([1, 2]), 1)
                  for key in [title.casefold()]]
        rows = self.rows(project_terms.build_cards(topics, strategy_key="ethics"))
        terms = {row.reverse_answer for row in rows if modes.card_variant(row) == "term"}
        self.assertGreaterEqual(len(terms), 6)
        now = datetime(2026, 11, 1)
        chosen = modes.select_cards(rows, "all", now, 6, mode="match")
        pairs = modes.build_items(chosen, "match", 7, 6)
        self.assertEqual(len(pairs), 6)
        self.assertEqual(len({item["prompt"] for item in pairs}), 6)
        chosen = modes.select_cards(rows, "all", now, 10, mode="test")
        self.assertEqual(len(modes.build_items(chosen, "test", 7, 10)), 10)

    def test_the_match_route_starts_with_six_pairs(self):
        application.app.config.update(FEATURE_FLASHCARD_MATCH_GAME=True)
        project_id = self.make_project(topics=many_terms)
        self.sync(project_id)
        response = self.client.post(f"/api/flashcards/sets/{self.set_id(project_id)}/sessions",
                                    json={"mode": "match", "count": 6, "resume": False})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True)[:300])
        self.assertEqual(len(response.get_json()["session"]["items"]), 6)

    def test_forms_cards_are_asked_in_writing_never_among_definitions(self):
        # flashcards_xp-05
        def entry(name, definition):
            return {"term": name, "definition": definition, "simple_definition": "", "example": "", "page_id": 1,
                    "page_ids": [1], "quote": "", "in_notes": True, "definition_from_notes": False,
                    "key": project_terms.term_key(name)}
        topic = {"key": "irregular", "title": "Irregular verbs", "source_page_ids": [1], "difficulty": "medium",
                 "terms": [entry("go – went – gone", "gehen"), entry("see – saw – seen", "sehen"),
                           entry("take – took – taken", "nehmen"), entry("give – gave – given", "geben"),
                           entry("write – wrote – written", "schreiben")], "items": []}
        rows = self.rows(project_terms.build_cards([topic], strategy_key="languages"))
        forms = {row.id for row in rows if modes.card_variant(row) == "term_forms"}
        self.assertTrue(forms)
        definitions = {row.back for row in rows if modes.card_variant(row) == "term"}
        for seed in range(4):
            for mode in ("learn", "test"):
                for item in modes.build_items(rows, mode, seed, len(rows)):
                    if item["card_id"] not in forms:
                        continue
                    with self.subTest(seed=seed, mode=mode, prompt=item["prompt"]):
                        self.assertNotIn(item["question_type"], ("multiple_choice", "true_false"))
                        self.assertFalse(set(item["options"]) & definitions)


if __name__ == "__main__":
    unittest.main()

"""The project workspace (Track DASH): sections, topic confirmation, evidence-based
"learned", coverage-based completion, the material check and the scheduler's honesty.

The user's report behind it: six papers, "only one of three topics selected", topics
"learned" after a session with every answer wrong, and no way to tell what is missing.
Every AI call is refused (`create_response` raises) - nothing here needs a model.
"""

import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from html import unescape
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_dashboard_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402
from learnova.projects import scheduler  # noqa: E402
from learnova.projects.terms import term_key  # noqa: E402

KANT = ("Immanuel Kant begründete eine deontologische Ethik. Nicht die Folgen einer Handlung entscheiden "
        "über ihren moralischen Wert, sondern der gute Wille und die Pflicht. Der kategorische Imperativ "
        "lautet: Handle nur nach derjenigen Maxime, durch die du zugleich wollen kannst, dass sie ein "
        "allgemeines Gesetz werde. ")


def no_ai(*_args, **_kwargs):
    raise AssertionError("the workspace must not call a model")


def term(name, page_id, *, definition="", simple="", example="", in_notes=True, from_notes=True):
    return {"term": name, "definition": definition or f"{name}: genaue Definition", "simple_definition": simple
            or f"{name} einfach erklärt", "example": example, "page_id": page_id, "page_ids": [page_id],
            "quote": name, "in_notes": in_notes, "definition_from_notes": from_notes, "key": term_key(name)}


class DashboardTests(unittest.TestCase):
    def setUp(self):
        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "AI_MODE", "CURRICULUM_CLEARED_SOURCES",
                    "FEATURE_PRIVATE_FLASHCARDS")
        application.app.config.update(TESTING=True, AI_MODE="cached", CURRICULUM_CLEARED_SOURCES="",
                                      FEATURE_PRIVATE_FLASHCARDS=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.register(self.client, "mia")
        self.ai = patch.object(application, "create_response", side_effect=no_ai)
        self.ai.start()
        self.addCleanup(self.ai.stop)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def register(client, name):
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery"})
        client.post("/settings/grade", data={"grade": "9"})

    def user_id(self, name="mia"):
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(application.User).where(
                application.User.username == name))
            assert user is not None
            return user.id

    def seed(self, documents, topics, *, confirmed=False, exam_days=None, analysis=None, subject="Ethics"):
        """documents: [(filename, [page dict])]; a page dict may set status, text, confidence_status,
        analysed (default True). topics: [dict(title, pages=[(doc index, page index)], terms=[names],
        items=[(kind, name)], importance)]. Returns (project id, [[page ids per document]], [section ids])."""

        with application.app.app_context():
            project = application.LearningProject(
                user_id=self.user_id(), title="Ethik Klassenarbeit", subject=subject, status="confirmed",
                topics_confirmed=confirmed,
                exam_date=date.today() + timedelta(days=exam_days) if exam_days else None)
            application.db.session.add(project)
            application.db.session.flush()
            order = 0
            page_ids: list[list[int]] = []
            analysed, failed, waiting = [], [], []
            for filename, pages in documents:
                file = application.ProjectFile(project_id=project.id, original_filename=filename,
                                               mime_type="application/pdf", original_data=b"%PDF", sha256=filename)
                application.db.session.add(file)
                application.db.session.flush()
                ids = []
                for number, spec in enumerate(pages, start=1):
                    order += 1
                    status = spec.get("status", "ready")
                    page = application.ProjectPage(
                        project_id=project.id, file_id=file.id, page_number=number, page_order=order,
                        extracted_text=spec.get("text", KANT * 3) if status == "ready" else "",
                        extraction_status=status, confidence_status=spec.get("confidence_status", "high"),
                        recognition_confidence=spec.get("confidence", 0.95),
                        recognition_json=json.dumps({"text_source": "vision"}), review_status="pending")
                    application.db.session.add(page)
                    application.db.session.flush()
                    ids.append(page.id)
                    if status == "ready":
                        (analysed if spec.get("analysed", True) else waiting).append(page.id)
                    elif status == "failed" and spec.get("analysis_failed"):
                        failed.append(page.id)
                page_ids.append(ids)
            section_ids = []
            for position, spec in enumerate(topics, start=1):
                cited = [page_ids[doc][index] for doc, index in spec.get("pages", [])]
                topic_map = {
                    "summary": f"{spec['title']} – Zusammenfassung.", "simple_summary": f"{spec['title']} kurz.",
                    "kind": "main",
                    "terms": [term(name, cited[0] if cited else None) for name in spec.get("terms", [])],
                    "items": [{"kind": kind, "name": name, "detail": f"{name} aus den Notizen",
                               "page_id": cited[0] if cited else None, "in_notes": True}
                              for kind, name in spec.get("items", [])],
                    "exam_questions": [], "related": [],
                }
                section = application.LearningSection(
                    project_id=project.id, position=position, title=spec["title"], main_topic=spec["title"],
                    topic_key=spec.get("key", spec["title"].casefold().replace(" ", "-")),
                    source_page_ids_json=json.dumps(cited), topic_map_json=json.dumps(topic_map),
                    importance=spec.get("importance", 2), estimated_minutes=spec.get("minutes", 15),
                    standard_explanation=f"{spec['title']} erklärt.", excluded=spec.get("excluded", False),
                    definitions_json=json.dumps(spec.get("definitions", [])))
                application.db.session.add(section)
                application.db.session.flush()
                section_ids.append(section.id)
            project.analysis_json = json.dumps({
                "analysis_version": 4, "analysed_page_ids": analysed, "failed_page_ids": failed,
                "not_analysed_page_ids": waiting, "uncovered_page_ids": [], "no_topic_page_ids": [],
                **(analysis or {})})
            application.db.session.commit()
            return project.id, page_ids, section_ids

    def page(self, project_id, *, client=None):
        response = (client or self.client).get(f"/projects/{project_id}")
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return unescape(response.get_data(as_text=True))

    def context(self, project_id):
        with application.app.test_request_context():
            project = application.db.session.get(application.LearningProject, project_id)
            return application.project_workspace_context(project)

    def finish(self, section_id, scores, *, hints=False):
        """A finished topic session with these answer scores; returns the outcome."""
        with application.app.test_request_context():
            section = application.db.session.get(application.LearningSection, section_id)
            assert section is not None
            lesson = application.Lesson(user_id=section.project.user_id, session_id=f"s{section_id}-{len(scores)}",
                                        title="t", subject="Ethics", section_id=section.id, content_json="{}")
            application.db.session.add(lesson)
            application.db.session.flush()
            for score in scores:
                application.db.session.add(application.Attempt(
                    lesson_id=lesson.id, question="q", student_answer="a", score=score, feedback="", difficulty=1,
                    concept=section.title, hints_used=hints))
            application.db.session.flush()
            outcome = application.record_topic_result(section, lesson)
            application.db.session.commit()
            return outcome

    def section(self, section_id):
        section = application.db.session.get(application.LearningSection, section_id)
        assert section is not None
        return section

    def three_topics(self, **kwargs):
        documents = [("ethik-1.pdf", [{}, {}]), ("ethik-2.pdf", [{}])]
        topics = [
            {"title": "Kants Pflichtethik", "pages": [(0, 0), (0, 1)], "terms": ["Pflicht", "Maxime"],
             "importance": 3},
            {"title": "Utilitarismus", "pages": [(1, 0)], "terms": ["Nutzen"], "importance": 2},
            {"title": "Tugendethik", "pages": [(0, 1)], "terms": [], "importance": 1},
        ]
        return self.seed(documents, topics, **kwargs)

    # ---------------------------------------------------------------- the topic list
    def test_every_topic_shows_status_importance_terms_source_pages_and_next_activity(self):
        project_id, _pages, sections = self.three_topics(confirmed=True, exam_days=20)
        html = self.page(project_id)
        for anchor in ("overview", "topics", "explanations", "terms", "exercises", "tests", "revision",
                       "progress", "materials", "pages"):
            self.assertIn(f'id="{anchor}"', html, anchor)
            if anchor != "pages":
                self.assertIn(f'href="#{anchor}"', html, f"a nav chip for #{anchor}")
        for title in ("Kants Pflichtethik", "Utilitarismus", "Tugendethik"):
            self.assertIn(title, html)
        self.assertIn("Very important", html)
        self.assertIn("Less important", html)
        self.assertIn("2 key term(s)", html)
        self.assertIn("ethik-1.pdf p. 1–2", html, "source pages as <file> p. <n>")
        self.assertIn("ethik-2.pdf p. 1", html)
        self.assertIn("Not started yet", html)
        self.assertIn(f'action="/projects/{project_id}/topics/{sections[0]}/start"', html)
        self.assertIn("Start lesson", html)
        context = self.context(project_id)
        by_title = {item["section"].title: item for item in context["topics"]}
        self.assertEqual(len(by_title), 3, "every topic is listed, none hidden by the plan")
        first = by_title["Kants Pflichtethik"]
        self.assertEqual((first["state"], first["importance"], first["terms_count"], first["next_activity"]),
                         ("not_started", 3, 2, "start_lesson"))
        self.assertEqual(first["source_labels"], ["ethik-1.pdf p. 1–2"])
        self.assertEqual(first["knowledge"], 0)
        # The key terms section groups the terms by topic, with their source and origin.
        self.assertIn("Pflicht einfach erklärt", html)
        self.assertIn("From your notes", html)

    def test_topics_not_yet_taught_are_listed_in_the_overview(self):
        project_id, _pages, sections = self.three_topics(confirmed=True)
        with application.app.app_context():
            self.section(sections[1]).importance = 3
            application.db.session.add(application.Lesson(
                user_id=self.user_id(), session_id="taught", title="t", subject="Ethics", section_id=sections[1],
                content_json="{}"))
            application.db.session.commit()
        context = self.context(project_id)
        self.assertEqual([section.id for section in context["not_taught_important"]], [sections[0]],
                         "only the important topic without a lesson")
        self.assertEqual({section.id for section in context["not_taught"]}, {sections[0], sections[2]})
        html = self.page(project_id)
        overview = html[html.index('id="overview"'):html.index('id="today"')]
        self.assertIn("Important topics not taught yet:", overview)
        self.assertIn(f'href="#topic-{sections[0]}"', overview)
        self.assertNotIn(f'href="#topic-{sections[1]}"', overview, "a taught topic is not 'not taught'")
        states = {item["section"].id: item["state"] for item in context["topics"]}
        self.assertEqual(states[sections[1]], "taught", "opening a lesson is 'taught', never more")

    # ---------------------------------------------------------------- learned needs evidence
    def test_a_session_with_every_answer_wrong_does_not_mark_the_topic_learned(self):
        project_id, _pages, sections = self.three_topics(confirmed=True)
        self.assertEqual(self.finish(sections[0], [0, 10, 0]), "incorrect")
        with application.app.app_context():
            section = self.section(sections[0])
            self.assertIsNone(section.learned_at)
        context = self.context(project_id)
        item = next(entry for entry in context["topics"] if entry["section"].id == sections[0])
        self.assertEqual(item["state"], "practising")
        self.assertEqual(context["learned"], 0)
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            application.refresh_project_plans(project.user_id)
            kinds = {(task["kind"], task.get("section_id")) for _, task in application.project_plan_rows(project)}
            self.assertIn(("learn", sections[0]), kinds, "the plan keeps teaching it")

    def test_a_correct_session_marks_the_topic_learned(self):
        _project_id, _pages, sections = self.three_topics(confirmed=True)
        self.assertEqual(self.finish(sections[0], [90, 100]), "correct")
        self.assertEqual(self.finish(sections[1], [90, 90], hints=True), "hinted")
        with application.app.app_context():
            self.assertIsNotNone(self.section(sections[0]).learned_at)
            self.assertIsNotNone(self.section(sections[1]).learned_at, "correct with hints is still shown")

    def test_a_wrong_first_answer_that_is_retaught_and_then_answered_right_counts(self):
        _project_id, _pages, sections = self.three_topics(confirmed=True)
        # A teach-first lesson: wrong, "Try again" right, next question right.
        self.assertEqual(self.finish(sections[0], [20, 100, 100]), "correct")
        # Right at first, then wrong at the end: not demonstrated.
        self.assertEqual(self.finish(sections[1], [100, 100, 0]), "incorrect")
        with application.app.app_context():
            self.assertIsNotNone(self.section(sections[0]).learned_at)
            self.assertIsNone(self.section(sections[1]).learned_at)

    def test_an_exam_score_below_60_does_not_mark_a_topic_learned_and_exam_answers_are_evidence(self):
        project_id, _pages, sections = self.three_topics(confirmed=True)
        with application.app.app_context():
            exam = application.FinalExam(
                project_id=project_id, question_count=4, duration_minutes=20, difficulty_mode="mixed",
                included_section_ids_json=json.dumps(sections[:2]), question_types_json='["multiple_choice"]',
                expires_at=application.utcnow() + timedelta(hours=1))
            application.db.session.add(exam)
            application.db.session.flush()
            question_ids = []
            for position, section_id in enumerate([sections[0], sections[0], sections[1], sections[1]], start=1):
                question = application.ExamQuestion(
                    exam_id=exam.id, section_id=section_id, position=position, difficulty="medium",
                    question_type="multiple_choice", prompt=f"Frage {position}?", concepts_json='["Ethik"]',
                    options_json=json.dumps([{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]),
                    expected_answer="a", supporting_text="Kant")
                application.db.session.add(question)
                application.db.session.flush()
                question_ids.append(question.id)
            application.db.session.commit()
            exam_id = exam.id
        answers = {f"question_{question_ids[0]}": "a", f"question_{question_ids[1]}": "a",
                   f"question_{question_ids[2]}": "b", f"question_{question_ids[3]}": "a"}
        response = self.client.post(f"/exams/{exam_id}/submit", data=answers)
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        self.assertIn("/results", response.headers["Location"])
        with application.app.app_context():
            self.assertIsNotNone(self.section(sections[0]).learned_at, "100% on the topic: demonstrated")
            self.assertIsNone(self.section(sections[1]).learned_at, "50% on the topic is below 60")
        evidence = self.context(project_id)["topics"]
        by_id = {item["section"].id: item["evidence"] for item in evidence}
        self.assertEqual((by_id[sections[0]].answers, by_id[sections[0]].correct_answers), (2, 2))
        self.assertEqual((by_id[sections[1]].answers, by_id[sections[1]].correct_answers), (2, 1))

    def test_marking_a_calendar_session_complete_keeps_a_learn_task_without_evidence(self):
        project_id, _pages, sections = self.three_topics(confirmed=True)
        self.page(project_id)       # plans the project
        with application.app.app_context():
            plan = application.db.session.scalar(application.db.select(application.StudyPlan).where(
                application.StudyPlan.project_id == project_id))
            assert plan is not None
            row = next(item for item in sorted(plan.sessions, key=lambda item: item.date)
                       if any(task.get("kind") == "learn" for task in json.loads(item.tasks_json)))
            learned_ids = {task["section_id"] for task in json.loads(row.tasks_json) if task.get("kind") == "learn"}
            plan_id, row_id = plan.id, row.id
        response = self.client.post(f"/study-plans/{plan_id}/sessions/{row_id}/complete")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            still_open = {task.get("section_id") for _, task in application.project_plan_rows(project)
                          if task.get("kind") == "learn"}
            self.assertTrue(learned_ids <= still_open, "the topic is planned again, not dropped")
            for section_id in learned_ids:
                self.assertIsNone(self.section(section_id).learned_at)
            events = application.db.session.scalars(application.db.select(application.LearningEvent).where(
                application.LearningEvent.event_type == "study_plan_task_completed")).all()
            self.assertEqual(len(events), 1, "the XP for the session is unchanged")

    def test_a_session_marked_complete_stays_complete_when_its_topic_is_planned_again(self):
        # Reviewer (DASHr): with room left on the day, the re-planned learn task landed on
        # the very session the student had just marked complete, which flipped back to
        # "planned" (and lost its completed state on the calendar).
        project_id, _pages, sections = self.three_topics(confirmed=True)
        with application.app.app_context():
            application.study_availability(self.user_id()).daily_minutes = 240
            application.db.session.commit()
        self.page(project_id)
        with application.app.app_context():
            plan = application.db.session.scalar(application.db.select(application.StudyPlan).where(
                application.StudyPlan.project_id == project_id))
            assert plan is not None
            row = next(item for item in sorted(plan.sessions, key=lambda item: item.date)
                       if any(task.get("kind") == "learn" for task in json.loads(item.tasks_json)))
            plan_id, row_id = plan.id, row.id
        self.client.post(f"/study-plans/{plan_id}/sessions/{row_id}/complete")
        with application.app.app_context():
            row = application.db.session.get(application.StudyPlanSession, row_id)
            assert row is not None
            self.assertEqual(row.status, "completed")
            tasks = json.loads(row.tasks_json)
            self.assertTrue(tasks and all(task.get("completed") for task in tasks), tasks)
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            still_open = {task.get("section_id") for _, task in application.project_plan_rows(project)
                          if task.get("kind") == "learn"}
            self.assertTrue(set(sections) <= still_open, "every topic without evidence is planned again")

    # ---------------------------------------------------------------- completion
    def complete_project(self, extra_documents=()):
        documents = [("ethik-1.pdf", [{}]), *extra_documents]
        project_id, pages, sections = self.seed(documents, [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"], "importance": 3}],
            confirmed=True)
        with application.app.app_context():
            section = self.section(sections[0])
            section.coverage_json = json.dumps({"lessons_started": 1, "terms_taught": [term_key("Pflicht")],
                                                "items_taught": []})
            section.learned_at = application.utcnow()
            section.mastery_score = 90
            application.db.session.commit()
        self.finish(sections[0], [100, 100])
        return project_id, pages, sections

    def test_a_project_is_complete_only_when_every_check_passes(self):
        project_id, _pages, _sections = self.complete_project()
        context = self.context(project_id)
        self.assertTrue(all(check["ok"] for check in context["coverage_checks"]), context["coverage_checks"])
        self.assertTrue(context["project_complete"])
        self.assertEqual(context["coverage_missing"], [])
        self.assertIn("Project complete", self.page(project_id))

    def test_an_unread_document_keeps_the_project_incomplete_and_says_what_is_missing(self):
        project_id, _pages, _sections = self.complete_project(
            extra_documents=[("scan-kant.pdf", [{"status": "unreadable"}])])
        context = self.context(project_id)
        self.assertFalse(context["project_complete"])
        failed = [check["code"] for check in context["coverage_checks"] if not check["ok"]]
        self.assertEqual(failed, ["documents_processed"])
        self.assertEqual(context["coverage_missing"], ["1 document(s) are not fully read and analysed yet."])
        html = self.page(project_id)
        self.assertNotIn("Project complete", html)
        self.assertIn("Missing before your project is complete:", html)
        self.assertIn("1 document(s) are not fully read and analysed yet.", html)

    def test_a_topic_without_correct_answers_keeps_the_project_incomplete(self):
        project_id, _pages, sections = self.seed([("ethik-1.pdf", [{}])], [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"]}], confirmed=True)
        self.finish(sections[0], [0, 0, 0])
        context = self.context(project_id)
        self.assertFalse(context["project_complete"])
        codes = {check["code"] for check in context["coverage_checks"] if not check["ok"]}
        self.assertIn("understanding_demonstrated", codes)
        self.assertIn("1 topic(s) still need correct answers to show you understand them.", context["coverage_missing"])

    # ---------------------------------------------------------------- material check
    def test_the_material_check_shows_each_of_six_documents_with_its_state(self):
        documents = [
            ("ethik-papier-1.pdf", [{}, {}]),
            ("ethik-papier-2.pdf", [{}, {"status": "failed"}]),
            ("tafelbild.jpg", [{"confidence_status": "unclear", "confidence": 0.35}]),
            ("scan-kant.pdf", [{"status": "unreadable"}, {"status": "failed"}]),
            ("foto-arbeitsblatt.jpg", [{"status": "waiting"}]),
            ("nachtrag.pdf", [{"analysed": False}, {"analysed": False}]),
        ]
        project_id, _pages, _sections = self.seed(documents, [
            {"title": "Kants Pflichtethik", "pages": [(0, 0), (0, 1), (1, 0), (2, 0)], "terms": ["Pflicht"]}])
        html = self.page(project_id)
        self.assertLess(html.index('id="materialCheck"'), html.index('id="topics"'),
                        "the material check comes before the topic list")
        context = self.context(project_id)
        rows = context["material_rows"]
        self.assertEqual([row["filename"] for row in rows], [name for name, _ in documents])
        self.assertEqual([row["position"] for row in rows], [1, 2, 3, 4, 5, 6])
        states = {row["filename"]: row["state"] for row in rows}
        self.assertEqual(states["ethik-papier-1.pdf"], "analysed")
        self.assertEqual(states["tafelbild.jpg"], "needs_ocr_review")
        self.assertEqual(states["scan-kant.pdf"], "unreadable")
        self.assertEqual(states["nachtrag.pdf"], "not_analysed")
        self.assertIn("Document 3: Needs OCR review", html)
        self.assertIn("Document 4: Could not be read", html)
        material_check = html[html.index('id="materialCheck"'):html.index('id="topics"')]
        for row in rows:
            self.assertIn(f'data-file-id="{row["file_id"]}"', material_check)
            self.assertIn(row["message"], material_check)
            self.assertIn(row["filename"], material_check)
        self.assertIn("1 of 2 pages read", material_check)
        # The materials section is always there, with a fix action per problem page.
        materials = html[html.index('id="materials"'):]
        self.assertIn('id="pages"', materials, "the old #pages anchor still lands here")
        self.assertIn("Try reading again", materials)
        self.assertIn("Continue reading", materials, "a waiting page can be continued")

    def test_the_materials_section_is_present_without_any_problem(self):
        project_id, _pages, _sections = self.complete_project()
        html = self.page(project_id)
        self.assertIn('id="materials"', html)
        self.assertIn('id="pages"', html)
        self.assertIn("Document 1: Fully analysed", html)

    # ---------------------------------------------------------------- confirmation
    def test_select_none_and_select_all(self):
        project_id, _pages, sections = self.three_topics()
        html = self.page(project_id)
        self.assertEqual(html.count('name="topic_ids"'), 3)
        self.assertEqual(html.count('name="topic_ids" value'), 3)
        self.assertNotIn('value="auto"', html)
        with application.app.app_context():
            self.assertFalse(any(self.section(section_id).excluded for section_id in sections),
                             "by default every topic is included")
        response = self.client.post(f"/projects/{project_id}/topics/select", data={"select": "none"})
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            self.assertTrue(all(self.section(section_id).excluded for section_id in sections))
        refused = self.client.post(f"/projects/{project_id}/topics/confirm", data={"choice": "manual"},
                                   follow_redirects=True)
        self.assertIn("at least one topic", refused.get_data(as_text=True))
        self.client.post(f"/projects/{project_id}/topics/select", data={"select": "all"})
        with application.app.app_context():
            self.assertFalse(any(self.section(section_id).excluded for section_id in sections))
        # Removing one topic is unticking it.
        self.client.post(f"/projects/{project_id}/topics/confirm",
                         data={"choice": "manual", "topic_ids": [str(sections[0]), str(sections[1])]})
        with application.app.app_context():
            self.assertEqual([self.section(section_id).excluded for section_id in sections], [False, False, True])

    def test_overlapping_topics_are_explained_and_merged_with_one_tap(self):
        documents = [("ethik-1.pdf", [{}, {}, {}, {}])]
        project_id, pages, sections = self.seed(documents, [
            {"title": "Kants Pflichtethik", "pages": [(0, 2), (0, 3)], "terms": ["Pflicht", "Maxime", "Neigung"]},
            {"title": "Der kategorische Imperativ", "pages": [(0, 2), (0, 3)], "terms": ["Pflicht", "Maxime"],
             "items": [("theory", "Kategorischer Imperativ")]},
            {"title": "Utilitarismus", "pages": [(0, 0)], "terms": ["Nutzen"]},
        ])
        html = self.page(project_id)
        self.assertIn("Both come from p. 3–4 and share the terms Pflicht, Maxime.", html)
        self.assertIn("Merge into one topic", html)
        context = self.context(project_id)
        self.assertEqual(len(context["overlaps"]), 1, "Utilitarismus overlaps with nothing")
        pair = context["overlaps"][0]
        self.assertEqual({pair["a"].id, pair["b"].id}, {sections[0], sections[1]})
        response = self.client.post(f"/projects/{project_id}/sections/merge",
                                    data={"section_ids": [str(sections[0]), str(sections[1])]})
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            self.assertEqual(len(project.sections), 2)
            merged = self.section(sections[0])
            topic_map = json.loads(merged.topic_map_json)
            self.assertEqual([item["term"] for item in topic_map["terms"]], ["Pflicht", "Maxime", "Neigung"])
            self.assertEqual([item["name"] for item in topic_map["items"]], ["Kategorischer Imperativ"])
            self.assertEqual(json.loads(merged.source_page_ids_json), pages[0][2:4])
        self.assertNotIn("Merge into one topic", self.page(project_id))

    def test_a_missing_topic_is_added_with_the_pages_the_student_picks(self):
        documents = [("ethik-1.pdf", [{}, {}, {}])]
        project_id, pages, _sections = self.seed(documents, [
            {"title": "Kants Pflichtethik", "pages": [(0, 0), (0, 1)], "terms": ["Pflicht"]}])
        html = self.page(project_id)
        # The page no topic covers yet is ticked in the picker; covered pages are not.
        self.assertIn(f'name="page_ids" value="{pages[0][2]}" checked', html)
        self.assertNotIn(f'name="page_ids" value="{pages[0][0]}" checked', html)
        self.client.post(f"/projects/{project_id}/topics/add",
                         data={"title": "Tugendethik", "page_ids": [str(pages[0][2])]})
        self.client.post(f"/projects/{project_id}/topics/add", data={"title": "Diskursethik"})
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            added = {section.title: json.loads(section.source_page_ids_json) for section in project.sections}
            self.assertEqual(added["Tugendethik"], [pages[0][2]])
            self.assertEqual(added["Diskursethik"], [], "no page picked: no page is claimed")
            self.assertTrue(all(not section.excluded for section in project.sections), "added topics are included")
        html = self.page(project_id)
        self.assertIn("No pages: explained as standard school content", html)

    def test_topics_dropped_over_the_cap_are_stated_and_can_be_added_back(self):
        project_id, _pages, _sections = self.three_topics(
            analysis={"topics_dropped": 2, "topics_dropped_titles": ["Diskursethik", "Vertragstheorie"]})
        html = self.page(project_id)
        self.assertIn("2 more were left out", html)
        self.assertIn("+ Diskursethik", html)
        self.assertIn('value="Vertragstheorie"', html)

    def test_a_stale_banner_says_why_the_topics_need_an_update(self):
        project_id, _pages, _sections = self.three_topics(analysis={"not_analysed_page_ids": [1, 2]})
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            project.topics_stale = True
            application.db.session.commit()
        self.assertIn("Learnova could not analyse 2 page(s) yet.", self.page(project_id))

    # ---------------------------------------------------------------- the plan
    def test_a_no_exam_plan_that_fits_nothing_says_so_with_the_minutes_needed(self):
        topics = tuple(scheduler.TopicInput(id=f"t{n}", project_id=1, title=f"T{n}", minutes=25) for n in range(3))
        project = scheduler.ProjectInput(id=1, title="Ethik", exam_date=None, topics=topics)
        plan = scheduler.build_plan([project], scheduler.Availability(daily_minutes=20), today=date(2026, 11, 2))
        self.assertFalse([task for day in plan.days for task in day.tasks], "nothing fits a 20-minute day")
        warning = next(item for item in plan.warnings if item["code"] == "insufficient_time")
        self.assertFalse(warning["exam"])
        self.assertTrue(warning["planned_none"])
        self.assertEqual(sorted(warning["topic_ids"]), ["t0", "t1", "t2"])
        self.assertEqual(warning["daily_minutes"], 20)
        self.assertIn(warning["minutes_needed"], scheduler.DAILY_MINUTE_OPTIONS)
        roomy = scheduler.build_plan([project], scheduler.Availability(daily_minutes=warning["minutes_needed"]),
                                     today=date(2026, 11, 2))
        self.assertEqual(roomy.unscheduled, [])
        smaller = [value for value in scheduler.DAILY_MINUTE_OPTIONS if 20 < value < warning["minutes_needed"]]
        for value in smaller:
            self.assertTrue(scheduler.build_plan([project], scheduler.Availability(daily_minutes=value),
                                                 today=date(2026, 11, 2)).unscheduled, value)

    def test_a_reteach_topic_gets_its_reteach_session_not_a_second_first_lesson(self):
        topic = scheduler.TopicInput(id="t", project_id=1, title="T", minutes=20, reteach=True)
        project = scheduler.ProjectInput(id=1, title="P", exam_date=None, topics=(topic,))
        plan = scheduler.build_plan([project], scheduler.Availability(daily_minutes=60), today=date(2026, 11, 2))
        kinds = [task["kind"] for day in plan.days for task in day.tasks]
        self.assertEqual(kinds.count("reteach"), 1)
        self.assertNotIn("learn", kinds)

    def test_a_reteach_session_that_does_not_fit_is_reported_not_dropped(self):
        # Reviewer (DASHr): a not-learned reteach topic no longer gets a learn task, so a
        # reteach session too long for the day vanished from the plan without a warning.
        long = scheduler.TopicInput(id="t", project_id=1, title="T", minutes=45, reteach=True)
        short = scheduler.TopicInput(id="u", project_id=1, title="U", minutes=20)
        project = scheduler.ProjectInput(id=1, title="P", exam_date=None, topics=(long, short))
        plan = scheduler.build_plan([project], scheduler.Availability(daily_minutes=20), today=date(2026, 11, 2))
        self.assertIn("t", plan.unscheduled)
        warning = next(item for item in plan.warnings if item["code"] == "insufficient_time")
        self.assertIn("t", warning["topic_ids"])
        roomy = scheduler.build_plan([project], scheduler.Availability(daily_minutes=warning["minutes_needed"]),
                                     today=date(2026, 11, 2))
        self.assertIn("reteach", [task["kind"] for day in roomy.days for task in day.tasks])
        self.assertEqual(roomy.unscheduled, [])

    def test_the_workspace_offers_the_minutes_that_fit_everything_with_one_tap(self):
        project_id, _pages, _sections = self.three_topics(confirmed=True)
        with application.app.app_context():
            application.study_availability(self.user_id()).daily_minutes = 20
            application.db.session.commit()
        html = self.page(project_id)
        self.assertIn("With 20 minutes a day no topic fits into your plan yet", html)
        context = self.context(project_id)
        warning = context["time_warnings"][0]
        minutes = warning["minutes_needed"]
        self.assertIn(f"Plan {minutes} minutes per day", html)
        self.assertIn(f'name="daily_minutes" value="{minutes}"', html)
        self.assertTrue(all(item["unplanned"] for item in context["topics"]))
        self.client.post(f"/projects/{project_id}/plan-settings", data={"daily_minutes": str(minutes)})
        with application.app.app_context():
            self.assertEqual(application.study_availability(self.user_id()).daily_minutes, minutes)
        context = self.context(project_id)
        self.assertEqual(context["time_warnings"], [])
        self.assertTrue(all(item["planned_on"] for item in context["topics"]), "every topic is planned now")

    # ---------------------------------------------------------------- ownership
    def test_another_student_can_neither_see_nor_change_the_workspace(self):
        project_id, pages, sections = self.three_topics()
        other = application.app.test_client()
        self.register(other, "eve")
        self.assertEqual(other.get(f"/projects/{project_id}").status_code, 404)
        self.assertEqual(other.get(f"/projects/{project_id}/sections/{sections[0]}").status_code, 404)
        for path, data in (
                (f"/projects/{project_id}/topics/select", {"select": "none"}),
                (f"/projects/{project_id}/topics/add", {"title": "Hacked", "page_ids": [str(pages[0][0])]}),
                (f"/projects/{project_id}/sections/merge", {"section_ids": [str(sections[0]), str(sections[1])]}),
                (f"/projects/{project_id}/topics/{sections[0]}/toggle", {}),
                (f"/projects/{project_id}/topics/{sections[0]}/rename", {"title": "Hacked"}),
                (f"/projects/{project_id}/topics/confirm", {"choice": "manual", "topic_ids": [str(sections[0])]}),
                (f"/projects/{project_id}/practice/mixed", {})):
            self.assertEqual(other.post(path, data=data).status_code, 404, path)
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            self.assertEqual(len(project.sections), 3)
            self.assertFalse(project.topics_confirmed)
            self.assertFalse(any(section.excluded for section in project.sections))
            self.assertNotIn("Hacked", {section.title for section in project.sections})

    # ---------------------------------------------------------------- notes page
    def test_the_notes_page_shows_terms_and_items_without_a_dict_repr(self):
        project_id, _pages, sections = self.seed([("ethik-1.pdf", [{}])], [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"],
             "items": [("theory", "Kategorischer Imperativ"), ("thought_experiment", "Lügenbeispiel"),
                       ("person", "Immanuel Kant")],
             "definitions": [{"term": "Pflicht", "definition": "Notwendigkeit einer Handlung"}]},
            {"title": "Altes Thema", "pages": [(0, 0)],
             "definitions": [{"term": "Maxime", "definition": "subjektiver Grundsatz"}]},
        ])
        with application.app.app_context():
            # An analysis before topic maps existed: only the old columns, objects inside.
            old = self.section(sections[1])
            old.topic_map_json = "{}"
            old.important_facts_json = json.dumps([{"name": "Kant", "detail": "lebte in Königsberg"}])
            application.db.session.commit()
        response = self.client.get(f"/projects/{project_id}/sections/{sections[0]}")
        self.assertEqual(response.status_code, 200)
        html = unescape(response.get_data(as_text=True))
        for text in ("Pflicht", "Pflicht einfach erklärt", "Precise definition", "Pflicht: genaue Definition",
                     "ethik-1.pdf p. 1", "From your notes", "Theories", "Kategorischer Imperativ",
                     "Thought experiments", "Lügenbeispiel", "People", "Immanuel Kant"):
            self.assertIn(text, html)
        self.assertNotIn("{'", html)
        legacy = unescape(self.client.get(f"/projects/{project_id}/sections/{sections[1]}").get_data(as_text=True))
        self.assertNotIn("{'", legacy, "old object entries are shown as text")
        self.assertIn("Kant – lebte in Königsberg", legacy)

    def test_an_old_project_with_odd_stored_data_still_renders(self):
        # Reviewer (DASHr): projects analysed before the rebuild (no topic map, no v4
        # analysis keys, NULL numbers, odd JSON) must render, never 500.
        project_id, _pages, sections = self.three_topics(confirmed=True)
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            project.analysis_json = json.dumps({"plan_warnings": [{"code": "insufficient_time", "topic_ids": None}],
                                                "topics_dropped": "x", "topics_dropped_titles": None,
                                                "overlap_suggestions": "bad"})
            first, second, third = (self.section(section_id) for section_id in sections)
            first.topic_map_json = "{}"
            first.definitions_json = json.dumps([{"term": "Pflicht", "definition": "Notwendigkeit einer Handlung"},
                                                 "Maxime: subjektiver Grundsatz"])
            first.importance = 0
            first.coverage_json = "not json"
            second.topic_map_json = json.dumps({"terms": ["Nutzen", {"term": "Glück", "page_id": "abc"}, None],
                                                "items": [{"kind": "unknown", "name": "Bentham", "page_id": 99999},
                                                          "text"]})
            second.source_page_ids_json = json.dumps(["1", None, True, 99999])
            third.source_page_ids_json = "{}"
            application.db.session.commit()
        html = self.page(project_id)
        for section_id in sections:
            self.assertIn(f'id="topic-{section_id}"', html)
        self.assertIn("Bentham", self.client.get(f"/projects/{project_id}/sections/{sections[1]}")
                      .get_data(as_text=True))
        for section_id in sections:
            self.assertEqual(self.client.get(f"/projects/{project_id}/sections/{section_id}").status_code, 200)

    def test_entry_text_turns_stored_objects_into_text(self):
        self.assertEqual(application.entry_text({"term": "Pflicht", "definition": "Notwendigkeit"}),
                         "Pflicht – Notwendigkeit")
        self.assertEqual(application.entry_text("Maxime"), "Maxime")
        self.assertEqual(application.entry_text(["a", {"name": "b"}]), "a; b")
        self.assertEqual(application.entry_text(None), "")

    # ---------------------------------------------------------------- spec audit (FIX1)
    # What the workspace says must match what was really done.
    def test_waiting_pages_are_said_at_the_top_with_one_continue_reading_button(self):
        # P1-04 / f / P1-16: six photos, the provider busy on the third. Quick-start built
        # topics from two and redirected; nothing said that four pages were still unread.
        documents = [(f"foto-{n}.png", [{} if n <= 2 else {"status": "waiting" if n == 3 else "pending"}])
                     for n in range(1, 7)]
        project_id, _pages, _sections = self.seed(documents, [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"]},
            {"title": "Utilitarismus", "pages": [(1, 0)], "terms": ["Nutzen"]}])
        html = self.page(project_id)
        banner = html.split('id="readingPaused"', 1)[1].split("</section>", 1)[0]
        self.assertIn("4 page(s) in 4 document(s) are not read yet", banner)
        self.assertIn(f"/projects/{project_id}/start?auto=1", banner)
        self.assertIn("Continue reading", banner)
        note = html.split('id="incompleteNote"', 1)[1].split("</p>", 1)[0]
        self.assertIn("topics from the missing pages are not in this list", note)
        self.assertIn("Start learning", html, "the student may still start")
        with application.app.test_request_context():
            self.assertNotIn("open the project again", application.page_waiting_message("hourly_limit"))
            self.assertIn("Continue reading", application.page_waiting_message("provider_busy"))

    def test_without_unread_pages_there_is_no_reading_banner(self):
        project_id, _pages, _sections = self.three_topics()
        self.assertNotIn('id="readingPaused"', self.page(project_id))

    def test_learned_counts_and_revision_need_demonstrated_understanding(self):
        # P5-01 / e: one right session set learned_at, the header said "1 of 3 topics
        # learned" while the topic itself said "Practising"; a wrong session put a never
        # learned topic on the revision schedule.
        project_id, _pages, sections = self.three_topics(confirmed=True)
        self.assertEqual(self.finish(sections[0], [100]), "correct")
        self.assertEqual(self.finish(sections[1], [0, 0]), "incorrect")
        with application.app.app_context():
            self.assertIsNotNone(self.section(sections[0]).learned_at)
            self.assertIsNone(self.section(sections[1]).next_review_on, "never learned: no revision date")
        context = self.context(project_id)
        states = {item["section"].id: item["state"] for item in context["topics"]}
        self.assertEqual(states[sections[0]], "practising")
        self.assertEqual(context["learned"], 0, "learned means understood or mastered")
        self.assertEqual(context["topic_progress"], 0)
        self.assertEqual([section.id for section in context["reviews"]], [sections[0]])
        self.assertIn(sections[1], [section.id for section in context["practise_first"]])
        html = self.page(project_id)
        self.assertIn("0 of 3 topics learned", html)
        revision = html.split('id="revision"', 1)[1].split("</section>", 1)[0]
        self.assertIn("Practise first", revision)
        self.assertIn("Utilitarismus", revision)

    def test_overlaps_stay_explained_after_a_practice_exam_and_unrelated_topics_still_merge(self):
        # P1-12 / j: one practice exam hid every overlap suggestion and refused every merge.
        documents = [("ethik-1.pdf", [{}, {}, {}, {}])]
        project_id, _pages, sections = self.seed(documents, [
            {"title": "Kants Pflichtethik", "pages": [(0, 2), (0, 3)], "terms": ["Pflicht", "Maxime", "Neigung"]},
            {"title": "Der kategorische Imperativ", "pages": [(0, 2), (0, 3)], "terms": ["Pflicht", "Maxime"]},
            {"title": "Utilitarismus", "pages": [(0, 0)], "terms": ["Nutzen", "Glueck"]},
            {"title": "Nutzenkalkuel", "pages": [(0, 0)], "terms": ["Nutzen", "Glueck"]},
        ])
        with application.app.app_context():
            exam = application.FinalExam(
                project_id=project_id, question_count=1, duration_minutes=10, difficulty_mode="mixed",
                included_section_ids_json=json.dumps(sections[:1]), question_types_json='["multiple_choice"]',
                expires_at=application.utcnow() + timedelta(hours=1))
            application.db.session.add(exam)
            application.db.session.flush()
            application.db.session.add(application.ExamQuestion(
                exam_id=exam.id, section_id=sections[0], position=1, difficulty="medium",
                question_type="multiple_choice", prompt="Frage?", concepts_json='["Ethik"]',
                options_json=json.dumps([{"id": "a", "label": "A"}]), expected_answer="a", supporting_text="Kant"))
            application.db.session.commit()
        context = self.context(project_id)
        pairs = {frozenset((pair["a"].id, pair["b"].id)): pair for pair in context["overlaps"]}
        asked = pairs[frozenset(sections[:2])]
        self.assertFalse(asked["mergeable"])
        self.assertIn("stay separate", asked["blocked_reason"])
        self.assertTrue(asked["explanation"])
        self.assertTrue(pairs[frozenset(sections[2:])]["mergeable"])
        html = self.page(project_id)
        self.assertIn("disabled>Merge into one topic", html)
        refused = self.client.post(f"/projects/{project_id}/sections/merge",
                                   data={"section_ids": [str(sections[0]), str(sections[1])]}, follow_redirects=True)
        self.assertIn("cannot be merged", unescape(refused.get_data(as_text=True)))
        self.client.post(f"/projects/{project_id}/sections/merge",
                         data={"section_ids": [str(sections[2]), str(sections[3])]})
        with application.app.app_context():
            project = application.db.session.get(application.LearningProject, project_id)
            assert project is not None
            self.assertEqual(len(project.sections), 3, "topics the exam never asked about merge")

    def test_update_topics_never_says_up_to_date_while_pages_failed_or_wait(self):
        # Concern a / P1-10.
        documents = [("ethik-1.pdf", [{}, {"status": "failed", "analysis_failed": True}, {"status": "pending"}])]
        project_id, _pages, _sections = self.seed(documents, [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"]}])
        with patch.object(application, "plan_project_sections", return_value=[]), \
                patch.object(application, "recognize_single_project_page", return_value=(False, "busy")):
            response = self.client.post(f"/projects/{project_id}/reanalyze", follow_redirects=True)
        html = unescape(response.get_data(as_text=True))
        self.assertNotIn("Your topics are up to date.", html)
        self.assertIn("could not be worked out yet", html)
        self.assertIn("1 page(s) are not read yet", html)
        self.assertIn("1 page(s) could not be read", html)

    # ---------------------------------------------------------------- review fixes (phase 4)
    def understood_topic(self):
        project_id, _pages, sections = self.seed([("ethik-1.pdf", [{}])], [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"],
             "items": [("theory", "Pflichtethik"), ("thought_experiment", "Lügner")]}], confirmed=True)
        self.finish(sections[0], [40, 40])
        self.finish(sections[0], [90, 90, 95])
        with application.app.app_context():
            section = self.section(sections[0])
            section.mastery_score = 70.0
            section.coverage_json = json.dumps({"terms_taught": [term_key("Pflicht")], "lessons_started": 2})
            application.db.session.commit()
        return project_id, sections[0]

    def test_practising_an_understood_topic_for_the_exam_is_no_first_lesson_again(self):
        # spec-01: "Practise for the exam" and "Topic test" on a topic the student understands.
        project_id, section_id = self.understood_topic()
        item = self.context(project_id)["topics"][0]
        self.assertEqual((item["state"], item["next_activity"]), ("understood", "exam_practice"))
        prompts = []

        class Stop(Exception):
            pass

        def capture(prompt, *_args, **_kwargs):
            prompts.append(prompt)
            raise Stop()

        with patch.object(application, "start_saved_practice", side_effect=capture):
            self.client.post(f"/projects/{project_id}/sections/{section_id}/test")
        self.assertEqual(len(prompts), 1)
        self.assertNotIn("Teach this topic for the first time", prompts[0])
        self.assertIn("spaced review", prompts[0])
        planned = [line for line in prompts[0].splitlines() if line[:3].strip().rstrip(".").isdigit()]
        for kind in ("thought_experiment", "theory"):
            self.assertFalse(any(f" {kind} — " in line for line in planned), (kind, planned))

    def test_mixed_practice_needs_a_topic_that_was_taught(self):
        # spec-02: never an unexplained examination question.
        project_id, _pages, _sections = self.seed([("ethik-1.pdf", [{}, {}])], [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"]},
            {"title": "Utilitarismus", "pages": [(0, 1)], "terms": ["Nutzen"]}], confirmed=True)
        self.assertNotIn(f"/projects/{project_id}/practice/mixed", self.page(project_id),
                         "the button waits until a topic was taught")
        with patch.object(application, "start_saved_practice", side_effect=AssertionError("no session")):
            response = self.client.post(f"/projects/{project_id}/practice/mixed", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Learn a topic first", unescape(response.get_data(as_text=True)))

    def test_a_planned_revision_with_nothing_taught_teaches_a_topic_first(self):
        # spec-02, review: the plan's mixed practice or final review (on the last study day
        # it comes before any lesson) must not be a dead end for Continue: it teaches first.
        project_id, _pages, sections = self.seed([("ethik-1.pdf", [{}, {}])], [
            {"title": "Kants Pflichtethik", "pages": [(0, 0)], "terms": ["Pflicht"]},
            {"title": "Utilitarismus", "pages": [(0, 1)], "terms": ["Nutzen"]}], confirmed=True)
        started = []

        class Stop(Exception):
            pass

        def lesson(section, *_args, **kwargs):
            started.append((section.id, kwargs.get("mode")))
            raise Stop()

        for kind in ("final_review", "mixed_practice"):
            started.clear()
            with self.subTest(kind=kind), \
                    patch.object(application, "project_next_task",
                                 return_value=(date.today(), {"kind": kind, "id": f"{kind}:planned"})), \
                    patch.object(application, "start_saved_practice", side_effect=AssertionError("no questions")), \
                    patch.object(application, "start_section_lesson", side_effect=lesson):
                response = self.client.post(f"/projects/{project_id}/next", follow_redirects=True)
                self.assertNotIn("Learn a topic first", unescape(response.get_data(as_text=True)))
                self.assertEqual(started, [(sections[0], "learn")])

    def test_mixed_practice_gets_the_teach_first_try_again(self):
        project_id, _section_id = self.understood_topic()
        self.assertIn(f"/projects/{project_id}/practice/mixed", self.page(project_id))

        def fake(prompt, *_args, **_kwargs):
            application.SESSIONS["mixed"] = {"lesson": {}, "history": [], "user_id": self.user_id()}
            return "mixed"

        with patch.object(application, "start_saved_practice", side_effect=fake), \
                patch.object(application, "save_session_state", lambda *args, **kwargs: None):
            self.client.post(f"/projects/{project_id}/practice/mixed")
        self.assertTrue(application.SESSIONS["mixed"].get("teaching_first"),
                        "a first wrong answer gets the misunderstanding, a reteach and a second try")

    def test_a_coverage_check_names_topics_left_out_by_the_cap(self):
        project_id, _pages, _sections = self.three_topics(
            analysis={"topics_dropped": 1, "topics_dropped_titles": ["Diskursethik"]})
        rows = {row["code"]: row for row in self.context(project_id)["coverage_checks"]}
        self.assertFalse(rows["topics_extracted"]["ok"])
        self.assertIn("1 topic(s) found in your notes were left out", rows["topics_extracted"]["missing"])


if __name__ == "__main__":
    unittest.main()

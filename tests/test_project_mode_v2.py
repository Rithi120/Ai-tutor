"""Project Mode v2 end to end: notes -> topics -> confirm -> learn -> revise, every AI call mocked.

Covers the parts the pure-module tests cannot: batched extraction through the gateway, the
correction flow for an unreadable page, re-analysis that keeps progress and renames,
manual topic choice, one budget shared by two exams, the overload warning, review state
after a finished session, idempotent retries, the adaptive/curriculum context of topic
lessons, and adding pages later.
"""

import io
import json
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_mode_v2_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

FIFTEEN = [
    "Gleichförmige Bewegung", "Geschwindigkeit", "Beschleunigung", "Freier Fall", "Kraft",
    "Newtonsche Gesetze", "Reibung", "Arbeit", "Energie", "Leistung", "Impuls",
    "Kreisbewegung", "Gravitation", "Schwingungen", "Wellen",
]
UNREADABLE = "__unreadable__"


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload)


def png(seed):
    image = Image.new("RGB", (900, 1200), (255, 255, 255 - seed % 50))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def page_text(order):
    topic = FIFTEEN[min(order - 1, 14)]
    return f"{topic}: Merksatz und Beispiele auf Seite {order}. " * 40


def lesson(title="Thema"):
    return {"lesson_title": title, "detected_level": "Klasse 9", "concepts": [{"name": title, "evidence": "notes"}],
            "explanation": "Erklärung.", "worked_example": {"problem": "Beispiel", "steps": ["Schritt"], "answer": "A"},
            "teacher_tips": [], "exceptions": [],
            "question": {"id": "q1", "concept": title, "difficulty": 1, "type": "multiple_choice", "prompt": "Frage?",
                         "hint": "Tipp", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
                         "expected_answer": "a"}}


class ProjectModeV2Tests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, AI_MODE="cached", CURRICULUM_CLEARED_SOURCES="")
        application.SESSIONS.clear()
        application._curriculum_index_cache.update(key=None, index=None)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lea", "email": "lea@example.com",
                                            "password": "correct-horse-battery"})
        self.client.post("/settings/grade", data={"grade": "9"})
        self.calls = []
        self.unreadable_orders = set()

    # ---------------------------------------------------------------- helpers
    def upload(self, pages=20, *, exam_days=None, title="Physik", subject="Physics", client=None):
        data = {"subject": subject, "title": title,
                "materials": [(io.BytesIO(png(n)), f"page-{n}.png", "image/png") for n in range(1, pages + 1)]}
        if exam_days is not None:
            data["exam_date"] = (date.today() + timedelta(days=exam_days)).isoformat()
        response = (client or self.client).post("/projects", data=data, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        with application.app.app_context():
            project = application.db.session.scalars(application.db.select(application.LearningProject).order_by(
                application.LearningProject.id.desc())).first()
            assert project is not None, "the upload created no project"
            return project.id

    def respond(self, *, task_type, **kwargs):
        self.calls.append(task_type)
        if task_type == "ocr_document_recognition":
            with application.app.app_context():
                # The page being read is the first one still pending (an unreadable page is
                # marked unreadable after its attempts, so the next call is the next page).
                pending = application.db.session.scalars(application.db.select(application.ProjectPage).where(
                    application.ProjectPage.extraction_status == "pending").order_by(application.ProjectPage.page_order)).first()
                order = pending.page_order if pending else 1
            if order in self.unreadable_orders:
                return FakeResponse({"blocks": [], "detected_page_number": "", "warning": "blurred"})
            return FakeResponse({"blocks": [{"type": "printed_text", "content": page_text(order), "bbox": [0.05, 0.05, 0.95, 0.9],
                                             "confidence": 0.95, "crossed_out": False, "important_candidate": False,
                                             "teacher_highlight_candidate": False, "nearby_text": ""}],
                                 "detected_page_number": str(order), "warning": ""})
        if task_type == "project_section_generation":
            prompt = str(kwargs.get("input", ""))
            pages = re.findall(r'"page_id": (\d+), "page_order": (\d+)', prompt)
            topics = []
            for page_id, order in pages:
                order = int(order)
                name = FIFTEEN[min(order - 1, 14)]
                title = f"{order}. {name}" if order % 2 else name
                topics.append({"title": title, "source_page_ids": [int(page_id)], "learning_goals": [f"{name} erklären"],
                               "standard_explanation": f"{name}.", "difficulty": "hard" if order % 5 == 0 else "medium",
                               "importance": "high" if order < 4 else "medium", "estimated_minutes": 15,
                               "recall_cards": [{"kind": "recall", "prompt": f"{name}?", "answer": "Merksatz",
                                                 "source_text": "Merksatz und Beispiele"}]})
            return FakeResponse({"sections": topics})
        if task_type in ("lesson_generation", "adaptive_practice"):
            self.lesson_instructions = kwargs.get("instructions", "")
            return FakeResponse(lesson())
        raise AssertionError(f"unexpected AI task {task_type}")

    def post(self, path, **kwargs):
        with patch.object(application, "create_response", side_effect=self.respond):
            return self.client.post(path, **kwargs)

    def read_and_find(self, project_id):
        found = self.post(f"/projects/{project_id}/quick-start")
        self.assertEqual(found.status_code, 302, found.get_data(as_text=True)[:400])
        return found

    def project(self, project_id) -> "application.LearningProject":
        project = application.db.session.get(application.LearningProject, project_id)
        assert project is not None, f"project {project_id} does not exist"
        return project

    # ---------------------------------------------------------------- tests
    def test_twenty_pages_of_notes_become_fifteen_topics_with_page_references(self):
        project_id = self.upload(20)
        self.read_and_find(project_id)
        self.assertEqual(self.calls.count("project_section_generation"), 2, "long notes are read in batches")
        self.assertNotIn("competency_extraction", self.calls, "no Kompetenzraster needed")
        with application.app.app_context():
            project = self.project(project_id)
            topics = sorted(project.sections, key=lambda item: item.position)
            self.assertEqual([topic.title.split(". ")[-1] for topic in topics], FIFTEEN)
            orders = {page.id: page.page_order for page in project.pages}
            waves = topics[-1]
            self.assertEqual([orders[page] for page in json.loads(waves.source_page_ids_json)], [15, 16, 17, 18, 19, 20])
            self.assertEqual({topic.importance for topic in topics[:3]}, {3})
            self.assertFalse(project.topics_confirmed, "the student confirms the topics first")
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("We found 15 topics in your notes", page)
        self.assertIn("No official curriculum documents are imported yet", page)

    def test_an_unreadable_page_is_shown_corrected_and_merged_without_losing_work(self):
        self.unreadable_orders = {3}
        project_id = self.upload(5)
        self.read_and_find(project_id)
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("Some of your pages need a look", page)
        self.assertIn("could not be read", page)
        with application.app.app_context():
            project = self.project(project_id)
            titles = {section.title.split(". ")[-1] for section in project.sections}
            self.assertNotIn("Beschleunigung", titles, "nothing is invented for the unreadable page")
            kraft = next(section for section in project.sections if "Kraft" in section.title)
            kraft_id = kraft.id
            broken = next(item for item in project.pages if item.page_order == 3)
            broken_id = broken.id
        self.client.post(f"/projects/{project_id}/topics/{kraft_id}/rename", data={"title": "Kräfte (mein Name)"})
        started = self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        self.assertIn("/?session_id=", started.headers["Location"])
        corrected = self.client.post(f"/projects/{project_id}/pages/{broken_id}/correct", data={"text": page_text(3)})
        self.assertEqual(corrected.status_code, 302)
        with application.app.app_context():
            self.assertTrue(self.project(project_id).topics_stale)
        before_calls = self.calls.count("project_section_generation")
        self.post(f"/projects/{project_id}/reanalyze")
        self.assertEqual(self.calls.count("project_section_generation"), before_calls + 1)
        with application.app.app_context():
            project = self.project(project_id)
            names = [section.title for section in sorted(project.sections, key=lambda item: item.position)]
            self.assertIn("Kräfte (mein Name)", names, "the student's rename survives a new scan")
            self.assertTrue(any("Beschleunigung" in name for name in names), "the corrected page now has its topic")
            self.assertEqual(len(names), len(set(names)))
            self.assertIn(kraft_id, {section.id for section in project.sections}, "same topic, same id, same progress")
            self.assertFalse(project.topics_stale)
            self.assertFalse(application.project_material_warnings(project))

    def test_manual_topic_choice_plans_only_the_chosen_topics(self):
        project_id = self.upload(4, exam_days=10)
        self.read_and_find(project_id)
        with application.app.app_context():
            ids = [section.id for section in sorted(self.project(project_id).sections, key=lambda item: item.position)]
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "manual", "topic_ids": [str(ids[0]), str(ids[2])]})
        with application.app.app_context():
            project = self.project(project_id)
            self.assertEqual({section.id for section in project.sections if not section.excluded}, {ids[0], ids[2]})
            planned = {task.get("section_id") for _, task in application.project_plan_rows(project) if task.get("section_id")}
            self.assertLessEqual(planned, {ids[0], ids[2]})
        refused = self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "manual"})
        self.assertEqual(refused.status_code, 302)

    def test_two_exams_share_one_daily_budget(self):
        first = self.upload(6, exam_days=5, title="Physik")
        self.read_and_find(first)
        self.post(f"/projects/{first}/topics/confirm", data={"choice": "auto"})
        second = self.upload(6, exam_days=9, title="Chemie", subject="Chemistry")
        self.read_and_find(second)
        self.post(f"/projects/{second}/topics/confirm", data={"choice": "auto"})
        with application.app.app_context():
            availability = application.study_availability(self.project(first).user_id)
            capacity = int(availability.daily_minutes * 0.9)
            per_day = {}
            for plan in application.db.session.scalars(application.db.select(application.StudyPlan).where(
                    application.StudyPlan.status == "active")).all():
                for row in plan.sessions:
                    open_minutes = sum(task["minutes"] for task in json.loads(row.tasks_json) if not task.get("completed"))
                    per_day[row.date] = per_day.get(row.date, 0) + open_minutes
            self.assertTrue(per_day)
            for day, minutes in per_day.items():
                self.assertLessEqual(minutes, capacity, f"{day}: the two exams never add up past the budget")
        overview = self.client.get("/dashboard").data.decode()
        self.assertIn("YOUR LEARNING PLAN", overview)
        self.assertIn("Physik", overview)
        self.assertIn("Chemie", overview)

    def test_too_little_time_is_said_out_loud(self):
        project_id = self.upload(20, exam_days=2)
        self.read_and_find(project_id)
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertIn("Not everything fits before the exam", page)

    def test_finished_sessions_move_topics_on_the_review_ladder(self):
        project_id = self.upload(3, exam_days=20)
        self.read_and_find(project_id)
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        with application.app.test_request_context():
            project = self.project(project_id)
            sections = sorted(project.sections, key=lambda item: item.position)

            def finish(section, scores, *, hints=False, diagnoses=()):
                lesson_row = application.Lesson(user_id=project.user_id, session_id=f"s-{section.id}-{len(scores)}",
                                                title="t", subject=project.subject, section_id=section.id,
                                                content_json="{}")
                application.db.session.add(lesson_row)
                application.db.session.flush()
                for index, score in enumerate(scores):
                    application.db.session.add(application.Attempt(
                        lesson_id=lesson_row.id, question="q", student_answer="a", score=score, feedback="", difficulty=1,
                        concept=section.title, hints_used=hints,
                        primary_diagnosis=diagnoses[index] if index < len(diagnoses) else ""))
                application.db.session.flush()
                return application.record_topic_result(section, lesson_row)

            today = date.today()
            self.assertEqual(finish(sections[0], [90, 100]), "correct")
            self.assertEqual(sections[0].next_review_on, today + timedelta(days=3))
            self.assertEqual(finish(sections[1], [90, 90], hints=True), "hinted")
            self.assertEqual(sections[1].next_review_on, today + timedelta(days=1), "with hints: keep it short")
            self.assertEqual(finish(sections[2], [20, 30], diagnoses=["conceptual_misconception"] * 2), "incorrect")
            self.assertTrue(sections[2].reteach, "a repeated misconception is taught differently next")
            application.refresh_project_plans(project.user_id)
            kinds = [task["kind"] for _, task in application.project_plan_rows(project)]
            self.assertIn("reteach", kinds)

    def test_retrying_never_duplicates_topics_or_tasks(self):
        project_id = self.upload(4, exam_days=8)
        self.read_and_find(project_id)
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        with application.app.app_context():
            first_tasks = [task["id"] for _, task in application.project_plan_rows(self.project(project_id))]
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        self.post(f"/projects/{project_id}/plan-settings", data={"daily_minutes": "45"})
        self.post(f"/projects/{project_id}/reanalyze")
        self.post(f"/projects/{project_id}/reanalyze")
        with application.app.app_context():
            project = self.project(project_id)
            tasks = [task["id"] for _, task in application.project_plan_rows(project)]
            self.assertEqual(len(tasks), len(set(tasks)))
            self.assertEqual(sorted(tasks), sorted(first_tasks))
            self.assertEqual(len(project.sections), 4)
            plans = [plan for plan in project.study_plans if plan.status == "active"]
            self.assertEqual(len(plans), 1)
        self.assertEqual(self.calls.count("project_section_generation"), 1, "unchanged pages are not re-read by the AI")

    def test_topic_lessons_carry_the_explanation_level_and_honest_curriculum_status(self):
        with application.app.app_context():
            application.import_curriculum_document(
                content=("3.2 Klassen 9/10\n3.2.1 Kraft\nDie Schülerinnen und Schüler können Kraft als "
                         "Ursache von Bewegungsänderungen beschreiben und Kraft messen.\n").encode(),
                fmt="text", source_url="Testfixture", rights_note="written for the tests", plan="gymnasium",
                subject="physik", title="Physik Testplan", citation="Testplan Physik", grades=(5, 10))
        self.client.post("/settings/school-type", data={"school_type": "gymnasium"})
        project_id = self.upload(5, exam_days=10)
        self.read_and_find(project_id)
        page = self.client.get(f"/projects/{project_id}").data.decode()
        self.assertNotIn("No official curriculum documents are imported yet", page)
        self.assertIn("Bildungsplan", page)
        with application.app.app_context():
            kraft = next(section for section in self.project(project_id).sections if "Kraft" in section.title)
            self.assertEqual(json.loads(kraft.curriculum_json)["status"], "matched")
            kraft_id = kraft.id
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        self.post(f"/projects/{project_id}/topics/{kraft_id}/start")
        self.assertIn("EXPLANATION LEVEL", self.lesson_instructions, "topic lessons are pitched at the student")
        self.assertIn("BADEN-WÜRTTEMBERG BILDUNGSPLAN", self.lesson_instructions)

    def test_merging_two_topics_keeps_everything_and_survives_a_new_scan(self):
        project_id = self.upload(4)
        self.read_and_find(project_id)
        with application.app.app_context():
            first, second = sorted(self.project(project_id).sections, key=lambda item: item.position)[:2]
            first_id, second_id = first.id, second.id
            cards_before = len(first.recall_cards) + len(second.recall_cards)
            goals_before = {goal for goal in json.loads(first.learning_goals_json) + json.loads(second.learning_goals_json)}
        merged = self.client.post(f"/projects/{project_id}/sections/merge",
                                  data={"section_ids": [str(first_id), str(second_id)]})
        self.assertEqual(merged.status_code, 302)
        with application.app.app_context():
            project = self.project(project_id)
            kept = application.db.session.get(application.LearningSection, first_id)
            assert kept is not None
            self.assertIsNone(application.db.session.get(application.LearningSection, second_id))
            self.assertEqual(len(kept.recall_cards), cards_before, "recall cards move, they are not dropped")
            self.assertEqual(set(json.loads(kept.learning_goals_json)), goals_before)
            self.assertTrue(kept.user_edited)
            count = len(project.sections)
        # The pages changed: a new scan must not split the merged topic up again.
        with application.app.app_context():
            project = self.project(project_id)
            project.analysis_json = json.dumps({**json.loads(project.analysis_json), "batches": {}})
            application.db.session.commit()
        self.post(f"/projects/{project_id}/reanalyze")
        with application.app.app_context():
            self.assertEqual(len(self.project(project_id).sections), count)

    def test_more_pages_can_be_added_later(self):
        project_id = self.upload(3)
        self.read_and_find(project_id)
        self.post(f"/projects/{project_id}/topics/confirm", data={"choice": "auto"})
        response = self.client.post(f"/projects/{project_id}/pages/add", data={
            "materials": [(io.BytesIO(png(99)), "extra.png", "image/png"), (io.BytesIO(png(1)), "page-1.png", "image/png")]},
            content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        self.assertIn(f"/projects/{project_id}/start", response.headers["Location"])
        with application.app.app_context():
            project = self.project(project_id)
            self.assertEqual(len(project.pages), 4, "the re-uploaded identical photo is not a new page")
            self.assertTrue(project.topics_stale)


if __name__ == "__main__":
    unittest.main()

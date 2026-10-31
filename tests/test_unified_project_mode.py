"""One Projektmodus: projects, the study planner and the exam planner are one feature."""

import io
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_unified_project_mode_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

db = application.db
ROOT = Path(__file__).resolve().parents[1]


def page(colour="white"):
    output = io.BytesIO()
    Image.new("RGB", (900, 1200), colour).save(output, format="PNG")
    return output.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")

    def tearDown(self):
        application.limiter.enabled = True
        application.app.config.clear()
        application.app.config.update(self.original)

    def register(self, name, language="en"):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery", "language": language, "grade": "8"})
        return client

    def create(self, client=None, **form):
        data = {"subject": "Mathematics", **form}
        return (client or self.client).post("/projects", data=data, content_type="multipart/form-data")

    def projects(self):
        with application.app.app_context():
            rows = db.session.scalars(db.select(application.LearningProject).order_by(application.LearningProject.id)).all()
            db.session.expunge_all()
            return rows

    def confirmed_project(self, exam_days=None, **fields):
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == "alice"))
            project = application.LearningProject(
                user_id=user.id, title="Algebra", subject="Mathematics", status="planned", topics_confirmed=True,
                exam_date=date.today() + timedelta(days=exam_days) if exam_days else None, **fields)
            db.session.add(project)
            db.session.flush()
            for position, title in enumerate(("Fractions", "Equations"), start=1):
                db.session.add(application.LearningSection(
                    project_id=project.id, position=position, title=title, main_topic=title,
                    topic_key=title.lower(), estimated_minutes=15, difficulty=3 if position == 2 else 2))
            db.session.commit()
            return project.id


class NavigationTests(Base):
    def test_one_entry_point_and_no_duplicates(self):
        html = self.client.get("/dashboard").get_data(as_text=True)
        nav = html.split('<header class="app-nav', 1)[1].split("</header>", 1)[0]
        self.assertEqual(nav.count('href="/projects"'), 2)            # desktop bar + mobile menu
        self.assertEqual(nav.count("Project Mode"), 2)
        for gone in ('href="/study-plans"', "Study Planner", "Exam Mode"):
            self.assertNotIn(gone, nav)
        self.assertNotIn('target="_blank"', nav)

    def test_german_names(self):
        client = self.register("bert", language="de")
        projects = client.get("/projects").get_data(as_text=True)
        for label in ("Projektmodus", "Neues Lernprojekt erstellen", "Meine Lernprojekte"):
            self.assertIn(label, projects)
        project_id = self.confirmed_project()
        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            project.user_id = db.session.scalar(db.select(application.User.id).where(application.User.username == "bert"))
            db.session.commit()
        overview = client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn("Projektübersicht", overview)

    def test_project_pages_mark_project_mode_active(self):
        project_id = self.confirmed_project(exam_days=20)
        for path in ("/projects", f"/projects/{project_id}"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertRegex(html, r'class="app-nav-link is-active" href="/projects"')


class OldEntryPointTests(Base):
    def test_old_planner_urls_lead_to_projektmodus(self):
        project_id = self.confirmed_project(exam_days=20)
        self.assertTrue(self.client.get("/study-plans").headers["Location"].endswith("/projects"))
        self.assertTrue(self.client.get("/study-plans/new").headers["Location"].endswith("/projects"))
        self.assertTrue(self.client.get(f"/study-plans/new?project_id={project_id}").headers["Location"]
                        .endswith(f"/projects/{project_id}#plan"))
        self.client.get(f"/projects/{project_id}")              # plans the project
        with application.app.app_context():
            plan_id = db.session.scalar(db.select(application.StudyPlan.id).where(
                application.StudyPlan.project_id == project_id))
        self.assertTrue(self.client.get(f"/study-plans/{plan_id}").headers["Location"]
                        .endswith(f"/projects/{project_id}#progress"))
        self.assertEqual(self.client.get(f"/study-plans/{plan_id}/calendar").status_code, 200)

    def test_another_students_project_is_never_reached(self):
        project_id = self.confirmed_project(exam_days=20)
        mallory = self.register("mallory")
        self.assertTrue(mallory.get(f"/study-plans/new?project_id={project_id}").headers["Location"].endswith("/projects"))
        self.assertEqual(mallory.get(f"/projects/{project_id}").status_code, 404)

    def test_old_planner_form_still_works(self):
        project_id = self.confirmed_project()
        response = self.client.post("/study-plans/new", data={
            "project_id": str(project_id), "exam_date": (date.today() + timedelta(days=15)).isoformat(),
            "daily_minutes": "40", "target_grade": "2", "difficulty_preference": "hard",
            "preferred_days": [str(day) for day in range(5)]})
        self.assertTrue(response.headers["Location"].endswith(f"/projects/{project_id}#progress"))
        with application.app.app_context():
            plan = db.session.scalar(db.select(application.StudyPlan).where(application.StudyPlan.project_id == project_id))
            self.assertEqual((plan.target_grade, plan.difficulty_preference), ("2", "hard"))
            self.assertEqual(db.session.get(application.LearningProject, project_id).exam_date,
                             date.today() + timedelta(days=15))


class CreationTests(Base):
    def test_with_notes_and_exam_date(self):
        response = self.create(title="Physik", exam_date=(date.today() + timedelta(days=30)).isoformat(),
                               grade="9", materials=[(io.BytesIO(page()), "p.png", "image/png")])
        self.assertIn("/start", response.headers["Location"])
        [project] = self.projects()
        self.assertEqual((project.title, project.grade, project.exam_date),
                         ("Physik", "9", date.today() + timedelta(days=30)))

    def test_without_exam_date_is_a_flexible_project(self):
        self.create(title="Just learning", materials=[(io.BytesIO(page()), "p.png", "image/png")])
        [project] = self.projects()
        self.assertIsNone(project.exam_date)

    def test_any_subject_can_still_be_typed(self):
        self.create(title="Latein", subject="Other", subject_other="Latein", topics="a-Deklination")
        self.create(title="Mathe", subject="Mathematics", subject_other="ignored", topics="Brüche")
        self.create(title="Sonstiges", subject="Other", topics="Etwas")
        self.assertEqual(sorted(project.subject for project in self.projects()), ["Latein", "Mathematics", "Other"])

    def test_superscript_digits_in_ids_are_ignored_not_a_crash(self):
        # "²".isdigit() is True but int("²") raises, so isdigit() guards turned this into a 500.
        project_id = self.confirmed_project()
        response = self.client.get("/study-plans/new?project_id=²")
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f"/projects/{project_id}/topics/confirm",
                                    data={"choice": "manual", "topic_ids": ["²"]})
        self.assertLess(response.status_code, 500)

    def test_topics_only_without_any_upload(self):
        response = self.create(title="Geometrie", topics="Pythagoras\nKreis, Dreiecke\npythagoras")
        [project] = self.projects()
        self.assertTrue(response.headers["Location"].endswith(f"/projects/{project.id}#topics"))
        with application.app.app_context():
            titles = [section.title for section in db.session.get(application.LearningProject, project.id).sections]
        self.assertEqual(titles, ["Pythagoras", "Kreis", "Dreiecke"])

    def test_kompetenzraster_pages_count_as_teacher_marked(self):
        self.create(title="Bio", materials=[(io.BytesIO(page("white")), "notes.png", "image/png")],
                    competency_grid=[(io.BytesIO(page("lightgrey")), "raster.png", "image/png")])
        [project] = self.projects()
        with application.app.app_context():
            pages = db.session.get(application.LearningProject, project.id).pages
            marked = sorted((item.page_order, item.teacher_highlighted) for item in pages)
        self.assertEqual(marked, [(1, False), (2, True)])

    def test_works_without_a_kompetenzraster_and_refuses_nothing_at_all(self):
        response = self.create(title="Empty")
        self.assertEqual(response.status_code, 200)
        self.assertIn("at least one topic", response.get_data(as_text=True))
        self.assertEqual(self.projects(), [])

    def test_a_double_submit_creates_one_project(self):
        upload = page()
        first = self.create(title="Chemie", materials=[(io.BytesIO(upload), "p.png", "image/png")])
        second = self.create(title="Chemie", materials=[(io.BytesIO(upload), "p.png", "image/png")])
        [project] = self.projects()
        self.assertIn(f"/projects/{project.id}", first.headers["Location"])
        self.assertTrue(second.headers["Location"].endswith(f"/projects/{project.id}"))
        self.create(title="Chemie", topics="Atome")       # different content: a new project
        self.assertEqual(len(self.projects()), 2)


class DashboardTests(Base):
    def test_exam_project_shows_an_exam_oriented_overview(self):
        project_id = self.confirmed_project(exam_days=20)
        html = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        for marker in ('id="progress"', "Exam readiness", 'id="knowledge"', 'id="revision"', "days left",
                       "Calendar view", "Customise the practice exam", "Practice exam", "Project overview"):
            self.assertIn(marker, html)

    def test_project_without_exam_shows_a_flexible_plan_on_the_same_dashboard(self):
        project_id = self.confirmed_project()
        html = self.client.get(f"/projects/{project_id}").get_data(as_text=True)
        self.assertIn('id="progress"', html)
        self.assertNotIn("Exam readiness", html)
        self.assertIn("No exam date: your plan keeps a steady pace", html)

    def test_plan_settings_hold_class_target_grade_and_difficulty(self):
        project_id = self.confirmed_project(exam_days=20)
        self.client.post(f"/projects/{project_id}/plan-settings", data={
            "grade": "10", "target_grade": "1,5", "difficulty_preference": "easy",
            "daily_minutes": "45", "weekdays_submitted": "1"})
        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            plan = application._active_project_plan(project)
            self.assertEqual(project.grade, "10")
            self.assertEqual((plan.target_grade, plan.difficulty_preference), ("1,5", "easy"))
            application.refresh_project_plans(project.user_id)       # a re-plan keeps them
            db.session.commit()
            plan = application._active_project_plan(db.session.get(application.LearningProject, project_id))
            self.assertEqual(plan.target_grade, "1,5")


class PreservationTests(Base):
    def test_existing_plans_sessions_and_exam_dates_survive(self):
        project_id = self.confirmed_project(exam_days=25)
        self.client.get(f"/projects/{project_id}")
        with application.app.app_context():
            plan = db.session.scalar(db.select(application.StudyPlan).where(application.StudyPlan.project_id == project_id))
            sessions_before = {row.id: row.date for row in plan.sessions}
            plan_id = plan.id
        self.assertTrue(sessions_before)
        self.client.get(f"/study-plans/{plan_id}", follow_redirects=True)
        self.client.get("/study-plans", follow_redirects=True)
        with application.app.app_context():
            plan = db.session.get(application.StudyPlan, plan_id)
            self.assertLessEqual(set(sessions_before), {row.id for row in plan.sessions})
            self.assertEqual(db.session.get(application.LearningProject, project_id).exam_date,
                             date.today() + timedelta(days=25))
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.LearningProject.id))), 1)

    def test_completing_or_missing_a_day_returns_to_the_project(self):
        project_id = self.confirmed_project(exam_days=25)
        self.client.get(f"/projects/{project_id}")
        with application.app.app_context():
            plan = db.session.scalar(db.select(application.StudyPlan).where(application.StudyPlan.project_id == project_id))
            row = plan.sessions[0]
            plan_id, session_id = plan.id, row.id
        response = self.client.post(f"/study-plans/{plan_id}/sessions/{session_id}/complete",
                                    data={"completed_minutes": "20"})
        self.assertTrue(response.headers["Location"].endswith(f"/projects/{project_id}#today"))


class LessonTests(Base):
    def test_a_project_grade_sets_the_lesson_level(self):
        with application.app.test_request_context():
            application.g.project_grade = "11"
            self.assertEqual(application._learner_grade(), application.grade_label("11"))

    def test_a_typed_topic_without_notes_gets_a_school_lesson(self):
        self.create(title="Geschichte", topics="Französische Revolution")
        [project] = self.projects()
        captured = {}

        def fake_start(prompt, *args, **kwargs):
            captured["prompt"] = prompt
            raise RuntimeError("stop")

        with application.app.test_request_context():
            from flask_login import login_user
            login_user(db.session.get(application.User, project.user_id))
            section = db.session.get(application.LearningProject, project.id).sections[0]
            with patch.object(application, "start_saved_practice", side_effect=fake_start):
                with self.assertRaises(RuntimeError):
                    application.start_section_lesson(section)
        self.assertIn("There are no notes for it", captured["prompt"])
        self.assertNotIn("use only the source", captured["prompt"])


class TemplateTests(unittest.TestCase):
    def test_internal_navigation_never_opens_new_tabs(self):
        for path in (ROOT / "templates").rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r'<a\b[^>]*target="_blank"[^>]*>', text):
                self.assertRegex(match.group(0), r'href="https?://', f"{path.name}: {match.group(0)}")


if __name__ == "__main__":
    unittest.main()

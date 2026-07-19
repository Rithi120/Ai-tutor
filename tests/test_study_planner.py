import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_study_planner_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("AI_MODE", "mock")

import app as application  # noqa: E402
from learnova.study_planner import (  # noqa: E402
    adapt_future_schedule,
    build_plan_schedule,
    calendar_days,
    exam_countdown,
)
from learnova.translations import translate  # noqa: E402


class StudyPlannerRuleTests(unittest.TestCase):
    def test_countdown_calendar_priority_and_mastery_adaptation_are_deterministic(self):
        today = date(2030, 5, 1)
        due = datetime(2030, 4, 20, tzinfo=timezone.utc)
        schedule = build_plan_schedule(
            today=today,
            exam_date=date(2030, 5, 20),
            daily_minutes=30,
            preferred_days=range(7),
            difficulty_preference="medium",
            sections=[{
                "id": 7, "position": 1, "title": "Linear equations",
                "estimated_minutes": 15, "status": "not_started", "excluded": False,
            }],
            masteries=[{
                "id": 3, "subject": "Math", "concept": "Fractions",
                "mastery_score": 32, "recent_mistake_count": 2,
                "next_review_at": due,
            }],
            mistakes=[{"id": 9, "subject": "Math", "concept": "Signs"}],
        )
        self.assertEqual(exam_countdown(date(2030, 5, 20), today), 19)
        self.assertEqual(schedule[0]["tasks"][0]["kind"], "review")
        self.assertEqual(schedule[0]["tasks"][0]["concept"], "Fractions")
        self.assertEqual(schedule[0]["tasks"][0]["difficulty"], "easy")
        self.assertEqual(schedule, build_plan_schedule(
            today=today, exam_date=date(2030, 5, 20), daily_minutes=30,
            preferred_days=range(7), difficulty_preference="medium",
            sections=[{"id": 7, "position": 1, "title": "Linear equations", "estimated_minutes": 15, "status": "not_started", "excluded": False}],
            masteries=[{"id": 3, "subject": "Math", "concept": "Fractions", "mastery_score": 32, "recent_mistake_count": 2, "next_review_at": due}],
            mistakes=[{"id": 9, "subject": "Math", "concept": "Signs"}],
        ))
        rows = [{"date": row["date"], "status": "planned", "tasks": row["tasks"]} for row in schedule]
        updates = adapt_future_schedule(
            rows, today=today - timedelta(days=1), score=40, subject="Math",
            concepts=["Fractions"], daily_minutes=30,
        )
        matching = [
            task for tasks in updates.values() for task in tasks
            if str(task.get("concept", "")).casefold() == "fractions"
        ]
        self.assertTrue(matching)
        self.assertTrue(all(task["difficulty"] == "easy" for task in matching))
        month = calendar_days(year=2030, month=5, sessions=rows)
        self.assertIn(len(month), {35, 42})

    def test_german_planner_translations_exist(self):
        self.assertEqual(translate("Study Planner", "de"), "Lernplaner")
        self.assertEqual(translate("Create study plan", "de"), "Lernplan erstellen")
        self.assertIn("Prüfung", translate("Your exam is in {count} days.", "de", count=3))


class StudyPlannerIntegrationTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "alice", "email": "alice@example.com",
            "password": "correct-horse-battery",
        })

    def make_project(self, title="Math finals", subject="Math"):
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            project = application.LearningProject(
                user_id=user.id, title=title, subject=subject, status="ready",
            )
            application.db.session.add(project)
            application.db.session.flush()
            application.db.session.add_all([
                application.LearningSection(
                    project_id=project.id, position=1, title="Fractions",
                    main_topic="Fractions", estimated_minutes=15,
                ),
                application.LearningSection(
                    project_id=project.id, position=2, title="Equations",
                    main_topic="Equations", estimated_minutes=15,
                ),
                application.ConceptMastery(
                    user_id=user.id, subject=subject, concept="Fractions",
                    mastery_score=35, attempts=3, incorrect_attempts=2,
                    recent_mistake_count=2,
                    next_review_at=datetime.now(timezone.utc) - timedelta(days=1),
                ),
            ])
            application.db.session.commit()
            return project.id

    def create_plan(self, project_id, days=20):
        response = self.client.post("/study-plans/new", data={
            "project_id": str(project_id),
            "exam_date": (date.today() + timedelta(days=days)).isoformat(),
            "target_grade": "Grade 1",
            "daily_minutes": "30",
            "preferred_days": [str(value) for value in range(7)],
            "difficulty_preference": "medium",
        })
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True))
        return int(response.headers["Location"].rstrip("/").split("/")[-1])

    def test_plan_creation_dashboard_calendar_and_restart_persistence(self):
        project_id = self.make_project()
        plan_id = self.create_plan(project_id)
        with application.app.app_context():
            plan = application.db.session.get(application.StudyPlan, plan_id)
            self.assertEqual(plan.target_grade, "Grade 1")
            self.assertEqual(plan.daily_minutes, 30)
            self.assertGreater(len(plan.sessions), 0)
            self.assertEqual(json.loads(plan.preferred_days), list(range(7)))
            session_id = plan.sessions[0].id
        detail = self.client.get(f"/study-plans/{plan_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn(b"Fractions", detail.data)
        self.assertEqual(self.client.get(f"/study-plans/{plan_id}/calendar").status_code, 200)
        self.assertEqual(
            self.client.get(f"/study-plans/{plan_id}/sessions/{session_id}").status_code, 200
        )
        self.assertIn(b"AI STUDY COACH", self.client.get("/dashboard").data)
        dashboard_html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertEqual(dashboard_html.count('<section class="learning-overview reveal-card">'), 1)
        self.assertLess(
            dashboard_html.index('class="dashboard-planner-widget reveal-card"'),
            dashboard_html.index('class="learning-overview reveal-card"'),
        )

        completed = self.client.post(
            f"/study-plans/{plan_id}/sessions/{session_id}/complete",
            data={"completed_minutes": "25"},
        )
        self.assertEqual(completed.status_code, 302)
        with application.app.app_context():
            application.db.session.remove()
        restarted_client = application.app.test_client()
        restarted_client.post("/login", data={
            "identifier": "alice", "password": "correct-horse-battery",
        })
        self.assertEqual(restarted_client.get(f"/study-plans/{plan_id}").status_code, 200)
        with application.app.app_context():
            persisted = application.db.session.get(application.StudyPlanSession, session_id)
            self.assertEqual(persisted.status, "completed")
            self.assertEqual(persisted.completed_minutes, 25)

    def test_skipped_day_redistributes_without_appending_and_projects_are_independent(self):
        first_project = self.make_project()
        second_project = self.make_project("Physics finals", "Physics")
        first_plan = self.create_plan(first_project, 25)
        second_plan = self.create_plan(second_project, 30)
        with application.app.app_context():
            plan = application.db.session.get(application.StudyPlan, first_plan)
            original_ids = [item.id for item in plan.sessions]
            skipped_id = plan.sessions[0].id
            original_last_date = plan.sessions[-1].date
        response = self.client.post(f"/study-plans/{first_plan}/sessions/{skipped_id}/skip")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            plan = application.db.session.get(application.StudyPlan, first_plan)
            self.assertEqual([item.id for item in plan.sessions], original_ids)
            self.assertEqual(plan.sessions[-1].date, original_last_date)
            self.assertEqual(application.db.session.get(application.StudyPlanSession, skipped_id).status, "skipped")
            self.assertEqual(application.db.session.get(application.StudyPlan, second_plan).project_id, second_project)

    def test_overdue_planned_day_is_redistributed_automatically(self):
        project_id = self.make_project()
        plan_id = self.create_plan(project_id, 20)
        with application.app.app_context():
            plan = application.db.session.get(application.StudyPlan, plan_id)
            missed = plan.sessions[0]
            missed.date = date.today() - timedelta(days=1)
            missed_id = missed.id
            original_ids = {item.id for item in plan.sessions}
            application.db.session.commit()
        self.assertEqual(self.client.get(f"/study-plans/{plan_id}").status_code, 200)
        with application.app.app_context():
            plan = application.db.session.get(application.StudyPlan, plan_id)
            self.assertEqual(application.db.session.get(application.StudyPlanSession, missed_id).status, "skipped")
            self.assertEqual({item.id for item in plan.sessions}, original_ids)
            self.assertTrue(any(item.date >= date.today() and item.planned_minutes for item in plan.sessions))

    def test_plan_and_session_permissions_are_user_isolated(self):
        project_id = self.make_project()
        plan_id = self.create_plan(project_id)
        with application.app.app_context():
            session_id = application.db.session.get(application.StudyPlan, plan_id).sessions[0].id
        self.client.post("/logout")
        self.client.post("/register", data={
            "username": "bob", "email": "bob@example.com",
            "password": "correct-horse-battery",
        })
        self.assertEqual(self.client.get(f"/study-plans/{plan_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/study-plans/{plan_id}/calendar").status_code, 404)
        self.assertEqual(
            self.client.get(f"/study-plans/{plan_id}/sessions/{session_id}").status_code, 404
        )
        self.assertEqual(
            self.client.post(f"/study-plans/{plan_id}/sessions/{session_id}/complete").status_code,
            404,
        )

    def test_german_planner_page_uses_account_language(self):
        project_id = self.make_project()
        self.create_plan(project_id)
        with application.app.app_context():
            user = application.db.session.scalar(
                application.db.select(application.User).where(application.User.username == "alice")
            )
            user.preferred_language = "de"
            application.db.session.commit()
        page = self.client.get("/study-plans")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Lernplaner", page.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()

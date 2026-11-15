"""Deleting a project or a Short Lesson from the dashboard, and the longer dashboard lists
(owner request 2026-11-13).

Routes through Flask on a scratch database: POST /projects/<id>/delete removes the project
and every row that belongs only to it (topics, pages, files with their stored bytes, plan,
exams, Kompetenzraster rows, project lessons), detaches its flashcard set, and keeps XP,
league entries, ConceptMastery and the usage ledger; POST /lessons/<id>/delete removes a
saved lesson with its answers (so its mistakes leave the Mistake Notebook), chat, state
and photos. Owner only (404), POST only, CSRF enforced, a project topic's lesson refused.
The chat cards that pointed at a deleted lesson or project offer to build it again.
`PRAGMA foreign_key_check` after every delete proves no dangling id is left; run the file
with LEARNOVA_TEST_SQLITE_FK=1 as well, so SQLite enforces the keys as PostgreSQL does.
"""

import os
import re
import tempfile
import unittest
from datetime import timedelta
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_dashboard_delete_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from sqlalchemy import event, func, select, text  # noqa: E402

from tests.test_study_format import THREE, TWELVE, StudyFormatRouteCase  # noqa: E402 - sets DATABASE_URL
from tests.test_continue_learning import FakeAI, lesson_payload  # noqa: E402
import app as application  # noqa: E402
from tests import dashboard_seed as seed  # noqa: E402
from tests.config_isolation import keep_attribute, keep_config  # noqa: E402

M = application
db = application.db
PASSWORD = "correct-horse-battery"

# Every table the project delete must empty for that project, with how its rows are found.
PROJECT_TABLES = {
    "learning_project": "id = :project",
    "project_file": "project_id = :project",
    "project_page": "project_id = :project",
    "document_block": "page_id = :page",
    "learning_section": "project_id = :project",
    "recall_card": "section_id = :section",
    "competency": "project_id = :project",
    "exam_prep_state": "project_id = :project",
    "study_plan": "project_id = :project",
    "study_plan_session": "study_plan_id = :plan",
    "final_exam": "project_id = :project",
    "exam_question": "exam_id = :exam",
    "exam_answer": "exam_id = :exam",
    # Its topics' lessons and the lessons made from its material outside a topic (the exam's
    # review lesson, mixed practice): Lesson.project_id.
    "lesson": "section_id = :section OR project_id = :project",
}


class DeleteCase(unittest.TestCase):
    def setUp(self):
        keep_config(self, application.app.config, "TESTING", "WTF_CSRF_ENABLED", "FEATURE_PRIVATE_FLASHCARDS",
                    "FEATURE_GAMIFICATION", "AI_CACHE_DIR")
        keep_attribute(self, application.limiter, "enabled")
        application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, FEATURE_PRIVATE_FLASHCARDS=True,
                                      FEATURE_GAMIFICATION=True,
                                      AI_CACHE_DIR=tempfile.mkdtemp(prefix="learnova-delete-cache-"))
        application.limiter.enabled = False
        application.SESSIONS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = self.register("alice")
        self.other = self.register("bob")

    def register(self, name):
        client = application.app.test_client()
        response = client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                                  "password": PASSWORD, "grade": "8"})
        self.assertIn(response.status_code, (302, 303))
        return client

    def user_id(self, name="alice"):
        with application.app.app_context():
            return db.session.scalar(select(M.User.id).where(M.User.username == name))

    def seed_project(self, name="alice"):
        with application.app.app_context():
            ids = seed.full_project(application, self.user_id(name))
            db.session.commit()
            return ids

    def seed_lesson(self, name="alice", **options):
        with application.app.app_context():
            ids = seed.short_lesson(application, self.user_id(name), **options)
            db.session.commit()
            return ids

    def count(self, table, where="1 = 1", **values):
        with application.app.app_context():
            return db.session.execute(text(f"SELECT COUNT(*) FROM {table} WHERE {where}"), values).scalar()

    def assert_no_dangling_keys(self):
        with application.app.app_context():
            problems = db.session.execute(text("PRAGMA foreign_key_check")).all()
        self.assertEqual(problems, [], "a row still points at a deleted one")

    def kept_state(self, user_id):
        """XP, league, mastery and ledger rows of the account: none of it may change."""

        with application.app.app_context():
            profile = db.session.get(M.UserGamificationProfile, user_id)
            return {
                "xp_rows": db.session.scalar(select(func.count(M.XPTransaction.id)).where(
                    M.XPTransaction.user_id == user_id)),
                "xp_total": profile.total_xp if profile else 0,
                "league": db.session.scalar(select(func.count(M.LeagueEntry.id)).where(
                    M.LeagueEntry.user_id == user_id)),
                "mastery": [(row.concept, row.mastery_score, row.attempts) for row in db.session.scalars(
                    select(M.ConceptMastery).where(M.ConceptMastery.user_id == user_id).order_by(
                        M.ConceptMastery.id)).all()],
                "history": db.session.scalar(select(func.count(M.MasteryHistory.id)).where(
                    M.MasteryHistory.user_id == user_id)),
                "events": db.session.scalar(select(func.count(M.LearningEvent.id)).where(
                    M.LearningEvent.user_id == user_id)),
                "ledger": [(row.kind, row.status) for row in db.session.scalars(select(M.UsageQuotaEvent).where(
                    M.UsageQuotaEvent.user_id == user_id).order_by(M.UsageQuotaEvent.id)).all()],
            }

    def earn(self, user_id, *, project_id=None, session_id=""):
        """XP, a league entry, a learning event and the allowance rows of what was made."""

        with application.app.app_context():
            M.award_xp(user_id, "lesson_completed", 40, f"test:xp:{user_id}:{project_id}:{session_id}",
                       session_id=session_id)
            week = M.utcnow().date() - timedelta(days=M.utcnow().weekday())
            if db.session.get(M.LeagueWeek, week) is None:
                db.session.add(M.LeagueWeek(week_start=week, starts_at=M.utcnow(),
                                            ends_at=M.utcnow() + timedelta(days=7)))
                db.session.flush()
            if not db.session.scalar(select(M.LeagueEntry.id).where(M.LeagueEntry.user_id == user_id)):
                db.session.add(M.LeagueEntry(week_start=week, user_id=user_id))
            db.session.add(M.LearningEvent(user_id=user_id, event_type="quiz_completed", session_id=session_id,
                                           idempotency_key=f"quiz-complete:{session_id or project_id}"))
            moment = M.utcnow().replace(tzinfo=None)
            for kind, reference in (("project", f"project:{project_id}"), ("lesson", f"lesson:{session_id}")):
                db.session.add(M.UsageQuotaEvent(user_id=user_id, kind=kind, status="committed", reference=reference,
                                                 occurred_at=moment, created_at=moment, settled_at=moment))
            db.session.commit()


class ProjectDeleteTests(DeleteCase):
    def test_the_owner_deletes_the_project_and_everything_only_it_owns(self):
        ids = self.seed_project()
        keep = self.seed_lesson(title="Eigene Lektion")      # a Short Lesson outside the project
        user = self.user_id()
        self.earn(user, project_id=ids["project"], session_id=ids["lesson"]["session_id"])
        values = {"project": ids["project"], "page": ids["page"], "section": ids["section"], "plan": ids["plan"],
                  "exam": ids["exam"]}
        for table, where in PROJECT_TABLES.items():
            self.assertGreater(self.count(table, where, **values), 0, table)
        before = self.kept_state(user)
        application.SESSIONS[ids["lesson"]["session_id"]] = {"user_id": user}

        response = self.client.post(f"/projects/{ids['project']}/delete", data={"next": "/dashboard"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        for table, where in PROJECT_TABLES.items():
            self.assertEqual(self.count(table, where, **values), 0, table)
        lesson_id = ids["lesson"]["id"]
        for table in ("attempt", "chat_message", "study_session"):
            self.assertEqual(self.count(table, "lesson_id = :lesson", lesson=lesson_id), 0, table)
        self.assertEqual(self.count("chat_attachment", "lesson_session_id = :s", s=ids["lesson"]["session_id"]), 0)
        # The stored bytes were columns of the deleted rows: nothing of the upload is left.
        self.assertEqual(self.count("project_file"), 0)
        self.assertEqual(self.count("project_page"), 0)
        self.assertNotIn(ids["lesson"]["session_id"], application.SESSIONS)
        # Kept: XP, league, mastery (the history row lost only its attempt), events, ledger.
        self.assertEqual(self.kept_state(user), before)
        self.assertEqual(self.count("mastery_history", "id = :h AND attempt_id IS NULL", h=ids["history"]), 1)
        # The student's own lesson outside the project is untouched.
        self.assertEqual(self.count("lesson", "id = :id", id=keep["id"]), 1)
        self.assertEqual(self.count("attempt", "lesson_id = :id", id=keep["id"]), 1)
        self.assert_no_dangling_keys()
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Project deleted.", page)
        self.assertNotIn(f"/projects/{ids['project']}\"", page)

    def test_the_key_terms_flashcard_set_is_kept_as_an_own_set(self):
        ids = self.seed_project()
        self.client.post(f"/projects/{ids['project']}/delete")
        with application.app.app_context():
            kept = db.session.get(M.FlashcardSet, ids["set"])
            self.assertIsNotNone(kept)
            self.assertIsNone(kept.project_id)
            self.assertEqual(kept.source_reference, "")
            self.assertEqual([card.front for card in kept.cards], ["Kraft"])
            self.assertEqual(kept.cards[0].source_page_ids_json, "[]", "no id of a deleted page is left")
        self.assertEqual(self.client.get(f"/flashcards/{ids['set']}").status_code, 200)
        self.assert_no_dangling_keys()

    def test_another_account_gets_404_and_nothing_changes(self):
        ids = self.seed_project()
        response = self.other.post(f"/projects/{ids['project']}/delete")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.count("learning_project", "id = :p", p=ids["project"]), 1)
        self.assertEqual(self.client.post("/projects/999999/delete").status_code, 404)

    def test_only_post_deletes(self):
        ids = self.seed_project()
        self.assertEqual(self.client.get(f"/projects/{ids['project']}/delete").status_code, 405)
        self.assertEqual(self.count("learning_project", "id = :p", p=ids["project"]), 1)

    def test_csrf_is_enforced(self):
        ids = self.seed_project()
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True)
        refused = self.client.post(f"/projects/{ids['project']}/delete")
        self.assertEqual(refused.status_code, 302, "a form without its token is sent back")
        self.assertEqual(self.count("learning_project", "id = :p", p=ids["project"]), 1)
        page = self.client.get("/dashboard").get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        accepted = self.client.post(f"/projects/{ids['project']}/delete", data={"csrf_token": token})
        self.assertEqual(accepted.status_code, 302)
        self.assertEqual(self.count("learning_project", "id = :p", p=ids["project"]), 0)

    def test_it_never_goes_back_to_the_deleted_project_or_off_site(self):
        ids = self.seed_project()
        response = self.client.post(f"/projects/{ids['project']}/delete",
                                    data={"next": f"/projects/{ids['project']}#plan"})
        self.assertTrue(response.headers["Location"].endswith("/dashboard"), response.headers["Location"])
        again = self.seed_project()
        response = self.client.post(f"/projects/{again['project']}/delete", data={"next": "https://evil.example/"})
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        third = self.seed_project()
        response = self.client.post(f"/projects/{third['project']}/delete", data={"next": "/projects"})
        self.assertTrue(response.headers["Location"].endswith("/projects"))

    def test_the_workspace_offers_the_delete_too(self):
        ids = self.seed_project()
        page = self.client.get(f"/projects/{ids['project']}").get_data(as_text=True)
        self.assertIn(f'action="/projects/{ids["project"]}/delete"', page)
        self.assertIn('id="delete-project"', page)


class LessonDeleteTests(DeleteCase):
    def test_the_owner_deletes_a_lesson_and_its_mistakes_leave_the_notebook(self):
        lesson = self.seed_lesson(title="Bruchrechnung", mistakes=2, photo=True)
        other = self.seed_lesson(title="Prozentrechnung", mistakes=1)
        user = self.user_id()
        self.earn(user, session_id=lesson["session_id"])
        with application.app.app_context():
            mastery = M.get_or_create_mastery(user, "Mathematics", "Bruchrechnung Konzept 1")
            mastery.attempts, mastery.mastery_score = 2, 35
            db.session.flush()
            db.session.add(M.MasteryHistory(user_id=user, mastery_id=mastery.id, attempt_id=lesson["attempt_ids"][0],
                                            subject="Mathematics", concept="Bruchrechnung Konzept 1",
                                            mastery_before=20, mastery_after=35, delta=15, score=30, difficulty=1,
                                            outcome="partial"))
            db.session.commit()
        before = self.kept_state(user)
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Bruchrechnung: Frage 1", page)
        self.assertIn(f'action="/lessons/{lesson["id"]}/delete"', page)
        application.SESSIONS[lesson["session_id"]] = {"user_id": user}

        response = self.client.post(f"/lessons/{lesson['id']}/delete", data={"next": "/dashboard?subject=Mathematics"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard?subject=Mathematics"))
        for table in ("attempt", "chat_message", "study_session"):
            self.assertEqual(self.count(table, "lesson_id = :id", id=lesson["id"]), 0, table)
        self.assertEqual(self.count("lesson", "id = :id", id=lesson["id"]), 0)
        self.assertEqual(self.count("chat_attachment", "lesson_session_id = :s", s=lesson["session_id"]), 0)
        self.assertNotIn(lesson["session_id"], application.SESSIONS)
        self.assertEqual(self.kept_state(user), before, "XP, mastery, events and the ledger are kept")
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Lesson deleted.", page)
        self.assertNotIn("Bruchrechnung: Frage 1", page, "its mistakes left the Mistake Notebook")
        self.assertIn("Prozentrechnung: Frage 1", page)
        self.assertEqual(self.count("lesson", "id = :id", id=other["id"]), 1)
        # Its old addresses answer as for any missing lesson, never a 500.
        self.assertEqual(self.client.get(f"/lessons/{lesson['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"/?session_id={lesson['session_id']}").status_code, 200)
        self.assertEqual(self.client.post(f"/lessons/{lesson['id']}/resume").status_code, 302)
        self.assert_no_dangling_keys()

    def test_the_resume_card_goes_with_the_lesson(self):
        lesson = self.seed_lesson(title="Unfertige Lektion")
        self.assertIn("Unfertige Lektion", self.client.get("/dashboard").get_data(as_text=True))
        self.client.post(f"/lessons/{lesson['id']}/delete")
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertNotIn("Unfertige Lektion", page)
        self.assertNotIn("CONTINUE WHERE YOU LEFT OFF", page)

    def test_guards(self):
        lesson = self.seed_lesson()
        self.assertEqual(self.other.post(f"/lessons/{lesson['id']}/delete").status_code, 404)
        self.assertEqual(self.client.get(f"/lessons/{lesson['id']}/delete").status_code, 405)
        self.assertEqual(self.count("lesson", "id = :id", id=lesson["id"]), 1)
        application.app.config.update(TESTING=False, WTF_CSRF_ENABLED=True)
        self.assertEqual(self.client.post(f"/lessons/{lesson['id']}/delete").status_code, 302)
        self.assertEqual(self.count("lesson", "id = :id", id=lesson["id"]), 1, "no token, nothing deleted")

    def test_a_project_topic_lesson_is_part_of_its_project(self):
        ids = self.seed_project()
        lesson_id = ids["lesson"]["id"]
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Kraft verstehen", page, "listed under Continue learning")
        self.assertNotIn(f"/lessons/{lesson_id}/delete", page, "but without a delete button")
        refused = self.client.post(f"/lessons/{lesson_id}/delete")
        self.assertEqual(refused.status_code, 302)
        self.assertEqual(self.count("lesson", "id = :id", id=lesson_id), 1)
        self.assertIn("This lesson belongs to a project.", self.client.get("/dashboard").get_data(as_text=True))


class ProjectMaterialLessonTests(DeleteCase):
    """Lessons made from a project's material outside its topics (Lesson.project_id) go with
    the project: the practice exam's review lesson with its graded answers (so the exam's
    mistakes leave the Mistake Notebook) and mixed-practice / targeted-revision lessons with
    the notes in their saved state."""

    def test_the_exam_review_and_practice_lessons_go_with_the_project(self):
        ids = self.seed_project()
        review, practice = ids["review"], ids["practice"]
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Exam review: Physik – Mechanik: Frage 1", page, "the exam's mistake is in the notebook")
        self.assertIn("Gemischte Übung Mechanik", page)
        self.assertEqual(self.count("study_session", "state_json LIKE :n", n="%GEHEIME NOTIZEN%"), 1)

        self.assertEqual(self.client.post(f"/projects/{ids['project']}/delete").status_code, 302)
        for lesson in (review, practice):
            self.assertEqual(self.count("lesson", "id = :id", id=lesson["id"]), 0)
            for table in ("attempt", "chat_message", "study_session"):
                self.assertEqual(self.count(table, "lesson_id = :id", id=lesson["id"]), 0, table)
        self.assertEqual(self.count("study_session", "state_json LIKE :n", n="%GEHEIME NOTIZEN%"), 0,
                         "nothing of the deleted project's notes is left")
        self.assertEqual(self.count("lesson", "content_json LIKE :c", c='%"exam_id"%'), 0,
                         "no lesson points at the deleted exam")
        page = self.client.get("/dashboard").get_data(as_text=True)
        self.assertNotIn("Exam review: Physik", page, "gone from Continue learning and the Mistake Notebook")
        self.assertNotIn("Gemischte Übung Mechanik", page)
        resumed = self.client.post(f"/lessons/{practice['id']}/resume")
        self.assertEqual(resumed.status_code, 302)
        self.assertNotIn("session_id=", resumed.headers["Location"], "the practice lesson cannot be resumed")
        self.assert_no_dangling_keys()

    def test_mixed_practice_is_saved_with_its_project(self):
        from flask_login import login_user

        ids = self.seed_project()
        with application.app.test_request_context():
            login_user(db.session.get(M.User, self.user_id()))
            project = db.session.get(M.LearningProject, ids["project"])
            section = db.session.get(M.LearningSection, ids["section"])
            with FakeAI(lesson_payload("Gemischte Übung")):
                session_id = M.start_mixed_project_practice(project, [section], "mixed_practice")
            db.session.commit()
            lesson = db.session.scalar(select(M.Lesson).where(M.Lesson.session_id == session_id))
            self.assertIsNotNone(lesson)
            self.assertIsNone(lesson.section_id)
            self.assertEqual(lesson.project_id, ids["project"], "saved with the project it was made from")
            lesson_id = lesson.id
        self.client.post(f"/projects/{ids['project']}/delete")
        self.assertEqual(self.count("lesson", "id = :id", id=lesson_id), 0)
        self.assertEqual(self.count("study_session", "lesson_id = :id", id=lesson_id), 0)
        self.assertNotIn(session_id, application.SESSIONS)
        self.assert_no_dangling_keys()

    def test_migration_047_links_the_existing_ones(self):
        ids = self.seed_project()
        user = self.user_id()
        with application.app.app_context():
            other_project = M.LearningProject(user_id=self.user_id("bob"), title="Fremd", subject="Physics")
            db.session.add(other_project)
            db.session.flush()
            legacy_review = seed.short_lesson(application, user, title="Exam review: alt",
                                              content={"exam_id": ids["exam"]}, chat=False)
            legacy_practice = seed.short_lesson(application, user, title="Mischung alt", state={
                "session_kind": "project_practice", "project_id": ids["project"]})
            # Another account's project in a state, a Short Lesson, a review of a missing exam.
            foreign = seed.short_lesson(application, user, title="Fremd", state={
                "session_kind": "project_practice", "project_id": other_project.id})
            plain = seed.short_lesson(application, user, title="Einfach")
            missing = seed.short_lesson(application, user, title="Exam review: weg", content={"exam_id": 987654})
            db.session.commit()
            with db.engine.begin() as connection:
                linked = M.backfill_lesson_projects(connection)
            db.session.expire_all()
            links = {row.id: row.project_id for row in db.session.execute(select(M.Lesson.id, M.Lesson.project_id))}
        self.assertEqual(linked, 2)
        self.assertEqual(links[legacy_review["id"]], ids["project"])
        self.assertEqual(links[legacy_practice["id"]], ids["project"])
        for lesson in (foreign, plain, missing):
            self.assertIsNone(links[lesson["id"]])
        self.client.post(f"/projects/{ids['project']}/delete")
        self.assertEqual(self.count("lesson", "id IN (:a, :b)", a=legacy_review["id"], b=legacy_practice["id"]), 0)
        self.assertEqual(self.count("lesson", "id = :id", id=plain["id"]), 1)
        self.assert_no_dangling_keys()


class SqliteIdReuseTests(DeleteCase):
    """SQLite gives a new row the id of the deleted newest one unless the table has
    AUTOINCREMENT. Kept rows name deleted ones by id (the usage ledger's "project:<id>", the
    learning events' "attempt:<id>", "recall:<id>", "final-exam:<id>",
    "study-plan-session:<id>"), so a reused id counted a new project as already counted and
    paid no XP for new answers. Migration 048 gives those tables AUTOINCREMENT."""

    def scalar(self, sql, **values):
        with application.app.app_context():
            return db.session.execute(text(sql), values).scalar()

    def newest(self):
        return {table: self.scalar(f"SELECT MAX(id) FROM {table}") for table in M.SQLITE_AUTOINCREMENT_TABLES}

    def test_ids_freed_by_a_delete_are_never_handed_out_again(self):
        first = self.seed_project()
        before = self.newest()
        self.assertTrue(all(before.values()), before)
        self.client.post(f"/projects/{first['project']}/delete")
        self.seed_project()
        after = self.newest()
        for table in M.SQLITE_AUTOINCREMENT_TABLES:
            self.assertGreater(after[table], before[table], f"{table} reused a deleted id")
        lesson = self.seed_lesson(mistakes=1)
        used = lesson["attempt_ids"][0]
        self.client.post(f"/lessons/{lesson['id']}/delete")
        again = self.seed_lesson(mistakes=1)
        self.assertGreater(again["attempt_ids"][0], used, "a new answer gets a new id, so a new event key")

    def test_a_new_project_after_a_delete_is_counted(self):
        first = self.seed_project()
        user = self.user_id()
        with application.app.app_context():
            moment = M.utcnow().replace(tzinfo=None)
            db.session.add(M.UsageQuotaEvent(user_id=user, kind="project", status="committed",
                                             reference=f"project:{first['project']}", occurred_at=moment,
                                             created_at=moment, settled_at=moment))
            db.session.commit()
        self.client.post(f"/projects/{first['project']}/delete")
        self.assertEqual(self.count("usage_quota_event", "reference = :r", r=f"project:{first['project']}"), 1,
                         "the ledger row is kept: no refund")
        second = self.seed_project()
        with application.app.app_context():
            counted = M.entitlements.already_counted(user, "project", f"project:{second['project']}")
        self.assertFalse(counted, "the new project is not mistaken for the deleted one")

    def test_an_existing_table_is_rebuilt_with_autoincrement(self):
        from sqlalchemy.schema import CreateIndex, CreateTable

        table = M.Attempt.__table__
        with application.app.app_context():
            legacy = str(CreateTable(table).compile(db.engine))
            self.assertIn("AUTOINCREMENT", legacy)
            with db.engine.begin() as connection:
                connection.exec_driver_sql("DROP TABLE attempt")
                connection.exec_driver_sql(legacy.replace(" AUTOINCREMENT", ""))
                for index in table.indexes:
                    connection.execute(CreateIndex(index))
        self.assertNotIn("AUTOINCREMENT", self.scalar("SELECT sql FROM sqlite_master WHERE name = 'attempt'"))
        lesson = self.seed_lesson(mistakes=3)
        with application.app.app_context():
            # An old SQLite database may already hold an answer whose lesson is gone (SQLite
            # never enforced the key): that must not stop the rebuild.
            raw = db.engine.raw_connection()
            try:
                raw.driver_connection.execute("PRAGMA foreign_keys=OFF")
                raw.driver_connection.execute(
                    "INSERT INTO attempt (lesson_id, question, concept, concepts_json, student_answer, score, "
                    "feedback, difficulty, timestamp, hints_used, retry_count, verdict, mistake_categories, "
                    "root_cause, analysis_json, resolved, diagnosis_json, diagnosis_version, primary_diagnosis, "
                    "next_action, diagnosis_validation, missing_evidence) VALUES (987654, 'q', 'c', '[]', 'a', 0, "
                    "'f', 1, '2026-11-01', 0, 0, '', '[]', '', '{}', 0, '{}', '', '', '', '', 0)")
                raw.driver_connection.commit()
                orphan = raw.driver_connection.execute("SELECT MAX(id) FROM attempt").fetchone()[0]
            finally:
                raw.close()
            self.assertTrue(M.rebuild_sqlite_autoincrement("attempt"))
            self.assertFalse(M.rebuild_sqlite_autoincrement("attempt"), "once is enough")
        self.assertIn("AUTOINCREMENT", self.scalar("SELECT sql FROM sqlite_master WHERE name = 'attempt'"))
        with application.app.app_context():
            kept = db.session.execute(text("SELECT id FROM attempt ORDER BY id")).scalars().all()
            db.session.execute(text("DELETE FROM attempt WHERE id = :id"), {"id": orphan})
            db.session.commit()
        self.assertEqual(kept, sorted(lesson["attempt_ids"] + [orphan]), "every row kept its id")
        for index in table.indexes:
            self.assertEqual(self.count("sqlite_master", "type = 'index' AND name = :n", n=index.name), 1, index.name)
        self.assert_no_dangling_keys()
        newest = max(lesson["attempt_ids"])
        self.client.post(f"/lessons/{lesson['id']}/delete")
        again = self.seed_lesson(mistakes=1)
        self.assertGreater(again["attempt_ids"][0], newest)


class ListContentTests(DeleteCase):
    def test_weakest_concepts_lists_only_concepts_that_are_not_yet_strong(self):
        user = self.user_id()
        with application.app.app_context():
            for name, score in (("Brüche", 10), ("Prozent", 50), ("Terme", 75), ("Gleichungen", 90),
                                ("Zahlen", 100)):
                mastery = M.get_or_create_mastery(user, "Mathematics", name)
                mastery.mastery_score, mastery.attempts = score, 3
            db.session.commit()
        captured = {}
        original = application.render_template

        def render(template, **context):
            if template == "dashboard.html":
                captured.update(context)
            return original(template, **context)

        with patch.object(application, "render_template", side_effect=render):
            self.assertEqual(self.client.get("/dashboard").status_code, 200)
        self.assertEqual([item.concept for item in captured["weakest_concepts"]], ["Brüche", "Prozent"],
                         "strong and mastered concepts are not weak spots")

    def test_the_plan_comparison_is_never_folded(self):
        with application.app.app_context():
            seed.premium_codes(application, self.user_id(), 8)
            db.session.commit()
        page = self.client.get("/settings").get_data(as_text=True)
        start = page.index('<div class="sub-plans"')
        plans = page[start:page.index('<div class="sub-history"', start)]
        self.assertEqual(plans.count('<article class="sub-plan'), 2)
        self.assertNotIn("data-show-more", plans, "a fixed plan description, not a growing list")
        self.assertIn("Shows ads", plans)
        self.assertIn("No ads", plans)
        self.assertIn('id="redemptionHistory" data-show-more="3"', page, "the growing code history still folds")


class _NestingParser(HTMLParser):
    """Records every <form> that opens while an <a> is open."""

    def __init__(self):
        super().__init__()
        self.open_links = 0
        self.nested_forms = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.open_links += 1
        elif tag == "form" and self.open_links:
            self.nested_forms.append(dict(attrs).get("action"))

    def handle_endtag(self, tag):
        if tag == "a" and self.open_links:
            self.open_links -= 1


class DashboardListTests(DeleteCase):
    def render_context(self, path="/dashboard"):
        captured = {}
        original = application.render_template

        def render(template, **context):
            if template == "dashboard.html":
                captured.update(context)
            return original(template, **context)

        with patch.object(application, "render_template", side_effect=render):
            response = self.client.get(path)
        self.assertEqual(response.status_code, 200)
        return response.get_data(as_text=True), captured

    def test_delete_controls_sit_beside_the_card_links_with_a_confirmation(self):
        ids = self.seed_project()
        lesson = self.seed_lesson()
        page, _ = self.render_context()
        parser = _NestingParser()
        parser.feed(page)
        self.assertEqual(parser.nested_forms, [], "a form inside a link is invalid HTML")
        for action in (f"/projects/{ids['project']}/delete", f"/lessons/{lesson['id']}/delete"):
            form = re.search(r'<form class="item-delete-form" method="post" action="%s"[^>]*>(.*?)</form>'
                             % re.escape(action), page, re.S)
            self.assertIsNotNone(form, action)
            self.assertIn("data-confirm-delete=", form.group(0))
            self.assertIn('name="csrf_token"', form.group(1))
            self.assertIn('name="next" value="/dashboard"', form.group(1))
        self.assertIn("This cannot be undone. Your XP and your flashcards are kept.", page)
        self.assertIn('aria-label="Delete project “Physik – Mechanik”"', page)
        self.assertIn("js/show-more.js", page)
        self.assertIn("js/confirm-delete.js", page)

    def test_lists_are_longer_and_marked_for_show_more(self):
        for number in range(30):
            self.seed_lesson(title=f"Lektion {number:02d}", mistakes=1, created_offset=number)
        page, context = self.render_context()
        self.assertEqual(len(context["lessons"]), 30, "more than the twenty of before")
        self.assertEqual(len(context["mistakes"]), 30)
        for number in range(30):
            self.assertIn(f"Lektion {number:02d}", page, "every item is in the page; only the script folds")
        self.assertIn('id="dashboardLessons" data-show-more="5"', page)
        self.assertIn('id="mistakeList" data-show-more="3"', page)
        self.assertNotIn(" hidden", re.search(r'<div class="mistake-list".*?</div>', page, re.S).group(0))

    def test_the_filters_and_mark_as_understood_still_work(self):
        self.seed_lesson(title="Bruch", mistakes=2)
        self.seed_lesson(title="Kraft", subject="Physics", mistakes=1)
        page, context = self.render_context("/dashboard?subject=Physics")
        self.assertEqual([row["attempt"].subject for row in context["mistakes"]], ["Physics"])
        self.assertIn('name="next" value="/dashboard?subject=Physics"', page)
        attempt_id = context["mistakes"][0]["attempt"].id
        marked = self.client.post(f"/mistakes/{attempt_id}/understood", data={"subject": "Physics", "status": ""})
        self.assertIn(marked.status_code, (302, 303))
        self.assertEqual(self.client.get(marked.headers["Location"]).status_code, 200)

    def test_the_lesson_list_costs_the_same_queries_for_five_or_forty_lessons(self):
        def queries():
            statements = []

            def record(*_args, **_kwargs):
                statements.append(1)

            with application.app.app_context():
                engine = db.engine
            event.listen(engine, "before_cursor_execute", record)
            try:
                self.render_context()
            finally:
                event.remove(engine, "before_cursor_execute", record)
            return len(statements)

        def draft_project(title):
            # A project still in its draft (topics not confirmed): listed on the dashboard,
            # but not planned, so the planner's own per-plan work does not count here.
            with application.app.app_context():
                project = M.LearningProject(user_id=self.user_id(), title=title, subject="Physics")
                db.session.add(project)
                db.session.flush()
                stored = M.ProjectFile(project_id=project.id, original_filename="a.png", mime_type="image/png",
                                       original_data=b"png")
                db.session.add(stored)
                db.session.flush()
                db.session.add(M.ProjectPage(project_id=project.id, file_id=stored.id, page_order=1))
                db.session.add(M.LearningSection(project_id=project.id, position=1, title="Thema"))
                db.session.commit()

        for number in range(5):
            self.seed_lesson(title=f"A{number}", mistakes=2)
        draft_project("Entwurf 1")
        few = queries()
        for number in range(35):
            self.seed_lesson(title=f"B{number}", mistakes=2)
        for number in range(2, 9):
            draft_project(f"Entwurf {number}")
        many = queries()
        self.assertEqual(many, few, "no query per lesson or per project")

    def test_settings_lists_every_code_and_marks_it_for_show_more(self):
        with application.app.app_context():
            seed.premium_codes(application, self.user_id(), 8)
            db.session.commit()
        page = self.client.get("/settings").get_data(as_text=True)
        self.assertIn('id="redemptionHistory" data-show-more="3"', page)
        self.assertEqual(page.count("…K00"), 8, "eight codes, more than the five of before")


class ChatCardTests(StudyFormatRouteCase):
    """The chat's cards after the lesson or project they opened was deleted from the dashboard."""

    def test_a_deleted_continue_learning_lesson_can_be_built_again(self):
        conversation_id = self.conversation()
        with FakeAI(lesson_payload()):
            built = self.lesson(conversation_id)
        self.assertEqual(built.status_code, 201, built.get_data(as_text=True))
        lesson_id, url = built.get_json()["lesson_id"], built.get_json()["url"]
        state = self.state(conversation_id)
        self.assertEqual((state["status"], state["url"]), ("ready", url))
        deleted = self.client.post(f"/lessons/{lesson_id}/delete")
        self.assertEqual(deleted.status_code, 302)
        state = self.state(conversation_id)
        self.assertEqual(state["status"], "none", "no link to a lesson that is gone")
        self.assertIsNone(state["url"])
        self.assertEqual(state["lessons"], [])
        self.assertTrue(state["available"], "the button offers to build it again")
        self.assertEqual(self.client.get(url).status_code, 200, "an old link opens the lesson builder, no 500")
        with FakeAI(lesson_payload()):
            again = self.lesson(conversation_id)
        self.assertEqual(again.status_code, 201)
        self.assertNotEqual(again.get_json()["url"], url, "a new lesson (SQLite may reuse the id, not the session)")

    def test_a_deleted_topic_lesson_goes_back_to_not_built(self):
        conversation_id = self.exam_conversation(THREE)
        keys = [item["key"] for item in self.state(conversation_id)["format"]["topics"]]
        with FakeAI(lesson_payload("Bruchrechnung")):
            built = self.lesson(conversation_id, topic_key=keys[0])
        self.assertEqual(built.status_code, 201, built.get_data(as_text=True))
        self.assertEqual(self.state(conversation_id)["format"]["topics"][0]["status"], "ready")
        self.client.post(f"/lessons/{built.get_json()['lesson_id']}/delete")
        topic = self.state(conversation_id)["format"]["topics"][0]
        self.assertEqual((topic["status"], topic["url"]), ("none", None))
        self.assertEqual(self.lesson_rows(conversation_id), [])

    def test_a_deleted_chat_project_is_offered_again(self):
        conversation_id = self.exam_conversation(TWELVE)
        created = self.project(conversation_id)
        self.assertEqual(created.status_code, 201, created.get_data(as_text=True))
        project_id = created.get_json()["project_id"]
        self.assertEqual(self.state(conversation_id)["format"]["project"]["status"], "ready")
        deleted = self.client.post(f"/projects/{project_id}/delete")
        self.assertEqual(deleted.status_code, 302)
        fmt = self.state(conversation_id)["format"]
        self.assertIsNone(fmt["project"], "the card offers to create the project again, no dead link")
        again = self.project(conversation_id)
        self.assertEqual(again.status_code, 201, again.get_data(as_text=True))
        self.assertEqual(len(self.projects()), 1, "one new project (SQLite may give it the old id)")
        self.assertEqual(self.state(conversation_id)["format"]["project"]["status"], "ready")
        with application.app.app_context():
            self.assertEqual(db.session.execute(text("PRAGMA foreign_key_check")).all(), [])


if __name__ == "__main__":
    unittest.main()

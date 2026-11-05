"""The dashboard's vocabulary widget with dates as SQLite returns them (FIX_PLAN PF5, R10).

SQLite hands a `DateTime(timezone=True)` column back without a timezone, so comparing it with
the aware "now" raised TypeError and the whole dashboard failed for any student with vocabulary
study state on a development database (PostgreSQL returns aware values and was not affected).
"""

import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select

import app as application
from tests.config_isolation import keep_config

M = application
db = application.db


class DashboardVocabularyWidgetTests(unittest.TestCase):
    def setUp(self):
        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "FEATURE_VOCABULARY_TRAINER")
        application.app.config.update(TESTING=True, FEATURE_VOCABULARY_TRAINER=True)
        with application.app.app_context():
            db.drop_all()
            db.create_all()
            application.ensure_database()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lena", "email": "lena@example.com",
                                            "password": "correct-horse-battery"})

    def test_naive_next_review_at(self):
        with application.app.app_context():
            user_id = db.session.scalar(select(M.User.id).where(M.User.username == "lena"))
            vocabulary_list = M.VocabularyList(owner_user_id=user_id, title="Unit 3", source_language="en",
                                               target_language="de")
            db.session.add(vocabulary_list)
            db.session.flush()
            entries = [M.VocabularyEntry(list_id=vocabulary_list.id, source_language="en", target_language="de",
                                         source_term=term, target_translation=translation)
                       for term, translation in (("house", "das Haus"), ("tree", "der Baum"))]
            db.session.add_all(entries)
            db.session.flush()
            now = M.utcnow()
            db.session.add_all([
                M.VocabularyStudyState(entry_id=entries[0].id, direction="source_to_target",
                                       next_review_at=now - timedelta(hours=1)),
                M.VocabularyStudyState(entry_id=entries[1].id, direction="source_to_target",
                                       next_review_at=now + timedelta(days=2)),
            ])
            db.session.commit()
        with application.app.app_context():
            stored = db.session.scalars(select(M.VocabularyStudyState)).first()
            self.assertIsNone(stored.next_review_at.tzinfo, "the SQLite shape this test is about")

        captured = {}
        original = application.render_template

        def render(template, **context):
            if template == "dashboard.html":
                captured.update(context)
            return original(template, **context)

        with patch.object(application, "render_template", side_effect=render):
            response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        # One state is due, one is not: the widget counted instead of crashing.
        self.assertEqual(captured["vocabulary_widget"]["due"], 1)
        self.assertEqual(captured["vocabulary_widget"]["words"], 2)


if __name__ == "__main__":
    unittest.main()

"""The human review queue exists for safety doubts. Quality is shown, not gated.

Two things used to send a set into `pending_manual_review`: the safety pass being
unable to reach a verdict, and the quality review scoring between 3.0 and 4.0. The
second one was the surprise. Its reason code was `approved_with_suggestions` - the
band was always meant to publish - but the status it returned was a hold, so every
ordinary three-star set waited for a reviewer who, with COMMUNITY_MODERATORS empty,
does not exist. From the author's side that was "publishing takes forever".

Nothing here loosens what blocks hidden messages or bad meanings: the moderation pass
still runs first and fails closed. Since v2 no quality score holds a set back - a low
one publishes with its warnings visible - and a safety flag from the quality review buys
a stricter moderation pass rather than rejecting on the reviewer's word alone.
"""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_quality_gate_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.community import service as community  # noqa: E402
from tests.test_moderation_integration import (  # noqa: E402
    ALLOW, QUALITY_REVIEW, route_ai,
)


class DecisionTableTests(unittest.TestCase):
    def test_no_score_without_a_safety_flag_ever_enters_the_human_queue(self):
        for tenths in range(0, 51):
            decision = community.publication_decision(tenths / 10, [])
            self.assertNotEqual(decision["status"], "pending_manual_review",
                                f"score {tenths / 10} was held for a human")

    def test_the_middle_band_publishes_and_keeps_its_suggestions_reason(self):
        for score in (3.0, 3.4, 3.9):
            decision = community.publication_decision(score, [])
            self.assertEqual(decision["status"], "approved", score)
            self.assertEqual(decision["reason"], "approved_with_suggestions", score)

    def test_without_moderation_a_safety_flag_still_rejects_at_any_score(self):
        for score in (1.0, 3.5, 5.0):
            decision = community.publication_decision(score, ["personal_information"])
            self.assertEqual((decision["status"], decision["reason"]),
                             ("rejected", "safety_violation"), score)

    def test_low_scores_publish_with_warnings_instead_of_rejecting(self):
        for score in (2.5, 1.0, 0.5):
            decision = community.publication_decision(score, [])
            self.assertEqual((decision["status"], decision["reason"]),
                             ("approved", "published_with_warnings"), score)


class PublishRouteTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, FEATURE_COMMUNITY_PUBLISHING=True, FEATURE_COMMUNITY_LIBRARY=True,
            FEATURE_COMMUNITY_MODERATION=True, FEATURE_PRIVATE_FLASHCARDS=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "author", "email": "author@example.com",
            "password": "correct-horse-battery"})
        created = self.client.post("/api/flashcards/sets", json={
            "title": "Unité 3", "subject": "Languages", "difficulty": "medium",
            "cards": [{"type": "question_answer", "front": "la médiathèque", "back": "die Mediathek"},
                      {"type": "question_answer", "front": "le bâtiment", "back": "das Gebäude"},
                      {"type": "question_answer", "front": "une marche", "back": "eine Stufe"}]})
        self.assertEqual(created.status_code, 201, created.data)
        self.set_id = created.get_json()["id"]

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def publish(self, quality):
        with route_ai(ALLOW, quality):
            return self.client.post("/api/community/publish", json={
                "source_set_id": self.set_id, "title": "Unité 3", "subject": "Languages",
                "grade": "7", "language": "de", "confirm": True})

    def test_a_three_star_vocabulary_set_is_published_with_its_suggestions(self):
        # The shape of a perfectly ordinary submission: short, correct, unremarkable.
        middling = {**QUALITY_REVIEW, "overallScore": 3.2, "accuracyScore": 3.5,
                    "clarityScore": 3.0, "usefulnessScore": 3.0, "coverageScore": 2.8,
                    "confidence": "Medium", "improvements": ["Add example sentences."]}
        response = self.publish(middling)
        self.assertEqual(response.status_code, 201, response.data)
        body = response.get_json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["review"]["stars"], 3)
        self.assertEqual(body["review"]["decision"]["reason"], "approved_with_suggestions")
        self.assertEqual(body["review"]["improvements"], ["Add example sentences."])
        self.assertEqual(len(self.client.get("/api/community/library").get_json()["sets"]), 1)

    def test_the_moderation_block_no_longer_claims_publication(self):
        # Moderation decides safety; the badge decides publication. The two were saying
        # different things on the same screen.
        body = self.publish({**QUALITY_REVIEW, "overallScore": 1.0}).get_json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["review"]["decision"]["reason"], "published_with_warnings")
        self.assertEqual(body["moderation"]["decision"], "allow")
        self.assertNotIn("published", body["moderation"]["status_message"].casefold())
        self.assertNotEqual(body["moderation"]["decision_label"], "Published")

    def test_a_quality_safety_flag_is_settled_by_the_stricter_check(self):
        # The stricter moderation pass (ALLOW here) finds nothing to quote, so the set is
        # published and an administrator hears about the disagreement.
        flagged = {**QUALITY_REVIEW, "safetyFlags": ["personal_information"]}
        body = self.publish(flagged).get_json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(len(self.client.get("/api/community/library").get_json()["sets"]), 1)
        with application.app.app_context():
            kinds = [row.kind for row in application.db.session.scalars(
                application.db.select(application.AdminNotification)).all()]
        self.assertEqual(kinds, ["quality_safety_conflict"])


if __name__ == "__main__":
    unittest.main()

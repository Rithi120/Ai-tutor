import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from flask import Flask


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_community_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.community import service as community  # noqa: E402
from learnova.config import configure_app  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


REVIEW_HIGH = {
    "overallScore": 4.5, "accuracyScore": 4.8, "clarityScore": 4.4, "usefulnessScore": 4.5,
    "coverageScore": 4.2, "difficultyScore": 4.3, "originalityScore": 4.1, "confidence": "High",
    "summary": "Strong, accurate set.", "strengths": ["Clear"], "improvements": ["Add one example"],
    "flaggedCards": [], "safetyFlags": [],
}
REVIEW_SAFETY = {**REVIEW_HIGH, "safetyFlags": ["offensive"], "summary": "Contains offensive content."}
SET_CARDS = [
    {"type": "question_answer", "front": "What is a cell?", "back": "The basic unit of life."},
    {"type": "term_definition", "front": "Nucleus", "back": "Controls the cell and stores DNA."},
]


class CommunityServiceTests(unittest.TestCase):
    def test_ai_stars_bands(self):
        self.assertEqual(community.ai_stars(4.7)[0], 5)
        self.assertEqual(community.ai_stars(3.6)[0], 4)
        self.assertEqual(community.ai_stars(2.6)[0], 3)
        self.assertEqual(community.ai_stars(1.6)[0], 2)
        self.assertEqual(community.ai_stars(0.9)[0], 1)

    def test_publication_decision_bands_and_safety_override(self):
        self.assertEqual(community.publication_decision(4.2, [])["status"], "approved")
        self.assertEqual(community.publication_decision(3.4, [])["status"], "pending_manual_review")
        needs = community.publication_decision(2.5, [])
        self.assertEqual((needs["status"], needs["correctable"]), ("rejected", True))
        self.assertEqual(community.publication_decision(1.2, [])["status"], "rejected")
        # safety overrides even an excellent score
        override = community.publication_decision(5.0, ["offensive"])
        self.assertEqual((override["status"], override["reason"]), ("rejected", "safety_violation"))

    def test_bayesian_average_protects_against_single_review(self):
        one_five = community.bayesian_average(5, 1)
        many_strong = community.bayesian_average(450, 100)  # avg 4.5 over 100 reviews
        self.assertLess(one_five, many_strong)
        self.assertLess(one_five, 5.0)

    def test_ranking_score_weights_and_penalty(self):
        strong = community.ranking_score(ai_overall=5, student_bayesian=5, completion_rate=1,
                                         helpful_votes=50, save_count=50, recency=1)
        weak = community.ranking_score(ai_overall=2, student_bayesian=2)
        self.assertGreater(strong, weak)
        self.assertLessEqual(strong, 1.0)
        penalized = community.ranking_score(ai_overall=5, student_bayesian=5, penalty=0.3)
        unpenalized = community.ranking_score(ai_overall=5, student_bayesian=5)
        self.assertAlmostEqual(unpenalized - penalized, 0.3, places=3)

    def test_normalize_review_derives_overall_and_flags(self):
        derived = community.normalize_ai_review({"accuracyScore": 4, "clarityScore": 4, "summary": "ok"})
        self.assertGreater(derived["scores"]["overallScore"], 0)
        flagged = community.normalize_ai_review({**REVIEW_SAFETY})
        self.assertEqual(flagged["safety_flags"], ["offensive"])
        with self.assertRaises(ValueError):
            community.normalize_ai_review({"summary": "no scores"})

    def test_production_flags_are_independent_and_environment_configurable(self):
        with patch.dict(os.environ, {
            "APP_ENV": "production", "AI_MODE": "live", "SECRET_KEY": "production-secret",
            "FEATURE_COMMUNITY_LIBRARY": "true",
            "FEATURE_COMMUNITY_PUBLISHING": "false",
        }, clear=False):
            configured = Flask("production-community")
            configure_app(configured)
        self.assertTrue(configured.config["FEATURE_COMMUNITY_LIBRARY"])
        self.assertFalse(configured.config["FEATURE_COMMUNITY_PUBLISHING"])
        render_config = Path(application.app.root_path, "render.yaml").read_text(encoding="utf-8")
        self.assertIn("FEATURE_COMMUNITY_LIBRARY", render_config)
        self.assertIn("FEATURE_COMMUNITY_PUBLISHING", render_config)


class CommunityApiTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_COMMUNITY_PUBLISHING=True,
            FEATURE_COMMUNITY_LIBRARY=True, FEATURE_PRIVATE_FLASHCARDS=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.alice = application.app.test_client()
        self.alice.post("/register", data={
            "username": "alice", "email": "alice@example.com", "password": "correct-horse-battery"})

    def _create_set(self, client, cards=None):
        saved = client.post("/api/flashcards/sets", json={
            "title": "Bio", "subject": "Biology", "difficulty": "medium",
            "cards": cards or SET_CARDS})
        self.assertEqual(saved.status_code, 201)
        return saved.get_json()["id"]

    def _publish(self, client, source_set_id, review_payload=REVIEW_HIGH):
        with patch.object(application, "create_response", return_value=FakeResponse(review_payload)):
            return client.post("/api/community/publish", json={
                "source_set_id": source_set_id, "title": "Cell Biology Basics", "subject": "Biology",
                "topic": "Cells", "grade": "8", "difficulty": "medium", "language": "en",
                "tags": ["cells", "biology"], "author_display": "username", "confirm": True})

    def test_publish_requires_confirmation(self):
        set_id = self._create_set(self.alice)
        response = self.alice.post("/api/community/publish", json={"source_set_id": set_id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "confirmation_required")

    def test_publish_high_score_auto_approves_and_lists(self):
        set_id = self._create_set(self.alice)
        published = self._publish(self.alice, set_id)
        self.assertEqual(published.status_code, 201)
        body = published.get_json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["review"]["stars"], 5)
        self.assertEqual(body["review"]["decision"]["status"], "approved")

        library = self.alice.get("/api/community/library").get_json()
        self.assertEqual(len(library["sets"]), 1)
        entry = library["sets"][0]
        # The two ratings are separate and both present.
        self.assertEqual(entry["ai_review"]["overall"], 4.5)
        self.assertIsNone(entry["student_rating"]["average"])
        self.assertEqual(entry["student_rating"]["count"], 0)

    def test_safety_flag_rejects_regardless_of_score(self):
        set_id = self._create_set(self.alice)
        published = self._publish(self.alice, set_id, review_payload=REVIEW_SAFETY)
        self.assertEqual(published.get_json()["status"], "rejected")
        self.assertEqual(published.get_json()["review"]["decision"]["reason"], "safety_violation")
        # rejected sets never appear publicly
        self.assertEqual(len(self.alice.get("/api/community/library").get_json()["sets"]), 0)

    def test_rating_requires_study_and_blocks_self_and_updates_bayesian(self):
        set_id = self._create_set(self.alice)
        public_id = self._publish(self.alice, set_id).get_json()["id"]

        # creator cannot rate their own set
        self.assertEqual(self.alice.post(f"/api/community/sets/{public_id}/rate",
                                         json={"stars": 5}).status_code, 403)

        bob = application.app.test_client()
        bob.post("/register", data={"username": "bob", "email": "bob@example.com",
                                    "password": "correct-horse-battery"})
        # must study before rating
        self.assertEqual(bob.post(f"/api/community/sets/{public_id}/rate",
                                  json={"stars": 5}).get_json()["code"], "not_studied")
        self.assertEqual(bob.post(f"/api/community/sets/{public_id}/study").status_code, 200)
        rated = bob.post(f"/api/community/sets/{public_id}/rate", json={"stars": 5})
        self.assertEqual(rated.status_code, 200)
        student = rated.get_json()["student_rating"]
        self.assertEqual(student["count"], 1)
        self.assertEqual(student["average"], 5.0)
        self.assertLess(student["bayesian"], 5.0)  # Bayesian pulls a single 5-star down

        # one rating per user: updating replaces, does not add
        bob.post(f"/api/community/sets/{public_id}/rate", json={"stars": 3})
        detail = self.alice.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["student_rating"]["count"], 1)
        self.assertEqual(detail["student_rating"]["average"], 3.0)

    def test_invalid_rating_rejected(self):
        set_id = self._create_set(self.alice)
        public_id = self._publish(self.alice, set_id).get_json()["id"]
        bob = application.app.test_client()
        bob.post("/register", data={"username": "bob", "email": "bob@example.com",
                                    "password": "correct-horse-battery"})
        bob.post(f"/api/community/sets/{public_id}/study")
        self.assertEqual(bob.post(f"/api/community/sets/{public_id}/rate",
                                  json={"stars": 9}).status_code, 400)


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_cards_frontend_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


REVIEW_HIGH = {
    "overallScore": 4.6, "accuracyScore": 4.8, "clarityScore": 4.5, "usefulnessScore": 4.6,
    "coverageScore": 4.4, "difficultyScore": 4.3, "originalityScore": 4.2, "confidence": "High",
    "summary": "Great set.", "strengths": ["Clear"], "improvements": [], "flaggedCards": [], "safetyFlags": [],
}
CARDS = [
    {"type": "question_answer", "front": "What is a cell?", "back": "The basic unit of life."},
    {"type": "term_definition", "front": "Nucleus", "back": "Stores DNA."},
]


class CardsFrontendTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "alice", "email": "alice@example.com", "password": "correct-horse-battery"})

    def _make_set(self, client=None, cards=None):
        client = client or self.client
        return client.post("/api/flashcards/sets", json={
            "title": "Bio", "subject": "Biology", "cards": cards or CARDS}).get_json()["id"]

    # ---- page rendering ----
    def test_flashcards_page_renders_with_flags(self):
        response = self.client.get("/flashcards")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"setsGrid", response.data)
        self.assertIn(b"LEARNOVA_FLAGS", response.data)

    def test_creator_page_renders(self):
        response = self.client.get("/flashcards/create")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"cardRows", response.data)
        self.assertIn(b"saveBar", response.data)

    def test_community_page_renders_when_library_enabled(self):
        response = self.client.get("/community")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"commGrid", response.data)

    def test_community_page_404_when_library_disabled(self):
        application.app.config["FEATURE_COMMUNITY_LIBRARY"] = False
        try:
            self.assertEqual(self.client.get("/community").status_code, 404)
        finally:
            application.app.config["FEATURE_COMMUNITY_LIBRARY"] = True

    # ---- feature-flag gating on the API ----
    def test_publish_blocked_when_publishing_flag_off(self):
        set_id = self._make_set()
        application.app.config["FEATURE_COMMUNITY_PUBLISHING"] = False
        try:
            response = self.client.post("/api/community/publish",
                                        json={"source_set_id": set_id, "confirm": True})
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.get_json()["code"], "feature_disabled")
        finally:
            application.app.config["FEATURE_COMMUNITY_PUBLISHING"] = True

    # ---- edit preserves spaced-repetition state ----
    def test_edit_set_preserves_srs_and_reconciles_cards(self):
        set_id = self._make_set()
        detail = self.client.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]
        keep_id = detail["cards"][0]["id"]
        # advance SRS on the kept card
        self.client.post(f"/api/flashcards/cards/{keep_id}/review", json={"grade": "good"})
        # PUT: edit kept card, drop the second, add a new one
        self.client.put(f"/api/flashcards/sets/{set_id}", json={"title": "Bio v2", "cards": [
            {"id": keep_id, "type": "question_answer", "front": "What is a cell? (edited)", "back": "Basic unit of life."},
            {"type": "question_answer", "front": "What is DNA?", "back": "The molecule of heredity."},
        ]})
        updated = self.client.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]
        self.assertEqual(updated["title"], "Bio v2")
        self.assertEqual(len(updated["cards"]), 2)
        kept = next(c for c in updated["cards"] if c["id"] == keep_id)
        self.assertEqual(kept["front"], "What is a cell? (edited)")
        self.assertEqual(kept["repetition_count"], 1)  # SRS preserved across edit
        added = next(c for c in updated["cards"] if c["id"] != keep_id)
        self.assertEqual(added["repetition_count"], 0)  # new card starts fresh
        self.assertNotIn("Nucleus", [c["front"] for c in updated["cards"]])  # dropped card removed

    def test_duplicate_creates_independent_copy(self):
        set_id = self._make_set()
        duplicated = self.client.post(f"/api/flashcards/sets/{set_id}/duplicate")
        self.assertEqual(duplicated.status_code, 201)
        sets = self.client.get("/api/flashcards/sets").get_json()["sets"]
        self.assertEqual(len(sets), 2)
        self.assertTrue(any(s["title"].endswith("(copy)") for s in sets))

    # ---- saved community copy is isolated from the public original ----
    def test_save_community_copy_is_isolated(self):
        set_id = self._make_set()
        with patch.object(application, "create_response", return_value=FakeResponse(REVIEW_HIGH)):
            published = self.client.post("/api/community/publish", json={
                "source_set_id": set_id, "title": "Public Bio", "subject": "Biology",
                "confirm": True, "author_display": "username"})
        public_id = published.get_json()["id"]

        bob = application.app.test_client()
        bob.post("/register", data={"username": "bob", "email": "bob@example.com", "password": "correct-horse-battery"})
        saved = bob.post(f"/api/community/sets/{public_id}/save")
        self.assertEqual(saved.status_code, 201)
        copy_id = saved.get_json()["id"]

        # Bob edits his copy heavily
        bob.put(f"/api/flashcards/sets/{copy_id}", json={"title": "Bob edit", "cards": [
            {"type": "question_answer", "front": "totally different", "back": "changed"}]})

        # The public original is unchanged
        with application.app.app_context():
            public = application.db.session.get(application.PublicFlashcardSet, public_id)
            public_cards = json.loads(public.cards_json)
        self.assertEqual(len(public_cards), 2)
        self.assertIn("What is a cell?", [c["front"] for c in public_cards])
        # Bob's copy is a separate owned set
        bob_sets = bob.get("/api/flashcards/sets").get_json()["sets"]
        self.assertEqual(len(bob_sets), 1)
        self.assertEqual(bob_sets[0]["title"], "Bob edit")


if __name__ == "__main__":
    unittest.main()

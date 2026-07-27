import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_fc_publish_test.db"
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
GENERATED = {"title": "T", "cards": [{"type": "question_answer", "front": "Q1", "back": "A1"}]}
CARDS = [
    {"type": "question_answer", "front": "What is a cell?", "back": "The basic unit of life."},
    {"type": "term_definition", "front": "Nucleus", "back": "Stores DNA."},
]


class PublishTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.app.config["FEATURE_COMMUNITY_PUBLISHING"] = True
        application.app.config["FEATURE_COMMUNITY_LIBRARY"] = True
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = self._register("alice")

    def _register(self, username, language="en"):
        client = application.app.test_client()
        client.post("/register", data={
            "username": username, "email": f"{username}@example.com",
            "password": "correct-horse-battery", "language": language})
        return client

    def _make_set(self, client=None):
        client = client or self.client
        return client.post("/api/flashcards/sets", json={
            "title": "Bio", "subject": "Biology", "cards": CARDS}).get_json()["id"]

    def _publish(self, set_id, review=REVIEW_HIGH, client=None):
        client = client or self.client
        with patch.object(application, "create_response", return_value=FakeResponse(review)):
            return client.post("/api/community/publish", json={
                "source_set_id": set_id, "title": "Public Bio", "subject": "Biology",
                "topic": "Cells", "grade": "8", "difficulty": "medium", "language": "en",
                "tags": ["cells"], "author_display": "username", "confirm": True})

    # ---- publish page + gating ----
    def test_publish_page_renders_for_owner(self):
        set_id = self._make_set()
        response = self.client.get(f"/flashcards/{set_id}/publish")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"pubConfirm", response.data)

    def test_publish_page_404_for_non_owner(self):
        set_id = self._make_set()
        stranger = self._register("stranger")
        self.assertEqual(stranger.get(f"/flashcards/{set_id}/publish").status_code, 404)

    def test_publish_page_404_when_flag_off(self):
        set_id = self._make_set()
        application.app.config["FEATURE_COMMUNITY_PUBLISHING"] = False
        try:
            self.assertEqual(self.client.get(f"/flashcards/{set_id}/publish").status_code, 404)
        finally:
            application.app.config["FEATURE_COMMUNITY_PUBLISHING"] = True

    def test_publish_page_german(self):
        de = self._register("hans", language="de")
        # not owner of alice's set; make hans his own set
        hans_set = self._make_set(de)
        html = de.get(f"/flashcards/{hans_set}/publish").data
        self.assertIn("Karteikartensatz veröffentlichen".encode(), html)

    # ---- state + flow ----
    def test_publication_state_not_published(self):
        set_id = self._make_set()
        state = self.client.get(f"/api/flashcards/sets/{set_id}/publication").get_json()
        self.assertFalse(state["published"])

    def test_confirmation_required(self):
        set_id = self._make_set()
        response = self.client.post("/api/community/publish", json={"source_set_id": set_id})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "confirmation_required")

    def test_publish_approves_and_becomes_public(self):
        set_id = self._make_set()
        published = self._publish(set_id)
        self.assertEqual(published.status_code, 201)
        self.assertEqual(published.get_json()["status"], "approved")
        state = self.client.get(f"/api/flashcards/sets/{set_id}/publication").get_json()
        self.assertTrue(state["published"])
        self.assertEqual(state["status"], "approved")
        library = self.client.get("/api/community/library").get_json()["sets"]
        self.assertEqual(len(library), 1)
        self.assertEqual(library[0]["publication_version"], 1)
        self.assertEqual(library[0]["public_url"], f"/community/sets/{published.get_json()['id']}")

    def test_publish_and_review_persist_after_session_reload(self):
        set_id = self._make_set()
        public_id = self._publish(set_id).get_json()["id"]
        with application.app.app_context():
            application.db.session.remove()
            application.ensure_database()
        reloaded = application.app.test_client()
        self.assertEqual(reloaded.get(f"/api/community/sets/{public_id}").status_code, 200)
        detail = reloaded.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["ai_review_detail"]["summary"], "Great set.")
        self.assertEqual(detail["publication_version"], 1)

    def test_only_approved_status_is_publicly_listed(self):
        set_id = self._make_set()
        approved_id = self._publish(set_id).get_json()["id"]
        blocked_ids = []
        with application.app.app_context():
            approved = application.db.session.get(application.PublicFlashcardSet, approved_id)
            for status in ("pending_ai_review", "pending_manual_review", "rejected",
                           "hidden", "unpublished", "draft"):
                row = application.PublicFlashcardSet(
                    creator_id=approved.creator_id, title=status, subject="Biology",
                    status=status, cards_json="[]", card_count=0)
                application.db.session.add(row)
                application.db.session.flush()
                blocked_ids.append(row.id)
            application.db.session.commit()
        rows = self.client.get("/api/community/library").get_json()["sets"]
        self.assertEqual([row["id"] for row in rows], [approved_id])
        anonymous = application.app.test_client()
        for blocked_id in blocked_ids:
            self.assertEqual(
                anonymous.get(f"/api/community/sets/{blocked_id}").status_code, 404)
            self.assertEqual(
                anonymous.get(f"/community/sets/{blocked_id}").status_code, 404)

    def test_stable_public_detail_page_and_api_work_logged_out(self):
        public_id = self._publish(self._make_set()).get_json()["id"]
        anonymous = application.app.test_client()
        page = anonymous.get(f"/community/sets/{public_id}")
        self.assertEqual(page.status_code, 200)
        self.assertIn(str(public_id).encode(), page.data)
        self.assertEqual(
            anonymous.get(f"/api/community/sets/{public_id}").status_code, 200)

    def test_edit_after_publish_marks_changed_without_touching_public(self):
        set_id = self._make_set()
        self._publish(set_id)
        # edit the private set
        self.client.put(f"/api/flashcards/sets/{set_id}", json={"title": "Bio v2", "cards": [
            {"type": "question_answer", "front": "changed front", "back": "changed back"}]})
        state = self.client.get(f"/api/flashcards/sets/{set_id}/publication").get_json()
        self.assertTrue(state["changed_since_publish"])
        # public snapshot still shows the original approved content
        with application.app.app_context():
            public = application.db.session.get(application.PublicFlashcardSet, state["public_id"])
            public_cards = json.loads(public.cards_json)
        self.assertIn("What is a cell?", [c["front"] for c in public_cards])

    def test_publish_changes_creates_new_version(self):
        set_id = self._make_set()
        public_id = self._publish(set_id).get_json()["id"]
        self.client.put(f"/api/flashcards/sets/{set_id}", json={"title": "v2", "cards": [
            {"type": "question_answer", "front": "new front", "back": "new back"}]})
        with patch.object(application, "create_response", return_value=FakeResponse(REVIEW_HIGH)):
            resubmit = self.client.post(f"/api/community/sets/{public_id}/resubmit")
        self.assertEqual(resubmit.status_code, 200)
        with application.app.app_context():
            versions = application.db.session.scalars(
                application.db.select(application.AIReview).where(application.AIReview.public_set_id == public_id)).all()
            public = application.db.session.get(application.PublicFlashcardSet, public_id)
            snapshots = application.db.session.scalars(
                application.db.select(application.FlashcardPublicationVersion).where(
                    application.FlashcardPublicationVersion.public_set_id == public_id).order_by(
                    application.FlashcardPublicationVersion.version)).all()
        self.assertEqual(len(versions), 2)
        self.assertEqual(len(snapshots), 2)
        self.assertIn("What is a cell?", json.loads(snapshots[0].cards_json)[0]["front"])
        self.assertIn("new front", json.loads(snapshots[1].cards_json)[0]["front"])
        self.assertEqual(public.active_version_id, snapshots[1].id)
        self.assertIn("new front", json.loads(public.cards_json)[0]["front"])

    def test_rejected_resubmission_does_not_replace_approved_snapshot(self):
        set_id = self._make_set()
        public_id = self._publish(set_id).get_json()["id"]
        self.client.put(f"/api/flashcards/sets/{set_id}", json={"cards": [
            {"type": "question_answer", "front": "unsafe edit", "back": "changed"}]})
        rejected = {**REVIEW_HIGH, "overallScore": 1.0}
        with patch.object(application, "create_response", return_value=FakeResponse(rejected)):
            response = self.client.post(f"/api/community/sets/{public_id}/resubmit")
        self.assertEqual(response.get_json()["status"], "rejected")
        with application.app.app_context():
            public = application.db.session.get(application.PublicFlashcardSet, public_id)
            active = application.db.session.get(
                application.FlashcardPublicationVersion, public.active_version_id)
        self.assertIn("What is a cell?", json.loads(active.cards_json)[0]["front"])

    def test_unpublish_removes_public_keeps_private_and_saved_copies(self):
        set_id = self._make_set()
        public_id = self._publish(set_id).get_json()["id"]
        bob = self._register("bob")
        bob.post(f"/api/community/sets/{public_id}/save")  # bob saves a personal copy
        # alice unpublishes
        unpub = self.client.post(f"/api/community/sets/{public_id}/unpublish")
        self.assertEqual(unpub.status_code, 200)
        self.assertEqual(len(self.client.get("/api/community/library").get_json()["sets"]), 0)
        # private set preserved
        self.assertEqual(self.client.get(f"/api/flashcards/sets/{set_id}").status_code, 200)
        # bob's saved copy preserved
        self.assertEqual(len(bob.get("/api/flashcards/sets").get_json()["sets"]), 1)
        # AI review history preserved
        with application.app.app_context():
            reviews = application.db.session.scalars(
                application.db.select(application.AIReview).where(application.AIReview.public_set_id == public_id)).all()
        self.assertGreaterEqual(len(reviews), 1)

    def test_rating_persists_after_reload(self):
        public_id = self._publish(self._make_set()).get_json()["id"]
        bob = self._register("ratingbob")
        bob.post(f"/api/community/sets/{public_id}/study")
        bob.post(f"/api/community/sets/{public_id}/rate", json={"stars": 4})
        with application.app.app_context():
            application.db.session.remove()
        anonymous = application.app.test_client()
        rating = anonymous.get(
            f"/api/community/sets/{public_id}").get_json()["set"]["student_rating"]
        self.assertEqual((rating["average"], rating["count"]), (4.0, 1))

    def test_non_owner_cannot_resubmit_unpublish_or_read_owner_review(self):
        public_id = self._publish(self._make_set()).get_json()["id"]
        stranger = self._register("publicationstranger")
        self.assertEqual(
            stranger.post(f"/api/community/sets/{public_id}/resubmit").status_code, 404)
        self.assertEqual(
            stranger.post(f"/api/community/sets/{public_id}/unpublish").status_code, 404)
        self.assertEqual(
            stranger.get(f"/api/community/sets/{public_id}/review").status_code, 404)

    # ---- Step 2: content language independent of interface language ----
    def test_content_language_independent_of_interface(self):
        de = self._register("greta", language="de")
        with patch.object(application, "create_response", return_value=FakeResponse(GENERATED)):
            english = de.post("/api/flashcards/generate", json={
                "source_kind": "text", "text": "x" * 80, "content_language": "en"}).get_json()
            german = de.post("/api/flashcards/generate", json={
                "source_kind": "text", "text": "x" * 80, "content_language": "de"}).get_json()
            default = de.post("/api/flashcards/generate", json={
                "source_kind": "text", "text": "x" * 80}).get_json()
        self.assertEqual(english["language"], "en")   # English content despite German interface
        self.assertEqual(german["language"], "de")
        self.assertEqual(default["language"], "de")    # falls back to interface language

    def test_creator_has_content_language_selector(self):
        self.assertIn(b"genContentLang", self.client.get("/flashcards/create").data)


if __name__ == "__main__":
    unittest.main()

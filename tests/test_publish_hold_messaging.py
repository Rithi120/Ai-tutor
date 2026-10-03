"""A held set whose check never ran says so, instead of saying it is being checked.

What happened: Groq withdrew the llama-3.x models this app defaulted to. Every
moderation call answered 404, the gateway filed that as a generic internal error, the
policy correctly held the content, and the author read "Your set is being checked and
will appear once the check is complete" under "Waiting for a reviewer" - for a set that
nothing was checking and no reviewer existed to clear. From the outside that looked like
publishing being very slow. It was publishing never finishing.

The hold is right and stays. These tests pin that the words now match it, and that the
one action which actually clears it - resubmitting - is what the author is pointed at.
"""

import os
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_publish_hold_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import moderation  # noqa: E402
from learnova.moderation import policy, taxonomy  # noqa: E402
from learnova.translations import catalog  # noqa: E402

UNAVAILABLE = ["MODERATION_UNAVAILABLE", "NEEDS_HUMAN_REVIEW"]
GENUINE_ESCALATION = ["LOW_CONFIDENCE", "NEEDS_HUMAN_REVIEW"]


class PolicyMessageTests(unittest.TestCase):
    def test_a_failed_check_tells_the_author_it_failed(self):
        message = policy._author_message("review", UNAVAILABLE)
        self.assertEqual(message, moderation.UNAVAILABLE_MESSAGE)
        self.assertIn("could not run", message)
        self.assertIn("Try publishing it again", message)
        self.assertNotIn("being checked", message)

    def test_a_genuine_escalation_still_says_a_check_is_in_progress(self):
        message = policy._author_message("review", GENUINE_ESCALATION)
        self.assertIn("being checked", message)

    def test_the_decision_from_an_unavailable_classification_carries_the_honest_message(self):
        findings = moderation.inspect("The mitochondrion is the powerhouse of the cell. " * 3)
        decision = moderation.decide(moderation.unavailable_classification("404"), findings)
        self.assertEqual(decision.decision, "review", "the hold itself must not change")
        self.assertTrue(decision.requires_review)
        self.assertEqual(decision.author_message, moderation.UNAVAILABLE_MESSAGE)

    def test_the_message_never_echoes_the_provider_error(self):
        # The evidence summary carries the category for reviewers; the author message
        # is a fixed sentence and must stay one.
        decision = moderation.decide(
            moderation.unavailable_classification("model `secret-model-id` not found"),
            moderation.inspect("Photosynthesis turns light into sugar. " * 3))
        self.assertNotIn("secret-model-id", decision.author_message)


class LabelTests(unittest.TestCase):
    def test_a_failed_check_is_not_labelled_waiting_for_a_reviewer(self):
        self.assertEqual(taxonomy.status_label("review", UNAVAILABLE), taxonomy.UNAVAILABLE_LABEL)
        self.assertEqual(taxonomy.status_label("pending", UNAVAILABLE), taxonomy.UNAVAILABLE_LABEL)

    def test_a_genuine_escalation_keeps_its_label(self):
        self.assertEqual(taxonomy.status_label("review", GENUINE_ESCALATION), "Waiting for a reviewer")

    def test_other_decisions_are_untouched_by_the_reason(self):
        for decision in ("allow", "reject", "revision_required"):
            self.assertEqual(taxonomy.status_label(decision, UNAVAILABLE),
                             taxonomy.decision_label(decision), decision)

    def test_a_missing_reason_list_is_tolerated(self):
        self.assertEqual(taxonomy.status_label("review", None), "Waiting for a reviewer")


class TranslationTests(unittest.TestCase):
    def test_every_enabled_language_still_covers_the_catalogue(self):
        self.assertEqual(catalog.SUPPORTED_LANGUAGES, ("en", "de", "fr", "es"))

    def test_the_honest_message_and_label_are_translated_everywhere(self):
        for source in (moderation.UNAVAILABLE_MESSAGE, taxonomy.UNAVAILABLE_LABEL):
            for language in ("de", "fr", "es"):
                self.assertIn(source, catalog.CATALOGS[language],
                              f"{source[:30]!r} has no {language} translation")

    def test_the_german_wording_is_the_intended_one(self):
        self.assertEqual(catalog.translate(taxonomy.UNAVAILABLE_LABEL, "de"),
                         "Prüfung konnte nicht laufen")


class PublishRouteTests(unittest.TestCase):
    """The author-facing payload, end to end through the publish route."""

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
            "title": "Cells", "subject": "Biology", "difficulty": "medium",
            "cards": [{"type": "question_answer", "front": "What does the mitochondrion do?",
                       "back": "It produces most of the cell's ATP through respiration."}] * 3})
        self.assertEqual(created.status_code, 201, created.data)
        self.set_id = created.get_json()["id"]

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def publish_with_provider(self, failure):
        from unittest.mock import patch
        with patch.object(application, "create_response", side_effect=failure):
            return self.client.post("/api/community/publish", json={
                "source_set_id": self.set_id, "title": "Cell Biology", "subject": "Biology",
                "grade": "9", "language": "en", "confirm": True})

    def test_a_404_from_the_provider_is_held_and_described_truthfully(self):
        failure = application.ai_service.AIProviderError(
            "model_not_found", "The configured AI model is not available from the provider.")
        response = self.publish_with_provider(failure)
        self.assertEqual(response.status_code, 201, response.data)
        body = response.get_json()
        self.assertEqual(body["status"], "pending_manual_review", "still held: fail closed")
        self.assertEqual(body["moderation"]["decision"], "review")
        self.assertEqual(body["moderation"]["decision_label"], taxonomy.UNAVAILABLE_LABEL)
        self.assertEqual(body["moderation"]["status_message"], moderation.UNAVAILABLE_MESSAGE)
        self.assertEqual(len(self.client.get("/api/community/library").get_json()["sets"]), 0)

    def test_the_reviewer_record_names_the_real_category(self):
        failure = application.ai_service.AIProviderError(
            "model_not_found", "The configured AI model is not available from the provider.")
        self.publish_with_provider(failure)
        with application.app.app_context():
            record = application.db.session.scalar(
                application.db.select(application.ModerationRecord))
            assert record is not None
            self.assertIn("model_not_found", record.evidence_summary)
            self.assertNotIn("internal_application_error", record.evidence_summary)

    def test_the_status_endpoint_reports_the_same_words_after_the_fact(self):
        failure = application.ai_service.AIProviderError("model_not_found", "unavailable")
        public_id = self.publish_with_provider(failure).get_json()["id"]
        status = self.client.get(f"/api/community/sets/{public_id}/review").get_json()
        self.assertEqual(status["moderation"]["decision_label"], taxonomy.UNAVAILABLE_LABEL)
        self.assertEqual(status["moderation"]["status_message"], moderation.UNAVAILABLE_MESSAGE)


if __name__ == "__main__":
    unittest.main()

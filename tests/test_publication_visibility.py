"""Flashcard publishing status (spec Phase 8, acceptance Test 7).

The bug: after publishing, the owner's set list still said "Private" - the badge was a
constant in library.js and the list API carried no publication data at all. The database
and the real access were right all along. These tests drive the whole lifecycle with two
accounts and an anonymous visitor and check that every author-facing label is the one the
server derives from the same rule the public reads use (`visible_set_filters`):

create -> Private; publish -> Published (and really visible to others); edit -> still
Published with "changed since publish" (not reset by other people studying or rating it);
publish changes -> the approved version STAYS public until the new one passes (owner
decision 2026-11-13), a rejected or held update never replaces it and its text is never
served; unpublish -> Private and 404 for everyone else.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_publication_visibility_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from sqlalchemy import event  # noqa: E402

import app as application  # noqa: E402
from learnova import moderation  # noqa: E402
from learnova.community import service as community  # noqa: E402
from learnova.translations import catalog  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


REVIEW = {
    "overallScore": 4.6, "accuracyScore": 4.8, "clarityScore": 4.5, "usefulnessScore": 4.6,
    "coverageScore": 4.4, "difficultyScore": 4.3, "originalityScore": 4.2, "confidence": "High",
    "summary": "Great set.", "strengths": ["Clear"], "improvements": [], "flaggedCards": [],
    "safetyFlags": [],
}
CARDS = [
    {"type": "question_answer", "front": "What is a cell?", "back": "The basic unit of life."},
    {"type": "term_definition", "front": "Nucleus", "back": "Stores DNA."},
]


def allow():
    dimensions = {name: "pass" for name in moderation.DIMENSIONS}
    dimensions["sexual_content_context"] = "not_applicable"
    return {
        "recommendation": "allow", "dimensions": dimensions, "confidence": 0.95,
        "evidence_sufficiency": "sufficient", "reason_codes": [], "quotes": [],
        "evidence_summary": "Factual subject content.", "suggested_revision": None,
        "requires_review": False,
    }


def reject_insult():
    payload = allow()
    payload.update(
        recommendation="reject", confidence=0.95,
        dimensions={**payload["dimensions"], "harassment_or_insult": "flag"},
        quotes=[{"dimension": "harassment_or_insult", "quote": "a worthless idiot"}],
        reason_codes=["HARASSMENT"], evidence_summary="A direct insult.")
    return payload


def ai(moderation_result=None, review=REVIEW):
    """Answer each AI task with its own payload; an exception means the provider is down."""

    def respond(**kwargs):
        if kwargs.get("task_type") == "content_moderation":
            result = moderation_result if moderation_result is not None else allow()
            if isinstance(result, Exception):
                raise result
            return FakeResponse(result)
        return FakeResponse(review)

    return patch.object(application, "create_response", side_effect=respond)


def unavailable():
    return application.ai_service.AIGatewayError("provider down")


class PublicationVisibilityTests(unittest.TestCase):
    MODERATION = True

    def setUp(self):
        keep_config(self, application.app.config, "FEATURE_COMMUNITY_PUBLISHING",
                    "FEATURE_COMMUNITY_LIBRARY", "FEATURE_COMMUNITY_MODERATION",
                    "FEATURE_PRIVATE_FLASHCARDS", "COMMUNITY_MODERATORS")
        application.app.config.update(
            TESTING=True, FEATURE_COMMUNITY_PUBLISHING=True, FEATURE_COMMUNITY_LIBRARY=True,
            FEATURE_COMMUNITY_MODERATION=self.MODERATION, FEATURE_PRIVATE_FLASHCARDS=True,
            COMMUNITY_MODERATORS={"mod"})
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.alice = self.register("alice")
        self.bob = self.register("bob")
        self.anon = application.app.test_client()

    def register(self, username):
        client = application.app.test_client()
        client.post("/register", data={
            "username": username, "email": f"{username}@example.com",
            "password": "correct-horse-battery"})
        return client

    # ---------------------------------------------------------------- helpers

    def make_set(self, client=None, cards=CARDS, title="Bio"):
        response = (client or self.alice).post("/api/flashcards/sets", json={
            "title": title, "subject": "Biology", "cards": cards})
        self.assertEqual(response.status_code, 201, response.data)
        return response.get_json()["id"]

    def publish(self, set_id, moderation_result=None):
        with ai(moderation_result):
            return self.alice.post("/api/community/publish", json={
                "source_set_id": set_id, "title": "Public Bio", "subject": "Biology",
                "topic": "Cells", "grade": "8", "difficulty": "medium", "language": "en",
                "tags": ["cells"], "author_display": "username", "confirm": True})

    def resubmit(self, public_id, moderation_result=None):
        with ai(moderation_result):
            response = self.alice.post(f"/api/community/sets/{public_id}/resubmit")
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()

    def listed(self, set_id, client=None):
        rows = (client or self.alice).get("/api/flashcards/sets").get_json()["sets"]
        return next(row for row in rows if row["id"] == set_id)["publication"]

    def state(self, set_id):
        response = self.alice.get(f"/api/flashcards/sets/{set_id}/publication")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def library_ids(self, client):
        return [row["id"] for row in client.get("/api/community/library").get_json()["sets"]]

    def served_fronts(self, client, public_id):
        response = client.get(f"/api/community/sets/{public_id}")
        self.assertEqual(response.status_code, 200)
        return [card["front"] for card in response.get_json()["set"]["cards"]]

    def edit_one_card(self, set_id, back):
        """What the real editor sends: the full set, title unchanged, cards with their ids."""

        current = self.alice.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]
        cards = [dict(card) for card in current["cards"]]
        cards[0]["back"] = back
        response = self.alice.put(f"/api/flashcards/sets/{set_id}", json={
            "title": current["title"], "subject": current["subject"],
            "difficulty": current["difficulty"], "cards": cards})
        self.assertEqual(response.status_code, 200, response.data)

    def assert_label(self, set_id, visibility, update_state=None):
        for source, data in (("list", self.listed(set_id)), ("publication", self.state(set_id))):
            with self.subTest(source=source):
                self.assertEqual(data["visibility"], visibility)
                self.assertEqual(data["is_public"], visibility == "published")
                self.assertEqual(data["update_state"], update_state)

    # ---------------------------------------------------------------- lifecycle

    def test_full_lifecycle_two_accounts_and_anonymous(self):
        set_id = self.make_set()
        # 1. Created: Private everywhere, nothing public.
        self.assert_label(set_id, "private")
        self.assertIsNone(self.state(set_id)["public_url"])

        # 2. Published: the label says Published because others really get it.
        published = self.publish(set_id)
        self.assertEqual(published.status_code, 201, published.data)
        body = published.get_json()
        public_id = body["id"]
        self.assertEqual((body["visibility"], body["is_public"]), ("published", True))
        self.assert_label(set_id, "published")
        self.assertEqual(self.state(set_id)["public_url"], f"/community/sets/{public_id}")
        self.assertIn(public_id, self.library_ids(self.bob))
        self.assertIn(public_id, self.library_ids(self.anon))
        self.assertEqual(self.served_fronts(self.bob, public_id), [c["front"] for c in CARDS])
        self.assertEqual(self.anon.get(f"/api/community/sets/{public_id}").status_code, 200)
        self.assertEqual(self.anon.get(f"/community/sets/{public_id}").status_code, 200)

        # Refresh (a second, independent read) says the same.
        self.assert_label(set_id, "published")

        # The owner's data stays the owner's.
        self.assertEqual(self.bob.get(f"/api/flashcards/sets/{set_id}/publication").status_code, 404)
        self.assertEqual(self.bob.get(f"/flashcards/{set_id}").status_code, 404)
        self.assertNotIn(set_id, [row["id"] for row in self.bob.get("/api/flashcards/sets").get_json()["sets"]])

        # 3. Edit a card the way the editor does (title unchanged): still Published, and
        # the author is told the public copy is older.
        self.edit_one_card(set_id, "The smallest unit of life.")
        self.assert_label(set_id, "published")
        self.assertTrue(self.state(set_id)["changed_since_publish"])
        self.assertIn(public_id, self.library_ids(self.bob))
        # Nothing is published by editing: others still get the approved snapshot.
        detail = self.bob.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "The basic unit of life.")

        # Other people studying and rating it does not clear "changed since publish".
        self.assertEqual(self.bob.post(f"/api/community/sets/{public_id}/study").status_code, 200)
        self.assertEqual(self.bob.post(f"/api/community/sets/{public_id}/rate", json={"stars": 4}).status_code, 200)
        self.assertTrue(self.state(set_id)["changed_since_publish"])

        # 4. Publish changes: they pass, so they replace the live version.
        body = self.resubmit(public_id)
        self.assertEqual((body["visibility"], body["update_state"]), ("published", None))
        self.assert_label(set_id, "published")
        self.assertFalse(self.state(set_id)["changed_since_publish"])
        detail = self.bob.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "The smallest unit of life.")

        # 5. Unpublish: Private for the author, gone for everyone else.
        response = self.alice.post(f"/api/community/sets/{public_id}/unpublish")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.get_json()["visibility"], response.get_json()["is_public"]),
                         ("private", False))
        self.assert_label(set_id, "private")
        self.assertEqual(self.library_ids(self.bob), [])
        self.assertEqual(self.bob.get(f"/api/community/sets/{public_id}").status_code, 404)
        self.assertEqual(self.anon.get(f"/api/community/sets/{public_id}").status_code, 404)
        self.assertEqual(self.anon.get(f"/community/sets/{public_id}").status_code, 404)
        self.assertEqual(self.bob.post(f"/api/community/sets/{public_id}/save").status_code, 404)
        # The private set is untouched.
        self.assertEqual(self.alice.get(f"/api/flashcards/sets/{set_id}").status_code, 200)

        # 6. Publishing again is a new publication, Published again.
        again = self.publish(set_id)
        self.assertEqual(again.status_code, 201)
        self.assertNotEqual(again.get_json()["id"], public_id)
        self.assert_label(set_id, "published")
        self.assertEqual(self.state(set_id)["public_id"], again.get_json()["id"])

    # ---------------------------------------------------------------- updates

    def test_an_update_waits_behind_the_live_version_until_it_passes(self):
        set_id = self.make_set()
        public_id = self.publish(set_id).get_json()["id"]
        self.edit_one_card(set_id, "Unchecked new text.")
        # The check cannot run: the update is held, the approved version stays public.
        body = self.resubmit(public_id, unavailable())
        self.assertEqual((body["visibility"], body["update_state"]), ("published", "pending"))
        self.assert_label(set_id, "published", "pending")
        self.assertFalse(self.state(set_id)["changed_since_publish"])
        self.assertEqual(self.state(set_id)["update_status"], "pending_manual_review")
        # Every public read keeps serving the approved snapshot, never the unchecked text.
        self.assertIn(public_id, self.library_ids(self.bob))
        for client in (self.bob, self.anon):
            detail = client.get(f"/api/community/sets/{public_id}").get_json()["set"]
            self.assertEqual(detail["cards"][0]["back"], "The basic unit of life.")
            self.assertNotIn("Unchecked new text", json.dumps(detail))
        studied = self.bob.post(f"/api/community/sets/{public_id}/study").get_json()["set"]
        self.assertEqual(studied["cards"][0]["back"], "The basic unit of life.")
        saved = self.bob.post(f"/api/community/sets/{public_id}/save")
        self.assertEqual(saved.status_code, 201)
        copy = self.bob.get(f"/api/flashcards/sets/{saved.get_json()['id']}").get_json()["set"]
        self.assertEqual(copy["cards"][0]["back"], "The basic unit of life.")
        library_row = next(row for row in self.bob.get("/api/community/library").get_json()["sets"]
                           if row["id"] == public_id)
        self.assertEqual(library_row["title"], "Public Bio")

        # The automatic re-check succeeds: the update replaces the live version.
        with ai(), application.app.app_context():
            self.assertEqual(application.recheck_community_set(public_id), "rechecked")
        self.assert_label(set_id, "published")
        detail = self.bob.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "Unchecked new text.")

    def test_a_rejected_update_keeps_the_approved_version_public(self):
        set_id = self.make_set()
        public_id = self.publish(set_id).get_json()["id"]
        self.edit_one_card(set_id, "Anyone who forgets this is a worthless idiot.")
        body = self.resubmit(public_id, reject_insult())
        self.assertEqual(body["status"], "rejected")
        self.assertEqual((body["visibility"], body["update_state"]), ("published", "action_needed"))
        self.assert_label(set_id, "published", "action_needed")
        # The author sees the outcome of the update.
        state = self.state(set_id)
        self.assertEqual(state["moderation"]["decision"], "reject")
        self.assertEqual(state["status"], "approved")
        # Others keep the approved version, and nothing of the rejected text.
        self.assertIn(public_id, self.library_ids(self.bob))
        detail = self.bob.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "The basic unit of life.")
        self.assertNotIn("worthless", json.dumps(detail))
        with application.app.app_context():
            public = application.db.session.get(application.PublicFlashcardSet, public_id)
            assert public is not None
            self.assertEqual((public.status, public.moderation_decision), ("approved", "allow"))
            self.assertNotIn("worthless", public.cards_json)
        # A fixed resubmission then goes live and clears the update label.
        self.edit_one_card(set_id, "The smallest unit of life.")
        self.resubmit(public_id)
        self.assert_label(set_id, "published")
        detail = self.bob.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "The smallest unit of life.")

    def test_a_moderator_decides_a_held_update(self):
        set_id = self.make_set()
        public_id = self.publish(set_id).get_json()["id"]
        moderator = self.register("mod")
        self.edit_one_card(set_id, "Held new text.")
        self.resubmit(public_id, unavailable())
        records = moderator.get("/api/moderation/queue").get_json()["records"]
        self.assertTrue(records)
        # Rejecting the update leaves the approved version public.
        decided = moderator.post(f"/api/moderation/records/{records[0]['id']}/decide",
                                 json={"decision": "reject", "note": "no"})
        self.assertEqual(decided.status_code, 200)
        self.assertIn(public_id, self.library_ids(self.bob))
        self.assertEqual(self.served_fronts(self.bob, public_id), [c["front"] for c in CARDS])
        self.assert_label(set_id, "published", "action_needed")

        # A second held update, cleared by a person: it goes live.
        self.edit_one_card(set_id, "Cleared new text.")
        self.resubmit(public_id, unavailable())
        records = moderator.get("/api/moderation/queue").get_json()["records"]
        decided = moderator.post(f"/api/moderation/records/{records[0]['id']}/decide",
                                 json={"decision": "allow", "note": "fine"})
        self.assertEqual(decided.status_code, 200)
        self.assert_label(set_id, "published")
        detail = self.bob.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "Cleared new text.")

    def test_a_set_that_is_not_public_is_still_hidden_while_it_is_rechecked(self):
        set_id = self.make_set()
        # First publication held: an honest "being checked", not Private and not Published.
        held = self.publish(set_id, unavailable())
        self.assertEqual(held.status_code, 201)
        public_id = held.get_json()["id"]
        self.assertEqual(held.get_json()["visibility"], "pending")
        self.assert_label(set_id, "pending")
        self.assertNotIn(public_id, self.library_ids(self.bob))
        self.assertEqual(self.bob.get(f"/api/community/sets/{public_id}").status_code, 404)
        self.assertIsNone(self.state(set_id)["public_url"])
        # Rejected content asks for changes and is not public either (the quote must be
        # in the text, or evidence grounding drops the flag).
        self.edit_one_card(set_id, "Anyone who forgets this is a worthless idiot.")
        rejected = self.resubmit(public_id, reject_insult())
        self.assertEqual(rejected["visibility"], "action_needed")
        self.assert_label(set_id, "action_needed")
        self.assertNotIn(public_id, self.library_ids(self.bob))

    def test_hidden_by_a_moderator_says_hidden(self):
        set_id = self.make_set()
        public_id = self.publish(set_id).get_json()["id"]
        moderator = self.register("mod")
        response = moderator.post(f"/api/moderation/reports/{public_id}/action",
                                  json={"action": "restrict", "note": "checking"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assert_label(set_id, "hidden")
        self.assertEqual(self.bob.get(f"/api/community/sets/{public_id}").status_code, 404)

    def test_the_author_cannot_lift_a_moderator_restriction(self):
        """Neither "publish changes" nor "unpublish, then publish again" un-hides a set."""

        set_id = self.make_set()
        public_id = self.publish(set_id).get_json()["id"]
        moderator = self.register("mod")
        restricted = moderator.post(f"/api/moderation/reports/{public_id}/action",
                                    json={"action": "restrict", "note": "checking"})
        self.assertEqual(restricted.status_code, 200, restricted.data)
        self.edit_one_card(set_id, "Changed text after the restriction.")
        with ai():
            resubmitted = self.alice.post(f"/api/community/sets/{public_id}/resubmit")
        self.assertEqual(resubmitted.status_code, 409, resubmitted.data)
        self.assertEqual(resubmitted.get_json()["code"], "restricted_by_moderator")
        withdrawn = self.alice.post(f"/api/community/sets/{public_id}/unpublish")
        self.assertEqual(withdrawn.status_code, 200, withdrawn.data)
        self.assertEqual(withdrawn.get_json()["visibility"], "hidden")
        again = self.publish(set_id)
        self.assertEqual(again.get_json()["id"], public_id)
        self.assert_label(set_id, "hidden")
        self.assertNotIn(public_id, self.library_ids(self.bob))
        self.assertEqual(self.bob.get(f"/api/community/sets/{public_id}").status_code, 404)

    # ---------------------------------------------------------------- withdrawn while checked

    def test_a_late_check_never_republishes_a_withdrawn_set(self):
        """Unpublish (or a moderator's restriction) wins over a check that finishes later.

        An update held because the check was down is retried later. If the author has
        unpublished meanwhile, or a moderator restricted the live deck, that retry must
        not put the set back in the library.
        """

        moderator = self.register("mod")
        for withdraw in ("unpublish", "restrict"):
            with self.subTest(withdraw=withdraw):
                set_id = self.make_set(title=f"Bio {withdraw}")
                public_id = self.publish(set_id).get_json()["id"]
                self.edit_one_card(set_id, "Held new text.")
                self.resubmit(public_id, unavailable())
                if withdraw == "unpublish":
                    response = self.alice.post(f"/api/community/sets/{public_id}/unpublish")
                    expected = "private"
                else:
                    response = moderator.post(f"/api/moderation/reports/{public_id}/action",
                                              json={"action": "restrict", "note": "checking"})
                    expected = "hidden"
                self.assertEqual(response.status_code, 200, response.data)
                with ai(), application.app.app_context():
                    self.assertEqual(application.recheck_community_set(public_id), "withdrawn")
                self.assertEqual(self.listed(set_id)["visibility"], expected)
                self.assertNotIn(public_id, self.library_ids(self.bob))
                self.assertEqual(self.anon.get(f"/api/community/sets/{public_id}").status_code, 404)

    def test_a_moderator_clearing_an_unpublished_set_keeps_it_private(self):
        set_id = self.make_set()
        public_id = self.publish(set_id, unavailable()).get_json()["id"]
        self.assertEqual(self.alice.post(f"/api/community/sets/{public_id}/unpublish").status_code, 200)
        moderator = self.register("mod")
        records = moderator.get("/api/moderation/queue").get_json()["records"]
        self.assertTrue(records)
        decided = moderator.post(f"/api/moderation/records/{records[0]['id']}/decide",
                                 json={"decision": "allow", "note": "fine"})
        self.assertEqual(decided.status_code, 200, decided.data)
        self.assert_label(set_id, "private")
        self.assertNotIn(public_id, self.library_ids(self.bob))
        self.assertEqual(self.bob.get(f"/api/community/sets/{public_id}").status_code, 404)

    # ---------------------------------------------------------------- edits

    def test_a_no_op_save_does_not_count_as_a_change(self):
        set_id = self.make_set()
        self.publish(set_id)
        current = self.alice.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]
        self.alice.put(f"/api/flashcards/sets/{set_id}", json={
            "title": current["title"], "cards": current["cards"]})
        self.assertFalse(self.state(set_id)["changed_since_publish"])

    def test_adding_or_removing_a_card_counts_as_a_change(self):
        set_id = self.make_set()
        self.publish(set_id)
        current = self.alice.get(f"/api/flashcards/sets/{set_id}").get_json()["set"]
        self.alice.put(f"/api/flashcards/sets/{set_id}", json={"cards": current["cards"][:1]})
        self.assertTrue(self.state(set_id)["changed_since_publish"])

    # ---------------------------------------------------------------- the list endpoint

    def test_the_list_reads_publication_state_in_constant_queries(self):
        def count_list_queries():
            statements = []

            def listener(*_args):
                statements.append(1)

            with application.app.app_context():
                engine = application.db.engine
            event.listen(engine, "before_cursor_execute", listener)
            try:
                self.assertEqual(self.alice.get("/api/flashcards/sets").status_code, 200)
            finally:
                event.remove(engine, "before_cursor_execute", listener)
            return len(statements)

        self.publish(self.make_set(title="Set 0"))
        one = count_list_queries()
        for number in range(1, 5):
            self.publish(self.make_set(title=f"Set {number}"))
        five = count_list_queries()
        # Each extra set costs only the card load the list already did (one per set);
        # the publication data is two queries for the whole list.
        self.assertLessEqual(five - one, 4)


class PublicationVisibilityWithoutModerationTests(PublicationVisibilityTests):
    """The same lifecycle with the safety gate off (the quality review is the only check)."""

    MODERATION = False

    # These two exercise the moderation queue and the safety gate itself.
    def test_a_moderator_decides_a_held_update(self):
        self.skipTest("needs FEATURE_COMMUNITY_MODERATION")

    def test_a_set_that_is_not_public_is_still_hidden_while_it_is_rechecked(self):
        self.skipTest("needs FEATURE_COMMUNITY_MODERATION")

    def test_hidden_by_a_moderator_says_hidden(self):
        self.skipTest("needs FEATURE_COMMUNITY_MODERATION")

    def test_the_author_cannot_lift_a_moderator_restriction(self):
        self.skipTest("needs FEATURE_COMMUNITY_MODERATION (the report queue)")

    def test_a_late_check_never_republishes_a_withdrawn_set(self):
        self.skipTest("needs FEATURE_COMMUNITY_MODERATION (a held check)")

    def test_a_moderator_clearing_an_unpublished_set_keeps_it_private(self):
        self.skipTest("needs FEATURE_COMMUNITY_MODERATION")

    def test_an_update_waits_behind_the_live_version_until_it_passes(self):
        # Without moderation a rejection comes from the quality reviewer's safety flag.
        set_id = self.make_set()
        public_id = self.publish(set_id).get_json()["id"]
        self.edit_one_card(set_id, "Rejected new text.")
        with patch.object(application, "create_response", side_effect=lambda **_: FakeResponse(
                {**REVIEW, "safetyFlags": ["offensive"]})):
            body = self.alice.post(f"/api/community/sets/{public_id}/resubmit").get_json()
        self.assertEqual((body["status"], body["update_state"]), ("rejected", "action_needed"))
        self.assert_label(set_id, "published", "action_needed")
        detail = self.bob.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "The basic unit of life.")
        self.assertEqual(detail["ai_review"]["overall"], 4.6)
        self.assertEqual(detail["ai_review_detail"]["decision"]["status"], "approved")

    def test_a_rejected_update_keeps_the_approved_version_public(self):
        self.test_an_update_waits_behind_the_live_version_until_it_passes()


class VisibilityRuleTests(unittest.TestCase):
    """`publication_visibility` is the Python twin of the SQL `visible_set_filters`."""

    def setUp(self):
        keep_config(self, application.app.config, "FEATURE_COMMUNITY_MODERATION")
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()

    def test_published_holds_exactly_when_the_public_filter_matches(self):
        decisions = ("allow", "pending", "review", "reject", "revision_required", "")
        for moderation_on in (True, False):
            application.app.config["FEATURE_COMMUNITY_MODERATION"] = moderation_on
            with application.app.app_context():
                application.db.drop_all()
                application.db.create_all()
                user = application.User(username="owner", email="owner@example.com")
                user.set_password("correct-horse-battery")
                application.db.session.add(user)
                application.db.session.flush()
                rows = {}
                for status in sorted(community.PUBLICATION_STATES):
                    for decision in decisions:
                        row = application.PublicFlashcardSet(
                            creator_id=user.id, title="t", status=status, moderation_decision=decision)
                        application.db.session.add(row)
                        application.db.session.flush()
                        rows[row.id] = (status, decision)
                visible = set(application.db.session.scalars(application.db.select(
                    application.PublicFlashcardSet.id).where(*application.visible_set_filters())).all())
                for row_id, (status, decision) in rows.items():
                    label = community.publication_visibility(status, decision, moderation_on=moderation_on)
                    with self.subTest(moderation=moderation_on, status=status, decision=decision):
                        self.assertIn(label, community.VISIBILITY_STATES)
                        self.assertEqual(label == "published", row_id in visible)
                        public = application.db.session.get(application.PublicFlashcardSet, row_id)
                        self.assertEqual(application.publication_is_visible(public), row_id in visible)
                application.db.session.rollback()

    def test_labels_are_honest_for_every_state(self):
        on = {status: community.publication_visibility(status, "allow", moderation_on=True)
              for status in community.PUBLICATION_STATES}
        self.assertEqual(on["unpublished"], "private")
        self.assertEqual(community.publication_visibility(None, None, moderation_on=True), "private")
        self.assertEqual(on["approved"], "published")
        self.assertEqual(on["hidden"], "hidden")
        self.assertEqual(on["rejected"], "action_needed")
        self.assertEqual(on["changes_requested"], "action_needed")
        for status in ("draft", "pending_moderation", "pending_ai_review", "pending_manual_review"):
            self.assertEqual(on[status], "pending")
        # Approved but not cleared by the safety gate is not public, and never "private".
        self.assertEqual(community.publication_visibility("approved", "review", moderation_on=True), "pending")
        self.assertEqual(community.update_state("pending_manual_review"), "pending")
        self.assertEqual(community.update_state("rejected"), "action_needed")
        self.assertIsNone(community.update_state("approved"))


class FrontendContractTests(unittest.TestCase):
    """The pages render the server's label and nothing else (Node is absent: source checks)."""

    def read(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_the_set_list_badge_is_no_longer_a_constant(self):
        source = self.read("static/js/flashcards/library.js")
        self.assertNotIn("LABEL.private", source)
        self.assertNotIn('"Privat"', source)
        self.assertIn("publicationBadge(set.publication)", source)

    def test_every_page_uses_the_shared_map_and_refreshes_after_back(self):
        for name in ("library", "overview", "publish"):
            source = self.read(f"static/js/flashcards/{name}.js")
            with self.subTest(page=name):
                self.assertIn("publicationBadge", source)
                self.assertRegex(source, r'addEventListener\("pageshow", event => \{?\s*if \(event\.persisted')
        overview = self.read("static/js/flashcards/overview.js")
        self.assertNotIn("STATUS_KEY", overview)
        self.assertIn("state.public_url", overview)
        publish = self.read("static/js/flashcards/publish.js")
        submit = publish[publish.index("async function submitForm"):publish.index("const SIDE_KEY")]
        self.assertIn("await loadState()", submit)
        self.assertNotIn("renderStatus(", submit)
        self.assertIn("state.is_public", publish)

    def test_the_shared_map_covers_every_server_state_with_a_translated_key(self):
        common = self.read("static/js/flashcards/common.js")
        german = catalog.frontend_catalog("de")
        for mapping, states in (("VISIBILITY_KEY", community.VISIBILITY_STATES),
                                ("UPDATE_KEY", community.UPDATE_STATES)):
            block = common[common.index(f"export const {mapping}"):]
            block = block[:block.index("};")]
            keys = dict(re.findall(r"(\w+): \"(\w+)\"", block))
            self.assertEqual(set(keys), set(states), mapping)
            for key in keys.values():
                with self.subTest(key=key):
                    self.assertIn(key, catalog.FRONTEND_MESSAGES)
                    self.assertNotEqual(german[key], catalog.FRONTEND_MESSAGES[key])
        self.assertEqual(german["fcStatusPublished"], "Veröffentlicht")
        self.assertEqual(german["fcStatusPublishedUpdatePending"], "Veröffentlicht · Änderung wird geprüft")

    def test_every_badge_class_has_a_dark_theme_override(self):
        light = self.read("static/css/flashcards.css")
        dark = self.read("static/css/learnova-dark.css")
        classes = [f"visibility-{state}" for state in community.VISIBILITY_STATES] + [
            f"visibility-update-{state}" for state in community.UPDATE_STATES]
        override = dark[dark.index("Flashcard visibility badges"):]
        override = override[:override.index("}")]
        self.assertIn(':root[data-theme="dark"]', override)
        for name in classes:
            with self.subTest(css=name):
                self.assertIn(f".{name}", light)
                self.assertIn(f".{name}", override)


if __name__ == "__main__":
    unittest.main()

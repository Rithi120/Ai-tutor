"""Community publishing v2: proportionate moderation, entry warnings, reports, re-review, alerts.

Every AI call is answered by a scripted responder patched over `app.create_response`,
and every SMTP connection is a mock, so nothing here reaches a provider or sends mail.
"""

import json
import os
import smtplib
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_community_reports_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import moderation, notifications  # noqa: E402
from learnova.community import reports as report_reviews  # noqa: E402
from learnova.community import service as community  # noqa: E402
from tests.test_moderation_integration import (  # noqa: E402
    ALLOW, INSULT_CARDS, QUALITY_REVIEW, REJECT_HARASSMENT, FakeResponse, moderation_payload,
)

ALERT_EMAIL = "maheswariskillbridge@gmail.com"
VOCABULARY = [
    {"type": "question_answer", "front": "la marche", "back": "der Marsch",
     "explanation": "Attention à la marche !"},
    {"type": "question_answer", "front": "le bâtiment", "back": "das Gebäude"},
    {"type": "question_answer", "front": "la chatte", "back": "die Katze"},
    {"type": "question_answer", "front": "Il a fait une faute.", "back": "Er hat einen Fehler gemacht."},
]
WARNING_REVIEW = {
    **QUALITY_REVIEW, "overallScore": 2.4, "accuracyScore": 2.5,
    "entryWarnings": [
        {"entry": 1, "side": "back", "kind": "several_meanings", "text": "der Marsch",
         "note": "Here 'la marche' is a step, as the example shows.", "suggestion": "die Stufe"},
        # Not in the set: the reviewer imagined it, so it must be dropped.
        {"entry": 2, "side": "back", "kind": "translation", "text": "das Haus", "note": "x"},
    ],
}
VALID_TRANSLATION_REPORT = {
    "outcome": "report_valid", "issue_category": "translation",
    "explanation": "'la marche' in the example is a step; 'der Marsch' is a march.",
    "affected": {"entry": 1, "side": "back", "text": "der Marsch"},
    "suggested_correction": "die Stufe", "human_review_recommended": False, "safety_concern": False,
}
UNSUPPORTED_REPORT = {
    "outcome": "report_not_supported", "issue_category": "none",
    "explanation": "'das Gebäude' is a correct translation of 'le bâtiment'.",
    "affected": None, "suggested_correction": "", "human_review_recommended": False,
    "safety_concern": False,
}
POSSIBLE_REPORT = {
    "outcome": "possible_issue", "issue_category": "explanation",
    "explanation": "The example could be read either way.",
    "affected": {"entry": 1, "side": "explanation", "text": "Attention à la marche"},
    "suggested_correction": "", "human_review_recommended": False, "safety_concern": False,
}
SAFETY_REVIEW = moderation_payload(
    recommendation="review", confidence=0.55, evidence_sufficiency="limited",
    requires_review=True, dimensions={"harassment_or_insult": "unknown"},
    evidence_summary="It is unclear whether the remark is aimed at anyone.")


class Responder:
    """Scripted AI: one answer (or a list of answers, used in order) per task type."""

    def __init__(self, **answers):
        self.answers = {task: list(value) if isinstance(value, list) else [value]
                        for task, value in answers.items()}
        self.calls: list[str] = []
        self.delay = 0.0
        self.lock = threading.Lock()

    def __call__(self, **kwargs):
        task = kwargs.get("task_type")
        with self.lock:
            self.calls.append(task)
            queue = self.answers.get(task) or [QUALITY_REVIEW]
            answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if self.delay:
            time.sleep(self.delay)
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)

    def count(self, task):
        return self.calls.count(task)


def provider_down():
    return application.ai_service.AIProviderError("provider_timeout", "timed out")


class CommunityTestCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        application.app.config.update(
            TESTING=True, FEATURE_COMMUNITY_PUBLISHING=True, FEATURE_COMMUNITY_LIBRARY=True,
            FEATURE_COMMUNITY_MODERATION=True, FEATURE_PRIVATE_FLASHCARDS=True,
            COMMUNITY_MODERATORS={"mod"}, COMMUNITY_ALERT_EMAIL=ALERT_EMAIL,
            MAIL_SMTP_HOST="", MAIL_FROM="", MAIL_SMTP_USERNAME="", MAIL_SMTP_PASSWORD="",
            MAIL_MAX_ATTEMPTS=3, REPORT_REVIEW_MAX_ATTEMPTS=3,
            REPORT_REVIEW_MAX_PER_SET_DAY=5, REPORT_REVIEW_MAX_PER_HOUR=60,
            MODERATION_MAX_REPORTS_PER_USER_DAY=20)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.author = self.register("alice")
        self.moderator = self.register("mod")

    def tearDown(self):
        application.app.config.clear()
        application.app.config.update(self.original)

    def register(self, name):
        client = application.app.test_client()
        client.post("/register", data={
            "username": name, "email": f"{name}@example.com", "password": "correct-horse-battery"})
        return client

    def create_set(self, cards=None, title="Unité 3", subject="Languages"):
        response = self.author.post("/api/flashcards/sets", json={
            "title": title, "subject": subject, "difficulty": "medium",
            "cards": cards or VOCABULARY})
        self.assertEqual(response.status_code, 201, response.data)
        return response.get_json()["id"]

    def publish(self, set_id, responder):
        with patch.object(application, "create_response", side_effect=responder):
            return self.author.post("/api/community/publish", json={
                "source_set_id": set_id, "title": "Unité 3", "subject": "Languages",
                "grade": "7", "language": "de", "confirm": True})

    def published(self, responder=None, cards=None):
        set_id = self.create_set(cards)
        response = self.publish(set_id, responder or Responder(content_moderation=ALLOW))
        self.assertEqual(response.status_code, 201, response.data)
        return set_id, response.get_json()["id"]

    def report(self, client, public_id, responder, **body):
        with patch.object(application, "create_response", side_effect=responder):
            return client.post(f"/api/community/sets/{public_id}/report",
                               json={"reason": "incorrect_translation", **body})

    def library_size(self):
        return len(self.author.get("/api/community/library").get_json()["sets"])

    def rows(self, model, **filters):
        with application.app.app_context():
            query = application.db.select(model)
            for name, value in filters.items():
                query = query.where(getattr(model, name) == value)
            rows = application.db.session.scalars(query.order_by(model.id)).all()
            application.db.session.expunge_all()
            return rows

    def public_set(self, public_id):
        with application.app.app_context():
            row = application.db.session.get(application.PublicFlashcardSet, public_id)
            application.db.session.expunge_all()
            return row


# ------------------------------------------------------------------ publishing


class PublishingTests(CommunityTestCase):
    def test_harmless_vocabulary_publishes(self):
        responder = Responder(content_moderation=ALLOW)
        _set_id, public_id = self.published(responder)
        self.assertEqual(self.public_set(public_id).status, "approved")
        self.assertEqual(self.library_size(), 1)
        self.assertEqual(responder.calls, ["content_moderation", "flashcard_review"])

    def test_alternative_translations_publish_with_a_grounded_warning(self):
        set_id = self.create_set()
        body = self.publish(set_id, Responder(content_moderation=ALLOW, flashcard_review=WARNING_REVIEW)).get_json()
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["review"]["decision"]["reason"], "published_with_warnings")
        warnings = body["review"]["entry_warnings"]
        # The ungrounded remark about "das Haus" is gone; the real one remains, alone.
        self.assertEqual([(w["entry"], w["text"]) for w in warnings], [(1, "der Marsch")])
        self.assertIn("several possible meanings", warnings[0]["message"])
        self.assertEqual(warnings[0]["suggestion"], "die Stufe")
        self.assertEqual(self.library_size(), 1)

    def test_public_readers_never_see_entry_warnings(self):
        set_id = self.create_set()
        public_id = self.publish(set_id, Responder(content_moderation=ALLOW, flashcard_review=WARNING_REVIEW)).get_json()["id"]
        reader = self.register("bob")
        detail = reader.get(f"/api/community/sets/{public_id}").get_json()["set"]
        self.assertNotIn("entry_warnings", detail["ai_review_detail"])

    def test_minor_grammar_and_awkward_wording_do_not_block(self):
        awkward = moderation_payload(
            recommendation="revision_required", confidence=0.8,
            dimensions={"language_quality": "flag", "age_appropriateness": "flag"},
            reason_codes=["LOW_LANGUAGE_QUALITY"], evidence_summary="Some awkward phrasing.")
        set_id = self.create_set()
        body = self.publish(set_id, Responder(content_moderation=awkward)).get_json()
        self.assertEqual(body["moderation"]["decision"], "allow")
        self.assertEqual(body["status"], "approved")

    def test_genuinely_prohibited_content_follows_the_existing_policy(self):
        set_id = self.create_set(cards=INSULT_CARDS)
        body = self.publish(set_id, Responder(content_moderation=REJECT_HARASSMENT)).get_json()
        self.assertEqual(body["moderation"]["decision"], "reject")
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(self.library_size(), 0)

    def test_provider_failure_is_held_and_then_rechecked_not_trapped(self):
        set_id = self.create_set()
        body = self.publish(set_id, Responder(content_moderation=provider_down())).get_json()
        self.assertEqual(body["status"], "pending_manual_review")
        self.assertEqual(self.library_size(), 0)
        with patch.object(application, "create_response", side_effect=Responder(content_moderation=ALLOW)):
            with application.app.test_request_context():
                outcome = application.recheck_community_set(body["id"])
        self.assertEqual(outcome, "rechecked")
        self.assertEqual(self.library_size(), 1)

    def test_repeated_provider_failures_alert_an_administrator(self):
        set_id = self.create_set()
        public_id = self.publish(set_id, Responder(content_moderation=provider_down())).get_json()["id"]
        for _ in range(3):
            with patch.object(application, "create_response", side_effect=Responder(content_moderation=provider_down())):
                with application.app.test_request_context():
                    application.recheck_community_set(public_id)
        kinds = [row.kind for row in self.rows(application.AdminNotification)]
        self.assertEqual(kinds, ["review_failed"])
        self.assertEqual(self.library_size(), 0)

    def test_a_quality_review_failure_publishes_with_the_quality_check_pending(self):
        set_id = self.create_set()
        response = self.publish(set_id, Responder(content_moderation=ALLOW, flashcard_review=provider_down()))
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual((body["status"], body["quality_status"], body["review"]), ("approved", "pending", None))
        self.assertEqual(self.library_size(), 1)
        with patch.object(application, "create_response", side_effect=Responder()):
            with application.app.test_request_context():
                self.assertEqual(application.recheck_community_set(body["id"]), "quality_reviewed")
        self.assertEqual(self.public_set(body["id"]).quality_status, "reviewed")

    def test_unchanged_resubmission_reuses_both_checks(self):
        set_id, public_id = self.published()
        again = Responder(content_moderation=ALLOW)
        with patch.object(application, "create_response", side_effect=again):
            response = self.author.post(f"/api/community/sets/{public_id}/resubmit")
        self.assertEqual(response.get_json()["status"], "approved")
        self.assertEqual(again.calls, [], "unchanged content paid for a check again")
        sources = [row.source for row in self.rows(application.ModerationRecord)]
        self.assertEqual(sources[-1], "reused")
        self.assertEqual(len(self.rows(application.AIReview)), 2)

    def test_an_unchanged_revision_request_is_not_asked_again(self):
        privacy = moderation_payload(
            recommendation="revision_required", dimensions={"privacy_or_personal_information": "flag"},
            quotes=[{"dimension": "privacy_or_personal_information", "quote": "die Katze"}],
            reason_codes=["PRIVACY_RISK"], suggested_revision="Remove the name.")
        set_id = self.create_set()
        public_id = self.publish(set_id, Responder(content_moderation=privacy)).get_json()["id"]
        again = Responder(content_moderation=ALLOW)
        with patch.object(application, "create_response", side_effect=again):
            body = self.author.post(f"/api/community/sets/{public_id}/resubmit").get_json()
        self.assertEqual(body["status"], "changes_requested")
        self.assertEqual(again.calls, [])

    def test_an_edit_invalidates_the_reused_checks(self):
        set_id, public_id = self.published()
        self.author.put(f"/api/flashcards/sets/{set_id}", json={"cards": VOCABULARY + [
            {"type": "question_answer", "front": "la porte", "back": "die Tür"}]})
        again = Responder(content_moderation=ALLOW)
        with patch.object(application, "create_response", side_effect=again):
            self.author.post(f"/api/community/sets/{public_id}/resubmit")
        self.assertEqual(again.calls, ["content_moderation", "flashcard_review"])

    def test_quality_reviewer_safety_flag_is_held_when_the_stricter_check_agrees(self):
        set_id = self.create_set(cards=INSULT_CARDS)
        responder = Responder(
            content_moderation=[ALLOW, REJECT_HARASSMENT],
            flashcard_review={**QUALITY_REVIEW, "safetyFlags": ["offensive"]})
        body = self.publish(set_id, responder).get_json()
        self.assertEqual(responder.count("content_moderation"), 2)
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(self.library_size(), 0)


class FalsePositiveRegressionTests(unittest.TestCase):
    """Ordinary study text that the deterministic pass used to treat as a risk."""

    CASES = {
        "messaging app as a word": "das Signal — the signal\nSignal and Telegram are messengers",
        "historical date": "14.07.1789 — Sturm auf die Bastille\n09.11.1989 — Mauerfall",
        "decimal": "π ≈ 3,14159\ne ≈ 2.71828",
        "a link to a source": "Zelle — cell (see https://de.wikipedia.org/wiki/Zelle)",
        "Russian vocabulary": "кошка — die Katze\nсобака — der Hund\nдом — das Haus",
        "Greek vocabulary": "η γάτα — die Katze\nο σκύλος — der Hund",
        "rude second meaning": "der Schwanz — the tail\nla chatte — die Katze",
    }

    def clean(self, **overrides):
        payload = moderation_payload(**overrides)
        payload["available"] = True
        return payload

    def test_ordinary_text_has_no_risk_signal_and_publishes(self):
        context = moderation.ModerationContext(subject="Languages", grade="7", language="de")
        for name, text in self.CASES.items():
            with self.subTest(name):
                findings = moderation.inspect(text)
                self.assertEqual(findings.signals.risk_level, "low", findings.signals.as_dict())
                self.assertFalse(findings.injection_detected)
                decision = moderation.decide(self.clean(), findings, context)
                self.assertEqual(decision.decision, "allow", decision.rationale)

    def test_an_unsure_classifier_does_not_hold_ordinary_text(self):
        context = moderation.ModerationContext(subject="Languages", grade="7", language="de")
        for name, text in self.CASES.items():
            with self.subTest(name):
                unsure = self.clean(confidence=0.3, evidence_sufficiency="limited",
                                    dimensions={"harassment_or_insult": "unknown"})
                decision = moderation.decide(unsure, moderation.inspect(text), context)
                self.assertEqual(decision.decision, "allow", decision.rationale)

    def test_a_real_phone_number_is_still_found(self):
        findings = moderation.inspect("Ruf mich an: 0176 12345678")
        self.assertIn("phone", findings.signals.contact_markers)
        self.assertIn("messaging_app", moderation.inspect("add me on snapchat @lena.k").signals.contact_markers)


class EntryWarningTests(CommunityTestCase):
    def setUp(self):
        super().setUp()
        self.set_id = self.create_set()
        body = self.publish(self.set_id, Responder(content_moderation=ALLOW, flashcard_review=WARNING_REVIEW)).get_json()
        self.public_id = body["id"]

    def act(self, client, index, action):
        return client.post(f"/api/community/sets/{self.public_id}/warnings/{index}", json={"action": action})

    def test_accepting_a_suggestion_changes_only_that_entry_of_the_private_set(self):
        response = self.act(self.author, 0, "accept")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.get_json()["needs_republish"])
        cards = self.author.get(f"/api/flashcards/sets/{self.set_id}").get_json()["set"]["cards"]
        self.assertEqual([card["back"] for card in cards],
                         ["die Stufe", "das Gebäude", "die Katze", "Er hat einen Fehler gemacht."])
        # The public version is untouched until the author republishes.
        detail = self.author.get(f"/api/community/sets/{self.public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "der Marsch")

    def test_keeping_the_original_is_remembered(self):
        self.assertEqual(self.act(self.author, 0, "keep").get_json()["state"], "kept")
        review = self.author.get(f"/api/community/sets/{self.public_id}/review").get_json()["review"]
        self.assertEqual(review["entry_warnings"][0]["state"], "kept")

    def test_nobody_else_can_act_on_an_authors_warnings(self):
        stranger = self.register("mallory")
        self.assertEqual(self.act(stranger, 0, "accept").status_code, 404)
        self.assertEqual(self.act(self.author, 7, "keep").status_code, 404)
        self.assertEqual(self.act(self.author, 0, "delete").status_code, 400)


# ------------------------------------------------------------------ reporting


class ReportingTests(CommunityTestCase):
    def setUp(self):
        super().setUp()
        self.set_id, self.public_id = self.published()
        self.reader = self.register("bob")

    def test_a_valid_report_is_stored_with_version_entry_and_review(self):
        response = self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT),
                               entry=1, detail="Marsch is wrong here")
        self.assertEqual(response.status_code, 201)
        [report] = self.rows(application.ContentReport)
        self.assertEqual((report.reason, report.entry_index, report.content_version, report.status),
                         ("incorrect_translation", 1, 1, "open"))
        self.assertIsNotNone(report.publication_version_id)
        [review] = self.rows(application.ReportReview)
        self.assertEqual((review.outcome, review.status, review.suggested_correction),
                         ("report_valid", "done", "die Stufe"))
        self.assertEqual(report.report_review_id, review.id)
        self.assertEqual(json.loads(review.affected_json)["text"], "der Marsch")

    def test_invalid_targets_and_values_are_refused(self):
        responder = Responder(report_review=VALID_TRANSLATION_REPORT)
        self.assertEqual(self.report(self.reader, 9999, responder).status_code, 404)
        self.assertEqual(self.report(self.reader, self.public_id, responder, entry=99).status_code, 400)
        self.assertEqual(self.report(self.reader, self.public_id, responder, reason="because").status_code, 400)
        self.assertEqual(self.report(self.author, self.public_id, responder).status_code, 403)
        self.assertEqual(responder.calls, [])

    def test_a_set_nobody_can_see_cannot_be_reported(self):
        self.author.post(f"/api/community/sets/{self.public_id}/unpublish")
        self.assertEqual(self.report(self.reader, self.public_id, Responder()).status_code, 404)

    def test_reports_are_not_readable_by_ordinary_users(self):
        self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT), entry=1)
        for client in (self.reader, self.author):
            self.assertEqual(client.get("/api/moderation/reports").status_code, 404)
            self.assertEqual(client.post(f"/api/moderation/reports/{self.public_id}/action",
                                         json={"action": "dismiss"}).status_code, 404)
        anonymous = application.app.test_client()
        self.assertIn(anonymous.get("/api/moderation/reports").status_code, {302, 401})
        # The author sees what was found, never who reported it.
        state = self.author.get(f"/api/community/sets/{self.public_id}/review").get_json()
        self.assertEqual(len(state["reader_findings"]), 1)
        self.assertNotIn("bob", json.dumps(state))

    def test_moderator_access_still_needs_the_allowlist(self):
        application.app.config["COMMUNITY_MODERATORS"] = set()
        self.assertEqual(self.moderator.get("/api/moderation/reports").status_code, 404)
        application.app.config["COMMUNITY_MODERATORS"] = {"mod"}
        self.assertEqual(self.moderator.get("/api/moderation/reports").status_code, 200)

    def test_duplicate_and_excessive_reports_are_controlled(self):
        responder = Responder(report_review=VALID_TRANSLATION_REPORT)
        self.assertEqual(self.report(self.reader, self.public_id, responder).status_code, 201)
        self.assertEqual(self.report(self.reader, self.public_id, responder, reason="spam").status_code, 200)
        self.assertEqual(len(self.rows(application.ContentReport)), 1)
        self.assertEqual(responder.count("report_review"), 1)
        application.app.config["MODERATION_MAX_REPORTS_PER_USER_DAY"] = 1
        _other_set, other_public = self.published()
        self.assertEqual(self.report(self.reader, other_public, responder).status_code, 429)

    def test_many_reports_about_the_same_thing_cost_one_review(self):
        responder = Responder(report_review=VALID_TRANSLATION_REPORT)
        for name in ("cara", "dan", "eve"):
            self.report(self.register(name), self.public_id, responder, entry=1)
        self.assertEqual(responder.count("report_review"), 1)
        reviews = {row.report_review_id for row in self.rows(application.ContentReport)}
        self.assertEqual(len(reviews), 1)
        self.assertEqual(len(self.rows(application.AdminNotification)), 1)

    def test_reports_do_not_penalize_the_author_or_hide_the_set(self):
        before = self.public_set(self.public_id).ranking_score
        for name in ("cara", "dan", "eve", "finn", "gus", "hal"):
            self.report(self.register(name), self.public_id,
                        Responder(report_review=VALID_TRANSLATION_REPORT), reason="incorrect")
        after = self.public_set(self.public_id)
        self.assertEqual((after.status, after.moderation_decision), ("approved", "allow"))
        self.assertEqual(after.ranking_score, before)
        self.assertEqual(self.library_size(), 1)
        self.assertEqual(self.reader.get(f"/api/community/sets/{self.public_id}").status_code, 200)

    def test_editing_the_set_makes_old_findings_stale(self):
        self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT), entry=1)
        self.author.put(f"/api/flashcards/sets/{self.set_id}", json={"cards": [
            {**VOCABULARY[0], "back": "die Stufe"}, *VOCABULARY[1:]]})
        with patch.object(application, "create_response", side_effect=Responder(content_moderation=ALLOW)):
            self.author.post(f"/api/community/sets/{self.public_id}/resubmit")
        state = self.author.get(f"/api/community/sets/{self.public_id}/review").get_json()
        self.assertEqual(state["reader_findings"], [])
        summary = self.moderator.get("/api/moderation/reports").get_json()["sets"][0]
        self.assertFalse(summary["reviews"][0]["current"])
        # A new report on the edited content is a new question and gets a new review.
        fresh = Responder(report_review=UNSUPPORTED_REPORT)
        self.report(self.register("cara"), self.public_id, fresh, entry=1)
        self.assertEqual(fresh.count("report_review"), 1)


# ------------------------------------------------------------------ re-review


class ReReviewTests(CommunityTestCase):
    def setUp(self):
        super().setUp()
        self.set_id, self.public_id = self.published()
        self.reader = self.register("bob")

    def review_of_only_report(self):
        [review] = self.rows(application.ReportReview)
        return review

    def test_a_genuine_translation_error_is_confirmed_and_highlighted(self):
        self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT), entry=1)
        self.assertEqual(self.review_of_only_report().outcome, "report_valid")
        findings = self.author.get(f"/api/community/sets/{self.public_id}/review").get_json()["reader_findings"]
        self.assertEqual((findings[0]["entry"], findings[0]["text"], findings[0]["suggestion"]),
                         (1, "der Marsch", "die Stufe"))
        # Nothing was changed or hidden by the finding itself.
        detail = self.reader.get(f"/api/community/sets/{self.public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "der Marsch")

    def test_an_unsupported_report_is_dismissed_by_the_review_but_kept_for_a_person(self):
        self.report(self.reader, self.public_id, Responder(report_review=UNSUPPORTED_REPORT), entry=2)
        self.assertEqual(self.review_of_only_report().outcome, "report_not_supported")
        [report] = self.rows(application.ContentReport)
        self.assertEqual(report.status, "open")
        self.assertEqual([row.kind for row in self.rows(application.AdminNotification)], ["report_received"])

    def test_ambiguous_findings_go_to_a_person(self):
        self.report(self.reader, self.public_id, Responder(report_review=POSSIBLE_REPORT), reason="misleading")
        review = self.review_of_only_report()
        self.assertEqual((review.outcome, review.human_review_recommended), ("possible_issue", True))
        self.assertEqual([row.kind for row in self.rows(application.AdminNotification)], ["possible_issue"])

    def test_a_valid_finding_that_quotes_nothing_real_is_only_a_possible_issue(self):
        invented = {**VALID_TRANSLATION_REPORT, "affected": {"entry": 1, "side": "back", "text": "der Hund"}}
        self.report(self.reader, self.public_id, Responder(report_review=invented), entry=1)
        self.assertEqual(self.review_of_only_report().outcome, "possible_issue")

    def test_invalid_model_output_fails_safely_and_keeps_the_report(self):
        for bad in ("{not json", {"outcome": "maybe", "explanation": "x"}):
            with self.subTest(bad=bad):
                self.setUp()
                self.report(self.reader, self.public_id, Responder(report_review=bad), entry=1)
                review = self.review_of_only_report()
                self.assertEqual((review.status, review.outcome), ("failed", "review_failed"))
                [report] = self.rows(application.ContentReport)
                self.assertEqual(report.status, "open")
                self.assertEqual(self.public_set(self.public_id).status, "approved")

    def test_a_failed_review_is_retried_without_losing_the_report(self):
        self.report(self.reader, self.public_id, Responder(report_review=provider_down()), entry=1)
        self.assertEqual(self.review_of_only_report().status, "failed")
        [report] = self.rows(application.ContentReport)
        with patch.object(application, "create_response", side_effect=Responder(report_review=VALID_TRANSLATION_REPORT)):
            with application.app.test_request_context():
                self.assertEqual(application.review_report(report.id), "report_valid")
        review = self.review_of_only_report()
        self.assertEqual((review.status, review.attempts), ("done", 2))

    def test_repeated_failures_stop_and_alert(self):
        self.report(self.reader, self.public_id, Responder(report_review=provider_down()), entry=1)
        [report] = self.rows(application.ContentReport)
        down = Responder(report_review=provider_down())
        for _ in range(4):
            with patch.object(application, "create_response", side_effect=down):
                with application.app.test_request_context():
                    application.review_report(report.id)
        self.assertEqual(self.review_of_only_report().attempts, 3)
        self.assertEqual(down.count("report_review"), 2, "retries must stop at the attempt limit")
        self.assertIn("review_failed", [row.kind for row in self.rows(application.AdminNotification)])

    def test_the_per_set_limit_caps_paid_reviews(self):
        application.app.config["REPORT_REVIEW_MAX_PER_SET_DAY"] = 1
        responder = Responder(report_review=VALID_TRANSLATION_REPORT)
        self.report(self.reader, self.public_id, responder, entry=1)
        self.report(self.register("cara"), self.public_id, responder, entry=2)
        self.assertEqual(responder.count("report_review"), 1)
        failures = [row.failure for row in self.rows(application.ReportReview)]
        self.assertIn("re-review limit reached", failures)

    def test_a_running_review_is_joined_not_repeated(self):
        responder = Responder(report_review=VALID_TRANSLATION_REPORT)
        with application.app.test_request_context():
            version = application.active_publication_version(
                application.db.session.get(application.PublicFlashcardSet, self.public_id))
            key = report_reviews.review_input_key(
                version.content_hash, "quality", 1, application.PROMPT_VERSIONS["report_review"])
            application.db.session.add(application.ReportReview(
                public_set_id=self.public_id, publication_version_id=version.id,
                content_version=version.version, content_hash=version.content_hash,
                input_key=key, track="quality", report_reason="incorrect_translation",
                entry_index=1, status="running"))
            application.db.session.commit()
        self.report(self.reader, self.public_id, responder, entry=1)
        self.assertEqual(responder.calls, [])
        self.assertEqual(len(self.rows(application.ReportReview)), 1)

    def test_concurrent_reports_trigger_one_review(self):
        cara = self.register("cara")
        with application.app.test_request_context():
            ids = []
            for user in ("bob", "cara"):
                account = application.db.session.scalar(application.db.select(application.User).where(
                    application.User.username == user))
                report = application.ContentReport(
                    public_set_id=self.public_id, reporter_id=account.id,
                    reason="incorrect_translation", entry_index=1, status="open")
                application.db.session.add(report)
                application.db.session.commit()
                ids.append(report.id)
        del cara
        responder = Responder(report_review=VALID_TRANSLATION_REPORT)
        responder.delay = 0.4
        results = []

        def run(report_id):
            with application.app.app_context():
                with patch.object(application, "create_response", side_effect=responder):
                    with application.app.test_request_context():
                        results.append(application.review_report(report_id))
                application.db.session.remove()

        threads = [threading.Thread(target=run, args=(report_id,)) for report_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(responder.count("report_review"), 1, results)
        self.assertEqual(len(self.rows(application.ReportReview)), 1)
        self.assertEqual(len({row.report_review_id for row in self.rows(application.ContentReport)}), 1)


class SafetyReReviewTests(CommunityTestCase):
    def setUp(self):
        super().setUp()
        # The cheap pass missed the insult at publication time.
        self.set_id, self.public_id = self.published(Responder(content_moderation=ALLOW), cards=INSULT_CARDS)
        self.reader = self.register("bob")

    def test_a_clear_violation_is_quarantined_for_a_person_not_deleted(self):
        responder = Responder(content_moderation=REJECT_HARASSMENT)
        self.report(self.reader, self.public_id, responder, reason="inappropriate")
        self.assertEqual(responder.calls, ["content_moderation"])
        review = self.rows(application.ReportReview)[0]
        self.assertEqual((review.outcome, review.restricted), ("report_valid", True))
        public_set = self.public_set(self.public_id)
        self.assertEqual((public_set.status, public_set.moderation_decision),
                         ("pending_manual_review", "review"))
        self.assertEqual(self.library_size(), 0)
        queue = self.moderator.get("/api/moderation/queue").get_json()["records"]
        self.assertEqual(len(queue), 1)
        self.assertEqual([row.kind for row in self.rows(application.AdminNotification)], ["safety_review"])
        # A person clears or confirms it; nothing was rejected outright.
        decided = self.moderator.post(f"/api/moderation/records/{queue[0]['id']}/decide",
                                      json={"decision": "allow"})
        self.assertEqual(decided.status_code, 200)
        self.assertEqual(self.library_size(), 1)

    def test_an_ambiguous_safety_finding_keeps_the_set_up_and_asks_a_person(self):
        self.report(self.reader, self.public_id, Responder(content_moderation=SAFETY_REVIEW), reason="inappropriate")
        review = self.rows(application.ReportReview)[0]
        self.assertEqual((review.outcome, review.restricted), ("possible_issue", False))
        self.assertEqual(self.library_size(), 1)
        queue = self.moderator.get("/api/moderation/queue").get_json()["records"]
        self.assertEqual(len(queue), 1)
        # The author is not told the set is waiting: it was never taken down.
        state = self.author.get(f"/api/community/sets/{self.public_id}/review").get_json()
        self.assertEqual(state["moderation"]["decision"], "allow")

    def test_an_unsupported_safety_report_changes_nothing(self):
        self.report(self.reader, self.public_id, Responder(content_moderation=ALLOW), reason="inappropriate")
        self.assertEqual(self.rows(application.ReportReview)[0].outcome, "report_not_supported")
        self.assertEqual(self.library_size(), 1)


# ------------------------------------------------------------------ email


class EmailTests(CommunityTestCase):
    def setUp(self):
        super().setUp()
        self.set_id, self.public_id = self.published()
        self.reader = self.register("bob")

    def configure_mail(self, **extra):
        application.app.config.update(
            MAIL_SMTP_HOST="smtp.example.com", MAIL_SMTP_PORT="587", MAIL_FROM="alerts@example.com",
            MAIL_SMTP_USERNAME="alerts@example.com", MAIL_SMTP_PASSWORD="s3cret-pass",
            MAIL_SMTP_SECURITY="starttls", **extra)

    def smtp(self, **behaviour):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.send_message.return_value = {}
        for name, value in behaviour.items():
            getattr(connection, name).side_effect = value
        return patch("learnova.notifications.mailer.smtplib.SMTP", return_value=connection), connection

    def test_an_alert_is_emailed_to_the_configured_recipient(self):
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT),
                        entry=1, detail="my private note")
        [alert] = self.rows(application.AdminNotification)
        self.assertEqual((alert.status, alert.recipient, alert.attempts), ("sent", ALERT_EMAIL, 1))
        message = connection.send_message.call_args.args[0]
        self.assertEqual(message["To"], ALERT_EMAIL)
        self.assertIn("Learnova", message["Subject"])
        body = message.get_content()
        for expected in ("Unité 3", f"#{self.public_id}", "Flashcard set", "Incorrect translation",
                         "der Marsch", "die Stufe", "Human review required: no", "/internal/moderation"):
            self.assertIn(expected, body)
        connection.starttls.assert_called_once()
        connection.login.assert_called_once_with("alerts@example.com", "s3cret-pass")

    def test_private_details_and_secrets_never_reach_the_email(self):
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT),
                        entry=1, detail="my private note")
        [alert] = self.rows(application.AdminNotification)
        text = alert.subject + alert.body + connection.send_message.call_args.args[0].as_string()
        for secret in ("bob", "bob@example.com", "my private note", "s3cret-pass", "alice@example.com"):
            self.assertNotIn(secret, text)
        self.assertNotIn("s3cret-pass", repr(notifications.mail_settings(application.app.config)))

    def test_missing_configuration_keeps_the_alert_and_sends_nothing(self):
        patcher, connection = self.smtp()
        with patcher as factory:
            self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT), entry=1)
        factory.assert_not_called()
        [alert] = self.rows(application.AdminNotification)
        self.assertEqual(alert.status, "not_configured")
        self.assertEqual(len(self.rows(application.ContentReport)), 1)
        # Configured later, the stored alert goes out.
        self.configure_mail()
        with patcher:
            with application.app.test_request_context():
                application.deliver_due_notifications()
        self.assertEqual(self.rows(application.AdminNotification)[0].status, "sent")

    def test_a_failed_delivery_keeps_the_report_and_is_retried_a_bounded_number_of_times(self):
        self.configure_mail(MAIL_MAX_ATTEMPTS=2)
        patcher, connection = self.smtp(send_message=smtplib.SMTPServerDisconnected("gone"))
        with patcher:
            self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT), entry=1)
            [alert] = self.rows(application.AdminNotification)
            self.assertEqual((alert.status, alert.attempts), ("retry", 1))
            with application.app.test_request_context():
                application.db.session.execute(application.update(application.AdminNotification).values(
                    next_attempt_at=None))
                application.db.session.commit()
                for _ in range(3):
                    application.deliver_due_notifications()
        [alert] = self.rows(application.AdminNotification)
        self.assertEqual((alert.status, alert.attempts), ("failed", 2))
        self.assertEqual(connection.send_message.call_count, 2)
        self.assertEqual(len(self.rows(application.ContentReport)), 1)

    def test_the_same_issue_is_emailed_once(self):
        self.configure_mail()
        patcher, connection = self.smtp()
        with patcher:
            for name in ("cara", "dan"):
                self.report(self.register(name), self.public_id,
                            Responder(report_review=VALID_TRANSLATION_REPORT), entry=1)
        self.assertEqual(connection.send_message.call_count, 1)


class MailerTests(unittest.TestCase):
    SETTINGS = notifications.MailSettings(
        host="smtp.example.com", port=587, sender="alerts@example.com",
        username="alerts@example.com", password="s3cret-pass")

    def send(self, settings=None, **behaviour):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.send_message.return_value = {}
        for name, value in behaviour.items():
            setattr(getattr(connection, name), "side_effect" if isinstance(value, Exception) else "return_value", value)
        factory = MagicMock(return_value=connection)
        result = notifications.send_email(settings or self.SETTINGS, ALERT_EMAIL, "Subject", "Body",
                                          smtp_factory=factory)
        return result, factory

    def test_only_server_acceptance_counts_as_sent(self):
        self.assertEqual(self.send()[0].status, "sent")
        self.assertEqual(self.send(send_message={ALERT_EMAIL: (550, b"no")})[0].status, "failed")

    def test_temporary_and_permanent_failures_are_told_apart(self):
        self.assertEqual(self.send(send_message=smtplib.SMTPResponseException(451, b"later"))[0].status, "retry")
        self.assertEqual(self.send(send_message=smtplib.SMTPResponseException(554, b"no"))[0].status, "failed")
        auth = self.send(login=smtplib.SMTPAuthenticationError(535, b"bad s3cret-pass"))[0]
        self.assertEqual(auth.status, "failed")
        self.assertNotIn("s3cret-pass", auth.detail)
        self.assertEqual(self.send(starttls=smtplib.SMTPNotSupportedError("no tls"))[0].status, "failed")
        self.assertEqual(self.send(starttls=ConnectionResetError())[0].status, "retry")

    def test_credentials_are_never_sent_without_tls(self):
        plain = notifications.MailSettings(host="smtp.example.com", port=25, sender="a@example.com",
                                           username="a@example.com", password="pw", security="none")
        result, factory = self.send(plain)
        self.assertEqual(result.status, "failed")
        factory.assert_not_called()

    def test_not_configured_is_reported_as_such(self):
        self.assertIsNone(notifications.mail_settings({"MAIL_SMTP_HOST": "", "MAIL_FROM": "a@b.c"}))
        self.assertEqual(notifications.send_email(None, ALERT_EMAIL, "s", "b").status, "not_configured")


# ------------------------------------------------------------------ administrator


class AdministratorTests(CommunityTestCase):
    def setUp(self):
        super().setUp()
        self.set_id, self.public_id = self.published()
        self.reader = self.register("bob")
        self.report(self.reader, self.public_id, Responder(report_review=VALID_TRANSLATION_REPORT),
                    entry=1, detail="Marsch is wrong")

    def act(self, responder=None, **body):
        with patch.object(application, "create_response", side_effect=responder or Responder()):
            return self.moderator.post(f"/api/moderation/reports/{self.public_id}/action", json=body)

    def test_the_dashboard_shows_reports_findings_alerts_and_history(self):
        data = self.moderator.get("/api/moderation/reports").get_json()
        [group] = data["sets"]
        self.assertEqual(group["reports"][0]["reporter"], "bob")
        self.assertEqual(group["reports"][0]["entry"]["back"], "der Marsch")
        self.assertEqual(group["reviews"][0]["suggested_correction"], "die Stufe")
        self.assertTrue(group["reviews"][0]["current"])
        self.assertEqual(group["notifications"][0]["status"], "not_configured")
        self.assertEqual([event["action"] for event in group["history"]][-1], "report_filed")
        self.assertFalse(data["mail_configured"])
        self.assertEqual(data["alert_recipient"], ALERT_EMAIL)

    def test_dismissing_is_recorded_in_the_audit_trail(self):
        self.assertEqual(self.act(action="dismiss", note="Both translations are fine").status_code, 200)
        [report] = self.rows(application.ContentReport)
        self.assertEqual((report.status, report.resolution), ("dismissed", "not_supported"))
        events = self.rows(application.ModerationAuditEvent, action="admin_dismiss")
        self.assertEqual(len(events), 1)
        self.assertIn("Both translations are fine", events[0].note)

    def test_correcting_publishes_a_new_version_and_leaves_the_private_set_alone(self):
        response = self.act(Responder(content_moderation=ALLOW), action="correct",
                            correction={"entry": 1, "side": "back", "text": "die Stufe"})
        self.assertEqual(response.status_code, 200, response.data)
        detail = self.reader.get(f"/api/community/sets/{self.public_id}").get_json()["set"]
        self.assertEqual((detail["cards"][0]["back"], detail["publication_version"]), ("die Stufe", 2))
        private = self.author.get(f"/api/flashcards/sets/{self.set_id}").get_json()["set"]["cards"]
        self.assertEqual(private[0]["back"], "der Marsch")
        self.assertEqual(self.rows(application.ContentReport)[0].resolution, "corrected")

    def test_a_correction_that_fails_the_safety_check_changes_nothing(self):
        response = self.act(Responder(content_moderation=REJECT_HARASSMENT), action="correct",
                            correction={"entry": 1, "side": "back", "text": "a worthless idiot"})
        self.assertEqual(response.status_code, 409)
        detail = self.reader.get(f"/api/community/sets/{self.public_id}").get_json()["set"]
        self.assertEqual(detail["cards"][0]["back"], "der Marsch")

    def test_restrict_and_restore(self):
        self.assertEqual(self.act(action="restrict").status_code, 200)
        self.assertEqual(self.library_size(), 0)
        self.assertEqual(self.act(action="restore").status_code, 200)
        self.assertEqual(self.library_size(), 1)
        actions = [event.action for event in self.rows(application.ModerationAuditEvent)]
        self.assertIn("admin_restrict", actions)
        self.assertIn("admin_restore", actions)

    def test_check_again_re_runs_the_review(self):
        responder = Responder(report_review=UNSUPPORTED_REPORT)
        self.assertEqual(self.act(responder, action="rereview").status_code, 200)
        self.assertEqual(responder.count("report_review"), 1)
        self.assertEqual(self.rows(application.ReportReview)[0].outcome, "report_not_supported")


class PureLogicTests(unittest.TestCase):
    def test_report_review_schema_is_strict(self):
        report_reviews.validate_report_review(VALID_TRANSLATION_REPORT)
        for broken in ({}, {**VALID_TRANSLATION_REPORT, "outcome": "review_failed"},
                       {**VALID_TRANSLATION_REPORT, "explanation": ""},
                       {**VALID_TRANSLATION_REPORT, "safety_concern": "no"},
                       {**VALID_TRANSLATION_REPORT, "affected": "card 1"},
                       {**VALID_TRANSLATION_REPORT, "confidence": 0.9, "issue_category": "vibes"}):
            with self.subTest(broken=broken), self.assertRaises(report_reviews.ReportReviewSchemaError):
                report_reviews.validate_report_review(broken)

    def test_review_tracks(self):
        self.assertEqual(report_reviews.review_track("incorrect_translation"), "quality")
        self.assertEqual(report_reviews.review_track("inappropriate"), "safety")
        self.assertEqual(report_reviews.review_track("spam"), "safety")
        self.assertEqual(report_reviews.review_track("copyright"), "human")

    def test_the_review_key_ignores_free_text_but_not_content(self):
        key = report_reviews.review_input_key("a" * 64, "quality", 1, "report_review:v1")
        self.assertEqual(key, report_reviews.review_input_key("a" * 64, "quality", 1, "report_review:v1"))
        self.assertNotEqual(key, report_reviews.review_input_key("b" * 64, "quality", 1, "report_review:v1"))
        self.assertNotEqual(key, report_reviews.review_input_key("a" * 64, "quality", 2, "report_review:v1"))

    def test_apply_suggestion_replaces_only_the_quoted_span(self):
        self.assertEqual(community.apply_suggestion("der Marsch (m.)", "der marsch", "die Stufe"), "die Stufe (m.)")
        self.assertIsNone(community.apply_suggestion("die Treppe", "der Marsch", "die Stufe"))

    def test_quality_never_blocks(self):
        for score in (0.5, 1.5, 2.9, 3.5, 4.5):
            self.assertEqual(community.publication_decision(score, [], moderated=True)["status"], "approved")


if __name__ == "__main__":
    unittest.main()

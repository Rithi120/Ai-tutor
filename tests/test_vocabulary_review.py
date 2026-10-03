"""The review step only appears when it has a question to ask.

The page it replaces showed, for every single entry, six input fields, a confidence
percentage, a page number, an English explanation sentence and three buttons - on top of
three list-wide buttons and a five-checkbox generation panel. A student who typed the
words themselves was being asked to confirm their own keystrokes against a source
document that does not exist.

What is kept: card generation still refuses to run on an entry nobody has confirmed. The
change is *which* entries that safeguard points at. Entries with an open question -
missing half, duplicate, a suggested correction, stray digits, or (for recognised text)
low OCR confidence - stay unconfirmed and are the only thing the review page lists.
Everything else is confirmed where the rule lives, on the server.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocabulary_review_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.translations import catalog  # noqa: E402
from learnova.vocabulary import service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def entry(**overrides):
    base = {
        "source_term": "mediathèque", "target_translation": "Mediathek",
        "status": "likely_valid", "confidence": 1.0,
    }
    return {**base, **overrides}


class NeedsAttentionTests(unittest.TestCase):
    """The one rule that decides whether a student is interrupted."""

    def test_a_plausible_typed_pair_needs_nothing(self):
        self.assertFalse(service.needs_attention(entry(), typed_by_hand=True))

    def test_a_plausible_recognized_pair_needs_nothing_when_confidence_is_high(self):
        self.assertFalse(service.needs_attention(entry(confidence=0.9), typed_by_hand=False))

    def test_low_confidence_only_matters_for_recognized_text(self):
        # There is no OCR confidence to speak of when the student typed it, so the same
        # entry is a question for a photo import and not a question for a typed one.
        low = entry(confidence=0.4)
        self.assertTrue(service.needs_attention(low, typed_by_hand=False))
        self.assertFalse(service.needs_attention(low, typed_by_hand=True))

    def test_the_threshold_is_the_one_the_old_accept_button_used(self):
        self.assertEqual(service.CONFIDENT_ENOUGH, 0.85)
        self.assertTrue(service.needs_attention(entry(confidence=0.84), typed_by_hand=False))
        self.assertFalse(service.needs_attention(entry(confidence=0.85), typed_by_hand=False))

    def test_a_missing_half_is_always_a_question(self):
        for typed in (True, False):
            self.assertTrue(service.needs_attention(
                entry(target_translation=""), typed_by_hand=typed))
            self.assertTrue(service.needs_attention(
                entry(source_term="   "), typed_by_hand=typed))

    def test_a_suggested_correction_is_always_a_question(self):
        self.assertTrue(service.needs_attention(
            entry(suggested_translation="Umwelt"), typed_by_hand=True))

    def test_every_non_quiet_status_is_a_question(self):
        for status in service.VALIDATION_STATUSES - service.QUIET_STATUSES:
            self.assertTrue(
                service.needs_attention(entry(status=status), typed_by_hand=True), status)

    def test_an_unparseable_confidence_is_treated_as_a_question(self):
        self.assertTrue(service.needs_attention(entry(confidence="?"), typed_by_hand=False))


class ReviewPlanTests(unittest.TestCase):
    def test_the_plan_names_the_flagged_rows_and_counts_the_rest(self):
        plan = service.review_plan(
            [entry(), entry(target_translation=""), entry()], typed_by_hand=True)
        self.assertEqual(plan, {
            "flagged": [1], "flagged_count": 1, "total": 3, "review_needed": True})

    def test_nothing_flagged_means_no_review(self):
        plan = service.review_plan([entry(), entry()], typed_by_hand=True)
        self.assertFalse(plan["review_needed"])

    def test_autoconfirm_leaves_the_flagged_rows_alone(self):
        entries = [entry(), entry(status="duplicate"), entry()]
        plan = service.autoconfirm(entries, typed_by_hand=True)
        self.assertEqual([item.get("user_confirmed", False) for item in entries],
                         [True, False, True])
        self.assertEqual(plan["flagged"], [1])

    def test_autoconfirm_never_unconfirms_what_a_student_already_confirmed(self):
        # The student can accept a flagged row as-is; re-validating must not undo that.
        entries = [entry(status="duplicate", user_confirmed=True)]
        service.autoconfirm(entries, typed_by_hand=True)
        self.assertTrue(entries[0]["user_confirmed"])

    def test_an_empty_import_asks_for_no_review(self):
        self.assertFalse(service.review_plan([], typed_by_hand=False)["review_needed"])


class ValidateEndpointTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, FEATURE_VOCABULARY_TRAINER=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "vocab", "email": "vocab@example.com",
            "password": "correct-horse-battery"})

    def manual_import(self, rows, key="manual-review"):
        created = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "manual", "source_language": "fr", "target_language": "de",
            "title": "Unité 3", "manual_entries": json.dumps(rows),
        }, headers={"Idempotency-Key": key})
        self.assertEqual(created.status_code, 201, created.data)
        import_id = created.get_json()["vocabulary_import"]["id"]
        self.client.post(f"/api/vocabulary/imports/{import_id}/extract")
        return import_id

    def test_clean_typed_words_skip_the_review_page(self):
        import_id = self.manual_import([
            {"source_term": "médiathèque", "target_translation": "Mediathek"},
            {"source_term": "une marche", "target_translation": "eine Stufe"},
        ])
        checked = self.client.post(
            f"/api/vocabulary/imports/{import_id}/validate").get_json()
        self.assertFalse(checked["review_needed"])
        self.assertEqual(checked["flagged_count"], 0)
        self.assertEqual(checked["entry_count"], 2)
        self.assertTrue(all(
            item["user_confirmed"] for item in checked["vocabulary_import"]["entries"]))

    def test_skipping_review_still_lets_cards_be_generated(self):
        # The end-to-end point of the change: without the review page, generation must
        # not trip over its own "confirm every entry first" guard.
        import_id = self.manual_import([
            {"source_term": "médiathèque", "target_translation": "Mediathek"}],
            key="manual-generate")
        self.client.post(f"/api/vocabulary/imports/{import_id}/validate")
        generated = self.client.post(
            f"/api/vocabulary/imports/{import_id}/generate",
            json={"directions": ["source_to_target"], "include_examples": True},
            headers={"Idempotency-Key": "manual-generate-cards"})
        self.assertEqual(generated.status_code, 200, generated.data)
        self.assertTrue(generated.get_json()["creator_url"])

    def test_a_typed_duplicate_still_stops_the_student(self):
        import_id = self.manual_import([
            {"source_term": "médiathèque", "target_translation": "Mediathek"},
            {"source_term": "médiathèque", "target_translation": "Mediathek"},
        ], key="manual-duplicate")
        checked = self.client.post(
            f"/api/vocabulary/imports/{import_id}/validate").get_json()
        self.assertTrue(checked["review_needed"])
        self.assertEqual(checked["flagged_count"], 1)
        self.assertIn("/review", checked["review_url"])
        confirmed = [item["user_confirmed"]
                     for item in checked["vocabulary_import"]["entries"]]
        self.assertEqual(confirmed, [True, False])

    def test_a_half_filled_row_is_reported_rather_than_only_vanishing(self):
        # The parser drops a word with no translation. The import form now refuses to
        # submit one, but a payload that skipped the form must not lose it in silence.
        parsed = service.parse_manual_entries([
            {"source_term": "médiathèque", "target_translation": "Mediathek"},
            {"source_term": "une marche", "target_translation": ""},
        ])
        self.assertEqual(len(parsed["entries"]), 1)
        self.assertEqual(parsed["unrecognized_lines"], ["une marche"])

    def test_a_wholly_blank_row_is_not_reported_as_a_problem(self):
        parsed = service.parse_manual_entries([
            {"source_term": "médiathèque", "target_translation": "Mediathek"},
            {"source_term": "", "target_translation": ""},
        ])
        self.assertEqual(parsed["unrecognized_lines"], [])

    def test_typed_words_are_not_sent_to_a_translation_provider(self):
        # Asking a model to second-guess words the student typed costs credits and
        # produces the "confirm this against the source" banner for a source that does
        # not exist.
        import_id = self.manual_import([
            {"source_term": "médiathèque", "target_translation": "Mediathek"}],
            key="manual-no-ai")
        with patch.object(application, "create_response") as provider:
            self.client.post(f"/api/vocabulary/imports/{import_id}/validate",
                             json={"ai_validation": True})
        provider.assert_not_called()

    def test_recognized_text_with_a_correction_still_gets_a_review(self):
        created = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "text", "source_language": "en", "target_language": "de",
            "text": "environment | Umweit\nhouse | das Haus",
        }, headers={"Idempotency-Key": "text-review"})
        import_id = created.get_json()["vocabulary_import"]["id"]
        self.client.post(f"/api/vocabulary/imports/{import_id}/extract")
        checked = self.client.post(
            f"/api/vocabulary/imports/{import_id}/validate",
            json={"ai_validation": False}).get_json()
        self.assertTrue(checked["review_needed"])
        entries = checked["vocabulary_import"]["entries"]
        self.assertEqual(entries[0]["status"], "likely_ocr_error")
        self.assertFalse(entries[0]["user_confirmed"])
        self.assertTrue(entries[1]["user_confirmed"])

    def test_every_entry_carries_an_explicit_confirmed_flag(self):
        # Never absent, so "is this still open?" cannot be a KeyError.
        import_id = self.manual_import([
            {"source_term": "médiathèque", "target_translation": "Mediathek"},
            {"source_term": "médiathèque", "target_translation": "Mediathek"},
        ], key="manual-flags")
        checked = self.client.post(
            f"/api/vocabulary/imports/{import_id}/validate").get_json()
        for item in checked["vocabulary_import"]["entries"]:
            self.assertIn("user_confirmed", item)


class ReviewPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = (ROOT / "templates/vocabulary_review.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "static/js/vocabulary.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static/css/vocabulary.css").read_text(encoding="utf-8")

    def test_the_clutter_is_gone(self):
        for removed in ("bulkAcceptVocabulary", "selectAllVocabulary", "deselectAllVocabulary",
                        "Accept high-confidence entries", "Select all", "Deselect all"):
            self.assertNotIn(removed, self.template, removed)
            self.assertNotIn(removed, self.js, removed)

    def test_the_default_row_is_two_boxes(self):
        pair = self.js.split('<div class="review-pair">', 1)[1].split("</div>", 1)[0]
        fields = re.findall(r'data-field="(\w+)"', pair)
        self.assertEqual(fields, ["source_term", "target_translation"])

    def test_the_other_fields_moved_under_a_disclosure_rather_than_disappearing(self):
        more = self.js.split("function moreHtml(", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("<details", more)
        for field in ("alternatives", "source_example_sentence", "part_of_speech"):
            self.assertIn(f'data-field="{field}"', more, field)
        for action in ("data-generate-example", "data-split", "data-merge"):
            self.assertIn(action, more, action)

    def test_the_confidence_percentage_and_english_explanation_are_not_shown(self):
        self.assertNotIn("validation_explanation", self.js)
        self.assertNotIn("Math.round(100 *", self.js)

    def test_the_reason_a_row_is_flagged_is_a_translated_status_not_raw_text(self):
        self.assertIn("t(`vocabularyStatus_${status}`)", self.js)

    def test_card_directions_are_folded_away_behind_a_summary(self):
        options = self.template.split('class="vocabulary-panel card-options"', 1)[1]
        self.assertIn("<summary>", options.split("</details>", 1)[0])
        self.assertEqual(self.template.count('name="direction"'), 4)

    def test_only_flagged_rows_are_listed_until_the_student_asks_for_the_rest(self):
        self.assertIn("showAllEntries", self.js)
        self.assertIn("showAllVocabulary", self.template)
        self.assertIn("hideQuiet", self.js)

    def test_answering_a_row_does_not_make_it_vanish_under_the_student(self):
        # Rows that had a question on arrival stay listed once answered, so confirming
        # the last one does not swap one row for forty.
        self.assertIn("const askedAbout = new WeakSet()", self.js)
        self.assertIn("askedAbout.has(entry)", self.js)
        self.assertIn("reviewEntries.filter(isFlagged).forEach(item => askedAbout.add(item))",
                      self.js)

    def test_the_way_back_to_the_other_words_never_disappears(self):
        render = self.js.split("function renderReview() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('toggle.classList.toggle("hidden", !isImport || !asked)', render)

    def test_a_row_the_student_adds_stays_visible_without_unhiding_everything(self):
        handler = self.js.split('#addVocabularyEntry").addEventListener', 1)[1].split("});", 1)[0]
        self.assertIn("askedAbout.add(fresh)", handler)
        self.assertNotIn("showAllEntries = true", handler)

    def test_the_visibility_bookkeeping_is_never_sent_to_the_server(self):
        # A flag written onto the entry would ride along in the PUT that saves it.
        self.assertNotIn("_wasFlagged", self.js)
        self.assertIn("WeakSet", self.js)

    def test_the_flag_rule_is_not_reimplemented_in_the_browser(self):
        # One rule, on the server. The client only reads the result of it.
        flagged = self.js.split("function isFlagged(entry) {", 1)[1].split("}", 1)[0]
        self.assertIn("user_confirmed", flagged)
        self.assertNotIn("confidence", flagged)

    def test_typing_in_a_flagged_row_answers_it_without_stealing_the_caret(self):
        handler = self.js.split('addEventListener("input"', 1)[1].split("});", 1)[0]
        self.assertIn("entry.user_confirmed = true", handler)
        self.assertNotIn("renderReview()", handler)

    def test_a_row_can_be_accepted_as_it_stands(self):
        self.assertIn("data-confirm", self.js)
        self.assertIn("vocabularyLooksRight", self.js)

    def test_the_two_boxes_stack_with_labels_on_a_phone(self):
        phone = self.css.split("@media (max-width: 700px)", 1)[1].split("}\n}", 1)[0]
        self.assertIn(".review-label { display: block; }", phone)
        self.assertIn(".review-arrow { display: none; }", phone)

    def test_the_review_colours_come_from_theme_tokens(self):
        review = self.css.split("/* ---- Review page", 1)[1].split("/* List page", 1)[0]
        self.assertNotRegex(review, r"#[0-9a-fA-F]{3,6}\b")


class ImportFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "static/js/vocabulary.js").read_text(encoding="utf-8")

    def test_the_client_only_opens_the_review_page_when_the_server_asks_for_it(self):
        submit = self.js.split('importForm.addEventListener("submit"', 1)[1].split("\n  });", 1)[0]
        self.assertIn("checked.review_needed", submit)
        self.assertIn("/generate", submit)
        self.assertIn("built.creator_url", submit)

    def test_the_skip_path_uses_the_same_defaults_the_options_panel_shows(self):
        submit = self.js.split('importForm.addEventListener("submit"', 1)[1].split("\n  });", 1)[0]
        self.assertIn('directions: ["source_to_target"], include_examples: true', submit)


class IncompleteRowTests(unittest.TestCase):
    """The check that used to happen on a later screen now happens next to the box."""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "static/js/vocabulary.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static/css/vocabulary.css").read_text(encoding="utf-8")

    def test_a_half_filled_row_blocks_the_submit(self):
        guard = self.js.split("function markIncompleteRows() {", 1)[1].split("\n  }", 1)[0]
        self.assertIn("Boolean(word) !== Boolean(translation)", guard)
        self.assertIn("is-incomplete", guard)

    def test_the_student_is_told_which_row_and_why(self):
        self.assertIn("vocabularyFillBothBoxes", self.js)
        self.assertIn('incomplete.querySelector("input")?.focus()', self.js)
        self.assertIn(".manual-row.is-incomplete input", self.css)

    def test_a_wholly_empty_row_is_not_treated_as_an_error(self):
        # Trailing blank rows are normal; only a half-filled one is a mistake.
        guard = self.js.split("function markIncompleteRows() {", 1)[1].split("\n  }", 1)[0]
        self.assertNotIn("!word || !translation", guard)


class TranslationTests(unittest.TestCase):
    NEW_KEYS = (
        "vocabularyBuildingCards", "vocabularyMore", "vocabularyLooksRight",
        "vocabularyNeedALook", "vocabularyWordCount", "vocabularyShowAll",
        "vocabularyShowOnlyFlagged", "vocabularyFillBothBoxes")

    def test_every_enabled_language_still_covers_the_catalogue(self):
        # SUPPORTED_LANGUAGES is computed from coverage, so a missing French string does
        # not fail loudly - it removes French from the app.
        self.assertEqual(catalog.SUPPORTED_LANGUAGES, ("en", "de", "fr", "es"))

    def test_the_new_frontend_strings_are_translated_everywhere(self):
        for language in catalog.SUPPORTED_LANGUAGES:
            strings = catalog.frontend_catalog(language)
            for key in self.NEW_KEYS:
                self.assertIn(key, strings, f"{key} missing from {language}")
                if language != "en":
                    self.assertNotEqual(
                        strings[key], catalog.FRONTEND_MESSAGES[key],
                        f"{key} is untranslated in {language}")

    def test_the_new_page_strings_are_translated_everywhere(self):
        for source in ("Check these words", "Edit vocabulary", "Card options", "Add word"):
            for language in ("de", "fr", "es"):
                self.assertNotEqual(
                    catalog.translate(source, language), source,
                    f"{source!r} is untranslated in {language}")

    def test_the_counting_strings_carry_their_placeholders_in_every_language(self):
        for language in catalog.SUPPORTED_LANGUAGES:
            strings = catalog.frontend_catalog(language)
            self.assertIn("{flagged}", strings["vocabularyNeedALook"], language)
            self.assertIn("{total}", strings["vocabularyNeedALook"], language)
            self.assertIn("{total}", strings["vocabularyShowAll"], language)

    def test_the_browser_can_fill_those_placeholders_in(self):
        source = (ROOT / "static/js/i18n.js").read_text(encoding="utf-8")
        self.assertIn("export function t(key, values)", source)
        self.assertIn(r"/\{(\w+)\}/g", source)


if __name__ == "__main__":
    unittest.main()

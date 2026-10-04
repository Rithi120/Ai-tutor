"""A textbook vocabulary page, read as the table it is.

The books look like this: the word with its pronunciation | the translation | an example
sentence with its own translation beneath. The import reads the page with the local OCR
engine and rebuilds those rows from where the lines sit; the structured vision reader
still exists for its contract tests but is no longer on the import path. The code under
test tidies the rows, labels words and sentences by the column they stood in, lets a
doubtful word be typed in or the page be photographed again, and keeps the three
practice scopes - words, sentences, both - honest about what they hold.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocabulary_textbook_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.contracts import validate_output  # noqa: E402
from learnova.ai_services.prompts import PROMPT_VERSIONS  # noqa: E402
from learnova.vocabulary import service  # noqa: E402
from learnova.ocr import local as local_ocr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VERSION = PROMPT_VERSIONS["vocabulary_page_extraction"]


def row(term: str, translation: str, examples=(), confidence: Any = 0.95, phonetic="", note="") -> dict[str, Any]:
    return {"term": term, "phonetic": phonetic, "translation": translation, "note": note,
            "examples": [{"sentence": sentence, "translation": translated}
                         for sentence, translated in examples],
            "confidence": confidence}


PAGE = {
    "rows": [
        row("à travers [atʁavɛʁ]", "durch",
            [("Il marche à travers le village.", "Er geht durch das Dorf.")]),
        row("la boulangerie", "die Bäckerei",
            [("La boulangerie ouvre à sept heures.", "Die Bäckerei öffnet um sieben Uhr."),
             ("Je vais à la boulangerie.", "")], confidence=0.9, phonetic="bulɑ̃ʒʁi"),
        row("Comment ça va ?", "Wie geht es dir?", confidence=0.9),
    ],
    "source_language": "fr", "target_language": "de",
}


class RowsToEntriesTests(unittest.TestCase):
    def entries(self, page=PAGE):
        return service.rows_to_entries(page, page_number=7)["entries"]

    def words(self, page=PAGE):
        return [entry for entry in self.entries(page) if entry["entry_kind"] != "sentence"]

    def sentences(self, page=PAGE):
        return [entry for entry in self.entries(page) if entry["entry_kind"] == "sentence"]

    def test_the_pronunciation_is_separated_from_the_word(self):
        first = self.words()[0]
        self.assertEqual(first["source_term"], "à travers")
        self.assertEqual(first["phonetic"], "atʁavɛʁ")
        self.assertEqual(self.words()[1]["phonetic"], "bulɑ̃ʒʁi", "a phonetic the reader already separated is kept")

    def test_the_first_example_is_attached_to_the_word(self):
        first = self.words()[0]
        self.assertEqual(first["source_example_sentence"], "Il marche à travers le village.")
        self.assertEqual(first["target_example_translation"], "Er geht durch das Dorf.")
        self.assertEqual(first["page_number"], 7)
        self.assertEqual(first["origin"], service.TEXTBOOK_ORIGIN)

    def test_every_translated_example_is_also_a_sentence_entry(self):
        sentences = self.sentences()
        self.assertEqual([entry["source_term"] for entry in sentences],
                         ["Il marche à travers le village.", "La boulangerie ouvre à sept heures."],
                         "the example without a translation cannot be practised, so it is not an entry")
        self.assertEqual(sentences[0]["target_translation"], "Er geht durch das Dorf.")
        self.assertEqual(sentences[0]["example_of"], "à travers")
        self.assertEqual(sentences[1]["example_of"], "la boulangerie")
        # Right after its word, so the page order survives into the list.
        self.assertEqual([entry["source_term"] for entry in self.entries()][:2],
                         ["à travers", "Il marche à travers le village."])

    def test_the_word_column_never_yields_a_sentence(self):
        self.assertEqual(service.text_kind("Comment ça va ?"), "sentence", "the heuristic alone would say so")
        self.assertEqual(self.words()[2]["entry_kind"], "phrase", "but the column says it is something you learn")

    def test_an_unreadable_word_is_kept_so_the_student_can_type_it(self):
        entries = self.entries({"rows": [row("", "die Bäckerei", confidence=0.4)]})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["source_term"], "")
        checked = service.validate_entry(entries[0], "fr", "de")
        self.assertEqual(checked["status"], "unrecognized")
        self.assertTrue(service.needs_attention(checked, typed_by_hand=False))

    def test_a_row_with_nothing_readable_is_skipped(self):
        self.assertEqual(self.entries({"rows": [row("", ""), "not a row", None]}), [])
        self.assertEqual(self.entries({"rows": "nonsense"}), [])
        self.assertEqual(self.entries(None), [])

    def test_confidence_is_clamped_and_garbage_defaults(self):
        entries = self.entries({"rows": [row("a", "b", confidence=1.7), row("c", "d", confidence="x")]})
        self.assertEqual([entry["confidence"] for entry in entries], [1.0, 0.5])

    def test_an_example_that_belongs_to_a_neighbour_lowers_confidence(self):
        entries = self.words({"rows": [
            row("la boulangerie", "die Bäckerei", [("Il marche à travers le village.", "Er geht durch das Dorf.")]),
            row("à travers", "durch"),
        ]})
        self.assertLess(entries[0]["confidence"], service.CONFIDENT_ENOUGH)
        self.assertIn("neighbouring", entries[0]["warnings"][0])
        self.assertEqual(entries[1]["confidence"], 0.95)

    def test_an_irregular_verb_is_not_punished_for_not_containing_itself(self):
        entries = self.words({"rows": [
            row("aller", "gehen", [("Je vais à Paris.", "Ich fahre nach Paris.")]),
            row("la gare", "der Bahnhof"),
        ]})
        self.assertEqual(entries[0]["confidence"], 0.95, "failing to match is no evidence; matching another word is")

    def test_validation_keeps_the_column_kind_for_a_short_sentence(self):
        from_table = self.sentences({"rows": [row("pleuvoir", "regnen", [("Il pleut.", "Es regnet.")])]})[0]
        self.assertEqual(service.validate_entry(from_table, "fr", "de")["entry_kind"], "sentence")
        from_a_line = service.parse_vocabulary_text("Il pleut. | Es regnet.")["entries"][0]
        self.assertEqual(service.validate_entry(from_a_line, "fr", "de")["entry_kind"], "phrase",
                         "with no column to go by, the heuristic decides")

    def test_who_gets_a_second_opinion(self):
        word, sentence = self.entries({"rows": [row("à travers", "durch", [("Il marche à travers le village.", "Er geht.")])]})
        self.assertFalse(service.wants_second_opinion(word), "printed next to the word; nothing to arbitrate")
        self.assertFalse(service.wants_second_opinion(sentence), "a sentence has many right translations")
        unsure = self.entries({"rows": [row("le vilage", "das Dorf", confidence=0.5)]})[0]
        self.assertTrue(service.wants_second_opinion(unsure))
        ocr_line = service.parse_vocabulary_text("le village | das Dorf")["entries"][0]
        self.assertTrue(service.wants_second_opinion(ocr_line))

    def test_folded_examples_also_become_sentence_entries(self):
        parsed = service.parse_vocabulary_text(
            "la médiathèque | die Mediathek\nJe vais à la médiathèque. | Ich gehe in die Mediathek.\n"
            "le bâtiment | das Gebäude | Le bâtiment est ancien.")["entries"]
        self.assertEqual(len(parsed), 2, "the sentence was folded into the word above")
        expanded = service.expand_examples(parsed)
        self.assertEqual([entry["source_term"] for entry in expanded],
                         ["la médiathèque", "Je vais à la médiathèque.", "le bâtiment"],
                         "an example without a translation stays only an example")
        self.assertEqual(expanded[1]["entry_kind"], "sentence")
        self.assertEqual(service.validate_entry(expanded[1], "fr", "de")["entry_kind"], "sentence")

    def test_the_prompt_names_both_languages_and_the_columns(self):
        prompt = service.page_reader_instructions("French", "German")
        for needle in ("French", "German", "left", "middle", "right", "phonetic", "Never guess"):
            self.assertIn(needle, prompt)


class RescanMergeTests(unittest.TestCase):
    def entry(self, term, translation, confirmed):
        return {"source_term": term, "target_translation": translation, "user_confirmed": confirmed,
                "entry_kind": "word", "included": True}

    def test_open_rows_are_replaced_settled_rows_and_typed_words_kept_new_finds_added(self):
        settled = self.entry("à travers", "durch", True)
        unreadable = self.entry("", "die Bäckerei", False)
        misread = self.entry("le vilage", "", False)
        typed = self.entry("le chat", "die Katze", True)
        fresh = [self.entry("à travers", "durch", False), self.entry("la boulangerie", "die Bäckerei", False),
                 self.entry("le village", "das Dorf", False), self.entry("le pain", "das Brot", False)]
        merged = service.merge_rescan([settled, unreadable, misread, typed], fresh)
        self.assertEqual([entry["source_term"] for entry in merged["entries"]],
                         ["à travers", "la boulangerie", "le vilage", "le chat", "le village", "le pain"])
        self.assertIs(merged["entries"][0], settled, "kept as the very object, not re-read")
        self.assertIs(merged["entries"][3], typed)
        self.assertEqual((merged["replaced"], merged["added"]), (1, 2))

    def test_an_open_row_is_matched_by_its_word_when_it_has_one(self):
        open_row = self.entry("le village", "", False)
        merged = service.merge_rescan([open_row], [self.entry("le village", "das Dorf", False)])
        self.assertEqual(merged["entries"][0]["target_translation"], "das Dorf")
        self.assertEqual(merged["replaced"], 1)

    def test_a_word_the_student_has_is_not_added_again_with_another_translation(self):
        merged = service.merge_rescan([self.entry("le chat", "die Katze", True)],
                                      [self.entry("le chat", "der Kater", False)])
        self.assertEqual(len(merged["entries"]), 1)
        self.assertEqual(merged["entries"][0]["target_translation"], "die Katze")
        self.assertEqual(merged["added"], 0)

    def test_incomplete_fresh_rows_replace_nothing(self):
        open_row = self.entry("", "die Bäckerei", False)
        merged = service.merge_rescan([open_row], [self.entry("", "die Bäckerei", False)])
        self.assertIs(merged["entries"][0], open_row)
        self.assertEqual((merged["replaced"], merged["added"]), (0, 0))


class PageContractTests(unittest.TestCase):
    def check(self, payload):
        validate_output("vocabulary_page_extraction", json.dumps(payload, ensure_ascii=False), VERSION, {})

    def test_the_sample_page_passes_and_an_empty_page_is_a_fact_not_a_failure(self):
        self.check(PAGE)
        self.check({"rows": []})

    def test_a_row_may_lack_one_side_but_not_both(self):
        self.check({"rows": [row("", "die Bäckerei")]})
        with self.assertRaises(ai_service.AIValidationError):
            self.check({"rows": [row("", "")]})

    def test_confidence_and_examples_are_checked(self):
        with self.assertRaises(ai_service.AIValidationError):
            self.check({"rows": [row("a", "b", confidence=1.5)]})
        with self.assertRaises(ai_service.AIValidationError):
            self.check({"rows": [{"term": "a", "translation": "b", "confidence": 0.9,
                                  "examples": [{"translation": "no sentence"}]}]})

    def test_the_task_is_registered_consistently(self):
        from learnova.ai_services import routing
        self.assertIn("vocabulary_page_extraction", ai_service.SUPPORTED_TASK_TYPES)
        self.assertIn("vocabulary_page_extraction", ai_service.DETERMINISTIC_TASKS, "the same page read twice costs once")
        self.assertEqual(routing.TASK_TIERS["vocabulary_page_extraction"], routing.Tier.vision)
        self.assertEqual(application.app.config["AI_VOCABULARY_PAGE_EXTRACTION_MAX_OUTPUT_TOKENS"],
                         ai_service.DEFAULT_OUTPUT_TOKEN_BUDGETS["vocabulary_page_extraction"])


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = json.dumps(payload, ensure_ascii=False)


def photo_bytes(shade="white"):
    image = Image.new("RGB", (800, 600), shade)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def ocr_line(text, y=0.1, x0=0.1, x1=0.9, confidence=0.98):
    from learnova.vocabulary import layout
    box = (x0, y, x1, y + 0.03)
    return layout.Line(text, box, confidence, layout.words_from_text(text, box, confidence))


def page_lines(page, confidences=None):
    """Lay the rows of a page out as the engine would read them: three columns.

    Word (with its pronunciation) on the left, translation in the middle, each example
    sentence on the right with its translation on the line beneath.
    """

    lines, y = [], 0.10
    for index, raw in enumerate(page["rows"]):
        confidence = (confidences or {}).get(index, 0.98)
        term = raw["term"] if "[" in raw["term"] or not raw.get("phonetic") else f"{raw['term']} [{raw['phonetic']}]"
        if raw["term"]:
            lines.append(ocr_line(term, y, 0.08, 0.26, confidence))
        if raw["translation"]:
            lines.append(ocr_line(raw["translation"], y, 0.30, 0.48, confidence))
        example_y = y
        for example in raw.get("examples", []):
            lines.append(ocr_line(example["sentence"], example_y, 0.52, 0.90, confidence))
            example_y += 0.035
            if example["translation"]:
                lines.append(ocr_line(example["translation"], example_y, 0.52, 0.90, confidence))
                example_y += 0.035
        y = max(y + 0.07, example_y + 0.02)
    return lines


def reading(lines):
    return local_ocr.LocalOcrResult(lines, 1200, 1600, "rapidocr", 420)


class TextbookImportTests(unittest.TestCase):
    """The whole import on the local path: the AI gateway is patched to fail if touched."""

    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_VOCABULARY_TRAINER=True,
            FEATURE_PRIVATE_FLASHCARDS=True, FEATURE_FLASHCARD_IMAGE_IMPORT=True,
            FEATURE_FLASHCARD_PDF_IMPORT=True, FEATURE_GAMIFICATION=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
            application.ensure_database()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "vocab", "email": "vocab@example.com",
            "password": "correct-horse-battery"})
        self.calls = []

    def no_model(self, **kwargs):
        self.calls.append(kwargs.get("task_type"))
        raise AssertionError(f"the import called the AI gateway for {kwargs.get('task_type')}")

    def answer_with(self, payloads):
        def respond(*, task_type, **_kwargs):
            self.calls.append(task_type)
            answer = payloads.get(task_type)
            if answer is None:
                raise AssertionError(f"unexpected AI task {task_type}")
            return FakeResponse(answer)
        return respond

    def upload(self, key="photo-one", shade="white"):
        response = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "file", "source_language": "fr", "target_language": "de",
            "title": "Vocabulaire M1", "file": (io.BytesIO(photo_bytes(shade)), "page.png"),
        }, content_type="multipart/form-data", headers={"Idempotency-Key": key})
        self.assertEqual(response.status_code, 201, response.data)
        return response.get_json()

    def extract(self, import_id, lines, *, engine_available=True):
        with patch.object(application, "create_response", self.no_model), \
                patch.object(local_ocr, "available", return_value=engine_available), \
                patch.object(local_ocr, "recognize", return_value=reading(lines)):
            return self.client.post(f"/api/vocabulary/imports/{import_id}/extract")

    def validate(self, import_id, body=None):
        # The second opinion is opt-in; the local path itself never asks a provider.
        with patch.object(application, "create_response", self.no_model):
            return self.client.post(f"/api/vocabulary/imports/{import_id}/validate",
                                    json=body or {"ai_validation": False})

    def test_a_photo_is_read_locally_and_a_clean_page_needs_no_review(self):
        created = self.upload()
        import_id = created["vocabulary_import"]["id"]
        extracted = self.extract(import_id, page_lines(PAGE))
        self.assertEqual(extracted.status_code, 200, extracted.data)
        self.assertEqual(self.calls, [], "no provider anywhere on the path")
        entries = extracted.get_json()["vocabulary_import"]["entries"]
        self.assertEqual([entry["entry_kind"] for entry in entries],
                         ["phrase", "sentence", "word", "sentence", "phrase"])
        checked = self.validate(import_id).get_json()
        self.assertFalse(checked["review_needed"], checked)
        self.assertEqual(checked["entry_count"], 5)
        self.assertTrue(checked["vocabulary_import"]["can_rescan"])

        generated = self.client.post(
            f"/api/vocabulary/imports/{import_id}/generate",
            json={"directions": ["source_to_target"], "include_examples": True},
            headers={"Idempotency-Key": "generate-one"})
        self.assertEqual(generated.status_code, 200, generated.data)
        list_id = generated.get_json()["list_id"]
        for scope, expected in (("words", 3), ("sentences", 2), ("all", 5)):
            items = self.client.get(
                f"/api/vocabulary/lists/{list_id}/practice?direction=source_to_target&scope={scope}"
            ).get_json()["items"]
            self.assertEqual(len(items), expected, scope)
        sentences = self.client.get(
            f"/api/vocabulary/lists/{list_id}/practice?direction=source_to_target&scope=sentences").get_json()
        self.assertEqual(sentences["items"][0]["prompt"], "Il marche à travers le village.")
        draft = self.client.get(f"/api/vocabulary/imports/{import_id}/draft").get_json()["draft"]
        word_card = next(card for card in draft["cards"] if card["front"] == "à travers")
        self.assertEqual(word_card["explanation"], "Il marche à travers le village.", "the example rides on the word card")

    def test_a_confident_textbook_row_is_not_sent_for_a_second_opinion(self):
        import_id = self.upload()["vocabulary_import"]["id"]
        page = {"rows": PAGE["rows"] + [row("le vilage", "das Dorf")]}
        self.extract(import_id, page_lines(page, confidences={3: 0.5}))
        self.calls.clear()
        asked = {}

        def respond(*, task_type, **kwargs):
            self.calls.append(task_type)
            asked.update(kwargs.get("validation_context") or {})
            return FakeResponse({"translations": ["das Dorf"]})
        with patch.object(application, "create_response", respond):
            checked = self.client.post(f"/api/vocabulary/imports/{import_id}/validate",
                                       json={"ai_validation": True}).get_json()
        self.assertEqual(self.calls, ["translation"], "the second opinion is opt-in and asked once")
        self.assertEqual(asked["texts"], ["le vilage"], "only the unsure row, never the confident ones or the sentences")
        self.assertTrue(checked["review_needed"])
        self.assertEqual(checked["flagged_count"], 1)

    def test_a_doubtful_word_sends_the_student_to_type_it_or_rescan(self):
        created = self.upload()
        import_id = created["vocabulary_import"]["id"]
        page = {"rows": [row("à travers", "durch"), row("le vilage", "das Dorf")]}
        self.extract(import_id, page_lines(page, confidences={1: 0.4}))
        checked = self.validate(import_id).get_json()
        self.assertTrue(checked["review_needed"])
        self.assertEqual(checked["flagged_count"], 1)
        page_html = self.client.get(created["review_url"])
        self.assertEqual(page_html.status_code, 200)
        html = page_html.get_data(as_text=True)
        self.assertIn("vocabularyRescanFile", html)
        self.assertIn("Rescan page", html)
        self.assertIn("vocabularyPhoto", html)

    def test_a_rescan_replaces_only_the_open_rows_and_keeps_typed_words(self):
        created = self.upload()
        import_id = created["vocabulary_import"]["id"]
        first_preview = created["vocabulary_import"]["preview_url"]
        first = {"rows": [row("à travers", "durch"), row("le vilage", "das Dorf")]}
        self.extract(import_id, page_lines(first, confidences={1: 0.4}))
        entries = self.validate(import_id).get_json()["vocabulary_import"]["entries"]
        entries.append({"source_term": "le chat", "target_translation": "die Katze", "user_confirmed": True,
                        "included": True, "status": "needs_review", "confidence": 1})
        self.assertEqual(self.client.put(f"/api/vocabulary/imports/{import_id}/entries",
                                         json={"entries": entries}).status_code, 200)
        self.calls.clear()
        second = {"rows": [row("à travers", "durch"), row("le village", "das Dorf")]}
        with patch.object(application, "create_response", self.no_model), \
                patch.object(local_ocr, "available", return_value=True), \
                patch.object(local_ocr, "recognize", return_value=reading(page_lines(second))):
            rescanned = self.client.post(f"/api/vocabulary/imports/{import_id}/rescan", data={
                "file": (io.BytesIO(photo_bytes("gray")), "page-again.png")}, content_type="multipart/form-data")
        self.assertEqual(rescanned.status_code, 200, rescanned.data)
        body = rescanned.get_json()
        self.assertEqual(self.calls, [])
        self.assertEqual((body["replaced"], body["added"]), (1, 0))
        self.assertFalse(body["review_needed"])
        self.assertEqual([entry["source_term"] for entry in body["vocabulary_import"]["entries"]],
                         ["à travers", "le village", "le chat"])
        self.assertTrue(all(entry["user_confirmed"] for entry in body["vocabulary_import"]["entries"]))
        self.assertNotEqual(body["vocabulary_import"]["preview_url"], first_preview)
        self.assertEqual(self.client.get(body["vocabulary_import"]["preview_url"]).status_code, 200)
        self.assertEqual(self.client.get(first_preview).status_code, 404, "the old photo is gone")

    def test_a_rescan_is_only_for_photo_imports(self):
        response = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "text", "source_language": "fr", "target_language": "de",
            "text": "le chat | die Katze"}, headers={"Idempotency-Key": "text-one"})
        import_id = response.get_json()["vocabulary_import"]["id"]
        rescanned = self.client.post(f"/api/vocabulary/imports/{import_id}/rescan", data={
            "file": (io.BytesIO(photo_bytes()), "page.png")}, content_type="multipart/form-data")
        self.assertEqual(rescanned.status_code, 400)
        self.assertEqual(rescanned.get_json()["code"], "rescan_not_possible")

    def test_when_the_page_is_no_table_the_lines_are_read_one_by_one(self):
        import_id = self.upload()["vocabulary_import"]["id"]
        single_column = [ocr_line("le village | das Dorf"),
                         ocr_line("Il habite dans le village. | Er wohnt im Dorf.", 0.14)]
        extracted = self.extract(import_id, single_column)
        self.assertEqual(extracted.status_code, 200, extracted.data)
        self.assertEqual(self.calls, [], "no provider, even when the page is no table")
        imported = extracted.get_json()["vocabulary_import"]
        self.assertEqual([entry["source_term"] for entry in imported["entries"]],
                         ["le village", "Il habite dans le village."])
        self.assertEqual(imported["entries"][1]["entry_kind"], "sentence")
        self.assertTrue(any("did not match a clear vocabulary table" in warning for warning in imported["warnings"]))

    def test_without_the_local_engine_the_import_says_so_instead_of_calling_a_model(self):
        import_id = self.upload()["vocabulary_import"]["id"]
        extracted = self.extract(import_id, [], engine_available=False)
        self.assertEqual(extracted.status_code, 200, extracted.data)
        self.assertEqual(self.calls, [])
        imported = extracted.get_json()["vocabulary_import"]
        self.assertEqual(imported["entries"], [])
        self.assertTrue(any("Local OCR is unavailable" in warning for warning in imported["warnings"]))

    def test_a_blank_photo_is_reported_not_sent_anywhere(self):
        import_id = self.upload()["vocabulary_import"]["id"]
        extracted = self.extract(import_id, [])
        self.assertEqual(extracted.status_code, 200)
        self.assertEqual(self.calls, [])
        self.assertTrue(any("No readable text" in warning
                            for warning in extracted.get_json()["vocabulary_import"]["warnings"]))

    def test_the_review_page_speaks_german(self):
        created = self.upload()
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(
                application.User).where(application.User.username == "vocab"))
            assert user is not None
            user.preferred_language = "de"
            application.db.session.commit()
        html = self.client.get(created["review_url"]).get_data(as_text=True)
        self.assertIn("Seite neu scannen", html)
        self.assertIn("Foto der Seite anzeigen", html)


class ReviewPageWiringTests(unittest.TestCase):
    """The rescan control, as the script and the stylesheet depend on it."""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "static/js/vocabulary.js").read_text(encoding="utf-8")
        cls.template = (ROOT / "templates/vocabulary_review.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "static/css/vocabulary.css").read_text(encoding="utf-8")

    def test_the_script_saves_first_then_rescans_then_adopts_the_merged_rows(self):
        rescan = self.js[self.js.index("async function rescanPage"):]
        self.assertLess(rescan.index("await saveReview()"), rescan.index("/rescan"))
        self.assertLess(rescan.index("/rescan"), rescan.index("adoptEntries(result.vocabulary_import.entries)"))
        self.assertIn('t("vocabularyRescanned", { replaced: result.replaced, added: result.added })', rescan)

    def test_the_control_is_a_file_input_the_phone_opens_as_camera_or_gallery(self):
        self.assertIn('id="vocabularyRescanFile" accept="image/*,.pdf"', self.template)
        self.assertNotIn("capture=", self.template, "the gallery must stay an option")
        self.assertIn(".visually-hidden-input", self.css)
        self.assertIn("#vocabularyRescanFile", self.js)

    def test_the_photo_is_shown_only_when_there_is_one(self):
        self.assertIn('id="vocabularyPhoto" hidden', self.template)
        self.assertIn("photo.hidden = !url", self.js)


if __name__ == "__main__":
    unittest.main()

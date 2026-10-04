"""A textbook vocabulary page rebuilt from where the words sit, with no model anywhere.

The fixture is the local engine's real reading of a photographed page (French textbook,
unit M1: word with pronunciation | German translation | examples with translations),
taken sideways on a phone. The rules under test turn those positioned lines back into
the table. They were written against this page and then checked against it: every pair
below is what the book prints.
"""

import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocabulary_table_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ocr import local as local_ocr  # noqa: E402
from learnova.vocabulary import layout, service  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "vocabulary_page_m1_lines.json"


def page_lines():
    return layout.lines_from_payload(json.loads(FIXTURE.read_text(encoding="utf-8")))


class RealPageTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = layout.table_rows(page_lines(), "fr", "de")
        cls.by_term = {row["term"].split(" [")[0]: row for row in cls.rows}

    def test_every_printed_word_pair_is_recovered(self):
        expected = {
            "protéger qn/qc": "jdn./etw. schützen",
            "un mineur/une mineure": "ein Minderjähriger/eine Minderjährige",
            "défendre qn/qc": "jdn./etw. verteidigen",
            "une objection": "ein Einwand",
            "par contre": "hingegen; dagegen",
            "un accord": "eine Übereinkunft",
            "à fond": "hier: voll und ganz (ugs.); gründlich",
            "un vieillard (péj.)": "ein alter Mann (abwertend)",
            "à travers": "durch",
            "écarter qc": "hier: etw. spreizen",
            "une dent": "ein Zahn",
            "approfondir qc": "etw. vertiefen",
            "Saint-Louis": "Stadt im Norden des Senegal",
            "bosser (fam.)": "schuften (ugs.); jobben (ugs.)",
            "un cybercafé": "ein Internetcafé",
        }
        for term, translation in expected.items():
            self.assertIn(term, self.by_term, term)
            self.assertEqual(self.by_term[term]["translation"], translation, term)
        self.assertEqual(len(self.rows), 16, [row["term"] for row in self.rows])

    def test_a_word_and_its_translation_printed_close_together_are_still_split(self):
        row = self.by_term["d'un côté, ..., de l’autre,"]
        self.assertEqual(row["translation"], "... einerseits ... , andererseits")

    def test_examples_are_paired_with_their_translations_even_when_they_wrap_past_the_next_word(self):
        row = self.by_term["d'un côté, ..., de l’autre,"]
        self.assertEqual(row["examples"], [{
            "sentence": "D'un côté, tu as raison, de l'autre, ce n'est pas aussi simple que ça.",
            "translation": "Einerseits hast du recht, andererseits ist es nicht ganz so einfach."}])
        self.assertEqual(self.by_term["par contre"]["examples"][0]["translation"],
                         "Ich finde dagegen, dass die Jugendlichen selbst entscheiden können.")
        self.assertEqual(self.by_term["à fond"]["examples"][0]["translation"],
                         "Sie kennt diese Geschichte in- und auswendig.")

    def test_equals_lines_become_a_pair_and_their_run_on_is_kept(self):
        self.assertEqual(self.by_term["défendre qn/qc"]["examples"],
                         [{"sentence": "défendre ses idées", "translation": "seine Ansichten vertreten"}])
        self.assertEqual(self.by_term["approfondir qc"]["examples"],
                         [{"sentence": "approfondir ses connaissances", "translation": "seine Kenntnisse vertiefen"}])

    def test_remarks_become_notes_not_translations(self):
        self.assertEqual(self.by_term["protéger qn/qc"]["note"], "protéger wird konjugiert wie manger.")
        self.assertEqual(self.by_term["approfondir qc"]["note"], "approfondir wird konjugiert wie finir.")
        self.assertIn("→ vieux, vieil, vieille (alt)", self.by_term["un vieillard (péj.)"]["note"])
        self.assertEqual(self.by_term["un vieillard (péj.)"]["examples"], [])

    def test_headings_page_numbers_hints_and_the_next_page_are_not_rows(self):
        terms = [row["term"] for row in self.rows]
        for noise in ("Vocabulaire", "cent-soixante-quatorze", "B7", "M1", "convenir", "A mon avis,"):
            self.assertNotIn(noise, terms)
        self.assertTrue(self.by_term["à fond"]["term"].startswith("à fond"), "the unit label B8 is stripped")
        for row in self.rows:
            for example in row["examples"]:
                self.assertNotIn("englisch:", example["sentence"])

    def test_the_pronunciation_stays_out_of_the_word(self):
        entries = service.rows_to_entries({"rows": self.rows}, 1)["entries"]
        words = {entry["source_term"] for entry in entries if entry["entry_kind"] != "sentence"}
        self.assertIn("protéger qn/qc", words)
        self.assertNotIn("protéger qn/qc [proteze]", words)
        self.assertGreaterEqual(sum(1 for entry in entries if entry["entry_kind"] == "sentence"), 10)
        self.assertTrue(all(float(entry["confidence"]) >= service.CONFIDENT_ENOUGH for entry in entries),
                        "printed text read by the engine needs no second opinion")

    def test_a_page_without_columns_is_not_a_table(self):
        single = [layout.Line("le village | das Dorf", (0.1, 0.1, 0.9, 0.13), 0.9),
                  layout.Line("la gare | der Bahnhof", (0.1, 0.14, 0.9, 0.17), 0.9)]
        self.assertEqual(layout.table_rows(single, "fr", "de"), [])

    def test_it_is_deterministic(self):
        self.assertEqual(layout.table_rows(page_lines(), "fr", "de"), self.rows)


def photo_bytes(shade="white"):
    image = Image.new("RGB", (800, 600), shade)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class LocalFirstImportTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(
            TESTING=True, FEATURE_VOCABULARY_TRAINER=True, FEATURE_PRIVATE_FLASHCARDS=True,
            FEATURE_FLASHCARD_IMAGE_IMPORT=True, FEATURE_FLASHCARD_PDF_IMPORT=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
            application.ensure_database()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "vocab", "email": "vocab@example.com",
                                            "password": "correct-horse-battery"})

    def test_a_photographed_textbook_page_becomes_cards_without_any_model_call(self):
        created = self.client.post("/api/vocabulary/imports", data={
            "source_kind": "file", "source_language": "fr", "target_language": "de",
            "file": (io.BytesIO(photo_bytes()), "page.png"),
        }, content_type="multipart/form-data", headers={"Idempotency-Key": "table-one"})
        import_id = created.get_json()["vocabulary_import"]["id"]
        calls = []

        def no_model(**kwargs):
            calls.append(kwargs.get("task_type"))
            raise AssertionError("the import must not need a model for a table it can read")
        reading = local_ocr.LocalOcrResult(page_lines(), 1200, 1600, "rapidocr", 2100, [], 90)
        with patch.object(application, "create_response", no_model), \
                patch.object(local_ocr, "available", return_value=True), \
                patch.object(local_ocr, "recognize", return_value=reading):
            extracted = self.client.post(f"/api/vocabulary/imports/{import_id}/extract")
            self.assertEqual(extracted.status_code, 200, extracted.data)
            checked = self.client.post(f"/api/vocabulary/imports/{import_id}/validate").get_json()
        self.assertEqual(calls, [])
        entries = extracted.get_json()["vocabulary_import"]["entries"]
        pairs = {(entry["source_term"], entry["target_translation"]) for entry in entries}
        self.assertIn(("à travers", "durch"), pairs)
        self.assertIn(("Il marche à travers le village.", "Er geht durch das Dorf."), pairs)
        self.assertIn(("Saint-Louis", "Stadt im Norden des Senegal"), pairs)
        self.assertFalse(checked["review_needed"], checked)
        generated = self.client.post(f"/api/vocabulary/imports/{import_id}/generate",
                                     json={"directions": ["source_to_target"], "include_examples": True},
                                     headers={"Idempotency-Key": "generate-table"})
        self.assertEqual(generated.status_code, 200, generated.data)
        list_id = generated.get_json()["list_id"]
        for scope, minimum in (("words", 15), ("sentences", 10)):
            items = self.client.get(
                f"/api/vocabulary/lists/{list_id}/practice?direction=source_to_target&scope={scope}").get_json()["items"]
            self.assertGreaterEqual(len(items), minimum, scope)


def rendered_page(lines, rotate=0):
    image = Image.new("RGB", (1400, 200 + 90 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    for index, text in enumerate(lines):
        draw.text((60, 60 + 90 * index), text, fill="black", font=font)
    if rotate:
        image = image.rotate(rotate, expand=True)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@unittest.skipUnless(local_ocr.available({"LOCAL_OCR_ENGINE": "auto"}), "rapidocr is not installed")
class RealEngineRotationTests(unittest.TestCase):
    LINES = ["La boulangerie ouvre à sept heures.", "Il marche à travers le village.",
             "Das Mädchen geht zur Schule.", "Elle est très gentille avec les enfants."]

    def test_a_page_photographed_sideways_is_read_upright_either_way_round(self):
        config = {"LOCAL_OCR_ENGINE": "auto", "LOCAL_OCR_LANGUAGE": "de"}
        local_ocr.warm_up(config)
        for turn in (90, 270):
            started = time.monotonic()
            result = local_ocr.recognize(rendered_page(self.LINES, rotate=turn), config)
            elapsed = time.monotonic() - started
            self.assertEqual([line.text for line in result.lines], self.LINES, turn)
            self.assertIn(result.rotation, (90, 270), turn)
            widths = [line.box[2] - line.box[0] for line in result.lines]
            heights = [line.box[3] - line.box[1] for line in result.lines]
            self.assertTrue(all(w > h for w, h in zip(widths, heights)), "lines are horizontal again")
            self.assertLess(elapsed, 6.0, f"{elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()

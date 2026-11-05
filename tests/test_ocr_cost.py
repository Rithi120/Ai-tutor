"""What a scan costs in AI credits, and the waste that used to be in it.

The reported problem was that scanning notes burns through the Groq free tier. Measuring
`instance/ai_usage.jsonl`, most of the spend was not recognition - it was failure:

    in=4668 out=2800 retry=1 FAIL schema_validation "response was empty"
    in=3644 out=8000 retry=1 FAIL schema_validation "response was empty"

Two of five real vision calls produced nothing and burned 59% of all OCR output tokens.
Each failure pays twice, because the gateway retries once.

These tests pin the four things that caused it and the two calls that no longer happen.
"""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_ocr_cost_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import pymupdf  # noqa: E402  pyright: ignore[reportMissingImports]
from PIL import Image  # noqa: E402

import app as application  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.contracts import (  # noqa: E402
    BLOCK_TYPES, AIValidationError, repair_recognition, validate_output,
)
from learnova.ai_services.prompts import PROMPT_VERSIONS  # noqa: E402
from learnova.flashcards.image_extraction import extract_image_text  # noqa: E402
from learnova.ocr.service import (  # noqa: E402
    ALLOWED_BLOCK_TYPES, TEXT_LAYER_MAX_LINES, has_usable_text_layer, native_text_quality,
    normalize_recognition, preprocess_document_image, recognition_from_text,
    recognition_instructions, render_pdf_page,
)

OCR_VERSION = PROMPT_VERSIONS["ocr_document_recognition"]

PROSE = ("Die Photosynthese ist der Vorgang, bei dem Pflanzen mit Hilfe von Licht aus "
         "Kohlendioxid und Wasser Traubenzucker herstellen. Dabei wird Sauerstoff frei. "
         "Der Vorgang findet in den Chloroplasten statt und bildet die Grundlage fast "
         "aller Nahrungsketten auf der Erde.")


def page_json(blocks):
    return json.dumps({"blocks": blocks, "detected_page_number": "", "warning": ""})


def blk(block_type="handwriting", content="Die Zelle", confidence=0.7):
    return {"type": block_type, "content": content, "confidence": confidence,
            "bbox": [0.1, 0.1, 0.9, 0.2]}


class RetriesWeWerePayingForTests(unittest.TestCase):
    """Each of these used to reject a whole page and buy a corrective retry."""

    def assert_accepted(self, payload, label):
        try:
            validate_output("ocr_document_recognition", payload, OCR_VERSION, {})
        except AIValidationError as error:
            self.fail(f"{label} still costs a retry: {error.category}: {error.safe_summary}")

    def test_the_prompt_and_the_validator_agree_on_block_types(self):
        # They disagreed, and it was the most expensive bug in the app: the prompt asked
        # for "uncertain" and "crossed_out", the validator accepted neither, and accepted
        # "list"/"unknown" that nothing ever produced.
        self.assertIs(BLOCK_TYPES, ALLOWED_BLOCK_TYPES)
        for required in ("uncertain", "crossed_out"):
            self.assertIn(required, BLOCK_TYPES)
        for never_produced in ("list", "unknown"):
            self.assertNotIn(never_produced, BLOCK_TYPES)

    def test_every_type_the_prompt_offers_is_accepted(self):
        offered = recognition_instructions("Biology", 1).split('"type": "', 1)[1].split('"', 1)[0]
        for block_type in offered.split("|"):
            self.assert_accepted(page_json([blk(block_type)]), f"a {block_type} block")

    def test_an_illegible_fragment_marked_uncertain_is_accepted(self):
        # The prompt says "mark truly illegible fragments as uncertain". Handwriting
        # produces these constantly, so this failure fired on almost every page.
        self.assert_accepted(page_json([blk("uncertain", "Photosyn???")]), "an uncertain block")

    def test_crossed_out_writing_is_accepted(self):
        self.assert_accepted(page_json([blk("crossed_out", "alte Notiz")]), "a crossed-out block")

    def test_a_diagram_with_no_caption_is_accepted(self):
        # normalize_recognition keeps such a block on purpose; the validator rejected the
        # whole page over it.
        self.assert_accepted(page_json([blk("diagram", "")]), "an uncaptioned diagram")

    def test_a_reasoning_preamble_is_accepted(self):
        # parse_json strips <think> blocks; the validator did not, so a reasoning model's
        # ordinary output cost a retry. The OCR vision model is a reasoning model.
        self.assert_accepted("<think>Looking at the page.</think>" + page_json([blk()]),
                             "a <think> preamble")

    def test_genuinely_broken_output_is_still_rejected(self):
        # The fixes must not turn the validator off. An invented block type and a short
        # bbox used to be listed here; since the repair (2026-10-31) the type becomes
        # "uncertain" and the bbox is dropped with the block kept, exactly as
        # normalize_recognition treats them - see RepairedShapesTests.
        for payload, label in (("not json at all", "prose"),
                               (page_json([{"type": "handwriting"}]), "a block with no content"),
                               (page_json([{"type": "handwriting", "content": None},
                                           {"type": "printed_text", "content": "   "}]),
                                "blocks that all carry nothing"),
                               ('{"text": "Die Zelle", "warning": ""}', "no block list"),
                               ('{"blocks": "Die Zelle"}', "blocks as text"),
                               ('"Die Zelle"', "a bare string"),
                               (json.dumps({"pages": [{"blocks": [blk()]}, {"blocks": [blk()]}]}),
                                "two pages in one answer (which one is this page is not guessed)")):
            with self.assertRaises(AIValidationError, msg=label):
                validate_output("ocr_document_recognition", payload, OCR_VERSION, {})


class RepairedShapesTests(unittest.TestCase):
    """The shapes contract_probe.py showed rejecting whole pages are repaired instead.

    Each one used to cost a corrective retry that came back the same, then a failed page
    (one "2/6" cost six provider calls per pass). The app's own normalize_recognition
    already accepted most of them; only the gateway contract refused.
    """

    def accepted(self, payload):
        text = payload if isinstance(payload, str) else json.dumps(payload)
        try:
            validate_output("ocr_document_recognition", text, OCR_VERSION, {})
        except AIValidationError as error:
            self.fail(f"rejected: {error.category}: {error.safe_summary}")
        return repair_recognition(json.loads(text))

    def one_block(self, **fields):
        block = {"type": "handwriting", "content": "Die Zelle", "confidence": 0.7,
                 "bbox": [0.1, 0.1, 0.9, 0.2], **fields}
        return self.accepted({"blocks": [block], "detected_page_number": "", "warning": ""})["blocks"][0]

    def test_worksheet_page_labels_are_kept_as_text(self):
        for label in ("2/6", "S. 3", "IV", "M1", "Seite 4"):
            repaired = self.accepted({"blocks": [blk()], "detected_page_number": label})
            self.assertEqual(repaired["detected_page_number"], label)
            self.assertEqual(normalize_recognition(repaired)["detected_page_number"], label)

    def test_page_numbers_that_are_not_labels_become_empty(self):
        for value, expected in ((3, "3"), (3.0, "3"), (0, ""), (-2, ""), (True, ""), (None, ""),
                                ("null", ""), ("  7  ", "7"), ("x" * 41, ""), ([2], "")):
            repaired = self.accepted({"blocks": [blk()], "detected_page_number": value})
            self.assertEqual(repaired["detected_page_number"], expected, repr(value))

    def test_a_box_on_the_thousand_grid_is_rescaled(self):
        self.assertEqual(self.one_block(bbox=[100, 200, 900, 300])["bbox"], [0.1, 0.2, 0.9, 0.3])

    def test_a_pixel_box_is_dropped_and_the_block_kept(self):
        block = self.one_block(bbox=[120, 340, 1800, 400])
        self.assertEqual((block["bbox"], block["content"]), ([], "Die Zelle"))

    def test_one_pixel_box_marks_every_box_on_the_page_as_pixels(self):
        # A 300-pixel box on a pixel page is not 0.3 of the page.
        repaired = self.accepted({"blocks": [blk() | {"bbox": [10, 20, 1500, 60]},
                                             blk("printed_text", "Kern") | {"bbox": [10, 80, 300, 120]}]})
        self.assertEqual([block["bbox"] for block in repaired["blocks"]], [[], []])
        self.assertEqual([block["content"] for block in repaired["blocks"]], ["Die Zelle", "Kern"])

    def test_missing_empty_short_and_inverted_boxes_are_dropped_not_fatal(self):
        for bbox in (None, [], [0.1, 0.2], [0.9, 0.1, 0.1, 0.2], ["a", 1, 2, 3], [True, 0, 1, 1],
                     "0.1,0.1,0.5,0.5"):
            self.assertEqual(self.one_block(bbox=bbox)["bbox"], [], repr(bbox))
        block = {"type": "handwriting", "content": "ohne Box", "confidence": 0.6}
        self.assertEqual(self.accepted({"blocks": [block]})["blocks"][0]["bbox"], [])

    def test_other_box_spellings_are_read(self):
        self.assertEqual(self.one_block(bbox={"x1": 0.1, "y1": 0.2, "x2": 0.5, "y2": 0.4})["bbox"],
                         [0.1, 0.2, 0.5, 0.4])
        self.assertEqual(self.one_block(bbox=[[0.1, 0.2], [0.5, 0.4]])["bbox"], [0.1, 0.2, 0.5, 0.4])
        self.assertEqual(self.one_block(bbox=None, bbox_2d=[100, 200, 500, 400])["bbox"],
                         [0.1, 0.2, 0.5, 0.4])
        self.assertEqual(self.one_block(bbox=[0.1, 0.2, 1.02, 0.4])["bbox"], [0.1, 0.2, 1.0, 0.4])
        # A four-corner polygon, nested or flattened to eight numbers.
        self.assertEqual(self.one_block(bbox=[[100, 200], [500, 200], [500, 400], [100, 400]])["bbox"],
                         [0.1, 0.2, 0.5, 0.4])
        self.assertEqual(self.one_block(bbox=[100, 200, 500, 200, 500, 400, 100, 400])["bbox"],
                         [0.1, 0.2, 0.5, 0.4])

    def test_a_box_far_past_the_edge_is_dropped_not_shrunk_into_a_corner(self):
        # 1.2 is neither a 0-1 box nor plausibly on the 0-1000 grid (where it would be a
        # speck in the top-left corner). A box on the wrong grid crops the wrong spot, and
        # the close-up second look would then write a word from elsewhere into this block.
        for bbox in ([0.1, 0.1, 1.2, 0.5], [0.5, 0.5, 1.9, 1.9]):
            block = self.one_block(bbox=bbox)
            self.assertEqual((block["bbox"], block["content"]), ([], "Die Zelle"), repr(bbox))

    def test_the_grid_is_decided_for_the_whole_page(self):
        # One garbage coordinate must not rescale every good 0-1 box on the page...
        page = self.accepted({"blocks": [blk(), blk("printed_text", "Kern") | {"bbox": [0.1, 0.3, 0.8, 0.4]},
                                         blk("printed_text", "Wand") | {"bbox": [0.1, 0.5, 7, 0.6]}]})
        self.assertEqual([block["bbox"] for block in page["blocks"]],
                         [[0.1, 0.1, 0.9, 0.2], [0.1, 0.3, 0.8, 0.4], []])
        # ...and on a 0-1000 page a stray small box is not taken as a full-width 0-1 box.
        page = self.accepted({"blocks": [blk() | {"bbox": [100, 100, 900, 200]},
                                         blk("printed_text", "Kern") | {"bbox": [100, 300, 800, 400]},
                                         blk("printed_text", "Wand") | {"bbox": [0.1, 0.5, 0.9, 0.6]}]})
        self.assertEqual([block["bbox"] for block in page["blocks"]],
                         [[0.1, 0.1, 0.9, 0.2], [0.1, 0.3, 0.8, 0.4], []])
        self.assertEqual(len(page["blocks"]), 3, "every block is kept either way")

    def test_numbers_too_large_for_a_float_never_raise(self):
        huge = "9" * 400   # valid JSON; float() of it raises OverflowError
        for text in ('{"blocks":[{"type":"printed_text","content":"x","confidence":0.5}],'
                     f'"detected_page_number":{huge}}}',
                     f'{{"blocks":[{{"type":"printed_text","content":"x","confidence":{huge}}}]}}',
                     f'{{"blocks":[{{"type":"printed_text","content":"x","bbox":[1,2,3,{huge}]}}]}}',
                     '{"blocks":[{"type":"printed_text","content":"x","confidence":1e400,'
                     '"bbox":[0.1,0.1,1e400,0.2]}],"detected_page_number":1e400}'):
            repaired = self.accepted(text)
            self.assertEqual(repaired["blocks"][0]["content"], "x")
            self.assertEqual(repaired["detected_page_number"], "")
            normalize_recognition(json.loads(text))

    def test_deeply_nested_content_never_raises(self):
        nested: object = "x"
        for _ in range(2000):   # json.loads accepts this; following it recursively did not
            nested = [nested]
        for content in (nested, {"rows": [nested]}):
            page = {"blocks": [blk(), {"type": "table", "content": content}]}
            repaired = self.accepted(json.dumps(page))
            self.assertEqual([block["content"] for block in repaired["blocks"]], ["Die Zelle"])
            self.assertEqual(normalize_recognition(page)["text"], "Die Zelle")

    def test_the_reading_is_found_wherever_the_block_put_it(self):
        def content(**fields):
            block = {"type": "printed_text", "confidence": 0.9} | fields
            return self.accepted({"blocks": [block]})["blocks"][0]["content"]
        self.assertEqual(content(content="", text="Mitose"), "Mitose", "empty content beside text")
        self.assertEqual(content(lines=["Zeile 1", "Zeile 2"]), "Zeile 1\nZeile 2")
        self.assertEqual(content(type="table", headers=["Jahr", "Ereignis"], rows=[["1789", "Revolution"]]),
                         "Jahr | Ereignis\n1789 | Revolution")
        self.assertEqual(content(content={"cells": ["a", "b"]}), "a | b")
        self.assertEqual(content(content={"text": None, "content": "Kern"}), "Kern")

    def test_page_labels_that_say_nothing_become_empty(self):
        for value in ("0", "-1", "-", "?", "N/A", "unknown", "  "):
            repaired = self.accepted({"blocks": [blk()], "detected_page_number": value})
            self.assertEqual(repaired["detected_page_number"], "", repr(value))
        self.assertEqual(self.accepted({"blocks": [blk()], "detected_page_number": "07"})["detected_page_number"],
                         "07", "a printed label is kept as printed")

    def test_one_bad_box_among_forty_blocks_keeps_the_page(self):
        blocks = [blk("printed_text", f"Zeile {index}") for index in range(40)]
        blocks[17]["bbox"] = [0.4, 0.1]
        repaired = self.accepted({"blocks": blocks})
        self.assertEqual(len(repaired["blocks"]), 40)
        self.assertEqual(sum(1 for block in repaired["blocks"] if block["bbox"]), 39)

    def test_confidences_in_other_shapes_become_zero_to_one(self):
        for value, expected in ((95, 0.95), ("95", 0.95), ("95%", 0.95), ("0,8", 0.8), (0.42, 0.42),
                                ("high", 0.9), ("High", 0.9), ("low", 0.4), ("medium", 0.65),
                                (1.2, 1.0), (250, 1.0), (-0.3, 0.0), (True, 0.0), (None, 0.0),
                                ("sure", 0.0)):
            self.assertEqual(self.one_block(confidence=value)["confidence"], expected, repr(value))
        missing = {"type": "handwriting", "content": "x", "bbox": []}
        self.assertEqual(self.accepted({"blocks": [missing]})["blocks"][0]["confidence"], 0.0)

    def test_a_low_word_stays_below_the_review_band(self):
        recognized = normalize_recognition(repair_recognition(
            {"blocks": [blk() | {"confidence": "low"}]}))
        self.assertEqual(recognized["blocks"][0]["confidence_status"], "unclear")

    def test_a_table_sent_as_rows_becomes_one_line_per_row(self):
        block = self.one_block(type="table", content=[["Stoff", "Formel"], ["Wasser", "H₂O"]])
        self.assertEqual(block["content"], "Stoff | Formel\nWasser | H₂O")
        block = self.one_block(type="table", content={"headers": ["Jahr", "Ereignis"],
                                                      "rows": [{"a": "1789", "b": "Revolution"}]})
        self.assertEqual(block["content"], "Jahr | Ereignis\n1789 | Revolution")
        self.assertEqual(self.one_block(content=["erste Zeile", "zweite Zeile"])["content"],
                         "erste Zeile\nzweite Zeile")
        self.assertEqual(self.one_block(content=42)["content"], "42")

    def test_block_types_are_mapped_like_the_app_maps_them(self):
        for written, expected in (("Printed Text", "printed_text"), ("text", "printed_text"),
                                  ("handwritten", "handwriting"), ("title", "heading"),
                                  ("equation", "formula"), ("figure", "diagram"),
                                  ("crossed-out", "crossed_out"), ("interpretive_dance", "uncertain"),
                                  (None, "uncertain")):
            self.assertEqual(self.one_block(type=written)["type"], expected, repr(written))

    def test_text_in_place_of_content_is_read(self):
        block = {"type": "printed_text", "text": "Mitose", "confidence": 0.9, "bbox": []}
        self.assertEqual(self.accepted({"blocks": [block]})["blocks"][0]["content"], "Mitose")

    def test_nulls_and_word_flags_do_not_turn_into_text(self):
        # normalize_recognition used to write str(None): a null suggestion became "None",
        # and bool("false") marked live writing as crossed out. It now repairs first, so
        # the app path is fixed without anyone remembering to call the repair.
        block = blk() | {"suggested_correction": None, "nearby_text": "null", "crossed_out": "false",
                         "important_candidate": "yes"}
        for recognized in (normalize_recognition({"blocks": [block], "warning": None}),
                           normalize_recognition(repair_recognition({"blocks": [block], "warning": "None"}))):
            item = recognized["blocks"][0]
            self.assertEqual((item["suggested_correction"], item["nearby_text"]), ("", ""))
            self.assertEqual((item["crossed_out"], item["important_candidate"]), (False, True))
            self.assertEqual(recognized["warning"], "")
            self.assertEqual(recognized["suggested_corrections"], [])
            self.assertEqual(recognized["text"], "Die Zelle")
        # Real content is never filtered: "None" can be what the page says.
        self.assertEqual(normalize_recognition({"blocks": [blk(content="None")]})["text"], "None")

    def test_a_crossed_out_block_is_flagged_by_its_type(self):
        item = self.accepted({"blocks": [blk("crossed_out", "alte Notiz"), blk()]})["blocks"][0]
        self.assertTrue(item["crossed_out"])
        self.assertEqual(normalize_recognition({"blocks": [blk("crossed_out", "alte Notiz"), blk()]})["text"],
                         "Die Zelle", "crossed-out writing is not study text")

    def test_an_unusable_block_never_sinks_the_page(self):
        repaired = self.accepted({"blocks": [blk(), "stray text", {"type": "handwriting"}, 7,
                                             blk("diagram", "")]})
        self.assertEqual([block["type"] for block in repaired["blocks"]], ["handwriting", "diagram"])

    def test_wrapped_and_bare_block_lists_are_unwrapped(self):
        for payload in ({"recognition": {"blocks": [blk()], "detected_page_number": "2/6"}},
                        {"result": {"page": {"blocks": [blk()]}}}, {"pages": [{"blocks": [blk()]}]},
                        [{"blocks": [blk()]}], [blk()], {"blocks": blk()},
                        {"blocks": {"1": blk(), "2": blk("printed_text", "Kern")}}):
            repaired = self.accepted(payload)
            self.assertEqual(repaired["blocks"][0]["content"], "Die Zelle", repr(payload)[:60])
        self.assertEqual(self.accepted({"blocks": None, "warning": "leere Seite"})["blocks"], [])

    def test_the_app_reads_every_shape_the_gateway_lets_through(self):
        # The validator accepting a shape is only half the repair: normalize_recognition,
        # which the app and the flashcard import call on the same output_text, must not
        # crash on it ([].get) or quietly read it as an empty page ({"recognition": ...}).
        shapes = ([blk()], {"recognition": {"blocks": [blk()]}}, {"pages": [{"blocks": [blk()]}]},
                  {"blocks": blk()}, {"blocks": {"1": blk()}},
                  {"blocks": [blk() | {"bbox": [100, 100, 900, 200], "confidence": "70%"}]})
        for shape in shapes:
            self.accepted(shape)
            recognized = normalize_recognition(shape)
            self.assertTrue(recognized["readable"], repr(shape)[:60])
            self.assertEqual(recognized["text"], "Die Zelle")
        blank = normalize_recognition({"blocks": None})
        self.assertEqual((blank["readable"], blank["blocks"]), (False, []))
        image = io.BytesIO()
        Image.new("RGB", (400, 300), "white").save(image, format="PNG")
        result = extract_image_text(image.getvalue(), subject="Biology", page_number=1,
                                    recognize=lambda **_kwargs: [blk()])
        self.assertEqual((result["readable"], result["text"]), (True, "Die Zelle"))

    def test_the_repair_never_raises_and_is_stable(self):
        for junk in (None, "x", 5, {"blocks": "x"}, {"a": {"b": 1}}, [None, 3]):
            self.assertEqual(repair_recognition(junk)["blocks"], [], repr(junk))
        once = repair_recognition({"blocks": [blk() | {"bbox": [100, 100, 500, 200], "confidence": 88}],
                                   "detected_page_number": "S. 3"})
        self.assertEqual(repair_recognition(once), once)

    def test_the_repaired_page_reads_like_a_clean_one(self):
        messy = {"blocks": [{"type": "Heading", "content": "Zellbiologie", "confidence": "high",
                             "bbox": [50, 40, 950, 90]},
                            {"type": "table", "content": [["Organell", "Aufgabe"], ["Kern", "Steuerung"]],
                             "confidence": 85, "bbox": []}],
                 "detected_page_number": "2/6"}
        recognized = normalize_recognition(repair_recognition(messy))
        self.assertTrue(recognized["readable"])
        self.assertEqual(recognized["text"], "Zellbiologie\nOrganell | Aufgabe\nKern | Steuerung")
        self.assertEqual(recognized["headings"][0]["bbox"], [0.05, 0.04, 0.95, 0.09])
        self.assertEqual(recognized["confidence"], 0.875)
        self.assertEqual(recognized["detected_page_number"], "2/6")


class RepairedPageCostTests(unittest.TestCase):
    """Through the real gateway: a "2/6" page is one provider call, not a retry loop."""

    CHANGED = ("TESTING", "RUN_LIVE_AI_TEST", "AI_USAGE_PATH", "ALLOW_LIVE_AI", "AI_CACHE_DIR", "AI_MODE")

    def setUp(self):
        # The provider itself is patched below; ALLOW_LIVE_AI only lets the gateway run
        # its live path (retries, validation) without depending on a developer's .env.
        # Every key is restored afterwards: the app object is shared by the whole suite.
        missing = object()
        self.saved = {key: application.app.config.get(key, missing) for key in self.CHANGED}
        self.missing = missing
        application.app.config.update(
            TESTING=True, RUN_LIVE_AI_TEST=True, AI_USAGE_PATH="", ALLOW_LIVE_AI=True,
            AI_CACHE_DIR=tempfile.mkdtemp(), AI_MODE="live")
        with application.app.app_context():
            application.db.create_all()

    def tearDown(self):
        for key, value in self.saved.items():
            if value is self.missing:
                application.app.config.pop(key, None)
            else:
                application.app.config[key] = value

    def provider_calls(self, output, page):
        class Response:
            output_text = output
            model = "m"

            class usage:  # noqa: N801
                input_tokens = 100
                output_tokens = 200
                total_tokens = 300
        with application.app.test_request_context("/"):
            with patch.object(ai_service, "_provider_response", return_value=Response()) as provider, \
                 patch.object(ai_service, "_assert_usage_limits"):
                try:
                    response = ai_service.create_response(
                        task_type="ocr_document_recognition", language="German", model="m",
                        instructions="read the page", input=f"worksheet page {page}",
                        max_output_tokens=100, private_scope=None)
                except AIValidationError as error:
                    response = error
        return provider.call_count, response

    def test_a_worksheet_page_label_costs_one_call(self):
        output = json.dumps({"blocks": [{"type": "printed_text", "content": "Arbeitsblatt",
                                         "confidence": 95, "bbox": [100, 50, 900, 120]}],
                             "detected_page_number": "2/6", "warning": ""})
        calls, response = self.provider_calls(output, 1)
        self.assertEqual(calls, 1, "no corrective retry for a repairable page")
        assert not isinstance(response, AIValidationError)
        # And what the app then makes of the very same text is the repaired page.
        recognized = normalize_recognition(ai_service.parse_json(response.output_text))
        self.assertEqual((recognized["detected_page_number"], recognized["confidence"]), ("2/6", 0.95))
        self.assertEqual(recognized["blocks"][0]["bbox"], [0.1, 0.05, 0.9, 0.12])

    def test_other_repairable_shapes_cost_one_call_each(self):
        shapes = (
            json.dumps([blk("table", "") | {"content": [["Stoff", "Formel"], ["Wasser", "H2O"]]}]),
            json.dumps({"recognition": {"blocks": [blk()], "detected_page_number": "S. 3"}}),
            json.dumps({"blocks": [blk(), blk() | {"bbox": [0.4, 0.1]}], "detected_page_number": "IV"}),
        )
        for page, output in enumerate(shapes, start=2):
            calls, response = self.provider_calls(output, page)
            self.assertNotIsInstance(response, AIValidationError, output[:60])
            self.assertEqual(calls, 1, output[:60])

    def test_an_answer_with_nothing_to_read_still_buys_its_retry(self):
        # The control: the gateway still corrects what no repair can rescue, so the
        # single-call results above are the repair working, not validation switched off.
        calls, response = self.provider_calls(page_json([{"type": "handwriting", "content": ""}]), 9)
        self.assertIsInstance(response, AIValidationError)
        self.assertEqual(calls, 2, "the first answer plus one corrective retry")


class NativeTextTests(unittest.TestCase):
    """A PDF that already contains its words should not be read from a picture."""

    def test_a_real_page_of_prose_is_usable(self):
        usable, reason = native_text_quality(PROSE)
        self.assertTrue(usable)
        self.assertEqual(reason, "native_text")

    def test_a_scanned_pdf_has_nothing_to_use(self):
        for text, reason in (("", "no_text_layer"), (None, "no_text_layer"),
                             ("Kapitel 3", "too_little_text"), ("42", "too_little_text")):
            usable, actual = native_text_quality(text)
            self.assertFalse(usable)
            self.assertEqual(actual, reason)

    def test_a_broken_embedded_font_is_refused(self):
        # A junk text layer must go to the vision model, not into a student's notes.
        # Enough letters to pass the length gate, but mostly punctuation - what a broken
        # embedded font actually extracts as.
        usable, reason = native_text_quality("ab ---- " * 150)
        self.assertFalse(usable)
        self.assertEqual(reason, "not_mostly_letters")

    def test_a_long_run_of_symbols_with_no_words_is_refused(self):
        usable, reason = native_text_quality("".join("�" for _ in range(600)))
        self.assertFalse(usable)
        self.assertEqual(reason, "too_little_text")

    def test_the_result_looks_like_any_other_recognised_page(self):
        result = recognition_from_text(PROSE)
        self.assertTrue(result["readable"])
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["confidence_status"], "high")
        self.assertIn("Photosynthese", result["text"])

    def test_it_never_triggers_a_paid_second_look(self):
        # Confidence 1.0 means no block is uncertain, so the close-up pass stays unspent.
        self.assertEqual(recognition_from_text(PROSE)["uncertain_regions"], [])

    def test_a_long_page_keeps_every_line(self):
        # 400 lines used to be the cap, and store_page_recognition then replaced the full
        # text layer with the shortened one: the end of a dense page silently vanished.
        text = "\n".join(f"Zeile {index}: Merksatz" for index in range(1, 451))
        result = recognition_from_text(text)
        self.assertEqual(len(result["blocks"]), 450)
        self.assertTrue(result["text"].endswith("Zeile 450: Merksatz"))

    def test_the_line_cap_bounds_blocks_but_never_drops_text(self):
        # The cap only limits DocumentBlock rows. Cutting the text would silently lose the
        # end of the page, because store_page_recognition replaces extracted_text with it.
        self.assertEqual(TEXT_LAYER_MAX_LINES, 3000)
        lines = [f"z{index}" for index in range(TEXT_LAYER_MAX_LINES + 500)]
        result = recognition_from_text("\n".join(lines))
        self.assertEqual(len(result["blocks"]), TEXT_LAYER_MAX_LINES)
        self.assertEqual(result["text"], "\n".join(lines), "every line survives")
        self.assertEqual(result["blocks"][-1]["content"].splitlines(), lines[TEXT_LAYER_MAX_LINES - 1:])
        small = recognition_from_text("a\nb\nc\nd", limit=2)
        self.assertEqual([block["content"] for block in small["blocks"]], ["a", "b\nc\nd"])
        self.assertEqual(recognition_from_text("a\nb", limit=0)["text"], "a\nb")

    def test_a_short_real_text_layer_counts_as_one(self):
        # A title page is not good enough to skip recognition, but it is born-digital.
        title = "Arbeitsblatt Zellbiologie - Klasse 8 - Name:"
        self.assertFalse(native_text_quality(title)[0])
        self.assertTrue(has_usable_text_layer(title))
        self.assertTrue(has_usable_text_layer(PROSE))
        for text in ("", None, "Kapitel 3", "ab ---- " * 150, "�" * 300):
            self.assertFalse(has_usable_text_layer(text), repr(text)[:40])


class VisionCallsPerPageTests(unittest.TestCase):
    """The measurement that matters: how many provider calls one page costs."""

    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "kira", "email": "kira@example.com",
            "password": "correct-horse-battery"})

    @staticmethod
    def _pdf(text=None):
        document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        if text:
            page.insert_textbox(pymupdf.Rect(60, 60, 540, 700), text, fontsize=13)
        data = document.tobytes()
        document.close()
        return data

    def _project_page(self, payload, filename):
        response = self.client.post("/projects", data={
            "title": "Notes", "subject": "Biology",
            "materials": [(io.BytesIO(payload), filename, "application/pdf")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.scalars(
                application.db.select(application.LearningProject)).all()[-1]
            page = application.db.session.scalars(application.db.select(application.ProjectPage)
                .where(application.ProjectPage.project_id == project.id)).all()[0]
            return project.id, page.id

    @staticmethod
    def _reply():
        class Response:
            output_text = page_json([blk(confidence=0.9)])
            usage = None
            model = "stub"
        return Response()

    def test_a_pdf_with_a_text_layer_costs_nothing(self):
        project_id, page_id = self._project_page(self._pdf(PROSE), "text.pdf")
        with patch.object(application, "create_response", return_value=self._reply()) as call:
            response = self.client.post(
                f"/projects/{project_id}/pages/{page_id}/recognize", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(call.call_count, 0, "a readable PDF should not reach the model")
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            assert page is not None
            self.assertEqual(page.extraction_status, "ready")
            self.assertIn("Photosynthese", page.extracted_text)

    def test_a_born_digital_page_is_not_warned_about_glare(self):
        # Rendered white paper is above the glare cut-off, so every clean PDF page used
        # to say "Possible glare detected". The same render still measures as glare when
        # the advisories are on - only the false wording is gone.
        pdf = self._pdf(PROSE)
        rendered = render_pdf_page(pdf, 0)
        self.assertIn("glare", " ".join(preprocess_document_image(rendered).warnings))
        quiet = preprocess_document_image(rendered, quality_warnings=False)
        self.assertFalse([item for item in quiet.warnings if "glare" in item or "blurred" in item])
        self.assertGreater(quiet.glare_ratio, 0.18, "the metric is still measured")
        project_id, page_id = self._project_page(pdf, "text.pdf")
        with patch.object(application, "create_response", return_value=self._reply()):
            self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={})
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            assert page is not None
            self.assertNotIn("glare", page.warning)
            self.assertNotIn("blurred", page.warning)
            self.assertIn("text layer", page.warning)

    def test_the_app_stores_a_repaired_reading(self):
        # A wrapped answer with a "2/6" label, confidence 95 and a 0-1000 box: the
        # gateway passes it, and the app's own reading of output_text must agree - before
        # the repair it came out as an empty, "unreadable" page.
        project_id, page_id = self._project_page(self._scan(), "scan.pdf")

        class Response:
            output_text = json.dumps({"recognition": {
                "blocks": [blk(confidence=95) | {"bbox": [100, 100, 900, 200]}],
                "detected_page_number": "2/6"}})
            usage = None
            model = "stub"
        with patch.object(application, "create_response", return_value=Response()) as call:
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(call.call_count, 1, "a confident reading needs no close-up")
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            assert page is not None
            self.assertEqual((page.extraction_status, page.detected_page_number), ("ready", "2/6"))
            self.assertEqual(page.extracted_text, "Die Zelle")
            self.assertEqual(json.loads(page.blocks[0].bbox_json), [0.1, 0.1, 0.9, 0.2])
            self.assertEqual(page.blocks[0].confidence, 0.95)

    @staticmethod
    def _scan():
        # A scanned page: one picture with marks on it and no text layer. (A page with no
        # mark at all is a blank back page and is not sent to the model - FIX1, P1-05.)
        image = Image.new("RGB", (850, 1100), "#f4f1ea")
        for row in range(10):
            image.paste((40, 40, 40), (80, 100 + row * 60, 600, 108 + row * 60))
        png = io.BytesIO()
        image.save(png, format="PNG")
        document = pymupdf.open()  # pyright: ignore[reportUnknownVariableType]
        page = document.new_page()  # pyright: ignore[reportAttributeAccessIssue]
        page.insert_image(page.rect, stream=png.getvalue())
        data = document.tobytes()
        document.close()
        return data

    def test_a_scanned_pdf_still_goes_to_the_model(self):
        project_id, page_id = self._project_page(self._scan(), "scan.pdf")
        with patch.object(application, "create_response", return_value=self._reply()) as call:
            response = self.client.post(
                f"/projects/{project_id}/pages/{page_id}/recognize", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(call.call_count, 1)

    def test_a_forced_retry_always_reaches_the_model(self):
        # Re-reading a page the student is unhappy with must not be short-circuited by
        # the free path, or "Retry recognition" would do nothing.
        project_id, page_id = self._project_page(self._pdf(PROSE), "text.pdf")
        with patch.object(application, "create_response", return_value=self._reply()) as call:
            self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize",
                             json={"mode": "grayscale"})
        self.assertEqual(call.call_count, 1)


class DeterministicCachingTests(unittest.TestCase):
    """Reading the same page twice should be paid for once - chat should not be."""

    def setUp(self):
        self.cache = tempfile.mkdtemp()
        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "RUN_LIVE_AI_TEST", "AI_USAGE_PATH", "AI_CACHE_DIR",
                    "AI_MODE")
        application.app.config.update(
            TESTING=True, RUN_LIVE_AI_TEST=True, AI_USAGE_PATH="",
            AI_CACHE_DIR=self.cache, AI_MODE="live")

    def tearDown(self):
        application.app.config.update(RUN_LIVE_AI_TEST=False)

    def _call(self, task_type, output_text):
        class Response:
            def __init__(self):
                self.output_text = output_text
                self.model = "m"
            class usage:  # noqa: N801
                input_tokens = 100
                output_tokens = 2000
                total_tokens = 2100
        with application.app.test_request_context("/"):
            with patch.object(ai_service, "_provider_response", return_value=Response()) as provider, \
                 patch.object(ai_service, "_assert_usage_limits"):
                ai_service.create_response(
                    task_type=task_type, language="German", model="m",
                    instructions="read the page", input="identical page bytes",
                    max_output_tokens=100, private_scope=None)
                return provider.call_count

    def test_recognising_the_same_page_again_is_free(self):
        payload = page_json([blk(confidence=0.9)])
        self.assertEqual(self._call("ocr_document_recognition", payload), 1)
        self.assertEqual(self._call("ocr_document_recognition", payload), 0)

    def test_chat_is_never_served_from_cache(self):
        # A repeated question must get a fresh answer; caching it would be a bug.
        for _ in range(3):
            self.assertEqual(self._call("assistant_chat", "Hallo!"), 1)

    def test_only_deterministic_tasks_are_cached(self):
        # The vocabulary page reader joined the set for the same reason: the same page
        # read twice must give the same rows, so paying twice for it is waste. So did the
        # shared image reader: a photo re-sent after a failed reply is the same photo.
        self.assertEqual(ai_service.DETERMINISTIC_TASKS,
                         {"ocr_document_recognition", "handwriting_region_review", "vocabulary_page_extraction",
                          "image_understanding"})

    def _age_every_cache_entry(self, days):
        import json
        from datetime import datetime, timedelta, timezone
        from pathlib import Path
        stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        for path in Path(self.cache).rglob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["created_at"] = stamp
            path.write_text(json.dumps(payload), encoding="utf-8")

    def test_an_expired_cache_entry_is_not_served(self):
        # `created_at` was written from the start and never read; a page read 40 days ago
        # is re-read rather than trusted forever.
        application.app.config["AI_CACHE_TTL_DAYS"] = 30
        payload = page_json([blk(confidence=0.9)])
        self.assertEqual(self._call("ocr_document_recognition", payload), 1)
        self._age_every_cache_entry(days=40)
        self.assertEqual(self._call("ocr_document_recognition", payload), 1)

    def test_a_ttl_of_zero_never_expires(self):
        application.app.config["AI_CACHE_TTL_DAYS"] = 0
        payload = page_json([blk(confidence=0.8)])
        self.assertEqual(self._call("ocr_document_recognition", payload), 1)
        self._age_every_cache_entry(days=4000)
        self.assertEqual(self._call("ocr_document_recognition", payload), 0)

    def test_cache_writes_use_a_unique_temporary_name(self):
        # A fixed ".tmp" per key let two writers finishing together collide.
        source = open(ai_service.__file__, encoding="utf-8").read()
        self.assertIn('path.with_name(f"{path.stem}.{uuid.uuid4().hex}.tmp")', source)


class BudgetTests(unittest.TestCase):
    """A budget below what the prompt asks for truncates, fails, and pays a retry."""

    def test_section_and_exam_generation_can_finish_their_json(self):
        for task, floor in (("project_section_generation", 4000),
                            ("final_exam_generation", 6000)):
            self.assertGreaterEqual(ai_service.DEFAULT_OUTPUT_TOKEN_BUDGETS[task], floor, task)
            self.assertGreaterEqual(
                application.app.config[f"AI_{task.upper()}_MAX_OUTPUT_TOKENS"], floor, task)


class PromptEconomyTests(unittest.TestCase):
    def test_the_prompt_stops_asking_for_fields_nothing_reads(self):
        prompt = recognition_instructions("Biology", 1)
        self.assertIn("diagram block only", prompt)     # nearby_text scoped to diagrams
        self.assertIn("charged for", prompt)            # and the model is told why

    def test_it_still_forbids_invention(self):
        self.assertIn("Never invent", recognition_instructions("Biology", 1))


if __name__ == "__main__":
    unittest.main()

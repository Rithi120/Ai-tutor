"""The handwriting second look: re-reading unclear words enlarged, in one extra call.

Messy handwriting mostly fails for a mechanical reason - the word is small, the page is
downscaled before the model sees it, and a shaky word survives as a smudge. These tests
cover the machinery that crops those words out and sends them back enlarged, and in
particular the rules that stop it making a page *worse*:

  * a close-up reading only replaces text when it is more confident than the first pass
  * a fragment the model admits is illegible is left alone, not guessed at
  * an out-of-order, repeated or invented index cannot corrupt a page
  * the whole pass is optional: if it fails, the first pass survives untouched
"""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

TEST_DATABASE = tempfile.gettempdir() + "/learnova_second_look_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ocr.service import (  # noqa: E402
    REGION_SHEET_MAX,
    _adaptive_threshold,
    _otsu_threshold,
    build_region_sheet,
    merge_region_review,
    normalize_recognition,
    normalize_region_review,
    region_review_instructions,
)


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


def page_png(size=(1600, 2200), words=(("Photosynthese", 200, 300),)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for text, x, y in words:
        draw.text((x, y), text, fill=(90, 90, 90))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def sheet(page_data, blocks, **kwargs):
    """build_region_sheet, asserted non-None so the tests below read cleanly."""
    result = build_region_sheet(page_data, blocks, **kwargs)
    assert result is not None, "expected a region sheet"
    return result


def uncertain(order, content, bbox=(0.1, 0.1, 0.4, 0.14), confidence=0.4):
    return {
        "order": order, "type": "handwriting", "content": content, "bbox": list(bbox),
        "confidence": confidence, "confidence_status": "review", "crossed_out": False,
        "important_candidate": False, "teacher_highlight_candidate": False,
        "nearby_text": "", "suggested_correction": "",
    }


# --------------------------------------------------------------- image preparation
class AdaptiveThresholdTests(unittest.TestCase):
    """A global cut assumes even lighting; a phone photo of a notebook rarely is."""

    @staticmethod
    def _gradient_page():
        """Faint pencil strokes spread evenly, with one side of the page in shadow."""
        image = Image.new("L", (900, 600), 255)
        draw = ImageDraw.Draw(image)
        for row in range(6):
            y = 60 + row * 80
            for x in range(60, 840, 14):
                draw.line((x, y, x + 9, y - 12), fill=150, width=3)
                draw.line((x + 9, y - 12, x + 12, y), fill=150, width=3)
        # One side progressively in shadow, subtracted as an image so the whole page is
        # darkened in one Pillow operation rather than pixel by pixel in Python.
        row = [int(120 * (x / 900) ** 1.5) for x in range(900)]
        gradient = Image.new("L", (900, 1))
        gradient.putdata(row)
        return ImageChops.subtract(image, gradient.resize((900, 600)))

    @staticmethod
    def _ink(binary):
        return sum(1 for value in binary.getdata() if value < 128)

    def _global(self, gray):
        cut = _otsu_threshold(gray)
        return gray.point([0] * cut + [255] * (256 - cut))

    def test_a_shadow_no_longer_swallows_half_the_page(self):
        page = self._gradient_page()
        halves = ((0, 0, 450, 600), (450, 0, 900, 600))
        global_left, global_right = (self._ink(self._global(page).crop(box)) for box in halves)
        local_left, local_right = (self._ink(_adaptive_threshold(page).crop(box)) for box in halves)

        # The strokes are spread evenly, so the two halves must contain similar ink.
        self.assertGreater(global_right, global_left * 5,
                           "expected the global cut to flood the shadowed half")
        self.assertLess(max(local_left, local_right), min(local_left, local_right) * 1.5,
                        f"adaptive halves should match: {local_left} vs {local_right}")

    def test_clean_black_on_white_is_not_damaged(self):
        image = Image.new("L", (800, 500), 255)
        draw = ImageDraw.Draw(image)
        for row in range(8):
            draw.text((40, 40 + row * 50), "Clean printed body text", fill=0)
        ink = self._ink(_adaptive_threshold(image))
        self.assertGreater(ink, 500)                      # the text survives
        self.assertLess(ink, image.width * image.height * 0.2)   # the page is not flooded

    def test_blank_paper_does_not_become_ink(self):
        blank = Image.new("L", (800, 500), 250)
        self.assertLess(self._ink(_adaptive_threshold(blank)), 800)

    def test_a_small_crop_falls_back_to_the_global_cut(self):
        # Below a certain size there is no meaningful neighbourhood to compare against.
        tiny = Image.new("L", (60, 40), 255)
        ImageDraw.Draw(tiny).rectangle((10, 10, 30, 25), fill=20)
        result = _adaptive_threshold(tiny)
        self.assertEqual(result.size, (60, 40))
        self.assertTrue(set(result.getdata()) <= {0, 255})


# ------------------------------------------------------------------- sheet building
class RegionSheetTests(unittest.TestCase):
    def test_uncertain_words_become_one_numbered_sheet(self):
        built = sheet(page_png(), [
            uncertain(1, "Photosyn???"), uncertain(4, "Chloro???"), uncertain(9, "Stoff???")])
        self.assertEqual(built.orders, [1, 4, 9])   # sheet position -> original block
        image = Image.open(io.BytesIO(built.data))
        self.assertGreater(image.height, 200)       # three strips stacked, not one crop

    def test_a_block_without_a_bounding_box_is_skipped_not_guessed(self):
        built = sheet(page_png(), [
            uncertain(1, "readable"), dict(uncertain(2, "no box"), bbox=[])])
        self.assertEqual(built.orders, [1])

    def test_nothing_to_re_read_returns_no_sheet(self):
        self.assertIsNone(build_region_sheet(page_png(), []))
        self.assertIsNone(build_region_sheet(page_png(), [dict(uncertain(1, "x"), bbox=[])]))

    def test_a_damaged_page_image_returns_no_sheet_rather_than_raising(self):
        self.assertIsNone(build_region_sheet(b"not an image", [uncertain(1, "x")]))

    def test_the_sheet_is_capped_so_one_page_stays_one_request(self):
        many = [uncertain(n, f"word {n}") for n in range(1, 25)]
        self.assertEqual(len(sheet(page_png(), many).orders), REGION_SHEET_MAX)

    def test_the_crop_is_enlarged_rather_than_shrunk(self):
        # The entire point is to add pixels: a 5%-wide word must come back bigger.
        built = sheet(page_png(), [uncertain(1, "tiny", bbox=(0.1, 0.1, 0.15, 0.115))])
        strip = Image.open(io.BytesIO(built.data))
        self.assertGreater(strip.width, 0.05 * 1600)

    def test_the_prompt_carries_the_first_reading_as_a_hint_not_an_answer(self):
        prompt = region_review_instructions("Biology", [uncertain(1, "Photosyn???")])
        self.assertIn("Photosyn???", prompt)
        self.assertIn("it is often", prompt)          # framed as unreliable
        self.assertIn("illegible", prompt)            # an honest "cannot read" is allowed
        self.assertIn("Do not translate", prompt)     # read, do not improve


# ---------------------------------------------------------------- reading + merging
class RegionReadingTests(unittest.TestCase):
    def test_readings_map_back_to_the_block_they_came_from(self):
        readings = normalize_region_review(
            {"regions": [{"index": 2, "content": "Chloroplast", "confidence": 0.9}]}, [1, 4, 9])
        self.assertEqual(readings, {4: {"content": "Chloroplast", "confidence": 0.9}})

    def test_an_invented_or_out_of_range_index_is_discarded(self):
        readings = normalize_region_review(
            {"regions": [{"index": 99, "content": "invented", "confidence": 1.0},
                         {"index": 0, "content": "also invented", "confidence": 1.0}]}, [1, 4])
        self.assertEqual(readings, {})

    def test_a_repeated_index_keeps_the_first_answer(self):
        readings = normalize_region_review({"regions": [
            {"index": 1, "content": "first", "confidence": 0.7},
            {"index": 1, "content": "second", "confidence": 0.99}]}, [3])
        self.assertEqual(readings[3]["content"], "first")

    def test_an_honest_illegible_is_not_turned_into_a_guess(self):
        readings = normalize_region_review({"regions": [
            {"index": 1, "content": "maybe this", "confidence": 0.9, "illegible": True},
            {"index": 2, "content": "   ", "confidence": 0.9}]}, [1, 2])
        self.assertEqual(readings, {})

    def test_garbage_yields_nothing_rather_than_raising(self):
        for payload in (None, "text", 42, {}, {"regions": "no"}, {"regions": [None, 7]}):
            self.assertEqual(normalize_region_review(payload, [1]), {})

    def test_a_more_confident_reading_replaces_the_text_and_keeps_the_old_one(self):
        page = normalize_recognition({"blocks": [uncertain(1, "Photosyn???")]})
        improved = merge_region_review(page, {1: {"content": "Photosynthese", "confidence": 0.92}})
        self.assertEqual(improved, 1)
        block = page["blocks"][0]
        self.assertEqual(block["content"], "Photosynthese")
        self.assertEqual(block["previous_reading"], "Photosyn???")   # auditable, not silent
        self.assertTrue(block["second_look"])
        self.assertEqual(block["confidence_status"], "high")

    def test_a_less_confident_reading_is_ignored(self):
        page = normalize_recognition({"blocks": [uncertain(1, "original", confidence=0.7)]})
        self.assertEqual(merge_region_review(page, {1: {"content": "worse", "confidence": 0.3}}), 0)
        self.assertEqual(page["blocks"][0]["content"], "original")

    def test_the_same_text_read_again_promotes_confidence_without_a_rewrite(self):
        page = normalize_recognition({"blocks": [uncertain(1, "Zellwand")]})
        self.assertEqual(merge_region_review(page, {1: {"content": "Zellwand", "confidence": 0.95}}), 0)
        block = page["blocks"][0]
        self.assertEqual(block["confidence_status"], "high")
        self.assertNotIn("previous_reading", block)

    def test_a_stale_suggested_correction_cannot_survive_a_re_read(self):
        # suggested_corrections copies a block's text, so it has to be rebuilt or the
        # review page shows the reading the second look already replaced.
        page = normalize_recognition({"blocks": [
            dict(uncertain(1, "Photosyn???"), suggested_correction="Photosynthesis")]})
        self.assertEqual(page["suggested_corrections"][0]["content"], "Photosyn???")
        merge_region_review(page, {1: {"content": "Photosynthese", "confidence": 0.95}})
        self.assertEqual(page["suggested_corrections"][0]["content"], "Photosynthese")

    def test_the_page_summary_is_rebuilt_so_nothing_goes_out_of_step(self):
        page = normalize_recognition({"blocks": [uncertain(1, "aaa"), uncertain(2, "bbb")]})
        self.assertEqual(len(page["uncertain_regions"]), 2)
        merge_region_review(page, {1: {"content": "Alpha", "confidence": 0.95},
                                   2: {"content": "Beta", "confidence": 0.95}})
        self.assertIn("Alpha", page["text"])            # extracted text follows the blocks
        self.assertEqual(page["uncertain_regions"], [])  # nothing is unclear any more
        self.assertEqual(page["confidence_status"], "high")


# --------------------------------------------------------------- end to end, in app
class SecondLookFlowTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, FEATURE_HANDWRITING_SECOND_LOOK=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "mira", "email": "mira@example.com", "password": "correct-horse-battery"})

    def _one_page_project(self):
        response = self.client.post("/projects", data={
            "title": "Notes", "subject": "Biology",
            "materials": [(io.BytesIO(page_png((1200, 1600))), "note.png", "image/png")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.scalar(
                application.db.select(application.LearningProject))
            page = application.db.session.scalar(application.db.select(application.ProjectPage))
            return project.id, page.id

    def _recognize(self, project_id, page_id, responses):
        with patch.object(application, "create_response", side_effect=responses) as mocked:
            response = self.client.post(
                f"/projects/{project_id}/pages/{page_id}/recognize", json={})
        return response, mocked

    @staticmethod
    def _page_payload(confidence):
        return {"blocks": [dict(uncertain(1, "Photosyn???", confidence=confidence))],
                "detected_page_number": "", "warning": ""}

    def test_an_unclear_word_is_re_read_and_corrected(self):
        project_id, page_id = self._one_page_project()
        response, mocked = self._recognize(project_id, page_id, [
            FakeResponse(self._page_payload(0.6)),
            FakeResponse({"regions": [{"index": 1, "content": "Photosynthese", "confidence": 0.93}]}),
        ])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_count, 2)  # one page read, one close-up read
        self.assertEqual(mocked.call_args_list[1].kwargs["task_type"], "handwriting_region_review")
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            assert page is not None
            self.assertIn("Photosynthese", page.extracted_text)
            self.assertGreaterEqual(page.recognition_confidence, 0.9)

    def test_a_confident_page_costs_no_extra_call(self):
        project_id, page_id = self._one_page_project()
        response, mocked = self._recognize(
            project_id, page_id, [FakeResponse(self._page_payload(0.95))])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_count, 1)

    def test_the_flag_turns_the_extra_call_off(self):
        application.app.config["FEATURE_HANDWRITING_SECOND_LOOK"] = False
        try:
            project_id, page_id = self._one_page_project()
            response, mocked = self._recognize(
                project_id, page_id, [FakeResponse(self._page_payload(0.6))])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(mocked.call_count, 1)
        finally:
            application.app.config["FEATURE_HANDWRITING_SECOND_LOOK"] = True

    def test_a_failed_second_look_leaves_the_first_reading_intact(self):
        """An optional improvement must never cost the student a page they could have had."""
        project_id, page_id = self._one_page_project()
        response, _mocked = self._recognize(project_id, page_id, [
            FakeResponse(self._page_payload(0.6)),
            RuntimeError("provider exploded"),
        ])
        self.assertEqual(response.status_code, 200)
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            assert page is not None
            self.assertEqual(page.extraction_status, "ready")
            self.assertIn("Photosyn???", page.extracted_text)


if __name__ == "__main__":
    unittest.main()

"""Handwriting recognition regression tests.

Covers the Project-mode upload -> preprocess -> multi-variant vision OCR -> validation
flow, focused on the reported bug: readable handwritten notebook photos were rejected as
"too small or unclear". These tests assert small handwriting is NOT rejected, that the
whole page is only rejected when every variant is empty, and that partial/uncertain
results survive with editable review + suggested corrections.
"""

import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

TEST_DATABASE = tempfile.gettempdir() + "/learnova_handwriting_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ocr.service import (  # noqa: E402
    _CV2_AVAILABLE,
    apply_recognition_variant,
    deskew_document_image,
    normalize_recognition,
    preprocess_document_image,
    preprocess_variants,
    recognition_instructions,
)
from learnova.ai_services.service import parse_json  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.output_text = json.dumps(payload)
        self.usage = None
        self.model = "test-model"


def rec_payload(blocks):
    return {"blocks": blocks, "detected_page_number": "", "warning": ""}


EMPTY = rec_payload([])


def block(content, confidence, btype="handwriting", suggestion=""):
    return {
        "type": btype, "content": content, "confidence": confidence,
        "bbox": [0.05, 0.05, 0.95, 0.15], "crossed_out": False,
        "important_candidate": False, "teacher_highlight_candidate": False,
        "nearby_text": "", "suggested_correction": suggestion,
    }


def png(size, background="white", draw_fn=None):
    image = Image.new("RGB", size, background)
    if draw_fn:
        draw_fn(ImageDraw.Draw(image), size)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _pixel_std(data: bytes) -> float:
    from PIL import ImageStat
    return ImageStat.Stat(Image.open(io.BytesIO(data)).convert("L")).stddev[0]


# ------------------------------------------------------------------ preprocessing
class PreprocessingTests(unittest.TestCase):
    def test_high_resolution_photo_is_not_downscaled(self):
        # Small handwriting in a high-res photo: resolution is large, text is small.
        # The image must NOT be shrunk before OCR (that would destroy the small text).
        source = png((1800, 2400))
        processed = preprocess_document_image(source, {"autocrop": False})
        self.assertEqual(processed.source_width, 1800)
        self.assertGreaterEqual(processed.width, 1800)   # never downscaled
        self.assertEqual(processed.upscaled, 1.0)         # already large -> no resize

    def test_small_image_is_upscaled_two_to_four_times(self):
        source = png((500, 650), draw_fn=lambda d, s: d.text((20, 20), "kleine Handschrift", fill="black"))
        processed = preprocess_document_image(source, {"autocrop": False})
        self.assertGreater(processed.width, 500)          # upscaled
        self.assertGreaterEqual(processed.upscaled, 2.0)  # 2x-4x
        self.assertLessEqual(processed.upscaled, 4.0)
        self.assertGreaterEqual(min(processed.width, processed.height), 1400)

    def test_low_contrast_is_enhanced(self):
        def halves(draw, size):
            draw.rectangle((0, 0, size[0] // 2, size[1]), fill=(120, 120, 120))
            draw.rectangle((size[0] // 2, 0, size[0], size[1]), fill=(138, 138, 138))
        source = png((900, 700), background=(128, 128, 128), draw_fn=halves)
        processed = preprocess_document_image(source, {"autocrop": False})
        self.assertGreater(_pixel_std(processed.data), _pixel_std(source))  # contrast increased

    def test_exif_orientation_is_corrected(self):
        landscape = Image.new("RGB", (900, 600), "white")
        exif = landscape.getexif()
        exif[274] = 6  # Orientation = rotate 90° CW on display -> becomes portrait
        buf = io.BytesIO(); landscape.save(buf, format="JPEG", exif=exif)
        processed = preprocess_document_image(buf.getvalue(), {"autocrop": False})
        self.assertGreater(processed.height, processed.width)  # orientation applied -> portrait

    def test_three_variants_enhanced_grayscale_threshold(self):
        source = png((900, 700), draw_fn=lambda d, s: d.text((30, 30), "Notes", fill="black"))
        variants = preprocess_variants(source)
        self.assertEqual([v.mode for v in variants], ["enhanced", "grayscale", "threshold"])
        # threshold variant is binarised: only near-black / near-white remain
        thresh = Image.open(io.BytesIO(variants[2].data)).convert("L")
        distinct = {p for p in thresh.getdata()}
        self.assertTrue(distinct <= {0, 255} or min(distinct) < 20 and max(distinct) > 235)

    def test_notebook_lines_and_two_columns_do_not_break(self):
        def lines(draw, size):
            for y in range(60, size[1], 40):
                draw.line((30, y, size[0] - 30, y), fill=(200, 200, 255), width=1)
            draw.text((40, 65), "Handwritten line one", fill="black")
        def two_columns(draw, size):
            draw.line((size[0] // 2, 20, size[0] // 2, size[1] - 20), fill=(180, 180, 180), width=2)
            draw.text((40, 40), "Left column", fill="black")
            draw.text((size[0] // 2 + 40, 40), "Right column", fill="black")
        for draw_fn in (lines, two_columns):
            processed = preprocess_document_image(png((1000, 1300), draw_fn=draw_fn))
            self.assertTrue(processed.data)
            self.assertGreaterEqual(min(processed.width, processed.height), 200)

    def test_recognition_prompt_requests_structure_and_no_invention(self):
        prompt = recognition_instructions("History", 1)
        for needle in ("handwriting", "headings", "tables", "Never invent", "suggested_correction"):
            self.assertIn(needle, prompt)


# --------------------------------------------------------------- normalization
class NormalizationTests(unittest.TestCase):
    def test_partial_and_uncertain_blocks_are_kept_not_rejected(self):
        result = normalize_recognition(rec_payload([
            block("Clear heading", 0.95, "heading"),
            block("mabye recieve", 0.35, "handwriting", suggestion="maybe receive"),
        ]))
        self.assertTrue(result["readable"])                        # partial text -> readable
        self.assertIn("Clear heading", result["text"])
        self.assertEqual(len(result["suggested_corrections"]), 1)
        self.assertEqual(result["suggested_corrections"][0]["suggestion"], "maybe receive")
        self.assertTrue(any(r["content"] == "mabye recieve" for r in result["uncertain_regions"]))

    def test_low_confidence_handwriting_labelled_review_not_unclear(self):
        # Relaxed handwriting thresholds: 0.5 confidence should be "review", not "unclear".
        result = normalize_recognition(rec_payload([block("scribbled note", 0.5)]))
        self.assertEqual(result["blocks"][0]["confidence_status"], "review")


# ------------------------------------------------------------------- OCR flow
class RecognitionFlowTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "alice", "email": "alice@example.com", "password": "correct-horse-battery"})

    def _one_page_project(self, image=None):
        image = image or png((900, 1200), draw_fn=lambda d, s: d.text((40, 60), "Handwritten notes", fill="black"))
        response = self.client.post("/projects", data={
            "title": "Notes", "subject": "History",
            "materials": [(io.BytesIO(image), "note.png", "image/png")],
        }, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302)
        with application.app.app_context():
            project = application.db.session.scalar(application.db.select(application.LearningProject))
            page = application.db.session.scalar(application.db.select(application.ProjectPage))
            return project.id, page.id

    def _recognize(self, project_id, page_id, mode=None):
        body = {"mode": mode} if mode else {}
        return self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json=body)

    def test_readable_handwriting_is_accepted(self):
        project_id, page_id = self._one_page_project()
        with patch.object(application, "create_response", return_value=FakeResponse(
            rec_payload([block("Die Handschrift ist lesbar.", 0.72)]))):
            response = self._recognize(project_id, page_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "ready_for_review")
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            self.assertEqual(page.extraction_status, "ready")
            self.assertIn("lesbar", page.extracted_text)

    def test_partial_text_prevents_whole_page_rejection(self):
        project_id, page_id = self._one_page_project()
        # Only one low-confidence block readable; the page must NOT be rejected.
        with patch.object(application, "create_response", return_value=FakeResponse(
            rec_payload([block("only this line is clear", 0.32)]))):
            response = self._recognize(project_id, page_id)
        self.assertEqual(response.status_code, 200)
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            self.assertEqual(page.extraction_status, "ready")
            self.assertIn("only this line", page.extracted_text)

    def test_uncertain_word_output_with_suggested_correction_persisted_and_shown(self):
        project_id, page_id = self._one_page_project()
        with patch.object(application, "create_response", return_value=FakeResponse(rec_payload([
            block("Heading", 0.95, "heading"),
            block("recieve", 0.3, "handwriting", suggestion="receive"),
        ]))):
            self._recognize(project_id, page_id)
        with application.app.app_context():
            suggestion = application.db.session.scalar(application.db.select(application.DocumentBlock).where(
                application.DocumentBlock.suggested_correction != ""))
            self.assertIsNotNone(suggestion)
            self.assertEqual(suggestion.suggested_correction, "receive")
        review = self.client.get(f"/projects/{project_id}/review").get_data(as_text=True)
        self.assertIn("Suggested:", review)
        self.assertIn("receive", review)

    def test_whole_page_rejected_only_when_all_variants_empty_with_specific_guidance(self):
        project_id, page_id = self._one_page_project()
        with patch.object(application, "create_response", return_value=FakeResponse(EMPTY)) as mocked:
            response = self._recognize(project_id, page_id)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(mocked.call_count, 3)  # tried enhanced + grayscale + threshold
        error = response.get_json()["error"]
        self.assertIn("grayscale", error)
        self.assertIn("Retry", error)
        self.assertNotIn("too small", error.lower())

    def test_multi_variant_escalation_recovers_text(self):
        project_id, page_id = self._one_page_project()
        # enhanced + grayscale empty, threshold finally reads the page.
        responses = [FakeResponse(EMPTY), FakeResponse(EMPTY),
                     FakeResponse(rec_payload([block("recovered by high contrast", 0.8)]))]
        with patch.object(application, "create_response", side_effect=responses) as mocked:
            response = self._recognize(project_id, page_id)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_count, 3)
        with application.app.app_context():
            page = application.db.session.get(application.ProjectPage, page_id)
            self.assertEqual(page.extraction_status, "ready")
            self.assertIn("recovered", page.extracted_text)

    def test_retry_with_forced_mode_uses_single_variant(self):
        project_id, page_id = self._one_page_project()
        with patch.object(application, "create_response", return_value=FakeResponse(
            rec_payload([block("grayscale read", 0.7)]))) as mocked:
            response = self._recognize(project_id, page_id, mode="grayscale")
        self.assertEqual(response.status_code, 200)
        # Count page reads, not total calls: a 0.7 reading is below CONFIDENCE_HIGH, so
        # the close-up second look legitimately follows it. What this test guards is that
        # the *variant* search stopped at the one the student forced.
        page_reads = [call for call in mocked.call_args_list
                      if call.kwargs.get("task_type") == "ocr_document_recognition"]
        self.assertEqual(len(page_reads), 1)  # only the forced variant is tried


# ---------------------------------------------------------- frontend upload
class FrontendUploadTests(unittest.TestCase):
    def test_scanner_captures_lossless_full_resolution_without_compression(self):
        from pathlib import Path
        scanner = Path(__file__).resolve().parent.parent.joinpath("static", "scanner.js").read_text(encoding="utf-8")
        self.assertIn("cameraVideo.videoWidth", scanner)   # capture at full sensor resolution
        self.assertIn("cameraVideo.videoHeight", scanner)
        self.assertIn('"image/png"', scanner)               # lossless PNG capture
        self.assertNotIn('"image/jpeg"', scanner)           # no lossy re-encode
        self.assertNotIn("0.8)", scanner)                   # no JPEG quality downscaling

    @unittest.skipUnless(_CV2_AVAILABLE, "OpenCV not installed")
    def test_deskew_straightens_tilted_page(self):
        # A page tilted ~13° (the case that made the vision model fail) is corrected.
        page = Image.new("RGB", (1400, 1000), "white")
        d = ImageDraw.Draw(page)
        for i in range(6):
            d.text((120, 120 + i * 120), "A clearly written handwritten line here", fill="black")
        tilted = page.rotate(13, expand=True, fillcolor="white")
        result, note = deskew_document_image(tilted)
        self.assertTrue(note, "expected a deskew/perspective correction note")
        self.assertIn("deskew", note.lower() + "perspective")  # one of the two paths fired

    @unittest.skipUnless(_CV2_AVAILABLE, "OpenCV not installed")
    def test_deskew_is_noop_on_straight_page(self):
        page = Image.new("RGB", (1200, 900), "white")
        d = ImageDraw.Draw(page)
        for i in range(5):
            d.text((80, 90 + i * 120), "Straight aligned line of text", fill="black")
        result, note = deskew_document_image(page)
        self.assertEqual(note, "")  # nothing meaningful to correct

    def test_parse_json_survives_reasoning_and_prose(self):
        self.assertEqual(parse_json('<think>let me look…</think>\n{"blocks": []}'), {"blocks": []})
        self.assertEqual(parse_json('Sure, here it is: {"a": 1} — done.'), {"a": 1})
        self.assertEqual(parse_json('```json\n{"b": 2}\n```'), {"b": 2})

    def test_apply_recognition_variant_grayscale(self):
        source = png((800, 600), draw_fn=lambda d, s: d.text((20, 20), "x", fill="black"))
        enhanced = preprocess_document_image(source)
        gray = apply_recognition_variant(enhanced.data, "grayscale")
        out = Image.open(io.BytesIO(gray.data)).convert("RGB")
        r, g, b = out.getpixel((5, 5))
        self.assertTrue(abs(r - g) <= 2 and abs(g - b) <= 2)


if __name__ == "__main__":
    unittest.main()

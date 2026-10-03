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

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ai_services.contracts import (  # noqa: E402
    BLOCK_TYPES, AIValidationError, validate_output,
)
from learnova.ai_services.prompts import PROMPT_VERSIONS  # noqa: E402
from learnova.ocr.service import (  # noqa: E402
    ALLOWED_BLOCK_TYPES, native_text_quality, recognition_from_text,
    recognition_instructions,
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
        # The fixes must not turn the validator off.
        for payload, label in (("not json at all", "prose"),
                               (page_json([{"type": "handwriting"}]), "a block with no content"),
                               (page_json([blk("interpretive_dance")]), "an invented type"),
                               ('{"blocks": [{"type": "handwriting", "content": "x",'
                                ' "confidence": 0.5, "bbox": [0.1, 0.2]}]}', "a short bbox")):
            with self.assertRaises(AIValidationError, msg=label):
                validate_output("ocr_document_recognition", payload, OCR_VERSION, {})


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

    def test_a_scanned_pdf_still_goes_to_the_model(self):
        project_id, page_id = self._project_page(self._pdf(), "scan.pdf")
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
        self.assertEqual(ai_service.DETERMINISTIC_TASKS,
                         {"ocr_document_recognition", "handwriting_region_review"})

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

"""Reading honesty (FIX_PLAN PF3): D3 retry, D4 provisional offline OCR, D1 daily limit, D6 duplicates.

Live run of 2026-11-01 (prod/live_docs.md):

* D3 - "Try reading again" returned the cached reading: `ocr_document_recognition` is a
  deterministic, cached task and the retry sent the same picture, so the misreading came
  back for free and unchanged. A retry now starts with another image mode and asks the
  provider again (the gateway's `fresh`), and the new reading is what the cache holds.
* D4 - after a 429 a photo page fell back to the offline OCR at once and was stored as
  read for good; after a later successful re-read its "Read with offline text
  recognition" warning stayed on the page. The offline reading is now provisional: vision
  reads it again later (bounded), a typed correction or a confirmation ends that, and a
  new reading drops the old reading's warning.
* D1 - the provider's daily limit: the page waits an hour between attempts and says so,
  instead of being knocked on again every five minutes.
* D6 - only 2 of the 4 duplicate topic pairs of the six-document project were suggested
  for merging (a one-word title inside a longer one had Jaccard 1/3 < 0.34).

Every model call is patched; no provider is reached.
"""

import io
import json
import os
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_reading_honesty_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from PIL import Image, ImageDraw  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.ocr.local import LocalOcrResult  # noqa: E402
from learnova.projects.topics import overlap_suggestions, topic_key  # noqa: E402
from learnova.vocabulary.layout import Line  # noqa: E402

db = application.db
_MISSING = object()
SIX_DOCS = Path(__file__).resolve().parent / "fixtures" / "six_docs_topics_2026-11-01.json"
OFFLINE_WARNING = "Read with offline text recognition while the AI was busy."
DAILY_MESSAGE = "The AI has reached its daily limit for reading pages."


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, text, confidence=0.93):
        self.output_text = json.dumps({"blocks": [{
            "type": "handwriting", "content": text, "confidence": confidence, "bbox": [],
            "crossed_out": False}], "detected_page_number": "", "warning": ""}, ensure_ascii=False)


def rate_limited(**_kwargs):
    raise ai_service.AIProviderError("provider_rate_limit", "The AI provider rate limit was reached.")


def daily_limited(**_kwargs):
    raise ai_service.AIProviderError("provider_daily_limit", "The AI provider's daily limit was reached.")


def png(label="Tafelbild"):
    image = Image.new("RGB", (900, 1200), "white")
    draw = ImageDraw.Draw(image)
    for row in range(8):
        draw.text((60, 80 + 60 * row), f"{label} {row}", fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


OFFLINE = LocalOcrResult(lines=[
    Line("Die Wuerde des Menschen ist unantastbar.", (0.1, 0.1, 0.9, 0.15), 0.93),
    Line("Artikel 1 Grundgesetz", (0.1, 0.2, 0.6, 0.25), 0.88)], width=900, height=1200,
    engine="rapidocr", duration_ms=5)


class Base(unittest.TestCase):
    CONFIG_KEYS = ("TESTING", "AI_MODE", "WTF_CSRF_ENABLED", "PROJECT_LOCAL_OCR_IN_TESTS", "RUN_LIVE_AI_TEST",
                   "ALLOW_LIVE_AI", "AI_USAGE_PATH", "AI_CACHE_DIR", "FEATURE_HANDWRITING_SECOND_LOOK")

    def setUp(self):
        self.saved_config = {key: application.app.config.get(key, _MISSING) for key in self.CONFIG_KEYS}
        application.app.config.update(TESTING=True, AI_MODE="cached", WTF_CSRF_ENABLED=False)
        application.SESSIONS.clear()
        ai_service._RECENT_RESULTS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "mila", "email": "mila@example.com",
                                            "password": "correct-horse-battery", "language": "en"})

    def tearDown(self):
        for key, value in self.saved_config.items():
            if value is _MISSING:
                application.app.config.pop(key, None)
            else:
                application.app.config[key] = value

    def upload_photo(self, name="tafel.png"):
        response = self.client.post("/projects", data={
            "title": "Ethik", "subject": "Other", "subject_other": "Ethik",
            "materials": [(io.BytesIO(png()), name, "image/png")]}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        with application.app.app_context():
            project = db.session.scalars(db.select(application.LearningProject).order_by(
                application.LearningProject.id.desc())).first()
            assert project is not None
            page = db.session.scalars(db.select(application.ProjectPage).where(
                application.ProjectPage.project_id == project.id)).first()
            assert page is not None
            return project.id, page.id

    def recognize(self, project_id, page_id, *, side_effect, body=None):
        with patch.object(application, "create_response", side_effect=side_effect) as mocked, \
                patch.object(application.ai_service, "remaining_hourly_requests", return_value=40):
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize", json=body or {})
        return response, mocked

    def page(self, page_id):
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            assert page is not None
            db.session.expunge(page)
            return page

    def saved(self, page_id):
        return json.loads(self.page(page_id).recognition_json)

    def age_note(self, page_id, key, minutes, field="since"):
        """Move a page's bookkeeping time `minutes` into the past."""

        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            assert page is not None
            saved = json.loads(page.recognition_json)
            moment = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
            if field:
                saved[key][field] = moment
            else:
                saved[key] = moment
            page.recognition_json = json.dumps(saved)
            db.session.commit()

    def start_page_ids(self, project_id):
        html = self.client.get(f"/projects/{project_id}/start").get_data(as_text=True)
        match = re.search(r'data-page-ids="([^"]*)"', html)
        assert match is not None, "the start page lists the pages it reads"
        return json.loads(match.group(1).replace("&#34;", '"').replace("&quot;", '"'))


# --------------------------------------------------------------------------- D3


class RetryReadsAgainTests(Base):
    def test_a_retry_starts_with_another_mode_and_is_never_served_from_the_cache(self):
        project_id, page_id = self.upload_photo()
        self.recognize(project_id, page_id, side_effect=[FakeResponse("Erste Lesung")])
        self.assertEqual(self.saved(page_id)["variant"], "enhanced")
        modes = []
        original = application.apply_recognition_variant

        def spy(data, mode):
            modes.append(mode)
            return original(data, mode)

        with patch.object(application, "apply_recognition_variant", side_effect=spy):
            response, mocked = self.recognize(project_id, page_id, body={"retry": True},
                                              side_effect=[FakeResponse("Zweite Lesung")])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked.call_count, 1, "a good first mode is enough")
        self.assertEqual(modes, ["grayscale"], "the stored reading's mode (enhanced) goes last")
        self.assertIs(mocked.call_args.kwargs.get("fresh"), True, "an explicit retry skips the cache")
        self.assertEqual(self.page(page_id).extracted_text, "Zweite Lesung")
        self.assertEqual(self.saved(page_id)["variant"], "grayscale")

    def test_an_automatic_read_still_uses_the_cache(self):
        project_id, page_id = self.upload_photo()
        _, mocked = self.recognize(project_id, page_id, side_effect=[FakeResponse("Erste Lesung")])
        self.assertNotIn("fresh", mocked.call_args.kwargs, "only a retry asks for a fresh reading")


class RetryThroughTheGatewayTests(Base):
    """The real gateway in live mode with the provider patched: the D3 reproduction."""

    def setUp(self):
        super().setUp()
        application.app.config.update(RUN_LIVE_AI_TEST=True, ALLOW_LIVE_AI=True, AI_USAGE_PATH="",
                                      AI_CACHE_DIR=tempfile.mkdtemp(prefix="learnova-pf3-cache-"), AI_MODE="live",
                                      FEATURE_HANDWRITING_SECOND_LOOK=False)

    def read(self, project_id, page_id, outputs, *, retry=False):
        class Usage:
            input_tokens, output_tokens, total_tokens = 1000, 200, 1200

        def provider(**_kwargs):
            text = outputs.pop(0)
            return type("Response", (), {"output_text": FakeResponse(text).output_text, "model": "m",
                                         "usage": Usage})()

        records = []
        with patch.object(ai_service, "_provider_response", side_effect=provider) as called, \
                patch.object(ai_service, "_assert_usage_limits"), \
                patch.object(ai_service, "_record_usage", side_effect=records.append), \
                patch.object(application.ai_service, "remaining_hourly_requests", return_value=40):
            response = self.client.post(f"/projects/{project_id}/pages/{page_id}/recognize",
                                        json={"retry": True} if retry else {})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        return called.call_count, [record.get("event_kind") for record in records
                                   if record.get("task_type") == "ocr_document_recognition"]

    def test_try_reading_again_really_reads_again(self):
        project_id, page_id = self.upload_photo()
        calls, events = self.read(project_id, page_id, ["Erste Lesung"])
        self.assertEqual((calls, events), (1, ["call"]))
        calls, events = self.read(project_id, page_id, ["Zweite Lesung"], retry=True)
        self.assertEqual((calls, events), (1, ["call"]), "live: the retry was a cache_hit of the first reading")
        self.assertEqual(self.page(page_id).extracted_text, "Zweite Lesung")
        # The next retry starts with the enhanced picture again - the one the cache has a
        # reading of - and still reaches the provider.
        calls, events = self.read(project_id, page_id, ["Dritte Lesung"], retry=True)
        self.assertEqual((calls, events), (1, ["call"]))
        self.assertEqual((self.page(page_id).extracted_text, self.saved(page_id)["variant"]),
                         ("Dritte Lesung", "enhanced"))

    def test_the_fresh_reading_is_what_the_cache_holds_afterwards(self):
        # Gateway semantics the retry relies on (the `fresh` seam already existed for the
        # chat's Regenerate): a second identical request is a cache hit; a fresh one asks
        # the provider and replaces the cached answer.
        def call(text, **extra):
            class Usage:
                input_tokens, output_tokens, total_tokens = 10, 20, 30
            reply = type("Response", (), {"output_text": FakeResponse(text).output_text, "model": "m",
                                          "usage": Usage})()
            with application.app.test_request_context("/"):
                with patch.object(ai_service, "_provider_response", return_value=reply) as provider, \
                        patch.object(ai_service, "_assert_usage_limits"):
                    answer = ai_service.create_response(
                        task_type="ocr_document_recognition", language="German", model="m",
                        instructions="read the page", input="the same page", max_output_tokens=100, **extra)
                    return provider.call_count, json.loads(answer.output_text)["blocks"][0]["content"]

        self.assertEqual(call("eins"), (1, "eins"))
        self.assertEqual(call("zwei"), (0, "eins"), "identical page: served from the cache")
        self.assertEqual(call("drei", fresh=True), (1, "drei"))
        self.assertEqual(call("vier"), (0, "drei"), "the fresh reading replaced the cached one")


# --------------------------------------------------------------------------- D4


class ProvisionalOfflineReadingTests(Base):
    def setUp(self):
        super().setUp()
        application.app.config["PROJECT_LOCAL_OCR_IN_TESTS"] = True

    def offline_after(self, failure):
        project_id, page_id = self.upload_photo()
        with patch.object(application.local_ocr, "available", return_value=True), \
                patch.object(application.local_ocr, "recognize", return_value=OFFLINE):
            response, mocked = self.recognize(project_id, page_id, side_effect=failure)
        return project_id, page_id, response, mocked

    def test_offline_ocr_after_a_rate_limit_is_provisional(self):
        project_id, page_id, response, mocked = self.offline_after(rate_limited)
        self.assertEqual(mocked.call_count, 1, "a provider-wide error stops the image modes")
        self.assertEqual((response.status_code, response.get_json()["state"]), (200, "needs_review"))
        page, saved = self.page(page_id), self.saved(page_id)
        self.assertEqual((page.extraction_status, saved["text_source"]), ("ready", "local_ocr"))
        self.assertIn("Wuerde des Menschen", page.extracted_text, "its topics can flow meanwhile")
        self.assertIn(OFFLINE_WARNING, page.warning)
        self.assertEqual(saved["vision_pending"]["waits"], 0)
        self.assertIn("since", saved["vision_pending"])

    def test_vision_reads_it_again_later_not_right_away(self):
        project_id, page_id, _, _ = self.offline_after(rate_limited)
        self.age_note(page_id, "vision_pending", 1)
        self.assertNotIn(page_id, self.start_page_ids(project_id), "not one minute later")
        response, mocked = self.recognize(project_id, page_id, side_effect=[FakeResponse("nie")])
        self.assertEqual((mocked.call_count, response.get_json().get("already_read")), (0, True))
        self.age_note(page_id, "vision_pending", 6)
        self.assertIn(page_id, self.start_page_ids(project_id), "six minutes later it is due")

    def test_a_successful_re_read_replaces_it_and_its_warning(self):
        project_id, page_id, _, _ = self.offline_after(rate_limited)
        self.age_note(page_id, "vision_pending", 6)
        response, mocked = self.recognize(project_id, page_id, side_effect=[
            FakeResponse("Die Würde des Menschen ist unantastbar. Artikel 1 Grundgesetz")])
        self.assertEqual((response.status_code, mocked.call_count, response.get_json()["state"]), (200, 1, "read"))
        page, saved = self.page(page_id), self.saved(page_id)
        self.assertEqual(saved["text_source"], "vision")
        self.assertNotIn("vision_pending", saved)
        self.assertNotIn("offline text recognition", page.warning, "the old reading's warning went with it")
        self.assertIn("Würde", page.extracted_text)
        self.assertNotIn(page_id, self.start_page_ids(project_id))

    def test_a_failed_re_read_keeps_the_reading_and_stops_after_three(self):
        project_id, page_id, _, _ = self.offline_after(rate_limited)
        for attempt in range(1, application.MAX_PROVIDER_WAITS + 1):
            self.age_note(page_id, "vision_pending", 6)
            self.assertIn(page_id, self.start_page_ids(project_id))
            response, mocked = self.recognize(project_id, page_id, side_effect=rate_limited)
            self.assertEqual((response.status_code, mocked.call_count), (202, 1))
            body = response.get_json()
            self.assertEqual((body["state"], body["code"]), ("waiting", "recognition_waiting"),
                             "the start page stops reading while the AI is busy")
            page, saved = self.page(page_id), self.saved(page_id)
            self.assertEqual((page.extraction_status, saved["text_source"]), ("ready", "local_ocr"))
            self.assertIn("Wuerde des Menschen", page.extracted_text)
            self.assertEqual(saved["vision_pending"]["waits"], attempt)
        self.age_note(page_id, "vision_pending", 60)
        self.assertNotIn(page_id, self.start_page_ids(project_id), "bounded: no fourth automatic re-read")

    def test_a_typed_correction_is_never_read_again(self):
        project_id, page_id, _, _ = self.offline_after(rate_limited)
        response = self.client.post(f"/projects/{project_id}/pages/{page_id}/correct",
                                    data={"text": "Die Würde des Menschen ist unantastbar."})
        self.assertEqual(response.status_code, 302)
        saved = self.saved(page_id)
        self.assertEqual(saved["text_source"], "manual")
        self.assertNotIn("vision_pending", saved)
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            assert page is not None
            saved_json = json.loads(page.recognition_json)
            saved_json["vision_pending"] = {"since": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                                            "waits": 0}
            page.recognition_json = json.dumps(saved_json)   # even with a stale note left behind
            db.session.commit()
        self.assertNotIn(page_id, self.start_page_ids(project_id))
        response, mocked = self.recognize(project_id, page_id, side_effect=[FakeResponse("nie")])
        self.assertEqual((mocked.call_count, response.get_json().get("already_read")), (0, True))

    def test_a_non_temporary_failure_keeps_the_old_wording(self):
        # Vision answered, but with nothing usable: the offline reading is all there is, and
        # no later vision read is promised.
        invalid = ai_service.AIValidationError("schema_validation", "response was empty")
        _, page_id, response, mocked = self.offline_after([invalid] * 3)
        self.assertEqual(mocked.call_count, 3)
        self.assertIn("Read with offline text recognition — please check it.", self.page(page_id).warning)
        self.assertNotIn("vision_pending", self.saved(page_id))


class StaleWarningTests(Base):
    def test_a_new_reading_drops_the_last_readings_warning_but_keeps_advisories(self):
        project_id, page_id = self.upload_photo()
        with application.app.app_context():
            page = db.session.get(application.ProjectPage, page_id)
            assert page is not None
            page.extraction_status = "ready"
            page.extracted_text = "alt"
            page.warning = "Possible glare detected. Read with offline text recognition — please check it."
            page.recognition_json = json.dumps({"text_source": "local_ocr", "blocks": [{"order": 1}],
                                                "warning": "Read with offline text recognition — please check it."})
            db.session.commit()
        self.recognize(project_id, page_id, body={"retry": True}, side_effect=[FakeResponse("neu gelesen")])
        warning = self.page(page_id).warning
        self.assertNotIn("offline text recognition", warning)
        self.assertIn("Possible glare detected.", warning, "a picture advisory is no reading's warning")


PAUSE_MESSAGE = "The AI is busy right now. Learnova read this page offline for now and paused reading the other pages"


class BusyProviderStopsTheRunTests(Base):
    """Review B B-1 (spec §2C: do not repeatedly retry requests that already reached a rate
    limit). With the offline OCR available - production installs it - a rate-limited vision
    model answered with a provisional offline reading, which counted as a success: the start
    page, Continue reading, quick-start and /reanalyze all went on to the next photo and asked
    the busy model again, one refused request per page."""

    def setUp(self):
        super().setUp()
        application.app.config["PROJECT_LOCAL_OCR_IN_TESTS"] = True
        self.ocr_calls = 0

    def upload_photos(self, count=4):
        response = self.client.post("/projects", data={
            "title": "Ethik", "subject": "Other", "subject_other": "Ethik",
            "materials": [(io.BytesIO(png(f"Tafel {number}")), f"tafel-{number}.png", "image/png")
                          for number in range(count)]}, content_type="multipart/form-data")
        self.assertEqual(response.status_code, 302, response.get_data(as_text=True)[:300])
        with application.app.app_context():
            project = db.session.scalars(db.select(application.LearningProject).order_by(
                application.LearningProject.id.desc())).first()
            assert project is not None
            return project.id, [page.id for page in sorted(project.pages, key=lambda item: item.page_order)]

    def model(self, *, task_type, **_kwargs):
        if task_type in ("ocr_document_recognition", "handwriting_region_review"):
            self.ocr_calls += 1
            return rate_limited()
        if task_type == "project_section_generation":
            raise ai_service.AIProviderError("provider_rate_limit", "The AI provider rate limit was reached.")
        raise AssertionError(f"unexpected AI task {task_type}")

    def post(self, path, **kwargs):
        with patch.object(application.local_ocr, "available", return_value=True), \
                patch.object(application.local_ocr, "recognize", return_value=OFFLINE), \
                patch.object(application, "create_response", side_effect=self.model), \
                patch.object(application.ai_service, "remaining_hourly_requests", return_value=40):
            return self.client.post(path, **kwargs)

    def statuses(self, page_ids):
        with application.app.app_context():
            return [db.session.get(application.ProjectPage, page_id).extraction_status for page_id in page_ids]

    def test_the_start_page_is_told_to_pause_after_a_busy_offline_reading(self):
        project_id, page_ids = self.upload_photos()
        response = self.post(f"/projects/{project_id}/pages/{page_ids[0]}/recognize", json={})
        body = response.get_json()
        self.assertEqual((response.status_code, body["state"]), (200, "needs_review"), "the page itself was read")
        self.assertTrue(body.get("paused"), "project-start.js stops sending pages")
        self.assertIn(PAUSE_MESSAGE, body.get("pause_message", ""))
        self.assertEqual(self.ocr_calls, 1)
        script = (Path(application.__file__).parent / "static" / "js" / "project-start.js").read_text(encoding="utf-8")
        self.assertIn("result.paused", script)

    def test_continue_reading_asks_the_busy_model_once(self):
        project_id, page_ids = self.upload_photos()
        response = self.post(f"/projects/{project_id}/recognize")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.ocr_calls, 1, "not one refused request per photo")
        self.assertEqual(self.statuses(page_ids), ["ready", "pending", "pending", "pending"])
        with self.client.session_transaction() as session:
            flashed = [message for _category, message in session.get("_flashes", [])]
        self.assertTrue(any(PAUSE_MESSAGE in message for message in flashed), flashed)

    def test_quick_start_asks_the_busy_model_once_and_not_again_right_away(self):
        project_id, page_ids = self.upload_photos()
        self.post(f"/projects/{project_id}/quick-start", json={}, headers={"Accept": "application/json"})
        self.assertEqual(self.ocr_calls, 1)
        self.assertEqual(self.statuses(page_ids), ["ready", "pending", "pending", "pending"])
        self.post(f"/projects/{project_id}/quick-start", json={}, headers={"Accept": "application/json"})
        self.assertEqual(self.ocr_calls, 1, "a busy reading moments ago: the provider is not asked again yet")

    def test_update_topics_asks_the_busy_model_once(self):
        project_id, page_ids = self.upload_photos()
        self.post(f"/projects/{project_id}/reanalyze")
        self.assertEqual(self.ocr_calls, 1)
        self.assertEqual(self.statuses(page_ids), ["ready", "pending", "pending", "pending"])


# --------------------------------------------------------------------------- D1


class DailyLimitTests(Base):
    def test_a_daily_limit_makes_the_page_wait_an_hour(self):
        project_id, page_id = self.upload_photo()
        response, mocked = self.recognize(project_id, page_id, side_effect=daily_limited)
        self.assertEqual((response.status_code, mocked.call_count), (202, 1))
        page, saved = self.page(page_id), self.saved(page_id)
        self.assertEqual((page.extraction_status, saved["waiting_reason"]), ("waiting", "provider_daily"))
        self.assertIn(DAILY_MESSAGE, page.warning)
        self.assertIn(DAILY_MESSAGE, response.get_json()["message"])
        self.assertEqual(page.retry_count, 0, "waiting out a daily limit never fails the page")

        # Within the hour nothing asks the provider again: not the start page's list, not
        # the per-page reading, not quick-start.
        self.age_note(page_id, "waiting_since", 30, field=None)
        self.assertNotIn(page_id, self.start_page_ids(project_id))
        response, mocked = self.recognize(project_id, page_id, side_effect=[FakeResponse("nie")])
        self.assertEqual((response.status_code, mocked.call_count), (202, 0))
        with patch.object(application, "create_response", return_value=FakeResponse("nie")) as mocked, \
                patch.object(application.ai_service, "remaining_hourly_requests", return_value=40):
            self.client.post(f"/projects/{project_id}/quick-start", json={}, headers={"Accept": "application/json"})
        ocr_calls = [call for call in mocked.call_args_list
                     if call.kwargs.get("task_type") == "ocr_document_recognition"]
        self.assertEqual(ocr_calls, [])
        report = self.client.get(f"/projects/{project_id}/analysis").get_json()
        self.assertIn(DAILY_MESSAGE, report["waiting_message"])

        self.age_note(page_id, "waiting_since", 61, field=None)
        self.assertIn(page_id, self.start_page_ids(project_id), "an hour later it is tried again")
        response, mocked = self.recognize(project_id, page_id, side_effect=[FakeResponse("Endlich gelesen")])
        self.assertEqual((response.status_code, mocked.call_count), (200, 1))
        self.assertEqual(self.page(page_id).extraction_status, "ready")

    def test_a_busy_provider_still_waits_five_minutes(self):
        project_id, page_id = self.upload_photo()
        self.recognize(project_id, page_id, side_effect=rate_limited)
        saved = self.saved(page_id)
        self.assertEqual(saved["waiting_reason"], "provider_busy")
        self.assertIn(page_id, self.start_page_ids(project_id), "'Continue reading' reads a busy page")


# --------------------------------------------------------------------------- D6


class DuplicateTopicTests(unittest.TestCase):
    def setUp(self):
        payload = json.loads(SIX_DOCS.read_text(encoding="utf-8"))
        self.topics = payload["topics"]
        self.pairs = payload["_meta"]["duplicate_pairs"]
        self.by_key = {topic["key"]: topic["title"] for topic in self.topics}

    def suggested(self):
        return {frozenset((self.by_key[item["a"]], self.by_key[item["b"]])): item
                for item in overlap_suggestions(self.topics)}

    def test_the_keys_are_todays(self):
        for topic in self.topics:
            self.assertEqual(topic["key"], topic_key(topic["title"], topic["main_topic"]))

    def test_all_four_measured_duplicate_pairs_are_suggested(self):
        suggested = self.suggested()
        for first, second in self.pairs:
            self.assertIn(frozenset((first, second)), suggested, f"{first} / {second}")
        self.assertEqual(suggested[frozenset(("Utilitarismus", "Utilitarismus – Folgenorientierte Ethik"))]["reasons"],
                         ["contained_title"])

    def test_suggestions_stay_ordered_and_bounded(self):
        found = overlap_suggestions(self.topics)
        self.assertEqual([item["score"] for item in found], sorted((item["score"] for item in found), reverse=True))
        self.assertLessEqual(len(overlap_suggestions(self.topics, limit=3)), 3)
        # The accepted false positive: the student decides.
        self.assertIn(frozenset(("Utilitarismus", "Vergleich von Utilitarismus und Pflichtethik")), self.suggested())

    def test_unrelated_titles_are_not_suggested(self):
        topics = [{"key": topic_key("Photosynthese"), "terms": [], "source_page_ids": [1]},
                  {"key": topic_key("Zellatmung"), "terms": [], "source_page_ids": [2]},
                  {"key": topic_key("Utilitarismus nach Bentham"), "terms": [], "source_page_ids": [3]},
                  {"key": topic_key("Utilitarismus nach Mill"), "terms": [], "source_page_ids": [4]}]
        pairs = [(item["a"], item["b"]) for item in overlap_suggestions(topics)]
        self.assertNotIn((topic_key("Photosynthese"), topic_key("Zellatmung")), pairs)
        for item in overlap_suggestions(topics):
            self.assertNotIn("contained_title", item["reasons"], "neither title contains the other")


if __name__ == "__main__":
    unittest.main()

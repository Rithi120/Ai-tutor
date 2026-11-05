"""PF_FIX2 F1: multi-page lessons on Groq's free tier, replayed from the final live run (offline).

Live evidence (scratchpad prod/live_final.md §3.4, fixture tests/fixtures/live_final_2026-11-02/
utilitarismus_413.json): the six-page Utilitarismus topic was refused at both ceiling settings.
Groq's "Requested" was the input plus about 2,350 tokens at max_output_tokens 5000 (on
2026-11-01 it had been input + 300-470): the full request (gateway estimate 8,619) drew "Limit
8000, Requested 10363", the app's rebuild "to the limit" (estimate 7,316) drew "Requested 9151",
and with AI_TEXT_REQUEST_TOKEN_CEILING=8000 the same fitted request was admitted, refused, and
then resent unchanged.

Pinned here, with only `OpenAI` inside the gateway replaced by a fake calibrated on the two
stored refusals (Requested = alpha x estimate + share x max_output_tokens, both numbers
computed from the fixture):
* the admission check and the notes fitting count AI_TEXT_REQUEST_OUTPUT_ALLOWANCE of the output
  cap; the output cap itself is never lowered;
* after a 413 that names Limit and Requested, the rebuild is smaller than the refused request by
  the overshoot (plus a tenth and the margin) - for the stored pair that is an estimate of at
  most 5,600, which the calibrated provider admits (about 7,554 < 8,000);
* a rebuild that cannot be that small is not sent: one refused call, the honest message;
* a one-page lesson is sent as before (nothing shortened, the topic map not shrunk).
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_live_final_limits_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import httpx  # noqa: E402
import openai  # noqa: E402
from flask_login import login_user  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.lessons import lesson_context  # noqa: E402
from tests import test_lesson_teaching_engine as teaching  # noqa: E402
from tests import test_text_request_limits as limits  # noqa: E402

db = application.db
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "live_final_2026-11-02" / "utilitarismus_413.json")
                     .read_text(encoding="utf-8"))
REQUEST = httpx.Request("POST", "http://127.0.0.1:9/openai/v1/responses")
SOURCE_HEADER = "Source material (the student's notes"
SHAPE_START = "Return valid JSON only in this shape"
LIMIT = 8000


def stored_error(call):
    """The SDK error exactly as openai built it from the stored 413 (organisation id removed)."""

    message = re.search(r"'message': '(.*?)', 'type'", call["provider_message"]).group(1)
    body = {"message": message, "type": "tokens", "code": "rate_limit_exceeded"}
    return openai.APIStatusError(f"Error code: 413 - {{'error': {body!r}}}",
                                 response=httpx.Response(413, request=REQUEST), body=body)


def gateway_estimate(text, instructions=FIXTURE["instructions"]):
    """The estimate the gateway reserved for the stored request (instructions as sent)."""

    return ai_service._estimate_tokens({"input": text, "instructions": instructions})


def stored_pairs():
    """[(gateway estimate, Groq's Requested), ...] of the two stored refusals."""

    pairs = []
    for call in FIXTURE["calls"]:
        requested = int(re.search(r"Requested (\d+)", call["provider_message"]).group(1))
        pairs.append((gateway_estimate(call["input"]), requested))
    return pairs


def calibration():
    """(alpha, share): Groq's Requested = alpha x gateway estimate + share x max_output_tokens."""

    (first_estimate, first_requested), (second_estimate, second_requested) = stored_pairs()
    alpha = (first_requested - second_requested) / (first_estimate - second_estimate)
    share = (first_requested - alpha * first_estimate) / FIXTURE["max_output_tokens"]
    return alpha, share


def groq_requested(estimate, max_output_tokens):
    alpha, share = calibration()
    return round(alpha * estimate + share * max_output_tokens)


def stored_pieces():
    """The stored six-page request split into (prefix, map block, header, pages, suffix)."""

    text = FIXTURE["calls"][0]["input"]
    map_start = text.index("TOPIC MAP (")
    header_start = text.index(SOURCE_HEADER)
    header_end = text.index("\n", header_start)
    shape = text.index(SHAPE_START)
    block = text[header_end + 1:shape].strip("\n")
    pages = []
    for index, chunk in enumerate(re.split(r"\n\n(?=\[[^\]\n]+, p\. \d+\]\n)", block), 1):
        label, _, body = chunk.partition("\n")
        pages.append(lesson_context.SourcePage(index, label, body))
    return (text[:map_start], text[map_start:header_start].rstrip("\n"), text[header_start:header_end], pages,
            text[shape:])


class CalibratedGroq(limits.GroqLikeProvider):
    """Answers like Groq's on_demand tier did on 2026-11-02: a text request whose calibrated
    "Requested" exceeds 8000 gets that 413, with the numbers in Groq's own words."""

    def __init__(self):
        super().__init__(limit=LIMIT)
        self.requested = []
        self.extra = 0          # tokens a stricter provider would count on top

    def __call__(self, **_client_options):
        provider = self

        class Responses:
            def create(self, **kwargs):
                estimate = ai_service._estimate_tokens({"input": kwargs.get("input"),
                                                        "instructions": kwargs.get("instructions")})
                requested = groq_requested(estimate, int(kwargs.get("max_output_tokens") or 0)) + provider.extra
                provider.calls.append({**kwargs, "estimate": estimate})
                provider.requested.append(requested)
                if requested > LIMIT:
                    raise limits.tpm_413(kwargs.get("model"), requested)
                usage = SimpleNamespace(input_tokens=estimate, output_tokens=900, total_tokens=estimate + 900)
                return SimpleNamespace(output_text=limits.lesson_text(), model=kwargs.get("model"), usage=usage)

        return SimpleNamespace(responses=Responses())


class StoredRefusalTests(unittest.TestCase):
    def setUp(self):
        self.context = application.app.app_context()
        self.context.push()
        self.saved = {key: application.app.config.get(key) for key in (
            "AI_TEXT_REQUEST_TOKEN_CEILING", "AI_TEXT_REQUEST_OUTPUT_ALLOWANCE", "AI_REQUEST_TOKEN_CEILINGS",
            "LESSON_SOURCE_MIN_CHARS")}
        application.app.config.update(AI_REQUEST_TOKEN_CEILINGS="", AI_TEXT_REQUEST_OUTPUT_ALLOWANCE=0.4,
                                      LESSON_SOURCE_MIN_CHARS=4000)
        ai_service._explicit_ceilings.cache_clear()

    def tearDown(self):
        application.app.config.update(self.saved)
        ai_service._explicit_ceilings.cache_clear()
        self.context.pop()

    def test_the_stored_requests_reproduce_the_live_estimates_and_numbers(self):
        self.assertEqual(stored_pairs(), [(8619, 10363), (7316, 9151)])
        facts = ai_service._provider_failure_facts(stored_error(FIXTURE["calls"][0]))
        self.assertEqual((facts["status_code"], facts["limit_tokens"], facts["requested_tokens"]),
                         (413, 8000, 10363))
        alpha, share = calibration()
        self.assertAlmostEqual(alpha, 0.930, places=3)
        self.assertAlmostEqual(share, 0.469, places=3)
        # The model also explains why the one-page lessons of that day were admitted.
        for accepted in FIXTURE["accepted_same_day"]:
            self.assertLessEqual(groq_requested(accepted["input_tokens_reported"] * 1.08, accepted["max_output_tokens"]),
                                 LIMIT)

    def test_the_rebuild_after_the_stored_refusal_fits_under_8000(self):
        error = ai_service.AIProviderError("request_too_large", "too large", status_code=413,
                                           limit_tokens=8000, requested_tokens=10363)
        room = ai_service.refit_input_room(error, 8619, FIXTURE["max_output_tokens"])
        # min(8000 - 400 - 0.4 x 5000, 8619 - 2363 - 236 - 400) = min(5600, 5620)
        self.assertEqual(room, 5600)
        self.assertLess(groq_requested(room, FIXTURE["max_output_tokens"]), LIMIT)
        # The old rebuild "to the limit" (estimate 7,316) is what Groq refused with 9151.
        self.assertGreater(groq_requested(7316, FIXTURE["max_output_tokens"]), LIMIT)

    def test_the_stored_fitted_request_is_refused_before_sending_at_ceiling_8000(self):
        # Live: estimate 7,317 + 400 <= 8000 was admitted, refused by Groq, and sent again.
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        with self.assertRaises(ai_service.AIProviderError) as caught:
            ai_service._text_admission("openai/gpt-oss-20b", 7317, 5000)
        self.assertEqual(caught.exception.category, "request_too_large")
        self.assertIsNone(caught.exception.status_code)
        ai_service._text_admission("openai/gpt-oss-20b", 5600, 5000)       # fits: no exception
        application.app.config["AI_TEXT_REQUEST_OUTPUT_ALLOWANCE"] = 0.0
        ai_service._text_admission("openai/gpt-oss-20b", 7317, 5000)       # 0 = the old input-only rule

    def test_the_stored_six_page_request_rebuilt_by_fit_lesson_source_fits(self):
        prefix, map_block, header, pages, suffix = stored_pieces()
        self.assertEqual(len(pages), 6)
        error = ai_service.AIProviderError("request_too_large", "too large", status_code=413,
                                           limit_tokens=8000, requested_tokens=10363)
        room = ai_service.refit_input_room(error, gateway_estimate(FIXTURE["calls"][0]["input"]), 5000)

        def build(block, source):
            return "\n".join(line for line in (prefix.rstrip("\n"), block, header, source, suffix) if line)

        sent, block, shrunk = application.fit_lesson_source(
            pages, skeleton=build("", ""), instructions=FIXTURE["instructions"], ceiling=8000, map_block=map_block,
            output_tokens=5000, room=room)
        rebuilt = build(block, lesson_context.source_block(sent))
        estimate = gateway_estimate(rebuilt)
        self.assertTrue(shrunk)
        self.assertLessEqual(estimate, room)
        self.assertLess(groq_requested(estimate, 5000), LIMIT, f"estimate {estimate}")
        self.assertEqual([page.label for page in sent], [page.label for page in pages], "every page keeps its label")
        # At least LESSON_SOURCE_MIN_CHARS of notes, labels included (the replay counts the stored
        # contract twice: the instructions as sent plus the gateway's contract allowance).
        self.assertGreaterEqual(len(lesson_context.source_block(sent)), 3900)


class LessonRefitTests(teaching.TeachingEngineCase):
    """The real start_section_lesson against the calibrated provider, with the stored notes."""

    CONFIG_KEYS = teaching.TeachingEngineCase.CONFIG_KEYS + tuple(limits.LIVE_SETTINGS) + (
        "AI_USAGE_PATH", "AI_CACHE_DIR", "AI_TEXT_REQUEST_OUTPUT_ALLOWANCE", "LESSON_SOURCE_MIN_CHARS")

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        application.app.config.update(limits.LIVE_SETTINGS, AI_USAGE_PATH=str(root / "usage.jsonl"),
                                      AI_CACHE_DIR=str(root / "cache"), FEATURE_PRIVATE_FLASHCARDS=False,
                                      AI_TEXT_REQUEST_OUTPUT_ALLOWANCE=0.4, LESSON_SOURCE_MIN_CHARS=4000)
        limits.GatewayCase.reset()
        self.provider = CalibratedGroq()
        self.openai_patch = patch.object(ai_service, "OpenAI", self.provider)
        self.openai_patch.start()

    def tearDown(self):
        self.openai_patch.stop()
        limits.GatewayCase.reset()
        super().tearDown()
        self.temporary.cleanup()

    def six_page_section(self):
        _, _, _, pages, _ = stored_pieces()
        _, section_id, _ = self.make_project(
            "Ethik", [("Ethik_Utilitarismus_Skript.pdf", [page.text for page in pages])], limits.big_topic)
        return section_id

    def start(self, section_id, mode="learn"):
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            return application.start_section_lesson(db.session.get(application.LearningSection, section_id), mode)

    def test_without_a_ceiling_one_413_then_a_lesson_that_groq_admits(self):
        with self.assertLogs(application.app.logger, level="INFO") as logs:
            session_id = self.start(self.six_page_section())
        self.assertEqual(len(self.provider.calls), 2, "the refusal, then the rebuilt lesson")
        self.assertGreater(self.provider.requested[0], LIMIT)
        self.assertLessEqual(self.provider.requested[1], LIMIT, self.provider.requested)
        self.assertLess(self.provider.calls[1]["estimate"],
                        self.provider.calls[0]["estimate"] - (self.provider.requested[0] - LIMIT))
        self.assertEqual({call["max_output_tokens"] for call in self.provider.calls}, {application.LESSON_TOKEN_LIMIT},
                         "the output cap is never lowered")
        self.assertTrue(any("lesson.fit_retry limit=8000" in line for line in logs.output))
        self.assertIn(limits.NOTICE, application.SESSIONS[session_id]["lesson"].get("notice", ""))

    def test_with_the_ceiling_one_call_that_groq_admits(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        session_id = self.start(self.six_page_section())
        self.assertEqual(len(self.provider.calls), 1, self.provider.requested)
        self.assertLessEqual(self.provider.requested[0], LIMIT)
        self.assertEqual(self.provider.calls[0]["max_output_tokens"], application.LESSON_TOKEN_LIMIT)
        self.assertIn(limits.NOTICE, application.SESSIONS[session_id]["lesson"].get("notice", ""))

    def test_a_rebuild_that_cannot_shrink_by_the_overshoot_is_never_sent(self):
        # A provider that counts 4000 tokens more than the calibration: the fitted request is
        # refused, and no rebuild can drop 4000+ tokens of a request already at its floor.
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        self.provider.extra = 4000
        with self.assertLogs(application.app.logger, level="INFO") as logs, \
                self.assertRaises(ai_service.AIProviderError) as caught:
            self.start(self.six_page_section())
        self.assertEqual(caught.exception.category, "request_too_large")
        self.assertEqual(len(self.provider.calls), 1, "no second, identical request")
        self.assertTrue(any("lesson.fit_retry_skipped" in line for line in logs.output))

    def test_every_rebuild_is_smaller_than_the_request_it_follows(self):
        # Without a ceiling: the unfitted request, then (overshoot 2,000 more than calibrated) a
        # rebuild that must be smaller by that overshoot; never the same request twice.
        self.provider.extra = 600
        try:
            self.start(self.six_page_section())
        except ai_service.AIProviderError:
            pass
        estimates = [call["estimate"] for call in self.provider.calls]
        self.assertLessEqual(len(estimates), 2)
        self.assertEqual(len(estimates), len(set(estimates)), estimates)
        if len(estimates) == 2:
            self.assertLess(estimates[1], estimates[0] - (self.provider.requested[0] - LIMIT))

    def test_the_one_page_live_lessons_are_sent_unchanged_at_ceiling_8000(self):
        # The six one-page lessons of the final run (4,373-5,071 estimated input) were admitted live;
        # with the output allowance they still go in full: one call, nothing shortened.
        from tests import test_live_final_lessons as replay
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        for key, case in replay.CASES.items():
            with self.subTest(key=key):
                self.provider.calls.clear()
                self.provider.requested.clear()
                _, section_id, _ = self.make_project(case["subject"], [(case["file"], [replay.notes(key)])],
                                                     replay.remapped_topic(key))
                session_id = self.start(section_id)
                self.assertEqual(len(self.provider.calls), 1)
                self.assertLessEqual(self.provider.requested[0], LIMIT)
                self.assertNotIn("notice", application.SESSIONS[session_id]["lesson"])
                sent = " ".join(self.provider.calls[0]["input"].split())
                self.assertIn(" ".join(replay.notes(key).split())[-300:], sent, "every line of the notes")
                self.assertIn(" ".join(replay.notes(key).split())[:300], sent)

if __name__ == "__main__":
    unittest.main()

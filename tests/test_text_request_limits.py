"""PF4 L4: text requests that do not fit the provider's per-request limit (offline).

Live evidence (scratchpad prod/live_lessons.md §1, §5.4): a six-page lesson (16k characters of
notes plus the topic map) was refused by Groq with HTTP 413 "Request too large ... TPM:
Limit 8000, Requested 8305" on gpt-oss-20b and then again ("Requested 8962") on the 120b
fallback, which has the same limit; the student read "AI is temporarily unavailable". Groq
admits text on its input plus a small output estimate, not on max_tokens, so the output cap
of a text request is never lowered.

Pinned here, with only `OpenAI` inside the gateway replaced (the fake answers like Groq: a
request whose estimated input plus 200 exceeds 8000 tokens gets that 413):
* the limit facts are kept, and no other model at the same provider is tried after a 413
  (unless its own ceiling is known and larger); another provider still is;
* AI_TEXT_REQUEST_TOKEN_CEILING refuses an oversized text request before anything is sent,
  without clamping the output cap of one that fits;
* a topic lesson and mixed practice fit their notes to the ceiling (and say so), or after a
  provider 413 rebuild once with the limit it named.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_text_request_limits_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import httpx  # noqa: E402
import openai  # noqa: E402
from flask_login import login_user  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import routing  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from tests import test_lesson_teaching_engine as teaching  # noqa: E402

db = application.db
REQUEST = httpx.Request("POST", "http://127.0.0.1:9/openai/v1/responses")
SMALL_MODEL, LARGE_MODEL = "openai/gpt-oss-20b", "openai/gpt-oss-120b"
NOTICE = "This topic has a lot of material, so this lesson uses a shortened version of your notes."


def tpm_413(model, requested):
    """The SDK error exactly as openai builds it from Groq's 413 (organisation id removed)."""

    message = (f"Request too large for model `{model}` in organization `org_x` service tier `on_demand` on "
               f"tokens per minute (TPM): Limit 8000, Requested {requested}, please reduce your message size and "
               "try again. Need more tokens? Upgrade to Dev Tier today at https://console.groq.com/settings/billing")
    body = {"message": message, "type": "tokens", "code": "rate_limit_exceeded"}
    return openai.APIStatusError(f"Error code: 413 - {{'error': {body!r}}}", response=httpx.Response(
        413, request=REQUEST), body=body)


def lesson_text():
    return json.dumps(teaching.lesson_payload([
        teaching.part("objective", "Du kannst das Nutzenprinzip erklären."),
        teaching.part("explanation", "Das Nutzenprinzip bewertet Handlungen nach ihren Folgen."),
        teaching.part("guided_exercise", "Wende das Prinzip an.", answer="Musterlösung"),
        teaching.part("summary", "Folgen zählen."),
    ], concept="Utilitarismus"), ensure_ascii=False)


class GroqLikeProvider:
    """Stands in for the OpenAI SDK client. Answers like Groq's on_demand tier: a text request
    whose gateway-estimated input plus 200 exceeds `limit` is refused with a 413."""

    def __init__(self, limit=8000):
        self.limit = limit
        self.limits = {}          # per model, where one has more room
        self.calls = []
        self.always_413 = False

    def __call__(self, **_client_options):
        provider = self

        class Responses:
            def create(self, **kwargs):
                estimate = ai_service._estimate_tokens({"input": kwargs.get("input"),
                                                        "instructions": kwargs.get("instructions")})
                provider.calls.append({**kwargs, "estimate": estimate})
                limit = provider.limits.get(kwargs.get("model"), provider.limit)
                if provider.always_413 or estimate + 200 > limit:
                    raise tpm_413(kwargs.get("model"), estimate + 200)
                usage = SimpleNamespace(input_tokens=estimate, output_tokens=900, total_tokens=estimate + 900)
                return SimpleNamespace(output_text=lesson_text(), model=kwargs.get("model"), usage=usage)

        return SimpleNamespace(responses=Responses())


LIVE_SETTINGS = dict(TESTING=True, RUN_LIVE_AI_TEST=True, AI_MODE="live", ALLOW_LIVE_AI=True,
                     GROQ_API_KEY="test-key", GROQ_TUTOR_MODEL=SMALL_MODEL, GROQ_ANALYSIS_MODEL=LARGE_MODEL,
                     AI_REQUEST_TOKEN_CEILINGS="", AI_TEXT_REQUEST_TOKEN_CEILING=0, AI_FALLBACK_MODEL="",
                     AI_SLOW_MODEL="", AI_PREMIUM_REASONING_MODEL="", AI_PREMIUM_GRADING_MODEL="")


class GatewayCase(unittest.TestCase):
    def setUp(self):
        self.original = dict(application.app.config)
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.usage_path = root / "usage.jsonl"
        application.app.config.update(LIVE_SETTINGS, AI_USAGE_PATH=str(self.usage_path),
                                      AI_CACHE_DIR=str(root / "cache"))
        self.reset()
        self.provider = GroqLikeProvider()
        self.openai_patch = patch.object(ai_service, "OpenAI", self.provider)
        self.openai_patch.start()

    def tearDown(self):
        self.openai_patch.stop()
        self.reset()
        application.app.config.clear()
        application.app.config.update(self.original)
        self.temporary.cleanup()

    @staticmethod
    def reset():
        for store in (ai_service._MODEL_COOLDOWNS, ai_service._PROVIDER_COOLDOWNS,
                      ai_service._LEARNED_OUTPUT_CAPS, ai_service._RECENT_RESULTS):
            store.clear()
        ai_service._explicit_ceilings.cache_clear()

    def records(self):
        if not self.usage_path.exists():
            return []
        return [json.loads(line) for line in self.usage_path.read_text(encoding="utf-8").splitlines() if line]

    def lesson_request(self, characters, **kwargs):
        with application.app.app_context():
            return ai_service.create_response(
                task_type="lesson_generation", language="de", model=SMALL_MODEL, instructions="Unterrichte.",
                input="Notizen: " + ("Das Nutzenprinzip bewertet Handlungen nach ihren Folgen. " * (characters // 57)),
                max_output_tokens=5000, **kwargs)


class GatewayLimitTests(GatewayCase):
    def test_a_413_keeps_its_limit_and_no_same_provider_model_is_tried(self):
        self.provider.always_413 = True
        with self.assertRaises(ai_service.AIProviderError) as caught:
            self.lesson_request(2000)
        self.assertEqual(caught.exception.category, "request_too_large")
        self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(caught.exception.limit_tokens, 8000)
        self.assertEqual([call["model"] for call in self.provider.calls], [SMALL_MODEL],
                         "120b has the same per-minute limit: asking it again only doubles the refusal")
        self.assertEqual(self.records()[-1]["error_category"], "request_too_large")

    def test_a_larger_known_ceiling_at_the_same_provider_is_still_tried(self):
        # 20b refuses (its estimate is over 8000); the owner says 120b has 20000, and it answers.
        application.app.config["AI_REQUEST_TOKEN_CEILINGS"] = f"groq:{LARGE_MODEL}=20000"
        self.provider.limits[LARGE_MODEL] = 20000
        response = self.lesson_request(36000)
        self.assertEqual([call["model"] for call in self.provider.calls], [SMALL_MODEL, LARGE_MODEL])
        self.assertEqual(response.model, LARGE_MODEL)

    def test_another_provider_is_still_tried_after_a_413(self):
        plan = routing.RoutePlan(tier=routing.Tier.standard, candidates=(
            routing.Candidate(model=SMALL_MODEL, provider="groq", reason="requested"),
            routing.Candidate(model=LARGE_MODEL, provider="groq", reason="fallback"),
            routing.Candidate(model="openai:gpt-test", provider="openai", reason="emergency_fallback"),
        ), max_calls=3)
        error = ai_service.AIProviderError("request_too_large", "too large", status_code=413, limit_tokens=8000)
        with application.app.app_context():
            self.assertEqual(ai_service.next_candidate_after(plan, 0, error, has_images=False), 2)
            application.app.config["AI_REQUEST_TOKEN_CEILINGS"] = f"groq:{LARGE_MODEL}=20000"
            ai_service._explicit_ceilings.cache_clear()
            self.assertEqual(ai_service.next_candidate_after(plan, 0, error, has_images=False), 1)
            rate = ai_service.AIProviderError("provider_rate_limit", "busy", status_code=429)
            self.assertEqual(ai_service.next_candidate_after(plan, 0, rate, has_images=False), 1,
                             "a rate limit is about this model only")

    def test_the_text_ceiling_refuses_before_anything_is_sent(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        with self.assertRaises(ai_service.AIProviderError) as caught:
            self.lesson_request(36000)
        self.assertEqual(caught.exception.category, "request_too_large")
        self.assertEqual(caught.exception.limit_tokens, 8000)
        self.assertGreater(caught.exception.requested_tokens, 8000)
        self.assertIsNone(caught.exception.status_code, "a refusal of our own, not the provider's")
        self.assertEqual(self.provider.calls, [])
        record = self.records()[-1]
        self.assertEqual(record["event_kind"], "refused")
        self.assertFalse(record["provider_called"])

    def test_a_text_request_that_fits_keeps_its_output_cap(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        self.lesson_request(12000)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.calls[0]["max_output_tokens"], 5000,
                         "input + output above the ceiling is fine for text: never clamped")

    def test_request_ceiling_for_text(self):
        with application.app.app_context():
            self.assertIsNone(ai_service.request_ceiling(SMALL_MODEL, has_images=False))
            application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
            self.assertEqual(ai_service.request_ceiling(SMALL_MODEL, has_images=False), 8000)
            application.app.config["AI_REQUEST_TOKEN_CEILINGS"] = f"groq:{SMALL_MODEL}=6000"
            ai_service._explicit_ceilings.cache_clear()
            self.assertEqual(ai_service.request_ceiling(SMALL_MODEL, has_images=False), 6000)
            self.assertEqual(ai_service.request_ceiling(LARGE_MODEL, has_images=False), 8000)
            application.app.config["AI_VISION_REQUEST_TOKEN_CEILING"] = 9000
            self.assertEqual(ai_service.request_ceiling(LARGE_MODEL, has_images=True), 9000,
                             "the image ceiling is untouched")


# ------------------------------------------------------------------------------ lessons

def page_text(number, characters=2700):
    sentence = (f"Seite {number}: Der Utilitarismus bewertet eine Handlung nach ihren Folgen für das Glück aller "
                f"Betroffenen, Beispiel {number}. ")
    return (sentence * (characters // len(sentence) + 1))[:characters]


def big_topic(page_ids):
    """A full topic map, as the live six-page topic had (about 5000 characters in the prompt)."""

    definition = ("eine Bewertung von Handlungen nach der Summe ihrer Folgen für das Glück aller Betroffenen, "
                  "wie sie Bentham und Mill in ihren Schriften begründen")
    terms = [teaching.term_entry(f"Fachbegriff {index}", f"{definition} ({index})", page_ids[index % len(page_ids)])
             for index in range(20)]
    items = [teaching.item_entry("theory", f"Position {index}", f"{definition} ({index})",
                                 page_ids[index % len(page_ids)]) for index in range(10)]
    return {"title": "Utilitarismus", "summary": "Bentham und Mill bewerten Handlungen nach ihren Folgen. " * 8,
            "terms": terms, "items": items, "exam_questions": ["Erläutere das Nutzenprinzip."] * 3}


class LessonFitTests(teaching.TeachingEngineCase):
    CONFIG_KEYS = teaching.TeachingEngineCase.CONFIG_KEYS + tuple(LIVE_SETTINGS) + (
        "AI_USAGE_PATH", "AI_CACHE_DIR")

    def setUp(self):
        super().setUp()
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.usage_path = root / "usage.jsonl"
        application.app.config.update(LIVE_SETTINGS, AI_USAGE_PATH=str(self.usage_path),
                                      AI_CACHE_DIR=str(root / "cache"), FEATURE_PRIVATE_FLASHCARDS=False)
        GatewayCase.reset()
        self.provider = GroqLikeProvider()
        self.openai_patch = patch.object(ai_service, "OpenAI", self.provider)
        self.openai_patch.start()

    def tearDown(self):
        self.openai_patch.stop()
        GatewayCase.reset()
        super().tearDown()
        self.temporary.cleanup()

    def real_start(self, section_id):
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            section = db.session.get(application.LearningSection, section_id)
            return application.start_section_lesson(section, "learn")

    def lesson_records(self):
        if not self.usage_path.exists():
            return []
        return [json.loads(line) for line in self.usage_path.read_text(encoding="utf-8").splitlines()
                if json.loads(line)["task_type"] == "lesson_generation"]

    def six_page_topic(self):
        _, section_id, _ = self.make_project(
            "Ethik", [("Utilitarismus-Skript.pdf", [page_text(number) for number in range(1, 7)])], big_topic)
        return section_id

    def test_a_large_topic_is_fitted_to_the_text_ceiling(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        session_id = self.real_start(self.six_page_topic())
        self.assertEqual(len(self.provider.calls), 1, "fitted before sending: no refusal")
        records = self.lesson_records()
        self.assertEqual([record["event_kind"] for record in records], ["call"])
        self.assertLessEqual(records[0]["input_token_estimate"], 7600)
        self.assertEqual(self.provider.calls[0]["max_output_tokens"], application.LESSON_TOKEN_LIMIT)
        lesson = application.SESSIONS[session_id]["lesson"]
        self.assertIn(NOTICE, lesson.get("notice", ""))
        with application.app.app_context():
            saved = db.session.scalar(db.select(application.Lesson).where(application.Lesson.session_id == session_id))
            self.assertIsNotNone(saved)
        # The grader keeps every page (a fitted lesson must not narrow what can be quoted).
        stored = application.SESSIONS[session_id]["source_pages"]
        self.assertEqual(len(stored), 6)
        self.assertGreater(sum(len(page["text"]) for page in stored), 15000)

    def test_after_a_provider_413_the_lesson_is_rebuilt_once_with_its_limit(self):
        with self.assertLogs(application.app.logger, level="INFO") as logs:
            session_id = self.real_start(self.six_page_topic())
        self.assertEqual(len(self.provider.calls), 2, "the 413, then the fitted lesson")
        self.assertGreater(self.provider.calls[0]["estimate"] + 200, 8000, "unfitted, 16k of notes do not fit")
        self.assertEqual({call["model"] for call in self.provider.calls}, {SMALL_MODEL})
        self.assertLessEqual(self.provider.calls[1]["estimate"], 7600)
        self.assertTrue(any("lesson.fit_retry limit=8000" in line for line in logs.output))
        self.assertIn(NOTICE, application.SESSIONS[session_id]["lesson"].get("notice", ""))

    def test_a_small_topic_is_not_shortened(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        _, section_id, _ = self.make_project("Ethik", [("Blatt.pdf", [page_text(1, 1500)])], big_topic)
        session_id = self.real_start(section_id)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertNotIn("notice", application.SESSIONS[session_id]["lesson"])
        self.assertIn(page_text(1, 1500)[:200], self.provider.calls[0]["input"])

    def test_the_notice_is_appended_to_an_existing_one(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        with application.app.test_request_context():
            content = {"notice": "Erste Notiz."}
            application.add_lesson_notice(content, "Zweite Notiz.")
            self.assertEqual(content["notice"], "Erste Notiz. Zweite Notiz.")

    def test_mixed_practice_over_four_large_topics_is_fitted(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        files = [(f"Thema-{index}.pdf", [page_text(index * 10 + 1, 3000), page_text(index * 10 + 2, 3000)])
                 for index in range(4)]
        project_id, first_section, page_ids = self.make_project("Ethik", files, big_topic)
        with application.app.app_context():
            sections = [db.session.get(application.LearningSection, first_section)]
            sections[0].source_page_ids_json = json.dumps(page_ids[0:2])
            for index in range(1, 4):
                section = application.LearningSection(
                    project_id=project_id, position=index + 1, title=f"Thema {index}", main_topic=f"Thema {index}",
                    learning_goals_json="[]", source_page_ids_json=json.dumps(page_ids[index * 2:index * 2 + 2]),
                    topic_map_json="{}", topic_key=f"thema-{index}", estimated_minutes=10)
                db.session.add(section)
            db.session.commit()
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            sections = db.session.scalars(db.select(application.LearningSection).where(
                application.LearningSection.project_id == project_id).order_by(application.LearningSection.position)).all()
            self.assertEqual(len(sections), 4)
            session_id = application.start_mixed_project_practice(project, list(sections), "mixed_practice")
        self.assertEqual(len(self.provider.calls), 1)
        self.assertLessEqual(self.lesson_records()[0]["input_token_estimate"], 7600)
        prompt = self.provider.calls[0]["input"]
        for index in range(1, 4):
            self.assertIn(f"## Thema {index}", prompt, "every topic keeps its share")
        self.assertTrue(application.SESSIONS[session_id]["source_context"])

    # ------------------------------------------------------------ review B B-6
    # The final exam, similar-mistake practice and the topic split carried up to 18 000
    # characters of notes per topic unfitted: with the ceiling set they were refused before
    # sending, on Groq's on_demand tier they got a 413.

    def three_large_topics(self):
        files = [(f"Thema-{index}.pdf", [page_text(index * 10 + 1, 6000), page_text(index * 10 + 2, 6000)])
                 for index in range(3)]
        project_id, first_section, page_ids = self.make_project("Ethik", files, big_topic)
        with application.app.app_context():
            db.session.get(application.LearningSection, first_section).source_page_ids_json = json.dumps(page_ids[0:2])
            for index in range(1, 3):
                db.session.add(application.LearningSection(
                    project_id=project_id, position=index + 1, title=f"Thema {index}", main_topic=f"Thema {index}",
                    learning_goals_json="[]", source_page_ids_json=json.dumps(page_ids[index * 2:index * 2 + 2]),
                    topic_map_json="{}", topic_key=f"thema-{index}", estimated_minutes=10))
            db.session.commit()
        return project_id, first_section

    def exam_calls(self, project_id):
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            sections = db.session.scalars(db.select(application.LearningSection).where(
                application.LearningSection.project_id == project_id).order_by(application.LearningSection.position)).all()
            try:
                application.generate_final_exam(project, list(sections), 6, 30, "mixed",
                                                application.mock_exam_types(project.subject))
            except Exception:  # noqa: BLE001 - the fake answers with a lesson; only the request matters here
                pass
        return [call for call in self.provider.calls if "Create a realistic final exam" in str(call["input"])]

    def test_a_final_exam_over_large_topics_is_fitted_to_the_text_ceiling(self):
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        project_id, _ = self.three_large_topics()
        calls = self.exam_calls(project_id)
        self.assertTrue(calls, "sent, not refused before sending")
        self.assertLessEqual(calls[0]["estimate"] + 200, 8000, "Groq would admit it")
        for title in ("Utilitarismus", "Thema 1", "Thema 2"):
            self.assertIn(title, calls[0]["input"], "every topic keeps its share")

    def test_without_a_ceiling_the_final_exam_request_is_unchanged(self):
        project_id, first_section = self.three_large_topics()
        calls = self.exam_calls(project_id)
        with application.app.app_context():
            section = db.session.get(application.LearningSection, first_section)
            unchanged = json.dumps(application.section_source_text(section)[:18000], ensure_ascii=False)[1:-1]
        self.assertIn(unchanged[-300:], calls[0]["input"])

    def large_section(self):
        """(project id, section id) of one topic with 21 000 characters of notes."""

        project_id, section_id, _ = self.make_project(
            "Ethik", [("Utilitarismus-Skript.pdf", [page_text(number, 3500) for number in range(1, 7)])], big_topic)
        return project_id, section_id

    def saved_mistake(self, section_id):
        with application.app.app_context():
            lesson = application.Lesson(user_id=self.user_id, session_id="mistake-session", subject="Ethics",
                                        title="Utilitarismus", section_id=section_id, content_json="{}")
            db.session.add(lesson)
            db.session.flush()
            attempt = application.Attempt(lesson_id=lesson.id, question="Erläutere das Nutzenprinzip.",
                                          subject="Ethics", concept="Utilitarismus", student_answer="Pflicht zählt.",
                                          score=20, feedback="Es zählen die Folgen.", difficulty=1)
            db.session.add(attempt)
            db.session.commit()
            return attempt.id

    def test_similar_mistake_practice_is_fitted_to_the_text_ceiling(self):
        # One topic's 18 000 characters fit 8000 tokens; a tier with 6000 per minute (and the
        # final exam above, over several topics) does not.
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 6000
        attempt_id = self.saved_mistake(self.large_section()[1])
        self.client.post(f"/mistakes/{attempt_id}/similar")
        calls = [call for call in self.provider.calls if "similar in skill" in str(call["input"])]
        self.assertEqual(len(calls), 1, "sent once, not refused before sending")
        self.assertLessEqual(calls[0]["estimate"] + 200, 6000)
        self.assertIn("Erläutere das Nutzenprinzip.", calls[0]["input"])

    def test_without_a_ceiling_similar_mistake_practice_is_unchanged(self):
        project_id, section_id = self.large_section()
        attempt_id = self.saved_mistake(section_id)
        with application.app.app_context():
            source = application.section_source_text(db.session.get(application.LearningSection, section_id))
        self.client.post(f"/mistakes/{attempt_id}/similar")
        calls = [call for call in self.provider.calls if "similar in skill" in str(call["input"])]
        self.assertTrue(calls)
        self.assertIn(source[:18000][-300:], calls[0]["input"])

    def test_splitting_a_large_topic_is_fitted_to_the_text_ceiling(self):
        # PF_FIX2 F1: the admission counts AI_TEXT_REQUEST_OUTPUT_ALLOWANCE (0.4) of the split's
        # 6000-token output budget, so a 5000-token ceiling (the test's tier before) leaves no room
        # for the notes at all; at 8000 the 18 000 characters still have to be fitted.
        application.app.config["AI_TEXT_REQUEST_TOKEN_CEILING"] = 8000
        project_id, section_id = self.large_section()
        self.client.post(f"/projects/{project_id}/sections/{section_id}/split")
        calls = [call for call in self.provider.calls if "Split this learning section" in str(call["input"])]
        self.assertTrue(calls, "sent, not refused before sending")
        self.assertLessEqual(calls[0]["estimate"] + 2400 + 400, 8000, "input + 0.4 x 6000 + the margin")
        with application.app.app_context():
            source = application.section_source_text(db.session.get(application.LearningSection, section_id))
        self.assertNotIn(source[:18000][-300:], calls[0]["input"], "fitted: the notes were shortened")

    def test_without_a_ceiling_the_split_request_is_unchanged(self):
        project_id, section_id = self.large_section()
        with application.app.app_context():
            source = application.section_source_text(db.session.get(application.LearningSection, section_id))
        self.client.post(f"/projects/{project_id}/sections/{section_id}/split")
        calls = [call for call in self.provider.calls if "Split this learning section" in str(call["input"])]
        self.assertTrue(calls)
        self.assertIn(source[:18000][-300:], calls[0]["input"])


if __name__ == "__main__":
    unittest.main()

"""Reliability V1 through the real routes: the lesson model, the calculation check, the
bounded correction call, the deterministic patch, the flags the page shows, and the
source-attribution rules. The model is always a stand-in (FakeAI)."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_reliability_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.ai_services import routing  # noqa: E402
from learnova.lessons import lesson_context, math_check  # noqa: E402
from tests.test_continue_learning import ContinueLearningTestCase, FakeAI, lesson_payload  # noqa: E402

WRONG_LINE = "100 · 4 · 10 : 36000 = 0,0111 €"
RIGHT_LINE = "100 · 4 · 10 : 36000 = 0,1111 €"


def interest_lesson(line=WRONG_LINE, *, extra_items=()):
    """A lesson whose worked example states `line`."""

    payload = lesson_payload("Tageszinsen")
    payload["parts"] = [
        {"kind": "objective", "title": "Ziel", "body": "Du berechnest Tageszinsen.", "items": [], "answer": "",
         "from_notes": False, "source_pages": []},
        {"kind": "explanation", "title": "So geht es", "body": "Tageszinsen: Z = K · p · t : 36000.", "items": [],
         "answer": "", "from_notes": False, "source_pages": []},
        {"kind": "worked_example", "title": "Beispiel", "body": "100 € bei 4 % für 10 Tage.",
         "items": [{"label": "Rechnung", "text": line}, *extra_items], "answer": "", "from_notes": False,
         "source_pages": []},
        {"kind": "summary", "title": "Kurz", "body": "Formel einsetzen, ausrechnen.", "items": [], "answer": "",
         "from_notes": False, "source_pages": []},
    ]
    payload["worked_example"] = {"problem": "100 € bei 4 % für 10 Tage", "steps": [line], "answer": ""}
    payload["concepts"] = [{"name": "Tageszinsen", "evidence": "Ziel"}]
    payload["question"] = {"id": "q1", "concept": "Tageszinsen", "difficulty": 1, "type": "multiple_choice",
                           "prompt": "Wie viel Zinsen bringen 200 € bei 5 % in 30 Tagen?", "hint": "Formel.",
                           "options": [{"id": "a", "label": "0,83 €"}, {"id": "b", "label": "8,33 €"}],
                           "expected_answer": "a"}
    return payload


class ShortLessonTests(ContinueLearningTestCase):
    def analyze(self, *lessons, goal="Tageszinsen berechnen"):
        with FakeAI(*lessons) as ai:
            response = self.client.post("/api/analyze", data={"study_goal": goal, "subject": "Mathematics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:400])
        return response.get_json()["lesson"], ai.lesson_calls()

    def example_part(self, lesson):
        return next(part for part in lesson["parts"] if part["kind"] == "worked_example")

    def test_a_correct_lesson_is_verified_without_another_call(self):
        lesson, calls = self.analyze(interest_lesson(RIGHT_LINE))
        self.assertEqual(len(calls), 1)
        self.assertEqual(lesson["verification"]["state"], math_check.VERIFIED)
        self.assertEqual(self.example_part(lesson)["math"], "verified")

    def test_the_lesson_model_is_the_stronger_configured_one(self):
        application.app.config["AI_LESSON_MODEL"] = "openai/gpt-oss-120b"
        _lesson, calls = self.analyze(interest_lesson(RIGHT_LINE))
        self.assertEqual(calls[0]["model"], "openai/gpt-oss-120b")
        self.assertIn("CALCULATIONS:", calls[0]["instructions"])
        application.app.config["AI_LESSON_MODEL"] = "groq:openai/gpt-oss-20b"
        _lesson, calls = self.analyze(interest_lesson(RIGHT_LINE))
        self.assertEqual(calls[0]["model"], "groq:openai/gpt-oss-20b", "configurable")

    def test_the_factor_of_ten_error_is_sent_back_once_and_the_correction_is_kept(self):
        lesson, calls = self.analyze(interest_lesson(WRONG_LINE), interest_lesson(RIGHT_LINE))
        self.assertEqual(len(calls), 2, "one correction call")
        fix = calls[1]
        self.assertEqual(fix["model"], application.app.config["AI_MATH_VERIFY_MODEL"])
        self.assertEqual(fix["signals"], {"strict_model": True}, "never a weaker model in its place")
        self.assertTrue(fix["prompt_version"].endswith("+mathfix1"))
        self.assertIn("0,0111", fix["input"])
        self.assertIn("0,1111", fix["input"], "the recomputed value is given")
        self.assertEqual(lesson["verification"]["state"], math_check.VERIFIED)
        self.assertTrue(lesson["verification"]["corrected_by_model"])
        self.assertEqual(self.example_part(lesson)["math"], "corrected")
        self.assertNotIn("0,0111", json.dumps(lesson, ensure_ascii=False))

    def test_a_failed_correction_falls_back_to_the_exact_recomputed_value(self):
        lesson, calls = self.analyze(interest_lesson(WRONG_LINE), RuntimeError("provider timeout"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.example_part(lesson)["items"][0]["text"], RIGHT_LINE)
        self.assertEqual(lesson["verification"]["state"], math_check.VERIFIED)
        self.assertEqual(self.example_part(lesson)["math"], "corrected")

    def test_an_invalid_correction_answer_is_not_used(self):
        lesson, _calls = self.analyze(interest_lesson(WRONG_LINE), "not json at all")
        self.assertNotIn("0,0111 €", self.example_part(lesson)["items"][0]["text"], "patched instead")

    def test_a_wrong_result_that_cannot_be_patched_safely_is_flagged_for_the_student(self):
        # The wrong value appears twice in the item: replacing it would be a guess.
        twice = "100 · 4 · 10 : 36000 = 0,0111 € (also 0,0111 €)"
        lesson, calls = self.analyze(interest_lesson(twice), interest_lesson(twice))
        self.assertEqual(len(calls), 2, "the correction was asked once and failed to fix it")
        self.assertEqual(lesson["verification"]["state"], math_check.NEEDS_REGENERATION)
        self.assertEqual(self.example_part(lesson)["math"], "needs_check")
        self.assertIn("0,0111", self.example_part(lesson)["items"][0]["text"], "never silently replaced by a guess")

    def test_the_check_can_be_switched_off(self):
        application.app.config["FEATURE_LESSON_MATH_CHECK"] = False
        lesson, calls = self.analyze(interest_lesson(WRONG_LINE))
        self.assertEqual(len(calls), 1)
        self.assertNotIn("verification", lesson)

    def test_no_correction_calls_when_attempts_are_zero(self):
        application.app.config["AI_MATH_CORRECTION_ATTEMPTS"] = 0
        lesson, calls = self.analyze(interest_lesson(WRONG_LINE))
        self.assertEqual(len(calls), 1)
        self.assertEqual(lesson["verification"]["correction_calls"], 0)
        self.assertEqual(self.example_part(lesson)["items"][0]["text"], RIGHT_LINE, "the exact patch still applies")


class RoutingTests(unittest.TestCase):
    def policy(self):
        return routing.RoutingPolicy(tier_models={routing.Tier.standard: "openai/gpt-oss-20b",
                                                  routing.Tier.strong: "openai/gpt-oss-120b"})

    def test_a_lesson_asks_for_the_stronger_model_and_keeps_the_standard_behind_it(self):
        plan = routing.plan_route(routing.RoutingContext(task_type="lesson_generation",
                                                         requested_model="openai/gpt-oss-120b"), self.policy(), {})
        self.assertEqual(plan.models[:2], ["openai/gpt-oss-120b", "openai/gpt-oss-20b"])

    def test_a_strict_call_has_no_fallback(self):
        plan = routing.plan_route(routing.RoutingContext(task_type="lesson_generation",
                                                         requested_model="openai/gpt-oss-120b",
                                                         signals={"strict_model": True}), self.policy(), {})
        self.assertEqual(plan.models, ["openai/gpt-oss-120b"])


class AttributionTests(unittest.TestCase):
    NOTES = ("Tageszinsen: Z = K · p · t : 36000 (1 Jahr = 360 Tage). Beispiel: K = 1200 €, p = 3, t = 90 Tage, "
             "Z = 1200 · 3 · 90 : 36000 = 9 €. Kein Dreisatz, immer die Formel einsetzen.")

    def guard(self, parts, source=NOTES):
        return lesson_context.guard_parts(parts, item_kinds=None, source_text=source, has_notes=bool(source))

    def test_an_example_from_the_notes_keeps_its_label(self):
        part = {"kind": "worked_example", "title": "Beispiel aus dem Merkheft", "from_notes": True,
                "source_pages": [1], "body": "Tageszinsen für K = 1200 €, p = 3 und t = 90 Tage mit der Formel einsetzen.",
                "items": [{"label": "Rechnung", "text": "Z = 1200 · 3 · 90 : 36000 = 9 €"}]}
        self.assertTrue(self.guard([part])[0]["from_notes"])

    def test_an_invented_example_and_background_lose_the_label(self):
        invented = {"kind": "worked_example", "title": "Beispiel", "from_notes": True, "source_pages": [1],
                    "body": "Stell dir vor, du legst dein Taschengeld auf ein Sparbuch bei der Sparkasse an.",
                    "items": [{"label": "Rechnung", "text": "Z = 100 · 4 · 10 : 36000 = 0,11 €"}]}
        background = {"kind": "summary", "title": "Geschichte", "from_notes": True, "source_pages": [1],
                      "body": "Schon im alten Babylon verlangten Händler Zinsen für geliehenes Getreide und Silber."}
        guarded = self.guard([invented, background])
        self.assertEqual([part["from_notes"] for part in guarded], [False, False])
        self.assertEqual([part["source_pages"] for part in guarded], [[], []], "no page for what is not on it")

    def test_mistakes_are_not_the_notes_own_because_they_name_the_formulas_numbers(self):
        # Live 2026-11-14: "Falsch: Z = K · p · t : 100 - Richtig: ... : 36000" kept the label
        # because 100 and 36000 are on the page. The notes list no mistakes.
        part = {"kind": "mistakes", "title": "Typische Fehler", "from_notes": True, "source_pages": [1],
                "body": "", "items": [{"label": "Falscher Nenner",
                                       "text": "Falsch: Z = K · p · t : 100, richtig ist der Nenner 36000 bei Tagen."}]}
        self.assertFalse(self.guard([part])[0]["from_notes"])

    def test_without_notes_nothing_is_from_the_notes(self):
        part = {"kind": "worked_example", "title": "Beispiel", "from_notes": True, "source_pages": [1],
                "body": "Tageszinsen.", "items": []}
        self.assertFalse(lesson_context.guard_parts([part], item_kinds=None, source_text="", has_notes=False)[0]["from_notes"])

    def test_a_textbook_page_named_by_a_kompetenzraster_is_no_page_citation(self):
        pages = [lesson_context.SourcePage(7, "[raster.jpg, p. 1]", "Ich kann Gleichungen lösen. S. 42 Nr. 7")]
        out = lesson_context.resolve_part_pages([{"kind": "objective", "title": "Ziel", "body": "x",
                                                  "source_pages": [42]}], pages, numbers={7: 1}, display={7: "raster p. 1"})
        self.assertEqual((out[0]["source_pages"], out[0]["source_labels"]), ([], []))


class ShortLessonPageTests(ContinueLearningTestCase):
    def test_only_the_photos_page_numbers_are_kept(self):
        lesson = copy.deepcopy(interest_lesson(RIGHT_LINE))
        for part in lesson["parts"]:
            part["source_pages"] = [1, 42]
        application.shape_short_lesson(lesson, material="Tageszinsen Z = K · p · t : 36000", page_count=1)
        self.assertTrue(all(part["source_pages"] in ([1], []) for part in lesson["parts"]))


if __name__ == "__main__":
    unittest.main()

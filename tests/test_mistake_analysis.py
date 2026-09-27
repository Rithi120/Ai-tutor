"""Tests for the student-answer mistake-analysis engine (learnova.analysis) and its wiring."""

import json
import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_analysis_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from learnova.analysis import (  # noqa: E402
    MISTAKE_CATEGORIES, VERDICTS, analysis_system_prompt, empty_analysis,
    normalize_analysis, repeated_misconceptions, validate_analysis,
)
from learnova.analysis.service import AnalysisSchemaError  # noqa: E402
from learnova.ai_services.contracts import TASK_SCHEMAS, AIValidationError  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def good(**over):
    base = {"verdict": "partially_correct", "score_fraction": 0.5, "confidence": 0.8,
            "mistake_categories": ["sign_error"], "root_cause": "dropped a negative sign",
            "next_question": {"question": "q", "purpose": "p", "difficulty_change": "same"}}
    base.update(over)
    return base


class SchemaValidationTests(unittest.TestCase):
    def test_accepts_well_formed(self):
        result = validate_analysis(good())
        self.assertEqual(result["verdict"], "partially_correct")
        self.assertEqual(result["mistake_categories"], ["sign_error"])
        self.assertTrue(result["should_create_mistake_record"])
        self.assertTrue(result["should_reduce_mastery"])

    def test_all_fields_present_after_normalize(self):
        result = normalize_analysis({"verdict": "correct"})
        for field in ("verdict", "score_fraction", "confidence", "question_intent", "student_approach",
                      "correct_parts", "mistake_categories", "root_cause", "likely_student_thought",
                      "exact_error_step", "correct_reasoning", "final_answer", "improvement_advice",
                      "prerequisites_to_review", "next_question", "should_create_mistake_record",
                      "should_reduce_mastery", "analysis_limitations"):
            self.assertIn(field, result)
        self.assertEqual(set(result["next_question"]), {"question", "purpose", "difficulty_change"})

    def test_correct_verdict_defaults_no_record_no_mastery_drop(self):
        result = normalize_analysis({"verdict": "correct"})
        self.assertFalse(result["should_create_mistake_record"])
        self.assertFalse(result["should_reduce_mastery"])
        self.assertEqual(result["score_fraction"], 1.0)

    def test_rejects_invalid_verdict(self):
        with self.assertRaises(AnalysisSchemaError):
            validate_analysis(good(verdict="nope"))

    def test_rejects_incorrect_without_category_or_cause(self):
        with self.assertRaises(AnalysisSchemaError):
            validate_analysis({"verdict": "incorrect", "mistake_categories": [], "root_cause": ""})

    def test_normalize_clamps_and_filters(self):
        result = normalize_analysis(good(score_fraction=5, confidence=-1,
                                         mistake_categories=["sign_error", "not_a_category", "sign_error"]))
        self.assertEqual(result["score_fraction"], 1.0)
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["mistake_categories"], ["sign_error"])  # dedup + drop unknown

    def test_bad_difficulty_change_coerced(self):
        result = normalize_analysis(good(next_question={"question": "q", "purpose": "p", "difficulty_change": "way harder"}))
        self.assertEqual(result["next_question"]["difficulty_change"], "same")

    def test_empty_analysis_is_valid(self):
        result = empty_analysis(note="unavailable")
        self.assertIn(result["verdict"], VERDICTS)
        self.assertEqual(result["analysis_limitations"], ["unavailable"])


class MalformedRepairTests(unittest.TestCase):
    def test_gateway_validator_raises_on_malformed(self):
        validator = TASK_SCHEMAS["mistake_analysis"].validator
        with self.assertRaises(AIValidationError):
            validator({"verdict": "banana"}, {})
        with self.assertRaises(AIValidationError):
            validator("not a dict", {})

    def test_gateway_validator_accepts_valid(self):
        TASK_SCHEMAS["mistake_analysis"].validator(good(), {})  # must not raise


class PromptTests(unittest.TestCase):
    def test_prompt_has_core_diagnostic_rules(self):
        prompt = analysis_system_prompt("Mathematics", "German", "Grade 8")
        self.assertIn("German", prompt)
        self.assertIn("Grade 8", prompt)
        low = prompt.lower()
        for needle in ("educational diagnostician", "smallest", "alternative method",
                       "arithmetic slip", "ai_generated_question_error", "symbolically",
                       "try again"):
            self.assertIn(needle, low)

    def test_all_categories_advertised(self):
        prompt = analysis_system_prompt("Physics", "English")
        for category in MISTAKE_CATEGORIES:
            self.assertIn(category, prompt)


class AggregationTests(unittest.TestCase):
    def test_groups_by_underlying_misconception_not_wording(self):
        records = [
            {"root_cause": "confuses area and circumference", "mistake_categories": ["conceptual_misunderstanding"],
             "subject": "Mathematics", "concept": "circles", "resolved": False, "last_seen": "2026-01-02"},
            {"root_cause": "confused area with the circumference of a circle", "mistake_categories": ["conceptual_misunderstanding"],
             "subject": "Mathematics", "concept": "circles", "resolved": True, "last_seen": "2026-01-05"},
            {"root_cause": "dropped a sign", "mistake_categories": ["sign_error"],
             "subject": "Mathematics", "concept": "equations", "resolved": False, "last_seen": "2026-01-03"},
        ]
        clusters = repeated_misconceptions(records, min_count=2)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 2)
        self.assertEqual(clusters[0]["top_category"], "conceptual_misunderstanding")
        self.assertIn("circles", clusters[0]["concepts"])
        self.assertFalse(clusters[0]["resolved"])  # any unresolved -> cluster unresolved
        self.assertEqual(clusters[0]["last_seen"], "2026-01-05")

    def test_singletons_excluded(self):
        records = [{"root_cause": "x", "mistake_categories": ["sign_error"], "subject": "M",
                    "concept": "c", "resolved": False, "last_seen": "2026-01-01"}]
        self.assertEqual(repeated_misconceptions(records, min_count=2), [])


class WiringStaticTests(unittest.TestCase):
    def test_true_false_labels_localized_in_mode_engine(self):
        js = (ROOT / "static" / "js" / "flashcards" / "mode-engine.js").read_text(encoding="utf-8")
        self.assertIn('t("fcTrue")', js)
        self.assertIn('t("fcFalse")', js)

    def test_repeat_mistakes_hidden_at_full_accuracy(self):
        js = (ROOT / "static" / "js" / "flashcards" / "mode-engine.js").read_text(encoding="utf-8")
        self.assertIn("incorrect_count", js)
        self.assertIn("#reviewWeak", js)

    def test_no_rainbow_gradient_on_primary_button(self):
        css = (ROOT / "static" / "css" / "learnova-components.css").read_text(encoding="utf-8")
        # primary button is a solid accent, not the multi-hue brand gradient
        self.assertIn(".primary-button { border-color: transparent; background: var(--ln-violet)", css)

    def test_math_containment_stylesheet_linked(self):
        base = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
        self.assertIn("learnova-content.css", base)
        content = (ROOT / "static" / "css" / "learnova-content.css").read_text(encoding="utf-8")
        self.assertIn(".katex-display", content)
        self.assertIn("overflow-x: auto", content)

    def test_gamification_widget_styled_globally(self):
        # The dashboard uses .gamification-dashboard but does not load flashcards.css,
        # so the widget's styles must live in a globally-linked stylesheet.
        dashboard = (ROOT / "templates" / "dashboard.html").read_text(encoding="utf-8")
        self.assertIn("gamification-dashboard", dashboard)
        self.assertNotIn("flashcards.css", dashboard)  # confirms the sheet is NOT loaded here
        content = (ROOT / "static" / "css" / "learnova-content.css").read_text(encoding="utf-8")
        self.assertIn(".gamification-dashboard {", content)
        self.assertIn(".gamification-dashboard > *", content)


if __name__ == "__main__":
    unittest.main()

"""Unit and evaluation tests for the adaptive diagnostics engine (learnova.diagnostics).

The engine is Flask-independent, so almost everything here runs as a plain unit test.
The final class drives the labeled evaluation harness and asserts its thresholds, so a
regression in the rules fails the build instead of silently producing worse diagnoses.
"""

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from learnova.diagnostics import (
    DIAGNOSIS_TAGS,
    DIAGNOSIS_VERSION,
    DiagnosisSchemaError,
    QuestionSpec,
    apply_second_opinion,
    check_answer,
    confident_status,
    confirmed_prerequisites,
    diagnosis_label,
    diagnosis_system_prompt,
    insufficient_evidence_diagnosis,
    merge_prerequisite,
    next_action_label,
    normalize_diagnosis,
    plan_next_action,
    prerequisite_chain,
    spec_from_constraints,
    student_view,
    to_legacy_analysis,
    update_evidence,
    validate_diagnosis,
    validate_question,
    verify_diagnosis,
)
from learnova.diagnostics.evaluation import THRESHOLDS, format_report, load_cases, run_suite
from learnova.diagnostics.knowledge import is_unassessed, uncertainty_from_weight
from learnova.diagnostics.planner import similarity
from learnova.diagnostics.verification import evaluate_arithmetic, parse_number
from learnova.translations import translate

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
CASES = ROOT / "tests" / "fixtures" / "diagnostics" / "cases.json"


def evidence(*pairs):
    return [{"id": f"e{i}", "source": src, "quote": q} for i, (src, q) in enumerate(pairs, 1)]


def diagnosis(**over):
    base = {
        "correctness_status": "incorrect",
        "score": {"points": 0.0, "max_points": 1.0},
        "concepts_assessed": ["Linear equations"],
        "primary_diagnosis": {"tag": "procedural_error", "statement": "Step skipped.",
                              "evidence_ids": ["e1"]},
        "evidence": evidence(("student_answer", "3x = 9 so x = 9")),
        "confidence": {"value": 0.8, "basis": "The step is written out."},
        "student_facing_explanation": "You stopped one step early.",
    }
    base.update(over)
    return base


def state(**over):
    base = {
        "concept": "Linear equations", "subject": "Mathematics", "mastery_score": 50.0,
        "status": "learning", "attempts": 4, "consecutive_correct": 0,
        "consecutive_incorrect": 0, "evidence_weight": 2.0, "uncertainty": 0.33,
        "difficulty_level": 2,
    }
    base.update(over)
    return base


class SchemaTests(unittest.TestCase):
    def test_normalized_result_carries_every_contract_field(self):
        result = normalize_diagnosis(diagnosis())
        for field in (
            "analysis_version", "correctness_status", "score", "concepts_assessed",
            "primary_diagnosis", "secondary_diagnoses", "evidence",
            "misconception_description", "prerequisite_gaps", "missing_evidence",
            "confidence", "recommended_intervention", "next_action",
            "candidate_question_constraints", "student_facing_explanation",
            "internal_diagnostic_summary", "validation_status",
        ):
            self.assertIn(field, result)
        self.assertEqual(result["analysis_version"], DIAGNOSIS_VERSION)

    def test_rejects_unknown_correctness_status(self):
        with self.assertRaises(DiagnosisSchemaError):
            validate_diagnosis(diagnosis(correctness_status="probably"))
        with self.assertRaises(DiagnosisSchemaError):
            validate_diagnosis("not an object")

    def test_claim_without_resolvable_evidence_is_dropped(self):
        result = normalize_diagnosis(diagnosis(primary_diagnosis={
            "tag": "conceptual_misconception", "statement": "Invented.", "evidence_ids": ["e99"],
        }))
        self.assertEqual(result["primary_diagnosis"]["tag"], "insufficient_evidence")
        self.assertEqual(result["correctness_status"], "insufficient_evidence")
        self.assertEqual(result["validation_status"], "repaired")

    def test_prerequisite_gap_without_evidence_is_dropped(self):
        result = normalize_diagnosis(diagnosis(
            prerequisite_gaps=[{"concept": "Times tables", "evidence_ids": []}]))
        self.assertEqual(result["prerequisite_gaps"], [])
        self.assertEqual(result["validation_status"], "repaired")

    def test_misconception_text_requires_a_misconception_tag(self):
        result = normalize_diagnosis(diagnosis(
            misconception_description="Believes area equals circumference."))
        self.assertEqual(result["misconception_description"], "")

    def test_unknown_tag_falls_back_without_inventing_a_cause(self):
        result = normalize_diagnosis(diagnosis(primary_diagnosis={"tag": "careless_mistake"}))
        self.assertEqual(result["primary_diagnosis"]["tag"], "insufficient_evidence")

    def test_correct_status_survives_a_missing_primary_tag(self):
        result = normalize_diagnosis({
            "correctness_status": "correct",
            "score": {"points": 1.0, "max_points": 1.0},
        })
        self.assertEqual(result["primary_diagnosis"]["tag"], "correct")
        self.assertEqual(result["score"]["fraction"], 1.0)

    def test_missing_evidence_caps_confidence(self):
        result = normalize_diagnosis(diagnosis(
            missing_evidence=True, confidence={"value": 0.99, "basis": "sure"}))
        self.assertLessEqual(result["confidence"]["value"], 0.4)

    def test_score_fraction_is_derived_and_clamped(self):
        result = normalize_diagnosis(diagnosis(
            score={"points": 9, "max_points": 4, "rubric_evidence": [
                {"criterion": "Chose the right operation", "met": True, "evidence_ids": ["e1"]},
                {"criterion": "Ghost criterion", "met": True, "evidence_ids": ["e42"]}]}))
        self.assertEqual(result["score"]["points"], 4.0)
        self.assertEqual(result["score"]["fraction"], 1.0)
        self.assertEqual(result["score"]["rubric_evidence"][1]["evidence_ids"], [])

    def test_insufficient_evidence_result_is_well_formed(self):
        result = insufficient_evidence_diagnosis("provider down", ["Fractions"])
        self.assertEqual(result["correctness_status"], "insufficient_evidence")
        self.assertTrue(result["missing_evidence"])
        self.assertEqual(result["validation_status"], "rejected")
        self.assertEqual(result["concepts_assessed"], ["Fractions"])


class PresentationTests(unittest.TestCase):
    def test_student_view_never_exposes_internal_fields(self):
        view = student_view(normalize_diagnosis(diagnosis(
            internal_diagnostic_summary="Teacher-only note.",
            evidence=evidence(("student_answer", "visible"), ("prior_attempt", "hidden")))))
        serialized = json.dumps(view, ensure_ascii=False)
        self.assertNotIn("Teacher-only note.", serialized)
        self.assertNotIn("internal_diagnostic_summary", serialized)
        self.assertIn("visible", serialized)
        self.assertNotIn("hidden", serialized)

    def test_student_view_can_omit_the_detail_panel(self):
        view = student_view(normalize_diagnosis(diagnosis()), include_detail=False)
        self.assertNotIn("detail", view)

    def test_legacy_projection_keeps_existing_consumers_working(self):
        legacy = to_legacy_analysis(normalize_diagnosis(diagnosis()))
        self.assertEqual(legacy["verdict"], "incorrect")
        self.assertIn("procedural_mistake", legacy["mistake_categories"])
        self.assertTrue(legacy["should_create_mistake_record"])
        self.assertEqual(set(legacy["next_question"]), {"question", "purpose", "difficulty_change"})

    def test_legacy_projection_never_blames_the_student_for_a_bad_item(self):
        flawed = normalize_diagnosis(diagnosis(
            correctness_status="correct",
            primary_diagnosis={"tag": "question_or_key_flawed", "statement": "Key is wrong.",
                               "evidence_ids": ["e1"]}))
        legacy = to_legacy_analysis(flawed)
        self.assertFalse(legacy["should_reduce_mastery"])
        self.assertFalse(legacy["should_create_mistake_record"])


class VerificationTests(unittest.TestCase):
    def test_numeric_equivalence_across_forms(self):
        self.assertTrue(check_answer("0.5", "1/2").matches_expected)
        self.assertTrue(check_answer("50%", "0.5").matches_expected)
        self.assertTrue(check_answer("4,0", "4").matches_expected)
        self.assertFalse(check_answer("7", "4").matches_expected)

    def test_unit_mismatch_is_not_a_match(self):
        result = check_answer("5 km", "5 m")
        self.assertTrue(result.decided)
        self.assertFalse(result.matches_expected)

    def test_open_response_is_left_undecided(self):
        result = check_answer("Because the pressure rises", "Pressure increases with depth")
        self.assertFalse(result.decided)
        self.assertIsNone(result.matches_expected)

    def test_ordering_answers_compare_in_order(self):
        self.assertTrue(check_answer(["a", "b", "c"], ["a", "b", "c"], question_type="ordering").matches_expected)
        self.assertFalse(check_answer(["b", "a", "c"], ["a", "b", "c"], question_type="ordering").matches_expected)

    def test_digits_glued_to_letters_are_not_numbers(self):
        self.assertIsNone(parse_number("x = l4"))
        self.assertIsNone(parse_number("3x"))
        self.assertEqual(parse_number("x = 14"), 14.0)
        self.assertEqual(parse_number("5 km"), 5.0)

    def test_arithmetic_evaluation_is_sandboxed(self):
        self.assertEqual(evaluate_arithmetic("5 + 4 - 6 * 3"), -9)
        self.assertIsNone(evaluate_arithmetic("__import__('os').system('echo hi')"))
        self.assertIsNone(evaluate_arithmetic("open('x')"))
        self.assertIsNone(evaluate_arithmetic("2 ** 999999"))
        self.assertIsNone(evaluate_arithmetic("1/0"))

    def test_objectively_right_answer_is_never_left_marked_wrong(self):
        updated, result = verify_diagnosis(
            normalize_diagnosis(diagnosis(correctness_status="incorrect")),
            student_answer="4", expected_answer="4", question_type="short_answer")
        self.assertEqual(updated["correctness_status"], "correct")
        self.assertEqual(updated["validation_status"], "deterministic_conflict")
        self.assertTrue(result.conflicts)

    def test_objectively_wrong_answer_is_never_left_marked_correct(self):
        updated, _ = verify_diagnosis(
            normalize_diagnosis(diagnosis(
                correctness_status="correct",
                primary_diagnosis={"tag": "correct", "statement": "", "evidence_ids": ["e1"]})),
            student_answer="7", expected_answer="4", question_type="short_answer")
        self.assertEqual(updated["correctness_status"], "incorrect")

    def test_low_ocr_confidence_blocks_a_character_level_override(self):
        updated, result = verify_diagnosis(
            normalize_diagnosis(diagnosis(
                correctness_status="correct",
                primary_diagnosis={"tag": "correct", "statement": "", "evidence_ids": ["e1"]})),
            student_answer="l4", expected_answer="14", question_type="short_answer",
            ocr_confidence=0.4)
        self.assertEqual(updated["correctness_status"], "correct")
        self.assertTrue(updated["missing_evidence"])
        self.assertFalse(result.decided)

    def test_risk_rises_for_unverifiable_understanding_claims(self):
        _, low = verify_diagnosis(
            normalize_diagnosis(diagnosis(
                correctness_status="incorrect",
                primary_diagnosis={"tag": "procedural_error", "statement": "x", "evidence_ids": ["e1"]})),
            student_answer="7", expected_answer="4", question_type="short_answer")
        _, high = verify_diagnosis(
            normalize_diagnosis(diagnosis(
                correctness_status="incorrect",
                primary_diagnosis={"tag": "conceptual_misconception", "statement": "x",
                                   "evidence_ids": ["e1"]},
                confidence={"value": 0.3, "basis": ""})),
            student_answer="a long written explanation", expected_answer="another explanation")
        self.assertLess(low.risk, high.risk)

    def test_second_opinion_can_only_weaken_a_diagnosis(self):
        original = normalize_diagnosis(diagnosis(
            primary_diagnosis={"tag": "conceptual_misconception", "statement": "Wrong idea.",
                               "evidence_ids": ["e1"]},
            misconception_description="Area equals circumference."))
        agreed = apply_second_opinion(original, {"agrees": True, "reason": "supported"})
        self.assertEqual(agreed["primary_diagnosis"]["tag"], "conceptual_misconception")
        self.assertGreaterEqual(agreed["confidence"]["value"], original["confidence"]["value"])
        rejected = apply_second_opinion(original, {"agrees": False, "reason": "no quote supports it"})
        self.assertEqual(rejected["primary_diagnosis"]["tag"], "insufficient_evidence")
        self.assertEqual(rejected["misconception_description"], "")
        self.assertLess(rejected["confidence"]["value"], original["confidence"]["value"])


class KnowledgeModelTests(unittest.TestCase):
    def test_evidence_accumulates_and_uncertainty_falls(self):
        first = update_evidence(state(evidence_weight=0.0, last_practised_at=None), now=NOW)
        second = update_evidence(
            {**state(), "evidence_weight": first["evidence_weight"], "last_practised_at": NOW},
            now=NOW)
        self.assertGreater(second["evidence_weight"], first["evidence_weight"])
        self.assertLess(second["uncertainty"], first["uncertainty"])

    def test_old_evidence_decays(self):
        fresh = update_evidence(
            {**state(), "evidence_weight": 4.0, "last_practised_at": NOW}, now=NOW)
        stale = update_evidence(
            {**state(), "evidence_weight": 4.0,
             "last_practised_at": NOW - timedelta(days=90)}, now=NOW)
        self.assertLess(stale["evidence_weight"], fresh["evidence_weight"])

    def test_weak_signals_count_for_less(self):
        clean = update_evidence(state(last_practised_at=NOW), now=NOW, diagnosis_confidence=0.9)
        hinted = update_evidence(state(last_practised_at=NOW), now=NOW,
                                 diagnosis_confidence=0.9, hints_used=True)
        unreadable = update_evidence(state(last_practised_at=NOW), now=NOW,
                                     diagnosis_confidence=0.9, ocr_confidence=0.4)
        unsupported = update_evidence(state(last_practised_at=NOW), now=NOW,
                                      diagnosis_confidence=0.9, missing_evidence=True)
        self.assertLess(hinted["observation_weight"], clean["observation_weight"])
        self.assertLess(unreadable["observation_weight"], clean["observation_weight"])
        self.assertLess(unsupported["observation_weight"], hinted["observation_weight"])

    def test_unassessed_is_distinct_from_weak(self):
        self.assertTrue(is_unassessed(state(attempts=0, uncertainty=1.0)))
        self.assertTrue(is_unassessed(state(attempts=1, uncertainty=0.9)))
        self.assertFalse(is_unassessed(state(attempts=6, uncertainty=0.2, mastery_score=10.0)))
        self.assertEqual(uncertainty_from_weight(0.0), 1.0)

    def test_mastery_needs_accumulated_evidence(self):
        self.assertEqual(confident_status(state(evidence_weight=0.9), "mastered"), "strong")
        self.assertEqual(confident_status(state(evidence_weight=4.0), "mastered"), "mastered")
        self.assertEqual(confident_status(state(evidence_weight=0.1), "weak"), "weak")

    def test_prerequisites_need_repeated_evidence(self):
        edges = {}
        merge_prerequisite(edges, concept="Equations", prerequisite="Signed integers",
                           now=NOW, confidence=0.8)
        rows = [dict(edge) for edge in edges.values()]
        self.assertEqual(confirmed_prerequisites(rows), [])
        merge_prerequisite(edges, concept="Equations", prerequisite="Signed integers",
                           now=NOW, confidence=0.6)
        rows = [dict(edge) for edge in edges.values()]
        self.assertEqual(len(confirmed_prerequisites(rows)), 1)
        self.assertAlmostEqual(rows[0]["confidence"], 0.7, places=3)

    def test_self_edges_are_ignored(self):
        edges = {}
        merge_prerequisite(edges, concept="Equations", prerequisite="equations", now=NOW)
        merge_prerequisite(edges, concept="Equations", prerequisite="", now=NOW)
        self.assertEqual(edges, {})

    def test_prerequisite_chain_is_bounded_and_cycle_safe(self):
        edges = [
            {"concept": "A", "prerequisite": "B"},
            {"concept": "B", "prerequisite": "C"},
            {"concept": "C", "prerequisite": "A"},
        ]
        self.assertEqual(prerequisite_chain(edges, "A"), ["B", "C"])


class PlannerPolicyTests(unittest.TestCase):
    def plan(self, tag, **kwargs):
        status = kwargs.pop("status", "incorrect")
        payload = normalize_diagnosis(diagnosis(
            correctness_status=status,
            primary_diagnosis={"tag": tag, "statement": "s", "evidence_ids": ["e1"]},
            **{k: v for k, v in kwargs.items() if k in
               ("prerequisite_gaps", "misconception_description", "missing_evidence")}))
        return plan_next_action(
            payload,
            kwargs.get("state", state()),
            now=NOW,
            history=kwargs.get("history", []),
            recent_prompts=kwargs.get("recent_prompts", []),
            hints_used=kwargs.get("hints_used", False),
        )

    def test_one_correct_answer_never_promotes(self):
        result = self.plan("correct", status="correct",
                           state=state(consecutive_correct=1, mastery_score=90.0, uncertainty=0.2))
        self.assertEqual(result.action, "maintain_difficulty")
        self.assertEqual(result.difficulty_delta, 0)

    def test_one_wrong_answer_never_demotes(self):
        result = self.plan("conceptual_misconception",
                           state=state(consecutive_incorrect=1, difficulty_level=3))
        self.assertEqual(result.difficulty_delta, 0)

    def test_a_slip_never_reduces_difficulty(self):
        result = self.plan("arithmetic_or_transcription_error",
                           state=state(consecutive_incorrect=3, difficulty_level=3),
                           history=[{"primary_diagnosis": "arithmetic_or_transcription_error",
                                     "concept": "Linear equations"}] * 3)
        self.assertEqual(result.action, "targeted_practice")
        self.assertEqual(result.difficulty_delta, 0)

    def test_promotion_requires_streak_mastery_and_low_uncertainty(self):
        gates = state(consecutive_correct=2, mastery_score=80.0, uncertainty=0.25)
        self.assertEqual(self.plan("correct", status="correct", state=gates).action,
                         "increase_difficulty")
        self.assertEqual(
            self.plan("correct", status="correct", state={**gates, "uncertainty": 0.8}).action,
            "maintain_difficulty")
        self.assertEqual(
            self.plan("correct", status="correct", state={**gates, "mastery_score": 40.0}).action,
            "maintain_difficulty")
        self.assertEqual(
            self.plan("correct", status="correct", state=gates, hints_used=True).action,
            "maintain_difficulty")

    def test_difficulty_never_moves_more_than_one_level(self):
        for tag in DIAGNOSIS_TAGS:
            for level in (1, 2, 3):
                result = self.plan(tag, state=state(difficulty_level=level,
                                                    consecutive_incorrect=3))
                self.assertLessEqual(abs(result.difficulty_delta), 1, tag)
                self.assertIn(result.difficulty, (1, 2, 3))

    def test_oscillation_is_suppressed(self):
        result = self.plan(
            "correct", status="correct",
            state=state(consecutive_correct=2, mastery_score=80.0, uncertainty=0.25,
                        difficulty_level=1),
            history=[{"primary_diagnosis": "prerequisite_gap", "concept": "Linear equations",
                      "next_action": "reduce_difficulty"}])
        self.assertEqual(result.action, "maintain_difficulty")

    def test_prerequisite_gap_targets_the_named_skill(self):
        payload = normalize_diagnosis(diagnosis(
            primary_diagnosis={"tag": "prerequisite_gap", "statement": "s", "evidence_ids": ["e1"]},
            prerequisite_gaps=[{"concept": "Signed integers", "evidence_ids": ["e1"]}]))
        result = plan_next_action(payload, state(difficulty_level=3), now=NOW)
        self.assertEqual(result.action, "prerequisite_reteach")
        self.assertEqual(result.constraints["prerequisite_focus"], "Signed integers")
        self.assertEqual(result.difficulty_delta, -1)

    def test_repeated_misconception_switches_to_contrast(self):
        history = [{"primary_diagnosis": "conceptual_misconception", "concept": "Linear equations"}] * 2
        self.assertEqual(self.plan("conceptual_misconception").action,
                         "worked_example_then_practice")
        self.assertEqual(self.plan("conceptual_misconception", history=history).action,
                         "misconception_contrast")

    def test_flawed_question_does_not_move_the_knowledge_model(self):
        result = self.plan("question_or_key_flawed", status="correct")
        self.assertEqual(result.action, "human_review_or_insufficient_evidence")
        self.assertFalse(result.update_mastery)
        self.assertFalse(result.penalise)

    def test_undiagnosable_response_still_counts_the_graded_score(self):
        result = self.plan("insufficient_evidence", status="insufficient_evidence")
        self.assertEqual(result.action, "diagnostic_check")
        self.assertTrue(result.update_mastery)

    def test_repeated_insufficient_evidence_escalates_to_a_human(self):
        history = [{"primary_diagnosis": "insufficient_evidence", "concept": "Linear equations"}] * 2
        result = self.plan("insufficient_evidence", status="insufficient_evidence", history=history)
        self.assertEqual(result.action, "human_review_or_insufficient_evidence")

    def test_max_difficulty_becomes_a_transfer_question(self):
        result = self.plan("correct", status="correct",
                           state=state(consecutive_correct=3, mastery_score=90.0,
                                       uncertainty=0.2, difficulty_level=3))
        self.assertEqual(result.action, "transfer_question")
        self.assertEqual(result.difficulty_delta, 0)

    def test_constraints_carry_the_repetition_guard(self):
        result = self.plan("procedural_error", recent_prompts=["Solve 2x + 3 = 11."])
        self.assertIn("Solve 2x + 3 = 11.", result.constraints["avoid_prompts"])
        self.assertTrue(result.constraints["purpose"])


class QuestionValidationTests(unittest.TestCase):
    def spec(self, **over):
        base = {"concept": "Linear equations", "difficulty": 2, "cognitive_demand": "apply",
                "question_type": "short_answer", "purpose": "Check sign handling."}
        base.update(over)
        return spec_from_constraints(base)

    def question(self, **over):
        base = {"id": "q1", "concept": "Linear equations", "difficulty": 2,
                "type": "short_answer", "prompt": "Solve for x and show the step: 3x + 5 = 20.",
                "hint": "Undo the addition first.", "options": [], "expected_answer": "5"}
        base.update(over)
        return base

    def test_accepts_a_well_formed_question(self):
        result = validate_question(self.question(), self.spec())
        self.assertTrue(result.valid, result.failures)
        self.assertTrue(all(result.checks.values()))

    def test_rejects_a_duplicate(self):
        result = validate_question(self.question(), self.spec(),
                                   recent_prompts=["Solve for x and show the step: 3x + 5 = 20."])
        self.assertFalse(result.valid)
        self.assertFalse(result.checks["not_duplicate"])

    def test_rejects_an_unanswerable_prompt(self):
        result = validate_question(
            self.question(prompt="Using the following diagram, find x."), self.spec())
        self.assertFalse(result.checks["self_contained"])

    def test_rejects_a_wrong_answer_key(self):
        result = validate_question(
            self.question(prompt="Work out 7 * 8 and write the result.", expected_answer="54",
                          type="calculation"),
            self.spec(question_type="calculation"))
        self.assertFalse(result.checks["answer_key_verified"])

    def test_rejects_an_off_concept_question(self):
        result = validate_question(
            self.question(concept="Photosynthesis", prompt="Name the green pigment in a leaf."),
            self.spec())
        self.assertFalse(result.checks["concept_aligned"])

    def test_rejects_difficulty_outside_the_requested_band(self):
        self.assertFalse(validate_question(self.question(difficulty=9), self.spec()).valid)
        self.assertFalse(
            validate_question(self.question(difficulty=3), self.spec(difficulty=1)).valid)

    def test_rejects_a_hint_that_gives_the_answer(self):
        leaky = validate_question(
            self.question(hint="The answer is 5.", prompt="Solve for x: 4x = 20."), self.spec())
        self.assertFalse(leaky.checks["hint_safe"])
        safe = validate_question(
            self.question(hint="Divide both sides by 4.", prompt="Solve for x: 4x = 20."),
            self.spec())
        self.assertTrue(safe.checks["hint_safe"])

    def test_choice_questions_need_a_reachable_answer(self):
        spec = self.spec(question_type="multiple_choice")
        options = [{"id": "a", "label": "2 pi r"}, {"id": "b", "label": "pi r squared"}]
        bad = validate_question(
            self.question(type="multiple_choice", options=options, expected_answer="z",
                          prompt="Which expression gives the area of a circle?"), spec)
        self.assertFalse(bad.checks["answer_in_options"])
        good = validate_question(
            self.question(type="multiple_choice", options=options, expected_answer="b",
                          prompt="Which expression gives the area of a circle?"), spec)
        self.assertTrue(good.valid, good.failures)

    def test_specification_is_attached_for_the_record(self):
        spec = self.spec()
        self.assertIsInstance(spec, QuestionSpec)
        self.assertEqual(spec.cognitive_demand, "apply")
        self.assertIn("learning_objective", spec.as_dict())

    def test_similarity_is_wording_insensitive(self):
        self.assertGreater(similarity("Solve 2x + 3 = 11", "solve  2x+3 = 11!"), 0.9)
        self.assertLess(similarity("Solve 2x + 3 = 11", "Name the capital of France"), 0.2)


class PromptTests(unittest.TestCase):
    def test_diagnosis_prompt_states_the_mandatory_rules(self):
        prompt = diagnosis_system_prompt("Mathematics", "German", "Grade 8").lower()
        for needle in ("evidence is mandatory", "never invent", "insufficient_evidence",
                       "german", "grade 8", "careless", "question_or_key_flawed",
                       "do not state what the student was thinking"):
            self.assertIn(needle, prompt)

    def test_every_tag_is_advertised_to_the_model(self):
        prompt = diagnosis_system_prompt("Physics", "English")
        for tag in DIAGNOSIS_TAGS:
            self.assertIn(tag, prompt)

    def test_prompt_never_requests_chain_of_thought(self):
        prompt = diagnosis_system_prompt("Biology", "English").lower()
        self.assertIn("do not write your reasoning process", prompt)
        for forbidden in ("think step by step", "show your reasoning", "chain of thought"):
            self.assertNotIn(forbidden, prompt)


class LocalizationTests(unittest.TestCase):
    """Every string the engine can show must exist in every selectable catalogue.

    Coverage is asserted against the catalogue itself rather than by comparing the
    translated text to the English source: some translations are legitimately identical
    ("Correct" in French), and a difference check would call those a failure.
    """

    def labels(self):
        from learnova.diagnostics.taxonomy import NEXT_ACTIONS
        return ([diagnosis_label(tag) for tag in DIAGNOSIS_TAGS]
                + [next_action_label(action) for action in NEXT_ACTIONS])

    def test_german_reference_catalogue_covers_every_label(self):
        from learnova.translations.catalog import GERMAN
        for label in self.labels():
            self.assertIn(label, GERMAN, f"{label!r} is missing from the German catalogue")

    def test_german_wording_matches_the_taxonomy_reference(self):
        from learnova.diagnostics.taxonomy import GERMAN_LABELS
        for label, expected in GERMAN_LABELS.items():
            self.assertEqual(translate(label, "de"), expected)

    def test_labels_are_available_in_every_selectable_language(self):
        from learnova.translations import SUPPORTED_LANGUAGES
        from learnova.translations.catalog import CATALOGS
        for language in SUPPORTED_LANGUAGES:
            if language == "en":
                continue
            catalog = CATALOGS.get(language, {})
            for label in self.labels():
                self.assertTrue(str(catalog.get(label, "")).strip(),
                                f"{label!r} missing in {language}")

    def test_adding_labels_did_not_disable_a_language(self):
        from learnova.translations import SUPPORTED_LANGUAGES
        for language in ("en", "de", "fr", "es"):
            self.assertIn(language, SUPPORTED_LANGUAGES)


class EvaluationHarnessTests(unittest.TestCase):
    """Drives the labeled case set and enforces the published thresholds."""

    @classmethod
    def setUpClass(cls):
        cls.metrics = run_suite(load_cases(CASES), now=NOW)

    def test_case_file_covers_every_required_situation(self):
        ids = {case["id"] for case in load_cases(CASES)}
        for required in (
            "correct-valid-reasoning", "slip-after-correct-reasoning",
            "misconception-first-time", "procedural-order-of-operations",
            "prerequisite-gap-reduces-difficulty", "one-word-answer-no-working",
            "ocr-low-confidence-marks-uncertainty", "german-incomplete-reasoning",
            "wrong-answer-key-blames-the-item", "invented-misconception-is-dropped",
            "no-oscillation-after-a-reduction", "question-duplicate-rejected",
            "question-wrong-answer-key-rejected",
        ):
            self.assertIn(required, ids)

    def test_metrics_meet_every_threshold(self):
        for name, threshold in THRESHOLDS.items():
            self.assertGreaterEqual(self.metrics[name], threshold,
                                    f"{name} regressed\n{format_report(self.metrics)}")

    def test_no_forbidden_difficulty_moves(self):
        self.assertEqual(self.metrics["forbidden_moves"], [],
                         format_report(self.metrics))

    def test_suite_passes_overall(self):
        self.assertTrue(self.metrics["passed"], format_report(self.metrics))


class SerialisedAnswerCheckTests(unittest.TestCase):
    """The app hands multi-part answers to the checker as a JSON string (see
    /api/answer); the checker has to read them back or a correct order is always wrong."""

    def test_a_json_encoded_ordering_answer_is_compared_as_an_order(self):
        from learnova.diagnostics.verification import check_answer

        right = check_answer('["a", "b", "c"]', ["a", "b", "c"], question_type="ordering")
        self.assertEqual((right.method, right.decided, right.matches_expected), ("choice", True, True))
        wrong = check_answer('["c", "b", "a"]', ["a", "b", "c"], question_type="ordering")
        self.assertEqual(wrong.matches_expected, False)

    def test_a_json_encoded_checkbox_answer_still_matches_and_plain_text_is_untouched(self):
        from learnova.diagnostics.verification import check_answer

        self.assertTrue(check_answer('["a", "b"]', ["a", "b"], question_type="checkboxes").matches_expected)
        self.assertFalse(check_answer('["a"]', ["a", "b"], question_type="checkboxes").matches_expected)
        self.assertTrue(check_answer("[x] marks the spot", "[x] marks the spot", question_type="text").matches_expected)


if __name__ == "__main__":
    unittest.main()

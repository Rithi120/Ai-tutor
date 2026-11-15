"""Reliability V1: the deterministic calculation check of AI-written lessons
(learnova/lessons/math_check.py). Pure - no app, no model."""

import unittest
from fractions import Fraction

from learnova.lessons import math_check as mc


def statuses(text, **kwargs):
    return [(check.status, check.kind) for check in mc.check_text(text, **kwargs)]


def only(text, **kwargs):
    checks = mc.check_text(text, **kwargs)
    assert len(checks) == 1, [check.as_dict() for check in checks]
    return checks[0]


class RegressionCaseTests(unittest.TestCase):
    """The live audit's Short Lesson (2026-11-14)."""

    def test_the_factor_of_ten_error_is_found(self):
        check = only("Beispiel: 100 € bei 4 % für 10 Tage → 100·4·10 ÷ 36000 = 0,0111 €.")
        self.assertEqual((check.status, check.kind), ("error", "decimal_place"))
        self.assertEqual((check.claimed, check.expected), ("0,0111", "0,1111"))

    def test_the_correct_values_pass(self):
        self.assertEqual(only("100 · 4 · 10 : 36000 = 0,11 €").status, "ok", "rounded to two places")
        self.assertEqual(only("100 · 4 · 10 : 36000 ≈ 0,11 €").status, "ok")
        self.assertEqual(only("100 · 4 · 10 : 36000 = 0,1111 €").status, "ok")

    def test_the_teachers_worked_example_passes(self):
        self.assertEqual(statuses("Z = 1200 · 3 · 90 : 36000 = 9 €"), [("ok", "")])
        self.assertEqual(statuses("Schritt 2 — Teile durch 36000: 324 000 ÷ 36000 = 9"), [("ok", "")])

    def test_a_wrong_example_on_purpose_is_not_judged_but_the_right_one_is(self):
        self.assertEqual(statuses("Falsch — 1200 € · 3 · 90 ÷ 360 = 90 € (falsch, weil 360 statt 36000)"), [])
        self.assertEqual(statuses("Richtig — 1200 € · 3 · 90 ÷ 36000 = 9 €"), [("ok", "")])
        self.assertEqual(statuses("Richtig — 1200 € · 3 · 90 ÷ 36000 = 90 €")[0][0], "error")


class ArithmeticTests(unittest.TestCase):
    def test_decimal_multiplication_and_division(self):
        self.assertEqual(only("2,5 · 0,4 = 1").status, "ok")
        self.assertEqual(only("2.5 × 0.4 = 1.0", language="en").status, "ok")
        self.assertEqual(only("7,5 : 2,5 = 3").status, "ok")
        wrong = only("2,5 · 0,4 = 10")
        self.assertEqual((wrong.status, wrong.kind), ("error", "decimal_place"))

    def test_fractions(self):
        self.assertEqual(only("3/4 + 1/4 = 1").status, "ok")
        self.assertEqual(only(r"$$\frac{3}{4} \cdot \frac{2}{3} = \frac{1}{2}$$").status, "ok")
        self.assertEqual(only("½ + ¼ = ¾").status, "ok")
        self.assertEqual(only(r"$$\frac{1}{3} + \frac{1}{3} = \frac{2}{6}$$").status, "error")

    def test_negative_numbers(self):
        self.assertEqual(only("-7 - (-3) = -4").status, "ok")
        self.assertEqual(only("−7 − (−3) = −4").status, "ok")
        wrong = only("-7 - (-3) = 4")
        self.assertEqual((wrong.status, wrong.kind), ("error", "sign"))

    def test_percentages(self):
        self.assertEqual(only("15 % · 200 € = 30 €").status, "ok")
        self.assertEqual(only("200 € · 15 : 100 = 30 €").status, "ok")
        # The formula's p written with "%": 4 % stands for p = 4 when the formula divides by 100.
        self.assertEqual(only("100 € · 4 % : 100 = 4 €").status, "ok")
        self.assertEqual(only("30 : 200 = 15 %").status, "ok")
        self.assertEqual(only("15 % · 200 € = 3 €").status, "error")

    def test_powers_and_roots(self):
        self.assertEqual(only("3² + 4² = 25").status, "ok")
        self.assertEqual(statuses(r"c = \sqrt{3^2+4^2} = \sqrt{25} = 5"), [("ok", ""), ("ok", "")])
        self.assertEqual(only("√2 ≈ 1,41").status, "ok")
        self.assertEqual(only("√2 = 1,41").status, "ok", "rounded to the places written")
        self.assertEqual(only("√2 = 1,42").status, "error")
        self.assertEqual(only("2^10 = 1024").status, "ok")
        self.assertEqual(only(r"\sqrt[3]{27} = 3").status, "ok")

    def test_scientific_notation(self):
        self.assertEqual(only("3 · 10^8 m/s · 2 s = 6 · 10^8 m").status, "ok")
        self.assertEqual(only("1,5e-3 · 2 = 3e-3").status, "ok")
        self.assertEqual(only("6 · 10⁻³ · 1000 = 6").status, "ok")
        wrong = only("3 · 10^8 m/s · 2 s = 6 · 10^7 m")
        self.assertEqual((wrong.status, wrong.kind), ("error", "decimal_place"))

    def test_rounding(self):
        self.assertEqual(only("200 · 5 · 30 : 36000 = 0,83 €").status, "ok")
        wrong = only("200 · 5 · 30 : 36000 = 0,84 €")
        self.assertEqual((wrong.status, wrong.kind), ("error", "rounding"))
        self.assertEqual(only("1 : 3 = 0,333").status, "ok")
        self.assertEqual(only("2 : 3 = 0,67").status, "ok")
        self.assertEqual(only("2 : 3 = 0,66").status, "error", "cut off, not rounded")

    def test_multi_step_chains_check_every_step(self):
        self.assertEqual(statuses("Z = 1200 · 3 · 90 : 36000 = 324000 : 36000 = 9 €"), [("ok", ""), ("ok", "")])
        result = statuses("Z = 1200 · 3 · 90 : 36000 = 32400 : 36000 = 0,9 €")
        self.assertIn(("error", "wrong_result"), result, "a wrong intermediate step is found")

    def test_division_by_zero(self):
        self.assertEqual(statuses("5 : 0 = 0"), [("error", "division_by_zero")])

    def test_equivalent_forms_are_not_rejected(self):
        self.assertEqual(only("2 · (3 + 4) = 2 · 3 + 2 · 4").status, "ok")
        self.assertEqual(only("0,5 = 1/2").status, "ok")
        self.assertEqual(only(r"$$\frac{6}{3} = 2,0$$").status, "ok")
        self.assertEqual(only("324 000 = 324000").status, "ok")


class UnitTests(unittest.TestCase):
    def test_unit_conversions(self):
        self.assertEqual(only("2,5 km = 2500 m").status, "ok")
        self.assertEqual(only("36 km/h = 10 m/s").status, "ok")
        self.assertEqual(only("1 h = 3600 s").status, "ok")
        self.assertEqual(only("250 ml = 0,25 l").status, "ok")
        wrong = only("2,5 km = 250 m")
        self.assertEqual((wrong.status, wrong.kind), ("error", "decimal_place"))

    def test_incorrect_units(self):
        wrong = only("20 Ω · 0,5 A = 10 A")
        self.assertEqual((wrong.status, wrong.kind), ("error", "unit_mismatch"))
        self.assertEqual(statuses("2 m + 3 kg = 5"), [("error", "unit_mismatch")])

    def test_a_missing_unit_is_a_warning_not_an_error(self):
        check = only("1200 € · 3 : 100 = 36")
        self.assertEqual((check.status, check.kind), ("warning", "unit_missing"))

    def test_currency(self):
        self.assertEqual(only("3 · 2,49 € = 7,47 €").status, "ok")
        self.assertEqual(only("150 ct = 1,50 €").status, "ok")
        self.assertEqual(only("3 · 2,49 € = 7,46 €").status, "error")

    def test_physics_formulas(self):
        self.assertEqual(statuses("U = R · I = 20 Ω · 0,5 A = 10 V"), [("ok", "")])
        self.assertEqual(only("F = m · a = 2 kg · 3 m/s² = 6 N").status, "ok")
        self.assertEqual(only("P = U · I = 230 V · 2 A = 460 W").status, "ok")
        self.assertEqual(only("E = P · t = 2 kW · 3 h = 6 kWh").status, "ok")
        self.assertEqual(only("v = s : t = 100 m : 20 s = 5 m/s").status, "ok")
        self.assertEqual(only("v = s : t = 100 m : 20 s = 50 m/s").status, "error")

    def test_multi_step_physics_with_known_values(self):
        lesson = {"parts": [{"kind": "worked_example", "title": "Beispiel",
                             "body": "Gegeben: m = 2 kg, a = 3 m/s².",
                             "items": [{"label": "Schritt 1", "text": "F = m · a = 2 kg · 3 m/s² = 6 N"},
                                       {"label": "Schritt 2", "text": "W = F · s = 6 N · 4 m = 24 J"}]}]}
        report = mc.check_lesson(lesson)
        self.assertEqual(report.state, mc.VERIFIED, [check.as_dict() for check in report.checks])
        lesson["parts"][0]["items"][1]["text"] = "W = F · s = 6 N · 4 m = 20 J"
        self.assertEqual(mc.check_lesson(lesson).state, mc.NEEDS_REGENERATION)


class AmbiguityTests(unittest.TestCase):
    def test_prose_and_variables_are_never_verified(self):
        self.assertEqual(statuses("(3x - 2)/5 = 4 ⇒ 3x - 2 = 20 ⇒ 3x = 22"), [])
        self.assertEqual(statuses("Das Ergebnis ist ungefähr gleich groß."), [])
        self.assertEqual(statuses("Z = K · p : 100"), [])

    def test_a_number_with_two_readings_is_accepted_only_if_a_reading_fits(self):
        self.assertEqual(only("1.200 € · 3 : 100 = 36 €").status, "ok", "1.200 = 1200 in German")
        self.assertEqual(only("1.200 € · 3 : 100 = 0,036 €").status, "ok", "or 1.2, an English decimal")
        self.assertEqual(only("1.200 € · 3 : 100 = 5 €").status, "error")

    def test_known_values_make_symbols_checkable(self):
        lesson = {"parts": [{"kind": "worked_example", "title": "Beispiel",
                             "body": "K = 1200 €, p = 3, t = 90",
                             "items": [{"label": "Einsetzen", "text": "Z = K · p · t : 36000 = 9 €"}]}]}
        self.assertEqual(mc.check_lesson(lesson).state, mc.VERIFIED)
        lesson["parts"][0]["items"][0]["text"] = "Z = K · p · t : 36000 = 90 €"
        self.assertEqual(mc.check_lesson(lesson).state, mc.NEEDS_REGENERATION)

    def test_a_lesson_without_calculations_is_unverified_not_verified(self):
        lesson = {"parts": [{"kind": "explanation", "title": "Was ist Zins?", "body": "Zinsen sind ein Preis für Geld."}]}
        self.assertEqual(mc.check_lesson(lesson).state, mc.UNVERIFIED)

    def test_partly_checkable_is_partially_verified(self):
        lesson = {"parts": [{"kind": "explanation", "title": "x",
                             "body": "100 · 4 · 10 : 36000 = 0,11 €\nZ = 5 Äpfel + 2 = 7 Kisten"}]}
        report = mc.check_lesson(lesson)
        self.assertIn(report.state, (mc.PARTIALLY_VERIFIED, mc.VERIFIED))
        lesson["parts"][0]["body"] = "Z = 1200 € · 3 : 100 = 36\n100 · 4 · 10 : 36000 = 0,11 €"
        self.assertEqual(mc.check_lesson(lesson).state, mc.PARTIALLY_VERIFIED, "a missing unit is not 'verified'")


class LessonTests(unittest.TestCase):
    def test_the_worked_examples_answer_must_match_its_last_step(self):
        lesson = {"worked_example": {"problem": "Tageszinsen für 1200 €, 3 %, 90 Tage",
                                     "steps": ["1200 · 3 · 90 = 324000", "324000 : 36000 = 9"],
                                     "answer": "Z = 90 €"}}
        report = mc.check_lesson(lesson)
        self.assertIn("answer_mismatch", [check.kind for check in report.errors])
        lesson["worked_example"]["answer"] = "Z = 9 €"
        self.assertEqual(mc.check_lesson(lesson).state, mc.VERIFIED)

    def test_question_options_are_not_judged(self):
        lesson = {"question": {"prompt": "Wie viel ist 2 + 2?", "options": [{"id": "a", "label": "2 + 2 = 5"}]}}
        self.assertEqual(mc.check_lesson(lesson).checks, [])

    def test_a_wrong_rearrangement_of_the_teachers_formula_is_found(self):
        lesson = {"parts": [{"kind": "rule", "title": "Umstellen", "body": "G = W · p : 100"}]}
        report = mc.check_lesson(lesson, teacher_formulas=["W = G · p : 100"])
        self.assertEqual([check.kind for check in report.errors], ["rearrangement"])
        lesson["parts"][0]["body"] = "G = W · 100 : p"
        self.assertEqual(mc.check_lesson(lesson, teacher_formulas=["W = G · p : 100"]).state, mc.VERIFIED)

    def test_equivalent_formula_forms(self):
        self.assertTrue(mc.formulas_equivalent("Z = K · p · t : 36000", r"Z = \frac{K \cdot p \cdot t}{36000}"))
        self.assertTrue(mc.formulas_equivalent("W = G · p : 100", "p = W · 100 : G"))
        self.assertFalse(mc.formulas_equivalent("W = G · p : 100", "p = W · G : 100"))
        self.assertIsNone(mc.formulas_equivalent("W = G · p : 100", "F = m · a"))

    def test_subscripts_and_short_words(self):
        self.assertTrue(mc.formulas_equivalent("K_n = K_0 · q^n", "Kₙ = K₀ · qⁿ"))
        # "also", "am" are words: the wrong value next to them is still found.
        self.assertEqual(statuses("100 · 4 · 10 : 36000 = 0,0111 € (also 0,0111 €)")[0], ("error", "decimal_place"))

    def test_english_thousands(self):
        self.assertEqual(only("1,500 · 2 = 3,000", language="en").status, "ok")
        self.assertEqual(only("1.5 · 2 = 3", language="en").status, "ok")

    def test_teacher_formula_used(self):
        lesson = {"parts": [{"kind": "rule", "title": "Formel", "body": r"$$Z = K \cdot \frac{p \cdot t}{36000}$$"}]}
        self.assertTrue(mc.teacher_formula_used(lesson, "Z = K · p · t : 36000"))
        self.assertFalse(mc.teacher_formula_used(lesson, "W = G · p : 100"))


class SafetyTests(unittest.TestCase):
    def test_nothing_is_executed(self):
        for text in ("__import__('os').system('x') = 0", "eval(1) = 1", "().__class__ = 1", "2 ** 999999 = 1"):
            with self.subTest(text=text):
                mc.check_text(text)       # no exception, nothing run
        self.assertEqual(statuses("10^99999 = 1"), [("unverifiable", "")], "too large to check: never verified")

    def test_formatting(self):
        self.assertEqual(mc.format_number(Fraction(1, 9), 2), "0,11")
        self.assertEqual(mc.format_number(Fraction(1, 8), 2, "en"), "0.13")


if __name__ == "__main__":
    unittest.main()

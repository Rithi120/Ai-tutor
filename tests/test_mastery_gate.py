"""The knowledge gate on plain values: what "known" means, when a test stops, what comes next."""

import unittest

from learnova.quizzes import mastery_gate as gate
from learnova.quizzes.mastery_gate import Observation, Prior


def correct(concept, difficulty=1, weight=1.2):
    return Observation(concept=concept, score=100, weight=weight, difficulty=difficulty)


def wrong(concept, difficulty=1, weight=1.2, score=20):
    return Observation(concept=concept, score=score, weight=weight, difficulty=difficulty)


class EstimateTests(unittest.TestCase):
    def test_a_fresh_concept_is_untested_at_zero(self):
        result = gate.estimate("Fractions", None, [])
        self.assertEqual((result.knowledge, result.status, result.known), (0.0, "untested", False))

    def test_two_correct_answers_at_easy_level_are_not_enough(self):
        # Score is fine (100) and evidence is just enough, but nothing above the easiest
        # level has been shown yet.
        result = gate.estimate("Fractions", None, [correct("Fractions"), correct("Fractions", weight=1.3)])
        self.assertGreaterEqual(result.knowledge, gate.TARGET)
        self.assertFalse(result.known)
        self.assertEqual(result.status, "learning")

    def test_a_harder_correct_answer_with_enough_evidence_makes_it_known(self):
        result = gate.estimate("Fractions", None, [correct("Fractions"), correct("Fractions", difficulty=2, weight=1.3)])
        self.assertTrue(result.known)
        self.assertEqual(result.hardest_correct, 2)

    def test_one_wrong_answer_pulls_a_known_concept_back_below_target(self):
        answers = [correct("Fractions"), correct("Fractions", difficulty=2), wrong("Fractions", difficulty=2)]
        result = gate.estimate("Fractions", None, answers)
        self.assertLess(result.knowledge, gate.TARGET)
        self.assertFalse(result.known)

    def test_a_strong_prior_needs_one_confirming_answer_and_never_outweighs_the_session(self):
        prior = Prior(score=95, weight=12.0)       # months of evidence
        untouched = gate.estimate("Fractions", prior, [])
        self.assertEqual(untouched.status, "untested")
        self.assertFalse(untouched.known, "the record alone does not pass a test")
        confirmed = gate.estimate("Fractions", prior, [correct("Fractions")])
        self.assertTrue(confirmed.known, "one confirming answer is enough for a known concept")
        contradicted = gate.estimate("Fractions", prior, [wrong("Fractions"), wrong("Fractions"), wrong("Fractions")])
        self.assertLess(contradicted.knowledge, gate.TARGET,
                        "three wrong answers today outweigh a capped prior")

    def test_hints_and_unverified_answers_count_for_less(self):
        full = gate.estimate("F", None, [correct("F", 2, weight=1.3), correct("F", 2, weight=1.3)])
        hinted = gate.estimate("F", None, [correct("F", 2, weight=0.6), correct("F", 2, weight=0.6)])
        self.assertTrue(full.known)
        self.assertFalse(hinted.known, "low-weight evidence does not reach the evidence floor")

    def test_zero_weight_observations_are_ignored(self):
        result = gate.estimate("F", None, [Observation("F", 100, 0.0, 3)])
        self.assertEqual(result.status, "untested")

    def test_estimates_follow_the_focus_order_and_ignore_drift(self):
        observations = [correct("B", 2), correct("Drift", 3, weight=5)]
        result = gate.estimates_for(["A", "B"], {}, observations)
        self.assertEqual([item.concept for item in result], ["A", "B"])
        self.assertEqual(result[0].status, "untested")
        self.assertEqual(result[1].attempts, 1)


class DecisionTests(unittest.TestCase):
    def known(self, name):
        return gate.estimate(name, None, [correct(name), correct(name, 2, weight=1.3)])

    def learning(self, name, score=20):
        return gate.estimate(name, None, [wrong(name, score=score)])

    def test_never_stops_before_the_minimum_even_when_everything_is_known(self):
        decision = gate.decide(answered=2, estimates=[self.known("A")], current_concept="A",
                               last_score=100, run_on_current=2)
        self.assertFalse(decision.stop)
        self.assertEqual(decision.reason, "minimum_pending")
        self.assertEqual(decision.next_concept, "A", "confirms the least-evidenced concept")

    def test_stops_at_the_minimum_once_every_concept_is_known(self):
        decision = gate.decide(answered=3, estimates=[self.known("A"), self.known("B")],
                               current_concept="B", last_score=100, run_on_current=1)
        self.assertTrue(decision.stop)
        self.assertEqual(decision.reason, "target_reached")
        self.assertTrue(decision.reached)

    def test_always_stops_at_the_maximum(self):
        decision = gate.decide(answered=15, estimates=[self.learning("A")], current_concept="A",
                               last_score=20, run_on_current=15)
        self.assertTrue(decision.stop)
        self.assertEqual(decision.reason, "max_questions")
        self.assertEqual([item.concept for item in decision.below_target], ["A"])

    def test_stays_on_a_concept_the_student_just_got_wrong(self):
        decision = gate.decide(answered=4, estimates=[self.learning("A"), gate.estimate("B", None, [])],
                               current_concept="A", last_score=20, run_on_current=1)
        self.assertEqual((decision.next_concept, decision.stay_on_current), ("A", True))

    def test_moves_on_after_four_in_a_row_on_one_failing_concept(self):
        decision = gate.decide(answered=5, estimates=[self.learning("A"), gate.estimate("B", None, [])],
                               current_concept="A", last_score=20, run_on_current=4)
        self.assertEqual(decision.next_concept, "B")

    def test_untested_concepts_come_before_revisiting_a_weak_one(self):
        estimates = [self.learning("A"), gate.estimate("B", None, []), gate.estimate("C", None, [])]
        decision = gate.decide(answered=3, estimates=estimates, current_concept="A",
                               last_score=100, run_on_current=1)
        self.assertEqual(decision.next_concept, "B")

    def test_the_weakest_open_concept_comes_next_and_a_change_of_concept_is_preferred(self):
        estimates = [self.learning("A", score=60), self.learning("B", score=10), self.known("C")]
        decision = gate.decide(answered=6, estimates=estimates, current_concept="B",
                               last_score=100, run_on_current=1)
        self.assertEqual(decision.next_concept, "A", "B was just answered correctly; A is also open")

    def test_a_remedial_planner_action_keeps_the_concept_even_after_a_correct_score(self):
        decision = gate.decide(answered=4, estimates=[self.learning("A", score=70), gate.estimate("B", None, [])],
                               current_concept="A", last_score=85, run_on_current=1,
                               planner_action="worked_example_then_practice")
        self.assertEqual(decision.next_concept, "A")

    def test_the_gate_asks_for_a_harder_question_once_an_easy_one_was_right(self):
        easy_right = gate.estimate("A", None, [correct("A", 1)])
        decision = gate.decide(answered=3, estimates=[easy_right], current_concept="A",
                               last_score=100, run_on_current=1)
        self.assertEqual(decision.next_concept, "A")
        self.assertTrue(decision.wants_harder, "the planner would hold level 1; the gate needs level 2")
        fresh = gate.decide(answered=1, estimates=[gate.estimate("A", None, [])], current_concept="A",
                            last_score=100, run_on_current=1)
        self.assertFalse(fresh.wants_harder, "nothing shown yet, so nothing to confirm")
        after_wrong = gate.decide(answered=2, estimates=[self.learning("A")], current_concept="A",
                                  last_score=20, run_on_current=1)
        self.assertFalse(after_wrong.wants_harder, "a wrong answer is re-taught, not escalated")

    def test_after_a_mistake_the_confirming_question_must_transfer(self):
        # Wrong once, then right once at the same kind of task: not known yet, and the
        # next question on A has to change context rather than repeat the pattern.
        recovered = gate.estimate("A", None, [wrong("A"), correct("A", 2)])
        decision = gate.decide(answered=3, estimates=[recovered], current_concept="A",
                               last_score=100, run_on_current=2)
        self.assertEqual(decision.next_concept, "A")
        self.assertTrue(decision.last_correct)
        self.assertTrue(decision.wants_transfer)
        # While the planner is still re-teaching after the mistake, no transfer yet.
        reteaching = gate.decide(answered=2, estimates=[self.learning("A")], current_concept="A",
                                 last_score=20, run_on_current=1)
        self.assertTrue(reteaching.stay_on_current)
        self.assertFalse(reteaching.last_correct)
        self.assertFalse(reteaching.wants_transfer)
        # Never a mistake: the ordinary harder confirmation, no transfer demanded.
        clean = gate.decide(answered=2, estimates=[gate.estimate("A", None, [correct("A", 1)])],
                            current_concept="A", last_score=100, run_on_current=1)
        self.assertFalse(clean.wants_transfer)
        self.assertEqual(clean.estimates[0].mistakes, 0)

    def test_bounds_are_clamped_and_never_inverted(self):
        self.assertEqual(gate.bounds(3, 15), (3, 15))
        self.assertEqual(gate.bounds(10, 4), (4, 4))
        self.assertEqual(gate.bounds("x", 99), (3, gate.HARD_LIMIT))
        self.assertEqual(gate.bounds(0, 0), (1, 1))

    def test_the_progress_view_is_numbers_and_labels_only(self):
        decision = gate.decide(answered=3, estimates=[self.known("A"), self.learning("B")],
                               current_concept="B", last_score=20, run_on_current=1)
        view = decision.as_dict()
        self.assertEqual((view["known"], view["total"], view["reached"], view["complete"]), (1, 2, False, False))
        self.assertEqual(view["concepts"][0]["status"], "known")
        self.assertEqual(set(view["concepts"][1]), {"concept", "knowledge", "confidence", "attempts", "known", "status"})


class PredictionTests(unittest.TestCase):
    def test_if_wrong_stays_and_if_correct_moves_on_once_the_concept_would_be_known(self):
        observations = [correct("A", 1)]
        if_correct, if_wrong = gate.targets_if(["A", "B"], {}, observations, "A",
                                               difficulty=2, run_on_current=2)
        self.assertEqual(if_wrong, "A")
        self.assertEqual(if_correct, "B", "a second, harder correct answer would finish A")

    def test_if_correct_goes_breadth_first_when_one_more_answer_is_not_enough(self):
        # One easy correct answer does not finish A, but the gate still moves to the untested
        # concept first and returns to A for its harder question later - covering the lesson
        # beats drilling one idea.
        if_correct, if_wrong = gate.targets_if(["A", "B"], {}, [], "A", difficulty=1, run_on_current=1)
        self.assertEqual((if_correct, if_wrong), ("B", "A"))
        only, _ = gate.targets_if(["A"], {}, [], "A", difficulty=1, run_on_current=1)
        self.assertEqual(only, "A", "with nothing else to test, A continues")


class FormatTests(unittest.TestCase):
    def test_early_slots_are_unchanged_and_late_slots_rotate_with_little_typing(self):
        self.assertEqual([gate.question_type_for(n) for n in (2, 3, 4, 5)], ["checkboxes", "dropdown", "ordering", "text"])
        late = [gate.question_type_for(n) for n in range(6, 14)]
        self.assertEqual(late.count("text"), 2)
        self.assertEqual(gate.question_type_for(4, pictures=2), "photo_ordering")
        self.assertEqual(gate.question_type_for(5, pictures=1), "photo_response")
        self.assertEqual(gate.question_type_for(6, pictures=3), "checkboxes", "pictures never fill late slots")

    def test_run_length_counts_the_trailing_streak_only(self):
        history = [{"concept": "A"}, {"concept": "B"}, {"concept": "B"}]
        self.assertEqual(gate.run_length(history, "B"), 2)
        self.assertEqual(gate.run_length(history, "A"), 0)


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timedelta, timezone

from adaptive_learning import (
    mastery_status,
    prioritize_concepts,
    review_interval_days,
    update_mastery,
)


NOW = datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc)


def state(score=50, **overrides):
    value = {
        "mastery_score": score,
        "attempts": 3,
        "correct_attempts": 1,
        "incorrect_attempts": 1,
        "consecutive_correct": 0,
        "consecutive_incorrect": 0,
        "difficulty_level": 2,
    }
    value.update(overrides)
    return value


class AdaptiveLearningRuleTests(unittest.TestCase):
    def test_correct_answers_increase_mastery_and_hints_reduce_gain(self):
        without_hint = update_mastery(state(), 100, hints_used=False, practised_at=NOW)
        with_hint = update_mastery(state(), 100, hints_used=True, practised_at=NOW)
        self.assertGreater(without_hint["mastery_score"], 50)
        self.assertGreater(without_hint["mastery_score"], with_hint["mastery_score"])
        self.assertEqual(without_hint["correct_attempts"], 2)

    def test_incorrect_and_repeated_incorrect_answers_decrease_mastery(self):
        first = update_mastery(state(score=60), 20, practised_at=NOW)
        repeated = update_mastery(
            state(score=60, consecutive_incorrect=2), 20, practised_at=NOW
        )
        self.assertLess(first["mastery_score"], 60)
        self.assertLess(repeated["mastery_score"], first["mastery_score"])
        self.assertEqual(first["next_review_at"], NOW + timedelta(days=1))

    def test_mastery_is_clamped_and_status_is_deterministic(self):
        self.assertEqual(update_mastery(state(score=98), 100, practised_at=NOW)["mastery_score"], 100)
        self.assertEqual(update_mastery(state(score=2), 0, practised_at=NOW)["mastery_score"], 0)
        self.assertEqual([mastery_status(value) for value in (10, 40, 75, 90)], [
            "weak", "learning", "strong", "mastered"
        ])

    def test_review_intervals(self):
        self.assertEqual([review_interval_days(value) for value in (29, 30, 50, 70, 85)], [1, 3, 7, 14, 30])

    def test_difficulty_changes_after_two_answer_streaks(self):
        harder = update_mastery(
            state(score=55, consecutive_correct=1, difficulty_level=2), 100, practised_at=NOW
        )
        easier = update_mastery(
            state(score=55, consecutive_incorrect=1, difficulty_level=2), 10, practised_at=NOW
        )
        self.assertEqual(harder["difficulty_level"], 3)
        self.assertEqual(easier["difficulty_level"], 1)

    def test_overdue_and_repeated_mistakes_are_prioritised_and_diversified(self):
        concepts = [
            {"id": 1, "subject": "Math", "concept": "Strong", "mastery_score": 90,
             "consecutive_incorrect": 0, "last_practised_at": NOW, "next_review_at": NOW + timedelta(days=20)},
            {"id": 2, "subject": "Math", "concept": "Overdue", "mastery_score": 45,
             "consecutive_incorrect": 0, "last_practised_at": NOW - timedelta(days=8), "next_review_at": NOW - timedelta(days=2)},
            {"id": 3, "subject": "Math", "concept": "Repeated", "mastery_score": 35,
             "consecutive_incorrect": 3, "last_practised_at": NOW - timedelta(days=2), "next_review_at": NOW + timedelta(days=1)},
        ]
        plan = prioritize_concepts(concepts, question_count=5, now=NOW)
        self.assertEqual(plan[0]["concept"], "Overdue")
        self.assertIn("Repeated", [item["concept"] for item in plan[:3]])
        self.assertTrue(all(plan[index]["id"] != plan[index + 1]["id"] for index in range(len(plan) - 1)))


if __name__ == "__main__":
    unittest.main()

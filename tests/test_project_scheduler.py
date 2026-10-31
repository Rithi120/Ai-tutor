"""Deterministic Project Mode scheduler (learnova/projects/scheduler.py)."""

import unittest
from datetime import date, timedelta

from learnova.projects.scheduler import (
    REVIEW_LADDER, Availability, ProjectInput, TopicInput, build_plan, next_review,
)

TODAY = date(2026, 11, 2)   # a Monday


def fifteen_topics(project_id=1, **overrides):
    """5 easy (15 min), 6 medium (20 min), 4 demanding (30 min); some weak, most unassessed."""
    specs = (
        [(f"easy-{n}", 1, 15, 1 + n % 2) for n in range(5)]
        + [(f"medium-{n}", 2, 20, 2) for n in range(6)]
        + [(f"hard-{n}", 3, 30, 3) for n in range(4)]
    )
    topics = []
    for index, (name, difficulty, minutes, importance) in enumerate(specs):
        topics.append(TopicInput(
            id=f"p{project_id}:{name}", project_id=project_id, title=name.title(), minutes=minutes,
            difficulty=difficulty, importance=importance,
            mastery=20.0 if index % 4 == 0 else 0.0, assessed=index % 4 == 0, **overrides))
    return tuple(topics)


def project(topics, *, project_id=1, days_left=6, exam=True):
    return ProjectInput(id=project_id, title=f"Project {project_id}",
                        exam_date=TODAY + timedelta(days=days_left) if exam else None, topics=topics)


def kinds(day):
    return [task["kind"] for task in day.tasks]


class SixDayFifteenTopicTests(unittest.TestCase):
    def setUp(self):
        self.availability = Availability(daily_minutes=75)
        self.plan = build_plan([project(fifteen_topics())], self.availability, today=TODAY)
        self.days = self.plan.days

    def test_six_study_days_and_capacity_is_never_exceeded(self):
        self.assertEqual([day.date for day in self.days], [TODAY + timedelta(days=n) for n in range(6)])
        for day in self.days:
            self.assertLessEqual(day.minutes, day.capacity, day.date)
            self.assertLessEqual(day.capacity, 75)

    def test_day_one_mixes_a_demanding_topic_with_manageable_ones(self):
        learn = [task for task in self.days[0].tasks if task["kind"] == "learn"]
        self.assertGreaterEqual(len(learn), 2)
        self.assertEqual(sum(task["difficulty"] == 3 for task in learn), 1)
        self.assertTrue(any(task["difficulty"] < 3 for task in learn))

    def test_day_two_reviews_day_one_topics_and_adds_new_ones_if_time_permits(self):
        day_one_topics = {task["topic_id"] for task in self.days[0].tasks if task["kind"] == "learn"}
        reviewed = {task["topic_id"] for task in self.days[1].tasks if task["kind"] == "review"}
        self.assertEqual(day_one_topics, reviewed)
        self.assertIn("learn", kinds(self.days[1]))

    def test_day_four_revisits_day_one_topics_at_the_three_day_interval(self):
        day_one_topics = {task["topic_id"] for task in self.days[0].tasks if task["kind"] == "learn"}
        reviewed = {task["topic_id"] for task in self.days[3].tasks if task["kind"] == "review"}
        self.assertLessEqual(day_one_topics, reviewed)

    def test_day_five_has_mixed_practice_and_day_six_the_timed_exam_then_revision(self):
        self.assertIn("mixed_practice", kinds(self.days[4]))
        self.assertEqual(kinds(self.days[5])[0], "mock_exam")
        self.assertIn("final_review", kinds(self.days[5]))
        self.assertNotIn("learn", kinds(self.days[5]), "no new topic on the exam-practice day")

    def test_demanding_topics_are_spread_and_scheduled_early(self):
        hard_days = [index for index, day in enumerate(self.days)
                     for task in day.tasks if task["kind"] == "learn" and task["difficulty"] == 3]
        self.assertEqual(len(hard_days), len(set(hard_days)), "at most one demanding topic per day here")
        self.assertLessEqual(max(hard_days), 3, "demanding topics leave time for practice before the exam")
        # No new topic on the last two days: day 5 consolidates, day 6 is the practice exam.
        self.assertNotIn("learn", kinds(self.days[4]))

    def test_what_does_not_fit_is_reported_lowest_priority_first(self):
        # A topic whose second part found no room is reported as not fitting, even though
        # its first part is planned: a half-learned topic is still at risk.
        learned = {task["topic_id"] for day in self.days for task in day.tasks if task["kind"] == "learn"}
        self.assertEqual(len(learned | set(self.plan.unscheduled)), 15)
        learned -= set(self.plan.unscheduled)
        if self.plan.unscheduled:
            warning = next(w for w in self.plan.warnings if w["code"] == "insufficient_time")
            self.assertEqual(warning["topic_ids"], self.plan.unscheduled)
            self.assertFalse(self.plan.feasible)
            # Nothing dropped outranks anything planned: important, demanding, weak first.
            from learnova.projects.scheduler import _priority
            topics = {topic.id: topic for topic in fifteen_topics()}
            planned = min(_priority(topics[t], 0) for t in learned)
            dropped = max(_priority(topics[t], 0) for t in self.plan.unscheduled)
            self.assertLessEqual(dropped, planned + 3.0, "only cheap fillers may beat a dropped topic")
            self.assertTrue(all(topics[t].difficulty < 3 for t in self.plan.unscheduled), "every demanding topic is planned")

    def test_enough_time_plans_every_topic_and_says_so(self):
        # 4 learning days (the last two consolidate and test) must hold 315 learning
        # minutes plus their spaced reviews.
        plan = build_plan([project(fifteen_topics())], Availability(daily_minutes=220), today=TODAY)
        learned = {task["topic_id"] for day in plan.days for task in day.tasks if task["kind"] == "learn"}
        self.assertEqual(len(learned), 15)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.unscheduled, [])

    def test_the_plan_is_deterministic(self):
        again = build_plan([project(fifteen_topics())], self.availability, today=TODAY)
        self.assertEqual([[t["id"] for t in d.tasks] for d in again.days],
                         [[t["id"] for t in d.tasks] for d in self.days])


class SchedulingRuleTests(unittest.TestCase):
    def test_prerequisites_are_learned_first(self):
        topics = (
            TopicInput("t:advanced", 1, "Advanced", minutes=20, difficulty=3, importance=3, prerequisites=("t:basics",)),
            TopicInput("t:basics", 1, "Basics", minutes=15, difficulty=1, importance=1),
        )
        plan = build_plan([project(topics)], Availability(daily_minutes=30), today=TODAY)
        order = [task["topic_id"] for day in plan.days for task in day.tasks if task["kind"] == "learn"]
        self.assertEqual(order, ["t:basics", "t:advanced"])

    def test_a_long_topic_is_split_into_parts_that_fit(self):
        topics = (TopicInput("t:long", 1, "Long", minutes=70, difficulty=2),)
        plan = build_plan([project(topics)], Availability(daily_minutes=60, session_cap=40), today=TODAY)
        parts = [task for day in plan.days for task in day.tasks if task["kind"] == "learn"]
        self.assertEqual([task["minutes"] for task in parts], [35, 35])
        self.assertEqual([task["id"] for task in parts], ["learn:t:long:1", "learn:t:long:2"])

    def test_unavailable_days_get_nothing(self):
        plan = build_plan([project(fifteen_topics())], Availability(daily_minutes=90, unavailable_weekdays=frozenset({1})), today=TODAY)
        tuesday = next(day for day in plan.days if day.date.weekday() == 1)
        self.assertEqual(tuesday.tasks, [])
        self.assertEqual(tuesday.capacity, 0)

    def test_no_exam_date_gives_a_flexible_plan_without_a_mock_or_a_deadline_warning(self):
        plan = build_plan([project(fifteen_topics(), exam=False)], Availability(daily_minutes=40), today=TODAY)
        self.assertEqual(len(plan.days), 14)
        self.assertNotIn("mock_exam", [k for day in plan.days for k in kinds(day)])
        self.assertFalse([w for w in plan.warnings if w["code"] == "insufficient_time"])

    def test_two_exams_share_one_daily_budget(self):
        near = project(fifteen_topics(1), project_id=1, days_left=4)
        far = project(fifteen_topics(2), project_id=2, days_left=10)
        plan = build_plan([near, far], Availability(daily_minutes=60), today=TODAY)
        for day in plan.days:
            self.assertLessEqual(day.minutes, day.capacity)
        first = plan.days[0]
        self.assertTrue(any(task["project_id"] == 1 for task in first.tasks), "the nearer exam is served first")
        self.assertTrue(plan.tasks_for(2), "the later exam is not starved")
        self.assertEqual(plan.days[3].tasks[0]["kind"], "mock_exam")
        self.assertEqual(plan.days[3].tasks[0]["project_id"], 1)


class RegenerationTests(unittest.TestCase):
    def test_completed_work_is_kept_and_never_duplicated(self):
        first = build_plan([project(fifteen_topics())], Availability(daily_minutes=75), today=TODAY)
        done = [dict(task, completed=True) for task in first.days[0].tasks]
        done_topics = {task["topic_id"] for task in done if task["kind"] == "learn"}
        topics = tuple(TopicInput(**{**t.__dict__, "learned": t.id in done_topics,
                                     "next_review_on": TODAY + timedelta(days=1) if t.id in done_topics else None})
                       for t in fifteen_topics())
        second = build_plan([project(topics, days_left=5)], Availability(daily_minutes=75),
                            today=TODAY + timedelta(days=1), preserved=done)
        ids = [task["id"] for day in second.days for task in day.tasks]
        self.assertEqual(len(ids), len(set(ids)), "no duplicate tasks after regeneration")
        relearned = {task["topic_id"] for day in second.days for task in day.tasks if task["kind"] == "learn"}
        self.assertFalse(relearned & done_topics, "finished topics are not taught again as new")
        self.assertTrue({task["topic_id"] for task in second.days[0].tasks if task["kind"] == "review"} >= done_topics)

    def test_a_missed_day_puts_overdue_reviews_first_without_penalty(self):
        learned = {"p1:hard-0", "p1:easy-0"}
        topics = tuple(TopicInput(**{**t.__dict__, "learned": t.id in learned,
                                     "next_review_on": TODAY + timedelta(days=1) if t.id in learned else None})
                       for t in fifteen_topics())
        # Student was away on day 2; the plan is rebuilt on day 3.
        plan = build_plan([project(topics)], Availability(daily_minutes=75), today=TODAY + timedelta(days=2))
        first = plan.days[0]
        self.assertEqual([task["kind"] for task in first.tasks[:2]], ["review", "review"])
        self.assertTrue(all(task["reason"] == "Overdue review" for task in first.tasks[:2]))
        self.assertLessEqual(first.minutes, first.capacity)
        self.assertEqual(plan.days[0].date, TODAY + timedelta(days=2))

    def test_a_pinned_task_keeps_its_day(self):
        pinned = {"id": "learn:p1:easy-3:1", "kind": "learn", "project_id": 1, "topic_id": "p1:easy-3",
                  "title": "Easy-3", "minutes": 15, "difficulty": 1, "reason": "Chosen by you",
                  "completed": False, "pinned": True, "date": (TODAY + timedelta(days=2)).isoformat()}
        plan = build_plan([project(fifteen_topics())], Availability(daily_minutes=75), today=TODAY, preserved=[pinned])
        self.assertIn(pinned["id"], [task["id"] for task in plan.days[2].tasks])
        all_ids = [task["id"] for day in plan.days for task in day.tasks]
        self.assertEqual(all_ids.count(pinned["id"]), 1)


class ReviewLadderTests(unittest.TestCase):
    def setUp(self):
        self.topic = TopicInput("t:x", 1, "X", learned=True, review_step=0)

    def test_independent_success_lengthens_hints_keep_and_errors_reset(self):
        correct = next_review(self.topic, outcome="correct", today=TODAY)
        self.assertEqual((correct.review_step, correct.next_review_on), (1, TODAY + timedelta(days=REVIEW_LADDER[1])))
        hinted = next_review(correct, outcome="hinted", today=TODAY)
        self.assertEqual(hinted.review_step, 1)
        wrong = next_review(correct, outcome="incorrect", today=TODAY)
        self.assertEqual((wrong.review_step, wrong.next_review_on, wrong.reteach), (0, TODAY + timedelta(days=1), False))
        repeated = next_review(correct, outcome="incorrect", today=TODAY, repeated_misconception=True)
        self.assertTrue(repeated.reteach)

    def test_unassessed_keeps_uncertainty_and_reviews_stay_before_the_exam(self):
        self.assertIs(next_review(self.topic, outcome="unassessed", today=TODAY), self.topic)
        late = next_review(TopicInput("t:y", 1, "Y", learned=True, review_step=2), outcome="correct",
                           today=TODAY, exam_date=TODAY + timedelta(days=4))
        self.assertEqual(late.next_review_on, TODAY + timedelta(days=3))

    def test_a_reteach_task_is_planned_before_new_material(self):
        topics = (TopicInput("t:mis", 1, "Mis", learned=True, reteach=True, minutes=20),
                  TopicInput("t:new", 1, "New", minutes=20, importance=3, difficulty=3))
        plan = build_plan([project(topics)], Availability(daily_minutes=60), today=TODAY)
        self.assertEqual(plan.days[0].tasks[0]["kind"], "reteach")


if __name__ == "__main__":
    unittest.main()

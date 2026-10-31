"""Deterministic study scheduler for Project Mode: topics -> days, across every exam.

Pure functions, no database and no AI. The same inputs always give the same plan,
so the plan is testable, explainable and safe to regenerate whenever something changes
(a missed day, new mastery evidence, a new topic, a moved exam date).

The rules, in the order they are applied to each day:

1. Work already done or pinned by the student stays where it is and uses capacity.
2. Each exam's last study day is reserved for a timed practice exam (when the plan has
   at least two days and the day has room), followed by targeted revision.
3. Reviews that are due come first: overdue before due, the nearest exam first.
4. A topic marked for reteaching (a repeated misconception) is taught again.
5. New topics fill what is left, highest priority first. A topic is only started when its
   prerequisites are already scheduled and at least one later day remains to review it.
   Demanding topics are spread out: at most one per day, unless there are more demanding
   topics than days left.
6. The day before a practice exam gets a short mixed-practice session.

Capacity is shared: the student has one daily budget, and every project draws from it.
A day is never planned past its capacity. When the material does not fit, the plan says
so (warnings + the unscheduled topics, lowest priority dropped first) instead of
pretending otherwise.

Spaced review starts at 1, 3 and 7 days after learning when the exam date allows, and
`next_review` moves a topic along (or back down) that ladder from each graded result.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Iterable

REVIEW_LADDER = (1, 3, 7, 14, 30)
REVIEW_MINUTES = {1: 8, 2: 10, 3: 12}
MIXED_PRACTICE_MINUTES = 15          # reserved for planning; the session may grow to the max
MIXED_PRACTICE_MAX_MINUTES = 30
MIN_SPLIT_MINUTES = 15
FINAL_REVIEW_MINUTES = 20
MIN_MOCK_MINUTES = 20
FLEXIBLE_HORIZON_DAYS = 14   # projects without an exam date get a rolling two-week plan


@dataclass(frozen=True)
class TopicInput:
    id: str                                # stable, e.g. "section:12"
    project_id: int
    title: str
    minutes: int = 20                      # one learning session: explain, example, practise, check
    difficulty: int = 2                    # 1 easy .. 3 demanding
    importance: int = 2                    # 1 low .. 3 high (teacher-marked material is 3)
    mastery: float = 0.0                   # 0..100
    assessed: bool = False
    learned: bool = False                  # the initial learning session is done
    prerequisites: tuple[str, ...] = ()
    next_review_on: date | None = None
    review_step: int = 0                   # position on REVIEW_LADDER
    reteach: bool = False                  # repeated misconception: teach differently before testing
    selected: bool = True


@dataclass(frozen=True)
class ProjectInput:
    id: int
    title: str
    exam_date: date | None
    topics: tuple[TopicInput, ...]
    mock_minutes: int = 45


@dataclass(frozen=True)
class Availability:
    daily_minutes: int = 45
    unavailable_weekdays: frozenset[int] = frozenset()
    unavailable_dates: frozenset[date] = frozenset()
    session_cap: int = 40                  # longest single block before a break
    buffer_ratio: float = 0.1              # kept free for things that take longer than planned

    def capacity(self, day: date) -> int:
        if day.weekday() in self.unavailable_weekdays or day in self.unavailable_dates:
            return 0
        return max(0, int(self.daily_minutes * (1 - self.buffer_ratio)))


@dataclass
class DayPlan:
    date: date
    capacity: int
    tasks: list[dict[str, Any]] = field(default_factory=list)

    @property
    def minutes(self) -> int:
        return sum(int(task["minutes"]) for task in self.tasks)

    @property
    def free(self) -> int:
        return self.capacity - self.minutes


@dataclass
class Plan:
    days: list[DayPlan]
    warnings: list[dict[str, Any]]
    unscheduled: list[str]

    @property
    def feasible(self) -> bool:
        return not any(warning["code"] == "insufficient_time" for warning in self.warnings)

    def tasks_for(self, project_id: int) -> dict[date, list[dict[str, Any]]]:
        result: dict[date, list[dict[str, Any]]] = {}
        for day in self.days:
            tasks = [task for task in day.tasks if task["project_id"] == project_id]
            if tasks:
                result[day.date] = tasks
        return result


# --------------------------------------------------------------------------- reviews

def next_review(topic: TopicInput, *, outcome: str, today: date, exam_date: date | None = None,
                repeated_misconception: bool = False) -> TopicInput:
    """Move a topic on the review ladder after one graded result.

    outcome: "correct" (independent), "hinted" (correct with help), "incorrect",
    or "unassessed" (nothing graded: uncertainty is kept, no date invented)."""

    if outcome == "unassessed":
        return topic
    step = topic.review_step
    reteach = False
    if outcome == "correct":
        step = min(step + 1, len(REVIEW_LADDER) - 1)
    elif outcome == "hinted":
        step = max(0, step)                 # same interval: not yet independent
    else:
        step = 0
        reteach = repeated_misconception
    when = today + timedelta(days=REVIEW_LADDER[step])
    if exam_date and when >= exam_date:
        when = max(today + timedelta(days=1), exam_date - timedelta(days=1))
        if when >= exam_date:
            when = None
    return replace(topic, review_step=step, next_review_on=when, reteach=reteach, learned=True)


# --------------------------------------------------------------------------- planning

def _priority(topic: TopicInput, dependents: int) -> float:
    weakness = (100 - topic.mastery) / 100 if topic.assessed else 0.7
    return topic.importance * 2 + topic.difficulty * 1.5 + weakness * 3 + dependents


def _task(kind: str, topic: TopicInput | None, project: ProjectInput, minutes: int, *,
          ident: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": ident, "kind": kind, "project_id": project.id,
        "topic_id": topic.id if topic else None, "title": topic.title if topic else project.title,
        "minutes": int(minutes), "difficulty": topic.difficulty if topic else 2,
        "reason": reason, "completed": False, "pinned": False, **extra,
    }


def _study_days(today: date, project: ProjectInput) -> list[date]:
    end = (project.exam_date - timedelta(days=1)) if project.exam_date else today + timedelta(days=FLEXIBLE_HORIZON_DAYS - 1)
    days, cursor = [], today
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _learn_parts(topic: TopicInput, cap: int) -> list[int]:
    parts = max(1, math.ceil(topic.minutes / cap))
    base, extra = divmod(topic.minutes, parts)
    return [base + (1 if index < extra else 0) for index in range(parts)]


def build_plan(projects: Iterable[ProjectInput], availability: Availability, *, today: date,
               preserved: Iterable[dict[str, Any]] = ()) -> Plan:
    """Plan every active project together over one shared daily budget."""

    projects = [project for project in projects if any(topic.selected for topic in project.topics)]
    preserved = [dict(task) for task in preserved]
    warnings: list[dict[str, Any]] = []
    study_days = {project.id: _study_days(today, project) for project in projects}
    all_dates = sorted({day for days_ in study_days.values() for day in days_})
    days = {day: DayPlan(day, availability.capacity(day)) for day in all_dates}
    preserved_ids = {task["id"] for task in preserved}
    preserved_dates = {task["id"]: date.fromisoformat(str(task["date"])) for task in preserved if task.get("date")}

    # 1. Completed and pinned work keeps its place (only days still in the plan).
    for task in preserved:
        day = preserved_dates.get(task["id"])
        if day is not None and day in days:
            days[day].tasks.append(task)

    topics_by_id = {topic.id: topic for project in projects for topic in project.topics if topic.selected}
    project_by_id = {project.id: project for project in projects}
    dependents: dict[str, int] = {}
    for topic in topics_by_id.values():
        for prerequisite in topic.prerequisites:
            dependents[prerequisite] = dependents.get(prerequisite, 0) + 1

    def priority(topic_id: str) -> float:
        return _priority(topics_by_id[topic_id], dependents.get(topic_id, 0))

    last_day: dict[int, date | None] = {}
    for project in projects:
        open_days = [day for day in study_days[project.id] if days[day].capacity > 0]
        last_day[project.id] = open_days[-1] if project.exam_date and len(open_days) >= 2 else None
        if project.exam_date and not open_days:
            warnings.append({"code": "no_study_days", "project_id": project.id,
                             "message": "There is no study day left before this exam."})
    final_days = {value for value in last_day.values() if value}

    # Reviews still to place: [original due date, topic id, ordinal]. The original date is
    # kept so a review rolled forward is still recognised as overdue.
    due_reviews: list[tuple[date, str, str]] = [
        (topic.next_review_on, topic.id, topic.next_review_on.isoformat())
        for topic in topics_by_id.values() if topic.learned and topic.next_review_on
    ]
    pending_learn: dict[str, list[int]] = {
        topic.id: _learn_parts(topic, availability.session_cap)
        for topic in topics_by_id.values() if not topic.learned
    }
    # How many parts each topic has *now* (a split adds one), so part ids stay unique.
    part_totals = {topic_id: len(parts) for topic_id, parts in pending_learn.items()}
    reteach_left = {topic.id for topic in topics_by_id.values() if topic.reteach}

    # Admission: which new topics the available time can realistically cover, decided in
    # priority order (nearest exam first on ties) *before* packing. Without this, small
    # low-priority topics took the gaps a higher-priority topic needed, and the plan then
    # reported the important topic as the one that did not fit.
    reserved = sum(MIN_MOCK_MINUTES + MIXED_PRACTICE_MINUTES for project in projects if last_day.get(project.id))
    learning_capacity = sum(days[d].free for d in all_dates if d not in final_days) - reserved // 2
    budget = learning_capacity - sum(REVIEW_MINUTES.get(topics_by_id[t].difficulty, 10) for _, t, _ in due_reviews)
    admitted: set[str] = set()
    for topic_id in sorted(pending_learn, key=lambda t: (-priority(t), t)):
        topic = topics_by_id[topic_id]
        cost = topic.minutes + 2 * REVIEW_MINUTES.get(topic.difficulty, 10)
        if cost <= budget:
            admitted.add(topic_id)
            budget -= cost

    def place(day: DayPlan, task: dict[str, Any]) -> bool:
        if task["id"] in preserved_ids or any(existing["id"] == task["id"] for existing in day.tasks):
            return True     # already there: regenerating never duplicates a task
        if task["minutes"] <= 0 or task["minutes"] > day.free:
            return False
        task["date"] = day.date.isoformat()
        day.tasks.append(task)
        return True

    def finish_learning(topic_id: str, on: date) -> None:
        del pending_learn[topic_id]
        project = project_by_id[topics_by_id[topic_id].project_id]
        for step, gap in enumerate(REVIEW_LADDER[:3]):
            review_day = on + timedelta(days=gap)
            if project.exam_date and review_day >= project.exam_date:
                break
            due_reviews.append((review_day, topic_id, f"after-{on.isoformat()}-{step + 1}"))

    # Learning already done or pinned in an earlier plan counts as scheduled.
    for topic_id in list(pending_learn):
        total = len(pending_learn[topic_id])
        done = [preserved_dates.get(f"learn:{topic_id}:{part}") for part in range(1, total + 1)]
        if all(done):
            finish_learning(topic_id, max(d for d in done if d))

    for day_date in all_dates:
        day = days[day_date]
        active = [project for project in projects if day_date in study_days[project.id]]
        urgency = {project.id: (project.exam_date - day_date).days if project.exam_date else 10_000 for project in active}

        # 2. Practice exam, then targeted revision, on each exam's last study day.
        for project in sorted(active, key=lambda item: urgency[item.id]):
            if last_day.get(project.id) != day_date:
                continue
            minutes = min(project.mock_minutes, day.free)
            if minutes >= MIN_MOCK_MINUTES:
                place(day, _task("mock_exam", None, project, minutes, ident=f"mock:{project.id}:{day_date.isoformat()}",
                                 reason="Timed practice exam over all chosen topics"))
            else:
                warnings.append({"code": "no_mock_time", "project_id": project.id,
                                 "message": "No room for a full practice exam before this exam."})
            minutes = min(FINAL_REVIEW_MINUTES, day.free)
            if minutes >= 5:
                place(day, _task("final_review", None, project, minutes,
                                 ident=f"final-review:{project.id}:{day_date.isoformat()}",
                                 reason="Targeted revision of what the practice exam shows"))

        # 3. Due reviews, overdue first, nearest exam first. On a project's practice-exam
        #    day the exam and its follow-up revision cover them.
        still_due = []
        for due, topic_id, ordinal in sorted(due_reviews, key=lambda item: (item[0], urgency.get(topics_by_id[item[1]].project_id, 10_000), item[1])):
            topic = topics_by_id[topic_id]
            project = project_by_id[topic.project_id]
            if due > day_date or project not in active:
                still_due.append((due, topic_id, ordinal))
                continue
            if last_day.get(project.id) == day_date:
                continue
            minutes = REVIEW_MINUTES.get(topic.difficulty, 10)
            reason = "Overdue review" if due < day_date else "Spaced review to keep it in memory"
            if not place(day, _task("review", topic, project, minutes, ident=f"review:{topic_id}:{ordinal}", reason=reason)):
                still_due.append((due, topic_id, ordinal))
        due_reviews = still_due

        # 4. Reteach before testing again.
        for topic_id in sorted(reteach_left):
            topic = topics_by_id[topic_id]
            project = project_by_id[topic.project_id]
            if project in active and place(day, _task("reteach", topic, project, max(10, int(topic.minutes * 0.6)),
                                                    ident=f"reteach:{topic_id}", reason="Repeated mistake: explained a different way")):
                reteach_left.discard(topic_id)

        # 5. New topics: admitted ones by priority; the rest only fill time once every
        #    admitted topic has its place.
        eves = {value - timedelta(days=1) for value in final_days}
        learning_days_left = sum(1 for d in all_dates if d >= day_date and d not in final_days | eves and days[d].capacity > 0)
        hard_left = sum(1 for topic_id in pending_learn if topic_id in admitted and topics_by_id[topic_id].difficulty >= 3)
        hard_today = 0
        # One demanding topic a day, unless more of them remain than days to learn them in.
        hard_cap = max(1, math.ceil(hard_left / max(1, learning_days_left)))
        candidates = sorted(
            pending_learn,
            key=lambda topic_id: (topic_id not in admitted, urgency.get(topics_by_id[topic_id].project_id, 10_000) > 3,
                                  -priority(topic_id), topic_id),
        )
        for topic_id in candidates:
            if topic_id not in pending_learn:
                continue
            if topic_id not in admitted:
                # A topic beyond the admitted set may still use time no admitted topic can,
                # as long as the review budget it needs is genuinely left over.
                cost = topics_by_id[topic_id].minutes + 2 * REVIEW_MINUTES.get(topics_by_id[topic_id].difficulty, 10)
                if cost > budget or topics_by_id[topic_id].minutes > day.free:
                    continue
            topic = topics_by_id[topic_id]
            project = project_by_id[topic.project_id]
            if project not in active or day_date == last_day.get(project.id):
                continue
            later = [d for d in study_days[project.id]
                     if d > day_date and days[d].capacity > 0 and d != last_day.get(project.id)]
            if project.exam_date and not later:
                continue     # no ordinary day left to review it: learning it now would not stick
            if any(prerequisite in pending_learn for prerequisite in topic.prerequisites):
                continue
            if topic.difficulty >= 3 and hard_today >= hard_cap:
                continue
            learning_later = [d for d in later if d not in eves]
            # Keep room for more important work: a lower-priority topic waits when placing
            # it would leave the remaining learning days too small for the higher-priority
            # topics still pending (after the reviews already due on those days).
            mine = priority(topic_id)
            waiting = sum(sum(pending_learn[other]) for other in pending_learn
                          if other != topic_id and other in admitted and priority(other) > mine)
            if waiting:
                # Later room = free time on later learning days, minus the reviews already
                # due there and the next-day review this topic would add. Today's leftover is
                # not counted: what is waiting could not be placed today anyway.
                due_later = sum(REVIEW_MINUTES.get(topics_by_id[t].difficulty, 10)
                                for due, t, _ in due_reviews if due in learning_later)
                room_later = (sum(days[d].free for d in learning_later) - due_later
                              - REVIEW_MINUTES.get(topic.difficulty, 10))
                if waiting > room_later:
                    continue
            parts = pending_learn[topic_id]
            total = part_totals[topic_id]
            while parts and f"learn:{topic_id}:{total - len(parts) + 1}" in preserved_ids:
                parts.pop(0)             # a part done or pinned earlier keeps its own day
            if not parts:
                finish_learning(topic_id, day_date)
                continue
            # A long topic that does not fit today's gap starts now and continues on the
            # next day (a natural break), instead of losing its place to a smaller topic.
            already_split = any(str(ident).startswith(f"learn:{topic_id}:") for ident in preserved_ids)
            if (parts[0] > day.free >= MIN_SPLIT_MINUTES and parts[0] >= 2 * MIN_SPLIT_MINUTES
                    and learning_later and not already_split):
                first = min(day.free, parts[0] - MIN_SPLIT_MINUTES)
                parts[0:1] = [first, parts[0] - first]
                total = part_totals[topic_id] = total + 1
            number = total - len(parts) + 1
            task = _task("learn", topic, project, parts[0], ident=f"learn:{topic_id}:{number}",
                         reason="New topic" if total == 1 else f"New topic, part {number} of {total}",
                         part=number, parts=total)
            if not place(day, task):
                continue
            if topic_id not in admitted:
                admitted.add(topic_id)
                budget -= topic.minutes + 2 * REVIEW_MINUTES.get(topic.difficulty, 10)
            parts.pop(0)
            if topic.difficulty >= 3:
                hard_today += 1
            if not parts:
                finish_learning(topic_id, day_date)

        # 6. Mixed practice the day before a practice exam.
        for project in active:
            final = last_day.get(project.id)
            if final and day_date == final - timedelta(days=1):
                minutes = min(MIXED_PRACTICE_MAX_MINUTES, day.free)
                if minutes >= 5:
                    place(day, _task("mixed_practice", None, project, minutes,
                                     ident=f"mixed:{project.id}:{day_date.isoformat()}",
                                     reason="Mixed questions across topics, aimed at the weakest"))

    unscheduled = sorted(pending_learn, key=lambda topic_id: (-priority(topic_id), topic_id))
    by_project: dict[int, list[str]] = {}
    for topic_id in unscheduled:
        by_project.setdefault(topics_by_id[topic_id].project_id, []).append(topic_id)
    for project_id, topic_ids in by_project.items():
        if project_by_id[project_id].exam_date:
            warnings.append({
                "code": "insufficient_time", "project_id": project_id, "topic_ids": topic_ids,
                "message": f"{len(topic_ids)} topic(s) do not fit before the exam with the time available. "
                           "The most important topics are planned first.",
            })
    for project in projects:
        if not project.exam_date:
            continue
        squeezed = sorted({topic_id for _, topic_id, _ in due_reviews
                           if topics_by_id[topic_id].project_id == project.id and topic_id not in unscheduled})
        if squeezed:
            warnings.append({"code": "reviews_squeezed", "project_id": project.id, "topic_ids": squeezed,
                             "message": "Some reviews do not fit; the practice exam covers them."})
    return Plan(days=[days[day] for day in all_dates], warnings=warnings, unscheduled=unscheduled)

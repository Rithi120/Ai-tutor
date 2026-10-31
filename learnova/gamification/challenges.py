"""Daily and weekly learning challenges. Pure rules: what exists, who gets which, and
how one learning event moves a challenge forward.

Challenges reward learning, not clicking. Every metric counts a learning event the
server already validated (an answer it graded, a lesson the knowledge gate closed, a
test it scored), never a button press, and each event is counted once because
`record_learning_event` only runs for events it has not seen before.

Periods are server-defined: days and weeks in APP_TIMEZONE, starting at local midnight
and on Monday. A device clock or a profile timezone cannot move them.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any

DIFFICULTIES = ("easy", "medium", "hard")

# Events that are one answer to one question.
ANSWER_EVENTS = frozenset({"practice_question_completed", "flashcard_reviewed", "vocabulary_reviewed"})
# Events that finish a scored test.
TEST_EVENTS = frozenset({"test_completed", "vocabulary_session_completed"})
# Events whose `improved` flag says the score beat the previous attempt at the same thing.
# Only tests the server scored: a self-graded review session's accuracy is the learner's
# own opinion and must not pay out "you improved".
RESULT_EVENTS = frozenset({"test_completed", "vocabulary_session_completed"})


@dataclass(frozen=True)
class ChallengeDefinition:
    key: str
    period: str          # daily | weekly
    metric: str
    target: int
    difficulty: str      # easy | medium | hard
    reward_xp: int
    title: str           # English source; {target} and {subject} are filled in
    # Offered only to someone who used one of these recently, so nobody is asked to
    # review flashcards they do not have. Empty means anyone can do it.
    requires: frozenset[str] = field(default_factory=frozenset)
    needs_subject: bool = False
    # Counted once per distinct value (a day, a subject) instead of once per event.
    distinct: bool = False


def _daily(key, metric, target, difficulty, reward, title, requires=(), **extra):
    return ChallengeDefinition(key, "daily", metric, target, difficulty, reward, title,
                               frozenset(requires), **extra)


def _weekly(key, metric, target, difficulty, reward, title, requires=(), **extra):
    return ChallengeDefinition(key, "weekly", metric, target, difficulty, reward, title,
                               frozenset(requires), **extra)


FLASHCARDS = ("flashcard_reviewed",)
VOCABULARY = ("vocabulary_reviewed", "vocabulary_session_completed")
LESSONS = ("practice_question_completed", "quiz_completed")
TESTS = ("test_completed", "vocabulary_session_completed")

# Daily rewards stay well below what a normal study day earns, so challenges guide what
# to practise rather than becoming the point of practising. Targets fit into roughly
# 15-30 minutes.
DAILY_CHALLENGES: tuple[ChallengeDefinition, ...] = (
    _daily("daily_correct_10", "correct_answers", 10, "easy", 15, "Answer {target} questions correctly"),
    _daily("daily_flashcards_10", "flashcard_reviews", 10, "easy", 15, "Review {target} flashcards", FLASHCARDS),
    _daily("daily_vocabulary_15", "vocabulary_reviews", 15, "easy", 15, "Practise {target} vocabulary words", VOCABULARY),
    # Lessons are the core of Learnova, so lesson challenges are open to everyone.
    _daily("daily_lessons_2", "lessons", 2, "medium", 25, "Complete {target} lessons"),
    _daily("daily_test_1", "tests", 1, "medium", 25, "Finish one test", TESTS),
    _daily("daily_weak_subject_5", "subject_answers", 5, "medium", 25,
           "Practise {subject}: answer {target} questions", LESSONS, needs_subject=True),
    _daily("daily_lessons_3", "lessons", 3, "hard", 40, "Complete {target} lessons"),
    _daily("daily_hard_3", "hard_correct", 3, "hard", 40, "Answer {target} difficult questions correctly"),
    _daily("daily_improve_1", "improved_results", 1, "hard", 40, "Beat your previous score on a test", TESTS),
    _daily("daily_no_hints_1", "lessons_without_hints", 1, "hard", 40, "Complete a lesson without using hints"),
)

WEEKLY_CHALLENGES: tuple[ChallengeDefinition, ...] = (
    _weekly("weekly_active_days_4", "active_days", 4, "easy", 60, "Study on {target} different days",
            distinct=True),
    _weekly("weekly_flashcards_50", "flashcard_reviews", 50, "easy", 60, "Review {target} flashcards", FLASHCARDS),
    _weekly("weekly_vocabulary_60", "vocabulary_reviews", 60, "easy", 60, "Practise {target} vocabulary words",
            VOCABULARY),
    _weekly("weekly_lessons_8", "lessons", 8, "medium", 80, "Complete {target} lessons"),
    _weekly("weekly_tests_3", "tests", 3, "medium", 80, "Complete {target} tests", TESTS),
    _weekly("weekly_active_days_5", "active_days", 5, "medium", 80, "Keep your streak: study on {target} days",
            distinct=True),
    _weekly("weekly_lessons_15", "lessons", 15, "hard", 100, "Complete {target} lessons"),
    _weekly("weekly_hard_10", "hard_correct", 10, "hard", 100, "Answer {target} difficult questions correctly"),
    _weekly("weekly_improve_subjects_2", "improved_subjects", 2, "hard", 100,
            "Improve your score in {target} different subjects", TESTS, distinct=True),
)

# Missions created before challenges existed keep working until their period ends.
LEGACY_CHALLENGES: tuple[ChallengeDefinition, ...] = (
    _daily("daily_answers", "correct_answers", 5, "easy", 10, "Answer {target} questions correctly"),
    _weekly("weekly_sessions", "sessions", 3, "medium", 30, "Complete {target} study sessions"),
)
CHALLENGE_BY_KEY = {item.key: item for item in DAILY_CHALLENGES + WEEKLY_CHALLENGES + LEGACY_CHALLENGES}
assert len(CHALLENGE_BY_KEY) == len(DAILY_CHALLENGES) + len(WEEKLY_CHALLENGES) + len(LEGACY_CHALLENGES)
assert all(item.reward_xp <= 100 for item in CHALLENGE_BY_KEY.values())  # award_xp's per-award ceiling


def period_bounds(now: datetime, zone: tzinfo, period: str) -> tuple[datetime, datetime, date]:
    """(start, end) in UTC and the local start date of the day or week containing `now`.

    Built from the local calendar date, so a daylight-saving change makes that day 23 or
    25 hours long instead of shifting the boundary.
    """

    local = now.astimezone(zone)
    start_date = local.date()
    if period == "weekly":
        start_date -= timedelta(days=start_date.weekday())
        end_date = start_date + timedelta(days=7)
    else:
        end_date = start_date + timedelta(days=1)

    def at_midnight(day: date) -> datetime:
        return datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)

    return at_midnight(start_date), at_midnight(end_date), start_date


def choose(
    period: str, user_id: int, period_start: date, *, recent_events: set[str] | frozenset[str],
    weak_subject: str | None = None, count: int = 3,
) -> list[ChallengeDefinition]:
    """This period's challenges for one learner: one per difficulty, varied day to day.

    Seeded by learner and period, so the selection is stable across requests and
    restarts, differs between learners, and changes every day or week. A challenge whose
    feature the learner has not used recently is skipped when anything else fits.
    """

    pool = DAILY_CHALLENGES if period == "daily" else WEEKLY_CHALLENGES
    rng = random.Random(f"{user_id}:{period}:{period_start.isoformat()}")

    def eligible(item: ChallengeDefinition) -> bool:
        if item.needs_subject and not weak_subject:
            return False
        return not item.requires or bool(item.requires & set(recent_events))

    chosen: list[ChallengeDefinition] = []
    for difficulty in DIFFICULTIES[:count]:
        bucket = [item for item in pool if item.difficulty == difficulty]
        preferred = [item for item in bucket if eligible(item)]
        fallback = [item for item in bucket if not item.requires and not item.needs_subject]
        options = preferred or fallback or bucket
        chosen.append(options[rng.randrange(len(options))])
    return chosen


def contribution(
    definition: ChallengeDefinition, event_type: str, metadata: dict[str, Any], *,
    subject: str = "", local_date: date | None = None, params: dict[str, Any] | None = None,
) -> int | str | None:
    """What one event adds: a count, a distinct value (day or subject), or nothing."""

    metric = definition.metric
    # Correctness counts only where the server graded it; a flashcard the learner marked
    # "good" themselves is still a review, but not a correct answer.
    correct = bool(metadata.get("correct")) and not metadata.get("self_graded")
    if metric == "correct_answers":
        return 1 if event_type in ANSWER_EVENTS and correct else None
    if metric == "flashcard_reviews":
        return 1 if event_type == "flashcard_reviewed" else None
    if metric == "vocabulary_reviews":
        return 1 if event_type == "vocabulary_reviewed" else None
    if metric == "lessons":
        return 1 if event_type == "quiz_completed" else None
    if metric == "lessons_without_hints":
        # Only an explicit False counts: a lesson recorded before hints were tracked
        # is not proof that none were used.
        return 1 if event_type == "quiz_completed" and metadata.get("hints_used") is False else None
    if metric == "tests":
        return 1 if event_type in TEST_EVENTS else None
    if metric == "hard_correct":
        hard = (event_type == "practice_question_completed" and int(metadata.get("difficulty") or 0) >= 3) or (
            event_type == "flashcard_reviewed" and metadata.get("difficult") is True)
        return 1 if hard and correct else None
    if metric == "improved_results":
        return 1 if event_type in RESULT_EVENTS and metadata.get("improved") is True else None
    if metric == "improved_subjects":
        if event_type in RESULT_EVENTS and metadata.get("improved") is True and subject.strip():
            return subject.strip().casefold()
        return None
    if metric == "subject_answers":
        wanted = str((params or {}).get("subject") or "").strip().casefold()
        return 1 if event_type in ANSWER_EVENTS and wanted and subject.strip().casefold() == wanted else None
    if metric == "active_days":
        return local_date.isoformat() if local_date else None
    if metric == "sessions":
        return 1 if event_type.endswith("_session_completed") else None
    return None


def title_values(definition: ChallengeDefinition, params: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"target": definition.target, "subject": str((params or {}).get("subject") or "")}

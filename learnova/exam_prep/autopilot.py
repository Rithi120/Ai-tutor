"""What the student should do next - decided, not asked.

The rule set, in the order it is applied:

1. **Final days** (the last quarter of the preparation, at most three days, at least the
   last two): retrieval beats new material. Due reviews first, then a mock exam if none
   was taken in the last two days, then practice on the weakest topic. New topics are only
   opened when nothing else is left.
2. **Spaced review** of older weak material interrupts the main line when enough reviews
   are due (three or more) and the current topic has at least been started - so going
   deep on topic 2 never means forgetting topic 1.
3. **The current topic**: the first topic, in the material's own order, that is not yet
   known. Not yet taught → learn it (the AI explains it from the student's pages, then
   the knowledge-gated test runs). Taught but not known → practice it until it is.
   Topic 1 is finished before topic 2 is opened; "known" means the knowledge gate's
   definition, never a single right answer.
4. **Everything known**: a mock exam (if none in the last three days), otherwise a review
   of the topic with the least evidence behind it.

Pure: `app.py` builds the `TopicState` rows from the database and executes the action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

FINAL_PHASE_MAX_DAYS = 3
SPACED_REVIEW_THRESHOLD = 3
MOCK_GAP_DAYS_FINAL = 2
MOCK_GAP_DAYS_NORMAL = 3


@dataclass(frozen=True)
class TopicState:
    section_id: int
    title: str
    position: int
    knowledge: float            # 0-100 from the knowledge model
    confidence: float           # 0-1
    known: bool                 # the knowledge gate's verdict
    learned: bool               # the AI has taught it at least once (a lesson exists)
    importance: float = 2.0     # from the competencies (1-3)
    missing_competencies: int = 0
    minutes: int = 10
    excluded: bool = False


@dataclass(frozen=True)
class Action:
    kind: str                   # learn | practice | review | mock_exam | done
    reason: str                 # a short code the app translates
    section_id: int | None = None
    title: str = ""
    minutes: int = 10
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reason": self.reason, "section_id": self.section_id,
                "title": self.title, "minutes": self.minutes, "detail": dict(self.detail)}


def phase(days_left: int, total_days: int) -> str:
    """learning | final. The final phase is the last quarter, capped at three days and
    never shorter than the last two."""

    if days_left <= 0:
        return "final"
    final_days = max(2, min(FINAL_PHASE_MAX_DAYS, round(max(1, total_days) * 0.25)))
    return "final" if days_left <= final_days else "learning"


def _ordered(topics: Sequence[TopicState]) -> list[TopicState]:
    return sorted((topic for topic in topics if not topic.excluded), key=lambda item: (item.position, item.section_id))


def weakest(topics: Sequence[TopicState], *, learned_only: bool = True) -> TopicState | None:
    """The weakest topic that has actually been assessed. A topic nobody has answered a
    question on yet is not "weak" - it is simply next."""

    pool = [topic for topic in _ordered(topics) if (topic.learned and topic.confidence > 0) or not learned_only]
    if not pool:
        return None
    return min(pool, key=lambda item: (item.knowledge, item.confidence, item.position))


def next_action(
    topics: Sequence[TopicState],
    *,
    days_left: int,
    total_days: int,
    due_reviews: int = 0,
    days_since_mock: int | None = None,
    mock_count: int = 0,
) -> Action:
    """The single next step for this student today."""

    ordered = _ordered(topics)
    if not ordered:
        return Action("done", "no_topics")
    current_phase = phase(days_left, total_days)
    open_topics = [topic for topic in ordered if not topic.known]

    if current_phase == "final":
        if due_reviews:
            return Action("review", "final_days_review", minutes=10, detail={"due": due_reviews})
        if days_left >= 1 and (days_since_mock is None or days_since_mock >= MOCK_GAP_DAYS_FINAL):
            return Action("mock_exam", "final_days_mock", minutes=30)
        target = weakest(ordered) or weakest(ordered, learned_only=False)
        if target is not None and target.learned:
            return Action("practice", "final_days_weakest", target.section_id, target.title, 10,
                          {"knowledge": round(target.knowledge)})
        if open_topics:
            first = open_topics[0]
            return Action("learn", "final_days_unlearned", first.section_id, first.title, first.minutes)
        return Action("review", "final_days_review", minutes=10)

    current = open_topics[0] if open_topics else None
    if due_reviews >= SPACED_REVIEW_THRESHOLD and (current is None or current.learned):
        return Action("review", "spaced_review", minutes=10, detail={"due": due_reviews})

    if current is not None:
        if not current.learned:
            return Action("learn", "next_topic", current.section_id, current.title, current.minutes,
                          {"missing_competencies": current.missing_competencies})
        return Action("practice", "finish_topic", current.section_id, current.title, 10,
                      {"knowledge": round(current.knowledge)})

    if days_since_mock is None or days_since_mock >= MOCK_GAP_DAYS_NORMAL:
        return Action("mock_exam", "all_known_mock", minutes=30)
    least = min(ordered, key=lambda item: (item.confidence, item.knowledge))
    return Action("practice", "keep_warm", least.section_id, least.title, 8, {"knowledge": round(least.knowledge)})


def plan_summary(topics: Sequence[TopicState], action: Action, *, days_left: int, total_days: int) -> dict[str, Any]:
    """The numbers the dashboard card shows beside the action."""

    ordered = _ordered(topics)
    total_weight = sum(max(0.5, topic.importance) for topic in ordered) or 1.0
    progress = sum(max(0.5, topic.importance) * topic.knowledge for topic in ordered) / total_weight if ordered else 0.0
    weak = weakest(ordered)
    return {
        "phase": phase(days_left, total_days),
        "days_left": days_left,
        "progress_percent": round(progress),
        "topics_known": sum(1 for topic in ordered if topic.known),
        "topics_total": len(ordered),
        "weakness": weak.title if weak is not None and not weak.known else None,
        "weakness_knowledge": round(weak.knowledge) if weak is not None and not weak.known else None,
        "action": action.as_dict(),
        "sequence": _sequence_for(action),
    }


def _sequence_for(action: Action) -> list[str]:
    """What the next session will consist of, as step codes the UI labels."""

    if action.kind == "learn":
        return ["lesson", "practice", "mastery_check"]
    if action.kind == "practice":
        return ["practice", "diagnosis", "mastery_check"]
    if action.kind == "review":
        return ["spaced_review", "mastery_check"]
    if action.kind == "mock_exam":
        return ["mock_exam", "knowledge_check", "gap_practice"]
    return []

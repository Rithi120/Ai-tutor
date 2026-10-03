"""An honest grade estimate (German 1-6) from demonstrated knowledge.

The estimate is a range, not a number, and it is wide when the evidence is thin: a
student who has shown two of six topics gets "2-4", not "2". It blends what the knowledge
model has actually measured (per topic, weighted by how much that topic matters in the
exam) with mock exam results when there are any. It never claims more than the evidence
supports, and every change comes with the reasons that moved it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# The common German Notenschlüssel for a written test, by percentage of points.
GRADE_THRESHOLDS = ((92, 1), (81, 2), (67, 3), (50, 4), (30, 5))
MOCK_WEIGHT = 0.4            # a mock exam is the closest thing to the real one
UNASSESSED_ASSUMED = 35.0    # a topic never tested counts as this, so untested work drags the estimate
MAX_SPREAD = 25.0            # percentage points of range at zero confidence
MIN_SPREAD = 5.0             # even full evidence leaves a little room


@dataclass(frozen=True)
class TopicEvidence:
    name: str
    knowledge: float          # 0-100, the knowledge model's estimate
    confidence: float         # 0-1, how much evidence stands behind it
    weight: float = 2.0       # importance in the exam (1-3)
    assessed: bool = True     # False when the topic has never been tested


@dataclass(frozen=True)
class GradeEstimate:
    available: bool
    expected_percent: float
    low_grade: int            # the better grade of the range (1 is best)
    high_grade: int           # the worse grade of the range
    confidence: float
    knowledge_percent: float
    mock_percent: float | None
    topics_assessed: int
    topics_total: int
    spread: float
    basis: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        if not self.available:
            return "–"
        return str(self.low_grade) if self.low_grade == self.high_grade else f"{self.low_grade}–{self.high_grade}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available, "expected_percent": round(self.expected_percent, 1),
            "low_grade": self.low_grade, "high_grade": self.high_grade, "label": self.label,
            "confidence": round(self.confidence, 2), "knowledge_percent": round(self.knowledge_percent, 1),
            "mock_percent": None if self.mock_percent is None else round(self.mock_percent, 1),
            "topics_assessed": self.topics_assessed, "topics_total": self.topics_total,
            "spread": round(self.spread, 1), "basis": list(self.basis),
        }


def grade_for_percent(percent: float) -> int:
    value = max(0.0, min(100.0, float(percent)))
    for threshold, grade in GRADE_THRESHOLDS:
        if value >= threshold:
            return grade
    return 6


def estimate_grade(
    topics: Sequence[TopicEvidence],
    mock_scores: Sequence[float] = (),
    *,
    mock_weight: float = MOCK_WEIGHT,
) -> GradeEstimate:
    """The expected grade range from topic knowledge and mock exams."""

    topics = list(topics)
    mocks = [max(0.0, min(100.0, float(score))) for score in mock_scores][-2:]
    assessed = [topic for topic in topics if topic.assessed]
    if not assessed and not mocks:
        return GradeEstimate(False, 0.0, 1, 6, 0.0, 0.0, None, 0, len(topics), MAX_SPREAD,
                             basis=("no_evidence",))
    total_weight = sum(max(0.5, topic.weight) for topic in topics) or 1.0
    knowledge = sum(
        max(0.5, topic.weight) * (max(0.0, min(100.0, topic.knowledge)) if topic.assessed else UNASSESSED_ASSUMED)
        for topic in topics
    ) / total_weight if topics else (mocks[-1] if mocks else 0.0)
    assessed_weight = sum(max(0.5, topic.weight) for topic in assessed)
    coverage = assessed_weight / total_weight if topics else 1.0
    evidence_confidence = (sum(topic.confidence for topic in assessed) / len(assessed)) if assessed else 0.0
    confidence = coverage * evidence_confidence
    mock_percent = sum(mocks) / len(mocks) if mocks else None
    basis = ["knowledge_model"] if assessed else []
    if mock_percent is not None:
        expected = (1 - mock_weight) * knowledge + mock_weight * mock_percent if assessed else mock_percent
        confidence = min(1.0, confidence + 0.25 if assessed else 0.45)
        basis.append("mock_exam")
    else:
        expected = knowledge
    if coverage < 1.0:
        basis.append("untested_topics")
    spread = MIN_SPREAD + (MAX_SPREAD - MIN_SPREAD) * (1.0 - max(0.0, min(1.0, confidence)))
    return GradeEstimate(
        available=True, expected_percent=round(expected, 1),
        low_grade=grade_for_percent(expected + spread), high_grade=grade_for_percent(expected - spread),
        confidence=round(max(0.0, min(1.0, confidence)), 3), knowledge_percent=round(knowledge, 1),
        mock_percent=mock_percent, topics_assessed=len(assessed), topics_total=len(topics),
        spread=round(spread, 1), basis=tuple(basis),
    )


def explain_change(
    previous: Mapping[str, Any] | None,
    current: GradeEstimate,
    *,
    previous_topics: Mapping[str, float] | None = None,
    current_topics: Mapping[str, float] | None = None,
    minimum_topic_move: float = 8.0,
) -> list[dict[str, Any]]:
    """Why the estimate moved since the last snapshot, as structured reasons.

    Returned items are `{"kind": ..., ...}` for the app to phrase in the student's
    language: `topic_up`/`topic_down` with `topic`, `before`, `after`; `mock` with
    `score`; `more_evidence`/`less_evidence`; `first_estimate`.
    """

    reasons: list[dict[str, Any]] = []
    if not current.available:
        return reasons
    if not previous or not previous.get("available"):
        return [{"kind": "first_estimate"}]
    before_topics = dict(previous_topics or {})
    after_topics = dict(current_topics or {})
    for name, after in after_topics.items():
        before = before_topics.get(name)
        if before is None:
            if after > 0:
                reasons.append({"kind": "topic_new", "topic": name, "after": round(after)})
            continue
        if after - before >= minimum_topic_move:
            reasons.append({"kind": "topic_up", "topic": name, "before": round(before), "after": round(after)})
        elif before - after >= minimum_topic_move:
            reasons.append({"kind": "topic_down", "topic": name, "before": round(before), "after": round(after)})
    previous_mock = previous.get("mock_percent")
    if current.mock_percent is not None and (previous_mock is None or abs(float(previous_mock) - current.mock_percent) >= 1):
        reasons.append({"kind": "mock", "score": round(current.mock_percent)})
    previous_confidence = float(previous.get("confidence") or 0.0)
    if current.confidence - previous_confidence >= 0.15:
        reasons.append({"kind": "more_evidence"})
    elif previous_confidence - current.confidence >= 0.15:
        reasons.append({"kind": "less_evidence"})
    return reasons[:4]


def estimate_changed(previous: Mapping[str, Any] | None, current: GradeEstimate) -> bool:
    """Whether the new estimate is worth a new history entry."""

    if not current.available:
        return False
    if not previous or not previous.get("available"):
        return True
    return (
        (previous.get("low_grade"), previous.get("high_grade")) != (current.low_grade, current.high_grade)
        or abs(float(previous.get("expected_percent") or 0.0) - current.expected_percent) >= 3.0
    )

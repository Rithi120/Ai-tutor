"""When a test may stop: the knowledge gate.

A test used to be five questions whatever the student knew. Now it runs from a minimum to a
maximum number of questions and stops when the engine is confident that the student *knows*
every concept the test is about. "Knows" has a precise meaning here: an evidence-weighted
estimate of at least TARGET (80), backed by enough evidence that one lucky answer could not
have produced it, and confirmed on at least one question that was not the easiest level.

Why the long-term mastery score is not used directly: `learnova.quizzes.adaptive.update_mastery`
moves it by about twelve points per answer *on purpose*, so a bad week cannot erase months of
work - but it also means a brand-new concept would need seven correct answers to reach 80.
Within one sitting the question is different: "what does this student know right now?" So the
estimate blends the student's prior (the stored score, weighted by how much decayed evidence
stands behind it, capped so it never outweighs the session) with every answer given in this
session, each weighted by `knowledge.observation_weight` - the same evidence weight the
knowledge model records. A hinted, unverified or undiagnosable answer therefore counts for
less here too, and a wrong answer pulls the estimate down hard enough that the concept has to
be proven again.

Pure: no Flask, no database. app.py builds the priors and observations and acts on the
decision; the tests exercise every rule on plain values.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from learnova.diagnostics.knowledge import MASTERY_EVIDENCE_FLOOR

TARGET = 80.0
MIN_QUESTIONS = 3
MAX_QUESTIONS = 15
# The configurable maximum can never exceed this; a "test" of fifty questions is an exam.
HARD_LIMIT = 30
# A strong prior counts for at most this many answers' worth of evidence, so what the
# student shows today always matters more than what the record says about last month.
PRIOR_WEIGHT_CAP = 3.0
# Consecutive questions the gate will spend on one failing concept before it moves on and
# comes back later. Grinding one idea six times in a row teaches less than it frustrates.
MAX_RUN_ON_ONE_CONCEPT = 4
# A score at or above this is a correct answer - the same line update_mastery draws.
CORRECT_SCORE = 80.0
# Weight assumed for a hypothetical correct answer when predicting whether one more right
# answer would finish a concept (a typical validated observation is 1.1-1.3).
ASSUMED_CORRECT_WEIGHT = 1.2
# Planner actions that mean "stay on this concept": the student has not got it yet and the
# planner has chosen how to re-teach it.
REMEDIAL_ACTIONS = frozenset({
    "prerequisite_reteach", "misconception_contrast", "worked_example_then_practice",
    "targeted_practice", "reduce_difficulty", "clarify_instruction", "diagnostic_check",
})
# Question formats by position. Slots 2-5 keep the shapes the lesson UI has always used;
# later slots rotate, with the written answer the rarest because students are on phones.
EARLY_TYPES = {2: "checkboxes", 3: "dropdown", 4: "ordering", 5: "text"}
LATE_TYPE_CYCLE = ("checkboxes", "dropdown", "text", "ordering")


@dataclass(frozen=True)
class Observation:
    """One graded answer about one concept, with the evidence weight it earned."""

    concept: str
    score: float
    weight: float
    difficulty: int = 1


@dataclass(frozen=True)
class Prior:
    """What the record said about a concept before this session began."""

    score: float = 0.0
    weight: float = 0.0


@dataclass(frozen=True)
class ConceptEstimate:
    concept: str
    knowledge: float        # 0-100, the evidence-weighted estimate for this sitting
    evidence: float         # prior (capped) plus this session's observation weights
    confidence: float       # 0-1, how much evidence stands behind the estimate
    attempts: int           # answers about this concept in this session
    hardest_correct: int    # highest difficulty answered correctly this session, 0 if none
    known: bool
    status: str             # "known" | "learning" | "untested"
    mistakes: int = 0       # answers below the correct line this session

    def as_dict(self) -> dict[str, Any]:
        return {
            "concept": self.concept,
            "knowledge": round(self.knowledge),
            "confidence": round(self.confidence, 2),
            "attempts": self.attempts,
            "known": self.known,
            "status": self.status,
        }


def _same(left: str, right: str) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def estimate(
    concept: str,
    prior: Prior | None,
    observations: Iterable[Observation],
    *,
    target: float = TARGET,
) -> ConceptEstimate:
    """The knowledge estimate for one concept from its prior and this session's answers."""

    own = [item for item in observations if _same(item.concept, concept) and item.weight > 0]
    prior_weight = min(PRIOR_WEIGHT_CAP, max(0.0, float(prior.weight))) if prior else 0.0
    prior_score = max(0.0, min(100.0, float(prior.score))) if prior else 0.0
    session_weight = sum(item.weight for item in own)
    total = prior_weight + session_weight
    if total <= 0:
        knowledge = prior_score
    else:
        knowledge = (prior_score * prior_weight + sum(
            max(0.0, min(100.0, float(item.score))) * item.weight for item in own)) / total
    hardest_correct = max((int(item.difficulty or 1) for item in own if item.score >= CORRECT_SCORE), default=0)
    mistakes = sum(1 for item in own if item.score < CORRECT_SCORE)
    prior_known = prior is not None and prior_score >= target and float(prior.weight) >= MASTERY_EVIDENCE_FLOOR
    # A concept the record already calls known needs one confirming answer this session;
    # anything else must be shown on a question above the easiest level.
    confirmed = hardest_correct >= 2 or (prior_known and hardest_correct >= 1)
    known = knowledge >= target and total >= MASTERY_EVIDENCE_FLOOR and confirmed
    status = "known" if known else ("untested" if not own else "learning")
    return ConceptEstimate(
        concept=concept, knowledge=round(knowledge, 1), evidence=round(total, 3),
        confidence=round(total / (total + 1.0), 3), attempts=len(own),
        hardest_correct=hardest_correct, known=known, status=status, mistakes=mistakes,
    )


def estimates_for(
    focus: Sequence[str],
    priors: Mapping[str, Prior],
    observations: Iterable[Observation],
    *,
    target: float = TARGET,
) -> tuple[ConceptEstimate, ...]:
    """Estimates for the test's concepts, in the order the test introduced them.

    Only the focus concepts count towards the gate; an answer the model attributed to some
    other concept is not allowed to extend the test on its own.
    """

    observations = list(observations)
    seen: set[str] = set()
    out: list[ConceptEstimate] = []
    for name in focus:
        key = str(name or "").strip().casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        prior = next((value for label, value in priors.items() if _same(label, name)), None)
        out.append(estimate(str(name).strip(), prior, observations, target=target))
    return tuple(out)


@dataclass(frozen=True)
class GateDecision:
    stop: bool
    reason: str             # "target_reached" | "max_questions" | "continue" | "minimum_pending"
    answered: int
    minimum: int
    maximum: int
    target: float
    estimates: tuple[ConceptEstimate, ...]
    next_concept: str | None
    stay_on_current: bool
    last_correct: bool = False

    @property
    def known_count(self) -> int:
        return sum(1 for item in self.estimates if item.known)

    @property
    def below_target(self) -> tuple[ConceptEstimate, ...]:
        return tuple(item for item in self.estimates if not item.known)

    @property
    def reached(self) -> bool:
        return bool(self.estimates) and all(item.known for item in self.estimates)

    def concept_is_known(self, concept: str) -> bool:
        return any(item.known and _same(item.concept, concept) for item in self.estimates)

    @property
    def wants_transfer(self) -> bool:
        """Should the next question apply the concept in a new situation?

        After a mistake on a concept, a correct repeat of the same kind of task is not
        enough: the confirming question changes the context (planner action
        `transfer_question`), so "understood" means it carries over, not that the pattern
        was memorised. Only once the student has just answered correctly again - while the
        last answer was wrong the planner is still re-teaching.
        """

        target = next((item for item in self.estimates if _same(item.concept, self.next_concept or "")), None)
        return bool(target and not target.known and target.mistakes >= 1 and target.attempts >= 2
                    and self.last_correct)

    @property
    def wants_harder(self) -> bool:
        """Should the next question be above the easiest level?

        True when the next concept has only been answered right at level 1 so far: the
        student has shown it once, and what the gate still needs is the confirmation on a
        harder question. The planner's default is to hold the level; this overrides it.
        """

        target = next((item for item in self.estimates if _same(item.concept, self.next_concept or "")), None)
        return bool(target and not target.known and target.attempts >= 1
                    and target.knowledge >= self.target and target.hardest_correct < 2)

    def as_dict(self) -> dict[str, Any]:
        """The progress view the browser renders: numbers and labels, no model internals."""

        return {
            "answered": self.answered,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "target": round(self.target),
            "known": self.known_count,
            "total": len(self.estimates),
            "reached": self.reached,
            "complete": self.stop,
            "reason": self.reason,
            "next_concept": self.next_concept,
            "concepts": [item.as_dict() for item in self.estimates],
        }


def bounds(minimum: Any, maximum: Any) -> tuple[int, int]:
    """Clamp configured bounds into something a test can actually follow."""

    try:
        high = int(maximum)
    except (TypeError, ValueError):
        high = MAX_QUESTIONS
    try:
        low = int(minimum)
    except (TypeError, ValueError):
        low = MIN_QUESTIONS
    high = max(1, min(HARD_LIMIT, high))
    low = max(1, min(low, high))
    return low, high


def choose_next(
    estimates: Sequence[ConceptEstimate],
    current_concept: str,
    *,
    last_score: float,
    run_on_current: int,
    planner_action: str = "",
) -> tuple[str | None, bool]:
    """Which concept the next question should be about, and whether that is a repeat.

    The order of preference: stay on a concept the student just got wrong (while the planner
    is re-teaching it and the run has not become a grind); then a concept not yet tested, in
    the test's own order; then the weakest concept still below target, preferring a change of
    concept over asking the same one again; and when everything is known but the minimum is
    not met, the concept with the least evidence, to confirm it.
    """

    current = next((item for item in estimates if _same(item.concept, current_concept)), None)
    if (current is not None and not current.known and run_on_current < MAX_RUN_ON_ONE_CONCEPT
            and (last_score < CORRECT_SCORE or planner_action in REMEDIAL_ACTIONS)):
        return current.concept, True
    untested = [item for item in estimates if item.status == "untested"]
    if untested:
        return untested[0].concept, False
    unknown = sorted((item for item in estimates if not item.known),
                     key=lambda item: (item.knowledge, item.attempts, item.concept.casefold()))
    if unknown:
        for item in unknown:
            if not _same(item.concept, current_concept):
                return item.concept, False
        return unknown[0].concept, True
    if estimates:
        least = min(estimates, key=lambda item: (item.evidence, item.concept.casefold()))
        return least.concept, _same(least.concept, current_concept)
    return None, False


def decide(
    *,
    answered: int,
    estimates: Sequence[ConceptEstimate],
    current_concept: str,
    last_score: float,
    run_on_current: int,
    planner_action: str = "",
    minimum: int = MIN_QUESTIONS,
    maximum: int = MAX_QUESTIONS,
    target: float = TARGET,
) -> GateDecision:
    """Stop or continue after `answered` questions, and where to go next if continuing."""

    minimum, maximum = bounds(minimum, maximum)
    estimates = tuple(estimates)

    def decision(stop: bool, reason: str, next_concept: str | None, stay: bool) -> GateDecision:
        return GateDecision(stop=stop, reason=reason, answered=answered, minimum=minimum,
                            maximum=maximum, target=target, estimates=estimates,
                            next_concept=next_concept, stay_on_current=stay,
                            last_correct=last_score >= CORRECT_SCORE)

    if answered >= maximum:
        return decision(True, "max_questions", None, False)
    all_known = bool(estimates) and all(item.known for item in estimates)
    if all_known and answered >= minimum:
        return decision(True, "target_reached", None, False)
    next_concept, stay = choose_next(
        estimates, current_concept, last_score=last_score,
        run_on_current=run_on_current, planner_action=planner_action)
    return decision(False, "minimum_pending" if all_known else "continue", next_concept, stay)


def targets_if(
    focus: Sequence[str],
    priors: Mapping[str, Prior],
    observations: Iterable[Observation],
    current_concept: str,
    *,
    difficulty: int,
    run_on_current: int,
    target: float = TARGET,
) -> tuple[str | None, str | None]:
    """Before the answer is graded: the next concept if it turns out right, and if wrong.

    The lesson path grades the answer and writes the next question in one model call, so
    the concept to aim at has to be named before the verdict exists. Both branches are
    computed from the current evidence: "if wrong" stays on the concept (unless the run has
    become a grind), "if correct" adds one hypothetical correct answer and asks whether the
    concept would then be known - if so the next question moves on.
    """

    observations = list(observations)
    before = estimates_for(focus, priors, observations, target=target)
    if_wrong, _ = choose_next(before, current_concept, last_score=0.0,
                              run_on_current=run_on_current, planner_action="targeted_practice")
    assumed = observations + [Observation(concept=current_concept, score=100.0,
                                          weight=ASSUMED_CORRECT_WEIGHT, difficulty=max(1, int(difficulty or 1)))]
    after = estimates_for(focus, priors, assumed, target=target)
    if_correct, _ = choose_next(after, current_concept, last_score=100.0,
                                run_on_current=run_on_current)
    return if_correct, if_wrong


def question_type_for(number: int, *, pictures: int = 0) -> str:
    """The answer format for question `number` (1-based). Pictures only ever fill slots 4-5."""

    if number == 4 and pictures >= 2:
        return "photo_ordering"
    if number == 5 and pictures >= 1:
        return "photo_response"
    if number in EARLY_TYPES:
        return EARLY_TYPES[number]
    if number < 2:
        return "multiple_choice"
    return LATE_TYPE_CYCLE[(number - 6) % len(LATE_TYPE_CYCLE)]


def run_length(history: Sequence[Mapping[str, Any]], concept: str) -> int:
    """How many of the most recent answers in a row were about `concept`."""

    count = 0
    for item in reversed(history):
        if _same(str(item.get("concept") or ""), concept):
            count += 1
        else:
            break
    return count

"""Building blocks of a subject teaching strategy.

A strategy is plain data: the order in which a lesson of one subject teaches (an Ethics
lesson retells the thought experiment before it argues, a maths lesson shows a worked
example before the student tries one), what the topic extraction should look for, which
graded answer formats suit the subject, and how a wrong answer is handled. The lesson
prompt is built from this data by `learnova.lessons.lesson_plan`; nothing here talks to
the database or a model.

`ITEM_KINDS`, `PART_KINDS`, `SERVER_PART_KINDS` and `KNOWLEDGE_STATES` are shared with
`learnova.projects.topics` (a test asserts the literals are identical), so the topic map
and the lesson parts speak the same vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

ITEM_KINDS = ("term", "example", "experiment", "theory", "person", "argument", "formula", "process",
              "diagram", "event", "date", "rule", "thought_experiment", "work", "method", "source",
              "place", "institution", "position", "cause_effect", "structure")
PART_KINDS = ("objective", "why", "explanation", "terms", "rule", "structure", "example",
              "worked_example", "experiment", "thought_experiment", "theory", "argument",
              "perspectives", "timeline", "cause_effect", "process", "diagram", "source_analysis",
              "model_analysis", "mistakes", "guided_exercise", "summary")
SERVER_PART_KINDS = ("flashcards",)   # inserted by the server, never requested from the model
KNOWLEDGE_STATES = ("new", "developing", "secure")

PLAN_MODES = ("learn", "review", "reteach")
QUESTION_TYPES = ("multiple_choice", "checkboxes", "dropdown", "ordering", "text")
# What the graded questions after the lesson ask for, in order: understanding first, then
# using it, interpreting it, discussing it, and an exam-style task last. A strategy picks
# the stages its subject needs (an Ethics test moves from comprehension to interpretation
# and discussion to a justified exam answer); `lesson_plan.question_stage` maps them onto
# the question numbers of a session.
QUESTION_STAGES = ("comprehension", "application", "interpretation", "discussion", "exam")
DEFAULT_QUESTION_STAGES = ("comprehension", "application", "exam")

# Parts that actually teach the content. "Teach first" means at least one of these comes
# before any guided exercise; objective, terms, example, mistakes and summary frame or
# apply the content but do not explain it on their own.
EXPLANATION_KINDS = frozenset({
    "explanation", "rule", "structure", "worked_example", "experiment", "thought_experiment",
    "theory", "argument", "perspectives", "timeline", "cause_effect", "process", "diagram",
    "source_analysis", "model_analysis",
})

ALL_MODES = frozenset(PLAN_MODES)
LEARN_RETEACH = frozenset({"learn", "reteach"})
RETEACH_ONLY = frozenset({"reteach"})
ALL_STATES = frozenset(KNOWLEDGE_STATES)
# Long parts a student who already knows the topic is not lectured with again.
NOT_SECURE = frozenset({"new", "developing"})

MAX_RULES = 6
# combine(): parts only a later strategy has are slotted in before the practice phase.
_PRACTICE_ANCHORS = ("mistakes", "guided_exercise", "summary")


@dataclass(frozen=True)
class LessonStep:
    kind: str                                   # one of PART_KINDS
    instruction: str                            # 1-2 English sentences for the model: what this part must do
    states: frozenset[str] = ALL_STATES
    modes: frozenset[str] = LEARN_RETEACH       # plan modes: learn, review, reteach
    when_items: tuple[str, ...] = ()            # only if the topic map has an item of one of these kinds

    def __post_init__(self) -> None:
        # Strategies are static data; a typo must fail at import, not silently drop a part.
        if self.kind not in PART_KINDS:
            raise ValueError(f"unknown part kind {self.kind!r}")
        if not self.instruction.strip():
            raise ValueError(f"step {self.kind!r} has no instruction")
        if not self.states or not self.states <= ALL_STATES:
            raise ValueError(f"step {self.kind!r} has invalid states {sorted(self.states)}")
        if not self.modes or not self.modes <= ALL_MODES:
            raise ValueError(f"step {self.kind!r} has invalid modes {sorted(self.modes)}")
        unknown = set(self.when_items) - set(ITEM_KINDS)
        if unknown:
            raise ValueError(f"step {self.kind!r} waits for unknown item kinds {sorted(unknown)}")


@dataclass(frozen=True)
class SubjectStrategy:
    key: str                     # "ethics"
    name: str                    # "Ethics and Philosophy" (English, used in prompts)
    priorities: tuple[str, ...]  # learning priorities, short English phrases
    steps: tuple[LessonStep, ...]            # the teaching sequence, in order
    extraction_focus: tuple[str, ...]        # ITEM_KINDS the topic extraction must look for
    question_types: tuple[str, ...]          # graded answer formats in rotation
    card_kinds: tuple[str, ...]              # ITEM_KINDS that become flashcards ("term" always first)
    rules: tuple[str, ...]                   # short teaching rules (<= 6)
    feedback_rule: str                       # what to do with a wrong answer in this subject
    exam_style: str                          # what exam-style tasks look like here
    question_stages: tuple[str, ...] = DEFAULT_QUESTION_STAGES   # QUESTION_STAGES, in order, "exam" last

    def __post_init__(self) -> None:
        if not self.steps:
            raise ValueError(f"strategy {self.key!r} has no steps")
        for field_name in ("extraction_focus", "card_kinds"):
            unknown = set(getattr(self, field_name)) - set(ITEM_KINDS)
            if unknown:
                raise ValueError(f"strategy {self.key!r}: unknown {field_name} {sorted(unknown)}")
        if not self.card_kinds or self.card_kinds[0] != "term":
            raise ValueError(f"strategy {self.key!r}: card_kinds must start with 'term'")
        if not self.question_types or set(self.question_types) - set(QUESTION_TYPES):
            raise ValueError(f"strategy {self.key!r}: invalid question_types {self.question_types}")
        if len(self.rules) > MAX_RULES:
            raise ValueError(f"strategy {self.key!r}: more than {MAX_RULES} rules")
        if (not self.question_stages or set(self.question_stages) - set(QUESTION_STAGES)
                or self.question_stages[-1] != "exam"):
            raise ValueError(f"strategy {self.key!r}: question_stages must be QUESTION_STAGES ending in 'exam'")

    def step_kinds(self) -> tuple[str, ...]:
        """Every part kind this strategy can ask for, in first-use order (all modes)."""

        return tuple(dict.fromkeys(step.kind for step in self.steps))


def _union(*groups: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for group in groups for value in group))


def _interleave(*groups: tuple[str, ...], limit: int) -> tuple[str, ...]:
    # Round-robin, so a combined subject keeps rules from every part within the cap
    # instead of only the first strategy's.
    out: list[str] = []
    for index in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if index < len(group) and group[index] not in out:
                out.append(group[index])
    return tuple(out[:limit])


def _joined(*texts: str) -> str:
    return " ".join(dict.fromkeys(text.strip() for text in texts if text.strip()))


def combine(key: str, name: str, *strategies: SubjectStrategy) -> SubjectStrategy:
    """One strategy for a subject that is several at once (NwT = sciences + technology).

    The first strategy sets the order. A step of a later strategy is added only for the
    plan modes in which the combined sequence has no part of that kind yet, and it goes
    right before the practice phase (the first mistakes / guided exercise / summary), so
    the extra content is still taught before the student practises."""

    if not strategies:
        raise ValueError("combine() needs at least one strategy")
    first, *rest = strategies
    steps = list(first.steps)
    for other in rest:
        for step in other.steps:
            missing = frozenset(mode for mode in step.modes
                                if not any(s.kind == step.kind and mode in s.modes for s in steps))
            if not missing:
                continue
            anchor = next((i for i, s in enumerate(steps) if s.kind in _PRACTICE_ANCHORS), len(steps))
            steps.insert(anchor, replace(step, modes=missing))
    return SubjectStrategy(
        key=key,
        name=name,
        priorities=_union(*(s.priorities for s in strategies)),
        steps=tuple(steps),
        extraction_focus=_union(*(s.extraction_focus for s in strategies)),
        question_types=first.question_types,
        card_kinds=_union(("term",), *(s.card_kinds for s in strategies)),
        rules=_interleave(*(s.rules for s in strategies), limit=MAX_RULES),
        feedback_rule=_joined(*(s.feedback_rule for s in strategies)),
        exam_style=_joined(*(s.exam_style for s in strategies)),
        question_stages=first.question_stages,
    )

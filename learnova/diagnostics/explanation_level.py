"""How complex the next explanation of one topic should be: grade + topic evidence + the turn.

Two signals, used together, never one for the other:

* The grade sets the register (words, examples from the learner's world) and the
  *ceiling* - the most complex treatment that is still relevant to that school year.
* The evidence about this one topic (ConceptMastery, recent diagnoses, confirmed
  prerequisite gaps, hint use) sets the *starting point* inside that range.

A Klasse 11 student weak in fractions therefore gets a foundational fractions
explanation in grown-up language, and a Klasse 5 student who has mastered fractions is
not walked through elementary steps again. The level describes the explanation, never
the student; it is chosen per topic, so one learner can be at 4 in geometry and 2 in
fractions at the same time.

The turn can move the level: an explicit "simpler"/"harder" request, a "I don't
understand", and what the last graded answers showed (`after_answer`). Those moves are
kept in the lesson session as a small offset, so they persist for the next turns
without overwriting the long-term mastery record, which only graded answers change.

Pure functions only; app.py reads the rows and stores the session state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .knowledge import MASTERY_EVIDENCE_FLOOR
from .taxonomy import EXECUTION_TAGS, UNDERSTANDING_TAGS

MASTERY_LABELS = ("not_assessed", "beginner", "developing", "proficient", "advanced")
CONTROLS = ("simpler", "step_by_step", "another_example", "harder")

LEVELS = {
    1: ("Concrete foundations",
        "familiar objects and everyday situations, very simple words, one small step at a time, "
        "no technical terms that are not strictly needed"),
    2: ("Guided basics",
        "simple numerical examples, every intermediate step written out, each important term "
        "explained in one short sentence, guided practice"),
    3: ("Standard school level",
        "the grade's normal terminology, a worked example, clear reasoning for each step, "
        "an independent exercise"),
    4: ("Advanced understanding",
        "more abstract reasoning, more than one solution method, connections to related "
        "concepts, more challenging exercises"),
    5: ("Extension",
        "challenging applications, generalisation and deeper reasoning, advanced methods "
        "that still belong to the learner's curriculum"),
}

# A single correct answer is never mastery: "proficient" needs two graded answers, and
# "advanced" needs the same evidence weight the knowledge model demands for "mastered".
_PROFICIENT_MIN_ATTEMPTS = 2


def mastery_label(evidence: dict[str, Any] | None) -> str:
    """Five-step topic label from the knowledge model's evidence summary."""

    if not evidence or not evidence.get("assessed"):
        return "not_assessed"
    score = float(evidence.get("mastery_score") or 0.0)
    attempts = int(evidence.get("attempts") or 0)
    weight = float(evidence.get("evidence_weight") or 0.0)
    if attempts < _PROFICIENT_MIN_ATTEMPTS:
        # One answer proves neither weakness nor mastery; the session state (after_answer)
        # already reacts to it for the next explanation.
        return "developing"
    if score >= 85 and weight >= MASTERY_EVIDENCE_FLOOR and attempts >= _PROFICIENT_MIN_ATTEMPTS:
        return "advanced"
    if score >= 70 and attempts >= _PROFICIENT_MIN_ATTEMPTS:
        return "proficient"
    if score >= 40:
        return "developing"
    return "beginner"


# What the evidence says about *how* the learner stands, beyond how much they know.
PATTERNS = {
    "no_evidence": "Never assessed on this topic: start accessible and, when useful, ask one short "
                   "diagnostic question before going deeper.",
    "prerequisite_gap": "Repeated answers point to a missing prerequisite{prerequisites}: rebuild that "
                        "first, briefly and without talking down, then return to the topic.",
    "conceptual_gap": "Recent answers show a misunderstanding of the idea itself: explain it with a "
                      "different representation than before, not the same words again.",
    "execution_errors": "The idea is understood but calculations slip: keep the explanation level, "
                        "show a quick self-check for each step instead of reteaching the topic.",
    "needs_independence": "Follows worked examples but relies on hints: fade the support step by step "
                          "and let the learner do the next step alone.",
    "mastered": "Has repeatedly solved this independently: skip elementary steps and diagnostic "
                "questions; offer a more demanding application.",
    "progressing": "Some evidence, no clear problem pattern: teach at the chosen level and check "
                   "understanding with one question.",
}


def learner_pattern(evidence: dict[str, Any] | None, *, diagnoses: list[str] | None = None,
                    prerequisite_gaps: list[str] | None = None, hint_rate: float | None = None) -> str:
    label = mastery_label(evidence)
    if label == "not_assessed":
        return "no_evidence"
    recent = [tag for tag in (diagnoses or [])[:3] if tag]
    if prerequisite_gaps or recent.count("prerequisite_gap") >= 2:
        return "prerequisite_gap"
    if sum(tag in UNDERSTANDING_TAGS for tag in recent) >= 2:
        return "conceptual_gap"
    if sum(tag in EXECUTION_TAGS for tag in recent) >= 2:
        return "execution_errors"
    # Hint reliance is checked first: success that leaned on hints is not *independent*
    # success, and only independent success earns the extension level.
    if hint_rate is not None and hint_rate >= 0.5:
        return "needs_independence"
    if label == "advanced" and int((evidence or {}).get("consecutive_correct") or 0) >= 2:
        return "mastered"
    return "progressing"


@dataclass(frozen=True)
class GradeBand:
    default: int   # where a topic with no evidence starts
    ceiling: int   # the most complex treatment still relevant to the school year
    register: str  # how to talk


def grade_band(grade: str | None) -> GradeBand:
    token = str(grade or "").strip().casefold()
    if token.isdigit():
        number = int(token)
        if number <= 2:
            return GradeBand(1, 2, "a young child: very short sentences, toys, fruit, fingers and counting")
        if number <= 4:
            return GradeBand(2, 3, "a primary-school child: short sentences and everyday examples")
        if number <= 6:
            return GradeBand(3, 4, "a lower-secondary student: everyday examples, terms introduced gently")
        if number <= 10:
            return GradeBand(3, 5, "a secondary student: the subject's proper terminology")
        return GradeBand(3, 5, "an upper-secondary student: adult language and precise terminology")
    if token == "university":
        return GradeBand(3, 5, "a university student: rigorous, technical language")
    if token == "vocational":
        return GradeBand(3, 4, "a vocational learner: practical, applied examples")
    # Unset or "other": start accessible, leave the whole range open.
    return GradeBand(2, 5, "a learner of unknown level: plain language, calibrate from their messages")


_REQUEST_PATTERNS = (
    ("confused", re.compile(
        r"\b(i\s+(?:still\s+)?(?:don'?t|do\s+not)\s+(?:get|understand)|confus|makes?\s+no\s+sense|"
        r"(?:versteh|kapier)\w*\s+(?:\w+\s+){0,3}(?:nicht|nichts|kein)|check\s+ich\s+nicht|zu\s+schwer|"
        r"too\s+hard|no\s+idea|keine\s+ahnung)", re.IGNORECASE)),
    ("direct_answer", re.compile(
        r"\b(just\s+(?:tell|give)\s+me|tell\s+me\s+the\s+(?:answer|solution)|what'?s\s+the\s+answer|"
        r"(?:full|complete|whole)\s+solution|sag\s+(?:mir\s+)?(?:einfach\s+)?die\s+(?:l[öo]sung|antwort)|"
        r"(?:gib|zeig)\s+mir\s+(?:einfach\s+)?die\s+(?:l[öo]sung|antwort)|komplette\s+l[öo]sung)", re.IGNORECASE)),
    ("simpler", re.compile(r"\b(simpler|easier|einfacher|leichter|simple\s+words|einfache\s+worte)", re.IGNORECASE)),
    ("harder", re.compile(r"\b(harder|more\s+(?:advanced|challenging|difficult)|challenge\s+me|schwieriger|"
                          r"anspruchsvoller|schwerer|herausforderung)", re.IGNORECASE)),
    ("step_by_step", re.compile(r"\b(step\s+by\s+step|schritt\s+f[üu]r\s+schritt)", re.IGNORECASE)),
    ("another_example", re.compile(r"\b(another\s+example|one\s+more\s+example|noch\s+ein(?:\s+anderes)?\s+beispiel|"
                                   r"weiteres\s+beispiel|anderes\s+beispiel)", re.IGNORECASE)),
)


def detect_request(message: str) -> str | None:
    """A control the student asked for in their own words (or None)."""

    for name, pattern in _REQUEST_PATTERNS:
        if pattern.search(message or ""):
            return name
    return None


REQUEST_DIRECTIVES = {
    "confused": "The student said they do not understand. Do NOT repeat or rephrase the previous "
                "explanation. Name the most likely point of confusion, show fewer steps at once, and "
                "check understanding before going on.",
    "direct_answer": "The student asked for the answer directly. If it is the open test question "
                     "(current_question), keep assessment integrity: say in one friendly line why you "
                     "won't give it and give the single most useful hint. Otherwise give the answer "
                     "first, then the shortest explanation of why it is right.",
    "simpler": "The student asked for a simpler explanation: simplify now - smaller example, fewer "
               "steps at once, everyday words - without repeating the previous wording.",
    "step_by_step": "The student asked for step by step: number each step, one operation per step, "
                    "and say WHY each step is done.",
    "another_example": "The student asked for another example: give a NEW example of the same idea "
                       "at the same level (different numbers or situation), solved step by step.",
    "harder": "The student asked for more challenge: go one step deeper or give a harder application, "
              "still within their curriculum and the prerequisites they have shown.",
}

FOCUS_DIRECTIVES = {
    "calculation_check": "Their last answer had a calculation slip, not a misunderstanding: fix that step "
                         "and show how to check it; do not reteach the whole topic.",
    "reteach_concept": "Their last answer showed a misunderstanding of the idea (see the diagnosis in "
                       "recent_results): name that specific misconception, contrast it with the correct "
                       "idea on one tiny example, then ask one short question that checks it is fixed.",
    "prerequisite": "Their last answer pointed to a missing prerequisite: rebuild that briefly first.",
    "simplify_and_diagnose": "Several answers in a row were wrong: simplify, find which prerequisite is "
                             "missing, and give one small targeted practice task.",
    "check_concept": "Their last answer was right but the reasoning looked unsure: check the underlying "
                     "idea with one short why-question.",
    "fade_support": "They are answering correctly on their own: reduce hints and scaffolding; raise the "
                    "challenge gradually.",
}

_OFFSET_LIMIT = 2

# What to switch to on the 1st, 2nd, 3rd... "I don't understand" about one topic. The
# model cannot be trusted to vary on its own - a paraphrase is the easy way out - so the
# code names a different representation every time.
REPRESENTATIONS = (
    "Try this representation: something concrete the student can picture - objects, a drawing, a "
    "number line or a small table - before any formula.",
    "Try this representation: a much smaller example with easy numbers, one step per line, and ask "
    "which step is the unclear one.",
    "Try this representation: go back to the prerequisite this idea rests on, check it with one tiny "
    "question, then rebuild the idea on top of it.",
    "Try this representation: ask the student to say in a few words what they think happens, then "
    "correct exactly that part.",
)


def topic_key(concept: str | None) -> str:
    return " ".join(str(concept or "").casefold().split())[:120]


def topic_state(teaching: dict[str, Any] | None, concept: str | None) -> dict[str, Any]:
    """The session adjustments for one topic. A request or a graded answer about one topic
    must not move the level of another topic in the same session."""

    topics = (teaching or {}).get("topics")
    if not isinstance(topics, dict):
        return {}  # also drops the old flat (whole-session) format
    state = topics.get(topic_key(concept))
    return dict(state) if isinstance(state, dict) else {}


def with_topic_state(teaching: dict[str, Any] | None, concept: str | None,
                     state: dict[str, Any]) -> dict[str, Any]:
    topics = (teaching or {}).get("topics")
    topics = dict(topics) if isinstance(topics, dict) else {}
    topics[topic_key(concept)] = state
    if len(topics) > 40:  # a long practice session must not grow the saved state forever
        topics = dict(list(topics.items())[-40:])
    return {"topics": topics}


@dataclass
class TeachingPlan:
    level: int
    mastery: str
    pattern: str
    band: GradeBand
    request: str | None = None
    focus: str | None = None
    prerequisites: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    representation: str = ""

    @property
    def level_name(self) -> str:
        return LEVELS[self.level][0]

    def summary(self) -> dict[str, Any]:
        return {"level": self.level, "level_name": self.level_name, "topic_mastery": self.mastery,
                "pattern": self.pattern, "request": self.request, "focus": self.focus}


def choose_level(*, grade: str | None, evidence: dict[str, Any] | None, pattern: str,
                 request: str | None = None, state: dict[str, Any] | None = None,
                 prerequisites: list[str] | None = None) -> TeachingPlan:
    band = grade_band(grade)
    label = mastery_label(evidence)
    reasons = [f"grade range 1-{band.ceiling}, start {band.default}"]
    base = {
        "not_assessed": band.default,
        "beginner": min(band.default, 2),
        "developing": band.default,
        "proficient": max(band.default, 3),
        "advanced": max(band.default, 4),
    }[label]
    reasons.append(f"topic mastery {label} -> {base}")
    if pattern == "mastered":
        base += 1
        reasons.append("repeated independent success +1")
    elif pattern in {"prerequisite_gap", "conceptual_gap"}:
        base -= 1
        reasons.append(f"{pattern} -1")
    offset = int((state or {}).get("offset") or 0)
    if offset:
        reasons.append(f"session adjustment {offset:+d}")
    level = base + offset
    if request in {"simpler", "confused"}:
        level -= 1
        reasons.append(f"{request} -1")
    elif request == "harder":
        level += 1
        # More depth, but not past what the prerequisites carry.
        if pattern == "prerequisite_gap":
            level = min(level, base)
        reasons.append("harder +1")
    level = max(1, min(band.ceiling, level))
    representation = ""
    if request in {"simpler", "confused"}:
        # The n-th time on this topic gets the n-th representation, so a second "I still
        # don't understand" can never be answered with the first explanation reworded.
        attempt = int((state or {}).get("confusions") or 0)
        representation = REPRESENTATIONS[attempt % len(REPRESENTATIONS)]
    return TeachingPlan(level=level, mastery=label, pattern=pattern, band=band, request=request,
                        focus=(state or {}).get("focus"), prerequisites=list(prerequisites or [])[:3],
                        reasons=reasons, representation=representation)


def record_request(state: dict[str, Any] | None, request: str | None) -> dict[str, Any]:
    """Make an explicit simpler/harder request (or confusion) stick for the next turns."""

    state = dict(state or {})
    offset = int(state.get("offset") or 0)
    if request in {"simpler", "confused"}:
        offset -= 1
    elif request == "harder":
        offset += 1
    state["offset"] = max(-_OFFSET_LIMIT, min(_OFFSET_LIMIT, offset))
    if request in {"simpler", "confused"}:
        state["confusions"] = int(state.get("confusions") or 0) + 1
    if request:
        state["last_request"] = request
    return state


def after_answer(state: dict[str, Any] | None, *, score: int, tag: str = "", hints_used: bool = False,
                 response_confidence: float | None = None) -> dict[str, Any]:
    """Move the session's teaching state after one graded answer.

    The long-term mastery record is updated elsewhere from the same answer; this only
    decides how the *next explanation* should differ from the last one."""

    state = dict(state or {})
    offset = int(state.get("offset") or 0)
    correct_streak = int(state.get("correct_streak") or 0)
    wrong_streak = int(state.get("wrong_streak") or 0)
    tag = str(tag or "")
    focus = None
    if score >= 80:
        wrong_streak = 0
        if hints_used:
            correct_streak = 0
            focus = "fade_support"
        else:
            correct_streak += 1
            if tag == "guessing_or_uncertain" or (response_confidence is not None and response_confidence < 40):
                focus = "check_concept"
            elif correct_streak >= 2:
                # Earned back after a "simpler" request, then a little further.
                offset = min(offset + 1, 1 if correct_streak >= 3 else 0)
                focus = "fade_support"
    else:
        correct_streak = 0
        wrong_streak += 1
        if tag == "prerequisite_gap":
            offset -= 1
            focus = "prerequisite"
        elif tag in UNDERSTANDING_TAGS:
            offset -= 1
            focus = "reteach_concept"
        elif tag in EXECUTION_TAGS:
            focus = "calculation_check"
        if wrong_streak >= 2 and focus not in {"prerequisite", "reteach_concept", "calculation_check"}:
            offset -= 1
            focus = "simplify_and_diagnose"
    state.update(offset=max(-_OFFSET_LIMIT, min(_OFFSET_LIMIT, offset)), correct_streak=correct_streak,
                 wrong_streak=wrong_streak, focus=focus)
    if score >= 80 and not hints_used:
        state["confusions"] = 0  # understood: the next confusion starts the rotation again
    return state


def instruction(plan: TeachingPlan, *, concept: str = "") -> str:
    """The block added to the tutor instructions. Short on purpose: one level, one pattern."""

    name, description = LEVELS[plan.level]
    topic = f" for '{concept}'" if concept else ""
    prerequisites = f" ({', '.join(plan.prerequisites)})" if plan.prerequisites else ""
    lines = [
        f"EXPLANATION LEVEL{topic}: {plan.level} of 5 - {name}: {description}.",
        f"Talk like you would to {plan.band.register}. The grade sets the words and the most complex "
        f"treatment that is relevant (level {plan.band.ceiling} here); this topic's evidence sets where "
        "to start. The level describes the explanation, never the student.",
        f"Topic knowledge: {plan.mastery.replace('_', ' ')}. "
        + PATTERNS[plan.pattern].format(prerequisites=prerequisites),
    ]
    if plan.request:
        lines.append(REQUEST_DIRECTIVES[plan.request])
    if plan.representation:
        lines.append(plan.representation)
    if plan.focus in FOCUS_DIRECTIVES and plan.request not in {"confused", "simpler"}:
        lines.append(FOCUS_DIRECTIVES[plan.focus])
    lines.append(
        "Size the reply to the need: a simple factual question gets the answer and one line of why; a "
        "concept gets the key idea and one example at this level; only a hard idea, or a request for it, "
        "gets a full worked example, one step at a time with WHY each step is done. Ask a check question "
        "only when the answer would tell you something new. Never conclude mastery from one correct "
        "answer, and never judge ability from age, grade, spelling or writing style."
    )
    return "\n".join(lines)

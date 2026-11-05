"""From a subject strategy to one concrete lesson: which parts, in which order, and how
the model's `parts` are repaired afterwards.

The lesson prompt used to ask every subject for the same shape (one explanation, one
worked example, tips), so an Ethics topic got a "worked example" and no retelling of the
thought experiment it was about. Now the lesson is a list of typed parts planned from
the subject's strategy and the student's state on the topic:

* `knowledge_state` turns attempts and mastery into new / developing / secure.
* `plan_steps` picks the strategy's steps for the plan mode (learn, review, reteach), the
  state and the item kinds the topic map actually has (no experiment part for a topic
  without an experiment), and guarantees "teach first": the objective comes first and
  something is explained before any exercise.
* `lesson_prompt_block` is the compact instruction block for the lesson prompt.
* `repair_parts`, `ensure_teaching_first`, `teaching_order_ok` and
  `explanation_from_parts` normalise what the model returns (repair, not reject).
* `exercise_type` picks the graded answer format for question n from the strategy.

Pure module: no database, no AI calls.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from typing import Any

from .strategies.base import (
    ALL_MODES, EXPLANATION_KINDS, KNOWLEDGE_STATES, PART_KINDS, PLAN_MODES, RETEACH_ONLY, SERVER_PART_KINDS,
    LessonStep, SubjectStrategy,
)

SECURE_MASTERY = 80.0
# Room for the longest real plan (Ethics, ten steps) with its priorities, rules and exam style.
PROMPT_BLOCK_LIMIT = 4200
MAX_PARTS = 14
# Planned steps; room is left in MAX_PARTS for the server's key-terms and flashcards parts
# and an "added" copy of a part (lesson_context.guard_parts).
MAX_PLAN_STEPS = 12
MAX_TITLE = 160
MAX_BODY = 4000
MAX_ITEMS = 12
MAX_ITEM_LABEL = 160
MAX_ITEM_TEXT = 1200
MAX_ANSWER = 2000
MAX_SOURCE_PAGES = 30
MAX_EXPLANATION = 6000

REVIEW_KINDS = ("objective", "terms", "guided_exercise", "summary")
# Parts that are not lecturing but still count as "taught before practice" in a review.
_REVIEW_TEACHING = frozenset({"terms", "example", "summary"})
_FALLBACK_KINDS = ("objective", "explanation", "guided_exercise", "summary")
_PRACTICE_ANCHORS = ("mistakes", "guided_exercise", "summary")

PARTS_SHAPE = ('"parts":[{"kind":"<step kind>","title":"short heading","body":"the explanation text",'
               '"items":[{"label":"short label","text":"text"}],"answer":"guided_exercise only: the worked solution",'
               '"from_notes":true,"source_pages":[1]}]')

_GENERIC_STEPS = {
    "objective": LessonStep("objective", "Say in one or two sentences what the student will be able to do after "
                            "this lesson.", modes=ALL_MODES),
    "explanation": LessonStep("explanation", "Explain the core idea of the topic clearly and simply, building on "
                              "what the student already knows.", modes=ALL_MODES),
    "terms": LessonStep("terms", "List the key terms of this topic from the notes as items: label = the term exactly "
                        "as in the notes, text = a simple definition.", modes=ALL_MODES),
    "mistakes": LessonStep("mistakes", "List the typical mistakes or misunderstandings with this topic, each with "
                           "the correct idea.", modes=ALL_MODES),
    "example": LessonStep("example", "Give one example that is simpler than before and explain it step by step.",
                          modes=ALL_MODES),
    "guided_exercise": LessonStep("guided_exercise", "Give one guided task that applies the idea, with hints in "
                                  "items and the solution in answer.", modes=ALL_MODES),
    "summary": LessonStep("summary", "Sum up the key points in a few short lines.", modes=ALL_MODES),
}
REVIEW_OBJECTIVE = ("Remind the student in two short lines what this topic is about and what they should be able "
                    "to do.")
_RETEACH_EXPLAIN = "Explain it again in a different way than before (another representation, smaller steps): "
_RETEACH_EXAMPLE = "Use a simpler example than before: "
_SKIP_IF_ABSENT = " Leave this part out if the material has nothing for it."
_WHY_LINE = " Add one sentence on why it matters (for the exam, everyday life or later topics)."
# Prefix for an explaining part the student's state had filtered out but that has to come
# back because nothing else would be taught before the exercise.
RECAP_PREFIX = "Keep this to a short recap of what the student needs before practising: "

_MODE_LINES = {
    "learn": "Teach first, then test: every part teaches, and the graded questions come after the lesson.",
    "review": ("This is a REVIEW of a topic the student has learned before: keep it short (a reminder, the key "
               "terms, one practice task, a recap) and add no long new explanations."),
    "reteach": ("This is a RETEACH: the student's answers on this topic were wrong. Explain it again differently "
                "and more simply than a first explanation, and address the typical mistakes."),
}
_STATE_LINES = {
    "new": "The student is a beginner on this topic (state new): explain fully and simply before any exercise.",
    "developing": ("The student has started this topic but is not secure yet (state developing): explain clearly "
                   "and focus on what is usually misunderstood."),
    "secure": ("The student already knows this topic well (state secure): keep explanations to a short recap and "
               "spend the lesson on practice and typical mistakes."),
}
_FROM_NOTES_LINE = ("Mark every part with from_notes true only when its content is in the student's notes; your own "
                    "additions (interpretations, extra examples, background) get from_notes false. Put the pages a "
                    "part is based on in source_pages. Never invent quotations, names, dates or figures.")


# --------------------------------------------------------------------------- state and plan

def knowledge_state(*, attempts: int, mastery_score: float | None) -> str:
    """new (never practised), secure (mastery >= 80) or developing."""

    try:
        count = int(attempts or 0)
    except (TypeError, ValueError):
        count = 0
    if count <= 0:
        return "new"
    try:
        score = float(mastery_score) if mastery_score is not None else None
    except (TypeError, ValueError):
        score = None
    return "secure" if score is not None and score >= SECURE_MASTERY else "developing"


def _normal_mode(mode: str) -> str:
    mode = str(mode or "").strip().casefold()
    return mode if mode in PLAN_MODES else "learn"


def _normal_state(state: str) -> str:
    state = str(state or "").strip().casefold()
    return state if state in KNOWLEDGE_STATES else "new"


def _first(steps: Iterable[LessonStep], kind: str, *, prefer_reteach: bool = False) -> LessonStep | None:
    matches = [step for step in steps if step.kind == kind]
    if prefer_reteach:
        own = [step for step in matches if step.modes == RETEACH_ONLY]
        if own:
            return own[0]
    return matches[0] if matches else None


def _review_steps(eligible: list[LessonStep]) -> list[LessonStep]:
    steps = []
    for step in eligible:
        if step.kind in REVIEW_KINDS and all(s.kind != step.kind for s in steps):
            steps.append(replace(step, instruction=REVIEW_OBJECTIVE) if step.kind == "objective" else step)
    for kind in REVIEW_KINDS:
        if all(s.kind != kind for s in steps):
            step = _GENERIC_STEPS[kind]
            steps.append(replace(step, instruction=REVIEW_OBJECTIVE) if kind == "objective" else step)
    return sorted(steps, key=lambda s: REVIEW_KINDS.index(s.kind))


def _reteach_steps(eligible: list[LessonStep]) -> list[LessonStep]:
    # A reteach is short and different: the same goal, the content explained another way,
    # what usually goes wrong, a simpler example, one guided try, a recap. A strategy's own
    # reteach-only steps say how "another way" looks in that subject; otherwise the
    # learn step is reused with a note to change the representation.
    explaining = [step for step in eligible if step.kind in EXPLANATION_KINDS]
    own = [step for step in explaining if step.modes == RETEACH_ONLY]
    if own:
        explanation = own[0]
    elif explaining:
        explanation = replace(explaining[0], instruction=_RETEACH_EXPLAIN + explaining[0].instruction)
    else:
        explanation = _GENERIC_STEPS["explanation"]
    example = _first(eligible, "example", prefer_reteach=True)
    if example is None:
        example = _GENERIC_STEPS["example"]
    elif example.modes != RETEACH_ONLY:
        example = replace(example, instruction=_RETEACH_EXAMPLE + example.instruction)
    return [
        _first(eligible, "objective") or _GENERIC_STEPS["objective"],
        explanation,
        _first(eligible, "mistakes", prefer_reteach=True) or _GENERIC_STEPS["mistakes"],
        example,
        _first(eligible, "guided_exercise") or _GENERIC_STEPS["guided_exercise"],
        _first(eligible, "summary") or _GENERIC_STEPS["summary"],
    ]


def _insert_at(steps: list[LessonStep], step: LessonStep, strategy: SubjectStrategy) -> int:
    """Where `step` goes back into `steps`: at its place in the strategy's order (so a
    restored explanation does not land after the example it is meant to explain), never
    before the objective and never after the first guided exercise."""

    rank = {id(s): i for i, s in enumerate(strategy.steps)}
    guided = next((i for i, s in enumerate(steps) if s.kind == "guided_exercise"), len(steps))
    target = rank.get(id(step))
    if target is not None:
        for index in range(1, guided):
            other = rank.get(id(steps[index]))
            if other is not None and other > target:
                return index
    anchor = next((i for i, s in enumerate(steps) if s.kind in _PRACTICE_ANCHORS), len(steps))
    return max(1, min(anchor, guided))


def _teach_first(steps: list[LessonStep], strategy: SubjectStrategy, mode: str, state: str,
                 available: Callable[[LessonStep], bool]) -> list[LessonStep]:
    """Objective first and at least one explaining part before the first guided exercise."""

    objective = _first(steps, "objective")
    if objective is None:
        objective = next((s for s in strategy.steps if s.kind == "objective"), _GENERIC_STEPS["objective"])
    steps = [objective] + [s for s in steps if s.kind != "objective"]
    guided = next((i for i, s in enumerate(steps) if s.kind == "guided_exercise"), None)
    limit = guided if guided is not None else len(steps)
    if any(s.kind in EXPLANATION_KINDS for s in steps[:limit]):
        return steps
    later = next((s for s in steps[limit:] if s.kind in EXPLANATION_KINDS), None)
    if later is not None:
        steps.remove(later)
        original = explaining = later
    else:
        # The state filter removed every explaining part (a "secure" lesson): bring back the
        # strategy's first one, as a short recap, rather than letting the student practise
        # untaught content.
        found = next((s for s in strategy.steps if s.kind in EXPLANATION_KINDS and mode in s.modes
                      and s.modes != RETEACH_ONLY and available(s)), None)
        original = found or _GENERIC_STEPS["explanation"]
        explaining = original
        if found is not None and state not in found.states:
            explaining = replace(found, instruction=RECAP_PREFIX + found.instruction)
    steps.insert(_insert_at(steps, original, strategy), explaining)
    return steps


def _known_kinds(item_kinds: Iterable[Any] | None) -> set[str] | None:
    """The item kinds of the topic map as a set; None when the topic has no map yet.

    Accepts kinds or whole item dicts, and a single kind given as a plain string (which
    would otherwise be read letter by letter)."""

    if item_kinds is None or not isinstance(item_kinds, Iterable):
        return None
    if isinstance(item_kinds, str):
        item_kinds = [item_kinds]
    known: set[str] = set()
    for entry in item_kinds:
        kind = entry.get("kind") if isinstance(entry, Mapping) else entry
        text = str(kind or "").strip().casefold()
        if text:
            known.add(text)
    return known


def plan_steps(strategy: SubjectStrategy, *, mode: str, state: str,
               item_kinds: Iterable[Any] | None = None) -> list[LessonStep]:
    """The steps of one lesson, in order.

    `item_kinds` are the kinds of the items in the topic map (or the item dicts); a step
    with `when_items` is kept only when one of them is present. None means the topic has
    no map yet (an old project): such steps are kept with a note to leave them out when
    the material has nothing for them, instead of losing, say, the thought experiment of
    an old Ethics topic."""

    mode = _normal_mode(mode)
    state = _normal_state(state)
    known = _known_kinds(item_kinds)

    def available(step: LessonStep) -> bool:
        return not step.when_items or known is None or bool(known.intersection(step.when_items))

    eligible = [s for s in strategy.steps if mode in s.modes and state in s.states and available(s)]
    if mode == "review":
        steps = _review_steps(eligible)
    elif mode == "reteach":
        steps = _teach_first(_reteach_steps(eligible), strategy, mode, state, available)
    else:
        if state == "secure":
            eligible = [s for s in eligible if s.kind != "why"]
        steps = _teach_first(eligible, strategy, mode, state, available) if eligible else []
    if not steps:
        steps = [_GENERIC_STEPS[kind] for kind in _FALLBACK_KINDS]
    steps = _capped(steps)
    if mode != "review" and state != "secure" and not any(step.kind == "why" for step in steps):
        # Default lesson structure, step 2 "Why is it important?": in one sentence of the
        # objective, unless the plan has its own "why" part (mathematics).
        steps = [replace(step, instruction=step.instruction + _WHY_LINE)
                 if step.kind == "objective" and "why it matters" not in step.instruction else step
                 for step in steps]
    if known is None:
        steps = [replace(s, instruction=s.instruction + _SKIP_IF_ABSENT) if s.when_items else s for s in steps]
    return steps


def _capped(steps: list[LessonStep]) -> list[LessonStep]:
    """At most MAX_PLAN_STEPS steps. A subject made of three (Sachunterricht: sciences,
    geography and history) would plan sixteen parts, more than a lesson answer holds; the
    explaining parts nearest the practice phase go first, parts that wait for an item
    kind before the others, and the objective, an explaining part, the practice and the
    summary always stay."""

    steps = list(steps)
    keep = {"objective", "guided_exercise", "summary", "mistakes"}
    while len(steps) > MAX_PLAN_STEPS:
        explaining = [i for i, step in enumerate(steps) if step.kind in EXPLANATION_KINDS]
        candidates = [i for i, step in enumerate(steps) if step.kind not in keep
                      and not (step.kind in EXPLANATION_KINDS and explaining == [i])]
        if not candidates:
            break
        conditional = [i for i in candidates if steps[i].when_items]
        steps.pop((conditional or candidates)[-1])
    return steps


# --------------------------------------------------------------------------- prompt block

def _sentence(text: str) -> str:
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    return match.group(1) if match else text


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…" if limit > 1 else ""


# Priorities and rules also reach the model through `tutor_rules_block` in the system
# instructions; the exam style is only in this block. When space is short it is
# therefore kept first, then the rules, then the priorities (shown in this order).
_EXTRA_ORDER = ("priorities", "rules", "exam")
_EXTRA_PRIORITY = ("exam", "rules", "priorities")


def lesson_prompt_block(strategy: SubjectStrategy, steps: Iterable[LessonStep], *, mode: str, state: str,
                        grade_label: str = "") -> str:
    """The teaching-method block of the lesson prompt (at most PROMPT_BLOCK_LIMIT characters).

    Every numbered step and the parts shape are always present; the exam style, rules and
    priorities are added while they fit, and step instructions shrink to their first
    sentence only if the steps alone would not fit."""

    mode = _normal_mode(mode)
    state = _normal_state(state)
    steps = list(steps) or [_GENERIC_STEPS[kind] for kind in _FALLBACK_KINDS]
    head = f"TEACHING METHOD — {strategy.name}. {_MODE_LINES[mode]} {_STATE_LINES[state]}"
    grade = " ".join(str(grade_label or "").split())[:80]
    if grade:
        head += f" Pitch every part at the level of {grade}."
    order = 'Build "parts" in exactly this order, one part per step:'
    shape = "PARTS SHAPE: " + PARTS_SHAPE

    def assemble(instructions: list[str], extras: list[str]) -> str:
        numbered = [f"{i}. {step.kind} — {text}" for i, (step, text) in enumerate(zip(steps, instructions), 1)]
        return "\n".join([head, order, *numbered, _FROM_NOTES_LINE, *extras, shape])

    instructions = [step.instruction for step in steps]
    if len(assemble(instructions, [])) > PROMPT_BLOCK_LIMIT:
        instructions = [_sentence(text) for text in instructions]
    optional = {
        "priorities": "Priorities: " + "; ".join(strategy.priorities) + "." if strategy.priorities else "",
        "rules": "Rules: " + " ".join(strategy.rules) if strategy.rules else "",
        "exam": "Exam style: " + strategy.exam_style if strategy.exam_style else "",
    }
    chosen: set[str] = set()
    for name in _EXTRA_PRIORITY:
        trial = chosen | {name}
        lines = [optional[key] for key in _EXTRA_ORDER if key in trial and optional[key]]
        if optional[name] and len(assemble(instructions, lines)) <= PROMPT_BLOCK_LIMIT:
            chosen = trial
    block = assemble(instructions, [optional[key] for key in _EXTRA_ORDER if key in chosen and optional[key]])
    if len(block) > PROMPT_BLOCK_LIMIT:
        # Only reachable with an unusually long custom plan (no extra fitted then): give
        # every instruction the same share of the room left, so each step survives.
        room = PROMPT_BLOCK_LIMIT - len(assemble([""] * len(steps), []))
        share = max(0, room // max(1, len(steps)))
        block = assemble([_clip(text, share) for text in instructions], [])
    if len(block) > PROMPT_BLOCK_LIMIT:
        # More steps than the block can even list: the parts shape must still be there.
        block = block[: PROMPT_BLOCK_LIMIT - len(shape) - 1].rstrip() + "\n" + shape
    return block


# --------------------------------------------------------------------------- repairing parts

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_KIND_ALIASES = {
    # terms
    "definitions": "terms", "definition": "terms", "fachbegriffe": "terms", "fachbegriff": "terms",
    "glossary": "terms", "glossar": "terms", "vocabulary": "terms", "vokabeln": "terms", "wortschatz": "terms",
    "key_terms": "terms", "begriffe": "terms", "term": "terms",
    # objective
    "goal": "objective", "goals": "objective", "lernziel": "objective", "lernziele": "objective",
    "learning_objective": "objective", "learning_objectives": "objective", "learning_goal": "objective",
    "learning_goals": "objective", "objectives": "objective", "what": "objective", "ziel": "objective",
    "aim": "objective",
    # why
    "importance": "why", "why_it_matters": "why", "relevance": "why", "motivation": "why", "warum": "why",
    # summary
    "recap": "summary", "zusammenfassung": "summary", "conclusion": "summary", "fazit": "summary",
    "merksatz": "summary",
    # guided exercise
    "exercise": "guided_exercise", "exercises": "guided_exercise", "practice": "guided_exercise",
    "guided": "guided_exercise", "guided_practice": "guided_exercise", "uebung": "guided_exercise",
    "uebungen": "guided_exercise", "aufgabe": "guided_exercise", "task": "guided_exercise",
    "practice_task": "guided_exercise", "try_it": "guided_exercise",
    # mistakes
    "common_mistakes": "mistakes", "typische_fehler": "mistakes", "pitfalls": "mistakes", "fehler": "mistakes",
    "mistake": "mistakes", "misconceptions": "mistakes", "fehlvorstellungen": "mistakes", "errors": "mistakes",
    "common_errors": "mistakes",
    # timeline
    "chronology": "timeline", "chronologie": "timeline", "zeitleiste": "timeline", "zeitstrahl": "timeline",
    # cause and effect
    "causes": "cause_effect", "ursachen": "cause_effect", "cause_and_effect": "cause_effect",
    "causes_and_consequences": "cause_effect", "consequences": "cause_effect", "folgen": "cause_effect",
    "ursachen_und_folgen": "cause_effect", "ursache_und_wirkung": "cause_effect",
    # experiment
    "versuch": "experiment", "experiments": "experiment", "versuchsprotokoll": "experiment",
    # thought experiment
    "gedankenexperiment": "thought_experiment", "thought_experiments": "thought_experiment",
    # model analysis
    "example_analysis": "model_analysis", "musteranalyse": "model_analysis", "sample_analysis": "model_analysis",
    "model_answer": "model_analysis", "musterinterpretation": "model_analysis",
    # worked example
    "worked_examples": "worked_example", "musterloesung": "worked_example", "musteraufgabe": "worked_example",
    "solved_example": "worked_example", "example_solution": "worked_example",
    # example
    "examples": "example", "beispiel": "example", "beispiele": "example", "application": "example",
    "anwendung": "example",
    # explanation
    "erklaerung": "explanation", "concept": "explanation", "introduction": "explanation",
    "einfuehrung": "explanation", "context": "explanation", "background": "explanation",
    # rule
    "regel": "rule", "regeln": "rule", "rules": "rule", "grammar": "rule", "grammar_rule": "rule",
    "formula": "rule", "formel": "rule", "method": "rule", "methode": "rule",
    # structure
    "pattern": "structure", "sentence_pattern": "structure", "satzbau": "structure", "satzmuster": "structure",
    "aufbau": "structure",
    # theory
    "theorie": "theory", "theories": "theory", "theorien": "theory", "position": "theory",
    # argument
    "arguments": "argument", "argumente": "argument", "pro_contra": "argument", "pros_and_cons": "argument",
    "argumentation": "argument",
    # perspectives
    "perspective": "perspectives", "perspektive": "perspectives", "perspektiven": "perspectives",
    "positions": "perspectives", "viewpoints": "perspectives", "comparison": "perspectives",
    "vergleich": "perspectives", "stakeholders": "perspectives",
    # process
    "prozess": "process", "processes": "process", "ablauf": "process", "procedure": "process",
    "algorithm": "process", "algorithmus": "process",
    # diagram
    "diagramm": "diagram", "diagrams": "diagram", "map": "diagram", "karte": "diagram", "graph": "diagram",
    "chart": "diagram", "schaubild": "diagram",
    # source analysis
    "quellenanalyse": "source_analysis", "source": "source_analysis", "sources": "source_analysis",
    "quelle": "source_analysis", "quellen": "source_analysis", "translation": "source_analysis",
    "uebersetzung": "source_analysis",
}
_SERVER_ALIASES = frozenset({*SERVER_PART_KINDS, "flashcard", "flash_cards", "karteikarten"})
# Unknown kind labels are matched by the first of these fragments they contain, before
# falling back to "explanation". The fallback matters most for exercises: a guided
# exercise mislabelled "Practice task" would otherwise become an explanation and its
# solution would be shown in the lesson text.
_KIND_HINTS = (
    ("worked", "worked_example"), ("musterloesung", "worked_example"),
    ("guided", "guided_exercise"), ("exercise", "guided_exercise"), ("uebung", "guided_exercise"),
    ("practice", "guided_exercise"), ("aufgabe", "guided_exercise"),
    ("mistake", "mistakes"), ("fehler", "mistakes"), ("misconception", "mistakes"), ("pitfall", "mistakes"),
    ("summary", "summary"), ("zusammenfassung", "summary"), ("recap", "summary"),
    ("objective", "objective"), ("lernziel", "objective"),
    ("thought_experiment", "thought_experiment"), ("gedankenexperiment", "thought_experiment"),
    ("experiment", "experiment"), ("versuch", "experiment"),
    ("model_analysis", "model_analysis"), ("musteranalyse", "model_analysis"), ("quellen", "source_analysis"),
    ("timeline", "timeline"), ("zeitleiste", "timeline"), ("chronolog", "timeline"),
    ("cause", "cause_effect"), ("ursache", "cause_effect"),
    ("perspective", "perspectives"), ("perspektive", "perspectives"),
    ("terms", "terms"), ("terminolog", "terms"), ("begriff", "terms"), ("glossar", "terms"), ("vocab", "terms"),
    ("vokabel", "terms"), ("definition", "terms"),
    ("diagram", "diagram"), ("process", "process"), ("prozess", "process"),
    ("argument", "argument"), ("theor", "theory"), ("rule", "rule"), ("regel", "rule"),
    ("example", "example"), ("beispiel", "example"), ("why", "why"),
)
# Field names are compared folded (case, umlauts, spaces/dashes → "_"), so "Title",
# "sourcePages", "Erklärung" and German field names a model invents still match.
_PART_KIND_FIELDS = ("kind", "type", "part", "part_kind", "category", "typ")
_PART_TITLE_FIELDS = ("title", "heading", "headline", "name", "label", "titel", "ueberschrift")
_PART_BODY_FIELDS = ("body", "text", "content", "explanation", "description", "inhalt", "erklaerung",
                     "beschreibung")
_PART_ITEM_FIELDS = ("items", "points", "list", "entries", "terms", "steps", "bullets", "hints", "definitions",
                     "punkte", "begriffe", "fachbegriffe", "schritte", "tipps")
_PART_ANSWER_FIELDS = ("answer", "solution", "model_answer", "loesung", "musterloesung", "antwort")
_PART_NOTES_FIELDS = ("from_notes", "fromnotes", "in_notes", "innotes")
_PART_PAGE_FIELDS = ("source_pages", "sourcepages", "pages", "source_page_ids", "page", "seiten", "seite")
_LABEL_KEYS = ("label", "term", "name", "key", "title", "step", "date", "cause", "word", "claim", "phase", "mistake",
               "wrong", "error", "incorrect", "question", "year", "person", "symbol", "form", "stakeholder", "group",
               "begriff", "wort", "datum", "jahr", "ursache", "schritt", "fehler", "frage")
_TEXT_KEYS = ("text", "definition", "simple_definition", "description", "value", "content", "detail", "effect",
              "meaning", "explanation", "event", "result", "translation", "reason", "correction", "correct", "right",
              "fix", "thesis", "position", "interest", "role", "hint", "tip", "note", "example", "bedeutung",
              "erklaerung", "beschreibung", "inhalt", "folge", "wirkung", "ereignis", "korrektur", "hinweis", "tipp",
              "beispiel", "uebersetzung", "answer")
_BULLET = re.compile(r"^(?:[-•*–]\s+|\d{1,2}[.)]\s+)")
_MAX_INDENT = 24
_PAGE_LIST = re.compile(r"[\d\s,;]+")


def _kind_key(value: Any) -> str:
    text = str(value or "").strip().casefold().translate(_FOLD)
    return re.sub(r"[^0-9a-z]+", "_", text).strip("_")


def _part_kind(value: Any) -> str | None:
    """The PART_KINDS kind for a model's kind label; None for a server-only kind."""

    key = _kind_key(value)
    if key in _SERVER_ALIASES:
        return None
    if key in PART_KINDS:
        return key
    if key in _KIND_ALIASES:
        return _KIND_ALIASES[key]
    return next((kind for fragment, kind in _KIND_HINTS if fragment in key), "explanation")


def _folded(raw: Mapping[Any, Any]) -> dict[str, Any]:
    # An exactly spelled field wins over a variant that folds to the same name.
    fields: dict[str, Any] = {}
    for key, value in raw.items():
        name = _kind_key(key)
        if name and (name not in fields or key == name):
            fields[name] = value
    return fields


def _scalar(value: Any) -> str:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value)


def _line(value: Any, limit: int) -> str:
    return " ".join(_scalar(value).split())[:limit]


def _body(value: Any, limit: int) -> str:
    """Body text with runs of spaces and blank lines tidied, newlines kept.

    Relative indentation survives (common indentation removed, at most 24 spaces) because
    bodies carry code (a Computer Science trace, a program to correct), where indentation
    is meaning."""

    if isinstance(value, (list, tuple)):
        value = "\n".join(_scalar(entry) for entry in value)
    rows: list[tuple[int, str]] = []
    for line in _scalar(value).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        expanded = line.expandtabs(4)
        words = expanded.split()
        rows.append((len(expanded) - len(expanded.lstrip()) if words else 0, " ".join(words)))
    common = min((indent for indent, text in rows if text), default=0)
    lines = [" " * min(indent - common, _MAX_INDENT) + text if text else "" for indent, text in rows]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    return text[:limit].rstrip()


def _pick(fields: Mapping[str, Any], keys: tuple[str, ...], limit: int, *, skip: str = "") -> tuple[str, str]:
    """(field name, text) of the first non-empty field among `keys`."""

    for key in keys:
        if key == skip:
            continue
        text = _line(fields.get(key), limit)
        if text:
            return key, text
    return "", ""


def _item(entry: Any) -> dict[str, str] | None:
    label = text = ""
    if isinstance(entry, Mapping):
        fields = _folded(entry)
        label_key, label = _pick(fields, _LABEL_KEYS, MAX_ITEM_LABEL)
        _, text = _pick(fields, _TEXT_KEYS, MAX_ITEM_TEXT, skip=label_key)
        pairs = [(_line(key, MAX_ITEM_LABEL), _line(value, MAX_ITEM_TEXT)) for key, value in entry.items()
                 if _kind_key(key) != label_key]
        pairs = [(key, value) for key, value in pairs if key and value]
        if label and not text and pairs:
            # {"term": "Pflicht", "bedeutung_einfach": "…"}: the meaning sits in a field
            # nobody predicted; take it rather than lose it.
            text = pairs[0][1]
        elif not label and not text and pairs:
            if len(pairs) == 1:             # {"Pflicht": "what one must do"}
                label, text = pairs[0]
            else:                           # unknown field names: keep every value, readable
                text = _line("; ".join(f"{key.replace('_', ' ')}: {value}" for key, value in pairs), MAX_ITEM_TEXT)
    elif isinstance(entry, (list, tuple)):  # ["Pflicht", "what one must do"]
        values = [value for value in (_line(value, MAX_ITEM_TEXT) for value in entry) if value]
        if len(values) == 1:
            text = values[0]
        elif values:
            label, text = values[0][:MAX_ITEM_LABEL], _line(" – ".join(values[1:]), MAX_ITEM_TEXT)
    else:
        text = _BULLET.sub("", _line(entry, MAX_ITEM_TEXT))
    if label and not text:
        label, text = "", label[:MAX_ITEM_TEXT]
    return {"label": label, "text": text} if text else None


def _items(value: Any) -> list[dict[str, str]]:
    if isinstance(value, Mapping):          # {"term": "definition", ...}
        value = [{**entry, "label": key} if isinstance(entry, Mapping) else {"label": key, "text": entry}
                 for key, entry in value.items()]
    elif isinstance(value, str):
        value = value.split("\n")
    if not isinstance(value, (list, tuple)):
        return []
    items: list[dict[str, str]] = []
    for entry in value:
        item = _item(entry)
        if item:
            items.append(item)
        if len(items) >= MAX_ITEMS:
            break
    return items


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return value == 1
    return isinstance(value, str) and value.strip().casefold() in {"true", "yes"}


def _pages(value: Any) -> list[int]:
    if isinstance(value, str):
        # "3", "3, 4" or "3 4"; anything else ("2/6", "S. 3") is not a page list.
        value = value.replace(",", " ").replace(";", " ").split() if _PAGE_LIST.fullmatch(value) else []
    elif not isinstance(value, (list, tuple)):
        value = [value]
    pages: list[int] = []
    for entry in value:
        if isinstance(entry, bool):
            continue
        if isinstance(entry, float) and entry.is_integer():
            entry = int(entry)
        if isinstance(entry, str) and entry.strip().isdecimal():
            entry = int(entry.strip())
        if isinstance(entry, int) and entry > 0 and entry not in pages:
            pages.append(entry)
        if len(pages) >= MAX_SOURCE_PAGES:
            break
    return pages


def _first_value(fields: Mapping[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = fields.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def repair_parts(value: Any, *, limit: int = MAX_PARTS) -> list[dict[str, Any]]:
    """The model's `parts` in the canonical shape; unusable parts are dropped, never the list.

    Accepts a list of part dicts (or a dict holding one under parts/steps/sections), kind
    labels and field names in many spellings, items as strings, pairs, dicts or a term →
    definition mapping."""

    if isinstance(value, Mapping):
        wrapper = _folded(value)
        value = next((wrapper[key] for key in ("parts", "steps", "sections")
                      if isinstance(wrapper.get(key), (list, tuple))), None)
    if not isinstance(value, (list, tuple)) or limit <= 0:
        return []
    parts: list[dict[str, Any]] = []
    for raw in value:
        if isinstance(raw, str):
            raw = {"kind": "explanation", "body": raw}
        if not isinstance(raw, Mapping):
            continue
        fields = _folded(raw)
        kind = _part_kind(_first_value(fields, _PART_KIND_FIELDS))
        if kind is None:
            continue
        body = _body(_first_value(fields, _PART_BODY_FIELDS), MAX_BODY)
        items = _items(_first_value(fields, _PART_ITEM_FIELDS))
        answer = _body(_first_value(fields, _PART_ANSWER_FIELDS), MAX_ANSWER)
        if answer and kind != "guided_exercise":
            # Only the guided exercise hides its solution; elsewhere the answer (a worked
            # example's result, say) is content the student must see.
            if items and len(items) < MAX_ITEMS:
                items.append({"label": "", "text": _line(answer, MAX_ITEM_TEXT)})
            else:
                body = _body(f"{body}\n\n{answer}" if body else answer, MAX_BODY)
            answer = ""
        if kind == "guided_exercise" and not body:
            # An exercise without its task (NwT, live 2026-11-02: "body": "" with two hints and a
            # model answer): with a solution, its first hint becomes the task; else it goes.
            if not (answer and items):
                continue
            body, items = _body(str(items[0].get("text") or ""), MAX_BODY), items[1:]
            if not body:
                continue
        if not body and not items:
            continue
        parts.append({
            "kind": kind,
            "title": _line(_first_value(fields, _PART_TITLE_FIELDS), MAX_TITLE),
            "body": body,
            "items": items,
            "answer": answer,
            "from_notes": _truthy(_first_value(fields, _PART_NOTES_FIELDS)),
            "source_pages": _pages(_first_value(fields, _PART_PAGE_FIELDS) or []),
        })
        if len(parts) >= limit:
            break
    return parts


def _kind_of(part: Any) -> Any:
    return part.get("kind") if isinstance(part, Mapping) else None


def _first_explaining(parts: list[dict[str, Any]]) -> int | None:
    return next((i for i, part in enumerate(parts) if _kind_of(part) in EXPLANATION_KINDS), None)


def ensure_teaching_first(parts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Guided exercises the model put before the first explaining part move right after it.

    When the lesson has no explaining part at all nothing can be moved; that gap is
    reported by `teaching_order_ok` instead of being papered over."""

    parts = list(parts)
    first = _first_explaining(parts)
    if first is None:
        return parts
    early = [part for part in parts[:first] if _kind_of(part) == "guided_exercise"]
    if not early:
        return parts
    rest = [part for part in parts if not any(part is moved for moved in early)]
    anchor = next(i for i, part in enumerate(rest) if part is parts[first])
    return rest[: anchor + 1] + early + rest[anchor + 1:]


def teaching_order_ok(parts: list[dict[str, Any]], *, mode: str = "learn") -> bool:
    """False when a guided exercise comes before anything has been taught (a teaching gap).

    A review lesson has no long explanation by design; there, key terms or a recap before
    the exercise count as taught."""

    teaching = EXPLANATION_KINDS | (_REVIEW_TEACHING if _normal_mode(mode) == "review" else frozenset())
    for part in parts:
        kind = _kind_of(part)
        if kind in teaching:
            return True
        if kind == "guided_exercise":
            return False
    return True


def _part_text(part: Mapping[str, Any], *, with_items: bool) -> str:
    body = str(part.get("body") or "").strip()
    if body or not with_items:
        return body
    lines = []
    for item in part.get("items") or []:
        if isinstance(item, Mapping):
            label, text = str(item.get("label") or "").strip(), str(item.get("text") or "").strip()
            line = f"{label}: {text}" if label and text else text or label
            if line:
                lines.append(line)
    return "\n".join(lines)


def explanation_from_parts(parts: list[dict[str, Any]]) -> str:
    """The objective, explaining and summary bodies as one text (at most 6000 characters).

    This fills the lesson's required short `explanation` when the model only sent parts.
    When those parts carry their content only as items (a theory as premise steps), the
    items are used, and as a last resort any part's text, so the field is never empty
    while the lesson has content. Guided-exercise solutions are never used."""

    usable = [part for part in parts if isinstance(part, Mapping)]
    wanted = [part for part in usable if part.get("kind") in EXPLANATION_KINDS | {"objective", "summary"}]
    text = ""
    for group, with_items in ((wanted, False), (wanted, True), (usable, True)):
        text = "\n\n".join(chunk for chunk in (_part_text(part, with_items=with_items) for part in group) if chunk)
        if text:
            break
    if len(text) <= MAX_EXPLANATION:
        return text
    cut = text[:MAX_EXPLANATION]
    boundary = max(cut.rfind("\n\n"), cut.rfind(". "))
    return (cut[: boundary + 1] if boundary > MAX_EXPLANATION // 2 else cut).rstrip()


# --------------------------------------------------------------------------- graded answer formats

def exercise_type(strategy: SubjectStrategy, number: int) -> str:
    """Answer format of graded question `number` (1-based): the first is always multiple
    choice (an easy start), then the strategy's formats in rotation."""

    try:
        number = int(number)
    except (TypeError, ValueError):
        number = 1
    if number <= 1:
        return "multiple_choice"
    types = strategy.question_types or ("multiple_choice",)
    return types[(number - 1) % len(types)]


# --------------------------------------------------------------------------- question stages

STAGE_LINES = {
    "comprehension": ("a comprehension question: what the material says or what happens in it (understanding, "
                      "no judgement yet)"),
    "application": "an application question: use the idea on a new case, task or example",
    "interpretation": ("an interpretation question: what an element, example or argument means or is meant to "
                       "show, answered with reasons from the material"),
    "discussion": ("a discussion question: compare the positions or weigh the arguments for and against, "
                   "with reasons"),
    "exam": "an exam-style task that requires a justified answer, written like this subject's exam tasks",
}


def question_stage(strategy: SubjectStrategy, number: int, *, minimum: int = 3, planned: int | None = None) -> str:
    """The stage of graded question `number` (1-based; question 1 comes with the lesson).

    The strategy's stages are spread over the questions the session is expected to ask
    (`planned`, at least `minimum`, the earliest the knowledge gate may stop), so every
    stage, the exam-style task last, comes up in a session of that length; any question
    after that stays at the last stage."""

    stages = strategy.question_stages or ("comprehension", "exam")
    try:
        number = max(1, int(number))
    except (TypeError, ValueError):
        number = 1
    try:
        expected = max(int(minimum or 0), int(planned or 0))
    except (TypeError, ValueError):
        expected = 0
    span = max(2, min(len(stages), expected or len(stages)))
    index = round((number - 1) * (len(stages) - 1) / (span - 1))
    return stages[min(len(stages) - 1, index)]


def question_stage_rule(strategy: SubjectStrategy, *, current: int, upcoming: int, minimum: int = 3,
                        planned: int | None = None) -> str:
    """The prompt line that tells the grader what the next question asks for: the stage the
    next question reaches after a correct answer, the current stage again after a wrong one,
    and this subject's exam style when the stage is the exam task."""

    now = question_stage(strategy, current, minimum=minimum, planned=planned)
    after = question_stage(strategy, upcoming, minimum=minimum, planned=planned)
    line = (f"NEXT QUESTION STAGE: if the answer is correct, the next question is {STAGE_LINES[after]}. If it is "
            f"wrong, the next question stays at this question's stage ({now}) and asks about the same idea in new "
            "words, aimed at the misconception.")
    if "exam" in (now, after) and strategy.exam_style:
        line += f" Exam style in {strategy.name}: {strategy.exam_style}"
    return line

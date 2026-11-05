"""What a topic lesson and its graded answers are given, and how the lesson is checked
against the student's notes afterwards.

The lesson prompt used to carry `source[:24000]`: pages in upload order until the cut,
so the last files of a six-file project never reached the lesson, and the answer
evaluation sent the same 24k characters into a 5000-token budget (HTTP 413). Here:

* `bounded_pages` gives every page a fair share of a fixed budget (short pages keep all
  their text, the room they leave goes to the long ones), each page labelled
  "[<file>, p. <n>]", so no file is dropped and every quote can be traced.
* `relevant_excerpt` picks, deterministically, the passages of those pages that share
  the most words with the question, for grading within a small budget.
* `topic_map_block` is the structured topic (summary, Fachbegriffe with their in-notes
  flags, typed items, goals, exam questions) as a bounded prompt block.
* `plan_item_kinds` turns the topic map into the item kinds `lesson_plan.plan_steps`
  needs, counting only what was found in the notes - including what the notes show by
  their own headings (`heading_kinds`: an experiment, a source, a map).
* `guard_parts` holds the model's parts to the notes: no experiment or thought
  experiment the notes do not contain, Fachbegriffe the notes do not contain moved into
  a part marked as added, interpretations and readings the notes do not support marked
  as added, experiment phases in their scientific order.
* `resolve_part_pages` turns the page numbers a part cites into the lesson's own pages
  ("<file> p. <n>" labels and page ids), so two files' "p. 1" are never confused.
* `terms_part` is the server's key-terms part when the model left out a planned one.
* `flashcards_part`, `insert_after_terms`, `taught_evidence` and `updated_coverage`
  build the server's flashcards part and the topic's coverage record.
* `reveals_answer` tells whether feedback given before a second try gives the answer away;
  `retry_reveals` adds a paraphrase guard and the right option's own words for the retry
  texts, and `quotes_source` tells whether a grader quoted the notes.
* `notes_support` / `notes_contradiction` find the notes sentence that says a claim, or its
  opposite; `choice_key_conflict`, `chosen_option_support`, `repeats_exercise` and
  `term_question` hold a lesson's questions to the notes and the lesson (PF_FIX2).

Pure module: no database, no AI calls.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from learnova.projects.terms import item_key, term_key, terms_mentioned

from .strategies.base import EXPLANATION_KINDS

LESSON_SOURCE_LIMIT = 16_000
ANSWER_SOURCE_LIMIT = 4_000
TOPIC_MAP_LIMIT = 5_000
MAX_MAP_TERMS = 20
MAX_MAP_ITEMS = 20
MAX_LESSON_CARDS = 12
CHUNK_SIZE = 700

_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_STOP = frozenset({
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einem", "einer", "eines", "und", "oder",
    "aber", "ist", "sind", "war", "waren", "wird", "werden", "mit", "von", "fuer", "auf", "aus", "bei", "nach",
    "nicht", "auch", "als", "wie", "was", "wer", "wann", "warum", "welche", "welcher", "welches", "dass", "sich",
    "sie", "ihr", "ihre", "seine", "sein", "zum", "zur", "the", "and", "for", "are", "was", "were", "with", "from",
    "that", "this", "what", "which", "who", "why", "how", "does", "did", "not", "you", "your", "its", "his", "her",
    "les", "des", "une", "est", "que", "qui", "los", "las", "una", "por", "para", "con",
})
_SUFFIXES = ("ungen", "ung", "en", "er", "es", "em", "e", "n", "s")
_WORD = re.compile(r"\w+")


# --------------------------------------------------------------------------- page sources

@dataclass(frozen=True)
class SourcePage:
    page_id: int
    label: str
    text: str


def page_label(filename: str, page_number: int) -> str:
    """"[<file>, p. <n>]": the label every page carries in a lesson or grading prompt."""

    name = " ".join(str(filename or "").split())[:120] or "notes"
    return f"[{name}, p. {int(page_number or 1)}]"


def _tidy(text: str) -> str:
    lines = [" ".join(line.split()) for line in str(text or "").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _clip(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a word boundary and marked with "…"."""

    if len(text) <= limit:
        return text
    if limit <= 2:
        return "…"[:max(0, limit)]
    cut = text[: limit - 2]
    space = max(cut.rfind(" "), cut.rfind("\n"))
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + " …"


def fair_shares(lengths: Sequence[int], room: int) -> list[int]:
    """Characters each page may use: short pages keep everything, the rest split evenly.

    Water filling: the shortest pages are served first, and whatever they leave is shared
    by the longer ones. The shares never add up to more than `room`."""

    shares = [0] * len(lengths)
    remaining = max(0, int(room))
    order = sorted(range(len(lengths)), key=lambda index: (lengths[index], index))
    for position, index in enumerate(order):
        share = remaining // max(1, len(order) - position)
        shares[index] = min(max(0, int(lengths[index])), share)
        remaining -= shares[index]
    return shares


def bounded_pages(pages: Sequence[SourcePage], limit: int = LESSON_SOURCE_LIMIT) -> list[SourcePage]:
    """The pages with their text cut to a fair share, so `source_block` stays within `limit`.

    Every page keeps its label even when its share is tiny, so the prompt still names
    every page the topic was built from; nothing is silently left out."""

    tidy = [SourcePage(page.page_id, page.label, _tidy(page.text)) for page in pages]
    overhead = sum(len(page.label) + 1 for page in tidy) + 2 * max(0, len(tidy) - 1)
    shares = fair_shares([len(page.text) for page in tidy], limit - overhead)
    return [SourcePage(page.page_id, page.label, _clip(page.text, share) if page.text else "")
            for page, share in zip(tidy, shares)]


def source_block(pages: Sequence[SourcePage]) -> str:
    return "\n\n".join(f"{page.label}\n{page.text}".rstrip() for page in pages)


# --------------------------------------------------------------------------- relevant excerpt

def _stem(word: str) -> str:
    for suffix in _SUFFIXES:
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def keywords(text: Any) -> set[str]:
    words = _WORD.findall(str(text or "").casefold().translate(_FOLD))
    return {_stem(word) for word in words if len(word) >= 3 and word not in _STOP and not word.isdigit()}


def _chunks(text: str, size: int = CHUNK_SIZE) -> list[str]:
    """Paragraphs, long ones split at sentence ends, so an excerpt quotes whole thoughts."""

    out: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        while len(paragraph) > size:
            cut = max(paragraph.rfind(". ", 0, size), paragraph.rfind("\n", 0, size))
            cut = cut + 1 if cut > size // 3 else size
            out.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        if paragraph:
            out.append(paragraph)
    return out


def relevant_excerpt(pages: Sequence[SourcePage], query: str, limit: int = ANSWER_SOURCE_LIMIT) -> str:
    """The passages most relevant to `query`, in note order, at most `limit` characters.

    Deterministic: passages are ranked by how many of the query's content words they
    contain, ties by their position in the notes. With no overlap at all, each page's
    first passage is taken in turn, so the excerpt still spans the whole topic."""

    if limit <= 0:
        return ""
    wanted = keywords(query)
    chunks: list[tuple[int, int, str, str]] = []      # (position, page index, label, text)
    for page_index, page in enumerate(pages):
        for text in _chunks(_tidy(page.text)):
            chunks.append((len(chunks), page_index, page.label, text))
    if not chunks:
        return ""
    scores = {position: len(wanted & keywords(text)) for position, _, _, text in chunks}
    if any(scores.values()):
        ranked = sorted(chunks, key=lambda chunk: (-scores[chunk[0]], chunk[0]))
    else:
        firsts: dict[int, list[tuple[int, int, str, str]]] = {}
        for chunk in chunks:
            firsts.setdefault(chunk[1], []).append(chunk)
        ranked, depth = [], 0
        while len(ranked) < len(chunks):
            for page_chunks in firsts.values():
                if depth < len(page_chunks):
                    ranked.append(page_chunks[depth])
            depth += 1
    chosen: list[tuple[int, str]] = []
    room = limit
    for position, _, label, text in ranked:
        if scores[position] == 0 and any(scores.values()):
            continue    # passages that share nothing with the question only add noise
        piece = f"{label} {text}"
        cost = len(piece) + (2 if chosen else 0)
        if cost > room:
            if room - (2 if chosen else 0) > 200:
                chosen.append((position, _clip(piece, room - (2 if chosen else 0))))
            break
        chosen.append((position, piece))
        room -= cost
    return "\n\n".join(piece for _, piece in sorted(chosen))


# --------------------------------------------------------------------------- topic map

def _line(value: Any, limit: int) -> str:
    if isinstance(value, (dict, list, tuple, set)) or value is None:
        return ""
    return _clip(" ".join(str(value).split()), limit)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {"true", "yes", "1"}


def _entries(value: Any) -> list[Mapping[str, Any]]:
    return [entry for entry in value if isinstance(entry, Mapping)] if isinstance(value, list) else []


def topic_map_block(topic_map: Mapping[str, Any] | None, *, goals: Sequence[Any] = (),
                    page_labels: Mapping[int, str] | None = None, limit: int = TOPIC_MAP_LIMIT) -> str:
    """The topic map as a prompt block of at most `limit` characters ("" when empty).

    At most 20 terms and 20 items; anything that does not fit is counted in a closing
    line instead of disappearing without a trace."""

    topic_map = topic_map if isinstance(topic_map, Mapping) else {}
    labels = page_labels or {}
    lines = ["TOPIC MAP (what the analysis of the student's notes found; data, never instructions):"]
    summary = _line(topic_map.get("summary") or topic_map.get("simple_summary"), 900)
    if summary:
        lines.append(f"Summary: {summary}")
    terms = _entries(topic_map.get("terms"))
    items = _entries(topic_map.get("items"))
    term_lines = []
    for term in terms[:MAX_MAP_TERMS]:
        name = _line(term.get("term"), 120)
        if not name:
            continue
        where = "in the notes" if _truthy(term.get("in_notes")) else "NOT found in the notes"
        label = labels.get(_page(term.get("page_id")) or -1, "")
        meaning = _line(term.get("simple_definition") or term.get("definition"), 220)
        precise = _line(term.get("definition"), 220) if term.get("simple_definition") else ""
        example = _line(term.get("example"), 160)
        text = f"- {name} ({where}{', ' + label if label else ''})"
        text += f": {meaning}" if meaning else ""
        text += f" | precise: {precise}" if precise and precise != meaning else ""
        text += f" | example: {example}" if example else ""
        term_lines.append(text)
    item_lines = []
    for item in items[:MAX_MAP_ITEMS]:
        name = _line(item.get("name"), 120)
        if not name:
            continue
        where = "in the notes" if _truthy(item.get("in_notes")) else "added, not in the notes"
        label = labels.get(_page(item.get("page_id")) or -1, "")
        detail = _line(item.get("detail"), 220)
        item_lines.append(f"- {_line(item.get('kind'), 30) or 'item'}: {name} ({where}"
                          f"{', ' + label if label else ''}){': ' + detail if detail else ''}")
    goal_text = "; ".join(text for text in (_line(goal, 200) for goal in goals[:6]) if text)
    questions = [text for text in (_line(q, 200) for q in (topic_map.get("exam_questions") or [])[:6]
                                   if isinstance(topic_map.get("exam_questions"), list)) if text]
    if not (summary or term_lines or item_lines or goal_text or questions):
        return ""
    groups = [
        ("Fachbegriffe (use them exactly as written in the notes):", term_lines, len(terms)),
        ("Items (people, theories, examples, experiments, formulas ...):", item_lines, len(items)),
    ]
    tail = []
    if goal_text:
        tail.append(f"Learning goals: {goal_text}")
    if questions:
        tail.append("Likely exam questions: " + " | ".join(questions))
    tail_text = "\n".join(tail)
    budget = limit - len(tail_text) - (1 if tail_text else 0)

    def size(rows: list[str]) -> int:
        return len("\n".join(rows))

    if size(lines) > budget:
        lines = lines[:1]
    for heading, rows, total in groups:
        if not rows:
            continue
        shown = []
        for row in rows:
            if size(lines + [heading] + shown + [row]) + 40 > budget:
                break
            shown.append(row)
        if shown:
            lines.append(heading)
            lines.extend(shown)
        missing = total - len(shown)
        if missing > 0 and size(lines) + 40 <= budget:
            lines.append(f"(+{missing} more not listed here)")
    block = "\n".join(lines + tail)
    return block[:limit]


def _page(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def plan_item_kinds(topic_map: Mapping[str, Any] | None, *, source_text: str = "") -> list[str] | None:
    """The item kinds `plan_steps` may plan for, or None for a topic without a map.

    Only what was found in the notes counts: an added example (in_notes false) must not
    switch on a part that teaches "the example from the notes". A map counts as checked
    when it has a terms or items list (every v4 analysis checks both against the pages).

    The notes' own headings count too (`heading_kinds` of `source_text`): the analysis
    returned NwT notes headed Fragestellung ... Erklärung with `items: []`, History notes
    with "Quelle: Art. 48 ..." without a source item, and a "Kartenbeschreibung" without a
    map item, and each lesson then lost the step that defines its subject (live, 2026-11-01)."""

    if not isinstance(topic_map, Mapping) or not any(isinstance(topic_map.get(key), list)
                                                      for key in ("terms", "items")):
        return None
    kinds = [str(item.get("kind") or "").strip().casefold() for item in _entries(topic_map.get("items"))
             if _truthy(item.get("in_notes"))]
    if any(_truthy(term.get("in_notes")) for term in _entries(topic_map.get("terms"))):
        kinds.append("term")
    kinds.extend(sorted(heading_kinds(source_text)))
    return [kind for kind in dict.fromkeys(kinds) if kind]


# --------------------------------------------------------------------------- item kinds from headings

HEADING_MAX_WORDS = 6
SOURCE_QUOTE_MIN_WORDS = 15
# A bullet or a number in front of a heading ("- ", "2. ", "b) ").
_HEADING_MARK = re.compile(r"^\s*(?:[-–—•*·]+\s*|\(?\d{1,2}[.)]\s+|\(?[a-z][.)]\s+)", re.IGNORECASE)
# A line that introduces a source: "Quelle: ...", "Q1 ...", "M2: ...", "Art. 48 ...", "Zitat: ...".
_SOURCE_LINE = re.compile(r"^(?:quelle|source|q\d+|m\d+|art\.|artikel|auszug|zitat)(?![^\W\d_])[\s:.\-–)]*\S",
                          re.IGNORECASE)
# A heading about a map or a location: "Kartenbeschreibung", "Atlaskarte", "Lage", "map".
# Not "Karteikarten" (flashcards, review B B-2).
_PLACE_HEADING = re.compile(r"(?<!\w)(?:(?!karteikart)\w*karte\w*|lage|atlas\w*|maps?|locations?)(?!\w)",
                            re.IGNORECASE)
_QUOTED_PASSAGE = re.compile(r"[„“”\"«»]([^„“”\"«»]+)[“”\"«»]")


def _headings(source_text: str) -> list[str]:
    """The headings of the notes: a short line (at most six words), or the label in front
    of the first colon of a line ("Durchführung: Wasser einfüllen ..."), numbering and
    bullets removed."""

    found = []
    for raw in str(source_text or "").splitlines():
        line = _HEADING_MARK.sub("", raw).strip()
        if not line:
            continue
        whole = line.rstrip(":").strip()
        if whole and len(whole.split()) <= HEADING_MAX_WORDS:
            found.append(whole)
            continue
        label, colon, _rest = line.partition(":")
        if colon and label.strip() and len(label.split()) <= HEADING_MAX_WORDS:
            found.append(label.strip())
    return found


def heading_kinds(source_text: str) -> set[str]:
    """Item kinds the notes show by their own headings (pure).

    * experiment: headings of at least two different phases of a lab report (Fragestellung,
      Hypothese/Vermutung, Aufbau/Material, Durchführung, Beobachtung, Ergebnis/Auswertung,
      Erklärung, and the English names in EXPERIMENT_PHASES), at least one of them a phase
      only a lab report has (DISTINCTIVE_PHASE_ALIASES: hypothesis, set-up, procedure,
      observation - not "Material", "Ziel", "Ergebnis" or "Erklärung");
    * source: a line that introduces one ("Quelle: ...", "Q1", "M2", "Art. 48", "Zitat"), or a
      quoted passage of at least fifteen words;
    * place: a heading about a map or a location ("Kartenbeschreibung", "Lage", "map")."""

    text = str(source_text or "")
    if not text.strip():
        return set()
    headings = _headings(text)
    kinds: set[str] = set()
    phases = {phase for phase in (experiment_phase(heading) for heading in headings) if phase is not None}
    # Review B B-2: "Ziel der Stunde", "Ergebnis", "Erklärung", "Material" head worksheets of
    # every subject (a chemistry calculation sheet had two of them). Only a lab report has a
    # hypothesis, a set-up, a procedure or an observation.
    if len(phases) >= 2 and any(_distinctive_phase(heading) for heading in headings):
        kinds.add("experiment")
    lines = (_HEADING_MARK.sub("", line).strip() for line in text.splitlines())
    if any(_SOURCE_LINE.match(line) for line in lines if line) or any(
            len(passage.split()) >= SOURCE_QUOTE_MIN_WORDS for passage in _QUOTED_PASSAGE.findall(text)):
        kinds.add("source")
    if any(_PLACE_HEADING.search(heading) for heading in headings):
        kinds.add("place")
    return kinds


# --------------------------------------------------------------------------- holding parts to the notes

# Parts that may only retell what the notes contain, and the item kinds that show the
# notes contain one. A thought experiment or an experiment "from the notes" that the
# notes do not have would be invented, however plausible.
NOTES_ONLY_PARTS: dict[str, tuple[str, ...]] = {
    # The retelling of the notes' thought experiment, text or example: the extraction labels
    # a parable or dilemma "example" as often as "thought_experiment".
    "thought_experiment": ("thought_experiment", "work", "source", "example"),
    "experiment": ("experiment",),
}

# Parts that interpret or argue. A model may present its own reading ("the fire stands for
# the media") as the notes' own; such a sentence or item shares few words with the pages,
# so it is checked against them and marked as added when the notes do not support it.
INTERPRETIVE_KINDS = frozenset({"thought_experiment", "theory", "argument", "perspectives", "source_analysis",
                                "model_analysis"})
# Parts that explain the notes' content, held to the pages the same way (PF4 L6, live
# 2026-11-01): a "why" part's "Kosten-Gewinn-Diagramme", invented "Sicherheitsregeln … Handschuhe"
# in a rule part and a cause "Hyperinflation … Radikalisierung" were all shown as the notes'
# own. Measured offline on the seven live lessons: every such quoted sentence moves to
# "added", 68 of 69 other from-notes units stay (one model definition of "Fehlerquellen" moves).
# A diagram part too (PF_FIX2 F4, live 2026-11-02): notes that only describe a map in words got
# a "diagram" part about a "Wellenhöhe vs. Zeit" chart the notes do not have, shown as theirs.
HELD_KINDS = frozenset({"why", "explanation", "rule", "cause_effect", "diagram"})
SUPPORTED_SHARE = 0.5       # share of a reading's content words that must be on the pages
MIN_CHECKED_WORDS = 4       # shorter sentences ("Kant sagt Nein.") are not judged either way
# A retelling in the model's own words shares only part of its vocabulary with the notes
# ("lodert" for "brennt", "verspotten" for "lachen aus"): a measured faithful paraphrase of
# the cave had 40-46 % of its words on the page per sentence, and half of it was moved to
# an "added" part, out of order. Sentences that tell (no reading of what something "stands
# for") therefore only move when almost none of their words are on the pages, as an
# invented scene's are; readings and interpretations keep the strict share.
TOLD_SHARE = 0.25
RETOLD_MIN_WORDS = 8        # a retelling kept although its kind is not in the map has substance
READING_MIN_WORDS = 2       # "das Feuer steht für die Medien": two things related, judged
# "X steht für Y", "symbolisiert", "stands for", "représente" ...: a sentence that says what
# something means. Those are the readings the notes must give.
_READING = re.compile(
    r"\bsteht\b[^.!?;]{0,30}\bf(?:ü|ue)r\b|\bstehen\b[^.!?;]{0,30}\bf(?:ü|ue)r\b|\bsymbolisier|\bverkörper"
    r"|\bbedeute|\bsinnbild|\bmetapher|\bgemeint\b|\bsoll(?:en)?\b[^.!?;]{0,40}\bzeigen\b"
    r"|\bstands? for\b|\brepresent|\bsymboli[sz]|\bmeans?\b|\bmetaphor"
    r"|\breprésent|\bsymbolis|\bsignifi|\brepresenta|\bsimboliza"
    # Comparisons and transfers to today ("die Gefangenen sind wie Menschen, die heute ..."),
    # the other shape an added reading takes.
    r"|\b(?:ist|sind|wäre|wären|wirkt|wirken|is|are|was|were)\b[^.!?;]{0,25}\b(?:wie|like)\b|\bvergleichbar"
    r"|\bheute\b|\bheutzutage\b|\btoday\b|\bnowadays\b|\baujourd'hui\b|\bhoy\b",
    re.IGNORECASE)
# The words of those phrases themselves (as keywords() spells them): left out when a reading
# is checked, so "symbolisiert" or "zeigen" never counts as a word the notes lack.
_READING_WORD = re.compile(r"^(?:steh|fuer|symboli|verkoerper|bedeut|sinnbild|metapher|gemeint|soll|zeig|stand"
                           r"|represent|mean|vergleichbar|heut|today|nowaday|aujourd|hoy|signifi|simboliz|wirk"
                           r"|waer|like)")
# Function words the shared stop list keeps (it serves the excerpt search too); they are
# no evidence either way whether a sentence is in the notes.
_SUPPORT_STOP = frozenset({
    "dir", "dich", "mir", "mich", "uns", "euch", "ihn", "ihm", "ihnen", "ihrer", "ihren", "ihrem", "ihres", "seiner",
    "seinen", "seinem", "seines", "deine", "dein", "meine", "mein", "vor", "vom", "seit", "bis", "durch", "gegen",
    "ohne", "unter", "ueber", "hinter", "neben", "zwischen", "noch", "nur", "schon", "dann", "denn", "doch", "weil",
    "wenn", "damit", "deshalb", "dort", "hier", "jetzt", "immer", "sehr", "mehr", "kann", "koennen", "muss", "soll",
    "will", "hat", "haben", "hatte", "wurde", "wurden", "sei", "also", "ganz", "einer", "stell", "wir", "unser",
    "unsere", "unserer", "unseren", "unserem", "eigentlich", "genau", "eben", "etwa", "our", "we",
    "they", "them", "their", "there", "then", "than", "into", "onto", "over", "under", "about", "after", "before",
    "only", "just", "also", "very", "can", "could", "would", "should", "has", "have", "had", "been", "being", "imagine",
    "dans", "pour", "avec", "sur", "sous", "mais", "comme", "tout", "tous", "elle", "ils", "elles", "leur", "leurs",
    "del", "como", "pero", "todo", "todos", "ellos", "ellas", "sobre", "entre",
})
# Function words that tell the language of a text. A lesson written in another language
# than the notes (an English lesson on German notes) cannot be checked word by word.
_LANGUAGE_WORDS = {
    "de": frozenset({"der", "die", "das", "und", "ist", "nicht", "ein", "eine", "mit", "sich", "auch", "dass"}),
    "en": frozenset({"the", "and", "is", "of", "to", "that", "this", "with", "are", "not", "it"}),
    "fr": frozenset({"le", "la", "les", "et", "est", "des", "une", "que", "pas", "dans", "pour"}),
    "es": frozenset({"el", "los", "las", "y", "es", "una", "que", "del", "por", "para", "con", "no"}),
}
_SENTENCE = re.compile(r"(?<=[.!?;])\s+")

EXPERIMENT_PHASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("question", ("question", "frage", "fragestellung", "forschungsfrage", "aim", "ziel", "objective")),
    ("hypothesis", ("hypothes", "vermutung", "prediction", "vorhersage")),
    ("setup", ("setup", "set_up", "material", "aufbau", "geraet", "apparatus")),
    ("procedure", ("procedure", "durchfuehrung", "steps", "ablauf", "method", "vorgehen")),
    ("observation", ("observation", "beobachtung")),
    ("result", ("result", "ergebnis", "auswertung", "messwert", "data")),
    ("explanation", ("explanation", "erklaerung", "deutung", "interpretation", "conclusion", "schluss")),
)


# The phase names only a lab report uses (review B B-2): a hypothesis, a set-up, a procedure,
# an observation. "Material" (a worksheet's materials), "Ziel"/"Frage", "Ergebnis",
# "Erklärung", "Methode", "Ablauf" or "Steps" head notes of every subject.
DISTINCTIVE_PHASE_ALIASES = ("hypothes", "vermutung", "prediction", "vorhersage", "setup", "set_up", "aufbau",
                             "geraet", "apparatus", "procedure", "durchfuehrung", "observation", "beobachtung")
# "Versuchsaufbau", "Versuchsdurchführung", "Versuchsbeobachtung": the phase after "Versuch".
_EXPERIMENT_PREFIX = re.compile(r"^versuchs?_?(?=[a-z])")


def _folded(text: Any) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(text or "").casefold().translate(_FOLD)).strip("_")


def _phase_label(label: Any) -> str:
    return _EXPERIMENT_PREFIX.sub("", _folded(label))


def _has_alias(folded: str, aliases: Sequence[str]) -> bool:
    return any(folded.startswith(alias) or f"_{alias}" in folded for alias in aliases)


def experiment_phase(label: Any) -> int | None:
    """The position of an experiment item's phase (0 = question … 6 = explanation)."""

    folded = _phase_label(label)
    if not folded:
        return None
    for index, (_name, aliases) in enumerate(EXPERIMENT_PHASES):
        if _has_alias(folded, aliases):
            return index
    return None


def _distinctive_phase(label: Any) -> bool:
    folded = _phase_label(label)
    return bool(folded) and _has_alias(folded, DISTINCTIVE_PHASE_ALIASES)


def order_experiment_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Experiment items in the order of a lab report; observation always before its
    interpretation. Items without a recognisable phase keep their order at the end."""

    known = [(experiment_phase(item.get("label")), index, dict(item)) for index, item in enumerate(items)]
    ordered = sorted((entry for entry in known if entry[0] is not None), key=lambda entry: (entry[0], entry[1]))
    rest = [entry for entry in known if entry[0] is None]
    return [item for _, _, item in ordered + rest]


_MONTHS = {
    name: number for number, names in enumerate((
        ("januar", "january", "jan", "janvier", "enero", "jaenner"),
        ("februar", "february", "feb", "fevrier", "febrero"),
        ("maerz", "march", "mar", "mars", "marzo"),
        ("april", "apr", "avril", "abril"),
        ("mai", "may", "mayo"),
        ("juni", "june", "jun", "juin", "junio"),
        ("juli", "july", "jul", "juillet", "julio"),
        ("august", "aug", "aout", "agosto"),
        ("september", "sep", "sept", "septembre", "septiembre"),
        ("oktober", "october", "oct", "okt", "octobre", "octubre"),
        ("november", "nov", "novembre", "noviembre"),
        ("dezember", "december", "dec", "dez", "decembre", "diciembre"),
    ), start=1) for name in names
}
_ACCENTS = str.maketrans({"é": "e", "è": "e", "ê": "e", "û": "u", "ú": "u", "á": "a", "à": "a", "í": "i", "ó": "o"})
_BEFORE_CHRIST = re.compile(r"\bv\.?\s*chr\b|\bvor\s+christus|\bv\.?\s*u\.?\s*z\b|\bbce?\b|\bav\.?\s*j\.?\s*-?\s*c\b|"
                            r"\ba\.\s*c\b|\bantes\s+de\s+cristo")
# A century or millennium is not one point in time: such a label keeps the model's order.
_VAGUE_TIME = re.compile(r"jahrhundert|jahrtausend|\bjh\b|centur|millenni|siecle|siglo")


def timeline_date(label: Any) -> tuple[int, int, int] | None:
    """(year, month, day) of a timeline label such as "14. Juli 1789", "1914–1918",
    "44 v. Chr." or "14.07.1789"; month and day are 0 when the label gives none. None when
    the label is not clearly one date (a century, "Nachkriegszeit", "Phase 2")."""

    text = " ".join(str(label or "").casefold().translate(_FOLD).translate(_ACCENTS).split())
    if not text or _VAGUE_TIME.search(text):
        return None
    numeric = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{3,4})\b", text)
    if numeric:
        day, month, year = (int(value) for value in numeric.groups())
        return (year, month, day) if 1 <= month <= 12 and 1 <= day <= 31 else None
    numbers = re.findall(r"\d+", text)
    years = [number for number in numbers if 3 <= len(number) <= 4]
    before_christ = bool(_BEFORE_CHRIST.search(text))
    if years:
        year = int(years[0])
    elif before_christ and numbers:
        year = int(numbers[-1])
    else:
        return None
    if before_christ:
        return (-year, 0, 0)
    month = next((_MONTHS[word] for word in re.findall(r"[a-z]+", text) if word in _MONTHS), 0)
    days = [int(number) for number in numbers if len(number) <= 2] if month else []
    day = days[0] if days and 1 <= days[0] <= 31 else 0
    return (year, month, day)


def order_timeline_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Timeline items in chronological order, when every label is clearly a date.

    The lesson asks the model for the events in order, but a timeline shown out of order
    teaches a wrong chronology. Only a timeline whose every label is a clear date is
    sorted (stably: a bare year stays before the days of that year); anything else keeps
    the model's order rather than guessing."""

    entries = [dict(item) for item in items]
    keys = [key for key in (timeline_date(item.get("label")) for item in entries) if key is not None]
    if len(entries) < 2 or len(keys) != len(entries):
        return entries
    order = sorted(range(len(entries)), key=lambda index: (keys[index], index))
    return [entries[index] for index in order]


def _fold_text(text: Any) -> str:
    return " ".join(str(text or "").casefold().translate(_FOLD).split())


def guard_parts(parts: Sequence[Mapping[str, Any]], *, item_kinds: Iterable[str] | None,
                verified_terms: Iterable[str] = (), source_text: str = "", has_notes: bool = True,
                heading_only_kinds: Iterable[str] = ()) -> list[dict[str, Any]]:
    """The model's parts, held to what the notes contain.

    * Without notes (a topic the student typed in) nothing is "from your notes".
    * A thought experiment or experiment part is dropped when the checked topic map has
      no such item: the notes do not contain one, so it cannot be retold from them. A kind
      only the notes' headings show (`heading_only_kinds`: in `item_kinds` through
      `heading_kinds`, no checked item) is no such item either - the part stays only when
      it is retold from the pages (review B B-2: an invented experiment on a worksheet).
    * In a key-terms part claimed as from the notes, terms neither in the topic map
      (`verified_terms`, term keys) nor written in the source move into a second key-terms
      part marked as added, so an added term is never shown as the student's own; so does
      a sentence of a term's text the pages do not support (an invented example).
    * Interpreting parts (INTERPRETIVE_KINDS) and explaining parts (HELD_KINDS: why,
      explanation, rule, cause and effect, diagram) keep only the sentences the pages
      support; the rest moves into a copy marked as added. A sentence that says the
      opposite of a notes sentence (one of the two negated: "ohne" / "mit") is not
      supported, however many words they share.
    * Experiment items are put into lab-report order, timeline items (all clear dates) into
      chronological order.
    """

    known = set(item_kinds) if item_kinds is not None else None
    headed = set(heading_only_kinds)
    if known is not None:
        known -= headed
    verified = set(verified_terms)
    source = _fold_text(source_text)
    page_words = _Support(source_text) if has_notes and str(source_text or "").strip() else None
    if page_words is not None:
        languages = {_language(parts_text(parts)), _language(source_text)}
        if None not in languages and len(languages) > 1:
            page_words = None
    note_sentences = notes_sentences(source_text) if page_words is not None else []
    out: list[dict[str, Any]] = []
    for raw in parts:
        part = dict(raw)
        kind = part.get("kind")
        if not has_notes:
            part["from_notes"] = False
        # A kind only the headings show is judged on what the part tells, not on its title and
        # phase labels ("Fragestellung", "Durchführung" are on every lab report's pages).
        held_by = NOTES_ONLY_PARTS.get(kind, ()) if isinstance(kind, str) else ()
        told = _told_content(part) if headed.intersection(held_by) else part
        if kind in NOTES_ONLY_PARTS and known is not None and not known.intersection(NOTES_ONLY_PARTS[kind]) \
                and not (part.get("from_notes") and page_words is not None and page_words.retold(told)):
            # Kept only when its content is on the pages anyway: the extraction labels an
            # allegory "theory" as often as "thought_experiment", and the label must not
            # cost the student the retelling of their own notes.
            continue
        if kind == "experiment" and isinstance(part.get("items"), list):
            part["items"] = order_experiment_items(part["items"])
        if kind == "timeline" and isinstance(part.get("items"), list):
            part["items"] = order_timeline_items(part["items"])
        if kind in (INTERPRETIVE_KINDS | HELD_KINDS) and part.get("from_notes") and page_words is not None:
            out.extend(_held_to_pages(part, page_words, strict=kind in HELD_KINDS, note_sentences=note_sentences))
            continue
        if kind == "terms" and part.get("from_notes") and isinstance(part.get("items"), list) and (verified or source):
            own, added = [], []
            for item in part["items"]:
                label = str(item.get("label") or "").strip() if isinstance(item, Mapping) else ""
                found = bool(label) and (term_key(label) in verified
                                         or (len(label) >= 2 and _fold_text(label) in source))
                (own if found else added).append(item)
            if page_words is not None:
                # A term from the notes with an example the notes do not give ("Schadensprinzip
                # … Beispiel: Verbot von gefährlichen Waffen", live 2026-11-01): the added
                # sentence moves, the term keeps what the notes say.
                own, held_out = _held_term_items(own, page_words)
                added.extend(held_out)
            if added:
                if own:
                    out.append({**part, "items": own})
                out.append({**part, "items": added, "body": "" if own else part.get("body", ""),
                            "from_notes": False, "source_pages": []})
                continue
        out.append(part)
    return out


def _told_content(part: Mapping[str, Any]) -> dict[str, Any]:
    """`part` without its title and item labels: what it tells, for `_Support.retold`."""

    items = [{**item, "label": ""} if isinstance(item, Mapping) else item for item in part.get("items") or []]
    return {**part, "title": "", "items": items}


def _language(text: Any) -> str | None:
    """"de", "en", "fr" or "es" by function words, None when too little text says so."""

    counts = Counter(word for word in _WORD.findall(str(text or "").casefold()))
    scores = {code: sum(counts[word] for word in words) for code, words in _LANGUAGE_WORDS.items()}
    best = max(scores, key=lambda code: scores[code])
    return best if scores[best] >= 3 else None


class _Support:
    """The content words of the pages, for "is this sentence in the notes?"."""

    def __init__(self, text: str) -> None:
        self.words = keywords(text)
        self.long = sorted(word for word in self.words if len(word) >= 4)

    def has(self, word: str) -> bool:
        # Compounds and inflections: "Schattenbilder" is supported by "Schatten",
        # "Sonnenlicht" by "Sonne", and a compound's head by the compound and back
        # ("Experiment" by the notes' "Gedankenexperiment": live 2026-11-01, the notes' own
        # sentence was moved to "added" for that word alone). Both sides at least five letters.
        if word in self.words:
            return True
        if len(word) < 5:
            return False
        return any(word.startswith(other) or other.startswith(word)
                   or (len(other) >= 5 and (other.endswith(word) or word.endswith(other)))
                   for other in self.long)

    def share(self, words: set[str]) -> float:
        return sum(1 for word in words if self.has(word)) / len(words) if words else 1.0

    def supports(self, text: str, *, strict: bool = False) -> bool | None:
        """True / False for a sentence, None when it is too short to judge. A reading
        ("the fire stands for ...") or a `strict` claim (a listed item) needs
        SUPPORTED_SHARE of its words on the pages; a sentence that retells needs only
        TOLD_SHARE, so a paraphrase stays."""

        words = {word for word in keywords(text) if word not in _SUPPORT_STOP}
        if _READING.search(str(text or "")):
            # A reading ("X steht für Y", "sie sind wie ...") is the notes' own only when
            # everything it relates is on the pages: one word the notes never use ("Handy",
            # "Medien") is the added part, however many of the notes' words surround it.
            words = {word for word in words if not _READING_WORD.match(word)}
            if len(words) < READING_MIN_WORDS:
                return None
            return all(self.has(word) for word in words)
        if len(words) < MIN_CHECKED_WORDS:
            return None
        return self.share(words) >= (SUPPORTED_SHARE if strict else TOLD_SHARE)

    def retold(self, part: Mapping[str, Any]) -> bool:
        """Whether a whole retelling part is told from the pages: at least SUPPORTED_SHARE
        of its content words (and RETOLD_MIN_WORDS of them) are on the pages. Measured: a
        faithful paraphrase of the notes' story 0.58, an invented story on the same
        topic's vocabulary 0.20."""

        words = {word for word in keywords(parts_text([part])) if word not in _SUPPORT_STOP}
        return len(words) >= RETOLD_MIN_WORDS and self.share(words) >= SUPPORTED_SHARE


# A full stop that ends an abbreviation or an ordinal, not a sentence: "z. B.", "d. h.",
# "bzw.", "Art.", "11. März" (a sentence split there left half a term text behind).
_NOT_A_SENTENCE_END = re.compile(
    r"(?:^|[\s(])(?:[^\W\d_]|\d{1,2}|bzw|ca|vgl|nr|art|abs|bspw|ggf|evtl|inkl|usw|etc|sog|eg|ie|vs|dr|prof|st|jh|chr)"
    r"\.$", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    pieces = [piece for piece in (chunk.strip() for chunk in _SENTENCE.split(str(text or ""))) if piece]
    merged: list[str] = []
    for piece in pieces:
        if merged and _NOT_A_SENTENCE_END.search(merged[-1]):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


# --------------------------------------------------------------------------- what the notes say, sentence by sentence
#
# PF_FIX2 (live 2026-11-02): a lesson sentence, a choice question's option or a student's chosen
# option is checked against the notes' own sentences - does one of them say it, or say the
# opposite? Word shares as in `_Support`, per sentence, plus the polarity: "ohne Sperrklausel
# (keine 5-Prozent-Hürde)" and "verlangte eine 5-Prozent-Sperrklausel" share their words but
# not their meaning. A sentence that shows a mistake ("Falsch: I have seen him yesterday.")
# supports the mistake, never the claim.

# Words that negate a claim (folded like _fold_text; "n't" is spelt "not" first).
_NEGATION_WORDS = frozenset({
    "nicht", "nichts", "kein", "keine", "keinen", "keinem", "keiner", "keines", "ohne", "nie", "niemals", "weder",
    "not", "no", "never", "without", "neither", "nor", "none", "cannot",
    "pas", "sans", "jamais", "aucun", "aucune", "ni",
    "sin", "nunca", "ningun", "ninguna", "ninguno", "tampoco",
})
_NEGATION_STEMS = frozenset(_stem(word) for word in _NEGATION_WORDS)
_CONTRACTED_NOT = re.compile(r"n['’]t\b", re.IGNORECASE)
# A sentence that shows a mistake, not a claim: "Falsch: …", "Typische Fehler …", "wrong: …".
_MISTAKE_MARK = re.compile(
    r"(?<!\w)(?:falsch\w*|fehler\w*|wrong|incorrect\w*|mistakes?|errors?|faux|fausse|erreurs?|incorrect[oa]s?"
    r"|equivocad[oa]s?)(?!\w)|[✗✘]", re.IGNORECASE)
# The share of a claim's content words one notes sentence must hold to say it literally, and
# to contradict it (said the other way round). A claim needs this many content words to be
# judged at all ("Wolle", "2", "Kein Material" are not claims).
LITERAL_SHARE = 0.75
CONTRADICTED_SHARE = 0.6
CLAIM_MIN_WORDS = 3


def _negated(text: Any) -> bool:
    return any(word in _NEGATION_WORDS
               for word in _WORD.findall(_fold_text(_CONTRACTED_NOT.sub(" not", str(text or "")))))


# A negation negates the next content words of its clause: "ohne Sperrklausel", "keine
# 5-Prozent-Hürde", "nicht bestätigt". It only makes two texts say opposite things when what it
# negates is something both say - not "Wenn der Reichstag keine Mehrheit fand, konnte …" against
# "Er konnte … vorbeiregieren", nor "…, aber nicht so stark wie bei Styropor".
NEGATION_REACH = 1
_CLAUSE = re.compile(r"[,;:.!?()\[\]\n–—]+")


def _negates_shared(text: Any, other: _Support) -> bool:
    """Whether `text` negates a content word that `other` also has: the first content word
    after a negation in its clause ("ohne Zustimmung des Reichstags" negates "Zustimmung", not
    "Reichstag")."""

    for clause in _CLAUSE.split(_fold_text(_CONTRACTED_NOT.sub(" not", str(text or "")))):
        tokens = _WORD.findall(clause)
        for index, token in enumerate(tokens):
            if token not in _NEGATION_WORDS:
                continue
            following = [word for word in tokens[index + 1:]
                         if word not in _NEGATION_WORDS and len(word) >= 3 and not word.isdigit()
                         and word not in _STOP and word not in _SUPPORT_STOP][:NEGATION_REACH]
            if any(other.has(_stem(word)) for word in following):
                return True
    return False


def _claim_words(text: Any, ignore: Iterable[str] = ()) -> set[str]:
    """The content words of a claim as keywords() spells them: no function words, no
    negation, none of `ignore` (a question's own words)."""

    skip = set(ignore)
    return {word for word in keywords(text)
            if word not in _SUPPORT_STOP and word not in _NEGATION_STEMS and word not in skip}


@dataclass(frozen=True)
class NoteSentence:
    text: str
    words: _Support
    negated: bool
    mistake: bool


def notes_sentences(source_text: Any) -> list[NoteSentence]:
    """The sentences of the notes (line breaks joined, abbreviation-safe), each with its words
    and whether it negates or shows a mistake."""

    flat = " ".join(str(source_text or "").split())
    out = []
    for sentence in _sentences(flat):
        if len(sentence) < 8:
            continue
        out.append(NoteSentence(sentence, _Support(sentence), _negated(sentence), bool(_MISTAKE_MARK.search(sentence))))
    return out


def _best_shares(words: set[str], sentences: Sequence[NoteSentence], text: Any
                 ) -> tuple[tuple[float, str], tuple[float, str]]:
    """((best share, sentence) said the same way round, (best share, sentence) said the
    other way round). A sentence that shows a mistake says the other way round what it
    writes: "Falsch: I have seen him yesterday." opposes that sentence, "Fehlerquellen:
    Thermometer nicht gleich tief eingetaucht" supports "immer gleich tief eintauchen"."""

    same: tuple[float, str] = (0.0, "")
    other: tuple[float, str] = (0.0, "")
    claim = _Support(str(text or ""))
    claim_negations = _negated(text)
    for sentence in sentences:
        share = sentence.words.share(words)
        if share <= max(same[0], other[0]) and share < CONTRADICTED_SHARE:
            continue
        negated = claim_negations and _negates_shared(text, sentence.words)
        says_negated = sentence.negated and _negates_shared(sentence.text, claim)
        if (says_negated != sentence.mistake) == negated:
            same = max(same, (share, sentence.text), key=lambda entry: entry[0])
        else:
            other = max(other, (share, sentence.text), key=lambda entry: entry[0])
    return same, other


def _contradicts(text: Any, sentences: Sequence[NoteSentence], *, ignore: Iterable[str] = ()) -> bool:
    """Whether a notes sentence says the opposite of `text`: it holds at least
    CONTRADICTED_SHARE of the text's content words (at least CLAIM_MIN_WORDS), the other
    way round, and no notes sentence says it as well the same way round."""

    words = _claim_words(text, ignore)
    if len(words) < CLAIM_MIN_WORDS or not sentences:
        return False
    same, other = _best_shares(words, sentences, text)
    return other[0] >= CONTRADICTED_SHARE and other[0] > same[0]


def notes_support(text: Any, source: Any, *, ignore: Iterable[str] = ()) -> str:
    """The notes sentence that literally says `text`, or "": at least LITERAL_SHARE of its
    content words (at least CLAIM_MIN_WORDS, `ignore` left out) in one sentence, said the
    same way round (a sentence that shows a mistake says the opposite of what it writes).
    `source` is the notes text or their `notes_sentences`."""

    sentences = source if isinstance(source, list) else notes_sentences(source)
    words = _claim_words(text, ignore)
    if len(words) < CLAIM_MIN_WORDS or not sentences:
        return ""
    same, _other = _best_shares(words, sentences, text)
    return same[1] if same[0] >= LITERAL_SHARE else ""


def notes_contradiction(text: Any, source: Any, *, ignore: Iterable[str] = ()) -> str:
    """The notes sentence that says the opposite of `text` (see `_contradicts`), or ""."""

    sentences = source if isinstance(source, list) else notes_sentences(source)
    words = _claim_words(text, ignore)
    if len(words) < CLAIM_MIN_WORDS or not sentences:
        return ""
    same, other = _best_shares(words, sentences, text)
    return other[1] if other[0] >= CONTRADICTED_SHARE and other[0] > same[0] else ""


def _held_term_items(items: Sequence[Any], page_words: _Support) -> tuple[list[Any], list[Any]]:
    """(own, added) items of a from-notes key-terms part: an item sentence the pages do not
    support (judged like a listed item, SUPPORTED_SHARE) moves into a copy of the item marked
    as added; the item keeps its other sentences, and moves whole when none is left."""

    own: list[Any] = []
    added: list[Any] = []
    for item in items:
        if not isinstance(item, Mapping):
            own.append(item)
            continue
        sentences = _sentences(str(item.get("text") or ""))
        verdicts = [page_words.supports(sentence, strict=True) for sentence in sentences]
        if not any(verdict is False for verdict in verdicts):
            own.append(item)
            continue
        kept = [sentence for sentence, verdict in zip(sentences, verdicts) if verdict is not False]
        moved = [sentence for sentence, verdict in zip(sentences, verdicts) if verdict is False]
        if kept:
            own.append({**item, "text": " ".join(kept)})
            added.append({**item, "text": " ".join(moved)})
        else:
            added.append(dict(item))
    return own, added


def _held_to_pages(part: dict[str, Any], page_words: _Support, *, strict: bool = False,
                   note_sentences: Sequence[NoteSentence] = ()) -> list[dict[str, Any]]:
    """An interpreting part claimed as from the notes: the sentences and items the pages
    do not support move into a copy marked as added (from_notes false), the rest stays.
    A part nothing of which is supported is marked as added as a whole. `strict` judges
    the body's sentences like listed items (SUPPORTED_SHARE): an explaining part states
    claims, it does not retell a scene in its own words. A sentence a notes sentence
    contradicts (`_contradicts`: their words, the other way round) moves too."""

    def unsupported(text: str, *, item_strict: bool) -> bool:
        verdict = page_words.supports(text, strict=item_strict)
        return verdict is False or (verdict is not None and _contradicts(text, note_sentences))

    body = str(part.get("body") or "")
    kept_sentences, added_sentences = [], []
    for sentence in _sentences(body):
        (added_sentences if unsupported(sentence, item_strict=strict) else kept_sentences).append(sentence)
    raw_items = part.get("items")
    items: list[Any] = raw_items if isinstance(raw_items, list) else []
    kept_items, added_items = [], []
    for item in items:
        text = (f"{item.get('label') or ''} {item.get('text') or ''}" if isinstance(item, Mapping)
                else str(item or ""))
        (added_items if unsupported(text, item_strict=True) else kept_items).append(item)
    if not added_sentences and not added_items:
        return [part]
    judged = [text for text in _sentences(body) if page_words.supports(text) is not None]
    judged_items = [item for item in items if page_words.supports(
        f"{item.get('label') or ''} {item.get('text') or ''}" if isinstance(item, Mapping) else str(item or ""))
        is not None]
    if len(added_sentences) == len(judged) and len(added_items) == len(judged_items):
        return [{**part, "from_notes": False}]
    own = {**part, "body": " ".join(kept_sentences), "items": kept_items}
    added = {**part, "body": " ".join(added_sentences), "items": added_items, "from_notes": False,
             "source_pages": [], "answer": ""}
    return [own, added]


# --------------------------------------------------------------------------- page references

def resolve_part_pages(parts: Sequence[Mapping[str, Any]], pages: Sequence[SourcePage], *,
                       numbers: Mapping[int, int], display: Mapping[int, str]) -> list[dict[str, Any]]:
    """Each part with "source_page_ids" and "source_labels" for the page numbers it cites.

    Parts cite page numbers as the prompt labels them ("[file, p. n]"), and a number is a
    page within its file: in a topic built from two files, "p. 1" is two pages. A number
    one page of the lesson has resolves to that page. A number several pages share
    resolves to the one whose text shares the most words with the part; when that does not
    single one out, the label names every candidate ("A p. 1 / B p. 1") and no page id is
    claimed. A number no page of the lesson has is dropped from the labels."""

    by_number: dict[int, list[SourcePage]] = {}
    for page in pages:
        number = numbers.get(page.page_id)
        if number is not None:
            by_number.setdefault(int(number), []).append(page)
    out = []
    for raw in parts:
        part = dict(raw)
        cited = [number for number in (_page(value) for value in part.get("source_pages") or []) if number]
        if not cited:
            out.append(part)
            continue
        words = keywords(parts_text([part]))
        page_ids: list[int] = []
        labels: list[str] = []
        for number in dict.fromkeys(cited):
            candidates = by_number.get(number) or []
            if len(candidates) > 1:
                scores = [len(words & keywords(page.text)) for page in candidates]
                best = max(scores)
                if best > 0 and scores.count(best) == 1:
                    candidates = [candidates[scores.index(best)]]
            if len(candidates) == 1:
                page_ids.append(candidates[0].page_id)
                labels.append(display.get(candidates[0].page_id, ""))
            elif candidates:
                labels.append(" / ".join(display.get(page.page_id, "") for page in candidates))
        part["source_page_ids"] = list(dict.fromkeys(page_ids))
        part["source_labels"] = [label for label in dict.fromkeys(labels) if label]
        out.append(part)
    return out


# --------------------------------------------------------------------------- pictures

# Parts a picture from the student's own page explains best: a diagram, a process, an
# experiment set-up.
VISUAL_KINDS = frozenset({"diagram", "process", "experiment"})
MAX_PART_PICTURES = 2


def attach_pictures(parts: Sequence[Mapping[str, Any]], pictures: Sequence[Mapping[str, Any]], *,
                    limit: int = MAX_PART_PICTURES) -> list[dict[str, Any]]:
    """Each diagram, process or experiment part with the diagrams of the student's own pages
    it cites ("media": [{block_id, page_id, labels}]). Only pictures on a page the part was
    resolved to (resolve_part_pages) are used, so a picture is never shown under a part
    about another page; the caller builds the URLs."""

    out = []
    for raw in parts:
        part = dict(raw)
        cited = part.get("source_page_ids") if isinstance(part.get("source_page_ids"), list) else []
        if part.get("kind") in VISUAL_KINDS and cited:
            chosen = [{"block_id": int(picture["block_id"]), "page_id": int(picture["page_id"]),
                       "labels": str(picture.get("labels") or "")}
                      for picture in pictures if _page(picture.get("page_id")) in cited][:limit]
            if chosen:
                part["media"] = chosen
        out.append(part)
    return out


# --------------------------------------------------------------------------- server parts

def terms_part(terms: Iterable[Mapping[str, Any]], *, title: str, limit: int = MAX_LESSON_CARDS
               ) -> dict[str, Any] | None:
    """A key-terms part built from the topic map's Fachbegriffe found in the notes, for a
    lesson whose plan has a terms step the model left out. Each item says whether its
    definition is the notes' own; the part counts as from the notes only if all are."""

    items = []
    for term in terms:
        if not isinstance(term, Mapping) or not _truthy(term.get("in_notes")):
            continue
        label = _line(term.get("term"), 160)
        text = _line(term.get("simple_definition") or term.get("definition"), 600)
        if label and text:
            items.append({"label": label, "text": text, "from_notes": _truthy(term.get("definition_from_notes"))})
        if len(items) >= limit:
            break
    if not items:
        return None
    return {"kind": "terms", "title": title, "body": "", "items": items, "answer": "",
            "from_notes": all(item["from_notes"] for item in items), "source_pages": []}


def insert_after_objective(parts: Sequence[Mapping[str, Any]], part: Mapping[str, Any]) -> list[dict[str, Any]]:
    parts = [dict(entry) for entry in parts]
    index = next((i for i, entry in enumerate(parts) if entry.get("kind") == "objective"), None)
    position = 0 if index is None else index + 1
    return parts[:position] + [dict(part)] + parts[position:]


# --------------------------------------------------------------------------- flashcards part

def flashcards_part(cards: Sequence[Mapping[str, Any]], *, title: str, set_id: int, study_url: str,
                    limit: int = MAX_LESSON_CARDS) -> dict[str, Any] | None:
    """The server's flashcards part ({"kind": "flashcards", ...}) or None without cards.

    Each item keeps its own origin: a card whose definition Learnova wrote (`from_notes`
    false on the card) is not shown as from the notes because the others are. The part
    counts as from the notes only when every card does."""

    items = []
    for card in cards:
        front, back = _line(card.get("front"), 400), _line(card.get("back"), 600)
        if front and back:
            items.append({"label": front, "text": back, "from_notes": card.get("from_notes") is not False})
        if len(items) >= limit:
            break
    if not items:
        return None
    return {"kind": "flashcards", "title": title, "body": "", "items": items, "answer": "",
            "set_id": int(set_id), "study_url": study_url,
            "from_notes": all(item["from_notes"] for item in items), "source_pages": []}


def insert_after_terms(parts: Sequence[Mapping[str, Any]], part: Mapping[str, Any]) -> list[dict[str, Any]]:
    """`part` right after the key-terms part, else after the first explaining part, else
    after the objective; the flashcards follow the terms they practise."""

    parts = [dict(entry) for entry in parts]
    for kinds in ({"terms"}, EXPLANATION_KINDS, {"objective"}):
        index = next((i for i, entry in enumerate(parts) if entry.get("kind") in kinds), None)
        if index is not None:
            return parts[: index + 1] + [dict(part)] + parts[index + 1:]
    return [dict(part)] + parts


# --------------------------------------------------------------------------- coverage evidence

# Which lesson parts teach an item kind. A formula is rarely "mentioned" word for word in
# LaTeX lesson text, so a rule or worked example citing the formula's page counts too.
ITEM_PARTS: dict[str, frozenset[str]] = {
    "formula": frozenset({"rule", "worked_example"}),
    "rule": frozenset({"rule", "structure"}),
    "method": frozenset({"rule", "process", "worked_example"}),
    "experiment": frozenset({"experiment"}),
    "thought_experiment": frozenset({"thought_experiment"}),
    "theory": frozenset({"theory"}),
    "position": frozenset({"theory", "perspectives", "argument"}),
    "argument": frozenset({"argument", "perspectives"}),
    "person": frozenset({"theory", "timeline", "perspectives"}),
    "event": frozenset({"timeline", "cause_effect"}),
    "date": frozenset({"timeline"}),
    "process": frozenset({"process"}),
    "diagram": frozenset({"diagram"}),
    "source": frozenset({"source_analysis"}),
    "work": frozenset({"source_analysis", "model_analysis", "thought_experiment"}),
    "example": frozenset({"example", "worked_example"}),
    "place": frozenset({"diagram", "example"}),
    "institution": frozenset({"explanation", "perspectives"}),
    "cause_effect": frozenset({"cause_effect", "timeline"}),
    "structure": frozenset({"structure", "rule"}),
}


def parts_text(parts: Sequence[Mapping[str, Any]]) -> str:
    chunks = []
    for part in parts:
        if not isinstance(part, Mapping):
            continue
        chunks.extend([str(part.get("title") or ""), str(part.get("body") or "")])
        for item in part.get("items") or []:
            if isinstance(item, Mapping):
                chunks.extend([str(item.get("label") or ""), str(item.get("text") or "")])
    return "\n".join(chunk for chunk in chunks if chunk)


def taught_evidence(parts: Sequence[Mapping[str, Any]], topic_map: Mapping[str, Any] | None,
                    page_numbers: Mapping[int, int] | None = None) -> tuple[list[str], list[str]]:
    """(term keys, item keys) a lesson taught: the term or item is named in the parts, or
    (items) a part of the matching kind cites the page the item is on.

    `page_numbers` maps the lesson's page ids to the page numbers its parts cite
    (source_pages). Page numbers count inside a file, so in a topic built from two
    documents "p. 1" names two pages: such a number proves nothing about either page, and
    an item on it only counts when the parts name it."""

    topic_map = topic_map if isinstance(topic_map, Mapping) else {}
    text = parts_text([part for part in parts if part.get("kind") != "flashcards"])
    terms = [term for term in _entries(topic_map.get("terms")) if _truthy(term.get("in_notes"))]
    items = [item for item in _entries(topic_map.get("items")) if _truthy(item.get("in_notes"))]
    terms_taught = terms_mentioned(terms, text)
    named = terms_mentioned([{"kind": item.get("kind"), "name": item.get("name")} for item in items], text)
    numbers = page_numbers or {}
    shared = {number for number, count in Counter(numbers.values()).items() if count > 1}
    items_taught = list(named)
    for item in items:
        kind = str(item.get("kind") or "").strip().casefold()
        page_id = _page(item.get("page_id")) or -1
        page = numbers.get(page_id)
        if kind not in ITEM_PARTS:
            continue

        def cites(part: Mapping[str, Any]) -> bool:
            # A part resolved to the lesson's pages (resolve_part_pages) names the page
            # itself; otherwise only a page number no other page of the lesson has counts.
            if isinstance(part.get("source_page_ids"), list):
                return page_id in part["source_page_ids"]
            return page is not None and page not in shared and page in (part.get("source_pages") or [])

        if any(part.get("kind") in ITEM_PARTS[kind] and cites(part) for part in parts):
            key = item_key(kind, str(item.get("name") or ""))
            if key not in items_taught:
                items_taught.append(key)
    return terms_taught, items_taught


def updated_coverage(coverage: Mapping[str, Any] | None, *, terms_taught: Iterable[str],
                     items_taught: Iterable[str], taught_at: str) -> dict[str, Any]:
    """learning_section.coverage_json after one more lesson: counts and unions only grow."""

    record = dict(coverage) if isinstance(coverage, Mapping) else {}
    try:
        started = int(record.get("lessons_started") or 0)
    except (TypeError, ValueError):
        started = 0
    record["lessons_started"] = started + 1
    for name, new in (("terms_taught", terms_taught), ("items_taught", items_taught)):
        stored = record.get(name)
        old = stored if isinstance(stored, list) else []
        record[name] = list(dict.fromkeys([str(key) for key in old] + [str(key) for key in new if key]))
    record["last_taught_at"] = taught_at
    return record


# --------------------------------------------------------------------------- try again

def _answer_texts(question: Mapping[str, Any]) -> list[str]:
    expected = question.get("expected_answer")
    options = {str(option.get("id")): str(option.get("label") or "")
               for option in question.get("options") or [] if isinstance(option, Mapping)}
    values = expected if isinstance(expected, list) else [expected]
    texts = []
    for value in values:
        # An expected value that is no option id ("Option a", the label itself) is
        # checked as written, or nothing could ever count as giving it away.
        text = options.get(str(value), "") if options else str(value or "")
        if not text:
            text = str(value or "")
        if text.strip():
            texts.append(text)
    return texts


_CHOICE_TYPES = frozenset({"multiple_choice", "dropdown", "checkboxes"})
# "a ist richtig", "Antwort (a) is correct", "richtig ist a", "the right answer is a." -
# in the student's language, folded like _fold_text (ä -> ae). Inflected forms count too:
# "Die richtige Antwort ist b." passed the uninflected list (live, 2026-11-01).
_CORRECT_WORDS = r"(?:richtig\w*|korrekt\w*|correct\w*|right|juste|bonne|exacte?|correct[ao]s?)"
# Review B B-3: the plural ("a und c sind richtig", "both a and c are correct") and "lautet".
_IS_WORDS = r"(?:ist|is|waere|war|was|est|es|sera|serait|seria|sería|sind|are|were|sont|son|lautet|lauten)"
_OPTION_WORDS = (r"(?:antwort|option|answer|loesung|lösung|wahl|reponse|réponse|respuesta|choice|opcion|opción"
                 r"|solution|solucion|solución)")
# "…, also Option b.", "Daher a." - a conclusion that names the id.
_SO_WORDS = r"(?:also|daher|damit|deshalb|therefore|so|thus|donc|por\s+tanto)"
# What may follow an id that is named as the answer: the end of the sentence, or a clause
# ("b, weil …", "a because …"). An id followed by another word ("a statement") is a word.
_ID_END = (r"(?=\s*(?:[.,;:!?)\]]|$)|\s+(?:weil|denn|da|because|since|as|car|parce|porque|und|and|et|y|"
           r"sondern|but|aber|mais|pero)(?!\w))")
_NEGATIONS = r"(?:nicht|not|kein\w*|pas|no|falsch\w*|wrong|incorrect\w*|faux|fausse)"
# Markup a model puts around an answer ("Option **b**", "`a`", "$2$"), and quotation marks.
_MARKUP = re.compile(r"[*_`$]+")
_QUOTE_MARKS = re.compile(r"[„“”\"'‚‘’«»]")
_BRACKETED_ID = re.compile(r"[(\[{]\s*([0-9A-Za-z])\s*[)\]}]")
_NOT_A_WORD = re.compile(r"[^\w\s=]+")
_ARTICLES = frozenset({"der", "die", "das", "den", "dem", "des", "ein", "eine", "the", "a", "an", "le", "la", "les",
                       "l", "el", "los", "las", "un", "une", "uno", "una"})


def _unmarked(text: Any) -> str:
    """`text` folded, without markdown emphasis, code marks, "$", quotation marks and the
    brackets around a single id. Sentence punctuation stays: the id rules need it."""

    value = _QUOTE_MARKS.sub("", _MARKUP.sub("", str(text or "")))
    return _fold_text(_BRACKETED_ID.sub(r" \1 ", value))


def _plain(text: Any) -> str:
    """`_unmarked`, then every punctuation mark except "=" as a space: for comparing labels
    ('Die richtige Form ist "I saw him yesterday".' holds the label "I saw him yesterday.")."""

    return " ".join(_NOT_A_WORD.sub(" ", _unmarked(text)).split())


def _label_core(label: Any) -> str:
    """A label without its leading articles ("Die Idee des Guten" -> "idee des guten")."""

    words = _plain(label).split()
    while len(words) > 1 and words[0] in _ARTICLES:
        words = words[1:]
    return " ".join(words)


def _correct_ids(question: Mapping[str, Any]) -> list[str]:
    ids = {str(option.get("id")) for option in question.get("options") or [] if isinstance(option, Mapping)}
    expected = question.get("expected_answer")
    return [str(value) for value in (expected if isinstance(expected, list) else [expected])
            if str(value) in ids and str(value).strip()]


def _says_correct_option(unmarked: str, question: Mapping[str, Any]) -> bool:
    """The id rules beyond `_names_correct_option`, on `_unmarked` text: "Die richtige Antwort
    ist b.", "… richtige Steigung 2, also Option **b**.", "Option b ist die richtige"."""

    if str(question.get("type") or "") not in _CHOICE_TYPES:
        return False
    for option_id in _correct_ids(question):
        token = re.escape(_fold_text(option_id))
        patterns = (
            rf"(?<!\w){_CORRECT_WORDS}\s+(?:[\w-]+\s+){{0,4}}?(?:{_OPTION_WORDS}\s+)?{token}{_ID_END}",
            rf"(?<!\w){_SO_WORDS}\s+(?:{_OPTION_WORDS}\s*)?{token}{_ID_END}",
            rf"(?<!\w){_OPTION_WORDS}\s+{token}\s+(?:(?!{_NEGATIONS}(?!\w))\w+\s+){{0,2}}{_CORRECT_WORDS}(?!\w)",
        )
        if any(re.search(pattern, unmarked) for pattern in patterns):
            return True
    return False


# A short numeric answer is stated, not just mentioned, when one of these comes within four
# words before it in the same sentence ("ist 2", "= 2", "die richtige Steigung 2").
_STATING_WORDS = frozenset({"=", "ist", "is", "betraegt", "lautet", "gleich", "equals",
                            # review B B-3: "Ergebnis 2", "Das Ergebnis: 2"
                            "ergebnis", "result", "resultat", "résultat", "resultado"})
_STATING_WINDOW = 4
# The number is an operand, a coordinate or a numbered step, not a result.
_OPERAND_BEFORE = re.compile(r"(?:[-+*/^·×÷|{(\[−]|[\d)]\s*:)\s*$")
_OPERAND_AFTER = re.compile(r"^\s*[-+*/^·×÷|}\])−:]")
_NUMBERED_BEFORE = re.compile(r"(?<!\w)(?:schritt|step|aufgabe|nr|seite|page|teil|zeile|line|klasse|grade|beispiel"
                              r"|exercise|task|punkt|kapitel|chapter|option|antwort)\.?\s*$", re.IGNORECASE)
_NEGATED_AFTER = re.compile(r"^\s*(?:(?:ist|is|waere|wäre|was|war|are|sind)\s+)?(?:(?:leider|aber|jedoch|also)\s+)?"
                            rf"{_NEGATIONS}(?!\w)", re.IGNORECASE)


# "2 ist richtig", "2 is the right one" - the number followed by a correctness word.
_CORRECT_AFTER = re.compile(rf"^\s*{_IS_WORDS}\s+(?:(?!{_NEGATIONS}(?!\w))\w+\s+){{0,2}}?{_CORRECT_WORDS}(?!\w)")
# "Die Steigung: 2" - a label and a colon right before the number.
_LABEL_COLON = re.compile(r"[^\W\d_]\s*:\s*$")


def _states_number(text: str, number: str) -> bool:
    """Whether `text` states the short numeric answer `number`: "Die Steigung ist 2.",
    "$$m=\\frac{8}{4}=2$$", "2 ist richtig.", "Die Steigung: 2." - but not "(1|2)", "5-2",
    "Schritt 2" or "2 ist falsch"."""

    raw = str(text or "")
    if not number:
        return False
    for match in re.finditer(rf"(?<![\w.,]){re.escape(number)}(?![\w]|[.,]\d)", raw):
        before, after = raw[:match.start()], raw[match.end():]
        if _OPERAND_BEFORE.search(before) or _OPERAND_AFTER.match(after) or _NUMBERED_BEFORE.search(before):
            continue
        sentence = re.split(r"[.!?;](?:\s|$)", before)[-1]
        window = re.findall(r"=|[^\W_]+", _fold_text(_MARKUP.sub(" ", sentence)))[-_STATING_WINDOW:]
        stated = (any(word in _STATING_WORDS or re.fullmatch(_CORRECT_WORDS, word) for word in window)
                  or bool(_LABEL_COLON.search(_MARKUP.sub(" ", sentence)))
                  or bool(_CORRECT_AFTER.match(_fold_text(after))))
        if not stated or _NEGATED_AFTER.match(after):
            continue
        return True
    return False


def _names_correct_option(folded: str, question: Mapping[str, Any]) -> bool:
    """Whether `folded` names a correct option by its id next to a correctness word.

    The student answers with an option id ("b"), so a grader writes "Nicht b, sondern a ist
    richtig"; the label rule never sees that (an id is one character)."""

    if str(question.get("type") or "") not in _CHOICE_TYPES:
        return False
    correct = _correct_ids(question)
    for option_id in correct:
        token = re.escape(_fold_text(option_id))
        before = (rf"(?<![\w]){_OPTION_WORDS}?\s*\(?{token}\)?\s*{_IS_WORDS}\s+(?:\w+\s+)?"
                  rf"{_CORRECT_WORDS}(?![\w])")
        after = (rf"(?<![\w]){_CORRECT_WORDS}\s+(?:{_OPTION_WORDS}\s+)?(?:{_IS_WORDS}\s+)?(?:\w+\s+)?"
                 rf"\(?{token}\)?(?=\s*(?:[.,;:!)]|$))")
        if re.search(before, folded) or re.search(after, folded):
            return True
    return False


def _states_the_order(folded: str, question: Mapping[str, Any]) -> bool:
    """Whether `folded` lists the expected order of an ordering task: every item, or at
    least three consecutive ones, named in that order. Two items in a row are no order."""

    labels = [_fold_text(text) for text in _answer_texts(question)]
    labels = [label for label in labels if len(label) >= 4]
    if len(labels) < 2:
        return False
    run = min(3, len(labels))
    for start in range(len(labels) - run + 1):
        position = -1
        for label in labels[start:start + run]:
            position = folded.find(label, position + 1)
            if position < 0:
                break
        else:
            return True
    return False


# --- review B B-3 (2026-11-02, prodrev_b/probes/adversarial_reveal.py): phrasings that named the
# answer without a correctness word next to the id, listed several ids, gave an order by ids,
# or stated a short answer the rules above do not read.
_ID = r"[a-z0-9]"
# One option id or a list of them: "b", "a und c", "a, c", "a/c".
_ID_LIST = rf"{_ID}(?:\s*(?:,|/|&|\+|\bund\b|\band\b|\bet\b|\by\b|\bsowie\b)\s*{_ID})*"
_DETERMINERS = r"(?:die|der|das|den|the|la|le|el|les|los|las|l)"
_CHOOSE_WORDS = (r"(?:waehle|waehlt|nimm|kreuze|choose|pick|select|choisis|choisissez|choisir|elige|elegir|escoge"
                 r"|escoger)")
_NAMED_IDS = tuple(re.compile(pattern) for pattern in (
    # "Die Antwort ist b.", "The answer is b.", "La respuesta es b.", "Antwort: b", "Die Lösung lautet b."
    rf"(?<!\w){_OPTION_WORDS}(?:en|s)?\s*(?::|{_IS_WORDS}(?!\w))\s*(?:(?:{_DETERMINERS}|option|wahl)\s+)*"
    rf"(?P<ids>{_ID_LIST}){_ID_END}",
    # "C'est la réponse b.", "Es ist b.", "It is b." (quotation marks are gone: "cest")
    rf"(?<!\w)(?:cest|ce\s+est|es\s+ist|das\s+ist|it\s+is|its|this\s+is|es)\s+(?:{_DETERMINERS}\s+)?"
    rf"(?:{_OPTION_WORDS}\s+)?(?P<ids>{_ID_LIST}){_ID_END}",
    # "a und c sind richtig.", "Both a and c are correct.", "Antwort b ist die richtige."
    rf"(?<!\w)(?:beide\s+|both\s+)?(?:{_OPTION_WORDS}(?:en|s)?\s+)?(?P<ids>{_ID_LIST})\s+{_IS_WORDS}\s+"
    rf"(?:(?!{_NEGATIONS}(?!\w))\w+\s+){{0,2}}?{_CORRECT_WORDS}(?!\w)",
    # "b stimmt.", "Die Antworten a und c stimmen." - not "b stimmt nicht".
    rf"(?<!\w)(?:{_OPTION_WORDS}(?:en|s)?\s+)?(?P<ids>{_ID_LIST})\s+(?:stimmt|stimmen|passt|passen)(?!\w)"
    rf"(?!\s+(?:\w+\s+)?(?:nicht|kein\w*)(?!\w))",
    # "Correct: b", "Richtig: a, c"
    rf"(?<!\w){_CORRECT_WORDS}\s*:\s*(?:{_OPTION_WORDS}\s+)?(?P<ids>{_ID_LIST}){_ID_END}",
    # "Wähle b.", "Il fallait choisir b.", "Debiste elegir la b."
    rf"(?<!\w){_CHOOSE_WORDS}\s+(?:(?:{_DETERMINERS}|{_OPTION_WORDS})\s+)*(?P<ids>{_ID_LIST}){_ID_END}",
    # "Du hättest b wählen sollen."
    rf"(?<!\w)(?P<ids>{_ID_LIST})\s+(?:waehlen|nehmen|ankreuzen|gewaehlt|genommen|angekreuzt)(?!\w)",
    # "→ b"
    rf"(?:→|->|=>)\s*(?:{_OPTION_WORDS}\s+)?(?P<ids>{_ID_LIST}){_ID_END}",
))


def _names_correct_ids(unmarked: str, question: Mapping[str, Any]) -> bool:
    """A correct option's id named as the answer by `_NAMED_IDS` (choice questions, on
    `_unmarked` text): one correct id of a multiple-choice answer gives it away."""

    if str(question.get("type") or "") not in _CHOICE_TYPES:
        return False
    correct = {_fold_text(option_id) for option_id in _correct_ids(question)}
    if not correct:
        return False
    for pattern in _NAMED_IDS:
        for match in pattern.finditer(unmarked):
            if correct.intersection(re.findall(rf"(?<![a-z0-9]){_ID}(?![a-z0-9])", match.group("ids"))):
                return True
    return False


# Ids in a row: "b, a, c, d", "b – a – c – d", "b a c d", "b, dann a, dann c".
_ID_SEQUENCE = re.compile(
    rf"(?<!\w){_ID}(?:(?:\s*[,;/>→–—-]\s*|\s+)(?:(?:dann|danach|then|und|and|puis|luego)\s+)?{_ID})+(?!\w)")


def _states_id_order(unmarked: str, question: Mapping[str, Any]) -> bool:
    """Whether an ordering task's expected order is given by its ids: all of them, or at
    least three consecutive ones, written in a row in that order."""

    expected = question.get("expected_answer")
    if not isinstance(expected, list):
        return False
    ids = [_fold_text(value) for value in expected]
    if len(ids) < 2 or any(len(value) != 1 for value in ids):
        return False
    run = min(3, len(ids))
    wanted = {tuple(ids[start:start + run]) for start in range(len(ids) - run + 1)}
    for match in _ID_SEQUENCE.finditer(unmarked):
        sequence = re.findall(rf"(?<![a-z0-9]){_ID}(?![a-z0-9])", match.group(0))
        if any(tuple(sequence[start:start + run]) in wanted for start in range(len(sequence) - run + 1)):
            return True
    return False


_BLANK = re.compile(r"_{2,}|\.{3,}|…")
_CLAUSE_BREAK = re.compile(r"[.!?:;\"„“”«»\n]")


def _fills_the_blank(text: str, question: Mapping[str, Any]) -> bool:
    """Whether `text` repeats the prompt's sentence with its gap filled by the answer
    ("I saw him yesterday." for "I ___ him yesterday."): the answer plus up to two words on
    either side of the gap, at least three words in all."""

    prompt = re.sub(r"\([^()]*\)", " ", str(question.get("prompt") or ""))   # "(see)": the verb to use
    gap = _BLANK.search(prompt)
    if gap is None:
        return False
    before = _plain(_CLAUSE_BREAK.split(prompt[:gap.start()])[-1]).split()[-2:]
    after = _plain(_CLAUSE_BREAK.split(prompt[gap.end():])[0]).split()[:2]
    if not before and not after:
        return False
    plain = f" {_plain(text)} "
    for answer in _answer_texts(question):
        filled = " ".join([*before, _plain(answer), *after])
        if len(filled.split()) >= 3 and f" {filled} " in plain:
            return True
    return False


_ANY_QUOTE = "[„“”\"'‚‘’«»]"


def _states_short_label(text: str, label: str) -> bool:
    """A correct label of two or three letters ("saw") stated as the answer: next to a
    correctness word ("Richtig wäre saw."), in quotation marks, or alone after a colon at the
    end of a clause ("Simple Past: saw.")."""

    core = _plain(label)
    if not 2 <= len(core) <= 3 or " " in core:
        return False
    token = re.escape(core)
    unmarked = _unmarked(text)
    if re.search(rf"{_ANY_QUOTE}\s*{re.escape(_fold_text(label))}\s*{_ANY_QUOTE}", _fold_text(text)):
        return True
    return any(re.search(pattern, unmarked) for pattern in (
        rf"(?<!\w){_CORRECT_WORDS}\s*:?\s+(?:{_IS_WORDS}\s+)?(?:[\w-]+\s+){{0,2}}?{token}{_ID_END}",
        rf"(?<!\w){token}\s+{_IS_WORDS}\s+(?:(?!{_NEGATIONS}(?!\w))\w+\s+){{0,2}}?{_CORRECT_WORDS}(?!\w)",
        rf"[^\W\d_]\s*:\s*{token}\s*(?:[.!]|$)",
    ))


# Minus signs and dashes in front of a number are a minus ("−3", "–0,5"); a model writes either.
_MINUS_SIGNS = re.compile(r"[−‐‑‒–—](?=\d)")


def _normal_signs(text: Any) -> str:
    return _MINUS_SIGNS.sub("-", str(text or ""))


def _number_variants(answer: str) -> list[str]:
    """The answer, and for a decimal number its other spelling ("-0,5" and "-0.5")."""

    if re.fullmatch(r"\s*[-+]?\d+[.,]\d+\s*", answer):
        other = answer.replace(",", ".") if "," in answer else answer.replace(".", ",")
        return [answer, other]
    return [answer]


_OPERATOR_SPACE = re.compile(r"\s*([=+\-*/^:·×÷<>≤≥≈])\s*")


def _compact(text: str) -> str:
    """Folded text without the spaces around operators: "x = 3" == "x=3"."""

    return _OPERATOR_SPACE.sub(r"\1", _fold_text(text))


def _has_token(text: str, token: str) -> bool:
    """`token` in `text` as a whole: "42" is in "ist 42." but not in "420" or "4,2"."""

    return re.search(rf"(?<![\w.,]){re.escape(token)}(?![\w]|[.,]\d)", text) is not None


def reveals_answer(text: Any, question: Mapping[str, Any]) -> bool:
    """True when `text` contains the correct answer of `question` (every correct option's
    label, or the expected written answer).

    Answers of four characters or more count wherever they occur. A shorter answer counts
    only when it holds a digit ("42", "x=3", the usual answer of a calculation) and
    appears as a whole token that the question itself does not already show; one
    character ("3", "a") is not checked, it occurs in any feedback - except a correct
    option's id next to a correctness word ("a ist richtig"). An ordering task's answer is
    its order: the items named in that order (all, or three in a row) give it away.

    Hardened after the live run of 2026-11-01, where every leak passed: markdown, code marks
    and quotation marks are ignored ("Option **b**"); an id also counts after an inflected
    correctness word or a conclusion ("Die richtige Antwort ist b.", "…, also Option b."); a
    label counts without its article and its closing punctuation ("Sonne = Idee des Guten"
    for "Die Idee des Guten"); and in a choice question a one- to three-character numeric
    label counts when the text states it ("Die Steigung ist 2.", "= 2"), never as an
    operand, a coordinate, a step number or "2 ist falsch".

    Hardened again after review B (2026-11-02, B-3): an id named as the answer without a
    correctness word ("Die Antwort ist b.", "La respuesta es b.", "Wähle b.", "b stimmt."),
    several ids ("a und c sind richtig"), an order given by ids ("b, a, c, d"), a written
    answer of one to three digits that the text states, minus signs and decimal commas
    ("−3", "-0.5" for "-0,5"), a label of two or three letters stated as the answer
    ("Richtig wäre saw.") and the prompt's gap filled with the answer ("I saw him
    yesterday." for "I ___ him yesterday.")."""

    text = _normal_signs(text)
    folded = _fold_text(text)
    unmarked = _unmarked(text)
    if str(question.get("type") or "") == "ordering":
        # Every item is in the prompt anyway; the answer is their order.
        return _states_the_order(folded, question) or _states_id_order(unmarked, question)
    if (_names_correct_option(folded, question) or _names_correct_option(unmarked, question)
            or _says_correct_option(unmarked, question) or _names_correct_ids(unmarked, question)
            or _fills_the_blank(text, question)):
        return True
    compact = _compact(text)
    plain = _plain(text)
    prompt = _compact(_normal_signs(question.get("prompt")))
    choice = str(question.get("type") or "") in _CHOICE_TYPES
    found: list[bool] = []
    for answer in _answer_texts(question):
        results = [result for result in (
            _answer_shown(_normal_signs(variant), text, folded=folded, compact=compact, plain=plain, prompt=prompt,
                          choice=choice)
            for variant in _number_variants(answer)) if result is not None]
        if results:
            found.append(any(results))
    return bool(found) and all(found)


def _answer_shown(answer: str, text: str, *, folded: str, compact: str, plain: str, prompt: str,
                  choice: bool) -> bool | None:
    """Whether `text` shows one correct answer (label or written answer); None when an answer
    this short is not judged (a one-letter word occurs in any feedback)."""

    long_form = _fold_text(answer)
    short = _compact(answer)
    if len(long_form) >= 4:
        full, core = _plain(answer), _label_core(answer)
        return (long_form in folded or short in compact
                or (len(full) >= 4 and full in plain) or (len(core) >= 4 and core in plain))
    numeric = any(char.isdigit() for char in short)
    checked, hit = False, False
    if len(short) >= 2 and numeric and not _has_token(prompt, short):
        # Also on the spaced text: compacting glues "gleich -3" into "gleich-3".
        checked, hit = True, _has_token(compact, short) or _has_token(folded, short)
    if choice and numeric and 1 <= len(short) <= 3:
        checked, hit = True, hit or _states_number(text, "".join(answer.split()))
    elif numeric and 1 <= len(short) <= 3 and _states_number(text, "".join(answer.split())):
        checked, hit = True, True           # a written answer of one to three digits, stated
    if not numeric and _states_short_label(text, answer):
        checked, hit = True, True
    return hit if checked else None


# The paraphrase guard: the words of a correct answer that the question does not already
# use, and the share of them a retry text may repeat before it gives the answer away.
PARAPHRASE_MIN_WORDS = 3
PARAPHRASE_SHARE = 0.6


def retry_reveals(text: Any, question: Mapping[str, Any], field: str = "") -> bool:
    """`reveals_answer`, plus a paraphrase guard for the texts of a "Try again" screen.

    A reteach that retells the correct option in other words gives it away as surely as
    quoting it (History, live 2026-11-01: "Art. 48 erlaubte dem Präsidenten, ohne
    Zustimmung des Reichstags zu regieren" for "Es ermöglichte dem Reichspräsidenten, ohne
    Zustimmung des Reichstags zu regieren"). Counted on content words, compounds included:
    the answer's words that the question itself does not use (at least three of them), of
    which the text repeats at least 60 %. An answer of exactly two such words counts when
    the text has both (review B B-3: "Die Sonne ist die Idee vom Guten." for "Die Idee des
    Guten"; measured on the 361 live texts of 2026-11-01 without a single new hit).

    In a single-answer choice question one word is enough when no other option and not the
    question uses it (`_distinctive_words`, pronouns such as "niemand" aside) - PF_FIX2 F3, live
    2026-11-02. A reteach or a simpler example (`field`) is about another case, so the right
    option's own word gives it away ("…, wenn der Klassenlehrer ohne Zustimmung der Schüler
    entscheiden darf" for "…, ohne Zustimmung des Reichstags zu regieren"); a misunderstanding
    names what the student thought and may use the topic's words, so there the word counts
    after a contrast ("du dachtest, der Boden sinkt, aber er hebt sich an" for "Der Meeresboden
    hebt sich an …")."""

    if reveals_answer(text, question):
        return True
    if not str(text or "").strip() or str(question.get("type") or "") == "ordering":
        return False
    distinctive = _distinctive_words(question)
    if distinctive:
        said = _Support(_after_contrast(text) if field == "misunderstanding" else str(text))
        if any(said.has(word) for word in distinctive):
            return True
    asked = keywords(question.get("prompt"))
    support: _Support | None = None
    for answer in _answer_texts(question):
        wanted = keywords(answer) - asked
        if len(wanted) < 2:
            continue
        support = support or _Support(str(text))
        needed = PARAPHRASE_SHARE if len(wanted) >= PARAPHRASE_MIN_WORDS else 1.0
        if support.share(wanted) >= needed:
            return True
    return False


QUOTE_MIN_WORDS = 6


def quotes_source(text: Any, source: Any, *, min_words: int = QUOTE_MIN_WORDS) -> bool:
    """Whether `text` quotes `source`: at least `min_words` consecutive words of it, in order
    (folded, punctuation ignored). A grader that quotes the student's notes for an answer
    marks a key that the notes may contradict (L2)."""

    source_words = _plain(source).split()
    text_words = _plain(text).split()
    if len(source_words) < min_words or len(text_words) < min_words:
        return False
    grams = {tuple(source_words[index:index + min_words]) for index in range(len(source_words) - min_words + 1)}
    return any(tuple(text_words[index:index + min_words]) in grams
               for index in range(len(text_words) - min_words + 1))


# --------------------------------------------------------------------------- questions held to the notes (PF_FIX2)

DISTINCTIVE_MIN_LETTERS = 4


def _option_labels(question: Mapping[str, Any]) -> dict[str, str]:
    return {str(option.get("id")): str(option.get("label") or "")
            for option in question.get("options") or [] if isinstance(option, Mapping) and option.get("id") is not None}


# Pronouns and quantifiers: no option owns them ("Stell dir vor, niemand sieht dich" is no
# answer to "Niemand ist freiwillig gerecht").
_GENERIC_WORDS = frozenset(_stem(word) for word in (
    "niemand", "jemand", "jemanden", "alle", "alles", "allen", "jeder", "jede", "jedes", "jeden", "jedem", "viele",
    "wenige", "etwas", "andere", "anderen", "nobody", "someone", "somebody", "everyone", "everybody", "everything",
    "nothing", "something", "others", "personne", "quelqu", "tout", "tous", "toutes", "autres", "nadie", "alguien",
    "todos", "todas", "todo", "nada", "otros"))
# What follows these in a sentence is what the text says instead: "…, aber er hebt sich an".
_CONTRAST = re.compile(r"(?<!\w)(?:aber|sondern|stattdessen|statt|vielmehr|tatsaechlich|wirklichkeit|jedoch|doch|but|"
                       r"instead|actually|rather|whereas|mais|plutot|realite|pero|sino|realidad)(?!\w)")


def _after_contrast(text: Any) -> str:
    """The parts of `text` that follow a contrast word in their sentence."""

    pieces = []
    for sentence in re.split(r"(?<=[.!?;])\s+", _fold_text(text)):
        match = _CONTRAST.search(sentence)
        if match:
            pieces.append(sentence[match.end():])
    return " ".join(pieces)


def _distinctive_words(question: Mapping[str, Any]) -> set[str]:
    """The content words of a single-answer choice question's correct option that neither its
    prompt nor any other option uses (inflections and compounds counted as in `_Support`). A
    checkbox question is left to the paraphrase guard: one word of one of its right answers
    ("Es kommt auf die Folgen an") does not give the whole answer away."""

    if str(question.get("type") or "") not in _SINGLE_CHOICE:
        return set()
    labels = _option_labels(question)
    correct = set(_correct_ids(question))
    if not correct or len(labels) < 2 or correct >= set(labels):
        return set()
    others = _Support(" ".join([str(question.get("prompt") or "")]
                               + [label for option_id, label in labels.items() if option_id not in correct]))
    words: set[str] = set()
    for option_id in correct:
        words |= _claim_words(labels.get(option_id, ""))
    return {word for word in words
            if len(word) >= DISTINCTIVE_MIN_LETTERS and word not in _GENERIC_WORDS and not others.has(word)}


# Choice questions whose options are example sentences or word forms, not claims: an option
# the notes print ("Richtig: I saw him yesterday.") is an example of a rule there, not the
# answer to "Which sentence uses the present perfect?".
EXAMPLE_OPTION_STRATEGIES = frozenset({"languages", "latin"})
_SINGLE_CHOICE = frozenset({"multiple_choice", "dropdown"})


def choice_key_conflict(question: Mapping[str, Any], source: Any, *, strategy_key: str = "") -> dict[str, str] | None:
    """Why a choice question built from the notes must not be asked as it is, or None.

    {"reason": "key_contradicted", "option": id, "sentence": …} when a notes sentence says the
    opposite of a correct option and none says it; {"reason": "distractor_supported", …} when
    a notes sentence literally says a wrong option while none says a correct one (PF_FIX2 F2:
    "if a sentence of the source supports an option, that option is correct"). Only options
    that state a claim (CLAIM_MIN_WORDS content words besides the question's own) are judged;
    language subjects' example sentences are not."""

    if str(question.get("type") or "") not in _CHOICE_TYPES or strategy_key in EXAMPLE_OPTION_STRATEGIES:
        return None
    labels = _option_labels(question)
    correct = set(_correct_ids(question))
    sentences = source if isinstance(source, list) else notes_sentences(source)
    if not correct or len(labels) < 2 or not sentences:
        return None
    asked = keywords(question.get("prompt"))
    supported = {option_id: notes_support(label, sentences, ignore=asked) for option_id, label in labels.items()}
    for option_id in sorted(correct):
        against = notes_contradiction(labels.get(option_id, ""), sentences, ignore=asked)
        if against and not supported.get(option_id):
            return {"reason": "key_contradicted", "option": option_id, "sentence": against}
    if not any(supported.get(option_id) for option_id in correct):
        for option_id, label in labels.items():
            if option_id not in correct and supported.get(option_id):
                return {"reason": "distractor_supported", "option": option_id, "sentence": supported[option_id]}
    return None


def _chosen_ids(answer: Any, labels: Mapping[str, str]) -> list[str]:
    values = answer if isinstance(answer, list) else re.split(r"[,;]", str(answer or ""))
    folded = {_plain(label): option_id for option_id, label in labels.items()}
    chosen = []
    for value in values:
        text = str(value or "").strip()
        option_id = text if text in labels else folded.get(_plain(text), "")
        if option_id:
            chosen.append(option_id)
    return list(dict.fromkeys(chosen))


def chosen_option_support(question: Mapping[str, Any], answer: Any, source: Any, *,
                          strategy_key: str = "") -> str:
    """The notes sentence that literally says the option the student chose, when that option
    is not the key (single-answer choice questions), or "". Such an answer is not wrong
    against the student's own notes (PF_FIX2 F2: History graded "Es schränkte die Grundrechte
    ein" 0 points although the source says so); the sentence is shown with the grade."""

    if str(question.get("type") or "") not in _SINGLE_CHOICE or strategy_key in EXAMPLE_OPTION_STRATEGIES:
        return ""
    labels = _option_labels(question)
    chosen = _chosen_ids(answer, labels)
    correct = set(_correct_ids(question))
    if len(chosen) != 1 or chosen[0] in correct:
        return ""
    label = labels.get(chosen[0], "")
    asked = keywords(question.get("prompt"))
    sentences = source if isinstance(source, list) else notes_sentences(source)
    supported = notes_support(label, sentences, ignore=asked)
    if supported:
        return supported
    # A quotation of the notes in the option, however short its other words (QUOTE_MIN_WORDS in a row).
    for sentence in sentences:
        if quotes_source(label, sentence.text):
            return sentence.text
    return ""


# The first question repeats the guided exercise when it asks for what the exercise asks:
# this share of its content words are the exercise's, and it brings no number of its own.
REPEAT_SHARE = 0.7
REPEAT_MIN_WORDS = 3
_NUMBER_TOKEN = re.compile(r"\d+(?:[.,]\d+)?")


def repeats_exercise(question: Mapping[str, Any], parts: Sequence[Mapping[str, Any]]) -> bool:
    """Whether `question` asks what a guided exercise of the lesson already asked and
    answered (PF_FIX2 F6, live 2026-11-02: "Warum gilt Art. 48 … als Schwachstelle?" after the
    exercise "Erkläre, warum Art. 48 als Schwachstelle der Verfassung gilt." and its model
    answer). A question with other numbers (other points, other values) is a new task."""

    prompt = str(question.get("prompt") or "")
    words = {word for word in keywords(prompt) if word not in _SUPPORT_STOP}
    if len(words) < REPEAT_MIN_WORDS:
        return False
    numbers = set(_NUMBER_TOKEN.findall(_normal_signs(prompt)))
    for part in parts:
        if not isinstance(part, Mapping) or part.get("kind") != "guided_exercise":
            continue
        task = f"{part.get('title') or ''} {part.get('body') or ''}"
        if numbers and not numbers <= set(_NUMBER_TOKEN.findall(_normal_signs(task))):
            continue
        if _Support(task).share(words) >= REPEAT_SHARE:
            return True
    return False


TERM_QUESTION_OPTIONS = 4
TERM_QUESTION_MIN_OPTIONS = 3


def term_question(terms: Any, *, prompt: Any, concept: str, question_id: str = "q1",
                  avoid: Iterable[str] = (), source: Any = "") -> dict[str, Any] | None:
    """A multiple-choice question from the topic map's Fachbegriffe found in the notes: which
    definition fits a term, the other options other terms' definitions - the definitions the
    lesson's key-terms part teaches. A definition the notes give themselves comes first; one
    the analysis wrote counts only when the pages support it (SUPPORTED_SHARE of its words,
    given `source`). For a first question that cannot be asked as the model wrote it; None
    with fewer than TERM_QUESTION_MIN_OPTIONS such terms. `prompt(name)` writes the question
    in the lesson's language; terms named in `avoid` (folded) are not asked."""

    skip = {_fold_text(name) for name in avoid}
    pages = _Support(str(source)) if str(source or "").strip() else None
    ranked: list[tuple[bool, str, str]] = []
    seen: set[str] = set()
    for term in _entries(terms):
        if not _truthy(term.get("in_notes")):
            continue
        own = _truthy(term.get("definition_from_notes"))
        name = _line(term.get("term"), 120)
        text = _line(term.get("definition") or term.get("simple_definition"), 240)
        key = _fold_text(text)
        if not name or not text or key in seen or _fold_text(name) in key:
            continue
        if not own and (pages is None or pages.share(_claim_words(text)) < SUPPORTED_SHARE):
            continue
        seen.add(key)
        ranked.append((not own, name, text))
    usable = [(name, text) for _, name, text in sorted(ranked, key=lambda entry: entry[0])]
    if len(usable) < TERM_QUESTION_MIN_OPTIONS:
        return None
    asked = next((entry for entry in usable if _fold_text(entry[0]) not in skip), usable[0])
    distractors = [text for name, text in usable if name != asked[0]][:TERM_QUESTION_OPTIONS - 1]
    position = sum(map(ord, asked[0])) % (len(distractors) + 1)
    labels = distractors[:position] + [asked[1]] + distractors[position:]
    ids = "abcdefgh"
    return {"id": question_id, "concept": concept, "difficulty": 1, "type": "multiple_choice",
            "prompt": prompt(asked[0]), "hint": "",
            "options": [{"id": ids[index], "label": label} for index, label in enumerate(labels)],
            "expected_answer": ids[position]}

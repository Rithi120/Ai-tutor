"""Understanding the student's question before answering it - shared by every AI mode.

Flask-free and deterministic; nothing here calls a model. Four parts:

* **Rules** (`UNDERSTANDING_RULE`, `SOURCE_TERMS_RULE`, `TRANSLATION_RULE`, `READING_RULE`):
  standing instructions. The gateway's output contract appends the right one to every
  task that answers, teaches, grades, translates or reads (`ai_services.prompts.
  output_contract`), so a new caller of those tasks cannot forget them.
* **`find_focus`**: which word, phrase or pair of terms a message asks about, and the
  sentence the student supplied with it - read from the message's own words, never guessed.
* **`focus_block`**: a short per-turn block that names that exact term for the model.
* **`check_reply`**: whether a reply kept that term or silently explained a similar-looking
  one instead. The caller then asks once more with `correction_note` - never more than
  once, and only when the check fails, so a correct answer costs nothing extra.

Why: live on the chat model (2026-11-15) "Was bedeutet Marketingsog?" was answered as
"Was bedeutet ein Marketing-Slogan?" - an unfamiliar compound swapped for a familiar word
that shares its first ten letters. Nothing in the prompts said to keep the student's term,
and nothing checked the reply. No word is special-cased here: the check compares the
student's own term with the reply's words.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

# --------------------------------------------------------------------------- rules
# ~150 tokens; sent with the chat tasks (assistant_chat, tutor_chat), where a student asks
# in their own words. The two pairs are illustrations of "similar is not the same", not a
# list the model is limited to.
UNDERSTANDING_RULE = """UNDERSTANDING THE QUESTION
- Find exactly which word, phrase or concept the student asks about and answer that one, in their spelling. Never swap an unfamiliar word for a similar-looking or more common one: one letter more or less is a different word.
- Unfamiliar is not a typo. Read a compound from its parts (in German the last part says what it is) and from its sentence. Call it a typo only when no reading makes sense, and say which word you assume.
- Explain a word as it is used in the sentence or text the student gives. Earlier messages help with follow-ups but never change what a new question asks.
- Similar expressions are different words (effektiv/effizient, scheinbar/anscheinend); name the difference only where it could confuse.
- Never invent a meaning or an acronym. If a word has several common meanings and nothing decides, give them briefly or ask one short question."""

# ~45 tokens; sent with every structured task that writes or grades learning content.
SOURCE_TERMS_RULE = (
    "Use every term exactly as the student or the source material writes it; never replace a word with a "
    "similar-looking or more common one. An unfamiliar but valid word, compound or phrase is not a mistake.")

# ~40 tokens; sent with translation.
TRANSLATION_RULE = (
    "Translate the meaning, never the look of a word: a similar-looking word in the other language is not a "
    "translation. Keep names, rare words and compounds whose meaning you cannot determine unchanged.")

# ~45 tokens; sent with the photo and page readers.
READING_RULE = (
    "A clearly written word you do not recognise is read exactly as written: rare words and long compounds are "
    "not errors. Use the sentence around an unclear word only to choose between readings its letters allow.")

# Task type -> the rule its output contract carries.
CHAT_TASKS = frozenset({"assistant_chat", "tutor_chat"})
CONTENT_TASKS = frozenset({
    "lesson_generation", "quiz_generation", "adaptive_practice", "question_generation", "answer_evaluation",
    "final_exam_generation", "final_exam_evaluation", "flashcard_generation", "flashcard_back_suggestion",
    "project_section_generation"})
READING_TASKS = frozenset({"image_understanding", "ocr_document_recognition", "vocabulary_page_extraction"})


def contract_rule(task_type: str) -> str:
    """The understanding rule a task's output contract carries ("" for none)."""

    if task_type in CHAT_TASKS:
        return UNDERSTANDING_RULE
    if task_type in CONTENT_TASKS:
        return SOURCE_TERMS_RULE
    if task_type == "translation":
        return TRANSLATION_RULE
    if task_type in READING_TASKS:
        return READING_RULE
    return ""


# --------------------------------------------------------------------------- folding
def fold(text: str) -> str:
    """Casefolded, accents and umlauts reduced to their base letter, ß as ss."""

    value = unicodedata.normalize("NFKD", str(text or "").casefold().replace("ß", "ss"))
    return "".join(char for char in value if not unicodedata.combining(char))


_JOINERS = re.compile(r"[­‐‑‒–—\-_/*`]+")
_WORD = re.compile(r"[^\W\d_]+(?:[-‐‑][^\W\d_]+)*")


def _compact(text: str) -> str:
    """Folded, with hyphens, markdown marks and soft hyphens taken out (Marketing-Sog = MarketingSog)."""

    return _JOINERS.sub("", fold(text))


def _words(text: str) -> list[str]:
    return [_JOINERS.sub("", word) for word in _WORD.findall(fold(text))]


# Function words that never carry the meaning of a phrase (all supported languages).
_STOP = frozenset("""
der die das den dem des ein eine einen einem einer eines und oder aber auch noch schon sich etwas ist sind war
wird werden kann konnen muss mussen soll hat haben mit von vom zum zur fur auf aus bei nach uber unter vor
kaum sehr ganz nicht kein keine wie was wer wo hier dort dann denn doch mal man es er sie wir ihr du ich
the a an and or of to in on at for with is are was be been by as that this it its very not
le la les un une des du de et ou est sont dans pour avec sur que qui ce cette
el los las una unos y o es son en con por para del al que
""".split())

# Endings an inflected form may add or drop (German, English, French, Spanish).
_ENDINGS = ("ern", "en", "er", "es", "em", "e", "n", "s", "ed", "ing", "ly")


def _stem(word: str) -> str:
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 5:
            return word[: -len(ending)]
    return word


# --------------------------------------------------------------------------- focus
KINDS = ("meaning", "difference", "concept")


@dataclass(frozen=True)
class QuestionFocus:
    """What one message asks about. Empty (falsy) when it names no term."""

    kind: str = ""                      # meaning | difference | concept | ""
    terms: tuple[str, ...] = ()         # exactly as the student wrote them
    context: str = ""                   # the sentence the student supplied, if any

    def __bool__(self) -> bool:
        return bool(self.terms)


_QUOTED = re.compile(r"[„“\"«»‚]\s*([^„“”\"«»‚‘’]{1,600}?)\s*[“”\"«»‘’]|(?:(?<=\s)|^)'([^'\n]{1,80})'")
_PLACEHOLDER = re.compile(r"⟦(\d+)⟧")

# Ends a term: punctuation, a line break, or a phrase that points at the context.
_TERM_END = re.compile(
    r"\s*(?:[?!;:\n(]|,|\.(?:\s|$)|\s(?:in|aus)\s+(?:diesem|dem|meinem|folgendem|folgenden)\s+(?:satz|text|zusammenhang|kontext|absatz)"
    r"|\sim\s+(?:satz|text|zusammenhang|kontext)|\shier\b|\sgenau\b|\seigentlich\b|\sin\s+this\s+(?:sentence|text|context)|\shere\b"
    r"|\sdans\s+(?:cette|la)\s+phrase|\sici\b|\sen\s+esta\s+frase|\saquí\b|\sund\s+wie\b|\sand\s+how\b)",
    re.IGNORECASE)
_LEADING = re.compile(
    r"^(?:(?:eigentlich|genau|denn|hier|da|nochmal|nochmals|bitte|mal|überhaupt|ueberhaupt|exactly|actually|here)\s+)+",
    re.IGNORECASE)
_LABEL = re.compile(
    r"^(?:(?:das|dieses|den)\s+(?:deutsche\s+|englische\s+)?(?:wort|verb|adjektiv|nomen|substantiv|adverb)"
    r"|(?:der|den|dieser|diesen)\s+(?:begriff|ausdruck|fachbegriff)"
    r"|(?:die|diese)\s+(?:redewendung|phrase|wendung|formulierung)"
    r"|the\s+(?:german\s+|english\s+|french\s+|spanish\s+)?(?:word|term|phrase|expression|idiom)"
    r"|(?:le|ce)\s+mot(?:\s+(?:allemand|anglais|espagnol))?|l['’]expression|la\s+palabra|el\s+t[ée]rmino"
    r"|la\s+expresi[óo]n)\s+", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:ein|eine|einen|einem|einer|der|die|das|den|dem|the|a|an|le|la|les|un|une|el|los|las|una|l['’])\s+",
                      re.IGNORECASE)
_POINTERS = frozenset({"das", "dies", "es", "das hier", "this", "that", "it", "ça", "ca", "cela", "esto", "eso",
                       "das da", "das wort", "dieses wort", "this word", "ce mot", "esta palabra"})

_MEANING = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bwas\s+(?:bedeu\w*|hei(?:ß|ss)t|meint)\s+(?P<t>.+)",
    r"\bwas\s+versteht\s+man\s+unter\s+(?P<t>.+)",
    r"\bwas\s+meint\s+man\s+mit\s+(?P<t>.+)",
    r"\bwas\s+(?:ist|sind)\s+mit\s+(?P<t>.+?)\s+gemeint",
    r"\bbedeutung\s+(?:von|des\s+wortes|des\s+begriffs|des\s+ausdrucks)\s+(?P<t>.+)",
    r"\b(?:definition|definiere|definier)\s+(?:von\s+|für\s+)?(?P<t>.+)",
    r"\berkl[äa]r\w*\s+(?:mir\s+)?(?:bitte\s+)?(?:mal\s+)?(?:kurz\s+)?(?P<t>(?:das|dieses|den|die)\s+(?:wort|begriff|ausdruck|redewendung|fachbegriff)\s+.+)",
    r"\bwhat\s+(?:does|do)\s+(?P<t>.+?)\s+mean\b",
    r"\bwhat\s+is\s+(?:the\s+)?meaning\s+of\s+(?P<t>.+)",
    r"\bwhat\s+is\s+meant\s+by\s+(?P<t>.+)",
    r"\b(?:the\s+)?meaning\s+of\s+(?P<t>.+)",
    r"\bdefine\s+(?P<t>.+)",
    r"\bque\s+(?:veut\s+dire|signifie)\s+(?P<t>.+)",
    r"\bqu['’]est-ce\s+que\s+(?:veut\s+dire|signifie)\s+(?P<t>.+)",
    r"\b(?:le\s+)?sens\s+(?:de|du|des)\s+(?P<t>.+)",
    r"^(?P<t>.+?)\s+(?:veut\s+dire|signifie)\s+quoi",
    r"\bqu[eé]\s+(?:significa|quiere\s+decir)\s+(?P<t>.+)",
    r"\bsignificado\s+de\s+(?P<t>.+)",
))
_DIFFERENCE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bunterschied(?:e)?\s+zwischen\s+(?P<a>.+?)\s+und\s+(?P<b>.+)",
    r"\bdifference\s+between\s+(?P<a>.+?)\s+and\s+(?P<b>.+)",
    r"\bdiff[ée]rence\s+entre\s+(?P<a>.+?)\s+et\s+(?P<b>.+)",
    r"\bdiferencia\s+entre\s+(?P<a>.+?)\s+y\s+(?P<b>.+)",
))
_DIFFERENCE_TO = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bunterschied\s+(?:zu|zur|zum)\s+(?P<b>.+)",
    r"\bdifference\s+(?:to|from)\s+(?P<b>.+)",
    r"\bdiff[ée]rence\s+avec\s+(?P<b>.+)",
    r"\bdiferencia\s+con\s+(?P<b>.+)",
))
_CONCEPT = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"^\W*(?:und\s+)?was\s+(?:ist|sind)\s+(?P<t>.+)",
    r"^\W*(?:and\s+)?what\s+(?:is|are)\s+(?P<t>.+)",
    r"^\W*qu['’]est-ce\s+qu['e]\s*(?:c['’]est\s+qu['e]\s*)?(?P<t>.+)",
    r"^\W*qu[eé]\s+(?:es|son)\s+(?P<t>.+)",
))

MAX_TERM_WORDS = 8
MAX_TERM_CHARACTERS = 80
MAX_CONTEXT_CHARACTERS = 400


def _mask_quotes(text: str) -> tuple[str, list[str]]:
    quoted: list[str] = []

    def keep(match: re.Match[str]) -> str:
        quoted.append((match.group(1) or match.group(2) or "").strip())
        return f"⟦{len(quoted) - 1}⟧"

    return _QUOTED.sub(keep, text), quoted


def _clean_term(raw: str, quoted: Sequence[str]) -> str:
    text = " ".join(str(raw or "").split())
    text = _LEADING.sub("", text)
    end = _TERM_END.search(text)
    if end:
        text = text[: end.start()]
    text = _LEADING.sub("", text.strip())
    for _ in range(2):
        text = _LABEL.sub("", text)
        text = _ARTICLE.sub("", text)
    placeholder = _PLACEHOLDER.search(text)
    if placeholder:
        # A quoted term ("das Wort „Sog“") is the term; anything around it is framing.
        text = quoted[int(placeholder.group(1))] if int(placeholder.group(1)) < len(quoted) else ""
    text = text.strip(" .,;:!?\"'„“”«»‚‘’()[]")
    if (not text or len(text) > MAX_TERM_CHARACTERS or len(text.split()) > MAX_TERM_WORDS
            or not re.search(r"[^\W\d_]", text) or text.casefold() in _POINTERS):
        return ""
    return text


def _context_for(terms: Sequence[str], quoted: Sequence[str], text: str) -> str:
    """The sentence the student supplied that contains the term: a quoted passage, else
    the text after "in diesem Satz:" / "in this sentence:"."""

    stems = [_stem(word) for term in terms for word in _words(term) if len(word) >= 3 and word not in _STOP]

    def score(passage: str) -> int:
        """How many of the term's words the passage holds, as whole words (inflections count):
        "Spielereihe" does not hold "Reihe"."""

        words = _words(passage)
        return sum(1 for stem in stems if any(word.startswith(stem) for word in words))

    passages = [item for item in quoted if len(item.split()) >= 4]
    after = re.search(r"(?:satz|text|sentence|phrase|frase)\s*:\s*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if after and len(after.group(1).split()) >= 4:
        passages.append(_PLACEHOLDER.sub(lambda m: quoted[int(m.group(1))], after.group(1)))
    for passage in passages:
        if score(passage):
            sentences = re.split(r"(?<=[.!?…])\s+", " ".join(passage.split()))
            hit = max(sentences, key=score) if sentences else passage
            return hit[:MAX_CONTEXT_CHARACTERS]
    return ""


def _last_question(masked: str) -> str:
    """The part of a long message that holds its question: a paragraph of text often comes
    first, the question last."""

    pieces = [piece for piece in re.split(r"(?<=[.!?])\s+|\n+", masked) if piece.strip()]
    for piece in reversed(pieces):
        if "?" in piece or any(pattern.search(piece) for pattern in (*_MEANING, *_DIFFERENCE, *_DIFFERENCE_TO)):
            return piece
    return pieces[-1] if pieces else masked


def find_focus(text: str, previous: Iterable[str] = ()) -> QuestionFocus:
    """The term(s) a student's message asks about, exactly as written.

    `previous` is the student's earlier messages, oldest first: "Und was ist der
    Unterschied zu Entwicklung?" takes its first term from the last one that named one.
    Only explicit word questions count (what does X mean, difference between X and Y, and
    "what is X" for a single word); everything else returns an empty focus, which changes
    nothing for the caller."""

    original = str(text or "")[:4000]
    masked, quoted = _mask_quotes(original)
    question = _last_question(masked)
    for pattern in _DIFFERENCE:
        match = pattern.search(question)
        if match:
            first, second = _clean_term(match.group("a"), quoted), _clean_term(match.group("b"), quoted)
            if first and second:
                return QuestionFocus("difference", (first, second), _context_for((first, second), quoted, masked))
    for pattern in _DIFFERENCE_TO:
        match = pattern.search(question)
        if match:
            second = _clean_term(match.group("b"), quoted)
            earlier = next((focus for focus in (find_focus(item) for item in reversed(list(previous))) if focus), None)
            if second and earlier:
                return QuestionFocus("difference", (earlier.terms[0], second), "")
    for pattern in _MEANING:
        match = pattern.search(question)
        if match:
            term = _clean_term(match.group("t"), quoted)
            if term:
                return QuestionFocus("meaning", (term,), _context_for((term,), quoted, masked))
    for pattern in _CONCEPT:
        match = pattern.search(question.strip())
        if match:
            term = _clean_term(match.group("t"), quoted)
            # Only a single word: "Was ist die Ursache dafür, dass ..." is a question about
            # the world, not about a word, and must not be checked as one.
            if term and " " not in term and len(term) >= 4:
                return QuestionFocus("concept", (term,), _context_for((term,), quoted, masked))
    return QuestionFocus()


def card_focus(front: str) -> QuestionFocus:
    """A flashcard front as the term to define (at most MAX_TERM_WORDS words)."""

    term = " ".join(str(front or "").split())
    if not term or len(term) > MAX_TERM_CHARACTERS or len(term.split()) > MAX_TERM_WORDS:
        return QuestionFocus()
    return QuestionFocus("meaning", (term,))


# --------------------------------------------------------------------------- block
def _quote(term: str) -> str:
    return f"«{term}»"


def focus_block(focus: QuestionFocus, *, mode: str = "answer") -> str:
    """The per-turn QUESTION FOCUS block (English, like every instruction; the reply stays in
    its own language). `mode`: "answer" (the chat), "tutor" (the lesson's tutor chat: the
    lesson's subject decides a technical reading), "lesson" (a Short Lesson's goal), "card"
    (a flashcard back). "" for an empty focus."""

    if not focus:
        return ""
    lines = ["QUESTION FOCUS (found by Learnova in the student's message; the student did not write this block)"]
    if focus.kind == "difference":
        first, second = focus.terms[:2]
        lines.append(f"The student asks how {_quote(first)} and {_quote(second)} differ. Explain both, exactly these "
                     "two, side by side: what each one is, the one feature that tells them apart, and one example each.")
    else:
        term = focus.terms[0]
        lines.append(f"The student asks about {_quote(term)}. Explain exactly {_quote(term)}, spelled as written - "
                     "not a similar-looking or more common word.")
        if " " not in term and len(term) >= 9:
            lines.append("If it is unfamiliar it may still be a real word: read a compound from its parts and the "
                         "sentence around it before deciding it is a typo; if you assume a typo, say which word you mean.")
        elif " " in term:
            lines.append("It is a phrase: explain what the whole expression means where people commonly use it "
                         "(news, books, films, games, everyday talk), not word by word.")
    if focus.context:
        lines.append(f"The sentence the student gave: \"{focus.context}\" - explain the meaning in that sentence.")
    elif focus.kind in ("meaning", "concept"):
        lines.append("No sentence given: take the everyday meaning unless a school subject in this conversation "
                     "clearly needs a technical one; if a word has several common meanings, name them briefly.")
    if mode == "answer":
        lines.append("Earlier topics of this conversation do not change what this question asks.")
        if focus.kind == "difference":
            lines.append("No exercise or check question unless the student asks for one.")
        if focus.kind in ("meaning", "concept"):
            lines.append("Shape, with short headings in the reply language: Meaning (a clear, direct definition); "
                         "In context (only when a sentence was given); Example (one simple one); Difference (only "
                         "when a similar expression could be confused). No exercise or check question unless the "
                         "student asks for one.")
    elif mode == "tutor":
        lines.append("When it is a term of this lesson, explain it the way the lesson uses it.")
    elif mode == "lesson":
        lines.append("The lesson teaches exactly this term: its title and explanation name it as written.")
    elif mode == "card":
        lines.append("Every suggestion defines exactly this term.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- check
@dataclass(frozen=True)
class ReplyCheck:
    """The result of `check_reply`. `ok` is False only when a term was neither kept nor
    plausibly corrected for a typo."""

    ok: bool = True
    missing: tuple[str, ...] = ()        # terms the reply never names
    substitutes: tuple[str, ...] = ()    # similar-looking words it explained instead
    assumed_typo: bool = False           # a term only appears corrected by one letter


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        diff = [index for index, (a, b) in enumerate(zip(left, right)) if a != b]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and left[diff[0]] == right[diff[1]] and left[diff[1]] == right[diff[0]])
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    for index in range(len(longer)):
        if longer[:index] + longer[index + 1:] == shorter:
            return True
    return False


def _common_prefix(left: str, right: str) -> int:
    size = 0
    for a, b in zip(left, right):
        if a != b:
            break
        size += 1
    return size


def _key_words(term: str) -> list[str]:
    """The words of a term that must show in a reply: a single word itself; for a phrase its
    two longest content words."""

    words = _words(term)
    if len(words) <= 1:
        return words
    content = sorted({word for word in words if len(word) >= 4 and word not in _STOP}, key=len, reverse=True)
    return content[:2] or words[:1]


def _present(term: str, compact_reply: str, joined_reply: str) -> bool:
    keys = _key_words(term)
    if not keys:
        return True
    if len(_words(term)) > 1:
        # A phrase is kept when one of its two longest content words is (inflected forms count).
        return any(_stem(key) in compact_reply for key in keys)
    key = keys[0]
    return key in compact_reply or key in joined_reply or (_stem(key) != key and _stem(key) in compact_reply)


def check_reply(focus: QuestionFocus, reply: str) -> ReplyCheck:
    """Did `reply` answer about the student's term(s)? Deterministic and cheap.

    A term counts as kept when the reply names it (case, accents, hyphens and inflection
    endings do not matter; a phrase needs one of its two longest content words). A missing
    single word is accepted when the reply names a word one letter away - the student's
    typo, corrected (`assumed_typo`). Anything else fails, with the similar-looking words
    the reply used instead (same first letters, not an inflection of the term)."""

    if not focus or not str(reply or "").strip():
        return ReplyCheck()
    compact_reply = _compact(reply)
    joined_reply = re.sub(r"\s+", "", compact_reply)
    reply_words = set(_words(reply))
    missing: list[str] = []
    substitutes: list[str] = []
    typo = False
    for term in focus.terms:
        if _present(term, compact_reply, joined_reply):
            continue
        keys = _key_words(term)
        if len(keys) == 1 and len(_words(term)) == 1 and len(keys[0]) >= 5 and any(
                _edit_distance_at_most_one(keys[0], word) for word in reply_words):
            typo = True
            continue
        missing.append(term)
        for key in keys:
            least = max(4, int(len(key) * 0.6 + 0.5))
            for word in sorted(reply_words):
                if word != key and _common_prefix(word, key) >= least and not word.startswith(_stem(key)):
                    substitutes.append(word)
    unique = tuple(dict.fromkeys(substitutes))[:3]
    return ReplyCheck(ok=not missing, missing=tuple(missing), substitutes=unique, assumed_typo=typo)


def correction_note(focus: QuestionFocus, check: ReplyCheck) -> str:
    """The hidden note for the single second attempt after a failed check."""

    asked = " and ".join(_quote(term) for term in (check.missing or focus.terms))
    said = ", ".join(_quote(word) for word in check.substitutes)
    lines = ["ANSWER CHECK (from Learnova, not from the student; never mention it)"]
    if said:
        lines.append(f"A first draft explained {said} instead of {asked}. Those are different words.")
    else:
        lines.append(f"A first draft did not explain {asked}.")
    lines.append(f"Answer the student's question about {asked}, spelled exactly so: read it from its parts and from "
                 "the sentence it stands in. Only if no reading makes sense, say which word you think was meant, "
                 "or ask one short question.")
    return "\n".join(lines)


__all__ = [
    "CHAT_TASKS", "CONTENT_TASKS", "KINDS", "READING_RULE", "READING_TASKS", "SOURCE_TERMS_RULE",
    "TRANSLATION_RULE", "UNDERSTANDING_RULE", "QuestionFocus", "ReplyCheck", "card_focus", "check_reply",
    "contract_rule", "correction_note", "find_focus", "focus_block", "fold",
]

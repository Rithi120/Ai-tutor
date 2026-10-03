"""Stage B/C/F: deterministic text preprocessing, signals and obfuscation analysis.

Flask-free and dependency-free. Everything here is a pure function over a string, so
every rule can be unit-tested without a request context, a database or a provider call.

Three principles decide what this module is allowed to do:

1. **The original is never modified.** `analyze()` returns the normalized and folded
   forms *alongside* the input; the caller stores the original. Nothing here writes.
2. **A signal is not a verdict.** Nothing in this module rejects anything. A detector
   raises `obfuscation_risk`, contributes evidence, and can force escalation to a human
   or to a stronger model - `learnova.moderation.policy` makes every decision. A
   flashcard set *about* prompt injection in a computer-science course is legitimate
   content that trips the injection detector, which is exactly why the detector may not
   decide.
3. **Nothing extracted is ever executed or trusted.** Decoded text is treated as opaque
   data: bounded in length, stripped of controls, and passed on only as more text to
   read. It is never evaluated, never rendered, and never given instruction authority.

Cost is bounded on purpose. Every scan is a single pass or a bounded regex over a
truncated string, so moderation latency stays dominated by the provider call rather than
by preprocessing.
"""

from __future__ import annotations

import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field


# Hard caps. Preprocessing must stay cheap even for a maximum-size submission.
MAX_ANALYZED_CHARACTERS = 40_000
MAX_DECODED_CHARACTERS = 400
MAX_EVIDENCE_ITEMS = 12
MAX_EVIDENCE_LENGTH = 120

# Risk bands for the obfuscation indicator. Calibrated against the labelled corpus in
# tests/fixtures/moderation/cases.json: "medium" is the point where a human should look,
# not the point where anything is presumed wrong.
RISK_REVIEW_THRESHOLD = 0.25
RISK_HIGH_THRESHOLD = 0.55

# Characters with no legitimate purpose in learner-submitted study text: zero-width
# joiners and spaces, bidirectional overrides, and the byte-order mark. Their presence is
# the single strongest obfuscation signal because typing them is not an accident.
INVISIBLE_CHARACTERS = frozenset(
    "​‌‍‎‏⁠⁡⁢⁣⁤"
    "‪‫‬‭‮⁦⁧⁨⁩﻿­᠎"
)

# Confusables that survive NFKC. NFKC already folds fullwidth, mathematical and most
# compatibility forms, so this map only needs the scripts it leaves alone: Cyrillic and
# Greek letters that render as Latin ones.
CONFUSABLES = {
    # Cyrillic
    "а": "a", "А": "A", "е": "e", "Е": "E", "о": "o",
    "О": "O", "р": "p", "Р": "P", "с": "c", "С": "C",
    "у": "y", "У": "Y", "х": "x", "Х": "X", "і": "i",
    "І": "I", "ј": "j", "Ј": "J", "н": "H", "Н": "H",
    "к": "k", "К": "K", "м": "m", "М": "M", "т": "T",
    "В": "B", "в": "b", "г": "r", "һ": "h", "ѕ": "s",
    "Ѕ": "S", "ґ": "r", "ӏ": "l",
    # Greek
    "α": "a", "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z",
    "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N",
    "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    "ο": "o", "ρ": "p", "υ": "u", "ι": "i", "κ": "k",
    "ν": "v", "τ": "t",
    # Other lookalikes
    "ı": "i", "ł": "l", "ǀ": "l", "⁄": "/", "∕": "/",
}

# Digit-and-symbol substitutions used to spell a word past a literal match. Applied only
# when building the aggressive comparison form, never to text a human will read.
LEET = {
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g",
    "@": "a", "$": "s", "!": "i", "|": "l", "+": "t", "(": "c", "€": "e", "£": "l",
}

# Separators someone inserts between letters to break up a word ("s.e.x", "s-e-x").
SEPARATORS = frozenset(" .,-_*~^`'\"/\\|:;!?()[]{}<>+=#%&")

# A run of substitution characters sandwiched between two letters, which is the only
# position where leet spelling is distinguishable from ordinary punctuation.
_LEET_RUN = re.compile(r"(?<=[a-z])([0-9@$!|+(€£]+)(?=[a-z])")

# Runs that look like an encoded payload. Bounded deliberately: an unbounded scan over a
# long document is exactly the expensive check this module is supposed to avoid.
BASE64_RUN = re.compile(r"[A-Za-z0-9+/]{24,512}={0,2}")
HEX_RUN = re.compile(r"(?:[0-9a-fA-F]{2}[\s:]?){12,256}")

# Text that addresses the reviewing system rather than a learner. Detected so the content
# can be quarantined as untrusted data and escalated - never to reject on its own, since
# a computer-science set may legitimately teach these exact phrases.
INJECTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier)\b", re.I),
    re.compile(r"\bdisregard\s+(?:all\s+)?(?:the\s+)?(?:previous|prior|above|earlier|rules?)\b", re.I),
    re.compile(r"\b(?:system|developer|assistant)\s*(?::|prompt\b|message\b)", re.I),
    re.compile(r"\byou\s+are\s+now\s+(?:a|an|in)\b", re.I),
    re.compile(r"\b(?:new|updated|revised)\s+instructions?\b", re.I),
    re.compile(r"\b(?:approve|allow|publish)\s+this\s+(?:content|set|submission|immediately)\b", re.I),
    re.compile(r"\bmoderation\s+(?:override|bypass|off|disabled)\b", re.I),
    re.compile(r"\bend\s+of\s+(?:prompt|instructions?|context)\b", re.I),
    re.compile(r"</?(?:system|instructions?|prompt)>", re.I),
    # German equivalents: the platform moderates de content with the same rules.
    re.compile(r"\bignoriere\s+(?:alle\s+)?(?:vorherigen|obigen)\b", re.I),
    re.compile(r"\bneue\s+anweisungen\b", re.I),
)

# Contact and identity patterns. Reliable enough to act on because the consequence is a
# revision request, never a rejection, so a false positive costs an author one edit.
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
URL = re.compile(r"\b(?:https?://|www\.)\S{4,}", re.I)
HANDLE = re.compile(r"(?:^|\s)@[A-Za-z][\w.]{2,29}\b")
PHONE = re.compile(r"(?<!\d)(?:\+\d{1,3}[\s./-]?)?(?:\(?\d{2,4}\)?[\s./-]?){2,4}\d{2,4}(?!\d)")
MESSAGING = re.compile(
    r"\b(?:whatsapp|telegram|snapchat|insta(?:gram)?|discord|tiktok|signal|kik)\b"
    r"(?:\s*(?:me|mich|:|@|is|ist))?", re.I)
LONG_DIGITS = re.compile(r"(?<!\d)\d{9,}(?!\d)")

REPEATED_CHARACTER = re.compile(r"(\w)\1{4,}")
EXCESSIVE_PUNCTUATION = re.compile(r"([!?.,*#~])\1{3,}")
SPACED_LETTERS = re.compile(r"\b(?:[A-Za-z]\s+){3,}[A-Za-z]\b")
WHITESPACE_RUN = re.compile(r"[ \t ]{6,}|\n{5,}")
WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


@dataclass(frozen=True)
class TextSignals:
    """Everything the deterministic layer could establish about one piece of text.

    `original` is untouched. `normalized` is the readable NFKC form given to the
    classifier. `folded` is an aggressive comparison-only form: it is never shown to a
    person, never stored as content, and exists so a literal check sees the same string
    whether it was written plainly or spelled around.
    """

    original: str
    normalized: str
    folded: str
    truncated: bool = False
    invisible_characters: int = 0
    confusable_characters: int = 0
    mixed_script_words: int = 0
    scripts: tuple[str, ...] = ()
    repeated_character_runs: int = 0
    excessive_punctuation_runs: int = 0
    spaced_letter_runs: int = 0
    excessive_whitespace_runs: int = 0
    leet_words: int = 0
    encoded_runs: int = 0
    revealed_text: str = ""
    acrostic: str = ""
    injection_markers: int = 0
    contact_markers: tuple[str, ...] = ()
    obfuscation_risk: float = 0.0
    evidence: tuple[str, ...] = ()

    @property
    def risk_level(self) -> str:
        """Band the numeric risk so the policy can read it without re-deriving thresholds."""

        if self.obfuscation_risk >= RISK_HIGH_THRESHOLD:
            return "high"
        if self.obfuscation_risk >= RISK_REVIEW_THRESHOLD:
            return "medium"
        return "low"

    @property
    def normalization_changed_meaning(self) -> bool:
        """True when normalization revealed text that the raw form concealed.

        This is the Stage F "suspicious content that appears only after normalization"
        check: the reader saw one thing and the stored bytes said another.
        """

        return bool(self.invisible_characters or self.confusable_characters or self.revealed_text)

    def as_dict(self) -> dict[str, object]:
        """Compact, privacy-safe summary for the stored record and the prompt.

        Deliberately omits `original`, `normalized` and `folded`: a moderation record
        keeps signals and short quotes, not another copy of the submission.
        """

        return {
            "truncated": self.truncated,
            "invisible_characters": self.invisible_characters,
            "confusable_characters": self.confusable_characters,
            "mixed_script_words": self.mixed_script_words,
            "scripts": list(self.scripts),
            "repeated_character_runs": self.repeated_character_runs,
            "excessive_punctuation_runs": self.excessive_punctuation_runs,
            "spaced_letter_runs": self.spaced_letter_runs,
            "excessive_whitespace_runs": self.excessive_whitespace_runs,
            "leet_words": self.leet_words,
            "encoded_runs": self.encoded_runs,
            "has_revealed_text": bool(self.revealed_text),
            "acrostic": self.acrostic,
            "injection_markers": self.injection_markers,
            "contact_markers": list(self.contact_markers),
            "obfuscation_risk": self.obfuscation_risk,
            "risk_level": self.risk_level,
            "evidence": list(self.evidence),
        }


def _script_of(character: str) -> str:
    """Coarse script name for one letter. Cheap by design: a name prefix lookup."""

    try:
        name = unicodedata.name(character)
    except ValueError:
        return "unknown"
    for script in ("LATIN", "CYRILLIC", "GREEK", "ARABIC", "HEBREW", "HAN", "HIRAGANA",
                   "KATAKANA", "HANGUL", "DEVANAGARI", "THAI", "ARMENIAN", "GEORGIAN"):
        if name.startswith(script):
            return script.casefold()
    return "other"


def normalize_text(value: str) -> str:
    """The readable form: NFKC, invisible characters removed, whitespace tidied.

    Safe to show to a person and safe to send to the classifier. It preserves wording,
    punctuation, case and line structure - it is not the aggressive comparison form.
    """

    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(character for character in text if character not in INVISIBLE_CHARACTERS)
    # Drop control characters except tab and newline, which carry real layout.
    text = "".join(
        character for character in text
        if character in "\t\n" or unicodedata.category(character)[0] != "C"
    )
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fold_text(value: str) -> str:
    """The comparison-only form. Never displayed, never stored as content.

    Lowercases, strips diacritics, maps confusables and leet substitutions to Latin,
    removes separators, and collapses repeated letters, so that a literal check matches
    the same string however it was spelled around.
    """

    text = unicodedata.normalize("NFKD", normalize_text(value).casefold())
    text = "".join(character for character in text if not unicodedata.combining(character))
    text = "".join(CONFUSABLES.get(character, character) for character in text)
    # Leet substitution applies only *between* letters. Applied to every character it
    # would rewrite ordinary punctuation - every "!" becoming an "i" - so a quote copied
    # faithfully from the content would stop matching it, and a correct safety flag would
    # be discarded as ungrounded.
    text = _LEET_RUN.sub(lambda match: "".join(
        LEET.get(character, character) for character in match.group(1)), text)
    text = "".join(character for character in text if character not in SEPARATORS)
    return re.sub(r"(.)\1{2,}", r"\1\1", text)


def _decode_runs(text: str) -> tuple[int, str]:
    """Count encoded-looking runs and recover their text, bounded and inert.

    Recovered text is data for a later read, never an instruction and never executed.
    Only printable results are kept: a run that decodes to binary is counted as a run and
    otherwise discarded.
    """

    revealed: list[str] = []
    runs = 0
    for match in list(BASE64_RUN.finditer(text))[:8]:
        candidate = match.group(0)
        if len(candidate) % 4:
            candidate = candidate[: len(candidate) - len(candidate) % 4]
        if len(candidate) < 24:
            continue
        runs += 1
        try:
            decoded = base64.b64decode(candidate, validate=True).decode("utf-8", "strict")
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        printable = "".join(character for character in decoded if character.isprintable())
        # A decode is only interesting if it is mostly readable text rather than noise.
        if len(printable) >= 8 and len(printable) >= 0.8 * len(decoded):
            revealed.append(printable)
    for match in list(HEX_RUN.finditer(text))[:4]:
        compact = re.sub(r"[\s:]", "", match.group(0))
        if len(compact) < 24 or len(compact) % 2:
            continue
        runs += 1
        try:
            decoded = bytes.fromhex(compact).decode("utf-8", "strict")
        except (ValueError, UnicodeDecodeError):
            continue
        printable = "".join(character for character in decoded if character.isprintable())
        if len(printable) >= 8 and len(printable) >= 0.8 * len(decoded):
            revealed.append(printable)
    return runs, " ".join(revealed)[:MAX_DECODED_CHARACTERS]


def _acrostic(text: str) -> str:
    """First letters of consecutive lines, when there are enough lines to mean anything.

    Reported, never treated as proof: three lines starting A, C, E are a coincidence, and
    the policy is written to remember that.
    """

    letters = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and stripped[0].isalpha():
            letters.append(stripped[0])
    return "".join(letters).casefold() if 4 <= len(letters) <= 40 else ""


def _contact_markers(text: str) -> tuple[str, ...]:
    """Which contact/identity pattern families appear. Kinds only, never the values."""

    found = []
    for name, pattern in (
        ("email", EMAIL), ("url", URL), ("handle", HANDLE),
        ("messaging_app", MESSAGING), ("long_number", LONG_DIGITS),
    ):
        if pattern.search(text):
            found.append(name)
    # Phone is checked last and only on text without long digit runs already reported, so
    # an ISBN or a year range does not read as a telephone number twice.
    if "long_number" not in found and PHONE.search(text):
        digits = sum(character.isdigit() for character in text)
        if digits >= 7:
            found.append("phone")
    return tuple(found)


def analyze(value: str, *, source: str = "typed") -> TextSignals:
    """Run the full deterministic pass over one piece of text.

    `source` is "typed" or "ocr". OCR text is held to a looser standard: a scanner
    produces repeated characters, broken spacing and stray glyphs on its own, so those
    detectors are damped rather than trusted as evidence of an attempt to hide something.
    """

    original = str(value or "")
    truncated = len(original) > MAX_ANALYZED_CHARACTERS
    working = original[:MAX_ANALYZED_CHARACTERS]

    invisible = sum(character in INVISIBLE_CHARACTERS for character in working)
    normalized = normalize_text(working)
    folded = fold_text(working)

    confusable = sum(character in CONFUSABLES for character in normalized)
    scripts: set[str] = set()
    mixed_words = 0
    leet_words = 0
    for match in WORD.finditer(normalized):
        word = match.group(0)
        word_scripts = {_script_of(character) for character in word if character.isalpha()}
        scripts |= word_scripts
        if len(word_scripts - {"unknown", "other"}) > 1:
            mixed_words += 1
    # Leet means a substitution *inside* a word, not a digit next to one. Addresses,
    # links and encoded runs are excluded: an email contains a letter-@-letter sequence
    # by definition, and counting that as disguised spelling would flag every author who
    # shares a contact address - a privacy finding, not an obfuscation one.
    for token in re.findall(r"\S+", normalized):
        if len(token) > 24 or EMAIL.match(token) or URL.match(token):
            continue
        letters = sum(character.isalpha() for character in token)
        swaps = sum(character in LEET for character in token)
        if letters >= 2 and swaps and re.search(r"[^\W\d_][\d$!|+][^\W\d_]", token):
            leet_words += 1

    repeated = len(REPEATED_CHARACTER.findall(normalized))
    punctuation = len(EXCESSIVE_PUNCTUATION.findall(normalized))
    spaced = len(SPACED_LETTERS.findall(normalized))
    whitespace = len(WHITESPACE_RUN.findall(working))
    encoded_runs, revealed = _decode_runs(normalized)
    acrostic = _acrostic(normalized)
    injections = sum(bool(pattern.search(normalized)) for pattern in INJECTION_PATTERNS)
    if revealed:
        injections += sum(bool(pattern.search(revealed)) for pattern in INJECTION_PATTERNS)
    contacts = _contact_markers(normalized)

    from_ocr = source == "ocr"
    evidence: list[str] = []

    def note(text: str) -> None:
        if len(evidence) < MAX_EVIDENCE_ITEMS:
            evidence.append(text[:MAX_EVIDENCE_LENGTH])

    # Weighted risk. Intent-bearing signals (things a scanner cannot produce and a writer
    # does not type by accident) dominate; cosmetic ones contribute very little.
    risk = 0.0
    if invisible:
        # The heaviest weight in the table: these cannot be typed by accident and a
        # scanner does not produce them, so their presence is intent, not noise.
        risk += min(0.50, 0.20 + 0.04 * invisible)
        note(f"{invisible} invisible or direction-control character(s) removed")
    if confusable:
        risk += min(0.35, 0.10 + 0.02 * confusable)
        note(f"{confusable} character(s) from another script that look like Latin letters")
    if mixed_words:
        risk += min(0.25, 0.08 * mixed_words)
        note(f"{mixed_words} word(s) mixing two scripts")
    if encoded_runs:
        risk += 0.20 if not revealed else 0.35
        note(f"{encoded_runs} encoded-looking run(s)"
             + (", decoded to readable text" if revealed else ""))
    if spaced and not from_ocr:
        risk += min(0.25, 0.12 * spaced)
        note(f"{spaced} run(s) of single letters separated by spaces")
    if leet_words and not from_ocr:
        risk += min(0.15, 0.05 * leet_words)
        note(f"{leet_words} word(s) spelled with digit or symbol substitutions")
    if repeated:
        risk += min(0.08, 0.02 * repeated) * (0.3 if from_ocr else 1.0)
        note(f"{repeated} run(s) of a repeated character")
    if punctuation:
        risk += min(0.06, 0.02 * punctuation)
        note(f"{punctuation} run(s) of repeated punctuation")
    if whitespace and not from_ocr:
        risk += min(0.06, 0.02 * whitespace)
        note(f"{whitespace} run(s) of unusual whitespace")
    if injections:
        risk += min(0.30, 0.15 * injections)
        note(f"{injections} phrase(s) addressed to the review system rather than to learners")
    if acrostic:
        # Recorded so a reviewer can see it. Weighted near zero on purpose: the first
        # letters of four lines spell something by chance far more often than by design.
        note(f"first letters of the lines read: {acrostic}")
        risk += 0.02

    return TextSignals(
        original=original,
        normalized=normalized,
        folded=folded,
        truncated=truncated,
        invisible_characters=invisible,
        confusable_characters=confusable,
        mixed_script_words=mixed_words,
        scripts=tuple(sorted(scripts)),
        repeated_character_runs=repeated,
        excessive_punctuation_runs=punctuation,
        spaced_letter_runs=spaced,
        excessive_whitespace_runs=whitespace,
        leet_words=leet_words,
        encoded_runs=encoded_runs,
        revealed_text=revealed,
        acrostic=acrostic,
        injection_markers=injections,
        contact_markers=contacts,
        obfuscation_risk=round(min(1.0, risk), 4),
        evidence=tuple(evidence),
    )


@dataclass(frozen=True)
class DeterministicFindings:
    """Cheap findings that route and inform, and never decide.

    Every field maps onto a dimension the classifier also judges. The policy engine reads
    both and resolves disagreement explicitly rather than letting either side win by
    running first.
    """

    signals: TextSignals
    privacy_markers: tuple[str, ...] = ()
    injection_detected: bool = False
    empty: bool = False
    too_short: bool = False
    evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_close_reading(self) -> bool:
        """Whether the cheap pass found anything that warrants the stronger model."""

        return bool(
            self.injection_detected
            or self.privacy_markers
            or self.signals.risk_level != "low"
            or self.signals.normalization_changed_meaning
        )


def inspect(value: str, *, source: str = "typed", minimum_characters: int = 12) -> DeterministicFindings:
    """Stage C: run the deterministic checks and report what they found.

    Returns findings, never a decision. The absence of a finding is not evidence that
    content is safe - a plainly written insult trips nothing here, which is precisely why
    the classifier still runs on everything.
    """

    signals = analyze(value, source=source)
    stripped = signals.normalized.strip()
    evidence = list(signals.evidence)
    if signals.contact_markers:
        evidence.append("contains " + ", ".join(signals.contact_markers) + " pattern(s)")
    return DeterministicFindings(
        signals=signals,
        privacy_markers=signals.contact_markers,
        injection_detected=signals.injection_markers > 0,
        empty=not stripped,
        too_short=0 < len(stripped) < minimum_characters,
        evidence=tuple(evidence[:MAX_EVIDENCE_ITEMS]),
    )

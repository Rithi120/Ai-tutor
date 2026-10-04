"""Which language is this text in? Answered from function words, with no model and no network.

A scanned page from a French textbook is full of "le", "la", "est", "dans" and "pour";
a German one of "der", "die", "und", "nicht". Counting those is enough to tell the
languages the trainer supports apart in a few dozen words, and it is deterministic:
the same page always gets the same answer. Exclusive letters (ß, ñ, ã, œ) add a little
weight so that a short caption still resolves.

The scanner only needs to know which dictionary to ask, so "unknown" is an acceptable
answer when there is nothing to go on, and the student can still pick the language.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Function words only. Content words would make the lists long and language-specific
# spelling would leak between them ("mobile", "animal"); function words are short,
# frequent, and almost never shared with the same spelling and frequency.
STOPWORDS: dict[str, frozenset[str]] = {
    "en": frozenset("""
        the and of to in is it you that he was for on are with as his they be at one have
        this from or had by not but what all were we when your can said there an each which
        she do how their if will up other about out many then them these so some her would
        like him into has two more see could my than first been who did get where after
        only me our very through just much think also well
    """.split()),
    "de": frozenset("""
        der die das und ist nicht ich du er sie es wir ihr ein eine einen einem einer eines
        zu den dem des mit auf für von im in an am aus bei nach über unter vor als auch aber
        oder wenn dann noch nur schon sehr hier dort war waren hat haben hatte sind wird
        werden kann können muss soll will mein dein sein kein keine wie was wer wo man sich
        mich dich uns euch ihnen ihm ihn heute morgen gestern viel viele alle jetzt immer
        mal doch ja nein bitte danke
    """.split()),
    "fr": frozenset("""
        le la les un une des du de et est sont je tu il elle on nous vous ils elles ne pas
        que qui quoi dans pour avec sur sous par au aux ce cet cette ces mon ma mes ton ta
        tes son sa ses notre votre leur leurs à en y se me te lui où mais ou donc car comme
        très bien plus moins aussi toujours jamais ici là demain hier être avoir fait va
        vais faire dit tout tous toute toutes quand si oui non merci est-ce c'est j'ai
        n'est qu'il d'un d'une aujourd'hui
    """.split()),
    "es": frozenset("""
        el la los las un una unos unas de del y e o u es son está están soy eres somos yo
        tú él ella usted nosotros vosotros ellos ellas ustedes no sí que qué quién cómo
        cuándo dónde por para con sin sobre en a al se me te le lo nos os les mi mis tu tus
        su sus nuestro nuestra pero porque como muy bien más menos también siempre nunca
        aquí allí hoy mañana ayer ser estar tener tiene tengo hay hace todo todos toda todas
        cuando gracias hola
    """.split()),
    "it": frozenset("""
        il lo la i gli le un uno una di del della dei delle e è sono io tu lui lei noi voi
        loro non che chi come quando dove per con senza su in a al alla si mi ti ci vi mio
        mia tuo tua suo sua ma perché molto bene più meno anche sempre mai qui qua oggi
        domani ieri essere avere ha ho hanno c'è tutto tutti grazie ciao questo questa
        quello quella
    """.split()),
    "pt": frozenset("""
        o a os as um uma uns umas de do da dos das e ou é são está estão eu tu ele ela nós
        vós eles elas você vocês não sim que quem como quando onde por para com sem sobre em
        no na nos nas se me te lhe meu minha teu tua seu sua mas porque muito bem mais menos
        também sempre nunca aqui ali hoje amanhã ontem ser estar ter tem tenho há tudo todos
        obrigado obrigada isto isso aquilo este esta esse essa
    """.split()),
    "nl": frozenset("""
        de het een en van is zijn ik je jij hij zij ze wij we jullie u niet dat die dit deze
        wat wie hoe waar wanneer waarom voor met zonder op in aan bij naar uit over onder
        als ook maar of dan nog al heel erg hier daar vandaag morgen gisteren was waren
        heeft hebben had wordt worden kan kunnen moet moeten wil willen mijn jouw haar ons
        onze hun er te om dus ja nee alsjeblieft dank
    """.split()),
}

# Letters that, in the supported languages, belong to one of them alone. Shared accents
# (é, á, ó) are deliberately absent: they would push French, Spanish and Portuguese
# around at random.
EXCLUSIVE_LETTERS: dict[str, str] = {
    "de": "ßäöü", "fr": "œêùâîôë", "es": "ñ¿¡", "pt": "ãõ",
}
_LETTER_WEIGHT = 0.5
_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")
# How many stopword hits make the answer solid. Below this the confidence shrinks, so a
# three-word caption never claims certainty.
_SOLID_EVIDENCE = 8.0
UNKNOWN = "unknown"


@dataclass(frozen=True)
class Detection:
    language: str
    confidence: float
    scores: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"language": self.language, "confidence": round(self.confidence, 3),
                "scores": {key: round(value, 2) for key, value in self.scores.items() if value}}


def tokens(text: Any) -> list[str]:
    """Words, casefolded, with elided forms ("c'est") kept whole and also split."""

    found = _WORD.findall(str(text or "").casefold())
    result: list[str] = []
    for token in found:
        result.append(token.replace("’", "'"))
        if "'" in token or "’" in token:
            result.extend(part for part in re.split(r"['’]", token) if part)
    return result


def detect_language(text: Any, candidates: tuple[str, ...] | list[str] | None = None) -> Detection:
    """Score each candidate language on function words and exclusive letters."""

    languages = [code for code in (candidates or tuple(STOPWORDS)) if code in STOPWORDS]
    words = tokens(text)
    lowered = str(text or "").casefold()
    scores = {code: 0.0 for code in languages}
    for code in languages:
        vocabulary = STOPWORDS[code]
        scores[code] += float(sum(1 for word in words if word in vocabulary))
        letters = EXCLUSIVE_LETTERS.get(code, "")
        if letters:
            scores[code] += _LETTER_WEIGHT * sum(lowered.count(letter) for letter in letters)
    if not scores:
        return Detection(UNKNOWN, 0.0, {})
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    best_code, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best <= 0 or best == second:
        # "la" is French and Spanish alike; a dead heat is not an answer.
        return Detection(UNKNOWN, 0.0, scores)
    margin = (best - second) / best
    evidence = min(1.0, best / _SOLID_EVIDENCE)
    return Detection(best_code, round(margin * evidence, 3), scores)

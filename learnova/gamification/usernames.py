"""Who may be called what. Pure rules for choosing and changing a username.

Usernames are public (community, leaderboard, profile links) and are also a login
identifier and an allowlist key (COMMUNITY_MODERATORS, PREMIUM_ADMIN_IDENTIFIERS match
on username or email). Two consequences shape the rules here:

- A name that sounds official is refused, so nobody can pose as staff.
- A name someone just gave up is held for a while (HOLD_DAYS), and names on an allowlist
  are refused outright, so changing a moderator's username can never hand their access
  to whoever claims the old name next. The app layer supplies both lists.
"""

from __future__ import annotations

import re
import unicodedata

from learnova.authentication.service import USERNAME_PATTERN

MIN_LENGTH, MAX_LENGTH = 3, 30
# Between two changes by the same account. Long enough that a name means something in
# the community, short enough to fix a regret.
COOLDOWN_DAYS = 30
# How long a released name stays unavailable to other accounts (its owner may take it
# back). Stops "change name, let a friend grab the old one" impersonation.
HOLD_DAYS = 90

# Exact names that would read as an official account or a system value.
RESERVED_NAMES = frozenset({
    "admin", "administrator", "root", "system", "support", "help", "helpdesk", "staff",
    "team", "official", "moderator", "mod", "mods", "learnova", "teacher", "lehrer",
    "lehrerin", "premium", "security", "service", "info", "contact", "kontakt", "null",
    "none", "undefined", "anonymous", "anonym", "deleted", "user", "username", "me",
    "settings", "profile", "leaderboard", "api", "static", "login", "logout", "register",
})
# A token beginning with one of these is refused anywhere in the name
# ("learnova_team", "admin2", "official.help"), but "badminton" is fine.
RESERVED_PREFIXES = ("learnova", "admin", "moderat", "official", "offiziell")

# Clearly offensive terms in English and German. Long terms match anywhere in the name
# once separators are removed ("f.u.c.k"); short ones only as a whole word, so "classic",
# "sussex", "Kassel", "therapist" and "Scunthorpe" stay allowed.
_OFFENSIVE_SUBSTRINGS = (
    "fuck", "shit", "bitch", "nigger", "nigga", "faggot", "whore",
    "pussy", "dildo", "wanker", "hitler", "porno", "vagina", "motherf",
    "wichser", "fotze", "hurensohn", "arschloch", "schlampe", "missgeburt", "kanake",
    "schwuchtel", "bastard", "scheisse", "scheiße", "fickdich", "fickt",
)
_OFFENSIVE_TOKENS = frozenset({
    "sex", "ass", "fick", "nazi", "fag", "cum", "kkk", "hure", "arsch", "porn", "rape",
    "slut", "tit", "tits", "dick", "cock", "sau", "piss", "cunt", "rapist", "penis",
    "nutte", "spast", "spasti", "retard", "nazis", "fck", "fuk", "phuck", "shyt", "btch",
    "kacke", "opfer", "neger", "heil", "sieg", "anal", "boobs", "horny", "nude", "nudes",
})
# Look-alike digits, each with every letter it is commonly used for. "4" stands for "a"
# in "4dmin" but for "u" in "f4ck"; trying both catches both without a word list per
# spelling. Usernames are ASCII only (see USERNAME_PATTERN), so Unicode look-alikes such
# as a Cyrillic "а" never get this far: NFKC folds full-width letters to ASCII and the
# pattern refuses everything else.
_LOOKALIKES = {"0": "o", "1": "il", "2": "z", "3": "e", "4": "au", "5": "s", "6": "g",
               "7": "t", "8": "b", "9": "g"}
_MAX_VARIANTS = 64


def normalize(candidate: str) -> str:
    """The stored form: NFKC, trimmed, lower-case."""

    return unicodedata.normalize("NFKC", str(candidate or "")).strip().casefold()


def _variants(name: str) -> list[str]:
    """The name with each look-alike digit read as each letter it can stand for."""

    variants = [""]
    for character in name:
        options = _LOOKALIKES.get(character, character)
        variants = [prefix + option for prefix in variants for option in options][:_MAX_VARIANTS]
    return variants


def _squeeze(text: str) -> str:
    """Stretched letters back to one: "fuuuuck" -> "fuck". Doubles stay ("mass")."""

    return re.sub(r"(.)\1{2,}", r"\1", text)


def _readings(name: str) -> list[tuple[list[str], str]]:
    """Every (words, words joined) a reader could see in the name."""

    readings = []
    for variant in _variants(name):
        for text in {variant, _squeeze(variant)}:
            tokens = [token for token in re.split(r"[^a-z]+", text) if token]
            readings.append((tokens, "".join(tokens)))
    return readings


def skeleton(name: str) -> str:
    """A comparison key for impersonation: letters only, look-alikes and repeats folded.

    "mod.anna", "mod_anna", "m0d-anna" and "modannna" share one skeleton, so a name that
    only differs from a moderator's by punctuation or a digit is treated as theirs.
    """

    first = "".join(_LOOKALIKES.get(character, character)[0] for character in normalize(name))
    return re.sub(r"(.)\1+", r"\1", re.sub(r"[^a-z]", "", first))


def problem(candidate: str) -> str | None:
    """Why a name is not allowed, or None. Codes: format | reserved | inappropriate."""

    name = normalize(candidate)
    if not (MIN_LENGTH <= len(name) <= MAX_LENGTH) or not USERNAME_PATTERN.fullmatch(name):
        return "format"
    if name.endswith((".", "-")) or ".." in name or "__" in name:
        return "format"
    readings = _readings(name)
    for tokens, joined in readings:
        if name in RESERVED_NAMES or joined in RESERVED_NAMES:
            return "reserved"
        if joined.startswith(RESERVED_PREFIXES) or any(token.startswith(RESERVED_PREFIXES) for token in tokens):
            return "reserved"
    for tokens, joined in readings:
        if any(term in joined for term in _OFFENSIVE_SUBSTRINGS):
            return "inappropriate"
        if any(token in _OFFENSIVE_TOKENS for token in tokens):
            return "inappropriate"
    return None


MESSAGES = {
    "format": ("Use 3 to 30 characters: lower-case letters, numbers, dots, dashes or "
               "underscores, starting with a letter or number."),
    "reserved": "That name is reserved. Please choose another one.",
    "inappropriate": "That name is not allowed in Learnova. Please choose another one.",
    "taken": "That username is already taken.",
    "held": "That username was in use recently and is not available yet.",
    "cooldown": "You can change your username again on {date}.",
    "unchanged": "That is already your username.",
}

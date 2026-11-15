"""Simulated league rivals: the rules. Pure functions, no database, no AI.

A weekly league group has 30 seats. When fewer than 27 real learners are in it, the rest
are filled with simulated rivals (`rival_count`): 3 learners meet 27 rivals, 26 meet 4,
27 or more meet none. Rivals are clearly labelled as simulated wherever they appear.

Each rival is planned once, when its seat is filled, from a stored seed:
- a name from curated lists ("nathan7", "emanuel.sc", "mia.study") with its display name;
- a behaviour profile - casual 40 %, consistent 40 %, competitive 15 %, strong 5 % of the
  seats - that sets how much it learns and how regularly;
- a target for the week: the league's calibrated baseline (last week's median real XP, or
  a configured default) times a bounded factor of its profile, within the weekly limit;
- a schedule of simulated learning events (lesson, quiz, flashcards, vocabulary, practice)
  spread over the week at plausible times, each worth what the real XP rules give and never
  more than the daily limit. A rival's weekly XP at any moment is the sum of its events
  that have happened by then: no jumps, no reaction to any particular learner.

Nothing here touches a real account. The app stores the plan in its own tables
(league_rival, league_rival_event) and deletes them when the week ends.
"""

from __future__ import annotations

import hashlib
import random
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

SEATS = 30
NO_RIVALS_FROM = 27          # 27 or more real learners: no rivals at all

# (profile, seats out of 30, factor range of the baseline, active days range)
BEHAVIOURS: tuple[tuple[str, int, tuple[float, float], tuple[int, int]], ...] = (
    ("casual", 12, (0.35, 0.75), (2, 4)),
    ("consistent", 12, (0.8, 1.15), (5, 7)),
    ("competitive", 4, (1.2, 1.5), (5, 7)),
    ("strong", 2, (1.5, 1.8), (6, 7)),
)
BEHAVIOUR_KEYS = tuple(item[0] for item in BEHAVIOURS)

# What one simulated learning event earns, from the real XP rules (gamification.XP_VALUES):
# a lesson is 10 XP plus its answers (3 each), a flashcard session 15 plus reviews, …
EVENT_XP: dict[str, tuple[int, int]] = {
    "lesson": (18, 30), "quiz": (14, 24), "flashcards": (12, 28), "vocabulary": (8, 20), "practice": (6, 18),
}
EVENT_KINDS = tuple(EVENT_XP)


@dataclass(frozen=True)
class Limits:
    """Configured bounds (LEAGUE_RIVAL_* settings)."""

    default_baseline: int = 60      # Bronze weekly XP when last week has too little data
    min_samples: int = 5            # real results needed to trust last week's median
    min_baseline: int = 20
    max_baseline: int = 400
    max_daily_xp: int = 150
    max_weekly_xp: int = 600


# Each league up is a little more demanding when there is no history to calibrate from.
TIER_FACTOR = {1: 1.0, 2: 1.2, 3: 1.4, 4: 1.65, 5: 1.9, 6: 2.2, 7: 2.5}
# Lifetime levels a learner in that league usually has (a promotion takes weeks of XP).
TIER_LEVELS = {1: (2, 6), 2: (3, 8), 3: (5, 10), 4: (6, 12), 5: (8, 14), 6: (10, 17), 7: (12, 20)}


def rival_count(real_learners: int, seats: int = SEATS) -> int:
    """How many rivals a group gets: 30 - real when fewer than 27 real learners, else 0.
    With another configured group size the rule is the same: none once at most 3 seats
    are free."""

    real, seats = max(0, int(real_learners)), max(1, int(seats))
    return max(0, seats - real) if real < seats - (SEATS - NO_RIVALS_FROM) else 0


def baseline(tier: int, last_week_results: list[int], limits: Limits = Limits()) -> int:
    """The weekly XP rivals are calibrated on: last week's median real league XP in this
    league when there are enough results, else the configured default for the league."""

    values = sorted(max(0, int(value)) for value in last_week_results)
    if len(values) >= limits.min_samples:
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
    else:
        median = limits.default_baseline * TIER_FACTOR.get(int(tier), TIER_FACTOR[7])
    return int(min(limits.max_baseline, max(limits.min_baseline, round(median))))


def seed_for(week_start: str, tier: int, group_number: int, seat: int) -> int:
    digest = hashlib.sha256(f"learnova-rival|{week_start}|{int(tier)}|{int(group_number)}|{int(seat)}".encode())
    return int(digest.hexdigest()[:15], 16)


def behaviour_for(week_start: str, tier: int, group_number: int, seat: int) -> str:
    """The profile of a seat: the 30 seats of a group are dealt 12 casual, 12 consistent,
    4 competitive and 2 strong, in an order fixed by the week, league and group."""

    deck = [key for key, seats, _factor, _days in BEHAVIOURS for _ in range(seats)]
    random.Random(seed_for(week_start, tier, group_number, 0)).shuffle(deck)
    return deck[(int(seat) - 1) % len(deck)]


# ------------------------------------------------------------------ names

_FIRST_NAMES = (
    "nathan", "emanuel", "mia", "alexander", "luca", "sophia", "benjamin", "emma", "lena", "jonas",
    "elias", "noah", "hannah", "leon", "paul", "emilia", "ben", "lina", "finn", "marie", "amira",
    "yusuf", "aylin", "elif", "mila", "tim", "lea", "julian", "zoe", "anton", "ida", "milan", "ella",
    "theo", "clara", "malik", "leyla", "felix", "nora", "david", "sara", "luis", "maja", "jakob",
    "ronja", "emil", "frieda", "selin", "matteo", "greta", "henry", "alina", "karim", "jana", "oskar",
    "mira", "valentin", "luisa", "arda", "nele", "pia", "levi", "melina", "jonathan", "johanna",
    "moritz", "charlotte", "lukas", "victoria", "mats", "fiona", "helena", "max", "carla", "leni",
    "aaron", "sophie", "niklas", "samuel", "lotta", "vincent", "isabel", "tom", "marlene",
)
_SURNAMES = (
    "Schneider", "Krüger", "Müller", "Weber", "Becker", "Hoffmann", "Neumann", "Zimmermann", "Braun",
    "Fischer", "Hartmann", "Koch", "Schulz", "Wagner", "Lehmann", "Richter", "Bauer", "Wolf", "Keller",
    "Schäfer", "Yilmaz", "Kaya", "Nowak", "Petrović", "Lang", "Vogel", "Frank", "Berger", "Roth", "Seidel",
)
_STUDY_SUFFIXES = ("study", "learn", "lernt", "de")


def _ascii(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
                                   .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue").replace("ß", "ss"))
    return "".join(char for char in folded if char.isascii() and char.isalnum()).lower()


def name_for(seed: int, attempt: int = 0) -> tuple[str, str]:
    """(username, display name), e.g. ("emanuel.sc", "Emanuel S.") or ("mia.study", "Mia")."""

    rng = random.Random(seed * 7919 + attempt)
    first = rng.choice(_FIRST_NAMES)
    surname = rng.choice(_SURNAMES)
    display = f"{first.capitalize()} {surname[0]}."
    style = rng.random()
    if style < 0.3:                                  # nathan7, alexander12
        username = f"{first}{rng.choice((rng.randint(1, 9), rng.randint(10, 19)))}"
    elif style < 0.55:                               # emanuel.sc
        username = f"{first}.{_ascii(surname)[:2]}"
    elif style < 0.75:                               # mia.study, luca.learn, benjamin.de
        username = f"{first}.{rng.choice(_STUDY_SUFFIXES)}"
        display = first.capitalize()
    elif style < 0.9:                                # sophia_8
        username = f"{first}_{rng.randint(1, 9)}"
    else:                                            # leon.weber
        username = f"{first}.{_ascii(surname)}"
        display = f"{first.capitalize()} {surname}"
    return username[:30], display


# ------------------------------------------------------------------ the plan of one rival

@dataclass(frozen=True)
class Event:
    at: datetime            # when it happens (timezone-aware)
    kind: str               # one of EVENT_KINDS
    xp: int


@dataclass(frozen=True)
class Plan:
    seat: int
    seed: int
    username: str
    display_name: str
    behaviour: str
    level: int
    avatar_pick: int        # 0 = the default avatar; else an index into the unlocked ones
    target_xp: int
    achievements: tuple[str, ...]
    events: tuple[Event, ...] = field(default_factory=tuple)


# Badges a learner of a given level plausibly has: (key, lowest level, chance). Only ones
# the catalogue really awards; no rare ones on a beginner.
_BADGE_ODDS = (
    ("first_steps", 1, 1.0), ("flashcard_beginner", 2, 0.7), ("first_vocabulary_list", 2, 0.4),
    ("mistake_fixer", 3, 0.45), ("perfect_test", 3, 0.3), ("fast_matcher", 4, 0.2),
    ("perfect_vocabulary_test", 5, 0.15), ("vocabulary_50", 6, 0.25), ("flashcard_master", 8, 0.25),
)


def _schedule(rng: random.Random, target: int, days: int, week_start_local: datetime,
              limits: Limits) -> tuple[Event, ...]:
    """Learning events adding up to `target`, on `days` days of the week, at the times a
    student learns (afternoons and evenings, later mornings at the weekend)."""

    smallest = min(low for low, _high in EVENT_XP.values())
    # A day needs room for at least one event; a small target means fewer active days.
    days = max(1, min(7, days, target // smallest or 1))
    chosen_days = sorted(rng.sample(range(7), days))
    weights = [rng.uniform(0.6, 1.4) for _ in chosen_days]
    budgets = [min(limits.max_daily_xp, int(target * weight / sum(weights))) for weight in weights]
    budgets[-1] = min(limits.max_daily_xp, budgets[-1] + max(0, target - sum(budgets)))
    events: list[tuple[int, int, str, int]] = []      # (day, minute, kind, xp)
    for day, budget in zip(chosen_days, budgets):
        left = budget
        while left >= smallest:
            kinds = [kind for kind in EVENT_KINDS if EVENT_XP[kind][0] <= left]
            kind = rng.choice(kinds)
            low, high = EVENT_XP[kind]
            xp = min(rng.randint(low, high), left)
            if left - xp < smallest and left - xp > 0 and xp + (left - xp) <= high + smallest:
                xp = left                              # the remainder joins this event
            if day >= 5:                               # Saturday, Sunday
                minute = rng.randint(9 * 60, 20 * 60)
            else:
                minute = rng.randint(14 * 60, 21 * 60 + 30)
            events.append((day, minute, kind, xp))
            left -= xp
    events.sort()
    used: set[tuple[int, int]] = set()
    out = []
    for day, minute, kind, xp in events:
        while (day, minute) in used:                   # two events never at the same minute
            minute += 7
        used.add((day, minute))
        out.append(Event(week_start_local + timedelta(days=day, minutes=minute), kind, xp))
    return tuple(out)


def plan_rival(week_start_local: datetime, tier: int, group_number: int, seat: int, *,
               baseline_xp: int, limits: Limits = Limits(), name_attempt: int = 0) -> Plan:
    """Everything about one rival, reproducible from (week, league, group, seat).

    week_start_local: Monday 00:00 of the week in the app's timezone (timezone-aware).
    name_attempt: > 0 only when the name was taken; it changes the name, nothing else.
    """

    week_key = week_start_local.date().isoformat()
    seed = seed_for(week_key, tier, group_number, seat)
    rng = random.Random(seed)
    behaviour = behaviour_for(week_key, tier, group_number, seat)
    _key, _seats, (low, high), (fewest, most) = next(item for item in BEHAVIOURS if item[0] == behaviour)
    target = int(min(limits.max_weekly_xp, max(1, round(baseline_xp * rng.uniform(low, high)))))
    days = rng.randint(fewest, most)
    events = _schedule(rng, target, days, week_start_local, limits)
    level_low, level_high = TIER_LEVELS.get(int(tier), TIER_LEVELS[7])
    if behaviour in ("competitive", "strong"):
        level_low = min(level_high, level_low + 2)
    level = rng.randint(level_low, level_high)
    achievements = [key for key, minimum, chance in _BADGE_ODDS if level >= minimum and rng.random() < chance]
    if behaviour in ("consistent", "strong") and level >= 4 and rng.random() < 0.6:
        achievements.insert(1, "study_streak")
    username, display = name_for(seed, name_attempt)
    return Plan(
        seat=int(seat), seed=seed, username=username, display_name=display, behaviour=behaviour,
        level=level, avatar_pick=0 if rng.random() < 0.2 else 1 + rng.randrange(level),
        target_xp=sum(event.xp for event in events), achievements=tuple(achievements[:6]), events=events,
    )


def lifetime_xp(level: int, seed: int, weekly_xp: int) -> int:
    """A total XP inside the rival's level (25·(L−1)·(L+2) … the next level's start)."""

    level = max(1, int(level))
    start = 25 * (level - 1) * (level + 2)
    span = 25 * level * (level + 3) - start
    return start + int(span * random.Random(seed + 17).uniform(0.05, 0.8)) + max(0, int(weekly_xp))

"""Simulated league rivals: the rules. Pure functions, no database, no AI.

A weekly league group has 30 seats. When fewer than 27 real learners are in it, the rest
are filled with simulated rivals (`rival_count`): 3 learners meet 27 rivals, 26 meet 4,
27 or more meet none. Rivals are clearly labelled as simulated wherever they appear.

Each rival's identity is planned once, when its seat is filled, from a stored seed: a
name from curated lists ("nathan7", "emanuel.sc", "mia.study") with its display name, a
level, badges, and a behaviour profile - casual 40 %, consistent 40 %, competitive 15 %,
strong 5 % of the seats - that sets when and how regularly it learns.

Its score is paced live (owner request 2026-11-15):
- **It starts at 0** when its seat is filled and earns only in the 15-minute slots that
  have passed since (`advance`), in its own waking hours, so the board moves as time passes.
- **It follows the group's pace.** Its weekly target is recomputed from what the group's
  fastest real learner is on course for (`reference_xp`): a fast learner meets faster
  rivals, a slow one slower rivals. The pace is measured up to the start of today, so a
  strong day can lift a learner past the rivals and they answer from the next day on.
  Early in the week the projection leans on the league's calibrated baseline (last
  week's median real XP, or a configured default).
- **The top places are hard.** The 3 strongest rivals aim a little above that pace and the
  next 2 level with it (`roles`, `target_for`): reaching the top 3 means beating your own
  pace. They stay beatable: a target only steers the rest of the week, every event and day
  is bounded, and a late sprint leaves them too little time to answer.
Past its target a rival keeps learning lightly (up to 15 % above it), so the board never
freezes. A rival's weekly XP at any moment is the sum of its events so far: it never goes down.

Nothing here touches a real account. The app stores the plan in its own tables
(league_rival, league_rival_event) and deletes them when the week ends.
"""

from __future__ import annotations

import hashlib
import random
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

SEATS = 30
NO_RIVALS_FROM = 27          # 27 or more real learners: no rivals at all

# (profile, seats out of 30, share of the group's pace it aims for, chance a day is active)
BEHAVIOURS: tuple[tuple[str, int, tuple[float, float], float], ...] = (
    ("casual", 12, (0.15, 0.45), 0.55),
    ("consistent", 12, (0.45, 0.8), 0.9),
    ("competitive", 4, (0.7, 0.92), 1.0),
    ("strong", 2, (0.85, 0.98), 1.0),
)
BEHAVIOUR_KEYS = tuple(item[0] for item in BEHAVIOURS)
# Local hours a profile learns on a school day (weekends start two hours later), and the
# chance it learns in one 15-minute slot of an active hour.
WAKING_HOURS = {"casual": (15, 21), "consistent": (7, 22), "competitive": (7, 23), "strong": (6, 23)}
SLOT_CHANCE = {"casual": 0.25, "consistent": 0.3, "competitive": 0.35, "strong": 0.4}
SLOT = timedelta(minutes=15)
# The strongest rivals of a group and how far above (or level with) the pace they aim.
PACESETTERS, PACESETTER_FACTOR = 3, (1.08, 1.22)
CHASERS, CHASER_FACTOR = 2, (0.9, 1.02)
STRENGTH = {"strong": 0, "competitive": 1, "consistent": 2, "casual": 3}
EVENT_XP_BOUNDS = (2, 45)           # one simulated session: never a jump
# Past its target a rival keeps learning lightly (short sessions, less often), up to this
# much above it, so the board never freezes; then it stops for the week.
KEEP_GOING_LIMIT, KEEP_GOING_XP, KEEP_GOING_CHANCE = 1.15, (2, 6), 0.3

# What one simulated learning event earns, in the range real sessions pay: a lesson is 10 XP
# plus its answers (3 each), a flashcard session half its correct answers (30 correct: 15), …
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
    # The most a rival earns: high enough to keep pace with a learner near the real daily
    # XP budgets, so a fast learner still meets competition.
    max_daily_xp: int = 600
    max_weekly_xp: int = 3500


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
    achievements: tuple[str, ...]


# Badges a learner of a given level plausibly has: (key, lowest level, chance). Only ones
# the catalogue really awards; no rare ones on a beginner.
_BADGE_ODDS = (
    ("first_steps", 1, 1.0), ("flashcard_beginner", 2, 0.7), ("first_vocabulary_list", 2, 0.4),
    ("mistake_fixer", 3, 0.45), ("perfect_test", 3, 0.3), ("fast_matcher", 4, 0.2),
    ("perfect_vocabulary_test", 5, 0.15), ("vocabulary_50", 6, 0.25), ("flashcard_master", 8, 0.25),
)


def plan_rival(week_start_local: datetime, tier: int, group_number: int, seat: int, *,
               name_attempt: int = 0) -> Plan:
    """Who one rival is, reproducible from (week, league, group, seat). Its score is not
    planned here: it is paced live (`advance`).

    week_start_local: Monday 00:00 of the week in the app's timezone (timezone-aware).
    name_attempt: > 0 only when the name was taken; it changes the name, nothing else.
    """

    week_key = week_start_local.date().isoformat()
    seed = seed_for(week_key, tier, group_number, seat)
    rng = random.Random(seed)
    behaviour = behaviour_for(week_key, tier, group_number, seat)
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
        achievements=tuple(achievements[:6]),
    )


# ------------------------------------------------------------------ pacing

def reference_xp(real_weekly_xp: list[int], now: datetime, week_start: datetime, week_end: datetime,
                 baseline_xp: int) -> float:
    """The weekly XP the group's fastest real learner is on course for.

    `real_weekly_xp` is measured up to `now` (the app passes the start of the local day).
    Their XP so far projected over the whole week, trusted fully after one day of the
    week (before that it is blended with the league's baseline, so one early burst does
    not send the rivals flying). Never below what they already have.
    """

    week = max(1.0, (week_end - week_start).total_seconds())
    elapsed = min(week, max(0.0, (now - week_start).total_seconds()))
    top = max((int(value) for value in real_weekly_xp), default=0)
    if top <= 0:
        return float(max(1, baseline_xp))
    projected = top / max(elapsed / week, 1 / 28)   # never project from under six hours
    trust = min(1.0, elapsed / 86400)
    return max(float(top), trust * projected + (1 - trust) * baseline_xp)


def roles(rivals: list[tuple[int, int, str]]) -> dict[int, str]:
    """{seat: role} for a group's rivals given as (seat, seed, behaviour): the 3 strongest
    are "pacesetter", the next 2 "chaser", the rest keep their behaviour."""

    order = sorted(rivals, key=lambda item: (STRENGTH.get(item[2], 9), item[1]))
    out = {}
    for index, (seat, _seed, behaviour) in enumerate(order):
        out[int(seat)] = ("pacesetter" if index < PACESETTERS else
                          "chaser" if index < PACESETTERS + CHASERS else behaviour)
    return out


def target_for(role: str, seed: int, reference: float, limits: Limits = Limits()) -> int:
    """A rival's weekly target: its share of the group's pace. The share is fixed per rival
    (from its seed), so the target moves only when the pace does."""

    if role == "pacesetter":
        low, high = PACESETTER_FACTOR
    elif role == "chaser":
        low, high = CHASER_FACTOR
    else:
        low, high = next(item[2] for item in BEHAVIOURS if item[0] == role)
    factor = random.Random(int(seed) * 31 + 7).uniform(low, high)
    return int(min(limits.max_weekly_xp, max(1, round(reference * factor))))


def slot_floor(moment: datetime) -> datetime:
    """The start of the 15-minute slot `moment` is in (slots are aligned in UTC)."""

    seconds = int(moment.timestamp())
    return datetime.fromtimestamp(seconds - seconds % int(SLOT.total_seconds()), tz=moment.tzinfo)


def _active_slots(seed: int, behaviour: str, start: datetime, end: datetime, zone) -> list[datetime]:
    """The slots in [start, end) in which this rival is awake (start is slot-aligned)."""

    first_hour, last_hour = WAKING_HOURS.get(behaviour, WAKING_HOURS["consistent"])
    day_chance = next((item[3] for item in BEHAVIOURS if item[0] == behaviour), 0.9)
    days: dict = {}
    out = []
    moment = start
    while moment < end:
        local = moment.astimezone(zone)
        day = local.date()
        if day not in days:
            days[day] = random.Random(int(seed) * 7 + day.toordinal()).random() < day_chance
        shift = 2 if local.weekday() >= 5 else 0
        if days[day] and first_hour + shift <= local.hour < last_hour:
            out.append(moment)
        moment += SLOT
    return out


def advance(*, seed: int, behaviour: str, earned: int, target: int, after: datetime, until: datetime,
            week_end: datetime, zone, day_xp: dict | None = None, limits: Limits = Limits()) -> list[Event]:
    """Simulated learning in the 15-minute slots that ended after `after` and by `until`.

    `after` is where the last call stopped (slot-aligned). Each event is sized so the rival
    is on course for `target` by `week_end`, so a new target only steers what is left of
    the week. `day_xp` ({local date: XP}) is what it already earned on those days.
    """

    stop = slot_floor(min(until, week_end))
    if stop <= after:
        return []
    awake = _active_slots(seed, behaviour, after, week_end, zone)
    chance = SLOT_CHANCE.get(behaviour, 0.3)
    totals = dict(day_xp or {})
    low_xp, high_xp = EVENT_XP_BOUNDS
    events = []
    ceiling = min(limits.max_weekly_xp, int(int(target) * KEEP_GOING_LIMIT))
    for index, slot in enumerate(awake):
        if slot >= stop:
            break
        need = int(target) - int(earned)
        rng = random.Random(int(seed) * 1_000_003 + int(slot.timestamp()) // 60)
        roll = rng.random()
        day = slot.astimezone(zone).date()
        room = limits.max_daily_xp - totals.get(day, 0)
        if need <= 0:                           # target reached: a short session now and then
            room = min(room, ceiling - int(earned))
            if roll >= chance * KEEP_GOING_CHANCE or room < KEEP_GOING_XP[0]:
                continue
            xp = min(room, rng.randint(*KEEP_GOING_XP))
        else:
            # A small target learns less often rather than in sessions below the minimum,
            # so it is not reached early in the week.
            left = max(1, len(awake) - index)
            odds = min(chance, need / (left * low_xp))
            if roll >= odds or room <= 0:
                continue
            per_event = need / (left * odds)
            xp = min(need, room, max(low_xp, min(high_xp, round(per_event * rng.uniform(0.6, 1.4)))))
        fitting = [kind for kind in EVENT_KINDS if EVENT_XP[kind][0] <= xp] or ["practice"]
        events.append(Event(slot + timedelta(seconds=rng.randrange(int(SLOT.total_seconds()))), rng.choice(fitting), xp))
        earned += xp
        totals[day] = totals.get(day, 0) + xp
    return events


def lifetime_xp(level: int, seed: int, weekly_xp: int) -> int:
    """A total XP inside the rival's level (25·(L−1)·(L+2) … the next level's start)."""

    level = max(1, int(level))
    start = 25 * (level - 1) * (level + 2)
    span = 25 * level * (level + 3) - start
    return start + int(span * random.Random(seed + 17).uniform(0.05, 0.8)) + max(0, int(weekly_xp))

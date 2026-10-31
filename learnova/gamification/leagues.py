"""Learnova Leagues: weekly competition rules. Pure functions, no database.

How a week works:
- A competition week runs Monday 00:00 to Monday 00:00 in APP_TIMEZONE, on the server's
  clock. Changing a device clock or timezone moves nothing.
- A participating learner enters the week when they earn their first XP in it, into a
  group of at most GROUP_SIZE learners of their league. Someone with no activity simply
  does not take part that week: they keep their league and cannot be demoted for it.
- Weekly XP is the XP the server awarded inside the week. It never touches lifetime XP.
- When the week ends each group is ranked and the top `promote` move up, the bottom
  `demote` move down; the top league has no promotion and the bottom none below it.

Ties are broken the same way everywhere: more weekly XP first, then whoever reached
their total earlier, then the lower account id, so a rank never depends on query order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True)
class League:
    tier: int
    key: str
    name: str
    # Two colours for the league's crest and header gradient.
    colour: str
    colour_dark: str


LEAGUES: tuple[League, ...] = (
    League(1, "bronze", "Bronze League", "#c9824a", "#8a4f24"),
    League(2, "silver", "Silver League", "#aeb8c7", "#66748a"),
    League(3, "gold", "Gold League", "#ffc94a", "#c78a10"),
    League(4, "sapphire", "Sapphire League", "#4f8cff", "#2146b8"),
    League(5, "ruby", "Ruby League", "#ff5577", "#b01e44"),
    League(6, "diamond", "Diamond League", "#7fe3ff", "#2a9ec4"),
    League(7, "legendary", "Legendary League", "#b48cff", "#5b3fd6"),
)
LEAGUE_BY_TIER = {league.tier: league for league in LEAGUES}
LOWEST, HIGHEST = LEAGUES[0].tier, LEAGUES[-1].tier
GROUP_SIZE = 30
PROMOTE, DEMOTE = 10, 10


def league(tier: int) -> League:
    return LEAGUE_BY_TIER.get(int(tier), LEAGUES[0])


@dataclass(frozen=True)
class Standing:
    user_id: int
    weekly_xp: int
    # When the learner earned the XP that brought them to their total; None with no XP.
    last_xp_at: datetime | None = None


def ranked(standings: Iterable[Standing]) -> list[Standing]:
    """Best first, with the documented tie-break."""

    far_future = datetime.max

    def key(item: Standing):
        moment = item.last_xp_at.replace(tzinfo=None) if item.last_xp_at else far_future
        return (-int(item.weekly_xp), moment, int(item.user_id))

    return sorted(standings, key=key)


def zone_sizes(size: int, tier: int, *, promote: int = PROMOTE, demote: int = DEMOTE) -> tuple[int, int]:
    """How many places promote and demote in a group of `size`.

    A full group uses the configured numbers. A group too small for both zones (fewer
    than promote + demote learners) shrinks them to a third each, so there is always a
    safe middle; with two or more learners at least one place still promotes, so a
    small league is never stuck. One learner alone neither rises nor falls.
    """

    size = max(0, int(size))
    promote, demote = max(0, int(promote)), max(0, int(demote))
    if size < 2:
        return 0, 0
    if size >= promote + demote:
        up, down = promote, demote
    else:
        up, down = max(1, size // 3), size // 3
    if tier >= HIGHEST:
        up = 0
    if tier <= LOWEST:
        down = 0
    return up, down


def outcomes(
    standings: Iterable[Standing], tier: int, *, promote: int = PROMOTE, demote: int = DEMOTE,
) -> list[tuple[Standing, int, str]]:
    """(standing, rank, outcome) for one group: promoted | demoted | stayed.

    Only learners who earned XP can be promoted. Each learner gets exactly one outcome,
    so nobody is promoted and demoted at once, even in a tiny group.
    """

    order = ranked(standings)
    up, down = zone_sizes(len(order), tier, promote=promote, demote=demote)
    result = []
    for index, item in enumerate(order):
        rank = index + 1
        if index < up and item.weekly_xp > 0:
            outcome = "promoted"
        elif index >= len(order) - down:
            outcome = "demoted"
        else:
            outcome = "stayed"
        result.append((item, rank, outcome))
    return result


def tier_after(tier: int, outcome: str) -> int:
    if outcome == "promoted":
        return min(HIGHEST, tier + 1)
    if outcome == "demoted":
        return max(LOWEST, tier - 1)
    return tier


def zone(rank: int, size: int, tier: int, weekly_xp: int, *,
         promote: int = PROMOTE, demote: int = DEMOTE) -> str:
    """Where a current rank sits right now: promotion | safe | demotion."""

    up, down = zone_sizes(size, tier, promote=promote, demote=demote)
    if rank <= up and weekly_xp > 0:
        return "promotion"
    if rank > size - down:
        return "demotion"
    return "safe"

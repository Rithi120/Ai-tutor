"""The 20 collectible Learnova characters and when each one unlocks.

The artwork lives in static/avatars/<id>.svg (original, self-contained SVG). Unlocks are
by level only, and a level never goes down because lifetime XP never does, so an unlock
is permanent by construction; the app still records each one (UserAvatarUnlock) so the
history survives any future change to this table.
"""

from __future__ import annotations

from dataclasses import dataclass

RARITIES = ("common", "uncommon", "rare", "epic", "legendary")
RARITY_LABELS = {
    "common": "Common", "uncommon": "Uncommon", "rare": "Rare",
    "epic": "Epic", "legendary": "Legendary",
}


@dataclass(frozen=True)
class Avatar:
    id: str
    name: str
    description: str
    unlock_level: int
    rarity: str
    # The ring colour the UI draws around the portrait; matches the artwork's palette.
    accent: str

    @property
    def asset(self) -> str:
        return f"avatars/{self.id}.svg"


# One character per level from 1 to 20: every level-up early on brings something new,
# and the last ones are rare enough to mean something.
AVATARS: tuple[Avatar, ...] = (
    Avatar("explorer", "The Explorer",
           "Wide-brim hat, compass and a map in every pocket. Every learning journey starts here.",
           1, "common", "#18b9bd"),
    Avatar("scientist", "The Scientist",
           "Goggles up, lab coat on, one bubbling flask away from a discovery.",
           2, "common", "#7c5cff"),
    Avatar("inventor", "The Inventor",
           "Gears on the headband, a magnifier over one eye and a wrench for every problem.",
           3, "common", "#e0803a"),
    Avatar("astronaut", "The Astronaut",
           "Helmet on and mission patch stitched. Curiosity has no gravity.",
           4, "common", "#ff7a6b"),
    Avatar("coder", "The Coder",
           "Hoodie, headphones and a screen full of code reflected in the glasses.",
           5, "common", "#2fd07a"),
    Avatar("scholar", "The Scholar",
           "Round spectacles, a quill behind the ear and a scarf for long library afternoons.",
           6, "uncommon", "#b0384f"),
    Avatar("ninja", "The Ninja",
           "Silent, focused and fast. Practice in the shadows, mastery in the light.",
           7, "uncommon", "#d6334a"),
    Avatar("knight", "The Knight",
           "Visor raised, plume high, shield bearing the Learnova star.",
           8, "uncommon", "#3b6fe0"),
    Avatar("wizard", "The Wizard",
           "A starry hat and a wand that sparkles whenever something finally clicks.",
           9, "uncommon", "#5b3fd6"),
    Avatar("artist", "The Artist",
           "Beret, paint on the cheek and a brush behind the ear. Ideas in every colour.",
           10, "uncommon", "#f25ca2"),
    Avatar("strategist", "The Strategist",
           "Always three moves ahead, with a chess knight for good luck.",
           11, "rare", "#1f9d73"),
    Avatar("detective", "The Detective",
           "Deerstalker, turned-up collar and a magnifying glass for every clue.",
           12, "rare", "#c98a2b"),
    Avatar("engineer", "The Engineer",
           "Hard hat, headset and blueprints. Builds understanding piece by piece.",
           13, "rare", "#f2b705"),
    Avatar("champion", "The Champion",
           "Laurel crown and gold medal, earned one lesson at a time.",
           14, "rare", "#e5a823"),
    Avatar("robot", "The Robot",
           "A friendly machine with a glowing core and a screen that smiles back.",
           15, "epic", "#28c7e8"),
    Avatar("dragon-guardian", "The Dragon Guardian",
           "Dragon-scale armour and a small dragon companion who never leaves their shoulder.",
           16, "epic", "#2fae6a"),
    Avatar("cyber-guardian", "The Cyber Guardian",
           "Neon visor and circuits of light. Guards the network of everything you know.",
           17, "epic", "#e040fb"),
    Avatar("time-traveller", "The Time Traveller",
           "Clock-face goggles and an aviator jacket, back from a future where you aced it.",
           18, "epic", "#1aa39a"),
    Avatar("mastermind", "The Mastermind",
           "Calm, knowing and surrounded by glowing runes of pure understanding.",
           19, "legendary", "#8a4dff"),
    Avatar("founder", "The Legendary Founder",
           "Crowned with the Learnova star. Reserved for those who climbed all the way.",
           20, "legendary", "#ffb547"),
)
AVATAR_BY_ID = {avatar.id: avatar for avatar in AVATARS}
DEFAULT_AVATAR = AVATARS[0].id
assert len(AVATARS) == 20 and len(AVATAR_BY_ID) == 20
assert [avatar.unlock_level for avatar in AVATARS] == list(range(1, 21))
assert all(avatar.rarity in RARITIES for avatar in AVATARS)


def get(avatar_id: str | None) -> Avatar:
    """The avatar for an id, or the default one for anything unknown."""

    return AVATAR_BY_ID.get(str(avatar_id or ""), AVATAR_BY_ID[DEFAULT_AVATAR])


def is_unlocked(avatar_id: str, level: int) -> bool:
    avatar = AVATAR_BY_ID.get(avatar_id)
    return avatar is not None and int(level) >= avatar.unlock_level


def unlocked_for_level(level: int) -> tuple[str, ...]:
    return tuple(avatar.id for avatar in AVATARS if int(level) >= avatar.unlock_level)


def next_unlock(level: int) -> Avatar | None:
    return next((avatar for avatar in AVATARS if avatar.unlock_level > int(level)), None)

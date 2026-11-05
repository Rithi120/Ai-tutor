"""Who sees which tutorial steps, and the translated JSON the browser engine runs on.

`capabilities()` turns what the server knows about a student (feature flags, plan, the
switches that live outside the flag dict, the leagues opt-out) into a set of names; a step
exists for the student only when every name in its `requires` is in that set, and every
"!name" in it is not (the variant shown while a feature is off). Role never adds anything:
no step targets an admin, moderator or diagnostics page, so an admin gets exactly the
student payload.

`payload()` filters the registry by those capabilities, translates every text and fills
the plural-safe config facts ({price}, {league_size}, ...). It is pure: the translate
function and the facts are handed in by app.py, which also caches it per
(capabilities, language, facts).
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Iterable, Mapping

from . import registry
from .registry import CONTAINERS, GROUPS, HUB_URL, OFFER, REGISTRY_VERSION, SECTIONS, UI, Section, Step

Translate = Callable[[str, str], str]
DEFAULT_WAIT_MS = Step.__dataclass_fields__["wait_ms"].default


def capabilities(flags: Mapping[str, Any], *, chat_images: bool = False, continue_learning: bool = False,
                 leagues: bool = False, premium: bool = False, leagues_opted_in: bool = True) -> frozenset[str]:
    """Every truthy feature flag, plus the switches that are not in the flag dict, the plan
    and `leagues_opted_in` (leagues on and the student has not switched them off in
    Settings: only then does the profile show a league card)."""

    caps = {str(name) for name, value in flags.items() if value}
    if flags.get("flashcard_pdf_import") or flags.get("flashcard_image_import"):
        caps.add("flashcard_import")
    # A set offers a choice of study modes only when one besides Flashcards is on.
    if any(flags.get(name) for name in ("flashcard_learn_mode", "flashcard_test_mode", "flashcard_match_game",
                                         "flashcard_blast_game", "flashcard_blocks_game")):
        caps.add("flashcard_modes")
    if chat_images:
        caps.add("chat_images")
    if continue_learning:
        caps.add("continue_learning")
    if leagues:
        caps.add("leagues")
        if leagues_opted_in:
            caps.add("leagues_opted_in")
    caps.add("premium" if premium else "free")
    return frozenset(caps)


def meets(requires: Iterable[str], caps: Iterable[str]) -> bool:
    """Every plain name is a capability the student has; every "!name" one they do not."""

    caps = frozenset(caps)
    return all((name[1:] not in caps) if name.startswith("!") else (name in caps) for name in requires)


def step_visible(step: Step, caps: Iterable[str]) -> bool:
    return meets(step.requires, caps)


def visible_steps(section: Section, caps: Iterable[str]) -> list[Step]:
    caps = frozenset(caps)
    if not meets(section.requires, caps):
        return []
    return [step for step in section.steps if step_visible(step, caps)]


def section_visible(section: Section, caps: Iterable[str]) -> bool:
    return bool(visible_steps(section, caps))


def visible_sections(caps: Iterable[str]) -> list[Section]:
    caps = frozenset(caps)
    return [section for section in SECTIONS if section_visible(section, caps)]


def visible_map(caps: Iterable[str]) -> dict[str, list[str]]:
    """{section id: [visible step ids in order]} for every visible section (the quick tour
    and every chapter)."""

    caps = frozenset(caps)
    return {section.id: [step.id for step in visible_steps(section, caps)] for section in visible_sections(caps)}


def chapters_for_page(page: str, caps: Iterable[str]) -> list[Section]:
    """The visible chapters that belong to one page, in registry order (the first one whose
    anchor shows starts by itself first)."""

    caps = frozenset(caps)
    return [section for section in visible_sections(caps) if section.kind == "chapter" and section.page == page]


def visible_groups(caps: Iterable[str]) -> list[tuple[registry.Group, list[Section]]]:
    """The hub's feature groups with their visible chapters (a group without one is left out)."""

    caps = frozenset(caps)
    shown = [section for section in visible_sections(caps) if section.kind == "chapter"]
    out = []
    for item in GROUPS:
        members = [section for section in shown if section.group == item.id]
        if members and meets(item.requires, caps):
            out.append((item, members))
    return out


def registry_order(section_id: str) -> dict[str, int]:
    """Position of every step of a section in the registry (visible or not)."""

    entry = registry.section(section_id)
    return {step.id: index for index, step in enumerate(entry.steps)} if entry else {}


def can_do(caps: Iterable[str]) -> list[tuple[str, str]]:
    """The hub's feature lines this student can use: (icon, English msgid), one per key."""

    caps = frozenset(caps)
    shown: dict[str, tuple[str, str]] = {}
    for key, icon, msgid, requires in registry.CAN_DO:
        if key not in shown and meets(requires, caps):
            shown[key] = (icon, msgid)
    return list(shown.values())


def signature(caps: Iterable[str], language: str, static_version: str = "", hub: str = HUB_URL,
              facts: Mapping[str, Any] | None = None) -> str:
    """Changes whenever the payload would: registry version, language, assets, hub, the
    visible step ids and the facts. tour.js caches the payload per tab under it."""

    ids = ",".join(step for steps in visible_map(caps).values() for step in steps)
    known = facts or {}
    fact_text = "&".join(f"{key}={known[key]}" for key in sorted(known))
    raw = f"{REGISTRY_VERSION}|{language}|{static_version}|{hub}|{fact_text}|{ids}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


class _Facts(dict):
    def __missing__(self, key: str) -> str:   # a placeholder the server does not fill stays as it is
        return "{" + key + "}"


def fill(text: str, facts: Mapping[str, Any]) -> str:
    if "{" not in text:
        return text
    try:
        return text.format_map(_Facts({key: value for key, value in facts.items()}))
    except (ValueError, IndexError, KeyError, AttributeError):
        return text


def payload(caps: Iterable[str], language: str, *, translate: Translate, facts: Mapping[str, Any] | None = None,
            static_version: str = "", hub: str = HUB_URL) -> dict[str, Any]:
    """The JSON tour.js runs on (shape documented in docs/TUTORIAL.md)."""

    caps = frozenset(caps)
    facts = dict(facts or {})

    def text(msgid: str) -> str:
        return fill(translate(msgid, language), facts) if msgid else ""

    sections_out: list[dict[str, Any]] = []
    steps_out: list[dict[str, Any]] = []
    for section in visible_sections(caps):
        steps = visible_steps(section, caps)
        order = registry_order(section.id)
        sections_out.append({"id": section.id, "kind": section.kind, "title": text(section.title),
                             "icon": section.icon, "page": section.page, "anchor": section.anchor,
                             "group": section.group, "steps": [step.id for step in steps]})
        for step in steps:
            steps_out.append(_step_json(step, section.id, order[step.id], text, hub))
    ui: dict[str, Any] = {key: text(msgid) for key, msgid in UI.items()}
    ui["offer"] = {key: text(msgid) for key, msgid in OFFER.items()}
    return {
        "v": int(REGISTRY_VERSION),
        "sig": signature(caps, language, static_version, hub, facts),
        "language": language,
        "hub": hub,
        "ui": ui,
        "sections": sections_out,
        "steps": steps_out,
        "containers": [{"inside": item.inside, "opener": item.opener, "closer": item.closer,
                        "toggle": item.toggle, "text": text(item.text)} for item in CONTAINERS],
    }


def _step_json(step: Step, section_id: str, order: int, text: Callable[[str], str], hub: str) -> dict[str, Any]:
    create = None
    if step.create:
        label, target = step.create[0], step.create[1]
        usable = bool(hub) if target == HUB_URL else target.startswith("/")
        create = [text(label), target] if usable else None
    out: dict[str, Any] = {
        "id": step.id, "section": section_id, "order": order, "page": step.page, "kind": step.kind,
        "advance": step.advance if step.kind == "action" else "next",
        "targets": list(step.targets), "title": text(step.title), "text": text(step.text),
    }
    # Optional fields only when they differ from the engine's defaults (tour-rules.js
    # normalizeStep): the payload travels once per tab, so it stays small.
    optional = {"also": list(step.also), "busy": step.busy, "create": create,
                "shortcut": list(step.shortcut) if step.shortcut else None, "icon": step.icon}
    out.update({key: value for key, value in optional.items() if value})
    if step.viewport != "any":
        out["viewport"] = step.viewport
    if int(step.wait_ms) != DEFAULT_WAIT_MS:
        out["wait"] = int(step.wait_ms)
    return out

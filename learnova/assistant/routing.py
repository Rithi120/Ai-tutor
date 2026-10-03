"""Which model answers a given assistant style.

Students choose a *style* (General, Research, Study coach, Plain explanation); the owner
decides which provider and model each style runs on. A student never names a model.

Resolution order, most specific first:
    ASSISTANT_DEEP_MODEL_<PRESET> / ASSISTANT_MODEL_<PRESET>   (per style)
    ASSISTANT_DEEP_MODEL / ASSISTANT_MODEL                     (global)
    GROQ_ANALYSIS_MODEL / GROQ_TUTOR_MODEL                     (the app's tiers)

Pure and Flask-free, like the rest of this package: the application passes its config.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .presets import PRESET_NAMES, normalize_preset


def preset_setting_names(preset: str, *, deep: bool) -> tuple[str, str]:
    """The per-style setting and its global fallback, for a given depth."""

    base = "ASSISTANT_DEEP_MODEL" if deep else "ASSISTANT_MODEL"
    return f"{base}_{normalize_preset(preset).upper()}", base


@dataclass(frozen=True)
class AssistantModelChoice:
    model: str
    setting: str          # which configuration key decided it
    mapped: bool          # True when a per-style setting won


def assistant_model_for(preset: str | None, deep: bool, config: Mapping[str, Any]) -> AssistantModelChoice:
    specific, general = preset_setting_names(preset or "", deep=deep)
    value = str(config.get(specific) or "").strip()
    if value:
        return AssistantModelChoice(model=value, setting=specific, mapped=True)
    value = str(config.get(general) or "").strip()
    if value:
        return AssistantModelChoice(model=value, setting=general, mapped=False)
    tier = "GROQ_ANALYSIS_MODEL" if deep else "GROQ_TUTOR_MODEL"
    return AssistantModelChoice(model=str(config.get(tier) or ""), setting=tier, mapped=False)


def preset_model_settings() -> list[str]:
    """Every per-style setting name, for configuration to read and documentation to list."""

    names: list[str] = []
    for preset in PRESET_NAMES:
        for deep in (False, True):
            names.append(preset_setting_names(preset, deep=deep)[0])
    return names

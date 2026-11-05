"""The interactive tutorial: a quick tour of every function, then short feature chapters that
start by themselves the first time a student uses a feature (docs/TUTORIAL.md).

registry.py    the steps themselves (pure data, English msgids) - the single source of truth
visibility.py  capabilities -> visible sections/steps -> the translated JSON payload + its signature
progress.py    pure rules for the stored progress (validation, percent, resume, section states)

The browser side is static/js/tour.js (DOM) and static/js/tour-rules.js (pure rules); it
never contains step texts or feature gating. docs/TUTORIAL.md describes the whole system.
"""

from . import progress, registry, visibility

__all__ = ["progress", "registry", "visibility"]

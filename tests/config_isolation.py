"""Put shared app state back when a test ends, so no setUp leaks into later modules.

Every test module shares one Flask app for the whole session. A setUp that changed
`app.config` and never put it back changed it for every module pytest reached afterwards:
the suite then passed or failed depending on the order the files ran in (the limiter left
off in tests/test_project_extraction.py broke test_chat_photo_live_fixes.py only in the full
alphabetical pass). No app import here, so a test module's environment set-up order is
untouched.
"""

from __future__ import annotations

import unittest
from typing import Any, MutableMapping

_ABSENT = object()


def keep_config(test: unittest.TestCase, config: MutableMapping[str, Any], *names: str) -> None:
    """Restore `names` in `config` to their current values (absent stays absent) when
    `test` ends. Call it before the setUp changes them; cleanups run even after a failure."""

    saved = {name: config.get(name, _ABSENT) for name in names}

    def restore() -> None:
        for name, value in saved.items():
            if value is _ABSENT:
                config.pop(name, None)
            else:
                config[name] = value

    test.addCleanup(restore)


def keep_attribute(test: unittest.TestCase, owner: Any, name: str) -> None:
    """Restore `owner.name` to its current value when `test` ends."""

    test.addCleanup(setattr, owner, name, getattr(owner, name))

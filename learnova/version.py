"""The application version: the one place it is written down.

Bump APP_VERSION when a release goes out. Settings -> Subscription shows it, with the
deployed commit appended when the host provides one (Render sets RENDER_GIT_COMMIT; any
other host can set GIT_COMMIT), so a screenshot from a student says exactly what ran.
"""

from __future__ import annotations

import os
import re

APP_VERSION = "1.0.0"


def app_version() -> str:
    commit = (os.getenv("RENDER_GIT_COMMIT") or os.getenv("GIT_COMMIT") or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", commit):
        return f"{APP_VERSION} ({commit[:7].lower()})"
    return APP_VERSION

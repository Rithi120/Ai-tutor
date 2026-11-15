"""The league countdown in a real browser (static/js/profile.js as it ships).

The server renders the remaining time as text; the script keeps it ticking and, once the
week is over, says so instead of "1 min" forever and reloads once (guarded per week end in
sessionStorage). Runs in Chrome/Edge through Playwright (tests/js_browser/harness.py);
skipped when Playwright or a Chromium-based browser is missing.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_js_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from tests.js_browser import harness  # noqa: E402

BROWSER = {}

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<script nonce="__NONCE__">window.LEARNOVA_I18N = {countdownDays: "{days} d {hours} h",
  countdownHours: "{hours} h {minutes} min", countdownMinutes: "{minutes} min"};</script></head>
<body>
<p id="ended" data-countdown="__PAST__" data-countdown-label="Week ends in {time}"
   data-countdown-ended="The week has ended. Results in a moment." data-countdown-reload>Week ends in 1 min</p>
<p id="running" data-countdown="__FUTURE__" data-countdown-label="Week ends in {time}"
   data-countdown-ended="The week has ended. Results in a moment." data-countdown-reload>Week ends in 2 d 3 h</p>
<span id="challenge" data-countdown="__PAST__" data-countdown-label="Resets in {time}"></span>
<script nonce="__NONCE__" type="module">
  import "/static/js/profile.js";
  window.__ready = true;
</script>
</body></html>"""


def setUpModule():
    if sync_playwright is None:
        raise unittest.SkipTest("Playwright is not installed (run with R:/ai_tutor/.venv/Scripts/python)")
    playwright = sync_playwright().start()
    try:
        BROWSER["browser"] = harness.launch(playwright)
    except SystemExit as error:
        playwright.stop()
        raise unittest.SkipTest(str(error))
    BROWSER["playwright"] = playwright


def tearDownModule():
    if BROWSER:
        BROWSER["browser"].close()
        BROWSER["playwright"].stop()
        BROWSER.clear()


class LeagueCountdownTests(unittest.TestCase):
    def open(self):
        now = datetime.now(timezone.utc)
        html = (PAGE.replace("__NONCE__", harness.NONCE)
                .replace("__PAST__", (now - timedelta(seconds=20)).isoformat())
                .replace("__FUTURE__", (now + timedelta(days=2, hours=3, minutes=5)).isoformat()))
        page = harness.open_page(BROWSER["browser"], html, katex="none")
        self.addCleanup(page.context.close)
        return page

    def test_an_ended_week_says_so_and_schedules_one_reload(self):
        page = self.open()
        self.assertEqual(page.text_content("#ended"), "The week has ended. Results in a moment.")
        self.assertEqual(page.text_content("#running"), "Week ends in 2 d 3 h")
        # Countdowns without an "ended" text (challenges) keep their old behaviour.
        self.assertEqual(page.text_content("#challenge"), "Resets in 1 min")
        keys = page.evaluate("() => Object.keys(window.sessionStorage)")
        self.assertEqual(len([key for key in keys if key.startswith("learnova.leagueReload.")]), 1)


if __name__ == "__main__":
    unittest.main()

r"""PF_FIX2 F9: a display formula wider than the lesson column at 390 px scrolls in a named,
focusable region with an edge cue, as the chat's do (run with R:/ai_tutor/.venv/Scripts/python).

Live 2026-11-02 (scratchpad prod/live_final.md §2, shots/P_390_dark_03.png): "… ⟹ y = 1" was cut at
the right edge of its scroller with nothing to say there was more. The chat marks its scrolling
formulas (assistant.js markScrollers); the lesson page now does the same through math.js
markFormulaScrollers, and learnova-content.css fades the edge that has more behind it.
"""

import os
import tempfile
import unittest
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
WIDE = r"$$\frac{x+3}{4} = \frac{2x-1}{3} \;\Longrightarrow\; 3(x+3) = 4(2x-1) \;\Longrightarrow\; 3x + 9 = 8x - 4 \;\Longrightarrow\; y = 1$$"
PAGE = """<!doctype html>
<html lang="de" data-theme="__THEME__">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Lesson formula harness</title>
<link rel="stylesheet" href="/static/styles.css">
<link rel="stylesheet" href="/static/css/learnova-tokens.css">
<link rel="stylesheet" href="/static/css/learnova-components.css">
<link rel="stylesheet" href="/static/css/learnova-layouts.css">
<link rel="stylesheet" href="/static/css/learnova-study.css">
<link rel="stylesheet" href="/static/css/learnova-pages.css">
<link rel="stylesheet" href="/static/css/learnova-content.css">
<link rel="stylesheet" href="/static/css/learnova-dark.css">
<link rel="stylesheet" href="/static/css/learnova-responsive.css">
</head>
<body>
<!-- Outside <main>, so only the test's own renderMath call typesets it (math.js AUTO_ROOTS). -->
<div class="page-shell">
  <section id="lessonView" class="lesson-view">
    <article class="lesson-part"><p>Short: $$x = 2$$</p><p>__WIDE__</p></article>
  </section>
</div>
<script nonce="__NONCE__" type="module">
  import { markFormulaScrollers, renderMath } from "/static/js/math.js";
  window.__api = { markFormulaScrollers, renderMath };
  window.__ready = true;
</script>
</body>
</html>"""

MARK = """async (label) => {
  const root = document.querySelector("#lessonView");
  await window.__api.renderMath(root);
  const count = window.__api.markFormulaScrollers(root, label);
  await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
  const boxes = [...root.querySelectorAll(":is(.katex-display, .ln-math-display)")].map((node) => ({
    scrolls: node.scrollWidth > node.clientWidth + 1,
    scroller: node.dataset.scroller || "",
    tabindex: node.getAttribute("tabindex"),
    role: node.getAttribute("role"),
    label: node.getAttribute("aria-label"),
    mask: getComputedStyle(node).maskImage || getComputedStyle(node).webkitMaskImage || "",
    animations: node.getAnimations().map((animation) => animation.animationName || ""),
  }));
  return { count, boxes, timelines: CSS.supports("animation-timeline: scroll()"),
           pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 1 };
}"""


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


class LessonFormulaScrollTests(unittest.TestCase):
    def page(self, *, width=390, theme="light"):
        html = PAGE.replace("__NONCE__", harness.NONCE).replace("__THEME__", theme).replace("__WIDE__", WIDE)
        page = harness.open_page(BROWSER["browser"], html, viewport=(width, 844), theme=theme)
        self.addCleanup(page.context.close)
        return page

    def test_a_wide_formula_at_390_px_is_a_named_region_with_an_edge_cue(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                result = self.page(theme=theme).evaluate(MARK, "Breite Formel, seitlich scrollbar")
                scrolling = [box for box in result["boxes"] if box["scrolls"]]
                self.assertEqual(result["count"], 1, result)
                self.assertEqual(len(scrolling), 1, result)
                box = scrolling[0]
                self.assertEqual((box["scroller"], box["tabindex"], box["role"]), ("1", "0", "region"))
                self.assertEqual(box["label"], "Breite Formel, seitlich scrollbar")
                if result["timelines"]:
                    self.assertIn("linear-gradient", box["mask"], "the edge with more behind it fades")
                self.assertFalse(result["pageOverflow"], "the formula scrolls, not the page")
                fitting = [box for box in result["boxes"] if not box["scrolls"]]
                self.assertTrue(fitting)
                for box in fitting:
                    self.assertEqual((box["scroller"], box["tabindex"], box["role"]), ("", None, None))

    def test_a_formula_that_fits_again_loses_the_region(self):
        page = self.page()
        self.assertEqual(page.evaluate(MARK, "Breite Formel")["count"], 1)
        page.set_viewport_size({"width": 1600, "height": 900})
        page.wait_for_timeout(50)
        after = page.evaluate("""() => {
          const root = document.querySelector("#lessonView");
          const count = window.__api.markFormulaScrollers(root, "Breite Formel");
          return { count, marked: root.querySelectorAll("[data-scroller], [tabindex], [role=region]").length };
        }""")
        self.assertEqual(after, {"count": 0, "marked": 0})


if __name__ == "__main__":
    unittest.main()

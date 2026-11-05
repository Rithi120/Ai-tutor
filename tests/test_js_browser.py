r"""The chat renderer in a real browser: math-protect.js, markdown.js, math.js, dom.js,
rich-history.js and code-highlight.js as they ship, with the app's stylesheets and CSP.

Node is not installed, so before this nothing ran the JavaScript: the security property
tests/js/assistant-render.test.mjs claimed to lock had never executed (and would have
failed). Here the modules run in Chrome/Edge through Playwright (tests/js_browser/harness.py),
and so do the tests/js/*.test.mjs files themselves.

Every rendering case goes through the three surfaces that show a chat reply - the
assistant thread (assistant.js messageNode), the lesson tutor chat (app.js addChatMessage)
and the lesson history page (rich-history.js) - and must come out the same on all three.
KaTeX is a stub by default (tests/js_browser/harness.py says what it rejects); one class
uses the real KaTeX 0.16.11 when LEARNOVA_KATEX_DIR (or LEARNOVA_JS_TESTS_ONLINE=1) is set.

Skipped when Playwright or a Chromium-based browser is missing (Store Python has no
Playwright: run with R:/ai_tutor/.venv/Scripts/python).
"""

import json
import os
import socket
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

ROOT = Path(__file__).resolve().parents[1]
BS = "\\"
BROWSER = {}


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


class _BrowserCase(unittest.TestCase):
    SURFACES = ("assistant", "tutor", "history")

    def page(self, **options):
        page = harness.open_page(BROWSER["browser"], **options)
        self.addCleanup(page.context.close)
        return page

    def render(self, text, **options):
        highlight = options.pop("highlight", False)
        return harness.render_markdown(self.page(**options), text, highlight=highlight)

    def each(self, result):
        for surface in self.SURFACES:
            with self.subTest(surface=surface):
                yield result[surface]

    def assert_formulas(self, text, expected, *, kind="katex", **options):
        """`expected` = [(tex, display)] in order, on every surface; nothing left raw."""

        result = self.render(text, **options)
        for view in self.each(result):
            self.assertEqual([(item["tex"], item["display"]) for item in view["formulas"]], expected, view["html"])
            self.assertEqual({item["kind"] for item in view["formulas"]} - {kind}, set(), view["formulas"])
            self.assertEqual(view["slots"], 0)
            for raw in (BS + "begin", BS + "frac", BS + "(", BS + "[", "$$"):
                self.assertNotIn(raw, view["text"], f"raw LaTeX left in the text: {view['text']!r}")
        return result


class MathRenderTests(_BrowserCase):
    """The spec's LaTeX list, one case each (spec §12 "LaTeX tests")."""

    def test_fraction(self):
        self.assert_formulas(r"Die Hälfte ist $\frac{1}{2}$.", [(r"\frac{1}{2}", False)])

    def test_square_root(self):
        self.assert_formulas(r"Die Wurzel \(\sqrt{x}\) ist für x ≥ 0 definiert.", [(r"\sqrt{x}", False)])

    def test_power_delimited_and_bare(self):
        self.assert_formulas("Es gilt $x^2 \\geq 0$.", [(r"x^2 \geq 0", False)])
        result = self.render("Es gilt x^2 ≥ 0 für alle x.")
        for view in self.each(result):
            self.assertIn("<sup>2</sup>", view["html"], "a bare power is raised, the caret never shows")

    def test_subscripts(self):
        self.assert_formulas(r"Die Lösungen $x_1$ und $x_2$, das Element \(a_{ij}\).",
                             [("x_1", False), ("x_2", False), ("a_{ij}", False)])

    def test_greek_letters(self):
        self.assert_formulas(r"Winkel: $\alpha + \beta + \gamma = 180^\circ$ und \(\Delta x = x_2 - x_1\).",
                             [(r"\alpha + \beta + \gamma = 180^\circ", False), (r"\Delta x = x_2 - x_1", False)])

    def test_matrix_over_several_lines(self):
        tex = "A = \\begin{pmatrix}\n1 & 2 \\\\\n3 & 4\n\\end{pmatrix}"
        self.assert_formulas(f"Die Matrix:\n$$\n{tex}\n$$", [(tex, True)])

    def test_multi_line_derivation_in_aligned(self):
        tex = "\\begin{aligned}\n2x + 3 &= 7 \\\\\n2x &= 4 \\\\\nx &= 2\n\\end{aligned}"
        self.assert_formulas(f"**Schritt für Schritt**\n\n\\[\n{tex}\n\\]", [(tex, True)])

    def test_system_of_equations(self):
        tex = "\\begin{cases}\nx + y = 5 \\\\\nx - y = 1\n\\end{cases}"
        self.assert_formulas(f"Das Gleichungssystem:\n\n\\[\n{tex}\n\\]\n\nAddiere beide.", [(tex, True)])

    def test_bare_environment_without_delimiters(self):
        tex = "\\begin{aligned}\nx &= 1 \\\\\ny &= 2\n\\end{aligned}"
        self.assert_formulas(f"Also:\n{tex}\nFertig.", [(tex, True)])

    def test_inline_formulas_with_spaces_and_digits_only(self):
        # The configured chat models (gpt-oss) pad \( \) with spaces; \(8\) is a formula too.
        self.assert_formulas(r"Setze \( a = 2 \), \( b = -4 \) und \(8\) ein.",
                             [("a = 2", False), ("b = -4", False), ("8", False)])

    def test_display_formula_on_one_line(self):
        self.assert_formulas(r"\[ \frac{3}{4} + \frac{1}{8} = \frac{7}{8} \]",
                             [(r"\frac{3}{4} + \frac{1}{8} = \frac{7}{8}", True)])

    def test_display_formula_indented_under_a_list_item(self):
        result = self.assert_formulas("- Bruch kürzen:\n  \\[\n  \\dfrac{12}{18} = \\dfrac{2}{3}\n  \\]\n- Fertig.",
                                      [(r"\dfrac{12}{18} = \dfrac{2}{3}", True)])
        for view in self.each(result):
            self.assertEqual(view["lists"], [{"tag": "ul", "start": None, "depth": 0, "items": 2}])

    def test_numbered_steps_keep_their_numbering_around_formula_lines(self):
        indented = ("1. Klammer auflösen:\n   \\[ 2(x+3) = 2x + 6 \\]\n2. Zusammenfassen:\n   \\[ 2x + 6 = 10 \\]\n"
                    "3. Nach \\(x\\) auflösen:\n   \\[ x = 2 \\]")
        result = self.assert_formulas(indented, [("2(x+3) = 2x + 6", True), ("2x + 6 = 10", True), ("x", False),
                                                 ("x = 2", True)])
        for view in self.each(result):
            self.assertEqual(view["lists"], [{"tag": "ol", "start": None, "depth": 0, "items": 3}])
        unindented = "1. Erst:\n\\[ a = 1 \\]\n2. Dann:\n\\[ b = 2 \\]\n3. Fertig."
        for view in self.each(self.render(unindented)):
            self.assertEqual(view["lists"], [{"tag": "ol", "start": None, "depth": 0, "items": 3}])
        # A paragraph between two steps ends the list; the next one goes on at 2, not at 1.
        for view in self.each(self.render("1. Erst das.\n\nDazwischen ein Satz.\n\n2. Dann das.")):
            self.assertEqual([(item["tag"], item["start"]) for item in view["lists"]], [("ol", None), ("ol", "2")])

    def test_formulas_in_a_table_keep_their_pipes(self):
        text = ("| Ausdruck | Wert |\n|---|---|\n| $|-3|$ | \\(3\\) |\n| \\(\\lvert x-2 \\rvert\\) | Abstand zu 2 |")
        result = self.assert_formulas(text, [("|-3|", False), ("3", False), (r"\lvert x-2 \rvert", False)])
        for view in self.each(result):
            self.assertIn("table", view["tags"])
            self.assertEqual(view["html"].count("<td>"), 4, view["html"])

    def test_a_star_inside_formulas_is_not_emphasis(self):
        result = self.assert_formulas("Rechne $3 * 4 = 12$ und dann $12 * 2 = 24$.",
                                      [("3 * 4 = 12", False), ("12 * 2 = 24", False)])
        for view in self.each(result):
            self.assertNotIn("em", view["tags"])

    def test_a_minus_line_inside_display_math_is_not_a_list(self):
        result = self.assert_formulas("$$\n- 3x + 6 = 0\n$$", [("- 3x + 6 = 0", True)])
        for view in self.each(result):
            self.assertEqual(view["lists"], [])

    def test_prices_stay_text(self):
        for view in self.each(self.render("Das kostet zwischen $5 und $10.")):
            self.assertEqual(view["formulas"], [])
            self.assertIn("$5 und $10", view["text"])
        result = self.render(r"Das kostet \$5, und $x=5$.")
        for view in self.each(result):
            self.assertEqual([item["tex"] for item in view["formulas"]], ["x=5"])
            self.assertIn("Das kostet $5, und ⟦F⟧.", view["text"])
            self.assertIn('<span class="ln-dollar">$</span>5', view["html"])

    def test_a_long_inline_formula_renders(self):
        long_tex = " + ".join(f"\\frac{{{i}}}{{{i + 1}}}" for i in range(1, 16))
        self.assertGreater(len(long_tex), 200)
        self.assert_formulas(f"Summe: \\({long_tex}\\).", [(long_tex, False)])
        # A single $ keeps the unchanged strict price rule (at most 200 characters).
        for view in self.each(self.render(f"Summe: ${long_tex}$.")):
            self.assertEqual(view["formulas"], [])

    def test_malformed_formulas_show_their_source(self):
        cases = [(r"Kaputt: $\frac{1}{2$ und weiter.", r"\frac{1}{2", False),
                 (r"Kaputt: \(\frac{a}{\) Ende.", r"\frac{a}{", False),
                 (r"$$\frac{a}{b$$", r"\frac{a}{b", True)]
        for text, tex, display in cases:
            with self.subTest(text=text):
                result = self.render(text)
                for view in self.each(result):
                    self.assertEqual(len(view["formulas"]), 1, view["html"])
                    formula = view["formulas"][0]
                    self.assertEqual((formula["kind"], formula["tag"], formula["display"]), ("source", "code", display))
                    self.assertEqual(formula["text"], ("\\[" if display else "\\(") + tex + ("\\]" if display else "\\)"))
                    self.assertNotIn("katex", view["html"])

    def test_katex_is_called_safely(self):
        result = self.render(r"\(\frac{1}{2}\) und \[x\]")
        options = [call["options"] for call in result["katexCalls"]]
        self.assertTrue(options)
        for option in options:
            self.assertEqual({key: option[key] for key in ("throwOnError", "strict", "trust", "maxSize", "maxExpand",
                                                           "output")},
                             {"throwOnError": True, "strict": "ignore", "trust": False, "maxSize": 20,
                              "maxExpand": 500, "output": "htmlAndMathml"})
        self.assertEqual(sorted({option["displayMode"] for option in options}), [False, True])

    def test_offline_formulas_are_readable_or_shown_as_source(self):
        text = (r"Brüche $\frac{1}{2}$, $\frac{x^{2}}{\left(a+b\right)}$, \(\sqrt{x}\), $2^5 = 32$ und "
                "\n$$A = \\begin{pmatrix} 1 & 2 \\\\ 3 & 4 \\end{pmatrix}$$\n" + r"und $\boxed{x = 3}$.")
        result = self.render(text, katex="none")
        for view in self.each(result):
            kinds = [item["kind"] for item in view["formulas"]]
            self.assertEqual(kinds, ["fallback", "fallback", "fallback", "fallback", "source", "source"], view["html"])
            readable = [item["text"] for item in view["formulas"] if item["kind"] == "fallback"]
            self.assertEqual(readable, ["(1)/(2)", "(x2)/((a+b))", "√(x)", "25 = 32"])
            for item in view["formulas"]:
                if item["kind"] == "source":
                    self.assertTrue(item["text"].startswith(("\\(", "\\[")), item)
                else:
                    self.assertNotIn("frac", item["text"])
            self.assertNotIn("frac{", view["text"])

    def test_chemistry_loads_mhchem(self):
        requested = []
        page = self.page(requests=requested)
        result = harness.render_markdown(page, r"Reaktion: $\ce{2H2 + O2 -> 2H2O}$")
        self.assertTrue(any("katex@0.16.11/dist/contrib/mhchem.min.js" in url for url in requested), requested)
        for view in self.each(result):
            self.assertEqual([(item["tex"], item["kind"]) for item in view["formulas"]],
                             [(r"\ce{2H2 + O2 -> 2H2O}", "katex")])
        # Without mhchem (offline) the reaction is shown as its source, not as "ce2H2".
        for view in self.each(self.render(r"Reaktion: $\ce{2H2 + O2 -> 2H2O}$", katex="none")):
            self.assertEqual([item["kind"] for item in view["formulas"]], ["source"])

    def test_streaming_prefixes_never_hold_half_a_formula(self):
        reply = ("Die Lösungsformel lautet\n\n\\[\nx_{1,2}= \\frac{-b\\pm\\sqrt{b^{2}-4ac}}{2a}\n\\]\n\n"
                 "Setze \\( a = 2 \\) und $b = -4$ ein. Das kostet $5.\n\n"
                 "1. Erst:\n   \\[\n   \\begin{aligned}\n   2x &= 4 \\\\\n   x &= 2\n   \\end{aligned}\n   \\]\n"
                 "2. Dann `code $x$` und\n```python\nprint('$y$')\n```\n"
                 "| a | b |\n|---|---|\n| $|x|$ | \\(8\\) |\n\\begin{cases} x = 1 \\\\ y = 2 \\end{cases}\nEnde \\$3.")
        check = harness.eval_module(self.page(), "/static/js/math-protect.js", r"""(mod) => {
          const text = %s;
          const full = mod.protectMath(text);
          const ranges = [];
          let position = 0;
          for (let i = 0; i < full.text.length; i += 1) {
            const char = full.text[i];
            if (char === "\uE000") {
              const close = full.text.indexOf("\uE001", i);
              const span = full.spans[Number(full.text.slice(i + 1, close))];
              ranges.push([position, position + span.raw.length]);
              position += span.raw.length;
              i = close;
            } else {
              position += char === "\uE002" ? 2 : 1;
            }
          }
          const problems = [];
          for (let end = 0; end <= text.length; end += 1) {
            const prefix = text.slice(0, end);
            const { stable, pending } = mod.splitStreamingSource(prefix);
            if (stable + pending !== prefix) problems.push(["split", end]);
            if (ranges.some(([from, to]) => stable.length > from && stable.length < to)) problems.push(["inside", end, stable.slice(-30)]);
            const spans = mod.protectMath(stable).spans;
            spans.forEach((span, index) => {
              if (!full.spans[index] || full.spans[index].raw !== span.raw) problems.push(["span", end, span.raw]);
            });
          }
          return { problems: problems.slice(0, 10), spans: full.spans.length,
                   whole: mod.splitStreamingSource(text).stable === text };
        }""" % json.dumps(reply))
        self.assertEqual(check["problems"], [])
        self.assertEqual(check["spans"], 7)
        self.assertTrue(check["whole"], "a finished reply is stable as a whole")

    def test_history_renders_exactly_like_a_live_reply(self):
        reply = ("### Lösung für \\(x^2 = 9\\)\n\nEs gilt:\n\n\\[\nx = \\pm 3\n\\]\n\n| A | B |\n|---|---|\n| $|x|$ | 3 |\n\n"
                 "- Probe: $3^2 = 9$\n  - und $(-3)^2 = 9$\n\nMehr: [Wurzel](https://de.wikipedia.org/wiki/Wurzel_(Mathematik)).")
        result = self.render(reply)
        self.assertEqual(result["assistant"]["html"], result["tutor"]["html"])
        self.assertEqual(result["tutor"]["html"], result["history"]["html"])
        self.assertEqual(len(result["assistant"]["formulas"]), 5)
        # The live and history call sites are the very sequences the harness replays.
        assistant_js = (ROOT / "static/js/assistant.js").read_text(encoding="utf-8")
        app_js = (ROOT / "static/app.js").read_text(encoding="utf-8")
        rich = (ROOT / "static/js/rich-history.js").read_text(encoding="utf-8")
        self.assertIn("body.innerHTML = renderMarkdown(message.content);", assistant_js)
        self.assertIn("renderMath(body);", assistant_js)
        self.assertIn("message.innerHTML = renderMarkdown(text);", app_js)
        self.assertIn("renderMath(message);", app_js)
        self.assertIn("element.innerHTML = renderMarkdown(source);", rich)
        self.assertIn("renderMath(element);", rich)


class MarkdownTests(_BrowserCase):
    def test_small_headings(self):
        for view in self.each(self.render("##### Kleine Überschrift\n\n###### Noch kleiner")):
            self.assertEqual(view["html"], "<h6>Kleine Überschrift</h6>\n<h6>Noch kleiner</h6>")

    def test_nested_lists(self):
        for view in self.each(self.render("- Punkt A\n  - Unterpunkt A1\n  - Unterpunkt A2\n- Punkt B")):
            self.assertEqual(view["lists"], [{"tag": "ul", "start": None, "depth": 0, "items": 2},
                                             {"tag": "ul", "start": None, "depth": 1, "items": 2}])

    def test_markdown_links_and_code(self):
        result = self.render("Mehr: [Bruchrechnung](https://de.wikipedia.org/wiki/Bruchrechnung).\n"
                             "```python\nprint('https://x.test', \"$x$\")\n```")
        for view in self.each(result):
            self.assertEqual(view["anchors"], [{"href": "https://de.wikipedia.org/wiki/Bruchrechnung",
                                                "attrs": ["href", "target", "rel"], "text": "Bruchrechnung"}])
            self.assertIn("<code>print('https://x.test', \"$x$\")</code>", view["html"])
            self.assertEqual(view["formulas"], [])

    def test_code_blocks_are_highlighted_only_when_the_highlighter_loads(self):
        text = "```python\nprint(1/2)  # <b>\n```"
        result = self.render(text, hljs="stub", highlight=True)
        for view in self.each(result):
            self.assertIn('<span class="hljs-keyword">print(1/2)  # &lt;b&gt;</span>', view["html"])
            self.assertNotIn("<b>", view["html"])
        # Without the script (blocked, offline) the code stays plain, readable text.
        page = self.page(hljs="none")
        harness.render_markdown(page, text)
        page.wait_for_timeout(400)
        blocks = page.evaluate("[...document.querySelectorAll('pre.asst-code code')].map((code) => code.innerHTML)")
        self.assertEqual(blocks, ["print(1/2)  # &lt;b&gt;"] * 3)


class SecurityTests(_BrowserCase):
    """Nothing a model writes becomes markup (audit_math §4.1: the HIGH attribute injection)."""

    def test_a_quote_in_a_url_cannot_add_attributes(self):
        for payload in ('Link: https://x.test/"style="position:fixed;inset:0;background:red;z-index:9999"x',
                        'Link: https://x.test/"onmouseover="window.__pwned=1"x',
                        "Link: [klick](https://x.test/a\"onmouseover=\"window.__pwned=1)"):
            with self.subTest(payload=payload):
                result = self.render(payload)
                for view in self.each(result):
                    self.assertEqual({attribute for anchor in view["anchors"] for attribute in anchor["attrs"]}
                                     - {"href", "target", "rel"}, set(), view["html"])
                    self.assertNotIn("style", view["attributes"])
                    self.assertNotIn("onmouseover", view["attributes"])
                    for anchor in view["anchors"]:
                        self.assertNotIn('"', anchor["href"])
                self.assertFalse(result["pwned"])

    def test_unsafe_schemes_and_math_are_never_linked(self):
        for payload in ("javascript:alert(1) und JaVaScRiPt:alert(2)", "[x](javascript:alert(1))",
                        "[x](data:text/html,<script>alert(1)</script>)", r"$\url{https://x.test}$",
                        r"\(\href{javascript:alert(1)}{klick}\)"):
            with self.subTest(payload=payload):
                for view in self.each(self.render(payload)):
                    self.assertEqual(view["anchors"], [], view["html"])

    def test_html_in_a_reply_stays_text(self):
        result = self.render('<img src=x onerror="window.__pwned=1"> <script>window.__pwned=1</script> '
                             "| a | <b>b</b> |\n|---|---|\n| <i>c</i> | d |")
        for view in self.each(result):
            self.assertEqual(set(view["tags"]) & {"img", "script", "b", "i"}, set(), view["html"])
            self.assertIn("&lt;img", view["html"])
        self.assertFalse(result["pwned"])

    def test_a_formula_source_cannot_break_out_of_its_slot(self):
        result = self.render('$$\\text{"><img src=x onerror=window.__pwned=1>}$$', katex="none")
        for view in self.each(result):
            self.assertNotIn("img", view["tags"])
        self.assertFalse(result["pwned"])


class LayoutTests(_BrowserCase):
    def test_a_wide_inline_formula_scrolls_inside_the_message_at_320px(self):
        wide = "x" * 160
        page = self.page(viewport=(320, 640))
        result = harness.render_markdown(page, f"Also gilt \\({wide}\\) wie gezeigt.")
        self.assertIsNone(result["pageOverflow"])
        for view in self.each(result):
            self.assertIsNone(view["overflow"], "the message itself does not widen")
        scroll = page.evaluate("""() => [...document.querySelectorAll(
              '.asst-body .ln-math, .tutor-message .ln-math, .history-message-body .ln-math')]
            .map((node) => ({ display: getComputedStyle(node).display, overflow: getComputedStyle(node).overflowX,
                              scrolls: node.scrollWidth > node.clientWidth,
                              fits: node.getBoundingClientRect().right <= window.innerWidth }))""")
        self.assertEqual(scroll, [{"display": "inline-block", "overflow": "auto", "scrolls": True, "fits": True}] * 3)

    def test_table_headers_are_not_split_mid_word(self):
        page = self.page(viewport=(360, 640))
        harness.render_markdown(page, "| Schritt | Rechnung | Ergebnis |\n|---|---|---|\n| 1 | \\(D = b^2 - 4ac\\) | 64 |")
        wrap = page.evaluate("getComputedStyle(document.querySelector('.asst-body')).overflowWrap")
        self.assertEqual(wrap, "break-word")
        # One line box per header word: "anywhere" used to split "Sch|ritt" at this width.
        lines = page.evaluate("""[...document.querySelectorAll('.asst-body th')].map((th) => {
            const range = document.createRange(); range.selectNodeContents(th);
            return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size; })""")
        self.assertEqual(lines, [1, 1, 1])


class NodeStyleTests(unittest.TestCase):
    """tests/js/*.test.mjs, written for `node --test`, run unchanged in the browser."""

    def run_file(self, filename, minimum):
        results = harness.run_node_tests(BROWSER["browser"], filename)
        failures = [result for result in results if not result["ok"]]
        self.assertEqual(failures, [], "\n\n".join(f"{item['name']}: {item.get('error')}" for item in failures))
        self.assertGreaterEqual(len(results), minimum)

    def test_the_renderer_security_suite(self):
        self.run_file("assistant-render.test.mjs", 25)

    def test_the_formula_rule_suite(self):
        self.run_file("math-rules.test.mjs", 12)

    def test_the_tutorial_rule_suite(self):
        # static/js/tour-rules.js: the guided tutorial's pure decisions (the quick tour and
        # the feature chapters that start by themselves, since 2026-11-04).
        self.run_file("tour-rules.test.mjs", 32)


class RealKatexTests(_BrowserCase):
    """The gpt-oss shaped corpus through the real KaTeX 0.16.11 (opt-in, see the module doc)."""

    CORPUS = [
        r"Der Satz des Pythagoras: \(a^2+b^2=c^2\) und \[\frac{a}{b}\]",
        r"Setze \( a = 2 \), \( b = -4 \) und \( c = -6 \) ein.",
        "Die Lösungsformel lautet\n\n\\[\nx_{1,2}= \\frac{-b\\pm\\sqrt{b^{2}-4ac}}{2a}\n\\]\n\nJetzt einsetzen.",
        "**Schritt für Schritt**\n\n\\[\n\\begin{aligned}\n2x + 3 &= 7 \\\\\n2x &= 4 \\\\\nx &= 2\n\\end{aligned}\n\\]",
        "Das Gleichungssystem:\n\n\\[\n\\begin{cases}\nx + y = 5 \\\\\nx - y = 1\n\\end{cases}\n\\]",
        "$$\nA = \\begin{pmatrix}\n1 & 2 \\\\\n3 & 4\n\\end{pmatrix}\n$$",
        "1. Klammer auflösen:\n   \\[ 2(x+3) = 2x + 6 \\]\n2. Zusammenfassen:\n   \\[ 2x + 6 = 10 \\]",
        "| Schritt | Rechnung | Ergebnis |\n|---|---|---|\n| 1 | \\(D = b^2 - 4ac\\) | \\(D = 64\\) |\n"
        "| 2 | \\(\\sqrt{D}\\) | \\(8\\) |\n| 3 | $|-3|$ | \\(x_1 = 3,\\; x_2 = -1\\) |",
        r"**Ergebnis:** \(\boxed{x = 3}\) und \( v = \frac{s}{t} = 5\,\text{m/s} \)",
        "- Bruch kürzen:\n  \\[\n  \\dfrac{12}{18} = \\dfrac{2}{3}\n  \\]\n- Fertig.",
        r"$$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$$ und $$\int_0^1 x^2\,dx = \frac{1}{3}$$",
        r"Reaktion: $\ce{2H2 + O2 -> 2H2O}$ und Wasser $\mathrm{H_2O}$",
    ]

    def test_the_corpus_renders_without_raw_latex(self):
        expected = [2, 3, 1, 1, 1, 1, 2, 6, 2, 1, 2, 2]
        for text, count in zip(self.CORPUS, expected, strict=True):
            with self.subTest(text=text[:40]):
                result = self.render(text, katex="real")
                for view in self.each(result):
                    self.assertEqual([item["kind"] for item in view["formulas"]], ["katex"] * count, view["html"][:600])
                    for raw in (BS + "begin", "frac", BS + "(", BS + "[", "$$", BS + "ce"):
                        self.assertNotIn(raw, view["text"])


class TutorHistoryPageTests(unittest.TestCase):
    """The real lesson page and lesson history page (Flask app, real templates and app.js):
    a resumed lesson shows its tutor chat rendered, a stored doubled reply comes back single."""

    @classmethod
    def setUpClass(cls):
        if not BROWSER:
            raise unittest.SkipTest("no browser")
        cls.audit = harness._audit_module()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.application, cls.server = cls.audit.start_server(port)
        cls.base = f"http://127.0.0.1:{port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def context(self):
        context = BROWSER["browser"].new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        context.add_init_script(harness.KATEX_STUB)
        base = self.base
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
        self.addCleanup(context.close)
        return context

    def test_a_resumed_lesson_and_the_history_page_render_the_chat(self):
        application = self.application
        page = self.context().new_page()
        page.goto(f"{self.base}/register")
        page.fill("input[name=username]", "historyjs")
        page.fill("input[name=email]", "historyjs@example.com")
        page.fill("input[name=password]", "correct-horse-battery")
        if page.locator("select[name=grade]").count():
            page.select_option("select[name=grade]", "8")
        page.locator("form [type=submit]").first.click()
        page.wait_for_load_state("networkidle")
        session_id = page.request.post(f"{self.base}/api/analyze", multipart={
            "study_goal": "Potenzgesetze", "subject": "Mathematics"}).json()["session_id"]
        chat = page.request.post(f"{self.base}/api/chat", data=json.dumps({"session_id": session_id,
                                                                            "message": "Wie geht das?"}),
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(chat.status, 200, chat.text()[:300])
        application.SESSIONS[session_id]["chat_history"].append(
            {"role": "tutor", "content": "Alt: $" + BS * 2 + "frac{1}{3}$ und " + BS + "[ x = 2 " + BS + "]"})

        page.goto(f"{self.base}/?session_id={session_id}", wait_until="load")
        page.wait_for_function("() => document.querySelectorAll('#chatMessages .tutor-message .katex').length >= 5",
                               timeout=8000)
        messages = page.evaluate("""() => [...document.querySelectorAll('#chatMessages .message')].map((node) => ({
            role: node.classList.contains('student-message') ? 'student' : 'tutor', text: node.textContent,
            tex: [...node.querySelectorAll('[data-ln-math]')].map((math) => math.dataset.lnMath),
            tables: node.querySelectorAll('table').length, slots: node.querySelectorAll('.ln-math-slot').length }))""")
        self.assertEqual([item["role"] for item in messages], ["tutor", "student", "tutor", "tutor"], messages)
        welcome, student, reply, old = messages
        self.assertEqual(student["text"], "Wie geht das?")
        self.assertEqual(reply["tables"], 1)
        self.assertEqual(reply["tex"], [r"2^{3} \cdot 2^{2} = 2^{3+2} = 2^{5}", "2^5 = 32", r"3^2 \cdot 3^4"])
        self.assertNotIn("CHIPS", reply["text"])
        self.assertEqual(old["tex"], [r"\frac{1}{3}", "x = 2"], "a stored doubled reply comes back single")
        self.assertEqual(reply["slots"] + old["slots"], 0)

        with application.app.app_context():
            lesson = application.db.session.scalar(application.db.select(application.Lesson).where(
                application.Lesson.session_id == session_id))
            assert lesson is not None
            lesson_id = lesson.id
        page.goto(f"{self.base}/lessons/{lesson_id}", wait_until="load")
        page.wait_for_function("() => document.querySelectorAll('.history-message-body .katex').length >= 3",
                               timeout=8000)
        history = page.evaluate("""() => [...document.querySelectorAll('.history-message.tutor .history-message-body')]
            .map((node) => ({ tables: node.querySelectorAll('table').length, raw: node.hasAttribute('data-rich-text'),
                              tex: [...node.querySelectorAll('[data-ln-math]')].map((math) => math.dataset.lnMath),
                              text: node.textContent }))""")
        self.assertEqual(len(history), 1, history)
        self.assertEqual(history[0]["tables"], 1)
        self.assertFalse(history[0]["raw"])
        self.assertEqual(len(history[0]["tex"]), 3)
        for raw in ("**", "|---|", "$$"):
            self.assertNotIn(raw, history[0]["text"])
        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
        self.assertFalse(overflow)


if __name__ == "__main__":
    unittest.main()

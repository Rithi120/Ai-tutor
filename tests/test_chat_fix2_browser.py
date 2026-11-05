r"""Chat replies from the live check of 2026-11-02, rendered by the shipped renderer in a
real browser (tests/js_browser/harness.py; run with R:/ai_tutor/.venv/Scripts/python).

D3: gpt-oss wrote a derivation as a table whose cells break over two lines
("| 3 | **Addiere …** zu beiden Seiten:  \n\(…\) | Wir ergänzen … |"). GitHub-flavoured
markdown ends a row at the line break, so every formula landed in the "Nr." column and
"Warum?" stayed empty. markdown.js now joins such a continuation to its row - only while
the row is still open, and only when the joined row then closes with "|".

D6: the page waited 60 s instead of 5 after a 409 conversation_busy, because Flask-Limiter
put its hour-scale window into Retry-After; the body's own details.retry_after now wins.
"""

import json
import os
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_js_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from learnova.assistant.formatting import split_reply_trailers  # noqa: E402
from tests.js_browser import harness  # noqa: E402

FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "live_chat_2026-11-02.json")
                     .read_text(encoding="utf-8"))
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


class Tables(HTMLParser):
    """Every table as rows of cells; a cell is (text, formulas, line breaks)."""

    def __init__(self):
        super().__init__()
        self.tables, self.cell, self.depth = [], None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.tables[-1].append([])
        elif tag in ("td", "th") and self.tables:
            self.cell = {"text": "", "formulas": 0, "breaks": 0}
        elif self.cell is not None and tag == "br":
            self.cell["breaks"] += 1
        elif self.cell is not None and "data-tex" in dict(attrs):
            self.cell["formulas"] += 1

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.tables[-1][-1].append(self.cell)
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data


def tables(html):
    parser = Tables()
    parser.feed(html)
    return parser.tables


class _Case(unittest.TestCase):
    def page(self, **options):
        page = harness.open_page(BROWSER["browser"], **options)
        self.addCleanup(page.context.close)
        return page

    def render_html(self, text):
        """The renderer's own output (before typesetting), on all three chat surfaces."""

        page = self.page(katex="none")
        return page.evaluate("""async (text) => {
            const { renderMarkdown, parseChips } = await import('/static/js/markdown.js');
            return renderMarkdown(parseChips(text).text); }""", text)


class LiveTableTests(_Case):
    def test_the_live_derivation_table_keeps_three_columns(self):
        text = split_reply_trailers(FIXTURE["reply_f1_table_derivation"]["text"])[0]
        (table,) = tables(self.render_html(text))
        header, *rows = table
        self.assertEqual([cell["text"].strip() for cell in header], ["Nr.", "Operation", "Warum?"])
        self.assertEqual(len(rows), 8)
        for number, row in enumerate(rows, start=1):
            with self.subTest(row=number):
                self.assertEqual(len(row), 3)
                self.assertEqual(row[0]["text"].strip(), str(number), "the number column holds only the number")
                self.assertEqual(row[0]["formulas"], 0)
                self.assertGreaterEqual(row[1]["formulas"], 1, "the step's formula stays in Operation")
                self.assertTrue(row[2]["text"].strip(), "Warum? is filled")
        for number in range(3, 9):
            self.assertEqual(rows[number - 1][1]["breaks"], 1, f"row {number}: the cell's own line break")
        self.assertIn("Wir ergänzen die quadratische Ergänzung", rows[2][2]["text"])
        self.assertIn("Mitternachtsformel", rows[7][2]["text"])

    def test_the_live_table_on_every_surface_with_katex(self):
        text = split_reply_trailers(FIXTURE["reply_f1_table_derivation"]["text"])[0]
        result = harness.render_markdown(self.page(), text)
        for surface in ("assistant", "tutor", "history"):
            with self.subTest(surface=surface):
                view = result[surface]
                (table,) = tables(view["html"])
                self.assertEqual([len(row) for row in table], [3] * 9)
                self.assertEqual(view["slots"], 0)
                self.assertEqual({item["kind"] for item in view["formulas"]}, {"katex"})
                self.assertEqual(view["html"].count("<td></td>"), 0, "no empty Warum? cell")

    def test_only_an_open_row_is_joined(self):
        cases = {
            # A closed row followed by prose: the prose stays a paragraph.
            "| a | b |\n|---|---|\n| 1 | 2 |\nDanach geht es weiter.": ([2, 2], "Danach geht es weiter."),
            # An open row that never closes: not joined (the old behaviour).
            "| a | b | c |\n|---|---|---|\n| 1 | zwei\nkein Abschluss": ([3, 3], "kein Abschluss"),
            # A continuation that would make more cells than the header: not joined.
            "| a | b |\n|---|---|\n| 1 | zwei\ndrei | vier |": ([2, 2, 2], None),
            # A row with all its cells but no closing pipe: complete, nothing to join.
            "| a | b |\n|---|---|\n| 1 | 2\nNoch ein Satz.": ([2, 2], "Noch ein Satz."),
            # A table without outer pipes is never joined.
            "a | b\n--|--\n1 | eins\nzwei | drei": ([2, 2, 2], None),
        }
        for source, (shape, paragraph) in cases.items():
            with self.subTest(source=source):
                html = self.render_html(source)
                (table,) = tables(html)
                self.assertEqual([len(row) for row in table], shape)
                self.assertNotIn("<br>", html.split("</table>")[0])
                if paragraph:
                    self.assertIn(f"<p>{paragraph}</p>", html)

    def test_a_joined_row_takes_at_most_four_lines_and_stops_at_a_blank_line(self):
        source = "| a | b | c |\n|---|---|---|\n| 1 | erste\nzweite\ndritte | ende |\n\n| x | y |\n|---|---|"
        table, second = tables(self.render_html(source))
        self.assertEqual([len(row) for row in table], [3, 3])
        self.assertEqual([len(row) for row in second], [2], "the blank line ends the first table")
        self.assertEqual(table[1][1]["breaks"], 2)
        self.assertEqual(table[1][1]["text"], "erstezweitedritte")
        long = "| a | b | c |\n|---|---|---|\n| 1 | " + "\n".join(["w"] * 6) + " | ende |"
        html = self.render_html(long)
        self.assertNotIn("<br>", html.split("</table>")[0], "six continuation lines are not a cell")

    def test_the_line_break_marker_cannot_be_written_by_the_model(self):
        html = self.render_html("| a | b |\n|---|---|\n| 1 \ue005 | 2 |\nText \ue005 hier")
        self.assertNotIn("<br>", html)
        self.assertNotIn("\ue005", html)


class RetryAfterRuleTests(_Case):
    def test_the_body_retry_after_beats_the_header(self):
        page = self.page()
        values = harness.eval_module(page, "/static/js/assistant-rules.js", (
            "(m) => [m.retryAfterSeconds({retryAfter: 3594, details: {retry_after: 5}}), "
            "m.retryAfterSeconds({retryAfter: 5}), m.retryAfterSeconds({details: {retry_after: 9}}), "
            "m.retryAfterSeconds({}), m.retryAfterSeconds({retryAfter: 600})]"))
        self.assertEqual(values, [5, 5, 9, 5, 60])


if __name__ == "__main__":
    unittest.main()

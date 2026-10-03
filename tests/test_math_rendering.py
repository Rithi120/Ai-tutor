"""Global LaTeX rendering: one renderer, everywhere, and no mangled prose.

Two things are tested here, differently.

**The rules.** Whether `$...$` is a formula or a price is the part that can quietly ruin
text, so it gets a corpus. Node is not installed in this image, so the JavaScript cannot
be executed; `MathRules` below is a transcription of static/js/math-rules.js and
`RuleParityTests` fails if the two drift. That proves the *algorithm* is right - it does
not prove the shipped JavaScript runs. tests/js/math-rules.test.mjs covers that for an
image with Node.

**The wiring.** That there is one renderer rather than five, that every page gets it,
and that editable text is never rewritten underneath a student.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_math_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RULES_JS = (ROOT / "static/js/math-rules.js").read_text(encoding="utf-8")
MATH_JS = (ROOT / "static/js/math.js").read_text(encoding="utf-8")


class MathRules:
    """Transcription of static/js/math-rules.js. Kept in step by RuleParityTests."""

    MAX_INLINE_LENGTH = 200
    DELIMITERS = [("$$", "$$", True), ("\\[", "\\]", True),
                  ("\\(", "\\)", False), ("$", "$", False)]
    SIGNAL = re.compile(r"[a-zA-Z\\^_{}=+<>/|]"
                        r"|[±×÷⁄←-⇿∀-⋿Α-ω]")

    @classmethod
    def looks_like_math(cls, tex, display):
        if not isinstance(tex, str) or not tex.strip():
            return False
        if display:
            return True
        if len(tex) > cls.MAX_INLINE_LENGTH:
            return False
        if re.match(r"^\s", tex) or re.search(r"\s$", tex):
            return False
        if re.search(r"[\n\r]", tex):
            return False
        return bool(cls.SIGNAL.search(tex))

    @classmethod
    def _match_at(cls, text, index):
        for open_d, close_d, display in cls.DELIMITERS:
            if not text.startswith(open_d, index):
                continue
            search = index + len(open_d)
            while search < len(text):
                close = text.find(close_d, search)
                if close == -1:
                    break
                if close > 0 and text[close - 1] == "\\":
                    search = close + 1
                    continue
                return display, text[index + len(open_d):close], close + len(close_d)
        return None

    @classmethod
    def split(cls, text):
        source = str(text or "")
        segments, plain, index = [], "", 0

        def flush():
            nonlocal plain
            if plain:
                segments.append(("text", plain, False))
                plain = ""

        while index < len(source):
            if source[index] == "\\" and index + 1 < len(source) and source[index + 1] in "$\\":
                plain += "$" if source[index + 1] == "$" else "\\\\"
                index += 2
                continue
            found = cls._match_at(source, index)
            if found and cls.looks_like_math(found[1], found[0]):
                flush()
                segments.append(("math", found[1], found[0]))
                index = found[2]
                continue
            plain += source[index]
            index += 1
        flush()
        return segments

    @classmethod
    def formulas(cls, text):
        return [value for kind, value, _display in cls.split(text) if kind == "math"]


class FormulaDetectionTests(unittest.TestCase):
    """What the AI writes must render; what a student writes about money must not."""

    def assert_formulas(self, text, expected):
        self.assertEqual(MathRules.formulas(text), expected, text)

    def test_inline_and_block_formulas(self):
        self.assert_formulas("Energy is $E=mc^2$ exactly.", ["E=mc^2"])
        self.assert_formulas("$$x^2+y^2=z^2$$", ["x^2+y^2=z^2"])
        self.assert_formulas(r"Use \(a+b\) inline.", ["a+b"])
        self.assert_formulas(r"Block: \[\int_0^1 x\,dx\]", [r"\int_0^1 x\,dx"])

    def test_the_notation_the_prompts_actually_ask_for(self):
        self.assert_formulas(r"Fraction $\frac{a}{b}$.", [r"\frac{a}{b}"])
        self.assert_formulas(r"Root $\sqrt{x+1}$.", [r"\sqrt{x+1}"])
        self.assert_formulas("Exponent $e^{i\\pi}$, subscript $a_{ij}$.", ["e^{i\\pi}", "a_{ij}"])
        self.assert_formulas(r"Greek $\pi$, $\alpha$, $\Omega$.", [r"\pi", r"\alpha", r"\Omega"])
        self.assert_formulas(r"Sum $\sum_{n=1}^{\infty}\frac{1}{n^2}$.",
                             [r"\sum_{n=1}^{\infty}\frac{1}{n^2}"])
        self.assert_formulas(r"$$\begin{pmatrix}1&0\\0&1\end{pmatrix}$$",
                             [r"\begin{pmatrix}1&0\\0&1\end{pmatrix}"])
        self.assert_formulas(r"Chemistry $\mathrm{H_2O}$ and $6CO_2$.", [r"\mathrm{H_2O}", "6CO_2"])
        self.assert_formulas(r"Inequality $a \leq b < c$.", [r"a \leq b < c"])

    def test_money_and_ordinary_prose_are_left_alone(self):
        # Each of these has two or more dollar signs, which is what a naive renderer
        # turns into a formula made of the words between them.
        for text in ("The book costs $5.",
                     "Between $5 and $10 per hour.",
                     "Prices: $5, $10, $15 each.",
                     "I paid $20$ for it.",
                     "It was $100 in cash and $200 in the bank.",
                     "Revenue rose from $1m to $2m.",
                     "A $ B $ C",
                     "No math here at all."):
            self.assert_formulas(text, [])

    def test_an_escaped_dollar_is_a_dollar(self):
        self.assert_formulas(r"A literal \$5 price.", [])
        self.assertEqual(MathRules.split(r"Costs \$5")[0][1], "Costs $5")

    def test_math_and_money_can_share_a_sentence(self):
        self.assert_formulas("Solve $x^2$ then pay $5 today.", ["x^2"])

    def test_an_unclosed_delimiter_stays_as_text(self):
        self.assert_formulas("An open $x^2 with no end.", [])

    def test_a_runaway_span_is_not_treated_as_one_formula(self):
        self.assert_formulas(f"${'a' * 400}$", [])

    def test_the_source_survives_a_round_trip(self):
        # "Keep formulas editable by preserving the original LaTeX source internally."
        original = r"Energy $E=mc^2$ and $$\frac{a}{b}$$ done."
        rebuilt = "".join(
            value if kind == "text" else (f"$${value}$$" if display else f"${value}$")
            for kind, value, display in MathRules.split(original))
        self.assertEqual(rebuilt, original)


class RuleParityTests(unittest.TestCase):
    """The transcription above is only evidence while it still matches the real module."""

    def test_the_delimiters_match(self):
        for opening, closing, _display in MathRules.DELIMITERS:
            self.assertIn(f'open: "{opening.replace(chr(92), chr(92) * 2)}"', RULES_JS)
            self.assertIn(f'close: "{closing.replace(chr(92), chr(92) * 2)}"', RULES_JS)

    def test_the_inline_length_cap_matches(self):
        cap = re.search(r"MAX_INLINE_LENGTH = (\d+)", RULES_JS)
        self.assertIsNotNone(cap)
        self.assertEqual(int(cap.group(1)), MathRules.MAX_INLINE_LENGTH)  # type: ignore[union-attr]

    def test_the_signal_pattern_matches(self):
        signal = re.search(r"const MATH_SIGNAL = /(.+)/;", RULES_JS)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.group(1), MathRules.SIGNAL.pattern)  # type: ignore[union-attr]

    def test_the_guards_that_reject_prose_are_all_present(self):
        for guard in (r"/^\s|\s$/.test(tex)", r"/[\n\r]/.test(tex)",
                      "tex.length > MAX_INLINE_LENGTH", "MATH_SIGNAL.test(tex)"):
            self.assertIn(guard, RULES_JS, guard)

    def test_the_rules_module_stays_free_of_the_dom(self):
        for forbidden in ("document.", "window.", "katex"):
            self.assertNotIn(forbidden, RULES_JS)


class OneRendererEverywhereTests(unittest.TestCase):
    """The point of the change: a property of the app, not a list of patched pages."""

    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "morgan", "email": "morgan@example.com", "password": "correct-horse-battery"})

    def test_every_page_loads_the_renderer(self):
        base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
        self.assertIn("js/math.js", base)
        for path in ("/dashboard", "/flashcards", "/community", "/projects",
                     "/assistant", "/progress", "/vocabulary"):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn("js/math.js", html, path)

    def test_no_template_pulls_katex_in_by_itself(self):
        # Five templates used to carry their own CDN tags; a sixth page simply showed
        # raw LaTeX because nobody remembered to add them.
        offenders = [path.name for path in (ROOT / "templates").glob("*.html")
                     if "cdn.jsdelivr.net/npm/katex" in path.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])

    def test_there_is_exactly_one_implementation(self):
        defined = [path.as_posix() for path in (ROOT / "static").rglob("*.js")
                   if re.search(r"^\s*(export )?function renderMath\b", path.read_text(encoding="utf-8"), re.M)]
        self.assertEqual(defined, [(ROOT / "static/js/math.js").as_posix()])

    def test_katex_is_fetched_only_when_a_page_has_a_formula(self):
        self.assertIn("loadKatex", MATH_JS)
        self.assertIn("hasMath", MATH_JS)

    def test_the_whole_main_area_is_covered_by_default(self):
        self.assertIn('"main, [data-math]"', MATH_JS)

    def test_editable_text_is_never_rewritten(self):
        # A textarea holds the LaTeX a student is typing. Rendering it would destroy
        # the source, which is the one thing the brief said to preserve.
        for tag in ("TEXTAREA", "INPUT", "SELECT", "OPTION", "PRE", "CODE", "SCRIPT"):
            self.assertIn(f'"{tag}"', MATH_JS)
        self.assertIn("isContentEditable", MATH_JS)

    def test_the_latex_source_is_kept_on_every_rendered_formula(self):
        self.assertIn("span.dataset.lnMath = tex", MATH_JS)
        self.assertIn("export function mathSource", MATH_JS)

    def test_a_broken_formula_shows_its_source_rather_than_nothing(self):
        self.assertIn("throwOnError: false", MATH_JS)
        self.assertIn("span.textContent = display", MATH_JS)

    def test_a_rendered_suggestion_still_yields_its_latex_when_tapped(self):
        # KaTeX replaces the chip's text with glyphs, so textContent is no longer the
        # answer. The source has to travel on the element.
        suggest = (ROOT / "static/js/flashcards/suggest.js").read_text(encoding="utf-8")
        create = (ROOT / "static/js/flashcards/create.js").read_text(encoding="utf-8")
        self.assertIn('data-back="${escapeHtml(item.back)}"', suggest)
        self.assertIn("chip.dataset.back", create)

    def test_formulas_follow_the_theme_instead_of_fixing_a_colour(self):
        css = (ROOT / "static/css/learnova-content.css").read_text(encoding="utf-8")
        rule = css.split(".ln-math {", 1)[1].split("}", 1)[0]
        self.assertIn("color: inherit", rule)
        self.assertIn(".ln-math-display { display: block; }", css)
        self.assertIn("overflow-x: auto", css)   # wide formulas scroll on a phone


if __name__ == "__main__":
    unittest.main()

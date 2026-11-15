"""Teacher-specific formula intelligence (spec Phase 2): pure parts, no app, no AI.

Pinned here:

* structural formula matching (learnova/projects/formulas.py): a LaTeX rewrite of a notes
  formula is found on the page in the page's notation; a wrong rearrangement ("U = R/I" on
  a page that writes "U = R · I") is not; one reaction in two spellings is one formula;
* topic normalisation (topics.normalize_topic) keeps notes formulas with their page and
  notation, keeps a formula whose words only are on the page as added (never "in the
  notes", never a card), drops an invented one, and marks Kompetenzraster pages;
* flashcards: no card for an added formula, the front of a notes formula typeset, the
  card's identity (origin key) unchanged;
* the LaTeX validator and repair (learnova/assistant/formatting.py): problems found, valid
  text unchanged, broken formulas repaired or made readable, idempotent;
* canonical_math no longer turns "\\rightarrow" into "arrow";
* the lesson side: the topic map lists the notes' formulas first, an "alternative" part is
  never from the notes, its aliases, and the formula plan steps per subject.
"""

from __future__ import annotations

import unittest

from learnova.assistant.formatting import display_tex, latex_problems, math_problems, plain_tex, repair_math
from learnova.lessons import lesson_context, lesson_plan
from learnova.lessons.strategies import REGISTRY
from learnova.lessons.strategies.base import PART_KINDS
from learnova.projects import formulas, terms, topics
from learnova.vision.notation import canonical_math, same_math

LINE = "Geradengleichung: y = mx + b"
OHM = "Ohmsches Gesetz: U = R · I (in V)"


class MatchKeyTests(unittest.TestCase):
    def test_spellings_of_one_formula_share_a_key(self):
        same = [
            ("y = m \\cdot x + b", "y = mx + b"), ("$y = mx + b$", "y = mx + b"), ("\\(y = m x + b\\)", "y=mx+b"),
            ("y - y_1 = m(x - x_1)", "y - y1 = m(x - x1)"), ("y - y₁ = m(x - x₁)", "y - y1 = m(x - x1)"),
            ("U = R \\cdot I", "U = R · I"), ("v = \\frac{s}{t}", "v = s : t"), ("a^{2} + b^{2} = c^{2}", "a² + b² = c²"),
            ("\\ce{2H2 + O2 -> 2H2O}", "2H2 + O2 -> 2H2O"), ("2H_2 + O_2 \\rightarrow 2H_2O", "2H2 + O2 → 2H2O"),
            ("x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}", "x = (-b ± √(b² - 4ac)) / (2a)"),
            ("\\rho = \\frac{m}{V}", "ρ = m/V"), ("y = m x + b", "y = mx + b"),
        ]
        for left, right in same:
            with self.subTest(left=left):
                self.assertTrue(formulas.match_key(left))
                self.assertEqual(formulas.match_key(left), formulas.match_key(right))

    def test_different_formulas_have_different_keys(self):
        page = formulas.match_key("U = R · I")
        for other in ("U = \\frac{R}{I}", "I = \\frac{U}{R}", "R = \\frac{U}{I}", "P = U \\cdot I", "F = m \\cdot a",
                      "U = R"):
            with self.subTest(other=other):
                self.assertNotEqual(formulas.match_key(other), page)

    def test_a_name_without_a_relation_is_no_formula_key(self):
        self.assertEqual(formulas.match_key("Satz des Pythagoras"), "")
        self.assertEqual(formulas.match_key(""), "")
        self.assertFalse(formulas.is_symbolic("Ohmsches Gesetz"))


class FindOnPagesTests(unittest.TestCase):
    PAGES = {1: LINE + "\nm ist die Steigung, b der y-Achsenabschnitt.", 2: OHM,
             3: "Punkt-Steigungs-Form: y - y1 = m(x - x1)", 4: "Knallgas: 2H2 + O2 -> 2H2O",
             5: "Die Formel lautet v = s : t.", 6: "Arbeit = Kraft · Weg"}

    def find(self, name, latex=""):
        return formulas.find_on_pages(name, latex, self.PAGES)

    def test_the_page_notation_is_returned_verbatim(self):
        self.assertEqual(self.find("y = m \\cdot x + b"), formulas.FoundFormula(1, "y = mx + b"))
        self.assertEqual(self.find("$y = mx + b$"), formulas.FoundFormula(1, "y = mx + b"))
        self.assertEqual(self.find("U = R \\cdot I"), formulas.FoundFormula(2, "U = R · I"))
        self.assertEqual(self.find("y - y_1 = m(x - x_1)").page_id, 3)
        self.assertEqual(self.find("\\ce{2H2 + O2 -> 2H2O}").notation, "2H2 + O2 -> 2H2O")
        self.assertEqual(self.find("v = \\frac{s}{t}"), formulas.FoundFormula(5, "v = s : t"))
        self.assertEqual(formulas.find_on_pages("y = mx + b", "", {7: "Gerade: y = m x + b gilt für alle x"}),
                         formulas.FoundFormula(7, "y = m x + b"))
        # A word equation keeps its words; the label before a formula is not part of it.
        self.assertEqual(self.find("Arbeit = Kraft \\cdot Weg").notation, "Arbeit = Kraft · Weg")

    def test_spaced_coefficients_and_vulgar_fractions_are_found(self):
        # German chemistry notes put a space between coefficient and formula; physics sheets write ½.
        self.assertEqual(formulas.find_on_pages("\\ce{2H2 + O2 -> 2H2O}", "", {8: "Knallgas: 2 H₂ + O₂ → 2 H₂O"}),
                         formulas.FoundFormula(8, "2 H₂ + O₂ → 2 H₂O"))
        self.assertEqual(formulas.find_on_pages("s = \\frac{1}{2} a t^2", "", {8: "Weg: s = ½ a t²"}).page_id, 8)
        self.assertEqual(formulas.find_on_pages("s = \\frac{1}{2} a t", "", {8: "Weg: s = ½ a t²"}).page_id, None)

    def test_the_latex_field_is_used_when_the_name_is_prose(self):
        self.assertEqual(self.find("Ohmsches Gesetz", "U = R \\cdot I").page_id, 2)

    def test_wrong_or_unwritten_rearrangements_are_not_on_the_page(self):
        for name in ("U = \\frac{R}{I}", "I = \\frac{U}{R}", "F = m \\cdot a", "P = U \\cdot I", "U = R", "y = mx"):
            with self.subTest(name=name):
                self.assertEqual(self.find(name), formulas.FoundFormula(None, ""))

    def test_a_link_of_a_chain_is_on_the_page(self):
        found = formulas.find_on_pages("v = \\frac{s}{t}", "", {9: "v = s/t = 10 m/s"})
        self.assertEqual(found.page_id, 9)

    def test_the_preferred_page_is_tried_first(self):
        pages = {1: LINE, 2: "Kompetenzraster: Ich kann y = mx + b aufstellen."}
        self.assertEqual(formulas.find_on_pages("y = mx + b", "", pages, prefer=2).page_id, 2)
        self.assertEqual(formulas.find_on_pages("y = mx + b", "", pages).page_id, 1)


class EnrichTests(unittest.TestCase):
    def test_enrich_formula(self):
        item = {"kind": "formula", "name": "U = R \\cdot I", "detail": "Ohmsches Gesetz", "page_id": 2,
                "in_notes": True, "latex": "U = R \\cdot \\frac{I}",
                "variables": [{"symbol": "U", "meaning": "Spannung", "unit": "V"},
                              {"symbol": "Q", "meaning": "Ladung", "unit": "C"},
                              {"symbol": "R", "meaning": "Widerstand", "unit": "Ω"}]}
        enriched = formulas.enrich_formula(item, {2: OHM + "\nU ist die Spannung."}, teacher_pages={2})
        self.assertEqual(enriched["match"], "exact")
        self.assertEqual(enriched["origin"], "notes")
        self.assertEqual(enriched["notation"], "U = R · I")
        self.assertEqual(enriched["latex"], "")                     # broken LaTeX is never kept
        self.assertTrue(enriched["teacher_marked"])
        self.assertEqual([variable["symbol"] for variable in enriched["variables"]], ["U", "R"])   # no Q
        self.assertEqual([variable["in_notes"] for variable in enriched["variables"]], [True, False])

    def test_an_old_wrong_formula_is_added_not_from_the_notes(self):
        enriched = formulas.enrich_formula({"kind": "formula", "name": "U = \\frac{R}{I}", "detail": "",
                                            "page_id": 2, "in_notes": True}, {2: OHM})
        self.assertEqual((enriched["match"], enriched["origin"], enriched["in_notes"]), ("words", "added", False))
        self.assertFalse(enriched["teacher_marked"])

    def test_a_named_formula_keeps_its_in_notes(self):
        enriched = formulas.enrich_formula({"kind": "formula", "name": "Satz des Pythagoras", "page_id": 1,
                                            "in_notes": True}, {1: "Satz des Pythagoras"})
        self.assertEqual((enriched["match"], enriched["in_notes"]), ("name", True))

    def test_enrich_map_only_touches_formulas_and_is_needed_once(self):
        stored = {"terms": [], "items": [{"kind": "formula", "name": "y = mx + b", "page_id": 1, "in_notes": True},
                                         {"kind": "example", "name": "Taxi", "page_id": 1, "in_notes": True}]}
        self.assertTrue(formulas.needs_enrichment(stored))
        enriched = formulas.enrich_map(stored, {1: LINE})
        self.assertFalse(formulas.needs_enrichment(enriched))
        self.assertEqual(enriched["items"][1], stored["items"][1])
        self.assertNotIn("match", stored["items"][0])               # the stored map is not changed

    def test_inventory_and_prompt_lines(self):
        maps = [
            {"items": [{"kind": "formula", "name": "y = m \\cdot x + b", "notation": "y = mx + b", "page_id": 3,
                        "in_notes": True, "origin": "notes", "teacher_marked": False}]},
            {"items": [{"kind": "formula", "name": "y = mx + b", "notation": "y = mx + b", "page_id": 3,
                        "in_notes": True, "origin": "notes", "teacher_marked": True},
                       {"kind": "formula", "name": "U = R · I", "notation": "U = R · I", "page_id": 1,
                        "in_notes": True, "origin": "notes",
                        "variables": [{"symbol": "U", "meaning": "Spannung", "unit": "V", "in_notes": True}]},
                       {"kind": "formula", "name": "I = U/R", "page_id": 1, "in_notes": False, "origin": "added"}]},
        ]
        found = formulas.inventory(maps, page_order={1: 1, 3: 3})
        self.assertEqual([entry["name"] for entry in found], ["y = mx + b", "U = R · I"])   # teacher-marked first
        self.assertTrue(found[0]["teacher_marked"])
        lines = formulas.prompt_lines(found, labels={1: "Physik.pdf p. 1", 3: "Mathe.pdf p. 3"})
        self.assertEqual(lines[0], "y = mx + b [Mathe.pdf p. 3, teacher-marked]")
        self.assertEqual(lines[1], "U = R · I [Physik.pdf p. 1] — U: Spannung (V)")
        labels = {1: "Physik.pdf p. 1", 3: "Mathe.pdf p. 3"}
        self.assertEqual(formulas.prompt_lines(found, labels=labels, limit=len(lines[0]) + 1), [lines[0]])
        self.assertEqual(formulas.prompt_lines(found, labels=labels, limit=10), [])


class NormalizeTopicTests(unittest.TestCase):
    def topic(self, items, pages, **kwargs):
        raw = {"title": "Thema", "source_page_ids": list(pages), "items": items}
        result = topics.normalize_topic(raw, set(pages), page_text=pages, **kwargs)
        assert result is not None
        return result

    def test_a_latex_rewrite_of_a_notes_formula_is_kept_in_the_page_notation(self):
        topic = self.topic([{"kind": "formula", "name": "y = m \\cdot x + b", "detail": "Gerade", "page_id": 1,
                             "latex": "y = m \\cdot x + b"}], {1: LINE})
        [item] = topic["items"]
        self.assertEqual((item["in_notes"], item["match"], item["notation"], item["page_id"]),
                         (True, "exact", "y = mx + b", 1))
        self.assertEqual(item["latex"], "y = m \\cdot x + b")
        self.assertEqual(topic["formulas"], ["y = mx + b: Gerade"])

    def test_a_wrong_rearrangement_is_kept_only_as_added(self):
        topic = self.topic([{"kind": "formula", "name": "U = \\frac{R}{I}", "detail": "Spannung", "page_id": 2}],
                           {2: OHM})
        [item] = topic["items"]
        self.assertEqual((item["in_notes"], item["match"], item["origin"]), (False, "words", "added"))
        self.assertEqual(topic["formulas"], [])                     # not in the old Notes list either

    def test_an_invented_formula_is_dropped(self):
        topic = self.topic([{"kind": "formula", "name": "F = m \\cdot a", "detail": "Kraft", "page_id": 2}], {2: OHM})
        self.assertEqual(topic["items"], [])

    def test_one_reaction_in_two_spellings_is_one_item(self):
        topic = self.topic([{"kind": "formula", "name": "2H_2 + O_2 \\rightarrow 2H_2O", "detail": "", "page_id": 4},
                            {"kind": "formula", "name": "2H2 + O2 -> 2H2O", "detail": "Knallgas", "page_id": 4}],
                           {4: "Knallgasreaktion: 2H2 + O2 -> 2H2O"})
        [item] = topic["items"]
        self.assertEqual(item["name"], "2H2 + O2 -> 2H2O")          # the page's spelling wins
        self.assertEqual(item["detail"], "Knallgas")

    def test_kompetenzraster_pages_mark_the_formula(self):
        pages = {1: LINE, 2: "Ich kann die Geradengleichung y = mx + b aufstellen."}
        marked = self.topic([{"kind": "formula", "name": "y = mx + b", "detail": "", "page_id": 2}], pages,
                            teacher_page_ids={2})
        self.assertTrue(marked["items"][0]["teacher_marked"])
        # Cited on the worksheet, listed on the Kompetenzraster too: still marked by the teacher,
        # and the worksheet stays its source page.
        cited = self.topic([{"kind": "formula", "name": "y = m \\cdot x + b", "detail": "", "page_id": 1}], pages,
                           teacher_page_ids={2})
        self.assertEqual((cited["items"][0]["page_id"], cited["items"][0]["teacher_marked"]), (1, True))
        plain = self.topic([{"kind": "formula", "name": "y = mx + b", "detail": "", "page_id": 1}],
                           {1: LINE, 2: "Ich kann lineare Funktionen zeichnen."}, teacher_page_ids={2})
        self.assertFalse(plain["items"][0]["teacher_marked"])
        unmarked = self.topic([{"kind": "formula", "name": "y = mx + b", "detail": "", "page_id": 1}], pages)
        self.assertFalse(unmarked["items"][0]["teacher_marked"])

    def test_variables_are_kept_as_the_page_gives_them(self):
        topic = self.topic([{"kind": "formula", "name": "U = R · I", "detail": "", "page_id": 2,
                             "variables": [{"symbol": "U", "meaning": "Spannung", "unit": "V"},
                                           {"zeichen": "I", "bedeutung": "Stromstärke", "einheit": "A"}]}],
                           {2: OHM + "\nU: Spannung, I: Stromstärke"})
        self.assertEqual([(v["symbol"], v["meaning"], v["unit"], v["in_notes"]) for v in topic["items"][0]["variables"]],
                         [("U", "Spannung", "V", True), ("I", "Stromstärke", "A", True)])

    def test_without_page_text_nothing_is_in_the_notes(self):
        raw = {"title": "Thema", "source_page_ids": [1],
               "items": [{"kind": "formula", "name": "y = mx + b", "detail": "", "page_id": 1}]}
        topic = topics.normalize_topic(raw, {1})
        assert topic is not None
        self.assertEqual(topic["items"][0]["in_notes"], False)


class FormulaCardTests(unittest.TestCase):
    def cards(self, items):
        topic = {"key": "ohm", "title": "Ohm", "source_page_ids": [2], "terms": [], "items": items}
        return terms.build_cards([topic], strategy_key="physics")

    def test_no_card_for_a_formula_that_is_not_in_the_notes(self):
        added = topics.normalize_topic({"title": "Ohm", "source_page_ids": [2], "items": [
            {"kind": "formula", "name": "U = \\frac{R}{I}", "detail": "Spannung aus Widerstand", "page_id": 2}]},
            {2}, page_text={2: OHM})
        assert added is not None
        self.assertEqual([card for card in terms.build_cards([added], strategy_key="physics")
                          if card["type"] == "formula_explanation"], [])
        # An old map that still says in_notes but whose formula was only matched by words.
        self.assertEqual(self.cards([{"kind": "formula", "name": "U = \\frac{R}{I}", "detail": "x", "page_id": 2,
                                      "in_notes": True, "origin": "added"}]), [])

    def test_the_front_is_typeset_and_the_identity_unchanged(self):
        [card] = self.cards([{"kind": "formula", "name": "U = R \\cdot I", "detail": "Ohmsches Gesetz", "page_id": 2,
                              "in_notes": True, "latex": "U = R \\cdot I"}])
        self.assertIn("\\(U = R \\cdot I\\)", card["front"])
        self.assertEqual(card["origin_key"], "ohm:formula:" + terms.item_key("formula", "U = R \\cdot I"))
        [plain] = self.cards([{"kind": "formula", "name": "U = R · I", "detail": "Ohmsches Gesetz", "page_id": 2,
                               "in_notes": True}])
        self.assertIn("U = R · I", plain["front"])
        [noted] = self.cards([{"kind": "formula", "name": "U = R \\cdot I", "detail": "Ohm", "page_id": 2,
                               "in_notes": True, "notation": "U = R · I", "origin": "notes"}])
        self.assertIn("U = R · I", noted["front"])                  # the notes' own notation
        self.assertEqual(noted["reverse_answer"], "U = R · I")      # asked back to front in that notation
        self.assertEqual(noted["origin_key"], "ohm:formula:" + terms.item_key("formula", "U = R \\cdot I"))

    def test_the_card_identity_key_still_folds_right_as_before(self):
        # Frozen on purpose: origin keys of existing cards depend on it (plan_sync is add-only).
        self.assertEqual(terms.item_key("formula", "2H_2 + O_2 \\rightarrow 2H_2O"), "2h_2+o_2arrow2h_2o")


class LatexValidityTests(unittest.TestCase):
    def test_valid_formulas_have_no_problems(self):
        for tex in ("\\frac{U}{I}", "U = R \\cdot I", "\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}",
                    "\\{1,2\\}", "\\frac12", "\\ce{2H2 + O2 -> 2H2O}", "\\sqrt[3]{8}", "\\left( a + b \\right)"):
            with self.subTest(tex=tex):
                self.assertEqual(latex_problems(tex), [])

    def test_problems_are_named(self):
        cases = {
            "x = \\frac{-b \\pm \\sqrt{b^2-4ac}{2a}": "unbalanced_braces", "\\frac{U}": "frac_arity",
            "\\left( a + b": "left_right", "\\begin{aligned} a &= b": "environment", "\\sqrt{}": "sqrt_empty",
            "\\\\frac{1}{2}": "doubled_backslash", "  ": "empty", "a}": "unbalanced_braces",
        }
        for tex, code in cases.items():
            with self.subTest(tex=tex):
                self.assertIn(code, latex_problems(tex))

    def test_math_problems_walk_the_text_like_the_browser(self):
        self.assertEqual(math_problems("between $5 and $10 today"), [])        # prices are prose
        self.assertEqual(math_problems("Gut: \\(U = R \\cdot I\\) und $x^2$"), [])
        self.assertEqual(math_problems("Halb ist \\frac{1}{2}."), ["bare_command"])
        self.assertIn("frac_arity", math_problems("Also \\(\\frac{U}\\)"))
        self.assertEqual(math_problems("`\\frac{1}` in code"), [])

    def test_repair(self):
        cases = {
            "Gut: \\(U = R \\cdot I\\)": "Gut: \\(U = R \\cdot I\\)",
            "Price between $5 and $10": "Price between $5 and $10",
            "Es gilt \\(x = \\frac{-b \\pm \\sqrt{b^2-4ac}{2a}\\).": "Es gilt \\(x = \\frac{-b \\pm \\sqrt{b^2-4ac}}{2a}\\).",
            "Halb ist \\frac{1}{2}.": "Halb ist \\(\\frac{1}{2}\\).",
            "x=\\frac{1}{2} und \\(y\\)": "\\(x=\\frac{1}{2}\\) und \\(y\\)",
            "\\(\\left( a + b\\)": "\\(( a + b\\)",
            "\\(x^{2\\)": "\\(x^{2}\\)",
            "Also \\(\\frac{U}\\) falsch": "Also U falsch",
            "\\[\\begin{aligned} a &= b\\]": "a = b",
            # The maths tokens before a bare command belong to its formula; a label does not.
            "Merke: U = R \\cdot I gilt immer.": "Merke: \\(U = R \\cdot I\\) gilt immer.",
            "(a) \\frac{1}{2}": "(a) \\(\\frac{1}{2}\\)",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                repaired, count = repair_math(text)
                self.assertEqual(repaired, expected)
                self.assertEqual(count > 0, text != expected)
                self.assertEqual(repair_math(repaired), (repaired, 0))      # idempotent
                self.assertEqual(math_problems(repaired), [])
        self.assertEqual(repair_math(None), (None, 0))
        self.assertEqual(repair_math("Kein Mathe hier."), ("Kein Mathe hier.", 0))

    def test_plain_and_display(self):
        self.assertEqual(plain_tex("\\frac{a}{b} \\cdot \\sqrt{x}"), "(a)/(b) · √(x)")
        self.assertEqual(display_tex("U = R · I"), "U = R · I")
        self.assertEqual(display_tex("y = mx + b", "y = m \\cdot x + b"), "\\(y = m \\cdot x + b\\)")
        self.assertEqual(display_tex("\\(a\\)"), "\\(a\\)")                       # never double-wrapped
        self.assertEqual(display_tex("U = \\frac{R}{I}"), "\\(U = \\frac{R}{I}\\)")
        self.assertEqual(display_tex("x", "\\frac{1}"), "x")                     # broken LaTeX is not shown


class CanonicalMathArrowTests(unittest.TestCase):
    def test_arrows_are_arrows(self):
        self.assertEqual(canonical_math("2H_2 + O_2 \\rightarrow 2H_2O"), "2H_2+O_2->2H_2O")
        self.assertEqual(canonical_math("\\ce{2H2 + O2 -> 2H2O}"), "2H2+O2->2H2O")
        self.assertEqual(canonical_math("A \\rightleftharpoons B"), "A<=>B")
        self.assertEqual(canonical_math("A \\leftarrow B"), "A<-B")
        self.assertEqual(canonical_math("\\left( a \\right)"), "(a)")
        self.assertTrue(same_math("x \\leq 2", "x ≤ 2"))


class LessonSideTests(unittest.TestCase):
    def test_the_topic_map_lists_the_notes_formulas_first(self):
        topic_map = {
            "terms": [{"term": "Steigung", "definition": "m", "in_notes": True, "page_id": 1}],
            "items": [{"kind": "example", "name": "Taxi", "detail": "Grundpreis", "in_notes": True, "page_id": 1},
                      {"kind": "formula", "name": "y = m \\cdot x + b", "notation": "y = mx + b", "latex": "y = mx + b",
                       "in_notes": True, "origin": "notes", "page_id": 2, "teacher_marked": True,
                       "variables": [{"symbol": "m", "meaning": "Steigung", "unit": "", "in_notes": True}]},
                      {"kind": "formula", "name": "x = \\frac{y-b}{m}", "in_notes": False, "origin": "added",
                       "page_id": 1}],
        }
        block = lesson_context.topic_map_block(topic_map, page_labels={1: "[A.pdf, p. 1]", 2: "[Raster.pdf, p. 1]"})
        heading = block.index(lesson_context.FORMULA_GROUP_HEADING)
        self.assertLess(heading, block.index("Fachbegriffe"))
        self.assertIn("- y = mx + b [[Raster.pdf, p. 1], teacher-marked] | variables: m: Steigung", block)
        self.assertIn("formula: x = \\frac{y-b}{m} (added, not in the notes", block)
        self.assertNotIn("teacher-approved", block)
        for limit in (300, 600, 1200):
            self.assertLessEqual(len(lesson_context.topic_map_block(topic_map, limit=limit)), limit)

    def test_an_alternative_part_is_never_from_the_notes(self):
        parts = [{"kind": "rule", "title": "Regel", "body": "y = mx + b", "from_notes": True, "source_pages": [1]},
                 {"kind": "alternative", "title": "Zusatztipp", "body": "Punkt-Steigungs-Form", "from_notes": True,
                  "source_pages": [1]}]
        guarded = lesson_context.guard_parts(parts, item_kinds=["formula"], source_text="")
        self.assertEqual([(part["kind"], part["from_notes"]) for part in guarded],
                         [("rule", True), ("alternative", False)])
        self.assertEqual(guarded[1]["source_pages"], [])

    def test_an_alternative_does_not_count_as_teaching_the_formula(self):
        topic_map = {"items": [{"kind": "formula", "name": "y = mx + b", "in_notes": True, "page_id": 1}]}
        parts = [{"kind": "alternative", "title": "Tipp", "body": "Statt y = mx + b geht auch ...", "items": []}]
        self.assertEqual(lesson_context.taught_evidence(parts, topic_map), ([], []))

    def test_aliases_and_kinds(self):
        self.assertIn("alternative", PART_KINDS)
        for alias in ("zusatztipp", "Additional tip", "alternative_method", "Alternative"):
            with self.subTest(alias=alias):
                self.assertEqual(lesson_plan.repair_parts([{"kind": alias, "body": "x"}])[0]["kind"], "alternative")
        # A bare "tip" may retell the notes: it is not forced into an added alternative.
        self.assertNotEqual(lesson_plan.repair_parts([{"kind": "tip", "body": "x"}])[0]["kind"], "alternative")

    def test_formula_steps_per_subject(self):
        for key in ("mathematics", "physics", "sciences"):
            with self.subTest(key=key):
                kinds = [step.kind for step in lesson_plan.plan_steps(REGISTRY[key], mode="learn", state="new",
                                                                      item_kinds=["formula"])]
                self.assertLess(kinds.index("rule"), kinds.index("worked_example"))
                self.assertLess(kinds.index("worked_example"), kinds.index("alternative"))
                without = [step.kind for step in lesson_plan.plan_steps(REGISTRY[key], mode="learn", state="new",
                                                                        item_kinds=["term"])]
                self.assertNotIn("alternative", without)
                if key != "mathematics":
                    self.assertNotIn("rule", without)
        nwt = [step.kind for step in lesson_plan.plan_steps(REGISTRY["nwt"], mode="learn", state="new",
                                                            item_kinds=["formula", "experiment", "process", "diagram"])]
        self.assertNotIn("alternative", nwt)
        self.assertLessEqual(len(nwt), lesson_plan.MAX_PLAN_STEPS)

    def test_every_prompt_block_fits(self):
        kinds_sets = (["formula", "term", "experiment", "process", "diagram"], ["formula"], [], None)
        for key, strategy in REGISTRY.items():
            for state in ("new", "developing", "secure"):
                for mode in ("learn", "review", "reteach"):
                    for kinds in kinds_sets:
                        steps = lesson_plan.plan_steps(strategy, mode=mode, state=state, item_kinds=kinds)
                        block = lesson_plan.lesson_prompt_block(strategy, steps, mode=mode, state=state,
                                                                grade_label="Klasse 10 Gymnasium")
                        self.assertLessEqual(len(block), lesson_plan.PROMPT_BLOCK_LIMIT, (key, state, mode, kinds))
                        self.assertLessEqual(len(steps), lesson_plan.MAX_PLAN_STEPS)

    def test_the_maths_rule_puts_the_notes_method_first(self):
        rules = " ".join(REGISTRY["mathematics"].rules)
        self.assertIn("Teach the method of the notes first", rules)
        self.assertNotIn("Teach the efficient method where appropriate", rules)


if __name__ == "__main__":
    unittest.main()

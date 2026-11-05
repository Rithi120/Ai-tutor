"""Project Mode Fachbegriff flashcards (learnova/projects/terms.py)."""

import re
import unittest
from types import SimpleNamespace

from learnova.flashcards.service import CARD_TYPES, normalize_card
from learnova.projects import topics as project_topics
from learnova.projects.terms import (
    ADDED_MARKER, CARD_KINDS_BY_STRATEGY, CARD_PROMPTS, EXAMPLE_LINE, ONE_WAY, OWN_WORDS, build_cards, item_key,
    origin_identity, plan_sync, term_key, terms_mentioned,
)

# The shared literal from the design (learnova/lessons/strategies/base.py and topics.py).
ITEM_KINDS = ("term", "example", "experiment", "theory", "person", "argument", "formula", "process",
              "diagram", "event", "date", "rule", "thought_experiment", "work", "method", "source",
              "place", "institution", "position", "cause_effect", "structure")
STRATEGY_KEYS = {"languages", "latin", "mathematics", "sciences", "physics", "nwt", "ethics", "religion",
                 "german", "history", "geography", "politics", "computer_science", "technology", "music",
                 "art", "physical_education", "general"}
# reverse_answer (LESSON-BE): the bare term asked back to front; normalize_card ignores it.
CARD_FIELDS = {"origin_key", "topic_key", "type", "front", "back", "explanation", "hint", "source_reference",
               "source_page_ids", "tags", "options", "image_query", "difficulty", "reverse_answer"}

PAGES = {11: ("Ethik-Blatt-1.pdf", 1), 12: ("Ethik-Blatt-1.pdf", 2), 13: ("Ethik-Blatt-2.pdf", 1),
         14: ("Ethik-Blatt-3.pdf", 4)}


def label(page_id):
    filename, number = PAGES[page_id]
    return f"{filename}, S. {number}"


def term(name, definition, *, simple="", example="", page_id: int | None = 11, quote="", in_notes=True,
         from_notes=True):
    return {"term": name, "definition": definition, "simple_definition": simple, "example": example,
            "page_id": page_id, "quote": quote, "in_notes": in_notes, "definition_from_notes": from_notes,
            "key": term_key(name)}


def item(kind, name, detail, *, page_id=11, in_notes=True):
    return {"kind": kind, "name": name, "detail": detail, "page_id": page_id, "in_notes": in_notes}


def kant_topic():
    return {
        "key": "kant-pflichtethik", "title": "Kants Pflichtethik", "source_page_ids": [11, 12], "difficulty": 2,
        "terms": [
            term("Kategorischer Imperativ", "Ein unbedingt gültiges Gebot der praktischen Vernunft.",
                 simple="Eine Regel, die immer und für alle gilt.",
                 example="Lügen ist nie erlaubt, auch nicht aus Höflichkeit.",
                 quote="Der kategorische Imperativ ist ein unbedingt gültiges Gebot der praktischen Vernunft."),
            term("Pflicht", "Die Notwendigkeit einer Handlung aus Achtung fürs Gesetz.", page_id=12,
                 quote="Pflicht ist die Notwendigkeit einer Handlung aus Achtung fürs Gesetz."),
            term("Maxime", "Ein subjektiver Grundsatz des Wollens.", simple="Eine persönliche Handlungsregel.",
                 page_id=12, from_notes=False),
            term("Neigung", "Ein Bedürfnis oder Wunsch, der nicht aus der Vernunft kommt.", page_id=11),
            term("Autonomie", "Selbstgesetzgebung des Willens.", in_notes=False),
            term("Heteronomie", "", simple=""),
        ],
        "items": [
            item("theory", "Pflichtethik", "Moralisch ist eine Handlung nur, wenn sie aus Pflicht geschieht."),
            item("person", "Immanuel Kant", "Philosoph der Aufklärung (1724-1804), begründet die Pflichtethik."),
            item("thought_experiment", "Der Kaufmann", "Ein Händler ist ehrlich, aber nur aus Eigennutz.",
                 page_id=12),
            item("event", "Französische Revolution", "Kant erlebte sie und sah sie positiv."),
            item("formula", "Universalisierung", "Handle nur nach der Maxime, die ein Gesetz werden kann."),
            item("example", "Lügenverbot", "Auch dem Mörder darf man nicht lügen.", in_notes=False),
        ],
    }


def utilitarism_topic():
    return {
        "key": "utilitarism", "title": "Utilitarismus", "source_page_ids": [13], "difficulty": 3,
        "terms": [
            term("Pflichten", "Was man moralisch tun muss.", page_id=13),
            term("Nutzen", "Das Glück, das eine Handlung für alle Betroffenen bewirkt.", page_id=13),
        ],
        "items": [item("position", "Handlungsutilitarismus", "Jede einzelne Handlung wird nach ihren Folgen bewertet.",
                       page_id=13)],
    }


def by_origin(cards, variant):
    return {origin_identity(card["origin_key"]).split(":", 1)[1]: card
            for card in cards if origin_identity(card["origin_key"]).split(":", 1)[0] == variant}


class TermKeyTests(unittest.TestCase):
    def test_articles_inflection_and_umlauts_fold_to_one_key(self):
        self.assertEqual(term_key("Der kategorische Imperativ"), term_key("Kategorischer Imperativ"))
        self.assertEqual(term_key("Pflicht"), term_key("Pflichten"))
        self.assertEqual(term_key("Kalkül"), term_key("Kalkuel"))
        self.assertEqual(term_key("Maxime (Ethik)"), term_key("Maxime"))

    def test_distinct_terms_keep_distinct_keys(self):
        self.assertNotEqual(term_key("Ursache/Wirkung"), term_key("Ursache"))
        self.assertNotEqual(term_key("Pflicht"), term_key("Neigung"))

    def test_stop_word_only_term_falls_back_to_its_text(self):
        self.assertEqual(term_key("Grundlagen"), "grundlagen")
        self.assertNotEqual(term_key("Grundlagen"), term_key("Thema"))
        self.assertEqual(term_key("Λόγος"), "λόγος".casefold())      # no Latin letters at all
        self.assertNotIn(":", term_key("Ratio: Vernunft"))

    def test_agrees_with_the_topic_maps_own_term_key(self):
        # The topic map (topics.normalize_topic) stores its own "key" per term; the
        # workspace joins cards and terms, so both keys must agree, including on "A/B"
        # terms and terms without Latin letters, where the two once differed.
        for name in ("Der kategorische Imperativ", "Pflichten", "Kalkül", "Maxime (Ethik)", "pH-Wert",
                     "Utilitarismus nach Bentham", "Text 2", "if-clause type 1", "Grundlagen", "Immanuel Kant",
                     "Satz des Pythagoras", "Präsens", "Erster Weltkrieg", "Ursache/Wirkung", "Λόγος",
                     "Квадратное уравнение", "范畴"):
            self.assertEqual(term_key(name), project_topics.term_key(name), name)


class ItemKeyTests(unittest.TestCase):
    def test_formulas_keep_their_symbols(self):
        binomials = ("(a+b)² = a² + 2ab + b²", "(a−b)² = a² − 2ab + b²", "(a+b)(a−b) = a² − b²")
        self.assertEqual(len({item_key("formula", formula) for formula in binomials}), 3)
        # As words these collide ("a" is an English article, operators are not words).
        self.assertEqual(term_key(binomials[0]), term_key(binomials[1]))
        self.assertNotEqual(item_key("formula", "F = m · a"), item_key("formula", "F = m · g"))

    def test_formula_spelling_does_not_change_its_identity(self):
        self.assertEqual(item_key("formula", "F = m · a"), item_key("formula", "F=m*a"))
        self.assertEqual(item_key("formula", "a^2 + b^2 = c^2"), item_key("formula", "a² + b² = c²"))
        self.assertEqual(item_key("formula", "v = s : t"), item_key("formula", "v = s ÷ t"))
        self.assertNotIn(":", item_key("formula", "v = s : t"))
        self.assertLessEqual(len(item_key("formula", "x = " + " + ".join(f"a{index}" for index in range(80)))), 120)

    def test_named_items_use_the_term_key(self):
        self.assertEqual(item_key("formula", "Satz des Pythagoras"), term_key("Satz des Pythagoras"))
        self.assertEqual(item_key("rule", "if + simple present, will + infinitive"),
                         term_key("if + simple present, will + infinitive"))
        self.assertEqual(item_key("theory", "Pflichtethik"), term_key("Pflichtethik"))
        self.assertEqual(item_key("formula", "="), term_key("="))


class CatalogueTests(unittest.TestCase):
    def test_strategy_keys_and_kinds(self):
        self.assertEqual(set(CARD_KINDS_BY_STRATEGY), STRATEGY_KEYS)
        for key, kinds in CARD_KINDS_BY_STRATEGY.items():
            self.assertEqual(kinds[0], "term", key)
            self.assertTrue(set(kinds) <= set(ITEM_KINDS), key)
            self.assertEqual(len(kinds), len(set(kinds)), key)

    def test_card_kinds_match_the_subject_strategies(self):
        # One source of truth in practice: the lesson engine's strategy decides which
        # items a subject teaches, and the cards must follow it.
        from learnova.lessons.strategies import REGISTRY

        self.assertEqual(set(REGISTRY), set(CARD_KINDS_BY_STRATEGY))
        for key, strategy in REGISTRY.items():
            self.assertEqual(tuple(strategy.card_kinds), CARD_KINDS_BY_STRATEGY[key], key)

    def test_every_item_kind_has_a_prompt_with_known_placeholders(self):
        self.assertTrue(set(ITEM_KINDS) | {"term_example", "term_choice"} <= set(CARD_PROMPTS))
        for kind, msgid in CARD_PROMPTS.items():
            self.assertEqual(set(re.findall(r"{(\w*)}", msgid)), {"example"} if kind == "term_example" else {"name"})
        self.assertEqual(re.findall(r"{(\w*)}", EXAMPLE_LINE), ["example"])
        self.assertNotIn("{", ADDED_MARKER)


class BuildCardsTests(unittest.TestCase):
    def setUp(self):
        self.cards = build_cards([kant_topic(), utilitarism_topic()], strategy_key="ethics", source_label=label)

    def test_every_card_passes_normalize_card_unchanged(self):
        self.assertTrue(self.cards)
        for card in self.cards:
            self.assertEqual(set(card), CARD_FIELDS)
            self.assertIn(card["type"], CARD_TYPES)
            normalized = normalize_card(card)
            self.assertEqual(normalized, {field: card[field] for field in normalized}, card["origin_key"])
            self.assertLessEqual(len(card["origin_key"]), 160)
            self.assertLessEqual(len(card["source_reference"]), 255)
            self.assertTrue(card["back"])

    def test_definition_card_from_notes(self):
        card = by_origin(self.cards, "term")[term_key("Kategorischer Imperativ")]
        self.assertEqual(card["type"], "term_definition")
        self.assertEqual(card["front"], "What does the term “Kategorischer Imperativ” mean?")
        self.assertEqual(card["back"], "Eine Regel, die immer und für alle gilt.")
        self.assertIn("Ein unbedingt gültiges Gebot der praktischen Vernunft.", card["explanation"])
        self.assertIn("Example: Lügen ist nie erlaubt", card["explanation"])
        self.assertNotIn(ADDED_MARKER, card["explanation"])
        self.assertEqual(card["source_reference"], "Ethik-Blatt-1.pdf, S. 1")
        self.assertEqual(card["source_page_ids"], [11])
        self.assertEqual(card["topic_key"], "kant-pflichtethik")
        self.assertTrue(card["origin_key"].startswith("kant-pflichtethik:term:"))

    def test_definition_without_simple_version_is_the_back_and_not_repeated(self):
        card = by_origin(self.cards, "term")[term_key("Pflicht")]
        self.assertEqual(card["back"], "Die Notwendigkeit einer Handlung aus Achtung fürs Gesetz.")
        self.assertEqual(card["explanation"], "")

    def test_definition_written_by_the_model_is_marked(self):
        card = by_origin(self.cards, "term")[term_key("Maxime")]
        self.assertEqual(card["back"], "Eine persönliche Handlungsregel.")
        self.assertIn("Ein subjektiver Grundsatz des Wollens.", card["explanation"])
        self.assertIn(ADDED_MARKER, card["explanation"])
        self.assertNotIn(term_key("Maxime"), by_origin(self.cards, "term_fill"))   # no verified quote

    def test_terms_not_in_the_notes_or_without_definition_get_no_card(self):
        keys = {origin_identity(card["origin_key"]).split(":", 1)[1] for card in self.cards}
        self.assertNotIn(term_key("Autonomie"), keys)
        self.assertNotIn(term_key("Heteronomie"), keys)
        self.assertFalse(any("Lügenverbot" in card["front"] for card in self.cards))   # additional example

    def test_fill_blank_from_the_verified_quote(self):
        card = by_origin(self.cards, "term_fill")[term_key("Kategorischer Imperativ")]
        self.assertEqual(card["type"], "fill_blank")
        self.assertEqual(card["front"], "Der ____ ist ein unbedingt gültiges Gebot der praktischen Vernunft.")
        self.assertEqual(card["back"], "kategorische Imperativ")         # the form the gap needs
        self.assertNotIn(term_key("Neigung"), by_origin(self.cards, "term_fill"))      # no quote at all

    def test_example_card(self):
        card = by_origin(self.cards, "term_example")[term_key("Kategorischer Imperativ")]
        self.assertEqual(card["front"],
                         "Which term fits this example? Lügen ist nie erlaubt, auch nicht aus Höflichkeit.")
        self.assertEqual(card["back"], "Kategorischer Imperativ")
        self.assertEqual(card["type"], "question_answer")

    def test_multiple_choice_is_deterministic_and_contains_the_answer(self):
        choice = by_origin(self.cards, "term_choice")
        self.assertTrue(choice)
        for card in choice.values():
            self.assertEqual(card["type"], "multiple_choice")
            self.assertEqual(len(card["options"]), 4)
            self.assertEqual(len({option.casefold() for option in card["options"]}), 4)
            self.assertIn(card["back"], card["options"])
        again = build_cards([kant_topic(), utilitarism_topic()], strategy_key="ethics", source_label=label)
        self.assertEqual([card["options"] for card in by_origin(again, "term_choice").values()],
                         [card["options"] for card in choice.values()])
        # The correct answer is not always in the same place, or a student learns the slot.
        self.assertGreater(len({card["options"].index(card["back"]) for card in choice.values()}), 1)

    def test_multiple_choice_answer_is_the_terms_own_definition_among_other_terms(self):
        definitions = {card["back"] for card in by_origin(self.cards, "term").values()}
        choice = by_origin(self.cards, "term_choice")
        imperative = choice[term_key("Kategorischer Imperativ")]
        self.assertEqual(imperative["back"], "Eine Regel, die immer und für alle gilt.")
        self.assertEqual(imperative["front"], "Which definition fits “Kategorischer Imperativ”?")
        for card in choice.values():
            self.assertTrue(set(card["options"]) <= definitions, card["options"])

    def test_no_multiple_choice_with_fewer_than_three_other_definitions(self):
        topic = {"key": "small", "title": "Klein", "source_page_ids": [11],
                 "terms": [term("Pflicht", "Notwendigkeit einer Handlung."), term("Neigung", "Ein Wunsch."),
                           term("Maxime", "Ein Grundsatz.")]}
        cards = build_cards([topic], strategy_key="ethics")
        self.assertFalse(by_origin(cards, "term_choice"))
        topic["terms"].append(term("Würde", "Ein absoluter innerer Wert."))
        self.assertEqual(len(by_origin(build_cards([topic], strategy_key="ethics"), "term_choice")), 4)

    def test_ethics_items(self):
        theory = by_origin(self.cards, "theory")[term_key("Pflichtethik")]
        self.assertEqual(theory["front"], "What does the theory “Pflichtethik” say?")
        self.assertEqual(theory["type"], "question_answer")
        experiment = by_origin(self.cards, "thought_experiment")[term_key("Der Kaufmann")]
        self.assertEqual(experiment["front"], "What is the thought experiment “Der Kaufmann” meant to show?")
        self.assertEqual(experiment["source_reference"], "Ethik-Blatt-1.pdf, S. 2")
        self.assertIn(term_key("Immanuel Kant"), by_origin(self.cards, "person"))
        self.assertIn(term_key("Handlungsutilitarismus"), by_origin(self.cards, "position"))
        # Ethics cards are about ideas: events and formulas of the same notes are not carded.
        self.assertFalse(by_origin(self.cards, "event"))
        self.assertFalse(by_origin(self.cards, "formula"))

    def test_fronts_are_unique_because_the_editor_dedupes_on_them(self):
        fronts = [card["front"].casefold() for card in self.cards]
        self.assertEqual(len(fronts), len(set(fronts)))
        origins = [card["origin_key"] for card in self.cards]
        self.assertEqual(len(origins), len(set(origins)))

    def test_cards_that_would_share_a_front_are_made_once(self):
        # Two terms with one example would give two identical example fronts; a
        # translation may also give two prompts one wording. The editor's save keeps
        # only one card per front, so the second is never created.
        topic = {"key": "k", "title": "K", "source_page_ids": [11], "terms": [
            term("Pflicht", "Notwendigkeit einer Handlung.", example="Ein Versprechen halten, obwohl es schwer fällt."),
            term("Achtung", "Respekt vor dem Gesetz.", example="Ein Versprechen halten, obwohl es schwer fällt.")],
            "items": [item("theory", "Deontologie", "Moral aus Pflicht."),
                      item("position", "Deontologie", "Handeln nach Regeln.")]}
        cards = build_cards([topic], strategy_key="ethics")
        self.assertEqual(len(by_origin(cards, "term_example")), 1)
        self.assertEqual(len(by_origin(cards, "theory")) + len(by_origin(cards, "position")), 2)
        same = {CARD_PROMPTS["theory"]: "Was besagt „{name}“?", CARD_PROMPTS["position"]: "Was besagt „{name}“?"}
        translated = build_cards([topic], strategy_key="ethics", translate=lambda text: same.get(text, text))
        self.assertEqual(len(by_origin(translated, "theory")) + len(by_origin(translated, "position")), 1)
        fronts = [card["front"].casefold() for card in translated]
        self.assertEqual(len(fronts), len(set(fronts)))

    def test_no_gap_text_from_an_unverified_quote(self):
        topic = kant_topic()
        topic["terms"][0]["definition_from_notes"] = False       # the excerpt was not found on the page
        cards = build_cards([topic], strategy_key="ethics")
        self.assertNotIn(term_key("Kategorischer Imperativ"), by_origin(cards, "term_fill"))
        self.assertIn(term_key("Pflicht"), by_origin(cards, "term_fill"))


class SubjectFormatTests(unittest.TestCase):
    TOPIC = {
        "key": "mixed", "title": "Gemischt", "source_page_ids": [11], "difficulty": 1,
        "terms": [term("Lehnwort", "Ein Wort aus einer anderen Sprache.")],
        "items": [
            item("event", "Sturm auf die Bastille", "Am 14. Juli 1789 stürmten Pariser die Bastille."),
            item("date", "1789", "Beginn der Französischen Revolution."),
            item("formula", "a² + b² = c²", "Satz des Pythagoras im rechtwinkligen Dreieck."),
            item("method", "Polynomdivision", "Teile schrittweise wie bei der schriftlichen Division."),
            item("rule", "if-clause type 1", "If + simple present, will + infinitive."),
            item("experiment", "Fotosynthese mit Wasserpest", "Unter Licht steigen Sauerstoffblasen auf."),
            item("work", "Der Erlkönig", "Ballade von Goethe über einen Vater und sein sterbendes Kind."),
            item("thought_experiment", "Höhlengleichnis", "Gefangene sehen nur Schatten an der Wand."),
        ],
    }

    def kinds(self, strategy):
        cards = build_cards([self.TOPIC], strategy_key=strategy)
        for card in cards:
            normalized = normalize_card(card)
            self.assertEqual(normalized, {field: card[field] for field in normalized})
        return {origin_identity(card["origin_key"]).split(":", 1)[0]: card for card in cards}

    def test_history_cards_events_and_dates(self):
        cards = self.kinds("history")
        self.assertEqual(cards["date"]["front"], "What happened in 1789?")
        self.assertEqual(cards["event"]["front"], "What happened: Sturm auf die Bastille?")
        self.assertNotIn("formula", cards)

    def test_mathematics_cards_formulas_as_formula_explanations(self):
        cards = self.kinds("mathematics")
        self.assertEqual(cards["formula"]["type"], "formula_explanation")
        self.assertEqual(cards["formula"]["front"], "What does the formula a² + b² = c² mean and when do you use it?")
        self.assertEqual(cards["method"]["front"], "How do you use Polynomdivision?")
        self.assertNotIn("event", cards)
        self.assertEqual(cards["term"]["difficulty"], "easy")

    def test_languages_card_grammar_rules(self):
        cards = self.kinds("languages")
        self.assertEqual(cards["rule"]["front"], "Explain the rule: if-clause type 1")
        self.assertNotIn("formula", cards)
        self.assertNotIn("event", cards)

    def test_sciences_card_experiments(self):
        cards = self.kinds("sciences")
        self.assertEqual(cards["experiment"]["front"], "What does the experiment “Fotosynthese mit Wasserpest” show?")
        self.assertEqual(cards["formula"]["type"], "formula_explanation")
        self.assertNotIn("work", cards)

    def test_german_cards_works(self):
        cards = self.kinds("german")
        self.assertEqual(cards["work"]["front"], "What is “Der Erlkönig” about?")
        self.assertNotIn("experiment", cards)

    def test_ethics_cards_thought_experiments(self):
        cards = self.kinds("ethics")
        self.assertIn("thought_experiment", cards)
        self.assertNotIn("date", cards)

    def test_unknown_strategy_uses_general(self):
        self.assertEqual(set(self.kinds("astrology")), set(self.kinds("general")))

    def test_the_strategys_own_card_kinds_take_precedence(self):
        cards = build_cards([self.TOPIC], strategy_key="history", card_kinds=("term", "work"))
        # term_own_words (LESSON-BE) is a practice variant of the term, not an item kind.
        self.assertEqual({origin_identity(card["origin_key"]).split(":", 1)[0] for card in cards},
                         {"term", "work", "term_own_words"})
        self.assertEqual(len(by_origin(build_cards([self.TOPIC], strategy_key="history", card_kinds=()), "term")), 1)


class DedupeAndLimitsTests(unittest.TestCase):
    def test_same_term_in_two_topics_is_one_card_with_both_sources(self):
        cards = build_cards([kant_topic(), utilitarism_topic()], strategy_key="ethics", source_label=label)
        duty = [card for card in cards if origin_identity(card["origin_key"]) == f"term:{term_key('Pflicht')}"]
        self.assertEqual(len(duty), 1)
        self.assertEqual(duty[0]["topic_key"], "kant-pflichtethik")                 # first topic wins
        self.assertEqual(duty[0]["back"], "Die Notwendigkeit einer Handlung aus Achtung fürs Gesetz.")
        self.assertEqual(duty[0]["source_page_ids"], [12, 13])
        self.assertEqual(duty[0]["source_reference"], "Ethik-Blatt-1.pdf, S. 2; Ethik-Blatt-2.pdf, S. 1")

    def test_every_page_a_term_appears_on_is_its_source(self):
        topic = kant_topic()
        topic["terms"][1]["page_ids"] = [12, 14]          # normalize_topic lists every page it is on
        card = by_origin(build_cards([topic], strategy_key="ethics", source_label=label), "term")[term_key("Pflicht")]
        self.assertEqual(card["source_page_ids"], [12, 14])
        self.assertEqual(card["source_reference"], "Ethik-Blatt-1.pdf, S. 2; Ethik-Blatt-3.pdf, S. 4")

    def test_item_that_is_also_a_term_gets_only_the_term_card(self):
        topic = kant_topic()
        topic["items"].append(item("theory", "Kategorischer Imperativ", "Kants oberstes Prinzip der Moral."))
        cards = build_cards([topic], strategy_key="ethics")
        self.assertNotIn(term_key("Kategorischer Imperativ"), by_origin(cards, "theory"))
        self.assertIn(term_key("Kategorischer Imperativ"), by_origin(cards, "term"))

    def test_per_topic_caps_only_the_practice_variants(self):
        topic = kant_topic()
        full = build_cards([topic], strategy_key="ethics")
        # The practice variants: gap, example, multiple choice and (LESSON-BE) own words.
        own = [card for card in full if card["type"] in ("term_definition", "question_answer")
               and origin_identity(card["origin_key"]).split(":", 1)[0] not in ("term_example", "term_own_words")]
        capped = build_cards([topic], strategy_key="ethics", per_topic=2)
        variants = [card for card in capped if card not in own]
        self.assertEqual(len(variants), 2)
        self.assertTrue(all(card in capped for card in own))
        self.assertEqual(build_cards([topic], strategy_key="ethics", per_topic=0), own)

    def test_a_topic_with_more_terms_than_the_cap_keeps_every_definition_card(self):
        # topics.normalize_topic keeps up to 40 terms per topic; none may lose its card.
        names = [f"Wort{chr(97 + index // 26)}{chr(97 + index % 26)}q" for index in range(36)]
        topic = {"key": "viele", "title": "Viele", "source_page_ids": [11],
                 "terms": [term(name, f"Definition Nummer {index}.") for index, name in enumerate(names)]}
        cards = build_cards([topic], strategy_key="ethics")
        self.assertEqual(set(by_origin(cards, "term")), {term_key(name) for name in names})
        self.assertEqual(len(by_origin(cards, "term_choice")), 25)          # the default cap of variants

    def test_formula_cards_do_not_merge(self):
        topic = {"key": "binom", "title": "Binomische Formeln", "source_page_ids": [11], "items": [
            item("formula", "(a+b)² = a² + 2ab + b²", "Erste binomische Formel."),
            item("formula", "(a−b)² = a² − 2ab + b²", "Zweite binomische Formel."),
            item("formula", "(a+b)(a−b) = a² − b²", "Dritte binomische Formel."),
            item("formula", "F = m · a", "Grundgleichung der Mechanik."),
            item("formula", "F = m · g", "Gewichtskraft."),
        ]}
        cards = build_cards([topic], strategy_key="mathematics")
        self.assertEqual(len(by_origin(cards, "formula")), 5)
        self.assertEqual({card["type"] for card in cards}, {"formula_explanation"})
        # A re-analysis that spells a formula differently finds the same card.
        respelled = {**topic, "items": [item("formula", "F=m*a", "Newton.")]}
        existing = {card["origin_key"]: card["source_page_ids"] for card in cards}
        self.assertEqual(plan_sync(existing, set(existing), build_cards([respelled], strategy_key="mathematics")),
                         ([], []))

    def test_long_keys_and_labels_stay_within_the_columns(self):
        name = " ".join(f"Wort{letter}x" for letter in "abcdefghijklmnopqrst")     # a 119-character key
        topic = {"key": "k" * 120, "title": "Lang", "source_page_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9],
                 "terms": [term(name, "Eine Definition.", page_id=None),
                           term("Aaa " + name, "Eine andere Definition.", page_id=None)]}
        self.assertGreater(len(term_key(name)), 110)
        cards = build_cards([topic], strategy_key="general", source_label=lambda page: f"{'Datei' * 10}.pdf, S. {page}")
        self.assertEqual(len(by_origin(cards, "term")), 2)
        for card in cards:
            self.assertLessEqual(len(card["origin_key"]), 160)
            self.assertLessEqual(len(card["topic_key"]), 120)
            self.assertLessEqual(len(card["source_reference"]), 200)
            self.assertTrue(card["source_reference"].endswith("; …"))
            self.assertEqual(card["source_page_ids"], [1, 2, 3, 4, 5, 6, 7, 8, 9])   # topic pages as fallback
            normalized = normalize_card(card)
            self.assertEqual(normalized, {field: card[field] for field in normalized})
        self.assertEqual(len({origin_identity(card["origin_key"]) for card in cards}), len(cards))

    def test_a_failing_page_label_does_not_cost_the_card(self):
        cards = build_cards([kant_topic()], strategy_key="ethics", source_label=lambda page: {}[page])
        self.assertTrue(cards)
        self.assertEqual({card["source_reference"] for card in cards}, {""})

    def test_translation_and_broken_translation(self):
        german = {CARD_PROMPTS["term"]: "Was bedeutet der Begriff „{name}“?",
                  EXAMPLE_LINE: "Beispiel: {example}", ADDED_MARKER: "Von Learnova ergänzte Erklärung",
                  CARD_PROMPTS["theory"]: "Was besagt die Theorie „{Name}“?"}      # broken placeholder
        cards = build_cards([kant_topic()], strategy_key="ethics", translate=lambda text: german.get(text, text))
        definition = by_origin(cards, "term")[term_key("Kategorischer Imperativ")]
        self.assertEqual(definition["front"], "Was bedeutet der Begriff „Kategorischer Imperativ“?")
        self.assertIn("Beispiel: Lügen", definition["explanation"])
        self.assertIn("Von Learnova ergänzte Erklärung", by_origin(cards, "term")[term_key("Maxime")]["explanation"])
        self.assertEqual(by_origin(cards, "theory")[term_key("Pflichtethik")]["front"],
                         "What does the theory “Pflichtethik” say?")

    def test_unusable_input_is_skipped(self):
        topics = [None, {"key": "x", "title": "X", "terms": ["Pflicht: kein Dict", {"term": ""}, 3],
                         "items": "nope"}, kant_topic()]
        self.assertEqual(build_cards(topics, strategy_key="ethics"),   # type: ignore[arg-type]
                         build_cards([kant_topic()], strategy_key="ethics"))
        odd = [{"terms": [{"term": {"x": 1}, "definition": "D", "in_notes": True},
                          {"term": "Pflicht", "definition": ["D"], "in_notes": True},
                          {"term": "Würde", "definition": "Ein Wert.", "in_notes": "yes", "page_id": "11",
                           "page_ids": [True, "x", None]}],
                "items": [{"kind": None, "name": "a", "detail": "b", "in_notes": True}],
                "difficulty": True, "source_page_ids": "nope"}]
        cards = build_cards(odd, strategy_key="ethics")
        self.assertEqual([(card["front"], card["source_page_ids"], card["topic_key"]) for card in cards],
                         [("What does the term “Würde” mean?", [11], "topic")])
        self.assertEqual(normalize_card(cards[0])["difficulty"], "medium")


class NormalizedTopicTests(unittest.TestCase):
    """Cards from real `topics.normalize_topic` output, checked against the page text."""

    PAGES = {
        11: "Kants Pflichtethik. Der kategorische Imperativ ist ein unbedingt gültiges Gebot der praktischen "
            "Vernunft. Eine Maxime ist ein subjektiver Grundsatz des Wollens.",
        12: "Pflicht ist die Notwendigkeit einer Handlung aus Achtung fürs Gesetz. Der Kaufmann ist ehrlich, "
            "aber nur aus Eigennutz.",
    }
    RAW = {
        "title": "Kants Pflichtethik", "source_page_ids": [11, 12], "difficulty": "hard",
        "terms": [
            {"term": "Kategorischer Imperativ", "definition": "Ein unbedingt gültiges Gebot der praktischen Vernunft.",
             "simple_definition": "Eine Regel, die immer gilt.", "page_id": 11,
             "quote": "Der kategorische Imperativ ist ein unbedingt gültiges Gebot der praktischen Vernunft."},
            {"term": "Maxime", "definition": "Ein subjektiver Grundsatz.", "page_id": 11,
             "quote": "Eine Maxime ist eine Regel, die ich mir selbst gebe."},      # not on the page
            {"term": "Autonomie", "definition": "Selbstgesetzgebung des Willens.", "page_id": 12},
        ],
        "items": [{"kind": "Gedankenexperiment", "name": "Der Kaufmann", "detail": "Ehrlich nur aus Eigennutz.",
                   "page_id": 12},
                  {"kind": "theory", "name": "Hedonismus", "detail": "Lust ist das höchste Gut.", "page_id": 12}],
    }

    def test_cards_from_the_notes_only(self):
        topic = project_topics.normalize_topic(self.RAW, set(self.PAGES), page_text=self.PAGES)
        assert topic is not None
        cards = build_cards([topic], strategy_key="ethics", source_label=label)
        for card in cards:
            normalized = normalize_card(card)
            self.assertEqual(normalized, {field: card[field] for field in normalized})
            self.assertEqual(card["topic_key"], topic["key"])
            self.assertEqual(card["difficulty"], "hard")
        definitions = by_origin(cards, "term")
        self.assertEqual(set(definitions), {term_key("Kategorischer Imperativ"), term_key("Maxime")})   # no Autonomie
        self.assertNotIn(ADDED_MARKER, definitions[term_key("Kategorischer Imperativ")]["explanation"])
        self.assertIn(ADDED_MARKER, definitions[term_key("Maxime")]["explanation"])          # quote not verified
        self.assertEqual(set(by_origin(cards, "term_fill")), {term_key("Kategorischer Imperativ")})
        self.assertIn(term_key("Der Kaufmann"), by_origin(cards, "thought_experiment"))
        self.assertFalse(by_origin(cards, "theory"))                                          # Hedonismus is not there
        self.assertEqual(by_origin(cards, "thought_experiment")[term_key("Der Kaufmann")]["source_reference"],
                         "Ethik-Blatt-1.pdf, S. 2")


class PlanSyncTests(unittest.TestCase):
    def setUp(self):
        self.cards = build_cards([kant_topic(), utilitarism_topic()], strategy_key="ethics", source_label=label)

    def test_first_sync_adds_everything(self):
        added, unions = plan_sync({}, set(), self.cards)
        self.assertEqual([card["origin_key"] for card in added], [card["origin_key"] for card in self.cards])
        self.assertEqual(unions, [])

    def test_resync_adds_nothing_and_never_overwrites(self):
        existing = {card["origin_key"]: card["source_page_ids"] for card in self.cards}
        synced = set(existing)
        again = build_cards([kant_topic(), utilitarism_topic()], strategy_key="ethics", source_label=label)
        self.assertEqual(plan_sync(existing, synced, again), ([], []))

    def test_new_pages_are_united_into_the_existing_card(self):
        existing = {card["origin_key"]: list(card["source_page_ids"]) for card in self.cards}
        topic = kant_topic()
        topic["terms"][0]["page_id"] = 14
        cards = build_cards([topic, utilitarism_topic()], strategy_key="ethics", source_label=label)
        added, unions = plan_sync(existing, set(existing), cards)
        self.assertEqual(added, [])
        origin = next(card["origin_key"] for card in self.cards
                      if origin_identity(card["origin_key"]) == f"term:{term_key('Kategorischer Imperativ')}")
        self.assertIn((origin, [11, 14]), unions)
        self.assertTrue(all(len(pages) > len(existing[key]) for key, pages in unions))

    def test_deleted_card_is_not_added_again(self):
        existing = {card["origin_key"]: card["source_page_ids"] for card in self.cards}
        synced = set(existing)
        deleted = next(key for key in existing if origin_identity(key) == f"term:{term_key('Maxime')}")
        existing.pop(deleted)
        added, _ = plan_sync(existing, synced, self.cards)
        self.assertEqual(added, [])

    def test_term_moved_to_another_topic_is_not_a_new_card(self):
        existing = {card["origin_key"]: card["source_page_ids"] for card in self.cards}
        # Re-analysis: the utilitarism topic now comes first and also holds "Pflicht".
        cards = build_cards([utilitarism_topic(), kant_topic()], strategy_key="ethics", source_label=label)
        duty = next(card for card in cards if origin_identity(card["origin_key"]) == f"term:{term_key('Pflicht')}")
        self.assertTrue(duty["origin_key"].startswith("utilitarism:"))
        added, unions = plan_sync(existing, set(existing), cards)
        self.assertEqual(added, [])
        self.assertEqual(unions, [])
        # ...and when the student deleted it under its old topic, it stays deleted.
        old = next(key for key in existing if origin_identity(key) == f"term:{term_key('Pflicht')}")
        remaining = {key: pages for key, pages in existing.items() if key != old}
        self.assertEqual(plan_sync(remaining, set(existing), cards)[0], [])

    def test_only_new_terms_are_added_and_each_once(self):
        existing = {card["origin_key"]: card["source_page_ids"] for card in self.cards}
        topic = utilitarism_topic()
        topic["terms"].append(term("Hedonismus", "Lust ist das höchste Gut.", page_id=13))
        cards = build_cards([kant_topic(), topic], strategy_key="ethics", source_label=label)
        added, _ = plan_sync(existing, set(existing), cards + cards)
        identities = [origin_identity(card["origin_key"]) for card in added]
        self.assertIn(f"term:{term_key('Hedonismus')}", identities)
        self.assertEqual(len(identities), len(set(identities)))
        self.assertTrue(all(term_key("Hedonismus") in identity for identity in identities))

    def test_synced_keys_straight_from_json(self):
        # analysis_json["flashcard_keys"] is a JSON list, or missing on the first sync.
        existing = {card["origin_key"]: card["source_page_ids"] for card in self.cards}
        deleted = next(key for key in existing if origin_identity(key) == f"term:{term_key('Maxime')}")
        remaining = {key: pages for key, pages in existing.items() if key != deleted}
        self.assertEqual(plan_sync(remaining, sorted(existing), self.cards)[0], [])
        self.assertEqual(len(plan_sync({}, None, self.cards)[0]), len(self.cards))
        self.assertEqual(plan_sync(None, None, [None, "x", {"origin_key": ""}]), ([], []))   # type: ignore[arg-type]

    def test_existing_page_ids_in_any_stored_shape(self):
        card = self.cards[0]
        row = SimpleNamespace(source_page_ids_json="[11]")
        for stored in ("[11]", {"source_page_ids": [11]}, {"source_page_ids_json": "[11]"}, (11,), row, "broken"):
            added, unions = plan_sync({card["origin_key"]: stored}, set(), [{**card, "source_page_ids": [11, 12]}])
            self.assertEqual(added, [])
            self.assertEqual(unions, [(card["origin_key"], [11, 12])])


class TermsMentionedTests(unittest.TestCase):
    def test_inflected_mentions_count(self):
        terms = [term("Der kategorische Imperativ", "x"), term("Pflicht", "y"), term("Neigung", "z")]
        text = "Kant formuliert den kategorischen Imperativ. Wer aus Pflichten handelt, handelt moralisch."
        self.assertEqual(terms_mentioned(terms, text), [term_key("Kategorischer Imperativ"), term_key("Pflicht")])

    def test_items_and_unusable_entries(self):
        entries = [item("person", "Immanuel Kant", "x"), {"name": ""}, "Kant"]
        self.assertEqual(terms_mentioned(entries, "Immanuel Kants Ethik"), [term_key("Immanuel Kant")])   # type: ignore[list-item]
        self.assertEqual(terms_mentioned([term("Pflicht", "y")], ""), [])
        self.assertEqual(terms_mentioned(None, None), [])   # type: ignore[arg-type]

    def test_keys_are_the_card_keys(self):
        formulas = [item("formula", "a² + b² = c²", "x"), item("formula", "c² = a² + b²", "y")]
        self.assertEqual(terms_mentioned(formulas, "Es gilt a² + b² = c² und c² = a² + b²."),
                         [item_key("formula", "a² + b² = c²"), item_key("formula", "c² = a² + b²")])
        self.assertNotIn(term_key("Pflicht"), terms_mentioned([term("Pflicht", "y")], "Pflichtethik nach Kant"))


class StudyDirectionAndAdditionsTests(unittest.TestCase):
    """LESSON-BE: reverse_answer / ONE_WAY for the study modes, own-words cards, and the
    ADDED_MARKER on text the notes do not contain (when page_text is given)."""

    PAGE_TEXT = {11: "Der kategorische Imperativ ist ein unbedingt gültiges Gebot der praktischen Vernunft. "
                     "Kant (1724-1804) ist ein Philosoph der Aufklärung und begründet die Pflichtethik.",
                 12: "Pflicht ist die Notwendigkeit einer Handlung aus Achtung fürs Gesetz. Der Kaufmann ist ehrlich.",
                 13: "Pflichten und Nutzen."}

    def test_term_cards_answer_back_to_front_with_the_bare_term(self):
        cards = build_cards([kant_topic()], strategy_key="ethics")
        self.assertEqual(by_origin(cards, "term")[term_key("Pflicht")]["reverse_answer"], "Pflicht")
        self.assertEqual(by_origin(cards, "person")[term_key("Immanuel Kant")]["reverse_answer"], "Immanuel Kant")
        self.assertEqual(by_origin(cards, "term_choice")[term_key("Pflicht")]["reverse_answer"], "Pflicht")
        for variant in ("term_fill", "term_example", "term_own_words"):
            for card in by_origin(cards, variant).values():
                # Own-words answers are graded by the student (flashcards.modes): tagged so.
                expected = [ONE_WAY, OWN_WORDS] if variant == "term_own_words" else [ONE_WAY]
                self.assertEqual(card["tags"], expected, variant)
                self.assertEqual(card["reverse_answer"], "", variant)
        self.assertEqual(by_origin(cards, "term")[term_key("Pflicht")]["tags"], [])

    def test_own_words_cards_only_from_definitions_in_the_notes_and_not_for_vocabulary(self):
        cards = build_cards([kant_topic()], strategy_key="ethics")
        own = by_origin(cards, "term_own_words")
        self.assertIn(term_key("Pflicht"), own)
        self.assertNotIn(term_key("Maxime"), own, "a definition the model wrote is not the yardstick")
        self.assertEqual(own[term_key("Pflicht")]["front"], "Explain “Pflicht” in your own words.")
        self.assertEqual(by_origin(build_cards([kant_topic()], strategy_key="languages"), "term_own_words"), {})

    def test_examples_and_details_not_in_the_notes_are_marked(self):
        cards = build_cards([kant_topic(), utilitarism_topic()], strategy_key="ethics", page_text=self.PAGE_TEXT)
        imperative = by_origin(cards, "term")[term_key("Kategorischer Imperativ")]
        # The definition is the notes' own; the example "Lügen ist nie erlaubt" is not on the page.
        self.assertIn(f"Example: Lügen ist nie erlaubt, auch nicht aus Höflichkeit. ({ADDED_MARKER})",
                      imperative["explanation"])
        self.assertIn(ADDED_MARKER, by_origin(cards, "term_example")[term_key("Kategorischer Imperativ")]["explanation"])
        self.assertEqual(by_origin(cards, "person")[term_key("Immanuel Kant")]["explanation"], "",
                         "a detail the page says (in other words) is not marked")
        self.assertEqual(by_origin(cards, "thought_experiment")[term_key("Der Kaufmann")]["explanation"],
                         ADDED_MARKER, "'aus Eigennutz' is not in the notes")
        self.assertEqual(by_origin(cards, "theory")[term_key("Pflichtethik")]["explanation"], ADDED_MARKER)
        # Without page text nothing can be checked, so nothing is marked (the old behaviour).
        unchecked = build_cards([kant_topic()], strategy_key="ethics")
        self.assertEqual(by_origin(unchecked, "theory")[term_key("Pflichtethik")]["explanation"], "")

    def test_translated_marker(self):
        german = {ADDED_MARKER: "Von Learnova ergänzte Erklärung"}
        cards = build_cards([kant_topic()], strategy_key="ethics", page_text=self.PAGE_TEXT,
                            translate=lambda text: german.get(text, text))
        self.assertEqual(by_origin(cards, "theory")[term_key("Pflichtethik")]["explanation"],
                         "Von Learnova ergänzte Erklärung")


class SubjectCardFormatTests(unittest.TestCase):
    """History causes and consequences, English sentence patterns and irregular forms (P3-06)."""

    def test_causes_and_consequences_become_history_and_geography_cards(self):
        topic = {"key": "industrialisierung", "title": "Industrialisierung", "source_page_ids": [11], "terms": [],
                 "items": [item("cause_effect", "Industrialisierung", "Ursache: Dampfmaschine; Folge: Verstädterung")]}
        for strategy in ("history", "geography"):
            with self.subTest(strategy=strategy):
                cards = by_origin(build_cards([topic], strategy_key=strategy), "cause_effect")
                card = cards[term_key("Industrialisierung")]
                self.assertEqual(card["front"], "What are the causes and consequences of Industrialisierung?")
                self.assertIn("Dampfmaschine", card["back"])

    def test_sentence_patterns_and_irregular_forms_become_language_cards(self):
        topic = {"key": "tenses", "title": "Past tenses", "source_page_ids": [11],
                 "terms": [term("go – went – gone", "gehen", quote="go – went – gone: gehen"),
                           term("relative clause", "a clause that describes a noun")],
                 "items": [item("structure", "if-clause type 1", "if + simple present, will + infinitive")]}
        cards = build_cards([topic], strategy_key="languages")
        pattern = by_origin(cards, "structure")[term_key("if-clause type 1")]
        self.assertEqual(pattern["front"], "How is the sentence pattern “if-clause type 1” built?")
        forms = by_origin(cards, "term_forms")
        self.assertEqual([card["front"] for card in forms.values()], ["Give the forms of “go”."])
        self.assertEqual(next(iter(forms.values()))["back"], "go – went – gone")
        self.assertEqual(next(iter(forms.values()))["tags"], [ONE_WAY])
        self.assertIn(term_key("go – went – gone"), by_origin(cards, "term"), "the meaning card stays")
        self.assertFalse(by_origin(build_cards([topic], strategy_key="ethics"), "term_forms"),
                         "forms cards are for language subjects")

    def test_the_forms_are_graded_right_when_typed_on_a_phone(self):
        # FIXR review: the forms card's answer is "go – went – gone" with en dashes, which
        # a phone keyboard does not offer; "go went gone" was graded wrong in every
        # written mode. Forms count as right in any separator, but never in another order.
        from learnova.flashcards.modes import answer_is_correct

        for typed in ("go went gone", "go, went, gone", "go - went - gone", "Go – went – gone", "go/went/gone"):
            with self.subTest(typed=typed):
                self.assertTrue(answer_is_correct(typed, "go – went – gone", "written"))
        self.assertFalse(answer_is_correct("go gone went", "go – went – gone", "written"))
        self.assertFalse(answer_is_correct("go went", "go – went – gone", "written"))
        # Answers that are no list of forms keep their exact comparison: a sign is meaning.
        self.assertFalse(answer_is_correct("3", "-3", "written"))
        self.assertFalse(answer_is_correct("Nord Süd", "Nord-Süd", "written"))

    def test_new_kinds_are_read_from_german_labels(self):
        self.assertEqual(project_topics.item_kind("Ursachen und Folgen"), "cause_effect")
        self.assertEqual(project_topics.item_kind("Satzbau"), "structure")


if __name__ == "__main__":
    unittest.main()

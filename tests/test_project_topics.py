"""Project Mode topic extraction helpers (learnova/projects/topics.py)."""

import json
import random
import unittest

from tests.typing_helpers import present

from learnova.projects import topics as project_topics
from learnova.projects.topics import (
    ITEM_KINDS, MAX_BATCH_CHARS, MAX_BATCH_PAGES, MAX_TERMS, StoredTopic, cap_topics, item_kind,
    merge_topics, normalize_topic, overlap_suggestions, page_batches, reconcile, resolve_related,
    same_topic, split_batch, term_key, topic_key, uncovered_pages,
)

FIFTEEN = [
    "Gleichförmige Bewegung", "Geschwindigkeit", "Beschleunigung", "Freier Fall", "Kraft",
    "Newtonsche Gesetze", "Reibung", "Arbeit", "Energie", "Leistung", "Impuls",
    "Kreisbewegung", "Gravitation", "Schwingungen", "Wellen",
]


def twenty_pages():
    """20 pages; topic i lives on page i+1, and topics 2, 6 and 10 continue onto the next page."""
    pages = []
    for number in range(1, 21):
        topic = FIFTEEN[min(number - 1, 14)]
        pages.append({"page_id": 100 + number, "page_order": number,
                      "text": f"{topic}: Merksatz und Beispiele auf Seite {number}. " * 120})
    return pages


def model_topics_for(batch):
    """What a model returns for one batch: one topic per page, numbered headings, repeats."""
    out = []
    for page in batch:
        index = min(page["page_order"] - 1, 14)
        title = FIFTEEN[index]
        if page["page_order"] % 2:
            title = f"{page['page_order']}. {title}"     # numbered heading on odd pages
        out.append({
            "title": title, "source_page_ids": [page["page_id"], 999],   # 999: a page that does not exist
            "standard_explanation": f"Erklärung {title}", "important_facts": [f"Fakt zu {FIFTEEN[index]}"],
            "estimated_minutes": 15, "difficulty": "hard" if index in (2, 5, 10) else "medium",
            "prerequisites": ["Geschwindigkeit"] if FIFTEEN[index] == "Beschleunigung" else [],
            "recall_cards": [
                {"kind": "recall", "prompt": "Merksatz?", "answer": "Merksatz", "source_text": "Merksatz und Beispiele"},
                {"kind": "recall", "prompt": "Erfunden?", "answer": "x", "source_text": "steht nirgends"},
                "not a card",
            ],
        })
    return out


class BatchingTests(unittest.TestCase):
    def test_twenty_long_pages_are_split_into_budget_sized_batches(self):
        batches = page_batches(twenty_pages())
        self.assertGreater(len(batches), 1)
        self.assertEqual(sum(len(batch) for batch in batches), 20)
        for batch in batches:
            self.assertLessEqual(sum(len(page["text"]) for page in batch), MAX_BATCH_CHARS)
        # Consecutive and in order: a topic running over two pages usually stays together.
        orders = [page["page_order"] for batch in batches for page in batch]
        self.assertEqual(orders, list(range(1, 21)))


class NormaliseAndMergeTests(unittest.TestCase):
    def setUp(self):
        self.pages = twenty_pages()
        self.valid = {page["page_id"] for page in self.pages}
        self.text = {page["page_id"]: page["text"] for page in self.pages}
        self.order = {page["page_id"]: page["page_order"] for page in self.pages}

    def extract(self):
        raw = [topic for batch in page_batches(self.pages) for topic in model_topics_for(batch)]
        normalized = [normalize_topic(item, self.valid, page_text=self.text) for item in raw]
        return merge_topics([item for item in normalized if item], page_order=self.order)

    def test_twenty_pages_give_fifteen_distinct_topics_in_note_order(self):
        topics = self.extract()
        self.assertEqual([topic["title"].split(". ")[-1] for topic in topics], FIFTEEN)
        self.assertEqual([topic["position"] for topic in topics], list(range(1, 16)))

    def test_repeated_and_continued_topics_keep_all_their_pages(self):
        topics = {topic["key"]: topic for topic in self.extract()}
        waves = topics[topic_key("Wellen")]
        self.assertEqual(waves["source_page_ids"], [115, 116, 117, 118, 119, 120])
        self.assertEqual(len(waves["important_facts"]), 1, "duplicate facts are merged")

    def test_invalid_pages_and_unsupported_cards_are_dropped_not_fatal(self):
        topic = self.extract()[0]
        self.assertNotIn(999, topic["source_page_ids"])
        self.assertEqual([card["prompt"] for card in topic["recall_cards"]], ["Merksatz?"])
        self.assertIsNone(normalize_topic({"title": "Nowhere", "source_page_ids": [999]}, self.valid))
        self.assertIsNone(normalize_topic({"title": "", "source_page_ids": [101]}, self.valid))
        self.assertIsNone(normalize_topic("nonsense", self.valid))

    def test_prerequisites_resolve_to_topics_in_this_material_only(self):
        topics = {topic["key"]: topic for topic in self.extract()}
        acceleration = topics[topic_key("Beschleunigung")]
        self.assertEqual(acceleration["prerequisites"], [topic_key("Geschwindigkeit")])
        lone = present(normalize_topic(
            {"title": "Optik", "source_page_ids": [101], "prerequisites": ["Quantenphysik"]}, self.valid))
        self.assertEqual(merge_topics([lone], page_order=self.order)[0]["prerequisites"], [])

    def test_levels_and_minutes_are_bounded(self):
        topic = present(normalize_topic({"title": "T", "source_page_ids": [101], "difficulty": "schwer",
                                         "importance": 9, "estimated_minutes": 500}, self.valid))
        self.assertEqual((topic["difficulty"], topic["importance"], topic["estimated_minutes"]), (3, 3, 45))

    def test_topic_key_ignores_numbering_case_and_umlauts(self):
        self.assertEqual(topic_key("3. Newtonsche Gesetze"), topic_key("newtonsche gesetze"))
        self.assertEqual(topic_key("Größen und Einheiten"), topic_key("Groessen Einheiten"))


class ReconcileTests(unittest.TestCase):
    def topics(self, *titles):
        valid = {101}
        return [present(normalize_topic({"title": title, "source_page_ids": [101]}, valid)) for title in titles]

    def test_reanalysis_updates_in_place_and_never_loses_student_work(self):
        stored = [
            StoredTopic(1, topic_key("Kraft"), "Kraft"),
            StoredTopic(2, topic_key("Reibung"), "Reibung", has_progress=True),
            StoredTopic(3, topic_key("Mein eigenes Thema"), "Mein eigenes Thema", user_edited=True),
            StoredTopic(4, topic_key("Alter Entwurf"), "Alter Entwurf"),
        ]
        result = reconcile(stored, self.topics("Kraft", "Energie"))
        self.assertEqual([item_id for item_id, _ in result.update], [1])
        self.assertEqual([topic["title"] for topic in result.create], ["Energie"])
        self.assertEqual(sorted(result.keep), [2, 3])
        self.assertEqual(result.delete, [4])

    def test_reconciling_the_same_extraction_twice_creates_nothing(self):
        topics = self.topics("Kraft", "Energie")
        stored = [StoredTopic(index, topic["key"], topic["title"]) for index, topic in enumerate(topics, start=1)]
        result = reconcile(stored, self.topics("Kraft", "Energie"))
        self.assertEqual(result.create, [])
        self.assertEqual(result.delete, [])
        self.assertEqual(len(result.update), 2)

    def test_topics_stored_with_the_first_key_version_are_still_recognised(self):
        # Keys stored before digits and "nach" counted: "Text 1" was "text", and
        # "Utilitarismus nach Bentham" was "bentham-nach-utilitarismu".
        stored = [
            StoredTopic(1, "text", "Text 1", has_progress=True),
            StoredTopic(2, "bentham-nach-utilitarismu", "Utilitarismus nach Bentham", has_progress=True),
        ]
        result = reconcile(stored, self.topics("Text 1", "Text 2", "Utilitarismus nach Bentham", "Utilitarismus nach Mill"))
        self.assertEqual([(item_id, topic["title"]) for item_id, topic in result.update],
                         [(1, "Text 1"), (2, "Utilitarismus nach Bentham")])
        self.assertEqual([topic["title"] for topic in result.create], ["Text 2", "Utilitarismus nach Mill"])
        self.assertEqual((result.keep, result.delete), ([], []))

    def test_an_exact_match_is_never_taken_by_a_weaker_one(self):
        stored = [StoredTopic(1, topic_key("Kategorischer Imperativ"), "Kategorischer Imperativ")]
        result = reconcile(stored, self.topics("Kategorischer Imperativ Beispiele", "Kategorischer Imperativ"))
        self.assertEqual([(item_id, topic["title"]) for item_id, topic in result.update], [(1, "Kategorischer Imperativ")])
        self.assertEqual([topic["title"] for topic in result.create], ["Kategorischer Imperativ Beispiele"])

    def test_a_different_ethics_topic_never_overwrites_a_stored_one(self):
        # The old 0.6 overlap rule let "... nach Mill" update the stored "... nach Bentham".
        stored = [StoredTopic(1, topic_key("Utilitarismus nach Bentham"), "Utilitarismus nach Bentham", has_progress=True)]
        result = reconcile(stored, self.topics("Utilitarismus nach Mill"))
        self.assertEqual(result.update, [])
        self.assertEqual([topic["title"] for topic in result.create], ["Utilitarismus nach Mill"])
        self.assertEqual(result.keep, [1])


# --------------------------------------------------------------------------- topic map (v4)

# Ethics titles from the six-papers investigation. Under the old rule (overlap >= 0.6 of
# the shorter key, digits dropped, "nach" not a stop word) these pairs were merged.
DIFFERENT_ETHICS_TOPICS = [
    ("Utilitarismus nach Bentham", "Utilitarismus nach Mill"),
    ("Gerechtigkeit nach Aristoteles", "Gerechtigkeit nach Rawls"),
    ("Ethik", "Ethik nach Kant"),
    ("Einführung in die Ethik", "Angewandte Ethik"),
    ("Grundlagen der Ethik", "Ethik und Wissenschaft"),
    ("Ethik", "Ethik der Verantwortung (Jonas)"),
    ("Freiheit", "Freiheit und Determinismus"),
    ("Kant", "Kants kategorischer Imperativ"),
    ("Text 1", "Text 2"),
    ("Utilitarismus Teil 1", "Utilitarismus Teil 2"),
    # One added word that names an aspect is a lesson of its own (review fix).
    ("Kategorischer Imperativ", "Kritik am kategorischen Imperativ"),
    ("Utilitarismus nach Mill", "Utilitarismus nach Mill: Einwände"),
    ("Pflichtethik nach Kant", "Grenzen der Pflichtethik Kants"),
    ("Gerechtigkeit nach Rawls", "Rawls' Gerechtigkeit im Vergleich"),
]
SAME_ETHICS_TOPIC = [
    ("Kategorischer Imperativ", "Kants kategorischer Imperativ"),
    ("Kants Ethik", "Ethik nach Kant"),
    ("Der kategorische Imperativ", "Kategorischer Imperativ"),
    ("Utilitarismus nach Bentham", "Benthams Utilitarismus"),
    ("Höhlengleichnis (Platon)", "Platons Höhlengleichnis"),
    ("2. Gerechtigkeit nach Rawls", "Gerechtigkeit (Rawls)"),
]
FOURTEEN_ETHICS_TITLES = [
    "Einführung in die Ethik", "Angewandte Ethik", "Ethik und Wissenschaft", "Ethik der Verantwortung (Jonas)",
    "Ethik nach Kant", "Kants kategorischer Imperativ", "Utilitarismus nach Bentham", "Utilitarismus nach Mill",
    "Gerechtigkeit nach Aristoteles", "Gerechtigkeit nach Rawls", "Freiheit", "Freiheit und Determinismus",
    "Text 1", "Text 2",
]

ETHICS_PAGES = {
    201: ("Kant: Pflichtethik\nDer kategorische Imperativ lautet: Handle nur nach derjenigen Maxime, durch die du "
          "zugleich wollen kannst, dass sie ein allgemeines Gesetz werde.\n"
          "Pflicht ist die Notwendigkeit einer Handlung aus Achtung fürs Gesetz."),
    202: ("Beispiel: das Lügenverbot. Wer lügt, kann nicht wollen, dass Lügen ein allgemeines Gesetz wird.\n"
          "Maximen sind subjektive Grundsätze des Handelns. Das Höhlengleichnis von Platon zeigt den Weg zur Erkenntnis."),
    203: "Utilitarismus nach Bentham: Das hedonistische Kalkül misst Freude und Leid. Nutzen und Glück werden verrechnet.",
}


def ethics_topic(page_ids=(201, 202), *, page_text=ETHICS_PAGES, **fields):
    raw = {"title": "Kants Pflichtethik", "source_page_ids": list(page_ids), **fields}
    return present(normalize_topic(raw, set(ETHICS_PAGES), page_text=page_text))


def stub(title, pages, *, terms=(), importance=2, kind="main", text="", **fields):
    """A normalised topic whose pages hold its title, its terms and `text`."""
    page_text = {page: " ".join([title, *terms, text]) for page in pages}
    raw = {"title": title, "source_page_ids": list(pages), "importance": importance, "kind": kind,
           "terms": [{"term": term} for term in terms], **fields}
    return present(normalize_topic(raw, set(pages), page_text=page_text))


class TopicIdentityTests(unittest.TestCase):
    def test_distinct_ethics_topics_are_never_merged(self):
        for first, second in DIFFERENT_ETHICS_TOPICS:
            with self.subTest(first=first, second=second):
                self.assertFalse(same_topic(topic_key(first), topic_key(second)))

    def test_one_ethics_topic_under_two_names_is_merged(self):
        for first, second in SAME_ETHICS_TOPIC:
            with self.subTest(first=first, second=second):
                self.assertTrue(same_topic(topic_key(first), topic_key(second)))

    def test_fourteen_ethics_titles_stay_fourteen_topics(self):
        pages = list(range(1, len(FOURTEEN_ETHICS_TITLES) + 1))
        topics = [stub(title, [page]) for title, page in zip(FOURTEEN_ETHICS_TITLES, pages)]
        merged = merge_topics(topics, page_order={page: page for page in pages})
        self.assertEqual([topic["title"] for topic in merged], FOURTEEN_ETHICS_TITLES)

    def test_digits_count_but_the_notes_numbering_does_not(self):
        self.assertNotEqual(topic_key("Text 1"), topic_key("Text 2"))
        self.assertIn("1", topic_key("Text 1").split("-"))
        for numbered in ("3. Pflichtethik", "Kapitel 3: Pflichtethik", "2) Pflichtethik", "1.2 Pflichtethik",
                         "Thema 4 – Pflichtethik", "1. a) Pflichtethik"):
            with self.subTest(numbered=numbered):
                self.assertEqual(topic_key(numbered), topic_key("Pflichtethik"))
        self.assertEqual(topic_key("9.11.1989 Mauerfall"), "11-1989-9-mauerfall", "a date is not numbering")
        self.assertEqual(topic_key("II. Kategorischer Imperativ"), topic_key("Kategorischer Imperativ"), "Roman numbering")
        self.assertNotEqual(topic_key("I. Weltkrieg"), topic_key("II. Weltkrieg"), "a Roman ordinal that names it")

    def test_an_ordinal_that_names_the_topic_is_kept(self):
        self.assertNotEqual(topic_key("1. Weltkrieg"), topic_key("2. Weltkrieg"))
        self.assertNotEqual(topic_key("1. binomische Formel"), topic_key("2. binomische Formel"))
        self.assertEqual(topic_key("19. Jahrhundert"), "19-jahrhundert")
        # The plural is the notes' numbering, the singular names one law.
        self.assertEqual(topic_key("3. Newtonsche Gesetze"), topic_key("Newtonsche Gesetze"))
        self.assertNotEqual(topic_key("3. Newtonsches Gesetz"), topic_key("1. Newtonsches Gesetz"))

    def test_according_to_words_are_not_part_of_the_identity(self):
        for title in ("Gerechtigkeit nach Rawls", "Gerechtigkeit laut Rawls", "Gerechtigkeit gemäß Rawls",
                      "Gerechtigkeit bei Rawls", "Über Gerechtigkeit: Rawls"):
            with self.subTest(title=title):
                self.assertEqual(topic_key(title), "gerechtigkeit-rawl")
        self.assertEqual(topic_key("Kant vs. Mill"), topic_key("Kant versus Mill"))

    def test_merging_never_chains_two_distinct_topics_through_a_third(self):
        bentham, both, mill = "Utilitarismus nach Bentham", "Utilitarismus: Bentham und Mill", "Utilitarismus nach Mill"
        self.assertTrue(same_topic(topic_key(both), topic_key(bentham)))
        self.assertTrue(same_topic(topic_key(both), topic_key(mill)))
        for order in ((bentham, both, mill), (both, bentham, mill), (both, mill, bentham)):
            with self.subTest(order=order):
                topics = [stub(title, [page]) for page, title in enumerate(order, start=1)]
                merged = merge_topics(topics, page_order={1: 1, 2: 2, 3: 3})
                self.assertEqual(len(merged), 2)
                keys = [set(topic["source_page_ids"]) for topic in merged]
                bentham_page, mill_page = order.index(bentham) + 1, order.index(mill) + 1
                self.assertFalse(any({bentham_page, mill_page} <= pages for pages in keys))

    def test_term_keys_ignore_articles_umlauts_and_plurals(self):
        self.assertEqual(term_key("Der kategorische Imperativ"), term_key("Kategorischer Imperativ"))
        self.assertEqual(term_key("Kalkül"), term_key("Kalkuel"))
        self.assertEqual(term_key("Pflicht"), term_key("Pflichten"))
        self.assertEqual(term_key("Grundlagen"), "grundlagen", "a stop-word term keeps its own key")
        self.assertEqual(term_key("Ursache/Wirkung"), "ursach-wirk", "a slash joins two concepts")

    def test_term_keys_match_the_flashcard_identity(self):
        try:
            from learnova.projects import terms    # Track D; may not exist yet
        except ImportError:
            self.skipTest("learnova.projects.terms is not there yet")
        for term in ("Der kategorische Imperativ", "Pflichten", "Kalkül", "Grundlagen", "Ursache/Wirkung",
                     "Maxime (Kant)", "der Bahnhof, -höfe", "Über", "1. Hauptsatz", "CO2"):
            with self.subTest(term=term):
                self.assertEqual(term_key(term), terms.term_key(term))


class PageBatchTests(unittest.TestCase):
    def test_a_long_page_is_split_at_paragraphs_and_never_cut(self):
        text = "\n\n".join(f"Absatz {number}. " + "Kant prüft Maximen am Gesetz. " * 30 for number in range(30))
        pages = [{"page_id": 7, "page_order": 1, "text": text, "formulas": ["F = m · a"]},
                 {"page_id": 8, "page_order": 2, "text": "Kurze Seite."}]
        batches = page_batches(pages)
        entries = [entry for batch in batches for entry in batch]
        parts = [entry for entry in entries if entry["page_id"] == 7]
        self.assertGreaterEqual(len(parts), 3)
        self.assertEqual([entry["part"] for entry in parts], list(range(1, len(parts) + 1)))
        self.assertEqual({entry["parts"] for entry in parts}, {len(parts)})
        self.assertEqual({tuple(entry["formulas"]) for entry in parts}, {("F = m · a",)})
        self.assertEqual("".join(text.split()), "".join("".join(entry["text"] for entry in parts).split()),
                         "nothing of the page is lost")
        for entry in parts[1:]:
            self.assertTrue(entry["text"].startswith("Absatz"), "cut at a paragraph break")
        self.assertNotIn("part", entries[-1])
        for batch in batches:
            self.assertLessEqual(len(batch), MAX_BATCH_PAGES)
            self.assertLessEqual(sum(len(entry["text"]) for entry in batch), MAX_BATCH_CHARS)

    def test_a_page_without_paragraphs_is_cut_after_a_sentence(self):
        text = "Der Utilitarismus bewertet Handlungen nach ihren Folgen. " * 400
        parts = [entry for batch in page_batches([{"page_id": 1, "text": text}]) for entry in batch]
        self.assertGreater(len(parts), 1)
        for entry in parts:
            self.assertTrue(entry["text"].endswith("Folgen."))
        unbroken = "x" * 20_000
        pieces = [entry["text"] for batch in page_batches([{"page_id": 2, "text": unbroken}]) for entry in batch]
        self.assertEqual("".join(pieces), unbroken)

    def test_a_page_read_later_changes_only_its_own_batch(self):
        def batches(orders):
            pages = [{"page_id": 300 + order, "page_order": order, "text": f"Seite {order}"} for order in orders]
            return [[page["page_id"] for page in batch] for batch in page_batches(pages)]

        before, after = batches([1, 2, 4, 5, 6, 7, 9]), batches([1, 2, 3, 4, 5, 6, 7, 9])
        self.assertEqual(before, [[301, 302, 304], [305, 306, 307], [309]])
        self.assertEqual(after, [[301, 302, 303, 304], [305, 306, 307], [309]], "the cached later batches still match")

    def test_a_batch_holds_at_most_four_pages(self):
        pages = [{"page_id": number, "page_order": number, "text": "kurz"} for number in range(1, 11)]
        self.assertEqual([len(batch) for batch in page_batches(pages)], [4, 4, 2])
        self.assertEqual([len(batch) for batch in page_batches(pages, max_pages=10)], [10])

    def test_a_failed_batch_can_be_halved_down_to_half_a_page(self):
        pages = [{"page_id": number, "text": f"Seite {number}"} for number in range(1, 4)]
        first, second = split_batch(pages)
        self.assertEqual(([page["page_id"] for page in first], [page["page_id"] for page in second]), ([1, 2], [3]))
        text = "Pflicht ist die Notwendigkeit einer Handlung aus Achtung fürs Gesetz. " * 20
        first, second = split_batch([{"page_id": 9, "text": text}])
        self.assertEqual([(entry["page_id"], entry["part"], entry["parts"]) for entry in first + second], [(9, 1, 2), (9, 2, 2)])
        self.assertEqual("".join(text.split()), "".join((first[0]["text"] + second[0]["text"]).split()))
        self.assertTrue(first[0]["text"].endswith("Gesetz."))
        first, second = split_batch([{"page_id": 9, "text": text, "part": 2, "parts": 3}])
        self.assertEqual([(entry["part"], entry["half"]) for entry in first + second], [(2, 1), (2, 2)])
        short = [{"page_id": 9, "text": "Zu kurz zum Teilen."}]
        self.assertEqual(split_batch(short), (short, []))
        self.assertEqual(split_batch([]), ([], []))


class TopicMapTermTests(unittest.TestCase):
    QUOTE = "Handle nur nach derjenigen Maxime, durch die du zugleich wollen kannst"

    def test_terms_are_kept_only_when_the_notes_mention_them(self):
        topic = ethics_topic(terms=[
            {"term": "Kategorischer Imperativ", "definition": "Handle nur nach derjenigen Maxime ...", "page_id": 202},
            {"term": "Hedonistisches Kalkül", "definition": "Bentham misst Freude und Leid."},   # page 203: not this topic
            {"term": "Maxime", "definition": "subjektiver Grundsatz des Handelns", "page_id": 999},
            {"term": "", "definition": "no term"}, "   ",
        ])
        terms = {term["term"]: term for term in topic["terms"]}
        self.assertEqual(list(terms), ["Kategorischer Imperativ", "Maxime"])
        self.assertTrue(all(term["in_notes"] for term in terms.values()))
        self.assertEqual(terms["Kategorischer Imperativ"]["page_id"], 201, "the page where it appears, not the claimed one")
        self.assertEqual(terms["Maxime"]["page_id"], 201, "first page where it appears")
        self.assertEqual(terms["Maxime"]["page_ids"], [201, 202], "Maximen counts as Maxime")
        self.assertEqual(terms["Maxime"]["key"], term_key("Maxime"))

    def test_a_definition_is_from_the_notes_only_when_its_quote_is_on_the_page(self):
        topic = ethics_topic(terms=[
            {"term": "Kategorischer Imperativ", "definition": "Handle nur nach ...",
             "quote": "„handle nur nach derjenigen   MAXIME, durch die du zugleich wollen kannst“"},
            {"term": "Maxime", "definition": "subjektiver Grundsatz", "quote": "Maximen sind subjektive Grundsätze"},
            {"term": "Pflicht", "definition": "Notwendigkeit", "quote": "Pflicht bedeutet, dass man muss"},
        ])
        terms = {term["term"]: term for term in topic["terms"]}
        self.assertTrue(terms["Kategorischer Imperativ"]["definition_from_notes"], "case, spacing and quote marks do not count")
        self.assertEqual(terms["Maxime"]["page_id"], 202, "the page that holds the quote")
        self.assertTrue(terms["Maxime"]["definition_from_notes"])
        self.assertFalse(terms["Pflicht"]["definition_from_notes"])
        self.assertEqual(terms["Pflicht"]["quote"], "", "a paraphrase is not shown as a quote")
        self.assertTrue(terms["Pflicht"]["in_notes"], "the term itself is in the notes")

    def test_legacy_term_shapes_are_read(self):
        topic = ethics_topic(
            terms=[{"term": "Kategorischer Imperativ", "definition": "Prüfe deine Maxime."}],
            fachbegriffe=[{"Begriff": "Der kategorische Imperativ", "Bedeutung": "anders", "Beispiel": "Lügenverbot"},
                          {"begriff": "Pflicht", "erklärung": "Notwendigkeit einer Handlung aus Achtung"}],
            definitions=["Maxime: subjektiver Grundsatz", "Achtung – ein Gefühl vor dem Gesetz",
                         "Lügenverbot - man darf nicht lügen", "Unter Erkenntnis versteht man das Erfassen der Ideen",
                         {"Höhlengleichnis": "Platons Bild für den Weg zur Erkenntnis"}],
            vocabulary=["Gesetz", "Kalkül"],
        )
        terms = {term["term"]: term for term in topic["terms"]}
        self.assertEqual(list(terms), ["Kategorischer Imperativ", "Pflicht", "Maxime", "Achtung", "Lügenverbot",
                                       "Erkenntnis", "Höhlengleichnis", "Gesetz"])
        self.assertEqual(terms["Kategorischer Imperativ"]["definition"], "Prüfe deine Maxime.", "the first one wins")
        self.assertEqual(terms["Kategorischer Imperativ"]["example"], "Lügenverbot", "a duplicate fills the gaps")
        self.assertEqual(terms["Pflicht"]["definition"], "Notwendigkeit einer Handlung aus Achtung")
        self.assertEqual(terms["Achtung"]["definition"], "ein Gefühl vor dem Gesetz")
        self.assertEqual(terms["Erkenntnis"]["definition"], "Unter Erkenntnis versteht man das Erfassen der Ideen")
        self.assertEqual(terms["Höhlengleichnis"]["definition"], "Platons Bild für den Weg zur Erkenntnis")
        self.assertEqual(terms["Gesetz"]["definition"], "")
        self.assertNotIn("Kalkül", terms, "not on this topic's pages")
        # The old columns still get strings (the Notes page used to print dicts).
        self.assertIn("Maxime: subjektiver Grundsatz", topic["definitions"])
        self.assertNotIn("Gesetz", [entry.split(":")[0] for entry in topic["definitions"]], "no definition, no entry")
        self.assertEqual(topic["vocabulary"], list(terms))

    def test_verified_definitions_become_recall_cards(self):
        raw_cards = [{"prompt": "Was ist Pflicht?", "answer": "Notwendigkeit", "source_text": "Pflicht ist die Notwendigkeit"},
                     {"prompt": "Erfunden?", "answer": "x", "source_text": "steht nirgends"}]
        terms = [{"term": "Kategorischer Imperativ", "definition": "Handle nur nach derjenigen Maxime ...", "quote": self.QUOTE},
                 {"term": "Pflicht", "definition": "Notwendigkeit einer Handlung"}]
        raw = {"title": "Kants Pflichtethik", "source_page_ids": [201], "terms": terms, "recall_cards": raw_cards}
        topic = present(normalize_topic(raw, {201}, page_text=ETHICS_PAGES, recall_prompt="Was bedeutet „{term}“?"))
        self.assertEqual(topic["recall_cards"], [
            {"kind": "recall", "prompt": "Was ist Pflicht?", "answer": "Notwendigkeit", "source_text": "Pflicht ist die Notwendigkeit"},
            {"kind": "definition", "prompt": "Was bedeutet „Kategorischer Imperativ“?",
             "answer": "Handle nur nach derjenigen Maxime ...", "source_text": self.QUOTE},
        ])
        english = present(normalize_topic(raw, {201}, page_text=ETHICS_PAGES))
        self.assertEqual(english["recall_cards"][1]["prompt"], "What does “Kategorischer Imperativ” mean?")

    def test_without_page_text_nothing_is_claimed_to_be_from_the_notes(self):
        raw = {"title": "Kants Pflichtethik", "source_page_ids": [201],
               "terms": [{"term": "Pflicht", "definition": "Notwendigkeit", "page_id": 201, "quote": "Pflicht ist"}],
               "items": [{"kind": "theory", "name": "Pflichtethik"}]}
        topic = present(normalize_topic(raw, {201}))
        self.assertEqual([(term["term"], term["in_notes"], term["definition_from_notes"], term["page_id"])
                          for term in topic["terms"]], [("Pflicht", False, False, 201)])
        self.assertEqual([(item["kind"], item["in_notes"]) for item in topic["items"]], [("theory", False)])
        self.assertFalse(any(card["kind"] == "definition" for card in topic["recall_cards"]))

    def test_at_most_forty_terms_per_topic(self):
        words = [f"Fachwort{number}" for number in range(MAX_TERMS + 10)]
        raw = {"title": "Viele Begriffe", "source_page_ids": [1], "terms": [{"term": word} for word in words]}
        topic = present(normalize_topic(raw, {1}, page_text={1: " ".join(words)}))
        self.assertEqual(len(topic["terms"]), MAX_TERMS)


class TopicMapItemTests(unittest.TestCase):
    def test_item_kinds_accept_german_and_english_labels(self):
        expected = {"Beispiel": "example", "Versuch": "experiment", "Theorie": "theory", "Philosoph": "person",
                    "people": "person", "Formel": "formula", "Ablauf": "process", "Schaubild": "diagram",
                    "Karte": "diagram", "Ereignis": "event", "Jahr": "date", "Grammatik": "rule",
                    "Gedankenexperiment": "thought_experiment", "Gleichnis": "thought_experiment",
                    "parable": "thought_experiment", "Gedicht": "work", "Verfahren": "method", "Quelle": "source",
                    "Region": "place", "Standpunkt": "position", "institution": "institution",
                    "argument": "argument", "irgendwas": "example", "": "example"}
        self.assertEqual({label: item_kind(label) for label in expected}, expected)
        self.assertTrue(set(expected.values()) <= set(ITEM_KINDS))

    def test_only_examples_may_come_from_outside_the_notes(self):
        topic = ethics_topic(items=[
            {"kind": "Gedankenexperiment", "name": "Höhlengleichnis", "detail": "Platons Bild", "page_id": 201},
            {"kind": "Philosoph", "name": "Kant"},
            {"kind": "Theorie", "name": "Kontraktualismus", "detail": "Moral als Vertrag"},
            {"kind": "Beispiel", "name": "Trolley-Problem", "detail": "Weiche umstellen", "page_id": 201},
            {"kind": "Beispiel", "name": "Lügenverbot", "detail": "Lügen kann kein Gesetz werden"},
        ])
        items = {item["name"]: item for item in topic["items"]}
        self.assertEqual(list(items), ["Höhlengleichnis", "Kant", "Trolley-Problem", "Lügenverbot"])
        self.assertEqual((items["Höhlengleichnis"]["kind"], items["Höhlengleichnis"]["page_id"]), ("thought_experiment", 202))
        self.assertEqual((items["Kant"]["kind"], items["Kant"]["in_notes"]), ("person", True))
        self.assertEqual((items["Trolley-Problem"]["in_notes"], items["Trolley-Problem"]["page_id"]), (False, None),
                         "kept as an additional example, with no page")
        self.assertEqual((items["Lügenverbot"]["in_notes"], items["Lügenverbot"]["page_id"]), (True, 202))

    def test_legacy_per_kind_lists_become_items(self):
        topic = ethics_topic(
            items={"theories": [{"name": "Pflichtethik", "Beschreibung": "Moral aus Pflicht und Achtung fürs Gesetz"}]},
            people=["Kant – Philosoph der Pflicht", {"Platon": "Höhlengleichnis"}],
            examples=["Lügenverbot: Wer lügt, kann nicht wollen, dass Lügen ein Gesetz wird"],
            thought_experiments=["Höhlengleichnis"],
            arguments=[{"kind": "argument", "name": "Verallgemeinerung", "detail": "Maxime als allgemeines Gesetz wollen"}],
        )
        self.assertEqual([(item["kind"], item["name"]) for item in topic["items"]], [
            ("theory", "Pflichtethik"), ("example", "Lügenverbot"), ("person", "Kant"), ("person", "Platon"),
            ("argument", "Verallgemeinerung"), ("thought_experiment", "Höhlengleichnis"),
        ])
        self.assertEqual(topic["examples"], ["Lügenverbot: Wer lügt, kann nicht wollen, dass Lügen ein Gesetz wird"])
        self.assertEqual(topic["important_facts"], ["Pflichtethik: Moral aus Pflicht und Achtung fürs Gesetz",
                                                    "Verallgemeinerung: Maxime als allgemeines Gesetz wollen"])

    def test_a_formula_string_is_one_formula(self):
        raw = {"title": "Bewegung", "source_page_ids": [1], "formulas": ["v = s : t", "F = m · a – Grundgleichung"]}
        topic = present(normalize_topic(raw, {1}, page_text={1: "Es gilt v = s : t und F = m · a (Grundgleichung)."}))
        self.assertEqual([(item["name"], item["detail"]) for item in topic["items"]],
                         [("v = s : t", ""), ("F = m · a", "Grundgleichung")])
        self.assertEqual(topic["formulas"], ["v = s : t", "F = m · a: Grundgleichung"])

    def test_duplicate_items_are_merged_by_kind_and_name(self):
        topic = ethics_topic(items=[{"kind": "person", "name": "Kant"},
                                    {"kind": "Philosoph", "name": "kant", "detail": "Pflichtethik"},
                                    {"kind": "work", "name": "Kant"}])
        self.assertEqual([(item["kind"], item["name"], item["detail"]) for item in topic["items"]],
                         [("person", "Kant", "Pflichtethik"), ("work", "Kant", "")])


class TopicMapShapeTests(unittest.TestCase):
    def test_summaries_and_the_old_explanations_fill_each_other(self):
        new = ethics_topic(summary="Kant begründet Moral mit Vernunft. Der Imperativ prüft Maximen.")
        self.assertEqual(new["simple_summary"], "Kant begründet Moral mit Vernunft.")
        self.assertEqual(new["standard_explanation"], new["summary"])
        self.assertEqual(new["detailed_explanation"], new["summary"], "the Detailed tab is never empty")
        self.assertEqual(new["simple_explanation"], new["simple_summary"])
        old = ethics_topic(simple_explanation="Einfach.", standard_explanation="Standard.", detailed_explanation="Genau.")
        self.assertEqual((old["summary"], old["simple_summary"]), ("Standard.", "Einfach."))
        self.assertEqual((old["simple_explanation"], old["detailed_explanation"]), ("Einfach.", "Genau."))

    def test_kind_exam_questions_and_related_titles(self):
        topic = ethics_topic(kind="Unterthema", exam_questions=["Erkläre den kategorischen Imperativ."],
                             likely_exam_questions=["erkläre den  kategorischen Imperativ.", {"frage": "Wende ihn an."}],
                             related_topics=["Utilitarismus", "Kants Pflichtethik", {"title": "Tugendethik"}])
        self.assertEqual(topic["kind"], "subtopic")
        self.assertEqual(ethics_topic(kind="whatever")["kind"], "main")
        self.assertEqual(topic["exam_questions"], ["Erkläre den kategorischen Imperativ.", "Wende ihn an."])
        self.assertEqual(topic["likely_exam_questions"], topic["exam_questions"])
        self.assertEqual(topic["related_topics"], ["Utilitarismus", "Tugendethik"], "not itself")
        self.assertEqual(topic["relationships"][:2], ["Utilitarismus", "Tugendethik"])

    def test_page_ids_and_levels_are_repaired_not_rejected(self):
        topic = present(normalize_topic({"title": "T", "source_page_ids": "201, 202", "importance": "high",
                                         "difficulty": "3"}, set(ETHICS_PAGES)))
        self.assertEqual((topic["source_page_ids"], topic["importance"], topic["difficulty"]), ([201, 202], 3, 3))
        self.assertEqual(present(normalize_topic({"title": "T", "source_page_ids": [201.0]}, {201}))["source_page_ids"], [201])

    def test_shared_constants_match_the_lesson_strategies(self):
        self.assertEqual(ITEM_KINDS, (
            "term", "example", "experiment", "theory", "person", "argument", "formula", "process",
            "diagram", "event", "date", "rule", "thought_experiment", "work", "method", "source",
            "place", "institution", "position", "cause_effect", "structure"))
        try:
            from learnova.lessons.strategies import base     # Track A; may not exist yet
        except ImportError:
            self.skipTest("learnova.lessons.strategies.base is not there yet")
        for name in ("ITEM_KINDS", "PART_KINDS", "SERVER_PART_KINDS", "KNOWLEDGE_STATES"):
            self.assertEqual(getattr(project_topics, name), getattr(base, name), name)


class TopicMapMergeTests(unittest.TestCase):
    def test_merging_combines_terms_items_questions_and_summaries(self):
        first = stub("Kategorischer Imperativ", [1], terms=("Maxime",), text="Kant", kind="subtopic", summary="Kurz.",
                     exam_questions=["Was prüft er?"], items=[{"kind": "person", "name": "Kant", "detail": ""}])
        second = stub("Kants kategorischer Imperativ", [2], terms=("Maxime", "Pflicht"), summary="Länger erklärt.",
                      exam_questions=["Was prüft er?", "Wende ihn an."],
                      items=[{"kind": "person", "name": "Kant", "detail": "Philosoph"}])
        merged = merge_topics([first, second], page_order={1: 1, 2: 2})
        self.assertEqual(len(merged), 1)
        topic = merged[0]
        self.assertEqual(topic["kind"], "main", "main if either is main")
        self.assertEqual(topic["summary"], "Länger erklärt.")
        self.assertEqual([(term["term"], term["page_ids"]) for term in topic["terms"]], [("Maxime", [1, 2]), ("Pflicht", [2])])
        self.assertEqual([(item["name"], item["detail"]) for item in topic["items"]], [("Kant", "Philosoph")])
        self.assertEqual(topic["exam_questions"], ["Was prüft er?", "Wende ihn an."])
        self.assertEqual(first["terms"][0]["page_ids"], [1], "the inputs are not changed")

    def test_related_titles_resolve_to_topic_keys(self):
        kant = stub("Kants Pflichtethik", [1], related_topics=["Utilitarismus nach Mill", "Quantenphysik"])
        mill = stub("Utilitarismus nach Mill", [2], related_topics=["Pflichtethik nach Kant"])
        merged = merge_topics([kant, mill], page_order={1: 1, 2: 2})
        self.assertEqual(merged[0]["related"], [topic_key("Utilitarismus nach Mill")])
        self.assertEqual(merged[1]["related"], [topic_key("Kants Pflichtethik")])
        merged[1]["related_topics"] = []
        resolve_related(merged)
        self.assertEqual(merged[1]["related"], [])

    def test_merge_does_not_cap_and_cap_topics_reports_what_it_drops(self):
        many = [stub(f"Text {number}", [number]) for number in range(1, 71)]
        merged = merge_topics(many, page_order={number: number for number in range(1, 71)})
        self.assertEqual(len(merged), 70, "digits keep the texts apart, and nothing is cut silently")
        kept, dropped = cap_topics(merged)
        self.assertEqual((len(kept), dropped), (60, 10))
        topics = merge_topics([
            stub("Freiheit", [1], importance=1), stub("Pflichtethik", [2], importance=3),
            stub("Utilitarismus", [3], importance=2, prerequisites=["Freiheit", "Pflichtethik"]),
            stub("Tugendethik", [4], importance=3, kind="subtopic"), stub("Gerechtigkeit", [5], importance=1),
        ], page_order={number: number for number in range(1, 6)})
        kept, dropped = cap_topics(topics, 3)
        self.assertEqual(([topic["title"] for topic in kept], dropped), (["Pflichtethik", "Utilitarismus", "Tugendethik"], 2))
        self.assertEqual([topic["position"] for topic in kept], [1, 2, 3])
        self.assertEqual(kept[1]["prerequisites"], [topic_key("Pflichtethik")], "no link to a dropped topic")
        self.assertEqual(topics[2]["prerequisites"], [topic_key("Freiheit"), topic_key("Pflichtethik")], "input unchanged")
        self.assertEqual(cap_topics(topics, 10), (topics, 0))

    def test_overlap_suggestions_explain_why_two_topics_may_be_one(self):
        topics = [
            stub("Ethik", [1], terms=("Moral",)),
            stub("Ethik nach Kant", [2]),
            stub("Utilitarismus nach Bentham", [3], terms=("Nutzen", "Glück", "Leid", "Kalkül")),
            stub("Utilitarismus nach Mill", [4], terms=("Nutzen", "Glück", "Leid")),
            stub("Pflichtethik", [5, 6], terms=("Pflicht",)),
            stub("Kategorischer Imperativ", [6], terms=("Pflicht",)),
            stub("Freiheit", [7], terms=("Moral",)),
            stub("Gerechtigkeit", [8]),
        ]
        suggestions = overlap_suggestions(topics)
        found = {(entry["a"], entry["b"]): entry for entry in suggestions}
        key = topic_key
        self.assertEqual(set(found), {(key("Ethik"), key("Ethik nach Kant")),
                                      (key("Utilitarismus nach Bentham"), key("Utilitarismus nach Mill")),
                                      (key("Pflichtethik"), key("Kategorischer Imperativ"))})
        # "Ethik" is contained in "Ethik nach Kant" (PF3 D6: contained titles are suggested).
        self.assertEqual(found[(key("Ethik"), key("Ethik nach Kant"))]["reasons"], ["similar_title", "contained_title"])
        utilitarian = found[(key("Utilitarismus nach Bentham"), key("Utilitarismus nach Mill"))]
        self.assertEqual((utilitarian["reasons"], utilitarian["shared_terms"]), (["shared_terms"], ["Nutzen", "Glück", "Leid"]))
        duty = found[(key("Pflichtethik"), key("Kategorischer Imperativ"))]
        self.assertEqual((duty["reasons"], duty["shared_pages"], duty["shared_terms"]), (["shared_pages"], [6], ["Pflicht"]))
        scores = [entry["score"] for entry in suggestions]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(len(overlap_suggestions(topics, limit=1)), 1)

    def test_uncovered_pages_are_listed_in_page_order(self):
        topics = [stub("Ethik", [1]), stub("Freiheit", [3, 1])]
        self.assertEqual(uncovered_pages([1, 2, 3, 4, 2], topics), [2, 4])
        self.assertEqual(uncovered_pages([1, 3], topics), [])


# --------------------------------------------------------------------------- review fixes

class OddModelOutputTests(unittest.TestCase):
    """Model output in odd shapes is repaired or dropped field by field, never fatal."""

    ODD_VALUES = [None, 0, -1, 2.5, float("nan"), float("inf"), True, "", "  ", "x", "201", [], [None], [[1]],
                  {}, {"a": None}, {"a": [1]}, [{"term": None}], [{"kind": None, "name": None}], [{"detail": ["x"]}],
                  [201, "202", None, 2.5, {"page_id": 201}], {"Pflicht": {"x": 1}}, [{"prompt": "P", "answer": "A"}]]
    FIELDS = ("title", "source_page_ids", "summary", "simple_summary", "kind", "main_topic", "terms", "fachbegriffe",
              "vocabulary", "definitions", "items", "examples", "people", "formulas", "exam_questions",
              "related_topics", "prerequisites", "learning_goals", "important_facts", "relationships", "recall_cards",
              "difficulty", "importance", "estimated_minutes")

    def test_no_field_value_crashes_normalising_or_merging(self):
        base = {"title": "Kants Pflichtethik", "source_page_ids": [201, 202]}
        results = []
        for field in self.FIELDS:
            for value in self.ODD_VALUES:
                with self.subTest(field=field, value=value):
                    topic = normalize_topic({**base, field: value}, set(ETHICS_PAGES), page_text=ETHICS_PAGES)
                    if topic is not None:
                        self.assertTrue(topic["key"] and topic["source_page_ids"])
                        self.assertTrue(1 <= topic["difficulty"] <= 3 and 1 <= topic["importance"] <= 3)
                        self.assertTrue(5 <= topic["estimated_minutes"] <= 45)
                        results.append(topic)
        merged = merge_topics(results, page_order={201: 1, 202: 2, 203: 3})
        cap_topics(merged, 1)
        overlap_suggestions(merged)
        resolve_related(merged)

    def test_nan_and_infinity_in_the_json_are_defaults(self):
        raw = json.loads('{"title": "T", "source_page_ids": [201], "estimated_minutes": Infinity, '
                         '"difficulty": NaN, "importance": -Infinity}')
        topic = present(normalize_topic(raw, {201}))
        self.assertEqual((topic["estimated_minutes"], topic["difficulty"], topic["importance"]), (15, 2, 2))

    def test_page_references_and_titles_in_variant_shapes(self):
        topic = present(normalize_topic({"title": "T", "source_page_ids": [
            201, "202", None, 2.5, {"page_id": 203}, "Seite 201", "201-203"]}, set(ETHICS_PAGES)))
        self.assertEqual(topic["source_page_ids"], [201, 202, 203])
        topic = present(normalize_topic({"titel": "Pflichtethik", "pages": "201"}, set(ETHICS_PAGES)))
        self.assertEqual((topic["title"], topic["source_page_ids"]), ("Pflichtethik", [201]))

    def test_single_strings_and_dicts_in_lists_are_read(self):
        topic = present(normalize_topic({"title": "T", "source_page_ids": [201], "learning_goals": "Pflicht erklären",
                                         "prerequisites": [{"title": "Kant"}, "kant", {"x": 1}]}, {201}))
        self.assertEqual(topic["learning_goals"], ["Pflicht erklären"])
        self.assertEqual(topic["prerequisites"], ["Kant"])

    def test_a_one_entry_dict_keyed_by_a_field_name_is_a_record_not_a_term(self):
        topic = ethics_topic(definitions=[{"definition": "Pflicht ist die Notwendigkeit einer Handlung."}],
                             items=[{"detail": "Das Lügenverbot als Beispiel"}])
        self.assertEqual([(term["term"], term["definition"]) for term in topic["terms"]],
                         [("Pflicht", "Pflicht ist die Notwendigkeit einer Handlung.")])
        self.assertEqual([item["name"] for item in topic["items"]], ["Das Lügenverbot als Beispiel"])


class GroundingReviewTests(unittest.TestCase):
    """Nothing is claimed to be from the notes unless the notes really say it."""

    def test_placeholder_quotes_never_verify_a_recall_card(self):
        cards = [{"prompt": "Was?", "answer": "x", "source_text": "..."},
                 {"prompt": "Wer?", "answer": "y", "source_text": "„“"},
                 {"prompt": "Wie?", "answer": "z", "source_text": " … "},
                 {"prompt": "Ok?", "answer": "w", "source_text": "Pflicht ist die Notwendigkeit"}]
        topic = ethics_topic(recall_cards=cards)
        self.assertEqual([card["prompt"] for card in topic["recall_cards"]], ["Ok?"])

    def test_a_quote_of_just_the_term_does_not_back_a_definition(self):
        topic = ethics_topic(terms=[
            {"term": "Pflicht", "definition": "Was man eben tun muss", "quote": "Pflicht"},
            {"term": "Kategorischer Imperativ", "definition": "Ein Prinzip", "quote": "Der kategorische Imperativ lautet"},
        ])
        terms = {term["term"]: term for term in topic["terms"]}
        for name in ("Pflicht", "Kategorischer Imperativ"):
            with self.subTest(term=name):
                self.assertTrue(terms[name]["in_notes"], "the term itself is on the page")
                self.assertFalse(terms[name]["definition_from_notes"])
                self.assertEqual(terms[name]["quote"], "")
        self.assertFalse(any(card["kind"] == "definition" for card in topic["recall_cards"]))

    def test_a_verified_quote_without_a_definition_becomes_the_definition(self):
        topic = ethics_topic(terms=[{"term": "Maxime", "quote": "Maximen sind subjektive Grundsätze des Handelns"}])
        term = topic["terms"][0]
        self.assertEqual((term["definition"], term["definition_from_notes"], term["page_id"]),
                         ("Maximen sind subjektive Grundsätze des Handelns", True, 202))
        self.assertIn("Maxime: Maximen sind subjektive Grundsätze des Handelns", topic["definitions"])
        self.assertEqual([(card["kind"], card["answer"]) for card in topic["recall_cards"]],
                         [("definition", "Maximen sind subjektive Grundsätze des Handelns")])

    def test_vocabulary_phrases_are_not_read_as_definitions(self):
        page = {1: "What is your name? I take the train from the station. A noun is a word for a thing."}
        raw = {"title": "Introductions", "source_page_ids": [1], "definitions": ["A noun is a word for a thing"],
               "vocabulary": ["What is your name?", "the station – der Bahnhof"]}
        topic = present(normalize_topic(raw, {1}, page_text=page))
        self.assertEqual([(term["term"], term["definition"]) for term in topic["terms"]], [
            ("A noun", "A noun is a word for a thing"), ("What is your name?", ""), ("the station", "der Bahnhof")])

    def test_terms_in_other_scripts_are_grounded_too(self):
        page = {1: "Глагол идти – это движение пешком в одном направлении. Λόγος bedeutet Vernunft. 这是一个范畴的例子。"}
        raw = {"title": "Глаголы движения", "source_page_ids": [1], "terms": [
            {"term": "идти", "definition": "движение пешком", "quote": "идти – это движение пешком в одном направлении"},
            {"term": "летать", "definition": "движение по воздуху"},
            {"term": "Λόγος", "definition": "Vernunft"}, {"term": "范畴", "definition": "category"},
        ]}
        terms = {term["term"]: term for term in present(normalize_topic(raw, {1}, page_text=page))["terms"]}
        self.assertEqual(list(terms), ["идти", "Λόγος", "范畴"], "a term that is not on the page is dropped")
        self.assertTrue(terms["идти"]["definition_from_notes"])

    def test_a_latex_formula_is_found_on_a_page_that_writes_it_plainly(self):
        raw = {"title": "Bewegung", "source_page_ids": [1],
               "items": [{"kind": "formula", "name": r"v = \frac{s}{t}", "detail": "Geschwindigkeit"},
                         {"kind": "formula", "name": r"E = \frac{1}{2} m v^2"}]}
        topic = present(normalize_topic(raw, {1}, page_text={1: "Die Geschwindigkeit ist v = s/t."}))
        self.assertEqual([item["name"] for item in topic["items"]], [r"v = \frac{s}{t}"], "the second is not there")

    def test_examples_learnova_added_stay_out_of_the_unmarked_examples_column(self):
        topic = ethics_topic(items=[{"kind": "Beispiel", "name": "Trolley-Problem", "detail": "Weiche umstellen"},
                                    {"kind": "Beispiel", "name": "Lügenverbot", "detail": "Lügen kann kein Gesetz werden"}])
        self.assertEqual([(item["name"], item["in_notes"]) for item in topic["items"]],
                         [("Trolley-Problem", False), ("Lügenverbot", True)])
        self.assertEqual(topic["examples"], ["Lügenverbot: Lügen kann kein Gesetz werden"])
        unchecked = present(normalize_topic({"title": "T", "source_page_ids": [201],
                                             "examples": ["Trolley-Problem"]}, {201}))
        self.assertEqual(unchecked["examples"], ["Trolley-Problem"], "without page text nothing can be checked")

    def test_kind_labels_in_list_entries_are_kinds_not_names(self):
        page = {1: "Beispiel: Lügen. Beispiel: Stehlen. Versuch: Kerze im Glas erlischt."}
        raw = {"title": "Pflicht", "source_page_ids": [1],
               "examples": ["Beispiel: Lügen", "Beispiel: Stehlen – nimmt fremdes Eigentum", "Versuch: Kerze im Glas"]}
        topic = present(normalize_topic(raw, {1}, page_text=page))
        self.assertEqual([(item["kind"], item["name"], item["detail"]) for item in topic["items"]], [
            ("example", "Lügen", ""), ("example", "Stehlen", "nimmt fremdes Eigentum"),
            ("experiment", "Kerze im Glas", "")])


class TopicIdentityReviewTests(unittest.TestCase):
    def test_titles_without_latin_content_words_keep_their_own_identity(self):
        titles = ["Квадратные уравнения", "Линейные функции", "Λόγος", "Einführung", "Grundlagen", "1. Глаголы"]
        keys = [topic_key(title) for title in titles]
        self.assertEqual(len(set(keys)), len(titles), keys)
        self.assertNotIn("topic", keys)
        self.assertEqual(topic_key("1. Глаголы"), topic_key("Глаголы"), "numbering is stripped in any script")
        self.assertEqual(topic_key("Einführung", "Ethik"), topic_key("Ethik"), "a stop-word title joins its main topic")
        self.assertEqual(topic_key("3. Grundlagen"), "3", "the number is all that tells it apart")
        self.assertEqual(topic_key(""), "topic")
        topics = [stub(title, [page]) for page, title in enumerate(titles, start=1)]
        merged = merge_topics(topics, page_order={page: page for page in range(1, len(titles) + 1)})
        self.assertEqual(len(merged), len(titles), "they used to share the key 'topic' and merge into one")


class ReconcileReviewTests(unittest.TestCase):
    def topics(self, *titles):
        return [present(normalize_topic({"title": title, "source_page_ids": [101]}, {101})) for title in titles]

    def test_a_digit_only_difference_never_takes_a_stored_topic_whatever_the_order(self):
        # The first key version ignored digits: "Text 1" was stored as "text".
        for stored_topic in (StoredTopic(1, "text", "Text 1", has_progress=True),
                             StoredTopic(1, "text", "Text 1", user_edited=True, has_progress=True)):   # typed in
            for order in (("Text 2", "Text 1"), ("Text 1", "Text 2")):
                with self.subTest(stored=stored_topic, order=order):
                    result = reconcile([stored_topic], self.topics(*order))
                    self.assertEqual([(item_id, topic["title"]) for item_id, topic in result.update], [(1, "Text 1")])
                    self.assertEqual([topic["title"] for topic in result.create], ["Text 2"])
        for titles in (("Text 2",), ("Text", "Text 2")):
            with self.subTest(titles=titles):
                alone = reconcile([StoredTopic(1, "text", "Text 1", has_progress=True)], self.topics(*titles))
                self.assertEqual((alone.update, alone.keep), ([], [1]), "progress on Text 1 never moves to another text")

    def test_a_renamed_topic_is_still_found_by_its_old_key(self):
        stored = [StoredTopic(1, "bentham-nach-utilitarismu", "Mein Bentham", user_edited=True, has_progress=True)]
        result = reconcile(stored, self.topics("Utilitarismus nach Mill", "Utilitarismus nach Bentham"))
        self.assertEqual([(item_id, topic["title"]) for item_id, topic in result.update],
                         [(1, "Utilitarismus nach Bentham")])
        self.assertEqual([topic["title"] for topic in result.create], ["Utilitarismus nach Mill"])


class BatchPropertyTests(unittest.TestCase):
    WORDS = ("Pflicht", "Maxime", "Gesetz", "Kant", "Achtung", "Vernunft", "Handlung", "Wille", "Freiheit")

    def random_pages(self, rng):
        pages = []
        for order in range(1, rng.randint(1, 12) + 1):
            if rng.random() < 0.2:
                continue                        # an unreadable or excluded page leaves a gap
            paragraphs = [" ".join(rng.choice(self.WORDS) for _ in range(rng.randint(1, 500))) + rng.choice((".", "", "!"))
                          for _ in range(rng.randint(0, 8))]
            pages.append({"page_id": 1000 + order, "page_order": order,
                          "text": rng.choice(("\n\n", "\n", " ")).join(paragraphs)})
        return pages

    def test_batches_lose_nothing_respect_both_limits_and_are_deterministic(self):
        rng = random.Random(20261031)
        for trial in range(40):
            pages = self.random_pages(rng)
            for max_chars in (300, MAX_BATCH_CHARS):
                with self.subTest(trial=trial, max_chars=max_chars):
                    batches = page_batches(pages, max_chars=max_chars)
                    self.assertEqual(batches, page_batches(pages, max_chars=max_chars))
                    entries = [entry for batch in batches for entry in batch]
                    for page in pages:
                        own = [entry for entry in entries if entry["page_id"] == page["page_id"]]
                        self.assertEqual("".join(page["text"].split()), "".join("".join(e["text"] for e in own).split()))
                        if len(own) > 1:
                            self.assertEqual([(e["part"], e["parts"]) for e in own],
                                             [(number, len(own)) for number in range(1, len(own) + 1)])
                    self.assertEqual([e["page_order"] for e in entries], sorted(e["page_order"] for e in entries))
                    for batch in batches:
                        self.assertLessEqual(len(batch), MAX_BATCH_PAGES)
                        self.assertLessEqual(sum(len(e["text"]) for e in batch), max_chars)

    def test_bisecting_a_failed_batch_ends_and_keeps_every_character(self):
        text = "\n\n".join("Kant prüft Maximen am Gesetz. " * 12 for _ in range(8))
        batch = [{"page_id": 1, "text": text}, {"page_id": 2, "text": "Kurz."}]

        def leaves(entries, depth=0):
            self.assertLess(depth, 40, "bisection must end")
            first, second = split_batch(entries)
            if not second:
                return list(first)
            return leaves(first, depth + 1) + leaves(second, depth + 1)

        pieces = leaves(batch)
        self.assertEqual("".join((text + "Kurz.").split()), "".join("".join(piece["text"] for piece in pieces).split()))
        self.assertTrue(all(len(piece["text"]) < project_topics.MIN_SPLIT_CHARS for piece in pieces))


if __name__ == "__main__":
    unittest.main()

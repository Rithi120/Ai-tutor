"""Project Mode topic extraction helpers (learnova/projects/topics.py)."""

import unittest

from tests.typing_helpers import present

from learnova.projects.topics import (
    MAX_BATCH_CHARS, StoredTopic, merge_topics, normalize_topic, page_batches, reconcile, topic_key,
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


if __name__ == "__main__":
    unittest.main()

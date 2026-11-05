"""Contracts between the Project Mode modules built separately (lessons, topics, terms,
document analysis, coverage, OCR).

Each module has its own tests; these check that the output of one is exactly what the
next one consumes, so a change in one module that breaks its neighbour fails here and
not only in the app: model topics -> topic map -> merged map -> flashcards ->
flashcard service, topic map -> lesson plan, topic map -> material report -> coverage.
Pure: no app, no database, no AI.
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from learnova.flashcards.service import CARD_TYPES, normalize_card
from learnova.lessons import lesson_plan
from learnova.lessons.strategies import REGISTRY, base, strategy_for, strategy_key_for
from learnova.projects import coverage, document_analysis, terms, topics

PAGES = {
    101: "Kants Pflichtethik. Der kategorische Imperativ ist ein unbedingt gültiges Gebot der praktischen "
         "Vernunft. Eine Maxime ist ein subjektiver Grundsatz des Wollens. Handle nur nach derjenigen Maxime, "
         "durch die du zugleich wollen kannst, dass sie ein allgemeines Gesetz werde.",
    102: "Pflicht ist die Notwendigkeit einer Handlung aus Achtung fürs Gesetz. Der Krämer ist ehrlich, aber nur "
         "aus Eigennutz. Immanuel Kant (1724-1804) war ein Philosoph der Aufklärung.",
    103: "Utilitarismus nach Bentham: Das größte Glück der größten Zahl. Der hedonistische Kalkül misst Lust und "
         "Leid. Jeremy Bentham begründete den klassischen Utilitarismus.",
    104: "Utilitarismus nach Mill: qualitative Unterschiede der Freuden. Lieber ein unzufriedener Sokrates als ein "
         "zufriedener Narr. John Stuart Mill.",
}
PAGE_ORDER = {101: 1, 102: 2, 103: 3, 104: 4}

# Two batches as a model returns them: v4 shape, German field names, a string term,
# items grouped by kind, page ids as strings, typographic quotes, one invented term.
BATCH_ONE = [{
    "title": "1. Kants Pflichtethik", "kind": "main", "summary": "Kant begründet Moral aus Pflicht.",
    "source_page_ids": [101, 102], "importance": "high", "difficulty": "hard",
    "terms": [
        {"term": "Kategorischer Imperativ", "definition": "Ein unbedingt gültiges Gebot der praktischen Vernunft.",
         "simple_definition": "Eine Regel, die immer gilt.", "page_id": 101,
         "quote": "„Der kategorische Imperativ ist ein unbedingt gültiges Gebot der praktischen Vernunft.“"},
        {"term": "Maxime", "definition": "Ein subjektiver Grundsatz des Wollens.", "page_id": 101,
         "quote": "Eine Maxime ist ein subjektiver Grundsatz des Wollens."},
        {"Begriff": "Pflicht", "Bedeutung": "Notwendigkeit einer Handlung aus Achtung fürs Gesetz", "Seite": "102"},
        {"term": "Autonomie", "definition": "Selbstgesetzgebung des Willens.", "page_id": 102},   # not in the notes
    ],
    "items": [
        {"kind": "Gedankenexperiment", "name": "Der Krämer", "detail": "Ehrlich nur aus Eigennutz.", "page_id": 102},
        {"kind": "Philosoph", "name": "Immanuel Kant", "detail": "Philosoph der Aufklärung (1724-1804).",
         "page_id": 102},
        {"kind": "Beispiel", "name": "Notlüge beim Arzt", "detail": "Eine Lüge, um zu helfen."},   # added example
    ],
    "exam_questions": ["Erläutere den kategorischen Imperativ."],
    "related_topics": ["Utilitarismus nach Bentham"],
}]
BATCH_TWO = [
    {"title": "Kants kategorischer Imperativ", "source_page_ids": [101], "kind": "subtopic",
     "terms": [{"term": "Der kategorische Imperativ", "definition": "Unbedingtes Gebot.", "page_id": 101}]},
    {"title": "Kants Pflichtethik", "source_page_ids": ["101"],          # the same topic seen again
     "terms": ["Maxime: subjektiver Grundsatz"]},
    {"title": "Utilitarismus nach Bentham", "source_page_ids": ["103"],
     "terms": ["Hedonistischer Kalkül: misst Lust und Leid"],
     "items": {"people": ["Jeremy Bentham"],
               "theories": [{"name": "Klassischer Utilitarismus", "detail": "Das größte Glück der größten Zahl."}]}},
    {"title": "Utilitarismus nach Mill", "source_page_ids": [104],
     "items": [{"kind": "argument", "name": "Sokrates und der Narr",
                "detail": "Lieber ein unzufriedener Sokrates als ein zufriedener Narr."},
               {"kind": "person", "name": "John Stuart Mill", "detail": "Qualitative Unterschiede der Freuden."}]},
]


def topic_map(batches=((BATCH_ONE, {101, 102}), (BATCH_TWO, {101, 103, 104}))):
    """What the extraction does: normalise every batch's topics, merge, cap."""

    normalized = []
    for batch, page_ids in batches:
        for raw in batch:
            topic = topics.normalize_topic(raw, page_ids, page_text={page: PAGES[page] for page in page_ids})
            if topic is not None:
                normalized.append(topic)
    merged = topics.merge_topics(normalized, page_order=PAGE_ORDER)
    kept, dropped = topics.cap_topics(merged)
    assert dropped == 0
    return kept


def label(page_id):
    return f"Ethik.pdf, S. {PAGE_ORDER[page_id]}"


class TopicMapToFlashcardsTests(unittest.TestCase):
    def setUp(self):
        self.topics = topic_map()
        self.cards = terms.build_cards(self.topics, strategy_key=strategy_key_for("Ethik"), source_label=label,
                                       card_kinds=strategy_for("Ethik").card_kinds)

    def test_merged_map_is_what_build_cards_reads(self):
        self.assertEqual([topic["key"] for topic in self.topics],
                         ["kant-pflichtethik", "imperativ-kant-kategorisch", "bentham-utilitarismu",
                          "mill-utilitarismu"])
        self.assertTrue(self.cards)
        for card in self.cards:
            with self.subTest(card=card["origin_key"]):
                # The flashcard service keeps every field exactly as built.
                normalized = normalize_card(card)
                self.assertEqual(normalized, {field: card[field] for field in normalized})
                self.assertIn(card["type"], CARD_TYPES)
                self.assertLessEqual(len(card["origin_key"]), 160)
                self.assertLessEqual(len(card["topic_key"]), 120)
                self.assertIn(card["topic_key"], {topic["key"] for topic in self.topics})
                owner = next(topic for topic in self.topics if topic["key"] == card["topic_key"])
                self.assertTrue(set(card["source_page_ids"]) <= set(owner["source_page_ids"]) | set(PAGES))
                self.assertTrue(card["source_reference"].startswith("Ethik.pdf, S. "))
        # Origin keys are unique, and so are fronts under the set editor's own rule
        # (app.update_flashcard_set drops a card whose stripped, casefolded front repeats).
        self.assertEqual(len({card["origin_key"] for card in self.cards}), len(self.cards))
        self.assertEqual(len({card["front"].strip().casefold() for card in self.cards}), len(self.cards))

    def test_every_term_in_the_notes_gets_its_definition_card_and_nothing_invented(self):
        in_notes = {term["key"] for topic in self.topics for term in topic["terms"] if term["in_notes"]}
        definitions = {card["origin_key"].split(":", 2)[2] for card in self.cards
                       if card["origin_key"].split(":")[1] == "term"}
        self.assertEqual(definitions, in_notes)
        self.assertNotIn(terms.term_key("Autonomie"), definitions)
        # The same term in two topics is one card; the first topic in note order has it.
        imperative = [card for card in self.cards if card["origin_key"].endswith(":term:imperativ-kategorisch")]
        self.assertEqual(len(imperative), 1)
        self.assertEqual(imperative[0]["topic_key"], "kant-pflichtethik")
        # A Learnova-added example is never a card.
        self.assertFalse([card for card in self.cards if "Notlüge" in card["front"]])

    def test_card_keys_join_the_topic_maps_term_keys(self):
        # The workspace joins cards to the map's terms and LESSON-BE counts terms taught by
        # key, so the map's "key", the card key and terms_mentioned must be one key.
        for topic in self.topics:
            for term in topic["terms"]:
                self.assertEqual(term["key"], terms.term_key(term["term"]), term["term"])
        lesson_text = ("Heute lernst du den kategorischen Imperativ, die Maxime und die Pflicht kennen. "
                       "Bentham rechnet mit dem hedonistischen Kalkül.")
        all_terms = [term for topic in self.topics for term in topic["terms"]]
        card_keys = {card["origin_key"].split(":", 2)[2] for card in self.cards}
        mentioned = terms.terms_mentioned(all_terms, lesson_text)
        self.assertEqual(set(mentioned), {terms.term_key("Kategorischer Imperativ"), terms.term_key("Maxime"),
                                          terms.term_key("Pflicht"), terms.term_key("Hedonistischer Kalkül")})
        self.assertTrue(set(mentioned) <= card_keys)
        items = [item for topic in self.topics for item in topic["items"] if item["in_notes"]]
        self.assertIn(terms.item_key("person", "Immanuel Kant"),
                      terms.terms_mentioned(items, "Immanuel Kant schrieb über Pflicht."))

    def test_term_keys_are_identical_in_both_modules(self):
        # topics.term_key is a copy of terms.term_key (terms imports topics, so it cannot
        # import back); this list holds the cases the two once disagreed on.
        for name in ("Der kategorische Imperativ", "Ursache/Wirkung", "Λόγος", "Квадратное уравнение", "范畴",
                     "Grundlagen", "pH-Wert", "Text 2", "1. Weltkrieg", "Maxime (Ethik)", "Kalkül", "if-clause",
                     "", "  ", "Ratio: Vernunft", "Leib-Seele-Problem", "Erster Weltkrieg"):
            with self.subTest(name=name):
                self.assertEqual(topics.term_key(name), terms.term_key(name))

    def test_resync_of_a_new_analysis_adds_nothing(self):
        existing = {card["origin_key"]: card["source_page_ids"] for card in self.cards}
        again = terms.build_cards(topic_map(), strategy_key="ethics", source_label=label)
        added, unions = terms.plan_sync(existing, json.loads(json.dumps(list(existing))), again)
        self.assertEqual((added, unions), ([], []))


# One item of every kind, named and described on one page, for the per-kind checks.
KIND_SAMPLES = {
    "term": ("Kategorischer Imperativ", "ein unbedingt gültiges Gebot der Vernunft"),
    "example": ("Notlüge beim Arzt", "eine Lüge, um jemanden zu schützen"),
    "experiment": ("Kerze im Glas", "die Flamme erlischt, weil der Sauerstoff verbraucht ist"),
    "theory": ("Evolutionstheorie", "Arten entstehen durch natürliche Auslese"),
    "person": ("Immanuel Kant", "Philosoph der Aufklärung"),
    "argument": ("Universalisierbarkeit", "eine Maxime muss für alle gelten können"),
    "formula": ("v = s / t", "Geschwindigkeit ist Weg durch Zeit"),
    "process": ("Photosynthese", "Pflanzen bilden aus Licht, Wasser und Kohlendioxid Zucker"),
    "diagram": ("Klimadiagramm Berlin", "Temperatur und Niederschlag über das Jahr"),
    "event": ("Sturm auf die Bastille", "Beginn der Französischen Revolution in Paris"),
    "date": ("1789", "Beginn der Französischen Revolution"),
    "rule": ("Relativsatz mit who", "who steht für Personen im Relativsatz"),
    "thought_experiment": ("Höhlengleichnis", "Menschen sehen nur Schatten an der Wand"),
    "work": ("Die Verwandlung", "Erzählung von Franz Kafka über Gregor Samsa"),
    "method": ("Satzgliedbestimmung", "Satzglieder mit der Umstellprobe finden"),
    "source": ("Ballhausschwur", "Quelle über den Schwur der Abgeordneten"),
    "place": ("Ruhrgebiet", "Industrieregion im Strukturwandel"),
    "institution": ("Bundesrat", "Vertretung der Länder bei der Gesetzgebung"),
    "position": ("Kompatibilismus", "Freiheit und Determinismus sind vereinbar"),
    "cause_effect": ("Industrialisierung", "Dampfmaschine führte zu Fabriken und Verstädterung"),
    "structure": ("if-clause type 1", "if plus simple present, then will plus infinitive"),
}
SAMPLE_PAGE = {1: " ".join(f"{name}: {detail}." for name, detail in KIND_SAMPLES.values())}


def sample_topic(kind):
    name, detail = KIND_SAMPLES[kind]
    raw = {"title": "Thema", "source_page_ids": [1]}
    if kind == "term":
        raw["terms"] = [{"term": name, "definition": detail, "page_id": 1, "quote": f"{name}: {detail}"}]
    else:
        raw["items"] = [{"kind": kind, "name": name, "detail": detail, "page_id": 1}]
    topic = topics.normalize_topic(raw, {1}, page_text=SAMPLE_PAGE)
    assert topic is not None
    return topic


class StrategyToTopicMapTests(unittest.TestCase):
    def test_shared_kinds_are_the_same_tuples(self):
        self.assertEqual(KIND_SAMPLES.keys(), set(base.ITEM_KINDS))
        for name in ("ITEM_KINDS", "PART_KINDS", "SERVER_PART_KINDS", "KNOWLEDGE_STATES"):
            self.assertEqual(getattr(topics, name), getattr(base, name), name)
        self.assertTrue(set(base.ITEM_KINDS) | {"term_example", "term_choice"} <= set(terms.CARD_PROMPTS))

    def test_every_extraction_focus_kind_survives_the_topic_map(self):
        for key, strategy in REGISTRY.items():
            for kind in strategy.extraction_focus:
                with self.subTest(strategy=key, kind=kind):
                    topic = sample_topic(kind)
                    if kind == "term":
                        self.assertEqual([(t["term"], t["in_notes"]) for t in topic["terms"]],
                                         [(KIND_SAMPLES[kind][0], True)])
                    else:
                        self.assertEqual([(item["kind"], item["in_notes"]) for item in topic["items"]],
                                         [(kind, True)])

    def test_every_card_kind_of_a_strategy_becomes_a_card(self):
        for key, strategy in REGISTRY.items():
            for kind in strategy.card_kinds:
                with self.subTest(strategy=key, kind=kind):
                    merged = topics.merge_topics([sample_topic(kind)], page_order={1: 1})
                    by_key = terms.build_cards(merged, strategy_key=key)
                    by_strategy = terms.build_cards(merged, strategy_key=key, card_kinds=strategy.card_kinds)
                    self.assertEqual(by_key, by_strategy)
                    self.assertIn(kind, {card["origin_key"].split(":")[1] for card in by_key})
                    for card in by_key:
                        normalized = normalize_card(card)
                        self.assertEqual(normalized, {field: card[field] for field in normalized})

    def test_topic_map_items_drive_the_conditional_lesson_steps(self):
        # LESSON-BE passes topic["items"] straight to plan_steps.
        sciences = strategy_for("Biologie")
        with_experiment = lesson_plan.plan_steps(sciences, mode="learn", state="new",
                                                 item_kinds=sample_topic("experiment")["items"])
        without = lesson_plan.plan_steps(sciences, mode="learn", state="new",
                                         item_kinds=sample_topic("theory")["items"])
        self.assertIn("experiment", [step.kind for step in with_experiment])
        self.assertNotIn("experiment", [step.kind for step in without])
        ethics = strategy_for("Ethik")
        steps = lesson_plan.plan_steps(ethics, mode="learn", state="new", item_kinds=topic_map()[0]["items"])
        self.assertIn("thought_experiment", [step.kind for step in steps])


class TopicMapToMaterialReportTests(unittest.TestCase):
    HANDOUT = ("Der kategorische Imperativ ist ein unbedingt gültiges Gebot der praktischen Vernunft. Handle nur "
               "nach derjenigen Maxime, durch die du zugleich wollen kannst, dass sie ein allgemeines Gesetz "
               "werde. Eine Maxime ist ein subjektiver Grundsatz des Wollens, eine Regel, die ich mir selbst gebe, "
               "wenn ich handle. Kant unterscheidet pflichtgemäßes Handeln von Handeln aus Pflicht, und nur "
               "Letzteres hat moralischen Wert.")
    # page id: (file id, page number in file, project order, text)
    PAGES = {
        1: (10, 1, 1, HANDOUT),
        2: (10, 2, 2, "Utilitarismus nach Bentham: Das größte Glück der größten Zahl. " * 6),
        3: (10, 3, 3, "Impressum und Inhaltsverzeichnis der Mappe. " * 8),
        4: (11, 1, 4, HANDOUT),                                         # the same handout photographed
        5: (11, 2, 5, "Utilitarismus nach Mill: lieber ein unzufriedener Sokrates als ein zufriedener Narr. " * 5),
        6: (12, 1, 6, ""),                                              # vision read nothing
    }
    RAW = [{"title": "Kategorischer Imperativ", "source_page_ids": [1]},
           {"title": "Utilitarismus nach Bentham", "source_page_ids": [2]},
           {"title": "Utilitarismus nach Mill", "source_page_ids": [5]}]

    def analyse(self):
        ordered = sorted(self.PAGES.items(), key=lambda entry: entry[1][2])
        duplicates = document_analysis.find_duplicate_pages([(page_id, value[3]) for page_id, value in ordered])
        content = [page_id for page_id, value in ordered if value[3].strip() and page_id not in duplicates]
        text = {page_id: self.PAGES[page_id][3] for page_id in content}
        normalized = [topics.normalize_topic(raw, set(content), page_text=text) for raw in self.RAW]
        kept, _ = topics.cap_topics(topics.merge_topics(
            [topic for topic in normalized if topic], page_order={p: v[2] for p, v in self.PAGES.items()}))
        uncovered = topics.uncovered_pages(content, kept)
        # analysis_json goes through JSON: int keys of the duplicate map come back as strings.
        stored = json.loads(json.dumps({"analysed_page_ids": content, "uncovered_page_ids": uncovered,
                                        "duplicates": duplicates}))
        return kept, uncovered, stored

    def report(self, kept, stored, no_topic):
        links = document_analysis.duplicate_links(stored["duplicates"])
        analysed = set(stored["analysed_page_ids"])
        cited = {page for topic in kept for page in topic["source_page_ids"]}
        files: dict[int, list] = {}
        for page_id, (file_id, number, order, text) in self.PAGES.items():
            files.setdefault(file_id, []).append(document_analysis.PageFacts(
                page_id=page_id, file_id=file_id, page_number=number, page_order=order,
                extraction_status="ready" if text.strip() else "failed", confidence_status="high",
                confidence=0.9, text_source="vision", text_chars=len(text.strip()),
                words=document_analysis.content_words(text),
                analysis="duplicate" if page_id in links else "analysed" if page_id in analysed else "not_analysed",
                covered=page_id in cited, duplicate_of=links.get(page_id)))
        facts = [document_analysis.FileFacts(file_id, f"Blatt-{file_id}.pdf", "pdf", tuple(pages))
                 for file_id, pages in files.items()]
        return document_analysis.material_report(facts, topics_total=len(kept), topics_included=len(kept),
                                                 no_topic_page_ids=no_topic)

    def test_uncovered_pages_are_the_reports_uncovered_pages(self):
        kept, uncovered, stored = self.analyse()
        self.assertEqual(uncovered, [3])
        self.assertEqual(stored["duplicates"], {"4": 1})
        report = self.report(kept, stored, no_topic=())
        self.assertEqual(report["pages_uncovered"], len(uncovered))
        self.assertEqual([(issue["filename"], issue["page_numbers"]) for issue in report["issues"]
                          if issue["code"] == "uncovered_pages"], [("Blatt-10.pdf", [3])])
        # The photographed copy borrows its original's topic; the empty page is reported.
        self.assertEqual(report["pages_duplicate"], 1)
        self.assertEqual(report["pages_unreadable"], 1)
        self.assertFalse(report["complete"])
        check = coverage.project_coverage(report, [])["checks"][1]
        self.assertEqual((check["code"], check["ok"], check["done"], check["total"]), ("topics_extracted", False, 4, 5))

    def test_pages_the_model_confirmed_without_topic_are_not_uncovered(self):
        kept, uncovered, stored = self.analyse()
        report = self.report(kept, stored, no_topic=json.loads(json.dumps(uncovered)))
        self.assertEqual((report["pages_uncovered"], report["pages_no_topic"]), (0, 1))
        self.assertNotIn("uncovered_pages", {issue["code"] for issue in report["issues"]})
        check = coverage.project_coverage(report, [])["checks"][1]
        self.assertTrue(check["ok"])

    def test_long_pages_split_into_batches_still_cite_the_real_page(self):
        long_page = {"page_id": 7, "page_order": 1, "text": ("Ein langer Absatz über Kant und die Pflicht. " * 400)}
        batches = topics.page_batches([long_page])
        self.assertGreater(len(batches), 1)
        self.assertEqual({entry["page_id"] for batch in batches for entry in batch}, {7})
        topic = topics.normalize_topic({"title": "Kant", "source_page_ids": [7]},
                                       {entry["page_id"] for entry in batches[1]}, page_text={7: long_page["text"]})
        assert topic is not None
        self.assertEqual(topics.uncovered_pages([7], [topic]), [])


class SharedVocabularyTests(unittest.TestCase):
    def test_coverage_reads_the_codes_and_states_the_report_writes(self):
        self.assertTrue(coverage._EXTRACTION_ISSUES <= set(document_analysis.ISSUE_CODES))
        self.assertTrue(coverage._PROCESSED_FILE_STATES <= set(document_analysis.FILE_STATES))

    def test_strategy_answer_formats_are_formats_the_lesson_contract_and_gate_know(self):
        from learnova.ai_services import contracts
        from learnova.quizzes import mastery_gate

        gate_types = {"multiple_choice", *mastery_gate.EARLY_TYPES.values(), *mastery_gate.LATE_TYPE_CYCLE}
        self.assertTrue(set(base.QUESTION_TYPES) <= contracts.QUESTION_TYPES)
        self.assertTrue(set(base.QUESTION_TYPES) <= gate_types)
        for key, strategy in REGISTRY.items():
            with self.subTest(strategy=key):
                self.assertEqual({lesson_plan.exercise_type(strategy, number) for number in range(1, 13)}
                                 - set(base.QUESTION_TYPES), set())


class ReadingBandTests(unittest.TestCase):
    def test_document_analysis_uses_the_ocr_review_band(self):
        # document_analysis repeats the literal so it stays importable without PIL/PyMuPDF.
        from learnova.ocr import service as ocr

        self.assertEqual(document_analysis.REVIEW_CONFIDENCE, ocr.CONFIDENCE_REVIEW)
        self.assertEqual(ocr.confidence_status(ocr.CONFIDENCE_REVIEW - 0.01), "unclear")
        self.assertEqual(document_analysis.page_state(document_analysis.PageFacts(
            page_id=1, file_id=1, page_number=1, page_order=1, extraction_status="ready",
            confidence_status=ocr.confidence_status(0.5), confidence=0.5, text_chars=40, words=8)), "read")

    def test_skipped_upload_duplicates_feed_the_report(self):
        from learnova.uploads import UploadResult

        result = UploadResult(pages_added=1, files_added=1, skipped_duplicates=["Blatt 1.pdf", "Blatt 1.pdf"])
        stored = json.loads(json.dumps({"skipped_duplicate_files": result.skipped_duplicates}))
        report = document_analysis.material_report([], skipped_duplicate_files=stored["skipped_duplicate_files"])
        self.assertEqual(report["skipped_duplicate_files"], ["Blatt 1.pdf"])
        self.assertEqual([issue["code"] for issue in report["issues"]], ["duplicate_file_skipped"])


class PurityTests(unittest.TestCase):
    def test_pure_project_modules_do_not_import_the_app(self):
        code = ("import sys, learnova.projects.topics, learnova.projects.terms, learnova.projects.coverage, "
                "learnova.projects.document_analysis, learnova.projects.planning; "
                "print(sorted(m for m in ('app', 'flask', 'sqlalchemy', 'learnova.extensions', "
                "'learnova.ai_services.service', 'PIL', 'fitz') if m in sys.modules))")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                                cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == "__main__":
    unittest.main()

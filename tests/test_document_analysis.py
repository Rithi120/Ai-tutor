"""Project Mode material understanding (learnova/projects/document_analysis.py).

The scenario behind this module: a student uploads six Ethik papers, one is read, five
fail or are never analysed, and the app still says "We found 3 topics". Every test here
checks that what happened to each document and each page is visible in the report.
"""

import dataclasses
import json
import unittest
from typing import Any

from learnova.projects.document_analysis import (
    ANALYSIS_STATES, FILE_STATES, ISSUE_CODES, PAGE_STATES, READING_STATES, TEXT_SOURCES, FileFacts,
    PageFacts, content_words, duplicate_links, file_status, find_duplicate_pages, find_overlaps,
    material_report, page_state,
)

UTILITARISMUS = (
    "Der Utilitarismus ist eine Form der teleologischen Ethik. Eine Handlung ist moralisch "
    "richtig, wenn sie das größte Glück der größten Zahl bewirkt. Jeremy Bentham begründete "
    "den quantitativen Utilitarismus und entwickelte das hedonistische Kalkül mit Kriterien "
    "wie Intensität, Dauer, Gewissheit, Nähe, Fruchtbarkeit und Reinheit einer Freude. "
    "John Stuart Mill unterschied dagegen höhere und niedere Freuden und vertrat einen "
    "qualitativen Utilitarismus: Es ist besser, ein unzufriedener Mensch zu sein als ein "
    "zufriedenes Schwein. Der Handlungsutilitarismus beurteilt jede einzelne Handlung nach "
    "ihren Folgen, der Regelutilitarismus dagegen Regeln, deren allgemeine Befolgung den "
    "größten Nutzen bringt. Kritiker wenden ein, dass die Rechte Einzelner verletzt werden "
    "können, wenn nur die Summe des Nutzens zählt, und dass sich Glück kaum messen lässt. "
    "Ein bekanntes Gedankenexperiment ist der Transplantationsfall: Ein Arzt könnte fünf "
    "Patienten retten, wenn er einen gesunden Besucher opfert. Die meisten Menschen halten "
    "das für falsch, obwohl die Nutzensumme steigt."
)
KANT = (
    "Immanuel Kant begründete eine deontologische Ethik. Nicht die Folgen einer Handlung "
    "entscheiden über ihren moralischen Wert, sondern der gute Wille und die Pflicht. Der "
    "kategorische Imperativ lautet: Handle nur nach derjenigen Maxime, durch die du zugleich "
    "wollen kannst, dass sie ein allgemeines Gesetz werde. Die Selbstzweckformel verlangt, "
    "die Menschheit niemals bloß als Mittel, sondern immer zugleich als Zweck zu behandeln. "
    "Kant unterscheidet pflichtgemäßes Handeln von Handeln aus Pflicht: Nur wer aus Pflicht "
    "handelt, handelt moralisch. Ein Kaufmann, der ehrlich ist, weil es sich lohnt, handelt "
    "zwar pflichtgemäß, aber nicht aus Pflicht."
)
REVISION_EXTRA = (
    "Wiederholung für die Klassenarbeit: Erkläre die Begriffe Nutzen, Folgen und Pflicht "
    "mit eigenen Worten. Vergleiche anschließend die Antwort eines Utilitaristen mit der "
    "Antwort Kants auf das Lügenbeispiel und begründe, welche Position dich mehr überzeugt. "
    "Notiere dir zu jeder Position mindestens ein Argument und ein Gegenargument."
)


def page(page_id, file_id, number, order, status="ready", **kwargs):
    kwargs.setdefault("confidence_status", "high")
    kwargs.setdefault("confidence", 0.95)
    kwargs.setdefault("text_source", "vision")
    kwargs.setdefault("analysis", "analysed")
    kwargs.setdefault("covered", True)
    if status == "ready":
        kwargs.setdefault("text_chars", 900)
        kwargs.setdefault("words", 140)
    return PageFacts(page_id=page_id, file_id=file_id, page_number=number, page_order=order,
                     extraction_status=status, **kwargs)


def document(file_id, filename, *pages, source_kind="upload"):
    return FileFacts(file_id=file_id, filename=filename, source_kind=source_kind, pages=tuple(pages))


def six_documents():
    """The reported case: six papers, each ending up in a different state."""
    return [
        # 1: a born-digital PDF, both pages from the text layer, analysed and covered.
        document(1, "ethik-papier-1.pdf",
                 page(11, 1, 1, 1, text_source="text_layer", confidence=1.0),
                 page(12, 1, 2, 2, text_source="text_layer", confidence=1.0)),
        # 2: a PDF whose second page vision could not read.
        document(2, "ethik-papier-2.pdf",
                 page(21, 2, 1, 3),
                 page(22, 2, 2, 4, status="failed", analysis="not_analysed", covered=False)),
        # 3: a handwritten Tafelbild photo: read by vision, but unclear.
        document(3, "tafelbild.jpg",
                 page(31, 3, 1, 5, confidence_status="unclear", confidence=0.35)),
        # 4: a scan where nothing could be read.
        document(4, "scan-kant.pdf",
                 page(41, 4, 1, 6, status="unreadable", analysis="not_analysed", covered=False),
                 page(42, 4, 2, 7, status="failed", analysis="not_analysed", covered=False)),
        # 5: a photo still waiting for recognition (the hourly AI budget was low).
        document(5, "foto-arbeitsblatt.jpg",
                 page(51, 5, 1, 8, status="waiting", analysis="not_analysed", covered=False)),
        # 6: read, but added after the topics were built: never analysed.
        document(6, "nachtrag.pdf",
                 page(61, 6, 1, 9, analysis="not_analysed", covered=False),
                 page(62, 6, 2, 10, analysis="not_analysed", covered=False)),
    ]


def find_issues(report, code, file_id=None) -> list[dict[str, Any]]:
    return [item for item in report["issues"] if item["code"] == code and item["file_id"] == file_id]


def issue(report, code, file_id=None) -> dict[str, Any]:
    matches = find_issues(report, code, file_id)
    if len(matches) != 1:
        raise AssertionError(f"expected one {code} issue for file {file_id}, got {matches}")
    return matches[0]


class SharedConstantsTests(unittest.TestCase):
    def test_states_and_codes_are_the_documented_ones(self):
        self.assertEqual(PAGE_STATES, ("pending", "waiting", "recognizing", "ready", "unreadable", "failed"))
        # "blank" (FIX1, P1-05): a blank PDF page stored without an AI call.
        self.assertEqual(TEXT_SOURCES, ("text_layer", "vision", "local_ocr", "manual", "blank", ""))
        self.assertEqual(ANALYSIS_STATES, ("analysed", "failed", "not_analysed", "duplicate", "no_content"))
        self.assertEqual(FILE_STATES, ("analysed", "partly_analysed", "needs_ocr_review", "unreadable",
                                       "processing", "not_analysed", "excluded"))
        self.assertIn("topics_excluded", ISSUE_CODES)


class AuditFixTests(unittest.TestCase):
    """FIX1: blank pages and explicit (v4) page lists."""

    def test_a_blank_page_is_read_with_no_content_never_unreadable_or_uncovered(self):
        blank = page(2, 1, 2, 2, text_source="blank", text_chars=0, words=0, analysis="not_analysed", covered=False)
        self.assertEqual(page_state(blank), "read")
        report = material_report([FileFacts(1, "blatt.pdf", "upload", (page(1, 1, 1, 1), blank))], topics_total=1,
                                 topics_included=1)
        self.assertTrue(report["complete"], report["issues"])
        file = report["files"][0]
        self.assertEqual((file["state"], file["blank_page_numbers"], file["pages_uncovered"]), ("analysed", [2], 0))
        self.assertTrue(file["pages"][1]["blank"])

    def test_explicit_page_lists_win_over_a_citation(self):
        cited = page(1, 1, 1, 1, analysis="not_analysed", covered=True)
        self.assertEqual(file_status(FileFacts(1, "a.pdf", "upload", (cited,)))["state"], "analysed",
                         "old projects: a citation means the page reached the extraction")
        listed = dataclasses.replace(cited, analysis_listed=True)
        self.assertEqual(file_status(FileFacts(1, "a.pdf", "upload", (listed,)))["state"], "not_analysed",
                         "v4: a typed or hand-added topic is no evidence of an analysis")


class PageStateTests(unittest.TestCase):
    def test_reading_states(self):
        self.assertEqual(page_state(page(1, 1, 1, 1)), "read")
        self.assertEqual(page_state(page(1, 1, 1, 1, excluded=True, status="failed")), "excluded")
        for status in ("pending", "waiting", "recognizing"):
            self.assertEqual(page_state(page(1, 1, 1, 1, status=status)), "processing")
        for status in ("unreadable", "failed"):
            self.assertEqual(page_state(page(1, 1, 1, 1, status=status)), "unreadable")
        self.assertEqual(page_state(page(2, 1, 2, 2, duplicate_of=1, analysis="duplicate")), "duplicate")
        for state in (page_state(page(1, 1, 1, 1, status=s)) for s in PAGE_STATES):
            self.assertIn(state, READING_STATES)

    def test_unknown_status_is_processing_not_read(self):
        self.assertEqual(page_state(page(1, 1, 1, 1, status="improved")), "processing")

    def test_ready_page_without_text_is_unreadable(self):
        """Nothing from it can reach a topic, so it must not be reported as read."""
        self.assertEqual(page_state(page(1, 1, 1, 1, text_chars=0, words=0)), "unreadable")

    def test_handwriting_review_rules(self):
        unclear = page(1, 1, 1, 1, confidence_status="unclear", confidence=0.7)
        low = page(1, 1, 1, 1, confidence_status="review", confidence=0.3)
        review_band = page(1, 1, 1, 1, confidence_status="review", confidence=0.6)
        offline = page(1, 1, 1, 1, text_source="local_ocr", confidence_status="review", confidence=0.9)
        typed = page(1, 1, 1, 1, text_source="manual", confidence_status="unclear", confidence=0.2)
        self.assertEqual(page_state(unclear), "needs_review")
        self.assertEqual(page_state(low), "needs_review")
        self.assertEqual(page_state(review_band), "read")      # 0.45-0.8 is good enough to learn from
        self.assertEqual(page_state(offline), "needs_review")
        self.assertEqual(page_state(typed), "read")            # the student typed it


class FileStatusTests(unittest.TestCase):
    def test_six_documents_get_six_statuses(self):
        report = material_report(six_documents())
        states = [item["state"] for item in report["files"]]
        self.assertEqual(states, ["analysed", "partly_analysed", "needs_ocr_review", "unreadable",
                                  "processing", "not_analysed"])
        self.assertEqual(len(set(states)), 6)
        self.assertEqual(report["documents_uploaded"], 6)
        self.assertEqual(report["documents_analysed"], 2)      # analysed + needs_ocr_review
        self.assertEqual(report["documents_by_state"]["unreadable"], 1)
        self.assertEqual(report["documents_by_state"]["excluded"], 0)
        self.assertFalse(report["complete"])

    def test_multiple_pdfs_report_page_numbers_inside_each_file(self):
        files = [
            document(1, "a.pdf", page(1, 1, 1, 1), page(2, 1, 2, 2, status="failed"), page(3, 1, 3, 3)),
            document(2, "b.pdf", page(4, 2, 1, 4), page(5, 2, 2, 5), page(6, 2, 3, 6, status="unreadable")),
        ]
        report = material_report(files)
        first, second = report["files"]
        self.assertEqual(first["unreadable_page_numbers"], [2])     # page 2 of a.pdf, not page_order
        self.assertEqual(second["unreadable_page_numbers"], [3])    # page 3 of b.pdf (page_order 6)
        self.assertEqual(issue(report, "unreadable_pages", 1)["page_numbers"], [2])
        self.assertEqual(issue(report, "unreadable_pages", 2)["filename"], "b.pdf")
        self.assertEqual([entry["page_id"] for entry in second["pages"]], [4, 5, 6])

    def test_handwritten_vision_page_needs_review(self):
        status = file_status(document(1, "heft.jpg", page(1, 1, 1, 1, confidence_status="unclear", confidence=0.4)))
        self.assertEqual(status["state"], "needs_ocr_review")
        self.assertEqual(status["review_page_numbers"], [1])
        self.assertEqual(status["pages_review"], 1)

    def test_scanned_page_read_by_local_ocr_needs_review(self):
        scan = document(1, "scan.pdf",
                        page(1, 1, 1, 1, text_source="local_ocr", confidence_status="review", confidence=0.8),
                        page(2, 1, 2, 2, text_source="local_ocr", confidence_status="review", confidence=0.8))
        status = file_status(scan)
        self.assertEqual(status["state"], "needs_ocr_review")
        self.assertEqual(status["review_page_numbers"], [1, 2])
        report = material_report([scan])
        # Review A F2: never complete while a page is flagged for an OCR check. Nothing is
        # missing (every page is read, analysed and in a topic), and that is said apart.
        self.assertFalse(report["complete"])
        self.assertTrue(report["nothing_missing"])
        self.assertEqual(issue(report, "needs_review", 1)["page_numbers"], [1, 2])

    def test_a_page_of_a_repaired_pdf_needs_review_until_it_is_checked(self):
        # Review A F1: a repaired PDF may have lost the end of a page; its text-layer reading
        # (confidence "review", 0.79) counted as read and the report as complete.
        repaired = document(1, "download.pdf",
                            page(1, 1, 1, 1, text_source="text_layer", confidence_status="review", confidence=0.79,
                                 pdf_repaired=True),
                            page(2, 1, 2, 2, text_source="text_layer", confidence_status="review", confidence=0.79,
                                 pdf_repaired=True))
        status = file_status(repaired)
        self.assertEqual(status["state"], "needs_ocr_review")
        self.assertEqual(status["review_page_numbers"], [1, 2])
        self.assertFalse(material_report([repaired])["complete"])
        checked = document(1, "download.pdf", *(dataclasses.replace(entry, text_source="manual")
                                                for entry in repaired.pages))
        self.assertEqual(file_status(checked)["state"], "analysed", "checked by the student: what the page says")
        self.assertTrue(material_report([checked])["complete"])
        plain = page(3, 2, 1, 3, text_source="text_layer", confidence_status="review", confidence=0.79)
        self.assertEqual(page_state(plain), "read", "a fallback reading of an intact PDF stays read")

    def test_multi_page_partially_unreadable(self):
        pdf = document(7, "skript.pdf", page(1, 7, 1, 1), page(2, 7, 2, 2),
                       page(3, 7, 3, 3, status="unreadable", analysis="not_analysed", covered=False),
                       page(4, 7, 4, 4))
        status = file_status(pdf)
        self.assertEqual(status["state"], "partly_analysed")
        self.assertEqual(status["pages_total"], 4)
        self.assertEqual(status["pages_read"], 3)
        self.assertEqual(status["pages_unreadable"], 1)
        self.assertEqual(status["unreadable_page_numbers"], [3])
        self.assertFalse(material_report([pdf])["complete"])

    def test_readable_page_with_failed_analysis_is_partly_analysed(self):
        pdf = document(1, "a.pdf", page(1, 1, 1, 1), page(2, 1, 2, 2, analysis="failed", covered=False))
        status = file_status(pdf)
        self.assertEqual(status["state"], "partly_analysed")
        self.assertEqual(status["failed_page_numbers"], [2])
        self.assertEqual(status["pages_analysis_failed"], 1)
        self.assertEqual(status["pages_not_analysed"], 0)
        report = material_report([pdf])
        self.assertEqual(issue(report, "analysis_failed", 1)["page_numbers"], [2])
        self.assertFalse(find_issues(report, "uncovered_pages", 1))   # failed is reported as failed, once
        self.assertFalse(report["complete"])

    def test_all_failed_analysis_is_not_analysed(self):
        pdf = document(1, "a.pdf", page(1, 1, 1, 1, analysis="failed", covered=False),
                       page(2, 1, 2, 2, analysis="not_analysed", covered=False),
                       page(3, 1, 3, 3, status="unreadable"))
        self.assertEqual(file_status(pdf)["state"], "not_analysed")

    def test_processing_wins_over_everything_but_exclusion(self):
        pdf = document(1, "a.pdf", page(1, 1, 1, 1, status="unreadable"), page(2, 1, 2, 2, status="recognizing"))
        self.assertEqual(file_status(pdf)["state"], "processing")
        self.assertEqual(file_status(pdf)["processing_page_numbers"], [2])

    def test_excluded_file_and_excluded_pages(self):
        excluded = document(1, "alt.pdf", page(1, 1, 1, 1, excluded=True), page(2, 1, 2, 2, excluded=True, status="failed"))
        self.assertEqual(file_status(excluded)["state"], "excluded")
        # Excluding the one unreadable page is how a student finishes a file.
        fixed = document(2, "b.pdf", page(3, 2, 1, 3), page(4, 2, 2, 4, status="failed", excluded=True))
        status = file_status(fixed)
        self.assertEqual(status["state"], "analysed")
        self.assertEqual(status["excluded_page_numbers"], [2])
        self.assertTrue(material_report([excluded, fixed])["complete"])

    def test_file_without_pages_is_unreadable_and_reported(self):
        report = material_report([document(1, "leer.pdf")])
        self.assertEqual(report["files"][0]["state"], "unreadable")
        self.assertEqual(issue(report, "unreadable_pages", 1)["page_numbers"], [])
        self.assertFalse(report["complete"])

    def test_counts_are_disjoint_by_reading_state(self):
        for status in material_report(six_documents())["files"]:
            parts = (status["pages_read"] + status["pages_review"] + status["pages_unreadable"]
                     + status["pages_processing"] + status["pages_duplicate"] + status["pages_excluded"])
            self.assertEqual(parts, status["pages_total"], status["filename"])
            self.assertEqual(status["pages_readable"],
                             status["pages_read"] + status["pages_review"] + status["pages_duplicate"])


class CoverageTests(unittest.TestCase):
    def three_pages(self):
        return document(1, "handout.pdf", page(1, 1, 1, 1), page(2, 1, 2, 2, covered=False), page(3, 1, 3, 3))

    def test_uncovered_page_is_reported_and_blocks_completeness(self):
        report = material_report([self.three_pages()])
        self.assertEqual(report["files"][0]["uncovered_page_numbers"], [2])
        self.assertEqual(report["files"][0]["state"], "analysed")
        self.assertEqual(report["pages_uncovered"], 1)
        self.assertEqual(issue(report, "uncovered_pages", 1)["page_numbers"], [2])
        self.assertFalse(report["complete"])

    def test_no_topic_pages_are_not_uncovered(self):
        """The coverage-repair pass asked the model again; it confirmed page 2 holds no topic."""
        stored: list[Any] = ["2", "junk", None]      # analysis_json is JSON: repair, not reject
        report = material_report([self.three_pages()], no_topic_page_ids=stored)
        status = report["files"][0]
        self.assertEqual(status["uncovered_page_numbers"], [])
        self.assertEqual(status["no_topic_page_numbers"], [2])
        self.assertEqual(report["pages_no_topic"], 1)
        self.assertTrue(report["complete"])
        self.assertEqual(report["issues"], [])

    def test_no_content_page_is_not_uncovered(self):
        cover = document(1, "deckblatt.pdf", page(1, 1, 1, 1, analysis="no_content", covered=False), page(2, 1, 2, 2))
        report = material_report([cover])
        self.assertEqual(report["pages_uncovered"], 0)
        self.assertTrue(report["complete"])

    def test_not_analysed_pages_are_reported_but_not_as_uncovered(self):
        report = material_report(six_documents())
        self.assertEqual(issue(report, "not_analysed", 6)["page_numbers"], [1, 2])
        self.assertFalse(find_issues(report, "uncovered_pages", 6))


class DuplicateResolutionTests(unittest.TestCase):
    def two_files(self, **original):
        first = document(1, "handout.pdf", page(1, 1, 1, 1, **original), page(2, 1, 2, 2))
        copy = document(2, "handout-foto.jpg",
                        page(3, 2, 1, 3, duplicate_of=1, analysis="duplicate", covered=False))
        return first, copy

    def test_duplicate_counts_as_read_and_covered_when_its_original_is(self):
        report = material_report(list(self.two_files()))
        copy = report["files"][1]
        self.assertEqual(copy["state"], "analysed")
        self.assertEqual(copy["pages_duplicate"], 1)
        self.assertEqual(copy["duplicate_page_numbers"], [1])
        self.assertEqual(copy["pages"][0]["duplicate_of"], 1)
        self.assertTrue(copy["pages"][0]["covered"])
        self.assertTrue(report["complete"])

    def test_duplicate_of_an_uncovered_original_is_uncovered(self):
        report = material_report(list(self.two_files(covered=False)))
        self.assertEqual(report["files"][1]["uncovered_page_numbers"], [1])
        self.assertFalse(report["complete"])

    def test_duplicate_stands_alone_when_its_original_is_excluded(self):
        report = material_report(list(self.two_files(excluded=True)))
        copy = report["files"][1]
        self.assertEqual(copy["pages"][0]["state"], "read")
        self.assertEqual(copy["state"], "not_analysed")
        self.assertFalse(report["complete"])

    def test_duplicate_stands_alone_when_its_original_was_deleted(self):
        copy = document(2, "foto.jpg", page(3, 2, 1, 3, duplicate_of=99, analysis="duplicate", covered=False))
        self.assertEqual(material_report([copy])["files"][0]["state"], "not_analysed")

    def test_file_looked_at_alone_trusts_a_cross_file_match(self):
        _, copy = self.two_files()
        self.assertEqual(file_status(copy)["state"], "analysed")
        first, copy = self.two_files(covered=False)
        every_page = {item.page_id: item for item in first.pages + copy.pages}
        self.assertEqual(file_status(copy, originals=every_page)["uncovered_page_numbers"], [1])

    def test_duplicate_cycle_does_not_recurse_forever(self):
        """Two pages that each claim the other as original: neither was analysed."""
        loop = document(1, "a.pdf", page(1, 1, 1, 1, duplicate_of=2, analysis="duplicate", covered=False),
                        page(2, 1, 2, 2, duplicate_of=1, analysis="duplicate", covered=False))
        report = material_report([loop])
        status = report["files"][0]
        self.assertEqual(status["pages_total"], 2)
        self.assertEqual(status["not_analysed_page_numbers"], [1, 2])
        self.assertFalse(report["complete"])

    def test_duplicate_mark_without_an_original_is_not_proof_of_coverage(self):
        """analysis "duplicate" but no original id: nothing shows the content reached a topic."""
        orphan = document(1, "foto.jpg", page(5, 1, 1, 1, analysis="duplicate", covered=False))
        self.assertEqual(page_state(orphan.pages[0]), "read")
        for status in (file_status(orphan), material_report([orphan])["files"][0]):
            self.assertEqual(status["state"], "not_analysed")
            self.assertEqual(status["pages"][0]["covered"], False)
        self.assertFalse(material_report([orphan])["complete"])

    def test_duplicate_of_itself_is_not_a_duplicate(self):
        own = document(1, "a.pdf", page(5, 1, 1, 1, duplicate_of=5))
        self.assertEqual(page_state(own.pages[0]), "read")
        self.assertEqual(material_report([own])["files"][0]["state"], "analysed")


class ReportTests(unittest.TestCase):
    def test_report_counts_and_issue_list_for_six_documents(self):
        report = material_report(six_documents(), topics_total=6, topics_included=4, terms_total=23,
                                 skipped_duplicate_files=["kopie.pdf", "kopie.pdf", " "])
        self.assertEqual(report["pages_total"], 10)
        self.assertEqual(report["pages_read"], 5)       # 11, 12, 21, 61, 62
        self.assertEqual(report["pages_review"], 1)     # 31
        self.assertEqual(report["pages_unreadable"], 3)  # 22, 41, 42
        self.assertEqual(report["pages_processing"], 1)  # 51
        self.assertEqual(report["pages_not_analysed"], 2)
        self.assertEqual(report["pages_uncovered"], 0)
        self.assertEqual(report["topics_total"], 6)
        self.assertEqual(report["topics_included"], 4)
        self.assertEqual(report["terms_total"], 23)
        self.assertEqual(report["skipped_duplicate_files"], ["kopie.pdf"])
        codes = [(item["code"], item["file_id"]) for item in report["issues"]]
        self.assertEqual(codes, [
            ("processing", 5), ("unreadable_pages", 2), ("unreadable_pages", 4), ("needs_review", 3),
            ("not_analysed", 6), ("duplicate_file_skipped", None), ("topics_excluded", None),
        ])
        self.assertEqual(issue(report, "unreadable_pages", 4)["page_numbers"], [1, 2])
        self.assertEqual(issue(report, "topics_excluded")["count"], 2)
        self.assertEqual(issue(report, "duplicate_file_skipped")["filename"], "kopie.pdf")
        for item in report["issues"]:
            self.assertIn(item["code"], ISSUE_CODES)

    def test_complete_material(self):
        files = [document(1, "a.pdf", page(1, 1, 1, 1), page(2, 1, 2, 2)), document(2, "b.jpg", page(3, 2, 1, 3))]
        report = material_report(files, topics_total=3, topics_included=3,
                                 skipped_duplicate_files=["a-kopie.pdf"])
        self.assertTrue(report["complete"])       # a skipped copy loses nothing
        self.assertEqual(report["documents_analysed"], 2)
        self.assertEqual([item["code"] for item in report["issues"]], ["duplicate_file_skipped"])

    def test_excluded_topics_do_not_make_material_incomplete(self):
        report = material_report([document(1, "a.pdf", page(1, 1, 1, 1))], topics_total=5, topics_included=1)
        self.assertTrue(report["complete"])
        self.assertEqual(issue(report, "topics_excluded")["count"], 4)

    def test_no_documents(self):
        report = material_report([])
        self.assertEqual(report["documents_uploaded"], 0)
        self.assertEqual(report["files"], [])
        self.assertTrue(report["complete"])

    def test_bad_analysis_value_is_repaired_to_not_analysed(self):
        status = file_status(document(1, "a.pdf", dataclasses.replace(page(1, 1, 1, 1, covered=False), analysis="done")))
        self.assertEqual(status["pages"][0]["analysis"], "not_analysed")
        self.assertEqual(status["state"], "not_analysed")

    def test_totals_are_the_sum_of_the_files_and_stay_disjoint_with_copies_and_exclusions(self):
        files = six_documents() + [
            document(7, "handout-foto.jpg",
                     page(71, 7, 1, 11, duplicate_of=11, analysis="duplicate", covered=False),
                     page(72, 7, 2, 12, excluded=True),
                     page(73, 7, 3, 13, covered=False)),
        ]
        report = material_report(files)
        for key in ("pages_total", "pages_read", "pages_review", "pages_unreadable", "pages_processing",
                    "pages_duplicate", "pages_excluded", "pages_readable", "pages_uncovered",
                    "pages_not_analysed", "pages_analysis_failed", "pages_no_topic"):
            self.assertEqual(report[key], sum(status[key] for status in report["files"]), key)
        self.assertEqual(report["pages_total"], 13)
        self.assertEqual(report["pages_duplicate"], 1)
        self.assertEqual(report["pages_excluded"], 1)
        for status in report["files"]:
            parts = (status["pages_read"] + status["pages_review"] + status["pages_unreadable"]
                     + status["pages_processing"] + status["pages_duplicate"] + status["pages_excluded"])
            self.assertEqual(parts, status["pages_total"], status["filename"])
            subsets = (status["pages_uncovered"] + status["pages_not_analysed"]
                       + status["pages_analysis_failed"] + status["pages_no_topic"])
            self.assertLessEqual(subsets, status["pages_readable"], status["filename"])
        self.assertEqual(issue(report, "uncovered_pages", 7)["page_numbers"], [3])

    def test_pages_are_reported_in_page_order_whatever_the_input_order(self):
        pdf = document(1, "a.pdf", page(3, 1, 3, 3, status="failed"), page(1, 1, 1, 1),
                       page(2, 1, 2, 2, status="failed"))
        status = file_status(pdf)
        self.assertEqual([entry["page_id"] for entry in status["pages"]], [1, 2, 3])
        self.assertEqual(status["unreadable_page_numbers"], [2, 3])
        self.assertEqual(material_report([pdf]), material_report([pdf]))

    def test_waiting_page_keeps_its_extraction_status_for_the_start_page(self):
        status = material_report(six_documents())["files"][4]
        self.assertEqual(status["pages"][0]["state"], "processing")
        self.assertEqual(status["pages"][0]["extraction_status"], "waiting")


class StoredBookkeepingTests(unittest.TestCase):
    """The facts come from analysis_json: JSON turns int keys into strings, keys go
    missing, single values replace lists. None of that may invent or hide a gap."""

    def test_duplicates_survive_a_json_round_trip(self):
        stored = json.loads(json.dumps(find_duplicate_pages([(1, UTILITARISMUS), (2, KANT), (3, UTILITARISMUS)])))
        self.assertEqual(stored, {"3": 1})                     # the trap: string keys
        links = duplicate_links(stored)
        self.assertEqual(links, {3: 1})
        copy = page(3, 2, 1, 3, duplicate_of=links.get(3), analysis="duplicate", covered=False)
        report = material_report([document(1, "a.pdf", page(1, 1, 1, 1), page(2, 1, 2, 2)),
                                  document(2, "b.jpg", copy)])
        self.assertEqual(report["files"][1]["state"], "analysed")
        self.assertTrue(report["complete"])

    def test_duplicate_links_repairs_junk(self):
        self.assertEqual(duplicate_links({"3": 1, "4": "2", "5": "5", "x": 1, "6": None, "7": True}),
                         {3: 1, 4: 2})
        for raw in (None, [], "3", 3):
            self.assertEqual(duplicate_links(raw), {})

    def test_string_original_id_is_resolved_not_reported_as_unanalysed(self):
        copy = page(3, 2, 1, 3, duplicate_of="1", analysis="duplicate", covered=False)  # type: ignore[arg-type]
        report = material_report([document(1, "a.pdf", page(1, 1, 1, 1)), document(2, "b.jpg", copy)])
        self.assertEqual(report["files"][1]["pages"][0]["state"], "duplicate")
        self.assertEqual(report["files"][1]["pages"][0]["duplicate_of"], 1)
        self.assertTrue(report["complete"])

    def test_a_lone_no_topic_id_is_one_page_not_its_digits(self):
        handout = document(1, "handout.pdf", page(1, 1, 1, 1), page(2, 1, 2, 2, covered=False),
                           page(12, 1, 3, 3, covered=False))
        for stored in ("12", 12, ["12"]):
            status = material_report([handout], no_topic_page_ids=stored)["files"][0]
            self.assertEqual(status["no_topic_page_numbers"], [3], stored)   # page id 12
            self.assertEqual(status["uncovered_page_numbers"], [2], stored)  # page id 2 is still open
        for junk in (None, {"a": 1}, 3.5j):
            status = material_report([handout], no_topic_page_ids=junk)["files"][0]
            self.assertEqual(status["uncovered_page_numbers"], [2, 3])

    def test_no_topic_flag_only_on_pages_without_a_topic(self):
        status = file_status(document(1, "a.pdf", page(1, 1, 1, 1), page(2, 1, 2, 2, covered=False)),
                             no_topic_page_ids=[1, 2])
        self.assertEqual([entry["no_topic"] for entry in status["pages"]], [False, True])
        self.assertEqual(status["pages_no_topic"], 1)

    def test_skipped_files_from_stored_json(self):
        one = [document(1, "a.pdf", page(1, 1, 1, 1))]
        self.assertEqual(material_report(one, skipped_duplicate_files=None)["skipped_duplicate_files"], [])
        self.assertEqual(material_report(one, skipped_duplicate_files="kopie.pdf")["skipped_duplicate_files"],
                         ["kopie.pdf"])
        mixed = [{"filename": "b.pdf"}, "b.pdf", None, "", "c.pdf"]
        self.assertEqual(material_report(one, skipped_duplicate_files=mixed)["skipped_duplicate_files"],
                         ["b.pdf", "c.pdf"])

    def test_pages_a_topic_cites_count_as_analysed(self):
        """Projects analysed before the page lists existed only have the citations. Every
        one of their pages must not turn into "never analysed"."""
        legacy = document(1, "alt.pdf", page(1, 1, 1, 1, analysis="not_analysed"),
                          page(2, 1, 2, 2, analysis="failed"),
                          page(3, 1, 3, 3, analysis="not_analysed", covered=False))
        report = material_report([legacy])
        status = report["files"][0]
        self.assertEqual([entry["analysis"] for entry in status["pages"]], ["analysed", "analysed", "not_analysed"])
        self.assertEqual(status["state"], "partly_analysed")
        self.assertEqual(issue(report, "not_analysed", 1)["page_numbers"], [3])
        self.assertFalse(find_issues(report, "analysis_failed", 1))

    def test_odd_numbers_do_not_crash_the_report(self):
        odd = document(1, "x.pdf",
                       dataclasses.replace(page(1, 1, 1, 1), page_number=None, page_order=None),  # type: ignore[arg-type]
                       dataclasses.replace(page(2, 1, 2, 2), confidence="0.3", text_chars=None),  # type: ignore[arg-type]
                       page(3, 1, 3, 3))
        report = material_report([odd], topics_total="4", topics_included=None, terms_total="x")  # type: ignore[arg-type]
        status = report["files"][0]
        self.assertEqual(status["pages_total"], 3)
        self.assertEqual(status["review_page_numbers"], [2])     # "0.3" is below the review band
        self.assertEqual(report["topics_total"], 4)
        self.assertEqual(issue(report, "topics_excluded")["count"], 4)
        self.assertEqual(report["terms_total"], 0)


class DuplicateDetectionTests(unittest.TestCase):
    def test_exact_duplicate(self):
        pages = [(1, UTILITARISMUS), (2, KANT), (3, UTILITARISMUS)]
        self.assertEqual(find_duplicate_pages(pages), {3: 1})

    def test_near_duplicate_with_an_ocr_slip_hyphenation_and_another_page_number(self):
        photo = (UTILITARISMUS.replace("größte Glück", "groBte Glück")
                 .replace("Utilitarismus und", "Utilita-\nrismus und") + " Seite 4")
        scan = UTILITARISMUS.upper() + "\nSeite 1"
        duplicates = find_duplicate_pages([(10, scan), (11, KANT), (12, photo)])
        self.assertEqual(duplicates, {12: 10})

    def test_a_messier_copy_is_an_overlap_not_a_dropped_duplicate(self):
        """A false duplicate would drop content from the analysis, so the bar is high;
        a copy below it is still surfaced, as an overlap."""
        photo = (UTILITARISMUS.replace("größte Glück", "groBte Glück").replace("Kalkül", "Kalkul")
                 + " Seite 4")
        scan = "ETHIK Klasse 9\n" + UTILITARISMUS + "\nSeite 1"
        self.assertEqual(find_duplicate_pages([(10, scan), (12, photo)]), {})
        overlaps = find_overlaps([(10, scan), (12, photo)])
        self.assertEqual([(first, second) for first, second, _ in overlaps], [(10, 12)])
        self.assertGreaterEqual(overlaps[0][2], 0.9)

    def test_different_pages_and_short_pages_are_not_duplicates(self):
        short = "Name: ____ Datum: ____ Klasse: 9b"
        self.assertEqual(find_duplicate_pages([(1, UTILITARISMUS), (2, KANT)]), {})
        self.assertEqual(find_duplicate_pages([(1, short), (2, short)]), {})
        self.assertEqual(find_duplicate_pages([(1, ""), (2, "")]), {})

    def test_chain_of_copies_points_at_the_first(self):
        pages = [(1, KANT), (2, KANT + " Seite 2"), (3, KANT)]
        self.assertEqual(find_duplicate_pages(pages), {2: 1, 3: 1})

    def test_threshold_is_respected(self):
        half = UTILITARISMUS[: len(UTILITARISMUS) // 2]
        self.assertEqual(find_duplicate_pages([(1, UTILITARISMUS), (2, half)]), {})
        self.assertEqual(find_duplicate_pages([(1, UTILITARISMUS), (2, half)], threshold=0.4), {2: 1})

    def test_overlap_revision_sheet_repeating_a_handout(self):
        first_half = UTILITARISMUS[: UTILITARISMUS.index("Der Handlungsutilitarismus")]
        revision = first_half + " " + REVISION_EXTRA
        overlaps = find_overlaps([(1, UTILITARISMUS), (2, KANT), (3, revision)])
        self.assertEqual([(first, second) for first, second, _ in overlaps], [(1, 3)])
        share = overlaps[0][2]
        self.assertGreaterEqual(share, 0.5)
        self.assertLess(share, 1.0)
        # It is not a duplicate: the revision sheet has its own tasks.
        self.assertEqual(find_duplicate_pages([(1, UTILITARISMUS), (3, revision)]), {})

    def test_duplicates_are_not_overlaps(self):
        self.assertEqual(find_overlaps([(1, UTILITARISMUS), (2, UTILITARISMUS)]), [])

    def test_content_words(self):
        self.assertEqual(content_words("Seite 3 – • Kant: Pflicht & Neigung | x"), 4)
        self.assertEqual(content_words("Utilita-\nrismus"), 1)
        self.assertEqual(content_words(""), 0)
        self.assertGreaterEqual(content_words(UTILITARISMUS), 40)


if __name__ == "__main__":
    unittest.main()

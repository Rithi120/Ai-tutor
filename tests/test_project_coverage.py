"""Project Mode learning state and completeness (learnova/projects/coverage.py)."""

import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

from learnova.projects.coverage import (
    ACTIVITIES, CHECK_CODES, TOPIC_STATES, TopicEvidence, next_activity, project_coverage, topic_state,
)
from learnova.projects.document_analysis import FileFacts, PageFacts, material_report

TODAY = date(2026, 10, 31)


def evidence(topic_id: int = 1, **values: Any) -> TopicEvidence:
    return TopicEvidence(topic_id=topic_id, title=f"Topic {topic_id}", **values)


UNDERSTOOD: dict[str, Any] = dict(lessons_started=1, lessons_finished=1, answers=4, correct_answers=3, mastery_score=75.0,
                  learned=True, terms_total=5, terms_taught=4, items_total=2, items_taught=2)
MASTERED: dict[str, Any] = {**UNDERSTOOD, "mastery_score": 92.0, "review_step": 2}


def file_row(file_id, state):
    return {"file_id": file_id, "filename": f"Ethik-Blatt-{file_id}.pdf", "state": state}


def report(states=("analysed",) * 6, **values):
    base = {"files": [file_row(index, state) for index, state in enumerate(states, start=1)],
            "documents_uploaded": len(states),
            "documents_analysed": sum(1 for state in states if state in ("analysed", "needs_ocr_review")),
            "pages_total": 12, "pages_read": 12, "pages_uncovered": 0, "pages_not_analysed": 0, "issues": []}
    base.update(values)
    return base


def checks(result):
    return {check["code"]: check for check in result["checks"]}


class TopicStateTests(unittest.TestCase):
    def test_nothing_done_is_not_started(self):
        self.assertEqual(topic_state(evidence()), "not_started")

    def test_opening_or_clicking_through_a_lesson_is_only_taught(self):
        self.assertEqual(topic_state(evidence(lessons_started=1)), "taught")
        self.assertEqual(topic_state(evidence(lessons_started=2, lessons_finished=2)), "taught")
        # Even a stale "learned" flag and a high score do not make an unanswered topic understood.
        self.assertEqual(topic_state(evidence(lessons_started=1, learned=True, mastery_score=100.0)), "taught")

    def test_wrong_answers_are_practising_never_understood(self):
        wrong = evidence(lessons_started=1, lessons_finished=1, answers=3, correct_answers=0, mastery_score=0.0,
                         learned=True)
        self.assertEqual(topic_state(wrong), "practising")

    def test_understood_needs_learned_two_correct_answers_and_mastery_60(self):
        self.assertEqual(topic_state(evidence(**UNDERSTOOD)), "understood")
        self.assertEqual(topic_state(evidence(**{**UNDERSTOOD, "learned": False})), "practising")
        self.assertEqual(topic_state(evidence(**{**UNDERSTOOD, "correct_answers": 1})), "practising")
        self.assertEqual(topic_state(evidence(**{**UNDERSTOOD, "mastery_score": 59.9})), "practising")
        self.assertEqual(topic_state(evidence(**{**UNDERSTOOD, "mastery_score": 60.0, "correct_answers": 2})),
                         "understood")

    def test_correct_answers_cannot_exceed_answers(self):
        self.assertEqual(topic_state(evidence(**{**UNDERSTOOD, "answers": 1, "correct_answers": 5})), "practising")

    def test_mastered_needs_mastery_85_and_two_reviews(self):
        self.assertEqual(topic_state(evidence(**MASTERED)), "mastered")
        self.assertEqual(topic_state(evidence(**{**MASTERED, "review_step": 1})), "understood")
        self.assertEqual(topic_state(evidence(**{**MASTERED, "mastery_score": 84.0})), "understood")

    def test_reteach_keeps_a_topic_practising(self):
        self.assertEqual(topic_state(evidence(**{**MASTERED, "reteach": True})), "practising")

    def test_answers_without_a_lesson_are_practice(self):
        self.assertEqual(topic_state(evidence(answers=2, correct_answers=2)), "practising")

    def test_values_straight_from_database_rows(self):
        # NULL columns, a DateTime review date and strings must not crash the workspace.
        odd: dict[str, Any] = dict(importance=None, included=None, lessons_started=None, answers="4",
                                   correct_answers=3.0, mastery_score=None, learned=1, review_step=None,
                                   reteach=None, terms_total=None, next_review_on="2026-10-30")
        topic = evidence(**odd)
        self.assertEqual((topic.importance, topic.included, topic.mastery_score, topic.answers, topic.reteach),
                         (2, True, 0.0, 4, False))
        self.assertEqual(topic.next_review_on, date(2026, 10, 30))
        self.assertEqual(topic_state(topic), "practising")
        self.assertEqual(topic_state(evidence(**{**UNDERSTOOD, "mastery_score": float("nan")})), "practising")
        self.assertEqual(evidence(learned="false").learned, False)
        self.assertIsNone(evidence(next_review_on="soon").next_review_on)

    def test_review_dates_as_datetimes(self):
        due = datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)
        topic = evidence(**MASTERED, next_review_on=due)
        self.assertEqual(topic.next_review_on, date(2026, 10, 31))
        self.assertEqual(next_activity(topic, topic_state(topic), today=datetime(2026, 10, 31, 8, 0)), "review_due")
        self.assertEqual(next_activity(topic, topic_state(topic), today=datetime(2026, 10, 30, 8, 0)), "done")


class NextActivityTests(unittest.TestCase):
    def activity(self, **values):
        topic = evidence(**values)
        result = next_activity(topic, topic_state(topic), today=TODAY)
        self.assertIn(result, ACTIVITIES)
        return result

    def test_each_state_has_its_next_step(self):
        self.assertEqual(self.activity(), "start_lesson")
        self.assertEqual(self.activity(lessons_started=1), "continue_practice")
        self.assertEqual(self.activity(lessons_started=1, answers=2, correct_answers=0), "continue_practice")
        self.assertEqual(self.activity(**UNDERSTOOD), "exam_practice")
        self.assertEqual(self.activity(**MASTERED), "done")

    def test_reteach_comes_first(self):
        self.assertEqual(self.activity(**{**MASTERED, "reteach": True}), "reteach")
        self.assertEqual(self.activity(lessons_started=1, answers=1, reteach=True), "reteach")

    def test_review_due_dates(self):
        self.assertEqual(self.activity(**MASTERED, next_review_on=TODAY), "review_due")
        self.assertEqual(self.activity(**MASTERED, next_review_on=TODAY - timedelta(days=3)), "review_due")
        self.assertEqual(self.activity(**MASTERED, next_review_on=TODAY + timedelta(days=1)), "done")
        self.assertEqual(self.activity(**UNDERSTOOD, next_review_on=TODAY), "review_due")
        # A topic still being practised keeps practising, whatever the calendar says.
        self.assertEqual(self.activity(lessons_started=1, answers=1, next_review_on=TODAY), "continue_practice")

    def test_key_terms_before_exam_practice(self):
        self.assertEqual(self.activity(**{**UNDERSTOOD, "terms_taught": 1}), "study_terms")
        self.assertEqual(self.activity(**{**UNDERSTOOD, "terms_taught": 1, "terms_studied": 3}), "exam_practice")
        self.assertEqual(self.activity(**{**MASTERED, "terms_taught": 0}), "study_terms")
        self.assertEqual(self.activity(**{**UNDERSTOOD, "terms_total": 0, "terms_taught": 0}), "exam_practice")


class ProjectCoverageTests(unittest.TestCase):
    def topics(self, *states):
        values = {"not_started": {}, "taught": {"lessons_started": 1}, "practising": {"lessons_started": 1, "answers": 2},
                  "understood": UNDERSTOOD, "mastered": MASTERED}
        return [evidence(index, **values[state]) for index, state in enumerate(states, start=1)]

    def test_complete_project(self):
        result = project_coverage(report(), self.topics("mastered", "understood", "mastered"))
        self.assertEqual([check["code"] for check in result["checks"]], list(CHECK_CODES))
        self.assertTrue(all(check["ok"] for check in result["checks"]))
        self.assertTrue(result["complete"])
        self.assertEqual(result["percent"], 100)
        self.assertEqual(result["counts"], {"not_started": 0, "taught": 0, "practising": 0, "understood": 1,
                                            "mastered": 2})
        self.assertEqual(result["not_taught"], [])

    def test_six_documents_one_unreadable_is_not_processed(self):
        result = project_coverage(report(("analysed", "analysed", "needs_ocr_review", "analysed", "unreadable",
                                          "partly_analysed")), self.topics("mastered"))
        documents = checks(result)["documents_processed"]
        self.assertEqual((documents["ok"], documents["done"], documents["total"]), (False, 4, 6))
        self.assertFalse(result["complete"])
        self.assertLess(result["percent"], 100)

    def test_excluded_documents_do_not_count(self):
        documents = checks(project_coverage(report(("analysed", "excluded")), []))["documents_processed"]
        self.assertEqual((documents["ok"], documents["done"], documents["total"]), (True, 1, 1))

    def test_counts_without_a_file_list(self):
        documents = checks(project_coverage({"documents_uploaded": 3, "documents_analysed": 2}, []))
        self.assertEqual(documents["documents_processed"]["done"], 2)
        self.assertFalse(documents["documents_processed"]["ok"])

    def test_uncovered_or_failed_pages_mean_topics_are_missing(self):
        uncovered = checks(project_coverage(report(pages_uncovered=2), self.topics("mastered")))["topics_extracted"]
        self.assertEqual((uncovered["ok"], uncovered["done"], uncovered["total"]), (False, 10, 12))
        failed = report(issues=[{"code": "analysis_failed", "file_id": 2, "page_numbers": [3]}])
        self.assertFalse(checks(project_coverage(failed, self.topics("mastered")))["topics_extracted"]["ok"])
        review = report(issues=[{"code": "needs_review", "file_id": 2, "page_numbers": [3]}])
        self.assertTrue(checks(project_coverage(review, self.topics("mastered")))["topics_extracted"]["ok"])

    def test_reads_the_real_material_report(self):
        # Six papers: handwritten pages that need a check are read pages too; one page
        # failed analysis, one is not covered by any topic, one file is unreadable.
        def page(page_id, file_id, number, *, status="ready", source="vision", confidence="ok", analysis="analysed",
                 covered=True):
            return PageFacts(page_id=page_id, file_id=file_id, page_number=number, page_order=page_id,
                             extraction_status=status, confidence_status=confidence, confidence=0.9,
                             text_source=source, text_chars=900, words=150, analysis=analysis, covered=covered)

        files = [
            FileFacts(1, "Ethik-1.pdf", "pdf", (page(1, 1, 1), page(2, 1, 2))),
            FileFacts(2, "Ethik-2.jpg", "image", (page(3, 2, 1, confidence="unclear"),
                                                  page(4, 2, 2, confidence="unclear"))),
            FileFacts(3, "Ethik-3.pdf", "pdf", (page(5, 3, 1), page(6, 3, 2, analysis="failed", covered=False))),
            FileFacts(4, "Ethik-4.pdf", "pdf", (page(7, 4, 1), page(8, 4, 2, covered=False))),
            FileFacts(5, "Ethik-5.jpg", "image", (page(9, 5, 1, status="failed"),)),
            FileFacts(6, "Ethik-6.pdf", "pdf", (page(10, 6, 1, source="local_ocr"),)),
        ]
        result = project_coverage(material_report(files), self.topics("mastered"))
        documents, extracted = checks(result)["documents_processed"], checks(result)["topics_extracted"]
        # analysed: file 1 and 4 (an uncovered page is reported separately); review: file 2 and 6.
        self.assertEqual((documents["ok"], documents["done"], documents["total"]), (False, 4, 6))
        # 9 readable pages (the two "unclear" ones included), 2 without a topic.
        self.assertEqual((extracted["ok"], extracted["done"], extracted["total"]), (False, 7, 9))
        self.assertFalse(result["complete"])
        fixed = [files[0], files[1], FileFacts(4, "Ethik-4.pdf", "pdf", (page(7, 4, 1), page(8, 4, 2))), files[5]]
        clean = project_coverage(material_report(fixed), self.topics("mastered"))
        self.assertTrue(checks(clean)["documents_processed"]["ok"])
        self.assertEqual(checks(clean)["topics_extracted"], {"code": "topics_extracted", "ok": True, "done": 7,
                                                             "total": 7})
        self.assertTrue(clean["complete"])

    def test_typed_topics_without_material(self):
        result = project_coverage({}, self.topics("mastered", "understood"))
        self.assertTrue(checks(result)["documents_processed"]["ok"])
        self.assertTrue(checks(result)["topics_extracted"]["ok"])
        self.assertTrue(result["complete"])
        self.assertEqual(project_coverage(None, self.topics("mastered")), project_coverage({}, self.topics("mastered")))  # type: ignore[arg-type]

    def test_pages_never_analysed_mean_topics_are_missing(self):
        # The hourly AI budget ran out: the remaining pages were never sent to the model.
        stopped = report(pages_not_analysed=3, issues=[{"code": "not_analysed", "file_id": 4, "page_numbers": [1]}])
        extracted = checks(project_coverage(stopped, self.topics("mastered")))["topics_extracted"]
        self.assertEqual((extracted["ok"], extracted["done"], extracted["total"]), (False, 9, 12))
        failed = checks(project_coverage(report(pages_analysis_failed=1), self.topics("mastered")))["topics_extracted"]
        self.assertEqual((failed["ok"], failed["done"]), (False, 11))

    def test_topics_left_out_by_the_cap_keep_the_extraction_check_open(self):
        # FIX1 (P1-11): found topics the cap left out are not silently "complete".
        dropped = checks(project_coverage(report(topics_dropped=2), self.topics("mastered")))["topics_extracted"]
        self.assertFalse(dropped["ok"])
        self.assertEqual(dropped["dropped"], 2)
        self.assertNotIn("dropped", checks(project_coverage(report(), self.topics("mastered")))["topics_extracted"])

    def test_no_topic_is_never_complete(self):
        result = project_coverage(report(), [])
        self.assertFalse(result["complete"])
        self.assertFalse(checks(result)["objectives_covered"]["ok"])
        self.assertFalse(checks(result)["understanding_demonstrated"]["ok"])

    def test_terms_need_80_percent_taught_or_studied(self):
        base = evidence(1, **{**MASTERED, "terms_total": 10, "terms_taught": 5, "terms_studied": 2})
        self.assertFalse(checks(project_coverage(report(), [base]))["terms_explained"]["ok"])
        enough = replace(base, terms_studied=3)
        terms = checks(project_coverage(report(), [enough]))["terms_explained"]
        self.assertEqual((terms["ok"], terms["done"], terms["total"]), (True, 8, 10))
        over = replace(base, terms_taught=10, terms_studied=10)
        self.assertEqual(checks(project_coverage(report(), [over]))["terms_explained"]["done"], 10)

    def test_items_need_80_percent_taught(self):
        topic = evidence(1, **{**MASTERED, "items_total": 5, "items_taught": 3})
        items = checks(project_coverage(report(), [topic]))["items_explained"]
        self.assertEqual((items["ok"], items["done"], items["total"]), (False, 3, 5))
        self.assertTrue(checks(project_coverage(report(), [replace(topic, items_taught=4)]))["items_explained"]["ok"])

    def test_every_included_topic_must_be_taught_and_understood(self):
        topics = self.topics("mastered", "taught", "practising", "not_started")
        result = project_coverage(report(), topics)
        objectives = checks(result)["objectives_covered"]
        understanding = checks(result)["understanding_demonstrated"]
        self.assertEqual((objectives["ok"], objectives["done"], objectives["total"]), (False, 3, 4))
        self.assertEqual((understanding["ok"], understanding["done"], understanding["total"]), (False, 1, 4))
        self.assertEqual(result["not_taught"], [4])
        self.assertEqual(set(result["counts"]), set(TOPIC_STATES))

    def test_opening_every_lesson_does_not_complete_a_project(self):
        topics = [evidence(index, lessons_started=1, lessons_finished=1, learned=True, mastery_score=100.0)
                  for index in (1, 2, 3)]
        result = project_coverage(report(), topics)
        self.assertTrue(checks(result)["objectives_covered"]["ok"])
        self.assertFalse(checks(result)["understanding_demonstrated"]["ok"])
        self.assertFalse(result["complete"])

    def test_excluded_topics_are_left_out_and_important_ones_are_listed(self):
        topics = [evidence(1, **MASTERED), evidence(2, importance=3), evidence(3, importance=1),
                  evidence(4, importance=3, included=False), evidence(5, importance=3, lessons_started=1)]
        result = project_coverage(report(), topics)
        self.assertEqual(result["not_taught_important"], [2])
        self.assertEqual(result["not_taught"], [2, 3])
        self.assertEqual(sum(result["counts"].values()), 4)
        self.assertTrue(project_coverage(report(), [topics[0], topics[3]])["complete"])

    def test_percent_is_learning_progress(self):
        self.assertEqual(project_coverage(report(), self.topics("not_started", "not_started"))["percent"], 0)
        taught = project_coverage(report(), self.topics("taught", "taught"))["percent"]
        halfway = project_coverage(report(), self.topics("understood", "taught"))["percent"]
        self.assertEqual(taught, 33)            # taught everything, understood nothing (no terms/items)
        self.assertLess(taught, halfway)
        self.assertLess(halfway, 100)
        self.assertEqual(project_coverage(report(), [])["percent"], 0)

    def test_percent_reaches_100_only_when_the_material_is_covered_too(self):
        topics = self.topics("mastered", "mastered")
        self.assertEqual(project_coverage(report(), topics)["percent"], 100)
        self.assertEqual(project_coverage(report(pages_uncovered=1), topics)["percent"], 99)


if __name__ == "__main__":
    unittest.main()

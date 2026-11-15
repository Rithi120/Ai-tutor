"""The exam autopilot's pure layer: competencies, grade estimate, next action."""

import unittest

from learnova.exam_prep import autopilot, competencies, grade
from learnova.exam_prep.autopilot import TopicState
from learnova.exam_prep.grade import TopicEvidence

NOTES = "Mitternachtsformel: x = (-b ± √(b²-4ac)) / 2a. Beispiel: x² - 5x + 6 = 0."


def row(statement, topic, coverage="covered", evidence="x = (-b ± √(b²-4ac)) / 2a", **extra):
    base = {"statement": statement, "topic": topic, "subtopic": "", "level": "intermediate", "importance": 2,
            "source_page_ids": [1], "coverage": coverage, "evidence": evidence}
    base.update(extra)
    return base


class CompetencyTests(unittest.TestCase):
    def test_rows_are_cleaned_and_vocabularies_enforced(self):
        rows = competencies.normalize_competencies({"competencies": [
            row("Ich kann  quadratische Gleichungen lösen.", "Quadratische Gleichungen", level="expert", importance=9,
                source_page_ids=[1, 99, "x"]),
            {"statement": "", "topic": "x"},
            "garbage",
        ]}, valid_page_ids=[1, 2], notes_text=NOTES)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["statement"], "Ich kann quadratische Gleichungen lösen.")
        self.assertEqual((rows[0]["level"], rows[0]["importance"], rows[0]["source_page_ids"]), ("intermediate", 3, [1]))
        self.assertEqual(rows[0]["coverage"], "covered")

    def test_a_covered_claim_without_real_evidence_becomes_missing(self):
        rows = competencies.normalize_competencies({"competencies": [
            row("Ich kann die Diskriminante deuten.", "Quadratische Gleichungen", "covered", "Diskriminante D = b² - 4ac"),
            row("Ich kann faktorisieren.", "Quadratische Gleichungen", "partial", ""),
            row("Ich kann die pq-Formel anwenden.", "Quadratische Gleichungen", "nonsense", "x"),
        ]}, valid_page_ids=[1], notes_text=NOTES)
        self.assertEqual([item["coverage"] for item in rows], ["missing", "missing", "missing"])
        self.assertEqual([item["evidence"] for item in rows], ["", "", ""],
                         "an unsupported coverage claim is the one mistake that must not get through")

    def test_duplicates_and_the_limit_are_respected(self):
        rows = competencies.normalize_competencies({"competencies": [
            row("Ich kann A.", "T"), row("ich kann a.", "T"), row("Ich kann B.", "T")]},
            valid_page_ids=[1], notes_text=NOTES, limit=2)
        self.assertEqual([item["statement"] for item in rows], ["Ich kann A.", "Ich kann B."])

    def test_competencies_attach_to_the_section_they_talk_about(self):
        sections = [{"id": 1, "title": "Quadratische Gleichungen", "main_topic": "Lösungsformel"},
                    {"id": 2, "title": "Lineare Funktionen", "main_topic": "Steigung"}]
        rows = competencies.attach_sections([
            row("Ich kann die Steigung ablesen.", "Lineare Funktionen"),
            row("Ich kann quadratische Gleichungen lösen.", "Quadratische Gleichungen"),
            row("Ich kann Wahrscheinlichkeiten berechnen.", "Stochastik"),
        ], sections)
        self.assertEqual([item["section_id"] for item in rows], [2, 1, None])
        summary = competencies.coverage_summary(rows)
        self.assertEqual((summary["total"], summary["covered"], summary["unassigned"]), (3, 3, 1))
        self.assertEqual(competencies.section_importance(rows, 1), 2.0)

    def test_coverage_summary_counts_partial_as_half(self):
        rows = [row("a", "T", "covered"), row("b", "T", "partial", "x"), row("c", "T", "missing", "")]
        summary = competencies.coverage_summary(rows)
        self.assertEqual(summary["covered_percent"], 50)
        self.assertEqual(competencies.missing_for_section([{**rows[2], "section_id": 7}], 7), ["c"])


# A Kompetenzraster page with 15 "Ich kann" rows under five headings (spec 1.1 tests).
FIFTEEN_ROWS = [
    ("Lineare Funktionen", ["Ich kann die Steigung einer linearen Funktion bestimmen.",
                            "Ich kann den Graphen einer linearen Funktion zeichnen.",
                            "Ich kann die Funktionsgleichung einer Geraden aufstellen."]),
    ("Quadratische Funktionen", ["Ich kann den Scheitelpunkt einer quadratischen Funktion bestimmen.",
                                 "Ich kann eine Normalparabel verschieben.",
                                 "Ich kann die Scheitelpunktform in die Normalform umwandeln."]),
    ("Lineare Gleichungssysteme", ["Ich kann ein lineares Gleichungssystem mit dem Einsetzungsverfahren lösen.",
                                   "Ich kann ein lineares Gleichungssystem grafisch lösen.",
                                   "Ich kann das Additionsverfahren anwenden."]),
    ("Satz des Pythagoras", ["Ich kann fehlende Seitenlängen im rechtwinkligen Dreieck berechnen.",
                             "Ich kann die Hypotenuse erkennen.",
                             "Ich kann den Satz des Pythagoras im Raum anwenden."]),
    ("Wahrscheinlichkeitsrechnung", ["Ich kann Laplace-Wahrscheinlichkeiten berechnen.",
                                     "Ich kann ein Baumdiagramm zeichnen.",
                                     "Ich kann die Pfadregeln anwenden."]),
]
FIFTEEN_PAGE = "Kompetenzraster Mathematik Klasse 9\n" + "\n".join(
    f"{heading}\n" + "\n".join(f"☐ {row}" for row in rows) for heading, rows in FIFTEEN_ROWS)


def grid_rows(rows=None):
    picked = rows if rows is not None else [(heading, row) for heading, items in FIFTEEN_ROWS for row in items]
    return {"competencies": [{"statement": row, "group_path": ["Kompetenzraster", heading], "level": "basic",
                              "importance": 2, "source_page_ids": [7], "section_title": heading}
                             for heading, row in picked]}


class KompetenzrasterInventoryTests(unittest.TestCase):
    """Spec 1.1: every row extracted, grounded, counted and tracked on its own."""

    def test_the_row_counter_reads_bullets_numbers_checkboxes_columns_and_wrapped_rows(self):
        page = ("Kompetenzraster\n• Ich kann lineare Funktionen zeichnen.\n- Ich kann die Steigung einer\n"
                "linearen Funktion bestimmen.\n1. Ich kann quadratische Gleichungen lösen\n"
                "☐ Ich kann die Mitternachtsformel anwenden.   Ich kann die Diskriminante deuten.\n"
                "I can solve systems | I can draw graphs\nIn der Klasse arbeiten wir viel. Ich kann das nicht.")
        rows = competencies.expected_rows(page)
        self.assertEqual(rows[:2], ["Ich kann lineare Funktionen zeichnen.",
                                    "Ich kann die Steigung einer linearen Funktion bestimmen."])
        self.assertEqual(len(rows), 7, "a sentence that only contains 'Ich kann' mid-line is no row")
        self.assertEqual(len(competencies.expected_rows(FIFTEEN_PAGE)), 15)

    def test_a_complete_answer_gives_fifteen_verified_rows_and_no_gap(self):
        inventory = competencies.ground_page(grid_rows(), page_id=7, page_text=FIFTEEN_PAGE)
        self.assertEqual(len(inventory.rows), 15)
        self.assertTrue(all(row["verified"] and row["source_page_ids"] == [7] for row in inventory.rows))
        self.assertIsNone(inventory.gap)
        self.assertEqual(inventory.rows[0]["group_path"], ["Kompetenzraster", "Lineare Funktionen"])

    def test_twelve_of_fifteen_is_a_gap(self):
        picked = [(heading, row) for heading, items in FIFTEEN_ROWS for row in items][:12]
        inventory = competencies.ground_page(grid_rows(picked), page_id=7, page_text=FIFTEEN_PAGE)
        self.assertEqual(inventory.gap, {"expected": 15, "found": 12, "missing": 3, "rejected": 0, "dropped": 0})

    def test_an_invented_statement_is_never_verified(self):
        payload = grid_rows()
        payload["competencies"].append({"statement": "Ich kann Integrale mit der Substitutionsregel berechnen.",
                                        "group_path": ["Analysis"], "source_page_ids": [7]})
        inventory = competencies.ground_page(payload, page_id=7, page_text=FIFTEEN_PAGE)
        self.assertEqual(len(inventory.rows), 15)
        self.assertEqual(inventory.rejected, ["Ich kann Integrale mit der Substitutionsregel berechnen."])
        self.assertEqual(inventory.gap["rejected"], 1, "an unverified row is a gap, never silent")

    def test_rows_differing_in_one_word_stay_two_and_the_cap_is_counted(self):
        rows = competencies.normalize_competencies({"competencies": [
            {"statement": "Ich kann lineare Funktionen zeichnen."},
            {"statement": "Ich kann quadratische Funktionen zeichnen."},
            {"statement": "ich kann  lineare Funktionen zeichnen"}]}, valid_page_ids=[1])
        self.assertEqual([row["statement"] for row in rows],
                         ["Ich kann lineare Funktionen zeichnen.", "Ich kann quadratische Funktionen zeichnen."])
        many = {"competencies": [{"statement": f"Ich kann Aufgabe {number} lösen"} for number in range(100)]}
        inventory = competencies.ground_page(many, page_id=1, page_text="\n".join(
            f"Ich kann Aufgabe {number} lösen" for number in range(100)))
        self.assertEqual((len(inventory.rows), inventory.dropped), (competencies.MAX_PER_PAGE, 40))
        self.assertEqual(inventory.gap["dropped"], 40)

    def test_the_parser_is_the_fallback_inventory(self):
        inventory = competencies.parsed_page(7, FIFTEEN_PAGE)
        self.assertEqual((len(inventory.rows), inventory.source, inventory.gap), (15, "parser", None))

    def test_attach_uses_stems_prefers_the_narrower_topic_and_leaves_ties_unassigned(self):
        sections = [{"id": 1, "title": "Lineare Funktionen", "main_topic": "Funktionen"},
                    {"id": 2, "title": "Quadratische Funktionen", "main_topic": "Funktionen"},
                    {"id": 3, "title": "Quadratische Gleichungen", "main_topic": "Gleichungen"},
                    {"id": 4, "title": "Lineare und quadratische Funktionen", "main_topic": "Funktionen"}]
        rows = competencies.attach_sections([
            {"statement": "Ich kann den Scheitelpunkt einer quadratischen Funktion bestimmen.", "group_path": []},
            {"statement": "Ich kann die Steigung einer linearen Funktion bestimmen.", "group_path": []},
            {"statement": "Ich kann Wahrscheinlichkeiten berechnen.", "group_path": []},
            {"statement": "Ich kann Gleichungen und Funktionen unterscheiden.", "group_path": []},
            {"statement": "Ich kann eine Parabel verschieben.", "group_path": ["Quadratische Funktionen"],
             "section_title": "Lineare Funktionen"},     # a wrong hint loses against the headings
        ], sections)
        self.assertEqual([row["section_id"] for row in rows], [2, 1, None, None, 2])

    def test_the_status_is_derived_and_mastery_is_never_inherited(self):
        state, evidence = competencies.competency_state, competencies.CompetencyEvidence
        self.assertEqual(state(evidence(1)), "identified")
        self.assertEqual(state(evidence(1, decision="confirmed")), "identified", "no topic yet")
        self.assertEqual(state(evidence(1, decision="removed", section_id=2)), competencies.REMOVED)
        self.assertEqual(state(evidence(1, decision="confirmed", section_id=2)), "not_started")
        self.assertEqual(state(evidence(1, decision="confirmed", section_id=2, taught_count=1)), "teaching")
        self.assertEqual(state(evidence(1, decision="confirmed", section_id=2, taught_count=1, answers=1,
                                        correct_answers=1)), "practised")
        self.assertEqual(state(evidence(1, decision="confirmed", section_id=2, answers=3, correct_answers=1)),
                         "needs_revision")
        self.assertEqual(state(evidence(1, decision="confirmed", section_id=2, answers=1, correct_answers=1,
                                        section_reteach=True)), "needs_revision")
        self.assertEqual(state(evidence(1, decision="confirmed", section_id=2, section_state="mastered",
                                        taught_count=2)), "teaching",
                         "the topic is mastered, this competency has no answer of its own")
        self.assertEqual(state(evidence(1, decision="confirmed", section_id=2, section_state="mastered",
                                        answers=2, correct_answers=2)), "mastered")

    def test_coverage_is_complete_only_when_every_competency_is_practised(self):
        check = competencies.competency_check
        self.assertIsNone(check([]), "no Kompetenzraster, no check")
        self.assertFalse(check(["practised", "mastered", "teaching"])["ok"])
        self.assertFalse(check(["practised", "identified"])["ok"])
        self.assertTrue(check(["practised", "mastered", competencies.REMOVED])["ok"])
        self.assertFalse(check(["practised"], missing_rows=3)["ok"], "a gap holds it open")
        self.assertEqual(check(["practised"], missing_rows=3)["total"], 4)
        self.assertFalse(check(["practised"], open_gaps=1)["ok"])

    def test_a_due_review_shows_needs_revision_but_never_reopens_the_coverage(self):
        state, evidence = competencies.competency_state, competencies.CompetencyEvidence
        addressed = competencies.counts_as_addressed
        mastered_due = evidence(1, decision="confirmed", section_id=2, section_state="mastered", answers=2,
                                correct_answers=2, review_due=True)
        self.assertEqual(state(mastered_due), "needs_revision", "the student sees the review is due")
        self.assertTrue(addressed(mastered_due), "complete does not flicker with the review schedule")
        weak_due = evidence(1, decision="confirmed", section_id=2, answers=3, correct_answers=1, review_due=True)
        self.assertFalse(addressed(weak_due), "weak answers still hold the check open")
        reteach = evidence(1, decision="confirmed", section_id=2, answers=1, correct_answers=1, section_reteach=True)
        self.assertFalse(addressed(reteach))
        self.assertFalse(addressed(evidence(1, decision="confirmed", section_id=2, taught_count=1)))

    def test_a_lesson_on_one_topic_does_not_teach_the_similar_one(self):
        rows = [{"id": 1, "statement": "Ich kann die Steigung einer linearen Funktion bestimmen."},
                {"id": 2, "statement": "Ich kann den Scheitelpunkt einer quadratischen Funktion bestimmen."}]
        lesson = ("Eine quadratische Funktion hat einen Scheitelpunkt. So bestimmen wir den Scheitelpunkt "
                  "der Funktion f(x) = (x-2)^2 + 1.")
        self.assertEqual(competencies.taught_competencies(lesson, rows), [2])
        self.assertEqual(competencies.match_competency("Bestimme die Steigung der linearen Funktion f(x)=2x+1.",
                                                       rows), 1)
        self.assertIsNone(competencies.match_competency("Was ist eine Funktion?", rows), "a tie is no tag")


class GradeTests(unittest.TestCase):
    def test_the_german_scale(self):
        self.assertEqual([grade.grade_for_percent(p) for p in (100, 92, 85, 70, 55, 35, 10)], [1, 1, 2, 3, 4, 5, 6])

    def test_no_evidence_means_no_estimate(self):
        result = grade.estimate_grade([TopicEvidence("A", 0, 0, assessed=False)])
        self.assertFalse(result.available)
        self.assertEqual(result.label, "–")
        self.assertEqual(result.basis, ("no_evidence",))

    def test_thin_evidence_gives_a_wide_range_and_untested_topics_drag_it_down(self):
        one_of_three = grade.estimate_grade([
            TopicEvidence("A", 95, 0.6), TopicEvidence("B", 0, 0, assessed=False), TopicEvidence("C", 0, 0, assessed=False)])
        self.assertTrue(one_of_three.available)
        self.assertLess(one_of_three.knowledge_percent, 60, "two untested topics count as roughly a 4")
        self.assertGreaterEqual(one_of_three.high_grade - one_of_three.low_grade, 1)
        self.assertIn("untested_topics", one_of_three.basis)
        all_strong = grade.estimate_grade([TopicEvidence(name, 95, 0.9) for name in "ABC"])
        self.assertEqual(all_strong.low_grade, 1)
        self.assertLessEqual(all_strong.high_grade, 2, "strong evidence still leaves a little room - never a certainty")
        self.assertGreater(all_strong.confidence, one_of_three.confidence)

    def test_a_mock_exam_pulls_the_estimate_towards_the_exam_result(self):
        without = grade.estimate_grade([TopicEvidence("A", 90, 0.8)])
        with_mock = grade.estimate_grade([TopicEvidence("A", 90, 0.8)], mock_scores=[60])
        self.assertLess(with_mock.expected_percent, without.expected_percent)
        self.assertEqual(with_mock.mock_percent, 60)
        self.assertIn("mock_exam", with_mock.basis)
        self.assertGreater(with_mock.confidence, without.confidence)

    def test_the_estimate_explains_why_it_moved(self):
        before = grade.estimate_grade([TopicEvidence("Quadratics", 50, 0.5)])
        after = grade.estimate_grade([TopicEvidence("Quadratics", 78, 0.8)], mock_scores=[71])
        reasons = grade.explain_change(before.as_dict(), after, previous_topics={"Quadratics": 50},
                                       current_topics={"Quadratics": 78})
        kinds = [reason["kind"] for reason in reasons]
        self.assertIn("topic_up", kinds)
        self.assertIn("mock", kinds)
        self.assertEqual(grade.explain_change(None, after), [{"kind": "first_estimate"}])
        self.assertTrue(grade.estimate_changed(before.as_dict(), after))
        self.assertFalse(grade.estimate_changed(after.as_dict(), after))


def topic(section_id, position, knowledge, *, known=False, learned=True, confidence=0.5, title=None):
    return TopicState(section_id=section_id, title=title or f"Topic {section_id}", position=position,
                      knowledge=knowledge, confidence=confidence, known=known, learned=learned)


class AutopilotTests(unittest.TestCase):
    def test_phases(self):
        self.assertEqual(autopilot.phase(10, 14), "learning")
        self.assertEqual(autopilot.phase(3, 14), "final")
        self.assertEqual(autopilot.phase(2, 4, ), "final", "the last two days are always final")
        self.assertEqual(autopilot.phase(0, 14), "final")

    def test_topic_one_is_finished_before_topic_two_is_opened(self):
        topics = [topic(1, 1, 60, learned=True), topic(2, 2, 0, learned=False)]
        action = autopilot.next_action(topics, days_left=12, total_days=14)
        self.assertEqual((action.kind, action.section_id, action.reason), ("practice", 1, "finish_topic"))
        topics = [topic(1, 1, 90, known=True), topic(2, 2, 0, learned=False)]
        action = autopilot.next_action(topics, days_left=12, total_days=14)
        self.assertEqual((action.kind, action.section_id, action.reason), ("learn", 2, "next_topic"))

    def test_older_weak_material_is_revisited_when_enough_reviews_are_due(self):
        topics = [topic(1, 1, 90, known=True), topic(2, 2, 40, learned=True)]
        action = autopilot.next_action(topics, days_left=12, total_days=14, due_reviews=3)
        self.assertEqual(action.kind, "review")
        action = autopilot.next_action(topics, days_left=12, total_days=14, due_reviews=1)
        self.assertEqual(action.kind, "practice", "one due review does not interrupt the main line")
        topics = [topic(1, 1, 0, learned=False)]
        action = autopilot.next_action(topics, days_left=12, total_days=14, due_reviews=5)
        self.assertEqual(action.kind, "learn", "a topic that was never taught comes first")

    def test_final_days_are_retrieval_and_mock_exams_not_new_material(self):
        topics = [topic(1, 1, 90, known=True), topic(2, 2, 55, learned=True), topic(3, 3, 0, learned=False)]
        review = autopilot.next_action(topics, days_left=2, total_days=14, due_reviews=2)
        self.assertEqual(review.kind, "review")
        mock = autopilot.next_action(topics, days_left=2, total_days=14, due_reviews=0, days_since_mock=None)
        self.assertEqual(mock.kind, "mock_exam")
        practice = autopilot.next_action(topics, days_left=2, total_days=14, due_reviews=0, days_since_mock=1)
        self.assertEqual((practice.kind, practice.section_id), ("practice", 2), "the weakest learned topic")

    def test_when_everything_is_known_the_mock_exam_is_next_then_the_least_certain_topic(self):
        topics = [topic(1, 1, 90, known=True, confidence=0.9), topic(2, 2, 85, known=True, confidence=0.6)]
        self.assertEqual(autopilot.next_action(topics, days_left=10, total_days=14).kind, "mock_exam")
        warm = autopilot.next_action(topics, days_left=10, total_days=14, days_since_mock=1, mock_count=1)
        self.assertEqual((warm.kind, warm.section_id, warm.reason), ("practice", 2, "keep_warm"))

    def test_the_summary_names_progress_and_the_weakness(self):
        topics = [topic(1, 1, 90, known=True), topic(2, 2, 40, learned=True)]
        action = autopilot.next_action(topics, days_left=12, total_days=14)
        summary = autopilot.plan_summary(topics, action, days_left=12, total_days=14)
        self.assertEqual((summary["progress_percent"], summary["topics_known"], summary["topics_total"]), (65, 1, 2))
        self.assertEqual(summary["weakness"], "Topic 2")
        self.assertEqual(summary["sequence"], ["practice", "diagnosis", "mastery_check"])
        self.assertEqual(autopilot.next_action([], days_left=5, total_days=10).kind, "done")


if __name__ == "__main__":
    unittest.main()

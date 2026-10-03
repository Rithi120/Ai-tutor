"""Exercises built on the student's own scanned diagrams.

Two new question types: writing about a picture, and sorting pictures into order. The
pictures are diagram regions cropped out of pages the student photographed themselves,
so the match is certain by construction - it is their own textbook page, not something
searched for and hoped to be right.

Everything that could go wrong here is about *which* picture: a model naming a block it
was never offered, a block belonging to another student, or a photo question with no
picture at all, which would ask about something invisible.
"""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_photo_ex_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from learnova.ai_services.contracts import (  # noqa: E402
    PHOTO_QUESTION_TYPES, QUESTION_TYPES, AIValidationError, _question,
)
from learnova.diagnostics import schema as diagnostics_schema  # noqa: E402
from learnova.diagnostics.question_spec import (  # noqa: E402
    ALL_TYPES, QuestionSpec, validate_question,
)
from learnova.projects import media as project_media  # noqa: E402
from learnova.projects.planning import (  # noqa: E402
    ALLOWED_QUESTION_TYPES, deterministic_question_score,
)


def block(block_id, page_id=1, content="nucleus; membrane", **overrides):
    data = {"id": block_id, "page_id": page_id, "block_type": "diagram",
            "content": content, "crossed_out": False, "confidence_status": "high"}
    data.update(overrides)
    return data


class OfferedPictureTests(unittest.TestCase):
    """Only diagrams worth building an exercise on are offered to the question writer."""

    def test_diagrams_from_the_students_pages_are_offered(self):
        offered = project_media.offer_pictures([block(7), block(9, page_id=2)])
        self.assertEqual([item.block_id for item in offered], [7, 9])
        self.assertEqual(offered[0].labels, "nucleus; membrane")

    def test_text_blocks_are_not_pictures(self):
        self.assertEqual(project_media.offer_pictures([
            block(1, block_type="printed_text"), block(2, block_type="handwriting")]), [])

    def test_an_unreadable_or_crossed_out_diagram_is_not_offered(self):
        # An exercise about a smudge the recogniser could not read is worse than none.
        self.assertEqual(project_media.offer_pictures([
            block(1, confidence_status="unclear"), block(2, crossed_out=True)]), [])

    def test_labels_are_tidied_and_capped(self):
        offered = project_media.offer_pictures([block(1, content="  a\n\n b   c  " + "x" * 300)])
        self.assertTrue(offered[0].labels.startswith("a b c"))
        self.assertLessEqual(len(offered[0].labels), project_media.MAX_LABEL_LENGTH)

    def test_the_offer_is_bounded(self):
        many = [block(n) for n in range(1, 40)]
        self.assertEqual(len(project_media.offer_pictures(many)), project_media.MAX_OFFERED)

    def test_garbage_yields_nothing(self):
        for payload in (None, 42, "blocks", [None, 7], [{"id": "x"}]):
            self.assertEqual(project_media.offer_pictures(payload), [])


class VerifyMediaTests(unittest.TestCase):
    """The whole correctness story: a model can only use what it was offered."""

    def setUp(self):
        self.offered = project_media.offer_pictures([block(7), block(9, page_id=2)])

    def test_an_offered_picture_is_kept(self):
        verified = project_media.verify_media([7], self.offered)
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["block_id"], 7)
        self.assertEqual(verified[0]["page_id"], 1)

    def test_an_id_that_was_never_offered_is_dropped(self):
        # 999 might be a hallucination, a memory of another session, or another
        # student's project. It is not on the list, so it does not exist here.
        self.assertEqual(project_media.verify_media([999], self.offered), [])
        self.assertEqual(project_media.verify_media([7, 999], self.offered)[0]["block_id"], 7)

    def test_nothing_is_accepted_when_nothing_was_offered(self):
        self.assertEqual(project_media.verify_media([7, 9], []), [])

    def test_objects_strings_and_bare_numbers_all_work(self):
        for value in (7, "7", [7], [{"block_id": 7}], [{"id": 7}]):
            self.assertEqual(project_media.verify_media(value, self.offered)[0]["block_id"], 7)

    def test_duplicates_and_the_cap_are_applied(self):
        self.assertEqual(len(project_media.verify_media([7, 7, 9], self.offered)), 2)
        offered = project_media.offer_pictures([block(n) for n in range(1, 9)])
        self.assertEqual(len(project_media.verify_media(list(range(1, 9)), offered)),
                         project_media.MAX_PER_QUESTION)

    def test_garbage_yields_nothing(self):
        for payload in (None, {}, [None], ["abc"], [[]]):
            self.assertEqual(project_media.verify_media(payload, self.offered), [])

    def test_the_url_is_built_by_the_app_never_supplied(self):
        verified = project_media.verify_media([7], self.offered)
        with_urls = project_media.media_urls(verified, project_id=42)
        self.assertEqual(with_urls[0]["url"], "/projects/42/blocks/7/region")


class UsableQuestionTests(unittest.TestCase):
    def test_a_photo_question_needs_its_pictures(self):
        self.assertFalse(project_media.photo_question_is_usable(
            {"type": "photo_response", "media": []}))
        # One tile is not a sorting exercise.
        self.assertFalse(project_media.photo_question_is_usable(
            {"type": "photo_ordering", "media": [{"block_id": 1}]}))

    def test_enough_pictures_makes_it_usable(self):
        self.assertTrue(project_media.photo_question_is_usable(
            {"type": "photo_response", "media": [{"block_id": 1}]}))
        self.assertTrue(project_media.photo_question_is_usable(
            {"type": "photo_ordering", "media": [{"block_id": 1}, {"block_id": 2}]}))

    def test_ordinary_questions_are_unaffected(self):
        self.assertTrue(project_media.photo_question_is_usable({"type": "text"}))


class QuestionValidationTests(unittest.TestCase):
    """The self-contained rule must bend for a picture, and only for a real one."""

    def spec(self, question_type):
        return QuestionSpec(learning_objective="name the parts of a cell",
                            concept="cell structure", difficulty=2,
                            question_type=question_type)

    def question(self, question_type, prompt, media=()):
        return {"id": "q1", "subject": "Biology", "concept": "cell structure",
                "difficulty": 2, "type": question_type, "prompt": prompt,
                "hint": "look at the shapes", "expected_answer": "the nucleus",
                "options": [], "media": list(media)}

    def test_referring_to_a_picture_is_refused_when_there_is_none(self):
        result = validate_question(
            self.question("photo_response", "Refer to the image and name the part shown."),
            self.spec("photo_response"))
        self.assertFalse(result.valid)

    def test_referring_to_a_picture_is_fine_when_one_is_attached(self):
        result = validate_question(
            self.question("photo_response", "Refer to the image and name the part shown.",
                          media=[{"block_id": 7, "url": "/projects/1/blocks/7/region"}]),
            self.spec("photo_response"))
        self.assertTrue(result.valid, result.failures)

    def test_a_photo_question_with_no_picture_fails_even_without_a_giveaway_phrase(self):
        # "Put these stages in order" contains no dangling phrase, so this needs its
        # own check or it reaches the student as a question about nothing.
        result = validate_question(
            self.question("photo_ordering", "Put these four stages into the correct order."),
            self.spec("photo_ordering"))
        self.assertFalse(result.valid)
        self.assertTrue(any("picture" in failure for failure in result.failures))

    def test_ordinary_questions_still_have_to_stand_alone(self):
        result = validate_question(
            self.question("text", "Explain the process as shown, in your own words."),
            self.spec("text"))
        self.assertFalse(result.valid)


class ContractTests(unittest.TestCase):
    def test_the_new_types_are_accepted_by_the_shared_validator(self):
        for question_type in sorted(PHOTO_QUESTION_TYPES):
            _question({"id": "q1", "prompt": "What does this show?", "concept": "cells",
                       "difficulty": "medium", "type": question_type,
                       "expected_answer": "the nucleus",
                       "options": [{"id": "a", "label": "x"}]})

    def test_an_unknown_type_is_still_refused(self):
        with self.assertRaises(AIValidationError):
            _question({"id": "q1", "prompt": "p", "concept": "c", "difficulty": "easy",
                       "type": "photo_interpretive_dance", "expected_answer": "x"})


class TypeListsAgreeTests(unittest.TestCase):
    """Four copies of the question-type universe, and nothing checked they matched."""

    def test_the_contract_and_diagnostics_lists_are_identical(self):
        self.assertEqual(QUESTION_TYPES, set(diagnostics_schema.QUESTION_TYPES))
        self.assertEqual(QUESTION_TYPES, ALL_TYPES)

    def test_the_exam_list_is_a_subset_of_the_contract(self):
        # Exams may allow fewer types, never a type the contract would reject.
        self.assertTrue(ALLOWED_QUESTION_TYPES <= QUESTION_TYPES)

    def test_dragging_is_not_offered_on_a_paper_exam(self):
        # The exam renderer has radios and a textarea only; there is no ordering control.
        self.assertIn("photo_response", ALLOWED_QUESTION_TYPES)
        self.assertNotIn("photo_ordering", ALLOWED_QUESTION_TYPES)
        self.assertNotIn("ordering", ALLOWED_QUESTION_TYPES)


class OrderingIsGradedWithoutTheAiTests(unittest.TestCase):
    """Putting things in order has one right answer, so no model needs to mark it."""

    def test_the_right_order_scores_full_marks(self):
        self.assertEqual(deterministic_question_score(
            "photo_ordering", ["7", "9", "3"], ["7", "9", "3"]), 100.0)

    def test_order_matters(self):
        self.assertEqual(deterministic_question_score(
            "photo_ordering", ["7", "9", "3"], ["9", "7", "3"]), 0.0)

    def test_the_json_list_the_browser_sends_is_understood(self):
        # app.py json.dumps a list answer before evaluating it.
        self.assertEqual(deterministic_question_score(
            "ordering", ["a", "b", "c"], '["a", "b", "c"]'), 100.0)

    def test_a_comma_separated_answer_is_understood(self):
        self.assertEqual(deterministic_question_score("ordering", "a,b,c", "a, b, c"), 100.0)

    def test_an_empty_answer_scores_nothing(self):
        self.assertEqual(deterministic_question_score("ordering", ["a"], ""), 0.0)

    def test_written_photo_answers_still_go_to_the_evaluator(self):
        # None means "no deterministic rule"; the caller then asks the AI.
        self.assertIsNone(deterministic_question_score("photo_response", "the nucleus", "nucleus"))


class FrontendWiringTests(unittest.TestCase):
    """What the browser has to do, asserted from source: there is no JS test runner."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent.parent
        cls.app_js = (root / "static/app.js").read_text(encoding="utf-8")
        cls.index = (root / "templates/index.html").read_text(encoding="utf-8")

    def test_the_question_card_has_somewhere_to_put_a_picture(self):
        self.assertIn('id="questionMedia"', self.index)

    def test_photo_ordering_reuses_the_existing_ordering_control(self):
        # Drag, plus the arrow buttons that make it work on touch and by keyboard.
        self.assertIn('question.type === "photo_ordering"', self.app_js)
        self.assertIn('id="orderingList"', self.app_js)
        self.assertIn('data-move="up"', self.app_js)

    def test_a_photo_ordering_answer_is_collected_as_an_order(self):
        self.assertIn('currentQuestion.type === "photo_ordering"', self.app_js)

    def test_picture_urls_come_from_the_server(self):
        # The browser renders item.url; it never builds one from a block id.
        self.assertIn("item.url", self.app_js)
        self.assertNotIn("/blocks/${", self.app_js)


if __name__ == "__main__":
    unittest.main()

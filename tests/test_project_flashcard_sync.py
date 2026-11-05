"""The project's key-terms flashcard set (track LESSON-BE): app.sync_project_flashcards.

One set per project, filled deterministically from the topic maps (no AI call), add-only:
a re-sync adds nothing, a card the student edited or deleted stays as the student left
it, a front the set already has is not added twice, two formulas stay two cards, every
card names its source page, and an old project without topic maps still gets cards from
its old definition columns. In the study modes a key-terms card asked back to front
expects the bare term, and gap cards are only ever asked one way. The sync runs after
every topic analysis and can never cost the student the analysis.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_project_flashcard_sync_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from flask_login import login_user  # noqa: E402

import app as application  # noqa: E402
from learnova.ai_services import service as ai_service  # noqa: E402
from learnova.flashcards import modes  # noqa: E402
from learnova.projects.terms import ADDED_MARKER, ONE_WAY, origin_identity, term_key  # noqa: E402

db = application.db
_MISSING = object()
PASSWORD = "correct-horse-battery"
DUTY = "Pflicht ist die Notwendigkeit einer Handlung aus Achtung vor dem Gesetz."
MAXIM = "Eine Maxime ist ein subjektiver Grundsatz des Wollens."
PAGE_ONE = f"Pflichtethik nach Kant. {DUTY} {MAXIM} Kant war ein Philosoph der Aufklärung."
PAGE_TWO = "Utilitarismus. Nutzen ist das Glück aller Betroffenen. Bentham begründete den Utilitarismus."


class FakeResponse:
    usage = None
    model = "test-model"

    def __init__(self, payload):
        self.output_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)


def term(name, definition, page_id, *, quote="", simple=""):
    return {"term": name, "definition": definition, "simple_definition": simple, "example": "",
            "page_id": page_id, "page_ids": [page_id], "quote": quote, "in_notes": True,
            "definition_from_notes": bool(quote), "key": term_key(name)}


def item(kind, name, detail, page_id):
    return {"kind": kind, "name": name, "detail": detail, "page_id": page_id, "in_notes": True}


class SyncCase(unittest.TestCase):
    maxDiff = None

    CONFIG_KEYS = ("TESTING", "AI_MODE", "CURRICULUM_CLEARED_SOURCES", "SUBSCRIPTION_QUOTAS_ENFORCED",
                   "RATELIMIT_ENABLED", "FEATURE_PRIVATE_FLASHCARDS", "FEATURE_ADAPTIVE_DIAGNOSTICS",
                   "FEATURE_DIAGNOSTIC_VERIFICATION", "TEST_MIN_QUESTIONS", "TEST_MAX_QUESTIONS", "KNOWLEDGE_TARGET")

    def tearDown(self):
        # Later test modules in the same process expect the app's own settings.
        for key, value in self.saved_config.items():
            if value is _MISSING:
                application.app.config.pop(key, None)
            else:
                application.app.config[key] = value
        application.limiter.enabled = self.saved_limiter

    def setUp(self):
        self.saved_config = {key: application.app.config.get(key, _MISSING) for key in self.CONFIG_KEYS}
        self.saved_limiter = application.limiter.enabled
        application.app.config.update(TESTING=True, AI_MODE="cached", CURRICULUM_CLEARED_SOURCES="",
                                      SUBSCRIPTION_QUOTAS_ENFORCED=False, RATELIMIT_ENABLED=False,
                                      FEATURE_PRIVATE_FLASHCARDS=True)
        application.limiter.enabled = False
        application.SESSIONS.clear()
        ai_service._RECENT_RESULTS.clear()
        with application.app.app_context():
            db.drop_all()
            db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "lea", "email": "lea@example.com", "password": PASSWORD,
                                            "grade": "10", "language": "en"})
        with application.app.app_context():
            user = db.session.scalar(db.select(application.User).where(application.User.username == "lea"))
            assert user is not None
            self.user_id = user.id

    def make_project(self, subject="Ethik", pages=(("Ethik-Blatt.pdf", PAGE_ONE), ("Ethik-Blatt-2.pdf", PAGE_TWO)),
                     topics=None, legacy=None):
        """topics(page_ids) -> [(title, topic map, [page index, ...])]; legacy: old columns."""

        with application.app.app_context():
            project = application.LearningProject(user_id=self.user_id, title="Ethik Klausur", subject=subject,
                                                  status="planned", topics_confirmed=True)
            db.session.add(project)
            db.session.flush()
            page_ids = []
            for order, (filename, text) in enumerate(pages, 1):
                upload = application.ProjectFile(project_id=project.id, original_filename=filename,
                                                 mime_type="application/pdf", original_data=b"%PDF", sha256=filename)
                db.session.add(upload)
                db.session.flush()
                page = application.ProjectPage(project_id=project.id, file_id=upload.id, page_number=1,
                                               page_order=order, extracted_text=text, extraction_status="ready",
                                               confidence_status="high", review_status="confirmed")
                db.session.add(page)
                db.session.flush()
                page_ids.append(page.id)
            for position, (title, topic_map, indexes) in enumerate((topics or (lambda ids: []))(page_ids), 1):
                db.session.add(application.LearningSection(
                    project_id=project.id, position=position, title=title, main_topic=title,
                    source_page_ids_json=json.dumps([page_ids[index] for index in indexes]),
                    topic_map_json=json.dumps(topic_map), topic_key=re.sub(r"\W+", "-", title.casefold())))
            if legacy:
                db.session.add(application.LearningSection(
                    project_id=project.id, position=9, title="Alt", main_topic="Alt",
                    source_page_ids_json=json.dumps([page_ids[0]]), topic_key="alt",
                    definitions_json=json.dumps(legacy)))
            db.session.commit()
            self.page_ids = page_ids
            return project.id

    def sync(self, project_id):
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            summary = application.sync_project_flashcards(project)
            db.session.commit()
            return summary

    def cards(self, project_id):
        with application.app.app_context():
            sets = db.session.scalars(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id)).all()
            self.assertLessEqual(len(sets), 1, "one set per project")
            if not sets:
                return []
            return [SimpleNamespace(**{column.name: getattr(card, column.name) for column in card.__table__.columns})
                    for card in sorted(sets[0].cards, key=lambda card: card.position)]

    def by_identity(self, project_id):
        return {origin_identity(card.origin_key): card for card in self.cards(project_id) if card.origin_key}


def kant_topics(ids):
    return [
        ("Pflichtethik", {"summary": "Kant.", "terms": [term("Pflicht", "die Notwendigkeit einer Handlung aus Achtung "
                                                                    "vor dem Gesetz", ids[0], quote=DUTY),
                                                       term("Maxime", "ein subjektiver Grundsatz des Wollens", ids[0],
                                                            quote=MAXIM)],
                          "items": [item("person", "Kant", "Philosoph der Aufklärung", ids[0]),
                                    item("theory", "Pflichtethik", "Moral nur aus reiner Freude am Tanzen", ids[0])],
                          "exam_questions": []}, [0]),
        ("Utilitarismus", {"summary": "Bentham.", "terms": [term("Nutzen", "das Glück aller Betroffenen", ids[1])],
                           "items": [], "exam_questions": []}, [1]),
    ]


class SyncTests(SyncCase):
    def test_one_set_per_project_with_source_pages(self):
        project_id = self.make_project(topics=kant_topics)
        summary = self.sync(project_id)
        self.assertGreater(summary["added"], 0)
        with application.app.app_context():
            flashcard_set = db.session.scalar(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id))
            assert flashcard_set is not None
            self.assertEqual(flashcard_set.title, "Ethik Klausur – key terms")
            self.assertEqual((flashcard_set.source_kind, flashcard_set.source_reference, flashcard_set.subject),
                             ("project", f"project:{project_id}", "Ethik"))
            self.assertEqual(flashcard_set.user_id, self.user_id)
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            analysis = json.loads(project.analysis_json)
        cards = self.by_identity(project_id)
        duty = cards[f"term:{term_key('Pflicht')}"]
        self.assertEqual(duty.front, "What does the term “Pflicht” mean?")
        self.assertEqual(duty.back, "die Notwendigkeit einer Handlung aus Achtung vor dem Gesetz")
        self.assertEqual(duty.reverse_answer, "Pflicht")
        self.assertEqual(duty.source_reference, "Ethik-Blatt.pdf p. 1")
        self.assertEqual(json.loads(duty.source_page_ids_json), [self.page_ids[0]])
        self.assertEqual(duty.topic_key, "pflichtethik")
        benefit = cards[f"term:{term_key('Nutzen')}"]
        self.assertEqual(benefit.source_reference, "Ethik-Blatt-2.pdf p. 1")
        self.assertEqual(set(analysis["flashcard_keys"]), {card.origin_key for card in self.cards(project_id)})
        self.assertEqual(analysis["flashcard_set_id"], summary["set_id"])
        # Ethics cards: the person and the theory from the notes are cards too.
        self.assertIn(f"person:{term_key('Kant')}", cards)

    def test_an_item_detail_the_notes_do_not_contain_is_marked_as_added(self):
        project_id = self.make_project(topics=kant_topics)
        self.sync(project_id)
        cards = self.by_identity(project_id)
        self.assertIn(ADDED_MARKER, cards[f"theory:{term_key('Pflichtethik')}"].explanation)
        self.assertNotIn(ADDED_MARKER, cards[f"person:{term_key('Kant')}"].explanation)

    def test_a_resync_adds_nothing_and_keeps_the_students_edits_and_deletions(self):
        project_id = self.make_project(topics=kant_topics)
        self.sync(project_id)
        before = self.cards(project_id)
        with application.app.app_context():
            duty = db.session.scalar(db.select(application.Flashcard).where(
                application.Flashcard.origin_key.like(f"%:term:{term_key('Pflicht')}")))
            maxim = db.session.scalar(db.select(application.Flashcard).where(
                application.Flashcard.origin_key.like(f"%:term:{term_key('Maxime')}")))
            assert duty is not None and maxim is not None
            duty.back = "Was ich tun muss (meine Worte)"
            duty.repetition_count, duty.interval = 3, 6
            db.session.delete(maxim)
            db.session.commit()
        again = self.sync(project_id)
        self.assertEqual(again["added"], 0)
        after = self.by_identity(project_id)
        self.assertEqual(len(after), len(before) - 1, "the deleted card is not brought back")
        self.assertNotIn(f"term:{term_key('Maxime')}", after)
        duty = after[f"term:{term_key('Pflicht')}"]
        self.assertEqual((duty.back, duty.repetition_count, duty.interval), ("Was ich tun muss (meine Worte)", 3, 6))

    def test_a_front_the_set_already_has_is_not_added_twice(self):
        project_id = self.make_project(topics=kant_topics)
        with application.app.app_context():
            own = application.FlashcardSet(user_id=self.user_id, project_id=project_id, title="Meine Karten",
                                           source_kind="project", source_reference=f"project:{project_id}")
            own.cards.append(application.Flashcard(position=0, front="What does the term “Pflicht” mean?",
                                                   back="Meine eigene Definition"))
            db.session.add(own)
            db.session.commit()
        summary = self.sync(project_id)
        self.assertGreaterEqual(summary["front_exists"], 1)
        fronts = [card.front for card in self.cards(project_id)]
        self.assertEqual(fronts.count("What does the term “Pflicht” mean?"), 1)
        self.assertEqual(self.cards(project_id)[0].back, "Meine eigene Definition")
        with application.app.app_context():
            project = db.session.get(application.LearningProject, project_id)
            assert project is not None
            self.assertGreaterEqual(json.loads(project.analysis_json)["flashcard_sync"]["front_exists"], 1)

    def test_two_formulas_stay_two_cards(self):
        first, second = "(a+b)^2 = a^2 + 2ab + b^2", "(a-b)^2 = a^2 - 2ab + b^2"
        text = f"Binomische Formeln. Erste: {first}. Zweite: {second}."
        project_id = self.make_project(subject="Mathematik", pages=(("Mathe.pdf", text),), topics=lambda ids: [
            ("Binome", {"terms": [], "items": [item("formula", first, "erste binomische Formel", ids[0]),
                                               item("formula", second, "zweite binomische Formel", ids[0])],
                        "exam_questions": []}, [0])])
        self.sync(project_id)
        formulas = [card for card in self.cards(project_id) if card.type == "formula_explanation"]
        self.assertEqual(len(formulas), 2)
        self.assertEqual({card.reverse_answer for card in formulas}, {first, second})

    def test_an_old_project_without_topic_maps_gets_cards_from_its_old_definitions(self):
        project_id = self.make_project(legacy=[f"Pflicht: {DUTY}", {"term": "Maxime", "definition": MAXIM},
                                               "Gerechtigkeit: kommt auf der Seite gar nicht vor"])
        self.sync(project_id)
        identities = set(self.by_identity(project_id))
        self.assertIn(f"term:{term_key('Pflicht')}", identities)
        self.assertIn(f"term:{term_key('Maxime')}", identities)
        self.assertNotIn(f"term:{term_key('Gerechtigkeit')}", identities, "a term not on the page gets no card")

    def test_excluded_topics_and_a_switched_off_feature_make_no_cards(self):
        project_id = self.make_project(topics=kant_topics)
        application.app.config["FEATURE_PRIVATE_FLASHCARDS"] = False
        try:
            self.assertEqual(self.sync(project_id)["added"], 0)
            self.assertEqual(self.cards(project_id), [])
        finally:
            application.app.config["FEATURE_PRIVATE_FLASHCARDS"] = True
        with application.app.app_context():
            for section in db.session.scalars(db.select(application.LearningSection)):
                section.excluded = True
            db.session.commit()
        self.sync(project_id)
        self.assertEqual(self.cards(project_id), [], "no set is created without cards")

    def test_a_set_the_student_deleted_is_not_recreated_empty(self):
        # Review LESSONBEr: deleting the whole set deletes every synced card; the next sync
        # used to create a new, empty "<project> – key terms" set in the student's library.
        project_id = self.make_project(topics=kant_topics)
        self.sync(project_id)
        with application.app.app_context():
            flashcard_set = db.session.scalar(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id))
            assert flashcard_set is not None
            response_set_id = flashcard_set.id
        deleted = self.client.delete(f"/api/flashcards/sets/{response_set_id}")
        self.assertIn(deleted.status_code, (200, 204), deleted.get_data(as_text=True)[:300])
        summary = self.sync(project_id)
        self.assertEqual(summary["added"], 0)
        with application.app.app_context():
            sets = db.session.scalars(db.select(application.FlashcardSet).where(
                application.FlashcardSet.user_id == self.user_id)).all()
        self.assertEqual(sets, [], "no empty ghost set after the student deleted it")


class StudyDirectionTests(SyncCase):
    def test_reverse_study_asks_for_the_bare_term_and_gap_cards_go_one_way(self):
        project_id = self.make_project(topics=kant_topics)
        self.sync(project_id)
        cards = self.cards(project_id)
        terms = [card for card in cards if origin_identity(card.origin_key).startswith("term:")]
        gaps = [card for card in cards if origin_identity(card.origin_key).startswith("term_fill:")]
        self.assertTrue(gaps, "a verified quote makes a gap card")
        self.assertTrue(all(ONE_WAY in json.loads(card.tags_json) for card in gaps))
        reverse = modes.build_items(terms, "test", seed=1, count=len(terms), direction="back_to_front",
                                    question_types=["written"])
        expected = {card.id: card.reverse_answer for card in terms}
        for entry in reverse:
            self.assertEqual(entry["direction"], "back_to_front")
            self.assertEqual(entry["correct_answer"], expected[entry["card_id"]])
            self.assertNotIn("What does the term", entry["correct_answer"])
        choice = modes.build_items(terms, "learn", seed=3, count=len(terms), direction="back_to_front")
        for entry in choice:
            for option in entry["options"]:
                self.assertNotIn("What does the term", option, "options are bare terms too")
        gap_items = modes.build_items(gaps, "test", seed=2, count=len(gaps), direction="back_to_front")
        self.assertTrue(all(entry["direction"] == "front_to_back" for entry in gap_items))
        matched = modes.build_items(terms, "match", seed=4, count=len(terms), direction="front_to_back")
        self.assertEqual({entry["prompt"] for entry in matched}, {card.reverse_answer for card in terms})


class FixTwoStudyModeTests(SyncCase):
    """Own-words cards, match tiles, choice options and duplicated sets (P3-02/03/04/07, v)."""

    def project_cards(self):
        project_id = self.make_project(topics=kant_topics)
        self.sync(project_id)
        return project_id, self.cards(project_id)

    def test_own_words_cards_are_self_graded_and_left_out_where_one_exact_answer_is_needed(self):
        _, cards = self.project_cards()
        own = {card.id for card in cards if origin_identity(card.origin_key).startswith("term_own_words:")}
        self.assertTrue(own, "definitions from the notes make own-words cards")
        learn = modes.build_items(cards, "learn", seed=1, count=len(cards), direction="front_to_back")
        self.assertEqual({entry["question_type"] for entry in learn if entry["card_id"] in own}, {"self_grade"},
                         "a correct paraphrase is never graded wrong by an exact match")
        for mode in ("test", "match", "blast", "blocks"):
            with self.subTest(mode=mode):
                items = modes.build_items(cards, mode, seed=1, count=len(cards))
                self.assertFalse(own & {entry["card_id"] for entry in items})
        # Cards synced before the tag existed are recognised by their origin key.
        untagged = [SimpleNamespace(**{**vars(card), "tags_json": json.dumps([ONE_WAY])}) for card in cards]
        self.assertTrue(all(modes.own_words(card) for card in untagged if card.id in own))

    def test_match_tiles_are_all_different(self):
        _, cards = self.project_cards()
        items = modes.build_items(cards, "match", seed=3, count=len(cards))
        prompts = [modes.normalize_answer(entry["prompt"]) for entry in items]
        answers = [modes.normalize_answer(entry["correct_answer"]) for entry in items]
        self.assertEqual(len(prompts), len(set(prompts)), prompts)
        self.assertEqual(len(answers), len(set(answers)), answers)
        self.assertFalse(set(prompts) & set(answers))
        terms = {card.reverse_answer for card in cards if origin_identity(card.origin_key).startswith("term:")}
        self.assertTrue(terms <= {entry["prompt"] for entry in items}, "every term keeps its own pair")

    def test_choice_options_are_answers_of_the_same_kind(self):
        _, cards = self.project_cards()
        names = {card.reverse_answer for card in cards if card.reverse_answer}
        fresh = [SimpleNamespace(**{**vars(card), "repetition_count": 0}) for card in cards]
        items = modes.build_items(fresh, "learn", seed=5, count=len(fresh), direction="front_to_back")
        by_id = {card.id: card for card in fresh}
        for entry in items:
            card = by_id[entry["card_id"]]
            if entry["question_type"] != "multiple_choice" or modes.card_variant(card) in ("term_fill", "term_example"):
                continue
            with self.subTest(card=card.front):
                self.assertFalse(set(entry["options"]) & names, "a definition is never offered among bare terms")
                if card.type == "multiple_choice" and json.loads(card.options_json or "[]"):
                    self.assertEqual(set(entry["options"]), set(json.loads(card.options_json)),
                                     "a choice card keeps the options it was built with")
        # "Which definition fits?" asks with the definitions it was built with.
        options = ["ein Lebewesen", "eine Handlung, die man tun muss", "Regeln einer Gruppe", "was wichtig ist"]
        choice = SimpleNamespace(id=99, front="Which definition fits “Werte”?", back="was wichtig ist",
                                 type="multiple_choice", options_json=json.dumps(options), tags_json="[]",
                                 origin_key="werte:term_choice:wert", reverse_answer="Werte", repetition_count=0,
                                 weakness_score=0.0)
        entry = modes.build_items(fresh + [choice], "learn", seed=2, count=len(fresh) + 1,
                                  direction="front_to_back")
        asked = next(item for item in entry if item["card_id"] == 99)
        self.assertEqual(sorted(asked["options"]), sorted(options))

    def test_a_duplicated_key_terms_set_still_asks_for_the_bare_term_back_to_front(self):
        project_id, cards = self.project_cards()
        with application.app.app_context():
            flashcard_set = db.session.scalar(db.select(application.FlashcardSet).where(
                application.FlashcardSet.project_id == project_id))
            assert flashcard_set is not None
            set_id = flashcard_set.id
        response = self.client.post(f"/api/flashcards/sets/{set_id}/duplicate")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True)[:300])
        copy_id = response.get_json()["id"]
        with application.app.app_context():
            copy = db.session.get(application.FlashcardSet, copy_id)
            assert copy is not None
            self.assertIsNone(copy.project_id, "a copy is not the project's set and is never synced")
            copied = sorted(copy.cards, key=lambda card: card.position)
            self.assertEqual([card.reverse_answer for card in copied], [card.reverse_answer for card in cards])
            self.assertTrue(all(card.origin_key == "" for card in copied))
            reverse = modes.build_items(copied, "test", seed=1, count=len(copied), direction="back_to_front",
                                        question_types=["written"])
        self.assertFalse(any("What does the term" in entry["correct_answer"] for entry in reverse))


class ModesWithoutProjectCardsTests(unittest.TestCase):
    """A card a student made (no reverse_answer, no tag) behaves exactly as before."""

    def card(self, card_id, front, back, **extra):
        values = dict(id=card_id, front=front, back=back, tags_json="[]", repetition_count=1, weakness_score=10.0)
        values.update(extra)
        return SimpleNamespace(**values)

    def test_student_cards_are_unchanged(self):
        cards = [self.card(1, "Hund", "dog"), self.card(2, "Katze", "cat")]
        items = modes.build_items(cards, "test", seed=5, count=2, direction="back_to_front", question_types=["written"])
        self.assertEqual({entry["correct_answer"] for entry in items}, {"Hund", "Katze"})
        matched = modes.build_items(cards, "match", seed=5, count=2, direction="front_to_back")
        self.assertEqual({entry["prompt"] for entry in matched}, {"Hund", "Katze"})
        mixed = modes.build_items(cards, "flashcards", seed=5, count=2)
        self.assertEqual(sorted(entry["direction"] for entry in mixed), ["back_to_front", "front_to_back"])


class AnalysisSyncTests(SyncCase):
    def respond(self, *, task_type, **kwargs):
        self.assertEqual(task_type, "project_section_generation")
        page_ids = [int(value) for value in re.findall(r'"page_id": (\d+)', str(kwargs.get("input", "")))]
        topics = []
        for page_id in dict.fromkeys(page_ids):
            first = page_id == self.page_ids[0]
            topics.append({
                "title": "Pflichtethik" if first else "Utilitarismus", "main_topic": "Ethik", "kind": "main",
                "summary": "Notizen.", "simple_summary": "Kurz.", "learning_goals": ["Erklären"],
                "source_page_ids": [page_id],
                "terms": [{"term": "Pflicht", "definition": "die Notwendigkeit einer Handlung aus Achtung vor dem "
                                                            "Gesetz", "simple_definition": "", "example": "",
                           "page_id": page_id, "quote": DUTY}] if first else
                [{"term": "Nutzen", "definition": "das Glück aller Betroffenen", "simple_definition": "",
                  "example": "", "page_id": page_id, "quote": "Nutzen ist das Glück aller Betroffenen."}],
                "items": [], "exam_questions": [], "related_topics": [], "prerequisites": [],
                "difficulty": "medium", "importance": "medium", "estimated_minutes": 15})
        return FakeResponse({"sections": topics})

    def analyse(self, project_id):
        with application.app.test_request_context():
            login_user(db.session.get(application.User, self.user_id))
            project = db.session.get(application.LearningProject, project_id)
            with patch.object(application, "create_response", side_effect=self.respond):
                application._plan_project_sections(project)

    def test_the_analysis_syncs_the_cards_and_a_failing_sync_never_costs_the_topics(self):
        project_id = self.make_project()
        self.analyse(project_id)
        identities = set(self.by_identity(project_id))
        self.assertIn(f"term:{term_key('Pflicht')}", identities)
        self.assertIn(f"term:{term_key('Nutzen')}", identities)
        count = len(identities)
        self.analyse(project_id)
        self.assertEqual(len(self.by_identity(project_id)), count, "a second analysis adds nothing")

        other = self.make_project()
        with patch.object(application, "sync_project_flashcards", side_effect=RuntimeError("disk full")):
            self.analyse(other)
        with application.app.app_context():
            sections = db.session.scalars(db.select(application.LearningSection).where(
                application.LearningSection.project_id == other)).all()
        self.assertEqual(len(sections), 2, "the topics are saved although the cards failed")
        self.assertEqual(self.cards(other), [])


if __name__ == "__main__":
    unittest.main()

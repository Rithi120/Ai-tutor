"""Community flashcard filters (COMMUNITY_FLASHCARD_FILTER_FIX.md): every filter, their
combinations with search and sorting, counts and pages - against the real API on a
throwaway SQLite file."""

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_community_filters_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402

db = application.db

# title, subject, grade, difficulty, language, kind, topic, tags, description, ai, rank, studied, saved, age_days
LIBRARY = [
    ("Irregular verbs", "English", "9", "easy", "en", "vocabulary", "Verbs", ["grammar"], "Past tense forms", 4.6, 90, 40, 5, 1),
    ("Present perfect drill", "English", "Klasse 9", "easy", "English", "flashcards", "Tenses", ["grammar"], "", 3.4, 60, 10, 30, 3),
    ("Vocabulary unit 4", "Englisch", "9. Klasse", "medium", "en", "vocabulary", "School life", [], "", 4.1, 70, 5, 2, 5),
    ("Reading strategies", "English", "8", "hard", "en", "flashcards", "Reading", [], "Skimming and scanning", 3.9, 40, 2, 1, 2),
    ("Brüche kürzen", "Mathe", "6", "easy", "German", "flashcards", "Bruchrechnung", ["brüche"], "", 4.8, 95, 99, 3, 9),
    ("Lineare Funktionen", "Mathematics", "Klasse 8", "medium", "de", "flashcards", "Funktionen", ["steigung"],
     "Steigung und y-Achsenabschnitt", 4.0, 80, 20, 50, 4),
    ("Zinsrechnung", "Mathematics", "8", "hard", "Deutsch", "flashcards", "Prozent", ["zinsen"], "Tageszinsen", 3.2, 30, 1, 0, 0),
    ("Photosynthese", "Biology", "7", "medium", "German", "flashcards", "Pflanzen", [], "Licht und Chlorophyll", 4.3, 50, 7, 8, 6),
]


class CommunityFilterCase(unittest.TestCase):
    def setUp(self):
        keep_config(self, application.app.config, "TESTING", "FEATURE_COMMUNITY_LIBRARY", "COMMUNITY_MODERATION_ENABLED")
        application.app.config.update(TESTING=True, FEATURE_COMMUNITY_LIBRARY=True)
        with application.app.app_context():
            db.drop_all()
            db.create_all()
            author = application.User(username="autor", email="autor@example.com")
            author.set_password("correct-horse-battery") if hasattr(author, "set_password") else None
            db.session.add(author)
            db.session.flush()
            now = application.utcnow()
            for (title, subject, grade, difficulty, language, kind, topic, tags, description, ai, rank, studied,
                 saved, age) in LIBRARY:
                db.session.add(application.PublicFlashcardSet(
                    creator_id=author.id, title=title, subject=subject, grade=grade, difficulty=difficulty,
                    language=language, set_kind=kind, topic=topic, tags_json=json.dumps(tags),
                    description=description, ai_overall=ai, ranking_score=rank, study_count=studied,
                    save_count=saved, status="approved", moderation_decision="allow", card_count=10,
                    author_display="nickname", nickname="Autor", cards_json="[]",
                    created_at=now - timedelta(days=age), published_at=now - timedelta(days=age)))
            # Never listed: not approved.
            db.session.add(application.PublicFlashcardSet(
                creator_id=author.id, title="Irregular verbs (pending)", subject="English", grade="9",
                difficulty="easy", language="en", status="pending_review", moderation_decision="allow",
                cards_json="[]", author_display="nickname", nickname="Autor"))
            db.session.commit()
        self.client = application.app.test_client()

    def titles(self, **params):
        response = self.client.get("/api/community/library", query_string=params)
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True)[:300])
        data = response.get_json()
        return [item["title"] for item in data["sets"]], data


class SingleFilterTests(CommunityFilterCase):
    def test_no_filter_lists_every_visible_set(self):
        titles, data = self.titles()
        self.assertEqual(data["total"], len(LIBRARY))
        self.assertNotIn("Irregular verbs (pending)", titles)

    def test_set_type(self):
        self.assertEqual(sorted(self.titles(kind="vocabulary")[0]), ["Irregular verbs", "Vocabulary unit 4"])
        self.assertEqual(self.titles(kind="flashcards")[1]["total"], 6)
        self.assertEqual(self.titles(kind="anything")[1]["total"], len(LIBRARY), "an unknown kind is ignored")

    def test_subject_matches_every_spelling_of_it(self):
        english = self.titles(subject="English")[0]
        self.assertEqual(sorted(english), ["Irregular verbs", "Present perfect drill", "Reading strategies",
                                           "Vocabulary unit 4"], "Englisch is English")
        self.assertEqual(sorted(self.titles(subject="Mathematics")[0]), ["Brüche kürzen", "Lineare Funktionen",
                                                                          "Zinsrechnung"], "Mathe is Mathematics")

    def test_grade_matches_however_it_was_typed(self):
        self.assertEqual(sorted(self.titles(grade="9")[0]), ["Irregular verbs", "Present perfect drill",
                                                             "Vocabulary unit 4"])
        self.assertEqual(sorted(self.titles(grade="Klasse 8")[0]), ["Lineare Funktionen", "Reading strategies",
                                                                    "Zinsrechnung"])

    def test_difficulty(self):
        self.assertEqual(sorted(self.titles(difficulty="hard")[0]), ["Reading strategies", "Zinsrechnung"])
        self.assertEqual(self.titles(difficulty="Easy")[1]["total"], 3, "case does not matter")
        self.assertEqual(self.titles(difficulty="extreme")[1]["total"], len(LIBRARY))

    def test_language_matches_codes_and_names(self):
        # The cause of the broken filter: generated sets store "German", the publish form "de".
        self.assertEqual(sorted(self.titles(language="de")[0]), ["Brüche kürzen", "Lineare Funktionen",
                                                                 "Photosynthese", "Zinsrechnung"])
        self.assertEqual(self.titles(language="en")[1]["total"], 4)

    def test_minimum_ai_and_student_rating(self):
        self.assertEqual(sorted(self.titles(min_ai="4.5")[0]), ["Brüche kürzen", "Irregular verbs"])
        self.assertEqual(self.titles(min_ai="not-a-number")[1]["total"], len(LIBRARY))
        self.assertEqual(self.titles(min_student="4")[1]["total"], 0, "no student ratings yet")


class SearchTests(CommunityFilterCase):
    def test_title_topic_keyword_and_description_case_insensitive(self):
        self.assertEqual(self.titles(q="IRREGULAR")[0], ["Irregular verbs"])
        self.assertEqual(self.titles(q="bruchrechnung")[0], ["Brüche kürzen"], "topic")
        self.assertEqual(self.titles(q="steigung")[0], ["Lineare Funktionen"], "keyword")
        self.assertEqual(self.titles(q="chlorophyll")[0], ["Photosynthese"], "description")

    def test_whitespace_and_several_words(self):
        self.assertEqual(self.titles(q="   present    perfect  ")[0], ["Present perfect drill"])
        self.assertEqual(self.titles(q="verbs grammar")[0], ["Irregular verbs"], "every word must match")
        self.assertEqual(self.titles(q="verbs biology")[1]["total"], 0)
        self.assertEqual(self.titles(q="   ")[1]["total"], len(LIBRARY), "an empty search finds everything")

    def test_search_with_filters(self):
        self.assertEqual(self.titles(q="grammar", kind="flashcards")[0], ["Present perfect drill"])
        self.assertEqual(self.titles(q="mathe", grade="8")[1]["total"], 2, "the subject's name is searchable")


class CombinationTests(CommunityFilterCase):
    def test_subject_grade_and_difficulty_together(self):
        titles, data = self.titles(subject="English", grade="9", difficulty="easy")
        self.assertEqual(sorted(titles), ["Irregular verbs", "Present perfect drill"])
        self.assertEqual(data["total"], 2)

    def test_every_filter_at_once(self):
        titles, _ = self.titles(subject="English", grade="9", difficulty="easy", language="en", kind="vocabulary",
                                q="verbs", min_ai="4")
        self.assertEqual(titles, ["Irregular verbs"])

    def test_no_match_is_an_empty_page_with_total_zero(self):
        titles, data = self.titles(subject="Biology", difficulty="hard")
        self.assertEqual((titles, data["total"], data["has_more"]), ([], 0, False))


class SortingTests(CommunityFilterCase):
    def test_each_sort_with_a_filter(self):
        math = {"subject": "Mathematics"}
        self.assertEqual(self.titles(sort="newest", **math)[0], ["Zinsrechnung", "Lineare Funktionen", "Brüche kürzen"])
        self.assertEqual(self.titles(sort="ai", **math)[0], ["Brüche kürzen", "Lineare Funktionen", "Zinsrechnung"])
        self.assertEqual(self.titles(sort="studied", **math)[0], ["Brüche kürzen", "Lineare Funktionen", "Zinsrechnung"])
        self.assertEqual(self.titles(sort="saved", **math)[0], ["Lineare Funktionen", "Brüche kürzen", "Zinsrechnung"])
        self.assertEqual(self.titles(sort="ranking", **math)[0], ["Brüche kürzen", "Lineare Funktionen", "Zinsrechnung"])
        self.assertEqual(self.titles(sort="bogus", **math)[0], self.titles(sort="ranking", **math)[0])


class PagingTests(CommunityFilterCase):
    def test_pages_keep_the_filters_and_the_order(self):
        first, data = self.titles(limit="3", sort="ranking")
        self.assertEqual((len(first), data["total"], data["has_more"]), (3, 8, True))
        second, data2 = self.titles(limit="3", offset="3", sort="ranking")
        third, data3 = self.titles(limit="3", offset="6", sort="ranking")
        self.assertEqual(len(first + second + third), 8)
        self.assertEqual(len(set(first + second + third)), 8, "no set twice, none missing")
        self.assertFalse(data3["has_more"])
        filtered, info = self.titles(subject="English", limit="2", offset="2")
        self.assertEqual((len(filtered), info["total"], info["has_more"]), (2, 4, False))

    def test_bad_paging_values_are_bounded(self):
        for limit, offset in (("0", "0"), ("9999", "0"), ("x", "y"), ("-1", "-5"), ("3", "999999999999")):
            with self.subTest(limit=limit, offset=offset):
                response = self.client.get("/api/community/library", query_string={"limit": limit, "offset": offset})
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(len(response.get_json()["sets"]), 50)


class PageTests(CommunityFilterCase):
    def test_the_page_offers_the_real_filters(self):
        page = self.client.get("/community").get_data(as_text=True)
        for marker in ('id="commFilterToggle"', 'aria-controls="commMoreFilters"', 'id="commMoreFilters" class="community-more-filters" hidden',
                       'name="grade"', 'name="difficulty"', 'name="language"', 'name="min_ai"', 'name="min_student"',
                       'id="commReset"', 'id="commCount"', 'id="commMore"', '<option value="Mathematics">'):
            self.assertIn(marker, page)
        self.assertNotIn('name="teacher_verified"', page, "no filter that can only find nothing")


if __name__ == "__main__":
    unittest.main()

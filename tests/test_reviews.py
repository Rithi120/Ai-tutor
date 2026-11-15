"""Public reviews on the About page: eligibility, ownership, ordering, moderation, safety.

Every review here is written by the test itself; the app has no seed or fallback review.
"""

import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_reviews_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova import reviews  # noqa: E402

from tests.test_diagnostics_integration import keep_app_config  # noqa: E402

XP_FOR_LEVEL = {1: 0, 2: 100, 3: 250, 4: 450, 7: 1350}


class ReviewTestCase(unittest.TestCase):
    def setUp(self):
        keep_app_config(self, "TESTING", "WTF_CSRF_ENABLED", "PREMIUM_ADMIN_IDENTIFIERS", "COMMUNITY_MODERATORS")
        application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, PREMIUM_ADMIN_IDENTIFIERS={"boss"},
                                      COMMUNITY_MODERATORS=set())
        application.limiter.enabled = False
        self.addCleanup(setattr, application.limiter, "enabled", True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()

    def student(self, name, level=3):
        client = application.app.test_client()
        client.post("/register", data={"username": name, "email": f"{name}@example.com",
                                       "password": "correct-horse-battery", "language": "en", "grade": "9"})
        with application.app.app_context():
            db = application.db
            user = db.session.scalar(db.select(application.User).where(application.User.username == name))
            profile = db.session.get(application.UserGamificationProfile, user.id)
            if profile is None:
                profile = application.UserGamificationProfile(user_id=user.id)
                db.session.add(profile)
            profile.total_xp = XP_FOR_LEVEL[level]
            db.session.commit()
            client.user_id = user.id
        return client

    def write(self, client, rating, text="", **headers):
        return client.post("/reviews", json={"rating": rating, "review_text": text}, headers=headers)

    def rows(self):
        with application.app.app_context():
            return [(row.user_id, row.rating, row.status, row.review_text) for row in application.db.session.scalars(
                application.db.select(application.ProductReview).order_by(application.ProductReview.id))]

    def set_status(self, user_id, status, created=None):
        with application.app.app_context():
            row = application.db.session.scalar(application.db.select(application.ProductReview).where(
                application.ProductReview.user_id == user_id))
            row.status = status
            if created is not None:
                row.created_at = created
            application.db.session.commit()

    def listing(self, client=None, **params):
        response = (client or application.app.test_client()).get("/api/reviews", query_string=params)
        self.assertEqual(response.status_code, 200)
        return response.get_json()


class EligibilityTests(ReviewTestCase):
    def test_visitors_read_but_cannot_write(self):
        self.write(self.student("mia"), 5, "Hilft mir sehr!")
        visitor = application.app.test_client()
        page = visitor.get("/about").get_data(as_text=True)
        self.assertIn("Hilft mir sehr!", page)
        self.assertIn("What students think about Learnova", page)
        response = visitor.post("/reviews", json={"rating": 5})
        self.assertIn(response.status_code, (302, 401))
        self.assertEqual(len(self.rows()), 1)

    def test_levels_one_and_two_cannot_write(self):
        for level in (1, 2):
            with self.subTest(level=level):
                client = self.student(f"low{level}", level)
                response = self.write(client, 5)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.get_json()["reason"], "level")
                page = client.get("/about").get_data(as_text=True)
                self.assertIn("You can submit a review starting at Level 3.", page)
        self.assertEqual(self.rows(), [])

    def test_level_three_and_higher_can_write(self):
        for level in (3, 4, 7):
            self.assertEqual(self.write(self.student(f"good{level}", level), 4).status_code, 201)
        self.assertEqual(len(self.rows()), 3)

    def test_reaching_level_three_later_unlocks_the_form(self):
        client = self.student("grower", 2)
        self.assertEqual(self.write(client, 5).status_code, 403)
        with application.app.app_context():
            application.db.session.get(application.UserGamificationProfile, client.user_id).total_xp = 260
            application.db.session.commit()
        self.assertEqual(self.write(client, 5).status_code, 201)

    def test_simulated_rivals_have_no_way_to_write(self):
        # Rivals are league_rival rows, never accounts: no session, so the route refuses.
        anonymous = application.app.test_client()
        self.assertIn(anonymous.post("/reviews", json={"rating": 5, "user_id": -1}).status_code, (302, 401))
        # A client that names someone else's id still writes as itself.
        writer = self.student("anna")
        self.write(writer, 4, user_id=999)
        self.assertEqual(self.rows()[0][0], writer.user_id)

    def test_a_blocked_account_cannot_write(self):
        client = self.student("spammer")
        with application.app.app_context():
            application.db.session.add(application.ReviewBlock(user_id=client.user_id, reason="spam"))
            application.db.session.commit()
        self.assertEqual(self.write(client, 5).status_code, 403)


class OwnershipTests(ReviewTestCase):
    def test_one_review_per_account_and_edits_keep_the_creation_date(self):
        client = self.student("mia")
        self.assertEqual(self.write(client, 3, "Okay").status_code, 201)
        with application.app.app_context():
            first = application.db.session.scalar(application.db.select(application.ProductReview))
            created = first.created_at
        self.assertEqual(self.write(client, 5, "Jetzt super").status_code, 200)
        self.assertEqual(self.write(client, 5, "Jetzt super").status_code, 200)    # a double submit
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:], (5, "published", "Jetzt super"))
        with application.app.app_context():
            row = application.db.session.scalar(application.db.select(application.ProductReview))
            self.assertEqual(row.created_at, created)
            self.assertIsNotNone(row.edited_at)

    def test_the_unique_owner_constraint_holds_in_the_database(self):
        client = self.student("mia")
        self.write(client, 4)
        with application.app.app_context():
            db = application.db
            db.session.add(application.ProductReview(user_id=client.user_id, rating=2))
            with self.assertRaises(application.IntegrityError):
                db.session.commit()
            db.session.rollback()
            db.session.add(application.ProductReview(user_id=client.user_id + 999, rating=9))
            with self.assertRaises(application.IntegrityError):
                db.session.commit()      # the rating CHECK constraint
            db.session.rollback()

    def test_only_the_owner_can_change_or_delete(self):
        mia, bert = self.student("mia"), self.student("bert")
        self.write(mia, 5, "Mia's review")
        self.write(bert, 2, "Bert's review")
        bert.post("/reviews/delete", headers={"Accept": "application/json"})
        self.assertEqual([row[3] for row in self.rows()], ["Mia's review"])       # Bert deleted only his own
        self.write(bert, 1, "new")                                              # creates Bert's again
        self.assertEqual(sorted(row[3] for row in self.rows()), ["Mia's review", "new"])
        # There is no route that takes a review id for editing or deleting.
        with application.app.app_context():
            mia_review = application.db.session.scalar(application.db.select(application.ProductReview).where(
                application.ProductReview.user_id == mia.user_id))
        for path in (f"/reviews/{mia_review.id}", f"/reviews/{mia_review.id}/delete"):
            self.assertEqual(bert.post(path, json={"rating": 1}).status_code, 404)
        self.assertIn("Mia's review", [row[3] for row in self.rows()])


class ValidationTests(ReviewTestCase):
    def test_invalid_ratings_are_refused(self):
        client = self.student("mia")
        for rating in (0, 6, -1, "five", None, "4.5"):
            with self.subTest(rating=rating):
                self.assertEqual(self.write(client, rating).status_code, 400)
        self.assertEqual(self.rows(), [])

    def test_text_is_optional_trimmed_and_at_most_1000_characters(self):
        client = self.student("mia")
        self.assertEqual(self.write(client, 5, "x" * 1001).status_code, 400)
        self.assertEqual(self.write(client, 5, "   \n\n\n\nGut   ").status_code, 201)
        self.assertEqual(self.rows()[0][3], "Gut")
        self.assertEqual(self.write(client, 4, "").status_code, 200)
        self.assertEqual(self.rows()[0][3], "")

    def test_html_is_shown_as_text(self):
        client = self.student("mia")
        attack = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        self.write(client, 5, attack)
        page = application.app.test_client().get("/about").get_data(as_text=True)
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x", page)
        self.assertIn("&lt;script&gt;alert", page)
        html = self.listing()["html"]
        self.assertNotIn("<script>alert", html)

    def test_links_and_contact_details_wait_for_a_moderator_but_criticism_does_not(self):
        spam, critic = self.student("spam"), self.student("critic")
        self.write(spam, 5, "Best app!!! visit www.cheap-essays.example now")
        self.write(critic, 1, "Honestly, the explanations were often too long and the app was slow for me.")
        statuses = {row[0]: row[2] for row in self.rows()}
        self.assertEqual(statuses[spam.user_id], "pending")
        self.assertEqual(statuses[critic.user_id], "published")
        self.assertEqual(reviews.hold_reason("Ruf mich an: +49 151 23456789"), "contact_details")
        self.assertEqual(reviews.hold_reason("Das war schlecht, wirklich schlecht."), "")


class OrderingTests(ReviewTestCase):
    def seed(self):
        now = application.utcnow()
        authors = {}
        for index, rating in enumerate((3, 5, 1, 4, 5, 2)):
            client = self.student(f"author{index}")
            self.write(client, rating, f"text {index} rated {rating}")
            self.set_status(client.user_id, "published", created=now - timedelta(days=10 - index))
            authors[index] = client
        return authors

    def ratings(self, **params):
        html = self.listing(**params)["html"]
        import re
        return [int(value) for value in re.findall(r"rated (\d)", html)]

    def test_recommended_puts_five_stars_first_and_newest_first_within_a_rating(self):
        self.seed()
        self.assertEqual(self.ratings(), [5, 5, 4, 3, 2, 1])
        html = self.listing()["html"]
        self.assertLess(html.index("text 4 rated 5"), html.index("text 1 rated 5"))   # newer five first

    def test_all_reviews_include_every_rating_and_each_filter_works(self):
        self.seed()
        self.assertEqual(sorted(self.ratings(rating="all")), [1, 2, 3, 4, 5, 5])
        for stars in range(1, 6):
            with self.subTest(stars=stars):
                self.assertTrue(self.ratings(rating=stars))
                self.assertEqual(set(self.ratings(rating=stars)), {stars})

    def test_newest_oldest_highest_lowest(self):
        self.seed()
        self.assertEqual(self.ratings(sort="newest"), [2, 5, 4, 1, 5, 3])
        self.assertEqual(self.ratings(sort="oldest"), [3, 5, 1, 4, 5, 2])
        self.assertEqual(self.ratings(sort="highest")[:2], [5, 5])
        self.assertEqual(self.ratings(sort="lowest")[:2], [1, 2])
        self.assertEqual(self.ratings(sort="nonsense"), [5, 5, 4, 3, 2, 1])      # falls back to recommended

    def test_the_summary_counts_every_published_review_and_nothing_else(self):
        authors = self.seed()
        self.set_status(authors[0].user_id, "hidden")       # 3 stars
        self.set_status(authors[2].user_id, "pending")      # 1 star
        self.set_status(authors[5].user_id, "rejected")     # 2 stars
        with application.app.test_request_context():
            summary = application.review_summary()
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.average, round((5 + 4 + 5) / 3, 2))
        self.assertEqual(dict(summary.counts), {1: 0, 2: 0, 3: 0, 4: 1, 5: 2})
        self.assertEqual(sorted(self.ratings(rating="all")), [4, 5, 5])
        for stars in (1, 2, 3):
            self.assertEqual(self.ratings(rating=stars), [])

    def test_no_reviews_is_an_empty_state_not_zero_stars(self):
        page = application.app.test_client().get("/about").get_data(as_text=True)
        self.assertIn("No reviews yet.", page)
        self.assertNotIn("0.0", page)
        summary = reviews.summarize([])
        self.assertIsNone(summary.average)
        self.assertTrue(summary.empty)

    def test_pagination_and_the_url_keeps_the_filter(self):
        now = application.utcnow()
        for index in range(reviews.PAGE_SIZE + 2):
            client = self.student(f"many{index}")
            self.write(client, 5, f"review number {index}")
            self.set_status(client.user_id, "published", created=now - timedelta(minutes=index))
        first, second = self.listing(page=1), self.listing(page=2)
        self.assertEqual((first["total"], first["has_more"], second["has_more"]), (reviews.PAGE_SIZE + 2, True, False))
        self.assertEqual(first["html"].count("review-card"), reviews.PAGE_SIZE)
        self.assertEqual(second["html"].count("review-card"), 2)
        page = application.app.test_client().get("/about?rating=5&sort=newest").get_data(as_text=True)
        self.assertIn("page=2", page)
        self.assertIn("rating=5", page)


class ModerationTests(ReviewTestCase):
    def test_reports_alert_a_moderator_and_never_hide_by_themselves(self):
        author, reader, other = self.student("author"), self.student("reader"), self.student("other")
        self.write(author, 1, "Not my thing.")
        review_id = self.listing()["html"].split('data-review-id="')[1].split('"')[0]
        for client in (reader, reader, other):
            client.post(f"/reviews/{review_id}/report", json={"reason": "spam"})
        self.assertEqual(author.post(f"/reviews/{review_id}/report", json={"reason": "spam"}).status_code, 400)
        self.assertEqual(reader.post(f"/reviews/{review_id}/report", json={"reason": "bogus"}).status_code, 400)
        with application.app.app_context():
            db = application.db
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.ReviewReport.id))), 2)
            self.assertEqual(db.session.scalar(db.select(application.ProductReview.report_count)), 2)
            self.assertEqual(db.session.scalar(db.select(db.func.count(application.AdminNotification.id)).where(
                application.AdminNotification.kind == "review_report")), 1)
        self.assertEqual(self.rows()[0][2], "published")

    def test_moderators_hide_reject_restore_and_block_with_an_audit(self):
        author, boss, reader = self.student("author"), self.student("boss"), self.student("reader")
        self.write(author, 2, "Too many ads?")
        review_id = int(self.listing()["html"].split('data-review-id="')[1].split('"')[0])
        self.assertEqual(reader.get("/admin/reviews").status_code, 404)
        self.assertEqual(reader.post(f"/admin/reviews/{review_id}/moderate", data={"action": "hide"}).status_code, 404)
        self.assertEqual(boss.get("/admin/reviews?status=published").status_code, 200)
        for action, status in (("hide", "hidden"), ("restore", "published"), ("reject", "rejected"),
                               ("publish", "published"), ("block_author", "hidden")):
            boss.post(f"/admin/reviews/{review_id}/moderate", data={"action": action, "note": "checked"})
            self.assertEqual(self.rows()[0][2], status, action)
        self.assertEqual(self.write(author, 5).status_code, 403)                 # blocked now
        with application.app.app_context():
            actions = application.db.session.scalars(application.db.select(
                application.ReviewModerationEvent.action).order_by(application.ReviewModerationEvent.id)).all()
        self.assertEqual(actions, ["hide", "restore", "reject", "publish", "block_author"])

    def test_an_edit_of_a_hidden_review_goes_back_to_a_moderator(self):
        author = self.student("author")
        self.write(author, 2, "first")
        self.set_status(author.user_id, "hidden")
        self.write(author, 3, "second try")
        self.assertEqual(self.rows()[0][2], "pending")

    def test_settings_shows_and_manages_the_own_review(self):
        client = self.student("mia")
        page = client.get("/settings").get_data(as_text=True)
        self.assertIn("Write your review", page)
        self.write(client, 4, "Gut erklärt")
        page = client.get("/settings").get_data(as_text=True)
        self.assertIn("Gut erklärt", page)
        self.assertIn("/reviews/delete", page)
        low = self.student("newbie", 1)
        self.assertIn("You can submit a review starting at Level 3.", low.get("/settings").get_data(as_text=True))


class MigrationTests(unittest.TestCase):
    def test_the_tables_exist_after_startup_and_the_marker_is_recorded(self):
        with application.app.app_context():
            db = application.db
            db.drop_all()
            db.create_all()
            application.ensure_database()
            names = set(application.inspect(db.engine).get_table_names())
            self.assertTrue({"product_review", "review_report", "review_moderation_event", "review_block"} <= names)
            self.assertIsNotNone(db.session.get(application.SchemaMigration, "052_product_reviews"))
            checks = application.inspect(db.engine).get_check_constraints("product_review")
            self.assertTrue(any("rating" in str(item.get("sqltext")) for item in checks))


if __name__ == "__main__":
    unittest.main()

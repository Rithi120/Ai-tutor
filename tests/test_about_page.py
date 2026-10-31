"""The public About page: reachable without login, translated, honest CTAs.

It is the only marketing surface, so these tests pin the properties that keep it
usable: anonymous access (no login redirect), exemption from the grade onboarding
gate, localized copy, working registration/dashboard CTAs, and the shared shell
(skip-link target, theme toggle) that every page promises.
"""

import os
import tempfile
import unittest
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_about_page_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class AboutPageTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def test_anonymous_visitors_to_the_site_root_land_on_about(self):
        # The root used to answer anonymous visitors with /login?next=/ - a bare login
        # form as the shop window. They are now greeted by the About page instead.
        response = self.client.get("/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/about", response.headers["Location"])
        self.assertNotIn("/login", response.headers["Location"])
        # Logged-in students keep the lesson builder at the root.
        self.client.post("/register", data={
            "username": "erik", "email": "erik@example.com",
            "password": "correct-horse-battery", "grade": "8",
        }, follow_redirects=True)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_about_is_public_and_renders_the_core_sections(self):
        response = self.client.get("/about")
        self.assertEqual(response.status_code, 200)
        page = response.data
        self.assertIn(b"Learn smarter. Understand deeper.", page)
        self.assertIn(b"Why choose Learnova?", page)
        self.assertIn(b"How Learnova works", page)
        self.assertIn(b"Frequently asked questions", page)
        self.assertIn(b"/static/css/about.css", page)
        # Competitors are named, and the comparison carries its honesty note.
        for competitor in (b"Quizlet", b"Knowt", b"StudyFetch", b"ChatGPT"):
            self.assertIn(competitor, page)
        self.assertIn("official pages in October 2026".encode(), page)

    def test_adsense_verification_renders_once_on_shared_pages_without_ad_code(self):
        # Site verification uses Google's meta tag, which loads nothing. The ad code itself
        # waits for a certified consent message (ADSENSE_CMP), so by default no page loads it.
        meta = b'<meta name="google-adsense-account" content="ca-pub-5205912301427741">'
        pages = [self.client.get("/about").data,
                 self.client.get("/login").data,
                 self.client.get("/register").data]
        self.client.post("/register", data={
            "username": "homepage", "email": "homepage@example.com",
            "password": "correct-horse-battery", "grade": "8",
        }, follow_redirects=True)
        pages.append(self.client.get("/").data)
        for page in pages:
            self.assertEqual(page.count(meta), 1)
            self.assertNotIn(b"adsbygoogle.js", page)

    def test_anonymous_visitors_get_registration_ctas(self):
        page = self.client.get("/about").data
        self.assertIn(b'href="/register"', page)
        self.assertIn(b"Start learning for free", page)
        self.assertIn(b'href="/login"', page)

    def test_anonymous_visitors_see_login_and_register_in_the_top_bar(self):
        # Visitors must not have to find the login page themselves: the navbar's
        # guest group carries Log in and Create account on every public page.
        page = self.client.get("/about").data.decode("utf-8")
        guest_nav = page.split('class="guest-nav"')[1].split("</div>")[0]
        self.assertIn('href="/login"', guest_nav)
        self.assertIn("Log in", guest_nav)
        self.assertIn('href="/register"', guest_nav)
        self.assertIn("Create account", guest_nav)
        # Logged-in students keep their own navigation instead.
        self.client.post("/register", data={
            "username": "dora", "email": "dora@example.com",
            "password": "correct-horse-battery", "grade": "8",
        }, follow_redirects=True)
        self.assertNotIn(b'class="guest-nav"', self.client.get("/about").data)

    def test_logged_in_students_get_their_dashboard_instead(self):
        self.client.post("/register", data={
            "username": "alice", "email": "alice@example.com",
            "password": "correct-horse-battery", "grade": "8",
        }, follow_redirects=True)
        page = self.client.get("/about").data
        self.assertIn(b"Open your dashboard", page)
        self.assertNotIn(b"Start learning for free", page)

    def test_a_user_without_a_grade_is_not_gated_away_from_about(self):
        # The grade onboarding gate redirects HTML GETs for grade-less users; the
        # public About page is on its exemption list, like /login and /settings.
        with application.app.app_context():
            user = application.User(username="bob", email="bob@example.com",
                                    preferred_language="en", grade="")
            user.set_password("correct-horse-battery")
            application.db.session.add(user)
            application.db.session.commit()
        self.client.post("/login", data={"identifier": "bob", "password": "correct-horse-battery"})
        gated = self.client.get("/dashboard", headers={"Accept": "text/html"})
        self.assertEqual(gated.status_code, 302, "the gate itself must still work")
        about = self.client.get("/about", headers={"Accept": "text/html"})
        self.assertEqual(about.status_code, 200)

    def test_the_page_is_translated_for_german_students(self):
        with self.client.session_transaction() as session:
            session["language"] = "de"
        page = self.client.get("/about").data.decode("utf-8")
        self.assertIn("Lerne schlauer", page)
        self.assertIn("Warum Learnova?", page)
        self.assertIn("Kostenlos lernen starten", page)
        self.assertNotIn("Start learning for free", page)

    def test_plans_and_limits_come_from_the_configuration(self):
        # The page used to say "There are no paid plans" after Premium existed.
        page = self.client.get("/about").data.decode("utf-8")
        self.assertNotIn("no paid plans", page)
        self.assertNotIn("All features are free", page)
        plan = application.subscription_settings()
        price = f"{plan.premium_price_eur:.2f}".replace(".", ",")
        self.assertIn(f"Premium costs {price} € a month", page)
        self.assertIn(f"new projects ({plan.free_projects_per_window} every {plan.free_project_window_days} days)", page)
        self.assertIn(f"{plan.free_lessons_per_day} new lessons a day outside projects", page)
        self.assertIn("Lessons and tests inside your projects are unlimited", page)
        bounds = application.test_range()
        self.assertIn(f"between {bounds['minimum']} and {bounds['maximum']} questions", page)

    def test_current_features_and_privacy_are_described(self):
        page = self.client.get("/about").data.decode("utf-8")
        for text in ("Project Mode", "Kompetenzraster", "flexible plan", "Vocabulary Trainer",
                     "An assistant for quick questions", "XP, challenges and weekly leagues",
                     "Who can see my profile?", "your email is never shown"):
            self.assertIn(text, page)
        # Profiles and leagues exist, so "nothing is shared" would no longer be true.
        self.assertNotIn("Nothing is shared with other students", page)
        self.assertNotIn("section tests, final exams", page)
        with self.client.session_transaction() as session:
            session["language"] = "de"
        german = self.client.get("/about").data.decode("utf-8")
        for text in ("Projektmodus", "Wer kann mein Profil sehen?", "Kostenlos, mit optionalem Premium"):
            self.assertIn(text, german)

    def test_the_page_keeps_the_shared_shell_contract(self):
        page = self.client.get("/about").data
        self.assertIn(b'id="main-content"', page)
        self.assertIn(b"data-theme-toggle", page)
        self.assertIn(b"/static/css/learnova-tokens.css", page)

    def test_the_about_link_is_reachable_from_the_footer_and_nav(self):
        anonymous = self.client.get("/about").data
        self.assertIn(b'href="/about"', anonymous)
        self.client.post("/register", data={
            "username": "carol", "email": "carol@example.com",
            "password": "correct-horse-battery", "grade": "8",
        }, follow_redirects=True)
        dashboard = self.client.get("/dashboard", headers={"Accept": "text/html"})
        self.assertEqual(dashboard.status_code, 200)
        self.assertIn(b'href="/about"', dashboard.data)


def tearDownModule():
    with application.app.app_context():
        application.db.session.remove()
        application.db.engine.dispose()
    if TEST_DATABASE.exists():
        TEST_DATABASE.unlink()


if __name__ == "__main__":
    unittest.main()

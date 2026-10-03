import os
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_redesign_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


class LearnovaRedesignTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "visual-student",
            "email": "visual@example.com",
            "password": "correct-horse-battery",
        })

    def make_set(self):
        response = self.client.post("/api/flashcards/sets", json={
            "title": "Cell biology",
            "subject": "Biology",
            "cards": [
                {"type": "term_definition", "front": "Cell", "back": "Basic unit of life"},
                {"type": "term_definition", "front": "DNA", "back": "Genetic material"},
            ],
        })
        return response.get_json()["id"]

    def test_shared_theme_and_accessibility_shell_is_on_product_pages(self):
        for path in ("/dashboard", "/flashcards", "/flashcards/create", "/community", "/progress", "/vocabulary"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            html = response.data
            self.assertIn(b'id="main-content"', html)
            self.assertIn(b'class="skip-link"', html)
            self.assertIn(b"learnova-tokens.css", html)
            self.assertIn(b"learnova-components.css", html)
            self.assertIn(b"learnova-pages.css", html)
            self.assertIn(b"data-theme-toggle", html)
            self.assertIn(b"theme.js", html)

    def test_dashboard_exposes_unique_learning_visuals_with_real_metrics(self):
        html = self.client.get("/dashboard").data
        for marker in (b"knowledge-pulse", b"mastery-map", b"study-orbit", b"focus-trail"):
            self.assertIn(marker, html)
        self.assertIn(b"Knowledge Pulse", html)
        self.assertIn(b"Mastery Map", html)

    def test_creator_defaults_to_essential_fields_and_supports_autosave(self):
        html = self.client.get("/flashcards/create").data
        self.assertIn(b'class="set-more"', html)
        self.assertIn(b'id="setTitle"', html)
        self.assertIn(b'id="cardRows"', html)
        self.assertIn(b'id="autosaveState"', html)
        script = Path("static/js/flashcards/create.js").read_text(encoding="utf-8")
        self.assertIn("learnova:flashcard-draft:", script)
        self.assertIn("scheduleAutosave", script)
        self.assertIn('event.key.toLowerCase() === "s"', script)
        self.assertIn('event.key === "Enter"', script)
        self.assertIn("data-up", script)
        self.assertIn("data-down", script)

    def test_community_has_discovery_sections_and_stable_detail_shell(self):
        html = self.client.get("/community").data
        for marker in (b"commTrending", b"commTopRated", b"commNewest", b"community-search"):
            self.assertIn(marker, html)
        script = Path("static/js/community.js").read_text(encoding="utf-8")
        self.assertIn("loadDiscoveryShelves", script)
        self.assertIn("history.pushState", script)
        self.assertIn("/community/sets/", script)

    def test_all_study_modes_share_focus_shell_and_next_action(self):
        set_id = self.make_set()
        for mode in ("study", "learn", "test", "match", "blast", "blocks"):
            response = self.client.get(f"/flashcards/{set_id}/{mode}")
            self.assertEqual(response.status_code, 200, mode)
            self.assertIn(b"data-mode-page", response.data)
            self.assertIn(b"focus-mode-label", response.data)
            self.assertIn(b"summary-next-action", response.data)

    def test_responsive_and_reduced_motion_rules_cover_core_surfaces(self):
        pages_css = Path("static/css/learnova-pages.css").read_text(encoding="utf-8")
        layout_css = Path("static/css/learnova-layouts.css").read_text(encoding="utf-8")
        study_css = Path("static/css/learnova-study.css").read_text(encoding="utf-8")
        components_css = Path("static/css/learnova-components.css").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 480px)", pages_css)
        self.assertIn("@media (max-width: 560px)", layout_css)
        self.assertIn("@media (max-width: 620px)", study_css)
        self.assertIn("@media (prefers-reduced-motion: reduce)", components_css)
        self.assertIn(":focus-visible", components_css)


if __name__ == "__main__":
    unittest.main()

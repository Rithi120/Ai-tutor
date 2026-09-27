import os
import re
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_flashcards_ui_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402


CARDS = [
    {"type": "question_answer", "front": "What is a cell?", "back": "The basic unit of life."},
    {"type": "term_definition", "front": "Nucleus", "back": "Stores DNA."},
]


class FlashcardsUiTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()

    def _client(self, username="alice", language="en"):
        client = application.app.test_client()
        client.post("/register", data={
            "username": username, "email": f"{username}@example.com",
            "password": "correct-horse-battery", "language": language})
        return client

    def _make_set(self, client):
        return client.post("/api/flashcards/sets", json={
            "title": "Bio", "subject": "Biology", "cards": CARDS}).get_json()["id"]

    # ---- internationalization ----
    def test_creator_renders_in_english(self):
        client = self._client(language="en")
        html = client.get("/flashcards/create").data
        self.assertIn("Create flashcards".encode(), html)
        self.assertIn(b"Add card", html)

    def test_creator_renders_in_german(self):
        client = self._client(username="bob", language="de")
        html = client.get("/flashcards/create").data
        self.assertIn("Karteikarten erstellen".encode(), html)
        self.assertIn("Karte hinzufügen".encode(), html)  # Add card

    def test_navigation_localized_to_german(self):
        client = self._client(username="carol", language="de")
        html = client.get("/dashboard").data
        self.assertIn("Karteikarten".encode(), html)  # Flashcards nav label

    def test_content_language_indicator_shown(self):
        # Interface is German; the creator shows which language content will be generated in.
        client = self._client(username="dan", language="de")
        html = client.get("/flashcards/create").data
        self.assertIn("Sprache".encode(), html)  # Language label localized

    # ---- dashboard integration ----
    def test_dashboard_flashcards_empty_state(self):
        client = self._client()
        html = client.get("/dashboard").data
        self.assertIn(b"My flashcards", html)
        self.assertIn(b"You have no flashcards yet", html)

    def test_dashboard_flashcards_with_data(self):
        client = self._client()
        self._make_set(client)
        html = client.get("/dashboard").data
        self.assertIn(b"My flashcards", html)
        self.assertIn(b"Cards due today", html)
        self.assertIn(b"Bio", html)

    # ---- routes ----
    def test_overview_edit_study_routes(self):
        client = self._client()
        set_id = self._make_set(client)
        for suffix in ("", "/edit", "/study"):
            self.assertEqual(client.get(f"/flashcards/{set_id}{suffix}").status_code, 200)

    def test_routes_404_for_unowned_or_missing(self):
        owner = self._client(username="owner")
        set_id = self._make_set(owner)
        stranger = self._client(username="stranger")
        self.assertEqual(stranger.get(f"/flashcards/{set_id}").status_code, 404)
        self.assertEqual(owner.get("/flashcards/999999/study").status_code, 404)

    # ---- feature flags ----
    def test_completed_modes_replace_coming_soon_placeholders(self):
        client = self._client()
        set_id = self._make_set(client)
        html = client.get(f"/flashcards/{set_id}").data
        self.assertNotIn(b"Coming soon", html)
        for mode in (b"/learn", b"/test", b"/match", b"/blast", b"/blocks"):
            self.assertIn(mode, html)

    def test_every_study_mode_link_goes_somewhere_without_javascript(self):
        """Start learning, Edit and the Flashcards card used to ship as href="#".

        A fetch in overview.js pointed them at a URL the server already knew, so until
        it finished - and permanently if it threw - clicking them did nothing at all,
        with no error. A link must work before, during and without JavaScript.
        """

        client = self._client()
        set_id = self._make_set(client)
        html = client.get(f"/flashcards/{set_id}").get_data(as_text=True)

        cards = re.findall(r'<a[^>]*class="[^"]*mode-card[^"]*"[^>]*href="([^"]+)"', html)
        actions = dict(re.findall(
            r'<a[^>]*id="(ovStudy|ovEdit)"[^>]*href="([^"]+)"', html))

        # Six cards: the plain flashcards mode plus the five game modes.
        self.assertEqual(len(cards), 6, cards)
        self.assertEqual(set(actions), {"ovStudy", "ovEdit"}, actions)

        for href in [*cards, *actions.values()]:
            self.assertNotEqual(href, "#", "a study link is still a dead placeholder")
            self.assertTrue(href.startswith(f"/flashcards/{set_id}/"), href)
            self.assertEqual(client.get(href).status_code, 200, href)

    def test_create_page_links_to_importer_when_enabled(self):
        # Import is enabled in the testing profile, so the creator must point to the
        # working importer instead of the old, misleading "coming soon" placeholder.
        client = self._client()
        html = client.get("/flashcards/create").data
        self.assertNotIn(b'type="file"', html)          # no non-functional upload control on the creator
        self.assertNotIn(b"coming soon", html.lower())  # the feature exists: no stale placeholder
        self.assertIn(b"/flashcards/import", html)      # real link to the working importer

    def test_create_page_import_link_hidden_when_flags_off(self):
        # When both import flags are off, hide the control entirely (no broken button, no placeholder).
        client = self._client()
        application.app.config["FEATURE_FLASHCARD_PDF_IMPORT"] = False
        application.app.config["FEATURE_FLASHCARD_IMAGE_IMPORT"] = False
        try:
            html = client.get("/flashcards/create").data
            self.assertNotIn(b'type="file"', html)
            self.assertNotIn(b"/flashcards/import", html)
        finally:
            application.app.config["FEATURE_FLASHCARD_PDF_IMPORT"] = True
            application.app.config["FEATURE_FLASHCARD_IMAGE_IMPORT"] = True

    def test_new_feature_flags_exposed_to_frontend(self):
        client = self._client()
        html = client.get("/flashcards").data
        self.assertIn(b"flashcard_games", html)
        self.assertIn(b"flashcard_pdf_import", html)

    def test_private_flashcards_flag_gates_pages(self):
        client = self._client()
        set_id = self._make_set(client)
        application.app.config["FEATURE_PRIVATE_FLASHCARDS"] = False
        try:
            self.assertEqual(client.get("/flashcards").status_code, 404)
            self.assertEqual(client.get(f"/flashcards/{set_id}/study").status_code, 404)
        finally:
            application.app.config["FEATURE_PRIVATE_FLASHCARDS"] = True


if __name__ == "__main__":
    unittest.main()

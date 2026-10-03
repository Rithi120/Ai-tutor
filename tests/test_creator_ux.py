"""The flashcard editor's default view stays minimal.

The editor used to put everything on screen at once: a title panel, an AI panel with
four dropdowns, and rows carrying a drag handle, a number, two boxed textareas, five
icon buttons and a disclosure. It worked, and it was exhausting.

These tests pin the shape of the redesign rather than its styling: what a student meets
before they open anything, what is reachable behind a disclosure, and that nothing was
deleted on the way - every advanced control still exists, one level down.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_creator_ux_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _remove_subtree(html, opening):
    """Delete the element `opening` starts, and everything inside it.

    Depth-counted rather than regex-matched: a non-greedy `.*?</div>` stops at the first
    closing tag, which would leave most of a nested panel behind and quietly make every
    assertion below pass.
    """

    while True:
        match = re.search(opening, html)
        if not match:
            return html
        tag = re.match(r"<(\w+)", html[match.start():])
        assert tag is not None
        name = tag.group(1)
        depth, index = 0, match.start()
        for step in re.finditer(rf"<{name}\b|</{name}>", html[match.start():]):
            depth += 1 if step.group(0).startswith(f"<{name}") and step.group(0) != f"</{name}>" else -1
            if depth == 0:
                index = match.start() + step.end()
                break
        else:
            return html[:match.start()]
        html = html[:match.start()] + html[index:]


def _collapse_details(html):
    """Reduce every <details> to its <summary>: the label shows, the body does not."""

    while True:
        match = re.search(r"<details\b", html)
        if not match:
            return html
        depth, end = 0, len(html)
        for step in re.finditer(r"<details\b|</details>", html[match.start():]):
            depth += 1 if step.group(0) == "<details" else -1
            if depth == 0:
                end = match.start() + step.end()
                break
        block = html[match.start():end]
        summary = re.search(r"<summary\b.*?</summary>", block, flags=re.S)
        html = html[:match.start()] + (summary.group(0) if summary else "") + html[end:]


def visible_part(html):
    """The page with everything a student cannot see yet removed.

    Progressive disclosure is the whole point, so a test that read the raw HTML would
    happily pass on a page that showed all of it at once.
    """

    return _remove_subtree(_collapse_details(html),
                           r'<\w+[^>]*class="[^"]*\bhidden\b[^"]*"')


class VisiblePartTests(unittest.TestCase):
    """The helper the assertions below depend on. If it over-strips they pass vacuously."""

    def test_it_keeps_what_is_shown_and_drops_what_is_not(self):
        page = ('<main><h1>Kept</h1>'
                '<details><summary>Open me</summary><input id="buried"></details>'
                '<div class="gen-body hidden"><div><select id="alsoBuried"></select></div></div>'
                '<button id="stillHere">Go</button></main>')
        shown = visible_part(page)
        self.assertIn("Kept", shown)
        self.assertIn("stillHere", shown)
        self.assertIn("Open me", shown)      # the summary is visible, its body is not
        self.assertNotIn("buried", shown)
        # The nested div is what a non-greedy regex would leave behind.
        self.assertNotIn("alsoBuried", shown)


class CreatorDefaultViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create_js = (ROOT / "static/js/flashcards/create.js").read_text(encoding="utf-8")
        cls.template = (ROOT / "templates/flashcards_create.html").read_text(encoding="utf-8")

    def setUp(self):
        application.app.config.update(TESTING=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "nils", "email": "nils@example.com",
            "password": "correct-horse-battery"})
        self.html = self.client.get("/flashcards/create").get_data(as_text=True)

    # ---- what a student meets first ----
    def test_the_title_is_the_only_field_in_the_open_view(self):
        shown = visible_part(self.html)
        self.assertIn('id="setTitle"', shown)
        for buried in ("setDescription", "metaSubject", "metaTopic",
                       "metaGrade", "metaDifficulty", "metaTags"):
            self.assertNotIn(buried, shown, f"{buried} should be behind a disclosure")

    def test_set_details_are_reachable_one_click_away(self):
        # Reduced, not removed: every field still exists inside the disclosure.
        self.assertIn('class="set-more"', self.html)
        for field in ("setDescription", "metaSubject", "metaTopic",
                      "metaGrade", "metaDifficulty", "metaTags"):
            self.assertIn(field, self.html)

    def test_generation_offers_three_ways_in_without_opening_a_panel(self):
        shown = visible_part(self.html)
        self.assertIn('data-gen-choice="text"', shown)
        self.assertIn('data-gen-choice="topic"', shown)
        self.assertIn("/flashcards/import", shown)

    def test_generation_settings_wait_until_they_are_asked_for(self):
        shown = visible_part(self.html)
        for control in ("genCount", "genType", "genDifficulty", "genContentLang", "genText"):
            self.assertNotIn(control, shown, f"{control} should not be in the default view")
            self.assertIn(control, self.html, f"{control} must still exist")
        self.assertIn('class="gen-options"', self.html)

    def test_the_generation_panel_starts_closed(self):
        panel = re.search(r'<div id="genPanel"[^>]*class="([^"]*)"', self.html)
        self.assertIsNotNone(panel)
        self.assertIn("hidden", panel.group(1))  # type: ignore[union-attr]

    def test_a_dropdown_with_one_choice_is_not_a_control(self):
        # #metaVisibility was a <select> offering only "Private (only me)".
        self.assertNotIn("metaVisibility", self.html)
        self.assertNotIn("metaVisibility", self.create_js)

    # ---- the card row ----
    def test_a_row_shows_only_the_ai_button_and_one_menu(self):
        head = self.create_js.split('class="card-row-head"', 1)[1].split("card-row-fields", 1)[0]
        self.assertIn("data-suggest", head)      # the AI button stays visible
        self.assertIn("card-menu", head)
        for action in ("data-up", "data-down", "data-dup", "data-del"):
            self.assertNotIn(action, head.split("card-menu-body", 1)[0],
                             f"{action} should live inside the menu, not beside the fields")

    def test_every_row_action_survived_the_move_into_the_menu(self):
        menu = self.create_js.split("card-menu-body", 1)[1].split("</details>", 1)[0]
        for action in ("data-up", "data-down", "data-regen", "data-dup", "data-del"):
            self.assertIn(action, menu)

    def test_fields_start_one_line_tall_and_grow(self):
        # A one-word term in a fixed five-line box is most of what made the old editor
        # feel heavy; rows="1" plus autosize is what replaces it.
        self.assertIn('data-field="front" rows="1"', self.create_js)
        self.assertIn('data-field="back" rows="1"', self.create_js)
        self.assertIn("function autosize(", self.create_js)
        self.assertIn("scrollHeight", self.create_js)

    def test_each_field_is_captioned_rather_than_boxed(self):
        self.assertIn('class="card-field-label"', self.create_js)
        css = (ROOT / "static/css/flashcards.css").read_text(encoding="utf-8")
        rule = css.split(".card-field textarea {", 1)[1].split("}", 1)[0]
        self.assertIn("border: 0", rule)
        self.assertIn("border-bottom", rule)

    def test_per_card_advanced_options_are_still_there(self):
        for field in ("explanation", "hint", "difficulty", "tags"):
            self.assertIn(f'data-field="{field}"', self.create_js)
        self.assertIn('class="card-more"', self.create_js)

    # ---- the AI features the redesign had to keep ----
    def test_the_ai_button_is_on_every_card(self):
        self.assertIn("data-suggest", self.create_js)
        self.assertIn("suggest-trigger", self.create_js)

    def test_the_menu_closes_when_the_student_clicks_elsewhere(self):
        # A native <details> stays open until told otherwise, unlike every other menu.
        self.assertIn(".card-menu[open]", self.create_js)

    def test_the_row_menu_flips_for_right_to_left_interfaces(self):
        # A dropdown pinned with `right:` would sit off-card in Arabic. Logical
        # properties flip on their own, which is why learnova-rtl.css needs no entry.
        css = (ROOT / "static/css/flashcards.css").read_text(encoding="utf-8")
        rule = css.split(".card-menu-body {", 1)[1].split("}", 1)[0]
        self.assertIn("inset-inline-end", rule)
        self.assertNotIn("right:", rule)


if __name__ == "__main__":
    unittest.main()

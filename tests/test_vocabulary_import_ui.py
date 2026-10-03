"""The vocabulary import page: one decision, one input, then the details.

The page used to render its form with browser defaults. `.field` and `.field-row` live in
flashcards.css, which this page never loads, so labels sat inline against tiny selects,
the input-method chooser was a bare <fieldset>, and the dropzone showed a raw "Choose
file" button. These tests pin the redesign's shape and, more importantly, that every hook
the JavaScript and the server depend on survived it.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_vocab_import_ui_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class VocabularyImportPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "static/css/vocabulary.css").read_text(encoding="utf-8")
        cls.js = (ROOT / "static/js/vocabulary.js").read_text(encoding="utf-8")

    def setUp(self):
        application.app.config.update(TESTING=True, FEATURE_VOCABULARY_TRAINER=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "vocab", "email": "vocab@example.com",
            "password": "correct-horse-battery"})
        self.html = self.client.get("/vocabulary/import").get_data(as_text=True)

    # ---- the contract with the script and the server ----
    def test_every_hook_the_script_depends_on_is_still_there(self):
        for hook in ('id="vocabularyImportForm"', 'id="vocabularyFileField"',
                     'id="vocabularyTextField"', 'id="vocabularyImportStatus"',
                     'id="vocabularyImportError"'):
            self.assertIn(hook, self.html, hook)

    def test_every_field_the_server_reads_is_still_submitted(self):
        for name in ("source_kind", "source_language", "target_language", "title", "file", "text"):
            self.assertIn(f'name="{name}"', self.html, name)

    def test_the_three_input_methods_are_still_radios_with_the_same_values(self):
        # app.py branches on exactly these values; the script toggles fields on them.
        values = re.findall(r'name="source_kind" value="([a-z]+)"', self.html)
        self.assertEqual(values, ["file", "text", "manual"])
        self.assertIn('value="file" checked', self.html)

    def test_the_text_field_starts_hidden_and_the_file_field_shown(self):
        text_field = re.search(r'<label id="vocabularyTextField" class="([^"]*)"', self.html)
        file_field = re.search(r'<label id="vocabularyFileField" class="([^"]*)"', self.html)
        assert text_field and file_field
        self.assertIn("hidden", text_field.group(1))
        self.assertNotIn("hidden", file_field.group(1))

    # ---- what changed for the student ----
    def test_the_method_chooser_is_cards_not_a_bare_fieldset(self):
        self.assertNotIn("<fieldset", self.html)
        self.assertNotIn("<legend", self.html)
        self.assertEqual(self.html.count('class="method-card"'), 3)
        self.assertIn('role="radiogroup"', self.html)

    def test_each_method_explains_itself_in_one_line(self):
        for hint in ("Snap a schoolbook page", "one word pair per line", "right here"):
            self.assertIn(hint, self.html)

    def test_the_dropzone_hides_the_raw_file_button_but_keeps_the_input(self):
        rule = self.css.split('.upload-drop input[type="file"] {', 1)[1].split("}", 1)[0]
        self.assertIn("opacity: 0", rule)
        self.assertIn("position: absolute", rule)   # still covers the zone, so a tap opens it
        self.assertIn("[data-upload-title]", self.js)  # and the script names the chosen file

    def test_the_form_controls_are_actually_styled_on_this_page(self):
        # The old page relied on .field from a stylesheet it never loaded.
        for selector in (".vocabulary-page .field {", ".vocabulary-page .field-label {",
                         ".vocabulary-page select,"):
            self.assertIn(selector, self.css, selector)

    def test_colours_come_from_the_theme_tokens(self):
        # Hardcoded #fff / #dbe2ea would ignore dark mode.
        import_rules = self.css.split("/* ---- Import page", 1)[1]
        self.assertNotRegex(import_rules, r"#[0-9a-fA-F]{3,6}\b")
        self.assertIn("var(--ln-violet)", import_rules)

    def test_it_stacks_on_a_phone(self):
        phone = self.css.split("@media (max-width: 700px)", 1)[1].split("}\n}", 1)[0]
        self.assertIn(".method-cards { grid-template-columns: 1fr; }", phone)
        self.assertIn(".import-languages { grid-template-columns: 1fr; }", phone)

    def test_german_labels_come_through(self):
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(
                application.User).where(application.User.username == "vocab"))
            assert user is not None
            user.preferred_language = "de"
            application.db.session.commit()
        page = self.client.get("/vocabulary/import").get_data(as_text=True)
        for german in ("Vokabeln importieren", "Wie möchtest du Wörter hinzufügen?",
                       "Foto oder PDF", "Manuelle Eingabe", "Datei"):
            self.assertIn(german, page, german)

    # ---- manual entry: three boxes, not one delimited line ----
    def test_manual_entry_offers_three_boxes(self):
        self.assertIn('id="vocabularyManualField"', self.html)
        self.assertIn('id="vocabularyManualRows"', self.html)
        self.assertIn('id="addManualRow"', self.html)
        for field in ("source_term", "target_translation", "source_example_sentence"):
            self.assertIn(f'data-manual="{field}"', self.js, field)

    def test_manual_rows_travel_as_structured_json(self):
        self.assertIn('name="manual_entries"', self.html)
        self.assertIn("JSON.stringify(collectManualRows())", self.js)

    def test_the_manual_card_no_longer_promises_a_next_screen(self):
        # The words are typed on this page now, so the old hint would be a lie.
        self.assertNotIn("on the next screen", self.html)
        self.assertIn("right here", self.html)

    def test_enter_moves_to_the_next_row(self):
        self.assertIn('event.key !== "Enter"', self.js)

    def test_manual_rows_stack_with_their_own_labels_on_a_phone(self):
        phone = self.css.split("@media (max-width: 700px)", 1)[1].split("}\n}", 1)[0]
        self.assertIn(".manual-head { display: none; }", phone)
        self.assertIn(".manual-label { display: block; }", phone)

    def test_the_sibling_pages_still_have_their_classes(self):
        # vocabulary.css was rewritten; every selector the other four pages use must remain.
        for selector in (".vocabulary-grid", ".vocabulary-list-card", ".vocabulary-table",
                         ".review-row", ".vocabulary-practice-card",
                         ".generation-settings", ".row-actions", ".status-valid"):
            self.assertIn(selector, self.css, selector)


if __name__ == "__main__":
    unittest.main()

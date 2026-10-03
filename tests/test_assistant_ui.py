"""The assistant page's visual contract.

Two things are pinned here. The first is the shape the page was restyled into: one
centred reading column, the learner's words in a rounded bubble, the reply as prose
beside a brand dot, and a single rounded composer shell with a round send button.

The second is the bug that made the restyle necessary in the first place. assistant.css
read `--border`, `--surface`, `--accent` and `--surface-muted`. None of those are defined
anywhere in the app - the tokens are all named `--ln-*` - so every rule silently fell
through to its hardcoded light-mode fallback and the page ignored dark mode completely,
while its own header comment claimed the opposite. The same file used `.visually-hidden`
for three labels, and that class lives in flashcards.css, which this page does not load,
so "Search conversations", "Assistant style" and "Your message" were drawn as loose text.

A stylesheet that reads a property nothing defines fails silently and looks fine in the
only theme the author had open. These tests make it fail loudly instead.
"""

import os
import re
import tempfile
import unittest
from pathlib import Path


TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_assistant_ui_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "static/css/assistant.css"
TOKENS = ROOT / "static/css/learnova-tokens.css"


def without_comments(text):
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


class TokenTests(unittest.TestCase):
    """Every custom property the page reads has to exist, or dark mode is a no-op."""

    @classmethod
    def setUpClass(cls):
        cls.css = without_comments(CSS.read_text(encoding="utf-8"))
        cls.tokens = TOKENS.read_text(encoding="utf-8")
        cls.defined = set(re.findall(r"^\s*(--[\w-]+)\s*:", cls.tokens, flags=re.M))

    def test_no_property_is_read_that_nothing_defines(self):
        read = set(re.findall(r"var\((--[\w-]+)", self.css))
        self.assertTrue(read, "the stylesheet reads no tokens at all")
        missing = sorted(read - self.defined)
        self.assertEqual(missing, [], f"undefined custom properties: {missing}")

    def test_the_four_properties_that_never_existed_are_gone(self):
        for dead in ("var(--border", "var(--surface,", "var(--accent", "var(--surface-muted"):
            self.assertNotIn(dead, self.css, dead)

    def test_the_page_follows_dark_mode(self):
        # Reading at least one token that the dark block redefines is what makes the
        # page change theme at all.
        dark = self.tokens.split('[data-theme="dark"]', 1)[1].split("}", 1)[0]
        redefined = set(re.findall(r"^\s*(--[\w-]+)\s*:", dark, flags=re.M))
        read = set(re.findall(r"var\((--[\w-]+)", self.css))
        for expected in ("--ln-surface", "--ln-line", "--ln-ink", "--ln-violet"):
            self.assertIn(expected, redefined, expected)
        self.assertTrue(read & redefined, "nothing on this page reacts to the dark theme")

    def test_colours_are_not_hardcoded_outside_the_code_block(self):
        # The code block is deliberately dark in both themes; nothing else may be.
        rules = self.css.split(".asst-code {", 1)
        before, after = rules[0], rules[1].split("}", 1)[1]
        for part in (before, after):
            for literal in re.findall(r"#[0-9a-fA-F]{3,8}\b", part):
                self.assertEqual(literal, "#fff", f"hardcoded colour {literal}")


class RoundedShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = without_comments(CSS.read_text(encoding="utf-8"))

    def rule(self, selector):
        """The declarations of the rule whose selector list *starts* with this one.

        A plain search would find `.asst-avatar` inside `.asst-msg-assistant >
        .asst-avatar`, and `.asst-send` inside the grouped reduced-motion rule, and read
        the wrong declarations in both cases.
        """

        match = re.search(
            r"^" + re.escape(selector) + r"\s*\{(.*?)\}", self.css, re.S | re.M)
        self.assertIsNotNone(match, f"no rule starts with {selector}")
        assert match
        return match.group(1)

    def test_the_panel_uses_the_shared_radius_scale(self):
        self.assertIn("border-radius: var(--ln-radius-lg)", self.rule(".asst-main"))

    def test_the_search_box_and_style_picker_are_pills(self):
        self.assertIn("border-radius: 999px", self.rule('.asst-sidebar input[type="search"]'))
        self.assertIn("border-radius: 999px", self.rule(".asst-thread-actions select"))

    def test_the_users_bubble_is_rounded_with_a_shorter_tail_corner(self):
        radius = re.search(r"border-radius: ([^;]+);", self.rule(".asst-msg-user .asst-body"))
        assert radius
        corners = radius.group(1).split()
        self.assertEqual(len(corners), 4, "a bubble needs four distinct corners")
        self.assertEqual(corners, ["22px", "22px", "6px", "22px"])

    def test_nothing_is_squarer_than_it_was(self):
        # Every literal radius on the page is at least as round as the old sheet's,
        # whose smallest box corner was 8px.
        for value in re.findall(r"border-radius: (\d+)px", self.css):
            self.assertGreaterEqual(int(value), 6, f"{value}px is a sharp corner")

    def test_the_send_button_is_a_circle(self):
        send = self.rule(".asst-send")
        self.assertIn("border-radius: 50%", send)
        self.assertIn("width: 2.3rem", send)
        self.assertIn("height: 2.3rem", send)

    def test_the_avatar_is_a_circle_in_the_brand_colour(self):
        avatar = self.rule(".asst-avatar")
        self.assertIn("border-radius: 50%", avatar)
        self.assertIn("var(--ln-violet)", avatar)


class ReadingShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = without_comments(CSS.read_text(encoding="utf-8"))
        cls.js = (ROOT / "static/js/assistant.js").read_text(encoding="utf-8")

    def test_a_reply_is_prose_rather_than_a_second_bubble(self):
        body = self.css.split(".asst-body {", 1)[1].split("}", 1)[0]
        self.assertNotIn("background", body)
        self.assertNotIn("border", body)

    def test_only_the_learners_own_words_are_bubbled(self):
        self.assertIn("background: color-mix", self.css.split(
            ".asst-msg-user .asst-body {", 1)[1].split("}", 1)[0])

    def test_messages_share_one_centred_column(self):
        msg = self.css.split(".asst-msg {", 1)[1].split("}", 1)[0]
        self.assertIn("max-width: 48rem", msg)
        self.assertIn("margin-inline: auto", msg)

    def test_the_reply_sits_beside_its_avatar_not_under_it(self):
        row = self.css.split(".asst-msg-assistant {", 1)[1].split("}", 1)[0]
        self.assertIn("display: grid", row)
        self.assertIn("grid-template-columns: 1.65rem", row)

    def test_the_uppercase_speaker_label_is_gone_but_the_name_is_still_announced(self):
        self.assertNotIn("asst-who", self.css)
        self.assertNotIn("asst-who", self.js)
        self.assertIn('who.className = "visually-hidden"', self.js)
        self.assertIn('t("asstYou")', self.js)
        self.assertIn('t("asstAssistant")', self.js)

    def test_the_avatar_is_hidden_from_screen_readers(self):
        # It carries no information a reader does not already get from the name.
        self.assertIn('avatar.setAttribute("aria-hidden", "true")', self.js)

    def test_the_waiting_bubble_matches_a_real_reply(self):
        pending = self.js.split("asst-pending", 1)[1].split("asst-dots", 1)[0]
        self.assertIn("asst-avatar", pending)
        self.assertIn("visually-hidden", pending)


class ComposerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = without_comments(CSS.read_text(encoding="utf-8"))
        cls.template = (ROOT / "templates/assistant.html").read_text(encoding="utf-8")

    def test_the_textarea_and_its_controls_share_one_shell(self):
        shell = self.template.split('class="asst-composer-shell"', 1)[1].split("</div>", 1)[0]
        self.assertIn('id="asstInput"', shell)
        shell_full = self.template.split('class="asst-composer-shell"', 1)[1]
        self.assertLess(shell_full.index('id="asstSend"'), shell_full.index("</form>"))

    def test_the_shell_carries_the_focus_ring_and_the_textarea_does_not(self):
        self.assertIn(":focus-within", self.css)
        self.assertIn("outline: none", self.css.split(
            ".asst-composer textarea:focus,", 1)[1].split("}", 1)[0])

    def test_the_send_button_keeps_an_accessible_name(self):
        button = self.template.split('id="asstSend"', 1)[0].rsplit("<button", 1)[1] \
            + self.template.split('id="asstSend"', 1)[1].split("</button>", 1)[0]
        self.assertIn('aria-label="{{ _(\'Send\') }}"', button)
        self.assertIn('aria-hidden="true"', button)


class RenderedPageTests(unittest.TestCase):
    def setUp(self):
        application.app.config.update(TESTING=True, FEATURE_ASSISTANT_CHAT=True)
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={
            "username": "asst", "email": "asst@example.com",
            "password": "correct-horse-battery"})
        self.html = self.client.get("/assistant").get_data(as_text=True)

    def test_the_hidden_labels_have_a_rule_to_hide_them(self):
        # The page uses the class, and is the only one that does not load the stylesheet
        # the other copy lives in - so it has to define it itself.
        self.assertIn('class="visually-hidden"', self.html)
        self.assertIn(".visually-hidden {", CSS.read_text(encoding="utf-8"))

    def test_every_hook_the_script_depends_on_survived_the_restyle(self):
        for hook in ("asstComposer", "asstInput", "asstSend", "asstDeep", "asstCount",
                     "asstThread", "asstList", "asstSearch", "asstNew", "asstPreset",
                     "asstRename", "asstArchive", "asstDelete", "asstWelcome",
                     "asstThreadTitle", "asstThreadMeta", "asstListEmpty",
                     "asstShowArchived", "asstToast"):
            self.assertIn(f'id="{hook}"', self.html, hook)

    def test_the_send_arrow_is_a_real_character_not_escaped_markup(self):
        self.assertIn("&uarr;", self.html)
        self.assertNotIn("&amp;uarr;", self.html)

    def test_german_still_comes_through(self):
        with application.app.app_context():
            user = application.db.session.scalar(application.db.select(
                application.User).where(application.User.username == "asst"))
            assert user is not None
            user.preferred_language = "de"
            application.db.session.commit()
        page = self.client.get("/assistant").get_data(as_text=True)
        for german in ("Neuer Chat", "Senden"):
            self.assertIn(german, page, german)


if __name__ == "__main__":
    unittest.main()

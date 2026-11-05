"""The chat assistant as the home page (WP5 of the assistant upgrade).

A signed-in student lands on the chat at "/". The lesson page keeps "/" for a running
lesson (`?session_id=`, which every lesson start and resume redirects to) and for a study
goal handed over (`?goal=`), and whenever the assistant is switched off; the blank lesson
builder - Short Lesson - has its own address, /lesson. The navigation leads with Chat and
keeps every other tool one tap away; the chat page has no footer, a viewport that follows
the phone keyboard, and every string in the student's language.
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_assistant_home_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from learnova.translations import catalog  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FR = json.loads((ROOT / "learnova/translations/data/fr.json").read_text(encoding="utf-8"))
ES = json.loads((ROOT / "learnova/translations/data/es.json").read_text(encoding="utf-8"))
PLACEHOLDER = re.compile(r"\{[^{}\s]*\}")
CHAT_LINK = 'href="/assistant"'
LESSON = {
    "lesson_title": "Potenzgesetze", "detected_level": "Klasse 8",
    "concepts": [{"name": "Potenzgesetze", "evidence": "Ziel"}],
    "explanation": "Bei gleicher Basis werden die Exponenten addiert: $a^m \\cdot a^n = a^{m+n}$.",
    "worked_example": {"problem": "Berechne $2^3 \\cdot 2^2$.", "steps": ["Exponenten addieren: 3 + 2 = 5."],
                       "answer": "$2^5 = 32$"},
    "teacher_tips": ["Exponenten nebeneinander schreiben."], "exceptions": [],
    "video_usefulness": 0, "videos": [], "image_usefulness": 0, "image_search_terms": [],
    "question": {"id": "q1", "concept": "Potenzgesetze", "difficulty": 1, "type": "multiple_choice",
                 "prompt": "Was ist $2^3 \\cdot 2^2$?", "hint": "Exponenten addieren.",
                 "options": [{"id": "a", "label": "$2^5$"}, {"id": "b", "label": "$2^6$"},
                             {"id": "c", "label": "$4^5$"}, {"id": "d", "label": "$2^1$"}],
                 "expected_answer": "a"},
}


def msgids(*names):
    pattern = re.compile(r"""_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")""")
    found = set()
    for name in names:
        text = (ROOT / "templates" / name).read_text(encoding="utf-8")
        found |= {match.group(1) if match.group(1) is not None else match.group(2) for match in pattern.finditer(text)}
    found.discard("")
    return found


def chat_js_keys():
    script = (ROOT / "static/js/assistant.js").read_text(encoding="utf-8")
    rules = (ROOT / "static/js/assistant-rules.js").read_text(encoding="utf-8")
    keys = set(re.findall(r'\bt\("(\w+)"', script + rules))
    keys |= set(re.findall(r'^\s+\w+: "(asst\w+)",$', rules, re.M))      # GROUP_LABELS
    keys |= set(re.findall(r'return "(asst\w+|img\w+)";', rules))         # failureKey
    return keys


class HomeCase(unittest.TestCase):
    def setUp(self):
        self.saved = {key: application.app.config.get(key) for key in (
            "FEATURE_ASSISTANT_CHAT", "FEATURE_CHAT_IMAGES", "FEATURE_CONTINUE_LEARNING",
            "FEATURE_GAMIFICATION", "FEATURE_LEAGUES")}
        application.app.config.update(TESTING=True, FEATURE_ASSISTANT_CHAT=True, FEATURE_CHAT_IMAGES=True)
        application.SESSIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()

    def tearDown(self):
        application.app.config.update(self.saved)

    def register(self, name="hannah", language="en"):
        return self.client.post("/register", data={
            "username": name, "email": f"{name}@example.com", "password": "correct-horse-battery",
            "language": language}, follow_redirects=False)

    def page(self, path):
        response = self.client.get(path)
        self.assertEqual(response.status_code, 200, f"{path} -> {response.status_code} {response.headers.get('Location')}")
        return response.get_data(as_text=True)

    @staticmethod
    def desktop_nav(html):
        return html.split('<nav class="desktop-app-nav', 1)[1].split("</nav>", 1)[0]


class HomeDispatchTests(HomeCase):
    def test_the_home_page_is_the_chat_with_its_empty_state(self):
        self.register()
        html = self.page("/")
        for hook in ('id="asstComposer"', 'id="asstWelcome"', 'id="asstInput"', 'id="asstSidebar"'):
            self.assertIn(hook, html)
        self.assertIn("What do you want to learn today?", html)
        self.assertIn("I explain at your class level", html)
        self.assertEqual(html.count('class="asst-suggestion"'), 5)
        self.assertIn('class="app-brand" href="/"', html)
        self.assertIn('src="/static/learnova-logo.svg"', html.split('id="asstWelcome"', 1)[1])
        self.assertNotIn('id="uploadForm"', html, "the lesson builder is no longer the home page")
        self.assertNotIn('class="global-footer"', html, "no footer under the chat")
        self.assertIn('content="width=device-width, initial-scale=1, interactive-widget=resizes-content"', html)
        self.assertIn('data-home-path="/"', html)
        self.assertIn('data-initial-conversation=""', html)

    def test_a_suggestion_prefills_and_never_sends_and_the_photo_one_opens_the_picker(self):
        self.register()
        html = self.page("/")
        suggestions = re.findall(r'<button type="button" class="asst-suggestion"([^>]*)>([^<]+)</button>', html)
        self.assertEqual([label for _attrs, label in suggestions], [
            "Explain a difficult topic", "Help me solve this exercise", "Explain this textbook page",
            "Test my knowledge", "Help me prepare for an exam"])
        self.assertTrue(all("data-prompt=" in attrs for attrs, _label in suggestions))
        self.assertIn('data-action="photo"', suggestions[2][0])
        application.app.config["FEATURE_CHAT_IMAGES"] = False
        self.assertEqual(self.page("/").count('class="asst-suggestion"'), 4, "no photo suggestion without photos")

    def test_a_running_lesson_and_a_handed_over_goal_keep_the_lesson_page_at_the_root(self):
        self.register()
        for path in ("/?goal=Bruchrechnung", "/?session_id=not-mine"):
            with self.subTest(path):
                html = self.page(path)
                self.assertIn('id="uploadForm"', html)
                self.assertNotIn('id="asstComposer"', html)

        def route(**_kwargs):
            class Reply:
                usage = None
                model = "test-model"
                output_text = json.dumps(LESSON)
            return Reply()

        with mock.patch.object(application, "create_response", route):
            started = self.client.post("/api/analyze", data={"study_goal": "Potenzgesetze", "subject": "Mathematics"})
        self.assertEqual(started.status_code, 200, started.get_data(as_text=True)[:300])
        html = self.page(f"/?session_id={started.get_json()['session_id']}")
        self.assertIn('id="lessonBootstrap"', html)
        self.assertNotIn('id="asstComposer"', html)
        self.assertIn('data-tour-page="index"', html)

    def test_short_lesson_is_the_builder_with_its_own_active_nav_item(self):
        self.register()
        html = self.page("/lesson")
        self.assertIn('class="app-nav-link nav-primary-action is-active" href="/lesson" aria-current="page"', html)
        self.assertEqual(html.count('id="uploadForm"'), 1)
        self.assertIn('class="global-footer"', html)
        self.assertNotIn('interactive-widget', html)
        self.assertIn('data-tour-page="index"', html)
        self.assertNotIn(f'class="app-nav-link is-active" {CHAT_LINK}', html)
        rules = [rule.rule for rule in application.app.url_map.iter_rules() if rule.endpoint == "short_lesson"]
        self.assertEqual(rules, ["/lesson"])

    def test_signed_out_visitors(self):
        self.assertIn("/about", self.client.get("/").headers["Location"])
        response = self.client.get("/lesson")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])
        self.assertIn("next=/lesson", response.headers["Location"].replace("%2F", "/"))

    def test_switched_off_the_root_is_the_lesson_builder_and_chat_is_gone(self):
        application.app.config["FEATURE_ASSISTANT_CHAT"] = False
        self.register()
        html = self.page("/")
        self.assertIn('id="uploadForm"', html)
        self.assertNotIn(CHAT_LINK, html)
        self.assertNotIn(">Chat<", html)
        self.assertEqual(self.client.get("/assistant").status_code, 404)
        self.assertIn('id="uploadForm"', self.page("/lesson"))


class NavigationTests(HomeCase):
    def test_chat_leads_the_navigation_and_is_active_on_every_chat_address(self):
        self.register()
        conversation = self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]
        for path in ("/", "/assistant", f"/assistant/c/{conversation['id']}"):
            with self.subTest(path):
                html = self.page(path)
                nav = self.desktop_nav(html)
                self.assertLess(nav.index(CHAT_LINK), nav.index('href="/dashboard"'))
                self.assertIn(f'class="app-nav-link is-active" {CHAT_LINK} aria-current="page"', nav)
                self.assertIn('href="/lesson"', nav)
                self.assertIn('href="/projects"', nav)
                mobile = html.split('id="mobileAppMenu"', 1)[1].split("</nav>", 1)[0]
                self.assertLess(mobile.index(">Chat<"), mobile.index("Overview"))
                for label in ("New Lesson", "Project Mode", "Today&#39;s Practice", "Settings", "Log out"):
                    self.assertIn(label, mobile)
                self.assertNotIn(">Assistant<", html, "the duplicate More-menu entry is gone")
                self.assertIn('data-tour-page="chat"', html)

    def test_settings_profile_premium_language_and_logout_stay_on_the_chat_page(self):
        self.register()
        html = self.page("/")
        for marker in ('aria-controls="profileMenu"', 'class="settings-link', '#language', 'action="/logout"',
                       'data-theme-toggle', 'data-tutorial-link', 'href="/profile"'):
            self.assertIn(marker, html, marker)

    def test_other_pages_link_new_lessons_to_short_lesson(self):
        application.app.config.update(FEATURE_GAMIFICATION=True, FEATURE_LEAGUES=True)
        self.register()
        for path in ("/dashboard", "/practice/today", "/leaderboard"):
            with self.subTest(path):
                html = self.page(path)
                main = html.split('id="main-content"', 1)[-1] if 'id="main-content"' in html else html
                self.assertRegex(main, r'class="(primary|secondary)-button" href="/lesson"')
        for name in ("dashboard.html", "leaderboard.html", "todays_practice.html"):
            self.assertNotIn("url_for('index')", (ROOT / "templates" / name).read_text(encoding="utf-8"), name)

    def test_the_tour_knows_which_page_it_is_on(self):
        self.register()
        # /projects takes its key from the endpoint now (tutorial_page_key; PLAN 8.4).
        expected = {"/": "chat", "/assistant": "chat", "/lesson": "index", "/?goal=x": "index",
                    "/dashboard": "dashboard", "/projects": "projects"}
        for path, key in expected.items():
            with self.subTest(path):
                self.assertIn(f'data-tour-page="{key}"', self.page(path))
        rules = (ROOT / "static/js/tour-rules.js").read_text(encoding="utf-8")
        self.assertIn('if (path === "/lesson") return "index";', rules)


class ChatPageMarkupTests(HomeCase):
    def test_every_hook_the_script_needs_is_on_the_page(self):
        self.register()
        html = self.page("/assistant")
        for hook in ("asstSidebar", "asstSidebarToggle", "asstDrawerToggle", "asstScrim", "asstMain", "asstNew",
                     "asstHeadNew", "asstSearch", "asstList", "asstListEmpty", "asstLoadMore", "asstShowArchived",
                     "asstArchivedList", "asstTools", "asstThread", "asstWelcome", "asstScrollDown", "asstDropZone",
                     "asstComposer", "asstAttachTray", "asstCamera", "asstGallery", "asstInput", "asstDeep", "asstCount",
                     "asstStop", "asstSend", "asstItemMenu", "asstRename", "asstArchive", "asstDelete",
                     "asstRenameDialog", "asstRenameForm", "asstRenameInput", "asstDeleteDialog", "asstDeleteHeading",
                     "asstDeleteCancel", "asstDeleteArchive", "asstDeleteConfirm", "asstThreadTitle", "asstThreadMeta",
                     "asstPreset", "asstToast"):
            self.assertIn(f'id="{hook}"', html, hook)

    def test_the_row_menu_and_the_dialogs_are_accessible(self):
        self.register()
        html = self.page("/assistant")
        menu = html.split('id="asstItemMenu"', 1)[1].split("</div>", 1)[0]
        self.assertIn('role="menu"', html.split('id="asstItemMenu"', 1)[1][:120])
        self.assertEqual(menu.count('role="menuitem"'), 3)
        self.assertLess(menu.index("Rename"), menu.index("Archive"))
        self.assertLess(menu.index("Archive"), menu.index("Delete"))
        self.assertIn('<dialog id="asstDeleteDialog"', html)
        self.assertIn('aria-labelledby="asstDeleteHeading"', html)
        dialog = html.split('<dialog id="asstDeleteDialog"', 1)[1].split("</dialog>", 1)[0]
        self.assertIn("permanently deletes the conversation with all its messages and photos", dialog)
        self.assertIn("It cannot be undone.", dialog)
        self.assertIn("Archive instead", dialog)
        self.assertIn("Delete permanently", dialog)
        self.assertIn('id="asstDeleteCancel" autofocus', dialog, "Cancel is the safe default")
        self.assertIn('data-template="Delete “{title}”?"', dialog)
        self.assertIn('maxlength="120"', html.split('<dialog id="asstRenameDialog"', 1)[1].split("</dialog>", 1)[0])
        for control in ("asstSidebarToggle", "asstDrawerToggle", "asstShowArchived"):
            tag = html.split(f'id="{control}"', 1)[1].split(">", 1)[0]
            self.assertIn("aria-expanded=", tag, control)
            self.assertIn("aria-controls=", tag, control)

    def test_the_chat_script_carries_the_requests_nonce(self):
        self.register()
        response = self.client.get("/")
        nonce = re.search(r"'nonce-([^']+)'", response.headers["Content-Security-Policy"]).group(1)
        html = response.get_data(as_text=True)
        self.assertIn(f'<script nonce="{nonce}" type="module" src="/static/js/assistant.js"></script>', html)
        for script in re.findall(r"<script\b[^>]*>", html):
            self.assertIn(f'nonce="{nonce}"', script, script)

    def test_a_deep_link_opens_its_conversation_and_new_chat_returns_to_the_assistant(self):
        self.register()
        conversation = self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]
        html = self.page(f"/assistant/c/{conversation['id']}")
        self.assertIn(f'data-initial-conversation="{conversation["id"]}"', html)
        self.assertIn('data-home-path="/assistant"', html)
        self.assertIn('data-home-path="/assistant"', self.page("/assistant"))

    def test_continue_learning_switch_reaches_the_page(self):
        self.register()
        self.assertIn('data-continue-learning="on"', self.page("/"))
        application.app.config["FEATURE_CONTINUE_LEARNING"] = False
        self.assertIn('data-continue-learning="off"', self.page("/"))

    def test_no_model_name_or_timing_under_replies(self):
        script = (ROOT / "static/js/assistant.js").read_text(encoding="utf-8")
        self.assertNotIn("message.model", script)
        self.assertNotIn("latency_ms", script)
        self.assertNotIn("asstNoModelYet", script)

    def test_german_chat_page(self):
        self.register(language="de")
        html = self.page("/")
        # "Neue Lektion": the sidebar names the short lesson as the navigation bar does.
        for german in ("Was möchtest du heute lernen?", "Neuer Chat", "Senden", "Neue Lektion", "Unterhaltungen",
                       ">Chat<", "Endgültig löschen", "Lieber archivieren"):
            self.assertIn(german, html, german)
        self.assertIn('"asstGroupYesterday": "Gestern"', html)


class ReviewWordingTests(HomeCase):
    """What the pages say after the review: honest photo retention, one name for the short
    lesson, the interface language by its own name, /lesson's own headline, fair use."""

    def test_the_photo_note_says_the_pictures_are_deleted_and_the_reading_kept(self):
        self.register()
        application.app.config["CHAT_ATTACHMENT_RETENTION_DAYS"] = 21
        try:
            html = self.page("/")
        finally:
            application.app.config["CHAT_ATTACHMENT_RETENTION_DAYS"] = 30
        self.assertIn("the photos themselves are deleted after 21 days", html)
        self.assertNotIn("kept with this conversation", html)
        self.assertIn("Archive it instead to keep its messages.", html)
        self.assertNotIn("Archive it instead to keep it.", html)

    def test_the_sidebar_names_the_short_lesson_as_the_navigation_bar_does(self):
        self.register()
        html = self.page("/")
        tools = html.split('id="asstTools"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('href="/lesson"', tools)
        self.assertIn("New Lesson", tools)
        self.assertIn("New Lesson", self.desktop_nav(html))
        self.assertNotIn("Short lesson", html)

    def test_the_language_entry_names_the_interface_language_itself(self):
        for language, native in (("fr", "Français"), ("es", "Español"), ("de", "Deutsch"), ("en", "English")):
            with self.subTest(language=language):
                self.client = application.app.test_client()
                self.register(name=f"lang{language}", language=language)
                html = self.page("/")
                entries = re.findall(r'#language">[^<]*· ([^<]+)</a>', html)
                self.assertEqual(entries, [native, native], "desktop and phone menus")

    def test_the_short_lesson_page_has_its_own_headline(self):
        self.register()
        html = self.page("/lesson")
        headline = html.split('<section id="uploadView"', 1)[1].split("<h1>", 1)[1].split("</h1>", 1)[0]
        self.assertNotIn("What do you want to", headline)
        self.assertIn("Build a short lesson", headline)
        self.assertIn('data-i18n="heroLessonTitle"', headline)
        self.assertEqual(catalog.FRONTEND_MESSAGES["heroLessonTitle"], "Build a short lesson")

    def test_the_about_page_promises_no_unlimited_ai(self):
        html = self.page("/about")
        self.assertIn("Fair-use limits, such as a maximum number of chat messages per hour, apply to every account, "
                      "Premium included", html)

    def test_french_and_spanish_home_texts_are_corrected(self):
        for language, good, bad in (("fr", "L&#39;assistant peut se tromper. Vérifie tout ce qui compte.", "L assistant"),
                                    ("es", "Puedes reintentar.", "reintentaar")):
            with self.subTest(language=language):
                self.client = application.app.test_client()
                self.register(name=f"home{language}", language=language)
                html = self.page("/")
                if language == "fr":
                    self.assertIn(good, html)
                self.assertNotIn(bad, html)
        self.assertEqual(FR["AI is temporarily unavailable. You can retry. Your saved work remains safe."],
                         "L'IA est temporairement indisponible. Tu peux réessayer. Ton travail enregistré est en sécurité.")
        self.assertNotIn("reintentaar", json.dumps(ES, ensure_ascii=False))
        self.assertTrue(ES["AI is temporarily unavailable. You can retry. Your saved work remains safe."].startswith(
            "La IA no está disponible"))


class FailedTurnTests(HomeCase):
    def test_a_failed_turn_says_which_kind_it_was(self):
        """The page offers "Send a new photo" instead of "Retry" for an unreadable photo."""

        self.register()
        conversation = self.client.post("/api/assistant/conversations", json={}).get_json()["conversation"]
        with application.app.app_context():
            for category in ("image_unreadable", "provider_timeout", ""):
                application.db.session.add(application.ConversationMessage(
                    conversation_id=conversation["id"], role="assistant", content="x", error_category=category))
            application.db.session.commit()
        messages = self.client.get(f"/api/assistant/conversations/{conversation['id']}").get_json()["conversation"]["messages"]
        self.assertEqual([item.get("error_code") for item in messages], ["image_unreadable", "provider_timeout", None])
        self.assertEqual([item.get("error", False) for item in messages], [True, True, False])


class TranslationTests(unittest.TestCase):
    def test_every_chat_and_navigation_msgid_is_translated(self):
        found = msgids("assistant.html", "components/_navbar.html", "components/_tour.html")
        self.assertGreater(len(found), 80)
        for msgid in sorted(found):
            for language, table in (("de", catalog.GERMAN), ("fr", FR), ("es", ES)):
                self.assertIn(msgid, table, f"{language}: {msgid!r}")
                self.assertEqual(sorted(PLACEHOLDER.findall(msgid)), sorted(PLACEHOLDER.findall(table[msgid])),
                                 f"{language} placeholders: {msgid!r}")

    def test_every_chat_script_string_reaches_the_browser_translated(self):
        keys = chat_js_keys()
        self.assertGreater(len(keys), 40)
        missing = sorted(key for key in keys if key not in catalog.FRONTEND_MESSAGES)
        self.assertEqual(missing, [])
        # Each key's English text has an entry in every catalogue (some words are the same in
        # French - "Assistant" - so equality with English is not the test; a missing entry is).
        for code, table in (("de", catalog.GERMAN), ("fr", FR), ("es", ES)):
            strings = catalog.frontend_catalog(code)
            for key in sorted(keys):
                with self.subTest(code=code, key=key):
                    english = catalog.FRONTEND_MESSAGES[key]
                    self.assertIn(english, table, f"{code} has no translation for {key}: {english!r}")
                    self.assertEqual(sorted(PLACEHOLDER.findall(english)), sorted(PLACEHOLDER.findall(strings[key])))
        self.assertEqual(catalog.frontend_catalog("de")["asstContinue"], "Weiterlernen: {topic}")

    def test_retired_chat_strings_are_gone_from_every_catalogue(self):
        """WP5 left five script strings no page uses any more (the rename and delete prompts
        became dialogs, Unarchive became Restore, the model line and the old lesson offer
        went). Removed from FRONTEND_MESSAGES and, since nothing else uses their English, from
        every language. asstDeleteConfirm stays: the same word is the delete dialog's button
        id, so a search cannot tell the two apart."""

        retired = {"asstRenamePrompt": "Name this conversation", "asstUnarchive": "Unarchive",
                   "asstNoModelYet": "No reply yet", "asstLessonOffer": "Learn this with practice questions?",
                   "asstLessonStart": "Start short lesson"}
        tables = {"de": catalog.GERMAN}
        for code in ("fr", "es", "ar"):
            tables[code] = json.loads((ROOT / f"learnova/translations/data/{code}.json").read_text(encoding="utf-8"))
        sources = "\n".join(path.read_text(encoding="utf-8") for pattern in ("static/**/*.js", "templates/**/*.html")
                            for path in ROOT.glob(pattern))
        sources += (ROOT / "app.py").read_text(encoding="utf-8")
        for key, english in retired.items():
            with self.subTest(key=key):
                self.assertFalse(key in catalog.FRONTEND_MESSAGES, f"{key} is still in FRONTEND_MESSAGES")
                self.assertFalse(key in sources, f"{key} is used by a page")
                self.assertFalse(english in sources, f"{english!r} is used by a page")
                for code, table in tables.items():
                    self.assertFalse(english in table, f"{code} keeps an orphaned translation of {english!r}")
        self.assertIn("asstDeleteConfirm", catalog.FRONTEND_MESSAGES)


if __name__ == "__main__":
    unittest.main()

"""The guided tutorial in a real browser: the real templates, stylesheets, tour.js and the
real Flask app with the AI patched (scripts/mobile_audit.start_server).

Two layers (docs/TUTORIAL.md): the quick tour (one step per function at its place in the
navigation) and feature chapters - a few tips on one page that start by themselves the
first time their anchor has been on screen for a moment, once, and never again after Skip
or Finish. A walk starts a section the way the hub does ([data-tutorial-start]) on the
section's own page and then does what a student does: Next on every step, the real opener
when a target hides in a menu or drawer, the real action on action steps. On every lit
step the test checks against its OWN selector list (EXPECTED, written independently of the
registry) that the lit element is the real one, inside the window and not covered.

AutoStartTests drives the chapters that start by themselves (once, not after Skip or
Finish, not with the switch off, not during the offer, a dialog, a generation or typing,
one per page load); HubTests presses the hub's buttons for real; ReviewFixTests keeps the
browser checks of the 2026-11-04 reviews (live region, dark contrast, tall targets, names,
focus, the account tag on the tab's storage).

Skipped when Playwright or a Chromium-based browser is missing (Store Python has no
Playwright: run with R:/ai_tutor/.venv/Scripts/python).
"""

import json
import os
import re
import socket
import tempfile
import time
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_tutorial_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None
    PlaywrightError = Exception

from tests.js_browser import harness  # noqa: E402

BROWSER = {}
PASSWORD = "correct-horse-battery"
QUESTION = "Wie löse ich die Gleichung x^2 - 2x - 3 = 0? Ich verstehe die Lösungsformel nicht."
REPLY = ("**Gut!** Bei \\(x^2 - 2x - 3 = 0\\) hilft die Lösungsformel:\n\n"
         "\\[\nx_{1,2} = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}\n\\]\n\n"
         "Welche Lösungen hat die Gleichung?\nCHIPS: x = 3 und x = -1 | x = 1\nTOPIC: Mathematics | Quadratische Gleichungen")
OLD_MISSING = ("This appears once you have used it for the first time.",
               "Das erscheint, sobald du es zum ersten Mal benutzt hast.")

# The test's own oracle: what each step must light (a selector the lit node matches, and
# optionally a JS predicate on it). Deliberately not read from the registry.
NEWEST_REPLY = "newest-reply"   # a check LIT_JS knows by name (the page's CSP forbids eval)
EXPECTED = {
    # The quick tour: the real entry points of the navigation.
    "overview-chat": "a[href='/assistant']",
    "overview-dashboard": "a[href='/dashboard']",
    "overview-lesson": "a[href='/lesson']",
    "overview-projects": "a[href='/projects']",
    "overview-flashcards": "a[href='/flashcards']",
    "overview-practice": "a[href='/practice/today']",
    "overview-community": "a[href='/community']",
    "overview-profile": "a[href='/profile']",
    "overview-leagues": "a[href='/leaderboard']",
    "overview-settings": "a.settings-link",
    "overview-xp": "a.xp-chip",
    "overview-done": "a[href='/tutorial']",
    # Chat.
    "chat-input": "#asstInput",
    "chat-photo": ".asst-attach",
    "chat-send": "#asstSend, #asstStop",
    "chat-style": "#asstPreset",
    "chat-reply": (".asst-msg-assistant", NEWEST_REPLY),
    "chat-tools": ".asst-msg-assistant .asst-actions",
    "chat-chips": ".asst-chips",
    "chat-continue": ".asst-continue",
    "chat-history-list": "#asstSidebar .asst-lists",
    "chat-menu": ".asst-row .asst-row-menu",
    "chat-new": "#asstNew, #asstHeadNew",
    # Lessons.
    "lessons-subject": "#uploadForm fieldset.subject-picker",
    "lessons-goal": "label.goal-field",
    "lessons-photos": "#attachmentButton",
    "lessons-build": "#uploadForm button.begin-button",
    "lessons-explanation": ".explanation-card, #lessonParts",
    "lessons-level": "#levelBadge",
    "lessons-listen": "#explanationListenControl, #partsListenControl",
    "lessons-tutor": "#chatToggle",
    "lessons-adapt": "#chatPanel .chat-controls",
    "more-microphone": "#chatPanel #chatMicrophoneControl",
    "lessons-start-test": "#startTest",
    "lessons-question": "#questionCard",
    "lessons-check": "#answerForm button[type=submit]",
    "lessons-confidence": "#answerForm fieldset.response-confidence",
    # Project Mode: the projects page, the draft (stage B), the learned project (stage C).
    "projects-create": "#projectCreateForm div.form-pair",
    "projects-upload": "label.document-input-option:has(#imageUploadInput)",
    "projects-topics-only": "details.project-more-options",
    "projects-create-free": "button#createProjectButton",
    "projects-topic-review": "section#topics:has(#confirmTopics) h2#topicsTitle",
    "projects-edit-topics": "section#topics:has(#confirmTopics) details.pm-edit",
    "projects-start-learning": "#confirmTopics button.pm-primary[type=submit]",
    "projects-next": "#overview button.pm-primary, section#overview",
    "projects-today": "section#today",
    "projects-exercises": "section#exercises h2#exercisesTitle",
    "projects-mock-exam": "#tests form[action$='/practice-exam'] button",
    "projects-progress": "section#progress",
    "projects-revision": "section#revision",
    "projects-plan": "section#plan",
    "projects-materials": "section#materials",
    "projects-review": "#reviewPageList > article.recognition-page-card",
    "projects-day-done": "section.session-completion",
    # Learning and practice.
    "practice-library": ".library-page .cards-toolbar",
    "practice-create": ".header-actions a[href='/flashcards/create']",
    "practice-import": ".header-actions a[href='/flashcards/import']",
    "practice-vocabulary": ".header-actions a[href='/vocabulary/import']",
    "practice-set": "a#ovStudy",
    "practice-edit": "a#ovEdit",
    "practice-modes": ".overview-page .mode-grid",
    "community-publish": "a#ovPublish",
    "practice-study": "section#modeConfig",
    "practice-start": "button#startMode",
    "practice-star": "#modePlay button#modeStar",
    "practice-today": ".practice-today-shell .today-grid",
    "practice-insights": ".mistake-intel > .cards-header",
    "more-overview": "section.ln-command-center",
    "lessons-saved": "article.sessions-card",
    "practice-mistakes": "article#mistake-notebook",
    "practice-progress": "section.progress-hero",
    "practice-goal": "form#dailyGoalForm",
    # Profile and rewards.
    "profile-hero": "section.profile-hero",
    "profile-challenges": "section.challenge-board",
    "profile-avatars": "section#avatars > header",
    "profile-league": "a.league-card",
    "leagues-ladder": "ol.league-ladder",
    "leagues-standing": "section.league-status, section.league-empty",
    # Community.
    "community-search": "form#commFilters",
    "community-discover": "section.community-discovery",
    "community-ratings": "#commGrid .community-card",
    "community-study": "#commDetail button#detailStudy",
    "community-rate": "#rateWidget, #commDetail button#detailStudy",
    "community-report": "#commDetail button#detailReport",
    # Premium and account.
    "account-language": "form[action='/settings/language']",
    "account-grade": "form[action='/settings/grade']",
    "profile-username": "input#new_username",
    "profile-public": "label.toggle-row:has(input[name=profile_public])",
    "profile-leagues-optout": "label.toggle-row:has(input[name=leaderboard_enabled])",
    "account-theme": "button[data-theme-toggle]",
    "account-status-free": "#subscription .sub-head",
    "account-compare": "#subscription .sub-plans",
    "account-code": "#subscription .sub-activate",
}
# Steps whose real action the walker performs instead of pressing Next.
DO_IT = {"lessons-start-test", "practice-start", "lessons-tutor", "chat-menu"}
# Steps that may be skipped silently for the seeded student: read-aloud needs a speech engine
# the test browser may lack; Continue learning needs a longer talk.
MAY_SKIP = {"lessons-listen", "chat-continue", "more-microphone"}
# Where each chapter is walked: a path, or "@token" = the student's own record.
WHERE = {"overview": "/dashboard", "chat-basics": "@chat_thread", "chat-answers": "@chat_thread",
         "chat-history": "@chat_thread", "lesson-start": "/lesson", "lesson-inside": "@lesson",
         "lesson-tutor": "@lesson", "lesson-test": "@lesson", "projects-start": "/projects",
         "project-draft": "@project_draft", "project-workspace": "@project", "project-progress": "@project",
         "project-review": "@project_review", "study-session": "@study_session", "flashcards-library": "/flashcards",
         "flashcard-set": "@flashcard_set", "flashcard-study": "@flashcard_study", "todays-practice": "/practice/today",
         "mistake-insights": "/insights/mistakes", "dashboard": "/dashboard", "progress": "/progress",
         "my-profile": "/profile", "leagues": "/leaderboard", "community-library": "/community",
         "community-set": "@community_set", "settings": "/settings", "settings-plan": "/settings"}
# What each chapter's student owns besides the lesson and the chat (TutorialBrowserCase.own).
OWNS = {"project-draft": ("projects",), "project-workspace": ("projects",), "project-progress": ("projects",),
        "project-review": ("projects",), "study-session": ("projects",), "flashcard-set": ("cards",),
        "flashcard-study": ("cards",), "community-library": ("community",), "community-set": ("community",)}

STATE_JS = """() => {
  const root = document.getElementById('tourRoot');
  if (!root) return null;
  const next = root.querySelector('[data-tour-next]');
  return {mode: root.dataset.tourMode || '', step: root.dataset.tourStep || '', section: root.dataset.tourSection || '',
          hidden: root.hidden, next: next.textContent.trim(), skip: root.querySelector('[data-tour-skip]').textContent.trim(),
          prevDisabled: root.querySelector('[data-tour-prev]').disabled,
          count: (root.querySelector('#tourProgress .tour-count') || {}).textContent || '',
          lit: [...document.querySelectorAll('[data-tour-lit]')].map(n => n.id || n.className || n.tagName).join(' + '),
          title: root.querySelector('#tourTitle').textContent, text: root.querySelector('#tourText').textContent,
          note: root.querySelector('#tourNote').textContent, path: location.pathname + location.search,
          choices: [...root.querySelectorAll('#tourChoices [data-tour-action]')].map(n => n.dataset.tourAction)};
}"""
LIT_JS = """([selector, predicate]) => {
  // The step's own target among the lit nodes (an `also` node may come first in the page).
  const all = [...document.querySelectorAll('[data-tour-lit]')];
  const node = all.find(n => n.matches(selector)) || all[0];
  if (!node) return {ok: false, why: 'nothing lit'};
  const checks = {
    'newest-reply': (n) => [...document.querySelectorAll('#asstThread .asst-msg-assistant:not(.asst-pending)')].pop() === n,
  };
  const matches = node.matches(selector) && (!predicate || checks[predicate](node));
  const r = node.getBoundingClientRect();
  const vw = innerWidth, vh = innerHeight;
  const s = document.querySelector('.tour-spot').getBoundingClientRect();
  // The part of the node inside the ring: a section taller than the screen is lit from its
  // top (tour.js fitRect), and its rest is meant to stay dimmed.
  const left = Math.max(r.left, 0, s.left), right = Math.min(r.right, vw, s.right);
  const top = Math.max(r.top, 0, s.top), bottom = Math.min(r.bottom, vh, s.bottom);
  if (right - left < 2 || bottom - top < 2) return {ok: false, why: 'off screen', rect: [r.left, r.top, r.width, r.height], vw, vh};
  const x = (left + right) / 2, y = (top + bottom) / 2;
  const hit = document.elementFromPoint(x, y);
  const covered = !(hit && (hit === node || node.contains(hit)));
  const spotInside = s.left >= 0 && s.top >= 0 && s.right <= vw && s.bottom <= vh;
  const name = (n) => n ? (n.id ? '#' + n.id : '') + '.' + String(n.className || n.tagName) : 'none';
  return {ok: matches && !covered && spotInside, matches, covered, spotInside, hit: name(hit), node: name(node),
          rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)], vw, vh};
}"""


def setUpModule():
    if sync_playwright is None:
        raise unittest.SkipTest("Playwright is not installed (run with R:/ai_tutor/.venv/Scripts/python)")
    playwright = sync_playwright().start()
    try:
        BROWSER["browser"] = harness.launch(playwright)
    except SystemExit as error:
        playwright.stop()
        raise unittest.SkipTest(str(error))
    BROWSER["playwright"] = playwright


def tearDownModule():
    if BROWSER:
        BROWSER["browser"].close()
        BROWSER["playwright"].stop()
        BROWSER.clear()


class TutorialBrowserCase(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        if not BROWSER:
            raise unittest.SkipTest("no browser")
        cls.audit = harness._audit_module()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        cls.application, cls.server = cls.audit.start_server(port)
        cls.base = f"http://127.0.0.1:{port}"
        cls.application.create_response.side_effect = cls.fake
        # A student here makes a second project in the same week (a draft and a learned one).
        cls.saved_quota = cls.application.app.config.get("SUBSCRIPTION_QUOTAS_ENFORCED")
        cls.application.app.config["SUBSCRIPTION_QUOTAS_ENFORCED"] = False

    @classmethod
    def tearDownClass(cls):
        cls.application.app.config["SUBSCRIPTION_QUOTAS_ENFORCED"] = cls.saved_quota
        cls.server.shutdown()

    @classmethod
    def fake(cls, **kwargs):
        if kwargs.get("task_type") != "assistant_chat":
            return cls.audit._fake_response(**kwargs)

        class Usage:
            input_tokens = output_tokens = total_tokens = 10

        class Reply:
            usage = Usage()
            model = "test-model"
            output_text = REPLY
        return Reply()

    # ------------------------------------------------------------------ helpers

    def context(self, width, height, *, theme="light", reduced=False):
        mobile = width <= 430
        options = {"viewport": {"width": width, "height": height}, "is_mobile": mobile, "has_touch": mobile}
        if reduced:
            options["reduced_motion"] = "reduce"
        context = BROWSER["browser"].new_context(**options)
        context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
        context.add_init_script(harness.KATEX_STUB)
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        return context

    def student(self, width=1280, height=900, *, theme="light", reduced=False, settle=True, seed=True, grade="8",
                tips=False, language="en"):
        """A new signed-in plain student and a page. `seed`: a saved lesson and a chat with a
        reply (and an older one). `tips=False` switches automatic tips off, so a walk is
        never interrupted by a chapter starting by itself."""

        context = self.context(width, height, theme=theme, reduced=reduced)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        name = f"t{uuid.uuid4().hex[:10]}"
        response = page.request.post(f"{self.base}/register", form={
            "username": name, "email": f"{name}@example.com", "password": PASSWORD, "grade": grade,
            "language": language})
        self.assertLess(response.status, 400)
        if grade:
            page.request.post(f"{self.base}/settings/grade", form={"grade": grade})
        if settle:
            page.request.post(f"{self.base}/api/tour/complete")
        if not tips:
            page.request.post(f"{self.base}/tutorial/tips", form={"back": "hub"})
        self.urls = {}
        if seed:
            lesson = page.request.post(f"{self.base}/api/analyze", multipart={"study_goal": "Potenzgesetze",
                                                                            "subject": "Mathematics"})
            self.assertTrue(lesson.ok, lesson.text()[:300])
            json_headers = {"Content-Type": "application/json"}
            old = page.request.post(f"{self.base}/api/assistant/conversations", data=json.dumps({}),
                                    headers=json_headers).json()["conversation"]["id"]
            page.request.post(f"{self.base}/api/assistant/conversations/{old}/messages",
                              data=json.dumps({"message": "Was ist eine Potenz?"}), headers=json_headers)
            created = page.request.post(f"{self.base}/api/assistant/conversations", data=json.dumps({}),
                                        headers=json_headers)
            conversation = created.json()["conversation"]["id"]
            sent = page.request.post(f"{self.base}/api/assistant/conversations/{conversation}/messages",
                                     data=json.dumps({"message": QUESTION}), headers=json_headers)
            self.assertTrue(sent.ok, sent.text()[:300])
            self.urls["thread"] = f"/assistant/c/{conversation}"
        return page

    def own(self, page, *what):
        """More of the student's own records: "cards" a flashcard set, "projects" a learned
        project with a plan (an uploaded page, real routes, AI patched) and a draft whose typed
        topics wait for a check, "community" a set someone else published."""

        json_headers = {"Content-Type": "application/json"}
        if "cards" in what:
            cards = [{"type": "question_answer", "front": f"Begriff {n}", "back": f"Erklärung {n}"} for n in range(1, 6)]
            created = page.request.post(f"{self.base}/api/flashcards/sets", headers=json_headers, data=json.dumps(
                {"title": "Physik – Bewegung", "subject": "Physics", "cards": cards}))
            self.assertTrue(created.ok, created.text()[:300])
            self.urls["set"] = f"/flashcards/{created.json()['id']}"
        if "projects" in what:
            exam = (date.today() + timedelta(days=12)).isoformat()
            created = page.request.post(f"{self.base}/projects", max_redirects=0, multipart={
                "subject": "Physics", "title": "Physik – Mechanik", "exam_date": exam,
                "materials": {"name": "notes.png", "mimeType": "image/png", "buffer": self.audit._note_png()}})
            project_id = re.search(r"/projects/(\d+)", created.headers.get("location", "")).group(1)
            page.request.post(f"{self.base}/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
            confirmed = page.request.post(f"{self.base}/projects/{project_id}/topics/confirm", form={"choice": "auto"})
            self.assertTrue(confirmed.ok, confirmed.text()[:300])
            draft = page.request.post(f"{self.base}/projects", max_redirects=0, form={
                "subject": "Physics", "title": "Physik – Wellen", "topics": "Schwingungen\nWellen\nSchall"})
            self.assertIn("/projects/", draft.headers.get("location", ""))
            self.urls["project"] = f"/projects/{project_id}"
        if "community" in what:
            app = self.application
            with app.app.app_context():
                author = app.User(username=f"p{uuid.uuid4().hex[:10]}", email=f"{uuid.uuid4().hex[:8]}@example.com")
                author.set_password(PASSWORD)
                app.db.session.add(author)
                app.db.session.flush()
                cards = [{"type": "question_answer", "front": "Geschwindigkeit", "back": "v = s / t"},
                         {"type": "question_answer", "front": "Beschleunigung", "back": "a = Δv / Δt"}]
                public = app.PublicFlashcardSet(
                    creator_id=author.id, title="Bewegung", subject="Physics", status="approved",
                    moderation_decision="allow", card_count=len(cards), cards_json=json.dumps(cards),
                    ai_overall=4.2, ai_stars=4, ranking_score=1.0, published_at=app.utcnow())
                app.db.session.add(public)
                app.db.session.flush()
                version = app.FlashcardPublicationVersion(
                    public_set_id=public.id, version=1, title=public.title, subject=public.subject,
                    cards_json=public.cards_json, card_count=len(cards), submission_status="approved")
                app.db.session.add(version)
                app.db.session.flush()
                public.active_version_id = version.id
                app.db.session.commit()

    def address(self, page, where):
        if not where.startswith("@"):
            return where
        url = self.progress(page)["context"][where[1:]]
        self.assertTrue(url, f"the student has no {where}")
        return url

    def set_run(self, page, section, step):
        """A stored run of THIS account (tour.js ignores one another account left)."""

        page.evaluate("""([section, step]) => sessionStorage.setItem('learnova.tutorial.run', JSON.stringify(
            {v: 5, section, step, auto: false, u: document.getElementById('tourRoot').dataset.tutorialUser}))""",
                      [section, step])

    def state(self, page):
        for _ in range(30):
            try:
                return page.evaluate(STATE_JS)
            except PlaywrightError:   # the page is navigating
                time.sleep(0.15)
        self.fail("the page never settled")

    def settled(self, page, timeout=9.0, previous=None):
        """The tour's state once it stopped changing (no hold/pending, same step 3 polls)."""

        deadline = time.monotonic() + timeout
        last, same = None, 0
        while time.monotonic() < deadline:
            current = self.state(page)
            key = current and (current["mode"], current["step"], current["path"], current["lit"])
            if current and current["mode"] not in ("", "loading", "hold", "pending") and key == last:
                same += 1
                if same >= 3 and (previous is None or key != previous):
                    return current
            else:
                same = 0
            last = key
            time.sleep(0.12)
        self.fail(f"the tutorial stalled: {self.state(page)}")

    @staticmethod
    def key(state):
        return state["mode"], state["step"], state["path"], state["lit"]

    def start(self, page, section, path):
        page.goto(f"{self.base}{path}")
        page.wait_for_load_state("domcontentloaded")
        page.evaluate("""(section) => {
          const button = document.createElement('button');
          button.type = 'button'; button.id = 'testHubStart'; button.dataset.tutorialStart = section;
          button.textContent = 'start'; document.querySelector('main, body').appendChild(button); button.focus(); button.click();
        }""", section)
        page.wait_for_function("() => document.getElementById('tourRoot').dataset.tourMode !== 'off'", timeout=8000)

    def wait_mode(self, page, mode, timeout=8000):
        page.wait_for_function("(mode) => document.getElementById('tourRoot').dataset.tourMode === mode", arg=mode,
                               timeout=timeout)
        return self.settled(page)

    def stays_off(self, page, seconds=3.2):
        """Nothing starts by itself for a while (longer than an anchor's dwell + a few ticks)."""

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            state = self.state(page)
            # "loading" = a stored run is being checked (and dropped when it belongs elsewhere).
            self.assertIn(state["mode"], ("off", "", "loading"), f"something started by itself: {state}")
            time.sleep(0.2)

    def payload(self, page):
        return page.request.get(f"{self.base}/api/tutorial/steps").json()

    def progress(self, page):
        return page.request.get(f"{self.base}/api/tutorial/progress").json()

    def status(self, page, section):
        return self.progress(page)["sections"][section]["status"]

    def assert_lit(self, page, step):
        expected = EXPECTED[step]
        selector, predicate = expected if isinstance(expected, tuple) else (expected, None)
        result = None
        for _ in range(12):   # a smooth scroll or a drawer sliding in may still be moving
            result = page.evaluate(LIT_JS, [selector, predicate])
            if result["ok"]:
                return result
            time.sleep(0.15)
        self.fail(f"{step}: the lit element is not the real one, or not visible: {result}")

    def walk(self, page, section, *, phone, start=True):
        """Walk a section from its page to Finish; return the steps seen and lit."""

        if start:
            self.start(page, section, self.address(page, WHERE[section]))
        payload = self.payload(page)
        steps = [step for step in payload["steps"] if step["section"] == section]
        expected_ids = [step["id"] for step in steps
                        if step.get("viewport", "any") in ("any", "phone" if phone else "desktop")]
        seen, spots, previous = [], [], None
        self.log = []
        for _ in range(60):
            current = self.settled(page, previous=previous)
            previous = self.key(current)
            mode, step = current["mode"], current["step"]
            if mode == "off":
                break
            self.log.append((step, mode))
            self.assertEqual(current["section"], section)
            for sentence in OLD_MISSING:
                self.assertNotIn(sentence, current["text"])
            self.assertNotEqual(mode, "elsewhere", f"{step}: a chapter never sends the student elsewhere")
            if step not in seen:
                seen.append(step)
            if mode == "opener":
                page.locator("[data-tour-lit]").first.click()
                continue
            if mode == "spot":
                self.assert_lit(page, step)
                spots.append(step)
                if step in DO_IT:
                    page.locator("[data-tour-lit]").first.click()
                    continue
            if current["next"] == payload["ui"]["finish"]:
                page.click("[data-tour-next]")
                final = self.settled(page)
                self.assertEqual(final["mode"], "off")
                break
            page.click("[data-tour-next]")
        else:
            self.fail(f"{section} never finished: {seen}")
        # A resumed run starts at its step: the ones before it were seen on an earlier page.
        first = expected_ids.index(seen[0]) if seen and seen[0] in expected_ids else 0
        missing = [step for step in expected_ids[first:] if step not in seen]
        self.assertEqual([step for step in missing if step not in MAY_SKIP], [], f"steps never shown in {section}")
        self.assertEqual(self.status(page, section), "completed")
        self.assertEqual(self.errors, [])
        return seen, spots


class OverviewWalkTests(TutorialBrowserCase):
    """The quick tour: every function at its real entry point, on a phone and a computer."""

    def test_quick_tour_phone_light(self):
        page = self.student(375, 812, reduced=True, seed=False)
        seen, spots = self.walk(page, "overview", phone=True)
        self.assertEqual(spots[:4], ["overview-chat", "overview-dashboard", "overview-lesson", "overview-projects"])
        for step in ("overview-flashcards", "overview-practice", "overview-community", "overview-profile",
                     "overview-leagues", "overview-settings", "overview-xp"):
            self.assertIn(step, spots)
        self.assertEqual(self.log[0], ("overview-chat", "opener"), "the phone menu button is lit first")
        self.assertIn("overview-done", seen)
        self.assertLessEqual(len(seen), 12)

    def test_quick_tour_desktop_dark(self):
        page = self.student(1280, 900, theme="dark", seed=False)
        _seen, spots = self.walk(page, "overview", phone=False)
        self.assertEqual(spots[0], "overview-chat", "the desktop bar needs no menu")
        for entry in (("overview-flashcards", "opener"), ("overview-profile", "opener")):
            self.assertIn(entry, self.log, "More, then the Account menu")
        self.assertFalse(page.evaluate("document.querySelector('#moreMenu').classList.contains('is-open')"
                                       " || document.querySelector('#profileMenu').classList.contains('is-open')"),
                         "the menus opened for the tour are closed again")


class ChapterWalkTests(TutorialBrowserCase):
    """Every chapter, phone light (reduced motion); a set of them desktop dark."""

    def check(self, section, width=375, height=812, theme="light", reduced=True):
        page = self.student(width, height, theme=theme, reduced=reduced)
        self.own(page, *OWNS.get(section, ()))
        seen, spots = self.walk(page, section, phone=width <= 820)
        return page, seen, spots

    def test_chat_chapters_phone_light(self):
        for section, lit in (("chat-basics", {"chat-input", "chat-photo", "chat-send", "chat-style"}),
                             ("chat-answers", {"chat-reply", "chat-tools", "chat-chips"}),
                             ("chat-history", {"chat-history-list", "chat-menu", "chat-new"})):
            with self.subTest(section=section):
                _page, _seen, spots = self.check(section)
                self.assertTrue(lit <= set(spots), set(spots))
        self.assertIn(("chat-history-list", "opener"), self.log, "the phone drawer is opened first")

    def test_lesson_chapters_phone_light(self):
        for section, lit in (("lesson-start", {"lessons-subject", "lessons-goal", "lessons-photos", "lessons-build"}),
                             ("lesson-inside", {"lessons-explanation", "lessons-level", "lessons-tutor"}),
                             ("lesson-tutor", {"lessons-adapt"}),
                             ("lesson-test", {"lessons-start-test", "lessons-question", "lessons-check",
                                              "lessons-confidence"})):
            with self.subTest(section=section):
                self.check(section)
                self.assertTrue(lit <= {step for step, mode in self.log if mode == "spot"}, self.log)
        self.assertEqual(self.log[0], ("lessons-start-test", "spot"))

    def test_project_chapters_phone_light(self):
        for section in ("projects-start", "project-draft", "project-workspace", "project-progress", "project-review",
                        "study-session"):
            with self.subTest(section=section):
                _page, seen, spots = self.check(section)
                self.assertEqual(seen, spots, f"every step lit: {self.log}")

    def test_practice_chapters_phone_light(self):
        for section in ("flashcards-library", "flashcard-set", "flashcard-study", "todays-practice",
                        "mistake-insights", "dashboard", "progress"):
            with self.subTest(section=section):
                _page, seen, spots = self.check(section)
                self.assertEqual(seen, spots, f"every step lit: {self.log}")

    def test_profile_community_and_account_chapters_phone_light(self):
        for section in ("my-profile", "leagues", "community-library", "community-set", "settings", "settings-plan"):
            with self.subTest(section=section):
                _page, seen, spots = self.check(section)
                self.assertEqual(seen, spots, f"every step lit: {self.log}")

    def test_six_chapters_desktop_dark(self):
        for section in ("chat-answers", "lesson-test", "project-workspace", "flashcard-study", "community-set",
                        "settings-plan", "my-profile"):
            with self.subTest(section=section):
                _page, seen, spots = self.check(section, 1280, 900, "dark", False)
                self.assertTrue(spots, self.log)
                self.assertEqual([step for step in seen if step not in spots and step not in MAY_SKIP], [], self.log)

    def test_production_flags_flashcards(self):
        # render.yaml: the study modes, the imports and the vocabulary trainer are off.
        off = ("FEATURE_FLASHCARD_LEARN_MODE", "FEATURE_FLASHCARD_TEST_MODE", "FEATURE_FLASHCARD_MATCH_GAME",
               "FEATURE_FLASHCARD_BLAST_GAME", "FEATURE_FLASHCARD_BLOCKS_GAME", "FEATURE_FLASHCARD_GAMES",
               "FEATURE_FLASHCARD_PDF_IMPORT", "FEATURE_FLASHCARD_IMAGE_IMPORT", "FEATURE_VOCABULARY_TRAINER")
        config = self.application.app.config
        saved = {key: config.get(key) for key in off}

        def restore():
            config.update(saved)
            self.application._tutorial_payload_cached.cache_clear()

        self.addCleanup(restore)
        config.update({key: False for key in off})
        self.application._tutorial_payload_cached.cache_clear()
        page = self.student(375, 812, reduced=True, seed=False)
        self.own(page, "cards")
        not_found = []
        page.on("response", lambda response: not_found.append(response.url)
                if response.request.resource_type == "document" and response.status == 404 else None)
        seen, _spots = self.walk(page, "flashcards-library", phone=True)
        self.assertNotIn("practice-import", seen)
        self.assertNotIn("practice-vocabulary", seen)
        seen, _spots = self.walk(page, "flashcard-set", phone=True)
        self.assertNotIn("practice-modes", seen, "one mode only: nothing to choose from")
        self.assertEqual(not_found, [], "no step leads to a switched-off page")


class AutoStartTests(TutorialBrowserCase):
    """A chapter starts by itself the first time its anchor has been on screen for a moment -
    once, never again after Skip or Finish, never with the switch off, never during the
    first-time offer, a dialog, a generation or typing, at most one per page load."""

    def auto_student(self, *args, **kwargs):
        return self.student(*args, tips=True, **kwargs)

    def test_a_chapter_starts_once_names_itself_and_skip_means_never_again(self):
        page = self.auto_student(375, 812, reduced=True, seed=False)
        page.goto(f"{self.base}/projects")
        state = self.wait_mode(page, "spot", timeout=6000)
        self.assertEqual((state["section"], state["step"]), ("projects-start", "projects-create"))
        self.assertEqual(state["count"], "Step 1 of 4", "the first card says how many tips there are")
        self.assertEqual(state["skip"], "Skip")
        self.assertTrue(page.evaluate("document.getElementById('tourProgress').textContent.startsWith('Start a project')"))
        self.assert_lit(page, "projects-create")
        page.click("[data-tour-skip]")
        self.assertEqual(self.settled(page)["mode"], "off")
        time.sleep(0.3)
        self.assertEqual(self.status(page, "projects-start"), "skipped")
        page.reload()
        self.stays_off(page)
        self.assertEqual(self.errors, [])

    def test_finish_completes_it_and_the_next_chapter_comes_when_the_student_scrolls_to_it(self):
        page = self.auto_student(1280, 900, seed=False)
        page.goto(f"{self.base}/settings")
        state = self.wait_mode(page, "spot", timeout=6000)
        self.assertEqual(state["section"], "settings")
        self.walk(page, "settings", phone=False, start=False)
        to_plan = "document.querySelector('[data-tour-target=\"plan-status\"]').scrollIntoView({block: 'center'})"
        ended = time.monotonic()
        page.evaluate(to_plan)
        state = self.wait_mode(page, "spot", timeout=9000)
        self.assertEqual(state["section"], "settings-plan", "Your plan follows once the student scrolls to it")
        self.assertGreaterEqual(time.monotonic() - ended, 3.0, "never right after the last one")
        page.keyboard.press("Escape")
        self.assertEqual(self.settled(page)["mode"], "off")
        time.sleep(0.3)
        self.assertEqual(self.status(page, "settings-plan"), "skipped", "Escape skips too")
        page.reload()
        self.stays_off(page)

    def test_switched_off_no_chapter_starts_and_on_again_it_does(self):
        page = self.auto_student(1280, 900, seed=False)
        page.goto(f"{self.base}/tutorial")
        box = page.locator("[data-tutorial-tips] input[name=auto_tips]")
        self.assertTrue(box.is_checked(), "on by default")
        self.assertFalse(page.locator("[data-tutorial-tips] button[type=submit]").is_visible(),
                         "with JavaScript one tap saves it (the Save button is the fallback without)")
        with page.expect_navigation():
            box.click()
        page.wait_for_url("**/tutorial#tips")
        self.assertFalse(page.locator("[data-tutorial-tips] input[name=auto_tips]").is_checked())
        page.goto(f"{self.base}/projects")
        self.stays_off(page)
        page.goto(f"{self.base}/settings#help")
        with page.expect_navigation():
            page.locator("#help [data-tutorial-tips] input[name=auto_tips]").click()
        page.wait_for_url("**/settings#help")
        page.goto(f"{self.base}/projects")
        self.assertEqual(self.wait_mode(page, "spot", timeout=6000)["section"], "projects-start")

    def test_never_during_the_first_time_offer_nor_right_after_it(self):
        page = self.auto_student(375, 812, reduced=True, seed=False, settle=False)
        page.goto(f"{self.base}/projects")
        state = self.wait_mode(page, "offer")
        self.assertEqual(state["choices"], ["offer:quick", "offer:explore"])
        time.sleep(2.0)
        self.assertEqual(self.state(page)["mode"], "offer", "no chapter over the offer")
        page.click("[data-tour-action='offer:explore']")
        self.stays_off(page)
        page.reload()
        self.assertEqual(self.wait_mode(page, "spot", timeout=6000)["section"], "projects-start",
                         "on the next page load the chapter comes")

    def test_never_while_a_dialog_is_open_or_learnova_is_writing(self):
        page = self.auto_student(1280, 900, seed=False)
        page.goto(f"{self.base}/projects")
        page.evaluate("""() => { const d = document.createElement('dialog'); d.id = 'testDialog';
          d.textContent = 'a page dialog'; document.body.appendChild(d); d.showModal(); }""")
        self.stays_off(page, 2.4)
        page.evaluate("""() => { document.getElementById('testDialog').close();
          const busy = document.createElement('p'); busy.id = 'testBusy'; busy.textContent = 'writing';
          busy.setAttribute('aria-busy', 'true'); document.querySelector('main').prepend(busy); }""")
        self.stays_off(page, 2.4)
        page.evaluate("document.getElementById('testBusy').remove()")
        self.assertEqual(self.wait_mode(page, "spot", timeout=6000)["section"], "projects-start")

    def test_never_while_the_student_types_and_the_focus_stays_in_the_field(self):
        page = self.auto_student(1280, 900)
        page.goto(f"{self.base}{self.urls['thread']}")
        page.wait_for_function("() => window.__asstReady === true")
        page.click("#asstInput")
        for _ in range(14):   # typing for ~3 s
            page.keyboard.type("ab", delay=90)
            self.assertIn(self.state(page)["mode"], ("off", ""), "a chapter started while the student typed")
        state = self.wait_mode(page, "spot", timeout=7000)
        self.assertEqual(state["section"], "chat-basics", "the first chapter of the page comes first")
        self.assertEqual(page.evaluate("document.activeElement.id"), "asstInput",
                         "starting by itself never takes the focus away")
        for _ in range(4):
            page.keyboard.press("Tab")
            self.assertTrue(page.evaluate("Boolean(document.activeElement.closest('.tour-panel, [data-tour-lit]'))"),
                            "Tab reaches the card")
        self.assertEqual(self.errors, [])

    def test_the_hub_and_the_grade_prompt_never_start_a_chapter(self):
        page = self.auto_student(375, 812, reduced=True, seed=False)
        page.goto(f"{self.base}/tutorial")
        self.stays_off(page, 2.4)
        self.assertEqual(page.get_attribute("#tourRoot", "data-tutorial-auto"), "")


class FollowUpTests(TutorialBrowserCase):
    """Doing what a tip says, and the tips of a feature the student starts using in place."""

    def test_using_the_lit_link_saves_the_chapter_and_the_next_page_starts_its_own(self):
        # Review M1: "Tap Start learning" - tapping it left the chapter half-done, and the
        # study page started nothing.
        page = self.student(375, 812, reduced=True, seed=False, tips=True)
        self.own(page, "cards")
        page.goto(f"{self.base}{self.urls['set']}")
        state = self.wait_mode(page, "spot", timeout=7000)
        self.assertEqual((state["section"], state["step"]), ("flashcard-set", "practice-set"))
        with page.expect_navigation():
            page.click("a#ovStudy")
        state = self.wait_mode(page, "spot", timeout=7000)
        self.assertEqual(state["section"], "flashcard-study", "the study page's own chapter starts")
        self.assertEqual(self.status(page, "flashcard-set"), "skipped", "never starts again by itself")
        page.click("[data-tour-skip]")
        page.goto(f"{self.base}{self.urls['set']}")
        self.stays_off(page)
        self.assertEqual(self.errors, [])

    def test_the_lit_link_on_the_last_step_completes_it(self):
        page = self.student(1280, 900, seed=False)
        page.goto(f"{self.base}/dashboard")
        self.set_run(page, "overview", "overview-done")
        page.reload()
        state = self.settled(page)
        self.assertEqual((state["step"], state["mode"]), ("overview-done", "opener"), "More is opened first")
        page.locator("[data-tour-lit]").first.click()
        state = self.settled(page, previous=self.key(state))
        self.assert_lit(page, "overview-done")
        with page.expect_navigation():
            page.locator("[data-tour-lit]").first.click()   # the student taps the Tutorial link
        page.wait_for_url("**/tutorial")
        self.assertEqual(self.status(page, "overview"), "completed")
        time.sleep(0.6)
        self.assertEqual(self.state(page)["mode"], "off", "nothing continues on the hub")

    def test_the_first_answer_after_explore_on_my_own_gets_its_tips(self):
        # Review M2: the first answer on the same page load as the offer got nothing.
        page = self.student(1280, 900, seed=False, settle=False, tips=True)
        page.goto(f"{self.base}/")
        self.wait_mode(page, "offer")
        page.click("[data-tour-action='offer:explore']")
        self.stays_off(page, 2.0)   # Chat basics' anchor was on screen when the offer closed
        page.wait_for_function("() => window.__asstReady === true")
        page.fill("#asstInput", "Was ist eine Potenz?")
        page.keyboard.press("Enter")
        page.wait_for_selector("[data-tour-target='chat-reply']", timeout=15000)
        state = self.wait_mode(page, "spot", timeout=10000)
        self.assertEqual((state["section"], state["step"]), ("chat-answers", "chat-reply"))
        self.assert_lit(page, "chat-reply")
        self.assertEqual(self.errors, [])

    def test_a_lesson_built_in_place_gets_its_tips_on_the_same_page_load(self):
        page = self.student(1280, 900, seed=False, tips=True)
        page.goto(f"{self.base}/lesson")
        state = self.wait_mode(page, "spot", timeout=7000)
        self.assertEqual(state["section"], "lesson-start")
        page.click("[data-tour-skip]")
        page.locator("fieldset.subject-picker label").nth(0).click()
        page.fill("#studyGoal", "Potenzgesetze")
        page.click("#uploadForm button.begin-button")
        state = self.wait_mode(page, "spot", timeout=20000)
        self.assertEqual(state["section"], "lesson-inside", "the lesson's tips come when it is ready")
        self.assertEqual(state["count"], "Step 1 of 4")
        self.assertEqual(self.errors, [])

    def test_a_chapter_never_starts_on_a_step_that_is_not_there(self):
        # Review m2: the lesson test started with "Still loading" over a blurred page.
        page = self.student(1280, 900, tips=True)
        lesson = self.progress(page)["context"]["lesson"]
        for section, step in (("lesson-inside", "lessons-explanation"), ("lesson-tutor", "lessons-adapt")):
            page.request.post(f"{self.base}/api/tutorial/progress", data=json.dumps(
                {"section": section, "step": step, "status": "skipped"}), headers={"Content-Type": "application/json"})
        page.goto(f"{self.base}{lesson}")
        page.click("#startTest")   # the student scrolls down to Start test and taps it
        modes = []
        deadline = time.monotonic() + 9
        while time.monotonic() < deadline:
            state = self.state(page)
            modes.append(state["mode"])
            if state["mode"] == "spot":
                break
            time.sleep(0.1)
        self.assertEqual(state["section"], "lesson-test")
        self.assertEqual((state["step"], state["count"]), ("lessons-question", "Step 1 of 3"))
        self.assertNotIn("pending", [mode for mode in modes if mode not in ("off", "", "hold")],
                         "nothing is shown before a real step")
        self.assertFalse(page.evaluate("document.getElementById('tourRoot').classList.contains('is-card')"))


class HubTests(TutorialBrowserCase):
    """/tutorial: its buttons start the quick tour, replay, resume, restart and mark
    chapters done for real."""

    STATUS_JS = "(id) => document.querySelector(`[data-tutorial-section='${id}']`)?.dataset.tutorialStatus || ''"

    def hub(self, page):
        page.goto(f"{self.base}/tutorial")
        page.wait_for_load_state("domcontentloaded")

    def wait_status(self, page, section, status):
        page.wait_for_function(f"() => ({self.STATUS_JS})({json.dumps(section)}) === {json.dumps(status)}",
                               timeout=8000)

    def redrawn(self, page, action):
        """Do `action` and wait until the hub has really been drawn again."""

        mark = page.evaluate("() => (window.__hubDocument = String(Math.random()))")
        action()
        page.wait_for_function("(mark) => window.__hubDocument !== mark && document.readyState !== 'loading'"
                               " && Boolean(document.querySelector('[data-tutorial-section]'))", arg=mark, timeout=8000)

    def test_a_new_student_sees_what_starts_when_and_nothing_old(self):
        page = self.student(375, 812, reduced=True, seed=False, language="de")
        self.hub(page)
        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"), "no sideways scroll")
        small = page.evaluate("""() => [...document.querySelectorAll('.tutorial-shell button, .tutorial-shell a')]
            .filter(n => n.getClientRects().length && n.getBoundingClientRect().height < 43.5).map(n => n.textContent.trim())""")
        self.assertEqual(small, [], "every hub button is at least 44 px high")
        text = page.text_content("main")
        self.assertIn("Startet nach deiner ersten Frage im Chat.", text)
        self.assertIn("Kurzer Überblick", text, "the quick tour; the hub itself is the Rundgang")
        for sentence in OLD_MISSING:
            self.assertNotIn(sentence, text)
        self.assertEqual(page.locator("[data-tutorial-section='chat-answers'] [data-tutorial-replay]").count(), 0)
        self.assertEqual(page.get_attribute("[data-tutorial-section='chat-answers'] a.secondary-button", "href"), "/")

    def test_quick_tour_from_the_hub_replay_restart_and_restart_all(self):
        page = self.student(1280, 900, theme="dark", seed=False)
        self.hub(page)
        time.sleep(0.8)
        self.assertEqual(self.state(page)["mode"], "off", "the hub shows nothing by itself")
        page.click("[data-tutorial-section='overview'] [data-tutorial-start]")
        state = self.settled(page)
        self.assertEqual((state["section"], state["step"], state["mode"]), ("overview", "overview-chat", "spot"))
        self.assertEqual(state["skip"], "Exit quick tour")
        self.redrawn(page, lambda: page.click("[data-tour-skip]"))
        self.wait_status(page, "overview", "skipped")
        page.wait_for_function("() => document.activeElement?.closest('[data-tutorial-section]')?.dataset"
                               ".tutorialSection === 'overview'", timeout=4000)
        # Replay a chapter: the link goes to its page and the chapter runs there.
        link = page.locator("[data-tutorial-section='projects-start'] a[data-tutorial-replay]")
        self.assertEqual(link.text_content().strip(), "Start")
        link.click()
        page.wait_for_url("**/projects")
        state = self.settled(page)
        self.assertEqual((state["section"], state["step"], state["mode"]), ("projects-start", "projects-create", "spot"))
        page.click("[data-tour-next]")
        self.settled(page, previous=self.key(state))
        page.goto(f"{self.base}/dashboard")   # left half-way: it stays in progress
        self.stays_off(page, 1.5)
        self.hub(page)
        self.wait_status(page, "projects-start", "in_progress")
        resume = page.locator("[data-tutorial-section='projects-start'] a[data-tutorial-replay]")
        self.assertEqual(resume.text_content().strip(), "Resume")
        self.assertEqual(resume.get_attribute("data-tutorial-from"), "projects-upload")
        resume.click()
        page.wait_for_url("**/projects")
        self.assertEqual(self.settled(page)["step"], "projects-upload", "resumed where it stopped")
        self.walk(page, "projects-start", phone=False, start=False)
        self.hub(page)
        self.wait_status(page, "projects-start", "completed")
        self.assertEqual(page.locator("[data-tutorial-section='projects-start'] [data-tutorial-done]").count(), 0)
        # Mark as done, then Restart.
        self.redrawn(page, lambda: page.click("[data-tutorial-section='todays-practice'] [data-tutorial-done]"))
        self.wait_status(page, "todays-practice", "completed")
        page.click("[data-tutorial-section='todays-practice'] [data-tutorial-restart]")
        page.wait_for_url("**/practice/today")
        state = self.settled(page)
        self.assertEqual((state["section"], state["step"]), ("todays-practice", "practice-today"), "Restart: replays it")
        self.assertEqual(self.status(page, "todays-practice"), "in_progress", "and it is new again")
        page.click("[data-tour-skip]")
        self.hub(page)
        # Reset all tips asks first: Cancel changes nothing, OK resets everything.
        page.once("dialog", lambda dialog: dialog.dismiss())
        page.click("[data-tutorial-restart-all]")
        time.sleep(0.6)
        self.assertEqual(self.state(page)["mode"], "off")
        self.assertEqual(self.status(page, "projects-start"), "completed", "cancelled: nothing was reset")
        messages = []
        page.once("dialog", lambda dialog: (messages.append(dialog.message), dialog.accept()))
        page.click("[data-tutorial-restart-all]")
        state = self.settled(page)
        self.assertIn("resets every tip", messages[0])
        self.assertEqual(state["section"], "overview", "Restart tutorial: everything from the quick tour")
        self.assertEqual({entry["status"] for key, entry in self.progress(page)["sections"].items()
                          if key != "overview"}, {"not_started"})
        self.assertEqual(self.errors, [])

    def test_mark_as_done_on_the_hub_and_skip_never_undoes_it(self):
        page = self.student(1280, 900, seed=False)
        self.hub(page)
        self.redrawn(page, lambda: page.click("[data-tutorial-section='overview'] [data-tutorial-done]"))
        self.wait_status(page, "overview", "completed")
        percent = page.text_content("[data-tutorial-percent]")
        start = page.locator("[data-tutorial-section='overview'] [data-tutorial-start]")
        self.assertEqual(start.text_content().strip(), "Replay")
        start.click()
        self.settled(page)
        self.redrawn(page, lambda: page.click("[data-tour-skip]"))
        self.wait_status(page, "overview", "completed")
        self.assertEqual(page.text_content("[data-tutorial-percent]"), percent, "the percent did not drop")
        self.assertEqual(self.errors, [])


class EngineTests(TutorialBrowserCase):
    """Previous, Skip, resume, keyboard, Escape, pausing, silent skips, the dark mask and
    the idle loop."""

    def test_previous_next_skip_and_resume_after_a_reload(self):
        page = self.student(1280, 900, seed=False)
        self.start(page, "lesson-start", "/lesson")
        first = self.settled(page)
        self.assertEqual(first["step"], "lessons-subject")
        self.assertTrue(first["prevDisabled"])
        page.click("[data-tour-next]")
        second = self.settled(page, previous=self.key(first))
        self.assertEqual(second["step"], "lessons-goal")
        self.assertFalse(second["prevDisabled"])
        self.assertEqual(page.evaluate("document.querySelectorAll('[data-tour-lit]').length"), 2, "goal + ideas lit together")
        page.click("[data-tour-prev]")
        back = self.settled(page, previous=self.key(second))
        self.assertEqual(back["step"], "lessons-subject")
        page.click("[data-tour-next]")
        self.assertEqual(self.settled(page, previous=self.key(back))["step"], "lessons-goal")
        page.reload()
        resumed = self.settled(page)
        self.assertEqual((resumed["mode"], resumed["step"]), ("spot", "lessons-goal"), "a reload resumes the run")
        page.click("[data-tour-skip]")
        self.assertEqual(self.settled(page)["mode"], "off")
        self.assertEqual(page.evaluate("document.activeElement.tagName"), "MAIN", "focus goes back to the page")
        time.sleep(0.3)
        self.assertEqual(self.status(page, "lesson-start"), "skipped")

    def test_the_students_own_action_moves_on_and_an_idea_jumps_ahead(self):
        page = self.student(1280, 900, seed=False)
        self.start(page, "lesson-start", "/lesson")
        first = self.settled(page)
        page.locator("fieldset.subject-picker label").nth(4).click()
        moved = self.settled(page, previous=self.key(first))
        self.assertEqual(moved["step"], "lessons-goal", "choosing a subject is doing the step")
        page.locator(".prompt-ideas button").first.click()
        jumped = self.settled(page, previous=self.key(moved))
        self.assertEqual(jumped["step"], "lessons-photos", "an idea fills subject and goal at once")

    def test_a_step_whose_element_is_not_there_is_skipped_silently(self):
        page = self.student(1280, 900, seed=False)
        page.goto(f"{self.base}/projects")
        page.evaluate("document.querySelector('[data-tour-target=\"project-topics-only\"]').remove()")
        page.evaluate("""() => { const b = document.createElement('button'); b.dataset.tutorialStart = 'projects-start';
          document.body.appendChild(b); b.click(); }""")
        modes = []
        previous = None
        for _ in range(8):
            state = self.settled(page, previous=previous)
            previous = self.key(state)
            if state["mode"] == "off":
                break
            modes.append((state["step"], state["mode"], state["count"]))
            for sentence in OLD_MISSING:
                self.assertNotIn(sentence, state["text"] + state["note"])
            page.click("[data-tour-next]")
        steps = [step for step, _mode, _count in modes]
        self.assertNotIn("projects-topics-only", steps, "nothing is said about it")
        self.assertEqual(steps, ["projects-create", "projects-upload", "projects-create-free"])
        self.assertEqual(modes[-1][2], "Step 3 of 3", "the counter leaves it out")
        self.assertEqual(self.status(page, "projects-start"), "completed")
        # Previous over it, back again, without a loop.
        self.start(page, "projects-start", "/projects")
        page.evaluate("document.querySelector('[data-tour-target=\"project-topics-only\"]').remove()")
        state = self.settled(page)
        for _ in range(2):
            page.click("[data-tour-next]")
            state = self.settled(page, previous=self.key(state))
        self.assertIn(state["step"], ("projects-create-free",))
        page.click("[data-tour-prev]")
        state = self.settled(page, previous=self.key(state))
        self.assertEqual(state["step"], "projects-upload", "Previous jumps over the missing step")
        self.assertEqual(self.errors, [])

    def test_tab_stays_in_the_card_and_the_lit_element_and_escape_skips(self):
        page = self.student(1280, 900, seed=False)
        self.start(page, "lesson-start", "/lesson")
        state = self.settled(page)
        self.assertEqual(state["step"], "lessons-subject")
        allowed = """() => {
          const a = document.activeElement;
          return Boolean(a && (a.closest('.tour-panel') || a.closest('[data-tour-lit]')));
        }"""
        for _ in range(10):
            page.keyboard.press("Tab")
            self.assertTrue(page.evaluate(allowed), page.evaluate("document.activeElement.outerHTML.slice(0, 120)"))
        page.keyboard.press("Shift+Tab")
        self.assertTrue(page.evaluate(allowed))
        page.keyboard.press("Escape")
        self.assertEqual(self.settled(page)["mode"], "off")
        self.assertEqual(page.evaluate("document.activeElement.id"), "testHubStart", "focus returns to where it started")

    def test_the_more_menu_stays_open_and_escape_closes_the_menu_first(self):
        page = self.student(1280, 900, seed=False)
        self.start(page, "overview", "/dashboard")
        state = self.settled(page)
        for _ in range(4):   # chat, Overview, New Lesson, Project Mode: the desktop bar
            page.click("[data-tour-next]")
            state = self.settled(page, previous=self.key(state))
        self.assertEqual((state["step"], state["mode"]), ("overview-flashcards", "opener"))
        self.assertIn("app-menu-trigger", state["lit"])
        page.locator("[data-tour-lit]").first.click()
        state = self.settled(page, previous=self.key(state))
        self.assertEqual(state["mode"], "spot")
        self.assert_lit(page, "overview-flashcards")
        is_open = "document.querySelector('#moreMenu').classList.contains('is-open')"
        page.focus("[data-tour-next]")
        for _ in range(4):
            page.keyboard.press("Tab")
            self.assertTrue(page.evaluate("Boolean(document.activeElement.closest('.tour-panel, [data-tour-lit]'))"))
        time.sleep(0.3)
        self.assertTrue(page.evaluate(is_open), "focus in the card does not close the menu")
        page.click("#tourTitle")
        time.sleep(0.3)
        self.assertTrue(page.evaluate(is_open), "a click on the card does not close it either")
        page.keyboard.press("Escape")
        time.sleep(0.4)
        self.assertFalse(page.evaluate(is_open))
        self.assertNotEqual(self.state(page)["mode"], "off", "the first Escape closed only the menu")
        page.keyboard.press("Escape")
        self.assertEqual(self.settled(page)["mode"], "off")
        self.assertEqual(self.errors, [])

    def test_a_page_dialog_pauses_the_tour_and_opening_the_menu_ends_the_chapter(self):
        page = self.student(1280, 900)
        page.goto(f"{self.base}{self.urls['thread']}")
        page.wait_for_function("() => window.__asstReady === true")
        self.set_run(page, "chat-history", "chat-new")
        page.reload()
        page.wait_for_function("() => window.__asstReady === true")
        state = self.settled(page)
        self.assertEqual((state["step"], state["mode"]), ("chat-new", "spot"))
        page.evaluate("""() => { const d = document.createElement('dialog'); d.id = 'testDialog';
          d.innerHTML = '<button id="testDialogClose">Close</button>'; document.body.appendChild(d); d.showModal(); }""")
        time.sleep(0.5)
        paused = self.state(page)
        self.assertEqual(paused["mode"], "paused")
        self.assertTrue(paused["hidden"], "the tour steps aside for the dialog")
        page.evaluate("document.getElementById('testDialog').close()")
        back = self.settled(page)
        self.assertEqual((back["mode"], back["step"]), ("spot", "chat-new"), "and comes back when it closes")
        page.click("[data-tour-next]")
        state = self.settled(page, previous=self.key(back))
        self.assertEqual((state["step"], state["next"]), ("chat-menu", "Finish"))
        page.locator("[data-tour-lit]").first.click()   # the student opens the ⋯ menu: the chapter is done
        self.assertEqual(self.settled(page)["mode"], "off")
        page.click("#asstItemMenu #asstRename")        # and the menu is usable, not under the mask
        page.wait_for_selector("#asstRenameDialog[open]")
        page.click("#asstRenameCancel")
        time.sleep(0.3)
        self.assertEqual(self.status(page, "chat-history"), "completed")
        self.assertEqual(self.errors, [])

    def test_dark_mask_ring_on_screen_and_no_layout_loop(self):
        page = self.student(1280, 900, theme="dark", seed=False)
        self.start(page, "overview", "/dashboard")
        state = self.settled(page)
        self.assertEqual(state["step"], "overview-chat")
        alpha = page.evaluate("""() => {
          const value = getComputedStyle(document.querySelector('.tour-mask[data-mask="top"]')).backgroundColor;
          const parts = value.match(/[\\d.]+/g).map(Number); return parts.length > 3 ? parts[3] : 1; }""")
        self.assertGreaterEqual(alpha, 0.65)
        mutations = page.evaluate("""() => new Promise(resolve => {
          let count = 0;
          const observer = new MutationObserver(records => { count += records.length; });
          observer.observe(document.getElementById('tourRoot'), {attributes: true, childList: true, subtree: true, characterData: true});
          setTimeout(() => { observer.disconnect(); resolve(count); }, 1000);
        })""")
        self.assertLess(mutations, 10, "idle: the tour writes nothing (it was about 720 a second)")
        spot = page.evaluate("(() => { const r = document.querySelector('.tour-spot').getBoundingClientRect();"
                             " return [r.left, r.top, r.right, r.bottom]; })()")
        self.assertGreaterEqual(spot[1], 0, "the ring of a header link is not cut off at the top")
        self.assertLessEqual(spot[2], 1280)
        seams = page.evaluate("""() => { const m = k => document.querySelector(`.tour-mask[data-mask="${k}"]`).getBoundingClientRect();
          return [m('top').bottom - m('left').top, m('left').bottom - m('bottom').top]; }""")
        self.assertEqual(seams, [0, 0], "no undimmed row between the masks")


class ReviewFixTests(TutorialBrowserCase):
    """The fixes of the three tutorial reviews (2026-11-04) that only a browser can show."""

    CONTRAST_JS = """(selector) => {
      const node = document.querySelector(selector);
      const parse = (value) => value.match(/[\\d.]+/g).slice(0, 3).map(Number);
      const lum = ([r, g, b]) => { const c = [r, g, b].map(v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; });
        return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]; };
      const style = getComputedStyle(node);
      const a = lum(parse(style.color)), b = lum(parse(style.backgroundColor));
      return (Math.max(a, b) + .05) / (Math.min(a, b) + .05);
    }"""

    def announced(self, page):
        page.wait_for_function("() => document.getElementById('tourAnnounce').textContent.length > 0", timeout=6000)
        return page.text_content("#tourAnnounce")

    def test_the_live_region_is_never_visible_text(self):
        page = self.student(375, 812, reduced=True, seed=False)
        for section, path in (("dashboard", "/dashboard"), ("lesson-start", "/lesson")):
            with self.subTest(path=path):
                self.start(page, section, path)
                self.settled(page)
                self.announced(page)
                box = page.evaluate("""() => { const r = document.getElementById('tourAnnounce').getBoundingClientRect();
                  const s = getComputedStyle(document.getElementById('tourAnnounce'));
                  return {w: r.width, h: r.height, position: s.position, overflow: s.overflow}; }""")
                self.assertLessEqual(box["w"], 1, box)
                self.assertLessEqual(box["h"], 1, box)
                self.assertEqual((box["position"], box["overflow"]), ("absolute", "hidden"))
                page.click("[data-tour-skip]")

    def test_dark_primary_buttons_read_at_four_and_a_half_to_one(self):
        page = self.student(1280, 900, theme="dark", seed=False)
        self.start(page, "overview", "/dashboard")
        self.settled(page)
        self.assertGreaterEqual(page.evaluate(self.CONTRAST_JS, "[data-tour-next]"), 4.5)
        page.hover("[data-tour-next]")
        time.sleep(0.3)
        self.assertGreaterEqual(page.evaluate(self.CONTRAST_JS, "[data-tour-next]"), 4.5, "and on hover")
        page.click("[data-tour-skip]")
        page.goto(f"{self.base}/tutorial")
        self.assertGreaterEqual(page.evaluate(self.CONTRAST_JS, "[data-tutorial-section='overview'] [data-tutorial-start]"),
                                4.5)

    def test_the_announcement_names_the_target_not_its_whole_text(self):
        page = self.student(1280, 900, seed=False)
        self.start(page, "lesson-start", "/lesson")
        self.settled(page)
        said = self.announced(page)
        self.assertIn("Build a lesson: Step 1 of 4.", said)
        self.assertIn("Highlighted: Choose a subject", said)
        for subject in ("Physics", "Chemistry", "History"):
            self.assertNotIn(subject, said, "the subject buttons inside are not read out")
        self.assertNotIn("..", said)
        page.click("[data-tour-skip]")
        self.start(page, "overview", "/lesson")
        previous = None
        for _ in range(20):
            state = self.settled(page, previous=previous)
            if state["step"] == "overview-done" and state["mode"] == "spot":
                break
            previous = self.key(state)
            if state["mode"] == "opener":
                page.locator("[data-tour-lit]").first.click()
            else:
                page.click("[data-tour-next]")
        self.assertEqual(state["step"], "overview-done")
        self.assert_lit(page, "overview-done")
        page.wait_for_function("() => document.getElementById('tourAnnounce').textContent.includes('You are ready')")
        self.assertNotIn("!.", self.announced(page), "You are ready! - no second full stop")

    def test_focus_goes_to_the_card_when_the_students_action_removed_it(self):
        page = self.student(1280, 900)
        lesson = self.progress(page)["context"]["lesson"]
        page.goto(f"{self.base}{lesson}")
        self.set_run(page, "lesson-test", "lessons-start-test")
        page.reload()
        state = self.wait_mode(page, "spot")
        self.assertEqual(state["step"], "lessons-start-test")
        page.focus("#startTest")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.getElementById('tourRoot').dataset.tourStep === 'lessons-question'")
        page.wait_for_function("() => document.activeElement && document.activeElement.id === 'tourTitle'", timeout=3000)

    def test_a_section_taller_than_the_phone_is_lit_from_its_top(self):
        page = self.student(375, 640, reduced=True, seed=False)
        self.start(page, "todays-practice", "/practice/today")
        state = self.wait_mode(page, "spot")
        self.assertEqual(state["step"], "practice-today")
        time.sleep(0.4)
        box = page.evaluate("""() => {
          const spot = document.querySelector('.tour-spot').getBoundingClientRect();
          const panel = document.querySelector('.tour-panel').getBoundingClientRect();
          const target = document.querySelector('[data-tour-lit]').getBoundingClientRect();
          const nav = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--app-nav-height')) || 64;
          return {spotTop: spot.top, spotBottom: spot.bottom, panelTop: panel.top, targetTop: target.top,
                  targetHeight: target.height, vh: innerHeight, nav};
        }""")
        if box["targetHeight"] > box["panelTop"] - box["nav"]:
            self.assertLessEqual(box["spotBottom"], box["panelTop"], f"the card never covers the lit part: {box}")
            self.assertLess(box["spotBottom"] - box["spotTop"], box["vh"] * 0.8, "part of the screen stays dimmed")
            self.assertLess(abs(box["targetTop"] - (box["nav"] + 12)), 40, f"scrolled to its top: {box}")
        self.assert_lit(page, "practice-today")

    def test_the_counter_stays_on_one_line_on_a_phone(self):
        page = self.student(375, 812, reduced=True, seed=False)
        self.start(page, "settings-plan", "/settings")
        self.settled(page)
        lines = page.evaluate("""() => { const node = document.querySelector('#tourProgress .tour-count');
          const range = document.createRange(); range.selectNodeContents(node);
          return [getComputedStyle(node).whiteSpace, new Set([...range.getClientRects()].map(r => Math.round(r.top))).size]; }""")
        self.assertEqual(lines, ["nowrap", 1])

    def test_the_title_ring_shows_only_for_keyboard_users(self):
        page = self.student(1280, 900, seed=False)
        self.start(page, "lesson-start", "/lesson")   # a click started it
        self.settled(page)
        page.wait_for_function("() => document.activeElement?.id === 'tourTitle'")
        outline = "() => getComputedStyle(document.getElementById('tourTitle')).outlineStyle"
        self.assertEqual(page.evaluate(outline), "none")
        page.focus("[data-tour-next]")
        page.keyboard.press("Enter")
        page.wait_for_function("() => document.getElementById('tourRoot').dataset.tourStep === 'lessons-goal'")
        page.wait_for_function("() => document.activeElement?.id === 'tourTitle'")
        self.assertEqual(page.evaluate(outline), "solid")

    def test_another_accounts_run_and_offer_are_ignored_and_logging_out_clears_them(self):
        page = self.student(1280, 900, settle=False, seed=False)
        page.goto(f"{self.base}/dashboard")
        self.wait_mode(page, "offer")
        page.evaluate("""() => {
          sessionStorage.setItem('learnova.tutorial.offer', 'dismissed:someone-else');
          sessionStorage.setItem('learnova.tutorial.run', JSON.stringify({v: 5, section: 'overview', step: 'overview-chat',
            auto: false, u: 'someone-else'}));
        }""")
        page.reload()
        self.assertEqual(self.wait_mode(page, "offer")["mode"], "offer",
                         "another account's choice and run do not count for this one")
        page.click("[data-tour-action='offer:explore']")
        self.assertEqual(self.settled(page)["mode"], "off")
        self.set_run(page, "overview", "overview-done")
        self.assertIsNotNone(page.evaluate("sessionStorage.getItem('learnova.tutorial.offer')"))
        page.evaluate("() => document.querySelector('form[action=\"/logout\"]').requestSubmit()")
        page.wait_for_load_state("domcontentloaded")
        page.wait_for_function("() => !document.getElementById('tourRoot')")
        left = page.evaluate("""() => ['run', 'offer', 'payload'].map(key => sessionStorage.getItem('learnova.tutorial.' + key))""")
        self.assertEqual(left, [None, None, None], "logging out leaves nothing of the tutorial in the tab")


class OnboardingTests(TutorialBrowserCase):
    """The first-time offer on the first page after registering."""

    def offer(self, page, path="/"):
        page.goto(f"{self.base}{path}")
        state = self.wait_mode(page, "offer")
        self.assertEqual(state["choices"], ["offer:quick", "offer:explore"])
        return state

    def test_explore_on_my_own_settles_it_for_good(self):
        page = self.student(375, 812, settle=False, seed=False, reduced=True)
        self.offer(page)
        self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"), "no sideways scroll")
        page.click("[data-tour-action='offer:explore']")
        self.assertEqual(self.settled(page)["mode"], "off")
        time.sleep(0.5)
        self.assertTrue(self.progress(page)["offer_settled"])
        page.reload()
        time.sleep(0.9)
        self.assertNotEqual(self.state(page)["mode"], "offer", "never offered again")

    def test_show_me_around_walks_the_quick_tour_and_escape_explores(self):
        page = self.student(1280, 900, settle=False, seed=False)
        self.offer(page, "/dashboard")
        self.assertEqual(page.text_content("[data-tour-action='offer:quick']"), "Show me around (1 minute)")
        page.click("[data-tour-action='offer:quick']")
        state = self.settled(page)
        self.assertEqual((state["section"], state["step"]), ("overview", "overview-chat"))
        self.assert_lit(page, "overview-chat")
        other = self.student(1280, 900, settle=False, seed=False)
        self.offer(other)
        other.keyboard.press("Escape")
        self.assertEqual(self.settled(other)["mode"], "off")

    def test_no_offer_on_the_grade_prompt_but_after_it(self):
        page = self.student(1280, 900, settle=False, seed=False, grade="")
        page.goto(f"{self.base}/onboarding/grade")
        time.sleep(0.9)
        self.assertEqual(self.state(page)["mode"], "off")
        page.click("form[action$='/onboarding/grade/skip'] [type=submit]")   # "Skip for now"
        page.wait_for_load_state("domcontentloaded")
        page.goto(f"{self.base}/dashboard")
        self.assertEqual(self.wait_mode(page, "offer")["mode"], "offer")


if __name__ == "__main__":
    unittest.main()

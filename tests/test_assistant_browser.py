"""The chat page in a real browser (WP5): the real templates, stylesheets and assistant.js
against the real Flask app with the AI patched (scripts/mobile_audit.start_server).

Covers what the server tests cannot: the sidebar collapse is remembered, the phone drawer
traps focus and closes on Escape and on the veil, the ⋯ menu works with the keyboard, the
rename and delete dialogs, archive and restore, search snippets, one send for a double
Enter, a reply landing in its own thread after a switch, the address bar surviving a
reload, Stop, Regenerate, Retry, tap answers, Continue Learning (loading, one request for
a double click, the link, the subject question), waiting photos coming back, photos kept
in the tray when a send is refused, no sideways scrolling at 320 px, the composer above a
shrunken visual viewport, and no light-on-light text in the dark theme. The pure rules
(static/js/assistant-rules.js) run in the module harness.

Skipped when Playwright or a Chromium-based browser is missing (Store Python has no
Playwright: run with R:/ai_tutor/.venv/Scripts/python).
"""

import base64
import io
import json
import os
import socket
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from PIL import Image

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_assistant_browser_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - depends on the machine
    sync_playwright = None

from tests.js_browser import harness  # noqa: E402

BROWSER = {}
RULES = "/static/js/assistant-rules.js"

REPLY = ("**Gut!** Bei \\(x^2 - 2x - 3 = 0\\) hilft die Lösungsformel:\n\n"
         "\\[\nx_{1,2} = \\frac{-b \\pm \\sqrt{b^2 - 4ac}}{2a}\n\\]\n\n"
         "Welche Lösungen hat die Gleichung?\nCHIPS: x = 3 und x = -1 | x = 1\nTOPIC: Mathematics | Quadratische Gleichungen")
# No TOPIC line at all (a reply that leaves it out): the subject is then asked for.
REPLY_NO_TOPIC = "Das ist eine gute Frage. Was genau möchtest du wissen?"
WIDE = ("Die lange Rechnung:\n\n\\[\n" + " + ".join(f"\\frac{{{n}}}{{{n + 1}}}" for n in range(1, 40)) + "\n\\]\n\n"
        "| " + " | ".join(f"Spalte {n}" for n in range(1, 13)) + " |\n|" + "---|" * 12 + "\n| "
        + " | ".join(f"Wert {n} mit langem Text" for n in range(1, 13)) + " |\n\n"
        "Und inline: $" + " + ".join(f"a_{{{n}}} x^{{{n}}}" for n in range(1, 30)) + "$\nTOPIC: Mathematics | Brüche")
LONG_QUESTION = "Wie löse ich die Gleichung x^2 - 2x - 3 = 0? Ich verstehe die Lösungsformel nicht."
# Formulas inside table cells (the cells must not break a formula over several lines).
TABLE_MATH = ("Die beiden Gleichungen:\n\n| Gleichung | Lösung |\n|---|---|\n"
              "| $x^2 - 2x - 3 = 0$ | $x_1 = 3$ und $x_2 = -1$ |\n"
              "| $\\frac{a}{b} + \\frac{c}{d} = \\frac{ad + bc}{bd}$ | gilt für $b, d \\neq 0$ |\n\n"
              "TOPIC: Mathematics | Gleichungen")
# WCAG contrast of every visible text in a scope, with translucent backgrounds and
# gradients composited (the design review's measurement): the texts under the threshold.
CONTRAST_JS = r"""
(scopeSel) => {
  const parse = (s) => {
    s = String(s || '').trim();
    let m = s.match(/^rgba?\(([^)]+)\)/);
    if (m) { const p = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = s.match(/^color\(srgb ([^)]+)\)/);
    if (m) { const p = m[1].split(/[ \/]+/).filter(Boolean).map(Number); return [p[0] * 255, p[1] * 255, p[2] * 255, p.length > 3 ? p[3] : 1]; }
    return null;
  };
  const gradColors = (img) => { const out = []; const re = /(rgba?\([^)]+\)|color\(srgb [^)]+\))/g; let m;
    while ((m = re.exec(img))) { const c = parse(m[1]); if (c) out.push(c); } return out; };
  const over = (top, bottom) => { const a = top[3]; return [top[0] * a + bottom[0] * (1 - a), top[1] * a + bottom[1] * (1 - a), top[2] * a + bottom[2] * (1 - a), 1]; };
  const lum = (c) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
  const bgsUnder = (el) => {
    const chain = []; for (let n = el; n; n = n.parentElement) chain.push(n); chain.reverse();
    let bgs = [[255, 255, 255, 1]];
    for (const n of chain) {
      const cs = getComputedStyle(n); const img = cs.backgroundImage;
      if (img && img !== 'none' && /gradient/.test(img) && !n.classList.contains('app-background')) {
        const cols = gradColors(img); if (cols.length) { bgs = cols.flatMap(c => bgs.map(b => over(c, b))); continue; }
      }
      const c = parse(cs.backgroundColor); if (c && c[3] > 0) bgs = bgs.map(b => over(c, b));
    }
    return bgs;
  };
  const opacityOf = (el) => { let o = 1; for (let n = el; n; n = n.parentElement) o *= Number(getComputedStyle(n).opacity); return o; };
  const visible = (el) => { const cs = getComputedStyle(el); if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    const r = el.getBoundingClientRect(); if (r.width <= 1 || r.height <= 1) return false;
    return !el.closest('.visually-hidden, [aria-hidden="true"], .katex, .ai-mode-badge, [hidden]'); };
  const results = []; const seen = new Set();
  for (const scope of document.querySelectorAll(scopeSel)) {
    const walker = document.createTreeWalker(scope, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      const node = walker.currentNode; if (!node.textContent.trim()) continue;
      const el = node.parentElement; if (!el || seen.has(el) || !visible(el)) continue; seen.add(el);
      const cs = getComputedStyle(el); const fg = parse(cs.color); if (!fg) continue;
      const op = opacityOf(el); let worst = 99;
      for (const bg of bgsUnder(el)) { let f = over(fg, bg); if (op < 1) f = over([f[0], f[1], f[2], op], bg); worst = Math.min(worst, ratio(f, bg)); }
      const size = parseFloat(cs.fontSize); const weight = Number(cs.fontWeight) || 400;
      const need = size >= 24 || (size >= 18.66 && weight >= 700) ? 3 : 4.5;
      if (worst < need) results.push({text: node.textContent.trim().slice(0, 40), cls: el.className, ratio: Math.round(worst * 100) / 100, need});
    }
  }
  return results;
}
"""


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


class ChatRulesTests(unittest.TestCase):
    """static/js/assistant-rules.js: the decisions behind the list, the menu and the controls."""

    @classmethod
    def setUpClass(cls):
        if not BROWSER:
            raise unittest.SkipTest("no browser")
        cls.page = harness.open_page(BROWSER["browser"], katex="none")

    @classmethod
    def tearDownClass(cls):
        cls.page.context.close()

    def rule(self, expression):
        return harness.eval_module(self.page, RULES, expression)

    def test_rows_group_under_the_servers_headings_and_drafts_stay_hidden(self):
        groups = self.rule("""(m) => m.groupRows([
            {id: "a", group: "today", message_count: 2}, {id: "b", group: "today", message_count: 0},
            {id: "c", group: "yesterday", message_count: 4}, {id: "d", group: "weird", message_count: 1},
            {id: "e", group: "older", message_count: 1}], {activeId: "b"}).map((g) => [g.key, g.label, g.rows.map((r) => r.id)])""")
        self.assertEqual(groups, [["today", "asstGroupToday", ["a", "b"]], ["yesterday", "asstGroupYesterday", ["c"]],
                                  ["older", "asstGroupOlder", ["d", "e"]]])
        hidden = self.rule("(m) => m.groupRows([{id: 'b', group: 'today', message_count: 0}], {activeId: null}).length")
        self.assertEqual(hidden, 0, "an empty conversation is a draft and is not listed")

    def test_the_menu_moves_with_the_arrow_keys_and_wraps(self):
        moves = self.rule("""(m) => [m.menuIndex(-1, "ArrowDown", 3), m.menuIndex(2, "ArrowDown", 3), m.menuIndex(0, "ArrowUp", 3),
                               m.menuIndex(1, "Home", 3), m.menuIndex(0, "End", 3), m.menuIndex(0, "a", 3)]""")
        self.assertEqual(moves, [0, 0, 2, 0, 2, None])

    def test_a_prefill_ends_ready_for_typing(self):
        self.assertEqual(self.rule("(m) => [m.prefillText('Test my knowledge on:'), m.prefillText('Teste mes connaissances sur :'), "
                                   "m.prefillText('Explain this textbook page to me.')]"),
                         ["Test my knowledge on: ", "Teste mes connaissances sur : ", "Explain this textbook page to me."])

    def test_continue_learning_shows_what_the_server_says(self):
        views = self.rule("""(m) => [
            m.continueView(null).kind,
            m.continueView({enabled: true, available: false, status: "none"}).kind,
            m.continueView({enabled: true, available: true, status: "none", topic: "Brüche"}),
            m.continueView({enabled: true, available: true, status: "none"}, {busy: true}).kind,
            m.continueView({enabled: true, available: true, status: "pending"}).kind,
            m.continueView({enabled: true, available: true, status: "ready", url: "/?session_id=x", title: "T"}),
            m.continueView({enabled: true, available: true, status: "failed", reason: "subject_needed",
                            subject_choices: [{value: "Mathematics", label: "Mathe"}]}),
            m.continueView({enabled: false, available: true, status: "ready", url: "/x"}).kind]""")
        self.assertEqual(views[0], "none")
        self.assertEqual(views[1], "none")
        self.assertEqual((views[2]["kind"], views[2]["topic"], views[2]["failed"]), ("button", "Brüche", False))
        self.assertEqual(views[3], "pending")
        self.assertEqual(views[4], "pending")
        self.assertEqual(views[5], {"kind": "ready", "url": "/?session_id=x", "title": "T"})
        self.assertEqual((views[6]["failed"], views[6]["needsSubject"], len(views[6]["subjects"])), (True, True, 1))
        self.assertEqual(views[7], "none")

    def test_too_little_context_shows_what_to_add_instead_of_nothing(self):
        views = self.rule("""(m) => [
            m.continueView({enabled: true, available: false, status: "none", reason: "too_little",
                            guidance: "Tell me which topic you are working on."}),
            m.continueView({enabled: true, available: false, status: "none", reason: "too_little"}).kind,
            m.continueView({enabled: true, available: false, status: "none", reason: "not_educational",
                            guidance: "x"}).kind,
            m.continueView({enabled: true, available: false, status: "none", reason: "no_reply", guidance: "x"}).kind]""")
        self.assertEqual(views[0], {"kind": "guidance", "text": "Tell me which topic you are working on."})
        self.assertEqual(views[1:], ["none", "none", "none"])

    def test_failures_are_worded_by_status_and_a_lost_send_is_checked_first(self):
        keys = self.rule("""(m) => [m.failureKey({network: true}), m.failureKey({status: 401}), m.failureKey({toLogin: true}),
                              m.failureKey({status: 413}), m.failureKey({status: 502}), m.failureKey({status: 504}),
                              m.failureKey({status: 500})]""")
        self.assertEqual(keys, ["asstOffline", "asstSessionExpired", "asstSessionExpired", "imgTooLarge",
                                "asstServerSlow", "asstServerSlow", "asstRequestFailed"])
        outcomes = self.rule("""(m) => [m.sendOutcome({network: true}), m.sendOutcome({status: 504}), m.sendOutcome({status: 502}),
                                  m.sendOutcome({status: 409, code: "conversation_busy"}), m.sendOutcome({status: 413, code: "photos_too_large"}),
                                  m.sendOutcome({status: 503, code: "photo_reader_busy"}), m.sendOutcome({status: 503}),
                                  m.sendOutcome({status: 500, code: "internal_error"})]""")
        # A 500 can come after the message was stored: checked first, like a lost connection.
        self.assertEqual(outcomes, ["unknown", "unknown", "unknown", "unsent", "unsent", "unsent", "unknown", "unknown"])
        self.assertEqual(self.rule("(m) => [m.retryAfterSeconds({retryAfter: 5}), m.retryAfterSeconds({details: {retry_after: 9}}), "
                                   "m.retryAfterSeconds({}), m.retryAfterSeconds({retryAfter: 600})]"), [5, 9, 5, 60])

    def test_a_changed_conversation_goes_where_its_date_puts_it(self):
        order = self.rule("""(m) => {
            const now = new Date("2026-11-02T12:00:00Z");
            const rows = [{id: "a", last_message_at: "2026-11-02T10:00:00Z", group: "today"},
                          {id: "b", last_message_at: "2026-10-20T10:00:00Z", group: "older"}];
            const opened = m.upsertRow(rows, {id: "c", last_message_at: "2026-11-01T09:00:00Z"}, {moveToTop: false, now});
            const sent = m.upsertRow(opened, {id: "b", last_message_at: "2026-11-02T11:59:00Z"});
            return [opened.map((r) => r.id + ":" + r.group), sent.map((r) => r.id + ":" + r.group),
                    m.groupFor("2026-10-29T10:00:00Z", now), m.groupFor("2026-10-01T10:00:00Z", now)];
        }""")
        self.assertEqual(order[0], ["a:today", "c:yesterday", "b:older"])
        self.assertEqual(order[1], ["b:today", "a:today", "c:yesterday"])
        self.assertEqual(order[2:], ["previous_7_days", "older"])


class ChatPageTests(unittest.TestCase):
    """The real page, the real app, the AI patched."""

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
        cls.fail_once = set()
        cls.calls = []
        cls.application.create_response.side_effect = cls.fake

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    @classmethod
    def fake(cls, **kwargs):
        if kwargs.get("task_type") != "assistant_chat":
            return cls.audit._fake_response(**kwargs)
        text = str(kwargs.get("input") or "")
        latest = text[text.rfind("Learner: ") + 9:].split("\n\nAssistant:", 1)[0].split("\n\nSTUDENT CONTEXT", 1)[0]
        cls.calls.append(latest)
        for token in list(cls.fail_once):
            if token in latest:
                cls.fail_once.discard(token)
                raise RuntimeError("provider down")
        if "SLOW" in latest:
            time.sleep(2.5)
        reply = (WIDE if "WIDE" in latest else TABLE_MATH if "TABLEMATH" in latest
                 else REPLY_NO_TOPIC if "NOTOPIC" in latest else REPLY)

        class Usage:
            input_tokens = output_tokens = total_tokens = 10

        class Reply:
            usage = Usage()
            model = "test-model"
            output_text = reply
        return Reply()

    # ------------------------------------------------------------------ helpers

    def context(self, width=1280, height=800, *, theme="light"):
        mobile = width <= 430
        context = BROWSER["browser"].new_context(viewport={"width": width, "height": height},
                                                 is_mobile=mobile, has_touch=mobile)
        context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
        context.add_init_script(harness.KATEX_STUB)
        self.audit.block_external(context, self.base)
        self.addCleanup(context.close)
        return context

    def student(self, width=1280, height=800, *, theme="light"):
        """A new signed-in student (tour finished, grade set) and a page."""

        context = self.context(width, height, theme=theme)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        name = f"s{uuid.uuid4().hex[:10]}"
        response = page.request.post(f"{self.base}/register", form={
            "username": name, "email": f"{name}@example.com", "password": "correct-horse-battery", "grade": "8"})
        self.assertLess(response.status, 400)
        page.request.post(f"{self.base}/settings/grade", form={"grade": "8"})
        page.request.post(f"{self.base}/api/tour/complete")
        # The chat's feature tips would start by themselves over the page (docs/TUTORIAL.md).
        page.request.post(f"{self.base}/tutorial/tips", form={"back": "hub"})
        return page

    def api(self, page, method, path, body=None):
        response = page.request.fetch(f"{self.base}{path}", method=method,
                                      data=json.dumps(body) if body is not None else None,
                                      headers={"Content-Type": "application/json"})
        return response.status, (response.json() if "json" in (response.headers.get("content-type") or "") else None)

    def conversation(self, page, text=LONG_QUESTION, title=None):
        status, data = self.api(page, "POST", "/api/assistant/conversations", {"title": title} if title else {})
        self.assertEqual(status, 201)
        cid = data["conversation"]["id"]
        if text:
            status, _ = self.api(page, "POST", f"/api/assistant/conversations/{cid}/messages", {"message": text})
            self.assertEqual(status, 201)
        return cid

    def open(self, page, path="/"):
        page.goto(f"{self.base}{path}", wait_until="load")
        page.wait_for_function("() => window.__asstReady === true")
        return page

    def open_thread(self, page, cid):
        self.open(page, f"/assistant/c/{cid}")
        page.wait_for_selector(".asst-msg-assistant:not(.asst-pending) .asst-body")
        return page

    def focused_id(self, page):
        return page.evaluate("document.activeElement && document.activeElement.id")

    # -------------------------------------------------------------- sidebar

    def test_desktop_sidebar_collapse_is_remembered_across_a_reload(self):
        page = self.open(self.student())
        self.assertEqual(page.get_attribute(".assistant-page", "data-sidebar"), "open")
        self.assertFalse(page.is_visible("#asstDrawerToggle"))
        page.click("#asstSidebarToggle")
        self.assertEqual(page.get_attribute(".assistant-page", "data-sidebar"), "collapsed")
        self.assertEqual(self.focused_id(page), "asstDrawerToggle")
        self.assertEqual(page.get_attribute("#asstDrawerToggle", "aria-label"), "Show conversations")
        page.reload(wait_until="load")
        page.wait_for_function("() => window.__asstReady === true")
        self.assertEqual(page.get_attribute(".assistant-page", "data-sidebar"), "collapsed")
        self.assertFalse(page.is_visible("#asstNew"))
        page.click("#asstDrawerToggle")
        self.assertEqual(page.get_attribute(".assistant-page", "data-sidebar"), "open")
        self.assertEqual(page.evaluate("localStorage.getItem('learnova:asst-sidebar')"), "open")

    def test_the_phone_drawer_traps_focus_and_closes_on_escape_and_on_the_veil(self):
        page = self.open(self.student(390, 844))
        self.assertFalse(page.is_visible("#asstNew"), "the list is off-canvas on a phone")
        page.click("#asstDrawerToggle")
        page.wait_for_timeout(300)
        self.assertEqual(page.get_attribute("#asstDrawerToggle", "aria-expanded"), "true")
        self.assertTrue(page.evaluate("document.querySelector('#asstMain').inert"))
        self.assertEqual(self.focused_id(page), "asstNew")
        box = page.locator("#asstSidebar").bounding_box()
        self.assertGreaterEqual(box["x"], -1)
        self.assertLessEqual(box["x"] + box["width"], 391)
        for _ in range(12):   # Tab never leaves the drawer
            page.keyboard.press("Tab")
            self.assertTrue(page.evaluate("document.querySelector('#asstSidebar').contains(document.activeElement)"))
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        self.assertEqual(page.get_attribute("#asstDrawerToggle", "aria-expanded"), "false")
        self.assertFalse(page.evaluate("document.querySelector('#asstMain').inert"))
        self.assertEqual(self.focused_id(page), "asstDrawerToggle")
        self.assertFalse(page.is_visible("#asstNew"))
        page.click("#asstDrawerToggle")
        page.wait_for_timeout(300)
        page.mouse.click(380, 600)   # the veil beside the drawer
        page.wait_for_timeout(300)
        self.assertEqual(page.get_attribute("#asstDrawerToggle", "aria-expanded"), "false")
        self.assertTrue(page.is_hidden("#asstScrim"))
        self.assertEqual(page.evaluate("document.documentElement.scrollWidth"), 390)

    # ----------------------------------------------------------- row menu

    def test_the_row_menu_works_with_the_keyboard(self):
        page = self.student()
        self.conversation(page, "Was ist Photosynthese? Erklär es mir bitte genau.")
        self.open(page)
        page.wait_for_selector(".asst-row .asst-row-menu")
        page.focus(".asst-row .asst-row-menu")
        page.keyboard.press("Enter")
        self.assertTrue(page.is_visible("#asstItemMenu"))
        self.assertEqual(page.get_attribute(".asst-row .asst-row-menu", "aria-expanded"), "true")
        self.assertEqual(self.focused_id(page), "asstRename")
        expected = ["asstArchive", "asstDelete", "asstRename"]
        for want in expected:
            page.keyboard.press("ArrowDown")
            self.assertEqual(self.focused_id(page), want)
        page.keyboard.press("End")
        self.assertEqual(self.focused_id(page), "asstDelete")
        page.keyboard.press("Home")
        self.assertEqual(self.focused_id(page), "asstRename")
        page.keyboard.press("Escape")
        self.assertTrue(page.is_hidden("#asstItemMenu"))
        self.assertEqual(page.get_attribute(".asst-row .asst-row-menu", "aria-expanded"), "false")
        self.assertTrue(page.evaluate("document.activeElement.classList.contains('asst-row-menu')"))
        page.keyboard.press("ArrowUp")
        self.assertEqual(self.focused_id(page), "asstDelete", "ArrowUp opens on the last item")
        page.mouse.click(900, 300)   # outside
        self.assertTrue(page.is_hidden("#asstItemMenu"))

    def test_rename_saves_the_students_title(self):
        page = self.student()
        cid = self.conversation(page, "Was ist Photosynthese? Erklär es mir bitte genau.")
        self.open(page)
        page.click(".asst-row .asst-row-menu")
        page.click("#asstRename")
        self.assertTrue(page.evaluate("document.querySelector('#asstRenameDialog').open"))
        self.assertEqual(page.input_value("#asstRenameInput"), "Was ist Photosynthese?")
        page.fill("#asstRenameInput", "Biologie: Photosynthese")
        page.keyboard.press("Enter")
        page.wait_for_function("() => !document.querySelector('#asstRenameDialog').open")
        page.wait_for_selector(".asst-list-title:text-is('Biologie: Photosynthese')")
        _, data = self.api(page, "GET", f"/api/assistant/conversations/{cid}")
        self.assertEqual((data["conversation"]["title"], data["conversation"]["title_source"]),
                         ("Biologie: Photosynthese", "user"))
        self.assertTrue(page.evaluate("document.activeElement.classList.contains('asst-row-menu')"))

    def test_the_delete_dialog_says_it_is_permanent_and_cancel_keeps_everything(self):
        page = self.student()
        cid = self.conversation(page, "Was ist Photosynthese? Erklär es mir bitte genau.")
        self.open_thread(page, cid)
        page.click(".asst-row .asst-row-menu")
        page.click("#asstDelete")
        self.assertTrue(page.evaluate("document.querySelector('#asstDeleteDialog').open"))
        self.assertEqual(page.text_content("#asstDeleteHeading"), "Delete “Was ist Photosynthese?”?")
        text = page.text_content("#asstDeleteText")
        self.assertIn("permanently deletes", text)
        self.assertIn("cannot be undone", text)
        self.assertEqual(self.focused_id(page), "asstDeleteCancel")
        tops = {round(page.locator(f"#{name}").bounding_box()["y"])
                for name in ("asstDeleteCancel", "asstDeleteArchive", "asstDeleteConfirm")}
        self.assertEqual(len(tops), 1, "on a desktop the three buttons share one row")
        page.click("#asstDeleteCancel")
        self.assertFalse(page.evaluate("document.querySelector('#asstDeleteDialog').open"))
        self.assertEqual(page.locator(".asst-row").count(), 1)
        self.assertEqual(self.api(page, "GET", f"/api/assistant/conversations/{cid}")[0], 200)
        page.keyboard.press("Escape")
        page.click(".asst-row .asst-row-menu")
        page.click("#asstDelete")
        page.click("#asstDeleteConfirm")
        page.wait_for_function("() => !document.querySelector('.asst-row')")
        self.assertEqual(self.api(page, "GET", f"/api/assistant/conversations/{cid}")[0], 404)
        self.assertTrue(page.url.endswith("/assistant"), "the deleted conversation's address is left")
        self.assertTrue(page.is_visible("#asstWelcome"))

    def test_archive_moves_the_row_to_archived_and_restore_brings_it_back(self):
        page = self.student()
        cid = self.conversation(page, "Was ist Photosynthese? Erklär es mir bitte genau.")
        self.open(page)
        page.click(".asst-row .asst-row-menu")
        self.assertEqual(page.text_content("#asstArchive"), "Archive")
        page.click("#asstArchive")
        page.wait_for_function("() => !document.querySelector('#asstList .asst-row')")
        self.assertTrue(self.api(page, "GET", f"/api/assistant/conversations/{cid}")[1]["conversation"]["archived"])
        page.click("#asstShowArchived")
        self.assertEqual(page.get_attribute("#asstShowArchived", "aria-expanded"), "true")
        page.wait_for_selector("#asstArchivedList .asst-row")
        page.click("#asstArchivedList .asst-row-menu")
        self.assertEqual(page.text_content("#asstArchive"), "Restore")
        page.click("#asstArchive")
        page.wait_for_selector("#asstList .asst-row")
        page.wait_for_function("() => !document.querySelector('#asstArchivedList .asst-row')")
        self.assertFalse(self.api(page, "GET", f"/api/assistant/conversations/{cid}")[1]["conversation"]["archived"])

    def test_search_finds_message_text_and_shows_a_snippet(self):
        page = self.student()
        self.conversation(page, "Die Mitochondrien sind die Kraftwerke der Zelle, oder? Erklär das bitte.")
        self.conversation(page, "Wann benutze ich das Present Perfect im Englischen?")
        self.open(page)
        page.wait_for_function("() => document.querySelectorAll('#asstList .asst-row').length === 2")
        page.fill("#asstSearch", "Kraftwerk")
        page.wait_for_function("() => document.querySelectorAll('#asstList .asst-row').length === 1")
        snippet = page.text_content("#asstList .asst-list-snippet")
        self.assertIn("Kraftwerk", snippet)
        page.fill("#asstSearch", "zzqx")
        page.wait_for_function("() => !document.querySelector('#asstList .asst-row')")
        self.assertEqual(page.text_content("#asstListEmpty"), "No conversations match.")
        page.fill("#asstSearch", "")
        page.wait_for_function("() => document.querySelectorAll('#asstList .asst-row').length === 2")

    # ------------------------------------------------------------- sending

    def test_a_double_enter_on_the_first_message_sends_once(self):
        page = self.open(self.student())
        posts = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        page.fill("#asstInput", "Erklär mir bitte die Lösungsformel für quadratische Gleichungen.")
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        page.wait_for_selector(".asst-msg-assistant:not(.asst-pending) .asst-body")
        _, data = self.api(page, "GET", "/api/assistant/conversations")
        self.assertEqual(len(data["conversations"]), 1)
        cid = data["conversations"][0]["id"]
        _, thread = self.api(page, "GET", f"/api/assistant/conversations/{cid}")
        self.assertEqual([item["role"] for item in thread["conversation"]["messages"]], ["user", "assistant"])
        self.assertEqual(sum(url.endswith("/messages") for url in posts), 1)
        self.assertEqual(sum(url.endswith("/api/assistant/conversations") for url in posts), 1)
        self.assertTrue(page.url.endswith(f"/assistant/c/{cid}"))
        self.assertEqual(page.input_value("#asstInput"), "")

    def test_a_reply_lands_in_its_own_thread_after_switching(self):
        page = self.student()
        other = self.conversation(page, "Wann benutze ich das Present Perfect im Englischen?")
        self.open(page)
        page.fill("#asstInput", "SLOW Was ist ein Bruch? Erklär es mir bitte einfach.")
        page.click("#asstSend")
        page.wait_for_selector(".asst-pending")
        self.assertTrue(page.is_visible("#asstStop"))
        page.click(f".asst-row[data-id='{other}'] .asst-list-item")
        page.wait_for_selector(".asst-msg-user .asst-body:text('Present Perfect')")
        self.assertFalse(page.is_visible("#asstStop"), "the other conversation can send meanwhile")
        time.sleep(3)
        page.wait_for_timeout(300)
        texts = page.locator(".asst-msg-user .asst-body").all_text_contents()
        self.assertEqual(texts, ["Wann benutze ich das Present Perfect im Englischen?"])
        self.assertEqual(page.locator(".asst-msg-assistant").count(), 1)
        self.assertTrue(page.url.endswith(f"/assistant/c/{other}"))
        _, data = self.api(page, "GET", "/api/assistant/conversations")
        new = next(row["id"] for row in data["conversations"] if row["id"] != other)
        page.click(f".asst-row[data-id='{new}'] .asst-list-item")
        page.wait_for_selector(".asst-msg-user .asst-body:text('Was ist ein Bruch')")
        self.assertEqual(page.locator(".asst-msg-assistant:not(.asst-pending)").count(), 1)

    def test_a_reload_comes_back_to_the_open_conversation(self):
        page = self.open(self.student())
        page.fill("#asstInput", "Erklär mir bitte die Lösungsformel für quadratische Gleichungen.")
        page.click("#asstSend")
        page.wait_for_selector(".asst-msg-assistant:not(.asst-pending) .asst-body")
        self.assertIn("/assistant/c/", page.url)
        page.reload(wait_until="load")
        page.wait_for_selector(".asst-msg-user .asst-body")
        self.assertEqual(page.text_content(".asst-msg-user .asst-body"),
                         "Erklär mir bitte die Lösungsformel für quadratische Gleichungen.")
        self.assertEqual(page.text_content("#asstThreadTitle"),
                         "Erklär mir bitte die Lösungsformel für quadratische Gleichungen.")
        self.assertEqual(page.get_attribute(".asst-row.is-active .asst-list-item", "aria-current"), "page")
        page.click("#asstNew")
        self.assertTrue(page.url.endswith("/assistant"), "New chat leaves the conversation's address")
        self.assertTrue(page.is_visible("#asstWelcome"))

    def test_stop_says_so_and_the_stored_reply_appears_later(self):
        page = self.open(self.student())
        page.fill("#asstInput", "SLOW Erklär mir den Satz des Pythagoras mit einem Beispiel.")
        page.click("#asstSend")
        page.wait_for_selector(".asst-pending")
        page.click("#asstStop")
        self.assertIn("Stopped.", page.text_content(".asst-stopped"))
        self.assertTrue(page.is_visible("#asstSend"))
        self.assertTrue(page.is_hidden("#asstStop"))
        page.wait_for_selector(".asst-msg-assistant:not(.asst-pending):not(.asst-stopped-msg) .katex", timeout=12000)
        self.assertEqual(page.locator(".asst-stopped").count(), 0)

    def test_regenerate_replaces_the_newest_reply(self):
        page = self.student()
        cid = self.conversation(page)
        self.open_thread(page, cid)
        # SQLite may give the new reply the old one's id, so the old node is marked instead.
        page.evaluate("document.querySelector('.asst-msg-assistant').dataset.old = '1'")
        page.click(".asst-msg-assistant button[aria-label='Regenerate']")
        page.wait_for_selector(".asst-msg-assistant:not(.asst-pending):not([data-old]) .katex")
        self.assertEqual(page.locator(".asst-msg-assistant").count(), 1)
        _, data = self.api(page, "GET", f"/api/assistant/conversations/{cid}")
        self.assertEqual([item["role"] for item in data["conversation"]["messages"]], ["user", "assistant"])

    def test_retry_on_a_failed_turn_answers_without_repeating_the_question(self):
        page = self.student()
        token = f"FAIL{uuid.uuid4().hex[:6]}"
        self.fail_once.add(token)
        cid = self.conversation(page, f"{token} Erklär mir bitte die Lösungsformel genau.")
        self.open(page, f"/assistant/c/{cid}")
        page.wait_for_selector(".asst-msg-error")
        self.assertEqual(page.locator(".asst-msg-error button:has-text('Retry')").count(), 1)
        page.click(".asst-msg-error button:has-text('Retry')")
        page.wait_for_selector(".asst-msg-assistant:not(.asst-msg-error):not(.asst-pending) .katex")
        self.assertEqual(page.locator(".asst-msg-error").count(), 0)
        self.assertEqual(page.locator(".asst-msg-user").count(), 1)

    def test_an_unreadable_photo_offers_a_new_photo_instead_of_retry(self):
        page = self.student()
        cid = self.conversation(page)
        conversation = self.api(page, "GET", f"/api/assistant/conversations/{cid}")[1]["conversation"]
        conversation["messages"][-1].update(error=True, error_code="image_unreadable", chips=[],
                                            content="I could not read anything in your photo.")
        page.route(f"**/api/assistant/conversations/{cid}", lambda route: route.fulfill(
            status=200, body=json.dumps({"ok": True, "conversation": conversation}),
            headers={"Content-Type": "application/json"}))
        self.open(page, f"/assistant/c/{cid}")
        page.wait_for_selector(".asst-msg-notice")
        self.assertEqual(page.locator(".asst-msg-notice button:has-text('Send a new photo')").count(), 1)
        self.assertEqual(page.locator(".asst-msg-notice button:has-text('Retry')").count(), 0)

    def test_a_tap_answer_sends_it(self):
        page = self.student()
        cid = self.conversation(page)
        self.open_thread(page, cid)
        chips = page.locator(".asst-chip").all_text_contents()
        self.assertEqual(chips[:2], ["x = 3 und x = -1", "x = 1"])
        page.click(".asst-chip >> nth=0")
        page.wait_for_function("() => document.querySelectorAll('.asst-msg-assistant:not(.asst-pending)').length === 2")
        self.assertEqual(page.locator(".asst-msg-user .asst-body").all_text_contents()[-1], "x = 3 und x = -1")
        self.assertTrue(self.calls[-1].startswith("x = 3 und x = -1"), self.calls[-1][:80])

    def test_a_busy_conversation_gives_the_message_back(self):
        page = self.student()
        cid = self.conversation(page)
        self.open_thread(page, cid)
        page.route("**/messages", lambda route: route.fulfill(
            status=409, headers={"Content-Type": "application/json", "Retry-After": "5"},
            body=json.dumps({"ok": False, "code": "conversation_busy", "details": {"retry_after": 5},
                             "error": "An answer is still being written for this conversation. Wait a moment."})))
        page.fill("#asstInput", "Und wie geht das mit der pq-Formel?")
        page.click("#asstSend")
        page.wait_for_selector("#asstToast:not(.hidden)")
        self.assertEqual(page.text_content("#asstToast .asst-toast-text"), "Still answering…")
        self.assertEqual(page.input_value("#asstInput"), "Und wie geht das mit der pq-Formel?")
        self.assertEqual(page.locator(".asst-msg-user").count(), 1, "the refused message is not left in the thread")

    def test_a_reply_is_announced_in_one_sentence_not_by_a_live_thread(self):
        page = self.open(self.student())
        self.assertIsNone(page.get_attribute("#asstThread", "aria-live"))
        self.assertIsNone(page.get_attribute("#asstAttachTray", "aria-live"), "no live region around live captions")
        page.fill("#asstInput", "Erklär mir bitte die Lösungsformel für quadratische Gleichungen.")
        page.click("#asstSend")
        page.wait_for_selector(".asst-msg-assistant:not(.asst-pending) .asst-body")
        page.wait_for_function("() => document.querySelector('#asstAnnounce').textContent !== ''")
        self.assertEqual(page.get_attribute("#asstAnnounce", "role"), "status")
        self.assertEqual(page.text_content("#asstAnnounce"), "The assistant answered.")

    def test_a_suggestion_fills_the_box_and_sends_nothing(self):
        page = self.open(self.student())
        posts = []
        page.on("request", lambda request: posts.append(request.url) if request.method == "POST" else None)
        page.click(".asst-suggestion:has-text('Test my knowledge')")
        self.assertEqual(page.input_value("#asstInput"), "Test my knowledge on: ")
        self.assertEqual(self.focused_id(page), "asstInput")
        self.assertEqual(page.evaluate("document.querySelector('#asstInput').selectionStart"),
                         len("Test my knowledge on: "), "the cursor waits at the end")
        page.wait_for_timeout(400)
        self.assertEqual(posts, [], "a suggestion starts a message; it never sends one")
        self.assertTrue(page.is_visible("#asstWelcome"))
        self.assertEqual(self.api(page, "GET", "/api/assistant/conversations")[1]["conversations"], [])

    def test_a_lost_send_is_retried_with_the_same_client_id(self):
        """A 500 (or a lost connection) is checked before the text is given back; when the
        check fails too, Retry re-sends with the same client_message_id, so the server can
        answer from what it stored instead of storing the message twice."""

        page = self.student()
        cid = self.conversation(page)
        self.open_thread(page, cid)
        bodies, checks = [], []

        def messages(route):
            bodies.append(json.loads(route.request.post_data or "{}"))
            if len(bodies) == 1:
                route.fulfill(status=500, headers={"Content-Type": "application/json"},
                              body=json.dumps({"ok": False, "code": "internal_error",
                                               "error": "Something went wrong. Please try again."}))
            else:
                route.continue_()

        def status(route):
            if route.request.method == "GET" and bodies and not checks:
                checks.append(route.request.url)
                route.abort()          # the status check is lost as well: still unknown
            else:
                route.continue_()

        page.route("**/messages", messages)
        page.route(f"**/api/assistant/conversations/{cid}", status)
        page.fill("#asstInput", "Und wie geht das mit der pq-Formel?")
        page.click("#asstSend")
        page.wait_for_selector("#asstToast:not(.hidden) .asst-toast-action")
        self.assertEqual(len(checks), 1, "a 500 is checked like a lost connection, not taken as unsent")
        self.assertEqual(page.input_value("#asstInput"), "Und wie geht das mit der pq-Formel?")
        page.click("#asstToast .asst-toast-action")
        page.wait_for_function("() => document.querySelectorAll('.asst-msg-assistant:not(.asst-pending)').length === 2")
        self.assertEqual(len(bodies), 2)
        self.assertEqual(bodies[1]["client_message_id"], bodies[0]["client_message_id"])
        self.assertEqual(bodies[1]["message"], bodies[0]["message"])

    def test_a_500_after_the_message_was_stored_is_not_given_back(self):
        page = self.student()
        cid = self.conversation(page)
        self.open_thread(page, cid)
        sent = []

        def stored_then_500(route):
            response = route.fetch()     # the server stores the message and its reply ...
            sent.append(response.status)
            route.fulfill(status=500, headers={"Content-Type": "application/json"},   # ... the answer is lost
                          body=json.dumps({"ok": False, "code": "internal_error",
                                           "error": "Something went wrong. Please try again."}))

        page.route("**/messages", stored_then_500)
        page.fill("#asstInput", "Und wie geht das mit der pq-Formel?")
        page.click("#asstSend")
        page.wait_for_function("() => document.querySelectorAll('.asst-msg-user').length === 2")
        page.wait_for_timeout(300)
        self.assertEqual(sent, [201])
        self.assertEqual(page.input_value("#asstInput"), "", "a stored message is not offered back to send twice")
        _, data = self.api(page, "GET", f"/api/assistant/conversations/{cid}")
        self.assertEqual([item["role"] for item in data["conversation"]["messages"]],
                         ["user", "assistant", "user", "assistant"])

    def test_a_failed_regenerate_puts_the_good_reply_back(self):
        page = self.student()
        token = f"KEEP{uuid.uuid4().hex[:6]}"
        cid = self.conversation(page, f"{token} Erklär mir bitte die Lösungsformel genau.")
        self.open_thread(page, cid)
        before = self.api(page, "GET", f"/api/assistant/conversations/{cid}")[1]["conversation"]["messages"]
        self.fail_once.add(token)       # the provider is down for the regenerate
        page.click(".asst-msg-assistant button[aria-label='Regenerate']")
        page.wait_for_selector("#asstToast:not(.hidden)")
        self.assertEqual(page.get_attribute("#asstToast", "role"), "alert")
        page.wait_for_selector(".asst-msg-assistant .katex")
        self.assertEqual(page.locator(".asst-msg-error").count(), 0, "no failed turn replaces the good reply")
        self.assertEqual(page.locator(".asst-msg-assistant button[aria-label='Regenerate']").count(), 1)
        after = self.api(page, "GET", f"/api/assistant/conversations/{cid}")[1]["conversation"]["messages"]
        self.assertEqual([(item["id"], item["content"]) for item in after], [(item["id"], item["content"]) for item in before])

    # ------------------------------------------------------------- photos

    @staticmethod
    def png_base64(colour):
        output = io.BytesIO()
        Image.new("RGB", (320, 240), colour).save(output, format="PNG")
        return base64.b64encode(output.getvalue()).decode()

    FILES_JS = """(data) => { const bytes = Uint8Array.from(atob(data), (c) => c.charCodeAt(0));
        const transfer = new DataTransfer();
        transfer.items.add(new File([bytes], "notes.png", {type: "image/png"}));
        return transfer; }"""

    def test_photos_can_be_dropped_on_the_chat_or_pasted_into_the_box(self):
        page = self.open(self.student())
        dropped = page.evaluate_handle(self.FILES_JS, self.png_base64((240, 200, 90)))
        page.dispatch_event("#asstMain", "dragenter", {"dataTransfer": dropped})
        self.assertTrue(page.is_visible("#asstDropZone"), "the drop zone shows while a photo is dragged over")
        page.dispatch_event("#asstMain", "drop", {"dataTransfer": dropped})
        self.assertTrue(page.is_hidden("#asstDropZone"))
        page.wait_for_selector("#asstAttachTray .attach-item.is-ready")
        pasted = page.evaluate_handle(self.FILES_JS, self.png_base64((60, 140, 220)))
        prevented = page.evaluate("""(transfer) => {
            const event = new ClipboardEvent("paste", {clipboardData: transfer, bubbles: true, cancelable: true});
            document.querySelector("#asstInput").dispatchEvent(event);
            return event.defaultPrevented; }""", pasted)
        self.assertTrue(prevented, "a pasted picture does not also paste text into the box")
        page.wait_for_function("() => document.querySelectorAll('#asstAttachTray .attach-item.is-ready').length === 2")
        self.assertEqual(page.input_value("#asstInput"), "")

    def upload(self, page, colour=None):
        data = self.audit._note_png() if colour is None else base64.b64decode(self.png_base64(colour))
        response = page.request.post(f"{self.base}/api/chat-attachments", multipart={
            "purpose": "chat", "file": {"name": "notes.png", "mimeType": "image/png", "buffer": data}})
        self.assertLess(response.status, 300, response.text()[:200])
        return response.json()["attachment"]["id"]

    def test_photos_waiting_from_before_a_reload_come_back_to_the_tray(self):
        page = self.student()
        attachment = self.upload(page)
        self.open(page)
        page.wait_for_selector("#asstAttachTray .attach-item.is-ready")
        self.assertEqual(page.evaluate("document.querySelectorAll('#asstAttachTray .attach-item').length"), 1)
        self.assertIn(attachment, page.get_attribute("#asstAttachTray img", "src"))

    def test_a_refused_photo_message_keeps_its_photos_and_text(self):
        page = self.student()
        self.upload(page)
        self.open(page)
        page.wait_for_selector("#asstAttachTray .attach-item.is-ready")
        page.route("**/messages", lambda route: route.fulfill(
            status=413, headers={"Content-Type": "application/json"},
            body=json.dumps({"ok": False, "code": "photos_too_large", "details": {"max_photos": 1},
                             "error": "These photos are too much to read at once. Send one photo at a time."})))
        page.fill("#asstInput", "Ist mein Rechenweg richtig?")
        page.click("#asstSend")
        page.wait_for_selector("#asstToast:not(.hidden)")
        self.assertIn("too much to read", page.text_content("#asstToast"))
        page.wait_for_selector("#asstAttachTray .attach-item.is-ready")
        self.assertEqual(page.input_value("#asstInput"), "Ist mein Rechenweg richtig?")
        self.assertTrue(page.is_visible("#asstWelcome"), "nothing half-sent is left behind")

    # ---------------------------------------------------- Continue Learning

    def test_continue_learning_loads_once_for_a_double_click_then_links_the_lesson(self):
        page = self.student()
        cid = self.conversation(page)
        self.open_thread(page, cid)
        button = page.locator(".asst-continue-button")
        self.assertEqual(button.text_content().strip(), "Continue learning: Quadratische Gleichungen")
        held, posts = [], []

        def hold(route):
            if route.request.method == "POST":
                posts.append(route.request.url)
                held.append(route)
            else:
                route.continue_()

        page.route("**/lesson", hold)
        button.dblclick()
        page.wait_for_selector(".asst-continue-button[aria-busy=true]")
        self.assertTrue(page.is_disabled(".asst-continue-button"))
        self.assertIn("Building your lesson", page.text_content(".asst-continue-button"))
        page.click(".asst-continue-button", force=True)
        page.wait_for_timeout(200)
        self.assertEqual(len(posts), 1, "one request for a double click and a third tap")
        held[0].continue_()
        page.wait_for_selector(".asst-continue-link", timeout=20000)
        self.assertTrue(page.get_attribute(".asst-continue-link", "href").startswith("/?session_id="))
        self.assertIn("Open lesson:", page.text_content(".asst-continue-link"))
        page.reload(wait_until="load")
        page.wait_for_selector(".asst-continue-link")   # the link survives a reload

    def test_continue_learning_asks_for_the_subject_when_it_is_unknown(self):
        page = self.student()
        cid = self.conversation(page, "NOTOPIC Es geht um die Zellatmung und die Mitochondrien in der Zelle, erklär das bitte.")
        self.open_thread(page, cid)
        page.wait_for_selector(".asst-continue-button")
        page.click(".asst-continue-button")
        page.wait_for_selector(".asst-continue .asst-chip")
        self.assertEqual(page.text_content(".asst-continue .asst-continue-note"), "Which subject?")
        page.click(".asst-continue .asst-chip:text('Biology')")
        page.wait_for_selector(".asst-continue-link", timeout=20000)
        self.assertEqual(self.api(page, "GET", f"/api/assistant/conversations/{cid}")[1]["conversation"]["subject"],
                         "Biology")

    # ------------------------------------------------------ layout, theme

    def test_nothing_scrolls_sideways_at_320px_with_a_long_formula_and_a_wide_table(self):
        page = self.student(320, 568)
        cid = self.conversation(page, "WIDE Zeig mir bitte eine sehr lange Rechnung mit Brüchen und einer Tabelle.")
        self.open_thread(page, cid)
        page.wait_for_selector(".asst-body table")
        sizes = page.evaluate("""() => ({doc: document.documentElement.scrollWidth, view: window.innerWidth,
            thread: [document.querySelector('#asstThread').scrollWidth, document.querySelector('#asstThread').clientWidth],
            body: [...document.querySelectorAll('.asst-msg-assistant .asst-body')].map((n) => [n.scrollWidth, n.clientWidth])})""")
        self.assertLessEqual(sizes["doc"], sizes["view"])
        self.assertLessEqual(sizes["thread"][0], sizes["thread"][1] + 1)
        for scroll, client in sizes["body"]:
            self.assertLessEqual(scroll, client + 1)
        result = page.evaluate(self.audit.AUDIT_JS)
        self.assertEqual(self.audit.blocking(result, 320, logged_in=True), [])

    def test_the_chat_pages_pass_the_layout_audit_with_the_drawer_light_and_dark(self):
        """scripts/mobile_audit.py's own checks: no sideways scroll, nothing clipped, the menu
        and (up to 900px) the conversation drawer open, fit and close; no light-on-light text."""

        page = self.student()
        cid = self.conversation(page)
        state = page.context.storage_state()
        for theme in ("light", "dark"):
            for width, height in ((320, 568), (390, 844), (768, 1024), (1440, 900)):
                mobile = width <= 430
                context = BROWSER["browser"].new_context(viewport={"width": width, "height": height}, is_mobile=mobile,
                                                         has_touch=mobile, storage_state=state)
                context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
                context.add_init_script(harness.KATEX_STUB)
                self.audit.block_external(context, self.base)
                self.addCleanup(context.close)
                for path in ("/", f"/assistant/c/{cid}"):
                    with self.subTest(theme=theme, width=width, path=path):
                        result = self.audit.audit_page(context, self.base + path, mobile_menu=width < 1024, open_chat=False,
                                                       screenshot=None, drawer=width <= self.audit.DRAWER_MAX_WIDTH)
                        self.assertEqual(self.audit.blocking(result, width, logged_in=True), [], result.get("drawer"))
                        if width <= self.audit.DRAWER_MAX_WIDTH:
                            self.assertTrue(result["drawer"]["closes_on_escape"])

    def test_the_composer_stays_above_a_shrunken_visual_viewport(self):
        page = self.student(390, 844)
        cid = self.conversation(page)
        self.open_thread(page, cid)
        page.evaluate("""() => {
            Object.defineProperty(window.visualViewport, 'height', {configurable: true, get: () => 420});
            window.visualViewport.dispatchEvent(new Event('resize'));
        }""")
        page.wait_for_timeout(250)
        self.assertEqual(page.evaluate("document.documentElement.style.getPropertyValue('--asst-vvh')"), "420px")
        box = page.locator("#asstComposer").bounding_box()
        self.assertLessEqual(box["y"] + box["height"], 421)
        self.assertGreater(box["y"], 72)
        thread = page.locator("#asstThread").bounding_box()
        self.assertGreater(thread["height"], 80)

    def test_the_dark_theme_has_no_light_text_on_a_light_surface(self):
        page = self.student(theme="dark")
        cid = self.conversation(page)
        self.open_thread(page, cid)
        self.assertEqual(page.evaluate("document.documentElement.dataset.theme"), "dark")
        self.assertEqual(page.evaluate(self.audit.AUDIT_JS)["invisibleText"], [])
        page.click(".asst-row .asst-row-menu")
        self.assertEqual(page.evaluate(self.audit.AUDIT_JS)["invisibleText"], [])
        page.click("#asstDelete")
        self.assertEqual(page.evaluate(self.audit.AUDIT_JS)["invisibleText"], [])
        page.click("#asstDeleteCancel")
        phone = self.student(390, 844, theme="dark")
        self.conversation(phone)
        self.open(phone)
        phone.click("#asstDrawerToggle")
        phone.wait_for_timeout(300)
        self.assertEqual(phone.evaluate(self.audit.AUDIT_JS)["invisibleText"], [])
        self.assertEqual(self.errors, [])

    # ------------------------------------------------ accessibility (review fixes)

    FOCUS_JS = """() => {
        const el = document.activeElement;
        if (!el || el === document.body) return null;
        let ring = el;
        if (el.matches('input[type=file], .asst-deep input')) ring = el.closest('label');
        const cs = getComputedStyle(ring);
        const shell = el.matches('#asstInput') ? getComputedStyle(el.closest('.asst-composer-shell')) : null;
        return {name: (el.id || el.className || el.tagName).toString().slice(0, 40), inSidebar: Boolean(el.closest('#asstSidebar')),
                style: cs.outlineStyle, width: parseFloat(cs.outlineWidth), color: cs.outlineColor,
                offset: parseFloat(cs.outlineOffset), shadow: shell ? shell.boxShadow : ''};
    }"""

    def test_keyboard_focus_shows_the_strong_ring_everywhere_and_the_sidebar_never_clips_it(self):
        page = self.student()
        self.conversation(page, "Was ist Photosynthese? Erklär es mir bitte genau.")
        self.open(page)
        page.wait_for_selector(".asst-row .asst-row-menu")
        focus = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--ln-focus').trim()")
        self.assertEqual(focus, "#2c6bed")
        seen = []
        for _ in range(45):
            page.keyboard.press("Tab")
            ring = page.evaluate(self.FOCUS_JS)
            if ring is None:
                continue
            if ring["name"] == "asstInput":
                page.wait_for_timeout(250)      # the shell's ring fades in (140 ms)
                ring = page.evaluate(self.FOCUS_JS)
            seen.append(ring)
            with self.subTest(control=ring["name"]):
                if ring["name"] == "asstInput":
                    self.assertIn("rgb(44, 107, 237)", ring["shadow"], "the message box's shell shows the ring")
                    continue
                self.assertEqual(ring["style"], "solid")
                self.assertGreaterEqual(ring["width"], 2)
                self.assertEqual(ring["color"], "rgb(44, 107, 237)", "the strong ring, not 24% violet")
                if ring["inSidebar"]:
                    self.assertLessEqual(ring["offset"], -ring["width"], "drawn inside: the scroller cannot clip it")
        names = {ring["name"] for ring in seen}
        for expected in ("asstNew", "asstSearch", "asstSend"):
            self.assertIn(expected, names)
        self.assertTrue(any("asst-row-menu" in name for name in names), names)

    def test_text_in_the_chat_meets_contrast_in_both_themes(self):
        for theme in ("light", "dark"):
            with self.subTest(theme=theme):
                page = self.student(theme=theme)
                # Colours are measured at rest: no theme or hover transition half-way.
                page.emulate_media(reduced_motion="reduce")
                token = f"FAIL{uuid.uuid4().hex[:6]}"
                self.fail_once.add(token)
                failed = self.conversation(page, f"{token} Erklär mir bitte die Lösungsformel genau.")
                cid = self.conversation(page)
                self.open_thread(page, cid)
                page.wait_for_selector(".asst-continue-button")
                self.assertEqual(page.evaluate(CONTRAST_JS, "#asstMain, #asstSidebar"), [])
                # Reply paragraphs read in the text colour, like the list items beside them.
                colours = page.evaluate("""() => [getComputedStyle(document.querySelector('.asst-msg-assistant .asst-body p')).color,
                                                  getComputedStyle(document.querySelector('.asst-msg-assistant .asst-body')).color]""")
                self.assertEqual(colours[0], colours[1])
                # Filled controls: white on both gradient stops, 4.5:1 or more.
                stops = page.evaluate("""() => ['.asst-new', '.asst-send'].map((s) => getComputedStyle(document.querySelector(s)).backgroundImage)""")
                for image in stops:
                    self.assertIn("rgb(110, 92, 255)", image)
                    self.assertIn("rgb(77, 61, 224)", image)
                page.click(".asst-row .asst-row-menu >> nth=0")
                self.assertEqual(page.evaluate(CONTRAST_JS, "#asstItemMenu"), [])
                page.click("#asstDelete")
                self.assertEqual(page.evaluate(CONTRAST_JS, "#asstDeleteDialog"), [])
                page.click("#asstDeleteCancel")
                page.click(".asst-row .asst-row-menu >> nth=0")
                page.click("#asstRename")
                self.assertEqual(page.evaluate(CONTRAST_JS, "#asstRenameDialog"), [])
                page.keyboard.press("Escape")
                self.open(page, f"/assistant/c/{failed}")
                page.wait_for_selector(".asst-msg-error button:has-text('Retry')")
                self.assertEqual(page.evaluate(CONTRAST_JS, "#asstMain"), [])
                retry = page.locator(".asst-msg-error .asst-actions").bounding_box()
                bubble = page.locator(".asst-msg-error .asst-body").bounding_box()
                self.assertAlmostEqual(retry["x"], bubble["x"], delta=1, msg="Retry lines up with the bubble")

    def test_a_used_up_allowance_is_a_note_with_a_premium_button_not_an_error(self):
        page = self.student()
        cid = self.conversation(page)
        self.open_thread(page, cid)
        page.route("**/lesson", lambda route: route.fulfill(
            status=429, headers={"Content-Type": "application/json"},
            body=json.dumps({"ok": False, "code": "free_lessons_used", "details": {"upgrade_url": "/settings#subscription"},
                             "error": "You have used today's free lessons."})) if route.request.method == "POST"
            else route.continue_())
        page.click(".asst-continue-button")
        page.wait_for_selector(".asst-continue-premium")
        note = page.locator(".asst-continue .asst-continue-note")
        self.assertEqual(note.get_attribute("role"), "status")
        self.assertNotIn("is-error", note.get_attribute("class"))
        self.assertEqual(page.get_attribute(".asst-continue-premium", "href"), "/settings#subscription")
        self.assertGreaterEqual(page.locator(".asst-continue-premium").bounding_box()["height"], 44)

    def test_too_little_to_build_a_lesson_from_says_what_to_add(self):
        page = self.student()
        cid = self.conversation(page, "Was ist x?")
        self.open_thread(page, cid)
        page.wait_for_selector(".asst-continue-guidance")
        self.assertIn("Tell me which topic you are working on", page.text_content(".asst-continue-guidance"))
        self.assertEqual(page.locator(".asst-continue-button").count(), 0)

    def test_phones_get_16px_fields_one_visible_h1_and_a_readable_title(self):
        page = self.student(320, 568)
        cid = self.conversation(page, "Wie löse ich quadratische Gleichungen mit der Lösungsformel?")
        self.open_thread(page, cid)
        sizes = page.evaluate("""() => [getComputedStyle(document.querySelector('#asstPreset')).fontSize,
                                        getComputedStyle(document.querySelector('#asstSearch')).fontSize]""")
        self.assertEqual(sizes, ["16px", "16px"], "iOS zooms into a field under 16px")
        headings = page.evaluate("""() => [...document.querySelectorAll('h1')].filter((h) => {
            const r = h.getBoundingClientRect(); return r.width > 1 && r.height > 1 && getComputedStyle(h).visibility !== 'hidden';
        }).map((h) => h.textContent.trim())""")
        self.assertEqual(headings, ["Wie löse ich quadratische Gleichungen mit der Lösungsformel?"])
        self.assertGreaterEqual(page.locator("#asstThreadTitle").bounding_box()["width"], 95,
                                "the style picker leaves the title room at 320px")

    def test_the_first_suggestion_starts_at_the_gutter_on_a_phone(self):
        page = self.open(self.student(390, 844))
        page.evaluate("document.querySelector('.asst-suggestions').scrollLeft = 0")
        page.wait_for_timeout(200)
        self.assertGreaterEqual(page.locator(".asst-suggestion >> nth=0").bounding_box()["x"], 10)

    def test_typing_on_a_short_phone_gives_the_conversation_the_room(self):
        page = self.student(320, 568)
        cid = self.conversation(page)
        self.open_thread(page, cid)
        thread_before = page.locator("#asstThread").bounding_box()["height"]
        page.focus("#asstInput")
        page.evaluate("""() => {
            Object.defineProperty(window.visualViewport, 'height', {configurable: true, get: () => 308});
            window.visualViewport.dispatchEvent(new Event('resize'));
        }""")
        page.wait_for_timeout(250)
        self.assertTrue(page.evaluate("document.documentElement.classList.contains('asst-keyboard')"))
        self.assertTrue(page.is_hidden(".app-nav"))
        composer = page.locator("#asstComposer").bounding_box()
        self.assertLessEqual(composer["y"] + composer["height"], 309, "the box stays above the keyboard")
        thread = page.locator("#asstThread").bounding_box()
        self.assertGreaterEqual(thread["height"], 170, f"only {thread['height']}px of conversation left")
        page.evaluate("""() => {
            Object.defineProperty(window.visualViewport, 'height', {configurable: true, get: () => 568});
            window.visualViewport.dispatchEvent(new Event('resize'));
            document.querySelector('#asstInput').blur();
        }""")
        page.wait_for_timeout(250)
        self.assertFalse(page.evaluate("document.documentElement.classList.contains('asst-keyboard')"))
        self.assertTrue(page.is_visible(".app-nav"))
        self.assertAlmostEqual(page.locator("#asstThread").bounding_box()["height"], thread_before, delta=2)

    def test_wide_formulas_tables_and_code_can_be_scrolled_with_the_keyboard(self):
        page = self.student(320, 568)
        cid = self.conversation(page, "WIDE Zeig mir bitte eine sehr lange Rechnung mit Brüchen und einer Tabelle.")
        self.open_thread(page, cid)
        page.wait_for_selector(".asst-body table[tabindex='0']")
        marks = page.evaluate("""() => [...document.querySelectorAll('.asst-body [data-scroller]')].map((n) => ({
            tag: n.tagName, role: n.getAttribute('role'), label: n.getAttribute('aria-label'), tab: n.tabIndex,
            scrolls: n.scrollWidth > n.clientWidth}))""")
        self.assertTrue(marks)
        for mark in marks:
            self.assertTrue(mark["scrolls"])
            self.assertEqual(mark["tab"], 0)
            self.assertTrue(mark["label"])
        table = next(mark for mark in marks if mark["tag"] == "TABLE")
        self.assertIsNone(table["role"], "a table keeps its table role")
        self.assertEqual(table["label"], "Wide table, scrolls sideways")
        self.assertTrue(any(mark["role"] in ("region", "group") and "formula" in mark["label"] for mark in marks), marks)
        page.focus(".asst-body table[tabindex='0']")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(150)
        self.assertGreater(page.evaluate("document.querySelector('.asst-body table[tabindex]').scrollLeft"), 0)

    def test_a_formula_in_a_table_cell_is_never_broken_over_lines(self):
        page = self.student(390, 844)
        cid = self.conversation(page, "TABLEMATH Zeig mir die Gleichungen in einer Tabelle.")
        self.open_thread(page, cid)
        page.wait_for_selector(".asst-body td .katex")
        spaces = page.evaluate("() => [...document.querySelectorAll('.asst-body td .katex')].map((n) => getComputedStyle(n).whiteSpace)")
        self.assertTrue(spaces)
        self.assertEqual(set(spaces), {"nowrap"})
        lines = page.evaluate("() => [...document.querySelectorAll('.asst-body td .katex')].map((n) => n.getClientRects().length)")
        self.assertEqual(set(lines), {1})

    def test_the_photo_tray_has_phone_sized_controls_that_never_overlap(self):
        page = self.student(390, 844)
        self.upload(page)
        self.upload(page, colour=(30, 160, 90))
        self.open(page)
        page.wait_for_function("() => document.querySelectorAll('#asstAttachTray .attach-item.is-ready').length === 2")
        boxes = page.evaluate("""() => [...document.querySelectorAll('#asstAttachTray .attach-item')].map((item) => ({
            item: item.getBoundingClientRect().toJSON(),
            buttons: [...item.querySelectorAll('.attach-actions button')].map((b) => ({cls: b.className, ...b.getBoundingClientRect().toJSON()})),
            caption: parseFloat(getComputedStyle(item.querySelector('.attach-status')).fontSize)}))""")
        self.assertEqual(len(boxes), 2)
        for index, box in enumerate(boxes):
            self.assertGreaterEqual(box["caption"], 14)
            for button in box["buttons"]:
                self.assertGreaterEqual(min(button["width"], button["height"]), 44, button)
            if index + 1 < len(boxes):
                neighbour = boxes[index + 1]["item"]
                for button in box["buttons"]:
                    self.assertLessEqual(button["right"], neighbour["left"], f"{button['cls']} spills over the next photo")
        remove = page.evaluate("""() => { const b = document.querySelector('#asstAttachTray .attach-remove');
            return [getComputedStyle(b).color, getComputedStyle(b).backgroundImage]; }""")
        self.assertEqual(remove[0], "rgb(255, 255, 255)", "a white mark on a dark disc shows on any photo")
        self.assertIn("radial-gradient", remove[1])
        tray = page.locator("#asstAttachTray").bounding_box()
        self.assertLessEqual(tray["height"], 176, "the tray is bounded; the box keeps its place")


if __name__ == "__main__":
    unittest.main()

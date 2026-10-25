"""Render Learnova in a real browser at phone, tablet and desktop sizes and report layout faults.

    python scripts/mobile_audit.py                 # every page, every viewport, light + dark
    python scripts/mobile_audit.py --pages /about /dashboard --widths 320 390
    python scripts/mobile_audit.py --screenshots out/   # also save full-page PNGs

Runs the app in-process on a temporary SQLite database (never a real one), logs in a
test student, creates one lesson through the real /api/analyze route with the AI mocked,
then visits each page with Playwright using the installed Chrome or Edge (no browser
download). For every page and viewport it reports:

* horizontal page overflow (document wider than the viewport);
* visible elements that stick out of the viewport (clipped controls);
* header controls that overlap each other or leave the screen;
* whether the mobile menu button is visible and the menu opens, fits and closes;
* tap targets smaller than 44 px (buttons, links styled as buttons, inputs);
* body text below 14 px.

Exit status 1 when a blocking fault (overflow, clipping, broken menu) is found.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = [(320, 568), (375, 667), (390, 844), (430, 932), (768, 1024), (1024, 768), (1440, 900)]
GUEST_PAGES = ["/about", "/login", "/register"]
STUDENT_PAGES = [
    "/dashboard", "LESSON", "/assistant", "/settings", "/flashcards", "/flashcards/create",
    "/flashcards/import", "/progress", "/projects", "/practice/today", "/study-plans", "/study-plans/new",
    "/vocabulary", "/vocabulary/import", "/community", "/insights/mistakes", "/onboarding/grade",
]

LESSON = {
    "lesson_title": "Potenzgesetze: Produkte von Potenzen mit gleicher Basis",
    "detected_level": "Klasse 8",
    "concepts": [{"name": "Potenzgesetze", "evidence": "Ziel"}],
    "explanation": (
        "Methode „Potenzgesetze anwenden“:\n1. Identifiziere das Gesetz, das zur Situation passt "
        "(gleiche Basis, gleiche Exponenten oder negativer Exponent). Warum? Jedes Gesetz hat eine feste Regel.\n"
        "2. Schreibe die Potenzen nach dem jeweiligen Gesetz um: $a^m \\cdot a^n = a^{m+n}$.\n"
        "3. Führe die verbleibenden Grundrechenarten aus.\n4. Prüfe das Ergebnis.\n"
        "Sehr lange Wörter wie Donaudampfschifffahrtsgesellschaftskapitänsmützenabzeichen und "
        "https://www.bildungsplaene-bw.de/,Lde/LS/BP2016BW/ALLG/GYM/M/IK/7-8/01 dürfen das Layout nicht sprengen."
    ),
    "worked_example": {
        "problem": "Berechne den Wert von $4^2 \\cdot 4^3$ im Kontext eines Taschengeldplans: Ein Schüler spart 4 Euro pro Woche.",
        "steps": [
            "1. Das Problem enthält gleiche Basen (4) mit den Exponenten 2 und 3, also wende das Gesetz a^m·a^n = a^{m+n} an.",
            "2. Addiere die Exponenten: 2 + 3 = 5, also wird 4^2·4^3 zu 4^5.",
            "3. Berechne $$4^5 = 4 \\cdot 4 \\cdot 4 \\cdot 4 \\cdot 4 = 1024$$",
            "Schritt 4: Prüfe: $4^2 = 16$ und $4^3 = 64$, $16 \\cdot 64 = 1024$.",
        ],
        "answer": "$4^5 = 1024$",
    },
    "teacher_tips": ["Schreibe die Exponenten zuerst nebeneinander."],
    "exceptions": ["Bei verschiedenen Basen gilt das Gesetz nicht: $2^3 \\cdot 3^2 \\neq 6^5$."],
    "video_usefulness": 0, "videos": [], "image_usefulness": 0, "image_search_terms": [],
    "question": {
        "id": "q1", "concept": "Potenzgesetze", "difficulty": 1, "type": "multiple_choice",
        "prompt": "Was ist $2^3 \\cdot 2^2$?", "hint": "Exponenten addieren.",
        "options": [{"id": "a", "label": "$2^5$"}, {"id": "b", "label": "$2^6$"},
                    {"id": "c", "label": "$4^5$"}, {"id": "d", "label": "$2^1$"}],
        "expected_answer": "a",
    },
}

CHAT_REPLY = (
    "**Gut gemacht!** Vergleiche die Schritte:\n\n| Schritt | Rechnung |\n|---|---|\n"
    "| Exponenten addieren | $$2^{3} \\cdot 2^{2} = 2^{3+2} = 2^{5}$$ |\n| Ausrechnen | $$2^5 = 32$$ |\n\n"
    "Wie lautet $3^2 \\cdot 3^4$?\nCHIPS: 3^6 | 9^6 | 3^8"
)

AUDIT_JS = r"""
() => {
  const vw = document.documentElement.clientWidth;
  const out = {vw, scrollWidth: document.documentElement.scrollWidth, offscreen: [], smallTargets: [], smallText: 0, textSamples: []};
  const visible = el => {
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || Number(s.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const insideScroller = el => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const s = getComputedStyle(p);
      if (/(auto|scroll|hidden|clip)/.test(s.overflowX) && p.scrollWidth > p.clientWidth + 1) return true;
      if (s.position === 'fixed' && p.getAttribute('aria-hidden') === 'true') return true;
    }
    return false;
  };
  const describe = el => {
    let d = el.tagName.toLowerCase();
    if (el.id) d += '#' + el.id;
    if (el.classList.length) d += '.' + [...el.classList].slice(0, 3).join('.');
    return d;
  };
  for (const el of document.body.querySelectorAll('*')) {
    if (!visible(el) || el.closest('[aria-hidden="true"], .hidden, template, script, style, .skip-link, ins.adsbygoogle, .ai-mode-badge')) continue;
    const r = el.getBoundingClientRect();
    if ((r.right > vw + 1 || r.left < -1) && !insideScroller(el)) {
      out.offscreen.push({el: describe(el), left: Math.round(r.left), right: Math.round(r.right), text: (el.innerText || '').trim().slice(0, 40)});
    }
  }
  out.offscreen = out.offscreen.filter((item, i, all) => i < 25);
  const targets = document.querySelectorAll('button, [role=button], input:not([type=hidden]), select, textarea, a.primary-button, a.secondary-button, .app-nav a, .chat-suggestions button, .chat-controls button');
  for (const el of targets) {
    if (!visible(el) || el.closest('[aria-hidden="true"], .hidden')) continue;
    const r = el.getBoundingClientRect();
    if (r.height < 43.5 || r.width < 43.5) {
      out.smallTargets.push({el: describe(el), w: Math.round(r.width), h: Math.round(r.height), text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 30)});
    }
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.textContent.trim() || !node.parentElement) continue;
    const parent = node.parentElement;
    if (!visible(parent) || parent.closest('[aria-hidden="true"], .hidden, .katex, sup, sub')) continue;
    const size = parseFloat(getComputedStyle(parent).fontSize);
    if (size < 14) {
      out.smallText += 1;
      if (out.textSamples.length < 8) out.textSamples.push({el: describe(parent), size, text: node.textContent.trim().slice(0, 30)});
    }
  }
  const header = document.querySelector('.app-nav-inner');
  out.header = null;
  if (header) {
    const kids = [...header.children].filter(visible).map(el => ({el: describe(el), r: el.getBoundingClientRect()}));
    const overlaps = [];
    for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
      const a = kids[i].r, b = kids[j].r;
      if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) overlaps.push([kids[i].el, kids[j].el]);
    }
    out.header = {height: Math.round(header.getBoundingClientRect().height), overlaps,
                  outside: kids.filter(k => k.r.right > vw + 1 || k.r.left < -1).map(k => k.el)};
  }
  const lead = document.querySelector('main p, .prose, main li');
  out.bodyFont = lead ? parseFloat(getComputedStyle(lead).fontSize) : null;
  return out;
}
"""


def _prepare_environment() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="learnova-mobile-audit-"))
    os.environ["DATABASE_URL"] = f"sqlite:///{(scratch / 'audit.db').as_posix()}"
    os.environ["AI_USAGE_PATH"] = str(scratch / "usage.jsonl")
    os.environ["AI_CACHE_DIR"] = str(scratch / "cache")
    os.environ.setdefault("SECRET_KEY", "mobile-audit")
    os.environ.setdefault("GROQ_API_KEY", "audit-no-key")
    os.environ["LOCAL_OCR_WARMUP"] = "false"
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    return scratch


def _fake_response(**kwargs):
    class Reply:
        usage = None
        model = "audit"
        output_text = CHAT_REPLY if kwargs.get("task_type") == "tutor_chat" else json.dumps(LESSON)
    return Reply()


def start_server(port: int):
    import app as application
    from werkzeug.serving import make_server

    saved = {key: application.app.config.get(key) for key in ("WTF_CSRF_ENABLED", "RATELIMIT_ENABLED")}
    saved_limiter = application.limiter.enabled
    application.app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    application.limiter.enabled = False
    with application.app.app_context():
        application.db.drop_all()
        application.db.create_all()
    patcher = mock.patch.object(application, "create_response", side_effect=_fake_response)
    patcher.start()
    server = make_server("127.0.0.1", port, application.app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    original_shutdown = server.shutdown

    def shutdown():
        # Undo everything above, so a test process that imported the app keeps its config.
        original_shutdown()
        server.server_close()
        patcher.stop()
        application.app.config.update(saved)
        application.limiter.enabled = saved_limiter

    server.shutdown = shutdown
    return application, server


def launch(playwright):
    for channel in ("chrome", "msedge", None):
        try:
            return playwright.chromium.launch(channel=channel) if channel else playwright.chromium.launch()
        except Exception:  # try the next installed browser
            continue
    raise SystemExit("No Chromium-based browser found (Chrome, Edge or a Playwright download).")


def block_external(context, base: str) -> None:
    """Only the app and the KaTeX CDN load: ads and web fonts would make every run slow and
    the layout depend on a third party. Fonts fall back to the system stack, which is also
    what a student on a slow connection sees first."""

    def handle(route):
        url = route.request.url
        if url.startswith(base) or "cdn.jsdelivr.net/npm/katex" in url:
            route.continue_()
        else:
            route.abort()

    context.route("**/*", handle)


def login_and_create_lesson(context, base: str) -> str:
    page = context.new_page()
    page.goto(f"{base}/register")
    page.fill("input[name=username]", "mobileaudit")
    page.fill("input[name=email]", "mobile@example.com")
    page.fill("input[name=password]", "correct-horse-battery")
    if page.locator("select[name=grade]").count():
        page.select_option("select[name=grade]", "8")
    page.locator("form [type=submit]").first.click()
    page.wait_for_load_state("networkidle")
    page.request.post(f"{base}/settings/grade", form={"grade": "8"})
    response = page.request.post(f"{base}/api/analyze", multipart={"study_goal": "Potenzgesetze", "subject": "Mathematics"})
    session_id = response.json()["session_id"]
    page.request.post(f"{base}/api/tour/complete")
    page.close()
    return f"/?session_id={session_id}"


def audit_page(context, url: str, *, mobile_menu: bool, open_chat: bool, screenshot: Path | None,
               full_page: bool = True) -> dict:
    page = context.new_page()
    page.goto(url, wait_until="load")
    page.wait_for_timeout(400)
    chat_error = None
    if open_chat and page.locator("#chatToggle").count():
        # A real tap, so a control hidden under another element fails here as it would on a phone.
        try:
            page.click("#chatToggle", timeout=4000)
            page.fill("#chatInput", "Warum addiert man die Exponenten?", timeout=4000)
            page.click("#chatForm [type=submit]", timeout=4000)
            page.wait_for_selector(".tutor-message >> nth=1", timeout=5000)
            page.wait_for_timeout(400)
        except Exception as error:
            chat_error = str(error).splitlines()[0][:160]
            for line in str(error).splitlines():
                if "intercepts pointer events" in line:
                    chat_error += " | " + line.strip()[:200]
                    break
    result = page.evaluate(AUDIT_JS)
    result["chat_error"] = chat_error
    result["menu"] = None
    if mobile_menu:
        toggle = page.locator(".mobile-nav-toggle, .guest-nav-toggle").first
        menu_state = {"present": toggle.count() > 0, "visible": False}
        if toggle.count() and toggle.is_visible():
            box = toggle.bounding_box() or {}
            menu_state["visible"] = box.get("x", -1) >= 0 and box.get("x", 0) + box.get("width", 0) <= result["vw"] + 1
            menu_state["box"] = {key: round(value) for key, value in box.items()}
            try:
                toggle.click(timeout=4000)
            except Exception as error:
                reason = next((line.strip() for line in str(error).splitlines()
                               if "intercepts" in line or "outside of the viewport" in line or "not visible" in line), "")
                menu_state["click_error"] = (str(error).splitlines()[0][:120] + " | " + reason[:200])
                result["menu"] = menu_state
                page.close()
                return result
            page.wait_for_timeout(350)
            menu = page.locator("#" + (toggle.get_attribute("aria-controls") or "mobileAppMenu"))
            menu_box = menu.bounding_box() if menu.count() else None
            menu_state["expanded"] = toggle.get_attribute("aria-expanded")
            menu_state["opens"] = bool(menu_box) and menu.is_visible()
            if screenshot and menu_state["opens"]:
                page.screenshot(path=str(screenshot.with_name(screenshot.stem + "-menu.png")))
            menu_state["fits"] = bool(menu_box) and menu_box["x"] >= -1 and menu_box["x"] + menu_box["width"] <= result["vw"] + 1
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            menu_state["closes_on_escape"] = toggle.get_attribute("aria-expanded") == "false"
        result["menu"] = menu_state
    if screenshot:
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(screenshot), full_page=full_page)
    page.close()
    return result


def blocking(result: dict, width: int, *, logged_in: bool) -> list[str]:
    problems = []
    if result.get("chat_error"):
        problems.append(f"chat unusable: {result['chat_error']}")
    if result["scrollWidth"] > result["vw"] + 1:
        problems.append(f"page scrolls sideways ({result['scrollWidth']} > {result['vw']})")
    if result["offscreen"]:
        problems.append("clipped: " + ", ".join(f"{item['el']} [{item['left']}..{item['right']}]" for item in result["offscreen"][:4]))
    header = result.get("header") or {}
    if header.get("overlaps"):
        problems.append(f"header overlap: {header['overlaps'][:2]}")
    if header.get("outside"):
        problems.append(f"header control off-screen: {header['outside']}")
    menu = result.get("menu")
    # The menu is required where the bar hides links: logged-in up to 820px, guests up to
    # 700px. Pages without the app bar (login, register) have nothing to put in a menu.
    needs_menu = result.get("header") is not None and width <= (820 if logged_in else 700)
    if menu is not None and needs_menu:
        if not menu.get("visible"):
            problems.append("menu button missing or off-screen")
        elif not (menu.get("opens") and menu.get("fits") and menu.get("closes_on_escape")):
            problems.append(f"menu broken: {menu}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pages", nargs="*")
    parser.add_argument("--widths", nargs="*", type=int)
    parser.add_argument("--themes", nargs="*", default=["light", "dark"])
    parser.add_argument("--screenshots", type=Path)
    parser.add_argument("--fold", action="store_true", help="screenshots of the first screen only (plus the open menu)")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--port", type=int, default=5057)
    args = parser.parse_args()

    _prepare_environment()
    from playwright.sync_api import sync_playwright

    _, server = start_server(args.port)
    base = f"http://127.0.0.1:{args.port}"
    viewports = [vp for vp in VIEWPORTS if not args.widths or vp[0] in args.widths]
    report, failures = [], 0
    try:
        with sync_playwright() as playwright:
            browser = launch(playwright)
            student = browser.new_context()
            block_external(student, base)
            lesson_url = login_and_create_lesson(student, base)
            student_state = student.storage_state()
            student.close()
            plan = [(path, False) for path in GUEST_PAGES] + [(path, True) for path in STUDENT_PAGES]
            if args.pages:
                plan = [(path, logged) for path, logged in plan if path in args.pages or (path == "LESSON" and "LESSON" in args.pages)]
            for theme in args.themes:
                for width, height in viewports:
                    mobile = width <= 430
                    for logged_in in (False, True):
                        context = browser.new_context(
                            viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile,
                            device_scale_factor=2 if mobile else 1,
                            storage_state=student_state if logged_in else None)
                        context.add_init_script(f"try {{ localStorage.setItem('learnova:theme', '{theme}') }} catch (e) {{}}")
                        block_external(context, base)
                        for path, needs_login in plan:
                            if needs_login != logged_in:
                                continue
                            url = base + (lesson_url if path == "LESSON" else path)
                            name = "lesson" if path == "LESSON" else path.strip("/").replace("/", "_") or "root"
                            shot = (args.screenshots / f"{theme}-{width}x{height}-{name}.png") if args.screenshots else None
                            try:
                                result = audit_page(context, url, mobile_menu=width < 1024, open_chat=path == "LESSON", screenshot=shot,
                                                    full_page=not args.fold)
                            except Exception as error:  # keep auditing the other pages
                                result = {"error": str(error)[:200], "vw": width, "scrollWidth": 0, "offscreen": [], "smallTargets": [], "smallText": 0}
                            problems = blocking(result, width, logged_in=logged_in) if "error" not in result else [f"error: {result['error']}"]
                            failures += bool(problems)
                            report.append({"theme": theme, "viewport": f"{width}x{height}", "page": name, "problems": problems,
                                           "small_targets": len(result.get("smallTargets", [])), "small_text": result.get("smallText", 0),
                                           "body_font": result.get("bodyFont"), "details": result})
                            flag = "FAIL" if problems else "ok  "
                            print(f"{flag} {theme:5} {width}x{height} {name:22} targets<44:{len(result.get('smallTargets', [])):3} "
                                  f"text<14:{result.get('smallText', 0):3} {'; '.join(problems)[:300]}")
                        context.close()
            browser.close()
    finally:
        server.shutdown()
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{failures} page/viewport combinations with blocking faults out of {len(report)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

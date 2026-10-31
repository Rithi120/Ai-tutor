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
import re
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
VIEWPORTS = [(320, 568), (375, 667), (390, 844), (430, 932), (768, 1024), (1024, 768), (1440, 900)]
GUEST_PAGES = ["/about", "/login", "/register"]
STUDENT_PAGES = [
    "/dashboard", "LESSON", "/assistant", "/settings", "/flashcards", "/flashcards/create",
    "/flashcards/import", "/progress", "/projects", "/practice/today", "/study-plans", "/study-plans/new",
    "/vocabulary", "/vocabulary/import", "/community", "/insights/mistakes", "/onboarding/grade",
    "PROJECT_REVIEW", "PROJECT", "LESSON_WRITTEN", "EXAM",
    # Pages that need data first; created in login_and_create_lesson. A key whose data
    # could not be made is skipped, never audited as a 404.
    "PROJECT_START", "PROJECT_SECTION", "EXAM_SETUP", "EXAM_RESULTS",
    "STUDY_PLAN_CALENDAR", "STUDY_SESSION", "LESSON_SAVED", "CONCEPT",
    "FLASHCARD_SET", "FLASHCARD_STUDY", "FLASHCARD_LEARN", "FLASHCARD_TEST", "FLASHCARD_MATCH",
    "FLASHCARD_BLAST", "FLASHCARD_BLOCKS", "FLASHCARD_EDIT", "FLASHCARD_PUBLISH",
    "ADMIN_CODES", "INTERNAL_DIAGNOSTICS", "INTERNAL_MODERATION",
    "/profile", "/leaderboard", "PUBLIC_PROFILE",
]
# A photo is attached on these pages, so the photo tray and sent-photo thumbnails are
# measured too: (file input, send button or None, what to wait for after sending).
PHOTO_STEPS = {
    "LESSON": ("#chatGallery", None, None),
    "/assistant": ("#asstGallery", "#asstSend", ".asst-msg-assistant:not(.asst-pending)"),
}
PHOTO_PATH: Path | None = None
# --ignore-console REGEX: console errors matching it are reported but do not fail a page.
IGNORED_CONSOLE: "re.Pattern[str] | None" = None
# Pages that forward elsewhere on purpose (the vocabulary hub lives on the import page;
# onboarding is done once a grade is set, which the audit account has).
EXPECTED_REDIRECTS = {"/vocabulary": "/vocabulary/import", "/onboarding/grade": "/",
                      # One Projektmodus: the old planner entry points lead to the projects.
                      "/study-plans": "/projects", "/study-plans/new": "/projects"}
# Pages whose test is started first, so the question card (and its photo buttons) is measured.
START_TEST_PAGES = {"LESSON_WRITTEN"}
READING = {"images": [{"index": 1, "kind": "handwritten_notes"}],
           "blocks": [{"image": 1, "type": "formula", "text": "4^2 · 4^3 = [?4^5]", "latex": "4^2 \\cdot 4^3 = 4^5",
                       "author": "student", "confidence": 0.9}],
           "uncertain": [{"image": 1, "text": "4^5", "alternatives": ["4^6"], "affects_answer": True}],
           "clarification_question": "Steht im Exponenten 5 oder 6?", "summary": "Eine Potenzrechnung."}

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
  // Dark mode: text whose colour and painted background are both light is invisible.
  // The background is the nearest ancestor that actually paints one. Light surfaces
  // with dark text are only listed (a callout may be light on purpose).
  out.invisibleText = [];
  out.lightSurfaces = [];
  if (document.documentElement.dataset.theme === 'dark') {
    const rgb = value => {
      const m = String(value).match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const parts = m[1].split(/[ ,\/]+/).filter(Boolean).map(Number);
      return {lum: (0.2126 * parts[0] + 0.7152 * parts[1] + 0.0722 * parts[2]) / 255, alpha: parts.length > 3 ? parts[3] : 1};
    };
    const paintedBackground = el => {
      for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
        const colour = rgb(getComputedStyle(node).backgroundColor);
        if (colour && colour.alpha >= 0.6) return {colour, node};
        if (getComputedStyle(node).backgroundImage !== 'none') return null;  // gradients: not judged
      }
      return null;
    };
    for (const el of document.body.querySelectorAll('*')) {
      if (!visible(el) || el.closest('[aria-hidden="true"], .hidden, template, script, style, ins.adsbygoogle, .ai-mode-badge, .katex')) continue;
      const own = [...el.childNodes].some(node => node.nodeType === 3 && node.textContent.trim());
      const painted = paintedBackground(el);
      if (!painted || painted.colour.lum < 0.82) continue;
      const surface = describe(painted.node) + (painted.node.parentElement ? ' in ' + describe(painted.node.parentElement) : '');
      if (own) {
        const text = rgb(getComputedStyle(el).color);
        if (text && text.lum > 0.62) out.invisibleText.push(`${describe(el)} on ${surface}`);
      }
      if (painted.node === el && !['IMG', 'VIDEO', 'CANVAS', 'INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) {
        const r = el.getBoundingClientRect();
        if (r.width * r.height >= 900) out.lightSurfaces.push(surface);
      }
    }
    out.invisibleText = [...new Set(out.invisibleText)].slice(0, 12);
    out.lightSurfaces = [...new Set(out.lightSurfaces)].slice(0, 20);
  }
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


PROJECT_TOPICS = [
    "Gleichförmige Bewegung", "Geschwindigkeit und Durchschnittsgeschwindigkeit", "Beschleunigung",
    "Freier Fall", "Kraft und Gegenkraft", "Energieerhaltungssatzanwendungsaufgaben",
]


def _fake_response(**kwargs):
    import re

    task = kwargs.get("task_type")
    if task == "ocr_document_recognition":
        payload = {"blocks": [{"type": "handwriting", "content": "Gleichförmige Bewegung: v = s / t. " * 30,
                               "bbox": [0.05, 0.05, 0.95, 0.9], "confidence": 0.9, "crossed_out": False,
                               "important_candidate": False, "teacher_highlight_candidate": False, "nearby_text": ""}],
                   "detected_page_number": "1", "warning": ""}
    elif task == "project_section_generation":
        pages = [int(value) for value in re.findall(r'"page_id": (\d+)', str(kwargs.get("input", "")))] or [1]
        payload = {"sections": [{"title": title, "source_page_ids": pages, "learning_goals": [f"{title} erklären"],
                                 "standard_explanation": title, "estimated_minutes": 15,
                                 "difficulty": "hard" if index % 3 == 2 else "medium"}
                                for index, title in enumerate(PROJECT_TOPICS)]}
    elif task == "final_exam_generation":
        payload = _fake_exam(str(kwargs.get("input", "")))
    elif task == "final_exam_evaluation":
        payload = {"results": [{"question_id": question_id, "score": 70, "evaluation": "Teilweise richtig: die Einheit fehlt."}
                               for question_id in (kwargs.get("validation_context") or {}).get("question_ids", [])]}
    elif task in ("tutor_chat", "assistant_chat"):
        payload = CHAT_REPLY
    elif task == "image_understanding":
        payload = READING
    elif "Schriftlich" in json.dumps(kwargs.get("input", ""), ensure_ascii=False):
        # The same lesson with a written-answer question, so "Photo of my working" shows.
        payload = {**LESSON, "question": {**LESSON["question"], "type": "text", "options": [],
                                          "prompt": "Berechne $4^2 \\cdot 4^3$ und schreibe den Rechenweg auf.",
                                          "expected_answer": "1024"}}
    else:
        payload = LESSON

    class Usage:
        input_tokens = output_tokens = total_tokens = 0

    class Reply:
        usage = Usage()
        model = "audit"
        output_text = payload if isinstance(payload, str) else json.dumps(payload)
    return Reply()


def _data_pages(page, base: str, project_id: int) -> dict:
    """URLs of pages that need records: a flashcard set, a submitted exam, a study plan,
    a saved lesson and a concept. Looked up in the app's own database (same process)."""

    import app as application

    urls: dict[str, str] = {}
    cards = [{"type": "question_answer", "front": f"Begriff {number}: Gleichförmige Bewegung",
              "back": f"Erklärung {number}: Geschwindigkeit ist Weg durch Zeit, v = s / t."}
             for number in range(1, 9)]
    created = page.request.post(f"{base}/api/flashcards/sets", data=json.dumps({
        "title": "Physik – Bewegung", "subject": "Physics", "cards": cards}),
        headers={"Content-Type": "application/json"})
    if created.ok:
        set_id = created.json()["id"]
        for key, suffix in (("FLASHCARD_SET", ""), ("FLASHCARD_STUDY", "/study"), ("FLASHCARD_LEARN", "/learn"),
                            ("FLASHCARD_TEST", "/test"), ("FLASHCARD_MATCH", "/match"), ("FLASHCARD_BLAST", "/blast"),
                            ("FLASHCARD_BLOCKS", "/blocks"), ("FLASHCARD_EDIT", "/edit"),
                            ("FLASHCARD_PUBLISH", "/publish")):
            urls[key] = f"/flashcards/{set_id}{suffix}"
    # A second exam, submitted, for the results page; the first stays open for EXAM.
    second = page.request.post(f"{base}/projects/{project_id}/exam/new", max_redirects=0, form={
        "question_count": "5", "duration_minutes": "30", "difficulty": "mixed"})
    location = second.headers.get("location", "")
    if "/exams/" in location:
        exam_id = location.split("/exams/")[1].split("?")[0].split("/")[0]
        with application.app.app_context():
            questions = application.db.session.scalars(application.db.select(application.ExamQuestion).where(
                application.ExamQuestion.exam_id == int(exam_id))).all()
            form = {f"question_{question.id}": ("a" if question.question_type == "multiple_choice" else "v = s / t")
                    for question in questions}
        submitted = page.request.post(f"{base}/exams/{exam_id}/submit", form=form, max_redirects=0)
        if "/results" in submitted.headers.get("location", ""):
            urls["EXAM_RESULTS"] = f"/exams/{exam_id}/results"
    with application.app.app_context():
        session = application.db.session
        plan = session.scalar(application.db.select(application.StudyPlan).where(
            application.StudyPlan.project_id == project_id))
        if plan is not None:
            urls["STUDY_PLAN_CALENDAR"] = f"/study-plans/{plan.id}/calendar"
            if plan.sessions:
                urls["STUDY_SESSION"] = f"/study-plans/{plan.id}/sessions/{plan.sessions[0].id}"
        section = session.scalar(application.db.select(application.LearningSection).where(
            application.LearningSection.project_id == project_id))
        if section is not None:
            urls["PROJECT_SECTION"] = f"/projects/{project_id}/sections/{section.id}"
        lesson = session.scalar(application.db.select(application.Lesson).order_by(application.Lesson.id))
        if lesson is not None:
            urls["LESSON_SAVED"] = f"/lessons/{lesson.id}"
        user = session.scalar(application.db.select(application.User).where(application.User.username == "mobileaudit"))
        if user is not None:
            # A concept exists only once a question was answered; make one directly.
            concept = application.get_or_create_mastery(user.id, "Physics", "Gleichförmige Bewegung")
            session.commit()
            urls["CONCEPT"] = f"/concepts/{concept.id}"
            # Real rival accounts so the league table, Premium badge and a public profile
            # render with content: long names, a Premium member, and the audit account.
            application.award_xp(user.id, "audit", 60, "audit:xp:self")
            for index, name in enumerate(("rival.one", "a_very_long_username_29chars", "rival.three", "rival.four")):
                rival = application.User(username=name, email=f"{name}@example.com", avatar_id="explorer")
                rival.set_password("correct-horse-battery")
                session.add(rival)
                session.flush()
                application.award_xp(rival.id, "audit", 30 + 25 * index, f"audit:xp:{name}")
                if index == 1:
                    session.add(application.PremiumEntitlement(
                        user_id=rival.id, status="active",
                        expires_at=application.utcnow() + application.timedelta(days=30)))
            session.commit()
            urls["PUBLIC_PROFILE"] = "/u/a_very_long_username_29chars"
    urls["ADMIN_CODES"] = "/admin/premium-keys"
    urls["INTERNAL_DIAGNOSTICS"] = "/internal/ai-diagnostics"
    urls["INTERNAL_MODERATION"] = "/internal/moderation"
    return urls


def _fake_exam(prompt: str) -> dict:
    """A practice exam the app accepts: real section and page ids, an excerpt that is on the page."""

    start = prompt.index("Sections and sources: ") + len("Sections and sources: ")
    sections = json.loads(prompt[start:prompt.index("\nSettings:", start)])
    count = int(re.search(r"question_count=(\d+)", prompt).group(1))  # type: ignore[union-attr]
    questions = []
    for index in range(count):
        section = sections[index % len(sections)]
        choice = index % 2 == 0
        questions.append({
            "id": f"q{index + 1}", "section_id": section["section_id"], "concepts": [section["title"]],
            "source_page_ids": section["source_page_ids"][:1],
            "supporting_text": "Gleichförmige Bewegung: v = s / t.",
            "difficulty": ("easy", "medium", "hard")[index % 3],
            "question_type": "multiple_choice" if choice else "short_answer",
            "prompt": f"Frage {index + 1}: Wie lautet die Formel für die Geschwindigkeit bei einer gleichförmigen Bewegung?",
            "options": [{"id": "a", "label": "v = s / t"}, {"id": "b", "label": "v = s · t"},
                        {"id": "c", "label": "v = t / s"}] if choice else [],
            "expected_answer": "a" if choice else "v = s / t",
            "explanation": "Geschwindigkeit ist Weg durch Zeit.",
        })
    return {"questions": questions}


def _note_png() -> bytes:
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (900, 1200), "white").save(output, format="PNG")
    return output.getvalue()


def start_server(port: int):
    import app as application
    from werkzeug.serving import make_server

    saved = {key: application.app.config.get(key) for key in ("WTF_CSRF_ENABLED", "RATELIMIT_ENABLED")}
    saved_limiter = application.limiter.enabled
    application.app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    saved.update({key: application.app.config.get(key) for key in (
        "PREMIUM_ADMIN_IDENTIFIERS", "AI_DIAGNOSTICS_ADMINS", "COMMUNITY_MODERATORS")})
    application.app.config.update(
        PREMIUM_ADMIN_IDENTIFIERS={"mobileaudit"}, AI_DIAGNOSTICS_ADMINS={"mobileaudit"},
        COMMUNITY_MODERATORS={"mobileaudit"})
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


def login_and_create_lesson(context, base: str) -> dict:
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
    urls = {"LESSON": f"/?session_id={session_id}"}
    written = page.request.post(f"{base}/api/analyze", multipart={"study_goal": "Potenzgesetze Schriftlich",
                                                                  "subject": "Mathematics"})
    urls["LESSON_WRITTEN"] = f"/?session_id={written.json()['session_id']}"
    # Two learning projects: one waiting for its topics to be confirmed, one being learned.
    from datetime import date, timedelta
    learned_project = 0
    for key, exam in (("PROJECT_REVIEW", None), ("PROJECT", (date.today() + timedelta(days=6)).isoformat())):
        form = {"subject": "Physics", "title": "Physik – Mechanik und Energieerhaltung",
                "materials": {"name": "notes.png", "mimeType": "image/png", "buffer": _note_png()}}
        if exam:
            form["exam_date"] = exam
        created = page.request.post(f"{base}/projects", multipart=form, max_redirects=0)
        project_id = created.headers["location"].split("/projects/")[1].split("/")[0]
        page.request.post(f"{base}/projects/{project_id}/quick-start", headers={"Accept": "application/json"})
        if key == "PROJECT":
            page.request.post(f"{base}/projects/{project_id}/topics/confirm", form={"choice": "auto"})
            # A practice exam on the learned project, so the exam page is measured too.
            started = page.request.post(f"{base}/projects/{project_id}/exam/new", max_redirects=0, form={
                "question_count": "5", "duration_minutes": "30", "difficulty": "mixed"})
            location = started.headers.get("location", "")
            if "/exams/" in location:
                urls["EXAM"] = "/exams/" + location.split("/exams/")[1].split("?")[0]
            else:
                print(f"practice exam not created: HTTP {started.status} -> {location or 'no redirect'}")
            urls["PROJECT_START"] = f"/projects/{project_id}/start"
            urls["EXAM_SETUP"] = f"/projects/{project_id}/exam/new"
            learned_project = int(project_id)
        urls[key] = f"/projects/{project_id}"
    urls.update(_data_pages(page, base, learned_project))
    page.close()
    return urls


def attach_photo(page, step) -> None:
    """Put a photo in the tray (and send it, where the step says so)."""

    selector, send, settled = step
    if PHOTO_PATH is None or not page.locator(selector).count():
        return
    page.set_input_files(selector, str(PHOTO_PATH))
    page.wait_for_selector(".attach-item.is-ready", timeout=8000)
    if send:
        page.fill("#asstInput", "Ist mein Rechenweg richtig?", timeout=4000)
        page.click(send, timeout=4000)
        page.wait_for_selector(".sent-photos img", timeout=8000)
        if settled:
            page.wait_for_selector(settled, timeout=8000)
    page.wait_for_timeout(300)


def audit_page(context, url: str, *, mobile_menu: bool, open_chat: bool, screenshot: Path | None,
               full_page: bool = True, photo_step=None, start_test: bool = False) -> dict:
    page = context.new_page()
    # JavaScript errors are layout faults too: a script that throws leaves a control dead.
    # Load failures of what block_external aborts on purpose (ads, web fonts) are not.
    script_errors: list[str] = []
    page.on("pageerror", lambda error: script_errors.append(f"uncaught: {str(error)[:160]}"))
    page.on("console", lambda message: script_errors.append(f"console: {message.text[:160]}")
            if message.type == "error" and not message.text.startswith("Failed to load resource")
            and not (IGNORED_CONSOLE and IGNORED_CONSOLE.search(message.text)) else None)
    try:
        response = page.goto(url, wait_until="load")
    except Exception as error:
        # One retry: on a loaded machine the first navigation occasionally exceeds 30 s,
        # which says nothing about the page.
        if "Timeout" not in str(error):
            raise
        response = page.goto(url, wait_until="load", timeout=60000)
    # The page actually audited: an error status, or a redirect elsewhere, means the
    # numbers below describe some other page.
    landed = {"status": response.status if response else 0, "url": page.url}
    page.wait_for_timeout(400)
    chat_error = None
    if start_test and page.locator("#startTest").count():
        try:
            page.click("#startTest", timeout=4000)
            page.wait_for_selector("#writtenAnswer", timeout=5000)
            page.wait_for_timeout(300)
        except Exception as error:
            chat_error = "start test: " + str(error).splitlines()[0][:160]
    if photo_step and not open_chat:
        try:
            attach_photo(page, photo_step)
        except Exception as error:
            chat_error = "photo: " + str(error).splitlines()[0][:160]
    if open_chat and page.locator("#chatToggle").count():
        # A real tap, so a control hidden under another element fails here as it would on a phone.
        try:
            page.click("#chatToggle", timeout=4000)
            if photo_step:
                attach_photo(page, photo_step)
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
    result["script_errors"] = script_errors
    result["landed"] = landed
    result["requested"] = url
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
    landed = result.get("landed") or {}
    if landed.get("status", 200) >= 400:
        problems.append(f"HTTP {landed['status']}")
    elif result.get("requested") and landed.get("url"):
        requested = urlsplit(result["requested"]).path
        actual = urlsplit(landed["url"]).path
        if actual != requested and EXPECTED_REDIRECTS.get(requested) != actual:
            problems.append(f"redirected to {landed['url']}")
    if result.get("invisibleText"):
        problems.append("light text on a light surface (dark mode): " + "; ".join(result["invisibleText"][:4]))
    if result.get("script_errors"):
        problems.append("script errors: " + " | ".join(dict.fromkeys(result["script_errors"]))[:400])
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
    parser.add_argument("--ignore-console", help="regex of console errors to set aside for this run")
    parser.add_argument("--list-light-surfaces", action="store_true",
                        help="dark mode: also print light surfaces that carry dark text (for review)")
    args = parser.parse_args()
    global IGNORED_CONSOLE
    IGNORED_CONSOLE = re.compile(args.ignore_console) if args.ignore_console else None

    global PHOTO_PATH
    workspace = _prepare_environment()
    from learnova.evaluation.vision_fixtures import build_cases
    PHOTO_PATH = workspace / "photo.jpg"
    PHOTO_PATH.write_bytes(next(case.data for case in build_cases(include_large=False) if case.name == "math_handwritten"))
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
            special = login_and_create_lesson(student, base)
            missing = [path for path in STUDENT_PAGES if path.isupper() and path not in special]
            if missing:
                print(f"not audited (their data could not be created): {', '.join(missing)}")
            student_state = student.storage_state()
            student.close()
            plan = [(path, False) for path in GUEST_PAGES] + [(path, True) for path in STUDENT_PAGES]
            if args.pages:
                plan = [(path, logged) for path, logged in plan if path in args.pages]
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
                            if path.isupper() and path not in special:
                                continue  # its data could not be created; reported once below
                            url = base + special.get(path, path)
                            name = path.lower() if path in special else path.strip("/").replace("/", "_") or "root"
                            shot = (args.screenshots / f"{theme}-{width}x{height}-{name}.png") if args.screenshots else None
                            try:
                                result = audit_page(context, url, mobile_menu=width < 1024, open_chat=path == "LESSON", screenshot=shot,
                                                    full_page=not args.fold, photo_step=PHOTO_STEPS.get(path),
                                                    start_test=path in START_TEST_PAGES)
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
                            if args.list_light_surfaces and result.get("lightSurfaces"):
                                print(f"      light surfaces: {', '.join(result['lightSurfaces'])[:600]}")
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

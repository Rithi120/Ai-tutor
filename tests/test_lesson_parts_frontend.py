"""The lesson page draws teaching-engine lessons part by part, and wrong answers get a retry.

A Projektmodus lesson arrives with `lesson.parts` (objective, why, explanation,
Fachbegriffe, flashcards, guided exercise, mistakes, summary ... in the subject's order),
and `/api/answer` may answer a first wrong attempt with `retry` instead of the correction.
Node is not installed here, so - like tests/test_photo_exercises.py and
tests/test_voice_accessibility.py - these are string-level checks of static/app.js and the
stylesheets, plus the rendered lesson page. What they pin:

* every PART_KIND (and the server's "flashcards" part) has a label and a renderer;
* model text in a part only ever reaches the page through textContent;
* the old explanation / worked-example cards are hidden only when parts exist, so a lesson
  without parts renders exactly as before;
* the retry step shows what is missing, a reteach and a simpler example - never the
  correction - and "Try again" re-asks the same question as a counted second attempt;
* every translation key exists in FRONTEND_MESSAGES and its English is translated in
  de/fr/es with the same placeholders (LEARNOVA_PENDING_I18N may still name a pending
  i18n file; keys already merged from it are ignored).

Flipping, revealing, read-aloud, the retry round trip, 320-1440px and dark mode were
measured in Chrome with Playwright (tests/test_mobile_layout.py covers the page layout).
"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TEST_DATABASE = Path(tempfile.gettempdir()) / "learnova_lesson_parts_frontend_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{TEST_DATABASE.as_posix()}")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
os.environ.setdefault("GROQ_API_KEY", "test-key")

import app as application  # noqa: E402
from tests.config_isolation import keep_config  # noqa: E402
from learnova.lessons.strategies.base import PART_KINDS, SERVER_PART_KINDS  # noqa: E402
from learnova.translations import catalog  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
APP_JS = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
INDEX = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
STYLES = (ROOT / "static" / "styles.css").read_text(encoding="utf-8")
STUDY_CSS = (ROOT / "static" / "css" / "learnova-study.css").read_text(encoding="utf-8")
DARK_CSS = (ROOT / "static" / "css" / "learnova-dark.css").read_text(encoding="utf-8")
# Phase 2 only: new JS keys wait in this file until they are merged into FRONTEND_MESSAGES.
PENDING_I18N = os.environ.get("LEARNOVA_PENDING_I18N", "")


def between(source: str, start: str, end: str) -> str:
    assert source.count(start) == 1, f"{start!r} must appear exactly once"
    begin = source.index(start)
    return source[begin:source.index(end, begin)]


def function_body(name: str) -> str:
    """The source of `function name(...) {...}` in app.js, up to its closing column-0 brace."""

    pattern = re.compile(rf"^(?:async )?function {name}\(.*?^\}}$", re.M | re.S)
    matches = pattern.findall(APP_JS)
    assert len(matches) == 1, f"function {name} must be defined exactly once ({len(matches)})"
    return matches[0]


def object_entries(source: str, name: str) -> dict[str, str]:
    """`const NAME = { key: value, ... };` as a dict; every line must be one entry."""

    body = between(source, f"const {name} = {{", "\n};")[len(f"const {name} = {{"):]
    lines = [line.strip() for line in body.strip().splitlines() if line.strip()]
    entries = dict(re.findall(r'^(\w+): "?(\w+)"?,$', "\n".join(lines), re.M))
    assert len(entries) == len(lines), f"{name}: {len(lines)} lines but {len(entries)} parsed entries"
    return entries


PARTS_BLOCK = between(APP_JS, "/* lesson-parts:start", "/* lesson-parts:end */")
JS_KEYS = sorted(set(re.findall(r'\bt\("(\w+)"', APP_JS)) | set(object_entries(APP_JS, "PART_LABEL_KEYS").values()))


def pending_i18n() -> dict:
    if PENDING_I18N and Path(PENDING_I18N).is_file():
        return json.loads(Path(PENDING_I18N).read_text(encoding="utf-8"))
    return {"strings": {}, "js": {}}


class PartRendererTests(unittest.TestCase):
    def test_every_part_kind_has_a_translated_label(self):
        labels = object_entries(APP_JS, "PART_LABEL_KEYS")
        self.assertEqual(len(labels), len(PART_KINDS) + len(SERVER_PART_KINDS))
        self.assertEqual(set(labels), set(PART_KINDS) | set(SERVER_PART_KINDS))
        self.assertEqual(len(set(labels.values())), len(labels), "two kinds share one label key")

    def test_special_kinds_have_their_own_renderer_and_the_rest_fall_back(self):
        renderers = object_entries(APP_JS, "PART_RENDERERS")
        self.assertEqual(set(renderers), {"terms", "flashcards", "timeline", "experiment", "cause_effect",
                                          "guided_exercise", "mistakes", "summary"})
        self.assertTrue(set(renderers) <= set(PART_KINDS) | set(SERVER_PART_KINDS))
        for function in set(renderers.values()) | {"genericContent"}:
            self.assertEqual(PARTS_BLOCK.count(f"function {function}("), 1, function)
        self.assertIn("PART_RENDERERS[kind] || genericContent", PARTS_BLOCK)
        # An unknown kind is shown as an explanation, never dropped.
        self.assertIn('hasOwnProperty.call(PART_LABEL_KEYS, part.kind) ? part.kind : "explanation"', PARTS_BLOCK)

    def test_terms_and_flashcards_are_keyboard_accessible_flip_cards(self):
        cards = function_body("flipCardsContent")
        self.assertIn('el("button", "lp-flip")', cards)
        self.assertIn('card.type = "button"', cards)
        self.assertIn('setAttribute("aria-pressed", "false")', cards)
        self.assertIn('t("lpFromNotes") : t("lpAddedExplanation")', cards)
        self.assertIn("internalUrl(part.study_url)", cards)
        self.assertIn('t("lpStudyAllTerms")', cards)
        self.assertIn('getAttribute("aria-pressed") !== "true"', PARTS_BLOCK)

    def test_every_part_is_marked_with_its_origin_and_source_pages(self):
        card = function_body("partCard")
        self.assertIn("originBadge(part.from_notes === true)", card)
        self.assertIn("sourcePages(part)", card)
        self.assertIn('t("lpFromNotes") : t("lpAddedByLearnova")', function_body("originBadge"))
        self.assertIn('t("lpPage", { page })', function_body("sourcePages"))

    def test_guided_exercise_is_a_self_check_whose_solution_is_not_spoken_early(self):
        guided = function_body("guidedExerciseContent")
        self.assertIn('t("showHint"), t("lpHideHint")', guided)
        self.assertIn('t("lpShowSolution"), t("lpHideSolution")', guided)
        panel = function_body("revealPanel")
        self.assertIn("panel.hidden = true", panel)
        # Read-aloud skips aria-hidden nodes; a closed solution must carry it.
        self.assertIn('panel.setAttribute("aria-hidden", "true")', panel)
        self.assertIn('setAttribute("aria-expanded", "false")', panel)
        self.assertIn('panel.removeAttribute("aria-hidden")', PARTS_BLOCK)
        # The hidden attribute must win over the panel's own display rule.
        self.assertIn(".lp-reveal-panel[hidden] { display: none; }", STUDY_CSS)

    def test_bodies_keep_indentation_and_code_blocks(self):
        body = function_body("bodyContent")
        self.assertIn("```", body)
        self.assertIn('el("pre", "lp-code")', body)
        self.assertRegex(STUDY_CSS, r"\.lp-text \{[^}]*white-space: pre-wrap")
        self.assertRegex(STUDY_CSS, r"\.lp-code \{[^}]*white-space: pre;[^}]*\}")
        self.assertRegex(STUDY_CSS, r"\.lp-code \{[^}]*overflow-x: auto")

    def test_model_text_never_becomes_markup(self):
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "DOMParser",
                     "createContextualFragment", "srcdoc"):
            self.assertFalse(sink in PARTS_BLOCK, f"{sink} in the parts renderer")
        self.assertIn("node.textContent = text", function_body("el"))
        self.assertIn("document.createTextNode(piece)", function_body("appendRichText"))
        # Only a same-site path becomes a link, and it never opens a new tab.
        url = function_body("internalUrl")
        self.assertIn('!url.startsWith("/") || url.startsWith("//") || url.includes("\\\\")', url)
        # Review fix: the URL parser drops tabs/newlines, so "/<tab>/evil.example" resolved to
        # http://evil.example/ (reproduced in Chrome). Whitespace and control characters are
        # rejected and the resolved origin must be this site's.
        self.assertIn(r"/[\s\u0000-\u001f\u007f]/.test(url)", url)
        self.assertIn("new URL(url, window.location.origin).origin === window.location.origin", url)
        for new_tab in ("_blank", 'setAttribute("target"', ".target ="):
            self.assertFalse(new_tab in PARTS_BLOCK, f"{new_tab} in the parts renderer")

    def test_old_cards_are_hidden_only_when_parts_exist(self):
        lesson = function_body("renderLesson")
        self.assertIn("const partCount = renderParts(lesson);", lesson)
        self.assertIn("const hasParts = partCount > 0;", lesson)
        self.assertIn('document.querySelector(".explanation-card")?.classList.toggle("hidden", hasParts);', lesson)
        self.assertIn('document.querySelector(".example-card")?.classList.toggle("hidden", hasParts || !worked ||', lesson)
        self.assertIn('document.querySelector(".teacher-notes-card")?.classList.toggle("hidden", hasParts && !tips.length);',
                      lesson)
        self.assertIn("if (hasParts) numberCardsAfterParts(partCount);", lesson)
        self.assertIn("if (!Array.isArray(parts)) return [];", function_body("usableParts"))
        self.assertIn('container.classList.toggle("hidden", parts.length === 0);', function_body("renderParts"))
        # The explanation text is still set, so nothing that reads it loses it.
        self.assertIn('document.querySelector("#explanation").textContent = lesson.explanation;', lesson)

    def test_read_aloud_gets_a_space_between_built_elements(self):
        # Review fix: read-aloud speaks textContent, and DOM-built siblings have no text between
        # them - a term card was spoken "PflichtNotwendigkeit ...", a timeline row "1724Geburt"
        # (reproduced in Chrome). renderParts separates sibling elements once the cards exist.
        render = function_body("renderParts")
        self.assertLess(render.index("list.replaceChildren("), render.index("spaceForSpeech(list);"))
        spacing = function_body("spaceForSpeech")
        self.assertIn('node.parentElement?.closest(".lp-text, pre")', spacing)
        self.assertIn("node.nextSibling?.nodeType === Node.ELEMENT_NODE", spacing)
        self.assertIn('node.after(document.createTextNode(" "))', spacing)

    def test_styles_cover_phone_widths_dark_mode_and_reduced_motion(self):
        for selector in (".lp-flip", ".lp-flip-back", ".lp-timeline", ".lp-steps", ".lp-cause-row", ".lp-reveal",
                         ".lp-warnings", ".lp-recap", ".lp-study-link", ".lp-origin-notes", ".lp-origin-added", ".lp-sr"):
            self.assertIn(selector, STUDY_CSS)
        self.assertRegex(STUDY_CSS, r"\.lp-reveal \{[^}]*min-height: var\(--ln-tap\)")
        self.assertRegex(STUDY_CSS, r"\.lp-study-link \{[^}]*min-height: var\(--ln-tap\)")
        self.assertRegex(STUDY_CSS, r"minmax\(min\(100%, 220px\), 1fr\)")
        self.assertRegex(STUDY_CSS, r"@media \(max-width: 560px\) \{\s*\.lp-cause-row \{ grid-template-columns: minmax\(0, 1fr\)")
        self.assertRegex(STUDY_CSS, r"@media \(prefers-reduced-motion: reduce\) \{\s*\.lp-flip-inner \{ transition: none; \}")
        self.assertIn("backface-visibility: hidden", STUDY_CSS)
        for kind in ("guided_exercise", "mistakes", "summary"):
            self.assertIn(f':root[data-theme="dark"] .lp-card.lp-kind-{kind} {{ border-top-color:', DARK_CSS)
        self.assertIn(':root[data-theme="dark"] .question-card.result-retry', DARK_CSS)
        # The classic cards' #4d5871 reading text was ~2.4:1 on the dark card.
        self.assertIn(':root[data-theme="dark"] :is(.prose, .example-card ol, .teacher-notes-grid ul) '
                      '{ color: var(--ln-muted); }', DARK_CSS)

    def test_answer_option_styles_do_not_reach_into_formulas(self):
        # ".answer-option span" also styled every span KaTeX builds, so "$2^5$" was a
        # 300px stack of padded boxes on a phone.
        self.assertNotRegex(STYLES, r"\.answer-option span\s*[{:]")
        self.assertIn(".answer-option > span {", STYLES)
        self.assertIn(".answer-option > span:before {", STYLES)
        self.assertNotRegex(DARK_CSS, r"\.answer-option span\s*[{:]")


class RetryTests(unittest.TestCase):
    def test_a_retry_is_shown_before_the_answer_counts_as_a_result(self):
        handler = between(APP_JS, 'answerForm.addEventListener("submit"', "const chatPanel")
        retry = handler.index("if (data.retry?.allowed && !data.complete) {")
        self.assertLess(retry, handler.index("questionResults.push(correct);"))
        self.assertLess(retry, handler.index("data.evaluation.correction"))
        self.assertIn("showRetry(data, data.retry);\n      return;", handler)

    def test_the_retry_panel_teaches_and_never_shows_the_correction(self):
        panel = function_body("showRetry")
        for needle in ('t("retryMissing"), retry.misunderstanding', 't("retryReteach"), retry.reteach',
                       't("retrySimplerExample"), retry.simpler_example', "diagnosisMarkup(data.diagnosis)",
                       't("lessonTryAgain")', "escapeHtml(partText(text))", "escapeHtml(said)"):
            self.assertIn(needle, panel)
        for leak in ("correction", "teacher_tip", "exception_note", "expected_answer", "questionResults.push"):
            self.assertNotIn(leak, panel)
        self.assertIn("updateProgress(questionResults.length + 1, latestKnowledge)", panel)

    def test_try_again_reasks_the_same_question_as_a_counted_second_attempt(self):
        panel = function_body("showRetry")
        self.assertIn("renderQuestion(retry.question && typeof retry.question === \"object\" ? retry.question : "
                      "currentQuestion, { retry: true })", panel)
        question = function_body("renderQuestion")
        self.assertIn("function renderQuestion(question, { retry = false } = {})", question)
        branch = re.search(r"if \(retry\) \{(.*?)\} else \{(.*?)\}", question, re.S)
        self.assertIsNotNone(branch)
        assert branch is not None
        self.assertIn("answerRetryCount += 1;", branch.group(1))
        self.assertNotIn("answerRetryCount = 0", branch.group(1))
        self.assertIn("answerRetryCount = 0;", branch.group(2))
        self.assertIn("hintUsed = false;", branch.group(2))
        # Controls come back: renderQuestion rebuilds the answer control and the submit button.
        self.assertIn('answerForm.querySelector(".primary-button").classList.remove("hidden");', question)
        self.assertIn("retry_count: answerRetryCount", APP_JS)

    def test_retry_styles_exist(self):
        for selector in (".question-card.result-retry {", ".feedback.retry {", ".retry-block {", ".retry-prompt {",
                         ".feedback .retry-button {"):
            self.assertIn(selector, STYLES)

    def test_try_again_keeps_the_typed_answer_and_the_reteach_in_view(self):
        # ui_i18n-1: on a phone a long written answer was wiped and the explanation the
        # student was told to read disappeared the moment they tapped "Try again".
        panel = function_body("showRetry")
        capture, click = panel.index("const previous = collectAnswer();"), panel.index('.retry-button").addEventListener')
        self.assertLess(capture, click, "the answer is read before the controls are rebuilt")
        handler = panel[click:]
        self.assertLess(handler.index("renderQuestion("), handler.index("restoreAnswer(previous)"))
        self.assertIn('feedback.querySelector(".retry-button")?.remove()', handler)
        restore = function_body("restoreAnswer")
        for needle in ('#writtenAnswer', 'input[name="answerOption"]', "#dropdownAnswer", "#orderingList"):
            self.assertIn(needle, restore)
        question = function_body("renderQuestion")
        hide = 'document.querySelector("#feedback").className = "feedback hidden";'
        self.assertIn(f"if (!retry) {hide}", question, "the reteach stays visible during the second try")


class WorkspaceTapTargetTests(unittest.TestCase):
    def test_the_workspace_section_chips_and_topic_chips_are_44px_tap_targets(self):
        # ui_i18n-3
        css = (ROOT / "static" / "css" / "project-workspace.css").read_text(encoding="utf-8")
        for selector in (".pm-nav a {", "a.pm-chip {"):
            with self.subTest(selector=selector):
                rule = css[css.index(selector):css.index("}", css.index(selector))]
                height = re.search(r"min-height:\s*([^;]+);", rule)
                assert height is not None, rule
                value = height.group(1).strip()
                self.assertTrue(value in ("44px", "var(--ln-tap, 44px)") or value.endswith(", 44px)"), value)


class TranslationKeyTests(unittest.TestCase):
    def test_every_key_used_by_the_lesson_page_is_defined(self):
        pending = pending_i18n()["js"]
        missing = [key for key in JS_KEYS if key not in catalog.FRONTEND_MESSAGES and key not in pending]
        self.assertEqual(missing, [], "add these keys to FRONTEND_MESSAGES (or the pending LESSONFE i18n file)")

    def test_every_key_is_translated_and_keeps_its_placeholders(self):
        # The browser shows FRONTEND_MESSAGES[key] translated through the catalogues, so a
        # key whose English has no entry would show English to a German student.
        for key in JS_KEYS:
            english = catalog.FRONTEND_MESSAGES.get(key)
            if english is None:
                continue        # still pending: checked by the tests around this one
            for language in ("de", "fr", "es"):
                translated = catalog.CATALOGS[language].get(english, "")
                self.assertTrue(translated, f"{key} ({english!r}) has no {language} translation")
                self.assertEqual(sorted(re.findall(r"\{\w+\}", translated)), sorted(re.findall(r"\{\w+\}", english)),
                                 f"{key} {language}: placeholders differ")

    def test_pending_keys_are_used_translated_and_keep_their_placeholders(self):
        # Only keys not merged yet are checked: once $S/i18n_merge.py has merged the
        # pending file, pointing LEARNOVA_PENDING_I18N at it is harmless.
        data = pending_i18n()
        unmerged = {key: english for key, english in data["js"].items() if key not in catalog.FRONTEND_MESSAGES}
        if not unmerged:
            self.skipTest("no unmerged pending i18n keys (merged into FRONTEND_MESSAGES)")
        self.assertEqual(sorted(key for key in unmerged if key not in JS_KEYS), [], "pending keys nobody uses")
        for key, english in unmerged.items():
            for language in ("de", "fr", "es"):
                translated = str(data["strings"].get(english, {}).get(language)
                                 or catalog.CATALOGS[language].get(english) or "")
                self.assertTrue(translated, f"{key} ({english!r}) has no {language} translation")
                self.assertEqual(sorted(re.findall(r"\{\w+\}", translated)), sorted(re.findall(r"\{\w+\}", english)),
                                 f"{key} {language}: placeholders differ")
        for english, languages in data["strings"].items():
            self.assertIn(catalog.CATALOGS["de"].get(english), (None, languages.get("de")),
                          f"{english!r} already has a different German translation; reuse it")


class LessonPageTests(unittest.TestCase):
    PARTS = [
        {"kind": "objective", "title": "", "body": "Du kannst erklären, was Pflicht bei Kant bedeutet.", "items": [],
         "answer": "", "from_notes": True, "source_pages": [1]},
        {"kind": "explanation", "title": "Pflicht </script><script>alert(1)</script>",
         "body": "Handeln aus Achtung vor dem Gesetz.", "items": [], "answer": "", "from_notes": True,
         "source_pages": [2]},
        {"kind": "terms", "title": "Fachbegriffe", "body": "", "answer": "", "from_notes": True, "source_pages": [2],
         "items": [{"label": "Maxime", "text": "subjektiver Grundsatz des Wollens"}]},
        {"kind": "guided_exercise", "title": "Probier es", "body": "Handelt der Kaufmann aus Pflicht?",
         "items": [{"label": "Tipp", "text": "Warum ist er ehrlich?"}], "answer": "Nein, aus Eigennutz.",
         "from_notes": False, "source_pages": [2]},
        {"kind": "summary", "title": "Das Wichtigste", "body": "Pflicht ist Handeln aus Achtung.", "items": [],
         "answer": "", "from_notes": True, "source_pages": [1, 2]},
    ]

    def setUp(self):
        from learnova.ai_services import service

        # Put back what this setUp changes, so it cannot leak into later modules.
        keep_config(self, application.app.config, "AI_MODE")
        application.app.config.update(TESTING=True, AI_MODE="cached")
        application.SESSIONS.clear()
        service._RECENT_RESULTS.clear()
        service._RESERVATIONS.clear()
        with application.app.app_context():
            application.db.drop_all()
            application.db.create_all()
        self.client = application.app.test_client()
        self.client.post("/register", data={"username": "parts_student", "email": "parts@example.com",
                                            "password": "correct-horse-battery", "language": "en"},
                         follow_redirects=True)

    def lesson(self, **extra):
        return {"lesson_title": "Pflicht und Neigung", "detected_level": "Klasse 9",
                "concepts": [{"name": "Pflicht"}], "explanation": "Pflicht ist Handeln aus Achtung vor dem Gesetz.",
                "teacher_tips": [], "exceptions": [],
                "question": {"id": "q1", "concept": "Pflicht", "difficulty": 1, "type": "multiple_choice",
                             "prompt": "Was ist Pflicht?", "hint": "Achtung.",
                             "options": [{"id": "a", "label": "Handeln aus Achtung"}, {"id": "b", "label": "Neigung"}],
                             "expected_answer": "a"}, **extra}

    def start(self, lesson, goal):
        def route(*, task_type, **_kwargs):
            if task_type != "lesson_generation":
                raise AssertionError(f"unexpected AI task type: {task_type}")

            class Reply:
                usage = None
                model = "test-model"
                output_text = json.dumps(lesson)
            return Reply()

        with mock.patch.object(application, "create_response", route):
            response = self.client.post("/api/analyze", data={"study_goal": goal, "subject": "Ethics"})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return self.client.get(f"/?session_id={response.get_json()['session_id']}").get_data(as_text=True)

    @staticmethod
    def bootstrap(html):
        found = re.search(r'<script nonce="[^"]*" id="lessonBootstrap" type="application/json">(.*?)</script>', html, re.S)
        assert found, "no lesson bootstrap on the page"
        return json.loads(found.group(1))

    def test_the_parts_container_sits_between_the_header_and_card_01_with_its_own_listen_control(self):
        html = self.client.get("/lesson").get_data(as_text=True)   # the lesson builder (/ is the chat)
        self.assertIn('<div id="lessonParts" class="lesson-parts hidden">', html)
        self.assertIn('id="partsListenControl"', html)
        self.assertIn('data-speech-target="#lessonPartsList"', html)
        self.assertIn('<div id="lessonPartsList" class="lesson-parts-list"></div>', html)
        self.assertLess(html.index('class="lesson-top"'), html.index('id="lessonParts"'))
        self.assertLess(html.index('id="lessonParts"'), html.index('class="content-card explanation-card"'))
        self.assertIn("listen_control('#lessonPartsList', 'partsListenControl')", INDEX)

    def test_a_lesson_with_parts_reaches_the_page_and_model_text_cannot_close_the_script(self):
        html = self.start(self.lesson(parts=self.PARTS), "Kant Pflicht parts page test")
        lesson = self.bootstrap(html)["lesson"]
        kinds = [part.get("kind") for part in lesson.get("parts", [])]
        sent = iter(kinds)
        self.assertTrue(all(kind in sent for kind in [part["kind"] for part in self.PARTS]), kinds)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("Pflicht </script><script>alert(1)</script>",
                      [part.get("title") for part in lesson["parts"]])

    def test_a_lesson_without_parts_still_has_the_classic_cards(self):
        html = self.start(self.lesson(), "Kant Pflicht classic page test")
        lesson = self.bootstrap(html)["lesson"]
        self.assertFalse(lesson.get("parts"))
        for card in ('class="content-card explanation-card"', 'class="content-card example-card"',
                     'class="content-card teacher-notes-card"'):
            self.assertIn(card, html)


class FixTwoFrontendTests(unittest.TestCase):
    """File-named page badges, per-card origin, the lesson notice and the retry counter (FIX2)."""

    def test_page_badges_use_the_servers_file_labels_and_fall_back_to_numbers(self):
        body = function_body("sourcePages")
        self.assertIn("part.source_labels", body)
        self.assertIn('if (labels.length) return [...new Set(labels)].map(label => el("span", "lp-page", label));',
                      body, "the labels win when present, as text, never markup")
        self.assertIn('t("lpPage", { page })', body, "older lessons keep their page numbers")
        self.assertRegex(STUDY_CSS, r"\.lp-page \{[^}]*overflow-wrap: anywhere", "a long file name wraps at 320px")

    def test_a_card_with_its_own_origin_shows_it(self):
        self.assertIn('typeof item.from_notes === "boolean" ? { from_notes: item.from_notes } : {}',
                      function_body("partItems"))
        flip = function_body("flipCardsContent")
        self.assertIn('const itemFromNotes = typeof item.from_notes === "boolean" ? item.from_notes : fromNotes;',
                      flip)
        self.assertIn('itemFromNotes ? t("lpFromNotes") : t("lpAddedExplanation")', flip)

    def test_the_lesson_notice_is_shown_as_text_above_the_parts(self):
        self.assertIn('<p id="lessonNotice" class="lesson-notice hidden" role="note"></p>', INDEX)
        self.assertLess(INDEX.index('id="lessonNotice"'), INDEX.index('id="lessonParts"'))
        render = function_body("renderLesson")
        self.assertIn("notice.textContent = partText(lesson.notice);", render)
        self.assertIn('notice.classList.toggle("hidden", !notice.textContent);', render)
        self.assertIn(".lesson-notice {", STUDY_CSS)

    def test_part_pictures_are_same_site_images_with_text_alt(self):
        figures = function_body("partFigures")
        self.assertIn("internalUrl(item && item.url)", figures, "only a same-site path becomes an image")
        self.assertIn('image.alt = item.labels || t("photoFromYourPage");', figures)
        self.assertNotIn("innerHTML", figures)
        self.assertIn("const figures = partFigures(part);", function_body("partCard"))
        self.assertRegex(STUDY_CSS, r"\.lp-figure img \{[^}]*max-width: 100%")

    def test_a_failed_request_does_not_count_as_a_second_try(self):
        handler = between(APP_JS, 'answerForm.addEventListener("submit"', 'const chatPanel')
        failure = handler[handler.index("} catch (error) {"):handler.index("} finally {")]
        self.assertNotIn("answerRetryCount", failure)


if __name__ == "__main__":
    unittest.main()

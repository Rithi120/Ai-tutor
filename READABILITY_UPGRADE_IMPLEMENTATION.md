# Readability and engagement upgrade (2026-11-14)

Nothing was committed, pushed or deployed. There is no database change, no new dependency and
no API change: every change is in the renderer, templates and CSS.

The specification arrived cut off at section 12 ("Preserve existing API"). Everything up to that
point was read. Section 12 was followed in the form it could be read: no route, payload or
stored data changed.

## 1. What changed

### 1.1 Chat answers: structured, with long answers folded (the biggest change)

**Problem.** The model already writes its answers in labelled parts ("**Antwort**",
"**Beispiel**", "### Formel", "**Check‑Frage**"). The renderer showed them as one long column
of paragraphs separated by rules. On a phone the answer, the example and the question to the
student looked the same.

**Change.** A new module, `static/js/markdown-sections.js`, is called once at the end of the
existing renderer (`static/js/markdown.js`, `renderBlocks`).
- **What counts as a label:** a heading, or a paragraph that starts with a bold label, whose
  text is one of the known labels in German, English, French or Spanish.
  - Each such label starts a section `<section class="md-section md-kind-…">` with a small icon:
    🧠 answer/explanation · 💡 example, tip · ⚡ formula · 🧮 calculation/steps · ✅ result ·
    📌 remember/summary · ⚠️ mistakes/warning · 📝 exercise · ❓ check question · 🎓 exam ·
    🎯 why it matters.
  - An unknown heading inside a structured answer becomes a plain section (•).
- **Only structured answers change.** An answer with fewer than two recognised labels is
  returned exactly as before. Most short replies are such answers.
- **Long answers** (more than 1,800 characters with four or more sections):
  - the first two sections stay open;
  - later sections fold into `<details>` the student can open with one tap (44 px);
  - **never folded:** the answer, the result, mistakes, exercises, the check question and exam
    tips.
- **The `---` rules disappear** inside a structured answer; the cards separate the parts.
- **Safety:** the module only regroups the renderer's own escaped HTML. Labels are matched on
  their text, and the icons and class names are fixed strings, so nothing in a reply can become
  markup that it could not become before. A test checks this.
- **Maths:** KaTeX still typesets inside the sections (checked in Chrome: 8 of 8 formulas).
- **Where it applies:** the same renderer is used for the chat (`/`) and the rich lesson
  history, so both get it.
- **Styles** (`static/css/learnova-content.css`, loaded on every page):
  - a light card with a coloured left edge per kind;
  - tokens only, so dark mode follows the existing token switch with no extra override;
  - a focus ring on the fold toggles; the chevron animates only when reduced motion is off.

### 1.2 Project and Short lessons: an icon per part

`static/app.js` (`PART_ICONS`, `partCard`) shows a small icon in front of every lesson part's
title:
- 🎯 objective / why · 🧠 explanation / theory · ⚡ rule · 💡 example / worked example;
- ⚠️ mistakes · 📝 guided exercise · ✅ summary · 📚 terms;
- and one each for the subject-specific kinds (🧪 experiment, 🕰️ timeline, 📜 source analysis …).

The icon is `aria-hidden`, so the title is read exactly as before. It uses a logical margin, so
Arabic (RTL) is correct. Style: `.lp-title-icon` in `learnova-study.css`.

### 1.3 Settings: groups and a jump row

`templates/settings.html` has the same settings as before, now grouped:
- **📚 Learning preferences:** interface language, grade, school type. These were three forms
  without a heading.
- **👤 Profile:** username, avatar, privacy, leagues (`_profile_settings.html`). Unchanged
  apart from the icon.
- **⭐ Subscription:** including "Redeem a Premium code" (`_subscription.html`). Unchanged apart
  from the icon.
- **ℹ️ Help and support.**

A row of pill links at the top jumps to each group (44 px, wraps on a phone, `scroll-margin` so
the sticky header does not cover the heading).

**Not invented:** the specification lists 🎨 Appearance, 🔔 Notifications and 🔒 Privacy groups.
- Learnova has no notification settings.
- The theme switch is in the header.
- Privacy lives inside Profile.

Adding empty groups would have meant new features, which was ruled out, so these were not
added.

**Strings:** "Learning preferences" and "Settings sections", added in DE/FR/ES (counted: each
catalogue went from 3387 to 3389).

### 1.4 Dashboard: icons on the card headings

`templates/dashboard.html`: a decorative icon in front of the nine fixed card titles:
- 🎯 Today's Practice · 📚 Your study projects · 📊 Subjects · ⚠️ Weakest concepts;
- 🔁 Concepts due today · 📈 Recent mastery improvements · 🎓 Continue learning;
- 📝 Mistake Notebook · 🏆 Recent exam performance.

Style: `.heading-icon`. Headings whose text is data (the resume title, counts) got no icon.

**Emoji rule kept:** icons sit only in front of headings and section titles, never inside
sentences, and always `aria-hidden`.

## 2. Areas not redesigned (honest scope)

These keep their current look. They already use the shared cards and tokens:
- the Project Mode overview cards;
- flashcard and vocabulary study cards;
- exam prep;
- the profile and leaderboard.

The requested "summary first" for long chat answers is done by keeping the answer section open
and folding the rest. No summary is generated. Writing one would need another model call or a
prompt change, and both were out of scope.

## 3. Tests

**New: `tests/js/markdown-sections.test.mjs`, 5 tests.** Run in Chrome by
`tests/test_js_browser.py::NodeStyleTests::test_the_answer_sections_suite`.
- labels in four languages, including gpt-oss's non-breaking hyphen ("Check‑Frage");
- a sentence or a longer word is not a label;
- a heading or a bold first line starts a section; bold inside a sentence does not;
- a structured answer → 3 sections with icons, the introduction first, no `<hr>`;
- **a plain answer is byte-for-byte unchanged**; one label alone changes nothing;
- a long answer folds later sections but **never** the answer, mistakes or check question;
  folded text is still present;
- labels and content stay escaped (`<img onerror>` and `<script>` stay text).

**One updated assertion: `tests/test_subscriptions.py::test_the_section_is_translated`.** It
looked for `>Abo<` and now looks for the German heading by its id, including the icon. This is
an intended change.

**Existing suites that pass with the change:**
- all JS renderer suites, including the renderer security suite;
- the chat and assistant suites, including browser tests;
- maths rendering;
- lesson parts frontend;
- lesson reliability, tutorial, i18n, subscriptions, premium vouchers;
- the dashboard, show-more and redesign suites.

Run numbers: 429 passed, 3 skipped; 120 passed, 1 skipped; 237 passed after the one update; 59
passed.

**Full regression suite (2026-11-14, after the audit fixes, Reliability V1, vouchers, community filters and readability together):** 3754 passed, 11 skipped, 0 failed (52 min). `instance/` was unchanged.

**Real Chrome checks (screenshots):**
- the chat at 390 px light and dark and at 1440 px, with a realistic German answer;
  - sections, icons and accent colours shown;
  - KaTeX inside the sections;
  - no sideways scroll;
  - the folded section opens on tap;
- Settings at 360, 390 dark and 1440 px:
  - the four jump links at 44 px, no sideways scroll;
  - the jump lands on the Subscription heading (109–371 px from the top).

**One observation.** On the very first visit to Settings, the interactive tutorial's chapter
starts and scrolls to its own target. A jump tapped at that moment is overridden. Later visits
jump correctly. This is existing tutorial behaviour and was not changed.

## 4. Files

- **New:**
  - `static/js/markdown-sections.js`
  - `tests/js/markdown-sections.test.mjs`
- **Changed:**
  - `static/js/markdown.js` (one import, one call)
  - `static/app.js` (`PART_ICONS`, title icon)
  - `static/css/learnova-content.css`, `learnova-study.css`, `profile.css`
  - `templates/settings.html`, `components/_profile_settings.html`,
    `components/_subscription.html`, `dashboard.html`
  - `learnova/translations/catalog.py`, `data/fr.json`, `data/es.json`
  - `tests/test_js_browser.py`, `tests/test_subscriptions.py`

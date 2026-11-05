# Tutorial and onboarding

Learnova's tutorial is a guided tour of the real interface. Each step dims the page, lights
the actual button or panel (never a screenshot or a fake element), says in one to three
short sentences what it does and when to use it, and offers **Previous**, **Next**
(**Finish** on the last step) and **Exit quick tour** or **Skip** (a feature chapter).
In German, French and Spanish the hub keeps the navigation's name for *Tutorial*
(*Rundgang* / *Visite guidée* / *Recorrido*) and the quick tour has its own (*Kurzer
Überblick* / *Aperçu rapide* / *Vistazo rápido*).

Since 2026-11-04 it has two layers, so a student never gets everything at once:

1. **The quick tour** (`overview`, up to 12 steps): one step per function the student can
   use, lit at its real entry point in the navigation - Chat, Overview, New Lesson and
   Project Mode in the desktop bar; Flashcards, Today's Practice (+ Mistake Intelligence)
   and Community under More; My profile, Leagues and Settings in the Account menu; on a
   phone all of them in the menu; the XP chip in the header. Its last step lights the
   Tutorial link (in More / the Account menu / the phone menu) and says that short tips appear
   when a feature is opened for the first time, and that the Tutorial replays them.
2. **Feature chapters** (27 chapters of 1-6 steps): a few tips that belong to **one page**.
   A chapter **starts by itself** the first time the student uses that feature, and the
   hub replays it at any time.

There is one tutorial system; the old nine sections (150 steps, 37 of which showed the
generic "This appears once you have used it for the first time." to a new student) were
replaced. That sentence no longer exists in the payload or in `tour.js` (its msgid stays
in the catalogue, unused). The registry has **98 steps** in all; `tests/test_tutorial.py`
keeps the total between 70 and 100.

## What the student sees

* **First-time welcome card.** A new account (`User.tour_completed_at` is empty) gets a
  card on the first page it opens after registering: *Show me around (1 minute)* (the
  quick tour) or *Explore on my own*. Any choice - and Escape, which means Explore -
  settles it for good on every device (`POST /api/tour/complete`); so does starting
  anything from the hub. It is a card, never a redirect gate, and it never appears on
  `/onboarding/grade` or `/tutorial`. Nobody has to finish anything to use Learnova.
* **Chapters that start by themselves.** On a page with chapters, the first chapter (in
  registry order) whose *anchor* has been visible and on screen for 0.9 s starts, with
  its own first card: "*Chapter* · Step 1 of N" and **Skip**. Never while the first-time
  offer is open or right after it on the same page load, never over a page dialog or an
  open menu, never while Learnova is writing (`#loading`, the chat's Stop button, any
  `[aria-busy="true"]`) and never within 2 s of the student typing. It starts at its first
  step that is really on the page (never a "still loading" card first). **Follow-ups on the
  same page load:** after a run or the offer ended, another chapter may start, but only one
  whose anchor was *not* on screen when that run ended - it appeared through the student's
  own action (a first answer, a lesson built in place, Start test, the chat list opened, a
  section scrolled to) - and not within 3 s (`FOLLOW_GAP_MS`): never two back to back.
  Starting by itself never takes the focus from what the student is doing; the card is
  announced and Tab reaches it.
* **Doing what a tip says.** Using the lit link or submit button of a step that leaves the
  page ("Tap Start learning") ends the chapter: completed on its last step, skipped
  otherwise (it never starts by itself again), and the next page's own chapter may start.
  The quick tour goes on on the next page. A chapter run carried over to another page goes
  on only where its anchor exists (it is given 2 s to be drawn); elsewhere it is left, still
  in progress, and does not block that page's chapters. Sections ended in a tab are also
  remembered there (`sessionStorage learnova.tutorial.ended`, account-tagged), so a page drawn
  before the server heard of it never restarts them.
* **Skip / Exit / Escape** mark the section **skipped**: it never starts by itself again.
  **Finish** marks it **completed**. A student who leaves a chapter's page half-way leaves
  it *in progress*; it may start again (at that step) the next time.
* **Show tips automatically** (on by default): a switch on the hub and in Settings → *Help
  and support*. Off, no chapter starts by itself; the hub still replays everything.
* **A step whose element is not there** (a Premium-only control, a list without rows, a
  button that only appears later) is skipped silently inside a running chapter - forward,
  or backward after Previous, never in a loop - and the counter leaves it out. A chapter
  in which nothing could be shown ends quietly (and, if it had started by itself, counts as
  skipped so it cannot start again and again).
* **The hub, `/tutorial`** (`templates/tutorial.html`, `static/css/tutorial.css`): welcome,
  the overall progress in percent, *Reset all tips* (after a confirmation: every chapter and
  the quick tour, then the quick tour starts), the
  quick tour's card (*Start* / *Resume* / *Replay*, *Restart*, *Mark as done*), the
  *Show tips automatically* switch (it saves as soon as it changes; its Save button is the
  fallback without JavaScript), then one collapsible block (`<details>`, open while something
  in it is left to do, with "Done: n of N") per feature group (Chat Assistant, Short
  Lessons, Project Mode, Learning and Practice, Profile and Rewards, Community, Premium and
  Account) with its chapters: status *Not seen* / *In progress · step n of N* / *Skipped* /
  *Done*, "Tips: N", and
  * **Start / Resume / Replay** - a real link to the chapter's page (the student's own
    record for `@token` chapters); tour.js stores the run first, so it starts on arrival;
  * or, when the student has **no record for it yet** (no chat answer, no lesson, no
    project, ...), a specific line - "Starts after your first question in the chat.",
    "Starts when you open a project whose topics are confirmed." - and **Take me there** to
    the page where that record is made. The line is shown only when the record is really
    absent (the context tokens below), never a dead-end card;
  * **Restart** (once seen: reset, then replay) and **Mark as done** (until done).
  Then "What Learnova can do" (only features that are switched on) and Still stuck?
* **Entry points** (each `a[data-tutorial-link][href="/tutorial"]`): the More menu, the
  Account menu, the phone menu, the Overview ("New here? Open the tutorial"), Settings →
  *Help and support* (first link) and the footer (signed in only).

### Chapters

| id | page | anchor (starts once on screen) | steps | needs (else "Starts when ...") |
|---|---|---|---|---|
| `overview` | `*` | offered / hub | 12 | – |
| `chat-basics` | chat | `#asstInput` | 4 | – |
| `chat-answers` | chat | the newest tutor answer | 4 | `chat_thread` |
| `chat-history` | chat | a second row in the chat list (phone: the drawer opened) | 3 | `chat_history` |
| `lesson-start` | index | the subject picker | 4 | – |
| `lesson-inside` | index | the lesson (`lesson-explanation` / `#lessonParts`) | 4 | `lesson` |
| `lesson-tutor` | index | the tutor chat open | 2 | `lesson` |
| `lesson-test` | index | Start test or `#questionCard` | 4 | `lesson` |
| `projects-start` | projects | `#projectCreateForm` | 5 | – |
| `project-draft` | project-draft | the topic check | 3 | `project_draft` |
| `project-workspace` | project | Next / `#overview` | 4 | `project` |
| `project-progress` | project | `#progress` (scrolled to) | 4 | `project` |
| `project-review` | project-review | the first page | 1 | `project_review` |
| `exam-results` | exam-results | Close the gaps | 1 | `exam_result` |
| `study-session` | study-session | Mark session complete | 1 | `study_session` |
| `flashcards-library` | flashcards | the toolbar | 4 | – |
| `flashcard-set` | flashcard-set | `#ovStudy` | 4 | `flashcard_set` |
| `flashcard-study` | flashcard-mode | `#modeConfig` | 3 | `flashcard_study` |
| `todays-practice` | practice-today | the plan | 1 | – |
| `mistake-insights` | mistakes | the header | 1 | – |
| `dashboard` | dashboard | the command center | 3 | – |
| `progress` | progress | the hero | 2 | – |
| `my-profile` | profile | the hero | 6 | – |
| `leagues` | leaderboard | the ladder | 2 | – |
| `community-library` | community | `#commFilters` | 3 | – |
| `community-set` | community | `#detailStudy` (a set opened in place) | 3 | `community_set` |
| `settings` | settings | the language form | 6 | – |
| `settings-plan` | settings | the plan status (scrolled to) | 4 | – |

Step counts are the registry's; a student sees fewer (flags, plan, layout). Granular steps
were merged into general ones (Copy / Regenerate / Retry = one *Answer tools* step; rename /
archive / delete / restore = one step on the ⋯ menu; the five study modes = one step on the
mode list, shown only when a mode besides Flashcards is on (`flashcard_modes`); upload + scan; the free plan's status + usage).

Steps that open something on the page end their chapter with the student's own tap
(`lessons-tutor` opens the tutor chat, `chat-menu` the ⋯ menu): the opened panel is never
under the dimmed page. Nothing that spends an allowance, pays, publishes, deletes or logs
out is ever pressed by the tutorial (Create learning project, Publish, Activate Premium,
the practice exam are read steps; *Build my lesson* is the student's own tap).

Not shown, because not built or not live (inventory of 2026-11-03): competency extraction
(only the optional Kompetenzraster upload), typed notes in projects, profile-photo uploads
(avatars only), notification preferences, a theme control inside Settings (the tip points at
the header switch), resuming a paused flashcard session, pin/edit in the chat, starring a
whole set, admin/moderator/diagnostics pages. No tip (yet): the lesson's media card, the
concept page, Share and Delete of a set, the test summary, other learners' public profiles,
About and Contact (both are linked from the hub).

## Architecture

```
learnova/tutorial/registry.py     SECTIONS (quick tour + chapters), GROUPS, PAGES, PAGE_BY_ENDPOINT, CONTAINERS, UI/OFFER msgids, CAN_DO
learnova/tutorial/visibility.py   capabilities() -> caps; payload(caps, language, translate, facts) -> JSON; signature();
                                  chapters_for_page(), visible_groups()
learnova/tutorial/progress.py     pure rules: validate, merge_status (never less done), section state, resume step, percent
app.py                            TutorialProgress model (migration 042), tutorial_caps/facts/payload/sig,
                                  tutorial_auto_chapters (#tourRoot[data-tutorial-auto]), tutorial_tips_on,
                                  context URLs, /api/tutorial/*, /tutorial (tutorial_hub), POST /tutorial/tips
templates/components/_tour.html   #tourRoot shell (+ #tourData JSON while the offer is open, and on the hub)
templates/components/_tutorial_tips.html   the "Show tips automatically" form (hub and Settings)
static/js/tour-rules.js           pure engine decisions (no DOM), tests/js/tour-rules.test.mjs
static/js/tour.js                 DOM engine: find, light, open containers, start chapters by themselves, trap focus, persist
static/css/tour.css               mask, ring, card, the tips switch; dark values in learnova-dark.css
```

The **Python registry is the single source of truth.** The server filters it by what the
student can use, translates every text, fills config facts and sends JSON; `tour.js` is
generic and contains no step text and no feature gating.

## Registry schema

```python
Step(id, page, title, text, kind="read", targets=(), also=(), advance="next", viewport="any",
     requires=(), wait_ms=1200, busy="", create=(), shortcut=(), icon="")
Section(id, title, icon, steps, kind="chapter", page="*", anchor="", group="", url="", where="",
        when="", summary="", requires=())
Group(id, title, icon, requires=())
```

* Section ids are kebab-case, ≤ 40 characters, stored in the database, and never one of
  `OLD_SECTION_IDS` (the retired sections: their rows are kept and ignored). Ids starting
  with `_` are reserved rows (`_auto_tips`). Step ids are unique kebab-case, ≤ 60.
* A **chapter** has a `page` (all its steps live there), an `anchor` selector, a `group`
  (hub) and a `url` for Replay: `"/path[#anchor]"` or `"@token"` (the student's own record).
  With an `@token` url it also has `when` ("Starts when you ...", English msgid) and
  `where` (the page where that record is made, for *Take me there*).
* `kind`: `card` (no target, centred), `read` (lit, Next), `action` (lit, and the student's
  own action also moves on: `advance` = `click` | `change` | `input` with ≥ 3 characters).
* `targets`: CSS selectors in priority order; the first selector with a **visible** match
  wins, and within it the first visible node. Prefer `[data-tour-target="…"]`, then a unique
  `#id`. One hook may sit on several elements (the desktop menu link and the phone-menu
  link): the visible one is lit. `also`: lit together with the target.
* `viewport`: `any`, `phone` (≤ 820 px) or `desktop`.
* `requires`: capabilities; all must be present or the step does not exist for the student.
  `"!name"` means the capability must be **absent** (`profile-avatars-pick`).
* `wait_ms`: how long a target that is not there yet is awaited before it is skipped
  silently (a target that matches nothing at all on the page: 0.5 s). `busy`: while this
  selector is visible the card says "Learnova is still writing it".
* Texts are English msgids, 1–3 sentences, titles 1–4 words (5 for a question), ≤ 260
  characters, button names exactly as the UI shows them; plural-safe placeholders only:
  `{price}`, `{league_up}`, `{league_down}`, `{league_size}`, `{username_days}`, `{pages}`,
  `{languages}` (`app.tutorial_facts()`).
* No target, URL or anchor may touch `/admin`, `/internal`, moderation, premium keys, contact
  messages or AI diagnostics (tested).

### Page keys

`#tourRoot[data-tour-page]` = the view's `tour_page` (`"chat"` on `/`, `/assistant`,
`/assistant/c/<id>`; `"index"` on `/lesson`, `/?session_id=`, `/?goal=`; `"tutorial"` on the
hub; `"project"` or `"project-draft"` on a project, by whether its topics are confirmed), else
`PAGE_BY_ENDPOINT[request.endpoint]`, else `other`. The community list and a set's page share
the key `community` (community.js opens a set without a page load): the set chapter's anchor
is the set's own Study button.

### Containers

`CONTAINERS` lists where a target can hide: the phone menu, the desktop More and Account
menus, the chat drawer and its ⋯ menu, the archived chats, the lesson's tutor chat and any
closed `<details>`. A hidden target lights its container's **opener** first ("Open More
first."); the student taps it, or presses Next and the engine taps it. A container opened
for a step is closed again when the next step is outside it. `navigation.js` and
`assistant.js` keep a menu open while the tutorial card has the focus or is clicked
(`closest("#tourRoot")`).

## Capabilities and gating

`app.tutorial_caps()` (once per request): every truthy key of `feature_flag_values()`, plus
`flashcard_import` (PDF or image import), `chat_images`, `continue_learning`, `leagues`
(`leagues_enabled()`), `leagues_opted_in` (leagues on and `User.leaderboard_enabled`), and
`premium` or `free` (`has_active_premium`). **Role never adds anything**: an admin,
moderator or diagnostics account gets exactly the student payload and hub.

A section is visible when its `requires` are met and at least one step is visible; a hub
group when one of its chapters is. Plan differences are separate step variants
(`projects-create-free` / `-premium`, `account-status-free` / `-premium`, the Premium badge
for Premium only). With production flags (render.yaml) the imports and the vocabulary
trainer are off, so `practice-import` and `practice-vocabulary` do not exist; the study-mode
step lights the mode list whatever it holds.

## Context tokens

Used by the hub (`app.tutorial_context_urls`, owner-only, newest first, one `LIMIT 1` query
each, `null` when absent, nothing ever created) to decide between Replay and "Starts when
you ...", and returned by `GET /api/tutorial/progress`.

| token | value |
|---|---|
| `chat_thread` | `/assistant/c/<id>`: own, not archived, ≥ 2 messages (assistant on) |
| `chat_history` | `/assistant/c/<id>`: the newest of at least two own chats that are not archived |
| `lesson` | `/lessons/<id>/resume`: own lesson with a study session |
| `project` | `/projects/<id>`: own project with confirmed topics |
| `project_draft` | `/projects/<id>`: own project not confirmed yet, ≥ 1 topic |
| `project_review` | `/projects/<id>/review`: own project with ≥ 1 page |
| `exam_result` | `/exams/<id>/results`: own newest handed-in practice exam |
| `study_session` | `/study-plans/<plan>/sessions/<id>`: the next planned day of an active plan |
| `flashcard_set` / `flashcard_study` | `/flashcards/<id>` / `…/study`: own set with ≥ 1 card |
| `community_set` | `/community/sets/<id>`: the newest set any reader may see (`visible_set_filters()`) |

## Payload and cache

`GET /api/tutorial/steps` (and the inline `#tourData`) =
`{v, sig, language, hub, ui, sections, steps, containers}`. A section is
`{id, kind, title, icon, page, anchor, group, steps}`; optional step fields are left out
when they have their default. `ui` = previous, next, finish, exit, skip, stepOf, busy, lit
and the offer (title, text, question, quick, explore). `sig` hashes the registry
version, language, static version, hub, facts and the visible step ids;
`#tourRoot[data-tutorial-sig]` carries it and `tour.js` caches the payload per tab under it
(`sessionStorage learnova.tutorial.payload`), so a flag or language change fetches a fresh
one. Bump `REGISTRY_VERSION` when steps change (`"6"` since the review fixes of 2026-11-05).

`#tourRoot[data-tutorial-auto]` (`app.tutorial_auto_chapters`, one small query, only on a
page with chapters) lists the chapters of this page that may still start by themselves -
`id` or `id:step` to resume one in progress - and is empty while the offer is open, on the
hub and the grade prompt, when the switch is off, and for chapters skipped or done.

## Progress API and storage

Table `tutorial_progress` (migration `042_tutorial_progress`; **no new migration**):
`user_id` (FK, cascade), `section`, `step` (the step **id** reached), `status`
(`in_progress` | `skipped` | `completed`; `String(12)`; no row = not seen), `started_at`,
`updated_at`, `completed_at` (first completion, kept), unique `(user_id, section)`.
A write never makes a section less done (`progress.merge_status`): `in_progress` never
overwrites `skipped` or `completed`, and `skipped` never overwrites `completed`; the step
still moves on. Only *Restart* (`POST /api/tutorial/reset`) clears a status.
`status: "completed"` is accepted only with the section's last visible step.

**Show tips automatically** is a reserved row of the same table (`section = "_auto_tips"`,
`status = "off"`): present = off, absent = on. It is not a section (refused by the progress
API), not counted, and *Reset all tips* keeps it. Rows of retired section ids are kept and
ignored.

| route | limit (per account) | body | answer |
|---|---|---|---|
| `GET /api/tutorial/steps` | 60/min | – | payload |
| `GET /api/tutorial/progress` | 60/min | – | `{sections: {id: {status, step, current, total, completed_at}}, percent, auto_tips, offer_settled, context}` |
| `POST /api/tutorial/progress` | 120/min | `{section, step, status}` | `{ok, section, percent}` |
| `POST /api/tutorial/reset` | 20/min | `{section: id \| "all"}` | `{ok, percent}` |
| `POST /api/tour/complete` | 30/min | `{}` | `{ok}`: the first-time card is settled |
| `POST /tutorial/tips` | 30/min | form `auto_tips=on`, `back=hub\|settings` | redirect (`/tutorial#tips` or `/settings#help`) |
| `GET /tutorial` | – | – | the hub (read-only) |

All require login (401 JSON on `/api`), CSRF (`X-CSRFToken` through the global fetch wrapper;
the tips form carries its own `csrf_token`), and only ever touch `current_user`'s rows.
Errors: `bad_request`, `unknown_section` (not in the registry or not visible to this
student), `unknown_step`, `bad_status`. `percent` = the mean over visible sections of 1
(completed), the steps before the stored step / all steps (in progress), 0 (skipped, not seen).
A run's position also lives in `sessionStorage learnova.tutorial.run`
(`RUN_VERSION` 5: `{v, section, step, auto, u}`), so a reload continues it; a chapter run
found on another page than its own is dropped (it stays in progress); restarting never
touches `tour_completed_at`.

**One tab, two students (a shared school device).** `#tourRoot[data-tutorial-user]` carries
a per-account tag (an HMAC of the account id under `SECRET_KEY`, 16 hex characters). The
stored run and the offer choice (`dismissed:<tag>`) carry it, and a value another account
left is ignored. Submitting the logout form also clears the tab's tutorial keys.

## Accessibility

The card is `role="dialog"` labelled by its title; `#tourAnnounce` (polite, `.visually-hidden`
- defined globally in `learnova-components.css` and again inside `.tour-root` in `tour.css`)
says the section, "Step n of N", the title (one full stop, never "!.") and "Highlighted:
<name>". The name is the lit element's `aria-label`/`aria-labelledby`, its own text only for
a button, link, label, legend or heading, a field's label, else the first heading inside it.
The lit element gets `aria-describedby="tourText"`. While a run is visible, Tab and
Shift+Tab cycle through the card and the lit element. Escape closes an open menu or drawer
first and skips the tutorial second; a page dialog pauses the tour until it closes. Focus
returns to where the run started; when the student's own action removed the focused
element (Start test hides itself), the card title takes the focus; a chapter that starts by
itself leaves the focus where it is. The title's focus ring shows only after keyboard input
in this tab. Every button is at least 44 px high (the tips switch's whole label is the tap
target); reduced motion turns off the animations and smooth scrolling. The hub's buttons
name their section through `aria-describedby`, and its progress bar is a
`role="progressbar"`. The tutorial needs JavaScript (the hub says so in a `<noscript>`
note; the tips switch is a plain form and works without it).

## Layout of the light and the card

* A target taller than the free band of the window (`tour-rules.js lightBand`) is scrolled
  to its top (below the header) and lit from there to the end of the band (`fitRect`): the
  ring keeps four visible edges, something stays dimmed and the card never covers the lit
  part. Every mask edge is rounded once and shared, so no undimmed seam appears.
* With no free side the floating card takes the corner that covers the least of the lit
  element. The counter ("Step n of N") never breaks inside itself on a phone.
* Dark theme: filled `.primary-button`s use `#5a49ef` (hover `#4d3de0`) with white text,
  5.8:1 and more.

## Adding a step or a chapter

1. Add a `Step` to its chapter in `learnova/tutorial/registry.py` (English msgids, the rules
   above), or a `Section(...)` to `SECTIONS` with `page`, `anchor`, `group`, `url` (+ `when`
   and `where` for an `@token` url). Keep the total between 70 and 100. Never make the
   student spend an allowance, pay, publish or delete: such steps are `read` steps; a step
   whose target opens a panel ends the chapter with the student's own tap.
2. Give the real element a stable hook: `data-tour-target="<name>"` appended at the **end**
   of its start tag (tests pin start-tag prefixes), on every layout's copy. Elements drawn
   by a script get `node.dataset.tourTarget = "<name>"`.
3. Translate every new msgid (German "du", French "tu", Spanish "tú") through the merge
   script with a pending file (`--check` first).
4. Add the step to `EXPECTED` in `tests/test_tutorial_browser.py` (and the chapter to
   `WHERE`, its records to `OWNS`), and its hook page to `HOOK_PAGES` in
   `tests/test_tutorial.py` if the page is new. Bump `REGISTRY_VERSION`.
5. Run `tests/test_tutorial.py` and the browser walk of the chapter.

## Testing

Drive C: is nearly full on the development machine: point `TEMP`/`TMP` (and
`DATABASE_URL=sqlite:///…`, `AI_USAGE_PATH`, `AI_CACHE_DIR`) at a folder on R: for every run.

* `python -m pytest tests/test_tutorial.py tests/test_tour.py` — registry integrity (the
  70–100 total, chapters on one page, no retired ids), visibility (development and
  production flags, free and Premium, admin = student), the API (validation, never less
  done, ownership, CSRF, limits, cascade, old rows ignored), the auto-start list per page,
  the tips switch, the hub (states, "Starts when you ..." for a new student, Replay with
  records, percent, buttons, German), entry points, hooks on their rendered pages,
  translations of every msgid, and that the old sentence reaches nobody in any language.
* `R:/ai_tutor/.venv/Scripts/python -m pytest tests/test_tutorial_browser.py` (Playwright,
  system Chrome) — the quick tour at 375 × 812 light and 1280 × 900 dark, every chapter at
  375 light and seven at 1280 dark, each lit element checked against the test's own
  selectors; chapters starting by themselves (once, never after Skip/Finish, not with the
  switch off, not during the offer, a dialog, a generation or typing; a follow-up only for an
  anchor that appeared later; using the lit link saves the chapter and the next page starts
  its own; a first answer after the offer and a lesson built in place get their tips);
  silent skips without loops; the hub's buttons; keyboard trap, Escape order, dialog pause,
  focus; the review fixes; the first-time card.
* `tests/test_js_browser.py` runs `tests/js/tour-rules.test.mjs` (no Node needed);
  `tests/test_mobile_layout.py` and `scripts/mobile_audit.py --pages /tutorial` check the hub
  at phone widths in light and dark.
* `R:/ai_tutor_suite_tmp/missing_tmp/probe_missing_v2.py --vp phone|desktop --lang de` walks
  the hub, every replayable section and everything that starts by itself for a brand-new
  student and counts the old sentence (0 since the redesign).

## Limitations

* The community, page-review and moderation pages are partly hard-coded English/German, so
  French and Spanish students see English there; the tutorial's own texts are translated.
* A chapter whose anchor sits low on a long page (Progress and plan, Your plan) starts only
  once the student scrolls to it (on the page load of another chapter, 3 s after that one).
* Read-aloud steps depend on the browser's speech engine (skipped silently without it).
* The lesson test and the community set chapters need a lesson or a published set; until
  then the hub says when they start.

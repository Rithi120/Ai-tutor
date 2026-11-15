# Classroom readiness – implementation log

Branch `feature/project-mode-rebuild`, based on `8dea93a`. Nothing is committed, pushed or deployed
without the owner's explicit permission.

## 0. Baseline (before any change)

- Full suite on `8dea93a`: **3274 passed, 5 skipped, 0 failed** in 43 min 38 s (TEMP on R:, scratch DB;
  `instance/` unchanged).
- A read-only audit of all nine spec areas was done first (one auditor per area, findings with file:line
  evidence and reproductions against a scratch DB). The root causes are summarised per phase below.

## 1. Owner decisions (2026-11-13)

| Topic | Decision |
| --- | --- |
| Lesson quota for a multi-topic pack | Every topic lesson counts as a normal lesson: free = 2 per day, Premium = its higher allowance (3 or more). The chat card says honestly which topic lessons can start now and which unlock tomorrow or with Premium. |
| Editing a published deck | The previously approved snapshot **stays public** while the edit is re-checked; the label reads "Published · update being checked". Unmoderated content is never shown. |
| YouTube without an API key | Keep the search links but label them honestly ("Search YouTube for …"), never as video titles. Real video cards (title, channel, thumbnail) appear only when `YOUTUBE_API_KEY` is set. |
| League tiers / opt-out | Rename tier 6 → Emerald, tier 7 → Diamond (stored as integers, no migration). Opting out mid-week records the week as `withdrawn` and demotes when the learner was in the demotion zone. |

Engineering defaults chosen where the spec was explicit enough (documented, reversible):

- **Kompetenzraster extraction is re-enabled.** The 2026-10-26 rebuild removed it deliberately; this spec
  requires it. The orphaned `Competency` table, `learnova/exam_prep/competencies.py` and the
  `competency_extraction` task are reused, not duplicated.
- Competency statuses: `identified` (extracted, not confirmed or not attached to a topic), `not_started`,
  `teaching`, `practised`, `needs_revision`, `mastered`. "Full coverage" needs every competency
  practised or mastered. A student can mark a competency "not in this exam" with one tap; an excluded topic
  does not silently exempt its competencies.
- Format decision: ≤ 3 manageable topics → separate Short Lessons; ≥ 10 topics (or a large/difficult
  volume) → Project Mode; in between → a choice card with one recommendation. Thresholds are named
  constants.
- The chat accepts PDFs for exam material and hands them to the existing project creation path; chat
  photos used for a project are re-read with Project recognition (quality first).
- Short Lessons always include objective, terminology and common mistakes (spec 1.3), without changing
  the subject strategies used by Project Mode.
- Provenance labels: "From your notes" for material formulas; "Marked by your teacher" only for
  Kompetenzraster / teacher-highlighted pages. The AI-written "TEACHER NOTES / Teacher tips" card is
  renamed to a neutral "Tips" label (it never came from a teacher).
- YouTube thumbnails go through a server-side proxy (no student IP sent to Google, no CSP widening).
  Diagrams appear automatically when relevant, videos behind one tap.
- Multi-image: photos over ~4 MB / 4096 px are re-encoded client-side to 2560 px JPEG (camera scans stay
  lossless); optional per-image labels; 40 images per collection.

## 2. Phases, root causes and plan

Migration numbers are reserved per lane so parallel work cannot collide:
043 competency inventory · 044 chat study format · 045 multi-image collections · 046 learning resources.

### Phase 1.1 – Complete Kompetenzraster recognition (CRITICAL)
Root causes: (1) competency extraction is dead code – no route writes a `Competency` row, although the
docs say it does; (2) `topics.same_topic` lets a compound title ("Lineare und quadratische Funktionen")
absorb one of its parts, order-dependently, while every completeness check stays green; (3) the topic
prompt tells the model to fold small headings together; (4) coverage is per topic, not per competency;
(5) no check compares the number of "Ich kann" rows with what was extracted.
Plan: compound-title guard in merge/reconcile; grid files marked `source_kind='grid'`; competency
extraction as one more polled analysis step (one call per grid page, cached, lease-protected); grounding
of every statement against its page; deterministic "Ich kann" row count → gap banner with one-tap re-read;
derived per-competency status; coverage validator; workspace inventory UI.

### Phase 1.2 / 1.3 – Short Lessons vs Project Mode, lesson quality (CRITICAL)
Root causes: the chat has no exam-material signal (the TOPIC trailer carries one topic), no per-topic
lesson row (unique key per reply), and no route into project creation (that logic is inline in the HTML
form handler). PDFs are refused in chat with no hand-off. `/api/analyze` (Short Lesson) predates the
typed-parts teaching engine, so objective/terminology/common mistakes are missing.
Plan: EXAM trailer; migration 044; pure `study_format` decision; per-topic Continue Learning lessons;
`create_learning_project()` extracted from `projects()` and reused by a chat route; tap-only choice cards;
Short Lesson moved onto the teaching engine; project sections fall back to shared ConceptMastery.

### Phase 2 – Teacher-specific formula intelligence (CRITICAL)
Root causes: formula grounding compares word sets, so a LaTeX rewrite of a notes formula is dropped and a
wrong rearrangement (`U = R/I`) passes as "in the notes" and becomes a flashcard; no server-side LaTeX
check; `\rightarrow` folds to "arrow"; no teacher-vs-alternative distinction; practice never sees the
formulas; AI tips are labelled as the teacher's.
Plan: structural formula matching (`learnova/projects/formulas.py`), LaTeX validator + repair, prompt with
page notation + separate `latex` + variables/units, teacher-first lesson block with labelled
"Additional tip" part, formula-aware practice tasks (substitution, rearrangement, word, application,
transfer), honest labels.

### Phase 3 – Multiple image upload (HIGH)
Root causes: Vocabulary is single-document at every layer (no `multiple`, `capture` hides the gallery,
`request.files.get`, one import = one document, rescan replaces); Project Mode loses earlier picks
(native input replaces its FileList), sends everything in one request (> 40 MB → 413 → redirect → scans
lost), and does not keep gallery/camera order; no thumbnails or page gallery.
Plan: one reusable `image-collection.js` component (built on `image-attach.js` primitives) with preview,
delete, reorder, replace, labels, full-screen preview, progress, retry, compression and batching; JSON
413s; ordered `materials_meta`; thumbnail variant; multi-page vocabulary imports (migration 045).

### Phase 4 – Vocabulary type selector (HIGH)
Root cause: the kind already lives in `Flashcard.tags_json` but no UI sets it, the API ignores `kind`,
"other" is unknown (studied as a word), and the 8-tag cap can drop it.
Plan: `kind` API field stored as the first tag (no migration); Type group in the existing three-dot menu;
scopes, distractors and sentence grading respect the kind.

### Phase 5 – Mobile three-dot menu (HIGH)
Root cause: `.card-row-tools button { width:44px; height:44px }` in `learnova-pages.css` also matches
the menu items, so on phones every item becomes a 44×44 box with letter-by-letter labels overlapping its
neighbours; the same leak greys out Delete; the menu opens under the fixed save bar.
Plan: child selectors, menu-item colours (danger red), icon sizing, save-bar-aware placement, browser
regression test at 320/375/390/430/1280.

### Phase 6 – Leagues audit (MEDIUM)
Existing system is real (7 tiers, groups of 30, promotion/demotion, ledger-based weekly XP, once-only
week transition, server-side XP). Gaps: tier names, no XP gap to next rank, no history view, opt-out
dodges demotion, a failing transition 500s three pages, lifetime XP lost update, week-boundary race,
project exams draw from the flashcard cap, cheap one-card sessions.
Plan: fixes for each, owner decisions above.

### Phase 7 – Educational images and YouTube (MEDIUM)
Root causes: Wikimedia images shown without author/licence; YouTube is only search links shown as if they
were titles; German lessons search with English subject/grade; dark-mode video row invisible; chat can
render model-written links.
Plan: licence-checked Wikimedia, YouTube Data API v3 module (header key, cache, quota cap, availability
check), MEDIA trailer with server veto in chat, link-out cards, thumbnail proxy, migration 046.

### Phase 8 – Flashcard publishing status (CRITICAL)
Root cause: database and permissions are correct; the owner's set list hardcodes "Private"
(`static/js/flashcards/library.js`) and its API returns no publication state. The publication endpoint
has no visibility field; card-only edits never bump `updated_at`; `changed_since_publish` is reset by
other users studying the set.
Plan: one server-side `publication_visibility()` derived from the same rule as `visible_set_filters`,
used by every serializer and response; frontend renders only that; bfcache refresh; old approved
snapshot stays public during re-check.

## 3. Execution

Work runs in five isolated git worktrees (`R:/ai_tutor_wt/<lane>`), each lane implement → adversarial
review/fix, then merged into the main tree and verified with the full suite.

| Lane | Phases |
| --- | --- |
| exam | 1.1 → 1.2/1.3 → 2 |
| uploads | 3 |
| cards | 5 → 4 → 8 |
| leagues | 6 |
| resources | 7 |

## 4. Status

Legend: **Done + tested** = implemented and covered by automated tests (model and external APIs
patched); **Done, not fully verified** = implemented, but a part of it can only be checked live
(real model, real API, PostgreSQL, a real phone); **Not done** = deliberately not implemented.

| Phase | Status | Main tests |
| --- | --- | --- |
| 1.1 Kompetenzraster | Done + tested (model patched). Not verified on a real Kompetenzraster scan. | `test_competency_inventory.py` (23), `test_exam_prep.py` KompetenzrasterInventoryTests, `test_project_topics.py` CompoundTitleTests, `test_project_coverage.py` |
| 1.2 Lessons vs Project | Done + tested (model patched). The EXAM line's false-positive rate on real chats is unmeasured. | `test_study_format.py` (37), `test_assistant_browser.py` format-card tests |
| 1.3 Short Lesson quality | Done + tested (prompt and parts shape). Lesson quality with a real model unmeasured. | `test_study_format.py` /api/analyze tests, `test_lesson_*` |
| 2 Formulas | Done + tested (model patched). Commuted/rearranged forms are not matched algebraically. | `test_project_formulas.py`, `test_formula_intelligence.py` |
| 3 Multi-image | Done + tested (Playwright, system Chrome). Real phone camera not tested. | `test_multi_image_uploads.py`, `test_vocabulary_multi_page.py`, `test_image_collection_browser.py`, `test_multi_image_browser.py`, `tests/js/image-collection-rules.test.mjs` |
| 4 Vocabulary type | Done + tested. No new sentence exercise types. | `test_flashcard_kind.py`, `test_flashcard_kind_browser.py`, `test_vocabulary_kinds.py` |
| 5 Mobile menus | Done + tested (320/375/390/430/1280, light/dark). | `test_mobile_layout.py`, `test_creator_ux.py`, `scripts/mobile_audit.py` |
| 6 Leagues | Done + tested on SQLite. PostgreSQL concurrency verified only by the SQL issued. | `test_league_integrity.py`, `test_league_countdown_browser.py`, `test_profiles_leagues.py`, `test_progression_hardening.py` |
| 7 Images / YouTube | Done + tested with stubbed APIs. Real YouTube/Wikimedia never called; legal sign-off open. | `test_learning_resources.py`, `test_youtube_videos.py`, `test_learning_resources_browser.py`, `test_media_enrichment.py` |
| 8 Publishing status | Done + tested. bfcache Back-navigation not tested end to end. | `test_publication_visibility.py`, `test_publication_badge_browser.py`, `test_moderation_integration.py` |

### 4.1 Merge notes (all five lanes into the main tree)

- **Chat trailers.** Both the resources lane (MEDIA) and the exam lane (EXAM) added a hidden line to
  `TUTORING_LAYER` and both called it `assistant_chat:v6`. Merged as **`assistant_chat:v7`** with both
  lines (415 of 420 estimated tokens), one `ReplyTrailers` result with `media` and `exam_topics`
  (`Trailers` kept as an alias), one trailer regex `CHIPS|TOPIC|MEDIA|EXAM`. Reply text still loses
  model-written YouTube links (`postprocess_reply_parts`); `resources_json` and `study_json` are both
  written per reply.
- **Project creation.** The uploads lane's JSON answers (multi-image form) were moved into the exam
  lane's shared `create_learning_project()`: the result carries the notes upload's per-file outcomes;
  `NothingUsableError` carries them for the 422 answer; `/projects/<id>/pages/add` accepts notes and a
  Kompetenzraster and answers scripts with JSON.
- **Merge-seam review** (read-only, three reviewers + adversarial verification over chat, project
  creation, schema/i18n): one confirmed defect – the script (JSON) create path returned before the
  "Only the first 40 topics were added" notice and never mentioned Kompetenzraster files it skipped or
  could not use. Fixed: those notices are flashed for the page the script opens next; the notes
  photos stay answered in the JSON only (`test_what_the_json_does_not_carry_is_said_on_the_next_page`).
- **Migrations** run in number order 043 → 044 → 045 → 046.
- **Catalogues.** All lanes' blocks kept; renamed keys ("Legendary" → "Diamond", "Teacher tip(s)" /
  "TEACHER NOTES" → "Tip" / "TIPS") stay removed; es/fr have 3322 keys each, no key with two texts.
- **Static assets.** Default `STATIC_VERSION` bumped from `20261101` to `20261113`, so returning browsers
  fetch the new CSS/JS.
- **Moderator restriction.** After merging, an author could lift a moderator's "restrict" by resubmitting
  or by unpublishing and publishing again. Both now keep the set hidden (409 `restricted_by_moderator`),
  with a test.

## 5. Files

### 5.1 Created
`CLASSROOM_READINESS_IMPLEMENTATION.md`, `docs/LEARNING_RESOURCES.md`, `docs/MULTI_IMAGE_UPLOADS.md`,
`learnova/assistant/resources.py`, `learnova/assistant/study_format.py`, `learnova/projects/formulas.py`,
`learnova/uploads/thumbnails.py`, `learnova/youtube_videos.py`, `static/js/image-collection.js`,
`static/js/image-collection-rules.js`, `static/js/project-create.js`, `static/js/project-pages.js`,
`static/js/resources.js`, `tests/js/image-collection-rules.test.mjs`, `tests/test_competency_inventory.py`,
`tests/test_flashcard_kind.py`, `tests/test_flashcard_kind_browser.py`, `tests/test_formula_intelligence.py`,
`tests/test_image_collection_browser.py`, `tests/test_league_countdown_browser.py`,
`tests/test_league_integrity.py`, `tests/test_learning_resources.py`,
`tests/test_learning_resources_browser.py`, `tests/test_multi_image_browser.py`,
`tests/test_multi_image_uploads.py`, `tests/test_project_formulas.py`,
`tests/test_publication_badge_browser.py`, `tests/test_publication_visibility.py`,
`tests/test_study_format.py`, `tests/test_vocabulary_multi_page.py`, `tests/test_youtube_videos.py`.

### 5.2 Modified
Backend: `app.py`, `learnova/config.py`, `learnova/ai_services/{contracts,prompts}.py`,
`learnova/assistant/{__init__,continue_learning,formatting,presets}.py`, `learnova/community/service.py`,
`learnova/diagnostics/prompts.py`, `learnova/exam_prep/competencies.py`,
`learnova/flashcards/{modes,service}.py`, `learnova/gamification/{leagues,service}.py`,
`learnova/lessons/{lesson_context,lesson_plan}.py`,
`learnova/lessons/strategies/{__init__,base,mathematics,sciences}.py`, `learnova/media_enrichment.py`,
`learnova/projects/{coverage,terms,topics}.py`, `learnova/translations/catalog.py`,
`learnova/translations/data/{ar,es,fr}.json`, `learnova/tutorial/registry.py`,
`learnova/uploads/{__init__,service}.py`, `learnova/vision/notation.py`.
Frontend: `static/app.js`, `static/scanner.js`, `static/styles.css`,
`static/css/{assistant,attachments,flashcards,learnova-components,learnova-content,learnova-dark,learnova-pages,learnova-tokens,profile,project-workspace,vocabulary}.css`,
`static/js/{assistant,assistant-rules,image-attach,markdown,profile,vocabulary}.js`,
`static/js/flashcards/{common,create,editor-rules,library,mode-engine,overview,publish}.js`.
Templates: `assistant.html`, `components/_profile_settings.html`, `dashboard.html`,
`flashcards_create.html`, `flashcards_mode.html`, `flashcards_publish.html`, `index.html`,
`leaderboard.html`, `lesson_history.html`, `project_workspace.html`, `projects.html`,
`section_learning.html`, `vocabulary_import.html`, `vocabulary_review.html`.
Config/docs: `.env.example`, `render.yaml`, `README.md`, `scripts/mobile_audit.py`,
`docs/{ADAPTIVE_EXPLANATIONS,AI_ROUTING,ARCHITECTURE,ASSISTANT,COMMUNITY_MODERATION,EXAM_AUTOPILOT,MOBILE_LAYOUT,OPERATIONS,PROFILES_AND_LEAGUES,PROJECT_MODE,SUBSCRIPTIONS,VISION}.md`.
Tests updated (intended behaviour changes only, each documented in the lane reports):
`tests/fixtures/ai/{en,de}/valid.json`, `test_assistant_browser`, `test_assistant_conversations`,
`test_assistant_learning`, `test_chat_photo_live_fixes`, `test_creator_ux`, `test_exam_prep`,
`test_js_browser`, `test_lesson_prompts`, `test_lesson_teaching_engine`, `test_live_final_lessons`,
`test_media_enrichment`, `test_mobile_layout`, `test_moderation_integration`, `test_profiles_leagues`,
`test_progression_hardening`, `test_project_coverage`, `test_project_topics`, `test_subject_strategies`,
`test_subscriptions`, `test_unified_project_mode`, `test_vocabulary_kinds`. No test was deleted or
weakened; pinned expectations moved to the new intended behaviour (typed topics split on newlines only,
a compound title no longer absorbs a narrower topic, prompt versions, the 044 constraint name).

## 6. Database migrations (all additive except the 044 unique key)

| Migration | What | Notes |
| --- | --- | --- |
| `043_competency_inventory` | 11 columns on `competency` (position, statement_key, source_file_id, group_path, verified, state, taught_count, answers, correct_answers, last_evidence_at, evidence_json) + 2 indexes | Table was empty everywhere; no backfill. Existing DBs get `source_file_id` without a FK (SQLite cannot add one). |
| `044_chat_study_format` | `conversation_message.study_json`; `conversation_lesson.topic_key/position/kind/project_id`; unique key widened from `uq_conversation_lesson_source (conversation_id, source_message_id)` to `uq_conversation_lesson_topic (conversation_id, source_message_id, topic_key)` | **Drops a constraint.** PostgreSQL: ADD COLUMNs, `DROP CONSTRAINT IF EXISTS`, `CREATE UNIQUE INDEX IF NOT EXISTS`. SQLite: table rebuilt in one transaction. Existing rows get `topic_key=''` and keep their meaning. Verified on SQLite only. |
| `045_multi_image_collections` | `project_page.label`; new table `vocabulary_import_page` | Additive; legacy imports read as before. |
| `046_learning_resources` | `conversation_message.resources_json`; new table `learning_resource_cache` | Additive. |
| `047_lesson_project_link` | `lesson.project_id` (nullable FK to `learning_project`) + index; backfill links existing exam-review and project-practice lessons to their owner's project | Additive (§13). |
| 048 (SQLite only, not recorded) | Rebuilds `learning_project`, `attempt`, `recall_card`, `final_exam`, `study_plan_session` with `AUTOINCREMENT` (one transaction per table, every id and index kept, FK check before commit) | **Rewrites tables on SQLite** — on the first start the local `instance/numeri.db` is rebuilt; copy that file first. Does nothing on PostgreSQL (production). Checked on every start; a failure is logged and the app still starts. |

043–047 are idempotent and recorded once; on a fresh database they are recorded as no-ops.

## 7. API endpoints

New:
- `POST /projects/<id>/delete`, `POST /lessons/<id>/delete` – owner-request delete (§13).
- `POST /projects/<id>/files/<file_id>/grid` – mark/unmark a file as the Kompetenzraster.
- `POST /projects/<id>/competencies/confirm`, `.../competencies/<cid>/exam` ("Not in this exam"),
  `.../competencies/<cid>/topic` ("Make it a topic"), `.../competencies/pages/<page_id>/reread`,
  `.../add-missing`, `.../add`, `.../complete` (gap banner actions).
- `POST /api/assistant/conversations/<id>/project` – exam project from the chat (JSON or a PDF).
- `GET /projects/<id>/pages/<page_id>/image/thumb` – 360 px page thumbnail.
- `POST /api/vocabulary/imports/<id>/pages`, `POST /api/vocabulary/imports/<id>/pages/<page_id>/extract`.
- `GET /api/assistant/messages/<id>/resources`, `GET /api/lessons/<session_id>/videos`,
  `GET /media/yt-thumb/<video_id>`.

Changed: `POST /projects` and `POST /projects/<id>/pages/add` (JSON mode, Kompetenzraster files,
newline-only typed topics; pages/add 60/h), `POST /projects/<id>/analysis/step` (competency step),
`POST /api/assistant/conversations/<id>/lesson` (`topic_key`), conversation payloads
(`continue_learning.format`, resources), `POST /api/analyze` (teaching engine, `format_hint`,
`videos_available`), `GET /dashboard` and `GET /lessons/<id>` (completed mark), vocabulary import
extract/rescan (`page_id`), flashcard set/session APIs (`kind`, publication visibility), community
publish/resubmit/unpublish (visibility fields; restricted sets stay hidden), moderation decide,
`GET /leaderboard` and `GET /api/leaderboard` (gaps, history), settings opt-out (withdrawn),
`POST /exams/<id>/submit` (practice XP budget), the 413 handler (JSON for scripts).

## 8. AI prompt and model changes

No model or provider changed. Prompt versions (fixtures updated):
- `assistant_chat` v5 → **v7** (MEDIA line + honesty rule against model-written links; EXAM line).
- `competency_extraction` v1 → v2 (every row copied exactly, heading chain, repair-not-reject contract).
- `lesson_generation` v9 → v10 (teacher-first formula rule, `alternative` part); Short Lessons labelled
  `+short1`, chat topic lessons `+chat2`.
- `question_generation` v2 → v3 (notes formulas + rotating formula task with worked solution).
- `final_exam_generation` v3 → v4 (section formulas).
- `project_section_generation` stays v4 on purpose (protects every project's topic cache); its prompt
  now keeps every Kompetenzraster row and asks formula subjects for page notation + separate LaTeX.
- Vocabulary validation asks the existing translation task in chunks of 40 words.

## 9. Environment variables (all optional; defaults in `learnova/config.py`)

`FEATURE_CHAT_STUDY_FORMAT` (true), `CHAT_PROJECTS_PER_HOUR` (10), `PROJECT_MAX_FILES_PER_REQUEST` (40),
`VOCABULARY_MAX_PAGES` (40), `VOCABULARY_PAGE_UPLOADS_PER_HOUR` (60), `FEATURE_LEARNING_RESOURCES` (true),
`YOUTUBE_API_KEY` (empty – leave unset until legal sign-off), `YOUTUBE_REGION_CODE` (DE),
`YOUTUBE_DAILY_SEARCH_LIMIT` (80), `YOUTUBE_CACHE_DAYS` (7), `YOUTUBE_TIMEOUT_SECONDS` (4),
`YOUTUBE_TRUSTED_CHANNELS` (empty), `LEAGUE_CLOSE_GRACE_SECONDS` (120), `STATIC_VERSION`
(default now 20261113). No secret is in the repository; `render.yaml` lists `YOUTUBE_API_KEY` with
`sync: false` only.

## 10. Known limitations

- Everything AI-related was tested with the model patched. Real Kompetenzraster scans (multi-column
  level tables), the EXAM line's false-positive rate, and how well the model follows the formula and
  Short Lesson rules are unmeasured.
- Competency answer evidence uses server-side word matching (the model does not tag questions). The
  inventory does not reorder the study plan; `exam_prep_card` / `ExamPrepState` do not exist.
- A combined header ("Lineare und quadratische Funktionen") can stay as an extra empty topic (one-tap
  merge offered). The optional subtitle rule is not implemented (owner decision).
- Chat projects are not auto-planned at creation; the student confirms the topics with one tap and the
  existing scheduler plans up to the exam date.
- Two topic lessons tapped within a minute can hit Groq's free-tier rate limit (the row shows Retry).
- Formulas: commuted/rearranged forms (U = I·R vs U = R·I) are not matched; they show as "added". A
  card made before this change from a LaTeX name can be duplicated once by a re-analysis.
- Vocabulary: no route to delete a single failed import page (it counts toward 40); the flashcard
  import page stays single-file; no context/grammar exercises for sentences; the German set-level
  "Duplikat" label collision remains; legacy cards with a bare `sentence`/`other` tag read as that type.
- Leagues: PostgreSQL advisory lock and atomic XP verified only by the SQL issued; farming bounded, not
  prevented; no cron/systemd timer in `render.yaml` (the week closes on the first request after the
  grace); the profile card shows no gaps/history; former tier-6 learners are not told about the rename.
- Resources: real YouTube/Wikimedia never called; relevance is lexical; thumbnail caches are per
  process; lessons from saved practice get no media; old lesson pictures without licence data are
  hidden on resume.
- Publishing: bfcache Back-navigation untested end to end; moderator corrections reset the "changed
  since publish" baseline.
- ES module imports (e.g. `create.js` imported by another module) carry no version query, so a browser
  may keep an old module until its cache revalidates.

## 11. Deployment considerations

1. Back up the production database. Run migrations 043–047 (they run in `ensure_database` at start-up)
   **first against a disposable PostgreSQL 16 copy** (`docs/OPERATIONS.md` migration rehearsal): 044 is
   the first migration that drops a constraint and has only been run on SQLite.
2. Bump `STATIC_VERSION` (default already 20261113) on every deploy of these assets.
3. Audit the Oracle production environment pins (the `render.yaml` token pins need the owner).
4. Leave `YOUTUBE_API_KEY` unset until the owner/legal sign-off on the YouTube API Services Terms and
   under-13 use; without it Learnova shows honest "Search YouTube for …" links.
5. Optional: install the leagues cron/systemd timer (`flask --app app leagues process`) so the week
   transition does not run inside a student's request.
6. `FEATURE_CHAT_STUDY_FORMAT=false` and `FEATURE_LEARNING_RESOURCES=false` switch the two new chat
   behaviours off without a code change.
7. The prompt-version bumps invalidate AI response caches for those tasks (expect a short rise in calls).

## 12. Tests executed

- Baseline `8dea93a`: 3274 passed, 5 skipped.
- Each lane: its implementer's and adversarial reviewer's targeted runs (details in the lane reports).
- After merging: targeted runs over every conflict area (595 passed, 2 skipped; earlier merges 317 and
  256 passed), then the full suite:

- **Full suite on the merged tree (2026-11-13): 3600 passed, 11 skipped, 1 failed** in 49 min 43 s
  (`instance/` unchanged). The flaky tutorial browser test and `test_ocr_cost` passed in this run.
- The one failure, `test_learning_resources.py::MigrationTests::test_an_old_database_gets_the_column_and_the_record_once`,
  is order-dependent and already failed in the resources lane's own worktree when run after
  `test_analysis_steps.py`'s migration test. Cause: the test drops a column through one pooled SQLite
  connection, and an idle pooled connection with the old cached schema then refuses the ADD COLUMN as
  a duplicate. A real start-up uses fresh connections, so the migration itself is correct. Fix in the test only (no
  assertion changed): `db.session.remove(); db.engine.dispose()` after the drop, the pattern other
  migration tests use. Re-run in the failing order and alone: passes (2/2, 31/31).
- After the suite: `test_multi_image_uploads`, `test_unified_project_mode`, `test_competency_inventory`
  with the merge-seam fix: 62 passed.
- **Full suite after the delete/show-more follow-up and its review fixes (2026-11-13): 3636 passed,
  11 skipped, 0 failed** in 51 min 59 s. The `instance/` guard reported `numeri.db` changed: that
  was the owner's own dev server (tutorial skips and a re-plan by user 2 at 22:31–22:33), which ran
  migrations 043–048 on the local database. Checked afterwards: `integrity_check` ok, no foreign-key
  problems, the five 048 tables carry AUTOINCREMENT with their rows, ids and indexes, and no temporary
  table was left behind.
- `scripts/mobile_audit.py` over the final tree (all pages, all widths, light and dark, own scratch DB):
  **0 page/viewport combinations with blocking faults out of 770.**

## 13. Follow-up: delete and show more (owner request 2026-11-13)

Owner request: "in dashboard make a delete button for unwanted projects and short lessons. and in
settings and dashboard all lists are rolled out so make show further button".

**Routes.** `POST /projects/<id>/delete` (`delete_project`, 20/hour) and `POST /lessons/<id>/delete`
(`delete_lesson`, 60/hour): login, owner only (404), CSRF, `next` back to the page it came from
(never the deleted project's own page, never off-site). Flash "Project deleted." / "Lesson deleted."
(German, French, Spanish). Table-by-table handling: `docs/ARCHITECTURE.md` "Deleting a project or a
saved lesson". Kept: XP and league entries, ConceptMastery and its history, learning events, the
usage ledger (no refund). The project's flashcard set is detached and kept. Chat cards that pointed at
a deleted lesson or project offer to build it again (their `conversation_lesson` rows are deleted).
A project topic's lesson has no delete button and is refused by the route (it is part of its
project's progress; deleting the project removes it).

**UI.** Dashboard: a trash button (44 px, `--ln-danger-text`, aria-label "Delete project “…”") beside
each project card's link (the card is wrapped, its look unchanged) and a "Delete" button in each
Short Lesson row; the project workspace has "Delete this project" at the bottom. One browser
confirmation naming the item, saying it cannot be undone and that XP is kept. "Show more (N)" /
"Mehr anzeigen (N)" on every long dashboard list (Mistake Notebook starts at 3) and in Settings (code
history; the plan comparison stays open, see the fixes below). Dashboard caps raised (lessons 20 → 50, projects 6 → 50, concepts/improvements
/exams 5 → 30, flashcard and vocabulary sets 4 → 12, Settings codes 5 → 30), with grouped counts
instead of loading every chat message and page.

**Files.** `app.py` (routes, `remove_project_records`, `remove_lesson_records`,
`deletion_return_url`, `forget_deleted_sessions`, caps), `learnova/dashboard/service.py`,
`templates/dashboard.html`, `templates/project_workspace.html`, `templates/components/_subscription.html`,
`templates/base.html`, new `static/js/show-more.js`, `static/js/show-more-rules.js`,
`static/js/confirm-delete.js`, `static/css/learnova-components.css`, `static/css/learnova-dark.css`,
`learnova/translations/catalog.py` + `data/fr.json` + `data/es.json` (12 strings each, counted),
`scripts/mobile_audit.py` (seeds long lists), `docs/ARCHITECTURE.md`.

**Tests.** New `tests/test_dashboard_delete.py` (19: every dependent table emptied, no dangling key
by `PRAGMA foreign_key_check`, XP/league/mastery/ledger unchanged, set detached, 404/405/CSRF, safe
redirect, Mistake Notebook, resume card, project lessons refused, no form inside a link, caps, equal
query count for 5 and 40 lessons, Settings history, chat lesson/topic/project cards degrade), also run
with `LEARNOVA_TEST_SQLITE_FK=1`; new `tests/test_dashboard_show_more_browser.py` (4, Chrome: 390 and
320 px, light and dark, focus, 44 px, no sideways scroll, no-JS, German label, confirm cancel and
accept); `tests/js/show-more-rules.test.mjs` (7) through `tests/test_js_browser.py`; helper
`tests/dashboard_seed.py`.

**Fixes after review (2026-11-13).**
- *Exam review and practice lessons outlived their project.* The "Exam review: …" lesson that
  `submit_final_exam` creates (its graded answers are its attempts, so the exam's mistakes sat in the
  Mistake Notebook) and the mixed-practice / targeted-revision / exam-gap lessons (the first two with up
  to 24 000 characters of the project's notes in their saved state) had no `section_id`, so the delete
  missed them. New column `Lesson.project_id` (FK, nullable, migration 047 with a backfill of the
  existing review and practice lessons), written with the lesson (`persist_lesson(project_id=)` via
  `start_saved_practice`, and in `submit_final_exam`); `remove_project_records` deletes lessons by
  `section_id` OR `project_id`. They stay deletable one by one from the dashboard.
- *SQLite reused freed ids.* A kept ledger row `project:<id>` made the next project with that id free,
  and kept event keys `attempt:<id>` (also `recall:`, `final-exam:`, `study-plan-session:`) denied XP to
  new answers. `learning_project`, `attempt`, `recall_card`, `final_exam` and `study_plan_session`
  declare `sqlite_autoincrement`; migration 048 (`rebuild_sqlite_autoincrement`, SQLite only, checked on
  each start, logged and retried on failure) rebuilds existing tables keeping every id and index.
- *Plan comparison folded.* Settings' Free/Premium feature lists hid "Shows ads" / "No ads" behind
  "Show more" while the tour step promised them; `data-show-more` removed from both (fixed text, not a
  growing list). The code history still folds.
- *"Show less" scrolled its button under the sticky nav.* `[data-show-more] > *` and
  `.show-more-button` get `scroll-margin-top: calc(var(--app-nav-height, 72px) + 12px)`.
- *"Weakest concepts" showed mastered concepts* after the cap went from 3 to 30: now only concepts
  whose status is `weak` or `learning`, capped at 30.

Tests for these: `tests/test_dashboard_delete.py` `ProjectMaterialLessonTests` (review and practice
lessons deleted with their project, mixed practice saved with `project_id`, migration 047 backfill),
`SqliteIdReuseTests` (no reused ids after either delete, the new project is counted, a legacy table is
rebuilt with AUTOINCREMENT keeping ids and indexes), `ListContentTests` (weakest concepts, plan
comparison open), the `lesson` row of `PROJECT_TABLES` now `section_id OR project_id`;
`tests/test_knowledge_gate_integration.py` (real exam submit and close-gaps: both lessons carry the
project); `tests/test_dashboard_show_more_browser.py` (Show less at 390 and 320 px keeps the focused
button below the nav and uncovered; the plan comparison shows both ad lines at 320 px). Each new test
was run with its fix temporarily undone and failed.

**Limitations.** No undo (the confirmation says so). Exam-gap practice lessons made before migration 047
name no project in their state, so the backfill cannot link them; they stay ordinary Short Lessons
(concept names only, no notes). Other deletes that existed before (flashcard sets, vocabulary lists)
are not covered by the AUTOINCREMENT change, which is limited to the five tables named above. A
request still writing to the project at that moment fails on PostgreSQL's foreign key instead of
recreating rows (the delete takes the project row's lock first). The `/projects` list has no delete
button (the workspace and the dashboard have one). Cached page thumbnails stay in the worker's LRU
until evicted (keys include the file hash, so they are never shown for other content).

## 14. Real-world acceptance audit (2026-11-14)

Live models, real photos, real Wikipedia, real browser: see `REAL_WORLD_ACCEPTANCE_AUDIT.md`.
Six reproduced defects fixed (A-1 unread pages after a busy reading, A-2 Wikimedia
thumbnail host, A-3 "Ich kann" heading counted as a row, A-4 non-existent page chips, A-5
chat EXAM/MEDIA fallback and leaked marker, A-6 ":" flagged by the review). Open: vision tier
(R1), AI explanation accuracy (R2), provenance labels for Kompetenzraster-only topics (R3).

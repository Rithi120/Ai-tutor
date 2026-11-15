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

| Phase | Status | Tests |
| --- | --- | --- |
| 1.1 | in progress | |
| 1.2/1.3 | in progress | |
| 2 | in progress | |
| 3 | in progress | |
| 4 | in progress | |
| 5 | in progress | |
| 6 | in progress | |
| 7 | in progress | |
| 8 | in progress | |

(Modified files, new endpoints, migrations, test results, known limitations and deployment notes are
filled in after the merge.)

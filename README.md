# Learnova — source-grounded AI study tutor

Learnova helps a student prepare for school exams from their own material. A student can upload PDFs/images or scan pages with a phone or laptop camera, verify structured print/handwriting recognition, review an editable learning plan, learn each focused section, and take a source-grounded timed final exam.

## Architecture

Learnova is organized into domain packages for authentication, uploads, OCR, AI services, projects, lessons, quizzes, exams, dashboard, settings, translations, web security, and utilities. Flask routes retain their existing URLs while delegating validation, ingestion, deterministic learning rules, AI transport, and optimized dashboard queries to services. Shared page chrome lives in `templates/components`; frontend tokens and behavior live in reusable `static/css` and `static/js` modules.

See [architecture and module responsibilities](docs/ARCHITECTURE.md) and the [extension guide](docs/EXTENDING.md).

The earlier quick lesson, adaptive quiz, translation, tutor chat, dashboard, spaced repetition, and Mistake Notebook remain available. Accounts, uploads, extracted text, attempts, mastery, exam answers, and results are isolated by user and stored in the database.

## Languages and translation workflow

Learnova ships four complete interface languages: English (`en`), German (`de`), French (`fr`) and Spanish (`es`). **Settings → Language** is the only language control. For a signed-in student, `User.preferred_language` is authoritative and survives navigation, refreshes, logout/login, server restarts, lessons, quizzes, practice, and exams. For a visitor, the Flask session is used; on the first visit only, the browser language is considered. English is the final fallback.

The resolver order is:

1. signed-in user's `preferred_language`;
2. `session["language"]`;
3. supported browser language on the first visit;
4. English.

Interface messages and the browser-side dictionary live in [learnova/translations/catalog.py](learnova/translations/catalog.py). English source strings are the message identifiers, so there is no separate `.pot`. German is inlined in that module and defines `REQUIRED_KEYS`; every other language is a JSON file under [learnova/translations/data/](learnova/translations/data/). The root `i18n.py` is a compatibility shim that re-exports from there.

Templates use `_()` and JavaScript reads `window.LEARNOVA_I18N`, which the shared base template creates for the active request. Learning-content language is separate from interface rendering: every tutor, lesson, adaptive-practice, answer-feedback, chat, section, and exam generation request receives an explicit instruction naming one of the eight content languages in `CONTENT_LANGUAGE_NAMES`.

This project uses a checked-in Python catalogue rather than gettext `.po`/`.mo` binaries, so extraction and compilation are intentionally no-op steps:

```powershell
# Extraction: not required; English source strings are the message identifiers.
# Compilation: not required; the catalogue is imported directly at application startup.
python -m py_compile learnova/translations/catalog.py
python -m pytest tests/test_i18n.py -q
```

`SUPPORTED_LANGUAGES` is **computed, not edited**. A language in the `LANGUAGES` registry
becomes selectable only once its catalogue covers every one of the ~1250 `REQUIRED_KEYS`,
so an unfinished translation never shows a half-English interface. Nineteen further
languages are registered and not yet selectable; Arabic is the closest, and is also the
only right-to-left language.

To finish a language:

1. add or complete `learnova/translations/data/<code>.json` — `python scripts/translate_catalog.py <code>` generates a starting point;
2. confirm it is selectable: `python -c "from learnova.translations.catalog import SUPPORTED_LANGUAGES; print(SUPPORTED_LANGUAGES)"`;
3. if the language should also be an AI content language, add it to `CONTENT_LANGUAGE_NAMES` in `app.py`;
4. run `python -m pytest tests/test_i18n.py -q`, which checks catalogue coverage, persistence, the JavaScript catalogue, validation and user isolation.

The settings selector and onboarding read `language_options()`, so a newly complete
language appears in both with no template change.

## Optional voice accessibility

Learnova keeps typing and reading as the primary path and adds browser-native voice controls as progressive enhancement. Tutor chat, written quiz answers, recall fields, and written exam answers expose an optional microphone control. Recognition starts only after the student presses the button, never submits or grades automatically, leaves the transcription editable, stops when the page is hidden or left, and does not persist raw audio.

Lesson explanations, questions, revealed hints, tutor replies, and answer feedback provide reusable Listen controls with play, pause, resume, stop, and speed selection. Speech never autoplays, only one item may play at a time, and navigation cancels active playback. Recognition and playback use `window.LEARNOVA_CONTENT_LANGUAGE` (`en-US` or `de-DE`) independently from the interface-language variable. Unsupported browsers retain every manual form and show a translated status message.

The reusable implementation lives in `templates/components/_speech_controls.html`, `static/js/speech-to-text.js`, and `static/js/text-to-speech.js`. This is not a streaming or real-time voice assistant.

## Intelligent Exam Study Planner

Open **More → Study Planner** after creating a learning project. Choose the project, future exam date, target grade, available minutes, weekdays, and preferred starting difficulty. Learnova then builds a deterministic daily schedule from the project's unfinished sections, saved concept mastery, overdue spaced-repetition reviews, recent mistakes, and remaining exam preparation. No AI tokens are required to calculate the schedule.

Each plan shows today's study tasks, exam countdown, readiness estimate, completed sessions, hours studied, streak, weak concepts, next review/quiz, remaining lessons, and upcoming mock exam. The monthly calendar opens every scheduled day. Completing a quiz, recall card, or final exam updates only affected future sessions. A low result adds or advances review and lowers relevant difficulty; a strong result moves forward and removes redundant review. Skipping a day rebalances incomplete work across existing future study days instead of appending it after the plan.

Planner task records store language-neutral kinds and source IDs. The shared translation catalogue localizes English/German labels and dates at render time. Every plan and session query checks both plan and project ownership, and attempts/mastery records remain append-only.

## Main workflow

**The fast path (what a student on a phone actually does):** open **Projects**, add
photos or a PDF, tap **Start lesson from these pages**. Nothing else is required — no
project name, no subject, no review. The start page then does the whole chain by itself
and shows three steps while it runs: *Reading your pages* (one request per page),
*Building your lesson* (`POST /projects/<id>/quick-start`: accepts the recognition as it
stands, marked `unreviewed`, builds the sections if there are none, opens an AI lesson on
the first unfinished section), *Opening your lesson* (redirect to the lesson, which
explains the material from the student's own pages and then runs the knowledge-gated
test). Pages that could not be read never block the student; they stay visibly marked for
the detailed review, which is one link away ("Check the scan first (optional)"). Tapping
**Continue lesson** on the project page, or `quick-start` again, **resumes** the saved,
unfinished test instead of starting over; the Overview and the New Lesson page show a
"Continue where you left off" banner for the newest unfinished lesson. Tests:
`tests/test_quick_start.py`.

The detailed path below is still there for students who want to correct the scan first.

1. Open **Study projects** and choose **Upload PDF**, **Upload Images**, and/or **Scan with Camera**. Camera pages and uploads can be combined in one ordered project. A project supports up to 20 pages, 15 MB per file, and 40 MB per request; 10–15 pages is recommended.
2. For camera capture, explicitly open the scanner, position one full page in the frame, capture, review, crop/rotate/retake, accept it, and continue. Front/rear cameras can be switched when the browser exposes both. Closing the scanner stops every camera track.
3. Learnova stores the original and a lossless processed recognition copy. Processing applies EXIF orientation, student-selected crop/rotation, conservative brightness/contrast/sharpness correction, resolution checks, and blur/glare warnings. Small captures are enlarged so thin strokes survive the vision model's own downscaling; large photos are never shrunk. PDFs—including scanned PDFs—are rendered page by page.

   Binarisation is **local, not global**: each pixel is compared with its own neighbourhood rather than with one threshold for the whole page. A single global cut assumes even lighting, which a phone photo of a notebook rarely has — on a page with a shadow across one side, the global cut turned that whole side into a black blob and erased every faint pencil stroke in it. Geometry correction (4-point perspective, then rotation deskew) needs `opencv-python-headless` and `numpy` from `requirements.txt`; where those wheels are missing it degrades silently to no-op, so recognition still runs but crooked photos are not straightened.
4. Run recognition. Each page is saved independently as typed blocks: printed text, handwriting, formulas, tables, diagrams, headings, annotations, uncertain content, and crossed-out content. Blocks retain confidence, bounding boxes, source file/page IDs, and review state.
   Anything still unclear then gets a **second look**: those words are cropped out of the page, enlarged, stitched onto one numbered sheet and re-read in a single extra vision call. Messy handwriting usually fails for a mechanical reason — the word is small, the whole page is downscaled before the model sees it, and a shaky word survives as a smudge — so a close-up recovers most of them. A close-up reading only replaces text when it is **more** confident than the first pass, the previous reading is kept on the block, and a fragment the model reports as illegible is left alone rather than guessed at. The pass is optional by construction: if it fails, the first reading survives untouched. `FEATURE_HANDWRITING_SECOND_LOOK=false` turns it off; `HANDWRITING_SECOND_LOOK_MAX_REGIONS` (default 8) caps how many words one sheet carries, which is what keeps it to one request per page.
5. Review original/processed images, uncertain source regions, formulas, diagram labels, and recognized text. Correct or restore text, exclude/rescan/retry pages, confirm page order, and confirm teacher emphasis or importance. Learnova does not create sections until the review is confirmed, unless the student explicitly continues without full review.
6. Build and edit learning sections, then learn at simple, standard, or detailed explanation level. Active recall and **Test Yourself** save attempts and update mastery. Confirmed high-priority content receives more weight.
7. Configure **Final Exam Mode** with 5–50 questions and a server-controlled duration. Answers autosave; hints, chat, corrections, and expected answers remain hidden until submission. Results include source references and mistakes are saved to the Mistake Notebook.

Camera APIs normally require HTTPS, except browsers generally allow them on `localhost`. If permission or camera support is unavailable, Learnova visibly directs the student to image upload.

## Local setup — Windows PowerShell

Python 3.13 is supported.

```powershell
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`AI_MODE=cached` is the safe development default: it cannot reach Groq unless you also set `ALLOW_LIVE_AI=true`, so a stray request is never billable. Edit `.env` and set at least:

```dotenv
APP_ENV=development
AI_MODE=cached
SECRET_KEY=a_long_random_value
DATABASE_URL=sqlite:///learnova.db
PORT=5000
```

Generate a suitable local secret with:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Initialize or upgrade the database, then start Flask:

```powershell
python -m flask --app app init-db
python app.py
```

Open `http://127.0.0.1:5000`. With the default SQLite URL, the database is `instance/learnova.db`. Existing installations using the legacy local database filename continue to open that database automatically unless `DATABASE_URL` overrides it.

## Database initialization and migrations

`python -m flask --app app init-db` is idempotent. It creates every missing table and applies versioned, additive upgrades recorded in `schema_migration`; it does not erase saved progress. Startup runs the same initialization automatically. Migration `009_add_query_path_indexes` adds composite indexes for PostgreSQL-scale dashboard, review, attempt, page-order, section-order, and exam queries. Migration `011_add_intelligent_study_planner` adds indexed study plans and calendar sessions. The existing `StudySession` remains the authoritative saved lesson state; planner days use the separate `StudyPlanSession` model to preserve compatibility.

Configuration profiles are selected with `APP_ENV=development|testing|production`. Production requires `SECRET_KEY`, enables secure cookies, and should use a shared `RATELIMIT_STORAGE_URI` such as Redis when running multiple web workers. Tests disable CSRF and rate limiting through their isolated Flask testing context.

The current schema includes users, lessons, attempts, mastery, persisted study sessions, chat messages, learning projects, private source files/pages, structured document blocks, learning sections, recall cards, final exams, exam questions, and autosaved exam answers. `006_link_lessons_to_sections` connects section tests to attempts; `007_add_document_recognition` adds original/processed recognition metadata, confidence, review state, priority, rotation, and recovery fields; `008_add_user_preferred_language` adds the indexed `en`/`de` account preference and safely defaults existing users to English. Back up production data before a deployment.

For PostgreSQL, set `DATABASE_URL` to a valid SQLAlchemy `postgresql://` URL. Render supplies this automatically through `render.yaml`.

Community publishing stores approved content in PostgreSQL-backed
`public_flashcard_set` and immutable `flashcard_publication_version` rows. AI reviews,
ratings, study records, and independently saved private copies are database-backed.
`FEATURE_COMMUNITY_LIBRARY` controls public browsing and
`FEATURE_COMMUNITY_PUBLISHING` independently controls owner submissions. The Render
blueprint enables both and connects the web service to the managed `learnova-db`
PostgreSQL database. Startup applies idempotent schema migrations, including
`015_immutable_publication_versions`, before serving requests.

## AI and cost configuration

Every lesson, quiz, answer evaluation, tutor chat, translation, OCR/recognition, project section, adaptive-practice, and final-exam request passes through `learnova.ai_services.service`. No route or other domain service creates a Groq client.

There are two modes:

- `AI_MODE=cached`: returns an identical request's saved response; a miss may call Groq and therefore requires `ALLOW_LIVE_AI=true` in development. This is the default outside production.
- `AI_MODE=live`: always calls Groq. Development requires `ALLOW_LIVE_AI=true`; production refuses to start unless `AI_MODE=live` is explicitly configured.

Run without spending anything. Without `ALLOW_LIVE_AI`, a cache miss raises a safe configuration error instead of reaching the provider:

```dotenv
APP_ENV=development
AI_MODE=cached
```

Failure handling is not a runtime switch. The sample-response corpus in `tests/fixtures/ai/` covers malformed and empty output, missing and wrongly typed fields, duplicate prompts and IDs, unknown source references, incorrect counts, invalid scores and difficulties, oversized output, and provider timeout/rate-limit errors; `tests/provider_stub.py` replays any of them through the real provider boundary. See `docs/AI_TESTING.md`.

Run cached mode with explicitly permitted cache misses:

```dotenv
AI_MODE=cached
ALLOW_LIVE_AI=true
GROQ_API_KEY=your_real_key
```

Responses are stored under `instance/ai_cache/`. Cache keys hash task, model, language, prompt version, normalized input, and a private HMAC user partition. Keys and accounting never contain API keys or complete student inputs. Clear only the development AI cache with:

```powershell
Remove-Item -Recurse -Force .\instance\ai_cache
```

Usage accounting is written to the `ai_usage_event` table (one row per provider call, cache hit, refusal or coalesced request - this is what token budgets are checked against) and mirrored to `instance/ai_usage.jsonl`. Each logical request records a random request ID, UTC timestamp, anonymized user/session references, task, model, language, prompt version, mode, input/output/total tokens, duration, cache status, retry count, validation result, outcome, safe failure category, and cost estimate. Prompts, uploads, API keys, passwords, and provider payloads are never written. Inspect a recent sample with:

```powershell
Get-Content .\instance\ai_usage.jsonl -Tail 20 | ConvertFrom-Json | Format-Table request_id,timestamp,task_type,prompt_version,ai_mode,total_tokens,cache_status,retry_count,validation_result,error_category
```

For estimated cost, configure `AI_INPUT_COST_PER_MILLION` and `AI_OUTPUT_COST_PER_MILLION`. Central request safeguards are configurable with `AI_MAX_REQUESTS_PER_USER_HOUR`, `AI_MAX_REQUESTS_PER_USER_DAY`, `AI_MAX_LIVE_REQUESTS_DEVELOPMENT`, `AI_MAX_TOKENS_PER_SESSION`, and `AI_MAX_OUTPUT_CHARACTERS`.

Task output defaults are tutor chat 200, answer evaluation 250, translation 400, lesson generation 700, quiz generation 600, project generation 1200, exam generation 1200, OCR 1400, adaptive practice 600, and exam evaluation 600 tokens. Override one with `AI_<TASK_NAME>_MAX_OUTPUT_TOKENS`; input budgets use the corresponding `AI_<TASK_NAME>_MAX_INPUT_TOKENS`. The gateway removes duplicate whitespace/repeated long lines, uses only the bounded context supplied by features, then rejects input that still exceeds its task budget. It never silently submits a complete oversized document.

Every structured response is checked against its task schema, not merely parsed as JSON. The schemas reject missing/wrong fields, empty required text, invalid enums and scores, duplicates, incorrect counts, and unknown source page/section IDs. Invalid output is never cached or saved. Live/cached modes make at most one corrective retry; a second failure returns a translated safe error and leaves uploads/completed work intact. Mock failure fixtures fail immediately without a provider retry.

Prompt versions are defined centrally in `learnova/ai_services/prompts.py` and are part of prompts, logs, cache keys, fixture metadata, and validation reports. Increment the task version whenever its prompt contract changes so incompatible cached output cannot be reused.

The internal diagnostics route is `/internal/ai-diagnostics`. It requires login and a username or email listed in `AI_DIAGNOSTICS_ADMINS` (any environment; an empty list hides the page everywhere). It shows only aggregated/sanitized data: every configured budget with used/remaining/reset, usage by provider and by task, the routing table, cache/validation rates, latency, token/cost totals, retries, current prompt versions, and safe failure summaries. Ordinary students receive 404.

Model defaults remain configurable:

```dotenv
GROQ_VISION_MODEL=qwen/qwen3.8-27b
GROQ_TUTOR_MODEL=openai/gpt-oss-20b
GROQ_FAST_MODEL=openai/gpt-oss-20b
LESSON_TOKEN_LIMIT=1800
ANSWER_TOKEN_LIMIT=1100
CHAT_TOKEN_LIMIT=350
TRANSLATE_TOKEN_LIMIT=2500
PROJECT_TOKEN_LIMIT=5000
```

To add a fixture, create the same scenario filename under both `tests/fixtures/ai/en/` and `tests/fixtures/ai/de/`. Use a top-level supported task name and an `output_text` value; `output_text` may be a JSON object or a plain string. Fixtures must be sanitized and deterministic. Update the `_meta.prompt_versions` map in each `valid.json`. Normal tests run every valid fixture through the production schema and assert each intentional failure fixture's category.

The controlled live smoke script makes at most one provider request, uses tiny input/a 220-token cap, validates the result, and prints only request/token metadata. It is never part of pytest and refuses to run without explicit confirmation:

```powershell
$env:GROQ_API_KEY="your_real_key"
$env:ALLOW_LIVE_AI_TESTS="true"
python scripts/live_ai_smoke_test.py --task lesson --language de
```

See [cost-safe AI testing](docs/AI_TESTING.md) for cache-key, privacy, accounting, fixture, and safety details. AI output is still validated before project sections or exams are committed; invalid source references, wrong counts, and partial generated exams are rejected or rolled back.

## Exact automated test commands

The tests use temporary SQLite databases and mocked model responses. They do not use API credits or modify the local application database.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m pytest tests/test_study_planner.py -q
python -m compileall -q app.py learnova tests
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pyright.exe
npm run test:js   # requires Node, which is not installed in the current image
```

`npm run test:js` covers the browser-side helpers that cannot be exercised from
Python: the safe-UUID fallback, and the assistant's markdown renderer, whose job is
to ensure nothing a model writes can become markup.

The suite verifies:

- table initialization and additive migration of an older database;
- English/German resolution, onboarding preference, switching, refresh/logout/login/restart persistence, safe redirects, translated validation, shared desktop/mobile Settings navigation, frontend catalogue generation, and preference isolation;
- duplicate username/email rejection, password hashing, login, and logout;
- lesson, chat, translation, attempt, mastery, and restart persistence;
- cached/live safety, private cache partitioning, task schemas, prompt contracts/versioning, bounded corrective retry, quotas/token budgets, bilingual sample-response conformance, sanitized observability/diagnostics, and global blocking of unmocked network calls;
- deterministic mastery bounds, review dates, adaptive difficulty, and overdue prioritization;
- Mistake Notebook and adaptive-practice ownership isolation;
- camera permission fallback, multi-page camera payloads, combined scans/uploads, secure magic-byte validation, conservative rotation/crop processing, and no temporary-file leakage;
- structured handwriting/print/formula/diagram storage, low-confidence review, student correction, source-reference persistence, retry/recovery, page reordering, restart persistence, and cross-user image/block isolation;
- validated section planning, confirmed priority weighting, recall, section testing, and section mastery persistence;
- hidden exam answers before submission, autosave, deterministic scoring, server-side expiry, idempotent submission, result persistence, and exam/project/section user isolation.
- direct assistant chat: preset system prompts and the shared honesty standard, context-window trimming (newest turn always sent, whole messages only, oversized turns truncated visibly, never opening on an assistant turn), title derivation, conversation lifecycle, per-account isolation across every route, feature-flag removal of the whole surface, provider-prefix routing, failures stored as turns without being replayed to the model, and the conversation and message ceilings.
- community moderation: Unicode/homoglyph/encoding preprocessing, obfuscation scoring, strict classification schema, evidence grounding of safety flags, the full decision policy and its configurable thresholds, publication-bypass attempts across every public read path, publish idempotency, the oversized-submission ceiling, re-moderation on edit, stale-approval refusal and stale-state reporting, reporting with per-day ceiling and auto-hide, reviewer authorization and append-only decisions, quote retention and redaction, operational metrics carrying no content, provider-failure fail-closed behaviour, and upgrade of a database that predates the moderation migration.
- deterministic study-plan creation, countdowns, due/weak priority, incremental performance adaptation, missed-day redistribution without schedule extension, calendar generation, completion/restart persistence, English/German planner UI, multiple projects, and cross-user plan/session isolation.

## Manual smoke test

1. Register account A and create a Physics project combining a PDF, uploaded image, and two camera pages. Deny camera once to verify the upload fallback, then grant access and switch cameras if available.
2. Capture, rotate, crop, retake, delete, accept, and reorder pages. Run recognition; retry a failed page and correct one low-confidence handwritten formula before confirming.
3. Confirm that both original and processed images remain visible and teacher-highlighted content can be confirmed or rejected. Build sections, open all three explanation levels, answer a recall card, and complete Test Yourself.
4. Start a five-question exam. Refresh after entering an answer and confirm the autosaved answer remains. Submit twice and confirm only one result/review lesson exists.
5. Restart Flask, sign back into account A, and confirm recognition corrections, source blocks, project, mastery, exam result, and mistakes remain.
6. Register account B and confirm direct URLs for account A's review, original/processed images, source regions, project, section, exam, lesson, and mistake actions return 404.
7. On a phone-sized viewport, confirm camera/review controls remain usable and symbol buttons insert values such as `√()`, `²`, `π`, `×`, `≤`, and `Δ` at the cursor.

## Flashcard document import

In development and testing, private flashcard imports support PDF, JPG/JPEG, PNG, and WebP. The staged workflow at
`/flashcards/import` validates the real file signature and document structure, stores the original below the private
instance directory, extracts PDF text page by page or recognizes images through the existing Learnova OCR/vision
gateway, and requires editable source review and page selection before generation. Generated cards are transferred
to the existing creator as an unsaved server-side draft; no private or public set is created automatically.

Temporary metadata is stored in `flashcard_import` (schema migration `012_add_flashcard_imports`). Raw files default
to `instance/flashcard_imports/` and are never served from `static`. Imports expire after 24 hours by default. Run
`python -m flask --app app cleanup-flashcard-imports` from a scheduler to remove expired rows and files; opening or
creating an import also performs opportunistic cleanup.

Production keeps `FEATURE_FLASHCARD_PDF_IMPORT` and `FEATURE_FLASHCARD_IMAGE_IMPORT` off unless explicitly enabled.

### Pictures and videos in lessons

A picture is shown only when the code can point at the evidence that it is the right one.
Anything it cannot prove is dropped and the lesson simply has no picture there.

This replaced a real failure. The model was asked for "the exact title of a real
Wikipedia article" and the app ran that title through **full-text search**, took whatever
ranked first, and never compared it to what it had asked for. "Ohm's law" ranks the
biography of Georg Ohm, so students were shown an oil painting captioned as an
explanation of resistance. `learnova/media_enrichment.py` now looks the title up
(`titles=` + `redirects=1`) and refuses a page that is:

* a disambiguation page, or an article about a person — a portrait never explains a law;
* a redirect that landed on a differently named article;
* a thumbnail under 200px, a flag, a coat of arms or a logo;
* served from anything but `upload.wikimedia.org`, which is all the CSP allows. The old
  check accepted any `*.wikimedia.org`, so the browser blocked the image and the student
  saw a broken picture under a confident caption.

Every rejection is logged with its reason (`media.images subject=… dropped=Ohm's law:about_a_person`),
so how often this happens is now measurable; before, a dropped image and a network
failure were indistinguishable and neither was recorded.

**Only visual things get pictures.** Each requested image must declare a `kind` from a
closed list — diagram, map, anatomy, apparatus, artwork, artifact, graph, structure — and
a term without one is never looked up. A process, a definition, a grammar rule or a
calculation has no valid kind, so it gets nothing. This is what makes "no unnecessary
photos" a rule the app enforces rather than advice the model can ignore.

Lessons are looked up in **their own content language's Wikipedia**. French, Spanish,
Italian, Portuguese, Dutch and Arabic lessons were previously searched against English
Wikipedia, which is where most of their wrong pictures came from.

**Videos stay search links**, never an embedded pick: a search cannot be factually wrong
the way a chosen video can, and it needs no API key or quota. The query now carries the
subject and school level, and Studyflix — a German site — is offered only for German
content instead of to every language. `FEATURE_LESSON_MEDIA=false` turns all of this off.

### Exercises built on your own pages

Two question types use pictures: `photo_response` (write about a picture) and
`photo_ordering` (drag pictures into order). The pictures are diagram regions cropped out
of pages the student scanned themselves, so the match is certain — it is their own
textbook page, not something searched for.

The model is offered a numbered list of the diagrams found on the pages behind the
section it is writing about, and may refer to them by id. Anything it names that was not
on that list is discarded before a URL is built, and the route serving each crop
re-checks ownership. A photo question left without enough verified pictures falls back to
an ordinary written question rather than asking about something invisible.

`question_spec.py` normally rejects a prompt that says "refer to the image", because such
a question is unanswerable. That rule is relaxed for exactly these types and only when
verified pictures are attached. Photo ordering is graded by comparing the sequence, not
by asking the AI.

### Mathematical notation

AI output contains LaTeX — the tutor prompts ask for it in mathematics and physics, and
imported or scanned material carries it too. Students must never see raw `$x^2$`, so
formula rendering is a property of the application rather than a per-page feature:
`templates/base.html` loads `static/js/math.js` on every page, and it typesets the whole
`<main>` area once the page is ready. JavaScript that builds content later calls
`renderMath(element)` after setting it. There used to be four copies of that helper and
five templates each fetching KaTeX themselves; a page nobody remembered to wire up simply
showed the raw source.

Inline `$…$` and `\(…\)`, block `$$…$$` and `\[…\]` are all supported, covering
fractions, exponents, subscripts, roots, Greek letters, matrices, sums, integrals and
relations.

Three properties worth knowing:

* **Prose containing `$` is left alone.** "Between $5 and $10" is not the formula
  "5 and ". `static/js/math-rules.js` holds the decision logic, separately from the DOM
  work, so it can be tested: a span is only typeset when it has no whitespace against its
  delimiters, stays on one line, is under 200 characters, and contains a variable,
  command, script or relation. When a span is ambiguous it stays text — unrendered LaTeX
  is cosmetic, mangled prose is not. `\$` is always a literal dollar.
* **The source is preserved.** Rendering only ever changes display. Textareas, inputs and
  `contenteditable` regions are skipped by definition, so the LaTeX a student is editing
  is never rewritten underneath them, and every rendered formula keeps its source in
  `data-ln-math`. Add `data-no-math` to opt a subtree out.
* **KaTeX is fetched lazily**, the first time a page turns out to contain a formula, so
  pages without mathematics pay nothing for it. If it cannot be reached the original
  source text stays on screen. Formulas inherit their colour, so dark mode needs no rule
  of its own, and display math scrolls rather than overflowing on a phone.

### The flashcard editor

The editor is built around one path: title → term → answer → add card → save. Only the
title is on screen when it opens. Everything else is one level down, so the default view
stays quiet:

* **Set details** (description, subject, topic, grade, difficulty, tags) sit behind a
  single "Description, subject and tags" disclosure.
* **AI generation** is three quiet buttons — paste text, enter a topic, import PDF or
  photos. Picking one reveals just that input; the count, card type, difficulty and
  content language moved behind an "Options" disclosure inside it.
* **A card row** shows a number, the two fields, an ✨ AI button and one `⋯` menu. Move
  up, move down, regenerate, duplicate and delete all live in that menu instead of a
  column of icons, and per-card explanation, hint, difficulty and tags stay in the
  existing "More options" disclosure.
* **The fields are underlines, not boxes**, and start one line tall, growing with what is
  written. A one-word term no longer occupies a fixed five-line box.
* An empty card always waits at the bottom, so adding the next one is typing rather than
  scrolling to a button.

Nothing was removed except `#metaVisibility`, a `<select>` that offered exactly one
choice. `tests/test_creator_ux.py` pins the arrangement: what is visible before anything
is opened, and that every advanced control is still reachable one level down.

### One-word card creation

Typing two long fields per card is the main reason a set never gets finished on a phone.
In the creator, leaving the term field asks `POST /api/flashcards/suggest-back` for two or
three candidate definitions in different styles (short, detailed, worked example); the
student taps one or ignores them and types their own. Nothing is ever written into a card
without a tap, and the definition stays fully editable afterwards.

The task is `flashcard_back_suggestion` on the shared AI gateway, answered by
`GROQ_FAST_MODEL` within `FLASHCARD_SUGGESTION_TOKEN_LIMIT` (400) tokens, rate limited to
30 requests per minute and validated by `learnova.flashcards.service.normalize_suggestions`,
which caps the list at three, de-duplicates it and never trusts the model's style label.

Because a student's AI budget is shared with tutor chat and lesson generation, the browser
caches suggestions per term in `localStorage`, asks for at most one at a time, and skips
the request entirely when the definition field already has text. If the budget runs out the
front-end stops asking automatically for the rest of the session and falls back to the
manual ✨ button, so card-making can never be the reason another feature stops working.
Students can switch the automatic behaviour off in the creator.

Private flashcards are not moderated, so suggestions are visible only to their author;
publishing to the community library still goes through the full moderation pipeline.

### Flashcard learning modes and gamification

The private flashcard workspace includes persistent Flashcards, Learn, Test, Match, Blast, and Blocks sessions.
Answers update spaced-repetition and mastery data on the server; completed work feeds the shared XP, levels,
streaks, daily goals, missions, badges, personal bests, dashboard summary, and `/progress` history. Production
keeps the individual `FEATURE_FLASHCARD_*_MODE` / `FEATURE_FLASHCARD_*_GAME` flags and `FEATURE_GAMIFICATION`,
`FEATURE_MISSIONS`, `FEATURE_BADGES`, and `FEATURE_DAILY_GOALS` off unless explicitly enabled. See `.env.example`
for the complete flag list. `GAMIFICATION_MIN_DAILY_EVENTS` controls how many meaningful events qualify a day
for streak credit.

### Vocabulary Trainer

The import page at `/vocabulary/import` asks one question first — *how do you want to add
words?* — as three tappable cards (photo or PDF, paste a list, type them yourself), then
shows only the input that choice needs, then the language pair and an optional title. The
cards are still the `source_kind` radios the server and `static/js/vocabulary.js` read; the
dropzone still wraps the real `<input type="file">`, now invisible, so a tap anywhere on it
opens the picker and the script writes the chosen filename into the zone. Before this the
page rendered its controls with browser defaults: `.field` and `.field-row` live in
`flashcards.css`, which this page never loads, so labels sat inline against unpadded
selects, the method chooser was a bare `<fieldset>`, and the dropzone showed a raw
"Choose file" button. `static/css/vocabulary.css` now styles its own controls from the
theme tokens, so the page follows dark mode, and `tests/test_vocabulary_import_ui.py`
pins both the arrangement and every hook the script depends on.

`/vocabulary` provides private photo/PDF/text/manual vocabulary imports using the same validated upload,
OCR, retention, cleanup, and ownership system as flashcard imports. Students select source and target
languages independently of the interface language, review structured word pairs and example sentences,
accept or reject visible corrections, then open selected card variants in the existing compact flashcard
editor. Dedicated practice persists mastery separately for each translation direction and contributes
idempotent XP, streak, mission, badge, dashboard, and progress activity. Initially supported vocabulary
languages are English, German, French, and Spanish. Production requires
`FEATURE_VOCABULARY_TRAINER=true`; the flag defaults on outside production.
Limits and retention are configurable through the `MAX_FLASHCARD_*`, `FLASHCARD_IMPORT_*`, and
`MAX_FLASHCARD_IMPORTS_PER_HOUR` environment variables documented in `.env.example`.

### Exam autopilot (scan → exam date → start)

Give a project an exam date — on the upload form or the one-field form on the project
page — and Learnova plans the preparation itself: it reads the **competencies** the exam
requires from the Kompetenzraster (or derives them from the material), checks which ones
the notes actually cover (every "covered" must quote the notes and the quote is verified),
builds the day-by-day schedule, and from then on decides the **next optimal action**:
teach the first topic that isn't known, practise it until the knowledge gate says it is,
revisit older weak material when enough reviews are due, and in the final days switch to
retrieval and a mock exam. The card on the project page and the Overview shows *Today*,
*Progress*, *Estimated grade (1–6, a range that widens with thin evidence, with the
reasons it moved)*, *Weakness*, *Next*, and one **Start** button. Two diagnosis types were
added for the loop — wording/vocabulary and application/transfer — and after a mistake the
confirming question must transfer the idea to a new situation. Details and limits:
[docs/EXAM_AUTOPILOT.md](docs/EXAM_AUTOPILOT.md).

### Knowledge-gated tests (3–15 questions, stop at 80% knowledge)

A test no longer has a fixed length. Every test that runs through `/api/answer` — the New
Lesson test, a project section test, Today's Practice, "Practice weakest", "Similar
question" and the exam's "Close the gaps" — asks between `TEST_MIN_QUESTIONS` (3) and
`TEST_MAX_QUESTIONS` (15) questions and stops as soon as the student **knows** every
concept the test is about. "Knows" is defined in `learnova/quizzes/mastery_gate.py`: an
evidence-weighted knowledge estimate of at least `KNOWLEDGE_TARGET` (80), backed by at
least `MASTERY_EVIDENCE_FLOOR` worth of evidence (so one lucky answer cannot do it) and
confirmed on a question above the easiest level. The estimate blends the student's prior
(the stored mastery score, weighted by its decayed evidence and capped at three answers'
worth) with every answer in the sitting, each weighted by the same
`knowledge.observation_weight` the knowledge model records — hinted, unverified or
undiagnosable answers count for less here too. It is deliberately **not** the long-term
mastery score, which moves ±12 a step by design and would need seven correct answers for
a fresh concept.

After each answer the gate decides: stop (`target_reached`, or `max_questions` with the
concepts still below target named and routed to Today's Practice) or continue, and which
concept comes next — stay on a concept just answered wrong while the planner re-teaches it
(at most four in a row), then untested concepts in lesson order, then the weakest open
one. The lesson path grades and writes the next question in one model call, so the model
is told both targets (`next_target.if_correct` / `.if_wrong`); if it spends the question on
a concept that is already known while another is open, the question is regenerated. The
end-of-test summary is deterministic (knowledge per concept, why it stopped, what next);
the model's summary is kept only when the maximum was reached. The browser shows a
knowledge bar and concept chips instead of "Question 3 of 5".

**Final exams** keep their chosen length but are judged the same way: at submission the
lowest-scoring wrong open answers (`EXAM_DIAGNOSIS_LIMIT`, default 3 — each is a model
call while the student waits) get the full diagnosis, every answer feeds the evidence
model, and the results page shows a **Knowledge check** per concept with a **Close the
gaps** button that starts a gated practice test on the concepts still below target.
Tests: `tests/test_mastery_gate.py` (rules), `tests/test_knowledge_gate_integration.py`
(the loop, planned practice and exams with the model patched).

### Beginner tour

A new account's first visit is an interactive walkthrough, not a slideshow: everything
dims except the one thing to tap, a short line says what to do ("Tap New Lesson. This is
where every lesson starts."), and the tour moves on only when the student actually does
it. It starts wherever registration lands (the New Lesson page), leads through picking a
subject, saying what to learn (or tapping a "Try asking" idea), building the lesson,
starting the test and finding the tutor chat, then across to the Overview, and ends with
where the rest lives (Menu → More). On a phone, a link hidden behind the menu button lights
the menu button first. The run's position lives in `sessionStorage` so it survives the
page changes; whether the account has finished or skipped it is stored on the server
(`User.tour_completed_at`, `POST /api/tour/complete`), so it never opens by itself twice
on any device. It can be restarted from **Account → Tutorial**, the mobile menu, or the
"New here?" link on the Overview. Steps and the pure routing rules: `static/js/tour-rules.js`;
pointing and listening: `static/js/tour.js`; shell: `templates/components/_tour.html`;
tests: `tests/test_tour.py`, `tests/js/tour-rules.test.mjs`.

### Direct assistant chat

`/assistant` is a durable, general-purpose conversation with the model — no lesson, no
upload, no session to expire. Conversations persist, are searchable and archivable, and
each one carries a **style preset** that decides what the assistant is told to be:
general, research (separates evidence from inference from what is still open), study coach
(gives the next step, not the solution), or plain explanation. A "Think harder" toggle
sends the turn to the stronger model.

Models are named `provider:model`, so pointing any task at another provider is one
environment variable. Groq, OpenAI, Anthropic and Gemini are registered; only Groq has been
exercised live from this codebase. Routing, token budgets, provider caps and fallback are
described in `docs/AI_ROUTING.md`; `docs/ASSISTANT.md` has the
exact steps and a worked adapter for adding Anthropic. Everything still goes through the
one AI gateway, so caching, token budgets, usage limits, sanitized errors and telemetry
apply to every provider.

Replies are not streamed in this release; `docs/ASSISTANT.md` explains why and what it
would take.

### Community moderation

Anything published to the community library passes a context-aware safety gate before the
existing quality review runs. It judges thirteen independent dimensions and keeps safety
separate from subject relevance, so reproductive biology in a biology set is published
while the same vocabulary attached to a mathematics card asks for a revision, and
solicitation is rejected. Every safety flag has to quote text that actually appears in the
submission; one that cannot is discarded and the item goes to a human instead. Obfuscation,
prompt injection, low confidence and conflicting signals all escalate and never reject on
their own, and nothing is publicly visible until both gates agree.

Readers can report published content, two safety reports auto-hide a set pending review,
and reviewers on the `COMMUNITY_MODERATORS` allowlist work the queue at
`/internal/moderation`. Full design, thresholds and known limitations are in
`docs/COMMUNITY_MODERATION.md`.

```powershell
python scripts/run_moderation_eval.py            # replay 46 labelled cases, no API cost
python scripts/run_moderation_eval.py --usage    # add real latency/cost from telemetry
flask redact-moderation-quotes                   # apply the quote retention window
```

Live decision metrics — the decision mix, escalation rate, latency and cost — appear on
the protected `/internal/ai-diagnostics` page.

## Current boundaries

- Recognition is deliberately not presented as perfect handwriting recognition. Low-confidence words, formulas, and regions remain visibly marked until the student verifies them.
- Image cleanup is conservative and uses automatic orientation plus manual crop/90° rotation. It avoids aggressive transformations that could erase handwriting or mathematical marks.
- Recognition and project generation require the configured AI service. Originals, processed images, and every successfully recognized page remain saved if another page or later generation step fails.
- Processing is in-memory; Learnova does not create temporary image files. Production retention and deletion policy still needs to be defined before a broad student rollout.
- This release intentionally does not include payments, leaderboards, parent accounts, voice tutoring, or social features.
- Community moderation depends on the classifier being right: plainly written abuse that the model calls clean trips no deterministic detector and is published. Reader reporting is the compensating control, and the evaluation corpus measures this as a separate `classifier_miss_rate` rather than folding it into the policy's own numbers.
- Moderation thresholds are calibrated against 46 synthetic cases, not production traffic, and an escalation queue only helps if somebody works it.

## Render deployment

This repository includes `render.yaml`.

1. Push the repository to GitHub.
2. In Render choose **New → Blueprint** and connect the repository.
3. Set `GROQ_API_KEY` when prompted.
4. Deploy and open the generated `onrender.com` URL.

Render installs `requirements.txt`, connects PostgreSQL, and starts Gunicorn on `0.0.0.0:$PORT`. The health check is `/health`. Use PostgreSQL in production because Render web-service filesystems are ephemeral. Use HTTPS and define appropriate student-data retention, consent, and backup policies before a wider launch.

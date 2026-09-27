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

1. Open **Study projects** and choose **Upload PDF**, **Upload Images**, and/or **Scan with Camera**. Camera pages and uploads can be combined in one ordered project. A project supports up to 20 pages, 15 MB per file, and 40 MB per request; 10–15 pages is recommended.
2. For camera capture, explicitly open the scanner, position one full page in the frame, capture, review, crop/rotate/retake, accept it, and continue. Front/rear cameras can be switched when the browser exposes both. Closing the scanner stops every camera track.
3. Learnova stores the original and a lossless processed recognition copy. Processing applies EXIF orientation, student-selected crop/rotation, conservative brightness/contrast/sharpness correction, resolution checks, and blur/glare warnings. PDFs—including scanned PDFs—are rendered page by page.
4. Run recognition. Each page is saved independently as typed blocks: printed text, handwriting, formulas, tables, diagrams, headings, annotations, uncertain content, and crossed-out content. Blocks retain confidence, bounding boxes, source file/page IDs, and review state.
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

Usage accounting is appended to `instance/ai_usage.jsonl`. Each logical request records a random request ID, UTC timestamp, anonymized user/session references, task, model, language, prompt version, mode, input/output/total tokens, duration, cache status, retry count, validation result, outcome, safe failure category, and cost estimate. Prompts, uploads, API keys, passwords, and provider payloads are never written. Inspect a recent sample with:

```powershell
Get-Content .\instance\ai_usage.jsonl -Tail 20 | ConvertFrom-Json | Format-Table request_id,timestamp,task_type,prompt_version,ai_mode,total_tokens,cache_status,retry_count,validation_result,error_category
```

For estimated cost, configure `AI_INPUT_COST_PER_MILLION` and `AI_OUTPUT_COST_PER_MILLION`. Central request safeguards are configurable with `AI_MAX_REQUESTS_PER_USER_HOUR`, `AI_MAX_REQUESTS_PER_USER_DAY`, `AI_MAX_LIVE_REQUESTS_DEVELOPMENT`, `AI_MAX_TOKENS_PER_SESSION`, and `AI_MAX_OUTPUT_CHARACTERS`.

Task output defaults are tutor chat 200, answer evaluation 250, translation 400, lesson generation 700, quiz generation 600, project generation 1200, exam generation 1200, OCR 1400, adaptive practice 600, and exam evaluation 600 tokens. Override one with `AI_<TASK_NAME>_MAX_OUTPUT_TOKENS`; input budgets use the corresponding `AI_<TASK_NAME>_MAX_INPUT_TOKENS`. The gateway removes duplicate whitespace/repeated long lines, uses only the bounded context supplied by features, then rejects input that still exceeds its task budget. It never silently submits a complete oversized document.

Every structured response is checked against its task schema, not merely parsed as JSON. The schemas reject missing/wrong fields, empty required text, invalid enums and scores, duplicates, incorrect counts, and unknown source page/section IDs. Invalid output is never cached or saved. Live/cached modes make at most one corrective retry; a second failure returns a translated safe error and leaves uploads/completed work intact. Mock failure fixtures fail immediately without a provider retry.

Prompt versions are defined centrally in `learnova/ai_services/prompts.py` and are part of prompts, logs, cache keys, fixture metadata, and validation reports. Increment the task version whenever its prompt contract changes so incompatible cached output cannot be reused.

In development, the internal diagnostics route is `/internal/ai-diagnostics`. It requires login, `APP_ENV=development`, and a username or email listed in `AI_DIAGNOSTICS_ADMINS`. It shows only aggregated/sanitized request counts, modes, cache/validation rates, latency, token/cost totals, retries, current prompt versions, and safe failure summaries. Ordinary students receive 404.

Model defaults remain configurable:

```dotenv
GROQ_VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
GROQ_TUTOR_MODEL=openai/gpt-oss-20b
GROQ_FAST_MODEL=llama-3.1-8b-instant
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

### Flashcard learning modes and gamification

The private flashcard workspace includes persistent Flashcards, Learn, Test, Match, Blast, and Blocks sessions.
Answers update spaced-repetition and mastery data on the server; completed work feeds the shared XP, levels,
streaks, daily goals, missions, badges, personal bests, dashboard summary, and `/progress` history. Production
keeps the individual `FEATURE_FLASHCARD_*_MODE` / `FEATURE_FLASHCARD_*_GAME` flags and `FEATURE_GAMIFICATION`,
`FEATURE_MISSIONS`, `FEATURE_BADGES`, and `FEATURE_DAILY_GOALS` off unless explicitly enabled. See `.env.example`
for the complete flag list. `GAMIFICATION_MIN_DAILY_EVENTS` controls how many meaningful events qualify a day
for streak credit.

### Vocabulary Trainer

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

### Direct assistant chat

`/assistant` is a durable, general-purpose conversation with the model — no lesson, no
upload, no session to expire. Conversations persist, are searchable and archivable, and
each one carries a **style preset** that decides what the assistant is told to be:
general, research (separates evidence from inference from what is still open), study coach
(gives the next step, not the solution), or plain explanation. A "Think harder" toggle
sends the turn to the stronger model.

Models are named `provider:model`, so pointing the assistant at another provider is one
environment variable. Groq and OpenAI are registered today; `docs/ASSISTANT.md` has the
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

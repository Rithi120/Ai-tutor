# Learnova — source-grounded AI study tutor

Learnova helps a student prepare for school exams from their own material. A student can upload PDFs/images or scan pages with a phone or laptop camera, verify structured print/handwriting recognition, review an editable learning plan, learn each focused section, and take a source-grounded timed final exam.

## Architecture

Learnova is organized into domain packages for authentication, uploads, OCR, AI services, projects, lessons, quizzes, exams, dashboard, settings, translations, web security, and utilities. Flask routes retain their existing URLs while delegating validation, ingestion, deterministic learning rules, AI transport, and optimized dashboard queries to services. Shared page chrome lives in `templates/components`; frontend tokens and behavior live in reusable `static/css` and `static/js` modules.

See [architecture and module responsibilities](docs/ARCHITECTURE.md) and the [extension guide](docs/EXTENDING.md).

The earlier quick lesson, adaptive quiz, translation, tutor chat, dashboard, spaced repetition, and Mistake Notebook remain available. Accounts, uploads, extracted text, attempts, mastery, exam answers, and results are isolated by user and stored in the database.

## Languages and translation workflow

Learnova supports English (`en`) and German (`de`). **Settings → Language** is the only language control. For a signed-in student, `User.preferred_language` is authoritative and survives navigation, refreshes, logout/login, server restarts, lessons, quizzes, practice, and exams. For a visitor, the Flask session is used; on the first visit only, the browser language is considered. English is the final fallback.

The resolver order is:

1. signed-in user's `preferred_language`;
2. `session["language"]`;
3. supported browser language on the first visit;
4. English.

Interface messages and the browser-side dictionary live in the single source catalogue [i18n.py](i18n.py). Templates use `_()` and JavaScript reads `window.LEARNOVA_I18N`, which the shared base template creates for the active request. Learning-content language is separate from interface rendering: every tutor, lesson, adaptive-practice, answer-feedback, chat, section, and exam generation request receives an explicit English or German instruction.

This project uses a checked-in Python catalogue rather than gettext `.po`/`.mo` binaries, so extraction and compilation are intentionally no-op steps:

```powershell
# Extraction: not required; English message IDs and German translations are in i18n.py.
# Compilation: not required; the catalogue is imported directly at application startup.
python -m py_compile i18n.py
python -m unittest tests.test_i18n -v
```

To add another language later:

1. add its code to `SUPPORTED_LANGUAGES` in `i18n.py`;
2. add a complete translation mapping and make `translate()` select it;
3. add the option to `templates/settings.html` and onboarding;
4. extend `language_instruction()` with an explicit AI response instruction;
5. copy the persistence, page, JavaScript-catalogue, validation, and user-isolation tests in `tests/test_i18n.py`.

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

Mock AI is the safe development default, so a Groq key is not required for local setup. Edit `.env` and set at least:

```dotenv
APP_ENV=development
AI_MODE=mock
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

## AI and cost configuration

Every lesson, quiz, answer evaluation, tutor chat, translation, OCR/recognition, project section, adaptive-practice, and final-exam request passes through `learnova.ai_services.service`. No route or other domain service creates a Groq client.

The three modes are:

- `AI_MODE=mock`: reads deterministic English/German fixtures and never calls the network. This is the default in development and tests.
- `AI_MODE=cached`: returns an identical request's saved response; a miss may call Groq and therefore requires `ALLOW_LIVE_AI=true` in development.
- `AI_MODE=live`: always calls Groq. Development requires `ALLOW_LIVE_AI=true`; production refuses to start unless `AI_MODE=live` is explicitly configured.

Run all features without tokens:

```dotenv
APP_ENV=development
AI_MODE=mock
AI_MOCK_SCENARIO=valid
AI_MOCK_LATENCY_MS=0
```

Use `AI_MOCK_SCENARIO=malformed_json`, `empty_response`, `missing_required_fields`, `wrong_types`, `timeout`, `rate_limit`, `duplicate_questions`, `duplicate_question_ids`, `invalid_source_references`, `incorrect_exam_question_count`, `invalid_scores`, `unsupported_difficulty`, or `oversized_output` to exercise failure handling. Set `AI_MOCK_LATENCY_MS` from `0` to `5000` to simulate latency without tokens.

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
```

The suite verifies:

- table initialization and additive migration of an older database;
- English/German resolution, onboarding preference, switching, refresh/logout/login/restart persistence, safe redirects, translated validation, shared desktop/mobile Settings navigation, frontend catalogue generation, and preference isolation;
- duplicate username/email rejection, password hashing, login, and logout;
- lesson, chat, translation, attempt, mastery, and restart persistence;
- mock/cached/live safety, private cache partitioning, task schemas, prompt contracts/versioning, bounded corrective retry, quotas/token budgets, deterministic bilingual fixtures, sanitized observability/diagnostics, and global blocking of unmocked network calls;
- deterministic mastery bounds, review dates, adaptive difficulty, and overdue prioritization;
- Mistake Notebook and adaptive-practice ownership isolation;
- camera permission fallback, multi-page camera payloads, combined scans/uploads, secure magic-byte validation, conservative rotation/crop processing, and no temporary-file leakage;
- structured handwriting/print/formula/diagram storage, low-confidence review, student correction, source-reference persistence, retry/recovery, page reordering, restart persistence, and cross-user image/block isolation;
- validated section planning, confirmed priority weighting, recall, section testing, and section mastery persistence;
- hidden exam answers before submission, autosave, deterministic scoring, server-side expiry, idempotent submission, result persistence, and exam/project/section user isolation.
- deterministic study-plan creation, countdowns, due/weak priority, incremental performance adaptation, missed-day redistribution without schedule extension, calendar generation, completion/restart persistence, English/German planner UI, multiple projects, and cross-user plan/session isolation.

## Manual smoke test

1. Register account A and create a Physics project combining a PDF, uploaded image, and two camera pages. Deny camera once to verify the upload fallback, then grant access and switch cameras if available.
2. Capture, rotate, crop, retake, delete, accept, and reorder pages. Run recognition; retry a failed page and correct one low-confidence handwritten formula before confirming.
3. Confirm that both original and processed images remain visible and teacher-highlighted content can be confirmed or rejected. Build sections, open all three explanation levels, answer a recall card, and complete Test Yourself.
4. Start a five-question exam. Refresh after entering an answer and confirm the autosaved answer remains. Submit twice and confirm only one result/review lesson exists.
5. Restart Flask, sign back into account A, and confirm recognition corrections, source blocks, project, mastery, exam result, and mistakes remain.
6. Register account B and confirm direct URLs for account A's review, original/processed images, source regions, project, section, exam, lesson, and mistake actions return 404.
7. On a phone-sized viewport, confirm camera/review controls remain usable and symbol buttons insert values such as `√()`, `²`, `π`, `×`, `≤`, and `Δ` at the cursor.

## Current boundaries

- Recognition is deliberately not presented as perfect handwriting recognition. Low-confidence words, formulas, and regions remain visibly marked until the student verifies them.
- Image cleanup is conservative and uses automatic orientation plus manual crop/90° rotation. It avoids aggressive transformations that could erase handwriting or mathematical marks.
- Recognition and project generation require the configured AI service. Originals, processed images, and every successfully recognized page remain saved if another page or later generation step fails.
- Processing is in-memory; Learnova does not create temporary image files. Production retention and deletion policy still needs to be defined before a broad student rollout.
- This release intentionally does not include payments, leaderboards, parent accounts, voice tutoring, or social features.

## Render deployment

This repository includes `render.yaml`.

1. Push the repository to GitHub.
2. In Render choose **New → Blueprint** and connect the repository.
3. Set `GROQ_API_KEY` when prompted.
4. Deploy and open the generated `onrender.com` URL.

Render installs `requirements.txt`, connects PostgreSQL, and starts Gunicorn on `0.0.0.0:$PORT`. The health check is `/health`. Use PostgreSQL in production because Render web-service filesystems are ephemeral. Use HTTPS and define appropriate student-data retention, consent, and backup policies before a wider launch.

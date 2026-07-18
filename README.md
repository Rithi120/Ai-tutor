# Learnova — source-grounded AI study tutor

Learnova helps a student prepare for school exams from their own material. A student can upload PDFs/images or scan pages with a phone or laptop camera, verify structured print/handwriting recognition, review an editable learning plan, learn each focused section, and take a source-grounded timed final exam.

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

Edit `.env` and set at least:

```dotenv
GROQ_API_KEY=your_real_key
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

`python -m flask --app app init-db` is idempotent. It creates every missing table and applies versioned, additive upgrades recorded in `schema_migration`; it does not erase saved progress. Startup runs the same initialization automatically.

The current schema includes users, lessons, attempts, mastery, persisted study sessions, chat messages, learning projects, private source files/pages, structured document blocks, learning sections, recall cards, final exams, exam questions, and autosaved exam answers. `006_link_lessons_to_sections` connects section tests to attempts; `007_add_document_recognition` adds original/processed recognition metadata, confidence, review state, priority, rotation, and recovery fields; `008_add_user_preferred_language` adds the indexed `en`/`de` account preference and safely defaults existing users to English. Back up production data before a deployment.

For PostgreSQL, set `DATABASE_URL` to a valid SQLAlchemy `postgresql://` URL. Render supplies this automatically through `render.yaml`.

## AI and cost configuration

The defaults route short chat/translation work to the fast model, lesson/answer/project work to the tutor model, and image extraction to the vision model:

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

The server logs token usage. Keep limits conservative during tester rollout. AI output is validated before project sections or exams are committed: section/page ownership, source references, exact exam count, proportional section allocation, and difficulty distribution must all match. Partial generated exams are rolled back.

## Exact automated test commands

The tests use temporary SQLite databases and mocked model responses. They do not use API credits or modify the local application database.

```powershell
& .\.venv\Scripts\Activate.ps1
python -m pip install ruff pyright
python -m unittest discover -s tests -v
python -m py_compile app.py i18n.py adaptive_learning.py study_projects.py document_processing.py tests\test_app.py tests\test_i18n.py tests\test_adaptive_learning.py tests\test_study_projects.py tests\test_document_processing.py
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pyright.exe
```

The suite verifies:

- table initialization and additive migration of an older database;
- English/German resolution, onboarding preference, switching, refresh/logout/login/restart persistence, safe redirects, translated validation, shared desktop/mobile Settings navigation, frontend catalogue generation, and preference isolation;
- duplicate username/email rejection, password hashing, login, and logout;
- lesson, chat, translation, attempt, mastery, and restart persistence;
- deterministic mastery bounds, review dates, adaptive difficulty, and overdue prioritization;
- Mistake Notebook and adaptive-practice ownership isolation;
- camera permission fallback, multi-page camera payloads, combined scans/uploads, secure magic-byte validation, conservative rotation/crop processing, and no temporary-file leakage;
- structured handwriting/print/formula/diagram storage, low-confidence review, student correction, source-reference persistence, retry/recovery, page reordering, restart persistence, and cross-user image/block isolation;
- validated section planning, confirmed priority weighting, recall, section testing, and section mastery persistence;
- hidden exam answers before submission, autosave, deterministic scoring, server-side expiry, idempotent submission, result persistence, and exam/project/section user isolation.

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

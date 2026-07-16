# Learnova — persistent AI tutor

Learnova turns a study goal and optional photos into a personalized lesson. It preserves upload, adaptive quiz, lesson-aware chat, and translation features while saving each student's lessons, attempts, and concept mastery.

The English/German dashboard lists saved lessons, attempts, mistakes, and chat history. Each adaptive study session is stored in SQLite and can be resumed from the dashboard after restarting the application.

## Low-cost hybrid model routing

The default configuration is designed for a small user test:

- `llama-3.1-8b-instant` handles short tutor chats and translations.
- `openai/gpt-oss-20b` handles lesson generation, answer grading, and weak-point practice with low reasoning effort.
- `meta-llama/llama-4-scout-17b-16e-instruct` is used only when image understanding is required.

Hard output limits are controlled by `LESSON_TOKEN_LIMIT`, `ANSWER_TOKEN_LIMIT`, `CHAT_TOKEN_LIMIT`, and `TRANSLATE_TOKEN_LIMIT`. The server logs exact input, output, and total token counts for every model call. Keep these defaults during testing and review the `AI usage` log entries before increasing a limit or selecting a larger model.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Add `GROQ_API_KEY` and a long, random `SECRET_KEY` to `.env`. Initialize the SQLite database, then start the app:

```powershell
python -m flask --app app init-db
python app.py
```

Open `http://127.0.0.1:5000`. By default SQLite is stored at `instance/numeri.db`. Set `DATABASE_URL` to use another SQLAlchemy database URL. The initialization command is safe to run again: it creates missing tables without deleting progress.

Missing tables are also created automatically when the application starts, so a fresh local installation can register its first user immediately. The explicit `init-db` command remains useful for deployment setup.

## Current limits

- Active adaptive lesson and chat state remains in memory; lessons, attempts, and mastery are persistent.
- Up to four JPG, PNG, or WebP images can be included in a lesson.
- Uploaded images are sent to the configured Groq vision model for analysis.

For production, use HTTPS and a production WSGI server, add database migrations and rate limiting, and define appropriate student-data retention and consent policies.

## Deploy to Render

This repository includes `render.yaml` for a Free Render web service and Free Render Postgres database.

1. Push the repository to GitHub.
2. In Render, select **New → Blueprint** and connect the repository.
3. Render reads `render.yaml` and creates `learnova` plus `learnova-db`.
4. When prompted for `GROQ_API_KEY`, paste the key as a secret environment variable.
5. Deploy, then open the generated `onrender.com` URL.

The Blueprint generates `SECRET_KEY`, connects `DATABASE_URL`, runs Gunicorn, and exposes `/health`. Do not use SQLite on Render's Free web service because its filesystem is temporary. Free Render Postgres is appropriate for the short tester phase but currently expires after 30 days; move to a longer-lived Postgres provider or paid database before that deadline if progress must remain available.

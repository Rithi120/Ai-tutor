# Operations

Notes for whoever deploys and runs Learnova: what to do before a deploy, which settings to
change from their defaults and why, how long requests behave behind gunicorn and a reverse
proxy, what has and has not been verified, how to roll back, and which log lines to watch.
Read it before you deploy, change the start command or the reverse proxy, or change the
number of workers.

`render.yaml` is the owner's file. Nothing here changes it. Where this page recommends a
value that differs from what is deployed, the owner sets it.

1. [Before every deploy](#before-every-deploy)
2. [The PostgreSQL migration test](#the-postgresql-migration-test)
3. [What this release migrates (037-041)](#what-this-release-migrates-037-041)
4. [Configuration recommendations](#configuration-recommendations)
5. [Long requests, gunicorn and the reverse proxy](#long-requests-gunicorn-and-the-reverse-proxy)
6. [What was verified, and where](#what-was-verified-and-where)
7. [Rolling back](#rolling-back)
8. [Monitoring](#monitoring)

## Before every deploy

1. **Back up the production database.** For PostgreSQL:

   ```bash
   pg_dump --format=custom --no-owner --file "learnova-$(date +%Y%m%d-%H%M).dump" "$DATABASE_URL"
   pg_restore --list learnova-<stamp>.dump | head   # the dump opens and lists its tables
   ```

   Keep the dump outside the repository and off the web host. The migrations of this
   release only add columns, indexes and tables (038 may unlink a duplicate key-terms set,
   040 fills two of its new columns), but a backup is the only way back from damaged data.
2. **Find out what production runs, read-only.**

   ```bash
   psql "$DATABASE_URL" -c "SELECT version FROM schema_migration ORDER BY version;"
   psql "$DATABASE_URL" -c "SELECT version();"   # the PostgreSQL major version, for step 3
   ```

   | Newest row | Production runs | This deploy applies | Test with |
   |---|---|---|---|
   | `036_unified_project_mode` | `8f924ac` (`origin/main`, PR #10) | 037-041 | `--old-rev 8f924ac` |
   | `030_curriculum` | `9493c7a` or older | 031-041 | `--old-rev 9493c7a` |
   | some of `037`-`041` | a build of this branch | the missing ones | both |

   `render.yaml` deploys on every commit (`autoDeployTrigger: commit`), so production may
   already run `8f924ac`. Nothing in this repository can see which.
3. **Run the [PostgreSQL migration test](#the-postgresql-migration-test)** on a throw-away
   server of the same major version. Every step must pass. Any failure blocks the deploy.
4. **Check the settings** in [Configuration recommendations](#configuration-recommendations):
   above all the token pins in `render.yaml`, the production Groq tier, `PROXY_FIX_X_FOR`
   and email.
5. **Deploy at a quiet time.** The first process that starts runs the migrations, under a
   PostgreSQL advisory lock ([below](#one-migrating-process-the-advisory-lock-and-the-lock-timeout)).
   An `ALTER TABLE` must wait for every open transaction on its table. If the old instance is
   still inside a long request, the booting process gives up after
   `DB_MIGRATION_LOCK_TIMEOUT_MS` (10 s): PostgreSQL rolls that migration back, the boot fails
   with "canceling statement due to lock timeout", and nothing is half-applied. Start it again
   when it is quieter.
6. **Watch the first start**, then `GET /health`, then the log lines under
   [Monitoring](#monitoring). A later start with nothing to migrate executes no DDL at all.
7. **Smoke test** with a test account:
   - create a project from six files: a PDF, photos, a PDF with a blank back page, and one
     file that is not a real PDF (a text file renamed to `.pdf`). Six document rows; the bad
     file has its own row, names the file and offers Replace file and Remove file;
   - the start page reads the pages, works out the topics in steps ("part x of y") and opens
     the topic list with every topic ticked;
   - Start learning: the lesson explains before it asks and shows key terms and flashcards;
   - answer one question wrong: "Try again" appears without the solution;
   - open the project flashcard set and the plan calendar, and take a practice exam;
   - in the chat, send one message with a formula and one with a photo.

Project Mode's own notes on deploying it are in [PROJECT_MODE.md](PROJECT_MODE.md#deployment-and-rollback).

## The PostgreSQL migration test

The test is **not in the repository.** It lives on the development machine in
`R:/ai_tutor_suite_tmp/prod_pg/`, next to the repository: `run_pg_migration_test.py`,
`pg_compile_metadata.py` and the `pgmig/` folder. Copy the whole folder. Its own
`README.md` has the details.

It has been run end to end on **SQLite only**, never against PostgreSQL: no PostgreSQL
server was available. On SQLite with foreign keys enforced it passes 22 of 23 steps for both
baselines (review A, 2026-11-02). The one failure, H1 (processes starting at once), is
expected there: the migration lock exists only on PostgreSQL.

**Safety.**
- Never point it at production. It drops every table of the target database, several times.
- It refuses URLs that contain `render.com`, `dpg-`, `amazonaws.com` or `learnova-db`, a
  PostgreSQL database name without `test`, `scratch`, `disposable` or `migtest`, and any
  PostgreSQL run without `--wipe`.
- Child processes get scratch paths, `APP_ENV=testing`, `AI_MODE=cached` and no API keys.
  Every AI call is mocked and the seed data is synthetic (`anna` ... `emil` @example.test).
- It never writes into the repository: the old version comes from `git archive`.

**Run it** from the repository root, with the project's Python environment
(`requirements.txt` includes `psycopg2-binary`), `git`, and Docker or a local PostgreSQL of
the same major version as production (16 if unsure). It takes 3-5 minutes per baseline.

```bash
# Where the test folder is (outside the repository), and where its output goes.
PGTEST=../ai_tutor_suite_tmp/prod_pg       # R:/ai_tutor_suite_tmp/prod_pg on the dev machine
OUT=../learnova-pgtest                     # any folder outside the repository

# 1. A disposable server (or the official installer plus `createdb learnova_migtest`).
docker run --name learnova-pg-migtest -e POSTGRES_PASSWORD=migtest \
  -e POSTGRES_DB=learnova_migtest -p 55432:5432 -d postgres:16

# 2. From origin/main (8f924ac, migrations up to 036): 037-041 are new.
python "$PGTEST/run_pg_migration_test.py" --repo . \
  --database-url postgresql://postgres:migtest@localhost:55432/learnova_migtest --wipe \
  --old-rev 8f924ac --work "$OUT/work_pg_8f924ac"

# 3. From the last version before 031 (9493c7a): 031-041 are new.
python "$PGTEST/run_pg_migration_test.py" --repo . \
  --database-url postgresql://postgres:migtest@localhost:55432/learnova_migtest --wipe \
  --old-rev 9493c7a --work "$OUT/work_pg_9493c7a"

# 4. Throw the server away.
docker rm -f learnova-pg-migtest
```

`--repo` defaults to `R:/ai_tutor` (or `$LEARNOVA_REPO`). Other options:
`--concurrency N` (processes started at once in step H, default 2), `--skip-concurrency`,
`--skip-fresh`. The step labels still say "031-039"; the run applies whatever migrations the
working tree has, 040 and 041 included. A rehearsal with `--database-url
sqlite:///<absolute path>/migtest.db` proves the procedure, not PostgreSQL.

**What it checks.**

| Step | Check |
|---|---|
| 1-7 | The previous version builds its own schema and is seeded through its own routes: 5 users (XP 0/15/350/1200/5200; Premium active, expired, free), projects with and without topics, pages, lessons, attempts, exams, study plans, flashcard sets with study history, missions, chat. Every other table gets 2 rows. |
| 8, 8a | The first start of the current code migrates; every old row is byte-identical in its old columns (035 removes only the seeded duplicate mission). |
| 8b, 8c | No executed hand-written SQL that PostgreSQL rejects; no statement waits on a lock its own process holds. |
| 13a, 13b, 13 | The second, third and fourth starts execute no DDL or DML and change no data; logins still work. |
| 9-11 | `/health`; every seeded user logs in (a wrong password is refused); projects, Premium state, XP and flashcard sets are intact. |
| 12 | New Project Mode on a migrated account (upload, quick-start, confirm, lesson; mocked AI), the 037/038 constraints, and the foreign-key behaviour of the delete paths. |
| 8d | The upgraded schema equals the models; documented defaults on old rows. |
| G | 038 on a database with two key-terms sets for one project. |
| H1, H2 | N processes start at once on an un-migrated database; the next start finds a complete schema. |
| I | An empty database: `create_all` from the current models. |

**Expected on PostgreSQL: all 23 steps pass** (a prediction until the owner has run it). The
three failures predicted before the fixes are fixed in code: I (`review_step` had an integer column with a boolean default), 12 (deleting
a studied flashcard set ran into foreign keys) and H1 (no migration lock). If any step fails,
do not deploy. Send `<work>/report.json` (executed SQL and synthetic data, no secrets) and the
`log_*.txt` files.

## What this release migrates (037-041)

`origin/main` (`8f924ac`) has migrations up to `036_unified_project_mode`. This release adds
five versions and two tables. Every column is additive with a constant default, which
PostgreSQL 11 and later adds without rewriting the table; existing rows keep their data.

| Version | Adds | Existing rows |
|---|---|---|
| `037_project_teaching_engine` | `learning_section.topic_map_json`, `coverage_json` (TEXT, `'{}'`); `flashcard_set.project_id` (INTEGER REFERENCES `learning_project`, nullable); `flashcard.origin_key` (VARCHAR(160)), `topic_key` (VARCHAR(120)), `source_page_ids_json` (TEXT, `'[]'`), `reverse_answer` (TEXT); index `ix_flashcard_set_project_id`; partial unique index `uq_flashcard_origin` on `flashcard (set_id, origin_key) WHERE origin_key <> ''` | Empty defaults; old sets have no project. |
| `038_one_project_flashcard_set` | partial unique index `uq_flashcard_set_project` on `flashcard_set (project_id) WHERE project_id IS NOT NULL`, after unlinking all but the oldest set of a project | A project with two key-terms sets keeps the oldest; the other keeps its cards as an ordinary set. |
| `039_contact_messages` | Nothing (a marker). The table `contact_message` comes from `create_all`. | Untouched. |
| `040_assistant_chat_upgrade` | `conversation.archived_at`, `reply_started_at` (TIMESTAMP WITH TIME ZONE), `title_source` (VARCHAR(10), `'auto'`), `subject` (VARCHAR(80)), `topic` (VARCHAR(160)), `teaching_json` (TEXT, `'{}'`); `conversation_message.chips_json` (TEXT, `'[]'`), `client_message_id` (VARCHAR(40)). The table `conversation_lesson` comes from `create_all`. | An archived thread gets its last update as `archived_at`; an untouched "New conversation" gets `title_source = 'default'`. |
| `041_analysis_lease` | `learning_project.analysis_lease_token` (VARCHAR(32) NOT NULL DEFAULT `''`), `analysis_lease_until` (TIMESTAMP WITH TIME ZONE) | The empty lease: no analysis running. |

How `ensure_database()` applies them:
- Each migration checks what is missing, then runs its DDL in one transaction
  (`engine.begin()`); the `schema_migration` row is written afterwards. On PostgreSQL a
  failed migration is rolled back as a whole, and the next start detects and finishes it.
- A recorded version is never run again. A later schema change needs a new version number;
  editing 037's column list after it shipped would never reach a database that has 037.
- New DDL uses `TIMESTAMP WITH TIME ZONE`. Older migrations (002, 005, 024, 027, 031) created
  some naive `TIMESTAMP` columns; that is harmless while the server's TimeZone is UTC.

### One migrating process: the advisory lock and the lock timeout

`ensure_database()` runs when the app is imported, so in every gunicorn worker, every
`flask` command and `flask init-db`. On PostgreSQL (`learnova/db_ops.py`):

- It first takes `pg_advisory_lock(726110)` on a connection of its own. A second process
  waits until the first has finished, then finds every migration recorded and runs no DDL.
  The lock is released when the block ends, or when the connection closes. SQLite: no lock.
- Every transaction inside `ensure_database()` starts with
  `SET LOCAL lock_timeout = '<DB_MIGRATION_LOCK_TIMEOUT_MS>ms'` (default 10000; 0 turns it
  off). An `ALTER TABLE` that would queue behind a long request of the old instance, and make
  every later query on that table queue behind it, gives up instead: the migration is rolled
  back, the process fails to boot, and the next start retries.

The wait for the advisory lock itself has no timeout: a second process waits as long as the
first one migrates.

## Configuration recommendations

Defaults are in `learnova/config.py` and `.env.example`. These are the settings to decide
before a release, with the evidence for each recommendation.

### Token limits

**Recommended: `LESSON_TOKEN_LIMIT=5000` and `ANSWER_TOKEN_LIMIT=2600`.** `render.yaml` still
pins both at `"2200"`, which overrides the code defaults of 5000 and 2600.
**Change the two pins to `"5000"` and `"2600"`, or remove them**, and check the host's
environment for the same names and for `AI_LESSON_GENERATION_MAX_OUTPUT_TOKENS` /
`AI_ANSWER_EVALUATION_MAX_OUTPUT_TOKENS` (the lower of a call-site cap and the gateway budget
wins).

Where they apply (`app.py`):
- `LESSON_TOKEN_LIMIT`: `_generate_saved_practice` (Project Mode topic lessons in learn,
  review and reteach mode, mixed practice and final review, similar-mistake practice) and
  `/api/analyze` (a lesson from photos or a study goal). Concept, weak-topic, today's and
  exam-gap lessons are `adaptive_practice` and stay capped at 2200 by
  `AI_ADAPTIVE_PRACTICE_MAX_OUTPUT_TOKENS`, whatever the pin; that cap was not measured.
- `ANSWER_TOKEN_LIMIT`: the answer evaluation of `/api/answer` (with the Try-again fields),
  its input fit, and `generate_adaptive_question` (also under the 2200 adaptive cap).

Measured with the development Groq key (live lesson run of 2026-11-01, 34 requests):

| What | Output tokens | Against 2200 | Against 5000 / 2600 |
|---|---|---|---|
| Lessons on a one-page topic (6 subjects, gpt-oss-20b) | 1,190-1,903, of which up to 327 reasoning | up to 86 % | up to 38 % |
| Geography, run at the production pins 2200/2200 | lesson 1,477, answer 504 | fits | - |
| Richest lesson (6-page Ethics topic, the corrective call on gpt-oss-120b) | 3,284 | 149 %: cut off | 66 % |
| Answer evaluations | 300-592 | 27 % | 23 % |

- At 2200 the rich lesson is cut off, fails validation, and its one corrective call is cut
  off at the same cap, so the lesson fails. gpt-oss-120b writes about 1.8 times as much as
  20b for the same prompt, so any 20b JSON slip on a rich topic becomes a failed lesson.
- No response was cut off at 5000/2600.
- Raising the caps is not free at admission any more. On 2026-11-01 Groq admitted a text
  request on its input plus a small output estimate ("Requested 8305" came with `max_tokens`
  5000 and about 8,000 input tokens); on 2026-11-02 the same model counted input + about
  2,350 at a cap of 5000 (about 47 % of the cap), so a lesson over about 5,600 input tokens
  was refused. `AI_TEXT_REQUEST_OUTPUT_ALLOWANCE` (0.4 of the cap) makes the admission check
  and the notes fitting count that share. Billing follows the tokens actually written. The gateway reserves input plus the full cap against
  `AI_BUDGET_*` while a call runs, which matters only with tight budgets.
- Do not go above 5000 without new evidence.

### The Groq tier

**Check the production key's limits in the Groq console before release.** They decide more
than any setting on this page. Measured on the development key (`on_demand` tier,
2026-11-01/02, from 429 answers and the `x-ratelimit-*` headers):

| Model | Tokens per minute | Output tokens per minute | Requests per day | Tokens per day |
|---|---|---|---|---|
| `openai/gpt-oss-20b` | 8,000 | - | 1,000 | not seen |
| `openai/gpt-oss-120b` | 8,000 | - | 1,000 | not seen |
| `qwen/qwen3.8-27b` (every image) | 8,000 | 1,000 | 1,000 | not seen |

What that meant in the live runs:
- About **one vision page per minute**, site-wide. The second page read in a row was refused
  ("OTPM: Limit 1000, Requested 1057"). A chat photo sent with the 3,500-token reading cap
  was always refused first.
- A lesson takes 5.5k (one page) to 10k (rich topic) of a model's 8,000 tokens per minute: about
  one lesson per minute per model. A graded answer takes about 4k: about two per minute.
- A lesson on a 6-page topic at the full source budget was refused outright with HTTP 413
  ("TPM: Limit 8000, Requested 8305", then 8962 on the fallback model).
- The topic analysis of a 40-page PDF needed three rounds a minute apart.
- This serves a handful of students at a time, not a class.

**On this tier, set:**

| Setting | Value | Why |
|---|---|---|
| `AI_TEXT_REQUEST_TOKEN_CEILING` | `8000` (default 0 = off) | Text requests are checked before they are sent (input estimate plus the output allowance below plus max(200, ceiling/20)). Lessons, mixed practice, the practice exam, similar-mistake practice and a topic split fit their notes to it; anything else too large is refused without a call ("This request is too large for the AI…", 413 `ai_request_too_large`). The output cap is never lowered. With 0, a lesson or mixed practice refused by the provider with 413 is rebuilt once, smaller by the overshoot the refusal names (one refused call paid, `lesson.fit_retry`), or not sent again at all when it cannot be that small (`lesson.fit_retry_skipped`); the other tasks are sent unfitted. On a paid tier set it to that tier's tokens per minute, or leave it at 0. |
| `AI_TEXT_REQUEST_OUTPUT_ALLOWANCE` | `0.4` (the default) | The share of a text request's `max_output_tokens` counted against the ceiling (and in the rebuild after a 413). Measured on Groq on_demand 2026-11-02: Requested ≈ 0.93 × our estimate + 2,346 at a cap of 5000 (about 47 % of the cap; the day before 300-470 tokens). With 0.4 the six-page lesson replayed from that run fits in one call (estimate 5,314, about 7,289 counted). If Groq refuses lessons with "Requested" well above input + 0.4 × cap, raise it (0.5); 0 restores the input-only rule. |
| `AI_VISION_REQUEST_TOKEN_CEILING` | `8000` (the default) | Input estimate (2,048 tokens per image) plus output of one image request. The output cap is lowered to fit; a request that cannot keep its minimum output is refused before any call. At 8000 a chat message takes 2 photos; 3 need at least 9,710. |
| `AI_VISION_OUTPUT_TOKEN_CEILING` | `950` (default 0 = learn) | Skips the first refusal: with 0 the gateway learns the 1,000-per-minute output limit from a refusal and sends photo readings (`image_understanding`) at 950 for an hour (kept in `AI_CACHE_DIR/_limits`). Measured in the chat live check: readings sent at 3,500 were refused, readings sent at 950 were admitted. The pin applies to **every** image request, Project Mode page readings included (their budget is 4,000): a page whose reading needs more than 950 output tokens comes back cut off, and after the image modes the page falls back to its text layer or the offline OCR (and needs review). On this tier vision could not read such a page anyway. On a tier with a higher output limit, leave it at 0. |

`AI_CHAT_PHOTO_READING_MAX_OUTPUT_TOKENS` (900) already keeps a chat photo's compact reading
under 1,000. The alternatives to all of this are a paid tier, or an `AI_VISION_FALLBACK_MODEL`
with a budget for its provider ([AI_ROUTING.md](AI_ROUTING.md), [VISION.md](VISION.md)).

### Analysis steps and the lease

| Setting | Recommendation |
|---|---|
| `ANALYSIS_STEP_CALLS` (default 2, 1-6) | **1 if the reverse proxy's read timeout is 60 s or less** (nginx's default). One extraction call is 2-8 s when the provider answers at once and about 120 s in the worst case (40 s timeout, a corrective retry, a fallback after a 429). A step the proxy cuts off is harmless (the page waits and asks again) but slower. |
| `ANALYSIS_LEASE_SECONDS` (default 150, 30-900) | Keep it above the longest single extraction call. Change it only together with `AI_PROVIDER_TIMEOUT_SECONDS`. |

Details: [Long requests, gunicorn and the reverse proxy](#long-requests-gunicorn-and-the-reverse-proxy).

### Proxy and database

- **`PROXY_FIX_X_FOR`** (default 0, at most 5): how many trusted proxies in front of the app
  set `X-Forwarded-For` (Werkzeug `ProxyFix`, which then also trusts `X-Forwarded-Proto` for
  the same hops). With 0 behind a reverse proxy, every client has the proxy's address.
  **Rate limits.** 59 of the 62 `@limiter.limit` rules in `app.py`, and both shared limits
  (the assistant's replies), are on routes that need a login and count **per account**
  (`user_rate_key`): creating a project 10 an hour, quick-start, Add pages and Update topics
  20 an hour, graded answers 30 a minute, the analysis step, flashcards, vocabulary, the
  community, the chats. A school network or a family router behind one address no longer
  shares them (`tests/test_rate_limit_keys.py`). An anonymous request to such a route counts
  against its address (`user_rate_key` falls back to it) and `login_required` refuses it.
  Only the three routes used before signing in stay **per client address** (Flask-Limiter
  `get_remote_address`): sign-in 20 a minute, registration 10 an hour, the contact form
  (`CONTACT_RATE_LIMIT`, 10 an hour). With `PROXY_FIX_X_FOR` 0 behind a proxy, every client
  shares those three; and even with it set, a class signing in together from one school
  address shares sign-in's 20 a minute. **Set it to 1** when
  exactly one proxy (for example nginx with
  `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` and
  `proxy_set_header X-Forwarded-Proto $scheme;`) sits in front and the app port cannot be
  reached from outside. Never set it higher than the real number of proxies: a client could
  then choose its own address. How many proxies Render puts in front was not checked.
- **`RATELIMIT_STORAGE_URI`** (default `memory://`): per process. Correct with one worker; see
  [Gunicorn](#gunicorn).
- **Bound parameters are hidden** (`hide_parameters=True` in `SQLALCHEMY_ENGINE_OPTIONS`, every
  dialect, `learnova/config.py`): a database error, its traceback and any report made from it
  show the SQL statement but not the values - answers, notes, names, e-mail addresses.
- **`DB_POOL_RECYCLE_SECONDS`** (default 280, 0 = no recycling): PostgreSQL only. For a
  `postgresql` URL the pool also uses `pool_pre_ping`, so a connection the server dropped
  while idle is replaced instead of failing the first request after a quiet spell. Lower it if
  the database or a connection proxy closes idle connections sooner than about five minutes.
- **`DB_MIGRATION_LOCK_TIMEOUT_MS`** (default 10000, 0 = off): see
  [the lock timeout](#one-migrating-process-the-advisory-lock-and-the-lock-timeout).

### Accounts, email and prices

- **`PREMIUM_ADMIN_IDENTIFIERS`**: comma-separated usernames or emails of accounts that
  already exist. They manage Premium (issue and revoke activation codes; the `flask premium`
  commands' `--admin` must name one of them) and read `/admin/contact-messages`. Empty means
  nobody. Never list an address nobody has registered: whoever registers it first becomes an
  administrator. Treat the value like a password ([SUBSCRIPTIONS.md](SUBSCRIPTIONS.md)).
- **`AI_DIAGNOSTICS_ADMINS`**: who may open `/internal/ai-diagnostics` (see
  [Monitoring](#monitoring)). Empty hides the page.
- **Email** (`MAIL_SMTP_HOST`, `MAIL_SMTP_PORT` 587, `MAIL_SMTP_SECURITY` starttls,
  `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD`, `MAIL_FROM`, optional `MAIL_TIMEOUT_SECONDS` 10,
  `MAIL_MAX_ATTEMPTS` 3, and `PUBLIC_BASE_URL` for links in alerts). Nothing is emailed until
  `MAIL_SMTP_HOST` and `MAIL_FROM` are set. Until then every contact message is stored and
  listed at `/admin/contact-messages`, every community alert at `/internal/moderation`, and the
  app logs a warning at start. Contact messages go to `CONTACT_EMAIL` (default
  contact.studylearnova@gmail.com), community alerts to `COMMUNITY_ALERT_EMAIL`. `render.yaml`
  has an uncommitted change of that address to contact.studylearnova@gmail.com, which the owner
  should confirm. Put the credentials in the host's secret settings only (`sync: false` in
  `render.yaml`). Then check with `flask --app app contact list` (its first line says whether
  email is configured) and `flask --app app community test-email`, and forward what waited
  with `flask --app app contact retry` ([CONTACT.md](CONTACT.md),
  [COMMUNITY_MODERATION.md](COMMUNITY_MODERATION.md)).
- **Prices** (`AI_COST_<PROVIDER>_INPUT_PER_MILLION` and `_OUTPUT_PER_MILLION` for GROQ,
  OPENAI, ANTHROPIC and GEMINI; else `AI_INPUT_COST_PER_MILLION` /
  `AI_OUTPUT_COST_PER_MILLION`, default 0). They feed the cost columns of the diagnostics page
  and `AI_BUDGET_GLOBAL_SPEND_PER_MONTH`, which caps priced providers only. Unset, every cost
  is 0 and a spend cap does nothing; the token budgets still work. The repository ships no
  prices: take them from the provider's current price list. There is one price per provider,
  so for Groq use the price of the most-used model or a weighted figure.
- **`AI_BUDGET_GLOBAL_TOKENS_PER_DAY`** (default 2,000,000 tokens per UTC day, an emergency
  ceiling for the whole site): set it deliberately rather than relying on the default.

### Scheduled jobs (there is no scheduler)

Nothing in the app runs on a timer. Several cleanups also run at some request, but a quiet
site needs a schedule: a systemd timer or cron on the server, or a Render Cron Job with the
web service's environment. Run each command from the checkout, with the same environment
file as the web service (`DATABASE_URL`, `APP_ENV`, `SECRET_KEY`, mail settings). Every
`flask` command imports the app, so it also runs `ensure_database()`, which is a no-op when
nothing is due.

| Command | What it does | Also runs by itself | Suggested |
|---|---|---|---|
| `flask --app app chat-attachments cleanup` | Deletes unsent chat photos older than a day; sent photos older than `CHAT_ATTACHMENT_RETENTION_DAYS` (30) lose the picture and keep the reading. | On the next chat photo upload. | daily |
| `flask --app app contact cleanup` | Deletes contact messages older than `CONTACT_MESSAGE_RETENTION_DAYS` (90). | On each new message and once at every start. | daily |
| `flask --app app contact retry` | Forwards stored messages that wait for email. | The retry button on `/admin/contact-messages`. | after setting up email, then hourly |
| `flask --app app community retry` | Retries held safety checks, pending quality reviews, failed report re-reviews and due alert emails. | - | hourly |
| `flask --app app leagues process` | Closes finished league weeks (idempotent). | On the next leaderboard or profile visit, after the next XP award. | Monday 00:05 Europe/Berlin and hourly at :35 (templates in `deploy/systemd/`, `deploy/cron/`, [PROFILES_AND_LEAGUES.md](PROFILES_AND_LEAGUES.md)) |
| `flask --app app cleanup-flashcard-imports` | Deletes expired temporary flashcard imports and their files. | At the start of the flashcard and vocabulary import pages and of `POST /api/flashcards/imports`. | daily |
| `flask --app app redact-moderation-quotes` | Redacts quoted content from moderation records past `MODERATION_QUOTE_RETENTION_DAYS`. | - | daily |
| `flask --app app premium expire` | Marks lapsed subscriptions expired in the record (access has already lapsed). | When the student opens Settings. | daily |

### Storage

A processed photo page is stored as PNG, about 6-9 MB (measured). Since the upload no longer
renders pages, a page gets its processed image only when it is first read or viewed. A
smaller format is a measured follow-up (FIX_PLAN DR-08b).

## Long requests, gunicorn and the reverse proxy

### What one request may do

The start page splits the slow work of Project Mode into short requests:

| Request | AI calls | Typical | Worst case within config |
|---|---|---|---|
| `POST /projects/<id>/pages/<page>/recognize` (one page) | 1 vision call, up to 4 for an unclear page | 8-20 s | 75 s per call (`AI_VISION_TIMEOUT_SECONDS`) |
| `POST /projects/<id>/analysis/step` (one part of the topic analysis) | at most `ANALYSIS_STEP_CALLS` (default 2) extraction calls | 15-80 s | about 120 s per call: 40 s provider timeout, one corrective retry, a fallback model after a 429 |
| `POST /projects/<id>/quick-start` after the steps | 0 extraction calls; one lesson when the topics are confirmed | 1 s, or 20-60 s with the lesson | the lesson's own timeouts |

The steps loop is in `static/js/project-start.js`. Each step stores one complete topic
state. The next step reads only batches that are not cached yet.

Rate limits of the step (per account, `user_rate_key`): 120 answered steps an hour, and
600 requests an hour in all. A `202 analysis_running` poll does not count against the 120
(`deduct_when`), so a tab waiting for another one cannot use them up. When the step's own
limit answers 429 the page stops and says so; it does not fall back to quick-start, which
would do everything that is left in one long request (review A F6, 2026-11-02).

Three paths still do everything in one request:

- **The no-JS quick-start.** The start page's button without JavaScript reads every
  page that is due and analyses the whole project, as before. A 40-page project takes
  165-440 s with real latencies (docrel measurement).
- **Lesson generation inside quick-start**, once the topics are confirmed (20-60 s).
- **`POST /projects/<id>/reanalyze`**, kept for old links. The workspace's "Update
  topics" now opens the start page with `then=topics`.

All three hold the analysis lease (below) and resume from the per-batch cache when a
request is killed. Only the batch that was in flight is asked again.

### One analysis per project: the lease

`learning_project.analysis_lease_token` / `analysis_lease_until` (migration
`041_analysis_lease`, additive) is taken with one conditional `UPDATE`. That makes it
correct across threads, workers and hosts. The lease:

- is renewed before every extraction call;
- is released when the request ends, also on errors;
- expires after `ANALYSIS_LEASE_SECONDS` (default 150 s) when a request is killed, and
  the next request takes it over.

While a fresh lease is held, every other analysis of that project makes no AI call:

- the step and quick-start (JSON) answer `202 analysis_running` with `Retry-After: 3`;
- the HTML routes (quick-start without JS, reanalyze, the exam autopilot) show "Learnova
  is already working out the topics of this project. This page updates by itself." and
  open the start page.

Page reading is not under the lease. Each page has its own guard.

`ANALYSIS_LEASE_SECONDS` must be longer than the longest single extraction call (about
120 s, see the table). Otherwise a slow call outlives its lease and a second request may
start the same batch.

### Gunicorn

`render.yaml` starts `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120`.
What gunicorn 23.0.0 does with that, from its source:

- **`--threads` above 1 selects the gthread worker.** `threads > 1` with the default
  sync class becomes `gunicorn.workers.gthread.ThreadWorker` (`config.py` 103-121).
- **gthread does not kill long requests at `--timeout`.** Its main loop calls `notify()`
  on every iteration (`workers/gthread.py` 205) and blocks in `poller.select(1.0)` at most
  (210), while requests run in a thread pool. The arbiter's `murder_workers`
  (`arbiter.py` 488-507) only kills a worker whose heartbeat is older than `--timeout`.
  It logs "WORKER TIMEOUT", sends SIGABRT and then SIGKILL. A 400 s request on a gthread
  worker is therefore not killed at 120 s. gunicorn's own documentation of `timeout`
  says the same for non-sync workers.
- **A sync worker does kill long requests.** With no `--threads`, or `--threads 1`, the
  worker calls `notify()` only between requests (`workers/sync.py`). Every request longer
  than `--timeout` is aborted. The steps survive that, because each step is short and a
  killed step loses only its own batch. But the three one-request paths above, and an
  unclear page read with four vision calls, can run past 120 s. **Keep `--threads 4`
  or more.**
- **`--graceful-timeout` (default 30 s) applies to deploys and restarts.** In-flight
  requests get that long, then they are killed. A killed step costs at most its batch.
  The student's start page gets 202 until the dead request's lease expires (up to
  `ANALYSIS_LEASE_SECONDS`), then carries on. If deploys happen while students work, a
  longer `--graceful-timeout` (for example 90) loses less.
- **Keep `--workers 1` while in-process state exists. Scale with threads.** These live in
  one Python process and are not shared between workers:
  - `SESSIONS` (lesson sessions);
  - Flask-Limiter's `memory://` storage (every limit would multiply by the worker count);
  - the AI gateway's cooldowns and duplicate-request guard;
  - the model cooldowns.

  The analysis lease is in the database and does not need one worker.
- **Thread starvation.** One worker with 4 threads serves at most 4 requests at once.
  Four long requests at the same time (uploads, page reads, lessons) hold every thread,
  and everything else queues behind them, `/health` included. The steps keep analysis
  requests short. `--threads 8` is the cheap next step if this is seen.

### Reverse proxy

The production host (Oracle) is not described in this repository. These are the settings
to check there:

- **`proxy_read_timeout` / `proxy_send_timeout` of at least 130 s** (nginx's default is
  60 s). The proxy then waits longer than gunicorn's 120 s.
  - When the proxy gives up first, the browser gets 504 while gunicorn keeps working.
    On an analysis step that is now harmless: the start page shows "This takes a little
    longer. Learnova keeps working – please keep this page open.", asks again after 5 s
    and gets 202 until the first step is done, so nothing is paid twice.
  - On a page read or on quick-start's lesson, the student still sees an error, so the
    timeout matters.
  - If the proxy timeout cannot be raised above 60 s, set `ANALYSIS_STEP_CALLS=1`.
- **`client_max_body_size` of at least 45m.** The app accepts up to 40 MB per request.
- **`X-Forwarded-For` from one trusted proxy only.** Set `PROXY_FIX_X_FOR` to the number
  of trusted hops. The default 0 ignores the header, so a client cannot choose its own
  address for the rate limits.

Render's own HTTP timeout is 100 minutes, according to an old answer by Render staff on
its community forum. That figure is not verified.

### What the owner checks on the production host

1. **The real start command.** Look in the systemd unit (`systemctl cat <service>`), the
   supervisor config or the process list (`ps -ef | grep gunicorn`). Check:
   - the worker class (`-k` / `--worker-class`) and `--threads`: gthread, or sync with
     `--threads` of 2 or more, is required;
   - `--timeout` and `--graceful-timeout`;
   - `--workers`, and whether more than one instance runs behind the proxy.
2. **The reverse proxy.** For nginx, `nginx -T | grep -E "proxy_(read|send)_timeout|client_max_body_size|X-Forwarded-For"`:
   the timeouts, the body size, and which forwarded headers are set.
3. **The settings below** in the host's environment, if they are overridden there.

### Settings

| Setting | Default | Range | Purpose |
|---|---|---|---|
| `ANALYSIS_STEP_CALLS` | 2 | 1-6 | Extraction calls one `POST /analysis/step` request may make. Lower it when the proxy timeout is short. |
| `ANALYSIS_LEASE_SECONDS` | 150 | 30-900 | How long an analysis lease lasts before another request may take it over. Renewed before every extraction call. Keep it above the longest single extraction call. |
| `PROXY_FIX_X_FOR` | 0 | hops | Trusted proxies that set `X-Forwarded-For`. 0 ignores the header. |
| `WAITING_RETRY_AFTER` | 5 min | constant, app.py | After a provider stop, neither a step nor quick-start knocks again sooner. |

## What was verified, and where

"Offline" means in this repository with mocked AI, synthetic documents and scratch
databases. "Live" means the development Groq key with synthetic documents only
(2026-11-01 and 2026-11-02). "Owner" means only the owner can check it; each of those items is
open until the owner confirms it.

| Area | Offline | Live | Owner |
|---|---|---|---|
| PostgreSQL migrations 031-041 | Every executed migration statement linted for PostgreSQL; the PostgreSQL lock rules replayed; every model table and index compiled for the PostgreSQL dialect; the migration test 22/23 on SQLite with foreign keys enforced, both baselines (H1 needs PostgreSQL); the last whole suite with foreign keys enforced (review A): 2903 passed, 1 failed (F4, fixed since), 119 skipped. | - | A real PostgreSQL run of both baselines; the read-only `schema_migration` query on production. |
| Damaged and odd uploads, page completeness | Truncated, repaired, AES owner-password, corrupted, password-protected and empty files; the six-document Ethics upload with a damaged fourth file; double taps on Retry/Replace; `tests/test_upload_robustness.py` (42). | Document reading on synthetic documents before the fixes (39 requests). | Real student uploads. |
| Analysis in steps, the lease | 27 tests (also with foreign keys enforced) and 6 browser tests of `project-start.js`; a killed step, concurrent tabs and a provider stop (review A probes). | - | The production start command, threads and timeouts; the proxy's read timeout, `client_max_body_size` and forwarded headers. |
| Gunicorn and `--timeout 120` | Read from the gunicorn 23.0.0 source; not run under gunicorn here (the development machine is Windows). | - | The production host. |
| Reading honesty (OCR doubts, withheld flashcards, provisional offline OCR, daily-limit wait) | The 22 stored live readings replayed through the doubt check (one flagged, the stress page); adversarial short-word pages; `tests/test_reading_doubts.py`, `tests/test_reading_honesty.py`. | Handwriting set in synthetic fonts before the fixes (CER/WER per page). | Real handwriting (clear, messy, pencil, low contrast, maths, umlauts, labelled diagrams): the doubt check's false positives and misses are unknown. |
| Lesson quality, Try again | Every stored live lesson and answer replayed through the current guards; the six live answer leaks are withheld; `tests/test_retry_reveal.py`, `tests/test_lesson_grounding.py`, `tests/test_lesson_prompts.py`. | 6 subjects before the fixes, scored 10-15 of 20 by the tester. **The proposed re-check after the fixes (about 15 calls, FIX_PLAN 6.4) has not been run**: the new prompt rules (lesson v8, grading v7, diagnosis v3) are untested against the real model. | A teacher's review of sample lessons per subject; approval of the re-check. |
| Token limits | Paths that use each setting (code reading). | Measured (above). | The `render.yaml` pins. |
| Groq limits | `tests/test_text_request_limits.py` with fake 413 and 429 answers. | The development key's tier (above). | The production key's tier in the Groq console. |
| Chat formulas and photos | The chat suites, browser tests with real KaTeX. | 247 of 247 formulas rendered (16 requests, 2026-11-02); photos failed at 1,000 output tokens per minute before CHAT_FIX2. CHAT_FIX2 (compact chat reading, one photo per request) is tested offline only. | Whether unreadable photos should cost no allowance (they do not now). |
| Phones | The mobile layout audit in system Chrome (0 blocking faults); the on-screen keyboard is simulated. | - | A real iPhone (Safari keyboard, camera, photo upload) and an Android phone. |

Also open: the 256 orphan files in the development checkout's `instance/flashcard_imports/1/`,
written by earlier test runs, are kept until the owner says they may be deleted.

## Rolling back

**The migrations are additive.** No table or column is dropped or renamed, and every new
`NOT NULL` column has a default, so the previous code reads and writes the migrated database
as before. There is no down-migration: to roll back, redeploy the previous commit and leave
the schema as it is. Do not restore the backup to undo a code change; that also throws away
everything written since the deploy. Restore only when data itself is damaged.

What the previous code (`8f924ac`) does with what this release wrote:
- A file that failed (a document row with no pages) shows "Could not be read", without Try
  this file again, Replace file or Remove file.
- A page this release uploaded that nobody has read or viewed yet has no processed image. The
  old code renders it when it reads it, but without the camera scanner's crop and rotation
  (kept in `recognition_json["upload_transform"]`), and its processed-image view answers 404
  until then.
- The lease columns are ignored: two analyses of one project can run side by side again.
- The PostgreSQL defects fixed in this release come back: deleting a flashcard set that has
  study history fails with 503, the expired-import cleanup fails, there is no migration lock
  (keep one process), and an empty PostgreSQL database cannot be created.

A boot that fails on a migration (lock timeout, an error) changes nothing: the migration's
transaction is rolled back and nothing is recorded. An old instance that is still running keeps
serving. Fix the cause and start again.

Smaller switches, without going back to old code:

| Setting | Effect |
|---|---|
| `PROJECT_PREPROCESS_ON_UPLOAD=true` | Uploads render and preprocess every page in the request again, as before (slow: 33 s for a 40-page PDF on the development machine). |
| `AI_TEXT_REQUEST_TOKEN_CEILING=0` | No text admission check and no fitting before sending. |
| `AI_TEXT_REQUEST_OUTPUT_ALLOWANCE=0` | Admission and fitting count the input only, as before 2026-11-03 (the rebuild after a 413 still uses the overshoot the refusal names). |
| `AI_VISION_OUTPUT_TOKEN_CEILING=0` | No pinned output cap for images; the limit is learned from the provider again. |
| `ANALYSIS_STEP_CALLS` | Fewer or more extraction calls per step (1-6). |

## Monitoring

Where to look:
- **The process log**: gunicorn's error log, the systemd journal or the host's log view.
- **`/internal/ai-diagnostics`** (for `AI_DIAGNOSTICS_ADMINS`): budgets with used, remaining
  and reset time, usage by provider and task, the routing table and recent failures. It reads
  the ledger: the `ai_usage_event` table, mirrored to `instance/ai_usage.jsonl`
  (`AI_USAGE_PATH`). Each failed call has its category there (`provider_rate_limit`,
  `provider_daily_limit`, `request_too_large`, `provider_timeout`, `invalid_json` …). In
  production this is the main view of AI failures.

**Log levels.** Outside production `app.py` turns on INFO logging. In production
(`APP_ENV=production`) it does not, and the Python default leaves only WARNING and above:
checked with Flask, `app.logger` and the `learnova.*` loggers are then not enabled for INFO.
gunicorn 23 leaves the root logger alone unless it is given a logging configuration: with
`--log-config-json <file>` or `logconfig_dict` in a `-c` config file it starts from its own
defaults, whose root logger is INFO; with `--log-config <ini file>` the file decides. So the
INFO lines below appear in production only if the host sets that up. They never contain
student content.

Seen in production (WARNING and ERROR):

| Line | Meaning | What to do |
|---|---|---|
| `ai.provider_failure task=… provider=… model=… status=… code=… type=… category=… limit_scope=… limit=… requested=… retry_after=…` | One failed provider call. Status, codes and limit figures only, never the provider's text. | Many `provider_rate_limit` (`limit_scope=tpm` or `otpm`) or `request_too_large`: the tier is too small, see [The Groq tier](#the-groq-tier). `provider_daily_limit`: a per-day limit (`tpd`, `rpd`). `model_not_found`: a model was withdrawn; change the model setting. `authentication_failure`, `provider_quota_exhausted`: the key or the account. |
| `ai.output_truncated task=… model=… max_output_tokens=… provider=… output_tokens=… finish_reason=… attempt=…` | One provider answer stopped at its output cap (`finish_reason` `max_output_tokens`, `incomplete`, `length` or `max_tokens`). Names and numbers only, never the answer. If the cut-off answer was not valid, no corrective retry went to the same model ([AI_ROUTING.md](AI_ROUTING.md#failures-retries-and-the-call-bound)). The usage log line of that request has `output_truncated: true` and the `finish_reason`. | Expected for chat photo readings at `AI_VISION_OUTPUT_TOKEN_CEILING=950` (kept as partial readings). Frequent for one text task: raise its `AI_<TASK>_MAX_OUTPUT_TOKENS`, within the tier's limits ([Token limits](#token-limits)). |
| `vision.read_failed purpose=… images=… category=… status=… code=…` | A chat, lesson or answer photo could not be read. | As above. In the chat live check, `invalid_json` here was a reading cut off by the output cap; an `ai.output_truncated` line just before it says so. |
| `topic analysis stopped: <error type>` | The topic analysis stopped at a provider-wide or limit error. The pages not reached are "not analysed" and are resumed later. | Repeated: the tier or the student's hourly allowance. |
| `topic batch failed: <error type>` | One batch's answer was unusable; it is retried in halves. | Frequent: look at the ledger's validation failures for `project_section_generation`. |
| `releasing the analysis lease failed; it expires by itself` | The database refused the release. | It expires after `ANALYSIS_LEASE_SECONDS`; check the database if it repeats. |
| `flashcard_import.cleanup_failed id=…` | An expired flashcard import could not be deleted; that row is skipped and retried next time. | Repeated for one id: look at that import and what still references it. |
| `flashcard_import.file_cleanup_failed id=…` | Its private file could not be removed after the row was deleted. | Check the storage folder's permissions; the file is now an orphan. |
| `Database operation failed` (with a traceback) | Any database error; the student gets 503 "The database is temporarily unavailable." | Read the traceback. Foreign-key violations on PostgreSQL show up here. |
| `Topic analysis step failed`, `Project step failed`, `Project upload failed`, `Reading a failed project file again failed`, `Material analysis failed` (with a traceback) | An unexpected error in a Project Mode request; the student's work is kept. | Read the traceback. |
| `Syncing the project flashcards failed; the topics are saved` (and the variants after a page check or before a lesson) | The key-terms set could not be updated; the next analysis or lesson tries again. | Read the traceback. |
| `assistant.claim_release_failed`, `continue_learning.row_update_failed`, `ai.cache_purge_failed` | Bookkeeping that failed safely; a claim expires after `ASSISTANT_REPLY_CLAIM_SECONDS`. | Only if it repeats. |
| At start: `COMMUNITY_MODERATORS is empty…`, `Community alert email is not configured…`, `Contact form email forwarding is not configured…`, `local OCR warm-up failed: …` | Configuration that is missing. | Set it ([Accounts, email and prices](#accounts-email-and-prices)). |
| gunicorn: `WORKER TIMEOUT (pid:…)` | A worker missed its heartbeat for `--timeout` and is killed. With the gthread worker that means the worker process itself stopped responding, not that one request was long. With a sync worker it is every request longer than `--timeout`. | See [Gunicorn](#gunicorn). |

Seen only with INFO logging:

| Line | Meaning |
|---|---|
| `lesson.fit_retry limit=… requested=… sent=… rebuilt=…` | A lesson or mixed practice was refused with 413 and rebuilt once, smaller by the overshoot (one refused call paid). Frequent: set `AI_TEXT_REQUEST_TOKEN_CEILING`. |
| `lesson.fit_retry_skipped limit=… requested=… sent=… rebuilt=… room=…` | The refused lesson could not be made small enough (its notes are at `LESSON_SOURCE_MIN_CHARS`), so it was not sent again; the student got the "too large" message. Frequent: raise `AI_TEXT_REQUEST_OUTPUT_ALLOWANCE` or check the tier. |
| `lesson.first_question_replaced reason=…` / `_rekeyed` / `_rewritten` / `_kept` | The lesson's first question was replaced by a Fachbegriffe question (repeats the guided exercise, a key the notes contradict, an unanswerable shape), re-keyed or rewritten (a slope key), or kept for lack of three terms. |
| `question.replaced source=grader reason=…` | The grader's next question was unanswerable or contradicted by the notes; the gate generated another. |
| `answer.notes_backed_option` | A choice the student's notes literally say was graded at least partly right, without a retry. |
| `exam.fitted sections=… chars=…` | A practice exam's notes were shortened to fit the text ceiling. |
| `ai.json_repaired task=… kind=…` | A JSON syntax slip (a stray quote, a missing final brace) was repaired without a corrective call. |
| `ai.json_salvaged task=…` | A chat photo reading that was cut off was kept as a partial reading. |
| `ocr.waiting project=… page=… reason=…` | A page waits instead of being read: `provider_busy`, `provider_daily` (an hour) or `hourly_limit` (the student's allowance). |
| `ocr.damaged`, `ocr.blank` | A damaged PDF page, or a blank one, stored without a vision call. |
| `ocr.fallback … source=… vision_error=…`, `ocr.local_fallback_failed`, `ocr.empty` | Vision gave nothing; the text layer or the offline OCR stood in, or nothing could be read. |
| `ocr.native …`, `ocr.native_rejected reason=…` | A PDF page read from its own text layer without a vision call; or a text layer that was not used (too little usable text, or `pictures_on_page`), so vision reads the page. |

Two gaps the final review found, both closed since:
- The gateway did not record the provider's finish status, so an answer cut off at its output
  cap showed up only as a validation failure (`invalid_json`). Now every usage log line
  carries `output_truncated` and `finish_reason`, and each cut-off call logs
  `ai.output_truncated` at WARNING (above). The `ai_usage_event` table has no such column;
  the JSONL log (`AI_USAGE_PATH`) and the WARNING line are where to look.
- SQLAlchemy wrote a failing statement's parameters into the traceback, so a database error
  inside an analysis step could log part of a project's `analysis_json`. The engine now sets
  `hide_parameters=True` on every dialect: the traceback shows
  `[SQL parameters hidden due to hide_parameters=True]` instead.

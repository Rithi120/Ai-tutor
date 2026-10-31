# Subscriptions: Free, Premium and activation codes

Learnova has two plans. There is no online payment: Premium is paid in cash at school and
switched on with a one-time activation code.

| | Free | Premium (3,00 € / month) |
|---|---|---|
| New projects | 1 in any rolling 7 days | no plan limit |
| Lessons and tests inside a project | unlimited | unlimited |
| Other new short lessons | 2 per calendar day (Europe/Berlin) | no plan limit |
| AI assistant (tutor chat and the assistant) | a daily free allowance | no plan allowance |
| Flashcards, vocabulary, practice, progress | yes | yes |
| Ads | shown once enabled ([ADSENSE.md](ADSENSE.md)) | never |

Premium removes the plan limits only. Abuse protection still applies to every account:
- hourly request caps;
- request-size limits;
- site-wide token budgets;
- provider rate limits;
- route rate limits.

Premium never changes which AI model a student gets.

Code: `learnova/subscriptions/`.

| File | What it does |
|---|---|
| `plans.py` | Settings |
| `codes.py` | Code format and hashing |
| `periods.py` | Calendar maths |
| `entitlements.py` | Allowances |
| `activation.py` | Issuing and redeeming codes |

The routes, the CLI and the hooks are in `app.py`. The version shown in Settings comes from
`learnova/version.py`, the only place it is written.

## Cash payment and activation codes (operator workflow)

### One-time setup

1. Set `PREMIUM_ADMIN_IDENTIFIERS` to the usernames or emails of accounts that **already
   exist**. Example: `PREMIUM_ADMIN_IDENTIFIERS=your-username`.
   - Do not list an address that nobody has registered yet: whoever registers it first
     becomes an administrator.
   - Treat this variable like a password.
2. Optionally set `PREMIUM_PAYMENT_INSTRUCTIONS`. It is a short plain-text note shown
   under the cash explanation, for example a room or a time.

### Selling one month

1. Take the 3 € and confirm the payment.
2. Sign in with your admin account and open **Settings → Subscription → Manage activation
   codes** (`/admin/premium-keys`).
3. Set the details, then press **Issue code**:
   - the months (1 by default);
   - the amount (pre-filled);
   - the date;
   - optionally a note without student names;
   - tick "I have received this cash payment in person".
4. The code, for example `LN-7KQ2M-X9TAD-4HNWE-R1CZP`, is shown **once**:
   - Copy it or read it out to the student.
   - It cannot be shown again: only its hash is stored.
   - The page is sent with `Cache-Control: no-store`.
5. The student opens **Settings → Subscription**, types the code and taps **Activate
   Premium**. Case, spaces and dashes do not matter, and O/0 and I/L/1 are read the same.

### From a server shell instead

```bash
flask --app app premium issue --admin your-username --amount 3.00      # prints the code once
flask --app app premium issue --admin your-username --amount 6.00 --months 2 --reference "receipt 17"
flask --app app premium list                                    # status and last 4 characters, never the code
flask --app app premium revoke --admin your-username 42                 # revoke unused code #42
flask --app app premium expire                                  # record lapsed subscriptions as expired
```

### Status, revoking and lost codes

- The admin page lists every code by record number and its last four characters. For each
  code it shows the months, the payment, the issue date, when it expires unused, and who
  redeemed it and when.
- It also lists the last 50 audit events: issued, redeemed, failed attempt (with reason),
  revoked, expired.
- An unused code can be revoked; a redeemed one cannot, because the student has paid.
- If a code is lost before it is used, revoke it and issue a new one.

## Rules a code follows

- **Generation:** 20 characters of Crockford Base32 from `secrets` (100 random bits),
  generated only on the server. There is no master code, no pattern and no sequence.
- **Storage:** the code is stored only as `SHA-256("learnova-activation-code:v2:" + code)`.
  The plaintext is not in the database, the logs, the audit rows or any response after
  issuance.
- **One use:**
  - A single conditional `UPDATE` claims the code, and `premium_redemption.premium_key_id`
    is unique, so two simultaneous redemptions cannot both succeed.
  - Submitting your own redeemed code again answers "already active" and extends nothing.
- **Expiry of unused codes:** `PREMIUM_CODE_VALIDITY_DAYS` (default 90) after issuance.
- **Duration:**
  - **calendar months** in `APP_TIMEZONE`, keeping the local time of day;
  - the day is clamped to the month's length: 31 January + 1 month = 28 February, or 29
    in a leap year.
- **Renewal:**
  - If Premium is still active, a new code extends it from its current end date, so no
    days are lost.
  - If it has lapsed, the new period starts at redemption.
  - Nothing renews automatically.
- **Failed attempts:**
  - Every failure gets the same answer, whether the code is unknown, malformed, expired,
    revoked or already used.
  - After 5 failures per account, or 20 per client address, within 15 minutes, attempts
    pause until the window has passed.
  - The address is stored only as a keyed hash.
  - Failures are counted in the database, so the limit survives restarts.
- **Legacy keys:** `LN-PREM-…` keys issued before calendar months still work and grant the
  30 days they were sold for (`duration_months = 0`).
- **Expiry of Premium:** Premium is checked against `expires_at` on every request, so an
  expired subscription is Free immediately. The status column is updated (and audited)
  when the student opens Settings or when `flask premium expire` runs.

## Free allowances

Free allowances are enforced on the server. A free account always has one of two states:
- **Premium:** no plan limits apply.
- **Free:** allowances are counted.

The plan always comes from the database, never from the request.

Usage is recorded in `usage_quota_event` as **reserved → committed**, or **reserved →
released**:
- **Reserving:** the reservation is taken before the AI work, under an account lock (an
  `UPDATE` of `user.usage_lock_version`). Two tabs therefore cannot both take the last
  lesson. This works on PostgreSQL and SQLite, across workers and restarts.
- **Committing:** only when the work succeeds.
- **Releasing:** on any failure the reservation is released. A reservation older than
  `QUOTA_RESERVATION_TTL_MINUTES` (15) counts for nothing, so a crashed request cannot hold
  an allowance.

### What counts

- **Project:** counted when its topics are generated for the first time
  (`plan_project_sections`), or when it is created if it starts with typed topics (those
  are its topics, so no analysis would ever count it):
  - Creating, opening, correcting pages, adding pages or re-analysing does not count.
  - A project counts at most once.
  - Deleting it does not give the allowance back.
  - A free account with no projects left is told before the upload.
- **Short lesson:** counted when a newly generated lesson is saved, from either of the two
  places lessons are generated:
  - `/api/analyze`, the "New lesson" page;
  - `start_saved_practice`, which covers reviews, today's practice, weak-point,
    similar-mistake and concept practice.

  Lessons and tests inside a project (topic lessons, section tests, mixed practice and
  exam-gap practice; `project_work=True`) are unlimited. The project is the counted unit.

  Reopening a saved lesson or resuming an unfinished one never counts.

  > Until 2026-10-31 project lessons counted too, so a free project plan moved at 2 lessons
  > a day. By user decision a free account now gets one project a week with unlimited
  > lessons and tests in it. Every project counts, including one made only from typed topics.
- **AI assistant** (tutor chat in a lesson and the assistant chat) — an internal credit
  budget per period (`FREE_CHAT_CREDITS_PER_PERIOD`, default 150 000 per day):
  - **Units:** a credit is one input token on a weight-1 model. Output tokens count
    `FREE_CHAT_OUTPUT_WEIGHT` (4), because providers bill output higher. The total is
    multiplied by the model's weight from `FREE_CHAT_MODEL_WEIGHTS` (e.g. `openai=5`).
  - **Before the call:** the maximum the call may cost is reserved: estimated input plus
    the whole output budget.
  - **After the call:** the reservation is replaced by what every provider call of that
    request really used, retries and fallbacks included. The gateway reports each call via
    `usage_observer`.
  - **Costs nothing:** cache hits, de-duplicated double clicks, and failures before any
    provider call.
  - **Typical use:** a chat message uses roughly 5 000–12 000 credits.
  - **What students see:** never credits or tokens. They see "available" or "used, back
    at …".

### What a student sees

When a limit is reached, the student sees what was used up, when it comes back (in local
time) and a **See Premium** link. JSON responses also carry `upgrade_url`, `resets_at` and
a `Retry-After` header. Everything else keeps working, including saved lessons, flashcards,
vocabulary and progress.

## Configuration

All values are optional and validated at startup: a bad value stops the deploy.

| Variable | Default | Meaning |
|---|---|---|
| `PREMIUM_MONTHLY_PRICE_EUR` | `3.00` | Price shown in Settings and pre-filled per month on the admin page |
| `PREMIUM_CODE_DURATION_MONTHS` | `1` | Default months per code |
| `PREMIUM_CODE_MAX_DURATION_MONTHS` | `12` | Most months one code may grant |
| `PREMIUM_CODE_VALIDITY_DAYS` | `90` | Days an unused code stays redeemable |
| `PREMIUM_PAYMENT_INSTRUCTIONS` | empty | Extra plain-text note under the cash instructions |
| `PREMIUM_REDEEM_ATTEMPTS_PER_ACCOUNT` | `5` | Failed attempts per account per window |
| `PREMIUM_REDEEM_ATTEMPTS_PER_SOURCE` | `20` | Failed attempts per client address per window |
| `PREMIUM_REDEEM_WINDOW_MINUTES` | `15` | The window |
| `APP_TIMEZONE` | `Europe/Berlin` | Calendar days and months for quotas and expiry |
| `FREE_PROJECTS_PER_WINDOW` / `FREE_PROJECT_WINDOW_DAYS` | `1` / `7` | Rolling project allowance |
| `FREE_LESSONS_PER_DAY` | `2` | Lessons per local calendar day |
| `FREE_CHAT_CREDITS_PER_PERIOD` / `FREE_CHAT_PERIOD` | `150000` / `day` | Chat allowance (`day`, `week`, `month`) |
| `FREE_CHAT_OUTPUT_WEIGHT` | `4` | Output-token weight |
| `FREE_CHAT_MODEL_WEIGHTS` | empty | `provider=weight` or `provider:model=weight` |
| `QUOTA_RESERVATION_TTL_MINUTES` | `15` | When an unfinished reservation lapses |

Under the test runner, allowances are off unless a test sets
`SUBSCRIPTION_QUOTAS_ENFORCED=True`; `tests/test_subscriptions.py` does.

## Database

Migration `032_subscriptions` is additive and runs at start-up in `ensure_database()`, or
with `flask --app app init-db`.

- **New tables** (from `create_all`):
  - `usage_quota_event`;
  - `subscription_audit_event`.
- **New columns:**
  - `user.usage_lock_version`;
  - `premium_key.duration_months`, `code_hint` and `revoked_by_id`;
  - `premium_redemption.previous_expires_at` and `new_expires_at`.
- **Existing data:**
  - Existing users are Free unless they already have an active `premium_entitlement`
    row.
  - Existing entitlements and keys are kept as they are.
  - Existing keys get `duration_months = 0`, so they keep their 30 days.

**Rollback:** the old code ignores the new columns and tables, so no down-migration is
needed. Dropping them by hand is safe once nothing is rolled forward again.

## Known limits

- **Client address behind a proxy:** the app has no `ProxyFix`, so behind a reverse proxy
  every request shares the proxy's address. The per-address attempt limit then acts as a
  site-wide one (20 failures per 15 minutes for everyone) while the per-account limit stays
  exact. Configure `ProxyFix` for the real proxy depth before relying on the address limit.
- **Flask-Limiter storage:** Flask-Limiter uses `memory://` unless `RATELIMIT_STORAGE_URI`
  is set. The decorator limits on the redeem route reset on restart; the database attempt
  limit does not.
- **Gateway's own reservations:** the gateway's in-process reservations (site and provider
  budgets) are still per process. One gunicorn worker is what is deployed. The free
  allowances above do not depend on that.
- **Practice exams:** practice exams (`final_exam_generation`) and answer grading are not
  plan-limited. Only the abuse limits apply to them.

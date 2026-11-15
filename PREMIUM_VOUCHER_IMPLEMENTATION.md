# Secure bulk Premium vouchers (2026-11-14)

Nothing was committed, pushed or deployed, and no production file or database was touched.

## 1. What existed, and the conflicts

Learnova already had one Premium system: cash payment and a one-time activation code (`learnova/subscriptions/`,
`docs/SUBSCRIPTIONS.md`). It already provided:

| Requirement | Existing implementation (kept) |
| --- | --- |
| Admin authorisation | `PREMIUM_ADMIN_IDENTIFIERS` allowlist. `_premium_admin_required` answers 404 to everyone else; community moderators are a separate list and get nothing |
| Atomic redemption | One conditional `UPDATE … WHERE redeemed_at IS NULL AND revoked_at IS NULL AND expires_at > now`, plus `SELECT … FOR UPDATE` on PostgreSQL. `premium_redemption.premium_key_id` is unique, and everything is one transaction, rolled back on any failure |
| Calendar months | `periods.add_months` in `APP_TIMEZONE`: 31 Jan + 1 month = 28/29 Feb |
| Extension | An active subscription is extended from its end date; a lapsed one starts at redemption |
| Brute force | One generic answer for every failure; 5 failures per account or 20 per hashed address in 15 minutes, counted in the database; the route is limited to 10/min |
| Audit | `subscription_audit_event`: issued, redeemed, failed (with a reason, never the code), revoked, expired |
| Entitlement | `premium_entitlement` (one row per user, `expires_at`). All Free/Premium checks read it on every request |

The spec conflicted with it in four places. Each was **extended, not replaced**:

1. **Hashing:**
   - Old codes are `SHA-256("learnova-activation-code:v2:" + code)`. The spec requires
     HMAC-SHA256 with a server pepper.
   - New codes use HMAC; existing codes keep their hash and still work. Old hashes can't be
     re-hashed, because the plaintext is not known.
2. **Entropy:**
   - Old codes have 100 random bits (20 Crockford characters); the spec requires at least 128.
   - New codes have **140 bits** (28 characters).
3. **Format:**
   - The spec's example `LN-PREM-…` was already the prefix of *legacy* keys (pre-2026 keys
     that grant 30 days, matched as their exact string).
   - New vouchers use `LN-PREM-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX-XXXX` (7 groups of 4) and are told
     apart by their length. A legacy key's exact-string hash can never equal a voucher's HMAC,
     so the two never collide. Tested.
4. **Batches:** they did not exist. Codes were issued one at a time for a recorded payment.
   - Batches are new: table `premium_code_batch` and `premium_key.batch_id`.
   - The single-code page stays; with the pepper set, its codes are vouchers too.

## 2. Architecture

| Part | File |
| --- | --- |
| Voucher format, normalisation, HMAC (`generate_voucher_code`, `normalize_voucher`, `voucher_hash`, `candidate_hashes`) | `learnova/subscriptions/codes.py` |
| Batch issue, batch revocation, pepper-aware single issue and redemption (`issue_batch`, `revoke_unused_in_batch`, `vouchers_enabled`) | `learnova/subscriptions/activation.py` |
| Models `PremiumCodeBatch` and `PremiumKey.batch_id`; migration 049; routes; messages | `app.py` |
| Admin page | `templates/admin_premium_vouchers.html` |
| Copy and download in the browser | `static/js/premium-vouchers.js` |
| "Premium-Code einlösen" in Settings | `templates/components/_subscription.html` |
| `PREMIUM_VOUCHER_PEPPER`, `PREMIUM_VOUCHER_VALIDITY_DAYS` | `learnova/config.py` |

## 3. Database (migration `049_premium_vouchers`)

**`premium_code_batch` (new table):**
- `id`, `public_id` (unique, for example `B-20261114-7KQ2`), `created_at`;
- `created_by_id` → `user.id` (indexed);
- `code_count`, `duration_months`, `expires_at`, `note` (≤ 200, no student names).

**`premium_key.batch_id`:** `INTEGER REFERENCES premium_code_batch(id)`, nullable, indexed.

**Voucher record (`premium_key`):**
- `key_hash` (unique), `code_hint` (last 4 characters), `duration_months`;
- `created_at`, `created_by_id`, `expires_at`;
- `redeemed_at`, `redeemed_by_id`, `revoked_at`, `revoked_by_id`, `batch_id`;
- the Premium expiry granted is in `premium_redemption.new_expires_at`.
- Status is derived, so it can never disagree with the timestamps:
  - revoked if `revoked_at`;
  - else redeemed if `redeemed_at`;
  - else expired if `expires_at` has passed;
  - else unused.

**How the migration runs:**
- It is additive and runs in `ensure_database` at start-up: `create_all` makes the new table,
  then one `ALTER TABLE premium_key ADD COLUMN …` and one `CREATE INDEX IF NOT EXISTS`.
- It is recorded once in `schema_migration`.
- It is plain SQL that both PostgreSQL and SQLite accept. It was **run on SQLite only**, so it
  must be rehearsed on a PostgreSQL copy before production (section 9).

## 4. Code generation

- **Randomness and format:** each character is `secrets.choice` over Crockford Base32
  (`0-9A-Z` without I, L, O, U), 28 characters = 140 bits. It is shown as
  `LN-PREM-` plus 7 groups of 4.
- **Batch sizes:** 10, 25, 50, 100, or a custom number from 1 to **500**.
  - Anything else is refused: 0, 501, words, "1e3", a preset that does not exist.
  - Refused before any row is written.
- **One transaction:** the batch row and all its `premium_key` rows are written together.
- **Duplicates:** a hash already drawn in the batch is drawn again. The unique index on
  `key_hash` is the final guard. A collision with an existing row aborts the whole batch, and
  nothing is stored.
- **Pepper required:** without it (or with one under 32 characters) no batch is generated,
  and the page says why.
- **Rate limit:** 20 generation requests per hour per admin account.

## 5. Hashing

- `HMAC-SHA256(PREMIUM_VOUCHER_PEPPER, "learnova-voucher:v3:" + normalised body)`, hex, 64
  characters, unique.
- **Normalisation:**
  - surrounding whitespace, spaces, dashes and case are ignored, and the prefix is optional;
  - O is read as 0 and I/L as 1;
  - input over 120 characters is rejected before hashing.
- **Lookup:** by the hash (indexed equality). The stored hash is compared with
  `hmac.compare_digest`.
- **Why a fast HMAC is enough:** 2^140 possibilities cannot be searched, and without the pepper
  a database copy cannot be used to test guesses offline.
- **Never anywhere:** the plaintext is not in the database, the audit table, the logs (only
  batch id and counts), API responses after the generation page, or the statistics CSV.

## 6. One-time display and downloads

- **The generation response is the only place the codes appear.** It is a POST result with
  `Cache-Control: no-store, private`, and carries a clear warning to save the codes now.
- **Copy and download:** "Copy all codes", "Download CSV" (`activation_code,batch_id,duration`)
  and "Download TXT" are built **in the browser** from that page (Blob download).
  - No file is written on the server, no download URL exists, and nothing can be fetched
    again.
  - Without JavaScript, the codes are in a read-only text field to copy.
- **No recovery:** reopening the admin page shows the batch and its counts, never a code.
  Lost codes cannot be recovered: revoke them and generate new ones.

## 7. Redemption

Settings → Subscription → **"Premium-Code einlösen"**: "Deinen Premium-Code eingeben und einen
Monat Premium freischalten." It has the existing loading state.
- The input's limit was **raised from 40 to 80 characters**. A voucher written in full is 42
  characters, so the old limit would have cut every voucher off in the browser.

The server, using the existing `ActivationService.redeem`:

1. **Check the request:** login required; route limit 10/min; account lock (`usage_lock_version`);
   failed-attempt limit.
2. **Find the code:** normalise and compute the candidate hashes (voucher with the pepper, v2,
   legacy); select `FOR UPDATE`; compare with `compare_digest`.
3. **Validate:** it must exist, not be revoked, not be redeemed, not be expired, and have a
   valid duration. Otherwise one generic answer is given and the failure is audited with a
   reason (never the code), in its own transaction.
4. **Claim it:** the conditional `UPDATE` claims the code (`rowcount == 1`, or it lost the race).
5. **Extend Premium:** the entitlement row (`FOR UPDATE`) is extended by calendar months, from
   its end if active, from now if lapsed.
6. **Record it:** the `premium_redemption` row and an audit row are added, then one commit.
   Any exception rolls everything back. This is tested: a failure leaves the voucher unused
   and the student Free, and the voucher works afterwards.

**Messages:**
- "Premium erfolgreich aktiviert! Dein Premium-Zugang wurde um einen Monat verlängert.
  Premium läuft bis {Datum}." (Premium activated, extended by one month, runs until {date};
  for 2+ months: "um {n} Monate"). Translated into English, French and Spanish.
- Failures keep the existing generic sentence. Redeeming your own code again gives "already
  active on your account" and extends nothing.

**Expiry of unused vouchers:** `PREMIUM_VOUCHER_VALIDITY_DAYS` (default **365**) after
generation. The default is longer than single codes (90 days) because a printed batch is sold
over months.

## 8. Administration (`/admin/premium-vouchers`, linked from Settings for admins)

- **Batches:** batch id, created, by, codes, and live counts of unused, redeemed, revoked and
  expired.
- **Records:**
  - filter by status (all, unused, redeemed, revoked, expired) and by batch id;
  - each row shows the record number, the last 4 characters, status, months, the redemption
    time, the account that redeemed it, and Premium until;
  - the newest 300 are shown.
- **Revoke:** a single unused voucher, or "Revoke unused" for a whole batch, with a browser
  confirmation. Redeemed vouchers are never revoked: the conditional `UPDATE` cannot match
  them.
- **Statistics CSV** (`/admin/premium-vouchers/stats.csv`): one row per batch, with no codes
  and no students.
- **Audit log:**
  - `voucher_batch_issued` (admin, batch, count, months);
  - `code_redeemed`, `code_redeem_failed` (reason, hashed source);
  - `code_revoked`, `voucher_batch_revoked` (admin, batch, count).

**Security summary:**
- admin check on every endpoint (404);
- CSRF on every POST (Flask-WTF, tested);
- server-side validation;
- rate limits on generation and redemption, plus the database failure limit;
- atomic claim, unique hash and unique redemption;
- `secrets` randomness;
- HMAC with a pepper;
- generic errors;
- no plaintext in logs or responses (tested);
- no export file on the server;
- students see only their own redemptions (tested).

Security is not guaranteed. Section 10 lists the known limits.

## 9. Production (Oracle Cloud: `/home/opc/learnova`, `/opt/learnova-venv`, `learnova-app.service`, PostgreSQL)

Do these only when you authorise the deployment. No secret is printed here.

1. **Generate the pepper on the server** (it is not stored in your shell history):

   ```bash
   /opt/learnova-venv/bin/python -c "import secrets; print(secrets.token_hex(32))"
   ```

   This gives 64 hex characters. Keep a copy in your password manager: **if the pepper is lost
   or changed, every unredeemed voucher stops working.**
2. **Add it to `/home/opc/learnova/.env`** with an editor, not with `echo`:

   ```bash
   nano /home/opc/learnova/.env      # add the line: PREMIUM_VOUCHER_PEPPER=<the 64 characters>
   # optional: PREMIUM_VOUCHER_VALIDITY_DAYS=365
   ```

3. **Set the permissions:**

   ```bash
   chown opc:opc /home/opc/learnova/.env
   chmod 600 /home/opc/learnova/.env
   ```

4. **Before the deploy:** back up the database and rehearse migrations 043–049
   (`docs/OPERATIONS.md`):

   ```bash
   pg_dump --format=custom --no-owner --file "learnova-$(date +%Y%m%d-%H%M).dump" "$DATABASE_URL"
   ```

   Then restore the dump into a throwaway PostgreSQL 16 database and start the new code
   against it once:

   ```bash
   DATABASE_URL=<copy> /opt/learnova-venv/bin/flask --app app shell -c ''   # or the start command
   ```

   Then check:

   ```bash
   psql <copy> -c "SELECT version FROM schema_migration ORDER BY version;"
   ```

   The list must include `049_premium_vouchers`, and `\d premium_key` must show `batch_id`.
5. **Deploy and restart** (the migration runs at start-up):

   ```bash
   sudo systemctl restart learnova-app.service
   sudo systemctl status learnova-app.service --no-pager
   journalctl -u learnova-app.service -n 50 --no-pager
   ```

   A short pepper stops the start with a clear message.
6. **Test without a customer's code:**
   - Register a test account (for example `voucher-test`).
   - As admin, open Settings → Premium vouchers, generate **10** codes with the note
     "TEST - do not sell", and download the TXT.
   - Redeem one code with the test account: the success message appears, and Settings shows
     Premium until one calendar month later.
   - Redeem the same code with another account: it gets the generic error.
   - Back on the admin page: the batch shows 1 redeemed and 9 unused. Press "Revoke unused" and
     confirm: 9 revoked.
   - Delete the TXT file. Optionally end the test account's Premium (`flask premium expire`
     after its date, or leave it).

**Rollback:**
- Remove `PREMIUM_VOUCHER_PEPPER`. Batch generation then switches off, single codes go back to
  v2, and **existing vouchers can no longer be redeemed** until the pepper is restored.
- A code rollback is safe: the new column and table are unused by the old code.

## 10. Known limitations

- **Pepper:**
  - Changing or losing it invalidates every unredeemed voucher. There is no rotation (it would
    need the plaintext).
  - It sits in `.env` with the other secrets. A server compromise exposes it, together with the
    database.
- **Admin page:** shows at most the newest 200 batches and 300 records; filters narrow it down.
- **No payment link for batches:** batch codes are sold later for cash, so no payment is
  recorded per code. Record sales outside Learnova, or use the single-code page, which records
  the payment.
- **Copy and download need JavaScript;** without it, copy from the text field.
- **The failure limit** counts per account and per hashed address. Many accounts from many
  addresses can try more guesses in total. At 140 bits this cannot find a code; it only costs
  the server work.
- **PostgreSQL:** the migration and the race test ran on SQLite only. The `FOR UPDATE` and
  conditional `UPDATE` behaviour on PostgreSQL is the same code as the existing single codes,
  which run in production today, but it is not re-tested here.

## 11. Tests

**`tests/test_premium_vouchers.py`: 31 passed.**
- **Format:**
  - 140 bits, no I/L/O/U, 2000 unique;
  - normalisation of case, spaces, prefix and look-alikes;
  - the HMAC matches the definition, differs per pepper, and is never plain SHA-256;
  - a short pepper is refused;
  - calendar months: 15 Jan → 15 Feb; 31 Mar → 30 Apr; 31 Aug → 30 Sep; 31 Jan → 28 Feb (2026)
    and 29 Feb (2028); 15 Dec → 15 Jan.
- **Generation:**
  - batches of 10, 25, 50, 100 and a custom 37, plus the 500 maximum;
  - invalid quantities refused: 0, custom 0, 501, abc, empty, 7, −5, 1e3;
  - a duplicate draw is drawn again;
  - only keyed hashes in the database (the raw SQLite file is searched for every code and body);
  - codes never shown again (admin page, statistics CSV);
  - no pepper → no batch;
  - generation is audited without codes.
- **Redemption:**
  - one calendar month, plus the redemption row;
  - an active subscription is extended, not replaced;
  - invalid, redeemed and revoked vouchers fail the same way;
  - the same student twice extends once;
  - 6 students at once on one voucher → exactly 1 redemption and 1 entitlement;
  - an unauthenticated request changes nothing;
  - a failure mid-redemption rolls everything back, and the voucher still works afterwards;
  - brute force is stopped after 5 failures, even for a valid voucher;
  - old v2 codes still work with the pepper set;
  - a single code issued with the pepper is a voucher;
  - the Settings field takes a whole voucher.
- **Administration and security:**
  - students and moderators get 404 on all 5 admin endpoints; anonymous users go to the login
    page;
  - batch statistics and filters (status, batch), with who redeemed;
  - revoking unused never touches redeemed vouchers, and is audited;
  - CSRF (a form without its token is sent back and does nothing, on generation and on
    redemption);
  - the pepper never appears in pages or logs, and no code appears in logs;
  - a student sees only their own codes;
  - generation is rate limited (the 21st request in an hour generates nothing).
- **Migration 049:** an old table gets `batch_id` once, recorded once, and running it again
  changes nothing.

**Existing tests:** `tests/test_subscriptions.py` and `tests/test_i18n.py` pass unchanged (96
passed together with the voucher file). The catalogues gained 43 strings each (German, French,
Spanish; counted).

**Real browser** (Chrome, scratch server, at 390 px, 1440 px and 360 px):
- a batch of 25 generated;
- "Copy all" matches the codes on screen;
- the CSV download (header `activation_code,batch_id,duration`, 25 rows) and the TXT download
  match them;
- none of them appears after a reload;
- a student redeemed one in Settings and saw "Premium erfolgreich aktiviert! Dein
  Premium-Zugang wurde um einen Monat verlängert. Premium läuft bis 14.12.2026 …";
- the admin list shows the redemption;
- a non-allowlisted account gets 404;
- no horizontal overflow at 360/390/1440 px;
- the code list and tables were fixed to scroll inside their card on phones.

**Full regression suite (2026-11-14, after the audit fixes, Reliability V1, vouchers, community filters and readability together):** 3754 passed, 11 skipped, 0 failed (52 min). `instance/` was unchanged.

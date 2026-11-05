# Contact page and contact messages

Learnova has a public **Contact us** page at `/contact`. It works without an account and
is linked from the footer of every page, the navigation menus, the login and register
cards and **Settings → Help and information**. The public address is
`contact.studylearnova@gmail.com` (`CONTACT_EMAIL`), shown as a `mailto:` link.

> **This does not create or verify the Gmail mailbox; the owner must own it.** Changing the
> address in the code or in the configuration does not set up, migrate or forward any
> mailbox. Nothing in this repository proves that the mailbox exists or who can read it.

## How it works

1. `GET /contact` renders the form: name, email address, topic (8 categories) and message.
   `?topic=<key>` preselects a topic (`bug`, `technical`, `suggestion`, `account`,
   `subscription`, `general`, `partnership`, `other`). A signed-in student gets their
   account email prefilled; the name is never prefilled.
2. `POST /contact` checks, in this order:
   - **Honeypot**: a hidden `website` field (hidden by `contact.css`, never inline). If it
     is filled, nothing is stored and the page asks the sender to try again or to email us.
   - **Form timing token**: `form_started` is an HMAC over the moment the form was
     rendered and a random nonce kept in the session (`learnova/contact/service.py`).
     Missing, forged, older than 24 hours or rendered for another session means the same
     refusal; younger than `CONTACT_MIN_FILL_SECONDS` (default 3) asks the sender to check
     the message and send it again. The nonce is dropped once a message is accepted, so
     each rendered form is sent once; a form left open in a second tab is refused once
     (text kept, fresh form) and then goes through.
   - **Validation** on the server (`learnova.contact.validate`), after normalisation
     (control characters removed, whitespace collapsed in the name, CRLF folded to LF):
     name 1–100 characters, one plain ASCII email address up to 254 characters, a known
     topic, a message of 10–5000 characters. Errors are shown next to each field and in a
     summary, and everything typed is kept (escaped).
   - **Duplicate guard**: the same mailbox and the same message text (case and spacing
     ignored) within `CONTACT_DUPLICATE_WINDOW_MINUTES` (default 10) is not stored again;
     the sender still sees the confirmation, because that message was already stored.
   - **Database ceilings**: at most `CONTACT_MAX_PER_EMAIL_DAY` (5) messages per mailbox
     per day and `CONTACT_MAX_PER_HOUR` (30) messages site-wide per hour.
   - "Mailbox" is `learnova.contact.email_key`: the address in lower case without a
     `+tag` (`bob+1@` and `bob+2@` count as `bob@`), and for `gmail.com`/`googlemail.com`
     also without dots. The address is stored as written, so the reply goes to it.
3. The message is **stored first** (`contact_message` table, status `pending`), and only
   after the commit is forwarding attempted. A storage failure shows "Your message could not
   be saved right now…" and keeps the text; the confirmation is shown only for a stored
   message.
4. The browser is redirected (303) to `/contact/sent`, which shows the confirmation once.
   The session holds only a bare flag and the random form nonce, never personal data.

Additional protection:

- **CSRF**: the existing global `CSRFProtect`; the form carries `csrf_token`. An expired
  token uses the existing "Your form expired…" handler.
- **Rate limit**: `POST /contact` is limited per client address by Flask-Limiter with
  `CONTACT_RATE_LIMIT` (default `10 per hour`, like `/register`). A value Flask-Limiter
  cannot parse stops the app at start-up (otherwise it would limit nothing). Behind a
  proxy without `ProxyFix`, every visitor may share one address, so this limit can act
  site-wide; the database ceilings and the honeypot carry the steady protection.
- **Duplicate clicks**: `core.js` already disables the submit button on submit.
- `static/js/contact.js` keeps the topic and message (never the name or email) in
  `sessionStorage`, so text survives the two redirects that drop the POST body (expired
  form, too many requests). It is removed after a successful send.
- Contact pages never carry ads (`AD_FREE_ENDPOINTS`) and are sent with
  `Cache-Control: no-store, private`.
- No IP address, user agent or other device data is stored with a message.

## Email forwarding (SMTP)

Forwarding uses the existing mailer (`learnova/notifications/mailer.py`, standard-library
`smtplib`). It is **off until `MAIL_SMTP_HOST` and `MAIL_FROM` are set**; until then every
message is stored with status `not_configured` and listed in the admin inbox. To forward
messages to `contact.studylearnova@gmail.com`, set these in the Render dashboard (never in
`render.yaml` or the repository):

| Variable | Value for Gmail |
| --- | --- |
| `MAIL_SMTP_HOST` | `smtp.gmail.com` |
| `MAIL_SMTP_PORT` | `587` |
| `MAIL_SMTP_SECURITY` | `starttls` |
| `MAIL_SMTP_USERNAME` | the sending Gmail address |
| `MAIL_SMTP_PASSWORD` | a Google **app password** (Google account → Security → App passwords) |
| `MAIL_FROM` | the sending address |
| `PUBLIC_BASE_URL` | the site address, for the inbox link in the email |
| `CONTACT_EMAIL` | optional; defaults to `contact.studylearnova@gmail.com` |

The forward is plain text in English. Its subject is `[Learnova contact] <topic> #<id>` and
never contains anything the sender typed. The sender's address is set as `Reply-To` only
when it is one plain address without whitespace or line breaks (header injection is not
possible); the name and message go only into the body.

After configuring, check with `flask contact list` (first line says whether email is
configured) and send stored messages with `flask contact retry`.

## Statuses

| Status | Meaning | Admin label |
| --- | --- | --- |
| `pending`, `sending` | stored, forwarding not finished | Waiting to be forwarded |
| `sent` | **the SMTP server accepted the message** – not proof it reached an inbox | Forwarded (accepted by the mail server) |
| `retry` | temporary failure (4xx, timeout, connection); retried after 1, 5, 30 minutes up to `MAIL_MAX_ATTEMPTS` | Forwarding will be retried |
| `failed` | permanent failure (authentication, 5xx, refused recipient) | Forwarding failed |
| `not_configured` | email is not configured; stored only | Stored only (email not configured) |

Logs carry the message id and status only, never the name, email or message.

## Admin inbox

`/admin/contact-messages` lists the latest 100 messages, newest first, with a reply
(`mailto:`) link, the status, a retry button and a delete button (with a confirmation
checkbox). The retry button resends `failed`, `retry` and `not_configured` messages, and a
message left in `sending` for more than 30 minutes (a restart during the send); a younger
`sending` message is left alone so it is never sent twice. Messages past the retention
period are deleted when the inbox opens and are never listed. It uses the existing operator allowlist `PREMIUM_ADMIN_IDENTIFIERS` (username
or email): everyone else gets 404, guests are sent to the login page. Community moderators
deliberately cannot read contact messages. Admins see a **Contact messages** link in
Settings → Help and information.

## Command line

```bash
flask contact list [--limit 20]   # id, time, topic, status, attempts, last error; no personal data
flask contact retry [--limit 20]  # forward stored, not-configured and due-retry messages
flask contact cleanup             # delete messages older than the retention period
```

There is no scheduler, as with the other retention jobs: run `retry` after configuring
email (or use the admin retry button). `list` and `retry` never show or forward a message
past the retention period.

## Retention and privacy

- Messages are deleted after `CONTACT_MESSAGE_RETENTION_DAYS` (default **90**, 7–730),
  whatever their status. Admins can delete a message earlier.
- Without a scheduler the deletion is enforced wherever it can be
  (`expire_contact_messages` in `app.py`): on each accepted message, on each visit to
  `/contact`, when the admin inbox opens or its retry button is used, in `flask contact
  list|retry|cleanup`, and once at every start of the app (a free Render instance restarts
  after each idle spell). Everything that reads messages also filters out expired ones, so
  a failed delete never shows one. A message can therefore outlive the period only while
  the app runs and nobody opens the contact page or the inbox.
- Copies forwarded to the Gmail mailbox are not covered by this retention. The contact page
  says so ("A copy may be forwarded to our inbox … That copy is not deleted
  automatically."), whether or not email is configured yet, because messages stored before
  it is configured are forwarded by `flask contact retry` once it is.
- A signed-in sender's message is linked to their account (`user_id`; the inbox shows the
  username), and the contact page says so to signed-in senders.
- Learnova has no account deletion yet; `contact_message.user_id` is `ON DELETE SET NULL`
  so it is ready for it.
- The page asks senders never to include passwords or other sensitive information, and
  says that a first name is enough.
- There is no privacy policy or imprint page. The contact page states what is used, why,
  that it is never public and when it is deleted; it makes no legal compliance claims.

## Configuration

| Variable | Default | Notes |
| --- | --- | --- |
| `CONTACT_EMAIL` | `contact.studylearnova@gmail.com` | must be an email address |
| `CONTACT_MESSAGE_RETENTION_DAYS` | `90` | 7–730 |
| `CONTACT_MAX_PER_EMAIL_DAY` | `5` | 1–100 |
| `CONTACT_MAX_PER_HOUR` | `30` | 1–1000 |
| `CONTACT_DUPLICATE_WINDOW_MINUTES` | `10` | 1–1440 |
| `CONTACT_MIN_FILL_SECONDS` | `3` | 0–60 |
| `CONTACT_RATE_LIMIT` | `10 per hour` | Flask-Limiter syntax, checked at start-up |
| `FOUNDER_NAME` | `Rithivik Velusamy` | shown on /about; up to 60 characters |
| `FOUNDER_INTRO` | empty | empty uses the translated default; a set value is shown as written in every language |

## Database

`contact_message` is a new table created by `db.create_all()`; migration
`039_contact_messages` only records when it arrived. Nothing existing is altered.

## Known limitations

- Only ASCII email addresses are accepted in the form; anyone can still use the `mailto:`
  link.
- Two POSTs within the same instant with JavaScript off can store two rows; the admin sees
  both.
- Forwarding runs on a background thread; a restart during a send leaves the row in
  `sending`, and `flask contact retry` or the admin retry button resets rows stuck for more
  than 30 minutes.
- Sessions are signed cookies, so a bot that replays its old session cookie can reuse one
  form token; the timing check then only delays its first post. The per-IP limit, the
  per-mailbox and site-wide ceilings and the honeypot carry the volume protection.

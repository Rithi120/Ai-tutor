# Login & security

Learnova accounts can sign in four ways:

- **Username or email plus password.** This existed before and is unchanged.
- **Google.** OpenID Connect, authorization code flow with PKCE.
- **Apple.** Sign in with Apple, OpenID Connect.
- **Phone number.** A 6-digit SMS code.

A sign-in method appears only when **all** of its settings are present (`.env.example`).
Nothing is simulated in production: with no SMS provider, phone login is simply not offered.

## Flows

**Google and Apple** (`learnova/authentication/oidc.py`, `oauth_start`, `oauth_callback` and `oauth_finish` in `app.py`):

1. The server creates an `auth_oauth_request` row. It holds the state (only its hash), the
   nonce and the PKCE verifier (Google only). A random browser cookie (`learnova_oauth`) is
   stored as a hash next to them.
2. The browser goes to the provider's consent page (scopes `openid email profile`; Apple: `name email`).
3. The callback checks state, expiry, single use and the browser cookie. It exchanges the
   code server-to-server and verifies the ID token: the RS256 signature against the
   provider's published keys, the issuer, the audience, the expiry, issued-at and the
   nonce. Only `sub`, the email, `email_verified` and the first name are kept; the access
   and refresh tokens are thrown away.
4. A same-site GET (`/finish`) then signs in, links, or starts onboarding. This step exists
   because Apple answers with a cross-site POST, which carries no `SameSite=Lax` session cookie.

| Situation | What happens |
|---|---|
| The identity is already linked | Sign-in to that account. |
| Its email matches an existing account | **No link.** The student is told to sign in the usual way and connect the provider under Settings → Login & Security. |
| Otherwise | Onboarding. |

**Phone** (`learnova/authentication/phone.py`, `/auth/phone`, `/auth/phone/code`):

- **Number:** normalised to E.164 by `phonenumbers`, with Germany (+49) as the default country.
- **Code:** six digits, valid 5 minutes, used once. Only an HMAC keyed with `SECRET_KEY` is stored.
- **Attempts:** at most 5, counted atomically before checking.
- **Limits:** a 60 s resend cooldown, 5 codes per number an hour and 10 a day, 20 per IP an
  hour, plus Flask-Limiter on the routes.
- **No enumeration:** the answer is the same whether or not the number has an account.
- **Failed SMS:** the student is told, and that code can never be used.

**Onboarding** (`/auth/onboarding`) has three steps:

1. Name and username (checked live, with the same rules as a rename: reserved,
   offensive, taken ignoring case, held).
2. Class, language and optional favourite subjects.
3. Welcome.

No `user` row exists until step 3, so an unfinished sign-up can't reach the dashboard. The
dashboard pages also redirect back to onboarding. Signing in again with the same
identity resumes the saved step (`auth_pending_signup`, 7 days).

## Linking (Settings → Login & Security)

- **Connect Google or Apple:** a full provider sign-in with `purpose=link`. An identity
  already linked to another account is refused.
- **Phone:** add or change through an SMS code.
- **Password:** set one, or change it with the current password.
- **Recent sign-in:** sensitive changes need a sign-in in the last 15 minutes
  (`AUTH_REAUTH_MINUTES`) or the password typed again.
- **Disconnecting** needs a ticked confirmation. **The last way to sign in can never be removed.**
- **Nothing else changes:** linking never touches the user id, XP, achievements, Premium,
  projects or leagues (tested).
- **Constraints:** `user_auth_identity` has unique (provider, subject) and (user, provider)
  constraints, so an identity can never belong to two accounts.
- **Audit:** every link, unlink, sign-up, password change or reset and email confirmation
  is in `auth_audit_event`, without secrets.

## Accounts without a password or an email

- `user.email` and `user.password_hash` stay `NOT NULL`; the `user` table was not rebuilt.
- An account without a password stores `!unusable`, which no password matches.
- A phone-only account gets `<random>@users.invalid`. That domain is reserved, can never
  receive mail, and is never shown.

## Messages (`account_notification`, `learnova/authentication/notices.py`)

**Welcome:**
- One per account (unique `dedupe_key`), by email when there is a real address, including
  Apple's relay address.
- For a password sign-up, the same email carries the confirmation link. The email counts as
  verified only after that link (or a password reset) is used.
- A welcome SMS for phone sign-ups exists but is off by default (`AUTH_WELCOME_SMS`).

**Security alerts:**
- Sent for a new device, a password change, an account recovery, linking or unlinking a
  provider, and phone changes.
- "New device" uses a random cookie id (hashed) and a label like "Chrome · Android". No IP
  address or location is stored. The first browser of an account never raises an alert,
  and ordinary sign-ins never do.
- At most 6 security messages per account an hour (`AUTH_SECURITY_EMAILS_PER_HOUR`); more
  are stored as `suppressed`.
- An account without email gets security messages by SMS to its verified number (`AUTH_SECURITY_SMS`).

**Delivery and contents:**
- Delivery runs in the background through the existing SMTP mailer and the retry logic of
  the community alerts.
- `status` is `sent` only when the server or provider accepted the message;
  `not_configured` means nothing was sent.
- `flask --app app auth deliver` retries the waiting messages; `flask --app app auth
  cleanup` removes expired requests and codes.
- Messages never contain a password, a code (except the code SMS), an OAuth code or a
  token. Links are signed (`itsdangerous`) and made at send time, so no working link sits
  in the database.
- Reset links expire after 60 minutes and stop working after use (bound to the password hash).
- All messages are transactional or security messages. There is no marketing message, and
  `notices.may_send` would refuse one without consent.

## Setup checklist (the owner)

1. Set `PUBLIC_BASE_URL` to the https address. It builds the redirect URIs and the email links.
2. **Google Cloud Console:** create an OAuth client of type "Web application", add the
   redirect URI `<PUBLIC_BASE_URL>/auth/google/callback`, and set `GOOGLE_CLIENT_ID` and
   `GOOGLE_CLIENT_SECRET`. Publish the consent screen.
3. **Apple Developer:** create a Services ID (`APPLE_CLIENT_ID`) with the domain and the
   return URL `<PUBLIC_BASE_URL>/auth/apple/callback`, and a "Sign in with Apple" key
   (`APPLE_KEY_ID`, with the `.p8` contents in `APPLE_PRIVATE_KEY`). Set `APPLE_TEAM_ID`.
   To receive mail at private relay addresses, register the `MAIL_FROM` domain in Apple's
   "Private Email Relay" settings.
4. **SMS:** set `SMS_PROVIDER` to `twilio` or `vonage`, plus `SMS_API_KEY`, `SMS_API_SECRET`
   and `SMS_SENDER`. Check the provider's sender-ID rules for Germany.
5. **Email:** set `MAIL_SMTP_HOST` and `MAIL_FROM` (docs/OPERATIONS.md). Without them,
   welcome and security emails are stored as `not_configured`.
6. Schedule `flask --app app auth deliver` and `flask --app app auth cleanup`, for example
   every 10 minutes and daily.
7. Test each provider end to end on the real domain. The automated tests sign and verify
   real tokens but cannot reach Google, Apple or an SMS provider.

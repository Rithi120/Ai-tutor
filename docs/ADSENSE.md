# Google AdSense: ads, consent and the Content Security Policy

Learnova shows Google AdSense ads to users without Premium, once a certified consent
message is set up. Premium accounts never get ads. This page covers what the code does,
what changed and why, and what only the AdSense account owner can do.

Code: `learnova/web/security.py` (headers), `ads_enabled()` in `app.py` (who gets ads),
`templates/base.html` (the tags), `learnova/config.py` (`_configure_ads`), tests in
`tests/test_ads_csp.py`.

## Why ads never loaded (root cause, found 2026-10-29)

The "Adsense" commit (297172d) added the AdSense script to `base.html`, but the Content
Security Policy in `learnova/web/security.py` was never changed. Its
`script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net` does not allow
`https://pagead2.googlesyndication.com`, so every browser refused the script. Every page
logged:

> Loading the script 'https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?...'
> violates the following Content Security Policy directive

Even with the script allowed, three more things would have blocked AdSense:

- **Ad frames, pixels and requests:** `default-src 'self'` (with no `frame-src`,
  `img-src` or `connect-src` for Google) blocks them.
- **The consent message:** `Referrer-Policy: same-origin` blocks it. Google's message is
  not shown when the `Referer` header is not sent cross-origin.
- **No consent management at all:** no message, no TCF. Since 16 January 2024, Google
  requires a Google-certified CMP that integrates with the IAB TCF to serve
  (personalised) ads in the EEA and UK, and since 31 July 2024 in Switzerland.

The repository has no reverse-proxy configuration and no `<meta>` CSP. The Oracle
production host's proxy is not in the repository: check that it does not add its own CSP
or Referrer-Policy header.

## What the policy is now

A nonce changes on every request. Every `<script>` tag carries it; `'strict-dynamic'`
lets those scripts load further scripts (KaTeX, and Google's ad code, which loads helpers
from domains Google says change over time).

### Every page, ads or not

| Directive | Before | Now |
|---|---|---|
| `script-src` | `'self' 'unsafe-inline' https://cdn.jsdelivr.net` | `'nonce-…' 'strict-dynamic' 'self' https://cdn.jsdelivr.net` |
| everything else | unchanged | unchanged |

This is stricter than before: an injected inline `<script>` or `onclick=` no longer runs.
CSP3 browsers ignore `'self'` and the jsDelivr host next to `'strict-dynamic'`; they
remain for CSP2-only browsers. To make this work:

- all 42 template `<script>` tags got the nonce;
- the two inline `onclick` confirmations in `recognition_review.html` became
  `data-confirm` attributes;
- the camera scanner no longer `document.write`s a returned page, because the written page
  would carry the wrong nonce.

### Only on a response whose page loads the ad code

Google documents a single supported policy for AdSense (support.google.com/adsense/answer/16283098):
`object-src 'none'; script-src 'nonce-{random}' 'unsafe-inline' 'unsafe-eval' 'strict-dynamic' https: http:; base-uri 'none'`,
with "More restrictive policies may break without notice". Google supports no domain
allowlist, "because the domains that the AdSense ad code uses change over time". On ad
pages only, Learnova adds:

| Directive | Added | Why |
|---|---|---|
| `script-src` | `https:` | The fallback a CSP2-only browser needs to load Google's helper scripts; CSP3 browsers ignore it next to `'strict-dynamic'`. |
| `script-src` | `'unsafe-eval'` | Part of Google's only supported policy. Switch off with `ADSENSE_CSP_UNSAFE_EVAL=false` to test without it. |
| `frame-src` | `'self' https:` | Ad iframes and the consent message frame come from Google and partner domains with no published list. |
| `img-src` | `https:` | Ad pixels. |
| `connect-src` | `https:` | Ad, consent and measurement requests. |
| `Referrer-Policy` | `strict-origin-when-cross-origin` (instead of `same-origin`) | Required for the consent message. Sends the origin only, never a path. |

These only govern what may load; scripts still run only with the nonce. Not taken over
from Google's example:

- `'unsafe-inline'`: every browser that supports nonces ignores it.
- `http:`: on an HTTPS page it is blocked as mixed content anyway.
- `base-uri 'none'`: Learnova keeps `'self'`, which Google's "more permissive is fine"
  allows.

Pages without the ad code keep the strict policy and `same-origin`: Premium, signed-in
students (by default), operator pages and API responses.

`CSP_REPORT_URI` adds a `report-uri` to every policy, so CSP violations reach a collector
you run. It is empty by default; Learnova has no collector of its own.

Unrelated fix made at the same time: `Permissions-Policy` had `microphone=()`, which
refused the microphone on every page and broke speech input (it uses `SpeechRecognition`).
It is now `microphone=(self)`, like `camera=(self)`, so no embedded frame, ads included,
can use either.

## Who gets ads

`ads_enabled()` in `app.py` decides, per page. The ad code loads only when all of these
hold:

1. **A publisher id is set:** `ADSENSE_CLIENT_ID`. The default is the one already in the
   repository, `ca-pub-5205912301427741`.
2. **A certified consent message is confirmed:** `ADSENSE_CMP=google_privacy_messaging`.
   Empty by default, so by default no page loads any ad code.
3. **The page is not an operator tool:** `/admin/premium-keys`, `/internal/*`.
4. **The visitor does not have Premium**, checked against the database on every request.
   An expired Premium gets the free behaviour again.
5. **The visitor is signed out, or ads for signed-in students are switched on:**
   `ADSENSE_SHOW_TO_STUDENTS=false` by default. See "Minors" below.

Always present: the verification tag
`<meta name="google-adsense-account" content="ca-pub-…">` (it loads nothing) and
`/ads.txt`: `google.com, pub-5205912301427741, DIRECT, f08c47fec0942fa0`.

## Consent: what exists and what does not

- **What the code does.** Learnova uses Google's own certified CMP, the "European
  regulations message" in AdSense → Privacy & messaging. Google states it is certified
  under the TCF requirement. The message is delivered by the AdSense code itself.
  Learnova does not build or host a consent banner, and invents no consent settings.
- **What the code cannot do.** Creating and publishing that message happens in your
  AdSense account. Until you confirm it (`ADSENSE_CMP`), Learnova loads no ad code, so
  no ad is requested without the certified message in place.
- **A different CMP.** No other CMP is integrated. A third-party CMP would need its own
  script, CSP review and a new `ADSENSE_CMP` value.

### Minors

Learnova's students are mostly at school, many under 16, the age of digital consent in
Germany (GDPR Art. 8). Consent from a child under 16 is not valid without a parent's.
That is why ads for signed-in students are off by default.

Before switching on `ADSENSE_SHOW_TO_STUDENTS`, decide (with legal advice) how you handle
this. Google offers age treatment for ad requests (`data-tag-for-age-treatment`, "child"
or "teen", which disables personalised ads). It is set per ad unit, on the `<ins>` ad code
from your AdSense account. Learnova uses Auto ads with no ad units, so it cannot set it
today, and no ad unit id is invented here.

Signed-out visitors (About, login and register pages) may also be minors. The consent
message covers them under the TCF; whether that is sufficient for your audience is also
part of that decision.

## Steps that remain for AdSense approval and launch

1. **Site in AdSense.** Add the site in AdSense (Sites) and verify it. The meta tag above
   is on every page, and `/ads.txt` is served.
2. **Privacy policy.** Write a privacy policy (Datenschutzerklärung) that covers
   advertising and cookies, and publish it. Learnova has no privacy-policy page yet. The
   consent message must link to it ("Google EU User Consent Policy"). Germany also
   expects an Impressum. Neither text is written here.
3. **Consent message.** In AdSense → Privacy & messaging, create and publish the
   European regulations (GDPR) message for this site, with the privacy policy URL.
4. **Minors.** Decide how to handle them; see above.
5. **Configure and deploy.** Set `ADSENSE_CMP=google_privacy_messaging` (and, only after
   step 4, `ADSENSE_SHOW_TO_STUDENTS=true`).
6. **Test on staging.** In a browser console, check that the consent message appears for
   an EEA visitor and that no CSP violation is logged. Google recommends trying a new
   policy with `Content-Security-Policy-Report-Only` first; `CSP_REPORT_URI` can collect
   reports.
7. **Production proxy.** Check that the proxy on the production host does not override the
   CSP, Referrer-Policy or Permissions-Policy headers.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ADSENSE_CLIENT_ID` | `ca-pub-5205912301427741` | Publisher id for the ad code, the meta tag and `ads.txt`; empty removes all three |
| `ADSENSE_CMP` | empty | `google_privacy_messaging` once the European regulations message is published |
| `ADSENSE_SHOW_TO_STUDENTS` | `false` | Ads for signed-in students without Premium |
| `ADSENSE_CSP_UNSAFE_EVAL` | `true` | Keep `'unsafe-eval'` on ad pages (Google's supported policy) |
| `CSP_REPORT_URI` | empty | An `https://` collector for CSP violation reports |

A bad value stops the start: an unknown CMP, a malformed publisher id, or a non-https
report URI.

## Verified and not verified

- **Verified by tests (`tests/test_ads_csp.py`):**
  - every script tag carries the request's nonce;
  - the nonce changes per request;
  - the exact directives added on ad pages, and the unchanged rest;
  - no ad code without `ADSENSE_CMP`;
  - students only when switched on;
  - operator pages and Premium never;
  - expired Premium is free again;
  - API responses stay strict;
  - `ads.txt` and the meta tag;
  - configuration validation;
  - no template with an inline handler, a `javascript:` URL or a script without a nonce;
  - no `document.write`.
- **Verified in Chrome:** the mobile layout audit renders every page under the new
  policy and fails on any console CSP violation.
- **Not verified:** Google's live ad code under this policy. Running it would request ads
  for the real publisher id from a test machine, which can count as invalid traffic.
  Check it on staging (step 6).

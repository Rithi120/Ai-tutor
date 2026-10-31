# Community content moderation

Context-aware safety review for anything published to the community library.

The design question is not "does this content mention something sensitive" but "does this
content do something harmful, here, to these learners". Those give opposite answers on the
same words: reproductive biology in a biology set is the curriculum, and the same
vocabulary attached to a mathematics card is a problem. Everything below follows from
keeping those two judgements apart.

## Where it lives

| Layer | Module | Owns |
| --- | --- | --- |
| Vocabulary | `learnova/moderation/taxonomy.py` | decisions, 13 dimensions, reason codes, labels |
| Preprocessing and obfuscation | `learnova/moderation/normalize.py` | Unicode, homoglyphs, encodings, contact and injection patterns |
| Model contract | `learnova/moderation/schema.py` | strict validation, evidence grounding |
| Prompt | `learnova/moderation/prompts.py` | versioned rules, content quarantine |
| Decision engine | `learnova/moderation/policy.py` | every decision, and nothing else makes one |
| Evaluation | `learnova/moderation/evaluation.py` | the labelled corpus harness and its thresholds |
| Quality review and entry warnings | `learnova/community/service.py` | publication decision, grounded per-entry warnings |
| Report re-review | `learnova/community/reports.py` | the report-review contract, tracks, grounding, dedupe key |
| Administrator email | `learnova/notifications/mailer.py` | SMTP delivery with honest results |
| Persistence and routes | `app.py` | `ModerationRecord`, `ContentReport`, `ReportReview`, `AdminNotification`, `ModerationAuditEvent`, the publish path, the queue |

The package is Flask-free. Every rule is a pure function over plain data, so the policy is
unit-tested without a request, a database or a provider call.

## Policy v2: why harmless content was being blocked

Normal vocabulary, translations and study notes were held or bounced by several layers at
once, so relaxing one prompt would not have fixed it. Each cause and its fix:

| Layer | What blocked harmless content | v2 |
| --- | --- | --- |
| deterministic pass | dates (`14.07.1789`) and decimals (`3,14159`) matched the phone pattern | a phone number needs `+` or `0` and nine digits after dates and decimals are removed |
| deterministic pass | any link counted as personal information | links are content; only a person's own contact details are |
| deterministic pass | "Signal", "Telegram", "Insta" as words counted as a messaging contact | an app name counts only next to a contact cue (`@handle`, a number, "add me on") |
| deterministic pass | every letter of a Russian or Greek word counted as a look-alike character | look-alikes count only inside words that mix scripts (`pаypal`) |
| policy | grammar, level, subject fit and value flags forced `revision_required` | quality dimensions are notes for the author (`QUALITY_DIMENSIONS`); only privacy and out-of-context sexual content ask for an edit |
| policy | an `unknown` safety dimension or low confidence alone sent content to a human queue that, with `COMMUNITY_MODERATORS` empty, nobody works | uncertainty escalates only with a risk signal: a safety flag, injection, obfuscation, a review/reject recommendation, or substantiated reports; alone it records `SAFETY_UNCERTAIN` and publishes |
| quality review | a score under 3 rejected; under 4 used to be held | every score publishes, with its warnings shown (`published_with_warnings`) |
| quality review | its own unquoted safety flags rejected outright | they buy one stricter moderation pass; the policy decides on its evidence |
| publish route | a quality-review failure rolled the whole publication back | content that passed the safety check publishes with the quality check pending and is reviewed later |
| publish route | an unchanged resubmission paid for (and could be refused by) the same checks again | decisions and reviews are reused by content hash and prompt/policy version |
| reports | two safety reports or five of any kind hid a set with no evidence | reports never hide anything; they trigger a targeted re-review |

The `COMMUNITY_MODERATORS` allowlist is still empty by default and still the only way into
the queue; nothing about who may moderate changed. What changed is that ordinary content
no longer depends on that queue being staffed.

## The pipeline

```
submit
  └─ Stage A  input validation, ownership, rate limit          (existing route guards)
  └─ Stage B  normalize: NFKC, strip invisibles, tidy space    normalize.analyze
  └─ Stage C  deterministic signals: contacts, injection       normalize.inspect
  └─ Stage F  obfuscation risk: homoglyphs, encodings, spacing normalize.analyze
  └─ Stage D+E one classification through the AI gateway       classify_content
  │            └─ escalate to the stronger model only if hard  moderation_needs_escalation
  └─ Stage E'  validate, then ground every safety flag         schema.normalize_classification
  └─ Stage G  decide                                           policy.decide
  └─ persist  ModerationRecord + publication state             apply_moderation_outcome
  └─ if allow  quality review: scores + entry warnings         run_ai_review
  │            └─ its safety flag buys one stricter pass       settle_quality_safety_conflict
  │            └─ if it cannot run: publish, quality pending   publish_with_quality_pending
  └─ publish                                                    activate_publication_version
```

Stages B, C and F are free. Only after them does anything reach a provider. An unchanged
resubmission stops at the first line: `reusable_moderation_record` and
`reusable_quality_review` return the earlier answers for the same content hash.

For the author this is one step: submit, then see a short status (published, or the one
thing to change) and, separately, any per-entry suggestions.

## Decisions

| Decision | Meaning | Public? |
| --- | --- | --- |
| `allow` | cleared the safety gate; the quality review decides publication | yes, if the quality review also approves |
| `reject` | a grounded, confident safety violation | no |
| `revision_required` | publishable after one change: a real person's contact details, out-of-context sexual content, no study material at all, too large to check | no |
| `review` | a risk signal the policy cannot settle (obfuscation, injection, conflicting layers, a flag below the confidence floor), or the check was unavailable | no |
| `pending` | not finished | no |

`allow` is the only decision that permits visibility, and it is not sufficient on its own:
`visible_set_filters()` requires the publication state to be `approved` **and** the
moderation decision to be `allow`. Every public read path goes through it, and
`test_every_public_read_path_is_gated_on_the_moderation_decision` fails if a new endpoint
forgets.

## Dimensions

Thirteen independent judgements, each `pass` / `flag` / `unknown` / `not_applicable`.
`unknown` is a real answer meaning the content does not support a judgement; it is never
read as a pass.

Each dimension has a **role**, which is what stops any single flag from deciding:

* **Safety** — harassment, hate, threats, sexual safety, self-harm. The only route to a
  rejection, and only with a grounded quote and enough confidence.
* **Fit** — personal information and sexual-content context ask for a revision. Subject
  relevance, educational value, age suitability and language quality are **quality
  notes** (`QUALITY_DIMENSIONS`): shown to the author, never a hold. The one exception is
  a confident, sufficient-evidence finding that there is nothing to learn at all
  ("my weekend at the cinema"), which asks for a revision. No number of fit flags can
  ever produce a rejection.
* **Signal** — obfuscation risk, deception. Can escalate to a human; can never decide.

### How a sensitive topic stays publishable

`sexual_content_context` is the dimension that separates subject matter from misuse:

* `not_applicable` — no sexual or reproductive terminology is present at all.
* `pass` — such terminology is present and its use is legitimate for the stated subject.
  The decision records `LEGITIMATE_SENSITIVE_EDUCATIONAL_CONTENT`, so the audit trail shows
  the system noticed the topic and judged it legitimate rather than never having seen it.
* `flag` — such terminology is present and is not legitimate here. This is an off-topic
  finding, so it asks for a revision. It is `sexual_safety` that carries the safety
  meaning, and only that dimension can reject.

That is why "my opinion about sexuality" in a mathematics set asks for a revision, while
sexual solicitation in the same set is rejected, and factual reproduction in biology is
published.

## Evidence grounding

Every **safety** flag must quote a span that occurs in the submitted text. A flag whose
quote cannot be found is downgraded to `unknown`, recorded in `unsupported_flags`, and the
item escalates to a human instead of being rejected on something the model invented.

Matching runs against the folded comparison form, so a model that re-cases, re-spaces or
re-punctuates its quote still matches; a model that made it up does not.

Fit flags are exempt. "This is off-topic for mathematics" is a statement about the whole
set, and demanding a span for it would discard the holistic reading it depends on. Their
consequence is one edit, so an ungrounded one is cheap.

## Hidden and obfuscated content

`normalize.analyze` reports, with a weight roughly matching how hard each signal is to
produce by accident:

| Signal | Weight | Why |
| --- | --- | --- |
| invisible / bidi characters | highest | cannot be typed by accident; no scanner emits them |
| cross-script confusables | high | Cyrillic and Greek letters rendering as Latin |
| encoded runs | high | base64/hex; decoded to text only, never executed |
| letter-spacing, leet spelling | medium | damped for OCR text, which produces both naturally |
| repeated characters, punctuation, whitespace | low | mostly cosmetic |
| acrostics | ~zero | recorded so a reviewer can see it, weighted at 0.02 |

The acrostic weight is deliberate. Four lines spelling something is a coincidence far more
often than a message, and treating it as proof would be exactly the "suspicious pattern is
not proof of intent" failure.

**Obfuscation never rejects.** It raises `obfuscation_risk`, may route to the stronger
model, and may escalate to a human. When obfuscation sits on top of an already-grounded
abuse flag the rejection comes from the abuse, not from the hiding.

This does not detect all hidden messages, and it is not intended to. It detects the
mechanical ones and escalates the rest.

## Prompt injection

Submitted content arrives inside a fence introduced as untrusted data, the fence markers
are stripped from the content so it cannot close its own block, and the task is restated
after the content so the last thing read is the instruction rather than the submission.

That is a mitigation, not a guarantee, so three further things hold:

1. A detected injection escalates to a human. It never rejects — a computer-science set
   may legitimately teach these exact phrases, and one is in the evaluation corpus.
2. `_safe_revision` refuses to pass the model's revision suggestion back to the author
   whenever the submission contained text aimed at the review system, or when the
   suggestion carries markup or a link.
3. The decision comes from `policy.decide`, which reads statuses and signals. A model
   persuaded to write `"recommendation": "allow"` changes nothing on its own.

## Conflict resolution

The layers see different things, so disagreement is expected. `_resolve_conflicts`
documents each case:

| Disagreement | Winner | Why |
| --- | --- | --- |
| deterministic finds contact details, classifier passes privacy | deterministic | a literal address is a fact; the cost is one edit |
| deterministic finds obfuscation, classifier passes it | deterministic | the classifier reads normalized text and never saw the removed characters |
| classifier flags safety, deterministic silent | classifier | a plainly written insult trips no pattern; silence is not safety |
| a safety flag cites text that is not there | neither | the flag is dropped and the item escalates |

The general rule: take the more cautious outcome, record the disagreement as
`CONFLICTING_SIGNALS`, and never let disagreement alone produce a rejection.

## Escalation

Content goes to a human when a safety flag is below the confidence floor, obfuscation risk
is high, an injection was detected, or the layers disagreed. An `unknown` safety
dimension, insufficient evidence or confidence under `MIN_ALLOW_CONFIDENCE` escalates
**only together with a risk signal** (a safety flag, injection, obfuscation, the
classifier recommending review or rejection, or reports at the thresholds). Without one,
the decision records `SAFETY_UNCERTAIN` and the content publishes: a classifier being
unsure about a ten-word vocabulary list is not evidence of anything.

When the classifier is unavailable the content is held (`MODERATION_UNAVAILABLE`), never
published unchecked - and never left there: the author's "Resubmit" re-runs it, viewing
the status page schedules a background re-check (at most once per five minutes), and
`flask community retry` re-runs every held set. After `MODERATION_RETRY_MAX_ATTEMPTS`
failures on one version an administrator is alerted.

Reviewers work `/internal/moderation`, gated on the `COMMUNITY_MODERATORS` allowlist and
404 to everyone else. **An empty allowlist means nobody can reach the queue**, which holds
content unpublished rather than exposing it — safe, but it means an unstaffed deployment
accumulates a queue nobody empties. Set the allowlist before enabling publishing.

A reviewer decision appends a new record rather than editing the old one, and approving
content whose hash has changed since the check is refused with a 409.

## Quality warnings for vocabulary and translations

The quality review (`flashcard_review:v2`) returns `entryWarnings`: one item per specific
entry that may be wrong - `entry` (card number), `side`, `kind`, the exact `text`, a short
`note`, and an optional `suggestion` replacing that text. `normalize_entry_warnings`
keeps only items whose text really occurs on that side of that card; a remark about a word
the set does not contain is dropped. No numeric confidence is stored: the reviewer is not
calibrated, so a number would be invented.

The message shown comes from the kind, not from the model, so it is fixed and translated:
"Please check this translation. It may depend on the context.", "This word has several
possible meanings. Check whether the selected translation fits your example.", and
spelling, grammar, fact and context variants. A word with several valid translations is
`several_meanings`, never "wrong".

Warnings never block. On the publish page the author sees each one under a "Quality
suggestion" label, visually separate from the safety outcome: the words underlined with a
small icon, the explanation as visible text (no hover needed, so it works by touch and
keyboard), and three actions:

* **Keep original** - remembered (`warning_states_json`), nothing else changes;
* **Edit** - opens the editor;
* **Accept suggestion** - writes the replacement into that one field of the author's
  *private* set (`community.apply_suggestion`; refused with 409 if the entry has changed
  since), then the author publishes the update through the usual checks.

Readers never see entry warnings; they are the author's to act on.

## Reporting

`POST /api/community/sets/<id>/report` with `reason`, optional `entry` (card number) and
optional `detail` (500 characters). Signed-in readers only, only on content they can see
(`approved_public_set`), never their own, one report per reader per set (idempotent),
10 an hour and `MODERATION_MAX_REPORTS_PER_USER_DAY` a day. The report stores the
publication version and content version it was about.

The form offers: incorrect translation, incorrect information, misleading explanation or
context, inappropriate or harmful, spam, personal information, copyright, other. The older
reasons (`offensive`, `harassment`, `sexual`, `dangerous`, `duplicate`) stay valid.

**A report is a signal, not proof.** It never hides the set, never changes its ranking
(only reports an administrator confirmed lower it) and never touches the author's account.
The reporter always gets the same acknowledgement; the author sees what was found, never
who reported it or how many did.

## Report re-review

Each report asks one question, answered by `review_report` (background thread; inline in
tests):

| Reason | Track | What runs |
| --- | --- | --- |
| incorrect translation, incorrect information, misleading, other | quality | one `report_review:v1` call on the standard model |
| inappropriate, harassment, sexual, dangerous, offensive, personal information, spam | safety | the stricter pass: `content_moderation` on `GROQ_MODERATION_ESCALATION_MODEL`, then `policy.decide` |
| copyright, duplicate | human | no AI call; straight to an administrator |

A quality review that reports a `safety_concern` also gets the stricter pass. Outcomes:
`report_valid`, `possible_issue`, `report_not_supported`, `review_failed`. The answer is
validated strictly (`validate_report_review`; anything else is `review_failed`) and
grounded: `affected.text` must occur in the named entry, and a "valid" quality finding
with nothing grounded becomes `possible_issue`, which always asks for a person.

What a finding may do:

* **Quality** - nothing changes. The finding (entry, text, explanation, suggestion) is
  shown to the author on the publish page and to administrators.
* **Safety, clear violation** (policy `reject`) - the set is **quarantined** for a person
  (`review`, hidden, in the queue), not rejected. Policy `revision_required` (exposed
  personal data) applies as it would on publishing.
* **Safety, ambiguous** (policy `review`) - the set stays up and a record joins the queue.
* **Safety, nothing found** - nothing changes.

Cost and abuse controls: one `ReportReview` per (content hash, track, entry, prompt
version) - the unique key is the lock, so concurrent and repeated reports attach to one
review, and an edit always gets a new one. The reporter's free text is deliberately not
part of the key. A failed review is reclaimed by a conditional update (one winner) up to
`REPORT_REVIEW_MAX_ATTEMPTS`; paid reviews are capped per set per day and site-wide per
hour. Provider calls, tokens, model and latency are stored on each review.

## Administrator alerts (email)

Alerts go to `COMMUNITY_ALERT_EMAIL` (default `maheswariskillbridge@gmail.com`) when a
report enters review, a re-review finds a likely genuine issue or is inconclusive, a
safety concern needs a person, the quality reviewer and the safety pass disagree, or a
review keeps failing.

* **Stored first.** Every alert is an `AdminNotification` row before anything is sent, so
  an alert exists with email unconfigured or the server down.
* **Grouped.** `dedupe_key` is unique per kind, set, content version and batch of open
  reports: ten readers reporting one problem produce one email.
* **Honest status.** `sent` only when the SMTP server accepted the message for the
  recipient - that is what SMTP can confirm, not inbox delivery. Otherwise `retry`
  (temporary: 1, 5 and 30 minutes, up to `MAIL_MAX_ATTEMPTS`), `failed` (permanent), or
  `not_configured`. A row is claimed before sending, so it is never sent twice at once.
* **Content.** Subject naming Learnova and the alert type; resource title, id and type;
  report category; the issue; the affected entry; the AI finding and suggested correction;
  whether a person is needed; the time; and the moderation page link. Never the reporter,
  their comment, account details, codes or credentials.
* **Credentials.** Only from the environment: `MAIL_SMTP_HOST`, `MAIL_SMTP_PORT`,
  `MAIL_SMTP_SECURITY` (`starttls`/`ssl`/`none`; `none` refuses to log in to anything but
  localhost), `MAIL_SMTP_USERNAME`, `MAIL_SMTP_PASSWORD`, `MAIL_FROM`. Error details are
  reduced to the exception type and reply code; server text, which can echo the login, is
  never stored or logged.

Setting it up with Gmail: create an app password for the sending account, then set
`MAIL_SMTP_HOST=smtp.gmail.com`, `MAIL_SMTP_PORT=587`, `MAIL_SMTP_SECURITY=starttls`,
`MAIL_SMTP_USERNAME`/`MAIL_FROM` to that account and `MAIL_SMTP_PASSWORD` to the app
password, in the host's secret settings. Verify with `flask community test-email`, which
prints what the server answered. Set `PUBLIC_BASE_URL` so emails carry an absolute link.

## Administrator workflow

`/internal/moderation` (allowlist only) now has two parts: the existing safety queue, and
**Reader reports** - grouped by set, with each report (reason, entry, comment, reporter),
the re-review findings (outcome, explanation, affected text, suggestion, whether it still
describes the live version, calls and tokens), alert delivery status with "Send again",
and the history. Actions (`POST /api/moderation/reports/<id>/action`):

| Action | Effect |
| --- | --- |
| Confirm issue | reports closed as `actioned`; confirmed reports lower ranking |
| Dismiss as unsupported | reports closed as `dismissed`; nothing else changes |
| Mark resolved | reports closed as `resolved` |
| Check again | re-runs the re-review (counts against the limits) |
| Correct one entry | publishes a new version with that field replaced, after the usual safety check; the author's private set is not touched |
| Restrict / Restore | hides a published set / restores a set restricted here (a set held by the safety policy is released through the queue decision instead) |

Every action, every report filed and every re-review is appended to
`moderation_audit_event`; safety decisions keep their own trail in `ModerationRecord`.

## What is stored

`ModerationRecord` keeps the decision, reason codes, per-dimension statuses, confidence,
the deterministic measurements, the policy/schema/prompt versions, the model, latency, and
up to twelve short quoted spans.

It does not keep a copy of the submission — that already lives on
`FlashcardPublicationVersion`. The quoted spans are the only fragments of submitted content
in the table, and the only field a retention job needs to redact
(`quotes_redacted_at`, `MODERATION_QUOTE_RETENTION_DAYS`).

No author identifier reaches the provider. The prompt carries subject, topic, level,
language and visibility; the learner level comes from the set's declared grade, not from
the submitting account's profile.

`content_hash` binds a decision to the exact text it was made about, which is what stops an
approval from surviving an edit.

## Cost

None for an unchanged resubmission. One small classification per new submission. The stronger model runs only when the cheap pass
cannot settle it: obfuscation at or above `MODERATION_ESCALATION_RISK_THRESHOLD`, a
detected injection, a non-`allow` recommendation, confidence below the floor, insufficient
evidence, or any safety dimension not at `pass`. At most two calls, ever.

Content that fails the safety gate never reaches the quality review, so an abusive
submission costs one small call rather than a small one plus a large one.

## Configuration

| Setting | Default | Purpose |
| --- | --- | --- |
| `FEATURE_COMMUNITY_MODERATION` | on | the gate. Forced on in production whenever publishing is enabled |
| `COMMUNITY_MODERATORS` | empty | comma-separated usernames or emails allowed into the queue |
| `GROQ_MODERATION_MODEL` | fast model | the first-pass classifier |
| `GROQ_MODERATION_ESCALATION_MODEL` | analysis model | the second opinion on hard cases |
| `MODERATION_ESCALATION_RISK_THRESHOLD` | 0.25 | obfuscation risk that buys the stronger model; 1.01 disables |
| `MODERATION_REJECT_CONFIDENCE` | 0.75 | confidence a safety flag needs to reject rather than escalate |
| `MODERATION_REJECT_CONFIDENCE_SEVERE` | 0.55 | the same for severe categories; must not exceed the above |
| `MODERATION_MIN_ALLOW_CONFIDENCE` | 0.40 | below this the classifier cannot clear content alone |
| `MODERATION_SAFETY_REPORT_THRESHOLD` | 2 | open safety reports that count as a risk signal in a (re-)check; nothing is auto-hidden |
| `MODERATION_TOTAL_REPORT_THRESHOLD` | 5 | the same for reports of any kind |
| `REPORT_REVIEW_MAX_PER_SET_DAY` | 5 | paid re-reviews per set per day |
| `REPORT_REVIEW_MAX_PER_HOUR` | 60 | paid re-reviews site-wide per hour |
| `REPORT_REVIEW_MAX_ATTEMPTS` | 3 | attempts for one re-review before an alert |
| `MODERATION_RETRY_MAX_ATTEMPTS` | 3 | automatic re-checks of an unavailable safety check before an alert |
| `COMMUNITY_ALERT_EMAIL` | maheswariskillbridge@gmail.com | alert recipient |
| `MAIL_SMTP_*`, `MAIL_FROM` | unset | SMTP delivery; unset means alerts are stored, not sent |
| `MAIL_MAX_ATTEMPTS` | 3 | delivery attempts per alert |
| `PUBLIC_BASE_URL` | unset | absolute links in alert emails |
| `COMMUNITY_BACKGROUND_JOBS` | on (off under tests) | run re-reviews and email off the request thread |
| `MODERATION_QUOTE_RETENTION_DAYS` | 90 | how long quoted spans are kept |
| `MODERATION_MAX_CONTENT_CHARACTERS` | 40000 | above this a submission is held, not partially checked |
| `MODERATION_MAX_REPORTS_PER_USER_DAY` | 20 | reporting abuse ceiling |
| `AI_CONTENT_MODERATION_MAX_OUTPUT_TOKENS` | 900 | per-task output budget |

Thresholds are `policy.Thresholds`, built from configuration by the app layer and passed
into `decide()` as an argument, so the policy stays pure and a deployment can retune
without a code change. Only *numbers* are configurable: which dimensions may reject, that
fit problems never reject, that quality never holds content, and that uncertainty
escalates only with a risk signal are properties of the rules and are deliberately not
settings. `configure_app` refuses an inconsistent pair at startup;
`moderation_thresholds()` clamps at request time, because one bad number should not turn
every publish into an error.

`configure_app` also logs a warning when publishing and moderation are both on and
`COMMUNITY_MODERATORS` is empty: content is still held safely, but the queue has nobody to
work it. It warns too when alert email is not configured.

## Operations

**Retention.** Quoted spans are the only fragments of submitted content a moderation
record holds, and the only thing with a retention policy:

```powershell
flask redact-moderation-quotes     # or app.redact_expired_moderation_quotes()
```

It blanks `quotes_json` and stamps `quotes_redacted_at` on records past
`MODERATION_QUOTE_RETENTION_DAYS`, keeping the decision, reason codes, dimension statuses
and measurements. The row stays so an audit survives its evidence, and the reviewer view
reports the redaction so "no quotes were cited" is distinguishable from "the quotes aged
out". It is idempotent, so it is safe to schedule.

**Metrics.** `/internal/ai-diagnostics` carries a moderation panel fed by
`moderation_summary()`: decision mix, escalation rate, how often the stronger model was
bought, how often the check was unavailable, latency p50/p95, provider calls, estimated
cost, the models used, and the most frequent reason codes. Counts only — no submitted
content, no quoted spans, no author identifiers.

This is the production counterpart to the offline harness: the harness measures whether
the rules are *right* against labelled cases, this measures what they are *doing* to real
traffic.

**Retries.** `flask community retry` re-runs held safety checks, pending quality reviews,
failed re-reviews (within their attempt limit) and due alert emails. `flask community
alerts` lists recent alerts and their delivery state; `flask community test-email` sends
one test message. All three are safe to schedule or run by hand.

**Idempotency.** Publishing the same source set twice returns the existing publication
with `already_published: true` and a 200 rather than creating a second set and a second
moderation job. An unpublished set is excluded, because republishing something taken down
is a deliberate new act.

**Size ceiling.** A submission over `MODERATION_MAX_CONTENT_CHARACTERS` is held as
`revision_required` without reaching a provider. Moderating a prefix and publishing the
whole thing would be the worst of both.

## Evaluation

```
python scripts/run_moderation_eval.py            # replay the labelled corpus, free
python scripts/run_moderation_eval.py --usage    # add real latency and cost from telemetry
```

46 labelled cases in `tests/fixtures/moderation/cases.json`, English and German, covering
normal maths/physics/chemistry, reproduction and health education, direct and indirect
harassment, threats, dangerous instructions, off-topic content, five obfuscation and bypass
attempts, OCR text, ambiguous cases, false-positive traps, malformed output, and reported
content. Each carries the model output to replay, so the suite is deterministic and free.

Two pairs of metrics exist because each pair measures different costs:

* `legitimate_block_rate` (rejecting good content — a harm, threshold 0) versus
  `legitimate_friction_rate` (delaying it — an inconvenience, threshold 0.34).
* `policy_false_negative_rate` (the pipeline had a signal and published anyway —
  threshold 0) versus `classifier_miss_rate` (the model called abuse clean — reported,
  deliberately not thresholded, because no policy rule can recover from it).

`escalation_rate` on this corpus is an upper bound, not a production estimate: the corpus
is deliberately enriched with hard cases. Measure the real rate against real traffic.

Latency and cost are reported as "not measured" rather than estimated, because replay
cannot produce them. `--usage` fills them in from the gateway's own telemetry once real
moderation requests have run.

## Known limitations

1. **A wrong classifier defeats the pipeline.** Plainly written abuse that the model calls
   clean trips no deterministic detector and is published. The corpus keeps such a case and
   reports it as `classifier_miss_rate`. Reader reporting is the compensating control.
2. **Not all hidden messages are detected.** Semantic steganography, in-jokes and
   coded references are out of reach of both layers.
3. **Only flashcard sets are wired up.** The taxonomy and policy are content-type agnostic
   and `CONTENT_TYPES` already names notes and explanations, but only the flashcard publish
   path calls the pipeline today.
4. **Thresholds are calibrated against 46 synthetic cases**, not production traffic. The
   confidence floors in `policy.py` are the numbers most likely to need adjusting once real
   decisions exist to measure.
5. **Model confidence is not calibrated.** It is used as an ordered signal, and the
   thresholds are validated against the corpus rather than treated as probabilities.
6. **The queue needs staffing.** Escalation only helps if somebody works it; startup warns
   when the allowlist is empty. Since v2 ordinary content no longer waits on it, but
   quarantined and ambiguous cases still do.
7. **Retention is a command, not a schedule.** `flask redact-moderation-quotes` exists and
   is idempotent, but nothing runs it automatically — wire it into your scheduler.
8. **Background jobs are threads.** Re-reviews and email run on daemon threads in the web
   process. A restart mid-job leaves a re-review `running` or an alert `sending`;
   `flask community retry` does not reclaim those (to avoid double sends), so they show on
   the moderation page for a person to re-trigger.
9. **"Sent" is SMTP acceptance.** Bounces after acceptance are not tracked.
10. **Entry warnings and report findings are model opinions**, grounded in the text but
    not verified; that is why they are suggestions and never act on their own.

## Extending it

A new dimension: add it to `DIMENSIONS`, give it a label, a reason code and a role, and
describe it in `MODERATION_RULES`. The schema, the prompt and the record follow
automatically; the policy only needs a change if the new dimension should decide something.

A new content type: add it to `CONTENT_TYPES`, build items with `kind`/`text`, and call
`run_content_moderation`. Nothing else in the package is flashcard-specific.

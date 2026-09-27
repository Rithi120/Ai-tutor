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
| Persistence and routes | `app.py` | `ModerationRecord`, `ContentReport`, the publish path, the queue |

The package is Flask-free. Every rule is a pure function over plain data, so the policy is
unit-tested without a request, a database or a provider call.

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
  └─ if allow  run the existing quality review                 run_ai_review
```

Stages B, C and F are free. Only after them does anything reach a provider.

## Decisions

| Decision | Meaning | Public? |
| --- | --- | --- |
| `allow` | cleared the safety gate; the quality review decides publication | yes, if the quality review also approves |
| `reject` | a grounded, confident safety violation | no |
| `revision_required` | publishable after changes: off-topic, thin, contact details, unclear | no |
| `review` | uncertain, conflicting, obfuscated, or unavailable | no |
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
* **Fit** — subject relevance, educational value, age suitability, language quality,
  personal information, sexual-content context. A flag here is a revision request. No
  number of fit flags can ever produce a rejection.
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

Content goes to a human when the classifier is unavailable or unreadable, a safety flag is
below the confidence floor, evidence is insufficient, a safety dimension is `unknown`,
obfuscation risk is high, an injection was detected, confidence is under
`MIN_ALLOW_CONFIDENCE`, several readers have reported it, or the layers disagreed.

Reviewers work `/internal/moderation`, gated on the `COMMUNITY_MODERATORS` allowlist and
404 to everyone else. **An empty allowlist means nobody can reach the queue**, which holds
content unpublished rather than exposing it — safe, but it means an unstaffed deployment
accumulates a queue nobody empties. Set the allowlist before enabling publishing.

A reviewer decision appends a new record rather than editing the old one, and approving
content whose hash has changed since the check is refused with a 409.

## Reporting

`POST /api/community/sets/<id>/report`, one open report per reader per set. Two
safety-flavoured reports or five total auto-hide the set and queue it. The response is
identical whatever happened, so the endpoint cannot be used to discover the threshold.

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

One small classification per submission. The stronger model runs only when the cheap pass
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
| `MODERATION_SAFETY_REPORT_THRESHOLD` | 2 | safety reports that auto-hide published content |
| `MODERATION_TOTAL_REPORT_THRESHOLD` | 5 | total reports that do the same |
| `MODERATION_QUOTE_RETENTION_DAYS` | 90 | how long quoted spans are kept |
| `MODERATION_MAX_CONTENT_CHARACTERS` | 40000 | above this a submission is held, not partially checked |
| `MODERATION_MAX_REPORTS_PER_USER_DAY` | 20 | reporting abuse ceiling |
| `AI_CONTENT_MODERATION_MAX_OUTPUT_TOKENS` | 900 | per-task output budget |

Thresholds are `policy.Thresholds`, built from configuration by the app layer and passed
into `decide()` as an argument, so the policy stays pure and a deployment can retune
without a code change. Only *numbers* are configurable: which dimensions may reject, that
fit problems never reject, and that uncertainty escalates are properties of the rules and
are deliberately not settings. `configure_app` refuses an inconsistent pair at startup;
`moderation_thresholds()` clamps at request time, because one bad number should not turn
every publish into an error.

`configure_app` also logs a warning when publishing and moderation are both on and
`COMMUNITY_MODERATORS` is empty: content is still held safely, but the queue has nobody to
work it.

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
   when the allowlist is empty, but nothing else can compensate.
7. **Retention is a command, not a schedule.** `flask redact-moderation-quotes` exists and
   is idempotent, but nothing runs it automatically — wire it into your scheduler.

## Extending it

A new dimension: add it to `DIMENSIONS`, give it a label, a reason code and a role, and
describe it in `MODERATION_RULES`. The schema, the prompt and the record follow
automatically; the policy only needs a change if the new dimension should decide something.

A new content type: add it to `CONTENT_TYPES`, build items with `kind`/`text`, and call
`run_content_moderation`. Nothing else in the package is flashcard-specific.

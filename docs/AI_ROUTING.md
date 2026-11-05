# AI routing, budgets and providers

How Learnova decides which model answers a request, what it may spend, and what happens
when a provider fails. Everything described here lives behind one function,
`learnova.ai_services.service.create_response`; call sites never pick a provider.

## Providers

| Provider | Key | API the adapter speaks | Live‑verified from this codebase |
| --- | --- | --- | --- |
| `groq` (default) | `GROQ_API_KEY` | OpenAI Responses (compatible endpoint) | **yes** – every call in the test deployment |
| `openai` | `OPENAI_API_KEY` | OpenAI Responses; `temperature` dropped for `gpt-5*`, `gpt-6*`, `o*` | **partly** – key accepted, but the account had no credit (2026‑10‑01); only the 429 `insufficient_quota` path is confirmed live |
| `anthropic` | `ANTHROPIC_API_KEY` | Messages API; `temperature` never sent (current models reject it) | **no** – stub‑tested only, no key yet |
| `gemini` | `GEMINI_API_KEY` | Google's OpenAI‑compatible endpoint, **Chat Completions only** | **yes** – `gemini-3.8-flash` and `gemini-3.5-flash-lite` answered through the gateway (2026‑10‑01) |

A model is named `provider:model`; a bare name is Groq. A provider with no key is not
offered. The request translation for each provider is pure code in
`learnova/ai_services/adapters.py`; the network call is in `service.py` and nowhere else
(`tests/test_ai_gateway.py::test_provider_boundary_is_centralized`).

> **What "live‑verified" means here.** The *shape* each provider receives and returns is
> pinned by tests against stubbed SDK clients (`tests/test_ai_adapters.py`,
> `tests/test_ai_providers.py`); "yes" above means a real key has also produced a real
> answer through `create_response`. Before relying on a provider:
> `ALLOW_LIVE_AI_TESTS=true python scripts/live_ai_smoke_test.py --task models` lists what
> the key is actually served, and `--task assistant --provider gemini --model <id>` makes
> one call and **fails if Groq answered instead** (the report's `routing` line names the
> hop, e.g. `hops=gemini:provider_unavailable`). Model ids at all three providers change
> faster than this code, so none is hardcoded as a default anywhere.
>
> Learned live from Gemini on 2026‑10‑01: `gemini-2.5-flash` is withdrawn for new keys (404);
> the free tier allows **5 requests per minute** per model and reports that cap with a 429
> whose text talks about "quota" and "billing" but ends in "retry in 41s" – the classifier
> reads the retry hint and treats it as a 60‑second rate limit, not a dead account; and
> the free tier answers 503 "high demand" at times, after which the request falls back to
> Groq within the same call. A free‑tier Gemini key is therefore fine as a *premium* slot
> (a handful of hard calls a minute) and wrong as a general‑purpose provider.

## Tiers and routing

Every task has a tier (`learnova/ai_services/routing.py::TASK_TIERS`); each tier maps to
a Groq model from configuration.

| Tier | Groq model (setting) | Tasks |
| --- | --- | --- |
| fast | `GROQ_FAST_MODEL` | tutor_chat, flashcard_back_suggestion, translation, diagnosis_verification |
| standard | `GROQ_TUTOR_MODEL` | lesson/quiz/question/adaptive/project/exam generation, flashcard generation & review, answer_evaluation, content_moderation, assistant_chat |
| strong | `GROQ_ANALYSIS_MODEL` | final_exam_evaluation, answer_diagnosis, mistake_analysis, assistant_chat with "think harder" |
| vision | `GROQ_VISION_MODEL` | image_understanding (every chat, lesson and answer photo, [VISION.md](VISION.md)), ocr_document_recognition, handwriting_region_review, vocabulary_page_extraction, and any request that carries an image |

For one request the router produces an ordered list of **candidates**:

1. a **premium** model – only for a premium‑eligible task, only when the request's own
   signals justify it, and only while that provider has a key, a configured budget with
   headroom, rate headroom and no cooldown;
2. the model the call site asked for (the tier's Groq model);
3. the tier's Groq **fallback** (fast/standard → strong, strong → standard; vision has none –
   a model that cannot see the page is not a fallback);
4. an optional **slower model** `AI_SLOW_MODEL`, where its provider is permitted (not for
   vision);
5. an optional emergency `AI_FALLBACK_MODEL`, only where its provider is permitted.

There is at most one premium candidate, and the list always ends in Groq.

### A student's own cap: one sentence and a cooldown

A student who exceeds their own allowance (`AI_MAX_REQUESTS_PER_USER_HOUR` / `_DAY`,
`AI_BUDGET_USER_TOKENS_*`) is not told to try again every minute. The gateway puts the
account on a cooldown of `AI_USER_COOLDOWN_HOURS` (default 6; 0 disables), stored on the
account (`User.ai_cooldown_until`, so it survives restarts and follows the student to
another device), and every AI request during it is refused *before* any counting or any
provider call with scope `user_cooldown`. The student sees exactly one sentence: "You
have reached your limit. AI help is back at 14:30 UTC. Your saved lessons, flashcards and
vocabulary still work." (429, `Retry-After` set). Site and provider limits are not the
student's doing and are worded as the AI's limit, never with provider or API jargon.

Study modes never touch the gateway — flashcard study/learn/test/match/blast, vocabulary
practice and reading saved lessons are deterministic endpoints — so a cooldown never
blocks them; `tests/test_ai_cooldown.py` pins that.

### When a limit is hit: the slower model, and the student is told

Rate limits are metered **per model**, so a 429 on `gpt-oss-20b` says nothing about
`gpt-oss-120b` at the same provider. The gateway treats `provider_rate_limit` and
`provider_overloaded` as *model-scoped* (`routing.MODEL_SCOPED_FAILURES`): the limited
model gets a 60‑second cooldown of its own (`_MODEL_COOLDOWNS`), the next candidate at
the same provider is tried, and on the following requests the cooling model is skipped
without spending a call (hop `model_cooldown`). Only quota/billing, authentication and
outages remain provider-wide. Before this change a Groq 429 skipped every other Groq
model and ended in "the provider is busy" – precisely the moment the slower model should
have answered.

An answer that came from a later candidate because a limit was hit carries
`GatewayResponse.degraded` (the limit category) and leaves `g.ai_degraded` on the
request. `app.inject_ai_notice` then adds `ai_notice` (`code: ai_slow_model`,
`message: "Max limit reached - using a slower AI model. Answers may take a little
longer."`, translated) to every successful JSON response, or flashes it on a redirect;
`core.js` shows it as one dismissible banner, at most once a minute
(`ai-limit-rules.js: aiNoticeFrom, noticeDue`). When even the last candidate is limited,
the error itself says "Max limit reached …" (429 for the student's own cap, 503 with the
reset time for a budget, 503 `ai_provider_busy` when every model is limited).

### Premium: when, and for what

| Task | Slot | Justified when |
| --- | --- | --- |
| answer_evaluation | `AI_PREMIUM_GRADING_MODEL` | the answer is final, or the question is hard/expert |
| final_exam_evaluation | `AI_PREMIUM_GRADING_MODEL` | always |
| answer_diagnosis, mistake_analysis | `AI_PREMIUM_REASONING_MODEL` | always |
| assistant_chat | `AI_PREMIUM_REASONING_MODEL` | "think harder", or the research style |

Everything else is routine and never leaves Groq, however much budget there is.
`AI_PREMIUM_GRADING_MODEL` defaults to the reasoning model; both unset means premium is off.

### Failures, retries and the call bound

- **A provider failure** moves to the next candidate. If it was provider‑wide (rate limit,
  overload, 5xx, timeout, network, authentication, withdrawn model) every remaining
  candidate at that provider is skipped, and a 429/529 puts the provider in a 60 s cooldown.
- **A validation failure** (the model answered, the answer did not fit the contract) spends
  the request's single corrective retry on the *next* candidate with corrective
  instructions – or on the same model only when nothing else is left. The usage log showed
  `answer_evaluation` failing validation on 7 of 21 live calls on gpt‑oss‑20b and the
  same‑model retry rescuing 0 of 7.
- **An answer cut off by its output cap** (PF6.1: Responses `status` `incomplete` with
  `incomplete_details.reason` `max_output_tokens`, or `incomplete` with no reason; Chat
  Completions `finish_reason` `length`; Anthropic `stop_reason` `max_tokens`;
  `adapters.stop_reason_of`) is recorded and logged, see [Watching it](#watching-it). If it
  then fails validation, its corrective retry is **not** sent to the same model: the cap is
  the same, so the answer stops in the same place (the section-generation and exam calls did
  exactly that before their caps were raised, `learnova/config.py`). A *different* next
  candidate – the fallback model – still gets the retry, on purpose: another model can fit
  the same answer into the same cap. A cut-off answer that still validates (a repaired
  missing closer, a salvaged chat photo reading) is used. The error category stays
  `invalid_json` / `schema_validation`, so callers are unchanged.
- **A JSON syntax slip is repaired first** (`contracts.repair_json_syntax`, PF4 L9): a stray
  quote after `]` or `}`, or a missing closing brace, is repaired before the answer counts
  as invalid, so it costs no corrective call (live 2026-11-01: 2 of 17 lesson and grading
  answers had such a slip). Logged as `ai.json_repaired task=… kind=…` at INFO. Valid JSON
  is never touched, a repair never changes a value, and a cut-off lesson is accepted only
  when its last one or two characters are missing (review B fuzzing).
- **A model that is cooling down** after a 429 is skipped without a call while another
  candidate follows it. When it is the last candidate it is asked again, except by
  `ocr_document_recognition` (`COOLDOWN_FAIL_FAST_TASKS`): a Project Mode page reading then
  fails at once with the cooldown's category and remaining seconds, and the page is read
  offline meanwhile (review B B-1). Chat photos and their close-up second look still ask.
- **A limit or budget error stops the request at once.** Nothing is retried around a cap.
- **Every provider call is bounded.** Clients are built with `max_retries=0` (the gateway
  does its own rotating and cooling down) and a `timeout` of `AI_PROVIDER_TIMEOUT_SECONDS`
  (default 40 s) for text, `AI_VISION_TIMEOUT_SECONDS` (default 75 s) when the input
  carries an image. The SDK defaults — 600 s and two silent retries — are how one
  unreachable vision endpoint turned a vocabulary scan into an eight-minute failure.
- **The hard bound** is `AI_MAX_PROVIDER_CALLS_PER_REQUEST` (default 3) provider calls per
  `create_response`, across primary, fallback and corrective. Loops above the gateway (OCR
  variants, question regeneration, moderation escalation, the diagnostics second opinion)
  each make their own bounded number of requests and never add premium calls: vision has
  no fallback, moderation stays on Groq, the verifier is a fast‑tier task.
- **If Groq is down and no other provider is permitted**, the request fails with
  `provider_unavailable` and the student reads "AI is temporarily unavailable. You can
  retry. Your saved work remains safe."

Every ledger row records a `routing_reason` such as
`tier=standard;premium=final_answer;candidate=2/3;hops=openai:provider_rate_limit`.

## Budgets

Budgets are enforced in **tokens**, because that is what providers bill; currency is
derived through per‑provider rates and is only shown and capped when rates are set.
Windows are **calendar UTC days and months** – "paused until 00:00 UTC" is something a
student can act on.

| Setting | Scope | Unset means |
| --- | --- | --- |
| `AI_BUDGET_USER_TOKENS_PER_DAY` / `_PER_MONTH` | one learner | no limit |
| `AI_BUDGET_GLOBAL_TOKENS_PER_DAY` | the whole site | 2,000,000 tokens per UTC day (emergency ceiling; an explicit blank disables it) |
| `AI_BUDGET_GLOBAL_TOKENS_PER_MONTH` | the whole site | no limit |
| `AI_BUDGET_<PROVIDER>_TOKENS_PER_DAY` / `_PER_MONTH` | one provider | Groq: no limit; **any other provider: not permitted** |
| `AI_RATE_<PROVIDER>_REQUESTS_PER_MINUTE` | one provider | no limit |
| `AI_COST_<PROVIDER>_INPUT_PER_MILLION` / `_OUTPUT_PER_MILLION` | prices | fall back to `AI_INPUT/OUTPUT_COST_PER_MILLION`, else 0 |
| `AI_BUDGET_GLOBAL_SPEND_PER_MONTH` | currency, priced providers only | no limit |
| `AI_VISION_REQUEST_TOKEN_CEILING` | one request that carries images: input estimate (`AI_IMAGE_TOKEN_ESTIMATE` 2048 per image) + output cap | 8000, Groq's on_demand tokens per minute for qwen3.8-27b; `0` turns it off. The output cap is lowered to fit; a request that cannot keep `AI_<TASK>_MIN_OUTPUT_TOKENS` is refused as `request_too_large` before anything is reserved or sent. Text requests are never clamped. Details: [VISION.md](VISION.md#fitting-the-vision-model-2026-11-01) |
| `AI_VISION_OUTPUT_TOKEN_CEILING` | output cap of every image request (page readings included) | 0 = learnt from the provider's output-per-minute refusal: photo readings (`image_understanding`) get one retry just below it, and the cap is remembered for an hour, in `AI_CACHE_DIR/_limits` too so every process knows it. A value pins the cap instead; 950 on Groq on_demand ([OPERATIONS.md](OPERATIONS.md#the-groq-tier) says what it costs a long page) |
| `AI_TEXT_REQUEST_TOKEN_CEILING` | one text request: input estimate + output allowance + max(200, ceiling/20) | 0 = off. A larger request is refused as `request_too_large` before anything is reserved or sent (usage `event_kind` `refused`); the **output cap is never lowered**. Lessons, mixed practice, the practice exam, similar-mistake practice and a topic split fit their notes to it (below). Recommended: the tier's tokens per minute (Groq on_demand 8000). |
| `AI_TEXT_REQUEST_OUTPUT_ALLOWANCE` | the share (0–1) of a text request's `max_output_tokens` counted against the ceiling, at admission and when notes are fitted | 0.4. Groq counts part of the output cap at admission, and how much changed overnight: "Requested 8305" for about 8 000 input tokens at `max_output_tokens` 5000 on 2026-11-01, but input + about 2,350 on 2026-11-02 ("Limit 8000, Requested 10363" for a gateway estimate of 8,619 and "Requested 9151" for 7,316: Requested ≈ 0.93 × estimate + 2,346, about 47 % of the cap). Our estimate runs about 8 % above Groq's input count, so 0.4 plus the 5 % margin covers that day. 0 restores the input-only rule. |
| `AI_REQUEST_TOKEN_CEILINGS` | one model, `provider:model=tokens,…` | no override; an entry wins over the general (image or text) ceiling for that model |

After `request_too_large` on a **text** request (a provider 413 or the admission check) no
other model at the same provider is tried unless its own ceiling is known and larger than
the limit that refused (`next_candidate_after`): Groq's 20b and 120b have the same
per-minute limit, and the fallback only doubled the refusal. Another provider is still
tried. Image requests keep their own handling (VISION.md).

**Text requests that carry notes are fitted, not clamped** (FIX_PLAN PF4 L4, review B B-6):

| | `AI_TEXT_REQUEST_TOKEN_CEILING` set | `AI_TEXT_REQUEST_TOKEN_CEILING` 0 |
| --- | --- | --- |
| Topic lesson, mixed practice (`fit_lesson_source`) | A request that fits as it is goes unchanged. Otherwise the notes get the room the rest of the request leaves under the ceiling minus the output allowance minus the margin; when that room is small the topic map first shrinks to 2 500 characters; never below `LESSON_SOURCE_MIN_CHARS` (4000) characters of notes. A shortened lesson says so in its notice. | Sent in full. A provider 413 that names its limit is rebuilt **once** (`refit_input_room`): within the limit minus the allowance and, when the refusal says "Requested R", smaller than the refused request's estimate by R − L plus a tenth plus the margin (`lesson.fit_retry limit=… requested=… sent=… rebuilt=…` at INFO). A rebuild that cannot be that small is **not sent** (`lesson.fit_retry_skipped`): the student gets the "too large" message, never a second identical request. The same rule applies after a 413 with the ceiling set. |
| Practice exam, similar-mistake practice, topic split (`fit_text_sources`) | Fitted the same way (the allowance counted on the task's output cap: 6000 for the exam and the split, the lesson cap for similar-mistake practice), only when the request would not fit (`exam.fitted` at INFO for the exam). | Sent unchanged; a 413 fails the request. |
| Everything else | Refused before sending when it does not fit. | Sent; the provider decides. |

Every page or topic keeps a labelled fair share of what is sent. Grading and the tutor keep
the unfitted pages. Replayed from the stored six-page Utilitarismus refusal (PF_FIX2 F1,
`tests/test_live_final_limits.py`): the rebuild room is min(8000 − 400 − 0.4 × 5000,
8619 − 2363 − 236 − 400) = 5,600, which the calibrated provider admits at about 7,554; the
lesson through the real route: 413 at 8,570, then one lesson at 5,314 (about 7,289 counted),
or one call at 5,314 with the ceiling set. A request the gateway refuses reaches the student as 413
`ai_request_too_large`: "This request is too large for the AI. Send less at once and try
again. Your saved work remains safe."

A paid provider is **off until it has a cap**. That is the one asymmetry, and it is
deliberate: a provider that bills must be switched on by a decision, never by omission.

The older per‑user *request* caps (`AI_MAX_REQUESTS_PER_USER_HOUR` / `_DAY`) still apply;
they are abuse protection and run before the cache is consulted. There is no per‑session
token cap: it stopped long Projektmodus tests partway and was removed on 2026-10-31.

### Never silently exceeded

Every provider call is bracketed by **reserve → call → settle**:

1. the gateway reserves the call's anticipated tokens (input estimate + the full output
   budget) against every applicable ceiling, under one process‑wide lock – so two requests
   racing for the last of a budget cannot both pass;
2. the provider is called;
3. the reservation is replaced by the tokens the provider actually billed; a failed call
   settles to zero.

Reservations abandoned by a crashed request expire after
`AI_BUDGET_RESERVATION_TTL_MINUTES` (10). Cache hits, coalesced requests and refused
requests are recorded for the diagnostics page but **never counted**: they cost nothing.

### What the student sees

| Situation | HTTP | `code` | Message |
| --- | --- | --- | --- |
| their own request cap | 429 | `ai_limit_reached` | "Your AI request limit has been reached…" + `Retry-After` |
| the site or a provider is out of budget | 503 | `ai_budget_exhausted` | "AI help is paused until {time} because the budget for this period is used up…" + `Retry-After` |
| a provider is rate‑limiting us | 503 | `ai_provider_busy` | "The AI provider is busy right now…" |
| nothing permitted can answer | 503 | `ai_unavailable` | "AI is temporarily unavailable…" |
| a text request too large for the model's per-minute limit, after fitting | 413 | `ai_request_too_large` | "This request is too large for the AI. Send less at once and try again…" |
| a photo could not be read (chat, lesson chat, photographed answer, New lesson) | 413 / 503 / 400 / 504 / 422 | `photos_too_large`, `photo_reader_busy`, `photo_reader_daily_limit`, `photo_reader_unavailable`, `image_rejected`, `photo_read_timeout`, `photo_read_failed` | one specific sentence per case, photos stay attached ([VISION.md](VISION.md#fitting-the-vision-model-2026-11-01)) |

`details.retry_after` and `details.resets_at` carry the same information for scripts.

Three failure categories came with the vision fixes (2026-11-01). `AIProviderError` now
carries the provider's HTTP status, error code and type, `Retry-After`, and the limit and
requested figures parsed from the message (integers only; the text is never logged). The
usage row records `provider_status`, `provider_error_code` and `provider_error_type`.
- **`request_too_large`.** An HTTP 413, or the gateway's own preflight refusal. It is about
  this request, so it is neither provider-wide nor a limit hop. A smaller request can
  still succeed, and a vision fallback model is still tried.
- **`provider_daily_limit`.** A 429 that names a per-day limit (tokens or requests per
  day). It is model-scoped: that model cools down for 10 minutes. It counts as a limit
  hop, not as an empty account.
- **`image_rejected`.** A 400 (`invalid_request`) on a request that carries an image:
  the picture itself was refused (format, size, count).

`image_understanding` gets no same-model corrective retry, because a truncated reading
would truncate again; since PF6.1 no task retries a cut-off answer on the same model
([above](#failures-retries-and-the-call-bound)). A timeout is never retried on the same
provider.
Pages append the retry time to the toast (`static/js/ai-limit-rules.js`); as‑you‑type
suggestions pause on a spent budget and resume on reload.

## The ledger

Budgets are checked against `ai_usage_event` (one row per provider call, cache hit, refusal
or coalesced request), written through `DatabaseLedger` in `app.py`. The JSONL log
`instance/ai_usage.jsonl` is still written, one line per request, for the diagnostics page
and for a checkout with no database wiring (`JsonlLedger`). Rows carry the hashed
`user_reference` the log always carried, never a raw user id.

Rows also carry `prompt_version` (`learnova/ai_services/prompts.py: PROMPT_VERSIONS`), so
`/internal/ai-diagnostics` can split a task by feature. A feature that reuses a task
appends its own suffix:

| Label | What it is |
| --- | --- |
| `assistant_chat:v5` | The chat assistant: tutoring layer, STUDENT CONTEXT, hidden CHIPS/TOPIC lines, one line per table row, decimal commas in formulas as `{,}` ([ASSISTANT.md](ASSISTANT.md)) |
| `tutor_chat:v6` | The lesson tutor chat: `\( … \)` / `\[ … \]` with single backslashes, the lesson state as plain text, one line per table row, decimal commas as `{,}` |
| `image_understanding:chat1+chat` | A chat photo's compact reading (no bbox, author or confidence; cut-off readings kept as partial) ([VISION.md](VISION.md)) |
| `image_understanding:v2+lesson` / `+solution` | A photo reading for a New lesson, or for a photographed answer (the full reading) |
| `lesson_generation:<version>+chat1` | A lesson built with Continue Learning, derived from the current `lesson_generation` version (today `lesson_generation:v9+chat1`) |

Ledger rows are buffered during a request and written on a separate connection after the
request's own transaction ends, so a route cannot commit half‑built work by accident and
SQLite's single writer is never waited on. Open reservations live in memory and are
visible to every thread the moment they are made. Both are per process: the deployment runs
one gunicorn worker. A multi‑worker deployment would need a database‑level reservation
(documented, not built).

## Caching and deduplication

- `AI_MODE=cached` caches every task; `live` caches only the deterministic OCR tasks.
  Entries older than `AI_CACHE_TTL_DAYS` (30; 0 = never) are misses.
- Identical requests within `AI_DEDUP_WINDOW_SECONDS` (15; 0 = off) share one provider
  call: the second waits on the first and takes its result, recorded as `coalesced` with
  zero tokens. Per process. Off under the test runner unless a test sets `AI_DEDUP_IN_TESTS`.
- `fresh=True` (server code only, never forwarded to the provider) skips both the cache read
  and the coalescing window, so the provider is really asked again; the new answer is
  cached as usual. Used by the chat's Regenerate and by Project Mode's "Try reading again"
  (a cached page reading used to come back unchanged for free; PF3 D3).
- The cache key names the provider explicitly, so `x` and `groq:x` are one entry and a
  future change of default provider cannot serve another provider's answer. (Existing
  entries missed once when this changed.)

## Watching it

`/internal/ai-diagnostics` is gated by `AI_DIAGNOSTICS_ADMINS` alone (any environment;
empty hides it everywhere) and shows every configured limit with used/remaining/reset,
usage by provider and by task, the routing table, coalesced requests and recent failures.
Nothing on it is student content.

**Cut-off answers (PF6.1).** Every line of the JSONL usage log carries `output_truncated`
(true when any provider call of the request stopped at its output cap) and `finish_reason`
(the provider's own word, sanitised: that cut-off call's, else the last answer's –
`completed`, `stop`, `end_turn`, `max_output_tokens`, `length`, `max_tokens`; empty for a
cache hit or a refusal). Each cut-off call also logs one WARNING, which production keeps
(INFO is off there):

```
ai.output_truncated task=lesson_generation model=openai/gpt-oss-20b max_output_tokens=5000 provider=groq output_tokens=5000 finish_reason=max_output_tokens attempt=1
```

Numbers and names only, never the answer. A task that shows up here often needs a larger
`AI_<TASK>_MAX_OUTPUT_TOKENS` (or a smaller request); photo readings at
`AI_VISION_OUTPUT_TOKEN_CEILING=950` are the expected case ([VISION.md](VISION.md)).

`scripts/live_budget_check.py` proves the budget mechanics against real Groq calls on a
scratch database: one call → one settled row with the billed tokens; a repeated OCR page →
cache hit, nothing billed; a spent site budget → 503 with a reset time and a zero‑token
refusal row; a user cap → 429 with `Retry-After`.

## What was fixed along the way

- `final_exam_evaluation` asked for 5000 output tokens and was capped at 600 – every open
  exam answer graded in 600 tokens. Now 5000. `/api/translate` 400 → 2500.
- Cache hits were counted as spend and refused requests as requests; the session‑token
  default was 60000 in config, 20000 in code and in `.env.example`.
- The diagnostics page was development‑only, so budgets could not be watched where the
  money is spent.

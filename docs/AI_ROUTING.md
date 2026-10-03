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
| vision | `GROQ_VISION_MODEL` | ocr_document_recognition, handwriting_region_review, lesson_generation from photos |

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
- **A limit or budget error stops the request at once.** Nothing is retried around a cap.
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
| `AI_BUDGET_GLOBAL_TOKENS_PER_DAY` / `_PER_MONTH` | the whole site | no limit |
| `AI_BUDGET_<PROVIDER>_TOKENS_PER_DAY` / `_PER_MONTH` | one provider | Groq: no limit; **any other provider: not permitted** |
| `AI_RATE_<PROVIDER>_REQUESTS_PER_MINUTE` | one provider | no limit |
| `AI_COST_<PROVIDER>_INPUT_PER_MILLION` / `_OUTPUT_PER_MILLION` | prices | fall back to `AI_INPUT/OUTPUT_COST_PER_MILLION`, else 0 |
| `AI_BUDGET_GLOBAL_SPEND_PER_MONTH` | currency, priced providers only | no limit |

A paid provider is **off until it has a cap**. That is the one asymmetry, and it is
deliberate: a provider that bills must be switched on by a decision, never by omission.

The older per‑user *request* caps (`AI_MAX_REQUESTS_PER_USER_HOUR` / `_DAY`) and the
per‑session token cap (`AI_MAX_TOKENS_PER_SESSION`) still apply; they are abuse protection
and run before the cache is consulted.

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

`details.retry_after` and `details.resets_at` carry the same information for scripts.
Pages append the retry time to the toast (`static/js/ai-limit-rules.js`); as‑you‑type
suggestions pause on a spent budget and resume on reload.

## The ledger

Budgets are checked against `ai_usage_event` (one row per provider call, cache hit, refusal
or coalesced request), written through `DatabaseLedger` in `app.py`. The JSONL log
`instance/ai_usage.jsonl` is still written, one line per request, for the diagnostics page
and for a checkout with no database wiring (`JsonlLedger`). Rows carry the hashed
`user_reference` the log always carried, never a raw user id.

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
- The cache key names the provider explicitly, so `x` and `groq:x` are one entry and a
  future change of default provider cannot serve another provider's answer. (Existing
  entries missed once when this changed.)

## Watching it

`/internal/ai-diagnostics` is gated by `AI_DIAGNOSTICS_ADMINS` alone (any environment;
empty hides it everywhere) and shows every configured limit with used/remaining/reset,
usage by provider and by task, the routing table, coalesced requests and recent failures.
Nothing on it is student content.

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

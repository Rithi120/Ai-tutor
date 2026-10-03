# Cost-safe AI testing

## Safety model

`learnova.ai_services.service.create_response()` is Learnova's only AI gateway. The OpenAI-compatible Groq client is private to that module. A source-audit test fails if another Python module imports the client or calls `responses.create()`.

There is no mock AI mode. The gateway has exactly two modes, `cached` and `live`, and both reach the provider through the single `_provider_response()` function. Automated tests never contact a provider: they either patch `app.create_response` (most feature tests) or patch `service._provider_response` (the gateway's own tests). Pytest also blocks socket connections globally, so an unmocked network request fails immediately.

Production startup requires explicit `AI_MODE=live`. Development `live` requests and `cached` cache misses additionally require `ALLOW_LIVE_AI=true`. Normal tests refuse provider calls and globally block sockets. The standalone smoke script requires `ALLOW_LIVE_AI_TESTS=true` and is never collected by pytest.

## Modes

`AI_MODE` accepts `cached` or `live`. Outside production it defaults to `cached`, which cannot spend anything on its own: a repeated request is served from disk, and a miss still refuses to call Groq unless `ALLOW_LIVE_AI=true`.

### Cached

```dotenv
APP_ENV=development
AI_MODE=cached
ALLOW_LIVE_AI=true
GROQ_API_KEY=your_real_key
```

The gateway hashes the normalized task type, selected model, language, prompt version, instructions, input, validation context, and private user partition. API keys are never part of the hash. Private partitions use HMAC, so the raw user identifier is not exposed in the cache key and two students cannot share cached private responses.

A hit is revalidated against the current production task schema before it can be returned. A valid miss is atomically saved under `instance/ai_cache/`; malformed responses are never cached. Live/cached output gets one corrective retry at most.

Clear the development cache:

```powershell
Remove-Item -Recurse -Force .\instance\ai_cache
```

### Live

```dotenv
APP_ENV=development
AI_MODE=live
ALLOW_LIVE_AI=true
GROQ_API_KEY=your_real_key
```

The UI shows a development-only `Cached AI` or `Live AI` badge. Production never shows this badge and refuses to start unless `AI_MODE=live` is explicitly present.

## Run one live smoke test

The standalone script makes no more than one provider request, uses a tiny lesson input and low output limit, validates the result, and prints token usage:

```powershell
$env:GROQ_API_KEY="your_real_key"
$env:ALLOW_LIVE_AI_TESTS="true"
python scripts/live_ai_smoke_test.py --task lesson --language de
```

`--task` accepts `lesson`, `moderation`, `assistant`, `diagnosis`, `suggestion`, `handwriting`, or `all`. Each makes exactly one request and checks the contract its feature actually depends on, so a replayed fixture passing is never mistaken for a real model complying. `suggestion` is the one worth running before trusting one-word card creation: it reports how many of the three definitions were usable, whether they were genuinely distinct, and their lengths. A real model that returns one definition, or three paraphrases of the same sentence, makes the feature not worth the tap it saves — and only a live run can show that.

`handwriting` builds a close-up sheet and checks the model answers one reading per numbered fragment with matching indexes. It verifies the contract, not accuracy: the fragments are rendered text, so a pass means the second look will not fall over in production, not that messy handwriting is now readable. Only real scanned pages show that.

Unset `ALLOW_LIVE_AI_TESTS` afterwards. Never enable it in the normal CI job.

## Add fixtures

1. Add the scenario to both `tests/fixtures/ai/en/` and `tests/fixtures/ai/de/`.
2. Use a supported task key such as `lesson_generation` or `final_exam_evaluation`.
3. Put a JSON object or plain text in `output_text`; the gateway serializes object values consistently.
4. Use `{"error":{"type":"timeout","message":"..."}}` or `rate_limit` for simulated provider failures.
5. Remove names, uploads, API keys, email addresses, and other student data.
6. Keep `_meta.prompt_versions` in both `valid.json` files synchronized with `learnova/ai_services/prompts.py`.
7. Add the expected production-schema assertion and run the entire suite in mock mode.

## Usage and cost accounting

Every provider call is reserved before and settled after against a **ledger**: the
`ai_usage_event` table in production (`DatabaseLedger`, `app.py`), the JSONL log for a
checkout with no database wiring (`JsonlLedger`). Token budgets (`AI_BUDGET_*`), per-provider
rate limits and the per-user request caps are all evaluated against it, under one lock, so
two requests racing for the last of a budget cannot both pass. Cache hits, coalesced
requests and refused requests are recorded but never counted.

`instance/ai_usage.jsonl` is still written, one line per request, with the provider that
answered and a `routing_reason`. It carries hashed user/session references and no content.

```bash
pytest tests/test_ai_budgets.py tests/test_ai_ledger.py tests/test_ai_observability.py   # the rules, the ledger, the gateway
ALLOW_LIVE_AI_TESTS=true python scripts/live_budget_check.py                             # the same, against real Groq calls
```

The live check runs on a scratch database and costs a few hundred tokens. It proves one
call becomes one settled row with the billed tokens, a repeated OCR page is a cache hit
that bills nothing, a spent site budget refuses the next call *before* the provider with a
503 and a reset time, and a user cap is a 429 with `Retry-After`.

Routing (which model answers which task, premium eligibility, fallback, the call bound) is
pure code in `learnova/ai_services/routing.py` and is tested in `tests/test_ai_routing.py`;
the gateway's behaviour on failures and retries is in `tests/test_ai_observability.py`.
`docs/AI_ROUTING.md` describes the policy.

## Community moderation

`content_moderation` is a task type like any other: registered in `SUPPORTED_TASK_TYPES`,
`PROMPT_VERSIONS`, `SCHEMA_SUMMARIES` and `VALIDATORS`, with its own token budgets and a
schema-valid sample in both languages. It has one extra validation step that runs at the
call site rather than in the gateway: a flagged safety dimension must quote text that
occurs in the submission, and the gateway does not have the submission. See
`docs/COMMUNITY_MODERATION.md`.

Its own harness replays 46 labelled cases with no provider call:

```powershell
python scripts/run_moderation_eval.py            # deterministic and free
python scripts/run_moderation_eval.py --usage    # add real latency/cost from telemetry
```

## The sample-response corpus

`tests/fixtures/ai/<language>/<scenario>.json` (language `en` or `de`) is test data, not a
runtime mode. `valid.json` holds one schema-correct sample per task type, and every other
file is a deliberately broken response: malformed or empty output, missing or wrongly
typed fields, duplicate prompts and IDs, unknown source references, incorrect counts,
invalid scores and difficulties, oversized output, and provider timeout/rate-limit errors.

`tests/provider_stub.py` replays a scenario through the real provider boundary:

```python
from tests.provider_stub import stub_provider

with stub_provider("malformed_json"):
    with self.assertRaises(AIValidationError):
        service.create_response(task_type="answer_evaluation", ...)
```

The stub reads the task type and language back out of the instructions the gateway builds,
so a test does not have to repeat them. Two tests keep the corpus honest:
`test_valid_samples_use_current_versions_and_production_schemas` fails if a sample drifts
from its validator or if `_meta.prompt_versions` falls behind `PROMPT_VERSIONS`, and
`test_invalid_samples_map_to_stable_categories` fails if a broken sample stops producing
its documented error category.

Adding a task type means adding its sample to both `en/valid.json` and `de/valid.json` and
updating `_meta.prompt_versions`; the test suite will tell you if you forget.

## Adaptive diagnostics

The diagnostics engine adds three gateway task types, each with its own validator,
prompt version, fixtures and budgets:

| Task type | Purpose | Calls per answer |
| --- | --- | --- |
| `answer_diagnosis` | The evidence-linked diagnosis (`diagnosis:v2`) | 1, replacing `mistake_analysis` |
| `diagnosis_verification` | Independent critique of a risky diagnosis | 0 or 1, above the risk threshold |
| `question_generation` | Spec-driven next question | 1 when the planner supplies a spec |

Mock fixtures live in `tests/fixtures/ai/{en,de}/valid.json` alongside the existing
tasks, and `tests/test_ai_observability.py` enforces that `_meta.prompt_versions` stays
in sync with `PROMPT_VERSIONS`.

### Evaluation harness

`tests/fixtures/diagnostics/cases.json` holds labeled cases with the model output to
replay, so the whole pipeline can be measured without a provider call:

```bash
python scripts/run_diagnostic_eval.py          # human-readable report, exit 1 on regression
python scripts/run_diagnostic_eval.py --json   # machine-readable metrics
pytest tests/test_diagnostics.py               # the same thresholds, as tests
pytest tests/test_diagnostics_integration.py   # the wired-up /api/answer flow
```

Adding a case is the normal way to pin a diagnosis or policy decision: give it an `id`,
the `model_output` to replay, and the `expected` label. Question cases use
`"kind": "question"` with a `constraints` block and the generated `question`.

The reported metrics measure agreement with the shipped labels only. They are not a
comparison against human teachers, and no such claim is made anywhere in the product.


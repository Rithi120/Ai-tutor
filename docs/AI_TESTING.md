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

Each logical gateway request appends one sanitized JSON line to `instance/ai_usage.jsonl` containing:

- request ID, timestamp, anonymized user/session reference, task, model, language, prompt version, and AI mode;
- estimated or provider-reported input/output/total tokens;
- cache hit or miss;
- estimated cost using `AI_INPUT_COST_PER_MILLION` and `AI_OUTPUT_COST_PER_MILLION`;
- duration, retry count, validation result, success, safe error category, and short safe summary.

It records only a request hash, never the complete prompt, upload, API key, or raw private user identifier.

```powershell
Get-Content .\instance\ai_usage.jsonl -Tail 20 |
  ConvertFrom-Json |
  Format-Table request_id,timestamp,task_type,prompt_version,ai_mode,total_tokens,cache_status,retry_count,validation_result,error_category
```

For the aggregate view, list a development administrator username/email in `AI_DIAGNOSTICS_ADMINS`, sign in as that account, and open `/internal/ai-diagnostics`. The route returns 404 outside development and for ordinary students.

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


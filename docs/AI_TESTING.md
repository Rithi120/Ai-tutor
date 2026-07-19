# Cost-safe AI testing

## Safety model

`learnova.ai_services.service.create_response()` is Learnova's only AI gateway. The OpenAI-compatible Groq client is private to that module. A source-audit test fails if another Python module imports the client or calls `responses.create()`.

Normal development and automated tests use `AI_MODE=mock`. The mock gateway loads deterministic, sanitized fixtures and never reaches the provider. Pytest also blocks socket connections globally, so an unmocked network request fails immediately.

Production startup requires explicit `AI_MODE=live`. Development `live` requests and `cached` cache misses additionally require `ALLOW_LIVE_AI=true`. Normal tests refuse provider calls and globally block sockets. The standalone smoke script requires `ALLOW_LIVE_AI_TESTS=true` and is never collected by pytest.

## Modes

### Mock

```dotenv
APP_ENV=development
AI_MODE=mock
AI_MOCK_SCENARIO=valid
AI_MOCK_LATENCY_MS=0
```

Fixtures live in `tests/fixtures/ai/<language>/<scenario>.json`, where language is `en` or `de`. In addition to provider timeout/rate-limit cases, fixtures cover malformed/empty output, missing or wrongly typed fields, duplicate prompts/IDs, unknown source references, incorrect counts, invalid scores/difficulties, and oversized output.

Set `AI_MOCK_LATENCY_MS=250` to add a deterministic 250 ms delay without using tokens.

### Cached

```dotenv
APP_ENV=development
AI_MODE=cached
ALLOW_LIVE_AI=true
GROQ_API_KEY=your_real_key
```

The gateway hashes the normalized task type, selected model, language, prompt version, instructions, input, fixture/validation context, and private user partition. API keys are never part of the hash. Private partitions use HMAC, so the raw user identifier is not exposed in the cache key and two students cannot share cached private responses.

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

The UI shows a development-only `Mock AI`, `Cached AI`, or `Live AI` badge. Production never shows this badge and refuses to start unless `AI_MODE=live` is explicitly present.

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

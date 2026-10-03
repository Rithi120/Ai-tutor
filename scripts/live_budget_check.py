"""Live proof that token budgets hold, on Groq, against a scratch database.

This is the one verification of the budget system that touches a real provider. It costs
a few hundred tokens on the fast model plus one small vision call, and it never touches
the real database or usage log: both are pointed at a temporary directory *before* the
application is imported.

    ALLOW_LIVE_AI_TESTS=true python scripts/live_budget_check.py

What it proves, in order:
  1. one provider call becomes one settled ledger row whose tokens match what Groq billed;
  2. a repeated deterministic task is a cache hit: no provider call, totals unchanged;
  3. a site budget set to exactly what has been used refuses the next call *before* the
     provider is reached, as a 503 with a reset time, and records a zero-token refusal;
  4. a user's own request cap is a 429 with Retry-After;
  5. the limits overview the admin page renders.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

SCRATCH = Path(tempfile.mkdtemp(prefix="learnova-budget-check-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(SCRATCH / 'budget_check.db').as_posix()}"
os.environ["AI_USAGE_PATH"] = str(SCRATCH / "usage.jsonl")
os.environ["AI_CACHE_DIR"] = str(SCRATCH / "cache")


def main() -> int:
    from dotenv import load_dotenv
    load_dotenv()
    if os.getenv("ALLOW_LIVE_AI_TESTS", "").casefold() != "true":
        print("Refusing live call: set ALLOW_LIVE_AI_TESTS=true explicitly.", file=sys.stderr)
        return 2
    if not os.getenv("GROQ_API_KEY"):
        print("Refusing live call: GROQ_API_KEY is not configured.", file=sys.stderr)
        return 2

    import app as application
    from learnova.ai_services import service
    from live_ai_smoke_test import _handwriting, _suggestion

    app = application.app
    app.config.update(
        TESTING=False, AI_MODE="live", ALLOW_LIVE_AI=True, AI_ENFORCE_LIMITS=True,
        AI_BUDGET_GLOBAL_TOKENS_PER_DAY=None, AI_BUDGET_GLOBAL_TOKENS_PER_MONTH=None,
        AI_BUDGET_USER_TOKENS_PER_DAY=None, AI_BUDGET_USER_TOKENS_PER_MONTH=None,
        AI_MAX_REQUESTS_PER_USER_HOUR=60)
    failures = 0

    def check(label: str, condition: bool, detail: str = "") -> None:
        nonlocal failures
        failures += not condition
        print(f"  [{'ok' if condition else 'FAIL'}] {label}{(' - ' + detail) if detail else ''}")

    with app.app_context():
        application.db.create_all()
        ledger = service.usage_ledger()
        print(f"scratch database: {SCRATCH}")
        print(f"ledger: {type(ledger).__name__}")

        print("\n1. one call, one settled row")
        spec = _suggestion(application, "German")
        spec.pop("_check", None)
        response = service.create_response(language="German", private_scope="budget-check", **spec)
        ledger.flush()
        rows = application.db.session.scalars(application.db.select(application.AIUsageEvent)).all()
        check("exactly one ledger row", len(rows) == 1, f"{len(rows)} rows")
        check("row is a settled call", rows[0].event_kind == "call" and rows[0].settled and rows[0].reserved_tokens == 0)
        check("row tokens equal what Groq billed", rows[0].total_tokens == response.usage.total_tokens,
              f"{rows[0].total_tokens} vs {response.usage.total_tokens}")
        check("totals agree", ledger.totals().tokens == response.usage.total_tokens)

        print("\n2. a repeated deterministic task is a cache hit")
        page = _handwriting(application, "English")
        page.pop("_check", None)
        first = service.create_response(language="English", private_scope="budget-check", **page)
        before = ledger.totals().tokens
        second = service.create_response(language="English", private_scope="budget-check", **page)
        after = ledger.totals().tokens
        check("second read returns the same text", first.output_text == second.output_text)
        check("second read billed nothing", after == before, f"{before} -> {after}")
        last = service._read_usage_records()[-1]
        check("usage log says cache_hit", bool(last.get("cache_hit")) and last.get("event_kind") == "cache_hit")

        print("\n3. a spent site budget refuses before the provider is reached")
        used = ledger.totals().tokens
        app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = used
        # A *different* request: an identical one within the deduplication window would
        # be served from step 1's answer for free and never reach the budget gate.
        fresh = {**spec, "input": str(spec["input"]) + " (second term, for the budget check)"}
        try:
            service.create_response(language="German", private_scope="budget-check", **fresh)
            check("call refused", False, "it went through")
        except service.AIRequestLimitError as error:
            check("call refused", True, f"scope={error.scope}")
            check("scope is the site budget", error.scope == "global_tokens_day")
            check("reset time known", error.resets_at is not None and (error.retry_after_seconds or 0) > 0,
                  f"resets {error.resets_at}")
            with app.test_request_context():
                http, status = application.ai_failure_response(error)
                payload = http.get_json()
            check("HTTP 503 ai_budget_exhausted", status == 503 and payload["code"] == "ai_budget_exhausted")
            check("message carries the reset time", "UTC" in payload["error"], payload["error"])
            check("Retry-After header set", "Retry-After" in http.headers)
        ledger.flush()
        refused = [row for row in application.db.session.scalars(
            application.db.select(application.AIUsageEvent)).all() if row.event_kind == "refused"]
        check("one zero-token refused row", len(refused) == 1 and refused[0].total_tokens == 0)
        check("totals unchanged by the refusal", ledger.totals().tokens == used)
        app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = None

        print("\n4. a user's own request cap is a 429")
        app.config["AI_MAX_REQUESTS_PER_USER_HOUR"] = 1
        try:
            service.create_response(language="German", private_scope="budget-check", **fresh)
            check("call refused", False, "it went through")
        except service.AIRequestLimitError as error:
            with app.test_request_context():
                http, status = application.ai_failure_response(error)
                payload = http.get_json()
            check("HTTP 429 ai_limit_reached", status == 429 and payload["code"] == "ai_limit_reached", f"scope={error.scope}")
            check("Retry-After header set", http.headers.get("Retry-After") is not None)
        app.config["AI_MAX_REQUESTS_PER_USER_HOUR"] = 60

        print("\n5. limits overview")
        app.config["AI_BUDGET_GLOBAL_TOKENS_PER_DAY"] = used + 10_000
        for row in service.limits_overview():
            print(f"  {row['scope']:22} {str(row['provider'] or '-'):10} {str(row['window']):6} "
                  f"configured={row['configured']} used={row['used']} remaining={row['remaining']} resets={row['resets_at']}")

    print(f"\n{'all checks passed' if not failures else f'{failures} check(s) FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

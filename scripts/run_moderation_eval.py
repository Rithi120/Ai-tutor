"""Run the community-moderation evaluation harness and print the metric report.

Usage:
    python scripts/run_moderation_eval.py [<case-file>] [--json] [--usage [<path>]]

Deterministic and free by default: each case carries the model output to replay, so the
whole pipeline (preprocessing, obfuscation analysis, schema validation, evidence
grounding and the policy engine) is measured without contacting a provider.

Latency and cost cannot be measured by replay, so they are reported as "not measured"
rather than estimated. `--usage` fills them in from real telemetry: it reads the
gateway's `ai_usage.jsonl` and reports the actual latency, token use and cost of every
`content_moderation` request that has genuinely run. With no live runs recorded, it says
so instead of producing a number.

Exits 1 when any published threshold is missed, so it can be wired into CI alongside
`pytest tests/test_moderation.py`.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from learnova.moderation.evaluation import (  # noqa: E402
    format_report, load_cases, run_suite,
)

DEFAULT_CASES = ROOT / "tests" / "fixtures" / "moderation" / "cases.json"
DEFAULT_USAGE = ROOT / "instance" / "ai_usage.jsonl"


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))], 2)


def usage_report(path: Path) -> str:
    """Real latency and cost for moderation calls, from the gateway's own telemetry."""

    if not path.exists():
        return f"\nLive usage: no telemetry at {path}. Latency and cost are not measured."
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("task_type") == "content_moderation":
            records.append(record)
    if not records:
        return ("\nLive usage: no content_moderation requests recorded yet. "
                "Latency and cost are not measured.")
    provider_calls = [item for item in records if item.get("provider_called")]
    latencies = [float(item.get("duration_ms") or 0) for item in records]
    costs = [float(item.get("estimated_or_reported_cost") or 0) for item in records]
    escalations = sum(1 for item in records if item.get("retry_count"))
    lines = [
        "",
        "Live usage (from the AI gateway's telemetry)",
        "-" * 44,
        f"requests                   {len(records)}",
        f"provider calls             {len(provider_calls)}"
        f"   (cache hits: {len(records) - len(provider_calls)})",
        f"latency_ms_p50             {_percentile(latencies, 0.5)}",
        f"latency_ms_p95             {_percentile(latencies, 0.95)}",
        f"mean_total_tokens          "
        f"{round(sum(int(item.get('total_tokens') or 0) for item in records) / len(records), 1)}",
        f"cost_per_request           {round(sum(costs) / len(records), 6)}",
        f"corrective retries         {escalations}",
        f"failures                   {sum(1 for item in records if not item.get('success'))}",
    ]
    if not any(costs):
        lines.append("cost is 0 because AI_INPUT/OUTPUT_COST_PER_MILLION are unset.")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    want_usage = "--usage" in argv
    positional = [item for item in argv if not item.startswith("--")]
    usage_path = DEFAULT_USAGE
    case_file = DEFAULT_CASES
    if want_usage and positional and positional[-1].endswith(".jsonl"):
        usage_path = Path(positional.pop())
    if positional:
        case_file = Path(positional[0])
    if not case_file.exists():
        print(f"case file not found: {case_file}", file=sys.stderr)
        return 2
    metrics = run_suite(load_cases(case_file))
    if as_json:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print(format_report(metrics))
        if want_usage:
            print(usage_report(usage_path))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

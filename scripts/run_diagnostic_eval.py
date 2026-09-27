"""Run the adaptive-diagnostics evaluation harness and print the metric report.

Usage:
    python scripts/run_diagnostic_eval.py [<case-file>] [--json]

Deterministic and free by default: each case carries the model output to replay, so the
whole pipeline (schema, evidence-link enforcement, deterministic verification, knowledge
update, planning, question validation) is measured without contacting a provider.

Exits 1 when any published threshold is missed, so it can be wired into CI alongside
`pytest tests/test_diagnostics.py`.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from learnova.diagnostics.evaluation import (  # noqa: E402
    format_report, load_cases, run_suite,
)

DEFAULT_CASES = ROOT / "tests" / "fixtures" / "diagnostics" / "cases.json"


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    paths = [item for item in argv if not item.startswith("--")]
    case_file = Path(paths[0]) if paths else DEFAULT_CASES
    if not case_file.exists():
        print(f"case file not found: {case_file}", file=sys.stderr)
        return 2
    metrics = run_suite(load_cases(case_file))
    print(json.dumps(metrics, indent=2, ensure_ascii=False) if as_json else format_report(metrics))
    return 0 if metrics["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

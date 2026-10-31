"""Run the teaching-quality benchmark and compare two implementations.

    # What the app sends + rubric on the reference replies (no AI calls, free):
    python scripts/teaching_benchmark.py --out after.json

    # The same scenarios against another checkout, e.g. the commit before a change:
    git worktree add ../learnova-baseline <commit>
    python scripts/teaching_benchmark.py --app-root ../learnova-baseline --out before.json

    # Real model replies for the measured turn (costs tokens; needs the provider key):
    python scripts/teaching_benchmark.py --live --out live-after.json

    python scripts/teaching_benchmark.py --compare before.json after.json

Scenarios, setup and scoring always come from *this* checkout (tests/fixtures/
teaching_benchmark.json, tests/teaching_benchmark.py, learnova/evaluation), so two runs
differ only in the app under test. Setup calls (lesson, grading, diagnosis) are always
answered from fixtures; with --live only the measured chat turn reaches the model.

The rubric is a regression proxy, not a measure of learning. Rising scores do not show
that students learn more; compare with performance on new, similar practice questions.
The database is always a temporary SQLite file, because the runner recreates the schema.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]


def _prepare(app_root: Path) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="learnova-benchmark-"))
    os.environ["DATABASE_URL"] = f"sqlite:///{(scratch / 'benchmark.db').as_posix()}"
    os.environ["AI_USAGE_PATH"] = str(scratch / "usage.jsonl")
    os.environ["AI_CACHE_DIR"] = str(scratch / "cache")
    os.environ.setdefault("SECRET_KEY", "benchmark")
    os.environ.setdefault("GROQ_API_KEY", os.environ.get("GROQ_API_KEY", "benchmark-no-key"))
    os.environ["LOCAL_OCR_WARMUP"] = "false"
    # The app under test first, then this checkout for the benchmark code itself.
    sys.path[:0] = [str(app_root), str(HERE)]
    os.chdir(app_root)


def _load_bench():
    import importlib.util
    import types

    # The app under test owns the `learnova` package, and an older checkout has no
    # learnova.evaluation: load this checkout's rubric by path under its usual name.
    import learnova  # noqa: F401  (the app's package, so the rubric's imports resolve there)

    rubric_spec = importlib.util.spec_from_file_location(
        "learnova.evaluation.teaching_rubric", HERE / "learnova" / "evaluation" / "teaching_rubric.py")
    assert rubric_spec and rubric_spec.loader
    sys.modules.setdefault("learnova.evaluation", types.ModuleType("learnova.evaluation"))
    rubric = importlib.util.module_from_spec(rubric_spec)
    sys.modules["learnova.evaluation.teaching_rubric"] = rubric
    rubric_spec.loader.exec_module(rubric)

    spec = importlib.util.spec_from_file_location("teaching_benchmark_runner", HERE / "tests" / "teaching_benchmark.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(app_root: Path, replies: str, live: bool) -> dict:
    _prepare(app_root)
    import app as application  # noqa: E402  (the app under test)

    bench = _load_bench()
    data = bench.load(HERE / "tests" / "fixtures" / "teaching_benchmark.json")
    gateway = application.ai_service.create_response

    def reply_for(scenario, call):
        if not live:
            return scenario["replies"][replies]
        kwargs = {key: value for key, value in call.items() if key != "task_type"}
        with application.app.test_request_context():
            return gateway(task_type=call["task_type"], **kwargs).output_text

    results = []
    for scenario in data["scenarios"]:
        result = bench.run_scenario(application, data, scenario, reply_for)
        results.append({
            "id": scenario["id"], "group": scenario["group"],
            "prompt_failures": bench.prompt_failures(result, scenario),
            "level": result["teaching"].get("level"), "model": result["model"],
            "seconds": result["seconds"], "score": result["score"].as_dict(),
            # Same 4-characters-per-token estimate the gateway uses for its input budget.
            "prompt_tokens_estimate": (len(result["instructions"]) + len(result["input"]) + 3) // 4,
            "reply": result["reply"] if live else None,
        })
    conforming = sum(not item["prompt_failures"] for item in results)
    return {
        "app_root": str(app_root), "replies": "live" if live else replies, "scenarios": len(results),
        "prompt_conformance": f"{conforming}/{len(results)}",
        "mean_score": round(sum(item["score"]["mean"] for item in results) / len(results), 2),
        "hard_failures": sum(not item["score"]["passed"] for item in results),
        "results": results,
    }


def compare(before_path: Path, after_path: Path) -> str:
    before, after = (json.loads(Path(path).read_text(encoding="utf-8")) for path in (before_path, after_path))
    rows = {item["id"]: item for item in before["results"]}
    lines = [f"{'scenario':34} {'prompt before':>14} {'prompt after':>13} {'score':>11}"]
    for item in after["results"]:
        old = rows.get(item["id"], {})
        mark = lambda entry: "ok" if entry and not entry.get("prompt_failures") else f"{len(entry.get('prompt_failures', []))} gaps"  # noqa: E731
        lines.append(f"{item['id']:34} {mark(old):>14} {mark(item):>13} "
                     f"{old.get('score', {}).get('mean', '-')!s:>5} -> {item['score']['mean']}")
    lines.append(f"prompt conformance: {before['prompt_conformance']} -> {after['prompt_conformance']}; "
                 f"mean score {before['mean_score']} -> {after['mean_score']}; "
                 f"hard failures {before['hard_failures']} -> {after['hard_failures']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-root", type=Path, default=HERE)
    parser.add_argument("--replies", choices=("good", "weak"), default="good")
    parser.add_argument("--live", action="store_true", help="measure real model replies (costs tokens)")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--compare", nargs=2, type=Path, metavar=("BEFORE", "AFTER"))
    args = parser.parse_args()
    if args.compare:
        print(compare(*args.compare))
        return
    report = run(args.app_root.resolve(), args.replies, args.live)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    for item in report["results"]:
        status = "ok" if not item["prompt_failures"] else "; ".join(item["prompt_failures"])
        print(f"{item['id']:34} level={item['level']} score={item['score']['mean']} prompt: {status}")
    print(f"prompt conformance {report['prompt_conformance']}, mean score {report['mean_score']}, "
          f"hard failures {report['hard_failures']}")


if __name__ == "__main__":
    main()

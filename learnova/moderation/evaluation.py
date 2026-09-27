"""The offline evaluation harness for the moderation pipeline.

A case file holds labelled submissions together with the model output to replay, so the
whole pipeline - preprocessing, obfuscation analysis, schema validation, evidence
grounding and the policy engine - is measured with no provider call and no flakiness.

Two rates are reported for legitimate educational content because they cost very
different things. Blocking a good biology set is a harm; sending it to a reviewer is a
delay. Collapsing both into one "false positive" number hides the distinction that the
whole design turns on, so they are measured separately and held to different thresholds.

Two false-negative rates are reported for the same reason. When prohibited content gets
published there are two very different causes: the policy had a usable signal and failed
to act on it, or the classifier looked straight at abuse and called it clean. Only the
first is a bug in this package, and only the first is held to a threshold. The second is
recorded, reported, and named as what it is - a limit of the model, and the reason the
corpus carries cases the pipeline cannot save.

Metrics reported:

* `decision_agreement`      - the pipeline reached the labelled decision
* `legitimate_block_rate`   - legitimate content that was rejected outright (the real harm)
* `legitimate_friction_rate`- legitimate content that was not published immediately
* `false_negative_rate`     - prohibited content that was published, for any reason
* `policy_false_negative_rate` - ...of which the policy had a signal and published anyway
* `classifier_miss_rate`    - ...of which the classifier reported clean on unsafe content
* `bypass_block_rate`       - deliberate evasion attempts that were not published
* `relevance_accuracy`      - subject_relevance matched the label
* `escalation_rate`         - share routed to a human
* `malformed_output_rate`   - share of model outputs that failed schema validation
* `grounding_drop_rate`     - share where an ungrounded safety flag was discarded
* `latency_ms_p50` / `p95`  - from recorded real runs, when the case file has them
* `estimated_cost_per_item` - from recorded token counts, when the case file has them

The corpus is deliberately enriched with hard and adversarial cases, so `escalation_rate`
here is an upper bound on how often a human is called, not an estimate of the production
rate. Measure that against real traffic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .normalize import inspect
from .policy import ModerationContext, decide
from .prompts import moderation_text
from .schema import (
    ModerationSchemaError,
    normalize_classification,
    unavailable_classification,
    validate_classification,
)

# Thresholds the shipped case set must meet. The test suite asserts these, so a
# regression in the rules fails the suite rather than silently degrading moderation.
THRESHOLDS = {
    "decision_agreement": 0.85,
    # A legitimate educational set must essentially never be rejected outright.
    "legitimate_block_rate": 0.0,
    # Some friction on legitimate content is acceptable; a third of it is not.
    "legitimate_friction_rate": 0.34,
    # Nothing labelled prohibited may be published *when the pipeline had a signal to
    # act on*. A case the classifier itself got wrong is measured separately, because no
    # policy rule can recover from a clean bill of health on plainly abusive text.
    "policy_false_negative_rate": 0.0,
    "bypass_block_rate": 1.0,
    "relevance_accuracy": 0.85,
}

# Thresholds where a *lower* number is better.
LOWER_IS_BETTER = frozenset({
    "legitimate_block_rate", "legitimate_friction_rate", "false_negative_rate",
    "policy_false_negative_rate", "classifier_miss_rate",
})

LABELS = ("legitimate", "prohibited", "off_topic", "ambiguous", "bypass", "insufficient_context")


@dataclass
class CaseResult:
    """What the pipeline produced for one labelled case."""

    case_id: str
    label: str
    language: str = "en"
    expected_decision: str = ""
    actual_decision: str = ""
    expected_relevance: str = ""
    actual_relevance: str = ""
    schema_valid: bool = True
    grounding_dropped: bool = False
    # False when the recorded model output is itself wrong about the content. Set by the
    # case file, never inferred: only whoever labelled the case knows this.
    classifier_correct: bool = True
    reason_codes: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    input_characters: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def decision_agrees(self) -> bool:
        return not self.expected_decision or self.expected_decision == self.actual_decision

    @property
    def relevance_agrees(self) -> bool:
        return not self.expected_relevance or self.expected_relevance == self.actual_relevance

    @property
    def published(self) -> bool:
        return self.actual_decision == "allow"

    @property
    def escalated(self) -> bool:
        return self.actual_decision == "review"


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """Read a case file. Accepts a bare list or an object with a `cases` key."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data.get("cases", []) if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError("case file must contain a list of cases")
    return cases


def run_case(case: dict[str, Any]) -> CaseResult:
    """Replay one case through the real pipeline and record what happened."""

    items = case.get("items") or []
    content = moderation_text(items)
    context = ModerationContext(**{
        key: value for key, value in (case.get("context") or {}).items()
        if key in ModerationContext.__dataclass_fields__
    })
    findings = inspect(content, source=case.get("source", "typed"))

    raw = case.get("model_output")
    schema_valid = True
    if raw is None:
        classification = unavailable_classification("no model output recorded for this case")
        schema_valid = False
    else:
        payload = raw
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = None
                schema_valid = False
        if payload is not None:
            try:
                validate_classification(payload)
            except ModerationSchemaError:
                schema_valid = False
        classification = (
            normalize_classification(payload, content=content) if schema_valid
            else unavailable_classification("recorded output failed schema validation"))

    decision = decide(
        classification, findings, context,
        safety_reports=int(case.get("safety_reports") or 0),
        total_reports=int(case.get("total_reports") or 0),
    )
    return CaseResult(
        case_id=str(case.get("id") or "unnamed"),
        label=str(case.get("label") or "ambiguous"),
        language=str(case.get("language") or "en"),
        expected_decision=str(case.get("expected_decision") or ""),
        actual_decision=decision.decision,
        expected_relevance=str(case.get("expected_relevance") or ""),
        actual_relevance=str((classification.get("dimensions") or {}).get("subject_relevance") or ""),
        schema_valid=schema_valid,
        grounding_dropped=bool(classification.get("unsupported_flags")),
        classifier_correct=bool(case.get("classifier_correct", True)),
        reason_codes=list(decision.reason_codes),
        latency_ms=case.get("latency_ms"),
        input_characters=len(content),
        input_tokens=int(case.get("input_tokens") or 0),
        output_tokens=int(case.get("output_tokens") or 0),
        notes=list(decision.rationale),
    )


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 2)


def run_suite(
    cases: list[dict[str, Any]],
    *,
    input_cost_per_million: float = 0.0,
    output_cost_per_million: float = 0.0,
) -> dict[str, Any]:
    """Run every case and return the metric report, including pass/fail per threshold."""

    results = [run_case(case) for case in cases]
    total = len(results)
    legitimate = [item for item in results if item.label == "legitimate"]
    prohibited = [item for item in results if item.label == "prohibited"]
    bypass = [item for item in results if item.label == "bypass"]
    with_relevance = [item for item in results if item.expected_relevance]
    latencies = [float(item.latency_ms) for item in results if item.latency_ms is not None]

    tokens_in = sum(item.input_tokens for item in results)
    tokens_out = sum(item.output_tokens for item in results)
    cost = (tokens_in / 1_000_000 * input_cost_per_million
            + tokens_out / 1_000_000 * output_cost_per_million)

    metrics: dict[str, Any] = {
        "cases": total,
        "by_label": {label: sum(item.label == label for item in results) for label in LABELS},
        "by_decision": {
            decision: sum(item.actual_decision == decision for item in results)
            for decision in ("allow", "reject", "revision_required", "review", "pending")
        },
        "decision_agreement": _rate(sum(item.decision_agrees for item in results), total),
        "legitimate_block_rate": _rate(
            sum(item.actual_decision == "reject" for item in legitimate), len(legitimate)),
        "legitimate_friction_rate": _rate(
            sum(not item.published for item in legitimate), len(legitimate)),
        "false_negative_rate": _rate(sum(item.published for item in prohibited), len(prohibited)),
        "policy_false_negative_rate": _rate(
            sum(item.published for item in prohibited if item.classifier_correct),
            len([item for item in prohibited if item.classifier_correct])),
        "classifier_miss_rate": _rate(
            sum(not item.classifier_correct for item in prohibited), len(prohibited)),
        "bypass_block_rate": _rate(sum(not item.published for item in bypass), len(bypass)),
        "relevance_accuracy": _rate(
            sum(item.relevance_agrees for item in with_relevance), len(with_relevance)),
        "escalation_rate": _rate(sum(item.escalated for item in results), total),
        "malformed_output_rate": _rate(sum(not item.schema_valid for item in results), total),
        "grounding_drop_rate": _rate(sum(item.grounding_dropped for item in results), total),
        "latency_ms_p50": _percentile(latencies, 0.5),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "latency_samples": len(latencies),
        "mean_input_characters": round(
            sum(item.input_characters for item in results) / total, 1) if total else 0.0,
        "estimated_cost_per_item": round(cost / total, 6) if total and cost else 0.0,
        "language_coverage": sorted({item.language for item in results}),
    }

    failures = []
    for name, threshold in THRESHOLDS.items():
        value = metrics.get(name)
        if value is None:
            continue
        failed = value > threshold if name in LOWER_IS_BETTER else value < threshold
        if failed:
            failures.append({"metric": name, "value": value, "threshold": threshold})
    metrics["threshold_failures"] = failures
    metrics["passed"] = not failures
    metrics["disagreements"] = [
        {
            "id": item.case_id, "label": item.label,
            "expected": item.expected_decision, "actual": item.actual_decision,
            "reason_codes": item.reason_codes,
        }
        for item in results if not item.decision_agrees
    ]
    return metrics


def format_report(metrics: dict[str, Any]) -> str:
    """Human-readable report for the CLI."""

    lines = [
        "Community moderation evaluation",
        "=" * 34,
        f"cases: {metrics['cases']}   languages: {', '.join(metrics['language_coverage'])}",
        "by label:    " + ", ".join(
            f"{name}={count}" for name, count in metrics["by_label"].items() if count),
        "by decision: " + ", ".join(
            f"{name}={count}" for name, count in metrics["by_decision"].items() if count),
        "",
    ]
    for name in (
        "decision_agreement", "legitimate_block_rate", "legitimate_friction_rate",
        "false_negative_rate", "policy_false_negative_rate", "classifier_miss_rate",
        "bypass_block_rate", "relevance_accuracy", "escalation_rate",
        "malformed_output_rate", "grounding_drop_rate",
    ):
        value = metrics.get(name)
        threshold = THRESHOLDS.get(name)
        marker = ""
        if threshold is not None and value is not None:
            failed = value > threshold if name in LOWER_IS_BETTER else value < threshold
            comparison = "max" if name in LOWER_IS_BETTER else "min"
            marker = f"   ({comparison} {threshold})  {'FAIL' if failed else 'ok'}"
        lines.append(f"{name:26s} {value}{marker}")

    lines.extend([
        "",
        "false_negative_rate counts every prohibited case that was published.",
        "policy_false_negative_rate counts only those the pipeline had a signal for; the",
        "remainder is classifier_miss_rate, which measures the model, not the policy.",
        "escalation_rate is an upper bound: this corpus is enriched with hard cases.",
        "",
    ])
    lines.append(f"{'mean_input_characters':26s} {metrics['mean_input_characters']}")
    if metrics["latency_samples"]:
        lines.append(f"{'latency_ms_p50':26s} {metrics['latency_ms_p50']}")
        lines.append(f"{'latency_ms_p95':26s} {metrics['latency_ms_p95']}"
                     f"   (from {metrics['latency_samples']} recorded run(s))")
    else:
        lines.append("latency                    not measured: no recorded runs in the case file")
    if metrics["estimated_cost_per_item"]:
        lines.append(f"{'estimated_cost_per_item':26s} {metrics['estimated_cost_per_item']}")
    else:
        lines.append("estimated_cost_per_item    not measured: no recorded token counts or prices")

    if metrics["disagreements"]:
        lines.extend(["", "Disagreements:"])
        for item in metrics["disagreements"]:
            lines.append(
                f"  {item['id']} ({item['label']}): expected {item['expected']}, "
                f"got {item['actual']} {item['reason_codes']}")
    lines.extend(["", "PASS" if metrics["passed"] else "FAIL"])
    if metrics["threshold_failures"]:
        for failure in metrics["threshold_failures"]:
            lines.append(
                f"  {failure['metric']}: {failure['value']} vs threshold {failure['threshold']}")
    return "\n".join(lines)

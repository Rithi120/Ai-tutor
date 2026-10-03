"""The offline evaluation harness for the diagnostics engine.

A case file holds labeled situations together with the raw model output to replay, so the
whole pipeline - normalization, evidence-link enforcement, deterministic verification,
knowledge update and planning - can be measured with no provider call and no flakiness.

Metrics reported:

* `diagnosis_agreement`   - primary tag matches the label
* `correctness_agreement` - correctness status matches the label
* `schema_validity`       - the raw output survived `validate_diagnosis`
* `evidence_link_rate`    - share of results whose primary claim is evidence-backed
* `action_agreement`      - planner chose the expected next action
* `oscillation_rate`      - share of cases where difficulty reversed its previous move
* `forbidden_moves`       - promotions or demotions the policy is supposed to forbid
* `question_validation_pass_rate` / `duplicate_rate` - from the question cases
* `latency_ms` / `input_characters` - cost proxies, when the case file records them
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .planner import plan_next_action
from .question_spec import QuestionSpec, spec_from_constraints, validate_question
from .schema import DiagnosisSchemaError, normalize_diagnosis, validate_diagnosis
from .verification import verify_diagnosis

# Thresholds the shipped case set must meet. The test suite asserts these, so a
# regression in the rules fails CI rather than silently degrading diagnoses.
THRESHOLDS = {
    "diagnosis_agreement": 0.85,
    "correctness_agreement": 0.9,
    "schema_validity": 0.9,
    "evidence_link_rate": 0.9,
    "action_agreement": 0.85,
    "question_validation_pass_rate": 0.9,
}


@dataclass
class CaseResult:
    """What the pipeline produced for one labeled case."""

    case_id: str
    kind: str
    schema_valid: bool
    expected_tag: str = ""
    actual_tag: str = ""
    expected_status: str = ""
    actual_status: str = ""
    expected_action: str = ""
    actual_action: str = ""
    difficulty_delta: int = 0
    evidence_linked: bool = False
    question_valid: bool | None = None
    duplicate: bool = False
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def tag_agrees(self) -> bool:
        return not self.expected_tag or self.expected_tag == self.actual_tag

    @property
    def status_agrees(self) -> bool:
        return not self.expected_status or self.expected_status == self.actual_status

    @property
    def action_agrees(self) -> bool:
        return not self.expected_action or self.expected_action == self.actual_action


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 1.0


def load_cases(path: str | Path) -> list[dict[str, Any]]:
    """Read a case file. Accepts either a bare list or `{"cases": [...]}`."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data.get("cases", data) if isinstance(data, dict) else data
    if not isinstance(cases, list):
        raise ValueError("case file must contain a list of cases")
    return [case for case in cases if isinstance(case, dict)]


def run_diagnosis_case(case: dict[str, Any], *, now: datetime | None = None) -> CaseResult:
    """Replay one labeled diagnosis case through the full deterministic pipeline."""

    now = now or datetime(2026, 1, 1, tzinfo=timezone.utc)
    expected = case.get("expected", {})
    result = CaseResult(
        case_id=str(case.get("id", "case")),
        kind="diagnosis",
        schema_valid=False,
        expected_tag=str(expected.get("primary_diagnosis", "")),
        expected_status=str(expected.get("correctness_status", "")),
        expected_action=str(expected.get("next_action", "")),
    )
    raw = case.get("model_output", {})
    try:
        diagnosis = validate_diagnosis(raw)
        result.schema_valid = True
    except DiagnosisSchemaError as error:
        result.notes.append(f"schema: {error}")
        diagnosis = normalize_diagnosis(raw if isinstance(raw, dict) else {})

    verified, verification = verify_diagnosis(
        diagnosis,
        student_answer=case.get("student_answer", ""),
        expected_answer=case.get("expected_answer", ""),
        question_type=case.get("question_type", ""),
        ocr_confidence=case.get("ocr_confidence"),
    )
    result.notes.extend(verification.conflicts)

    critique = case.get("second_opinion")
    if critique and verified.get("_needs_second_opinion"):
        from .verification import apply_second_opinion
        verified = apply_second_opinion(verified, critique)

    result.actual_tag = verified.get("primary_diagnosis", {}).get("tag", "")
    result.actual_status = verified.get("correctness_status", "")
    primary = verified.get("primary_diagnosis", {})
    result.evidence_linked = (
        primary.get("tag") in ("correct", "partially_correct", "insufficient_evidence")
        or bool(primary.get("evidence_ids"))
    )

    plan = plan_next_action(
        verified,
        case.get("state", {}),
        now=now,
        history=case.get("history", []),
        recent_prompts=case.get("recent_prompts", []),
        hints_used=bool(case.get("hints_used")),
    )
    result.actual_action = plan.action
    result.difficulty_delta = plan.difficulty_delta

    if not result.tag_agrees:
        result.failures.append(f"tag {result.actual_tag!r} != {result.expected_tag!r}")
    if not result.status_agrees:
        result.failures.append(f"status {result.actual_status!r} != {result.expected_status!r}")
    if not result.action_agrees:
        result.failures.append(f"action {result.actual_action!r} != {result.expected_action!r}")
    expected_delta = expected.get("difficulty_delta")
    if expected_delta is not None and int(expected_delta) != plan.difficulty_delta:
        result.failures.append(f"difficulty delta {plan.difficulty_delta} != {int(expected_delta)}")
    return result


def run_question_case(case: dict[str, Any]) -> CaseResult:
    """Replay one labeled question-generation case through the deterministic validator."""

    expected = case.get("expected", {})
    spec_source = case.get("spec")
    spec = (
        QuestionSpec(**spec_source) if isinstance(spec_source, dict) and "learning_objective" in spec_source
        else spec_from_constraints(case.get("constraints", {}))
    )
    validation = validate_question(
        case.get("question", {}),
        spec,
        recent_prompts=case.get("recent_prompts", []),
        strict_type=bool(case.get("strict_type", True)),
    )
    should_pass = bool(expected.get("valid", True))
    result = CaseResult(
        case_id=str(case.get("id", "case")),
        kind="question",
        schema_valid=True,
        question_valid=validation.valid,
        duplicate=not validation.checks.get("not_duplicate", True),
        notes=list(validation.warnings),
    )
    if validation.valid != should_pass:
        result.failures.append(
            f"expected valid={should_pass}, got {validation.valid} ({validation.summary})"
        )
    for required in expected.get("failed_checks", []):
        if validation.checks.get(required, True):
            result.failures.append(f"check {required!r} should have failed")
    return result


def run_suite(
    cases: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    diagnose: Callable[[dict[str, Any]], CaseResult] | None = None,
) -> dict[str, Any]:
    """Run every case and return the metric report.

    `diagnose` can be swapped for a live runner that calls the real provider; the default
    replays the model output stored in the case file, which keeps the suite deterministic
    and free.
    """

    diagnose = diagnose or (lambda case: run_diagnosis_case(case, now=now))
    results: list[CaseResult] = []
    for case in cases:
        if case.get("kind") == "question":
            results.append(run_question_case(case))
        else:
            results.append(diagnose(case))

    diagnosis_results = [item for item in results if item.kind == "diagnosis"]
    question_results = [item for item in results if item.kind == "question"]
    tag_scored = [item for item in diagnosis_results if item.expected_tag]
    status_scored = [item for item in diagnosis_results if item.expected_status]
    action_scored = [item for item in diagnosis_results if item.expected_action]

    forbidden = [
        item.case_id for item in diagnosis_results
        if abs(item.difficulty_delta) > 1
    ]
    oscillations = [
        item.case_id for item in diagnosis_results
        if item.difficulty_delta and item.expected_action == "maintain_difficulty"
    ]

    metrics = {
        "cases": len(results),
        "diagnosis_cases": len(diagnosis_results),
        "question_cases": len(question_results),
        "diagnosis_agreement": _rate(sum(1 for i in tag_scored if i.tag_agrees), len(tag_scored)),
        "correctness_agreement": _rate(sum(1 for i in status_scored if i.status_agrees), len(status_scored)),
        "schema_validity": _rate(sum(1 for i in diagnosis_results if i.schema_valid), len(diagnosis_results)),
        "evidence_link_rate": _rate(
            sum(1 for i in diagnosis_results if i.evidence_linked), len(diagnosis_results)),
        "action_agreement": _rate(sum(1 for i in action_scored if i.action_agrees), len(action_scored)),
        "question_validation_pass_rate": _rate(
            sum(1 for i in question_results if not i.failures), len(question_results)),
        "duplicate_rate": _rate(sum(1 for i in question_results if i.duplicate), len(question_results)),
        "oscillation_rate": _rate(len(oscillations), len(diagnosis_results)),
        "forbidden_moves": forbidden,
        "failures": [
            {"id": item.case_id, "kind": item.kind, "problems": item.failures}
            for item in results if item.failures
        ],
    }
    metrics["passed"] = all(
        metrics.get(name, 0.0) >= threshold for name, threshold in THRESHOLDS.items()
    ) and not forbidden
    return metrics


def format_report(metrics: dict[str, Any]) -> str:
    """A short plain-text report for the CLI."""

    lines = [
        f"cases: {metrics['cases']} ({metrics['diagnosis_cases']} diagnosis, "
        f"{metrics['question_cases']} question)",
    ]
    for name, threshold in THRESHOLDS.items():
        value = metrics.get(name, 0.0)
        mark = "PASS" if value >= threshold else "FAIL"
        lines.append(f"  {name:<32} {value:>6.1%}  (>= {threshold:.0%})  {mark}")
    lines.append(f"  {'duplicate_rate':<32} {metrics['duplicate_rate']:>6.1%}")
    lines.append(f"  {'oscillation_rate':<32} {metrics['oscillation_rate']:>6.1%}")
    if metrics["forbidden_moves"]:
        lines.append(f"  forbidden difficulty moves: {', '.join(metrics['forbidden_moves'])}")
    for failure in metrics["failures"]:
        lines.append(f"  - {failure['id']} ({failure['kind']}): {'; '.join(failure['problems'])}")
    lines.append(f"overall: {'PASS' if metrics['passed'] else 'FAIL'}")
    return "\n".join(lines)

"""Deterministic verification: check the model before trusting it, and price the risk.

Nothing here calls a provider. The job is to (a) decide independently whether the answer
matches the expected answer when that is decidable in code, (b) detect self-contradiction
inside the diagnosis, and (c) produce a risk score so an expensive second opinion is only
bought when it would actually change something.
"""

from __future__ import annotations

import ast
import json
import math
import operator
import re
from dataclasses import dataclass, field
from typing import Any

from .taxonomy import NON_EVIDENTIAL_TAGS, UNDERSTANDING_TAGS

# Risk at or above this level justifies a second independent critique. The app reads the
# effective threshold from configuration; this is the module default.
DEFAULT_VERIFY_RISK_THRESHOLD = 0.6

_NUMBER = re.compile(r"[-+]?\d{1,15}(?:[.,]\d+)?(?:\s*[eE][-+]?\d+)?")
_FRACTION = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*/\s*([-+]?\d+(?:[.,]\d+)?)\s*$")
_PERCENT = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)\s*%\s*$")
_UNIT = re.compile(r"(?<=[\d\s])([a-zA-Zµ°Ω]{1,6}(?:/[a-zA-Z]{1,4})?(?:\^?[23])?)\s*$")
_ARITHMETIC = re.compile(r"^[\d\s+\-*/().,^]+$")

_SAFE_BINARY = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
}
_SAFE_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


@dataclass(frozen=True)
class VerificationResult:
    """What deterministic checking could establish, and how risky the diagnosis is."""

    method: str                      # exact | numeric | choice | arithmetic | none
    decided: bool                    # True when code alone settled correct/incorrect
    matches_expected: bool | None    # None when undecidable
    conflicts: list[str] = field(default_factory=list)
    risk: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def needs_second_opinion(self) -> bool:
        return self.risk >= DEFAULT_VERIFY_RISK_THRESHOLD


def _decimal(value: str) -> str:
    """Normalize a decimal comma to a point without touching thousands groups."""

    return value.replace(",", ".") if value.count(",") == 1 and "." not in value else value.replace(",", "")


def extract_unit(value: Any) -> str:
    """The trailing unit of an answer, lowercased; empty when there is none."""

    text = str(value or "").strip()
    if not text or _FRACTION.match(text) or _PERCENT.match(text):
        return ""
    match = _UNIT.search(text)
    if not match:
        return ""
    unit = match.group(1).strip().lower()
    # A bare letter after a number is usually a variable name (3x), not a unit.
    return "" if len(unit) == 1 and unit.isalpha() and unit not in {"m", "s", "g", "l", "n", "k", "v", "a", "j", "w"} else unit


def parse_number(value: Any) -> float | None:
    """Parse a scalar answer: plain number, fraction, or percentage. None when not numeric."""

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    percent = _PERCENT.match(text)
    if percent:
        try:
            return float(_decimal(percent.group(1))) / 100.0
        except ValueError:
            return None
    fraction = _FRACTION.match(text)
    if fraction:
        try:
            denominator = float(_decimal(fraction.group(2)))
            return float(_decimal(fraction.group(1))) / denominator if denominator else None
        except (ValueError, ZeroDivisionError):
            return None
    # Strip only a suffix that is genuinely a unit. "3x" keeps its x, so it is read as a
    # variable expression and not silently compared as the number 3.
    unit = extract_unit(text)
    stripped = (text[: text.rfind(unit)] if unit else text).strip()
    candidates = [
        match for match in _NUMBER.finditer(stripped)
        # A digit run glued to a letter is not a number: "l4" is an OCR misread of "14",
        # and "x2" is a variable, so neither may be compared as the value 4 or 2.
        if not (match.start() and stripped[match.start() - 1].isalpha())
        and not (match.end() < len(stripped) and stripped[match.end()].isalpha())
    ]
    if len(candidates) != 1:
        return None
    try:
        return float(_decimal(candidates[0].group(0).replace(" ", "")))
    except ValueError:
        return None


def numbers_match(left: float, right: float, *, tolerance: float = 1e-6) -> bool:
    """Compare two numeric answers, tolerating float noise and sensible rounding."""

    if math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance):
        return True
    # Accept an answer rounded to the same precision the expected answer was given at.
    for digits in (0, 1, 2, 3):
        if round(left, digits) == round(right, digits) and abs(left - right) < 0.5 * 10 ** -digits * 1.001:
            return True
    return False


def normalized_text(value: Any) -> str:
    """Case/punctuation-insensitive form used for exact-match comparison."""

    if isinstance(value, (list, tuple)):
        return "|".join(sorted(normalized_text(item) for item in value))
    return re.sub(r"[^\w]+", " ", str(value or ""), flags=re.UNICODE).casefold().strip()


def evaluate_arithmetic(expression: str) -> float | None:
    """Evaluate a pure-arithmetic expression safely; None when it is not one.

    Only literal numbers and + - * / % ** are permitted, so no name, call, attribute or
    comprehension can be reached from a student- or model-supplied string.
    """

    text = _decimal(str(expression or "").strip()).replace("^", "**").replace("×", "*").replace("÷", "/")
    if not text or not _ARITHMETIC.match(text.replace("**", "")):
        return None
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return None

    def visit(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_UNARY:
            return _SAFE_UNARY[type(node.op)](visit(node.operand))
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_BINARY:
            right = visit(node.right)
            if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
                raise ValueError("division by zero")
            if isinstance(node.op, ast.Pow) and abs(right) > 32:
                raise ValueError("exponent out of range")
            return _SAFE_BINARY[type(node.op)](visit(node.left), right)
        raise ValueError("unsupported expression")

    try:
        result = visit(tree)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError, RecursionError):
        return None
    return result if math.isfinite(result) else None


def check_answer(
    student_answer: Any,
    expected_answer: Any,
    *,
    question_type: str = "",
    options: Any = None,
) -> VerificationResult:
    """Decide independently whether the student's answer matches the expected answer.

    `decided` is only True when code can genuinely settle it. Everything else stays
    undecidable, so the model's judgement is used but flagged as unverified.
    """

    notes: list[str] = []
    if expected_answer in (None, "") or student_answer in (None, ""):
        return VerificationResult("none", False, None, notes=["no comparable answer key"])
    # The app serialises a multi-part answer (checkboxes, ordering) to JSON before it
    # reaches the model and this check. Read it back, or an ordering answer is compared
    # as one string against the joined key and a correct order is always "wrong".
    if isinstance(student_answer, str) and student_answer.lstrip().startswith("["):
        try:
            decoded = json.loads(student_answer)
        except ValueError:
            decoded = None
        if isinstance(decoded, list):
            student_answer = decoded

    closed_types = {"multiple_choice", "checkboxes", "dropdown", "ordering", "true_false", "matching"}
    if question_type in closed_types or isinstance(expected_answer, (list, tuple)):
        student_key = normalized_text(student_answer)
        expected_key = normalized_text(expected_answer)
        if question_type == "ordering":
            student_key = "|".join(normalized_text(i) for i in (
                student_answer if isinstance(student_answer, (list, tuple)) else [student_answer]))
            expected_key = "|".join(normalized_text(i) for i in (
                expected_answer if isinstance(expected_answer, (list, tuple)) else [expected_answer]))
        return VerificationResult("choice", True, student_key == expected_key,
                                  notes=["compared as a closed-form selection"])

    expected_number = parse_number(expected_answer)
    student_number = parse_number(student_answer)
    if expected_number is not None and student_number is not None:
        matches = numbers_match(student_number, expected_number)
        expected_unit, student_unit = extract_unit(expected_answer), extract_unit(student_answer)
        if matches and expected_unit and student_unit and expected_unit != student_unit:
            notes.append("value matches but the unit differs")
            return VerificationResult("numeric", True, False, notes=notes)
        return VerificationResult("numeric", True, matches, notes=["compared numerically"])

    if normalized_text(student_answer) == normalized_text(expected_answer):
        return VerificationResult("exact", True, True, notes=["identical after normalization"])

    return VerificationResult("none", False, None, notes=["open response: not decidable in code"])


def verify_diagnosis(
    diagnosis: dict[str, Any],
    *,
    student_answer: Any = "",
    expected_answer: Any = "",
    question_type: str = "",
    ocr_confidence: float | None = None,
    verify_risk_threshold: float = DEFAULT_VERIFY_RISK_THRESHOLD,
) -> tuple[dict[str, Any], VerificationResult]:
    """Cross-check a diagnosis against deterministic evidence and score its risk.

    Returns the (possibly corrected) diagnosis plus the verification result. The
    diagnosis is corrected only where code is authoritative: a decided answer check
    overrides a contradicting correctness status, and confidence is lowered - never
    raised - by conflicts and by OCR uncertainty.
    """

    result = check_answer(student_answer, expected_answer, question_type=question_type)
    unreliable_reading = ocr_confidence is not None and ocr_confidence < 0.7
    if unreliable_reading and result.decided:
        # OCR uncertainty is evidence uncertainty: when the transcription itself is in
        # doubt, a character-level comparison cannot be used to overrule the diagnosis
        # or to accuse the student of a mistake the scanner may have introduced.
        result = VerificationResult(
            result.method, False, None,
            notes=[*result.notes, "answer text was read with low confidence"],
        )
    conflicts: list[str] = []
    updated = dict(diagnosis)
    status = updated.get("correctness_status", "insufficient_evidence")
    primary_tag = updated.get("primary_diagnosis", {}).get("tag", "")

    if result.decided and result.matches_expected is not None and primary_tag != "question_or_key_flawed":
        claims_correct = status == "correct"
        if result.matches_expected and not claims_correct and status != "insufficient_evidence":
            conflicts.append("the answer matches the expected answer but was not marked correct")
        elif not result.matches_expected and claims_correct:
            conflicts.append("the answer does not match the expected answer but was marked correct")

    score_fraction = updated.get("score", {}).get("fraction", 0.0)
    if status == "correct" and score_fraction < 0.75:
        conflicts.append("marked correct with a low score")
    if status == "incorrect" and score_fraction > 0.75:
        conflicts.append("marked incorrect with a high score")
    if primary_tag in UNDERSTANDING_TAGS and not updated.get("evidence"):
        conflicts.append("an understanding claim carries no quoted evidence")
    if updated.get("prerequisite_gaps") and primary_tag in NON_EVIDENTIAL_TAGS:
        conflicts.append("prerequisite gaps recorded without a usable diagnosis")

    risk = 0.0
    risk += 0.45 * min(2, len(conflicts))
    if not result.decided:
        risk += 0.2                       # an open response we could not check in code
    if updated.get("confidence", {}).get("value", 0.0) < 0.5:
        risk += 0.2
    if primary_tag in UNDERSTANDING_TAGS:
        risk += 0.15                      # durable claims deserve a harder look
    if ocr_confidence is not None and ocr_confidence < 0.7:
        risk += 0.2
    risk = round(min(1.0, risk), 3)

    # Apply the corrections code is entitled to make.
    if result.decided and result.matches_expected is not None and conflicts and primary_tag != "question_or_key_flawed":
        if result.matches_expected and status in ("incorrect", "partially_correct"):
            # An objectively right answer is never left marked wrong; the cause becomes a
            # question/key problem rather than a student failing.
            updated["correctness_status"] = "correct"
            updated["primary_diagnosis"] = {
                "tag": "correct",
                "statement": updated.get("primary_diagnosis", {}).get("statement", ""),
                "evidence_ids": updated.get("primary_diagnosis", {}).get("evidence_ids", []),
            }
            updated["secondary_diagnoses"] = []
            updated["prerequisite_gaps"] = []
            updated["misconception_description"] = ""
            score = dict(updated.get("score", {}))
            score["points"] = score.get("max_points", 1.0)
            score["fraction"] = 1.0
            updated["score"] = score
        elif not result.matches_expected and status == "correct":
            updated["correctness_status"] = "incorrect"
            if primary_tag in ("correct", ""):
                updated["primary_diagnosis"] = {
                    "tag": "insufficient_evidence",
                    "statement": "The answer does not match the expected answer.",
                    "evidence_ids": [],
                }
                updated["missing_evidence"] = True
                updated["missing_evidence_reason"] = (
                    "The stated cause did not hold up against the expected answer."
                )

    confidence = dict(updated.get("confidence", {"value": 0.5, "basis": ""}))
    penalty = 0.2 * len(conflicts) + (0.15 if (ocr_confidence is not None and ocr_confidence < 0.7) else 0.0)
    confidence["value"] = round(max(0.0, min(1.0, float(confidence.get("value", 0.5)) - penalty)), 3)
    if result.decided and not conflicts:
        confidence["basis"] = (confidence.get("basis") or "") + " Confirmed by a deterministic answer check."
        confidence["value"] = round(min(1.0, confidence["value"] + 0.1), 3)
    elif conflicts:
        confidence["basis"] = "Lowered: " + "; ".join(conflicts[:2])
    confidence["basis"] = confidence["basis"].strip()[:300]
    updated["confidence"] = confidence

    if conflicts:
        updated["validation_status"] = "deterministic_conflict"
    elif not result.decided and updated.get("validation_status") == "validated":
        updated["validation_status"] = "unverified"

    if ocr_confidence is not None and ocr_confidence < 0.7 and not updated.get("missing_evidence"):
        # OCR uncertainty is evidence uncertainty: the reading of the answer is in doubt.
        updated["missing_evidence"] = True
        updated["missing_evidence_reason"] = (
            "The scanned response could not be read confidently, so the diagnosis is provisional."
        )

    verification = VerificationResult(
        method=result.method,
        decided=result.decided,
        matches_expected=result.matches_expected,
        conflicts=conflicts,
        risk=risk,
        notes=result.notes,
    )
    updated["_needs_second_opinion"] = risk >= verify_risk_threshold and not (
        result.decided and not conflicts
    )
    return updated, verification


def apply_second_opinion(diagnosis: dict[str, Any], critique: dict[str, Any]) -> dict[str, Any]:
    """Fold an independent critique into a diagnosis without letting it invent new claims.

    A critique may only *weaken* a result: it can reject the primary diagnosis, mark the
    evidence insufficient, or lower the confidence. It can never add a misconception, a
    prerequisite gap or a higher confidence, because it never saw fresh evidence.
    """

    updated = dict(diagnosis)
    agrees = bool(critique.get("agrees"))
    reason = str(critique.get("reason", "") or "").strip()[:300]
    confidence = dict(updated.get("confidence", {"value": 0.5, "basis": ""}))
    if agrees:
        confidence["value"] = round(min(1.0, float(confidence.get("value", 0.5)) + 0.1), 3)
        confidence["basis"] = (confidence.get("basis", "") + " An independent check agreed.").strip()[:300]
        updated["confidence"] = confidence
        if updated.get("validation_status") in ("unverified", "repaired"):
            updated["validation_status"] = "validated"
        return updated
    confidence["value"] = round(max(0.0, float(confidence.get("value", 0.5)) - 0.3), 3)
    confidence["basis"] = (f"An independent check disagreed: {reason}" if reason
                           else "An independent check disagreed.")[:300]
    updated["confidence"] = confidence
    updated["missing_evidence"] = True
    updated["missing_evidence_reason"] = reason or "Two independent checks disagreed about the cause."
    updated["primary_diagnosis"] = {
        "tag": "insufficient_evidence",
        "statement": reason,
        "evidence_ids": [],
    }
    updated["secondary_diagnoses"] = []
    updated["prerequisite_gaps"] = []
    updated["misconception_description"] = ""
    updated["correctness_status"] = (
        updated.get("correctness_status") if updated.get("correctness_status") == "correct"
        else "insufficient_evidence"
    )
    updated["validation_status"] = "deterministic_conflict"
    return updated

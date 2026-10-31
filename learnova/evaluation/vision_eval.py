"""The repeatable image-reading evaluation: what can be measured offline, and how.

Offline (no paid calls, `run_offline`):

1. Intake: every synthetic file case is accepted or refused with the expected code.
2. Payload size: what each path sends a provider, before and after this change, against
   Groq's documented 4 MB limit for a base64 image (configurable).
3. Legibility kept: the local OCR engine (RapidOCR, when installed) reads the full image
   and the sized provider copy; the character error rate (CER) must not rise. The reader
   is held constant, so this measures the pipeline, not a model.
4. Old versus new preprocessing on the case the old one got wrong: a transparent
   screenshot, which the old page pipeline turned black on black.
5. Quality detector: expected issues against detected issues, on the calibration seed and
   on a held-out seed the thresholds were not tuned on.

What offline cannot measure: how well a vision *model* reads handwriting. That needs the
live run (scripts/vision_eval.py --live), which calls the configured provider, costs
money, and is never part of the automated tests.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from PIL import Image

from learnova.evaluation import vision_fixtures
from learnova.evaluation.vision_metrics import cer
from learnova.vision import intake

GROQ_BASE64_LIMIT = 4_000_000  # characters; Groq's documented base64 image limit (4 MB)
HELD_OUT_SEEDS = (11, 23, 31)


@dataclass
class EvaluationReport:
    intake: list[dict[str, Any]] = field(default_factory=list)
    payload: list[dict[str, Any]] = field(default_factory=list)
    legibility: list[dict[str, Any]] = field(default_factory=list)
    preprocessing: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        intake_ok = sum(1 for row in self.intake if row["correct"])
        payload_new = sum(1 for row in self.payload if row["new_within_limit"])
        payload_old = sum(1 for row in self.payload if row["old_within_limit"])
        return {
            "intake_correct": f"{intake_ok}/{len(self.intake)}",
            "payload_within_limit_old": f"{payload_old}/{len(self.payload)}",
            "payload_within_limit_new": f"{payload_new}/{len(self.payload)}",
            "legibility_cases": len(self.legibility),
            "legibility_worse": [row["case"] for row in self.legibility if row["cer_provider"] > row["cer_full"] + 0.02],
            "quality": {seed: {key: value for key, value in result.items() if key != "rows"}
                        for seed, result in self.quality.items()},
        }


def _base64_length(byte_count: int) -> int:
    return 4 * ((byte_count + 2) // 3)


def _old_page_bytes(data: bytes) -> int:
    """What Project Mode used to send for this photo: the full preprocessed PNG."""

    from learnova.ocr.service import preprocess_document_image
    return len(preprocess_document_image(data).data)


def evaluate_intake(cases: list[vision_fixtures.VisionCase]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        started = time.perf_counter()
        try:
            prepared = intake.prepare(case.data)
            outcome, issues = "accepted", sorted(prepared.quality.issues)
        except intake.ImageProblem as problem:
            outcome, issues = problem.code, []
        expected = case.reject or "accepted"
        rows.append({"case": case.name, "expected": expected, "outcome": outcome, "correct": outcome == expected,
                     "issues": issues, "ms": round((time.perf_counter() - started) * 1000)})
    return rows


def evaluate_payload(cases: list[vision_fixtures.VisionCase], limits: intake.IntakeLimits,
                     limit_characters: int = GROQ_BASE64_LIMIT) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        if case.reject:
            continue
        old = _base64_length(_old_page_bytes(case.data))
        prepared = intake.prepare(case.data, limits)
        sized, mime = intake.provider_copy(prepared.data, limits)
        new = _base64_length(len(sized))
        with Image.open(io.BytesIO(sized)) as probe:
            size = probe.size
        rows.append({"case": case.name, "upload_bytes": len(case.data), "old_base64": old, "new_base64": new,
                     "new_size": f"{size[0]}x{size[1]}", "new_mime": mime,
                     "old_within_limit": old <= limit_characters, "new_within_limit": new <= limit_characters})
    return rows


def evaluate_quality(cases: list[vision_fixtures.VisionCase]) -> dict[str, Any]:
    checked = ("blurred", "dark", "low_contrast", "low_resolution", "content_at_edge")
    true_positive = false_positive = false_negative = 0
    rows = []
    for case in cases:
        if case.reject:
            continue
        found = set(intake.prepare(case.data).quality.issues) & set(checked)
        expected = set(case.issues) & set(checked)
        true_positive += len(found & expected)
        false_positive += len(found - expected)
        false_negative += len(expected - found)
        rows.append({"case": case.name, "expected": sorted(expected), "found": sorted(found)})
    precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 1.0
    recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    return {"true_positive": true_positive, "false_positive": false_positive, "false_negative": false_negative,
            "precision": round(precision, 3), "recall": round(recall, 3), "rows": rows}


def local_reader(config: Any = None) -> Callable[[bytes], str] | None:
    """RapidOCR as a fixed, free reader, or None when it is not installed."""

    from learnova.ocr import local
    settings = config or {"LOCAL_OCR_ENGINE": "auto", "LOCAL_OCR_LANGUAGE": "de"}
    if not local.available(settings):
        return None

    def read(data: bytes) -> str:
        return local.recognize(data, settings).text

    return read


def evaluate_legibility(cases: list[vision_fixtures.VisionCase], limits: intake.IntakeLimits,
                        read: Callable[[bytes], str]) -> list[dict[str, Any]]:
    rows = []
    for case in cases:
        if case.reject or not case.truth or case.kind == "diagram" or case.pages:
            continue
        prepared = intake.prepare(case.data, limits)
        sized, _mime = intake.provider_copy(prepared.data, limits)
        full_text, sized_text = read(prepared.data), read(sized)
        rows.append({"case": case.name, "kind": case.kind, "cer_full": round(cer(case.truth, full_text), 3),
                     "cer_provider": round(cer(case.truth, sized_text), 3)})
    return rows


def evaluate_old_preprocessing(cases: list[vision_fixtures.VisionCase],
                               read: Callable[[bytes], str]) -> list[dict[str, Any]]:
    from learnova.ocr.service import preprocess_document_image
    rows = []
    for case in cases:
        if case.name != "transparent_screenshot":
            continue
        old = preprocess_document_image(case.data).data
        new = intake.prepare(case.data).data
        rows.append({"case": case.name, "cer_old_page_pipeline": round(cer(case.truth, read(old)), 3),
                     "cer_new_intake": round(cer(case.truth, read(new)), 3)})
    return rows


def run_offline(*, include_large: bool = True, with_ocr: bool = True,
                limits: intake.IntakeLimits | None = None) -> EvaluationReport:
    limits = limits or intake.IntakeLimits()
    report = EvaluationReport()
    calibration = vision_fixtures.build_cases(include_large=include_large, seed=7)
    report.intake = evaluate_intake(calibration)
    report.payload = evaluate_payload(calibration, limits)
    report.quality = {"seed 7 (calibration)": evaluate_quality(calibration)}
    # Seeds the thresholds were never tuned on: different jitter, blur, darkness, tilt, crop.
    for seed in HELD_OUT_SEEDS:
        report.quality[f"seed {seed} (held out)"] = evaluate_quality(
            vision_fixtures.build_cases(include_large=False, seed=seed))
    font = vision_fixtures.font(46)
    report.notes.append(
        f"Fixture font: {font.name} (German letters {'drawn' if font.covers_german else 'transliterated'}); "
        f"handwriting font: {vision_fixtures.font(46, True).name}. Simulated handwriting is jittered type, "
        "not real handwriting.")
    if with_ocr:
        read = local_reader()
        if read is None:
            report.notes.append("RapidOCR is not installed: legibility was not measured.")
        else:
            report.legibility = evaluate_legibility(calibration, limits, read)
            report.preprocessing = evaluate_old_preprocessing(calibration, read)
    return report

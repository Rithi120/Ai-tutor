"""Image-reading evaluation for Learnova (docs/VISION.md).

    python scripts/vision_eval.py                 # offline: free, deterministic
    python scripts/vision_eval.py --json out.json # the same, machine-readable
    python scripts/vision_eval.py --live          # LIVE: calls the configured vision model
    python scripts/vision_eval.py --live --cases DIR   # LIVE, on your own photos

Offline needs no key and makes no AI call. It checks intake, payload sizes against the
provider limit, quality hints, and - with RapidOCR installed - that the sized provider
copy is as legible as the full image (see learnova/evaluation/vision_eval.py).

--live COSTS MONEY on the configured provider and is never run by the test suite. It reads
each synthetic case (or each image in DIR that has a same-named .txt file holding the exact
text on it) twice - with the old Project Mode page prompt and with the new shared reader -
and reports character/word error rates, maths matched by structure, and how many readings
were marked uncertain. Requires AI_MODE=live, ALLOW_LIVE_AI=true and a provider key.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from learnova.evaluation import vision_eval, vision_fixtures  # noqa: E402
from learnova.evaluation.vision_metrics import cer, label_recall, math_exact, wer  # noqa: E402


def print_offline(report: vision_eval.EvaluationReport) -> None:
    summary = report.summary()
    print("== Intake ==")
    for row in report.intake:
        mark = "ok " if row["correct"] else "BAD"
        print(f"  {mark} {row['case']:24} expected={row['expected']:18} got={row['outcome']:18} "
              f"issues={','.join(row['issues']) or '-'} {row['ms']} ms")
    print(f"  correct: {summary['intake_correct']}")
    print(f"\n== Payload sent to the provider (limit {vision_eval.GROQ_BASE64_LIMIT:,} base64 characters) ==")
    for row in report.payload:
        print(f"  {row['case']:24} upload {row['upload_bytes'] / 1e6:6.2f} MB | old page path "
              f"{row['old_base64'] / 1e6:6.2f} MB {'ok ' if row['old_within_limit'] else 'OVER'} | new "
              f"{row['new_base64'] / 1e6:5.2f} MB {row['new_size']:>9} {row['new_mime']}")
    print(f"  within limit: old {summary['payload_within_limit_old']}, new {summary['payload_within_limit_new']}")
    print("\n== Quality hints (expected vs detected) ==")
    for seed, result in report.quality.items():
        misses = [f"{row['case']}: expected {row['expected']} found {row['found']}"
                  for row in result["rows"] if row["expected"] != row["found"]]
        print(f"  {seed}: precision {result['precision']} recall {result['recall']} "
              f"(TP {result['true_positive']}, FP {result['false_positive']}, FN {result['false_negative']})")
        for miss in misses:
            print(f"      {miss}")
    if report.legibility:
        print("\n== Legibility with a fixed local reader (RapidOCR): full image vs provider copy ==")
        for row in report.legibility:
            print(f"  {row['case']:24} CER full {row['cer_full']:.3f}  provider copy {row['cer_provider']:.3f}")
        print(f"  worse by more than 0.02: {summary['legibility_worse'] or 'none'}")
    for row in report.preprocessing:
        print(f"\n== Old page preprocessing vs new intake: {row['case']} ==")
        print(f"  CER old {row['cer_old_page_pipeline']:.3f}  new {row['cer_new_intake']:.3f}")
    for note in report.notes:
        print(f"\nNote: {note}")


def live_cases(directory: str | None) -> list[vision_fixtures.VisionCase]:
    if not directory:
        return [case for case in vision_fixtures.build_cases(include_large=False)
                if not case.reject and (case.truth or case.labels)]
    cases = []
    for path in sorted(Path(directory).iterdir()):
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            continue
        truth = path.with_suffix(".txt")
        if truth.exists():
            cases.append(vision_fixtures.VisionCase(path.stem, path.read_bytes(), truth.read_text(encoding="utf-8").strip(),
                                                    "photo"))
    return cases


def run_live(directory: str | None) -> list[dict]:
    import app as application
    from learnova import vision
    from learnova.ocr.service import normalize_recognition, recognition_instructions

    config = application.app.config
    if str(config.get("AI_MODE")) != "live" or not config.get("ALLOW_LIVE_AI"):
        raise SystemExit("Live evaluation needs AI_MODE=live and ALLOW_LIVE_AI=true.")
    rows = []
    with application.app.test_request_context():
        for case in live_cases(directory):
            prepared = vision.prepare(case.data, application.vision_limits())
            sources = [vision.ImageSource(prepared.data, prepared.mime_type)]
            for page in case.pages:
                extra = vision.prepare(page, application.vision_limits())
                sources.append(vision.ImageSource(extra.data, extra.mime_type))
            # Old: the Project Mode single-page prompt, first page only, no second look.
            old_response = application.ai_service.create_response(
                task_type="ocr_document_recognition", language="German", model=application.VISION_MODEL,
                instructions="You are a careful school-document recognition system. Return structured JSON only.",
                input=[{"role": "user", "content": [
                    {"type": "input_text", "text": recognition_instructions("Other", 1)},
                    {"type": "input_image", "image_url": application.provider_image_url(prepared.data, prepared.mime_type),
                     "detail": "high"}]}],
                max_output_tokens=4000, temperature=0, private_scope="vision-eval")
            old_text = normalize_recognition(application.parse_json(old_response.output_text))["text"]
            new = application.read_images_for(sources, purpose="notes").reading
            new_text = new.text()
            formulas = [block.latex or block.text for block in new.blocks if block.type == "formula"]
            rows.append({
                "case": case.name, "kind": case.kind,
                "cer_old": round(cer(case.truth, old_text), 3) if case.truth else None,
                "cer_new": round(cer(case.truth, new_text), 3) if case.truth else None,
                "wer_new": round(wer(case.truth, new_text), 3) if case.truth else None,
                "math_new": round(math_exact(case.math_truth, formulas + new_text.splitlines()), 3) if case.math_truth else None,
                "labels_new": round(label_recall(case.labels, new_text + " " + " ".join(
                    " ".join([item.description, *item.labels]) for item in new.diagrams)), 3) if case.labels else None,
                "uncertain_new": len(new.uncertain),
            })
            print(json.dumps(rows[-1], ensure_ascii=False))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", help="also write the offline report as JSON to this path")
    parser.add_argument("--no-ocr", action="store_true", help="skip the RapidOCR legibility checks")
    parser.add_argument("--live", action="store_true", help="LIVE, PAID: read cases with the configured model")
    parser.add_argument("--cases", help="with --live: a folder of photos, each with a same-named .txt truth file")
    args = parser.parse_args()
    if args.live:
        run_live(args.cases)
        return
    report = vision_eval.run_offline(with_ocr=not args.no_ocr)
    print_offline(report)
    if args.json:
        Path(args.json).write_text(json.dumps({
            "summary": report.summary(), "intake": report.intake, "payload": report.payload,
            "legibility": report.legibility, "preprocessing": report.preprocessing,
            "quality": report.quality, "notes": report.notes}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

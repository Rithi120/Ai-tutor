"""One-call, opt-in live AI contract smoke test (never part of pytest).

Every automated test in this repository replays recorded or stubbed provider responses.
That verifies the policy thoroughly and verifies the *provider* not at all: whether a real
model reliably emits the contracts this application demands - the 13-dimension moderation
JSON with grounded quotes, the evidence-linked diagnosis schema, a coherent assistant
reply - is a separate question, and this script is how it gets answered.

Each task below makes exactly one call, with the smallest token budget that can still
produce a valid answer, and prints what came back plus whether it satisfied the
production validator. Nothing is written to the database.

    ALLOW_LIVE_AI_TESTS=true python scripts/live_ai_smoke_test.py --task moderation
    ALLOW_LIVE_AI_TESTS=true python scripts/live_ai_smoke_test.py --task all

Afterwards `python scripts/run_moderation_eval.py --usage` reports real latency and cost
from the telemetry these calls leave behind, and /internal/ai-diagnostics shows the
decision mix.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TASKS = ("lesson", "moderation", "assistant", "diagnosis")


def _lesson(application, language: str) -> dict:
    """The original check: does the model still honour the lesson JSON contract?"""

    application.app.config["AI_LESSON_GENERATION_MAX_OUTPUT_TOKENS"] = 220
    return {
        "task_type": "lesson_generation",
        "model": application.app.config["GROQ_FAST_MODEL"],
        "instructions": "Use accurate elementary arithmetic and the required output contract.",
        "input": (
            "Create a tiny lesson about 1 + 1. Return the Learnova lesson JSON structure "
            f"in {language}, with one text question and a one-step worked example."),
        "max_output_tokens": 220,
    }


def _moderation(application, language: str) -> dict:
    """Can a real model produce the 13-dimension contract, with quotes that ground?

    The content is deliberately the hardest *easy* case: biology that is entirely
    legitimate but uses the vocabulary a naive filter would flag. A model that returns
    `sexual_safety: flag` here is the failure this design exists to prevent.
    """

    from learnova import moderation

    items = moderation.flashcard_items(
        "Cell Biology Basics", "Reproduction and cell division for grade 9", [
            {"front": "What is sexual reproduction?",
             "back": "The combination of genetic material from two gametes."},
            {"front": "What does meiosis produce?",
             "back": "Four genetically distinct haploid gametes."},
        ])
    context = moderation.ModerationContext(
        subject="Biology", topic="Reproduction", grade="9", language=language[:2].casefold())
    findings = moderation.inspect(moderation.moderation_text(items))
    return {
        "task_type": "content_moderation",
        "model": application.app.config["GROQ_MODERATION_MODEL"],
        "instructions": moderation.moderation_system_prompt(language),
        "input": moderation.moderation_user_prompt(context, items, findings.signals),
        "max_output_tokens": application.app.config["AI_CONTENT_MODERATION_MAX_OUTPUT_TOKENS"],
        "temperature": 0,
        "_check": lambda text: _check_moderation(text, items),
    }


def _check_moderation(text: str, items) -> dict:
    """Validate, ground the quotes, and decide - the whole pipeline, minus the database."""

    from learnova import moderation
    from learnova.ai_services import service

    payload = service.parse_json(text)
    moderation.validate_classification(payload)
    content = moderation.moderation_text(items)
    classification = moderation.normalize_classification(payload, content=content)
    decision = moderation.decide(classification, moderation.inspect(content),
                                 moderation.ModerationContext(subject="Biology", grade="9"))
    return {
        "decision": decision.decision,
        "reason_codes": list(decision.reason_codes),
        "confidence": classification["confidence"],
        "flagged": moderation.flagged_dimensions(classification),
        "unknown": moderation.unknown_dimensions(classification),
        "dropped_ungrounded_flags": classification["unsupported_flags"],
        "expected": "allow (legitimate biology); anything else is the false-positive "
                    "this design exists to prevent",
    }


def _assistant(application, language: str) -> dict:
    """Does the assistant answer coherently under the research preset?"""

    from learnova import assistant

    window = assistant.build_window(
        [{"role": "user",
          "content": "In two sentences: what is the evidence that vitamin C prevents "
                     "the common cold, and how strong is it?"}],
        token_budget=2000, reserve_for_reply=400)
    return {
        "task_type": "assistant_chat",
        "model": application.app.config["ASSISTANT_MODEL"],
        "instructions": assistant.system_prompt("research", language=language),
        "input": assistant.render_transcript(window),
        "max_output_tokens": 400,
        "temperature": 0.3,
        "_check": lambda text: {
            "reply_characters": len(text.strip()),
            "non_empty": bool(text.strip()),
            "expected": "a short answer that separates the evidence from how strong it is",
            "reply": text.strip()[:400],
        },
    }


def _diagnosis(application, language: str) -> dict:
    """Does the model honour the evidence-linked diagnosis contract?"""

    from learnova import diagnostics

    evidence = diagnostics.build_evidence_bundle(
        question="Solve for x: 2x + 6 = 14",
        expected_answer="4",
        student_answer="x = 10",
        subject="Mathematics",
        concept="linear equations",
        grade="8",
        language=language,
        question_type="short_answer",
    )
    return {
        "task_type": "answer_diagnosis",
        "model": application.app.config["GROQ_DIAGNOSIS_MODEL"],
        "instructions": diagnostics.diagnosis_system_prompt(
            "Mathematics", language, grade="8"),
        "input": diagnostics.diagnosis_user_prompt(evidence),
        "max_output_tokens": application.app.config["AI_ANSWER_DIAGNOSIS_MAX_OUTPUT_TOKENS"],
        "temperature": 0,
        "_check": _check_diagnosis,
    }


def _check_diagnosis(text: str) -> dict:
    from learnova import diagnostics
    from learnova.ai_services import service

    payload = service.parse_json(text)
    diagnostics.validate_diagnosis(payload)
    result = diagnostics.normalize_diagnosis(payload)
    return {
        "correctness_status": result.get("correctness_status"),
        "primary_diagnosis": (result.get("primary_diagnosis") or {}).get("tag"),
        "next_action": result.get("next_action"),
        "expected": "incorrect, with a procedural or arithmetic tag: the student "
                    "subtracted wrongly (2x = 8, so x = 4)",
    }


BUILDERS = {
    "lesson": _lesson, "moderation": _moderation,
    "assistant": _assistant, "diagnosis": _diagnosis,
}


def run_one(application, service, task: str, language: str) -> int:
    spec = BUILDERS[task](application, language)
    check = spec.pop("_check", None)
    print(f"\n=== {task} ===")
    print(f"model: {spec['model']}")
    try:
        with application.app.app_context():
            response = service.create_response(
                language=language, private_scope="live-smoke-test", **spec)
    except Exception as error:  # noqa: BLE001 - a smoke test reports, it does not raise
        category, summary = service._failure_details(error)
        print(json.dumps({"ok": False, "error_category": category, "detail": summary},
                         indent=2))
        return 1

    report = {
        "ok": True,
        "request_id": response.request_id,
        "model": response.model,
        "gateway_validation": response.validation,
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "total_tokens": response.usage.total_tokens,
    }
    if check is not None:
        try:
            report["contract"] = check(response.output_text)
        except Exception as error:  # noqa: BLE001
            report["ok"] = False
            report["contract_error"] = f"{type(error).__name__}: {error}"
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=(*TASKS, "all"), default="lesson")
    parser.add_argument("--language", choices=("en", "de"), default="en")
    args = parser.parse_args()

    if os.getenv("ALLOW_LIVE_AI_TESTS", "").casefold() != "true":
        print("Refusing live call: set ALLOW_LIVE_AI_TESTS=true explicitly.", file=sys.stderr)
        return 2
    if not os.getenv("GROQ_API_KEY"):
        print("Refusing live call: GROQ_API_KEY is not configured.", file=sys.stderr)
        return 2

    import app as application
    from learnova.ai_services import service

    language = "German" if args.language == "de" else "English"
    application.app.config.update(
        AI_MODE="live", ALLOW_LIVE_AI=True, RUN_LIVE_AI_TEST=True, AI_ENFORCE_LIMITS=True)

    tasks = TASKS if args.task == "all" else (args.task,)
    failures = sum(run_one(application, service, task, language) for task in tasks)
    print(f"\n{len(tasks) - failures}/{len(tasks)} task(s) satisfied their contract.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

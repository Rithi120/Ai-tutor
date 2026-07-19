"""One-call, opt-in live AI contract smoke test (never part of pytest)."""

from __future__ import annotations

import argparse
import json
import os
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", choices=("lesson",), default="lesson")
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
        AI_MODE="live", ALLOW_LIVE_AI=True, RUN_LIVE_AI_TEST=True,
        AI_ENFORCE_LIMITS=True,
        AI_LESSON_GENERATION_MAX_OUTPUT_TOKENS=220,
    )
    prompt = (
        "Create a tiny lesson about 1 + 1. Return the Learnova lesson JSON structure "
        f"in {language}, with one text question and a one-step worked example."
    )
    with application.app.app_context():
        response = service.create_response(
            task_type="lesson_generation", language=language, private_scope="live-smoke-test",
            model=application.app.config["GROQ_FAST_MODEL"], input=prompt,
            instructions="Use accurate elementary arithmetic and the required output contract.",
            max_output_tokens=220, temperature=0,
        )
    print(json.dumps({
        "request_id": response.request_id, "model": response.model,
        "validation": response.validation, "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "total_tokens": response.usage.total_tokens,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

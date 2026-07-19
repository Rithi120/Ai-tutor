"""Opt-in provider smoke test; skipped during every normal test run."""

import os

import pytest

import app as application
from learnova.ai_services import service


@pytest.mark.live_ai
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_AI_TEST") != "1",
    reason="Set RUN_LIVE_AI_TEST=1 for the explicitly billable integration test",
)
def test_one_explicit_live_tutor_request():
    application.app.config.update(
        TESTING=False,
        AI_MODE="live",
        ALLOW_LIVE_AI=True,
        RUN_LIVE_AI_TEST=True,
    )
    with application.app.app_context():
        response = service.create_response(
            task_type="tutor_chat",
            language="English",
            prompt_version="live-smoke-v1",
            model=application.app.config["GROQ_FAST_MODEL"],
            instructions="Reply with the single word ready.",
            input="Health check",
            max_output_tokens=8,
            temperature=0,
        )
    assert response.output_text.strip()

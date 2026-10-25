"""Scenario runner for the teaching-quality benchmark (tests/fixtures/teaching_benchmark.json).

Each scenario builds a real learner state through the app's own routes - register with a
grade, stored topic mastery, a generated lesson, graded answers with a diagnosis, earlier
chat turns - with every *setup* AI call answered from fixtures. Only the measured call
(the last chat turn) is handed to `reply_for`, which returns either a fixture reply
(deterministic tests) or a live model reply (scripts/teaching_benchmark.py --live).

The result records what the app sent (instructions, input, chosen level) and how the
reply scores on learnova/evaluation/teaching_rubric.py.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable
from unittest import mock

from learnova.evaluation.teaching_rubric import score_reply

FIXTURE = Path(__file__).with_name("fixtures") / "teaching_benchmark.json"


class _Reply:
    usage = None
    model = "benchmark-fixture"

    def __init__(self, text: str):
        self.output_text = text


def load(path: Path = FIXTURE) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _evaluation(score: int) -> dict[str, Any]:
    return {
        "evaluation": {"is_correct": score >= 80, "score": score, "feedback": "Feedback.",
                       "correction": "", "teacher_tip": "Check each step.", "exception_note": "",
                       "skill_status": "needs_practice" if score < 80 else "developing"},
        "next_question": _next_question(), "summary": None,
    }


def _next_question(subject: str = "Mathematics", concept: str = "Practice") -> dict[str, Any]:
    # A valid follow-up question, so /api/answer completes; the benchmark resets the open
    # question afterwards because it measures the tutor's next turn, not the next item.
    return {
        "id": "q2", "subject": subject, "concept": concept, "difficulty": 1,
        "cognitive_demand": "recall", "type": "checkboxes",
        "prompt": "Which two steps are needed here?", "hint": "Two steps.",
        "options": [{"id": "a", "label": "first step"}, {"id": "b", "label": "second step"}],
        "expected_answer": ["a", "b"], "solution_steps": ["first step", "second step"],
        "rubric": [{"criterion": "Both steps named", "points": 1}],
    }


def _diagnosis(answer: dict[str, Any], concept: str) -> dict[str, Any]:
    tag = answer["tag"]
    correct = tag == "correct"
    return {
        "analysis_version": "diagnosis:v2",
        "correctness_status": "correct" if correct else "incorrect",
        "score": {"points": 1.0 if correct else 0.3, "max_points": 1.0, "rubric_evidence": [
            {"criterion": "Method chosen", "met": True, "evidence_ids": ["e1"]}]},
        "concepts_assessed": [concept],
        "primary_diagnosis": {"tag": tag, "statement": answer.get("statement", ""), "evidence_ids": ["e1"]},
        "secondary_diagnoses": [],
        "evidence": [{"id": "e1", "source": "student_answer", "quote": answer.get("quote", "b")}],
        "prerequisite_gaps": [],
        "missing_evidence": False,
        "confidence": {"value": 0.85, "basis": "The step is written out."},
        "recommended_intervention": answer.get("intervention", "worked_example"),
        "candidate_question_constraints": {"concept": concept, "difficulty": 1},
        "student_facing_explanation": answer.get("statement", ""),
        "internal_diagnostic_summary": "Teacher-only note.",
    }


def run_scenario(application, data: dict[str, Any], scenario: dict[str, Any],
                 reply_for: Callable[[dict[str, Any], dict[str, Any]], str]) -> dict[str, Any]:
    app = application.app
    # The runner recreates the schema; refuse anything but a throwaway SQLite file.
    url = str(app.config.get("SQLALCHEMY_DATABASE_URI", ""))
    if not url.startswith("sqlite:///") or not any(
            marker in url.replace("\\", "/").lower() for marker in ("/tmp", "/temp/", "pytest", "benchmark")):
        raise RuntimeError(f"refusing to run the benchmark against {url!r}: use a temporary SQLite file")
    app.config.update(TESTING=True, FEATURE_ADAPTIVE_DIAGNOSTICS=True,
                      FEATURE_DIAGNOSTIC_VERIFICATION=True, AI_MODE="cached")
    application.SESSIONS.clear()
    with app.app_context():
        application.db.drop_all()
        application.db.create_all()
    client = app.test_client()
    client.post("/register", data={"username": "bench", "email": "bench@example.com",
                                   "password": "correct-horse-battery", "language": "en",
                                   "grade": scenario["grade"]}, follow_redirects=True)
    client.post("/settings/grade", data={"grade": scenario["grade"]})
    lesson = data["lessons"][scenario["lesson"]]
    subject = scenario.get("subject", "Mathematics")
    concept = lesson["question"]["concept"]

    with app.app_context():
        user = application.db.session.execute(application.db.select(application.User)).scalar_one()
        for row in scenario.get("mastery", []):
            score = float(row["score"])
            application.db.session.add(application.ConceptMastery(
                user_id=user.id, subject=row.get("subject", subject), concept=row["concept"],
                attempts=row.get("attempts", 5), total_score=score * row.get("attempts", 5),
                mastery_score=score, evidence_weight=row.get("evidence_weight", 3.0), uncertainty=0.25,
                correct_attempts=row.get("correct_attempts", 4 if score >= 70 else 1),
                consecutive_correct=row.get("consecutive_correct", 3 if score >= 85 else 0)))
        application.db.session.commit()

    responses: dict[str, Any] = {}
    captured: list[dict[str, Any]] = []

    def route(*, task_type, **kwargs):
        captured.append({"task_type": task_type, **kwargs})
        value = responses[task_type]
        return _Reply(value if isinstance(value, str) else json.dumps(value))

    with mock.patch.object(application, "create_response", route):
        responses["lesson_generation"] = lesson
        started = client.post("/api/analyze", data={"study_goal": scenario.get("goal", lesson["lesson_title"]),
                                                    "subject": subject})
        assert started.status_code == 200, started.get_data(as_text=True)[:300]
        session_id = started.get_json()["session_id"]
        for answer in scenario.get("answers", []):
            responses.update({
                "answer_evaluation": _evaluation(answer.get("score", 30)),
                "answer_diagnosis": _diagnosis(answer, concept),
                "diagnosis_verification": {"agrees": True, "reason": "supported", "better_tag": ""},
                "question_generation": {"question": _next_question(subject, concept)},
            })
            client.post("/api/answer", json={"session_id": session_id, "answer": answer.get("quote", "b"),
                                             "hints_used": False, "retry_count": 0, "response_confidence": 60})
            # Keep the lesson's question open: the benchmark is about the tutor's next turn.
            application.SESSIONS[session_id]["current_question"] = dict(lesson["question"])
        for turn in scenario.get("turns", []):
            responses["tutor_chat"] = turn["reply"]
            client.post("/api/chat", json={"session_id": session_id, "message": turn.get("message", ""),
                                           **({"control": turn["control"]} if turn.get("control") else {})})

    measure = scenario["measure"]
    holder: dict[str, Any] = {}

    def measured(*, task_type, **kwargs):
        call = {"task_type": task_type, **kwargs}
        holder["call"] = call
        started_at = time.perf_counter()
        holder["reply"] = reply_for(scenario, call)
        holder["seconds"] = round(time.perf_counter() - started_at, 3)
        return _Reply(holder["reply"])

    with mock.patch.object(application, "create_response", measured):
        response = client.post("/api/chat", json={
            "session_id": session_id, "message": measure.get("message", ""),
            **({"control": measure["control"]} if measure.get("control") else {})})
    body = response.get_json() or {}
    call = holder.get("call", {})
    expect = dict(scenario.get("expect", {}))
    if scenario.get("turns") and expect.get("compare_previous"):
        expect["previous_reply"] = scenario["turns"][-1]["reply"]
    return {
        "id": scenario["id"], "status": response.status_code, "teaching": body.get("teaching") or {},
        "instructions": str(call.get("instructions", "")), "input": str(call.get("input", "")),
        "model": call.get("model"), "reply": holder.get("reply", ""), "seconds": holder.get("seconds"),
        "score": score_reply(holder.get("reply", ""), expect),
    }


def prompt_failures(result: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    """What the app *sent* that contradicts the scenario's prompt expectations."""

    expected = scenario.get("prompt_expect", {})
    failures = []
    if result["status"] != 200:
        return [f"chat returned {result['status']}"]
    level = expected.get("level")
    if level is not None and result["teaching"].get("level") != level:
        failures.append(f"level {result['teaching'].get('level')} != {level}")
    for text in expected.get("instructions_contain", []):
        if text not in result["instructions"]:
            failures.append(f"instructions lack: {text}")
    for text in expected.get("instructions_absent", []):
        if text in result["instructions"]:
            failures.append(f"instructions contain: {text}")
    for text in expected.get("input_contain", []):
        if text not in result["input"]:
            failures.append(f"input lacks: {text}")
    for text in expected.get("input_absent", []):
        if text in result["input"]:
            failures.append(f"input contains: {text}")
    return failures

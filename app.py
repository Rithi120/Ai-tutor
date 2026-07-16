import base64
import json
import os
import re
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from openai import OpenAI


load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

VISION_MODEL = os.getenv(
    "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
TUTOR_MODEL = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-120b")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
SESSIONS = {}


TUTOR_RULES = """You are a patient expert tutor for students of any age and level.
Teach only the selected subject, study goal, and concepts supported by the student's material.
Use the student's apparent level and explain in respectful baby steps without childish language.
Define unfamiliar terms, show how each step connects, identify common mistakes, give practical teacher tips, and mention relevant exceptions or disputed interpretations.
Adapt your teaching method to the subject: use worked calculations for mathematics and science, examples and corrections for languages, chronology and cause/effect for history, and evidence-based explanations for other subjects.
For mathematics, never skip transformations. Avoid LaTeX commands and use readable symbols such as ÷, ×, parentheses, and √.
Never reveal hidden chain-of-thought. Give concise instructional explanations and verifiable steps instead."""


def client():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY is not configured.")
    return OpenAI(api_key=api_key, base_url=GROQ_BASE_URL)


def parse_json(text):
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(cleaned)


def image_data_url(upload):
    if upload.mimetype not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Please upload a JPG, PNG, or WebP image.")
    payload = base64.b64encode(upload.read()).decode("ascii")
    return f"data:{upload.mimetype};base64,{payload}"


def mastery_snapshot(session):
    concepts = []
    for name, record in session["mastery"].items():
        attempts = record["attempts"]
        average = round(record["total_score"] / attempts) if attempts else 0
        if not attempts:
            status = "not_tested"
        elif average < 55:
            status = "needs_practice"
        elif average < 80:
            status = "developing"
        else:
            status = "mastered"
        concepts.append({"concept": name, "attempts": attempts,
                        "average_score": average, "status": status})
    return sorted(concepts, key=lambda item: (item["average_score"] if item["attempts"] else -1, item["attempts"]))


def normalize_question_concept(session, question):
    names = list(session["mastery"])
    requested = str(question.get("concept", "")).strip().casefold()
    exact = next(
        (name for name in names if name.casefold() == requested), None)
    if exact:
        question["concept"] = exact
        return
    weakest = mastery_snapshot(session)
    question["concept"] = weakest[0]["concept"] if weakest else (
        names[0] if names else session.get("subject", "General studies"))


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/analyze")
def analyze_material():
    uploads = [upload for upload in request.files.getlist("images") if upload.filename]
    study_goal = request.form.get("study_goal", "").strip()[:3000]
    subject = request.form.get("subject", "Other").strip()[:80] or "Other"
    language = request.form.get("language", "English")
    if language not in {"English", "German"}:
        language = "English"
    if not uploads and not study_goal:
        return jsonify(error="Describe what you want to learn or attach study material."), 400
    if len(uploads) > 4:
        return jsonify(error="Upload no more than four images for this MVP."), 400

    try:
        content = [{
            "type": "input_text",
            "text": f"Create one lesson for the subject '{subject}'. The student's study goal is: {study_goal or 'Understand the attached material'}. Use all attached images as supporting study material. Write all student-facing content in {language}.\n" + """Return valid JSON only in this exact shape:
{
  "lesson_title": "short specific title",
  "detected_level": "estimated school level",
  "concepts": [{"name": "concept", "evidence": "what in the images shows it"}],
  "explanation": "a complete step-by-step explanation using the uploaded examples; define every symbol and explain why each step follows",
  "worked_example": {"problem": "specific demonstration, example, text analysis, timeline task, or calculation matching the subject", "steps": ["small teaching step"], "answer": "clear conclusion or answer"},
  "teacher_tips": ["specific useful technique, mental check, shortcut, or memory aid for this exact material"],
  "exceptions": ["important case where a visible rule does not apply, needs a condition, or changes; use an empty list only if genuinely none exist"],
  "question": {"id": "q1", "concept": "one detected concept", "difficulty": 1, "type": "multiple_choice", "prompt": "one easy answerable question", "hint": "small hint", "options": [{"id": "a", "label": "answer choice"}, {"id": "b", "label": "answer choice"}, {"id": "c", "label": "answer choice"}, {"id": "d", "label": "answer choice"}], "expected_answer": "the correct option id"}
}
The demonstration must use many small steps rather than combining ideas. Never write 'obviously', 'simply', or 'just'.
Give 2 to 4 teacher_tips that an excellent classroom teacher would actually use. Mention common traps and quick ways to check an answer.
For exceptions, state the exact condition and give a tiny example. Do not invent exceptions unrelated to the uploaded material.
The first question must be easy, check understanding of the explanation, and not copy the worked example exactly. It must have exactly one correct option."""
        }]
        for upload in uploads:
            content.append(
                {"type": "input_image", "image_url": image_data_url(upload), "detail": "high"})

        response = client().responses.create(
            model=VISION_MODEL if uploads else TUTOR_MODEL,
            instructions=TUTOR_RULES,
            input=[{"role": "user", "content": content}],
        )
        lesson = parse_json(response.output_text)
        session_id = uuid.uuid4().hex
        SESSIONS[session_id] = {
            "lesson": lesson,
            "history": [],
            "chat_history": [],
            "language": language,
            "subject": subject,
            "test_total": 5,
            "current_question": lesson["question"],
            "mastery": {
                concept["name"]: {"attempts": 0, "total_score": 0}
                for concept in lesson["concepts"]
            },
        }
        normalize_question_concept(SESSIONS[session_id], lesson["question"])
        public_lesson = {key: value for key,
                         value in lesson.items() if key != "question"}
        question = {key: value for key, value in lesson["question"].items(
        ) if key != "expected_answer"}
        return jsonify(session_id=session_id, lesson=public_lesson, question=question)
    except (ValueError, json.JSONDecodeError, KeyError) as error:
        return jsonify(error=f"The material could not be read reliably: {error}"), 422
    except Exception as error:
        app.logger.exception("Material analysis failed")
        message = str(error) if isinstance(
            error, RuntimeError) else "The tutor service is temporarily unavailable."
        return jsonify(error=message), 500


@app.post("/api/answer")
def check_answer():
    payload = request.get_json(silent=True) or {}
    session = SESSIONS.get(payload.get("session_id"))
    raw_answer = payload.get("answer", "")
    answer = json.dumps(raw_answer, ensure_ascii=False) if isinstance(
        raw_answer, list) else str(raw_answer).strip()
    if not session:
        return jsonify(error="This lesson expired. Upload the material again."), 404
    if raw_answer is None or raw_answer == "" or (isinstance(raw_answer, list) and not raw_answer):
        return jsonify(error="Write an answer before checking it."), 400
    if payload.get("language") in {"English", "German"}:
        session["language"] = payload["language"]

    question = session["current_question"]
    question_number = len(session["history"]) + 1
    is_final = question_number >= session["test_total"]
    next_question_number = question_number + 1
    question_types = {
        2: "checkboxes",
        3: "dropdown",
        4: "ordering",
        5: "text",
    }
    next_question_type = question_types.get(next_question_number, "text")
    context = {
        "subject": session.get("subject", "Other"),
        "lesson_title": session["lesson"]["lesson_title"],
        "concepts": session["lesson"]["concepts"],
        "teacher_tips": session["lesson"].get("teacher_tips", []),
        "exceptions": session["lesson"].get("exceptions", []),
        "question": question,
        "student_answer": answer,
        "previous_results": session["history"][-4:],
        "concept_mastery": mastery_snapshot(session),
        "response_language": session["language"],
        "question_number": question_number,
        "test_total": session["test_total"],
        "is_final_question": is_final,
    }
    next_question_example = None if is_final else {
        "id": f"q{next_question_number}",
        "concept": "weakest relevant concept",
        "difficulty": next_question_number,
        "type": next_question_type,
        "prompt": "question",
        "hint": "small hint",
        "options": [{"id": "a", "label": "choice or ordering item"}],
        "expected_answer": "option id, list of ids, ordered list of ids, or written answer",
    }
    summary_example = {
        "overall": "short honest result",
        "strengths": ["specific strength"],
        "weaknesses": ["specific weak concept and misconception"],
        "next_steps": ["specific practice action"],
    } if is_final else None
    prompt = f"""Evaluate this student's answer, then create the next personalized question.
Lesson data: {json.dumps(context, ensure_ascii=False)}

Return this exact JSON shape:
{{
  "evaluation": {{
    "is_correct": true,
    "score": 0,
    "feedback": "specific step-by-step explanation of what was right or wrong",
    "correction": "a corrected solution in small numbered-style steps, empty if fully correct",
    "teacher_tip": "one practical technique or quick self-check tailored to this answer",
    "exception_note": "a relevant exception or boundary case, empty if none applies",
    "skill_status": "needs_practice or developing or mastered"
  }},
  "next_question": {json.dumps(next_question_example, ensure_ascii=False)},
  "summary": {json.dumps(summary_example, ensure_ascii=False)}
}}
Score is an integer from 0 to 100. If the answer is wrong, keep or lower difficulty and target the misconception.
Write all student-facing JSON values in response_language.
The teacher_tip must be concrete and immediately usable. Only provide exception_note when it is relevant to the current concept.
For multiple_choice and dropdown use exactly one correct option and 4 options. For checkboxes use 4 or 5 options with 2 or 3 correct answers. For ordering provide 4 items in a shuffled order. For text, options must be an empty list.
Always target the weakest concept from concept_mastery. Untested concepts count as weak. Increase difficulty according to the question number, but keep the content tied to the uploaded material.
Follow the exact null/object structure shown above. Do not replace a required object with null."""

    try:
        response = client().responses.create(
            model=TUTOR_MODEL,
            instructions=TUTOR_RULES,
            input=prompt,
        )
        result = parse_json(response.output_text)
        evaluation = result["evaluation"]
        next_question = result.get("next_question")
        session["history"].append({
            "concept": question["concept"],
            "difficulty": question["difficulty"],
            "score": evaluation["score"],
            "skill_status": evaluation["skill_status"],
        })
        concept_record = session["mastery"].setdefault(
            question["concept"], {"attempts": 0, "total_score": 0}
        )
        concept_record["attempts"] += 1
        concept_record["total_score"] += int(evaluation["score"])
        average = round(
            sum(item["score"] for item in session["history"]) / len(session["history"]))
        mastery = mastery_snapshot(session)
        public_question = None
        if not is_final:
            if not isinstance(next_question, dict):
                raise KeyError("next_question")
            normalize_question_concept(session, next_question)
            next_question["difficulty"] = next_question_number
            next_question["type"] = next_question_type
            next_question.setdefault("options", [])
            session["current_question"] = next_question
            public_question = {
                key: value for key, value in next_question.items() if key != "expected_answer"}
        return jsonify(evaluation=evaluation, next_question=public_question,
                       complete=is_final, summary=result.get("summary") if is_final else None, progress={
            "answered": len(session["history"]),
            "total": session["test_total"],
            "average_score": average,
            "mastery": mastery,
            "weakest_concept": mastery[0]["concept"] if mastery else None,
        })
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        return jsonify(error=f"The feedback could not be prepared reliably: {error}"), 422
    except Exception:
        app.logger.exception("Answer evaluation failed")
        return jsonify(error="The tutor service is temporarily unavailable."), 500


@app.post("/api/chat")
def tutor_chat():
    payload = request.get_json(silent=True) or {}
    session = SESSIONS.get(payload.get("session_id"))
    message = str(payload.get("message", "")).strip()
    if not session:
        return jsonify(error="This lesson expired. Upload the material again."), 404
    if not message:
        return jsonify(error="Write a message for your tutor."), 400
    if payload.get("language") in {"English", "German"}:
        session["language"] = payload["language"]

    chat_context = {
        "subject": session.get("subject", "Other"),
        "lesson_title": session["lesson"]["lesson_title"],
        "explanation": session["lesson"]["explanation"],
        "concepts": session["lesson"]["concepts"],
        "teacher_tips": session["lesson"].get("teacher_tips", []),
        "exceptions": session["lesson"].get("exceptions", []),
        "mastery": mastery_snapshot(session),
        "current_question": session["current_question"],
        "recent_results": session["history"][-4:],
        "recent_chat": session["chat_history"][-6:],
        "student_message": message,
        "response_language": session["language"],
    }
    prompt = f"""Act as the student's live math tutor using this lesson state:
{json.dumps(chat_context, ensure_ascii=False)}

Reply only in response_language and focus on the student's weakest concept when relevant.
Teach like an excellent classroom teacher: offer practical techniques, memory aids, common-error warnings, and a quick way to verify understanding.
When a rule, interpretation, or method has restrictions or exceptions, state the exact condition and show a small example.
Do not invent an exception when the rule has none relevant to the lesson.
Explain in respectful baby steps regardless of the student's age or the topic's complexity.
Break the idea into the smallest useful steps, define each new symbol or term, and explain why each operation is valid.
Never skip a transformation and never use words such as "obviously", "simply", or "just".
For a complex explanation, teach one small chunk and check understanding before moving to the next chunk.
For calculations, independently verify the arithmetic. For languages and humanities, check grammar, evidence, chronology, context, and whether multiple interpretations can be valid.
Never call an answer wrong if your own correction or accepted interpretation agrees with it.
Treat a short reply such as "12" as the answer to the last question you asked in recent_chat.
If the student's answer is correct, clearly say it is correct and continue from that result.
If they ask for help on the current question, guide them with one useful step or question instead of giving the final answer.
If they ask a general question about the uploaded lesson, explain it clearly using the lesson's notation.
Do not discuss unrelated subjects. End with a short question that keeps the student thinking."""

    try:
        response = client().responses.create(
            model=TUTOR_MODEL,
            instructions="You are a careful, friendly Socratic tutor across school subjects. Teach complex ideas in respectful baby steps without removing their real difficulty. Factual accuracy and internal consistency are mandatory.",
            input=prompt,
        )
        reply = response.output_text.strip()
        session["chat_history"].extend([
            {"role": "student", "content": message},
            {"role": "tutor", "content": reply},
        ])
        return jsonify(reply=reply, mastery=mastery_snapshot(session))
    except Exception:
        app.logger.exception("Tutor chat failed")
        return jsonify(error="The tutor is temporarily unavailable."), 500


@app.post("/api/translate")
def translate_content():
    payload = request.get_json(silent=True) or {}
    session = SESSIONS.get(payload.get("session_id"))
    language = payload.get("language")
    texts = payload.get("texts", [])
    if not session:
        return jsonify(error="This lesson expired. Upload the material again."), 404
    if language not in {"English", "German"}:
        return jsonify(error="Unsupported language."), 400
    if not isinstance(texts, list) or len(texts) > 80:
        return jsonify(error="Invalid translation request."), 400
    cleaned = [str(item)[:3000] for item in texts]
    if sum(len(item) for item in cleaned) > 30000:
        return jsonify(error="Too much text to translate at once."), 400

    prompt = f"""Translate each string into {language}.
Return JSON exactly as {{"translations": ["translated string"]}} with the same number and order of items.
Preserve all numbers, mathematical symbols, formulas, option letters, and line breaks.
Translate the explanatory language naturally. If a string is already in {language}, keep it unchanged.
Strings: {json.dumps(cleaned, ensure_ascii=False)}"""
    try:
        response = client().responses.create(
            model=TUTOR_MODEL,
            instructions="You are a precise educational translator. Return valid JSON only.",
            input=prompt,
        )
        result = parse_json(response.output_text)
        translations = result["translations"]
        if not isinstance(translations, list) or len(translations) != len(cleaned):
            raise ValueError("Translation count mismatch")
        session["language"] = language
        return jsonify(translations=translations)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        return jsonify(error=f"The content could not be translated reliably: {error}"), 422
    except Exception:
        app.logger.exception("Content translation failed")
        return jsonify(error="The translation service is temporarily unavailable."), 500


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))

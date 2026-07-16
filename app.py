import base64
import json
import os
import re
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, session as flask_session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI
from sqlalchemy import UniqueConstraint, func
from werkzeug.security import check_password_hash, generate_password_hash


load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///numeri.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"  # pyright: ignore[reportAttributeAccessIssue]
login_manager.login_message = "Please log in to use your tutor."
login_manager.session_protection = "strong"

VISION_MODEL = os.getenv(
    "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
TUTOR_MODEL = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-20b")
FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
LESSON_TOKEN_LIMIT = int(os.getenv("LESSON_TOKEN_LIMIT", "1800"))
ANSWER_TOKEN_LIMIT = int(os.getenv("ANSWER_TOKEN_LIMIT", "1100"))
CHAT_TOKEN_LIMIT = int(os.getenv("CHAT_TOKEN_LIMIT", "350"))
TRANSLATE_TOKEN_LIMIT = int(os.getenv("TRANSLATE_TOKEN_LIMIT", "2500"))
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
SESSIONS = {}


def utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    lessons = db.relationship("Lesson", back_populates="user", cascade="all, delete-orphan")
    concept_masteries = db.relationship("ConceptMastery", back_populates="user", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Lesson(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    session_id = db.Column(db.String(32), unique=True, nullable=False, index=True)
    subject = db.Column(db.String(80), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    content_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    user = db.relationship("User", back_populates="lessons")
    attempts = db.relationship("Attempt", back_populates="lesson", cascade="all, delete-orphan")
    study_session = db.relationship("StudySession", back_populates="lesson", cascade="all, delete-orphan", uselist=False)
    chat_messages = db.relationship("ChatMessage", back_populates="lesson", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class Attempt(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    concept = db.Column(db.String(255), nullable=False, index=True)
    student_answer = db.Column(db.Text, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    feedback = db.Column(db.Text, nullable=False)
    difficulty = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    lesson = db.relationship("Lesson", back_populates="attempts")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class ConceptMastery(db.Model):
    __table_args__ = (UniqueConstraint("user_id", "subject", "concept", name="uq_user_subject_concept"),)
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    subject = db.Column(db.String(80), nullable=False, index=True)
    concept = db.Column(db.String(255), nullable=False, index=True)
    attempts = db.Column(db.Integer, nullable=False, default=0)
    total_score = db.Column(db.Integer, nullable=False, default=0)
    mastery_score = db.Column(db.Float, nullable=False, default=0)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    user = db.relationship("User", back_populates="concept_masteries")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class StudySession(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), unique=True, nullable=False, index=True)
    state_json = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    lesson = db.relationship("Lesson", back_populates="study_session")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=False, index=True)
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    lesson = db.relationship("Lesson", back_populates="chat_messages")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


@app.cli.command("init-db")
def init_db_command():
    """Create the application database tables."""
    db.create_all()
    print("Initialized the database.")


def ensure_database():
    """Create missing tables so a fresh local install works on first start."""
    with app.app_context():
        db.create_all()


ensure_database()


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


def create_response(**kwargs: Any):
    """Typed boundary for Groq's OpenAI-compatible Responses API."""
    return client().responses.create(**kwargs)


def quality_options() -> dict[str, Any]:
    """Keep reasoning useful but small on GPT-OSS; allow model overrides safely."""
    return {"reasoning": {"effort": "low"}} if TUTOR_MODEL.startswith("openai/gpt-oss") else {}


def log_usage(response, task):
    usage = getattr(response, "usage", None)
    if usage:
        app.logger.info("AI usage task=%s model=%s input=%s output=%s total=%s", task,
                        getattr(response, "model", "unknown"),
                        getattr(usage, "input_tokens", 0), getattr(usage, "output_tokens", 0),
                        getattr(usage, "total_tokens", 0))


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


def persist_lesson(session_id, subject, lesson):
    record = Lesson(user_id=current_user.id, session_id=session_id, subject=subject,
                    title=str(lesson.get("lesson_title", "Lesson"))[:255],
                    content_json=json.dumps(lesson, ensure_ascii=False))
    db.session.add(record)
    db.session.flush()
    db.session.add(StudySession(lesson_id=record.id, state_json=json.dumps(SESSIONS[session_id], ensure_ascii=False)))
    db.session.commit()
    return record


def owned_session(session_id):
    session = SESSIONS.get(session_id)
    if not session or session.get("user_id") != current_user.id:
        return None
    return session


def save_session_state(session_id, commit=True):
    state = owned_session(session_id)
    if not state:
        return
    saved = db.session.scalar(
        db.select(StudySession).join(Lesson).where(
            Lesson.session_id == session_id, Lesson.user_id == current_user.id))
    if saved:
        saved.state_json = json.dumps(state, ensure_ascii=False)
        saved.updated_at = utcnow()
        if commit:
            db.session.commit()


DASHBOARD_TEXT = {
    "en": {"progress": "YOUR PROGRESS", "title": "Learning dashboard", "new": "New lesson",
           "logout": "Log out", "practice": "Practise weak points", "subjects": "Subjects",
           "attempts": "attempts", "no_results": "No quiz results yet.", "weak": "Weakest concepts",
           "weak_empty": "Your weak points will appear after your first quiz.", "mistakes": "Recent mistakes",
           "no_mistakes": "No recent mistakes.", "your_answer": "Your answer", "sessions": "Saved lessons and chats",
           "no_sessions": "No saved lessons yet.", "resume": "Resume", "view": "View history",
           "chat_messages": "chat messages", "level": "Level", "no_chat": "No chat messages yet.", "you": "You"},
    "de": {"progress": "DEIN FORTSCHRITT", "title": "Lernübersicht", "new": "Neue Lektion",
           "logout": "Abmelden", "practice": "Schwachstellen üben", "subjects": "Fächer",
           "attempts": "Versuche", "no_results": "Noch keine Testergebnisse.", "weak": "Schwächste Konzepte",
           "weak_empty": "Deine Schwachstellen erscheinen nach dem ersten Quiz.", "mistakes": "Letzte Fehler",
           "no_mistakes": "Keine aktuellen Fehler.", "your_answer": "Deine Antwort", "sessions": "Gespeicherte Lektionen und Chats",
           "no_sessions": "Noch keine Lektionen gespeichert.", "resume": "Fortsetzen", "view": "Verlauf ansehen",
           "chat_messages": "Chatnachrichten", "level": "Stufe", "no_chat": "Noch keine Chatnachrichten.", "you": "Du"},
}


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:255]
        password = request.form.get("password", "")
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash("Enter a valid email address.", "error")
        elif len(password) < 8:
            flash("Use a password with at least 8 characters.", "error")
        elif db.session.scalar(db.select(User).where(User.email == email)):
            flash("An account with that email already exists.", "error")
        else:
            user = User(email=email)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            return redirect(url_for("index"))
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:255]
        user = db.session.scalar(db.select(User).where(User.email == email))
        if not user or not user.check_password(request.form.get("password", "")):
            flash("Invalid email or password.", "error")
        else:
            login_user(user, remember=bool(request.form.get("remember")))
            return redirect(url_for("index"))
    return render_template("auth.html", mode="login")


@app.post("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


@app.get("/")
@login_required
def index():
    session_id = request.args.get("session_id", "")
    session = owned_session(session_id) if session_id else None
    bootstrap = None
    if session:
        bootstrap = {
            "session_id": session_id,
            "lesson": {key: value for key, value in session["lesson"].items() if key != "question"},
            "question": {key: value for key, value in session["current_question"].items() if key != "expected_answer"},
        }
    return render_template("index.html", bootstrap=bootstrap)


@app.get("/dashboard")
@login_required
def dashboard():
    language = request.args.get("lang")
    if language in DASHBOARD_TEXT:
        flask_session["dashboard_language"] = language
    language = flask_session.get("dashboard_language", "en")
    masteries = db.session.scalars(
        db.select(ConceptMastery).where(ConceptMastery.user_id == current_user.id)
        .order_by(ConceptMastery.mastery_score, ConceptMastery.updated_at.desc())
    ).all()
    subject_rows = db.session.execute(
        db.select(ConceptMastery.subject, func.avg(ConceptMastery.mastery_score), func.sum(ConceptMastery.attempts))
        .where(ConceptMastery.user_id == current_user.id).group_by(ConceptMastery.subject)
        .order_by(func.avg(ConceptMastery.mastery_score))
    ).all()
    mistakes = db.session.scalars(
        db.select(Attempt).join(Lesson).where(Lesson.user_id == current_user.id, Attempt.score < 80)
        .order_by(Attempt.timestamp.desc()).limit(10)
    ).all()
    lessons = db.session.scalars(
        db.select(Lesson).where(Lesson.user_id == current_user.id)
        .order_by(Lesson.created_at.desc()).limit(20)).all()
    return render_template("dashboard.html", masteries=masteries[:8], subjects=subject_rows,
                           mistakes=mistakes, lessons=lessons, language=language,
                           text=DASHBOARD_TEXT[language])


@app.get("/lessons/<int:lesson_id>")
@login_required
def lesson_history(lesson_id):
    lesson = db.session.scalar(db.select(Lesson).where(
        Lesson.id == lesson_id, Lesson.user_id == current_user.id))
    if not lesson:
        return "Lesson not found", 404
    language = flask_session.get("dashboard_language", "en")
    return render_template("lesson_history.html", lesson=lesson, language=language,
                           text=DASHBOARD_TEXT[language])


@app.post("/lessons/<int:lesson_id>/resume")
@login_required
def resume_lesson(lesson_id):
    lesson = db.session.scalar(db.select(Lesson).where(
        Lesson.id == lesson_id, Lesson.user_id == current_user.id))
    if not lesson or not lesson.study_session:
        flash("This saved lesson cannot be resumed.", "error")
        return redirect(url_for("dashboard"))
    try:
        state = json.loads(lesson.study_session.state_json)
        state["user_id"] = current_user.id
        SESSIONS[lesson.session_id] = state
    except (json.JSONDecodeError, TypeError):
        flash("This saved lesson is damaged and cannot be resumed.", "error")
        return redirect(url_for("dashboard"))
    return redirect(url_for("index", session_id=lesson.session_id))


@app.post("/practice-weak")
@login_required
def practice_weak():
    subject = request.form.get("subject", "").strip()[:80]
    query = db.select(ConceptMastery).where(ConceptMastery.user_id == current_user.id)
    if subject:
        query = query.where(ConceptMastery.subject == subject)
    weak = db.session.scalars(query.order_by(ConceptMastery.mastery_score, ConceptMastery.attempts).limit(5)).all()
    if not weak:
        flash("Complete a quiz first so Learnova can find your weak points.", "error")
        return redirect(url_for("dashboard"))
    subject = subject or weak[0].subject
    concepts = [{"name": item.concept, "mastery": round(item.mastery_score)} for item in weak]
    prompt = f"""Create a focused revision lesson for {subject} from these saved weak concepts:
{json.dumps(concepts, ensure_ascii=False)}
Return valid JSON only in the same shape:
{{"lesson_title":"short title","detected_level":"adaptive review","concepts":[{{"name":"concept","evidence":"saved weak point"}}],"explanation":"step-by-step review","worked_example":{{"problem":"example","steps":["small step"],"answer":"answer"}},"teacher_tips":["tip"],"exceptions":[],"question":{{"id":"q1","concept":"one listed concept","difficulty":1,"type":"multiple_choice","prompt":"question targeting the weak concept","hint":"hint","options":[{{"id":"a","label":"choice"}},{{"id":"b","label":"choice"}},{{"id":"c","label":"choice"}},{{"id":"d","label":"choice"}}],"expected_answer":"correct option id"}}}}
Use only the listed concepts, target the weakest first, and make exactly one option correct."""
    try:
        response = create_response(model=TUTOR_MODEL, instructions=TUTOR_RULES, input=prompt,
                                             max_output_tokens=LESSON_TOKEN_LIMIT, temperature=0.2,
                                             **quality_options())
        log_usage(response, "weak-practice")
        lesson = parse_json(response.output_text)
        session_id = uuid.uuid4().hex
        SESSIONS[session_id] = {
            "user_id": current_user.id, "lesson": lesson, "history": [], "chat_history": [],
            "language": "English", "subject": subject, "test_total": 5,
            "current_question": lesson["question"],
            "mastery": {item["name"]: {"attempts": 0, "total_score": 0} for item in lesson["concepts"]},
        }
        normalize_question_concept(SESSIONS[session_id], lesson["question"])
        persist_lesson(session_id, subject, lesson)
        return redirect(url_for("index", session_id=session_id))
    except Exception:
        app.logger.exception("Weak-point practice generation failed")
        flash("The practice lesson could not be generated right now.", "error")
        return redirect(url_for("dashboard"))


@app.post("/api/analyze")
@login_required
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

        response = create_response(
            model=VISION_MODEL if uploads else TUTOR_MODEL,
            instructions=TUTOR_RULES,
            input=[{"role": "user", "content": content}],
            max_output_tokens=LESSON_TOKEN_LIMIT,
            temperature=0.2,
            **(quality_options() if not uploads else {}),
        )
        log_usage(response, "vision-lesson" if uploads else "text-lesson")
        lesson = parse_json(response.output_text)
        session_id = uuid.uuid4().hex
        SESSIONS[session_id] = {
            "user_id": current_user.id,
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
        persist_lesson(session_id, subject, lesson)
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
@login_required
def check_answer():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
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
        "concepts": [item.get("name", "") for item in session["lesson"]["concepts"]],
        "teacher_tips": session["lesson"].get("teacher_tips", [])[:2],
        "exceptions": session["lesson"].get("exceptions", [])[:2],
        "question": question,
        "student_answer": answer,
        "previous_results": session["history"][-3:],
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
        response = create_response(
            model=TUTOR_MODEL,
            instructions=TUTOR_RULES,
            input=prompt,
            max_output_tokens=ANSWER_TOKEN_LIMIT,
            temperature=0.1,
            **quality_options(),
        )
        log_usage(response, "answer")
        result = parse_json(response.output_text)
        evaluation = result["evaluation"]
        score = max(0, min(100, int(evaluation["score"])))
        evaluation["score"] = score
        next_question = result.get("next_question")
        session["history"].append({
            "concept": question["concept"],
            "difficulty": question["difficulty"],
            "score": score,
            "skill_status": evaluation["skill_status"],
        })
        concept_record = session["mastery"].setdefault(
            question["concept"], {"attempts": 0, "total_score": 0}
        )
        concept_record["attempts"] += 1
        concept_record["total_score"] += score
        lesson_record = db.session.scalar(
            db.select(Lesson).where(Lesson.session_id == payload.get("session_id"),
                                    Lesson.user_id == current_user.id)
        )
        if not lesson_record:
            raise KeyError("lesson")
        attempt = Attempt(
            lesson=lesson_record,
            question=str(question.get("prompt", "")),
            concept=str(question.get("concept", "General"))[:255],
            student_answer=answer,
            score=score,
            feedback=str(evaluation.get("feedback", "")),
            difficulty=int(question.get("difficulty", 1)),
        )
        db.session.add(attempt)
        persistent_mastery = db.session.scalar(
            db.select(ConceptMastery).where(
                ConceptMastery.user_id == current_user.id,
                ConceptMastery.subject == lesson_record.subject,
                ConceptMastery.concept == attempt.concept,
            )
        )
        if not persistent_mastery:
            persistent_mastery = ConceptMastery(
                user_id=current_user.id, subject=lesson_record.subject, concept=attempt.concept,
                attempts=0, total_score=0, mastery_score=0)
            db.session.add(persistent_mastery)
        persistent_mastery.attempts += 1
        persistent_mastery.total_score += score
        persistent_mastery.mastery_score = persistent_mastery.total_score / persistent_mastery.attempts
        persistent_mastery.updated_at = utcnow()
        db.session.commit()
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
        save_session_state(payload.get("session_id"))
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
@login_required
def tutor_chat():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
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
        "explanation": session["lesson"]["explanation"][:2500],
        "concepts": [item.get("name", "") for item in session["lesson"]["concepts"]],
        "teacher_tips": session["lesson"].get("teacher_tips", [])[:2],
        "exceptions": session["lesson"].get("exceptions", [])[:2],
        "mastery": mastery_snapshot(session),
        "current_question": session["current_question"],
        "recent_results": session["history"][-3:],
        "recent_chat": session["chat_history"][-4:],
        "student_message": message,
        "response_language": session["language"],
    }
    prompt = f"""Tutor the student using this lesson state:
{json.dumps(chat_context, ensure_ascii=False)}

Reply only in response_language in at most 120 words. Be accurate and use small, explicit steps.
Use the saved context and weakest concept. For question help, give one useful hint, not the final answer.
Verify calculations; allow valid interpretations in humanities/languages. Mention an exception only when relevant.
Treat a short reply as an answer to the latest chat question. End with one short checking question."""

    try:
        response = create_response(
            model=FAST_MODEL,
            instructions="You are a careful, friendly Socratic tutor across school subjects. Teach complex ideas in respectful baby steps without removing their real difficulty. Factual accuracy and internal consistency are mandatory.",
            input=prompt,
            max_output_tokens=CHAT_TOKEN_LIMIT,
            temperature=0.2,
        )
        log_usage(response, "chat")
        reply = response.output_text.strip()
        session["chat_history"].extend([
            {"role": "student", "content": message},
            {"role": "tutor", "content": reply},
        ])
        lesson_record = db.session.scalar(db.select(Lesson).where(
            Lesson.session_id == payload.get("session_id"), Lesson.user_id == current_user.id))
        if lesson_record:
            db.session.add_all([
                ChatMessage(lesson_id=lesson_record.id, role="student", content=message),
                ChatMessage(lesson_id=lesson_record.id, role="tutor", content=reply),
            ])
            save_session_state(payload.get("session_id"), commit=False)
            db.session.commit()
        return jsonify(reply=reply, mastery=mastery_snapshot(session))
    except Exception:
        app.logger.exception("Tutor chat failed")
        return jsonify(error="The tutor is temporarily unavailable."), 500


@app.post("/api/translate")
@login_required
def translate_content():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
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
        response = create_response(
            model=FAST_MODEL,
            instructions="You are a precise educational translator. Return valid JSON only.",
            input=prompt,
            max_output_tokens=TRANSLATE_TOKEN_LIMIT,
            temperature=0,
        )
        log_usage(response, "translation")
        result = parse_json(response.output_text)
        translations = result["translations"]
        if not isinstance(translations, list) or len(translations) != len(cleaned):
            raise ValueError("Translation count mismatch")
        session["language"] = language
        save_session_state(payload.get("session_id"))
        return jsonify(translations=translations)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        return jsonify(error=f"The content could not be translated reliably: {error}"), 422
    except Exception:
        app.logger.exception("Content translation failed")
        return jsonify(error="The translation service is temporarily unavailable."), 500


if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", "5000")))

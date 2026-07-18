import base64
import hashlib
import io
import json
import os
import re
import secrets
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, send_file, session as flask_session, url_for
from flask_login import LoginManager, UserMixin, current_user, login_required, login_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from openai import OpenAI
from pypdf import PdfReader
from sqlalchemy import UniqueConstraint, func, inspect, or_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from adaptive_learning import (
    difficulty_label,
    estimated_question_count,
    mastery_status,
    prioritize_concepts,
    review_is_due,
    update_mastery,
)
from document_processing import (
    crop_image_region,
    normalize_recognition,
    preprocess_document_image,
    render_pdf_page,
    recognition_instructions,
    validate_document_upload,
)
from study_projects import (
    ALLOWED_QUESTION_TYPES,
    clean_extracted_pages,
    deterministic_question_score,
    difficulty_distribution,
    normalize_section,
    preparation_plan,
    proportional_section_counts,
)
from i18n import SUPPORTED_LANGUAGES, frontend_catalog, translate


load_dotenv()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 40 * 1024 * 1024
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY") or secrets.token_urlsafe(32)
legacy_database = os.path.join(app.instance_path, "numeri.db")
branded_database = os.path.join(app.instance_path, "learnova.db")
default_database_url = "sqlite:///numeri.db" if (
    os.path.exists(legacy_database) and not os.path.exists(branded_database)
) else "sqlite:///learnova.db"
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", default_database_url)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"  # pyright: ignore[reportAttributeAccessIssue]
login_manager.login_message = ""
login_manager.session_protection = "strong"

VISION_MODEL = os.getenv(
    "GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
TUTOR_MODEL = os.getenv("GROQ_TUTOR_MODEL", "openai/gpt-oss-20b")
FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
LESSON_TOKEN_LIMIT = int(os.getenv("LESSON_TOKEN_LIMIT", "1800"))
ANSWER_TOKEN_LIMIT = int(os.getenv("ANSWER_TOKEN_LIMIT", "1100"))
CHAT_TOKEN_LIMIT = int(os.getenv("CHAT_TOKEN_LIMIT", "350"))
TRANSLATE_TOKEN_LIMIT = int(os.getenv("TRANSLATE_TOKEN_LIMIT", "2500"))
PROJECT_TOKEN_LIMIT = int(os.getenv("PROJECT_TOKEN_LIMIT", "5000"))
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
SESSIONS = {}


def utcnow():
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """Normalize database datetimes so SQLite and Postgres compare consistently."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    preferred_language = db.Column(db.String(2), nullable=False, default="en", index=True)
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
    section_id = db.Column(db.Integer, db.ForeignKey("learning_section.id"), nullable=True, index=True)
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
    subject = db.Column(db.String(80), nullable=True, index=True)
    concept = db.Column(db.String(255), nullable=False, index=True)
    student_answer = db.Column(db.Text, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    feedback = db.Column(db.Text, nullable=False)
    difficulty = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    understood_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    hints_used = db.Column(db.Boolean, nullable=False, default=False)
    mastery_before = db.Column(db.Float, nullable=True)
    mastery_after = db.Column(db.Float, nullable=True)
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
    correct_attempts = db.Column(db.Integer, nullable=False, default=0)
    incorrect_attempts = db.Column(db.Integer, nullable=False, default=0)
    consecutive_correct = db.Column(db.Integer, nullable=False, default=0)
    consecutive_incorrect = db.Column(db.Integer, nullable=False, default=0)
    last_practised_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    next_review_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    difficulty_level = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="weak", index=True)
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


class LearningProject(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(80), nullable=False, index=True)
    exam_date = db.Column(db.Date, nullable=True)
    status = db.Column(db.String(30), nullable=False, default="uploaded", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    files = db.relationship("ProjectFile", back_populates="project", cascade="all, delete-orphan")
    pages = db.relationship("ProjectPage", back_populates="project", cascade="all, delete-orphan")
    sections = db.relationship("LearningSection", back_populates="project", cascade="all, delete-orphan")
    exams = db.relationship("FinalExam", back_populates="project", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class ProjectFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("learning_project.id"), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(100), nullable=False)
    original_data = db.Column(db.LargeBinary, nullable=False)
    source_kind = db.Column(db.String(20), nullable=False, default="upload", index=True)
    sha256 = db.Column(db.String(64), nullable=False, default="", index=True)
    uploaded_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    project = db.relationship("LearningProject", back_populates="files")
    pages = db.relationship("ProjectPage", back_populates="source_file", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class ProjectPage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("learning_project.id"), nullable=False, index=True)
    file_id = db.Column(db.Integer, db.ForeignKey("project_file.id"), nullable=False, index=True)
    page_number = db.Column(db.Integer, nullable=False, default=1)
    page_order = db.Column(db.Integer, nullable=False, index=True)
    extracted_text = db.Column(db.Text, nullable=False, default="")
    processed_data = db.Column(db.LargeBinary, nullable=True)
    processed_mime_type = db.Column(db.String(100), nullable=False, default="")
    recognition_json = db.Column(db.Text, nullable=False, default="{}")
    recognition_confidence = db.Column(db.Float, nullable=True)
    confidence_status = db.Column(db.String(30), nullable=False, default="unclear", index=True)
    detected_page_number = db.Column(db.String(40), nullable=False, default="")
    review_status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    important = db.Column(db.Boolean, nullable=False, default=False)
    teacher_highlighted = db.Column(db.Boolean, nullable=False, default=False)
    excluded = db.Column(db.Boolean, nullable=False, default=False, index=True)
    rotation = db.Column(db.Integer, nullable=False, default=0)
    image_width = db.Column(db.Integer, nullable=True)
    image_height = db.Column(db.Integer, nullable=True)
    processing_stage = db.Column(db.String(40), nullable=False, default="uploaded", index=True)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    extraction_status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    warning = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    project = db.relationship("LearningProject", back_populates="pages")
    source_file = db.relationship("ProjectFile", back_populates="pages")
    blocks = db.relationship("DocumentBlock", back_populates="page", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class DocumentBlock(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey("project_page.id"), nullable=False, index=True)
    block_order = db.Column(db.Integer, nullable=False)
    block_type = db.Column(db.String(30), nullable=False, index=True)
    content = db.Column(db.Text, nullable=False, default="")
    bbox_json = db.Column(db.Text, nullable=False, default="[]")
    confidence = db.Column(db.Float, nullable=False, default=0)
    confidence_status = db.Column(db.String(30), nullable=False, default="unclear", index=True)
    source_json = db.Column(db.Text, nullable=False, default="{}")
    review_status = db.Column(db.String(30), nullable=False, default="pending", index=True)
    important = db.Column(db.Boolean, nullable=False, default=False)
    teacher_highlighted = db.Column(db.Boolean, nullable=False, default=False)
    crossed_out = db.Column(db.Boolean, nullable=False, default=False)
    page = db.relationship("ProjectPage", back_populates="blocks")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class LearningSection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("learning_project.id"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False, index=True)
    title = db.Column(db.String(255), nullable=False)
    main_topic = db.Column(db.String(255), nullable=False, default="")
    learning_goals_json = db.Column(db.Text, nullable=False, default="[]")
    important_facts_json = db.Column(db.Text, nullable=False, default="[]")
    definitions_json = db.Column(db.Text, nullable=False, default="[]")
    formulas_json = db.Column(db.Text, nullable=False, default="[]")
    examples_json = db.Column(db.Text, nullable=False, default="[]")
    vocabulary_json = db.Column(db.Text, nullable=False, default="[]")
    relationships_json = db.Column(db.Text, nullable=False, default="[]")
    likely_questions_json = db.Column(db.Text, nullable=False, default="[]")
    source_page_ids_json = db.Column(db.Text, nullable=False, default="[]")
    simple_explanation = db.Column(db.Text, nullable=False, default="")
    standard_explanation = db.Column(db.Text, nullable=False, default="")
    detailed_explanation = db.Column(db.Text, nullable=False, default="")
    estimated_minutes = db.Column(db.Integer, nullable=False, default=10)
    mastery_score = db.Column(db.Float, nullable=False, default=0)
    status = db.Column(db.String(30), nullable=False, default="not_started", index=True)
    excluded = db.Column(db.Boolean, nullable=False, default=False)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    project = db.relationship("LearningProject", back_populates="sections")
    recall_cards = db.relationship("RecallCard", back_populates="section", cascade="all, delete-orphan")
    lessons = db.relationship("Lesson")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class RecallCard(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("learning_section.id"), nullable=False, index=True)
    kind = db.Column(db.String(40), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    source_text = db.Column(db.Text, nullable=False, default="")
    attempts = db.Column(db.Integer, nullable=False, default=0)
    correct_attempts = db.Column(db.Integer, nullable=False, default=0)
    section = db.relationship("LearningSection", back_populates="recall_cards")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class FinalExam(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("learning_project.id"), nullable=False, index=True)
    question_count = db.Column(db.Integer, nullable=False)
    duration_minutes = db.Column(db.Integer, nullable=False)
    difficulty_mode = db.Column(db.String(20), nullable=False)
    included_section_ids_json = db.Column(db.Text, nullable=False)
    question_types_json = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="in_progress", index=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    submitted_at = db.Column(db.DateTime(timezone=True), nullable=True)
    score = db.Column(db.Float, nullable=True)
    result_json = db.Column(db.Text, nullable=False, default="{}")
    project = db.relationship("LearningProject", back_populates="exams")
    questions = db.relationship("ExamQuestion", back_populates="exam", cascade="all, delete-orphan")
    answers = db.relationship("ExamAnswer", back_populates="exam", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class ExamQuestion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("final_exam.id"), nullable=False, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey("learning_section.id"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    difficulty = db.Column(db.String(20), nullable=False, index=True)
    question_type = db.Column(db.String(40), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    options_json = db.Column(db.Text, nullable=False, default="[]")
    expected_answer = db.Column(db.Text, nullable=False)
    explanation = db.Column(db.Text, nullable=False, default="")
    source_page_ids_json = db.Column(db.Text, nullable=False, default="[]")
    supporting_text = db.Column(db.Text, nullable=False)
    exam = db.relationship("FinalExam", back_populates="questions")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class ExamAnswer(db.Model):
    __table_args__ = (UniqueConstraint("exam_id", "question_id", name="uq_exam_question_answer"),)
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("final_exam.id"), nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey("exam_question.id"), nullable=False, index=True)
    answer_text = db.Column(db.Text, nullable=False, default="")
    score = db.Column(db.Float, nullable=True)
    evaluation = db.Column(db.Text, nullable=False, default="")
    saved_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    exam = db.relationship("FinalExam", back_populates="answers")
    question = db.relationship("ExamQuestion")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class SchemaMigration(db.Model):
    version = db.Column(db.String(100), primary_key=True)
    applied_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


@login_manager.user_loader
def load_user(user_id):
    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None


@app.cli.command("init-db")
def init_db_command():
    """Create the application database tables."""
    ensure_database()
    print("Initialized the database and applied pending schema migrations.")


def apply_schema_migration(version, operation):
    if db.session.get(SchemaMigration, version):
        return
    operation()
    db.session.add(SchemaMigration(version=version))
    db.session.commit()


def ensure_database():
    """Create tables and apply idempotent upgrades for existing SQLite/Postgres data."""
    with app.app_context():
        db.create_all()
        columns = {column["name"] for column in inspect(db.engine).get_columns("user")}
        if "username" not in columns:
            def add_username():
                with db.engine.begin() as connection:
                    connection.execute(text('ALTER TABLE "user" ADD COLUMN username VARCHAR(30)'))
                    connection.execute(text(
                        "UPDATE \"user\" SET username = 'user_' || CAST(id AS VARCHAR) "
                        "WHERE username IS NULL OR username = ''"
                    ))
                    connection.execute(text(
                        'CREATE UNIQUE INDEX IF NOT EXISTS uq_user_username_idx ON "user" (username)'
                    ))
            apply_schema_migration("001_add_username", add_username)
        columns = {column["name"] for column in inspect(db.engine).get_columns("user")}
        if "preferred_language" not in columns:
            def add_preferred_language():
                with db.engine.begin() as connection:
                    connection.execute(text(
                        'ALTER TABLE "user" ADD COLUMN preferred_language VARCHAR(2) '
                        "NOT NULL DEFAULT 'en'"
                    ))
                    connection.execute(text(
                        "UPDATE \"user\" SET preferred_language = 'en' "
                        "WHERE preferred_language IS NULL OR preferred_language NOT IN ('en', 'de')"
                    ))
                    connection.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_user_preferred_language '
                        'ON "user" (preferred_language)'
                    ))
            apply_schema_migration("008_add_user_preferred_language", add_preferred_language)
        attempt_columns = {column["name"] for column in inspect(db.engine).get_columns("attempt")}
        if "understood_at" not in attempt_columns:
            def add_understood_at():
                with db.engine.begin() as connection:
                    connection.execute(text('ALTER TABLE attempt ADD COLUMN understood_at TIMESTAMP'))
                    connection.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_attempt_understood_at ON attempt (understood_at)'
                    ))
            apply_schema_migration("002_add_understood_at", add_understood_at)
        attempt_columns = {column["name"] for column in inspect(db.engine).get_columns("attempt")}
        adaptive_attempt_columns = {
            "hints_used": "BOOLEAN NOT NULL DEFAULT FALSE",
            "mastery_before": "FLOAT",
            "mastery_after": "FLOAT",
        }
        missing_attempt_columns = {
            name: definition for name, definition in adaptive_attempt_columns.items()
            if name not in attempt_columns
        }
        if missing_attempt_columns:
            def add_adaptive_attempt_fields():
                with db.engine.begin() as connection:
                    for name, definition in missing_attempt_columns.items():
                        connection.execute(text(f"ALTER TABLE attempt ADD COLUMN {name} {definition}"))
            apply_schema_migration("003_add_adaptive_attempt_fields", add_adaptive_attempt_fields)
        attempt_columns = {column["name"] for column in inspect(db.engine).get_columns("attempt")}
        if "subject" not in attempt_columns:
            def add_attempt_subject():
                with db.engine.begin() as connection:
                    connection.execute(text('ALTER TABLE attempt ADD COLUMN subject VARCHAR(80)'))
                    connection.execute(text(
                        "UPDATE attempt SET subject = ("
                        "SELECT lesson.subject FROM lesson WHERE lesson.id = attempt.lesson_id)"
                    ))
                    connection.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_attempt_subject ON attempt (subject)'
                    ))
            apply_schema_migration("004_add_attempt_subject", add_attempt_subject)
        mastery_columns = {
            column["name"] for column in inspect(db.engine).get_columns("concept_mastery")
        }
        adaptive_mastery_columns = {
            "correct_attempts": "INTEGER NOT NULL DEFAULT 0",
            "incorrect_attempts": "INTEGER NOT NULL DEFAULT 0",
            "consecutive_correct": "INTEGER NOT NULL DEFAULT 0",
            "consecutive_incorrect": "INTEGER NOT NULL DEFAULT 0",
            "last_practised_at": "TIMESTAMP",
            "next_review_at": "TIMESTAMP",
            "difficulty_level": "INTEGER NOT NULL DEFAULT 1",
            "status": "VARCHAR(20) NOT NULL DEFAULT 'weak'",
        }
        missing_mastery_columns = {
            name: definition for name, definition in adaptive_mastery_columns.items()
            if name not in mastery_columns
        }
        if missing_mastery_columns:
            def add_adaptive_mastery_fields():
                with db.engine.begin() as connection:
                    for name, definition in missing_mastery_columns.items():
                        connection.execute(text(
                            f"ALTER TABLE concept_mastery ADD COLUMN {name} {definition}"
                        ))
                    connection.execute(text(
                        "UPDATE concept_mastery SET "
                        "last_practised_at = updated_at, next_review_at = updated_at, "
                        "difficulty_level = CASE WHEN mastery_score < 40 THEN 1 ELSE 2 END, "
                        "status = CASE WHEN mastery_score < 30 THEN 'weak' "
                        "WHEN mastery_score < 70 THEN 'learning' "
                        "WHEN mastery_score < 85 THEN 'strong' ELSE 'mastered' END"
                    ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_concept_mastery_next_review_at "
                        "ON concept_mastery (next_review_at)"
                    ))
            apply_schema_migration("005_add_adaptive_mastery_fields", add_adaptive_mastery_fields)
        lesson_columns = {column["name"] for column in inspect(db.engine).get_columns("lesson")}
        if "section_id" not in lesson_columns:
            def add_lesson_section():
                with db.engine.begin() as connection:
                    connection.execute(text('ALTER TABLE lesson ADD COLUMN section_id INTEGER'))
                    connection.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_lesson_section_id ON lesson (section_id)'
                    ))
            apply_schema_migration("006_link_lessons_to_sections", add_lesson_section)
        project_file_columns = {
            column["name"] for column in inspect(db.engine).get_columns("project_file")
        }
        page_columns = {
            column["name"] for column in inspect(db.engine).get_columns("project_page")
        }
        document_file_fields = {
            "source_kind": "VARCHAR(20) NOT NULL DEFAULT 'upload'",
            "sha256": "VARCHAR(64) NOT NULL DEFAULT ''",
        }
        binary_type = "BYTEA" if db.engine.dialect.name == "postgresql" else "BLOB"
        document_page_fields = {
            "processed_data": binary_type,
            "processed_mime_type": "VARCHAR(100) NOT NULL DEFAULT ''",
            "recognition_json": "TEXT NOT NULL DEFAULT '{}'",
            "recognition_confidence": "FLOAT",
            "confidence_status": "VARCHAR(30) NOT NULL DEFAULT 'unclear'",
            "detected_page_number": "VARCHAR(40) NOT NULL DEFAULT ''",
            "review_status": "VARCHAR(30) NOT NULL DEFAULT 'pending'",
            "important": "BOOLEAN NOT NULL DEFAULT FALSE",
            "teacher_highlighted": "BOOLEAN NOT NULL DEFAULT FALSE",
            "excluded": "BOOLEAN NOT NULL DEFAULT FALSE",
            "rotation": "INTEGER NOT NULL DEFAULT 0",
            "image_width": "INTEGER",
            "image_height": "INTEGER",
            "processing_stage": "VARCHAR(40) NOT NULL DEFAULT 'uploaded'",
            "retry_count": "INTEGER NOT NULL DEFAULT 0",
        }
        missing_file_fields = {
            name: definition for name, definition in document_file_fields.items()
            if name not in project_file_columns
        }
        missing_page_fields = {
            name: definition for name, definition in document_page_fields.items()
            if name not in page_columns
        }
        if missing_file_fields or missing_page_fields:
            def add_document_recognition_fields():
                with db.engine.begin() as connection:
                    for name, definition in missing_file_fields.items():
                        connection.execute(text(
                            f"ALTER TABLE project_file ADD COLUMN {name} {definition}"
                        ))
                    for name, definition in missing_page_fields.items():
                        connection.execute(text(
                            f"ALTER TABLE project_page ADD COLUMN {name} {definition}"
                        ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_project_page_review_status "
                        "ON project_page (review_status)"
                    ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_project_page_processing_stage "
                        "ON project_page (processing_stage)"
                    ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_project_page_confidence_status "
                        "ON project_page (confidence_status)"
                    ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_project_page_excluded ON project_page (excluded)"
                    ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_project_file_source_kind ON project_file (source_kind)"
                    ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_project_file_sha256 ON project_file (sha256)"
                    ))
            apply_schema_migration("007_add_document_recognition", add_document_recognition_fields)


ensure_database()


def get_current_language() -> str:
    """Resolve the single active interface language for the current request."""
    if current_user.is_authenticated:
        preferred = str(getattr(current_user, "preferred_language", "en") or "en")
        language = preferred if preferred in SUPPORTED_LANGUAGES else "en"
        flask_session["language"] = language
        return language
    saved = flask_session.get("language")
    if saved in SUPPORTED_LANGUAGES:
        return saved
    browser = request.accept_languages.best_match(SUPPORTED_LANGUAGES) or "en"
    flask_session["language"] = browser
    return browser


def learning_content_language() -> str:
    return "German" if get_current_language() == "de" else "English"


def language_instruction() -> str:
    return (
        "Antworte vollständig auf Deutsch."
        if get_current_language() == "de"
        else "Respond entirely in English."
    )


def tutor_instructions() -> str:
    return f"{TUTOR_RULES}\n\n{language_instruction()}"


def tr(message: str, **values) -> str:
    return translate(message, get_current_language(), **values)


def safe_internal_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    return value


@app.context_processor
def inject_i18n():
    language = get_current_language()
    return {
        "_": lambda message, **values: translate(message, language, **values),
        "current_language": language,
        "frontend_translations": frontend_catalog(language),
    }


@login_manager.unauthorized_handler
def unauthorized():
    flash(tr("Please log in to use your tutor."), "error")
    return redirect(url_for("login", next=request.path))


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(self), microphone=()")
    return response


@app.errorhandler(413)
def request_too_large(_error):
    message = tr("The upload is too large. Keep the complete request under 40 MB.")
    if request.path.startswith("/api/"):
        return jsonify(error=message), 413
    flash(message, "error")
    return redirect(request.referrer or url_for("index"))


@app.errorhandler(SQLAlchemyError)
def database_error(_error):
    db.session.rollback()
    app.logger.exception("Database operation failed")
    if request.path.startswith("/api/"):
        return jsonify(error=tr("The database is temporarily unavailable.")), 503
    flash(tr("The database is temporarily unavailable."), "error")
    return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))


TUTOR_RULES = """You are a patient expert tutor for students of any age and level.
Teach only the selected subject, study goal, and concepts supported by the student's material.
Use the student's apparent level and explain in respectful baby steps without childish language.
Define unfamiliar terms, show how each step connects, identify common mistakes, give practical teacher tips, and mention relevant exceptions or disputed interpretations.
Adapt your teaching method to the subject: use worked calculations for mathematics and science, examples and corrections for languages, chronology and cause/effect for history, and evidence-based explanations for other subjects.
For mathematics and physics, never skip transformations or combine multiple operations into one unexplained jump.
Use plain Unicode notation instead of LaTeX: ×, ÷, √, ², ³, π, Δ, ≤, ≥, parentheses, and readable units.
Put each calculation transformation on its own line. Name the rule or operation first, show the changed expression next, and explain why it is valid.
Follow the order of operations explicitly. For example, explain 5 + 4 − 6 × 3 as:
Step 1 — Multiply first
6 × 3 = 18
5 + 4 − 18
Step 2 — Add 5 and 4
5 + 4 = 9
9 − 18
Step 3 — Subtract
9 − 18 = −9
Distinguish subtraction from multiplication of signed numbers; never use misleading sign shortcuts.
For formulas, define every variable and unit before substitution, show the substituted formula, include units on intermediate values, and finish with a clearly labelled final answer.
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
    if not session and session_id and current_user.is_authenticated:
        lesson = db.session.scalar(db.select(Lesson).where(
            Lesson.session_id == session_id, Lesson.user_id == current_user.id))
        if lesson and lesson.study_session:
            try:
                session = json.loads(lesson.study_session.state_json)
                session["user_id"] = current_user.id
                SESSIONS[session_id] = session
            except (json.JSONDecodeError, TypeError):
                session = None
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


def mastery_state(record):
    return {
        "id": record.id,
        "subject": record.subject,
        "concept": record.concept,
        "mastery_score": record.mastery_score,
        "attempts": record.attempts,
        "correct_attempts": record.correct_attempts,
        "incorrect_attempts": record.incorrect_attempts,
        "consecutive_correct": record.consecutive_correct,
        "consecutive_incorrect": record.consecutive_incorrect,
        "last_practised_at": record.last_practised_at,
        "next_review_at": record.next_review_at,
        "difficulty_level": record.difficulty_level,
        "status": record.status,
    }


def get_or_create_mastery(user_id, subject, concept):
    record = db.session.scalar(db.select(ConceptMastery).where(
        ConceptMastery.user_id == user_id,
        ConceptMastery.subject == subject,
        ConceptMastery.concept == concept,
    ))
    if not record:
        record = ConceptMastery(
            user_id=user_id,
            subject=subject,
            concept=concept,
            mastery_score=0,
            attempts=0,
            total_score=0,
            correct_attempts=0,
            incorrect_attempts=0,
            consecutive_correct=0,
            consecutive_incorrect=0,
            difficulty_level=1,
            status="weak",
        )
        db.session.add(record)
        db.session.flush()
    return record


def apply_mastery_update(record, score, hints_used=False, practised_at=None):
    before = float(record.mastery_score or 0)
    updated = update_mastery(
        mastery_state(record), score, hints_used=hints_used, practised_at=practised_at
    )
    for field in (
        "mastery_score", "attempts", "correct_attempts", "incorrect_attempts",
        "consecutive_correct", "consecutive_incorrect", "last_practised_at",
        "next_review_at", "difficulty_level", "status",
    ):
        setattr(record, field, updated[field])
    record.total_score = int(record.total_score or 0) + int(score)
    record.updated_at = updated["last_practised_at"]
    return before, updated


def user_mastery_plan(user_id, question_count=None, now=None):
    records = db.session.scalars(
        db.select(ConceptMastery).where(ConceptMastery.user_id == user_id)
    ).all()
    states = [mastery_state(record) for record in records]
    return prioritize_concepts(states, question_count=question_count, now=now)


def recent_concept_questions(user_id, subject, concept, limit=5):
    return db.session.scalars(
        db.select(Attempt.question).join(Lesson).where(
            Lesson.user_id == user_id,
            func.coalesce(Attempt.subject, Lesson.subject) == subject,
            Attempt.concept == concept,
        ).order_by(Attempt.timestamp.desc()).limit(limit)
    ).all()


def json_value(value, fallback=None):
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return [] if fallback is None else fallback


def json_object(value):
    parsed = json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


app.jinja_env.globals.update(json_value=json_value)


def owned_project(project_id):
    return db.session.scalar(db.select(LearningProject).where(
        LearningProject.id == project_id, LearningProject.user_id == current_user.id
    ))


def owned_project_page(project_id, page_id):
    return db.session.scalar(db.select(ProjectPage).join(LearningProject).where(
        ProjectPage.id == page_id,
        ProjectPage.project_id == project_id,
        LearningProject.user_id == current_user.id,
    ))


def private_binary_response(data, mime_type, download_name=None):
    response = send_file(io.BytesIO(data), mimetype=mime_type, download_name=download_name)
    response.cache_control.private = True
    response.cache_control.no_store = True
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def store_page_recognition(page, recognized):
    for block in list(page.blocks):
        db.session.delete(block)
    db.session.flush()
    page.extracted_text = recognized["text"]
    page.recognition_json = json.dumps(recognized, ensure_ascii=False)
    page.recognition_confidence = recognized["confidence"]
    page.confidence_status = recognized["confidence_status"]
    page.detected_page_number = recognized["detected_page_number"]
    page.extraction_status = "ready" if recognized["readable"] else "unreadable"
    page.processing_stage = "ready_for_review" if recognized["readable"] else "failed"
    page.review_status = "pending"
    recognition_warning = recognized.get("warning", "")
    page.warning = " ".join(value for value in [page.warning, recognition_warning] if value).strip()
    for item in recognized["blocks"]:
        source = {
            "project_id": page.project_id,
            "file_id": page.file_id,
            "page_id": page.id,
            "page_number": page.page_order,
            "bbox": item["bbox"],
            "source_kind": page.source_file.source_kind,
            "nearby_text": item["nearby_text"],
        }
        db.session.add(DocumentBlock(
            page_id=page.id, block_order=item["order"], block_type=item["type"],
            content=item["content"], bbox_json=json.dumps(item["bbox"]),
            confidence=item["confidence"], confidence_status=item["confidence_status"],
            source_json=json.dumps(source),
            important=item["important_candidate"],
            teacher_highlighted=item["teacher_highlight_candidate"],
            crossed_out=item["crossed_out"],
        ))


def ensure_processed_page_image(page):
    if page.processed_data and page.processed_mime_type:
        return
    source = page.source_file.original_data
    if page.source_file.mime_type == "application/pdf":
        source = render_pdf_page(source, page.page_number - 1)
    processed = preprocess_document_image(source)
    page.processed_data = processed.data
    page.processed_mime_type = processed.mime_type
    page.image_width, page.image_height = processed.width, processed.height
    page.processing_stage = "improved"
    page.warning = " ".join(value for value in [page.warning, *processed.warnings] if value).strip()


def sync_page_recognition_json(page):
    saved = json_object(page.recognition_json)
    saved.update({
        "text": page.extracted_text,
        "confidence": page.recognition_confidence,
        "confidence_status": page.confidence_status,
        "review_status": page.review_status,
        "important": page.important,
        "teacher_highlighted": page.teacher_highlighted,
        "excluded": page.excluded,
        "blocks": [{
            "id": block.id,
            "order": block.block_order,
            "type": block.block_type,
            "content": block.content,
            "bbox": json_value(block.bbox_json),
            "confidence": block.confidence,
            "confidence_status": block.confidence_status,
            "source": json_value(block.source_json, {}),
            "review_status": block.review_status,
            "important": block.important,
            "teacher_highlighted": block.teacher_highlighted,
            "crossed_out": block.crossed_out,
        } for block in sorted(page.blocks, key=lambda item: item.block_order)],
    })
    page.recognition_json = json.dumps(saved, ensure_ascii=False)


def recognize_single_project_page(page, project):
    page_id = page.id
    try:
        ensure_processed_page_image(page)
        page.processing_stage = "recognizing"
        db.session.commit()
        response = create_response(
            model=VISION_MODEL,
            instructions=(
                "You are a conservative school-document recognition system. Never guess missing words, "
                "symbols, labels, or handwriting. Return structured JSON only."
            ),
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": recognition_instructions(project.subject, page.page_order)},
                {"type": "input_image", "image_url": (
                    f"data:{page.processed_mime_type};base64,"
                    f"{base64.b64encode(page.processed_data).decode('ascii')}"
                ), "detail": "high"},
            ]}],
            max_output_tokens=PROJECT_TOKEN_LIMIT,
            temperature=0,
        )
        log_usage(response, "document-recognition")
        recognized = normalize_recognition(parse_json(response.output_text))
        if not recognized["readable"]:
            raise ValueError("No reliable printed or handwritten content was recognized")
        store_page_recognition(page, recognized)
        project.status = "reviewing"
        db.session.commit()
        return True, ""
    except Exception as error:
        db.session.rollback()
        saved_page = db.session.get(ProjectPage, page_id)
        if saved_page:
            saved_page.extraction_status = "failed"
            saved_page.processing_stage = "failed"
            saved_page.review_status = "pending"
            saved_page.retry_count += 1
            saved_page.warning = f"Recognition failed: {error}"
            saved_project = db.session.get(LearningProject, saved_page.project_id)
            if saved_project:
                saved_project.status = "reviewing"
            db.session.commit()
        app.logger.exception("Recognition failed for project %s page %s", project.id, page_id)
        return False, str(error)


def owned_section(project_id, section_id):
    return db.session.scalar(
        db.select(LearningSection).join(LearningProject).where(
            LearningSection.id == section_id,
            LearningSection.project_id == project_id,
            LearningProject.user_id == current_user.id,
        )
    )


def owned_exam(exam_id):
    return db.session.scalar(
        db.select(FinalExam).join(LearningProject).where(
            FinalExam.id == exam_id, LearningProject.user_id == current_user.id
        )
    )


def section_source_text(section):
    page_ids = json_value(section.source_page_ids_json)
    pages = db.session.scalars(
        db.select(ProjectPage).where(
            ProjectPage.project_id == section.project_id,
            ProjectPage.id.in_(page_ids),
        ).order_by(ProjectPage.page_order)
    ).all() if page_ids else []
    return "\n\n".join(
        f"[Page {page.page_order}: {page.source_file.original_filename}]\n{page.extracted_text}"
        for page in pages
    )


def source_text_for_pages(project_id, page_ids):
    pages = db.session.scalars(db.select(ProjectPage).where(
        ProjectPage.project_id == project_id,
        ProjectPage.id.in_(page_ids),
    ).order_by(ProjectPage.page_order)).all() if page_ids else []
    return "\n".join(page.extracted_text for page in pages)


def source_page_orders(project_id, page_ids):
    if isinstance(page_ids, str):
        page_ids = json_value(page_ids)
    return db.session.scalars(db.select(ProjectPage.page_order).where(
        ProjectPage.project_id == project_id,
        ProjectPage.id.in_(page_ids),
    ).order_by(ProjectPage.page_order)).all() if page_ids else []


app.jinja_env.globals.update(source_page_orders=source_page_orders)


def section_has_progress(section):
    return bool(
        section.lessons
        or section.mastery_score
        or section.completed_at
        or any(card.attempts for card in section.recall_cards)
    )


def section_status_from_score(score, completed=False):
    if not completed:
        return "learning" if score else "not_started"
    if score < 55:
        return "needs_review"
    if score < 80:
        return "learning"
    if score < 90:
        return "strong"
    return "exam_ready"


def update_section_mastery(section, completed=False):
    """Derive section mastery from saved attempts; never ask the AI to calculate it."""
    scores = db.session.scalars(
        db.select(Attempt.score).join(Lesson).where(
            Lesson.section_id == section.id,
            Lesson.user_id == section.project.user_id,
        )
    ).all()
    if not scores:
        return
    section.mastery_score = round(sum(scores) / len(scores), 2)
    if completed:
        section.completed_at = section.completed_at or utcnow()
    section.status = section_status_from_score(
        section.mastery_score, completed=completed or bool(section.completed_at)
    )


def generate_adaptive_question(session, target, question_number, question_type):
    recent_questions = recent_concept_questions(
        current_user.id, target["subject"], target["concept"]
    )
    previous_mistakes = db.session.execute(
        db.select(Attempt.question, Attempt.student_answer, Attempt.feedback).join(Lesson).where(
            Lesson.user_id == current_user.id,
            func.coalesce(Attempt.subject, Lesson.subject) == target["subject"],
            Attempt.concept == target["concept"],
            Attempt.score < 50,
        ).order_by(Attempt.timestamp.desc()).limit(3)
    ).all()
    context = {
        "subject": target["subject"],
        "concept": target["concept"],
        "difficulty": difficulty_label(target["difficulty_level"]),
        "mastery_score": target["mastery_score"],
        "status": target["status"],
        "previous_mistakes": [dict(row._mapping) for row in previous_mistakes],
        "recent_questions_to_avoid": recent_questions,
        "lesson_context": str(session["lesson"].get("explanation", ""))[:1800],
        "response_language": session["language"],
    }
    prompt = f"""Generate the next adaptive practice question.
Learning context: {json.dumps(context, ensure_ascii=False)}
Return JSON exactly as:
{{"question":{{"id":"q{question_number}","subject":"{target['subject']}","concept":"{target['concept']}","difficulty":{target['difficulty_level']},"type":"{question_type}","prompt":"new question","hint":"small hint","options":[{{"id":"a","label":"choice"}}],"expected_answer":"answer id, list, order, or text"}}}}
Match the requested easy/medium/hard difficulty. Do not duplicate a recent question. For multiple choice or dropdown return four options with one correct answer; for checkboxes return four or five options with two or three correct answers; for ordering return four shuffled items; for text return an empty options list."""
    question = None
    recent_normalized = {item.strip().casefold() for item in recent_questions}
    for generation_attempt in range(2):
        retry_note = "" if generation_attempt == 0 else "\nThe previous generated prompt duplicated a recent question. Use a distinctly different scenario and wording."
        response = create_response(
            model=TUTOR_MODEL,
            instructions=tutor_instructions(),
            input=prompt + retry_note,
            max_output_tokens=ANSWER_TOKEN_LIMIT,
            temperature=0.15,
            **quality_options(),
        )
        log_usage(response, "adaptive-question")
        result = parse_json(response.output_text)
        question = result.get("question") or result.get("next_question")
        if not isinstance(question, dict):
            raise KeyError("question")
        if str(question.get("prompt", "")).strip().casefold() not in recent_normalized:
            break
    else:
        raise ValueError("The generated question duplicated a recent question")
    for key in ("prompt", "hint", "expected_answer"):
        if key not in question:
            raise KeyError(f"question.{key}")
    question.update({
        "id": f"q{question_number}",
        "subject": target["subject"],
        "concept": target["concept"],
        "difficulty": target["difficulty_level"],
        "type": question_type,
    })
    question.setdefault("options", [])
    return question


def adaptive_session_results(session):
    changes = list(session.get("mastery_changes", {}).values())
    next_dates = [item.get("next_review_at") for item in changes if item.get("next_review_at")]
    improved = [item["concept"] for item in changes if item["after"] > item["before"]]
    still_weak = [item["concept"] for item in changes if item["after"] < 50]
    if still_weak:
        action = "Review the still-weak concepts in the next scheduled session."
    elif improved:
        action = "Continue with the next scheduled review to consolidate these gains."
    else:
        action = "Review the feedback, then retry the weakest concept tomorrow."
    return {
        "score": round(sum(item["score"] for item in session["history"]) / len(session["history"])),
        "concepts_practised": list(dict.fromkeys(item["concept"] for item in changes)),
        "mastery_changes": changes,
        "concepts_improved": improved,
        "concepts_still_weak": still_weak,
        "next_recommended_review_date": min(next_dates) if next_dates else None,
        "recommended_next_action": action,
    }


DASHBOARD_TEXT = {
    "en": {"progress": "YOUR PROGRESS", "title": "Learning dashboard", "new": "New lesson",
           "logout": "Log out", "practice": "Practise weak points", "subjects": "Subjects",
           "attempts": "attempts", "no_results": "No quiz results yet.", "weak_concepts": "Weakest concepts",
           "weak_empty": "Your weak points will appear after your first quiz.", "mistakes": "Recent mistakes",
           "no_mistakes": "No mistakes match these filters.", "your_answer": "Your answer", "sessions": "Saved lessons and chats",
           "mistake_notebook": "Mistake Notebook", "filters": "Filters", "all_subjects": "All subjects",
           "all_statuses": "All mastery statuses", "weak": "Weak", "learning": "Learning", "strong": "Strong",
           "mastered": "Mastered", "understood": "Understood", "try_similar": "Try similar question",
           "mark_understood": "Mark as understood", "feedback": "Feedback", "score": "Score", "date": "Date",
           "apply_filters": "Apply filters", "practice_weakest": "Practice weakest concepts",
           "due_today": "Due today", "next_review": "Next review", "mastery_trend": "Mastery trend",
           "continue_learning": "Continue learning", "todays_practice": "Today's Practice",
           "no_sessions": "No saved lessons yet.", "resume": "Resume", "view": "View history",
           "chat_messages": "chat messages", "level": "Level", "no_chat": "No chat messages yet.", "you": "You"},
    "de": {"progress": "DEIN FORTSCHRITT", "title": "Lernübersicht", "new": "Neue Lektion",
           "logout": "Abmelden", "practice": "Schwachstellen üben", "subjects": "Fächer",
           "attempts": "Versuche", "no_results": "Noch keine Testergebnisse.", "weak_concepts": "Schwächste Konzepte",
           "weak_empty": "Deine Schwachstellen erscheinen nach dem ersten Quiz.", "mistakes": "Letzte Fehler",
           "no_mistakes": "Keine Fehler entsprechen diesen Filtern.", "your_answer": "Deine Antwort", "sessions": "Gespeicherte Lektionen und Chats",
           "mistake_notebook": "Fehlernotizbuch", "filters": "Filter", "all_subjects": "Alle Fächer",
           "all_statuses": "Alle Lernstände", "weak": "Schwach", "learning": "Lernend", "strong": "Stark",
           "mastered": "Gemeistert", "understood": "Verstanden", "try_similar": "Ähnliche Frage versuchen",
           "mark_understood": "Als verstanden markieren", "feedback": "Feedback", "score": "Punktzahl", "date": "Datum",
           "apply_filters": "Filter anwenden", "practice_weakest": "Schwächste Konzepte üben",
           "due_today": "Heute fällig", "next_review": "Nächste Wiederholung", "mastery_trend": "Lerntrend",
           "continue_learning": "Weiterlernen", "todays_practice": "Heutige Übung",
           "no_sessions": "Noch keine Lektionen gespeichert.", "resume": "Fortsetzen", "view": "Verlauf ansehen",
           "chat_messages": "Chatnachrichten", "level": "Stufe", "no_chat": "Noch keine Chatnachrichten.", "you": "Du"},
}


@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip().casefold()
        email = request.form.get("email", "").strip().lower()[:255]
        password = request.form.get("password", "")
        language = request.form.get("language", get_current_language())
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,29}", username):
            flash(tr("Username must be 3–30 characters using letters, numbers, dots, hyphens, or underscores."), "error")
        elif language not in SUPPORTED_LANGUAGES:
            flash(tr("Unsupported language."), "error")
        elif not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            flash(tr("Enter a valid email address."), "error")
        elif len(password) < 8 or len(password) > 256:
            flash(tr("Use a password with 8–256 characters."), "error")
        elif db.session.scalar(db.select(User).where(User.username == username)):
            flash(tr("That username is already registered."), "error")
        elif db.session.scalar(db.select(User).where(User.email == email)):
            flash(tr("An account with that email already exists."), "error")
        else:
            try:
                user = User(username=username, email=email, preferred_language=language)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                login_user(user)
                flask_session["language"] = language
                return redirect(url_for("index"))
            except IntegrityError:
                db.session.rollback()
                flash(tr("That username or email is already registered."), "error")
            except SQLAlchemyError:
                db.session.rollback()
                app.logger.exception("Account registration failed")
                flash(tr("Your account could not be created right now."), "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        identifier = (request.form.get("identifier") or request.form.get("email", "")).strip().casefold()[:255]
        user = db.session.scalar(db.select(User).where(
            (User.email == identifier) | (User.username == identifier)
        ))
        if not user or not user.check_password(request.form.get("password", "")):
            flash(tr("Invalid username, email, or password."), "error")
        else:
            login_user(user, remember=bool(request.form.get("remember")))
            flask_session["language"] = user.preferred_language
            return redirect(safe_internal_url(request.form.get("next")) or url_for("index"))
    return render_template("auth.html", mode="login")


@app.post("/logout")
@login_required
def logout():
    language = get_current_language()
    logout_user()
    flask_session.clear()
    flask_session["language"] = language
    return redirect(url_for("login"))


@app.get("/settings")
def settings():
    return render_template(
        "settings.html",
        next_url=safe_internal_url(request.args.get("next")),
    )


@app.post("/settings/language")
def update_language():
    language = request.form.get("language", "")
    if language not in SUPPORTED_LANGUAGES:
        flash(tr("Unsupported language."), "error")
        return redirect(url_for("settings")), 400
    if current_user.is_authenticated:
        current_user.preferred_language = language
        db.session.commit()
    flask_session["language"] = language
    flash(translate("Your language preference was saved.", language), "success")
    destination = safe_internal_url(request.form.get("next"))
    return redirect(destination or url_for("settings"))


@app.get("/")
@login_required
def index():
    session_id = request.args.get("session_id", "")
    session = owned_session(session_id) if session_id else None
    bootstrap = None
    if session:
        bootstrap = {
            "session_id": session_id,
            "test_total": session["test_total"],
            "lesson": {key: value for key, value in session["lesson"].items() if key != "question"},
            "question": {key: value for key, value in session["current_question"].items() if key != "expected_answer"},
        }
    return render_template("index.html", bootstrap=bootstrap)


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.route("/projects", methods=["GET", "POST"])
@login_required
def projects():
    if request.method == "POST":
        title = request.form.get("title", "").strip()[:255]
        subject = request.form.get("subject", "").strip()[:80]
        uploads = [(item, "upload", {}) for item in request.files.getlist("materials") if item.filename]
        scan_files = [item for item in request.files.getlist("camera_scans") if item.filename]
        try:
            scan_metadata = json.loads(request.form.get("scan_metadata", "[]"))
        except json.JSONDecodeError:
            scan_metadata = []
        for index, item in enumerate(scan_files):
            metadata = scan_metadata[index] if index < len(scan_metadata) and isinstance(scan_metadata[index], dict) else {}
            uploads.append((item, "camera", metadata))
        exam_date = None
        invalid_exam_date = False
        if request.form.get("exam_date"):
            try:
                exam_date = date.fromisoformat(request.form["exam_date"])
            except ValueError:
                flash(tr("Enter a valid exam date."), "error")
                invalid_exam_date = True
        if invalid_exam_date:
            pass
        elif not title or not subject:
            flash(tr("Project title and subject are required."), "error")
        elif not uploads:
            flash(tr("Add at least one image or PDF."), "error")
        else:
            project = LearningProject(
                user_id=current_user.id, title=title, subject=subject,
                exam_date=exam_date, status="uploaded",
            )
            db.session.add(project)
            db.session.flush()
            page_order = 1
            seen_hashes = set()
            try:
                for upload, source_kind, transform in uploads:
                    data = upload.read()
                    filename = secure_filename(upload.filename or "material")[:255] or "material"
                    mime_type = validate_document_upload(data, filename, upload.mimetype)
                    digest = hashlib.sha256(data).hexdigest()
                    if digest in seen_hashes:
                        continue
                    seen_hashes.add(digest)
                    source_file = ProjectFile(
                        project_id=project.id, original_filename=filename,
                        mime_type=mime_type, original_data=data, source_kind=source_kind,
                        sha256=digest,
                    )
                    db.session.add(source_file)
                    db.session.flush()
                    if mime_type == "application/pdf":
                        try:
                            reader = PdfReader(io.BytesIO(data))
                            if page_order - 1 + len(reader.pages) > 20:
                                raise ValueError("Keep one project to 20 pages or fewer")
                            for pdf_index, pdf_page in enumerate(reader.pages, start=1):
                                extracted = (pdf_page.extract_text() or "").strip()
                                rendered = render_pdf_page(data, pdf_index - 1)
                                processed = preprocess_document_image(rendered)
                                db.session.add(ProjectPage(
                                    project_id=project.id, file_id=source_file.id,
                                    page_number=pdf_index, page_order=page_order,
                                    extracted_text=extracted,
                                    processed_data=processed.data,
                                    processed_mime_type=processed.mime_type,
                                    image_width=processed.width, image_height=processed.height,
                                    extraction_status="pending", processing_stage="improved",
                                    warning=" ".join(processed.warnings),
                                ))
                                page_order += 1
                        except Exception as error:
                            raise ValueError(f"Could not read PDF {filename}: {error}") from error
                    else:
                        if page_order > 20:
                            raise ValueError("Keep one project to 20 pages or fewer")
                        processed = preprocess_document_image(data, transform)
                        db.session.add(ProjectPage(
                            project_id=project.id, file_id=source_file.id,
                            page_number=1, page_order=page_order,
                            processed_data=processed.data,
                            processed_mime_type=processed.mime_type,
                            image_width=processed.width, image_height=processed.height,
                            rotation=int(transform.get("rotation", 0) or 0) % 360,
                            extraction_status="pending", processing_stage="improved",
                            warning=" ".join(processed.warnings),
                        ))
                        page_order += 1
                if page_order == 1:
                    raise ValueError("No unique supported pages were uploaded")
                if page_order - 1 > 20:
                    raise ValueError("Keep one project to 20 pages or fewer")
                db.session.commit()
                return redirect(url_for("review_project_recognition", project_id=project.id))
            except (ValueError, SQLAlchemyError) as error:
                db.session.rollback()
                flash(str(error), "error")
            except Exception:
                db.session.rollback()
                app.logger.exception("Project upload failed")
                flash("The upload could not be stored safely. Please check the files and try again.", "error")
    project_rows = db.session.scalars(
        db.select(LearningProject).where(LearningProject.user_id == current_user.id)
        .order_by(LearningProject.updated_at.desc())
    ).all()
    return render_template("projects.html", projects=project_rows)


@app.route("/projects/<int:project_id>/review", methods=["GET", "POST"])
@login_required
def review_project_recognition(project_id):
    project = owned_project(project_id)
    if not project:
        return "Project not found", 404
    pages = sorted(project.pages, key=lambda item: item.page_order)
    if request.method == "POST":
        action = request.form.get("action", "save")
        try:
            for page in pages:
                page.excluded = request.form.get(f"excluded_{page.id}") == "1"
                page.important = request.form.get(f"important_{page.id}") == "1"
                page.teacher_highlighted = request.form.get(f"teacher_{page.id}") == "1"
                submitted_text = request.form.get(f"text_{page.id}")
                if submitted_text is not None:
                    if len(submitted_text) > 60_000:
                        raise ValueError(f"Page {page.page_order} text exceeds 60,000 characters.")
                    page.extracted_text = submitted_text.strip()
                for block in page.blocks:
                    block_texts = request.form.getlist(f"block_{block.id}")
                    if block_texts:
                        corrected_block = block_texts[-1].strip()
                        if block.content and block.content in page.extracted_text:
                            page.extracted_text = page.extracted_text.replace(
                                block.content, corrected_block, 1
                            )
                        block.content = corrected_block
                    block.important = request.form.get(f"block_important_{block.id}") == "1"
                    block.teacher_highlighted = request.form.get(f"block_teacher_{block.id}") == "1"
            if action == "confirm":
                failed = [page for page in pages if not page.excluded and page.extraction_status != "ready"]
                empty = [page for page in pages if not page.excluded and not page.extracted_text.strip()]
                if failed or empty:
                    raise ValueError("Retry, exclude, or correct every failed/empty page before confirming.")
                for page in pages:
                    page.review_status = "confirmed" if not page.excluded else "excluded"
                    for block in page.blocks:
                        block.review_status = "confirmed"
                    sync_page_recognition_json(page)
                project.status = "confirmed"
                flash("Recognition review confirmed. Learnova can now build grounded sections.", "success")
                db.session.commit()
                return redirect(url_for("project_dashboard", project_id=project.id))
            if action == "continue_unreviewed":
                if not any(page.extracted_text.strip() for page in pages if not page.excluded):
                    raise ValueError("At least one included page needs recognized text.")
                for page in pages:
                    page.review_status = "unreviewed" if not page.excluded else "excluded"
                    sync_page_recognition_json(page)
                project.status = "confirmed"
                flash("Continuing without full review. Uncertain recognition remains visibly marked.", "error")
                db.session.commit()
                return redirect(url_for("project_dashboard", project_id=project.id))
            project.status = "reviewing"
            for page in pages:
                sync_page_recognition_json(page)
            db.session.commit()
            flash("Recognition corrections saved.", "success")
        except ValueError as error:
            db.session.rollback()
            flash(str(error), "error")
    counts = Counter(page.processing_stage for page in pages)
    recognition_page_ids = [
        page.id for page in pages if not page.excluded and (
            page.extraction_status != "ready"
            or not json_object(page.recognition_json).get("blocks")
        )
    ]
    needs_recognition = bool(recognition_page_ids)
    return render_template(
        "recognition_review.html", project=project, pages=pages,
        processing_counts=counts, needs_recognition=needs_recognition,
        recognition_page_ids=recognition_page_ids,
    )


@app.post("/projects/<int:project_id>/recognize")
@login_required
def recognize_project_pages(project_id):
    project = owned_project(project_id)
    if not project:
        return "Project not found", 404
    pages = sorted(project.pages, key=lambda item: item.page_order)
    attempted = 0
    failed = 0
    for page in pages:
        existing_recognition = json_object(page.recognition_json)
        if page.excluded or (
            page.extraction_status == "ready" and existing_recognition.get("blocks")
        ):
            continue
        attempted += 1
        success, _error = recognize_single_project_page(page, project)
        if not success:
            failed += 1
    project = owned_project(project_id)
    if project:
        project.status = "reviewing"
        db.session.commit()
    if not attempted:
        flash("All included pages already have recognition results.", "success")
    elif failed:
        flash(f"{attempted - failed} page(s) recognized; {failed} need retry or correction.", "error")
    else:
        flash(f"Recognized {attempted} page(s). Review uncertain content before continuing.", "success")
    return redirect(url_for("review_project_recognition", project_id=project_id))


@app.post("/projects/<int:project_id>/pages/<int:page_id>/recognize")
@login_required
def recognize_one_project_page(project_id, page_id):
    project = owned_project(project_id)
    page = owned_project_page(project_id, page_id) if project else None
    if not project or not page:
        return jsonify(error="Page not found"), 404
    if page.excluded:
        return jsonify(error="Excluded pages are not recognized"), 400
    success, error = recognize_single_project_page(page, project)
    if not success:
        return jsonify(status="failed", error=error, page_id=page_id), 422
    return jsonify(
        status="ready_for_review", page_id=page_id,
        confidence=page.recognition_confidence, confidence_status=page.confidence_status,
    )


@app.post("/projects/<int:project_id>/pages/<int:page_id>/retry")
@login_required
def retry_project_page(project_id, page_id):
    project = owned_project(project_id)
    page = owned_project_page(project_id, page_id)
    if not project or not page:
        return "Page not found", 404
    page.extraction_status = "pending"
    page.processing_stage = "improved"
    page.warning = ""
    db.session.commit()
    success, error = recognize_single_project_page(page, project)
    flash(
        f"Page {page.page_order} is ready for review." if success
        else f"Page {page.page_order} still could not be recognized: {error}",
        "success" if success else "error",
    )
    return redirect(url_for("review_project_recognition", project_id=project_id))


@app.post("/projects/<int:project_id>/pages/<int:page_id>/rotate")
@login_required
def rotate_project_page(project_id, page_id):
    page = owned_project_page(project_id, page_id)
    if not page or not page.processed_data:
        return "Page image not found", 404
    try:
        processed = preprocess_document_image(page.processed_data, {"rotation": 90})
        page.processed_data = processed.data
        page.processed_mime_type = processed.mime_type
        page.image_width, page.image_height = processed.width, processed.height
        page.rotation = (page.rotation + 90) % 360
        page.extraction_status = "pending"
        page.processing_stage = "improved"
        page.review_status = "pending"
        page.extracted_text = ""
        page.project.status = "reviewing"
        for block in list(page.blocks):
            db.session.delete(block)
        db.session.commit()
        flash(f"Page {page.page_order} rotated. Run recognition again.", "success")
    except ValueError as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(url_for("review_project_recognition", project_id=project_id))


@app.post("/projects/<int:project_id>/pages/<int:page_id>/replace")
@login_required
def replace_project_page(project_id, page_id):
    page = owned_project_page(project_id, page_id)
    upload = request.files.get(f"replacement_{page_id}")
    if not page:
        return "Page not found", 404
    if not upload or not upload.filename:
        flash("Choose a replacement image.", "error")
        return redirect(url_for("review_project_recognition", project_id=project_id))
    try:
        old_file = page.source_file
        data = upload.read()
        filename = secure_filename(upload.filename)[:255] or f"rescan-page-{page.page_order}.png"
        mime_type = validate_document_upload(data, filename, upload.mimetype)
        if mime_type == "application/pdf":
            raise ValueError("Rescan one page as a JPG, PNG, or WebP image.")
        processed = preprocess_document_image(data)
        source_file = ProjectFile(
            project_id=project_id, original_filename=filename, mime_type=mime_type,
            original_data=data, source_kind=request.form.get("source_kind", "upload")[:20],
            sha256=hashlib.sha256(data).hexdigest(),
        )
        db.session.add(source_file)
        db.session.flush()
        page.file_id = source_file.id
        db.session.flush()
        remaining_old_pages = db.session.scalar(db.select(func.count(ProjectPage.id)).where(
            ProjectPage.file_id == old_file.id
        )) or 0
        if not remaining_old_pages:
            db.session.delete(old_file)
        page.page_number = 1
        page.processed_data = processed.data
        page.processed_mime_type = processed.mime_type
        page.image_width, page.image_height = processed.width, processed.height
        page.extracted_text = ""
        page.extraction_status = "pending"
        page.processing_stage = "improved"
        page.review_status = "pending"
        page.warning = " ".join(processed.warnings)
        page.project.status = "reviewing"
        for block in list(page.blocks):
            db.session.delete(block)
        db.session.commit()
        flash(f"Page {page.page_order} replaced. Run recognition again.", "success")
    except (ValueError, SQLAlchemyError) as error:
        db.session.rollback()
        flash(str(error), "error")
    return redirect(url_for("review_project_recognition", project_id=project_id))


@app.get("/projects/<int:project_id>/pages/<int:page_id>/image/<variant>")
@login_required
def project_page_image(project_id, page_id, variant):
    page = owned_project_page(project_id, page_id)
    if not page or variant not in {"original", "processed"}:
        return "Page image not found", 404
    if variant == "processed":
        if not page.processed_data:
            return "Processed image not available", 404
        return private_binary_response(page.processed_data, page.processed_mime_type)
    if page.source_file.mime_type == "application/pdf":
        return private_binary_response(
            page.source_file.original_data, "application/pdf", page.source_file.original_filename,
        )
    return private_binary_response(page.source_file.original_data, page.source_file.mime_type)


@app.get("/projects/<int:project_id>/blocks/<int:block_id>/region")
@login_required
def document_block_region(project_id, block_id):
    block = db.session.scalar(db.select(DocumentBlock).join(ProjectPage).join(LearningProject).where(
        DocumentBlock.id == block_id,
        ProjectPage.project_id == project_id,
        LearningProject.user_id == current_user.id,
    ))
    if not block or not block.page.processed_data:
        return "Region not found", 404
    try:
        region = crop_image_region(block.page.processed_data, json_value(block.bbox_json))
        return private_binary_response(region, "image/png")
    except ValueError:
        return "Region could not be rendered", 422


@app.get("/projects/<int:project_id>")
@login_required
def project_dashboard(project_id):
    project = owned_project(project_id)
    if not project:
        return "Project not found", 404
    pages = sorted(project.pages, key=lambda item: item.page_order)
    all_sections = sorted(project.sections, key=lambda item: item.position)
    sections = [item for item in all_sections if not item.excluded]
    completed = sum(1 for item in sections if item.completed_at)
    strong = sum(1 for item in sections if item.status in {"strong", "exam_ready"})
    weak = sum(1 for item in sections if item.status == "needs_review")
    readiness = round(sum(item.mastery_score for item in sections) / len(sections)) if sections else 0
    plan = preparation_plan(project.exam_date, len(sections), completed)
    current_section = next((item for item in sections if not item.completed_at), sections[-1] if sections else None)
    return render_template(
        "project_dashboard.html", project=project, pages=pages, sections=sections,
        all_sections=all_sections,
        completed=completed, strong=strong, weak=weak, readiness=readiness,
        preparation=plan, current_section=current_section,
    )


@app.post("/projects/<int:project_id>/pages/reorder")
@login_required
def reorder_project_pages(project_id):
    project = owned_project(project_id)
    if not project:
        return jsonify(error="Project not found"), 404
    order = (request.get_json(silent=True) or {}).get("page_ids", [])
    owned_ids = {page.id for page in project.pages}
    if not isinstance(order, list) or set(order) != owned_ids:
        return jsonify(error="Page order must contain every project page exactly once."), 400
    lookup = {page.id: page for page in project.pages}
    for position, page_id in enumerate(order, start=1):
        lookup[page_id].page_order = position
    project.updated_at = utcnow()
    db.session.commit()
    return jsonify(status="ok")


@app.post("/projects/<int:project_id>/pages/<int:page_id>/delete")
@login_required
def delete_project_page(project_id, page_id):
    project = owned_project(project_id)
    page = db.session.scalar(db.select(ProjectPage).where(
        ProjectPage.id == page_id, ProjectPage.project_id == project_id
    )) if project else None
    if not page:
        return "Page not found", 404
    if project.sections:
        flash("Pages cannot be removed after a section plan exists because its source references must remain valid.", "error")
        return redirect(url_for("project_dashboard", project_id=project_id))
    source_file = page.source_file
    db.session.delete(page)
    db.session.flush()
    remaining_file_pages = db.session.scalar(db.select(func.count(ProjectPage.id)).where(
        ProjectPage.file_id == source_file.id
    )) or 0
    if not remaining_file_pages:
        db.session.delete(source_file)
    for position, remaining in enumerate(
        sorted(project.pages, key=lambda item: item.page_order), start=1
    ):
        remaining.page_order = position
    db.session.commit()
    destination = "review_project_recognition" if "/review" in (request.referrer or "") else "project_dashboard"
    return redirect(url_for(destination, project_id=project_id))


@app.post("/projects/<int:project_id>/process")
@login_required
def process_project(project_id):
    project = owned_project(project_id)
    if not project:
        return "Project not found", 404
    pages = sorted([page for page in project.pages if not page.excluded], key=lambda item: item.page_order)
    if not pages:
        flash("Upload at least one page before processing.", "error")
        return redirect(url_for("project_dashboard", project_id=project_id))
    if project.exams or any(section_has_progress(section) for section in project.sections):
        flash("This plan already has saved learning or exam progress. Create a new project to rebuild it safely.", "error")
        return redirect(url_for("project_dashboard", project_id=project_id))
    if project.status != "confirmed":
        flash("Review and confirm recognized text before Learnova creates learning sections.", "error")
        return redirect(url_for("review_project_recognition", project_id=project_id))
    cleaned = clean_extracted_pages([page.extracted_text for page in pages])
    for page, cleaned_text in zip(pages, cleaned, strict=True):
        page.extracted_text = cleaned_text
    project.updated_at = utcnow()
    db.session.commit()

    readable_pages = [page for page in pages if page.extracted_text.strip()]
    if not readable_pages:
        flash("None of the uploaded pages contained reliably readable content.", "error")
        return redirect(url_for("project_dashboard", project_id=project_id))
    source_payload = [{
        "page_id": page.id,
        "page_order": page.page_order,
        "filename": page.source_file.original_filename,
        "text": page.extracted_text[:9000],
        "review_status": page.review_status,
        "recognition_confidence": page.recognition_confidence,
        "confirmed_high_priority": bool(
            page.review_status == "confirmed" and (
                page.important or page.teacher_highlighted
                or any(block.important or block.teacher_highlighted for block in page.blocks)
            )
        ),
        "formulas": [block.content for block in page.blocks if block.block_type == "formula" and not block.crossed_out],
        "diagrams": [{
            "labels": block.content, "bbox": json_value(block.bbox_json),
            "nearby_source": json_value(block.source_json),
        } for block in page.blocks if block.block_type == "diagram"],
    } for page in readable_pages]
    prompt = f"""Divide this student's uploaded {project.subject} material into focused 5–15 minute learning sections.
Uploaded pages are the only source of truth: {json.dumps(source_payload, ensure_ascii=False)}
Return JSON exactly as {{"sections":[{{"title":"...","main_topic":"...","learning_goals":[],"important_facts":[],"definitions":[],"formulas":[],"examples":[],"vocabulary":[],"relationships":[],"likely_exam_questions":[],"source_page_ids":[1],"simple_explanation":"...","standard_explanation":"...","detailed_explanation":"...","estimated_minutes":10,"recall_cards":[{{"kind":"flashcard|recall|fill_blank|definition|formula|timeline|vocabulary","prompt":"...","answer":"...","source_text":"exact supporting excerpt"}}]}}]}}.
Preserve formulas and dates exactly. Give confirmed_high_priority content greater weight in summaries, recall cards, Test Yourself, and likely exam questions. Diagrams may support label/function questions only when their visible labels and nearby source text support the answer; never invent diagram meaning. Do not add topics absent from the pages. Every section must reference valid page_id values and every recall answer must be supported by source_text."""
    try:
        response = create_response(
            model=TUTOR_MODEL, instructions=tutor_instructions(), input=prompt,
            max_output_tokens=PROJECT_TOKEN_LIMIT, temperature=0.1, **quality_options(),
        )
        result = parse_json(response.output_text)
        raw_sections = result["sections"]
        if not isinstance(raw_sections, list) or not raw_sections:
            raise ValueError("No learning sections were returned")
        valid_page_ids = {page.id for page in readable_pages}
        normalized = [
            normalize_section(item, position, valid_page_ids)
            for position, item in enumerate(raw_sections, start=1)
        ]
        for old_section in list(project.sections):
            db.session.delete(old_section)
        db.session.flush()
        for item in normalized:
            section = LearningSection(
                project_id=project.id, position=item["position"], title=item["title"],
                main_topic=item["main_topic"],
                learning_goals_json=json.dumps(item["learning_goals"], ensure_ascii=False),
                important_facts_json=json.dumps(item["important_facts"], ensure_ascii=False),
                definitions_json=json.dumps(item["definitions"], ensure_ascii=False),
                formulas_json=json.dumps(item["formulas"], ensure_ascii=False),
                examples_json=json.dumps(item["examples"], ensure_ascii=False),
                vocabulary_json=json.dumps(item["vocabulary"], ensure_ascii=False),
                relationships_json=json.dumps(item["relationships"], ensure_ascii=False),
                likely_questions_json=json.dumps(item["likely_exam_questions"], ensure_ascii=False),
                source_page_ids_json=json.dumps(item["source_page_ids"]),
                simple_explanation=item["simple_explanation"],
                standard_explanation=item["standard_explanation"],
                detailed_explanation=item["detailed_explanation"],
                estimated_minutes=item["estimated_minutes"],
            )
            db.session.add(section)
            db.session.flush()
            for card in item["recall_cards"]:
                if not isinstance(card, dict) or not card.get("prompt") or not card.get("answer"):
                    continue
                supporting = str(card.get("source_text", "")).strip()
                section_source = "\n".join(
                    page.extracted_text for page in readable_pages
                    if page.id in item["source_page_ids"]
                )
                if not supporting or supporting.casefold() not in section_source.casefold():
                    raise ValueError("A recall card was not supported by its referenced source pages")
                db.session.add(RecallCard(
                    section_id=section.id, kind=str(card.get("kind", "recall"))[:40],
                    prompt=str(card["prompt"]), answer=str(card["answer"]),
                    source_text=supporting,
                ))
        project.status = "planned"
        project.updated_at = utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Section planning failed")
        flash("The pages were saved, but a complete grounded section plan could not be created.", "error")
    return redirect(url_for("project_dashboard", project_id=project_id))


@app.get("/projects/<int:project_id>/sections/<int:section_id>")
@login_required
def learn_section(project_id, section_id):
    section = owned_section(project_id, section_id)
    if not section:
        return "Section not found", 404
    level = request.args.get("level", "standard")
    if level not in {"simple", "standard", "detailed"}:
        level = "standard"
    if section.status == "not_started":
        section.status = "learning"
        db.session.commit()
    fields = {
        "learning_goals": json_value(section.learning_goals_json),
        "important_facts": json_value(section.important_facts_json),
        "definitions": json_value(section.definitions_json),
        "formulas": json_value(section.formulas_json),
        "examples": json_value(section.examples_json),
        "vocabulary": json_value(section.vocabulary_json),
        "relationships": json_value(section.relationships_json),
        "likely_questions": json_value(section.likely_questions_json),
    }
    explanation = getattr(section, f"{level}_explanation")
    return render_template(
        "section_learning.html", project=section.project, section=section,
        level=level, explanation=explanation, fields=fields,
    )


@app.post("/projects/<int:project_id>/sections/<int:section_id>/edit")
@login_required
def edit_section(project_id, section_id):
    section = owned_section(project_id, section_id)
    if not section:
        return "Section not found", 404
    title = request.form.get("title", "").strip()[:255]
    if title:
        section.title = title
    section.excluded = request.form.get("excluded") == "1"
    db.session.commit()
    return redirect(url_for("project_dashboard", project_id=project_id))


@app.post("/projects/<int:project_id>/sections/reorder")
@login_required
def reorder_sections(project_id):
    project = owned_project(project_id)
    if not project:
        return jsonify(error="Project not found"), 404
    order = (request.get_json(silent=True) or {}).get("section_ids", [])
    owned_ids = {section.id for section in project.sections}
    if not isinstance(order, list) or set(order) != owned_ids:
        return jsonify(error="Section order must contain every section exactly once."), 400
    lookup = {section.id: section for section in project.sections}
    for position, section_id in enumerate(order, start=1):
        lookup[section_id].position = position
    db.session.commit()
    return jsonify(status="ok")


@app.post("/projects/<int:project_id>/sections/merge")
@login_required
def merge_sections(project_id):
    project = owned_project(project_id)
    ids = request.form.getlist("section_ids")
    try:
        section_ids = [int(value) for value in ids]
    except ValueError:
        section_ids = []
    sections = [item for item in project.sections if item.id in section_ids] if project else []
    if len(sections) != 2:
        flash("Choose exactly two sections to merge.", "error")
        return redirect(url_for("project_dashboard", project_id=project_id))
    sections.sort(key=lambda item: item.position)
    first, second = sections
    if project.exams or any(section_has_progress(section) for section in sections):
        flash("Sections with saved learning or exam progress cannot be merged.", "error")
        return redirect(url_for("project_dashboard", project_id=project_id))
    first.title = request.form.get("title", "").strip()[:255] or f"{first.title} + {second.title}"
    for attribute in (
        "simple_explanation", "standard_explanation", "detailed_explanation"
    ):
        setattr(first, attribute, f"{getattr(first, attribute)}\n\n{getattr(second, attribute)}")
    source_ids = list(dict.fromkeys(
        json_value(first.source_page_ids_json) + json_value(second.source_page_ids_json)
    ))
    first.source_page_ids_json = json.dumps(source_ids)
    first.estimated_minutes = min(15, first.estimated_minutes + second.estimated_minutes)
    db.session.delete(second)
    db.session.flush()
    for position, section in enumerate(
        sorted(project.sections, key=lambda item: item.position), start=1
    ):
        section.position = position
    db.session.commit()
    return redirect(url_for("project_dashboard", project_id=project_id))


@app.post("/projects/<int:project_id>/sections/<int:section_id>/split")
@login_required
def split_section(project_id, section_id):
    section = owned_section(project_id, section_id)
    if not section:
        return "Section not found", 404
    if section.project.exams or section_has_progress(section):
        flash("A section with saved learning or exam progress cannot be split.", "error")
        return redirect(url_for("project_dashboard", project_id=project_id))
    source = section_source_text(section)
    prompt = f"""Split this learning section into exactly two smaller sections grounded only in the source.
Current title: {section.title}
Source: {source[:18000]}
Return JSON as {{"sections":[{{"title":"...","main_topic":"...","simple_explanation":"...","standard_explanation":"...","detailed_explanation":"..."}},{{"title":"...","main_topic":"...","simple_explanation":"...","standard_explanation":"...","detailed_explanation":"..."}}]}}. Do not add new topics."""
    try:
        response = create_response(
            model=TUTOR_MODEL, instructions=tutor_instructions(), input=prompt,
            max_output_tokens=PROJECT_TOKEN_LIMIT, temperature=0.1, **quality_options(),
        )
        parts = parse_json(response.output_text)["sections"]
        if not isinstance(parts, list) or len(parts) != 2:
            raise ValueError("Split response must contain two sections")
        shared = {
            "learning_goals_json": section.learning_goals_json,
            "important_facts_json": section.important_facts_json,
            "definitions_json": section.definitions_json,
            "formulas_json": section.formulas_json,
            "examples_json": section.examples_json,
            "vocabulary_json": section.vocabulary_json,
            "relationships_json": section.relationships_json,
            "likely_questions_json": section.likely_questions_json,
            "source_page_ids_json": section.source_page_ids_json,
        }
        section.title = str(parts[0].get("title", section.title))[:255]
        section.main_topic = str(parts[0].get("main_topic", ""))[:255]
        section.simple_explanation = str(parts[0].get("simple_explanation", ""))
        section.standard_explanation = str(parts[0].get("standard_explanation", ""))
        section.detailed_explanation = str(parts[0].get("detailed_explanation", ""))
        for item in section.project.sections:
            if item.position > section.position:
                item.position += 1
        second = LearningSection(
            project_id=project_id, position=section.position + 1,
            title=str(parts[1].get("title", "New section"))[:255],
            main_topic=str(parts[1].get("main_topic", ""))[:255],
            simple_explanation=str(parts[1].get("simple_explanation", "")),
            standard_explanation=str(parts[1].get("standard_explanation", "")),
            detailed_explanation=str(parts[1].get("detailed_explanation", "")),
            estimated_minutes=max(5, section.estimated_minutes // 2), **shared,
        )
        section.estimated_minutes = max(5, section.estimated_minutes // 2)
        db.session.add(second)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Section split failed")
        flash("This section could not be split reliably.", "error")
    return redirect(url_for("project_dashboard", project_id=project_id))


@app.post("/projects/<int:project_id>/sections/<int:section_id>/recall/<int:card_id>")
@login_required
def answer_recall_card(project_id, section_id, card_id):
    section = owned_section(project_id, section_id)
    card = db.session.scalar(db.select(RecallCard).where(
        RecallCard.id == card_id, RecallCard.section_id == section_id
    )) if section else None
    if not card:
        return jsonify(error="Recall card not found"), 404
    answer = request.form.get("answer", "").strip()
    correct = answer.casefold() == card.answer.strip().casefold()
    card.attempts += 1
    if correct:
        card.correct_attempts += 1
    db.session.commit()
    return jsonify(correct=correct, answer=card.answer, source_text=card.source_text)


@app.post("/projects/<int:project_id>/sections/<int:section_id>/test")
@login_required
def start_section_test(project_id, section_id):
    section = owned_section(project_id, section_id)
    if not section:
        return "Section not found", 404
    source = section_source_text(section)
    question_count = max(3, min(7, round(section.estimated_minutes / 2)))
    prompt = f"""Create the opening lesson and first Test Yourself question for this uploaded-material section.
Section: {section.title}
Source material: {source[:24000]}
Return the normal lesson JSON shape with lesson_title, detected_level, concepts, explanation, worked_example, teacher_tips, exceptions, and question. The first question must include source_page_ids {section.source_page_ids_json}, use only the source, and be one of multiple_choice, true_false, fill_blank, short_answer, explanation, or calculation. Do not invent missing facts."""
    try:
        session_id = start_saved_practice(
            prompt, section.project.subject, "section-test", test_total=question_count
        )
        lesson = db.session.scalar(db.select(Lesson).where(
            Lesson.session_id == session_id, Lesson.user_id == current_user.id
        ))
        if not lesson:
            raise ValueError("Saved section test lesson is missing")
        lesson.section_id = section.id
        state = SESSIONS[session_id]
        state["session_kind"] = "section_test"
        state["section_id"] = section.id
        state["source_context"] = source[:24000]
        save_session_state(session_id, commit=False)
        db.session.commit()
        return redirect(url_for("index", session_id=session_id))
    except Exception:
        db.session.rollback()
        app.logger.exception("Section test generation failed")
        flash("The section test could not be generated right now.", "error")
        return redirect(url_for("learn_section", project_id=project_id, section_id=section_id))


def save_exam_answer(exam, question, answer_text):
    saved = db.session.scalar(db.select(ExamAnswer).where(
        ExamAnswer.exam_id == exam.id, ExamAnswer.question_id == question.id
    ))
    if not saved:
        saved = ExamAnswer(exam_id=exam.id, question_id=question.id)
        db.session.add(saved)
    saved.answer_text = str(answer_text or "")[:12000]
    saved.saved_at = utcnow()
    return saved


def submit_exam_record(exam):
    if exam.status == "submitted":
        return
    questions = sorted(exam.questions, key=lambda item: item.position)
    answers = {item.question_id: item for item in exam.answers}
    open_items = []
    for question in questions:
        answer = answers.get(question.id)
        answer_text = answer.answer_text if answer else ""
        deterministic = deterministic_question_score(
            question.question_type, question.expected_answer, answer_text
        )
        if deterministic is None:
            open_items.append({
                "question_id": question.id,
                "prompt": question.prompt,
                "expected_answer": question.expected_answer,
                "student_answer": answer_text,
                "supporting_source": question.supporting_text,
            })
        else:
            if not answer:
                answer = save_exam_answer(exam, question, answer_text)
                answers[question.id] = answer
            answer.score = deterministic
            answer.evaluation = (
                "Correct." if deterministic == 100 else
                ("Unanswered." if not answer_text else f"Expected: {question.expected_answer}")
            )
    if open_items:
        prompt = f"""Evaluate these exam answers only against their expected answers and uploaded supporting source.
{json.dumps(open_items, ensure_ascii=False)}
Return JSON as {{"results":[{{"question_id":1,"score":0,"evaluation":"brief source-grounded explanation"}}]}}. Scores must be 0–100. Give partial credit for partially correct reasoning. Never add facts absent from supporting_source."""
        response = create_response(
            model=TUTOR_MODEL, instructions=tutor_instructions(), input=prompt,
            max_output_tokens=PROJECT_TOKEN_LIMIT, temperature=0, **quality_options(),
        )
        result_rows = parse_json(response.output_text)["results"]
        result_map = {int(item["question_id"]): item for item in result_rows}
        for item in open_items:
            question = next(value for value in questions if value.id == item["question_id"])
            answer = answers.get(question.id) or save_exam_answer(exam, question, item["student_answer"])
            evaluation = result_map.get(question.id)
            if not evaluation:
                raise ValueError(f"Missing evaluation for exam question {question.id}")
            answer.score = max(0, min(100, float(evaluation["score"])))
            answer.evaluation = str(evaluation.get("evaluation", ""))
            answers[question.id] = answer
    db.session.flush()
    scores = [float(answers[item.id].score or 0) for item in questions]
    difficulty_scores = {}
    section_scores = {}
    for question in questions:
        score = float(answers[question.id].score or 0)
        difficulty_scores.setdefault(question.difficulty, []).append(score)
        section_scores.setdefault(question.section_id, []).append(score)
    exam.score = round(sum(scores) / max(1, len(scores)), 2)
    exam.status = "submitted"
    exam.submitted_at = utcnow()
    wrong = sum(1 for value in scores if value < 50)
    partial = sum(1 for value in scores if 50 <= value < 80)
    correct = sum(1 for value in scores if value >= 80)
    unanswered = sum(1 for item in answers.values() if not item.answer_text.strip())
    result = {
        "score": exam.score,
        "correct": correct,
        "partial": partial,
        "wrong": wrong,
        "unanswered": unanswered,
        "time_used_seconds": min(
            exam.duration_minutes * 60,
            max(0, int((as_utc(exam.submitted_at or utcnow()) - as_utc(exam.started_at)).total_seconds())),
        ),
        "difficulty_scores": {
            key: round(sum(values) / len(values), 2) for key, values in difficulty_scores.items()
        },
        "section_scores": {
            str(key): round(sum(values) / len(values), 2) for key, values in section_scores.items()
        },
    }
    exam.result_json = json.dumps(result)
    mistake_lesson = Lesson(
        user_id=exam.project.user_id, session_id=uuid.uuid4().hex,
        subject=exam.project.subject, title=f"Exam review: {exam.project.title}",
        content_json=json.dumps({"exam_id": exam.id}),
    )
    db.session.add(mistake_lesson)
    db.session.flush()
    for question in questions:
        answer = answers[question.id]
        section = db.session.get(LearningSection, question.section_id)
        if answer.score is not None and answer.score < 80 and section:
            db.session.add(Attempt(
                lesson_id=mistake_lesson.id, subject=exam.project.subject,
                question=question.prompt, concept=section.title,
                student_answer=answer.answer_text or "(unanswered)", score=round(answer.score),
                feedback=answer.evaluation or question.explanation,
                difficulty={"easy": 1, "medium": 2, "hard": 3}.get(question.difficulty, 2),
            ))
        if section:
            section_score = result["section_scores"][str(section.id)]
            section.mastery_score = round((section.mastery_score + section_score) / 2, 2)
            section.status = section_status_from_score(section.mastery_score, completed=True)
    db.session.commit()


@app.route("/projects/<int:project_id>/exam/new", methods=["GET", "POST"])
@login_required
def new_final_exam(project_id):
    project = owned_project(project_id)
    if not project:
        return "Project not found", 404
    sections = sorted(
        [item for item in project.sections if not item.excluded], key=lambda item: item.position
    )
    if request.method == "POST":
        try:
            count = max(5, min(50, int(request.form.get("question_count", 15))))
            duration = max(5, min(180, int(request.form.get("duration_minutes", 30))))
        except ValueError:
            flash("Question count and duration must be numbers.", "error")
            return redirect(url_for("new_final_exam", project_id=project_id))
        mode = request.form.get("difficulty", "mixed").lower()
        if mode not in {"easy", "medium", "hard", "mixed"}:
            mode = "mixed"
        selected_ids = {int(value) for value in request.form.getlist("section_ids") if value.isdigit()}
        included = [item for item in sections if item.id in selected_ids] or sections
        selected_types = [
            value for value in request.form.getlist("question_types")
            if value in ALLOWED_QUESTION_TYPES
        ] or ["multiple_choice", "short_answer", "explanation", "calculation"]
        if not included:
            flash("Process learning sections before creating an exam.", "error")
            return redirect(url_for("project_dashboard", project_id=project_id))
        allocation = proportional_section_counts([{
            "id": item.id, "mastery_score": item.mastery_score,
            "importance": len(json_value(item.important_facts_json)) + len(json_value(item.formulas_json)),
        } for item in included], count)
        source_sections = [{
            "section_id": item.id,
            "title": item.title,
            "question_count": allocation[item.id],
            "source_page_ids": json_value(item.source_page_ids_json),
            "source": section_source_text(item)[:18000],
            "likely_questions": json_value(item.likely_questions_json),
            "mastery": item.mastery_score,
        } for item in included]
        distribution = difficulty_distribution(count, mode)
        prompt = f"""Create a realistic final exam grounded primarily and strictly in the student's uploaded material.
Sections and sources: {json.dumps(source_sections, ensure_ascii=False)}
Settings: question_count={count}, difficulty_distribution={json.dumps(distribution)}, allowed_types={json.dumps(selected_types)}.
Return JSON as {{"questions":[{{"section_id":1,"source_page_ids":[1],"supporting_text":"exact excerpt supporting the answer","difficulty":"easy|medium|hard","question_type":"multiple_choice|true_false|matching|fill_blank|short_answer|explanation|calculation","prompt":"...","options":[],"expected_answer":"...","explanation":"source-grounded explanation shown only after submission"}}]}}.
Return exactly {count} questions and follow each section's question_count proportionally. Easy tests direct recall, medium tests connections/application, hard tests synthesis or unfamiliar application. Hard means deeper reasoning, not confusing wording. Every answer must be supported by supporting_text and valid source_page_ids."""
        try:
            response = create_response(
                model=TUTOR_MODEL, instructions=tutor_instructions(), input=prompt,
                max_output_tokens=max(PROJECT_TOKEN_LIMIT, count * 350), temperature=0.1,
                **quality_options(),
            )
            questions = parse_json(response.output_text)["questions"]
            if not isinstance(questions, list) or len(questions) != count:
                raise ValueError(f"Expected exactly {count} grounded exam questions")
            included_map = {item.id: item for item in included}
            exam = FinalExam(
                project_id=project.id, question_count=count, duration_minutes=duration,
                difficulty_mode=mode,
                included_section_ids_json=json.dumps(list(included_map)),
                question_types_json=json.dumps(selected_types),
                expires_at=utcnow() + timedelta(minutes=duration),
            )
            db.session.add(exam)
            db.session.flush()
            actual_sections = Counter()
            actual_difficulties = Counter()
            for position, item in enumerate(questions, start=1):
                section_id = int(item["section_id"])
                section = included_map.get(section_id)
                page_ids = [int(value) for value in item.get("source_page_ids", [])]
                valid_pages = set(json_value(section.source_page_ids_json)) if section else set()
                if not section or not page_ids or not set(page_ids) <= valid_pages:
                    raise ValueError(f"Question {position} has a missing or invalid source reference")
                question_type = str(item.get("question_type", ""))
                difficulty = str(item.get("difficulty", ""))
                if question_type not in selected_types or difficulty not in {"easy", "medium", "hard"}:
                    raise ValueError(f"Question {position} has invalid metadata")
                supporting = str(item.get("supporting_text", "")).strip()
                referenced_source = source_text_for_pages(project.id, page_ids)
                if not supporting or supporting.casefold() not in referenced_source.casefold():
                    raise ValueError(f"Question {position} is not supported by its source pages")
                actual_sections[section_id] += 1
                actual_difficulties[difficulty] += 1
                db.session.add(ExamQuestion(
                    exam_id=exam.id, section_id=section_id, position=position,
                    difficulty=difficulty, question_type=question_type,
                    prompt=str(item["prompt"]),
                    options_json=json.dumps(item.get("options", []), ensure_ascii=False),
                    expected_answer=str(item["expected_answer"]),
                    explanation=str(item.get("explanation", "")),
                    source_page_ids_json=json.dumps(page_ids), supporting_text=supporting,
                ))
            expected_difficulties = {key: value for key, value in distribution.items() if value}
            if dict(actual_sections) != {key: value for key, value in allocation.items() if value}:
                raise ValueError("Exam questions did not follow the required section allocation")
            if dict(actual_difficulties) != expected_difficulties:
                raise ValueError("Exam questions did not follow the required difficulty distribution")
            db.session.commit()
            return redirect(url_for("take_final_exam", exam_id=exam.id))
        except Exception:
            db.session.rollback()
            app.logger.exception("Final exam generation failed")
            flash("A complete source-grounded exam could not be generated. No partial exam was saved.", "error")
    readiness = round(sum(item.mastery_score for item in sections) / len(sections)) if sections else 0
    return render_template("exam_setup.html", project=project, sections=sections, readiness=readiness)


@app.get("/exams/<int:exam_id>")
@login_required
def take_final_exam(exam_id):
    exam = owned_exam(exam_id)
    if not exam:
        return "Exam not found", 404
    if exam.status == "submitted":
        return redirect(url_for("final_exam_results", exam_id=exam.id))
    if utcnow() >= as_utc(exam.expires_at):
        try:
            submit_exam_record(exam)
        except Exception:
            db.session.rollback()
            app.logger.exception("Automatic exam submission failed")
            return "The expired exam could not be submitted safely.", 500
        return redirect(url_for("final_exam_results", exam_id=exam.id))
    answers = {item.question_id: item.answer_text for item in exam.answers}
    questions = sorted(exam.questions, key=lambda item: item.position)
    return render_template("exam_take.html", exam=exam, project=exam.project,
                           questions=questions, answers=answers,
                           expires_epoch=round(as_utc(exam.expires_at).timestamp()))


@app.post("/exams/<int:exam_id>/autosave")
@login_required
def autosave_exam(exam_id):
    exam = owned_exam(exam_id)
    if not exam:
        return jsonify(error="Exam not found"), 404
    if exam.status == "submitted":
        return jsonify(status="submitted"), 409
    if utcnow() >= as_utc(exam.expires_at):
        try:
            submit_exam_record(exam)
        except Exception:
            db.session.rollback()
            app.logger.exception("Automatic exam submission failed during autosave")
            return jsonify(error="Your saved answers are safe, but evaluation is temporarily unavailable."), 503
        return jsonify(status="submitted", redirect=url_for("final_exam_results", exam_id=exam.id)), 409
    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    question = db.session.scalar(db.select(ExamQuestion).where(
        ExamQuestion.id == question_id, ExamQuestion.exam_id == exam.id
    ))
    if not question:
        return jsonify(error="Question not found"), 404
    save_exam_answer(exam, question, payload.get("answer", ""))
    db.session.commit()
    return jsonify(status="saved", saved_at=utcnow().isoformat())


@app.post("/exams/<int:exam_id>/submit")
@login_required
def submit_final_exam(exam_id):
    exam = owned_exam(exam_id)
    if not exam:
        return "Exam not found", 404
    if exam.status == "submitted":
        return redirect(url_for("final_exam_results", exam_id=exam.id))
    for question in exam.questions:
        key = f"question_{question.id}"
        if key in request.form:
            save_exam_answer(exam, question, request.form[key])
    # Preserve submitted text independently from the potentially fallible AI evaluation.
    db.session.commit()
    try:
        submit_exam_record(exam)
    except Exception:
        db.session.rollback()
        app.logger.exception("Exam submission failed")
        flash("Your answers are saved, but evaluation could not finish. Submit again safely.", "error")
        return redirect(url_for("take_final_exam", exam_id=exam.id))
    return redirect(url_for("final_exam_results", exam_id=exam.id))


@app.get("/exams/<int:exam_id>/results")
@login_required
def final_exam_results(exam_id):
    exam = owned_exam(exam_id)
    if not exam:
        return "Exam not found", 404
    if exam.status != "submitted":
        return redirect(url_for("take_final_exam", exam_id=exam.id))
    answers = {item.question_id: item for item in exam.answers}
    sections = {item.id: item for item in exam.project.sections}
    questions = sorted(exam.questions, key=lambda item: item.position)
    return render_template(
        "exam_results.html", exam=exam, project=exam.project, questions=questions,
        answers=answers, sections=sections, result=json_value(exam.result_json, {}),
    )


@app.get("/dashboard")
@login_required
def dashboard():
    language = get_current_language()
    masteries = db.session.scalars(
        db.select(ConceptMastery).where(ConceptMastery.user_id == current_user.id)
        .order_by(ConceptMastery.mastery_score, ConceptMastery.updated_at.desc())
    ).all()
    subject_rows = db.session.execute(
        db.select(ConceptMastery.subject, func.avg(ConceptMastery.mastery_score), func.sum(ConceptMastery.attempts))
        .where(ConceptMastery.user_id == current_user.id).group_by(ConceptMastery.subject)
        .order_by(func.avg(ConceptMastery.mastery_score))
    ).all()
    subject_filter = request.args.get("subject", "").strip()[:80]
    status_filter = request.args.get("status", "").strip()
    allowed_statuses = {"weak", "learning", "strong", "mastered", "understood"}
    if status_filter not in allowed_statuses:
        status_filter = ""
    mistake_query = (
        db.select(Attempt).join(Lesson).where(Lesson.user_id == current_user.id, Attempt.score < 80)
        .order_by(Attempt.timestamp.desc())
    )
    if subject_filter:
        mistake_query = mistake_query.where(
            func.coalesce(Attempt.subject, Lesson.subject) == subject_filter
        )
    attempts = db.session.scalars(mistake_query.limit(100)).all()
    mastery_lookup = {
        (item.subject, item.concept): item.mastery_score for item in masteries
    }
    mistakes = []
    for item in attempts:
        item_subject = item.subject or item.lesson.subject
        score = mastery_lookup.get((item_subject, item.concept), 0)
        status = "understood" if item.understood_at else mastery_status(score)
        if status_filter and status != status_filter:
            continue
        mistakes.append({"attempt": item, "mastery_status": status})
    mistake_subjects = db.session.scalars(
        db.select(func.coalesce(Attempt.subject, Lesson.subject)).select_from(Attempt).join(Lesson).where(
            Lesson.user_id == current_user.id, Attempt.score < 80
        ).distinct().order_by(func.coalesce(Attempt.subject, Lesson.subject))
    ).all()
    now = utcnow()
    due_today = db.session.scalar(
        db.select(func.count(ConceptMastery.id)).where(
            ConceptMastery.user_id == current_user.id,
            or_(ConceptMastery.next_review_at.is_(None), ConceptMastery.next_review_at <= now),
        )
    ) or 0
    next_review = db.session.scalar(
        db.select(func.min(ConceptMastery.next_review_at)).where(
            ConceptMastery.user_id == current_user.id,
            ConceptMastery.next_review_at.is_not(None),
        )
    )
    trend_attempts = db.session.scalars(
        db.select(Attempt).join(Lesson).where(
            Lesson.user_id == current_user.id, Attempt.mastery_after.is_not(None)
        ).order_by(Attempt.timestamp.desc()).limit(12)
    ).all()
    mastery_trend = list(reversed(trend_attempts))
    lessons = db.session.scalars(
        db.select(Lesson).where(Lesson.user_id == current_user.id)
        .order_by(Lesson.created_at.desc()).limit(20)).all()
    projects = db.session.scalars(
        db.select(LearningProject).where(LearningProject.user_id == current_user.id)
        .order_by(LearningProject.updated_at.desc()).limit(6)
    ).all()
    return render_template("dashboard.html", masteries=masteries[:3], subjects=subject_rows,
                           mistakes=mistakes, mistake_subjects=mistake_subjects,
                           subject_filter=subject_filter, status_filter=status_filter,
                           due_today=due_today, next_review=next_review,
                           mastery_trend=mastery_trend, lessons=lessons, projects=projects,
                           language=language)


@app.get("/lessons/<int:lesson_id>")
@login_required
def lesson_history(lesson_id):
    lesson = db.session.scalar(db.select(Lesson).where(
        Lesson.id == lesson_id, Lesson.user_id == current_user.id))
    if not lesson:
        return "Lesson not found", 404
    return render_template("lesson_history.html", lesson=lesson, language=get_current_language())


@app.get("/practice/today")
@login_required
def todays_practice():
    now = utcnow()
    masteries = db.session.scalars(
        db.select(ConceptMastery).where(ConceptMastery.user_id == current_user.id)
        .order_by(ConceptMastery.mastery_score, ConceptMastery.next_review_at)
    ).all()
    due = [item for item in masteries if review_is_due(item.next_review_at, now)]
    weakest = masteries[:5]
    failed_attempts = db.session.scalars(
        db.select(Attempt).join(Lesson).where(
            Lesson.user_id == current_user.id, Attempt.score < 50
        ).order_by(Attempt.timestamp.desc()).limit(30)
    ).all()
    recently_failed = []
    seen = set()
    for attempt in failed_attempts:
        key = (attempt.subject or attempt.lesson.subject, attempt.concept)
        if key not in seen:
            recently_failed.append({"subject": key[0], "concept": key[1], "date": attempt.timestamp})
            seen.add(key)
        if len(recently_failed) == 5:
            break
    states = [mastery_state(item) for item in masteries]
    question_count = estimated_question_count(states, now) if states else 0
    language = get_current_language()
    return render_template(
        "todays_practice.html",
        due=due,
        weakest=weakest,
        recently_failed=recently_failed,
        question_count=question_count,
        estimated_minutes=question_count * 2,
        difficulty_label=difficulty_label,
        language=language,
    )


@app.post("/practice/today/start")
@login_required
def start_todays_practice():
    plan = user_mastery_plan(current_user.id)
    if not plan:
        flash("Complete at least one lesson question before starting adaptive practice.", "error")
        return redirect(url_for("todays_practice"))
    target = plan[0]
    previous_questions = recent_concept_questions(
        current_user.id, target["subject"], target["concept"]
    )
    previous_mistakes = db.session.execute(
        db.select(Attempt.question, Attempt.student_answer, Attempt.feedback).join(Lesson).where(
            Lesson.user_id == current_user.id,
            func.coalesce(Attempt.subject, Lesson.subject) == target["subject"],
            Attempt.concept == target["concept"],
            Attempt.score < 50,
        ).order_by(Attempt.timestamp.desc()).limit(3)
    ).all()
    context = {
        "subject": target["subject"],
        "concept": target["concept"],
        "difficulty": difficulty_label(target["difficulty_level"]),
        "mastery": target["mastery_score"],
        "previous_mistakes": [dict(row._mapping) for row in previous_mistakes],
        "recent_questions_to_avoid": previous_questions,
    }
    prompt = f"""Create the opening lesson and first question for today's adaptive practice.
Learning context: {json.dumps(context, ensure_ascii=False)}
Return valid JSON only in this shape:
{{"lesson_title":"Today's adaptive practice","detected_level":"adaptive review","concepts":[{{"name":"{target['concept']}","evidence":"due or prioritised review"}}],"explanation":"brief focused refresher","worked_example":{{"problem":"related example","steps":["small step"],"answer":"answer"}},"teacher_tips":["tip"],"exceptions":[],"question":{{"id":"q1","subject":"{target['subject']}","concept":"{target['concept']}","difficulty":{target['difficulty_level']},"type":"multiple_choice","prompt":"new question","hint":"small hint","options":[{{"id":"a","label":"choice"}},{{"id":"b","label":"choice"}},{{"id":"c","label":"choice"}},{{"id":"d","label":"choice"}}],"expected_answer":"correct option id"}}}}
Match the requested difficulty. Do not repeat any recent question exactly. Make exactly one option correct."""
    try:
        session_id = start_saved_practice(
            prompt,
            "Today's adaptive practice",
            "today-practice",
            test_total=len(plan),
            adaptive_plan=plan,
        )
        return redirect(url_for("index", session_id=session_id))
    except Exception:
        db.session.rollback()
        app.logger.exception("Today's practice generation failed")
        flash("Today's practice could not be generated right now.", "error")
        return redirect(url_for("todays_practice"))


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


def start_saved_practice(prompt, subject, log_task, test_total, adaptive_plan=None):
    response = create_response(
        model=TUTOR_MODEL,
        instructions=tutor_instructions(),
        input=prompt,
        max_output_tokens=LESSON_TOKEN_LIMIT,
        temperature=0.2,
        **quality_options(),
    )
    log_usage(response, log_task)
    lesson = parse_json(response.output_text)
    for key in ("lesson_title", "concepts", "explanation", "worked_example", "question"):
        if key not in lesson:
            raise KeyError(key)
    if not isinstance(lesson["concepts"], list) or not lesson["concepts"]:
        raise ValueError("Practice lesson needs at least one concept")
    if adaptive_plan:
        existing = {str(item.get("name", "")).casefold() for item in lesson["concepts"]}
        for target in adaptive_plan:
            if target["concept"].casefold() not in existing:
                lesson["concepts"].append({
                    "name": target["concept"], "evidence": f"Scheduled review: {target['subject']}"
                })
                existing.add(target["concept"].casefold())
        first_target = adaptive_plan[0]
        lesson["question"]["concept"] = first_target["concept"]
        lesson["question"]["subject"] = first_target["subject"]
        lesson["question"]["difficulty"] = first_target["difficulty_level"]
    session_id = uuid.uuid4().hex
    state = {
        "user_id": current_user.id,
        "lesson": lesson,
        "history": [],
        "chat_history": [],
        "language": learning_content_language(),
        "subject": subject,
        "test_total": test_total,
        "current_question": lesson["question"],
        "mastery": {
            item["name"]: {"attempts": 0, "total_score": 0}
            for item in lesson["concepts"] if item.get("name")
        },
    }
    if adaptive_plan:
        state["session_kind"] = "adaptive_practice"
        state["planned_concepts"] = [{
            "id": item["id"],
            "subject": item["subject"],
            "concept": item["concept"],
            "mastery_score": item["mastery_score"],
            "difficulty_level": item["difficulty_level"],
            "status": item["status"],
        } for item in adaptive_plan]
        state["initial_mastery"] = {
            f"{item['subject']}::{item['concept']}": item["mastery_score"]
            for item in adaptive_plan
        }
        state["mastery_changes"] = {}
    if not state["mastery"]:
        raise ValueError("Practice lesson concepts are invalid")
    SESSIONS[session_id] = state
    try:
        normalize_question_concept(state, lesson["question"])
        persist_lesson(session_id, subject, lesson)
    except Exception:
        SESSIONS.pop(session_id, None)
        raise
    return session_id


@app.post("/mistakes/<int:attempt_id>/understood")
@login_required
def mark_mistake_understood(attempt_id):
    attempt = db.session.scalar(
        db.select(Attempt).join(Lesson).where(
            Attempt.id == attempt_id, Lesson.user_id == current_user.id
        )
    )
    if not attempt:
        return "Mistake not found", 404
    attempt.understood_at = utcnow()
    db.session.commit()
    flash("Mistake marked as understood. The original attempt remains in your notebook.", "success")
    return redirect(url_for(
        "dashboard",
        subject=request.form.get("subject", "")[:80],
        status=request.form.get("status", "") if request.form.get("status") in {
            "weak", "learning", "strong", "mastered", "understood"
        } else "",
    ))


@app.post("/mistakes/<int:attempt_id>/similar")
@login_required
def practice_similar_mistake(attempt_id):
    attempt = db.session.scalar(
        db.select(Attempt).join(Lesson).where(
            Attempt.id == attempt_id, Lesson.user_id == current_user.id
        )
    )
    if not attempt:
        return "Mistake not found", 404
    source_section = db.session.scalar(
        db.select(LearningSection).join(LearningProject).where(
            LearningSection.id == attempt.lesson.section_id,
            LearningProject.user_id == current_user.id,
        )
    ) if attempt.lesson.section_id else None
    grounded_source = section_source_text(source_section)[:18000] if source_section else ""
    prompt = f"""Create one focused practice lesson with one new question similar in skill, but not wording, to this saved mistake.
Subject: {attempt.lesson.subject}
Concept: {attempt.concept}
Original question: {attempt.question}
Student answer: {attempt.student_answer}
Feedback: {attempt.feedback}
Uploaded source (when present, this is the only factual source): {grounded_source}

Return valid JSON only in this shape:
{{"lesson_title":"Similar question: {attempt.concept}","detected_level":"targeted review","concepts":[{{"name":"{attempt.concept}","evidence":"saved mistake"}}],"explanation":"brief correction of the underlying misconception without giving away the new answer","worked_example":{{"problem":"related example","steps":["small step"],"answer":"answer"}},"teacher_tips":["tip"],"exceptions":[],"question":{{"id":"q1","concept":"{attempt.concept}","difficulty":1,"type":"multiple_choice","prompt":"new similar question","hint":"small hint","options":[{{"id":"a","label":"choice"}},{{"id":"b","label":"choice"}},{{"id":"c","label":"choice"}},{{"id":"d","label":"choice"}}],"expected_answer":"correct option id"}}}}
Use exactly one correct option and do not repeat the original question. When uploaded source is present, do not introduce unsupported facts."""
    try:
        session_id = start_saved_practice(
            prompt, attempt.lesson.subject, "similar-mistake", test_total=1
        )
        if source_section:
            lesson = db.session.scalar(db.select(Lesson).where(
                Lesson.session_id == session_id, Lesson.user_id == current_user.id
            ))
            if not lesson:
                raise ValueError("Saved source-grounded practice lesson is missing")
            lesson.section_id = source_section.id
            state = SESSIONS[session_id]
            state["source_context"] = grounded_source
            state["section_id"] = source_section.id
            save_session_state(session_id, commit=False)
            db.session.commit()
        return redirect(url_for("index", session_id=session_id))
    except Exception:
        db.session.rollback()
        app.logger.exception("Similar-mistake practice generation failed")
        flash("A similar question could not be generated right now.", "error")
        return redirect(url_for("dashboard"))


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
    practice_subject = subject or "Adaptive review"
    concepts = [
        {"name": item.concept, "subject": item.subject, "mastery": round(item.mastery_score)}
        for item in weak
    ]
    weak_plan = prioritize_concepts(
        [mastery_state(item) for item in weak], question_count=5
    )
    prompt = f"""Create a focused revision lesson from these saved weakest concepts:
{json.dumps(concepts, ensure_ascii=False)}
Return valid JSON only in the same shape:
{{"lesson_title":"short title","detected_level":"adaptive review","concepts":[{{"name":"concept","evidence":"saved weak point"}}],"explanation":"step-by-step review","worked_example":{{"problem":"example","steps":["small step"],"answer":"answer"}},"teacher_tips":["tip"],"exceptions":[],"question":{{"id":"q1","concept":"one listed concept","difficulty":1,"type":"multiple_choice","prompt":"question targeting the weak concept","hint":"hint","options":[{{"id":"a","label":"choice"}},{{"id":"b","label":"choice"}},{{"id":"c","label":"choice"}},{{"id":"d","label":"choice"}}],"expected_answer":"correct option id"}}}}
Use only the listed concepts, target the weakest first, and make exactly one option correct. This begins a five-question adaptive test; later questions will be generated from the saved mastery state."""
    try:
        session_id = start_saved_practice(
            prompt, practice_subject, "weak-practice", test_total=5,
            adaptive_plan=weak_plan,
        )
        return redirect(url_for("index", session_id=session_id))
    except Exception:
        db.session.rollback()
        app.logger.exception("Weak-point practice generation failed")
        flash("The practice lesson could not be generated right now.", "error")
        return redirect(url_for("dashboard"))


@app.post("/api/analyze")
@login_required
def analyze_material():
    uploads = [upload for upload in request.files.getlist("images") if upload.filename]
    study_goal = request.form.get("study_goal", "").strip()[:3000]
    subject = request.form.get("subject", "Other").strip()[:80] or "Other"
    language = learning_content_language()
    if not uploads and not study_goal:
        return jsonify(error=tr("Describe what you want to learn or attach study material.")), 400
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
            instructions=tutor_instructions(),
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
        return jsonify(session_id=session_id, test_total=5, lesson=public_lesson, question=question)
    except (ValueError, json.JSONDecodeError, KeyError) as error:
        db.session.rollback()
        return jsonify(error=f"The material could not be read reliably: {error}"), 422
    except Exception as error:
        db.session.rollback()
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
        return jsonify(error=tr("This lesson expired. Upload the material again.")), 404
    if raw_answer is None or raw_answer == "" or (isinstance(raw_answer, list) and not raw_answer):
        return jsonify(error=tr("Write an answer before checking it.")), 400
    if len(answer) > 12000:
        return jsonify(error=tr("Keep the answer under 12,000 characters.")), 400
    session["language"] = learning_content_language()

    question = session["current_question"]
    question_subject = str(question.get("subject") or session.get("subject", "Other"))[:80]
    hints_used = bool(payload.get("hints_used", False))
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
    adaptive_plan = session.get("planned_concepts", [])
    planned_next_target = (
        adaptive_plan[next_question_number - 1]
        if adaptive_plan and not is_final and next_question_number <= len(adaptive_plan) else None
    )
    context = {
        "subject": question_subject,
        "lesson_title": session["lesson"]["lesson_title"],
        "concepts": [item.get("name", "") for item in session["lesson"]["concepts"]],
        "teacher_tips": session["lesson"].get("teacher_tips", [])[:2],
        "exceptions": session["lesson"].get("exceptions", [])[:2],
        "question": question,
        "student_answer": answer,
        "hints_used": hints_used,
        "previous_results": session["history"][-3:],
        "concept_mastery": mastery_snapshot(session),
        "response_language": session["language"],
        "question_number": question_number,
        "test_total": session["test_total"],
        "is_final_question": is_final,
        "next_target": planned_next_target,
        "uploaded_source": session.get("source_context", ""),
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
For mathematics or physics, format feedback and correction with real newline characters: one named step, equation transformation, or unit conversion per line. Never compress a multi-step calculation into one paragraph.
For multiple_choice and dropdown use exactly one correct option and 4 options. For checkboxes use 4 or 5 options with 2 or 3 correct answers. For ordering provide 4 items in a shuffled order. For text, options must be an empty list.
When next_target is present, target exactly that subject, concept, and difficulty. Otherwise target the weakest relevant concept. When uploaded_source is present, it is the only factual source for evaluation and new questions; never introduce facts outside it.
Follow the exact null/object structure shown above. Do not replace a required object with null."""

    original_session = json.loads(json.dumps(session, ensure_ascii=False))
    try:
        response = create_response(
            model=TUTOR_MODEL,
            instructions=tutor_instructions(),
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
        public_question = None
        if not is_final and not adaptive_plan:
            if not isinstance(next_question, dict):
                raise KeyError("next_question")
            for required_key in ("concept", "prompt", "hint", "expected_answer"):
                if required_key not in next_question:
                    raise KeyError(f"next_question.{required_key}")
            normalize_question_concept(session, next_question)
            next_question["type"] = next_question_type
            next_question.setdefault("options", [])
        session["history"].append({
            "subject": question_subject,
            "concept": question["concept"],
            "difficulty": question["difficulty"],
            "score": score,
            "hints_used": hints_used,
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
        persistent_mastery = get_or_create_mastery(
            current_user.id, question_subject, str(question.get("concept", "General"))[:255]
        )
        mastery_before, mastery_update = apply_mastery_update(
            persistent_mastery, score, hints_used=hints_used
        )
        evaluation["skill_status"] = mastery_update["status"]
        attempt = Attempt(
            lesson=lesson_record,
            question=str(question.get("prompt", "")),
            subject=question_subject,
            concept=str(question.get("concept", "General"))[:255],
            student_answer=answer,
            score=score,
            feedback=str(evaluation.get("feedback", "")),
            difficulty=int(question.get("difficulty", 1)),
            hints_used=hints_used,
            mastery_before=mastery_before,
            mastery_after=mastery_update["mastery_score"],
        )
        db.session.add(attempt)
        if lesson_record.section_id:
            section = db.session.scalar(
                db.select(LearningSection).join(LearningProject).where(
                    LearningSection.id == lesson_record.section_id,
                    LearningProject.user_id == current_user.id,
                )
            )
            if not section:
                raise KeyError("section")
            update_section_mastery(section, completed=is_final)
        if session.get("session_kind") == "adaptive_practice":
            change_key = f"{question_subject}::{attempt.concept}"
            initial = session.get("initial_mastery", {}).get(change_key, mastery_before)
            session["mastery_changes"][change_key] = {
                "subject": question_subject,
                "concept": attempt.concept,
                "before": initial,
                "after": mastery_update["mastery_score"],
                "change": mastery_update["mastery_score"] - initial,
                "status": mastery_update["status"],
                "next_review_at": mastery_update["next_review_at"].date().isoformat(),
            }
        if not is_final:
            if adaptive_plan:
                if not planned_next_target:
                    raise KeyError("planned_concept")
                target_record = db.session.scalar(db.select(ConceptMastery).where(
                    ConceptMastery.id == planned_next_target["id"],
                    ConceptMastery.user_id == current_user.id,
                ))
                if not target_record:
                    raise KeyError("planned_concept")
                next_question = generate_adaptive_question(
                    session, mastery_state(target_record), next_question_number, next_question_type
                )
            else:
                next_subject = str(next_question.get("subject") or lesson_record.subject)[:80]
                next_mastery = get_or_create_mastery(
                    current_user.id, next_subject, str(next_question["concept"])[:255]
                )
                next_question["subject"] = next_subject
                next_question["difficulty"] = next_mastery.difficulty_level
            session["current_question"] = next_question
            public_question = {
                key: value for key, value in next_question.items() if key != "expected_answer"}
        save_session_state(payload.get("session_id"), commit=False)
        db.session.commit()
        average = round(
            sum(item["score"] for item in session["history"]) / len(session["history"]))
        mastery = mastery_snapshot(session)
        practice_results = adaptive_session_results(session) if is_final and adaptive_plan else None
        return jsonify(evaluation=evaluation, next_question=public_question,
                       complete=is_final, summary=result.get("summary") if is_final else None, progress={
            "answered": len(session["history"]),
            "total": session["test_total"],
            "average_score": average,
            "mastery": mastery,
            "weakest_concept": mastery[0]["concept"] if mastery else None,
        }, practice_results=practice_results)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        db.session.rollback()
        SESSIONS[payload.get("session_id")] = original_session
        return jsonify(error=f"The feedback could not be prepared reliably: {error}"), 422
    except Exception:
        db.session.rollback()
        SESSIONS[payload.get("session_id")] = original_session
        app.logger.exception("Answer evaluation failed")
        return jsonify(error=tr("The tutor service is temporarily unavailable.")), 500


@app.post("/api/chat")
@login_required
def tutor_chat():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
    message = str(payload.get("message", "")).strip()
    if not session:
        return jsonify(error=tr("This lesson expired. Upload the material again.")), 404
    if not message:
        return jsonify(error=tr("Write a message for your tutor.")), 400
    if len(message) > 4000:
        return jsonify(error="Keep tutor messages under 4,000 characters."), 400
    session["language"] = learning_content_language()

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
            instructions=(
                "You are a careful, friendly Socratic tutor across school subjects. "
                "Teach complex ideas in respectful baby steps without removing their real difficulty. "
                f"Factual accuracy and internal consistency are mandatory. {language_instruction()}"
            ),
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
        db.session.rollback()
        app.logger.exception("Tutor chat failed")
        return jsonify(error=tr("The tutor service is temporarily unavailable.")), 500


@app.post("/api/translate")
@login_required
def translate_content():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
    language = payload.get("language")
    texts = payload.get("texts", [])
    if not session:
        return jsonify(error=tr("This lesson expired. Upload the material again.")), 404
    if language not in {"English", "German"}:
        return jsonify(error=tr("Unsupported language.")), 400
    if not isinstance(texts, list) or not texts or len(texts) > 80:
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
        save_session_state(payload.get("session_id"))
        return jsonify(translations=translations)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        db.session.rollback()
        return jsonify(error=f"The content could not be translated reliably: {error}"), 422
    except Exception:
        db.session.rollback()
        app.logger.exception("Content translation failed")
        return jsonify(error="The translation service is temporarily unavailable."), 500


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
    )

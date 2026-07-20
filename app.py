import base64
import hashlib
import io
import json
import os
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from dotenv import load_dotenv
from flask import Flask, flash, has_request_context, jsonify, redirect, render_template, request, send_file, session as flask_session, url_for
from flask_login import UserMixin, current_user, login_required, login_user, logout_user
from flask_wtf.csrf import CSRFError
from sqlalchemy import Index, UniqueConstraint, func, inspect, or_, text
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

from learnova.quizzes.adaptive import (
    difficulty_label,
    prioritize_concepts,
    update_mastery,
)
from learnova.ocr.service import (
    crop_image_region,
    normalize_recognition,
    preprocess_document_image,
    render_pdf_page,
    recognition_instructions,
    validate_document_upload,
)
from learnova.projects.planning import (
    ALLOWED_QUESTION_TYPES,
    clean_extracted_pages,
    deterministic_question_score,
    difficulty_distribution,
    normalize_section,
    preparation_plan,
    proportional_section_counts,
)
from learnova.translations import SUPPORTED_LANGUAGES, frontend_catalog, translate
from learnova.config import configure_app
from learnova.extensions import csrf, db, limiter, login_manager
from learnova.ai_services import service as ai_service
from learnova.ai_services.prompts import TUTOR_RULES
from learnova.authentication.service import (
    AccountConflict,
    authenticate,
    create_user,
    identity_conflict,
    normalize_registration,
    validate_registration,
)
from learnova.uploads import create_project_from_uploads
from learnova.dashboard import dashboard_context, todays_practice_context
from learnova.study_planner import (
    adapt_future_schedule,
    build_plan_schedule,
    calendar_days,
    normalize_preferred_days,
    planner_metrics,
    redistribute_after_skip,
    redistribute_overdue_sessions,
    session_task_ids,
)
from learnova.web.responses import api_error
from learnova.web.security import apply_security_headers


load_dotenv()

app = Flask(__name__)
APP_ENV = configure_app(app)
db.init_app(app)
login_manager.init_app(app)
csrf.init_app(app)
limiter.init_app(app)


@limiter.request_filter
def disable_rate_limits_during_tests():
    return app.testing
login_manager.login_view = "login"  # pyright: ignore[reportAttributeAccessIssue]
login_manager.login_message = ""
login_manager.session_protection = "strong"

VISION_MODEL = app.config["GROQ_VISION_MODEL"]
TUTOR_MODEL = app.config["GROQ_TUTOR_MODEL"]
FAST_MODEL = app.config["GROQ_FAST_MODEL"]
LESSON_TOKEN_LIMIT = app.config["LESSON_TOKEN_LIMIT"]
ANSWER_TOKEN_LIMIT = app.config["ANSWER_TOKEN_LIMIT"]
CHAT_TOKEN_LIMIT = app.config["CHAT_TOKEN_LIMIT"]
TRANSLATE_TOKEN_LIMIT = app.config["TRANSLATE_TOKEN_LIMIT"]
PROJECT_TOKEN_LIMIT = app.config["PROJECT_TOKEN_LIMIT"]
GROQ_BASE_URL = app.config["GROQ_BASE_URL"]
ALLOWED_IMAGE_TYPES = ai_service.ALLOWED_IMAGE_TYPES
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
    study_plans = db.relationship("StudyPlan", back_populates="user", cascade="all, delete-orphan")

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
    __table_args__ = (
        Index("ix_attempt_lesson_timestamp", "lesson_id", "timestamp"),
        Index("ix_attempt_lesson_score", "lesson_id", "score"),
    )
    id = db.Column(db.Integer, primary_key=True)
    lesson_id = db.Column(db.Integer, db.ForeignKey("lesson.id"), nullable=False, index=True)
    question = db.Column(db.Text, nullable=False)
    subject = db.Column(db.String(80), nullable=True, index=True)
    concept = db.Column(db.String(255), nullable=False, index=True)
    concepts_json = db.Column(db.Text, nullable=False, default="[]")
    student_answer = db.Column(db.Text, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    feedback = db.Column(db.Text, nullable=False)
    difficulty = db.Column(db.Integer, nullable=False)
    timestamp = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    understood_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    hints_used = db.Column(db.Boolean, nullable=False, default=False)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    response_confidence = db.Column(db.Float, nullable=True)
    mastery_before = db.Column(db.Float, nullable=True)
    mastery_after = db.Column(db.Float, nullable=True)
    lesson = db.relationship("Lesson", back_populates="attempts")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class ConceptMastery(db.Model):
    __table_args__ = (
        UniqueConstraint("user_id", "subject", "concept", name="uq_user_subject_concept"),
        Index("ix_mastery_user_review", "user_id", "next_review_at"),
        Index("ix_mastery_user_score", "user_id", "mastery_score"),
    )
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
    recent_mistake_count = db.Column(db.Integer, nullable=False, default=0, index=True)
    confidence_trend = db.Column(db.Float, nullable=False, default=50)
    last_practised_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    next_review_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    difficulty_level = db.Column(db.Integer, nullable=False, default=1)
    status = db.Column(db.String(20), nullable=False, default="weak", index=True)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    user = db.relationship("User", back_populates="concept_masteries")
    history = db.relationship(
        "MasteryHistory", back_populates="mastery", cascade="all, delete-orphan"
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class MasteryHistory(db.Model):
    __table_args__ = (
        Index("ix_mastery_history_user_practised", "user_id", "practised_at"),
        Index("ix_mastery_history_mastery_practised", "mastery_id", "practised_at"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    mastery_id = db.Column(
        db.Integer, db.ForeignKey("concept_mastery.id"), nullable=False, index=True
    )
    attempt_id = db.Column(db.Integer, db.ForeignKey("attempt.id"), nullable=True, index=True)
    subject = db.Column(db.String(80), nullable=False, index=True)
    concept = db.Column(db.String(255), nullable=False, index=True)
    mastery_before = db.Column(db.Float, nullable=False)
    mastery_after = db.Column(db.Float, nullable=False)
    delta = db.Column(db.Float, nullable=False)
    score = db.Column(db.Integer, nullable=False)
    difficulty = db.Column(db.Integer, nullable=False)
    hints_used = db.Column(db.Boolean, nullable=False, default=False)
    retry_count = db.Column(db.Integer, nullable=False, default=0)
    response_confidence = db.Column(db.Float, nullable=False, default=50)
    confidence_before = db.Column(db.Float, nullable=False, default=50)
    confidence_after = db.Column(db.Float, nullable=False, default=50)
    outcome = db.Column(db.String(20), nullable=False, index=True)
    practised_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    mastery = db.relationship("ConceptMastery", back_populates="history")
    attempt = db.relationship("Attempt")

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
    __table_args__ = (Index("ix_project_user_updated", "user_id", "updated_at"),)
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
    study_plans = db.relationship("StudyPlan", back_populates="project", cascade="all, delete-orphan")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class StudyPlan(db.Model):
    """A student's exam target and scheduling preferences for one project."""

    __table_args__ = (
        Index("ix_study_plan_user_status_exam", "user_id", "status", "exam_date"),
        Index("ix_study_plan_project_status", "project_id", "status"),
    )
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    project_id = db.Column(
        db.Integer, db.ForeignKey("learning_project.id"), nullable=False, index=True
    )
    exam_date = db.Column(db.Date, nullable=False, index=True)
    target_grade = db.Column(db.String(40), nullable=False)
    daily_minutes = db.Column(db.Integer, nullable=False)
    preferred_days = db.Column(db.Text, nullable=False, default="[0,1,2,3,4,5,6]")
    difficulty_preference = db.Column(db.String(20), nullable=False, default="medium")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    status = db.Column(db.String(20), nullable=False, default="active", index=True)
    user = db.relationship("User", back_populates="study_plans")
    project = db.relationship("LearningProject", back_populates="study_plans")
    sessions = db.relationship(
        "StudyPlanSession", back_populates="study_plan", cascade="all, delete-orphan",
        order_by="StudyPlanSession.date",
    )

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class StudyPlanSession(db.Model):
    """One calendar day in a StudyPlan.

    The project already used ``StudySession`` for persisted lesson UI state, so
    this intentionally scoped name prevents corrupting existing saved lessons.
    """

    __tablename__ = "study_plan_session"
    __table_args__ = (
        UniqueConstraint("study_plan_id", "date", name="uq_study_plan_session_date"),
        Index("ix_study_plan_session_plan_status_date", "study_plan_id", "status", "date"),
    )
    id = db.Column(db.Integer, primary_key=True)
    study_plan_id = db.Column(
        db.Integer, db.ForeignKey("study_plan.id"), nullable=False, index=True
    )
    date = db.Column(db.Date, nullable=False, index=True)
    planned_minutes = db.Column(db.Integer, nullable=False, default=0)
    completed_minutes = db.Column(db.Integer, nullable=False, default=0)
    lesson_ids = db.Column(db.Text, nullable=False, default="[]")
    quiz_ids = db.Column(db.Text, nullable=False, default="[]")
    review_ids = db.Column(db.Text, nullable=False, default="[]")
    exam_ids = db.Column(db.Text, nullable=False, default="[]")
    tasks_json = db.Column(db.Text, nullable=False, default="[]")
    status = db.Column(db.String(20), nullable=False, default="planned", index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    study_plan = db.relationship("StudyPlan", back_populates="sessions")

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
    __table_args__ = (Index("ix_project_page_project_order", "project_id", "page_order"),)
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
    __table_args__ = (Index("ix_document_block_page_order", "page_id", "block_order"),)
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
    __table_args__ = (Index("ix_section_project_position", "project_id", "position"),)
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
    concepts_json = db.Column(db.Text, nullable=False, default="[]")
    answer = db.Column(db.Text, nullable=False)
    source_text = db.Column(db.Text, nullable=False, default="")
    attempts = db.Column(db.Integer, nullable=False, default=0)
    correct_attempts = db.Column(db.Integer, nullable=False, default=0)
    section = db.relationship("LearningSection", back_populates="recall_cards")

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)  # pyright: ignore[reportCallIssue]


class FinalExam(db.Model):
    __table_args__ = (Index("ix_exam_project_status", "project_id", "status"),)
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
    __table_args__ = (Index("ix_exam_question_exam_position", "exam_id", "position"),)
    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("final_exam.id"), nullable=False, index=True)
    section_id = db.Column(db.Integer, db.ForeignKey("learning_section.id"), nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False)
    difficulty = db.Column(db.String(20), nullable=False, index=True)
    question_type = db.Column(db.String(40), nullable=False)
    prompt = db.Column(db.Text, nullable=False)
    concepts_json = db.Column(db.Text, nullable=False, default="[]")
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

        def add_query_path_indexes():
            indexes = (
                "CREATE INDEX IF NOT EXISTS ix_attempt_lesson_timestamp ON attempt (lesson_id, timestamp)",
                "CREATE INDEX IF NOT EXISTS ix_attempt_lesson_score ON attempt (lesson_id, score)",
                "CREATE INDEX IF NOT EXISTS ix_mastery_user_review ON concept_mastery (user_id, next_review_at)",
                "CREATE INDEX IF NOT EXISTS ix_mastery_user_score ON concept_mastery (user_id, mastery_score)",
                "CREATE INDEX IF NOT EXISTS ix_project_user_updated ON learning_project (user_id, updated_at)",
                "CREATE INDEX IF NOT EXISTS ix_project_page_project_order ON project_page (project_id, page_order)",
                "CREATE INDEX IF NOT EXISTS ix_document_block_page_order ON document_block (page_id, block_order)",
                "CREATE INDEX IF NOT EXISTS ix_section_project_position ON learning_section (project_id, position)",
                "CREATE INDEX IF NOT EXISTS ix_exam_project_status ON final_exam (project_id, status)",
                "CREATE INDEX IF NOT EXISTS ix_exam_question_exam_position ON exam_question (exam_id, position)",
            )
            with db.engine.begin() as connection:
                for statement in indexes:
                    connection.execute(text(statement))

        apply_schema_migration("009_add_query_path_indexes", add_query_path_indexes)

        concept_tracking_tables = {
            "attempt": {
                "concepts_json": "TEXT NOT NULL DEFAULT '[]'",
                "retry_count": "INTEGER NOT NULL DEFAULT 0",
                "response_confidence": "FLOAT",
            },
            "concept_mastery": {
                "recent_mistake_count": "INTEGER NOT NULL DEFAULT 0",
                "confidence_trend": "FLOAT NOT NULL DEFAULT 50",
            },
            "recall_card": {
                "concepts_json": "TEXT NOT NULL DEFAULT '[]'",
            },
            "exam_question": {
                "concepts_json": "TEXT NOT NULL DEFAULT '[]'",
            },
        }
        missing_concept_tracking = {}
        for table_name, definitions in concept_tracking_tables.items():
            existing = {
                column["name"] for column in inspect(db.engine).get_columns(table_name)
            }
            missing_concept_tracking[table_name] = {
                name: definition for name, definition in definitions.items()
                if name not in existing
            }

        if any(missing_concept_tracking.values()):
            def add_concept_level_tracking():
                with db.engine.begin() as connection:
                    for table_name, definitions in missing_concept_tracking.items():
                        for name, definition in definitions.items():
                            connection.execute(text(
                                f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"
                            ))
                    connection.execute(text(
                        "CREATE INDEX IF NOT EXISTS ix_concept_mastery_recent_mistake_count "
                        "ON concept_mastery (recent_mistake_count)"
                    ))
            apply_schema_migration("010_add_concept_level_tracking", add_concept_level_tracking)

        def add_study_planner_indexes():
            statements = (
                "CREATE INDEX IF NOT EXISTS ix_study_plan_user_status_exam "
                "ON study_plan (user_id, status, exam_date)",
                "CREATE INDEX IF NOT EXISTS ix_study_plan_project_status "
                "ON study_plan (project_id, status)",
                "CREATE INDEX IF NOT EXISTS ix_study_plan_session_plan_status_date "
                "ON study_plan_session (study_plan_id, status, date)",
            )
            with db.engine.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement))

        apply_schema_migration("011_add_intelligent_study_planner", add_study_planner_indexes)


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


# Subjects whose formulas should be typeset with LaTeX in the tutor UI.
LATEX_SUBJECTS = {"mathematics", "physics"}
# Subjects that are themselves a language, mapped to the language their content stays in.
LANGUAGE_SUBJECTS = {"english": "English", "german": "German", "deutsch": "German"}


def subject_teaching_instruction(subject: str | None) -> str:
    """Extra, subject-specific tutoring rules layered on top of the shared rules."""

    key = str(subject or "").strip().casefold()
    parts: list[str] = []
    target_language = LANGUAGE_SUBJECTS.get(key)
    learner_language = learning_content_language()
    if target_language and target_language != learner_language:
        parts.append(
            f"This is a language lesson teaching {target_language} to a {learner_language}-speaking student. "
            f"This overrides any instruction to write everything in {learner_language}: write only the "
            f"explanations, grammar notes, instructions, question prompts, and hints in {learner_language}. "
            f"Keep every {target_language} word, example sentence, phrase, and quotation in {target_language}, "
            f"and add its {learner_language} meaning in parentheses right after it. Never translate the "
            f"{target_language} material the student is meant to practise."
        )
    if key in LATEX_SUBJECTS:
        parts.append(
            "MATH FORMATTING IS MANDATORY. You MUST write every formula, equation, fraction, root, and numeric "
            "step in LaTeX, never as plain text. Put each standalone formula or solution step on its own line as "
            r"display math wrapped in $$...$$, and use \(...\) only for a single symbol inside a sentence. "
            r"Use \frac{...}{...} for fractions, \sqrt{...} for roots, ^{} for powers and _{} for subscripts. "
            "Give each transformation its own $$...$$ line; never chain several = steps on one line.\n"
            r"CORRECT: $$x_1 = \frac{-b + \sqrt{D}}{2a}$$ then $$x_1 = \frac{4 + 8}{4}$$ then $$x_1 = \frac{12}{4} = 3$$" + "\n"
            r"WRONG, never do this: x_1=(-b+√D)/(2a)=(4+8)/(4)=12/4=3" + "\n"
            "Never use Unicode math symbols (such as √, ², ×, ÷, subscript digits) or a slash "
            "for a fraction. Write backslash LaTeX commands normally (\\frac, \\sqrt); the system handles JSON escaping."
        )
    return "\n".join(parts)


def tutor_instructions(subject: str | None = None) -> str:
    base = f"{TUTOR_RULES}\n\n{language_instruction()}"
    extra = subject_teaching_instruction(subject)
    return f"{base}\n\n{extra}" if extra else base


def tr(message: str, **values) -> str:
    return translate(message, get_current_language(), **values)


def planner_task_title(task: dict[str, Any]) -> str:
    """Localize a stored planner task without storing translated database text."""

    kind = task.get("kind")
    if kind == "learn":
        return tr("Learn {section}", section=task.get("section_title") or tr("Learning section"))
    if kind == "quiz":
        return tr("Quiz: {section}", section=task.get("section_title") or tr("Learning section"))
    if kind in {"review", "retention"}:
        return tr("Review {concept}", concept=task.get("concept") or tr("saved concepts"))
    if kind == "mistakes":
        return tr("Practice mistakes: {concept}", concept=task.get("concept") or tr("recent mistakes"))
    if kind == "mock_exam":
        return tr("Mock exam")
    return tr("Study activity")


def planner_date(value: date) -> str:
    return tr(
        "{weekday}, {day} {month} {year}", weekday=tr(value.strftime("%A")),
        day=value.day, month=tr(value.strftime("%B")), year=value.year,
    )


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
    ai_mode = app.config.get("AI_MODE", "mock")
    return {
        "_": lambda message, **values: translate(message, language, **values),
        "current_language": language,
        "learning_content_language_code": "de-DE" if learning_content_language() == "German" else "en-US",
        "frontend_translations": frontend_catalog(language),
        "ai_mode_badge": (
            {"mock": "Mock AI", "cached": "Cached AI", "live": "Live AI"}.get(ai_mode)
            if app.config.get("ENV_NAME") == "development" else None
        ),
        "ai_mode": ai_mode,
        "planner_task_title": planner_task_title,
        "planner_date": planner_date,
    }


@login_manager.unauthorized_handler
def unauthorized():
    flash(tr("Please log in to use your tutor."), "error")
    return redirect(url_for("login", next=request.path))


@app.after_request
def security_headers(response):
    return apply_security_headers(response)


@app.errorhandler(413)
def request_too_large(_error):
    message = tr("The upload is too large. Keep the complete request under 40 MB.")
    if request.path.startswith("/api/"):
        return api_error(message, 413, "upload_too_large")
    flash(message, "error")
    return redirect(request.referrer or url_for("index"))


@app.errorhandler(SQLAlchemyError)
def database_error(_error):
    db.session.rollback()
    app.logger.exception("Database operation failed")
    if request.path.startswith("/api/"):
        return api_error(tr("The database is temporarily unavailable."), 503, "database_unavailable")
    flash(tr("The database is temporarily unavailable."), "error")
    return redirect(url_for("dashboard") if current_user.is_authenticated else url_for("login"))


@app.errorhandler(CSRFError)
def csrf_error(_error):
    message = tr("Your form expired. Refresh the page and try again.")
    if request.path.startswith("/api/") or request.is_json:
        return api_error(message, 400, "csrf_failed")
    flash(message, "error")
    return redirect(request.referrer or url_for("index" if current_user.is_authenticated else "login"))


@app.errorhandler(429)
def rate_limit_error(_error):
    message = tr("Too many requests. Please wait a moment and try again.")
    if request.path.startswith("/api/") or request.is_json:
        return api_error(message, 429, "rate_limited")
    flash(message, "error")
    return redirect(request.referrer or url_for("index" if current_user.is_authenticated else "login"))


def create_response(*, task_type: str, language: str | None = None, **kwargs: Any):
    private_scope = None
    session_scope = kwargs.pop("session_scope", None)
    if has_request_context() and current_user.is_authenticated:
        private_scope = current_user.get_id()
        if session_scope is None and request.is_json:
            session_scope = (request.get_json(silent=True) or {}).get("session_id")
    return ai_service.create_response(
        task_type=task_type,
        language=language or learning_content_language(),
        private_scope=private_scope,
        session_scope=session_scope,
        **kwargs,
    )


def ai_failure_message(error: Exception) -> tuple[str, int, str]:
    """Return a translated, non-sensitive failure suitable for a student response."""

    category, _summary = ai_service._failure_details(error)
    messages = {
        "schema_validation": ("The AI response could not be validated. You can retry. Your saved work remains safe.", 422, "invalid_ai_output"),
        "invalid_json": ("The AI response could not be validated. You can retry. Your saved work remains safe.", 422, "invalid_ai_output"),
        "source_reference_validation": ("The AI response could not be validated against your material. You can retry. Your saved work remains safe.", 422, "invalid_ai_output"),
        "request_limit_reached": ("Your AI request limit has been reached. Try again later or use your saved content.", 429, "ai_limit_reached"),
        "token_limit_exceeded": ("The uploaded material is too large for one AI request. Split it into smaller sections and try again. Your saved work remains safe.", 413, "ai_input_too_large"),
        "provider_timeout": ("Generation timed out. You can retry. Your saved work remains safe.", 504, "ai_timeout"),
    }
    message, status, code = messages.get(category, (
        "AI is temporarily unavailable. You can retry. Your saved work remains safe.",
        503, "ai_unavailable",
    ))
    return tr(message), status, code


def flash_ai_failure(error: Exception) -> None:
    message, _status, _code = ai_failure_message(error)
    flash(message, "error")


@app.get("/internal/ai-diagnostics")
@login_required
def ai_diagnostics():
    allowed = app.config.get("AI_DIAGNOSTICS_ADMINS", set())
    identities = {current_user.username.casefold(), current_user.email.casefold()}
    if app.config.get("ENV_NAME") != "development" or not identities.intersection(allowed):
        return "Not found", 404
    return render_template("ai_diagnostics.html", diagnostics=ai_service.diagnostics_summary())


def quality_options() -> dict[str, Any]:
    return ai_service.quality_options(TUTOR_MODEL)


def parse_json(text):
    return ai_service.parse_json(text)


def image_data_url(upload):
    return ai_service.image_data_url(upload)


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
    """Canonicalize one-or-more concepts and retain the legacy primary concept."""
    names = list(session["mastery"])
    requested_values = question.get("concepts", [])
    if not isinstance(requested_values, list):
        requested_values = []
    selected = []
    for value in [question.get("concept"), *requested_values]:
        requested = str(value or "").strip()
        if not requested:
            continue
        canonical = next(
            (name for name in names if name.casefold() == requested.casefold()), requested
        )
        if canonical.casefold() not in {item.casefold() for item in selected}:
            selected.append(canonical[:255])
        if len(selected) == 3:
            break
    if not selected:
        weakest = mastery_snapshot(session)
        selected = [weakest[0]["concept"] if weakest else (
            names[0] if names else session.get("subject", "General studies"))]
    question["concepts"] = selected
    question["concept"] = selected[0]


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
        "recent_mistake_count": record.recent_mistake_count,
        "confidence_trend": record.confidence_trend,
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
            recent_mistake_count=0,
            confidence_trend=50,
            difficulty_level=1,
            status="weak",
        )
        db.session.add(record)
        db.session.flush()
    return record


def apply_mastery_update(
    record,
    score,
    hints_used=False,
    practised_at=None,
    *,
    difficulty=None,
    retry_count=0,
    response_confidence=None,
):
    before = float(record.mastery_score or 0)
    updated = update_mastery(
        mastery_state(record),
        score,
        hints_used=hints_used,
        practised_at=practised_at,
        difficulty=difficulty,
        retry_count=retry_count,
        response_confidence=response_confidence,
    )
    for field in (
        "mastery_score", "attempts", "correct_attempts", "incorrect_attempts",
        "consecutive_correct", "consecutive_incorrect", "last_practised_at",
        "next_review_at", "difficulty_level", "status", "recent_mistake_count",
        "confidence_trend",
    ):
        setattr(record, field, updated[field])
    record.total_score = int(record.total_score or 0) + int(score)
    record.updated_at = updated["last_practised_at"]
    return before, updated


def saved_concepts(value, fallback):
    parsed = json_value(value, [])
    values = parsed if isinstance(parsed, list) else []
    concepts = []
    for item in [*values, fallback]:
        name = str(item or "").strip()[:255]
        if name and name.casefold() not in {value.casefold() for value in concepts}:
            concepts.append(name)
    return concepts or ["General"]


def add_mastery_history(
    record,
    before,
    updated,
    *,
    score,
    difficulty,
    hints_used=False,
    retry_count=0,
    response_confidence: float = 50.0,
    attempt=None,
):
    previous_confidence = float(record.confidence_trend or 50)
    if updated.get("confidence_trend") is not None:
        previous_confidence = round(
            (float(updated["confidence_trend"]) - float(response_confidence) * 0.3) / 0.7,
            2,
        )
    db.session.add(MasteryHistory(
        user_id=record.user_id,
        mastery_id=record.id,
        attempt_id=attempt.id if attempt else None,
        subject=record.subject,
        concept=record.concept,
        mastery_before=before,
        mastery_after=updated["mastery_score"],
        delta=updated["delta"],
        score=int(score),
        difficulty=int(difficulty),
        hints_used=bool(hints_used),
        retry_count=int(retry_count),
        response_confidence=float(response_confidence),
        confidence_before=previous_confidence,
        confidence_after=updated["confidence_trend"],
        outcome=updated["outcome"],
        practised_at=updated["last_practised_at"],
    ))


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
            task_type="ocr_document_recognition",
            language=learning_content_language(),
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
            if isinstance(error, (ai_service.AIGatewayError, ai_service.AIValidationError)):
                saved_page.warning = ai_failure_message(error)[0]
            else:
                saved_page.warning = "Recognition failed safely. Retry is available."
            saved_project = db.session.get(LearningProject, saved_page.project_id)
            if saved_project:
                saved_project.status = "reviewing"
            db.session.commit()
        app.logger.exception("Recognition failed for project %s page %s", project.id, page_id)
        if isinstance(error, (ai_service.AIGatewayError, ai_service.AIValidationError)):
            return False, ai_failure_message(error)[0]
        return False, "Recognition failed safely. Retry is available."


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
    response = create_response(
        task_type="adaptive_practice",
        language=session["language"],
        fixture_context={
            "subject": target["subject"], "concept": target["concept"],
            "question_number": question_number,
        },
        validation_context={"recent_questions": recent_questions},
        model=TUTOR_MODEL, instructions=tutor_instructions(target["subject"]), input=prompt,
        max_output_tokens=ANSWER_TOKEN_LIMIT, temperature=0.15,
        **quality_options(),
    )
    result = parse_json(response.output_text)
    question = result.get("question") or result.get("next_question")
    if not isinstance(question, dict):
        raise KeyError("question")
    for key in ("prompt", "hint", "expected_answer"):
        if key not in question:
            raise KeyError(f"question.{key}")
    question.update({
        "id": f"q{question_number}",
        "subject": target["subject"],
        "concept": target["concept"],
        "concepts": [target["concept"]],
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


@app.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        registration = normalize_registration(
            request.form.get("username", ""),
            request.form.get("email", ""),
            request.form.get("password", ""),
            request.form.get("language", get_current_language()),
        )
        validation_error = validate_registration(registration, SUPPORTED_LANGUAGES)
        conflict = identity_conflict(db, User, registration) if validation_error is None else None
        if validation_error == "username":
            flash(tr("Username must be 3–30 characters using letters, numbers, dots, hyphens, or underscores."), "error")
        elif validation_error == "language":
            flash(tr("Unsupported language."), "error")
        elif validation_error == "email":
            flash(tr("Enter a valid email address."), "error")
        elif validation_error == "password":
            flash(tr("Use a password with 8–256 characters."), "error")
        elif conflict == "username_taken":
            flash(tr("That username is already registered."), "error")
        elif conflict == "email_taken":
            flash(tr("An account with that email already exists."), "error")
        else:
            try:
                user = create_user(db, User, registration)
                login_user(user)
                flask_session["language"] = registration.language
                return redirect(url_for("index"))
            except AccountConflict:
                flash(tr("That username or email is already registered."), "error")
            except SQLAlchemyError:
                db.session.rollback()
                app.logger.exception("Account registration failed")
                flash(tr("Your account could not be created right now."), "error")
    return render_template("auth.html", mode="register")


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute", methods=["POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        user = authenticate(
            db,
            User,
            request.form.get("identifier") or request.form.get("email", ""),
            request.form.get("password", ""),
        )
        if not user:
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
            "subject": session.get("subject", "Other"),
            "lesson": {key: value for key, value in session["lesson"].items() if key != "question"},
            "question": {key: value for key, value in session["current_question"].items() if key != "expected_answer"},
        }
    return render_template("index.html", bootstrap=bootstrap)


@app.get("/health")
def health():
    return jsonify(ok=True, status="ok")


@app.route("/projects", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
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
            try:
                project = create_project_from_uploads(
                    db,
                    LearningProject,
                    ProjectFile,
                    ProjectPage,
                    user_id=current_user.id,
                    title=title,
                    subject=subject,
                    exam_date=exam_date,
                    uploads=uploads,
                )
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
@limiter.limit("10 per hour")
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
        return api_error("Page not found", 404, "not_found")
    if page.excluded:
        return api_error("Excluded pages are not recognized", 400, "page_excluded")
    success, error = recognize_single_project_page(page, project)
    if not success:
        return jsonify(ok=False, status="failed", error=error, code="recognition_failed", page_id=page_id), 422
    return jsonify(ok=True,
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
        return api_error("Project not found", 404, "not_found")
    order = (request.get_json(silent=True) or {}).get("page_ids", [])
    owned_ids = {page.id for page in project.pages}
    if not isinstance(order, list) or set(order) != owned_ids:
        return api_error("Page order must contain every project page exactly once.", 400, "invalid_page_order")
    lookup = {page.id: page for page in project.pages}
    for position, page_id in enumerate(order, start=1):
        lookup[page_id].page_order = position
    project.updated_at = utcnow()
    db.session.commit()
    return jsonify(ok=True, status="ok")


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
    valid_page_ids = {page.id for page in readable_pages}
    try:
        response = create_response(
            task_type="project_section_generation",
            language=learning_content_language(),
            fixture_context={
                "source_page_ids": sorted(valid_page_ids),
                "section_count": 1,
            },
            model=TUTOR_MODEL, instructions=tutor_instructions(), input=prompt,
            max_output_tokens=PROJECT_TOKEN_LIMIT, temperature=0.1, **quality_options(),
        )
        result = parse_json(response.output_text)
        raw_sections = result["sections"]
        if not isinstance(raw_sections, list) or not raw_sections:
            raise ValueError("No learning sections were returned")
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
                    concepts_json=json.dumps(
                        [section.main_topic or section.title], ensure_ascii=False
                    ),
                    source_text=supporting,
                ))
        project.status = "planned"
        project.updated_at = utcnow()
        db.session.commit()
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        flash_ai_failure(error)
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
        return api_error("Project not found", 404, "not_found")
    order = (request.get_json(silent=True) or {}).get("section_ids", [])
    owned_ids = {section.id for section in project.sections}
    if not isinstance(order, list) or set(order) != owned_ids:
        return api_error("Section order must contain every section exactly once.", 400, "invalid_section_order")
    lookup = {section.id: section for section in project.sections}
    for position, section_id in enumerate(order, start=1):
        lookup[section_id].position = position
    db.session.commit()
    return jsonify(ok=True, status="ok")


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
            task_type="project_section_generation",
            language=learning_content_language(),
            fixture_context={
                "source_page_ids": json_value(section.source_page_ids_json),
                "section_count": 2,
                "exact_section_count": True,
                "operation": "split",
            },
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
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        flash_ai_failure(error)
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
        return api_error("Recall card not found", 404, "not_found")
    answer = request.form.get("answer", "").strip()
    correct = answer.casefold() == card.answer.strip().casefold()
    try:
        response_confidence = float(request.form.get("response_confidence", 50))
    except (TypeError, ValueError):
        return api_error("Confidence must be a number", 400, "invalid_confidence")
    if not 0 <= response_confidence <= 100:
        return api_error("Confidence must be between 0 and 100", 400, "invalid_confidence")
    retry_count = max(0, card.attempts)
    concepts = saved_concepts(card.concepts_json, section.main_topic or section.title)
    card.concepts_json = json.dumps(concepts, ensure_ascii=False)
    card.attempts += 1
    if correct:
        card.correct_attempts += 1
    score = 100 if correct else 0
    mastery_changes = []
    for concept_name in concepts:
        mastery = get_or_create_mastery(
            section.project.user_id, section.project.subject, concept_name
        )
        before, updated = apply_mastery_update(
            mastery,
            score,
            difficulty=1,
            retry_count=retry_count,
            response_confidence=response_confidence,
        )
        add_mastery_history(
            mastery,
            before,
            updated,
            score=score,
            difficulty=1,
            retry_count=retry_count,
            response_confidence=response_confidence,
        )
        mastery_changes.append({
            "concept": concept_name,
            "before": before,
            "after": updated["mastery_score"],
        })
    record_planner_activity(
        section.project.user_id, activity_kind="review", score=score,
        subject=section.project.subject, concepts=concepts, section_id=section.id,
    )
    db.session.commit()
    return jsonify(
        ok=True,
        correct=correct,
        answer=card.answer,
        source_text=card.source_text,
        mastery_changes=mastery_changes,
    )


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
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        flash_ai_failure(error)
        return redirect(url_for("learn_section", project_id=project_id, section_id=section_id))
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
            task_type="final_exam_evaluation",
            language=learning_content_language(),
            fixture_context={"question_ids": [item["question_id"] for item in open_items]},
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
        difficulty = {"easy": 1, "medium": 2, "hard": 3}.get(question.difficulty, 2)
        score = round(float(answer.score or 0))
        concepts = saved_concepts(
            question.concepts_json,
            (section.main_topic or section.title) if section else exam.project.subject,
        )[:3]
        question.concepts_json = json.dumps(concepts, ensure_ascii=False)
        mastery_updates = []
        for concept_name in concepts:
            mastery = get_or_create_mastery(
                exam.project.user_id, exam.project.subject, concept_name
            )
            before, updated = apply_mastery_update(
                mastery,
                score,
                difficulty=difficulty,
                response_confidence=50,
            )
            mastery_updates.append((mastery, before, updated))
        _primary, mastery_before, mastery_update = mastery_updates[0]
        attempt = Attempt(
            lesson_id=mistake_lesson.id,
            subject=exam.project.subject,
            question=question.prompt,
            concept=concepts[0],
            concepts_json=json.dumps(concepts, ensure_ascii=False),
            student_answer=answer.answer_text or "(unanswered)",
            score=score,
            feedback=answer.evaluation or question.explanation,
            difficulty=difficulty,
            hints_used=False,
            retry_count=0,
            response_confidence=50,
            mastery_before=mastery_before,
            mastery_after=mastery_update["mastery_score"],
        )
        db.session.add(attempt)
        db.session.flush()
        for mastery, before, updated in mastery_updates:
            add_mastery_history(
                mastery,
                before,
                updated,
                score=score,
                difficulty=difficulty,
                response_confidence=50,
                attempt=attempt,
            )
        if section:
            section_score = result["section_scores"][str(section.id)]
            section.mastery_score = round((section.mastery_score + section_score) / 2, 2)
            section.status = section_status_from_score(section.mastery_score, completed=True)
    record_planner_activity(
        exam.project.user_id, activity_kind="mock_exam", score=exam.score,
        subject=exam.project.subject,
        concepts=[value for question in questions for value in saved_concepts(
            question.concepts_json, exam.project.subject
        )],
        exam_id=exam.id,
    )
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
Return JSON as {{"questions":[{{"id":"q1","section_id":1,"concepts":["specific concept"],"source_page_ids":[1],"supporting_text":"exact excerpt supporting the answer","difficulty":"easy|medium|hard","question_type":"multiple_choice|true_false|matching|fill_blank|short_answer|explanation|calculation","prompt":"...","options":[],"expected_answer":"...","explanation":"source-grounded explanation shown only after submission"}}]}}.
Return exactly {count} questions and follow each section's question_count proportionally. Easy tests direct recall, medium tests connections/application, hard tests synthesis or unfamiliar application. Hard means deeper reasoning, not confusing wording. Every answer must be supported by supporting_text and valid source_page_ids."""
        try:
            response = create_response(
                task_type="final_exam_generation",
                language=learning_content_language(),
                fixture_context={
                    "question_count": count,
                    "section_ids": [item.id for item in included],
                    "section_allocation": {str(key): value for key, value in allocation.items()},
                    "difficulty_distribution": distribution,
                    "question_types": selected_types,
                    "source_page_ids": {
                        str(item.id): json_value(item.source_page_ids_json) for item in included
                    },
                    "supporting_text": {
                        str(item.id): section_source_text(item)[:300] for item in included
                    },
                },
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
                raw_concepts = item.get("concepts", [])
                if not isinstance(raw_concepts, list):
                    raw_concepts = []
                question_concepts = saved_concepts(
                    json.dumps(raw_concepts, ensure_ascii=False),
                    section.main_topic or section.title,
                )[:3]
                db.session.add(ExamQuestion(
                    exam_id=exam.id, section_id=section_id, position=position,
                    difficulty=difficulty, question_type=question_type,
                    prompt=str(item["prompt"]),
                    concepts_json=json.dumps(question_concepts, ensure_ascii=False),
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
        except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
            db.session.rollback()
            flash_ai_failure(error)
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
        return api_error("Exam not found", 404, "not_found")
    if exam.status == "submitted":
        return jsonify(ok=True, status="submitted"), 409
    if utcnow() >= as_utc(exam.expires_at):
        try:
            submit_exam_record(exam)
        except Exception:
            db.session.rollback()
            app.logger.exception("Automatic exam submission failed during autosave")
            return api_error("Your saved answers are safe, but evaluation is temporarily unavailable.", 503, "evaluation_unavailable")
        return jsonify(ok=True, status="submitted", redirect=url_for("final_exam_results", exam_id=exam.id)), 409
    payload = request.get_json(silent=True) or {}
    question_id = payload.get("question_id")
    question = db.session.scalar(db.select(ExamQuestion).where(
        ExamQuestion.id == question_id, ExamQuestion.exam_id == exam.id
    ))
    if not question:
        return api_error("Question not found", 404, "not_found")
    save_exam_answer(exam, question, payload.get("answer", ""))
    db.session.commit()
    return jsonify(ok=True, status="saved", saved_at=utcnow().isoformat())


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


def owned_study_plan(plan_id):
    return db.session.scalar(
        db.select(StudyPlan).join(LearningProject).where(
            StudyPlan.id == plan_id,
            StudyPlan.user_id == current_user.id,
            LearningProject.user_id == current_user.id,
        )
    )


def owned_plan_session(plan_id, session_id):
    return db.session.scalar(
        db.select(StudyPlanSession).join(StudyPlan).join(LearningProject).where(
            StudyPlanSession.id == session_id,
            StudyPlanSession.study_plan_id == plan_id,
            StudyPlan.user_id == current_user.id,
            LearningProject.user_id == current_user.id,
        )
    )


def _planner_masteries(project):
    return db.session.scalars(
        db.select(ConceptMastery).where(
            ConceptMastery.user_id == project.user_id,
            ConceptMastery.subject == project.subject,
        ).order_by(ConceptMastery.mastery_score, ConceptMastery.next_review_at)
    ).all()


def _planner_mistakes(project, limit=50):
    return db.session.scalars(
        db.select(Attempt).join(Lesson).where(
            Lesson.user_id == project.user_id,
            func.coalesce(Attempt.subject, Lesson.subject) == project.subject,
            Attempt.score < 80,
        ).order_by(Attempt.timestamp.desc()).limit(limit)
    ).all()


def _mastery_payload(item):
    return {
        "id": item.id, "subject": item.subject, "concept": item.concept,
        "mastery_score": item.mastery_score,
        "recent_mistake_count": item.recent_mistake_count,
        "next_review_at": item.next_review_at,
        "last_practised_at": item.last_practised_at,
    }


def _section_payload(item):
    return {
        "id": item.id, "position": item.position, "title": item.title,
        "estimated_minutes": item.estimated_minutes, "status": item.status,
        "mastery_score": item.mastery_score, "excluded": item.excluded,
    }


def _plan_session_payload(item):
    return {
        "id": item.id, "date": item.date, "planned_minutes": item.planned_minutes,
        "completed_minutes": item.completed_minutes, "status": item.status,
        "tasks": json_value(item.tasks_json),
    }


def _save_planner_tasks(session_record, tasks):
    task_ids = session_task_ids(tasks)
    session_record.tasks_json = json.dumps(tasks, ensure_ascii=False)
    session_record.planned_minutes = sum(int(item.get("minutes") or 0) for item in tasks)
    for name, values in task_ids.items():
        setattr(session_record, name, json.dumps(values))
    session_record.updated_at = utcnow()


def _planner_context(plan, today=None):
    today = today or date.today()
    raw_rows = [_plan_session_payload(item) for item in plan.sessions]
    missed_dates, redistributed = redistribute_overdue_sessions(
        raw_rows, today=today, daily_minutes=plan.daily_minutes
    )
    if missed_dates:
        for item in plan.sessions:
            if item.date in missed_dates:
                item.status = "skipped"
                item.completed_minutes = 0
            elif item.date >= today and item.status != "completed":
                _save_planner_tasks(item, redistributed.get(item.date, []))
        plan.updated_at = utcnow()
        db.session.commit()
    session_rows = [_plan_session_payload(item) for item in plan.sessions]
    masteries = _planner_masteries(plan.project)
    mastery_rows = [_mastery_payload(item) for item in masteries]
    section_rows = [_section_payload(item) for item in plan.project.sections]
    metrics = planner_metrics(
        exam_date=plan.exam_date, sessions=session_rows, masteries=mastery_rows,
        sections=section_rows, today=today,
    )
    today_session = next((item for item in plan.sessions if item.date == today), None)
    next_review = next(
        (item for item in masteries if item.next_review_at and item.next_review_at.date() >= today),
        None,
    )
    reminders = []
    if today_session and today_session.status == "planned":
        reminders.append(tr("Today's study session is ready."))
    overdue = sum(
        1 for item in masteries
        if item.next_review_at is None or item.next_review_at.date() < today
    )
    if overdue:
        reminders.append(tr("You have {count} overdue reviews.", count=overdue))
    if metrics["countdown"] <= 3:
        reminders.append(tr("Your exam is in {count} days.", count=metrics["countdown"]))
    recently_mastered = next((item for item in masteries if item.status == "mastered"), None)
    if recently_mastered:
        reminders.append(tr("You mastered {concept}.", concept=recently_mastered.concept))
    return {
        "plan": plan, "planner_sessions": plan.sessions, "today_session": today_session,
        "weakest_concepts": masteries[:3], "next_review": next_review,
        "planner_metrics": metrics, "planner_reminders": reminders,
    }


def _active_plan_for_user(user_id, subject=None):
    query = db.select(StudyPlan).join(LearningProject).where(
        StudyPlan.user_id == user_id,
        LearningProject.user_id == user_id,
        StudyPlan.status == "active",
        StudyPlan.exam_date >= date.today(),
    )
    if subject:
        query = query.where(LearningProject.subject == subject)
    return db.session.scalar(query.order_by(StudyPlan.exam_date, StudyPlan.updated_at.desc()))


def record_planner_activity(
    user_id, *, activity_kind, score, subject, concepts=(), section_id=None, exam_id=None
):
    """Record matching work and incrementally rebalance only future plan days."""

    plan = _active_plan_for_user(user_id, subject)
    if not plan:
        return
    today = date.today()
    today_record = next((item for item in plan.sessions if item.date == today), None)
    if today_record:
        tasks = json_value(today_record.tasks_json)
        completed_one = False
        for task in tasks:
            kind_matches = task.get("kind") == activity_kind or (
                activity_kind == "quiz" and task.get("kind") in {"quiz", "review"}
            )
            reference_matches = (
                section_id is None or task.get("section_id") in {None, section_id}
            ) and (exam_id is None or task.get("exam_id") in {None, exam_id})
            if kind_matches and reference_matches and not task.get("completed"):
                task["completed"] = True
                completed_one = True
                break
        if completed_one:
            today_record.completed_minutes = sum(
                int(item.get("minutes") or 0) for item in tasks if item.get("completed")
            )
            if tasks and all(item.get("completed") for item in tasks):
                today_record.status = "completed"
            _save_planner_tasks(today_record, tasks)
    session_rows = [_plan_session_payload(item) for item in plan.sessions]
    updates = adapt_future_schedule(
        session_rows, today=today, score=float(score), subject=subject,
        concepts=concepts, daily_minutes=plan.daily_minutes,
    )
    for item in plan.sessions:
        if item.date > today and item.status != "completed" and item.date in updates:
            _save_planner_tasks(item, updates[item.date])
    plan.updated_at = utcnow()


def planner_dashboard_widget(user_id):
    plan = _active_plan_for_user(user_id)
    return _planner_context(plan) if plan else None


@app.get("/study-plans")
@login_required
def study_plans():
    plans = db.session.scalars(
        db.select(StudyPlan).join(LearningProject).where(
            StudyPlan.user_id == current_user.id,
            LearningProject.user_id == current_user.id,
        ).order_by(StudyPlan.status, StudyPlan.exam_date)
    ).all()
    return render_template("study_plans.html", plans=plans, today=date.today())


@app.route("/study-plans/new", methods=["GET", "POST"])
@login_required
def new_study_plan():
    projects = db.session.scalars(
        db.select(LearningProject).where(LearningProject.user_id == current_user.id)
        .order_by(LearningProject.updated_at.desc())
    ).all()
    selected_project_id = request.form.get("project_id") or request.args.get("project_id")
    if request.method == "POST":
        try:
            project_id = int(selected_project_id or 0)
            exam_date = date.fromisoformat(request.form.get("exam_date", ""))
            daily_minutes = int(request.form.get("daily_minutes", ""))
        except (TypeError, ValueError):
            flash(tr("Enter a valid project, exam date, and study time."), "error")
            return render_template(
                "study_plan_wizard.html", projects=projects,
                selected_project_id=selected_project_id, today=date.today(),
            ), 400
        project = db.session.scalar(db.select(LearningProject).where(
            LearningProject.id == project_id, LearningProject.user_id == current_user.id
        ))
        target_grade = request.form.get("target_grade", "").strip()[:40]
        difficulty = request.form.get("difficulty_preference", "medium")
        requested_days = request.form.getlist("preferred_days")
        preferred_days = normalize_preferred_days(requested_days)
        if not project or exam_date <= date.today() or not 10 <= daily_minutes <= 480:
            flash(tr("Choose a future exam date and 10 to 480 minutes per day."), "error")
            return render_template(
                "study_plan_wizard.html", projects=projects,
                selected_project_id=selected_project_id, today=date.today(),
            ), 400
        if not target_grade or not requested_days or difficulty not in {"easy", "medium", "hard"}:
            flash(tr("Choose a target grade, study weekdays, and difficulty preference."), "error")
            return render_template(
                "study_plan_wizard.html", projects=projects,
                selected_project_id=selected_project_id, today=date.today(),
            ), 400
        for existing in db.session.scalars(db.select(StudyPlan).where(
            StudyPlan.user_id == current_user.id,
            StudyPlan.project_id == project.id,
            StudyPlan.status == "active",
        )).all():
            existing.status = "archived"
        plan = StudyPlan(
            user_id=current_user.id, project_id=project.id, exam_date=exam_date,
            target_grade=target_grade, daily_minutes=daily_minutes,
            preferred_days=json.dumps(preferred_days),
            difficulty_preference=difficulty, status="active",
        )
        db.session.add(plan)
        db.session.flush()
        masteries = _planner_masteries(project)
        mistakes = _planner_mistakes(project)
        schedule = build_plan_schedule(
            today=date.today(), exam_date=exam_date, daily_minutes=daily_minutes,
            preferred_days=preferred_days, difficulty_preference=difficulty,
            sections=[_section_payload(item) for item in project.sections],
            masteries=[_mastery_payload(item) for item in masteries],
            mistakes=[{
                "id": item.id, "subject": item.subject or item.lesson.subject,
                "concept": item.concept,
            } for item in mistakes],
        )
        for row in schedule:
            saved = StudyPlanSession(study_plan_id=plan.id, date=row["date"], status="planned")
            _save_planner_tasks(saved, row["tasks"])
            db.session.add(saved)
        project.exam_date = exam_date
        db.session.commit()
        flash(tr("Your study plan is ready."), "success")
        return redirect(url_for("study_plan_detail", plan_id=plan.id))
    return render_template(
        "study_plan_wizard.html", projects=projects,
        selected_project_id=selected_project_id, today=date.today(),
    )


@app.get("/study-plans/<int:plan_id>")
@login_required
def study_plan_detail(plan_id):
    plan = owned_study_plan(plan_id)
    if not plan:
        return tr("Study plan not found"), 404
    context = _planner_context(plan)
    context["planner_mastery_growth"] = list(reversed(db.session.scalars(
        db.select(MasteryHistory).where(
            MasteryHistory.user_id == current_user.id,
            MasteryHistory.subject == plan.project.subject,
        ).order_by(MasteryHistory.practised_at.desc()).limit(12)
    ).all()))
    return render_template("study_plan_detail.html", **context)


@app.get("/study-plans/<int:plan_id>/calendar")
@login_required
def study_plan_calendar(plan_id):
    plan = owned_study_plan(plan_id)
    if not plan:
        return tr("Study plan not found"), 404
    month_value = request.args.get("month", date.today().strftime("%Y-%m"))
    try:
        year, month = (int(value) for value in month_value.split("-", 1))
        if not 1 <= month <= 12:
            raise ValueError
    except (TypeError, ValueError):
        year, month = date.today().year, date.today().month
    sessions = [_plan_session_payload(item) for item in plan.sessions]
    first = date(year, month, 1)
    previous = (first - timedelta(days=1)).strftime("%Y-%m")
    next_month = (date(year + (month == 12), 1 if month == 12 else month + 1, 1)).strftime("%Y-%m")
    return render_template(
        "study_plan_calendar.html", plan=plan,
        calendar_rows=calendar_days(year=year, month=month, sessions=sessions),
        month_label=f"{tr(first.strftime('%B'))} {year}", previous_month=previous,
        next_month=next_month, today=date.today(),
    )


@app.get("/study-plans/<int:plan_id>/sessions/<int:session_id>")
@login_required
def study_plan_session_detail(plan_id, session_id):
    plan = owned_study_plan(plan_id)
    session_record = owned_plan_session(plan_id, session_id)
    if not plan or not session_record:
        return tr("Study session not found"), 404
    return render_template(
        "study_plan_session.html", plan=plan, study_day=session_record,
        tasks=json_value(session_record.tasks_json), today=date.today(),
    )


@app.post("/study-plans/<int:plan_id>/sessions/<int:session_id>/complete")
@login_required
def complete_study_plan_session(plan_id, session_id):
    session_record = owned_plan_session(plan_id, session_id)
    if not session_record:
        return tr("Study session not found"), 404
    try:
        completed_minutes = int(request.form.get("completed_minutes", session_record.planned_minutes))
    except (TypeError, ValueError):
        completed_minutes = session_record.planned_minutes
    session_record.completed_minutes = max(0, min(480, completed_minutes))
    session_record.status = "completed"
    tasks = json_value(session_record.tasks_json)
    for task in tasks:
        task["completed"] = True
    _save_planner_tasks(session_record, tasks)
    session_record.study_plan.updated_at = utcnow()
    db.session.commit()
    flash(tr("Study session completed."), "success")
    return redirect(url_for("study_plan_detail", plan_id=plan_id))


@app.post("/study-plans/<int:plan_id>/sessions/<int:session_id>/skip")
@login_required
def skip_study_plan_session(plan_id, session_id):
    plan = owned_study_plan(plan_id)
    session_record = owned_plan_session(plan_id, session_id)
    if not plan or not session_record:
        return tr("Study session not found"), 404
    rows = [_plan_session_payload(item) for item in plan.sessions]
    redistributed = redistribute_after_skip(
        rows, skipped_date=session_record.date, daily_minutes=plan.daily_minutes
    )
    session_record.status = "skipped"
    session_record.completed_minutes = 0
    for item in plan.sessions:
        if item.date > session_record.date and item.status != "completed":
            _save_planner_tasks(item, redistributed.get(item.date, []))
    plan.updated_at = utcnow()
    db.session.commit()
    flash(tr("Missed work was balanced across your existing future study days."), "success")
    return redirect(url_for("study_plan_detail", plan_id=plan_id))


@app.get("/dashboard")
@login_required
def dashboard():
    language = get_current_language()
    now = utcnow()
    study_planner = planner_dashboard_widget(current_user.id)
    context = dashboard_context(
        db,
        user_id=current_user.id,
        concept_mastery_model=ConceptMastery,
        attempt_model=Attempt,
        lesson_model=Lesson,
        project_model=LearningProject,
        final_exam_model=FinalExam,
        mastery_history_model=MasteryHistory,
        subject_filter=request.args.get("subject", ""),
        status_filter=request.args.get("status", "").strip(),
        now=now,
    )
    context["study_planner"] = study_planner
    return render_template("dashboard.html", **context, language=language)


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
    language = get_current_language()
    context = todays_practice_context(
        db,
        user_id=current_user.id,
        concept_mastery_model=ConceptMastery,
        attempt_model=Attempt,
        lesson_model=Lesson,
        now=now,
        mastery_serializer=mastery_state,
        difficulty_label=difficulty_label,
    )
    return render_template("todays_practice.html", **context, language=language)


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
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        flash_ai_failure(error)
        return redirect(url_for("todays_practice"))
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
        task_type=("adaptive_practice" if adaptive_plan or "practice" in log_task else "lesson_generation"),
        language=learning_content_language(),
        fixture_context=(
            {
                "subject": adaptive_plan[0]["subject"],
                "concept": adaptive_plan[0]["concept"],
                "lesson": True,
            }
            if adaptive_plan else {"subject": subject}
        ),
        model=TUTOR_MODEL,
        instructions=tutor_instructions(adaptive_plan[0]["subject"] if adaptive_plan else subject),
        input=prompt,
        max_output_tokens=LESSON_TOKEN_LIMIT,
        temperature=0.2,
        **quality_options(),
    )
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
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        flash_ai_failure(error)
        return redirect(url_for("dashboard"))
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
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        flash_ai_failure(error)
        return redirect(url_for("dashboard"))
    except Exception:
        db.session.rollback()
        app.logger.exception("Weak-point practice generation failed")
        flash("The practice lesson could not be generated right now.", "error")
        return redirect(url_for("dashboard"))


@app.get("/concepts/<int:mastery_id>")
@login_required
def concept_detail(mastery_id):
    mastery = db.session.scalar(db.select(ConceptMastery).where(
        ConceptMastery.id == mastery_id,
        ConceptMastery.user_id == current_user.id,
    ))
    if not mastery:
        return "Concept not found", 404
    candidate_attempts = db.session.scalars(
        db.select(Attempt)
        .join(Lesson)
        .where(
            Lesson.user_id == current_user.id,
            func.coalesce(Attempt.subject, Lesson.subject) == mastery.subject,
        )
        .order_by(Attempt.timestamp.desc())
        .limit(200)
    ).all()
    attempts = [
        item for item in candidate_attempts
        if mastery.concept.casefold() in {
            concept.casefold() for concept in saved_concepts(item.concepts_json, item.concept)
        }
    ][:30]
    history = db.session.scalars(
        db.select(MasteryHistory).where(
            MasteryHistory.user_id == current_user.id,
            MasteryHistory.mastery_id == mastery.id,
        ).order_by(MasteryHistory.practised_at.desc()).limit(50)
    ).all()
    section_ids = {
        item.lesson.section_id for item in attempts if item.lesson.section_id
    }
    section_conditions = [
        LearningSection.title == mastery.concept,
        LearningSection.main_topic == mastery.concept,
    ]
    if section_ids:
        section_conditions.append(LearningSection.id.in_(section_ids))
    source_sections = db.session.scalars(
        db.select(LearningSection)
        .join(LearningProject)
        .where(
            LearningProject.user_id == current_user.id,
            LearningProject.subject == mastery.subject,
            or_(*section_conditions),
        )
        .order_by(LearningProject.updated_at.desc(), LearningSection.position)
    ).all()
    return render_template(
        "concept_detail.html",
        mastery=mastery,
        history=history,
        attempts=attempts,
        mistakes=[item for item in attempts if item.score < 80],
        source_sections=source_sections,
        difficulty=difficulty_label(mastery.difficulty_level),
    )


@app.post("/concepts/<int:mastery_id>/practice")
@login_required
def practice_concept(mastery_id):
    mastery = db.session.scalar(db.select(ConceptMastery).where(
        ConceptMastery.id == mastery_id,
        ConceptMastery.user_id == current_user.id,
    ))
    if not mastery:
        return "Concept not found", 404
    recent_questions = recent_concept_questions(
        current_user.id, mastery.subject, mastery.concept
    )
    prompt = f"""Create the opening lesson and first question for targeted adaptive practice.
Subject: {mastery.subject}
Concept: {mastery.concept}
Mastery: {mastery.mastery_score}
Difficulty: {difficulty_label(mastery.difficulty_level)}
Recent questions that must not be repeated: {json.dumps(recent_questions, ensure_ascii=False)}
Return the normal lesson JSON shape. Use this exact concept, include it in question.concepts, and create a new question with different wording and scenario from every recent question."""
    plan = prioritize_concepts([mastery_state(mastery)], question_count=5)
    try:
        session_id = start_saved_practice(
            prompt,
            mastery.subject,
            "targeted-concept-practice",
            test_total=5,
            adaptive_plan=plan,
        )
        return redirect(url_for("index", session_id=session_id))
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        flash_ai_failure(error)
        return redirect(url_for("concept_detail", mastery_id=mastery.id))
    except Exception:
        db.session.rollback()
        app.logger.exception("Targeted concept practice generation failed")
        flash("Targeted practice could not be generated right now.", "error")
        return redirect(url_for("concept_detail", mastery_id=mastery.id))


@app.post("/api/analyze")
@limiter.limit("10 per minute")
@login_required
def analyze_material():
    uploads = [upload for upload in request.files.getlist("images") if upload.filename]
    study_goal = request.form.get("study_goal", "").strip()[:3000]
    subject = request.form.get("subject", "Other").strip()[:80] or "Other"
    language = learning_content_language()
    if not uploads and not study_goal:
        return api_error(tr("Describe what you want to learn or attach study material."), 400, "missing_material")
    if len(uploads) > 4:
        return api_error("Upload no more than four images for this MVP.", 400, "too_many_images")

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
            task_type="lesson_generation",
            language=language,
            model=VISION_MODEL if uploads else TUTOR_MODEL,
            instructions=tutor_instructions(subject),
            input=[{"role": "user", "content": content}],
            max_output_tokens=LESSON_TOKEN_LIMIT,
            temperature=0.2,
            **(quality_options() if not uploads else {}),
        )
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
        return jsonify(ok=True, session_id=session_id, test_total=5, subject=subject, lesson=public_lesson, question=question)
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        message, status, code = ai_failure_message(error)
        return api_error(message, status, code)
    except (ValueError, json.JSONDecodeError, KeyError):
        db.session.rollback()
        return api_error(tr("The material could not be read reliably because the AI response could not be validated. You can retry. Your saved work remains safe."), 422, "invalid_ai_output")
    except Exception:
        db.session.rollback()
        app.logger.exception("Material analysis failed")
        return api_error(tr("AI is temporarily unavailable. You can retry. Your saved work remains safe."), 503, "ai_unavailable")


@app.post("/api/answer")
@limiter.limit("30 per minute")
@login_required
def check_answer():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
    raw_answer = payload.get("answer", "")
    answer = json.dumps(raw_answer, ensure_ascii=False) if isinstance(
        raw_answer, list) else str(raw_answer).strip()
    if not session:
        return api_error(tr("This lesson expired. Upload the material again."), 404, "lesson_expired")
    if raw_answer is None or raw_answer == "" or (isinstance(raw_answer, list) and not raw_answer):
        return api_error(tr("Write an answer before checking it."), 400, "answer_required")
    if len(answer) > 12000:
        return api_error(tr("Keep the answer under 12,000 characters."), 400, "answer_too_long")
    session["language"] = learning_content_language()

    question = session["current_question"]
    normalize_question_concept(session, question)
    question_subject = str(question.get("subject") or session.get("subject", "Other"))[:80]
    hints_used = bool(payload.get("hints_used", False))
    try:
        retry_count = max(0, min(10, int(payload.get("retry_count", 0))))
        response_confidence = float(payload.get("response_confidence", 50))
        question_difficulty = max(1, min(3, int(question.get("difficulty", 1))))
    except (TypeError, ValueError):
        return api_error(tr("Confidence and retry values must be numbers."), 400, "invalid_learning_context")
    if not 0 <= response_confidence <= 100:
        return api_error(tr("Confidence must be between 0 and 100."), 400, "invalid_confidence")
    question_concepts = saved_concepts(
        json.dumps(question.get("concepts", []), ensure_ascii=False),
        question.get("concept", "General"),
    )
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
        "difficulty": question_difficulty,
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
For mathematics or physics, write every equation, transformation, and unit conversion as its own $$...$$ LaTeX line in feedback and correction; never write formulas as plain text and never compress a multi-step calculation into one paragraph.
For multiple_choice and dropdown use exactly one correct option and 4 options. For checkboxes use 4 or 5 options with 2 or 3 correct answers. For ordering provide 4 items in a shuffled order. For text, options must be an empty list.
When next_target is present, target exactly that subject, concept, and difficulty. Otherwise target the weakest relevant concept. When uploaded_source is present, it is the only factual source for evaluation and new questions; never introduce facts outside it.
Follow the exact null/object structure shown above. Do not replace a required object with null."""

    original_session = json.loads(json.dumps(session, ensure_ascii=False))
    try:
        response = create_response(
            task_type="answer_evaluation",
            language=session["language"],
            validation_context={"is_final": bool(is_final or adaptive_plan)},
            model=TUTOR_MODEL,
            instructions=tutor_instructions(question_subject),
            input=prompt,
            max_output_tokens=ANSWER_TOKEN_LIMIT,
            temperature=0.1,
            **quality_options(),
        )
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
            "concepts": question_concepts,
            "difficulty": question_difficulty,
            "score": score,
            "hints_used": hints_used,
            "retry_count": retry_count,
            "response_confidence": response_confidence,
        })
        for concept_name in question_concepts:
            concept_record = session["mastery"].setdefault(
                concept_name, {"attempts": 0, "total_score": 0}
            )
            concept_record["attempts"] += 1
            concept_record["total_score"] += score
        lesson_record = db.session.scalar(
            db.select(Lesson).where(Lesson.session_id == payload.get("session_id"),
                                    Lesson.user_id == current_user.id)
        )
        if not lesson_record:
            raise KeyError("lesson")
        mastery_updates = []
        for concept_name in question_concepts:
            persistent_mastery = get_or_create_mastery(
                current_user.id, question_subject, concept_name
            )
            mastery_before, mastery_update = apply_mastery_update(
                persistent_mastery,
                score,
                hints_used=hints_used,
                difficulty=question_difficulty,
                retry_count=retry_count,
                response_confidence=response_confidence,
            )
            mastery_updates.append((persistent_mastery, mastery_before, mastery_update))
        _primary_mastery, mastery_before, mastery_update = mastery_updates[0]
        evaluation["skill_status"] = mastery_update["status"]
        attempt = Attempt(
            lesson=lesson_record,
            question=str(question.get("prompt", "")),
            subject=question_subject,
            concept=question_concepts[0],
            concepts_json=json.dumps(question_concepts, ensure_ascii=False),
            student_answer=answer,
            score=score,
            feedback=str(evaluation.get("feedback", "")),
            difficulty=question_difficulty,
            hints_used=hints_used,
            retry_count=retry_count,
            response_confidence=response_confidence,
            mastery_before=mastery_before,
            mastery_after=mastery_update["mastery_score"],
        )
        db.session.add(attempt)
        db.session.flush()
        for record, before, updated in mastery_updates:
            add_mastery_history(
                record,
                before,
                updated,
                score=score,
                difficulty=question_difficulty,
                hints_used=hints_used,
                retry_count=retry_count,
                response_confidence=response_confidence,
                attempt=attempt,
            )
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
        record_planner_activity(
            current_user.id,
            activity_kind="quiz" if lesson_record.section_id else "review",
            score=score,
            subject=question_subject,
            concepts=question_concepts,
            section_id=lesson_record.section_id,
        )
        if session.get("session_kind") == "adaptive_practice":
            for record, before, updated in mastery_updates:
                change_key = f"{question_subject}::{record.concept}"
                initial = session.get("initial_mastery", {}).get(change_key, before)
                session["mastery_changes"][change_key] = {
                    "subject": question_subject,
                    "concept": record.concept,
                    "before": initial,
                    "after": updated["mastery_score"],
                    "change": updated["mastery_score"] - initial,
                    "status": updated["status"],
                    "next_review_at": updated["next_review_at"].date().isoformat(),
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
        return jsonify(ok=True, evaluation=evaluation, next_question=public_question,
                       complete=is_final, summary=result.get("summary") if is_final else None, progress={
            "answered": len(session["history"]),
            "total": session["test_total"],
            "average_score": average,
            "mastery": mastery,
            "weakest_concept": mastery[0]["concept"] if mastery else None,
        }, practice_results=practice_results)
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        SESSIONS[payload.get("session_id")] = original_session
        message, status, code = ai_failure_message(error)
        return api_error(message, status, code)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        db.session.rollback()
        SESSIONS[payload.get("session_id")] = original_session
        return api_error(tr("The AI response could not be validated. You can retry. Your saved work remains safe."), 422, "invalid_ai_output")
    except Exception:
        db.session.rollback()
        SESSIONS[payload.get("session_id")] = original_session
        app.logger.exception("Answer evaluation failed")
        return api_error(tr("The tutor service is temporarily unavailable."), 500, "tutor_unavailable")


@app.post("/api/chat")
@limiter.limit("30 per minute")
@login_required
def tutor_chat():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
    message = str(payload.get("message", "")).strip()
    if not session:
        return api_error(tr("This lesson expired. Upload the material again."), 404, "lesson_expired")
    if not message:
        return api_error(tr("Write a message for your tutor."), 400, "message_required")
    if len(message) > 4000:
        return api_error("Keep tutor messages under 4,000 characters.", 400, "message_too_long")
    session["language"] = learning_content_language()

    active_plan = _active_plan_for_user(current_user.id, session.get("subject", "Other"))
    scheduled_context = None
    if active_plan:
        scheduled = next(
            (item for item in active_plan.sessions if item.date == date.today()),
            next((item for item in active_plan.sessions if item.date > date.today()), None),
        )
        if scheduled:
            scheduled_context = {
                "date": scheduled.date.isoformat(),
                "exam_date": active_plan.exam_date.isoformat(),
                "target_grade": active_plan.target_grade,
                "tasks": [
                    {
                        "kind": item.get("kind"), "concept": item.get("concept"),
                        "section": item.get("section_title"), "minutes": item.get("minutes"),
                        "reason": item.get("reason"), "difficulty": item.get("difficulty"),
                    }
                    for item in json_value(scheduled.tasks_json)
                    if not item.get("completed")
                ],
            }
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
        "scheduled_study_plan": scheduled_context,
    }
    prompt = f"""Tutor the student using this lesson state:
{json.dumps(chat_context, ensure_ascii=False)}

Reply only in response_language in at most 120 words. Be accurate and use small, explicit steps.
Use the saved context and today's scheduled_study_plan when present. Explain recommendations from its mastery/reason/date evidence instead of suggesting a random topic. For question help, give one useful hint, not the final answer.
Verify calculations; allow valid interpretations in humanities/languages. Mention an exception only when relevant.
Treat a short reply as an answer to the latest chat question. End with one short checking question."""

    try:
        response = create_response(
            task_type="tutor_chat",
            language=session["language"],
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
        return jsonify(ok=True, reply=reply, mastery=mastery_snapshot(session))
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        message, status, code = ai_failure_message(error)
        return api_error(message, status, code)
    except Exception:
        db.session.rollback()
        app.logger.exception("Tutor chat failed")
        return api_error(tr("AI is temporarily unavailable. You can retry. Your saved work remains safe."), 503, "ai_unavailable")


@app.post("/api/translate")
@limiter.limit("20 per minute")
@login_required
def translate_content():
    payload = request.get_json(silent=True) or {}
    session = owned_session(payload.get("session_id"))
    language = payload.get("language")
    texts = payload.get("texts", [])
    if not session:
        return api_error(tr("This lesson expired. Upload the material again."), 404, "lesson_expired")
    if language not in {"English", "German"}:
        return api_error(tr("Unsupported language."), 400, "unsupported_language")
    if not isinstance(texts, list) or not texts or len(texts) > 80:
        return api_error("Invalid translation request.", 400, "invalid_translation_request")
    cleaned = [str(item)[:3000] for item in texts]
    if sum(len(item) for item in cleaned) > 30000:
        return api_error("Too much text to translate at once.", 400, "translation_too_large")

    prompt = f"""Translate each string into {language}.
Return JSON exactly as {{"translations": ["translated string"]}} with the same number and order of items.
Preserve all numbers, mathematical symbols, formulas, option letters, and line breaks.
Translate the explanatory language naturally. If a string is already in {language}, keep it unchanged.
Strings: {json.dumps(cleaned, ensure_ascii=False)}"""
    try:
        response = create_response(
            task_type="translation",
            language=language,
            fixture_context={"texts": cleaned},
            model=FAST_MODEL,
            instructions="You are a precise educational translator. Return valid JSON only.",
            input=prompt,
            max_output_tokens=TRANSLATE_TOKEN_LIMIT,
            temperature=0,
        )
        result = parse_json(response.output_text)
        translations = result["translations"]
        if not isinstance(translations, list) or len(translations) != len(cleaned):
            raise ValueError("Translation count mismatch")
        save_session_state(payload.get("session_id"))
        return jsonify(ok=True, translations=translations)
    except (ai_service.AIGatewayError, ai_service.AIValidationError) as error:
        db.session.rollback()
        message, status, code = ai_failure_message(error)
        return api_error(message, status, code)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        db.session.rollback()
        return api_error(tr("The AI response could not be validated. You can retry. Your saved work remains safe."), 422, "invalid_ai_output")
    except Exception:
        db.session.rollback()
        app.logger.exception("Content translation failed")
        return api_error("The translation service is temporarily unavailable.", 500, "translation_unavailable")


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "").lower() in {"1", "true", "yes"},
    )

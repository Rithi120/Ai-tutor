"""Records for the dashboard delete and "Show more" tests (tests/test_dashboard_delete.py,
tests/test_dashboard_show_more_browser.py, scripts/mobile_audit.py).

Written straight into the app's database inside an app context, so no AI call is needed:
a project with a row in every table that refers to it, Short Lessons with mistakes, and
Premium codes for the Settings history. Never imports `app` itself - the caller passes the
module it already imported (its DATABASE_URL was set first).
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta
from typing import Any


def short_lesson(application: Any, user_id: int, *, title: str = "Bruchrechnung", subject: str = "Mathematics",
                 mistakes: int = 1, section_id: int | None = None, created_offset: int = 0,
                 chat: bool = True, photo: bool = False, project_id: int | None = None,
                 content: dict[str, Any] | None = None, state: dict[str, Any] | None = None) -> dict[str, Any]:
    """A saved lesson with `mistakes` wrong answers (score 30), a tutor chat message and its
    saved state. `project_id` links it to a project as a lesson made from the project's
    material outside a topic (Lesson.project_id); `content` replaces its content and `state`
    is merged into its saved state. Returns {"id", "session_id", "attempt_ids"}. The caller
    commits."""

    db = application.db
    session_id = uuid.uuid4().hex
    lesson = application.Lesson(
        user_id=user_id, section_id=section_id, project_id=project_id, session_id=session_id, subject=subject,
        title=title, content_json=json.dumps(content if content is not None else {"lesson_title": title}),
        created_at=application.utcnow() - timedelta(minutes=created_offset))
    db.session.add(lesson)
    db.session.flush()
    attempt_ids = []
    for number in range(mistakes):
        attempt = application.Attempt(
            lesson_id=lesson.id, question=f"{title}: Frage {number + 1}", subject=subject,
            concept=f"{title} Konzept {number + 1}", student_answer="Ich weiß es nicht genau, vielleicht 4.",
            score=30, feedback="Fast. Prüfe den Nenner noch einmal.", difficulty=1, verdict="incorrect",
            timestamp=application.utcnow() - timedelta(minutes=created_offset, seconds=number))
        db.session.add(attempt)
        db.session.flush()
        attempt_ids.append(attempt.id)
    if chat:
        db.session.add(application.ChatMessage(lesson_id=lesson.id, role="user", content="Warum?"))
    db.session.add(application.StudySession(lesson_id=lesson.id, state_json=json.dumps(
        {"user_id": user_id, "lesson": {"lesson_title": title}, "history": [], "subject": subject,
         "test_total": 3, "current_question": {"id": "q1", "prompt": "?"}, "mastery": {}, **(state or {})})))
    if photo:
        db.session.add(application.ChatAttachment(
            user_id=user_id, lesson_session_id=session_id, purpose="answer", data=b"\x89PNG lesson photo",
            reading_json="{}", read_status="ready", sent_at=application.utcnow()))
    db.session.flush()
    return {"id": lesson.id, "session_id": session_id, "attempt_ids": attempt_ids}


def full_project(application: Any, user_id: int, *, title: str = "Physik – Mechanik") -> dict[str, Any]:
    """A project with a row in every table that refers to it (or to its topics, pages and
    files), a project lesson with a graded answer behind a MasteryHistory row, the exam's
    review lesson and a mixed-practice lesson (Lesson.project_id), and its key-terms
    flashcard set. Returns the ids. The caller commits."""

    db = application.db
    now = application.utcnow()
    project = application.LearningProject(user_id=user_id, title=title, subject="Physics",
                                          topics_confirmed=True, exam_date=date.today() + timedelta(days=10))
    db.session.add(project)
    db.session.flush()
    stored = application.ProjectFile(project_id=project.id, original_filename="notes.pdf",
                                     mime_type="application/pdf", original_data=b"%PDF-1.4 project file bytes",
                                     sha256="ab" * 32)
    db.session.add(stored)
    db.session.flush()
    page = application.ProjectPage(project_id=project.id, file_id=stored.id, page_number=1, page_order=1,
                                   extracted_text="Kraft ist Masse mal Beschleunigung.",
                                   processed_data=b"\x89PNG processed page", processed_mime_type="image/png")
    db.session.add(page)
    db.session.flush()
    db.session.add(application.DocumentBlock(page_id=page.id, block_order=1, block_type="text",
                                             content="F = m · a"))
    section = application.LearningSection(project_id=project.id, position=1, title="Kraft", main_topic="Kraft",
                                          source_page_ids_json=json.dumps([page.id]), mastery_score=40)
    db.session.add(section)
    db.session.flush()
    db.session.add(application.RecallCard(section_id=section.id, kind="definition", prompt="Was ist Kraft?",
                                          answer="F = m · a"))
    db.session.add(application.Competency(project_id=project.id, section_id=section.id, source_file_id=stored.id,
                                          statement="Ich kann die Kraft berechnen.", statement_key="kraft"))
    db.session.add(application.ExamPrepState(project_id=project.id))
    plan = application.StudyPlan(user_id=user_id, project_id=project.id, exam_date=date.today() + timedelta(days=10),
                                 target_grade="2", daily_minutes=30)
    db.session.add(plan)
    db.session.flush()
    db.session.add(application.StudyPlanSession(study_plan_id=plan.id, date=date.today(), planned_minutes=20))
    exam = application.FinalExam(project_id=project.id, question_count=1, duration_minutes=20, difficulty_mode="mixed",
                                 included_section_ids_json=json.dumps([section.id]), question_types_json="[]",
                                 status="submitted", expires_at=now + timedelta(minutes=20), submitted_at=now,
                                 score=55)
    db.session.add(exam)
    db.session.flush()
    question = application.ExamQuestion(exam_id=exam.id, section_id=section.id, position=1, difficulty="easy",
                                        question_type="short_answer", prompt="Was ist F?", expected_answer="m · a",
                                        supporting_text="F = m · a")
    db.session.add(question)
    db.session.flush()
    db.session.add(application.ExamAnswer(exam_id=exam.id, question_id=question.id, answer_text="m · a", score=100))
    lesson = short_lesson(application, user_id, title="Kraft verstehen", subject="Physics", section_id=section.id,
                          photo=True)
    # Lessons made from the project's material outside a topic (Lesson.project_id): the
    # practice exam's review lesson (its graded answers are its attempts, as
    # submit_final_exam saves them) and a mixed-practice lesson with the notes in its state.
    review = short_lesson(application, user_id, title=f"Exam review: {title}", subject="Physics",
                          project_id=project.id, content={"exam_id": exam.id}, chat=False)
    practice = short_lesson(application, user_id, title="Gemischte Übung Mechanik", subject="Physics",
                            project_id=project.id, state={"session_kind": "project_practice",
                                                          "project_id": project.id,
                                                          "source_context": "GEHEIME NOTIZEN DES PROJEKTS"})
    mastery = application.get_or_create_mastery(user_id, "Physics", "Kraft")
    mastery.attempts, mastery.mastery_score = 1, 40
    db.session.flush()
    history = application.MasteryHistory(
        user_id=user_id, mastery_id=mastery.id, attempt_id=lesson["attempt_ids"][0], subject="Physics",
        concept="Kraft", mastery_before=30, mastery_after=40, delta=10, score=30, difficulty=1, outcome="partial")
    db.session.add(history)
    flashcard_set = application.FlashcardSet(user_id=user_id, title=f"{title} – key terms", subject="Physics",
                                             source_kind="project", source_reference=f"project:{project.id}",
                                             project_id=project.id)
    db.session.add(flashcard_set)
    db.session.flush()
    card = application.Flashcard(set_id=flashcard_set.id, front="Kraft", back="F = m · a",
                                 source_page_ids_json=json.dumps([page.id]), origin_key="kraft:term:kraft")
    db.session.add(card)
    db.session.flush()
    return {"project": project.id, "file": stored.id, "page": page.id, "section": section.id, "plan": plan.id,
            "exam": exam.id, "question": question.id, "lesson": lesson, "review": review, "practice": practice,
            "mastery": mastery.id,
            "history": history.id, "set": flashcard_set.id, "card": card.id}


def premium_codes(application: Any, user_id: int, count: int) -> None:
    """`count` redeemed Premium codes in the account's history (Settings -> Subscription),
    all in the past, so the account is on the Free plan. The caller commits."""

    db = application.db
    now = application.utcnow()
    for number in range(count):
        key = application.PremiumKey(key_hash=uuid.uuid4().hex * 2, duration_months=1, code_hint=f"K{number:03d}",
                                     expires_at=now - timedelta(days=400 - number),
                                     created_by_id=user_id, redeemed_at=now - timedelta(days=400 - 31 * number),
                                     redeemed_by_id=user_id)
        db.session.add(key)
        db.session.flush()
        db.session.add(application.PremiumRedemption(
            premium_key_id=key.id, user_id=user_id, redeemed_at=now - timedelta(days=400 - 31 * number),
            new_expires_at=now - timedelta(days=370 - 31 * number)))
    db.session.flush()

"""Read models for dashboard and daily-practice pages."""

from __future__ import annotations

from sqlalchemy.orm import selectinload

from learnova.quizzes.adaptive import estimated_question_count, mastery_status, review_is_due


VALID_MASTERY_FILTERS = {"weak", "learning", "strong", "mastered", "understood"}
# How many rows each dashboard list loads. The page shows the first few of each and a
# "Show more" button for the rest (static/js/show-more.js), so these are generous but
# bounded; every list is one query (or two, for the lesson counts), never one per row.
LIST_CAPS = {"lessons": 50, "projects": 50, "concepts": 30, "improvements": 30, "exams": 30}


def dashboard_context(
    database,
    *,
    user_id: int,
    concept_mastery_model,
    attempt_model,
    lesson_model,
    project_model,
    final_exam_model,
    mastery_history_model,
    subject_filter: str,
    status_filter: str,
    now,
):
    func = database.func
    subject_filter = subject_filter.strip()[:80]
    status_filter = status_filter if status_filter in VALID_MASTERY_FILTERS else ""
    masteries = database.session.scalars(
        database.select(concept_mastery_model)
        .where(concept_mastery_model.user_id == user_id)
        .order_by(concept_mastery_model.mastery_score, concept_mastery_model.updated_at.desc())
    ).all()
    subject_rows = database.session.execute(
        database.select(
            concept_mastery_model.subject,
            func.avg(concept_mastery_model.mastery_score),
            func.sum(concept_mastery_model.attempts),
        )
        .where(concept_mastery_model.user_id == user_id)
        .group_by(concept_mastery_model.subject)
        .order_by(func.avg(concept_mastery_model.mastery_score))
    ).all()
    mistake_query = (
        database.select(attempt_model)
        .join(lesson_model)
        .options(selectinload(attempt_model.lesson))
        .where(lesson_model.user_id == user_id, attempt_model.score < 80)
        .order_by(attempt_model.timestamp.desc())
    )
    if subject_filter:
        mistake_query = mistake_query.where(
            func.coalesce(attempt_model.subject, lesson_model.subject) == subject_filter
        )
    attempts = database.session.scalars(mistake_query.limit(100)).all()
    mastery_lookup = {(item.subject, item.concept): item.mastery_score for item in masteries}
    mistakes = []
    for item in attempts:
        item_subject = item.subject or item.lesson.subject
        status = "understood" if item.understood_at else mastery_status(
            mastery_lookup.get((item_subject, item.concept), 0)
        )
        if not status_filter or status == status_filter:
            mistakes.append({"attempt": item, "mastery_status": status})
    mistake_subjects = database.session.scalars(
        database.select(func.coalesce(attempt_model.subject, lesson_model.subject))
        .select_from(attempt_model)
        .join(lesson_model)
        .where(lesson_model.user_id == user_id, attempt_model.score < 80)
        .distinct()
        .order_by(func.coalesce(attempt_model.subject, lesson_model.subject))
    ).all()
    due_today = database.session.scalar(
        database.select(func.count(concept_mastery_model.id)).where(
            concept_mastery_model.user_id == user_id,
            database.or_(
                concept_mastery_model.next_review_at.is_(None),
                concept_mastery_model.next_review_at <= now,
            ),
        )
    ) or 0
    next_review = database.session.scalar(
        database.select(func.min(concept_mastery_model.next_review_at)).where(
            concept_mastery_model.user_id == user_id,
            concept_mastery_model.next_review_at.is_not(None),
        )
    )
    due_concepts = [
        item for item in masteries if review_is_due(item.next_review_at, now)
    ][:LIST_CAPS["concepts"]]
    recent_improvements = database.session.scalars(
        database.select(mastery_history_model)
        .where(
            mastery_history_model.user_id == user_id,
            mastery_history_model.delta > 0,
        )
        .order_by(mastery_history_model.practised_at.desc())
        .limit(LIST_CAPS["improvements"])
    ).all()
    trend_attempts = database.session.scalars(
        database.select(attempt_model)
        .join(lesson_model)
        .options(selectinload(attempt_model.lesson))
        .where(lesson_model.user_id == user_id, attempt_model.mastery_after.is_not(None))
        .order_by(attempt_model.timestamp.desc())
        .limit(12)
    ).all()
    lessons = database.session.scalars(
        database.select(lesson_model)
        .options(selectinload(lesson_model.study_session))
        .where(lesson_model.user_id == user_id)
        .order_by(lesson_model.created_at.desc())
        .limit(LIST_CAPS["lessons"])
    ).all()
    # The list shows how many answers and chat messages each lesson has: counted in the
    # database (one grouped query each), not by loading every message of fifty lessons.
    lesson_ids = [lesson.id for lesson in lessons]
    lesson_counts = {lesson_id: {"attempts": 0, "messages": 0} for lesson_id in lesson_ids}
    if lesson_ids:
        for relationship, key in ((lesson_model.attempts, "attempts"), (lesson_model.chat_messages, "messages")):
            child = relationship.property.mapper.class_
            for lesson_id, count in database.session.execute(
                database.select(child.lesson_id, func.count(child.id))
                .where(child.lesson_id.in_(lesson_ids))
                .group_by(child.lesson_id)
            ).all():
                lesson_counts[lesson_id][key] = int(count or 0)
    # Only what a project card shows: the page count (counted, not loaded - page text is
    # long) and each topic's mastery and exclusion (not its explanations and topic map).
    section_model = project_model.sections.property.mapper.class_
    page_model = project_model.pages.property.mapper.class_
    projects = database.session.scalars(
        database.select(project_model)
        .options(selectinload(project_model.sections).load_only(
            section_model.id, section_model.project_id, section_model.excluded, section_model.mastery_score))
        .where(project_model.user_id == user_id)
        .order_by(project_model.updated_at.desc())
        .limit(LIST_CAPS["projects"])
    ).all()
    project_page_counts = {project.id: 0 for project in projects}
    if projects:
        for project_id, count in database.session.execute(
            database.select(page_model.project_id, func.count(page_model.id))
            .where(page_model.project_id.in_(list(project_page_counts)))
            .group_by(page_model.project_id)
        ).all():
            project_page_counts[project_id] = int(count or 0)
    recent_exams = database.session.scalars(
        database.select(final_exam_model)
        .join(project_model, final_exam_model.project_id == project_model.id)
        .options(selectinload(final_exam_model.project))
        .where(project_model.user_id == user_id, final_exam_model.status == "submitted")
        .order_by(final_exam_model.submitted_at.desc())
        .limit(LIST_CAPS["exams"])
    ).all()
    return {
        "masteries": masteries[:3],
        # The "Weakest concepts" list (masteries above stays the three the signals use): only
        # concepts not yet strong, so "Show more" reveals more weak spots, never mastered ones.
        "weakest_concepts": [item for item in masteries if mastery_status(
            item.mastery_score or 0) in {"weak", "learning"}][:LIST_CAPS["concepts"]],
        "subjects": subject_rows,
        "mistakes": mistakes,
        "mistake_subjects": mistake_subjects,
        "subject_filter": subject_filter,
        "status_filter": status_filter,
        "due_today": due_today,
        "next_review": next_review,
        "due_concepts": due_concepts,
        "recent_improvements": recent_improvements,
        "mastery_trend": list(reversed(trend_attempts)),
        "lessons": lessons,
        "lesson_counts": lesson_counts,
        "projects": projects,
        "project_page_counts": project_page_counts,
        "recent_exams": recent_exams,
    }


def todays_practice_context(
    database,
    *,
    user_id: int,
    concept_mastery_model,
    attempt_model,
    lesson_model,
    now,
    mastery_serializer,
    difficulty_label,
):
    masteries = database.session.scalars(
        database.select(concept_mastery_model)
        .where(concept_mastery_model.user_id == user_id)
        .order_by(concept_mastery_model.mastery_score, concept_mastery_model.next_review_at)
    ).all()
    failed_attempts = database.session.scalars(
        database.select(attempt_model)
        .join(lesson_model)
        .options(selectinload(attempt_model.lesson))
        .where(lesson_model.user_id == user_id, attempt_model.score < 50)
        .order_by(attempt_model.timestamp.desc())
        .limit(30)
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
    states = [mastery_serializer(item) for item in masteries]
    question_count = estimated_question_count(states, now) if states else 0
    return {
        "due": [item for item in masteries if review_is_due(item.next_review_at, now)],
        "weakest": masteries[:5],
        "recently_failed": recently_failed,
        "question_count": question_count,
        "estimated_minutes": question_count * 2,
        "difficulty_label": difficulty_label,
    }

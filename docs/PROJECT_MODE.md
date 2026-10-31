# Project Mode (Projektmodus)

Project Mode is the orchestration layer of Learnova's learning systems. A student uploads
notes, gets a list of topics from them, and then works through a plan by tapping **Next**:
learn, practise, get tested, revise, and take a final practice exam.

## One feature, one entry point

Projects, the study planner ("Lernplaner") and the exam planner ("Prüfungsplaner",
"Prüfungsmodus") used to have three navigation entries but one data model: a
`LearningProject` owns its topics (`LearningSection`), its plan (`StudyPlan` /
`StudyPlanSession`, written only by the scheduler) and its practice exams (`FinalExam`).
They are one feature now, **Projektmodus**:

| Before | Now |
|---|---|
| Nav "Projekte", "Mehr → Lernplaner", "Mehr → Prüfungsmodus" | one nav entry, **Projektmodus** (`/projects`) |
| `/study-plans` (plan list) | redirects to `/projects`; each project card shows its exam date, days left or "Flexible plan", and the next task |
| `/study-plans/new` (exam planner form) | GET redirects to the project's **Exam date and study time** (`/projects/<id>#plan`), or to `/projects`; an old form POST still works and lands on the project |
| `/study-plans/<id>` (plan dashboard) | redirects to `/projects/<id>#progress` |
| `/study-plans/<id>/calendar`, `/sessions/<id>` | unchanged sub-pages, linked from the overview, back link to the project |
| `/projects/<id>/exam/new` (custom practice exam) | unchanged, linked as "Customise the practice exam" |

Nothing was migrated away or deleted: the rows are the same, only the pages that show them
changed. Migration 036 adds one optional column, `learning_project.grade`.

**Creating a project** ("Neues Lernprojekt erstellen"): name, subject, class, optional exam
date, notes (photos, PDFs, camera), and - under "Kompetenzraster and own topics" - an
optional Kompetenzraster upload and topics typed in one per line. Any one of notes,
Kompetenzraster or typed topics is enough; a project of typed topics only goes straight to
the topic list. Kompetenzraster pages are stored as teacher-marked pages, which raises the
importance of the topics they cover. A topic with no notes behind it is taught from
standard school content. Submitting the same project twice within two minutes (same
title, subject and files, or the same typed topics) opens the first one instead of making a
copy.

**The project overview** ("Projektübersicht", `/projects/<id>`) is the only dashboard:
next step, today, progress (topics learned, plan progress, consistency, mastery growth, and
exam readiness only when there is an exam date), knowledge and weak spots (topic difficulty
and the weakest concepts), the revision schedule, the practice exam and its results, the
topic list, plan settings (exam date, class, target grade, difficulty, study time, free
days) and the pages. Without an exam date the same page shows a flexible plan.

**Class**: a project's class overrides the student's own grade for that project's lessons
and curriculum matching (`g.project_grade`, carried in the lesson session).

## The student flow

1. **New learning project** (`/projects`). One form: subject, optional name, photos or PDFs
   (or the camera scanner), and an optional exam date. The button is **Analyze my notes**.
   A Kompetenzraster is never required.
2. **Reading** (`/projects/<id>/start`). Pages are read one at a time, with progress shown.
   Then `/quick-start` finds the topics.
3. **Confirm topics** (`/projects/<id>`, the workspace, `#topics`). The workspace shows:
   - the topics it found, with their source pages, learning goals and curriculum status;
   - any page that could not be read, which the student can correct by typing the text or
     by retaking the photo.

   The student can rename, merge, add or remove topics, or choose topics themselves; the
   default is "Let Learnova choose". **Start learning** plans every project and opens the
   first lesson.
4. **Learn and revise** (the same workspace). A big **Next** button sits at the top. Below
   it are today's tasks, the topic list (where any topic can be started), the practice
   exam, the exam date and study time, and the pages (add more, or check every recognised
   page in detail).

On the Overview, the "Your learning plan" card shows the next action across all projects,
today's combined workload, upcoming exams and overload warnings.

## Architecture

All the logic modules below are pure (no database, no AI) and unit-tested.

| Piece | Where | Notes |
|---|---|---|
| Topic extraction | `learnova/projects/topics.py` | See below. |
| Scheduler | `learnova/projects/scheduler.py` | See below. |
| Recall grading | `learnova/projects/planning.recall_answer_score` | Deterministic: word sets, umlaut folding, spelling slips; numbers must match. |
| Orchestration | `app.py`, the "Project Mode v2" block | `refresh_project_plans`, `project_next_task`, `start_project_task`, `complete_topic_session`, `record_topic_result`, the workspace routes. |
| Lessons | `start_section_lesson(section, mode)` | Modes are learn, review and reteach. A lesson carries the topic's curriculum excerpts and its explanation level (`topic_teaching_plan`). |
| Grading and mastery | `/api/answer` (unchanged) | When a topic lesson finishes, `complete_topic_session` updates the review ladder, ticks the plan task and re-plans, once per lesson. |
| Practice exams | `generate_final_exam` | See below. |

**Topic extraction** (`learnova/projects/topics.py`):

- Pages are batched to fit the AI input budget.
- Each topic is validated leniently: bad items are dropped and must cite a real page.
- Duplicates are merged across batches and pages using a stable `topic_key`.
- `reconcile` keeps the student's renames, merges and progress when the notes are analysed
  again.

**The scheduler** (`learnova/projects/scheduler.py`):

- One shared daily budget covers every project, with 10% kept as a buffer.
- Completed and pinned work is preserved.
- The last study day before an exam holds the practice exam and targeted revision.
- Overdue reviews come first, then reteaching, then new topics in priority order: important,
  demanding, weak and with dependants first, nearest exam first.
- At most one demanding topic per day, unless there are more demanding topics than days.
- The day before the practice exam has mixed practice.
- A topic is only started if it can still be reviewed afterwards.
- Long topics are split into parts. A topic that does not fit is reported, never squeezed in.

**Practice exams** (`generate_final_exam`):

- Lenient: an unusable question is dropped, and the exam is saved if at least 60% of the
  questions and 70% of the topics are covered.
- Multiple-choice options are normalised to option ids, so a correct answer always matches
  the key.
- The answer key never reaches the exam page.
- Results update every topic's mastery once and its review date.

### Data and migration 031

The migration is purely additive; every new column has a default.

- `learning_section` gains:
  - `topic_key` and `user_edited` (topic identity);
  - `difficulty`, `importance`, `confidence`, `prerequisites_json`, `curriculum_json`;
  - review state: `learned_at`, `review_step`, `next_review_on`, `reteach`.
- `learning_project` gains:
  - `topics_confirmed`: the student pressed Start learning. Projects that already had
    sections are marked confirmed by the migration.
  - `topics_stale`: pages changed since the topics were made.
  - `analysis_json`: cached batch results, warnings, merged-topic aliases, plan warnings.
- `study_plan` gains `flexible`, for a project without an exam date.
- The new table `study_availability` holds one budget per student: `daily_minutes` and
  `unavailable_weekdays`.

The calendar and study-day pages (`/study-plans/<id>/calendar`, `/sessions/<id>`) still
render the same `StudyPlan` / `StudyPlanSession` rows; the scheduler writes them. The old
plan list, plan form and plan dashboard redirect into Projektmodus (see above).

### Review ladder

After a topic session, or the topic's part of a practice exam, the next review moves:

| Result | Next review |
|---|---|
| Correct without hints | One step further on the ladder: 1 → 3 → 7 → 14 → 30 days. |
| Correct with hints | Same interval. |
| Incorrect | 1 day. |
| Incorrect with two misconception diagnoses in the session | 1 day, and the topic is marked for **reteaching** (a different explanation before the next test). |
| Unassessed | Nothing changes; no date is invented. |

Reviews are always kept before the exam date.

### Curriculum

Each topic is matched against imported Bildungsplan documents (`docs/CURRICULUM.md`),
using the student's grade and school type.

- **No documents imported:** the workspace says so ("No official curriculum documents are
  imported yet, so the topics come from your notes only") and nothing is fabricated.
- **A match from a later grade:** flagged for review ("check with your teacher"), never
  removed.

### Missed days and changes

The plan is regenerated:

- when the workspace or Overview is opened on a new day, or when an unfinished past day
  exists;
- after a finished topic session or a practice exam;
- when topics are confirmed, toggled or merged;
- when the exam date or study time changes.

A missed day becomes "missed", never deleted, and its work is planned again. Overdue
reviews come first. "I missed this day" also blocks that date for every project.

## Running

```bash
pip install -r requirements-dev.txt
flask --app app run
python -m pytest tests/test_project_scheduler.py tests/test_project_topics.py tests/test_project_mode_v2.py
python -m pytest tests/test_mobile_layout.py       # Chrome or Edge needed
MSYS_NO_PATHCONV=1 python scripts/mobile_audit.py  # every page, 7 viewports, light and dark
```

## Deployment and rollback checklist

1. **Back up the production database** (for Postgres, `pg_dump`). Migration 031 only adds
   columns and a table, but always back up first.
2. **Deploy.** `ensure_database()` applies migration 031 at start-up; it is idempotent.
3. **Smoke test:**
   - create a project from two photos;
   - confirm the topics;
   - tap Next;
   - answer a question;
   - open the plan calendar;
   - take a practice exam.
4. **Watch** `instance/ai_usage.jsonl` (or the AI diagnostics page) for
   `project_section_generation` and `final_exam_generation` failures.
5. **Roll back** by redeploying the previous commit. The new columns and table are ignored
   by the old code, so no down-migration is needed. Old projects keep their sections either
   way.

## Known limits

- **Extraction quality on real handwriting:** topic granularity depends on the model. It is
  tested with mocked responses only; a live evaluation is still to do.
- **Kompetenzraster:** a Kompetenzraster in the upload is read like any page. Its "Ich kann"
  rows are not yet used as separate teacher objectives, though teacher-marked pages raise
  topic importance.
- **No grade estimate.** There is no documented grading scale for a practice exam, so the
  workspace shows scores and topic states instead.
- **Synchronous processing:** OCR and analysis run inside the request, one page per request
  from the start page. There is no job queue.

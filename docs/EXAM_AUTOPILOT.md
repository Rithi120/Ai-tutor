# Exam autopilot

Scan → enter the exam date → start. From there the student is never asked "what should I
learn now?": the autopilot decides the next optimal action from the Kompetenzraster, the
notes, the knowledge model and the remaining time, and says honestly what grade the
evidence points to.

## What it reuses (and does not duplicate)

| Stage | Existing piece |
| --- | --- |
| Input, recognition, topics | Projects → pages → recognition → `plan_project_sections` (sections = topics with goals, facts, recall cards, likely questions) |
| Knowledge graph | `ConceptMastery` with evidence weights, uncertainty, prerequisites (`learnova/diagnostics/knowledge.py`) |
| Teaching | `start_section_lesson` – the AI explains a section from the student's own pages |
| Practice / error diagnosis / remediation | the answer loop: grading, evidence‑based diagnosis (now 13 types), planner actions, knowledge‑gated test (`learnova/quizzes/mastery_gate.py`) |
| Spaced review | Today's Practice / `prioritize_concepts` |
| Day‑by‑day schedule and reminders | `learnova/study_planner` (`build_plan_schedule`, overdue redistribution, in‑app reminders) |
| Mock exam | `generate_final_exam` (extracted from the manual exam setup) |

## What is new – `learnova/exam_prep/`

- **`competencies.py`** – the `competency_extraction` AI task reads "Ich kann …" statements
  (topic, subtopic, level, importance, page references) and judges whether the notes cover
  each one. The module enforces the vocabulary and **verifies every coverage quote against
  the notes**: a "covered" whose quote is not in the notes is stored as *missing*. Rows are
  attached to sections by word overlap; a requirement with no section stays visible.
- **`autopilot.py`** – `next_action()`: final days (last quarter, 2–3 days) → due reviews,
  then a mock exam if none in two days, then the weakest learned topic; otherwise spaced
  review when ≥3 reviews are due and the current topic has been started; otherwise the
  first topic not yet *known* (taught → learn, taught but not known → practice); when all
  are known → mock exam, then the least‑certain topic. "Known" is the knowledge model's
  verdict (≥ target with enough evidence), never one right answer.
- **`grade.py`** – `estimate_grade()`: German 1–6 from the importance‑weighted knowledge
  per topic (untested topics count as ~35%, so untested work drags the estimate), blended
  40/60 with the last two mock exams. The range widens with thin evidence (±5 to ±25
  points). `explain_change()` yields the reasons shown as "Why the estimate changed".

## App wiring

- `ensure_exam_autopilot(project, exam_date)` – sections → competencies → StudyPlan
  (30 min/day, every day, so the calendar and reminders work) → `ExamPrepState`. Runs from
  `quick-start` when the upload carried an exam date, or from the one‑field form on the
  project page (`POST /projects/<id>/autopilot`).
- `exam_prep_card(project)` – topic state from the knowledge model per section (concepts
  answered in that section's lessons), the action, the estimate; stores the estimate
  history with its reasons when it moves.
- `POST /projects/<id>/autopilot/next` – executes the action: resume the unfinished lesson,
  teach the section, run a knowledge‑gated practice on its concepts, review what is due, or
  generate a mock exam (count 4×sections+4, 2 min/question, mixed difficulty).
- The card appears on the project page and on the Overview (soonest exam).

## Honest limits

- Verified with the model patched (`tests/test_exam_autopilot_integration.py`), not on a
  real Kompetenzraster scan yet; the extraction prompt will need tuning on real grids.
- Reminders are in‑app (planner widget); there is no push/e‑mail infrastructure to reuse.
- The schedule still comes from `build_plan_schedule`; the autopilot's per‑day balance is
  the *action*, not a rewrite of the calendar.

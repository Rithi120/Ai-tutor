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

- **`competencies.py`** – the Kompetenzraster inventory (spec Phase 1.1, wired since
  2026-11-13; see "Kompetenzraster inventory" in [PROJECT_MODE.md](PROJECT_MODE.md)). The
  `competency_extraction:v2` task reads every "Ich kann …" row of a grid page (statement as
  written, heading chain, level, importance, the topic it belongs to). The module grounds
  every statement on its own page (a row the page does not say is never stored), dedupes by
  the exact statement key only, counts the page's rows deterministically (`expected_rows`)
  so a short answer is a visible gap, attaches each row to one topic (stemmed words; ties
  stay unassigned) and derives each competency's status from evidence
  (`competency_state`). The old notes-coverage fields (`coverage`, `evidence`) stay in the
  table but are no longer asked for: covering the notes is the topics' job.
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

- `ensure_exam_autopilot(project, exam_date)` – sections (`ensure_project_sections`, which
  also reads the Kompetenzraster pages into the competency inventory) → the competencies
  attached to a topic are confirmed → topics confirmed → the project's StudyPlan
  (`refresh_project_plans`). Runs from the one-field form on the project page
  (`POST /projects/<id>/autopilot`).
- The workspace (`project_workspace_context`) shows the inventory under each topic and adds
  the coverage check "Every competency of the Kompetenzraster practised" for a project with
  a grid, so the project is never "complete" while a competency is open.
- `POST /projects/<id>/autopilot/next` – the one Next action of the project (`project_next`).
- **Not wired (honest):** there is no `exam_prep_card` and no `ExamPrepState` writer in
  `app.py`; `autopilot.py` and `grade.py` are pure modules with tests only, and the
  workspace deliberately shows no grade estimate (no documented grading scale). The
  competency inventory does not change the plan's order yet: teacher-marked grid pages
  raise the importance of the topics they cover, as before.

## Honest limits

- Verified with the model patched (`tests/test_exam_autopilot_integration.py`,
  `tests/test_competency_inventory.py`), not on a real Kompetenzraster scan yet; the
  extraction prompt will need tuning on real grids (multi-column level tables read by the
  text layer or vision can split or join rows: the gap banner makes that visible).
- Reminders are in‑app (planner widget); there is no push/e‑mail infrastructure to reuse.
- The schedule still comes from `build_plan_schedule`; the autopilot's per‑day balance is
  the *action*, not a rewrite of the calendar.

# Adaptive explanations (grade + topic knowledge)

Each tutoring response is pitched at an **explanation level** from 1 to 5. The level is chosen
per topic from two signals:

- **The grade** sets how to talk and the *ceiling*: the most complex treatment that is still
  relevant to that school year.
- **The evidence about this topic** sets the starting point inside that range. It comes from
  `ConceptMastery`, recent diagnoses, confirmed prerequisite gaps and hint use.

The decision rules are in `learnova/diagnostics/explanation_level.py` (pure functions, unit
tested). The wiring is in `app.py`: `topic_mastery_record`, `topic_teaching_plan`, and the
`teaching=` argument of `tutor_instructions`.

| Level | Name | Default when nothing is known |
|---|---|---|
| 1 | Concrete foundations | Klasse 1–2 |
| 2 | Guided basics | Klasse 3–4, grade not set |
| 3 | Standard school level | Klasse 5 and up |
| 4 | Advanced understanding | |
| 5 | Extension | |

Ceilings by grade: 2 for Klasse 1–2, 3 for Klasse 3–4, 4 for Klasse 5–6, and 5 from Klasse 7 up.

## Topic knowledge

There is no new table. The topic label is derived from the existing knowledge model
(`evidence_summary`):

- **`not_assessed`:** no evidence yet, or the uncertainty is too high.
- **`beginner`, `developing`, `proficient`, `advanced`:** set by the mastery score.
- **Guards against over-reading one answer:**
  - Any topic with fewer than two graded answers is `developing`, whether that one answer
    was right or wrong.
  - `advanced` also needs the evidence weight the knowledge model requires for "mastered".

The evidence behind each estimate is already stored: `Attempt` holds the score, hints and
diagnosis tag, `MasteryHistory` holds each change and its reason, and `ConceptPrerequisite`
holds the gaps.

The **learner pattern** separates five cases:

- `no_evidence`: never studied.
- `prerequisite_gap`: studied, but a prerequisite is missing.
- `execution_errors`: understands the idea, but calculations slip.
- `needs_independence`: can follow examples but relies on hints.
- `mastered`: has repeatedly solved problems independently.

`conceptual_gap` (the idea itself is misunderstood) and `progressing` (no clear pattern)
cover the rest.

## The turn

**Chat controls.** The chat panel has *Simpler explanation*, *Explain step by step*,
*Give me another example* and *Make it harder*. Each button sends `control` to `/api/chat`
with its visible label as the message. A typed request ("einfacher", "Ich verstehe das
nicht", "make it harder") is detected the same way. "I don't understand" also tells the
tutor not to rephrase the same explanation.

**A different representation each time.** Every "simpler" or "I don't understand" on one
topic names the next entry in `REPRESENTATIONS`: something concrete, then a much smaller
example, then the prerequisite, then the student's own explanation. A second "I still
don't understand" therefore never gets the first explanation reworded. A correct answer
without hints restarts the rotation.

**Direct answers.** "Just tell me the answer" is detected too. For the open test question
the tutor keeps assessment integrity and gives one hint; for anything else it answers
first and then explains why.

**Session state, per topic.** Requests and graded answers adjust
`session["teaching"]["topics"][<concept>]`, which holds a bounded offset (−2 to +2), streaks,
the last focus and a confusion count. They never touch the long-term `ConceptMastery`
record. Each topic has its own state, so "simpler" on fractions does not simplify the next
topic in a mixed practice session.

**Graded answers.** Each graded answer in `/api/answer` updates the session state through
`after_answer`:

| Answer | Effect on the next explanation |
|---|---|
| Calculation slip | Same level; fix the step and show how to check it. |
| Misconception | One level down; explain the idea a different way. |
| Prerequisite gap | One level down; rebuild the prerequisite first. |
| Several wrong in a row | Simplify, find the missing prerequisite, give targeted practice. |
| Correct but unsure | Check the underlying idea with one short why-question. |
| Correct, with hints | Reduce the hints and support. |
| Two or more correct in a row, without hints | Earns the level back; a third raises it one step. |

Answers the planner marks as not counting (for example a flawed question) move nothing.

## Where it applies

- **`/api/chat`:** the topic is the open question's concept and subject, falling back to the
  lesson's focus concept. A known concept is looked up exactly: if it has no row, the topic
  is unassessed, and words in the chat message never borrow another topic's mastery. The
  chat context also carries the learning objective, the worked example (so "why?" can refer
  to its steps) and each graded answer's student-facing diagnosis note.
- **`/api/answer`:** the evaluation feedback and correction use the plan.
- **`/api/analyze`:** a new lesson has no concept yet, so the study goal is matched against
  the student's stored topics.

The block is about 900 characters and goes before the subject notation rules, so the LaTeX
mandate stays last. It contains one level and one pattern, never a history dump.

If mastery data is missing or cannot be read, the plan falls back to the grade alone.

## Teaching-quality benchmark

`tests/fixtures/teaching_benchmark.json` holds 15 scenarios:

| Group | Scenarios |
|---|---|
| A | Class adaptation: addition in Klasse 2, negative numbers in Klasse 9 |
| B | Topic adaptation: weak and mastered fractions in Klasse 11 |
| C | A distributive-property misconception |
| D | A calculation slip |
| E | The four controls |
| F | Follow-up questions: "why?", "I still don't understand" |
| G | Integrity, curriculum honesty, verified arithmetic |

`tests/teaching_benchmark.py` builds each scenario through the real routes. Setup AI calls
come from fixtures; the measured chat turn goes to the reply source. Each scenario is
checked in two ways:

- **`prompt_expect`:** what the app must send (the level, the directives, and what the
  context must or must not contain). This is exact, because Python decides it.
- **`expect`:** the input to `learnova/evaluation/teaching_rubric.py`, which scores a reply 1–5
  on correctness, clarity, difficulty, relevance and usefulness.

The rubric's hard failures are:

- a wrong calculation (equations in replies are checked with the diagnostics arithmetic
  evaluator);
- revealing the open question's answer;
- claiming curriculum coverage without a curriculum excerpt;
- stating a known wrong value.

`tests/test_teaching_benchmark.py` checks that every scenario's prompt conforms, and that
the rubric separates the hand-written good reply from the weak one.

```bash
python scripts/teaching_benchmark.py --out after.json                      # free, no AI
git worktree add ../baseline <commit>
python scripts/teaching_benchmark.py --app-root ../baseline --out before.json
python scripts/teaching_benchmark.py --live --out live.json                # real model, costs tokens
python scripts/teaching_benchmark.py --compare before.json after.json
```

The rubric is a regression proxy. It does not measure learning: longer, simpler or
higher-scoring answers are not evidence that students learn more. That evidence is
performance on new, similar practice questions after a session, compared between
variants.

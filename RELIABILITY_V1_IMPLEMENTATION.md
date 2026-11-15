# Reliability V1: mathematical accuracy and trustworthy lessons (2026-11-14)

Nothing was committed, pushed or deployed. There are no database migrations.

## 1. Previous problems (from REAL_WORLD_ACCEPTANCE_AUDIT.md, risk R2)

- **Wrong calculations, presented as the student's notes.** A Short Lesson on Tageszinsen,
  written by `gpt-oss-20b`, contained:
  - `100·4·10 ÷ 36000 = 0,0111 €` (correct: 0,11 €);
  - "Setze Z zurück in die Formel ein – du solltest wieder K erhalten" (false);
  - `1200·3·90 ÷ 360 = 90 €` (correct: 900 €).
  - All three were labelled "Aus deinen Notizen" (from your notes).
- **A nonsensical "Typische Fehler" block.** A project lesson's "wrong" line was identical to
  its "right" line, and a correct solution was called a mistake.
- **Invented page citations.** "Aus deinen Notizen S. 42" was shown, although 42 is a
  textbook exercise number the Kompetenzraster names. The project had only pages 1–2.
- **A Kompetenzraster row counted as notes.** For a topic only the Kompetenzraster names, the
  row's words "supported" every sentence that repeated them.

## 2. Root causes

| Problem | Cause |
| --- | --- |
| Wrong calculations | Lessons ran on the lightweight model (`GROQ_TUTOR_MODEL`, pinned at the Short Lesson call site). No calculation was ever checked; `teaching_rubric.arithmetic_errors` existed, but only for the offline benchmark, and it raises false alarms on "1200 €" and "324 000" |
| "From your notes" on invented content | `guard_parts` checked only explaining parts (why, explanation, rule, …) sentence by sentence. Worked examples, mistakes, exercises, summaries and objectives kept the model's own claim |
| "S. 42" | `resolve_part_pages` dropped the unknown number from the labels but kept the raw `source_pages`, which the page shows when there are no labels (fixed during the audit, A-4). Short Lessons never checked page numbers against the photos sent |
| Kompetenzraster as notes | The project lesson's notes source included the Kompetenzraster pages |

## 3. What changed

### 3.1 Model configuration (`learnova/config.py`, `learnova/ai_services/routing.py`)

| Setting | Default | Use |
| --- | --- | --- |
| `AI_LESSON_MODEL` | `GROQ_ANALYSIS_MODEL` (`openai/gpt-oss-120b`) | Writes every lesson: Short Lessons, project topics, Continue Learning, practice and review lessons. A planned adaptive practice session keeps `GROQ_TUTOR_MODEL` |
| `AI_MATH_VERIFY_MODEL` | `AI_LESSON_MODEL` | The one correction call for a lesson whose calculations failed the check |
| `FEATURE_LESSON_MATH_CHECK` | `true` | Turns the check off without a code change |
| `AI_MATH_CORRECTION_ATTEMPTS` | `1` (0–2) | Correction calls per lesson; 0 = check and flag only |

- Chat (`ASSISTANT_MODEL`, `AI_TUTOR_CHAT_MODEL`) is unchanged.
- Any `provider:model` the existing provider registry knows can be used (`gemini:…`,
  `openai:…`).
- **Fallback.** The lesson call keeps the existing routing behind the lesson model: the
  standard Groq model, then the configured slower or emergency models. A rate limit
  therefore still gives a lesson, marked "degraded" as before.
  - This is not a silent downgrade of accuracy: the calculation check runs on every lesson
    whichever model wrote it.
- **The correction call is strict** (new routing signal `strict_model`). Its route is the
  requested model only. If that model is unavailable, the lesson keeps its flagged
  calculation; a weaker model never answers in its place.

### 3.2 Calculation verification (`learnova/lessons/math_check.py`, new; pure Python)

- **Reading.** The module reads calculation chains from every part's body, items and
  answer, and from the explanation, the worked example and the tips.
  - Question options are not read: multiple choice offers wrong values on purpose.
  - Lines that present a mistake on purpose ("Falsch: …") are not judged; their "Richtig"
    counterpart is.
- **Parsing.** Its own tokenizer and recursive-descent parser work over `fractions.Fraction`,
  with `decimal.Decimal` for rounding. There is no `eval`, `exec` or `ast`, and exponents
  and expression lengths are bounded.
- **Formats understood:**
  - German and English numbers (decimal comma or point; "324 000", "1.200", "1,500"; a
    number with two possible readings keeps both);
  - LaTeX (`\frac`, `\sqrt[n]`, `\cdot`, `{,}`, `^{…}`) and Unicode (·, ×, ÷, :, −, ², ⁿ, √,
    ½, subscripts);
  - scientific notation (`3 · 10^8`, `1,5e-3`);
  - percentages (as a fraction, or as the number p of a formula that divides by 100 itself);
  - implicit multiplication.
- **Units with dimensions:**
  - length, area and volume; mass; time (s, min, h, Tag, Woche);
  - €, ct; N, J, kJ, W, kW, Wh, kWh, Pa, V, A, mA, Ω, Hz;
  - compound units such as km/h, m/s and m/s².
  - Jahr and Monat have no fixed size (360 or 365 days), so they are never converted.
- **Each check is classified as one of:**
  - `ok`;
  - an error: `wrong_result`, `decimal_place` (a power of ten off), `rounding`, `sign`,
    `unit_mismatch`, `division_by_zero`, `answer_mismatch` (the worked example's answer
    against its last step), or `rearrangement` (a formula over the teacher's variables that
    is no rearrangement of it, decided by values at fixed sample points);
  - a warning: `unit_missing`;
  - `unverifiable`.
- **Never accepted as verified:** a side with an unknown word, a variable without a value, a
  number whose readings disagree, or a result too large to compute.
- **Equivalent forms are accepted:** `2·(3+4) = 2·3 + 2·4`, `0,5 = 1/2`, rounded values to
  the places written, both rounding conventions, and `≈`.
- **Known values:** values a part assigns ("K = 1200 €, p = 3, t = 90") make
  `Z = K · p · t : 36000 = 9 €` checkable.

### 3.3 Verification states

| State | Meaning |
| --- | --- |
| `VERIFIED` | At least one calculation recomputed; all correct; nothing in the lesson left unreadable |
| `PARTIALLY_VERIFIED` | Some recomputed and correct; some could not be checked (or a unit is missing) |
| `UNVERIFIED` | No calculation could be checked, or the lesson has none |
| `NEEDS_REGENERATION` | At least one calculation is wrong (after correction, it is what is left) |

The state is stored on the lesson (`lesson["verification"]`: state, checked, errors,
errors_found, corrected, corrected_by_model, patched, correction_calls, teacher_formulas,
teacher_formulas_used). It is logged as numbers only: `lesson.math_check state=… checked=…
errors_found=… errors_left=… corrected=… calls=…`.

### 3.4 Pipeline (`app.verify_lesson_calculations`)

This runs after the model's lesson is parsed, in `_generate_saved_practice` (every saved,
project and Continue Learning lesson) and in `/api/analyze` (Short Lessons):

1. Check the lesson.
2. If it is `NEEDS_REGENERATION`: one correction call to `AI_MATH_VERIFY_MODEL` (strict).
   - The call carries the lesson JSON, each wrong calculation and its recomputed value, and
     asks to change nothing else.
   - The answer is kept only if it is a complete lesson with fewer errors.
3. If a result is still wrong: replace that one number by its recomputed value, but only
   when the check knows the exact value and the wrong number occurs exactly once in that
   text. Then check again and keep the change only if errors went down. Otherwise nothing is
   replaced: no guessed answer.
4. Mark each part that has calculations:
   - `math: "verified"`: all of them recomputed and correct;
   - `"corrected"`: correct after step 2 or 3;
   - `"needs_check"`: still wrong.
5. **What the student sees** (`static/app.js`):
   - verified and corrected parts get a quiet chip "✓ Von Learnova nachgerechnet" (checked by
     Learnova);
   - a `needs_check` part gets one sentence: "Learnova konnte diese Rechnung nicht
     bestätigen. Rechne sie bitte selbst nach." (Learnova couldn't confirm this calculation;
     please work it out yourself);
   - parts without calculations show nothing new.
   - The translations are in German, French and Spanish.

**Cost.**
- A correct lesson costs no extra call.
- A lesson with a wrong calculation costs at most `AI_MATH_CORRECTION_ATTEMPTS` (default 1)
  more calls of about the lesson's size. It sends the lesson JSON, roughly 2–3k tokens in
  and ≤ `LESSON_TOKEN_LIMIT` out.
- The check itself is local and runs in milliseconds.
- Chat messages are not checked.

### 3.5 Teacher formulas

- **The rule.** `CALCULATION_RULE` (prompt `lesson_generation:v11`) asks for:
  - the formula first, then the same formula with numbers and units;
  - one operation per step;
  - "=" only between equal values;
  - rounding only at the end, stating the places;
  - ":" kept where the notes write ":".
- **Where it applies.** It is added to the lesson instructions (`lesson_instructions`, the
  Short Lesson call) only, not to chat. The existing `FORMULA_TEACHING_RULE` (the notes'
  formulas first, in their notation; alternatives labelled "Additional tip") is unchanged.
- **The check.** The notes' formula lines are passed to the check as the teacher's formulas.
  These are the formulas in the lesson prompt's notes, or in a Short Lesson's photo reading.
  - A formula in the lesson over the same variables must be a rearrangement of one of them.
  - Whether each teacher formula is used is recorded (`teacher_formulas_used`).

### 3.6 Source attribution

- **Worked examples, mistakes, exercises, summaries, objectives** (`lesson_context.guard_parts`):
  "Aus deinen Notizen" (from your notes) stays only when the pages tell the part. That means
  either the existing `retold` test (≥ 50 % of ≥ 8 content words on the pages), or a worked
  example or exercise whose numbers are the notes' numbers (at least two numbers besides
  0–10, all on the pages). Otherwise the part becomes "Von Learnova ergänzt" (added by
  Learnova) and loses its page citation.
- **Explaining parts** keep the existing sentence-level check.
- **Kompetenzraster pages are goals, not notes:** a project lesson's "from your notes" source
  leaves them out. Their page labels are still available.
- **Page numbers:**
  - project lessons keep only numbers their pages have (A-4);
  - Short Lessons keep only 1…(number of photos sent).
- **Verified calculations** are shown separately ("✓ Von Learnova nachgerechnet").

## 4. Files changed

- **New:** `learnova/lessons/math_check.py`, `tests/test_math_check.py`,
  `tests/test_lesson_reliability.py`, `RELIABILITY_V1_IMPLEMENTATION.md`.
- **Changed:**
  - `app.py`: `CALCULATION_RULE`, `verify_lesson_calculations` and helpers, lesson model at
    both lesson call sites, Kompetenzraster pages left out of the notes source, Short Lesson
    page bound;
  - `learnova/config.py`;
  - `learnova/ai_services/routing.py` (`strict_model`);
  - `learnova/ai_services/prompts.py` (`lesson_generation` v11);
  - `learnova/lessons/lesson_context.py` (attribution);
  - `static/app.js` (chip and note);
  - `static/css/learnova-study.css`;
  - `learnova/translations/catalog.py` and `data/fr.json`, `data/es.json` (2 strings each, 2
    frontend keys; key counts checked);
  - `.env.example`;
  - tests whose pinned version moved to v11: `tests/fixtures/ai/{de,en}/valid.json`,
    `tests/test_lesson_prompts.py`, `tests/test_live_final_lessons.py`.

## 5. Tests

See section 8 (filled in when the runs finish).

## 6. Remaining limitations

See section 8.

## 7. Production configuration, deployment and rollback

- **Environment.** Nothing is required: the defaults apply. To pin the models explicitly,
  add to `/home/opc/learnova/.env`:

  ```
  AI_LESSON_MODEL=openai/gpt-oss-120b
  AI_MATH_VERIFY_MODEL=openai/gpt-oss-120b
  ```

  Check first that the production Groq key serves `openai/gpt-oss-120b`:
  `python scripts/live_ai_smoke_test.py --task models`.
- **Capacity.** The 120b model's tokens-per-minute limit on the production tier decides how
  many lessons a minute can be written. On the development key a lesson takes most of a
  minute's 8000 tokens.
- **Deploy.** Restart the service after deploying (no migration). Then bump `STATIC_VERSION`
  so browsers fetch the new `app.js` and CSS.
- **Rollback, without code:**
  - `AI_LESSON_MODEL=openai/gpt-oss-20b` restores the old lesson model;
  - `FEATURE_LESSON_MATH_CHECK=false` turns the check, the correction calls and the chips
    off;
  - `AI_MATH_CORRECTION_ATTEMPTS=0` keeps the check but never calls the model again.
- **Rollback, with code:** revert the files in section 4. No stored data depends on the new
  fields: old lessons have no `verification` and no `math` flag and render as before.

## 8. Results

### 8.1 Automated tests (model always a stand-in)

- `tests/test_math_check.py`, 34 tests:
  - the regression case `100·4·10 ÷ 36000 = 0,0111` → `decimal_place`, expected 0,1111;
  - correct and rounded forms (0,11 / ≈ 0,11 / 0,1111) pass;
  - decimals, fractions (LaTeX, ½), negative numbers and sign errors;
  - percentages (both readings), powers and roots (², √, \sqrt[3]), scientific notation;
  - rounding (0,83 ok; 0,84 and the cut-off 0,66 are errors);
  - multi-step chains with a wrong intermediate step;
  - division by zero;
  - equivalent forms (`2·(3+4) = 2·3+2·4`, `0,5 = 1/2`);
  - unit conversions (km/m, km/h ↔ m/s, h/s, ml/l), wrong units, a missing unit (warning);
  - currency (€/ct);
  - physics (U = R·I, F = m·a, P = U·I, E = P·t, v = s:t), multi-step physics with known
    values;
  - ambiguity: prose, variables and "1.200" are never verified;
  - an answer that disagrees with its last step;
  - question options not judged;
  - teacher-formula rearrangements and equivalence (sampling), subscripts;
  - English thousands;
  - safety: no execution, huge exponents left unverifiable.
- `tests/test_lesson_reliability.py`, 17 tests, through the real routes:
  - lesson model selected and configurable;
  - CALCULATION_RULE sent;
  - the factor-of-ten error goes back once to the verify model (strict) and the correction is
    kept;
  - provider timeout during correction → exact patch;
  - an invalid correction answer is not used;
  - an ambiguous error (the wrong value twice) is flagged `needs_check`, never guessed;
  - feature flag off; 0 correction attempts;
  - routing: lesson model then standard fallback; strict = no fallback;
  - attribution: the notes' own example keeps its label; an invented example, background and
    a list of mistakes lose it; no notes → nothing from the notes; a Kompetenzraster
    textbook page is no citation; Short Lesson page bound.
- **Existing tests changed, intended only:**
  - `lesson_generation` v10 → v11 pins (fixtures and 3 assertions);
  - one Continue Learning assertion now expects `AI_LESSON_MODEL` instead of
    `GROQ_TUTOR_MODEL` (the intended model change);
  - `test_learning_resources.py` reads `row.search_query`, the attribute renamed in the
    type-error fix (same column).
- **Three real defects found by existing tests and fixed in the code, not in the tests:**
  - "x = 2 oder x = -2" was judged as a contradiction (a plain statement is now never judged;
    a variable with two values is unknown);
  - parts derived from a page lost their page citation (now kept when ≥ 25 % of their words
    are on the page);
  - one-word parts were judged (now under 4 content words are not judged, as for sentences).
- **No false alarms** on the 13 stored real lessons in `tests/fixtures` (5 VERIFIED, 8
  UNVERIFIED with nothing checkable).
- Full suite: section 8.3.

### 8.2 Real-provider test (2026-11-14, Groq development key, scratch database)

Two Short Lessons were made as a new free student (the daily limit of 2), through
`/api/analyze`:

| Lesson | Model | Check | Result |
| --- | --- | --- | --- |
| Tageszinsen, from the Merkheft photo | `gpt-oss-120b` | 10 calculations recomputed, 0 errors | **VERIFIED**, no extra call, 4 "nachgerechnet" chips; 8 notes formulas found, the teacher's `Z = K·p·t : 36000` used |
| Prozentrechnung, typed goal | 120b refused with 429 (tokens per minute) → routing used `gpt-oss-20b` | 1 wrong calculation in "Typische Fehler" | one strict correction call (120b) fixed it → **VERIFIED**, part marked "corrected" |

- **Manual check of every number:**
  - photo lesson: 1200·3·90 = 324000, 324000 : 36000 = 9, 800·4,5·45 = 162000, 162000 : 36000 = 4,5;
  - typed lesson: 15 % of 80 € = 12 €, 68 €; 10 % of 250 € = 25 €, 225 €;
  - all correct as shown.
- **Found by reading, not by the check:**
  - the explanation says "weil ein Schuljahr 360 Tage hat"; it should be the bank year, a
    wording error with no number in it;
  - the formula is shown with a fraction bar, not the teacher's ":";
  - "Typische Fehler" in the photo lesson kept "Aus deinen Notizen" because it names 100 and
    36000. Fixed: the number rule now applies only to examples and exercises (with a test).
- **Cost:** about 2 lesson calls and 1 correction call. The 120b lesson took 17 s, the
  fallback lesson with its correction 9 s.

### 8.3 Full regression suite

**Result: 3696 passed, 11 skipped, 3 failed** (1 h 03 min, 2026-11-14). The 3 failures:

- **2 intended model pins:**
  - `test_vision.py::…photos_are_read_first_then_the_tutor_model_writes_the_lesson` asserted the
    lesson ran on `TUTOR_MODEL`; it now expects `AI_LESSON_MODEL`.
  - `test_text_request_limits.py::LessonFitTests::…413…` fits a lesson to the *small* model's
    8000-token limit. Its settings now pin `AI_LESSON_MODEL`/`AI_MATH_VERIFY_MODEL` to that small
    model, so it tests the same thing.
  - Re-run: 86 passed.
- **1 flaky test,**
  `test_tutorial_browser.py::AutoStartTests::test_finish_completes_it_and_the_next_chapter_comes_when_the_student_scrolls_to_it`:
  it fails at times in full runs (also before this work) and passes alone (re-run: passed).

**Final combined run** (2026-11-14, after Reliability V1 plus vouchers, community filters and
readability): **3754 passed, 11 skipped, 0 failed** (52 min). `instance/` was unchanged.

### 8.4 Remaining limitations

- **Only numbers are checked.** A wrong statement without a calculation ("Schuljahr" for
  "Bankjahr", "set Z back into the formula to get K") is not detected. Explaining parts still
  have only the existing sentence-level notes check.
- **Not checked:**
  - calculations written in words ("zwanzig Prozent von achtzig") or inside a table;
  - an answer to a question option (multiple choice offers wrong values on purpose);
  - equations with unknowns, such as solving 3x − 2 = 20 step by step: each step has the
    unknown, so it stays unverified. The check recomputes numbers; it does not solve.
- **A wrong method with correct arithmetic** (using 360 instead of 36000) is found only when
  the lesson states the teacher's formula over the same variables; otherwise its numbers are
  consistent and pass.
- **The patch** only replaces a wrong number that occurs once and whose value is exact. Later
  steps that used the wrong number are rechecked, and stay flagged if they no longer fit.
- **Fallback on a rate limit:** the lesson may come from the standard model (the
  "slower/limit" notice is shown as before). The check still applies, but 20b lessons need
  the correction call more often.
- **"From your notes" is still judged by words,** not by meaning: a paraphrase with few shared
  words can lose its label, and a part that reuses many words of the notes keeps it.
- **Formula notation:** the model may still show ":" as a fraction bar in project lessons.
  The rule asks it not to, and the check accepts both as the same formula.

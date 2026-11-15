# AI understanding and answer accuracy: implementation report

Written 15 Nov 2026. Nothing was committed, pushed or deployed. Design: `docs/UNDERSTANDING.md`.

## Root causes (found by inspection and a live baseline)

1. **No rule kept the student's term.** No chat, lesson or flashcard prompt said that an
   unfamiliar word must be explained as written. The 20B chat model (`openai/gpt-oss-20b`)
   filled the gap with a familiar look-alike ("Marketing-Slogan") or an invented acronym
   ("Marketing-SOG = Standard Operating Guidelines").
2. **No check after generation.** A reply about the wrong word was stored and shown unchanged.
3. **Phrases were read word by word.** "jüngster Teil der Reihe" became "the last number
   of a sequence".
4. **Ambiguity was not surfaced.** "Bank" got only its financial sense.
5. **The tutoring layer made the model add a quiz to almost every answer.** In the
   baseline, 33 of 34 pure definition questions got an unasked exercise.
6. **Broken back-suggestion budget (found during the audit).** `/api/flashcards/suggest-back`
   was refused on every call: its real input (about 1,340 tokens) exceeded an 800-token
   budget. The fix raised it to 2,600.

There was no preprocessing step that rewrote the question. The live failure came from the
model, and the prompts gave it no guard.

## What was built

- **`learnova/understanding/`** (pure, no model calls):
  - rules per task type, sent centrally through `output_contract`;
  - `find_focus` (DE, EN, FR, ES; meaning, difference and follow-up questions);
  - `focus_block`;
  - `check_reply`, which allows inflections and a one-letter typo correction;
  - `correction_note`.
- **`app.recheck_reply`:** at most one second call, made only when the check fails. It
  sends the full original request plus a hidden note and logs counts only.
- **Wired into:**
  - chat homepage (focus block, check, retry);
  - lesson tutor chat, including inside Project Mode (`tutor` mode);
  - Short Lessons (`lesson` mode, plus one regeneration when the lesson teaches another word);
  - flashcard back suggestion (`card` mode; look-alike definitions dropped; the budget bug fixed);
  - every lesson, practice, exam, grading and flashcard task (source-terms rule);
  - translation (meaning, not look);
  - all photo and page readers (rare words are read literally);
  - chat photos (an unclear photo is kept apart from a word with several meanings).
- **Versions:** 12 prompt versions bumped. The project topic map and the image readers keep
  their versions (documented in `prompts.py`), so cached data stays valid.

## Live measurement

Real chat route, configured model, isolated DB. Grading used a separate model
(`gpt-oss-120b`) with a written rubric per case. 18 cases cover the specification's table
plus a context sentence, a typo, a compound, an ambiguous word, English, French, a long
paragraph, a follow-up and a difference question.

| | Baseline (old prompts) | After | After the phrase/difference refinement* |
|---|---|---|---|
| Replies graded | 36 | 36 | 21 (7 cases × 3) |
| Misinterpreted | **6 (17%)** | **2 (6%)** | 2 (10%; both "jüngster Teil der Reihe" without context) |
| Unrequested exercise in a definition answer | **33 of 34** | 6 of 34 | **1 of 18** |
| Additional model calls (failed checks) | – | 0 | 0 |
| Input tokens per word question | ~1,540 | ~1,880 (+~340) | ~1,890 |
| Output tokens per reply | ~300 | ~242 (−19%) | ~281 |
| Mean latency | 1.5–1.9 s | 1.3 s | 1.4 s |

\* The refinement run re-tested the seven weakest cases only, so its rates are not directly
comparable with the 18-case columns.

- **Fixed:** "Marketingsog" (Marketing + Sog = the pull of advertising) and "Bank" (both senses).
- **Still weak:** the bare phrase "jüngster Teil der Reihe". It is now right 1 of 3 times
  (it was 0 of 4), and right 3 of 3 times when the sentence is given.
- **The deterministic check:** 0 false alarms in 93 replies. It caught none of the remaining
  misreadings, because they weren't look-alike substitutions. It guards against the
  Slogan-type swap the student reported. The rules and the focus block fix the rest.

## Tests

- `tests/test_understanding.py`: 46 tests covering the table, extra phrasings, the check,
  the per-task rules and their sizes, a guard that every task is covered or exempt, and the
  bounded retry through the real chat, tutor-chat, Short Lesson and flashcard routes.
- The AI test subset: 1,375 passed.
- The version guards that failed were updated to the deliberate new versions: 218 passed.

## Limits

- The check sees which word a reply explains, not whether the meaning is true. Invented
  meanings are reduced by the prompts, not caught deterministically.
- Live verification: the chat homepage was measured as above. Three other modes were each
  checked once, live, through their real routes:
  - flashcard suggestion for "Marketingsog": 200, three correct definitions (before: refused every time);
  - Short Lesson "Was bedeutet Marketingsog?": titled "Bedeutung von „Marketingsog“";
  - lesson tutor chat "jüngster Teil der Reihe": correct, the newest part of a series (gpt-oss-120b).

  The remaining modes (practice, exams, grading, readers, translation) were verified offline
  through their contracts and routes only.
- "Think harder" (gpt-oss-120b) was not measured separately.

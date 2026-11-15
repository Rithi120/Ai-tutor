# Real-world acceptance audit (2026-11-14)

This audit tested what the automated tests cannot test: the live AI models, real images,
the real Wikipedia/Wikimedia APIs and real browser workflows. The tests in `tests/` only
use stand-ins for the AI, so a passing test suite says nothing about what the real model
writes.

Nothing was committed, pushed or deployed. No API key or private user data appears in this
report.

## 1. Verdict

**Conditionally ready for a supervised classroom demonstration.**

These flows worked end to end with the real AI, after the fixes in section 4:
- Kompetenzraster upload, all 13 competencies extracted, topics and plan
- The chat's exam card (Short Lessons or a project) and pictures in the chat
- Publishing a flashcard deck
- Deleting lessons and projects

The biggest remaining risk is **mistakes in AI-written explanations**. One Short Lesson had
three calculation or logic errors, all labelled "Aus deinen Notizen" (from your notes). A
second lesson had a nonsensical "typical mistakes" section. A teacher should check lessons
shown in a demonstration until risk R2 is decided (section 6).

| Area | Result |
| --- | --- |
| Competency extraction from a multi-topic Kompetenzraster | **Pass**: 13 of 13 rows, verbatim, correct groups. It passed only after fix A-1; before it, the grid was never read |
| Short Lessons vs Project Mode in the chat | **Pass after fix A-5**: the card never appeared before; the exam date ("am Freitag") is still not understood |
| Teacher-specific formulas | **Pass with gaps**: recognised and kept in the teacher's notation; project lessons show `:` as a fraction bar |
| Explanation quality | **Fail**: factual errors in 2 of 3 lessons (section 3.3) |
| Wikimedia pictures | **Pass after fix A-2**: before it, no picture was ever shown |
| YouTube | **Pass** (no key, by owner decision: no fabricated links); the honest search links never came up in the live lessons |
| Image upload and previews | **Pass** for notes photos; the Kompetenzraster file has no thumbnail |
| Page reading by the vision model | **Fail on this key**: refused 3 of 3 times by Groq's output limit; local OCR read the pages instead |
| Publishing | **Pass**: approved in 4 s. The review flagged `:` as wrong (fixed, A-6). The primary moderation model is always refused on this key |
| Deleting and navigation | **Pass** |

## 2. How the audit was run

- **Environment.** The real app server (`flask run`) and the real Chrome browser (Playwright),
  at phone size (390 × 844), German interface, a new Klasse 8 student on the free plan
  (quotas enforced).
  - Everything wrote to a scratch database and scratch cache, AI-usage and upload folders
    under `R:/ai_tutor_suite_tmp/live/`.
  - `instance/` was not touched.
- **Models.** The configured local models, all served to this key (a zero-token check of
  `scripts/live_ai_smoke_test.py --task models`):
  - text: `openai/gpt-oss-20b`, plus `gpt-oss-120b` for analysis and deep chat;
  - vision: `qwen/qwen3.8-27b`;
  - moderation: `openai/gpt-oss-safeguard-20b`.
  - The production model settings were not checked (`render.yaml` pins need the owner).
- **Documents.** Two pages, rendered and then turned into phone photos (rotated, unevenly
  lit, JPEG):
  - **Kompetenzraster Mathematik Klasse 8b**: 3 chapters (Terme und Gleichungen, Lineare
    Funktionen, Prozent- und Zinsrechnung), 13 "Ich kann" rows, level and textbook columns.
    The row list is the answer key.
  - **Merkheft (Herr Keller)**, in a handwriting-style font, with teacher-specific notation:
    `m = (y₂ − y₁) : (x₂ − x₁)`, `W = G · p : 100`, `q = 1 + p : 100`,
    `Z = K · p · t : 36000` (360-day year), `Kₙ = K₀ · qⁿ`, worked example 1200 €, 3 %,
    90 days → 9 €.
  - The page `tests/fixtures/live_chat_2026-11-02_crop.jpg` was not needed.
- **Spending.**
  - The audit made 45 real AI calls, about 113,000 tokens, on the free Groq development key
    (cost reported: 0).
  - The chat experiments in section 3.4 called the model directly: 9 calls with the current
    prompt, 12 with the reworded one.
- **Scripts and evidence.**
  - `R:/ai_tutor_suite_tmp/live/` holds the scripts (`b1_create.py`, `b2_continue.py`,
    `b3_lessons.py`, `b4_short.py`, `b5_publish_delete.py`, `b6_chat_picture.py`,
    `c1_chat.py`, `exp_trailers.py`, `r1_media.py`), screenshots and lesson texts (`out/`),
    and the server logs (`server*.log`).
  - These files are outside the repository.

## 3. Test results

Each test lists: input and scenario, actual result, expected result, pass or fail, evidence,
and the fix.

### 3.1 Page reading and uploads

**T1. Upload previews** (create form, both photos)
- Actual:
  - The notes photo shows a thumbnail (320 × 205) with a label field.
  - The Kompetenzraster file only shows a count: "1 hochgeladene Datei + 1
    Kompetenzraster-Datei(en) bereit".
  - The project was created in 10 s.
- Expected: a preview of every selected page.
- **Pass, with a minor gap.** Evidence: `out/02_previews.png`.
- Recommendation: a thumbnail for the Kompetenzraster file too. That is a small UI feature,
  so it was not built.

**T2. Vision OCR of project pages** (`ocr_document_recognition`)
- Actual: Groq refused all 3 requests with 429 "output tokens per minute", limit 1000,
  requested 1037 and 1142. The pages fell back to the local OCR.
  - The Short Lesson photo reading (`image_understanding`) succeeded once (1499 output
    tokens, confidence 0.95). Groq's estimate of a request's output varies.
- Expected: the vision model reads the pages.
- **Fail (provider tier, not code).** Evidence: `ai.provider_failure … limit_scope=otpm
  limit=1000` in `server.log`.
- Recommendation: see risk R1.

**T3. Every uploaded page gets read** (the start page after creating the project)
- Actual before the fix:
  - Page 1 (the Merkheft) was refused by vision and read offline. The start page then
    stopped reading (review B B-1).
  - The Kompetenzraster stayed "noch nicht gelesen" (not yet read).
  - The topics were built from the notes alone, and the project was planned with
    **0 competencies**.
  - Only a tap on "Weiterlesen" (continue reading) read it.
- Actual after the fix: in one run, page 2 was read offline without an AI request
  (`ocr.offline_only project=2 page=2`), and 13 competencies were extracted on the first
  pass.
- Expected: all material is read before topics are built.
- **Fail, fixed (A-1).** Evidence: `server.log` and `server2.log`, `out/b1_log.txt`.

**T4. Local OCR quality**
- Actual:
  - The Kompetenzraster photo was transcribed essentially perfectly: every row verbatim.
  - The handwriting-style Merkheft lost 3 of 13 lines:
    - the line `y = m · x + b` came out as "(7s- = ) +.m = :bnang)";
    - `Kₙ = K₀ · qⁿ` became "K = Ko · qn";
    - the worked example came out garbled.
- Expected: a readable text for review. The page is marked "bitte prüfen" (please check).
- **Partial.** It is honestly flagged for review, but a student who does not check gets
  topics without `y = m·x + b`.

### 3.2 Kompetenzraster, topics and formulas

**T5. Competency extraction** (`competency_extraction:v2`, live)
- Actual: 13 of 13 rows extracted word for word, all verified against the page, each under
  its A/B/C heading. Tested twice, once before and once after the fixes.
- Expected: every row.
- **Pass.** Evidence: database dump in `inspect_db.py` output, workspace "Kompetenzraster ·
  13 Kompetenzen".

**T6. Row count and gap banner**
- Actual before the fix:
  - The banner said "kompetenzraster_photo.jpg S. 1 enthält 14 „Ich kann“-Zeilen, Learnova
    hat 13 gefunden".
  - The column header cell "Ich kann …" was counted as a row.
  - "Fehlende Zeilen hinzufügen" (add missing rows) would have added "Ich kann ..." as a
    competency.
- Actual after the fix: no banner.
- Expected: no gap for a complete extraction.
- **Fail, fixed (A-3).** Evidence: `out/05_part0.png`; `expected_rows()` returned 14 on the
  real OCR text.

**T7. Topic structure**
- Actual:
  - First run, notes and grid analysed separately: 16 topics. One per "Ich kann" row, plus
    3 near-duplicates from the notes: "Steigung" next to "Steigung aus zwei Punkten
    berechnen", "Prozentrechnung", "Zinsen". A one-tap merge was offered for only one pair.
  - After A-1, both pages analysed together: 14 topics, no duplicates.
  - The row "Jahreszinsen und Tageszinsen berechnen" got no topic, because the model split
    it into two topics. It is shown honestly as "Noch in keinem Thema" (not in any topic
    yet), with "Als Thema anlegen" (make it a topic).
- Expected: one topic per competency or chapter, no duplicates, every row in a topic.
- **Partial.** One topic per row (14 lessons for a 3-chapter test) is the deliberate
  spec 1.1 design (no merging). See risk R4.

**T8. Teacher formulas in the topic maps**
- Actual: `m = (y₂ − y₁) : (x₂ − x₁)`, `W = G·p : 100`, `Z = K·p·t : 36000` and
  `Z = K·p : 100` were kept in the teacher's notation, with LaTeX alongside.
  - In the first run, a grid topic cited its formulas to the Kompetenzraster page although
    they are on the Merkheft.
  - "Steigung m aus zwei Punkten" was stored as a "named formula" (the design allows
    formulas named without symbols).
- Expected: the teacher's formulas, in the teacher's notation, cited to their page.
- **Pass, with minor gaps.**

### 3.3 Lessons and explanation quality

**T9. Project lesson "Jahreszinsen und Tageszinsen berechnen"** (`lesson_generation:v10`)
- Actual: the maths is correct, it uses the teacher's two formulas and the 360-day bank year,
  and the worked example is correct (2000 € · 3 : 100 = 60 €). However:
  - The formulas show as fraction bars, not the teacher's ":".
  - They are labelled "kompetenzraster_photo.jpg S. 1", although they are on the Merkheft.
  - "Die Notizen geben folgende Formeln" (the notes give these formulas) is marked "Von
    Learnova ergänzt" (added by Learnova).
  - The text shows a stray "Euro (\€)".
- Expected: correct, in the teacher's notation, with true source labels.
- **Partial.** Evidence: `out/lesson_topic18.txt`.

**T10. Project lesson "Gleichungen mit Brüchen lösen und die Probe machen"** (the first
lesson of the plan)
- Actual: the worked example ((3x−2)/5 = 4 → x = 22/3, with the check) and the exercise are
  correct. However:
  - "Typische Fehler" (typical mistakes) shows a "wrong" line identical to the "right" line,
    then calls a correct solution a mistake.
  - The rule's numbered steps were split by the notes/added check into fragments: step 2
    appears before steps 1 and 3, and the dangling "Falls ja …" is kept.
  - Every part claimed "Aus deinen Notizen S. 42". The project has two one-page files;
    "S. 42" is the textbook exercise that the Kompetenzraster row names.
- Expected: correct mistakes, an intact rule, honest labels.
- **Fail.**
  - The "S. 42" chip is fixed (A-4).
  - The mistakes block is model output (risk R2).
  - The split rule and the label over-claim come from the Kompetenzraster row being the
    topic's only "notes" (risk R3).
- Evidence: `out/lesson_first.txt`; the stored parts carry `source_pages: [42]`.

**T11. Short Lesson from the Merkheft photo** (`/lesson`, `image_understanding` +
`lesson_generation:v10+short1`)
- Actual: done in 11 s. It keeps the teacher's notation exactly (`Z = K·p·t : 36000`) and
  reuses the teacher's own example (1200 €, 3 %, 90 days → 9 €). It has **3 factual
  errors**, all labelled "Aus deinen Notizen":
  1. "100·4·10 ÷ 36000 = 0,0111 €". Correct: 0,11 €.
  2. "Setze Z zurück in die Formel ein – du solltest wieder K erhalten" (put Z back into the
     formula and you get K again). False.
  3. The "wrong" example "1200·3·90 ÷ 360 = 90 €". The value is 900 €.
- Expected: no mathematical errors.
- **Fail (AI quality).** Evidence: `out/lesson_short.txt`.
- The repository's `teaching_rubric.arithmetic_errors` (used only by the offline benchmark)
  catches error 1. On this lesson it also raises false alarms ("1200 € · 3", "324 000"), so
  it needs tuning before it could guard production lessons. See risk R2.

### 3.4 Chat: Short Lessons or Project Mode

**T12. Plain question** ("Was ist die Steigung einer Geraden?")
- Actual: a correct, concise answer with a correct check task. No exam card.
- Expected: the same.
- **Pass.**

**T13. Multi-topic exam** ("Ich schreibe am Freitag eine Mathearbeit über Terme und
Gleichungen, lineare Funktionen und Prozent- und Zinsrechnung …")
- Actual before the fix: no card.
  - In 6 live replies to exam requests (2 subjects × 3), the model never wrote the hidden
    EXAM line. All 6 had the TOPIC line, which listed the topics.
  - The reply proposed a "2-week plan" for an exam on Friday, and wrote simple interest once
    as `Z = K·p·t` without "/100".
- Actual after the fix: card "choice", recommending Short Lessons, 4 topics, inside the free
  plan's 2 lessons a day.
- Expected: a card with Short Lessons (3–4 topics).
- **Fail, fixed (A-5).** The exam date is still empty: weekday names are not understood
  (risk R6).
- Evidence: `out/chat_exam_multi.json`, `out/exp_current.jsonl`.

**T14. Single-topic test tomorrow** (Satz des Pythagoras)
- Actual: a correct explanation, tap answers, and a "Weiterlernen" button for one lesson.
- Expected: one topic means the single lesson button, not the card (by design).
- **Pass.**

**T15. A reworded prompt** (tried, then rejected)
- Actual: with the hidden lines as a fixed template, EXAM appeared in 6 of 6 exam replies,
  but also in 5 of 6 non-exam replies.
  - The model copied the placeholder "Wikipedia title in the reply language" 3 times.
  - It wrote `CHIPS: none`.
  - It dropped TOPIC once.
- **Rejected; the prompt is unchanged.** A-5 decides from the student's own words instead.
  Evidence: `out/exp_reworded.jsonl`.

### 3.5 Pictures and videos

**T16. Wikimedia lookups** (7 terms, the app's own code, live)
- Actual before the fix: 0 of 7 pictures.
  - Wikipedia's API now gives thumbnails from `thumb.wikimedia.org`.
  - The server, the content security policy (CSP) and the page's JavaScript only allowed
    `upload.wikimedia.org`.
- Actual after the fix:
  - "Satz des Pythagoras" and "Mitose" return credited diagrams (author and CC BY-SA 4.0
    looked up).
  - The rest are correct vetoes: "Pflanzenzelle" redirects to another article with a
    bacteria photo, "Steigung einer Geraden" has no article, "Lineare Funktion" and
    "Zinsrechnung" have no image, and "Baden-Württemberg" only has a coat of arms.
- Expected: real teaching pictures with credits, and nothing fabricated.
- **Fail, fixed (A-2).** Evidence: `r1_media.py` output.

**T17. Picture in the chat** ("Kannst du mir den Satz des Pythagoras mit einem Bild
erklären?")
- Actual before the fix: the model wrote a visible "**MEDIA** / Diagramm: …" block, and no
  picture appeared.
- Actual after the fix: the Pythagoras diagram loads in the chat page (960 px), credited
  "Bild: Petrus3743, CC BY-SA 4.0 (Wikimedia Commons)", with no CSP error.
- Expected: a credited picture.
- **Fail, fixed (A-2, A-5).** Evidence: `out/10_chat_picture.png`.

**T18. Network outage** (all connections refused)
- Actual: `lookup_failed`, no exception, no picture, nothing stored as settled.
- Expected: the lesson goes on without a picture.
- **Pass.**

**T19. YouTube without a key**
- Actual: the server says videos are unavailable. No video card or link was invented in any
  reply or lesson, and model-written YouTube links are still removed.
- Expected: no fabricated videos; honest search links where a Short Lesson suggests videos.
- **Pass.** The search-link rendering was not exercised live: the model suggested no videos
  in the Short Lesson.

### 3.6 Publishing, deletion and navigation

**T20. Publishing a deck** (6 cards in the teacher's notation)
- Actual: published and approved in 4 s, public at `/community/sets/1`, safety check
  passed.
  - The primary moderation model `gpt-oss-safeguard-20b` was refused (413: this key allows
    it 2000 tokens a minute), so the fallback `gpt-oss-120b` decided.
- Expected: published.
- **Pass, with risk R5.**

**T21. AI quality review of the deck**
- Actual before the fix: 4 "grammar" warnings, "das Zeichen ':' ist nicht üblich" (the ":"
  sign is unusual), one on every formula, each suggesting "/".
- Actual after the fix: suggestions that only change the division sign are dropped.
- Expected: no warning for correct school notation.
- **Fail, fixed (A-6).**

**T22. Deleting from the dashboard** (browser, real confirmation dialogs)
- Actual: "Lektion „Tageszinsen verstehen und anwenden“ … löschen? Das kann nicht
  rückgängig gemacht werden. Deine XP bleiben erhalten." (delete the lesson? this can't be
  undone; your XP stays). Likewise for the project ("… Deine XP und deine Karteikarten
  bleiben erhalten").
  - Afterwards the flash says "Lektion gelöscht." / "Projekt gelöscht.".
  - The database has no rows left for the project, lessons, attempts or plan, and no
    foreign-key problems.
  - The independent flashcard set remains.
- Expected: the same.
- **Pass.**

**T23. Back button and old links**
- Actual: Back returns to the dashboard; `/projects/1` and its start page answer 404; the
  flashcard set still opens.
- Expected: no broken page.
- **Pass, with a minor gap.** The 404 is the plain text "Project not found" (existing
  behaviour), not a styled page.

## 4. Fixes made (only reproduced problems)

| # | Problem | Fix | Files | Tests |
| --- | --- | --- | --- | --- |
| A-1 | The start page, Continue reading, quick-start and `/reanalyze` stopped at the first page read offline because the AI was busy, so later pages (the Kompetenzraster) stayed unread | After such a page, the rest of the run is read offline with no AI request (`offline`). The busy model is still asked only once; a "waiting" page still ends the run. New message: "Die KI ist gerade ausgelastet, deshalb hat Learnova deine Seiten vorerst offline gelesen …" | `app.py`, `static/js/project-start.js`, catalogues (de/fr/es, key count unchanged at 3334) | `test_reading_honesty.py` (asked-once kept; later pages now read and marked for a later AI re-read; new tests for `{"offline": true}` and for "Try again" overriding it) |
| A-2 | Wikimedia thumbnails (`thumb.wikimedia.org`) were refused, so no picture was ever shown | Host allowed in the server check, the CSP `img-src` and `resources.js` | `learnova/media_enrichment.py`, `learnova/web/security.py`, `static/js/resources.js` | `test_media_enrichment.py` (live-recorded URL; every accepted host is allowed by the CSP), `test_learning_resources_browser.py` |
| A-3 | The column header "Ich kann …" was counted as a row (false gap banner, bogus "add missing rows") | Header cells with no statement are skipped | `learnova/exam_prep/competencies.py` | `test_exam_prep.py` (real OCR text, the "…", ":", "I can" and joined-header forms; a row after a colon still counts) |
| A-4 | A cited page the lesson does not have ("S. 42") was still shown from the raw number | `resolve_part_pages` keeps only the numbers that resolve | `learnova/lessons/lesson_context.py` | `test_subject_strategies.py` |
| A-5 | The chat never showed the exam card or pictures (the model omits the EXAM and MEDIA lines), and a bare "**MEDIA**" heading leaked into the reply | Fallback: exam words in the student's message plus 2 or more topics in the TOPIC line give the exam topics; an explicit picture request offers a diagram of the topic, through the existing veto. A bare keyword heading and its value line are hidden. The prompt is unchanged (still `assistant_chat:v7`) | `app.py`, `learnova/assistant/study_format.py`, `learnova/assistant/resources.py`, `learnova/assistant/formatting.py` | `test_study_format.py`, `test_learning_resources.py` |
| A-6 | The AI review flagged ":" (the school division sign) as a grammar error | Warnings whose suggestion only changes the division sign are dropped; real corrections stay | `learnova/community/service.py` | `test_community_reports.py` |

- No migration, no new endpoint, no new setting and no model change was made.
- One request field was added: `{"offline": true}` on the existing per-page reading route.
- Docs updated: `docs/PROJECT_MODE.md`, `docs/VISION.md`, `docs/AI_ROUTING.md`,
  `docs/LEARNING_RESOURCES.md`, `docs/ASSISTANT.md`.

### Regression tests

- After each fix, its area's test files: 98, 138, 23, 227, 134 and 8 passed.
- Full suite after all fixes: see section 7.

## 5. Failed scenarios (still open)

- **T2:** vision OCR refused by the provider's output limit on this key (R1).
- **T10, T11:** factual errors and a nonsensical "typical mistakes" block in AI-written
  lessons (R2).
- **T10:** "Aus deinen Notizen" over-claimed for Kompetenzraster-only topics, and a split
  rule (R3).

## 6. Remaining risks and required decisions

| # | Risk | Severity for a demo | What would fix it (owner decision) |
| --- | --- | --- | --- |
| R1 | On this Groq key the vision model refuses every project page reading (1000 output tokens a minute), so pages are read by the local OCR; handwriting is read poorly (T4). The production tier is unknown | High if the demo uses handwritten notes; low for printed sheets | A paid Groq tier, or `AI_VISION_FALLBACK_MODEL` on the configured OpenAI or Gemini key (cost and privacy decision), or `AI_VISION_OUTPUT_TOKEN_CEILING=950` (a full Kompetenzraster does not fit in 950 tokens) |
| R2 | AI explanations contain arithmetic and logic errors labelled as the student's notes (T10, T11) | **High** | Generate lessons with `gpt-oss-120b` (`GROQ_TUTOR_MODEL`; mind the token-per-minute limits), and/or switch on an arithmetic check of worked numbers (exists for the benchmark, needs tuning), and/or the deferred self-check call |
| R3 | For a topic that only the Kompetenzraster names, the grid row counts as "notes": lessons claim "Aus deinen Notizen", and the honesty check can cut a numbered rule into fragments | Medium | Treat Kompetenzraster rows as goals, not notes content, when labelling lesson parts |
| R4 | One topic per Kompetenzraster row (14 lessons for a 3-chapter test); a row the model splits into two topics stays without a topic until the student taps | Medium | Group rows under their chapter, or attach a row to every topic it covers |
| R5 | The primary moderation model is always refused on this key (2000 tokens a minute), so every publish takes the fallback path | Low | A tier with more capacity for `gpt-oss-safeguard-20b`, or make the fallback the primary |
| R6 | "am Freitag" / "nächsten Dienstag" are not read as an exam date, so the card's timing rules do not apply | Low | Weekday names in `exam_date_from_text` |
| R7 | Chat content slips: a "2-week plan" for a Friday exam; simple interest once without "/100" (T13) | Medium | Same as R2 |
| R8 | Project lessons show the teacher's ":" as a fraction bar (the Short Lesson kept ":") | Low | Render the stored page notation in lesson formulas |
| R9 | Not verified live: YouTube with a key (unset by decision), the honest search links, a real phone camera, migrations 043–047 on PostgreSQL, bfcache Back after publishing | — | Before production, as in CLASSROOM_READINESS_IMPLEMENTATION.md §11 |
| R10 | Local development only: no `SECRET_KEY` in `.env`, so every server restart logs everyone out (production generates one) | — | Set `SECRET_KEY` in the local `.env` |

## 6a. R2 update - Reliability V1 (2026-11-14)

Details are in `RELIABILITY_V1_IMPLEMENTATION.md`.

- **Lessons** are written by `AI_LESSON_MODEL` (default `gpt-oss-120b`). Every lesson's
  calculations are recomputed by `learnova/lessons/math_check.py`, with no `eval`.
- **A wrong calculation** is sent back once to `AI_MATH_VERIFY_MODEL`, which never falls back
  to a weaker model. A result still wrong is replaced only when its value is exact and
  unambiguous; anything else is flagged on its part for the student.
- **"Aus deinen Notizen"** (from your notes) is no longer taken on the model's word for
  examples, mistakes, exercises, summaries and objectives. Kompetenzraster rows no longer
  count as notes.

**Status of R2:**

| Finding | Status |
| --- | --- |
| `100·4·10 ÷ 36000 = 0,0111 €` | **Detected** (`decimal_place`, expected 0,1111) and corrected; regression test |
| Wrong arithmetic shown as verified | **Not possible:** VERIFIED only when every calculation was recomputed; an unreadable one is at most PARTIALLY_VERIFIED |
| "Typische Fehler" nonsense | **Partly:** wrong numbers in it are found; a "mistake" that is no mistake, without numbers, is not |
| "Setze Z zurück … K erhalten" (logic, no number) | **Not detected:** not a calculation |
| "Aus deinen Notizen" on invented content | **Fixed** for examples, mistakes, exercises, summaries and objectives, and for Kompetenzraster-only topics |
| "S. 42" | **Fixed** (A-4 for projects; Short Lessons now keep only the photos' page numbers) |

**Live:** the Merkheft Short Lesson, written by 120b, was VERIFIED (10 calculations, 0
errors). A typed Short Lesson fell back to 20b on a rate limit; it had 1 wrong calculation,
which was corrected with one call.

**R2 is now Medium (was High).** Calculations are safe to show. Wording and logic still need
a teacher's eye.

## 7. Regression run

Full suite after the fixes: **3647 passed, 11 skipped, 2 failed** (48 min 46 s,
`instance/` unchanged).

- `test_project_start_browser.py::…busy_stops_the_reading` failed because it pinned the old
  behaviour that A-1 changed: reading stopped after the busy photo.
  - Updated to the new behaviour, keeping its main rule: the busy model is asked once.
  - It now also checks that all 3 photos are read, that photos 2 and 3 are sent with
    `{"offline": true}`, and the new message.
  - Re-run: **passes**.
- A tutorial browser test failed in this run. In the re-run of the two files, a different
  tutorial test failed and the run hung for almost 4 hours.
  - Run alone, both tutorial tests **pass** (2 passed in 59 s).
  - The tutorial code was not touched by these fixes, and this test has been flaky before.
  - Recorded as a flaky browser test, not a regression.

## 8. Recommendation

For a classroom demonstration, show:
- the Kompetenzraster upload, competencies, topics and plan (with printed sheets);
- the chat's exam card and pictures;
- publishing a deck;
- deleting lessons and projects.

Have a teacher review any AI lesson before it is shown, or decide R2 first (the most
effective single change is generating lessons with the larger model). Do not present
handwritten notes until R1 is decided.

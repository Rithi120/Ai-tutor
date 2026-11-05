# Project Mode (Projektmodus)

Project Mode is the orchestration layer of Learnova's learning systems. A student uploads
notes, Learnova reads every page of every document, builds a topic map from all of them,
and then teaches each topic the way its subject is taught: explanation first, then the
key terms (Fachbegriffe) and flashcards, then guided and independent practice, tests,
revision and a final practice exam. The student mostly taps **Next**.

The rebuild of November 2026 ("teaching engine") replaced the generic question-and-answer
lesson and the single-pass topic extraction. It came from one bug report: six Ethics
papers were uploaded, one was read, three topics were found and one was used. The causes
and the fixes are listed under [Why six papers became one](#why-six-papers-became-one).

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
changed.

**Creating a project** ("Neues Lernprojekt erstellen", button **Create learning
project**): name, subject, class, optional exam date, notes (photos, PDFs, camera), and -
under "Kompetenzraster and own topics" - an optional Kompetenzraster upload and topics
typed in one per line. Any one of notes, Kompetenzraster or typed topics is enough; a
project of typed topics only goes straight to the topic list. Kompetenzraster pages are
stored as teacher-marked pages, which raises the importance of the topics they cover. A
topic with no notes behind it is taught from standard school content and marked as an
addition. Submitting the same project twice within two minutes (same title, subject and
files, or the same typed topics) opens the first one instead of making a copy.

**Subject**: whatever the student picks or types ("Ethik", "Mathe", "Bio LK") is folded
to one of the 23 canonical subjects in `learnova/lessons/subjects.py` (`SUBJECT_CHOICES`)
before it is stored; a combined subject the catalogue does not know
("Geschichte/Gemeinschaftskunde") is kept as typed and gets a combined strategy.

**Class**: a project's class overrides the student's own grade for that project's lessons
and curriculum matching (`g.project_grade`, carried in the lesson session).

## The student flow

1. **New learning project** (`/projects`). The form above.
2. **Reading** (`/projects/<id>/start`). Every document has its own row and a dot per
   page; pages are read one request at a time ("Reading your pages"), then the topics are
   worked out ("Building your lesson", with "part x of y" while the analysis runs). See
   [Reading every page](#reading-every-page) and [The topic map](#the-topic-map).
   The topics are worked out in short requests too: the page loops
   `POST /projects/<id>/analysis/step` (at most `ANALYSIS_STEP_CALLS` extraction calls each,
   every step stores a complete topic state) until the server says `finished`, then calls
   quick-start, which finds every batch cached. One analysis per project runs at a time (a
   database lease, migration 041): a second tab or a retry after a proxy timeout gets
   202 `analysis_running` and waits ("Learnova is already working out the topics of this
   project…"); a 502/504 on a step is waited out, not shown as a failure; a reload carries
   on by itself. The step allows 120 answered steps an hour per account; a 202 poll does
   not count against them (its own budget is 600 requests an hour), and the step's own 429
   stops the page with its message instead of falling back to quick-start's one long
   request (review A F6). Without JavaScript the button posts to quick-start, which does it all in
   one request as before. **Update topics** opens this page with `then=topics` and ends on
   the topic list, never in a lesson.
3. **Check the material and confirm the topics** (`/projects/<id>#topics`, stage B). Nothing
   starts behind the student's back. The page shows:
   - the **material check**: one row per uploaded document ("Document 5: Needs OCR
     review"), what is missing and a link to fix or retry those pages;
   - "We found N topics in your notes": **every topic is included**; the student unticks
     what they do not need, or uses **Select all** / **Select none**;
   - topics that may be the same, each with the reason ("Both come from p. 3-4 and share
     the terms Pflicht, Maxime") and a one-tap **Merge into one topic**;
   - topics left out by the topic cap, each as a one-tap "+ title" chip that brings it
     back grounded on its own pages;
   - **Rename or merge topics** and **Add a missing topic** (with a page picker; pages no
     topic covers yet are ticked already).

   **Start learning** saves the choice, plans every project and opens the first lesson.
4. **Learn and revise** (the project overview, stage C). A big **Next** button sits at the
   top, then the sections listed under [The project overview](#the-project-overview).

On the Overview (`/dashboard`), the "Your learning plan" card shows the next action across
all projects, today's combined workload, upcoming exams and overload warnings.

## Reading every page

`recognize_single_project_page` (app.py) reads one page. Each reading records where its
text came from in `recognition_json["text_source"]`:

| `text_source` | When | Review |
|---|---|---|
| `text_layer` | A born-digital PDF page whose own text passes `native_text_quality`: no vision call at all. Also the fallback below. | No for a clean first pass; a fallback reading is put in the review band. |
| `vision` | Handwriting, photos and scans: the vision model in up to three image modes (enhanced, grayscale, high-contrast), plus the close-up second look at unclear words. | When the confidence is low, or the reading has doubts (`reading_doubts`, below). |
| `local_ocr` | The offline engine (RapidOCR, `learnova/ocr/local.py`), when vision gave nothing or was busy and the page has no usable text layer. After a busy provider it is provisional and read by vision later (`vision_pending`, below). | Always: it misreads handwriting, so a person checks it. |
| `manual` | The student typed the text ("Type or retake it"). | Never. |
| `blank` | A PDF page without a text layer that renders as empty paper (`page_is_blank`). Stored as read with no content, no AI call. Photos are never treated as blank. | Never; "Try reading again" still sends it to vision. |

What a failure costs is bounded:

- A **provider-wide error** (rate limit, quota, timeout, the student's own limits) stops
  the image modes at once instead of trying the next two.
- A free account with fewer than `VISION_MIN_REMAINING_REQUESTS` requests left this hour
  is not read at all: the page is left **waiting** ("Learnova paused reading so you do not
  run into the hourly AI limit"), so it never trips the hourly cap and its cooldown.
- A **temporary** failure (busy provider, timeout, network) also leaves the page waiting;
  after `MAX_PROVIDER_WAITS` waits it is failed. A limit wait does not count against that.
- When vision gives nothing, the page's own text layer and then the offline OCR are tried
  before the page is given up. While the provider is only busy, a scanned page is not
  stored from its text layer (the handwriting would be lost for good); it waits.
- A **failed** or **unreadable** page is only read again when the student taps **Try
  reading again**; the start page and quick-start never retry it by themselves.
- A page stuck at "recognizing" by a killed request counts as not read after
  `RECOGNIZING_STALE_AFTER` (10 minutes).

After a waiting page the start page stops reading, builds the topics from what was read,
and the overview shows "N page(s) in M document(s) are not read yet - topics from them are
still missing" with one **Continue reading** button. A second quick-start reads the rest
and adds their topics (`project_pages_to_analyse`).

### Reading honesty (FIX_PLAN PF3, live run 2026-11-01)

- **A doubtful vision reading needs review (D2).** The page confidence is the mean of the
  model's own block confidences, so a very messy page (word error rate 0.55, invented words)
  scored 0.806 "high" and its document said "Fully analysed". `reading_doubts`
  (`learnova/ocr/service.py`, pure) now looks at the reading itself: two or more usable text
  blocks below 0.80 or any below 0.45 (`uncertain_words`), or two or more broken-off
  fragments in the running text - a line of one to three letters without a digit or
  operator once its list marker is gone ("mut", "en!"), or a word split by a colon
  ("verge:sen"; URLs, ratios and the gender colon "Schüler:innen" excepted)
  (`fragmented_text`). Such a reading is stored at most 0.79, `confidence_status`
  "unclear", `recognition_json["doubts"]`, with "Some words on this page may be misread.
  Please check them before you learn from it." The page then reads "Needs OCR review", never
  "Fully analysed". Replayed on the 22 stored live readings it flags exactly that page
  (`tests/fixtures/ocr/live_sample_2026-11-01.json`, `tests/test_reading_doubts.py`).
- **Nothing from an unchecked flagged page is shown as the notes' own.** A page whose machine
  reading needs review (offline OCR, "unclear", below 0.45) and that the student has not
  confirmed (`page_reading_unverified`) gives no flashcards yet: `terms.build_cards(...,
  unverified_pages=)` withholds a card whose every source page is such a page (counted in
  `analysis_json["flashcard_sync"]["waiting_for_check"]`), and its definitions are no
  distractors. Key terms from it say "From a page that still needs checking" instead of
  "From your notes". Saving the page's text unchanged ("Save correction") or confirming the
  review page syncs the waiting cards at once; corrected text waits for "Update topics".
- **"Try reading again" reads again (D3).** The retry passes the gateway's `fresh` (no
  cached reading, no coalescing; the new reading is cached) and starts with another image
  mode: the mode of the stored reading (`recognition_json["variant"]`) goes last, because at
  temperature 0 the same picture gives the same words. Automatic reads still use the cache.
- **Offline OCR after a busy provider is provisional (D4).** When a temporary failure (429,
  timeout, the daily limit) made the offline OCR stand in, the reading is kept (its topics
  flow) with `recognition_json["vision_pending"]` and "Read with offline text recognition
  while the AI was busy. Learnova reads it again with the AI later – or check it now."
  `page_vision_reread_due` lets the start page and quick-start read it by vision again after
  `WAITING_RETRY_AFTER` (an hour after a daily limit), at most `MAX_PROVIDER_WAITS` failed
  attempts, never while it is being read and never once confirmed or typed. A failed re-read
  keeps the offline reading (202 "waiting" when the provider is busy, so the start page
  stops). Any new reading drops the note and the last reading's own warning
  (`store_page_recognition`); upload and picture advisories stay.
- **A busy provider is asked once per run, not once per photo (review B B-1, spec §2C).**
  A provisional offline reading counts as read, so every reading loop went on to the next
  photo and collected another refusal. Now a page read offline because the AI was busy
  (`page_read_while_busy`: the busy `vision_pending` note) stops the loops like a waiting
  page: the per-page route answers 200 with its real state plus `paused` and
  `pause_message` ("The AI is busy right now. Learnova read this page offline for now and
  paused reading the other pages – …"), and project-start.js stops sending pages;
  Continue reading, quick-start and `/reanalyze` break there; quick-start does not read
  again within `WAITING_RETRY_AFTER` of such a page (`page_recently_busy`). In the gateway,
  `ocr_document_recognition` fails at once, without a call, while its only model is cooling
  down after a 429 (`COOLDOWN_FAIL_FAST_TASKS`; the error keeps the cooldown's category and
  remaining seconds); every other task - chat photos and their close-up second look
  included - still asks the last model, as WP2 built it.
- **Short lines are fragments only in a doubtful reading (review B B-5).** `fragmented_text`
  counts only when at least one block is below 0.80: a confident vocabulary list, element
  symbols, note names or "gut"/"ok" in the margin are no misreading (hw8 has blocks below
  0.80 and stays flagged).
- **The provider's daily limit (D1, code part).** `provider_daily_limit` leaves the page
  waiting with reason `provider_daily` and "The AI has reached its daily limit for reading
  pages. Learnova continues tomorrow – you can type the page in the meantime." Such a page is
  not asked again for `DAILY_LIMIT_RETRY_AFTER` (60 minutes): not in the start page's list,
  202 without a call from the per-page route, quick-start skips reading. It never counts
  against `MAX_PROVIDER_WAITS`. The tier itself (dev org: OTPM 1000, TPM 8000) is the
  owner's configuration.

**Uploads** (`learnova/uploads/service.py`): every file and every PDF page becomes a
`ProjectPage`, up to `PROJECT_MAX_PAGES` (40) per project across all files and later
additions. A file whose bytes match a file already in the project is skipped and named
("“x.pdf” was uploaded twice; Learnova kept one copy"), never dropped silently.

- **One damaged file never costs the others** (`ingest_uploads(partial=True)`, used by
  the create form, "Add more pages", and the file routes below). Everything about a file
  that can fail - validation, the PDF inspection, the page limit, the picture check - runs
  before any row of it is written. A file that fails is kept as a document row with no
  pages (its bytes stored for a retry when 1 byte to 15 MB, `sha256` empty so it never
  counts as "already in this project") and an entry in `analysis_json["file_errors"]`
  (`{file id: {code, msgid, params, sha256, at}}`; no migration). Every message names the
  file and is translated: `unreadable_pdf`, `password`, `empty`, `image_damaged`,
  `type_mismatch`, `not_supported`, `too_large`, `page_limit` (the file that crosses the
  limit; later files that still fit are kept). When no file of a new project could be
  used and no topic was typed, nothing is saved: "None of the files could be used, so
  nothing was saved." plus one message per file.
- **The failed row** shows its reason on the start page and in the materials, with **Try
  this file again** (not for empty, oversized or unsupported files), **Replace file** (one
  tap on the file chooser) and **Remove file**: `POST /projects/<id>/files/<file id>/retry`,
  `/replace` (field `replacement_file`) and `/remove`, owner only (404), 400 for a file
  that has pages, 20 per hour per account (`user_rate_key`, review A F3: a class behind one
  school network no longer shares 20). A file read on a retry or replaced appends its pages after
  every page there is: nothing read or analysed before is read or paid for again (the
  start page reads only the new pages, the analysis only their batch). Uploading the same
  bytes again is a retry of that row, never a second row. Each of the three first claims the
  row (`claim_failed_file`: one conditional UPDATE, only while the row has no page, rowcount
  1), so of a double tap exactly one request reads the file; the other waits for its commit,
  finds nothing to claim and says "“…” is already being taken care of." (review A F7). A
  retry that meets the page limit again repeats the page-limit message, not "still could
  not be read" (F5).
- **PyMuPDF decides the page list** (`inspect_pdf`, `learnova/ocr/service.py`). The text
  stays pypdf's whenever both libraries agree on the pages (every healthy file is
  extracted as before); otherwise it comes from PyMuPDF. Truncated downloads and AES
  "restrict editing" exports that pypdf refused are accepted; a corrupted page object no
  longer shifts page 3's words onto page 2. A page MuPDF warns about that yields no text
  is `pdf_damaged`: it is never "blank", is stored as unreadable without an AI call ("This
  page of the PDF is damaged…"; Try reading again still uses vision), and blocks
  `complete`. Pages of a repaired file are `pdf_repaired`: a text-layer reading of them is
  put in the review band with "This PDF was damaged and Learnova repaired it – check that
  every page is complete." Since review A F1 (2026-11-02) such a page also counts as
  "needs review" (`PageFacts.pdf_repaired`, `page_reading_unverified`) until the student
  confirms or corrects it - a download cut off at 80 % kept only 259 of 351 characters of
  its last page and was reported "Fully analysed" - and the document's row says the repair
  note in its detail (start page and workspace). Its flashcards wait for the check like any
  flagged page. A file that needs a user password gets its own message.
- **No rendering in the upload request** (unless `PROJECT_PREPROCESS_ON_UPLOAD`). PDF pages
  keep their text layer; photos keep the camera scanner's rotation and crop in
  `recognition_json["upload_transform"]`. `ensure_processed_page_image` makes the picture
  when it is first needed (a vision read, the offline OCR, `GET …/image/processed`, a
  rotation, a block region) and stores it. Measured on the dev machine (2026-11-02): a
  40-page born-digital PDF upload 0.12 s (33 s with the switch on), 40 phone photos 0.64 s
  (102 s with the switch on). A text-layer page is never rendered at all.
- **The text-layer shortcut** is skipped for a page that is a picture, carries vector ink,
  or has raster images covering at least `PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE` (10 %) of it:
  vision reads it, and if vision is busy it waits or ends "unclear", never "read". A maths
  layer whose formulas fell apart (`text_layer_layout_lost`: six or more short formula
  fragments making up 15 % of the lines, or three U+FFFD) is stored from the text layer at
  confidence 0.6, "unclear", with a note to check the formulas or tap Try reading again.

## The material report

`learnova/projects/document_analysis.py` (pure) rolls pages up into one state per
document and one report per project; `project_material_report` (app.py) adds labels and
messages, and `GET /projects/<id>/analysis` returns it as JSON (owner only, no-store) for
the start page.

**Page reading states** (`page_state`): `read`, `needs_review` (offline OCR, unclear,
confidence below 0.45, or a page of a repaired PDF; a typed page never), `unreadable`,
`processing`, `excluded`, `duplicate`.

**Document states** (`file_status`, first match wins):

| State | Label shown | Meaning |
|---|---|---|
| `unreadable` | Could not be read | No page, or every considered page unreadable. |
| `excluded` | Left out | Every page excluded by the student. |
| `processing` | (reading label, e.g. "Waiting - reading continues later") | A page is still pending, waiting or recognizing. |
| `not_analysed` | Read, not analysed yet | Pages are read but no topic analysis has run on them. |
| `partly_analysed` | Partly analysed | Some page unreadable, or some readable page failed or was not analysed. |
| `needs_ocr_review` | Needs OCR review | Everything analysed, but a page needs its text checked. |
| `analysed` | Fully analysed | Every page read, analysed and accounted for. |

Each document row says what is missing in plain words: "3 of 4 pages read · page(s) 2
could not be read · page(s) 4 repeat another page · page(s) 3 blank · page(s) 1 not in
any topic yet", plus "the topics on page(s) x could not be worked out yet", "page(s) x
have no learning topic" and "page(s) x only belong to a topic you removed". Page numbers
are numbers inside the file (the PDF page), because that is what a student finds on paper.

The summary line reads "5 of 6 documents read · 1 need(s) checking" (a partly read
document counts as read and says so: "5 of 5 documents read (1 partly)") and "4 of 6
documents analysed".

**Completeness check.** `material_report(...)["nothing_missing"]` is true when no page is
processing or unreadable, every readable page was analysed, and every analysed content
page is cited by a topic. Excluded pages, blank pages and pages the model confirmed hold
no learning topic (`no_topic_page_ids`) never count against it. Until it is true, the
topic list says "Not all of your material is read and analysed yet (...), so topics from
the missing pages are not in this list", and the student can still start.
`material_report(...)["complete"]` additionally needs every reading verified (review A F2,
2026-11-02): while a page is flagged "needs review" (offline OCR, an unclear or
low-confidence reading, a page of a repaired PDF) it stays false, the material check keeps
"Fix or retry these pages", and the topic list says "Some pages still need checking – the
text recognition may be wrong. Their topics are already in this list; …" with **Check the
text** (the review page). Nothing waits for that check: the flagged pages' topics are built
and taught, quick-start, lessons, tests and exams run as before, and `project_coverage`'s
"Documents read and analysed" counts a "Needs OCR review" document as processed (it was
read and analysed), so "Project complete" is not held back by a check the student skips.
Confirming the review page (or saving a page's text) makes the page "read" and the report
complete. `reading["complete"]` stays "every document read" (a page to check is read).

**Duplicates and overlaps.** `find_duplicate_pages` matches two readings of one sheet (a
photo and a scan of one handout): folded word 5-grams with a Jaccard similarity of at
least 0.9, pages with at least 40 content words. A duplicate is left out of the topic
extraction and borrows its original's analysis; if the original is later excluded or
fails, the copy is analysed on its own. `find_overlaps` lists pages that share at least
half of the smaller page's phrases (a revision sheet repeating a handout); it is
informational and stored in `analysis_json["overlaps"]`.

## The topic map

`_plan_project_sections` (app.py) with the pure helpers in `learnova/projects/topics.py`:

1. **Only read pages feed it** (`page_ready_for_topics`). A failed or unreadable page is
   reported, never mined for a few words of text layer. Duplicates are left out and
   recorded. Running headers and footers are removed from the payload only
   (`clean_extracted_pages`); line-end hyphenation is joined ("Notwen-\ndigkeit").
2. **Small batches** (`page_batches`: at most 9 000 characters and 4 pages), so one answer
   fits the 6 000-token output budget. A long page is split at a paragraph or sentence
   break, never cut.
3. **Bisect on failure.** A broken or cut-off answer is retried in halves
   (`split_batch`) until one page fails alone; only that page is "failed". A
   provider-wide error stops every further call, and the pages not reached are "not
   analysed" (read on a later visit). One run makes at most `3 × batches + 8` calls
   (`TOPIC_EXTRA_CALLS`), and it stops early when the hourly allowance runs low.
4. **Cache and progress.** Each batch answer is cached in `analysis_json["batches"]` by the
   hash of its pages and the prompt version, and stored as it arrives, so a request killed
   mid-run resumes instead of starting over. `analysis_progress` drives "part x of y".
5. **Normalise, repair, merge** (`normalize_topic`, `repair_page_refs`, `merge_topics`).
   Each topic is validated leniently: a bad term or item is dropped, never the whole
   topic. A topic must cite a real page; page numbers the model gave instead of ids are
   mapped back. Fachbegriffe and items are kept only when the student's pages really
   mention them (`in_notes`); a definition the model wrote itself is kept but marked.
   Duplicates across batches merge on a stable `topic_key`.
6. **Importance and cap.** Teacher-marked pages raise their topics to importance 3, then
   `cap_topics` keeps at most 60 topics (least important first out). What was left out is
   stored with its pages (`topics_dropped_pages`) and shown as "+ title" chips.
7. **Coverage repair.** Pages no topic cites get one more call that asks about exactly
   those pages. A page still without a topic is recorded as holding none
   (`no_topic_page_ids`); if that second look fails, the page is failed and **Update
   topics** stays visible.
8. **Reconcile** with the stored topics (`reconcile`): matches are updated in place
   (progress, review state and renames survive), new topics are added and included, and a
   stored topic is only deleted when the student never touched it, has no progress on it,
   and its pages were analysed in this run. A topic the student merged earlier keeps
   absorbing its alias on later scans.

Every page's outcome is stored in `analysis_json` (`analysis_version` 4):
`analysed_page_ids`, `failed_page_ids`, `not_analysed_page_ids`, `uncovered_page_ids`,
`no_topic_page_ids`, `duplicates`, `overlaps`, `overlap_suggestions`, `topics_dropped*`,
`topics_unplaced*` (topics the model named without a valid page), `analysis_stopped`,
`extraction_calls`. Projects analysed before these lists existed are read leniently: a
page a topic cites counts as analysed.

**A topic** (`learning_section.topic_map_json`) holds: title, main topic, kind (main or
subtopic), summary and simple summary, source page ids, Fachbegriffe (term, definition,
simple definition, example, page, quote, `in_notes`, `definition_from_notes`), typed
items (`ITEM_KINDS`: example, experiment, theory, person, argument, formula, process,
diagram, event, date, rule, thought experiment, work, method, source, place, institution,
position, cause/effect, structure), learning goals, likely exam questions, related topics
and prerequisites, difficulty, importance and estimated minutes. The old `*_json` columns
are still filled from it for older screens.

**Topic selection and editing** (all owner-only POSTs under `/projects/<id>/`):

| Action | Route | Notes |
|---|---|---|
| Start learning | `topics/confirm` | Keeps the ticked topics; "Let Learnova choose" includes all. At least one topic must be ticked. |
| Select all / none | `topics/select` | Every topic in or out at once. |
| Include or remove one | `topics/<id>/toggle` | Re-plans. |
| Rename | `topics/<id>/rename` | Marks the topic `user_edited`, so a re-scan never renames or deletes it. |
| Merge | `sections/merge` | Overlap suggestions (`overlap_suggestions`: similar title, 3+ shared terms, or half the pages plus a shared term) are recomputed from the topics as they are now. Topics with learning or exam progress cannot be merged and say why. |
| Add a missing topic | `topics/add` | Grounded on the ticked pages; without a page it is taught as standard school content and does not hide uncovered pages. |
| Update topics | `reanalyze` | Re-runs the analysis on failed and new pages; the cache makes unchanged batches free. |

## Subject strategies

`learnova/lessons/strategies/` holds one `SubjectStrategy` per way of teaching: plain
data (no prompts, no database). A strategy is the teaching sequence (`LessonStep`s, each
with the plan modes and knowledge states it applies to, and optionally the item kinds it
needs), what the topic extraction looks for, the graded answer formats in rotation, the
question stages, which items become flashcards, up to six teaching rules, the wrong-answer
rule and the exam style. `strategy_for(subject)` never fails: an unknown subject gets
`general`, any other language gets `languages`.

| Key | Subjects | Teaching sequence (learn mode) |
|---|---|---|
| `languages` | English, French, Spanish, German as a Foreign Language, other languages | objective → rule → sentence structure → examples → common mistakes → key terms → guided exercise → summary |
| `latin` | Latin | as languages, plus source analysis before the exercise |
| `mathematics` | Mathematics | objective → why it matters → what the concept means → the rule or formula → worked example → common mistakes → guided exercise → summary |
| `sciences` | Chemistry, Biology | objective → Fachbegriffe → scientific concept → experiment* → process* → diagram* → worked example with a formula* → mistakes → guided exercise → summary |
| `physics` | Physics | as sciences (its own card kinds) |
| `nwt` | Science and Technology (NwT) | sciences combined with technology: adds rule and an application example before practice |
| `ethics` | Ethics, Philosophy | objective → Fachbegriffe → thought experiment retold from the notes* → theory, philosophers and positions* → arguments → perspectives → example → mistakes → guided exercise → summary |
| `religion` | Religion | as ethics, with source analysis in place of the thought experiment |
| `german` | German | objective → text or literary concept → terminology → structure → rule → model analysis → mistakes → guided exercise → summary |
| `history` | History | objective → context → timeline* → terms → causes and consequences → perspectives → source analysis* → guided exercise → summary |
| `geography` | Geography | objective → concept → Fachbegriffe → process step by step → diagram or map* → causes and consequences → new example → guided exercise → summary |
| `politics` | Politics, Economics | objective → terminology → system or concept → stakeholders and perspectives → arguments → example* → guided exercise → summary |
| `computer_science` | Computer Science | objective → terms → concept → process → worked example → mistakes → guided exercise → summary |
| `technology` | Technology | objective → terms → concept → process → diagram* → rule → example → mistakes → guided exercise → summary |
| `music`, `art`, `physical_education` | Music, Art, Physical Education | short sequences with examples and structure (music), model analysis (art), rules and movement processes (PE) |
| `general` | Other | objective → explanation → terms → example → mistakes → guided exercise → summary |

\* Only when the topic map has an item of that kind (no experiment part for a topic
without an experiment). Old topics without a map keep the step with "leave this part out
if the material has nothing for it".

**Mixed subjects.** `strategy_key_for` splits a combined subject into its parts and joins
their keys with "+": "Geschichte/Gemeinschaftskunde" → `history+politics`, "GWG" →
`geography+politics`, "Sachunterricht" → `nwt+geography+history`. `combine()` keeps the
first strategy's order and slots the others' extra steps in before the practice phase, so
they are still taught first; the plan is capped at 12 steps.

**Question stages.** The graded questions after the lesson move from understanding to an
exam-style task (`QUESTION_STAGES`). Ethics and Religion use comprehension →
comprehension → interpretation → discussion → exam; the others comprehension →
application → exam. The exam stage is always a written answer that must be justified.

**Tutor rules.** `tutor_rules_block` is the subject method in at most 900 characters for
every tutor prompt (lesson chat, grading); the wrong-answer rule is never cut.

## Lessons: teaching first

`start_section_lesson(section, mode)` opens a lesson on one topic (`mode` is the plan
task: learn, review or reteach). `learnova/lessons/lesson_plan.py` and
`learnova/lessons/lesson_context.py` (both pure) do the work:

- **Knowledge state** (`knowledge_state`): `new` (never practised), `secure` (mastery 80
  or more), else `developing`. With the subject, the class and the adaptive explanation
  level (`docs/ADAPTIVE_EXPLANATIONS.md`) it decides how much is explained: a secure
  student gets a short recap and more practice, a review is a reminder, the key terms, one
  task and a recap; a reteach explains differently and more simply and addresses the
  typical mistakes.
- **Plan** (`plan_steps`): the strategy's steps for that mode, state and the item kinds in
  the topic map. "Teach first" is guaranteed: the objective (with one sentence on why it
  matters) comes first and at least one explaining part comes before any guided exercise.
- **Sources** (`bounded_pages`): every page of the topic with a fair share of 16 000
  characters, labelled "[<file>, p. <n>]", so a later file is never cut off. The topic map
  goes in as a bounded block (5 000 characters).
- **Fitted to the provider's limit** (`fit_lesson_source`, PF4, PF_FIX2 F1): with
  `AI_TEXT_REQUEST_TOKEN_CEILING` set, a request that fits goes as it is; otherwise the notes
  get the room the rest of the request leaves under the ceiling minus the output allowance
  (`AI_TEXT_REQUEST_OUTPUT_ALLOWANCE`, 0.4 of the lesson's output cap: Groq counted about 47 %
  of it at admission on 2026-11-02) and the margin (the topic map shrinks to 2 500 characters
  first, the notes never below `LESSON_SOURCE_MIN_CHARS`). Without the ceiling, a lesson the
  provider refuses with HTTP 413 is rebuilt once, smaller than the refused request by the
  overshoot the refusal names ("Limit L, Requested R": R - L, a tenth more and the margin;
  `lesson.fit_retry limit=… requested=… sent=… rebuilt=…` in the log); a rebuild that cannot
  be that small is not sent (`lesson.fit_retry_skipped`), so an identical request never goes
  twice and the student gets the honest "too large" message. A shortened lesson says so in
  its notice. Grading and the tutor keep the unfitted pages.
  Mixed practice is fitted the same way (24 000 characters at most). Since review B B-6 the
  final exam (each topic's notes, up to 18 000 characters), similar-mistake practice and a
  topic split are fitted too (`fit_text_sources`: every topic or page keeps a fair share,
  never below `LESSON_SOURCE_MIN_CHARS`), only while `AI_TEXT_REQUEST_TOKEN_CEILING` is set
  and the request would not fit it; with the ceiling at 0 their requests are unchanged.
- **Item kinds from headings** (`heading_kinds`, through `plan_item_kinds(source_text=)`):
  the notes' own headings add the kinds the topic map may have missed, so the
  subject-defining step is planned and not dropped (live 2026-11-01: NwT, History and
  Geography lessons lost it). An experiment needs headings of two or more lab-report phases,
  at least one of them a phase only a lab report has - hypothesis, set-up, procedure or
  observation - because "Ziel", "Material", "Ergebnis" and "Erklärung" head worksheets of
  every subject (review B B-2: a chemistry calculation sheet). A source is a line such as
  "Quelle:", "Q1", "M2", "Art. 48" or "Zitat", or a quoted passage of 15 or more words; a
  place is a heading about a map or a location ("Kartenbeschreibung", "Lage"; not
  "Karteikarten"). A kind that only the headings show is no checked item: an experiment
  part built on it stays only when what it tells is retold from the pages (`guard_parts`,
  `heading_only_kinds`), so an invented experiment is dropped, not shown as the notes'.
- **Parts.** The model returns typed `parts` (`lesson_generation:v8`/`v9`, shape `PARTS_SHAPE`;
  no `worked_example` with a teaching method). The hidden `explanation` (the question
  generator's context) is rebuilt from the guarded from-notes parts.
  `repair_parts` normalises them (repair, not reject), `ensure_teaching_first` moves an
  exercise that came too early, and `teaching_order_ok` reports a lesson that teaches
  nothing before practice.
- **Held to the notes** (`guard_parts`): no experiment or thought experiment the notes do
  not contain; Fachbegriffe and interpretations the notes do not support move into a part
  marked as an added explanation, and so do the sentences of why, explanation, rule,
  cause-and-effect and diagram parts and of term texts the pages do not support (a compound's
  head counts as supported: "Experiment" by "Gedankenexperiment"; a diagram part since PF_FIX2
  F4, after a "Wellenhöhe vs. Zeit" chart the notes did not have was shown as theirs), and a
  sentence that says the opposite of a notes sentence (a negation of a word both use: "mit
  einer 5-Prozent-Hürde" against "ohne Sperrklausel (keine 5-Prozent-Hürde)"); an inversion
  by antonym ("je schlechter" for "je besser") is not detected. Geography's map step
  (`lesson_generation:v9`) retells a map the notes only describe in words and never describes
  a chart they do not contain. A guided exercise that came without its task gets its first
  hint as the task when it has a solution, and is dropped otherwise (`repair_parts`);
  experiment phases are put in scientific order (question,
  hypothesis, setup, procedure, observation, result, explanation) and timelines in date
  order. Each part cites its pages (`resolve_part_pages`), so two files' "p. 1" are never
  confused.
- **The first question** (`checked_first_question`, PF_FIX2 F2/F5/F6), without another AI
  call: a key code can compute - the slope through two given points - is repaired (a choice
  question re-keyed, or turned into a written one with the computed answer when no option
  has it); a question shaped so it cannot be answered (a written question keyed with an
  option letter, an ordering task keyed with one id), a choice question whose key a notes
  sentence contradicts or whose wrong option a notes sentence literally says while none says
  the key (`choice_key_conflict`; language subjects' example sentences are not judged), and a
  question that asks the guided exercise again (`repeats_exercise`) are replaced by "Which
  definition fits “<term>”?" on the topic's Fachbegriffe found in the notes (`term_question`:
  the notes' own definitions first, the analysis' only when the pages support them); with
  fewer than three such terms the question is kept (`lesson.first_question_kept`).
- **Server parts.** If the model left out a planned key-terms part, the server adds one
  from the topic map (`terms_part`). The **flashcards** part (the topic's cards from the
  project set, at most 12) is inserted after the key terms.
- **Coverage record** (`taught_evidence`, `updated_coverage`): which in-notes terms and
  items the lesson actually named or cited is added to `learning_section.coverage_json`
  (counts and unions only grow).

### A wrong first answer: try again

In a teach-first lesson the first wrong answer to a question that is not the last one, not
in an adaptive plan and not a two-option question, gets **Try again** instead of the
solution (`offer_answer_retry`):

- the answer evaluation (`answer_evaluation:v8`) also returns `retry`: the exact
  misunderstanding (what the student thought, never the right alternative), a short reteach
  and a simpler example;
- the correction is **withheld** on the server (`withheld_correction`) and never sent to
  the page, the next prompt or the "Why?" panel before the second try;
- the page gets only the generic line "Not quite yet. Read the explanation below and try
  the same question again." and the retry fields. The evaluation sent keeps only
  `is_correct`, `score`, `feedback` (the generic line) and `skill_status`; the model's own
  feedback, teacher tip and exception note are not sent (PF4: they gave the answer away in 4
  of 6 live retries). The diagnosis sent with the retry - and by `GET /api/diagnosis` while
  the retry is open - is its status and labels only (`retry_safe_diagnosis`): no
  explanation, statement, misconception, intervention or quote of the key, so the page shows
  no diagnosis panel;
- a retry field that gives the answer away is blanked (`retry_reveals`: `reveals_answer`
  plus a paraphrase guard - 60 % of the answer's content words that the question does not
  use, when there are three or more, or both of two - and, in a single-answer choice
  question, one word of the right option that neither the question nor another option uses:
  anywhere in a reteach or simpler example, after a contrast ("aber", "sondern", "but") in a
  misunderstanding; PF_FIX2 F3, live: "du dachtest, der Boden sinkt, aber er hebt sich an");
  with no field left, the answer is graded as usual. On the adversarial set of review B the
  detector misses 6 of 71 phrasings (45 before PF_FIX). Measured on the live retry and
  feedback texts: 2026-11-01 12 -> 14 of 33 flagged (both new ones teacher tips that state
  the answer), 2026-11-02 8 -> 10 of 20 (exactly the two live leaks); the curated legitimate
  reteaches all survive;
- the grader's own feedback is kept on the server too (`withheld_feedback`) and written to
  the first try's `Attempt` only once the retry is resolved (the second try answered, or the
  test ended; review B B-4). Until then the Mistakes card, the lesson history,
  similar-mistake practice and the adaptive context see the generic line;
- the lesson chat does not see the grader's explanation either: while the retry is open, the
  history entry's `diagnosis_note` (which the chat reads as `recent_results`) is empty;
- a partly right answer (score 50 or more) whose feedback quotes the student's notes (six
  words or more) is graded as usual: the key may be wrong;
- a choice the notes literally say (`chosen_option_support`: one notes sentence holds 75 % of
  the option's content words, the same way round, or six words of it in a row) is never a
  mistake (PF_FIX2 F2): no retry, at least 50 points, the notes sentence shown in place of the
  grader's feedback ("Your own notes support this answer: …"), no diagnosis explanation, and -
  when the grader said it was wrong - no evidence for mastery either way. A paraphrase is not
  caught: History's "Es schränkte die Grundrechte der Bürger stark ein" for the source's
  "Grundrechte ganz oder zum Teil außer Kraft setzen" is still graded by the model alone;
- a next question from the grader that is shaped so it cannot be answered, or whose key the
  notes contradict, is replaced by the knowledge gate's own generator; a slope key is repaired;
- the second try is graded normally and then shows the withheld correction.

When a session has three or more answers, its later half decides the outcome if the last
answer is right and that half averages 80 or more, so what the student shows after
reteaching counts.

## Fachbegriffe and the project flashcard set

`learnova/projects/terms.py` builds cards from the topic maps deterministically (no AI
call), and `sync_project_flashcards` (app.py) stores them. It runs right after every
analysis is committed (a failing sync never costs the topics) and before a lesson of an
older project that has no set yet. It needs `FEATURE_PRIVATE_FLASHCARDS`.

- **One set per project**: `flashcard_set.project_id`, title "<project> – key terms",
  `source_kind` "project", created only when there is a card to put in it. Migration 038
  makes the project key unique.
- **Only content from the notes**: a term not found on the page gets no card. A definition
  the model wrote is still used, but the card carries "Explanation added by Learnova".
  With the page text, examples and item details not found on the page are marked too.
- **Only from pages the student can rely on** (PF3): a card whose every source page has a
  machine reading flagged for review that nobody checked yet is not made; the first sync
  after the check makes it ("Reading honesty" above).
- **Card kinds per subject** (`CARD_KINDS_BY_STRATEGY`, the same lists as the
  strategies): terms always, then e.g. formula, rule, method for maths; event, date,
  person, cause/effect for history; theory, position, argument, thought experiment for
  ethics. A combined subject gets the union.
- **Per topic**: one definition card per term ("What does the term “Werte” mean?" → the
  simple definition), one card per subject item, then per term a gap card from the
  verified quote, a "which term fits this example?" card, a multiple-choice card with
  three distractors, and an own-words card (not for language subjects). Every card names
  its source ("Ethik.pdf, S. 3") and keeps its page ids.
- **Two directions**: a card's front is a question, so `reverse_answer` holds the bare term
  for back-to-front study. Gap, example and own-words cards are tagged `one_way` and are
  only asked front to back; own-words cards are self-graded (`learnova/flashcards/modes.py`).
- **Add-only sync** (`plan_sync`): a card is identified by its `origin_key`
  ("<topic key>:<variant>:<term key>"); the part after the topic key is its identity, so
  the same term in two topics is one card. An existing card only gains page ids; a card
  the student edited keeps their edit and its spaced-repetition state; a card the student
  deleted (its key is in `analysis_json["flashcard_keys"]` but no longer in the set) is
  never brought back; a card whose front the set already has is skipped.

The overview's **Key terms & flashcards** section lists each topic's terms with "From your
notes", "Added explanation" or "From a page that still needs checking", the card count, the
cards due, and **Study the project flashcards**.

## Coverage and "learned"

`learnova/projects/coverage.py` (pure) decides a topic's state from evidence only:

| State | Needs |
|---|---|
| `not_started` | Nothing yet. |
| `taught` | A lesson was opened or finished, no answer yet. Clicking through a lesson never makes a topic more than taught. |
| `practising` | Answers, whatever their score. A reteach flag keeps a topic here whatever its old mastery. |
| `understood` | Marked learned **and** at least 2 correct answers (score 80 or more without hints) **and** mastery 60 or more (`UNDERSTOOD_CORRECT`, `UNDERSTOOD_MASTERY`). |
| `mastered` | Understood, mastery 85 or more and two spaced reviews passed (`MASTERED_MASTERY`, `MASTERED_REVIEW_STEP`). |

**Learned** (`learning_section.learned_at`) is set only by demonstrated performance:

- a finished topic session with a correct or hinted outcome (`record_topic_result`; an
  all-wrong or ungraded session moves the review ladder or flags a reteach but leaves the
  topic not learned, and it gets no revision date);
- a practice exam topic score of at least `EXAM_LEARNED_SCORE` (60).

Answers count from topic lessons and from submitted practice exams. Terms count as
explained when a lesson named them (`coverage_json`) or the student answered their
flashcard correctly at least once.

**Project checks** (`project_coverage`), shown as "Missing before your project is
complete":

| Check | Passes when |
|---|---|
| `documents_processed` | Every document not excluded is analysed or needs only an OCR check. |
| `topics_extracted` | No readable page is uncovered, unanalysed or failed, and no topic was left out by the cap. |
| `terms_explained` | 80% of the in-notes Fachbegriffe were taught or studied (`EXPLAINED_SHARE`). |
| `objectives_covered` | Every included topic was at least started. |
| `items_explained` | 80% of the in-notes items (experiments, theories, examples ...) were taught. |
| `understanding_demonstrated` | Every included topic is understood or mastered. |

A project with no included topic is never complete. **Project progress** is the weighted
mean of the learning checks (understanding counts double); it reaches 100% only when
every check, the material ones included, passes. Included topics of importance 3 that
were not started are listed at the top as "Important topics not taught yet".

Each topic's **next activity** (`next_activity`): start lesson → keep practising (or
"Explain it differently" when flagged) → a due review → study the key terms when too few
were covered → practise for the exam → done.

## The project overview

`/projects/<id>` is the only dashboard. After the topics are confirmed it has a section
menu with these anchors (`WORKSPACE_SECTIONS`):

| Anchor | Section | Shows |
|---|---|---|
| `#overview` | Overview | The **Next** button, project progress, what is missing before the project is complete, important topics not taught yet. |
| `#today` | Today | Today's tasks. |
| `#topics` | Learning topics | Every topic with its state, importance, knowledge level, key-term count, source pages, the next activity and its plan date (or why it is not in the plan); add, remove, rename, merge. |
| `#explanations` | Explanations | Each topic's summary and a link to its notes page. |
| `#terms` | Key terms & flashcards | See above. |
| `#exercises` | Exercises | Practise a topic, its topic test, mixed practice once a topic is learned. |
| `#tests` | Tests | The practice exam, its results, "Customise the practice exam". |
| `#revision` | Revision schedule | Topics due for revision, and topics to practise first before they get a date. |
| `#progress` | Progress & weaknesses | Topics learned and understood, plan progress, consistency, exam readiness (with an exam date), knowledge and weak spots (`#knowledge`). |
| `#plan` | Exam date and study time | Exam date, class, target grade, difficulty, study time per day, free days. |
| `#materials` | Original materials | One row per document with its state and problem pages (Try reading again, Type or retake it, Check the text), add pages (`#pages`). |

Without an exam date the same page shows a flexible plan.

## Architecture

All the logic modules below are pure (no database, no AI) and unit-tested.

| Piece | Where | Notes |
|---|---|---|
| Subjects | `learnova/lessons/subjects.py` | Canonical subject names and aliases, language subjects, combined subjects. |
| Strategies | `learnova/lessons/strategies/` | `REGISTRY` (18 keys), `strategy_for`, `strategy_key_for`, `tutor_rules_block`. |
| Lesson plan | `learnova/lessons/lesson_plan.py` | Knowledge state, steps, prompt block, part repair, teaching-first order, question stages. |
| Lesson context | `learnova/lessons/lesson_context.py` | Page budgets, topic map block, part guards, flashcards part, taught evidence, `reveals_answer`. |
| Material report | `learnova/projects/document_analysis.py` | Page and document states, completeness, duplicates, overlaps. |
| Topic extraction | `learnova/projects/topics.py` | Batches, normalisation, merge, cap, overlap suggestions, reconcile. |
| Fachbegriff cards | `learnova/projects/terms.py` | `build_cards`, `plan_sync`, term and item keys. |
| Coverage | `learnova/projects/coverage.py` | Topic states, next activity, project checks. |
| Scheduler | `learnova/projects/scheduler.py` | See below. |
| Recall grading | `learnova/projects/planning.recall_answer_score` | Deterministic: word sets, umlaut folding, spelling slips; numbers must match. |
| Orchestration | `app.py`, the "Project Mode v2" block | Page reading, `_plan_project_sections`, `sync_project_flashcards`, `project_material_report`, `project_topic_evidence`, `refresh_project_plans`, `project_next_task`, `start_project_task`, `complete_topic_session`, `record_topic_result`, the workspace routes. |
| Grading and mastery | `/api/answer` | When a topic lesson finishes, `complete_topic_session` updates the review ladder, ticks the plan task and re-plans, once per lesson. |
| Practice exams | `generate_final_exam` | See below. |

**The scheduler** (`learnova/projects/scheduler.py`):

- One shared daily budget covers every project, with 10% kept as a buffer.
- Completed and pinned work is preserved.
- The last study day before an exam holds the practice exam and targeted revision.
- Overdue reviews come first, then reteaching, then new topics in priority order: important,
  demanding, weak and with dependants first, nearest exam first. A topic marked for
  reteaching gets its reteach session, not a second first lesson.
- At most one demanding topic per day, unless there are more demanding topics than days.
- The day before the practice exam has mixed practice.
- A topic is only started if it can still be reviewed afterwards.
- Long topics are split into parts. A topic that does not fit is reported, never squeezed
  in, with or without an exam date; the warning names the daily minutes that would fit
  everything ("Plan 60 minutes per day", one tap).

**Practice exams** (`generate_final_exam`):

- Lenient: an unusable question is dropped, and the exam is saved if at least 60% of the
  questions and 70% of the topics are covered.
- Multiple-choice options are normalised to option ids, so a correct answer always matches
  the key.
- The answer key never reaches the exam page.
- Results update every topic's mastery once and its review date; a topic score of 60 or
  more marks it learned.

### Data and migrations

All migrations are additive; every new column has a default and existing rows keep their
data.

**031** (Project Mode v2):

- `learning_section` gains `topic_key` and `user_edited` (topic identity); `difficulty`,
  `importance`, `confidence`, `prerequisites_json`, `curriculum_json`; review state
  `learned_at`, `review_step`, `next_review_on`, `reteach`.
- `learning_project` gains `topics_confirmed` (the student pressed Start learning;
  projects that already had sections are marked confirmed), `topics_stale` (pages changed
  since the topics were made, or pages wait for analysis) and `analysis_json` (batch cache,
  page lists, warnings, merged-topic aliases, plan warnings, flashcard sync bookkeeping).
- `study_plan` gains `flexible`, for a project without an exam date.
- The new table `study_availability` holds one budget per student: `daily_minutes` and
  `unavailable_weekdays`.

**036_unified_project_mode**: `learning_project.grade` (the project's class).

**037_project_teaching_engine**:

| Table | Column | Type and default |
|---|---|---|
| `learning_section` | `topic_map_json` | TEXT NOT NULL DEFAULT '{}' |
| `learning_section` | `coverage_json` | TEXT NOT NULL DEFAULT '{}' |
| `flashcard_set` | `project_id` | INTEGER REFERENCES learning_project (id), nullable |
| `flashcard` | `origin_key` | VARCHAR(160) NOT NULL DEFAULT '' |
| `flashcard` | `topic_key` | VARCHAR(120) NOT NULL DEFAULT '' |
| `flashcard` | `source_page_ids_json` | TEXT NOT NULL DEFAULT '[]' |
| `flashcard` | `reverse_answer` | TEXT NOT NULL DEFAULT '' |

plus the index `ix_flashcard_set_project_id` and the partial unique index
`uq_flashcard_origin` on `flashcard (set_id, origin_key) WHERE origin_key <> ''`.

**038_one_project_flashcard_set**: should a project have two sets (two first syncs at
once), the oldest stays the project's and the other is unlinked (it keeps its cards as a
set of its own); then the partial unique index `uq_flashcard_set_project` on
`flashcard_set (project_id) WHERE project_id IS NOT NULL`.

**041_analysis_lease**: the per-project topic-analysis lease.

| Table | Column | Type and default |
|---|---|---|
| `learning_project` | `analysis_lease_token` | VARCHAR(32) NOT NULL DEFAULT '' |
| `learning_project` | `analysis_lease_until` | TIMESTAMP WITH TIME ZONE, nullable |

Taken with one conditional `UPDATE … WHERE id = :id AND (analysis_lease_until IS NULL OR
analysis_lease_until < :now OR analysis_lease_token = :token)`, renewed before every
extraction call, released in a `finally`; an expired lease (a killed request) is taken
over. Existing projects get the empty lease. The round of analysis steps lives in
`analysis_json["analysis_round"]` (`id`, `calls`, `new_topic_ids`, `update`, `finished`;
current for 10 minutes after its last step), next to `analysis_paused` (a step's budget
was used up: not a provider stop) and `analysis_batches_done` / `analysis_batches_total`.

No migration came with the damaged-file rows, the reading doubts or the withheld
flashcards: they live in `analysis_json` (`file_errors`, `flashcard_sync`) and
`recognition_json` (`doubts`, `vision_pending`, `pdf_damaged`, `pdf_repaired`,
`upload_transform`). `039_contact_messages` and `040_assistant_chat_upgrade` belong to other
features. The full list a deploy applies, the PostgreSQL advisory lock and the lock timeout
are in [OPERATIONS.md](OPERATIONS.md#what-this-release-migrates-037-041).

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

A topic gets a revision date only once it is learned; before that it is listed under
"practise these first". Reviews are always kept before the exam date.

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
- when topics are confirmed, selected, toggled, added or merged;
- when the exam date or study time changes.

A missed day becomes "missed", never deleted, and its work is planned again. Overdue
reviews come first. "I missed this day" also blocks that date for every project.

## Configuration

| Setting | Default | Where | Meaning |
|---|---|---|---|
| `PROJECT_MAX_PAGES` | 40 | env, `learnova/config.py` | Pages per project across every file and later additions. Twenty was exceeded by six four-page worksheets, and the whole upload was refused. |
| `PROJECT_PREPROCESS_ON_UPLOAD` | false | env, `learnova/config.py` | true renders and preprocesses every page inside the upload request again (the rollback switch for lazy rendering). |
| `PDF_TEXT_SHORTCUT_MAX_IMAGE_SHARE` | 0.10 | env, `learnova/config.py` | Raster share (0..1) of a PDF page from which its text layer counts as partial and vision reads the page. |
| `ANALYSIS_STEP_CALLS` | 2 | env, `learnova/config.py` | Extraction calls one `POST /analysis/step` request may make (1..6). See docs/OPERATIONS.md. |
| `ANALYSIS_LEASE_SECONDS` | 150 | env, `learnova/config.py` | How long a request's analysis lease lasts (30..900), renewed before every extraction call; a killed request's lease expires after this. |
| `VISION_MIN_REMAINING_REQUESTS` | 4 | constant, app.py | Below this many AI requests left this hour (free accounts) a page waits instead of being read, and the topic analysis stops. Scaled to a quarter of a small `AI_MAX_REQUESTS_PER_USER_HOUR` (at least 1). |
| `MAX_PROVIDER_WAITS` | 3 | constant, app.py | How often a page waits for a busy provider before it is failed; also the most failed vision re-reads of a provisional offline reading. |
| `WAITING_RETRY_AFTER` | 5 min | constant, app.py | Quick-start and the analysis do not knock on a busy provider again sooner; a provisional offline reading is read by vision again after this. |
| `DAILY_LIMIT_RETRY_AFTER` | 60 min | constant, app.py | A page that met the provider's daily limit is not asked again sooner. |
| `RECOGNIZING_STALE_AFTER` | 10 min | constant, app.py | A page stuck at "recognizing" by a killed request counts as not read after this. |
| `PROJECT_LOCAL_OCR_IN_TESTS` | off | `app.config`, tests only | The offline OCR fallback is off under the test runner unless a test sets this. |
| `LESSON_TOKEN_LIMIT` | 5000 | env, `learnova/config.py` | Call-site output cap of a lesson (7-12 typed parts). |
| `ANSWER_TOKEN_LIMIT` | 2600 | env, `learnova/config.py` | Call-site output cap of answer evaluation (with the retry fields). |
| `AI_TEXT_REQUEST_TOKEN_CEILING` | 0 (off) | env, `learnova/config.py` | Admission ceiling for text requests; lessons, mixed practice, the practice exam, similar-mistake practice and a topic split fit their notes to it. Recommended on Groq on_demand: 8000 (its tokens per minute). See AI_ROUTING.md and OPERATIONS.md. |
| `AI_TEXT_REQUEST_OUTPUT_ALLOWANCE` | 0.4 | env, `learnova/config.py` | Share of a text request's output cap counted against that ceiling (Groq counted about 47 % on 2026-11-02). 0 = input only. |
| `AI_VISION_OUTPUT_TOKEN_CEILING` | 0 (learn) | env, `learnova/config.py` | Output cap of every image request, page readings included. 950 on Groq on_demand skips the first refusal; see OPERATIONS.md for what it costs a long page. |
| `LESSON_SOURCE_MIN_CHARS` | 4000 | env, `learnova/config.py` | The smallest share of the notes a fitted lesson keeps. |
| `AI_LESSON_GENERATION_MAX_OUTPUT_TOKENS` | 5000 | env | Gateway budget for `lesson_generation`; input budget 12 000. |
| `AI_ANSWER_EVALUATION_MAX_OUTPUT_TOKENS` | 2600 | env | Gateway budget for `answer_evaluation`. |
| `AI_PROJECT_SECTION_GENERATION_MAX_OUTPUT_TOKENS` | 6000 | env | One topic-map batch answer (up to four pages). |
| `AI_FINAL_EXAM_GENERATION_MAX_OUTPUT_TOKENS` | 6000 | env | A practice exam of up to 20 questions. |
| `FEATURE_PRIVATE_FLASHCARDS` | on | env | Without it no project flashcard set is made and lessons show no flashcards part. |

The call-site caps and the gateway budgets must both be at least these values: the lower
of the two wins, and a lesson cut off at the budget fails to parse or loses its parts.

> **Operator note.** `render.yaml` still pins `LESSON_TOKEN_LIMIT` and
> `ANSWER_TOKEN_LIMIT` at `"2200"`, which overrides the new defaults in production. The
> owner must raise them to `"5000"` and `"2600"` (and check the Render dashboard and any
> `AI_*_MAX_OUTPUT_TOKENS` overrides for the same tasks) before deploying the teaching
> engine. This change set does not touch `render.yaml`. Measured on 2026-11-01: one-page
> lessons used 1,190-1,903 output tokens and the richest 3,284, which 2200 cuts off; answer
> evaluations used at most 592 ([OPERATIONS.md](OPERATIONS.md#token-limits)).

## Running

```bash
pip install -r requirements-dev.txt
flask --app app run
python -m pytest tests/test_project_scheduler.py tests/test_project_topics.py tests/test_project_mode_v2.py
python -m pytest tests/test_document_analysis.py tests/test_project_documents.py tests/test_project_extraction.py \
  tests/test_subject_strategies.py tests/test_lesson_teaching_engine.py tests/test_project_teaching_engine.py \
  tests/test_project_terms.py tests/test_project_flashcard_sync.py tests/test_project_coverage.py \
  tests/test_project_dashboard.py tests/test_project_engine_contracts.py tests/test_lesson_parts_frontend.py \
  tests/test_project_review_fixes.py
# Production-readiness fixes (damaged uploads, steps and lease, reading honesty, Try again,
# text limits, PostgreSQL); LEARNOVA_TEST_SQLITE_FK=1 enforces foreign keys as PostgreSQL does
python -m pytest tests/test_upload_robustness.py tests/test_analysis_steps.py tests/test_reading_doubts.py \
  tests/test_reading_honesty.py tests/test_retry_reveal.py tests/test_text_request_limits.py \
  tests/test_json_repair.py tests/test_lesson_prompts.py tests/test_lesson_grounding.py \
  tests/test_db_ops.py tests/test_pg_compat.py tests/test_pg_fk_integrity.py
.venv/Scripts/python -m pytest tests/test_project_start_browser.py   # Playwright; skipped without it
python -m pytest tests/test_mobile_layout.py       # Chrome or Edge needed
MSYS_NO_PATHCONV=1 python scripts/mobile_audit.py  # every page, 7 viewports, light and dark
```

Restart the local server after changing prompts or contracts.

## Deployment and rollback

The deploy procedure is in [OPERATIONS.md](OPERATIONS.md#before-every-deploy): back up the
database, read the production baseline (`SELECT version FROM schema_migration`), run the
PostgreSQL migration test, check the settings, deploy at a quiet time, smoke test. For
Project Mode in particular:

1. **Raise the token pins** in `render.yaml` (the operator note above).
2. **Decide the text and vision ceilings** for the production Groq tier
   (`AI_TEXT_REQUEST_TOKEN_CEILING`, `AI_VISION_OUTPUT_TOKEN_CEILING`) and
   `ANALYSIS_STEP_CALLS` for the proxy's read timeout.
3. **Smoke test** the six-document project, the analysis steps, the topic list, a lesson,
   Try again, the project flashcards and a practice exam (the list in OPERATIONS.md).
4. **Watch** the AI diagnostics page (or `instance/ai_usage.jsonl`) for
   `ocr_document_recognition`, `project_section_generation`, `lesson_generation`,
   `answer_evaluation` and `final_exam_generation` failures, and the log lines listed in
   OPERATIONS.md ("Monitoring").
5. **Roll back** by redeploying the previous commit; no down-migration is needed. Old
   projects keep their sections either way; cards in a project set stay in the student's
   library. What the old code makes of failed-file rows and of pages that were never
   rendered is listed in OPERATIONS.md ("Rolling back"). `PROJECT_PREPROCESS_ON_UPLOAD=true`
   brings back rendering at upload without a rollback.

## Why six papers became one

| Cause | Fix |
|---|---|
| Vision was rate-limited, every failing page still tried all three image modes, quick-start re-read the failed pages, the hourly cap tripped and the student sat out its cooldown. | Stop at the first provider-wide error, wait instead of fail, fall back to the text layer or offline OCR, retry a failed page only on request, read nothing below the hourly threshold. |
| Twenty pages per project; six four-page worksheets were refused. | `PROJECT_MAX_PAGES` 40. |
| Blank back pages cost three vision calls each and ended "could not be read". | Blank PDF pages are stored as read, without a call. |
| The flow went on as soon as any page had text; five failed papers vanished behind one that was read. | One state per document, a material report, a completeness check, and a "Continue reading" banner for waiting pages. |
| A strict reading contract rejected answers with page numbers like "2/6", 0-1000 boxes or a confidence of 95. | The reading is repaired, not rejected. |
| Topic batches of 28 000 characters against a 4 000-token answer: the JSON was cut off, the batch was skipped and only a count was stored. | Batches of four pages, bisect on failure, a coverage-repair pass, a resumable cache, per-page bookkeeping shown in the material report. |
| An aggressive merge collapsed distinct topics ("Utilitarismus nach Bentham" into "... nach Mill"), and header cleaning deleted headings on every page. | `same_topic` is strict and near-duplicates become merge suggestions; only real running headers and footers are removed, from the payload only. |
| Typed topics blocked the analysis of the uploaded papers, and pages added later were never extracted. | `project_pages_to_analyse` reads every page the extraction has not seen. |
| "Only one topic selected": every topic stayed included, but the scheduler planned only what fit before the exam, so Next and Today showed one; a project without an exam and a small budget got no task and no warning. | Topics that do not fit are always reported with the minutes that would fit them; the topic list says every topic is included and shows each topic's plan date or why it is not planned. |
| The lesson saw `source[:24000]` in upload order, so the last files never reached it; grading sent the same 24 000 characters into a 5 000-token budget. | Every page gets a fair share of the lesson budget, labelled by file and page; grading gets a 4 000-character excerpt relevant to the question. |
| One generic lesson shape for every subject, and a topic was "learned" after any finished session, even with every answer wrong. | Subject strategies, teaching-first parts, evidence-based coverage. |

## Known limits

- **Live evidence predates the fixes.** The live runs of 2026-11-01 (development Groq key,
  synthetic documents: 39 reading and analysis requests, 34 lesson requests) found the
  defects fixed above; the fixes themselves are verified offline, partly by replaying the
  stored live outputs. The final live re-check of 2026-11-02 (25 calls) ran
  `lesson_generation:v8`, `answer_evaluation:v7` and `answer_diagnosis:v3` and found the
  defects PF_FIX2 addresses (F1-F9, fixed offline and replayed from its stored outputs in
  `tests/test_live_final_limits.py` and `tests/test_live_final_lessons.py`); the new prompt
  versions (`lesson_generation:v9`, `answer_evaluation:v8`, `answer_diagnosis:v4`) and the
  output allowance are untested against the real model. Lessons were scored by the
  tester (10-15 of 20 per subject), not by a teacher; German, Biology, Religion, Music,
  Physics and Chemistry were not run live at all.
- **Answer correctness is mostly prompt-level.** The key-check and grading rules are prompt
  text. Deterministic since PF_FIX2: slope keys, unanswerable shapes, literal notes conflicts
  and literally notes-backed choices; `quotes_source` for an answer scored 50 or more. A
  paraphrased conflict (the recurring History Art. 48 option), a wrong guided-exercise key
  ("Tom lost his key" for "has lost") and a subjective ordering task need the deferred L2
  self-check call.
- **The production Groq tier is unknown.** On the development key (8,000 tokens and, for
  images, 1,000 output tokens per minute) about one page is read by vision per minute and a
  lesson takes most of a minute's budget (OPERATIONS.md, "The Groq tier").
- **Long requests left:** reading runs one page per request and the topic analysis in
  steps of at most `ANALYSIS_STEP_CALLS` calls from the start page, but three paths are
  still one long request: the no-JS quick-start (reads every due page and analyses the
  whole project, as before), quick-start's lesson generation after the topics are
  confirmed (20-60 s), and `POST /reanalyze` for old links. All of them run under the
  analysis lease and resume from the batch cache when killed. gunicorn's gthread worker
  (`--threads 4` in `render.yaml`) does not kill them at `--timeout 120`; a sync worker
  would. See docs/OPERATIONS.md.
- **Per-account limits (fixed in the final cleanup).** Quick-start, Add pages and
  `POST /reanalyze` (20 an hour), creating a project (10 an hour) and every other Project
  Mode limit now count per account (`user_rate_key`), like the analysis step and the file
  routes, so students behind one school network no longer share them. Only sign-in,
  registration and the contact form stay per address
  ([OPERATIONS.md](OPERATIONS.md#proxy-and-database)).
- **Blank detection** covers PDF pages only; a photo of empty paper is still read.
- **The doubt check is calibrated on 22 synthetic pages** (fonts, not students). Its false
  positives on real handwriting are unknown; the thresholds are constants in
  `learnova/ocr/service.py`. A confident list of short words is no longer flagged (B-5), but a
  reading with one doubtful block and short lines still is. A word the model invents with
  high confidence ("Pflichtethik" for "Pflicht zählt") cannot be seen without a dictionary
  and is not flagged. Topics kept from a reading the student later corrected keep their old
  terms until "Update topics"; cards follow the topics.
- **Flagged pages still teach.** Cards from an unchecked flagged page are withheld, but the
  lesson parts built from it can still say "from your notes" until the page is checked.
  Quick-start may spend up to three vision calls re-reading a provisional offline page.
- **Heading kinds are lexical.** "M1:", "Quelle: …" citations and "Art. 1" lists still count
  as a source, "Lage" as a place, and French lab-report headings are not recognised.
- **Lesson guard misses.** In the replay of the live lessons the guard kept 93 % of the
  sentences claimed as from the notes and moved the four invented passages of the live run to
  "added", but some additions close to the notes' words still pass as from the notes (an
  Ethics perspective, an English structure part). The check is lexical.
- **Deferred topic-analysis fixes:** junk terms, over-splitting of one page into several
  topics, and item kinds found at extraction time need a `project_section_generation`
  prompt bump, which would invalidate every project's batch cache. Table titles, diagram
  labels in the page text and false glare warnings (D7) are model-side.
- **Damaged files:** a file that fails is kept as its own row with Retry, Replace and
  Remove. Replacing it appends the new pages at the end, so the replacement becomes the last
  document ("Document 6"), not "Document 4" again. A genuinely blank page inside a repaired
  PDF counts as damaged (conservative). Lazy rendering moves 1-4 s of CPU per page into its
  first read or view; two simultaneous views of one unrendered page may render it twice.
- **Processed photos** are still stored as PNG (6-9 MB each); a lossless WebP format is a
  measured follow-up (FIX_PLAN DR-08b).
- **Typed topics** the analysis never matches to a page are taught as standard school
  content. Old projects analysed before the v4 page lists, whose topics cite every page,
  look covered until their next analysis.
- **Kompetenzraster:** read like any page. Its "Ich kann" rows are not yet separate
  teacher objectives, though teacher-marked pages raise topic importance.
- **No grade estimate.** There is no documented grading scale for a practice exam, so the
  workspace shows scores and topic states instead.
- **Frontend JavaScript.** The start page's loop (`project-start.js`) runs in six
  Playwright browser tests (`tests/test_project_start_browser.py`, `.venv` only). The lesson
  parts and Try again are covered by server-side tests of the rendered page only; Node is not
  available for JS unit tests.
- **Not checked on the production host:** a real PostgreSQL migration run, the gunicorn
  command and proxy timeouts, real handwriting and a real iPhone. OPERATIONS.md ("What was
  verified, and where") lists what only the owner can confirm.

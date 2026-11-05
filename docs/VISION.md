# Image understanding: photos, handwriting, worksheets and diagrams

Every Learnova feature that accepts a picture reads it the same way. A photo is treated the
same whether it is one of these:

- a chat attachment;
- a New-lesson photo;
- a photographed answer;
- a Project page;
- a flashcard import.

The code is in `learnova/vision/`. The routes that use it are in `app.py`, in the section
"Shared image understanding".

| Module | What it does |
|---|---|
| `intake.py` | Checks, decodes, orients, assesses and sizes one uploaded image. No AI. |
| `reader.py` | Reads 1–4 images with the vision model, in bounded stages, through the normal AI gateway. |
| `reading.py` | `VisualReading`, the structured result every feature uses. It keeps uncertainty. |
| `prompts.py` | The reading contract, plus the handwriting and maths rules it shares with Project Mode. |
| `notation.py` | A canonical form of written maths, so readings can be compared by structure. |

## Why reading was unreliable (root causes found)

1. **Pictures were sent far over the provider's size limit.**
   - Project pages, flashcard images and vocabulary pages were sent as the full-resolution
     preprocessed PNG.
   - A 1.6 MB phone JPEG became an 8.3 MB PNG, which is 11 MB as base64. Groq documents
     4 MB for a base64 image.
   - Measured on the synthetic set: 6 of 17 images, every photo-sized one, were over the
     limit on the old path. On the new path all 17 fit (see Evaluation).
2. **New lesson from photos (`/api/analyze`) trusted the browser.**
   - It accepted the browser's MIME type without looking at the bytes.
   - It had no pixel limit, ignored the phone's rotation tag and sent the raw upload.
   - It never told the router that the request carried images. When the vision model
     failed, the next candidates were text models, which cannot see a picture.
3. **One weak call did everything on that path.**
   - The vision model read the handwriting and wrote the lesson in the same call.
   - Nothing told it what to do with a word it could not read.
   - The tutor chat afterwards never learnt what the photo said.
4. **Neither chat accepted images,** and there was no way to check handwritten working.
5. **Transparent screenshots turned black.**
   - The old page preprocessing converted RGBA to RGB, so a transparent screenshot became
     black on black.
   - Measured with the local OCR engine: the character error rate went from 1.000 (nothing
     readable) to 0.000.
6. **Two separate OCR paths gave different rules.** Project Mode and flashcard import had
   their own prompts, so a page was read by different rules depending on where it was
   uploaded.
7. **Chat photos failed on the real model (audit 2026-11-01).** Upload, MIME, base64 and the
   payload shape were correct; the reading request did not fit `qwen/qwen3.8-27b` on Groq:
   - R1: about 3.3k input tokens per photo (prompt ≈1.2k + 2048 per image) plus a
     5000-token output cap was over the 8000 tokens per minute Groq allows that model on the
     on_demand tier ("Request too large", HTTP 413); the dev key also allows it only 1000
     output tokens per minute (HTTP 429 "Request too large … OTPM").
   - R2: up to 4 images were sent; the model takes at most 3.
   - R3: every provider error became "AI is temporarily unavailable. You can retry." with
     nothing logged, so a retry failed the same way. Tests patched `app.create_response`
     and never exercised the provider limits.
   Amplifiers: the image estimate was 1500 tokens (Groq counts 2048), one message could
   exceed the 120 s worker timeout (stage 1 + same-model retry + stages 2/3 + reply), the
   assistant route did not commit the reading before the reply, the tray limit was
   hard-coded to 4, removing a duplicate photo deleted the shared attachment, and non-JSON
   401/413/502 answers became raw errors. See "Fitting the vision model" below.

## The pipeline

```
device ──► camera/gallery input ──► (browser) re-encode: upright, ≤2560 px, JPEG
       ──► POST upload ──► intake.prepare ─ bytes decide the type (JPG/PNG/WebP; HEIC/PDF named)
                                          ─ header checked before decoding (pixel bombs)
                                          ─ EXIF rotation applied, metadata dropped, alpha → white
                                          ─ quality hints: blurred / dark / faint / small / cut off
                                          ─ stored copy ≤2560 px
       ──► reader.read_images ─ stage 1: one call, all images, provider copy ≤2048 px / ≤2.9 MB
                              ─ stage 2: model says "sideways" → turn, read once more (max once)
                              ─ stage 3: unclear words with a position → enlarged close-up sheet,
                                         re-read; replaced only when more confident
       ──► VisualReading ─ text in reading order, unclear parts as [?7], alternatives,
                           formulas as text + LaTeX, diagrams with labels, tables, who wrote what
                           (printed / student / teacher), clarification question if it matters
       ──► the feature's own prompt (chat, lesson, answer box) ──► reply rendered as before
```

The gateway (`learnova/ai_services/service.py`, `routing.py`) adds two safeguards for every
call site, including ones that do not use the reader:

- **Image signal and routing.** Any request whose input carries an image gets
  `has_images`, and the router sends it only to models that can see:
  - the vision tier model (`GROQ_VISION_MODEL`);
  - the optional `AI_VISION_FALLBACK_MODEL`;
  - any model listed in `AI_VISION_MODELS`.

  This holds even for "think harder" in the assistant. Images never go to premium
  reasoning models, because their image support is not something this code can know.
- **Size safety net.** An oversized image data URL is shrunk to the provider limit before
  it leaves.

### Uncertainty, honestly

- **Confidence:** this is the model's own number. It is `None` when the model gave none,
  never an invented 0.5.
- **Unclear readings:** inline they are marked `[?reading]`, and they are also listed with
  their alternatives (`7` or `1`, `x` or `×`, a comma or a decimal point).
- **Clarification:** when an unclear part changes a task, a number or a step,
  `affects_answer` is set and the reading carries one short clarification question. The
  chat instructions (`CHAT_IMAGE_RULES` in `app.py`) tell the model to ask that question
  and to answer only what does not depend on it.
- **No warning for clear photos:** nothing is shown when the photo was clear.
- **Project pages: a confident mean is not enough (PF3 D2).** A project page read by vision
  is checked by `reading_doubts` (`learnova/ocr/service.py`): two or more text blocks below
  0.80 or one below 0.45, or two or more broken-off fragments ("mut", "en!", "verge:sen")
  in a reading with at least one block below 0.80 (review B B-5: a confident vocabulary list
  or "gut"/"ok" in the margin is no misreading) make the page "unclear" (at most 0.79), so
  it needs review and nothing from it is shown as
  verified notes content until the student checks it. In the live run of 2026-11-01 a page
  with a word error rate of 0.55 had scored 0.806 "high"; replayed offline, the check flags
  that page and none of the other 21 (docs/PROJECT_MODE.md, "Reading honesty").
- **A busy vision model is asked once per Project Mode run (review B B-1).** While the only
  vision model cools down after a 429, `ocr_document_recognition` fails at once without a
  call (`COOLDOWN_FAIL_FAST_TASKS`) and the page is read offline; the reading loops stop at
  the first page read offline because the AI was busy. Chat photos (`image_understanding`
  and their close-up second look) still ask the model as before.
- **"Try reading again" is a real re-read (PF3 D3).** Page readings are cached
  (`DETERMINISTIC_TASKS`), so a retry used to return the same reading for free. The retry
  now passes `fresh` to the gateway and starts with another image mode; the stored
  reading's mode goes last.

### Maths

- Formulas keep their structure: fractions, signs, exponents, roots, units, the decimal
  separator as written (a German comma stays a comma), and one line per written line.
- Each formula gets a readable `text` and a `latex` form.
- `notation.canonical_math` compares two readings by structure: `\frac{2}{3}` equals
  `2/3`, `−4,5` does not equal `-4.5`. The evaluation uses it.

## Where it is used

| Feature | What happens now |
|---|---|
| **Assistant** (`/assistant`) | Camera and gallery buttons, a preview tray, remove/reorder/retry, real upload progress. Up to `chat_image_limit()` photos per message (2 at the default 8000-token ceiling; the tray reads it from `data-max-photos`), photo without text allowed; a photo-only message of which nothing could be read gets a fixed "I could not read anything in your photo…" answer without any model call. The photos are read once, then the normal assistant model answers from the reading. Later turns reuse the stored reading; the picture is never sent again. |
| **Lesson tutor chat** (`/api/chat`) | The same, inside a lesson. If the reply fails, the reading is kept and the photos stay unsent, so sending again costs no second reading. |
| **Photo of my working** (`/api/answer/photo`) | On written questions. The solution is read with the "solution" purpose: task separated from the student's working, line by line, every step kept. The transcription goes into the answer box with unclear parts marked. The student fixes it and sends it as their answer, so nothing is graded from a photo directly. The grader is told it came from a photo and must judge the steps and never count an unreadable line as wrong. The expected answer never reaches the reader. |
| **New lesson with photos** (`/api/analyze`) | Shared intake, then a reading, then the normal tutor model writes the lesson from it, with quality options; it no longer sees the images. Unreadable photos get a clear 422 and the lesson is not counted. The reading becomes the lesson's `source_context`, so the tutor chat and the grading know what the photos said. Unclear parts are shown in one line above the lesson. |
| **Project Mode pages** | The existing pipeline is kept: native PDF text, three preprocessing variants, the close-up second look, the review page and topic confirmation. Since the teaching-engine rebuild a provider-wide error stops the image modes at once, a page waits instead of failing while the provider or the hourly allowance is out, blank PDF pages are skipped without a call, and the text layer and then the offline OCR are the fallback (`text_source`, see `docs/PROJECT_MODE.md`). Each page goes to the provider sized (`provider_image_url`), and the page prompt shares the confusable-characters and maths rules. The scanner still captures lossless full-resolution PNG (an earlier handwriting decision); only the copy sent to a provider is sized. When the live camera is refused or missing, it offers the phone's own camera app, with specific advice for a refused permission. Exam preparation reads the same Project pages. |
| **Flashcard image import, vocabulary textbook pages** | Sent sized. Their prompts and flows are unchanged. |
| **Vocabulary tap-a-word scanner** | Unchanged and still LLM-free, by earlier decision. |

## Privacy, retention, cost

- **Ownership:** every photo belongs to one account. `owned_attachment` filters by the
  signed-in user. Another account's id gets a 404, and attaching it gets a 400 before
  anything is read.
- **How the picture is served:** through `/api/chat-attachments/<id>/image` only, with
  `Cache-Control: no-store, private` and `nosniff`. No storage paths or public URLs exist;
  the bytes are in the database.
- **What is stored:** the intake copy, never the upload. It is re-encoded with no EXIF, so
  no GPS position and no phone model are kept.
- **Retention** (`cleanup_chat_attachments`, which runs on each upload):
  - photos never sent are deleted after 1 day;
  - a sent photo's picture is deleted after `CHAT_ATTACHMENT_RETENTION_DAYS` (30). Its
    reading stays with the conversation;
  - deleting a conversation deletes its photos.
- **Logging:** only counts and flags (`vision.read purpose=… images=… unclear=…`), never
  text read from an image.
- **Free plan:** reading a photo sent to a chat or photographed as an answer is charged to
  the free chat allowance, like the text sent with it (`charge_as_chat`). A lesson's or a
  project's own reading is covered by that lesson's or project's allowance.
- **Cost of the stages:**
  - stage 1 is one call per message, however many photos it has;
  - stage 2 adds one call only for sideways photos;
  - stage 3 adds one call per image only when words are unclear;
  - identical readings are served from the gateway's private cache (`image_understanding`
    is a deterministic task);
  - the same photo chosen twice is one attachment.
- **No new paid service and no new secret.** Everything runs on the existing Groq vision
  model. `AI_VISION_FALLBACK_MODEL` can point at Gemini or OpenAI, which this repo already
  supports; using one costs that provider's per-image price and needs its key and an
  `AI_BUDGET_<PROVIDER>_*` cap.

## Fitting the vision model (2026-11-01)

**Effective photo limit.** `app.chat_image_limit()` = the smallest of
`CHAT_IMAGES_PER_MESSAGE` (owner cap, default 3), `VISION_MAX_IMAGES_PER_REQUEST` (what the
model accepts, 3 for qwen3.8-27b) and `vision_images_that_fit()`: the largest n whose
reading request - the gateway's own estimate of the v2 prompt with a 600-character question,
2048 tokens per image, the system prompt and the 600-token contract allowance - plus
`AI_IMAGE_UNDERSTANDING_MIN_OUTPUT_TOKENS` (1500) and a margin of max(200, ceiling/20) stays
within the ceiling. At the default `AI_VISION_REQUEST_TOKEN_CEILING=8000` that is **2**; 3
photos need a ceiling of at least 9 710 (2 photos need 7 530; a paid tier). The same limit applies to the assistant,
the lesson chat, photographed answers and `/api/analyze`, and is rendered as
`data-max-photos` for the trays and the lesson builder.

**Gateway preflight (requests that carry images only).** Before anything is reserved or
sent, `image_output_cap` lowers `max_output_tokens` to the room left under the ceiling
(`request_ceiling(model, has_images=True)`: an `AI_REQUEST_TOKEN_CEILINGS` entry for the model,
else `AI_VISION_REQUEST_TOKEN_CEILING`), and refuses with `request_too_large` (no call, usage
`event_kind=refused`) when that room is below the task's minimum output. Text requests are
never clamped: clamping would truncate lessons. Their admission check (Project Mode's PF4,
on the same `request_ceiling`) counts the input plus `AI_TEXT_REQUEST_OUTPUT_ALLOWANCE` of the
output cap, because Groq counted about 47 % of it on 2026-11-02 (PF_FIX2, AI_ROUTING.md).

**Output per minute.** When Groq refuses a reading because its expected output exceeds the
model's output-tokens-per-minute limit (429 "Request too large … OTPM: Limit 1000, Requested
1057"), the gateway retries once with the cap just below that limit (950) and remembers it for
an hour - in every worker: the learned cap is also kept in `AI_CACHE_DIR/_limits/
learned_output_caps.state` (integers only), so a new process does not pay a refusal to learn
it again. `AI_VISION_OUTPUT_TOKEN_CEILING` pins it instead (0 = learn; 950 on Groq on_demand). The pin caps every image request, Project Mode page readings included (budget 4000), so a long page reading is cut off at it (docs/OPERATIONS.md, "The Groq tier"). A 429 that only found the minute's output budget used up ("Used 901,
Requested 900") is a real rate limit: no retry, the student is told to try again in a minute
(`Retry-After` from Groq). A 413 that names its limit gets one retry with the overshoot removed
from the output cap, if that still leaves the minimum. Only `image_understanding` is retried
this way. The OTPM 429 keeps the category `provider_rate_limit`, and a 413 is
`request_too_large`, which is not a provider-wide limit: Project Mode's page reading and topic
analysis behave as before (a rate-limited page waits, or is read offline for now and by
vision later; a too-large topic batch is halved).

**Time.** A photo message should finish within 120 s: gunicorn's `--timeout` kills a longer
request on a sync worker (the gthread worker of `render.yaml` does not), and a reverse proxy
with a shorter read timeout gives up sooner (docs/OPERATIONS.md). The reading gets
`VISION_TIME_BUDGET_SECONDS` (100) minus what the reply still needs
(`AI_PROVIDER_TIMEOUT_SECONDS`, 40, for chat and lesson photos; 0 for a photographed answer):
every call is passed `timeout_seconds` = what is left (the gateway bounds the SDK timeout by
it, at least 5 s), and stage 2 and each stage-3 close-up start only with
`VISION_OPTIONAL_PASS_MIN_SECONDS` (15) left; skipped passes are logged (`skipped=`). A
timeout is never retried on the same provider, and an invalid reading is not read again by
the same model (a truncated reading would truncate again). The assistant commits the reading
before the reply starts, so a failed or killed reply never throws a paid reading away.

**What the student is told** (`app.image_failure_response`, all JSON; nothing is stored and
the photos stay attached):

| Category | Status, code | Message |
|---|---|---|
| `request_too_large`, `token_limit_exceeded` | 413 `photos_too_large` (+`details.max_photos`) | "These photos are too much to read at once. Send at most {count} photos per message." / one photo: "…Crop it to the task you need, or type the task instead." |
| `provider_rate_limit`, `provider_overloaded` | 503 `photo_reader_busy` + `Retry-After` (provider's, else 60) | "The photo reader is busy right now. Try again in a minute. Your photos stay attached." |
| `provider_daily_limit`, `provider_quota_exhausted` | 503 `photo_reader_daily_limit` | "The photo reader has reached its limit for today. Type the task instead, or try again tomorrow." |
| `image_rejected` (a 400 on an image request), `invalid_request` | 400 `image_rejected` | "This photo could not be read. Take it again as a JPEG or PNG, straight from above." |
| `provider_timeout` | 504 `photo_read_timeout` | "Reading the photo took too long. Try again with one photo." |
| `model_not_found`, `authentication_failure`, `provider_unavailable`, `network_failure` | 503 `photo_reader_unavailable` | "Photos can't be read right now. You can type the task instead. Your photos stay attached." |
| `invalid_json`, `schema_validation` | 422 `photo_read_failed` | "The photo reader could not finish reading your photo this time. Crop it to the exercise you need, or try again in a minute." / several photos: "…Send one photo at a time, or try again in a minute." (it used to say "send a sharper photo": live, sharp worksheets failed here because the reader ran out of output) |
| allowances, budgets | as before (`ai_failure_response`) | e.g. the free chat allowance |

Groq's 429 bodies all link to its billing page; a "tokens/requests per day" 429 is now
`provider_daily_limit` (that model cools down 10 minutes), not an empty account.

**Logging, no content.** `ai.provider_failure task=… provider=… model=… status=… code=…
type=… category=… limit_scope=… limit=… requested=… retry_after=…` per failed call,
`vision.read_failed purpose=… images=… category=… status=… code=…` per failed reading, and
the usage record gains `provider_status`, `provider_error_code`, `provider_error_type`. The
provider's message text is parsed for integers only and never logged. Readings are recorded
per feature: `prompt_version` = `image_understanding:chat1+chat` for a chat photo,
`image_understanding:v2+lesson|solution` for the others. `vision.read` logs `partial=[…]`,
the images a reading stopped in.

### Chat photos on a 1000-output-tokens-per-minute tier (live check 2026-11-02)

Live on Groq's on_demand tier no ordinary worksheet photo could be read in a chat: the
reading asked for 3500 output tokens (429 OTPM, "Limit 1000, Requested 2274"), the retry at
950 stopped inside block 8 of 10 - the v2 reading is about 110 tokens a block, pretty-printed
with bbox, author and confidence on every block - and the truncated JSON became a 422 that
told the student to send a sharper photo. What changed (CHAT_FIX2):

- **Compact chat reading** (`image_understanding:chat1`, `vision/prompts.COMPACT_*`, label in
  `ai_services/prompts.PROMPT_VARIANTS`, named per call in `validation_context.prompt_variant`):
  `lines` of `type` (task, text, formula, working, table, heading, note, crossed_out, unclear),
  `text` and `latex`, plus the images' rotation/legibility, diagrams, uncertain parts, the
  clarification question and a one-sentence summary; minified; no bbox, author or per-line
  confidence. `working` lines are the student's (author `student`), `task` lines show as
  "(task)" to the tutor. Without a bbox there is no close-up second look for chat photos;
  unclear parts stay `[?…]` and the reply asks about them. The same worksheet is about 360
  tokens. `AI_CHAT_PHOTO_READING_MAX_OUTPUT_TOKENS` (900 per photo, under the 1000 per minute).
  Lesson photos and photographed answers keep `image_understanding:v2` and their caches.
- **A cut-off reading keeps its complete lines** (`contracts.salvage_cut_off_json`, only for
  the chat variant): the image it stopped in is marked `partial`, the tutor is told "only
  partly read: the photo reader stopped after the last line below, the rest was not read"
  and never to guess the rest, and the student sees "Only the first part of your photo could
  be read; the rest was not read. Crop it to the exercise you need, or send it again in a
  minute." A response with no complete line still fails (422). A cut-off reading is never
  cached, so sending the photo again really reads it.
- **One photo per request when the output minute is the limit:** when a per-minute output cap
  is known (`AI_VISION_OUTPUT_TOKEN_CEILING`, or learned) and the photos' readings together
  would exceed it, each photo is read alone, with what this message's earlier readings left
  of the minute (at least 300 tokens, else 503 `photo_reader_busy` with `Retry-After`). Each
  reading is committed as soon as it is read, so after a refusal the resend reads only the
  photos still unread. While no limit is known the photos are read together, each with 900;
  the first refusal teaches the limit to every worker.
- **No charge for nothing:** a free account's chat allowance is given back for a photo
  reading that failed or found nothing readable (the deferred settlements become releases);
  the site's usage ledger still records what the provider was paid.
- **The size hint measures the writing:** a photo under 450 000 pixels or 800 px is called
  small only when its text lines (75th percentile of their heights) are under 28 px - a
  legible crop of one exercise (1180×340, lines of 47 px) is no longer told to move closer,
  while the synthetic small page (24 px) and pages shrunk until hard to read (15-24 px) are.

**Prompt.** `image_understanding:v2`: the reading instructions are about 30 % shorter (2 539
instead of 3 666 characters for a chat photo; the handwriting and maths rules and every schema
field are kept), a `bbox` is asked for only where the close-up needs it (confidence < 0.8 or
an unclear mark) and `latex` only on formula blocks. Reading quality on real photos is not
measured offline (live check proposal, chat plan §9).

**Fallback model.** `AI_VISION_FALLBACK_MODEL` stays empty by default (owner decision). Images
are never routed to a text model, and a failed reading is never shown as if the photo had
been read.

## Configuration

All values are optional and validated at start-up.

| Variable | Default | Meaning |
|---|---|---|
| `FEATURE_CHAT_IMAGES` | `true` | Photos in both chats and photographed answers |
| `CHAT_IMAGES_PER_MESSAGE` | `3` | Owner cap on photos per message (1-4); the effective limit is `chat_image_limit()` |
| `VISION_MAX_IMAGES_PER_REQUEST` | `3` | Images the vision model accepts in one request |
| `AI_IMAGE_TOKEN_ESTIMATE` | `2048` | Tokens one image counts as in every estimate |
| `AI_VISION_REQUEST_TOKEN_CEILING` | `8000` | Input + output ceiling for image requests (0 = off) |
| `AI_VISION_OUTPUT_TOKEN_CEILING` | `0` | Pinned output cap for image requests (0 = learn from the provider) |
| `AI_REQUEST_TOKEN_CEILINGS` | empty | Per-model ceilings `provider:model=tokens,…` (image requests) |
| `AI_IMAGE_UNDERSTANDING_MIN_OUTPUT_TOKENS` | `1500` | Smallest output a reading may be sent with |
| `VISION_TIME_BUDGET_SECONDS` / `VISION_OPTIONAL_PASS_MIN_SECONDS` | `100` / `15` | Reading time budget per message; seconds an optional pass needs |
| `CHAT_ATTACHMENT_RETENTION_DAYS` | `30` | Days a sent photo's picture is kept |
| `CHAT_ATTACHMENT_UPLOADS_PER_HOUR` | `60` | Upload rate limit per client |
| `VISION_MAX_UPLOAD_BYTES` | 15 MB | Per-image upload limit |
| `VISION_MAX_PIXELS` | 40 000 000 | Pixel ceiling, checked from the header |
| `VISION_STORAGE_EDGE_PX` | `2560` | Longest edge kept |
| `VISION_PROVIDER_EDGE_PX` / `VISION_PROVIDER_MAX_BYTES` | `2048` / `2900000` | What a provider receives |
| `AI_VISION_FALLBACK_MODEL` | empty | Second vision model, `provider:model` |
| `AI_VISION_MODELS` | empty | Further models you vouch can see images |
| `VISION_LOCAL_ORIENTATION` | `true` | Turn sideways landscape photos upright locally (needs RapidOCR) |
| `AI_IMAGE_UNDERSTANDING_MAX_OUTPUT_TOKENS` / `…_INPUT_TOKENS` | `3500` / `10000` | Reading budgets (the ceiling may lower the output per request) |

The existing `FEATURE_HANDWRITING_SECOND_LOOK` and `HANDWRITING_SECOND_LOOK_MAX_REGIONS`
also control stage 3. The tutor chat's input budget rose from 6000 to 8000 tokens, so one
turn's reading fits next to the lesson state. If your server pins
`AI_TUTOR_CHAT_MAX_INPUT_TOKENS`, raise it too.

Database: one new table, `chat_attachment` (migration marker `033_chat_attachments`),
created by `create_all`. Nothing existing is altered.

## Evaluation

```bash
.venv/Scripts/python scripts/vision_eval.py            # offline, free
.venv/Scripts/python scripts/vision_eval.py --json r.json
.venv/Scripts/python scripts/vision_eval.py --live     # PAID: real vision model
.venv/Scripts/python scripts/vision_eval.py --live --cases DIR   # PAID: your photos + .txt truth
```

The cases are synthetic and project-owned (`learnova/evaluation/vision_fixtures.py`):
- printed German;
- simulated handwriting in German and English on ruled and squared paper;
- light pencil;
- handwritten maths with exponents, a decimal comma and minus signs;
- a tilted page, a low-light photo, a blurred page and a sideways page;
- an EXIF-rotated photo, cut-off lines and a low-resolution photo;
- a table, a labelled triangle and two pages;
- a transparent screenshot and a 12-megapixel photo;
- 7 broken or unsupported files (corrupt, GIF, HEIC, PDF, a pixel bomb, empty, text).

**Simulated handwriting is jittered type, not real handwriting.** Results on it say
nothing about how well a model reads a real student's hand.

Measured offline on 2026-10-28 (Windows, Arial and Segoe Script fonts, RapidOCR 3.9 as a
fixed reader):

| Check | Result |
|---|---|
| Intake decisions | 24/24 correct |
| Within Groq's 4 MB base64 limit | old page path 11/17, new 17/17 (e.g. 12 MP photo: 7.21 MB → 0.41 MB) |
| Legibility of the provider copy vs the kept copy (CER, RapidOCR) | no case worse by more than 0.02. Ruled handwriting 0.061 → 0.079 and low light 0.018 → 0.026 rose slightly; the rest were identical |
| Transparent screenshot, old preprocessing vs new intake (CER) | 1.000 → 0.000 |
| Quality hints, seed 7 (calibration) | precision 1.0, recall 1.0 |
| Quality hints, held-out seeds 11 / 23 / 31 | precision 1.0 on all; recall 1.0 / 1.0 / 0.75 (a milder blur on seed 31 was not flagged; the threshold is deliberately conservative) |

The `--live` mode reads every case twice, once with the old Project page prompt and once
with the new reader. It reports CER/WER, maths matched by structure, diagram-label recall
and the number of unclear readings. **It has not been run:** it costs money on your
account. Run it before claiming any change in model reading accuracy.

## Known limitations

- **Real handwriting accuracy is unmeasured.** It depends on the Groq vision model
  (`qwen/qwen3.8-27b`) and has not been measured on real student handwriting here. Run
  `--live --cases` on a folder of real photos with typed-out `.txt` truth files.
- **The chat model never sees the picture.** It answers from the reading. A question about
  something the reading missed (a colour, an exact position) can only be answered as well
  as the reading described it.
- **Sideways photos:** landscape photos without a rotation tag are turned upright by the
  local OCR engine before any model sees them (`VISION_LOCAL_ORIENTATION`, about half a
  second each, only where RapidOCR is installed). On the synthetic set it turned both
  sideways pages the right way and no upright one. **Upside-down pages are not detected**
  locally; for them, and wherever RapidOCR is missing, stage 2 (the model reports the
  rotation) is the fallback.
- **The model's own confidence numbers are not calibrated.** They are shown as reported,
  never used to reject a photo.
- **Quality hints are heuristics,** tuned on synthetic images: no false alarms on four
  seeds, but a mild blur can go unflagged.
- **No live camera for chat photos.** Chat photos use the phone's camera app
  (`<input capture>`). The live in-page camera (`getUserMedia`) exists only in the Project
  scanner. This is deliberate: it is the most reliable path on iOS Safari and Android
  Chrome, but there is no live framing guide in the chats.
- **Browser tests need Playwright.** Node is not installed; `tests/test_image_attach_browser.py`
  runs the shipped `image-attach.js` (tray limit, duplicates, restore, upload errors) in the
  system Chrome/Edge through Playwright, so it runs only with `.venv/Scripts/python` and is
  skipped under Store Python. The pages are also covered by the mobile layout audit.
- **3 photos per message need a higher tier.** At Groq's on_demand limits (8000 tokens and,
  on the dev key, 1000 output tokens per minute for qwen3.8-27b) a message takes 2 photos and
  the photo reader serves about one reading per minute site-wide; check the production key's
  tier, or configure `AI_VISION_FALLBACK_MODEL` with a budget.

## Manual verification (phone)

1. **Assistant, camera:** tap 📷, take a photo of a handwritten calculation, type "Is this
   right?", send.
   - The thumbnail shows upload progress.
   - The reply refers to the actual numbers.
   - If a digit was smudged, it asks which one it is.
2. **Assistant, gallery:** choose two photos, reorder them with ← →, remove one, then send
   only a photo.
3. **Lesson chat:** in any lesson, open the tutor chat, attach a worksheet photo and ask
   about it.
4. **Written answer:** on a written-answer question, tap "Photo of my working". The answer
   box fills with what was read and unclear parts are marked. Fix one and tap Check my
   answer.
5. **New lesson:** build a lesson from a notes photo; afterwards ask the tutor chat about
   something on the photo.
6. **Project scanner:** deny the camera permission once. The scanner offers "Open camera
   app".
7. **Dark mode:** repeat a few of these in dark mode.

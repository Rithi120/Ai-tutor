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

### Maths

- Formulas keep their structure: fractions, signs, exponents, roots, units, the decimal
  separator as written (a German comma stays a comma), and one line per written line.
- Each formula gets a readable `text` and a `latex` form.
- `notation.canonical_math` compares two readings by structure: `\frac{2}{3}` equals
  `2/3`, `−4,5` does not equal `-4.5`. The evaluation uses it.

## Where it is used

| Feature | What happens now |
|---|---|
| **Assistant** (`/assistant`) | Camera and gallery buttons, a preview tray, remove/reorder/retry, real upload progress. Up to 4 photos per message, photo without text allowed. The photos are read once, then the normal assistant model answers from the reading. Later turns reuse the stored reading; the picture is never sent again. |
| **Lesson tutor chat** (`/api/chat`) | The same, inside a lesson. If the reply fails, the reading is kept and the photos stay unsent, so sending again costs no second reading. |
| **Photo of my working** (`/api/answer/photo`) | On written questions. The solution is read with the "solution" purpose: task separated from the student's working, line by line, every step kept. The transcription goes into the answer box with unclear parts marked. The student fixes it and sends it as their answer, so nothing is graded from a photo directly. The grader is told it came from a photo and must judge the steps and never count an unreadable line as wrong. The expected answer never reaches the reader. |
| **New lesson with photos** (`/api/analyze`) | Shared intake, then a reading, then the normal tutor model writes the lesson from it, with quality options; it no longer sees the images. Unreadable photos get a clear 422 and the lesson is not counted. The reading becomes the lesson's `source_context`, so the tutor chat and the grading know what the photos said. Unclear parts are shown in one line above the lesson. |
| **Project Mode pages** | The existing pipeline is kept: native PDF text, three preprocessing variants, the close-up second look, the review page and topic confirmation. Now each page goes to the provider sized (`provider_image_url`), and the page prompt shares the confusable-characters and maths rules. The scanner still captures lossless full-resolution PNG (an earlier handwriting decision); only the copy sent to a provider is sized. When the live camera is refused or missing, it offers the phone's own camera app, with specific advice for a refused permission. Exam preparation reads the same Project pages. |
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

## Configuration

All values are optional and validated at start-up.

| Variable | Default | Meaning |
|---|---|---|
| `FEATURE_CHAT_IMAGES` | `true` | Photos in both chats and photographed answers |
| `CHAT_IMAGES_PER_MESSAGE` | `4` | Most photos per message (at most 4) |
| `CHAT_ATTACHMENT_RETENTION_DAYS` | `30` | Days a sent photo's picture is kept |
| `CHAT_ATTACHMENT_UPLOADS_PER_HOUR` | `60` | Upload rate limit per client |
| `VISION_MAX_UPLOAD_BYTES` | 15 MB | Per-image upload limit |
| `VISION_MAX_PIXELS` | 40 000 000 | Pixel ceiling, checked from the header |
| `VISION_STORAGE_EDGE_PX` | `2560` | Longest edge kept |
| `VISION_PROVIDER_EDGE_PX` / `VISION_PROVIDER_MAX_BYTES` | `2048` / `2900000` | What a provider receives |
| `AI_VISION_FALLBACK_MODEL` | empty | Second vision model, `provider:model` |
| `AI_VISION_MODELS` | empty | Further models you vouch can see images |
| `VISION_LOCAL_ORIENTATION` | `true` | Turn sideways landscape photos upright locally (needs RapidOCR) |
| `AI_IMAGE_UNDERSTANDING_MAX_OUTPUT_TOKENS` / `…_INPUT_TOKENS` | `5000` / `10000` | Reading budgets |

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
- **No browser-side tests.** The JavaScript has no automated browser tests here (Node is
  not installed). The pages are covered by the mobile layout audit and the markup tests.

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

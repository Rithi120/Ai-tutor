# Many images in one go: Project Mode and Vocabulary Mode

Built 2026-11-13 (classroom readiness, phase 3). A student photographs a whole unit of
notes or a vocabulary chapter: 30 to 40 pages. Before this, Vocabulary Mode took exactly
one photo, and Project Mode effectively one pick (a second pick replaced the first), sent
everything in one request (over 40 MB the server answered with a redirect, and every
camera scan held in the page was lost), stored camera scans after all gallery photos
whatever order the student meant, and showed no page anywhere after the upload.

## What the student sees

- **One gallery per collection.** Photos and PDFs chosen from the phone's library (an
  input with `multiple` and no `capture`, so the phone offers the library and files), photos
  from the camera (a separate button: the scanner in Project Mode, `<input capture>` in
  Vocabulary Mode and under "Add more pages"), and files dropped on a desktop all join one
  ordered list. Picks add up.
- Each page is a thumbnail with its number, a camera or PDF mark, **×** (remove), **←/→**
  (move; on a desktop also drag), **↻** (replace: the page keeps its place), an optional
  label (stored, at most 80 characters) and its upload status with a progress bar.
- **Full-screen preview**: tap a thumbnail. Arrow keys or ‹ › to step, Escape or × to close;
  on a phone swipe left/right to step and down to close.
- **Saving**: in batches with a line "Uploading pages 7–12 of 40…". A page the server
  refuses says why under its thumbnail; a batch that fails as a whole (no connection, a
  limit) stops there, and **Retry the pages that failed** sends exactly what is missing.
  Nothing navigates away until everything is saved (Project Mode also offers *Continue with
  the saved pages*). Saved pages are fixed: they cannot be moved or removed in the form any
  more (the server has them), but everything not yet saved can still be edited.
- **After the upload (Project Mode)**: the workspace's *Original materials* section shows
  every page as a lazily loaded thumbnail in reading order: open it full screen, move it
  (saved at once), retake it (the chosen photo is sent at once), delete it - only while no
  topic refers to it. Once topics exist the page says so ("Pages cannot be deleted once the
  topics exist…") and the server refuses with a translated message, as before (owner
  decision: deleting a cited page stays refused).

## How it works

`static/js/image-collection.js` is the one component, built on the chat tray's primitives
in `static/js/image-attach.js` (`xhrUpload`, `normalizeImage`, `makeThumbnail`; the chat
tray's `uploadImage` is now a thin wrapper over `xhrUpload`, behaviour unchanged). Its pure
decisions are in `static/js/image-collection-rules.js` (tested by
`tests/js/image-collection-rules.test.mjs` in the browser harness).

| Part | API |
|---|---|
| `createImageCollection({root, inputs, cameraInputs, dropZone, max, maxFileBytes, accept, compress, labels, onChange, onError})` | Controller: `items()`, `keys()`, `count()`, `add(files, {kind, transform, label})`, `remove(key)`, `move(key, index)`, `replace(key, file)`, `setLabel`, `setStatus`, `prepare(key)`, `freezeOrder()`, `openPreview(i)`, `closePreview()`, `clear()`, `destroy()`. |
| `uploadCollection(collection, {send, batch, onStep})` | Sends the unsent items in order, batch by batch; `send(entries, {onProgress})` answers `{results: [{index, status, message}]}`. |
| `materialsForm(entries, base)` | The `materials` files plus `materials_meta`. |
| `createImageViewer({count, describe, onClose})` | The full-screen preview, also used by the workspace gallery (`static/js/project-pages.js`). |

**Performance.** Thumbnails are made two at a time with `createImageBitmap(file,
{resizeWidth: 320})` into a small JPEG blob, so forty full-size photos are never decoded at
once; the full picture is decoded only while the preview shows it. Every object URL is
revoked when its page is removed or replaced, when the preview moves on, and on
`clear()`/`destroy()`. The browser test measures it: forty 1600×1200 photos, all forty
thumbnails in about 1.3 s on the dev machine, longest main-thread task under 300 ms, no gap
over 400 ms in a 16 ms probe (`tests/test_multi_image_browser.py`).

**Compression.** A gallery photo over ~4 MB or 4096 px (or in a format the server does not
take, such as HEIC where the browser can decode it) is re-encoded to a 2560 px JPEG at
quality 0.9 before upload (`DEFAULT_COMPRESS`). Camera scans are never re-encoded: they stay
lossless PNG (`tests/test_handwriting_recognition.py`). The server keeps what it receives;
the copy an AI provider sees is still made per page, one page per request
(`provider_image_url`, docs/VISION.md) - never all originals at once.

**Batches.** At most 6 files and 24 MB per request (`DEFAULT_BATCH`), under the 40 MB
`MAX_CONTENT_LENGTH`. A single file over 24 MB goes alone. Vocabulary pages go one per
request.

## Server

### Project Mode

- `POST /projects` and `POST /projects/<id>/pages/add` answer JSON when the request asks for
  it first (`Accept: application/json`, `wants_json()` in app.py): `{ok, project_id,
  duplicate, add_pages_url, start_url, workspace_url, pages, max_pages, pages_added,
  results: [{index, status: added|duplicate|failed, pages, code?, message?, retryable?}],
  skipped_duplicates, failed_files}` (201 for a new project). Errors are `api_error` JSON
  (`too_many_files`, `nothing_usable` with `details.results`, `page_limit`, quota answers
  as `ai_failure_response`). A plain form post (no JavaScript) keeps its redirects and
  flashes.
- `results` come from `UploadResult.outcomes` (`learnova/uploads/service.py`), one per file
  in the order sent, so the page marks each thumbnail without matching file names.
- A batch sent twice adds nothing (sha256 per project); a create batch sent twice within two
  minutes returns the same project (`duplicate: true`).
- **`materials_meta`**: a JSON list aligned with the `materials` files, `{kind: "camera" |
  "upload", rotation, crop, label}`. Only `UPLOAD_TRANSFORM_KEYS` are kept as the page's
  `upload_transform`; the label is one line, at most 80 characters (`project_page.label`).
  Without it the old order holds: `materials` first, then `camera_scans` with
  `scan_metadata` (no-JS and old clients).
- **413, 429 and an expired CSRF token**: JSON (`upload_too_large`, `rate_limited`,
  `csrf_failed`) for a script that asks for JSON, a redirect with a flash for a browser
  navigation. (A redirect was followed by the XHR and swallowed: the page showed only
  "upload failed" and the flash surfaced on some later page.)
- **Limits**: `PROJECT_MAX_PAGES` (40, PDF pages included) across all batches - the file
  that crosses it fails with `page_limit`, the others are kept; `PROJECT_MAX_FILES_PER_REQUEST`
  (env, default 40 so a no-JS form can still post a whole project; the component sends 6);
  `/pages/add` 60 per hour per account (was 20), `/projects` unchanged at 10 per hour (one
  create per project).
- **Thumbnails**: `GET /projects/<id>/pages/<page id>/image/thumb` - a JPEG of at most 360 px,
  owner only (404 otherwise), `Cache-Control: private, max-age=3600` with an ETag (304 on
  `If-None-Match`). A photo is previewed from its original with the scanner's rotation
  (Pillow draft decode), a retake from its retake, a PDF page from its rendered picture
  (rendered on first view and stored, as the reading would). Kept in a 256-entry in-process
  cache (`learnova/uploads/thumbnails.py`). `original` and `processed` stay `no-store`.

### Vocabulary Mode (migration 045)

- Table `vocabulary_import_page` (`id`, `import_id`, `flashcard_import_id`, `position`,
  `page_count`, `label`, `status` uploaded|read|failed, `warnings_json`, `created_at`;
  unique `(import_id, position)`). Every new file import gets page 1; the first page's
  document is still `vocabulary_import.flashcard_import_id`, so the preview and older code
  keep working. An import from before has no page rows and is read and rescanned exactly as
  before; adding a page to it first makes its document page 1 (its entries become page 1's).
- `POST /api/vocabulary/imports` (unchanged, plus optional `position` and `label`).
- `POST /api/vocabulary/imports/<id>/pages` - one file (`file`, optional `position`,
  `label`); 201 with `{page, vocabulary_import}`; the same photo twice is `duplicate: true`;
  `409 page_limit` past `VOCABULARY_MAX_PAGES` (40, a PDF counts each page), `409
  position_taken`, `409 import_finished`, `400 pages_not_possible` for a text import.
  `VOCABULARY_PAGE_UPLOADS_PER_HOUR` (60) per account.
- `POST /api/vocabulary/imports/<id>/pages/<page id>/extract` - reads that page only (local
  OCR, no language model) and puts its entries in place: after earlier pages, before later
  ones, whatever order pages are read in. Reading a page again replaces exactly its entries
  (`import_page_id`, `page_in_file` on each entry; `page_number` is import-wide). A failure
  marks the page `failed`; reading it again is safe.
- `POST /api/vocabulary/imports/<id>/extract` - on an import with pages, reads every page not
  read yet (all of them when all are read); the legacy path is unchanged.
- `POST /api/vocabulary/imports/<id>/rescan` takes `page_id` (default: the first page): only
  that page's document is replaced and only its entries are merged (`merge_rescan` on that
  subset, spliced back in place); the review page shows a page choice when there are two or
  more.
- Deleting an import deletes every page's document and file (after the commit). The expiry
  clean-up and `remove_flashcard_import_dependents` delete page rows before their documents
  (PostgreSQL foreign keys; tested with SQLite foreign keys on).
- The AI second opinion (`validate`, opt-in) is asked in chunks of 40 words: one call for
  hundreds of words could never hold the answer in its output budget.

The vocabulary form creates the import with the first page, adds the others one at a time
at the place the student gave them, reads them one by one ("Reading page 3 of 12…"), then
validates and builds the cards as before.

## Not done / limits

- A page whose upload failed alone (the server refused that file) and was replaced and
  retried is appended after the pages already saved in Project Mode; the student can move it
  in the workspace gallery. (A batch that fails as a whole stops the upload, so the retry
  keeps the order.) Vocabulary pages always land at their place (explicit positions).
- The Kompetenzraster input on the project form is still a plain input (it rides with the
  first batch; its bytes are taken off that run's 24 MB batch budget).
- A page added while an upload runs (the scanner closed mid-upload) is sent in the same run,
  after the others, so the form never navigates away from an unsent page.
- Vocabulary: a page keeps the place it was saved with; an unsaved page asks for a place
  after every saved page before it, so removing a failed page and retrying never collides
  with a saved page's place. A page whose reading failed and was then removed from the
  list stays on the server as an unread page without words (there is no page-delete route).
- The flashcard import page (`/flashcards/import`) stays single-file by design: it reviews
  one document's pages.
- The tap-a-word scanner stays one photo at a time (tap a word on one page).
- PDFs show a "PDF" tile in the form (no client-side rendering); their pages appear as
  thumbnails in the workspace after the upload.
- Labels are shown in the galleries; they are not sent to any AI.

## Tests

`tests/test_multi_image_uploads.py` (JSON answers, batches, duplicates, materials_meta order,
legacy order, page limit across batches, per-request cap, JSON 413, thumbnails and their
ownership, workspace gallery, delete refusal), `tests/test_vocabulary_multi_page.py`
(create/add/read/order, idempotent reads, limits, rescan of one page, delete, expiry
clean-up with foreign keys, legacy imports, chunked second opinion),
`tests/test_image_collection_browser.py` (the component alone),
`tests/test_multi_image_browser.py` (forty images in each mode on the real app, with a
responsiveness probe), `tests/js/image-collection-rules.test.mjs`.

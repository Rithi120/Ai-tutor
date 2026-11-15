/* The multi-image collection's pure decisions (static/js/image-collection.js).
 *
 * No DOM and no browser API, so tests/js/image-collection-rules.test.mjs runs them as they
 * ship (through tests/test_js_browser.py, since Node is not installed here). Everything the
 * component decides about files, order, limits, batches and gestures lives here.
 */

export const DEFAULT_MAX_ITEMS = 40;
// Under the server's 40 MB MAX_CONTENT_LENGTH with room for the form fields and multipart
// overhead; six files keep each request short on a phone's upload link.
export const DEFAULT_BATCH = Object.freeze({ maxFiles: 6, maxBytes: 24 * 1024 * 1024 });
// A gallery photo is re-encoded (2560 px JPEG, quality 0.9) only past ~4 MB or 4096 px;
// camera scans never are (they stay lossless, tests/test_handwriting_recognition.py).
export const DEFAULT_COMPRESS = Object.freeze({ maxEdge: 2560, quality: 0.9, minBytes: 4_000_000, keepEdge: 4096 });
export const LABEL_MAX = 80;
export const SWIPE_MIN_PX = 48;

const IMAGE_EXTENSIONS = /\.(jpe?g|png|webp|heic|heif)$/i;
const HEIC = /\.(heic|heif)$/i;

function extensionOf(name) {
  const match = /\.([a-z0-9]+)$/i.exec(String(name || ""));
  return match ? `.${match[1].toLowerCase()}` : "";
}

/* "application/pdf,image/jpeg,.png,image/*" -> a test of one file against that list, the
 * way a browser's file picker reads its accept attribute. HEIC/HEIF photos pass whenever
 * images do (browsers often report them with an empty type). */
export function acceptsFile(file, accept) {
  const rules = String(accept || "").split(",").map(rule => rule.trim().toLowerCase()).filter(Boolean);
  if (!rules.length) return true;
  const type = String(file?.type || "").toLowerCase();
  const extension = extensionOf(file?.name);
  const imagesAllowed = rules.some(rule => rule === "image/*" || rule.startsWith("image/"));
  for (const rule of rules) {
    if (rule.startsWith(".")) {
      if (rule === extension) return true;
    } else if (rule.endsWith("/*")) {
      if (type.startsWith(rule.slice(0, -1))) return true;
    } else if (rule === type) {
      return true;
    }
  }
  if (imagesAllowed && HEIC.test(file?.name || "")) return true;
  // A browser that reports no type: judge by the extension alone.
  if (!type && imagesAllowed && IMAGE_EXTENSIONS.test(file?.name || "")) return true;
  if (!type && extension === ".pdf" && rules.some(rule => rule === "application/pdf" || rule === ".pdf")) return true;
  return false;
}

/* null when the file may join the collection, otherwise why not: "not_supported" or
 * "too_large" (over maxBytes before any re-encoding; a photo that compression would
 * bring under the limit is still allowed when `compressible`). */
export function validateFile(file, { accept = "", maxBytes = 0, compressible = false } = {}) {
  if (!file) return "not_supported";
  if (!acceptsFile(file, accept)) return "not_supported";
  if (!(Number(file.size) > 0)) return "empty";
  if (maxBytes && file.size > maxBytes && !(compressible && isImage(file))) return "too_large";
  return null;
}

export function isPdf(file) {
  return String(file?.type || "").toLowerCase() === "application/pdf" || extensionOf(file?.name) === ".pdf";
}

export function isImage(file) {
  return !isPdf(file) && (String(file?.type || "").startsWith("image/") || IMAGE_EXTENSIONS.test(file?.name || ""));
}

/* How many of `adding` fit next to `current` under `max`. */
export function clampCount(current, adding, max) {
  const room = Math.max(0, Number(max) - Number(current));
  const accepted = Math.min(room, Math.max(0, Number(adding)));
  return { accepted, refused: Math.max(0, Number(adding) - accepted) };
}

/* A copy of `list` with the item at `from` moved to `to` (both clamped to the list). */
export function moveItem(list, from, to) {
  const copy = [...list];
  if (!copy.length) return copy;
  const source = Math.min(Math.max(0, Number(from) || 0), copy.length - 1);
  const target = Math.min(Math.max(0, Number(to) || 0), copy.length - 1);
  if (source === target) return copy;
  const [item] = copy.splice(source, 1);
  copy.splice(target, 0, item);
  return copy;
}

/* Whether one more file of `size` bytes still fits in a batch that holds `count` files
 * of `bytes` in all. An empty batch always takes one file, however large: a single file
 * over the byte budget goes alone (the server says if it is too large). */
export function batchHasRoom(count, bytes, size, { maxFiles = DEFAULT_BATCH.maxFiles, maxBytes = DEFAULT_BATCH.maxBytes } = {}) {
  if (count === 0) return true;
  return count < maxFiles && bytes + Number(size || 0) <= maxBytes;
}

/* Split `items` ({size}) into upload batches in order. */
export function planBatches(items, limits = DEFAULT_BATCH) {
  const batches = [];
  let current = [];
  let bytes = 0;
  for (const item of items || []) {
    const size = Number(item?.size || 0);
    if (!batchHasRoom(current.length, bytes, size, limits)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(item);
    bytes += size;
  }
  if (current.length) batches.push(current);
  return batches;
}

/* Whether a picked photo is re-encoded before upload (see DEFAULT_COMPRESS). */
export function needsCompression({ size = 0, width = 0, height = 0, type = "" } = {}, options = DEFAULT_COMPRESS) {
  if (!options) return false;
  const supported = /^image\/(jpeg|png|webp)$/.test(String(type));
  const longEdge = Math.max(Number(width) || 0, Number(height) || 0);
  if (!supported) return true;   // HEIC and friends: a JPEG wherever the browser can decode it
  return Number(size) >= (options.minBytes ?? DEFAULT_COMPRESS.minBytes)
    || longEdge > (options.keepEdge ?? DEFAULT_COMPRESS.keepEdge);
}

/* One line, at most LABEL_MAX characters (the server keeps the same). */
export function cleanLabel(value) {
  return String(value ?? "").replace(/\s+/g, " ").trim().slice(0, LABEL_MAX);
}

/* What a finished swipe on the full-screen preview means: "next", "previous", "close"
 * (a downward swipe) or null (a tap or a short drag). */
export function swipeAction(dx, dy, threshold = SWIPE_MIN_PX) {
  const x = Number(dx) || 0;
  const y = Number(dy) || 0;
  if (Math.abs(x) >= threshold && Math.abs(x) > Math.abs(y) * 1.2) return x < 0 ? "next" : "previous";
  if (y >= threshold * 1.5 && y > Math.abs(x) * 1.2) return "close";
  return null;
}

/* The index a step of `step` from `index` reaches in a list of `count` (wraps). */
export function stepIndex(index, step, count) {
  if (!count) return 0;
  return ((Number(index) + Number(step)) % count + count) % count;
}

/* Counts of the items' statuses, for the summary line and the submit button. */
export function statusCounts(items) {
  const counts = { ready: 0, uploading: 0, done: 0, failed: 0, total: 0 };
  for (const item of items || []) {
    counts.total += 1;
    if (Object.prototype.hasOwnProperty.call(counts, item?.status)) counts[item.status] += 1;
  }
  return counts;
}

/* The items still to send, in order: everything not yet uploaded. */
export function pendingItems(items) {
  return (items || []).filter(item => item && item.status !== "done");
}

/* A server's per-file answers ({index, status, message}) mapped onto the batch it was
 * sent as: [{item, status: "done" | "failed", duplicate, error}]. An item the server did not
 * answer for is failed (it must be sent again), never silently "done". */
export function applyResults(batch, results, fallbackError = "") {
  const byIndex = new Map((results || []).map(result => [Number(result?.index), result]));
  return batch.map((item, index) => {
    const result = byIndex.get(index);
    if (!result) return { item, status: "failed", duplicate: false, error: fallbackError };
    if (result.status === "failed") return { item, status: "failed", duplicate: false, error: String(result.message || fallbackError) };
    return { item, status: "done", duplicate: result.status === "duplicate", error: "" };
  });
}

/* The materials_meta entry the server reads for one item (app.uploads_from_request). */
export function metaFor(item) {
  const meta = { kind: item?.kind === "camera" ? "camera" : "upload" };
  const transform = item?.transform || {};
  if (Number.isFinite(Number(transform.rotation)) && Number(transform.rotation)) meta.rotation = Number(transform.rotation) % 360;
  if (transform.crop && typeof transform.crop === "object") meta.crop = transform.crop;
  const label = cleanLabel(item?.label);
  if (label) meta.label = label;
  return meta;
}

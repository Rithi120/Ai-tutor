// The multi-image collection's pure rules (static/js/image-collection-rules.js). Run with:
// node --test tests/js (Node is not installed here: tests/test_js_browser.py runs this file
// unchanged in Chrome).
import test from "node:test";
import assert from "node:assert/strict";
import {
  DEFAULT_BATCH, DEFAULT_COMPRESS, LABEL_MAX, acceptsFile, applyResults, batchHasRoom, clampCount, cleanLabel,
  isImage, isPdf, metaFor, moveItem, needsCompression, pendingItems, planBatches, statusCounts, stepIndex,
  swipeAction, validateFile,
} from "../../static/js/image-collection-rules.js";

const MB = 1024 * 1024;
const PROJECT_ACCEPT = "application/pdf,image/jpeg,image/png,image/webp";
const VOCABULARY_ACCEPT = ".pdf,.jpg,.jpeg,.png,.webp,image/*";
const file = (name, type, size = 1000) => ({ name, type, size });

test("the batch budget stays under the server's 40 MB request limit", () => {
  assert.equal(DEFAULT_BATCH.maxFiles, 6);
  assert.ok(DEFAULT_BATCH.maxBytes < 40 * MB);
  assert.deepEqual({ ...DEFAULT_COMPRESS }, { maxEdge: 2560, quality: 0.9, minBytes: 4_000_000, keepEdge: 4096 });
});

test("planBatches keeps the order and respects the file count", () => {
  const items = Array.from({ length: 14 }, (_, index) => ({ id: index, size: MB }));
  const batches = planBatches(items, { maxFiles: 6, maxBytes: 100 * MB });
  assert.deepEqual(batches.map(batch => batch.length), [6, 6, 2]);
  assert.deepEqual(batches.flat().map(item => item.id), items.map(item => item.id));
});

test("planBatches respects the byte budget", () => {
  const items = [10, 10, 10, 3, 20].map((size, id) => ({ id, size: size * MB }));
  const batches = planBatches(items, { maxFiles: 6, maxBytes: 24 * MB });
  assert.deepEqual(batches.map(batch => batch.map(item => item.id)), [[0, 1], [2, 3], [4]]);
});

test("one file over the byte budget goes alone, never dropped", () => {
  const items = [{ id: "a", size: MB }, { id: "big", size: 30 * MB }, { id: "c", size: MB }];
  const batches = planBatches(items, { maxFiles: 6, maxBytes: 24 * MB });
  assert.deepEqual(batches.map(batch => batch.map(item => item.id)), [["a"], ["big"], ["c"]]);
  assert.deepEqual(planBatches([], DEFAULT_BATCH), []);
});

test("batchHasRoom: an empty batch always takes one file", () => {
  assert.equal(batchHasRoom(0, 0, 99 * MB, { maxFiles: 1, maxBytes: MB }), true);
  assert.equal(batchHasRoom(1, MB, 1, { maxFiles: 1, maxBytes: 99 * MB }), false);
  assert.equal(batchHasRoom(2, 20 * MB, 5 * MB, { maxFiles: 6, maxBytes: 24 * MB }), false);
  assert.equal(batchHasRoom(2, 20 * MB, 4 * MB, { maxFiles: 6, maxBytes: 24 * MB }), true);
});

test("moveItem moves and clamps, and never changes the original", () => {
  const list = ["a", "b", "c", "d"];
  assert.deepEqual(moveItem(list, 0, 2), ["b", "c", "a", "d"]);
  assert.deepEqual(moveItem(list, 3, 0), ["d", "a", "b", "c"]);
  assert.deepEqual(moveItem(list, 1, 99), ["a", "c", "d", "b"]);
  assert.deepEqual(moveItem(list, -5, 1), ["b", "a", "c", "d"]);
  assert.deepEqual(moveItem(list, 2, 2), list);
  assert.deepEqual(list, ["a", "b", "c", "d"]);
  assert.deepEqual(moveItem([], 0, 1), []);
});

test("validateFile: type by the accept list, HEIC by name, size", () => {
  assert.equal(validateFile(file("a.jpg", "image/jpeg"), { accept: PROJECT_ACCEPT }), null);
  assert.equal(validateFile(file("a.pdf", "application/pdf"), { accept: PROJECT_ACCEPT }), null);
  assert.equal(validateFile(file("a.gif", "image/gif"), { accept: PROJECT_ACCEPT }), "not_supported");
  assert.equal(validateFile(file("notes.docx", "application/msword"), { accept: PROJECT_ACCEPT }), "not_supported");
  assert.equal(validateFile(file("IMG_1.HEIC", ""), { accept: PROJECT_ACCEPT }), null, "HEIC is a photo");
  assert.equal(validateFile(file("IMG_1.heic", "image/heic"), { accept: VOCABULARY_ACCEPT }), null);
  assert.equal(validateFile(file("scan.pdf", ""), { accept: VOCABULARY_ACCEPT }), null, "a PDF without a type");
  assert.equal(validateFile(file("a.png", "image/png", 0), { accept: PROJECT_ACCEPT }), "empty");
  assert.equal(validateFile(file("a.png", "image/png", 20 * MB), { accept: PROJECT_ACCEPT, maxBytes: 15 * MB }), "too_large");
  assert.equal(validateFile(file("a.png", "image/png", 20 * MB), { accept: PROJECT_ACCEPT, maxBytes: 15 * MB, compressible: true }),
    null, "a photo that will be re-encoded may start larger");
  assert.equal(validateFile(file("a.pdf", "application/pdf", 20 * MB), { accept: PROJECT_ACCEPT, maxBytes: 15 * MB, compressible: true }),
    "too_large", "a PDF is never re-encoded");
  assert.equal(validateFile(null), "not_supported");
  assert.equal(acceptsFile(file("x.anything", "application/zip"), ""), true, "no accept list takes anything");
});

test("isPdf and isImage", () => {
  assert.equal(isPdf(file("a.PDF", "")), true);
  assert.equal(isImage(file("a.PDF", "")), false);
  assert.equal(isImage(file("a.webp", "")), true);
  assert.equal(isImage(file("photo", "image/png")), true);
});

test("clampCount: the collection limit", () => {
  assert.deepEqual(clampCount(38, 5, 40), { accepted: 2, refused: 3 });
  assert.deepEqual(clampCount(40, 1, 40), { accepted: 0, refused: 1 });
  assert.deepEqual(clampCount(0, 3, 40), { accepted: 3, refused: 0 });
});

test("needsCompression: past ~4 MB or 4096 px, or a format the server does not take", () => {
  assert.equal(needsCompression({ size: 2 * MB, width: 3000, height: 4000, type: "image/jpeg" }), false);
  assert.equal(needsCompression({ size: 5 * MB, width: 3000, height: 4000, type: "image/jpeg" }), true);
  assert.equal(needsCompression({ size: MB, width: 4000, height: 6000, type: "image/png" }), true);
  assert.equal(needsCompression({ size: MB, width: 100, height: 100, type: "image/heic" }), true);
  assert.equal(needsCompression({ size: 9 * MB, width: 100, height: 100, type: "image/jpeg" }, false), false);
});

test("cleanLabel: one line, at most 80 characters", () => {
  assert.equal(LABEL_MAX, 80);
  assert.equal(cleanLabel("  Arbeitsblatt\n 3 "), "Arbeitsblatt 3");
  assert.equal(cleanLabel("x".repeat(200)).length, 80);
  assert.equal(cleanLabel(null), "");
});

test("swipeAction: left and right page, down closes, a tap does nothing", () => {
  assert.equal(swipeAction(-120, 10), "next");
  assert.equal(swipeAction(120, -10), "previous");
  assert.equal(swipeAction(5, 140), "close");
  assert.equal(swipeAction(10, 10), null);
  assert.equal(swipeAction(60, 60), null, "a diagonal drag is no swipe");
  assert.equal(swipeAction(0, -150), null, "an upward swipe does not close");
});

test("stepIndex wraps around", () => {
  assert.equal(stepIndex(0, -1, 40), 39);
  assert.equal(stepIndex(39, 1, 40), 0);
  assert.equal(stepIndex(5, 1, 40), 6);
  assert.equal(stepIndex(3, 1, 0), 0);
});

test("statusCounts and pendingItems", () => {
  const items = [{ status: "done" }, { status: "failed" }, { status: "ready" }, { status: "uploading" }, { status: "done" }];
  assert.deepEqual(statusCounts(items), { ready: 1, uploading: 1, done: 2, failed: 1, total: 5 });
  assert.deepEqual(pendingItems(items).map(item => item.status), ["failed", "ready", "uploading"]);
});

test("applyResults maps the server's per-file answers onto the batch", () => {
  const batch = ["a", "b", "c", "d"];
  const outcome = applyResults(batch, [
    { index: 0, status: "added" }, { index: 1, status: "duplicate" },
    { index: 2, status: "failed", message: "damaged" },
  ], "not saved");
  assert.deepEqual(outcome.map(item => [item.item, item.status, item.duplicate, item.error]), [
    ["a", "done", false, ""], ["b", "done", true, ""], ["c", "failed", false, "damaged"], ["d", "failed", false, "not saved"],
  ]);
  assert.equal(applyResults(["x"], undefined, "lost")[0].status, "failed", "no answer is never success");
});

test("metaFor: the materials_meta entry the server reads", () => {
  assert.deepEqual(metaFor({ kind: "upload", transform: {}, label: "" }), { kind: "upload" });
  assert.deepEqual(metaFor({ kind: "camera", transform: { rotation: 450, crop: { left: 0.1 } }, label: " Seite  1 " }),
    { kind: "camera", rotation: 90, crop: { left: 0.1 }, label: "Seite 1" });
  assert.deepEqual(metaFor({ kind: "something", transform: { rotation: "x" } }), { kind: "upload" });
});

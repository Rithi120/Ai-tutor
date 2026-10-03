// Unit tests for the pure definition-suggestion rules. Run with: node --test tests/js/*.test.mjs
// (Node is not installed in the current CI image.)
//
// Every guard here spends or saves one of a student's limited AI requests, so each one
// gets its own named subtest rather than a single opaque assertion.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  shouldRequestSuggestions, cacheKey, pickSuggestions, trimCache, MAX_SUGGESTIONS,
} from "../../static/js/flashcards/suggest-rules.js";

const base = { front: "Photosynthesis", back: "", lastRequested: "", busy: false, enabled: true };

test("a typed term with an empty definition is worth asking about", () => {
  assert.equal(shouldRequestSuggestions(base), true);
});

test("a definition the student already wrote is never competed with", () => {
  assert.equal(shouldRequestSuggestions({ ...base, back: "Already answered" }), false);
});

test("a one-character term is not a term", () => {
  assert.equal(shouldRequestSuggestions({ ...base, front: "x" }), false);
});

test("a pasted paragraph belongs in Generate with AI, not one card", () => {
  assert.equal(shouldRequestSuggestions({ ...base, front: "y".repeat(300) }), false);
});

test("re-focusing a field without editing it does not re-ask", () => {
  assert.equal(
    shouldRequestSuggestions({ ...base, lastRequested: "  Photosynthesis " }), false);
});

test("only one request is in flight at a time", () => {
  assert.equal(shouldRequestSuggestions({ ...base, busy: true }), false);
});

test("the student can switch automatic suggestions off", () => {
  assert.equal(shouldRequestSuggestions({ ...base, enabled: false }), false);
});

test("the same term in a different subject is a different question", () => {
  const biology = cacheKey({ front: "Cell", subject: "Biology", cardType: "mixed", language: "en" });
  const history = cacheKey({ front: "Cell", subject: "History", cardType: "mixed", language: "en" });
  assert.notEqual(biology, history);
});

test("casing and spacing do not split the cache", () => {
  assert.equal(
    cacheKey({ front: "  Cell   Wall ", subject: "Biology", cardType: "mixed", language: "en" }),
    cacheKey({ front: "cell wall", subject: "Biology", cardType: "mixed", language: "en" }));
});

test("at most three suggestions reach the student, whatever the model returns", () => {
  const many = { suggestions: Array.from({ length: 12 }, (_, n) => ({ back: `Definition ${n}` })) };
  assert.equal(pickSuggestions(many).length, MAX_SUGGESTIONS);
});

test("blank and duplicate suggestions are dropped", () => {
  const picked = pickSuggestions({ suggestions: [
    { back: "  " }, { back: "A cell wall" }, { back: "a cell wall" }, { back: "Something else" },
  ] });
  assert.deepEqual(picked.map(item => item.back), ["A cell wall", "Something else"]);
});

test("a malformed payload yields nothing rather than throwing", () => {
  for (const payload of [null, undefined, {}, { suggestions: "text" }]) {
    assert.deepEqual(pickSuggestions(payload), []);
  }
});

test("the cache keeps the newest entries and stays bounded", () => {
  const entries = { a: { at: 1 }, b: { at: 5 }, c: { at: 3 } };
  assert.deepEqual(Object.keys(trimCache(entries, 2)).sort(), ["b", "c"]);
  assert.deepEqual(trimCache(entries, 10), entries);
});

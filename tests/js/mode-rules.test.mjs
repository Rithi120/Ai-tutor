// Unit tests for the pure flashcard game rules. Run with: node --test tests/js/*.test.mjs
// (Node is not installed in the current CI image.)
//
// Uses node:test like its siblings, so `npm run test:js` reports named subtests rather
// than a single opaque pass/fail for the whole file.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  normalizedAnswer, scoreAnswer, nextLives, shouldReduceMotion, matchPair,
} from "../../static/js/flashcards/mode-rules.js";

test("answers are normalised before comparison", () => {
  assert.equal(normalizedAnswer("  Paris. "), "paris");
});

test("a correct answer extends the combo and scores", () => {
  assert.deepEqual(scoreAnswer(true, 2, "blast"), { combo: 3, points: 130 });
});

test("a wrong answer resets the combo and scores nothing", () => {
  assert.deepEqual(scoreAnswer(false, 4, "blocks"), { combo: 0, points: 0 });
});

test("lives decrease on a miss and never go below zero", () => {
  assert.equal(nextLives(3, false), 2);
  assert.equal(nextLives(0, false), 0);
});

test("reduced motion is honoured", () => {
  assert.equal(shouldReduceMotion(true, false), true);
});

test("a pair matches only when it is the same card and opposite sides", () => {
  assert.equal(matchPair({ cardId: 2, side: "front" }, { cardId: 2, side: "back" }), true);
  assert.equal(matchPair({ cardId: 2, side: "front" }, { cardId: 3, side: "back" }), false);
});

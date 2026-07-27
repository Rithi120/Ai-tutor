import assert from "node:assert/strict";
import { normalizedAnswer, scoreAnswer, nextLives, shouldReduceMotion, matchPair } from "../../static/js/flashcards/mode-rules.js";

assert.equal(normalizedAnswer("  Paris. "), "paris");
assert.deepEqual(scoreAnswer(true, 2, "blast"), { combo: 3, points: 130 });
assert.deepEqual(scoreAnswer(false, 4, "blocks"), { combo: 0, points: 0 });
assert.equal(nextLives(3, false), 2);
assert.equal(nextLives(0, false), 0);
assert.equal(shouldReduceMotion(true, false), true);
assert.equal(matchPair({ cardId: 2, side: "front" }, { cardId: 2, side: "back" }), true);
assert.equal(matchPair({ cardId: 2, side: "front" }, { cardId: 3, side: "back" }), false);
console.log("mode-rules tests passed");

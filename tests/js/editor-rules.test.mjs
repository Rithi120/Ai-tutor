// Run with: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import { isBlankCard, mergeGeneratedCards, needsMathPreview } from "../../static/js/flashcards/editor-rules.js";

test("generated cards replace the empty starter rows and keep typed ones", () => {
  const typed = { front: "Osmosis", back: "Water moves" };
  const merged = mergeGeneratedCards([{ front: "", back: "" }, typed, { front: " ", back: "" }], [{ front: "A", back: "B" }]);
  assert.deepEqual(merged, [typed, { front: "A", back: "B" }]);
  assert.equal(isBlankCard({ front: "  ", back: "" }), true);
});

test("a field gets a maths preview only when it carries maths", () => {
  assert.equal(needsMathPreview("Was ist der Wert von $\sqrt{81}$?"), true);
  assert.equal(needsMathPreview("$$A = \frac{1}{2} b h$$"), true);
  assert.equal(needsMathPreview("Kostet 5$ oder 6$"), false);
  assert.equal(needsMathPreview("Plain text"), false);
  assert.equal(needsMathPreview(""), false);
});

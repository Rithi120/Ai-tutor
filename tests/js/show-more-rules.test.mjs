// "Show more" for long lists: the pure rules (static/js/show-more-rules.js). Run with:
// node --test tests/js (Node is not installed here: tests/test_js_browser.py runs this file
// unchanged in Chrome).
import test from "node:test";
import assert from "node:assert/strict";
import { MIN_HIDDEN, buttonState, initialState, nextShown, parseCount } from "../../static/js/show-more-rules.js";

test("parseCount reads whole positive numbers only", () => {
  assert.equal(parseCount("5"), 5);
  assert.equal(parseCount(" 12 "), 12);
  for (const value of ["0", "-3", "2.5", "abc", "", null, undefined]) {
    assert.equal(parseCount(value), 0, String(value));
  }
  assert.equal(parseCount("x", 7), 7);
});

test("a long list starts with the first items and a step that defaults to that count", () => {
  assert.deepEqual(initialState(12, "5", undefined), { collapsible: true, initial: 5, step: 5, total: 12, shown: 5 });
  assert.deepEqual(initialState(30, "3", "5"), { collapsible: true, initial: 3, step: 5, total: 30, shown: 3 });
});

test("a short list, or one that would hide a single item, stays open", () => {
  assert.equal(MIN_HIDDEN, 2);
  assert.equal(initialState(3, "3").collapsible, false);
  assert.equal(initialState(4, "3").collapsible, false, "one hidden item is not worth a tap");
  assert.equal(initialState(5, "3").collapsible, true);
  assert.equal(initialState(0, "3").collapsible, false);
  const open = initialState(4, "3");
  assert.equal(open.shown, 4, "everything is shown");
});

test("a list without a valid count is never folded", () => {
  assert.equal(initialState(40, "").collapsible, false);
  assert.equal(initialState(40, "zero").collapsible, false);
  assert.equal(initialState(40, "0").shown, 40);
});

test("each tap reveals the next step, then everything, then folds back", () => {
  const state = initialState(13, "3", "5");
  state.shown = nextShown(state);
  assert.equal(state.shown, 8);
  state.shown = nextShown(state);
  assert.equal(state.shown, 13, "the last five");
  state.shown = nextShown(state);
  assert.equal(state.shown, 3, "Show less goes back to the first items");
});

test("a step never leaves a single item behind", () => {
  const state = initialState(9, "3", "5");
  state.shown = nextShown(state);
  assert.equal(state.shown, 9, "3 + 5 would leave one: it is shown too");
});

test("the button says how many are hidden, and Show less once all are shown", () => {
  const state = initialState(8, "3", "5");
  assert.deepEqual(buttonState(state), { kind: "more", remaining: 5, expanded: false });
  state.shown = nextShown(state);
  assert.deepEqual(buttonState(state), { kind: "less", remaining: 0, expanded: true });
});

// Run with: node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";
import { STEPS, advancesOn, isFinished, pageKey, parseState, resolveStep, shouldAutoStart } from "../../static/js/tour-rules.js";

test("pages are recognised by path, with or without a trailing slash", () => {
  assert.equal(pageKey("/"), "index");
  assert.equal(pageKey("/dashboard"), "dashboard");
  assert.equal(pageKey("/dashboard/"), "dashboard");
  assert.equal(pageKey("/projects"), "other");
});

test("a nav step is skipped when the student is already on its page", () => {
  const goNewLesson = STEPS.findIndex(step => step.id === "go-new-lesson");
  assert.equal(resolveStep(goNewLesson, "index").step.id, "subject");
  assert.equal(resolveStep(goNewLesson, "dashboard").step.id, "go-new-lesson");
});

test("a page-bound step on the wrong page becomes a detour that keeps the stored index", () => {
  const build = STEPS.findIndex(step => step.id === "build");
  const resolved = resolveStep(build, "dashboard");
  assert.equal(resolved.step.id, "go-new-lesson");
  assert.equal(resolved.detour, true);
  assert.equal(resolved.index, build);
});

test("the tour ends past the last step", () => {
  assert.equal(resolveStep(STEPS.length, "index").step, null);
  assert.equal(isFinished(STEPS.length), true);
  assert.equal(isFinished(0), false);
});

test("it opens by itself only for an account that never finished it and only without a stored run", () => {
  assert.equal(shouldAutoStart({ autostart: true, storedIndex: null }), true);
  assert.equal(shouldAutoStart({ autostart: true, storedIndex: 3 }), false);
  assert.equal(shouldAutoStart({ autostart: false, storedIndex: null }), false);
});

test("the student's own action advances a step, nothing else does", () => {
  const click = STEPS.find(step => step.advance === "click");
  const change = STEPS.find(step => step.advance === "change");
  const input = STEPS.find(step => step.advance === "input");
  assert.equal(advancesOn(click, { type: "click", insideTarget: true }), true);
  assert.equal(advancesOn(click, { type: "click", insideTarget: false }), false);
  assert.equal(advancesOn(change, { type: "change", insideTarget: true }), true);
  assert.equal(advancesOn(input, { type: "input", insideTarget: true, textLength: 2 }), false);
  assert.equal(advancesOn(input, { type: "input", insideTarget: true, textLength: 3 }), true);
  assert.equal(advancesOn(STEPS[0], { type: "click", insideTarget: true }), false, "a card waits for its button");
});

test("stored state is read defensively", () => {
  assert.equal(parseState(null), null);
  assert.equal(parseState("garbage"), null);
  assert.equal(parseState('{"index": -1}'), null);
  assert.equal(parseState('{"index": 4}'), 4);
});

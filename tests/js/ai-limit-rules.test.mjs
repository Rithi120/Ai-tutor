import test from "node:test";
import assert from "node:assert/strict";
import { describeAiLimit, formatRetry, withRetryHint, aiNoticeFrom, noticeDue } from "../../static/js/ai-limit-rules.js";

test("a spent site budget pauses automatic features and says when to retry", () => {
  const result = describeAiLimit({ code: "ai_budget_exhausted", status: 503, details: { retry_after: 7200 } });
  assert.deepEqual(result, { paused: true, pausedByBudget: true, retryText: "2 h" });
});

test("a spent user quota pauses too, even with no retry time", () => {
  const result = describeAiLimit({ code: "ai_limit_reached", status: 429 });
  assert.equal(result.paused, true);
  assert.equal(result.pausedByBudget, true);
  assert.equal(result.retryText, null);
});

test("a busy provider is momentary and must not pause anything", () => {
  const result = describeAiLimit({ code: "ai_provider_busy", status: 503, details: { retry_after: 20 } });
  assert.equal(result.paused, true);
  assert.equal(result.pausedByBudget, false);
  assert.equal(result.retryText, "20 s");
});

test("unrelated errors pass through untouched", () => {
  assert.deepEqual(describeAiLimit({ code: "set_not_found", status: 404 }),
    { paused: false, pausedByBudget: false, retryText: null });
  assert.deepEqual(describeAiLimit(undefined), { paused: false, pausedByBudget: false, retryText: null });
  assert.equal(withRetryHint("Saved.", { code: "set_not_found" }), "Saved.");
});

test("retry times read like a person would say them", () => {
  assert.equal(formatRetry(1), "1 s");
  assert.equal(formatRetry(89), "89 s");
  assert.equal(formatRetry(90), "2 min");
  assert.equal(formatRetry(3600), "60 min");
  assert.equal(formatRetry(5400), "2 h");
  assert.equal(formatRetry(172800), "2 d");
  assert.equal(formatRetry(0), null);
  assert.equal(formatRetry("soon"), null);
});

test("the hint is appended in parentheses", () => {
  const error = { code: "ai_budget_exhausted", status: 503, details: { retry_after: 600 } };
  assert.equal(withRetryHint("AI help is paused.", error), "AI help is paused. (10 min)");
});

test("a slow-model notice is read from a successful answer and nothing else", () => {
  assert.deepEqual(aiNoticeFrom({ ok: true, ai_notice: { code: "ai_slow_model", message: "Max limit reached - using a slower AI model." } }),
    { code: "ai_slow_model", message: "Max limit reached - using a slower AI model." });
  assert.equal(aiNoticeFrom({ ok: true }), null);
  assert.equal(aiNoticeFrom({ ai_notice: { code: "something_else", message: "x" } }), null);
  assert.equal(aiNoticeFrom(null), null);
});

test("the banner is not repeated within a minute", () => {
  assert.equal(noticeDue(0, 1000), true);
  assert.equal(noticeDue(1000, 30000), false);
  assert.equal(noticeDue(1000, 61001), true);
  assert.equal(noticeDue("garbage", 5), true);
});

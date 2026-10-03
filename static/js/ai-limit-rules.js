// Pure rules for showing an AI limit to a student. No DOM, so tests/js can run them.
//
// The server says *which* ceiling was hit through `code` and, when it knows, how long
// until it resets through `details.retry_after` (seconds). The page's job is only to turn
// that into one short, honest sentence - never to retry on its own.

export const AI_LIMIT_CODES = new Set(["ai_limit_reached", "ai_budget_exhausted", "ai_provider_busy"]);

export function formatRetry(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return null;
  if (value < 90) return `${Math.max(1, Math.round(value))} s`;
  if (value < 5400) return `${Math.round(value / 60)} min`;
  if (value < 172800) return `${Math.round(value / 3600)} h`;
  return `${Math.round(value / 86400)} d`;
}

export function describeAiLimit(error) {
  const code = error?.code;
  const status = Number(error?.status);
  const limited = AI_LIMIT_CODES.has(code) || status === 429;
  if (!limited) return { paused: false, pausedByBudget: false, retryText: null };
  return {
    paused: true,
    // A spent budget - the student's own or the site's - stays spent until it resets, so
    // features that fire automatically (suggestions as you type) should stop asking.
    // A busy provider is momentary and should not pause anything.
    pausedByBudget: code === "ai_limit_reached" || code === "ai_budget_exhausted",
    retryText: formatRetry(error?.details?.retry_after),
  };
}

export function withRetryHint(message, error) {
  const { retryText } = describeAiLimit(error);
  return retryText ? `${message} (${retryText})` : message;
}

// A successful answer can still carry a notice: the server fell back to a slower model
// because a limit was hit (`ai_notice` on the JSON). Pure helpers so the banner logic in
// core.js stays a few lines and the rule can be tested without a browser.
export const AI_NOTICE_CODES = new Set(["ai_slow_model"]);

export function aiNoticeFrom(payload) {
  const notice = payload && typeof payload === "object" ? payload.ai_notice : null;
  if (!notice || !AI_NOTICE_CODES.has(notice.code) || !notice.message) return null;
  return { code: notice.code, message: String(notice.message) };
}

// Show the banner again only after a quiet gap, so a page firing many requests while
// degraded does not stack the same sentence ten times.
export function noticeDue(lastShownAt, now, minimumGapMs = 60000) {
  const last = Number(lastShownAt);
  if (!Number.isFinite(last) || last <= 0) return true;
  return now - last >= minimumGapMs;
}

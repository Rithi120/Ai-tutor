// Pure rules for the chat page (static/js/assistant.js). No DOM, no fetch: the decisions
// that are easy to get subtly wrong - which conversations a list shows under which date
// heading, when the thread may scroll by itself, how the row menu moves with the arrow
// keys, what the Continue Learning control shows, how a failed request is worded - can be
// tested on their own in the browser harness (tests/test_assistant_browser.py).

// The server's date groups (learnova/assistant/history.py GROUPS), in display order, and
// the catalogue keys of their headings.
export const GROUP_LABELS = {
  today: "asstGroupToday",
  yesterday: "asstGroupYesterday",
  previous_7_days: "asstGroupPrevious7",
  older: "asstGroupOlder",
};

// The rows of one list, grouped under their headings in the order the API sent them (it
// sorts newest first, so the groups come out in date order). A conversation without a
// single message is a draft and only listed while it is the open one; an archived list
// and search results have no date headings worth showing in their own right, but keep the
// server's order. Rows without a group (an older server) go under "older".
export function groupRows(rows, { activeId = null } = {}) {
  const groups = [];
  const byKey = new Map();
  for (const row of rows || []) {
    if (!row || !row.id) continue;
    if (!row.message_count && row.id !== activeId) continue;
    const key = GROUP_LABELS[row.group] ? row.group : "older";
    if (!byKey.has(key)) {
      const group = { key, label: GROUP_LABELS[key], rows: [] };
      byKey.set(key, group);
      groups.push(group);
    }
    byKey.get(key).rows.push(row);
  }
  return groups;
}

// Merge one more page into a list without duplicates (a row that moved between pages, or
// a "Load more" after the list was refreshed), keeping the first copy's position.
export function mergeRows(current, incoming) {
  const seen = new Set((current || []).map((row) => row.id));
  return [...(current || []), ...(incoming || []).filter((row) => row && !seen.has(row.id))];
}

// The date group of a moment in the browser's own calendar, for a conversation the list
// API did not group (one opened by its address or just changed): the same rule as the
// server's history.date_group - same day, the day before, 2-7 days, older.
export function groupFor(isoMoment, now = new Date()) {
  const moment = new Date(isoMoment);
  if (Number.isNaN(moment.getTime())) return "older";
  const day = (value) => Date.UTC(value.getFullYear(), value.getMonth(), value.getDate());
  const days = Math.round((day(now) - day(moment)) / 86400000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days <= 7) return "previous_7_days";
  return "older";
}

// Put an updated conversation at the top of the list (it has a new message), or update it
// in place when `moveToTop` is false (a rename, a restyle, an open); one the list did not
// have yet goes where its last message puts it, so the date headings stay in order.
export function upsertRow(rows, conversation, { moveToTop = true, now = new Date() } = {}) {
  if (!conversation || !conversation.id) return rows || [];
  const list = (rows || []).filter((row) => row.id !== conversation.id);
  const previous = (rows || []).find((row) => row.id === conversation.id);
  const merged = { ...(previous || {}), ...conversation };
  if (moveToTop) {
    merged.group = "today";
    return [merged, ...list];
  }
  if (!merged.group) merged.group = groupFor(merged.last_message_at, now);
  if (previous) return (rows || []).map((row) => (row.id === conversation.id ? merged : row));
  const time = Date.parse(merged.last_message_at) || 0;
  const index = list.findIndex((row) => (Date.parse(row.last_message_at) || 0) < time);
  return index < 0 ? [...list, merged] : [...list.slice(0, index), merged, ...list.slice(index)];
}

// Auto-scroll only when the student is already reading the end of the thread: a reply
// arriving while they scrolled up to re-read something must not yank them down.
export function isNearBottom({ scrollTop, scrollHeight, clientHeight }, threshold = 120) {
  return scrollHeight - (scrollTop + clientHeight) <= threshold;
}

// Arrow-key movement in a menu of `count` items (WAI-ARIA menu pattern): Down/Up wrap
// around, Home/End jump. Returns the new index, or null for a key the menu ignores.
export function menuIndex(current, key, count) {
  if (!count) return null;
  const index = Number.isInteger(current) && current >= 0 ? current : -1;
  switch (key) {
    case "ArrowDown": return (index + 1) % count;
    case "ArrowUp": return index <= 0 ? count - 1 : index - 1;
    case "Home": return 0;
    case "End": return count - 1;
    default: return null;
  }
}

// A suggestion's prefill ends ready for typing: "Test my knowledge on:" gets a space.
export function prefillText(prompt) {
  const text = String(prompt || "").trim();
  return /[:：]$/.test(text) ? `${text} ` : text;
}

// The newest message of a thread, when it is an assistant reply: only that one can be
// regenerated (the server answers 409 not_latest for any other).
export function newestReply(messages) {
  const list = messages || [];
  const last = list[list.length - 1];
  return last && last.role === "assistant" ? last : null;
}

// What the Continue Learning control under the newest reply shows, from the server's
// `continue_learning` object (app.py continue_learning_state) and whether a request is in
// flight from this page. Kinds: "none" (nothing), "guidance" (not enough to build a lesson
// from yet: the server's sentence on what to add), "button" (start or retry), "pending"
// (building), "ready" (a link). The server decides availability; the page never guesses.
export function continueView(state, { busy = false } = {}) {
  if (!state || !state.enabled) return { kind: "none" };
  if (busy || state.status === "pending") return { kind: "pending" };
  if (state.status === "ready" && state.url) {
    return { kind: "ready", url: state.url, title: state.title || "" };
  }
  if (!state.available) {
    const guidance = String(state.guidance || "").trim();
    return state.reason === "too_little" && guidance ? { kind: "guidance", text: guidance } : { kind: "none" };
  }
  return {
    kind: "button",
    topic: String(state.topic || "").trim(),
    failed: state.status === "failed",
    failure: state.failure_reason || "",
    needsSubject: state.reason === "subject_needed",
    subjects: Array.isArray(state.subject_choices) ? state.subject_choices : [],
  };
}

// How a request that did not give the expected JSON is worded, by status - never a
// JavaScript exception text. Returns a catalogue key. (A JSON error body carries its own
// translated sentence and is shown as it is.)
export function failureKey({ status = 0, network = false, toLogin = false } = {}) {
  if (network) return "asstOffline";
  if (toLogin || status === 401) return "asstSessionExpired";
  if (status === 413) return "imgTooLarge";
  if (status === 502 || status === 503 || status === 504) return "asstServerSlow";
  return "asstRequestFailed";
}

// Whether a failed send left the student's message unsent, so its text and photos go
// back into the composer. A 409 conversation_busy, an image or validation error, a quota
// answer: nothing was stored. A lost connection, a gateway timeout or a 500 (the server
// can fail after it stored the message) is not known - the page asks the server (the
// message's client id) before it decides.
export function sendOutcome(error) {
  const status = Number(error?.status) || 0;
  if (error?.network || status === 0 || status === 500 || status === 502 || status === 504) return "unknown";
  if (status === 503 && !error?.code) return "unknown";
  return "unsent";
}

// Seconds to wait before asking again after a 409 conversation_busy (Retry-After). The
// body's own details.retry_after comes first: a rate limiter in front of the route can put
// its hour-long window into the header (3594 s, live 2026-11-02).
export function retryAfterSeconds(error, fallback = 5) {
  const own = Number(error?.details?.retry_after);
  const value = Number.isFinite(own) && own > 0 ? own : Number(error?.retryAfter);
  return Number.isFinite(value) && value > 0 ? Math.min(value, 60) : fallback;
}

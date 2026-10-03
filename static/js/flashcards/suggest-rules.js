/* Pure decision logic for definition suggestions - no DOM, no fetch, no imports.
 *
 * Split out so it can be unit-tested (tests/js/suggest-rules.test.mjs), following the
 * same convention as mode-rules.js. Every guard here exists to avoid spending one of a
 * student's limited AI requests on a call that cannot help them.
 */

export const MIN_TERM = 2;
export const MAX_TERM = 200;
export const MAX_SUGGESTIONS = 3;

/** Whether leaving this front field should trigger an AI request. */
export function shouldRequestSuggestions({ front, back, lastRequested, busy, enabled }) {
  if (!enabled || busy) return false;
  const term = String(front ?? "").trim();
  if (term.length < MIN_TERM || term.length > MAX_TERM) return false;
  // Never compete with an answer the student already wrote.
  if (String(back ?? "").trim()) return false;
  // Re-focusing a field without editing it must not re-ask.
  return term !== String(lastRequested ?? "").trim();
}

/** Cache identity: the same term in a different subject or language is a different ask. */
export function cacheKey({ front, subject, cardType, language }) {
  return [
    String(front ?? "").trim().toLocaleLowerCase().replace(/\s+/g, " "),
    String(subject ?? ""), String(cardType ?? ""), String(language ?? ""),
  ].join("|");
}

/** Clamp an API payload to at most `limit` usable suggestions. Mirrors the server. */
export function pickSuggestions(payload, limit = MAX_SUGGESTIONS) {
  const items = Array.isArray(payload?.suggestions) ? payload.suggestions : [];
  const seen = new Set();
  const picked = [];
  for (const item of items) {
    const back = String(item?.back ?? "").trim();
    const key = back.toLocaleLowerCase();
    if (!back || seen.has(key)) continue;
    seen.add(key);
    picked.push({ back, style: String(item?.style ?? "short") });
    if (picked.length >= limit) break;
  }
  return picked;
}

/** Keep the newest `limit` entries of a cache object. Prevents unbounded localStorage. */
export function trimCache(entries, limit) {
  const pairs = Object.entries(entries || {});
  if (pairs.length <= limit) return Object.fromEntries(pairs);
  pairs.sort((a, b) => (b[1]?.at ?? 0) - (a[1]?.at ?? 0));
  return Object.fromEntries(pairs.slice(0, limit));
}

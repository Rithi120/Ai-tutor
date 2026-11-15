/* "Show more" for long lists: the pure decisions (static/js/show-more.js does the DOM).
 *
 * No DOM and no browser API, so tests/js/show-more-rules.test.mjs runs them as they ship
 * (through tests/test_js_browser.py, since Node is not installed here).
 *
 * A list declares data-show-more="N" (items shown at first) and optionally
 * data-show-more-step="M" (items each tap reveals, N when absent).
 */

// Hiding a single item behind a button costs a tap to save one row: such a list stays open.
export const MIN_HIDDEN = 2;

/* A data attribute -> a positive whole number, or `fallback`. */
export function parseCount(value, fallback = 0) {
  const text = String(value ?? "").trim();
  if (!/^\d+$/.test(text)) return fallback;
  const number = Number(text);
  return number >= 1 ? number : fallback;
}

/* The starting state of a list of `total` items: {collapsible, initial, step, shown}.
 * Not collapsible (everything stays shown, no button) when nothing or too little would
 * be hidden. */
export function initialState(total, initialAttr, stepAttr) {
  const count = Math.max(0, Math.floor(Number(total) || 0));
  const initial = parseCount(initialAttr, 0);
  const step = parseCount(stepAttr, initial);
  const collapsible = initial >= 1 && count - initial >= MIN_HIDDEN;
  return { collapsible, initial, step: step || initial, total: count, shown: collapsible ? initial : count };
}

/* After one tap: the next `step` items, and the rest too when only one would stay hidden;
 * when everything is shown, back to the first `initial` items ("Show less"). */
export function nextShown(state) {
  const { total, initial, step, shown } = state;
  if (shown >= total) return Math.min(initial, total);
  const next = Math.min(total, shown + Math.max(1, step));
  return total - next < MIN_HIDDEN ? total : next;
}

/* What the button says and announces: {kind: "more"|"less", remaining, expanded}. */
export function buttonState(state) {
  const remaining = Math.max(0, state.total - state.shown);
  return remaining > 0
    ? { kind: "more", remaining, expanded: false }
    : { kind: "less", remaining: 0, expanded: true };
}

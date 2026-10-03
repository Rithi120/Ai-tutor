/* Definition suggestions: the student types one word, taps one of 2-3 answers.
 *
 * Owns the request, the cache and the panel so create.js only has to wire events.
 * Nothing here ever writes a card by itself - applying a suggestion is always a tap.
 */

import { api, escapeHtml, t, toast } from "./common.js";
import {
  cacheKey, pickSuggestions, shouldRequestSuggestions, trimCache,
  MAX_SUGGESTIONS, MIN_TERM, MAX_TERM,
} from "./suggest-rules.js";

const CACHE_KEY = "learnova:flashcard-suggestions";
const AUTO_KEY = "learnova:flashcard-suggest-auto";
const CACHE_LIMIT = 200;

let cache = {};
let busy = false;           // one request at a time: a student typing fast is not five calls
let autoEnabled = true;
let pausedByQuota = false;  // set when the AI budget is spent, cleared only on reload
let warnedAboutQuota = false;
const lastRequested = new Map();  // row index -> the term we last asked about
let context = () => ({});         // supplied by create.js: subject, grade, language, ...

/* ------------------------------- persistence ------------------------------- */

function readStore(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (_) {
    return fallback;  // private mode, blocked storage, corrupt value - all non-fatal
  }
}
function writeStore(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) { /* nothing to do */ }
}

export function autoSuggestEnabled() { return autoEnabled && !pausedByQuota; }

export function setAutoSuggest(enabled) {
  autoEnabled = Boolean(enabled);
  writeStore(AUTO_KEY, autoEnabled);
}

/* --------------------------------- panel --------------------------------- */

function panelFor(row) {
  let panel = row.querySelector(".card-suggest");
  if (!panel) {
    panel = document.createElement("div");
    panel.className = "card-suggest";
    panel.setAttribute("role", "group");
    panel.setAttribute("aria-label", t("fcSuggestions"));
    // Inside the term's own field, not the row: on a phone that puts the options directly
    // under the word just typed and above the definition, and on a desktop it stays in
    // the term column instead of breaking the two-column row. Above the field's caption,
    // so the caption stays attached to the line it names.
    const wrap = row.querySelector(".card-front-wrap") || row;
    const caption = wrap.querySelector(".card-field-label");
    if (caption) caption.before(panel); else wrap.append(panel);
  }
  return panel;
}

export function closePanel(row) {
  row?.querySelector(".card-suggest")?.remove();
}

function renderStatus(row, message) {
  const panel = panelFor(row);
  panel.innerHTML =
    `<p class="suggest-status" role="status" aria-live="polite">${escapeHtml(message)}</p>`;
}

function renderSuggestions(row, suggestions) {
  if (!suggestions.length) {
    renderStatus(row, t("fcSuggestNone"));
    return;
  }
  const panel = panelFor(row);
  // Model output is untrusted data: escaped, never inserted as markup.
  panel.innerHTML = `
    <div class="suggest-head">
      <span class="suggest-title">${escapeHtml(t("fcSuggestions"))}</span>
      <button type="button" class="suggest-close" data-suggest-close
              aria-label="${escapeHtml(t("fcSuggestDismiss"))}">&times;</button>
    </div>
    ${suggestions.map(item => `
      <button type="button" class="suggest-chip" data-suggest-apply
              data-back="${escapeHtml(item.back)}"
              title="${escapeHtml(t("fcSuggestUse"))}">${escapeHtml(item.back)}</button>`).join("")}`;
}

/* -------------------------------- requesting -------------------------------- */

async function fetchSuggestions(front) {
  const params = context();
  const key = cacheKey({ front, subject: params.subject, cardType: params.card_type, language: params.content_language });
  const hit = cache[key];
  if (hit?.suggestions) return hit.suggestions;

  const data = await api("/api/flashcards/suggest-back", {
    method: "POST", body: { ...params, front },
  });
  const suggestions = pickSuggestions(data, MAX_SUGGESTIONS);
  cache[key] = { suggestions, at: Date.now() };
  cache = trimCache(cache, CACHE_LIMIT);
  writeStore(CACHE_KEY, cache);
  return suggestions;
}

/** Ask for suggestions for one row. `manual` bypasses the auto-suggest guards. */
export async function requestFor(row, { manual = false } = {}) {
  const index = Number(row.dataset.index);
  const front = row.querySelector('[data-field="front"]')?.value ?? "";
  const back = row.querySelector('[data-field="back"]')?.value ?? "";

  // A tap on the trigger blurs the term field first, so both paths fire for one gesture.
  // Manual may override "already asked" and "auto is off", but never "one at a time".
  if (busy) return;
  if (!manual && !shouldRequestSuggestions({
    front, back, lastRequested: lastRequested.get(index),
    busy, enabled: autoSuggestEnabled(),
  })) return;

  const term = front.trim();
  if (manual && (term.length < MIN_TERM || term.length > MAX_TERM)) return;

  lastRequested.set(index, term);
  busy = true;
  renderStatus(row, t("fcSuggestThinking"));
  try {
    renderSuggestions(row, await fetchSuggestions(term));
  } catch (error) {
    // A spent site budget pauses the as-you-type suggestions exactly like a spent quota.
    if (error.status === 429 || error.code === "ai_limit_reached" || error.code === "ai_budget_exhausted") {
      // Card-making must never be the reason tutor chat stops working for an hour.
      pausedByQuota = true;
      closePanel(row);
      if (!warnedAboutQuota) { warnedAboutQuota = true; toast(t("fcSuggestPaused"), "info"); }
    } else if (manual) {
      renderStatus(row, error.message);
    } else {
      closePanel(row);  // an automatic attempt failing must not shout at the student
    }
  } finally {
    busy = false;
  }
}

/** The term changed, so any earlier answer for this row is stale. */
export function invalidate(index) { lastRequested.delete(index); }

/** Reordering renumbers every row, so index-keyed memory no longer means anything. */
export function reset() { lastRequested.clear(); }

export function init(contextProvider) {
  context = contextProvider;
  cache = trimCache(readStore(CACHE_KEY, {}) || {}, CACHE_LIMIT);
  autoEnabled = readStore(AUTO_KEY, true) !== false;
}

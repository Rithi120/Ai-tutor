// Pure rules for the guided tutorial (docs/TUTORIAL.md). No DOM and no imports, so every
// decision that matters - which step shows where, what Previous and Next mean, when a
// feature chapter may start by itself, when a step that is not there is skipped, where the
// card sits - is tested on its own (tests/js/tour-rules.test.mjs). static/js/tour.js does
// the finding, lighting and listening. The steps themselves come from the server
// (learnova/tutorial/registry.py).

// 5: the quick tour + feature chapters; a run is {v, section, step, auto, u}. The account
// tag (#tourRoot[data-tutorial-user]) makes a run another account left in this tab (a
// shared school device) no run at all.
export const RUN_VERSION = 5;
export const RUN_KEY = "learnova.tutorial.run";
export const PAYLOAD_KEY = "learnova.tutorial.payload";
export const OFFER_KEY = "learnova.tutorial.offer";
export const LEGACY_KEY = "learnova.tour.v2";
export const PHONE_MAX = 820;
export const HOLD_MS = 250;
export const DEFAULT_WAIT = 1200;
// A chapter starts by itself only after its anchor has been on screen this long (never on
// a half-drawn page), and never within this long after the student last typed.
export const DWELL_MS = 900;
export const TYPING_MS = 2000;
// After a run or the first-time offer ended, a follow-up chapter on the same page load
// waits at least this long - and only for an anchor that was not on screen when it ended
// (it appeared through the student's own action: a first answer, a lesson built in place).
export const FOLLOW_GAP_MS = 3000;
// Sections skipped or finished in this tab: never started by themselves again, even when
// the page was drawn before the server heard of it.
export const ENDED_KEY = "learnova.tutorial.ended";

// The server names the page (data-tour-page on #tourRoot); this is only the fallback when
// the attribute is empty. "/" is the chat home for a signed-in student, the lesson builder
// is /lesson; a running lesson keeps its "/?session_id=" address, which is why the
// server's name wins.
export function pageKey(pathname) {
  const path = String(pathname || "/").replace(/\/+$/, "") || "/";
  if (path === "/lesson") return "index";
  if (path === "/" || path === "/assistant" || path.startsWith("/assistant/c/")) return "chat";
  if (path === "/dashboard") return "dashboard";
  if (path === "/tutorial") return "tutorial";
  if (path === "/onboarding/grade") return "onboarding";
  return "other";
}

// The payload leaves out fields that have their default value; every step the engine
// handles has all of them.
export function normalizeStep(raw) {
  return {
    kind: "read", advance: "next", page: "*", targets: [], also: [], viewport: "any", wait: DEFAULT_WAIT,
    busy: "", create: null, shortcut: null, icon: "", title: "", text: "",
    ...raw,
  };
}

// The run of this tab. Anything else (garbage, an older version, a section the payload no
// longer has, another account's run) is no run at all.
export function parseRun(raw, payload = null, user = null) {
  if (raw === null || raw === undefined || raw === "") return null;
  let value = raw;
  if (typeof raw === "string") {
    try { value = JSON.parse(raw); } catch { return null; }
  }
  if (!value || typeof value !== "object" || value.v !== RUN_VERSION) return null;
  if (typeof value.section !== "string" || !value.section) return null;
  if (value.step !== undefined && typeof value.step !== "string") return null;
  if (payload && !(payload.sections || []).some(section => section.id === value.section)) return null;
  const owner = typeof value.u === "string" ? value.u : "";
  if (user !== null && owner !== String(user || "")) return null;
  return { v: RUN_VERSION, section: value.section, step: value.step || "", auto: Boolean(value.auto), u: owner };
}

export function serializeRun(run, user = "") {
  return JSON.stringify({ v: RUN_VERSION, section: String(run.section), step: String(run.step || ""),
    auto: Boolean(run.auto), u: String(user || run.u || "") });
}

// The first-time offer settled in this tab, by this account ("dismissed:<tag>"); a choice
// another account made in the same tab does not count.
export function offerValue(user) {
  return `dismissed:${String(user || "")}`;
}

export function offerDismissed(value, user) {
  return value === offerValue(user);
}

export function sectionOf(payload, sectionId) {
  return (payload?.sections || []).find(item => item.id === sectionId) || null;
}

export function sectionSteps(payload, sectionId) {
  const section = sectionOf(payload, sectionId);
  if (!section) return [];
  const byId = new Map((payload.steps || []).map(step => [step.id, step]));
  return section.steps.map(id => byId.get(id)).filter(Boolean).map(normalizeStep);
}

// A stored run continues only where it belongs: the quick tour on every page, a chapter
// on its own page (a student who went elsewhere left the chapter; it stays in progress).
export function runFitsPage(section, page) {
  return Boolean(section) && (section.page === "*" || section.page === page);
}

export function forViewport(step, isPhone) {
  const viewport = step?.viewport || "any";
  return viewport === "any" || (viewport === "phone") === Boolean(isPhone);
}

// A step belongs to its page ("*" = any page).
export function onPage(step, page) {
  return step.page === "*" || step.page === page;
}

// A step applies here: its layout, and not one already found missing (ctx.skip, a Set of
// step ids skipped silently in this run).
function applies(step, ctx) {
  return forViewport(step, ctx?.isPhone) && !(ctx?.skip && ctx.skip.has(step.id));
}

// The next step that applies here; steps.length when there is none (the run is done).
export function nextIndex(steps, index, ctx) {
  for (let position = index + 1; position < steps.length; position += 1) {
    if (applies(steps[position], ctx)) return position;
  }
  return steps.length;
}

// The previous step that applies here; -1 on the first one (Previous is disabled).
export function prevIndex(steps, index, ctx) {
  for (let position = Math.min(index, steps.length) - 1; position >= 0; position -= 1) {
    if (applies(steps[position], ctx)) return position;
  }
  return -1;
}

export function firstIndex(steps, ctx) {
  return nextIndex(steps, -1, ctx);
}

// "Step n of N" counts the steps that explain something on this layout and were not
// found missing.
export function counter(steps, index, isPhone, skip = null) {
  const step = steps[index];
  if (!step) return null;
  let current = 0;
  let total = 0;
  steps.forEach((item, position) => {
    if (!forViewport(item, isPhone) || (skip && skip.has(item.id) && position !== index)) return;
    total += 1;
    if (position <= index) current += 1;
  });
  return { current, total };
}

// What the panel shows for the current step:
//   paused    a page dialog is open (Rename, Delete): the tour steps aside until it closes
//   card      a step without a target
//   elsewhere the step belongs to another page (the run is left there)
//   spot      the target is visible: light it
//   opener    the target hides in a closed menu/drawer/panel whose opener is visible
//   busy      the page is still working on it (a lesson being written)
//   hold      the first moments after a step change: render nothing new (no flicker)
//   pending   waiting for the target (up to the step's wait)
//   missing   it did not appear: the step is skipped silently (never a dead end)
export function displayMode({ step, page, found, openerFound, busyFound, elapsed, otherDialogOpen }) {
  if (otherDialogOpen) return "paused";
  if (step.kind === "card") return "card";
  if (!onPage(step, page)) return "elsewhere";
  if (found) return "spot";
  if (openerFound) return "opener";
  if (busyFound) return "busy";
  if (elapsed < HOLD_MS) return "hold";
  if (elapsed < (Number.isFinite(step.wait) ? step.wait : DEFAULT_WAIT)) return "pending";
  return "missing";
}

// Where a silent skip goes: on, or back when the student pressed Previous (and on again
// when nothing earlier is there, so it can never loop). steps.length = the run is done.
export function skipTarget(steps, index, direction, ctx) {
  if (direction < 0) {
    const back = prevIndex(steps, index, ctx);
    if (back >= 0) return { index: back, direction: -1 };
  }
  return { index: nextIndex(steps, index, ctx), direction: 1 };
}

// Does the student's own action count as doing the step? Only action steps listen; a
// typed goal needs three characters.
export function advancesOn(step, { type, insideTarget, textLength = 0 }) {
  if (!step || step.kind !== "action" || !insideTarget) return false;
  switch (step.advance) {
    case "click": return type === "click";
    case "change": return type === "change";
    case "input": return (type === "input" || type === "change") && textLength >= 3;
    default: return false;
  }
}

// Where a stored run continues: the stored id; if a flag hid it since, the first visible
// step with a higher registry order; otherwise the start.
export function resumeIndex(steps, stepId, orderOf = {}) {
  if (!stepId) return 0;
  const found = steps.findIndex(step => step.id === stepId);
  if (found >= 0) return found;
  const rank = orderOf instanceof Map ? orderOf.get(stepId) : orderOf?.[stepId];
  if (Number.isFinite(rank)) {
    const later = steps.findIndex(step => Number(step.order) > rank);
    if (later >= 0) return later;
  }
  return 0;
}

// A same-site path, never another host ("//evil", "https://...").
export function safePath(url) {
  return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") ? url : null;
}

// Same path and query (a #fragment or a trailing slash does not make another place).
export function samePlace(url, currentHref) {
  if (!url) return false;
  try {
    const a = new URL(url, currentHref);
    const b = new URL(currentHref);
    const path = value => value.replace(/\/+$/, "") || "/";
    return a.origin === b.origin && path(a.pathname) === path(b.pathname) && a.search === b.search;
  } catch {
    return false;
  }
}

// The first-time offer: only for an account that never settled it, never on the grade
// prompt or the tutorial hub, never over a run or after a choice in this tab.
export function shouldOffer({ autostart, page, run, dismissed }) {
  return Boolean(autostart) && !run && !dismissed && page !== "onboarding" && page !== "tutorial";
}

// #tourRoot[data-tutorial-auto]: "id id:step ..." -> [{id, step}] (the server already left
// out what is skipped or done, and everything when the student switched tips off).
export function parseAuto(value) {
  return String(value || "").split(/\s+/).filter(Boolean).map(item => {
    const at = item.indexOf(":");
    return at < 0 ? { id: item, step: "" } : { id: item.slice(0, at), step: item.slice(at + 1) };
  }).filter(item => /^[a-z][a-z0-9-]*$/.test(item.id));
}

// The chapters of this page that may start by themselves, in the server's order.
export function autoCandidates(payload, auto, page) {
  return auto.map(item => ({ ...item, section: sectionOf(payload, item.id) }))
    .filter(item => item.section && item.section.kind === "chapter" && item.section.page === page
      && item.section.anchor);
}

// May a chapter start by itself right now? Never during the first-time offer, a run, a page
// dialog or a running generation, never while the student types, never within
// FOLLOW_GAP_MS after the last run or the offer ended (sinceEnded: Infinity when nothing
// ended on this page load), and only once its anchor has been on screen for DWELL_MS.
export function mayAutoStart({ run, offerOpen, dialogOpen, busy, sinceTyping, sinceEnded = Infinity, visibleFor }) {
  if (run || offerOpen || dialogOpen || busy) return false;
  if (Number.isFinite(sinceTyping) && sinceTyping < TYPING_MS) return false;
  if (Number.isFinite(sinceEnded) && sinceEnded < FOLLOW_GAP_MS) return false;
  return Number.isFinite(visibleFor) && visibleFor >= DWELL_MS;
}

// Which chapters may still follow on this page load: not one that ended in this tab, and
// not one whose anchor was already on screen when the last run or the offer ended.
export function followUps(candidates, ended, excluded) {
  return candidates.filter(item => !(ended && ended.has(item.id)) && !(excluded && excluded.has(item.id)));
}

// The sections ended in this tab, by this account ({u, ids}); another account's list is
// no list at all.
export function parseEnded(raw, user) {
  try {
    const value = JSON.parse(raw || "null");
    if (!value || value.u !== String(user || "") || !Array.isArray(value.ids)) return new Set();
    return new Set(value.ids.filter(id => typeof id === "string"));
  } catch {
    return new Set();
  }
}

export function serializeEnded(ids, user) {
  return JSON.stringify({ u: String(user || ""), ids: [...ids].slice(-60) });
}

// Using the lit element of a step that leaves the page (a link, a submit button) is doing
// the step: on the last step the section is completed, otherwise it is skipped - either
// way it never starts by itself again. The quick tour goes on on the next page unless it
// was its last step.
export function leaveStatus({ kind, isLast }) {
  if (isLast) return "completed";
  return kind === "overview" ? "" : "skipped";
}

// Where to scroll a lit box that fits the free band: its middle on the band's middle
// (the delta for window.scrollBy).
export function bandScroll(rect, band) {
  return Math.round(rect.top + rect.height / 2 - (band.top + (band.bottom - band.top) / 2));
}

// How a run ends: Finish = completed, Skip / Exit / Escape = skipped (the server never
// makes a finished section less done).
export function endStatus(how) {
  return how === "finish" ? "completed" : "skipped";
}

// A container opened for a step is closed again when the next step is not in it.
export function closeOnLeave(prevContainer, nextTargetInside) {
  return Boolean(prevContainer) && !nextTargetInside;
}

// "{current}" placeholders, the same shape as the server's str.format.
export function fillText(template, values = {}) {
  return String(template || "").replace(/\{(\w+)\}/g, (match, name) =>
    Object.prototype.hasOwnProperty.call(values, name) ? String(values[name]) : match);
}

// The lit rectangle kept on screen (a header button at y = 0 would lose its ring).
export function clampRect(rect, vw, vh, margin = 4) {
  const left = Math.max(margin, Math.min(rect.left, vw - margin));
  const top = Math.max(margin, Math.min(rect.top, vh - margin));
  const right = Math.max(left, Math.min(rect.left + rect.width, vw - margin));
  const bottom = Math.max(top, Math.min(rect.top + rect.height, vh - margin));
  return { left, top, width: right - left, height: bottom - top };
}

// Is a box (part of) on screen? The anchor of a chapter must be.
export function onScreen(rect, vw, vh) {
  return rect.width > 0 && rect.height > 0 && rect.left < vw && rect.top < vh
    && rect.left + rect.width > 0 && rect.top + rect.height > 0;
}

function covers(box, x, y) {
  return x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height;
}

// Where the explanation card goes, so it never covers the middle of the lit element.
//   < 640 px: a bottom sheet, or a top sheet when the target reaches into the sheet.
//   wider:    below the target, else above, else beside it, else the corner away from it.
export function placePanel({ rect, vw, vh, panelW, panelH, navH = 64, gap = 14, margin = 12 }) {
  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;
  if (vw < 640) {
    const sheetTop = vh - panelH;
    const topSheet = { left: 0, top: navH + 8, width: vw, height: panelH };
    const bottomSheet = { left: 0, top: sheetTop, width: vw, height: panelH };
    if (rect.top + rect.height <= sheetTop) return { kind: "bottom" };
    if (rect.top >= navH + 8 + panelH) return { kind: "top" };
    if (!covers(bottomSheet, cx, cy)) return { kind: "bottom" };
    if (!covers(topSheet, cx, cy)) return { kind: "top" };
    return { kind: cy > vh / 2 ? "top" : "bottom" };
  }
  const width = Math.min(panelW, vw - 2 * margin);
  const clampX = value => Math.max(margin, Math.min(value, vw - width - margin));
  const clampY = value => Math.max(margin, Math.min(value, vh - panelH - margin));
  const below = rect.top + rect.height + gap;
  if (below + panelH <= vh - margin) return { kind: "float", left: clampX(rect.left), top: below };
  const above = rect.top - gap - panelH;
  if (above >= margin) return { kind: "float", left: clampX(rect.left), top: above };
  if (vw - (rect.left + rect.width) >= width + gap + margin) {
    return { kind: "float", left: rect.left + rect.width + gap, top: clampY(rect.top) };
  }
  if (rect.left >= width + gap + margin) return { kind: "float", left: rect.left - gap - width, top: clampY(rect.top) };
  // No free side: the corner that covers the least of the lit element (on a tie, the one
  // away from its middle).
  const corners = [];
  for (const top of [margin, vh - panelH - margin]) {
    for (const left of [margin, vw - width - margin]) {
      const box = { left: clampX(left), top: clampY(top), width, height: panelH };
      const away = (box.left + width / 2 - cx) ** 2 + (box.top + panelH / 2 - cy) ** 2;
      corners.push({ box, overlap: overlapArea(box, rect), away });
    }
  }
  corners.sort((a, b) => a.overlap - b.overlap || b.away - a.away);
  return { kind: "float", left: corners[0].box.left, top: corners[0].box.top };
}

export function overlapArea(a, b) {
  const width = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left);
  const height = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top);
  return width > 0 && height > 0 ? width * height : 0;
}

// The band of the window the card leaves free for the light: above a phone's bottom
// sheet, or above/below the floating card when the target is too wide for it to sit
// beside. A target taller than that band (a whole page section) is lit from the top of
// the band down to its end, so the ring has four visible edges, something stays dimmed
// and the card never covers the lit part.
export function lightBand({ rect, vw, vh, panelW, panelH, navH = 64, gap = 14, margin = 12 }) {
  const top = navH + 8;
  if (vw < 640) return { top, bottom: vh - panelH - gap };
  const width = Math.min(panelW, vw - 2 * margin);
  const beside = vw - (rect.left + rect.width) >= width + gap + margin || rect.left >= width + gap + margin;
  return { top, bottom: beside ? vh - margin : vh - panelH - gap - margin };
}

export function isTall(rect, band) {
  return rect.height > band.bottom - band.top;
}

export function fitRect(rect, band) {
  if (!isTall(rect, band)) return rect;
  const top = Math.max(rect.top, band.top);
  const bottom = Math.min(rect.top + rect.height, band.bottom);
  if (bottom - top < 24) return rect;   // hardly on screen: leave it to the scroll
  return { left: rect.left, top, width: rect.width, height: bottom - top };
}

// "Title." for the screen reader, without a second full stop after "?", "!" or "…".
export function sentence(text) {
  const value = String(text || "").trim();
  return !value || /[.!?…:]$/.test(value) ? value : `${value}.`;
}

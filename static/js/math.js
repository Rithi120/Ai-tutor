/* The one formula renderer for the whole application.
 *
 * Before this, four copies of renderMath lived in app.js, assistant.js, community.js and
 * flashcards/common.js, and five templates each pulled KaTeX from the CDN themselves. A
 * page that nobody remembered to wire up simply showed students raw `$x^2$`.
 *
 * Two rules this module keeps:
 *   1. It never touches anything editable. A textarea holds the LaTeX source a student
 *      is writing, so the source must survive untouched - display is rendered, data is not.
 *   2. It never fails loudly. A malformed formula is shown as its own source, in a
 *      monospace <code> - never KaTeX's red partial render, never a command with its
 *      backslash stripped ("frac{1}{2}"); a renderer that throws must not take the lesson
 *      down with it.
 *
 * Two kinds of input:
 *   * formula slots - <span class="ln-math-slot" data-tex data-display> - that the markdown
 *     renderer (markdown.js) leaves where it found a formula in a chat reply. They are
 *     typeset first and exactly as tokenised, so a formula that spans lines, sits in a
 *     table or contains "*" or "|" renders whole;
 *   * delimiters in ordinary text nodes ($…$, \(…\), …) anywhere else in the page.
 *
 * KaTeX is fetched only once a page is found to contain a formula, so pages that never
 * show mathematics - settings, the library, most of the dashboard - pay nothing for it.
 * mhchem (\ce{…}, \pu{…}) is fetched on top only when a formula uses it.
 */

import { hasBarePower, hasMath, powerSegments, readableTex, splitMath } from "./math-rules.js";

const KATEX_VERSION = "0.16.11";
const KATEX_BASE = `https://cdn.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist`;
// A stalled CDN must not leave raw source on screen indefinitely: after this the
// readable fallback is shown (and KaTeX, if it arrives later, serves the next render).
const KATEX_TIMEOUT_MS = 8000;
const SLOT_SELECTOR = ".ln-math-slot";
const CHEMISTRY = /\\(?:ce|pu)\s*\{/;

// trust:false keeps \href{javascript:…} and \htmlClass inert; maxSize/maxExpand bound what
// a hostile formula can do to the page; strict:"ignore" keeps the console quiet about
// harmless non-standard input. throwOnError makes a parse error reach our own fallback.
const KATEX_OPTIONS = {
  throwOnError: true, strict: "ignore", trust: false, maxSize: 20, maxExpand: 500,
  output: "htmlAndMathml",
};

// Elements whose text is code, data, or something the student is editing.
const SKIP_TAGS = new Set([
  "SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "INPUT", "SELECT", "OPTION",
  "PRE", "CODE", "KBD", "SAMP",
]);

let katexReady = null;
let mhchemReady = null;

function loadStylesheet() {
  if (document.querySelector('link[data-katex]')) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = `${KATEX_BASE}/katex.min.css`;
  link.crossOrigin = "anonymous";
  link.dataset.katex = "1";
  document.head.appendChild(link);
}

/** Fetch KaTeX once, on the first page that actually needs it. */
function loadKatex() {
  if (katexReady) return katexReady;
  if (window.katex) { loadStylesheet(); katexReady = Promise.resolve(window.katex); return katexReady; }
  katexReady = new Promise((resolve) => {
    loadStylesheet();
    let settled = false;
    const settle = (value) => { if (!settled) { settled = true; resolve(value); } };
    const script = document.createElement("script");
    script.src = `${KATEX_BASE}/katex.min.js`;
    script.crossOrigin = "anonymous";
    script.defer = true;
    // Resolving with null on error or timeout is deliberate: the page shows the readable
    // fallback instead of hanging on a promise that never settles.
    script.onload = () => {
      if (settled && window.katex) katexReady = Promise.resolve(window.katex);
      settle(window.katex || null);
    };
    script.onerror = () => settle(null);
    window.setTimeout(() => settle(null), KATEX_TIMEOUT_MS);
    document.head.appendChild(script);
  });
  return katexReady;
}

/** KaTeX's chemistry extension, loaded once and only for a formula that uses it. */
function loadMhchem(katex) {
  if (!katex) return Promise.resolve(katex);
  if (!mhchemReady) {
    mhchemReady = new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = `${KATEX_BASE}/contrib/mhchem.min.js`;
      script.crossOrigin = "anonymous";
      const done = () => resolve();
      script.onload = done;
      script.onerror = done;      // without it \ce{…} shows its source, which is readable
      window.setTimeout(done, KATEX_TIMEOUT_MS);
      document.head.appendChild(script);
    });
  }
  return mhchemReady.then(() => katex);
}

function rendererFor(sources) {
  return loadKatex().then((katex) => (sources.some((tex) => CHEMISTRY.test(tex)) ? loadMhchem(katex) : katex));
}

function skip(node) {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    if (SKIP_TAGS.has(parent.tagName)) return true;
    if (parent.isContentEditable) return true;
    if (parent.classList.contains("katex") || parent.dataset.lnMath !== undefined) return true;
    if (parent.classList.contains("ln-math-slot")) return true;
    if (parent.dataset.noMath !== undefined) return true;
  }
  return false;
}

function textNodesWithMath(root) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const value = node.nodeValue;
      if (!value) return NodeFilter.FILTER_REJECT;
      const delimited = (value.includes("$") || value.includes("\\")) && hasMath(value);
      // A power the model wrote without $...$ ("4^2·4^3") is raised too, so the caret
      // never reaches the student.
      if (!delimited && !(value.includes("^") && hasBarePower(value))) return NodeFilter.FILTER_REJECT;
      if (skip(node)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const found = [];
  for (let node = walker.nextNode(); node; node = walker.nextNode()) found.push(node);
  return found;
}

/** The formula's source, readable and copyable, for input KaTeX cannot typeset. */
function sourceElement(tex, display) {
  const code = document.createElement("code");
  code.className = display ? "ln-math-source ln-math-source-display" : "ln-math-source";
  code.dataset.lnMath = tex;
  code.textContent = display ? `\\[${tex}\\]` : `\\(${tex}\\)`;
  return code;
}

function formulaElement(katex, tex, display) {
  const span = document.createElement("span");
  span.className = display ? "ln-math ln-math-display" : "ln-math";
  // The source is kept on the element so the original is always recoverable - for
  // copying, for re-editing, and for anyone debugging what the AI actually produced.
  span.dataset.lnMath = tex;
  try {
    katex.render(tex, span, { ...KATEX_OPTIONS, displayMode: display });
  } catch (_) {
    return sourceElement(tex, display);   // show the source, never nothing
  }
  return span;
}

/** Text with plain-text powers raised: "a^{m+n}" becomes a<sup>m+n</sup>. */
function appendWithPowers(parent, text) {
  for (const piece of powerSegments(text)) {
    if (piece.type === "sup") {
      const sup = document.createElement("sup");
      sup.textContent = piece.value;
      parent.appendChild(sup);
    } else {
      parent.appendChild(document.createTextNode(piece.value));
    }
  }
}

/** Readable stand-in for a formula when KaTeX is unavailable (offline, CDN blocked):
 *  (1)/(2), 2⁵ = 32 - or the source itself when it cannot be spelled out faithfully. */
function fallbackElement(tex, display) {
  const readable = readableTex(tex, { strict: true });
  if (readable === null) return sourceElement(tex, display);
  const span = document.createElement("span");
  span.className = display ? "ln-math-fallback ln-math-display" : "ln-math-fallback";
  span.dataset.lnMath = tex;
  appendWithPowers(span, readable);
  return span;
}

function typesetSlot(katex, slot) {
  // Already typeset by an earlier call, or removed from its message meanwhile.
  if (!slot.parentNode || !slot.classList.contains("ln-math-slot")) return;
  const tex = slot.dataset.tex ?? "";
  const display = slot.dataset.display === "1";
  slot.replaceWith(katex ? formulaElement(katex, tex, display) : fallbackElement(tex, display));
}

function replaceNode(katex, node) {
  const segments = splitMath(node.nodeValue);
  const fragment = document.createDocumentFragment();
  for (const segment of segments) {
    if (segment.type === "math") {
      fragment.appendChild(katex ? formulaElement(katex, segment.value, segment.display)
                                 : fallbackElement(segment.value, segment.display));
    } else {
      appendWithPowers(fragment, segment.value);
    }
  }
  node.parentNode?.replaceChild(fragment, node);
}

/**
 * Typeset every formula inside `root`, leaving everything else exactly as it was.
 *
 * Safe to call on any element, as often as needed: text with no formula in it is left
 * alone, and already-typeset formulas are not touched again. Formula slots are typeset
 * first; the text pass then handles delimiters in ordinary text. Returns a promise that
 * settles once everything inside `root` is typeset (callers may ignore it).
 */
export function renderMath(root) {
  const element = typeof root === "string" ? document.querySelector(root) : root;
  if (!element || !element.querySelectorAll) return Promise.resolve();
  const slots = [...element.querySelectorAll(SLOT_SELECTOR)];
  const nodes = textNodesWithMath(element);
  if (!slots.length && !nodes.length) return Promise.resolve();
  if (!slots.length && !nodes.some(node => hasMath(node.nodeValue))) {
    // Only plain-text powers: no renderer needed, raise them now.
    for (const node of nodes) replaceNode(null, node);
    return Promise.resolve();
  }
  const sources = [...slots.map((slot) => slot.dataset.tex ?? ""), ...nodes.map((node) => node.nodeValue)];
  return rendererFor(sources).then((katex) => {
    // Offline or blocked: a readable fallback (2⁵ = 32, (1)/(2)) instead of raw LaTeX.
    for (const slot of slots) typesetSlot(katex, slot);
    // Checked after the await: the page may have moved on while KaTeX was loading.
    for (const node of nodes) if (node.isConnected) replaceNode(katex, node);
  });
}

/** The LaTeX source behind a rendered formula, or "" if this is not one. */
export function mathSource(element) {
  return element?.dataset?.lnMath ?? "";
}

// The boxes a display formula scrolls in: KaTeX's own .katex-display, or the wrapper when
// there is none (the readable fallback, a stand-in renderer).
const FORMULA_SCROLLERS = ":is(.katex-display, .ln-math-display)";

/**
 * Display formulas inside `root` that are wider than their column: each becomes a named,
 * focusable region (so a keyboard can scroll it) marked data-scroller, which the edge cue
 * in learnova-content.css shows; one that fits again loses all of it. The chat does the same
 * in assistant.js (markScrollers); this is the lesson page's (PF_FIX2 F9: a clipped formula at
 * 390 px with nothing to say there was more).
 */
export function markFormulaScrollers(root, label) {
  const element = typeof root === "string" ? document.querySelector(root) : root;
  if (!element || !element.querySelectorAll) return 0;
  let count = 0;
  for (const node of element.querySelectorAll(FORMULA_SCROLLERS)) {
    const scrolls = node.scrollWidth > node.clientWidth + 1;
    if (scrolls) {
      count += 1;
      if (!node.dataset.scroller) {
        node.dataset.scroller = "1";
        node.tabIndex = 0;
        node.setAttribute("role", "region");
        node.setAttribute("aria-label", label || "Wide formula, scrolls sideways");
      }
    } else if (node.dataset.scroller) {
      delete node.dataset.scroller;
      node.removeAttribute("tabindex");
      node.removeAttribute("role");
      node.removeAttribute("aria-label");
    }
  }
  return count;
}

// Global by default: the whole main content area of every page is typeset once it is
// ready, which is what makes this a property of the application rather than a list of
// pages someone has to remember to update. Anything built later by JavaScript calls
// renderMath itself after setting its content.
//
// Opt a subtree out with data-no-math. Editable controls need no opt-out - textareas,
// inputs and contenteditable regions are skipped by definition, so the LaTeX a student
// is writing is never rewritten underneath them.
const AUTO_ROOTS = "main, [data-math]";

function renderMarkedRegions() {
  document.querySelectorAll(AUTO_ROOTS).forEach(renderMath);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", renderMarkedRegions);
} else {
  renderMarkedRegions();
}

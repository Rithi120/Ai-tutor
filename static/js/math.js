/* The one formula renderer for the whole application.
 *
 * Before this, four copies of renderMath lived in app.js, assistant.js, community.js and
 * flashcards/common.js, and five templates each pulled KaTeX from the CDN themselves. A
 * page that nobody remembered to wire up simply showed students raw `$x^2$`.
 *
 * Two rules this module keeps:
 *   1. It never touches anything editable. A textarea holds the LaTeX source a student
 *      is writing, so the source must survive untouched - display is rendered, data is not.
 *   2. It never fails loudly. A malformed formula renders as its own source text; a
 *      renderer that throws must not take the lesson down with it.
 *
 * KaTeX is fetched only once a page is found to contain a formula, so pages that never
 * show mathematics - settings, the library, most of the dashboard - pay nothing for it.
 */

import { hasBarePower, hasMath, powerSegments, readableTex, splitMath } from "./math-rules.js";

const KATEX_VERSION = "0.16.11";
const KATEX_BASE = `https://cdn.jsdelivr.net/npm/katex@${KATEX_VERSION}/dist`;

// Elements whose text is code, data, or something the student is editing.
const SKIP_TAGS = new Set([
  "SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "INPUT", "SELECT", "OPTION",
  "PRE", "CODE", "KBD", "SAMP",
]);

let katexReady = null;

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
    const script = document.createElement("script");
    script.src = `${KATEX_BASE}/katex.min.js`;
    script.crossOrigin = "anonymous";
    script.defer = true;
    // Resolving with null on error is deliberate: the page keeps its original text
    // instead of hanging on a promise that never settles.
    script.onload = () => resolve(window.katex || null);
    script.onerror = () => resolve(null);
    document.head.appendChild(script);
  });
  return katexReady;
}

function skip(node) {
  for (let parent = node.parentElement; parent; parent = parent.parentElement) {
    if (SKIP_TAGS.has(parent.tagName)) return true;
    if (parent.isContentEditable) return true;
    if (parent.classList.contains("katex") || parent.dataset.lnMath !== undefined) return true;
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

function formulaElement(katex, tex, display) {
  const span = document.createElement("span");
  span.className = display ? "ln-math ln-math-display" : "ln-math";
  // The source is kept on the element so the original is always recoverable - for
  // copying, for re-editing, and for anyone debugging what the AI actually produced.
  span.dataset.lnMath = tex;
  try {
    katex.render(tex, span, { displayMode: display, throwOnError: false, output: "htmlAndMathml" });
  } catch (_) {
    span.textContent = display ? `$$${tex}$$` : `$${tex}$`;   // show the source, never nothing
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

/** Readable stand-in for a formula when KaTeX is unavailable (offline, CDN blocked). */
function fallbackElement(tex, display) {
  const span = document.createElement("span");
  span.className = display ? "ln-math-fallback ln-math-display" : "ln-math-fallback";
  span.dataset.lnMath = tex;
  appendWithPowers(span, readableTex(tex));
  return span;
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
 * alone, and already-typeset formulas are not touched again.
 */
export function renderMath(root) {
  const element = typeof root === "string" ? document.querySelector(root) : root;
  if (!element || !element.querySelectorAll) return;
  const nodes = textNodesWithMath(element);
  if (!nodes.length) return;
  if (!nodes.some(node => hasMath(node.nodeValue))) {
    // Only plain-text powers: no renderer needed, raise them now.
    for (const node of nodes) replaceNode(null, node);
    return;
  }
  loadKatex().then((katex) => {
    // Offline or blocked: a readable fallback (2⁵ = 32, (1)/(2)) instead of raw LaTeX.
    // Checked after the await: the page may have moved on while KaTeX was loading.
    for (const node of nodes) if (node.isConnected) replaceNode(katex, node);
  });
}

/** The LaTeX source behind a rendered formula, or "" if this is not one. */
export function mathSource(element) {
  return element?.dataset?.lnMath ?? "";
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

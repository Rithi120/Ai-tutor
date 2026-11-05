/* Syntax colours for fenced code blocks in chat replies, loaded only when one appears.
 *
 * markdown.js renders ```lang blocks as <pre class="asst-code"><span class="code-lang">…
 * <code>…</code></pre> with the code as escaped text. highlight.js reads that text
 * (textContent) and writes its own escaped token spans, so model text still cannot
 * become markup. Only a block whose language highlight.js knows is coloured; the rest,
 * and every block when the script cannot load, stays plain monospace text.
 */
const HLJS_URL = "https://cdn.jsdelivr.net/npm/@highlightjs/cdn-assets@11.9.0/highlight.min.js";
const LOAD_TIMEOUT_MS = 8000;

let hljsReady = null;

function loadHighlighter() {
  if (window.hljs) return Promise.resolve(window.hljs);
  if (hljsReady) return hljsReady;
  hljsReady = new Promise((resolve) => {
    let settled = false;
    const settle = (value) => { if (!settled) { settled = true; resolve(value); } };
    const script = document.createElement("script");
    script.src = HLJS_URL;
    script.crossOrigin = "anonymous";
    script.onload = () => settle(window.hljs || null);
    script.onerror = () => settle(null);
    window.setTimeout(() => settle(null), LOAD_TIMEOUT_MS);
    document.head.appendChild(script);
  });
  return hljsReady;
}

/** Colour the labelled code blocks under `root`. Safe to call repeatedly; never throws. */
export function highlightCode(root) {
  if (!root?.querySelectorAll) return;
  const blocks = [...root.querySelectorAll("pre.asst-code")]
    .filter((pre) => pre.querySelector(".code-lang") && pre.querySelector("code") && !pre.dataset.highlighted);
  if (!blocks.length) return;
  loadHighlighter().then((hljs) => {
    if (!hljs || typeof hljs.highlightElement !== "function") return;
    for (const pre of blocks) {
      const code = pre.querySelector("code");
      const language = pre.querySelector(".code-lang").textContent.trim().toLowerCase();
      if (!code || pre.dataset.highlighted || !hljs.getLanguage?.(language)) continue;
      pre.dataset.highlighted = "1";
      code.classList.add(`language-${language}`);
      try {
        hljs.highlightElement(code);
      } catch (_) {
        /* the plain text stays */
      }
    }
  });
}

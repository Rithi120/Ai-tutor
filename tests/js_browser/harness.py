"""Browser harness for Learnova's static JavaScript: real modules, real stylesheets, real CSP.

Node is not installed here, so the shipped ES modules run in the system Chrome/Edge through
Playwright (the browser scripts/mobile_audit.launch finds; no download). Every request is
answered by `context.route`:

* ``/static/**`` and ``/tests/js/**`` from disk;
* ``/__page`` from the HTML a test gives, sent with the app's own Content-Security-Policy
  (learnova/web/security.content_security_policy: nonce + strict-dynamic, fixed nonce);
* ``/__shim/*`` - tiny stand-ins for ``node:test`` and ``node:assert/strict`` so the
  ``tests/js/*.test.mjs`` files run unchanged (see run_node_tests);
* KaTeX from jsdelivr: a stub (default), nothing (``katex="none"``, the offline case) or
  the real 0.16.11 build (``katex="real"``: from ``LEARNOVA_KATEX_DIR`` when set, the network
  only with ``LEARNOVA_JS_TESTS_ONLINE=1``, else the test is skipped);
* highlight.js: aborted, or a stub with ``hljs="stub"``;
* everything else is aborted.

The KaTeX stub throws a ParseError on unbalanced braces, \\left without \\right, \\begin
without \\end, a \\frac with an empty or missing argument, and \\ce/\\pu before mhchem has
defined them; otherwise it writes ``<span class="katex" data-stub-tex="…">`` and records the
options it was called with in ``window.__katexCalls``.

Not collected by pytest (no ``test_`` prefix); tests/test_js_browser.py uses it.
"""

from __future__ import annotations

import importlib.util
import json
import mimetypes
import os
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "static"
JS_TESTS = ROOT / "tests" / "js"
PAGES = Path(__file__).resolve().parent / "pages"
ORIGIN = "http://learnova.test"
NONCE = "learnova-js-harness"
KATEX_VERSION = "0.16.11"
KATEX_URL = f"https://cdn.jsdelivr.net/npm/katex@{KATEX_VERSION}/dist/"
HLJS_PREFIX = "https://cdn.jsdelivr.net/npm/@highlightjs/"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from learnova.web.security import content_security_policy  # noqa: E402

CSP = content_security_policy(NONCE)

KATEX_STUB = r"""
(() => {
  const calls = [];
  const macros = new Set();
  class ParseError extends Error {
    constructor(message) { super(message); this.name = "ParseError"; }
  }
  function balanced(tex) {
    let depth = 0;
    for (let i = 0; i < tex.length; i += 1) {
      if (tex[i] === "\\") { i += 1; continue; }
      if (tex[i] === "{") depth += 1;
      else if (tex[i] === "}" && --depth < 0) return false;
    }
    return depth === 0;
  }
  function check(tex) {
    if (!balanced(tex)) throw new ParseError(`KaTeX parse error: unbalanced braces in ${tex}`);
    const count = (pattern) => (tex.match(pattern) || []).length;
    if (count(/\\left(?![A-Za-z])/g) !== count(/\\right(?![A-Za-z])/g)) throw new ParseError("\\left without \\right");
    if (count(/\\begin\{/g) !== count(/\\end\{/g)) throw new ParseError("\\begin without \\end");
    const frac = /\\[dt]?frac(?![A-Za-z])/g;
    for (let match = frac.exec(tex); match; match = frac.exec(tex)) {
      let i = match.index + match[0].length;
      for (let group = 0; group < 2; group += 1) {
        while (tex[i] === " ") i += 1;
        if (tex[i] !== "{") throw new ParseError("\\frac needs two arguments");
        let depth = 0;
        const start = i;
        for (; i < tex.length; i += 1) {
          if (tex[i] === "\\") { i += 1; continue; }
          if (tex[i] === "{") depth += 1;
          else if (tex[i] === "}" && --depth === 0) break;
        }
        if (!tex.slice(start + 1, i).trim()) throw new ParseError("empty \\frac argument");
        i += 1;
      }
    }
    if (/\\(?:ce|pu)\s*\{/.test(tex) && !macros.has("\\ce")) throw new ParseError("Undefined control sequence: \\ce");
  }
  window.katex = {
    ParseError,
    render(tex, element, options = {}) {
      calls.push({ tex, options: { ...options } });
      check(tex);
      const outer = document.createElement("span");
      outer.className = "katex";
      outer.dataset.stubTex = tex;
      // Like KaTeX's own .base boxes: one unbreakable line.
      const base = document.createElement("span");
      base.className = "base";
      base.style.display = "inline-block";
      base.style.whiteSpace = "nowrap";
      base.textContent = `[${tex}]`;
      outer.appendChild(base);
      element.replaceChildren(outer);
    },
    __defineMacro(name) { macros.add(name); },
  };
  window.__katexCalls = calls;
})();
"""

MHCHEM_STUB = """
window.katex.__defineMacro("\\\\ce", () => "");
window.katex.__defineMacro("\\\\pu", () => "");
window.__mhchemLoaded = true;
"""

HLJS_STUB = """
window.hljs = {
  getLanguage(name) { return ["python", "javascript", "js"].includes(name) ? { name } : undefined; },
  highlightElement(element) {
    const text = element.textContent;
    const span = document.createElement("span");
    span.className = "hljs-keyword";
    span.textContent = text;
    element.replaceChildren(span);
    element.classList.add("hljs");
  },
};
"""

NODE_TEST_SHIM = """
const registered = [];
export function test(name, options, fn) {
  registered.push({ name, fn: typeof options === "function" ? options : fn });
}
export const it = test;
export function describe(_name, fn) { fn(); }
export default test;
export async function run() {
  const results = [];
  for (const { name, fn } of registered) {
    try {
      await fn();
      results.push({ name, ok: true });
    } catch (error) {
      results.push({ name, ok: false, error: String((error && (error.stack || error.message)) || error) });
    }
  }
  return results;
}
"""

NODE_ASSERT_SHIM = """
export class AssertionError extends Error {
  constructor(message) { super(message); this.name = "AssertionError"; }
}
const show = (value) => { try { return JSON.stringify(value); } catch (_) { return String(value); } };
const fail = (message, fallback) => { throw new AssertionError(message || fallback); };
function deep(a, b) {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => deep(a[key], b[key]));
}
export function ok(value, message) { if (!value) fail(message, `expected a truthy value, got ${show(value)}`); }
export function equal(actual, expected, message) {
  if (!Object.is(actual, expected)) fail(message, `${show(actual)} !== ${show(expected)}`);
}
export function notEqual(actual, expected, message) {
  if (Object.is(actual, expected)) fail(message, `${show(actual)} === ${show(expected)}`);
}
export function deepEqual(actual, expected, message) {
  if (!deep(actual, expected)) fail(message, `${show(actual)} is not deep-equal to ${show(expected)}`);
}
export function match(value, pattern, message) {
  if (!pattern.test(value)) fail(message, `${show(value)} does not match ${pattern}`);
}
export function doesNotMatch(value, pattern, message) {
  if (pattern.test(value)) fail(message, `${show(value)} matches ${pattern}`);
}
export function throws(fn, _expected, message) {
  try { fn(); } catch (_) { return; }
  fail(typeof _expected === "string" ? _expected : message, "expected the function to throw");
}
export const strictEqual = equal;
export const notStrictEqual = notEqual;
export const deepStrictEqual = deepEqual;
const assert = Object.assign((value, message) => ok(value, message), {
  AssertionError, ok, equal, notEqual, deepEqual, match, doesNotMatch, throws, strictEqual,
  notStrictEqual, deepStrictEqual,
});
export default assert;
"""

NODE_TEST_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>node:test in the browser</title>
<script nonce="__NONCE__" type="importmap">{"imports": {"node:test": "/__shim/node-test.js",
  "node:assert/strict": "/__shim/node-assert.js", "node:assert": "/__shim/node-assert.js"}}</script>
</head><body>
<script nonce="__NONCE__" type="module">
  import { run } from "/__shim/node-test.js";
  try {
    await import("/tests/js/__FILE__");
    window.__results = await run();
  } catch (error) {
    window.__results = [{ name: "(import)", ok: false, error: String((error && error.stack) || error) }];
  }
</script>
</body></html>
"""


def _audit_module():
    spec = importlib.util.spec_from_file_location("mobile_audit", ROOT / "scripts" / "mobile_audit.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("mobile_audit", module)
    spec.loader.exec_module(module)
    return module


def launch(playwright):
    """The installed Chrome or Edge (scripts/mobile_audit.launch). Raises SystemExit if none."""

    return _audit_module().launch(playwright)


def katex_source() -> Path | str | None:
    """Where the real KaTeX comes from: a local copy, the network (opt-in) or nowhere."""

    folder = os.environ.get("LEARNOVA_KATEX_DIR", "").strip()
    if folder and (Path(folder) / "katex.min.js").is_file():
        return Path(folder)
    if os.environ.get("LEARNOVA_JS_TESTS_ONLINE") == "1":
        return "network"
    return None


def render_page(theme: str = "light") -> str:
    html = (PAGES / "render.html").read_text(encoding="utf-8")
    return html.replace("__NONCE__", NONCE).replace("__THEME__", theme)


def _file_response(route, base: Path, relative: str) -> None:
    path = (base / relative.split("?", 1)[0]).resolve()
    if base.resolve() not in path.parents or not path.is_file():
        route.fulfill(status=404, body="")
        return
    content_type = "text/javascript" if path.suffix in {".js", ".mjs"} else (
        mimetypes.guess_type(str(path))[0] or "application/octet-stream")
    route.fulfill(status=200, body=path.read_bytes(), headers={"Content-Type": content_type})


def open_page(browser, html: str | None = None, *, katex: str = "stub", hljs: str = "none",
              viewport: tuple[int, int] = (390, 844), theme: str = "light",
              requests: list[str] | None = None, ready: str | None = "() => window.__ready === true"):
    """A Playwright page on http://learnova.test showing `html` (default: pages/render.html).

    The caller closes it with ``page.context.close()``. `requests` collects every URL the
    page asked for, including aborted ones (to see that mhchem was requested). `ready` is
    a JS *function* source: a bare expression would be polled through eval(), which the
    page's CSP (no 'unsafe-eval') refuses."""

    if katex == "real" and katex_source() is None:
        raise unittest.SkipTest("real KaTeX not available: set LEARNOVA_KATEX_DIR or LEARNOVA_JS_TESTS_ONLINE=1")
    source = katex_source()
    page_html = html if html is not None else render_page(theme)
    width, height = viewport
    mobile = width <= 430
    context = browser.new_context(viewport={"width": width, "height": height}, is_mobile=mobile, has_touch=mobile)
    if katex == "stub":
        context.add_init_script(KATEX_STUB)

    def handle(route):
        url = route.request.url
        if requests is not None:
            requests.append(url)
        if url.startswith(f"{ORIGIN}/__page"):
            route.fulfill(status=200, body=page_html, headers={
                "Content-Type": "text/html; charset=utf-8", "Content-Security-Policy": CSP})
        elif url.startswith(f"{ORIGIN}/__shim/"):
            body = NODE_TEST_SHIM if url.endswith("/node-test.js") else NODE_ASSERT_SHIM
            route.fulfill(status=200, body=body, headers={"Content-Type": "text/javascript"})
        elif url.startswith(f"{ORIGIN}/static/"):
            _file_response(route, STATIC, url[len(f"{ORIGIN}/static/"):])
        elif url.startswith(f"{ORIGIN}/tests/js/"):
            _file_response(route, JS_TESTS, url[len(f"{ORIGIN}/tests/js/"):])
        elif url.startswith(KATEX_URL):
            relative = url[len(KATEX_URL):]
            if katex == "stub" and relative.startswith("contrib/mhchem"):
                route.fulfill(status=200, body=MHCHEM_STUB, headers={"Content-Type": "text/javascript"})
            elif katex == "real" and isinstance(source, Path):
                _file_response(route, source, relative)
            elif katex == "real" and source == "network":
                route.continue_()
            else:
                route.abort()
        elif url.startswith(HLJS_PREFIX) and hljs == "stub":
            route.fulfill(status=200, body=HLJS_STUB, headers={"Content-Type": "text/javascript"})
        else:
            route.abort()

    context.route("**/*", handle)
    page = context.new_page()
    page.goto(f"{ORIGIN}/__page")
    if ready:
        page.wait_for_function(ready, timeout=10000)
    return page


RENDER_JS = r"""
async ({ text, highlight }) => {
  const api = window.__api;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // static/js/assistant.js messageNode (live reply and openConversation history alike).
  const article = document.createElement("article");
  article.className = "asst-msg asst-msg-assistant";
  const body = document.createElement("div");
  body.className = "asst-body";
  body.innerHTML = api.renderMarkdown(text);
  article.appendChild(body);
  api.renderMath(body);
  if (highlight) api.highlightCode(body);
  document.querySelector("#asstThread").appendChild(article);

  // static/app.js sendChat + addChatMessage (live reply and resumed-lesson history alike).
  const parsed = api.parseChips(text);
  const message = document.createElement("div");
  message.className = "message tutor-message";
  message.innerHTML = api.renderMarkdown(parsed.text);
  message.dataset.speechListenAuto = "";
  api.renderMath(message);
  api.highlightCode(message);
  document.querySelector("#chatMessages").appendChild(message);

  // templates/lesson_history.html + static/js/rich-history.js (stored reply, no CHIPS line).
  const wrapper = document.createElement("div");
  wrapper.className = "history-message tutor";
  const stored = document.createElement("div");
  stored.className = "history-message-body";
  stored.setAttribute("data-rich-text", "");
  stored.setAttribute("data-no-math", "");
  stored.textContent = parsed.text;
  wrapper.appendChild(stored);
  document.querySelector("#historyList").appendChild(wrapper);
  api.renderRichText(wrapper);

  const started = performance.now();
  while (document.querySelector(".ln-math-slot") && performance.now() - started < 10000) await sleep(25);
  if (highlight) {
    while (document.querySelector("pre.asst-code .code-lang") && !document.querySelector("pre[data-highlighted]")
           && performance.now() - started < 10000) await sleep(25);
  }
  await sleep(30);

  const depth = (node) => { let d = 0; for (let p = node.parentElement; p; p = p.parentElement) if (/^(OL|UL)$/.test(p.tagName)) d += 1; return d; };
  const describe = (element) => {
    const formulas = [...element.querySelectorAll("[data-ln-math]")].map((node) => ({
      tex: node.dataset.lnMath,
      display: node.classList.contains("ln-math-display") || node.classList.contains("ln-math-source-display"),
      kind: node.classList.contains("ln-math-source") ? "source"
        : node.classList.contains("ln-math-fallback") ? "fallback"
        : node.querySelector(".katex-error") ? "katex-error"
        : node.querySelector(".katex") ? "katex" : "other",
      text: node.textContent,
      tag: node.tagName.toLowerCase(),
    }));
    const clone = element.cloneNode(true);
    clone.querySelectorAll("[data-ln-math], .ln-math-slot").forEach((node) => node.replaceWith("⟦F⟧"));
    clone.querySelectorAll("br").forEach((node) => node.replaceWith("\n"));
    const all = [...element.querySelectorAll("*")];
    return {
      html: element.innerHTML,
      text: clone.textContent,
      formulas,
      slots: element.querySelectorAll(".ln-math-slot").length,
      lists: [...element.querySelectorAll("ol, ul")].map((list) => ({
        tag: list.tagName.toLowerCase(), start: list.getAttribute("start"), depth: depth(list),
        items: [...list.children].filter((child) => child.tagName === "LI").length })),
      anchors: [...element.querySelectorAll("a")].map((anchor) => ({
        href: anchor.getAttribute("href"), attrs: [...anchor.attributes].map((attr) => attr.name),
        text: anchor.textContent })),
      tags: [...new Set(all.map((node) => node.tagName.toLowerCase()))],
      attributes: [...new Set(all.flatMap((node) => [...node.attributes].map((attr) => attr.name)))],
      overflow: element.scrollWidth > element.clientWidth + 1 ? [element.scrollWidth, element.clientWidth] : null,
    };
  };
  return {
    assistant: describe(body), tutor: describe(message), history: describe(stored),
    chips: parsed.chips,
    pageOverflow: document.documentElement.scrollWidth > window.innerWidth
      ? [document.documentElement.scrollWidth, window.innerWidth] : null,
    katexCalls: (window.__katexCalls || []).map((call) => ({ tex: call.tex, options: call.options })),
    pwned: window.__pwned === 1,
  };
}
"""


def render_markdown(page, text: str, *, highlight: bool = False) -> dict[str, Any]:
    """Render `text` through the assistant, tutor-chat and lesson-history sequences and
    describe each result (formulas, lists, anchors, visible text, overflow)."""

    return page.evaluate(RENDER_JS, {"text": text, "highlight": highlight})


def eval_module(page, module: str, expression: str) -> Any:
    """Import `module` (e.g. "/static/js/math-protect.js") and evaluate `expression`, a JS
    function of the module namespace: ``eval_module(page, m, "(mod) => mod.protectMath('$x$')")``."""

    return page.evaluate(f"async () => {{ const mod = await import({json.dumps(module)}); "
                         f"return ({expression})(mod); }}")


def run_node_tests(browser, filename: str) -> list[dict[str, Any]]:
    """Run tests/js/<filename> (written for `node --test`) in the browser and return
    [{name, ok, error?}] for every test it registers."""

    html = NODE_TEST_PAGE.replace("__NONCE__", NONCE).replace("__FILE__", filename)
    page = open_page(browser, html, katex="none", ready=None)
    try:
        page.wait_for_function("() => Array.isArray(window.__results)", timeout=15000)
        return page.evaluate("window.__results")
    finally:
        page.context.close()

// Unit test for the assistant's markdown renderer. Run with: node --test tests/js/*.test.mjs
// (Node is not installed in the current CI image; this test locks the security property
// the renderer exists to provide, so it runs the moment Node is available.)
//
// The property: assistant output is untrusted text. It is escaped first, and only then
// given the small set of tags the renderer builds itself. Nothing the model writes may
// become markup. The renderer is deliberately tiny for that reason - every construct it
// supports is one it introduces, not one it passes through.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));

// assistant.js is browser-oriented (document, fetch, KaTeX). Load only the two pure
// rendering functions by evaluating them beside a minimal escapeHtml, exactly as the
// real module receives it from dom.js.
const source = readFileSync(path.join(dir, "../../static/js/assistant.js"), "utf8");
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} not found in assistant.js`);
  // Walk braces from the first "{" after the signature to find the function body end.
  let depth = 0;
  let index = source.indexOf("{", start);
  for (let cursor = index; cursor < source.length; cursor += 1) {
    if (source[cursor] === "{") depth += 1;
    else if (source[cursor] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, cursor + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

const escapeSource = readFileSync(path.join(dir, "../../static/js/dom.js"), "utf8")
  .match(/export function escapeHtml[\s\S]*?\n}/)[0]
  .replace("export ", "");

const moduleUrl = "data:text/javascript," + encodeURIComponent(
  `${escapeSource}\n${extract("renderInline")}\n${extract("renderMarkdown")}\n` +
  "export { renderInline, renderMarkdown };");
const { renderMarkdown } = await import(moduleUrl);

test("script tags cannot survive rendering", () => {
  const out = renderMarkdown('<script>alert("x")</script>');
  assert.ok(!out.includes("<script"), out);
  assert.ok(out.includes("&lt;script"), out);
});

test("event-handler attributes cannot be produced", () => {
  const out = renderMarkdown('<img src=x onerror="alert(1)">');
  assert.ok(!out.includes("<img"), out);
  assert.ok(!out.includes("onerror="), out);
});

test("a javascript: URL is never turned into a link", () => {
  const out = renderMarkdown("javascript:alert(1) and JaVaScRiPt:alert(2)");
  assert.ok(!out.includes("<a "), out);
});

test("only http and https become links, with safe rel and target", () => {
  const out = renderMarkdown("See https://example.com/docs for more.");
  assert.match(out, /<a href="https:\/\/example\.com\/docs"/);
  assert.match(out, /rel="noopener noreferrer nofollow"/);
  assert.match(out, /target="_blank"/);
});

test("a link cannot smuggle a quote to escape its own attribute", () => {
  const out = renderMarkdown('https://example.com/"onmouseover="alert(1)');
  assert.ok(!out.includes('"onmouseover="'), out);
  assert.ok(out.includes("&quot;") || !out.includes("<a "), out);
});

test("code blocks keep their content as text", () => {
  const out = renderMarkdown("```html\n<b>bold</b>\n```");
  assert.ok(out.includes("<pre"), out);
  assert.ok(out.includes("&lt;b&gt;bold&lt;/b&gt;"), out);
  assert.ok(!out.includes("<b>bold</b>"), out);
});

test("an unterminated code fence still renders its content escaped", () => {
  const out = renderMarkdown("```\n<i>unclosed");
  assert.ok(out.includes("&lt;i&gt;unclosed"), out);
  assert.ok(!out.includes("<i>unclosed"), out);
});

test("headings, lists and emphasis render as the tags the renderer builds", () => {
  const out = renderMarkdown(
    "# Title\n\n- first\n- second\n\n1. one\n2. two\n\n**bold** and *italic* and `code`");
  assert.ok(out.includes("<h3>Title</h3>"), out);
  assert.ok(out.includes("<ul>") && out.includes("<li>first</li>"), out);
  assert.ok(out.includes("<ol>") && out.includes("<li>one</li>"), out);
  assert.ok(out.includes("<strong>bold</strong>"), out);
  assert.ok(out.includes("<em>italic</em>"), out);
  assert.ok(out.includes("<code>code</code>"), out);
});

test("markdown syntax inside escaped html is not re-interpreted as markup", () => {
  const out = renderMarkdown("<div>**not bold html**</div>");
  assert.ok(!out.includes("<div>"), out);
  // The asterisks may still become <strong>; what matters is the div never appears.
  assert.ok(out.includes("&lt;div&gt;"), out);
});

test("empty and whitespace input produce nothing dangerous", () => {
  for (const value of ["", "   ", "\n\n\n", null, undefined]) {
    const out = renderMarkdown(value);
    assert.ok(!out.includes("<script"), String(value));
  }
});

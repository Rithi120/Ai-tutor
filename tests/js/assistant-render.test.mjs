// Unit test for the shared markdown renderer (static/js/markdown.js), used by both the
// assistant and the lesson tutor chat. Run with: node --test tests/js/*.test.mjs
// (Node is not installed in the current CI image; this test locks the security property
// the renderer exists to provide, so it runs the moment Node is available.)
//
// The property: model output is untrusted text. It is escaped first, and only then
// given the small set of tags the renderer builds itself. Nothing the model writes may
// become markup. The renderer is deliberately tiny for that reason - every construct it
// supports is one it introduces, not one it passes through.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));

// markdown.js imports escapeHtml from the browser-oriented dom.js. Load only the pure
// functions by evaluating them beside a minimal escapeHtml, exactly as the real module
// receives it from dom.js.
const source = readFileSync(path.join(dir, "../../static/js/markdown.js"), "utf8");
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert.notEqual(start, -1, `${name} not found in markdown.js`);
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
  `${escapeSource}\n${extract("renderInline")}\n${extract("isTableSeparator")}\n` +
  `${extract("splitTableRow")}\n${extract("renderMarkdown")}\n${extract("parseChips")}\n` +
  "export { renderInline, renderMarkdown, parseChips };");
const { renderMarkdown, parseChips } = await import(moduleUrl);

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

test("a pipe table renders as a real table with escaped cells", () => {
  const out = renderMarkdown(
    "| Seite | Argument |\n|-------|----------|\n| Pro | **Lernen** hilft |\n| Kontra | <b>Ablenkung</b> |");
  assert.ok(out.includes("<table>"), out);
  assert.ok(out.includes("<th>Seite</th>"), out);
  assert.ok(out.includes("<strong>Lernen</strong>"), out);
  assert.ok(out.includes("&lt;b&gt;Ablenkung&lt;/b&gt;"), out);
  assert.ok(!out.includes("<b>Ablenkung</b>"), out);
});

test("a short row is padded and an overlong row is clipped to the header width", () => {
  const out = renderMarkdown("| a | b |\n|---|---|\n| only |\n| x | y | extra |");
  assert.ok(out.includes("<td>only</td><td></td>"), out);
  assert.ok(!out.includes("extra"), out);
});

test("a lone pipe in prose does not start a table", () => {
  const out = renderMarkdown("Use the | symbol here.\nNext line.");
  assert.ok(!out.includes("<table>"), out);
  assert.ok(out.includes("Use the | symbol here."), out);
});

test("a header-looking row without a separator stays text", () => {
  const out = renderMarkdown("| a | b |\n| c | d |");
  assert.ok(!out.includes("<table>"), out);
});

test("a horizontal rule renders and a list dash does not", () => {
  const out = renderMarkdown("before\n\n---\n\nafter\n- item");
  assert.ok(out.includes("<hr>"), out);
  assert.ok(out.includes("<li>item</li>"), out);
});

test("blockquote lines render as one escaped blockquote", () => {
  const out = renderMarkdown("> first <i>line</i>\n> second line\nplain");
  assert.ok(out.includes("<blockquote>"), out);
  assert.ok(out.includes("&lt;i&gt;line&lt;/i&gt;"), out);
  assert.ok(out.includes("first") && out.includes("second line"), out);
  assert.ok(out.includes("<p>plain</p>"), out);
});

test("empty and whitespace input produce nothing dangerous", () => {
  for (const value of ["", "   ", "\n\n\n", null, undefined]) {
    const out = renderMarkdown(value);
    assert.ok(!out.includes("<script"), String(value));
  }
});

// ------------------------------------------------- tutor chat CHIPS convention

test("a trailing CHIPS line becomes tap answers and leaves the text", () => {
  const { text, chips } = parseChips("Welche Seite?\n\nCHIPS: Pro | Contra");
  assert.equal(text, "Welche Seite?");
  assert.deepEqual(chips, ["Pro", "Contra"]);
});

test("a reply without a CHIPS line degrades to no chips", () => {
  const { text, chips } = parseChips("Just an answer.");
  assert.equal(text, "Just an answer.");
  assert.deepEqual(chips, []);
});

test("CHIPS mid-text is ordinary text, not a control line", () => {
  const reply = "The line CHIPS: a | b appears in running text.\nAnd more.";
  const { text, chips } = parseChips(reply);
  assert.equal(text, reply);
  assert.deepEqual(chips, []);
});

test("chips are trimmed, empties dropped, and capped at three", () => {
  const { chips } = parseChips("Q?\nCHIPS:  one |   | two | three | four ");
  assert.deepEqual(chips, ["one", "two", "three"]);
});

test("a malformed CHIPS line is still stripped so it is never spoken", () => {
  const { text, chips } = parseChips("Q?\nCHIPS: |  | ");
  assert.deepEqual(chips, []);
  assert.ok(!text.includes("CHIPS"), text);
});

test("empty input yields no chips and no crash", () => {
  for (const value of ["", null, undefined]) {
    const { chips } = parseChips(value);
    assert.deepEqual(chips, []);
  }
});

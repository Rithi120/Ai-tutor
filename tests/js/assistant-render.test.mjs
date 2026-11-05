// Unit test for the shared markdown renderer (static/js/markdown.js), used by both the
// assistant and the lesson tutor chat. Run with: node --test tests/js/*.test.mjs
// Node is not installed in the current CI image, so tests/test_js_browser.py runs this
// very file in Chrome/Edge (with stand-ins for node:test and node:assert).
//
// The property: model output is untrusted text. It is escaped first, and only then
// given the small set of tags the renderer builds itself. Nothing the model writes may
// become markup. The renderer is deliberately tiny for that reason - every construct it
// supports is one it introduces, not one it passes through.
import { test } from "node:test";
import assert from "node:assert/strict";
// markdown.js, dom.js's escapeHtml and math-protect.js are pure (no DOM), so the real
// modules are imported as they ship.
import { parseChips, renderMarkdown } from "../../static/js/markdown.js";
import { escapeHtml } from "../../static/js/dom.js";

test("escapeHtml encodes the five characters that can leave text or an attribute", () => {
  assert.equal(escapeHtml(`<a href="x" title='y'>&</a>`),
               "&lt;a href=&quot;x&quot; title=&#39;y&#39;&gt;&amp;&lt;/a&gt;");
  assert.equal(escapeHtml(null), "");
  assert.equal(escapeHtml(0), "0");
});

test("a bare URL cannot carry a style or handler attribute into the anchor", () => {
  const out = renderMarkdown('Link: https://x.test/"style="position:fixed;inset:0"x');
  assert.match(out, /<a href="https:\/\/x\.test\/" target="_blank" rel="noopener noreferrer nofollow">/);
  assert.ok(!/<a [^>]*style=/.test(out), out);
  assert.ok(out.includes("&quot;style=&quot;"), out);
});

test("markdown links are http(s) or mailto only", () => {
  assert.match(renderMarkdown("[Wiki](https://de.wikipedia.org/wiki/Satz_(Mathematik))"),
               /<a href="https:\/\/de\.wikipedia\.org\/wiki\/Satz_\(Mathematik\)"[^>]*>Wiki<\/a>/);
  assert.match(renderMarkdown("[Mail](mailto:hilfe@example.com)"), /<a href="mailto:hilfe@example\.com"/);
  for (const text of ["[x](javascript:alert(1))", "[x](data:text/html,hi)", "[x](/relative)"]) {
    assert.ok(!renderMarkdown(text).includes("<a "), text);
  }
});

test("nothing inside a formula or code is linked", () => {
  assert.ok(!renderMarkdown("$\\url{https://x.test}$").includes("<a "));
  assert.ok(!renderMarkdown("`https://x.test`").includes("<a "));
  assert.ok(!renderMarkdown("```\nhttps://x.test\n```").includes("<a "));
});

test("formulas become slots carrying their source, escaped", () => {
  const out = renderMarkdown('Es gilt \\( a < b \\) und $$\\text{"q"}$$.');
  assert.ok(out.includes('<span class="ln-math-slot" data-display="0" data-tex="a &lt; b">\\( a &lt; b \\)</span>'), out);
  assert.ok(out.includes('data-display="1" data-tex="\\text{&quot;q&quot;}"'), out);
});

test("script tags cannot survive rendering", () => {
  const out = renderMarkdown('<script>alert("x")</script>');
  assert.ok(!out.includes("<script"), out);
  assert.ok(out.includes("&lt;script"), out);
});

test("event-handler attributes cannot be produced", () => {
  const out = renderMarkdown('<img src=x onerror="alert(1)">');
  assert.ok(!out.includes("<img"), out);
  // The words stay visible as escaped text; what must never exist is the attribute on a tag.
  assert.ok(!/<[a-z][^>]*\sonerror=/i.test(out), out);
  assert.ok(out.includes("onerror=&quot;alert(1)&quot;"), out);
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

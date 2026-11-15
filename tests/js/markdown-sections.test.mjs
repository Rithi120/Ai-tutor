// Readable chat answers: labelled parts become sections (static/js/markdown-sections.js).
// Run with: node --test tests/js (Node is not installed here: tests/test_js_browser.py runs
// this file unchanged in Chrome).
import test from "node:test";
import assert from "node:assert/strict";
import { LONG_ANSWER_CHARACTERS, labelKind, sectionStart, sectionize } from "../../static/js/markdown-sections.js";
import { renderMarkdown } from "../../static/js/markdown.js";

test("labels are recognised in German, English, French and Spanish", () => {
  assert.equal(labelKind("Antwort"), "answer");
  assert.equal(labelKind("Beispiel:"), "example");
  assert.equal(labelKind("Check‑Frage"), "check", "a non-breaking hyphen as gpt-oss writes it");
  assert.equal(labelKind("Typische Fehler"), "warning");
  assert.equal(labelKind("Formel"), "formula");
  assert.equal(labelKind("Ergebnis"), "result");
  assert.equal(labelKind("Unabhängige Übung"), "exercise");
  assert.equal(labelKind("Common mistakes"), "warning");
  assert.equal(labelKind("Exemple"), "example");
  assert.equal(labelKind("Ejemplo"), "example");
  assert.equal(labelKind("💡 Tipp"), "tip");
  assert.equal(labelKind("Die Steigung einer Geraden ist das Verhältnis"), "", "a sentence is no label");
  assert.equal(labelKind("Beispielhaft"), "", "a longer word is not the label");
});

test("a heading or a bold first line starts a section", () => {
  assert.deepEqual(sectionStart("<h5>Beispiel</h5>"), { label: "Beispiel", rest: "" });
  assert.deepEqual(sectionStart("<p><strong>Check-Frage</strong><br>Wie lautet die Formel?</p>"),
    { label: "Check-Frage", rest: "<p>Wie lautet die Formel?</p>" });
  assert.equal(sectionStart("<p>Die <strong>Steigung</strong> ist …</p>"), null, "bold inside a sentence");
});

test("an answer with labels becomes sections with icons; a plain one is unchanged", () => {
  const html = renderMarkdown("Kurz vorab.\n\n**Antwort**\nDie Steigung ist 2.\n\n---\n\n**Beispiel**\nA(1|2), B(4|8).\n\n**Check-Frage**\nWas bedeutet m < 0?");
  assert.match(html, /^<p>Kurz vorab\.<\/p>/, "the introduction stays first");
  assert.equal((html.match(/<section class="md-section/g) || []).length, 3);
  assert.match(html, /md-kind-answer[\s\S]*🧠[\s\S]*Antwort/);
  assert.match(html, /md-kind-example[\s\S]*💡/);
  assert.match(html, /md-kind-check[\s\S]*❓[\s\S]*Was bedeutet m &lt; 0\?/);
  assert.doesNotMatch(html, /<hr>/, "the cards separate the sections");
  const plain = "Die Steigung ist 2.\n\nSie sagt, wie steil die Gerade ist.";
  assert.equal(renderMarkdown(plain), "<p>Die Steigung ist 2.</p>\n<p>Sie sagt, wie steil die Gerade ist.</p>");
  assert.doesNotMatch(renderMarkdown("**Antwort**\nNur eine Überschrift."), /md-section/, "one label is not enough");
});

test("a long answer folds its later sections but never the answer, result, mistakes or check", () => {
  const filler = "Satz. ".repeat(Math.ceil(LONG_ANSWER_CHARACTERS / 6));
  const source = ["**Antwort**", filler, "**Beispiel**", "Rechnung.", "### Hintergrund", "Geschichte.",
    "**Typische Fehler**", "Vorzeichen.", "**Tipp**", "Probe machen.", "**Check-Frage**", "Warum?"].join("\n\n");
  const html = renderMarkdown(source);
  assert.match(html, /<section class="md-section md-kind-answer">/);
  assert.match(html, /<section class="md-section md-kind-example">/, "the first sections stay open");
  assert.match(html, /<details class="md-section md-kind-plain"><summary class="md-section-title">/);
  assert.match(html, /<section class="md-section md-kind-warning">/, "mistakes never fold");
  assert.match(html, /<details class="md-section md-kind-tip">/);
  assert.match(html, /<section class="md-section md-kind-check">/, "the question never folds");
  assert.match(html, /Probe machen\./, "folded content is still there");
});

test("labels and content stay escaped", () => {
  const html = renderMarkdown("**Antwort**\n<img src=x onerror=alert(1)>\n\n**Beispiel**\n<script>x</script>");
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img/);
  const blocks = sectionize(["<p><strong>Antwort</strong></p>", "<p>a</p>", "<h5>Beispiel</h5>", "<p>b</p>"]);
  assert.equal(blocks.length, 2);
});

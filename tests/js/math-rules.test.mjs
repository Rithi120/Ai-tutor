// Unit tests for the pure formula-detection rules. Run with: node --test tests/js/*.test.mjs
// (Node is not installed in the current CI image. tests/test_math_rendering.py checks the
// same corpus against a transcription of this module, and fails if the two drift.)
import { test } from "node:test";
import assert from "node:assert/strict";
import { hasMath, looksLikeMath, splitMath, MAX_INLINE_LENGTH } from "../../static/js/math-rules.js";

const formulas = (text) => splitMath(text).filter(s => s.type === "math").map(s => s.value);

test("inline and block delimiters are all recognised", () => {
  assert.deepEqual(formulas("Energy is $E=mc^2$ exactly."), ["E=mc^2"]);
  assert.deepEqual(formulas("$$x^2+y^2=z^2$$"), ["x^2+y^2=z^2"]);
  assert.deepEqual(formulas("Use \\(a+b\\) inline."), ["a+b"]);
  assert.deepEqual(formulas("Block: \\[\\int_0^1 x\\,dx\\]"), ["\\int_0^1 x\\,dx"]);
});

test("the notation the prompts ask for survives", () => {
  assert.deepEqual(formulas("Fraction $\\frac{a}{b}$."), ["\\frac{a}{b}"]);
  assert.deepEqual(formulas("Root $\\sqrt{x+1}$."), ["\\sqrt{x+1}"]);
  assert.deepEqual(formulas("Greek $\\pi$ and $\\Omega$."), ["\\pi", "\\Omega"]);
  assert.deepEqual(formulas("$$\\begin{pmatrix}1&0\\\\0&1\\end{pmatrix}$$"),
                   ["\\begin{pmatrix}1&0\\\\0&1\\end{pmatrix}"]);
  assert.deepEqual(formulas("Subscript $a_{ij}$, exponent $e^{i\\pi}$."), ["a_{ij}", "e^{i\\pi}"]);
});

test("money is not mathematics", () => {
  for (const text of ["The book costs $5.",
                      "Between $5 and $10 per hour.",
                      "Prices: $5, $10, $15 each.",
                      "I paid $20$ for it.",
                      "Revenue rose from $1m to $2m."]) {
    assert.deepEqual(formulas(text), [], text);
  }
});

test("an escaped dollar is a literal dollar", () => {
  assert.deepEqual(formulas("A literal \\$5 price."), []);
  assert.equal(splitMath("Costs \\$5")[0].value, "Costs $5");
});

test("math and money can share a sentence", () => {
  assert.deepEqual(formulas("Solve $x^2$ then pay $5 today."), ["x^2"]);
});

test("an unclosed or runaway span stays as text", () => {
  assert.deepEqual(formulas("An open $x^2 with no end."), []);
  assert.deepEqual(formulas(`$${"a".repeat(MAX_INLINE_LENGTH + 10)}$`), []);
});

test("the original source is recoverable from the segments", () => {
  const original = "Energy $E=mc^2$ and $$\\frac{a}{b}$$ done.";
  const rebuilt = splitMath(original)
    .map(s => s.type === "text" ? s.value : (s.display ? `$$${s.value}$$` : `$${s.value}$`))
    .join("");
  assert.equal(rebuilt, original);
});

test("display math is always accepted, inline math has to earn it", () => {
  assert.equal(looksLikeMath("anything at all", true), true);
  assert.equal(looksLikeMath(" x ", false), false);      // padded against its delimiters
  assert.equal(looksLikeMath("5, ", false), false);      // trailing space: a price list
  assert.equal(looksLikeMath("x^2", false), true);
});

test("hasMath is a cheap yes/no for the renderer", () => {
  assert.equal(hasMath("nothing here"), false);
  assert.equal(hasMath("costs $5 and $10"), false);
  assert.equal(hasMath("value $x$"), true);
});

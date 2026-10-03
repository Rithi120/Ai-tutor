/* Deciding what is a formula and what is just text. No DOM, no KaTeX, no imports.
 *
 * Split out from the renderer so it can be unit-tested (tests/js/math-rules.test.mjs),
 * following the same convention as mode-rules.js and suggest-rules.js.
 *
 * The hard part is `$`. AI content writes formulas as $x^2$, and students write prices
 * as "$5". A naive renderer reading "between $5 and $10" sees the formula "5 and " and
 * typesets it, which is worse than showing the raw text it was meant to fix. The rules
 * below are deliberately conservative: when a span is ambiguous it stays as text,
 * because unrendered LaTeX is a cosmetic problem and mangled prose is a correctness one.
 */

export const MAX_INLINE_LENGTH = 200;

// Longest first, so $$ is never mistaken for two empty $ spans.
const DELIMITERS = [
  { open: "$$", close: "$$", display: true },
  { open: "\\[", close: "\\]", display: true },
  { open: "\\(", close: "\\)", display: false },
  { open: "$", close: "$", display: false },
];

// What makes a span look like mathematics rather than a number in a sentence: a LaTeX
// command, a script, a group, a relation, or a variable.
const MATH_SIGNAL = /[a-zA-Z\\^_{}=+<>/|]|[±×÷⁄←-⇿∀-⋿Α-ω]/;

/** Whether the text between two delimiters should be typeset. */
export function looksLikeMath(tex, display) {
  if (typeof tex !== "string" || !tex.trim()) return false;
  if (display) return true;                       // $$…$$ is always deliberate
  if (tex.length > MAX_INLINE_LENGTH) return false;
  // "between $5 and $10" closes on the second price: the span ends in a space, and no
  // real inline formula is padded with whitespace against its own delimiters.
  if (/^\s|\s$/.test(tex)) return false;
  if (/[\n\r]/.test(tex)) return false;           // inline math does not span lines
  // "$20" in "I paid $20$ for it" has no variable, operator or command in it.
  return MATH_SIGNAL.test(tex);
}

function matchAt(text, index) {
  for (const delimiter of DELIMITERS) {
    if (!text.startsWith(delimiter.open, index)) continue;
    let search = index + delimiter.open.length;
    while (search < text.length) {
      const close = text.indexOf(delimiter.close, search);
      if (close === -1) break;
      if (text[close - 1] === "\\") { search = close + 1; continue; }   // escaped
      return { delimiter, tex: text.slice(index + delimiter.open.length, close),
               end: close + delimiter.close.length };
    }
  }
  return null;
}

/**
 * Split text into alternating prose and formula segments.
 *
 * Returns [{type: "text", value}, {type: "math", value, display}, …]. The formula's
 * `value` is its LaTeX source exactly as written, so the original is always recoverable.
 */
export function splitMath(text) {
  const source = String(text ?? "");
  const segments = [];
  let plain = "";
  let index = 0;

  const flush = () => { if (plain) { segments.push({ type: "text", value: plain }); plain = ""; } };

  while (index < source.length) {
    if (source[index] === "\\" && (source[index + 1] === "$" || source[index + 1] === "\\")) {
      plain += source[index + 1] === "$" ? "$" : "\\\\";   // \$ is a literal dollar sign
      index += 2;
      continue;
    }
    const found = matchAt(source, index);
    if (found && looksLikeMath(found.tex, found.delimiter.display)) {
      flush();
      segments.push({ type: "math", value: found.tex, display: found.delimiter.display });
      index = found.end;
      continue;
    }
    plain += source[index];
    index += 1;
  }
  flush();
  return segments;
}

/** Whether a string contains anything worth loading a formula renderer for. */
export function hasMath(text) {
  return splitMath(text).some(segment => segment.type === "math");
}

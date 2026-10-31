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

// A power written in plain text, outside any delimiters: "4^2", "a^{m+n}", "x^-1",
// "(a+b)^2". The base must touch the caret, so a lone "^" in prose ("see ^ above") or a
// caret between spaces is never treated as an exponent.
const BARE_POWER = /([\p{L}\p{N})\]])\^(?:\{([^{}\n]{1,24})\}|(-?[\p{L}\p{N}]{1,4}))/gu;

export function hasBarePower(text) {
  BARE_POWER.lastIndex = 0;
  return BARE_POWER.test(String(text ?? ""));
}

/**
 * Split plain text into text and superscript segments, so "4^2·4^3" reads as 4²·4³
 * instead of showing the caret. Used for powers the model wrote without $…$, and for
 * the fallback when KaTeX cannot load. Returns [{type: "text"|"sup", value}, …].
 */
export function powerSegments(text) {
  const source = String(text ?? "");
  const segments = [];
  let last = 0;
  BARE_POWER.lastIndex = 0;
  for (let match = BARE_POWER.exec(source); match; match = BARE_POWER.exec(source)) {
    const caret = match.index + match[1].length;
    segments.push({ type: "text", value: source.slice(last, caret) });
    segments.push({ type: "sup", value: match[2] ?? match[3] });
    last = match.index + match[0].length;
  }
  segments.push({ type: "text", value: source.slice(last) });
  return segments.filter(segment => segment.value !== "");
}

/**
 * A LaTeX source made readable as plain text, for when the formula renderer is not
 * available (offline, CDN blocked): \frac{a}{b} -> (a)/(b), \cdot -> ·, \sqrt{x} -> √(x).
 * Powers are kept as "^" so powerSegments can raise them. Never alters the mathematics,
 * only its spelling.
 */
export function readableTex(tex) {
  let value = String(tex ?? "");
  for (let pass = 0; pass < 3; pass += 1) {
    value = value
      .replace(/\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}/g, "($1)/($2)")
      .replace(/\\sqrt\{([^{}]*)\}/g, "√($1)");
  }
  const symbols = {
    cdot: "·", times: "×", div: "÷", pm: "±", mp: "∓", le: "≤", leq: "≤", ge: "≥", geq: "≥",
    neq: "≠", ne: "≠", approx: "≈", infty: "∞", pi: "π", alpha: "α", beta: "β", gamma: "γ",
    delta: "δ", Delta: "Δ", lambda: "λ", mu: "μ", sigma: "σ", theta: "θ", rightarrow: "→",
    to: "→", Rightarrow: "⇒", cdots: "⋯", ldots: "…", circ: "°", degree: "°",
  };
  value = value
    .replace(/\\(?:left|right|displaystyle|,|;|!|quad|qquad)/g, " ")
    .replace(/\\(?:text|mathrm|mathbf|operatorname)\{([^{}]*)\}/g, "$1")
    .replace(/\\([A-Za-z]+)/g, (whole, name) => symbols[name] ?? name)
    .replace(/_\{([^{}]*)\}/g, "_$1");
  return value.replace(/\s+/g, " ").trim();
}

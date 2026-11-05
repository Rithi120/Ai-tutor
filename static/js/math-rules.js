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

/**
 * Whether a span between these delimiters is a formula. Only a single `$` is ambiguous
 * (prices), so only it has to pass looksLikeMath. `\(…\)` is never a price: the models
 * write `\( a = 2 \)` with inner spaces, and `\(8\)` is a formula too, so any non-empty
 * content counts - spaces, digits only, newlines and long spans included.
 */
export function acceptsMath(open, tex) {
  if (open === "$") return looksLikeMath(tex, false);
  return typeof tex === "string" && tex.trim() !== "";
}

/** A delimiter at `index` is escaped when an odd number of backslashes precedes it:
 *  `x \\]` is a row break followed by "]", `\\\]` is a row break and then the closer. */
export function isEscaped(text, index) {
  let count = 0;
  for (let cursor = index - 1; cursor >= 0 && text[cursor] === "\\"; cursor -= 1) count += 1;
  return count % 2 === 1;
}

function matchAt(text, index) {
  for (const delimiter of DELIMITERS) {
    if (!text.startsWith(delimiter.open, index)) continue;
    let search = index + delimiter.open.length;
    while (search < text.length) {
      const close = text.indexOf(delimiter.close, search);
      if (close === -1) break;
      if (isEscaped(text, close)) { search = close + 1; continue; }
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
    if (found && acceptsMath(found.delimiter.open, found.tex)) {
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

const READABLE_SYMBOLS = {
  cdot: "·", times: "×", div: "÷", pm: "±", mp: "∓", le: "≤", leq: "≤", ge: "≥", geq: "≥",
  neq: "≠", ne: "≠", approx: "≈", equiv: "≡", sim: "∼", propto: "∝", infty: "∞",
  to: "→", rightarrow: "→", leftarrow: "←", Rightarrow: "⇒", Leftarrow: "⇐",
  Leftrightarrow: "⇔", leftrightarrow: "↔", cdots: "⋯", ldots: "…", dots: "…", circ: "°",
  degree: "°", in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", cup: "∪", cap: "∩",
  forall: "∀", exists: "∃", angle: "∠", triangle: "△", perp: "⊥", parallel: "∥",
  partial: "∂", nabla: "∇", sum: "Σ", prod: "Π", int: "∫", lvert: "|", rvert: "|",
  mid: "|", vert: "|", lVert: "‖", rVert: "‖", percent: "%",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε", zeta: "ζ",
  eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ", lambda: "λ", mu: "μ", nu: "ν",
  xi: "ξ", pi: "π", rho: "ρ", sigma: "σ", tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ",
  chi: "χ", psi: "ψ", omega: "ω", Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ",
  Pi: "Π", Sigma: "Σ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};
// Operator names that read correctly as the bare word.
const READABLE_WORDS = new Set([
  "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh",
  "tanh", "log", "ln", "lg", "exp", "lim", "max", "min", "det", "gcd", "dim", "deg",
]);

/** The balanced `{…}` group starting at `start`, or null. */
function groupAt(value, start) {
  if (value[start] !== "{") return null;
  let depth = 0;
  for (let cursor = start; cursor < value.length; cursor += 1) {
    if (value[cursor] === "\\") { cursor += 1; continue; }
    if (value[cursor] === "{") depth += 1;
    else if (value[cursor] === "}") {
      depth -= 1;
      if (depth === 0) return { inner: value.slice(start + 1, cursor), end: cursor + 1 };
    }
  }
  return null;
}

/** Rewrite every `\name{a}{b}…` (balanced groups, so nested braces survive). */
function rewriteCommand(value, pattern, groups, build) {
  let out = "";
  let last = 0;
  pattern.lastIndex = 0;
  for (let match = pattern.exec(value); match; match = pattern.exec(value)) {
    let cursor = match.index + match[0].length;
    let option = "";
    if (match[0].endsWith("sqrt") && value[cursor] === "[") {
      const close = value.indexOf("]", cursor);
      if (close !== -1) { option = value.slice(cursor + 1, close); cursor = close + 1; }
    }
    const parts = [];
    for (let index = 0; index < groups; index += 1) {
      const group = groupAt(value, cursor);
      if (!group) break;
      parts.push(group.inner);
      cursor = group.end;
    }
    if (parts.length !== groups) continue;
    out += value.slice(last, match.index) + build(parts, option);
    last = cursor;
    pattern.lastIndex = cursor;
  }
  return out + value.slice(last);
}

/**
 * A LaTeX source made readable as plain text, for when the formula renderer is not
 * available (offline, CDN blocked): \frac{a}{b} -> (a)/(b), \cdot -> ·, \sqrt{x} -> √(x).
 * Powers are kept as "^" so powerSegments can raise them. Never alters the mathematics,
 * only its spelling.
 *
 * With `strict` (the renderer's offline fallback) it returns null as soon as anything
 * it cannot spell remains - an unknown command, a matrix, a row break - so the caller
 * shows the formula's source instead. Dropping the backslash of an unknown command is
 * how a student once saw "frac{x2}{ (a+b )}". Read-aloud uses the lenient default,
 * where an unknown command is spoken as its name.
 */
export function readableTex(tex, { strict = false } = {}) {
  let value = String(tex ?? "");
  for (let pass = 0; pass < 6; pass += 1) {
    const before = value;
    value = rewriteCommand(value, /\\[dt]?frac(?![A-Za-z])/g, 2, ([top, bottom]) => `(${top})/(${bottom})`);
    value = rewriteCommand(value, /\\sqrt(?![A-Za-z])/g, 1, ([inner], index) => `${index}√(${inner})`);
    value = rewriteCommand(value, /\\(?:text|textbf|textit|mathrm|mathbf|mathit|operatorname)(?![A-Za-z])/g, 1,
                           ([inner]) => inner);
    if (value === before) break;
  }
  let unknown = false;
  value = value
    .replace(/\\(?:left|right|displaystyle|textstyle|big|Big|bigg|Bigg)(?![A-Za-z])/g, "")
    .replace(/\\(?:quad|qquad)(?![A-Za-z])|\\[,;:! ]/g, " ")
    .replace(/\\([{}%$&#_|])/g, "$1")
    .replace(/\\([A-Za-z]+)/g, (whole, name) => {
      if (Object.prototype.hasOwnProperty.call(READABLE_SYMBOLS, name)) return READABLE_SYMBOLS[name];
      if (READABLE_WORDS.has(name)) return name;
      unknown = true;
      return name;
    })
    .replace(/_\{([^{}]*)\}/g, "_$1");
  if (strict && (unknown || value.includes("\\"))) return null;
  return value.replace(/\s+/g, " ").trim();
}

/* Finding the formulas in a reply before markdown sees it. Pure: no DOM, no KaTeX.
 *
 * The markdown renderer used to run first and the formula renderer second, on whatever
 * text nodes were left. Markdown joins lines with <br>, splits table rows on every "|",
 * italicises between two "*" and links URLs - so a formula that spanned lines, sat in a
 * table, contained a "*" or a "|", or was indented under a list item lost its pairing and
 * reached the student as raw LaTeX. The formulas are therefore taken out of the raw reply
 * first and replaced by opaque private-use tokens; markdown runs on the tokenised text and
 * the tokens are swapped back for formula slots afterwards (static/js/markdown.js).
 *
 * What counts as a formula:
 *   $$…$$  and  \[…\]           display, may span lines
 *   \(…\)                        inline, may span lines and be padded with spaces
 *   \begin{aligned}…\end{aligned} (and the other environments below), display
 *   $…$                          inline, only when looksLikeMath() says so - prices stay text
 * Code (fenced blocks and `inline code`) is copied untouched, `\$` is a literal dollar, a
 * closer preceded by an odd number of backslashes is escaped, and an opener that never
 * closes is plain text.
 *
 * splitStreamingSource() runs the same scan on a partial reply and says where the part
 * that can already be rendered ends, so a streamed reply never shows half a formula and
 * the finished reply is rendered by the identical tokeniser.
 */
import { looksLikeMath } from "./math-rules.js";

// One formula token in the tokenised text: <index into spans>.
export const MATH_TOKEN = /(\d+)/g;
// `\$` in prose: a dollar no later pass may pair (restored after markdown).
export const LITERAL_DOLLAR = "";
// The private-use characters the renderer reserves; a model cannot inject them. U+E005 is
// markdown.js's line break inside a table cell (CELL_BREAK).
export const RESERVED = /[-]/g;
// A fence line exactly as markdown.js reads it, so both agree on what is code.
export const FENCE = /^\s*```(\w*)\s*$/;

const ENVIRONMENTS = new Set([
  "aligned", "align", "align*", "equation", "equation*", "gather", "gathered", "cases",
  "matrix", "pmatrix", "bmatrix", "vmatrix", "Vmatrix", "array", "split", "multline",
]);
// Longest first, so $$ is never read as two empty $ spans.
const PAIRS = [
  { open: "$$", close: "$$", display: true },
  { open: "\\[", close: "\\]", display: true },
  { open: "\\(", close: "\\)", display: false },
];
const BEGIN = /^\\begin\{([A-Za-z]+\*?)\}/;
// The end of a partial reply that could still become a formula opener: "\", "\beg",
// "\begin{alig".
const PARTIAL_COMMAND = /^\\(?:[A-Za-z]*|begin\{[A-Za-z]*\*?)$/;
// The last, incomplete line of a partial reply that could still become a fence.
const PARTIAL_FENCE = /^[ \t]*`{1,3}\w*[ \t]*$/;

function normalise(source) {
  return String(source ?? "").replace(/\r\n?/g, "\n").replace(RESERVED, "");
}

function escapedAt(text, index) {
  let count = 0;
  for (let cursor = index - 1; cursor >= 0 && text[cursor] === "\\"; cursor -= 1) count += 1;
  return count % 2 === 1;
}

/** Fenced code blocks as [start, end) ranges, by markdown.js's own fence rule. */
function fenceRanges(text) {
  const ranges = [];
  let offset = 0;
  let openAt = -1;
  for (const line of text.split("\n")) {
    const lineEnd = offset + line.length;
    if (FENCE.test(line)) {
      if (openAt === -1) {
        openAt = offset;
      } else {
        ranges.push({ start: openAt, end: Math.min(lineEnd + 1, text.length), open: false });
        openAt = -1;
      }
    }
    offset = lineEnd + 1;
  }
  if (openAt !== -1) ranges.push({ start: openAt, end: text.length, open: true });
  return ranges;
}

/** The first unescaped `close` at or after `from` that ends before `limit`, or -1. */
function closerAt(text, close, from, limit) {
  for (let at = text.indexOf(close, from); at !== -1; at = text.indexOf(close, at + 1)) {
    if (at + close.length > limit) return -1;
    if (!escapedAt(text, at)) return at;
  }
  return -1;
}

/** The end of the \begin{env}…\end{env} block starting at `start` (nesting counted). */
function environmentEnd(text, start, name, limit) {
  const begin = `\\begin{${name}}`;
  const end = `\\end{${name}}`;
  let depth = 0;
  let cursor = start;
  while (cursor < limit) {
    const nextEnd = text.indexOf(end, cursor);
    if (nextEnd === -1 || nextEnd + end.length > limit) return -1;
    const nextBegin = text.indexOf(begin, cursor);
    if (nextBegin !== -1 && nextBegin < nextEnd) {
      depth += 1;
      cursor = nextBegin + begin.length;
      continue;
    }
    depth -= 1;
    cursor = nextEnd + end.length;
    if (depth === 0) return cursor;
  }
  return -1;
}

/**
 * What starts at `index`: a formula `{tex, display, open, close, end}`, an opener that
 * never closes `{unclosed, width, inline}`, or null for ordinary text.
 */
function spanAt(text, index, limit) {
  for (const pair of PAIRS) {
    if (!text.startsWith(pair.open, index)) continue;
    const at = closerAt(text, pair.close, index + pair.open.length, limit);
    if (at === -1) return { unclosed: true, width: pair.open.length };
    const inner = text.slice(index + pair.open.length, at);
    // Empty, or reaching into `inline code` (LaTeX has no backticks): not a formula.
    if (!inner.trim() || inner.includes("`")) return { literal: true, width: pair.open.length };
    return { tex: inner.trim(), display: pair.display, open: pair.open, close: pair.close,
             end: at + pair.close.length };
  }
  if (text.startsWith("\\begin{", index)) {
    const begin = BEGIN.exec(text.slice(index, index + 40));
    if (!begin || !ENVIRONMENTS.has(begin[1])) return null;
    const end = environmentEnd(text, index, begin[1], limit);
    if (end === -1) return { unclosed: true, width: begin[0].length };
    if (text.slice(index, end).includes("`")) return null;
    return { tex: text.slice(index, end), display: true, open: "", close: "", end };
  }
  if (text[index] === "$") {
    const at = closerAt(text, "$", index + 1, limit);
    if (at === -1) return { unclosed: true, width: 1, inline: true };
    const tex = text.slice(index + 1, at);
    // The strict rule: "between $5 and $10" is two prices, not the formula "5 and ".
    if (tex.includes("`") || !looksLikeMath(tex, false)) return null;
    return { tex, display: false, open: "$", close: "$", end: at + 1 };
  }
  return null;
}

function scan(source, { streaming = false } = {}) {
  const text = normalise(source);
  const fences = fenceRanges(text);
  const spans = [];
  let out = "";
  let index = 0;
  let nextFence = 0;
  let hold = -1;
  const lastLineStart = text.lastIndexOf("\n") + 1;
  const lastLine = (at) => at >= lastLineStart;

  while (index < text.length) {
    const fence = fences[nextFence];
    if (fence && fence.start === index) {
      // An unterminated fence in a partial reply: the code may still close.
      if (streaming && fence.open) { hold = index; break; }
      out += text.slice(fence.start, fence.end);
      index = fence.end;
      nextFence += 1;
      continue;
    }
    // No formula reaches into a code block below it.
    const limit = fence ? fence.start : text.length;
    const char = text[index];

    if (streaming && index === lastLineStart && PARTIAL_FENCE.test(text.slice(index))) {
      hold = index;
      break;
    }
    if (char === "`") {
      const end = text.indexOf("`", index + 1);
      const code = end === -1 ? "" : text.slice(index + 1, end);
      if (end !== -1 && code && !code.includes("\n")) {
        out += text.slice(index, end + 1);
        index = end + 1;
        continue;
      }
      // A backtick on the last line of a partial reply may still be closed.
      if (streaming && lastLine(index)) { hold = index; break; }
      out += char;
      index += 1;
      continue;
    }
    if (char === "\\") {
      const next = text[index + 1];
      if (next === "$") { out += LITERAL_DOLLAR; index += 2; continue; }
      if (next === "\\") { out += "\\\\"; index += 2; continue; }
      if (streaming && PARTIAL_COMMAND.test(text.slice(index))) { hold = index; break; }
    }
    if (char === "$" || char === "\\") {
      const found = spanAt(text, index, limit);
      if (found?.unclosed) {
        // A partial reply: the closer may still arrive. A single $ cannot span lines, so
        // one with a line break after it is already plain text.
        if (streaming && (!found.inline || lastLine(index))) { hold = index; break; }
        out += text.slice(index, index + found.width);
        index += found.width;
        continue;
      }
      if (found?.literal) {
        out += text.slice(index, index + found.width);
        index += found.width;
        continue;
      }
      if (found) {
        out += `${spans.length}`;
        spans.push({ tex: found.tex, display: found.display, open: found.open, close: found.close,
                     raw: text.slice(index, found.end) });
        index = found.end;
        continue;
      }
    }
    out += char;
    index += 1;
  }
  return { text, out, spans, hold };
}

/**
 * Tokenise the formulas in a raw reply.
 * Returns { text, spans }: `text` with every formula replaced by <n>, and
 * spans[n] = { tex, display, open, close, raw } (`raw` is the source with delimiters).
 */
export function protectMath(source) {
  const { out, spans } = scan(source);
  return { text: out, spans };
}

/**
 * Split a partial (streaming) reply into the part that can be rendered now and the part
 * that must wait: `stable` ends before the first formula opener that has not closed yet
 * (and before a code fence that has not closed, at that line's start); `pending` is the
 * rest, to be shown as escaped plain text until more arrives.
 */
export function splitStreamingSource(source) {
  const { text, hold } = scan(source, { streaming: true });
  const at = hold === -1 ? text.length : hold;
  return { stable: text.slice(0, at), pending: text.slice(at) };
}

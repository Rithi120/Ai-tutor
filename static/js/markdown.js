/* Escape-first markdown renderer shared by the assistant and the lesson tutor chat.
 *
 * Model output is untrusted text. It is escaped first and only then given the small
 * set of tags this file introduces itself, so nothing the model writes can become
 * markup. The renderer is deliberately tiny for that reason: every construct it
 * supports is one it builds, not one it passes through.
 * Security property locked by tests/js/assistant-render.test.mjs (run in a real browser by
 * tests/test_js_browser.py, since Node is not installed).
 *
 * Formulas and links are taken out of the raw text before any of this runs and put back
 * afterwards, so markdown can never split, italicise or link inside a formula:
 *   1. protectMath (math-protect.js) replaces each formula with a private-use token;
 *   2. links ([label](url), bare http(s) URLs) become tokens too, outside code only, and
 *      only for http(s) - or mailto for an explicit [label](mailto:…) - URLs that contain
 *      no quote, angle bracket, backslash or whitespace;
 *   3. block markdown is rendered on the tokenised, escaped text;
 *   4. tokens become <a> elements and formula slots (<span class="ln-math-slot">) that
 *      math.js typesets; a slot shows its escaped source until then.
 */
import { escapeAttr, escapeHtml } from "./dom.js";
import { FENCE, LITERAL_DOLLAR, MATH_TOKEN, protectMath } from "./math-protect.js";

const LINK_TOKEN = /(\d+)/g;
const MAX_LIST_DEPTH = 3;
const LIST_ITEM = /^(\s*)(?:[-*•]|(\d{1,9})[.)])\s+(.*)$/;
// A line that is nothing but one formula token (checked against the span: display only).
const TOKEN_LINE = /^\s*(\d+)\s*$/;

function renderInline(text) {
  // `text` is already escaped. Each replacement introduces only tags built here; links
  // and formulas are tokens at this point, so none of this can reach inside them.
  return text
    .replace(/`([^`\n]+)`/g, (_m, code) => `<code>${code}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, (_m, bold) => `<strong>${bold}</strong>`)
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, (_m, lead, italic) => `${lead}<em>${italic}</em>`);
}

/* ------------------------------------------------------------------- links */

const UNSAFE_URL = /["'<>\\\s-]/;
const MARKDOWN_LINK = /\[([^[\]\n]{1,300})\]\(((?:[^\s()]|\([^\s()]*\))+)\)/g;
const BARE_URL = /\bhttps?:\/\/[^\s<>"'`\\-]+/g;

/** The URL when it may become a link: http(s) only (and mailto for an explicit link),
 *  with nothing in it that could leave the attribute. Anything else stays text. */
function safeUrl(url, { mailto = false } = {}) {
  if (!url || UNSAFE_URL.test(url)) return null;
  let protocol;
  try {
    protocol = new URL(url).protocol;
  } catch (_) {
    return null;
  }
  return protocol === "http:" || protocol === "https:" || (mailto && protocol === "mailto:") ? url : null;
}

/** A bare URL without the sentence punctuation after it ("see https://x.test/a)."). */
function trimUrl(url) {
  let end = url.length;
  for (;;) {
    const last = url[end - 1];
    if (".,;:!?]".includes(last)) { end -= 1; continue; }
    if (last === ")") {
      const body = url.slice(0, end);
      if ((body.match(/\(/g) || []).length < (body.match(/\)/g) || []).length) { end -= 1; continue; }
    }
    break;
  }
  return url.slice(0, end);
}

function linkify(part, links) {
  const token = (url, label) => {
    links.push({ url, label });
    return `${links.length - 1}`;
  };
  return part
    .replace(MARKDOWN_LINK, (whole, label, url) => {
      const safe = safeUrl(url, { mailto: true });
      return safe ? token(safe, label) : whole;
    })
    .replace(BARE_URL, (found) => {
      const url = trimUrl(found);
      const safe = safeUrl(url);
      return safe ? token(safe, url) + found.slice(url.length) : found;
    });
}

/** Tokenise links outside fenced and inline code. Returns { text, links }. */
function protectLinks(text) {
  const links = [];
  let inFence = false;
  const lines = text.split("\n").map((line) => {
    if (FENCE.test(line)) { inFence = !inFence; return line; }
    if (inFence) return line;
    return line.split(/(`[^`\n]+`)/).map((part, index) => (index % 2 ? part : linkify(part, links))).join("");
  });
  return { text: lines.join("\n"), links };
}

/* ------------------------------------------------------------------ tables */

// A line break inside a table cell (a private-use character math-protect.js strips from
// the model's text, so only joinBrokenRow can put one there); it becomes <br> after escaping.
const CELL_BREAK = "";
const MAX_ROW_CONTINUATIONS = 4;

// A row ends with "|" (not an escaped "\|").
function rowClosed(line) {
  return /(^|[^\\])\|\s*$/.test(line);
}

/* A table row the model broke inside a cell (live 2026-11-02):
 *   | 3 | **Addiere \(…\)** zu beiden Seiten:
 *   \(…\) | Wir ergänzen … |
 * GitHub-flavoured markdown ends the row at the line break, so the formula became the next
 * row's first cell. In a table written with outer pipes, a row that is still open (no
 * closing "|") takes the following lines that do not start with "|", joined by a cell line
 * break - at most MAX_ROW_CONTINUATIONS of them, none blank, a fence or a separator - but
 * only when the joined row then closes and has no more cells than the header. Otherwise
 * nothing is joined and the table reads as it always did. Returns {row, last} or null. */
function joinBrokenRow(lines, start, columns) {
  const first = lines[start].trimEnd();
  if (!first.trimStart().startsWith("|") || rowClosed(first)) return null;
  let row = first;
  for (let next = start + 1; next < lines.length && next <= start + MAX_ROW_CONTINUATIONS; next += 1) {
    const part = lines[next].trim();
    if (!part || part.startsWith("|") || FENCE.test(part) || isTableSeparator(part)) return null;
    row += CELL_BREAK + part;
    if (rowClosed(row)) return splitTableRow(row).length <= columns ? { row, last: next } : null;
  }
  return null;
}

// A pipe-table separator row like "|---|:---:|" - only pipes, dashes, colons and
// spaces, with at least one real dash run. It is what commits the line above to
// being a table header; a lone "|" inside prose never starts a table.
function isTableSeparator(line) {
  return line.includes("-") && /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && /-{2,}/.test(line);
}

// Cells split on "|" outside `code` (formulas are tokens already, so $|x|$ survives); "\|"
// is a literal pipe.
function splitTableRow(line) {
  const row = line.trim().replace(/^\|/, "").replace(/(^|[^\\])\|$/, "$1");
  const code = [...row.matchAll(/`[^`\n]+`/g)].map((match) => [match.index, match.index + match[0].length]);
  const inCode = (index) => code.some(([start, end]) => index > start && index < end);
  const cells = [];
  let cell = "";
  for (let index = 0; index < row.length; index += 1) {
    const char = row[index];
    if (char === "\\" && row[index + 1] === "|" && !inCode(index)) { cell += "|"; index += 1; continue; }
    if (char === "|" && !inCode(index)) { cells.push(cell.trim()); cell = ""; continue; }
    cell += char;
  }
  cells.push(cell.trim());
  return cells;
}

/* ------------------------------------------------------------------ blocks */

function indentOf(line) {
  const lead = /^[ \t]*/.exec(line)[0];
  return lead.replace(/\t/g, "    ").length;
}

function renderBlocks(text, spans) {
  const lines = text.split("\n");
  const out = [];
  let paragraph = [];
  let quote = [];           // accumulated blockquote lines
  let code = null;          // accumulated code-block lines
  let codeLanguage = "";
  const lists = [];         // open lists, outermost first: { tag, indent }; each has an open <li>

  // Closing tags attach to the element they close, so "<li>one</li>" stays one piece.
  const append = (html) => { if (out.length) out[out.length - 1] += html; else out.push(html); };
  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${renderInline(paragraph.join("<br>"))}</p>`);
      paragraph = [];
    }
  };
  const closeList = () => append(`</li></${lists.pop().tag}>`);
  const closeLists = () => { while (lists.length) closeList(); };
  const flushQuote = () => {
    if (quote.length) {
      out.push(`<blockquote>${quote.map((item) => renderInline(escapeHtml(item))).join("<br>")}</blockquote>`);
      quote = [];
    }
  };
  const flushBlocks = () => { flushParagraph(); closeLists(); flushQuote(); };
  const formulaLine = (line) => {
    const match = TOKEN_LINE.exec(line);
    return Boolean(match && spans[Number(match[1])]?.display);
  };
  const nextContent = (from) => {
    for (let index = from; index < lines.length; index += 1) if (lines[index].trim()) return lines[index];
    return null;
  };
  const openList = (tag, number, indent) => {
    out.push(tag === "ol" && number > 1 ? `<ol start="${number}">` : `<${tag}>`);
    lists.push({ tag, indent });
  };

  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    const fence = raw.match(FENCE);
    if (fence) {
      if (code === null) {
        flushBlocks();
        code = []; codeLanguage = escapeHtml(fence[1] || "");
      } else {
        const label = codeLanguage
          ? `<span class="code-lang">${codeLanguage}</span>` : "";
        out.push(`<pre class="asst-code">${label}<code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = null; codeLanguage = "";
      }
      continue;
    }
    if (code !== null) { code.push(raw); continue; }

    const line = raw.trimEnd();
    if (!line.trim()) {
      flushParagraph(); flushQuote();
      // A blank line inside a list keeps it open when the list goes on - another item or
      // an indented continuation - so the numbering does not start again at 1.
      const next = nextContent(index + 1);
      if (lists.length && !(next !== null && (LIST_ITEM.test(next) || indentOf(next) >= 2))) closeLists();
      continue;
    }

    const quoted = line.match(/^\s*>\s?(.*)$/);
    if (quoted) {
      flushParagraph(); closeLists();
      if (quoted[1].trim()) quote.push(quoted[1]);
      continue;
    }
    flushQuote();

    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      flushParagraph(); closeLists();
      const level = Math.min(6, heading[1].length + 2);   // "#" renders as <h3>
      out.push(`<h${level}>${renderInline(escapeHtml(heading[2]))}</h${level}>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushParagraph(); closeLists();
      out.push("<hr>");
      continue;
    }

    // A pipe table: a header row with at least two cells, committed by a separator row
    // on the next line. Each cell is escaped before inline rendering, like any text.
    if (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])
        && splitTableRow(line).length >= 2) {
      flushParagraph(); closeLists();
      const headers = splitTableRow(line);
      const cell = (tag, value) => `<${tag}>${renderInline(escapeHtml(value)).replaceAll(CELL_BREAK, "<br>")}</${tag}>`;
      const bordered = rowClosed(line.trim()) && line.trim().startsWith("|");
      const rows = [];
      let next = index + 2;
      for (; next < lines.length; next += 1) {
        let candidate = lines[next].trimEnd();
        if (!candidate.trim() || !candidate.includes("|")) break;
        const broken = bordered ? joinBrokenRow(lines, next, headers.length) : null;
        if (broken) {
          candidate = broken.row;
          next = broken.last;
        }
        const cells = splitTableRow(candidate);
        rows.push(`<tr>${headers.map((_h, column) => cell("td", cells[column] ?? "")).join("")}</tr>`);
      }
      out.push(
        `<table><thead><tr>${headers.map((header) => cell("th", header)).join("")}</tr></thead>`
        + (rows.length ? `<tbody>${rows.join("")}</tbody>` : "") + "</table>");
      index = next - 1;
      continue;
    }

    const item = line.match(LIST_ITEM);
    if (item) {
      flushParagraph();
      const indent = indentOf(item[1]);
      const tag = item[2] === undefined ? "ul" : "ol";
      const number = item[2] === undefined ? 1 : Number(item[2]);
      while (lists.length && indent < lists[lists.length - 1].indent) closeList();
      const top = lists[lists.length - 1];
      if (!top) {
        openList(tag, number, indent);
      } else if (indent >= top.indent + 2 && lists.length < MAX_LIST_DEPTH) {
        openList(tag, number, indent);          // nested inside the open item
      } else if (top.tag === tag) {
        append("</li>");                        // the next item of the same list
      } else {
        closeList();                            // another kind of list at this level
        openList(tag, number, indent);
      }
      out.push(`<li>${renderInline(escapeHtml(item[3]))}`);
      continue;
    }
    // A continuation of the open item: an indented line, or a formula on a line of its own
    // between two steps (a formula line used to end the list and restart it at "1.").
    if (lists.length && (formulaLine(line) || indentOf(line) >= 2)) {
      if (!formulaLine(line)) {
        while (lists.length > 1 && indentOf(line) < lists[lists.length - 1].indent + 2) closeList();
      }
      append(`<br>${renderInline(escapeHtml(line.trim()))}`);
      continue;
    }
    closeLists();
    paragraph.push(escapeHtml(line));
  }
  if (code !== null) {
    // An unterminated fence: render what there is rather than dropping it.
    out.push(`<pre class="asst-code"><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  }
  flushBlocks();
  return out.join("\n");
}

/* ----------------------------------------------------------------- restore */

function restoreTokens(html, spans, links) {
  return html
    .replace(LINK_TOKEN, (_m, index) => {
      const link = links[Number(index)];
      return link ? `<a href="${escapeAttr(link.url)}" target="_blank" rel="noopener noreferrer nofollow">`
        + `${escapeHtml(link.label)}</a>` : "";
    })
    .replace(MATH_TOKEN, (_m, index) => {
      const span = spans[Number(index)];
      // The escaped source is what shows until math.js typesets the slot (or if it cannot).
      return span ? `<span class="ln-math-slot" data-display="${span.display ? 1 : 0}" `
        + `data-tex="${escapeAttr(span.tex)}">${escapeHtml(span.raw)}</span>` : "";
    })
    // An escaped dollar stays a dollar: in its own element, so the text pass in math.js
    // can never pair it with another one.
    .replaceAll(LITERAL_DOLLAR, '<span class="ln-dollar">$</span>');
}

function renderMarkdown(source) {
  const { text, spans } = protectMath(source);
  const linked = protectLinks(text);
  return restoreTokens(renderBlocks(linked.text, spans), spans, linked.links);
}

/* The tutor chat's tap-answer convention: a reply may end with one final line
 * "CHIPS: first | second | third" naming 2-3 tap answers for its question. The line is
 * stripped from the shown (and spoken) text; a reply without one degrades to no chips.
 * Only a trailing line counts - "CHIPS:" mid-text stays ordinary text. */
function parseChips(reply) {
  const text = String(reply || "");
  const match = text.match(/\n\s*CHIPS:\s*(.+?)\s*$/);
  if (!match) return { text, chips: [] };
  const chips = match[1].split("|").map((part) => part.trim()).filter(Boolean).slice(0, 3);
  return { text: text.slice(0, match.index).trimEnd(), chips };
}

export { renderInline, renderMarkdown, parseChips };

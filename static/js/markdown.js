/* Escape-first markdown renderer shared by the assistant and the lesson tutor chat.
 *
 * Model output is untrusted text. It is escaped first and only then given the small
 * set of tags this file introduces itself, so nothing the model writes can become
 * markup. The renderer is deliberately tiny for that reason: every construct it
 * supports is one it builds, not one it passes through.
 * Security property locked by tests/js/assistant-render.test.mjs.
 */
import { escapeHtml } from "./dom.js";

function renderInline(text) {
  // `text` is already escaped. Each replacement introduces only tags built here.
  return text
    .replace(/`([^`\n]+)`/g, (_m, code) => `<code>${code}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, (_m, bold) => `<strong>${bold}</strong>`)
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, (_m, lead, italic) => `${lead}<em>${italic}</em>`)
    // Only http(s) becomes a link, and only after escaping, so a "javascript:" URL
    // cannot be produced here. Trailing punctuation is left outside the link.
    .replace(/\bhttps?:\/\/[^\s<]+[^\s<.,;:!?)\]]/g, (url) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer nofollow">${url}</a>`);
}

// A pipe-table separator row like "|---|:---:|" - only pipes, dashes, colons and
// spaces, with at least one real dash run. It is what commits the line above to
// being a table header; a lone "|" inside prose never starts a table.
function isTableSeparator(line) {
  return line.includes("-") && /^\s*\|?[\s:|-]+\|?\s*$/.test(line) && /-{2,}/.test(line);
}

function splitTableRow(line) {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|")
    .map((cell) => cell.trim());
}

function renderMarkdown(source) {
  const lines = String(source || "").split("\n");
  const out = [];
  let paragraph = [];
  let list = null;          // "ul" | "ol"
  let quote = [];           // accumulated blockquote lines
  let code = null;          // accumulated code-block lines
  let codeLanguage = "";

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${renderInline(paragraph.join("<br>"))}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };
  const flushQuote = () => {
    if (quote.length) {
      out.push(`<blockquote>${quote.map((item) => renderInline(escapeHtml(item))).join("<br>")}</blockquote>`);
      quote = [];
    }
  };
  const flushBlocks = () => { flushParagraph(); flushList(); flushQuote(); };

  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    const fence = raw.match(/^\s*```(\w*)\s*$/);
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
    if (!line.trim()) { flushBlocks(); continue; }

    const quoted = line.match(/^\s*>\s?(.*)$/);
    if (quoted) {
      flushParagraph(); flushList();
      if (quoted[1].trim()) quote.push(quoted[1]);
      continue;
    }
    flushQuote();

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushParagraph(); flushList();
      const level = Math.min(6, heading[1].length + 2);   // "#" renders as <h3>
      out.push(`<h${level}>${renderInline(escapeHtml(heading[2]))}</h${level}>`);
      continue;
    }
    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flushParagraph(); flushList();
      out.push("<hr>");
      continue;
    }

    // A pipe table: a header row with at least two cells, committed by a separator row
    // on the next line. Each cell is escaped before inline rendering, like any text.
    if (line.includes("|") && index + 1 < lines.length && isTableSeparator(lines[index + 1])
        && splitTableRow(line).length >= 2) {
      flushParagraph(); flushList();
      const headers = splitTableRow(line);
      const cell = (tag, value) => `<${tag}>${renderInline(escapeHtml(value))}</${tag}>`;
      const rows = [];
      let next = index + 2;
      for (; next < lines.length; next += 1) {
        const candidate = lines[next].trimEnd();
        if (!candidate.trim() || !candidate.includes("|")) break;
        const cells = splitTableRow(candidate);
        rows.push(`<tr>${headers.map((_h, column) => cell("td", cells[column] ?? "")).join("")}</tr>`);
      }
      out.push(
        `<table><thead><tr>${headers.map((header) => cell("th", header)).join("")}</tr></thead>`
        + (rows.length ? `<tbody>${rows.join("")}</tbody>` : "") + "</table>");
      index = next - 1;
      continue;
    }

    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const wanted = bullet ? "ul" : "ol";
      if (list !== wanted) { flushList(); list = wanted; out.push(`<${wanted}>`); }
      out.push(`<li>${renderInline(escapeHtml((bullet || numbered)[1]))}</li>`);
      continue;
    }
    flushList();
    paragraph.push(escapeHtml(line));
  }
  if (code !== null) {
    // An unterminated fence: render what there is rather than dropping it.
    out.push(`<pre class="asst-code"><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  }
  flushBlocks();
  return out.join("\n");
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

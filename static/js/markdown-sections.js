/* Readable chat answers: the sections an answer already has, shown as sections.
 *
 * The model writes its answers with labels - "**Antwort**", "**Beispiel**", "### Formel",
 * "**Check-Frage**" - and the plain renderer showed them as one long column of paragraphs.
 * This pure pass takes the renderer's finished blocks (markdown.js), recognises a label
 * block - a heading, or a paragraph that starts with a bold label - and groups it with the
 * blocks that follow into a <section class="md-section md-kind-…"> with a small icon. A
 * long answer keeps its first sections and every answer, result, warning, exercise and
 * check question open; other later sections fold into <details> the student can open.
 *
 * Nothing new can become markup: the blocks are the renderer's own escaped HTML, labels are
 * matched on their text, and the icons and class names are fixed strings here. An answer
 * with fewer than two recognised labels is returned unchanged.
 */

// kind: [icon, words a label starts with (lower case; German, English, French, Spanish)]
export const SECTION_KINDS = {
  answer: ["🧠", ["antwort", "kurze antwort", "kurz gesagt", "erklärung", "answer", "short answer", "explanation", "réponse", "explication", "respuesta", "explicación"]],
  example: ["💡", ["beispiel", "beispielrechnung", "rechenbeispiel", "example", "worked example", "exemple", "ejemplo"]],
  formula: ["⚡", ["formel", "formeln", "formula", "formulas", "formule", "formules", "fórmula", "fórmulas"]],
  steps: ["🧮", ["rechnung", "rechenweg", "schritt für schritt", "lösungsweg", "calculation", "step by step", "steps", "calcul", "étapes", "cálculo", "pasos"]],
  result: ["✅", ["ergebnis", "endergebnis", "lösung", "result", "final answer", "solution", "résultat", "resultado", "solución"]],
  tip: ["💡", ["tipp", "tipps", "hinweis", "merktipp", "tip", "tips", "hint", "astuce", "conseil", "consejo", "pista"]],
  remember: ["📌", ["merke", "merksatz", "definition", "wichtig", "zusammenfassung", "kurzüberblick", "remember", "key point", "important", "summary", "à retenir", "définition", "résumé", "recuerda", "definición", "resumen"]],
  warning: ["⚠️", ["achtung", "vorsicht", "typische fehler", "häufige fehler", "fehler", "warning", "common mistake", "common mistakes", "attention", "erreurs", "cuidado", "errores"]],
  exercise: ["📝", ["übung", "unabhängige übung", "aufgabe", "übungsaufgabe", "exercise", "practice", "try it", "exercice", "ejercicio", "práctica"]],
  check: ["❓", ["check-frage", "check", "kontrollfrage", "mini-check", "quiz", "frage an dich", "question", "check question", "vérifie", "pregunta"]],
  exam: ["🎓", ["prüfung", "klassenarbeit", "klausur", "prüfungstipp", "exam", "test tip", "examen", "contrôle"]],
  why: ["🎯", ["warum das wichtig ist", "warum ist das wichtig", "wozu", "why it matters", "why this matters", "pourquoi", "por qué"]],
};
// Never folded away: what the student asked for and what they must act on.
const ALWAYS_OPEN = new Set(["answer", "result", "warning", "exercise", "check", "exam"]);
export const LONG_ANSWER_CHARACTERS = 1800;
export const OPEN_SECTIONS = 2;

const HEADING = /^<h([1-6])>([\s\S]*)<\/h\1>$/;
const LABEL_PARAGRAPH = /^<p><strong>([^<]{1,60})<\/strong>\s*:?\s*(?:<br>([\s\S]*))?<\/p>$/;
const TAGS = /<[^>]*>/g;

function plain(html) {
  return String(html || "").replace(TAGS, "").replace(/&amp;/g, "&").trim();
}

/** The kind of a label text, or "" when it is not one of the known labels. */
export function labelKind(text) {
  // "Check‑Frage" with a non-breaking hyphen (gpt-oss writes it) is "check-frage".
  const label = String(text || "").toLowerCase().replace(/[\u2010-\u2015\u2212]/gu, "-").replace(/[:：!?.\s]+$/u, "")
    .replace(/^[\p{Extended_Pictographic}️\s#*_]+/u, "").trim();
  if (!label || label.length > 48) return "";
  for (const [kind, [, words]] of Object.entries(SECTION_KINDS)) {
    if (words.some((word) => label === word || label.startsWith(`${word} `) || label.startsWith(`${word}:`)
        || label.startsWith(`${word} (`) || label.startsWith(`${word} –`) || label.startsWith(`${word} -`))) {
      return kind;
    }
  }
  return "";
}

/** {label (HTML), rest (HTML after the label, or "")} when the block starts a section. */
export function sectionStart(block) {
  const heading = HEADING.exec(block);
  if (heading) return { label: heading[2], rest: "" };
  const paragraph = LABEL_PARAGRAPH.exec(block);
  if (paragraph) return { label: paragraph[1], rest: paragraph[2] ? `<p>${paragraph[2]}</p>` : "" };
  return null;
}

/** The renderer's blocks grouped into sections (see the module comment). */
export function sectionize(blocks) {
  const items = blocks.map((block) => ({ block, start: sectionStart(block) }));
  const recognised = items.filter((item) => item.start && labelKind(plain(item.start.label))).length;
  if (recognised < 2) return blocks;
  const intro = [];
  const sections = [];
  for (const item of items) {
    if (item.block === "<hr>") continue;                 // the cards separate the sections now
    if (item.start) {
      const kind = labelKind(plain(item.start.label)) || "plain";
      sections.push({ kind, label: item.start.label, body: item.start.rest ? [item.start.rest] : [] });
    } else if (sections.length) {
      sections[sections.length - 1].body.push(item.block);
    } else {
      intro.push(item.block);
    }
  }
  const total = blocks.reduce((sum, block) => sum + plain(block).length, 0);
  const long = total > LONG_ANSWER_CHARACTERS && sections.length > OPEN_SECTIONS + 1;
  const html = sections.map((section, index) => {
    const icon = (SECTION_KINDS[section.kind] || ["•"])[0];
    const title = `<span class="md-section-icon" aria-hidden="true">${icon}</span><span class="md-section-label">${section.label}</span>`;
    const body = section.body.join("\n");
    if (long && index >= OPEN_SECTIONS && !ALWAYS_OPEN.has(section.kind)) {
      return `<details class="md-section md-kind-${section.kind}"><summary class="md-section-title">${title}</summary>${body}</details>`;
    }
    return `<section class="md-section md-kind-${section.kind}"><div class="md-section-title">${title}</div>${body}</section>`;
  });
  return [...intro, ...html];
}

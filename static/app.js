import {escapeHtml} from "./js/dom.js";
import {markFormulaScrollers, renderMath} from "./js/math.js";
import {parseChips, renderMarkdown} from "./js/markdown.js";
import {highlightCode} from "./js/code-highlight.js";
import {announceXp} from "./js/xp.js";
import {bindDiagnosisDetails, diagnosisMarkup} from "./js/diagnosis.js";
import {selectedLanguage, t} from "./js/i18n.js";
import {answerSymbols} from "./js/symbols.js";
import {createAttachmentTray, normalizeImage, photoNotesNode, sentPhotosNode, uploadImage} from "./js/image-attach.js";

const uploadView = document.querySelector("#uploadView");
const lessonView = document.querySelector("#lessonView");

/* Typeset the formulas in `root`; a display formula wider than the column then becomes a
 * named, focusable region with an edge cue (math.js markFormulaScrollers), as in the chat -
 * and again when the width changes (a phone turned). */
function renderFormulas(root) {
  return renderMath(root).then(() => markFormulaScrollers(root, t("asstScrollFormula")));
}
let formulaScrollFrame = 0;
window.addEventListener("resize", () => {
  window.cancelAnimationFrame(formulaScrollFrame);
  formulaScrollFrame = window.requestAnimationFrame(() => {
    for (const root of [lessonView, document.querySelector("#questionCard")]) {
      markFormulaScrollers(root, t("asstScrollFormula"));
    }
  });
});
const uploadForm = document.querySelector("#uploadForm");
const imageInput = document.querySelector("#images");
const previews = document.querySelector("#previews");
const studyGoal = document.querySelector("#studyGoal");
const answerForm = document.querySelector("#answerForm");
let sessionId = null;
let nextQuestion = null;
let currentQuestion = null;
let questionResults = [];
let testTotal = 15;
let testRange = { minimum: 3, maximum: 15, target: 80 };
let lessonConcepts = [];
let latestKnowledge = null;   // the knowledge gate's latest view, from the last answer
let hintUsed = false;
let answerRetryCount = 0;
// "photo" once the written answer was filled in from a photo of the student's working, so
// the grader judges the steps; reset with every new question.
let answerSource = "typed";
const photoAnswers = answerForm?.dataset.photoAnswers === "1";
let currentSubject = "Mathematics";
// Which subjects get the on-screen symbol keyboard when typing an answer. Rendering
// is no longer subject-gated - math.js decides per span - but offering a student a
// palette of integrals in a history lesson still makes no sense.
const MATH_SUBJECTS = new Set(["Mathematics", "Physics", "Chemistry"]);
function applyTranslations() {
  // The interface language (and RTL direction) is set authoritatively by the server on
  // <html> in base.html; never override it here from the content language.
  document.querySelectorAll("[data-i18n]").forEach(node => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(node => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  const exampleAnswer = document.querySelector("#exampleAnswer");
  if (exampleAnswer?.dataset.answer) exampleAnswer.textContent = `${t("answer")}: ${exampleAnswer.dataset.answer}`;
  if (currentQuestion && !document.querySelector("#testProgress").classList.contains("hidden")) {
    updateProgress(Math.min(questionResults.length + 1, testTotal));
  }
}

applyTranslations();

function renderMastery(items = []) {
  const list = document.querySelector("#masteryList");
  if (!items.length) {
    list.innerHTML = `<div class="mastery-item"><b>${t("noMastery")}</b></div>`;
    return;
  }
  list.innerHTML = items.map((item, index) => {
    const score = item.attempts ? item.average_score : 0;
    const label = item.attempts ? `${score}%` : t("newLabel");
    return `<div class="mastery-item ${index === 0 ? "weak" : ""}"><b>${escapeHtml(item.concept)}</b><span>${label}</span><div class="mastery-bar"><i style="width:${Math.max(score, 4)}%"></i></div></div>`;
  }).join("");
}

function showError(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 4500);
}

// The server's photo limit for one lesson (what the vision model can read in one request).
const photoLimit = Number(imageInput?.dataset.maxPhotos) || 2;

function previewFiles() {
  previews.innerHTML = "";
  [...imageInput.files].slice(0, photoLimit).forEach(file => {
    const image = document.createElement("img");
    image.src = URL.createObjectURL(file);
    previews.appendChild(image);
  });
}

imageInput.addEventListener("change", previewFiles);
document.querySelectorAll(".prompt-ideas button").forEach(button => button.addEventListener("click", () => {
  studyGoal.value = selectedLanguage === "German" ? button.dataset.promptDe : button.dataset.promptEn;
  const subjectInput = document.querySelector(`input[name="subject"][value="${button.dataset.subject}"]`);
  if (subjectInput) subjectInput.checked = true;
  studyGoal.focus();
}));

// The test has no fixed length any more: it runs between testRange.minimum and
// testRange.maximum questions and ends when every concept is known. So the bar shows
// knowledge towards the target, and the steps show the concepts, not question numbers.
function updateProgress(questionNumber, knowledge = latestKnowledge) {
  const concepts = knowledge?.concepts || lessonConcepts.map(name => ({ concept: name, knowledge: 0, status: "untested", known: false }));
  const target = knowledge?.target || testRange.target;
  const average = concepts.length ? concepts.reduce((sum, item) => sum + Math.min(item.knowledge, target), 0) / concepts.length : 0;
  const percent = Math.min(100, Math.round((average / target) * 100));
  const where = knowledge
    ? t("conceptsAtTarget", { known: knowledge.known, total: knowledge.total, target })
    : t("upToQuestions", { max: testRange.maximum });
  document.querySelector("#progressLabel").textContent = `${t("question")} ${questionNumber} · ${where}`;
  document.querySelector("#progressPercent").textContent = `${percent}%`;
  document.querySelector("#progressFill").style.width = `${percent}%`;
  // From the minimum onwards the student may end the test; the summary then says
  // honestly what the answers so far show.
  const finish = document.querySelector("#finishTest");
  if (finish) finish.hidden = !(knowledge && knowledge.answered >= knowledge.minimum && !knowledge.complete);
  document.querySelector("#progressSteps").innerHTML = concepts.map(item => {
    const label = item.status === "known" ? t("knowledgeKnown") : item.status === "learning" ? `${item.knowledge}%` : t("knowledgeUntested");
    return `<span class="concept-chip ${item.status}" title="${escapeHtml(item.concept)}">${escapeHtml(item.concept)} · ${escapeHtml(label)}</span>`;
  }).join("");
}

function optionMarkup(option, type) {
  return `<label class="answer-option"><input type="${type}" name="answerOption" value="${escapeHtml(option.id)}"><span>${escapeHtml(option.label)}</span></label>`;
}

function enableOrdering() {
  const list = document.querySelector("#orderingList");
  let dragged = null;
  list.querySelectorAll(".ordering-item").forEach(item => {
    item.addEventListener("dragstart", () => { dragged = item; item.classList.add("dragging"); });
    item.addEventListener("dragend", () => { item.classList.remove("dragging"); dragged = null; });
    item.addEventListener("dragover", event => {
      event.preventDefault();
      if (!dragged || dragged === item) return;
      const box = item.getBoundingClientRect();
      list.insertBefore(dragged, event.clientY < box.top + box.height / 2 ? item : item.nextSibling);
    });
  });
  list.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    const item = button.closest(".ordering-item");
    if (button.dataset.move === "up" && item.previousElementSibling) list.insertBefore(item, item.previousElementSibling);
    if (button.dataset.move === "down" && item.nextElementSibling) list.insertBefore(item.nextElementSibling, item);
  });
}

function symbolKeyboardMarkup() {
  const label = t("symbols");
  return `<div class="symbol-keyboard" aria-label="${label}"><small>${label}</small><div>${answerSymbols.map(([visible, value, title]) => `<button type="button" data-symbol="${value}" title="${title}">${visible}</button>`).join("")}</div></div>`;
}

function enableSymbolKeyboard() {
  const textarea = document.querySelector("#writtenAnswer");
  const keyboard = document.querySelector(".symbol-keyboard");
  if (!textarea || !keyboard) return;
  keyboard.addEventListener("click", event => {
    const button = event.target.closest("button[data-symbol]");
    if (!button) return;
    const symbol = button.dataset.symbol;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    textarea.setRangeText(symbol, start, end, "end");
    if (symbol.endsWith("()")) textarea.setSelectionRange(start + symbol.length - 1, start + symbol.length - 1);
    textarea.focus();
  });
}

const CLOSED_TYPES = new Set(["multiple_choice", "checkboxes", "dropdown", "ordering"]);

function renderQuestion(question, { retry = false } = {}) {
  // The server does the same; this is the belt to its braces: no choices, no ordering task.
  if (CLOSED_TYPES.has(question.type) && !(Array.isArray(question.options) && question.options.length >= 2)) {
    question = { ...question, type: "text", options: [] };
  }
  currentQuestion = question;
  if (retry) {
    // "Try again": the same question once more. A hint already seen stays counted, and the
    // server learns this is a second attempt.
    answerRetryCount += 1;
  } else {
    hintUsed = false;
    answerRetryCount = 0;
  }
  answerSource = "typed";
  const neutralConfidence = answerForm.querySelector('input[name="responseConfidence"][value="50"]');
  if (neutralConfidence) neutralConfidence.checked = true;
  const questionNumber = questionResults.length + 1;
  document.querySelector("#questionNumber").textContent = String(questionNumber).padStart(2, "0");
  document.querySelector("#questionPrompt").textContent = question.prompt;
  document.querySelector("#difficulty").textContent = `${t("level")} ${question.difficulty}`;
  // The hint text arrives only when asked for (POST /api/hint), so the server knows.
  document.querySelector("#hint").textContent = question.hint || "";
  document.querySelector("#hint").classList.add("hidden");
  document.querySelector("#hintButton").classList.toggle("hidden", question.has_hint === false && !question.hint);
  document.querySelector("#hintListenControl")?.classList.add("hidden");
  // "Try again" keeps the reteach in view while the student answers again.
  if (!retry) document.querySelector("#feedback").className = "feedback hidden";
  if (!retry) document.querySelector("#feedbackListenControl")?.classList.add("hidden");
  document.querySelector("#questionCard").className = "content-card question-card";
  const control = document.querySelector("#answerControl");
  const options = question.options || [];
  // Pictures come from the student's own pages; the server verified every id and built
  // the URL, so nothing here can be pointed somewhere else.
  const media = Array.isArray(question.media) ? question.media : [];
  const figures = document.querySelector("#questionMedia");
  if (figures) {
    const showAbove = media.length && question.type !== "photo_ordering";
    figures.innerHTML = showAbove ? media.map(item =>
      `<figure class="question-photo"><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.labels || t("photoFromYourPage"))}" loading="lazy"></figure>`).join("") : "";
    figures.classList.toggle("hidden", !showAbove);
  }
  if (question.type === "multiple_choice") {
    control.innerHTML = `<p>${t("selectAnswer")}</p>${options.map(option => optionMarkup(option, "radio")).join("")}`;
  } else if (question.type === "checkboxes") {
    control.innerHTML = `<p>${t("selectAll")}</p>${options.map(option => optionMarkup(option, "checkbox")).join("")}`;
  } else if (question.type === "dropdown") {
    control.innerHTML = `<select id="dropdownAnswer"><option value="">${t("selectAnswer")}</option>${options.map(option => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.label)}</option>`).join("")}</select>`;
  } else if (question.type === "photo_ordering") {
    // Tiles are the student's own scanned diagrams. Same control as text ordering, so
    // dragging and the arrow-button fallback (touch, keyboard) come with it.
    control.innerHTML = `<p>${t("arrangeOrder")}</p><div id="orderingList" class="ordering-list ordering-photos">${media.map((item, index) => `<div class="ordering-item ordering-photo" draggable="true" data-id="${escapeHtml(String(item.block_id))}"><span class="drag-handle">⋮⋮</span><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.labels || `${t("photoFromYourPage")} ${index + 1}`)}" loading="lazy"><span class="order-buttons"><button type="button" data-move="up" aria-label="${t("moveUp")}">↑</button><button type="button" data-move="down" aria-label="${t("moveDown")}">↓</button></span></div>`).join("")}</div>`;
    enableOrdering();
  } else if (question.type === "ordering") {
    control.innerHTML = `<p>${t("arrangeOrder")}</p><div id="orderingList" class="ordering-list">${options.map(option => `<div class="ordering-item" draggable="true" data-id="${escapeHtml(option.id)}"><span class="drag-handle">⋮⋮</span><b>${escapeHtml(option.label)}</b><span class="order-buttons"><button type="button" data-move="up" aria-label="${t("moveUp")}">↑</button><button type="button" data-move="down" aria-label="${t("moveDown")}">↓</button></span></div>`).join("")}</div>`;
    enableOrdering();
  } else {
    const showSymbols = MATH_SUBJECTS.has(currentSubject);
    control.innerHTML = `<textarea id="writtenAnswer" rows="4" placeholder="${t("answerPlaceholder")}" required></textarea>${showSymbols ? symbolKeyboardMarkup() : ""}${photoAnswers ? answerPhotoMarkup() : ""}`;
    if (showSymbols) enableSymbolKeyboard();
    if (photoAnswers) enableAnswerPhoto();
  }
  document.querySelector("#answerMicrophoneControl")?.classList.toggle("hidden", !document.querySelector("#writtenAnswer"));
  answerForm.classList.remove("hidden");
  answerForm.querySelector(".primary-button").classList.remove("hidden");
  renderFormulas(document.querySelector("#questionCard"));
  updateProgress(questionNumber);
}

function answerPhotoMarkup() {
  return `<div class="answer-photo"><span class="attach-buttons"><label class="attach-button"><input id="answerCamera" class="attach-input" type="file" accept="image/*" capture="environment"><span aria-hidden="true">📷</span> ${escapeHtml(t("imgPhotoOfWorking"))}</label><label class="attach-button"><input id="answerGallery" class="attach-input" type="file" accept="image/*"><span aria-hidden="true">🖼</span> ${escapeHtml(t("imgChoosePhoto"))}</label></span><p id="answerPhotoStatus" class="answer-photo-status" role="status" aria-live="polite"></p><p id="answerPhotoNote" class="answer-photo-note hidden"></p></div>`;
}

/* A photo of handwritten working is read into the answer box - never graded directly. The
 * student sees exactly what was read, with unclear parts marked [?…], fixes it, and sends
 * it like a typed answer; so a misread digit cannot turn a right answer into a wrong one. */
function enableAnswerPhoto() {
  const status = document.querySelector("#answerPhotoStatus");
  const note = document.querySelector("#answerPhotoNote");
  const box = document.querySelector("#writtenAnswer");
  const inputs = [document.querySelector("#answerCamera"), document.querySelector("#answerGallery")];
  const setBusy = busy => inputs.forEach(input => { if (input) input.disabled = busy; });
  inputs.forEach(input => input?.addEventListener("change", async event => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || !sessionId) return;
    setBusy(true);
    note.classList.add("hidden");
    status.textContent = t("imgUploading");
    try {
      const { attachment } = await uploadImage(await normalizeImage(file), "answer", progress => {
        status.textContent = t("imgUploadingPercent", { percent: Math.round(progress * 100) });
      });
      status.textContent = t("imgReadingWorking");
      const response = await fetch("/api/answer/photo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, attachments: [attachment.id] })
      });
      // A proxy error page or a login redirect is not JSON; never show a parser error.
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || t("imgUploadFailed"));
      if (!data.readable || !data.transcription) {
        status.textContent = "";
        note.textContent = t("imgWorkingUnreadable");
        note.classList.remove("hidden");
        return;
      }
      box.value = box.value.trim() ? `${box.value.trim()}\n\n${data.transcription}` : data.transcription;
      answerSource = "photo";
      box.focus();
      const parts = [t("imgCheckWorking")];
      const unclear = (data.unclear || []).map(item => item.alternatives?.length
        ? t("imgUnclearWithOptions", { text: item.text, options: item.alternatives.join(" / ") })
        : `“${item.text}”`);
      if (unclear.length) parts.push(t("imgUnclearParts", { parts: unclear.join(", ") }));
      if (data.clarification_question) parts.push(data.clarification_question);
      if (data.hints?.length) parts.push(data.hints[0]);
      note.textContent = parts.join(" ");
      note.classList.remove("hidden");
      status.textContent = "";
    } catch (error) {
      status.textContent = error.message || t("imgUploadFailed");
    } finally {
      setBusy(false);
    }
  }));
}

function collectAnswer() {
  if (currentQuestion.type === "multiple_choice") return document.querySelector('input[name="answerOption"]:checked')?.value || "";
  if (currentQuestion.type === "checkboxes") return [...document.querySelectorAll('input[name="answerOption"]:checked')].map(input => input.value);
  if (currentQuestion.type === "dropdown") return document.querySelector("#dropdownAnswer").value;
  if (currentQuestion.type === "ordering" || currentQuestion.type === "photo_ordering") return [...document.querySelectorAll("#orderingList .ordering-item")].map(item => item.dataset.id);
  return document.querySelector("#writtenAnswer").value.trim();
}

function renderMedia(lesson) {
  const card = document.querySelector("#mediaCard");
  if (!card) return;
  const videos = Array.isArray(lesson.video_links) ? lesson.video_links : [];
  const images = Array.isArray(lesson.image_media) ? lesson.image_media : [];
  // Studyflix is a German site, so the server only sends that link for German content.
  document.querySelector("#lessonVideos").innerHTML = videos.map(video =>
    `<div class="media-video"><div class="media-video-text"><b>${escapeHtml(video.title)}</b>${video.why ? `<small>${escapeHtml(video.why)}</small>` : ""}</div><span class="media-links"><a href="${escapeHtml(video.youtube)}" target="_blank" rel="noopener noreferrer">▶ YouTube</a>${video.studyflix ? `<a href="${escapeHtml(video.studyflix)}" target="_blank" rel="noopener noreferrer">Studyflix</a>` : ""}</span></div>`
  ).join("");
  // alt describes the picture; the caption says which article it came from. Both used to
  // be the article title, and the caption claimed a licence nobody had checked.
  document.querySelector("#lessonImages").innerHTML = images.map(image =>
    `<figure class="media-image"><img src="${escapeHtml(image.url)}" alt="${escapeHtml(image.alt || image.title)}" loading="lazy" data-media-image><figcaption>${escapeHtml(image.title)}${image.page_url ? ` — <a href="${escapeHtml(image.page_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(image.source || "Wikipedia")}</a>` : ""}</figcaption></figure>`
  ).join("");
  // A picture that cannot load leaves a broken icon under a confident caption, which
  // reads as "here is your illustration" when there is none. Drop the whole figure.
  document.querySelectorAll("#lessonImages [data-media-image]").forEach(img => {
    img.addEventListener("error", () => {
      img.closest("figure")?.remove();
      const left = document.querySelectorAll("#lessonImages figure").length;
      if (!left && !videos.length) card.classList.add("hidden");
    }, { once: true });
  });
  const hasMedia = videos.length > 0 || images.length > 0;
  card.classList.toggle("hidden", !hasMedia);
  if (hasMedia) {
    document.querySelector("#mediaKicker").textContent = t("exploreMore");
    document.querySelector("#mediaHeading").textContent = t("watchAndSee");
  }
}

/* lesson-parts:start
 * A lesson from the teaching engine arrives with `lesson.parts`: the subject's own teaching
 * order (objective, why, explanation, Fachbegriffe, examples, flashcards, guided exercise,
 * mistakes, summary ...). Each part is drawn as one numbered card. Every string in a part is
 * model output, so this block builds the DOM with textContent only - nothing a model writes
 * can become markup, and no part field is ever parsed as HTML. Lessons without parts keep
 * the fixed explanation / worked example / teacher notes cards exactly as before. */
const PART_LABEL_KEYS = {
  objective: "lpKind_objective",
  why: "lpKind_why",
  explanation: "lpKind_explanation",
  terms: "lpKind_terms",
  rule: "lpKind_rule",
  structure: "lpKind_structure",
  example: "lpKind_example",
  worked_example: "lpKind_worked_example",
  experiment: "lpKind_experiment",
  thought_experiment: "lpKind_thought_experiment",
  theory: "lpKind_theory",
  argument: "lpKind_argument",
  perspectives: "lpKind_perspectives",
  timeline: "lpKind_timeline",
  cause_effect: "lpKind_cause_effect",
  process: "lpKind_process",
  diagram: "lpKind_diagram",
  source_analysis: "lpKind_source_analysis",
  model_analysis: "lpKind_model_analysis",
  mistakes: "lpKind_mistakes",
  guided_exercise: "lpKind_guided_exercise",
  summary: "lpKind_summary",
  flashcards: "lpKind_flashcards",
};
// Items of these kinds are a sequence (steps of a process or a worked solution), so they
// are numbered; every other kind lists its items as "label — text".
const ORDERED_PART_KINDS = new Set(["process", "worked_example", "structure"]);
// The <ol> numbers steps itself; models often number them too ("1. 1. Das Problem ...").
// Only a leading "N." / "N)" / "Schritt N:" followed by a space is removed, so "4.5 kg"
// and "2 + 3 = 5" stay intact.
const OWN_NUMBER = /^\s*(?:(?:Schritt|Step|Étape|Paso|Passo|Stap)\s*)?\d{1,2}\s*[.):]\s+/i;
// An experiment's items are labelled in the content language ("Vermutung", "Hypothesis");
// a recognised label gets its step's marker. The label shown is always the item's own.
const EXPERIMENT_STEPS = [
  ["question", /^(research question|question|forschungsfrage|fragestellung|frage|problemstellung|problem|probl[eè]me|pregunta)/],
  ["hypothesis", /^(hypothes[ei]s|hypoth[eè]se|vermutung|hip[oó]tesis)/],
  ["setup", /^(set ?-?up|materials?|equipment|versuchsaufbau|aufbau|materialien|ger[aä]te|mat[eé]riel|dispositif|montage|materiales|montaje)/],
  ["procedure", /^(procedure|method|steps|versuchsdurchf[uü]hrung|durchf[uü]hrung|vorgehen|ablauf|protocole|proc[eé]dure|mode op[eé]ratoire|procedimiento)/],
  ["observation", /^(observations?|beobachtung(en)?|observaci[oó]n(es)?)/],
  ["result", /^(results?|ergebnis(se)?|auswertung|messwerte|r[eé]sultats?|resultados?)/],
  ["explanation", /^(explanation|conclusion|interpretation|erkl[aä]rung|deutung|schlussfolgerung|fazit|explication|explicaci[oó]n|conclusi[oó]n)/],
];
const PART_RENDERERS = {
  terms: flipCardsContent,
  flashcards: flipCardsContent,
  timeline: timelineContent,
  experiment: experimentContent,
  cause_effect: causeEffectContent,
  guided_exercise: guidedExerciseContent,
  mistakes: mistakesContent,
  summary: summaryContent,
};
let partPanelCounter = 0;

function partText(value) {
  return typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
}

function partItems(part) {
  // An item may carry its own origin (a key-terms card whose definition Learnova wrote,
  // among cards from the notes); it is kept only when the server said so explicitly.
  return (Array.isArray(part.items) ? part.items : [])
    .map(item => (item && typeof item === "object"
      ? { label: partText(item.label), text: partText(item.text), ...(typeof item.from_notes === "boolean" ? { from_notes: item.from_notes } : {}) }
      : { label: "", text: partText(item) }))
    .filter(item => item.label || item.text);
}

function usableParts(parts) {
  if (!Array.isArray(parts)) return [];
  return parts.filter(part => {
    if (!part || typeof part !== "object") return false;
    const items = partItems(part);
    if (part.kind === "flashcards") return items.length > 0;
    return Boolean(partText(part.title) || partText(part.body) || items.length || partText(part.answer));
  });
}

function el(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

// "**word**" is the one inline mark drawn, as a <strong> element built here; everything
// else stays literal text (a single "*" is a multiplication sign in maths lessons).
function appendRichText(parent, text) {
  String(text).split(/\*\*([^*\n]+)\*\*/).forEach((piece, index) => {
    if (piece) parent.appendChild(index % 2 ? el("strong", "", piece) : document.createTextNode(piece));
  });
}

// Body text keeps its line breaks and indentation (white-space: pre-wrap), because bodies
// carry code and traces where indentation is meaning; ``` fences become <pre><code>, which
// renderMath leaves alone. Paragraphs split on blank lines.
function bodyContent(text) {
  const body = el("div", "lp-body");
  String(text || "").split(/^[ \t]*```[\w+#-]*[ \t]*$/m).forEach((chunk, index) => {
    if (index % 2) {
      const pre = el("pre", "lp-code");
      pre.appendChild(el("code", "", chunk.replace(/^\n/, "").replace(/\n$/, "")));
      body.appendChild(pre);
      return;
    }
    chunk.split(/\n[ \t]*\n/).forEach(paragraph => {
      const clean = paragraph.replace(/^\n+/, "").replace(/\s+$/, "");
      if (!clean.trim()) return;
      const line = el("p", "lp-text");
      appendRichText(line, clean);
      body.appendChild(line);
    });
  });
  return body.childNodes.length ? body : null;
}

function itemNode(item, { ordered = false } = {}) {
  const row = el("li");
  const text = ordered ? item.text.replace(OWN_NUMBER, "") : item.text;
  if (item.label) row.appendChild(el("b", "lp-item-label", item.label));
  if (item.label && text) row.appendChild(el("span", "lp-item-sep", " — "));
  if (text) row.appendChild(el("span", "lp-item-text", text));
  return row;
}

function itemList(items, { ordered = false, className = "lp-items" } = {}) {
  const list = el(ordered ? "ol" : "ul", className);
  items.forEach(item => list.appendChild(itemNode(item, { ordered })));
  return list;
}

function originBadge(fromNotes) {
  return el("span", `lp-origin ${fromNotes ? "lp-origin-notes" : "lp-origin-added"}`,
    fromNotes ? t("lpFromNotes") : t("lpAddedByLearnova"));
}

function sourcePages(part) {
  // The server names the lesson's own pages ("Ethik-2.pdf p. 1"): in a topic from two files
  // a bare "p. 1" would be either. Older lessons only have the numbers.
  const labels = (Array.isArray(part.source_labels) ? part.source_labels : []).map(partText).filter(Boolean);
  if (labels.length) return [...new Set(labels)].map(label => el("span", "lp-page", label));
  const pages = (Array.isArray(part.source_pages) ? part.source_pages : [])
    .map(Number).filter(page => Number.isInteger(page) && page > 0);
  return [...new Set(pages)].map(page => el("span", "lp-page", t("lpPage", { page })));
}

// A link the server built (the project set's study page). Only a same-site path is used:
// "//host" and "/\host" would leave the site, anything else is not a path at all. The URL
// parser drops tabs and line breaks, so "/<tab>/host" became "//host": any whitespace or
// control character rejects the value, and the resolved link must stay on this origin.
function internalUrl(value) {
  const url = partText(value);
  if (!url.startsWith("/") || url.startsWith("//") || url.includes("\\") || /[\s\u0000-\u001f\u007f]/.test(url)) return "";
  try {
    return new URL(url, window.location.origin).origin === window.location.origin ? url : "";
  } catch (_error) {
    return "";
  }
}

/* Fachbegriffe and flashcards: tap-to-flip cards. Both faces stay in the DOM (one grid
 * cell, so the card is as tall as its longer face) and are read by screen readers and
 * read-aloud; the flip is a visual aid, never hidden content. */
function flipCardsContent(part, items) {
  const fromNotes = part.from_notes === true;
  const fragment = document.createDocumentFragment();
  const intro = bodyContent(part.body);
  if (intro) fragment.appendChild(intro);
  const grid = el("ul", "lp-flip-grid");
  items.forEach(item => {
    const cell = el("li");
    if (!item.label || !item.text) {
      cell.appendChild(el("div", "lp-flip-static", item.label || item.text));
      grid.appendChild(cell);
      return;
    }
    const card = el("button", "lp-flip");
    card.type = "button";
    card.setAttribute("aria-pressed", "false");
    const inner = el("span", "lp-flip-inner");
    const front = el("span", "lp-flip-face lp-flip-front");
    front.appendChild(el("span", "lp-flip-label", item.label));
    const cue = el("span", "lp-flip-cue", t("lpTapToFlip"));
    cue.setAttribute("aria-hidden", "true");
    front.appendChild(cue);
    const back = el("span", "lp-flip-face lp-flip-back");
    back.appendChild(el("span", "lp-flip-text", item.text));
    // The part's origin is announced once in its header; on each card it is a visual cue,
    // and a card with its own origin (a definition Learnova wrote) shows that one.
    const itemFromNotes = typeof item.from_notes === "boolean" ? item.from_notes : fromNotes;
    const badge = el("span", `lp-badge ${itemFromNotes ? "lp-badge-notes" : "lp-badge-added"}`,
      itemFromNotes ? t("lpFromNotes") : t("lpAddedExplanation"));
    badge.setAttribute("aria-hidden", "true");
    back.appendChild(badge);
    inner.append(front, back);
    card.appendChild(inner);
    cell.appendChild(card);
    grid.appendChild(cell);
  });
  fragment.appendChild(grid);
  const studyUrl = internalUrl(part.study_url);
  if (studyUrl) {
    const link = el("a", "lp-study-link", t("lpStudyAllTerms"));
    link.href = studyUrl;
    const arrow = el("span", "", "→");
    arrow.setAttribute("aria-hidden", "true");
    link.appendChild(arrow);
    fragment.appendChild(link);
  }
  return fragment;
}

function timelineContent(part, items) {
  const fragment = document.createDocumentFragment();
  const intro = bodyContent(part.body);
  if (intro) fragment.appendChild(intro);
  if (items.length) {
    const list = el("ol", "lp-timeline");
    items.forEach(item => {
      const row = el("li");
      if (item.label) row.appendChild(el("span", "lp-timeline-when", item.label));
      if (item.text) row.appendChild(el("span", "lp-timeline-what", item.text));
      list.appendChild(row);
    });
    fragment.appendChild(list);
  }
  return fragment;
}

function experimentStep(label) {
  const key = label.toLocaleLowerCase().replace(/[:\s]+$/, "");
  return EXPERIMENT_STEPS.find(([, pattern]) => pattern.test(key))?.[0] || "";
}

function experimentContent(part, items) {
  const fragment = document.createDocumentFragment();
  const intro = bodyContent(part.body);
  if (intro) fragment.appendChild(intro);
  if (items.length) {
    const list = el("ol", "lp-steps");
    items.forEach(item => {
      const row = el("li", "lp-step");
      const step = item.label ? experimentStep(item.label) : "";
      if (step) row.dataset.step = step;
      if (item.label) row.appendChild(el("b", "lp-step-label", item.label));
      if (item.text) row.appendChild(el("span", "lp-step-text", item.text.replace(OWN_NUMBER, "")));
      list.appendChild(row);
    });
    fragment.appendChild(list);
  }
  return fragment;
}

function causeEffectContent(part, items) {
  const fragment = document.createDocumentFragment();
  const intro = bodyContent(part.body);
  if (intro) fragment.appendChild(intro);
  if (items.length) {
    const list = el("ul", "lp-causes");
    items.forEach(item => {
      const row = el("li", "lp-cause-row");
      if (item.label && item.text) {
        row.appendChild(el("span", "lp-cause", item.label));
        const arrow = el("span", "lp-cause-arrow", "→");
        arrow.setAttribute("aria-hidden", "true");
        // Spoken and read by screen readers in place of the arrow: "war leads to hunger".
        row.append(arrow, el("span", "lp-sr", ` ${t("lpLeadsTo")} `), el("span", "lp-effect", item.text));
      } else {
        row.classList.add("lp-cause-single");
        row.appendChild(el("span", "lp-effect", item.label || item.text));
      }
      list.appendChild(row);
    });
    fragment.appendChild(list);
  }
  return fragment;
}

// A self-check, never graded: the hint (items) and the solution (answer) open on a tap.
// Closed panels carry aria-hidden as well as hidden, because read-aloud skips aria-hidden
// nodes - the solution must not be spoken before the student asks for it.
function revealPanel(className, buttonLabel, hideLabel, content) {
  partPanelCounter += 1;
  const id = `lessonPartPanel${partPanelCounter}`;
  const button = el("button", "lp-reveal", buttonLabel);
  button.type = "button";
  button.setAttribute("aria-expanded", "false");
  button.setAttribute("aria-controls", id);
  button.dataset.showLabel = buttonLabel;
  button.dataset.hideLabel = hideLabel;
  const panel = el("div", `lp-reveal-panel ${className}`);
  panel.id = id;
  panel.hidden = true;
  panel.setAttribute("aria-hidden", "true");
  panel.appendChild(content);
  return { button, panel };
}

function guidedExerciseContent(part, items) {
  const fragment = document.createDocumentFragment();
  const task = bodyContent(part.body);
  if (task) fragment.appendChild(task);
  const answer = partText(part.answer);
  if (!items.length && !answer) return fragment;
  fragment.appendChild(el("p", "lp-try-first", t("lpTryFirst")));
  const actions = el("div", "lp-reveal-actions");
  const panels = [];
  if (items.length) {
    const hint = revealPanel("lp-hint", t("showHint"), t("lpHideHint"), itemList(items));
    actions.appendChild(hint.button);
    panels.push(hint.panel);
  }
  if (answer) {
    const solution = document.createDocumentFragment();
    solution.appendChild(el("b", "lp-solution-label", t("lpSolution")));
    const solutionBody = bodyContent(answer);
    if (solutionBody) solution.appendChild(solutionBody);
    const reveal = revealPanel("lp-solution", t("lpShowSolution"), t("lpHideSolution"), solution);
    actions.appendChild(reveal.button);
    panels.push(reveal.panel);
  }
  fragment.append(actions, ...panels);
  return fragment;
}

function mistakesContent(part, items) {
  const fragment = document.createDocumentFragment();
  const intro = bodyContent(part.body);
  if (intro) fragment.appendChild(intro);
  if (items.length) fragment.appendChild(itemList(items, { className: "lp-items lp-warnings" }));
  return fragment;
}

function summaryContent(part, items) {
  const recap = el("div", "lp-recap");
  const body = bodyContent(part.body);
  if (body) recap.appendChild(body);
  if (items.length) recap.appendChild(itemList(items, { className: "lp-items lp-checklist" }));
  return recap;
}

function genericContent(part, items, kind) {
  const fragment = document.createDocumentFragment();
  const body = bodyContent(part.body);
  if (body) fragment.appendChild(body);
  if (items.length) fragment.appendChild(itemList(items, { ordered: ORDERED_PART_KINDS.has(kind) }));
  // Only a guided exercise hides its answer; anywhere else it is content to read.
  const answer = bodyContent(part.answer);
  if (answer) fragment.appendChild(answer);
  return fragment;
}

function partCard(part, number) {
  const kind = Object.prototype.hasOwnProperty.call(PART_LABEL_KEYS, part.kind) ? part.kind : "explanation";
  const label = t(PART_LABEL_KEYS[kind]);
  const title = partText(part.title);
  const card = el("article", `content-card lp-card lp-kind-${kind}`);
  card.dataset.partKind = kind;
  const badge = el("div", "card-number", String(number).padStart(2, "0"));
  badge.setAttribute("aria-hidden", "true");
  const main = el("div", "wide");
  const meta = el("div", "lp-meta");
  if (title) meta.appendChild(el("span", "card-kicker lp-kicker", label));
  meta.appendChild(originBadge(part.from_notes === true));
  meta.append(...sourcePages(part));
  main.append(meta, el("h2", "lp-title", title || label));
  const render = PART_RENDERERS[kind] || genericContent;
  main.appendChild(render(part, partItems(part), kind));
  const figures = partFigures(part);
  if (figures) main.appendChild(figures);
  card.append(badge, main);
  return card;
}

// Diagrams from the student's own page under a diagram, process or experiment part. The
// server verified each picture and built its URL; only a same-site path is drawn.
function partFigures(part) {
  const media = (Array.isArray(part.media) ? part.media : [])
    .map(item => ({ url: internalUrl(item && item.url), labels: partText(item && item.labels) }))
    .filter(item => item.url);
  if (!media.length) return null;
  const wrap = el("div", "lp-figures");
  media.forEach(item => {
    const figure = el("figure", "lp-figure");
    const image = el("img");
    image.src = item.url;
    image.alt = item.labels || t("photoFromYourPage");
    image.loading = "lazy";
    figure.appendChild(image);
    if (item.labels) figure.appendChild(el("figcaption", "", item.labels));
    wrap.appendChild(figure);
  });
  return wrap;
}

// Read-aloud speaks the list's textContent, and elements built one by one have no text
// between them: a term card was spoken "PflichtNotwendigkeit einer Handlung", a timeline
// row "1724Geburt", a title ran into its body. One space between sibling elements keeps
// the words apart. It draws nothing: the containers are grid/flex (whitespace between
// their children is not rendered) and inline runs collapse it into the existing space.
// Text paragraphs and code keep their own whitespace (pre-wrap / pre), so they are skipped.
function spaceForSpeech(root) {
  root.querySelectorAll("*").forEach(node => {
    if (node.parentElement?.closest(".lp-text, pre")) return;
    if (node.nextSibling?.nodeType === Node.ELEMENT_NODE) node.after(document.createTextNode(" "));
  });
}

/* Draws `lesson.parts` into #lessonParts; returns how many parts were drawn (0 hides it). */
function renderParts(lesson) {
  const container = document.querySelector("#lessonParts");
  const list = document.querySelector("#lessonPartsList");
  if (!container || !list) return 0;
  const parts = usableParts(lesson.parts);
  list.replaceChildren(...parts.map((part, index) => partCard(part, index + 1)));
  spaceForSpeech(list);
  container.classList.toggle("hidden", parts.length === 0);
  container.dataset.strategy = partText(lesson.strategy?.key);
  container.dataset.knowledgeState = partText(lesson.knowledge_state);
  return parts.length;
}

// With parts, the cards that still show (teacher notes with tips, media) continue the parts'
// numbering instead of repeating 03 / 04 after card 07.
function numberCardsAfterParts(count) {
  let number = count;
  [".teacher-notes-card", "#mediaCard"].forEach(selector => {
    const card = document.querySelector(selector);
    if (!card || card.classList.contains("hidden")) return;
    number += 1;
    const badge = card.querySelector(".card-number");
    if (badge) badge.textContent = String(number).padStart(2, "0");
  });
}

// One delegated listener: flip a card, or open/close a hint or solution.
document.querySelector("#lessonPartsList")?.addEventListener("click", event => {
  const card = event.target.closest(".lp-flip");
  if (card) {
    card.setAttribute("aria-pressed", String(card.getAttribute("aria-pressed") !== "true"));
    return;
  }
  const button = event.target.closest(".lp-reveal");
  const panel = button ? document.getElementById(button.getAttribute("aria-controls")) : null;
  if (!panel) return;
  const open = button.getAttribute("aria-expanded") !== "true";
  button.setAttribute("aria-expanded", String(open));
  button.textContent = open ? button.dataset.hideLabel : button.dataset.showLabel;
  panel.hidden = !open;
  if (open) panel.removeAttribute("aria-hidden");
  else panel.setAttribute("aria-hidden", "true");
});
/* lesson-parts:end */

function renderLesson(data) {
  const { lesson, question } = data;
  if (data.subject) currentSubject = data.subject;
  testTotal = Number(data.test_total || 15);
  testRange = data.test_range || { minimum: 3, maximum: testTotal, target: 80 };
  lessonConcepts = (lesson.concepts || []).map(item => item.name).filter(Boolean);
  latestKnowledge = data.knowledge || null;
  document.querySelector("#lessonTitle").textContent = lesson.lesson_title;
  document.querySelector("#sideTitle").textContent = lesson.lesson_title;
  document.querySelector("#levelBadge").textContent = lesson.detected_level;
  document.querySelector("#explanation").textContent = lesson.explanation;
  // A teaching-engine lesson carries its content in `parts`; the explanation and worked
  // example cards would repeat it, so they are hidden - only then. A lesson without parts
  // renders exactly as before.
  const partCount = renderParts(lesson);
  const hasParts = partCount > 0;
  // Server-translated note, e.g. that the topic's pages could not be read.
  const notice = document.querySelector("#lessonNotice");
  if (notice) {
    notice.textContent = partText(lesson.notice);
    notice.classList.toggle("hidden", !notice.textContent);
  }
  document.querySelector(".explanation-card")?.classList.toggle("hidden", hasParts);
  // A lesson can arrive without a usable worked example; the card is hidden then, and an
  // example without a stated problem or answer shows only what it has.
  const worked = lesson.worked_example && typeof lesson.worked_example === "object" ? lesson.worked_example : null;
  const workedSteps = Array.isArray(worked?.steps) ? worked.steps : [];
  document.querySelector(".example-card")?.classList.toggle("hidden", hasParts || !worked || (!worked.problem && !workedSteps.length));
  document.querySelector("#exampleProblem").textContent = worked?.problem || "";
  document.querySelector("#exampleProblem").classList.toggle("hidden", !worked?.problem);
  document.querySelector("#exampleSteps").innerHTML = workedSteps
    .map(step => `<li>${escapeHtml(String(step).replace(OWN_NUMBER, ""))}</li>`).join("");
  document.querySelector("#exampleAnswer").dataset.answer = worked?.answer || "";
  document.querySelector("#exampleAnswer").textContent = worked?.answer ? `${t("answer")}: ${worked.answer}` : "";
  document.querySelector("#exampleAnswer").classList.toggle("hidden", !worked?.answer);
  const tips = lesson.teacher_tips || [];
  const exceptions = lesson.exceptions || [];
  // Next to parts, teacher notes only earn their card when there are tips to show.
  document.querySelector(".teacher-notes-card")?.classList.toggle("hidden", hasParts && !tips.length);
  document.querySelector("#teacherTips").innerHTML = tips.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  document.querySelector("#exceptionsList").innerHTML = exceptions.length
    ? exceptions.map(item => `<li>${escapeHtml(item)}</li>`).join("")
    : `<li>${t("noExceptions")}</li>`;
  document.querySelector("#conceptList").innerHTML = lesson.concepts.map(item => `<div class="concept">${escapeHtml(item.name)}</div>`).join("");
  renderMastery(lesson.concepts.map(item => ({ concept: item.name, attempts: 0, average_score: 0 })));
  currentQuestion = question;
  questionResults = [];
  document.querySelector("#startTestCard").classList.remove("hidden");
  document.querySelector("#questionCard").classList.add("hidden");
  document.querySelector("#testProgress").classList.add("hidden");
  document.querySelector("#testSummary").classList.add("hidden");
  renderMedia(lesson);
  if (hasParts) numberCardsAfterParts(partCount);
  renderFormulas(lessonView);
}

uploadForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (imageInput.files.length > photoLimit) return showError(t("photoLimitCount", { count: photoLimit }));
  if (!studyGoal.value.trim() && imageInput.files.length === 0) {
    return showError(t("goalRequired"));
  }
  uploadForm.querySelector("button").disabled = true;
  uploadView.classList.add("hidden");
  lessonView.classList.remove("hidden");
  document.querySelector("#lessonContent").classList.add("hidden");
  document.querySelector("#loading").classList.remove("hidden");
  try {
    const formData = new FormData(uploadForm);
    formData.delete("images");
    for (const file of [...imageInput.files].slice(0, photoLimit)) {
      const prepared = await normalizeImage(file);
      formData.append("images", prepared, prepared.name || file.name);
    }
    currentSubject = uploadForm.querySelector('input[name="subject"]:checked')?.value || currentSubject;
    const response = await fetch("/api/analyze", { method: "POST", body: formData });
    // A proxy's 413/502/504 page or a login redirect is HTML: the student gets a sentence,
    // never "Unexpected token '<'".
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.session_id) {
      const failure = new Error(data.error || (response.status === 413 ? t("imgTooLarge") : t("lessonStartFailed")));
      failure.userFacing = true;
      throw failure;
    }
    sessionId = data.session_id;
    renderLesson(data);
    showMaterialNotes(data.material_notes);
    document.querySelector("#loading").classList.add("hidden");
    document.querySelector("#lessonContent").classList.remove("hidden");
  } catch (error) {
    lessonView.classList.add("hidden");
    uploadView.classList.remove("hidden");
    showError(error?.userFacing && error.message ? error.message : t("lessonStartFailed"));
  } finally {
    uploadForm.querySelector("button").disabled = false;
  }
});

/* What could not be read from the lesson's photos: one calm line, not a warning box. */
function showMaterialNotes(notes) {
  const target = document.querySelector("#materialNotes");
  if (!target) return;
  const unclear = notes?.unclear || [];
  const parts = [];
  if (unclear.length) parts.push(t("imgLessonUnclear", { parts: unclear.map(item => `“${item}”`).join(", ") }));
  (notes?.hints || []).slice(0, 1).forEach(hint => parts.push(hint));
  target.textContent = parts.join(" ");
  target.classList.toggle("hidden", parts.length === 0);
}

document.querySelector("#hintButton").addEventListener("click", async () => {
  hintUsed = true;
  const hint = document.querySelector("#hint");
  if (!hint.textContent && sessionId) {
    try {
      const response = await fetch("/api/hint", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || t("dxLoadFailed"));
      hint.textContent = data.hint || "";
    } catch (error) {
      showError(error.message);
      return;
    }
  }
  hint.classList.toggle("hidden");
  document.querySelector("#hintListenControl")?.classList.toggle("hidden", hint.classList.contains("hidden"));
});
document.querySelector("#newLesson").addEventListener("click", () => location.reload());
document.querySelector("#finishTest")?.addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch("/api/finish", { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify({ session_id: sessionId }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    announceXp(data, `finish:${sessionId}`);
    latestKnowledge = data.progress.knowledge;
    button.hidden = true;
    renderSummary(data);
  } catch (error) {
    showError(error.message || t("answerFailed"));
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#restartTest").addEventListener("click", () => location.reload());
document.querySelector("#startTest").addEventListener("click", () => {
  document.querySelector("#startTestCard").classList.add("hidden");
  document.querySelector("#testProgress").classList.remove("hidden");
  renderQuestion(currentQuestion);
  document.querySelector("#testProgress").scrollIntoView({ behavior: "smooth", block: "start" });
});

function renderSummary(data) {
  const summary = data.summary || {};
  const practice = data.practice_results;
  const mastery = practice ? practice.mastery_changes.map(item => ({
    concept: item.concept, attempts: 1, average_score: item.after
  })) : (data.progress.mastery || []);
  document.querySelector("#questionCard").classList.add("hidden");
  document.querySelector("#testProgress").classList.add("hidden");
  document.querySelector("#testSummary").classList.remove("hidden");
  document.querySelector("#summaryScore").innerHTML = `${data.progress.average_score}<small>/100</small>`;
  document.querySelector("#summaryOverall").textContent = [summary.overall, practice?.recommended_next_action].filter(Boolean).join(" ");
  // The knowledge gate's own numbers when it supplied them: knowledge per concept against
  // the target, not the average test score.
  const knowledge = summary.knowledge || data.progress?.knowledge;
  const rows = knowledge?.concepts?.length ? knowledge.concepts.map(item => ({
    concept: item.concept, score: item.knowledge, status: item.known ? "strong" : item.knowledge >= 55 ? "developing" : "weak",
    label: item.known ? t("knowledgeKnown") : item.status === "untested" ? t("knowledgeUntested") : `${item.knowledge}%`,
  })) : mastery.map(item => {
    const score = item.attempts ? item.average_score : 0;
    return { concept: item.concept, score, status: score >= 80 ? "strong" : score >= 55 ? "developing" : "weak", label: `${score}%` };
  });
  document.querySelector("#summaryChart").innerHTML = rows.map(item =>
    `<div class="chart-row"><div><span>${escapeHtml(item.concept)}</span><b>${escapeHtml(item.label)}</b></div><div class="chart-track"><i class="${item.status}" style="width:${Math.max(item.score, 3)}%"></i></div></div>`
  ).join("") + (knowledge ? `<p class="knowledge-target-note">${escapeHtml(t("conceptsAtTarget", { known: knowledge.known, total: knowledge.total, target: knowledge.target }))}</p>` : "");
  const weaknesses = summary.weaknesses?.length ? summary.weaknesses : practice ? practice.concepts_still_weak : mastery.filter(item => item.average_score < 80).map(item => item.concept);
  document.querySelector("#summaryWeaknesses").innerHTML = weaknesses.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  const nextSteps = practice ? [
    practice.recommended_next_action,
    practice.next_recommended_review_date ? `Next review: ${practice.next_recommended_review_date}` : ""
  ].filter(Boolean) : (summary.next_steps || []);
  document.querySelector("#summaryNextSteps").innerHTML = nextSteps.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  renderMath(document.querySelector("#testSummary"));
  document.querySelector("#testSummary").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* A wrong first answer in a teach-first lesson: the server withholds the correction and
 * sends a short reteach instead. The student sees what is missing, the idea explained
 * another way and a simpler example, then answers the SAME question again. Nothing here
 * shows the answer; the second result is graded and shown as usual. */
function showRetry(data, retry) {
  const feedback = document.querySelector("#feedback");
  const evaluation = data.evaluation || {};
  // What the student gave, so "Try again" starts from it (a long written answer on a phone
  // must not have to be typed again).
  const previous = collectAnswer();
  latestKnowledge = data.progress?.knowledge || latestKnowledge;
  // Same question: the number and the results so far stay as they are.
  updateProgress(questionResults.length + 1, latestKnowledge);
  document.querySelector("#questionCard").classList.add("result-retry");
  document.querySelectorAll("#answerControl input, #answerControl select, #answerControl textarea, #answerControl button").forEach(control => { control.disabled = true; });
  feedback.className = "feedback incorrect retry";
  // Model text, escaped; a field that is not text (or is empty) leaves its block out.
  const section = (className, label, text) => partText(text)
    ? `<div class="retry-block ${className}"><b>${label}</b><div class="feedback-steps">${escapeHtml(partText(text))}</div></div>` : "";
  const said = partText(evaluation.feedback);
  feedback.innerHTML = `<div id="feedbackSpeechText"><strong>${t("incorrectTitle")}</strong>`
    + `${said ? `<div class="feedback-steps">${escapeHtml(said)}</div>` : ""}`
    + `${diagnosisMarkup(data.diagnosis)}`
    + section("retry-missing", t("retryMissing"), retry.misunderstanding)
    + section("retry-reteach", t("retryReteach"), retry.reteach)
    + section("retry-example", t("retrySimplerExample"), retry.simpler_example)
    + `<p class="retry-prompt">${t("retryPrompt")}</p></div>`
    + `<button class="next-button retry-button" type="button">${t("lessonTryAgain")}</button>`;
  renderMath(feedback);
  bindDiagnosisDetails(feedback);
  document.querySelector("#feedbackListenControl")?.classList.remove("hidden");
  answerForm.querySelector(".primary-button").classList.add("hidden");
  if (data.progress) {
    document.querySelector("#score").textContent = data.progress.average_score;
    document.querySelector("#answered").textContent = `${data.progress.answered} ${data.progress.answered === 1 ? t("question") : t("questions")} ${t("answered")}`;
    renderMastery(data.progress.mastery);
  }
  feedback.querySelector(".retry-button").addEventListener("click", () => {
    renderQuestion(retry.question && typeof retry.question === "object" ? retry.question : currentQuestion, { retry: true });
    restoreAnswer(previous);
    // The reteach stays where it is; only its button goes.
    feedback.querySelector(".retry-button")?.remove();
    document.querySelector("#questionCard").scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

/* Put an earlier answer back into freshly drawn controls (the second try of "Try again"). */
function restoreAnswer(previous) {
  if (Array.isArray(previous)) {
    const list = document.querySelector("#orderingList");
    if (list) {
      previous.forEach(id => {
        const item = [...list.querySelectorAll(".ordering-item")].find(node => node.dataset.id === String(id));
        if (item) list.appendChild(item);
      });
      return;
    }
    document.querySelectorAll('input[name="answerOption"]').forEach(input => { input.checked = previous.includes(input.value); });
    return;
  }
  if (typeof previous !== "string" || !previous) return;
  const written = document.querySelector("#writtenAnswer");
  if (written) { written.value = previous; return; }
  const dropdown = document.querySelector("#dropdownAnswer");
  if (dropdown) { dropdown.value = previous; return; }
  const option = [...document.querySelectorAll('input[name="answerOption"]')].find(input => input.value === previous);
  if (option) option.checked = true;
}

answerForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = answerForm.querySelector(".primary-button");
  const submittedAnswer = collectAnswer();
  if (submittedAnswer === "" || (Array.isArray(submittedAnswer) && submittedAnswer.length === 0)) {
    showError(t("answerRequired"));
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        answer: submittedAnswer,
        hints_used: hintUsed,
        retry_count: answerRetryCount,
        answer_source: answerSource,
        response_confidence: Number(answerForm.querySelector('input[name="responseConfidence"]:checked')?.value || 50)
      })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    announceXp(data, `lesson:${sessionId}:${questionResults.length}${answerRetryCount ? `:${answerRetryCount}` : ""}`);
    if (data.retry?.allowed && !data.complete) {
      showRetry(data, data.retry);
      return;
    }
    nextQuestion = data.next_question;
    const feedback = document.querySelector("#feedback");
    const correct = data.evaluation.is_correct;
    questionResults.push(correct);
    latestKnowledge = data.progress.knowledge || latestKnowledge;
    updateProgress(data.complete ? questionResults.length : questionResults.length + 1, latestKnowledge);
    document.querySelector("#questionCard").classList.add(correct ? "result-correct" : "result-wrong");
    document.querySelectorAll("#answerControl input, #answerControl select, #answerControl textarea, #answerControl button").forEach(control => { control.disabled = true; });
    feedback.className = `feedback ${correct ? "correct" : "incorrect"}`;
    const continueLabel = data.complete ? t("viewResults") : t("nextQuestion");
    feedback.innerHTML = `<div id="feedbackSpeechText"><strong>${correct ? t("correctTitle") : t("incorrectTitle")}</strong><div class="feedback-steps">${escapeHtml(data.evaluation.feedback)}</div>${data.evaluation.correction ? `<div class="feedback-steps"><b>${t("correction")}:</b>\n${escapeHtml(data.evaluation.correction)}</div>` : ""}${data.evaluation.teacher_tip ? `<div class="feedback-note"><b>${t("tip")}:</b> ${escapeHtml(data.evaluation.teacher_tip)}</div>` : ""}${data.evaluation.exception_note ? `<div class="feedback-note exception"><b>${t("exceptionNote")}:</b> ${escapeHtml(data.evaluation.exception_note)}</div>` : ""}</div>${diagnosisMarkup(data.diagnosis)}<button class="next-button" type="button">${continueLabel}</button>`;
    renderMath(feedback);
    bindDiagnosisDetails(feedback);
    document.querySelector("#feedbackListenControl")?.classList.remove("hidden");
    button.classList.add("hidden");
    document.querySelector("#score").textContent = data.progress.average_score;
    document.querySelector("#answered").textContent = `${data.progress.answered} ${data.progress.answered === 1 ? t("question") : t("questions")} ${t("answered")}`;
    renderMastery(data.progress.mastery);
    feedback.querySelector("button").addEventListener("click", () => {
      if (data.complete) {
        renderSummary(data);
      } else {
        renderQuestion(nextQuestion);
        document.querySelector("#testProgress").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  } catch (error) {
    // A failed request is not an attempt: counting it made the first real answer after a
    // 422 look like a second try, and it lost its "Try again".
    showError(error.message || t("answerFailed"));
  } finally {
    button.disabled = false;
  }
});

const chatPanel = document.querySelector("#chatPanel");
const chatMessages = document.querySelector("#chatMessages");
const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");

document.querySelector("#chatToggle").addEventListener("click", () => {
  chatPanel.classList.remove("hidden");
  chatInput.focus();
});
document.querySelector("#chatClose").addEventListener("click", () => chatPanel.classList.add("hidden"));
const chatSuggestions = document.querySelector(".chat-suggestions");
// One delegated listener on the container: the tutor replaces these buttons with its
// own quick replies (chips) after each turn, and delegation survives that replacement.
chatSuggestions.addEventListener("click", event => {
  const button = event.target.closest("button");
  if (!button) return;
  chatInput.value = button.textContent;
  chatForm.requestSubmit();
});

function renderChips(labels) {
  const entries = labels.length
    ? labels.map(label => ({ label }))
    : [{ label: t("giveHint"), key: "giveHint" }, { label: t("whatPractice"), key: "whatPractice" }];
  chatSuggestions.replaceChildren(...entries.map(({ label, key }) => {
    const button = document.createElement("button");
    button.type = "button";
    if (key) button.dataset.i18n = key;
    button.textContent = label; // textContent: a model-written label can never become markup
    return button;
  }));
}

function addChatMessage(text, role, extraClass = "") {
  const message = document.createElement("div");
  message.className = `message ${role === "student" ? "student-message" : "tutor-message"} ${extraClass}`;
  if (role === "tutor" && !extraClass.includes("typing-message")) {
    // Tutor replies are untrusted model text: the shared escape-first renderer builds
    // the only tags that appear (formulas become slots before markdown runs), then KaTeX
    // typesets them. A resumed lesson's history goes through this same path.
    message.innerHTML = renderMarkdown(text);
    message.dataset.speechListenAuto = "";
    renderMath(message);
    highlightCode(message);
  } else {
    message.textContent = text; // student text and the typing indicator stay verbatim
  }
  chatMessages.appendChild(message);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return message;
}

// Explanation controls send a control token with their label, so the tutor adapts the
// next reply to the current topic without the student retyping the question.
document.querySelector(".chat-controls")?.addEventListener("click", event => {
  const button = event.target.closest("button[data-control]");
  if (button) sendChat(button.textContent.trim(), button.dataset.control);
});

const chatTray = document.querySelector("#chatAttachTray") ? createAttachmentTray({
  tray: document.querySelector("#chatAttachTray"),
  inputs: [document.querySelector("#chatCamera"), document.querySelector("#chatGallery")],
  purpose: "chat",
  onError: showError,
}) : null;

chatForm.addEventListener("submit", event => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (chatTray?.busy()) return showError(t("imgStillUploading"));
  if (chatTray?.failed()) return showError(t("imgFixFailed"));
  if (!message && !chatTray?.count()) return;
  chatInput.value = "";
  sendChat(message);
});

async function sendChat(message, control = null) {
  const attachments = chatTray ? chatTray.ids() : [];
  if ((!message && !attachments.length) || !sessionId) return;
  const studentBubble = addChatMessage(message || t("imgPhotoOnly"), "student");
  const typing = addChatMessage(attachments.length ? t("imgReading") : t("thinking"), "tutor", "typing-message");
  try {
    const body = { session_id: sessionId, message };
    if (control) body.control = control;
    if (attachments.length) body.attachments = attachments;
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    });
    // A killed worker's 502/504 page or a login redirect is HTML, not JSON: the student
    // reads the server's own message or "Tutor chat failed.", never a parser error.
    const data = await response.json().catch(() => ({}));
    if (!response.ok || typeof data.reply !== "string") {
      const failure = new Error(data.error || t("chatError"));
      failure.userFacing = true;
      throw failure;
    }
    typing.remove();
    if (data.attachments?.length) {
      studentBubble.appendChild(sentPhotosNode(data.attachments));
      const notes = photoNotesNode(data.image_notes);
      if (notes) studentBubble.appendChild(notes);
      chatTray?.clear();
    }
    // The reply may end with a "CHIPS: a | b" line naming tap answers for its question;
    // it is stripped before rendering (and speech), so it is never shown or read aloud.
    const parsed = parseChips(data.reply);
    addChatMessage(parsed.text, "tutor");
    renderChips(parsed.chips);
    renderMastery(data.mastery);
  } catch (error) {
    typing.remove();
    // Photos stay in the tray, already read, so sending again costs no second reading.
    if (attachments.length && !chatInput.value) chatInput.value = message;
    addChatMessage(t("chatFailed"), "tutor");
    showError(error?.userFacing && error.message ? error.message : t("chatError"));
  }
}

const bootstrapElement = document.querySelector("#lessonBootstrap");
const lessonBootstrap = bootstrapElement ? JSON.parse(bootstrapElement.textContent) : null;
if (lessonBootstrap) {
  sessionId = lessonBootstrap.session_id;
  uploadView.classList.add("hidden");
  lessonView.classList.remove("hidden");
  renderLesson(lessonBootstrap);
  // A resumed or refreshed lesson shows the tutor chat it already had, rendered exactly
  // like a live reply (the server stores tutor text without its CHIPS line).
  for (const item of lessonBootstrap.chat_history || []) {
    const content = String(item?.content || "");
    if (content) addChatMessage(content, item.role === "student" ? "student" : "tutor");
  }
} else {
  // The assistant's "start a short lesson" button lands here with the question as
  // ?goal=..., so the student taps "Build my lesson" instead of retyping it.
  const prefillGoal = new URLSearchParams(window.location.search).get("goal");
  if (prefillGoal && studyGoal) {
    studyGoal.value = prefillGoal.slice(0, 500);
    studyGoal.focus();
  }
}

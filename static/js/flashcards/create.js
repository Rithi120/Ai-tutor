import { api, toast, escapeHtml, t, optionsHtml, SUBJECTS, SET_ID } from "./common.js";
import { renderMath } from "../math.js";
import { mergeGeneratedCards, needsMathPreview } from "./editor-rules.js";
import * as suggest from "./suggest.js";

let cards = [];          // [{id?, type, front, back, explanation, hint, tags[], options[], difficulty}]
let genParams = null;    // last generation params (enables per-card regenerate)
let dirty = false;
let generating = false;
let saving = false;
const IMPORT_ID = window.LEARNOVA_IMPORT_ID ?? null;
const VOCABULARY_IMPORT_ID = window.LEARNOVA_VOCABULARY_IMPORT_ID ?? null;
let vocabularyListId = null;
const DRAFT_KEY = `learnova:flashcard-draft:${SET_ID || IMPORT_ID || VOCABULARY_IMPORT_ID || "new"}`;
let autosaveTimer = null;

function draftLabel(en, de) {
  return window.LEARNOVA_LANGUAGE === "de" ? de : en;
}
function setAutosaveState(message) {
  const status = document.querySelector("#autosaveState");
  if (status) status.textContent = message;
}
function saveLocalDraft() {
  const draft = {
    savedAt: Date.now(),
    title: document.querySelector("#setTitle").value,
    description: document.querySelector("#setDescription").value,
    subject: document.querySelector("#metaSubject").value,
    topic: document.querySelector("#metaTopic").value,
    grade: document.querySelector("#metaGrade").value,
    difficulty: document.querySelector("#metaDifficulty").value,
    tags: document.querySelector("#metaTags").value,
    cards,
  };
  try {
    localStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
    setAutosaveState(draftLabel("Draft saved", "Entwurf gespeichert"));
  } catch (_) {
    setAutosaveState(draftLabel("Draft not saved", "Entwurf nicht gespeichert"));
  }
}
function scheduleAutosave() {
  setAutosaveState(draftLabel("Saving draft…", "Entwurf wird gespeichert…"));
  window.clearTimeout(autosaveTimer);
  autosaveTimer = window.setTimeout(saveLocalDraft, 450);
}
function restoreLocalDraft() {
  try {
    const draft = JSON.parse(localStorage.getItem(DRAFT_KEY) || "null");
    if (!draft?.cards?.length) return false;
    document.querySelector("#setTitle").value = draft.title || "";
    document.querySelector("#setDescription").value = draft.description || "";
    document.querySelector("#metaSubject").value = draft.subject || "Other";
    document.querySelector("#metaTopic").value = draft.topic || "";
    document.querySelector("#metaGrade").value = draft.grade || "";
    document.querySelector("#metaDifficulty").value = draft.difficulty || "medium";
    document.querySelector("#metaTags").value = draft.tags || "";
    cards = draft.cards.map(normalize);
    setAutosaveState(draftLabel("Draft restored", "Entwurf wiederhergestellt"));
    return true;
  } catch (_) {
    localStorage.removeItem(DRAFT_KEY);
    return false;
  }
}

function blankCard() {
  return { type: "question_answer", front: "", back: "", explanation: "", hint: "", tags: [], options: [], difficulty: "medium" };
}
function normalize(card) {
  return {
    id: card.id, type: card.type || "question_answer", front: card.front || "", back: card.back || "",
    explanation: card.explanation || "", hint: card.hint || "",
    tags: Array.isArray(card.tags) ? card.tags : [], options: Array.isArray(card.options) ? card.options : [],
    difficulty: card.difficulty || "medium", source_reference: card.source_reference || "",
    vocabulary_entry_id: card.vocabulary_entry_id || "",
  };
}
function markDirty() {
  dirty = true;
  document.querySelector("#saveBar").classList.remove("hidden");
  scheduleAutosave();
}

/* --------------------------- rendering --------------------------- */
function rowHtml(card, index) {
  const label = (key) => escapeHtml(t(key));
  // One number, one AI button, one menu. Everything else a card can do lives behind the
  // menu or the disclosure, so a row at rest is two fields and nothing competing.
  return `
    <li class="card-row" data-index="${index}" draggable="true">
      <div class="card-row-head">
        <span class="card-num">${index + 1}</span>
        <span class="drag-handle" aria-hidden="true" title="${escapeHtml(draftLabel("Drag to reorder", "Zum Sortieren ziehen"))}">⠿</span>
        <div class="card-row-tools">
          <button type="button" data-suggest class="suggest-trigger" title="${label("fcSuggestDefinition")}" aria-label="${label("fcSuggestDefinition")}">✨</button>
          <details class="card-menu">
            <summary class="card-menu-trigger" title="${label("fcCardActions")}" aria-label="${label("fcCardActions")}"><span aria-hidden="true">⋯</span></summary>
            <div class="card-menu-body">
              <button type="button" data-up><span aria-hidden="true">↑</span> ${label("fcMoveUp")}</button>
              <button type="button" data-down><span aria-hidden="true">↓</span> ${label("fcMoveDown")}</button>
              ${genParams ? `<button type="button" data-regen><span aria-hidden="true">↻</span> ${label("fcRegenerate")}</button>` : ""}
              <button type="button" data-dup><span aria-hidden="true">⧉</span> ${label("fcDuplicateCard")}</button>
              <button type="button" data-del class="danger"><span aria-hidden="true">✕</span> ${label("fcDeleteCard")}</button>
            </div>
          </details>
        </div>
      </div>
      <div class="card-row-fields">
        <div class="card-field card-front-wrap">
          <textarea id="front-${index}" data-field="front" rows="1">${escapeHtml(card.front)}</textarea>
          <label class="card-field-label" for="front-${index}">${label("fcTerm")}</label>
          ${mathPreviewHtml(card.front)}
        </div>
        <div class="card-field">
          <textarea id="back-${index}" data-field="back" rows="1">${escapeHtml(card.back)}</textarea>
          <label class="card-field-label" for="back-${index}">${label("fcDefinition")}</label>
          ${mathPreviewHtml(card.back)}
        </div>
      </div>
      <details class="card-more">
        <summary>${label("fcMoreOptions")}</summary>
        <div class="card-more-body">
          <label>${label("fcExplanation")}<textarea data-field="explanation" rows="1">${escapeHtml(card.explanation)}</textarea></label>
          <div class="card-more-row">
            <label>${label("fcHint")}<input data-field="hint" value="${escapeHtml(card.hint)}"></label>
            <label>${label("fcDifficulty")}<select data-field="difficulty">${optionsHtml(["easy", "medium", "hard"], card.difficulty)}</select></label>
            <label>${label("fcTags")}<input data-field="tags" value="${escapeHtml(card.tags.join(", "))}"></label>
          </div>
        </div>
      </details>
    </li>`;
}

// The editor fields hold the LaTeX source ($\sqrt{81}$) because that is what gets saved;
// the preview underneath shows what the student will actually see on the card.
function mathPreviewHtml(text) {
  return needsMathPreview(text) ? `<div class="card-math-preview">${escapeHtml(text)}</div>` : "";
}

function refreshMathPreview(textarea) {
  const wrap = textarea.closest(".card-field");
  if (!wrap) return;
  let preview = wrap.querySelector(".card-math-preview");
  if (!needsMathPreview(textarea.value)) { preview?.remove(); return; }
  if (!preview) { preview = document.createElement("div"); preview.className = "card-math-preview"; wrap.appendChild(preview); }
  preview.textContent = textarea.value;
  renderMath(preview);
}

function renderAllMathPreviews() {
  document.querySelectorAll("#cardRows .card-math-preview").forEach(node => renderMath(node));
}

/* A full re-render replaces every row, which would throw away the caret the student is
 * typing in and close any open "More options". Snapshot both, restore after. */
function focusSnapshot() {
  const active = document.activeElement;
  const inList = active && active.closest?.("#cardRows");
  return {
    id: inList ? active.id : "",
    start: inList ? active.selectionStart : 0,
    end: inList ? active.selectionEnd : 0,
    open: [...document.querySelectorAll("#cardRows .card-more[open]")]
      .map(el => Number(el.closest(".card-row")?.dataset.index)),
  };
}
function restoreFocus(snapshot) {
  snapshot.open.forEach(index => {
    const details = document.querySelector(`.card-row[data-index="${index}"] .card-more`);
    if (details) details.open = true;
  });
  if (!snapshot.id) return;
  const field = document.getElementById(snapshot.id);
  if (!field) return;
  field.focus({ preventScroll: true });
  try { field.setSelectionRange(snapshot.start, snapshot.end); } catch (_) { /* not a text field */ }
}

/* Quizlet-style fields start one line tall and grow with the writing. Without this a
 * one-word term still occupies a fixed five-line box, which is most of what made the
 * old editor feel heavy. */
function autosize(field) {
  if (!field || field.tagName !== "TEXTAREA") return;
  field.style.height = "auto";
  field.style.height = `${field.scrollHeight}px`;
}
function autosizeAll() {
  document.querySelectorAll("#cardRows textarea").forEach(autosize);
}

function updateCardCount() {
  document.querySelector("#cardCount").textContent =
    `${cards.length} ${cards.length === 1 ? t("fcCard") : t("fcCards")}`;
}

function renderCards() {
  const list = document.querySelector("#cardRows");
  const snapshot = focusSnapshot();
  updateCardCount();
  list.innerHTML = cards.map(rowHtml).join("");
  autosizeAll();
  restoreFocus(snapshot);
}

/* Appending never renumbers the rows above it, so it can be done without touching them -
 * which is what lets a new row appear while the student is still typing in the last one. */
function appendRows(newCards) {
  const list = document.querySelector("#cardRows");
  const start = cards.length;
  cards.push(...newCards);
  list.insertAdjacentHTML("beforeend",
    newCards.map((card, offset) => rowHtml(card, start + offset)).join(""));
  autosizeAll();
  renderAllMathPreviews();
  updateCardCount();
}

/* --------------------------- row events --------------------------- */
function onListClick(event) {
  const row = event.target.closest(".card-row");
  if (!row) return;
  const index = Number(row.dataset.index);
  const chip = event.target.closest("[data-suggest-apply]");
  // Read the source from the attribute, not the text: a suggestion containing a
  // formula is typeset, and its textContent would then be rendered glyphs.
  if (chip) { applySuggestion(row, index, chip.dataset.back ?? chip.textContent); return; }
  if (event.target.closest("[data-suggest-close]")) { suggest.closePanel(row); return; }
  if (event.target.closest("[data-suggest]")) { suggest.requestFor(row, { manual: true }); return; }

  // Reordering renumbers rows, so every remembered term is stale afterwards.
  if (event.target.closest("[data-del]")) { cards.splice(index, 1); suggest.reset(); markDirty(); renderCards(); }
  else if (event.target.closest("[data-dup]")) { cards.splice(index + 1, 0, normalize({ ...cards[index], id: undefined })); suggest.reset(); markDirty(); renderCards(); }
  else if (event.target.closest("[data-up]") && index > 0) { [cards[index - 1], cards[index]] = [cards[index], cards[index - 1]]; suggest.reset(); markDirty(); renderCards(); }
  else if (event.target.closest("[data-down]") && index < cards.length - 1) { [cards[index + 1], cards[index]] = [cards[index], cards[index + 1]]; suggest.reset(); markDirty(); renderCards(); }
  else if (event.target.closest("[data-regen]")) { regenerateCard(index); }
}

/* Applying a suggestion writes straight into the field instead of re-rendering, so the
 * page never jumps and the student keeps their place. */
function applySuggestion(row, index, text) {
  const value = String(text || "").trim();
  if (!value) return;
  cards[index].back = value;
  const field = row.querySelector('[data-field="back"]');
  if (field) { field.value = value; autosize(field); }
  suggest.closePanel(row);
  markDirty();
}
function onListInput(event) {
  const row = event.target.closest(".card-row");
  const field = event.target.dataset.field;
  if (!row || !field) return;
  const index = Number(row.dataset.index);
  cards[index][field] = field === "tags" ? event.target.value.split(",").map(s => s.trim()).filter(Boolean) : event.target.value;
  autosize(event.target);
  if (field === "front" || field === "back") refreshMathPreview(event.target);
  if (field === "front") {
    // The term changed, so anything suggested for the old one no longer applies.
    suggest.invalidate(index);
    suggest.closePanel(row);
  }
  ensureTrailingBlank();
  markDirty();
}

/* The invariant: exactly one empty card is always waiting at the bottom, so adding the
 * next one is never a scroll down to the footer. Idempotent, so it is safe to call on
 * every keystroke; collectBody() drops the blank, so it is never saved. */
function ensureTrailingBlank() {
  const last = cards[cards.length - 1];
  if (last && (last.front.trim() || last.back.trim())) appendRows([blankCard()]);
}
function onListKeydown(event) {
  if (event.key === "Escape") {
    const row = event.target.closest(".card-row");
    suggest.closePanel(row);
    row?.querySelectorAll(".card-menu[open]").forEach(menu => { menu.open = false; });
    return;
  }
  if (event.key !== "Enter" || event.shiftKey) return;
  const field = event.target.dataset.field;
  const index = Number(event.target.closest(".card-row")?.dataset.index);
  // Term -> definition -> next term, so a whole set is one uninterrupted typing run.
  if (field === "front") {
    event.preventDefault();
    document.querySelector(`#back-${index}`)?.focus();
  } else if (field === "back") {
    event.preventDefault();
    if (index >= cards.length - 1) appendRows([blankCard()]);
    document.querySelector(`#front-${index + 1}`)?.focus();
  }
}

/* drag reorder */
let dragIndex = null;
function onDragStart(event) { const row = event.target.closest(".card-row"); if (row) { dragIndex = Number(row.dataset.index); row.classList.add("dragging"); } }
function onDragEnd(event) { event.target.closest(".card-row")?.classList.remove("dragging"); dragIndex = null; }
function onDragOver(event) {
  event.preventDefault();
  const row = event.target.closest(".card-row");
  if (row === null || dragIndex === null) return;
  const overIndex = Number(row.dataset.index);
  if (overIndex === dragIndex) return;
  const [moved] = cards.splice(dragIndex, 1);
  cards.splice(overIndex, 0, moved);
  dragIndex = overIndex;
  markDirty();
  renderCards();
}

/* --------------------------- actions --------------------------- */
function addCards(n) { appendRows(Array.from({ length: n }, blankCard)); markDirty(); }

async function generate() {
  if (generating) return;
  const sourceKind = document.querySelector("#genSourceKind").value;
  const params = {
    source_kind: sourceKind, subject: document.querySelector("#metaSubject").value,
    grade: document.querySelector("#metaGrade").value.trim(),
    difficulty: document.querySelector("#genDifficulty").value,
    card_type: document.querySelector("#genType").value,
    count: Number(document.querySelector("#genCount").value) || 10,
    content_language: document.querySelector("#genContentLang").value,
  };
  if (sourceKind === "topic") params.topic = document.querySelector("#genTopic").value.trim();
  else params.text = document.querySelector("#genText").value.trim();
  const errorEl = document.querySelector("#genError");
  errorEl.classList.add("hidden");
  if ((sourceKind === "topic" && !params.topic) || (sourceKind === "text" && !params.text)) {
    errorEl.textContent = sourceKind === "topic" ? t("fcEnterTopic") : t("fcPasteText");
    errorEl.classList.remove("hidden");
    return;
  }
  generating = true;
  const button = document.querySelector("#genButton");
  const status = document.querySelector("#genStatus");
  button.disabled = true;
  status.textContent = t("fcGenerating");
  try {
    const data = await api("/api/flashcards/generate", { method: "POST", body: params });
    genParams = params;
    cards = mergeGeneratedCards(cards, (data.cards || []).map(normalize));
    if (!document.querySelector("#setTitle").value && data.title) document.querySelector("#setTitle").value = data.title;
    if (data.low_quality) toast(t("fcLowQuality"), "warn");
    status.textContent = `${(data.cards || []).length} ${t("fcGenerated")}`;
    markDirty();
    renderCards();
  } catch (error) {
    status.textContent = "";
    errorEl.textContent = error.message;
    errorEl.classList.remove("hidden");
  } finally {
    generating = false;
    button.disabled = false;
  }
}

async function regenerateCard(index) {
  if (!genParams) return;
  try {
    const data = await api("/api/flashcards/generate", { method: "POST", body: { ...genParams, count: 1 } });
    if (data.cards && data.cards[0]) { cards[index] = normalize(data.cards[0]); markDirty(); renderCards(); toast(t("fcRegenerated"), "info"); }
  } catch (error) { toast(error.message, "error"); }
}

function collectBody() {
  return {
    title: document.querySelector("#setTitle").value.trim() || "Untitled set",
    subject: document.querySelector("#metaSubject").value,
    grade: document.querySelector("#metaGrade").value,
    difficulty: document.querySelector("#metaDifficulty").value,
    card_type: "mixed",
    vocabulary_list_id: vocabularyListId,
    cards: cards.filter(c => c.front.trim() && c.back.trim()),
  };
}

async function save() {
  if (saving) return;
  const body = collectBody();
  if (!body.cards.length) { toast(t("fcNoCards"), "error"); return; }
  saving = true;
  try {
    let id = SET_ID;
    if (id) {
      await api(`/api/flashcards/sets/${id}`, { method: "PUT", body });
      toast(t("fcUpdated"), "success");
    } else {
      const data = await api("/api/flashcards/sets", { method: "POST", body });
      id = data.id;
      toast(t("fcSaved"), "success");
    }
    dirty = false;
    localStorage.removeItem(DRAFT_KEY);
    window.location.href = `/flashcards/${id}`;
  } catch (error) {
    toast(error.message, "error");
  } finally {
    saving = false;
  }
}

/* --------------------------- init --------------------------- */
async function init() {
  document.querySelector("#metaSubject").innerHTML = optionsHtml(SUBJECTS, "Mathematics");
  // The suggester reads the set's settings at request time, so changing the subject or
  // content language mid-session is picked up without re-wiring anything.
  suggest.init(() => ({
    subject: document.querySelector("#metaSubject").value,
    grade: document.querySelector("#metaGrade").value.trim(),
    difficulty: document.querySelector("#metaDifficulty").value,
    card_type: document.querySelector("#genType").value,
    content_language: document.querySelector("#genContentLang").value,
  }));
  const autoToggle = document.querySelector("#autoSuggest");
  if (autoToggle) {
    autoToggle.checked = suggest.autoSuggestEnabled();
    autoToggle.addEventListener("change", () => suggest.setAutoSuggest(autoToggle.checked));
  }
  document.querySelector("#genSourceKind").addEventListener("change", event => {
    document.querySelectorAll("[data-gen-source]").forEach(el => el.classList.toggle("hidden", el.dataset.genSource !== event.target.value));
  });
  document.querySelectorAll("[data-gen-choice]").forEach(choice => {
    choice.addEventListener("click", () => {
      const kind = choice.dataset.genChoice;
      const source = document.querySelector("#genSourceKind");
      source.value = kind;
      source.dispatchEvent(new Event("change"));   // reuses the toggle above
      document.querySelector("#genPanel").classList.remove("hidden");
      document.querySelectorAll("[data-gen-choice]").forEach(other =>
        other.setAttribute("aria-expanded", String(other === choice)));
      document.querySelector(kind === "topic" ? "#genTopic" : "#genText")?.focus();
    });
  });
  // A native <details> menu stays open until it is told otherwise; clicking anywhere
  // else should dismiss it, the way every other menu on the page behaves.
  document.addEventListener("click", event => {
    document.querySelectorAll("#cardRows .card-menu[open]").forEach(menu => {
      if (!menu.contains(event.target)) menu.open = false;
    });
  });
  const list = document.querySelector("#cardRows");
  list.addEventListener("click", onListClick);
  list.addEventListener("input", onListInput);
  list.addEventListener("keydown", onListKeydown);
  // Leaving the term is the moment the student has said what the card is about, and the
  // moment before they would otherwise have to type a definition themselves.
  list.addEventListener("focusout", event => {
    const row = event.target.closest?.(".card-row");
    if (row && event.target.dataset.field === "front") suggest.requestFor(row);
  });
  list.addEventListener("dragstart", onDragStart);
  list.addEventListener("dragend", onDragEnd);
  list.addEventListener("dragover", onDragOver);
  document.querySelector("#addCard").addEventListener("click", () => { addCards(1); document.querySelector(`#front-${cards.length - 1}`)?.focus(); });
  document.querySelector("#addFive").addEventListener("click", () => addCards(5));
  document.querySelector("#genButton").addEventListener("click", generate);
  document.querySelector("#saveButton").addEventListener("click", save);
  document.querySelector("#discardButton").addEventListener("click", () => {
    if (window.confirm(t("fcUnsaved"))) { localStorage.removeItem(DRAFT_KEY); window.location.reload(); }
  });
  document.querySelectorAll("#setTitle, #setDescription, #metaSubject, #metaTopic, #metaGrade, #metaDifficulty, #metaTags")
    .forEach(el => el.addEventListener("input", markDirty));

  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      addCards(1);
      document.querySelector(`#front-${cards.length - 1}`)?.focus();
    }
  });
  window.addEventListener("beforeunload", event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } });

  if (SET_ID) {
    try {
      const data = await api(`/api/flashcards/sets/${SET_ID}`);
      const set = data.set;
      document.querySelector("#setTitle").value = set.title;
      document.querySelector("#metaSubject").value = set.subject;
      document.querySelector("#metaDifficulty").value = set.difficulty || "medium";
      document.querySelector("#metaGrade").value = set.grade || "";
      cards = set.cards.map(normalize);
    } catch (error) { toast(error.message, "error"); }
  } else if (IMPORT_ID || VOCABULARY_IMPORT_ID) {
    try {
      const data = await api(
        VOCABULARY_IMPORT_ID
          ? `/api/vocabulary/imports/${VOCABULARY_IMPORT_ID}/draft`
          : `/api/flashcards/imports/${IMPORT_ID}/draft`);
      const draft = data.draft;
      vocabularyListId = draft.vocabulary_list_id || null;
      document.querySelector("#setTitle").value = draft.title || "";
      document.querySelector("#metaSubject").value = draft.subject || "Other";
      document.querySelector("#metaDifficulty").value = draft.difficulty || "medium";
      document.querySelector("#metaGrade").value = draft.grade || "";
      cards = (draft.cards || []).map(normalize);
      genParams = {
        source_kind: draft.source_kind, text: "",
        subject: draft.subject, grade: draft.grade, difficulty: draft.difficulty,
        card_type: draft.card_type, content_language: draft.language,
      };
      markDirty();
    } catch (error) { toast(error.message, "error"); cards = [blankCard(), blankCard()]; }
  } else {
    cards = [blankCard(), blankCard()];
  }
  const restoredDraft = restoreLocalDraft();
  // A set loaded from the server or a draft ends on a filled card, so give it the blank
  // one too - opening an existing set should not start with a trip to the footer either.
  const last = cards[cards.length - 1];
  if (!last || last.front.trim() || last.back.trim()) cards.push(blankCard());
  renderCards();
  if (!IMPORT_ID && !VOCABULARY_IMPORT_ID && !restoredDraft) {
    dirty = false;
    document.querySelector("#saveBar").classList.add("hidden");
  }
}

document.addEventListener("DOMContentLoaded", init);

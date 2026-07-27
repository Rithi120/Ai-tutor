import { api, toast, escapeHtml, t, optionsHtml, SUBJECTS, SET_ID } from "./common.js";

let cards = [];          // [{id?, type, front, back, explanation, hint, tags[], options[], difficulty}]
let genParams = null;    // last generation params (enables per-card regenerate)
let dirty = false;
let generating = false;
let saving = false;
const IMPORT_ID = window.LEARNOVA_IMPORT_ID ?? null;
const VOCABULARY_IMPORT_ID = window.LEARNOVA_VOCABULARY_IMPORT_ID ?? null;
let vocabularyListId = null;

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
function markDirty() { dirty = true; document.querySelector("#saveBar").classList.remove("hidden"); }

/* --------------------------- rendering --------------------------- */
function renderCards() {
  const list = document.querySelector("#cardRows");
  document.querySelector("#cardCount").textContent = `${cards.length} ${cards.length === 1 ? t("fcCard") : t("fcCards")}`;
  list.innerHTML = cards.map((card, index) => `
    <li class="card-row" data-index="${index}" draggable="true">
      <div class="card-row-main">
        <span class="drag-handle" aria-hidden="true" title="Drag to reorder">⋮⋮</span>
        <span class="card-num">${index + 1}</span>
        <label class="visually-hidden" for="front-${index}">${escapeHtml(t("fcTerm"))}</label>
        <textarea id="front-${index}" data-field="front" rows="2" placeholder="${escapeHtml(t("fcTerm"))}">${escapeHtml(card.front)}</textarea>
        <label class="visually-hidden" for="back-${index}">${escapeHtml(t("fcDefinition"))}</label>
        <textarea id="back-${index}" data-field="back" rows="2" placeholder="${escapeHtml(t("fcDefinition"))}">${escapeHtml(card.back)}</textarea>
        <div class="card-row-tools">
          ${genParams ? `<button type="button" data-regen title="Regenerate">↻</button>` : ""}
          <button type="button" data-dup title="${escapeHtml(t("fcDuplicateCard"))}">⧉</button>
          <button type="button" data-del class="danger" title="${escapeHtml(t("fcDeleteCard"))}">🗑</button>
        </div>
      </div>
      <details class="card-more">
        <summary>${escapeHtml(t("fcMoreOptions"))}</summary>
        <div class="card-more-body">
          <label>${escapeHtml(t("fcExplanation"))}<textarea data-field="explanation" rows="1">${escapeHtml(card.explanation)}</textarea></label>
          <div class="card-more-row">
            <label>${escapeHtml(t("fcHint"))}<input data-field="hint" value="${escapeHtml(card.hint)}"></label>
            <label>${escapeHtml(t("fcDifficulty"))}<select data-field="difficulty">${optionsHtml(["easy", "medium", "hard"], card.difficulty)}</select></label>
            <label>${escapeHtml(t("fcTags"))}<input data-field="tags" value="${escapeHtml(card.tags.join(", "))}"></label>
          </div>
        </div>
      </details>
    </li>`).join("");
}

/* --------------------------- row events --------------------------- */
function onListClick(event) {
  const row = event.target.closest(".card-row");
  if (!row) return;
  const index = Number(row.dataset.index);
  if (event.target.closest("[data-del]")) { cards.splice(index, 1); markDirty(); renderCards(); }
  else if (event.target.closest("[data-dup]")) { cards.splice(index + 1, 0, normalize({ ...cards[index], id: undefined })); markDirty(); renderCards(); }
  else if (event.target.closest("[data-regen]")) { regenerateCard(index); }
}
function onListInput(event) {
  const row = event.target.closest(".card-row");
  const field = event.target.dataset.field;
  if (!row || !field) return;
  const index = Number(row.dataset.index);
  cards[index][field] = field === "tags" ? event.target.value.split(",").map(s => s.trim()).filter(Boolean) : event.target.value;
  markDirty();
}
function onListKeydown(event) {
  // Enter (without shift) in the definition field adds a new card
  if (event.key === "Enter" && !event.shiftKey && event.target.dataset.field === "back") {
    event.preventDefault();
    addCards(1);
    document.querySelector(`#front-${cards.length - 1}`)?.focus();
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
function addCards(n) { for (let i = 0; i < n; i++) cards.push(blankCard()); markDirty(); renderCards(); }

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
    cards = cards.concat((data.cards || []).map(normalize));
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
  document.querySelector("#genSourceKind").addEventListener("change", event => {
    document.querySelectorAll("[data-gen-source]").forEach(el => el.classList.toggle("hidden", el.dataset.genSource !== event.target.value));
  });
  const list = document.querySelector("#cardRows");
  list.addEventListener("click", onListClick);
  list.addEventListener("input", onListInput);
  list.addEventListener("keydown", onListKeydown);
  list.addEventListener("dragstart", onDragStart);
  list.addEventListener("dragend", onDragEnd);
  list.addEventListener("dragover", onDragOver);
  document.querySelector("#addCard").addEventListener("click", () => { addCards(1); document.querySelector(`#front-${cards.length - 1}`)?.focus(); });
  document.querySelector("#addFive").addEventListener("click", () => addCards(5));
  document.querySelector("#genButton").addEventListener("click", generate);
  document.querySelector("#saveButton").addEventListener("click", save);
  document.querySelector("#discardButton").addEventListener("click", () => { if (window.confirm(t("fcUnsaved"))) window.location.reload(); });
  document.querySelectorAll("#setTitle, #setDescription, #metaSubject, #metaTopic, #metaGrade, #metaDifficulty, #metaVisibility, #metaTags")
    .forEach(el => el.addEventListener("input", markDirty));

  document.addEventListener("keydown", event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); save(); }
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
  renderCards();
  if (!IMPORT_ID) {
    dirty = false;
    document.querySelector("#saveBar").classList.add("hidden");
  }
}

document.addEventListener("DOMContentLoaded", init);

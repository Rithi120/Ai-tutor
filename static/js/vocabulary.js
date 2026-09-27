import { api, escapeHtml, t, toast } from "./flashcards/common.js";
import { safeUUID } from "./dom.js";

const importForm = document.querySelector("#vocabularyImportForm");
const reviewPage = document.querySelector("[data-vocabulary-review]");
const studyPage = document.querySelector("[data-vocabulary-study]");
let reviewEntries = [];
let practiceItems = [];
let practiceIndex = 0;
let practiceStarted = 0;
let practiceSessionId = null;

function uuid() {
  return safeUUID();
}

if (importForm) {
  importForm.addEventListener("change", event => {
    if (event.target.name !== "source_kind") return;
    const file = event.target.value === "file";
    document.querySelector("#vocabularyFileField").classList.toggle("hidden", !file);
    document.querySelector("#vocabularyTextField").classList.toggle("hidden", file || event.target.value === "manual");
  });
  importForm.addEventListener("submit", async event => {
    event.preventDefault();
    const status = document.querySelector("#vocabularyImportStatus");
    const error = document.querySelector("#vocabularyImportError");
    error.classList.add("hidden");
    try {
      status.textContent = t("vocabularyUploading");
      const created = await api("/api/vocabulary/imports", {
        method: "POST", body: new FormData(importForm),
      });
      const id = created.vocabulary_import.id;
      status.textContent = t("vocabularyExtracting");
      await api(`/api/vocabulary/imports/${id}/extract`, { method: "POST", body: {} });
      status.textContent = t("vocabularyValidating");
      await api(`/api/vocabulary/imports/${id}/validate`, { method: "POST", body: {} });
      location.href = created.review_url;
    } catch (caught) {
      status.textContent = "";
      error.textContent = caught.message;
      error.classList.remove("hidden");
    }
  });
}

function rowHtml(entry, index) {
  const suggestion = entry.suggested_translation
    ? `<div class="vocabulary-suggestion"><b>${escapeHtml(t("vocabularySuggested"))}: ${escapeHtml(entry.suggested_translation)}</b><button data-accept-suggestion>${escapeHtml(t("vocabularyAcceptSuggestion"))}</button><button data-keep-original>${escapeHtml(t("vocabularyKeepOriginal"))}</button></div>`
    : "";
  return `<article class="vocabulary-review-row" data-index="${index}">
    <label><input data-field="included" type="checkbox"${entry.included !== false ? " checked" : ""}> ${escapeHtml(t("vocabularyInclude"))}</label>
    <label>${escapeHtml(t("vocabularySourceWord"))}<input data-field="source_term" value="${escapeHtml(entry.source_term || "")}"></label>
    <label>${escapeHtml(t("vocabularyTranslation"))}<input data-field="target_translation" value="${escapeHtml(entry.target_translation || "")}"></label>
    <label>${escapeHtml(t("vocabularyAlternatives"))}<input data-field="alternatives" value="${escapeHtml((entry.alternatives || []).join(", "))}"></label>
    <label>${escapeHtml(t("vocabularyExample"))}${entry.example_ai_generated ? `<small>${escapeHtml(t("vocabularyAiGenerated"))}</small>` : ""}<textarea data-field="source_example_sentence">${escapeHtml(entry.source_example_sentence || "")}</textarea></label>
    <label>${escapeHtml(t("vocabularyPartOfSpeech"))}<input data-field="part_of_speech" value="${escapeHtml(entry.part_of_speech || "")}"></label>
    <div class="vocabulary-validation status-${escapeHtml(entry.status || "needs_review")}"><b>${escapeHtml(t(`vocabularyStatus_${entry.status || "needs_review"}`))}</b><span>${escapeHtml(entry.validation_explanation || "")}</span><small>${Math.round(100 * Number(entry.confidence || 0))}% · ${escapeHtml(t("vocabularyPage"))} ${entry.page_number || "—"}</small></div>
    ${suggestion}
    <div class="row-actions"><button data-generate-example>${escapeHtml(t("vocabularyGenerateExample"))}</button>${entry.status === "duplicate" ? `<button data-merge>${escapeHtml(t("vocabularyMerge"))}</button>` : ""}<button data-split>${escapeHtml(t("vocabularySplit"))}</button><button data-remove class="danger">${escapeHtml(t("vocabularyRemove"))}</button></div>
  </article>`;
}
function renderReview() {
  document.querySelector("#vocabularyReviewRows").innerHTML = reviewEntries.map(rowHtml).join("");
}
async function saveReview() {
  const id = reviewPage.dataset.importId;
  if (id) {
    await api(`/api/vocabulary/imports/${id}/entries`, {
      method: "PUT", body: { entries: reviewEntries },
    });
  } else {
    await api(`/api/vocabulary/lists/${reviewPage.dataset.listId}`, {
      method: "PUT", body: { entries: reviewEntries },
    });
  }
}
async function initReview() {
  const id = reviewPage.dataset.importId;
  const data = await api(id
    ? `/api/vocabulary/imports/${id}`
    : `/api/vocabulary/lists/${reviewPage.dataset.listId}`);
  reviewEntries = id ? data.vocabulary_import.entries : data.vocabulary_list.entries;
  renderReview();
}
if (reviewPage) {
  initReview().catch(error => toast(error.message, "error"));
  document.querySelector("#vocabularyReviewRows").addEventListener("input", event => {
    const row = event.target.closest("[data-index]");
    if (!row || !event.target.dataset.field) return;
    const entry = reviewEntries[Number(row.dataset.index)];
    const field = event.target.dataset.field;
    entry[field] = field === "included" ? event.target.checked
      : field === "alternatives" ? event.target.value.split(",").map(value => value.trim()).filter(Boolean)
      : event.target.value;
    entry.user_confirmed = true;
  });
  document.querySelector("#vocabularyReviewRows").addEventListener("click", async event => {
    const row = event.target.closest("[data-index]");
    if (!row) return;
    const index = Number(row.dataset.index);
    const entry = reviewEntries[index];
    if (event.target.closest("[data-remove]")) reviewEntries.splice(index, 1);
    if (event.target.closest("[data-accept-suggestion]")) {
      entry.target_translation = entry.suggested_translation;
      entry.user_confirmed = true;
      entry.status = "valid";
    }
    if (event.target.closest("[data-keep-original]")) {
      entry.suggested_translation = "";
      entry.user_confirmed = true;
    }
    if (event.target.closest("[data-split]")) {
      reviewEntries.splice(index + 1, 0, { ...entry, source_term: "", target_translation: "", user_confirmed: false });
    }
    if (event.target.closest("[data-merge]")) {
      const original = reviewEntries.find((candidate, candidateIndex) =>
        candidateIndex !== index
        && candidate.source_term?.toLocaleLowerCase() === entry.source_term?.toLocaleLowerCase());
      if (original) {
        original.alternatives = [...new Set([...(original.alternatives || []), entry.target_translation])];
        original.user_confirmed = true;
        reviewEntries.splice(index, 1);
      }
    }
    if (event.target.closest("[data-generate-example]")) {
      try {
        const generated = await api(`/api/vocabulary/imports/${reviewPage.dataset.importId}/example`, {
          method: "POST", body: { source_term: entry.source_term },
        });
        entry.source_example_sentence = generated.sentence;
        entry.example_ai_generated = true;
      } catch (error) { toast(error.message, "error"); }
    }
    renderReview();
  });
  document.querySelector("#selectAllVocabulary").addEventListener("click", () => {
    reviewEntries.forEach(entry => { entry.included = true; }); renderReview();
  });
  document.querySelector("#deselectAllVocabulary").addEventListener("click", () => {
    reviewEntries.forEach(entry => { entry.included = false; }); renderReview();
  });
  document.querySelector("#bulkAcceptVocabulary").addEventListener("click", () => {
    reviewEntries.forEach(entry => {
      if (entry.confidence >= 0.85 && ["valid", "likely_valid"].includes(entry.status)) entry.user_confirmed = true;
    });
    renderReview();
  });
  document.querySelector("#addVocabularyEntry").addEventListener("click", () => {
    reviewEntries.push({ source_term: "", target_translation: "", alternatives: [], included: true, status: "needs_review", confidence: 1 });
    renderReview();
  });
  document.querySelector("#generateVocabularyCards").addEventListener("click", async () => {
    try {
      await saveReview();
      if (!reviewPage.dataset.importId) {
        toast(t("vocabularySaved"), "success");
        return;
      }
      const directions = [...document.querySelectorAll("[name=direction]:checked")].map(item => item.value);
      const result = await api(`/api/vocabulary/imports/${reviewPage.dataset.importId}/generate`, {
        method: "POST", body: {
          directions, include_examples: document.querySelector("#includeVocabularyExamples").checked,
        },
      });
      location.href = result.creator_url;
    } catch (error) { toast(error.message, "error"); }
  });
}

function speak(item) {
  if (!("speechSynthesis" in window) || !item) return;
  speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(item.prompt);
  utterance.lang = item.language;
  utterance.volume = 0.75;
  speechSynthesis.speak(utterance);
}
function renderPractice() {
  const item = practiceItems[practiceIndex];
  if (!item) {
    document.querySelector("#vocabularyPracticeCard").innerHTML = `<h2>${escapeHtml(t("vocabularySessionComplete"))}</h2>`;
    return;
  }
  document.querySelector("#vocabularyPracticeProgress").textContent = `${practiceIndex + 1} / ${practiceItems.length}`;
  document.querySelector("#vocabularyPrompt").textContent = item.prompt;
  document.querySelector("#speakVocabulary").classList.toggle("hidden", !item.has_audio);
  document.querySelector("#vocabularyAnswer").value = "";
  document.querySelector("#vocabularyFeedback").textContent = "";
  practiceStarted = Date.now();
}
if (studyPage) {
  document.querySelector("#startVocabularyPractice").addEventListener("click", async () => {
    const direction = document.querySelector("#vocabularyDirection").value;
    const objective = document.querySelector("#vocabularyObjective").value;
    try {
      const data = await api(`/api/vocabulary/lists/${studyPage.dataset.listId}/practice?direction=${encodeURIComponent(direction)}&objective=${encodeURIComponent(objective)}`);
      practiceItems = data.items; practiceIndex = data.current_position || 0;
      practiceSessionId = data.session_id;
      document.querySelector("#vocabularyPracticeConfig").classList.add("hidden");
      document.querySelector("#vocabularyPracticeCard").classList.remove("hidden");
      renderPractice();
    } catch (error) { toast(error.message, "error"); }
  });
  document.querySelector("#checkVocabularyAnswer").addEventListener("click", async () => {
    const item = practiceItems[practiceIndex];
    if (!item) return;
    try {
      const result = await api(`/api/vocabulary/lists/${studyPage.dataset.listId}/practice/${item.entry_id}/answer`, {
        method: "POST", body: {
          answer: document.querySelector("#vocabularyAnswer").value,
          direction: item.direction,
          strictness: document.querySelector("#vocabularyStrictness").value,
          response_ms: Date.now() - practiceStarted, request_id: uuid(),
          session_id: practiceSessionId,
        },
      });
      document.querySelector("#vocabularyFeedback").textContent = result.correct
        ? `${t("fcCorrect")} · +${result.xp_earned} XP`
        : `${t("fcIncorrect")} · ${t("fcCorrectAnswer")}: ${result.expected}`;
      setTimeout(() => { practiceIndex += 1; renderPractice(); }, 700);
    } catch (error) { toast(error.message, "error"); }
  });
  document.querySelector("#speakVocabulary").addEventListener("click", () => speak(practiceItems[practiceIndex]));
  document.querySelector("#vocabularyAnswer").addEventListener("keydown", event => {
    if (event.key === "Enter") document.querySelector("#checkVocabularyAnswer").click();
  });
}

import { api, toast, escapeHtml, t, renderMath, SET_ID, selectedLanguage, FLAGS } from "./common.js";

const isDe = selectedLanguage === "German";
const STATUS_KEY = {
  pending_ai_review: "fcStatusPendingAi", pending_manual_review: "fcStatusPendingManual",
  approved: "fcStatusApproved", rejected: "fcStatusRejected", hidden: "fcStatusHidden",
  unpublished: "fcStatusUnpublished",
};

async function showPublicationState() {
  const badge = document.querySelector("#ovStatus");
  const publish = document.querySelector("#ovPublish");
  try {
    const state = await api(`/api/flashcards/sets/${SET_ID}/publication`);
    if (state.published && state.status !== "unpublished") {
      badge.textContent = t(STATUS_KEY[state.status] || state.status) || state.status;
      badge.className = `review-status status-${state.status}`;
      badge.classList.remove("hidden");
      if (publish) publish.textContent = t("fcViewStatus");
    } else {
      badge.textContent = t("fcStatusPrivate");
      badge.className = "review-status status-draft";
      badge.classList.remove("hidden");
    }
  } catch (_) { /* publication state is best-effort */ }
}

async function load() {
  try {
    const data = await api(`/api/flashcards/sets/${SET_ID}`);
    const set = data.set;
    document.querySelector("#ovTitle").textContent = set.title;
    document.querySelector("#ovMeta").textContent =
      `${set.subject} · ${t(set.difficulty) || set.difficulty}${set.grade ? " · " + (isDe ? "Klasse " : "Grade ") + set.grade : ""} · ${set.cards.length} ${set.cards.length === 1 ? t("fcCard") : t("fcCards")}`;
    const studyHref = `/flashcards/${SET_ID}/study`;
    const editHref = `/flashcards/${SET_ID}/edit`;
    document.querySelector("#ovStudy").href = studyHref;
    document.querySelector("#modeFlashcards").href = studyHref;
    document.querySelector("#ovEdit").href = editHref;
    document.querySelector("#ovCount").textContent = set.cards.length;
    const list = document.querySelector("#ovPreview");
    list.innerHTML = set.cards.map(card => `
      <li class="preview-item">
        <div class="preview-term">${escapeHtml(card.front)}</div>
        <div class="preview-def">${escapeHtml(card.back)}</div>
      </li>`).join("");
    renderMath(list);
    if (FLAGS.community_publishing) showPublicationState();
  } catch (error) { toast(error.message, "error"); }
}

function init() {
  document.querySelector("#ovShare").addEventListener("click", () => {
    const url = `${window.location.origin}/flashcards/${SET_ID}`;
    navigator.clipboard?.writeText(url).then(() => toast(t("fcCopied"), "success"), () => toast(url, "info"));
  });
  document.querySelector("#ovDelete").addEventListener("click", async () => {
    if (!window.confirm(t("fcConfirmDelete"))) return;
    try {
      await api(`/api/flashcards/sets/${SET_ID}`, { method: "DELETE" });
      toast(t("fcDeleted"), "success");
      window.location.href = "/flashcards";
    } catch (error) { toast(error.message, "error"); }
  });
  load();
}

document.addEventListener("DOMContentLoaded", init);

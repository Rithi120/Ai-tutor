import { api, toast, escapeHtml, t, renderMath, SET_ID, selectedLanguage, FLAGS, publicationBadge } from "./common.js";

const isDe = selectedLanguage === "German";
// Where "Share" points: the public page while the set is really public, else the owner page.
let publicUrl = null;

async function showPublicationState() {
  const badge = document.querySelector("#ovStatus");
  const publish = document.querySelector("#ovPublish");
  try {
    const state = await api(`/api/flashcards/sets/${SET_ID}/publication`);
    // The label is the server's visibility (same rule as the public library), not the
    // moderation word: "Published", "Being checked", "Private" ...
    const view = publicationBadge(state);
    badge.textContent = view.text;
    badge.className = `review-status ${view.className}`;
    badge.dataset.visibility = view.visibility;
    badge.classList.remove("hidden");
    publicUrl = state.is_public && state.public_url ? state.public_url : null;
    if (publish) publish.textContent = view.visibility === "private" ? t("fcPublish") : t("fcViewStatus");
  } catch (_) { /* publication state is best-effort */ }
}

async function load() {
  try {
    const data = await api(`/api/flashcards/sets/${SET_ID}`);
    const set = data.set;
    document.querySelector("#ovTitle").textContent = set.title;
    document.querySelector("#ovMeta").textContent =
      `${set.subject} · ${t(set.difficulty) || set.difficulty}${set.grade ? " · " + (isDe ? "Klasse " : "Grade ") + set.grade : ""} · ${set.cards.length} ${set.cards.length === 1 ? t("fcCard") : t("fcCards")}`;
    // "Start learning", "Edit" and the Flashcards mode card are rendered with real
    // hrefs by the template. They used to be href="#" and were pointed somewhere by
    // this function, which meant that until it finished - and forever if it threw -
    // clicking them did nothing at all, silently. A link to a URL the server already
    // knows should never depend on a fetch succeeding.
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
    const url = `${window.location.origin}${publicUrl || `/flashcards/${SET_ID}`}`;
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

// Back from the publish page restores this page from the back/forward cache with the old
// badge ("Private" right after publishing); read the real state again instead.
window.addEventListener("pageshow", event => {
  if (event.persisted && FLAGS.community_publishing) showPublicationState();
});
document.addEventListener("DOMContentLoaded", init);

import { api, toast, escapeHtml, t, optionsHtml, SUBJECTS, selectedLanguage, FLAGS } from "./common.js";

const isDe = selectedLanguage === "German";
const LABEL = {
  study: isDe ? "Lernen" : "Study",
  edit: isDe ? "Bearbeiten" : "Edit",
  private: isDe ? "Privat" : "Private",
};
let allSets = [];

async function load() {
  const loading = document.querySelector("#setsLoading");
  const grid = document.querySelector("#setsGrid");
  const empty = document.querySelector("#setsEmpty");
  try {
    const data = await api("/api/flashcards/sets");
    allSets = data.sets || [];
    loading.hidden = true;
    if (!allSets.length) { empty.classList.remove("hidden"); return; }
    grid.hidden = false;
    render();
  } catch (error) {
    loading.hidden = true;
    toast(error.message, "error");
  }
}

function render() {
  const grid = document.querySelector("#setsGrid");
  const search = document.querySelector("#setSearch").value.trim().toLowerCase();
  const subject = document.querySelector("#setSubjectFilter").value;
  const difficulty = document.querySelector("#setDifficultyFilter").value;
  const sort = document.querySelector("#setSort").value;
  let sets = allSets.filter(s =>
    (!subject || s.subject === subject) &&
    (!difficulty || s.difficulty === difficulty) &&
    (!search || s.title.toLowerCase().includes(search) || s.subject.toLowerCase().includes(search)));
  const sorters = {
    newest: (a, b) => b.updated_at.localeCompare(a.updated_at),
    oldest: (a, b) => a.updated_at.localeCompare(b.updated_at),
    title: (a, b) => a.title.localeCompare(b.title),
  };
  sets.sort(sorters[sort] || sorters.newest);
  grid.innerHTML = sets.map(set => `
    <article class="set-card" data-subject="${escapeHtml(set.subject.toLowerCase())}">
      <a class="set-card-link" href="/flashcards/${set.id}">
        <span class="ln-status">${escapeHtml(LABEL.private)}</span>
        <h3>${escapeHtml(set.title)}</h3>
        <p class="set-meta">${escapeHtml(set.subject)} · ${escapeHtml(t(set.difficulty) || set.difficulty)}</p>
        <p class="set-stats">${set.total} ${set.total === 1 ? t("fcCard") : t("fcCards")}${set.due ? ` · <b>${set.due} ${t("fcDue")}</b>` : ""}${set.mastered ? ` · ${set.mastered} ${t("fcMastered")}` : ""}</p>
      </a>
      <div class="set-actions">
        <a class="primary-button" href="/flashcards/${set.id}/study">${escapeHtml(LABEL.study)}</a>
        <a class="ghost-button" href="/flashcards/${set.id}/edit">${escapeHtml(LABEL.edit)}</a>
        ${FLAGS.community_publishing ? `<a class="ghost-button" href="/flashcards/${set.id}/publish">${escapeHtml(t("fcPublish"))}</a>` : ""}
      </div>
    </article>`).join("");
}

function init() {
  document.querySelector("#setSubjectFilter").insertAdjacentHTML("beforeend", optionsHtml(SUBJECTS));
  ["setSearch", "setSubjectFilter", "setDifficultyFilter", "setSort"].forEach(id =>
    document.querySelector(`#${id}`).addEventListener("input", render));
  load();
}

document.addEventListener("DOMContentLoaded", init);

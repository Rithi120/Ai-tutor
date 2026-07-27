import { api, toast, t, renderMath, SET_ID } from "./common.js";

const state = { cards: [], order: [], index: 0, flipped: false, correct: 0, incorrect: 0, results: {}, title: "", starred: new Set() };

async function start(only) {
  try {
    const data = await api(`/api/flashcards/sets/${SET_ID}`);
    let cards = data.set.cards;
    if (only === "difficult") cards = cards.filter(c => ["new", "learning"].includes(c.mastery_level) || c.ease_factor < 2.0);
    if (only === "incorrect") cards = cards.filter(c => state.results[c.id] === "again");
    if (!cards.length) { toast(t("fcNoCards"), "info"); return; }
    state.cards = cards;
    state.title = data.set.title;
    state.order = cards.map((_, i) => i);
    state.index = 0; state.correct = 0; state.incorrect = 0; state.flipped = false; state.results = {};
    document.querySelector("#studyTitle").textContent = data.set.title;
    document.querySelector("#studyMeta").textContent = `${cards.length} ${cards.length === 1 ? t("fcCard") : t("fcCards")}`;
    document.querySelector("#studyCorrect").textContent = "0";
    document.querySelector("#studyIncorrect").textContent = "0";
    document.querySelector("#studySummary").classList.add("hidden");
    document.querySelector("#studyArea").classList.remove("hidden");
    renderCard();
  } catch (error) { toast(error.message, "error"); }
}

function current() { return state.cards[state.order[state.index]]; }

function renderCard() {
  const card = current();
  if (!card) return;
  state.flipped = false;
  const front = document.querySelector("#studyFront");
  const back = document.querySelector("#studyBack");
  front.textContent = card.front;
  back.textContent = card.back;
  document.querySelector("#studyCard").classList.remove("flipped");
  document.querySelector("#studyGrades").classList.add("hidden");
  document.querySelector("#studyFlip").textContent = t("fcShowAnswer");
  const hint = document.querySelector("#studyHintText");
  hint.textContent = card.hint || "";
  hint.classList.add("hidden");
  const star = document.querySelector("#starButton");
  const isStarred = state.starred.has(card.id);
  star.textContent = isStarred ? "★" : "☆";
  star.setAttribute("aria-pressed", String(isStarred));
  document.querySelector("#studyPos").textContent = `${state.index + 1} / ${state.cards.length}`;
  document.querySelector("#studyBar").style.width = `${(state.index / state.cards.length) * 100}%`;
  renderMath(front); renderMath(back);
}

function flip() {
  state.flipped = !state.flipped;
  document.querySelector("#studyCard").classList.toggle("flipped", state.flipped);
  document.querySelector("#studyGrades").classList.toggle("hidden", !state.flipped);
  document.querySelector("#studyFlip").textContent = state.flipped ? t("fcHideAnswer") : t("fcShowAnswer");
}

async function grade(value) {
  const card = current();
  if (!card) return;
  state.results[card.id] = value;
  if (value === "again") state.incorrect += 1; else state.correct += 1;
  document.querySelector("#studyCorrect").textContent = state.correct;
  document.querySelector("#studyIncorrect").textContent = state.incorrect;
  try { await api(`/api/flashcards/cards/${card.id}/review`, { method: "POST", body: { grade: value } }); }
  catch (error) { toast(error.message, "warn"); }
  advance();
}

function advance() {
  if (state.index >= state.cards.length - 1) { finish(); return; }
  state.index += 1; renderCard();
}

function finish() {
  document.querySelector("#studyArea").classList.add("hidden");
  document.querySelector("#studyBar").style.width = "100%";
  document.querySelector("#studySummaryText").textContent =
    `${state.cards.length} ${t("fcCards")} · ✓ ${state.correct} · ✗ ${state.incorrect}`;
  document.querySelector("#practiceIncorrect").classList.toggle("hidden", !Object.values(state.results).includes("again"));
  document.querySelector("#studySummary").classList.remove("hidden");
}

function toggleFullscreen() {
  const page = document.querySelector("#studyPage");
  if (!document.fullscreenElement) page.requestFullscreen?.(); else document.exitFullscreen?.();
}

function onKeydown(event) {
  if (event.key === " " || event.key === "Enter") { event.preventDefault(); flip(); }
  else if (event.key === "ArrowRight") advance();
  else if (event.key === "ArrowLeft" && state.index > 0) { state.index -= 1; renderCard(); }
  else if (state.flipped && ["1", "2", "3", "4"].includes(event.key)) grade(["again", "hard", "good", "easy"][Number(event.key) - 1]);
}

function init() {
  document.querySelector("#studyFlip").addEventListener("click", flip);
  document.querySelector("#studyCard").addEventListener("click", event => { if (!event.target.closest(".star-toggle")) flip(); });
  document.querySelector("#studyCard").addEventListener("keydown", event => { if (event.key === " " || event.key === "Enter") { event.preventDefault(); flip(); } });
  document.querySelector("#studyNext").addEventListener("click", advance);
  document.querySelector("#studyPrev").addEventListener("click", () => { if (state.index > 0) { state.index -= 1; renderCard(); } });
  document.querySelector("#studyShuffle").addEventListener("click", () => {
    for (let i = state.order.length - 1; i > 0; i--) { const j = (i * 2654435761) % (i + 1); [state.order[i], state.order[j]] = [state.order[j], state.order[i]]; }
    state.index = 0; renderCard(); toast(t("fcShuffled"), "info");
  });
  document.querySelector("#studyHint").addEventListener("click", () => document.querySelector("#studyHintText").classList.toggle("hidden"));
  document.querySelector("#studyGrades").addEventListener("click", event => { const g = event.target.closest("[data-grade]"); if (g) grade(g.dataset.grade); });
  document.querySelector("#starButton").addEventListener("click", event => {
    event.stopPropagation();
    const card = current(); if (!card) return;
    if (state.starred.has(card.id)) state.starred.delete(card.id); else state.starred.add(card.id);
    renderCard();
  });
  document.querySelector("#studyFull").addEventListener("click", toggleFullscreen);
  document.querySelector("#restart").addEventListener("click", () => start());
  document.querySelector("#practiceIncorrect").addEventListener("click", () => start("incorrect"));
  document.querySelector("#practiceDifficult").addEventListener("click", () => start("difficult"));
  document.addEventListener("keydown", onKeydown);
  start();
}

document.addEventListener("DOMContentLoaded", init);

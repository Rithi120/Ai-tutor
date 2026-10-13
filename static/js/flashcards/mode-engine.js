import { api, toast, t, renderMath, SET_ID, FLAGS } from "./common.js";
import { safeUUID } from "../dom.js";
import { matchPair, nextLives, scoreAnswer, shouldReduceMotion } from "./mode-rules.js";
import { announceXp } from "../xp.js";

const MODE = window.LEARNOVA_MODE;
const resultSession = window.LEARNOVA_RESULT_SESSION;
const state = { session: null, index: 0, started: 0, sessionStarted: 0, combo: 0, maxCombo: 0, lives: 3, selected: null, timer: null, paused: false, locked: false, flipped: false, finishing: false, touchX: null };
const $ = value => document.querySelector(value);

function error(message = "") { $("#modeError").textContent = message; $("#modeError").classList.toggle("hidden", !message); }
function current() { return state.session?.items[state.index]; }
function updateCounters() {
  const s = state.session;
  $("#modeCorrect").textContent = s.correct_count;
  $("#modeIncorrect").textContent = s.incorrect_count;
  $("#modePosition").textContent = `${Math.min(state.index + 1, s.total_items)} / ${s.total_items}`;
  $("#modeBar").style.width = `${100 * s.answered_items / Math.max(1, s.total_items)}%`;
  $("#modeCombo") && ($("#modeCombo").textContent = state.combo);
  $("#modeLives") && ($("#modeLives").textContent = state.lives);
  const level = $("#modeLevel");
  if (level) level.textContent = String(1 + Math.floor(s.correct_count / 3));
}
function renderItem() {
  const item = current();
  if (!item) return finish();
  state.locked = false;
  $("#modeFeedback").classList.add("hidden");
  $("#modePrompt").textContent = item.prompt;
  state.flipped = false;
  $("#modeAnswer").textContent = "";
  $("#modeAnswer").classList.add("hidden");
  $("#modeStar").textContent = item.starred ? "★" : "☆";
  const written = ["written", "fill_blank", "front_to_back", "back_to_front"].includes(item.question_type);
  $("#writtenWrap").classList.toggle("hidden", !written);
  $("#submitModeAnswer").classList.toggle("hidden", !written);
  $("#selfGrades").classList.add("hidden");
  // Tapping the card or the "Show answer" button flips it; then Wrong/Correct.
  $("#standardQuestion").classList.toggle("flashcard-tappable", item.question_type === "self_grade");
  $("#flipCard").classList.toggle("hidden", item.question_type !== "self_grade");
  $("#nextCard").classList.remove("next-ready");
  $("#flashcardControls").classList.toggle("hidden", item.question_type !== "self_grade");
  $("#modeOptions").innerHTML = written || item.question_type === "self_grade" ? "" :
    (item.options || []).map(value => {
      // The value stays raw (server compares it); only the visible label is localized.
      const label = value === "true" ? t("fcTrue") : value === "false" ? t("fcFalse") : value;
      return `<button type="button" data-answer="${escapeAttr(value)}">${escapeText(label)}</button>`;
    }).join("");
  if (MODE === "match") renderMatch();
  renderMath($("#standardQuestion"));
  updateCounters();
}
function escapeText(value) { const span = document.createElement("span"); span.textContent = value; return span.innerHTML; }
function escapeAttr(value) { return escapeText(value).replaceAll('"', "&quot;"); }
function renderMatch() {
  $("#standardQuestion").classList.add("hidden");
  const remaining = state.session.items.filter(item => !item.answered);
  const tiles = remaining.flatMap(item => [
    { cardId: item.card_id, side: "front", text: item.prompt },
    { cardId: item.card_id, side: "back", text: item.correct_answer },
  ]).sort((a, b) => ((a.cardId * 17 + (a.side === "front" ? 1 : 7)) % 23) - ((b.cardId * 17 + (b.side === "front" ? 1 : 7)) % 23));
  $("#matchBoard").classList.remove("hidden");
  $("#matchBoard").innerHTML = tiles.map(tile => `<button role="gridcell" data-card="${tile.cardId}" data-side="${tile.side}">${escapeText(tile.text)}</button>`).join("");
  renderMath($("#matchBoard"));
}
async function submit(answer, item = current()) {
  if (!item || state.locked || state.paused) return;
  state.locked = true;
  try {
    const responseMs = Math.max(150, Date.now() - state.started);
    const data = await api(`/api/flashcards/sessions/${state.session.id}/items/${item.id}/answer`, { method: "POST", body: {
      answer, response_ms: responseMs, request_id: safeUUID(),
    } });
    announceXp(data, `item:${item.id}`);
    if (data.duplicate) {
      // A replay (double-tap, navigation race) earns nothing and must not notify or
      // bump the counters a second time.
      state.locked = false;
      return;
    }
    if (MODE === "test") {
      item.answered = true; item.student_answer = answer; state.session.answered_items++;
      state.index++; state.started = Date.now(); renderItem(); return;
    }
    item.answered = true; item.correct = data.correct;
    state.session.answered_items++; state.session.correct_count += data.correct ? 1 : 0;
    state.session.incorrect_count += data.correct ? 0 : 1;
    if (MODE === "match") {
      // Matched pairs simply leave the board; no question-card feedback/advance.
      updateCounters();
      state.locked = false;
      if (state.session.items.every(entry => entry.answered)) { window.setTimeout(finish, 250); }
      else renderMatch();
      return;
    }
    const scored = scoreAnswer(data.correct, state.combo, MODE);
    state.combo = scored.combo; state.maxCombo = Math.max(state.maxCombo, state.combo);
    state.lives = nextLives(state.lives, data.correct);
    const feedback = $("#modeFeedback");
    feedback.innerHTML = `<strong>${escapeText(data.correct ? t("fcCorrect") : t("fcIncorrect"))}</strong><p>${escapeText(t("fcCorrectAnswer"))}: ${escapeText(data.correct_answer)}</p>${FLAGS.gamification_enabled ? `<p>${escapeText(t("fcXpEarned"))}: +${data.xp_earned}</p>` : ""}`;
    feedback.classList.remove("hidden");
    renderMath(feedback);
    updateCounters();
    if (MODE === "flashcards") {
      // The page waits: the student reads the answer in peace, and "Next" lights up
      // in the Learnova violet to carry them to the following card.
      $("#selfGrades").classList.add("hidden");
      $("#nextCard").classList.add("next-ready");
      state.locked = false;
      return;
    }
    if (["blast", "blocks"].includes(MODE) && state.lives <= 0) {
      window.setTimeout(finish, 250);
      return;
    }
    window.setTimeout(() => { state.index++; state.started = Date.now(); renderItem(); }, shouldReduceMotion(matchMedia("(prefers-reduced-motion: reduce)").matches, $("#reducedMotion").checked) ? 0 : 550);
  } catch (e) { error(e.message); state.locked = false; }
}
async function start(resume = true) {
  error("");
  try {
    // "All cards" is sent as 0: the server reads it as "every card in the set".
    const wantsAll = $("#modeCountChoice").value === "all";
    const data = await api(`/api/flashcards/sets/${SET_ID}/sessions`, { method: "POST", body: {
      mode: MODE, objective: $("#modeObjective").value,
      count: wantsAll ? 0 : Math.max(1, Number($("#modeCount").value) || 1),
      time_limit: Number($("#modeTime")?.value || 0) * 60,
      direction: $("#modeDirection")?.value || "mixed", resume,
      reduced_motion: $("#reducedMotion").checked, idempotency_key: safeUUID(),
    } });
    state.session = data.session;
    state.index = Math.min(state.session.current_position || 0, state.session.items.length - 1);
    $("#modeConfig").classList.add("hidden"); $("#modePlay").classList.remove("hidden");
    state.started = Date.now();
    state.sessionStarted = Date.now() - ((state.session.active_seconds || 0) * 1000);
    renderItem(); startTimer();
  } catch (e) { error(e.message); }
}
function startTimer() {
  clearInterval(state.timer);
  state.timer = setInterval(() => {
    if (state.paused || !state.session) return;
    const seconds = Math.floor((Date.now() - state.sessionStarted) / 1000);
    $("#modeTimer").textContent = `${seconds}s`;
    const limit = state.session.settings.time_limit;
    if (limit && seconds >= limit) finish();
  }, 1000);
}
async function finish() {
  if (!state.session || state.session.status === "completed" || state.finishing) return;
  state.finishing = true;
  clearInterval(state.timer);
  try {
    const data = await api(`/api/flashcards/sessions/${state.session.id}/complete`, { method: "POST", body: { max_combo: state.maxCombo } });
    announceXp({ xp_earned: 0, xp: data.xp }, `complete:${state.session.id}`);
    state.session = data.session; showSummary();
  } catch (e) { error(e.message); state.finishing = false; }
}
function showSummary() {
  $("#modePlay").classList.add("hidden"); $("#modeSummary").classList.remove("hidden");
  const s = state.session;
  $("#summaryStats").innerHTML = `<div><b>${s.score}</b><span>${escapeText(t("fcScore"))}</span></div><div><b>${s.accuracy}%</b><span>${escapeText(t("fcAccuracy"))}</span></div><div><b>${s.active_seconds}s</b><span>${escapeText(t("fcStudyTime"))}</span></div>${FLAGS.gamification_enabled ? `<div><b>+${s.xp_earned}</b><span>${escapeText(t("fcXp"))}</span></div>` : ""}`;
  renderMath($("#summaryStats"));
  // "Review mistakes" must not appear when the session had no incorrect answers.
  const hadMistakes = (s.incorrect_count || 0) > 0;
  $("#reviewWeak")?.classList.toggle("hidden", !hadMistakes);
  const nextAction = document.querySelector(".summary-next-action");
  if (nextAction) nextAction.classList.toggle("hidden", !hadMistakes);
  if (MODE === "test") {
    $("#resultReview").innerHTML = s.items.map(item => `<article class="result-item ${item.correct ? "correct" : "incorrect"}"><b>${escapeText(item.prompt)}</b><p>${escapeText(t("fcYourAnswer"))}: ${escapeText(item.student_answer || "—")}</p><p>${escapeText(t("fcCorrectAnswer"))}: ${escapeText(item.correct_answer)}</p></article>`).join("");
    renderMath($("#resultReview"));
  }
}
async function onMatch(event) {
  const button = event.target.closest("[data-card]");
  if (!button || state.locked) return;
  const tile = { cardId: Number(button.dataset.card), side: button.dataset.side, button };
  if (!state.selected) { state.selected = tile; button.classList.add("selected"); return; }
  const first = state.selected; state.selected = null;
  if (matchPair(first, tile)) {
    const item = state.session.items.find(value => value.card_id === tile.cardId);
    first.button.disabled = button.disabled = true; submit(item.correct_answer, item);
  } else {
    state.combo = 0;
    first.button.classList.remove("selected"); button.classList.add("wrong");
    setTimeout(() => button.classList.remove("wrong"), 350);
    try {
      const data = await api(`/api/flashcards/sessions/${state.session.id}/miss`, { method: "POST", body: {} });
      state.session.incorrect_count = data.incorrect_count;
      updateCounters();
    } catch (e) {
      error(e.message);
    }
  }
}
function flipCard() {
  const item = current();
  if (!item || item.question_type !== "self_grade") return;
  state.flipped = true;
  $("#modeAnswer").textContent = item.correct_answer;
  $("#modeAnswer").classList.remove("hidden");
  $("#flipCard").classList.add("hidden");
  // An already-graded card only shows its answer again; re-grading would double-count.
  if (item.answered) $("#nextCard").classList.add("next-ready");
  else $("#selfGrades").classList.remove("hidden");
  renderMath($("#modeAnswer"));
}
function navigate(delta) {
  if (!state.session || MODE !== "flashcards") return;
  const total = state.session.items.length;
  state.index = (state.index + delta + total) % total;
  state.started = Date.now();
  renderItem();
}
function advance() {
  if (!state.session || MODE !== "flashcards") return;
  // Once every card is graded, "Next" completes the session instead of looping.
  if (state.session.items.every(item => item.answered)) return finish();
  navigate(1);
}
function shuffleCards() {
  if (!state.session || MODE !== "flashcards") return;
  for (let i = state.session.items.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [state.session.items[i], state.session.items[j]] = [state.session.items[j], state.session.items[i]];
  }
  state.index = 0;
  renderItem();
}
async function loadResult() {
  const data = await api(`/api/flashcards/sessions/${resultSession}`);
  state.session = data.session; $("#modeConfig").classList.add("hidden"); showSummary();
}
async function offerVocabularyScopes() {
  // Words only / sentences only make sense when the set has both; cards carry their kind as a tag.
  try {
    const data = await api(`/api/flashcards/sets/${SET_ID}`);
    const cards = data.set?.cards || [];
    // The settings say how big the set is, so "All cards" is an informed choice.
    const total = $("#modeTotal");
    if (total && cards.length) {
      total.textContent = t("fcSetTotal", { count: cards.length });
      total.classList.remove("hidden");
    }
    const sentences = cards.filter(card => (card.tags || []).includes("sentence")).length;
    if (sentences > 0 && sentences < cards.length) {
      document.querySelectorAll("#modeObjective [data-vocabulary-only]").forEach(option => { option.hidden = false; });
    }
  } catch (e) { /* the ordinary objectives still work */ }
}
function init() {
  offerVocabularyScopes();
  const objective = new URLSearchParams(location.search).get("objective"); if (objective) $("#modeObjective").value = objective;
  $("#startMode").addEventListener("click", () => start(false));
  $("#continueMode").addEventListener("click", () => start(true));
  $("#modeCountChoice").addEventListener("change", e => {
    $("#modeCountField").classList.toggle("hidden", e.currentTarget.value !== "custom");
  });
  $("#modeOptions").addEventListener("click", e => { const b = e.target.closest("[data-answer]"); if (b) submit(b.dataset.answer); });
  $("#selfGrades").addEventListener("click", e => { const b = e.target.closest("[data-answer]"); if (b) submit(b.dataset.answer); });
  $("#submitModeAnswer").addEventListener("click", () => { const value = $("#writtenAnswer").value; if (value.trim()) { $("#writtenAnswer").value = ""; submit(value); } });
  $("#modeStar").addEventListener("click", async () => { const item = current(); item.starred = !item.starred; await api(`/api/flashcards/cards/${item.card_id}/star`, { method: "PUT", body: { starred: item.starred } }); renderItem(); });
  $("#matchBoard").addEventListener("click", onMatch);
  // The card itself flips too: one tap anywhere on it that is not a control.
  $("#standardQuestion").addEventListener("click", e => {
    if (MODE !== "flashcards" || state.flipped) return;
    if (e.target.closest("button, a, input, label")) return;
    flipCard();
  });
  $("#flipCard").addEventListener("click", flipCard);
  $("#previousCard").addEventListener("click", () => navigate(-1));
  $("#nextCard").addEventListener("click", advance);
  $("#shuffleCards").addEventListener("click", shuffleCards);
  $("#pauseMode").addEventListener("click", async () => { state.paused = !state.paused; $("#pauseMode").textContent = state.paused ? t("fcResume") : t("fcPause"); await api(`/api/flashcards/sessions/${state.session.id}/${state.paused ? "pause" : "resume"}`, { method: "POST", body: {} }); });
  $("#soundToggle").addEventListener("click", e => { const on = e.currentTarget.getAttribute("aria-pressed") !== "true"; e.currentTarget.setAttribute("aria-pressed", String(on)); e.currentTarget.textContent = on ? t("fcSoundOn") : t("fcSoundOff"); });
  $("#retryMode").addEventListener("click", () => location.reload());
  document.addEventListener("keydown", e => {
    if (e.key === "Enter" && !$("#writtenWrap").classList.contains("hidden")) $("#submitModeAnswer").click();
    if (e.key === "Escape" && state.session && !document.fullscreenElement) $("#pauseMode").click();
    if (MODE === "flashcards" && e.key === " ") { e.preventDefault(); flipCard(); }
    if (MODE === "flashcards" && e.key === "ArrowLeft") navigate(-1);
    if (MODE === "flashcards" && e.key === "ArrowRight") advance();
    // Self-grading is a single honest question: did you know it? 1 = wrong, 2 = correct.
    if (MODE === "flashcards" && state.flipped && ["1", "2"].includes(e.key)) {
      submit(e.key === "1" ? "again" : "good");
    }
  });
  $("#standardQuestion").addEventListener("touchstart", e => { state.touchX = e.changedTouches[0]?.clientX ?? null; }, { passive: true });
  $("#standardQuestion").addEventListener("touchend", e => {
    if (MODE !== "flashcards" || state.touchX === null) return;
    const delta = (e.changedTouches[0]?.clientX ?? state.touchX) - state.touchX;
    if (Math.abs(delta) > 60) navigate(delta > 0 ? -1 : 1);
    state.touchX = null;
  }, { passive: true });
  if (resultSession) loadResult();
}
document.addEventListener("DOMContentLoaded", init);

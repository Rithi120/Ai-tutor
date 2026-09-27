import { escapeHtml } from "./dom.js";

const SUBJECTS = ["Mathematics", "English", "German", "History", "Biology", "Chemistry", "Physics", "Other"];
const MODERATION_ENABLED =
  document.querySelector("#main-content")?.dataset.moderationEnabled === "true";
// Mirrors learnova.moderation.taxonomy.REPORT_REASONS. The server validates against that
// list, so an unknown value here is refused rather than silently stored.
const REPORT_REASONS = [
  ["incorrect", "Incorrect information", "Falsche Informationen"],
  ["offensive", "Offensive content", "Anstößiger Inhalt"],
  ["harassment", "Harassment or bullying", "Belästigung oder Mobbing"],
  ["sexual", "Sexual content", "Sexueller Inhalt"],
  ["dangerous", "Dangerous content", "Gefährlicher Inhalt"],
  ["personal_information", "Personal information", "Persönliche Daten"],
  ["spam", "Spam or advertising", "Spam oder Werbung"],
  ["copyright", "Copyright problem", "Urheberrechtsproblem"],
  ["other", "Something else", "Etwas anderes"],
];
const tx = (en, de) => window.LEARNOVA_LANGUAGE === "de" ? de : en;
const MATH_DELIMITERS = [
  { left: "$$", right: "$$", display: true },
  { left: "\\[", right: "\\]", display: true },
  { left: "$", right: "$", display: false },
  { left: "\\(", right: "\\)", display: false },
];

function renderMath(root) {
  if (!root || typeof window.renderMathInElement !== "function") return;
  try {
    window.renderMathInElement(root, { delimiters: MATH_DELIMITERS, throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option", "input"] });
  } catch (_) { /* ignore */ }
}

async function api(url, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* non-JSON */ }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.code = data.code; error.status = response.status; throw error;
  }
  return data;
}

let toastTimer = null;
function toast(message, kind = "info") {
  const el = document.querySelector("#commToast");
  el.textContent = message;
  el.className = `toast toast-${kind}`;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.add("hidden"), 4200);
}

function showView(id) {
  document.querySelectorAll(".community-page .cards-view").forEach(v => v.classList.toggle("hidden", v.id !== id));
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ------------------------- ratings display (kept separate) ------------------------- */
function aiRatingHtml(ai) {
  return `<span class="rating-chip ai" title="${tx("AI quality review", "KI-Qualitätsprüfung")}"><span aria-hidden="true">AI</span> ${ai.overall ? ai.overall.toFixed(1) : "—"}/5 · ${escapeHtml(ai.confidence || "—")}</span>`;
}
function studentRatingHtml(student) {
  if (!student.count) return `<span class="rating-chip student muted">${tx("No student ratings yet", "Noch keine Bewertungen")}</span>`;
  return `<span class="rating-chip student" title="${tx("Student rating", "Bewertung von Lernenden")}">★ ${student.average.toFixed(1)}/5 · ${student.count} ${student.count === 1 ? tx("rating", "Bewertung") : tx("ratings", "Bewertungen")}</span>`;
}

/* ------------------------- library ------------------------- */
async function loadLibrary() {
  const params = new URLSearchParams();
  new FormData(document.querySelector("#commFilters")).forEach((value, key) => { if (value) params.set(key, value); });
  const grid = document.querySelector("#commGrid");
  const loading = document.querySelector("#commLoading");
  const empty = document.querySelector("#commEmpty");
  loading.classList.remove("hidden"); grid.innerHTML = ""; empty.classList.add("hidden");
  try {
    const data = await api(`/api/community/library?${params.toString()}`);
    loading.classList.add("hidden");
    const sets = data.sets || [];
    if (!sets.length) { empty.classList.remove("hidden"); return; }
    grid.innerHTML = sets.map(cardHtml).join("");
  } catch (error) {
    loading.classList.add("hidden");
    toast(error.message, "error");
  }
}

async function loadShelf(targetId, sort) {
  const target = document.querySelector(`#${targetId}`);
  if (!target) return;
  target.innerHTML = `<div class="cards-skeleton shelf-skeleton" aria-hidden="true"><i></i><i></i><i></i></div>`;
  try {
    const data = await api(`/api/community/library?sort=${encodeURIComponent(sort)}`);
    const sets = (data.sets || []).slice(0, 6);
    target.innerHTML = sets.length
      ? sets.map(cardHtml).join("")
      : `<p class="cards-empty">${window.LEARNOVA_LANGUAGE === "de" ? "Noch keine Sets in diesem Bereich." : "No sets in this collection yet."}</p>`;
  } catch (error) {
    target.innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
  }
}

function loadDiscoveryShelves() {
  return Promise.all([
    loadShelf("commTrending", "trending"),
    loadShelf("commTopRated", "student"),
    loadShelf("commNewest", "newest"),
  ]);
}

function cardHtml(set) {
  const excerpt = set.description ? escapeHtml(set.description.slice(0, 120)) + (set.description.length > 120 ? "…" : "") : "";
  return `
    <article class="set-card community-card" data-subject="${escapeHtml(set.subject.toLowerCase())}" data-open="${set.id}" tabindex="0" role="button" aria-label="${tx("Open", "Öffnen")}: ${escapeHtml(set.title)}">
      <div class="community-card-top">
        <h3>${escapeHtml(set.title)}</h3>
        ${set.teacher_verified ? `<span class="badge verified" title="${tx("Teacher verified", "Von Lehrkraft verifiziert")}">✓ ${tx("Verified", "Verifiziert")}</span>` : ""}
      </div>
      <p class="set-meta"><span class="subject-accent">${escapeHtml(set.subject)}</span>${set.topic ? " · " + escapeHtml(set.topic) : ""} · ${escapeHtml(set.difficulty)}${set.grade ? ` · ${tx("Grade", "Klasse")} ` + escapeHtml(set.grade) : ""}</p>
      ${excerpt ? `<p class="set-excerpt">${excerpt}</p>` : ""}
      <div class="rating-row">${aiRatingHtml(set.ai_review)}${studentRatingHtml(set.student_rating)}</div>
      <p class="set-stats">${set.card_count} ${tx("cards", "Karten")}${set.study_count ? ` · ${set.study_count} ${tx("studied", "gelernt")}` : ""}${set.save_count ? ` · ${set.save_count} ${tx("saved", "gespeichert")}` : ""} · ${tx("by", "von")} ${escapeHtml(set.author)}</p>
    </article>`;
}

/* ------------------------- detail ------------------------- */
const local = { cards: [], index: 0, flipped: false, setId: null, studied: false };

async function openDetail(setId, updateHistory = true) {
  try {
    const data = await api(`/api/community/sets/${setId}`);
    renderDetail(data.set);
    showView("commDetail");
    if (updateHistory) history.pushState({ communitySet: Number(setId) }, "", data.set.public_url || `/community/sets/${setId}`);
  } catch (error) { toast(error.message, "error"); }
}

function renderDetail(set) {
  local.cards = set.cards || [];
  local.index = 0; local.flipped = false; local.setId = set.id; local.studied = false;
  document.querySelector("#detailTitle").textContent = set.title;
  const ai = set.ai_review_detail;
  const shareUrl = `${window.location.origin}${set.public_url || `/community/sets/${set.id}`}`;
  const container = document.querySelector("#detailContent");
  container.innerHTML = `
    <div class="detail-grid">
      <div class="detail-main">
        <p class="set-meta">${escapeHtml(set.subject)}${set.topic ? " · " + escapeHtml(set.topic) : ""} · ${escapeHtml(set.difficulty)}${set.grade ? " · Grade " + escapeHtml(set.grade) : ""} · by ${escapeHtml(set.author)}</p>
        ${set.description ? `<p class="detail-description">${escapeHtml(set.description)}</p>` : ""}
        <div class="rating-row large">${aiRatingHtml(set.ai_review)}${studentRatingHtml(set.student_rating)}</div>
        <p class="rating-explainer">${tx("The AI review checks quality; student ratings reflect the learning experience.", "Die KI-Prüfung bewertet die Qualität; Bewertungen von Lernenden zeigen die Lernerfahrung.")}</p>

        <div class="detail-actions">
          <button type="button" class="primary-button" id="detailStudy">${tx("Begin studying", "Lernen starten")}</button>
          <button type="button" class="ghost-button" id="detailSave">${tx("Save a personal copy", "Persönliche Kopie speichern")}</button>
          <button type="button" class="ghost-button" id="detailShare">${tx("Copy link", "Link kopieren")}</button>
          ${MODERATION_ENABLED ? `<button type="button" class="ghost-button subtle-button" id="detailReport">${tx("Report this set", "Dieses Set melden")}</button>` : ""}
        </div>
        ${MODERATION_ENABLED ? `<form id="reportPanel" class="report-panel hidden">
          <p class="report-question">${tx("Why are you reporting this?", "Warum meldest du das?")}</p>
          <div class="report-reasons" role="radiogroup" aria-label="${tx("Why are you reporting this?", "Warum meldest du das?")}">
            ${REPORT_REASONS.map(([value, en, de], index) => `<label class="report-reason"><input type="radio" name="reportReason" value="${value}"${index === 0 ? " checked" : ""}> ${tx(en, de)}</label>`).join("")}
          </div>
          <label class="visually-hidden" for="reportDetail">${tx("Anything else we should know?", "Sonst noch etwas?")}</label>
          <textarea id="reportDetail" rows="2" maxlength="500" placeholder="${tx("Anything else we should know? (optional)", "Sonst noch etwas? (optional)")}"></textarea>
          <div class="report-actions">
            <button type="submit" class="primary-button">${tx("Send report", "Meldung senden")}</button>
            <button type="button" class="ghost-button" id="reportCancel">${tx("Cancel", "Abbrechen")}</button>
          </div>
        </form>` : ""}

        <div id="detailStudyArea" class="hidden">
          <div id="detailCard" class="study-card" role="button" tabindex="0" aria-label="Flip card">
            <div class="study-card-inner">
              <div class="study-card-face study-card-front"><div id="detailFront" class="study-card-text"></div></div>
              <div class="study-card-face study-card-back"><div id="detailBack" class="study-card-text"></div></div>
            </div>
          </div>
          <div class="study-controls">
            <button type="button" class="ghost-button" id="detailPrev">←</button>
            <button type="button" class="primary-button" id="detailFlip">${tx("Show answer", "Antwort zeigen")}</button>
            <button type="button" class="ghost-button" id="detailNext">→</button>
            <span id="detailPos" class="counter-position"></span>
          </div>
          <p class="field-hint">${tx("This preview does not track mastery. Save a personal copy to keep your progress.", "Diese Vorschau speichert keinen Lernfortschritt. Speichere eine persönliche Kopie, um deinen Fortschritt zu verfolgen.")}</p>
        </div>

        <div id="rateWidget" class="rate-widget hidden">
          <span>${tx("Your rating:", "Deine Bewertung:")}</span>
          <div class="stars" id="rateStars" role="radiogroup" aria-label="${tx("Rate this set 1 to 5 stars", "Dieses Set mit 1 bis 5 Sternen bewerten")}">
            ${[1, 2, 3, 4, 5].map(n => `<button type="button" class="star" role="radio" aria-checked="false" data-star="${n}" aria-label="${n} ${tx("stars", "Sterne")}">☆</button>`).join("")}
          </div>
          <span id="rateStatus" class="rate-status"></span>
        </div>
      </div>
      ${ai ? `<aside class="detail-review">
        <h3>${tx("AI quality review", "KI-Qualitätsprüfung")}</h3>
        <p class="review-headline"><b>${ai.stars}/5</b> · ${tx("Confidence", "Sicherheit")} ${escapeHtml(ai.confidence)}</p>
        <p class="review-summary">${escapeHtml(ai.summary)}</p>
        <div class="review-scores">${["accuracy", "clarity", "usefulness", "coverage", "difficulty", "originality"].map(k => `<span>${k}: <b>${(ai.scores[k] ?? 0).toFixed(1)}</b></span>`).join("")}</div>
        ${(ai.strengths || []).length ? `<h4>${tx("Strengths", "Stärken")}</h4><ul>${ai.strengths.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
        ${(ai.improvements || []).length ? `<h4>${tx("Improvements", "Verbesserungen")}</h4><ul>${ai.improvements.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
      </aside>` : ""}
    </div>`;

  // wire detail actions
  document.querySelector("#detailStudy").addEventListener("click", beginStudy);
  document.querySelector("#detailSave").addEventListener("click", () => saveCopy(set.id));
  document.querySelector("#detailShare").addEventListener("click", () => {
    navigator.clipboard?.writeText(shareUrl).then(() => toast(tx("Link copied.", "Link kopiert."), "success"), () => toast(shareUrl, "info"));
  });
  document.querySelector("#detailFlip").addEventListener("click", flipDetail);
  document.querySelector("#detailCard").addEventListener("click", flipDetail);
  document.querySelector("#detailNext").addEventListener("click", () => moveDetail(1));
  document.querySelector("#detailPrev").addEventListener("click", () => moveDetail(-1));
  document.querySelectorAll("#rateStars .star").forEach(star =>
    star.addEventListener("click", () => submitRating(set.id, Number(star.dataset.star))));
  wireReporting(set.id);
  if (set.your_rating) highlightStars(set.your_rating);
}

function wireReporting(setId) {
  const panel = document.querySelector("#reportPanel");
  const trigger = document.querySelector("#detailReport");
  if (!panel || !trigger) return;
  trigger.addEventListener("click", () => {
    if (!window.LEARNOVA_AUTHENTICATED) {
      window.location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
      return;
    }
    panel.classList.toggle("hidden");
    if (!panel.classList.contains("hidden")) panel.querySelector("textarea").focus();
  });
  document.querySelector("#reportCancel").addEventListener("click", () => panel.classList.add("hidden"));
  panel.addEventListener("submit", async event => {
    event.preventDefault();
    const reason = panel.querySelector('input[name="reportReason"]:checked')?.value || "other";
    const detail = panel.querySelector("#reportDetail").value.trim();
    try {
      await api(`/api/community/sets/${setId}/report`, { method: "POST", body: { reason, detail } });
      panel.classList.add("hidden");
      trigger.disabled = true;
      // The same acknowledgement whatever happened. Telling the reporter that their
      // report crossed a threshold would make this an oracle for how many it takes.
      toast(tx("Thanks — our team will look at this.", "Danke — unser Team sieht sich das an."), "success");
    } catch (error) {
      toast(error.message, "error");
    }
  });
}

async function beginStudy() {
  if (!window.LEARNOVA_AUTHENTICATED) {
    window.location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    return;
  }
  try {
    await api(`/api/community/sets/${local.setId}/study`, { method: "POST" });  // marks studied (server), enables rating
    local.studied = true;
    document.querySelector("#detailStudyArea").classList.remove("hidden");
    document.querySelector("#rateWidget").classList.remove("hidden");
    renderDetailCard();
  } catch (error) { toast(error.message, "error"); }
}

function renderDetailCard() {
  const card = local.cards[local.index];
  if (!card) return;
  local.flipped = false;
  document.querySelector("#detailFront").textContent = card.front;
  document.querySelector("#detailBack").textContent = card.back;
  document.querySelector("#detailCard").classList.remove("flipped");
  document.querySelector("#detailFlip").textContent = tx("Show answer", "Antwort zeigen");
  document.querySelector("#detailPos").textContent = `${local.index + 1} / ${local.cards.length}`;
  renderMath(document.querySelector("#detailFront"));
  renderMath(document.querySelector("#detailBack"));
}
function flipDetail() {
  local.flipped = !local.flipped;
  document.querySelector("#detailCard").classList.toggle("flipped", local.flipped);
  document.querySelector("#detailFlip").textContent = local.flipped ? tx("Hide answer", "Antwort verbergen") : tx("Show answer", "Antwort zeigen");
}
function moveDetail(delta) {
  local.index = Math.max(0, Math.min(local.cards.length - 1, local.index + delta));
  renderDetailCard();
}

async function saveCopy(setId) {
  if (!window.LEARNOVA_AUTHENTICATED) {
    window.location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    return;
  }
  try {
    await api(`/api/community/sets/${setId}/save`, { method: "POST" });
    toast(tx("Saved to your flashcards as an independent copy.", "Als unabhängige Kopie in deinen Karteikarten gespeichert."), "success");
  } catch (error) { toast(error.message, "error"); }
}

function highlightStars(value) {
  document.querySelectorAll("#rateStars .star").forEach(star => {
    const on = Number(star.dataset.star) <= value;
    star.textContent = on ? "★" : "☆";
    star.setAttribute("aria-checked", String(Number(star.dataset.star) === value));
  });
}

async function submitRating(setId, stars) {
  if (!window.LEARNOVA_AUTHENTICATED) {
    window.location.href = `/login?next=${encodeURIComponent(location.pathname)}`;
    return;
  }
  try {
    const data = await api(`/api/community/sets/${setId}/rate`, { method: "POST", body: { stars } });
    highlightStars(stars);
    const s = data.student_rating;
    document.querySelector("#rateStatus").textContent = `${tx("Saved", "Gespeichert")} — ${s.average.toFixed(1)}/5 · ${s.count} ${s.count === 1 ? tx("rating", "Bewertung") : tx("ratings", "Bewertungen")}`;
    toast(tx("Rating saved.", "Bewertung gespeichert."), "success");
  } catch (error) {
    const messages = {
      not_studied: tx("Study the set before rating it.", "Lerne das Set, bevor du es bewertest."),
      self_rating: tx("You cannot rate your own set.", "Du kannst dein eigenes Set nicht bewerten."),
    };
    document.querySelector("#rateStatus").textContent = messages[error.code] || error.message;
    toast(messages[error.code] || error.message, "error");
  }
}

/* ------------------------- init ------------------------- */
function init() {
  document.querySelector("#commSubject").insertAdjacentHTML("beforeend",
    SUBJECTS.map(s => `<option value="${s}">${s}</option>`).join(""));
  document.querySelector("#commFilters").addEventListener("input", () => loadLibrary());
  document.querySelector("#commFilters").addEventListener("submit", e => { e.preventDefault(); loadLibrary(); });
  document.querySelector("#commGrid").addEventListener("click", e => {
    const card = e.target.closest("[data-open]"); if (card) openDetail(card.dataset.open);
  });
  document.querySelector("#commGrid").addEventListener("keydown", e => {
    if ((e.key === "Enter" || e.key === " ") && e.target.closest("[data-open]")) { e.preventDefault(); openDetail(e.target.closest("[data-open]").dataset.open); }
  });
  document.querySelectorAll(".community-rail").forEach(rail => {
    rail.addEventListener("click", e => {
      const card = e.target.closest("[data-open]"); if (card) openDetail(card.dataset.open);
    });
    rail.addEventListener("keydown", e => {
      if ((e.key === "Enter" || e.key === " ") && e.target.closest("[data-open]")) {
        e.preventDefault(); openDetail(e.target.closest("[data-open]").dataset.open);
      }
    });
  });
  document.querySelectorAll("[data-apply-sort]").forEach(button => button.addEventListener("click", () => {
    document.querySelector("#commSort").value = button.dataset.applySort;
    loadLibrary();
    document.querySelector(".library-results-heading")?.scrollIntoView({ behavior: "smooth" });
  }));
  document.querySelector("[data-action='back-to-community']").addEventListener("click", () => {
    showView("commLibrary");
    history.pushState({}, "", "/community");
  });
  window.addEventListener("popstate", () => {
    const match = location.pathname.match(/^\/community\/sets\/(\d+)$/);
    if (match) openDetail(match[1], false); else showView("commLibrary");
  });

  const deepLink = window.LEARNOVA_COMMUNITY_SET_ID || new URLSearchParams(window.location.search).get("set");
  loadLibrary();
  loadDiscoveryShelves();
  if (deepLink) openDetail(deepLink, false);
}

document.addEventListener("DOMContentLoaded", init);

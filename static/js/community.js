import { escapeHtml } from "./dom.js";

const SUBJECTS = ["Mathematics", "English", "German", "History", "Biology", "Chemistry", "Physics", "Other"];
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
  return `<span class="rating-chip ai" title="AI quality review">🤖 ${ai.overall ? ai.overall.toFixed(1) : "—"}/5 · ${escapeHtml(ai.confidence || "—")}</span>`;
}
function studentRatingHtml(student) {
  if (!student.count) return `<span class="rating-chip student muted">⭐ No student ratings yet</span>`;
  return `<span class="rating-chip student" title="Student rating">⭐ ${student.average.toFixed(1)}/5 · ${student.count} ${student.count === 1 ? "rating" : "ratings"}</span>`;
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

function cardHtml(set) {
  const excerpt = set.description ? escapeHtml(set.description.slice(0, 120)) + (set.description.length > 120 ? "…" : "") : "";
  return `
    <article class="set-card community-card" data-open="${set.id}" tabindex="0" role="button" aria-label="Open ${escapeHtml(set.title)}">
      <div class="community-card-top">
        <h3>${escapeHtml(set.title)}</h3>
        ${set.teacher_verified ? `<span class="badge verified" title="Teacher verified">✓ Verified</span>` : ""}
      </div>
      <p class="set-meta">${escapeHtml(set.subject)}${set.topic ? " · " + escapeHtml(set.topic) : ""} · ${escapeHtml(set.difficulty)}${set.grade ? " · Grade " + escapeHtml(set.grade) : ""}</p>
      ${excerpt ? `<p class="set-excerpt">${excerpt}</p>` : ""}
      <div class="rating-row">${aiRatingHtml(set.ai_review)}${studentRatingHtml(set.student_rating)}</div>
      <p class="set-stats">${set.card_count} cards${set.study_count ? ` · ${set.study_count} studied` : ""}${set.save_count ? ` · ${set.save_count} saved` : ""} · by ${escapeHtml(set.author)}</p>
    </article>`;
}

/* ------------------------- detail ------------------------- */
const local = { cards: [], index: 0, flipped: false, setId: null, studied: false };

async function openDetail(setId) {
  try {
    const data = await api(`/api/community/sets/${setId}`);
    renderDetail(data.set);
    showView("commDetail");
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
        <p class="rating-explainer">The AI review and student rating are separate and measure different things.</p>

        <div class="detail-actions">
          <button type="button" class="primary-button" id="detailStudy">Begin studying</button>
          <button type="button" class="ghost-button" id="detailSave">Save a personal copy</button>
          <button type="button" class="ghost-button" id="detailShare">Copy link</button>
        </div>

        <div id="detailStudyArea" class="hidden">
          <div id="detailCard" class="study-card" role="button" tabindex="0" aria-label="Flip card">
            <div class="study-card-inner">
              <div class="study-card-face study-card-front"><div id="detailFront" class="study-card-text"></div></div>
              <div class="study-card-face study-card-back"><div id="detailBack" class="study-card-text"></div></div>
            </div>
          </div>
          <div class="study-controls">
            <button type="button" class="ghost-button" id="detailPrev">←</button>
            <button type="button" class="primary-button" id="detailFlip">Show answer</button>
            <button type="button" class="ghost-button" id="detailNext">→</button>
            <span id="detailPos" class="counter-position"></span>
          </div>
          <p class="field-hint">Community study is a local preview and is not saved to your account. Save a personal copy to track progress.</p>
        </div>

        <div id="rateWidget" class="rate-widget hidden">
          <span>Your rating:</span>
          <div class="stars" id="rateStars" role="radiogroup" aria-label="Rate this set 1 to 5 stars">
            ${[1, 2, 3, 4, 5].map(n => `<button type="button" class="star" role="radio" aria-checked="false" data-star="${n}" aria-label="${n} stars">☆</button>`).join("")}
          </div>
          <span id="rateStatus" class="rate-status"></span>
        </div>
      </div>
      ${ai ? `<aside class="detail-review">
        <h3>🤖 AI quality review</h3>
        <p class="review-headline"><b>${ai.stars}/5</b> · Confidence ${escapeHtml(ai.confidence)}</p>
        <p class="review-summary">${escapeHtml(ai.summary)}</p>
        <div class="review-scores">${["accuracy", "clarity", "usefulness", "coverage", "difficulty", "originality"].map(k => `<span>${k}: <b>${(ai.scores[k] ?? 0).toFixed(1)}</b></span>`).join("")}</div>
        ${(ai.strengths || []).length ? `<h4>Strengths</h4><ul>${ai.strengths.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
        ${(ai.improvements || []).length ? `<h4>Improvements</h4><ul>${ai.improvements.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
      </aside>` : ""}
    </div>`;

  // wire detail actions
  document.querySelector("#detailStudy").addEventListener("click", beginStudy);
  document.querySelector("#detailSave").addEventListener("click", () => saveCopy(set.id));
  document.querySelector("#detailShare").addEventListener("click", () => {
    navigator.clipboard?.writeText(shareUrl).then(() => toast("Link copied.", "success"), () => toast(shareUrl, "info"));
  });
  document.querySelector("#detailFlip").addEventListener("click", flipDetail);
  document.querySelector("#detailCard").addEventListener("click", flipDetail);
  document.querySelector("#detailNext").addEventListener("click", () => moveDetail(1));
  document.querySelector("#detailPrev").addEventListener("click", () => moveDetail(-1));
  document.querySelectorAll("#rateStars .star").forEach(star =>
    star.addEventListener("click", () => submitRating(set.id, Number(star.dataset.star))));
  if (set.your_rating) highlightStars(set.your_rating);
}

async function beginStudy() {
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
  document.querySelector("#detailFlip").textContent = "Show answer";
  document.querySelector("#detailPos").textContent = `${local.index + 1} / ${local.cards.length}`;
  renderMath(document.querySelector("#detailFront"));
  renderMath(document.querySelector("#detailBack"));
}
function flipDetail() {
  local.flipped = !local.flipped;
  document.querySelector("#detailCard").classList.toggle("flipped", local.flipped);
  document.querySelector("#detailFlip").textContent = local.flipped ? "Hide answer" : "Show answer";
}
function moveDetail(delta) {
  local.index = Math.max(0, Math.min(local.cards.length - 1, local.index + delta));
  renderDetailCard();
}

async function saveCopy(setId) {
  try {
    await api(`/api/community/sets/${setId}/save`, { method: "POST" });
    toast("Saved to your flashcards as an independent copy.", "success");
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
  try {
    const data = await api(`/api/community/sets/${setId}/rate`, { method: "POST", body: { stars } });
    highlightStars(stars);
    const s = data.student_rating;
    document.querySelector("#rateStatus").textContent = `Saved — ${s.average.toFixed(1)}/5 from ${s.count} ${s.count === 1 ? "rating" : "ratings"}`;
    toast("Rating saved.", "success");
  } catch (error) {
    const messages = { not_studied: "Study the set before rating it.", self_rating: "You cannot rate your own set." };
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
  document.querySelector("[data-action='back-to-community']").addEventListener("click", () => showView("commLibrary"));

  const deepLink = window.LEARNOVA_COMMUNITY_SET_ID || new URLSearchParams(window.location.search).get("set");
  loadLibrary();
  if (deepLink) openDetail(deepLink);
}

document.addEventListener("DOMContentLoaded", init);

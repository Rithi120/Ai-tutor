import { escapeHtml } from "./dom.js";
import { withRetryHint } from "./ai-limit-rules.js";
import { renderMath } from "./math.js";

const MODERATION_ENABLED =
  document.querySelector("#main-content")?.dataset.moderationEnabled === "true";
// A subset of learnova.moderation.taxonomy.REPORT_REASONS. The server validates against
// that list, so an unknown value here is refused rather than silently stored. One
// plain-language safety choice ("inappropriate or harmful") replaces the four finer ones
// the form used to offer; the server still accepts those from older clients.
const REPORT_REASONS = [
  ["incorrect_translation", "Incorrect translation", "Falsche Übersetzung"],
  ["incorrect", "Incorrect information", "Falsche Informationen"],
  ["misleading", "Misleading explanation or context", "Irreführende Erklärung oder irreführender Kontext"],
  ["inappropriate", "Inappropriate or harmful", "Unangemessen oder schädlich"],
  ["spam", "Spam or advertising", "Spam oder Werbung"],
  ["personal_information", "Personal information", "Persönliche Daten"],
  ["copyright", "Copyright problem", "Urheberrechtsproblem"],
  ["other", "Something else", "Etwas anderes"],
];
const shorten = (text, length = 48) => {
  const value = String(text ?? "").replace(/\s+/g, " ").trim();
  return value.length > length ? `${value.slice(0, length - 1)}…` : value;
};
const tx = (en, de) => window.LEARNOVA_LANGUAGE === "de" ? de : en;



async function api(url, { method = "GET", body, signal } = {}) {
  const options = { method, headers: {}, signal };
  if (body !== undefined) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* non-JSON */ }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.code = data.code; error.status = response.status; error.details = data.details || {};
    error.message = withRetryHint(error.message, error);
    throw error;
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
/* Filters (COMMUNITY_FLASHCARD_FILTER_FIX.md). The server filters the whole library and
 * pages it; this keeps the form, the URL and the shown results in step:
 *  - every load cancels the one before it (AbortController) and a late answer of an older
 *    request is dropped (sequence), so the results always belong to the current filters;
 *  - typing is debounced, a select applies at once;
 *  - the filters live in the URL (?subject=English&grade=9), so a reload, a shared link and
 *    the browser's Back/Forward show the same results;
 *  - while any filter is active the highlight shelves (unfiltered by design) step aside, so
 *    the matching sets are right under the filter bar. */
const FILTER_FIELDS = ["q", "kind", "subject", "grade", "difficulty", "language", "min_ai", "min_student", "sort"];
const PANEL_FIELDS = ["grade", "difficulty", "language", "min_ai", "min_student"];
const DEFAULT_SORT = "ranking";
const PAGE_SIZE = 30;
const library = { offset: 0, controller: null, sequence: 0, typing: null };

const filterForm = () => document.querySelector("#commFilters");

function currentFilters() {
  const values = {};
  new FormData(filterForm()).forEach((value, key) => {
    const text = String(value).replace(/\s+/g, " ").trim();
    if (text) values[key] = text;
  });
  if (values.sort === DEFAULT_SORT) delete values.sort;
  return values;
}

const filtersActive = values => Object.keys(values).some(key => key !== "sort");

function libraryUrl(values) {
  const query = new URLSearchParams(values).toString();
  return `/community${query ? `?${query}` : ""}`;
}

function syncUrl(values, push) {
  if (location.pathname !== "/community") return;      // a set's own page keeps its address
  const url = libraryUrl(values);
  if (location.pathname + location.search === url) return;
  history[push ? "pushState" : "replaceState"]({ communityFilters: true }, "", url);
}

function applyUrlToForm() {
  const params = new URLSearchParams(location.search);
  const form = filterForm();
  FILTER_FIELDS.forEach(name => {
    const field = form.elements.namedItem(name);
    if (!field) return;
    const wanted = params.get(name) || (name === "sort" ? DEFAULT_SORT : "");
    const known = field.tagName !== "SELECT" || [...field.options].some(option => option.value === wanted);
    field.value = known ? wanted : (name === "sort" ? DEFAULT_SORT : "");
  });
  if (PANEL_FIELDS.some(name => params.get(name))) togglePanel(true);
}

function fieldLabel(name, value) {
  const field = filterForm().elements.namedItem(name);
  if (name === "q") return `„${value}“`;
  if (field && field.tagName === "SELECT") {
    const option = [...field.options].find(item => item.value === value);
    const label = field.closest("label")?.firstChild?.textContent?.trim();
    const text = option ? option.textContent.trim() : value;
    // "Schwierigkeit: Leicht", but "Klasse 9", not "Klasse: Klasse 9".
    return label && PANEL_FIELDS.includes(name) && !text.toLowerCase().includes(label.toLowerCase())
      ? `${label}: ${text}` : text;
  }
  return value;
}

function togglePanel(open) {
  const panel = document.querySelector("#commMoreFilters");
  const toggle = document.querySelector("#commFilterToggle");
  const show = open ?? panel.hidden;
  panel.hidden = !show;
  toggle.setAttribute("aria-expanded", String(show));
  toggle.classList.toggle("is-open", show);
}

function renderFilterState(values) {
  const form = filterForm();
  const panelCount = PANEL_FIELDS.filter(name => values[name]).length;
  const badge = document.querySelector("#commFilterBadge");
  badge.textContent = String(panelCount);
  badge.hidden = panelCount === 0;
  document.querySelector("#commFilterToggle").classList.toggle("has-filters", panelCount > 0);
  const chips = document.querySelector("#commActive");
  chips.innerHTML = "";
  FILTER_FIELDS.filter(name => name !== "sort" && values[name]).forEach(name => {
    const label = fieldLabel(name, values[name]);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "comm-chip";
    chip.dataset.field = name;
    chip.setAttribute("aria-label", (form.dataset.removeLabel || "Remove filter: {name}").replace("{name}", label));
    chip.append(label, Object.assign(document.createElement("span"), { textContent: "×", ariaHidden: "true" }));
    chips.appendChild(chip);
  });
  const active = filtersActive(values) || Boolean(values.sort);
  document.querySelector("#commReset").hidden = !active;
  document.querySelector(".community-discovery")?.toggleAttribute("hidden", active);
  const title = document.querySelector("#commResultsTitle");
  if (title) title.textContent = filtersActive(values) ? title.dataset.filtered : title.dataset.all;
}

function countText(total) {
  const form = filterForm();
  return total === 1 ? (form.dataset.countOne || "1 set") : (form.dataset.countMany || "{count} sets").replace("{count}", String(total));
}

async function loadLibrary({ append = false, push = false } = {}) {
  const values = currentFilters();
  renderFilterState(values);
  syncUrl(values, push);
  if (!append) library.offset = 0;
  library.controller?.abort();
  const controller = new AbortController();
  library.controller = controller;
  const sequence = ++library.sequence;
  const params = new URLSearchParams(values);
  params.set("limit", String(PAGE_SIZE));
  params.set("offset", String(library.offset));
  const grid = document.querySelector("#commGrid");
  const loading = document.querySelector("#commLoading");
  const empty = document.querySelector("#commEmpty");
  const more = document.querySelector("#commMore");
  more.hidden = true;
  if (!append) { loading.classList.remove("hidden"); grid.innerHTML = ""; empty.classList.add("hidden"); }
  grid.setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/community/library?${params.toString()}`, { signal: controller.signal });
    if (sequence !== library.sequence) return;          // an older answer: the filters moved on
    loading.classList.add("hidden");
    const sets = data.sets || [];
    grid.insertAdjacentHTML("beforeend", sets.map(cardHtml).join(""));
    library.offset += sets.length;
    const total = Number(data.total ?? sets.length);
    document.querySelector("#commCount").textContent = countText(total);
    empty.classList.toggle("hidden", total > 0);
    more.hidden = !data.has_more;
  } catch (error) {
    if (error.name === "AbortError" || sequence !== library.sequence) return;
    loading.classList.add("hidden");
    toast(error.message, "error");
  } finally {
    if (sequence === library.sequence) grid.removeAttribute("aria-busy");
  }
}

function resetFilters() {
  const form = filterForm();
  form.reset();
  form.elements.namedItem("sort").value = DEFAULT_SORT;
  togglePanel(false);
  loadLibrary({ push: true });
  document.querySelector("#commSearch").focus();
}

function wireFilters() {
  const form = filterForm();
  form.addEventListener("input", event => {
    if (event.target.id !== "commSearch") return;
    window.clearTimeout(library.typing);
    library.typing = window.setTimeout(() => loadLibrary(), 300);
  });
  form.addEventListener("change", event => {
    if (event.target.id === "commSearch") return;
    loadLibrary({ push: true });
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    window.clearTimeout(library.typing);
    loadLibrary({ push: true });
  });
  document.querySelector("#commFilterToggle").addEventListener("click", () => togglePanel());
  document.querySelector("#commMoreFilters").addEventListener("keydown", event => {
    if (event.key === "Escape") { togglePanel(false); document.querySelector("#commFilterToggle").focus(); }
  });
  document.querySelector("#commActive").addEventListener("click", event => {
    const chip = event.target.closest("[data-field]");
    if (!chip) return;
    const field = form.elements.namedItem(chip.dataset.field);
    if (field) field.value = "";
    loadLibrary({ push: true });
  });
  document.querySelector("#commReset").addEventListener("click", resetFilters);
  document.querySelectorAll("[data-reset-filters]").forEach(button => button.addEventListener("click", resetFilters));
  document.querySelector("#commMore").addEventListener("click", () => loadLibrary({ append: true }));
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

// The author's identity, from the server's author_identity (present only when the set is
// published under the username). Premium and level come from the server; nothing here
// can grant either. Links only where the card is not itself a button.
const SAFE_COLOUR = /^#[0-9a-f]{3,8}$/i;
const PREMIUM_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M2 5.2 5 7.6 8 2.5l3 5.1 3-2.4-1.2 7.3H3.2Z" fill="currentColor"/><rect x="3.2" y="13" width="9.6" height="1.6" rx=".8" fill="currentColor"/></svg>';

function authorHtml(set, { link = false } = {}) {
  const who = set.author_identity;
  if (!who) return `${tx("by", "von")} ${escapeHtml(set.author)}`;
  const accent = SAFE_COLOUR.test(who.accent || "") ? who.accent : "#7c5cff";
  const inner = `<span class="avatar-frame" style="--avatar-accent: ${accent}"><img src="${escapeHtml(who.avatar_url)}" alt="" width="256" height="256" loading="lazy" decoding="async"></span>`
    + `<span class="author-chip-name">${escapeHtml(who.username)}</span>`
    + (who.level ? `<span class="author-chip-level">${tx("Level", "Level")} ${Number(who.level)}</span>` : "")
    + (who.premium ? `<span class="premium-badge is-compact" title="${tx("Premium member", "Premium-Mitglied")}">${PREMIUM_ICON}<span class="visually-hidden">Premium</span></span>` : "");
  if (link && who.profile_url) return `<a class="author-chip" href="${escapeHtml(who.profile_url)}">${inner}</a>`;
  return `<span class="author-chip">${inner}</span>`;
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
      <p class="set-stats">${set.set_kind === "vocabulary" ? `<span class="set-kind-badge">${tx("Vocabulary", "Vokabeln")}</span> · ` : ""}${set.card_count} ${tx("cards", "Karten")}${set.study_count ? ` · ${set.study_count} ${tx("studied", "gelernt")}` : ""}${set.save_count ? ` · ${set.save_count} ${tx("saved", "gespeichert")}` : ""}</p>
      <p class="set-author">${authorHtml(set)}</p>
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
        <p class="set-meta">${escapeHtml(set.subject)}${set.topic ? " · " + escapeHtml(set.topic) : ""} · ${escapeHtml(set.difficulty)}${set.grade ? " · Grade " + escapeHtml(set.grade) : ""}</p>
        <p class="set-author">${authorHtml(set, { link: true })}</p>
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
          <p class="report-question">${tx("What is wrong?", "Was stimmt nicht?")}</p>
          <div class="report-reasons" role="radiogroup" aria-label="${tx("Why are you reporting this?", "Warum meldest du das?")}">
            ${REPORT_REASONS.map(([value, en, de]) => `<label class="report-reason"><input type="radio" name="reportReason" value="${value}"${value === (set.set_kind === "vocabulary" ? "incorrect_translation" : "incorrect") ? " checked" : ""}> ${tx(en, de)}</label>`).join("")}
          </div>
          ${(set.cards || []).length > 1 ? `<label class="report-entry-label" for="reportEntry">${tx("Which entry? (optional)", "Welcher Eintrag? (optional)")}</label>
          <select id="reportEntry" class="report-entry">
            <option value="">${tx("The whole set", "Das ganze Set")}</option>
            ${set.cards.map((card, index) => `<option value="${index + 1}">${index + 1}. ${escapeHtml(shorten(card.front))}${card.back ? ` — ${escapeHtml(shorten(card.back, 32))}` : ""}</option>`).join("")}
          </select>` : ""}
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
    const entry = Number(panel.querySelector("#reportEntry")?.value || 0) || null;
    try {
      await api(`/api/community/sets/${setId}/report`, { method: "POST", body: { reason, detail, entry } });
      panel.classList.add("hidden");
      trigger.disabled = true;
      // The same acknowledgement whatever the review finds: the reporter is told it was
      // received, never what it led to.
      toast(tx("Thanks — your report was received. We will check it.", "Danke — deine Meldung ist eingegangen. Wir prüfen sie."), "success");
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
    const data = await api(`/api/community/sets/${setId}/save`, { method: "POST" });
    toast(tx("Saved to your flashcards as an independent copy.", "Als unabhängige Kopie in deinen Karteikarten gespeichert."), "success");
    // Your copy: edit it, and every learning mode and game is there.
    if (data.redirect) window.setTimeout(() => { window.location.href = data.redirect; }, 400);
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
  // Subjects come from the page (the app's subject list, translated); the filter keeps
  // what is in the address, so a reload or a shared link shows the same results.
  wireFilters();
  applyUrlToForm();
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
  // "View all": the shelf's order over every set that matches the active filters.
  document.querySelectorAll("[data-apply-sort]").forEach(button => button.addEventListener("click", () => {
    document.querySelector("#commSort").value = button.dataset.applySort;
    loadLibrary({ push: true });
    document.querySelector(".library-results-heading")?.scrollIntoView({ behavior: "smooth" });
  }));
  document.querySelector("[data-action='back-to-community']").addEventListener("click", () => {
    showView("commLibrary");
    history.pushState({}, "", libraryUrl(currentFilters()));
  });
  window.addEventListener("popstate", () => {
    const match = location.pathname.match(/^\/community\/sets\/(\d+)$/);
    if (match) { openDetail(match[1], false); return; }
    showView("commLibrary");
    applyUrlToForm();          // Back/Forward between filter states shows those results
    loadLibrary();
  });

  const deepLink = window.LEARNOVA_COMMUNITY_SET_ID || new URLSearchParams(window.location.search).get("set");
  loadLibrary();
  loadDiscoveryShelves();
  if (deepLink) openDetail(deepLink, false);
}

document.addEventListener("DOMContentLoaded", init);

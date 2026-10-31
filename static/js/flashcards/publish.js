import { api, toast, escapeHtml, t, optionsHtml, SUBJECTS, SET_ID, selectedLanguage } from "./common.js";

const STATUS_KEY = {
  draft: "fcStatusDraft", pending_moderation: "fcStatusPendingModeration",
  pending_ai_review: "fcStatusPendingAi",
  pending_manual_review: "fcStatusPendingManual", approved: "fcStatusApproved",
  changes_requested: "fcStatusChangesRequested",
  rejected: "fcStatusRejected", hidden: "fcStatusHidden", unpublished: "fcStatusUnpublished",
};
const isDe = selectedLanguage === "German";

function statusLabel(status, review) {
  if (status === "rejected" && review && review.decision && review.decision.reason === "needs_correction") {
    return t("fcStatusChangesRequested");
  }
  return t(STATUS_KEY[status] || status) || status;
}

async function loadState() {
  const loading = document.querySelector("#publishLoading");
  try {
    const state = await api(`/api/flashcards/sets/${SET_ID}/publication`);
    loading.hidden = true;
    if (state.published && state.status !== "unpublished") {
      renderStatus(state);
    } else {
      await showForm();
    }
  } catch (error) {
    loading.hidden = true;
    toast(error.message, "error");
  }
}

async function showForm() {
  try {
    const data = await api(`/api/flashcards/sets/${SET_ID}`);
    const set = data.set;
    document.querySelector("#pubSubject").innerHTML = optionsHtml(SUBJECTS, set.subject || "Other");
    document.querySelector("#pubTitle").value = set.title || "";
    document.querySelector("#pubGrade").value = set.grade || "";
    document.querySelector("#pubDifficulty").value = set.difficulty || "medium";
    document.querySelector("#pubLanguage").value = set.language || (isDe ? "de" : "en");
  } catch (_) { /* still allow publishing with defaults */ }
  document.querySelector("#publishForm").classList.remove("hidden");
}

async function submitForm(event) {
  event.preventDefault();
  const errorEl = document.querySelector("#publishError");
  errorEl.classList.add("hidden");
  if (!document.querySelector("#pubConfirm").checked) {
    errorEl.textContent = t("fcConfirmOwnership");
    errorEl.classList.remove("hidden");
    return;
  }
  const body = {
    source_set_id: SET_ID,
    title: document.querySelector("#pubTitle").value.trim(),
    description: document.querySelector("#pubDescription").value.trim(),
    subject: document.querySelector("#pubSubject").value,
    topic: document.querySelector("#pubTopic").value.trim(),
    grade: document.querySelector("#pubGrade").value.trim(),
    difficulty: document.querySelector("#pubDifficulty").value,
    language: document.querySelector("#pubLanguage").value,
    tags: document.querySelector("#pubTags").value.split(",").map(s => s.trim()).filter(Boolean),
    author_display: document.querySelector("#pubDisplay").value,
    confirm: true,
  };
  try {
    const data = await api("/api/community/publish", { method: "POST", body });
    document.querySelector("#publishForm").classList.add("hidden");
    renderStatus({ published: true, public_id: data.id, status: data.status, review: data.review,
                   moderation: data.moderation, quality_status: data.quality_status,
                   changed_since_publish: false });
  } catch (error) {
    errorEl.textContent = error.message;
    errorEl.classList.remove("hidden");
  }
}

const SIDE_KEY = { front: "fcSideFront", back: "fcSideBack", explanation: "fcSideExplanation" };
const STATE_KEY = { kept: "fcWarningKept", accepted: "fcWarningAccepted", edited: "fcWarningEdited" };

// One questionable entry: where it is, the words in question (underlined, with a small
// icon that is decoration only), why, and what the author can do. Everything is visible
// text rather than a hover tooltip, so it works with touch and keyboard alike.
function entryItemHtml(item, actions) {
  const where = `${escapeHtml(t("fcEntryCard", { number: item.entry ?? "?" }))} · ${escapeHtml(t(SIDE_KEY[item.side] || "fcSideBack"))}`;
  const state = item.state && item.state !== "open" ? `<p class="entry-warning-state">${escapeHtml(t(STATE_KEY[item.state] || "fcWarningKept"))}</p>` : "";
  const buttons = actions && (!item.state || item.state === "open") ? `<div class="entry-warning-actions">
      <button type="button" class="ghost-button" data-warning="${item.index}" data-warning-act="keep">${escapeHtml(t("fcKeepOriginal"))}</button>
      <a class="ghost-button" href="/flashcards/${SET_ID}/edit" data-warning="${item.index}" data-warning-act="edited">${escapeHtml(t("fcEditEntry"))}</a>
      ${item.suggestion ? `<button type="button" class="primary-button" data-warning="${item.index}" data-warning-act="accept">${escapeHtml(t("fcAcceptSuggestion"))}</button>` : ""}
    </div>` : "";
  return `<li class="entry-warning" data-state="${escapeHtml(item.state || "open")}">
    <p class="entry-warning-where">${where}</p>
    <p><mark class="entry-warning-mark"><span aria-hidden="true">⚠</span> ${escapeHtml(item.text || "")}</mark></p>
    <p class="entry-warning-message">${escapeHtml(item.message || "")}</p>
    ${item.note || item.explanation ? `<p class="entry-warning-note">${escapeHtml(item.note || item.explanation)}</p>` : ""}
    ${item.suggestion ? `<p class="entry-warning-suggestion">${escapeHtml(t("fcSuggestion", { text: item.suggestion }))}</p>` : ""}
    ${state}${buttons}
  </li>`;
}

function warningsHtml(review, findings) {
  const warnings = (review && review.entry_warnings) || [];
  const fromReaders = (findings || []).filter(item => item.text || item.explanation);
  if (!warnings.length && !fromReaders.length) return "";
  return `<section class="entry-warnings" aria-labelledby="entryWarningsTitle">
    <p class="entry-warnings-badge">${escapeHtml(t("fcQualitySuggestion"))}</p>
    <h4 id="entryWarningsTitle">${escapeHtml(t("fcEntryWarnings"))}</h4>
    <p class="field-hint">${escapeHtml(t("fcEntryWarningsIntro"))}</p>
    ${warnings.length ? `<ul class="entry-warning-list">${warnings.map(item => entryItemHtml(item, true)).join("")}</ul>` : ""}
    ${fromReaders.length ? `<h4>${escapeHtml(t("fcReaderFindings"))}</h4><ul class="entry-warning-list">${
      fromReaders.map(item => entryItemHtml({ ...item, state: "open", index: -1 }, false)).join("")}</ul>` : ""}
  </section>`;
}

async function onWarningAction(event) {
  const control = event.target.closest("[data-warning-act]");
  if (!control) return;
  const publicId = document.querySelector("#statusActions").dataset.publicId;
  const action = control.dataset.warningAct;
  event.preventDefault();
  try {
    const data = await api(`/api/community/sets/${publicId}/warnings/${control.dataset.warning}`, { method: "POST", body: { action } });
    // "Edit" records the choice first, then opens the editor.
    if (action === "edited") { window.location.href = control.href; return; }
    if (data.needs_republish) toast(t("fcSuggestionAccepted"), "success");
    loadState();
  } catch (error) { toast(error.message, "error"); }
}

function renderStatus(state) {
  const section = document.querySelector("#publishStatus");
  const badge = document.querySelector("#statusBadge");
  badge.textContent = statusLabel(state.status, state.review);
  badge.className = `review-status status-${escapeHtml(state.status)}`;
  document.querySelector("#changedNote").classList.toggle("hidden", !state.changed_since_publish);
  document.querySelector("#changedNote").textContent = state.changed_since_publish ? t("fcChangedSincePublish") : "";

  const moderation = state.moderation;
  document.querySelector("#statusModeration").innerHTML = moderation ? `
    <p class="moderation-headline"><b>${escapeHtml(moderation.decision_label || "")}</b></p>
    <p class="moderation-message">${escapeHtml(moderation.status_message || "")}</p>
    ${(moderation.reasons || []).length ? `<ul class="moderation-reasons">${
      moderation.reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}
    ${moderation.suggested_revision ? `<p class="moderation-suggestion">${escapeHtml(moderation.suggested_revision)}</p>` : ""}` : "";

  const review = state.review;
  const scores = review ? review.scores : null;
  document.querySelector("#statusReview").innerHTML = review ? `
    <p class="review-headline">🤖 ${t("fcAiConfidence")}: ${escapeHtml(review.confidence || "—")} · <b>${review.stars || 0}/5</b></p>
    <p class="review-summary">${escapeHtml(review.summary || "")}</p>
    <div class="review-scores">${["accuracy", "clarity", "usefulness", "coverage", "difficulty", "originality"].map(k => `<span>${k}: <b>${(scores[k] ?? 0).toFixed(1)}</b></span>`).join("")}</div>
    ${(review.strengths || []).length ? `<h4>${t("fcStrengths")}</h4><ul>${review.strengths.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
    ${(review.improvements || []).length ? `<h4>${t("fcImprovements")}</h4><ul>${review.improvements.map(s => `<li>${escapeHtml(s)}</li>`).join("")}</ul>` : ""}
    ${(review.flagged || []).length ? `<h4>${t("fcFlaggedCards")}</h4><ul>${review.flagged.map(f => `<li><b>${escapeHtml(f.reference || "")}</b> ${escapeHtml(f.issue || "")}</li>`).join("")}</ul>` : ""}` : "";
  const pending = state.quality_status === "pending" && state.status === "approved"
    ? `<p class="field-hint quality-pending">${escapeHtml(t("fcQualityPending"))}</p>` : "";
  document.querySelector("#statusReview").insertAdjacentHTML("afterbegin", pending);
  document.querySelector("#statusReview").insertAdjacentHTML("beforeend", warningsHtml(review, state.reader_findings));

  const actions = [];
  actions.push(`<a class="ghost-button" href="/flashcards/${SET_ID}/edit">${escapeHtml(t("fcEditPrivate"))}</a>`);
  if (state.changed_since_publish) actions.push(`<button type="button" class="primary-button" data-act="resubmit">${escapeHtml(t("fcPublishChanges"))}</button>`);
  else actions.push(`<button type="button" class="ghost-button" data-act="resubmit">${escapeHtml(t("fcResubmit"))}</button>`);
  if (state.status === "approved") actions.push(`<a class="ghost-button" href="/community?set=${state.public_id}">${escapeHtml(t("fcViewPublic"))}</a>`);
  actions.push(`<button type="button" class="ghost-button danger" data-act="unpublish">${escapeHtml(t("fcUnpublish"))}</button>`);
  const bar = document.querySelector("#statusActions");
  bar.dataset.publicId = state.public_id;
  bar.innerHTML = actions.join("");
  section.classList.remove("hidden");
}

async function onStatusAction(event) {
  const button = event.target.closest("[data-act]");
  if (!button) return;
  const publicId = document.querySelector("#statusActions").dataset.publicId;
  if (button.dataset.act === "resubmit") {
    try { await api(`/api/community/sets/${publicId}/resubmit`, { method: "POST" }); toast(t("fcResubmit"), "success"); loadState(); }
    catch (error) { toast(error.message, "error"); }
  } else if (button.dataset.act === "unpublish") {
    if (!window.confirm(t("fcConfirmUnpublish"))) return;
    try { await api(`/api/community/sets/${publicId}/unpublish`, { method: "POST" }); toast(t("fcUnpublished"), "success"); loadState(); }
    catch (error) { toast(error.message, "error"); }
  }
}

function init() {
  document.querySelector("#publishForm").addEventListener("submit", submitForm);
  document.querySelector("#statusActions").addEventListener("click", onStatusAction);
  document.querySelector("#statusReview").addEventListener("click", onWarningAction);
  loadState();
}

document.addEventListener("DOMContentLoaded", init);

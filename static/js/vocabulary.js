import { api, escapeHtml, t, toast } from "./flashcards/common.js";
import { safeUUID } from "./dom.js";
import { createImageCollection, uploadCollection, xhrUpload } from "./image-collection.js";

const importForm = document.querySelector("#vocabularyImportForm");
const reviewPage = document.querySelector("[data-vocabulary-review]");
let reviewEntries = [];

function uuid() {
  return safeUUID();
}

if (importForm) {
  // ---- Manual entry: three boxes per word, not one delimited line ----------------
  // The rows are sent as structured JSON, so an example sentence containing a dash or a
  // semicolon survives; the pasted-text parser would split on those and drop the tail.
  const manualField = document.querySelector("#vocabularyManualField");
  const manualRows = document.querySelector("#vocabularyManualRows");
  const manualPayload = document.querySelector("#vocabularyManualPayload");

  function manualRowHtml() {
    const label = (key) => escapeHtml(t(key));
    return `<div class="manual-row">
      <label><span class="manual-label">${label("vocabularySourceWord")}</span>
        <input data-manual="source_term" autocomplete="off"></label>
      <label><span class="manual-label">${label("vocabularyTranslation")}</span>
        <input data-manual="target_translation" autocomplete="off"></label>
      <label><span class="manual-label">${label("vocabularyExample")}</span>
        <input data-manual="source_example_sentence" autocomplete="off"></label>
      <button type="button" class="manual-remove" data-remove-row
        aria-label="${label("vocabularyRemove")}" title="${label("vocabularyRemove")}">&times;</button>
    </div>`;
  }

  function addManualRow(focus = false) {
    manualRows?.insertAdjacentHTML("beforeend", manualRowHtml());
    if (focus) manualRows?.lastElementChild?.querySelector("input")?.focus();
  }

  function collectManualRows() {
    return [...(manualRows?.querySelectorAll(".manual-row") || [])].map(row => {
      const entry = {};
      row.querySelectorAll("[data-manual]").forEach(input => {
        entry[input.dataset.manual] = input.value.trim();
      });
      return entry;
    }).filter(entry => entry.source_term || entry.target_translation);
  }

  function markIncompleteRows() {
    let first = null;
    for (const row of manualRows?.querySelectorAll(".manual-row") || []) {
      const word = row.querySelector('[data-manual="source_term"]')?.value.trim();
      const translation = row.querySelector('[data-manual="target_translation"]')?.value.trim();
      const half = Boolean(word) !== Boolean(translation);
      row.classList.toggle("is-incomplete", half);
      if (half && !first) first = row;
    }
    return first;
  }

  document.querySelector("#addManualRow")?.addEventListener("click", () => addManualRow(true));
  manualRows?.addEventListener("click", event => {
    if (!event.target.closest("[data-remove-row]")) return;
    event.target.closest(".manual-row")?.remove();
    if (!manualRows.children.length) addManualRow();   // never leave the student with nothing
  });
  // Enter in the last row adds the next one, so a list can be typed without reaching
  // for the mouse.
  manualRows?.addEventListener("keydown", event => {
    if (event.key !== "Enter" || !event.target.matches("[data-manual]")) return;
    event.preventDefault();
    const row = event.target.closest(".manual-row");
    if (row === manualRows.lastElementChild) addManualRow(true);
    else row?.nextElementSibling?.querySelector("input")?.focus();
  });

  const dropzone = document.querySelector("#vocabularyFileField");
  const fileInput = dropzone?.querySelector('input[type="file"]');
  const uploadTitle = dropzone?.querySelector("[data-upload-title]");
  const uploadHint = dropzone?.querySelector("[data-upload-hint]");
  const idleTitle = uploadTitle?.textContent ?? "";
  const idleHint = uploadHint?.textContent ?? "";
  const fileExtras = document.querySelector("#vocabularyFileExtras");
  const imagesMount = document.querySelector("#vocabularyImages");
  const retryButton = document.querySelector("#vocabularyRetryFailed");

  // ---- Photo or PDF: several pages per import ------------------------------------------
  // The pages gather in one ordered gallery (static/js/image-collection.js): picks add up,
  // the camera has its own button, every page can be seen, moved, replaced or removed.
  // The real file input is invisible inside the dropzone, so the zone itself has to say
  // what was picked - otherwise a tap that worked looks identical to one that didn't.
  const pages = imagesMount && fileInput ? createImageCollection({
    root: imagesMount,
    inputs: [fileInput],
    cameraInputs: [document.querySelector("#vocabularyCameraInput")].filter(Boolean),
    dropZone: dropzone,
    max: Number(imagesMount.dataset.max) || 40,
    maxFileBytes: Number(imagesMount.dataset.maxBytes) || 0,
    accept: fileInput.getAttribute("accept") || "",
    labels: true,
    onChange: collection => {
      const count = collection.count();
      dropzone.classList.toggle("has-file", count > 0);
      if (uploadTitle && uploadHint) {
        uploadTitle.textContent = count ? t("vocabularyAddMorePages") : idleTitle;
        uploadHint.textContent = count ? t("imgcolCount", { count, max: collection.max() }) : idleHint;
      }
      if (retryButton) retryButton.hidden = !(collection.failed() && !collection.busy());
    },
    onError: message => {
      const error = document.querySelector("#vocabularyImportError");
      error.textContent = message;
      error.classList.remove("hidden");
    },
  }) : null;
  window.__vocabularyPages = pages;
  // One import per submit, kept across retries: a page that failed is sent to the same
  // import again, never to a second one.
  const upload = { importId: "", createKey: uuid(), pageIds: new Map(), positions: new Map(), read: new Set() };

  // The place a page asks for: after every saved page before it, never before its place in
  // the list. A saved page keeps its place, so removing a page that failed (which shifts
  // the saved pages after it in the list) never makes a retry ask for a taken place.
  function positionFor(key) {
    if (upload.positions.has(key)) return upload.positions.get(key);
    const keys = pages.keys();
    const index = keys.indexOf(key);
    const savedBefore = keys.slice(0, Math.max(0, index))
      .reduce((highest, other) => Math.max(highest, upload.positions.get(other) || 0), 0);
    return Math.max(index + 1, savedBefore + 1);
  }

  // The fourth method is not an import at all: the word scanner takes the form's place.
  const scanSection = document.querySelector("#vocabularyScanSection");
  function showMethod(kind) {
    document.querySelector("#vocabularyFileField").classList.toggle("hidden", kind !== "file");
    fileExtras?.classList.toggle("hidden", kind !== "file");
    document.querySelector("#vocabularyTextField").classList.toggle("hidden", kind !== "text");
    manualField?.classList.toggle("hidden", kind !== "manual");
    document.querySelectorAll(".import-only").forEach(part => part.classList.toggle("hidden", kind === "scan"));
    scanSection?.classList.toggle("hidden", kind !== "scan");
    // Typing starts on an empty row rather than an empty panel with a button.
    if (kind === "manual" && !manualRows?.children.length) addManualRow();
  }
  showMethod(document.querySelector("[name=source_kind]:checked")?.value || "file");

  importForm.addEventListener("change", event => {
    if (event.target.name === "source_kind") {
      showMethod(event.target.value);
    }
  });
  // Dropped files join the gallery (image-collection.js handles the drop zone).

  // Each page on its own request: the first creates the import, the others join it at
  // the place the student gave them; then each is read on its own request (local OCR).
  async function sendPage(entry, { onProgress }) {
    const position = positionFor(entry.key);
    const name = entry.file.name || `page-${position}.jpg`;
    const created = !upload.importId;
    const body = created ? new FormData(importForm) : new FormData();
    body.delete("file");
    body.append("file", entry.file, name);
    body.set("position", String(position));
    if (entry.item.label) body.set("label", entry.item.label);
    try {
      const { data } = await xhrUpload(
        created ? "/api/vocabulary/imports" : `/api/vocabulary/imports/${upload.importId}/pages`, body,
        { onProgress, headers: created ? { "Idempotency-Key": upload.createKey } : {} });
      if (created) {
        upload.importId = data.vocabulary_import.id;
        const first = (data.vocabulary_import.pages || []).find(page => page.position === position)
          || data.vocabulary_import.pages?.[0];
        if (first) {
          upload.pageIds.set(entry.key, first.id);
          upload.positions.set(entry.key, first.position);
        }
      } else {
        upload.pageIds.set(entry.key, data.page.id);
        upload.positions.set(entry.key, data.page.position);
      }
      return { results: [{ index: 0, status: data.duplicate ? "duplicate" : "added" }] };
    } catch (error) {
      // The server refused this one file (type, size, a damaged picture): say why next to
      // it and carry on with the others. Anything else (no connection, a limit) stops.
      if (error.status === 400 || error.status === 409) {
        return { results: [{ index: 0, status: "failed", message: error.message }] };
      }
      throw error;
    }
  }

  async function readPages(status) {
    const keys = pages.keys().filter(key => upload.pageIds.has(key));
    for (const [index, key] of keys.entries()) {
      if (upload.read.has(key)) continue;
      status.textContent = t("vocabularyReadingPage", { current: index + 1, total: keys.length });
      try {
        await api(`/api/vocabulary/imports/${upload.importId}/pages/${upload.pageIds.get(key)}/extract`,
          { method: "POST", body: {} });
        upload.read.add(key);
      } catch (error) {
        pages.setStatus(key, "failed", { error: error.message });
        throw error;
      }
    }
  }

  // Uploads and reads every page; resolves the import's id (null while one is busy).
  async function submitPages(status) {
    if (!pages?.count()) throw new Error(t("imgcolChooseFirst"));
    if (pages.busy()) return null;
    if (retryButton) retryButton.hidden = true;
    const outcome = await uploadCollection(pages, {
      batch: { maxFiles: 1, maxBytes: Number.MAX_SAFE_INTEGER },
      send: ([entry], options) => sendPage(entry, options),
      onStep: ({ from, total }) => { status.textContent = t("imgcolUploadingOne", { number: from, total }); },
    });
    if (!outcome.ok) {
      if (retryButton) retryButton.hidden = false;
      throw new Error([outcome.error?.message || "", t("imgcolFailedSummary", { count: outcome.failed })]
        .filter(Boolean).join(" "));
    }
    try {
      await readPages(status);
    } catch (error) {
      if (retryButton) retryButton.hidden = false;
      throw error;
    }
    return upload.importId;
  }

  retryButton?.addEventListener("click", () => importForm.requestSubmit());
  importForm.addEventListener("submit", async event => {
    event.preventDefault();
    const status = document.querySelector("#vocabularyImportStatus");
    const error = document.querySelector("#vocabularyImportError");
    error.classList.add("hidden");
    try {
      if (manualPayload) {
        // A word with no translation is dropped by the parser. That used to be caught
        // later, on a review screen; now it is caught here, next to the empty box, which
        // is where the student can actually fix it.
        const incomplete = markIncompleteRows();
        if (incomplete) {
          error.textContent = t("vocabularyFillBothBoxes");
          error.classList.remove("hidden");
          incomplete.querySelector("input")?.focus();
          return;
        }
        manualPayload.value = JSON.stringify(collectManualRows());
      }
      let id;
      if (importForm.querySelector("[name=source_kind]:checked")?.value === "file" && pages) {
        // Photos and PDFs: several pages, each uploaded and read on its own request.
        id = await submitPages(status);
        if (!id) return;
      } else {
        status.textContent = t("vocabularyUploading");
        const created = await api("/api/vocabulary/imports", {
          method: "POST", body: new FormData(importForm),
        });
        id = created.vocabulary_import.id;
        status.textContent = t("vocabularyExtracting");
        await api(`/api/vocabulary/imports/${id}/extract`, { method: "POST", body: {} });
      }
      status.textContent = t("vocabularyValidating");
      await api(`/api/vocabulary/imports/${id}/validate`, { method: "POST", body: {} });
      // Students land directly in the card editor, where every pair is still visible
      // and editable before anything is saved - no separate review screen. auto_confirm
      // keeps flagged entries at their original reading; the review page remains
      // reachable by URL for rescanning a page or merging duplicates.
      status.textContent = t("vocabularyBuildingCards");
      const built = await api(`/api/vocabulary/imports/${id}/generate`, {
        method: "POST", body: { directions: ["source_to_target"], include_examples: true, auto_confirm: true },
      });
      location.href = built.creator_url;
    } catch (caught) {
      status.textContent = "";
      error.textContent = caught.message;
      error.classList.remove("hidden");
    }
  });
}

// ---- Review: two boxes per word, and only the words that need one -------------------
// The old row showed six fields, a confidence percentage, a page number, an English
// explanation string and three buttons - for every entry, including the ones nothing was
// wrong with. What a student actually decides here is: is this pair right, or not. That
// is two inputs and two buttons. Everything else still exists, one tap away under "More".
let showAllEntries = false;
// Rows that had an open question when the page loaded. They stay listed after the
// student answers them, so answering the last one does not swap one row for forty.
// A WeakSet rather than a flag on the entry, which would be sent back to the server.
const askedAbout = new WeakSet();

function isFlagged(entry) {
  // The server decides this and marks the rest confirmed, so the rule lives in one
  // place (learnova/vocabulary/service.py) instead of being re-implemented here.
  return !entry.user_confirmed;
}

function reasonFor(entry) {
  const status = entry.status || "needs_review";
  if (!entry.source_term || !entry.target_translation) return t("vocabularyStatus_missing_translation");
  return t(`vocabularyStatus_${status}`);
}

function moreHtml(entry) {
  const example = entry.example_ai_generated
    ? ` <small>${escapeHtml(t("vocabularyAiGenerated"))}</small>` : "";
  const merge = entry.status === "duplicate"
    ? `<button data-merge type="button">${escapeHtml(t("vocabularyMerge"))}</button>` : "";
  const origin = entry.page_number
    ? `<small class="review-origin">${escapeHtml(t("vocabularyPage"))} ${entry.page_number}</small>` : "";
  return `<details class="review-more">
    <summary>${escapeHtml(t("vocabularyMore"))}</summary>
    <label><span class="review-label">${escapeHtml(t("vocabularyAlternatives"))}</span>
      <input data-field="alternatives" value="${escapeHtml((entry.alternatives || []).join(", "))}"></label>
    <label><span class="review-label">${escapeHtml(t("vocabularyExample"))}${example}</span>
      <textarea data-field="source_example_sentence">${escapeHtml(entry.source_example_sentence || "")}</textarea></label>
    <label><span class="review-label">${escapeHtml(t("vocabularyPartOfSpeech"))}</span>
      <input data-field="part_of_speech" value="${escapeHtml(entry.part_of_speech || "")}"></label>
    <label><span class="review-label">${escapeHtml(t("vocabularyType"))}</span>
      <select data-field="entry_kind">${["word", "phrase", "sentence"].map(kind =>
        `<option value="${kind}"${(entry.entry_kind || "word") === kind ? " selected" : ""}>${
          escapeHtml(t(`vocabularyKind_${kind}`))}</option>`).join("")}</select></label>
    <div class="row-actions">
      <button data-generate-example type="button">${escapeHtml(t("vocabularyGenerateExample"))}</button>
      ${merge}
      <button data-split type="button">${escapeHtml(t("vocabularySplit"))}</button>
    </div>
    ${origin}
  </details>`;
}

function rowHtml(entry, index) {
  const flagged = isFlagged(entry);
  const suggestion = entry.suggested_translation
    ? `<div class="review-suggestion">
        <span>${escapeHtml(t("vocabularySuggested"))}: <b>${escapeHtml(entry.suggested_translation)}</b></span>
        <button data-accept-suggestion type="button">${escapeHtml(t("vocabularyAcceptSuggestion"))}</button>
        <button data-keep-original type="button">${escapeHtml(t("vocabularyKeepOriginal"))}</button>
      </div>`
    : "";
  const reason = flagged
    ? `<p class="review-reason">${escapeHtml(reasonFor(entry))}</p>` : "";
  const confirm = flagged
    ? `<button data-confirm type="button" class="review-ok"
        aria-label="${escapeHtml(t("vocabularyLooksRight"))}" title="${escapeHtml(t("vocabularyLooksRight"))}">&check;</button>`
    : "";
  return `<article class="review-row${flagged ? " is-flagged" : ""}" data-index="${index}">
    <div class="review-pair">
      <label><span class="review-label">${escapeHtml(t("vocabularySourceWord"))}</span>
        <input data-field="source_term" autocomplete="off" value="${escapeHtml(entry.source_term || "")}"></label>
      <span class="review-arrow" aria-hidden="true">&rarr;</span>
      <label><span class="review-label">${escapeHtml(t("vocabularyTranslation"))}</span>
        <input data-field="target_translation" autocomplete="off" value="${escapeHtml(entry.target_translation || "")}"></label>
      <div class="review-row-actions">${confirm}<button data-remove type="button" class="review-drop"
        aria-label="${escapeHtml(t("vocabularyRemove"))}" title="${escapeHtml(t("vocabularyRemove"))}">&times;</button></div>
    </div>
    ${reason}
    ${suggestion}
    ${moreHtml(entry)}
  </article>`;
}

function renderReview() {
  const isImport = Boolean(reviewPage.dataset.importId);
  const flagged = reviewEntries.filter(isFlagged).length;
  // Editing a saved list is a different job from checking a fresh import: there the
  // student came to change something, so every row is shown.
  const asked = reviewEntries.some(item => askedAbout.has(item));
  const hideQuiet = isImport && !showAllEntries && asked;
  const visible = reviewEntries
    .map((entry, index) => [entry, index])
    .filter(([entry]) => !hideQuiet || askedAbout.has(entry));
  document.querySelector("#vocabularyReviewRows").innerHTML =
    visible.map(([entry, index]) => rowHtml(entry, index)).join("");

  const count = document.querySelector("#vocabularyReviewCount");
  if (count) {
    count.textContent = isImport && flagged
      ? t("vocabularyNeedALook", { flagged, total: reviewEntries.length })
      : t("vocabularyWordCount", { total: reviewEntries.length });
  }
  const toggle = document.querySelector("#showAllVocabulary");
  if (toggle) {
    // Tied to whether rows are being hidden, not to whether anything is still
    // open: answering the last question must not remove the way back to the rest.
    toggle.classList.toggle("hidden", !isImport || !asked);
    toggle.textContent = showAllEntries
      ? t("vocabularyShowOnlyFlagged")
      : t("vocabularyShowAll", { total: reviewEntries.length });
  }
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
// A multi-page import: which page the photo shows and a rescan replaces.
let shownImport = null;
function showPagePhoto(imported) {
  shownImport = imported || shownImport;
  const select = document.querySelector("#vocabularyRescanPage");
  const pageList = shownImport?.pages || [];
  if (select) {
    const chosen = select.value;
    select.hidden = pageList.length < 2;
    select.replaceChildren(...pageList.map(page => {
      const option = document.createElement("option");
      option.value = page.id;
      option.textContent = t("imgcolPage", { number: page.first_page_number })
        + (page.label ? ` · ${page.label}` : page.filename ? ` · ${page.filename}` : "");
      return option;
    }));
    if (pageList.some(page => page.id === chosen)) select.value = chosen;
  }
  const page = pageList.find(item => item.id === select?.value) || pageList[0];
  const photo = document.querySelector("#vocabularyPhoto");
  if (!photo) return;
  const url = page ? page.preview_url : shownImport?.preview_url;
  photo.hidden = !url;
  if (url) photo.querySelector("img").src = url;
}

function adoptEntries(entries) {
  reviewEntries = entries;
  reviewEntries.filter(isFlagged).forEach(item => askedAbout.add(item));
  renderReview();
}

async function initReview() {
  const id = reviewPage.dataset.importId;
  const data = await api(id
    ? `/api/vocabulary/imports/${id}`
    : `/api/vocabulary/lists/${reviewPage.dataset.listId}`);
  if (id) showPagePhoto(data.vocabulary_import);
  adoptEntries(id ? data.vocabulary_import.entries : data.vocabulary_list.entries);
}

// ---- Rescan: a new photo of the same page, folded into what is already here ---------
// Typing a missed word is the quick fix. A rescan is for a photo that was bad all over:
// the student's own edits are saved first, the server replaces only the rows that are
// still open, and anything the new reading found that the first missed is added.
async function rescanPage(file) {
  const id = reviewPage.dataset.importId;
  const status = document.querySelector("#vocabularyReviewStatus");
  await saveReview();
  status.textContent = t("vocabularyRescanning");
  const body = new FormData();
  body.append("file", file);
  const pageChoice = document.querySelector("#vocabularyRescanPage");
  if (pageChoice && !pageChoice.hidden && pageChoice.value) body.append("page_id", pageChoice.value);
  const result = await api(`/api/vocabulary/imports/${id}/rescan`, { method: "POST", body });
  showPagePhoto(result.vocabulary_import);
  adoptEntries(result.vocabulary_import.entries);
  status.textContent = t("vocabularyRescanned", { replaced: result.replaced, added: result.added });
}
if (reviewPage) {
  initReview().catch(error => toast(error.message, "error"));
  document.querySelector("#vocabularyReviewRows").addEventListener("input", event => {
    const row = event.target.closest("[data-index]");
    if (!row || !event.target.dataset.field) return;
    const entry = reviewEntries[Number(row.dataset.index)];
    const field = event.target.dataset.field;
    if (field === "entry_kind") return;   // handled on "change" above
    entry[field] = field === "alternatives"
      ? event.target.value.split(",").map(value => value.trim()).filter(Boolean)
      : event.target.value;
    // Typing in a flagged row answers the question it was asking. The row is not
    // re-rendered here, because that would take the caret away mid-word.
    entry.user_confirmed = true;
    row.classList.remove("is-flagged");
    row.querySelector(".review-reason")?.remove();
    row.querySelector("[data-confirm]")?.remove();
  });
  document.querySelector("#vocabularyReviewRows").addEventListener("change", event => {
    const row = event.target.closest("[data-index]");
    if (!row || event.target.dataset.field !== "entry_kind") return;
    const entry = reviewEntries[Number(row.dataset.index)];
    entry.entry_kind = event.target.value;
    entry.user_confirmed = true;
  });
  document.querySelector("#vocabularyReviewRows").addEventListener("click", async event => {
    const row = event.target.closest("[data-index]");
    if (!row) return;
    const index = Number(row.dataset.index);
    const entry = reviewEntries[index];
    if (event.target.closest("[data-remove]")) reviewEntries.splice(index, 1);
    if (event.target.closest("[data-confirm]")) entry.user_confirmed = true;
    if (event.target.closest("[data-accept-suggestion]")) {
      entry.target_translation = entry.suggested_translation;
      entry.suggested_translation = "";
      entry.user_confirmed = true;
      entry.status = "valid";
    }
    if (event.target.closest("[data-keep-original]")) {
      entry.suggested_translation = "";
      entry.user_confirmed = true;
    }
    if (event.target.closest("[data-split]")) {
      const half = { ...entry, source_term: "", target_translation: "", user_confirmed: false };
      askedAbout.add(half);
      reviewEntries.splice(index + 1, 0, half);
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
    // Opening "More" must not redraw the row out from under the tap.
    if (!event.target.closest("summary")) renderReview();
  });
  document.querySelector("#showAllVocabulary").addEventListener("click", () => {
    showAllEntries = !showAllEntries;
    renderReview();
  });
  document.querySelector("#vocabularyRescanPage")?.addEventListener("change", () => showPagePhoto(null));
  document.querySelector("#vocabularyRescanFile")?.addEventListener("change", async event => {
    const input = event.target;
    const file = input.files?.[0];
    if (!file) return;
    try {
      await rescanPage(file);
    } catch (error) {
      document.querySelector("#vocabularyReviewStatus").textContent = "";
      toast(error.message, "error");
    } finally {
      input.value = "";
    }
  });
  document.querySelector("#addVocabularyEntry").addEventListener("click", () => {
    const fresh = {
      source_term: "", target_translation: "", alternatives: [], included: true,
      status: "needs_review", confidence: 1, user_confirmed: false };
    askedAbout.add(fresh);
    reviewEntries.push(fresh);
    renderReview();
    const rows = document.querySelectorAll("#vocabularyReviewRows .review-row");
    rows[rows.length - 1]?.querySelector("input")?.focus();
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
          directions: directions.length ? directions : ["source_to_target"],
          include_examples: document.querySelector("#includeVocabularyExamples").checked,
        },
      });
      location.href = result.creator_url;
    } catch (error) { toast(error.message, "error"); }
  });
}

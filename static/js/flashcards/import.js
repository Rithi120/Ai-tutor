import { api, escapeHtml, optionsHtml, SUBJECTS, FLAGS, t, selectedLanguage } from "./common.js";

let documentState = null;
let sourceKind = FLAGS.flashcard_pdf_import ? "pdf" : "image";
let busy = false;
const $ = selector => document.querySelector(selector);

function showError(message = "") {
  $("#importError").textContent = message;
  $("#importError").classList.toggle("hidden", !message);
}
function step(number) {
  document.querySelectorAll(".import-steps li").forEach((el, index) => el.classList.toggle("active", index < number));
}
function configureInput() {
  const input = $("#importFile");
  input.value = "";
  if (sourceKind === "pdf") {
    input.accept = ".pdf,application/pdf";
    input.removeAttribute("capture");
  } else {
    input.accept = ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp";
    input.setAttribute("capture", "environment");
  }
}
function warningHtml(warnings) {
  if (!warnings?.length) return "";
  return `<ul>${warnings.map(value => `<li>${escapeHtml(value)}</li>`).join("")}</ul>`;
}
function statusLabel(status) {
  return t({
    success: "fcExtractSuccess", low_text: "fcExtractLowText",
    blank: "fcExtractBlank", needs_ocr: "fcExtractNeedsOcr", failed: "fcExtractFailed",
  }[status] || status);
}
function renderReview() {
  const doc = documentState;
  $("#uploadPanel").classList.add("hidden");
  $("#reviewPanel").classList.remove("hidden");
  $("#fileSummary").textContent = `${doc.filename} · ${doc.page_count} ${t("fcImportPages")} · ${t("fcConfidence")}: ${Math.round((doc.confidence || 0) * 100)}%`;
  $("#overallWarnings").innerHTML = warningHtml(doc.warnings);
  $("#imagePreview").classList.toggle("hidden", doc.source_type !== "image");
  if (doc.preview_url) $("#imagePreview").src = doc.preview_url;
  $("#pageControls").classList.toggle("hidden", doc.source_type !== "pdf");
  $("#pageReviews").innerHTML = (doc.pages || []).map(page => `
    <article class="import-page-review" data-page="${page.page_number}">
      <header>
        <label><input type="checkbox" data-selected${page.selected ? " checked" : ""}> ${t("fcPage")} ${page.page_number}</label>
        <span class="status-badge status-${escapeHtml(page.status)}">${escapeHtml(statusLabel(page.status))}</span>
        <span>${t("fcConfidence")}: ${Math.round((page.confidence || 0) * 100)}%</span>
        <span>${escapeHtml(page.source === "ocr" ? t("fcOcrSource") : t("fcNativeSource"))}</span>
      </header>
      ${warningHtml(page.warnings)}
      <label class="field">${t("fcExtractedText")}<textarea data-text rows="8">${escapeHtml(page.text || "")}</textarea></label>
      ${doc.source_type === "pdf" && ["needs_ocr", "low_text", "failed"].includes(page.status) ? `<button type="button" data-ocr class="ghost-button">${t("fcRunOcr")}</button>` : ""}
    </article>`).join("");
  step(5);
}
async function upload() {
  if (busy || !$("#importFile").files[0]) return showError(t("fcChooseImportFile"));
  busy = true; showError(""); $("#importStatus").textContent = t("fcUploading");
  try {
    const form = new FormData();
    form.append("file", $("#importFile").files[0]);
    const data = await api("/api/flashcards/imports", { method: "POST", body: form });
    documentState = data.document;
    step(3); $("#importStatus").textContent = t("fcExtracting");
    const extracted = await api(`/api/flashcards/imports/${documentState.id}/extract`, { method: "POST", body: {} });
    documentState = extracted.document;
    renderReview();
  } catch (error) { showError(error.message); $("#importStatus").textContent = ""; }
  finally { busy = false; }
}
function collectPages() {
  return [...document.querySelectorAll(".import-page-review")].map(row => ({
    page_number: Number(row.dataset.page),
    selected: row.querySelector("[data-selected]").checked,
    text: row.querySelector("[data-text]").value,
  }));
}
async function saveReview() {
  try {
    await api(`/api/flashcards/imports/${documentState.id}/content`, { method: "PUT", body: { pages: collectPages() } });
    $("#generationPanel").classList.remove("hidden");
    step(6); showError("");
    $("#generationPanel").scrollIntoView({ behavior: "smooth" });
  } catch (error) { showError(error.message); }
}
async function runOcr(pageNumber) {
  if (busy) return;
  busy = true;
  try {
    const data = await api(`/api/flashcards/imports/${documentState.id}/ocr-page`, { method: "POST", body: { page_number: pageNumber } });
    documentState = data.document; renderReview();
  } catch (error) { showError(error.message); }
  finally { busy = false; }
}
async function generate() {
  if (busy) return;
  busy = true; showError(""); $("#generateImport").disabled = true;
  try {
    const data = await api(`/api/flashcards/imports/${documentState.id}/generate`, { method: "POST", body: {
      subject: $("#importSubject").value, grade: $("#importGrade").value.trim(),
      difficulty: $("#importDifficulty").value, count: Number($("#importCount").value),
      card_type: $("#importType").value, content_language: $("#importLanguage").value,
    } });
    step(8); window.location.href = data.creator_url;
  } catch (error) { showError(error.message); }
  finally { busy = false; $("#generateImport").disabled = false; }
}
async function cancelImport() {
  if (documentState) {
    try { await api(`/api/flashcards/imports/${documentState.id}`, { method: "DELETE" }); } catch (_) {}
  }
  window.location.href = "/flashcards";
}
function parseRange(value) {
  const selected = new Set();
  value.split(",").forEach(part => {
    const match = part.trim().match(/^(\d+)(?:-(\d+))?$/);
    if (!match) return;
    const start = Number(match[1]), end = Number(match[2] || match[1]);
    for (let value = Math.min(start, end); value <= Math.max(start, end); value++) selected.add(value);
  });
  return selected;
}
function init() {
  $("#importSubject").innerHTML = optionsHtml(SUBJECTS, "Mathematics");
  $("#importLanguage").value = selectedLanguage === "German" ? "de" : "en";
  document.querySelectorAll("[data-kind]").forEach(tab => tab.addEventListener("click", () => {
    sourceKind = tab.dataset.kind;
    document.querySelectorAll("[data-kind]").forEach(item => item.setAttribute("aria-selected", String(item === tab)));
    configureInput();
  }));
  configureInput();
  $("#uploadButton").addEventListener("click", upload);
  $("#saveReview").addEventListener("click", saveReview);
  $("#generateImport").addEventListener("click", generate);
  $("#cancelImport").addEventListener("click", cancelImport);
  $("#replaceFile").addEventListener("click", () => window.location.reload());
  $("#retryExtract").addEventListener("click", async () => {
    try {
      const data = await api(`/api/flashcards/imports/${documentState.id}/extract`, { method: "POST", body: {} });
      documentState = data.document; renderReview();
    } catch (error) { showError(error.message); }
  });
  $("#selectAll").addEventListener("click", () => document.querySelectorAll("[data-selected]").forEach(input => { input.checked = true; }));
  $("#applyRange").addEventListener("click", () => {
    const range = parseRange($("#pageRange").value);
    document.querySelectorAll(".import-page-review").forEach(row => { row.querySelector("[data-selected]").checked = range.has(Number(row.dataset.page)); });
  });
  $("#pageReviews").addEventListener("click", event => {
    const button = event.target.closest("[data-ocr]");
    if (button) runOcr(Number(button.closest(".import-page-review").dataset.page));
  });
}
document.addEventListener("DOMContentLoaded", init);

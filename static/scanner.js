const projectForm = document.querySelector("#projectCreateForm");
const scannerModal = document.querySelector("#scannerModal");
const cameraVideo = document.querySelector("#cameraPreview");
const liveCapture = document.querySelector("#liveCapture");
const captureReview = document.querySelector("#captureReview");
const cameraFallback = document.querySelector("#cameraFallback");
const capturedPreview = document.querySelector("#capturedPreview");
const selectedMaterial = document.querySelector("#selectedMaterial");
const pageCounter = document.querySelector("#pageCounter");
const scanThumbnails = document.querySelector("#scanThumbnails");
let cameraStream = null;
let facingMode = "environment";
let acceptedScans = [];
let pendingScan = null;
let retakeIndex = null;
const ui = key => window.LEARNOVA_I18N?.[key] || key;
// Without live camera access (no getUserMedia, permission refused, no camera) the fallback
// is the phone's own camera app through a file input, feeding the same review step.

// static/js/project-create.js (the multi-image collection) marks the form when it runs.
// Then the accepted scans are handed to its one ordered list when the scanner closes, and
// it sends the form; without it (the module failed to load) this script sends everything.
const collectionActive = () => projectForm?.dataset.imageCollection === "ready";
const collectionCount = () => Number(projectForm?.dataset.collectionCount || 0);

function handOverScans() {
  if (!collectionActive() || !acceptedScans.length) return;
  const scans = acceptedScans.map(scan => ({ blob: scan.blob, rotation: scan.rotation, crop: { ...scan.crop } }));
  acceptedScans.forEach(scan => URL.revokeObjectURL(scan.previewUrl));
  acceptedScans = [];
  retakeIndex = null;
  document.dispatchEvent(new CustomEvent("learnova:scans-finished", { detail: { scans } }));
  renderScans();
}

function closeScanner() {
  stopCamera();
  if (pendingScan) URL.revokeObjectURL(pendingScan.previewUrl);
  pendingScan = null;
  retakeIndex = null;
  captureReview.classList.add("hidden");
  scannerModal.classList.add("hidden");
  handOverScans();
}

function showCaptureStep() {
  // Live video when there is a stream; the camera-app fallback otherwise (a dead video
  // with a capture button that does nothing was shown after a retake without a stream).
  captureReview.classList.add("hidden");
  liveCapture.classList.toggle("hidden", !cameraStream);
  cameraFallback.classList.toggle("hidden", Boolean(cameraStream));
}

function stopCamera() {
  if (cameraStream) cameraStream.getTracks().forEach(track => track.stop());
  cameraStream = null;
  cameraVideo.srcObject = null;
}

async function startCamera() {
  cameraFallback.classList.add("hidden");
  liveCapture.classList.remove("hidden");
  if (!navigator.mediaDevices?.getUserMedia) {
    liveCapture.classList.add("hidden");
    cameraFallback.classList.remove("hidden");
    return;
  }
  stopCamera();
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: facingMode }, width: { ideal: 1920 }, height: { ideal: 2560 } },
      audio: false
    });
    cameraVideo.srcObject = cameraStream;
    await cameraVideo.play();
    const devices = await navigator.mediaDevices.enumerateDevices();
    document.querySelector("#switchCamera").classList.toggle("hidden", devices.filter(item => item.kind === "videoinput").length < 2);
  } catch (error) {
    stopCamera();
    liveCapture.classList.add("hidden");
    cameraFallback.classList.remove("hidden");
    // A refused permission needs different advice from a missing camera.
    const refused = error.name === "NotAllowedError" || error.name === "SecurityError";
    cameraFallback.querySelector("p").textContent = ui(refused ? "cameraBlocked" : "cameraUnavailableNative");
  }
}

function reviewScan(blob) {
  pendingScan = { blob, previewUrl: URL.createObjectURL(blob), rotation: 0, crop: { left: 0, right: 0, top: 0, bottom: 0 } };
  capturedPreview.src = pendingScan.previewUrl;
  capturedPreview.style.transform = "rotate(0deg)";
  document.querySelectorAll("[data-crop]").forEach(input => { input.value = 0; });
  liveCapture.classList.add("hidden"); cameraFallback.classList.add("hidden"); captureReview.classList.remove("hidden");
}

document.querySelector("#nativeCameraInput")?.addEventListener("change", event => {
  const file = event.target.files?.[0];
  event.target.value = "";
  if (file) reviewScan(file);
});

// A project can start from notes, a Kompetenzraster, or topics typed in - any of them.
const gridCount = () => document.querySelector("#competencyGridInput")?.files.length || 0;
const typedTopics = () => (document.querySelector("#projectTopics")?.value || "").split(/[\n,]/).map(item => item.trim()).filter(Boolean).length;

function updateMaterialSummary() {
  const uploaded = collectionActive() ? collectionCount()
    : [...document.querySelectorAll('#pdfInput, #imageUploadInput')].reduce((sum, input) => sum + input.files.length, 0);
  const parts = [];
  if (uploaded) parts.push(`${uploaded} ${ui(uploaded === 1 ? "uploadedFile" : "uploadedFiles")}`);
  if (gridCount()) parts.push(`${gridCount()} ${ui("competencyGridFiles")}`);
  if (typedTopics()) parts.push(`${typedTopics()} ${ui("typedTopics")}`);
  if (acceptedScans.length) parts.push(`${acceptedScans.length} ${ui(acceptedScans.length === 1 ? "cameraPage" : "cameraPages")}`);
  selectedMaterial.textContent = parts.length ? `${parts.join(" + ")} ${ui("materialsReady")}` : ui("noPages");
  pageCounter.textContent = `${acceptedScans.length} ${ui(acceptedScans.length === 1 ? "pageAccepted" : "pagesAccepted")}`;
}

function renderScans() {
  scanThumbnails.innerHTML = acceptedScans.map((scan, index) => `<article draggable="true" data-index="${index}"><img src="${scan.previewUrl}" alt="${ui("cameraPage")} ${index + 1}"><b>${ui("page")} ${index + 1}</b><div><button type="button" data-move="up" aria-label="${ui("moveUp")}">↑</button><button type="button" data-move="down" aria-label="${ui("moveDown")}">↓</button><button type="button" data-retake>${ui("retake")}</button><button type="button" data-delete>${ui("delete")}</button></div></article>`).join("");
  updateMaterialSummary();
}

document.querySelector("#openScanner").addEventListener("click", () => {
  scannerModal.classList.remove("hidden");
  startCamera();
});
document.querySelector("#closeScanner").addEventListener("click", closeScanner);
document.querySelector("#finishScanning").addEventListener("click", closeScanner);
document.querySelector("#switchCamera").addEventListener("click", () => { facingMode = facingMode === "environment" ? "user" : "environment"; startCamera(); });

document.querySelector("#capturePage").addEventListener("click", () => {
  if (!cameraVideo.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = cameraVideo.videoWidth; canvas.height = cameraVideo.videoHeight;
  canvas.getContext("2d").drawImage(cameraVideo, 0, 0);
  // Lossless and full sensor resolution on purpose (tests/test_handwriting_recognition.py):
  // faint pencil and thin minus signs must reach the server intact. The server keeps this
  // page and sends providers a sized copy, so the size here never reaches a provider.
  canvas.toBlob(blob => { if (blob) reviewScan(blob); }, "image/png");
});

document.querySelector("#rotateCapture").addEventListener("click", () => {
  if (!pendingScan) return;
  pendingScan.rotation = (pendingScan.rotation + 90) % 360;
  capturedPreview.style.transform = `rotate(${pendingScan.rotation}deg)`;
});
document.querySelectorAll("[data-crop]").forEach(input => input.addEventListener("input", () => {
  if (pendingScan) pendingScan.crop[input.dataset.crop] = Number(input.value) / 100;
}));
document.querySelector("#retakePage").addEventListener("click", () => {
  if (pendingScan) URL.revokeObjectURL(pendingScan.previewUrl);
  pendingScan = null;
  showCaptureStep();
});
document.querySelector("#acceptPage").addEventListener("click", () => {
  if (!pendingScan) return;
  if (retakeIndex === null || !acceptedScans[retakeIndex]) acceptedScans.push(pendingScan);
  else {
    // A retake replaces its page only now, when the new capture is accepted: closing the
    // scanner halfway through a retake keeps the old page.
    URL.revokeObjectURL(acceptedScans[retakeIndex].previewUrl);
    acceptedScans.splice(retakeIndex, 1, pendingScan);
  }
  pendingScan = null; retakeIndex = null;
  showCaptureStep(); renderScans();
});

scanThumbnails.addEventListener("click", event => {
  const card = event.target.closest("[data-index]"); if (!card) return;
  const index = Number(card.dataset.index);
  if (event.target.closest("[data-retake]")) { retakeIndex = index; if (pendingScan) URL.revokeObjectURL(pendingScan.previewUrl); pendingScan = null; showCaptureStep(); return; }
  if (event.target.closest("[data-delete]")) { URL.revokeObjectURL(acceptedScans[index].previewUrl); acceptedScans.splice(index, 1); if (retakeIndex !== null) retakeIndex = null; }
  if (event.target.closest("[data-move='up']") && index > 0) [acceptedScans[index - 1], acceptedScans[index]] = [acceptedScans[index], acceptedScans[index - 1]];
  if (event.target.closest("[data-move='down']") && index < acceptedScans.length - 1) [acceptedScans[index + 1], acceptedScans[index]] = [acceptedScans[index], acceptedScans[index + 1]];
  renderScans();
});
document.querySelectorAll("#pdfInput, #imageUploadInput, #competencyGridInput").forEach(input => input.addEventListener("change", updateMaterialSummary));
document.querySelector("#projectTopics")?.addEventListener("input", updateMaterialSummary);
document.addEventListener("learnova:collection-changed", updateMaterialSummary);

projectForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (collectionActive()) return;   // project-create.js sends it, in batches
  // One combined input (photos and PDFs) on the new form; the count works with either.
  const uploadCount = [...document.querySelectorAll("#pdfInput, #imageUploadInput")].reduce((sum, input) => sum + input.files.length, 0);
  if (!uploadCount && !acceptedScans.length && !gridCount() && !typedTopics()) { selectedMaterial.textContent = ui("chooseProjectInput"); return; }
  stopCamera();
  const button = document.querySelector("#createProjectButton"); const progress = document.querySelector("#uploadProgress");
  button.disabled = true; progress.classList.remove("hidden");
  const data = new FormData(projectForm);
  acceptedScans.forEach((scan, index) => {
    const extension = scan.blob.type === "image/png" ? "png" : scan.blob.type === "image/webp" ? "webp" : "jpg";
    data.append("camera_scans", scan.blob, `camera-page-${index + 1}.${extension}`);
  });
  data.append("scan_metadata", JSON.stringify(acceptedScans.map(scan => ({ rotation: scan.rotation, crop: scan.crop }))));
  try {
    const response = await fetch(projectForm.action || location.href, { method: "POST", body: data });
    if (response.redirected) { location.href = response.url; return; }
    if (!response.ok) throw new Error(ui("uploadFailed"));
    // The server answered with the page and its error messages instead of a redirect.
    // Show those messages here and keep the student's scans. Writing the returned page
    // over this one (document.write) would keep this page's CSP nonce, so every script
    // in the new page would be blocked and it would stop working.
    const returned = new DOMParser().parseFromString(await response.text(), "text/html");
    const messages = [...returned.querySelectorAll(".form-alert span")].map(node => node.textContent.trim()).filter(Boolean);
    selectedMaterial.textContent = messages.join(" ") || ui("uploadRetry");
    button.disabled = false; progress.classList.add("hidden");
  } catch (error) {
    selectedMaterial.textContent = error.message || ui("uploadRetry");
    button.disabled = false; progress.classList.add("hidden");
  }
});

window.addEventListener("pagehide", stopCamera);
updateMaterialSummary();

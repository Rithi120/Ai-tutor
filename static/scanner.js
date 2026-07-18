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
// The no-camera fallback remains the Upload Images workflow.

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
    cameraFallback.querySelector("p").textContent = `${ui("cameraUnavailable")} (${error.name || "camera error"})`;
  }
}

function updateMaterialSummary() {
  const uploaded = [...document.querySelectorAll('#pdfInput, #imageUploadInput')].reduce((sum, input) => sum + input.files.length, 0);
  const parts = [];
  if (uploaded) parts.push(`${uploaded} ${ui(uploaded === 1 ? "uploadedFile" : "uploadedFiles")}`);
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
document.querySelector("#closeScanner").addEventListener("click", () => { stopCamera(); scannerModal.classList.add("hidden"); });
document.querySelector("#finishScanning").addEventListener("click", () => { stopCamera(); scannerModal.classList.add("hidden"); });
document.querySelector("#switchCamera").addEventListener("click", () => { facingMode = facingMode === "environment" ? "user" : "environment"; startCamera(); });

document.querySelector("#capturePage").addEventListener("click", () => {
  if (!cameraVideo.videoWidth) return;
  const canvas = document.createElement("canvas");
  canvas.width = cameraVideo.videoWidth; canvas.height = cameraVideo.videoHeight;
  canvas.getContext("2d").drawImage(cameraVideo, 0, 0);
  canvas.toBlob(blob => {
    if (!blob) return;
    pendingScan = { blob, previewUrl: URL.createObjectURL(blob), rotation: 0, crop: { left: 0, right: 0, top: 0, bottom: 0 } };
    capturedPreview.src = pendingScan.previewUrl;
    capturedPreview.style.transform = "rotate(0deg)";
    document.querySelectorAll("[data-crop]").forEach(input => { input.value = 0; });
    liveCapture.classList.add("hidden"); captureReview.classList.remove("hidden");
  }, "image/png");
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
  pendingScan = null; captureReview.classList.add("hidden"); liveCapture.classList.remove("hidden");
});
document.querySelector("#acceptPage").addEventListener("click", () => {
  if (!pendingScan) return;
  if (retakeIndex === null) acceptedScans.push(pendingScan);
  else acceptedScans.splice(retakeIndex, 0, pendingScan);
  pendingScan = null; retakeIndex = null;
  captureReview.classList.add("hidden"); liveCapture.classList.remove("hidden"); renderScans();
});

scanThumbnails.addEventListener("click", event => {
  const card = event.target.closest("[data-index]"); if (!card) return;
  const index = Number(card.dataset.index);
  if (event.target.closest("[data-retake]")) { URL.revokeObjectURL(acceptedScans[index].previewUrl); acceptedScans.splice(index, 1); retakeIndex = index; pendingScan = null; captureReview.classList.add("hidden"); liveCapture.classList.remove("hidden"); renderScans(); return; }
  if (event.target.closest("[data-delete]")) { URL.revokeObjectURL(acceptedScans[index].previewUrl); acceptedScans.splice(index, 1); }
  if (event.target.closest("[data-move='up']") && index > 0) [acceptedScans[index - 1], acceptedScans[index]] = [acceptedScans[index], acceptedScans[index - 1]];
  if (event.target.closest("[data-move='down']") && index < acceptedScans.length - 1) [acceptedScans[index + 1], acceptedScans[index]] = [acceptedScans[index], acceptedScans[index + 1]];
  renderScans();
});
document.querySelectorAll("#pdfInput, #imageUploadInput").forEach(input => input.addEventListener("change", updateMaterialSummary));

projectForm.addEventListener("submit", async event => {
  event.preventDefault();
  const uploadCount = document.querySelector("#pdfInput").files.length + document.querySelector("#imageUploadInput").files.length;
  if (!uploadCount && !acceptedScans.length) { selectedMaterial.textContent = ui("choosePage"); return; }
  stopCamera();
  const button = document.querySelector("#createProjectButton"); const progress = document.querySelector("#uploadProgress");
  button.disabled = true; progress.classList.remove("hidden");
  const data = new FormData(projectForm);
  acceptedScans.forEach((scan, index) => data.append("camera_scans", scan.blob, `camera-page-${index + 1}.png`));
  data.append("scan_metadata", JSON.stringify(acceptedScans.map(scan => ({ rotation: scan.rotation, crop: scan.crop }))));
  try {
    const response = await fetch(projectForm.action || location.href, { method: "POST", body: data });
    if (response.redirected) { location.href = response.url; return; }
    if (!response.ok) throw new Error(ui("uploadFailed"));
    document.open(); document.write(await response.text()); document.close();
  } catch (error) {
    selectedMaterial.textContent = error.message || ui("uploadRetry");
    button.disabled = false; progress.classList.add("hidden");
  }
});

window.addEventListener("pagehide", stopCamera);
updateMaterialSummary();

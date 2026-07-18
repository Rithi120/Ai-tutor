const recognizeForm = document.querySelector("#recognizeForm");
const ui = key => window.LEARNOVA_I18N?.[key] || key;
if (recognizeForm) recognizeForm.addEventListener("submit", async event => {
  event.preventDefault();
  const overlay = document.querySelector("#processingOverlay");
  const label = document.querySelector("#processingLabel");
  overlay.classList.remove("hidden");
  const pageIds = JSON.parse(recognizeForm.dataset.pageIds || "[]");
  let failures = 0;
  for (let index = 0; index < pageIds.length; index += 1) {
    label.textContent = `${ui("recognizingPage")} ${index + 1} ${ui("of")} ${pageIds.length}…`;
    const url = recognizeForm.dataset.pageUrl.replace(/\/0\/recognize$/, `/${pageIds[index]}/recognize`);
    try { const response = await fetch(url, {method:"POST"}); if (!response.ok) failures += 1; }
    catch { failures += 1; }
  }
  label.textContent = failures ? `${failures} ${ui("reviewPages")}` : ui("readyReview");
  location.reload();
});

const list = document.querySelector("#reviewPageList");
if (list) {
  let dragged = null;
  list.querySelectorAll(".review-drag").forEach(handle => handle.setAttribute("draggable", "true"));
  list.addEventListener("dragstart", event => { const handle = event.target.closest(".review-drag"); if (!handle) return; dragged = handle.closest("[data-id]"); dragged?.classList.add("dragging"); });
  list.addEventListener("dragover", event => { event.preventDefault(); if (!dragged) return; const target = event.target.closest("[data-id]"); if (!target || target === dragged) return; const box = target.getBoundingClientRect(); list.insertBefore(dragged, event.clientY < box.top + box.height / 2 ? target : target.nextSibling); });
  list.addEventListener("dragend", async () => {
    dragged?.classList.remove("dragging"); dragged = null;
    const status = document.querySelector("#reviewOrderStatus"); const ids = [...list.children].map(card => Number(card.dataset.id)); status.textContent = ui("savingPageOrder");
    try { const response = await fetch(list.dataset.reorderUrl, {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({page_ids:ids})}); if (!response.ok) throw new Error(); status.textContent = ui("pageOrderSaved"); }
    catch { status.textContent = ui("pageOrderFailed"); }
  });
}

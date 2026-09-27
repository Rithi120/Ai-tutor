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

// Apply a model-suggested correction into its editable field (never applied automatically).
document.addEventListener("click", event => {
  const button = event.target.closest("[data-apply-suggestion]");
  if (!button) return;
  const field = document.getElementById(button.dataset.applySuggestion);
  if (field) {
    field.value = button.dataset.suggestion || field.value;
    field.focus();
    field.dispatchEvent(new Event("input", { bubbles: true }));  // let the region editor mark it reviewed
  }
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

/* =========================================================================
 * "Fix the words Learnova was unsure about" — tap a marked spot on the page
 * (or a card) and type what it really says. Writes straight into the existing
 * block_<id> fields, so the normal Save/Confirm flow persists the corrections.
 * No backend change: boxes are positioned from each block's stored bbox.
 * ========================================================================= */
(function setupRegionEditors() {
  const editors = document.querySelectorAll("[data-region-editor]");
  if (!editors.length) return;
  const mqMobile = window.matchMedia("(max-width: 719px)");

  // One shared popover + backdrop for the whole page.
  const backdrop = document.createElement("div");
  backdrop.className = "region-popover-backdrop";
  const pop = document.createElement("div");
  pop.className = "region-popover";
  pop.setAttribute("role", "dialog");
  pop.setAttribute("aria-modal", "false");
  pop.innerHTML =
    '<div class="region-popover-head"><span class="region-popover-title"></span>' +
    '<button type="button" class="region-popover-close" aria-label="Close">✕</button></div>' +
    '<img class="region-popover-crop" alt="">' +
    '<textarea rows="2"></textarea>' +
    '<p class="region-popover-suggest" hidden></p>' +
    '<div class="region-popover-actions">' +
    '<button type="button" class="region-nav region-prev" aria-label="Previous">‹</button>' +
    '<button type="button" class="region-accept"></button>' +
    '<button type="button" class="region-nav region-next" aria-label="Next">›</button>' +
    '<button type="button" class="region-done"></button></div>';
  document.body.append(backdrop, pop);
  const popTitle = pop.querySelector(".region-popover-title");
  const popCrop = pop.querySelector(".region-popover-crop");
  const popInput = pop.querySelector("textarea");
  const popSuggest = pop.querySelector(".region-popover-suggest");
  const btnClose = pop.querySelector(".region-popover-close");
  const btnPrev = pop.querySelector(".region-prev");
  const btnNext = pop.querySelector(".region-next");
  const btnAccept = pop.querySelector(".region-accept");
  const btnDone = pop.querySelector(".region-done");

  let active = null;      // { editor, regions, index }

  const t = (editor, key, fallback) => editor.dataset["i18n" + key] || fallback;

  function collect(editor) {
    return [...editor.querySelectorAll("[data-region-field]")].map((field, index) => {
      const key = field.id;
      const card = editor.querySelector('[data-region-card="' + key + '"]');
      const box = editor.querySelector('[data-region-box="' + key + '"]');
      const suggestBtn = card && card.querySelector("[data-apply-suggestion]");
      return {
        index, key, field, card, box,
        original: (field.dataset.regionOriginal || "").trim(),
        crop: (card && card.querySelector(".region-crop")?.getAttribute("src")) || "",
        suggestion: suggestBtn ? suggestBtn.dataset.suggestion || "" : "",
        accepted: false,
      };
    });
  }

  function isResolved(region) {
    return region.accepted || region.field.value.trim() !== region.original;
  }

  function refresh(editor, regions) {
    let done = 0;
    regions.forEach(region => {
      const resolved = isResolved(region);
      if (resolved) done += 1;
      region.box && region.box.classList.toggle("resolved", resolved);
      region.card && region.card.classList.toggle("resolved", resolved);
    });
    const counter = editor.querySelector("[data-region-count]");
    if (counter) counter.textContent = String(done);
    const progress = editor.querySelector(".region-progress");
    if (progress) progress.classList.toggle("complete", done === regions.length && regions.length > 0);
  }

  function markActive(regions, index) {
    regions.forEach((region, i) => {
      const on = i === index;
      region.box && region.box.classList.toggle("active", on);
      region.card && region.card.classList.toggle("active", on);
    });
  }

  function positionPopover(region) {
    pop.classList.remove("as-sheet");
    pop.style.top = pop.style.left = "";
    if (mqMobile.matches || !region.box) {
      pop.classList.add("as-sheet");
      backdrop.classList.add("open");
      return;
    }
    backdrop.classList.remove("open");
    const rect = region.box.getBoundingClientRect();
    const pr = pop.getBoundingClientRect();
    const margin = 8, vw = window.innerWidth, vh = window.innerHeight;
    let left = Math.min(Math.max(margin, rect.left), vw - pr.width - margin);
    let top = rect.bottom + margin;
    if (top + pr.height > vh - margin) top = Math.max(margin, rect.top - pr.height - margin);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  function open(editor, regions, index) {
    const region = regions[index];
    if (!region) return;
    active = { editor, regions, index };
    popTitle.textContent = t(editor, "Region", "Region") + " " + (index + 1);
    if (region.crop) { popCrop.src = region.crop; popCrop.hidden = false; } else { popCrop.hidden = true; }
    popInput.value = region.field.value;
    popInput.placeholder = t(editor, "Type", "Type what it really says…");
    if (region.suggestion) {
      popSuggest.hidden = false;
      popSuggest.textContent = "💡 " + region.suggestion;
      popSuggest.style.cursor = "pointer";
    } else { popSuggest.hidden = true; }
    btnAccept.textContent = t(editor, "Correct", "Looks correct");
    btnDone.textContent = t(editor, "Done", "Done");
    btnPrev.disabled = regions.length < 2;
    btnNext.disabled = regions.length < 2;
    pop.classList.add("open");
    markActive(regions, index);
    positionPopover(region);            // measured after .open so height is known
    positionPopover(region);            // second pass now that width/height are final
    popInput.focus();
    popInput.setSelectionRange(popInput.value.length, popInput.value.length);
  }

  function close() {
    pop.classList.remove("open", "as-sheet");
    backdrop.classList.remove("open");
    if (active) markActive(active.regions, -1);
    active = null;
  }

  popInput.addEventListener("input", () => {
    if (!active) return;
    const region = active.regions[active.index];
    region.field.value = popInput.value;
    region.accepted = false;
    refresh(active.editor, active.regions);
  });
  popSuggest.addEventListener("click", () => {
    if (!active || popSuggest.hidden) return;
    const region = active.regions[active.index];
    popInput.value = region.suggestion;
    popInput.dispatchEvent(new Event("input", { bubbles: true }));
    popInput.focus();
  });
  btnAccept.addEventListener("click", () => {
    if (!active) return;
    active.regions[active.index].accepted = true;
    refresh(active.editor, active.regions);
    step(1, true);
  });
  btnPrev.addEventListener("click", () => step(-1, false));
  btnNext.addEventListener("click", () => step(1, false));
  btnDone.addEventListener("click", close);
  btnClose.addEventListener("click", close);
  backdrop.addEventListener("click", close);

  function step(delta, closeIfWrap) {
    if (!active) return;
    const { editor, regions, index } = active;
    const next = index + delta;
    if (next < 0 || next >= regions.length) { if (closeIfWrap) close(); return; }
    open(editor, regions, next);
  }

  document.addEventListener("keydown", event => {
    if (!active || !pop.classList.contains("open")) return;
    if (event.key === "Escape") { event.preventDefault(); close(); }
    else if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); step(1, true); }
  });
  // Desktop click-away (mobile uses the backdrop).
  document.addEventListener("mousedown", event => {
    if (!active || mqMobile.matches) return;
    if (pop.contains(event.target) || event.target.closest("[data-region-box]")) return;
    close();
  });
  window.addEventListener("resize", () => { if (active) positionPopover(active.regions[active.index]); });

  // Wire each editor's boxes and fields.
  editors.forEach(editor => {
    const regions = collect(editor);
    editor.addEventListener("click", event => {
      const boxEl = event.target.closest("[data-region-box]");
      if (!boxEl) return;
      const idx = regions.findIndex(r => r.box === boxEl);
      if (idx >= 0) open(editor, regions, idx);
    });
    regions.forEach(region => {
      region.field.addEventListener("input", () => { region.accepted = false; refresh(editor, regions); });
      region.field.addEventListener("focus", () => markActive(regions, region.index));
    });
    refresh(editor, regions);
  });
})();

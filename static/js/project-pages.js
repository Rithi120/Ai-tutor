/* A saved project's pages (templates/project_workspace.html, #materials).
 *
 * The gallery: tap a thumbnail for the page full screen (swipe or arrows for the next),
 * move a page earlier or later (buttons, or drag on a desktop; saved at once through
 * POST /projects/<id>/pages/reorder), retake a photo (the existing replace form, sent as
 * soon as a photo is chosen) and delete a page while no topic refers to it yet (the
 * existing delete form). "Add more pages" uses the same multi-image collection as the
 * create form, in batches to /pages/add, then opens the start page that reads them.
 * Without this module the forms still work on their own.
 */
import { t } from "./i18n.js";
import {
  createImageCollection, createImageViewer, materialsForm, uploadCollection, xhrUpload,
} from "./image-collection.js";

const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

setUpGallery();
setUpAddPages();

function setUpGallery() {
  const wrap = document.querySelector("[data-project-pages]");
  const gallery = document.querySelector("#projectPageGallery");
  if (!wrap || !gallery) return;
  const status = document.querySelector("#projectPagesStatus");
  const reorderUrl = wrap.dataset.reorderUrl;
  const rows = () => [...gallery.querySelectorAll("li[data-page-id]")];
  let saving = false;
  let dragged = null;

  const viewer = createImageViewer({
    count: () => rows().length,
    describe: index => {
      const row = rows()[index];
      return { url: row.dataset.full, revoke: false, title: row.dataset.title || "", alt: row.dataset.title || "" };
    },
    onClose: index => rows()[index]?.querySelector(".imgcol-thumb")?.focus(),
  });

  function renumber() {
    rows().forEach((row, index) => {
      const number = index + 1;
      row.querySelector(".imgcol-number").textContent = String(number);
      const earlier = row.querySelector("[data-move='-1']");
      const later = row.querySelector("[data-move='1']");
      earlier.hidden = false;
      later.hidden = false;
      earlier.disabled = saving || index === 0;
      later.disabled = saving || index === rows().length - 1;
      earlier.setAttribute("aria-label", t("imgcolMoveEarlier", { number }));
      later.setAttribute("aria-label", t("imgcolMoveLater", { number }));
      row.querySelector(".imgcol-thumb")?.setAttribute("aria-label", t("imgcolOpen", { number }));
      row.draggable = !saving;
    });
  }

  async function saveOrder(previous) {
    saving = true;
    renumber();
    try {
      const response = await fetch(reorderUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", "X-CSRFToken": csrfToken },
        body: JSON.stringify({ page_ids: rows().map(row => Number(row.dataset.pageId)) }),
      });
      if (!response.ok) throw new Error(t("pmPagesOrderFailed"));
      status.textContent = t("pmPagesOrderSaved");
    } catch (error) {
      // Put the pages back where the server still has them.
      previous.forEach(row => gallery.appendChild(row));
      status.textContent = error.message || t("pmPagesOrderFailed");
    } finally {
      saving = false;
      renumber();
    }
  }

  function move(row, target) {
    const list = rows();
    const from = list.indexOf(row);
    if (from < 0 || target < 0 || target >= list.length || target === from || saving) return;
    const previous = [...list];
    const anchor = list[target];
    gallery.insertBefore(row, target > from ? anchor.nextSibling : anchor);
    renumber();
    saveOrder(previous);
  }

  gallery.addEventListener("click", event => {
    const row = event.target.closest("li[data-page-id]");
    if (!row) return;
    const thumb = event.target.closest(".imgcol-thumb");
    if (thumb) {
      event.preventDefault();
      viewer.open(rows().indexOf(row));
      return;
    }
    const mover = event.target.closest("[data-move]");
    if (mover) {
      move(row, rows().indexOf(row) + Number(mover.dataset.move));
      (mover.disabled ? row.querySelector(".imgcol-thumb") : mover)?.focus();
    }
  });
  gallery.addEventListener("submit", event => {
    if (event.target.matches("[data-delete-page]") && !window.confirm(t("pmDeletePageConfirm"))) event.preventDefault();
  });
  gallery.addEventListener("change", event => {
    // A retake is sent as soon as the photo is chosen: no second button to find.
    const form = event.target.closest("form[data-retake]");
    if (form && event.target.files?.length) form.requestSubmit ? form.requestSubmit() : form.submit();
  });
  gallery.addEventListener("dragstart", event => {
    dragged = event.target.closest?.("li[data-page-id]") || null;
    if (!dragged || saving) { event.preventDefault(); return; }
    dragged.classList.add("is-dragging");
    event.dataTransfer?.setData("text/plain", dragged.dataset.pageId);
  });
  gallery.addEventListener("dragend", () => {
    dragged?.classList.remove("is-dragging");
    dragged = null;
    rows().forEach(row => row.classList.remove("is-drop-target"));
  });
  gallery.addEventListener("dragover", event => {
    const row = event.target.closest("li[data-page-id]");
    if (!dragged || !row || row === dragged) return;
    event.preventDefault();
    rows().forEach(other => other.classList.toggle("is-drop-target", other === row));
  });
  gallery.addEventListener("drop", event => {
    const row = event.target.closest("li[data-page-id]");
    if (!dragged || !row) return;
    event.preventDefault();
    row.classList.remove("is-drop-target");
    move(dragged, rows().indexOf(row));
  });
  renumber();
}

function setUpAddPages() {
  const form = document.querySelector("#addPagesForm");
  const mount = document.querySelector("#addPagesImages");
  const input = document.querySelector("#addPagesInput");
  if (!form || !mount || !input) return;
  const camera = document.querySelector("#addPagesCamera");
  const status = document.querySelector("#addPagesStatus");
  const submitButton = form.querySelector("button[type=submit]");
  // The gallery holds the files now: the input is emptied after every pick.
  input.required = false;
  document.querySelector("[data-camera-button]")?.removeAttribute("hidden");
  const max = Number(form.dataset.max);
  const collection = createImageCollection({
    root: mount, inputs: [input], cameraInputs: camera ? [camera] : [], dropZone: form,
    max: Number.isFinite(max) ? max : 40, maxFileBytes: Number(form.dataset.maxBytes) || 0,
    accept: input.getAttribute("accept") || "", labels: true,
    onError: message => { status.textContent = message; },
  });
  window.__addPages = collection;
  let startUrl = "";

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!collection.count()) {
      status.textContent = t("imgcolChooseFirst");
      return;
    }
    if (collection.busy()) return;
    submitButton.disabled = true;
    const outcome = await uploadCollection(collection, {
      send: async (entries, { onProgress }) => {
        const { data } = await xhrUpload(form.action, materialsForm(entries, new FormData(form)), { onProgress });
        startUrl = data.start_url || startUrl;
        return data;
      },
      onStep: ({ from, to, total }) => {
        status.textContent = from === to ? t("imgcolUploadingOne", { number: from, total })
          : t("imgcolUploadingRange", { from, to, total });
      },
    });
    submitButton.disabled = false;
    if (outcome.ok && startUrl) {
      status.textContent = t("imgcolAllSaved");
      window.location.assign(startUrl);
      return;
    }
    status.textContent = [outcome.error?.message || "", t("imgcolFailedSummary", { count: outcome.failed })]
      .filter(Boolean).join(" ");
  });
}

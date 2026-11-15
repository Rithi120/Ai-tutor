/* A collection of up to forty photos, PDFs and camera scans: see them, order them, fix
 * them, then send them in batches.
 *
 * Used by Project Mode (create a project, add pages) and Vocabulary Mode (a multi-page
 * import). Built on the chat tray's primitives in image-attach.js (normalizeImage,
 * makeThumbnail, xhrUpload), not beside them. What it does:
 *   - picks accumulate: a second pick from the gallery adds to the first instead of
 *     replacing it (a native file input replaces its FileList on every pick);
 *   - gallery photos (an input with `multiple`, no `capture`, so a phone offers its photo
 *     library and files) and camera photos (a separate input with `capture`) share one list;
 *   - every item shows a thumbnail made one or two at a time, decoded at thumbnail size
 *     (forty full-size decodes at once froze a phone), and every object URL is revoked when
 *     its item goes;
 *   - delete, move earlier/later (buttons for touch and keyboard, drag on a desktop),
 *     replace, an optional label, a full-screen preview (arrows, Escape, swipe);
 *   - per-item status and progress, and a retry of the items that failed.
 * The pure rules live in image-collection-rules.js. Nothing here is trusted by the server,
 * which checks every byte again.
 */
import { t } from "./i18n.js";
import { makeThumbnail, normalizeImage, xhrUpload } from "./image-attach.js";
import {
  DEFAULT_BATCH, DEFAULT_COMPRESS, DEFAULT_MAX_ITEMS, applyResults, batchHasRoom, cleanLabel, clampCount, isImage,
  isPdf, metaFor, moveItem, pendingItems, statusCounts, stepIndex, swipeAction, validateFile,
} from "./image-collection-rules.js";

export { xhrUpload, metaFor };

const THUMB_EDGE = 320;
const THUMB_CONCURRENCY = 2;

function fileIdentity(file) {
  return file && file.name ? `${file.name}|${file.size}|${file.lastModified || 0}` : "";
}

function button(className, symbol, label, handler) {
  const control = document.createElement("button");
  control.type = "button";
  control.className = className;
  control.textContent = symbol;
  control.title = label;
  control.setAttribute("aria-label", label);
  if (handler) control.addEventListener("click", handler);
  return control;
}

/* createImageCollection({root, inputs, cameraInputs, dropZone, ...}) -> controller.
 *   root          element the gallery is drawn into
 *   inputs        gallery file inputs (kind "upload")
 *   cameraInputs  camera file inputs (kind "camera": never re-encoded)
 *   dropZone      element that takes dropped files (desktop)
 *   max           items at most (40)
 *   maxFileBytes  per file, before re-encoding (0 = no client check)
 *   accept        accept list, as on the inputs
 *   compress      normalizeImage options for gallery photos, or false
 *   labels        show an optional label box per item
 *   noun          "page" wording is the default; the strings are the imgcol* keys
 *   onChange(controller), onError(message)
 */
export function createImageCollection({
  root, inputs = [], cameraInputs = [], dropZone = null, max = DEFAULT_MAX_ITEMS, maxFileBytes = 0,
  accept = "", compress = DEFAULT_COMPRESS, labels = false, onChange, onError,
} = {}) {
  const items = [];
  const nodes = new Map();
  const thumbQueue = [];
  let thumbsRunning = 0;
  let counter = 0;
  let busy = false;
  let orderFrozen = false;
  let destroyed = false;
  let started = false;   // no onChange while the controller is still being built
  let replaceKey = null;
  let dragKey = null;

  const host = document.createElement("div");
  host.className = "imgcol";
  const summary = document.createElement("p");
  summary.className = "imgcol-summary";
  const grid = document.createElement("ol");
  grid.className = "imgcol-grid";
  grid.setAttribute("aria-label", t("imgcolListLabel"));
  const announcer = document.createElement("p");
  announcer.className = "imgcol-announce";
  announcer.setAttribute("role", "status");
  announcer.setAttribute("aria-live", "polite");
  host.append(summary, grid, announcer);
  root.replaceChildren(host);

  // One hidden picker for "Replace": the item keeps its place, only its file changes.
  const replaceInput = document.createElement("input");
  replaceInput.type = "file";
  replaceInput.accept = accept || "image/*";
  replaceInput.className = "imgcol-replace-input";
  replaceInput.tabIndex = -1;
  replaceInput.setAttribute("aria-hidden", "true");
  host.appendChild(replaceInput);

  const viewer = createImageViewer({
    count: () => items.length,
    describe: index => {
      const item = items[index];
      const title = t("imgcolPreviewTitle", { number: index + 1, total: items.length }) + (item.label ? ` · ${item.label}` : "");
      // The full picture only while it is on screen; its URL goes when the preview does.
      if (isImage(item.file)) {
        return { url: URL.createObjectURL(item.file), revoke: true, title, alt: t("imgcolPage", { number: index + 1 }) };
      }
      return { title, note: t("imgcolPdfNote", { name: item.name }) };
    },
    onClose: index => {
      const item = items[index];
      if (item) nodes.get(item.key)?.querySelector(".imgcol-thumb")?.focus();
    },
  });

  function say(message) {
    announcer.textContent = "";
    // A fresh text node each time, so a screen reader repeats the same sentence too.
    window.requestAnimationFrame?.(() => { announcer.textContent = message; });
    if (!window.requestAnimationFrame) announcer.textContent = message;
  }

  function warn(message) {
    if (message) onError?.(message);
  }

  function indexOf(key) {
    return items.findIndex(item => item.key === key);
  }

  function release(item) {
    if (item.thumbUrl) URL.revokeObjectURL(item.thumbUrl);
    item.thumbUrl = "";
    item.prepared = null;
  }

  // ---- thumbnails: one or two at a time, never forty decodes at once ----------------
  function queueThumbnail(item) {
    if (!isImage(item.file)) return;
    thumbQueue.push(item);
    pumpThumbnails();
  }

  function pumpThumbnails() {
    while (!destroyed && thumbsRunning < THUMB_CONCURRENCY && thumbQueue.length) {
      const item = thumbQueue.shift();
      if (indexOf(item.key) < 0) continue;
      const file = item.file;
      thumbsRunning += 1;
      makeThumbnail(file, THUMB_EDGE).then(blob => {
        // Removed or replaced while this was being made: throw the result away.
        if (!blob || destroyed || indexOf(item.key) < 0 || item.file !== file) return;
        if (item.thumbUrl) URL.revokeObjectURL(item.thumbUrl);
        item.thumbUrl = URL.createObjectURL(blob);
        item.thumbFailed = false;
        updateNode(item);
      }, () => {}).finally(() => {
        if (indexOf(item.key) >= 0 && item.file === file && !item.thumbUrl) {
          item.thumbFailed = true;
          updateNode(item);
        }
        thumbsRunning -= 1;
        // Let the page breathe between decodes: input and scrolling come first.
        window.setTimeout(pumpThumbnails, 0);
      });
    }
  }

  // ---- drawing -------------------------------------------------------------------
  function statusText(item) {
    if (item.status === "uploading") {
      return item.progress == null ? t("imgUploading") : t("imgUploadingPercent", { percent: Math.round(item.progress * 100) });
    }
    if (item.status === "failed") return item.error || t("imgcolNotSaved");
    if (item.status === "done") return item.duplicate ? t("imgcolAlreadySaved") : t("imgcolSaved");
    return "";
  }

  function makeNode(item) {
    const node = document.createElement("li");
    node.className = "imgcol-item";
    node.dataset.key = item.key;
    const open = button("imgcol-thumb", "", "", () => openViewer(indexOf(item.key)));
    const image = document.createElement("img");
    image.alt = "";
    image.decoding = "async";
    image.hidden = true;
    const tile = document.createElement("span");
    tile.className = "imgcol-tile";
    tile.setAttribute("aria-hidden", "true");
    open.append(image, tile);
    const number = document.createElement("span");
    number.className = "imgcol-number";
    number.setAttribute("aria-hidden", "true");
    const kind = document.createElement("span");
    kind.className = "imgcol-kind";
    kind.setAttribute("aria-hidden", "true");
    const remove = button("imgcol-remove", "×", "", () => removeItem(item.key));
    const bar = document.createElement("span");
    bar.className = "imgcol-bar";
    bar.appendChild(document.createElement("i"));
    const status = document.createElement("span");
    status.className = "imgcol-status";
    const tools = document.createElement("div");
    tools.className = "imgcol-tools";
    tools.append(
      button("imgcol-earlier", "←", "", () => moveBy(item.key, -1)),
      button("imgcol-later", "→", "", () => moveBy(item.key, 1)),
      button("imgcol-replace", "↻", "", () => startReplace(item.key)),
    );
    node.append(open, number, kind, remove, bar, status, tools);
    if (labels) {
      const label = document.createElement("input");
      label.className = "imgcol-label";
      label.type = "text";
      label.maxLength = 80;
      label.autocomplete = "off";
      label.placeholder = t("imgcolLabel");
      label.addEventListener("input", () => { item.label = cleanLabel(label.value); });
      node.appendChild(label);
    }
    // Drag to reorder on a desktop; touch and keyboard use the arrow buttons.
    node.addEventListener("dragstart", event => {
      if (!canMove(item)) { event.preventDefault(); return; }
      dragKey = item.key;
      node.classList.add("is-dragging");
      event.dataTransfer?.setData("text/plain", item.key);
      if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
    });
    node.addEventListener("dragend", () => {
      dragKey = null;
      node.classList.remove("is-dragging");
      grid.querySelectorAll(".is-drop-target").forEach(other => other.classList.remove("is-drop-target"));
    });
    node.addEventListener("dragover", event => {
      if (!dragKey || dragKey === item.key) return;
      event.preventDefault();
      node.classList.add("is-drop-target");
    });
    node.addEventListener("dragleave", () => node.classList.remove("is-drop-target"));
    node.addEventListener("drop", event => {
      if (!dragKey) return;
      event.preventDefault();
      event.stopPropagation();
      node.classList.remove("is-drop-target");
      const from = dragKey;
      dragKey = null;
      moveTo(from, indexOf(item.key));
    });
    nodes.set(item.key, node);
    return node;
  }

  function canMove(item) {
    return !busy && !orderFrozen && item.status !== "done" && items.length > 1;
  }

  function updateNode(item) {
    const node = nodes.get(item.key);
    if (!node) return;
    const index = indexOf(item.key);
    const number = index + 1;
    node.className = `imgcol-item is-${item.status}${item.kind === "camera" ? " is-camera" : ""}`;
    node.draggable = canMove(item);
    const image = node.querySelector("img");
    const tile = node.querySelector(".imgcol-tile");
    if (item.thumbUrl) {
      if (image.getAttribute("src") !== item.thumbUrl) image.src = item.thumbUrl;
      image.hidden = false;
      tile.hidden = true;
    } else {
      image.hidden = true;
      image.removeAttribute("src");
      tile.hidden = false;
      tile.textContent = isPdf(item.file) ? "PDF" : (item.thumbFailed ? "🖼" : "");
      tile.classList.toggle("is-loading", !isPdf(item.file) && !item.thumbFailed);
    }
    const open = node.querySelector(".imgcol-thumb");
    const name = item.label || item.name;
    open.setAttribute("aria-label", t("imgcolOpen", { number }) + (name ? ` – ${name}` : ""));
    open.title = name || t("imgcolPage", { number });
    node.querySelector(".imgcol-number").textContent = String(number);
    node.querySelector(".imgcol-kind").textContent = item.kind === "camera" ? "📷" : (isPdf(item.file) ? "PDF" : "");
    const remove = node.querySelector(".imgcol-remove");
    remove.setAttribute("aria-label", t("imgcolRemove", { number }));
    remove.title = t("imgcolRemove", { number });
    remove.disabled = busy || item.status === "done";
    const earlier = node.querySelector(".imgcol-earlier");
    const later = node.querySelector(".imgcol-later");
    const replace = node.querySelector(".imgcol-replace");
    earlier.setAttribute("aria-label", t("imgcolMoveEarlier", { number }));
    earlier.title = earlier.getAttribute("aria-label");
    later.setAttribute("aria-label", t("imgcolMoveLater", { number }));
    later.title = later.getAttribute("aria-label");
    replace.setAttribute("aria-label", t("imgcolReplace", { number }));
    replace.title = replace.getAttribute("aria-label");
    earlier.disabled = !canMove(item) || index === 0 || items[index - 1]?.status === "done";
    later.disabled = !canMove(item) || index === items.length - 1;
    replace.disabled = busy || item.status === "done";
    const bar = node.querySelector(".imgcol-bar");
    bar.hidden = item.status !== "uploading";
    bar.classList.toggle("is-indeterminate", item.progress == null);
    if (item.progress != null) bar.style.setProperty("--progress", `${Math.round(item.progress * 100)}%`);
    const status = node.querySelector(".imgcol-status");
    status.textContent = statusText(item);
    status.setAttribute("role", item.status === "failed" ? "alert" : "status");
    const label = node.querySelector(".imgcol-label");
    if (label) {
      label.setAttribute("aria-label", t("imgcolLabelFor", { number }));
      if (document.activeElement !== label && label.value !== item.label) label.value = item.label;
      label.disabled = busy || item.status === "done";
    }
  }

  function render() {
    // Moves the existing nodes into order (no rebuild: focus and decoded images stay).
    items.forEach((item, index) => {
      const node = nodes.get(item.key) || makeNode(item);
      if (grid.children[index] !== node) grid.insertBefore(node, grid.children[index] || null);
    });
    while (grid.children.length > items.length) grid.lastElementChild.remove();
    items.forEach(updateNode);
    const counts = statusCounts(items);
    summary.textContent = items.length ? t("imgcolCount", { count: items.length, max }) : t("imgcolEmpty");
    host.classList.toggle("is-empty", !items.length);
    host.classList.toggle("is-busy", busy);
    host.dataset.count = String(items.length);
    host.dataset.failed = String(counts.failed);
    for (const input of [...inputs, ...cameraInputs]) if (input) input.disabled = busy || items.length >= max;
    if (started) onChange?.(controller);
  }

  // ---- changes ------------------------------------------------------------------
  function add(files, { kind = "upload", transform = {}, label = "" } = {}) {
    const list = [...(files || [])];
    const { accepted } = clampCount(items.length, list.length, max);
    if (accepted < list.length) warn(t("imgcolLimit", { count: max }));
    const added = [];
    for (const file of list.slice(0, accepted)) {
      const problem = validateFile(file, {
        accept: kind === "camera" ? "" : accept, maxBytes: maxFileBytes,
        compressible: Boolean(compress) && kind !== "camera",
      });
      if (problem === "not_supported") { warn(t("imgcolNotSupported", { name: file?.name || "" })); continue; }
      if (problem === "empty") { warn(t("imgcolEmptyFile", { name: file?.name || "" })); continue; }
      if (problem === "too_large") {
        warn(t("imgcolTooLarge", { name: file.name, size: Math.round(maxFileBytes / (1024 * 1024)) }));
        continue;
      }
      const identity = fileIdentity(file);
      if (identity && items.some(item => fileIdentity(item.file) === identity)) {
        warn(t("imgcolDuplicate", { name: file.name }));
        continue;
      }
      counter += 1;
      const item = {
        key: `i${counter}`, file, name: String(file.name || ""), kind: kind === "camera" ? "camera" : "upload",
        transform: { ...(transform || {}) }, label: cleanLabel(label), status: "ready", progress: null, error: "",
        duplicate: false, thumbUrl: "", thumbFailed: false, prepared: null, result: null,
      };
      items.push(item);
      added.push(item);
    }
    render();
    added.forEach(queueThumbnail);
    if (added.length) say(t("imgcolAdded", { count: added.length, total: items.length }));
    return added.map(item => item.key);
  }

  function removeItem(key) {
    const index = indexOf(key);
    if (index < 0 || busy || items[index].status === "done") return;
    const [item] = items.splice(index, 1);
    release(item);
    nodes.get(key)?.remove();
    nodes.delete(key);
    if (viewer.isOpen()) closeViewer();
    render();
    say(t("imgcolRemoved"));
    const next = grid.children[Math.min(index, grid.children.length - 1)];
    next?.querySelector(".imgcol-thumb")?.focus();
  }

  function moveTo(key, target) {
    const from = indexOf(key);
    if (from < 0 || target < 0 || !canMove(items[from])) return;
    // Saved pages keep their place: nothing moves in front of them.
    const firstFree = items.findIndex(item => item.status !== "done");
    const to = Math.max(target, firstFree < 0 ? 0 : firstFree);
    const reordered = moveItem(items, from, to);
    items.splice(0, items.length, ...reordered);
    render();
    say(t("imgcolMoved", { number: indexOf(key) + 1 }));
  }

  function moveBy(key, step) {
    const from = indexOf(key);
    moveTo(key, from + step);
    // Keep the keyboard on the item that moved.
    const node = nodes.get(key);
    const control = node?.querySelector(step < 0 ? ".imgcol-earlier" : ".imgcol-later");
    (control && !control.disabled ? control : node?.querySelector(".imgcol-thumb"))?.focus();
  }

  function startReplace(key) {
    if (busy) return;
    replaceKey = key;
    replaceInput.value = "";
    replaceInput.click();
  }

  function replace(key, file) {
    const item = items[indexOf(key)];
    if (!item || !file || busy || item.status === "done") return false;
    const problem = validateFile(file, { accept, maxBytes: maxFileBytes, compressible: Boolean(compress) });
    if (problem) {
      warn(problem === "too_large"
        ? t("imgcolTooLarge", { name: file.name, size: Math.round(maxFileBytes / (1024 * 1024)) })
        : t("imgcolNotSupported", { name: file.name || "" }));
      return false;
    }
    release(item);
    Object.assign(item, {
      file, name: String(file.name || ""), kind: "upload", transform: {}, status: "ready", error: "", progress: null,
      thumbFailed: false, duplicate: false,
    });
    render();
    queueThumbnail(item);
    say(t("imgcolReplaced", { number: indexOf(key) + 1 }));
    return true;
  }

  replaceInput.addEventListener("change", () => {
    const file = replaceInput.files?.[0];
    const key = replaceKey;
    replaceKey = null;
    replaceInput.value = "";
    if (file && key) replace(key, file);
  });

  for (const input of inputs) {
    input?.addEventListener("change", event => {
      add(event.target.files, { kind: "upload" });
      event.target.value = "";   // the next pick adds to this one; the same file can come back
    });
  }
  for (const input of cameraInputs) {
    input?.addEventListener("change", event => {
      add(event.target.files, { kind: "camera" });
      event.target.value = "";
    });
  }

  if (dropZone) {
    const isFileDrag = event => [...(event.dataTransfer?.types || [])].includes("Files");
    for (const type of ["dragenter", "dragover"]) {
      dropZone.addEventListener(type, event => {
        if (!isFileDrag(event) || busy) return;
        event.preventDefault();
        dropZone.classList.add("is-dragover");
      });
    }
    dropZone.addEventListener("dragleave", event => {
      if (!dropZone.contains(event.relatedTarget)) dropZone.classList.remove("is-dragover");
    });
    dropZone.addEventListener("drop", event => {
      dropZone.classList.remove("is-dragover");
      if (!isFileDrag(event) || busy) return;
      event.preventDefault();
      add(event.dataTransfer.files, { kind: "upload" });
    });
  }

  // ---- full-screen preview -----------------------------------------------------------
  function openViewer(index) {
    viewer.open(index);
  }

  function closeViewer() {
    viewer.close();
  }

  // ---- upload support -----------------------------------------------------------------
  async function prepare(key) {
    const item = items[indexOf(key)];
    if (!item) return null;
    if (item.prepared && item.prepared.source === item.file) return item.prepared.file;
    let file = item.file;
    // Camera scans stay lossless; gallery photos past ~4 MB / 4096 px become 2560 px JPEG.
    if (compress && item.kind !== "camera" && isImage(file)) file = await normalizeImage(file, compress);
    item.prepared = { source: item.file, file };
    return file;
  }

  function setStatus(key, status, { error = "", progress = null, duplicate = false, result = null } = {}) {
    const item = items[indexOf(key)];
    if (!item) return;
    item.status = status;
    item.error = error;
    item.progress = progress;
    item.duplicate = duplicate;
    if (result) item.result = result;
    updateNode(item);
    const counts = statusCounts(items);
    host.dataset.failed = String(counts.failed);
    if (started) onChange?.(controller);
  }

  function setProgress(key, progress) {
    const item = items[indexOf(key)];
    if (!item || item.status !== "uploading") return;
    item.progress = progress;
    updateNode(item);
  }

  const controller = {
    element: host,
    items: () => items.map(item => ({ ...item })),
    keys: () => items.map(item => item.key),
    count: () => items.length,
    max: () => max,
    busy: () => busy,
    failed: () => items.filter(item => item.status === "failed").length,
    pending: () => pendingItems(items).length,
    add,
    remove: removeItem,
    move: (key, index) => moveTo(key, index),
    replace,
    setLabel(key, value) {
      const item = items[indexOf(key)];
      if (!item) return;
      item.label = cleanLabel(value);
      updateNode(item);
    },
    setStatus,
    setProgress,
    setBusy(value) {
      busy = Boolean(value);
      render();
    },
    freezeOrder() {
      orderFrozen = true;
      items.forEach(updateNode);
    },
    prepare,
    openPreview: index => openViewer(index),
    closePreview: closeViewer,
    previewOpen: () => viewer.isOpen(),
    clear() {
      items.splice(0).forEach(release);
      nodes.forEach(node => node.remove());
      nodes.clear();
      closeViewer();
      render();
    },
    destroy() {
      destroyed = true;
      controller.clear();
      viewer.destroy();
      host.remove();
    },
  };
  render();
  started = true;
  return controller;
}

/* A full-screen preview of a list of pictures: arrows and Escape on a keyboard, a swipe
 * (left/right for the next/previous, down to close) on a phone, buttons for both.
 *   count()          how many pictures there are now
 *   describe(index)  {url, revoke, title, alt, note}: revoke=true for an object URL, which
 *                    is revoked as soon as another picture is shown or the preview closes
 *   onClose(index)   after closing (to give the focus back)
 * Also used by the saved project's page gallery (project-pages.js), with server URLs. */
export function createImageViewer({ count, describe, onClose } = {}) {
  let index = -1;
  let current = null;
  const dialog = document.createElement("dialog");
  dialog.className = "imgcol-viewer";
  dialog.setAttribute("aria-label", t("imgcolPreview"));
  const bar = document.createElement("div");
  bar.className = "imgcol-viewer-bar";
  const title = document.createElement("p");
  title.className = "imgcol-viewer-title";
  const close = button("imgcol-viewer-close", "×", t("imgcolClose"), () => hide());
  bar.append(title, close);
  const stage = document.createElement("div");
  stage.className = "imgcol-viewer-stage";
  const image = document.createElement("img");
  image.alt = "";
  image.decoding = "async";
  const note = document.createElement("p");
  note.className = "imgcol-viewer-note";
  stage.append(image, note);
  const nav = document.createElement("div");
  nav.className = "imgcol-viewer-nav";
  const step = by => show(stepIndex(index, by, count()));
  nav.append(
    button("imgcol-viewer-prev", "‹", t("imgcolPrevious"), () => step(-1)),
    button("imgcol-viewer-next", "›", t("imgcolNext"), () => step(1)),
  );
  dialog.append(bar, stage, nav);
  dialog.addEventListener("cancel", event => { event.preventDefault(); hide(); });
  dialog.addEventListener("close", () => { if (index >= 0) hide(); });
  dialog.addEventListener("keydown", event => {
    if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
    if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
    if (event.key === "Escape") { event.preventDefault(); hide(); }
  });
  let start = null;
  stage.addEventListener("pointerdown", event => { start = { x: event.clientX, y: event.clientY }; });
  stage.addEventListener("pointerup", event => {
    if (!start) return;
    const action = swipeAction(event.clientX - start.x, event.clientY - start.y);
    start = null;
    if (action === "next") step(1);
    if (action === "previous") step(-1);
    if (action === "close") hide();
  });
  stage.addEventListener("pointercancel", () => { start = null; });
  document.body.appendChild(dialog);

  function release() {
    if (current?.revoke && current.url) URL.revokeObjectURL(current.url);
    current = null;
  }

  function show(next) {
    if (next < 0 || next >= count()) return;
    release();
    index = next;
    current = describe(next) || {};
    title.textContent = current.title || "";
    nav.hidden = count() < 2;
    if (current.url) {
      image.src = current.url;
      image.alt = current.alt || "";
      image.hidden = false;
      note.textContent = current.note || "";
    } else {
      image.removeAttribute("src");
      image.hidden = true;
      note.textContent = current.note || "";
    }
  }

  function open(next) {
    show(next);
    if (index < 0) return;
    if (!dialog.open) {
      if (typeof dialog.showModal === "function") dialog.showModal();
      else dialog.setAttribute("open", "");
    }
    close.focus();
  }

  function hide() {
    const last = index;
    if (last < 0 && !dialog.open) return;
    index = -1;
    release();
    image.removeAttribute("src");
    if (dialog.open) {
      if (typeof dialog.close === "function") dialog.close();
      else dialog.removeAttribute("open");
    }
    if (last >= 0) onClose?.(last);
  }

  return {
    open, close: hide, show, element: dialog,
    isOpen: () => index >= 0,
    index: () => index,
    destroy() { hide(); dialog.remove(); },
  };
}

/* Send the collection's unsent items in batches, one batch after another.
 *   send(entries, {batch, total, onProgress}) -> Promise<{results: [{index, status, message}]}>
 *     entries = [{key, item, file}] (file = what to upload: re-encoded where that applies)
 *   batch: {maxFiles, maxBytes}
 *   onStep({done, total, from, to}) before each batch, for a "pages x-y of n" line.
 * A batch the server refuses as a whole (an error, a 413, no connection) marks its items
 * failed and stops: later items stay unsent, so a retry sends them in their order. Items
 * the server refused one by one are failed with the server's reason; the rest are done.
 * Resolves {ok, failed, error}. */
export async function uploadCollection(collection, { send, batch = DEFAULT_BATCH, onStep } = {}) {
  let queue = pendingItems(collection.items());
  const tried = new Set();
  let error = null;
  collection.setBusy(true);
  try {
    let batchNumber = 0;
    while (queue.length && !error) {
      queue.forEach(item => tried.add(item.key));
      const total = collection.count();
      let next = 0;
      while (next < queue.length) {
        const entries = [];
        let bytes = 0;
        while (next < queue.length) {
          const key = queue[next].key;
          if (!collection.keys().includes(key)) { next += 1; continue; }
          const file = await collection.prepare(key);
          if (!file) { next += 1; continue; }
          if (!batchHasRoom(entries.length, bytes, file.size, batch)) break;
          entries.push({ key, item: queue[next], file });
          bytes += file.size;
          next += 1;
        }
        if (!entries.length) break;
        batchNumber += 1;
        const keys = collection.keys();
        const positions = entries.map(entry => keys.indexOf(entry.key) + 1);
        onStep?.({ batch: batchNumber, from: Math.min(...positions), to: Math.max(...positions), total });
        entries.forEach(entry => collection.setStatus(entry.key, "uploading", { progress: 0 }));
        let answer;
        try {
          answer = await send(entries, {
            batch: batchNumber, total,
            onProgress: fraction => entries.forEach(entry => collection.setProgress(entry.key, fraction)),
          });
        } catch (caught) {
          error = caught;
          entries.forEach(entry => collection.setStatus(entry.key, "failed", { error: caught?.message || t("imgcolNotSaved") }));
          break;
        }
        const outcomes = applyResults(entries.map(entry => entry.item), answer?.results, t("imgcolNotSaved"));
        outcomes.forEach((outcome, index) => collection.setStatus(entries[index].key, outcome.status, {
          error: outcome.error, duplicate: outcome.duplicate, result: answer?.results?.[index] || null,
        }));
        collection.freezeOrder();
      }
      // A page added while this ran (the scanner handed one over mid-upload) is sent too,
      // so the caller never navigates away from a page that was never uploaded.
      queue = error ? [] : collection.items().filter(item => item.status === "ready" && !tried.has(item.key));
    }
  } finally {
    collection.setBusy(false);
  }
  const failed = collection.failed();
  return { ok: !error && failed === 0, failed, error };
}

/* A FormData of `entries` as `materials` files with their `materials_meta` (the order and
 * kind of each, app.uploads_from_request), on top of `base` (other form fields). */
export function materialsForm(entries, base = null) {
  const form = base || new FormData();
  form.delete("materials");
  form.delete("materials_meta");
  entries.forEach((entry, index) => {
    const fallback = entry.item.kind === "camera" ? `camera-page-${index + 1}.png` : `page-${index + 1}.jpg`;
    form.append("materials", entry.file, entry.file.name || entry.item.name || fallback);
  });
  form.append("materials_meta", JSON.stringify(entries.map(entry => metaFor(entry.item))));
  return form;
}

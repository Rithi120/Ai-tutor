/* Photo attachments: take a photo or choose one, see it, remove it, reorder it, retry it.
 *
 * Used by the assistant, the lesson chat and the "photo of my working" answer button.
 * The camera is the device's own (an <input capture>), not getUserMedia: it works in
 * every mobile browser, asks for permission the way the phone always does, and falls
 * back to the photo picker where there is no camera. Nothing here is trusted by the
 * server, which checks every byte again (learnova/vision/intake.py); this module only
 * makes the upload smaller and the experience better.
 */
import { t } from "./i18n.js";

const MAX_EDGE = 2560;
const JPEG_QUALITY = 0.9;
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

/* Re-encode on the device before uploading: upright (the camera's rotation tag applied),
 * at most 2560 px, JPEG - a 5 MB phone photo becomes well under 1 MB on mobile data, and
 * an iPhone HEIC photo becomes a JPEG wherever the browser can decode it. A small photo
 * already in a supported format is sent as it is. Any failure sends the original and
 * lets the server explain what is wrong with it. */
export async function normalizeImage(file, { maxEdge = MAX_EDGE, quality = JPEG_QUALITY, minBytes = 1_500_000,
  keepEdge = 0 } = {}) {
  if (typeof createImageBitmap !== "function") return file;
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch (_) {
    return file;
  }
  try {
    const longEdge = Math.max(bitmap.width, bitmap.height);
    const supported = /^image\/(jpeg|png|webp)$/.test(file.type);
    // keepEdge: a collection keeps a supported photo as it is until it passes both its size
    // and its edge budget (image-collection.js: ~4 MB or 4096 px), then sends maxEdge.
    if (supported && file.size < minBytes && longEdge <= (keepEdge || maxEdge)) return file;
    const scale = Math.min(1, maxEdge / longEdge);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";  // a transparent screenshot stays readable, not black
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", quality));
    if (!blob) return file;
    const name = `${String(file.name || "photo").replace(/\.[^.]+$/, "") || "photo"}.jpg`;
    return new File([blob], name, { type: "image/jpeg" });
  } catch (_) {
    return file;
  } finally {
    bitmap.close?.();
  }
}

/* Why an upload failed, in words the student can act on. The server's own JSON message
 * wins; a response that is not JSON (an expired session redirected to the login page, a
 * proxy's 413 or 502 page) is named by its status instead of "Upload failed". */
export function uploadErrorText(status, data, responseUrl = "") {
  if (data && typeof data.error === "string" && data.error) return data.error;
  if (status === 401 || /\/login(?:[/?#]|$)/.test(String(responseUrl || ""))) return t("asstSessionExpired");
  if (status === 413) return t("imgTooLarge");
  return t("imgUploadFailed");
}

/* One POST of a FormData with real upload progress. fetch() has no upload progress, so
 * this is XHR - which also means the CSRF header core.js adds to fetch() is added here by
 * hand. Resolves {status, data, url} for any 2xx answer with a JSON body; rejects with an
 * Error whose message is uploadErrorText(...) and which carries .status, .code and .data
 * (the server's JSON, when there was one) for anything else. Accept: application/json
 * asks routes that also serve forms (/projects, /pages/add) for JSON instead of a
 * redirect, so a refused upload never navigates away from photos held in memory. */
export function xhrUpload(url, formData, { onProgress, headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", url);
    if (csrfToken) request.setRequestHeader("X-CSRFToken", csrfToken);
    request.setRequestHeader("Accept", "application/json");
    for (const [name, value] of Object.entries(headers)) request.setRequestHeader(name, value);
    request.responseType = "json";
    request.upload.addEventListener("progress", event => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    });
    const fail = (status, data, responseUrl) => {
      const error = new Error(uploadErrorText(status, data, responseUrl));
      error.status = status;
      error.code = data?.code || "";
      error.data = data;
      reject(error);
    };
    request.addEventListener("load", () => {
      // responseType "json" gives null for an HTML page (a login redirect, a proxy error).
      const data = request.response && typeof request.response === "object" ? request.response : null;
      if (request.status >= 200 && request.status < 300 && data && data.ok !== false) {
        resolve({ status: request.status, data, url: request.responseURL });
      } else {
        fail(request.status, data, request.responseURL);
      }
    });
    request.addEventListener("error", () => fail(0, null, ""));
    request.addEventListener("abort", () => fail(0, null, ""));
    request.send(formData);
  });
}

/* One chat photo with real progress. Resolves {attachment, duplicate}: the server answers
 * the same photo chosen twice with the attachment it already has. */
export async function uploadImage(file, purpose, onProgress) {
  const form = new FormData();
  form.append("file", file, file.name || "photo.jpg");
  form.append("purpose", purpose);
  const { status, data, url } = await xhrUpload("/api/chat-attachments", form, { onProgress });
  if (!data.attachment) throw new Error(uploadErrorText(status, data, url));
  return { attachment: data.attachment, duplicate: Boolean(data.duplicate) };
}

/* A small preview of a photo for a gallery: decoded straight to `edge` px where the browser
 * can (createImageBitmap's resize options), upright, as a JPEG blob of a few kB - so forty
 * thumbnails never hold forty full-size decoded photos in memory. Resolves null for a file
 * the browser cannot decode (a HEIC photo in Chrome, a PDF): the caller shows a tile. */
export async function makeThumbnail(file, edge = 320) {
  if (typeof createImageBitmap !== "function" || !file) return null;
  let bitmap = null;
  try {
    try {
      // Decoded at the thumbnail width (the height follows the aspect ratio); a portrait
      // photo is brought down to `edge` on its long side by the canvas below.
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image", resizeWidth: edge,
        resizeQuality: "medium" });
    } catch (_) {
      bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    }
    const scale = Math.min(1, edge / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    return await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", 0.8));
  } catch (_) {
    return null;
  } finally {
    bitmap?.close?.();
  }
}

/* The row of thumbnails above a message box. Returns a small controller:
 *   ids()     the uploaded attachment ids, in the order shown
 *   busy()    whether something is still uploading
 *   failed()  whether an upload failed and is waiting for retry or removal
 *   count()   how many photos are in the tray
 *   clear()   empty the tray after the photos were sent (they belong to the message now)
 *   add(files)
 *   restore(attachments)  show photos the server still holds unsent (after a refresh)
 * The limit comes from the tray's data-max-photos (the server's effective per-message
 * limit for the configured vision model), else `max`.
 */
export function createAttachmentTray({ tray, inputs = [], purpose = "chat", max = 4, onChange, onError }) {
  const items = [];
  let counter = 0;
  max = Number(tray?.dataset?.maxPhotos) || max;

  function notify() {
    tray.classList.toggle("hidden", items.length === 0);
    onChange?.();
  }

  function statusLabel(item) {
    if (item.status === "uploading") {
      return item.progress == null ? t("imgUploading") : t("imgUploadingPercent", { percent: Math.round(item.progress * 100) });
    }
    if (item.status === "failed") return item.error || t("imgUploadFailed");
    return t("imgReady");
  }

  function render() {
    tray.replaceChildren(...items.map((item, index) => {
      const figure = document.createElement("figure");
      figure.className = `attach-item is-${item.status}`;
      figure.dataset.key = item.key;
      const image = document.createElement("img");
      image.src = item.previewUrl;
      image.alt = t("imgPhotoNumber", { number: index + 1 });
      figure.appendChild(image);
      if (item.status === "uploading") {
        const bar = document.createElement("span");
        bar.className = "attach-progress";
        // Width only when the browser reports progress; otherwise an animated stripe.
        if (item.progress != null) bar.style.setProperty("--progress", `${Math.round(item.progress * 100)}%`);
        else bar.classList.add("is-indeterminate");
        figure.appendChild(bar);
      }
      const caption = document.createElement("figcaption");
      caption.className = "attach-status";
      caption.textContent = statusLabel(item);
      caption.setAttribute("role", item.status === "failed" ? "alert" : "status");
      figure.appendChild(caption);
      const hints = item.attachment?.hints || [];
      if (hints.length) {
        const hint = document.createElement("p");
        hint.className = "attach-hint";
        hint.textContent = hints[0];
        figure.appendChild(hint);
      }
      const actions = document.createElement("div");
      actions.className = "attach-actions";
      // Classes let a surface lay the controls out its own way (the chat composer puts ×
      // on the corner and the others under the thumbnail).
      if (item.status === "failed") actions.appendChild(button("↻", t("imgRetry"), () => start(item), "attach-retry"));
      if (items.length > 1 && index > 0) actions.appendChild(button("←", t("imgMoveEarlier"), () => move(index, -1), "attach-move"));
      if (items.length > 1 && index < items.length - 1) actions.appendChild(button("→", t("imgMoveLater"), () => move(index, 1), "attach-move"));
      actions.appendChild(button("×", t("imgRemove"), () => remove(item), "attach-remove"));
      figure.appendChild(actions);
      return figure;
    }));
    notify();
  }

  function button(symbol, label, handler, className = "") {
    const control = document.createElement("button");
    control.type = "button";
    if (className) control.className = className;
    control.textContent = symbol;
    control.title = label;
    control.setAttribute("aria-label", label);
    control.addEventListener("click", handler);
    return control;
  }

  function move(index, step) {
    const [item] = items.splice(index, 1);
    items.splice(index + step, 0, item);
    render();
  }

  function release(item) {
    // Only previews made here are object URLs; a restored photo shows the server's URL.
    if (String(item.previewUrl || "").startsWith("blob:")) URL.revokeObjectURL(item.previewUrl);
  }

  function heldElsewhere(item) {
    const id = item.attachment?.id;
    return Boolean(id) && items.some(other => other !== item && other.attachment?.id === id);
  }

  function remove(item) {
    const index = items.indexOf(item);
    if (index < 0) return;
    items.splice(index, 1);
    release(item);
    // Not sent yet, so it is deleted on the server too; a failure only leaves it to the
    // one-day clean-up. Never while another thumbnail still holds the same attachment.
    if (item.attachment?.id && !heldElsewhere(item)) {
      fetch(`/api/chat-attachments/${encodeURIComponent(item.attachment.id)}`, { method: "DELETE" }).catch(() => {});
    }
    render();
  }

  async function start(item) {
    item.status = "uploading";
    item.progress = null;
    item.error = "";
    render();
    try {
      const file = await normalizeImage(item.file);
      const { attachment } = await uploadImage(file, purpose, progress => {
        item.progress = progress;
        const figure = tray.querySelector(`[data-key="${item.key}"]`);
        const bar = figure?.querySelector(".attach-progress");
        if (bar) {
          bar.classList.remove("is-indeterminate");
          bar.style.setProperty("--progress", `${Math.round(progress * 100)}%`);
        }
        const caption = figure?.querySelector(".attach-status");
        if (caption) caption.textContent = statusLabel(item);
      });
      item.attachment = attachment;
      item.status = "ready";
      if (heldElsewhere(item)) {
        // The same photo chosen twice is one attachment on the server: keep one thumbnail,
        // and do not delete the attachment the other one still holds.
        const index = items.indexOf(item);
        if (index >= 0) items.splice(index, 1);
        release(item);
        onError?.(t("imgDuplicate"));
      }
    } catch (error) {
      item.status = "failed";
      item.error = error.message;
    }
    render();
  }

  function restore(attachments) {
    for (const attachment of attachments || []) {
      if (!attachment?.id || !attachment.url || items.length >= max) continue;
      if (items.some(item => item.attachment?.id === attachment.id)) continue;
      counter += 1;
      items.push({ key: `a${counter}`, file: null, previewUrl: attachment.url, status: "ready", progress: null, attachment });
    }
    render();
  }

  function add(files) {
    for (const file of files) {
      if (items.length >= max) {
        onError?.(t("imgLimit", { count: max }));
        break;
      }
      if (file.type && !file.type.startsWith("image/") && !/\.(heic|heif)$/i.test(file.name || "")) {
        onError?.(t("imgNotPhoto"));
        continue;
      }
      counter += 1;
      const item = { key: `a${counter}`, file, previewUrl: URL.createObjectURL(file), status: "uploading", progress: null };
      items.push(item);
      start(item);
    }
    render();
  }

  for (const input of inputs) {
    input?.addEventListener("change", event => {
      add([...event.target.files]);
      event.target.value = "";  // the same photo can be chosen again after removing it
    });
  }
  tray.classList.add("hidden");

  return {
    ids: () => items.filter(item => item.status === "ready").map(item => item.attachment.id),
    busy: () => items.some(item => item.status === "uploading"),
    failed: () => items.some(item => item.status === "failed"),
    count: () => items.length,
    max: () => max,
    add,
    restore,
    clear() {
      items.splice(0).forEach(release);
      render();
    },
  };
}

/* Thumbnails of photos already sent with a message. The URL comes from the server, which
 * serves each picture only to its owner; a picture past its retention has no URL. */
export function sentPhotosNode(attachments) {
  const list = document.createElement("div");
  list.className = "sent-photos";
  attachments.forEach((item, index) => {
    if (item.url) {
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noopener";
      const image = document.createElement("img");
      image.src = item.url;
      image.alt = t("imgPhotoNumber", { number: index + 1 });
      image.loading = "lazy";
      link.appendChild(image);
      list.appendChild(link);
    } else {
      const gone = document.createElement("span");
      gone.className = "sent-photo-gone";
      gone.textContent = t("imgNoLongerStored");
      list.appendChild(gone);
    }
  });
  return list;
}

/* Tells the student what was unclear in their photo, briefly: the tutor's reply already
 * asks about anything that matters, so this is one line, not a warning box. */
export function photoNotesNode(notes) {
  const unclear = notes?.unclear || [];
  const hints = notes?.hints || [];
  if (!unclear.length && !hints.length) return null;
  const note = document.createElement("p");
  note.className = "photo-notes";
  const parts = [];
  if (unclear.length) parts.push(t("imgUnclearParts", { parts: unclear.map(item => `“${item}”`).join(", ") }));
  if (hints.length) parts.push(hints[0]);
  note.textContent = parts.join(" ");
  return note;
}

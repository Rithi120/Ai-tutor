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
export async function normalizeImage(file) {
  if (typeof createImageBitmap !== "function") return file;
  let bitmap;
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch (_) {
    return file;
  }
  try {
    const scale = Math.min(1, MAX_EDGE / Math.max(bitmap.width, bitmap.height));
    const supported = /^image\/(jpeg|png|webp)$/.test(file.type);
    if (scale === 1 && supported && file.size < 1_500_000) return file;
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bitmap.width * scale));
    canvas.height = Math.max(1, Math.round(bitmap.height * scale));
    const context = canvas.getContext("2d");
    context.fillStyle = "#ffffff";  // a transparent screenshot stays readable, not black
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/jpeg", JPEG_QUALITY));
    if (!blob) return file;
    const name = `${String(file.name || "photo").replace(/\.[^.]+$/, "") || "photo"}.jpg`;
    return new File([blob], name, { type: "image/jpeg" });
  } catch (_) {
    return file;
  } finally {
    bitmap.close?.();
  }
}

/* One upload with real progress. fetch() has no upload progress, so this is XHR - which
 * also means the CSRF header core.js adds to fetch() is added here by hand. */
export function uploadImage(file, purpose, onProgress) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/chat-attachments");
    if (csrfToken) request.setRequestHeader("X-CSRFToken", csrfToken);
    request.responseType = "json";
    request.upload.addEventListener("progress", event => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    });
    request.addEventListener("load", () => {
      const data = request.response || {};
      if (request.status >= 200 && request.status < 300 && data.ok !== false && data.attachment) {
        resolve(data.attachment);
      } else {
        reject(new Error(data.error || t("imgUploadFailed")));
      }
    });
    request.addEventListener("error", () => reject(new Error(t("imgUploadFailed"))));
    request.addEventListener("abort", () => reject(new Error(t("imgUploadFailed"))));
    const form = new FormData();
    form.append("file", file, file.name || "photo.jpg");
    form.append("purpose", purpose);
    request.send(form);
  });
}

/* The row of thumbnails above a message box. Returns a small controller:
 *   ids()     the uploaded attachment ids, in the order shown
 *   busy()    whether something is still uploading
 *   failed()  whether an upload failed and is waiting for retry or removal
 *   count()   how many photos are in the tray
 *   clear()   empty the tray after the photos were sent (they belong to the message now)
 *   add(files)
 */
export function createAttachmentTray({ tray, inputs = [], purpose = "chat", max = 4, onChange, onError }) {
  const items = [];
  let counter = 0;

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
      if (item.status === "failed") actions.appendChild(button("↻", t("imgRetry"), () => start(item)));
      if (items.length > 1 && index > 0) actions.appendChild(button("←", t("imgMoveEarlier"), () => move(index, -1)));
      if (items.length > 1 && index < items.length - 1) actions.appendChild(button("→", t("imgMoveLater"), () => move(index, 1)));
      actions.appendChild(button("×", t("imgRemove"), () => remove(item)));
      figure.appendChild(actions);
      return figure;
    }));
    notify();
  }

  function button(symbol, label, handler) {
    const control = document.createElement("button");
    control.type = "button";
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

  function remove(item) {
    const index = items.indexOf(item);
    if (index < 0) return;
    items.splice(index, 1);
    URL.revokeObjectURL(item.previewUrl);
    // Not sent yet, so it is deleted on the server too; a failure only leaves it to the
    // one-day clean-up.
    if (item.attachment?.id) fetch(`/api/chat-attachments/${encodeURIComponent(item.attachment.id)}`, { method: "DELETE" }).catch(() => {});
    render();
  }

  async function start(item) {
    item.status = "uploading";
    item.progress = null;
    item.error = "";
    render();
    try {
      const file = await normalizeImage(item.file);
      item.attachment = await uploadImage(file, purpose, progress => {
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
      item.status = "ready";
    } catch (error) {
      item.status = "failed";
      item.error = error.message;
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
    add,
    clear() {
      items.splice(0).forEach(item => URL.revokeObjectURL(item.previewUrl));
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

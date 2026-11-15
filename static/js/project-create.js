/* Project Mode's create form with the multi-image collection (templates/projects.html).
 *
 * The photos, PDFs and camera scans the student chooses gather in one ordered gallery
 * (image-collection.js) instead of the native input, which forgot the first pick when a
 * second one was made. On "Create" the first batch creates the project (POST /projects
 * with Accept: application/json) and the others are added to it (POST .../pages/add), each
 * under the server's request limit, so forty phone photos never become one refused 40 MB
 * request. Nothing navigates away until every page is saved: a failed batch stays in the
 * gallery with "Retry", and the project made by the first batch is never made twice.
 * Without this module (no JavaScript, or it failed) scanner.js posts the form as before.
 */
import { t } from "./i18n.js";
import { createImageCollection, materialsForm, uploadCollection, xhrUpload } from "./image-collection.js";
import { DEFAULT_BATCH } from "./image-collection-rules.js";

const form = document.querySelector("#projectCreateForm");
const mount = document.querySelector("#projectImages");
const galleryInput = document.querySelector("#imageUploadInput");

if (form && mount && galleryInput) setUp();

function setUp() {
  const status = document.querySelector("#projectUploadStatus");
  const retry = document.querySelector("#projectRetryFailed");
  const continueLink = document.querySelector("#projectContinue");
  const submitButton = document.querySelector("#createProjectButton");
  const progress = document.querySelector("#uploadProgress");
  const action = form.getAttribute("action") || window.location.pathname;
  let project = null;   // the server's answer once the first batch has created the project
  let scanNumber = 0;

  const collection = createImageCollection({
    root: mount,
    inputs: [galleryInput],
    dropZone: form.querySelector(".document-input-options"),
    max: Number(mount.dataset.max) || 40,
    maxFileBytes: Number(mount.dataset.maxBytes) || 0,
    accept: galleryInput.getAttribute("accept") || "",
    labels: true,
    onChange: changed => {
      form.dataset.collectionCount = String(changed.count());
      document.dispatchEvent(new CustomEvent("learnova:collection-changed"));
      retry.hidden = !(changed.failed() && !changed.busy());
    },
    onError: message => { status.textContent = message; },
  });
  form.dataset.imageCollection = "ready";
  window.__projectImages = collection;   // for the browser tests and the console

  // The scanner (scanner.js) hands over its accepted pages when it closes: lossless PNGs
  // with the rotation and crop the student chose, in the order they were accepted.
  document.addEventListener("learnova:scans-finished", event => {
    for (const scan of event.detail?.scans || []) {
      scanNumber += 1;
      const type = scan.blob?.type || "image/png";
      const extension = type === "image/png" ? "png" : type === "image/webp" ? "webp" : "jpg";
      const file = new File([scan.blob], `camera-page-${scanNumber}.${extension}`, { type, lastModified: Date.now() + scanNumber });
      collection.add([file], { kind: "camera", transform: { rotation: scan.rotation || 0, crop: scan.crop || {} } });
    }
  });

  const typedTopics = () => (document.querySelector("#projectTopics")?.value || "")
    .split(/[\n,]/).map(item => item.trim()).filter(Boolean).length;
  const gridFiles = () => document.querySelector("#competencyGridInput")?.files.length || 0;
  const gridBytes = () => [...(document.querySelector("#competencyGridInput")?.files || [])]
    .reduce((sum, file) => sum + file.size, 0);

  function working(on) {
    submitButton.disabled = on;
    progress.classList.toggle("hidden", !on);
  }

  async function send(entries, { onProgress }) {
    if (!project) {
      // The first batch carries the form (title, subject, class, exam date, topics and the
      // Kompetenzraster) and creates the project.
      const body = materialsForm(entries, new FormData(form));
      try {
        const { data } = await xhrUpload(action, body, { onProgress });
        project = data;
      } catch (error) {
        // Every file of this batch was refused: no project was made. Each file says why,
        // and the next batch (or a retry) tries to create it again.
        const results = error.data?.details?.results;
        if (error.status === 422 && Array.isArray(results)) return { results };
        throw error;
      }
      continueLink.href = project.start_url;
      if (project.duplicate && !(project.results || []).length) {
        // The same first batch arrived twice (the answer to the first was lost): its
        // files are in the project already.
        return { results: entries.map((_entry, index) => ({ index, status: "duplicate" })) };
      }
      return project;
    }
    const { data } = await xhrUpload(project.add_pages_url, materialsForm(entries), { onProgress });
    project.start_url = data.start_url;
    continueLink.href = data.start_url;
    return data;
  }

  async function submit() {
    status.textContent = "";
    if (!collection.count() && !project && !gridFiles() && !typedTopics()) {
      status.textContent = t("chooseProjectInput");
      return;
    }
    if (collection.busy()) return;
    working(true);
    retry.hidden = true;
    try {
      if (!collection.count() && !project) {
        // Topics or a Kompetenzraster only: one request, no gallery.
        const { data } = await xhrUpload(action, materialsForm([], new FormData(form)));
        window.location.assign(data.start_url);
        return;
      }
      const outcome = await uploadCollection(collection, {
        send,
        // The Kompetenzraster rides with the first batch: its bytes come off the budget, so
        // that request stays under the server's limit too.
        batch: { ...DEFAULT_BATCH,
          maxBytes: Math.max(4 * 1024 * 1024, DEFAULT_BATCH.maxBytes - (project ? 0 : gridBytes())) },
        onStep: ({ from, to, total }) => {
          status.textContent = from === to ? t("imgcolUploadingOne", { number: from, total })
            : t("imgcolUploadingRange", { from, to, total });
        },
      });
      if (outcome.ok && project) {
        status.textContent = t("imgcolAllSaved");
        window.location.assign(project.start_url);
        return;
      }
      const reason = outcome.error?.message || "";
      status.textContent = [reason, t("imgcolFailedSummary", { count: outcome.failed })].filter(Boolean).join(" ");
      retry.hidden = !outcome.failed;
      continueLink.hidden = !project;
    } catch (error) {
      status.textContent = error.message || t("uploadRetry");
    }
    working(false);
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    submit();
  });
  retry.addEventListener("click", () => submit());
}

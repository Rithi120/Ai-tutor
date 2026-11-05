import { t } from "./i18n.js";

// The one-tap start page. Reads each page (one request per page, like the review page
// does, so a slow scan never hits one long request), then asks the server to build the
// sections and open the lesson, then follows the redirect. Each step is shown as it runs,
// and every uploaded document has its own row: one dot per page and a state in words, so
// "Document 5: Needs OCR review" is said by name instead of disappearing behind the
// documents that were read.
//
// The topics are worked out the same way: one short request per part (analysis/step), until
// the server says they are finished; a reload carries on where the steps stopped (the server
// marks the page to run by itself), and "Update topics" (data-then="topics") ends on the
// topic list instead of a lesson.
//
// A page that comes back "waiting" means the AI provider is busy or the student's hourly
// AI allowance is nearly used up. Sending the next pages would only spend requests to learn
// the same thing (and could trip a six-hour cooldown), so reading stops there, the page
// says that reading continues later, and the chain goes on with what was read. The same
// for a page that comes back read but "paused": the offline OCR read it because the AI is
// busy.
const card = document.getElementById("projectStart");

if (card) {
  const pageIds = JSON.parse(card.dataset.pageIds || "[]");
  const form = document.getElementById("startForm");
  const button = document.getElementById("startButton");
  const error = document.getElementById("startError");
  const detail = document.getElementById("startReadDetail");
  const buildDetail = document.getElementById("startBuildDetail");
  const summary = document.getElementById("startSummary");
  const notice = document.getElementById("startNotice");
  const steps = Object.fromEntries([...card.querySelectorAll("[data-step]")].map(node => [node.dataset.step, node]));
  let running = false;

  function mark(step, state) {
    if (steps[step]) steps[step].dataset.state = state;
  }

  function dot(pageId) {
    return card.querySelector(`.start-dots [data-page-id="${Number(pageId)}"]`);
  }

  // One document row from the server's material report (project_material_report).
  function showFile(file) {
    if (!file) return;
    const row = card.querySelector(`.start-file[data-file-id="${Number(file.file_id)}"]`);
    if (!row) return;
    row.dataset.state = file.reading_state || "";
    const state = row.querySelector('[data-role="state"]');
    if (state) state.textContent = file.status_text || file.reading_label || "";
    for (const page of file.pages || []) {
      const node = dot(page.page_id);
      if (!node) continue;
      node.dataset.state = page.reading || "";
      node.title = page.message || "";
    }
  }

  function showReport(report) {
    if (!report) return;
    for (const file of report.files || []) showFile(file);
    if (summary && typeof report.summary === "string") summary.textContent = report.summary;
  }

  function showNotice(message) {
    if (!notice || !message) return;
    notice.textContent = message;
    notice.hidden = false;
  }

  async function refreshReport() {
    if (!card.dataset.analysisUrl) return;
    try {
      const response = await fetch(card.dataset.analysisUrl, { headers: { Accept: "application/json" } });
      if (response.ok) showReport(await response.json());
    } catch {
      // The rows keep what the page requests already showed.
    }
  }

  // While the server works out the topics (one AI call per few pages, stored as each one
  // arrives), the report says how far it got: "Working out the topics: part 2 of 10".
  function watchAnalysis() {
    let stopped = false;
    async function tick() {
      if (stopped || !card.dataset.analysisUrl) return;
      try {
        const response = await fetch(card.dataset.analysisUrl, { headers: { Accept: "application/json" } });
        const report = response.ok ? await response.json() : null;
        const progress = report && report.analysis_progress;
        if (!stopped && buildDetail && progress && progress.total) {
          buildDetail.textContent = t("startAnalysingPart", { done: Math.min(progress.done + 1, progress.total), total: progress.total });
        }
      } catch {
        // Progress is a nicety; the build itself goes on.
      }
      if (!stopped) setTimeout(tick, 3000);
    }
    setTimeout(tick, 2000);
    return () => { stopped = true; };
  }

  async function readPage(pageId) {
    const url = card.dataset.recognizeUrl.replace(/\/0\/recognize$/, `/${pageId}/recognize`);
    const node = dot(pageId);
    if (node) node.dataset.state = "active";
    try {
      const response = await fetch(url, {
        method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: "{}",
      });
      const data = await response.json().catch(() => ({}));
      if (data.file) showFile(data.file);
      else if (node) node.dataset.state = response.ok ? "read" : "unreadable";
      if (summary && typeof data.summary === "string") summary.textContent = data.summary;
      // paused: the page was read offline because the AI is busy - asking it again for the
      // next page would only cost another refused request, so reading stops here too.
      return {
        state: data.state || (response.ok ? "read" : "unreadable"), message: data.message || "",
        paused: data.paused === true, pauseMessage: data.pause_message || "",
      };
    } catch {
      if (node) node.dataset.state = "pending";
      return { state: "unreadable", message: "" };
    }
  }

  const sleep = ms => new Promise(resolve => { setTimeout(resolve, ms); });

  // {status, data, retryAfter}; status 0 when the request never got an answer.
  async function post(url, body) {
    try {
      const response = await fetch(url, {
        method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const data = await response.json().catch(() => null);
      const wait = Number(response.headers.get("Retry-After"));
      return { status: response.status, data, retryAfter: Number.isFinite(wait) && wait > 0 ? Math.min(wait, 30) : 0 };
    } catch {
      return { status: 0, data: null, retryAfter: 0 };
    }
  }

  // The topics are worked out in short steps (one or two AI calls each), so no request runs
  // into a proxy timeout, and every step stores what it found. A 202 means another request
  // (a second tab, a step a proxy cut off) is still on it: wait, then ask again - nothing is
  // paid twice. A cut-off answer (502/504, no connection) is not a failure either: the step
  // may still be running on the server, and the next one finds its work stored. A provider
  // stop ends the loop: with topics the chain goes on with them, without it is said.
  async function analyse() {
    const url = card.dataset.analysisStepUrl;
    if (!url) return;
    const started = Date.now();
    let idle = 0;
    for (let attempt = 0; attempt < 60 && Date.now() - started < 600000; attempt += 1) {
      const { status, data, retryAfter } = await post(url, { then: card.dataset.then || "" });
      if (status === 200 && data) {
        if (data.finished) return;
        idle = data.calls ? 0 : idle + 1;
        if (idle >= 2) return;
        if (buildDetail && data.total) {
          buildDetail.textContent = t("startAnalysingPart", { done: Math.min((data.done || 0) + 1, data.total), total: data.total });
        }
        continue;
      }
      if (status === 202) {
        if (buildDetail) buildDetail.textContent = t("startAnalysisBusy");
        await sleep(1000 * (retryAfter || 3));
        continue;
      }
      if ((status === 429 || status === 503) && data && data.stopped) {
        showNotice(data.error);
        if (data.has_topics) return;
        throw new Error(data.error || t("startFailed"));
      }
      if (status === 429) {
        // The step's own hourly limit: quick-start would work out everything that is left in
        // one long request, which is what the steps are for avoiding. Stop and say so.
        throw new Error((data && data.error) || t("startFailed"));
      }
      if (status === 0 || status === 502 || status === 504 || (status >= 500 && !data)) {
        if (buildDetail) buildDetail.textContent = t("startAnalysisSlow");
        await sleep(5000);
        continue;
      }
      return; // Anything else: quick-start's own path decides.
    }
  }

  // Opens the topics or the lesson. After the steps every batch is stored, so this makes no
  // analysis call; while another request still holds the analysis it answers 202.
  async function quickStart() {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const { status, data, retryAfter } = await post(card.dataset.quickStartUrl, { then: card.dataset.then || "" });
      if (status === 202 && data && data.code === "analysis_running") {
        if (buildDetail) buildDetail.textContent = t("startAnalysisBusy");
        await sleep(1000 * (retryAfter || 3));
        continue;
      }
      if (status < 200 || status >= 300 || !data || !data.redirect) throw new Error((data && data.error) || t("startFailed"));
      return data;
    }
    throw new Error(t("startAnalysisBusy"));
  }

  async function run() {
    if (running) return;
    running = true;
    button.disabled = true;
    error.hidden = true;
    card.classList.add("is-running");
    try {
      mark("read", "active");
      let trouble = false;
      for (let index = 0; index < pageIds.length; index += 1) {
        detail.textContent = t("startReadingPage", { current: index + 1, total: pageIds.length });
        const result = await readPage(pageIds[index]);
        if (result.state !== "read") trouble = true;
        if (result.state === "waiting") {
          showNotice(result.message || t("startReadingPaused"));
          break;
        }
        if (result.paused) {
          showNotice(result.pauseMessage || t("startReadingPaused"));
          break;
        }
      }
      await refreshReport();
      detail.textContent = summary ? summary.textContent : "";
      mark("read", trouble ? "warn" : "done");
      mark("build", "active");
      const stopWatching = watchAnalysis();
      let data;
      try {
        await analyse();
        data = await quickStart();
      } finally {
        stopWatching();
      }
      mark("build", "done");
      mark("open", "active");
      location.href = data.redirect;
    } catch (failure) {
      error.textContent = failure.message || t("startFailed");
      error.hidden = false;
      button.disabled = false;
      button.textContent = t("startTryAgain");
      card.classList.remove("is-running");
      running = false;
      refreshReport();
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    run();
  });
  if (card.dataset.auto === "true") run();
}

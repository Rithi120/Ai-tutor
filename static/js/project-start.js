import { t } from "./i18n.js";

// The one-tap start page. Reads each page (one request per page, like the review page
// does, so a slow scan never hits one long request), then asks the server to build the
// sections and open the lesson, then follows the redirect. Each step is shown as it runs.
const card = document.getElementById("projectStart");

if (card) {
  const pageIds = JSON.parse(card.dataset.pageIds || "[]");
  const form = document.getElementById("startForm");
  const button = document.getElementById("startButton");
  const error = document.getElementById("startError");
  const detail = document.getElementById("startReadDetail");
  const steps = Object.fromEntries([...card.querySelectorAll("[data-step]")].map(node => [node.dataset.step, node]));
  let running = false;

  function mark(step, state) {
    if (steps[step]) steps[step].dataset.state = state;
  }

  async function run() {
    if (running) return;
    running = true;
    button.disabled = true;
    error.hidden = true;
    card.classList.add("is-running");
    try {
      mark("read", "active");
      let failures = 0;
      for (let index = 0; index < pageIds.length; index += 1) {
        detail.textContent = t("startReadingPage", { current: index + 1, total: pageIds.length });
        const url = card.dataset.recognizeUrl.replace(/\/0\/recognize$/, `/${pageIds[index]}/recognize`);
        try {
          const response = await fetch(url, { method: "POST" });
          if (!response.ok) failures += 1;
        } catch {
          failures += 1;
        }
      }
      detail.textContent = failures ? t("startPagesUnreadable", { count: failures }) : "";
      mark("read", failures ? "warn" : "done");
      mark("build", "active");
      const response = await fetch(card.dataset.quickStartUrl, {
        method: "POST", headers: { Accept: "application/json", "Content-Type": "application/json" }, body: "{}",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || t("startFailed"));
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
    }
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    run();
  });
  if (card.dataset.auto === "true") run();
}

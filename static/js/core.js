import { aiNoticeFrom, noticeDue } from "./ai-limit-rules.js";

const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const nativeFetch = window.fetch.bind(window);
const NOTICE_SHOWN_KEY = "learnova.aiNotice.shownAt";

// "Max limit reached - using a slower AI model": one banner, bottom of the screen on a
// phone, dismissible, and not repeated within a minute.
function showAiNotice(notice) {
  let shownAt = 0;
  try { shownAt = Number(sessionStorage.getItem(NOTICE_SHOWN_KEY)) || 0; } catch { /* storage blocked */ }
  if (!noticeDue(shownAt, Date.now())) return;
  try { sessionStorage.setItem(NOTICE_SHOWN_KEY, String(Date.now())); } catch { /* storage blocked */ }
  document.getElementById("aiNotice")?.remove();
  const banner = document.createElement("div");
  banner.id = "aiNotice";
  banner.className = "ai-notice";
  banner.setAttribute("role", "status");
  const text = document.createElement("span");
  text.textContent = `⚠️ ${notice.message}`;
  const close = document.createElement("button");
  close.type = "button";
  close.setAttribute("aria-label", "×");
  close.textContent = "×";
  close.addEventListener("click", () => banner.remove());
  banner.append(text, close);
  document.body.appendChild(banner);
  window.setTimeout(() => banner.remove(), 12000);
}

function watchForAiNotice(response) {
  if (!response.ok || !(response.headers.get("content-type") || "").includes("application/json")) return;
  response.clone().json().then(payload => {
    const notice = aiNoticeFrom(payload);
    if (notice) showAiNotice(notice);
  }).catch(() => { /* not an object, or already consumed */ });
}

window.fetch = (input, options = {}) => {
  const requestUrl = new URL(typeof input === "string" ? input : input.url, window.location.href);
  const method = String(options.method || (typeof input === "string" ? "GET" : input.method) || "GET").toUpperCase();
  if (requestUrl.origin === window.location.origin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    const headers = new Headers(options.headers || (typeof input === "string" ? undefined : input.headers));
    if (csrfToken && !headers.has("X-CSRFToken")) headers.set("X-CSRFToken", csrfToken);
    options = {...options, headers};
  }
  return nativeFetch(input, options).then(response => {
    watchForAiNotice(response);
    return response;
  });
};

document.addEventListener("submit", event => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || !csrfToken || form.method.toUpperCase() === "GET") return;
  let tokenInput = form.querySelector('input[name="csrf_token"]');
  if (!tokenInput) {
    tokenInput = document.createElement("input");
    tokenInput.type = "hidden";
    tokenInput.name = "csrf_token";
    form.appendChild(tokenInput);
  }
  tokenInput.value = csrfToken;
});

function dismissAlert(alert) {
  alert.classList.add("alert-leaving");
  window.setTimeout(() => alert.remove(), 240);
}

document.querySelectorAll(".global-alerts .form-alert").forEach(alert => {
  alert.querySelector(".alert-dismiss")?.addEventListener("click", () => dismissAlert(alert));
  if (alert.dataset.alertCategory !== "success") return;
  let timer = window.setTimeout(() => dismissAlert(alert), 6500);
  alert.addEventListener("mouseenter", () => window.clearTimeout(timer));
  alert.addEventListener("mouseleave", () => { timer = window.setTimeout(() => dismissAlert(alert), 2500); });
});

const loadingOverlay = document.querySelector("#globalLoadingOverlay");
const loadingStage = loadingOverlay?.querySelector(".loading-stage");
let loadingTimer = null;

function showStagedLoading() {
  if (!loadingOverlay || !loadingStage) return;
  const stages = JSON.parse(loadingStage.dataset.loadingStages || "[]");
  let index = 0;
  loadingOverlay.classList.remove("hidden");
  loadingOverlay.setAttribute("aria-hidden", "false");
  loadingTimer = window.setInterval(() => {
    index = Math.min(index + 1, stages.length - 1);
    if (stages[index]) loadingStage.textContent = stages[index];
    if (index === stages.length - 1) window.clearInterval(loadingTimer);
  }, 1700);
}

document.addEventListener("submit", event => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || form.method.toUpperCase() === "GET") return;
  queueMicrotask(() => {
    if (event.defaultPrevented || !form.checkValidity() || form.classList.contains("auth-form")) return;
    const submitter = event.submitter || form.querySelector("button[type='submit'], input[type='submit']");
    if (submitter instanceof HTMLButtonElement && !submitter.disabled) {
      submitter.style.minWidth = `${submitter.offsetWidth}px`;
      submitter.classList.add("app-button-loading");
      submitter.disabled = true;
      submitter.dataset.appSubmitting = "true";
    }
    const action = new URL(form.action || location.href, location.href).pathname;
    if (/recognize|process|practice|\/test|\/exam\/new/.test(action)) showStagedLoading();
  });
});

window.addEventListener("pageshow", () => {
  document.querySelectorAll("[data-app-submitting]").forEach(button => {
    button.disabled = false;
    button.classList.remove("app-button-loading");
    button.style.minWidth = "";
    delete button.dataset.appSubmitting;
  });
  if (loadingTimer) window.clearInterval(loadingTimer);
  loadingOverlay?.classList.add("hidden");
  loadingOverlay?.setAttribute("aria-hidden", "true");
});

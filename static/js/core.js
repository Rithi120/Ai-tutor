const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const nativeFetch = window.fetch.bind(window);

window.fetch = (input, options = {}) => {
  const requestUrl = new URL(typeof input === "string" ? input : input.url, window.location.href);
  const method = String(options.method || (typeof input === "string" ? "GET" : input.method) || "GET").toUpperCase();
  if (requestUrl.origin === window.location.origin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    const headers = new Headers(options.headers || (typeof input === "string" ? undefined : input.headers));
    if (csrfToken && !headers.has("X-CSRFToken")) headers.set("X-CSRFToken", csrfToken);
    options = {...options, headers};
  }
  return nativeFetch(input, options);
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

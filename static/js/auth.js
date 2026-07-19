const form = document.querySelector("#authForm");
const card = document.querySelector(".auth-card");
const password = document.querySelector("#authPassword");
const passwordToggle = document.querySelector(".password-toggle");
const submitButton = document.querySelector(".auth-submit");
const submitLabel = document.querySelector(".auth-submit-label");
const defaultSubmitLabel = submitLabel?.textContent || "";

function t(key) {
  return window.LEARNOVA_I18N?.[key] || key;
}

passwordToggle?.addEventListener("click", () => {
  const showing = password?.type === "text";
  password.type = showing ? "password" : "text";
  const label = t(showing ? "showPassword" : "hidePassword");
  passwordToggle.setAttribute("aria-label", label);
  passwordToggle.setAttribute("title", label);
  passwordToggle.classList.toggle("is-visible", !showing);
  password.focus({preventScroll: true});
});

function resetSubmissionState() {
  if (!submitButton || !submitLabel) return;
  submitButton.disabled = false;
  submitButton.classList.remove("is-loading");
  submitLabel.textContent = defaultSubmitLabel;
  card?.removeAttribute("aria-busy");
}

form?.addEventListener("submit", event => {
  if (event.defaultPrevented || !form.checkValidity() || submitButton?.disabled) return;
  submitButton.disabled = true;
  submitButton.classList.add("is-loading");
  submitLabel.textContent = t(card?.dataset.authMode === "register" ? "creatingAccount" : "signingIn");
  card?.setAttribute("aria-busy", "true");
});

window.addEventListener("pageshow", resetSubmissionState);

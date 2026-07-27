import { escapeHtml } from "../dom.js";
import { t, selectedLanguage } from "../i18n.js";

export { escapeHtml, t, selectedLanguage };

export const SUBJECTS = ["Mathematics", "English", "German", "History", "Biology", "Chemistry", "Physics", "Other"];
export const FLAGS = window.LEARNOVA_FLAGS || {};
export const SET_ID = window.LEARNOVA_SET_ID ?? null;

const MATH_DELIMITERS = [
  { left: "$$", right: "$$", display: true },
  { left: "\\[", right: "\\]", display: true },
  { left: "$", right: "$", display: false },
  { left: "\\(", right: "\\)", display: false },
];

export function renderMath(root) {
  if (!root || typeof window.renderMathInElement !== "function") return;
  try {
    window.renderMathInElement(root, {
      delimiters: MATH_DELIMITERS, throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option", "input"],
    });
  } catch (_) { /* never let a formatting glitch break the page */ }
}

export async function api(url, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    if (body instanceof FormData) {
      options.body = body;
    } else {
      options.headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(body);
    }
  }
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* non-JSON */ }
  if (!response.ok) {
    const error = new Error(data.error || `Request failed (${response.status})`);
    error.code = data.code;
    error.status = response.status;
    throw error;
  }
  return data;
}

let toastTimer = null;
export function toast(message, kind = "info") {
  let el = document.querySelector("#fcToast");
  if (!el) {
    el = document.createElement("div");
    el.id = "fcToast";
    el.setAttribute("role", "status");
    el.setAttribute("aria-live", "polite");
    document.body.appendChild(el);
  }
  el.textContent = message;
  el.className = `toast toast-${kind}`;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => el.classList.add("hidden"), 4200);
}

export function optionsHtml(values, selected) {
  return values.map(v => `<option value="${escapeHtml(v)}"${v === selected ? " selected" : ""}>${escapeHtml(v)}</option>`).join("");
}

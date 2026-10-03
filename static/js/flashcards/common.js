import { escapeHtml } from "../dom.js";
import { t, selectedLanguage } from "../i18n.js";
import { renderMath } from "../math.js";
import { withRetryHint } from "../ai-limit-rules.js";

// One renderer for the whole app; re-exported so existing importers are unchanged.
export { escapeHtml, t, selectedLanguage, renderMath };

export const SUBJECTS = ["Mathematics", "English", "German", "History", "Biology", "Chemistry", "Physics", "Other"];
export const FLAGS = window.LEARNOVA_FLAGS || {};
export const SET_ID = window.LEARNOVA_SET_ID ?? null;



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
    error.details = data.details || {};
    error.message = withRetryHint(error.message, error);
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

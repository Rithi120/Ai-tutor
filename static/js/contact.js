// Contact page (/contact): message counter, focus on the result, and a draft of the
// topic and message in sessionStorage. The draft covers the two cases where the server
// cannot hand typed text back (an expired form or too many requests both redirect).
// The name and email address are never stored. No user-visible strings here.

const DRAFT_KEY = "learnova:contact-draft";

function readDraft() {
  try {
    const raw = window.sessionStorage.getItem(DRAFT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeDraft(draft) {
  try {
    window.sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  } catch {
    /* private mode or storage blocked: the form still works */
  }
}

function clearDraft() {
  try {
    window.sessionStorage.removeItem(DRAFT_KEY);
  } catch {
    /* ignore */
  }
}

// An error-summary link jumps to its field. The browser would put the control itself at
// the top, under the sticky header, with its label and error above the screen; instead
// the whole field is scrolled below the header (scroll-margin in contact.css), then the
// control is focused where it is.
function linkSummaryToFields() {
  document.querySelectorAll(".contact-error-summary a[href^='#contact-']").forEach((link) => {
    link.addEventListener("click", (event) => {
      const control = document.getElementById(link.getAttribute("href").slice(1));
      const field = control?.closest(".contact-field");
      if (!control || !field) return;
      event.preventDefault();
      field.scrollIntoView({ block: "start" });
      control.focus({ preventScroll: true });
    });
  });
}

function init() {
  const focusTarget = document.querySelector("[data-contact-focus]");
  if (focusTarget) focusTarget.focus();
  linkSummaryToFields();

  if (document.querySelector("[data-contact-sent]")) {
    clearDraft();
    return;
  }

  const form = document.querySelector("[data-contact-form]");
  if (!form) return;
  const message = form.querySelector("[data-contact-message]");
  const category = form.querySelector("select[name='category']");
  const counter = form.querySelector("[data-contact-count]");

  const updateCount = () => {
    if (counter && message) counter.textContent = String(message.value.length);
  };

  const hasServerState = Boolean(document.querySelector(".contact-error-summary"))
    || Boolean(message && message.value) || Boolean(category && category.value);
  const draft = readDraft();
  if (draft && !hasServerState) {
    if (message && !message.value && typeof draft.message === "string") message.value = draft.message;
    if (category && !category.value && typeof draft.category === "string"
        && [...category.options].some((option) => option.value === draft.category)) {
      category.value = draft.category;
    }
  }
  updateCount();

  const save = () => writeDraft({
    category: category ? category.value : "",
    message: message ? message.value : "",
  });
  message?.addEventListener("input", () => { updateCount(); save(); });
  category?.addEventListener("change", save);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init, { once: true });
} else {
  init();
}

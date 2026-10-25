const STORAGE_KEY = "learnova:theme";

// Storage can throw (Safari private browsing, blocked site data); the theme then simply
// follows the system setting for this visit.
function savedTheme() {
  try { return localStorage.getItem(STORAGE_KEY); } catch (_) { return null; }
}

function preferredTheme() {
  const saved = savedTheme();
  if (saved === "light" || saved === "dark") return saved;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll("[data-theme-toggle]").forEach(button => {
    const dark = theme === "dark";
    const label = dark ? button.dataset.labelLight : button.dataset.labelDark;
    button.setAttribute("aria-pressed", String(dark));
    // A toggle with visible text (the one in the mobile menu) is named by that text, so
    // what a screen reader announces matches what a sighted student reads.
    const visible = button.querySelector("[data-theme-label]");
    if (visible) {
      visible.textContent = label;
      button.removeAttribute("aria-label");
    } else {
      button.setAttribute("aria-label", label);
      button.title = label;
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(preferredTheme());
  document.querySelectorAll("[data-theme-toggle]").forEach(button => {
    button.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem(STORAGE_KEY, next); } catch (_) { /* this visit only */ }
      applyTheme(next);
    });
  });
});

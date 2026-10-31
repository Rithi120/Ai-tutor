// Settings -> Subscription and the admin code page: a submit button that says it is busy
// and cannot be pressed twice, so a slow connection never sends a code twice. The server
// is still the guard - a repeated code is answered "already active" and extends nothing.

for (const form of document.querySelectorAll("form[data-busy-form]")) {
  form.addEventListener("submit", () => {
    const button = form.querySelector("button[type=submit][data-busy-label]");
    if (!button || button.disabled) return;
    button.dataset.idleLabel = button.textContent;
    button.textContent = button.dataset.busyLabel;
    button.disabled = true;
    form.setAttribute("aria-busy", "true");
  });
}

// Coming back with the browser's back button restores the page from cache with the
// button still disabled; give the student a working form again.
window.addEventListener("pageshow", () => {
  for (const button of document.querySelectorAll("form[data-busy-form] button[data-idle-label]")) {
    button.textContent = button.dataset.idleLabel;
    button.disabled = false;
    button.closest("form")?.removeAttribute("aria-busy");
  }
});

// The issued code: one tap copies it.
const copyButton = document.querySelector("[data-copy-target]");
if (copyButton) {
  copyButton.addEventListener("click", async () => {
    const target = document.getElementById(copyButton.dataset.copyTarget);
    if (!target) return;
    try {
      await navigator.clipboard.writeText(target.textContent.trim());
      copyButton.textContent = copyButton.dataset.copiedLabel || copyButton.textContent;
    } catch (_) {
      // No clipboard permission: the code stays selectable on screen.
      window.getSelection()?.selectAllChildren(target);
    }
  });
}

// Onboarding (templates/auth_onboarding.html): the username is checked while it is typed,
// with the same rules the server applies on submit (/auth/username-check). The server
// check stays the decision; this only saves a round trip.
const input = document.querySelector("[data-username-check]");
const hint = document.querySelector("[data-username-hint]");

if (input && hint) {
  const neutral = hint.textContent;
  let timer = 0;
  let last = "";
  input.addEventListener("input", () => {
    window.clearTimeout(timer);
    const value = input.value.trim();
    if (value.length < 3) {
      hint.textContent = neutral;
      hint.className = "field-hint";
      input.removeAttribute("aria-invalid");
      return;
    }
    timer = window.setTimeout(async () => {
      if (value === last) return;
      last = value;
      try {
        const response = await fetch(`${input.dataset.usernameCheck}?name=${encodeURIComponent(value)}`, {
          headers: { Accept: "application/json" }, credentials: "same-origin" });
        if (!response.ok) return;
        const data = await response.json();
        if (input.value.trim() !== value) return;
        hint.textContent = data.ok ? `✓ @${data.name}` : data.message;
        hint.className = data.ok ? "field-ok" : "field-error";
        input.setAttribute("aria-invalid", String(!data.ok));
      } catch (error) {
        hint.textContent = neutral;   // offline: the server checks on submit anyway
      }
    }, 350);
  });
}

// The code field accepts "123 456" and pasted codes; only digits are kept.
const code = document.querySelector(".otp-input");
code?.addEventListener("input", () => {
  const digits = code.value.replace(/\D/g, "").slice(0, 6);
  if (code.value !== digits) code.value = digits;
});

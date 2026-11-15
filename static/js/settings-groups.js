/* Settings: each group is one row ("Profil ›") that opens on a tap (templates/settings.html).
 *
 * - A link into a group (/settings#subscription, #profile, #help, #tips …) opens that group
 *   and scrolls to the target, also when the address changes on the same page.
 * - Saving a form reloads the page; the group it was saved from opens again, once. Every
 *   other visit starts with all groups closed. Without storage it simply starts closed.
 */

const KEY = "learnova:settings-saved-in";

function openFor(hash) {
  const id = decodeURIComponent(String(hash || "").replace(/^#/, ""));
  if (!id) return false;
  const target = document.getElementById(id);
  const group = target?.closest("details.settings-group");
  if (!group) return false;
  group.open = true;
  // After the group has laid out, bring the target (not the row) into view.
  requestAnimationFrame(() => target.scrollIntoView({ block: "start" }));
  return true;
}

function reopenAfterSave() {
  let id = "";
  try {
    id = sessionStorage.getItem(KEY) || "";
    sessionStorage.removeItem(KEY);
  } catch (_) { return; }
  const group = id ? document.getElementById(id) : null;
  if (group?.matches("details.settings-group")) group.open = true;
}

if (!openFor(location.hash)) reopenAfterSave();
window.addEventListener("hashchange", () => openFor(location.hash));
for (const form of document.querySelectorAll(".settings-group form")) {
  form.addEventListener("submit", () => {
    try { sessionStorage.setItem(KEY, form.closest("details.settings-group")?.id || ""); } catch (_) { /* ignore */ }
  });
}

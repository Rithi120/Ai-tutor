/* Profile, avatar collection and leagues.
 *
 * Nothing here decides anything: unlocks are checked by POST /api/profile/avatar on the
 * server, and countdowns count down to an end time the server sent. The page only
 * shows what the server says.
 */
import { t } from "./i18n.js";

/* ---------------------------------------------------------------- countdowns */

function formatRemaining(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return t("countdownDays", { days, hours });
  if (hours > 0) return t("countdownHours", { hours, minutes });
  return t("countdownMinutes", { minutes: Math.max(1, minutes) });
}

function tickCountdowns() {
  document.querySelectorAll("[data-countdown]").forEach(element => {
    const end = Date.parse(element.dataset.countdown);
    if (Number.isNaN(end)) return;
    const label = element.dataset.countdownLabel || "{time}";
    element.textContent = label.replace("{time}", formatRemaining((end - Date.now()) / 1000));
  });
}

tickCountdowns();
window.setInterval(tickCountdowns, 30000);

/* ---------------------------------------------------------------- avatar collection */

const dialog = document.querySelector("#avatarPreview");
let previewed = null;

function openPreview(tile) {
  if (!dialog) return;
  previewed = tile;
  const unlocked = tile.dataset.unlocked === "true";
  const selected = tile.classList.contains("is-selected");
  const frame = dialog.querySelector("[data-preview-frame]");
  frame.style.setProperty("--avatar-accent", tile.style.getPropertyValue("--avatar-accent"));
  frame.classList.toggle("is-locked", !unlocked);
  const image = dialog.querySelector("[data-preview-img]");
  image.src = tile.dataset.url;
  image.alt = tile.dataset.name;
  const rarity = dialog.querySelector("[data-preview-rarity]");
  rarity.textContent = tile.dataset.rarity;
  rarity.className = `rarity-chip rarity-${tile.dataset.rarityKey}`;
  dialog.querySelector("[data-preview-name]").textContent = tile.dataset.name;
  dialog.querySelector("[data-preview-description]").textContent = tile.dataset.description;
  const lock = dialog.querySelector("[data-preview-lock]");
  lock.hidden = unlocked;
  lock.textContent = unlocked ? "" : t("avatarUnlocksAt", { level: tile.dataset.level });
  const select = dialog.querySelector("[data-preview-select]");
  select.hidden = !unlocked || selected;
  dialog.querySelector("[data-preview-status]").textContent = selected ? t("avatarInUse") : "";
  if (typeof dialog.showModal === "function") dialog.showModal();
  else dialog.setAttribute("open", "");
}

async function selectPreviewed() {
  if (!previewed) return;
  const status = dialog.querySelector("[data-preview-status]");
  const button = dialog.querySelector("[data-preview-select]");
  button.disabled = true;
  try {
    const response = await fetch("/api/profile/avatar", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ avatar: previewed.dataset.avatar }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || t("avatarSaveFailed"));
    document.querySelectorAll(".avatar-tile.is-selected").forEach(tile => {
      tile.classList.remove("is-selected");
      const meta = tile.querySelector(".avatar-tile-meta");
      if (meta) meta.textContent = tile.dataset.rarity;
    });
    previewed.classList.add("is-selected");
    const meta = previewed.querySelector(".avatar-tile-meta");
    if (meta) meta.textContent = t("avatarInUse");
    // Every place the page shows the current avatar follows the choice.
    document.querySelectorAll(".profile-hero .avatar-frame img, .profile-avatar img, .settings-profile-head img").forEach(image => {
      image.src = data.url;
    });
    document.querySelectorAll(".profile-hero, .profile-hero .avatar-frame").forEach(element => {
      element.style.setProperty("--avatar-accent", previewed.style.getPropertyValue("--avatar-accent"));
    });
    const name = document.querySelector(".profile-avatar-name");
    if (name) name.textContent = previewed.dataset.name;
    status.textContent = t("avatarSelected");
    button.hidden = true;
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

document.querySelectorAll(".avatar-tile").forEach(tile => tile.addEventListener("click", () => openPreview(tile)));
dialog?.querySelector("[data-preview-select]")?.addEventListener("click", selectPreviewed);
dialog?.addEventListener("click", event => {
  // A click on the backdrop closes the preview.
  if (event.target === dialog) dialog.close();
});

/* ---------------------------------------------------------------- league result */

document.querySelector("[data-dismiss-result]")?.addEventListener("click", async event => {
  event.currentTarget.closest("[data-league-result]")?.remove();
  try { await fetch("/api/leaderboard/result-seen", { method: "POST" }); } catch (_) { /* shown again next time */ }
});

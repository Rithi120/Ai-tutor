/* XP feedback shared by every learning surface.
 *
 * One entry point: announceXp(data, key). It shows a small "+N XP" pill, live-updates
 * the navbar chip (level, bar, total), celebrates a level-up, and - exactly once per
 * account+browser - introduces the XP system with a dismissible note. Duplicate events
 * (server `duplicate` flag, an already-seen key, or zero XP) update the chip quietly
 * but never notify again. Everything no-ops when gamification is off.
 */
import { t } from "./i18n.js";

const FLAGS = window.LEARNOVA_FLAGS || {};
const INTRO_KEY = "learnova:xpIntroSeen";
const seenKeys = new Set();
let pillTimer = 0;

function reducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function updateChip(xp) {
  const chip = document.querySelector("[data-xp-chip]");
  if (!chip || !xp) return 0;
  const previousLevel = Number(chip.dataset.xpChipLevel) || 0;
  chip.dataset.xpChipLevel = String(xp.level);
  const level = chip.querySelector("[data-xp-level]");
  const total = chip.querySelector("[data-xp-total]");
  const bar = chip.querySelector("[data-xp-bar]");
  if (level) level.textContent = String(xp.level);
  if (total) total.textContent = String(xp.total_xp);
  if (bar && xp.next_level_xp) {
    bar.style.width = `${Math.min(100, Math.round(100 * xp.current_level_xp / xp.next_level_xp))}%`;
  }
  if (!reducedMotion()) {
    chip.classList.remove("xp-chip-pulse");
    void chip.offsetWidth; // restart the animation
    chip.classList.add("xp-chip-pulse");
  }
  return previousLevel && xp.level > previousLevel ? xp.level : 0;
}

function showPill(text, levelUp) {
  let pill = document.querySelector("#xpToast");
  if (!pill) {
    pill = document.createElement("div");
    pill.id = "xpToast";
    pill.className = "xp-toast";
    pill.setAttribute("role", "status");
    pill.setAttribute("aria-live", "polite");
    document.body.appendChild(pill);
  }
  pill.textContent = text;
  pill.classList.toggle("xp-toast-levelup", Boolean(levelUp));
  pill.classList.remove("xp-toast-show");
  void pill.offsetWidth;
  pill.classList.add("xp-toast-show");
  window.clearTimeout(pillTimer);
  pillTimer = window.setTimeout(() => pill.classList.remove("xp-toast-show"), levelUp ? 3200 : 2200);
}

function introSeen() {
  try { return Boolean(window.localStorage.getItem(INTRO_KEY)); } catch (e) { return true; }
}

function maybeShowIntro() {
  if (introSeen() || document.querySelector("#xpIntro")) return;
  const note = document.createElement("aside");
  note.id = "xpIntro";
  note.className = "xp-intro";
  note.setAttribute("role", "note");
  const text = document.createElement("p");
  text.textContent = t("xpIntroBody");
  const actions = document.createElement("div");
  actions.className = "xp-intro-actions";
  const progress = document.createElement("a");
  progress.href = "/progress";
  progress.textContent = t("xpIntroProgress");
  const dismiss = document.createElement("button");
  dismiss.type = "button";
  dismiss.textContent = t("xpIntroDismiss");
  dismiss.addEventListener("click", () => {
    try { window.localStorage.setItem(INTRO_KEY, new Date().toISOString()); } catch (e) { /* private mode */ }
    note.remove();
  });
  actions.append(progress, dismiss);
  note.append(text, actions);
  document.body.appendChild(note);
}

export function announceXp(data, key) {
  if (!FLAGS.gamification_enabled || !data) return;
  const earned = Number(data.xp_earned || 0);
  const fresh = !data.duplicate && earned > 0 && (!key || !seenKeys.has(key));
  if (key) seenKeys.add(key);
  const levelUp = updateChip(data.xp);
  // A level-up is announced even when this call itself carried no new per-answer XP
  // (e.g. a completion bonus tipped the level); the chip's stored level fires it once.
  if (!fresh && !levelUp) return;
  showPill(levelUp ? t("xpLevelUp", { level: levelUp }) : t("xpGained", { xp: earned }), levelUp);
  maybeShowIntro();
}

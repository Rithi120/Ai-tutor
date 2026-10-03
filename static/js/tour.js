import { t } from "./i18n.js";
import { STATE_KEY, STEPS, advancesOn, isFinished, pageKey, parseState, resolveStep, shouldAutoStart } from "./tour-rules.js";

// The first-time walkthrough. It points a light at the real button, says what to tap, and
// moves on when the student taps it - across pages, because the state of the run lives in
// sessionStorage and every page re-reads it. The server remembers only one thing: whether
// this account has finished (or skipped) the tour, so it never opens by itself twice.

const root = document.getElementById("tourRoot");

if (root) {
  const page = root.dataset.tourPage || pageKey(location.pathname);
  const masks = Object.fromEntries([...root.querySelectorAll("[data-mask]")].map(node => [node.dataset.mask, node]));
  const spot = root.querySelector(".tour-spot");
  const panel = root.querySelector(".tour-panel");
  const icon = root.querySelector("#tourIcon");
  const title = root.querySelector("#tourTitle");
  const text = root.querySelector("#tourText");
  const progress = root.querySelector("#tourProgress");
  const nextButton = root.querySelector("[data-tour-next]");
  const PAD = 8;

  let index = null;        // stored position in STEPS, or null when no run is active
  let current = null;      // {index, step, detour} for what is on screen
  let targetNodes = [];
  let observer = null;
  let rafPending = false;

  function readState() {
    try { return parseState(sessionStorage.getItem(STATE_KEY)); } catch { return null; }
  }
  function writeState(value) {
    try {
      if (value === null) sessionStorage.removeItem(STATE_KEY);
      else sessionStorage.setItem(STATE_KEY, JSON.stringify({ index: value }));
    } catch { /* a private window just forgets the run on the next page */ }
  }

  function visible(node) {
    if (!(node instanceof HTMLElement)) return false;
    if (node.hidden || node.closest("[hidden], .hidden")) return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0 && getComputedStyle(node).visibility !== "hidden";
  }

  // The first selector with a visible match wins; for a step that spans two areas (goal
  // field plus the idea buttons) every visible match of that selector is lit together.
  function findTargets(step) {
    for (const selector of step.targets || []) {
      const nodes = [...document.querySelectorAll(selector)].filter(visible);
      if (nodes.length) return selector === ".goal-field" ? nodes.concat([...document.querySelectorAll(".prompt-ideas")].filter(visible)) : nodes;
    }
    return [];
  }

  function unionRect(nodes) {
    const rects = nodes.map(node => node.getBoundingClientRect());
    const left = Math.min(...rects.map(r => r.left)), top = Math.min(...rects.map(r => r.top));
    const right = Math.max(...rects.map(r => r.right)), bottom = Math.max(...rects.map(r => r.bottom));
    return { left: left - PAD, top: top - PAD, width: right - left + 2 * PAD, height: bottom - top + 2 * PAD };
  }

  function layout() {
    rafPending = false;
    if (!current?.step) return;
    const step = current.step;
    const mobileToggle = document.querySelector(".mobile-nav-toggle");
    let nodes = step.kind === "card" ? [] : findTargets(step);
    let copy = t(step.text);
    if (!nodes.length && step.nav && visible(mobileToggle)) {
      // The link lives behind the menu button on a phone: light the button first.
      nodes = [mobileToggle];
      copy = t("tourOpenMenu");
    }
    if (!nodes.length && step.kind !== "card") {
      // Nothing to point at yet (the lesson is still being written): wait quietly.
      root.hidden = true;
      targetNodes = [];
      return;
    }
    root.hidden = false;
    targetNodes = nodes;
    if (text) text.textContent = copy;
    if (icon) icon.textContent = step.icon || "";
    if (title) { title.textContent = step.title ? t(step.title) : ""; title.hidden = !step.title; }
    if (progress) progress.textContent = t("tourStepOf", { current: current.index + 1, total: STEPS.length });
    if (nextButton) {
      const label = step.kind === "card" ? t(step.button) : step.advance === "next" ? t("Next") : "";
      nextButton.hidden = !label;
      nextButton.textContent = label;
    }
    root.classList.toggle("is-card", !nodes.length);
    if (!nodes.length) {
      Object.values(masks).forEach(mask => mask.style.cssText = "");
      if (spot) spot.hidden = true;
      panel.style.cssText = "";
      return;
    }
    const rect = unionRect(nodes);
    const vw = window.innerWidth, vh = window.innerHeight;
    masks.top.style.cssText = `left:0;top:0;width:${vw}px;height:${Math.max(rect.top, 0)}px`;
    masks.bottom.style.cssText = `left:0;top:${rect.top + rect.height}px;width:${vw}px;height:${Math.max(vh - rect.top - rect.height, 0)}px`;
    masks.left.style.cssText = `left:0;top:${rect.top}px;width:${Math.max(rect.left, 0)}px;height:${rect.height}px`;
    masks.right.style.cssText = `left:${rect.left + rect.width}px;top:${rect.top}px;width:${Math.max(vw - rect.left - rect.width, 0)}px;height:${rect.height}px`;
    if (spot) {
      spot.hidden = false;
      spot.style.cssText = `left:${rect.left}px;top:${rect.top}px;width:${rect.width}px;height:${rect.height}px`;
    }
    // Phones: the panel is a bottom sheet (CSS). Wider screens: sit below the target, or
    // above it when there is no room below.
    if (vw >= 640) {
      const panelHeight = panel.offsetHeight || 180;
      const below = rect.top + rect.height + 14;
      const top = below + panelHeight <= vh - 12 ? below : Math.max(12, rect.top - panelHeight - 14);
      const left = Math.min(Math.max(12, rect.left), vw - panel.offsetWidth - 12);
      panel.style.cssText = `top:${top}px;left:${left}px`;
    } else {
      panel.style.cssText = "";
    }
  }

  function scheduleLayout() {
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(layout);
  }

  function show(position) {
    index = position;
    if (isFinished(position)) { finish(); return; }
    writeState(position);
    current = resolveStep(position, page);
    if (!current.step) { finish(); return; }
    if (current.step.kind !== "card") {
      const first = findTargets(current.step)[0];
      first?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
    layout();
    window.setTimeout(() => panel?.focus({ preventScroll: true }), 50);
  }

  function advance() {
    if (!current) return;
    if (current.detour) {
      // The nav step was shown as a detour: the click is about to change pages; keep the
      // stored index so the real step appears on arrival.
      writeState(current.index);
      return;
    }
    show(current.index + 1);
  }

  async function complete() {
    try {
      await fetch("/api/tour/complete", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    } catch { /* the next page load offers the tour again; nothing is lost */ }
  }

  function stop() {
    writeState(null);
    index = null;
    current = null;
    targetNodes = [];
    root.hidden = true;
    root.classList.remove("is-card");
    observer?.disconnect();
    observer = null;
  }

  function finish() {
    stop();
    complete();
  }

  function start() {
    observer = new MutationObserver(scheduleLayout);
    observer.observe(document.body, { attributes: true, childList: true, subtree: true, attributeFilter: ["class", "hidden", "aria-expanded", "style"] });
    show(readState() ?? 0);
  }

  function insideTarget(node) {
    return targetNodes.some(target => target.contains(node));
  }

  // The student's own actions move the tour on. Capture phase, so a click that navigates
  // away still updates the stored state first.
  document.addEventListener("click", event => {
    if (!current?.step || !(event.target instanceof Element)) return;
    if (root.contains(event.target)) return;
    // One of the "Try asking" ideas fills in subject and goal at once, so it completes both
    // of those steps in a single tap.
    if (event.target.closest(".prompt-ideas button") && ["subject", "goal"].includes(current.step.id)) {
      show(STEPS.findIndex(step => step.id === "build"));
      return;
    }
    const within = insideTarget(event.target);
    const textLength = (document.querySelector("#studyGoal")?.value || "").trim().length
      || (event.target.closest(".prompt-ideas button") ? 3 : 0);
    if (advancesOn(current.step, { type: "click", insideTarget: within, textLength })) {
      // A lit menu button only opens the menu; the real link appears next.
      if (event.target.closest(".mobile-nav-toggle") && !current.step.targets?.some(selector => event.target.closest(selector))) return;
      advance();
    }
  }, true);
  document.addEventListener("change", event => {
    if (current?.step && event.target instanceof Element && advancesOn(current.step, { type: "change", insideTarget: insideTarget(event.target) })) advance();
  }, true);
  document.addEventListener("input", event => {
    if (!current?.step || !(event.target instanceof HTMLTextAreaElement)) return;
    if (advancesOn(current.step, { type: "input", insideTarget: insideTarget(event.target), textLength: event.target.value.trim().length })) advance();
  }, true);

  nextButton?.addEventListener("click", () => { if (current?.step?.kind === "card" || current?.step?.advance === "next") advance(); });
  root.querySelectorAll("[data-tour-skip]").forEach(node => node.addEventListener("click", finish));
  // Account -> Tutorial (or the "New here?" link): always from the beginning.
  document.querySelectorAll("[data-tour-open]").forEach(node => node.addEventListener("click", event => {
    event.preventDefault();
    writeState(0);
    if (observer) show(0); else start();
  }));
  document.addEventListener("keydown", event => {
    if (event.key === "Escape" && !root.hidden) finish();
  });
  window.addEventListener("resize", scheduleLayout);
  window.addEventListener("scroll", scheduleLayout, true);

  const stored = readState();
  if (stored !== null || shouldAutoStart({ autostart: root.dataset.tourAutostart === "true", storedIndex: stored })) {
    window.setTimeout(start, 400);
  }
}

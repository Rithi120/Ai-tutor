// Pure rules for the first-time walkthrough. No DOM here, so the decisions that matter -
// which step shows on which page, when a step is skipped, what "next" means - can be tested
// on their own. tour.js does the pointing and listening.

export const TOUR_VERSION = "v2";
export const STATE_KEY = `learnova.tour.${TOUR_VERSION}`;

// Each step either points at something real on a page (`targets`, first visible wins) and
// waits for the student to use it (`advance`), or is a plain card (`kind: "card"`). A step
// with `nav: true` leads to its page from anywhere else and is skipped once the student is
// already there. `page` is where the step belongs; `text`/`title`/`button` are catalogue keys.
export const STEPS = [
  { id: "welcome", kind: "card", icon: "👋", title: "tourWelcomeTitle", text: "tourWelcomeText", button: "tourLetsGo" },
  { id: "go-new-lesson", page: "index", nav: true, icon: "✨", text: "tourGoNewLesson", advance: "click",
    targets: ['[data-tour-target="new-lesson"]', ".mobile-app-menu .nav-primary-action"] },
  { id: "subject", page: "index", icon: "📚", text: "tourSubject", advance: "change", targets: [".subject-picker"] },
  { id: "goal", page: "index", icon: "💭", text: "tourGoal", advance: "input", targets: [".goal-field", ".prompt-ideas"] },
  { id: "build", page: "index", icon: "🚀", text: "tourBuild", advance: "click", targets: [".begin-button"] },
  { id: "lesson", page: "index", icon: "📖", text: "tourLesson", advance: "next", targets: [".explanation-card"], waits: true },
  { id: "start-test", page: "index", icon: "✅", text: "tourStartTest", advance: "click", targets: ["#startTest"] },
  { id: "chat", page: "index", icon: "💬", text: "tourChat", advance: "next", targets: ["#chatToggle", "#chatPanel"] },
  { id: "go-overview", page: "dashboard", nav: true, icon: "🏠", text: "tourGoOverview", advance: "click",
    targets: ['[data-tour-target="overview"]', '.mobile-app-menu a[href="/dashboard"]'] },
  { id: "done", page: "dashboard", kind: "card", icon: "🎉", title: "tourDoneTitle", text: "tourDoneText", button: "tourFinish" },
];

export function pageKey(pathname) {
  const path = String(pathname || "/").replace(/\/+$/, "") || "/";
  if (path === "/") return "index";
  if (path === "/dashboard") return "dashboard";
  return "other";
}

// Which step to show for stored index `index` on page `page`.
//
// - A nav step whose page the student is already on is skipped (index moves forward).
// - A page-bound step shown on the wrong page becomes a *detour*: the nav step that leads to
//   its page is shown instead, and the stored index does not move - after the navigation the
//   student lands exactly where they were.
// - Past the end means the tour is over.
export function resolveStep(index, page, steps = STEPS) {
  let position = Math.max(0, Number(index) || 0);
  while (position < steps.length) {
    const step = steps[position];
    if (step.nav && step.page === page) { position += 1; continue; }
    if (step.page && step.page !== page) {
      const detour = steps.find(candidate => candidate.nav && candidate.page === step.page);
      return detour ? { index: position, step: detour, detour: true } : { index: position, step: null, detour: false };
    }
    return { index: position, step, detour: false };
  }
  return { index: position, step: null, detour: false };
}

export function isFinished(index, steps = STEPS) {
  return index >= steps.length;
}

// The tour opens by itself for an account that has never finished or skipped it, unless a
// run is already stored for this tab (then that run simply resumes).
export function shouldAutoStart({ autostart, storedIndex }) {
  return Boolean(autostart) && storedIndex === null;
}

// Does this event count as "the student did the thing" for the current step?
export function advancesOn(step, { type, insideTarget, textLength = 0 }) {
  if (!step || step.kind === "card") return false;
  switch (step.advance) {
    case "click": return type === "click" && insideTarget;
    case "change": return type === "change" && insideTarget;
    case "input": return (type === "input" && insideTarget && textLength >= 3) || (type === "click" && insideTarget && textLength >= 3);
    default: return false;
  }
}

export function parseState(raw) {
  if (raw == null || raw === "") return null;
  try {
    const value = JSON.parse(raw);
    const index = Number(value?.index);
    return Number.isInteger(index) && index >= 0 ? index : null;
  } catch {
    return null;
  }
}

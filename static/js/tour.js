import {
  DEFAULT_WAIT, ENDED_KEY, HOLD_MS, LEGACY_KEY, OFFER_KEY, PAYLOAD_KEY, PHONE_MAX, RUN_KEY,
  advancesOn, autoCandidates, bandScroll, clampRect, closeOnLeave, counter, displayMode, endStatus, fillText,
  firstIndex, fitRect, followUps, forViewport, isTall, leaveStatus, lightBand, mayAutoStart, nextIndex,
  offerDismissed, offerValue, onScreen, pageKey, parseAuto, parseEnded, parseRun, placePanel, prevIndex,
  resumeIndex, runFitsPage, safePath, samePlace, sectionOf, sectionSteps, sentence, serializeEnded, serializeRun,
  shouldOffer, skipTarget,
} from "./tour-rules.js";

// The guided tutorial (docs/TUTORIAL.md). It lights the real button, explains it in a
// small card with Previous / Next / Exit (or Skip), and moves on when the student presses
// Next or does the real thing. Two kinds of runs:
//   * the quick tour (one step per function, at its place in the navigation), offered to a
//     new account and started from the hub;
//   * a feature chapter: a few tips on one page. It starts by itself the first time its
//     anchor has been on screen for a moment (#tourRoot[data-tutorial-auto] lists the
//     chapters of this page the student has not skipped or finished). Another one may
//     follow on the same page load, but only for an anchor that came on screen after the
//     last run or the offer ended - through the student's own action (a first answer, a
//     lesson built in place) - and a few seconds later. The hub replays any chapter.
// A run lives in sessionStorage, so a reload continues it; the server keeps the step
// reached and the status (skipped / completed) per section for the hub.
//
// The steps come from the server (learnova/tutorial/registry.py), already filtered by what
// this student can use and translated: inline in #tourData on the first visit and on the
// hub, otherwise GET /api/tutorial/steps once per tab, cached under data-tutorial-sig.
// Nothing here knows a step text or a feature flag.

const root = document.getElementById("tourRoot");

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), '
  + 'select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])';
// A page overlay that closes itself on Escape: the tour leaves that Escape to it.
const OVERLAYS = [".mobile-app-menu.is-open", ".app-dropdown.is-open", "#asstSidebar.is-open", "#scannerModal:not(.hidden)"];
// Overlays that cover the page: no chapter starts by itself under them (the chat drawer is
// not one of them: opening the chat list is what starts "Your chats" on a phone).
const COVERING = [".mobile-app-menu.is-open", ".app-dropdown.is-open", "#scannerModal:not(.hidden)"];
// Learnova is writing something: no chapter starts by itself meanwhile.
const BUSY = ["#loading:not(.hidden)", "#asstStop:not([hidden])", '[aria-busy="true"]'];
const TEXT_FIELDS = 'input:not([type]), input[type="text"], input[type="search"], input[type="email"], textarea, [contenteditable="true"]';
const PAD = 6;
const TICK_MS = 600;
const AUTO_TICK_MS = 300;
// A step whose target matches nothing at all on the page is given up sooner.
const ABSENT_WAIT = 500;
// How long a run carried over from an earlier page waits for its chapter's anchor to exist.
const RESUME_WAIT_MS = 2000;
// The hub reloads when a run started there ends there (its cards then show the new state);
// this says which of its buttons gets the focus back afterwards.
const HUB_FOCUS_KEY = "learnova.tutorial.hubFocus";
// "keyboard" once the student used the keyboard in this tab: only then does the focused
// card title show its focus ring (a touch or mouse user never asked for one).
const INPUT_KEY = "learnova.tutorial.input";
// What may give the lit element its spoken name: interactive things and headings carry
// their own text; any other container only its label or its first heading (never the raw
// text of everything inside it).
const NAMED = "a, button, summary, label, legend, h1, h2, h3, h4, h5, h6";
const FIELDS = "input, select, textarea";

if (root) setUp();

function setUp() {
  const masks = Object.fromEntries([...root.querySelectorAll("[data-mask]")].map(node => [node.dataset.mask, node]));
  const spot = root.querySelector(".tour-spot");
  const panel = root.querySelector(".tour-panel");
  const progressLabel = root.querySelector("#tourProgress");
  const iconNode = root.querySelector("#tourIcon");
  const titleNode = root.querySelector("#tourTitle");
  const textNode = root.querySelector("#tourText");
  const noteNode = root.querySelector("#tourNote");
  const choicesNode = root.querySelector("#tourChoices");
  const prevButton = root.querySelector("[data-tour-prev]");
  const nextButton = root.querySelector("[data-tour-next]");
  const skipButton = root.querySelector("[data-tour-skip]");
  const announcer = root.querySelector("#tourAnnounce");
  const phoneLayout = window.matchMedia(`(max-width: ${PHONE_MAX}px)`);
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const page = root.dataset.tourPage || pageKey(location.pathname);
  const user = root.dataset.tutorialUser || "";

  let payload = null;
  let payloadRequest = null;
  let postQueue = Promise.resolve();

  let run = null;               // {section, step, auto}
  let section = null;           // the payload's section of the run
  let steps = [];
  let index = -1;
  let current = null;
  let direction = 1;            // -1 after Previous: a step that is not there is skipped backwards
  let skipped = new Set();      // steps of this run found missing (skipped silently)
  let absent = new Set();       // later steps whose element is not on the page at all (left out of the count)
  let shownIds = new Set();     // steps of this run really shown
  let runResumed = false;       // the run was carried over from an earlier page (sessionStorage)
  let offerOpen = false;
  let endedAt = -Infinity;      // when the last run or the offer ended on this page load
  let excluded = new Set();     // chapters whose anchor was on screen then: not on this page load
  let mode = "";                // what is on screen (tour-rules displayMode, or "offer")
  let stepStarted = 0;
  let contentKey = "";
  let layoutKey = "";
  let lit = [];                 // nodes inside the light
  let targets = [];             // the step's own target (the student's action counts here)
  let openerInfo = null;        // {container, holder, opener, text} in opener mode
  let stack = [];               // containers a step lit into: closed again when the next step is outside
  let returnFocusTo = null;
  let wantFocus = false;
  let observer = null;
  let ticker = 0;
  let frame = 0;
  let inputTimer = 0;
  let scrolledFor = "";
  let lastPosted = "";
  let autoList = [];            // chapters of this page that may start by themselves
  let autoTimer = 0;
  let lastTyped = -Infinity;
  const anchorSince = new Map();
  const described = new Map();
  const memory = new Map();

  // Every write to the tour's own nodes only when the value really changes: an idle tour
  // must not produce mutations (classList.remove and dataset writes always do).
  function setClass(node, name, on) {
    if (node.classList.contains(name) !== Boolean(on)) node.classList.toggle(name, Boolean(on));
  }
  function setData(key, value) {
    if (value === null || value === undefined) { if (key in root.dataset) delete root.dataset[key]; return; }
    if (root.dataset[key] !== value) root.dataset[key] = value;
  }
  function setHidden(node, hidden) {
    if (node.hidden !== Boolean(hidden)) node.hidden = Boolean(hidden);
  }

  // ------------------------------------------------------------------ storage

  function storageGet(key) {
    try {
      const value = window.sessionStorage.getItem(key);
      if (value !== null) return value;
    } catch { /* blocked: the run lives in memory for this page only */ }
    return memory.has(key) ? memory.get(key) : null;
  }
  function storageSet(key, value) {
    memory.set(key, value);
    try { window.sessionStorage.setItem(key, value); } catch { /* blocked or full */ }
  }
  function storageRemove(key) {
    memory.delete(key);
    try { window.sessionStorage.removeItem(key); } catch { /* blocked */ }
  }
  function storeRun() {
    if (run) storageSet(RUN_KEY, serializeRun(run, user));
  }

  // ------------------------------------------------------------------ server

  function inlinePayload() {
    const node = document.getElementById("tourData");
    if (!node) return null;
    try {
      const data = JSON.parse(node.textContent || "null");
      return data && Array.isArray(data.steps) ? data : null;
    } catch { return null; }
  }

  function cachedPayload(sig) {
    try {
      const value = JSON.parse(storageGet(PAYLOAD_KEY) || "null");
      return value && value.sig === sig && Array.isArray(value.data?.steps) ? value.data : null;
    } catch { return null; }
  }

  function loadPayload() {
    if (payload) return Promise.resolve(payload);
    if (payloadRequest) return payloadRequest;
    const sig = root.dataset.tutorialSig || "";
    const ready = inlinePayload() || cachedPayload(sig);
    if (ready) {
      payload = ready;
      if (sig && ready.sig === sig) storageSet(PAYLOAD_KEY, JSON.stringify({ sig, data: ready }));
      return Promise.resolve(payload);
    }
    payloadRequest = fetch("/api/tutorial/steps", { headers: { Accept: "application/json" } })
      .then(response => (response.ok ? response.json() : null))
      .catch(() => null)
      .then(data => {
        if (data && Array.isArray(data.steps)) {
          payload = data;
          storageSet(PAYLOAD_KEY, JSON.stringify({ sig: data.sig, data }));
        }
        return payload;
      });
    return payloadRequest;
  }

  // One request after the other, so "completed" can never arrive before the step before it.
  function post(url, body) {
    const send = () => fetch(url, {
      method: "POST", keepalive: true, body: JSON.stringify(body),
      headers: { "Content-Type": "application/json", Accept: "application/json" },
    }).then(response => (response.ok ? response.json() : null)).catch(() => null);
    const result = postQueue.then(send, send);
    postQueue = result.then(() => undefined, () => undefined);
    return result;
  }

  function saveStep(sectionId, stepId, status = "in_progress") {
    if (!sectionId || !stepId) return Promise.resolve(null);
    return post("/api/tutorial/progress", { section: sectionId, step: stepId, status });
  }

  // ------------------------------------------------------------------ finding things

  function shown(node) {
    if (!(node instanceof Element) || !node.isConnected) return false;
    if (node.closest("[hidden], .hidden, [inert]")) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return false;
    const style = window.getComputedStyle(node);
    if (style.visibility === "hidden" || style.display === "none") return false;
    let opacity = 1;
    for (let item = node; item && item !== document.documentElement; item = item.parentElement) {
      opacity *= Number(window.getComputedStyle(item).opacity);
      if (opacity <= 0.05) return false;
    }
    return true;
  }

  // A page element the tour may light: shown, and never one of the tour's own nodes.
  function visible(node) {
    return node instanceof Element && !root.contains(node) && shown(node);
  }

  function all(selector, scope = document) {
    if (!selector) return [];
    try { return [...scope.querySelectorAll(selector)]; } catch { return []; }
  }

  function closest(node, selector) {
    try { return node.closest(selector); } catch { return null; }
  }

  function firstVisible(selector) {
    return all(selector).find(visible) || null;
  }

  // The first selector with a visible match wins, and within it the first visible node.
  function findTargets(step) {
    for (const selector of step.targets) {
      const node = firstVisible(selector);
      if (node) return [node];
    }
    return [];
  }

  // Does the page have the step's target at all (shown or hidden)? A state in the selector
  // (":not(.hidden)") does not count: the element is there, only not shown yet.
  function existsAtAll(step) {
    return step.targets.some(selector => all(selector.replace(/:not\([^()]*\)/g, "")).some(node => !root.contains(node)));
  }

  function holderOf(node) {
    for (const container of payload?.containers || []) {
      const holder = closest(node, container.inside);
      if (holder) return { container, holder };
    }
    return null;
  }

  function isClosed(holder) {
    return holder.tagName === "DETAILS" ? !holder.open : !visible(holder);
  }

  // A target hidden in a closed menu, drawer, panel or <details>: its opener, following the
  // chain when the opener hides in another container (the ⋯ inside the phone drawer).
  function resolveOpener(node, depth = 0) {
    const found = holderOf(node);
    if (!found || !isClosed(found.holder)) return null;
    const { container, holder } = found;
    const openers = container.opener.startsWith(":scope") ? all(container.opener, holder) : all(container.opener);
    const opener = openers.find(visible) || openers[0];
    if (!opener) return null;
    if (visible(opener)) return { container, holder, opener, text: container.text };
    return depth < 3 ? resolveOpener(opener, depth + 1) : null;
  }

  function findOpener(step) {
    for (const selector of step.targets) {
      for (const node of all(selector)) {
        if (root.contains(node)) continue;
        const found = resolveOpener(node);
        if (found) return found;
      }
    }
    return null;
  }

  function remember(nodes) {
    for (const node of nodes) {
      let probe = node;
      for (let depth = 0; probe && depth < 4; depth += 1) {
        const found = holderOf(probe);
        if (!found) break;
        if (!stack.some(entry => entry.holder === found.holder)) stack.push(found);
        probe = found.holder.parentElement;
      }
    }
  }

  // The container an opener opens: closed again when a later step is outside it.
  function rememberOpened(info) {
    if (info && !stack.some(entry => entry.holder === info.holder)) stack.push({ container: info.container, holder: info.holder });
  }

  function closeContainer({ container, holder }) {
    if (!holder.isConnected || isClosed(holder)) return;
    if (holder.tagName === "DETAILS") { holder.open = false; return; }
    const closer = container.closer ? firstVisible(container.closer) : null;
    if (closer) { closer.click(); return; }
    if (!container.toggle) return;
    const byId = holder.id ? all(`[aria-controls="${CSS.escape(holder.id)}"][aria-expanded="true"]`)[0] : null;
    (byId || all(container.opener).find(node => node.getAttribute("aria-expanded") === "true"))?.click();
  }

  // The containers a step's target lives in, and the ones its openers live in (the chat's
  // ⋯ menu is opened from a row inside the phone drawer: the drawer stays open for it).
  function relatedHolders(step) {
    const holders = new Set();
    if (!step || step.kind === "card") return holders;
    let frontier = step.targets.flatMap(selector => all(selector)).filter(node => !root.contains(node));
    for (let depth = 0; depth < 4 && frontier.length; depth += 1) {
      const next = [];
      for (const node of frontier) {
        const found = holderOf(node);
        if (!found || holders.has(found.holder)) continue;
        holders.add(found.holder);
        if (!found.container.opener.startsWith(":scope")) next.push(...all(found.container.opener));
        if (found.holder.id) next.push(...all(`[aria-controls="${CSS.escape(found.holder.id)}"][aria-expanded="true"]`));
        if (found.holder.parentElement) next.push(found.holder.parentElement);
      }
      frontier = next;
    }
    return holders;
  }

  // Leaving for a step whose target is not in a container that was opened for an earlier
  // step closes that container again (innermost first).
  function closeContainersFor(step) {
    if (!stack.length) return;
    const nodes = step && step.kind !== "card" ? step.targets.flatMap(selector => all(selector)) : [];
    const related = relatedHolders(step);
    for (let position = stack.length - 1; position >= 0; position -= 1) {
      const entry = stack[position];
      if (!entry.holder.isConnected) { stack.splice(position, 1); continue; }
      const inside = related.has(entry.holder) || nodes.some(node => entry.holder.contains(node));
      if (!closeOnLeave(entry, inside)) continue;
      stack.splice(position, 1);
      closeContainer(entry);
    }
  }

  function pageDialogOpen() {
    return all("dialog[open]").some(dialog => !root.contains(dialog));
  }

  // ------------------------------------------------------------------ lighting

  function light(node) {
    node.setAttribute("data-tour-lit", "");
    const before = node.getAttribute("aria-describedby");
    described.set(node, before);
    if (!String(before || "").split(/\s+/).includes("tourText")) {
      node.setAttribute("aria-describedby", before ? `${before} tourText` : "tourText");
    }
  }
  function unlight(node) {
    node.removeAttribute("data-tour-lit");
    const before = described.get(node);
    if (before === null || before === undefined) node.removeAttribute("aria-describedby");
    else node.setAttribute("aria-describedby", before);
    described.delete(node);
  }
  function setLit(nodes) {
    if (nodes.length === lit.length && nodes.every((node, position) => node === lit[position])) return;
    lit.filter(node => !nodes.includes(node)).forEach(unlight);
    nodes.filter(node => !lit.includes(node)).forEach(light);
    lit = nodes;
  }

  function unionRect(nodes) {
    const rects = nodes.map(node => node.getBoundingClientRect());
    const left = Math.min(...rects.map(rect => rect.left)) - PAD;
    const top = Math.min(...rects.map(rect => rect.top)) - PAD;
    const right = Math.max(...rects.map(rect => rect.right)) + PAD;
    const bottom = Math.max(...rects.map(rect => rect.bottom)) + PAD;
    return { left, top, width: right - left, height: bottom - top };
  }

  function navHeight() {
    const value = parseFloat(window.getComputedStyle(document.documentElement).getPropertyValue("--app-nav-height"));
    return Number.isFinite(value) ? value : 64;
  }

  // Writes styles only when something moved: the body observer would otherwise see the
  // tour's own writes and lay it out again on every frame (the old 720 mutations a second).
  function writePositions(nodes) {
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    if (!nodes.length) {
      const key = `card|${vw}|${vh}`;
      if (key === layoutKey) return;
      layoutKey = key;
      setClass(root, "is-card", true);
      Object.values(masks).forEach(mask => { if (mask.style.cssText) mask.style.cssText = ""; });
      setHidden(spot, true);
      setClass(panel, "is-top", false);
      panel.style.cssText = "";
      return;
    }
    setClass(root, "is-card", false);
    const panelH = Math.ceil((panel.offsetHeight || 200) / 16) * 16;
    const clamped = clampRect(unionRect(nodes), vw, vh);
    const rect = fitRect(clamped, lightBand({ rect: clamped, vw, vh, panelW: 360, panelH, navH: navHeight() }));
    const place = placePanel({ rect, vw, vh, panelW: 360, panelH, navH: navHeight() });
    const round = value => Math.round(value);
    const key = [round(rect.left), round(rect.top), round(rect.width), round(rect.height), vw, vh, place.kind,
      round(place.left || 0), round(place.top || 0)].join("|");
    if (key === layoutKey) return;
    layoutKey = key;
    const top = round(rect.top);
    const bottom = round(rect.top + rect.height);
    const left = round(rect.left);
    const right = round(rect.left + rect.width);
    // Every edge is rounded once and shared by the masks on both sides of it, so no row
    // or column is left undimmed between them.
    masks.top.style.cssText = `left:0;top:0;width:${vw}px;height:${top}px`;
    masks.bottom.style.cssText = `left:0;top:${bottom}px;width:${vw}px;height:${Math.max(0, vh - bottom)}px`;
    const sideH = Math.max(0, bottom - top);
    masks.left.style.cssText = `left:0;top:${top}px;width:${left}px;height:${sideH}px`;
    masks.right.style.cssText = `left:${right}px;top:${top}px;width:${Math.max(0, vw - right)}px;height:${sideH}px`;
    setHidden(spot, false);
    spot.style.cssText = `left:${left}px;top:${top}px;width:${right - left}px;height:${sideH}px`;
    setClass(panel, "is-top", place.kind === "top");
    panel.style.cssText = place.kind === "float" ? `top:${round(place.top)}px;left:${round(place.left)}px` : "";
  }

  // ------------------------------------------------------------------ the panel

  function ctx() {
    return { page, isPhone: phoneLayout.matches, skip: absent.size ? new Set([...skipped, ...absent]) : skipped };
  }

  // Steps whose element is not on the page at all (shown or hidden) are skipped: the
  // counter and Finish leave them out already (they come back when they appear).
  function updateAbsent() {
    absent = new Set(steps.filter((step, position) => position !== index && step.kind !== "card" && !existsAtAll(step))
      .map(step => step.id));
  }

  function ui(key) {
    return payload?.ui?.[key] || "";
  }

  // The lit element's name for the announcement: its label, its own text when it is a
  // button, link or heading, a field's label, else the first heading inside it - never the
  // text of a whole container ("Rename or merge topics Topic name Rename ...").
  function accessibleName(node) {
    let label = node.getAttribute("aria-label") || "";
    if (!label && node.getAttribute("aria-labelledby")) {
      label = node.getAttribute("aria-labelledby").split(/\s+/)
        .map(id => document.getElementById(id)?.textContent || "").join(" ");
    }
    if (!label && node.matches(NAMED)) label = node.textContent || "";
    if (!label && node.matches(FIELDS)) label = node.labels?.[0]?.textContent || node.getAttribute("placeholder") || "";
    if (!label) label = node.getAttribute("title") || "";
    if (!label) {
      const heading = node.querySelector("h1, h2, h3, h4, h5, h6, legend, summary");
      label = heading ? heading.textContent || "" : "";
    }
    return label.replace(/\s+/g, " ").trim().slice(0, 80);
  }

  function isLast() {
    return nextIndex(steps, index, ctx()) >= steps.length;
  }

  // Everything the panel says for this step and mode, as data (compared, then written once).
  function model(info) {
    updateAbsent();
    const view = ctx();
    const count = counter(steps, index, view.isPhone, view.skip);
    const title = section?.title || "";
    const out = {
      progress: count ? `${title} · ` : title, count: count ? fillText(ui("stepOf"), count) : "",
      icon: current.icon || "", title: current.title, text: current.text, note: "", busy: false,
      choices: [], prev: prevIndex(steps, index, view) >= 0, next: isLast() ? ui("finish") : ui("next"),
      skip: section?.kind === "overview" ? ui("exit") : ui("skip"), said: "",
    };
    if (mode === "opener") out.text = info.opener.text;
    if (mode === "busy") out.text = ui("busy");
    if (mode === "card" && current.create) {
      const target = safePath(current.create[1]);
      if (target && !samePlace(target, location.href) && !(target === payload.hub && !payload.hub)) {
        out.choices.push({ kind: "button", action: "create", label: current.create[0], style: "secondary" });
      }
    }
    const where = out.count ? `${sentence(out.count)} ` : "";
    out.said = `${title}: ${where}${sentence(out.title)}`;
    if (mode === "spot" && lit[0]) {
      const name = accessibleName(lit[0]);
      if (name) out.said += ` ${fillText(ui("lit"), { name })}`;
    }
    return out;
  }

  function choiceNode(choice) {
    const node = document.createElement("button");
    node.type = "button";
    node.className = choice.style === "primary" ? "primary-button" : choice.style === "text" ? "tour-choice-text" : "secondary-button";
    node.dataset.tourAction = choice.action;
    node.textContent = choice.label;
    return node;
  }

  function writeContent(data) {
    const key = JSON.stringify([current?.id || "offer", mode, data]);
    if (key === contentKey) return false;
    contentKey = key;
    // "Section · Step n of N": the counter never breaks inside (a narrow phone wraps
    // between the two parts instead).
    const counterNode = document.createElement("span");
    counterNode.className = "tour-count";
    counterNode.textContent = data.count || "";
    progressLabel.replaceChildren(document.createTextNode(data.progress || ""), ...(data.count ? [counterNode] : []));
    iconNode.textContent = data.icon;
    titleNode.textContent = data.title;
    textNode.textContent = data.text;
    setHidden(textNode, !data.text);
    noteNode.textContent = data.note;
    noteNode.hidden = !data.note;
    if (data.busy) panel.setAttribute("aria-busy", "true"); else panel.removeAttribute("aria-busy");
    choicesNode.replaceChildren(...data.choices.map(choiceNode));
    choicesNode.hidden = !data.choices.length;
    prevButton.disabled = !data.prev;
    prevButton.setAttribute("aria-disabled", String(!data.prev));
    nextButton.textContent = data.next;
    if (data.skip && skipButton.textContent !== data.skip) skipButton.textContent = data.skip;
    return true;
  }

  function announce(text) {
    announcer.textContent = "";
    window.setTimeout(() => { announcer.textContent = text; }, 40);
  }

  function focusTitle() {
    window.setTimeout(() => { if (!root.hidden) titleNode.focus({ preventScroll: true }); }, 50);
  }

  function hideRoot() {
    setHidden(root, true);
    setClass(document.body, "tour-open", false);
  }

  function showRoot() {
    setHidden(root, false);
    setClass(document.body, "tour-open", true);
  }

  // ------------------------------------------------------------------ layout loop

  function scheduleLayout(refresh = false) {
    if (refresh) contentKey = "";
    if (frame) return;
    frame = window.requestAnimationFrame(() => { frame = 0; layout(); });
  }

  // A step whose target is not there (a Premium-only control, a list without rows) is
  // skipped silently: on, or back after Previous. Past the end the run is over.
  function skipMissing() {
    skipped.add(current.id);
    const target = skipTarget(steps, index, direction, ctx());
    if (target.index >= steps.length) { finish(); return; }
    goTo(target.index, { focus: wantFocus, direction: target.direction });
  }

  function layout() {
    if (offerOpen) { renderOffer(); return; }
    if (!run || !current) return;
    const view = ctx();
    if (!forViewport(current, view.isPhone)) { goTo(nextIndex(steps, index, view), { focus: false }); return; }
    const here = current.kind !== "card" && (current.page === "*" || current.page === view.page);
    const found = here ? findTargets(current) : [];
    const opener = here && !found.length ? findOpener(current) : null;
    const busy = here && !found.length && current.busy ? firstVisible(current.busy) : null;
    const absent = here && !found.length && !opener && !busy && !existsAtAll(current);
    const next = displayMode({
      step: absent ? { ...current, wait: Math.min(current.wait, ABSENT_WAIT) } : current, page: view.page,
      found: found.length > 0, openerFound: Boolean(opener), busyFound: Boolean(busy),
      elapsed: performance.now() - stepStarted, otherDialogOpen: pageDialogOpen(),
    });
    if (next === "hold") {
      if (!mode || mode === "paused") { hideRoot(); setData("tourMode", "hold"); }
      return;
    }
    if (next === "paused") {
      mode = "paused";
      setData("tourMode", mode);
      hideRoot();
      return;
    }
    if (next === "missing") { skipMissing(); return; }
    if (next === "pending") {
      // Waiting for an element that is not there yet: nothing is shown (no blurred page,
      // no text about a step that may never come).
      mode = "pending";
      setData("tourMode", mode);
      setLit([]);
      hideRoot();
      return;
    }
    if (next === "elsewhere") { stop({ restore: false }); return; }   // the run is left here; it stays in progress
    const resumed = mode === "paused";
    mode = next;
    setData("tourMode", mode);
    setData("tourStep", current.id);
    setData("tourSection", run.section);
    if (mode === "spot" || mode === "card") shownIds.add(current.id);
    const nodes = mode === "spot" ? found.concat(current.also.map(firstVisible).filter(Boolean))
      : mode === "opener" ? [opener.opener] : mode === "busy" ? [busy] : [];
    targets = mode === "spot" ? found : [];
    openerInfo = mode === "opener" ? opener : null;
    setLit([...new Set(nodes)]);
    if (mode === "spot" || mode === "opener") remember(lit);
    const scrollNow = mode === "spot" && scrolledFor !== current.id;
    if (scrollNow) scrolledFor = current.id;
    showRoot();
    setClass(root, "is-offer", false);
    const data = model({ opener });
    const changed = writeContent(data);
    if (scrollNow) scrollToLight();
    writePositions(lit);
    if (changed) {
      announce(data.said);
      if (wantFocus && !resumed && mode !== "pending") { wantFocus = false; focusTitle(); }
    }
  }

  // An element in a box that scrolls on its own, or in a fixed layer (the phone menu, the
  // chat drawer): the browser's own scrolling brings it into view.
  function scrollsInside(node) {
    for (let item = node.parentElement; item && item !== document.body; item = item.parentElement) {
      const style = window.getComputedStyle(item);
      if (style.position === "fixed" || style.position === "sticky") return true;
      if (/(auto|scroll|overlay)/.test(style.overflowY) && item.scrollHeight > item.clientHeight + 1) return true;
    }
    return false;
  }

  // A target taller than the free band is scrolled to its top (below the header: tour.css
  // gives [data-tour-lit] a scroll margin); any other one to the middle of the free band -
  // above a phone's bottom sheet, not the middle of the whole window.
  function scrollToLight() {
    if (!lit.length) return;
    const behavior = reducedMotion.matches ? "auto" : "smooth";
    const box = unionRect(lit);
    const band = lightBand({ rect: box, vw: window.innerWidth, vh: window.innerHeight, panelW: 360,
      panelH: panel.offsetHeight || 240, navH: navHeight() });
    const first = lit.reduce((top, node) => (node.getBoundingClientRect().top < top.getBoundingClientRect().top ? node : top), lit[0]);
    if (isTall(box, band)) { first.scrollIntoView({ block: "start", inline: "nearest", behavior }); return; }
    if (scrollsInside(first)) { first.scrollIntoView({ block: "center", inline: "nearest", behavior }); return; }
    const delta = bandScroll(box, band);
    if (Math.abs(delta) > 2) window.scrollBy({ top: delta, behavior });
  }

  // ------------------------------------------------------------------ running

  function clearInputTimer() {
    if (inputTimer) window.clearTimeout(inputTimer);
    inputTimer = 0;
  }

  function activate() {
    if (!observer) {
      observer = new MutationObserver(records => {
        if (records.every(record => root.contains(record.target))) return;
        scheduleLayout();
      });
      observer.observe(document.body, {
        attributes: true, childList: true, subtree: true,
        attributeFilter: ["class", "hidden", "aria-expanded", "style", "open", "inert", "aria-hidden"],
      });
    }
    // A slow tick for what no mutation reports (a hover, a CSS transition, a late font);
    // it writes nothing while nothing moved.
    if (!ticker) ticker = window.setInterval(() => scheduleLayout(), TICK_MS);
  }

  function goTo(position, { focus = true, direction: heading = 1 } = {}) {
    if (!run) return;
    const view = ctx();
    let target = Math.max(0, position);
    while (target < steps.length && !forViewport(steps[target], view.isPhone)) target += 1;
    if (target >= steps.length) { finish(); return; }
    direction = heading;
    index = target;
    current = steps[target];
    run.step = current.id;
    storeRun();
    if (lastPosted !== current.id) { lastPosted = current.id; saveStep(run.section, current.id); }
    closeContainersFor(current);
    clearInputTimer();
    stepStarted = performance.now();
    contentKey = "";
    layoutKey = "";
    targets = [];
    openerInfo = null;
    wantFocus = focus;
    const wait = Number.isFinite(current.wait) ? current.wait : DEFAULT_WAIT;
    [HOLD_MS + 20, ABSENT_WAIT + 20, wait + 20].forEach(delay => window.setTimeout(() => scheduleLayout(), delay));
    activate();
    layout();
  }

  function startRun(sectionId, stepId = "", { focusFrom = null, auto = false, focus = true, skipBefore = false,
    resumed = false } = {}) {
    if (!payload) return;
    const list = sectionSteps(payload, sectionId);
    const entry = sectionOf(payload, sectionId);
    if (!list.length || !entry) { stop({ restore: false }); return; }
    offerOpen = false;
    setClass(root, "is-offer", false);
    if (focusFrom instanceof HTMLElement && focusFrom !== document.body && !root.contains(focusFrom)) returnFocusTo = focusFrom;
    run = { section: sectionId, step: stepId || "", auto: Boolean(auto) };
    runResumed = Boolean(resumed);
    section = entry;
    steps = list;
    current = null;
    index = -1;
    skipped = new Set();
    absent = new Set();
    shownIds = new Set();
    scrolledFor = "";
    mode = mode === "paused" ? "" : mode;
    lastPosted = "";
    const order = Object.fromEntries(list.map(step => [step.id, step.order]));
    const start = stepId ? resumeIndex(steps, stepId, order) : firstIndex(steps, ctx());
    // A chapter that starts by itself begins at its first step that is on the page: the
    // ones before it (Start test, once the test runs) are left out of it.
    if (skipBefore) steps.slice(0, start).forEach(step => skipped.add(step.id));
    goTo(start, { focus });
  }

  function advance({ focus }) {
    if (!run) return;
    const target = nextIndex(steps, index, ctx());
    if (target >= steps.length) { finish(); return; }
    goTo(target, { focus });
  }

  // Finish: the section is done. A run in which nothing could be shown is not a finished
  // one: it ends quietly (a chapter that started by itself is then skipped, so it can
  // never start again and again).
  function finish({ restore = true, refresh = true } = {}) {
    if (run) {
      const last = steps[steps.length - 1];
      if (last && shownIds.size > 0) { saveStep(run.section, last.id, endStatus("finish")); addEnded(run.section); }
      // (A run carried over from another page that finds nothing here just ends: it was
      // seen there and stays in progress.)
      else if (run.auto && !runResumed && current) { saveStep(run.section, current.id, endStatus("skip")); addEnded(run.section); }
    }
    stop({ restore, refresh });
  }

  // Skip / Exit / Escape: skipped (the server keeps a finished section finished).
  function skip() {
    if (run && current) { saveStep(run.section, current.id, endStatus("skip")); addEnded(run.section); }
    stop();
  }

  // The student used the lit link or submit button of a step - did what it says - and the
  // page is about to change: the chapter is completed on its last step, else skipped, and
  // the run is over, so the next page's own chapter may start. The quick tour goes on on
  // the next page (unless it was its last step).
  function leaveRun() {
    const last = isLast();
    const status = leaveStatus({ kind: section?.kind, isLast: last });
    if (!status) { storeRun(); return; }
    saveStep(run.section, last ? steps[steps.length - 1].id : current.id, status);
    addEnded(run.section);
    stop({ restore: false, refresh: false });
  }

  function leavesPage(node) {
    const link = closest(node, "a[href]");
    if (link) {
      const href = link.getAttribute("href") || "";
      if (!href || href.startsWith("#") || /^javascript:/i.test(href) || link.target === "_blank" || link.hasAttribute("download")) return false;
      try {
        const url = new URL(link.href, location.href);
        return url.origin === location.origin && !samePlace(url.pathname + url.search, location.href);
      } catch { return false; }
    }
    const button = closest(node, 'button, input[type="submit"]');
    return Boolean(button && button.form && (button.type || "submit") === "submit");
  }

  function stop({ restore = true, refresh = true } = {}) {
    const ended = run;
    const offerWasOpen = offerOpen;
    run = null;
    section = null;
    steps = [];
    index = -1;
    current = null;
    offerOpen = false;
    mode = "";
    storageRemove(RUN_KEY);
    closeContainersFor(null);
    stack = [];
    setLit([]);
    targets = [];
    openerInfo = null;
    clearInputTimer();
    observer?.disconnect();
    observer = null;
    if (ticker) window.clearInterval(ticker);
    ticker = 0;
    // A follow-up waits only after something was really shown (or the offer was open).
    if ((ended && shownIds.size) || offerWasOpen) markEnded();
    hideRoot();
    setClass(root, "is-card", false);
    setClass(root, "is-offer", false);
    setData("tourStep", null);
    setData("tourSection", null);
    setData("tourMode", "off");
    contentKey = "";
    layoutKey = "";
    scrolledFor = "";
    if (ended && refresh && page === "tutorial") { refreshHub(ended); return; }
    if (restore) restoreFocus();
  }

  // A run that ends on the hub (Finish or Exit on one of its cards): the saved progress
  // goes first, then the hub is drawn again with the new state and the focus goes back to
  // the button the run started from.
  function refreshHub(ended) {
    storageSet(HUB_FOCUS_KEY, ended.section);
    reloadAfterPosts();
  }

  // Reload once every queued write has landed - also one queued after this was called
  // (Exit, then at once Mark as done), so the redrawn hub never misses it.
  function reloadAfterPosts() {
    const pending = postQueue;
    pending.then(() => { if (pending === postQueue) window.location.reload(); else reloadAfterPosts(); });
  }

  function restoreFocus() {
    const target = returnFocusTo;
    returnFocusTo = null;
    if (target && target.isConnected && visible(target)) { target.focus({ preventScroll: true }); return; }
    const active = document.activeElement;
    if (active && active !== document.body && active.isConnected && !root.contains(active) && shown(active)) return;
    const main = document.getElementById("main-content") || document.querySelector("main");
    if (!main) return;
    if (!main.hasAttribute("tabindex")) main.setAttribute("tabindex", "-1");
    main.focus({ preventScroll: true });
  }

  // ------------------------------------------------------------------ chapters that start by themselves

  function stopAuto() {
    if (autoTimer) window.clearInterval(autoTimer);
    autoTimer = 0;
  }

  // The sections ended in this tab (account-tagged): never started by themselves again,
  // even on a page drawn before the server heard of it.
  let endedIds = parseEnded(storageGet(ENDED_KEY), user);
  function addEnded(id) {
    if (!id || endedIds.has(id)) return;
    endedIds.add(id);
    storageSet(ENDED_KEY, serializeEnded(endedIds, user));
  }
  function forgetEnded(id) {
    if (id === "all") endedIds = new Set(); else endedIds.delete(id);
    storageSet(ENDED_KEY, serializeEnded(endedIds, user));
  }

  // A run or the offer ended: a follow-up chapter may come, but not one whose anchor is on
  // screen right now (that would be back to back), and only after FOLLOW_GAP_MS.
  function markEnded() {
    endedAt = performance.now();
    for (const item of autoList) {
      const node = firstVisible(item.section.anchor);
      if (node && onScreen(node.getBoundingClientRect(), window.innerWidth, window.innerHeight)) excluded.add(item.id);
    }
    anchorSince.clear();
  }

  // The first step of a chapter that is on the page now (a card counts); -1 when none is.
  function firstShowable(list) {
    const view = ctx();
    return list.findIndex(step => forViewport(step, view.isPhone)
      && (step.kind === "card" || findTargets(step).length > 0 || Boolean(findOpener(step))));
  }

  function anyVisible(selectors) {
    return selectors.some(selector => all(selector).some(visible));
  }

  // Every tick: which anchors are on screen, and since when; the first chapter (registry
  // order) whose anchor has been there long enough starts, if nothing speaks against it.
  function autoTick() {
    const open = followUps(autoList, endedIds, excluded);
    if (!open.length) { stopAuto(); return; }
    if (run || offerOpen) return;
    const now = performance.now();
    for (const item of open) {
      const node = firstVisible(item.section.anchor);
      const there = node && onScreen(node.getBoundingClientRect(), window.innerWidth, window.innerHeight);
      if (!there) anchorSince.delete(item.id);
      else if (!anchorSince.has(item.id)) anchorSince.set(item.id, now);
    }
    const blocked = { run, offerOpen, dialogOpen: pageDialogOpen() || anyVisible(COVERING),
      busy: anyVisible(BUSY), sinceTyping: now - lastTyped, sinceEnded: now - endedAt };
    const ready = open.find(item => anchorSince.has(item.id)
      && mayAutoStart({ ...blocked, visibleFor: now - anchorSince.get(item.id) }));
    if (!ready) return;
    // It starts at a step that is really there (never a "still loading" card first).
    const list = sectionSteps(payload, ready.id);
    const resumeAt = ready.step ? list.findIndex(step => step.id === ready.step) : -1;
    const first = resumeAt >= 0 ? resumeAt : firstShowable(list);
    if (first < 0) return;
    // Starting by itself never takes the focus out of what the student is doing; the card
    // is announced, and Tab reaches it.
    const active = document.activeElement;
    const idle = !active || active === document.body || active === document.documentElement;
    startRun(ready.id, list[first].id, { auto: true, focus: idle, focusFrom: idle ? null : active,
      skipBefore: resumeAt < 0 });
  }

  function watchAuto() {
    autoList = autoCandidates(payload, parseAuto(root.dataset.tutorialAuto), page);
    if (!followUps(autoList, endedIds, excluded).length || autoTimer) return;
    autoTimer = window.setInterval(autoTick, AUTO_TICK_MS);
  }

  // ------------------------------------------------------------------ the first-time offer

  function showOffer(focusFrom = null) {
    if (!payload) return;
    run = null;
    steps = [];
    current = null;
    offerOpen = true;
    if (focusFrom instanceof HTMLElement && focusFrom !== document.body && !root.contains(focusFrom)) returnFocusTo = focusFrom;
    contentKey = "";
    layoutKey = "";
    wantFocus = true;
    activate();
    renderOffer();
  }

  function renderOffer() {
    const offer = payload.ui.offer || {};
    mode = "offer";
    setData("tourMode", mode);
    setData("tourStep", null);
    setLit([]);
    showRoot();
    setClass(root, "is-offer", true);
    const data = {
      progress: "", icon: "👋", title: offer.title, text: offer.text, note: offer.question, busy: false,
      prev: false, next: ui("next"), skip: ui("exit"), said: offer.title,
      choices: [
        { kind: "button", action: "offer:quick", label: offer.quick, style: "primary" },
        { kind: "button", action: "offer:explore", label: offer.explore, style: "text" },
      ],
    };
    const changed = writeContent(data);
    writePositions([]);
    if (changed) {
      announce(`${offer.title}. ${offer.question}`);
      if (wantFocus) { wantFocus = false; focusTitle(); }
    }
  }

  // The first-time offer is settled for good (POST /api/tour/complete): by any choice on it,
  // and by starting anything from the hub.
  function settleOffer() {
    storageSet(OFFER_KEY, offerValue(user));
    if (root.dataset.tourAutostart === "true") {
      root.dataset.tourAutostart = "false";
      post("/api/tour/complete", {});
    }
  }

  // Any choice settles the offer, before anything else.
  function chooseOffer(choice) {
    settleOffer();
    offerOpen = false;
    markEnded();
    setClass(root, "is-offer", false);
    const from = returnFocusTo;
    if (choice === "quick") startRun("overview", "", { focusFrom: from });
    else stop();
  }

  // ------------------------------------------------------------------ the panel's buttons

  function runChoice(action) {
    if (action.startsWith("offer:")) { chooseOffer(action.slice(6)); return; }
    if (!run || !current) return;
    if (action === "create" && current.create) {
      const url = safePath(current.create[1]);
      if (!url) return;
      if (isLast()) finish({ restore: false, refresh: false });
      window.location.assign(url);
    }
  }

  choicesNode.addEventListener("click", event => {
    const node = event.target instanceof Element ? event.target.closest("[data-tour-action]") : null;
    if (!node) return;
    event.preventDefault();
    runChoice(node.dataset.tourAction);
  });

  nextButton.addEventListener("click", () => {
    if (!run || !current) return;
    if (mode === "opener" && openerInfo) {
      rememberOpened(openerInfo);
      const { opener, holder } = openerInfo;
      if (holder.tagName === "DETAILS" && opener.tagName !== "SUMMARY") holder.open = true;
      else opener.click();
      wantFocus = true;
      contentKey = "";
      scheduleLayout();
      return;
    }
    advance({ focus: true });
  });

  prevButton.addEventListener("click", () => {
    if (!run) return;
    const target = prevIndex(steps, index, ctx());
    if (target >= 0) goTo(target, { focus: true, direction: -1 });
  });

  skipButton.addEventListener("click", () => skip());

  // ------------------------------------------------------------------ the student's own actions

  function eventNode(event) {
    const node = event.target instanceof Element ? event.target : null;
    return node && !root.contains(node) ? node : null;
  }

  // The student's own action moved the tour on: the focus stays where they are, unless
  // that action took the focused element away (Start test hides itself) - then the card
  // title takes it, so a keyboard or screen-reader user does not land on the page body.
  function later(stepId, delay = 0) {
    window.setTimeout(() => {
      if (current?.id !== stepId) return;
      advance({ focus: false });
      window.setTimeout(() => {
        const active = document.activeElement;
        if (run && !root.hidden && (!active || active === document.body || !active.isConnected || !shown(active))) focusTitle();
      }, 80);
    }, delay);
  }

  // Capture phase, so a click that navigates away updates the stored run first.
  document.addEventListener("click", event => {
    if (!run || !current || root.hidden) return;
    const node = eventNode(event);
    if (!node) return;
    if (current.shortcut && closest(node, current.shortcut[0])) {
      const position = steps.findIndex(step => step.id === current.shortcut[1]);
      if (position > index) {
        window.setTimeout(() => { if (run) goTo(position, { focus: false }); }, 0);
        return;
      }
    }
    if (mode === "opener" && openerInfo?.opener.contains(node)) {
      rememberOpened(openerInfo);
      scheduleLayout();
      return;
    }
    if (!targets.some(target => target.contains(node))) return;
    if (advancesOn(current, { type: "click", insideTarget: true })) { later(current.id); return; }
    if (current.kind !== "action" && leavesPage(node)) leaveRun();
  }, true);

  document.addEventListener("change", event => {
    if (!run || !current || root.hidden) return;
    const node = eventNode(event);
    if (!node) return;
    const inside = targets.some(target => target.contains(node));
    const textLength = typeof node.value === "string" ? node.value.trim().length : 0;
    if (advancesOn(current, { type: "change", insideTarget: inside, textLength })) {
      clearInputTimer();
      later(current.id);
    }
  }, true);

  // Typing three characters is "doing it", but the light moves only after a short pause,
  // so it never jumps away mid-word (and focus stays in the field). Any typing also holds
  // back a chapter that would start by itself.
  document.addEventListener("input", event => {
    const node = eventNode(event);
    if (node && (node.matches(TEXT_FIELDS) || node.isContentEditable)) lastTyped = performance.now();
    if (!run || !current || root.hidden || current.advance !== "input") return;
    if (!node || typeof node.value !== "string" || !targets.some(target => target.contains(node))) return;
    clearInputTimer();
    if (!advancesOn(current, { type: "input", insideTarget: true, textLength: node.value.trim().length })) return;
    const stepId = current.id;
    inputTimer = window.setTimeout(() => { inputTimer = 0; if (current?.id === stepId) advance({ focus: false }); }, 1500);
  }, true);

  // ------------------------------------------------------------------ keyboard

  function cycle() {
    const inPanel = all(FOCUSABLE, panel).filter(shown);
    const inLit = lit.flatMap(node => [node, ...all(FOCUSABLE, node)])
      .filter(node => node.matches(FOCUSABLE) && visible(node));
    const radios = new Set();
    return [...new Set([...inPanel, ...inLit])].filter(node => {
      if (!(node instanceof HTMLInputElement) || node.type !== "radio" || !node.name) return true;
      // A radio group is one stop: its checked button (or its first).
      if (radios.has(node.name)) return false;
      const group = inLit.filter(item => item instanceof HTMLInputElement && item.type === "radio" && item.name === node.name);
      const chosen = group.find(item => item.checked) || group[0];
      if (chosen !== node) return false;
      radios.add(node.name);
      return true;
    });
  }

  function trapTab(event) {
    const items = cycle();
    if (!items.length) return;
    const at = items.indexOf(document.activeElement);
    const step = event.shiftKey ? -1 : 1;
    const next = at === -1 ? items[event.shiftKey ? items.length - 1 : 0] : items[(at + step + items.length) % items.length];
    event.preventDefault();
    event.stopImmediatePropagation();
    next.focus();
  }

  function onEscape(event) {
    const claim = () => { event.preventDefault(); event.stopImmediatePropagation(); };
    if (offerOpen) { claim(); chooseOffer("explore"); return; }
    if (pageDialogOpen()) return;
    // The chat's ⋯ menu listens for Escape only while it has focus; close it here.
    const menuButton = all('[aria-controls="asstItemMenu"][aria-expanded="true"]')[0];
    if (menuButton && document.querySelector("#asstItemMenu:not([hidden])")) { claim(); menuButton.click(); return; }
    if (OVERLAYS.some(selector => document.querySelector(selector))) return;   // the page closes its own overlay
    claim();
    skip();
  }

  // Registered at module evaluation: before navigation.js's bubble handler and before
  // assistant.js's capture handler, so the tour's Tab trap wins while it is visible.
  document.addEventListener("keydown", event => {
    if (root.hidden || (!run && !offerOpen)) return;
    if (event.key === "Tab") trapTab(event);
    else if (event.key === "Escape" || event.key === "Esc") onEscape(event);
  }, true);

  // ------------------------------------------------------------------ the page moving

  window.addEventListener("resize", () => scheduleLayout());
  window.addEventListener("scroll", () => scheduleLayout(), { capture: true, passive: true });
  document.addEventListener("transitionend", event => {
    if (!(event.target instanceof Node) || !root.contains(event.target)) scheduleLayout();
  }, true);
  const onLayoutChange = () => { if (run && current) goTo(index, { focus: false }); };
  if (phoneLayout.addEventListener) phoneLayout.addEventListener("change", onLayoutChange);

  // ------------------------------------------------------------------ the hub's buttons

  // templates/tutorial.html: [data-tutorial-start] runs the quick tour here;
  // a[data-tutorial-replay] is a real link to the chapter's page (its run is stored first,
  // so it starts on arrival); [data-tutorial-restart] / [data-tutorial-restart-all] start
  // again from nothing; [data-tutorial-done] marks a section as done.
  function replay(id, from, href, focusFrom) {
    forgetEnded(id);
    storageSet(RUN_KEY, serializeRun({ section: id, step: from || "" }, user));
    if (href && !samePlace(href, location.href)) { window.location.assign(href); return; }
    loadPayload().then(data => { if (data) startRun(id, from || "", { focusFrom }); });
  }

  document.addEventListener("click", event => {
    const link = event.target instanceof Element ? event.target.closest("a[data-tutorial-replay]") : null;
    if (!link || root.contains(link)) return;
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    settleOffer();
    replay(link.dataset.tutorialReplay, link.dataset.tutorialFrom || "", safePath(link.getAttribute("href")), link);
  });

  document.addEventListener("click", event => {
    const button = event.target instanceof Element
      ? event.target.closest("[data-tutorial-start], [data-tutorial-restart], [data-tutorial-restart-all], [data-tutorial-done]")
      : null;
    if (!button || root.contains(button)) return;
    event.preventDefault();
    // Restart tutorial resets every tip: the student says so first.
    if (button.hasAttribute("data-tutorial-restart-all") && button.dataset.tutorialConfirm
        && !window.confirm(button.dataset.tutorialConfirm)) return;
    settleOffer();
    loadPayload().then(data => {
      if (!data) return;
      if (button.hasAttribute("data-tutorial-start")) {
        startRun(button.dataset.tutorialStart, button.dataset.tutorialFrom || "", { focusFrom: button });
      } else if (button.hasAttribute("data-tutorial-restart")) {
        const id = button.dataset.tutorialRestart;
        forgetEnded(id);
        post("/api/tutorial/reset", { section: id }).then(() => {
          const link = document.querySelector(`a[data-tutorial-replay="${CSS.escape(id)}"]`);
          if (link) replay(id, "", safePath(link.getAttribute("href")), button);
          else if (sectionOf(data, id)?.page === "*") startRun(id, "", { focusFrom: button });
          else { storageSet(HUB_FOCUS_KEY, id); reloadAfterPosts(); }
        });
      } else if (button.hasAttribute("data-tutorial-restart-all")) {
        forgetEnded("all");
        post("/api/tutorial/reset", { section: "all" }).then(() => startRun("overview", "", { focusFrom: button }));
      } else if (button.hasAttribute("data-tutorial-done")) {
        const id = button.dataset.tutorialDone;
        const list = sectionSteps(data, id);
        const last = list[list.length - 1];
        if (last) {
          storageSet(HUB_FOCUS_KEY, id);
          saveStep(id, last.id, "completed");
          reloadAfterPosts();
        }
      }
    });
  });

  // Every entry point (the More and Account menus, the phone menu, the Overview, Settings,
  // the footer: [data-tutorial-link]) is a plain link to the hub, /tutorial.

  // ------------------------------------------------------------------ on load

  function whenReady(callback, delay) {
    const go = () => window.setTimeout(callback, delay);
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", go, { once: true });
    else go();
  }

  // Logging out (on a shared device) leaves nothing of this account's tutorial in the tab.
  document.addEventListener("submit", event => {
    const form = event.target instanceof HTMLFormElement ? event.target : null;
    if (!form || !/\/logout\/?$/.test(new URL(form.action, location.href).pathname)) return;
    [RUN_KEY, OFFER_KEY, PAYLOAD_KEY, HUB_FOCUS_KEY, INPUT_KEY, ENDED_KEY].forEach(storageRemove);
  }, true);

  function setInput(kind) {
    setData("tourInput", kind);
    if (storageGet(INPUT_KEY) !== kind) storageSet(INPUT_KEY, kind);
  }
  document.addEventListener("keydown", event => { if (!event.isComposing) setInput("keyboard"); }, true);
  document.addEventListener("pointerdown", () => setInput("pointer"), true);
  setData("tourInput", storageGet(INPUT_KEY) === "keyboard" ? "keyboard" : "pointer");

  storageRemove(LEGACY_KEY);
  setData("tourMode", "off");
  if (page === "tutorial") {
    const focusId = storageGet(HUB_FOCUS_KEY);
    storageRemove(HUB_FOCUS_KEY);
    if (focusId) {
      whenReady(() => {
        const card = document.querySelector(`[data-tutorial-section="${CSS.escape(focusId)}"]`);
        const group = card?.closest("details");
        if (group) group.open = true;
        card?.querySelector("[data-tutorial-start], a[data-tutorial-replay], [data-tutorial-restart], a")
          ?.focus({ preventScroll: false });
      }, 0);
    }
    window.addEventListener("pageshow", event => { if (event.persisted) window.location.reload(); });
  }
  // The "Show tips automatically" switch saves as soon as it changes (its Save button is
  // the fallback without JavaScript).
  document.querySelectorAll("form[data-tutorial-tips]").forEach(form => { form.dataset.live = "true"; });
  document.addEventListener("change", event => {
    const box = event.target instanceof HTMLInputElement ? event.target : null;
    const form = box?.closest("form[data-tutorial-tips]");
    if (box && form && box.name === "auto_tips") form.requestSubmit();
  });

  const stored = parseRun(storageGet(RUN_KEY), null, user);
  // The chapters of this page are watched in any case: one starts once nothing else runs.
  const watch = () => whenReady(() => loadPayload().then(data => { if (data) watchAuto(); }), 200);
  if (page !== "onboarding") {
    if (stored) {
      setData("tourMode", "loading");   // a run continues on this page (test hook; nothing is shown yet)
      whenReady(() => loadPayload().then(data => {
        if (!data) { setData("tourMode", "off"); return; }   // offline: the run waits for the next page
        const valid = parseRun(storageGet(RUN_KEY), data, user);
        // A chapter continues only on its own page; elsewhere it stays in progress, and
        // this page's own chapter may start.
        if (!valid || !runFitsPage(sectionOf(data, valid.section), page)) {
          storageRemove(RUN_KEY);
          setData("tourMode", "off");
          if (root.dataset.tutorialAuto) watchAuto();
          return;
        }
        // A chapter carried over from an earlier page goes on only where its anchor exists
        // (a reload of the same lesson, not the empty chat home): it is given a moment to be
        // drawn, else the run is left (still in progress) and this page's chapters may start.
        const entry = sectionOf(data, valid.section);
        const anchor = entry.kind === "chapter" ? entry.anchor.replace(/:not\([^()]*\)/g, "") : "";
        const began = performance.now();
        const resume = () => {
          if (run || offerOpen) return;
          if (anchor && !all(anchor).some(node => !root.contains(node))) {
            if (performance.now() - began < RESUME_WAIT_MS) { window.setTimeout(resume, 150); return; }
            storageRemove(RUN_KEY);
            setData("tourMode", "off");
            if (root.dataset.tutorialAuto) watchAuto();
            return;
          }
          startRun(valid.section, valid.step, { auto: valid.auto, resumed: true });
          if (root.dataset.tutorialAuto) watchAuto();
        };
        resume();
      }), 120);
    } else if (shouldOffer({ autostart: root.dataset.tourAutostart === "true", page, run: stored,
      dismissed: offerDismissed(storageGet(OFFER_KEY), user) })) {
      whenReady(() => loadPayload().then(data => {
        if (data && !run && !offerOpen) showOffer(document.activeElement);
        if (data && root.dataset.tutorialAuto) watchAuto();
      }), 400);
    } else if (root.dataset.tutorialAuto) {
      watch();
    }
  }
}

/* Learnova's chat assistant - the home page of a signed-in student.
 *
 * Assistant output is untrusted text: the shared escape-first renderer in markdown.js
 * turns it into the small set of tags that module builds itself, math.js typesets the
 * formulas and code-highlight.js colours code from its text, so nothing the model writes
 * can become markup. Titles, snippets, tap answers and lesson names are set with
 * textContent.
 *
 * What the page keeps track of (and why):
 * - one send per conversation at a time, keyed by the conversation (or "draft" before
 *   the first message created it). The guard is set before anything is awaited, so a
 *   double Enter or Enter plus a tap sends once and creates one conversation;
 * - every message carries a client id; after a lost connection the page asks the server
 *   whether it was stored before it offers the text back, so a retry never doubles it;
 * - a reply that arrives after the student opened another conversation goes to its own
 *   thread (the list row updates; the open thread is never written into);
 * - opening a conversation cancels the previous open, and the address bar follows
 *   (/assistant/c/<id>), so a refresh comes back to the same conversation;
 * - drafts are kept per conversation; photos travel with the message and come back to
 *   the tray whenever it was not stored (403/409/413/503/504...).
 * Pure decisions live in assistant-rules.js.
 */
import { escapeHtml, safeUUID } from "./dom.js";
import { renderMarkdown } from "./markdown.js";
import { withRetryHint } from "./ai-limit-rules.js";
import { renderMath } from "./math.js";
import { highlightCode } from "./code-highlight.js";
import { t } from "./i18n.js";
import { createAttachmentTray, photoNotesNode, sentPhotosNode } from "./image-attach.js";
import {
  continueView, failureKey, groupRows, isNearBottom, menuIndex, mergeRows, newestReply, prefillText,
  retryAfterSeconds, sendOutcome, upsertRow,
} from "./assistant-rules.js";

const SIDEBAR_KEY = "learnova:asst-sidebar";
const WAITING_MAX = 12;
const LESSON_POLL_MS = 3000;
const LESSON_POLL_LIMIT_MS = 120000;
const BUSY_POLL_LIMIT_MS = 150000;
// Below this visible height, typing compacts the page (see trackViewport).
const KEYBOARD_COMPACT_HEIGHT = 500;
const SVG_NS = "http://www.w3.org/2000/svg";
// Constant icon shapes (never data); the template draws the same set for its own buttons.
const ICONS = {
  dots: '<circle class="asst-icon-fill" cx="5.5" cy="12" r="1.7"/><circle class="asst-icon-fill" cx="12" cy="12" r="1.7"/><circle class="asst-icon-fill" cx="18.5" cy="12" r="1.7"/>',
  copy: '<rect x="9" y="9" width="11.5" height="11.5" rx="2.5"/><path d="M5.5 15H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v.5"/>',
  regenerate: '<path d="M20 11.5A8 8 0 1 1 17.7 6"/><path d="M20 4v4.5h-4.5"/>',
  book: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5Z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20v3H6.5"/>',
  camera: '<path d="M14.5 4h-5L7.5 7H4.5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h15a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3Z"/><circle cx="12" cy="13" r="3.5"/>',
};

const page = document.querySelector(".assistant-page");
const el = (id) => document.getElementById(id);
const narrowScreen = window.matchMedia("(max-width: 900px)");
const coarsePointer = window.matchMedia("(pointer: coarse)");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

const state = {
  lists: { recent: [], archived: [] },
  cursors: { recent: null, archived: null },
  listControllers: { recent: null, archived: null },
  loaded: { recent: false, archived: false },
  activeId: null,
  active: null,            // the open conversation as the server sent it, with its messages
  sending: new Map(),      // key (conversation id or "draft") -> the send in flight
  drafts: new Map(),       // key -> unsent text
  // key -> {text, clientId} of a message given back after a failed send: sending the same
  // text again reuses its client id, so a message the server did store is replayed, not doubled.
  resend: new Map(),
  openController: null,
  openSeq: 0,
  search: "",
  searchTimer: null,
  archivedOpen: false,
  tray: null,
  menu: null,              // {button, conversation, archived} while the row menu is open
  dialogFor: null,         // {conversation, archived, returnTo}
  drawerReturn: null,
  stickToBottom: true,
  busyPolls: new Map(),    // conversation id -> timer, while the server is still answering
  lessonBusy: new Set(),   // conversation ids with a Continue Learning request in flight
  lessonPolls: new Map(),
  lessonUi: new Map(),     // conversation id -> {picker, note: {text, error, link}}
  waitingChecked: false,
  welcome: null,
  baseTitle: document.title,
};

/* -------------------------------------------------------------------- helpers */

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("class", "asst-icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.innerHTML = ICONS[name] || "";
  return svg;
}

function conversationUrl(id) {
  return `/assistant/c/${encodeURIComponent(id)}`;
}

function apiUrl(id, suffix = "") {
  return `/api/assistant/conversations/${encodeURIComponent(id)}${suffix}`;
}

function activeKey() {
  return state.activeId || "draft";
}

function browserZone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch (_) {
    return "";
  }
}

let toastTimer = null;
function toast(message, tone = "info", action = null) {
  const node = el("asstToast");
  if (!node) return;
  const text = document.createElement("span");
  text.className = "asst-toast-text";
  text.textContent = message;
  node.replaceChildren(text);
  if (action) {
    const control = document.createElement(action.href ? "a" : "button");
    control.className = "asst-toast-action";
    control.textContent = action.label;
    if (action.href) {
      control.href = action.href;
    } else {
      control.type = "button";
      control.addEventListener("click", () => {
        node.classList.add("hidden");
        action.onClick?.();
      });
    }
    node.appendChild(control);
  }
  // A failure interrupts (alert); anything else waits its turn (status).
  node.setAttribute("role", tone === "error" ? "alert" : "status");
  node.className = `toast ${tone}`;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => node.classList.add("hidden"), action ? 9000 : 4500);
}

/* What a screen reader hears when a reply arrives: one short sentence in a status region.
 * The thread itself is not a live region - it used to announce the student's own echo, the
 * waiting dots and every redraw of the buttons under the newest reply. */
function announce(message) {
  const node = el("asstAnnounce");
  if (!node) return;
  node.textContent = "";
  window.setTimeout(() => { node.textContent = message; }, 60);
}

/* Formulas, tables and code that are wider than the column scroll sideways inside the
 * message. Only those that really scroll become focusable (and are named), so a keyboard
 * can scroll them; a table keeps its table role. */
const SCROLLERS = ".asst-body :is(.ln-math, .katex-display, table, pre.asst-code)";
let scrollersFrame = 0;
function markScrollers() {
  window.cancelAnimationFrame(scrollersFrame);
  scrollersFrame = window.requestAnimationFrame(() => {
    for (const node of el("asstThread").querySelectorAll(SCROLLERS)) {
      const scrolls = node.scrollWidth > node.clientWidth + 1;
      if (scrolls && !node.dataset.scroller) {
        node.dataset.scroller = "1";
        node.tabIndex = 0;
        const kind = node.matches("table") ? "asstScrollTable" : node.matches("pre") ? "asstScrollCode" : "asstScrollFormula";
        node.setAttribute("aria-label", t(kind));
        if (!node.matches("table")) node.setAttribute("role", node.matches(".ln-math:not(.ln-math-display)") ? "group" : "region");
      } else if (!scrolls && node.dataset.scroller) {
        delete node.dataset.scroller;
        node.removeAttribute("tabindex");
        node.removeAttribute("aria-label");
        if (!node.matches("table")) node.removeAttribute("role");
      }
    }
  });
}

/* An API call. JSON errors carry the server's own translated sentence; anything else
 * (an expired session redirected to the login page, a proxy's 413 or 502 page, no
 * network) is worded by status - never a JavaScript exception text. */
async function api(url, { method = "GET", body, signal } = {}) {
  const options = { method, headers: { Accept: "application/json" }, signal, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw Object.assign(new Error(t(failureKey({ network: true }))), { status: 0, network: true });
  }
  const toLogin = response.redirected && /\/login(?:[/?#]|$)/.test(new URL(response.url, location.href).pathname);
  let data = null;
  if ((response.headers.get("Content-Type") || "").includes("json")) {
    try { data = await response.json(); } catch (_) { data = null; }
  }
  if (toLogin || (response.status === 401 && !data?.error)) {
    throw Object.assign(new Error(t(failureKey({ toLogin: true }))), { status: 401, code: "login_required", login: true });
  }
  if (!data || typeof data !== "object") {
    throw Object.assign(new Error(t(failureKey({ status: response.status }))), { status: response.status });
  }
  if (!response.ok || data.ok === false) {
    const error = Object.assign(new Error(data.error || t("asstRequestFailed")), {
      status: response.status, code: data.code || "", details: data.details || {},
      retryAfter: Number(response.headers.get("Retry-After")) || undefined,
      login: response.status === 401,
    });
    error.message = withRetryHint(error.message, error);
    throw error;
  }
  data.httpStatus = response.status;
  return data;
}

/* What to show for a failed request: the message, plus "Log in" or "See Premium" when that
 * is the way forward. */
function showError(error) {
  if (error?.login) {
    toast(error.message, "error", { label: t("asstLoginAgain"), href: `/login?next=${encodeURIComponent(location.pathname)}` });
  } else if (error?.details?.upgrade_url) {
    toast(error.message, "error", { label: t("asstSeePremium"), href: error.details.upgrade_url });
  } else {
    toast(error?.message || t("asstRequestFailed"), "error");
  }
}

/* ------------------------------------------------------------------ messages */

function messageNode(message, { imageNotes = null } = {}) {
  const article = document.createElement("article");
  article.className = `asst-msg asst-msg-${message.role === "user" ? "user" : "assistant"}`;
  if (message.error) {
    article.classList.add("asst-msg-error");
    if (message.error_code === "image_unreadable") article.classList.add("asst-msg-notice");
  }
  article.dataset.id = message.id;

  // Who said it is carried by the shape - a bubble on the right, plain prose with a
  // brand dot on the left - rather than by an uppercase label over every message. The
  // name stays in the document for anyone reading with a screen reader.
  const who = document.createElement("p");
  who.className = "visually-hidden";
  who.textContent = message.role === "user" ? t("asstYou") : t("asstAssistant");
  article.appendChild(who);
  if (message.role !== "user") {
    const avatar = document.createElement("span");
    avatar.className = "asst-avatar";
    avatar.setAttribute("aria-hidden", "true");
    article.appendChild(avatar);
  }

  const body = document.createElement("div");
  body.className = "asst-body";
  if (message.role === "user") {
    // The learner's own text is shown verbatim, not interpreted as markup.
    body.textContent = message.content;
  } else {
    body.innerHTML = renderMarkdown(message.content);
  }
  article.appendChild(body);
  if (message.attachments?.length) article.appendChild(sentPhotosNode(message.attachments));
  // What could not be read from the photos goes in before the formulas are typeset, so
  // a reading like "4^5" is shown the way the reply shows it.
  const notes = imageNotes ? photoNotesNode(imageNotes) : null;
  if (notes) article.appendChild(notes);
  if (message.context_dropped) {
    const meta = document.createElement("p");
    meta.className = "asst-msg-meta";
    meta.textContent = t("asstContextTrimmed").replace("{count}", message.context_dropped);
    article.appendChild(meta);
  }
  if (message.role === "assistant" && !message.error && message.id !== "pending") {
    const actions = document.createElement("div");
    actions.className = "asst-actions";
    const copy = actionButton("copy", t("asstCopy"), () => copyText(message.content));
    copy.dataset.tourTarget = "chat-copy";   // stable hooks for the tutorial (learnova/tutorial/registry.py)
    actions.appendChild(copy);
    article.appendChild(actions);
  }
  renderMath(body);
  if (notes) renderMath(notes);
  if (message.role !== "user") highlightCode(body);
  return article;
}

function pendingNode(withPhotos = false) {
  const article = document.createElement("article");
  article.className = "asst-msg asst-msg-assistant asst-pending";
  // With photos the wait is mostly the reading; saying so beats three silent dots.
  const label = withPhotos ? `<span class="asst-pending-label">${escapeHtml(t("imgReading"))}</span>` : "";
  article.innerHTML =
    `<p class="visually-hidden">${escapeHtml(t("asstAssistant"))}</p>` +
    `<span class="asst-avatar" aria-hidden="true"></span>` +
    `<div class="asst-body">${label}<span class="asst-dots"><i></i><i></i><i></i></span></div>`;
  article.setAttribute("aria-busy", "true");
  return article;
}

/* Where a stopped reply was: said honestly - the server finishes and stores it anyway. */
function stoppedNode() {
  const article = document.createElement("article");
  article.className = "asst-msg asst-msg-assistant asst-stopped-msg";
  const who = document.createElement("p");
  who.className = "visually-hidden";
  who.textContent = t("asstAssistant");
  const avatar = document.createElement("span");
  avatar.className = "asst-avatar";
  avatar.setAttribute("aria-hidden", "true");
  const note = document.createElement("p");
  note.className = "asst-stopped asst-body";
  note.textContent = t("asstStoppedNote");
  article.append(who, avatar, note);
  return article;
}

function actionButton(iconName, label, handler, { text = false, strong = false } = {}) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `asst-action${strong ? " asst-action-strong" : ""}`;
  if (iconName) button.appendChild(icon(iconName));
  if (text) {
    const span = document.createElement("span");
    span.textContent = label;
    button.appendChild(span);
  } else {
    button.setAttribute("aria-label", label);
    button.title = label;
  }
  button.addEventListener("click", handler);
  return button;
}

function copyText(text) {
  const done = () => toast(t("asstCopied"), "success");
  const failed = () => toast(t("asstCopyFailed"), "error");
  if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, failed);
  else failed();
}

function articleFor(id) {
  if (id === null || id === undefined) return null;
  return el("asstThread").querySelector(`.asst-msg[data-id="${CSS.escape(String(id))}"]`);
}

function welcomeNode() {
  return state.welcome ? state.welcome.cloneNode(true) : document.createElement("div");
}

/* Copy and the newest reply's extras (regenerate or retry, tap answers, Continue
 * Learning) are drawn from the open conversation's data; redrawn whenever it changes. */
function decorate() {
  const thread = el("asstThread");
  thread.querySelectorAll(".asst-deco").forEach((node) => node.remove());
  // The tutorial lights "the newest answer": only one article carries the hook.
  thread.querySelectorAll('[data-tour-target="chat-reply"]').forEach((node) => node.removeAttribute("data-tour-target"));
  const conversation = state.active;
  if (!conversation || state.sending.has(conversation.id)) return;
  const messages = conversation.messages || [];
  const newest = newestReply(messages);
  const article = newest ? articleFor(newest.id) : null;
  if (newest && article) {
    if (newest.error) {
      const row = document.createElement("div");
      row.className = "asst-actions asst-actions-pills asst-deco";
      let retry;
      if (newest.error_code === "image_unreadable" && state.tray) {
        // Retrying cannot read the same photo; a new one can.
        retry = actionButton("camera", t("asstNewPhoto"), choosePhoto, { text: true, strong: true });
      } else {
        retry = actionButton("regenerate", t("asstRetry"), () => regenerate(newest), { text: true, strong: true });
      }
      retry.dataset.tourTarget = "chat-retry";
      row.appendChild(retry);
      article.appendChild(row);
    } else {
      article.dataset.tourTarget = "chat-reply";
      const row = article.querySelector(".asst-actions");
      const again = actionButton("regenerate", t("asstRegenerate"), () => regenerate(newest));
      again.classList.add("asst-deco");
      again.dataset.tourTarget = "chat-regenerate";
      row?.appendChild(again);
      if (Array.isArray(newest.chips) && newest.chips.length) article.insertBefore(chipsNode(newest.chips), row);
    }
  }
  const lessonState = conversation.continue_learning;
  if (page.dataset.continueLearning === "on" && lessonState) {
    const source = articleFor(lessonState.source_message_id);
    const node = source ? continueNode(conversation) : null;
    if (node) source.insertBefore(node, source.querySelector(".asst-actions"));
    for (const lesson of lessonState.lessons || []) {
      if (lesson.source_message_id === lessonState.source_message_id || !lesson.url) continue;
      const older = articleFor(lesson.source_message_id);
      if (older) older.insertBefore(lessonLinkNode(lesson.url, lesson.title), older.querySelector(".asst-actions"));
    }
  }
}

function chipsNode(chips) {
  const box = document.createElement("div");
  box.className = "asst-chips asst-deco";
  box.dataset.tourTarget = "chat-chips";
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", t("asstTapAnswer"));
  for (const label of chips.slice(0, 3)) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "asst-chip";
    chip.textContent = label;   // textContent: a model-written label can never become markup
    chip.addEventListener("click", () => send(label, { fromChip: true }));
    box.appendChild(chip);
    renderMath(chip);
  }
  return box;
}

/* ------------------------------------------------------- Continue Learning */

function lessonLinkNode(url, title) {
  const box = document.createElement("div");
  box.className = "asst-continue asst-deco";
  box.dataset.tourTarget = "chat-continue";
  const link = document.createElement("a");
  link.className = "asst-continue-link";
  link.href = url;
  link.append(icon("book"), document.createTextNode(t("asstOpenLesson", { title: title || t("asstContinueGeneric") })));
  box.appendChild(link);
  return box;
}

function continueNode(conversation) {
  const id = conversation.id;
  const view = continueView(conversation.continue_learning, { busy: state.lessonBusy.has(id) });
  const ui = state.lessonUi.get(id) || {};
  if (view.kind === "ready") return lessonLinkNode(view.url, view.title);
  if (view.kind === "none" && !ui.note) return null;
  const box = document.createElement("div");
  box.className = "asst-continue asst-deco";
  box.dataset.tourTarget = "chat-continue";
  box.dataset.conversation = id;
  if (view.kind === "guidance" && !ui.note) {
    // Too little to build a lesson from yet: say what would make one possible.
    const note = document.createElement("p");
    note.className = "asst-continue-note asst-continue-guidance";
    note.append(icon("book"), document.createTextNode(view.text));
    box.appendChild(note);
    return box;
  }
  if (view.kind === "pending") {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "asst-continue-button";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    const spinner = document.createElement("span");
    spinner.className = "asst-spinner";
    spinner.setAttribute("aria-hidden", "true");
    button.append(spinner, document.createTextNode(t("asstBuildingLesson")));
    box.appendChild(button);
    if (!state.lessonBusy.has(id)) pollLesson(id);   // built elsewhere (another tab, a reload)
    return box;
  }
  if (ui.note) {
    const note = document.createElement("p");
    note.className = `asst-continue-note${ui.note.error ? " is-error" : ""}`;
    note.setAttribute("role", ui.note.error ? "alert" : "status");
    note.textContent = ui.note.text;
    box.appendChild(note);
    if (ui.note.link) {
      // A used-up allowance is information with a way forward, not an error.
      const link = document.createElement("a");
      link.className = "asst-continue-premium";
      link.href = ui.note.link.href;
      link.textContent = ui.note.link.label;
      box.appendChild(link);
    }
  }
  // A used-up allowance is not fixed by trying again today: the note links to Premium instead.
  if (view.kind !== "button" || ui.note?.link) return box;
  if (ui.picker && view.subjects.length) {
    const label = document.createElement("p");
    label.className = "asst-continue-note";
    label.textContent = t("asstPickSubject");
    const chips = document.createElement("div");
    chips.className = "asst-chips";
    chips.setAttribute("role", "group");
    chips.setAttribute("aria-label", t("asstPickSubject"));
    for (const subject of view.subjects) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "asst-chip";
      chip.textContent = subject.label || subject.value;
      chip.addEventListener("click", () => startLesson(id, subject.value));
      chips.appendChild(chip);
    }
    box.append(label, chips);
    return box;
  }
  if (view.failed && !ui.note) {
    const note = document.createElement("p");
    note.className = "asst-continue-note is-error";
    note.textContent = t("asstLessonFailed");
    box.appendChild(note);
  }
  const button = document.createElement("button");
  button.type = "button";
  button.className = "asst-continue-button";
  button.appendChild(icon("book"));
  button.appendChild(document.createTextNode(view.failed || ui.note?.error ? t("asstTryAgain")
    : view.topic ? t("asstContinue", { topic: view.topic }) : t("asstContinueGeneric")));
  button.addEventListener("click", () => {
    if (view.needsSubject && view.subjects.length) {
      state.lessonUi.set(id, { picker: true });
      redrawContinue(id);
      el("asstThread").querySelector(`.asst-continue[data-conversation="${CSS.escape(id)}"] .asst-chip`)?.focus();
      return;
    }
    startLesson(id);
  });
  box.appendChild(button);
  return box;
}

function setLessonState(id, patch) {
  if (state.active?.id !== id) return;
  state.active.continue_learning = { ...(state.active.continue_learning || {}), ...patch };
}

function redrawContinue(id) {
  if (state.active?.id === id) decorate();
}

/* One click builds one lesson: the button is disabled while the request runs, and the
 * server's own guard (one lesson per conversation and reply) answers a second tab's
 * click with 202 "being built" and then the same lesson. */
async function startLesson(id, subject = "") {
  if (state.lessonBusy.has(id)) return;
  state.lessonBusy.add(id);
  state.lessonUi.delete(id);
  redrawContinue(id);
  try {
    const data = await api(apiUrl(id, "/lesson"), { method: "POST", body: subject ? { subject } : {} });
    if (data.httpStatus === 202 || data.status === "pending") {
      setLessonState(id, { status: "pending" });
      pollLesson(id, { keepBusy: true });
      return;
    }
    state.lessonBusy.delete(id);
    setLessonState(id, { status: "ready", url: data.url, title: data.title || "" });
  } catch (error) {
    state.lessonBusy.delete(id);
    if (error.code === "subject_needed") {
      setLessonState(id, { available: true, reason: "subject_needed", subject_choices: error.details?.subject_choices || [] });
      state.lessonUi.set(id, { picker: true });
    } else if (error.code === "not_enough_context") {
      // The server says what to add; no button until the conversation changes.
      setLessonState(id, { available: false });
      state.lessonUi.set(id, { note: { text: error.message, error: false } });
    } else if (error.code === "conversation_busy") {
      toast(t("asstStillAnswering"));
    } else if (error.details?.upgrade_url) {
      state.lessonUi.set(id, { note: { text: error.message, error: false,
                                       link: { href: error.details.upgrade_url, label: t("asstSeePremium") } } });
      setLessonState(id, { status: "failed" });
    } else if (error.login) {
      showError(error);
    } else {
      setLessonState(id, { status: "failed" });
      state.lessonUi.set(id, { note: { text: error.status === 404 ? error.message : t("asstLessonFailed"), error: true } });
    }
  }
  redrawContinue(id);
}

function pollLesson(id, { keepBusy = false } = {}) {
  if (state.lessonPolls.has(id)) return;
  if (keepBusy) state.lessonBusy.add(id);
  const started = Date.now();
  const finish = (patch, note = null) => {
    state.lessonPolls.delete(id);
    state.lessonBusy.delete(id);
    setLessonState(id, patch);
    if (note) state.lessonUi.set(id, { note });
    redrawContinue(id);
  };
  const tick = async () => {
    try {
      const data = await api(apiUrl(id, "/lesson"));
      const lesson = data.continue_learning || {};
      if (lesson.status === "ready") return finish(lesson);
      if (lesson.status !== "pending") return finish(lesson, { text: t("asstLessonFailed"), error: true });
    } catch (error) {
      if (error.status === 404) return finish({ status: "failed" }, { text: error.message, error: true });
    }
    if (Date.now() - started > LESSON_POLL_LIMIT_MS) {
      return finish({ status: "failed" }, { text: t("asstLessonFailed"), error: true });
    }
    state.lessonPolls.set(id, window.setTimeout(tick, LESSON_POLL_MS));
  };
  state.lessonPolls.set(id, window.setTimeout(tick, LESSON_POLL_MS));
}

/* -------------------------------------------------------------------- thread */

function setHeader(conversation) {
  const title = conversation ? (conversation.title || t("asstNewChat")) : t("asstNewChat");
  el("asstThreadTitle").textContent = title;
  const meta = [];
  if (conversation?.archived) meta.push(t("asstArchivedBadge"));
  if (conversation?.topic) meta.push(conversation.topic);
  el("asstThreadMeta").textContent = meta.join(" · ");
  if (conversation?.preset) el("asstPreset").value = conversation.preset;
  document.title = conversation ? `${title} — Learnova` : state.baseTitle;
}

function renderThread(conversation) {
  state.active = conversation;
  const thread = el("asstThread");
  thread.setAttribute("aria-busy", "true");
  const nodes = (conversation.messages || []).map((message) => messageNode(message));
  thread.replaceChildren(...nodes);
  if (!nodes.length) thread.appendChild(welcomeNode());
  const job = state.sending.get(conversation.id);
  if (job) {
    // Still answering from this page: show the wait where the reply will appear.
    const pending = pendingNode(Boolean(job.attachments?.length));
    thread.appendChild(pending);
    job.nodes = { pending, draft: null };
  }
  decorate();
  setHeader(conversation);
  thread.setAttribute("aria-busy", "false");
  scrollToEnd(true);
  updateComposer();
}

function scrollToEnd(force = false) {
  const thread = el("asstThread");
  if (force || state.stickToBottom) {
    thread.scrollTop = thread.scrollHeight;
    state.stickToBottom = true;
  }
  el("asstScrollDown").hidden = state.stickToBottom;
}

function onThreadScroll() {
  state.stickToBottom = isNearBottom(el("asstThread"));
  el("asstScrollDown").hidden = state.stickToBottom;
}

function saveDraft() {
  const text = el("asstInput").value;
  if (text.trim()) state.drafts.set(activeKey(), text);
  else state.drafts.delete(activeKey());
}

function loadDraft(key) {
  el("asstInput").value = state.drafts.get(key) || "";
  updateCount();
}

async function openConversation(id, { focus = true } = {}) {
  if (!id) return;
  closeMenu({ restore: false });
  if (id !== state.activeId) saveDraft();
  state.openController?.abort();
  const controller = new AbortController();
  state.openController = controller;
  const seq = ++state.openSeq;
  state.activeId = id;
  if (state.active?.id !== id) state.active = null;
  history.replaceState(null, "", conversationUrl(id));
  loadDraft(id);
  markRows();
  updateComposer();
  if (narrowScreen.matches) closeDrawer({ restoreFocus: false });
  el("asstThread").setAttribute("aria-busy", "true");
  try {
    const data = await api(apiUrl(id), { signal: controller.signal });
    if (seq !== state.openSeq || state.activeId !== id) return;
    rememberConversation(data.conversation, { moveToTop: false });
    renderThread(data.conversation);
    if (data.conversation.busy && !state.sending.has(id)) pollUntilIdle(id);
    if (focus && !coarsePointer.matches) el("asstInput").focus({ preventScroll: true });
  } catch (error) {
    if (error?.name === "AbortError" || seq !== state.openSeq) return;
    el("asstThread").setAttribute("aria-busy", "false");
    if (error.status === 404) {
      forgetConversation(id);
      newChat({ focus: false });
    }
    showError(error);
  }
}

function newChat({ focus = true } = {}) {
  closeMenu({ restore: false });
  saveDraft();
  state.openController?.abort();
  state.openSeq += 1;
  state.activeId = null;
  state.active = null;
  el("asstThread").replaceChildren(welcomeNode());
  setHeader(null);
  history.replaceState(null, "", page.dataset.homePath || "/assistant");
  loadDraft("draft");
  markRows();
  updateComposer();
  scrollToEnd(true);
  if (narrowScreen.matches) closeDrawer({ restoreFocus: false });
  if (focus && !coarsePointer.matches) el("asstInput").focus();
}

/* Ask again until the server has finished the reply it is writing (a 409, a stop, a
 * reply written by another tab), then show the conversation as stored. */
function pollUntilIdle(id, delaySeconds = 4) {
  if (state.busyPolls.has(id)) return;
  const started = Date.now();
  const tick = async () => {
    try {
      const data = await api(apiUrl(id));
      const conversation = data.conversation;
      if (!conversation.busy || Date.now() - started > BUSY_POLL_LIMIT_MS) {
        state.busyPolls.delete(id);
        rememberConversation(conversation, { moveToTop: false });
        if (state.activeId === id && !state.sending.has(id)) renderThread(conversation);
        return;
      }
    } catch (error) {
      if (error.status === 404 || error.login) {
        state.busyPolls.delete(id);
        return;
      }
    }
    state.busyPolls.set(id, window.setTimeout(tick, delaySeconds * 1000));
  };
  state.busyPolls.set(id, window.setTimeout(tick, delaySeconds * 1000));
}

/* --------------------------------------------------------------------- sending */

function updateComposer() {
  const busy = state.sending.has(activeKey());
  const stop = el("asstStop");
  const hadFocus = document.activeElement === stop;
  el("asstSend").hidden = busy;
  stop.hidden = !busy;
  if (hadFocus && !busy) el("asstInput").focus({ preventScroll: true });
  el("asstComposer").classList.toggle("is-empty", !state.activeId);
  markRows();
}

function updateCount() {
  const input = el("asstInput");
  const used = input.value.length;
  el("asstCount").textContent = used > 12000 ? `${used} / ${input.maxLength}` : "";
  input.style.height = "auto";
  // Empty, the box is one line high whatever its hint text would need.
  if (input.value) input.style.height = `${Math.min(192, input.scrollHeight)}px`;
}

function trayAttachments(ids) {
  return ids.map((id) => ({ id, url: `/api/chat-attachments/${encodeURIComponent(id)}/image` }));
}

/* Put an unsent message back where it was typed: into the composer when its conversation
 * is open and the box is empty, otherwise into that conversation's draft; its photos go
 * back into the tray. */
function giveBack(job, key) {
  const input = el("asstInput");
  // Whether the server stored it may be unknown: a re-send of the same text carries the
  // same client id, and the server answers it from what it stored.
  if (job.text && job.clientId) state.resend.set(key, { text: job.text, clientId: job.clientId });
  if (job.text && !job.fromChip) {
    if (activeKey() === key && !input.value.trim()) {
      input.value = job.text;
      updateCount();
    } else if (!state.drafts.get(key)) {
      state.drafts.set(key, job.text);
    }
  }
  if (job.attachments?.length && state.tray && job.returnPhotos !== false) {
    state.tray.restore(trayAttachments(job.attachments));
  }
}

function removeOptimistic(job) {
  job.nodes?.draft?.remove();
  job.nodes?.pending?.remove();
}

async function send(text, { fromChip = false } = {}) {
  const key = activeKey();
  if (state.sending.has(key)) {
    toast(t("asstStillAnswering"));
    return;
  }
  const input = el("asstInput");
  const message = String(text ?? input.value).trim();
  const tray = state.tray;
  if (tray?.busy()) { toast(t("imgStillUploading")); return; }
  if (tray?.failed()) { toast(t("imgFixFailed"), "error"); return; }
  const attachments = tray ? tray.ids() : [];
  if (!message && !attachments.length) return;
  // The same text given back after a failed send keeps its client id (see giveBack).
  const previous = state.resend.get(key);
  state.resend.delete(key);
  const clientId = previous && previous.text === message ? previous.clientId : safeUUID();
  // The guard comes before anything is awaited: a second Enter finds it.
  const job = { key, clientId, controller: new AbortController(), text: message, attachments, fromChip, nodes: null };
  state.sending.set(key, job);
  if (!fromChip) {
    input.value = "";
    state.drafts.delete(key);
    updateCount();
  }
  if (attachments.length) tray.clear();   // the photos travel with this message now
  closeMenu({ restore: false });

  const thread = el("asstThread");
  thread.querySelector(".asst-welcome")?.remove();
  thread.querySelectorAll(".asst-deco").forEach((node) => node.remove());
  const draft = messageNode({ id: "pending-user", role: "user", content: message || t("imgPhotoOnly") });
  const pending = pendingNode(attachments.length > 0);
  thread.append(draft, pending);
  job.nodes = { draft, pending };
  scrollToEnd(true);
  updateComposer();

  let id = state.activeId;
  try {
    if (!id) {
      const created = await api("/api/assistant/conversations", {
        method: "POST", body: { preset: el("asstPreset").value }, signal: job.controller.signal,
      });
      id = created.conversation.id;
      // The guard moves from the draft to the new conversation.
      state.sending.delete(key);
      job.key = id;
      state.sending.set(id, job);
      rememberConversation(created.conversation);
      if (state.activeId === null && job.nodes.pending.isConnected) {
        state.activeId = id;
        state.active = { ...created.conversation, messages: [] };
        history.replaceState(null, "", conversationUrl(id));
        setHeader(created.conversation);
        markRows();
      }
    }
    const data = await api(apiUrl(id, "/messages"), {
      method: "POST", signal: job.controller.signal,
      body: { message, deep: el("asstDeep").checked, attachments, client_message_id: job.clientId },
    });
    sendDone(job, id, data);
  } catch (error) {
    await sendFailed(job, id, error);
  } finally {
    updateComposer();
  }
}

function sendDone(job, id, data) {
  state.sending.delete(job.key);
  rememberConversation(data.conversation);
  if (state.activeId !== id) return;   // another conversation is open: its thread stays as it is
  if (!job.nodes?.pending?.isConnected || !job.nodes?.draft?.isConnected || !state.active) {
    openConversation(id, { focus: false });
    return;
  }
  const messages = [...(state.active.messages || [])];
  if (data.message) {
    job.nodes.draft?.replaceWith(messageNode(data.message, { imageNotes: data.image_notes }));
    messages.push(data.message);
  }
  if (data.reply) {
    job.nodes.pending.replaceWith(messageNode(data.reply));
    messages.push(data.reply);
  } else {
    // A replayed message whose reply is still being written (another tab, a lost answer):
    // the wait stays on screen until the stored reply is there.
    pollUntilIdle(id, 3);
  }
  state.lessonUi.delete(id);   // a new reply: an earlier Continue Learning note no longer applies
  state.active = { ...state.active, ...data.conversation, messages };
  decorate();
  setHeader(state.active);
  scrollToEnd();
  if (data.reply) announce(data.reply.error ? data.reply.content : t("asstReplyArrived"));
}

async function sendFailed(job, id, error) {
  const key = job.key;
  state.sending.delete(key);
  if (job.stopped || error?.name === "AbortError") return;   // stop() already said what happened
  if (error.code === "conversation_busy") {
    removeOptimistic(job);
    giveBack(job, key);
    toast(t("asstStillAnswering"));
    if (id) pollUntilIdle(id, retryAfterSeconds(error));
    decorate();
    return;
  }
  if (id && sendOutcome(error) === "unknown") {
    // The message may have been stored before the connection broke: ask before offering
    // it back, so trying again never sends it twice.
    try {
      const data = await api(apiUrl(id));
      const stored = (data.conversation.messages || []).some((item) => item.client_message_id === job.clientId);
      rememberConversation(data.conversation, { moveToTop: false });
      if (stored) {
        if (state.activeId === id) renderThread(data.conversation);
        if (data.conversation.busy) pollUntilIdle(id);
        toast(t("asstServerSlow"));
        return;
      }
    } catch (_) {
      /* still unknown: offered back below; giveBack keeps its client id for the re-send */
    }
  }
  removeOptimistic(job);
  job.returnPhotos = error.code !== "attachments_invalid";
  giveBack(job, key === "draft" && id ? id : key);
  if (state.activeId === (id || null)) {
    decorate();
    const thread = el("asstThread");
    if (!thread.querySelector(".asst-msg")) thread.replaceChildren(welcomeNode());
  }
  if (sendOutcome(error) === "unknown" && !error.login) {
    // A tap answer is not put back into the box; Retry sends the same tap again.
    const again = () => (job.fromChip ? send(job.text, { fromChip: true }) : send(el("asstInput").value));
    toast(error.message, "error", { label: t("asstRetry"), onClick: again });
  } else {
    showError(error);
  }
}

function stop() {
  const key = activeKey();
  const job = state.sending.get(key);
  if (!job) return;
  job.stopped = true;
  job.controller.abort();
  state.sending.delete(key);
  if (job.nodes?.pending?.isConnected) job.nodes.pending.replaceWith(stoppedNode());
  updateComposer();
  // The server finishes what it started; the stored reply appears once it is done.
  if (job.key !== "draft") pollUntilIdle(job.key, 4);
}

async function regenerate(message) {
  const id = state.activeId;
  if (!id || state.sending.has(id)) return;
  const article = articleFor(message.id);
  const job = { key: id, controller: new AbortController(), regenerate: true, nodes: null };
  state.sending.set(id, job);
  el("asstThread").querySelectorAll(".asst-deco").forEach((node) => node.remove());
  const pending = pendingNode(false);
  if (article) article.replaceWith(pending);
  job.nodes = { pending, draft: null };
  updateComposer();
  scrollToEnd();
  try {
    const data = await api(apiUrl(id, "/regenerate"), {
      method: "POST", signal: job.controller.signal, body: { message_id: message.id, deep: el("asstDeep").checked },
    });
    state.sending.delete(id);
    rememberConversation(data.conversation, { moveToTop: false });
    if (state.activeId !== id) return;
    if (!pending.isConnected || !state.active) {
      openConversation(id, { focus: false });
      return;
    }
    pending.replaceWith(messageNode(data.reply));
    state.lessonUi.delete(id);
    const messages = (state.active.messages || []).filter((item) => item.id !== data.replaced_id && item.id !== message.id);
    state.active = { ...state.active, ...data.conversation, messages: [...messages, data.reply] };
    decorate();
    setHeader(state.active);
    scrollToEnd();
    announce(data.reply.error ? data.reply.content : t("asstReplyArrived"));
  } catch (error) {
    state.sending.delete(id);
    if (job.stopped || error?.name === "AbortError") return;
    if (pending.isConnected && article) pending.replaceWith(article);
    if (error.code === "not_latest") {
      openConversation(id, { focus: false });   // the thread moved on elsewhere: show it as stored
    } else if (error.code === "conversation_busy") {
      toast(t("asstStillAnswering"));
      pollUntilIdle(id, retryAfterSeconds(error));
    } else {
      showError(error);
    }
    decorate();
  } finally {
    updateComposer();
  }
}

function choosePhoto() {
  const camera = el("asstCamera");
  const gallery = el("asstGallery");
  const target = coarsePointer.matches && camera && camera.offsetParent !== null ? camera : gallery;
  target?.click();
}

/* ------------------------------------------------------------- conversations */

function forgetConversation(id) {
  state.lists.recent = state.lists.recent.filter((row) => row.id !== id);
  state.lists.archived = state.lists.archived.filter((row) => row.id !== id);
  state.drafts.delete(id);
  renderLists();
}

/* A conversation the server just sent back (a send, a rename, an open): into the right
 * list, at the top when it has a new message. While searching, the search runs again. */
function rememberConversation(conversation, { moveToTop = true } = {}) {
  if (!conversation?.id) return;
  if (state.active?.id === conversation.id) {
    state.active = { ...state.active, ...conversation, messages: conversation.messages || state.active.messages };
  }
  if (state.search) {
    scheduleSearch(0);
    return;
  }
  const row = { ...conversation };
  delete row.messages;
  delete row.continue_learning;
  if (row.archived) {
    state.lists.recent = state.lists.recent.filter((item) => item.id !== row.id);
    if (state.loaded.archived) state.lists.archived = upsertRow(state.lists.archived, row, { moveToTop });
  } else {
    state.lists.archived = state.lists.archived.filter((item) => item.id !== row.id);
    state.lists.recent = upsertRow(state.lists.recent, row, { moveToTop });
  }
  renderLists();
}

async function loadList(view = "recent", { append = false } = {}) {
  state.listControllers[view]?.abort();
  const controller = new AbortController();
  state.listControllers[view] = controller;
  const params = new URLSearchParams({ view });
  const zone = browserZone();
  if (zone) params.set("tz", zone);
  if (state.search) params.set("q", state.search);
  if (append && state.cursors[view]) params.set("cursor", state.cursors[view]);
  try {
    const data = await api(`/api/assistant/conversations?${params}`, { signal: controller.signal });
    state.lists[view] = append ? mergeRows(state.lists[view], data.conversations) : (data.conversations || []);
    state.cursors[view] = data.next_cursor || null;
    state.loaded[view] = true;
    renderLists();
  } catch (error) {
    if (error?.name !== "AbortError") showError(error);
  }
}

function scheduleSearch(delay = 250) {
  window.clearTimeout(state.searchTimer);
  state.searchTimer = window.setTimeout(() => {
    loadList("recent");
    if (state.archivedOpen) loadList("archived");
  }, delay);
}

function rowNode(conversation, archived) {
  const item = document.createElement("li");
  item.className = "asst-row";
  item.dataset.id = conversation.id;
  const link = document.createElement("a");
  link.className = "asst-list-item";
  link.href = conversationUrl(conversation.id);
  const title = document.createElement("span");
  title.className = "asst-list-title";
  title.textContent = conversation.title || t("asstNewChat");
  link.appendChild(title);
  if (conversation.snippet) {
    const snippet = document.createElement("span");
    snippet.className = "asst-list-snippet";
    snippet.textContent = conversation.snippet;
    link.appendChild(snippet);
  }
  link.addEventListener("click", (event) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    openConversation(conversation.id);
  });
  const more = document.createElement("button");
  more.type = "button";
  more.className = "asst-row-menu";
  more.dataset.tourTarget = "chat-row-menu";
  more.setAttribute("aria-haspopup", "menu");
  more.setAttribute("aria-expanded", "false");
  more.setAttribute("aria-controls", "asstItemMenu");
  more.setAttribute("aria-label", t("asstMoreOptionsFor", { title: conversation.title || t("asstNewChat") }));
  more.appendChild(icon("dots"));
  more.addEventListener("click", (event) => {
    event.stopPropagation();
    if (state.menu?.button === more) closeMenu();
    else openMenu(more, conversation, archived, "first");
  });
  more.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openMenu(more, conversation, archived, event.key === "ArrowUp" ? "last" : "first");
    }
  });
  item.append(link, more);
  return item;
}

function renderList(container, rows, archived) {
  container.replaceChildren();
  const groups = groupRows(rows, { activeId: state.activeId });
  for (const group of groups) {
    if (!archived && !state.search) {
      const heading = document.createElement("h3");
      heading.className = "asst-group-label";
      heading.textContent = t(group.label);
      container.appendChild(heading);
    }
    const list = document.createElement("ul");
    list.className = "asst-group";
    for (const row of group.rows) list.appendChild(rowNode(row, archived));
    container.appendChild(list);
  }
  return groups.reduce((count, group) => count + group.rows.length, 0);
}

function renderLists() {
  if (state.menu) closeMenu({ restore: false });
  const shown = renderList(el("asstList"), state.lists.recent, false);
  const empty = el("asstListEmpty");
  empty.textContent = state.search ? t("asstNoResults") : empty.dataset.emptyText;
  empty.classList.toggle("hidden", shown > 0 || !state.loaded.recent);
  el("asstLoadMore").hidden = !state.cursors.recent;
  if (state.archivedOpen) {
    const archived = el("asstArchivedList");
    const count = renderList(archived, state.lists.archived, true);
    if (state.loaded.archived && !count) {
      const note = document.createElement("p");
      note.className = "asst-empty-note";
      note.textContent = state.search ? t("asstNoResults") : t("asstNoArchived");
      archived.appendChild(note);
    }
    if (state.cursors.archived) {
      const more = document.createElement("button");
      more.type = "button";
      more.className = "asst-load-more";
      more.textContent = t("asstLoadMore");
      more.addEventListener("click", () => loadList("archived", { append: true }));
      archived.appendChild(more);
    }
  }
  markRows();
}

/* Active and busy marks without rebuilding the list (opening a conversation, a send). */
function markRows() {
  for (const row of document.querySelectorAll(".asst-row")) {
    const id = row.dataset.id;
    const active = id === state.activeId;
    row.classList.toggle("is-active", active);
    const link = row.querySelector(".asst-list-item");
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
    const listed = state.lists.recent.find((item) => item.id === id) || state.lists.archived.find((item) => item.id === id);
    row.classList.toggle("is-busy", state.sending.has(id) || state.busyPolls.has(id) || Boolean(listed?.busy));
  }
}

function toggleArchived() {
  state.archivedOpen = !state.archivedOpen;
  el("asstShowArchived").setAttribute("aria-expanded", String(state.archivedOpen));
  el("asstArchivedList").hidden = !state.archivedOpen;
  if (state.archivedOpen) {
    loadList("archived");
  } else {
    el("asstArchivedList").replaceChildren();
  }
}

/* --------------------------------------------------------------- row menu */

function menuItems() {
  return [...el("asstItemMenu").querySelectorAll("[role=menuitem]")];
}

function openMenu(button, conversation, archived, focusWhich = "first") {
  closeMenu({ restore: false });
  const menu = el("asstItemMenu");
  state.menu = { button, conversation, archived };
  el("asstArchive").textContent = archived ? t("asstRestore") : t("asstArchive");
  // The tutorial's Restore step lights this item only when it really says Restore.
  if (archived) el("asstArchive").dataset.tourTarget = "chat-restore";
  else delete el("asstArchive").dataset.tourTarget;
  menu.hidden = false;
  button.setAttribute("aria-expanded", "true");
  const box = button.getBoundingClientRect();
  const width = menu.offsetWidth;
  const height = menu.offsetHeight;
  const rtl = document.documentElement.dir === "rtl";
  let left = rtl ? box.left : box.right - width;
  left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
  let top = box.bottom + 4;
  if (top + height > window.innerHeight - 8) top = Math.max(8, box.top - height - 4);
  menu.style.left = `${Math.round(left)}px`;
  menu.style.top = `${Math.round(top)}px`;
  const items = menuItems();
  (focusWhich === "last" ? items[items.length - 1] : items[0])?.focus();
  document.addEventListener("pointerdown", onOutsidePointer, true);
}

function closeMenu({ restore = true } = {}) {
  if (!state.menu) return;
  const { button } = state.menu;
  state.menu = null;
  el("asstItemMenu").hidden = true;
  button.setAttribute("aria-expanded", "false");
  document.removeEventListener("pointerdown", onOutsidePointer, true);
  if (restore && button.isConnected) button.focus();
}

function onOutsidePointer(event) {
  if (!state.menu) return;
  if (el("asstItemMenu").contains(event.target) || state.menu.button.contains(event.target)) return;
  // The tutorial (tour.js) lights the menu's items: a tap on its own card keeps the menu open.
  if (event.target instanceof Element && event.target.closest("#tourRoot")
      && document.querySelector("#asstItemMenu [data-tour-lit]")) return;
  closeMenu({ restore: false });
}

function onMenuKeydown(event) {
  const items = menuItems();
  const index = items.indexOf(document.activeElement);
  if (event.key === "Escape") {
    event.preventDefault();
    event.stopPropagation();
    closeMenu();
    return;
  }
  if (event.key === "Tab") {
    closeMenu();   // focus goes back to the row's button; Tab then moves on from there
    return;
  }
  const next = menuIndex(index, event.key, items.length);
  if (next !== null) {
    event.preventDefault();
    items[next].focus();
  }
}

function menuAction(action) {
  if (!state.menu) return;
  const { conversation, archived, button } = state.menu;
  closeMenu({ restore: false });
  if (action === "rename") openRename(conversation, button);
  else if (action === "archive") setArchived(conversation, !archived, button);
  else if (action === "delete") openDelete(conversation, archived, button);
}

/* ----------------------------------------------------------- rename, delete */

function returnFocus(target) {
  const id = state.dialogFor?.conversation?.id;
  const button = id ? document.querySelector(`.asst-row[data-id="${CSS.escape(id)}"] .asst-row-menu`) : null;
  (button || (target?.isConnected ? target : null) || el("asstInput")).focus({ preventScroll: true });
}

function openRename(conversation, returnTo) {
  state.dialogFor = { conversation, returnTo };
  const input = el("asstRenameInput");
  input.value = conversation.title || "";
  el("asstRenameSave").disabled = false;
  el("asstRenameDialog").showModal();
  input.focus();
  input.select();
}

async function saveRename(event) {
  event.preventDefault();
  const target = state.dialogFor;
  const input = el("asstRenameInput");
  const title = input.value.trim();
  if (!target || !title) {
    input.focus();
    return;
  }
  el("asstRenameSave").disabled = true;
  try {
    const data = await api(apiUrl(target.conversation.id), { method: "PATCH", body: { title } });
    rememberConversation(data.conversation, { moveToTop: false });
    if (state.activeId === data.conversation.id) setHeader(state.active || data.conversation);
    el("asstRenameDialog").close();
    toast(t("asstRenameSaved"), "success");
  } catch (error) {
    el("asstRenameSave").disabled = false;
    showError(error);
  }
}

function openDelete(conversation, archived, returnTo) {
  state.dialogFor = { conversation, archived, returnTo };
  const heading = el("asstDeleteHeading");
  heading.textContent = (heading.dataset.template || "{title}").replace("{title}", conversation.title || t("asstNewChat"));
  el("asstDeleteArchive").hidden = Boolean(archived);
  el("asstDeleteConfirm").disabled = false;
  el("asstDeleteDialog").showModal();
  el("asstDeleteCancel").focus();
}

async function confirmDelete() {
  const target = state.dialogFor;
  if (!target) return;
  const id = target.conversation.id;
  el("asstDeleteConfirm").disabled = true;
  try {
    await api(apiUrl(id), { method: "DELETE" });
    el("asstDeleteDialog").close();
    forgetConversation(id);
    if (state.activeId === id) newChat({ focus: false });
    toast(t("asstDeleted"), "success");
  } catch (error) {
    el("asstDeleteConfirm").disabled = false;
    showError(error);
  }
}

async function setArchived(conversation, archived, returnTo = null) {
  try {
    const data = await api(apiUrl(conversation.id), { method: "PATCH", body: { archived } });
    rememberConversation(data.conversation, { moveToTop: false });
    // The lists are sorted by the server (an archive or restore keeps its place in time).
    loadList("recent");
    if (state.archivedOpen) loadList("archived");
    if (archived && state.activeId === conversation.id) newChat({ focus: false });
    else if (state.activeId === conversation.id) setHeader(state.active || data.conversation);
    toast(archived ? t("asstArchived") : t("asstRestored"), "success");
    if (returnTo?.isConnected) returnTo.focus();
  } catch (error) {
    showError(error);
  }
}

/* -------------------------------------------------------- sidebar and drawer */

function storedSidebar() {
  try {
    return window.localStorage.getItem(SIDEBAR_KEY);
  } catch (_) {
    return null;
  }
}

function setSidebarCollapsed(collapsed, { remember = true } = {}) {
  page.dataset.sidebar = collapsed ? "collapsed" : "open";
  if (remember) {
    try {
      window.localStorage.setItem(SIDEBAR_KEY, collapsed ? "collapsed" : "open");
    } catch (_) {
      /* private mode: the choice lasts for this page only */
    }
  }
  syncToggles();
}

function drawerOpen() {
  return el("asstSidebar").classList.contains("is-open");
}

function syncToggles() {
  const toggle = el("asstDrawerToggle");
  const inner = el("asstSidebarToggle");
  if (narrowScreen.matches) {
    toggle.setAttribute("aria-expanded", String(drawerOpen()));
    inner.setAttribute("aria-expanded", String(drawerOpen()));
    toggle.setAttribute("aria-label", toggle.dataset.labelMobile);
    toggle.title = toggle.dataset.labelMobile;
  } else {
    const open = page.dataset.sidebar !== "collapsed";
    toggle.setAttribute("aria-expanded", String(open));
    inner.setAttribute("aria-expanded", String(open));
    toggle.setAttribute("aria-label", toggle.dataset.labelDesktop);
    toggle.title = toggle.dataset.labelDesktop;
  }
}

function openDrawer() {
  const sidebar = el("asstSidebar");
  state.drawerReturn = document.activeElement;
  sidebar.classList.add("is-open");
  el("asstScrim").hidden = false;
  el("asstMain").inert = true;
  syncToggles();
  el("asstNew").focus();
}

function closeDrawer({ restoreFocus = true } = {}) {
  if (!drawerOpen()) return;
  closeMenu({ restore: false });
  el("asstSidebar").classList.remove("is-open");
  el("asstScrim").hidden = true;
  el("asstMain").inert = false;
  syncToggles();
  if (restoreFocus) {
    const back = state.drawerReturn?.isConnected && !el("asstSidebar").contains(state.drawerReturn)
      ? state.drawerReturn : el("asstDrawerToggle");
    back.focus();
  }
}

function onDrawerToggle() {
  if (narrowScreen.matches) {
    if (drawerOpen()) closeDrawer();
    else openDrawer();
  } else {
    setSidebarCollapsed(false);
    el("asstSidebarToggle").focus();
  }
}

function onSidebarToggle() {
  if (narrowScreen.matches) {
    closeDrawer();
  } else {
    setSidebarCollapsed(true);
    el("asstDrawerToggle").focus();
  }
}

/* The open drawer keeps focus: Tab cycles inside it, Escape closes it. */
function onDocumentKeydown(event) {
  if (!drawerOpen() || state.menu || document.querySelector("dialog[open]")) return;
  if (event.key === "Escape") {
    event.preventDefault();
    closeDrawer();
    return;
  }
  if (event.key !== "Tab") return;
  const focusable = [...el("asstSidebar").querySelectorAll("a[href], button:not([disabled]), input:not([disabled])")]
    .filter((node) => node.offsetParent !== null && !node.closest("[hidden]"));
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (!el("asstSidebar").contains(document.activeElement)) {
    event.preventDefault();
    first.focus();
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function onScreenChange() {
  if (!narrowScreen.matches && drawerOpen()) closeDrawer({ restoreFocus: false });
  el("asstMain").inert = false;
  syncToggles();
}

/* The shell is as tall as what the student actually sees: on iOS the keyboard shrinks
 * the visual viewport only, so --asst-vvh follows it and the composer stays above. */
function trackViewport() {
  const viewport = window.visualViewport;
  if (!viewport) return;
  // Typing on a short screen (a phone with its keyboard open): the navigation bar and the
  // thread's meta line step aside (assistant.css, html.asst-keyboard) until it closes, so
  // more of the conversation stays in view than the few lines left between bar and box.
  const compact = () => {
    const typing = document.activeElement === el("asstInput");
    document.documentElement.classList.toggle(
      "asst-keyboard", narrowScreen.matches && typing && viewport.height < KEYBOARD_COMPACT_HEIGHT);
  };
  const apply = () => {
    if (Math.abs((viewport.scale || 1) - 1) > 0.01) return;   // pinch zoom: leave the layout alone
    const keepBottom = state.stickToBottom;
    document.documentElement.style.setProperty("--asst-vvh", `${Math.round(viewport.height)}px`);
    compact();
    if (keepBottom) scrollToEnd(true);
    if (window.scrollY > 0 && document.activeElement === el("asstInput")) window.scrollTo(0, 0);
  };
  viewport.addEventListener("resize", apply);
  viewport.addEventListener("scroll", apply);
  el("asstInput").addEventListener("focus", compact);
  el("asstInput").addEventListener("blur", compact);
  apply();
}

/* --------------------------------------------------------------------- photos */

async function restoreWaitingPhotos() {
  if (!state.tray) return;
  try {
    const data = await api("/api/chat-attachments?waiting=1&purpose=chat");
    const waiting = data.attachments || [];
    state.tray.restore(waiting);
    if (waiting.length > state.tray.max()) offerRemoveWaiting();
  } catch (_) {
    /* the tray simply starts empty */
  }
}

function offerRemoveWaiting() {
  toast(t("asstWaitingFull"), "info", { label: t("asstRemoveWaiting"), onClick: removeWaitingPhotos });
}

async function removeWaitingPhotos() {
  try {
    const data = await api("/api/chat-attachments?waiting=1&purpose=chat");
    const kept = new Set(state.tray?.ids() || []);
    await Promise.all((data.attachments || []).filter((item) => !kept.has(item.id)).map((item) =>
      fetch(`/api/chat-attachments/${encodeURIComponent(item.id)}`, { method: "DELETE" }).catch(() => null)));
    toast(t("asstWaitingRemoved"), "success");
  } catch (error) {
    showError(error);
  }
}

/* An upload refused because too many photos wait unsent: offer to clear them. */
async function onTrayChange() {
  if (!state.tray?.failed()) {
    state.waitingChecked = false;
    return;
  }
  if (state.waitingChecked) return;
  state.waitingChecked = true;
  try {
    const data = await api("/api/chat-attachments?waiting=1&purpose=chat");
    if ((data.attachments || []).length >= WAITING_MAX) offerRemoveWaiting();
  } catch (_) {
    /* the tray already shows why the upload failed */
  }
}

function hasFiles(event) {
  return [...(event.dataTransfer?.types || [])].includes("Files");
}

function setupDropAndPaste() {
  const main = el("asstMain");
  const zone = el("asstDropZone");
  if (!state.tray || !zone) return;
  let depth = 0;
  main.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth += 1;
    zone.hidden = false;
  });
  main.addEventListener("dragover", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  main.addEventListener("dragleave", (event) => {
    if (!hasFiles(event)) return;
    depth = Math.max(0, depth - 1);
    if (!depth) zone.hidden = true;
  });
  main.addEventListener("drop", (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    depth = 0;
    zone.hidden = true;
    const files = [...event.dataTransfer.files];
    if (files.length) state.tray.add(files);
  });
  el("asstInput").addEventListener("paste", (event) => {
    const files = [...(event.clipboardData?.files || [])].filter((file) => file.type.startsWith("image/"));
    if (!files.length) return;
    if (!event.clipboardData.getData("text/plain")) event.preventDefault();
    state.tray.add(files);
  });
}

/* ----------------------------------------------------------------------- setup */

function onSuggestion(button) {
  const input = el("asstInput");
  input.value = prefillText(button.dataset.prompt || button.textContent);
  updateCount();
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  if (button.dataset.action === "photo") choosePhoto();
}

function init() {
  if (!page) return;
  const input = el("asstInput");
  state.welcome = el("asstWelcome")?.cloneNode(true) || null;
  el("asstListEmpty").dataset.emptyText = el("asstListEmpty").textContent;

  el("asstComposer").addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });
  input.addEventListener("input", updateCount);
  input.addEventListener("keydown", (event) => {
    // Enter sends, Shift+Enter makes a new line - on a phone the keyboard's Enter makes a
    // new line and the send button sends.
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing && !coarsePointer.matches) {
      event.preventDefault();
      send();
    }
  });
  el("asstStop").addEventListener("click", stop);
  el("asstDeep").addEventListener("change", (event) => {
    event.target.closest(".asst-deep")?.classList.toggle("is-on", event.target.checked);
  });
  if (el("asstAttachTray")) {
    state.tray = createAttachmentTray({
      tray: el("asstAttachTray"), inputs: [el("asstCamera"), el("asstGallery")], purpose: "chat",
      onError: (message) => toast(message, "error"),
      onChange: onTrayChange,
    });
  }
  setupDropAndPaste();

  el("asstNew").addEventListener("click", () => newChat());
  el("asstHeadNew").addEventListener("click", () => newChat());
  el("asstSearch").addEventListener("input", (event) => {
    const value = event.target.value.trim();
    const query = value.length >= 2 ? value.slice(0, 100) : "";
    if (query === state.search) return;
    state.search = query;
    scheduleSearch();
  });
  el("asstShowArchived").addEventListener("click", toggleArchived);
  el("asstLoadMore").addEventListener("click", () => loadList("recent", { append: true }));
  el("asstPreset").addEventListener("change", async (event) => {
    const id = state.activeId;
    if (!id) return;   // a new chat takes the style when it is created
    try {
      const data = await api(apiUrl(id), { method: "PATCH", body: { preset: event.target.value } });
      rememberConversation(data.conversation, { moveToTop: false });
    } catch (error) {
      showError(error);
    }
  });

  el("asstItemMenu").addEventListener("keydown", onMenuKeydown);
  el("asstRename").addEventListener("click", () => menuAction("rename"));
  el("asstArchive").addEventListener("click", () => menuAction("archive"));
  el("asstDelete").addEventListener("click", () => menuAction("delete"));
  el("asstRenameForm").addEventListener("submit", saveRename);
  el("asstRenameCancel").addEventListener("click", () => el("asstRenameDialog").close());
  el("asstRenameDialog").addEventListener("close", () => {
    returnFocus(state.dialogFor?.returnTo);
    state.dialogFor = null;
  });
  el("asstDeleteCancel").addEventListener("click", () => el("asstDeleteDialog").close());
  el("asstDeleteConfirm").addEventListener("click", confirmDelete);
  el("asstDeleteArchive").addEventListener("click", () => {
    const target = state.dialogFor;
    el("asstDeleteDialog").close();
    if (target) setArchived(target.conversation, true);
  });
  el("asstDeleteDialog").addEventListener("close", () => {
    returnFocus(state.dialogFor?.returnTo);
    state.dialogFor = null;
  });
  for (const dialog of [el("asstRenameDialog"), el("asstDeleteDialog")]) {
    // A click on the backdrop (outside the box) closes, like Escape.
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  el("asstDrawerToggle").addEventListener("click", onDrawerToggle);
  el("asstSidebarToggle").addEventListener("click", onSidebarToggle);
  el("asstScrim").addEventListener("click", () => closeDrawer());
  document.addEventListener("keydown", onDocumentKeydown, true);
  narrowScreen.addEventListener("change", onScreenChange);
  if (storedSidebar() === "collapsed") setSidebarCollapsed(true, { remember: false });
  syncToggles();

  const thread = el("asstThread");
  thread.addEventListener("scroll", onThreadScroll, { passive: true });
  thread.addEventListener("click", (event) => {
    const suggestion = event.target.closest(".asst-suggestion");
    if (suggestion) onSuggestion(suggestion);
  });
  // Formulas, code colours and photos change a reply's height (and width) after it was added.
  new MutationObserver(() => {
    if (state.stickToBottom) scrollToEnd();
    markScrollers();
  }).observe(thread, { childList: true, subtree: true });
  if (window.ResizeObserver) new ResizeObserver(() => markScrollers()).observe(thread);
  thread.addEventListener("load", () => { if (state.stickToBottom) scrollToEnd(); }, true);
  el("asstScrollDown").addEventListener("click", () => {
    thread.scrollTo({ top: thread.scrollHeight, behavior: reducedMotion.matches ? "auto" : "smooth" });
  });
  trackViewport();

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    loadList("recent");
    if (state.archivedOpen) loadList("archived");
  });

  const initial = page.dataset.initialConversation;
  if (initial) {
    openConversation(initial, { focus: false });
  } else {
    setHeader(null);
    updateComposer();
  }
  loadList("recent");
  restoreWaitingPhotos();
  updateCount();
  window.__asstReady = true;
}

document.addEventListener("DOMContentLoaded", init);

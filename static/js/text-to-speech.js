import {t} from "./i18n.js";

const synthesis = window.speechSynthesis;
const Utterance = window.SpeechSynthesisUtterance;
let active = null;

function announce(control, message) {
  const status = control.querySelector("[data-playback-status]");
  if (status) status.textContent = message;
}

function setControlState(control, state) {
  const pause = control.querySelector("[data-speech-pause]");
  const resume = control.querySelector("[data-speech-resume]");
  const stop = control.querySelector("[data-speech-stop-playback]");
  if (pause) pause.disabled = state !== "playing";
  if (resume) resume.disabled = state !== "paused";
  if (stop) stop.disabled = state === "idle";
  control.classList.toggle("is-playing", state === "playing");
  control.classList.toggle("is-paused", state === "paused");
}

function stopActive(message = "") {
  if (!active) {
    synthesis?.cancel();
    return;
  }
  const previous = active;
  active = null;
  synthesis?.cancel();
  setControlState(previous.control, "idle");
  if (message) announce(previous.control, message);
}

function targetText(control) {
  const selector = control.dataset.speechTarget || "";
  const target = selector ? document.querySelector(selector) : null;
  return target?.textContent?.replace(/\s+/g, " ").trim() || "";
}

function play(control) {
  if (!synthesis || !Utterance) {
    announce(control, t("speechSynthesisUnsupported"));
    return;
  }
  const text = targetText(control);
  if (!text) return;
  if (active) stopActive(t("playbackStopped"));
  const utterance = new Utterance(text);
  utterance.lang = window.LEARNOVA_CONTENT_LANGUAGE || "en-US";
  utterance.rate = Number(control.querySelector("[data-speech-rate]")?.value || 1);
  const session = {control, utterance};
  active = session;
  utterance.onstart = () => {
    if (active !== session) return;
    setControlState(control, "playing");
    announce(control, t("playing"));
  };
  utterance.onend = () => {
    if (active !== session) return;
    active = null;
    setControlState(control, "idle");
    announce(control, t("playbackFinished"));
  };
  utterance.onerror = () => {
    if (active !== session) return;
    active = null;
    setControlState(control, "idle");
    announce(control, t("speechSynthesisUnsupported"));
  };
  synthesis.speak(utterance);
}

function initializeControl(control) {
  if (control.dataset.playbackInitialized === "true") return;
  control.dataset.playbackInitialized = "true";
  setControlState(control, "idle");
  control.querySelector("[data-speech-play]")?.addEventListener("click", () => play(control));
  control.querySelector("[data-speech-pause]")?.addEventListener("click", () => {
    if (active?.control !== control || !synthesis) return;
    synthesis.pause();
    setControlState(control, "paused");
    announce(control, t("playbackPaused"));
  });
  control.querySelector("[data-speech-resume]")?.addEventListener("click", () => {
    if (active?.control !== control || !synthesis) return;
    synthesis.resume();
    setControlState(control, "playing");
    announce(control, t("playbackResumed"));
  });
  control.querySelector("[data-speech-stop-playback]")?.addEventListener("click", () => {
    if (active?.control === control) stopActive(t("playbackStopped"));
  });
}

function buildButton(attribute, icon, label) {
  const button = document.createElement("button");
  button.type = "button";
  button.setAttribute(attribute, "");
  button.setAttribute("aria-label", label);
  const visibleIcon = document.createElement("span");
  visibleIcon.setAttribute("aria-hidden", "true");
  visibleIcon.textContent = icon;
  const text = document.createElement("span");
  text.textContent = label;
  button.append(visibleIcon, text);
  return button;
}

export function attachListenControl(container) {
  if (!(container instanceof HTMLElement) || container.dataset.listenAttached === "true") return;
  container.dataset.listenAttached = "true";
  let readable = container.querySelector(":scope > .speech-readable-text");
  if (!readable) {
    readable = document.createElement("span");
    readable.className = "speech-readable-text";
    readable.textContent = container.textContent;
    container.replaceChildren(readable);
  }
  if (!readable.id) readable.id = `speech-text-${crypto.randomUUID?.() || Math.random().toString(36).slice(2)}`;
  const control = document.createElement("div");
  control.className = "listen-control compact-listen-control";
  control.dataset.listenControl = "";
  control.dataset.speechTarget = `#${readable.id}`;
  control.setAttribute("role", "group");
  control.setAttribute("aria-label", t("listen"));
  const play = buildButton("data-speech-play", "▶", t("listen"));
  const pause = buildButton("data-speech-pause", "Ⅱ", t("pause"));
  const resume = buildButton("data-speech-resume", "▶", t("resume"));
  const stop = buildButton("data-speech-stop-playback", "■", t("stop"));
  const rate = document.createElement("select");
  rate.dataset.speechRate = "";
  rate.setAttribute("aria-label", t("playbackSpeed"));
  [0.75, 1, 1.25, 1.5, 2].forEach(value => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = `${value}×`;
    option.selected = value === 1;
    rate.appendChild(option);
  });
  const status = document.createElement("span");
  status.className = "speech-status";
  status.dataset.playbackStatus = "";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  status.setAttribute("aria-atomic", "true");
  control.append(play, pause, resume, stop, rate, status);
  container.appendChild(control);
  initializeControl(control);
}

export function initializeListenControls(root = document) {
  root.querySelectorAll("[data-listen-control]").forEach(initializeControl);
  root.querySelectorAll("[data-speech-listen-auto]").forEach(attachListenControl);
}

const observer = new MutationObserver(records => {
  records.forEach(record => record.addedNodes.forEach(node => {
    if (!(node instanceof HTMLElement)) return;
    if (node.matches("[data-speech-listen-auto]")) attachListenControl(node);
    node.querySelectorAll?.("[data-speech-listen-auto]").forEach(attachListenControl);
  }));
});
observer.observe(document.body, {childList: true, subtree: true});

window.addEventListener("pagehide", () => stopActive());
window.addEventListener("beforeunload", () => stopActive());

initializeListenControls();

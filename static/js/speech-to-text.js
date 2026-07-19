import {t} from "./i18n.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let active = null;

function announce(control, message) {
  const status = control.querySelector("[data-speech-status]");
  if (status) status.textContent = message;
}

function setRecordingUi(control, recording) {
  control.classList.toggle("is-recording", recording);
  const record = control.querySelector("[data-speech-record]");
  const stop = control.querySelector("[data-speech-stop]");
  const cancel = control.querySelector("[data-speech-cancel]");
  record?.setAttribute("aria-pressed", recording ? "true" : "false");
  record?.classList.toggle("hidden", recording);
  stop?.classList.toggle("hidden", !recording);
  cancel?.classList.toggle("hidden", !recording);
}

function finishSession(session, message = "") {
  if (active !== session) return;
  setRecordingUi(session.control, false);
  if (message) announce(session.control, message);
  active = null;
}

function cancelActive({restore = true, announceCancellation = true} = {}) {
  if (!active) return;
  const session = active;
  session.cancelled = true;
  try { session.recognition.abort(); } catch (_error) { /* already stopped */ }
  if (restore) session.target.value = session.originalValue;
  finishSession(session, announceCancellation ? t("recordingCancelled") : "");
}

function appendTranscript(session) {
  const reviewedText = session.transcript.trim();
  if (!reviewedText) return;
  const prefix = session.originalValue.trim();
  session.target.value = prefix ? `${session.originalValue.trimEnd()} ${reviewedText}` : reviewedText;
  session.target.focus();
}

function startRecording(control) {
  const selector = control.dataset.speechTarget || "";
  const target = selector ? document.querySelector(selector) : null;
  if (!(target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement)) {
    announce(control, t("speechRecognitionFailed"));
    return;
  }
  if (!Recognition) {
    announce(control, t("speechRecognitionUnsupported"));
    return;
  }
  cancelActive({restore: false, announceCancellation: false});
  const recognition = new Recognition();
  const session = {
    control,
    target,
    recognition,
    originalValue: target.value,
    transcript: "",
    cancelled: false,
  };
  active = session;
  recognition.lang = window.LEARNOVA_CONTENT_LANGUAGE || "en-US";
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.maxAlternatives = 1;
  recognition.onstart = () => {
    if (active !== session) return;
    setRecordingUi(control, true);
    announce(control, t("recording"));
  };
  recognition.onspeechstart = () => announce(control, t("listening"));
  recognition.onresult = event => {
    if (session.cancelled || active !== session) return;
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      if (event.results[index].isFinal) {
        session.transcript += `${event.results[index][0].transcript} `;
      }
    }
    appendTranscript(session);
  };
  recognition.onerror = event => {
    if (session.cancelled || active !== session) return;
    const denied = event.error === "not-allowed" || event.error === "service-not-allowed";
    finishSession(session, denied ? t("microphoneDenied") : t("speechRecognitionFailed"));
  };
  recognition.onend = () => {
    if (session.cancelled || active !== session) return;
    appendTranscript(session);
    finishSession(
      session,
      session.transcript.trim() ? t("transcriptionReady") : t("recordingStopped"),
    );
  };
  try {
    recognition.start();
  } catch (_error) {
    finishSession(session, t("speechRecognitionFailed"));
  }
}

function initializeControl(control) {
  if (control.dataset.speechInitialized === "true") return;
  control.dataset.speechInitialized = "true";
  control.querySelector("[data-speech-record]")?.addEventListener("click", () => startRecording(control));
  control.querySelector("[data-speech-stop]")?.addEventListener("click", () => {
    if (!active || active.control !== control) return;
    announce(control, t("recordingStopped"));
    try { active.recognition.stop(); } catch (_error) { finishSession(active); }
  });
  control.querySelector("[data-speech-cancel]")?.addEventListener("click", () => {
    if (active?.control === control) cancelActive();
  });
}

export function initializeSpeechInputs(root = document) {
  root.querySelectorAll("[data-speech-input]").forEach(initializeControl);
}

window.addEventListener("pagehide", () => cancelActive({restore: false, announceCancellation: false}));
window.addEventListener("beforeunload", () => cancelActive({restore: false, announceCancellation: false}));
document.addEventListener("visibilitychange", () => {
  if (document.hidden) cancelActive({restore: false, announceCancellation: false});
});

initializeSpeechInputs();

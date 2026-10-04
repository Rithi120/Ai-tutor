// Scan a word: photo -> words as chips -> one tapped word with meaning and sentence.
//
// Everything slow happens once, at upload: the server reads the page locally and sends
// back every word with its line. A tap is then a small lookup that returns in well under
// a second from the translation cache, and the student sees one card: the word, its
// meaning (editable, in case no provider answered), the sentence it came from and the
// sentence's meaning. One more tap saves it as a vocabulary entry.
import { api, escapeHtml, t, toast } from "./flashcards/common.js";

const page = document.querySelector("[data-vocabulary-scan]");

if (page) {
  const start = document.querySelector("#scanStart");
  const result = document.querySelector("#scanResult");
  const lookupPanel = document.querySelector("#scanLookup");
  const status = document.querySelector("#scanStatus");
  const error = document.querySelector("#scanError");
  const words = document.querySelector("#scanWords");
  const sourceSelect = document.querySelector("#scanSource");
  const targetSelect = document.querySelector("#scanTarget");
  let scan = null;
  let current = null;   // the lookup shown in the card
  let activeChip = null;

  function showError(message) {
    status.textContent = "";
    error.textContent = message;
    error.classList.remove("hidden");
  }

  function pairParams() {
    const params = new URLSearchParams();
    if (sourceSelect.value) params.set("source", sourceSelect.value);
    if (targetSelect.value) params.set("target", targetSelect.value);
    return params.toString();
  }

  function renderWords() {
    words.innerHTML = scan.lines.map((line, lineIndex) =>
      `<div class="scan-line">${(line.words || []).map((word, wordIndex) =>
        `<button type="button" class="scan-word" data-line="${lineIndex}" data-word="${wordIndex}"
          ${word.clean ? "" : "disabled"}>${escapeHtml(word.text)}</button>`).join("")}</div>`).join("");
    document.querySelector("#scanHint").textContent =
      t("scanWordsFound", { count: scan.word_count });
  }

  async function upload(file) {
    error.classList.add("hidden");
    status.textContent = t("scanReading");
    const body = new FormData();
    body.append("file", file);
    if (sourceSelect.value) body.append("source_language", sourceSelect.value);
    if (targetSelect.value && result.classList.contains("hidden") === false) {
      body.append("target_language", targetSelect.value);
    }
    try {
      const data = await api("/api/vocabulary/scans", { method: "POST", body });
      scan = data.scan;
      sourceSelect.value = scan.source_language || "";
      targetSelect.value = scan.target_language || page.dataset.defaultTarget || "";
      const photo = document.querySelector("#scanPhoto");
      photo.hidden = !scan.preview_url;
      if (scan.preview_url) photo.querySelector("img").src = scan.preview_url;
      renderWords();
      status.textContent = "";
      start.classList.add("hidden");
      result.classList.remove("hidden");
      lookupPanel.classList.add("hidden");
      current = null;
      if (!scan.source_language) toast(t("scanLanguageUnknown"), "error");
    } catch (caught) {
      showError(caught.message);
    }
  }

  async function lookup(lineIndex, wordIndex) {
    if (!scan) return;
    if (!sourceSelect.value) {
      toast(t("scanChooseLanguage"), "error");
      sourceSelect.focus();
      return;
    }
    const statusLine = document.querySelector("#lookupStatus");
    lookupPanel.classList.remove("hidden");
    statusLine.textContent = t("scanLookingUp");
    try {
      const data = await api(
        `/api/vocabulary/scans/${scan.id}/words/${lineIndex}/${wordIndex}?${pairParams()}`);
      current = data.lookup;
      renderLookup();
      statusLine.textContent = "";
    } catch (caught) {
      statusLine.textContent = "";
      toast(caught.message, "error");
    }
  }

  function renderLookup() {
    document.querySelector("#lookupWord").textContent = current.word;
    const translation = document.querySelector("#lookupTranslation");
    translation.value = current.translation || "";
    const provider = document.querySelector("#lookupProvider");
    provider.textContent = current.translation_ok ? "" : t("scanTranslationFailed");
    const sentence = document.querySelector("#lookupSentence");
    if (current.sentence) {
      // The tapped word, highlighted inside its sentence - the first match of the word's
      // letters, so an inflected form ("marche" in "Il marche") still lights up.
      const pattern = new RegExp(current.word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i");
      const match = current.sentence.match(pattern);
      sentence.innerHTML = match
        ? `${escapeHtml(current.sentence.slice(0, match.index))}<mark>${escapeHtml(match[0])}</mark>${
          escapeHtml(current.sentence.slice(match.index + match[0].length))}`
        : escapeHtml(current.sentence);
    } else {
      sentence.textContent = "";
    }
    document.querySelector("#lookupSentenceTranslation").textContent = current.sentence_translation || "";
    if (!current.translation) translation.focus();
  }

  async function save() {
    if (!current || !scan) return;
    const translation = document.querySelector("#lookupTranslation").value.trim();
    const statusLine = document.querySelector("#lookupStatus");
    if (!translation) {
      statusLine.textContent = t("scanAddTranslation");
      document.querySelector("#lookupTranslation").focus();
      return;
    }
    try {
      const saved = await api(`/api/vocabulary/scans/${scan.id}/words/save`, {
        method: "POST", body: {
          word: current.word, translation,
          sentence: current.sentence, sentence_translation: current.sentence_translation,
          source_language: sourceSelect.value, target_language: targetSelect.value,
        },
      });
      statusLine.innerHTML = `${escapeHtml(t(saved.duplicate ? "scanAlreadySaved" : "scanSaved", { list: saved.list_title }))}
        <a href="${escapeHtml(saved.list_url)}">${escapeHtml(t("scanOpenList"))}</a>`;
    } catch (caught) {
      toast(caught.message, "error");
    }
  }

  for (const id of ["#scanCamera", "#scanGallery"]) {
    document.querySelector(id)?.addEventListener("change", event => {
      const file = event.target.files?.[0];
      if (file) upload(file);
      event.target.value = "";
    });
  }
  words.addEventListener("click", event => {
    const chip = event.target.closest(".scan-word");
    if (!chip || chip.disabled) return;
    activeChip?.classList.remove("is-active");
    activeChip = chip;
    chip.classList.add("is-active");
    lookup(Number(chip.dataset.line), Number(chip.dataset.word));
  });
  // Changing either language re-asks for the word that is open, so the card never shows
  // a meaning in a language the student has just moved away from.
  for (const select of [sourceSelect, targetSelect]) {
    select?.addEventListener("change", () => {
      if (current) lookup(current.line_index, current.word_index);
    });
  }
  document.querySelector("#scanAgain")?.addEventListener("click", () => {
    result.classList.add("hidden");
    lookupPanel.classList.add("hidden");
    start.classList.remove("hidden");
    status.textContent = "";
  });
  document.querySelector("#lookupSave")?.addEventListener("click", save);
  document.querySelector("#lookupTranslation")?.addEventListener("keydown", event => {
    if (event.key === "Enter") { event.preventDefault(); save(); }
  });
  document.querySelector("#lookupSpeak")?.addEventListener("click", () => {
    if (!current || !("speechSynthesis" in window)) return;
    speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(current.sentence || current.word);
    utterance.lang = sourceSelect.value || "en";
    speechSynthesis.speak(utterance);
  });
}

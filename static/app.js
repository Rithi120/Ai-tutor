const uploadView = document.querySelector("#uploadView");
const lessonView = document.querySelector("#lessonView");
const uploadForm = document.querySelector("#uploadForm");
const imageInput = document.querySelector("#images");
const previews = document.querySelector("#previews");
const studyGoal = document.querySelector("#studyGoal");
const answerForm = document.querySelector("#answerForm");
let sessionId = null;
let nextQuestion = null;
let currentQuestion = null;
let questionResults = [];
const testTotal = 5;
const languageSelect = document.querySelector("#languageSelect");
let selectedLanguage = localStorage.getItem("numeriLanguage") || "English";
languageSelect.value = selectedLanguage;

const translations = {
  English: {
    settings: "Settings", aiTutor: "AI study tutor", heroEyebrow: "YOUR PERSONAL STUDY SPACE",
    heroTitle: "What do you want to", heroAccent: "learn today?", heroCopy: "Choose a subject and tell Numeri what you need help with. Add school material only when it is useful.",
    startLesson: "Build my lesson", privacy: "🔒 Your material is used only to create this lesson.", chooseSubject: "Choose a subject", subjectMath: "Mathematics", subjectHistory: "History", subjectBiology: "Biology", subjectChemistry: "Chemistry", subjectPhysics: "Physics", subjectOther: "Other", studyQuestion: "What should we work on?", studyPlaceholder: "For example: Explain the causes of World War I and then test me…", addMaterial: "Add photos or notes", optionalMaterial: "Optional · up to 4 images", tryPrompt: "Try asking:", promptPhotosynthesis: "“Explain photosynthesis”", promptPoem: "“Analyze a poem”", promptRevolution: "“French Revolution”",
    share: "Share", shareCopy: "Your notes or worksheet", understand: "Understand", understandCopy: "A clear, tailored explanation", practice: "Practice", practiceCopy: "Questions that adapt to you",
    yourLesson: "YOUR LESSON", mastery: "MASTERY", sessionScore: "SESSION SCORE", noAnswers: "No questions answered yet", newMaterial: "＋ New material",
    reading: "Building your lesson…", finding: "Finding key concepts and preparing your personal study path", personalLesson: "YOUR PERSONAL LESSON", makeClear: "Let's make this clear", seeAction: "SEE IT IN ACTION", yourTurn: "YOUR TURN",
    showHint: "💡 Show a hint", answerPlaceholder: "Write your answer and show your thinking…", checkAnswer: "Check my answer", askTutor: "Ask your tutor", chatKnows: "Knows your lesson and weak points",
    chatWelcome: "I’m here while you practice. Ask me to explain a step, give a hint, or help you understand a mistake.", giveHint: "Give me a hint", whatPractice: "What should I practice?", chatPlaceholder: "Ask about this lesson…",
    language: "Language", languageCopy: "Choose the language used by your tutor.", teacherNotes: "TEACHER NOTES", tipsAndExceptions: "Tips, traps, and exceptions", teacherTips: "Teacher tips", exceptions: "Exceptions and special cases", noExceptions: "No special exceptions are needed for this material.", tip: "Teacher tip", exceptionNote: "Important exception", readyTest: "READY TO PRACTICE?", testTitle: "Test your understanding", testCopy: "Five questions will move from easy to advanced and adapt to your weak points.", startTest: "Start test", testComplete: "TEST COMPLETE", yourResults: "Your learning summary", weaknessChart: "Concept mastery", weakPoints: "Weak points", nextSteps: "Next steps", selectAnswer: "Choose an answer", selectAll: "Select every correct answer", arrangeOrder: "Drag into the correct order", moveUp: "Move up", moveDown: "Move down", viewResults: "View results", level: "Level", answer: "Answer", correction: "Correction", correctTitle: "Nice work — that's correct.", incorrectTitle: "Not quite yet — let's fix it.", nextQuestion: "Next question →", newLabel: "New", thinking: "Thinking…", question: "question", questions: "questions", answered: "answered", of: "of"
  },
  German: {
    settings: "Einstellungen", aiTutor: "KI-Lerntutor", heroEyebrow: "DEIN PERSÖNLICHER LERNBEREICH",
    heroTitle: "Was möchtest du heute", heroAccent: "lernen?", heroCopy: "Wähle ein Fach und beschreibe, wobei du Hilfe brauchst. Schulmaterial kannst du optional hinzufügen.",
    startLesson: "Meine Lektion erstellen", privacy: "🔒 Dein Material wird nur für diese Lektion verwendet.", chooseSubject: "Wähle ein Fach", subjectMath: "Mathematik", subjectHistory: "Geschichte", subjectBiology: "Biologie", subjectChemistry: "Chemie", subjectPhysics: "Physik", subjectOther: "Anderes", studyQuestion: "Woran sollen wir arbeiten?", studyPlaceholder: "Zum Beispiel: Erkläre die Ursachen des Ersten Weltkriegs und teste mich danach…", addMaterial: "Fotos oder Notizen hinzufügen", optionalMaterial: "Optional · bis zu 4 Bilder", tryPrompt: "Beispiele:", promptPhotosynthesis: "„Photosynthese erklären“", promptPoem: "„Ein Gedicht analysieren“", promptRevolution: "„Französische Revolution“",
    share: "Hochladen", shareCopy: "Notizen oder Arbeitsblatt", understand: "Verstehen", understandCopy: "Eine klare, passende Erklärung", practice: "Üben", practiceCopy: "Fragen, die sich an dich anpassen",
    yourLesson: "DEINE LEKTION", mastery: "LERNSTAND", sessionScore: "PUNKTE DIESER SITZUNG", noAnswers: "Noch keine Fragen beantwortet", newMaterial: "＋ Neues Material",
    reading: "Deine Lektion wird erstellt…", finding: "Wichtige Themen werden erkannt und dein persönlicher Lernweg wird vorbereitet", personalLesson: "DEINE PERSÖNLICHE LEKTION", makeClear: "Machen wir es verständlich", seeAction: "BEISPIEL SCHRITT FÜR SCHRITT", yourTurn: "DU BIST DRAN",
    showHint: "💡 Hinweis anzeigen", answerPlaceholder: "Schreibe deine Antwort und deinen Rechenweg…", checkAnswer: "Antwort prüfen", askTutor: "Tutor fragen", chatKnows: "Kennt deine Lektion und deine Schwachstellen",
    chatWelcome: "Ich begleite dich beim Üben. Bitte mich um eine Erklärung, einen Hinweis oder Hilfe bei einem Fehler.", giveHint: "Gib mir einen Hinweis", whatPractice: "Was soll ich üben?", chatPlaceholder: "Frage etwas zu dieser Lektion…",
    language: "Sprache", languageCopy: "Wähle die Sprache der App und deines Tutors.", teacherNotes: "TIPPS DER LEHRKRAFT", tipsAndExceptions: "Tipps, Stolperfallen und Ausnahmen", teacherTips: "Lehrertipps", exceptions: "Ausnahmen und Sonderfälle", noExceptions: "Für diesen Stoff sind keine besonderen Ausnahmen nötig.", tip: "Lehrertipp", exceptionNote: "Wichtige Ausnahme", readyTest: "BEREIT ZUM ÜBEN?", testTitle: "Teste dein Verständnis", testCopy: "Fünf Fragen werden von leicht bis anspruchsvoll schwieriger und passen sich deinen Schwächen an.", startTest: "Test starten", testComplete: "TEST ABGESCHLOSSEN", yourResults: "Deine Lernzusammenfassung", weaknessChart: "Beherrschung der Themen", weakPoints: "Schwachstellen", nextSteps: "Nächste Schritte", selectAnswer: "Wähle eine Antwort", selectAll: "Wähle alle richtigen Antworten", arrangeOrder: "Ziehe die Elemente in die richtige Reihenfolge", moveUp: "Nach oben", moveDown: "Nach unten", viewResults: "Ergebnisse ansehen", level: "Stufe", answer: "Antwort", correction: "Korrektur", correctTitle: "Sehr gut — das ist richtig.", incorrectTitle: "Noch nicht ganz — wir korrigieren es.", nextQuestion: "Nächste Frage →", newLabel: "Neu", thinking: "Ich denke nach…", question: "Frage", questions: "Fragen", answered: "beantwortet", of: "von"
  }
};

function t(key) {
  return translations[selectedLanguage][key] || translations.English[key] || key;
}

function applyTranslations() {
  document.documentElement.lang = selectedLanguage === "German" ? "de" : "en";
  document.querySelectorAll("[data-i18n]").forEach(node => { node.textContent = t(node.dataset.i18n); });
  document.querySelectorAll("[data-i18n-placeholder]").forEach(node => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  const exampleAnswer = document.querySelector("#exampleAnswer");
  if (exampleAnswer?.dataset.answer) exampleAnswer.textContent = `${t("answer")}: ${exampleAnswer.dataset.answer}`;
  if (currentQuestion && !document.querySelector("#testProgress").classList.contains("hidden")) {
    updateProgress(Math.min(questionResults.length + 1, testTotal));
  }
}

function dynamicTranslationNodes() {
  const selectors = [
    "#sideTitle", "#lessonTitle", "#levelBadge", "#explanation", "#exampleProblem",
    "#exampleSteps li", "#teacherTips li", "#exceptionsList li", "#conceptList .concept",
    "#masteryList b", "#questionPrompt", "#hint", ".answer-option span",
    "#dropdownAnswer option:not(:first-child)", ".ordering-item b", "#summaryOverall",
    "#summaryWeaknesses li", "#summaryNextSteps li", ".chart-row span",
    ".tutor-message:not([data-i18n])"
  ];
  return [...document.querySelectorAll(selectors.join(","))].filter(node => node.textContent.trim());
}

async function translateDynamicContent(language) {
  if (!sessionId) return;
  const nodes = dynamicTranslationNodes();
  const entries = nodes.map(node => ({
    text: node.textContent,
    apply: translated => { node.textContent = translated; }
  }));
  const addQuestionEntries = question => {
    if (!question) return;
    ["prompt", "hint"].forEach(key => {
      if (question[key]) entries.push({ text: question[key], apply: translated => { question[key] = translated; } });
    });
    (question.options || []).forEach(option => {
      if (option.label) entries.push({ text: option.label, apply: translated => { option.label = translated; } });
    });
  };
  addQuestionEntries(currentQuestion);
  if (nextQuestion && nextQuestion !== currentQuestion) addQuestionEntries(nextQuestion);
  if (!entries.length) return;
  const response = await fetch("/api/translate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, language, texts: entries.map(entry => entry.text) })
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  if (selectedLanguage !== language) return;
  entries.forEach((entry, index) => entry.apply(data.translations[index]));
}

const settingsPanel = document.querySelector("#settingsPanel");
const settingsBackdrop = document.querySelector("#settingsBackdrop");
function setSettingsOpen(open) {
  settingsPanel.classList.toggle("hidden", !open);
  settingsBackdrop.classList.toggle("hidden", !open);
}
document.querySelector("#settingsButton").addEventListener("click", () => setSettingsOpen(true));
document.querySelector("#settingsClose").addEventListener("click", () => setSettingsOpen(false));
settingsBackdrop.addEventListener("click", () => setSettingsOpen(false));
languageSelect.addEventListener("change", async () => {
  selectedLanguage = languageSelect.value;
  localStorage.setItem("numeriLanguage", selectedLanguage);
  applyTranslations();
  languageSelect.disabled = true;
  try {
    await translateDynamicContent(selectedLanguage);
  } catch (error) {
    showError(error.message || (selectedLanguage === "German" ? "Die Inhalte konnten nicht übersetzt werden." : "The content could not be translated."));
  } finally {
    languageSelect.disabled = false;
  }
});
applyTranslations();

function renderMastery(items = []) {
  const list = document.querySelector("#masteryList");
  if (!items.length) {
    list.innerHTML = `<div class="mastery-item"><b>${selectedLanguage === "German" ? "Beantworte Fragen, um zu beginnen" : "Answer questions to begin"}</b></div>`;
    return;
  }
  list.innerHTML = items.map((item, index) => {
    const score = item.attempts ? item.average_score : 0;
    const label = item.attempts ? `${score}%` : t("newLabel");
    return `<div class="mastery-item ${index === 0 ? "weak" : ""}"><b>${escapeHtml(item.concept)}</b><span>${label}</span><div class="mastery-bar"><i style="width:${Math.max(score, 4)}%"></i></div></div>`;
  }).join("");
}

function showError(message) {
  const toast = document.querySelector("#toast");
  toast.textContent = message;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 4500);
}

function previewFiles() {
  previews.innerHTML = "";
  [...imageInput.files].slice(0, 4).forEach(file => {
    const image = document.createElement("img");
    image.src = URL.createObjectURL(file);
    previews.appendChild(image);
  });
}

imageInput.addEventListener("change", previewFiles);
document.querySelectorAll(".prompt-ideas button").forEach(button => button.addEventListener("click", () => {
  studyGoal.value = selectedLanguage === "German" ? button.dataset.promptDe : button.dataset.promptEn;
  const subjectInput = document.querySelector(`input[name="subject"][value="${button.dataset.subject}"]`);
  if (subjectInput) subjectInput.checked = true;
  studyGoal.focus();
}));

function updateProgress(questionNumber) {
  const answered = questionResults.length;
  const percent = Math.round((answered / testTotal) * 100);
  document.querySelector("#progressLabel").textContent = `${t("question")} ${questionNumber} ${t("of")} ${testTotal}`;
  document.querySelector("#progressPercent").textContent = `${percent}%`;
  document.querySelector("#progressFill").style.width = `${percent}%`;
  document.querySelector("#progressSteps").innerHTML = Array.from({ length: testTotal }, (_, index) => {
    const result = questionResults[index];
    const state = result === true ? "correct" : result === false ? "wrong" : index === questionNumber - 1 ? "current" : "";
    return `<span class="${state}">${result === true ? "✓" : result === false ? "×" : index + 1}</span>`;
  }).join("");
}

function optionMarkup(option, type) {
  return `<label class="answer-option"><input type="${type}" name="answerOption" value="${escapeHtml(option.id)}"><span>${escapeHtml(option.label)}</span></label>`;
}

function enableOrdering() {
  const list = document.querySelector("#orderingList");
  let dragged = null;
  list.querySelectorAll(".ordering-item").forEach(item => {
    item.addEventListener("dragstart", () => { dragged = item; item.classList.add("dragging"); });
    item.addEventListener("dragend", () => { item.classList.remove("dragging"); dragged = null; });
    item.addEventListener("dragover", event => {
      event.preventDefault();
      if (!dragged || dragged === item) return;
      const box = item.getBoundingClientRect();
      list.insertBefore(dragged, event.clientY < box.top + box.height / 2 ? item : item.nextSibling);
    });
  });
  list.addEventListener("click", event => {
    const button = event.target.closest("button");
    if (!button) return;
    const item = button.closest(".ordering-item");
    if (button.dataset.move === "up" && item.previousElementSibling) list.insertBefore(item, item.previousElementSibling);
    if (button.dataset.move === "down" && item.nextElementSibling) list.insertBefore(item.nextElementSibling, item);
  });
}

function renderQuestion(question) {
  currentQuestion = question;
  const questionNumber = questionResults.length + 1;
  document.querySelector("#questionNumber").textContent = String(questionNumber).padStart(2, "0");
  document.querySelector("#questionPrompt").textContent = question.prompt;
  document.querySelector("#difficulty").textContent = `${t("level")} ${question.difficulty}`;
  document.querySelector("#hint").textContent = question.hint;
  document.querySelector("#hint").classList.add("hidden");
  document.querySelector("#feedback").className = "feedback hidden";
  document.querySelector("#questionCard").className = "content-card question-card";
  const control = document.querySelector("#answerControl");
  const options = question.options || [];
  if (question.type === "multiple_choice") {
    control.innerHTML = `<p>${t("selectAnswer")}</p>${options.map(option => optionMarkup(option, "radio")).join("")}`;
  } else if (question.type === "checkboxes") {
    control.innerHTML = `<p>${t("selectAll")}</p>${options.map(option => optionMarkup(option, "checkbox")).join("")}`;
  } else if (question.type === "dropdown") {
    control.innerHTML = `<select id="dropdownAnswer"><option value="">${t("selectAnswer")}</option>${options.map(option => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.label)}</option>`).join("")}</select>`;
  } else if (question.type === "ordering") {
    control.innerHTML = `<p>${t("arrangeOrder")}</p><div id="orderingList" class="ordering-list">${options.map(option => `<div class="ordering-item" draggable="true" data-id="${escapeHtml(option.id)}"><span class="drag-handle">⋮⋮</span><b>${escapeHtml(option.label)}</b><span class="order-buttons"><button type="button" data-move="up" aria-label="${t("moveUp")}">↑</button><button type="button" data-move="down" aria-label="${t("moveDown")}">↓</button></span></div>`).join("")}</div>`;
    enableOrdering();
  } else {
    control.innerHTML = `<textarea id="writtenAnswer" rows="4" placeholder="${t("answerPlaceholder")}" required></textarea>`;
  }
  answerForm.classList.remove("hidden");
  answerForm.querySelector(".primary-button").classList.remove("hidden");
  updateProgress(questionNumber);
}

function collectAnswer() {
  if (currentQuestion.type === "multiple_choice") return document.querySelector('input[name="answerOption"]:checked')?.value || "";
  if (currentQuestion.type === "checkboxes") return [...document.querySelectorAll('input[name="answerOption"]:checked')].map(input => input.value);
  if (currentQuestion.type === "dropdown") return document.querySelector("#dropdownAnswer").value;
  if (currentQuestion.type === "ordering") return [...document.querySelectorAll("#orderingList .ordering-item")].map(item => item.dataset.id);
  return document.querySelector("#writtenAnswer").value.trim();
}

function renderLesson(data) {
  const { lesson, question } = data;
  document.querySelector("#lessonTitle").textContent = lesson.lesson_title;
  document.querySelector("#sideTitle").textContent = lesson.lesson_title;
  document.querySelector("#levelBadge").textContent = lesson.detected_level;
  document.querySelector("#explanation").textContent = lesson.explanation;
  document.querySelector("#exampleProblem").textContent = lesson.worked_example.problem;
  document.querySelector("#exampleSteps").innerHTML = lesson.worked_example.steps.map(step => `<li>${escapeHtml(step)}</li>`).join("");
  document.querySelector("#exampleAnswer").dataset.answer = lesson.worked_example.answer;
  document.querySelector("#exampleAnswer").textContent = `${t("answer")}: ${lesson.worked_example.answer}`;
  const tips = lesson.teacher_tips || [];
  const exceptions = lesson.exceptions || [];
  document.querySelector("#teacherTips").innerHTML = tips.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  document.querySelector("#exceptionsList").innerHTML = exceptions.length
    ? exceptions.map(item => `<li>${escapeHtml(item)}</li>`).join("")
    : `<li>${t("noExceptions")}</li>`;
  document.querySelector("#conceptList").innerHTML = lesson.concepts.map(item => `<div class="concept">${escapeHtml(item.name)}</div>`).join("");
  renderMastery(lesson.concepts.map(item => ({ concept: item.name, attempts: 0, average_score: 0 })));
  currentQuestion = question;
  questionResults = [];
  document.querySelector("#startTestCard").classList.remove("hidden");
  document.querySelector("#questionCard").classList.add("hidden");
  document.querySelector("#testProgress").classList.add("hidden");
  document.querySelector("#testSummary").classList.add("hidden");
}

function escapeHtml(value) {
  const node = document.createElement("div");
  node.textContent = value;
  return node.innerHTML;
}

uploadForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (imageInput.files.length > 4) return showError("Please choose no more than four photos.");
  if (!studyGoal.value.trim() && imageInput.files.length === 0) {
    return showError(selectedLanguage === "German" ? "Beschreibe dein Lernziel oder füge Material hinzu." : "Describe your study goal or add material.");
  }
  uploadForm.querySelector("button").disabled = true;
  uploadView.classList.add("hidden");
  lessonView.classList.remove("hidden");
  document.querySelector("#lessonContent").classList.add("hidden");
  document.querySelector("#loading").classList.remove("hidden");
  try {
    const formData = new FormData(uploadForm);
    formData.append("language", selectedLanguage);
    const response = await fetch("/api/analyze", { method: "POST", body: formData });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    sessionId = data.session_id;
    renderLesson(data);
    document.querySelector("#loading").classList.add("hidden");
    document.querySelector("#lessonContent").classList.remove("hidden");
  } catch (error) {
    lessonView.classList.add("hidden");
    uploadView.classList.remove("hidden");
    showError(error.message || "Could not start the lesson.");
  } finally {
    uploadForm.querySelector("button").disabled = false;
  }
});

document.querySelector("#hintButton").addEventListener("click", () => document.querySelector("#hint").classList.toggle("hidden"));
document.querySelector("#newLesson").addEventListener("click", () => location.reload());
document.querySelector("#restartTest").addEventListener("click", () => location.reload());
document.querySelector("#startTest").addEventListener("click", () => {
  document.querySelector("#startTestCard").classList.add("hidden");
  document.querySelector("#testProgress").classList.remove("hidden");
  renderQuestion(currentQuestion);
  document.querySelector("#testProgress").scrollIntoView({ behavior: "smooth", block: "start" });
});

function renderSummary(data) {
  const summary = data.summary || {};
  const mastery = data.progress.mastery || [];
  document.querySelector("#questionCard").classList.add("hidden");
  document.querySelector("#testProgress").classList.add("hidden");
  document.querySelector("#testSummary").classList.remove("hidden");
  document.querySelector("#summaryScore").innerHTML = `${data.progress.average_score}<small>/100</small>`;
  document.querySelector("#summaryOverall").textContent = summary.overall || "";
  document.querySelector("#summaryChart").innerHTML = mastery.map(item => {
    const score = item.attempts ? item.average_score : 0;
    const status = score >= 80 ? "strong" : score >= 55 ? "developing" : "weak";
    return `<div class="chart-row"><div><span>${escapeHtml(item.concept)}</span><b>${score}%</b></div><div class="chart-track"><i class="${status}" style="width:${Math.max(score, 3)}%"></i></div></div>`;
  }).join("");
  const weaknesses = summary.weaknesses?.length ? summary.weaknesses : mastery.filter(item => item.average_score < 80).map(item => item.concept);
  document.querySelector("#summaryWeaknesses").innerHTML = weaknesses.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  document.querySelector("#summaryNextSteps").innerHTML = (summary.next_steps || []).map(item => `<li>${escapeHtml(item)}</li>`).join("");
  document.querySelector("#testSummary").scrollIntoView({ behavior: "smooth", block: "start" });
}

answerForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = answerForm.querySelector(".primary-button");
  const submittedAnswer = collectAnswer();
  if (submittedAnswer === "" || (Array.isArray(submittedAnswer) && submittedAnswer.length === 0)) {
    showError(selectedLanguage === "German" ? "Bitte wähle oder schreibe eine Antwort." : "Please choose or write an answer.");
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, answer: submittedAnswer, language: selectedLanguage })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    nextQuestion = data.next_question;
    const feedback = document.querySelector("#feedback");
    const correct = data.evaluation.is_correct;
    questionResults.push(correct);
    updateProgress(Math.min(questionResults.length + 1, testTotal));
    document.querySelector("#questionCard").classList.add(correct ? "result-correct" : "result-wrong");
    document.querySelectorAll("#answerControl input, #answerControl select, #answerControl textarea, #answerControl button").forEach(control => { control.disabled = true; });
    feedback.className = `feedback ${correct ? "correct" : "incorrect"}`;
    const continueLabel = data.complete ? t("viewResults") : t("nextQuestion");
    feedback.innerHTML = `<strong>${correct ? t("correctTitle") : t("incorrectTitle")}</strong><div>${escapeHtml(data.evaluation.feedback)}</div>${data.evaluation.correction ? `<div><b>${t("correction")}:</b> ${escapeHtml(data.evaluation.correction)}</div>` : ""}${data.evaluation.teacher_tip ? `<div class="feedback-note"><b>${t("tip")}:</b> ${escapeHtml(data.evaluation.teacher_tip)}</div>` : ""}${data.evaluation.exception_note ? `<div class="feedback-note exception"><b>${t("exceptionNote")}:</b> ${escapeHtml(data.evaluation.exception_note)}</div>` : ""}<button class="next-button" type="button">${continueLabel}</button>`;
    button.classList.add("hidden");
    document.querySelector("#score").textContent = data.progress.average_score;
    document.querySelector("#answered").textContent = `${data.progress.answered} ${data.progress.answered === 1 ? t("question") : t("questions")} ${t("answered")}`;
    renderMastery(data.progress.mastery);
    feedback.querySelector("button").addEventListener("click", () => {
      if (data.complete) {
        renderSummary(data);
      } else {
        renderQuestion(nextQuestion);
        document.querySelector("#testProgress").scrollIntoView({ behavior: "smooth", block: "start" });
      }
    });
  } catch (error) {
    showError(error.message || "Could not check your answer.");
  } finally {
    button.disabled = false;
  }
});

const chatPanel = document.querySelector("#chatPanel");
const chatMessages = document.querySelector("#chatMessages");
const chatForm = document.querySelector("#chatForm");
const chatInput = document.querySelector("#chatInput");

document.querySelector("#chatToggle").addEventListener("click", () => {
  chatPanel.classList.remove("hidden");
  chatInput.focus();
});
document.querySelector("#chatClose").addEventListener("click", () => chatPanel.classList.add("hidden"));
document.querySelectorAll(".chat-suggestions button").forEach(button => button.addEventListener("click", () => {
  chatInput.value = button.textContent;
  chatForm.requestSubmit();
}));

function addChatMessage(text, role, extraClass = "") {
  const message = document.createElement("div");
  message.className = `message ${role === "student" ? "student-message" : "tutor-message"} ${extraClass}`;
  message.textContent = text;
  chatMessages.appendChild(message);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  return message;
}

chatForm.addEventListener("submit", async event => {
  event.preventDefault();
  const message = chatInput.value.trim();
  if (!message || !sessionId) return;
  addChatMessage(message, "student");
  chatInput.value = "";
  const typing = addChatMessage(t("thinking"), "tutor", "typing-message");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message, language: selectedLanguage })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    typing.remove();
    addChatMessage(data.reply, "tutor");
    renderMastery(data.mastery);
  } catch (error) {
    typing.remove();
    addChatMessage("I couldn't respond just now. Please try again.", "tutor");
    showError(error.message || "Tutor chat failed.");
  }
});

import {escapeHtml} from "./js/dom.js";
import {selectedLanguage, t} from "./js/i18n.js";
import {answerSymbols} from "./js/symbols.js";

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
let testTotal = 5;
let hintUsed = false;
let answerRetryCount = 0;
let currentSubject = "Mathematics";
const MATH_SUBJECTS = new Set(["Mathematics", "Physics", "Chemistry"]);
const MATH_DELIMITERS = [
  { left: "$$", right: "$$", display: true },
  { left: "\\[", right: "\\]", display: true },
  { left: "\\(", right: "\\)", display: false },
];

// Typeset any LaTeX inside an element after its text/HTML has been set.
// Harmless for non-math subjects: without delimiters, KaTeX leaves the text untouched.
function renderMath(root) {
  if (!root || typeof window.renderMathInElement !== "function") return;
  try {
    window.renderMathInElement(root, {
      delimiters: MATH_DELIMITERS,
      throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option"],
    });
  } catch (_) { /* never let a formatting glitch break the lesson */ }
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

applyTranslations();

function renderMastery(items = []) {
  const list = document.querySelector("#masteryList");
  if (!items.length) {
    list.innerHTML = `<div class="mastery-item"><b>${t("noMastery")}</b></div>`;
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

function symbolKeyboardMarkup() {
  const label = t("symbols");
  return `<div class="symbol-keyboard" aria-label="${label}"><small>${label}</small><div>${answerSymbols.map(([visible, value, title]) => `<button type="button" data-symbol="${value}" title="${title}">${visible}</button>`).join("")}</div></div>`;
}

function enableSymbolKeyboard() {
  const textarea = document.querySelector("#writtenAnswer");
  const keyboard = document.querySelector(".symbol-keyboard");
  if (!textarea || !keyboard) return;
  keyboard.addEventListener("click", event => {
    const button = event.target.closest("button[data-symbol]");
    if (!button) return;
    const symbol = button.dataset.symbol;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    textarea.setRangeText(symbol, start, end, "end");
    if (symbol.endsWith("()")) textarea.setSelectionRange(start + symbol.length - 1, start + symbol.length - 1);
    textarea.focus();
  });
}

function renderQuestion(question) {
  currentQuestion = question;
  hintUsed = false;
  answerRetryCount = 0;
  const neutralConfidence = answerForm.querySelector('input[name="responseConfidence"][value="50"]');
  if (neutralConfidence) neutralConfidence.checked = true;
  const questionNumber = questionResults.length + 1;
  document.querySelector("#questionNumber").textContent = String(questionNumber).padStart(2, "0");
  document.querySelector("#questionPrompt").textContent = question.prompt;
  document.querySelector("#difficulty").textContent = `${t("level")} ${question.difficulty}`;
  document.querySelector("#hint").textContent = question.hint;
  document.querySelector("#hint").classList.add("hidden");
  document.querySelector("#hintListenControl")?.classList.add("hidden");
  document.querySelector("#feedback").className = "feedback hidden";
  document.querySelector("#feedbackListenControl")?.classList.add("hidden");
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
    const showSymbols = MATH_SUBJECTS.has(currentSubject);
    control.innerHTML = `<textarea id="writtenAnswer" rows="4" placeholder="${t("answerPlaceholder")}" required></textarea>${showSymbols ? symbolKeyboardMarkup() : ""}`;
    if (showSymbols) enableSymbolKeyboard();
  }
  document.querySelector("#answerMicrophoneControl")?.classList.toggle("hidden", !document.querySelector("#writtenAnswer"));
  answerForm.classList.remove("hidden");
  answerForm.querySelector(".primary-button").classList.remove("hidden");
  renderMath(document.querySelector("#questionCard"));
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
  if (data.subject) currentSubject = data.subject;
  testTotal = Number(data.test_total || 5);
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
  renderMath(lessonView);
}

uploadForm.addEventListener("submit", async event => {
  event.preventDefault();
  if (imageInput.files.length > 4) return showError(t("photoLimit"));
  if (!studyGoal.value.trim() && imageInput.files.length === 0) {
    return showError(t("goalRequired"));
  }
  uploadForm.querySelector("button").disabled = true;
  uploadView.classList.add("hidden");
  lessonView.classList.remove("hidden");
  document.querySelector("#lessonContent").classList.add("hidden");
  document.querySelector("#loading").classList.remove("hidden");
  try {
    const formData = new FormData(uploadForm);
    currentSubject = uploadForm.querySelector('input[name="subject"]:checked')?.value || currentSubject;
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
    showError(error.message || t("lessonStartFailed"));
  } finally {
    uploadForm.querySelector("button").disabled = false;
  }
});

document.querySelector("#hintButton").addEventListener("click", () => {
  hintUsed = true;
  const hint = document.querySelector("#hint");
  hint.classList.toggle("hidden");
  document.querySelector("#hintListenControl")?.classList.toggle("hidden", hint.classList.contains("hidden"));
});
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
  const practice = data.practice_results;
  const mastery = practice ? practice.mastery_changes.map(item => ({
    concept: item.concept, attempts: 1, average_score: item.after
  })) : (data.progress.mastery || []);
  document.querySelector("#questionCard").classList.add("hidden");
  document.querySelector("#testProgress").classList.add("hidden");
  document.querySelector("#testSummary").classList.remove("hidden");
  document.querySelector("#summaryScore").innerHTML = `${data.progress.average_score}<small>/100</small>`;
  document.querySelector("#summaryOverall").textContent = practice?.recommended_next_action || summary.overall || "";
  document.querySelector("#summaryChart").innerHTML = mastery.map(item => {
    const score = item.attempts ? item.average_score : 0;
    const status = score >= 80 ? "strong" : score >= 55 ? "developing" : "weak";
    return `<div class="chart-row"><div><span>${escapeHtml(item.concept)}</span><b>${score}%</b></div><div class="chart-track"><i class="${status}" style="width:${Math.max(score, 3)}%"></i></div></div>`;
  }).join("");
  const weaknesses = practice ? practice.concepts_still_weak : (summary.weaknesses?.length ? summary.weaknesses : mastery.filter(item => item.average_score < 80).map(item => item.concept));
  document.querySelector("#summaryWeaknesses").innerHTML = weaknesses.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  const nextSteps = practice ? [
    practice.recommended_next_action,
    practice.next_recommended_review_date ? `Next review: ${practice.next_recommended_review_date}` : ""
  ].filter(Boolean) : (summary.next_steps || []);
  document.querySelector("#summaryNextSteps").innerHTML = nextSteps.map(item => `<li>${escapeHtml(item)}</li>`).join("");
  renderMath(document.querySelector("#testSummary"));
  document.querySelector("#testSummary").scrollIntoView({ behavior: "smooth", block: "start" });
}

answerForm.addEventListener("submit", async event => {
  event.preventDefault();
  const button = answerForm.querySelector(".primary-button");
  const submittedAnswer = collectAnswer();
  if (submittedAnswer === "" || (Array.isArray(submittedAnswer) && submittedAnswer.length === 0)) {
    showError(t("answerRequired"));
    return;
  }
  button.disabled = true;
  try {
    const response = await fetch("/api/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        answer: submittedAnswer,
        hints_used: hintUsed,
        retry_count: answerRetryCount,
        response_confidence: Number(answerForm.querySelector('input[name="responseConfidence"]:checked')?.value || 50)
      })
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
    feedback.innerHTML = `<div id="feedbackSpeechText"><strong>${correct ? t("correctTitle") : t("incorrectTitle")}</strong><div class="feedback-steps">${escapeHtml(data.evaluation.feedback)}</div>${data.evaluation.correction ? `<div class="feedback-steps"><b>${t("correction")}:</b>\n${escapeHtml(data.evaluation.correction)}</div>` : ""}${data.evaluation.teacher_tip ? `<div class="feedback-note"><b>${t("tip")}:</b> ${escapeHtml(data.evaluation.teacher_tip)}</div>` : ""}${data.evaluation.exception_note ? `<div class="feedback-note exception"><b>${t("exceptionNote")}:</b> ${escapeHtml(data.evaluation.exception_note)}</div>` : ""}</div><button class="next-button" type="button">${continueLabel}</button>`;
    renderMath(feedback);
    document.querySelector("#feedbackListenControl")?.classList.remove("hidden");
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
    answerRetryCount += 1;
    showError(error.message || t("answerFailed"));
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
  if (role === "tutor" && !extraClass.includes("typing-message")) message.dataset.speechListenAuto = "";
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
      body: JSON.stringify({ session_id: sessionId, message })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    typing.remove();
    addChatMessage(data.reply, "tutor");
    renderMastery(data.mastery);
  } catch (error) {
    typing.remove();
    addChatMessage(t("chatFailed"), "tutor");
    showError(error.message || t("chatError"));
  }
});

const bootstrapElement = document.querySelector("#lessonBootstrap");
const lessonBootstrap = bootstrapElement ? JSON.parse(bootstrapElement.textContent) : null;
if (lessonBootstrap) {
  sessionId = lessonBootstrap.session_id;
  uploadView.classList.add("hidden");
  lessonView.classList.remove("hidden");
  renderLesson(lessonBootstrap);
}

import {escapeHtml} from "./js/dom.js";
import {renderMath} from "./js/math.js";
import {bindDiagnosisDetails, diagnosisMarkup} from "./js/diagnosis.js";
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
let testTotal = 15;
let testRange = { minimum: 3, maximum: 15, target: 80 };
let lessonConcepts = [];
let latestKnowledge = null;   // the knowledge gate's latest view, from the last answer
let hintUsed = false;
let answerRetryCount = 0;
let currentSubject = "Mathematics";
// Which subjects get the on-screen symbol keyboard when typing an answer. Rendering
// is no longer subject-gated - math.js decides per span - but offering a student a
// palette of integrals in a history lesson still makes no sense.
const MATH_SUBJECTS = new Set(["Mathematics", "Physics", "Chemistry"]);
function applyTranslations() {
  // The interface language (and RTL direction) is set authoritatively by the server on
  // <html> in base.html; never override it here from the content language.
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

// The test has no fixed length any more: it runs between testRange.minimum and
// testRange.maximum questions and ends when every concept is known. So the bar shows
// knowledge towards the target, and the steps show the concepts, not question numbers.
function updateProgress(questionNumber, knowledge = latestKnowledge) {
  const concepts = knowledge?.concepts || lessonConcepts.map(name => ({ concept: name, knowledge: 0, status: "untested", known: false }));
  const target = knowledge?.target || testRange.target;
  const average = concepts.length ? concepts.reduce((sum, item) => sum + Math.min(item.knowledge, target), 0) / concepts.length : 0;
  const percent = Math.min(100, Math.round((average / target) * 100));
  const where = knowledge
    ? t("conceptsAtTarget", { known: knowledge.known, total: knowledge.total, target })
    : t("upToQuestions", { max: testRange.maximum });
  document.querySelector("#progressLabel").textContent = `${t("question")} ${questionNumber} · ${where}`;
  document.querySelector("#progressPercent").textContent = `${percent}%`;
  document.querySelector("#progressFill").style.width = `${percent}%`;
  // From the minimum onwards the student may end the test; the summary then says
  // honestly what the answers so far show.
  const finish = document.querySelector("#finishTest");
  if (finish) finish.hidden = !(knowledge && knowledge.answered >= knowledge.minimum && !knowledge.complete);
  document.querySelector("#progressSteps").innerHTML = concepts.map(item => {
    const label = item.status === "known" ? t("knowledgeKnown") : item.status === "learning" ? `${item.knowledge}%` : t("knowledgeUntested");
    return `<span class="concept-chip ${item.status}" title="${escapeHtml(item.concept)}">${escapeHtml(item.concept)} · ${escapeHtml(label)}</span>`;
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

const CLOSED_TYPES = new Set(["multiple_choice", "checkboxes", "dropdown", "ordering"]);

function renderQuestion(question) {
  // The server does the same; this is the belt to its braces: no choices, no ordering task.
  if (CLOSED_TYPES.has(question.type) && !(Array.isArray(question.options) && question.options.length >= 2)) {
    question = { ...question, type: "text", options: [] };
  }
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
  // Pictures come from the student's own pages; the server verified every id and built
  // the URL, so nothing here can be pointed somewhere else.
  const media = Array.isArray(question.media) ? question.media : [];
  const figures = document.querySelector("#questionMedia");
  if (figures) {
    const showAbove = media.length && question.type !== "photo_ordering";
    figures.innerHTML = showAbove ? media.map(item =>
      `<figure class="question-photo"><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.labels || t("photoFromYourPage"))}" loading="lazy"></figure>`).join("") : "";
    figures.classList.toggle("hidden", !showAbove);
  }
  if (question.type === "multiple_choice") {
    control.innerHTML = `<p>${t("selectAnswer")}</p>${options.map(option => optionMarkup(option, "radio")).join("")}`;
  } else if (question.type === "checkboxes") {
    control.innerHTML = `<p>${t("selectAll")}</p>${options.map(option => optionMarkup(option, "checkbox")).join("")}`;
  } else if (question.type === "dropdown") {
    control.innerHTML = `<select id="dropdownAnswer"><option value="">${t("selectAnswer")}</option>${options.map(option => `<option value="${escapeHtml(option.id)}">${escapeHtml(option.label)}</option>`).join("")}</select>`;
  } else if (question.type === "photo_ordering") {
    // Tiles are the student's own scanned diagrams. Same control as text ordering, so
    // dragging and the arrow-button fallback (touch, keyboard) come with it.
    control.innerHTML = `<p>${t("arrangeOrder")}</p><div id="orderingList" class="ordering-list ordering-photos">${media.map((item, index) => `<div class="ordering-item ordering-photo" draggable="true" data-id="${escapeHtml(String(item.block_id))}"><span class="drag-handle">⋮⋮</span><img src="${escapeHtml(item.url)}" alt="${escapeHtml(item.labels || `${t("photoFromYourPage")} ${index + 1}`)}" loading="lazy"><span class="order-buttons"><button type="button" data-move="up" aria-label="${t("moveUp")}">↑</button><button type="button" data-move="down" aria-label="${t("moveDown")}">↓</button></span></div>`).join("")}</div>`;
    enableOrdering();
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
  if (currentQuestion.type === "ordering" || currentQuestion.type === "photo_ordering") return [...document.querySelectorAll("#orderingList .ordering-item")].map(item => item.dataset.id);
  return document.querySelector("#writtenAnswer").value.trim();
}

function renderMedia(lesson) {
  const card = document.querySelector("#mediaCard");
  if (!card) return;
  const videos = Array.isArray(lesson.video_links) ? lesson.video_links : [];
  const images = Array.isArray(lesson.image_media) ? lesson.image_media : [];
  // Studyflix is a German site, so the server only sends that link for German content.
  document.querySelector("#lessonVideos").innerHTML = videos.map(video =>
    `<div class="media-video"><div class="media-video-text"><b>${escapeHtml(video.title)}</b>${video.why ? `<small>${escapeHtml(video.why)}</small>` : ""}</div><span class="media-links"><a href="${escapeHtml(video.youtube)}" target="_blank" rel="noopener noreferrer">▶ YouTube</a>${video.studyflix ? `<a href="${escapeHtml(video.studyflix)}" target="_blank" rel="noopener noreferrer">Studyflix</a>` : ""}</span></div>`
  ).join("");
  // alt describes the picture; the caption says which article it came from. Both used to
  // be the article title, and the caption claimed a licence nobody had checked.
  document.querySelector("#lessonImages").innerHTML = images.map(image =>
    `<figure class="media-image"><img src="${escapeHtml(image.url)}" alt="${escapeHtml(image.alt || image.title)}" loading="lazy" data-media-image><figcaption>${escapeHtml(image.title)}${image.page_url ? ` — <a href="${escapeHtml(image.page_url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(image.source || "Wikipedia")}</a>` : ""}</figcaption></figure>`
  ).join("");
  // A picture that cannot load leaves a broken icon under a confident caption, which
  // reads as "here is your illustration" when there is none. Drop the whole figure.
  document.querySelectorAll("#lessonImages [data-media-image]").forEach(img => {
    img.addEventListener("error", () => {
      img.closest("figure")?.remove();
      const left = document.querySelectorAll("#lessonImages figure").length;
      if (!left && !videos.length) card.classList.add("hidden");
    }, { once: true });
  });
  const hasMedia = videos.length > 0 || images.length > 0;
  card.classList.toggle("hidden", !hasMedia);
  if (hasMedia) {
    document.querySelector("#mediaKicker").textContent = t("exploreMore");
    document.querySelector("#mediaHeading").textContent = t("watchAndSee");
  }
}

function renderLesson(data) {
  const { lesson, question } = data;
  if (data.subject) currentSubject = data.subject;
  testTotal = Number(data.test_total || 15);
  testRange = data.test_range || { minimum: 3, maximum: testTotal, target: 80 };
  lessonConcepts = (lesson.concepts || []).map(item => item.name).filter(Boolean);
  latestKnowledge = data.knowledge || null;
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
  renderMedia(lesson);
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
document.querySelector("#finishTest")?.addEventListener("click", async event => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const response = await fetch("/api/finish", { method: "POST", headers: { "Content-Type": "application/json" },
                                 body: JSON.stringify({ session_id: sessionId }) });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    latestKnowledge = data.progress.knowledge;
    button.hidden = true;
    renderSummary(data);
  } catch (error) {
    showError(error.message || t("answerFailed"));
  } finally {
    button.disabled = false;
  }
});
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
  document.querySelector("#summaryOverall").textContent = [summary.overall, practice?.recommended_next_action].filter(Boolean).join(" ");
  // The knowledge gate's own numbers when it supplied them: knowledge per concept against
  // the target, not the average test score.
  const knowledge = summary.knowledge || data.progress?.knowledge;
  const rows = knowledge?.concepts?.length ? knowledge.concepts.map(item => ({
    concept: item.concept, score: item.knowledge, status: item.known ? "strong" : item.knowledge >= 55 ? "developing" : "weak",
    label: item.known ? t("knowledgeKnown") : item.status === "untested" ? t("knowledgeUntested") : `${item.knowledge}%`,
  })) : mastery.map(item => {
    const score = item.attempts ? item.average_score : 0;
    return { concept: item.concept, score, status: score >= 80 ? "strong" : score >= 55 ? "developing" : "weak", label: `${score}%` };
  });
  document.querySelector("#summaryChart").innerHTML = rows.map(item =>
    `<div class="chart-row"><div><span>${escapeHtml(item.concept)}</span><b>${escapeHtml(item.label)}</b></div><div class="chart-track"><i class="${item.status}" style="width:${Math.max(item.score, 3)}%"></i></div></div>`
  ).join("") + (knowledge ? `<p class="knowledge-target-note">${escapeHtml(t("conceptsAtTarget", { known: knowledge.known, total: knowledge.total, target: knowledge.target }))}</p>` : "");
  const weaknesses = summary.weaknesses?.length ? summary.weaknesses : practice ? practice.concepts_still_weak : mastery.filter(item => item.average_score < 80).map(item => item.concept);
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
    latestKnowledge = data.progress.knowledge || latestKnowledge;
    updateProgress(data.complete ? questionResults.length : questionResults.length + 1, latestKnowledge);
    document.querySelector("#questionCard").classList.add(correct ? "result-correct" : "result-wrong");
    document.querySelectorAll("#answerControl input, #answerControl select, #answerControl textarea, #answerControl button").forEach(control => { control.disabled = true; });
    feedback.className = `feedback ${correct ? "correct" : "incorrect"}`;
    const continueLabel = data.complete ? t("viewResults") : t("nextQuestion");
    feedback.innerHTML = `<div id="feedbackSpeechText"><strong>${correct ? t("correctTitle") : t("incorrectTitle")}</strong><div class="feedback-steps">${escapeHtml(data.evaluation.feedback)}</div>${data.evaluation.correction ? `<div class="feedback-steps"><b>${t("correction")}:</b>\n${escapeHtml(data.evaluation.correction)}</div>` : ""}${data.evaluation.teacher_tip ? `<div class="feedback-note"><b>${t("tip")}:</b> ${escapeHtml(data.evaluation.teacher_tip)}</div>` : ""}${data.evaluation.exception_note ? `<div class="feedback-note exception"><b>${t("exceptionNote")}:</b> ${escapeHtml(data.evaluation.exception_note)}</div>` : ""}</div>${diagnosisMarkup(data.diagnosis)}<button class="next-button" type="button">${continueLabel}</button>`;
    renderMath(feedback);
    bindDiagnosisDetails(feedback);
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

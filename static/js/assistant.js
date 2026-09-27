/* Direct assistant chat.
 *
 * Assistant output is untrusted text. It is escaped first and only then given the small
 * set of tags this file introduces itself, so nothing the model writes can become markup.
 * The renderer below is deliberately tiny for that reason: every construct it supports is
 * one it builds, not one it passes through.
 */
import { escapeHtml } from "./dom.js";
import { t } from "./i18n.js";

const MATH_DELIMITERS = [
  { left: "$$", right: "$$", display: true },
  { left: "\\[", right: "\\]", display: true },
  { left: "$", right: "$", display: false },
  { left: "\\(", right: "\\)", display: false },
];

const state = {
  conversations: [],
  activeId: null,
  sending: false,
  showArchived: false,
  search: "",
};

const el = (id) => document.querySelector(`#${id}`);

function toast(message, tone = "info") {
  const node = el("asstToast");
  node.textContent = message;
  node.className = `toast ${tone}`;
  node.classList.remove("hidden");
  window.setTimeout(() => node.classList.add("hidden"), 4500);
}

async function api(url, { method = "GET", body } = {}) {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(url, options);
  let data = {};
  try { data = await response.json(); } catch (_) { /* non-JSON */ }
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `${t("asstRequestFailed")} (${response.status})`);
  }
  return data;
}

/* ----------------------------------------------------------------- rendering */

function renderInline(text) {
  // `text` is already escaped. Each replacement introduces only tags built here.
  return text
    .replace(/`([^`\n]+)`/g, (_m, code) => `<code>${code}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, (_m, bold) => `<strong>${bold}</strong>`)
    .replace(/(^|[\s(])\*([^*\n]+)\*/g, (_m, lead, italic) => `${lead}<em>${italic}</em>`)
    // Only http(s) becomes a link, and only after escaping, so a "javascript:" URL
    // cannot be produced here. Trailing punctuation is left outside the link.
    .replace(/\bhttps?:\/\/[^\s<]+[^\s<.,;:!?)\]]/g, (url) =>
      `<a href="${url}" target="_blank" rel="noopener noreferrer nofollow">${url}</a>`);
}

function renderMarkdown(source) {
  const lines = String(source || "").split("\n");
  const out = [];
  let paragraph = [];
  let list = null;          // "ul" | "ol"
  let code = null;          // accumulated code-block lines
  let codeLanguage = "";

  const flushParagraph = () => {
    if (paragraph.length) {
      out.push(`<p>${renderInline(paragraph.join("<br>"))}</p>`);
      paragraph = [];
    }
  };
  const flushList = () => {
    if (list) {
      out.push(`</${list}>`);
      list = null;
    }
  };

  for (const raw of lines) {
    const fence = raw.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      if (code === null) {
        flushParagraph(); flushList();
        code = []; codeLanguage = escapeHtml(fence[1] || "");
      } else {
        const label = codeLanguage
          ? `<span class="code-lang">${codeLanguage}</span>` : "";
        out.push(`<pre class="asst-code">${label}<code>${escapeHtml(code.join("\n"))}</code></pre>`);
        code = null; codeLanguage = "";
      }
      continue;
    }
    if (code !== null) { code.push(raw); continue; }

    const line = raw.trimEnd();
    if (!line.trim()) { flushParagraph(); flushList(); continue; }

    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushParagraph(); flushList();
      const level = Math.min(6, heading[1].length + 2);   // "#" renders as <h3>
      out.push(`<h${level}>${renderInline(escapeHtml(heading[2]))}</h${level}>`);
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (bullet || numbered) {
      flushParagraph();
      const wanted = bullet ? "ul" : "ol";
      if (list !== wanted) { flushList(); list = wanted; out.push(`<${wanted}>`); }
      out.push(`<li>${renderInline(escapeHtml((bullet || numbered)[1]))}</li>`);
      continue;
    }
    flushList();
    paragraph.push(escapeHtml(line));
  }
  if (code !== null) {
    // An unterminated fence: render what there is rather than dropping it.
    out.push(`<pre class="asst-code"><code>${escapeHtml(code.join("\n"))}</code></pre>`);
  }
  flushParagraph(); flushList();
  return out.join("\n");
}

function renderMath(root) {
  if (!root || typeof window.renderMathInElement !== "function") return;
  try {
    window.renderMathInElement(root, {
      delimiters: MATH_DELIMITERS, throwOnError: false,
      ignoredTags: ["script", "noscript", "style", "textarea", "pre", "code", "option", "input"],
    });
  } catch (_) { /* a malformed formula must not take the message down */ }
}

function messageNode(message) {
  const article = document.createElement("article");
  article.className = `asst-msg asst-msg-${message.role}`;
  if (message.error) article.classList.add("asst-msg-error");
  article.dataset.id = message.id;

  const who = document.createElement("p");
  who.className = "asst-who";
  who.textContent = message.role === "user" ? t("asstYou") : t("asstAssistant");
  article.appendChild(who);

  const body = document.createElement("div");
  body.className = "asst-body";
  if (message.role === "user") {
    // The learner's own text is shown verbatim, not interpreted as markup.
    body.textContent = message.content;
  } else {
    body.innerHTML = renderMarkdown(message.content);
  }
  article.appendChild(body);

  const notes = [];
  if (message.model) notes.push(escapeHtml(message.model));
  if (message.latency_ms) notes.push(`${Math.round(message.latency_ms)} ms`);
  if (message.context_dropped) {
    notes.push(t("asstContextTrimmed").replace("{count}", message.context_dropped));
  }
  if (notes.length) {
    const meta = document.createElement("p");
    meta.className = "asst-msg-meta";
    meta.innerHTML = notes.join(" · ");
    article.appendChild(meta);
  }
  if (message.role === "assistant" && !message.error) {
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "asst-copy";
    copy.textContent = t("asstCopy");
    copy.addEventListener("click", () => {
      navigator.clipboard?.writeText(message.content).then(
        () => toast(t("asstCopied"), "success"),
        () => toast(t("asstCopyFailed"), "error"));
    });
    article.appendChild(copy);
  }
  renderMath(body);
  return article;
}

function scrollToEnd() {
  const thread = el("asstThread");
  thread.scrollTop = thread.scrollHeight;
}

/* -------------------------------------------------------------- conversations */

function conversationMatches(conversation) {
  if (!state.search) return true;
  return conversation.title.toLowerCase().includes(state.search);
}

function renderList() {
  const list = el("asstList");
  const visible = state.conversations.filter(conversationMatches);
  list.innerHTML = "";
  for (const conversation of visible) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "asst-list-item";
    if (conversation.id === state.activeId) button.classList.add("is-active");
    if (conversation.archived) button.classList.add("is-archived");
    button.dataset.id = conversation.id;
    const title = document.createElement("span");
    title.className = "asst-list-title";
    title.textContent = conversation.title;
    const meta = document.createElement("span");
    meta.className = "asst-list-meta";
    meta.textContent = `${conversation.preset_label} · ${conversation.message_count}`;
    button.append(title, meta);
    button.addEventListener("click", () => openConversation(conversation.id));
    list.appendChild(button);
  }
  el("asstListEmpty").classList.toggle("hidden", visible.length > 0);
}

function setThreadControls(conversation) {
  const hasThread = Boolean(conversation);
  for (const id of ["asstRename", "asstArchive", "asstDelete"]) el(id).hidden = !hasThread;
  el("asstThreadTitle").textContent = conversation ? conversation.title : t("asstAssistant");
  el("asstThreadMeta").textContent = conversation
    ? [conversation.preset_label, conversation.model || t("asstNoModelYet")].join(" · ")
    : "";
  if (conversation) {
    el("asstPreset").value = conversation.preset;
    el("asstArchive").textContent = conversation.archived ? t("asstUnarchive") : t("asstArchive");
  }
}

async function loadConversations() {
  try {
    const data = await api(
      `/api/assistant/conversations${state.showArchived ? "?archived=1" : ""}`);
    state.conversations = data.conversations;
    renderList();
  } catch (error) {
    toast(error.message, "error");
  }
}

function activeConversation() {
  return state.conversations.find((item) => item.id === state.activeId) || null;
}

async function openConversation(conversationId) {
  try {
    const data = await api(`/api/assistant/conversations/${conversationId}`);
    state.activeId = conversationId;
    const existing = state.conversations.findIndex((item) => item.id === conversationId);
    if (existing >= 0) state.conversations[existing] = data.conversation;
    const thread = el("asstThread");
    thread.innerHTML = "";
    for (const message of data.conversation.messages) thread.appendChild(messageNode(message));
    if (!data.conversation.messages.length) thread.appendChild(welcomeNode());
    setThreadControls(data.conversation);
    renderList();
    scrollToEnd();
    el("asstInput").focus();
  } catch (error) {
    toast(error.message, "error");
  }
}

function welcomeNode() {
  const template = el("asstWelcome");
  return template ? template.cloneNode(true) : document.createElement("div");
}

async function newConversation() {
  try {
    const data = await api("/api/assistant/conversations", {
      method: "POST", body: { preset: el("asstPreset").value },
    });
    state.conversations.unshift(data.conversation);
    await openConversation(data.conversation.id);
  } catch (error) {
    toast(error.message, "error");
  }
}

/* --------------------------------------------------------------------- sending */

function pendingNode() {
  const article = document.createElement("article");
  article.className = "asst-msg asst-msg-assistant asst-pending";
  article.innerHTML =
    `<p class="asst-who">${escapeHtml(t("asstAssistant"))}</p>` +
    `<div class="asst-body"><span class="asst-dots"><i></i><i></i><i></i></span></div>`;
  return article;
}

async function send(text) {
  if (state.sending) return;
  const message = text.trim();
  if (!message) return;
  if (!state.activeId) {
    await newConversation();
    if (!state.activeId) return;
  }
  const thread = el("asstThread");
  thread.querySelector(".asst-welcome")?.remove();
  state.sending = true;
  el("asstSend").disabled = true;
  el("asstInput").value = "";
  updateCount();

  thread.appendChild(messageNode({ id: "pending-user", role: "user", content: message }));
  const pending = pendingNode();
  thread.appendChild(pending);
  scrollToEnd();

  try {
    const data = await api(`/api/assistant/conversations/${state.activeId}/messages`, {
      method: "POST", body: { message, deep: el("asstDeep").checked },
    });
    pending.replaceWith(messageNode(data.reply));
    const index = state.conversations.findIndex((item) => item.id === data.conversation.id);
    if (index >= 0) state.conversations[index] = data.conversation;
    else state.conversations.unshift(data.conversation);
    setThreadControls(data.conversation);
    renderList();
  } catch (error) {
    pending.remove();
    // The message is put back in the box rather than lost, so a failure costs nothing
    // but the wait.
    el("asstInput").value = message;
    updateCount();
    toast(error.message, "error");
  } finally {
    state.sending = false;
    el("asstSend").disabled = false;
    scrollToEnd();
    el("asstInput").focus();
  }
}

/* ----------------------------------------------------------------------- setup */

function updateCount() {
  const input = el("asstInput");
  const used = input.value.length;
  el("asstCount").textContent = used > 12000 ? `${used} / ${input.maxLength}` : "";
  input.style.height = "auto";
  input.style.height = `${Math.min(220, input.scrollHeight)}px`;
}

async function renameActive() {
  const conversation = activeConversation();
  if (!conversation) return;
  const title = window.prompt(t("asstRenamePrompt"), conversation.title);
  if (title === null) return;
  try {
    const data = await api(`/api/assistant/conversations/${conversation.id}`, {
      method: "PATCH", body: { title },
    });
    Object.assign(conversation, data.conversation);
    setThreadControls(conversation);
    renderList();
  } catch (error) {
    toast(error.message, "error");
  }
}

async function toggleArchiveActive() {
  const conversation = activeConversation();
  if (!conversation) return;
  try {
    await api(`/api/assistant/conversations/${conversation.id}`, {
      method: "PATCH", body: { archived: !conversation.archived },
    });
    await loadConversations();
    if (!state.showArchived && !conversation.archived) {
      state.activeId = null;
      el("asstThread").innerHTML = "";
      el("asstThread").appendChild(welcomeNode());
      setThreadControls(null);
    }
  } catch (error) {
    toast(error.message, "error");
  }
}

async function deleteActive() {
  const conversation = activeConversation();
  if (!conversation || !window.confirm(t("asstDeleteConfirm"))) return;
  try {
    await api(`/api/assistant/conversations/${conversation.id}`, { method: "DELETE" });
    state.conversations = state.conversations.filter((item) => item.id !== conversation.id);
    state.activeId = null;
    el("asstThread").innerHTML = "";
    el("asstThread").appendChild(welcomeNode());
    setThreadControls(null);
    renderList();
  } catch (error) {
    toast(error.message, "error");
  }
}

function init() {
  const input = el("asstInput");
  el("asstComposer").addEventListener("submit", (event) => {
    event.preventDefault();
    send(input.value);
  });
  input.addEventListener("input", updateCount);
  input.addEventListener("keydown", (event) => {
    // Enter sends, Shift+Enter makes a new line: what a chat box is expected to do.
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      send(input.value);
    }
  });
  el("asstNew").addEventListener("click", newConversation);
  el("asstRename").addEventListener("click", renameActive);
  el("asstArchive").addEventListener("click", toggleArchiveActive);
  el("asstDelete").addEventListener("click", deleteActive);
  el("asstSearch").addEventListener("input", (event) => {
    state.search = event.target.value.trim().toLowerCase();
    renderList();
  });
  el("asstShowArchived").addEventListener("change", (event) => {
    state.showArchived = event.target.checked;
    loadConversations();
  });
  el("asstPreset").addEventListener("change", async (event) => {
    const conversation = activeConversation();
    if (!conversation) return;
    try {
      const data = await api(`/api/assistant/conversations/${conversation.id}`, {
        method: "PATCH", body: { preset: event.target.value },
      });
      Object.assign(conversation, data.conversation);
      setThreadControls(conversation);
      renderList();
    } catch (error) {
      toast(error.message, "error");
    }
  });
  document.addEventListener("click", (event) => {
    const suggestion = event.target.closest(".asst-suggestion");
    if (suggestion) send(suggestion.textContent);
  });
  loadConversations();
  updateCount();
}

document.addEventListener("DOMContentLoaded", init);

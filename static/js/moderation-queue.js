/* Reviewer queue for community moderation.
 *
 * Reviewer-only, so unlike the author-facing views this deliberately shows everything:
 * per-dimension statuses, the raw-character measurements, the quoted spans and the
 * policy rationale. A reviewer cannot check a decision they cannot see.
 *
 * Every value rendered here is untrusted - quoted spans come from submitted content and
 * evidence summaries come from a model - so it all goes through escapeHtml. Nothing on
 * this page is ever inserted as markup.
 */
import { escapeHtml } from "./dom.js";

const DECISIONS = [
  ["allow", "Publish"],
  ["revision_required", "Ask for changes"],
  ["reject", "Reject"],
];

function toast(message, tone = "info") {
  const element = document.querySelector("#modToast");
  element.textContent = message;
  element.className = `toast ${tone}`;
  element.classList.remove("hidden");
  window.setTimeout(() => element.classList.add("hidden"), 4000);
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
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}

function dimensionsHtml(dimensions, labels) {
  const entries = Object.entries(dimensions || {});
  // Problems first: a reviewer should not have to scan a row of passes to find the flag.
  const rank = { flag: 0, unknown: 1, pass: 2, not_applicable: 3 };
  entries.sort((a, b) => (rank[a[1]] ?? 9) - (rank[b[1]] ?? 9) || a[0].localeCompare(b[0]));
  return entries.map(([name, status]) => {
    const label = (labels || {})[name] || {};
    return `<span class="mod-dimension" data-status="${escapeHtml(status)}" title="${escapeHtml(name)}">${
      escapeHtml(label.dimension || name)}: ${escapeHtml(label.status || status)}</span>`;
  }).join("");
}

function listHtml(title, items) {
  if (!items || !items.length) return "";
  return `<div class="mod-section"><h4>${escapeHtml(title)}</h4><ul>${
    items.map(item => `<li>${escapeHtml(String(item))}</li>`).join("")}</ul></div>`;
}

function quotesHtml(quotes) {
  if (!quotes || !quotes.length) return "";
  return `<div class="mod-section"><h4>Cited spans</h4><ul>${quotes.map(item =>
    `<li><b>${escapeHtml(item.dimension)}</b>: “${escapeHtml(item.quote)}”</li>`).join("")}</ul></div>`;
}

function signalsHtml(signals) {
  if (!signals) return "";
  const evidence = signals.evidence || [];
  const headline = `risk ${signals.obfuscation_risk ?? 0} (${escapeHtml(signals.risk_level || "low")})`;
  return `<div class="mod-section"><h4>Raw-character measurements</h4>
    <ul><li>${escapeHtml(headline)}</li>${
      evidence.map(item => `<li>${escapeHtml(String(item))}</li>`).join("")}</ul></div>`;
}

function contentHtml(record) {
  const content = record.content;
  if (!content) return "";
  const cards = (content.cards || []).map(card =>
    `<li><b>${escapeHtml(String(card.front ?? ""))}</b> — ${escapeHtml(String(card.back ?? ""))}</li>`
  ).join("");
  const stale = record.content_is_current === false
    ? `<p class="mod-stale">This content changed after it was checked. It must be checked again before it can be published.</p>`
    : "";
  return `<div class="mod-section"><h4>Submitted content (version ${escapeHtml(String(content.version))})</h4>
    ${stale}
    <div class="mod-content">
      <p><b>${escapeHtml(content.title || "")}</b></p>
      ${content.description ? `<p>${escapeHtml(content.description)}</p>` : ""}
      <ul>${cards}</ul>
    </div></div>`;
}

function recordHtml(record) {
  const set = record.set || {};
  const reports = record.reports || {};
  return `
    <article class="mod-record" data-record="${record.id}">
      <header>
        <div>
          <h3>${escapeHtml(set.title || "(untitled)")}</h3>
          <p class="mod-meta">${escapeHtml(set.subject || "no subject")}
            · ${escapeHtml(set.grade ? `grade ${set.grade}` : "no level")}
            · ${escapeHtml(set.language || "")}
            · publication state: ${escapeHtml(set.status || "")}
            · ${reports.total || 0} report(s), ${reports.safety || 0} about safety${
              (record.report_reasons || []).length
                ? " — " + record.report_reasons.map(item =>
                    `${escapeHtml(item.label)} ×${item.count}`).join(", ")
                : ""}</p>
        </div>
        <span class="mod-decision" data-decision="${escapeHtml(record.decision)}">${escapeHtml(record.decision)}</span>
      </header>
      <p class="mod-meta">confidence ${record.confidence} · evidence ${escapeHtml(record.evidence_sufficiency || "")}
        · ${escapeHtml(record.model || "no model")}${record.escalated ? " (escalated)" : ""}
        · ${escapeHtml(record.policy_version || "")} / ${escapeHtml(record.prompt_version || "")}</p>
      ${record.evidence_summary ? `<p>${escapeHtml(record.evidence_summary)}</p>` : ""}
      <div class="mod-dimensions">${dimensionsHtml(record.dimensions, record.dimension_labels)}</div>
      ${listHtml("Reasons", record.reason_labels || record.reason_codes)}
      ${quotesHtml(record.quotes)}
      ${signalsHtml(record.signals)}
      ${listHtml("Why the policy decided this", record.rationale)}
      <div id="content-${record.id}"></div>
      <div class="mod-section">
        <label class="visually-hidden" for="note-${record.id}">Reviewer note</label>
        <textarea id="note-${record.id}" rows="2" maxlength="500" placeholder="Note (stored with the decision)"></textarea>
        <div class="report-actions">
          <button type="button" class="ghost-button" data-load-content="${record.id}">Show submitted content</button>
          ${DECISIONS.map(([value, label]) =>
            `<button type="button" class="${value === "allow" ? "primary-button" : "ghost-button"}" data-decide="${value}" data-record="${record.id}">${label}</button>`
          ).join("")}
        </div>
      </div>
    </article>`;
}

async function loadContent(recordId) {
  const target = document.querySelector(`#content-${recordId}`);
  if (!target || target.dataset.loaded === "true") return;
  try {
    const data = await api(`/api/moderation/records/${recordId}`);
    target.innerHTML = contentHtml(data.record);
    target.dataset.loaded = "true";
  } catch (error) {
    toast(error.message, "error");
  }
}

async function decide(recordId, decision) {
  const note = document.querySelector(`#note-${recordId}`)?.value.trim() || "";
  if (decision === "reject" && !window.confirm("Reject this submission?")) return;
  try {
    await api(`/api/moderation/records/${recordId}/decide`, {
      method: "POST", body: { decision, note },
    });
    document.querySelector(`.mod-record[data-record="${recordId}"]`)?.remove();
    toast(`Recorded: ${decision}.`, "success");
    if (!document.querySelector(".mod-record")) {
      document.querySelector("#modEmpty").classList.remove("hidden");
    }
  } catch (error) {
    // A 409 means the content changed after the reviewer opened it; the server refuses
    // to apply an approval to text nobody read.
    toast(error.message, "error");
  }
}

async function load() {
  const loading = document.querySelector("#modLoading");
  const queue = document.querySelector("#modQueue");
  try {
    const data = await api("/api/moderation/queue");
    loading.hidden = true;
    loading.classList.add("hidden");
    if (!data.records.length) {
      document.querySelector("#modEmpty").classList.remove("hidden");
      return;
    }
    queue.innerHTML = data.records.map(recordHtml).join("");
  } catch (error) {
    loading.hidden = true;
    loading.classList.add("hidden");
    toast(error.message, "error");
  }
}

document.addEventListener("click", event => {
  const decideButton = event.target.closest("[data-decide]");
  if (decideButton) {
    decide(Number(decideButton.dataset.record), decideButton.dataset.decide);
    return;
  }
  const contentButton = event.target.closest("[data-load-content]");
  if (contentButton) loadContent(Number(contentButton.dataset.loadContent));
});

load();

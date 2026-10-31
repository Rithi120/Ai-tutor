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

/* ------------------------------------------------------------ reader reports */

const OUTCOME_TONE = {
  report_valid: "reject", possible_issue: "revision_required",
  report_not_supported: "allow", review_failed: "review",
};
const REPORT_ACTIONS = [
  ["confirm_issue", "Confirm issue", "Mark the selected reports as a genuine issue."],
  ["dismiss", "Dismiss as unsupported", "Close the reports without changing the set."],
  ["resolve", "Mark resolved", "Close the reports; nothing else changes."],
  ["rereview", "Check again", "Run the automatic re-review again (counts against the limit)."],
];

function affectedHtml(review) {
  const affected = review.affected;
  const entry = review.entry_text;
  const parts = [];
  if (affected && affected.entry) {
    parts.push(`<li>Card ${escapeHtml(String(affected.entry))} (${escapeHtml(affected.side || "")}): <mark class="entry-warning-mark">${escapeHtml(affected.text || "")}</mark></li>`);
  }
  if (entry) {
    parts.push(`<li>Reported card ${escapeHtml(String(entry.entry))}: <b>${escapeHtml(entry.front)}</b> — ${escapeHtml(entry.back)}</li>`);
  }
  if (review.suggested_correction) parts.push(`<li>Suggested correction: <b>${escapeHtml(review.suggested_correction)}</b></li>`);
  return parts.length ? `<ul>${parts.join("")}</ul>` : "";
}

function reviewHtml(review) {
  const stale = review.current ? "" : `<p class="mod-stale">Made about version ${escapeHtml(String(review.content_version))}; the set has changed since.</p>`;
  return `<div class="mod-section mod-finding">
    <p><span class="mod-decision" data-decision="${escapeHtml(OUTCOME_TONE[review.outcome] || "review")}">${escapeHtml(review.outcome_label || review.status)}</span>
      <span class="mod-meta">${escapeHtml(review.track)} check · ${escapeHtml(review.status)} · attempt ${escapeHtml(String(review.attempts))}
      · ${escapeHtml(review.model || "no model")} · ${escapeHtml(String(review.provider_calls))} call(s), ${escapeHtml(String(review.total_tokens))} tokens
      ${review.human_review_recommended ? " · human review recommended" : ""}${review.restricted ? " · restricted by the moderation policy" : ""}</span></p>
    ${stale}
    ${review.explanation ? `<p>${escapeHtml(review.explanation)}</p>` : ""}
    ${review.failure ? `<p class="mod-meta">Failure: ${escapeHtml(review.failure)}</p>` : ""}
    ${affectedHtml(review)}
  </div>`;
}

function reportSetHtml(group) {
  const set = group.set;
  const open = group.reports.filter(report => report.status === "open");
  const latest = (group.reviews || [])[0];
  const affected = latest && latest.affected ? latest.affected : {};
  const reports = group.reports.map(report => `<li>
      <label><input type="checkbox" data-report-select="${set.id}" value="${report.id}" ${report.status === "open" ? "checked" : "disabled"}>
      <b>${escapeHtml(report.reason_label)}</b></label>
      ${report.entry ? ` · card ${escapeHtml(String(report.entry.entry))}: ${escapeHtml(report.entry.front)} — ${escapeHtml(report.entry.back)}` : ""}
      · by ${escapeHtml(report.reporter || "unknown")} · v${escapeHtml(String(report.content_version))}
      · ${escapeHtml(report.status)}${report.resolution ? ` (${escapeHtml(report.resolution)})` : ""}
      ${report.detail ? `<br><span class="mod-meta">“${escapeHtml(report.detail)}”</span>` : ""}</li>`).join("");
  const alerts = (group.notifications || []).map(row => `<li>${escapeHtml(row.label)} — <b>${escapeHtml(row.status)}</b>
      (${escapeHtml(String(row.attempts))} attempt(s))${row.last_error ? `: ${escapeHtml(row.last_error)}` : ""}
      ${row.status !== "sent" ? `<button type="button" class="ghost-button subtle-button" data-alert-retry="${row.id}">Send again</button>` : ""}</li>`).join("");
  const history = (group.history || []).map(event =>
    `<li>${escapeHtml(event.created_at.slice(0, 16).replace("T", " "))} · ${escapeHtml(event.actor)} · ${escapeHtml(event.action)}${event.note ? ` — ${escapeHtml(event.note)}` : ""}</li>`).join("");
  return `<article class="mod-record" data-report-set="${set.id}">
    <header>
      <div>
        <h3><a href="${escapeHtml(set.public_url)}" target="_blank" rel="noopener">${escapeHtml(set.title || "(untitled)")}</a> <span class="mod-meta">#${set.id}</span></h3>
        <p class="mod-meta">${escapeHtml(set.set_kind)} · version ${escapeHtml(String(set.version ?? "—"))} · ${escapeHtml(String(set.card_count))} cards
          · publication state ${escapeHtml(set.status)} · safety ${escapeHtml(set.moderation_decision)}
          ${set.front_language || set.back_language ? ` · ${escapeHtml(set.front_language || "?")} → ${escapeHtml(set.back_language || "?")}` : ""}</p>
      </div>
      <span class="mod-decision">${open.length} open</span>
    </header>
    <div class="mod-section"><h4>Reports</h4><ul>${reports}</ul></div>
    ${(group.reviews || []).length ? `<div class="mod-section"><h4>Re-review findings</h4>${group.reviews.map(reviewHtml).join("")}</div>` : ""}
    ${alerts ? `<div class="mod-section"><h4>Email alerts</h4><ul>${alerts}</ul></div>` : ""}
    <div class="mod-section">
      <label class="visually-hidden" for="rnote-${set.id}">Note</label>
      <textarea id="rnote-${set.id}" rows="2" maxlength="500" placeholder="Note (stored in the history)"></textarea>
      <div class="report-actions">
        ${REPORT_ACTIONS.map(([value, label, title]) => `<button type="button" class="ghost-button" title="${escapeHtml(title)}" data-report-action="${value}" data-set="${set.id}" ${open.length ? "" : "disabled"}>${label}</button>`).join("")}
        ${set.status === "approved" ? `<button type="button" class="ghost-button danger" data-report-action="restrict" data-set="${set.id}">Restrict</button>` : ""}
        ${set.status === "hidden" ? `<button type="button" class="ghost-button" data-report-action="restore" data-set="${set.id}">Restore</button>` : ""}
      </div>
      <details class="mod-correct"><summary>Correct one entry</summary>
        <div class="field-row">
          <label>Card <input type="number" min="1" max="${escapeHtml(String(set.card_count))}" id="rentry-${set.id}" value="${escapeHtml(String(affected.entry || ""))}"></label>
          <label>Side <select id="rside-${set.id}">${["back", "front", "explanation"].map(side =>
            `<option value="${side}" ${affected.side === side ? "selected" : ""}>${side}</option>`).join("")}</select></label>
        </div>
        <label class="visually-hidden" for="rtext-${set.id}">Corrected text</label>
        <input type="text" id="rtext-${set.id}" maxlength="500" placeholder="The whole corrected text for that side">
        <p class="mod-meta">Publishes a new version after the usual safety check. The author's private set is not changed.</p>
        <button type="button" class="primary-button" data-report-action="correct" data-set="${set.id}">Publish correction</button>
      </details>
    </div>
    ${history ? `<div class="mod-section"><h4>History</h4><ul>${history}</ul></div>` : ""}
  </article>`;
}

async function loadReports() {
  const list = document.querySelector("#reportsList");
  if (!list) return;
  const all = document.querySelector("#reportsShowAll")?.checked;
  const only = new URLSearchParams(window.location.search).get("set");
  const query = new URLSearchParams({ status: all ? "all" : "open" });
  if (only && /^\d+$/.test(only)) query.set("set", only);
  try {
    const data = await api(`/api/moderation/reports?${query}`);
    document.querySelector("#mailStatus").textContent = data.mail_configured
      ? `Alerts are emailed to ${data.alert_recipient}.`
      : `Email is not configured: alerts for ${data.alert_recipient} are stored here and not sent. See docs/COMMUNITY_MODERATION.md.`;
    document.querySelector("#reportsEmpty").classList.toggle("hidden", data.sets.length > 0);
    list.innerHTML = data.sets.map(reportSetHtml).join("");
  } catch (error) {
    toast(error.message, "error");
  }
}

async function reportAction(setId, action) {
  const note = document.querySelector(`#rnote-${setId}`)?.value.trim() || "";
  const reportIds = [...document.querySelectorAll(`[data-report-select="${setId}"]:checked`)].map(box => Number(box.value));
  const body = { action, note, report_ids: reportIds };
  if (action === "correct") {
    body.correction = {
      entry: Number(document.querySelector(`#rentry-${setId}`)?.value || 0),
      side: document.querySelector(`#rside-${setId}`)?.value || "back",
      text: document.querySelector(`#rtext-${setId}`)?.value.trim() || "",
    };
    if (!window.confirm("Publish this correction to the public set?")) return;
  }
  if (action === "restrict" && !window.confirm("Hide this set from the library until it is restored?")) return;
  try {
    await api(`/api/moderation/reports/${setId}/action`, { method: "POST", body });
    toast(`Recorded: ${action.replace("_", " ")}.`, "success");
    loadReports();
  } catch (error) {
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
  if (contentButton) {
    loadContent(Number(contentButton.dataset.loadContent));
    return;
  }
  const actionButton = event.target.closest("[data-report-action]");
  if (actionButton) {
    reportAction(Number(actionButton.dataset.set), actionButton.dataset.reportAction);
    return;
  }
  const retryButton = event.target.closest("[data-alert-retry]");
  if (retryButton) {
    api(`/api/moderation/notifications/${retryButton.dataset.alertRetry}/retry`, { method: "POST" })
      .then(data => { toast(`Alert ${data.notification.status}.`, "info"); loadReports(); })
      .catch(error => toast(error.message, "error"));
  }
});
document.querySelector("#reportsShowAll")?.addEventListener("change", loadReports);

load();
loadReports();

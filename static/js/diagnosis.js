import {escapeHtml} from "./dom.js";
import {t} from "./i18n.js";

// Progressive disclosure for the answer diagnosis.
//
// The answer response carries only the short, student-facing explanation. The detailed
// evidence-linked breakdown is fetched from /api/diagnosis/<attempt_id> the first time
// the student opens the panel, so the common case stays small and fast and nobody is
// handed a wall of analysis they did not ask for.

const STATUS_CLASS = {
  correct: "dx-correct",
  partially_correct: "dx-partial",
  incorrect: "dx-incorrect",
  insufficient_evidence: "dx-unknown",
};

function confidenceWord(value) {
  const percent = Math.round((Number(value) || 0) * 100);
  return `${percent}%`;
}

function list(items, className = "") {
  const usable = (items || []).filter(item => String(item || "").trim());
  if (!usable.length) return "";
  return `<ul class="${className}">${usable.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

function renderDetail(detail, diagnosis) {
  const blocks = [];
  if (detail.statement) {
    blocks.push(`<p class="dx-statement">${escapeHtml(detail.statement)}</p>`);
  }
  if (detail.misconception) {
    blocks.push(`<p class="dx-misconception">${escapeHtml(detail.misconception)}</p>`);
  }
  const quotes = (detail.evidence || []).map(item => item.quote).filter(Boolean);
  if (quotes.length) {
    blocks.push(`<h4>${t("dxFromAnswer")}</h4>${list(quotes, "dx-evidence")}`);
  }
  const rubric = (detail.rubric || []).filter(item => item && item.criterion);
  if (rubric.length) {
    blocks.push(`<h4>${t("dxChecked")}</h4><ul class="dx-rubric">${rubric.map(item =>
      `<li class="${item.met ? "met" : "unmet"}">${escapeHtml(item.criterion)}</li>`).join("")}</ul>`);
  }
  if ((detail.prerequisite_gaps || []).length) {
    blocks.push(`<h4>${t("dxPrerequisites")}</h4>${list(detail.prerequisite_gaps, "dx-prereq")}`);
  }
  if ((detail.concepts_assessed || []).length) {
    blocks.push(`<h4>${t("dxConcepts")}</h4>${list(detail.concepts_assessed, "dx-concepts")}`);
  }
  if (diagnosis.next_action_label) {
    blocks.push(`<p class="dx-next"><b>${t("dxNextStep")}:</b> ${escapeHtml(diagnosis.next_action_label)}</p>`);
  }
  blocks.push(`<p class="dx-confidence">${t("dxConfidence")}: ${confidenceWord(diagnosis.confidence)}</p>`);
  return blocks.join("");
}

/**
 * Build the diagnosis block for one answered question.
 * Returns an empty string when there is nothing trustworthy to show, so the feedback
 * panel is never padded with an empty section.
 */
export function diagnosisMarkup(diagnosis) {
  if (!diagnosis) return "";
  const statusClass = STATUS_CLASS[diagnosis.correctness_status] || "dx-unknown";
  const parts = [];
  if (diagnosis.explanation) {
    parts.push(`<p class="dx-explanation">${escapeHtml(diagnosis.explanation)}</p>`);
  }
  if (diagnosis.missing_evidence) {
    parts.push(
      `<p class="dx-missing"><b>${t("dxInsufficient")}.</b> ` +
      `${escapeHtml(diagnosis.missing_evidence_reason || t("dxShowWorking"))}</p>`);
  }
  if (!parts.length) return "";
  const label = diagnosis.primary_label ? `<span class="dx-tag">${escapeHtml(diagnosis.primary_label)}</span>` : "";
  // The detail panel only appears when there is an attempt to fetch it for.
  const detail = diagnosis.attempt_id
    ? `<details class="dx-details" data-attempt="${escapeHtml(String(diagnosis.attempt_id))}">` +
      `<summary>${t("dxWhy")}</summary>` +
      `<div class="dx-body" data-state="idle"></div></details>`
    : "";
  return `<div class="diagnosis ${statusClass}">${label}${parts.join("")}${detail}</div>`;
}

/**
 * Wire the lazy fetch on every diagnosis panel inside `root`.
 * A failed fetch shows a short message rather than an error dialog: the student already
 * has their score and explanation, and the detail is an extra.
 */
export function bindDiagnosisDetails(root) {
  root.querySelectorAll(".dx-details").forEach(element => {
    element.addEventListener("toggle", async () => {
      const body = element.querySelector(".dx-body");
      if (!element.open || body.dataset.state !== "idle") return;
      body.dataset.state = "loading";
      body.innerHTML = `<p class="dx-loading">${t("dxLoading")}</p>`;
      try {
        const response = await fetch(`/api/diagnosis/${encodeURIComponent(element.dataset.attempt)}`);
        const data = await response.json();
        if (!response.ok || !data.diagnosis) throw new Error(data.error || "unavailable");
        body.innerHTML = renderDetail(data.diagnosis.detail || {}, data.diagnosis);
        body.dataset.state = "loaded";
      } catch (_error) {
        body.innerHTML = `<p class="dx-loading">${t("dxLoadFailed")}</p>`;
        body.dataset.state = "idle";
      }
    });
  });
}

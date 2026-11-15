/* Premium vouchers (admin): copy and download a freshly generated batch, and confirm a batch
 * revocation.
 *
 * The codes exist only in this page: the CSV and TXT files are built here, in the browser,
 * from the JSON the page carries, and handed to the browser as a download. Nothing is
 * stored on the server or fetched again; when the page is closed the codes are gone. */

function exportData() {
  const node = document.getElementById("voucherExport");
  if (!node) return null;
  try {
    const data = JSON.parse(node.textContent || "{}");
    return Array.isArray(data.codes) ? data : null;
  } catch (_error) {
    return null;
  }
}

function csvCell(value) {
  const text = String(value ?? "");
  return /[",\n\r;]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function download(name, type, text) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const data = exportData();

document.querySelectorAll("[data-voucher-copy]").forEach(button => {
  button.addEventListener("click", async () => {
    if (!data) return;
    const label = button.textContent;
    try {
      await navigator.clipboard.writeText(data.codes.join("\n"));
      button.textContent = button.dataset.copiedLabel || label;
    } catch (_error) {
      const area = document.getElementById("voucherCodes");
      if (area) { area.focus(); area.select(); }
    }
    setTimeout(() => { button.textContent = label; }, 2000);
  });
});

document.querySelectorAll("[data-voucher-download]").forEach(button => {
  button.addEventListener("click", () => {
    if (!data) return;
    const base = `learnova-vouchers-${data.batch}`;
    if (button.dataset.voucherDownload === "csv") {
      const duration = button.dataset.durationLabel || `${data.months} month(s)`;
      const rows = [["activation_code", "batch_id", "duration"], ...data.codes.map(code => [code, data.batch, duration])];
      // A byte-order mark so spreadsheet programs read the file as UTF-8.
      download(`${base}.csv`, "text/csv;charset=utf-8", `﻿${rows.map(row => row.map(csvCell).join(",")).join("\r\n")}\r\n`);
    } else {
      download(`${base}.txt`, "text/plain;charset=utf-8", `${data.codes.join("\r\n")}\r\n`);
    }
  });
});

document.querySelectorAll("form[data-voucher-confirm]").forEach(form => {
  form.addEventListener("submit", event => {
    if (!window.confirm(form.dataset.voucherConfirm)) event.preventDefault();
  });
});

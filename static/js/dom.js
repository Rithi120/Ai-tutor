// Text made safe for HTML, in element content *and* in a quoted attribute value. The old
// version went through textContent/innerHTML, which leaves " and ' alone: a model reply
// containing https://x.test/"style="position:fixed;inset:0..." closed the href and drew an
// overlay across the page. A pure function also runs outside a browser (tests).
const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

export function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => HTML_ESCAPES[char]);
}

export const escapeAttr = escapeHtml;

// Shared, browser-safe UUID generator.
// `crypto.randomUUID` is only defined in secure contexts (HTTPS or localhost);
// over plain http:// to a LAN IP, or in older browsers, it is undefined and a
// bare call throws "crypto.randomUUID is not a function". This helper degrades
// gracefully: native randomUUID -> getRandomValues-built v4 -> time+random.
// These IDs are only used as client-side idempotency/request keys and are never
// trusted by the server for ownership or authorization.
export function safeUUID() {
  const cryptoObj = globalThis.crypto;
  if (cryptoObj && typeof cryptoObj.randomUUID === "function") {
    try {
      return cryptoObj.randomUUID();
    } catch (_) { /* fall through to the next strategy */ }
  }
  if (cryptoObj && typeof cryptoObj.getRandomValues === "function") {
    const bytes = new Uint8Array(16);
    cryptoObj.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10xx
    const hex = [];
    for (let i = 0; i < 16; i++) hex.push(bytes[i].toString(16).padStart(2, "0"));
    return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10, 16).join("")}`;
  }
  return `id-${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}`;
}

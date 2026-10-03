// Unit test for the shared safe UUID helper. Run with: node --test tests/js/*.test.mjs
// (Node is not installed in the current CI image; this test documents and locks the
// fallback behaviour for environments where crypto.randomUUID is undefined, e.g. a
// dev server reached over plain http:// on a LAN IP, or an older browser/webview.)
import { test } from "node:test";
import assert from "node:assert/strict";
import { webcrypto } from "node:crypto";

// Re-implement the loader: dom.js is browser-oriented (uses document); we import only
// the pure function by evaluating the exported source in a minimal shim.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const dir = path.dirname(fileURLToPath(import.meta.url));
const source = readFileSync(path.join(dir, "../../static/js/dom.js"), "utf8");
// Extract the safeUUID function body via dynamic import of a data URL (no DOM needed).
const moduleUrl = "data:text/javascript," + encodeURIComponent(
  source.replace(/export function escapeHtml[\s\S]*?\n}\n/, "")  // drop DOM-dependent export
);
const { safeUUID } = await import(moduleUrl);

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test("uses native randomUUID when available", () => {
  globalThis.crypto = webcrypto;
  assert.match(safeUUID(), UUID_RE);
});

test("falls back to getRandomValues when randomUUID is undefined", () => {
  // Simulate a non-secure context: getRandomValues exists, randomUUID does not.
  globalThis.crypto = { getRandomValues: (a) => webcrypto.getRandomValues(a) };
  const id = safeUUID();
  assert.match(id, UUID_RE, `expected a v4 UUID, got ${id}`);
});

test("returns a non-empty string even with no crypto at all", () => {
  globalThis.crypto = undefined;
  const id = safeUUID();
  assert.equal(typeof id, "string");
  assert.ok(id.length > 8);
});

// @ts-check
/**
 * Regression gate: no module other than text-format.mjs may define a
 * local `escAttr` function. The XSS we shipped (and fixed) in `b83c0f2`
 * and the follow-up audit (modals-models.mjs + faces.mjs) had the same
 * shape — a local helper that forgot the `"` escape, used to interpolate
 * a server-controlled string into an HTML attribute.
 *
 * The canonical helper is `escapeAttr` in `text-format.mjs`. Anything
 * else with the same prefix is a hazard.
 */

import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const MODULES_DIR = resolve(__dirname, "../bpp/web/static/js/modules");
const ALLOWED_FILES = new Set([
  // The canonical helper lives here; it's the only file that may name
  // anything `escAttr*` or `escapeAttr*` at the top level.
  "text-format.mjs",
]);

/** @returns {string[]} */
function walkModules(dir) {
  /** @type {string[]} */
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    const st = statSync(full);
    if (st.isDirectory()) {
      out.push(...walkModules(full));
    } else if (entry.endsWith(".mjs")) {
      out.push(full);
    }
  }
  return out;
}

describe("no local escAttr stubs", () => {
  it("rejects any .mjs module that defines a local escAttr / escapeAttr", () => {
    const offenders = [];
    for (const path of walkModules(MODULES_DIR)) {
      const filename = path.split("/").pop() || "";
      if (ALLOWED_FILES.has(filename)) continue;
      const src = readFileSync(path, "utf8");
      // Match `const escAttr =`, `function escAttr(`, `const escapeAttr =`,
      // `function escapeAttr(` — anything that DEFINES the helper locally.
      // Imports (`import { escapeAttr } from ...`) are fine and don't match.
      if (/(?:const|let|var|function)\s+(escAttr|escapeAttr)\s*[=(]/.test(src)) {
        offenders.push(filename);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("text-format.mjs really exports the canonical escapeAttr", () => {
    const src = readFileSync(join(MODULES_DIR, "text-format.mjs"), "utf8");
    expect(src).toMatch(/export\s+function\s+escapeAttr\s*\(/);
    // The contract that local stubs missed: it must escape the double
    // quote and the single quote, not just &<>. Pin it here.
    expect(src).toMatch(/&quot;/);
    expect(src).toMatch(/&#39;|&apos;/);
  });
});

// @ts-check
/**
 * P8 — window-pollution gate.
 *
 * The pre-P8 globals.js has 92 lines of `window.X = X` assignments — every
 * handler that the dispatcher needs to find via `window[name]`. The audit
 * counted ~890 effective globals when you include the
 * `Object.assign(window, ...moduleNamespaces)` bridge in index.html.
 *
 * This gate scans globals.js for new `window.<name> = <name>` assignments.
 * As handlers migrate to the action registry, their bridges should be
 * removed; the assertion below is a high-water mark that ratchets DOWN as
 * the migration progresses. **The gate fails if pollution grows.**
 *
 * Migration story:
 *   1. New code: use `registerAction(name, handler)` in a module. Don't
 *      add a `window.X = X` bridge in globals.js.
 *   2. Existing code: when you touch a handler, replace its window bridge
 *      with a registerAction call in the appropriate module.
 *   3. Update WINDOW_POLLUTION_CAP below (downward) in the same commit.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { describe, expect, test } from "vitest";

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(__dirname, "..");
const GLOBALS_PATH = join(REPO_ROOT, "bpp/web/static/js/globals.js");

// Set INTENTIONALLY to the current count. Future migrations decrement this
// in the same PR as the registerAction call. If a PR raises the count,
// the gate fails — the author either reverts the addition or justifies
// the bump in the PR description.
const WINDOW_POLLUTION_CAP = 92;

describe("window-pollution gate", () => {
  test("globals.js window.X = X assignment count does not grow", () => {
    const src = readFileSync(GLOBALS_PATH, "utf8");
    // Match patterns like `window.foo = foo;` or `window.foo = bar;`.
    // Skip lines that are inside comments — single-line // and the
    // /* ... */ block forms.
    const lines = src.split("\n");
    let count = 0;
    let inBlockComment = false;
    for (const raw of lines) {
      const line = raw.trim();
      // Handle /* ... */ block comments that span lines.
      if (inBlockComment) {
        if (line.includes("*/")) inBlockComment = false;
        continue;
      }
      if (line.startsWith("/*")) {
        if (!line.includes("*/")) inBlockComment = true;
        continue;
      }
      if (line.startsWith("//")) continue;
      if (/^window\.[A-Za-z_][A-Za-z0-9_]*\s*=/.test(line)) {
        count++;
      }
    }
    expect(count).toBeLessThanOrEqual(WINDOW_POLLUTION_CAP);
  });

  test("WINDOW_POLLUTION_CAP doesn't silently rise without ratchet update", () => {
    // Sanity assertion: if someone changes the cap, the test files
    // changed AND globals.js changed. We can't enforce coordination
    // automatically, but if the cap is significantly above the
    // measured value, flag it so the next maintainer notices the
    // ratchet hasn't been pulled.
    const src = readFileSync(GLOBALS_PATH, "utf8");
    let measured = 0;
    let inBlockComment = false;
    for (const raw of src.split("\n")) {
      const line = raw.trim();
      if (inBlockComment) {
        if (line.includes("*/")) inBlockComment = false;
        continue;
      }
      if (line.startsWith("/*")) {
        if (!line.includes("*/")) inBlockComment = true;
        continue;
      }
      if (line.startsWith("//")) continue;
      if (/^window\.[A-Za-z_][A-Za-z0-9_]*\s*=/.test(line)) measured++;
    }
    // If you've shipped 5+ migrations without ratcheting the cap, the
    // gate is too loose. Tighten WINDOW_POLLUTION_CAP downward.
    expect(WINDOW_POLLUTION_CAP - measured).toBeLessThan(10);
  });
});

describe("action registry exists and is loadable", () => {
  test("modules/action-registry.mjs exports registerAction", async () => {
    const mod = await import("../bpp/web/static/js/modules/action-registry.mjs");
    expect(typeof mod.registerAction).toBe("function");
    expect(typeof mod.replaceAction).toBe("function");
    expect(typeof mod.lookupAction).toBe("function");
  });
});

describe("T0.1 — action-registry is wired into production bootstrap", () => {
  // The review's H1 finding: the dispatcher in globals.js looks up
  // window.__bppActionRegistry, but pre-T0.1 nothing imported the registry
  // module, so the global was undefined and the registry-first lookup
  // always fell through to window[name]. This test gates the production
  // bootstrap so the substrate is actually reachable.

  const INDEX_HTML_PATH = join(REPO_ROOT, "bpp/web/templates/index.html");

  test("index.html module bootstrap imports action-registry.mjs", () => {
    const src = readFileSync(INDEX_HTML_PATH, "utf8");
    expect(src).toMatch(
      /import\s+\*\s+as\s+\w+\s+from\s+["']\/static\/js\/modules\/action-registry\.mjs["']/
    );
  });

  test("action-registry import is in the same module bootstrap block as state.mjs", () => {
    // The bootstrap block is the single <script type="module"> that loads
    // every other module. If action-registry lands in a SEPARATE script
    // tag, its side-effect (assigning window.__bppActionRegistry) may
    // race with the dispatcher's first click. Same-block guarantees the
    // assignment happens before any user event.
    const src = readFileSync(INDEX_HTML_PATH, "utf8");
    // Find the <script type="module"> block that imports state.mjs.
    const blockMatch = src.match(
      /<script type="module"[^>]*>([\s\S]*?state\.mjs[\s\S]*?)<\/script>/
    );
    expect(blockMatch).toBeTruthy();
    const block = blockMatch[1];
    expect(block).toMatch(/action-registry\.mjs/);
  });
});

// @ts-check
import { describe, expect, test } from "vitest";

import { USE_CONTEXT_OPTIONS } from "../bpp/web/static/js/modules/use-context-options.mjs";

describe("USE_CONTEXT_OPTIONS — single source of truth", () => {
  test("exports the canonical three options in plan order", () => {
    expect(USE_CONTEXT_OPTIONS.map((o) => o.value)).toEqual(["personal", "research", "commercial"]);
  });

  test("every option has title + non-empty desc", () => {
    for (const o of USE_CONTEXT_OPTIONS) {
      expect(typeof o.value).toBe("string");
      expect(typeof o.title).toBe("string");
      expect(typeof o.desc).toBe("string");
      expect(o.title.length).toBeGreaterThan(0);
      expect(o.desc.length).toBeGreaterThan(0);
    }
  });

  test("Commercial option warns about hard-block (item 16)", () => {
    const commercial = USE_CONTEXT_OPTIONS.find((o) => o.value === "commercial");
    expect(commercial).toBeDefined();
    expect(commercial?.desc.toLowerCase()).toMatch(/hard-?block/);
    expect(commercial?.desc.toLowerCase()).toMatch(/separate rights|rights assertion/);
  });
});

describe("both renderers reach through the same module", () => {
  test("onboarding.mjs source references USE_CONTEXT_OPTIONS", async () => {
    const fs = await import("node:fs/promises");
    const src = await fs.readFile("bpp/web/static/js/modules/onboarding.mjs", "utf8");
    expect(src).toMatch(/USE_CONTEXT_OPTIONS/);
    // Also ensure the inline opts array is gone — a future drift
    // would re-introduce it.
    expect(src).not.toMatch(/"Curating my own photos\."/);
  });

  test("modals-face-embedders.mjs source references USE_CONTEXT_OPTIONS", async () => {
    const fs = await import("node:fs/promises");
    const src = await fs.readFile("bpp/web/static/js/modules/modals-face-embedders.mjs", "utf8");
    expect(src).toMatch(/USE_CONTEXT_OPTIONS/);
    expect(src).not.toMatch(/"Curating my own photos\."/);
  });
});

// @ts-check
/**
 * Final sweep, UI side: a failed batch call is reported as a failure
 * (no success toast, no optimistic state), the "All" actions name a
 * failed listing, and the preview gallery shows its first page before
 * the rest arrive.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { restoreAllDeleted, unhideAllHidden } from "../bpp/web/static/js/modules/deleted.mjs";
import { jsonResp, setup, teardown } from "./_deleted-setup.mjs";

function toastTexts() {
  return [...document.querySelectorAll("#toast-container .toast")].map((t) => t.textContent || "");
}

describe("All actions", () => {
  beforeEach(setup);
  afterEach(teardown);

  test("a failed listing is named, not a generic error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"error":"library_changed"}', { status: 409 }))
    );
    await restoreAllDeleted();
    await unhideAllHidden();
    const texts = toastTexts();
    expect(texts.some((t) => t.includes("Couldn't list the deleted photos"))).toBe(true);
    expect(texts.some((t) => t.includes("Couldn't list the hidden photos"))).toBe(true);
  });
});

describe("batch actions", () => {
  beforeEach(setup);
  afterEach(teardown);

  test("a failed override puts the view back and shows no success toast", async () => {
    const { batchOverride } = await import("../bpp/web/static/js/modules/photos-batch.mjs");
    const win = /** @type {any} */ (window);
    win.multiSelected = new Set(["/a.jpg", "/b.jpg"]);
    win.overrides = { "/a.jpg": "include" };
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"error":"disk full"}', { status: 500 }))
    );
    await batchOverride("exclude");
    expect(win.overrides).toEqual({ "/a.jpg": "include" });
    const texts = toastTexts();
    expect(texts.some((t) => t.includes("Couldn't apply the override"))).toBe(true);
    expect(texts.some((t) => t.includes("Excluded"))).toBe(false);
  });
});

describe("preview gallery", () => {
  beforeEach(setup);
  afterEach(teardown);

  test("the first page is rendered before the second arrives", async () => {
    const { showPreviewGallery } = await import("../bpp/web/static/js/modules/analysis-status.mjs");
    const win = /** @type {any} */ (window);
    const renders = [];
    win.renderGrid = () => renders.push(win.photos.length);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        const offset = Number(/offset=(\d+)/.exec(String(url))?.[1] || 0);
        const page = offset === 0 ? 500 : 20;
        return jsonResp({
          photos: Array.from({ length: page }, (_, i) => ({ filepath: `/p${offset + i}.jpg` })),
          count: page,
          total: 520,
        });
      })
    );
    await showPreviewGallery(520);
    expect(renders).toEqual([500, 520]);
    expect(win.photos.length).toBe(520);
  });
});

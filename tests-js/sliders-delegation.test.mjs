// @ts-check
// Regression (2026-06-12): tuning sliders ([data-param]) were wired with
// per-element listeners at boot; any innerHTML rebuild of their container
// (renderAlbumNav rebuilds the sidebar People-boost slider on every nav
// change) orphaned them — the slider dragged but did nothing. initSliders
// must DELEGATE so handlers survive rebuilds.
import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/analysis.mjs", () => ({
  scheduleRecompute: vi.fn(),
  doRecompute: vi.fn(),
  showSkeletonGrid: vi.fn(),
  updateStats: vi.fn(),
}));

import { scheduleRecompute } from "../bpp/web/static/js/modules/analysis.mjs";
import { initSliders } from "../bpp/web/static/js/modules/ui-helpers.mjs";

describe("initSliders delegation", () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="host"></div>';
    vi.mocked(scheduleRecompute).mockClear();
  });

  test("a slider rendered AFTER initSliders still updates its label + recomputes", () => {
    initSliders();
    // Simulate a post-boot re-render (the renderAlbumNav case).
    const host = document.getElementById("host");
    if (host) {
      host.innerHTML =
        '<input type="range" data-param="face_selection_boost" min="0" max="0.5" step="0.01" value="0.15"><span class="val">0.15</span>';
    }
    const input = /** @type {HTMLInputElement} */ (document.querySelector("[data-param]"));
    input.value = "0.3";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    expect(document.querySelector(".val")?.textContent).not.toBe("0.15");
    expect(scheduleRecompute).toHaveBeenCalled();
  });
});

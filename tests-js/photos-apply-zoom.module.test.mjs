// @ts-check
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import {
  _updateVisibleCards,
  applyZoom,
  stepZoom,
  updateCardInPlace,
  vgrid,
} from "../bpp/web/static/js/modules/photos.mjs";
import { setup, teardown, samplePhoto } from "./_photos-setup.mjs";

beforeEach(setup);
afterEach(teardown);

describe("applyZoom / stepZoom", () => {
  test("clamps to [40, 300] and updates label", () => {
    applyZoom(150, false);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("150%");
    applyZoom(500, false);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("300%");
    applyZoom(10, false);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("40%");
  });

  test("stepZoom adjusts the slider by delta", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("zoom-slider")).value = "100";
    stepZoom(20);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("120%");
  });
});

describe("vgrid", () => {
  test("setItems populates internal items array", () => {
    vgrid.setItems([samplePhoto]);
    expect(vgrid.items).toHaveLength(1);
  });

  test("appendItems concatenates", () => {
    vgrid.setItems([samplePhoto]);
    vgrid.appendItems([{ ...samplePhoto, filepath: "/b" }]);
    expect(vgrid.items).toHaveLength(2);
  });

  test("onResize re-renders with updated clientHeight", () => {
    // Simulate initial render when clientHeight=0 (layout not yet resolved)
    const grid = document.getElementById("photo-grid");
    const content = document.querySelector(".content");
    if (!grid || !content) throw new Error("DOM not set up");

    // Give the grid a measurable size so measure() can compute rowHeight
    Object.defineProperty(grid, "clientWidth", { value: 1000, configurable: true });

    const items = Array.from({ length: 50 }, (_, i) => ({
      ...samplePhoto,
      filepath: `/p${i}.jpg`,
      thumb_hash: `h${i}`,
    }));
    vgrid.rowHeight = 260;
    vgrid.setItems(items);

    // clientHeight=0 → render schedules a rAF retry, lastRow stays -1
    Object.defineProperty(content, "clientHeight", { value: 0, configurable: true });
    vgrid.render(true);
    // With clientHeight=0 the render returns early (rAF retry) — lastRow unchanged
    expect(vgrid.lastRow).toBe(-1);

    // clientHeight=800 → re-render fills the viewport
    Object.defineProperty(content, "clientHeight", { value: 800, configurable: true });
    vgrid.onResize();
    const rowsAfterResize = vgrid.lastRow - vgrid.firstRow + 1;
    expect(rowsAfterResize).toBeGreaterThan(vgrid.buffer);
  });
});

describe("_updateVisibleCards / updateCardInPlace", () => {
  test("_updateVisibleCards iterates without throwing", () => {
    expect(() => _updateVisibleCards()).not.toThrow();
  });

  test("updateCardInPlace returns false when card missing", () => {
    expect(updateCardInPlace("/missing.jpg")).toBe(false);
  });
});

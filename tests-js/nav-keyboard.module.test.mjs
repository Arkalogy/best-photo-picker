// @ts-check
// Sidebar rows (albums, people, Faces, BPP Picks) are reachable with Tab
// and act like buttons, so a context menu opened from one hands focus
// back to the row, not to the nearest unrelated control.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

afterEach(() => vi.unstubAllGlobals());

import { renderAlbumNav } from "../bpp/web/static/js/modules/albums.mjs";

beforeEach(() => {
  document.body.innerHTML = '<aside class="sidebar"><div id="album-list"></div></aside>';
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (/** @type {string} */ k) => (k in store ? store[k] : null),
    setItem: (/** @type {string} */ k, /** @type {string} */ v) => {
      store[k] = String(v);
    },
    removeItem: (/** @type {string} */ k) => {
      delete store[k];
    },
    clear: () => {},
  });
  const win = /** @type {any} */ (window);
  win.albumList = [
    { id: 1, album_type: "all", name: "Library", photo_count: 3, selected_count: 1 },
    { id: 2, album_type: "manual", name: "Trip", photo_count: 2 },
  ];
  win.faceClusters = [];
  win.petClusters = [];
  win.ICONS = {};
});

describe("sidebar rows", () => {
  test("every row is a focusable button", () => {
    renderAlbumNav();
    const rows = document.querySelectorAll("#album-list .nav-item, #album-list .nav-subitem");
    expect(rows.length).toBeGreaterThan(2);
    for (const row of rows) {
      if (row.tagName === "SUMMARY") continue; // focusable on its own
      // A row with its own delete button is a group (the button stays
      // exposed); any other row is a button.
      expect(["button", "group"]).toContain(row.getAttribute("role"));
      expect(row.getAttribute("tabindex")).toBe("0");
      expect(row.getAttribute("data-key-activate")).toBe("true");
    }
  });
});

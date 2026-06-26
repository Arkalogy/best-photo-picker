// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _clearRedeyePoints,
  _redeyeClick,
  _removeRedeyeOverlay,
  _renderRedeyeMarkers,
  _showRedeyeOverlay,
  _toggleRedeyeMode,
} from "../bpp/web/static/js/modules/editor-redeye.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div class="lb-img-wrapper" style="width:1000px;height:500px"></div>
    <button class="editor-btn editor-btn-redeye"></button>
    <div id="toast-container"></div>
  `;
  /** @type {any} */ (window).editorEdits = {};
  /** @type {any} */ (window)._redeyeMode = false;
  /** @type {any} */ (window)._refreshStylesTab = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).editorEdits);
  delete (/** @type {any} */ (window)._redeyeMode);
  delete (/** @type {any} */ (window)._refreshStylesTab);
});

const overlay = () => document.getElementById("redeye-overlay");

describe("_toggleRedeyeMode", () => {
  test("first toggle activates: button .active, overlay mounted, toast shown", () => {
    _toggleRedeyeMode();
    expect(/** @type {any} */ (window)._redeyeMode).toBe(true);
    expect(document.querySelector(".editor-btn-redeye")?.classList.contains("active")).toBe(true);
    expect(overlay()).toBeTruthy();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Click on red eyes"
    );
  });

  test("second toggle deactivates: button cleared, overlay removed", () => {
    _toggleRedeyeMode(); // on
    _toggleRedeyeMode(); // off
    expect(/** @type {any} */ (window)._redeyeMode).toBe(false);
    expect(overlay()).toBeNull();
  });
});

describe("_showRedeyeOverlay", () => {
  test("appends a single overlay into .lb-img-wrapper", () => {
    _showRedeyeOverlay();
    expect(overlay()).toBeTruthy();
    // Re-mount replaces, doesn't duplicate
    _showRedeyeOverlay();
    expect(document.querySelectorAll("#redeye-overlay")).toHaveLength(1);
  });

  test("renders existing markers from editorEdits.redeye_points", () => {
    /** @type {any} */ (window).editorEdits = {
      redeye_points: [
        { x: 0.5, y: 0.5, radius: 0.03 },
        { x: 0.25, y: 0.75, radius: 0.03 },
      ],
    };
    _showRedeyeOverlay();
    expect(document.querySelectorAll(".redeye-marker")).toHaveLength(2);
  });

  test("no-op when wrapper is missing", () => {
    document.body.innerHTML = "";
    expect(() => _showRedeyeOverlay()).not.toThrow();
    expect(overlay()).toBeNull();
  });
});

describe("_removeRedeyeOverlay", () => {
  test("removes the overlay element when present", () => {
    _showRedeyeOverlay();
    expect(overlay()).toBeTruthy();
    _removeRedeyeOverlay();
    expect(overlay()).toBeNull();
  });

  test("no-op when overlay isn't present", () => {
    expect(() => _removeRedeyeOverlay()).not.toThrow();
  });
});

describe("_redeyeClick", () => {
  test("appends a normalized point into editorEdits.redeye_points", () => {
    _showRedeyeOverlay();
    const ov = /** @type {HTMLElement} */ (overlay());
    // Mock the bounding rect since jsdom lays everything out at 0,0
    ov.getBoundingClientRect = () =>
      /** @type {any} */ ({
        left: 0,
        top: 0,
        width: 1000,
        height: 500,
        right: 1000,
        bottom: 500,
      });
    _redeyeClick(
      /** @type {any} */ ({
        currentTarget: ov,
        clientX: 500, // 50% horizontally
        clientY: 250, // 50% vertically
      })
    );
    /** @type {any} */
    const win = window;
    expect(win.editorEdits.redeye_points).toHaveLength(1);
    expect(win.editorEdits.redeye_points[0].x).toBe(0.5);
    expect(win.editorEdits.redeye_points[0].y).toBe(0.5);
    expect(win.editorEdits.redeye_points[0].radius).toBe(0.03);
    expect(win._refreshStylesTab).toHaveBeenCalled();
  });

  test("rounds coordinates to 3 decimal places", () => {
    _showRedeyeOverlay();
    const ov = /** @type {HTMLElement} */ (overlay());
    ov.getBoundingClientRect = () =>
      /** @type {any} */ ({ left: 0, top: 0, width: 1000, height: 500 });
    _redeyeClick(
      /** @type {any} */ ({
        currentTarget: ov,
        clientX: 333,
        clientY: 167,
      })
    );
    /** @type {any} */
    const win = window;
    expect(win.editorEdits.redeye_points[0].x).toBe(0.333);
    expect(win.editorEdits.redeye_points[0].y).toBe(0.334);
  });
});

describe("_renderRedeyeMarkers", () => {
  test("clears prior markers before re-rendering", () => {
    _showRedeyeOverlay();
    /** @type {any} */ (window).editorEdits = {
      redeye_points: [{ x: 0.5, y: 0.5, radius: 0.03 }],
    };
    _renderRedeyeMarkers();
    expect(document.querySelectorAll(".redeye-marker")).toHaveLength(1);
    /** @type {any} */ (window).editorEdits.redeye_points = [
      { x: 0.1, y: 0.1, radius: 0.03 },
      { x: 0.2, y: 0.2, radius: 0.03 },
      { x: 0.3, y: 0.3, radius: 0.03 },
    ];
    _renderRedeyeMarkers();
    expect(document.querySelectorAll(".redeye-marker")).toHaveLength(3);
  });

  test("clicking a marker removes that point + re-renders", () => {
    _showRedeyeOverlay();
    /** @type {any} */ (window).editorEdits = {
      redeye_points: [
        { x: 0.1, y: 0.1, radius: 0.03 },
        { x: 0.5, y: 0.5, radius: 0.03 },
      ],
    };
    _renderRedeyeMarkers();
    const markers = document.querySelectorAll(".redeye-marker");
    expect(markers).toHaveLength(2);
    /** @type {HTMLElement} */ (markers[0]).click();
    /** @type {any} */
    const win = window;
    expect(win.editorEdits.redeye_points).toHaveLength(1);
    // First was removed → second survives
    expect(win.editorEdits.redeye_points[0].x).toBe(0.5);
  });

  test("removing the last point nulls redeye_points entirely", () => {
    _showRedeyeOverlay();
    /** @type {any} */ (window).editorEdits = {
      redeye_points: [{ x: 0.5, y: 0.5, radius: 0.03 }],
    };
    _renderRedeyeMarkers();
    /** @type {HTMLElement} */ (document.querySelector(".redeye-marker")).click();
    expect(/** @type {any} */ (window).editorEdits.redeye_points).toBeNull();
  });

  test("no-op when overlay is missing", () => {
    expect(() => _renderRedeyeMarkers()).not.toThrow();
  });
});

describe("_clearRedeyePoints", () => {
  test("nulls redeye_points and re-renders + refreshes the styles tab", () => {
    _showRedeyeOverlay();
    /** @type {any} */ (window).editorEdits = {
      redeye_points: [{ x: 0.5, y: 0.5, radius: 0.03 }],
    };
    _renderRedeyeMarkers();
    expect(document.querySelectorAll(".redeye-marker")).toHaveLength(1);
    _clearRedeyePoints();
    expect(/** @type {any} */ (window).editorEdits.redeye_points).toBeNull();
    expect(document.querySelectorAll(".redeye-marker")).toHaveLength(0);
    expect(/** @type {any} */ (window)._refreshStylesTab).toHaveBeenCalled();
  });
});

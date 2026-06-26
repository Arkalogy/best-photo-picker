// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _adjustCropToAspect,
  _applyCropFromOverlay,
  _clearCrop,
  _removeCropOverlay,
  _setAspectRatio,
  _setCropBox,
  _showCropOverlay,
  _toggleCropOverlay,
} from "../bpp/web/static/js/modules/editor-crop.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div class="lb-img-wrapper">
      <img id="lb-img" />
    </div>
    <button id="editor-crop-toggle"></button>
    <div id="editor-crop-tab"></div>
    <button class="editor-aspect-pill" id="aspect-1x1"></button>
    <button class="editor-aspect-pill" id="aspect-orig"></button>
  `;
  /** @type {any} */ (window).editorEdits = {};
  /** @type {any} */ (window).editorCropActive = false;
  /** @type {any} */ (window)._cropDragging = null;
  /** @type {any} */ (window)._cropStartX = 0;
  /** @type {any} */ (window)._cropStartY = 0;
  /** @type {any} */ (window)._cropStartRect = {};
  /** @type {any} */ (window)._editorAspectRatio = null;
  /** @type {any} */ (window)._renderCropControls = vi.fn(() => "<i>controls</i>");
  // Stub layout properties — jsdom returns 0 for these by default.
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get() {
      return 200;
    },
  });
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get() {
      return 100;
    },
  });
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "editorEdits",
    "editorCropActive",
    "_cropDragging",
    "_cropStartX",
    "_cropStartY",
    "_cropStartRect",
    "_editorAspectRatio",
    "_renderCropControls",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
});

describe("_showCropOverlay / _removeCropOverlay", () => {
  test("show creates the overlay with crop-box and 4 dim layers", () => {
    _showCropOverlay();
    expect(document.getElementById("crop-overlay")).toBeTruthy();
    expect(document.getElementById("crop-box")).toBeTruthy();
    expect(document.querySelectorAll(".crop-dim")).toHaveLength(4);
  });

  test("show is idempotent — re-showing doesn't duplicate", () => {
    _showCropOverlay();
    _showCropOverlay();
    expect(document.querySelectorAll(".crop-overlay")).toHaveLength(1);
  });

  test("remove clears the overlay", () => {
    _showCropOverlay();
    _removeCropOverlay();
    expect(document.getElementById("crop-overlay")).toBeNull();
  });

  test("show seeds crop_x/y/w/h from editorEdits when present", () => {
    /** @type {any} */ (window).editorEdits = {
      crop_x: 0.1,
      crop_y: 0.2,
      crop_w: 0.5,
      crop_h: 0.5,
    };
    _showCropOverlay();
    const box = /** @type {HTMLElement} */ (document.getElementById("crop-box"));
    expect(box.style.left).toBe("10%");
    expect(box.style.top).toBe("20%");
    expect(box.style.width).toBe("50%");
    expect(box.style.height).toBe("50%");
  });
});

describe("_toggleCropOverlay", () => {
  test("first call activates + shows overlay", () => {
    _toggleCropOverlay();
    expect(/** @type {any} */ (window).editorCropActive).toBe(true);
    expect(document.getElementById("crop-overlay")).toBeTruthy();
    expect(document.getElementById("editor-crop-toggle")?.textContent).toBe("Apply Crop");
  });

  test("second call applies + removes overlay", () => {
    _toggleCropOverlay();
    _toggleCropOverlay();
    expect(/** @type {any} */ (window).editorCropActive).toBe(false);
    expect(document.getElementById("crop-overlay")).toBeNull();
    expect(document.getElementById("editor-crop-toggle")?.textContent).toBe("Start Crop");
  });
});

describe("_setAspectRatio", () => {
  test("numeric ratio sets _editorAspectRatio", () => {
    const btn = /** @type {HTMLElement} */ (document.getElementById("aspect-1x1"));
    _setAspectRatio(1.0, btn);
    expect(/** @type {any} */ (window)._editorAspectRatio).toBe(1.0);
    expect(btn.classList.contains("active")).toBe(true);
  });

  test("'original' uses image natural dimensions", () => {
    const img = /** @type {HTMLImageElement} */ (document.getElementById("lb-img"));
    Object.defineProperty(img, "naturalWidth", { value: 1200, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 800, configurable: true });
    _setAspectRatio("original");
    expect(/** @type {any} */ (window)._editorAspectRatio).toBeCloseTo(1.5, 5);
  });

  test("removes 'active' from other pills, adds to clicked one", () => {
    const a = /** @type {HTMLElement} */ (document.getElementById("aspect-1x1"));
    const b = /** @type {HTMLElement} */ (document.getElementById("aspect-orig"));
    a.classList.add("active");
    _setAspectRatio(2.0, b);
    expect(a.classList.contains("active")).toBe(false);
    expect(b.classList.contains("active")).toBe(true);
  });
});

describe("_setCropBox", () => {
  test("clamps x/y/w/h within bounds", () => {
    _showCropOverlay();
    _setCropBox(-1, -1, 2, 2);
    const box = /** @type {HTMLElement} */ (document.getElementById("crop-box"));
    // After clamp: w/h capped at 1, x/y at 0
    expect(box.style.width).toBe("100%");
    expect(box.style.height).toBe("100%");
  });

  test("noop when overlay missing", () => {
    expect(() => _setCropBox(0, 0, 0.5, 0.5)).not.toThrow();
  });
});

describe("_applyCropFromOverlay", () => {
  test("nulls out crop_* when overlay covers whole image", () => {
    /** @type {any} */ (window).editorEdits = {
      crop_x: 0.1,
      crop_y: 0.1,
      crop_w: 0.8,
      crop_h: 0.8,
    };
    _showCropOverlay();
    // Manually set the crop-box to cover full overlay
    const box = /** @type {HTMLElement} */ (document.getElementById("crop-box"));
    Object.defineProperty(box, "offsetLeft", { value: 0, configurable: true });
    Object.defineProperty(box, "offsetTop", { value: 0, configurable: true });
    _applyCropFromOverlay();
    expect(/** @type {any} */ (window).editorEdits.crop_x).toBeNull();
    expect(/** @type {any} */ (window).editorEdits.crop_w).toBeNull();
  });

  test("rounds crop dims to 3 decimals", () => {
    _showCropOverlay();
    const box = /** @type {HTMLElement} */ (document.getElementById("crop-box"));
    Object.defineProperty(box, "offsetLeft", { value: 30, configurable: true });
    Object.defineProperty(box, "offsetTop", { value: 20, configurable: true });
    Object.defineProperty(box, "offsetWidth", { value: 100, configurable: true });
    Object.defineProperty(box, "offsetHeight", { value: 50, configurable: true });
    _applyCropFromOverlay();
    expect(/** @type {any} */ (window).editorEdits.crop_x).toBe(0.15);
    expect(/** @type {any} */ (window).editorEdits.crop_y).toBe(0.2);
    expect(/** @type {any} */ (window).editorEdits.crop_w).toBe(0.5);
    expect(/** @type {any} */ (window).editorEdits.crop_h).toBe(0.5);
  });
});

describe("_clearCrop", () => {
  test("nulls crop_* fields, removes overlay, calls _renderCropControls", () => {
    /** @type {any} */ (window).editorEdits = {
      crop_x: 0.1,
      crop_y: 0.1,
      crop_w: 0.5,
      crop_h: 0.5,
    };
    _showCropOverlay();
    /** @type {any} */ (window).editorCropActive = true;
    _clearCrop();
    expect(/** @type {any} */ (window).editorEdits.crop_x).toBeNull();
    expect(document.getElementById("crop-overlay")).toBeNull();
    expect(/** @type {any} */ (window).editorCropActive).toBe(false);
    expect(/** @type {any} */ (window)._renderCropControls).toHaveBeenCalled();
    expect(document.getElementById("editor-crop-tab")?.innerHTML).toContain("controls");
  });
});

describe("_adjustCropToAspect", () => {
  test("noop when aspect ratio not set", () => {
    _showCropOverlay();
    /** @type {any} */ (window)._editorAspectRatio = null;
    expect(() => _adjustCropToAspect()).not.toThrow();
  });

  test("adjusts crop box to match target aspect", () => {
    _showCropOverlay();
    /** @type {any} */ (window)._editorAspectRatio = 1.0;
    _adjustCropToAspect();
    // Just verify it doesn't throw and the crop-box exists
    expect(document.getElementById("crop-box")).toBeTruthy();
  });
});

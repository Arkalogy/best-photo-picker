// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _applyCustomRatio,
  _applyFilter,
  _applyStyle,
  _autoStraighten,
  _editorAutoEnhance,
  _editorDateChanged,
  _editorFlipH,
  _editorFlipV,
  _editorOnZoomSlider,
  _editorQuickRotate,
  _editorReset,
  _editorResetStyle,
  _editorRevertToOriginal,
  _editorRotate,
  _editorSetZoom,
  _editorSliderChange,
  _editorSwitchTab,
  _editorTabClick,
  _editorZoomIn,
  _editorZoomOut,
  _hideBefore,
  _openAdjustSlider,
  _renderCropControls,
  _showBefore,
  _showCustomRatio,
  _toggleBWMode,
  _updateSliders,
  closeEditor,
  openEditor,
} from "../bpp/web/static/js/modules/editor.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="lightbox">
      <div class="lb-img-wrapper">
        <img id="lb-img" />
      </div>
      <div class="lightbox-panel"></div>
    </div>
    <input id="editor-zoom-slider" type="range" />
    <span id="editor-zoom-pct"></span>
    <div id="editor-adjust-tab" class="hidden"></div>
    <div id="editor-styles-tab" class="hidden"></div>
    <div id="editor-crop-tab" class="hidden"></div>
    <div id="editor-remove-tab" class="hidden"></div>
    <div class="editor-tab" data-tab="adjust"></div>
    <div class="editor-tab" data-tab="styles"></div>
    <div class="editor-tab" data-tab="crop"></div>
    <div class="editor-tab" data-tab="remove"></div>
  `;
  /** @type {any} */ (window).editorActive = false;
  /** @type {any} */ (window).editorEdits = {};
  /** @type {any} */ (window).editorOriginalEdits = null;
  /** @type {any} */ (window).editorCropActive = false;
  /** @type {any} */ (window)._redeyeMode = false;
  /** @type {any} */ (window)._inpaintMode = false;
  /** @type {any} */ (window)._editorRevertPending = false;
  /** @type {any} */ (window)._cropSavedPerspective = null;
  /** @type {any} */ (window)._activeAdjustSlider = null;
  /** @type {any} */ (window)._editorAspectRatio = null;
  /** @type {any} */ (window).lbZoom = 1;
  /** @type {any} */ (window).LB_ZOOM_MIN = 0.1;
  /** @type {any} */ (window).LB_ZOOM_MAX = 10;
  /** @type {any} */ (window).lightboxIdx = 0;
  /** @type {any} */ (window).currentGridItems = [
    { id: 5, filepath: "/a.jpg", thumb_hash: "h1", date: "2024-01-01T12:00:00", face_count: 0 },
  ];
  /** @type {any} */ (window)._lbApplyTransform = vi.fn();
  /** @type {any} */ (window)._lbShowZoomIndicator = vi.fn();
  /** @type {any} */ (window).lbResetZoom = vi.fn();
  /** @type {any} */ (window).updateLightboxFaces = vi.fn();
  /** @type {any} */ (window).updateLightboxActions = vi.fn();
  /** @type {any} */ (window).openLightbox = vi.fn();
  /** @type {any} */ (window)._setAspectRatio = vi.fn();
  /** @type {any} */ (window)._toggleCropOverlay = vi.fn();
  /** @type {any} */ (window)._removeCropOverlay = vi.fn();
  /** @type {any} */ (window)._applyCropFromOverlay = vi.fn();
  /** @type {any} */ (window)._removeRedeyeOverlay = vi.fn();
  /** @type {any} */ (window)._showInpaintOverlay = vi.fn();
  /** @type {any} */ (window)._removeInpaintOverlay = vi.fn();
  /** @type {any} */ (window)._renderRemoveControls = vi.fn(() => "");
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "editorActive",
    "editorEdits",
    "editorOriginalEdits",
    "editorCropActive",
    "_redeyeMode",
    "_inpaintMode",
    "_editorRevertPending",
    "_cropSavedPerspective",
    "_activeAdjustSlider",
    "_editorAspectRatio",
    "lbZoom",
    "LB_ZOOM_MIN",
    "LB_ZOOM_MAX",
    "lightboxIdx",
    "currentGridItems",
    "_lbApplyTransform",
    "_lbShowZoomIndicator",
    "lbResetZoom",
    "updateLightboxFaces",
    "updateLightboxActions",
    "openLightbox",
    "_setAspectRatio",
    "_toggleCropOverlay",
    "_removeCropOverlay",
    "_applyCropFromOverlay",
    "_removeRedeyeOverlay",
    "_showInpaintOverlay",
    "_removeInpaintOverlay",
    "_renderRemoveControls",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
});

/**
 * @param {object} body
 */
function jsonResp(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("openEditor / closeEditor", () => {
  test("openEditor populates editorEdits, marks active, renders panel", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ edits: { brightness: 1.2 } }))
    );
    await openEditor("adjust");
    expect(/** @type {any} */ (window).editorActive).toBe(true);
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.2);
    expect(document.getElementById("lightbox")?.classList.contains("editor-mode")).toBe(true);
    expect(document.querySelector(".lightbox-panel")?.innerHTML).toContain("Edit Photo");
  });

  test("openEditor with empty edits uses defaults", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await openEditor();
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.0);
  });

  test("closeEditor(false) reverts image src", () => {
    /** @type {any} */ (window).editorActive = true;
    closeEditor(false);
    expect(/** @type {any} */ (window).editorActive).toBe(false);
    expect(/** @type {any} */ (window).editorCropActive).toBe(false);
    expect(document.getElementById("lightbox")?.classList.contains("editor-mode")).toBe(false);
  });

  test("closeEditor(true) saves edits", async () => {
    /** @type {any} */ (window).editorActive = true;
    /** @type {any} */ (window).editorEdits = {
      brightness: 1.5,
      contrast: 1,
      saturation: 1,
      sharpness: 1,
    };
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    closeEditor(true);
    // Save is fire-and-forget — wait for the promise chain
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalled();
  });
});

describe("_editorTabClick / _editorSwitchTab", () => {
  test("_editorTabClick opens editor when not active", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await _editorTabClick("crop");
    expect(/** @type {any} */ (window).editorActive).toBe(true);
  });

  test("_editorSwitchTab toggles active class on matching tab", () => {
    _editorSwitchTab("styles");
    const t = /** @type {HTMLElement} */ (document.querySelector('.editor-tab[data-tab="styles"]'));
    expect(t.classList.contains("active")).toBe(true);
    expect(document.getElementById("editor-styles-tab")?.classList.contains("hidden")).toBe(false);
  });

  test("switching to crop tab triggers _toggleCropOverlay when crop not active", () => {
    /** @type {any} */ (window).editorEdits = { perspective_v: 0.3, perspective_h: -0.1 };
    _editorSwitchTab("crop");
    expect(/** @type {any} */ (window)._toggleCropOverlay).toHaveBeenCalled();
    // Saves perspective for restore
    expect(/** @type {any} */ (window)._cropSavedPerspective).toEqual({ v: 0.3, h: -0.1 });
  });
});

describe("_editorSetZoom / _editorZoomIn / _editorZoomOut", () => {
  test("clamps zoom to LB_ZOOM_MAX upward", () => {
    _editorSetZoom(50);
    expect(/** @type {any} */ (window).lbZoom).toBe(10);
  });

  test("snaps to 1 (no pan) when below 1.01 threshold (this also catches under-clamp)", () => {
    _editorSetZoom(1.005);
    expect(/** @type {any} */ (window).lbZoom).toBe(1);
    _editorSetZoom(0.001);
    expect(/** @type {any} */ (window).lbZoom).toBe(1);
  });

  test("zoomIn / zoomOut multiply / divide by 1.3", () => {
    /** @type {any} */ (window).lbZoom = 2;
    _editorZoomIn();
    expect(/** @type {any} */ (window).lbZoom).toBeCloseTo(2.6, 5);
    _editorZoomOut();
    expect(/** @type {any} */ (window).lbZoom).toBeCloseTo(2, 5);
  });

  test("_editorOnZoomSlider delegates to setZoom", () => {
    _editorOnZoomSlider(3);
    expect(/** @type {any} */ (window).lbZoom).toBe(3);
  });
});

describe("_editorQuickRotate", () => {
  test("adds 90° to current rotation", async () => {
    /** @type {any} */ (window).editorActive = true;
    /** @type {any} */ (window).editorEdits = { rotation: 0 };
    await _editorQuickRotate();
    expect(/** @type {any} */ (window).editorEdits.rotation).toBe(90);
    await _editorQuickRotate();
    expect(/** @type {any} */ (window).editorEdits.rotation).toBe(180);
  });

  test("wraps at 360°", async () => {
    /** @type {any} */ (window).editorActive = true;
    /** @type {any} */ (window).editorEdits = { rotation: 270 };
    await _editorQuickRotate();
    expect(/** @type {any} */ (window).editorEdits.rotation).toBe(0);
  });
});

describe("_editorRotate / _editorFlipH / _editorFlipV", () => {
  test("_editorRotate adds degrees with mod 360", () => {
    /** @type {any} */ (window).editorEdits = { rotation: 0 };
    _editorRotate(-90);
    expect(/** @type {any} */ (window).editorEdits.rotation).toBe(270);
  });

  test("_editorFlipH / _editorFlipV toggle", () => {
    /** @type {any} */ (window).editorEdits = { flip_h: false, flip_v: false };
    _editorFlipH();
    expect(/** @type {any} */ (window).editorEdits.flip_h).toBe(true);
    _editorFlipV();
    expect(/** @type {any} */ (window).editorEdits.flip_v).toBe(true);
  });
});

describe("_editorSliderChange", () => {
  test("updates editorEdits and clears filter_name", () => {
    /** @type {any} */ (window).editorEdits = { filter_name: "Vintage" };
    _editorSliderChange("brightness", "1.5");
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.5);
    expect(/** @type {any} */ (window).editorEdits.filter_name).toBeNull();
  });
});

describe("_openAdjustSlider", () => {
  test("toggles current slider on/off", () => {
    _openAdjustSlider("exposure");
    expect(/** @type {any} */ (window)._activeAdjustSlider).toBe("exposure");
    _openAdjustSlider("exposure");
    expect(/** @type {any} */ (window)._activeAdjustSlider).toBeNull();
  });
});

describe("_applyStyle / _editorResetStyle / _applyFilter", () => {
  test("_applyStyle applies params + preserves geometry", () => {
    /** @type {any} */ (window).editorEdits = {
      crop_x: 0.1,
      crop_y: 0.1,
      crop_w: 0.5,
      crop_h: 0.5,
      rotation: 90,
      flip_h: false,
      flip_v: false,
      straighten: 0,
      perspective_v: 0,
      perspective_h: 0,
      redeye_points: null,
    };
    _applyStyle("Vivid|Warm");
    expect(/** @type {any} */ (window).editorEdits.crop_x).toBe(0.1);
    expect(/** @type {any} */ (window).editorEdits.rotation).toBe(90);
    expect(/** @type {any} */ (window).editorEdits.warmth).toBe(0.3);
    expect(/** @type {any} */ (window).editorEdits.filter_name).toBe("Vivid|Warm");
  });

  test("_applyStyle('Standard|Neutral') routes to reset", () => {
    /** @type {any} */ (window).editorEdits = { brightness: 1.5, crop_x: null };
    _applyStyle("Standard|Neutral");
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.0);
  });

  test("_editorResetStyle resets to defaults but preserves geometry+redeye", () => {
    /** @type {any} */ (window).editorEdits = {
      brightness: 1.7,
      contrast: 1.5,
      saturation: 1.5,
      crop_x: 0.2,
      rotation: 180,
      redeye_points: [{ x: 1, y: 2 }],
    };
    _editorResetStyle();
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.0);
    expect(/** @type {any} */ (window).editorEdits.crop_x).toBe(0.2);
    expect(/** @type {any} */ (window).editorEdits.redeye_points).toEqual([{ x: 1, y: 2 }]);
  });

  test("_applyFilter sets filter_name", () => {
    /** @type {any} */ (window).editorEdits = { rotation: 0, redeye_points: null };
    _applyFilter("Vintage");
    expect(/** @type {any} */ (window).editorEdits.filter_name).toBe("Vintage");
    expect(/** @type {any} */ (window).editorEdits.warmth).toBe(0.3);
  });

  test("_applyFilter('None') resets style", () => {
    /** @type {any} */ (window).editorEdits = { brightness: 1.5 };
    _applyFilter("None");
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.0);
  });
});

describe("_toggleBWMode", () => {
  test("flips saturation between 0 and default", () => {
    /** @type {any} */ (window).editorEdits = { saturation: 1.0 };
    _toggleBWMode();
    expect(/** @type {any} */ (window).editorEdits.saturation).toBe(0.0);
    _toggleBWMode();
    expect(/** @type {any} */ (window).editorEdits.saturation).toBe(1.0);
  });
});

describe("_autoStraighten", () => {
  test("toasts and updates straighten on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ angle: 1.5 }))
    );
    /** @type {any} */ (window).editorEdits = {};
    await _autoStraighten();
    expect(/** @type {any} */ (window).editorEdits.straighten).toBe(1.5);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Auto-straighten"
    );
  });

  test("toasts on error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await _autoStraighten();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't auto-straighten"
    );
  });
});

describe("_showCustomRatio / _applyCustomRatio", () => {
  test("show unhides the custom row", () => {
    document.body.innerHTML += `
      <div id="editor-custom-ratio-row" class="hidden">
        <input id="editor-custom-ratio-input" />
      </div>
    `;
    _showCustomRatio();
    expect(document.getElementById("editor-custom-ratio-row")?.classList.contains("hidden")).toBe(
      false
    );
  });

  test("applyCustomRatio parses '16:9' and calls _setAspectRatio", () => {
    document.body.innerHTML += `
      <input id="editor-custom-ratio-input" value="16:9" />
      <button id="editor-custom-ratio-btn"></button>
      <div id="editor-custom-ratio-row"></div>
    `;
    _applyCustomRatio();
    expect(/** @type {any} */ (window)._setAspectRatio).toHaveBeenCalledWith(
      16 / 9,
      expect.any(Object)
    );
  });

  test("applyCustomRatio toasts on bad input", () => {
    document.body.innerHTML += `<input id="editor-custom-ratio-input" value="garbage" />`;
    _applyCustomRatio();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Use format"
    );
  });
});

describe("_renderCropControls", () => {
  test("returns HTML with aspect ratio pills + transform buttons", () => {
    /** @type {any} */ (window).editorEdits = { straighten: 0, perspective_v: 0, perspective_h: 0 };
    const html = _renderCropControls();
    expect(html).toContain("Aspect Ratio");
    expect(html).toContain("editor-aspect-pill");
    expect(html).toContain("Transform");
    expect(html).toContain("editor-transform-btn");
  });

  test("shows 'Apply Crop' label when active", () => {
    /** @type {any} */ (window).editorCropActive = true;
    /** @type {any} */ (window).editorEdits = {};
    expect(_renderCropControls()).toContain("Apply Crop");
  });

  test("shows 'Clear Crop' button when crop_x is set", () => {
    /** @type {any} */ (window).editorEdits = { crop_x: 0.1 };
    expect(_renderCropControls()).toContain("Clear Crop");
  });
});

describe("_editorAutoEnhance", () => {
  test("applies returned params silently (live preview is the feedback)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          params: {
            "/a.jpg": { brightness: 1.2, contrast: 1.1, saturation: 1.05, sharpness: 1.0 },
          },
        })
      )
    );
    /** @type {any} */ (window).editorEdits = {};
    await _editorAutoEnhance();
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.2);
    // No success toast — the live preview + moved sliders show the result.
    expect(document.querySelector("#toast-container .toast")).toBeNull();
  });

  test("toasts on error key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ errors: { "/a.jpg": "no model" } }))
    );
    await _editorAutoEnhance();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't auto-enhance"
    );
  });
});

describe("_editorReset", () => {
  test("resets but preserves redeye_points", () => {
    /** @type {any} */ (window).editorEdits = {
      brightness: 1.5,
      redeye_points: [{ x: 1, y: 2 }],
    };
    _editorReset();
    expect(/** @type {any} */ (window).editorEdits.brightness).toBe(1.0);
    expect(/** @type {any} */ (window).editorEdits.redeye_points).toEqual([{ x: 1, y: 2 }]);
  });
});

describe("_editorRevertToOriginal", () => {
  test("posts reset-edits + toasts", async () => {
    /** @type {any} */ (window).editorActive = true;
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await _editorRevertToOriginal();
    expect(fetchMock).toHaveBeenCalled();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Reverted to original"
    );
  });

  test("guards against double-call via _editorRevertPending", async () => {
    /** @type {any} */ (window)._editorRevertPending = true;
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await _editorRevertToOriginal();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("_showBefore / _hideBefore", () => {
  test("toggles editor-before class on lb-img", () => {
    _showBefore();
    expect(document.getElementById("lb-img")?.classList.contains("editor-before")).toBe(true);
    _hideBefore();
    expect(document.getElementById("lb-img")?.classList.contains("editor-before")).toBe(false);
  });
});

describe("_editorDateChanged", () => {
  test("PUTs new date and updates photo metadata", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          date: "2024-06-15T10:00:00",
          date_day: "2024-06-15",
          date_month: "2024-06",
        })
      )
    );
    await _editorDateChanged("2024-06-15T10:00");
    /** @type {any} */
    const win = window;
    const items = /** @type {any[]} */ (win.currentGridItems);
    expect(items[0].date).toBe("2024-06-15T10:00:00");
    expect(items[0].date_day).toBe("2024-06-15");
  });

  test("noop when date unchanged", async () => {
    /** @type {any} */ (window).currentGridItems[0].date = "2024-01-01T12:00:00";
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await _editorDateChanged("2024-01-01T12:00");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("_updateSliders", () => {
  test("noop when sliders not in DOM (just exercises code path)", () => {
    /** @type {any} */ (window).editorEdits = { brightness: 1.5 };
    expect(() => _updateSliders()).not.toThrow();
  });
});

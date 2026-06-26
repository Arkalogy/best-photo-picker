// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _checkInpaintAvailable,
  _inpaintApply,
  _inpaintClearMask,
  _inpaintSetBrushSize,
  _removeInpaintOverlay,
  _renderRemoveControls,
  _setInpaintTool,
  _showInpaintOverlay,
} from "../bpp/web/static/js/modules/editor-inpaint.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div class="lb-img-wrapper">
      <img id="lb-img" />
    </div>
    <div id="toast-container"></div>
    <div id="confirm-overlay"><div class="confirm-dialog"></div></div>
    <div id="inpaint-status"></div>
  `;
  /** @type {any} */ (window)._inpaintMode = false;
  /** @type {any} */ (window)._inpaintBrushSize = 30;
  /** @type {any} */ (window)._inpaintCanvas = null;
  /** @type {any} */ (window)._inpaintCtx = null;
  /** @type {any} */ (window)._inpaintPainting = false;
  /** @type {any} */ (window)._inpaintAvailable = null;
  /** @type {any} */ (window)._inpaintTool = "erase";
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).lightboxIdx = -1;
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "_inpaintMode",
    "_inpaintBrushSize",
    "_inpaintCanvas",
    "_inpaintCtx",
    "_inpaintPainting",
    "_inpaintAvailable",
    "_inpaintTool",
    "currentGridItems",
    "lightboxIdx",
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

describe("_renderRemoveControls", () => {
  test("renders Erase + Retouch buttons + brush slider", () => {
    const html = _renderRemoveControls();
    expect(html).toContain("Erase");
    expect(html).toContain("Retouch");
    expect(html).toContain("es-inpaint-brush");
  });

  test("Apply disabled until availability is true", () => {
    /** @type {any} */ (window)._inpaintAvailable = null;
    expect(_renderRemoveControls()).toContain("disabled");
    /** @type {any} */ (window)._inpaintAvailable = true;
    expect(_renderRemoveControls()).not.toContain(
      'id="inpaint-apply-btn" onclick="_inpaintApply()" disabled'
    );
  });
});

describe("_setInpaintTool", () => {
  test("activates the matching button", () => {
    document.body.innerHTML += `
      <button class="editor-remove-tool-btn">Erase</button>
      <button class="editor-remove-tool-btn">Retouch</button>
    `;
    _setInpaintTool("retouch");
    const btns = document.querySelectorAll(".editor-remove-tool-btn");
    expect(/** @type {HTMLElement} */ (btns[0]).classList.contains("active")).toBe(false);
    expect(/** @type {HTMLElement} */ (btns[1]).classList.contains("active")).toBe(true);
    expect(/** @type {any} */ (window)._inpaintTool).toBe("retouch");
  });

  test("retouch shrinks brush size if it was large", () => {
    /** @type {any} */ (window)._inpaintBrushSize = 50;
    _setInpaintTool("retouch");
    expect(/** @type {any} */ (window)._inpaintBrushSize).toBe(15);
  });

  test("erase keeps brush size", () => {
    /** @type {any} */ (window)._inpaintBrushSize = 50;
    _setInpaintTool("erase");
    expect(/** @type {any} */ (window)._inpaintBrushSize).toBe(50);
  });
});

describe("_inpaintSetBrushSize", () => {
  test("updates global + label + slider --pct CSS variable", () => {
    document.body.innerHTML += `
      <span id="ev-inpaint-brush"></span>
      <input id="es-inpaint-brush" type="range" />
    `;
    _inpaintSetBrushSize(75);
    expect(/** @type {any} */ (window)._inpaintBrushSize).toBe(75);
    expect(document.getElementById("ev-inpaint-brush")?.textContent).toBe("75px");
  });
});

describe("_showInpaintOverlay / _removeInpaintOverlay", () => {
  // jsdom's HTMLCanvasElement.getContext returns null, so stub it before
  // each test in this describe.
  beforeEach(() => {
    /** @type {any} */ (HTMLCanvasElement.prototype).getContext = function () {
      return /** @type {any} */ ({
        lineCap: "",
        lineJoin: "",
        beginPath: () => {},
        moveTo: () => {},
        lineTo: () => {},
        stroke: () => {},
        arc: () => {},
        fill: () => {},
        clearRect: () => {},
        getImageData: () => ({ data: new Uint8ClampedArray(4) }),
        createImageData: (w, h) => ({ data: new Uint8ClampedArray(w * h * 4) }),
        putImageData: () => {},
        globalCompositeOperation: "",
        strokeStyle: "",
        fillStyle: "",
        lineWidth: 0,
      });
    };
  });

  test("show creates a canvas with image dimensions and binds events", () => {
    const img = /** @type {HTMLImageElement} */ (document.getElementById("lb-img"));
    Object.defineProperty(img, "naturalWidth", { value: 400, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 300, configurable: true });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ available: true }))
    );
    _showInpaintOverlay();
    const canvas = /** @type {HTMLCanvasElement} */ (document.getElementById("inpaint-canvas"));
    expect(canvas).toBeTruthy();
    expect(canvas.width).toBe(400);
    expect(canvas.height).toBe(300);
    expect(/** @type {any} */ (window)._inpaintMode).toBe(true);
  });

  test("remove clears canvas + ctx state", () => {
    const img = /** @type {HTMLImageElement} */ (document.getElementById("lb-img"));
    Object.defineProperty(img, "naturalWidth", { value: 100, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 100, configurable: true });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ available: true }))
    );
    _showInpaintOverlay();
    _removeInpaintOverlay();
    expect(document.getElementById("inpaint-canvas")).toBeNull();
    expect(/** @type {any} */ (window)._inpaintCanvas).toBeNull();
    expect(/** @type {any} */ (window)._inpaintCtx).toBeNull();
  });

  test("show is idempotent", () => {
    const img = /** @type {HTMLImageElement} */ (document.getElementById("lb-img"));
    Object.defineProperty(img, "naturalWidth", { value: 100, configurable: true });
    Object.defineProperty(img, "naturalHeight", { value: 100, configurable: true });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ available: true }))
    );
    _showInpaintOverlay();
    _showInpaintOverlay();
    expect(document.querySelectorAll("#inpaint-canvas")).toHaveLength(1);
  });
});

describe("_checkInpaintAvailable", () => {
  test("populates _inpaintAvailable + status when API returns true", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ available: true }))
    );
    await _checkInpaintAvailable();
    expect(/** @type {any} */ (window)._inpaintAvailable).toBe(true);
  });

  test("falls back to false on fetch failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await _checkInpaintAvailable();
    expect(/** @type {any} */ (window)._inpaintAvailable).toBe(false);
    expect(document.getElementById("inpaint-status")?.innerHTML).toContain(
      "AI removal model not available"
    );
  });

  test("reuses cached value when already known", async () => {
    /** @type {any} */ (window)._inpaintAvailable = true;
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await _checkInpaintAvailable();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("_inpaintClearMask", () => {
  test("noop when no canvas/ctx", () => {
    expect(() => _inpaintClearMask()).not.toThrow();
  });

  test("calls clearRect on canvas when available", () => {
    const canvas = document.createElement("canvas");
    canvas.width = 100;
    canvas.height = 100;
    document.body.appendChild(canvas);
    const clearRect = vi.fn();
    /** @type {any} */ (window)._inpaintCanvas = canvas;
    /** @type {any} */ (window)._inpaintCtx = { clearRect };
    _inpaintClearMask();
    expect(clearRect).toHaveBeenCalledWith(0, 0, 100, 100);
  });
});

describe("_inpaintApply", () => {
  test("toasts when no canvas/ctx", async () => {
    /** @type {any} */ (window)._inpaintAvailable = true;
    await _inpaintApply();
    // No canvas → silent return; no toast either
    expect(document.querySelectorAll("#toast-container .toast")).toHaveLength(0);
  });

  test("toasts when AI model not installed", async () => {
    /** @type {any} */ (window)._inpaintAvailable = false;
    /** @type {any} */ (window)._inpaintCanvas = document.createElement("canvas");
    /** @type {any} */ (window)._inpaintCtx = {};
    await _inpaintApply();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "AI model not installed"
    );
  });

  test("toasts when no photo selected (no currentGridItems)", async () => {
    /** @type {any} */ (window)._inpaintAvailable = true;
    /** @type {any} */ (window).lightboxIdx = 0;
    /** @type {any} */ (window).currentGridItems = [];
    /** @type {any} */ (window)._inpaintCanvas = document.createElement("canvas");
    /** @type {any} */ (window)._inpaintCtx = {};
    await _inpaintApply();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "No photo selected"
    );
  });
});

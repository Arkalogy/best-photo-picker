// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  applySettings,
  getCurrentSettings,
  syncPresetButtons,
} from "../bpp/web/static/js/modules/presets.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <select id="preset-select">
      <option value="">Load preset...</option>
      <option value="vacation">vacation</option>
      <option value="portraits">portraits</option>
    </select>
    <button id="btn-save-preset">Save</button>
    <button id="btn-update-preset" style="display:none">Update</button>
    <button id="btn-delete-preset" style="display:none">Delete</button>
    <input data-param="blur_weight" value="0.5">
    <span></span>
    <input data-param="face_weight" value="0.7">
    <span></span>
    <input id="param-k" value="50">
  `;
  /** @type {any} */ (window).selectedFaceIds = new Set();
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).scheduleRecompute = vi.fn();
  /** @type {any} */ (window).renderFaceGallery = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = "";
  delete (/** @type {any} */ (window).selectedFaceIds);
  delete (/** @type {any} */ (window).faceClusters);
  delete (/** @type {any} */ (window).scheduleRecompute);
  delete (/** @type {any} */ (window).renderFaceGallery);
});

const sel = () => /** @type {HTMLSelectElement} */ (document.getElementById("preset-select"));

describe("syncPresetButtons", () => {
  test("hides update/delete + shows 'Save' label when no preset selected", () => {
    sel().value = "";
    syncPresetButtons();
    expect(
      /** @type {HTMLElement} */ (document.getElementById("btn-update-preset")).style.display
    ).toBe("none");
    expect(
      /** @type {HTMLElement} */ (document.getElementById("btn-delete-preset")).style.display
    ).toBe("none");
    expect(document.getElementById("btn-save-preset").textContent).toBe("Save");
  });

  test("shows update/delete + flips label to 'Save As…' when a preset is selected", () => {
    sel().value = "vacation";
    syncPresetButtons();
    expect(
      /** @type {HTMLElement} */ (document.getElementById("btn-update-preset")).style.display
    ).toBe("inline-block");
    expect(
      /** @type {HTMLElement} */ (document.getElementById("btn-delete-preset")).style.display
    ).toBe("inline-block");
    expect(document.getElementById("btn-save-preset").textContent).toBe("Save As…");
  });
});

describe("getCurrentSettings", () => {
  test("snapshots all data-param sliders + k", () => {
    const s = getCurrentSettings();
    expect(s.blur_weight).toBe(0.5);
    expect(s.face_weight).toBe(0.7);
    expect(s.k).toBe(50);
    expect(s.selected_faces).toBeUndefined();
  });

  test("includes selected_faces when the global Set is non-empty", () => {
    /** @type {any} */ (window).selectedFaceIds = new Set([1, 5, 7]);
    const s = getCurrentSettings();
    expect(s.selected_faces.sort()).toEqual([1, 5, 7]);
  });

  test("k falls back to 50 when the input is empty / NaN", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("param-k")).value = "";
    expect(getCurrentSettings().k).toBe(50);
  });
});

describe("applySettings", () => {
  test("writes slider values, refreshes labels, calls scheduleRecompute", () => {
    applySettings({ blur_weight: 0.9, face_weight: 0.2, k: 100 });
    expect(
      /** @type {HTMLInputElement} */ (document.querySelector('[data-param="blur_weight"]')).value
    ).toBe("0.9");
    expect(
      /** @type {HTMLInputElement} */ (document.querySelector('[data-param="face_weight"]')).value
    ).toBe("0.2");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("param-k")).value).toBe("100");
    expect(/** @type {any} */ (window).scheduleRecompute).toHaveBeenCalled();
  });

  test("ignores unknown keys", () => {
    expect(() => applySettings({ unknown: 1 })).not.toThrow();
  });

  test("rebuilds selectedFaceIds + re-renders face gallery when clusters present", () => {
    /** @type {any} */ (window).faceClusters = [{ cluster_id: 1 }, { cluster_id: 2 }];
    applySettings({ selected_faces: [1, 3] });
    /** @type {any} */
    const win = window;
    expect([...win.selectedFaceIds].sort()).toEqual([1, 3]);
    expect(win.renderFaceGallery).toHaveBeenCalled();
  });

  test("does NOT call renderFaceGallery when clusters are empty", () => {
    /** @type {any} */ (window).faceClusters = [];
    applySettings({ selected_faces: [1] });
    expect(/** @type {any} */ (window).renderFaceGallery).not.toHaveBeenCalled();
  });
});

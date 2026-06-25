// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getWizardFaceIds,
  _setWizardFaceIds,
  closeWizard,
  markWizardDone,
  shouldShowWizard,
  showWizard,
  wizToggleFace,
} from "../bpp/web/static/js/modules/wizard.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="wizard-overlay" class="hidden">
      <div id="wiz-icon"></div>
      <div id="wiz-title"></div>
      <div id="wiz-body"></div>
      <div id="wiz-actions"></div>
    </div>
    <div id="nudge-container"></div>
    <input id="param-k" value="50">
  `;
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    )
  );
  /** @type {any} */ (window).faceRecognitionAvailable = true;
  /** @type {any} */ (window).faceClusters = [
    {
      cluster_id: 1,
      photo_count: 10,
      representative: { thumb_hash: "h1", face_index: 0 },
    },
    {
      cluster_id: 2,
      photo_count: 5,
      representative: { thumb_hash: "h2", face_index: 1 },
    },
  ];
  _setWizardFaceIds(new Set());
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).faceRecognitionAvailable);
  delete (/** @type {any} */ (window).faceClusters);
});

const overlay = () => /** @type {HTMLElement} */ (document.getElementById("wizard-overlay"));

describe("shouldShowWizard", () => {
  test("false when face recognition is not available", () => {
    /** @type {any} */ (window).faceRecognitionAvailable = false;
    expect(shouldShowWizard()).toBe(false);
  });

  test("false when there are no clusters", () => {
    /** @type {any} */ (window).faceClusters = [];
    expect(shouldShowWizard()).toBe(false);
  });

  test("true when both are present and not yet completed", () => {
    expect(shouldShowWizard()).toBe(true);
  });

  test("false after markWizardDone has been called", () => {
    markWizardDone();
    // settings-client uses fetch under the hood for saveSetting, but it
    // mutates the in-memory cache synchronously — re-check
    // This test depends on settings-client._dbSettings being populated
    // which it isn't in this test. So shouldShowWizard checks
    // localStorage… actually it checks getSetting which reads
    // _dbSettings cache. The cache update inside saveSetting is sync.
    expect(shouldShowWizard()).toBe(false);
  });
});

describe("showWizard / closeWizard", () => {
  test("showWizard reveals the overlay and renders Step 1", () => {
    showWizard();
    expect(overlay().classList.contains("visible")).toBe(true);
    expect(document.getElementById("wiz-title").textContent).toBe("Who matters most?");
    // Renders one chip per cluster (capped at 16)
    expect(document.querySelectorAll(".face-chip")).toHaveLength(2);
  });

  test("closeWizard hides the overlay", () => {
    overlay().classList.add("visible");
    closeWizard();
    expect(overlay().classList.contains("visible")).toBe(false);
  });

  test("showWizard resets wizardFaceIds", () => {
    _setWizardFaceIds(new Set([99]));
    showWizard();
    expect(_getWizardFaceIds().size).toBe(0);
  });
});

describe("wizToggleFace", () => {
  test("first call adds the cluster id and disables-then-enables Next", () => {
    showWizard();
    expect(/** @type {HTMLButtonElement} */ (document.getElementById("wiz-next1")).disabled).toBe(
      true
    );
    wizToggleFace(1);
    expect(_getWizardFaceIds().has(1)).toBe(true);
    expect(/** @type {HTMLButtonElement} */ (document.getElementById("wiz-next1")).disabled).toBe(
      false
    );
  });

  test("second call on same id removes it; Next disables again", () => {
    showWizard();
    wizToggleFace(1);
    wizToggleFace(1);
    expect(_getWizardFaceIds().has(1)).toBe(false);
    expect(/** @type {HTMLButtonElement} */ (document.getElementById("wiz-next1")).disabled).toBe(
      true
    );
  });

  test("syncs the .selected class on chips", () => {
    showWizard();
    wizToggleFace(2);
    const chip1 = document.getElementById("wiz-chip-1");
    const chip2 = document.getElementById("wiz-chip-2");
    expect(chip2?.classList.contains("selected")).toBe(true);
    expect(chip1?.classList.contains("selected")).toBe(false);
  });
});

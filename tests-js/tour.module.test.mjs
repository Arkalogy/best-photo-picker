// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getTourStepIndex,
  _isTourActive,
  _resetTourState,
  TOUR_STEPS,
  advanceTourToPhase,
  buildTooltipHTML,
  endTour,
  findTourTarget,
  maybeAutoStartTour,
  nextTourStep,
  prevTourStep,
  removeTourOverlay,
  showTourStep,
  startTour,
} from "../bpp/web/static/js/modules/tour.mjs";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <button id="btn-import-toolbar">Import</button>
    <button id="btn-analyze-toolbar">Analyze</button>
    <button id="btn-settings-toolbar">Settings</button>
    <button id="btn-export">Export</button>
    <div id="nav-face-boost">People</div>
    <div id="album-list">Albums</div>
    <div class="card">A card</div>
  `;
  // jsdom doesn't compute layout — pretend everything is visible.
  Object.defineProperty(HTMLElement.prototype, "offsetParent", {
    configurable: true,
    get() {
      return this.parentNode;
    },
  });
  // jsdom returns 0 for offsetWidth/offsetHeight; stub to non-zero.
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
  HTMLElement.prototype.getBoundingClientRect = function () {
    return /** @type {DOMRect} */ ({
      x: 10,
      y: 20,
      left: 10,
      top: 20,
      right: 110,
      bottom: 120,
      width: 100,
      height: 100,
    });
  };
  /** @type {any} */ (window).getSetting = vi.fn(() => null);
  /** @type {any} */ (window).saveSetting = vi.fn();
  _resetTourState();
});

afterEach(() => {
  _resetTourState();
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).getSetting);
  delete (/** @type {any} */ (window).saveSetting);
});

describe("TOUR_STEPS", () => {
  test("each step has required fields", () => {
    expect(TOUR_STEPS.length).toBeGreaterThan(0);
    for (const s of TOUR_STEPS) {
      expect(s.id).toBeTruthy();
      expect(s.title).toBeTruthy();
      expect(s.text).toBeTruthy();
      expect(s.target).toBeTruthy();
      expect(s.phase).toBeGreaterThanOrEqual(1);
    }
  });
});

describe("startTour / endTour", () => {
  test("startTour sets active and renders step 0", () => {
    startTour();
    expect(_isTourActive()).toBe(true);
    expect(_getTourStepIndex()).toBe(0);
    expect(document.querySelector(".tour-overlay")).toBeTruthy();
  });

  test("endTour clears active, removes overlay, persists tour_done", async () => {
    const settings = await import("../bpp/web/static/js/modules/settings-client.mjs");
    const saveSpy = vi.spyOn(settings, "saveSetting").mockImplementation(() => {});
    startTour();
    endTour();
    expect(_isTourActive()).toBe(false);
    expect(document.querySelector(".tour-overlay")).toBeNull();
    expect(saveSpy).toHaveBeenCalledWith("tour_done", "true");
    saveSpy.mockRestore();
  });
});

describe("nextTourStep / prevTourStep", () => {
  test("nextTourStep advances index", () => {
    startTour();
    nextTourStep();
    expect(_getTourStepIndex()).toBe(1);
  });

  test("nextTourStep at last step ends tour and toasts", () => {
    startTour();
    // jump past the end
    for (let i = 0; i < TOUR_STEPS.length + 2; i++) nextTourStep();
    expect(_isTourActive()).toBe(false);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Tour complete"
    );
  });

  test("prevTourStep clamps to 0", () => {
    startTour();
    prevTourStep();
    expect(_getTourStepIndex()).toBe(0);
  });
});

describe("findTourTarget", () => {
  test("returns the element when target selector matches", () => {
    const el = findTourTarget(/** @type {any} */ ({ target: "#btn-import-toolbar" }));
    expect(el).toBeTruthy();
    expect(el?.id).toBe("btn-import-toolbar");
  });

  test("falls back when target missing and fallback matches", () => {
    const el = findTourTarget(
      /** @type {any} */ ({
        target: "#nope",
        fallback: ".card",
      })
    );
    expect(el).toBeTruthy();
    expect(el?.classList.contains("card")).toBe(true);
  });

  test("returns null when neither target nor fallback matches", () => {
    const el = findTourTarget(/** @type {any} */ ({ target: "#nope", fallback: "#alsono" }));
    expect(el).toBeNull();
  });
});

describe("buildTooltipHTML", () => {
  test("first step has no Back, has Next", () => {
    startTour();
    const html = buildTooltipHTML(TOUR_STEPS[0]);
    expect(html).not.toContain("Back");
    expect(html).toContain("Next");
    expect(html).toContain("1 of " + TOUR_STEPS.length);
  });

  test("last step has Done, not Next", () => {
    // Walk to the last step
    startTour();
    while (_getTourStepIndex() < TOUR_STEPS.length - 1) nextTourStep();
    if (_getTourStepIndex() >= TOUR_STEPS.length) {
      // ended early because some steps had no target
      return;
    }
    const html = buildTooltipHTML(TOUR_STEPS[_getTourStepIndex()]);
    expect(html).toContain("Done");
    expect(html).not.toContain("Next</button>");
  });

  test("includes title and text from step", () => {
    const html = buildTooltipHTML(
      /** @type {any} */ ({
        title: "Hello",
        text: "World",
        phase: 1,
        target: "#x",
        id: "x",
      })
    );
    expect(html).toContain("Hello");
    expect(html).toContain("World");
    expect(html).toContain("Skip tour");
  });
});

describe("showTourStep", () => {
  test("creates tour-overlay/highlight/tooltip/backdrop on body", () => {
    startTour();
    expect(document.querySelector(".tour-overlay")).toBeTruthy();
    expect(document.querySelector(".tour-highlight")).toBeTruthy();
    expect(document.querySelector(".tour-tooltip")).toBeTruthy();
    expect(document.querySelector(".tour-backdrop")).toBeTruthy();
  });

  test("waitFor steps make the highlight clickable and forward to target", () => {
    startTour();
    // step 0 is "import" with waitFor: "import_done"
    const step = TOUR_STEPS[0];
    expect(step.waitFor).toBeTruthy();
    const highlight = /** @type {HTMLElement} */ (document.querySelector(".tour-highlight"));
    expect(highlight.style.pointerEvents).toBe("auto");

    const target = /** @type {HTMLButtonElement} */ (document.querySelector(step.target));
    const clickSpy = vi.spyOn(target, "click");
    highlight.click();
    expect(clickSpy).toHaveBeenCalled();
  });

  test("re-rendering replaces the overlay (no duplicates)", () => {
    startTour();
    showTourStep();
    showTourStep();
    expect(document.querySelectorAll(".tour-overlay")).toHaveLength(1);
  });
});

describe("removeTourOverlay", () => {
  test("removes overlay if present, no-ops if not", () => {
    startTour();
    expect(document.querySelector(".tour-overlay")).toBeTruthy();
    removeTourOverlay();
    expect(document.querySelector(".tour-overlay")).toBeNull();
    expect(() => removeTourOverlay()).not.toThrow();
  });
});

describe("advanceTourToPhase", () => {
  test("no-ops when tour not active", () => {
    advanceTourToPhase(3);
    expect(_getTourStepIndex()).toBe(0);
  });

  test("jumps forward to the first step in the given phase", () => {
    startTour();
    const phase3Idx = TOUR_STEPS.findIndex((s) => s.phase >= 3);
    expect(phase3Idx).toBeGreaterThan(0);
    advanceTourToPhase(3);
    expect(_getTourStepIndex()).toBe(phase3Idx);
  });

  test("does not move backward", () => {
    startTour();
    advanceTourToPhase(3);
    const before = _getTourStepIndex();
    advanceTourToPhase(1);
    expect(_getTourStepIndex()).toBe(before);
  });
});

describe("maybeAutoStartTour", () => {
  test("starts tour after 600ms when tour_done not set", async () => {
    const settings = await import("../bpp/web/static/js/modules/settings-client.mjs");
    const getSpy = vi.spyOn(settings, "getSetting").mockReturnValue(null);
    maybeAutoStartTour();
    expect(_isTourActive()).toBe(false);
    vi.advanceTimersByTime(600);
    expect(_isTourActive()).toBe(true);
    getSpy.mockRestore();
  });

  test("does NOT start tour when tour_done is 'true'", async () => {
    const settings = await import("../bpp/web/static/js/modules/settings-client.mjs");
    const getSpy = vi.spyOn(settings, "getSetting").mockReturnValue("true");
    maybeAutoStartTour();
    vi.advanceTimersByTime(600);
    expect(_isTourActive()).toBe(false);
    getSpy.mockRestore();
  });
});

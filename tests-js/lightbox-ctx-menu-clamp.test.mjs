// @ts-check
/**
 * Lightbox context menu — viewport clamping.
 *
 * Bug we're guarding against: right-clicking near the bottom or right
 * edge of the window placed the menu so far down/right that items
 * fell below the viewport and were unreachable. showLbCtxMenu now
 * measures the rendered menu and flips/clamps the position so the
 * full menu stays on-screen.
 *
 * These tests stub menu.offsetWidth/offsetHeight (jsdom always reports
 * 0 for those), exercise edge cases, and assert the final pixel
 * coordinates.
 */
import { beforeEach, describe, expect, test, vi } from "vitest";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="lightbox">
      <div class="lb-img-wrapper" style="width:1000px;height:800px;position:relative">
        <img id="lb-img" />
      </div>
      <div id="lb-ctx-menu" class="hidden" style="position:fixed">
        <div id="lb-ctx-include"></div>
        <div id="lb-ctx-exclude"></div>
        <div id="lb-ctx-fav"></div>
        <div id="lb-ctx-enhance"></div>
        <div id="lb-ctx-tag"></div>
      </div>
    </div>
  `;
  /** @type {any} */ (globalThis.window).currentGridItems = [{ filepath: "/p.jpg" }];
  /** @type {any} */ (globalThis.window).lightboxIdx = 0;
  /** @type {any} */ (globalThis.window).overrides = {};
  /** @type {any} */ (globalThis.window).favorites = new Set();
  /** @type {any} */ (globalThis.window).faceClusters = [];

  // Stub the menu's measured size — jsdom doesn't lay out, so offsetWidth/
  // offsetHeight are always 0 unless we override them. 200×300 is a
  // realistic size for the lightbox ctx menu.
  const menu = /** @type {HTMLElement} */ (document.getElementById("lb-ctx-menu"));
  Object.defineProperty(menu, "offsetWidth", { configurable: true, value: 200 });
  Object.defineProperty(menu, "offsetHeight", { configurable: true, value: 300 });

  // Viewport: 1280 × 800 (matches Tauri default window size).
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 1280 });
  Object.defineProperty(window, "innerHeight", { configurable: true, value: 800 });
});

/**
 * Build a synthetic MouseEvent with .preventDefault/.stopPropagation stubs.
 * @param {number} clientX
 * @param {number} clientY
 */
function makeClick(clientX, clientY) {
  return /** @type {any} */ ({
    clientX,
    clientY,
    preventDefault: vi.fn(),
    stopPropagation: vi.fn(),
  });
}

describe("showLbCtxMenu — viewport clamp", () => {
  test("click in the middle places menu at the click point unchanged", async () => {
    const mod = await import("../bpp/web/static/js/modules/lightbox.mjs");
    mod.showLbCtxMenu(makeClick(400, 300));

    const menu = /** @type {HTMLElement} */ (document.getElementById("lb-ctx-menu"));
    expect(menu.classList.contains("hidden")).toBe(false);
    expect(menu.style.left).toBe("400px");
    expect(menu.style.top).toBe("300px");
  });

  test("click near bottom-right flips menu so it stays fully visible", async () => {
    const mod = await import("../bpp/web/static/js/modules/lightbox.mjs");
    // Click at (1250, 780). Menu is 200×300. Naive placement would
    // overflow right by (1250+200) - 1280 = 170px AND overflow bottom
    // by (780+300) - 800 = 280px.
    mod.showLbCtxMenu(makeClick(1250, 780));

    const menu = /** @type {HTMLElement} */ (document.getElementById("lb-ctx-menu"));
    const left = parseInt(menu.style.left, 10);
    const top = parseInt(menu.style.top, 10);
    // After clamp: right edge must be at most viewport - PAD (8).
    expect(
      left + 200,
      `menu right edge ${left + 200} should not exceed viewport width 1280 (-pad)`
    ).toBeLessThanOrEqual(1280 - 8);
    expect(
      top + 300,
      `menu bottom edge ${top + 300} should not exceed viewport height 800 (-pad)`
    ).toBeLessThanOrEqual(800 - 8);
  });

  test("click near bottom edge only flips vertically, leaves x alone", async () => {
    const mod = await import("../bpp/web/static/js/modules/lightbox.mjs");
    // Click at (400, 780): only the bottom would overflow.
    mod.showLbCtxMenu(makeClick(400, 780));

    const menu = /** @type {HTMLElement} */ (document.getElementById("lb-ctx-menu"));
    expect(menu.style.left, "x unchanged when only y overflows").toBe("400px");
    const top = parseInt(menu.style.top, 10);
    expect(top + 300).toBeLessThanOrEqual(800 - 8);
  });

  test("click in tiny viewport: menu pinned to the padded top-left", async () => {
    // Simulate a viewport smaller than the menu itself — clamp should
    // pin to PAD rather than producing negative coordinates.
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 150 });
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 200 });
    const mod = await import("../bpp/web/static/js/modules/lightbox.mjs");
    mod.showLbCtxMenu(makeClick(140, 190));

    const menu = /** @type {HTMLElement} */ (document.getElementById("lb-ctx-menu"));
    expect(menu.style.left, "left clamped to PAD when menu wider than viewport").toBe("8px");
    expect(menu.style.top, "top clamped to PAD when menu taller than viewport").toBe("8px");
  });
});

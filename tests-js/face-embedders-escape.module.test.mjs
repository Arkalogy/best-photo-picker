// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn().mockResolvedValue({}),
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
}));

const { _feInstallGlobalHandlers } =
  await import("../bpp/web/static/js/modules/face-embedders-popover.mjs");

/** Stands in for lightbox.mjs's app-wide ESC handler, which is
 *  registered on `document` (bubble phase) BEFORE the picker's. */
const appWideEscape = vi.fn();
/** @param {KeyboardEvent} e */
const appWideHandler = (e) => {
  if (e.key === "Escape") appWideEscape();
};

const pressEscape = () =>
  document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));

beforeEach(() => {
  document.body.innerHTML = `
    <div id="settings-overlay" class="visible">
      <button class="fe-overflow-trigger" data-arg0="nudenet_320n">⋯</button>
      <div class="fe-overflow-menu open" id="fe-ovr-nudenet_320n"><button>Item</button></div>
    </div>
    <div id="fe-acceptance-overlay"></div>
  `;
  appWideEscape.mockClear();
  document.addEventListener("keydown", appWideHandler);
  /** @type {any} */ (window).__feGlobalHandlersInstalled = false;
  _feInstallGlobalHandlers();
});

afterEach(() => {
  document.removeEventListener("keydown", appWideHandler);
  document.body.innerHTML = "";
});

describe("Escape in Settings → Models", () => {
  test("with a ⋯ menu open, Escape closes only the menu", () => {
    pressEscape();
    expect(document.querySelector(".fe-overflow-menu.open")).toBeNull();
    expect(appWideEscape).not.toHaveBeenCalled();
  });

  test("focus returns to the menu's ⋯ trigger", () => {
    pressEscape();
    expect(document.activeElement?.classList.contains("fe-overflow-trigger")).toBe(true);
  });

  test("a second Escape (nothing open) reaches the app-wide handler", () => {
    pressEscape();
    pressEscape();
    expect(appWideEscape).toHaveBeenCalledTimes(1);
  });

  test("with the license dialog open, Escape closes only the dialog", () => {
    document.querySelector(".fe-overflow-menu")?.classList.remove("open");
    const overlay = /** @type {HTMLElement} */ (document.getElementById("fe-acceptance-overlay"));
    overlay.classList.add("visible");
    pressEscape();
    expect(overlay.classList.contains("visible")).toBe(false);
    expect(appWideEscape).not.toHaveBeenCalled();
  });
});

describe("hideSettings", () => {
  test("closes any picker menu left open", async () => {
    const { hideSettings } = await import("../bpp/web/static/js/modules/modals.mjs");
    hideSettings();
    expect(document.querySelector(".fe-overflow-menu.open")).toBeNull();
    expect(document.getElementById("settings-overlay")?.classList.contains("visible")).toBe(false);
  });
});

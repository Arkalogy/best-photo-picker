// @ts-check
/**
 * Optimistic edits tell the truth. An override whose save failed used to
 * stay applied on screen with an "Included" toast and an Undo that
 * re-sent the failure; a failed Undo of a favorite or override went to
 * the console only; Clear library bypassed the API client and toasted a
 * bare "Failed to clear".
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/analysis.mjs", () => ({
  scheduleRecompute: vi.fn(),
  showEmptyLibrary: vi.fn(),
}));

import { setOverride } from "../bpp/web/static/js/modules/photos.mjs";
import { toggleFavorite } from "../bpp/web/static/js/modules/toolbar.mjs";
import { clearLibrary } from "../bpp/web/static/js/modules/utils.mjs";
import { showToast } from "../bpp/web/static/js/modules/toast.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <input id="clear-confirm-input" value="delete">
    <button id="btn-clear-library">Delete All</button>`;
  const win = /** @type {any} */ (window);
  win.overrides = {};
  win.favorites = new Set();
  win.selectedPaths = new Set();
  win.currentAlbumId = null;
  win.updateCardInPlace = vi.fn(() => true);
  win.renderAlbumNav = vi.fn();
  win.updateOverrideStats = vi.fn();
  win.showToast = showToast; // toolbar.mjs reaches the toast through window
});

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const toasts = () =>
  [...document.querySelectorAll("#toast-container .toast")].map((t) => t.textContent || "");
const flush = () => new Promise((r) => setTimeout(r, 0));

/** @param {(init: any, n: number) => Response | Promise<Response>} fn */
function stubFetch(fn) {
  let n = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_u, init) => fn(init, (n += 1)))
  );
}
const fail = () => new Response(JSON.stringify({ error: "database is locked" }), { status: 503 });
const ok = () => new Response(JSON.stringify({ ok: true }), { status: 200 });

describe("setOverride", () => {
  test("a failed save reverts the override and offers no undo", async () => {
    stubFetch(() => fail());
    await setOverride("/p/a.jpg", "include");
    expect(/** @type {any} */ (window).overrides["/p/a.jpg"]).toBeUndefined();
    expect(toasts().join(" ")).toMatch(/Couldn't save the override: database is locked/);
    expect(document.querySelector("#toast-container .toast-undo")).toBeNull();
  });

  test("a failed undo is reported and the local state follows the server", async () => {
    stubFetch((_init, n) => (n === 1 ? ok() : fail()));
    await setOverride("/p/a.jpg", "include");
    expect(/** @type {any} */ (window).overrides["/p/a.jpg"]).toBe("include");
    /** @type {HTMLElement} */ (document.querySelector("#toast-container .toast-undo")).click();
    await flush();
    await flush();
    expect(toasts().join(" ")).toMatch(/Couldn't undo the override: database is locked/);
    expect(/** @type {any} */ (window).overrides["/p/a.jpg"]).toBe("include");
  });
});

describe("toggleFavorite", () => {
  test("a failed undo is reported and the favorite stays as the server has it", async () => {
    stubFetch((_init, n) => (n === 1 ? ok() : fail()));
    await toggleFavorite("/p/a.jpg");
    expect(/** @type {any} */ (window).favorites.has("/p/a.jpg")).toBe(true);
    /** @type {HTMLElement} */ (document.querySelector("#toast-container .toast-undo")).click();
    await flush();
    await flush();
    expect(toasts().join(" ")).toMatch(/Couldn't undo the favorite: database is locked/);
    expect(/** @type {any} */ (window).favorites.has("/p/a.jpg")).toBe(true);
  });
});

describe("clearLibrary", () => {
  test("a refused clear names the action and the reason", async () => {
    stubFetch(() => fail());
    await clearLibrary();
    expect(toasts().join(" ")).toMatch(/Couldn't clear the library: database is locked/);
    expect(document.getElementById("btn-clear-library")?.textContent).toBe("Delete All");
  });
});

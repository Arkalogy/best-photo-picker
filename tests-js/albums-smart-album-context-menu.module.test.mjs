// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  albumFilterInput,
  cycleAlbumsSort,
  loadYearMonths,
  removeSmartAlbum,
  removeTagAlbum,
  renameSmartAlbum,
  renderAlbumNav,
  showSmartAlbumMenu,
  showTagAlbumMenu,
  switchAlbum,
  switchToMonth,
  toggleAlbumsCollapsed,
} from "../bpp/web/static/js/modules/albums.mjs";
import { setup, teardown, jsonResp } from "./_albums-setup.mjs";

beforeEach(setup);
afterEach(teardown);

describe("smart album context menu", () => {
  test("show creates the menu DOM", () => {
    showSmartAlbumMenu(
      /** @type {any} */ ({ preventDefault() {}, stopPropagation() {}, clientX: 30, clientY: 30 }),
      5,
      "Top"
    );
    expect(document.getElementById("smart-album-ctx")).toBeTruthy();
  });

  test("renameSmartAlbum noop on cancelled prompt", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = renameSmartAlbum(5, "Top");
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(false);
    await promise;
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("removeSmartAlbum noop on declined confirm", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = removeSmartAlbum(5, "Top");
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(false);
    await promise;
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("tag album menu", () => {
  test("showTagAlbumMenu renders rename + delete options", () => {
    showTagAlbumMenu(
      /** @type {any} */ ({ preventDefault() {}, stopPropagation() {}, clientX: 0, clientY: 0 }),
      9,
      "beach",
      42
    );
    const html = document.getElementById("smart-album-ctx")?.innerHTML || "";
    expect(html).toContain("Rename");
    expect(html).toContain("Delete tag");
  });

  test("removeTagAlbum DELETEs tag on accepted confirm", async () => {
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    const fetchMock = vi.fn(async () => jsonResp({ albums: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = removeTagAlbum(9, "beach", 42);
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(
      calls.find((c) => String(c[0]).includes("/api/v1/tags/42") && c[1]?.method === "DELETE")
    ).toBeDefined();
  });
});

describe("loadYearMonths / switchToMonth", () => {
  test("loadYearMonths populates the months container", async () => {
    document.body.innerHTML += `
      <details data-year="2024" data-album-id="50" open>
        <div class="nav-year-months"></div>
      </details>
    `;
    const detailsEl = /** @type {HTMLDetailsElement} */ (
      document.querySelector("details[data-year]")
    );
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          months: [
            { month: 1, count: 5 },
            { month: 6, count: 12 },
          ],
        })
      )
    );
    await loadYearMonths(detailsEl);
    expect(document.querySelectorAll(".nav-month-item")).toHaveLength(2);
  });

  test("switchToMonth re-renders when already in same album", () => {
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).currentView = "album";
    /** @type {any} */ (window).photos = [];
    /** @type {any} */ (window).albumList = [{ id: 7, album_type: "smart_time" }];
    expect(() => switchToMonth(7, "2024-06")).not.toThrow();
  });
});

// ── Album section: collapse / sort / filter ───────────────────────────────────

describe("Albums section state (localStorage)", () => {
  beforeEach(() => {
    localStorage.clear();
    /** @type {any} */ (window).albumList = [
      { id: 10, name: "Hawaii Trip", album_type: "manual", photo_count: 38, parent_id: null },
      { id: 11, name: "Baby Shower", album_type: "manual", photo_count: 24, parent_id: null },
      { id: 12, name: "NYC Visit", album_type: "manual", photo_count: 31, parent_id: null },
      { id: 13, name: "Christmas 2022", album_type: "manual", photo_count: 52, parent_id: null },
      { id: 14, name: "Graduation", album_type: "manual", photo_count: 16, parent_id: null },
    ];
    renderAlbumNav();
  });

  afterEach(() => {
    localStorage.clear();
  });

  test("albums section renders as <details> open by default", () => {
    const details = document.querySelector(".nav-albums-section");
    expect(details).toBeTruthy();
    expect(/** @type {HTMLDetailsElement} */ (details).hasAttribute("open")).toBe(true);
  });

  test("toggleAlbumsCollapsed closes the section and persists via the unified key", () => {
    toggleAlbumsCollapsed();
    // Storage migrated from `bpp-albums-collapsed` (legacy) to the
    // unified `bpp-nav-open-keys` map (true = open). Closed ⇒ stored
    // as false in the map.
    const map = JSON.parse(localStorage.getItem("bpp-nav-open-keys") || "{}");
    expect(map["section:albums"]).toBe(false);
    const details = document.querySelector(".nav-albums-section");
    expect(/** @type {HTMLDetailsElement} */ (details).hasAttribute("open")).toBe(false);
  });

  test("toggleAlbumsCollapsed twice reopens the section", () => {
    toggleAlbumsCollapsed();
    toggleAlbumsCollapsed();
    const map = JSON.parse(localStorage.getItem("bpp-nav-open-keys") || "{}");
    expect(map["section:albums"]).toBe(true);
    const details = document.querySelector(".nav-albums-section");
    expect(/** @type {HTMLDetailsElement} */ (details).hasAttribute("open")).toBe(true);
  });

  test("cycleAlbumsSort advances through sort modes", () => {
    expect(localStorage.getItem("bpp-albums-sort")).toBeNull(); // default name-asc
    cycleAlbumsSort(); // → name-desc
    expect(localStorage.getItem("bpp-albums-sort")).toBe("name-desc");
    cycleAlbumsSort(); // → count-desc
    expect(localStorage.getItem("bpp-albums-sort")).toBe("count-desc");
    cycleAlbumsSort(); // → count-asc
    expect(localStorage.getItem("bpp-albums-sort")).toBe("count-asc");
    cycleAlbumsSort(); // → date-desc
    expect(localStorage.getItem("bpp-albums-sort")).toBe("date-desc");
    cycleAlbumsSort(); // → wraps back to name-asc
    expect(localStorage.getItem("bpp-albums-sort")).toBe("name-asc");
  });

  test("cycleAlbumsSort name-asc renders albums in alphabetical order", () => {
    localStorage.setItem("bpp-albums-sort", "name-asc");
    renderAlbumNav();
    const items = [...document.querySelectorAll(".nav-albums-section .nav-item")];
    const names = items.map((el) => el.textContent?.trim().split("\n")[0].trim()).filter(Boolean);
    const sorted = [...names].sort((a, b) => (a ?? "").localeCompare(b ?? ""));
    expect(names).toEqual(sorted);
  });

  test("cycleAlbumsSort count-desc renders albums by photo count descending", () => {
    localStorage.setItem("bpp-albums-sort", "count-desc");
    renderAlbumNav();
    const counts = [...document.querySelectorAll(".nav-albums-section .nav-count")]
      .map((el) => parseInt(el.textContent || "0", 10))
      .filter((n) => !isNaN(n) && n > 0);
    for (let i = 1; i < counts.length; i++) {
      expect(counts[i]).toBeLessThanOrEqual(counts[i - 1]);
    }
  });

  test("albumFilterInput shows only matching albums", () => {
    albumFilterInput("ha");
    const items = [...document.querySelectorAll(".nav-albums-section .nav-item")];
    const names = items.map((el) => el.textContent?.trim().split("\n")[0].trim().toLowerCase());
    for (const name of names) {
      expect(name).toContain("ha");
    }
    expect(localStorage.getItem("bpp-albums-filter")).toBe("ha");
  });

  test("albumFilterInput empty string shows all albums", () => {
    albumFilterInput("zzz"); // hide everything
    albumFilterInput(""); // reset
    const items = document.querySelectorAll(".nav-albums-section .nav-item");
    expect(items.length).toBe(5);
  });

  test("albumFilterInput with no matches shows 'No match' hint", () => {
    albumFilterInput("xxxxxx");
    expect(document.querySelector(".nav-empty-hint")?.textContent).toBe("No match");
  });
});

describe("switchAlbum 404 fallback", () => {
  test("falls back to all-photos album when photo fetch returns 404", async () => {
    /** @type {any} */
    const win = window;
    win.albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 10 },
      { id: 99, album_type: "manual", name: "Stale Album", photo_count: 5 },
    ];
    win.switchAlbum = switchAlbum;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (/** @type {string} */ url) => {
        if (String(url).includes("/api/v1/albums/99/photos")) {
          return new Response(JSON.stringify({ error: "Not Found" }), {
            status: 404,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response(JSON.stringify({ photos: [], total: 0, album: {} }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      })
    );

    await switchAlbum(99);
    expect(win.currentAlbumId).toBe(1);
  });

  test("does not fall back when all-photos album is also absent", async () => {
    /** @type {any} */
    const win = window;
    win.albumList = [{ id: 99, album_type: "manual", name: "Stale Album" }];
    win.switchAlbum = switchAlbum;

    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "Not Found" }), {
            status: 404,
            headers: { "content-type": "application/json" },
          })
      )
    );

    await switchAlbum(99);
    expect(win.currentAlbumId).toBe(99);
  });
});

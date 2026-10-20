// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  deleteAlbumPrompt,
  loadAlbumList,
  moveAlbumTo,
  renderAlbumNav,
  showAlbumMoveMenu,
  showNewAlbumInput,
  switchAlbum,
  toggleFaceSort,
} from "../bpp/web/static/js/modules/albums.mjs";
import { setup, teardown, jsonResp } from "./_albums-setup.mjs";

beforeEach(setup);
afterEach(teardown);

describe("loadAlbumList", () => {
  test("populates window.albumList and renders nav", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({ albums: [{ id: 1, album_type: "all", name: "Library", photo_count: 12 }] })
      )
    );
    await loadAlbumList();
    expect(/** @type {any} */ (window).albumList).toHaveLength(1);
    expect(document.getElementById("album-list")?.innerHTML).toContain("Library");
  });

  test("survives a fetch failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await loadAlbumList();
    // Just verifies no throw — albumList stays empty
    expect(/** @type {any} */ (window).albumList).toEqual([]);
  });
});

describe("renderAlbumNav", () => {
  test("renders Library at top + Faces + Calendar + Favorites", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100, selected_count: 50 },
    ];
    renderAlbumNav();
    const html = document.getElementById("album-list")?.innerHTML || "";
    expect(html).toContain("Library");
    expect(html).toContain("Faces");
    expect(html).toContain("Calendar");
    expect(html).toContain("Favorites");
  });

  test("BPP Picks appears as sub-item under Library, not as standalone nav item", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100, selected_count: 50 },
    ];
    renderAlbumNav();
    const container = document.getElementById("album-list");
    // Sub-item must exist
    expect(container?.querySelector(".nav-subitem-picks")).toBeTruthy();
    // Standalone nav-item-picks must NOT exist
    expect(container?.querySelector(".nav-item-picks")).toBeFalsy();
  });

  test("BPP Picks sub-item shows selected_count from all-album (DB-accurate)", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100, selected_count: 42 },
    ];
    /** @type {any} */ (window).selectedPaths = new Set(); // in-memory count is 0 — should be ignored
    renderAlbumNav();
    const badge = document.querySelector(".nav-subitem-picks .nav-count");
    expect(badge?.textContent).toBe("42");
  });

  test("BPP Picks sub-item is active when currentView is picks", () => {
    /** @type {any} */ (window).currentView = "picks";
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100, selected_count: 10 },
    ];
    renderAlbumNav();
    expect(document.querySelector(".nav-subitem-picks")?.classList.contains("active")).toBe(true);
  });

  test("manual albums render in their section with delete button", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    renderAlbumNav();
    expect(document.getElementById("album-list")?.innerHTML).toContain("Trip");
  });

  test("smart album section appears when smart albums present", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 9, album_type: "smart_score", name: "Top Rated", photo_count: 10, rule: {} },
    ];
    renderAlbumNav();
    expect(document.getElementById("album-list")?.innerHTML).toContain("Smart Albums");
    expect(document.getElementById("album-list")?.innerHTML).toContain("Top Rated");
  });

  test("Faces folder appears for smart_person albums", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      {
        id: 9,
        album_type: "smart_person",
        name: "Alice",
        photo_count: 10,
        rule: { cluster_id: 1 },
      },
    ];
    /** @type {any} */ (window).faceClusters = [{ cluster_id: 1, photo_count: 10 }];
    renderAlbumNav();
    expect(document.getElementById("album-list")?.innerHTML).toContain("Alice");
  });

  test("noop when album-list element missing", () => {
    document.getElementById("album-list")?.remove();
    expect(() => renderAlbumNav()).not.toThrow();
  });
});

describe("switchAlbum", () => {
  test("noop when already viewing that album in album/library view", async () => {
    /** @type {any} */ (window).currentAlbumId = 5;
    /** @type {any} */ (window).currentView = "album";
    const fetchMock = vi.fn(async () => jsonResp({ photos: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    await switchAlbum(5);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("force option bypasses the noop", async () => {
    /** @type {any} */ (window).currentAlbumId = 5;
    /** @type {any} */ (window).currentView = "album";
    /** @type {any} */ (window).albumList = [{ id: 5, album_type: "manual", name: "X" }];
    const fetchMock = vi.fn(async () => jsonResp({ photos: [], total: 0 }));
    vi.stubGlobal("fetch", fetchMock);
    await switchAlbum(5, { force: true });
    expect(fetchMock).toHaveBeenCalled();
  });

  test("loads photos and populates state on first switch", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 7, album_type: "manual", name: "Trip", photo_count: 2 },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          photos: [
            { filepath: "/a", selected: true },
            { filepath: "/b", selected: false, override: "include" },
          ],
          total: 2,
          album: {},
        })
      )
    );
    await switchAlbum(7);
    expect(/** @type {any} */ (window).currentAlbumId).toBe(7);
    expect(/** @type {any} */ (window).currentView).toBe("album");
    expect(/** @type {any} */ (window).photos).toHaveLength(2);
    expect(/** @type {any} */ (window).selectedPaths.has("/a")).toBe(true);
    expect(/** @type {any} */ (window).overrides["/b"]).toBe("include");
  });

  test("All-album switch sets currentView to 'library' + populates favorites", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 1 },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          photos: [{ filepath: "/a", favorite: true }],
          total: 1,
          album: {},
        })
      )
    );
    await switchAlbum(1);
    expect(/** @type {any} */ (window).currentView).toBe("library");
    expect(/** @type {any} */ (window).favorites.has("/a")).toBe(true);
  });

  test("All-album switch syncs toolbar-k from albumData.k (actual last-used k)", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 10 },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          photos: [],
          total: 10,
          // albumData.k=35 (last recompute) vs config.default_selection_k=50
          album: { k: 35, config: { default_selection_k: 50 } },
        })
      )
    );
    await switchAlbum(1);
    expect(/** @type {HTMLInputElement} */ (document.getElementById("toolbar-k")).value).toBe("35");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("param-k")).value).toBe("35");
  });

  test("auto-recompute fires on album entry even when selectedPaths is non-empty", async () => {
    // Regression: previously recompute was gated on selectedPaths.size === 0,
    // so an album with any stale persisted picks (e.g. 1) would NOT refresh
    // its picks to match the current k (e.g. 5) — user saw "1 selected of 24"
    // when they expected "5 selected of 24" without manual intervention.
    /** @type {any} */ (window).albumList = [
      { id: 7, album_type: "manual", name: "Baby Shower", photo_count: 24 },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("param-k")).value = "5";
    const calls = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        calls.push(String(url));
        if (String(url).includes("/recompute")) {
          return jsonResp({ scores: {}, selected_paths: ["/a", "/b", "/c", "/d", "/e"] });
        }
        return jsonResp({
          // One persisted pick — old code would skip recompute.
          photos: [
            { filepath: "/a", selected: true },
            { filepath: "/b", selected: false },
            { filepath: "/c", selected: false },
          ],
          total: 3,
          album: { k: 5, config: { k_user_set: true } },
        });
      })
    );
    await switchAlbum(7);
    // Recompute must have been requested. The 150ms debounce is bypassed
    // here because doRecompute is called directly (not via scheduleRecompute).
    // The test waits via the awaited fetch call.
    await new Promise((r) => setTimeout(r, 50));
    const recomputeHit = calls.some((u) => u.includes("/recompute"));
    expect(recomputeHit, "switchAlbum must trigger /recompute on album entry").toBe(true);
  });

  test("All-album falls back to config.default_selection_k when albumData.k is absent", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 10 },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          photos: [],
          total: 10,
          album: { config: { default_selection_k: 75 } },
        })
      )
    );
    await switchAlbum(1);
    expect(/** @type {HTMLInputElement} */ (document.getElementById("toolbar-k")).value).toBe("75");
  });
});

describe("auto-k re-compute on album load", () => {
  function albumFetch(albumData, total) {
    return vi.fn(async () => jsonResp({ photos: [], total, album: albumData }));
  }

  test("re-computes k when album has grown (no config, stale k)", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 5, album_type: "smart_person", name: "Emma", photo_count: 1225 },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("param-k")).value = "50";
    vi.stubGlobal("fetch", albumFetch({ k: 11, config: null }, 1225));
    await switchAlbum(5);
    // 10% of 1225 = 122, capped at globalK=50
    expect(/** @type {HTMLInputElement} */ (document.getElementById("toolbar-k")).value).toBe("50");
  });

  test("preserves user-set k when config has k_user_set", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 5, album_type: "smart_person", name: "Emma", photo_count: 1225 },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("param-k")).value = "50";
    vi.stubGlobal("fetch", albumFetch({ k: 11, config: { k_user_set: true } }, 1225));
    await switchAlbum(5);
    // k_user_set → formula does NOT run → stays at 11
    expect(/** @type {HTMLInputElement} */ (document.getElementById("toolbar-k")).value).toBe("11");
  });

  test("auto-k is capped at globalK", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 5, album_type: "smart_person", name: "Emma", photo_count: 200 },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("param-k")).value = "15";
    vi.stubGlobal("fetch", albumFetch({ k: 50, config: null }, 200));
    await switchAlbum(5);
    // 10% of 200 = 20, but capped at globalK=15
    expect(/** @type {HTMLInputElement} */ (document.getElementById("toolbar-k")).value).toBe("15");
  });
});

describe("showNewAlbumInput", () => {
  test("noop when prompt is cancelled", async () => {
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const promise = showNewAlbumInput();
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(false);
    await promise;
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("deleteAlbumPrompt", () => {
  test("declined confirm = no DELETE", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = deleteAlbumPrompt(7, "Trip");
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(false);
    await promise;
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("accepted confirm = DELETE", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all" },
      { id: 7, album_type: "manual", name: "Trip" },
    ];
    /** @type {any} */ (window).currentAlbumId = 7;
    const fetchMock = vi.fn(async (url, opts) => {
      if (opts?.method === "DELETE") return jsonResp({});
      return jsonResp({ photos: [], total: 0, albums: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = deleteAlbumPrompt(7, "Trip");
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls.find((c) => c[1]?.method === "DELETE")).toBeDefined();
  });

  test("drops the saved per-album filter so it can't haunt a reused ID", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all" },
      { id: 7, album_type: "manual", name: "Trip" },
    ];
    /** @type {any} */ (window).currentAlbumId = 7;
    // Two entries: the album we're about to delete + an unrelated one
    // that must NOT be touched.
    localStorage.setItem(
      "bpp-album-filters",
      JSON.stringify({ "album:7": "selected", "album:9": "favorites" })
    );
    const fetchMock = vi.fn(async (url, opts) => {
      if (opts?.method === "DELETE") return jsonResp({});
      return jsonResp({ photos: [], total: 0, albums: [] });
    });
    vi.stubGlobal("fetch", fetchMock);
    const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
    const promise = deleteAlbumPrompt(7, "Trip");
    await Promise.resolve();
    await Promise.resolve();
    dialogs.resolveConfirm(true);
    await promise;
    const map = JSON.parse(localStorage.getItem("bpp-album-filters") || "{}");
    expect(map["album:7"]).toBeUndefined();
    expect(map["album:9"]).toBe("favorites");
  });
});

describe("toggleFaceSort", () => {
  test("flips between count and name", () => {
    /** @type {any} */ (window).sidebarFaceSort = "count";
    toggleFaceSort();
    expect(/** @type {any} */ (window).sidebarFaceSort).toBe("name");
    toggleFaceSort();
    expect(/** @type {any} */ (window).sidebarFaceSort).toBe("count");
  });
});

describe("showAlbumMoveMenu", () => {
  test("noop on smart album", () => {
    /** @type {any} */ (window).albumList = [{ id: 9, album_type: "smart_score", name: "X" }];
    showAlbumMoveMenu(
      /** @type {any} */ ({ preventDefault() {}, stopPropagation() {}, clientX: 0, clientY: 0 }),
      9
    );
    expect(document.getElementById("album-move-menu")).toBeNull();
  });

  test("renders move-menu for manual album", () => {
    /** @type {any} */ (window).albumList = [
      { id: 7, album_type: "manual", name: "Trip" },
      { id: 8, album_type: "manual", name: "Family" },
    ];
    showAlbumMoveMenu(
      /** @type {any} */ ({ preventDefault() {}, stopPropagation() {}, clientX: 50, clientY: 50 }),
      7
    );
    expect(document.getElementById("album-move-menu")).toBeTruthy();
    expect(document.getElementById("album-move-menu")?.textContent).toContain("Family");
  });
});

describe("moveAlbumTo", () => {
  test("PUTs parent_id and toasts on success", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 7, album_type: "manual", name: "Trip" },
      { id: 8, album_type: "manual", name: "Family" },
    ];
    const fetchMock = vi.fn(async () => jsonResp({ albums: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await moveAlbumTo(7, 8);
    expect(fetchMock).toHaveBeenCalled();
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const putCall = calls.find((c) => c[1]?.method === "PUT");
    expect(putCall).toBeDefined();
    expect(putCall && JSON.parse(putCall[1].body).parent_id).toBe(8);
  });
});

// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  albumFilterInput,
  cycleAlbumsSort,
  deleteAlbumPrompt,
  loadAlbumList,
  loadYearMonths,
  moveAlbumTo,
  onFilterChange,
  removeSmartAlbum,
  removeTagAlbum,
  renameSmartAlbum,
  renderAlbumNav,
  showAlbumMoveMenu,
  showNewAlbumInput,
  showSmartAlbumMenu,
  showTagAlbumMenu,
  switchAlbum,
  switchToMonth,
  toggleAlbumsCollapsed,
  toggleFaceSort,
} from "../bpp/web/static/js/modules/albums.mjs";

// Bridged to window so the tests can dispatch via the same path the
// data-onchange="onFilterChange" attribute uses at runtime.
beforeEach(() => {
  /** @type {any} */ (window).onFilterChange = onFilterChange;
});

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay"><div class="confirm-dialog"></div></div>
    <aside class="sidebar">
      <div id="album-list"></div>
    </aside>
    <main>
      <div id="photo-grid"></div>
      <div id="people-view"></div>
      <div id="pets-view"></div>
      <div id="groups-view"></div>
      <div id="map-view"></div>
      <div id="calendar-view"></div>
      <div id="toolbar"></div>
      <div id="toolbar-title"></div>
      <div id="toolbar-subtitle"></div>
      <input id="filter-by" />
      <input id="sort-by" />
      <input id="toolbar-k" type="number" value="50" />
      <input id="param-k" type="number" value="50" />
    </main>
  `;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).petClusters = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).currentViewId = null;
  /** @type {any} */ (window).petsAvailable = false;
  /** @type {any} */ (window).sidebarFaceSort = "count";
  /** @type {any} */ (window).FACE_MIN_PHOTOS = 4;
  /** @type {any} */ (window).ICONS = {
    library: "<i>l</i>",
    people: "<i>p</i>",
    folder: "<i>f</i>",
    paw: "<i>pw</i>",
    group: "<i>g</i>",
    map: "<i>m</i>",
    calendar: "<i>c</i>",
    picks: "<i>pi</i>",
    heart: "<i>h</i>",
    hidden: "<i>hd</i>",
    trash: "<i>t</i>",
    star: "<i>s</i>",
    clock: "<i>cl</i>",
    inbox: "<i>in</i>",
    video: "<i>v</i>",
    screenshot: "<i>sc</i>",
    duplicate: "<i>du</i>",
    noFace: "<i>nf</i>",
    document: "<i>do</i>",
    pencil: "<i>pe</i>",
    tag: "<i>tg</i>",
  };
  /** @type {any} */ (window).isClusterExcluded = vi.fn(() => false);
  /** @type {any} */ (window).personDisplayName = vi.fn(() => null);
  /** @type {any} */ (window).updatePersonAlbumBar = vi.fn();
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
});

afterEach(() => {
  document.body.innerHTML = "";
  document.getElementById("album-move-menu")?.remove();
  document.getElementById("smart-album-ctx")?.remove();
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "faceClusters",
    "petClusters",
    "selectedPaths",
    "favorites",
    "overrides",
    "photos",
    "currentAlbumId",
    "currentView",
    "currentViewId",
    "petsAvailable",
    "sidebarFaceSort",
    "FACE_MIN_PHOTOS",
    "ICONS",
    "isClusterExcluded",
    "personDisplayName",
    "updatePersonAlbumBar",
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

// ── Sidebar active-state + folder-open persistence ───────────────────
//
// Regression tests for the "selected album highlight is lost on refresh"
// and "manually-expanded folder collapses on every nav" bugs. These pin:
//
//   - renderAlbumNav must apply `.active` to nav items matching
//     currentAlbumId when currentView === "album".
//   - The parent folder of the active album must render `<details open>`
//     (auto-expand around the user's current location).
//   - A user-toggled folder open-state persists via localStorage and
//     survives re-renders that don't touch currentAlbumId.
//   - The Albums-section <details> renders with the data-nav-key so
//     the global toggle listener can persist its state — without this
//     the user's "I want Albums collapsed" preference snaps back on
//     every render because the localStorage value was the only source
//     of truth and the native toggle didn't update it.

describe("renderAlbumNav — active state + open-folder persistence", () => {
  beforeEach(() => {
    // Reset the per-key nav-open map so tests start clean. Other
    // beforeEach resets DOM but not localStorage.
    localStorage.removeItem("bpp-nav-open-keys");
    localStorage.removeItem("bpp-albums-collapsed");
  });

  test("active album receives .active class on the nav-item", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100 },
      { id: 7, album_type: "manual", name: "Hawaii", photo_count: 38 },
    ];
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).currentView = "album";
    renderAlbumNav();
    const item = document.querySelector('[data-album-id="7"]');
    expect(item, "Hawaii album item must exist in rendered sidebar").toBeTruthy();
    expect(
      item?.classList.contains("active"),
      "active album must have .active class so the highlight survives a refresh"
    ).toBe(true);
  });

  test("user-clicking a folder summary writes the open state to localStorage", () => {
    // This is the load-bearing one: my unit tests above set localStorage
    // directly and verified renderAlbumNav reads it. But on real refresh
    // the FIRST step is "user clicks summary → toggle event fires → my
    // listener must write to localStorage". If the listener isn't being
    // installed or the toggle event doesn't reach it, the data flow is
    // broken end-to-end even though both halves work in isolation.
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    renderAlbumNav();
    const details = /** @type {HTMLDetailsElement} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(details, "Albums section must render").toBeTruthy();
    expect(details.dataset.navKey).toBe("section:albums");

    // Simulate: user closes the section. Native <details> updates `open`
    // first, then fires `toggle`. We mimic the same order so the
    // listener sees `details.open === false` when the event arrives.
    details.open = false;
    details.dispatchEvent(new Event("toggle"));

    const map = JSON.parse(localStorage.getItem("bpp-nav-open-keys") || "{}");
    expect(
      map["section:albums"],
      "after a user-driven toggle, localStorage must reflect the new state. " +
        "If this fails, the persistence listener isn't catching the native " +
        "<details> toggle event (likely a bubbles/capture issue) — meaning " +
        "every user interaction is lost on refresh."
    ).toBe(false);

    // Reopen.
    details.open = true;
    details.dispatchEvent(new Event("toggle"));
    const map2 = JSON.parse(localStorage.getItem("bpp-nav-open-keys") || "{}");
    expect(map2["section:albums"]).toBe(true);
  });

  test("simulated boot flow: switchAlbum after initial render lands active", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100 },
      { id: 7, album_type: "manual", name: "Hawaii", photo_count: 38 },
    ];
    // Step 1: app.mjs sets currentAlbumId to all and does initial render.
    /** @type {any} */ (window).currentAlbumId = 1;
    /** @type {any} */ (window).currentView = "library";
    renderAlbumNav();

    // Step 2: saved nav restore — switchAlbum is called for the previously
    // active album. This is the boot path the user sees on Cmd+R.
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], has_more: false, selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await switchAlbum(7);

    // After switchAlbum, the sidebar must show Hawaii (id=7) highlighted.
    const item = document.querySelector('[data-album-id="7"]');
    expect(item, "Hawaii item must exist after switchAlbum").toBeTruthy();
    expect(
      item?.classList.contains("active"),
      "boot-time switchAlbum must produce .active on the item — this is the refresh case the user reported"
    ).toBe(true);
    // And Library (id=1) must NOT be marked active anymore.
    const lib = document.querySelector('[data-album-id="1"]');
    expect(
      lib?.classList.contains("active"),
      "Library item must lose .active when we switch away"
    ).toBe(false);
  });

  test("active album inside a Tag folder forces that folder open", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      {
        id: 42,
        album_type: "smart_tag",
        name: "Sunset",
        photo_count: 5,
        rule: { tag_id: 99 },
      },
    ];
    /** @type {any} */ (window).currentAlbumId = 42;
    /** @type {any} */ (window).currentView = "album";
    renderAlbumNav();
    // The Tags <details> wraps the smart_tag entries — must render open
    // because the active album lives inside it.
    const tagDetails = document.querySelector('[data-nav-key="section:tags"]');
    expect(tagDetails, "Tags section must render with data-nav-key").toBeTruthy();
    expect(
      /** @type {HTMLDetailsElement | null} */ (tagDetails)?.open,
      "parent folder of active album must auto-open"
    ).toBe(true);
  });

  test("user-toggled folder open state survives re-render without active child", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      {
        id: 42,
        album_type: "smart_tag",
        name: "Sunset",
        photo_count: 5,
        rule: { tag_id: 99 },
      },
    ];
    // No active album inside Tags. User expanded it anyway.
    localStorage.setItem("bpp-nav-open-keys", JSON.stringify({ "section:tags": true }));
    /** @type {any} */ (window).currentAlbumId = 1;
    /** @type {any} */ (window).currentView = "library";
    renderAlbumNav();
    const tagDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector('[data-nav-key="section:tags"]')
    );
    expect(
      tagDetails?.open,
      "user-expanded folder must NOT collapse just because the active album isn't inside it"
    ).toBe(true);
  });

  test("Albums section <details> renders with data-nav-key for unified persistence", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    renderAlbumNav();
    const albumsDetails = document.querySelector(
      'details.nav-albums-section[data-nav-key="section:albums"]'
    );
    expect(
      albumsDetails,
      "Albums section must declare data-nav-key so the unified toggle listener captures clicks"
    ).toBeTruthy();
  });

  test("Albums section honors user-stored open=true via the unified key", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    localStorage.setItem("bpp-nav-open-keys", JSON.stringify({ "section:albums": true }));
    renderAlbumNav();
    const albumsDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(albumsDetails?.open).toBe(true);
  });

  test("Albums section honors user-stored open=false via the unified key", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    localStorage.setItem("bpp-nav-open-keys", JSON.stringify({ "section:albums": false }));
    renderAlbumNav();
    const albumsDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(albumsDetails?.open).toBe(false);
  });

  test("per-album filter persists across switchAlbum round-trip", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 100 },
      { id: 7, album_type: "manual", name: "Birthday Party", photo_count: 25 },
      { id: 11, album_type: "manual", name: "Hawaii Trip", photo_count: 38 },
    ];
    const filterEl = /** @type {HTMLSelectElement} */ (document.getElementById("filter-by"));
    // Make the select behave like a real filter dropdown with the values
    // photos.mjs's onFilterChange writes.
    filterEl.innerHTML =
      '<option value="all">All</option>' +
      '<option value="selected">Selected only</option>' +
      '<option value="favorites">Favorites</option>';

    /** @type {any} */ (window).renderGrid = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );

    // Step 1: switch to Birthday Party.
    await switchAlbum(7);
    // Step 2: user picks "Selected only" filter while on Birthday Party.
    filterEl.value = "selected";
    /** @type {any} */ (window).onFilterChange();

    // Step 3: switch to Hawaii Trip. Its scope has no saved filter →
    // must default back to "all" (not inherit Birthday Party's).
    await switchAlbum(11);
    expect(
      filterEl.value,
      "Hawaii Trip must default to 'all' — filter is per-album, " +
        "switching away from Birthday Party must NOT carry its filter across"
    ).toBe("all");

    // Step 4: switch back to Birthday Party. Its saved "selected" filter
    // must come back — that's the load-bearing requirement.
    await switchAlbum(7);
    expect(
      filterEl.value,
      "Birthday Party must restore its previously-saved 'selected' filter"
    ).toBe("selected");
  });

  test("per-album filter survives a simulated refresh (boot → switchAlbum)", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Birthday Party", photo_count: 25 },
    ];
    const filterEl = /** @type {HTMLSelectElement} */ (document.getElementById("filter-by"));
    filterEl.innerHTML =
      '<option value="all">All</option>' + '<option value="selected">Selected only</option>';

    // Seed localStorage as if a previous session saved the filter.
    localStorage.setItem("bpp-album-filters", JSON.stringify({ "album:7": "selected" }));

    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );

    // Simulate boot: switchAlbum is called via savedNav restore.
    await switchAlbum(7);

    expect(
      filterEl.value,
      "after refresh boot, switchAlbum must restore the previously-saved filter from localStorage"
    ).toBe("selected");
  });

  test("filter==='all' is stored as ABSENCE so the map doesn't bloat", async () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    const filterEl = /** @type {HTMLSelectElement} */ (document.getElementById("filter-by"));
    filterEl.innerHTML =
      '<option value="all">All</option>' + '<option value="selected">Selected only</option>';

    /** @type {any} */ (window).renderGrid = vi.fn();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [], selected_paths: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );

    await switchAlbum(7);
    // Apply a non-default filter, then revert to "all" — the entry should
    // be deleted from the map rather than stored as "all" verbatim. This
    // keeps the map size proportional to "things the user customized."
    filterEl.value = "selected";
    /** @type {any} */ (window).onFilterChange();
    let map = JSON.parse(localStorage.getItem("bpp-album-filters") || "{}");
    expect(map["album:7"]).toBe("selected");

    filterEl.value = "all";
    /** @type {any} */ (window).onFilterChange();
    map = JSON.parse(localStorage.getItem("bpp-album-filters") || "{}");
    expect(
      Object.prototype.hasOwnProperty.call(map, "album:7"),
      "reverting to default 'all' must clear the entry, not store 'all' explicitly"
    ).toBe(false);
  });

  test("legacy LS_COLLAPSED still honored when unified key is absent (migration path)", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all", name: "Library", photo_count: 0 },
      { id: 7, album_type: "manual", name: "Trip", photo_count: 5 },
    ];
    // Legacy users have only the old key. Should be respected until the
    // first toggle migrates them to the unified scheme.
    localStorage.setItem("bpp-albums-collapsed", "1");
    renderAlbumNav();
    const albumsDetails = /** @type {HTMLDetailsElement | null} */ (
      document.querySelector("details.nav-albums-section")
    );
    expect(albumsDetails?.open, "legacy collapse preference must survive migration").toBe(false);
  });
});

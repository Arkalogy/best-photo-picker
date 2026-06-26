// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _resetToolbarState,
  closePopovers,
  initToolbarIcons,
  saveFavorites,
  saveOverrides,
  setFilterFromPopover,
  setSortFromPopover,
  syncKFromToolbar,
  syncToolbarK,
  toggleFavorite,
  toggleShowPicks,
  toggleToolbarPopover,
  updateLibStats,
  updatePickScope,
  updateShowPicksChip,
  updateToolbarForView,
} from "../bpp/web/static/js/modules/toolbar.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div class="toolbar-right">
      <div class="toolbar-popover-anchor" id="sort-anchor">
        <button id="btn-sort"></button>
        <div id="sort-popover" class="toolbar-popover">
          <div class="popover-option" data-sort="date-asc"></div>
          <div class="popover-option" data-sort="date-desc"></div>
        </div>
      </div>
      <div class="toolbar-popover-anchor" id="filter-anchor">
        <button id="btn-filter"></button>
        <div id="filter-popover" class="toolbar-popover">
          <div class="popover-option" data-filter="all"></div>
          <div class="popover-option" data-filter="selected"></div>
        </div>
      </div>
      <input id="sort-by" value="date-desc" />
      <input id="filter-by" value="all" />
      <input id="param-k" type="number" value="50" />
      <input id="toolbar-k" type="number" value="50" />
      <button id="btn-search-toolbar"></button>
      <button id="btn-import-toolbar"></button>
      <button id="btn-analyze-toolbar"></button>
      <button id="btn-export"></button>
      <button id="btn-slideshow"></button>
      <button id="btn-settings-toolbar"></button>
      <button id="ss-play-btn"></button>
      <button id="ss-shuffle-btn"></button>
      <button id="ss-kb-btn"></button>
      <button id="ss-info-btn"></button>
      <div id="zoom-control"></div>
      <div id="toolbar-pick"><span id="toolbar-pick-scope"></span></div>
      <button id="toolbar-show-picks"></button>
      <div id="lib-stats"></div>
    </div>
  `;
  const localStorageStore = new Map();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key) => (localStorageStore.has(key) ? localStorageStore.get(key) : null)),
    setItem: vi.fn((key, value) => localStorageStore.set(key, String(value))),
    removeItem: vi.fn((key) => localStorageStore.delete(key)),
    clear: vi.fn(() => localStorageStore.clear()),
  });
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).ICONS = {
    search: "<svg id='ic-search'/>",
    sort: "<svg id='ic-sort'/>",
    filter: "<svg id='ic-filter'/>",
    importArrow: "<svg id='ic-import'/>",
    analyze: "<svg id='ic-analyze'/>",
    exportArrow: "<svg id='ic-export'/>",
    ssSlideshow: "<svg id='ic-slideshow'/>",
    more: "<svg id='ic-more'/>",
    ssPause: "<svg id='ic-pause'/>",
    ssShuffle: "<svg id='ic-shuffle'/>",
    ssKenBurns: "<svg id='ic-kb'/>",
    ssInfo: "<svg id='ic-info'/>",
  };
  /** @type {any} */ (window).renderGrid = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {any} */ (window).updateCardInPlace = vi.fn(() => false);
  /** @type {any} */ (window).scheduleRecompute = vi.fn();
  /** @type {any} */ (window).updateOverrideStats = vi.fn();
  /** @type {any} */ (window).showToast = vi.fn();
  _resetToolbarState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "photos",
    "currentAlbumId",
    "currentView",
    "favorites",
    "selectedPaths",
    "ICONS",
    "renderGrid",
    "renderAlbumNav",
    "updateCardInPlace",
    "scheduleRecompute",
    "updateOverrideStats",
    "showToast",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetToolbarState();
});

describe("toggleToolbarPopover", () => {
  test("opens the matching popover and marks active option", () => {
    toggleToolbarPopover("sort");
    const popover = /** @type {HTMLElement} */ (document.getElementById("sort-popover"));
    expect(popover.classList.contains("open")).toBe(true);
    const active = /** @type {HTMLElement} */ (popover.querySelector(".popover-option.active"));
    expect(active.dataset.sort).toBe("date-desc");
  });

  test("re-toggling closes it", () => {
    toggleToolbarPopover("sort");
    toggleToolbarPopover("sort");
    expect(document.getElementById("sort-popover")?.classList.contains("open")).toBe(false);
  });

  test("opening filter closes sort", () => {
    toggleToolbarPopover("sort");
    toggleToolbarPopover("filter");
    expect(document.getElementById("sort-popover")?.classList.contains("open")).toBe(false);
    expect(document.getElementById("filter-popover")?.classList.contains("open")).toBe(true);
  });

  test("noop if either button or popover missing", () => {
    document.getElementById("btn-sort")?.remove();
    expect(() => toggleToolbarPopover("sort")).not.toThrow();
  });
});

describe("setSortFromPopover / setFilterFromPopover", () => {
  test("setSortFromPopover updates the value, calls renderGrid, closes popover", () => {
    toggleToolbarPopover("sort");
    setSortFromPopover("date-asc");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("sort-by")).value).toBe(
      "date-asc"
    );
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
    expect(document.getElementById("sort-popover")?.classList.contains("open")).toBe(false);
  });

  test("setFilterFromPopover toggles has-active-filter on btn-filter", () => {
    setFilterFromPopover("selected");
    expect(document.getElementById("btn-filter")?.classList.contains("has-active-filter")).toBe(
      true
    );
    setFilterFromPopover("all");
    expect(document.getElementById("btn-filter")?.classList.contains("has-active-filter")).toBe(
      false
    );
  });

  test("setFilterFromPopover persists the current scope filter", () => {
    if (typeof localStorage.removeItem === "function") localStorage.removeItem("bpp-album-filters");
    setFilterFromPopover("selected");
    expect(JSON.parse(localStorage.getItem("bpp-album-filters") || "{}")["view:library"]).toBe(
      "selected"
    );
  });
});

describe("closePopovers", () => {
  test("removes .open from all toolbar-popover", () => {
    document.getElementById("sort-popover")?.classList.add("open");
    document.getElementById("filter-popover")?.classList.add("open");
    closePopovers();
    expect(document.querySelectorAll(".toolbar-popover.open")).toHaveLength(0);
  });
});

describe("initToolbarIcons", () => {
  test("populates innerHTML on each known toolbar button", () => {
    initToolbarIcons();
    expect(document.getElementById("btn-search-toolbar")?.innerHTML).toContain("ic-search");
    expect(document.getElementById("btn-export")?.innerHTML).toContain("ic-export");
    expect(document.getElementById("ss-play-btn")?.innerHTML).toContain("ic-pause");
  });
});

describe("syncKFromToolbar / syncToolbarK", () => {
  test("syncKFromToolbar writes to settings input and recomputes", () => {
    syncKFromToolbar(75);
    expect(/** @type {HTMLInputElement} */ (document.getElementById("param-k")).value).toBe("75");
    expect(/** @type {any} */ (window).scheduleRecompute).toHaveBeenCalled();
  });

  test("syncKFromToolbar ignores invalid values", () => {
    syncKFromToolbar("not-a-number");
    expect(/** @type {any} */ (window).scheduleRecompute).not.toHaveBeenCalled();
  });

  test("syncKFromToolbar PUTs k + k_user_set flag to album endpoint", async () => {
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).albumList = [{ id: 7, album_type: "manual", k: 50 }];
    const fetchMock = vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    syncKFromToolbar(99);
    expect(fetchMock).toHaveBeenCalled();
    const call = /** @type {any[]} */ (fetchMock.mock.calls[0]);
    expect(String(call[0])).toContain("/api/v1/albums/7");
    expect(call[1].method).toBe("PUT");
    const body = JSON.parse(call[1].body);
    expect(body.k).toBe(99);
    expect(body.config.k_user_set).toBe(true);
  });

  test("syncKFromToolbar sets k_user_set on in-memory album config", () => {
    const album = /** @type {any} */ ({ id: 7, album_type: "manual", k: 50, config: null });
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).albumList = [album];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("{}", { status: 200, headers: { "content-type": "application/json" } })
      )
    );
    syncKFromToolbar(30);
    expect(album.config.k_user_set).toBe(true);
  });

  test("syncToolbarK copies param-k → toolbar-k", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("param-k")).value = "30";
    syncToolbarK();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("toolbar-k")).value).toBe("30");
  });
});

describe("updateToolbarForView", () => {
  test("hides grid controls in non-grid views", () => {
    /** @type {any} */ (window).currentView = "people";
    updateToolbarForView();
    expect(/** @type {HTMLElement} */ (document.getElementById("zoom-control")).style.display).toBe(
      "none"
    );
    expect(/** @type {HTMLElement} */ (document.getElementById("btn-export")).style.display).toBe(
      "none"
    );
    expect(
      /** @type {HTMLElement} */ (document.getElementById("btn-slideshow")).style.display
    ).toBe("none");
  });

  test("shows grid controls in library view", () => {
    /** @type {any} */ (window).currentView = "library";
    updateToolbarForView();
    expect(/** @type {HTMLElement} */ (document.getElementById("zoom-control")).style.display).toBe(
      "flex"
    );
    expect(/** @type {HTMLElement} */ (document.getElementById("btn-export")).style.display).toBe(
      "flex"
    );
  });

  test("creates Review Duplicates button on first call", () => {
    /** @type {any} */ (window).currentView = "library";
    updateToolbarForView();
    expect(document.getElementById("btn-review-dupes")).toBeTruthy();
  });

  test("Review Duplicates is visible only inside Duplicates album", () => {
    /** @type {any} */ (window).currentView = "album";
    /** @type {any} */ (window).currentAlbumId = 5;
    /** @type {any} */ (window).albumList = [
      { id: 5, album_type: "smart_duplicates", name: "Dupes" },
    ];
    updateToolbarForView();
    const dupeBtn = /** @type {HTMLElement} */ (document.getElementById("btn-review-dupes"));
    expect(dupeBtn.style.display).toBe("inline-flex");
  });

  test("pick control is visible in picks view", () => {
    /** @type {any} */ (window).currentView = "picks";
    updateToolbarForView();
    expect(/** @type {HTMLElement} */ (document.getElementById("toolbar-pick")).style.display).toBe(
      "flex"
    );
  });

  test("pick control is visible in library (all-album) view", () => {
    /** @type {any} */ (window).currentView = "library";
    /** @type {any} */ (window).currentAlbumId = 1;
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all", name: "All Photos" }];
    updateToolbarForView();
    expect(/** @type {HTMLElement} */ (document.getElementById("toolbar-pick")).style.display).toBe(
      "flex"
    );
  });

  test("pick control is visible inside a manual album", () => {
    /** @type {any} */ (window).currentView = "album";
    /** @type {any} */ (window).currentAlbumId = 3;
    /** @type {any} */ (window).albumList = [{ id: 3, album_type: "manual", name: "Trip" }];
    updateToolbarForView();
    expect(/** @type {HTMLElement} */ (document.getElementById("toolbar-pick")).style.display).toBe(
      "flex"
    );
  });

  test("pick control is hidden in non-grid views", () => {
    /** @type {any} */ (window).currentView = "people";
    updateToolbarForView();
    expect(/** @type {HTMLElement} */ (document.getElementById("toolbar-pick")).style.display).toBe(
      "none"
    );
  });
});

describe("updatePickScope", () => {
  test("updates scope text inside album", () => {
    updatePickScope({ name: "Vacation 2024", album_type: "manual" });
    expect(document.getElementById("toolbar-pick-scope")?.textContent).toContain("from");
  });

  test("clears scope when album is null or 'all'", () => {
    updatePickScope({ name: "All", album_type: "all" });
    expect(document.getElementById("toolbar-pick-scope")?.textContent).toBe("");
    updatePickScope(null);
    expect(document.getElementById("toolbar-pick-scope")?.textContent).toBe("");
  });

  test("truncates long album names with ellipsis", () => {
    updatePickScope({ name: "A".repeat(50), album_type: "manual" });
    expect(document.getElementById("toolbar-pick-scope")?.textContent).toContain("…");
  });
});

describe("toggleShowPicks / updateShowPicksChip", () => {
  test("toggleShowPicks flips filter-by between all and selected", () => {
    toggleShowPicks();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value).toBe(
      "selected"
    );
    toggleShowPicks();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value).toBe(
      "all"
    );
  });

  test("updateShowPicksChip shows count when active", () => {
    /** @type {any} */ (window).selectedPaths = new Set(["/a", "/b"]);
    // Photos must contain the picked paths for the in-scope count to match.
    /** @type {any} */ (window).photos = [
      { filepath: "/a" },
      { filepath: "/b" },
      { filepath: "/c" },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    updateShowPicksChip();
    expect(document.getElementById("toolbar-show-picks")?.textContent).toContain("(2)");
  });

  test("updateShowPicksChip shows All Photos when active", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "selected";
    updateShowPicksChip();
    expect(document.getElementById("toolbar-show-picks")?.textContent).toContain("All Photos");
  });

  test("updateShowPicksChip counts album-scoped picks, not library-wide", () => {
    // Library-wide picks: 50 paths. Current view (album) has only 3 photos,
    // 2 of which are in the picks set. Pill must show 2, not 50.
    const libraryPicks = new Set();
    for (let i = 0; i < 50; i++) libraryPicks.add("/lib/" + i + ".jpg");
    libraryPicks.add("/album/picked1.jpg");
    libraryPicks.add("/album/picked2.jpg");
    /** @type {any} */ (window).selectedPaths = libraryPicks;
    /** @type {any} */ (window).photos = [
      { filepath: "/album/picked1.jpg" },
      { filepath: "/album/picked2.jpg" },
      { filepath: "/album/unpicked.jpg" },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    updateShowPicksChip();
    const text = document.getElementById("toolbar-show-picks")?.textContent || "";
    expect(text).toContain("(2)");
    expect(text).not.toContain("(50)");
    expect(text).not.toContain("(52)");
  });

  test("updateShowPicksChip ignores deleted photos in scope count", () => {
    /** @type {any} */ (window).selectedPaths = new Set(["/a", "/b", "/c"]);
    /** @type {any} */ (window).photos = [
      { filepath: "/a" },
      { filepath: "/b", deleted_at: "2024-01-01" },
      { filepath: "/c" },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    updateShowPicksChip();
    expect(document.getElementById("toolbar-show-picks")?.textContent).toContain("(2)");
  });

  test("updateShowPicksChip omits count parenthetical when no picks in scope", () => {
    /** @type {any} */ (window).selectedPaths = new Set(["/elsewhere"]);
    /** @type {any} */ (window).photos = [{ filepath: "/album/only.jpg" }];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    updateShowPicksChip();
    const text = document.getElementById("toolbar-show-picks")?.textContent || "";
    expect(text).toBe("BPP Picks");
  });
});

describe("toggleFavorite", () => {
  test("adds filepath to favorites and POSTs", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response("{}", {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    await toggleFavorite("/a.jpg");
    expect(/** @type {Set<string>} */ (/** @type {any} */ (window).favorites).has("/a.jpg")).toBe(
      true
    );
    expect(fetchMock).toHaveBeenCalled();
    expect(/** @type {any} */ (window).showToast).toHaveBeenCalledWith(
      "Favorited",
      4000,
      expect.any(Function)
    );
  });

  test("removes filepath when already favorited", async () => {
    /** @type {any} */ (window).favorites = new Set(["/a.jpg"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("{}", {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await toggleFavorite("/a.jpg");
    expect(/** @type {Set<string>} */ (/** @type {any} */ (window).favorites).has("/a.jpg")).toBe(
      false
    );
    expect(/** @type {any} */ (window).showToast).toHaveBeenCalledWith(
      "Unfavorited",
      4000,
      expect.any(Function)
    );
  });

  test("reverts the optimistic add and shows no success toast when the save fails", async () => {
    /** @type {any} */ (window).favorites = new Set();
    /** @type {any} */ (window).showToast.mockClear?.();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "nope" }), {
            status: 500,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await toggleFavorite("/a.jpg");
    // Optimistic add rolled back — server never persisted it.
    expect(/** @type {Set<string>} */ (/** @type {any} */ (window).favorites).has("/a.jpg")).toBe(
      false
    );
    // No "Favorited" success toast on a failed save.
    expect(/** @type {any} */ (window).showToast).not.toHaveBeenCalled();
  });
});

describe("saveOverrides / saveFavorites", () => {
  test("saveOverrides triggers updateOverrideStats", () => {
    saveOverrides();
    expect(/** @type {any} */ (window).updateOverrideStats).toHaveBeenCalled();
  });

  test("saveFavorites is a no-op", () => {
    expect(() => saveFavorites()).not.toThrow();
  });
});

describe("updateLibStats", () => {
  test("renders count from albumList 'all' album when present", async () => {
    /** @type {any} */ (window).albumList = [{ album_type: "all", photo_count: 1234 }];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ total_count: 0 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    updateLibStats();
    // Synchronous count populated immediately
    expect(document.getElementById("lib-stats")?.textContent).toContain("1,234");
  });

  test("clears text when count is 0", () => {
    /** @type {any} */ (window).albumList = [];
    /** @type {any} */ (window).photos = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ total_count: 0 }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    updateLibStats();
    expect(document.getElementById("lib-stats")?.textContent).toBe("");
  });
});

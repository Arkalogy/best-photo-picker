// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getSearchActiveIdx,
  _getSearchResultCount,
  _resetSearchState,
  doSearch,
  executeSearchResult,
  hideSearch,
  isSearchOpen,
  renderSearchResults,
  scrollToPhotoAndOpen,
  showSearch,
} from "../bpp/web/static/js/modules/search.mjs";

beforeEach(() => {
  // jsdom has no scrollIntoView — stub before any DOM lookup runs.
  Element.prototype.scrollIntoView = function () {};
  document.body.innerHTML = `
    <div id="search-overlay">
      <input id="search-input" />
      <div id="search-results"></div>
    </div>
  `;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).ICONS = {
    settings: "<i>cog</i>",
    importArrow: "<i>imp</i>",
    exportArrow: "<i>exp</i>",
    search: "<i>se</i>",
    folder: "<i>fo</i>",
    analyze: "<i>an</i>",
    people: "<i>pe</i>",
    calendar: "<i>ca</i>",
    tag: "<i>ta</i>",
    library: "<i>li</i>",
  };
  /** @type {any} */ (window).switchAlbum = vi.fn();
  /** @type {any} */ (window).openLightbox = vi.fn();
  /** @type {any} */ (window).applyTagFilter = vi.fn();
  /** @type {any} */ (window).showSettings = vi.fn();
  /** @type {any} */ (window).showImportModal = vi.fn();
  /** @type {any} */ (window).showExportModal = vi.fn();
  /** @type {any} */ (window).showLibraryPicker = vi.fn();
  /** @type {any} */ (window).startReanalyze = vi.fn();
  _resetSearchState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "currentGridItems",
    "ICONS",
    "switchAlbum",
    "openLightbox",
    "applyTagFilter",
    "showSettings",
    "showImportModal",
    "showExportModal",
    "showLibraryPicker",
    "startReanalyze",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetSearchState();
});

describe("showSearch / hideSearch / isSearchOpen", () => {
  test("showSearch toggles visible class on overlay", () => {
    expect(isSearchOpen()).toBe(false);
    showSearch();
    expect(isSearchOpen()).toBe(true);
  });

  test("hideSearch removes visible class", () => {
    showSearch();
    hideSearch();
    expect(isSearchOpen()).toBe(false);
  });

  test("hideSearch clears the input and results", () => {
    showSearch();
    /** @type {HTMLInputElement} */ (document.getElementById("search-input")).value = "x";
    /** @type {HTMLElement} */ (document.getElementById("search-results")).innerHTML = "old";
    hideSearch();
    expect(/** @type {HTMLInputElement} */ (document.getElementById("search-input")).value).toBe(
      ""
    );
    expect(document.getElementById("search-results")?.innerHTML).toBe("");
  });
});

describe("doSearch", () => {
  test("renders error category on fetch failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await doSearch("anything");
    expect(document.getElementById("search-results")?.innerHTML).toContain("Error searching");
  });

  test("renders quick-action matches even when API returns empty", async () => {
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
    await doSearch("settings");
    const html = document.getElementById("search-results")?.innerHTML || "";
    expect(html).toContain("Actions");
    expect(html).toContain("Settings");
  });
});

describe("renderSearchResults", () => {
  test("renders all top-level categories present in the response", () => {
    renderSearchResults({
      _quickActionsMatch: [{ label: "Settings", icon: "gear", action: () => {} }],
      people: [{ name: "Alice", photo_count: 5 }],
      albums: [{ name: "Trip", photo_count: 12 }],
      dates: [{ label: "2024", year: 2024 }],
      tags: [{ name: "beach", id: 1, photo_count: 3 }],
      semantic: [{ filepath: "/x.jpg", similarity: 0.7, thumb_hash: "h" }],
      photos: [{ filepath: "/y.jpg", aggregate_score: 0.8, thumb_hash: "h2" }],
    });
    const html = document.getElementById("search-results")?.innerHTML || "";
    expect(html).toContain("Actions");
    expect(html).toContain("People");
    expect(html).toContain("Albums");
    expect(html).toContain("Dates");
    expect(html).toContain("Tags");
    expect(html).toContain("Visual Match");
    expect(html).toContain("Photos");
  });

  test("renders 'No results' with hint when CLIP not ready", () => {
    renderSearchResults({
      clip_status: { ready: false, models_available: false },
    });
    const html = document.getElementById("search-results")?.innerHTML || "";
    expect(html).toContain("No results");
    expect(html).toContain("Visual search");
  });

  test("populates internal items list (queryable count)", () => {
    renderSearchResults({
      people: [
        { name: "A", photo_count: 1 },
        { name: "B", photo_count: 2 },
      ],
    });
    expect(_getSearchResultCount()).toBe(2);
  });

  test("auto-selects first item when results exist", () => {
    renderSearchResults({
      people: [{ name: "A", photo_count: 1 }],
    });
    expect(_getSearchActiveIdx()).toBe(0);
  });
});

describe("executeSearchResult", () => {
  test("action item invokes its callback", () => {
    const cb = vi.fn();
    renderSearchResults({
      _quickActionsMatch: [{ label: "X", icon: "gear", action: cb }],
    });
    executeSearchResult(0);
    expect(cb).toHaveBeenCalled();
  });

  test("person item switches album", () => {
    renderSearchResults({
      people: [{ name: "A", photo_count: 1, album_id: 42 }],
    });
    executeSearchResult(0);
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(42);
  });

  test("tag item switches to library and applies tag filter", () => {
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    renderSearchResults({
      tags: [{ id: 7, name: "beach", photo_count: 3 }],
    });
    executeSearchResult(0);
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(1);
    expect(/** @type {any} */ (window).applyTagFilter).toHaveBeenCalledWith(7, "beach");
  });

  test("date item picks matching smart_time album when present", () => {
    /** @type {any} */ (window).albumList = [
      { id: 5, album_type: "smart_time", rule: { year: "2024" } },
    ];
    renderSearchResults({
      dates: [{ label: "2024", year: 2024 }],
    });
    executeSearchResult(0);
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(5);
  });

  test("date item falls back to library when no smart_time album matches", () => {
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    renderSearchResults({
      dates: [{ label: "1999", year: 1999 }],
    });
    executeSearchResult(0);
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(1);
  });

  test("out-of-range index is a no-op", () => {
    renderSearchResults({});
    expect(() => executeSearchResult(99)).not.toThrow();
  });
});

describe("scrollToPhotoAndOpen", () => {
  test("opens lightbox immediately when photo is in current grid", async () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a.jpg" }, { filepath: "/b.jpg" }];
    await scrollToPhotoAndOpen("/b.jpg");
    expect(/** @type {any} */ (window).openLightbox).toHaveBeenCalledWith(1);
  });

  test("switches to library and opens after load when not in current grid", async () => {
    /** @type {any} */ (window).currentGridItems = [];
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    /** @type {any} */ (window).switchAlbum = vi.fn(async () => {
      /** @type {any} */ (window).currentGridItems = [{ filepath: "/c.jpg" }];
    });
    await scrollToPhotoAndOpen("/c.jpg");
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(1);
    expect(/** @type {any} */ (window).openLightbox).toHaveBeenCalledWith(0);
  });
});

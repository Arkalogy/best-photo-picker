// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _formatDuration,
  _updateVisibleCards,
  applyZoom,
  batchAddToAlbum,
  batchFavorite,
  batchOverride,
  clearMultiSelect,
  clearOverrides,
  countSelectedInScope,
  createAlbumAndAdd,
  handleCardClick,
  hideAlbumPicker,
  renderCardHTML,
  renderGrid,
  setOverride,
  showAlbumPickerModal,
  stepZoom,
  updateCardInPlace,
  updateMultiSelectUI,
  updateOverrideStats,
  vgrid,
} from "../bpp/web/static/js/modules/photos.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="photo-grid"></div>
    <div class="content"></div>
    <div id="toolbar-subtitle"></div>
    <input id="sort-by" />
    <input id="filter-by" />
    <input id="zoom-slider" type="range" min="40" max="300" value="80" />
    <span id="zoom-pct"></span>
    <div id="batch-bar">
      <span id="batch-count"></span>
      <button id="batch-compare-btn" style="display:none"></button>
    </div>
    <div id="album-picker-overlay">
      <div id="album-picker-list"></div>
      <input id="album-picker-new-name" />
    </div>
    <span id="override-stats"></span>
  `;
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).multiSelected = new Set();
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).sortedItems = [];
  /** @type {any} */ (window).lastMultiClickIdx = -1;
  /** @type {any} */ (window)._albumPickerFilepaths = [];
  /** @type {any} */ (window)._simClusterMap = {};
  /** @type {any} */ (window).ICONS = { paw: "<i>p</i>", pencil: "<i>e</i>", trash: "<i>t</i>" };
  /** @type {any} */ (window).openLightbox = vi.fn();
  /** @type {any} */ (window).updatePersonPhotoSelection = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "photos",
    "selectedPaths",
    "overrides",
    "favorites",
    "multiSelected",
    "albumList",
    "currentAlbumId",
    "currentGridItems",
    "sortedItems",
    "lastMultiClickIdx",
    "_albumPickerFilepaths",
    "_simClusterMap",
    "ICONS",
    "openLightbox",
    "updatePersonPhotoSelection",
    "renderAlbumNav",
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

const samplePhoto = {
  filepath: "/a.jpg",
  filename: "a.jpg",
  thumb_hash: "h1",
  aggregate_score: 0.7,
  blur_score: 0.6,
  exposure_score: 0.7,
  face_score: 0.5,
  composition_score: 0.4,
};

describe("_formatDuration", () => {
  test("under 1 hour: M:SS format", () => {
    expect(_formatDuration(65)).toBe("1:05");
    expect(_formatDuration(599)).toBe("9:59");
  });

  test("over 1 hour: H:MM:SS format", () => {
    expect(_formatDuration(3661)).toBe("1:01:01");
  });

  test("rounds non-integer seconds", () => {
    expect(_formatDuration(0.4)).toBe("0:00");
    expect(_formatDuration(0.5)).toBe("0:01");
  });
});

describe("renderCardHTML", () => {
  test("renders with score badge + filename", () => {
    const html = renderCardHTML(samplePhoto, 0);
    expect(html).toContain("70%");
    expect(html).toContain("a.jpg");
    expect(html).toContain('class="card');
  });

  test("includes selected class when in selectedPaths", () => {
    /** @type {any} */ (window).selectedPaths = new Set(["/a.jpg"]);
    expect(renderCardHTML(samplePhoto, 0)).toContain("selected");
  });

  test("includes is-fav when favorited", () => {
    /** @type {any} */ (window).favorites = new Set(["/a.jpg"]);
    expect(renderCardHTML(samplePhoto, 0)).toContain("is-fav");
  });

  test("override include badge + class", () => {
    /** @type {any} */ (window).overrides = { "/a.jpg": "include" };
    const html = renderCardHTML(samplePhoto, 0);
    expect(html).toContain("force-included");
    expect(html).toContain('override-badge">Pick');
  });

  test("video badge with duration", () => {
    const p = { ...samplePhoto, is_video: true, video_duration: 65 };
    expect(renderCardHTML(p, 0)).toContain("1:05");
  });

  test("dedup badge when cluster_size > 1", () => {
    expect(renderCardHTML({ ...samplePhoto, cluster_size: 3 }, 0)).toContain("dedup-badge");
  });
});

describe("renderGrid", () => {
  test("filters by selected", () => {
    /** @type {any} */ (window).photos = [
      { ...samplePhoto, filepath: "/a" },
      { ...samplePhoto, filepath: "/b" },
    ];
    /** @type {any} */ (window).selectedPaths = new Set(["/a"]);
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "selected";
    renderGrid();
    expect(/** @type {any} */ (window).currentGridItems).toHaveLength(1);
    expect(/** @type {any} */ (window).currentGridItems[0].filepath).toBe("/a");
  });

  test("excludes deleted unless filter='deleted'", () => {
    /** @type {any} */ (window).photos = [
      { ...samplePhoto, filepath: "/a" },
      { ...samplePhoto, filepath: "/b", deleted_at: "2024-01-01" },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    renderGrid();
    expect(/** @type {any} */ (window).currentGridItems).toHaveLength(1);
  });

  test("sorts by score-desc", () => {
    /** @type {any} */ (window).photos = [
      { ...samplePhoto, filepath: "/a", aggregate_score: 0.3 },
      { ...samplePhoto, filepath: "/b", aggregate_score: 0.9 },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    /** @type {HTMLInputElement} */ (document.getElementById("sort-by")).value = "score-desc";
    renderGrid();
    expect(/** @type {any} */ (window).currentGridItems[0].filepath).toBe("/b");
  });

  test("renders empty-state when no items match a non-all filter", () => {
    /** @type {any} */ (window).photos = [];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "favorites";
    renderGrid();
    expect(document.getElementById("photo-grid")?.textContent).toContain("No favorited photos");
  });

  test("empty-state survives a subsequent vgrid.onResize() (UAT Bug #7)", () => {
    // The bug: when a filter switch lands on items.length === 0, the
    // empty-state branch sets grid.innerHTML correctly. But vgrid still
    // holds the PREVIOUS filter's items in vgrid.items. Anything that
    // fires vgrid.onResize() afterward — the popover-close
    // ResizeObserver in app.mjs:360, a window resize, anything —
    // triggers vgrid.render(true), which re-renders the OLD items over
    // my empty-state HTML.
    //
    // User-visible symptom: clicking Enhanced (or any filter that
    // returns 0 matches) does nothing — the grid keeps showing the
    // unfiltered library, because vgrid re-paints over the empty-state
    // milliseconds after renderGrid wrote it.
    //
    // Force a realistic browser-like layout so vgrid.render() doesn't
    // bail out at its `!this.rowHeight` early-return — that's what
    // makes this test exercise the actual bug instead of accidentally
    // passing.
    const grid = /** @type {HTMLElement} */ (document.getElementById("photo-grid"));
    const content = /** @type {HTMLElement} */ (document.querySelector(".content"));
    Object.defineProperty(grid, "clientWidth", { value: 1000, configurable: true });
    Object.defineProperty(content, "clientHeight", { value: 800, configurable: true });

    /** @type {any} */ (window).photos = [
      { ...samplePhoto, filepath: "/a" },
      { ...samplePhoto, filepath: "/b" },
      { ...samplePhoto, filepath: "/c" },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    renderGrid(); // populates vgrid with 3 items (the 'previous filter')
    expect(vgrid.items.length).toBe(3);

    // Switch to a filter that returns 0 matches.
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "favorites";
    renderGrid();
    const initialHtml = document.getElementById("photo-grid")?.innerHTML || "";
    expect(initialHtml).toContain("No favorited photos");

    // NOW the ResizeObserver / window-resize fires — pre-fix this
    // overwrites the empty-state HTML with the OLD cards because
    // vgrid.items was never cleared.
    vgrid.onResize();

    const afterResizeHtml = document.getElementById("photo-grid")?.innerHTML || "";
    expect(afterResizeHtml).toContain("No favorited photos");
    // Belt-and-braces: assert no card markup snuck back in.
    expect(afterResizeHtml).not.toMatch(/class=["'][^"']*\bcard\b/);
  });

  test("populates _simClusterMap for photos with similar_photos", () => {
    /** @type {any} */ (window).photos = [
      {
        ...samplePhoto,
        filepath: "/a",
        similar_photos: [{ filepath: "/b" }],
      },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    renderGrid();
    expect(/** @type {any} */ (window)._simClusterMap["/a"]).toEqual(["/a", "/b"]);
    expect(/** @type {any} */ (window)._simClusterMap["/b"]).toEqual(["/a", "/b"]);
  });

  test("subtitle counts album-scoped picks, not library-wide", () => {
    // Regression for the 2026-05-22 bug where status bar showed
    // "50 selected of 24" — selectedPaths was library-wide but the
    // grid was already scoped to the album's 24 photos.
    const libraryPicks = new Set();
    for (let i = 0; i < 50; i++) libraryPicks.add("/lib/" + i + ".jpg");
    libraryPicks.add("/album/picked1.jpg");
    libraryPicks.add("/album/picked2.jpg");
    /** @type {any} */ (window).selectedPaths = libraryPicks;
    /** @type {any} */ (window).photos = [
      { ...samplePhoto, filepath: "/album/picked1.jpg" },
      { ...samplePhoto, filepath: "/album/picked2.jpg" },
      { ...samplePhoto, filepath: "/album/p3.jpg" },
    ];
    /** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value = "all";
    renderGrid();
    const subtitle = document.getElementById("toolbar-subtitle")?.textContent || "";
    expect(subtitle).toBe("2 selected of 3");
    expect(subtitle).not.toContain("50");
  });
});

describe("countSelectedInScope", () => {
  test("returns 0 when selectedPaths is empty", () => {
    expect(countSelectedInScope([{ filepath: "/a" }], new Set())).toBe(0);
  });

  test("returns 0 when selectedPaths is null", () => {
    expect(countSelectedInScope([{ filepath: "/a" }], null)).toBe(0);
  });

  test("counts intersection of photos and selectedPaths", () => {
    const picks = new Set(["/a", "/c"]);
    const photos = [{ filepath: "/a" }, { filepath: "/b" }, { filepath: "/c" }];
    expect(countSelectedInScope(photos, picks)).toBe(2);
  });

  test("ignores deleted photos", () => {
    const picks = new Set(["/a", "/b"]);
    const photos = [{ filepath: "/a", deleted_at: "2024-01-01" }, { filepath: "/b" }];
    expect(countSelectedInScope(photos, picks)).toBe(1);
  });

  test("ignores photos without a filepath field", () => {
    const picks = new Set(["/a"]);
    const photos = [{ filepath: "/a" }, { /* missing filepath */ id: 5 }];
    expect(countSelectedInScope(photos, picks)).toBe(1);
  });

  test("counts album-scoped picks, not library-wide picks", () => {
    // Regression for the "50 selected of 24" bug.
    const libraryPicks = new Set();
    for (let i = 0; i < 50; i++) libraryPicks.add("/lib/" + i + ".jpg");
    libraryPicks.add("/album/x.jpg");
    const albumPhotos = [{ filepath: "/album/x.jpg" }, { filepath: "/album/y.jpg" }];
    expect(countSelectedInScope(albumPhotos, libraryPicks)).toBe(1);
  });
});

describe("setOverride / clearOverrides", () => {
  test("setOverride toggles include → null on second call", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    /** @type {any} */ (window).overrides = {};
    await setOverride("/a", "include");
    expect(/** @type {any} */ (window).overrides["/a"]).toBe("include");
    await setOverride("/a", "include");
    expect(/** @type {any} */ (window).overrides["/a"]).toBeUndefined();
  });

  test("clearOverrides empties the map and POSTs", async () => {
    /** @type {any} */ (window).overrides = { "/a": "include", "/b": "exclude" };
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await clearOverrides();
    expect(Object.keys(/** @type {any} */ (window).overrides)).toHaveLength(0);
    expect(fetchMock).toHaveBeenCalled();
  });

  test("clearOverrides noop on empty map", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await clearOverrides();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("handleCardClick", () => {
  test("plain click opens lightbox", () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a.jpg" }];
    handleCardClick(/** @type {any} */ ({ metaKey: false, ctrlKey: false, shiftKey: false }), 0);
    expect(/** @type {any} */ (window).openLightbox).toHaveBeenCalledWith(0);
  });

  test("Cmd-click toggles multi-select", () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a.jpg" }];
    handleCardClick(
      /** @type {any} */ ({ metaKey: true, ctrlKey: false, preventDefault: () => {} }),
      0
    );
    expect(/** @type {any} */ (window).multiSelected.has("/a.jpg")).toBe(true);
    handleCardClick(
      /** @type {any} */ ({ metaKey: true, ctrlKey: false, preventDefault: () => {} }),
      0
    );
    expect(/** @type {any} */ (window).multiSelected.has("/a.jpg")).toBe(false);
  });

  test("Shift-click selects a range", () => {
    /** @type {any} */ (window).currentGridItems = [
      { filepath: "/a" },
      { filepath: "/b" },
      { filepath: "/c" },
    ];
    /** @type {any} */ (window).lastMultiClickIdx = 0;
    handleCardClick(
      /** @type {any} */ ({
        shiftKey: true,
        metaKey: false,
        ctrlKey: false,
        preventDefault: () => {},
      }),
      2
    );
    expect(/** @type {any} */ (window).multiSelected.size).toBe(3);
  });

  test("plain click with active multiSelect toggles", () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a" }];
    /** @type {any} */ (window).multiSelected = new Set(["/x"]);
    handleCardClick(/** @type {any} */ ({ metaKey: false, ctrlKey: false, shiftKey: false }), 0);
    expect(/** @type {any} */ (window).multiSelected.has("/a")).toBe(true);
  });

  test("deleted card click is a noop", () => {
    /** @type {any} */ (window).currentGridItems = [{ filepath: "/a", deleted_at: "2024-01-01" }];
    handleCardClick(/** @type {any} */ ({ metaKey: false, ctrlKey: false, shiftKey: false }), 0);
    expect(/** @type {any} */ (window).openLightbox).not.toHaveBeenCalled();
  });
});

describe("multiSelectUI / clearMultiSelect", () => {
  test("updateMultiSelectUI shows batch-bar when count > 0", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a", "/b"]);
    updateMultiSelectUI();
    expect(document.getElementById("batch-bar")?.classList.contains("visible")).toBe(true);
    expect(document.getElementById("batch-count")?.textContent).toBe("2 selected");
  });

  test("updateMultiSelectUI shows compare button when count >= 2", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a", "/b"]);
    updateMultiSelectUI();
    expect(
      /** @type {HTMLElement} */ (document.getElementById("batch-compare-btn")).style.display
    ).toBe("inline-block");
  });

  test("clearMultiSelect empties the set + hides batch bar", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a"]);
    clearMultiSelect();
    expect(/** @type {any} */ (window).multiSelected.size).toBe(0);
    expect(/** @type {any} */ (window).lastMultiClickIdx).toBe(-1);
  });
});

describe("batchOverride / batchFavorite", () => {
  test("batchOverride applies mode to all + POSTs", async () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a", "/b"]);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await batchOverride("include");
    expect(/** @type {any} */ (window).overrides["/a"]).toBe("include");
    expect(/** @type {any} */ (window).overrides["/b"]).toBe("include");
    expect(fetchMock).toHaveBeenCalled();
  });

  test("batchFavorite adds to favorites + POSTs", async () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a"]);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await batchFavorite(true);
    expect(/** @type {any} */ (window).favorites.has("/a")).toBe(true);
  });

  test("noop on empty selection", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await batchOverride("include");
    await batchFavorite(true);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("album picker", () => {
  test("show / hide toggles overlay class", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a"]);
    showAlbumPickerModal();
    expect(document.getElementById("album-picker-overlay")?.classList.contains("visible")).toBe(
      true
    );
    hideAlbumPicker();
    expect(document.getElementById("album-picker-overlay")?.classList.contains("visible")).toBe(
      false
    );
  });

  test("show with no selection is a noop", () => {
    showAlbumPickerModal();
    expect(document.getElementById("album-picker-overlay")?.classList.contains("visible")).toBe(
      false
    );
  });

  test("show with no manual albums shows the empty-state", () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a"]);
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    showAlbumPickerModal();
    expect(document.getElementById("album-picker-list")?.textContent).toContain("No albums");
  });

  test("batchAddToAlbum POSTs and refreshes album list", async () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a"]);
    /** @type {any} */ (window).albumList = [{ id: 7, album_type: "manual", name: "Trip" }];
    showAlbumPickerModal();
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      if (u.includes("/add-photos")) return jsonResp({ count: 1 });
      if (u.includes("/api/v1/albums")) return jsonResp({ albums: [] });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    await batchAddToAlbum(7);
    expect(fetchMock).toHaveBeenCalled();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      'Added 1 photos to "Trip"'
    );
  });

  test("createAlbumAndAdd creates + adds + refreshes", async () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a"]);
    showAlbumPickerModal();
    // showAlbumPickerModal clears the new-name input — set value AFTER.
    /** @type {HTMLInputElement} */ (document.getElementById("album-picker-new-name")).value =
      "Vacation";
    const fetchMock = vi.fn(async (url) => {
      const u = String(url);
      // Order matters — /add-photos is more specific than /api/albums.
      if (u.includes("/add-photos")) return jsonResp({ count: 1 });
      if (u.includes("/api/v1/albums")) return jsonResp({ id: 99, albums: [] });
      return jsonResp({});
    });
    vi.stubGlobal("fetch", fetchMock);
    await createAlbumAndAdd();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      'Created "Vacation" with 1 photos'
    );
  });
});

describe("updateOverrideStats", () => {
  test("None when empty", () => {
    updateOverrideStats();
    expect(document.getElementById("override-stats")?.textContent).toBe("None");
  });

  test("counts include + exclude", () => {
    /** @type {any} */ (window).overrides = { "/a": "include", "/b": "include", "/c": "exclude" };
    updateOverrideStats();
    expect(document.getElementById("override-stats")?.textContent).toBe("2 included, 1 excluded");
  });
});

describe("applyZoom / stepZoom", () => {
  test("clamps to [40, 300] and updates label", () => {
    applyZoom(150, false);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("150%");
    applyZoom(500, false);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("300%");
    applyZoom(10, false);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("40%");
  });

  test("stepZoom adjusts the slider by delta", () => {
    /** @type {HTMLInputElement} */ (document.getElementById("zoom-slider")).value = "100";
    stepZoom(20);
    expect(document.getElementById("zoom-pct")?.textContent).toBe("120%");
  });
});

describe("vgrid", () => {
  test("setItems populates internal items array", () => {
    vgrid.setItems([samplePhoto]);
    expect(vgrid.items).toHaveLength(1);
  });

  test("appendItems concatenates", () => {
    vgrid.setItems([samplePhoto]);
    vgrid.appendItems([{ ...samplePhoto, filepath: "/b" }]);
    expect(vgrid.items).toHaveLength(2);
  });

  test("onResize re-renders with updated clientHeight", () => {
    // Simulate initial render when clientHeight=0 (layout not yet resolved)
    const grid = document.getElementById("photo-grid");
    const content = document.querySelector(".content");
    if (!grid || !content) throw new Error("DOM not set up");

    // Give the grid a measurable size so measure() can compute rowHeight
    Object.defineProperty(grid, "clientWidth", { value: 1000, configurable: true });

    const items = Array.from({ length: 50 }, (_, i) => ({
      ...samplePhoto,
      filepath: `/p${i}.jpg`,
      thumb_hash: `h${i}`,
    }));
    vgrid.rowHeight = 260;
    vgrid.setItems(items);

    // clientHeight=0 → render schedules a rAF retry, lastRow stays -1
    Object.defineProperty(content, "clientHeight", { value: 0, configurable: true });
    vgrid.render(true);
    // With clientHeight=0 the render returns early (rAF retry) — lastRow unchanged
    expect(vgrid.lastRow).toBe(-1);

    // clientHeight=800 → re-render fills the viewport
    Object.defineProperty(content, "clientHeight", { value: 800, configurable: true });
    vgrid.onResize();
    const rowsAfterResize = vgrid.lastRow - vgrid.firstRow + 1;
    expect(rowsAfterResize).toBeGreaterThan(vgrid.buffer);
  });
});

describe("_updateVisibleCards / updateCardInPlace", () => {
  test("_updateVisibleCards iterates without throwing", () => {
    expect(() => _updateVisibleCards()).not.toThrow();
  });

  test("updateCardInPlace returns false when card missing", () => {
    expect(updateCardInPlace("/missing.jpg")).toBe(false);
  });
});

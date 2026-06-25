// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getAllTags,
  _getTagFilter,
  _setAllTags,
  _setPhotoTagsCache,
  _setTagFilter,
  applyTagFilter,
  batchAddTag,
  clearTagFilter,
  filterByTag,
  loadAllTags,
  onTagInput,
  renderTagFilterChip,
  renderTagsSidebar,
} from "../bpp/web/static/js/modules/tags.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="lb-tags" class="hidden"></div>
    <div id="tag-filter-chip" class="hidden"></div>
    <div id="tag-list"></div>
    <div id="toast-container"></div>
  `;
  /** @type {any} */ (window).renderGrid = vi.fn();
  /** @type {any} */ (window).loadAlbumList = vi.fn();
  _setAllTags([]);
  _setTagFilter(null);
  _setPhotoTagsCache({});
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).renderGrid);
  delete (/** @type {any} */ (window).loadAlbumList);
  delete (/** @type {any} */ (window).multiSelected);
  delete (/** @type {any} */ (window).currentGridItems);
  delete (/** @type {any} */ (window).lightboxIdx);
});

describe("loadAllTags", () => {
  test("populates allTags from /api/tags", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              tags: [
                { id: 1, name: "summer", count: 12 },
                { id: 2, name: "kids", count: 30 },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await loadAllTags();
    expect(_getAllTags()).toHaveLength(2);
  });

  test("on failure, leaves allTags untouched and console.warns", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    _setAllTags([{ id: 99, name: "stale" }]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await loadAllTags();
    expect(_getAllTags()).toEqual([{ id: 99, name: "stale" }]);
    expect(warnSpy).toHaveBeenCalled();
    warnSpy.mockRestore();
  });
});

describe("applyTagFilter / clearTagFilter", () => {
  test("apply sets the filter and triggers re-render", () => {
    applyTagFilter(7, "vacation");
    expect(_getTagFilter()).toEqual({ id: 7, name: "vacation" });
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
  });

  test("clear nulls the filter and triggers re-render", () => {
    _setTagFilter({ id: 1, name: "x" });
    clearTagFilter();
    expect(_getTagFilter()).toBeNull();
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
  });
});

describe("filterByTag", () => {
  test("pass-through when no filter active", () => {
    const items = [{ id: 1 }, { id: 2 }];
    expect(filterByTag(items)).toBe(items);
  });

  test("retains photos whose cache contains the active tag id", () => {
    _setTagFilter({ id: 7, name: "vacation" });
    _setPhotoTagsCache({
      1: [{ id: 7, name: "vacation" }],
      2: [{ id: 99, name: "other" }],
      3: [
        { id: 5, name: "kids" },
        { id: 7, name: "vacation" },
      ],
    });
    const out = filterByTag([{ id: 1 }, { id: 2 }, { id: 3 }, { id: 4 }]);
    expect(out.map((p) => p.id).sort()).toEqual([1, 3]);
  });

  test("photos missing from cache are excluded (cache miss = no match)", () => {
    _setTagFilter({ id: 7, name: "vacation" });
    _setPhotoTagsCache({}); // empty cache
    expect(filterByTag([{ id: 1 }])).toEqual([]);
  });
});

describe("renderTagFilterChip", () => {
  test("hidden + cleared when no filter", () => {
    document.getElementById("tag-filter-chip").innerHTML = "<old>";
    document.getElementById("tag-filter-chip").classList.remove("hidden");
    renderTagFilterChip();
    const chip = document.getElementById("tag-filter-chip");
    expect(chip.classList.contains("hidden")).toBe(true);
    expect(chip.innerHTML).toBe("");
  });

  test("renders the active chip with name + clear button", () => {
    _setTagFilter({ id: 1, name: "summer" });
    renderTagFilterChip();
    const chip = document.getElementById("tag-filter-chip");
    expect(chip.classList.contains("hidden")).toBe(false);
    expect(chip.textContent).toContain("summer");
    expect(chip.querySelector(".tag-chip-clear")).toBeTruthy();
  });

  test("escapes name to prevent XSS", () => {
    _setTagFilter({ id: 1, name: "<script>x" });
    renderTagFilterChip();
    expect(document.getElementById("tag-filter-chip").innerHTML).toContain("&lt;script&gt;x");
  });

  test("no-op when container is missing", () => {
    document.body.innerHTML = "";
    expect(() => renderTagFilterChip()).not.toThrow();
  });
});

describe("renderTagsSidebar", () => {
  test("'No tags yet' empty state when allTags is []", () => {
    renderTagsSidebar();
    expect(document.getElementById("tag-list").textContent).toContain("No tags yet");
  });

  test("renders one .nav-item per tag, sorted alphabetically", () => {
    _setAllTags([
      { id: 3, name: "z-tag", count: 1 },
      { id: 1, name: "a-tag", count: 5 },
      { id: 2, name: "m-tag", count: 2 },
    ]);
    renderTagsSidebar();
    const items = document.querySelectorAll("#tag-list .nav-item");
    expect(items).toHaveLength(3);
    expect(items[0].textContent).toContain("a-tag");
    expect(items[1].textContent).toContain("m-tag");
    expect(items[2].textContent).toContain("z-tag");
  });

  test("highlights the active filter with .active class", () => {
    _setAllTags([{ id: 1, name: "summer", count: 5 }]);
    _setTagFilter({ id: 1, name: "summer" });
    renderTagsSidebar();
    const item = document.querySelector("#tag-list .nav-item");
    expect(item?.classList.contains("active")).toBe(true);
  });

  test("escapes tag names inside inline handler attributes", () => {
    _setAllTags([{ id: 1, name: 'x" onmouseover="window.__bpp_xss = 1', count: 1 }]);
    renderTagsSidebar();

    const item = document.querySelector("#tag-list .nav-item");
    expect(item).toBeTruthy();
    expect(item.getAttribute("onmouseover")).toBeNull();
    expect(item.textContent).toContain('x" onmouseover="window.__bpp_xss = 1');
  });
});

describe("onTagInput", () => {
  beforeEach(() => {
    document.body.innerHTML += `
      <input id="lb-tag-input">
      <div id="lb-tag-suggest" class="hidden"></div>
    `;
  });

  test("hides suggestions when input is empty", () => {
    const input = /** @type {HTMLInputElement} */ (document.getElementById("lb-tag-input"));
    input.value = "";
    document.getElementById("lb-tag-suggest").classList.remove("hidden");
    onTagInput(input);
    expect(document.getElementById("lb-tag-suggest").classList.contains("hidden")).toBe(true);
  });

  test("shows prefix matches against allTags, capped at 8", () => {
    _setAllTags(
      Array.from({ length: 12 }, (_, i) => ({
        id: i + 1,
        name: "summer-" + (i + 1),
        count: i,
      }))
    );
    const input = /** @type {HTMLInputElement} */ (document.getElementById("lb-tag-input"));
    input.value = "summer";
    onTagInput(input);
    expect(document.getElementById("lb-tag-suggest").classList.contains("hidden")).toBe(false);
    expect(document.querySelectorAll(".lb-tag-option")).toHaveLength(8);
  });

  test("hides suggestions when no matches", () => {
    _setAllTags([{ id: 1, name: "winter", count: 1 }]);
    const input = /** @type {HTMLInputElement} */ (document.getElementById("lb-tag-input"));
    input.value = "summer";
    onTagInput(input);
    expect(document.getElementById("lb-tag-suggest").classList.contains("hidden")).toBe(true);
  });

  test("escapes suggestion names inside inline handler attributes", () => {
    _setAllTags([{ id: 1, name: 'x" onmouseover="window.__bpp_xss = 1', count: 1 }]);
    const input = /** @type {HTMLInputElement} */ (document.getElementById("lb-tag-input"));
    input.value = "x";
    onTagInput(input);

    const option = document.querySelector("#lb-tag-suggest .lb-tag-option");
    expect(option).toBeTruthy();
    expect(option.getAttribute("onmouseover")).toBeNull();
  });
});

describe("batchAddTag", () => {
  test("toasts when no selection", async () => {
    /** @type {any} */ (window).multiSelected = new Set();
    /** @type {any} */ (window).currentGridItems = [];
    await batchAddTag();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "No photos selected"
    );
  });
});

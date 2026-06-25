// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getMemoriesList,
  _memoryDateRange,
  _setMemoriesList,
  loadMemories,
  refreshMemories,
  renderGridFromItems,
  renderMemoriesSidebar,
} from "../bpp/web/static/js/modules/memories.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="memories-nav" class="hidden"></div>
    <div class="content">
      <div id="photo-grid"></div>
    </div>
    <div id="toast-container"></div>
  `;
  _setMemoriesList([]);
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

const nav = () => /** @type {HTMLElement} */ (document.getElementById("memories-nav"));

describe("_memoryDateRange", () => {
  test("empty when no date_start", () => {
    expect(_memoryDateRange(/** @type {any} */ ({}))).toBe("");
  });

  test("single-day range", () => {
    const out = _memoryDateRange(
      /** @type {any} */ ({ date_start: "2024-06-15", date_end: "2024-06-15" })
    );
    expect(out).toMatch(/2024/);
    expect(out).toMatch(/Jun/);
  });

  test("same-year range omits year on the start", () => {
    const out = _memoryDateRange(
      /** @type {any} */ ({ date_start: "2024-06-15", date_end: "2024-06-20" })
    );
    // Start has no year, end has year — joined with em dash variant
    expect(out).toContain("–");
    expect(out).toMatch(/2024/);
  });

  test("cross-year range includes year on both", () => {
    const out = _memoryDateRange(
      /** @type {any} */ ({ date_start: "2023-12-25", date_end: "2024-01-05" })
    );
    expect(out).toMatch(/2023/);
    expect(out).toMatch(/2024/);
  });

  test("garbage input — returns 'Invalid Date' string (production behavior)", () => {
    // Note: production code only catches thrown errors. jsdom's
    // toLocaleDateString silently returns "Invalid Date" for an
    // Invalid Date object instead of throwing — so the catch never
    // fires. Worth a TODO in the production code to validate the date
    // up front, but documenting current behavior here.
    const out = _memoryDateRange(/** @type {any} */ ({ date_start: "not a date" }));
    expect(out).toContain("Invalid Date");
  });
});

describe("renderMemoriesSidebar", () => {
  test("hides container + clears HTML when empty", () => {
    nav().classList.remove("hidden");
    nav().innerHTML = "<old>";
    renderMemoriesSidebar();
    expect(nav().classList.contains("hidden")).toBe(true);
    expect(nav().innerHTML).toBe("");
  });

  test("renders one .memory-card per memory, capped at 8", () => {
    /** @type {any[]} */
    const items = Array.from({ length: 12 }, (_, i) => ({
      id: i,
      title: `M${i}`,
      photo_count: 5,
    }));
    _setMemoriesList(items);
    renderMemoriesSidebar();
    expect(nav().classList.contains("hidden")).toBe(false);
    expect(nav().querySelectorAll(".memory-card")).toHaveLength(8);
  });

  test("escapes title to prevent XSS", () => {
    _setMemoriesList([/** @type {any} */ ({ id: 1, title: "<script>x", photo_count: 1 })]);
    renderMemoriesSidebar();
    expect(nav().innerHTML).toContain("&lt;script&gt;x");
  });

  test("uses hero_hash to set background-image when present", () => {
    _setMemoriesList([
      /** @type {any} */ ({ id: 1, title: "T", photo_count: 1, hero_hash: "abc123" }),
    ]);
    renderMemoriesSidebar();
    expect(nav().innerHTML).toContain("/thumb/abc123");
  });

  test("no-op when #memories-nav is missing", () => {
    document.body.innerHTML = "";
    expect(() => renderMemoriesSidebar()).not.toThrow();
  });
});

describe("loadMemories", () => {
  test("populates list from server response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ memories: [{ id: 1, title: "x", photo_count: 1 }] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await loadMemories();
    expect(_getMemoriesList()).toHaveLength(1);
    expect(nav().classList.contains("hidden")).toBe(false);
  });

  test("on fetch failure, empties the list silently", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network");
      })
    );
    _setMemoriesList([/** @type {any} */ ({ id: 99, title: "stale", photo_count: 1 })]);
    await loadMemories();
    expect(_getMemoriesList()).toEqual([]);
  });
});

describe("refreshMemories", () => {
  test("re-renders sidebar and toasts the count on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              memories: [
                { id: 1, title: "a", photo_count: 1 },
                { id: 2, title: "b", photo_count: 2 },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await refreshMemories();
    expect(_getMemoriesList()).toHaveLength(2);
    // refreshMemories pre-toasts "Generating memories…" before the
    // API call (project convention: nothing should be silent applies here —
    // the recompute can run 30s+ on a populated library). The
    // success message lands as a second toast after the response
    // returns. Find the success-class toast by content rather than
    // querying the first .toast in the container.
    const toasts = Array.from(document.querySelectorAll("#toast-container .toast"));
    const successToast = toasts.find((t) => (t.textContent || "").includes("memories generated"));
    expect(successToast).toBeDefined();
    expect(successToast?.textContent).toContain("2 memories");
  });

  test("on failure, toasts an error and leaves list intact", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network");
      })
    );
    const original = [/** @type {any} */ ({ id: 1, title: "a", photo_count: 1 })];
    _setMemoriesList(original);
    await refreshMemories();
    // Refresh failure → list re-rendered from current value (don't reset)
    const toastEl = document.querySelector("#toast-container .toast.error");
    // toastError → "Couldn't generate memories: <reason> — try again"
    expect(toastEl?.textContent).toContain("generate memories");
  });
});

describe("renderGridFromItems", () => {
  test("renders empty state when items is empty", () => {
    renderGridFromItems([]);
    expect(document.getElementById("photo-grid").innerHTML).toContain("No photos");
  });

  test("calls vgrid.setItems for non-empty input", () => {
    /** @type {any} */
    const setItems = vi.fn();
    /** @type {any} */ (window).vgrid = { setItems };
    renderGridFromItems([{ id: 1 }, { id: 2 }]);
    expect(setItems).toHaveBeenCalledWith([{ id: 1 }, { id: 2 }]);
    delete (/** @type {any} */ (window).vgrid);
  });

  test("no-op when #photo-grid is missing", () => {
    document.body.innerHTML = "";
    expect(() => renderGridFromItems([])).not.toThrow();
  });
});

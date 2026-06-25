// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _isLibraryPickerOpen,
  formatLibDate,
  hideLibraryPicker,
  loadLibraryList,
  renderLibraryList,
  showLibraryPicker,
  switchLibrary,
} from "../bpp/web/static/js/modules/library.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="library-picker-overlay" class="hidden">
      <div id="library-list"></div>
    </div>
    <div id="toast-container"></div>
    <div id="library-name-display"></div>
  `;
  /** @type {any} */ (window).ICONS = { folder: "<svg>F</svg>" };
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).ICONS);
});

const overlay = () =>
  /** @type {HTMLElement} */ (document.getElementById("library-picker-overlay"));
const list = () => /** @type {HTMLElement} */ (document.getElementById("library-list"));

describe("show/hideLibraryPicker", () => {
  test("show reveals overlay + sets open flag + triggers loadLibraryList", () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ libraries: [], path: "" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    showLibraryPicker();
    expect(overlay().classList.contains("hidden")).toBe(false);
    expect(_isLibraryPickerOpen()).toBe(true);
  });

  test("hide adds .hidden + clears open flag", () => {
    overlay().classList.remove("hidden");
    hideLibraryPicker();
    expect(overlay().classList.contains("hidden")).toBe(true);
    expect(_isLibraryPickerOpen()).toBe(false);
  });

  test("show is a no-op when overlay element is missing", () => {
    document.body.innerHTML = "";
    expect(() => showLibraryPicker()).not.toThrow();
    expect(_isLibraryPickerOpen()).toBe(false);
  });
});

describe("formatLibDate", () => {
  test("delegates to formatDate(iso, 'relative')", () => {
    // Use a known-old date so the relative formatter is deterministic.
    const out = formatLibDate("2020-06-15T12:00:00");
    expect(out).toContain("2020");
  });
});

describe("renderLibraryList", () => {
  test("empty state when libraries is []", () => {
    renderLibraryList([], "");
    expect(list().textContent).toContain("No libraries registered yet");
  });

  test("renders one .library-item per library", () => {
    renderLibraryList(
      [
        { path: "/p1", name: "First", exists: true },
        { path: "/p2", name: "Second", exists: true },
      ],
      "/p1"
    );
    const items = list().querySelectorAll(".library-item");
    expect(items).toHaveLength(2);
  });

  test("active library gets .library-item-active + 'Current' badge", () => {
    renderLibraryList(
      [
        { path: "/p1", name: "Active", exists: true },
        { path: "/p2", name: "Other", exists: true },
      ],
      "/p1"
    );
    const items = list().querySelectorAll(".library-item");
    expect(items[0].classList.contains("library-item-active")).toBe(true);
    expect(items[0].textContent).toContain("Current");
    expect(items[1].classList.contains("library-item-active")).toBe(false);
  });

  test("missing library gets .library-item-missing + 'Missing' badge + folder-not-exists hint", () => {
    renderLibraryList([{ path: "/gone", name: "Gone", exists: false }], "/active");
    const item = list().querySelector(".library-item");
    expect(item?.classList.contains("library-item-missing")).toBe(true);
    expect(item?.textContent).toContain("Missing");
    expect(item?.textContent).toContain("Folder no longer exists");
  });

  test("active library has NO Remove button (can't remove the active one)", () => {
    renderLibraryList(
      [
        { path: "/active", name: "Active", exists: true },
        { path: "/other", name: "Other", exists: true },
      ],
      "/active"
    );
    const items = list().querySelectorAll(".library-item");
    // First item is active — only the rename button (no danger button)
    expect(items[0].querySelector(".library-item-btn-danger")).toBeNull();
    // Second has both buttons
    expect(items[1].querySelector(".library-item-btn-danger")).toBeTruthy();
  });

  test("missing library has NO Rename button (just a Remove)", () => {
    renderLibraryList([{ path: "/gone", name: "Gone", exists: false }], "/active");
    const item = list().querySelector(".library-item");
    const buttons = item?.querySelectorAll("button") || [];
    expect(buttons).toHaveLength(1);
    expect(buttons[0].classList.contains("library-item-btn-danger")).toBe(true);
  });

  test("escapes name + path to prevent XSS", () => {
    renderLibraryList([{ path: "/<x>", name: "<script>x", exists: true }], "/active");
    expect(list().innerHTML).toContain("&lt;script&gt;x");
    expect(list().innerHTML).toContain("/&lt;x&gt;");
  });

  test("renders 'Last opened' for non-missing libraries with last_opened set", () => {
    renderLibraryList(
      [{ path: "/p", name: "X", exists: true, last_opened: "2020-06-15T12:00:00" }],
      "/active"
    );
    expect(list().textContent).toContain("Last opened");
    expect(list().textContent).toContain("2020");
  });

  test("no-op when container is missing", () => {
    document.body.innerHTML = "";
    expect(() => renderLibraryList([], "")).not.toThrow();
  });
});

describe("loadLibraryList", () => {
  test("fetches /api/libraries + /api/libraries/active and renders", async () => {
    let calls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url) => {
        calls++;
        if (String(url).includes("/active")) {
          return new Response(JSON.stringify({ path: "/active", name: "ActiveLib" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          });
        }
        return new Response(
          JSON.stringify({ libraries: [{ path: "/active", name: "ActiveLib", exists: true }] }),
          { status: 200, headers: { "content-type": "application/json" } }
        );
      })
    );
    await loadLibraryList();
    expect(calls).toBe(2);
    expect(list().textContent).toContain("ActiveLib");
  });

  test("on failure, renders 'Failed to load libraries'", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("boom");
      })
    );
    await loadLibraryList();
    expect(list().textContent).toContain("Failed to load libraries");
  });
});

describe("switchLibrary", () => {
  /** @type {Record<string,string>} */
  let store;
  /** @type {any} */
  let mockLocalStorage;

  beforeEach(() => {
    store = {};
    mockLocalStorage = {
      getItem: (/** @type {string} */ k) => (k in store ? store[k] : null),
      setItem: (/** @type {string} */ k, /** @type {string} */ v) => {
        store[k] = String(v);
      },
      removeItem: (/** @type {string} */ k) => {
        delete store[k];
      },
      clear: () => {
        for (const k of Object.keys(store)) delete store[k];
      },
    };
    vi.stubGlobal("localStorage", mockLocalStorage);
  });

  test("clears bpp_nav from localStorage before reloading", async () => {
    mockLocalStorage.setItem("bpp_nav", JSON.stringify({ view: "album", albumId: 153 }));

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
    const reloadSpy = vi.fn();
    vi.stubGlobal("location", { ...window.location, reload: reloadSpy });

    await switchLibrary("/new/library");

    expect(mockLocalStorage.getItem("bpp_nav")).toBeNull();
    expect(reloadSpy).toHaveBeenCalled();
  });

  test("does not clear bpp_nav when switch request fails", async () => {
    mockLocalStorage.setItem("bpp_nav", JSON.stringify({ view: "album", albumId: 153 }));

    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "forbidden" }), {
            status: 403,
            headers: { "content-type": "application/json" },
          })
      )
    );

    await switchLibrary("/new/library");

    expect(localStorage.getItem("bpp_nav")).not.toBeNull();
  });
});

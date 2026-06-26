// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  checkStorageHealth,
  loadOverridesFromDB,
  navigateTo,
  navigateToFavorites,
  navigateToLibraryPicks,
  navigateToPeople,
  navigateToPicks,
  recheckMissingPhotos,
  startStorageHealthCheck,
  switchToLibrary,
  toggleSidebar,
  updateStorageBanner,
  updateToolbarTitle,
} from "../bpp/web/static/js/modules/core.mjs";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="sidebar-overlay"></div>
    <aside class="sidebar"></aside>
    <main class="main">
      <div id="toolbar-title"></div>
      <div id="toolbar-subtitle"></div>
      <input id="filter-by" />
    </main>
  `;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).currentViewId = null;
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).storageOnline = true;
  /** @type {any} */ (window).storageCheckInterval = null;
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).switchAlbum = vi.fn(async () => {});
  /** @type {any} */ (window).scheduleRecompute = vi.fn();
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).renderGrid = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {any} */ (window).updateToolbarForView = vi.fn();
  /** @type {any} */ (window).hideCardCtxMenu = vi.fn();
  /** @type {any} */ (window).showPeopleView = vi.fn();
  /** @type {any} */ (window).showPetsView = vi.fn();
  /** @type {any} */ (window).showGroupsView = vi.fn();
  /** @type {any} */ (window).navigateToMap = vi.fn();
  /** @type {any} */ (window).navigateToCalendar = vi.fn();
  /** @type {any} */ (window).loadPhotosAndRecompute = vi.fn(async () => {});
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
  vi.useRealTimers();
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "currentAlbumId",
    "currentView",
    "currentViewId",
    "faceClusters",
    "storageOnline",
    "storageCheckInterval",
    "favorites",
    "overrides",
    "switchAlbum",
    "scheduleRecompute",
    "selectedPaths",
    "renderGrid",
    "renderAlbumNav",
    "updateToolbarForView",
    "hideCardCtxMenu",
    "showPeopleView",
    "showPetsView",
    "showGroupsView",
    "navigateToMap",
    "navigateToCalendar",
    "loadPhotosAndRecompute",
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

describe("loadOverridesFromDB", () => {
  test("populates window.overrides + window.favorites on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          overrides: { "/a": "include" },
          favorites: ["/b", "/c"],
        })
      )
    );
    await loadOverridesFromDB();
    expect(/** @type {any} */ (window).overrides).toEqual({ "/a": "include" });
    expect([.../** @type {any} */ (window).favorites].sort()).toEqual(["/b", "/c"]);
  });

  test("falls back to empty defaults on error", async () => {
    /** @type {any} */ (window).overrides = { "/old": "exclude" };
    /** @type {any} */ (window).favorites = new Set(["/old"]);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    await loadOverridesFromDB();
    expect(/** @type {any} */ (window).overrides).toEqual({});
    expect(/** @type {any} */ (window).favorites.size).toBe(0);
  });
});

describe("toggleSidebar", () => {
  test("flips .open on .sidebar and .visible on overlay", () => {
    toggleSidebar();
    expect(document.querySelector(".sidebar")?.classList.contains("open")).toBe(true);
    expect(document.getElementById("sidebar-overlay")?.classList.contains("visible")).toBe(true);
    toggleSidebar();
    expect(document.querySelector(".sidebar")?.classList.contains("open")).toBe(false);
    expect(document.getElementById("sidebar-overlay")?.classList.contains("visible")).toBe(false);
  });
});

describe("updateToolbarTitle", () => {
  test("library view → plain title", () => {
    /** @type {any} */ (window).currentView = "library";
    updateToolbarTitle();
    expect(document.getElementById("toolbar-title")?.textContent).toBe("Library");
  });

  test("subtitle is rendered when provided", () => {
    updateToolbarTitle("Library", "120 photos");
    expect(document.getElementById("toolbar-subtitle")?.textContent).toBe("120 photos");
  });

  test("smart_person album → 'Faces / <name>' breadcrumb", () => {
    /** @type {any} */ (window).currentView = "album";
    /** @type {any} */ (window).currentAlbumId = 5;
    /** @type {any} */ (window).albumList = [{ id: 5, album_type: "smart_person", name: "Alice" }];
    updateToolbarTitle();
    expect(document.getElementById("toolbar-title")?.textContent).toContain("Faces");
    expect(document.getElementById("toolbar-title")?.textContent).toContain("Alice");
  });

  test("manual album → 'Library / <name>' breadcrumb", () => {
    /** @type {any} */ (window).currentView = "album";
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).albumList = [{ id: 7, album_type: "manual", name: "Trip" }];
    updateToolbarTitle();
    expect(document.getElementById("toolbar-title")?.textContent).toContain("Library");
    expect(document.getElementById("toolbar-title")?.textContent).toContain("Trip");
  });

  test("favorites view → 'Library / Favorites'", () => {
    /** @type {any} */ (window).currentView = "favorites";
    updateToolbarTitle();
    const html = document.getElementById("toolbar-title")?.innerHTML || "";
    expect(html).toContain("Library");
    expect(html).toContain("Favorites");
  });
});

describe("switchToLibrary", () => {
  test("calls switchAlbum on the 'all' album", () => {
    /** @type {any} */ (window).albumList = [
      { id: 1, album_type: "all" },
      { id: 2, album_type: "manual" },
    ];
    switchToLibrary();
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(1);
  });

  test("noop when no 'all' album", () => {
    switchToLibrary();
    expect(/** @type {any} */ (window).switchAlbum).not.toHaveBeenCalled();
  });
});

describe("navigateTo", () => {
  test("library: switches album and shows photo-grid panels", () => {
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    navigateTo("library");
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(1);
  });

  test("people: hides photo-grid, calls showPeopleView", () => {
    navigateTo("people");
    expect(/** @type {any} */ (window).showPeopleView).toHaveBeenCalled();
    expect(/** @type {any} */ (window).currentView).toBe("people");
  });

  test("pets: calls showPetsView", () => {
    navigateTo("pets");
    expect(/** @type {any} */ (window).showPetsView).toHaveBeenCalled();
  });

  test("groups: calls showGroupsView", () => {
    navigateTo("groups");
    expect(/** @type {any} */ (window).showGroupsView).toHaveBeenCalled();
  });

  test("map / calendar: delegates to navigate helpers and returns early", () => {
    navigateTo("map");
    expect(/** @type {any} */ (window).navigateToMap).toHaveBeenCalled();
    navigateTo("calendar");
    expect(/** @type {any} */ (window).navigateToCalendar).toHaveBeenCalled();
  });
});

describe("navigateToPeople", () => {
  test("equivalent to navigateTo('people')", () => {
    navigateToPeople();
    expect(/** @type {any} */ (window).currentView).toBe("people");
  });
});

describe("storage health checks", () => {
  test("checkStorageHealth flips storageOnline + toasts on reconnect", async () => {
    /** @type {any} */ (window).storageOnline = false;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ accessible: true }))
    );
    await checkStorageHealth();
    expect(/** @type {any} */ (window).storageOnline).toBe(true);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Storage reconnected"
    );
  });

  test("updateStorageBanner adds banner when offline", () => {
    /** @type {any} */ (window).storageOnline = false;
    updateStorageBanner();
    expect(document.getElementById("storage-banner")).toBeTruthy();
    expect(document.getElementById("storage-banner")?.classList.contains("hidden")).toBe(false);
  });

  test("updateStorageBanner hides banner when online", () => {
    /** @type {any} */ (window).storageOnline = false;
    updateStorageBanner();
    /** @type {any} */ (window).storageOnline = true;
    updateStorageBanner();
    expect(document.getElementById("storage-banner")?.classList.contains("hidden")).toBe(true);
  });

  test("recheckMissingPhotos toasts and refreshes when restored > 0", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ restored: 5 }))
    );
    await recheckMissingPhotos();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Restored 5 photos"
    );
    expect(/** @type {any} */ (window).loadPhotosAndRecompute).toHaveBeenCalled();
  });

  test("startStorageHealthCheck sets storageCheckInterval and is idempotent", () => {
    startStorageHealthCheck();
    const first = /** @type {any} */ (window).storageCheckInterval;
    expect(first).toBeTruthy();
    startStorageHealthCheck();
    expect(/** @type {any} */ (window).storageCheckInterval).toBe(first);
  });
});

describe("navigateToFavorites / navigateToPicks", () => {
  test("navigateToFavorites awaits switchAlbum, sets filter to 'favorites'", async () => {
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    navigateToFavorites();
    // Wait for both microtasks (switchAlbum + .then)
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(/** @type {any} */ (window).currentView).toBe("favorites");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value).toBe(
      "favorites"
    );
  });

  test("navigateToPicks with photos loaded sets currentView and filter", () => {
    /** @type {any} */ (window).photos = [{ filepath: "/a.jpg" }, { filepath: "/b.jpg" }];
    navigateToPicks();
    expect(/** @type {any} */ (window).currentView).toBe("picks");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value).toBe(
      "selected"
    );
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
    expect(/** @type {any} */ (window).renderAlbumNav).toHaveBeenCalled();
    expect(/** @type {any} */ (window).updateToolbarForView).toHaveBeenCalled();
  });

  test("navigateToPicks with no photos loaded delegates to switchToLibrary", () => {
    /** @type {any} */ (window).photos = [];
    /** @type {any} */ (window).switchToLibrary = vi.fn();
    navigateToPicks();
    expect(/** @type {any} */ (window).switchToLibrary).toHaveBeenCalled();
    // renderGrid should NOT be called — we returned early
    expect(/** @type {any} */ (window).renderGrid).not.toHaveBeenCalled();
    delete (/** @type {any} */ (window).switchToLibrary);
  });

  test("navigateToPicks sets currentViewId to null", () => {
    /** @type {any} */ (window).photos = [{ filepath: "/a.jpg" }];
    /** @type {any} */ (window).currentViewId = 42;
    navigateToPicks();
    expect(/** @type {any} */ (window).currentViewId).toBeNull();
  });
});

describe("navigateToLibraryPicks", () => {
  const ALL_ALBUM = { id: 1, album_type: "all" };

  test("switches to all-album with force:true, then sets picks view and filter", async () => {
    /** @type {any} */ (window).albumList = [ALL_ALBUM];
    /** @type {any} */ (window).currentAlbumId = 7; // currently in a sub-album
    await navigateToLibraryPicks();
    expect(/** @type {any} */ (window).switchAlbum).toHaveBeenCalledWith(1, { force: true });
    expect(/** @type {any} */ (window).currentView).toBe("picks");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value).toBe(
      "selected"
    );
  });

  test("race condition guard: applyFilter is skipped if user navigated away during await", async () => {
    /** @type {any} */ (window).albumList = [ALL_ALBUM];
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).switchAlbum = vi.fn(async () => {
      // Simulate user navigating to a different album mid-flight
      /** @type {any} */ (window).currentAlbumId = 99;
    });
    await navigateToLibraryPicks();
    // applyFilter guard fires: currentAlbumId changed, so currentView must NOT be "picks"
    expect(/** @type {any} */ (window).currentView).not.toBe("picks");
  });

  test("already in all-album: skips switchAlbum, applies filter directly", async () => {
    /** @type {any} */ (window).albumList = [ALL_ALBUM];
    /** @type {any} */ (window).currentAlbumId = 1;
    /** @type {any} */ (window).photos = [{ filepath: "/a.jpg" }];
    await navigateToLibraryPicks();
    expect(/** @type {any} */ (window).switchAlbum).not.toHaveBeenCalled();
    expect(/** @type {any} */ (window).currentView).toBe("picks");
    expect(/** @type {HTMLInputElement} */ (document.getElementById("filter-by")).value).toBe(
      "selected"
    );
  });

  test("triggers scheduleRecompute when selectedPaths is empty after switching", async () => {
    /** @type {any} */ (window).albumList = [ALL_ALBUM];
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).selectedPaths = new Set(); // empty → needs recompute
    await navigateToLibraryPicks();
    expect(/** @type {any} */ (window).scheduleRecompute).toHaveBeenCalled();
  });

  test("does NOT trigger recompute when picks already exist", async () => {
    /** @type {any} */ (window).albumList = [ALL_ALBUM];
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).selectedPaths = new Set(["/a.jpg", "/b.jpg"]);
    await navigateToLibraryPicks();
    expect(/** @type {any} */ (window).scheduleRecompute).not.toHaveBeenCalled();
  });

  test("noop when no all-album in albumList", async () => {
    /** @type {any} */ (window).albumList = [{ id: 2, album_type: "manual" }];
    await navigateToLibraryPicks();
    expect(/** @type {any} */ (window).switchAlbum).not.toHaveBeenCalled();
    expect(/** @type {any} */ (window).currentView).toBe("library"); // unchanged
  });
});

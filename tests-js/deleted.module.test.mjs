// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getCardCtxFilepath,
  _resetDeletedState,
  batchDelete,
  batchEnhance,
  batchHide,
  deleteFromCard,
  deletePhotos,
  enhancePhotos,
  hideCardCtxMenu,
  hidePhotos,
  initCardCtxMenu,
  loadDeletedPhotos,
  loadHiddenPhotos,
  navigateToDeleted,
  navigateToHidden,
  permanentDeleteAll,
  permanentDeletePhotos,
  removeFromAlbum,
  restoreAllDeleted,
  restorePhotos,
  revertEnhance,
  showCardCtxMenu,
  unhideAllHidden,
  unhidePhotos,
} from "../bpp/web/static/js/modules/deleted.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay">
      <div class="confirm-dialog"></div>
    </div>
    <div id="photo-grid"></div>
    <div id="deleted-count"></div>
    <div id="hidden-count"></div>
    <div id="album-picker-overlay">
      <div id="album-picker-list"></div>
      <input id="album-picker-new-name" />
    </div>
    <div id="card-ctx-menu" class="hidden">
      <div class="ctx-menu-item" id="card-ctx-fav" data-action="favorite"></div>
      <div class="ctx-menu-item" id="card-ctx-include" data-action="include"></div>
      <div class="ctx-menu-item" id="card-ctx-exclude" data-action="exclude"></div>
      <div class="ctx-menu-item" id="card-ctx-tag" data-action="tag-person"></div>
      <div class="ctx-menu-item" id="card-ctx-enhance" data-action="enhance"></div>
      <div class="ctx-menu-item" id="card-ctx-revert" data-action="revert-enhance"></div>
      <div class="ctx-menu-item" id="card-ctx-hide" data-action="hide"></div>
      <div class="ctx-menu-item" id="card-ctx-unhide" data-action="unhide"></div>
      <div class="ctx-menu-item" id="card-ctx-delete" data-action="delete"></div>
      <div class="ctx-menu-item" id="card-ctx-restore" data-action="restore"></div>
      <div class="ctx-menu-item" id="card-ctx-perm-delete" data-action="perm-delete"></div>
      <div id="card-ctx-sep1"></div>
      <div id="card-ctx-sep2"></div>
    </div>
  `;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).currentViewId = null;
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).multiSelected = new Set();
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).ICONS = { trash: "<i>tr</i>", hidden: "<i>hi</i>" };
  /** @type {any} */ (window).clearMultiSelect = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {any} */ (window).hide = vi.fn();
  /** @type {any} */ (window).show = vi.fn();
  /** @type {any} */ (window).updateToolbarTitle = vi.fn();
  /** @type {any} */ (window).updateToolbarForView = vi.fn();
  /** @type {any} */ (window).loadPhotosAndRecompute = vi.fn();
  /** @type {any} */ (window).toggleFavorite = vi.fn();
  /** @type {any} */ (window).setOverride = vi.fn();
  /** @type {any} */ (window).showBatchRenameModal = vi.fn();
  /** @type {any} */ (window)._iphShowTagPicker = vi.fn();
  // resolveConfirm is window-bridged from dialogs.mjs
  /** @type {any} */ (window).resolveConfirm = vi.fn();
  _resetDeletedState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "currentAlbumId",
    "currentView",
    "currentViewId",
    "photos",
    "favorites",
    "overrides",
    "multiSelected",
    "faceClusters",
    "ICONS",
    "clearMultiSelect",
    "renderAlbumNav",
    "hide",
    "show",
    "updateToolbarTitle",
    "updateToolbarForView",
    "loadPhotosAndRecompute",
    "toggleFavorite",
    "setOverride",
    "showBatchRenameModal",
    "_iphShowTagPicker",
    "_albumPickerFilepaths",
    "resolveConfirm",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetDeletedState();
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

/**
 * Run a function that calls appConfirm, then immediately resolve the
 * confirm dialog with the given answer.
 * @param {() => Promise<any>} action
 * @param {boolean} answer
 */
async function withConfirm(action, answer) {
  const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
  const promise = action();
  // Wait one microtask for the dialog to render
  await Promise.resolve();
  await Promise.resolve();
  dialogs.resolveConfirm(answer);
  return promise;
}

describe("deletePhotos", () => {
  test("noop on empty input", async () => {
    await deletePhotos([]);
    expect(document.querySelectorAll("#toast-container .toast")).toHaveLength(0);
  });

  test("declined confirm = no API call, no toast", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await withConfirm(() => deletePhotos(["/a.jpg"]), false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("accepted confirm POSTs and toasts", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await withConfirm(() => deletePhotos(["/a.jpg", "/b.jpg"]), true);
    expect(fetchMock).toHaveBeenCalled();
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Deleted 2 photos"
    );
  });
});

describe("deleteFromCard", () => {
  test("calls deletePhotos when not in manual album", async () => {
    /** @type {any} */ (window).currentAlbumId = null;
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await withConfirm(() => deleteFromCard("/x.jpg"), true);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls.find((c) => String(c[0]).includes("/api/v1/photos/delete"))).toBeDefined();
  });

  test("calls removeFromAlbum when inside a manual album", async () => {
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).albumList = [{ id: 7, album_type: "manual", name: "Trip" }];
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await withConfirm(() => deleteFromCard("/x.jpg"), true);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(
      calls.find((c) => String(c[0]).includes("/api/v1/albums/7/remove-photos"))
    ).toBeDefined();
  });
});

describe("batchDelete", () => {
  test("noop on empty selection", async () => {
    await batchDelete();
    expect(document.querySelectorAll("#toast-container .toast")).toHaveLength(0);
  });

  test("uses multiSelected and prompts confirm", async () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a", "/b"]);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await withConfirm(() => batchDelete(), true);
    expect(fetchMock).toHaveBeenCalled();
  });
});

describe("showCardCtxMenu / hideCardCtxMenu", () => {
  // jsdom has `"ontouchstart" in window === true` AND a default
  // contextmenu MouseEvent has button=0. The touch-path branch in
  // showCardCtxMenu (`isTouch = "ontouchstart" in window && (!e.button
  // || …)`) would short-circuit into multi-select mode for every test
  // below unless we pass button=2 to mark the event as a real
  // right-click.
  const rightClick = (init = {}) => new MouseEvent("contextmenu", { button: 2, ...init });

  test("showCardCtxMenu unhides menu and stores filepath", () => {
    /** @type {any} */ (window).photos = [
      { filepath: "/x.jpg", thumb_hash: "h", deleted_at: null, hidden_at: null },
    ];
    showCardCtxMenu(rightClick({ clientX: 50, clientY: 60 }), "/x.jpg");
    expect(_getCardCtxFilepath()).toBe("/x.jpg");
    expect(document.getElementById("card-ctx-menu")?.classList.contains("hidden")).toBe(false);
  });

  test("hideCardCtxMenu re-hides and clears filepath", () => {
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h" }];
    showCardCtxMenu(rightClick(), "/x.jpg");
    hideCardCtxMenu();
    expect(_getCardCtxFilepath()).toBeNull();
    expect(document.getElementById("card-ctx-menu")?.classList.contains("hidden")).toBe(true);
  });

  test("favorite label flips to Unfavorite when filepath is favorited", () => {
    /** @type {any} */ (window).favorites = new Set(["/x.jpg"]);
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h" }];
    showCardCtxMenu(rightClick(), "/x.jpg");
    expect(document.getElementById("card-ctx-fav")?.textContent).toBe("Unfavorite");
  });

  test("delete label flips to Remove from Album in manual albums", () => {
    /** @type {any} */ (window).currentAlbumId = 7;
    /** @type {any} */ (window).albumList = [{ id: 7, album_type: "manual", name: "T" }];
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h" }];
    showCardCtxMenu(rightClick(), "/x.jpg");
    expect(document.getElementById("card-ctx-delete")?.textContent).toBe("Remove from Album");
  });

  test("hides non-applicable items in deleted view", () => {
    /** @type {any} */ (window).currentView = "deleted";
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h" }];
    showCardCtxMenu(rightClick(), "/x.jpg");
    expect(/** @type {HTMLElement} */ (document.getElementById("card-ctx-fav")).style.display).toBe(
      "none"
    );
    expect(
      /** @type {HTMLElement} */ (document.getElementById("card-ctx-restore")).style.display
    ).toBe("block");
    expect(
      /** @type {HTMLElement} */ (document.getElementById("card-ctx-perm-delete")).style.display
    ).toBe("block");
  });
});

describe("initCardCtxMenu", () => {
  // Same right-click shim as above — see the comment in the prior describe.
  const rightClick = (init = {}) => new MouseEvent("contextmenu", { button: 2, ...init });

  test("clicking 'favorite' menu item routes to toggleFavorite", () => {
    initCardCtxMenu();
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h" }];
    showCardCtxMenu(rightClick(), "/x.jpg");
    const item = /** @type {HTMLElement} */ (document.getElementById("card-ctx-fav"));
    item.click();
    expect(/** @type {any} */ (window).toggleFavorite).toHaveBeenCalledWith("/x.jpg");
  });

  test("clicking 'rename' opens batch rename modal", () => {
    initCardCtxMenu();
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h" }];
    showCardCtxMenu(rightClick(), "/x.jpg");
    // No data-action="rename" in fixture; simulate by manually crafting an item
    const renameItem = document.createElement("div");
    renameItem.className = "ctx-menu-item";
    renameItem.dataset.action = "rename";
    document.getElementById("card-ctx-menu")?.appendChild(renameItem);
    showCardCtxMenu(rightClick(), "/x.jpg");
    renameItem.click();
    expect(/** @type {any} */ (window).showBatchRenameModal).toHaveBeenCalled();
  });
});

describe("enhancePhotos / revertEnhance", () => {
  test("enhancePhotos POSTs and toasts on success", async () => {
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h" }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ enhanced: 1 }))
    );
    await enhancePhotos(["/x.jpg"]);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Enhanced 1 photo"
    );
    expect(/** @type {any} */ (window).photos[0]._enhanced).toBe(true);
  });

  test("enhancePhotos stops on error response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ error: "no models" }))
    );
    await enhancePhotos(["/x.jpg"]);
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "no models"
    );
  });

  test("revertEnhance clears _enhanced and toasts", async () => {
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "h", _enhanced: true }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ reset: 1 }))
    );
    await revertEnhance(["/x.jpg"]);
    expect(/** @type {any} */ (window).photos[0]._enhanced).toBe(false);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain("Reverted 1");
  });

  test("enhancePhotos adds card-enhanced-pulse and removes it after timeout", async () => {
    vi.useFakeTimers();
    const grid = document.getElementById("photo-grid");
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.filepath = "/x.jpg";
    card.dataset.idx = "0";
    grid?.appendChild(card);
    /** @type {any} */ (window).photos = [{ filepath: "/x.jpg", thumb_hash: "abc" }];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ enhanced: 1 }))
    );
    await enhancePhotos(["/x.jpg"]);
    expect(card.classList.contains("card-enhanced-pulse")).toBe(true);
    vi.advanceTimersByTime(1100);
    expect(card.classList.contains("card-enhanced-pulse")).toBe(false);
    vi.useRealTimers();
  });

  test("revertEnhance adds card-reverted-pulse and removes it after timeout", async () => {
    vi.useFakeTimers();
    const grid = document.getElementById("photo-grid");
    const card = document.createElement("div");
    card.className = "card";
    card.dataset.filepath = "/x.jpg";
    grid?.appendChild(card);
    /** @type {any} */ (window).photos = [
      { filepath: "/x.jpg", thumb_hash: "abc", _enhanced: true },
    ];
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ reset: 1 }))
    );
    await revertEnhance(["/x.jpg"]);
    expect(card.classList.contains("card-reverted-pulse")).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(card.classList.contains("card-reverted-pulse")).toBe(false);
    vi.useRealTimers();
  });
});

describe("removeFromAlbum", () => {
  test("noop when not in manual album", async () => {
    /** @type {any} */ (window).currentAlbumId = 1;
    /** @type {any} */ (window).albumList = [{ id: 1, album_type: "all" }];
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await removeFromAlbum(["/x"]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("POSTs to /albums/<id>/remove-photos in manual album", async () => {
    /** @type {any} */ (window).currentAlbumId = 5;
    /** @type {any} */ (window).albumList = [{ id: 5, album_type: "manual", name: "T" }];
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await removeFromAlbum(["/a", "/b"]);
    const call = /** @type {any[]} */ (fetchMock.mock.calls[0]);
    expect(String(call[0])).toContain("/api/v1/albums/5/remove-photos");
  });
});

describe("navigateToDeleted / navigateToHidden", () => {
  test("navigateToDeleted sets currentView and loads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ photos: [] }))
    );
    await navigateToDeleted();
    expect(/** @type {any} */ (window).currentView).toBe("deleted");
    expect(/** @type {any} */ (window).updateToolbarTitle).toHaveBeenCalledWith(
      "Recently Deleted",
      ""
    );
  });

  test("navigateToHidden sets currentView and loads", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ photos: [] }))
    );
    await navigateToHidden();
    expect(/** @type {any} */ (window).currentView).toBe("hidden");
    expect(/** @type {any} */ (window).updateToolbarTitle).toHaveBeenCalledWith("Hidden", "");
  });
});

describe("loadDeletedPhotos / loadHiddenPhotos", () => {
  test("loadDeletedPhotos shows empty state when no photos", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ photos: [] }))
    );
    await loadDeletedPhotos();
    expect(document.getElementById("photo-grid")?.textContent).toContain("No deleted photos");
  });

  test("loadDeletedPhotos renders one card per photo + Restore/Delete All toolbar", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          photos: [
            {
              filepath: "/a.jpg",
              filename: "a.jpg",
              thumb_hash: "h1",
              deleted_at: new Date().toISOString().slice(0, -1),
            },
          ],
        })
      )
    );
    await loadDeletedPhotos();
    expect(document.querySelectorAll(".deleted-card")).toHaveLength(1);
    expect(document.getElementById("photo-grid")?.innerHTML).toContain("Restore All");
    expect(document.getElementById("photo-grid")?.innerHTML).toContain("Delete All");
  });

  test("loadHiddenPhotos shows empty state when no photos", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({ photos: [] }))
    );
    await loadHiddenPhotos();
    expect(document.getElementById("photo-grid")?.textContent).toContain("No hidden photos");
  });

  test("loadHiddenPhotos renders Unhide All toolbar", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        jsonResp({
          photos: [{ filepath: "/x.jpg", filename: "x.jpg", thumb_hash: "h" }],
        })
      )
    );
    await loadHiddenPhotos();
    expect(document.getElementById("photo-grid")?.innerHTML).toContain("Unhide All");
  });
});

describe("restorePhotos / permanentDeletePhotos", () => {
  test("restorePhotos POSTs and toasts", async () => {
    const fetchMock = vi.fn(async () => jsonResp({ photos: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await restorePhotos(["/x"]);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain("Restored 1");
  });

  test("permanentDeletePhotos requires confirm + posts confirmation token", async () => {
    const fetchMock = vi.fn(async () => jsonResp({ photos: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await withConfirm(() => permanentDeletePhotos(["/y"]), true);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const call = calls.find((c) => String(c[0]).includes("delete-permanent"));
    expect(call).toBeDefined();
    expect(call && JSON.parse(call[1].body).confirmation).toBe("delete");
  });

  test("permanentDeletePhotos declined = no fetch", async () => {
    const fetchMock = vi.fn(async () => jsonResp({ photos: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await withConfirm(() => permanentDeletePhotos(["/y"]), false);
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls.find((c) => String(c[0]).includes("delete-permanent"))).toBeUndefined();
  });
});

describe("restoreAllDeleted / permanentDeleteAll / unhideAllHidden", () => {
  test("restoreAllDeleted gathers filepaths and restores", async () => {
    const grid = /** @type {HTMLElement} */ (document.getElementById("photo-grid"));
    grid.innerHTML = `
      <div class="deleted-card" data-filepath="/a"></div>
      <div class="deleted-card" data-filepath="/b"></div>
    `;
    const fetchMock = vi.fn(async () => jsonResp({ photos: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await restoreAllDeleted();
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    const call = calls.find((c) => String(c[0]).includes("/restore"));
    expect(call).toBeDefined();
    expect(call && JSON.parse(call[1].body).filepaths).toEqual(["/a", "/b"]);
  });

  test("permanentDeleteAll noop when grid is empty", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await permanentDeleteAll();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("unhideAllHidden gathers and unhides", async () => {
    const grid = /** @type {HTMLElement} */ (document.getElementById("photo-grid"));
    grid.innerHTML = `<div class="deleted-card" data-filepath="/h"></div>`;
    const fetchMock = vi.fn(async () => jsonResp({ photos: [] }));
    vi.stubGlobal("fetch", fetchMock);
    await unhideAllHidden();
    const calls = /** @type {any[][]} */ (fetchMock.mock.calls);
    expect(calls.find((c) => String(c[0]).includes("/api/v1/photos/unhide"))).toBeDefined();
  });
});

describe("hidePhotos / unhidePhotos / batchHide", () => {
  test("hidePhotos noop on empty input", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await hidePhotos([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("hidePhotos POSTs and toasts", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await hidePhotos(["/x", "/y"]);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Hidden 2 photos"
    );
  });

  test("unhidePhotos toasts the singular form for one item", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResp({}))
    );
    await unhidePhotos(["/x"]);
    expect(document.querySelector("#toast-container .toast")?.textContent).toContain(
      "Unhidden 1 photo"
    );
  });

  test("batchHide uses multiSelected", async () => {
    /** @type {any} */ (window).multiSelected = new Set(["/a"]);
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await batchHide();
    expect(fetchMock).toHaveBeenCalled();
  });
});

describe("batchEnhance", () => {
  test("noop on empty selection", async () => {
    const fetchMock = vi.fn(async () => jsonResp({}));
    vi.stubGlobal("fetch", fetchMock);
    await batchEnhance();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

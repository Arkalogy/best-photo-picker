// @ts-check
/**
 * Top-level navigation + view-aware breadcrumbs, sidebar toggle, library
 * switch, the storage-health banner & retry, and the favorites / picks
 * shortcuts. Plus `loadOverridesFromDB()` which seeds `state.overrides`
 * + `state.favorites` from the API on startup.
 *
 * `favorites`, `multiSelected`, `lastMultiClickIdx` are declared on
 * `window` from globals.js (so classic-script readers fall through to
 * the global object); this module never reassigns them — it only
 * mutates the underlying Set in place where possible. `state.favorites`
 * IS reassigned in `loadOverridesFromDB` because the original
 * implementation did so and several classic scripts (e.g. albums.js)
 * also reassign it; classic readers resolve `favorites` via the global
 * scope-chain fallback so reassignment via `state.favorites = ...`
 * remains visible to them.
 */

import { apiFetch } from "./api-client.mjs";
import { state } from "./state.mjs";
import { updateBreadcrumbs, saveNavState } from "./navigation.mjs";
import { hide, show } from "./utils.mjs";
import { toast, toastError } from "./toast.mjs";

export async function loadOverridesFromDB() {
  /** @type {any} */
  const win = window;
  try {
    const data = await apiFetch("/api/v1/overrides");
    win.overrides = data.overrides || {};
    win.favorites = new Set(data.favorites || []);
  } catch {
    win.overrides = {};
    win.favorites = new Set();
  }
}

export function toggleSidebar() {
  const sb = document.querySelector(".sidebar");
  const ov = document.getElementById("sidebar-overlay");
  if (!sb || !ov) return;
  sb.classList.toggle("open");
  ov.classList.toggle("visible", sb.classList.contains("open"));
  // Self-install the swipe-to-close listener on first open. Done lazily
  // so we don't race with sidebar DOM creation during initial render.
  _wireSidebarSwipeClose();
}

// Wire swipe-left-to-close on the sidebar itself. Native phone idiom:
// the sidebar that opened from the left edge closes when swiped back
// to the left. Tapping the backdrop still works (existing onclick on
// #sidebar-overlay) — this just adds the gesture as a second affordance.
//
// Threshold: 50px horizontal AND mostly horizontal motion (|dy/dx| < 0.6)
// AND quick (under 600ms). Slow drags are intentional, leave the sheet
// alone. Self-installs once on first toggleSidebar() call so we don't
// miss the listener target during initial render.
let _sidebarSwipeWired = false;
export function _wireSidebarSwipeClose() {
  if (_sidebarSwipeWired) return;
  const sb = document.querySelector(".sidebar");
  if (!sb) return;
  _sidebarSwipeWired = true;
  let sx = 0;
  let sy = 0;
  let st = 0;
  sb.addEventListener("touchstart", (ev) => {
    const e = /** @type {TouchEvent} */ (ev);
    if (e.touches.length !== 1) return;
    sx = e.touches[0].clientX;
    sy = e.touches[0].clientY;
    st = Date.now();
  }, { passive: true });
  sb.addEventListener("touchend", (ev) => {
    const e = /** @type {TouchEvent} */ (ev);
    if (e.changedTouches.length !== 1) return;
    if (!sb.classList.contains("open")) return;
    const dx = e.changedTouches[0].clientX - sx;
    const dy = e.changedTouches[0].clientY - sy;
    const dt = Date.now() - st;
    if (dt > 600) return;
    if (dx > -50) return; // not leftward enough
    // Tighter ratio (was 0.6) — 0.6 admitted ±31° drift, which fired
    // false-positives on momentum scrolls inside the sidebar nav. 0.35
    // is ~19°: still permissive enough for a real swipe with finger
    // wobble, strict enough to reject scroll-with-leftward-drift.
    if (Math.abs(dy) > Math.abs(dx) * 0.35) return;
    toggleSidebar();
  }, { passive: true });
}

/**
 * @param {string} [title]
 * @param {string} [subtitle]
 */
export function updateToolbarTitle(title, subtitle) {
  /** @type {any} */
  const win = window;
  const subtitleEl = document.getElementById("toolbar-subtitle");
  if (subtitleEl) subtitleEl.textContent = subtitle || "";

  const albums = /** @type {any[]} */ (win.albumList || []);
  const view = win.currentView;
  if (view === "library") {
    updateBreadcrumbs(title || "Library");
  } else if (view === "album") {
    const album = albums.find((a) => a.id === win.currentAlbumId);
    const atype = album ? album.album_type : "";
    if (atype === "smart_person") {
      updateBreadcrumbs(title || album.name, "Faces", "navigateTo('people')");
    } else if (atype === "smart_pet") {
      updateBreadcrumbs(title || album.name, "Pets", "navigateTo('pets')");
    } else if (atype === "smart_group") {
      updateBreadcrumbs(title || album.name, "Groups", "navigateTo('groups')");
    } else {
      updateBreadcrumbs(title || (album ? album.name : "Album"), "Library", "switchToLibrary()");
    }
  } else if (view === "people") {
    updateBreadcrumbs(title || "Faces", "Library", "switchToLibrary()");
  } else if (view === "picks") {
    updateBreadcrumbs(title || "BPP Picks", "Library", "switchToLibrary()");
  } else if (view === "favorites") {
    updateBreadcrumbs(title || "Favorites", "Library", "switchToLibrary()");
  } else if (view === "groups") {
    updateBreadcrumbs(title || "Groups", "Library", "switchToLibrary()");
  } else if (view === "deleted") {
    updateBreadcrumbs(title || "Recently Deleted", "Library", "switchToLibrary()");
  } else if (view === "hidden") {
    updateBreadcrumbs(title || "Hidden", "Library", "switchToLibrary()");
  } else {
    updateBreadcrumbs(title || "Library");
  }
  saveNavState();
}

export function switchToLibrary() {
  /** @type {any} */
  const win = window;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const allAlbum = albums.find((a) => a.album_type === "all");
  if (allAlbum) win.switchAlbum?.(allAlbum.id);
}

/**
 * @param {string} view
 * @param {string | number} [id]
 */
export function navigateTo(view, id) {
  /** @type {any} */
  const win = window;
  win.currentView = view;
  win.currentViewId = id || null;

  win.hideCardCtxMenu?.();

  document.querySelectorAll(".nav-item").forEach((el) => el.classList.remove("active"));

  if (view === "library" || view === "album") {
    if (id) {
      win.switchAlbum?.(id);
    } else {
      switchToLibrary();
    }
    show("photo-grid");
    hide("people-view");
    hide("pets-view");
    hide("groups-view");
    hide("tags-view");
    hide("map-view");
    hide("calendar-view");
  } else if (view === "people") {
    win.currentView = "people";
    win.currentAlbumId = null;
    const fc = /** @type {any[]} */ (win.faceClusters || []);
    updateToolbarTitle("Faces", `${fc.length} people`);
    hide("photo-grid");
    hide("person-album-bar");
    hide("person-photo-selection-bar");
    hide("pets-view");
    hide("groups-view");
    hide("tags-view");
    hide("map-view");
    hide("calendar-view");
    win.showPeopleView?.();
    win.renderAlbumNav?.();
  } else if (view === "pets") {
    win.currentView = "pets";
    win.currentAlbumId = null;
    hide("photo-grid");
    hide("people-view");
    hide("groups-view");
    hide("tags-view");
    hide("map-view");
    hide("calendar-view");
    win.showPetsView?.();
    win.renderAlbumNav?.();
  } else if (view === "groups") {
    win.currentView = "groups";
    win.currentAlbumId = null;
    hide("photo-grid");
    hide("people-view");
    hide("pets-view");
    hide("tags-view");
    hide("map-view");
    hide("calendar-view");
    win.showGroupsView?.();
    win.renderAlbumNav?.();
  } else if (view === "tags") {
    win.navigateToTags?.();
    return;
  } else if (view === "map") {
    win.navigateToMap?.();
    return;
  } else if (view === "calendar") {
    win.navigateToCalendar?.();
    return;
  } else if (view === "favorites") {
    navigateToFavorites();
    return;
  } else if (view === "picks") {
    navigateToPicks();
    return;
  }
  win.updateToolbarForView?.();
}

export function navigateToPeople() {
  navigateTo("people");
}

export async function checkStorageHealth() {
  /** @type {any} */
  const win = window;
  try {
    const data = await apiFetch("/api/v1/health/storage");
    const wasOffline = !win.storageOnline;
    win.storageOnline = data.accessible;
    updateStorageBanner();
    if (wasOffline && win.storageOnline) {
      toast("Storage reconnected");
      recheckMissingPhotos(true);
    }
  } catch {
    /* server probably down */
  }
}

export function updateStorageBanner() {
  /** @type {any} */
  const win = window;
  let banner = document.getElementById("storage-banner");
  if (!win.storageOnline) {
    if (!banner) {
      banner = document.createElement("div");
      banner.id = "storage-banner";
      banner.className = "storage-banner";
      banner.innerHTML = `
        <span class="storage-banner-icon">&#x26A0;</span>
        <span>Storage is unreachable. Photos on network drives may be unavailable.</span>
        <button data-action="recheckMissingPhotos">Retry</button>
      `;
      const main = document.querySelector(".main");
      if (main) main.prepend(banner);
    }
    banner.classList.remove("hidden");
  } else if (banner) {
    banner.classList.add("hidden");
  }
}

/**
 * @param {boolean} [background] true when fired by the storage-health
 *   poll — failures stay quiet there (an offline NAS would otherwise
 *   toast on every reconnect attempt). The banner's Retry button calls
 *   with no args and gets a real error toast.
 */
export async function recheckMissingPhotos(background) {
  /** @type {any} */
  const win = window;
  try {
    const data = await apiFetch("/api/v1/photos/recheck-missing", { method: "POST" });
    if (data.restored > 0) {
      toast(`Restored ${data.restored} photo${data.restored === 1 ? "" : "s"}`);
      await win.loadPhotosAndRecompute?.();
    }
  } catch (e) {
    if (background === true) return; // poll retries on its own
    toastError("recheck missing photos", e);
  }
}

export function startStorageHealthCheck() {
  /** @type {any} */
  const win = window;
  if (win.storageCheckInterval) return;
  // Skip the poll when the tab is hidden — there's no UI to update,
  // and on a NAS-backed library every poll round-trips a network
  // request that we'd be wasting. visibilitychange brings the poll
  // back the moment the tab is visible again.
  const tick = () => {
    if (document.hidden) return;
    checkStorageHealth();
  };
  win.storageCheckInterval = setInterval(tick, 30000);
  setTimeout(tick, 5000);
  // Re-check immediately on tab-become-visible so the user doesn't
  // see stale state after returning to a long-backgrounded tab.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) checkStorageHealth();
  });
}

export function navigateToFavorites() {
  /** @type {any} */
  const win = window;
  win.currentView = "favorites";
  win.currentViewId = null;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const allAlbum = albums.find((a) => a.album_type === "all");
  if (allAlbum && win.switchAlbum) {
    win.currentAlbumId = allAlbum.id;
    win.switchAlbum(allAlbum.id, { force: true }).then(() => {
      win.currentView = "favorites";
      const filterEl = /** @type {HTMLInputElement | null} */ (
        document.getElementById("filter-by")
      );
      if (filterEl) filterEl.value = "favorites";
      win.renderGrid?.();
      win.renderAlbumNav?.();
      win.updateToolbarForView?.();
    });
  }
}

export async function navigateToLibraryPicks() {
  /** @type {any} */
  const win = window;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const allAlbum = albums.find((a) => a.album_type === "all");
  if (!allAlbum) return;

  const applyFilter = () => {
    // Guard: user may have navigated elsewhere while switchAlbum was in flight.
    if (win.currentAlbumId !== allAlbum.id) return;
    win.currentView = "picks";
    win.currentViewId = null;
    const filterEl = /** @type {HTMLInputElement | null} */ (
      document.getElementById("filter-by")
    );
    if (filterEl) filterEl.value = "selected";
    // Only schedule recompute if switchAlbum didn't already trigger one.
    // switchAlbum auto-recomputes when selectedPaths is empty on a cold
    // album load — firing again here would double the API call.
    const selectedPaths = /** @type {Set<string>} */ (win.selectedPaths || new Set());
    if (selectedPaths.size === 0 && win.currentAlbumId === allAlbum.id) {
      win.scheduleRecompute?.();
    }
    win.renderGrid?.();
    win.renderAlbumNav?.();
    win.updateToolbarForView?.();
  };

  if (win.currentAlbumId === allAlbum.id) {
    applyFilter();
  } else if (win.switchAlbum) {
    win.currentAlbumId = allAlbum.id;
    // force:true matches navigateToFavorites and ensures the picks filter
    // applies even when already on the all-album with a different filter.
    try {
      await win.switchAlbum(allAlbum.id, { force: true });
      applyFilter();
    } catch (e) {
      console.warn("navigateToLibraryPicks: switchAlbum failed", e);
      win.currentAlbumId = null;
      win.toast?.("Failed to load library picks", true);
    }
  }
}

/**
 * Contextual picks filter — applies "selected" filter to the CURRENT album/view.
 * Does NOT switch to the library. Used by the toolbar chip.
 * For library-wide picks use navigateToLibraryPicks() (sidebar sub-item).
 */
export function navigateToPicks() {
  /** @type {any} */
  const win = window;
  // BPP Picks is a view filter on the current context — not a reload.
  // It just shows whichever photos are already selected in the current
  // album/library. The toolbar Pick [k] slider is responsible for
  // triggering recompute when k changes. This keeps the behavior
  // consistent: picks are always scoped to wherever you already are.
  win.currentView = "picks";
  win.currentViewId = null;
  const filterEl = /** @type {HTMLInputElement | null} */ (
    document.getElementById("filter-by")
  );
  if (filterEl) filterEl.value = "selected";

  // If no photos loaded yet, load the library first then show picks
  if ((win.photos || []).length === 0 && win.switchToLibrary) {
    win.switchToLibrary();
    return;
  }

  win.renderGrid?.();
  win.renderAlbumNav?.();
  win.updateToolbarForView?.();
}

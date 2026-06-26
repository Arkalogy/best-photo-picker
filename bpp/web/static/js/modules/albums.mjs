// @ts-check
/**
 * Album sidebar tree + album-switching + smart-album context menus.
 *
 * Builds the entire `#album-list` sidebar HTML in `renderAlbumNav()` —
 * Library, Faces/Pets/Groups/Map/Calendar, BPP Picks, Favorites,
 * manual-album folders, smart albums (Faces / Groups / Tags / Time),
 * Hidden + Recently Deleted footer.
 *
 * `switchAlbum(id)` is the workhorse: hides view panes, paginates
 * photos, populates `state.photos` / `selectedPaths` / `overrides` /
 * `favorites`, syncs K, kicks off auto-recompute on cold albums, and
 * scopes the boost chips to the album's faces.
 *
 * Cross-file callees that stay classic (people.js): `personDisplayName`,
 * `isClusterExcluded`, `showPersonCtxMenu`, `updatePersonAlbumBar` —
 * called via `window`.
 */

import { _getFaceGroups } from "./groups.mjs";
import { state } from "./state.mjs";
import { _getTagFilter, _setTagFilter } from "./tags.mjs";
import { _getTimelineFilter, _setTimelineFilter } from "./timeline.mjs";
import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm, appPrompt } from "./dialogs.mjs";
import { doRecompute, showSkeletonGrid, updateStats } from "./analysis.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { formatVal } from "./format-helpers.mjs";
import { hide, show } from "./utils.mjs";
import { loadAlbumFaces } from "./faces.mjs";
import { MONTHS_SHORT } from "./date-format.mjs";
import { petDisplayName } from "./pets.mjs";
import { renderGrid } from "./photos.mjs";
import { saveSetting } from "./settings-client.mjs";
import { showToast, toast } from "./toast.mjs";
import { toggleSidebar, updateToolbarTitle } from "./core.mjs";
import { updateToolbarForView } from "./toolbar.mjs";

export async function loadAlbumList() {
  // Protection B: route through wrapSectionLoader so an /api/v1/albums
  // failure can't blank the sidebar. The wrapper catches, sets a
  // sentinel, surfaces a retry toast, and re-renders the nav so the
  // OTHER sections (People / Pets / Tags) still display.
  const { wrapSectionLoader } = await import("./sidebar-safety.mjs");
  return wrapSectionLoader("albums", _loadAlbumListInner);
}

async function _loadAlbumListInner() {
  /** @type {any} */
  const win = window;
  const data = await apiFetch("/api/v1/albums");
  win.albumList = data.albums || [];
  renderAlbumNav();
  // Sidebar badges only need the total, not the photo payload. Ask
  // for a single row so we don't transfer up to 200 photo dicts
  // just to read the count.
  apiFetch("/api/v1/photos/deleted?limit=1")
    .then((d) => {
      const el = document.getElementById("deleted-count");
      if (el) el.textContent = (d.total || 0) > 0 ? d.total : "";
    })
    .catch((e) => console.warn("Deleted count fetch failed:", e));
  apiFetch("/api/v1/photos/hidden?limit=1")
    .then((d) => {
      const el = document.getElementById("hidden-count");
      if (el) el.textContent = (d.total || 0) > 0 ? d.total : "";
    })
    .catch((e) => console.warn("Hidden count fetch failed:", e));
}

// ── Album section state (persisted to localStorage) ──────────────────────────

const LS_COLLAPSED = "bpp-albums-collapsed";
const LS_SORT = "bpp-albums-sort";
const LS_FILTER = "bpp-albums-filter";
// Per-folder open state in the sidebar (Faces / Groups / Tags / Timeline /
// individual album folders / year folders). Persists across renders so a
// user-expanded folder with no active child doesn't collapse the moment they
// navigate elsewhere — the previous behavior recomputed `open` purely from
// "contains the active album" on every render.
const LS_NAV_OPEN = "bpp-nav-open-keys";

/** @returns {Record<string, boolean>} */
function _navOpenMap() {
  try {
    return JSON.parse(localStorage.getItem(LS_NAV_OPEN) || "{}") || {};
  } catch {
    return {};
  }
}
/**
 * Read the user's explicit preference for a folder. Returns:
 *   - `true`  → user opened it
 *   - `false` → user closed it
 *   - `undefined` → no preference recorded; caller decides the default
 *
 * Distinguishing "user closed it" from "no preference" matters for
 * sections whose default is open (like Albums) — without it, closing
 * the section would delete the key and the next render would re-default
 * to open, snapping right back.
 *
 * @param {string} key
 * @returns {boolean | undefined}
 */
export function _getNavFolderPref(key) {
  const map = _navOpenMap();
  if (!Object.prototype.hasOwnProperty.call(map, key)) return undefined;
  return map[key] === true;
}
/**
 * @param {string} key
 * @param {boolean} isOpen
 */
export function _setNavFolderOpen(key, isOpen) {
  const map = _navOpenMap();
  map[key] = !!isOpen;
  try {
    localStorage.setItem(LS_NAV_OPEN, JSON.stringify(map));
  } catch {
    /* quota — best-effort */
  }
}
/**
 * Decide whether a folder renders `open`. Priority (highest wins):
 *
 *   1. User preference (explicit open or close, persisted)
 *   2. `hasActiveChild` (auto-open the folder containing the current view)
 *   3. `defaultOpen` (per-section default — e.g. Albums defaults open,
 *      Faces/Tags default closed)
 *
 * @param {string} key
 * @param {boolean} hasActiveChild
 * @param {boolean} [defaultOpen]
 * @returns {string}  Returns the literal " open" attribute string or "".
 */
export function _navFolderOpenAttr(key, hasActiveChild, defaultOpen = false) {
  const pref = _getNavFolderPref(key);
  if (pref !== undefined) return pref ? " open" : "";
  return hasActiveChild || defaultOpen ? " open" : "";
}

/** @returns {boolean} */
export function _albumsCollapsed() {
  // Read priority: unified `section:albums` pref → legacy
  // `bpp-albums-collapsed` (migrates on next toggle) → default OPEN.
  const pref = _getNavFolderPref("section:albums");
  if (pref !== undefined) return !pref;
  return localStorage.getItem(LS_COLLAPSED) === "1";
}
/** @returns {"name-asc"|"name-desc"|"count-desc"|"count-asc"|"date-desc"} */
export function _albumsSort() {
  return /** @type {any} */ (localStorage.getItem(LS_SORT) || "name-asc");
}
/** @returns {string} */
export function _albumsFilter() {
  return localStorage.getItem(LS_FILTER) || "";
}

// ── Per-album filter state ───────────────────────────────────────────────────
//
// "Show only BPP Picks" and the other `filter-by` choices used to be global
// (one value across the whole app), so toggling it on Birthday Party would
// also affect Library and every other album. Users expect filters to be
// per-album — looking at Birthday Party's picks shouldn't change what they
// see when they switch to Hawaii Trip. Persists across refresh.

export const LS_ALBUM_FILTERS = "bpp-album-filters";

/** @returns {Record<string, string>} */
export function _albumFiltersMap() {
  try {
    return JSON.parse(localStorage.getItem(LS_ALBUM_FILTERS) || "{}") || {};
  } catch {
    return {};
  }
}

/**
 * Scope key for the current view. Used as both the read and write key for
 * per-album filter persistence. Returns null for views where filtering
 * doesn't make sense (e.g. People view, Calendar) so the caller can skip.
 *
 * @returns {string | null}
 */
function _filterScopeKey() {
  /** @type {any} */
  const win = window;
  if (win.currentView === "album" && win.currentAlbumId != null) {
    return `album:${win.currentAlbumId}`;
  }
  if (win.currentView === "library") return "view:library";
  if (win.currentView === "favorites") return "view:favorites";
  if (win.currentView === "picks") return "view:picks";
  return null;
}

/**
 * Persist the current filter-by value for the current scope. Called from
 * the filter dropdown's onchange wrapper (see `onFilterChange` below).
 */
export function saveCurrentScopeFilter() {
  const scope = _filterScopeKey();
  if (!scope) return;
  const filterEl = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("filter-by")
  );
  if (!filterEl) return;
  const map = _albumFiltersMap();
  const v = filterEl.value || "all";
  if (v === "all") {
    // "all" is the default — store as absence so we don't bloat the map
    // with no-op entries.
    delete map[scope];
  } else {
    map[scope] = v;
  }
  try {
    localStorage.setItem(LS_ALBUM_FILTERS, JSON.stringify(map));
  } catch {
    /* quota — best-effort */
  }
}

/**
 * Restore the saved filter for the current scope onto the filter-by
 * dropdown. Called by switchAlbum after currentAlbumId/currentView are
 * settled. Falls back to "all" when no preference is stored.
 */
export function restoreCurrentScopeFilter() {
  const scope = _filterScopeKey();
  if (!scope) return;
  const filterEl = /** @type {HTMLSelectElement | null} */ (
    document.getElementById("filter-by")
  );
  if (!filterEl) return;
  const map = _albumFiltersMap();
  filterEl.value = map[scope] || "all";
}

/**
 * Wraps the renderGrid call previously wired to filter-by's onchange.
 * Saves the new value into the per-scope map first so the choice
 * survives a refresh or a round-trip through another album.
 */
export function onFilterChange() {
  saveCurrentScopeFilter();
  /** @type {any} */
  const win = window;
  win.renderGrid?.();
}

export function toggleAlbumsCollapsed() {
  // Flip current state and persist via the unified map. The native
  // <details> toggle on user click already routes through the same
  // unified store via _installNavOpenPersistence; this function is for
  // programmatic callers (settings shortcuts, etc.).
  const isCollapsed = _albumsCollapsed();
  _setNavFolderOpen("section:albums", isCollapsed); // collapsed → now open
  renderAlbumNav();
}

const _SORT_CYCLE = /** @type {const} */ ([
  "name-asc",
  "name-desc",
  "count-desc",
  "count-asc",
  "date-desc",
]);
export const _SORT_LABEL = {
  "name-asc": "A–Z",
  "name-desc": "Z–A",
  "count-desc": "# ↓",
  "count-asc": "# ↑",
  "date-desc": "New",
};

export function cycleAlbumsSort() {
  const cur = _albumsSort();
  const next = _SORT_CYCLE[(_SORT_CYCLE.indexOf(cur) + 1) % _SORT_CYCLE.length];
  localStorage.setItem(LS_SORT, next);
  renderAlbumNav();
}

export function albumFilterInput(value) {
  localStorage.setItem(LS_FILTER, (value || "").trim());
  renderAlbumNav();
}

// ─────────────────────────────────────────────────────────────────────────────

/**
 * Wire up the persistence listener for `<details data-nav-key>` toggles.
 * Runs once per sidebar; the inner `_navPersistInstalled` flag prevents
 * adding multiple listeners on subsequent renderAlbumNav() calls.
 *
 * @param {HTMLElement} sidebar
 */
export function _installNavOpenPersistence(sidebar) {
  if (/** @type {any} */ (sidebar)._navPersistInstalled) return;
  /** @type {any} */ (sidebar)._navPersistInstalled = true;
  // The `toggle` event doesn't bubble, so we use capture-phase on the
  // sidebar root. Filter to elements declaring a data-nav-key — anything
  // else in the sidebar (e.g. third-party widget details) is ignored.
  sidebar.addEventListener(
    "toggle",
    (e) => {
      const t = /** @type {HTMLDetailsElement} */ (e.target);
      const key = t && t.dataset && t.dataset.navKey;
      if (!key) return;
      _setNavFolderOpen(key, !!t.open);
    },
    true,
  );
}


/**
 * @param {number} albumId
 * @param {{force?: boolean}} [opts]
 */


import {
  deleteAlbumPrompt,
  loadYearMonths,
  moveAlbumTo,
  removeSmartAlbum,
  removeTagAlbum,
  renameSmartAlbum,
  showAlbumMoveMenu,
  showNewAlbumInput,
  showSmartAlbumMenu,
  showTagAlbumMenu,
  switchToMonth,
  toggleFaceSort,
} from "./albums-menus.mjs";
export {
  deleteAlbumPrompt,
  loadYearMonths,
  moveAlbumTo,
  removeSmartAlbum,
  removeTagAlbum,
  renameSmartAlbum,
  showAlbumMoveMenu,
  showNewAlbumInput,
  showSmartAlbumMenu,
  showTagAlbumMenu,
  switchToMonth,
  toggleFaceSort,
};

import { switchAlbum } from "./albums-switch.mjs";
export { switchAlbum };

import { renderAlbumNav } from "./albums-render.mjs";
export { renderAlbumNav };

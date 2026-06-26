// @ts-check
/**
 * Album switching + paginated photo loading.
 *
 * Extracted from albums.mjs during the v0.1 cleanup. Owns the
 * workhorse that hides view panes, paginates photos, populates
 * state.photos / selectedPaths / overrides / favorites, syncs K,
 * kicks off auto-recompute on cold albums, and scopes the boost
 * chips to the album's faces.
 *
 *   * switchAlbum(albumId, opts) — main entry
 *   * _loadRemainingPages(albumId, pageSize) — pagination follow-up
 *
 * Re-exported from albums.mjs.
 */

import { state } from "./state.mjs";
import { apiFetch } from "./api-client.mjs";
import { doRecompute, showSkeletonGrid, updateStats } from "./analysis.mjs";
import { formatVal } from "./format-helpers.mjs";
import { hide, show } from "./utils.mjs";
import { loadAlbumFaces } from "./faces.mjs";
import { renderGrid } from "./photos.mjs";
import { _setTagFilter } from "./tags.mjs";
import { _setTimelineFilter } from "./timeline.mjs";
import { toast, toastError } from "./toast.mjs";
import { toggleSidebar, updateToolbarTitle } from "./core.mjs";
import { updateToolbarForView } from "./toolbar.mjs";
import { renderAlbumNav, restoreCurrentScopeFilter } from "./albums.mjs";
import { setSensitiveMode } from "./sensitive.mjs";


export async function switchAlbum(albumId, opts) {
  /** @type {any} */
  const win = window;
  if (
    !(opts && opts.force) &&
    win.currentAlbumId === albumId &&
    (win.currentView === "album" || win.currentView === "library")
  ) {
    return;
  }
  const sb = document.querySelector(".sidebar");
  if (sb && sb.classList.contains("open")) toggleSidebar();
  win.currentAlbumId = albumId;
  win.currentView = "album";
  win.currentViewId = albumId;
  _setTimelineFilter(null);
  _setTagFilter(null);
  document.querySelectorAll(".sidebar .nav-item").forEach((el) => el.classList.remove("active"));
  // Match the rendered selector — items are tagged with data-album-id.
  // The pre-modules code used an inline onclick="" attribute and grepped
  // for it here; after the data-action migration the selector matched
  // nothing, so the active highlight was silently lost on every album
  // switch and on every refresh-into-album.
  document
    .querySelectorAll(`.sidebar [data-album-id="${albumId}"]`)
    .forEach((el) => el.classList.add("active"));
  // Re-render the sidebar so the parent folder of the new album
  // auto-opens (the `hasActiveChild` check in renderAlbumNav needs
  // the updated currentAlbumId). Folder open-state persists via
  // localStorage, so this is non-destructive for user-expanded
  // folders elsewhere.
  renderAlbumNav();
  // Restore the per-album filter (e.g. user had "BPP Picks" on
  // Birthday Party — switching to Hawaii Trip resets to that album's
  // own saved filter, or "all" if none).
  restoreCurrentScopeFilter();
  hide("people-view");
  hide("pets-view");
  hide("groups-view");
  hide("tags-view");
  hide("map-view");
  hide("calendar-view");
  show("photo-grid");
  updateToolbarForView();
  // Note: filter-by no longer needs an "if leaving Favorites/Picks, reset
  // to all" coercion here — per-scope persistence (restoreCurrentScopeFilter
  // above) already loaded the correct filter for this album. A user who
  // explicitly wants "favorites only" within Album X is preserved.
  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = albums.find((a) => a.id === albumId);
  if (album && album.album_type === "all") {
    win.currentView = "library";
    updateToolbarTitle("Library", `${album.photo_count || 0} photos`);
  } else if (album) {
    updateToolbarTitle(album.name, `${album.photo_count || 0} photos`);
  }

  win.updatePersonAlbumBar?.(album);

  const grid = document.getElementById("photo-grid");
  if (grid && (!grid.children.length || !grid.querySelector(".card"))) {
    showSkeletonGrid();
  }

  const PAGE_SIZE = 1000;
  try {
    const data = await apiFetch(`/api/v1/albums/${albumId}/photos?limit=${PAGE_SIZE}&offset=0`);
    if (win.currentAlbumId !== albumId) return;
    win.photos = data.photos || [];
    win.selectedPaths = new Set(win.photos.filter((p) => p.selected).map((p) => p.filepath));

    win.overrides = {};
    for (const p of win.photos) {
      if (p.override) win.overrides[p.filepath] = p.override;
    }
    const isAllAlbum = album && album.album_type === "all";
    if (isAllAlbum) {
      win.favorites = new Set();
      for (const p of win.photos) {
        if (p.favorite) win.favorites.add(p.filepath);
      }
    }

    const albumData = data.album;
    const isAll = album && album.album_type === "all";
    if (!isAll && albumData && albumData.config) {
      document.querySelectorAll("[data-param]").forEach((el) => {
        const elH = /** @type {HTMLInputElement} */ (el);
        const key = /** @type {HTMLElement} */ (el).dataset.param || "";
        if (albumData.config[key] !== undefined) {
          elH.value = albumData.config[key];
          const sib = /** @type {HTMLElement | null} */ (el.nextElementSibling);
          if (sib) sib.textContent = formatVal(key, albumData.config[key]);
        }
      });
      // String enum — not a [data-param] slider (see sensitive.mjs).
      if (albumData.config.sensitive_in_picks !== undefined) {
        setSensitiveMode(albumData.config.sensitive_in_picks);
      }
    }
    const toolbarK = /** @type {HTMLInputElement | null} */ (document.getElementById("toolbar-k"));
    const paramK = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
    if (isAll) {
      const globalK = albumData?.k || albumData?.config?.default_selection_k || 50;
      if (toolbarK) toolbarK.value = String(globalK);
      if (paramK) paramK.value = String(globalK);
    } else if (albumData) {
      let albumK = albumData.k || 50;
      const autoK = !albumData.config || !albumData.config.k_user_set;
      if (autoK) {
        const total = data.total || win.photos.length;
        const globalK = parseInt(paramK?.value || "0") || 50;
        albumK = Math.max(5, Math.min(globalK, Math.round(total * 0.1)));
      }
      if (toolbarK) toolbarK.value = String(albumK);
      if (paramK) paramK.value = String(albumK);
    }

    const totalCount = data.total || win.photos.length;
    renderGrid();
    updateStats({ total: totalCount, total_selected: win.selectedPaths.size });

    if (data.has_more) {
      _loadRemainingPages(albumId, PAGE_SIZE);
    }

    // Auto-recompute on album entry so the displayed picks always reflect the
    // current k. Previously this only fired when selectedPaths was empty,
    // which left stale persisted picks visible when entering an album with
    // any prior selection (e.g. album has 1 stored pick but k=5 → user sees
    // "1 selected" instead of "5 selected"). Delta-mode is cheap; just do it.
    if (!isAll && win.photos.length > 0) {
      doRecompute({ delta: true }).catch((e) => console.warn("Auto-recompute failed:", e));
    }

    loadAlbumFaces(albumId);
  } catch (e) {
    if (/** @type {any} */ (e).status === 404) {
      const allAlbum = /** @type {any[]} */ (win.albumList || []).find(
        (a) => a.album_type === "all",
      );
      if (allAlbum && allAlbum.id !== albumId) switchAlbum(allAlbum.id);
    } else {
      console.warn("Failed to load album photos:", e);
    }
  }
}

/**
 * @param {number} albumId
 * @param {number} pageSize
 */
async function _loadRemainingPages(albumId, pageSize) {
  /** @type {any} */
  const win = window;
  const CONCURRENT = 2;
  let offset = pageSize;
  while (win.currentAlbumId === albumId) {
    const fetches = [];
    for (let i = 0; i < CONCURRENT; i++) {
      const url = `/api/v1/albums/${albumId}/photos?limit=${pageSize}&offset=${offset + i * pageSize}&slim=1`;
      const fetchWithRetry = (async () => {
        let lastErr = null;
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            return await apiFetch(url);
          } catch (e) {
            lastErr = e;
            if (attempt < 2) await new Promise((r) => setTimeout(r, (attempt + 1) * 1000));
          }
        }
        throw lastErr;
      })();
      fetches.push(fetchWithRetry);
    }

    let results;
    try {
      results = await Promise.all(fetches);
    } catch (e) {
      console.warn("Failed to load pages at offset", offset, e);
      toastError("load all photos", e);
      break;
    }
    if (win.currentAlbumId !== albumId) return;

    const albums = /** @type {any[]} */ (win.albumList || []);
    const isAll = albums.find((a) => a.id === albumId)?.album_type === "all";
    let anyMore = false;
    for (const data of results) {
      const batch = data.photos || [];
      if (batch.length === 0) break;
      win.photos = win.photos.concat(batch);
      for (const p of batch) {
        if (p.selected) win.selectedPaths.add(p.filepath);
        if (p.override) win.overrides[p.filepath] = p.override;
        if (isAll && p.favorite) win.favorites.add(p.filepath);
      }
      if (data.has_more) anyMore = true;
    }

    // The Moments album collapses bursts to one cover card each
    // (renderGrid does the collapse). It must NOT take the direct
    // vgrid.items=extended path below — that would shove the raw burst
    // photos in and clobber the covers. Route through renderGrid, which
    // is the single collapse point; keepScroll so streaming pages don't
    // jump the user back to the top mid-scroll.
    const _isMoments =
      /** @type {any[]} */ (win.albumList || []).find((a) => a.id === albumId)?.album_type ===
      "smart_moments";
    if (_isMoments) {
      win.renderGrid?.({ keepScroll: true });
    } else {
      const sortByEl = /** @type {HTMLInputElement | null} */ (document.getElementById("sort-by"));
      const filterByEl = /** @type {HTMLInputElement | null} */ (
        document.getElementById("filter-by")
      );
      const sortBy = sortByEl?.value || "";
      const filterBy = filterByEl?.value || "all";
      let extended = [...win.photos].filter((p) => !p.deleted_at);
      if (filterBy === "selected")
        extended = extended.filter((p) => win.selectedPaths.has(p.filepath));
      else if (filterBy === "favorites")
        extended = extended.filter((p) => win.favorites.has(p.filepath));
      else if (filterBy === "overridden")
        extended = extended.filter((p) => win.overrides[p.filepath]);
      if (sortBy === "score-desc")
        extended.sort((a, b) => (b.aggregate_score || 0) - (a.aggregate_score || 0));
      else if (sortBy === "date-desc")
        extended.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
      else if (sortBy === "date-asc")
        extended.sort((a, b) => (a.date || "").localeCompare(b.date || ""));
      if (win.vgrid) {
        win.vgrid.items = extended;
        win.vgrid.totalRows = Math.ceil(win.vgrid.items.length / win.vgrid.cols);
        // Rebuilding `extended` re-sorts the array, so idx -> photo
        // mapping changed. The render cache is keyed by idx, so old
        // entries are now garbage that just occupies the 600-slot LRU
        // until evicted by misses. Drop the cache outright — same
        // contract as setItems() but without the forced render() that
        // would cause flicker mid-page-load. Background scroll-driven
        // renders will refill the cache lazily for the visible window.
        win.vgrid._cardCache = new Map();
      }
      win.currentGridItems = extended;
      win.sortedItems = extended;
    }
    updateStats({
      total: results[0]?.total || win.photos.length,
      total_selected: win.selectedPaths.size,
    });

    offset += CONCURRENT * pageSize;
    if (!anyMore) break;
  }
  // Rebuild the sidebar once after pagination settles. Calling
  // renderAlbumNav() inside the loop reflows the full sidebar HTML on
  // every batch (5+ rebuilds on large albums) just to update counts.
  if (win.currentAlbumId === albumId) renderAlbumNav();
}

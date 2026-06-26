// @ts-check
/**
 * Recompute path: scheduleRecompute, getParams, doRecompute, updateStats,
 * showSkeletonGrid, paginated photo loader.
 *
 * Extracted from analysis.mjs during the v0.1 cleanup. Owns the
 * 'recompute selection over current scope' flow that fires from
 * weight-slider drag-end, K change, force-include/exclude, etc.
 *
 * Re-exported from analysis.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { state } from "./state.mjs";
import { toast, toastError } from "./toast.mjs";
import { loadAlbumList } from "./albums.mjs";
import { _resetAnalysisState, updateStatusSummary } from "./analysis.mjs";
import { syncToolbarK, updateShowPicksChip } from "./toolbar.mjs";
import { updateDedupStats } from "./clip.mjs";
import { renderGrid } from "./photos.mjs";
import { loadMemories } from "./memories.mjs";
import { switchAlbum } from "./albums-switch.mjs";
import { getSensitiveMode } from "./sensitive.mjs";


export function scheduleRecompute() {
  /** @type {any} */
  const win = window;
  if (win.recomputeTimer) clearTimeout(win.recomputeTimer);
  syncToolbarK();
  win.recomputeTimer = setTimeout(
    () => doRecompute({ delta: true }).catch((e) => console.warn("Recompute failed:", e)),
    150
  );
}

/**
 * @param {{delta?: boolean}} [opts]
 */
export function getParams(opts) {
  /** @type {any} */
  const win = window;
  /** @type {Record<string, any>} */
  const params = {};
  document.querySelectorAll("[data-param]").forEach((el) => {
    const key = /** @type {HTMLElement} */ (el).dataset.param;
    if (key) params[key] = parseFloat(/** @type {HTMLInputElement} */ (el).value);
  });
  // Sensitive-photo policy is a string enum, not a float slider — read
  // it from its own 2-way control (see sensitive.mjs).
  params.sensitive_in_picks = getSensitiveMode();
  const kInput = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
  params.k = parseInt(kInput?.value || "0") || 50;
  params.seed = 42;
  const selectedFaceIds = /** @type {Set<number>} */ (win.selectedFaceIds || new Set());
  if (selectedFaceIds.size > 0) {
    params.selected_faces = [...selectedFaceIds];
  }
  const photos = /** @type {any[]} */ (win.photos || []);
  if (opts && opts.delta && photos.length > 0) {
    params.delta = true;
  }
  return params;
}

/**
 * @param {{delta?: boolean}} [opts]
 */
export async function doRecompute(opts) {
  /** @type {any} */
  const win = window;
  const params = getParams(opts);
  // Album recompute always runs in delta mode. Album
  // photo metadata is already loaded via the paginated
  // /api/v1/albums/<id>/photos endpoint; recompute only needs to
  // return updated scores + selection. Without this, large albums
  // (>5000 photos) would 413 out and the UI would have to handle
  // it. Forcing delta on the album path is simpler and matches
  // what the all-photos first-load already does.
  if (win.currentAlbumId) {
    params.delta = true;
  }
  const url = win.currentAlbumId
    ? `/api/v1/albums/${win.currentAlbumId}/recompute`
    : "/api/v1/recompute";
  const _recomputeT0 = performance.now();
  try {
    const data = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    // Surface slow recomputes so the user knows what they're waiting on.
    // Prefer the server's elapsed_ms (excludes network) and fall back to
    // round-trip wall time. Threshold matches "noticeable lag" — sub-300ms
    // is silent so we don't spam on snappy local changes.
    const _wallMs = Math.round(performance.now() - _recomputeT0);
    const _serverMs = data?.stats?.elapsed_ms;
    const _shown = typeof _serverMs === "number" ? _serverMs : _wallMs;
    if (_shown >= 300) {
      toast(`Recomputed in ${(_shown / 1000).toFixed(2)}s`);
    }

    const isDelta = !data.photos && !!data.scores;
    const photos = /** @type {any[]} */ (win.photos || []);
    if (data.photos) {
      win.photos = data.photos;
    } else if (data.scores) {
      for (const p of photos) {
        if (p.filepath in data.scores) {
          p.aggregate_score = data.scores[p.filepath];
        }
      }
    }
    win.selectedPaths = new Set(data.selected_paths);
    updateShowPicksChip();

    const s = data.stats;
    updateStats(s);
    const filterBy = /** @type {HTMLInputElement | null} */ (
      document.getElementById("filter-by")
    )?.value;
    if (isDelta && filterBy !== "selected" && filterBy !== "not-selected") {
      win._updateVisibleCards?.();
    } else {
      win.renderGrid?.();
    }
    if (win.currentAlbumId) win.loadAlbumList?.();
  } catch (e) {
    toastError("recompute the selection", e);
  }
}

/**
 * @param {any} s
 */
export function updateStats(s) {
  updateStatusSummary(s);
  updateDedupStats(s);
}

/**
 * @param {number} [count]
 */
export function showSkeletonGrid(count) {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  const n = count || 12;
  grid.innerHTML = Array.from(
    { length: n },
    () =>
      `<div class="skeleton-card">
      <div class="skeleton-img"></div>
      <div class="skeleton-text">
        <div class="skeleton-line"></div>
        <div class="skeleton-line"></div>
      </div>
    </div>`
  ).join("");
}

/**
 * Paginated bulk fetch of all photos via /api/v1/photos. Used by the
 * first-load path on the All Photos view so a 50k+ library doesn't
 * OOM the browser on a single multi-MB JSON response. The server caps
 * each page at 5000 rows; we loop until has_more is false.
 *
 * Used in place of an unpaginated recompute payload for the initial
 * load. doRecompute is then invoked in delta mode to layer scores +
 * selection state on top of the loaded metadata.
 */
async function _loadAllPhotosPaginated() {
  /** @type {any} */
  const win = window;
  /** @type {any[]} */
  const all = [];
  const PAGE_SIZE = 5000;
  // PAGE_SIZE * MAX_PAGES = 500_000 photo dicts in memory. The previous
  // comment claimed 5M (assuming 50000 per page), but we use 5000 per
  // page so the real cap is 500k. Either bump the limit by raising one
  // of the multipliers, or surface a clear error when truncation would
  // happen — the worst outcome is a partially-loaded grid that the
  // user can't tell is partial. D-06.
  const MAX_PAGES = 100;
  let offset = 0;
  let truncated = false;
  let pages = 0;
  while (pages < MAX_PAGES) {
    const data = await apiFetch(`/api/v1/photos?limit=${PAGE_SIZE}&offset=${offset}`);
    if (!data || data.error) break;
    const page = data.photos || [];
    all.push(...page);
    if (!data.has_more || page.length === 0) {
      // Loaded everything cleanly.
      win.photos = all;
      return;
    }
    offset += page.length;
    pages++;
  }
  // Fell through the loop without seeing has_more=false → server still
  // had more rows after MAX_PAGES * PAGE_SIZE = 500k. Report truncation
  // so the user knows the grid is incomplete instead of staring at a
  // partial library wondering why their last 50k photos are missing.
  truncated = true;
  win.photos = all;
  if (truncated) {
    console.error(
      `Photo library exceeds ${all.length} dict ceiling — grid truncated. ` +
        `Bump MAX_PAGES in _loadAllPhotosPaginated or move to a streaming ` +
        `viewport-driven loader for libraries this large.`
    );
    toast(
      `Showing first ${all.length.toLocaleString()} photos. Library is larger; ` +
        "some photos won't appear in the grid. (Filters and search still work " +
        "via the server.)",
      true
    );
  }
}

export async function loadPhotosAndRecompute() {
  /** @type {any} */
  const win = window;
  const photos = /** @type {any[]} */ (win.photos || []);
  const isFirstLoad = photos.length === 0;
  if (isFirstLoad) showSkeletonGrid();

  // Paginate the photo load when the in-memory list is empty AND we
  // are showing the All Photos view. For non-all albums, switchAlbum()
  // has already populated photos via /api/v1/albums/<id>/photos, so
  // we just want a recompute. The currentAlbumId == all-album.id case
  // matters for fresh boot: app.mjs's _bootstrap defaults currentAlbumId
  // to the All album on first load (so the sidebar renders correctly),
  // but the photo grid is still empty and the album-recompute endpoint
  // assumes photos are already loaded. Without this check, the grid
  // never renders on a brand-new library.
  const albumList = /** @type {any[]} */ (win.albumList || []);
  const allAlbum = albumList.find((a) => a.album_type === "all");
  const isAllView = !win.currentAlbumId || (allAlbum && win.currentAlbumId === allAlbum.id);

  if (isFirstLoad && isAllView) {
    // Paginate the All Photos first-load so the recompute response
    // can stay in delta mode (scores + selection only, no full photo
    // dicts).
    await _loadAllPhotosPaginated();
    // Render the grid once before delta-recompute so the
    // _updateVisibleCards path inside doRecompute has DOM to update.
    win.renderGrid?.();
    await doRecompute({ delta: true });
  } else {
    await doRecompute();
  }
  loadMemories();
}

/** Test-only: reset internal state. */

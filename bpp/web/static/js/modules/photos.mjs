// @ts-check
/**
 * Photo grid: virtual scroll renderer (`vgrid`), card HTML, render loop,
 * multi-select handlers, batch ops (override/favorite/album), per-card
 * inline updates, video sprite scrub on hover, dupe-cluster highlight on
 * hover.
 *
 * Reads/writes shared globals on `window` (`photos`, `selectedPaths`,
 * `overrides`, `favorites`, `multiSelected`, `lastMultiClickIdx`,
 * `currentAlbumId`, `albumList`, `currentGridItems`, `sortedItems`,
 * `_albumPickerFilepaths`, `_simClusterMap`, `ICONS`). Cross-file
 * helpers (`openLightbox`, `updatePersonPhotoSelection`, `renderAlbumNav`)
 * stay classic-side and are looked up on `window`.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { _getTimelineFilter, buildTimeline } from "./timeline.mjs";
import { _getTagFilter, filterByTag, renderTagFilterChip } from "./tags.mjs";
import { deleteFromCard } from "./deleted.mjs";
import { formatDateStamp } from "./date-format.mjs";
import { safeRender } from "./sidebar-safety.mjs";
import { saveOverrides, toggleFavorite } from "./toolbar.mjs";
import { saveSetting } from "./settings-client.mjs";
import { scheduleRecompute } from "./analysis.mjs";
import { scoreBadgeBg } from "./score-format.mjs";
import { showToast, toastError } from "./toast.mjs";

// countSelectedInScope + _formatDuration moved to photos-helpers.mjs
// (LOC cap). Re-exported so the dispatcher + cross-module callers keep
// resolving them via the photos.mjs namespace.
import { countSelectedInScope, _formatDuration } from "./photos-helpers.mjs";
export { countSelectedInScope, _formatDuration };

// renderCardHTML moved to photos-card.mjs in the v0.1 cleanup.
// Re-exported below so the dispatcher + cross-module callers keep
// resolving it via the photos.mjs namespace.
import { renderCardHTML } from "./photos-card.mjs";
export { renderCardHTML };
import { computeMomentKeepers, momentClasses } from "./moments-view.mjs";
import { buildMomentStacks } from "./moments-stacks.mjs";

// ── Virtual Grid ──

export function _updateVisibleCards() {
  /** @type {any} */
  const win = window;
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  const selectedPaths = /** @type {Set<string>} */ (win.selectedPaths || new Set());
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());

  for (const card of /** @type {NodeListOf<HTMLElement>} */ (grid.querySelectorAll(".card"))) {
    const idx = parseInt(card.dataset.idx || "", 10);
    const p = vgrid.items[idx];
    if (!p) continue;
    const isSel = selectedPaths.has(p.filepath);
    const ov = overrides[p.filepath];
    const isFav = favorites.has(p.filepath);
    const isDeleted = !!p.deleted_at;
    let cls = "card";
    if (isDeleted) cls += " is-deleted";
    else if (ov === "include") cls += " force-included selected";
    else if (ov === "exclude") cls += " force-excluded";
    else if (isSel) cls += " selected";
    if (isFav) cls += " is-fav";
    if (multiSelected.has(p.filepath)) cls += " multi-selected";
    // Preserve Moment grouping classes — this updater rewrites className
    // wholesale, so without re-adding them the in-place sync (selection /
    // score) would strip the frame ("shows then disappears").
    cls += momentClasses(p, win.momentKeepers);
    card.className = cls;
    const score = p.aggregate_score || 0;
    const badge = /** @type {HTMLElement | null} */ (card.querySelector(".score-badge"));
    if (badge) {
      badge.textContent = (score * 100).toFixed(0) + "%";
      badge.style.background = scoreBadgeBg(score);
    }
  }
  const subtitleEl = document.getElementById("toolbar-subtitle");
  const filterEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
  const filterBy = filterEl ? filterEl.value : "all";
  const photos = /** @type {any[]} */ (win.photos || []);
  const activePhotos = photos.filter((p) => !p.deleted_at);
  if (subtitleEl && filterBy === "all") {
    const inScope = countSelectedInScope(activePhotos, selectedPaths);
    subtitleEl.textContent = `${inScope} selected of ${activePhotos.length}`;
  }
  updateOverrideStats();
}

/**
 * @param {string} filepath
 * @returns {boolean}
 */
export function updateCardInPlace(filepath) {
  /** @type {any} */
  const win = window;
  const grid = document.getElementById("photo-grid");
  if (!grid) return false;
  const selectedPaths = /** @type {Set<string>} */ (win.selectedPaths || new Set());
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());

  const cards = /** @type {NodeListOf<HTMLElement>} */ (grid.querySelectorAll(".card"));
  for (const card of cards) {
    const idx = parseInt(card.dataset.idx || "", 10);
    const p = vgrid.items[idx];
    if (!p || p.filepath !== filepath) continue;
    const isSel = selectedPaths.has(filepath);
    const ov = overrides[filepath];
    const isFav = favorites.has(filepath);
    const isDeleted = !!p.deleted_at;
    let cls = "card";
    if (isDeleted) cls += " is-deleted";
    else if (ov === "include") cls += " force-included selected";
    else if (ov === "exclude") cls += " force-excluded";
    else if (isSel) cls += " selected";
    if (isFav) cls += " is-fav";
    if (multiSelected.has(filepath)) cls += " multi-selected";
    // Preserve Moment grouping classes (see _updateVisibleCards) so an
    // in-place card update doesn't strip the frame.
    cls += momentClasses(p, win.momentKeepers);
    card.className = cls;
    const favBtn = card.querySelector(".card-action");
    if (favBtn) favBtn.className = "card-action" + (isFav ? " active-fav" : "");
    const incBtn = card.querySelectorAll(".card-action")[1];
    if (incBtn) incBtn.className = "card-action" + (ov === "include" ? " active-include" : "");
    let badge = card.querySelector(".override-badge");
    if (ov === "include" || ov === "exclude") {
      if (!badge) {
        badge = document.createElement("div");
        badge.className = "override-badge";
        card.querySelector(".card-image")?.appendChild(badge);
      }
      badge.textContent = ov === "include" ? "Pick" : "Skip";
    } else if (badge) {
      badge.remove();
    }
    return true;
  }
  return false;
}

/**
 * @param {string | number} pct
 * @param {boolean} [save]
 */
export function applyZoom(pct, save) {
  const grid = /** @type {HTMLElement | null} */ (document.getElementById("photo-grid"));
  const slider = /** @type {HTMLInputElement | null} */ (document.getElementById("zoom-slider"));
  const n = Math.max(40, Math.min(300, parseInt(String(pct), 10)));
  if (slider) slider.value = String(n);
  const label = document.getElementById("zoom-pct");
  if (label) label.textContent = n + "%";
  const size = Math.round((260 * n) / 100);
  if (grid) {
    grid.style.setProperty("--thumb-size", size + "px");
    grid.style.setProperty("--thumb-height", Math.round(size * 0.85) + "px");
  }
  vgrid.onResize();
  if (save !== false) saveSetting("zoom_pct", n);
}

/** @param {number} delta */
export function stepZoom(delta) {
  const slider = /** @type {HTMLInputElement | null} */ (document.getElementById("zoom-slider"));
  if (!slider) return;
  applyZoom(parseInt(slider.value, 10) + delta);
}

/**
 * Render photo grid. Protection F wraps the actual render so a single
 * render-time exception (bad data, missing global, vgrid bug) doesn't
 * leave the grid blank — a "Photo grid couldn't render" pill with a
 * Reload button appears in its container instead.
 *
 * @param {{keepScroll?: boolean}} [opts]
 */
export function renderGrid(opts) {
  safeRender("photo-grid", "Photo grid", () => _doRenderGrid(opts));
}

/**
 * @param {{keepScroll?: boolean}} [opts]
 */
function _doRenderGrid(opts) {
  /** @type {any} */
  const win = window;
  const resetScroll = !(opts && opts.keepScroll);
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  grid.classList.remove("simple-cards");
  const sortByEl = /** @type {HTMLInputElement | null} */ (document.getElementById("sort-by"));
  const filterByEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
  const sortBy = sortByEl?.value || "";
  const filterBy = filterByEl?.value || "all";
  const photos = /** @type {any[]} */ (win.photos || []);
  const selectedPaths = /** @type {Set<string>} */ (win.selectedPaths || new Set());
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  let items = [...photos];

  if (filterBy === "deleted") {
    items = items.filter((p) => p.deleted_at);
  } else {
    items = items.filter((p) => !p.deleted_at);
    if (filterBy === "selected") items = items.filter((p) => selectedPaths.has(p.filepath));
    else if (filterBy === "not-selected")
      items = items.filter((p) => !selectedPaths.has(p.filepath));
    else if (filterBy === "favorites") items = items.filter((p) => favorites.has(p.filepath));
    else if (filterBy === "overridden") items = items.filter((p) => overrides[p.filepath]);
    else if (filterBy === "enhanced") items = items.filter((p) => p._enhanced || p._auto_enhanced);
    else if (filterBy === "photos-only") items = items.filter((p) => !p.is_video && !p.is_raw);
    else if (filterBy === "videos-only") items = items.filter((p) => p.is_video);
    else if (filterBy === "raw-only") items = items.filter((p) => p.is_raw);
  }

  const _tlFilter = _getTimelineFilter();
  if (_tlFilter) items = items.filter((p) => p.date_month === _tlFilter);

  if (_getTagFilter()) items = filterByTag(items);
  renderTagFilterChip();

  if (sortBy === "score-desc")
    items.sort((a, b) => (b.aggregate_score || 0) - (a.aggregate_score || 0));
  else if (sortBy === "score-asc")
    items.sort((a, b) => (a.aggregate_score || 0) - (b.aggregate_score || 0));
  else if (sortBy === "date-asc") items.sort((a, b) => (a.date || "").localeCompare(b.date || ""));
  else if (sortBy === "date-desc") items.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
  else if (sortBy === "name")
    items.sort((a, b) => (a.filename || "").localeCompare(b.filename || ""));
  else if (sortBy === "size-desc") items.sort((a, b) => (b.file_size || 0) - (a.file_size || 0));
  else if (sortBy === "size-asc") items.sort((a, b) => (a.file_size || 0) - (b.file_size || 0));
  else if (sortBy === "faces-desc") items.sort((a, b) => (b.face_count || 0) - (a.face_count || 0));

  const activePhotos = photos.filter((p) => !p.deleted_at);
  const total = activePhotos.length;
  const shown = items.length;
  const inScopeSelected = countSelectedInScope(activePhotos, selectedPaths);
  const subtitleText =
    filterBy === "deleted"
      ? `${shown} deleted`
      : filterBy === "all"
        ? `${inScopeSelected} selected of ${total}`
        : `${shown} of ${total}`;
  const subtitleEl = document.getElementById("toolbar-subtitle");
  if (subtitleEl) subtitleEl.textContent = subtitleText;

  // Moments album: collapse each burst to one cover card (a prune queue,
  // not a photo grid). Single collapse point — both the initial render
  // and streaming background pages route here. Clicking a cover opens the
  // compare overlay (handleCardClick honors _momentSiblings).
  const _curAlbum = /** @type {any[]} */ (win.albumList || []).find(
    (a) => a.id === win.currentAlbumId
  );
  if (_curAlbum && _curAlbum.album_type === "smart_moments") {
    items = buildMomentStacks(items);
  }

  win.sortedItems = items;
  win.currentGridItems = items;
  // Keeper filepath per Moment, for the gallery's in-place keeper star.
  win.momentKeepers = computeMomentKeepers(items);

  if (resetScroll) {
    const content = /** @type {HTMLElement | null} */ (document.querySelector(".content"));
    if (content) content.scrollTop = 0;
    /** @type {HTMLElement} */ (grid).style.paddingTop = "0";
    /** @type {HTMLElement} */ (grid).style.paddingBottom = "0";
  }

  if (items.length === 0 && filterBy !== "all") {
    // UAT Bug #7: clear vgrid's item list BEFORE writing the
    // empty-state HTML. app.mjs:360 installs a ResizeObserver on
    // .content that fires when the popover closes — that
    // ResizeObserver calls vgrid.onResize() → vgrid.render(true),
    // which re-renders OLD items over our empty-state HTML if
    // vgrid.items still holds the previous filter's photos. Clearing
    // first makes vgrid.render() take its `items.length === 0` early
    // return and leaves the empty-state in place.
    vgrid.items = [];
    vgrid.totalRows = 0;
    vgrid.firstRow = -1;
    vgrid.lastRow = -1;

    // L-S4: each empty-state carries a one-line hint explaining the
    // action that populates the filter. Without the hints a first-time
    // user clicking 'Favorites' on a brand-new library saw a bare
    // 'No favorited photos' and concluded the feature was broken
    // rather than 'I haven't used this feature yet'.
    /** @type {Record<string, {label: string, hint?: string}>} */
    const empties = {
      selected: {
        label: "selected",
        hint: "Selection is what BPP Picks chose — recompute picks or adjust the K slider.",
      },
      "not-selected": {
        label: "not selected",
        hint: "Every photo here is in your current picks.",
      },
      favorites: {
        label: "favorited",
        hint: "Click the heart icon on any photo to add it here.",
      },
      overridden: {
        label: "overridden",
        hint: "Right-click any photo and choose Include or Exclude to add an override.",
      },
      enhanced: {
        label: "enhanced",
        hint: "Open a photo in the lightbox and use the Edit pane to enhance it.",
      },
      deleted: {
        label: "deleted",
        hint: "Deleted photos appear here for 30 days before they're permanently removed.",
      },
      "photos-only": { label: "photo", hint: "Try All to see videos and RAWs too." },
      "videos-only": { label: "video", hint: "Import a folder containing .mp4 / .mov files." },
      "raw-only": { label: "RAW", hint: "Import a folder containing camera RAW files." },
    };
    const entry = empties[filterBy] || { label: filterBy };
    grid.innerHTML =
      `<div class="grid-empty-msg">No ${esc(entry.label)} photos` +
      (entry.hint ? `<div class="grid-empty-hint">${esc(entry.hint)}</div>` : "") +
      `</div>`;
  } else {
    vgrid.setItems(items);
    /** @type {Record<string, string[]>} */
    const sim = {};
    // Build from explicit similar_photos (Duplicates album API response)
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it && it.similar_photos && it.similar_photos.length > 0) {
        const cluster = [it.filepath];
        for (let j = 0; j < it.similar_photos.length; j++) {
          cluster.push(it.similar_photos[j].filepath);
        }
        for (let k = 0; k < cluster.length; k++) {
          sim[cluster[k]] = cluster;
        }
      }
    }
    // Also build from dup_cluster_id so the lightbox similar-photos strip
    // works from any view (Library, Needs Review, etc.), not just Duplicates.
    /** @type {Record<number, string[]>} */
    const byCluster = {};
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      const cid = it && it.dup_cluster_id;
      if (cid && cid > 0) {
        if (!byCluster[cid]) byCluster[cid] = [];
        byCluster[cid].push(it.filepath);
      }
    }
    for (const paths of Object.values(byCluster)) {
      if (paths.length > 1) {
        for (let k = 0; k < paths.length; k++) {
          if (!sim[paths[k]]) sim[paths[k]] = paths;
        }
      }
    }
    win._simClusterMap = sim;
  }

  updateOverrideStats();
  buildTimeline();
}

/**
 * @param {string} filepath
 * @param {string | null} mode
 */
export async function setOverride(filepath, mode) {
  /** @type {any} */
  const win = window;
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const selectedPaths = /** @type {Set<string>} */ (win.selectedPaths || new Set());
  const prevMode = overrides[filepath] || null;
  const newMode = prevMode === mode ? null : mode;
  if (newMode) overrides[filepath] = newMode;
  else delete overrides[filepath];
  saveOverrides();
  scheduleRecompute();
  const url = win.currentAlbumId ? `/api/v1/albums/${win.currentAlbumId}/override` : "/api/v1/override";
  try {
    const data = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filepath,
        mode: newMode,
        selected_paths: Array.from(selectedPaths),
      }),
    });
    if (data.feedback_recorded) showToast("Noted — this helps tune duplicate detection");
  } catch (e) {
    toastError("save the override", e);
  }
  const label =
    newMode === "include" ? "Included" : newMode === "exclude" ? "Excluded" : "Override cleared";
  showToast(label, 4000, () => {
    if (prevMode) overrides[filepath] = prevMode;
    else delete overrides[filepath];
    saveOverrides();
    scheduleRecompute();
    apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filepath,
        mode: prevMode,
        selected_paths: Array.from(selectedPaths),
      }),
    }).catch((e) => console.warn("Override sync failed:", e));
  });
}

export async function clearOverrides() {
  /** @type {any} */
  const win = window;
  const overrides = /** @type {Record<string, string>} */ (win.overrides || {});
  const fps = Object.keys(overrides);
  if (!fps.length) return;
  win.overrides = {};
  saveOverrides();
  scheduleRecompute();
  try {
    await apiFetch("/api/v1/batch/override", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepaths: fps, mode: null }),
    });
  } catch (e) {
    toastError("clear the overrides", e);
  }
}

/**
 * @param {MouseEvent} event
 * @param {number} idx
 */

/**
 * @param {string | null} mode
 */

import {
  batchAddToAlbum,
  batchFavorite,
  batchOverride,
  createAlbumAndAdd,
  hideAlbumPicker,
  showAlbumPickerModal,
  updateOverrideStats,
} from "./photos-batch.mjs";
export {
  batchAddToAlbum,
  batchFavorite,
  batchOverride,
  createAlbumAndAdd,
  hideAlbumPicker,
  showAlbumPickerModal,
  updateOverrideStats,
};




// Re-export the deleteFromCard reference so it's still accessible via window
// for inline `onclick` handlers in renderCardHTML.
export { deleteFromCard, toggleFavorite };

import { vgrid } from "./photos-vgrid.mjs";
export { vgrid };

import {
  clearMultiSelect,
  handleCardClick,
  updateMultiSelectUI,
} from "./photos-select.mjs";
export {
  clearMultiSelect,
  handleCardClick,
  updateMultiSelectUI,
};

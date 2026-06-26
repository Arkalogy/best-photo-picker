// @ts-check
/**
 * Top-bar state machine: popovers (sort/filter), toolbar icon init,
 * K-spinner sync between toolbar and settings, library stats, view-aware
 * toolbar visibility, Show-Picks chip, favorite toggle.
 *
 * Self-attaches a document `click` listener on import to dismiss
 * popovers on outside clicks.
 *
 * Reads many shared globals via `window` (`currentAlbumId`, `albumList`,
 * `currentView`, `photos`, `favorites`, `selectedPaths`, `mergeSourceId`,
 * `ICONS`) and calls cross-file helpers (`renderGrid`, `renderAlbumNav`,
 * `updateCardInPlace`, `closeMergePicker`, `startDupeReview`,
 * `updateOverrideStats`, `scheduleRecompute`, `showToast`) the same way —
 * they all still live in classic land.
 */

import { saveCurrentScopeFilter } from "./albums.mjs";
import { apiFetch } from "./api-client.mjs";
import { _formatBytes } from "./format-helpers.mjs";
import { toast, toastError } from "./toast.mjs";

/** @type {HTMLElement | null} */
let activePopover = null;

/**
 * Open or close the named popover (`sort` / `filter`). Closes any
 * currently-open popover and positions the opening one against its
 * trigger button.
 *
 * @param {string} type
 */
export function toggleToolbarPopover(type) {
  const btn = document.getElementById("btn-" + type);
  const popover = /** @type {HTMLElement | null} */ (document.getElementById(type + "-popover"));
  if (!popover || !btn) return;
  if (activePopover && activePopover !== popover) {
    activePopover.classList.remove("open");
  }
  const wasOpen = popover.classList.contains("open");
  popover.classList.toggle("open");
  activePopover = !wasOpen ? popover : null;
  if (!wasOpen) {
    const r = btn.getBoundingClientRect();
    popover.style.top = r.bottom + 6 + "px";
    popover.style.right = window.innerWidth - r.right + "px";
  }
  if (type === "sort") {
    const sortBy = /** @type {HTMLInputElement | null} */ (document.getElementById("sort-by"));
    if (sortBy) {
      popover.querySelectorAll(".popover-option").forEach((el) => {
        el.classList.toggle("active", /** @type {HTMLElement} */ (el).dataset.sort === sortBy.value);
      });
    }
  } else if (type === "filter") {
    const filterBy = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
    if (filterBy) {
      popover.querySelectorAll(".popover-option").forEach((el) => {
        el.classList.toggle(
          "active",
          /** @type {HTMLElement} */ (el).dataset.filter === filterBy.value
        );
      });
    }
  }
}

/**
 * @param {string} value
 */
export function setSortFromPopover(value) {
  /** @type {any} */
  const win = window;
  const el = /** @type {HTMLInputElement | null} */ (document.getElementById("sort-by"));
  if (el) el.value = value;
  win.renderGrid?.();
  closePopovers();
}

/**
 * @param {string} value
 */
export function setFilterFromPopover(value) {
  /** @type {any} */
  const win = window;
  const el = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
  if (el) {
    el.value = value;
    saveCurrentScopeFilter();
  }
  win.renderGrid?.();
  closePopovers();
  const btn = document.getElementById("btn-filter");
  if (btn) btn.classList.toggle("has-active-filter", value !== "all");
}

export function closePopovers() {
  document.querySelectorAll(".toolbar-popover.open").forEach((el) => el.classList.remove("open"));
  activePopover = null;
}

document.addEventListener("click", (e) => {
  const target = /** @type {HTMLElement | null} */ (e.target);
  if (activePopover && target && !target.closest(".toolbar-popover-anchor")) {
    closePopovers();
  }
});

export function initToolbarIcons() {
  /** @type {any} */
  const win = window;
  const ICONS = win.ICONS || {};
  /** @type {Record<string, string>} */
  const map = {
    "btn-search-toolbar": ICONS.search,
    "btn-sort": ICONS.sort,
    "btn-filter": ICONS.filter,
    "btn-import-toolbar": ICONS.importArrow,
    "btn-analyze-toolbar": ICONS.analyze,
    "btn-export": ICONS.exportArrow,
    "btn-slideshow": ICONS.ssSlideshow,
    "btn-settings-toolbar": ICONS.more,
  };
  for (const [id, svg] of Object.entries(map)) {
    const el = document.getElementById(id);
    if (el && svg) el.innerHTML = svg;
  }
  const playBtn = document.getElementById("ss-play-btn");
  if (playBtn && ICONS.ssPause) playBtn.innerHTML = ICONS.ssPause;
  const shuffleBtn = document.getElementById("ss-shuffle-btn");
  if (shuffleBtn && ICONS.ssShuffle) shuffleBtn.innerHTML = ICONS.ssShuffle;
  const kbBtn = document.getElementById("ss-kb-btn");
  if (kbBtn && ICONS.ssKenBurns) kbBtn.innerHTML = ICONS.ssKenBurns;
  const infoBtn = document.getElementById("ss-info-btn");
  if (infoBtn && ICONS.ssInfo) infoBtn.innerHTML = ICONS.ssInfo;
}

/**
 * @param {string | number} val
 */
export function syncKFromToolbar(val) {
  /** @type {any} */
  const win = window;
  const n = parseInt(String(val), 10);
  if (!n || n < 1) return;
  const settings = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
  if (settings) settings.value = String(n);
  // Optimistic update: show new k immediately; recompute will correct if overrides change the real count.
  const picksBtn = /** @type {HTMLElement | null} */ (document.getElementById("toolbar-show-picks"));
  if (picksBtn && picksBtn.style.display !== "none") {
    const filterEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
    const isActive = !!(filterEl && filterEl.value === "selected");
    picksBtn.textContent = isActive ? "All Photos" : `BPP Picks (${n})`;
  }
  win.scheduleRecompute?.();
  if (win.currentAlbumId) {
    const albums = /** @type {any[]} */ (win.albumList || []);
    const album = albums.find((a) => a.id === win.currentAlbumId);
    if (album && album.album_type !== "all") {
      const prevK = album.k;
      const prevKUserSet = album.config?.k_user_set;
      album.k = n;
      if (!album.config) album.config = {};
      album.config.k_user_set = true;
      apiFetch(`/api/v1/albums/${win.currentAlbumId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ k: n, config: { k_user_set: true } }),
      }).catch((e) => {
        console.warn("Album k update failed:", e);
        album.k = prevK;
        if (album.config) album.config.k_user_set = prevKUserSet;
        toastError("save the pick count", e);
      });
    }
  }
}

export function syncToolbarK() {
  const src = /** @type {HTMLInputElement | null} */ (document.getElementById("param-k"));
  const dest = /** @type {HTMLInputElement | null} */ (document.getElementById("toolbar-k"));
  if (src && dest) dest.value = src.value;
}

/**
 * Keyboard handler for the Pick count input. Recomputes IMMEDIATELY
 * on arrow keys (up/down) so the spinner-key path matches the spinner-
 * button path. Enter blurs the field, which fires `change` and commits
 * the typed value. Plain digit/backspace keys fall through to the
 * native input — `change` will commit them on blur, so we don't kick
 * off a recompute on every keystroke (a 4-digit edit "1500" would
 * otherwise queue four expensive recomputes for transient values
 * "1", "15", "150", "1500").
 *
 * @param {KeyboardEvent} e
 */
export function onToolbarKKeydown(e) {
  const input = /** @type {HTMLInputElement} */ (e.currentTarget || e.target);
  if (e.key === "ArrowUp" || e.key === "ArrowDown") {
    // Let the native step happen first, then read the new value.
    // requestAnimationFrame ensures we read post-update.
    requestAnimationFrame(() => syncKFromToolbar(input.value));
    return;
  }
  if (e.key === "Enter") {
    e.preventDefault();
    input.blur(); // triggers `change` → syncKFromToolbar
    return;
  }
}

/** Favorites are persisted via API now — kept for callers that haven't been updated. */
export function saveFavorites() {
  /* no-op */
}

export function updateLibStats() {
  /** @type {any} */
  const win = window;
  const el = document.getElementById("lib-stats");
  if (!el) return;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const photos = /** @type {any[]} */ (win.photos || []);
  const allAlbum = albums.find((a) => a.album_type === "all");
  const count = allAlbum ? allAlbum.photo_count || 0 : photos.length;
  el.textContent = count > 0 ? `${count.toLocaleString()} photos` : "";
  apiFetch("/api/v1/stats")
    .then((stats) => {
      if (!stats || !stats.total_count) return;
      const parts = [`${stats.total_count.toLocaleString()} items`];
      if (stats.total_size > 0) parts.push(_formatBytes(stats.total_size));
      const sub = [];
      if (stats.photo_count) sub.push(`${stats.photo_count} photos`);
      if (stats.video_count) sub.push(`${stats.video_count} videos`);
      if (stats.raw_count) sub.push(`${stats.raw_count} RAW`);
      el.innerHTML =
        `<div>${parts.join(" · ")}</div>` +
        (sub.length ? `<div style="margin-top:2px">${sub.join(" · ")}</div>` : "");
    })
    .catch((e) => console.warn("Stats fetch failed:", e));
}

export function updateToolbarForView() {
  /** @type {any} */
  const win = window;
  const zoomCtrl = /** @type {HTMLElement | null} */ (document.getElementById("zoom-control"));
  const sortAnchor = /** @type {HTMLElement | null} */ (document.getElementById("sort-anchor"));
  const filterAnchor = /** @type {HTMLElement | null} */ (
    document.getElementById("filter-anchor")
  );
  const exportBtn = /** @type {HTMLElement | null} */ (document.getElementById("btn-export"));
  const analyzeBtn = /** @type {HTMLElement | null} */ (
    document.getElementById("btn-analyze-toolbar")
  );
  const slideshowBtn = /** @type {HTMLElement | null} */ (
    document.getElementById("btn-slideshow")
  );
  const pickCtrl = /** @type {HTMLElement | null} */ (document.getElementById("toolbar-pick"));

  const nonGridViews = ["people", "pets", "groups", "map", "calendar", "deleted"];
  const currentView = win.currentView;
  const currentAlbumId = win.currentAlbumId;
  const albums = /** @type {any[]} */ (win.albumList || []);

  if (nonGridViews.includes(currentView)) {
    if (zoomCtrl) zoomCtrl.style.display = "none";
    if (sortAnchor) sortAnchor.style.display = "none";
    if (filterAnchor) filterAnchor.style.display = "none";
    if (exportBtn) exportBtn.style.display = "none";
    if (analyzeBtn) analyzeBtn.style.display = "flex";
    if (slideshowBtn) slideshowBtn.style.display = "none";
    if (pickCtrl) pickCtrl.style.display = "none";
  } else {
    if (zoomCtrl) zoomCtrl.style.display = "flex";
    if (sortAnchor) sortAnchor.style.display = "inline-flex";
    if (filterAnchor) filterAnchor.style.display = "inline-flex";
    if (exportBtn) exportBtn.style.display = "flex";
    if (analyzeBtn) analyzeBtn.style.display = "flex";
    if (slideshowBtn) slideshowBtn.style.display = "flex";
    const showPicksBtn = /** @type {HTMLElement | null} */ (
      document.getElementById("toolbar-show-picks")
    );
    if (pickCtrl) {
      const isPicks = currentView === "picks";
      const isAlbumView = currentView === "album" && currentAlbumId;
      const album = isAlbumView ? albums.find((a) => a.id === currentAlbumId) : null;
      pickCtrl.style.display = "flex";
      if (showPicksBtn) showPicksBtn.style.display = "inline";
      updatePickScope(isPicks ? null : album);
      updateShowPicksChip();
    }
    if (typeof win.mergeSourceId !== "undefined" && win.mergeSourceId !== null) {
      win.closeMergePicker?.();
    }

    let dupeBtn = /** @type {HTMLElement | null} */ (document.getElementById("btn-review-dupes"));
    if (!dupeBtn) {
      dupeBtn = document.createElement("button");
      dupeBtn.id = "btn-review-dupes";
      dupeBtn.className = "people-review-btn";
      dupeBtn.textContent = "Review Duplicates";
      /** @type {HTMLButtonElement} */ (dupeBtn).onclick = () => {
        if (typeof win.startDupeReview === "function") win.startDupeReview();
      };
      const toolbar = document.querySelector(".toolbar-right");
      if (toolbar) toolbar.appendChild(dupeBtn);
    }
    const isDupeAlbum = currentView === "album" && currentAlbumId;
    const dupeAlbum = isDupeAlbum ? albums.find((a) => a.id === currentAlbumId) : null;
    dupeBtn.style.display =
      dupeAlbum && dupeAlbum.album_type === "smart_duplicates" ? "inline-flex" : "none";
  }
}

/**
 * @param {{name: string, album_type: string} | null} album
 */
export function updatePickScope(album) {
  const scope = document.getElementById("toolbar-pick-scope");
  if (!scope) return;
  if (album && album.album_type !== "all") {
    const name = album.name.length > 20 ? album.name.slice(0, 18) + "…" : album.name;
    scope.textContent = "from " + name;
    /** @type {HTMLElement} */ (scope).title = "Pick from: " + album.name;
  } else {
    scope.textContent = "";
    /** @type {HTMLElement} */ (scope).title = "";
  }
}

export function toggleShowPicks() {
  /** @type {any} */
  const win = window;
  const filterEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
  if (!filterEl) return;
  const isActive = filterEl.value === "selected";
  filterEl.value = isActive ? "all" : "selected";
  // Persist the per-album filter so this choice survives a refresh AND
  // round-trips through other albums. Without this, the BPP Picks
  // chip would silently reset to "all" every time the user navigated
  // away — which was the bug.
  saveCurrentScopeFilter();
  win.renderGrid?.();
  updateShowPicksChip();
}

export function updateShowPicksChip() {
  /** @type {any} */
  const win = window;
  const btn = document.getElementById("toolbar-show-picks");
  if (!btn) return;
  const filterEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
  const isActive = !!(filterEl && filterEl.value === "selected");
  btn.classList.toggle("active", isActive);
  // Count picks WITHIN the current view (album-scoped) — not library-wide.
  // Otherwise an album with 24 photos shows "BPP Picks (50)" when the user
  // has 50 picks across the library. See countSelectedInScope() for the
  // intersection.
  const selected = /** @type {Set<string> | undefined} */ (win.selectedPaths);
  const photos = /** @type {any[] | undefined} */ (win.photos) || [];
  let count = 0;
  if (selected && selected.size > 0) {
    for (const p of photos) {
      if (p.deleted_at) continue;
      if (p.filepath && selected.has(p.filepath)) count++;
    }
  }
  btn.textContent = isActive ? `All Photos` : `BPP Picks${count ? " (" + count + ")" : ""}`;
}

/**
 * @param {string} filepath
 */
export async function toggleFavorite(filepath) {
  /** @type {any} */
  const win = window;
  const favorites = /** @type {Set<string>} */ (win.favorites || new Set());
  const wasFav = favorites.has(filepath);
  if (wasFav) favorites.delete(filepath);
  else favorites.add(filepath);
  if (!win.updateCardInPlace?.(filepath)) win.renderGrid?.();
  win.renderAlbumNav?.();
  const url = "/api/v1/favorite";
  try {
    await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepath }),
    });
  } catch (e) {
    // Revert the optimistic toggle — the server didn't persist it — and
    // bail before the success toast/undo so we don't claim success on a
    // failed save.
    if (wasFav) favorites.add(filepath);
    else favorites.delete(filepath);
    if (!win.updateCardInPlace?.(filepath)) win.renderGrid?.();
    win.renderAlbumNav?.();
    toastError("favorite this photo", e);
    return;
  }
  const label = wasFav ? "Unfavorited" : "Favorited";
  win.showToast?.(label, 4000, () => {
    if (wasFav) favorites.add(filepath);
    else favorites.delete(filepath);
    if (!win.updateCardInPlace?.(filepath)) win.renderGrid?.();
    win.renderAlbumNav?.();
    apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepath }),
    }).catch((e) => console.warn("Favorite toggle failed:", e));
  });
}

/** Overrides are persisted via API now — kept for callers that haven't been updated. */
export function saveOverrides() {
  /** @type {any} */
  const win = window;
  win.updateOverrideStats?.();
}

/** Test-only: reset module-private state. */
export function _resetToolbarState() {
  if (activePopover) activePopover.classList.remove("open");
  activePopover = null;
}

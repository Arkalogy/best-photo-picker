// @ts-check
/**
 * Status-bar UI: progress bar, "Analyzing", summary text, right-side
 * stats badge, tuning/empty/preview transitions.
 *
 * Extracted from analysis.mjs during the v0.1 cleanup.
 *
 *   * showStatusProgress / hideStatusProgress / showStatusAnalyzing
 *   * updateStatusSummary / _refreshStatusRight
 *   * _navigateToSmartAlbum + _statusLink helper
 *   * _formatSize / _formatDateRange
 *   * showPreviewGallery / showTuningState / showEmptyLibrary
 *
 * Re-exported from analysis.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { escapeAttr } from "./text-format.mjs";
import { MONTHS_SHORT, formatDate } from "./date-format.mjs";
import { loadAlbumList } from "./albums.mjs";
import { switchAlbum } from "./albums-switch.mjs";
import { loadPresetList } from "./presets.mjs";
import { updateLibStats } from "./toolbar.mjs";
import { showSkeletonGrid } from "./analysis-recompute.mjs";


let _lastStatsAlbumId = null;
let _lastStatsHTML = "";


export function showStatusProgress(text, pct) {
  /** @type {any} */
  const win = window;
  win.show?.("status-bar");
  const center = document.getElementById("status-progress");
  if (center) center.classList.remove("hidden");
  const textEl = document.getElementById("status-progress-text");
  if (textEl) textEl.textContent = text;
  const fill = /** @type {HTMLElement | null} */ (
    document.getElementById("status-progress-fill")
  );
  if (fill) fill.style.width = pct + "%";
}

export function hideStatusProgress() {
  const center = document.getElementById("status-progress");
  if (center) center.classList.add("hidden");
  const fill = /** @type {HTMLElement | null} */ (
    document.getElementById("status-progress-fill")
  );
  if (fill) fill.style.width = "0%";
}

/**
 * @param {boolean} show
 */
export function showStatusAnalyzing(show) {
  const right = document.getElementById("status-right");
  if (!right) return;
  if (show) {
    right.innerHTML =
      '<span class="analyzing-dot"></span><span class="analyzing-text">Analyzing</span>';
  } else {
    right.innerHTML = "";
  }
}

/**
 * @param {string} text
 * @param {string} action
 * @param {string} [arg0]
 */
function _statusLink(text, action, arg0) {
  const argAttr = arg0 !== undefined ? ` data-arg0="${escapeAttr(arg0)}"` : "";
  return `<a class="status-link" data-action="${escapeAttr(action)}"${argAttr}>${text}</a>`;
}

/**
 * @param {string} type
 */
export function _navigateToSmartAlbum(type) {
  /** @type {any} */
  const win = window;
  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = albums.find((a) => a.album_type === type);
  if (album) win.switchAlbum?.(album.id);
}

/**
 * @param {any} s
 */
export function updateStatusSummary(s) {
  /** @type {any} */
  const win = window;
  const summary = document.getElementById("status-summary");
  if (!summary) return;

  /** @param {string} d */
  const _si = (d) =>
    `<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" style="width:12px;height:12px;vertical-align:-1px;margin-right:2px">${d}</svg>`;
  const imgIcon = _si(
    '<rect x="1.5" y="2.5" width="13" height="11" rx="1.5"/><circle cx="5.5" cy="6" r="1.2"/><path d="M1.5 11l3.5-3.5 2 2 3-3.5 4.5 5"/>'
  );
  const parts = [
    _statusLink(imgIcon + `${s.total.toLocaleString()} photos`, "switchToLibrary"),
  ];
  if (s.after_dedupe !== undefined) {
    parts.push(
      _statusLink(
        `${s.after_dedupe.toLocaleString()} unique`,
        "_navigateToSmartAlbum",
        "smart_duplicates"
      )
    );
  }
  if (s.total_selected) {
    parts.push(
      _statusLink(
        `<strong>${s.total_selected}</strong> selected`,
        "_navigateToSmartAlbum",
        "smart_score"
      )
    );
  }
  const dedupRemoved = (s.after_exclude || 0) - (s.after_dedupe || 0);
  if (dedupRemoved > 0) parts.push(`${dedupRemoved} dupes removed`);
  const favCount = /** @type {Set<string>} */ (win.favorites || new Set()).size;
  if (favCount > 0) {
    parts.push(
      _si(
        '<path d="M8 13.7l-5.7-5.2A3.1 3.1 0 012 6.2C2 4.43 3.43 3 5.2 3c1.1 0 2.1.56 2.8 1.44A3.18 3.18 0 0110.8 3C12.57 3 14 4.43 14 6.2c0 .86-.35 1.64-.92 2.2L8 13.7z"/>'
      ) + `${favCount} fav`
    );
  }
  const ovCount = Object.keys(/** @type {Record<string, string>} */ (win.overrides || {})).length;
  if (ovCount > 0) parts.push(`${ovCount} overridden`);
  const sortEl = /** @type {HTMLInputElement | null} */ (document.getElementById("sort-by"));
  const filterEl = /** @type {HTMLInputElement | null} */ (document.getElementById("filter-by"));
  const sortVal = sortEl ? sortEl.value : "date";
  const filterVal = filterEl ? filterEl.value : "all";
  if (sortVal && sortVal !== "date") {
    /** @type {Record<string, string>} */
    const sortLabels = {
      score: "Score",
      name: "Name",
      size: "Size",
      "date-asc": "Oldest",
    };
    parts.push(
      _si('<path d="M5 3v10M3 5l2-2 2 2M11 13V3M9 11l2 2 2-2"/>') + (sortLabels[sortVal] || sortVal)
    );
  }
  if (filterVal && filterVal !== "all") {
    /** @type {Record<string, string>} */
    const filterLabels = {
      selected: "Selected",
      "not-selected": "Not selected",
      favorites: "Favorites",
      overridden: "Overridden",
      "photos-only": "Photos",
      "videos-only": "Videos",
      "raw-only": "RAW",
      deleted: "Deleted",
    };
    parts.push(
      _si('<path d="M2 3h12M4 6.5h8M6 10h4M7 13.5h2"/>') + (filterLabels[filterVal] || filterVal)
    );
  }

  summary.innerHTML = parts.join(" &nbsp;·&nbsp; ");

  _refreshStatusRight();
}

/**
 * @param {number | null | undefined} bytes
 */
function _formatSize(bytes) {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return bytes + " B";
  if (bytes < 1048576) return (bytes / 1024).toFixed(0) + " KB";
  if (bytes < 1073741824) return (bytes / 1048576).toFixed(1) + " MB";
  return (bytes / 1073741824).toFixed(1) + " GB";
}

/**
 * @param {string | null | undefined} minDate
 * @param {string | null | undefined} maxDate
 */
function _formatDateRange(minDate, maxDate) {
  if (!minDate) return "";
  const m1 = formatDate(minDate);
  const m2 = formatDate(maxDate || "");
  if (m1 === m2) return m1;
  const y1 = minDate.slice(0, 4);
  const y2 = maxDate ? maxDate.slice(0, 4) : y1;
  if (y1 === y2 && maxDate) {
    const mo1 = parseInt(minDate.slice(5, 7), 10) - 1;
    const mo2 = parseInt(maxDate.slice(5, 7), 10) - 1;
    if (mo1 === mo2) return `${MONTHS_SHORT[mo1]} ${y1}`;
    return `${MONTHS_SHORT[mo1]} – ${MONTHS_SHORT[mo2]} ${y1}`;
  }
  return `${m1} – ${m2}`;
}

export async function _refreshStatusRight() {
  /** @type {any} */
  const win = window;
  const right = document.getElementById("status-right");
  if (!right || right.querySelector(".analyzing-dot")) return;

  if (!win.currentAlbumId) {
    right.innerHTML = "";
    return;
  }

  const albums = /** @type {any[]} */ (win.albumList || []);
  const album = albums.find((a) => a.id === win.currentAlbumId);
  if (_lastStatsAlbumId !== win.currentAlbumId) {
    right.textContent = album ? (album.album_type !== "all" ? album.name : "") : "";
  }

  try {
    const data = await apiFetch(`/api/v1/albums/${win.currentAlbumId}/stats`);
    if (win.currentAlbumId !== (album && album.id)) return; // stale
    _lastStatsAlbumId = win.currentAlbumId;

    /** @param {string} d @param {string} vb */
    const _si = (d, vb) =>
      `<svg viewBox="${vb}" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" style="width:12px;height:12px;vertical-align:-1px;margin-right:2px">${d}</svg>`;
    const parts = [];
    /** @param {string} html @param {string} tip */
    const _w = (html, tip) => `<span title="${escapeAttr(tip)}">${html}</span>`;
    const dateRange = _formatDateRange(data.date_min, data.date_max);
    if (dateRange)
      parts.push(
        _w(
          _si(
            '<rect x="2" y="3" width="12" height="11" rx="1.5"/><line x1="2" y1="7" x2="14" y2="7"/><line x1="5" y1="1.5" x2="5" y2="4.5"/><line x1="11" y1="1.5" x2="11" y2="4.5"/>',
            "0 0 16 16"
          ) + dateRange,
          "Date range of photos in this album"
        )
      );
    if (data.disk_size)
      parts.push(
        _w(
          _si(
            '<path d="M4 2h8l2 4v7a1 1 0 01-1 1H3a1 1 0 01-1-1V6l2-4z"/><path d="M2 6h12"/>',
            "0 0 16 16"
          ) + _formatSize(data.disk_size),
          "Total disk size of photos in this album"
        )
      );
    if (data.avg_score != null)
      parts.push(
        _w(
          _si(
            '<path d="M8 1.5l1.85 4.1L14.2 6l-3.1 2.85.85 4.15L8 10.75 4.05 13l.85-4.15L1.8 6l4.35-.4L8 1.5z"/>',
            "0 0 16 16"
          ) + `${(data.avg_score * 100).toFixed(0)}%`,
          "Average quality score (sharpness, exposure, faces, composition)"
        )
      );
    if (data.people_count > 0)
      parts.push(
        _w(
          _si(
            '<circle cx="8" cy="5" r="2.5"/><path d="M3 14c0-2.76 2.24-5 5-5s5 2.24 5 5"/>',
            "0 0 16 16"
          ) + `${data.people_count} people`,
          "Number of recognized people in this album"
        )
      );
    if (data.gps_count > 0 && data.gps_count < data.total)
      parts.push(
        _w(
          _si(
            '<path d="M8 1.5C5.5 1.5 3.5 3.5 3.5 6c0 3.5 4.5 8.5 4.5 8.5s4.5-5 4.5-8.5c0-2.5-2-4.5-4.5-4.5z"/><circle cx="8" cy="6" r="1.5"/>',
            "0 0 16 16"
          ) + `${data.gps_count} with GPS`,
          "Photos with GPS location data"
        )
      );
    _lastStatsHTML = parts.join(" &nbsp;·&nbsp; ");
    right.innerHTML = _lastStatsHTML;
  } catch {
    /* keep existing label */
  }
}

/**
 * @param {number} [importedCount]
 */
export async function showPreviewGallery(importedCount) {
  /** @type {any} */
  const win = window;
  win.hide?.("empty-state");
  win.show?.("album-nav");
  win.show?.("toolbar");
  win.show?.("photo-grid");
  win.show?.("status-bar");
  win.show?.("analyzing-banner");
  showStatusAnalyzing(true);
  document.querySelector("main")?.classList.add("analyzing-pending");
  showSkeletonGrid(importedCount || 12);

  const data = await apiFetch("/api/v1/photos/preview");
  if (data.photos && data.photos.length > 0) {
    win.photos = data.photos;
    win.selectedPaths = new Set();
    win.renderGrid?.();
  }
  win.loadAlbumList?.();
}

export function showTuningState() {
  /** @type {any} */
  const win = window;
  win.hide?.("empty-state");
  win.hide?.("analyzing-banner");
  document.querySelector("main")?.classList.remove("analyzing-pending");
  win.show?.("toolbar");
  win.show?.("photo-grid");
  win.show?.("album-nav");
  win.show?.("status-bar");
  hideStatusProgress();
  showStatusAnalyzing(false);
  loadPresetList();
  win.loadAlbumList?.();
  updateLibStats();
}

export function showEmptyLibrary() {
  /** @type {any} */
  const win = window;
  win.hide?.("toolbar");
  win.hide?.("photo-grid");
  win.hide?.("analyzing-banner");
  win.hide?.("status-bar");
  document.querySelector("main")?.classList.remove("analyzing-pending");
  win.show?.("empty-state");
  win.show?.("album-nav");
  win.loadAlbumList?.();
  updateLibStats();
}


// @ts-check
/**
 * Date timeline bar — month-by-month vertical bars in the toolbar
 * row, click a bar to filter the grid to that month.
 *
 * Reads the global `photos` array (still classic state) and calls
 * `renderGrid()` (still classic) when the filter changes. Both are
 * accessed via `window.*` so this module compiles without those
 * classic-side dependencies needing module migration first.
 *
 * Bridged onto window — `buildTimeline`, `applyTimelineFilter`,
 * `clearTimelineFilter` are referenced from app.js bootstrap and
 * inline `` in the rendered segment HTML.
 */

import { MONTHS_FULL, MONTHS_SHORT } from "./date-format.mjs";
import { escapeAttr } from "./text-format.mjs";

/** @type {string | null} Active month filter, e.g. "2024-01". */
let timelineFilter = null;

/**
 * @typedef {Object} TimelinePhoto
 * @property {string | null | undefined} [date_month] - "YYYY-MM"
 * @property {string | null} [deleted_at]
 */

/**
 * Convert a "YYYY-MM" string to the full label used in the active chip
 * and the segment tooltip — e.g. "January 2024".
 *
 * @param {string} m
 * @returns {string}
 */
export function _tlLabel(m) {
  const parts = m.split("-");
  return MONTHS_FULL[parseInt(parts[1], 10) - 1] + " " + parts[0];
}

/**
 * Convert a "YYYY-MM" string to the short label rendered under each
 * segment — e.g. "Jan ’24".
 *
 * @param {string} m
 * @returns {string}
 */
export function _tlShort(m) {
  const parts = m.split("-");
  return MONTHS_SHORT[parseInt(parts[1], 10) - 1] + " ’" + parts[0].slice(2);
}

/** @returns {string | null} */
export function _getTimelineFilter() {
  return timelineFilter;
}

/** @param {string | null} m */
export function _setTimelineFilter(m) {
  timelineFilter = m;
}

/**
 * Render the month-by-month timeline bar for the current photo set.
 *
 * No-ops when the bar container is missing, the photo list is empty,
 * or fewer than two months are represented (a single-month timeline
 * is just clutter).
 */
export function buildTimeline() {
  const bar = document.getElementById("timeline-bar");
  if (!bar) return;

  /** @type {TimelinePhoto[]} */
  const all = /** @type {any} */ (window).photos || [];
  const items = all.filter((p) => !p.deleted_at);
  if (items.length === 0) {
    bar.classList.add("hidden");
    return;
  }

  /** @type {Record<string, number>} */
  const counts = {};
  for (const p of items) {
    const m = p.date_month;
    if (m) counts[m] = (counts[m] || 0) + 1;
  }

  const months = Object.keys(counts).sort();
  if (months.length < 2) {
    bar.classList.add("hidden");
    return;
  }

  bar.classList.remove("hidden");
  let maxCount = 0;
  for (const m of months) {
    if (counts[m] > maxCount) maxCount = counts[m];
  }

  // Decimate labels when the timeline spans many months — labels
  // collide if every month gets one. Show every Nth month so total
  // visible labels stays around 12. Tooltip on each bar still shows
  // the full month so per-month detail isn't lost.
  const labelStride = Math.max(1, Math.ceil(months.length / 12));

  let html = '<div class="tl-segments">';
  months.forEach((mo, i) => {
    const active = timelineFilter === mo ? " active" : "";
    const h = Math.max(6, Math.round((counts[mo] / maxCount) * 28));
    const label = _tlLabel(mo);
    const showLabel = i % labelStride === 0;
    html +=
      '<div class="tl-seg' +
      active +
      '" data-month="' +
      mo +
      '"' +
      ' data-action="applyTimelineFilter" data-arg0="' +
      mo +
      '"' +
      ' title="' +
      escapeAttr(label) +
      ": " +
      counts[mo] +
      ' photos">' +
      '<div class="tl-seg-bar" style="height:' +
      h +
      'px"></div>' +
      (showLabel
        ? '<div class="tl-seg-label">' + _tlShort(mo) + "</div>"
        : '<div class="tl-seg-label tl-seg-label-empty"></div>') +
      "</div>";
  });
  html += "</div>";

  if (timelineFilter) {
    const ct = counts[timelineFilter] || 0;
    html +=
      '<div class="tl-active-chip" data-action="clearTimelineFilter">' +
      _tlLabel(timelineFilter) +
      " (" +
      ct +
      ') <span class="tl-clear">&times;</span></div>';
  }

  bar.innerHTML = html;
}

/**
 * Toggle the timeline filter for `month`. Clicking the same month
 * twice clears the filter; clicking a different month replaces it.
 *
 * @param {string} month
 */
export function applyTimelineFilter(month) {
  timelineFilter = timelineFilter === month ? null : month;
  /** @type {any} */ (window).renderGrid?.();
}

export function clearTimelineFilter() {
  timelineFilter = null;
  /** @type {any} */ (window).renderGrid?.();
}

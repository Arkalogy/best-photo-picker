// @ts-check
/**
 * "On This Day" sidebar — fans of past-year cards for today's date,
 * each clickable to load that day's photos as the grid view.
 *
 * Pulls from `/api/v1/on-this-day`. Reads state.photos / currentGridItems
 * / currentView / currentViewId and calls window.updateToolbarTitle /
 * updateBreadcrumbs / saveNavState — all still classic state.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { viewFetch } from "./view-guard.mjs";
import { state } from "./state.mjs";
import { toast, toastError } from "./toast.mjs";
import { esc } from "./text-format.mjs";
import { renderGridFromItems } from "./memories.mjs";

/**
 * @typedef {Object} OnThisDayPhoto
 * @property {number | null | undefined} [id]
 * @property {string} [filepath]
 * @property {string | null} [hash]
 * @property {string | null} [date]
 * @property {number | null} [score]
 * @property {string | null} [filename]
 */

/**
 * @typedef {Object} OnThisDayYear
 * @property {number} year
 * @property {number} years_ago
 * @property {number} count
 * @property {string | null | undefined} [hero_hash]
 * @property {OnThisDayPhoto[]} [photos]
 */

/**
 * @typedef {Object} OnThisDayData
 * @property {number} month
 * @property {number} day
 * @property {OnThisDayYear[]} years
 */

/** @type {OnThisDayData | null} */
let onThisDayData = null;

/** @returns {OnThisDayData | null} */
export function _getOnThisDayData() {
  return onThisDayData;
}

/** @param {OnThisDayData | null} d */
export function _setOnThisDayData(d) {
  onThisDayData = d;
}

/** Load the data and render the sidebar. Silently empties on failure. */
export async function loadOnThisDay() {
  try {
    onThisDayData = await apiFetch("/api/v1/on-this-day");
    renderOnThisDay();
  } catch {
    onThisDayData = null;
  }
}

/**
 * Render fans of "X year(s) ago" cards into `#on-this-day`. Hides the
 * container when there's nothing to show.
 */
export function renderOnThisDay() {
  const container = document.getElementById("on-this-day");
  if (!container) return;

  if (!onThisDayData || !onThisDayData.years || onThisDayData.years.length === 0) {
    container.innerHTML = "";
    container.classList.add("hidden");
    return;
  }

  container.classList.remove("hidden");
  const m = onThisDayData.month;
  const d = onThisDayData.day;
  const dateLabel = new Date(2000, m - 1, d).toLocaleDateString(undefined, {
    month: "long",
    day: "numeric",
  });

  const cards = onThisDayData.years
    .map((yr) => {
      const photos = yr.photos || [];
      const fanHtml = photos
        .slice(1, 3)
        .map((p, i) =>
          p.hash
            ? `<div class="otd-fan otd-fan-${i + 1}" style="background-image:url(${authedSrc("/thumb/" + p.hash)})"></div>`
            : "",
        )
        .join("");
      return `<div class="otd-card" data-action="openOnThisDayYear" data-arg0="${yr.year}" data-arg1="${m}" data-arg2="${d}" title="${yr.years_ago} year${yr.years_ago !== 1 ? "s" : ""} ago">
      ${fanHtml}
      <div class="otd-card-main">
        <div class="memory-card-bg"${yr.hero_hash ? ` style="background-image:url(${authedSrc("/thumb/" + yr.hero_hash)})"` : ""}></div>
        <div class="memory-card-overlay">
          <div class="memory-card-title">${yr.year}</div>
          <div class="memory-card-meta">${yr.years_ago} year${yr.years_ago !== 1 ? "s" : ""} ago &middot; ${yr.count} photo${yr.count !== 1 ? "s" : ""}</div>
        </div>
      </div>
    </div>`;
    })
    .join("");

  container.innerHTML = `
    <div class="memories-header">
      <span class="memories-label">On This Day &middot; ${esc(dateLabel)}</span>
    </div>
    <div class="memories-scroll">${cards}</div>
  `;
}

/**
 * Load that year's photos for the given (month, day) and switch the
 * grid view to show them.
 *
 * @param {number} year
 * @param {number} month
 * @param {number} day
 */
export async function openOnThisDayYear(year, month, day) {
  try {
    const data = await viewFetch(`/api/v1/on-this-day?month=${month}&day=${day}`);
    if (!data) return; // view changed mid-fetch — don't hijack the new view
    if (!data.years) {
      toast("No photos found", true);
      return;
    }
    const yearEntry = data.years.find((y) => y.year === year);
    if (!yearEntry || !yearEntry.photos.length) {
      toast("No photos for " + year, true);
      return;
    }

    /** @type {any} */
    const win = window;
    const allPhotos = /** @type {any[]} */ (win.photos || []);
    const photoMap = new Map(allPhotos.map((ph) => [ph.filepath, ph]));
    const otdPhotos = yearEntry.photos.map((p) => {
      const match = allPhotos.find((ph) => ph.id === p.id);
      if (match) return match;
      return (
        photoMap.get(p.filepath || "") || {
          id: p.id,
          filepath: p.filepath || "",
          thumb_hash: p.hash,
          date: p.date,
          aggregate_score: p.score || 0,
          filename: p.filename || "",
        }
      );
    });

    win.currentGridItems = otdPhotos;
    win.currentView = "on-this-day";
    win.currentViewId = `${year}-${month}-${day}`;
    const dateLabel = new Date(year, month - 1, day).toLocaleDateString(undefined, {
      month: "long",
      day: "numeric",
      year: "numeric",
    });
    win.updateToolbarTitle?.("On This Day: " + dateLabel);
    win.updateBreadcrumbs?.("On This Day: " + dateLabel, "Library", "switchToLibrary()");
    renderGridFromItems(win.currentGridItems);

    const subtitle = document.getElementById("toolbar-subtitle");
    if (subtitle) {
      subtitle.textContent = `${yearEntry.count} photos from ${year}`;
    }

    win.saveNavState?.();
  } catch (e) {
    toastError("load photos", e);
  }
}

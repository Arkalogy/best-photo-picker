// @ts-check
/**
 * Memories / auto-stories — sidebar cards + opening a memory as the
 * current grid view. Pulls from `/api/v1/memories`.
 *
 * Reads the global `photos` array (still classic state) and several
 * classic functions (updateToolbarTitle / updateBreadcrumbs /
 * saveNavState / vgrid) via window. Bridged onto window so callers
 * keep working unchanged.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { viewFetch } from "./view-guard.mjs";
import { toast, toastError } from "./toast.mjs";
import { esc, escapeAttr } from "./text-format.mjs";

/**
 * @typedef {Object} Memory
 * @property {number} id
 * @property {string} title
 * @property {number} photo_count
 * @property {string | null} [hero_hash]
 * @property {string | null} [date_start]
 * @property {string | null} [date_end]
 */

/** @type {Memory[]} */
let memoriesList = [];

/** @returns {Memory[]} */
export function _getMemoriesList() {
  return memoriesList;
}

/** @param {Memory[]} m */
export function _setMemoriesList(m) {
  memoriesList = m;
}

/** Fetch the memories from the server and re-render the sidebar. */
export async function loadMemories() {
  try {
    const data = await apiFetch("/api/v1/memories");
    memoriesList = data.memories || [];
    renderMemoriesSidebar();
  } catch {
    memoriesList = [];
  }
}

/**
 * Trigger a server-side regeneration pass. The endpoint returns the
 * fresh list directly so we don't need a follow-up GET.
 */
export async function refreshMemories() {
  // Project convention: nothing should be silent — the memory recompute
  // typically runs 30s+ on a populated library. Pre-toast so the
  // user has feedback the click registered before the completion
  // toast lands.
  toast("Generating memories…");
  try {
    const data = await apiFetch("/api/v1/memories/refresh", { method: "POST" });
    memoriesList = data.memories || [];
    renderMemoriesSidebar();
    toast(`${memoriesList.length} memories generated`);
  } catch (e) {
    toastError("generate memories", e);
  }
}

/**
 * Render the first 8 memory cards into `#memories-nav`. Hides the
 * container when the list is empty.
 */
export function renderMemoriesSidebar() {
  const container = document.getElementById("memories-nav");
  if (!container) return;

  if (memoriesList.length === 0) {
    container.innerHTML = "";
    container.classList.add("hidden");
    return;
  }

  container.classList.remove("hidden");
  const items = memoriesList
    .slice(0, 8)
    .map((m) => {
      const dateRange = _memoryDateRange(m);
      return `<div class="memory-card" data-action="openMemory" data-arg0="${m.id}" title="${escapeAttr(m.title)}">
      <div class="memory-card-bg"${m.hero_hash ? ` style="background-image:url(${authedSrc("/thumb/" + m.hero_hash)})"` : ""}></div>
      <div class="memory-card-overlay">
        <div class="memory-card-title">${esc(m.title)}</div>
        <div class="memory-card-meta">${m.photo_count} photos${dateRange ? " &middot; " + esc(dateRange) : ""}</div>
      </div>
    </div>`;
    })
    .join("");

  container.innerHTML = `
    <div class="memories-header">
      <span class="memories-label">Memories</span>
    </div>
    <div class="memories-scroll">${items}</div>
  `;
}

/**
 * Format a memory's date range as a human-readable string. Handles
 * single-day, same-year, and cross-year cases.
 *
 * @param {Memory} m
 * @returns {string}
 */
export function _memoryDateRange(m) {
  if (!m.date_start) return "";
  try {
    const s = new Date(m.date_start);
    const e = new Date(m.date_end || m.date_start);
    const opts = /** @type {const} */ ({ month: "short", day: "numeric" });
    if (s.toDateString() === e.toDateString()) {
      return s.toLocaleDateString(undefined, { ...opts, year: "numeric" });
    }
    if (s.getFullYear() === e.getFullYear()) {
      return (
        s.toLocaleDateString(undefined, opts) +
        " – " +
        e.toLocaleDateString(undefined, { ...opts, year: "numeric" })
      );
    }
    return (
      s.toLocaleDateString(undefined, { ...opts, year: "numeric" }) +
      " – " +
      e.toLocaleDateString(undefined, { ...opts, year: "numeric" })
    );
  } catch {
    return "";
  }
}

/**
 * Load a memory's photos and switch the grid view to show them.
 *
 * @param {number} memoryId
 */
export async function openMemory(memoryId) {
  try {
    const data = await viewFetch(`/api/v1/memories/${memoryId}`);
    if (!data) return; // view changed mid-fetch
    if (!data.photos) {
      toast("Memory not found", true);
      return;
    }

    /** @type {any} */
    const win = window;
    const allPhotos = /** @type {any[]} */ (win.photos || []);
    const photoMap = new Map(allPhotos.map((ph) => [ph.filepath, ph]));
    const memPhotos = data.photos.map((p) => {
      return (
        photoMap.get(p.filepath) || {
          id: p.id,
          filepath: p.filepath,
          thumb_hash: p.hash,
          date: p.date,
          aggregate_score: p.score || 0,
          filename: p.filepath.split("/").pop(),
        }
      );
    });

    win.currentGridItems = memPhotos;
    win.currentView = "memory";
    win.currentViewId = memoryId;
    win.updateToolbarTitle?.(data.title);
    win.updateBreadcrumbs?.(data.title, "Library", "switchToLibrary()");
    renderGridFromItems(win.currentGridItems);

    const subtitle = document.getElementById("toolbar-subtitle");
    if (subtitle) subtitle.textContent = `${data.photo_count} photos`;

    win.saveNavState?.();
  } catch (e) {
    toastError("load the memory", e);
  }
}

/**
 * Render an arbitrary list of photos into the grid via VGrid. Used by
 * memory views, calendar date-range views, and on-this-day.
 *
 * @param {any[]} items
 */
export function renderGridFromItems(items) {
  const grid = document.getElementById("photo-grid");
  if (!grid) return;
  /** @type {HTMLElement | null} */
  const content = document.querySelector(".content");
  if (content) content.scrollTop = 0;
  grid.style.paddingTop = "0";
  grid.style.paddingBottom = "0";
  if (items.length === 0) {
    grid.innerHTML = '<div class="grid-empty">No photos</div>';
    return;
  }
  grid.innerHTML = "";
  /** @type {any} */
  const win = window;
  win.vgrid?.setItems(items);
}

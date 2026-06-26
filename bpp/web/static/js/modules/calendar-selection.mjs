// @ts-check
/**
 * Calendar multi-day selection state + the "open range" / "pick best"
 * actions that operate on the selected range.
 *
 * Extracted from calendar.mjs during the v0.1 cleanup. Owns the
 * shift-click range selection logic, the selection bar, and the two
 * date-range API hits (calendarOpenRange, calendarPickBestFromRange).
 * Re-exported from calendar.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { getParams } from "./analysis.mjs";
import { renderGridFromItems } from "./memories.mjs";
import { toast, toastError } from "./toast.mjs";
import { calendarOpenDay } from "./calendar.mjs";

/** @type {string | null} */
let _calSelStart = null;
/** @type {string | null} */
let _calSelEnd = null;
let _calPickBestPending = false;

export function getCalSelStart() {
  return _calSelStart;
}
export function getCalSelEnd() {
  return _calSelEnd;
}

export function _resetCalendarSelection() {
  _calSelStart = null;
  _calSelEnd = null;
  _calPickBestPending = false;
}

/**
 * @param {MouseEvent} e
 * @param {string} dateStr
 */
export function calendarDayClick(e, dateStr) {
  if (e.shiftKey && _calSelStart) {
    _calSelEnd = dateStr;
    if (_calSelStart > _calSelEnd) {
      const tmp = _calSelStart;
      _calSelStart = _calSelEnd;
      _calSelEnd = tmp;
    }
    _calApplySelectionClasses();
    _calUpdateSelectionBar();
  } else {
    _calClearSelection();
    calendarOpenDay(dateStr);
  }
}

/**
 * @param {string} dateStr
 */
export function _calDateInSelection(dateStr) {
  if (!_calSelStart) return false;
  if (!_calSelEnd) return dateStr === _calSelStart;
  return dateStr >= _calSelStart && dateStr <= _calSelEnd;
}

export function _calApplySelectionClasses() {
  document
    .querySelectorAll(".cal-cell.cal-selected")
    .forEach((c) => c.classList.remove("cal-selected"));
  if (!_calSelStart) return;
  document.querySelectorAll(".cal-cell[data-date]").forEach((c) => {
    if (_calDateInSelection(/** @type {HTMLElement} */ (c).dataset.date || "")) {
      c.classList.add("cal-selected");
    }
  });
}

export function _calClearSelection() {
  _calSelStart = null;
  _calSelEnd = null;
  _calApplySelectionClasses();
  const bar = document.getElementById("cal-sel-bar");
  if (bar) bar.classList.add("hidden");
}

export function _calUpdateSelectionBar() {
  const bar = document.getElementById("cal-sel-bar");
  if (!bar) return;

  if (_calSelStart && _calSelEnd && _calSelStart !== _calSelEnd) {
    let totalPhotos = 0;
    let dayCount = 0;
    document.querySelectorAll(".cal-cell.cal-selected").forEach((c) => {
      const countEl = c.querySelector(".cal-day-count");
      if (countEl) {
        totalPhotos += parseInt(countEl.textContent || "0") || 0;
        dayCount++;
      }
    });
    const d1 = new Date(_calSelStart + "T00:00:00");
    const d2 = new Date(_calSelEnd + "T00:00:00");
    const label1 = d1.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    const label2 = d2.toLocaleDateString(undefined, { month: "short", day: "numeric" });
    bar.innerHTML = `<span>${label1} – ${label2}</span><span>${totalPhotos} photos in ${dayCount} days</span><button class="cal-sel-view-btn" data-action="calendarOpenRange">View photos</button><button class="cal-sel-view-btn" data-action="calendarPickBestFromRange">Pick best</button><button class="cal-sel-clear-btn" data-action="_calClearSelection">&times;</button>`;
    bar.classList.remove("hidden");
  } else {
    bar.classList.add("hidden");
  }
}

export async function calendarOpenRange() {
  /** @type {any} */
  const win = window;
  if (!_calSelStart || !_calSelEnd) return;
  try {
    const data = await apiFetch(
      `/api/v1/calendar/photos?start=${_calSelStart}&end=${_calSelEnd}`
    );
    if (!data.photos || data.photos.length === 0) {
      toast("No photos in this date range");
      return;
    }
    const photos = /** @type {any[]} */ (win.photos || []);
    const photoMap = new Map(photos.map((ph) => [ph.filepath, ph]));
    const rangePhotos = data.photos.map((p) => {
      return (
        photoMap.get(p.filepath) || {
          id: p.id,
          filepath: p.filepath,
          thumb_hash: p.hash,
          date: p.date,
          aggregate_score: p.score || 0,
          filename: p.filename,
        }
      );
    });
    win.currentGridItems = rangePhotos;
    win.currentView = "calendar_day";
    win.currentViewId = _calSelStart;
    win.hide?.("calendar-view");
    win.show?.("photo-grid");
    const d1 = new Date(_calSelStart + "T00:00:00");
    const d2 = new Date(_calSelEnd + "T00:00:00");
    const title =
      d1.toLocaleDateString(undefined, { month: "long", day: "numeric" }) +
      " – " +
      d2.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
    win.updateToolbarTitle?.(title, `${rangePhotos.length} photos`);
    win.updateBreadcrumbs?.(title, "Calendar", "navigateToCalendar()");
    win.updateToolbarForView?.();
    renderGridFromItems(rangePhotos);
    win.saveNavState?.();
  } catch (e) {
    toastError("load photos for that date range", e);
  }
}

export async function calendarPickBestFromRange() {
  /** @type {any} */
  const win = window;
  if (!_calSelStart || !_calSelEnd) return;
  if (_calPickBestPending) return;
  _calPickBestPending = true;
  try {
    /** @type {Record<string, any>} */
    const params = getParams() || { k: 50, seed: 42 };
    params.start_date = _calSelStart;
    params.end_date = _calSelEnd;
    const data = await apiFetch("/api/v1/recompute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(params),
    });
    if (data.error) {
      toast(data.error, true);
      return;
    }
    if (!data.photos || data.photos.length === 0) {
      toast("No photos in this date range");
      return;
    }
    win.photos = data.photos;
    win.selectedPaths = new Set(data.selected_paths || []);
    win.currentGridItems = win.photos;
    win.currentView = "calendar_day";
    win.currentViewId = _calSelStart;
    win.hide?.("calendar-view");
    win.show?.("photo-grid");
    const d1 = new Date(_calSelStart + "T00:00:00");
    const d2 = new Date(_calSelEnd + "T00:00:00");
    const title =
      d1.toLocaleDateString(undefined, { month: "long", day: "numeric" }) +
      " – " +
      d2.toLocaleDateString(undefined, { month: "long", day: "numeric", year: "numeric" });
    const pickCount = win.selectedPaths.size;
    win.updateToolbarTitle?.(title, `${pickCount} picks from ${data.photos.length} photos`);
    win.updateBreadcrumbs?.(title, "Calendar", "navigateToCalendar()");
    const filterEl = /** @type {HTMLInputElement | null} */ (
      document.getElementById("filter-by")
    );
    if (filterEl) filterEl.value = "selected";
    win.renderGrid?.();
    win.updateToolbarForView?.();
    win.saveNavState?.();
    toast(
      `Picked ${pickCount} best photos from ${d1.toLocaleDateString(undefined, { month: "short", day: "numeric" })} – ${d2.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
    );
  } catch (e) {
    console.error("Pick best from range failed:", e);
    toastError("pick those photos", e);
  } finally {
    _calPickBestPending = false;
  }
}

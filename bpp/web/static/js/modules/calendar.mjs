// @ts-check
/**
 * Calendar view (month / week / year), multi-day selection, "open day"
 * and "open range" navigation, "Pick best" date-range recompute, and
 * the keyboard-arrow focus handler.
 *
 * Self-attaches a global keydown listener on import (calendarKeyHandler),
 * gated to calendar view. Reads/writes shared globals on `window`
 * (`currentView`, `currentViewId`, `currentGridItems`, `photos`,
 * `selectedPaths`, `ICONS`) and calls cross-file helpers (`hide`, `show`,
 * `renderAlbumNav`, `updateToolbarTitle`, `updateToolbarForView`,
 * `updateBreadcrumbs`, `saveNavState`, `renderGrid`).
 */

import { apiFetch } from "./api-client.mjs";
import { viewFetch } from "./view-guard.mjs";
import { MONTHS_FULL } from "./date-format.mjs";
import { renderGridFromItems } from "./memories.mjs";
import { toast, toastError } from "./toast.mjs";

/** @type {Array<{year: number, month: number}>} */
let calendarMonths = [];
/** @type {number | null} */
let calendarYear = null;
/** @type {number | null} */
let calendarMonth = null;
/** @type {"month" | "week" | "year"} */
let calendarViewMode = "month";
/** @type {Date | null} */
let _calWeekStart = null;
/** @type {number | null} */
let _calFocusDay = null;

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function navigateToCalendar() {
  /** @type {any} */
  const win = window;
  win.currentView = "calendar";
  win.currentViewId = null;

  win.hide?.("photo-grid");
  win.hide?.("people-view");
  win.hide?.("pets-view");
  win.hide?.("groups-view");
  win.hide?.("map-view");
  win.show?.("calendar-view");
  win.renderAlbumNav?.();
  win.updateToolbarTitle?.("Calendar", "");
  win.updateToolbarForView?.();
  win.saveNavState?.();
  loadCalendarData();
}

export async function loadCalendarData() {
  try {
    const data = await viewFetch("/api/v1/calendar/months");
    if (!data) return; // user switched away mid-fetch
    calendarMonths = data.months || [];
    if (calendarMonths.length > 0 && !calendarYear) {
      const last = calendarMonths[calendarMonths.length - 1];
      calendarYear = last.year;
      calendarMonth = last.month;
    }
    if (calendarYear) {
      _calRenderCurrentMode();
    } else {
      renderCalendarEmpty();
    }
  } catch {
    renderCalendarEmpty();
  }
}

function _calRenderCurrentMode() {
  _calClearSelection();
  if (calendarViewMode === "year") {
    if (calendarYear) loadCalendarYear(calendarYear);
  } else if (calendarViewMode === "week") {
    if (!_calWeekStart && calendarYear && calendarMonth) {
      _calWeekStart = _calWeekOf(new Date(calendarYear, calendarMonth - 1, 1));
    }
    if (_calWeekStart) loadCalendarWeek(_calWeekStart);
  } else if (calendarMonth && calendarYear) {
    loadCalendarMonth(calendarYear, calendarMonth);
  }
}

/**
 * @param {"month" | "week" | "year"} mode
 */
export function calendarSetMode(mode) {
  if (mode === calendarViewMode) return;
  calendarViewMode = mode;
  _calRenderCurrentMode();
}

export function _calViewToggle() {
  const modes = [
    { key: "month", label: "Month" },
    { key: "week", label: "Week" },
    { key: "year", label: "Year" },
  ];
  let html = '<div class="cal-mode-toggle">';
  for (const m of modes) {
    const active = m.key === calendarViewMode ? " active" : "";
    html += `<button class="cal-mode-btn${active}" data-action="calendarSetMode" data-arg0="${m.key}">${m.label}</button>`;
  }
  html += "</div>";
  return html;
}

/**
 * @param {number} year
 * @param {number} month
 */
export async function loadCalendarMonth(year, month) {
  calendarYear = year;
  calendarMonth = month;
  calendarViewMode = "month";
  // Selecting a month dismisses the year-picker if it's open. (Prev/Next
  // and initial load call this too, where the picker is already hidden —
  // a harmless no-op.)
  document.getElementById("cal-year-picker")?.classList.add("hidden");
  try {
    const data = await viewFetch(`/api/v1/calendar/days?year=${year}&month=${month}`);
    if (!data) return;
    renderCalendarGrid(year, month, data.days || []);
  } catch {
    renderCalendarGrid(year, month, []);
  }
}

/**
 * @param {Date} date
 * @returns {Date}
 */
function _calWeekOf(date) {
  const d = new Date(date);
  d.setDate(d.getDate() - d.getDay());
  d.setHours(0, 0, 0, 0);
  return d;
}

/**
 * @param {Date | string} startDate
 */
export async function loadCalendarWeek(startDate) {
  _calWeekStart = new Date(startDate);
  calendarViewMode = "week";
  const mid = new Date(_calWeekStart);
  mid.setDate(mid.getDate() + 3);
  calendarYear = mid.getFullYear();
  calendarMonth = mid.getMonth() + 1;

  const dayPromises = [];
  for (let i = 0; i < 7; i++) {
    const d = new Date(_calWeekStart);
    d.setDate(d.getDate() + i);
    const y = d.getFullYear();
    const m = d.getMonth() + 1;
    dayPromises.push(
      apiFetch(`/api/v1/calendar/days?year=${y}&month=${m}`)
        .then((data) => ({ date: d, days: data.days || [] }))
        .catch((e) => {
          console.warn("Calendar days fetch failed:", e);
          return { date: d, days: [] };
        })
    );
  }
  const results = await Promise.all(dayPromises);
  renderCalendarWeek(_calWeekStart, results, calendarYear, calendarMonth);
}

export function calendarWeekPrev() {
  if (!_calWeekStart) return;
  const d = new Date(_calWeekStart);
  d.setDate(d.getDate() - 7);
  loadCalendarWeek(d);
}

export function calendarWeekNext() {
  if (!_calWeekStart) return;
  const d = new Date(_calWeekStart);
  d.setDate(d.getDate() + 7);
  loadCalendarWeek(d);
}

/**
 * @param {number} year
 */
export async function loadCalendarYear(year) {
  calendarYear = year;
  calendarViewMode = "year";
  try {
    const data = await viewFetch(`/api/v1/calendar/year?year=${year}`);
    if (!data) return;
    renderCalendarYearGrid(year, data.months || {});
  } catch {
    renderCalendarYearGrid(year, {});
  }
}

export function calendarPrev() {
  if (!calendarYear || !calendarMonth) return;
  for (let i = calendarMonths.length - 1; i >= 0; i--) {
    const m = calendarMonths[i];
    if (m.year < calendarYear || (m.year === calendarYear && m.month < calendarMonth)) {
      loadCalendarMonth(m.year, m.month);
      return;
    }
  }
}

export function calendarNext() {
  if (!calendarYear || !calendarMonth) return;
  for (const m of calendarMonths) {
    if (m.year > calendarYear || (m.year === calendarYear && m.month > calendarMonth)) {
      loadCalendarMonth(m.year, m.month);
      return;
    }
  }
}

export function calendarShowYearPicker() {
  const picker = document.getElementById("cal-year-picker");
  if (!picker) return;
  if (!picker.classList.contains("hidden")) {
    picker.classList.add("hidden");
    return;
  }
  /** @type {Record<number, Array<{year: number, month: number}>>} */
  const years = {};
  for (const m of calendarMonths) {
    if (!years[m.year]) years[m.year] = [];
    years[m.year].push(m);
  }
  let html = "";
  for (const [yr, months] of Object.entries(years).sort((a, b) => Number(b[0]) - Number(a[0]))) {
    html += `<div class="cal-yp-year"><span class="cal-yp-label">${yr}</span><div class="cal-yp-months">`;
    for (const m of months) {
      const active = m.year === calendarYear && m.month === calendarMonth;
      html += `<button class="cal-yp-btn${active ? " active" : ""}" data-action="loadCalendarMonth" data-arg0="${m.year}" data-arg1="${m.month}">${MONTHS_FULL[m.month - 1].slice(0, 3)}</button>`;
    }
    html += "</div></div>";
  }
  picker.innerHTML = html;
  picker.classList.remove("hidden");
}

/**
 * @param {KeyboardEvent} e
 */
export function calendarKeyHandler(e) {
  /** @type {any} */
  const win = window;
  if (win.currentView !== "calendar") return;

  if (e.key === "Escape") {
    // Sibling bubble handlers (slideshow, compare) don't get to also
    // process this ESC. Dialog (capture phase) runs first.
    e.stopImmediatePropagation();
    _calFocusDay = null;
    _calClearSelection();
    document
      .querySelectorAll(".cal-cell.cal-focused")
      .forEach((c) => c.classList.remove("cal-focused"));
    return;
  }

  if (calendarViewMode !== "month") return;
  if (!calendarYear || !calendarMonth) return;

  const daysInMonth = new Date(calendarYear, calendarMonth, 0).getDate();

  if (e.key === "ArrowRight") {
    e.preventDefault();
    _calFocusDay = Math.min(daysInMonth, (_calFocusDay || 0) + 1);
    _calHighlight(_calFocusDay);
  } else if (e.key === "ArrowLeft") {
    e.preventDefault();
    _calFocusDay = Math.max(1, (_calFocusDay || 2) - 1);
    _calHighlight(_calFocusDay);
  } else if (e.key === "ArrowDown") {
    e.preventDefault();
    _calFocusDay = Math.min(daysInMonth, (_calFocusDay || 0) + 7);
    _calHighlight(_calFocusDay);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    _calFocusDay = Math.max(1, (_calFocusDay || 8) - 7);
    _calHighlight(_calFocusDay);
  } else if (e.key === "Enter" && _calFocusDay) {
    e.preventDefault();
    const dateStr = `${calendarYear}-${String(calendarMonth).padStart(2, "0")}-${String(_calFocusDay).padStart(2, "0")}`;
    const cell = document.querySelector(`.cal-cell[data-date="${dateStr}"]`);
    if (cell && cell.classList.contains("cal-has-photos")) {
      calendarOpenDay(dateStr);
    }
  }
}

/**
 * @param {number} day
 */
function _calHighlight(day) {
  document
    .querySelectorAll(".cal-cell.cal-focused")
    .forEach((c) => c.classList.remove("cal-focused"));
  if (!calendarYear || !calendarMonth) return;
  const dateStr = `${calendarYear}-${String(calendarMonth).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  const cell = document.querySelector(`.cal-cell[data-date="${dateStr}"]`);
  if (cell) {
    cell.classList.add("cal-focused");
    cell.scrollIntoView({ block: "nearest" });
  }
}

document.addEventListener("keydown", calendarKeyHandler);

/**
 * @param {string} dateStr
 */
export async function calendarOpenDay(dateStr) {
  /** @type {any} */
  const win = window;
  try {
    const data = await viewFetch(`/api/v1/calendar/photos?date=${dateStr}`);
    if (!data) return; // user switched away mid-fetch
    if (!data.photos || data.photos.length === 0) {
      toast("No photos for this date");
      return;
    }
    const photos = /** @type {any[]} */ (win.photos || []);
    const photoMap = new Map(photos.map((ph) => [ph.filepath, ph]));
    const dayPhotos = data.photos.map((p) => {
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
    win.currentGridItems = dayPhotos;
    win.currentView = "calendar_day";
    win.currentViewId = dateStr;
    win.hide?.("calendar-view");
    win.show?.("photo-grid");
    const d = new Date(dateStr + "T00:00:00");
    const title = d.toLocaleDateString(undefined, {
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
    });
    win.updateToolbarTitle?.(title, `${dayPhotos.length} photos`);
    win.updateBreadcrumbs?.(title, "Calendar", "navigateToCalendar()");
    win.updateToolbarForView?.();
    renderGridFromItems(dayPhotos);
    win.saveNavState?.();
  } catch (e) {
    toastError("load photos for that date", e);
  }
}

export function getCalendarMonths() {
  return calendarMonths;
}

/** Test-only: read internal selection. */
export function _getCalState() {
  return {
    calendarMonths,
    calendarYear,
    calendarMonth,
    calendarViewMode,
    selStart: getCalSelStart(),
    selEnd: getCalSelEnd(),
    weekStart: _calWeekStart,
  };
}

/** Test-only: reset module-private state. */
export function _resetCalendarState() {
  calendarMonths = [];
  calendarYear = null;
  calendarMonth = null;
  calendarViewMode = "month";
  _calWeekStart = null;
  _calFocusDay = null;
  _resetCalendarSelection();
}


import {
  renderCalendarEmpty,
  renderCalendarGrid,
  renderCalendarStrip,
  renderCalendarWeek,
  renderCalendarYearGrid,
} from "./calendar-render.mjs";
export {
  renderCalendarEmpty,
  renderCalendarGrid,
  renderCalendarStrip,
  renderCalendarWeek,
  renderCalendarYearGrid,
};

import {
  _calClearSelection,
  _calDateInSelection,
  _calUpdateSelectionBar,
  _resetCalendarSelection,
  calendarDayClick,
  calendarOpenRange,
  calendarPickBestFromRange,
  getCalSelEnd,
  getCalSelStart,
} from "./calendar-selection.mjs";
export {
  _calClearSelection,
  _calDateInSelection,
  _calUpdateSelectionBar,
  calendarDayClick,
  calendarOpenRange,
  calendarPickBestFromRange,
  getCalSelEnd,
  getCalSelStart,
};

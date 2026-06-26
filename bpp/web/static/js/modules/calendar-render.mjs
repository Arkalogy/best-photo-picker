// @ts-check
/**
 * Calendar grid render helpers — month grid, year grid, year strip,
 * empty state.
 *
 * Extracted from calendar.mjs during the v0.1 cleanup. Pure HTML
 * composition from photo-count data, no state mutation. Re-exported
 * from calendar.mjs.
 */

import { authedSrc } from "./api-client.mjs";
import { MONTHS_FULL } from "./date-format.mjs";
import { _calViewToggle, getCalendarMonths } from "./calendar.mjs";
import { _calDateInSelection, _calUpdateSelectionBar } from "./calendar-selection.mjs";

const DAY_NAMES = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];


export function renderCalendarEmpty() {
  const el = document.getElementById("calendar-view");
  if (!el) return;
  el.innerHTML = '<div class="cal-empty">No photos with dates found</div>';
}

/**
 * @param {number} year
 * @param {number} month
 * @param {Array<{day: number, count: number, top_hash?: string}>} days
 */
export function renderCalendarGrid(year, month, days) {
  /** @type {any} */
  const win = window;
  const ICONS = win.ICONS || {};
  const el = document.getElementById("calendar-view");
  if (!el) return;

  /** @type {Record<number, {day: number, count: number, top_hash?: string}>} */
  const dayMap = {};
  let maxCount = 0;
  for (const d of days) {
    dayMap[d.day] = d;
    if (d.count > maxCount) maxCount = d.count;
  }

  const months = getCalendarMonths();
  const hasPrev = months.some(
    (m) => m.year < year || (m.year === year && m.month < month)
  );
  const hasNext = months.some(
    (m) => m.year > year || (m.year === year && m.month > month)
  );

  let html = `<div class="cal-header">
    <button class="cal-nav-btn" data-action="calendarPrev" ${hasPrev ? "" : "disabled"} aria-label="Previous month">&lsaquo;</button>
    <div class="cal-title-group">
      <span class="cal-title">${MONTHS_FULL[month - 1]} ${year}</span>
      <button class="cal-year-btn" data-action="calendarShowYearPicker" aria-label="Jump to month">${ICONS.calendar || ""}</button>
    </div>
    <button class="cal-nav-btn" data-action="calendarNext" ${hasNext ? "" : "disabled"} aria-label="Next month">&rsaquo;</button>
  </div>`;

  html += _calViewToggle();
  html += '<div class="cal-year-picker hidden" id="cal-year-picker"></div>';

  html += '<div class="cal-grid">';
  for (const dn of DAY_NAMES) {
    html += `<div class="cal-dow">${dn}</div>`;
  }

  const firstDay = new Date(year, month - 1, 1).getDay();
  const daysInMonth = new Date(year, month, 0).getDate();

  for (let i = 0; i < firstDay; i++) {
    html += '<div class="cal-cell cal-empty-cell"></div>';
  }

  const now = new Date();
  const todayDay =
    now.getFullYear() === year && now.getMonth() + 1 === month ? now.getDate() : -1;

  for (let d = 1; d <= daysInMonth; d++) {
    const info = dayMap[d];
    const count = info ? info.count : 0;
    const intensity = maxCount > 0 && count > 0 ? Math.max(0.15, count / maxCount) : 0;
    const hasPhotos = count > 0;
    const isToday = d === todayDay;
    const thumbStyle =
      info && info.top_hash ? `background-image:url(${authedSrc("/thumb/" + info.top_hash)})` : "";

    const dateStr = `${year}-${String(month).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const cls = ["cal-cell"];
    if (hasPhotos) cls.push("cal-has-photos");
    if (isToday) cls.push("cal-today");
    if (_calDateInSelection(dateStr)) cls.push("cal-selected");

    html += `<div class="${cls.join(" ")}" data-date="${dateStr}" `;
    if (hasPhotos) {
      html += `data-action="calendarDayClick" data-pass-event="true" data-arg0="${dateStr}"`;
    }
    html += `style="--cal-intensity: ${intensity}">`;

    if (thumbStyle) {
      html += `<div class="cal-cell-thumb" style="${thumbStyle}"></div>`;
    }
    html += `<div class="cal-day-num">${d}</div>`;
    if (count > 0) {
      html += `<div class="cal-day-count">${count}</div>`;
    }
    html += "</div>";
  }

  html += "</div>";

  if (maxCount > 0) {
    html += `<div class="cal-legend">
      <span class="cal-legend-label">Fewer</span>
      <span class="cal-legend-swatch" style="--cal-intensity:0.15"></span>
      <span class="cal-legend-swatch" style="--cal-intensity:0.4"></span>
      <span class="cal-legend-swatch" style="--cal-intensity:0.65"></span>
      <span class="cal-legend-swatch" style="--cal-intensity:0.9"></span>
      <span class="cal-legend-label">More</span>
      <span class="cal-legend-max">(max ${maxCount})</span>
    </div>`;
  }

  html += '<div class="cal-sel-bar hidden" id="cal-sel-bar"></div>';
  html += renderCalendarStrip(year, month);

  el.innerHTML = html;
  _calUpdateSelectionBar();
}

/**
 * @param {number} year
 * @param {Record<string, Array<{day: number, count: number}>>} monthsData
 */
export function renderCalendarYearGrid(year, monthsData) {
  const el = document.getElementById("calendar-view");
  if (!el) return;

  const years = [...new Set(getCalendarMonths().map((m) => m.year))].sort();
  const hasPrevYear = years.some((y) => y < year);
  const hasNextYear = years.some((y) => y > year);

  let html = `<div class="cal-header">
    <button class="cal-nav-btn" data-action="loadCalendarYear" data-arg0="${year - 1}" ${hasPrevYear ? "" : "disabled"} aria-label="Previous year">&lsaquo;</button>
    <div class="cal-title-group">
      <span class="cal-title">${year}</span>
    </div>
    <button class="cal-nav-btn" data-action="loadCalendarYear" data-arg0="${year + 1}" ${hasNextYear ? "" : "disabled"} aria-label="Next year">&rsaquo;</button>
  </div>`;

  html += _calViewToggle();

  let globalMax = 0;
  for (const days of Object.values(monthsData)) {
    for (const d of days) {
      if (d.count > globalMax) globalMax = d.count;
    }
  }

  html += '<div class="cal-year-grid">';

  for (let m = 1; m <= 12; m++) {
    const days = monthsData[String(m)] || [];
    /** @type {Record<number, number>} */
    const dayMap = {};
    let monthTotal = 0;
    for (const d of days) {
      dayMap[d.day] = d.count;
      monthTotal += d.count;
    }

    const hasData = days.length > 0;
    html += `<div class="cal-mini-month${hasData ? " cal-mini-clickable" : ""}" ${hasData ? `data-action="loadCalendarMonth" data-arg0="${year}" data-arg1="${m}"` : ""}>`;
    html += `<div class="cal-mini-label">${MONTHS_FULL[m - 1].slice(0, 3)}${monthTotal > 0 ? ` <span class="cal-mini-count">${monthTotal}</span>` : ""}</div>`;

    const firstDay = new Date(year, m - 1, 1).getDay();
    const daysInMonth = new Date(year, m, 0).getDate();

    html += '<div class="cal-mini-grid">';
    for (let i = 0; i < firstDay; i++) {
      html += '<div class="cal-mini-cell cal-mini-empty"></div>';
    }
    for (let d = 1; d <= daysInMonth; d++) {
      const count = dayMap[d] || 0;
      const intensity = globalMax > 0 && count > 0 ? Math.max(0.2, count / globalMax) : 0;
      html += `<div class="cal-mini-cell${count > 0 ? " cal-mini-has" : ""}" style="--cal-intensity:${intensity}" title="${MONTHS_FULL[m - 1]} ${d}: ${count} photos"></div>`;
    }
    html += "</div></div>";
  }

  html += "</div>";

  if (years.length > 1) {
    html += '<div class="cal-strip">';
    for (const y of years) {
      const active = y === year;
      html += `<button class="cal-strip-btn${active ? " active" : ""}" data-action="loadCalendarYear" data-arg0="${y}">${y}</button>`;
    }
    html += "</div>";
  }

  el.innerHTML = html;
}

/**
 * @param {number} currentYear
 * @param {number} currentMonth
 */
/**
 * @param {Date} weekStart
 * @param {Array<{date: Date, days: Array<{day: number, count: number, top_hash?: string}>}>} weekData
 * @param {number | null} stripYear
 * @param {number | null} stripMonth
 */
export function renderCalendarWeek(weekStart, weekData, stripYear, stripMonth) {
  const el = document.getElementById("calendar-view");
  if (!el || !weekStart) return;

  const weekEnd = new Date(weekStart);
  weekEnd.setDate(weekEnd.getDate() + 6);

  const startLabel = weekStart.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
  const endLabel = weekEnd.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });

  let html = `<div class="cal-header">
    <button class="cal-nav-btn" data-action="calendarWeekPrev" aria-label="Previous week">&lsaquo;</button>
    <div class="cal-title-group">
      <span class="cal-title">${startLabel} – ${endLabel}</span>
    </div>
    <button class="cal-nav-btn" data-action="calendarWeekNext" aria-label="Next week">&rsaquo;</button>
  </div>`;

  html += _calViewToggle();

  html += '<div class="cal-week-grid">';

  const now = new Date();
  const todayStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;

  for (let i = 0; i < 7; i++) {
    const d = new Date(weekStart);
    d.setDate(d.getDate() + i);
    const dayNum = d.getDate();
    const month = d.getMonth() + 1;
    const year = d.getFullYear();
    const dateStr = `${year}-${String(month).padStart(2, "0")}-${String(dayNum).padStart(2, "0")}`;
    const isToday = dateStr === todayStr;

    const wd = weekData[i];
    const dayInfo = wd.days.find((dd) => dd.day === dayNum);
    const count = dayInfo ? dayInfo.count : 0;
    const thumbHash = dayInfo ? dayInfo.top_hash : null;

    const cls = ["cal-week-day"];
    if (count > 0) cls.push("cal-week-has-photos");
    if (isToday) cls.push("cal-today");

    html += `<div class="${cls.join(" ")}" ${count > 0 ? `data-action="calendarOpenDay" data-arg0="${dateStr}"` : ""}>`;
    html += `<div class="cal-week-day-header">`;
    html += `<span class="cal-week-dow">${DAY_NAMES[i]}</span>`;
    html += `<span class="cal-week-date${isToday ? " cal-today-num" : ""}">${dayNum}</span>`;
    html += `</div>`;

    if (thumbHash) {
      html += `<div class="cal-week-thumb" style="background-image:url(${authedSrc("/thumb/" + thumbHash)})"></div>`;
    } else {
      html += `<div class="cal-week-thumb cal-week-empty-thumb"></div>`;
    }

    if (count > 0) {
      html += `<div class="cal-week-count">${count} photo${count !== 1 ? "s" : ""}</div>`;
    }
    html += `</div>`;
  }

  html += "</div>";
  if (stripYear && stripMonth) {
    html += renderCalendarStrip(stripYear, stripMonth);
  }
  el.innerHTML = html;
}

export function renderCalendarStrip(currentYear, currentMonth) {
  const months = getCalendarMonths();
  if (months.length === 0) return "";
  let html = '<div class="cal-strip">';
  for (const m of months) {
    const active = m.year === currentYear && m.month === currentMonth;
    const label = MONTHS_FULL[m.month - 1].slice(0, 3);
    const yearLabel =
      m.month === 1 || m === months[0] ? ` '${String(m.year).slice(2)}` : "";
    html += `<button class="cal-strip-btn${active ? " active" : ""}" data-action="loadCalendarMonth" data-arg0="${m.year}" data-arg1="${m.month}">${label}${yearLabel}</button>`;
  }
  html += "</div>";
  return html;
}



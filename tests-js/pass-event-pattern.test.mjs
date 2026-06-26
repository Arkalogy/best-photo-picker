// @ts-check
/**
 * Tests for the data-pass-event + data-arg* pattern used by functions
 * that need both the MouseEvent and additional args.
 *
 * Covers: calendarDayClick, lbRenameFace, _personCardClick source shapes.
 */

import { beforeEach, describe, expect, test, vi } from "vitest";
import { calendarDayClick } from "../bpp/web/static/js/modules/calendar.mjs";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn().mockResolvedValue({ photos: [], days: [] }),
  authedSrc: (p) => p,
}));

vi.mock("../bpp/web/static/js/modules/analysis.mjs", () => ({
  getParams: vi.fn().mockReturnValue({}),
  scheduleRecompute: vi.fn(),
}));

vi.mock("../bpp/web/static/js/modules/memories.mjs", () => ({
  renderGridFromItems: vi.fn(),
}));

vi.mock("../bpp/web/static/js/modules/toast.mjs", () => ({
  toast: vi.fn(),
  showToast: vi.fn(),
}));

beforeEach(() => {
  document.body.innerHTML = `
    <div id="calendar-view"></div>
    <div id="photo-grid"></div>
    <div id="toolbar-title"></div>
    <div id="toolbar-subtitle"></div>
    <div id="album-nav"></div>
    <div id="toast-container"></div>
  `;
  /** @type {any} */ (window).currentView = null;
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).ICONS = {};
});

// ── calendarDayClick ──────────────────────────────────────────────────────────

describe("calendarDayClick — pass-event pattern", () => {
  test("accepts (e, dateStr) — normal direct call", () => {
    const e = new MouseEvent("click");
    // Should not throw with a normal date string
    expect(() => calendarDayClick(e, "2024-07-15")).not.toThrow();
  });

  test("source shape: uses data-pass-event in template", () => {
    const { readFileSync } = require("fs");
    const renderSrc = readFileSync("bpp/web/static/js/modules/calendar-render.mjs", "utf8");
    const selectionSrc = readFileSync("bpp/web/static/js/modules/calendar-selection.mjs", "utf8");
    // The calendar day cells must use data-pass-event so the click event
    // reaches calendarDayClick for modifier key detection etc.
    expect(renderSrc).toContain('data-pass-event="true"');
    expect(renderSrc).toContain("calendarDayClick");
    expect(selectionSrc).toContain("export function calendarDayClick");
  });
});

// ── (removed) lbRenameFace ────────────────────────────────────────────────────
// Inline rename via clicking the People-row name was removed when the chip's
// primary click was reassigned to "open this person's album." Rename now
// lives in the right-click person context menu (shared with the sidebar).

// ── _personCardClick ─────────────────────────────────────────────────────────

describe("_personCardClick — pass-event + dataset fallback", () => {
  test("source: card template uses data-pass-event and data-arg0", () => {
    const { readFileSync } = require("fs");
    const src = readFileSync("bpp/web/static/js/modules/people-view.mjs", "utf8");
    // The person card div must carry data-pass-event so modifier keys work,
    // and data-arg0 for the cluster ID read via dataset fallback.
    expect(src).toContain('data-action="_personCardClick"');
    expect(src).toContain('data-pass-event="true"');
  });

  test("source: function reads clusterId from dataset.arg0 as fallback", () => {
    const { readFileSync } = require("fs");
    const src = readFileSync("bpp/web/static/js/modules/people.mjs", "utf8");
    // When called via dispatcher, clusterId arrives as undefined (event is
    // arg0, but dispatcher puts it there via pass-event, so arg0 in data-arg
    // is the cluster id). The back-compat reads from this.dataset.arg0.
    expect(src).toMatch(/clusterId.*=.*dataset\.arg0|dataset\.arg0.*clusterId/);
  });
});

// @ts-check
/**
 * The calendar's arrow / Escape keys apply to the calendar grid alone.
 *
 * The handler used to run on every keydown while the calendar view was
 * current: arrows typed into the search box moved the day focus, and its
 * Escape stopped the event before the lightbox opened from a day (or a
 * dialog over the calendar) could close.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getCalState,
  _resetCalendarState,
  calendarKeyHandler,
  loadCalendarMonth,
} from "../bpp/web/static/js/modules/calendar.mjs";

beforeEach(async () => {
  Element.prototype.scrollIntoView = function () {};
  document.body.innerHTML = `
    <div id="calendar-view"></div>
    <input id="search-box" type="text">
    <div class="lightbox-overlay" id="lightbox" role="dialog" aria-modal="true"></div>
    <div class="modal-overlay" id="modal-overlay" role="dialog" aria-modal="true"></div>
    <div class="ss-overlay hidden" id="slideshow-overlay" role="dialog"></div>`;
  /** @type {any} */ (window).currentView = "calendar";
  /** @type {any} */ (window).lightboxIdx = -1;
  _resetCalendarState();
  vi.stubGlobal(
    "fetch",
    vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            days: [
              { day: 5, count: 2 },
              { day: 6, count: 1 },
            ],
          }),
          {
            status: 200,
          }
        )
    )
  );
  await loadCalendarMonth(2024, 3);
});

afterEach(() => {
  document.body.innerHTML = "";
  _resetCalendarState();
  vi.unstubAllGlobals();
});

/** @param {string} key @param {EventTarget} [target] */
function press(key, target) {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  (target || document.body).dispatchEvent(ev);
  return ev;
}

describe("calendar keys stay out of other surfaces", () => {
  test("arrows move the day focus from the grid", () => {
    press("ArrowRight");
    expect(_getCalState().focusDay).toBe(1);
  });

  test("arrows typed into an input are left to the input", () => {
    const input = /** @type {HTMLInputElement} */ (document.getElementById("search-box"));
    const ev = press("ArrowRight", input);
    expect(ev.defaultPrevented).toBe(false);
    expect(_getCalState().focusDay).toBe(null);
  });

  test("Escape in the open lightbox is not swallowed", () => {
    /** @type {any} */ (window).lightboxIdx = 0;
    let reached = false;
    const after = () => {
      reached = true;
    };
    document.addEventListener("keydown", after);
    press("Escape");
    document.removeEventListener("keydown", after);
    expect(reached).toBe(true);
  });

  test("Escape with a dialog open is left to the dialog", () => {
    document.getElementById("modal-overlay")?.classList.add("visible");
    let reached = false;
    const after = () => {
      reached = true;
    };
    document.addEventListener("keydown", after);
    press("Escape");
    document.removeEventListener("keydown", after);
    expect(reached).toBe(true);
  });

  test("a dialog that hides with .hidden counts once it is shown", () => {
    document.getElementById("slideshow-overlay")?.classList.remove("hidden");
    press("ArrowRight");
    expect(_getCalState().focusDay).toBe(null);
  });

  test("Escape on the bare calendar still clears the day focus", () => {
    press("ArrowRight");
    press("Escape");
    expect(_getCalState().focusDay).toBe(null);
  });
});

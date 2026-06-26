// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getTimelineFilter,
  _setTimelineFilter,
  _tlLabel,
  _tlShort,
  applyTimelineFilter,
  buildTimeline,
  clearTimelineFilter,
} from "../bpp/web/static/js/modules/timeline.mjs";

beforeEach(() => {
  document.body.innerHTML = '<div id="timeline-bar" class="hidden"></div>';
  _setTimelineFilter(null);
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).renderGrid = vi.fn();
});

afterEach(() => {
  document.body.innerHTML = "";
  delete (/** @type {any} */ (window).photos);
  delete (/** @type {any} */ (window).renderGrid);
});

const bar = () => /** @type {HTMLElement} */ (document.getElementById("timeline-bar"));

describe("_tlLabel", () => {
  test("YYYY-MM → 'Month Year'", () => {
    expect(_tlLabel("2024-01")).toBe("January 2024");
    expect(_tlLabel("2024-12")).toBe("December 2024");
    expect(_tlLabel("2020-06")).toBe("June 2020");
  });
});

describe("_tlShort", () => {
  test("YYYY-MM → 'Mon ’YY'", () => {
    expect(_tlShort("2024-01")).toBe("Jan ’24");
    expect(_tlShort("2024-12")).toBe("Dec ’24");
    expect(_tlShort("2020-06")).toBe("Jun ’20");
  });
});

describe("buildTimeline", () => {
  test("hides the bar when there are no photos", () => {
    /** @type {any} */ (window).photos = [];
    bar().classList.remove("hidden");
    buildTimeline();
    expect(bar().classList.contains("hidden")).toBe(true);
  });

  test("hides the bar when only one month is represented", () => {
    /** @type {any} */ (window).photos = [{ date_month: "2024-06" }, { date_month: "2024-06" }];
    buildTimeline();
    expect(bar().classList.contains("hidden")).toBe(true);
  });

  test("renders one .tl-seg per month, scaled by max count", () => {
    /** @type {any} */ (window).photos = [
      { date_month: "2024-01" },
      { date_month: "2024-01" },
      { date_month: "2024-02" },
      { date_month: "2024-03" },
      { date_month: "2024-03" },
      { date_month: "2024-03" },
      { date_month: "2024-03" }, // March is max (4)
    ];
    buildTimeline();
    expect(bar().classList.contains("hidden")).toBe(false);
    const segments = bar().querySelectorAll(".tl-seg");
    expect(segments).toHaveLength(3);
    // Months are sorted ascending
    expect(segments[0].getAttribute("data-month")).toBe("2024-01");
    expect(segments[1].getAttribute("data-month")).toBe("2024-02");
    expect(segments[2].getAttribute("data-month")).toBe("2024-03");
    // Max-month bar is full height (28px)
    const marchBar = /** @type {HTMLElement} */ (segments[2].querySelector(".tl-seg-bar"));
    expect(marchBar.style.height).toBe("28px");
  });

  test("ignores soft-deleted photos", () => {
    /** @type {any} */ (window).photos = [
      { date_month: "2024-01" },
      { date_month: "2024-02", deleted_at: "2024-03-01" },
    ];
    buildTimeline();
    // After filtering deleted, only one month remains → hidden
    expect(bar().classList.contains("hidden")).toBe(true);
  });

  test("highlights the active filter and renders the active chip", () => {
    /** @type {any} */ (window).photos = [
      { date_month: "2024-01" },
      { date_month: "2024-02" },
      { date_month: "2024-02" },
    ];
    _setTimelineFilter("2024-02");
    buildTimeline();
    const active = bar().querySelector(".tl-seg.active");
    expect(active?.getAttribute("data-month")).toBe("2024-02");
    const chip = bar().querySelector(".tl-active-chip");
    expect(chip).toBeTruthy();
    expect(chip.textContent).toContain("February 2024");
    expect(chip.textContent).toContain("(2)");
  });

  test("no-op when the timeline-bar element is missing", () => {
    document.body.innerHTML = "";
    expect(() => buildTimeline()).not.toThrow();
  });
});

describe("applyTimelineFilter", () => {
  test("first click sets the filter and triggers a grid re-render", () => {
    applyTimelineFilter("2024-06");
    expect(_getTimelineFilter()).toBe("2024-06");
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
  });

  test("clicking the same month a second time clears the filter", () => {
    applyTimelineFilter("2024-06");
    applyTimelineFilter("2024-06");
    expect(_getTimelineFilter()).toBeNull();
  });

  test("clicking a different month replaces the filter", () => {
    applyTimelineFilter("2024-06");
    applyTimelineFilter("2024-07");
    expect(_getTimelineFilter()).toBe("2024-07");
  });
});

describe("clearTimelineFilter", () => {
  test("nulls the filter and triggers a re-render", () => {
    _setTimelineFilter("2024-06");
    clearTimelineFilter();
    expect(_getTimelineFilter()).toBeNull();
    expect(/** @type {any} */ (window).renderGrid).toHaveBeenCalled();
  });
});

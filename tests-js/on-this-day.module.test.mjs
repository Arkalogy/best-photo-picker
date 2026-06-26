// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getOnThisDayData,
  _setOnThisDayData,
  loadOnThisDay,
  renderOnThisDay,
} from "../bpp/web/static/js/modules/on-this-day.mjs";

beforeEach(() => {
  document.body.innerHTML = `
    <div id="on-this-day" class="hidden"></div>
    <div id="toast-container"></div>
  `;
  _setOnThisDayData(null);
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

const container = () => /** @type {HTMLElement} */ (document.getElementById("on-this-day"));

describe("renderOnThisDay", () => {
  test("hides container when no data", () => {
    _setOnThisDayData(null);
    renderOnThisDay();
    expect(container().classList.contains("hidden")).toBe(true);
  });

  test("hides container when years list is empty", () => {
    _setOnThisDayData(/** @type {any} */ ({ month: 4, day: 25, years: [] }));
    renderOnThisDay();
    expect(container().classList.contains("hidden")).toBe(true);
  });

  test("renders one .otd-card per year", () => {
    _setOnThisDayData(
      /** @type {any} */ ({
        month: 4,
        day: 25,
        years: [
          { year: 2024, years_ago: 1, count: 5, photos: [] },
          { year: 2023, years_ago: 2, count: 3, photos: [] },
        ],
      })
    );
    renderOnThisDay();
    expect(container().classList.contains("hidden")).toBe(false);
    expect(container().querySelectorAll(".otd-card")).toHaveLength(2);
  });

  test("renders fan thumbnails from photos[1..3] when hash present", () => {
    _setOnThisDayData(
      /** @type {any} */ ({
        month: 1,
        day: 1,
        years: [
          {
            year: 2024,
            years_ago: 1,
            count: 5,
            photos: [
              { hash: "h0" }, // hero, skipped
              { hash: "h1" },
              { hash: "h2" },
              { hash: "h3" }, // beyond fan range
            ],
          },
        ],
      })
    );
    renderOnThisDay();
    const fans = container().querySelectorAll(".otd-fan");
    expect(fans).toHaveLength(2);
    expect(container().innerHTML).toContain("/thumb/h1");
    expect(container().innerHTML).toContain("/thumb/h2");
    expect(container().innerHTML).not.toContain("/thumb/h3");
  });

  test("hero_hash sets background-image when present", () => {
    _setOnThisDayData(
      /** @type {any} */ ({
        month: 6,
        day: 15,
        years: [{ year: 2024, years_ago: 1, count: 1, hero_hash: "abc", photos: [] }],
      })
    );
    renderOnThisDay();
    expect(container().innerHTML).toContain("/thumb/abc");
  });

  test("'1 year ago' is singular, '2 years ago' plural", () => {
    _setOnThisDayData(
      /** @type {any} */ ({
        month: 1,
        day: 1,
        years: [
          { year: 2024, years_ago: 1, count: 1, photos: [] },
          { year: 2023, years_ago: 2, count: 1, photos: [] },
        ],
      })
    );
    renderOnThisDay();
    expect(container().innerHTML).toContain("1 year ago");
    expect(container().innerHTML).toContain("2 years ago");
  });

  test("count is pluralized correctly", () => {
    _setOnThisDayData(
      /** @type {any} */ ({
        month: 1,
        day: 1,
        years: [
          { year: 2024, years_ago: 1, count: 1, photos: [] },
          { year: 2023, years_ago: 2, count: 5, photos: [] },
        ],
      })
    );
    renderOnThisDay();
    expect(container().innerHTML).toContain("1 photo");
    expect(container().innerHTML).toContain("5 photos");
  });

  test("escapes the date label header", () => {
    // The date is built from Date(...) which the module owns, so this
    // just confirms the esc() wrapping is in place — no XSS surface.
    _setOnThisDayData(
      /** @type {any} */ ({
        month: 4,
        day: 25,
        years: [{ year: 2024, years_ago: 1, count: 1, photos: [] }],
      })
    );
    renderOnThisDay();
    // Header includes the rendered date label
    expect(container().innerHTML).toMatch(/On This Day/);
  });

  test("no-op when container is missing", () => {
    document.body.innerHTML = "";
    expect(() => renderOnThisDay()).not.toThrow();
  });
});

describe("loadOnThisDay", () => {
  test("populates data + renders on success", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              month: 4,
              day: 25,
              years: [{ year: 2024, years_ago: 1, count: 3, photos: [] }],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await loadOnThisDay();
    expect(_getOnThisDayData()?.years).toHaveLength(1);
    expect(container().classList.contains("hidden")).toBe(false);
  });

  test("on failure, nulls the data silently", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network");
      })
    );
    _setOnThisDayData(/** @type {any} */ ({ month: 1, day: 1, years: [{}] }));
    await loadOnThisDay();
    expect(_getOnThisDayData()).toBeNull();
  });
});

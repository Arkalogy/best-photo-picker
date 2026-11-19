// @ts-check
/**
 * Values that come from the library (its database, through the API) are
 * text someone else may have written, even where the page expects a
 * number, a date or a hash. Written into the page they must stay text:
 * no value may add an element of its own.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { thumbUrl } from "../bpp/web/static/js/modules/api-client.mjs";
import { renderCalendarGrid } from "../bpp/web/static/js/modules/calendar-render.mjs";
import { _renderFoldOut } from "../bpp/web/static/js/modules/editor-rendering.mjs";
import { updateLightboxTrim } from "../bpp/web/static/js/modules/lightbox-info.mjs";
import { _setMemoriesList, renderMemoriesSidebar } from "../bpp/web/static/js/modules/memories.mjs";
import { updatePersonAlbumBar } from "../bpp/web/static/js/modules/people-album-bar.mjs";
import { renderCardHTML } from "../bpp/web/static/js/modules/photos-card.mjs";
import { _setTimelineFilter, buildTimeline } from "../bpp/web/static/js/modules/timeline.mjs";

/** Breaks out of a double-quoted attribute and adds an element. */
const ATTR = 'x"><b id="vmark">x</b><i x="';
/** Adds an element where text is expected. */
const TEXT = '<b id="vmark">x</b>';
/** Breaks out of url(...) inside a style attribute. */
const URL = 'x)"><b id="vmark">x</b><i x="(';

const win = /** @type {any} */ (window);
const planted = () => document.getElementById("vmark");

beforeEach(() => {
  document.body.innerHTML = '<div id="host"></div>';
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete win.photos;
  delete win.editorEdits;
  delete win._activeAdjustSlider;
});

const host = () => /** @type {HTMLElement} */ (document.getElementById("host"));

describe("a library value never becomes an element", () => {
  test("photo card: date", () => {
    host().innerHTML = renderCardHTML(
      /** @type {any} */ ({
        filepath: "/x/a.jpg",
        filename: "a.jpg",
        thumb_hash: "ab",
        date: TEXT,
      }),
      0
    );
    expect(planted()).toBeNull();
    expect(host().querySelector(".card-date-stamp")?.textContent).toBe(TEXT);
  });

  test("photo card: thumbnail hash", () => {
    host().innerHTML = renderCardHTML(
      /** @type {any} */ ({ filepath: "/x/a.jpg", filename: "a.jpg", thumb_hash: ATTR }),
      0
    );
    expect(planted()).toBeNull();
    expect(host().querySelectorAll(".card-image img").length).toBe(1);
  });

  test("timeline bar: month", () => {
    document.body.innerHTML = '<div id="timeline-bar" class="hidden"></div>';
    _setTimelineFilter(null);
    win.photos = [{ date_month: "aaaa-bb" }, { date_month: `aaaa-cc${ATTR}` }];
    buildTimeline();
    expect(document.querySelectorAll(".tl-seg").length).toBe(2);
    expect(planted()).toBeNull();
  });

  test("memories: hero hash and photo count", () => {
    document.body.innerHTML = '<div id="memories-nav" class="hidden"></div>';
    _setMemoriesList(
      /** @type {any} */ ([{ id: ATTR, title: "t", photo_count: TEXT, hero_hash: URL }])
    );
    renderMemoriesSidebar();
    expect(document.querySelectorAll(".memory-card").length).toBe(1);
    expect(planted()).toBeNull();
  });

  test("calendar month: thumbnail hash", () => {
    document.body.innerHTML = '<div id="calendar-view"></div><div id="calendar-grid"></div>';
    const html = renderCalendarGrid(
      2024,
      2,
      /** @type {any} */ ([{ day: 5, count: 1, top_hash: URL }])
    );
    if (typeof html === "string") host().innerHTML = html;
    expect(planted()).toBeNull();
    expect(document.querySelectorAll(".cal-cell-thumb").length).toBe(1);
  });

  test("video trim: duration", () => {
    document.body.innerHTML = '<div id="lb-trim"></div>';
    updateLightboxTrim(/** @type {any} */ ({ is_video: true, video_duration: ATTR }));
    expect(planted()).toBeNull();
    expect(document.getElementById("lb-trim-end")).not.toBeNull();
  });

  test("person album bar: person id", () => {
    document.body.innerHTML = '<div id="person-album-bar" class="hidden"></div>';
    updatePersonAlbumBar(
      /** @type {any} */ ({
        id: 1,
        name: "P",
        album_type: "smart_person",
        rule: { cluster_id: ATTR },
      })
    );
    expect(planted()).toBeNull();
    expect(document.querySelectorAll(".people-filter-pill").length).toBeGreaterThan(5);
  });

  test("editor slider: saved value", () => {
    win.editorEdits = { brightness: ATTR };
    win._activeAdjustSlider = "brightness";
    host().innerHTML = ["Light", "Color", "Detail", "Effects"]
      .map((s) => _renderFoldOut(s))
      .join("");
    expect(planted()).toBeNull();
    expect(document.getElementById("es-brightness")).not.toBeNull();
  });
});

describe("a file path survives the trip through a data attribute", () => {
  // The card's actions (favorite, include, delete, context menu) read
  // the path back from the attribute. A name that holds a character
  // reference, an apostrophe or a backslash must come back unchanged,
  // or the action lands on another file (or on none).
  test.each(["/lib/Camera/&#73;MG_0001.jpg", "/lib/O'Brien/a.jpg", "/lib/back\\slash.jpg"])(
    "%s",
    (filepath) => {
      host().innerHTML = renderCardHTML(
        /** @type {any} */ ({ filepath, filename: "a.jpg", thumb_hash: "ab" }),
        0
      );
      const card = /** @type {HTMLElement} */ (host().querySelector("[data-filepath]"));
      expect(card.dataset.filepath).toBe(filepath);
      const actions = [...host().querySelectorAll("[data-action][data-arg0]")]
        .filter((el) =>
          /^(toggleFavorite|setOverride|deleteFromCard)$/.test(
            /** @type {HTMLElement} */ (el).dataset.action || ""
          )
        )
        .map((el) => /** @type {HTMLElement} */ (el).dataset.arg0);
      expect(actions).toEqual([filepath, filepath, filepath]);
    }
  );
});

describe("thumbUrl", () => {
  test("keeps an ordinary hash readable", () => {
    expect(thumbUrl("0a1b2c")).toMatch(/^\/thumb\/0a1b2c/);
  });

  test("encodes everything that could end an attribute or url()", () => {
    const url = thumbUrl(`a"b'c(d)e<f>g&h i`);
    expect(url).not.toMatch(/["'()<> ]/);
    expect(url.split("?")[0]).not.toContain("&");
  });
});

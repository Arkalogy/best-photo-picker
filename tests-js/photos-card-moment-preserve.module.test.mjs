// @ts-check
import { beforeEach, describe, expect, test } from "vitest";
import {
  _updateVisibleCards,
  updateCardInPlace,
  vgrid,
} from "../bpp/web/static/js/modules/photos.mjs";

beforeEach(() => {
  document.body.innerHTML =
    '<div id="photo-grid"><div class="card in-moment moment-keeper" data-idx="0">' +
    '<div class="card-actions"><button class="card-action"></button><button class="card-action"></button></div>' +
    '<div class="card-image"><div class="score-badge"></div></div></div></div>';
  /** @type {any} */ (vgrid).items = [
    {
      filepath: "a.jpg",
      filename: "a.jpg",
      moment_cluster_id: 4,
      moment_size: 10,
      aggregate_score: 0.8,
    },
  ];
  /** @type {any} */ (globalThis.window).momentKeepers = new Set(["a.jpg"]);
  /** @type {any} */ (globalThis.window).selectedPaths = new Set();
  /** @type {any} */ (globalThis.window).overrides = {};
  /** @type {any} */ (globalThis.window).favorites = new Set();
  /** @type {any} */ (globalThis.window).multiSelected = new Set();
  /** @type {any} */ (globalThis.window).photos = vgrid.items;
});

describe("in-place card updaters preserve Moment classes", () => {
  test("_updateVisibleCards keeps in-moment + moment-keeper (regression: frame vanished)", () => {
    _updateVisibleCards();
    const card = document.querySelector(".card");
    expect(card?.classList.contains("in-moment")).toBe(true);
    expect(card?.classList.contains("moment-keeper")).toBe(true);
  });

  test("updateCardInPlace keeps in-moment", () => {
    updateCardInPlace("a.jpg");
    const card = document.querySelector(".card");
    expect(card?.classList.contains("in-moment")).toBe(true);
  });

  test("a non-keeper card keeps in-moment but not moment-keeper", () => {
    /** @type {any} */ (globalThis.window).momentKeepers = new Set(["other.jpg"]);
    _updateVisibleCards();
    const card = document.querySelector(".card");
    expect(card?.classList.contains("in-moment")).toBe(true);
    expect(card?.classList.contains("moment-keeper")).toBe(false);
  });
});

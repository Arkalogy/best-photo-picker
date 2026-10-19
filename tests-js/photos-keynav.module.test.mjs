// @ts-check
// The photo grid is usable from the keyboard: one card is in the Tab
// order at a time, the arrow keys move that focus (Home / End jump), and
// the virtual grid keeps it across re-renders.
import { beforeEach, describe, expect, test, vi } from "vitest";

import {
  _bppCardKeydown,
  focusCard,
  nextCardIndex,
} from "../bpp/web/static/js/modules/photos-select.mjs";
import { renderCardHTML } from "../bpp/web/static/js/modules/photos-card.mjs";

describe("nextCardIndex", () => {
  const cases = [
    ["ArrowRight", 0, 1],
    ["ArrowRight", 8, null],
    ["ArrowLeft", 3, 2],
    ["ArrowLeft", 0, null],
    ["ArrowDown", 1, 4],
    ["ArrowDown", 7, null], // the last row is full: nowhere to go
    ["ArrowDown", 8, null],
    ["ArrowUp", 4, 1],
    ["ArrowUp", 1, null], // top row: stays
    ["ArrowUp", 0, null],
    ["Home", 5, 0],
    ["End", 5, 8],
    ["Enter", 5, null],
  ];
  test.each(cases)("%s from %i (3 columns, 9 cards) → %s", (key, idx, want) => {
    expect(nextCardIndex(String(key), Number(idx), 3, 9)).toBe(want);
  });
  test("an empty grid has nowhere to go", () => {
    expect(nextCardIndex("ArrowRight", 0, 3, 0)).toBeNull();
  });
  test("a short last row below: down lands on its last card", () => {
    expect(nextCardIndex("ArrowDown", 4, 3, 7)).toBe(6); // rows: 0-2, 3-5, 6
    expect(nextCardIndex("ArrowDown", 5, 3, 7)).toBe(6);
  });
});

describe("a rendered card", () => {
  test("is a button out of the Tab order until the grid hands it focus", () => {
    const html = renderCardHTML({ filepath: "a.jpg", filename: "a.jpg", thumb_hash: "h" }, 0);
    expect(html).toContain('role="group"'); // its action buttons stay exposed
    expect(html).toContain('data-key-activate="true"');
    expect(html).toContain('tabindex="-1"');
    expect(html).toContain('aria-label="a.jpg, Enter opens"');
    expect(html).toContain('data-onkeydown="_bppCardKeydown"');
  });
});

describe("focusCard / card keydown", () => {
  /** @type {any} */
  let vg;
  beforeEach(() => {
    document.body.innerHTML =
      '<div class="content"><div id="photo-grid">' +
      [0, 1, 2, 3, 4]
        .map((i) => `<div class="card" tabindex="-1" data-idx="${i}"></div>`)
        .join("") +
      "</div></div>";
    vg = { items: [1, 2, 3, 4, 5], cols: 2, rowHeight: 100, render: vi.fn() };
    /** @type {any} */ (window).vgrid = vg;
  });

  test("moves the single tabindex 0 and the focus to the card", () => {
    focusCard(3);
    const cards = document.querySelectorAll(".card");
    expect([...cards].map((c) => c.getAttribute("tabindex"))).toEqual([
      "-1",
      "-1",
      "-1",
      "0",
      "-1",
    ]);
    expect(document.activeElement).toBe(cards[3]);
    expect(vg.focusIdx).toBe(3);
  });

  test("an arrow key on a card moves the focus and is consumed", () => {
    const card = /** @type {HTMLElement} */ (document.querySelector('[data-idx="1"]'));
    const e = new KeyboardEvent("keydown", { key: "ArrowDown" });
    const prevent = vi.spyOn(e, "preventDefault");
    _bppCardKeydown.call(card, e);
    expect(document.activeElement).toBe(document.querySelector('[data-idx="3"]'));
    expect(prevent).toHaveBeenCalled();
  });

  test("Enter and Space open the card like a click", () => {
    const card = /** @type {HTMLElement} */ (document.querySelector('[data-idx="1"]'));
    const clicks = vi.fn();
    card.addEventListener("click", clicks);
    for (const key of ["Enter", " "]) {
      const e = new KeyboardEvent("keydown", { key });
      const prevent = vi.spyOn(e, "preventDefault");
      _bppCardKeydown.call(card, e);
      expect(prevent).toHaveBeenCalled();
    }
    expect(clicks).toHaveBeenCalledTimes(2);
  });

  test("the grid's keys are off while the viewer is open", () => {
    document.body.insertAdjacentHTML("beforeend", '<div id="lightbox" class="visible"></div>');
    const card = /** @type {HTMLElement} */ (document.querySelector('[data-idx="1"]'));
    const e = new KeyboardEvent("keydown", { key: "ArrowDown" });
    const prevent = vi.spyOn(e, "preventDefault");
    _bppCardKeydown.call(card, e);
    expect(prevent).not.toHaveBeenCalled();
    expect(document.activeElement).not.toBe(document.querySelector('[data-idx="3"]'));
  });

  test("a key that isn't navigation is left alone", () => {
    const card = /** @type {HTMLElement} */ (document.querySelector('[data-idx="1"]'));
    const e = new KeyboardEvent("keydown", { key: "a" });
    const prevent = vi.spyOn(e, "preventDefault");
    _bppCardKeydown.call(card, e);
    expect(prevent).not.toHaveBeenCalled();
  });

  test("scrolls the row into view so the virtual grid renders the card", () => {
    const content = /** @type {HTMLElement} */ (document.querySelector(".content"));
    Object.defineProperty(content, "clientHeight", { value: 150, configurable: true });
    content.scrollTop = 0;
    focusCard(4); // row 2 → 200-300px, below the 150px viewport
    expect(content.scrollTop).toBe(150);
    expect(vg.render).toHaveBeenCalledWith(false);
  });
});

describe("the virtual grid's Tab stop", () => {
  /** @type {any} */
  let vgrid;
  beforeEach(async () => {
    ({ vgrid } = await import("../bpp/web/static/js/modules/photos-vgrid.mjs"));
    document.body.innerHTML = '<div class="content"><div id="photo-grid"></div></div>';
    const content = /** @type {HTMLElement} */ (document.querySelector(".content"));
    Object.defineProperty(content, "clientHeight", { value: 200, configurable: true });
    /** @type {any} */ (window).selectedPaths = new Set();
    vgrid.items = Array.from({ length: 12 }, (_, i) => ({
      filepath: `p${i}.jpg`,
      filename: `p${i}.jpg`,
      thumb_hash: "h",
    }));
    vgrid.cols = 2;
    vgrid.rowHeight = 100;
    vgrid.totalRows = 6;
    vgrid.buffer = 0;
    vgrid.firstRow = -1;
    vgrid.lastRow = -1;
    vgrid.focusIdx = 0;
    vgrid._cardCache = new Map();
  });

  test("the focused card is the Tab stop; scrolled away, the first card in view is", () => {
    vgrid.render(true);
    expect(document.querySelector('.card[tabindex="0"]')?.getAttribute("data-idx")).toBe("0");
    const content = /** @type {HTMLElement} */ (document.querySelector(".content"));
    vgrid.buffer = 1; // one buffer row above the view is rendered too
    content.scrollTop = 400; // rows 4-5 in view; row 3 buffered; card 0 gone
    vgrid.render(true);
    const stop = document.querySelector('.card[tabindex="0"]');
    expect(stop?.getAttribute("data-idx")).toBe("8"); // row 4, not the buffer row
    expect(vgrid.focusIdx).toBe(8);
  });

  test("a keyboard user's focus survives the re-render that replaces its card", () => {
    vgrid.render(true);
    vgrid._keyNav = true;
    const card3 = /** @type {HTMLElement} */ (document.querySelector('.card[data-idx="3"]'));
    card3.focus();
    vgrid.render(true); // a full re-render replaces card 3
    expect(vgrid.focusIdx).toBe(3);
    expect(document.activeElement?.getAttribute("data-idx")).toBe("3");
  });

  test("a card the mouse focused is not chased when scrolling drops it", () => {
    vgrid.render(true);
    vgrid._keyNav = false;
    /** @type {HTMLElement} */ (document.querySelector('.card[data-idx="1"]')).focus();
    const content = /** @type {HTMLElement} */ (document.querySelector(".content"));
    content.scrollTop = 400;
    vgrid.render(true); // card 1 is gone; the Tab stop moves, focus does not hop
    expect(document.activeElement).toBe(document.body);
    expect(document.querySelector('.card[tabindex="0"]')).toBeTruthy();
  });

  test("a focused control inside a card keeps its focus across a re-render that keeps it", () => {
    vgrid.render(true);
    const btn = /** @type {HTMLElement} */ (
      document.querySelector('.card[data-idx="1"] .card-action')
    );
    btn.focus();
    vgrid.render(false); // same window: nothing replaced
    expect(document.activeElement).toBe(btn);
  });
});

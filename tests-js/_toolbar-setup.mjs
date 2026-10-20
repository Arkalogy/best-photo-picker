// @ts-check
// Shared setup of the toolbar suite (split for the 500-LOC cap).
import { vi } from "vitest";
import { _resetToolbarState } from "../bpp/web/static/js/modules/toolbar.mjs";

/** Per-test DOM + window state (the suite's former beforeEach). */
export function setup() {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div class="toolbar-right">
      <div class="toolbar-popover-anchor" id="sort-anchor">
        <button id="btn-sort"></button>
        <div id="sort-popover" class="toolbar-popover">
          <div class="popover-option" data-sort="date-asc"></div>
          <div class="popover-option" data-sort="date-desc"></div>
        </div>
      </div>
      <div class="toolbar-popover-anchor" id="filter-anchor">
        <button id="btn-filter"></button>
        <div id="filter-popover" class="toolbar-popover">
          <div class="popover-option" data-filter="all"></div>
          <div class="popover-option" data-filter="selected"></div>
        </div>
      </div>
      <input id="sort-by" value="date-desc" />
      <input id="filter-by" value="all" />
      <input id="param-k" type="number" value="50" />
      <input id="toolbar-k" type="number" value="50" />
      <button id="btn-search-toolbar"></button>
      <button id="btn-import-toolbar"></button>
      <button id="btn-analyze-toolbar"></button>
      <button id="btn-export"></button>
      <button id="btn-slideshow"></button>
      <button id="btn-settings-toolbar"></button>
      <button id="ss-play-btn"></button>
      <button id="ss-shuffle-btn"></button>
      <button id="ss-kb-btn"></button>
      <button id="ss-info-btn"></button>
      <div id="zoom-control"></div>
      <div id="toolbar-pick"><span id="toolbar-pick-scope"></span></div>
      <button id="toolbar-show-picks"></button>
      <div id="lib-stats"></div>
    </div>
  `;
  const localStorageStore = new Map();
  vi.stubGlobal("localStorage", {
    getItem: vi.fn((key) => (localStorageStore.has(key) ? localStorageStore.get(key) : null)),
    setItem: vi.fn((key, value) => localStorageStore.set(key, String(value))),
    removeItem: vi.fn((key) => localStorageStore.delete(key)),
    clear: vi.fn(() => localStorageStore.clear()),
  });
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).ICONS = {
    search: "<svg id='ic-search'/>",
    sort: "<svg id='ic-sort'/>",
    filter: "<svg id='ic-filter'/>",
    importArrow: "<svg id='ic-import'/>",
    analyze: "<svg id='ic-analyze'/>",
    exportArrow: "<svg id='ic-export'/>",
    ssSlideshow: "<svg id='ic-slideshow'/>",
    more: "<svg id='ic-more'/>",
    ssPause: "<svg id='ic-pause'/>",
    ssShuffle: "<svg id='ic-shuffle'/>",
    ssKenBurns: "<svg id='ic-kb'/>",
    ssInfo: "<svg id='ic-info'/>",
  };
  /** @type {any} */ (window).renderGrid = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {any} */ (window).updateCardInPlace = vi.fn(() => false);
  /** @type {any} */ (window).scheduleRecompute = vi.fn();
  /** @type {any} */ (window).updateOverrideStats = vi.fn();
  /** @type {any} */ (window).showToast = vi.fn();
  _resetToolbarState();
}

/** Undo setup() (the suite's former afterEach). */
export function teardown() {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "photos",
    "currentAlbumId",
    "currentView",
    "favorites",
    "selectedPaths",
    "ICONS",
    "renderGrid",
    "renderAlbumNav",
    "updateCardInPlace",
    "scheduleRecompute",
    "updateOverrideStats",
    "showToast",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetToolbarState();
}

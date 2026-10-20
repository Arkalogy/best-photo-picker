// @ts-check
// Shared setup of the photos suite (split for the 500-LOC cap).
import { vi } from "vitest";

/** Per-test DOM + window state (the suite's former beforeEach). */
export function setup() {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="photo-grid"></div>
    <div class="content"></div>
    <div id="toolbar-subtitle"></div>
    <input id="sort-by" />
    <input id="filter-by" />
    <input id="zoom-slider" type="range" min="40" max="300" value="80" />
    <span id="zoom-pct"></span>
    <div id="batch-bar">
      <span id="batch-count"></span>
      <button id="batch-compare-btn" style="display:none"></button>
    </div>
    <div id="album-picker-overlay">
      <div id="album-picker-list"></div>
      <input id="album-picker-new-name" />
    </div>
    <span id="override-stats"></span>
  `;
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).multiSelected = new Set();
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).sortedItems = [];
  /** @type {any} */ (window).lastMultiClickIdx = -1;
  /** @type {any} */ (window)._albumPickerFilepaths = [];
  /** @type {any} */ (window)._simClusterMap = {};
  /** @type {any} */ (window).ICONS = { paw: "<i>p</i>", pencil: "<i>e</i>", trash: "<i>t</i>" };
  /** @type {any} */ (window).openLightbox = vi.fn();
  /** @type {any} */ (window).updatePersonPhotoSelection = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
}

/** Undo setup() (the suite's former afterEach). */
export function teardown() {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "photos",
    "selectedPaths",
    "overrides",
    "favorites",
    "multiSelected",
    "albumList",
    "currentAlbumId",
    "currentGridItems",
    "sortedItems",
    "lastMultiClickIdx",
    "_albumPickerFilepaths",
    "_simClusterMap",
    "ICONS",
    "openLightbox",
    "updatePersonPhotoSelection",
    "renderAlbumNav",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
}

/**
 * @param {object} body
 */
export function jsonResp(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

export const samplePhoto = {
  filepath: "/a.jpg",
  filename: "a.jpg",
  thumb_hash: "h1",
  aggregate_score: 0.7,
  blur_score: 0.6,
  exposure_score: 0.7,
  face_score: 0.5,
  composition_score: 0.4,
};

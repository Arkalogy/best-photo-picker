// @ts-check
// Shared setup of the analysis suite (split for the 500-LOC cap).
import { vi } from "vitest";
import { _resetAnalysisState } from "../bpp/web/static/js/modules/analysis.mjs";

/** Per-test DOM + window state (the suite's former beforeEach). */
export function setup() {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <main>
      <div id="toast-container"></div>
      <div id="empty-state"></div>
      <div id="album-nav"></div>
      <div id="toolbar"></div>
      <div id="photo-grid"></div>
      <div id="status-bar">
        <div id="status-summary"></div>
        <div id="status-right"></div>
        <div id="status-progress" class="hidden">
          <div id="status-progress-text"></div>
          <div id="status-progress-fill"></div>
        </div>
      </div>
      <div id="analyzing-banner"></div>
      <button id="btn-analyze-toolbar"></button>
      <button id="btn-analyze"></button>
      <input id="input-dir" />
      <input id="param-k" type="number" value="50" />
      <input id="sort-by" value="date" />
      <input id="filter-by" value="all" />
    </main>
  `;
  /** @type {any} */ (globalThis).EventSource = class {
    constructor() {
      /** @type {any} */
      this.onmessage = null;
      /** @type {any} */
      this.onerror = null;
    }
    close() {}
  };
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).faceRecognitionAvailable = true;
  /** @type {any} */ (window).faceInstallable = false;
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).selectedFaceIds = new Set();
  /** @type {any} */ (window).activeOperation = null;
  /** @type {any} */ (window).recomputeTimer = null;
  /** @type {any} */ (window).renderGrid = vi.fn();
  /** @type {any} */ (window)._updateVisibleCards = vi.fn();
  /** @type {any} */ (window).updatePicksNavCount = vi.fn();
  /** @type {any} */ (window).cancelOperation = vi.fn();
  /** @type {any} */ (window).switchAlbum = vi.fn();
  /** @type {any} */ (window).loadAlbumList = vi.fn();
  /** @type {any} */ (window).switchToLibrary = vi.fn();
  /** @type {any} */ (window).hide = vi.fn();
  /** @type {any} */ (window).show = vi.fn();
  // ML-consent gate: pretend consent was already granted so
  // startReanalyze() doesn't block on appConfirm. Tests targeting
  // the consent flow itself can override these stubs.
  /** @type {any} */ (window).getSetting = vi.fn(() => "1");
  /** @type {any} */ (window).saveSetting = vi.fn();
  /** @type {any} */ (window).appConfirm = vi.fn(async () => true);
  _resetAnalysisState();
}

/** Undo setup() (the suite's former afterEach). */
export function teardown() {
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  for (const k of [
    "photos",
    "selectedPaths",
    "albumList",
    "currentAlbumId",
    "faceRecognitionAvailable",
    "faceInstallable",
    "favorites",
    "overrides",
    "selectedFaceIds",
    "activeOperation",
    "recomputeTimer",
    "renderGrid",
    "_updateVisibleCards",
    "updatePicksNavCount",
    "cancelOperation",
    "switchAlbum",
    "loadAlbumList",
    "switchToLibrary",
    "hide",
    "show",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetAnalysisState();
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

// @ts-check
// Shared setup of the lightbox suite (split for the 500-LOC cap).
import { vi } from "vitest";

/** Per-test DOM + window state (the suite's former beforeEach). */
export function setup() {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay"><div class="confirm-dialog"></div></div>
    <div id="album-picker-overlay"></div>
    <div id="settings-overlay"></div>
    <div id="export-modal-overlay"></div>
    <div id="import-modal-overlay"></div>
    <div id="modal-overlay"></div>
    <div id="wizard-overlay"></div>
    <div id="lightbox">
      <div class="lb-img-wrapper">
        <img id="lb-img" />
        <video id="lb-video" class="hidden"></video>
      </div>
      <div id="lightbox-panel" class="lightbox-panel">
        <div id="lb-panel-handle" class="lb-panel-handle" role="button" tabindex="0"></div>
        <div class="lb-header"></div>
      </div>
      <div id="lb-flash"></div>
      <div id="lb-action-text"></div>
      <div id="lb-zoom-level"></div>
      <span id="lb-filename"></span>
      <span id="lb-date"></span>
      <input id="lb-date-input" />
      <span id="lb-quality"></span>
      <div id="lb-scores"></div>
      <div id="lb-actions"></div>
      <button id="lb-top-include"></button>
      <button id="lb-top-exclude"></button>
      <button id="lb-top-fav"></button>
      <button id="lb-top-delete"></button>
      <button id="lb-undo" class="kb-undo"></button>
      <button id="editor-enhance-btn"></button>
      <div id="lb-faces"></div>
      <div id="lb-pets"></div>
      <div id="lb-tags"></div>
      <div id="lb-similar"></div>
      <div id="lb-exif"></div>
      <div id="lb-video-info"></div>
      <div id="lb-trim"></div>
      <div id="lb-map-container">
        <div id="lb-map-header"><span id="lb-map-toggle"></span></div>
        <div id="lb-map"></div>
      </div>
      <div id="lb-ctx-menu" class="hidden">
        <div class="ctx-menu-item" id="lb-ctx-include" data-action="include"></div>
        <div class="ctx-menu-item" id="lb-ctx-exclude" data-action="exclude"></div>
        <div class="ctx-menu-item" id="lb-ctx-fav" data-action="favorite"></div>
        <div class="ctx-menu-item" id="lb-ctx-enhance" data-action="enhance"></div>
        <div class="ctx-menu-item" id="lb-ctx-tag" data-action="tag-person"></div>
      </div>
    </div>
  `;
  /** @type {any} */ (window).lightboxIdx = -1;
  /** @type {any} */ (window).lbZoom = 1;
  /** @type {any} */ (window).lbPanX = 0;
  /** @type {any} */ (window).lbPanY = 0;
  /** @type {any} */ (window).LB_ZOOM_MIN = 1;
  /** @type {any} */ (window).LB_ZOOM_MAX = 10;
  /** @type {any} */ (window).SCORE_LABELS = {
    blur_score: "Sharpness",
    exposure_score: "Exposure",
    face_score: "Faces",
    composition_score: "Composition",
  };
  /** @type {any} */ (window).currentGridItems = [];
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).multiSelected = new Set();
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).editorActive = false;
  /** @type {any} */ (window).ICONS = { paw: "<i>p</i>" };
  /** @type {any} */ (window).hideCardCtxMenu = vi.fn();
  /** @type {any} */ (window).personDisplayName = vi.fn(() => null);
  /** @type {any} */ (window).petDisplayName = vi.fn(() => null);
  /** @type {any} */ (window).getPersonAlbumId = vi.fn(() => 100);
  /** @type {any} */ (window).showPersonCtxMenu = vi.fn();
  /** @type {any} */ (window).showPeopleView = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
}

/** Undo setup() (the suite's former afterEach). */
export function teardown() {
  document.body.innerHTML = "";
  vi.useRealTimers();
  vi.unstubAllGlobals();
  for (const k of [
    "lightboxIdx",
    "lbZoom",
    "lbPanX",
    "lbPanY",
    "LB_ZOOM_MIN",
    "LB_ZOOM_MAX",
    "SCORE_LABELS",
    "currentGridItems",
    "favorites",
    "overrides",
    "faceClusters",
    "albumList",
    "multiSelected",
    "selectedPaths",
    "editorActive",
    "ICONS",
    "hideCardCtxMenu",
    "personDisplayName",
    "petDisplayName",
    "getPersonAlbumId",
    "showPersonCtxMenu",
    "showPeopleView",
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
  id: 5,
  filepath: "/a.jpg",
  filename: "a.jpg",
  thumb_hash: "h1",
  date: "2024-01-01T12:00:00",
  aggregate_score: 0.7,
  blur_score: 0.6,
  exposure_score: 0.7,
  face_score: 0.5,
  composition_score: 0.4,
};

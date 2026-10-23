// @ts-check
// Shared setup of the albums suite (split for the 500-LOC cap).
import { vi } from "vitest";
import { onFilterChange } from "../bpp/web/static/js/modules/albums.mjs";

/** Per-test DOM + window state (the suite's former beforeEach). */
export function setup() {
  // Bridged to window so the tests can dispatch via the same path the
  // data-onchange="onFilterChange" attribute uses at runtime.
  /** @type {any} */ (window).onFilterChange = onFilterChange;
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay"><div class="confirm-dialog"></div></div>
    <aside class="sidebar">
      <div id="album-list"></div>
    </aside>
    <main>
      <div id="photo-grid"></div>
      <div id="people-view"></div>
      <div id="pets-view"></div>
      <div id="groups-view"></div>
      <div id="map-view"></div>
      <div id="calendar-view"></div>
      <div id="toolbar"></div>
      <div id="toolbar-title"></div>
      <div id="toolbar-subtitle"></div>
      <input id="filter-by" />
      <input id="sort-by" />
      <input id="toolbar-k" type="number" value="50" />
      <input id="param-k" type="number" value="50" />
    </main>
  `;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).petClusters = [];
  /** @type {any} */ (window).selectedPaths = new Set();
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).currentViewId = null;
  /** @type {any} */ (window).petsAvailable = false;
  /** @type {any} */ (window).sidebarFaceSort = "count";
  /** @type {any} */ (window).FACE_MIN_PHOTOS = 4;
  /** @type {any} */ (window).ICONS = {
    library: "<i>l</i>",
    people: "<i>p</i>",
    folder: "<i>f</i>",
    paw: "<i>pw</i>",
    group: "<i>g</i>",
    map: "<i>m</i>",
    calendar: "<i>c</i>",
    picks: "<i>pi</i>",
    heart: "<i>h</i>",
    hidden: "<i>hd</i>",
    trash: "<i>t</i>",
    star: "<i>s</i>",
    clock: "<i>cl</i>",
    inbox: "<i>in</i>",
    video: "<i>v</i>",
    screenshot: "<i>sc</i>",
    duplicate: "<i>du</i>",
    noFace: "<i>nf</i>",
    document: "<i>do</i>",
    pencil: "<i>pe</i>",
    tag: "<i>tg</i>",
  };
  /** @type {any} */ (window).isClusterExcluded = vi.fn(() => false);
  /** @type {any} */ (window).personDisplayName = vi.fn(() => null);
  /** @type {any} */ (window).updatePersonAlbumBar = vi.fn();
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
  document.getElementById("album-move-menu")?.remove();
  document.getElementById("smart-album-ctx")?.remove();
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "faceClusters",
    "petClusters",
    "selectedPaths",
    "favorites",
    "overrides",
    "photos",
    "currentAlbumId",
    "currentView",
    "currentViewId",
    "petsAvailable",
    "sidebarFaceSort",
    "FACE_MIN_PHOTOS",
    "ICONS",
    "isClusterExcluded",
    "personDisplayName",
    "updatePersonAlbumBar",
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

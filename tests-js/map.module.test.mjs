// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _getMapInstance,
  _initMap,
  _renderMapMarkers,
  _resetMapState,
  hideMapView,
  loadMapPhotos,
  navigateToMap,
  openMapPhoto,
} from "../bpp/web/static/js/modules/map.mjs";

/**
 * Minimal Leaflet mock — the tests only care about the call surface,
 * not actual rendering.
 *
 * @returns {any}
 */
function mockLeaflet() {
  /** @type {any} */
  const map = {
    setView: vi.fn().mockReturnThis(),
    addLayer: vi.fn(),
    removeLayer: vi.fn(),
    invalidateSize: vi.fn(),
    fitBounds: vi.fn(),
  };
  const tileLayer = vi.fn().mockReturnValue({ addTo: vi.fn().mockReturnThis() });
  const marker = vi.fn().mockReturnValue({ bindPopup: vi.fn().mockReturnThis() });
  const layerGroup = vi.fn().mockReturnValue({ addLayer: vi.fn() });
  const markerClusterGroup = vi.fn().mockReturnValue({ addLayer: vi.fn() });
  return {
    map: vi.fn().mockReturnValue(map),
    tileLayer,
    marker,
    layerGroup,
    markerClusterGroup,
    _map: map, // keep a handle for assertions
  };
}

beforeEach(() => {
  document.body.innerHTML = `
    <div id="map-view" class="hidden">
      <div id="map-container"></div>
    </div>
    <div id="toast-container"></div>
    <div id="toolbar-subtitle"></div>
    <div class="sidebar"></div>
  `;
  /** @type {any} */ (window).hide = vi.fn();
  /** @type {any} */ (window).show = vi.fn();
  /** @type {any} */ (window).updateToolbarTitle = vi.fn();
  /** @type {any} */ (window).updateToolbarForView = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {any} */ (window).toggleSidebar = vi.fn();
  /** @type {any} */ (window).openLightbox = vi.fn();
  _resetMapState();
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  delete (/** @type {any} */ (window).hide);
  delete (/** @type {any} */ (window).show);
  delete (/** @type {any} */ (window).updateToolbarTitle);
  delete (/** @type {any} */ (window).updateToolbarForView);
  delete (/** @type {any} */ (window).renderAlbumNav);
  delete (/** @type {any} */ (window).toggleSidebar);
  delete (/** @type {any} */ (window).openLightbox);
  delete (/** @type {any} */ (window).L);
  delete (/** @type {any} */ (window).currentView);
  delete (/** @type {any} */ (window).currentViewId);
  delete (/** @type {any} */ (window).currentAlbumId);
  delete (/** @type {any} */ (window).currentGridItems);
});

describe("navigateToMap", () => {
  test("toasts an error when Leaflet didn't load", () => {
    // No window.L set
    navigateToMap();
    const toastEl = document.querySelector("#toast-container .toast");
    expect(toastEl?.textContent).toContain("Map library failed to load");
  });

  test("sets view-state, hides other views, calls Leaflet init when L is loaded", () => {
    /** @type {any} */ (window).L = mockLeaflet();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ photos: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    navigateToMap();
    expect(/** @type {any} */ (window).currentView).toBe("map");
    expect(/** @type {any} */ (window).hide).toHaveBeenCalledWith("photo-grid");
    expect(/** @type {any} */ (window).show).toHaveBeenCalledWith("map-view");
    expect(/** @type {any} */ (window).updateToolbarTitle).toHaveBeenCalledWith("Map", "");
  });
});

describe("_initMap", () => {
  test("creates a Leaflet map instance and adds the OSM tile layer", () => {
    /** @type {any} */ (window).L = mockLeaflet();
    _initMap();
    expect(/** @type {any} */ (window).L.map).toHaveBeenCalled();
    expect(/** @type {any} */ (window).L.tileLayer).toHaveBeenCalledWith(
      "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      expect.objectContaining({ maxZoom: 19 })
    );
    expect(_getMapInstance()).toBeTruthy();
  });

  test("re-init detaches Leaflet from the container before creating a new map", () => {
    /** @type {any} */ (window).L = mockLeaflet();
    /** @type {any} */ (document.getElementById("map-container"))._leaflet_id = 99;
    _initMap();
    // _leaflet_id was deleted by re-init guard
    expect(
      /** @type {any} */ (document.getElementById("map-container"))._leaflet_id
    ).toBeUndefined();
  });

  test("no-op when L is undefined", () => {
    _initMap();
    expect(_getMapInstance()).toBeNull();
  });
});

describe("_renderMapMarkers", () => {
  test("no-op when no map instance", () => {
    _renderMapMarkers([{ id: 1, gps_lat: 0, gps_lon: 0 }]);
    // No throw is the assertion
  });

  test("creates a marker per photo with GPS, fits bounds when >1", () => {
    /** @type {any} */ (window).L = mockLeaflet();
    _initMap();
    _renderMapMarkers([
      { id: 1, gps_lat: 0, gps_lon: 0, filename: "a.jpg" },
      { id: 2, gps_lat: 1, gps_lon: 1, filename: "b.jpg" },
    ]);
    expect(/** @type {any} */ (window).L.marker).toHaveBeenCalledTimes(2);
    expect(_getMapInstance().fitBounds).toHaveBeenCalled();
  });

  test("setView for single-marker case, not fitBounds", () => {
    /** @type {any} */ (window).L = mockLeaflet();
    _initMap();
    /** @type {any} */ (window).L._map.setView.mockClear();
    _renderMapMarkers([{ id: 1, gps_lat: 50, gps_lon: -120, filename: "a.jpg" }]);
    expect(/** @type {any} */ (window).L._map.setView).toHaveBeenCalledWith([50, -120], 12);
    expect(/** @type {any} */ (window).L._map.fitBounds).not.toHaveBeenCalled();
  });

  test("skips photos missing GPS", () => {
    /** @type {any} */ (window).L = mockLeaflet();
    _initMap();
    _renderMapMarkers([
      { id: 1, gps_lat: null, gps_lon: null, filename: "no-gps.jpg" },
      { id: 2, gps_lat: 5, gps_lon: 5, filename: "ok.jpg" },
    ]);
    expect(/** @type {any} */ (window).L.marker).toHaveBeenCalledTimes(1);
  });

  test("uses MarkerCluster when available, layerGroup otherwise", () => {
    /** @type {any} */
    const L = mockLeaflet();
    /** @type {any} */ (window).L = L;
    _initMap();
    _renderMapMarkers([{ id: 1, gps_lat: 0, gps_lon: 0, filename: "a.jpg" }]);
    expect(L.markerClusterGroup).toHaveBeenCalled();
    expect(L.layerGroup).not.toHaveBeenCalled();

    // No clustering plugin → falls back
    L.markerClusterGroup = undefined;
    _renderMapMarkers([{ id: 2, gps_lat: 1, gps_lon: 1, filename: "b.jpg" }]);
    expect(L.layerGroup).toHaveBeenCalled();
  });
});

describe("loadMapPhotos", () => {
  test("hits /api/photos/map and updates subtitle with count", async () => {
    /** @type {any} */ (window).L = mockLeaflet();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              photos: [
                { id: 1, gps_lat: 0, gps_lon: 0, filename: "a.jpg" },
                { id: 2, gps_lat: 1, gps_lon: 1, filename: "b.jpg" },
                { id: 3, gps_lat: 2, gps_lon: 2, filename: "c.jpg" },
              ],
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    await loadMapPhotos();
    expect(document.getElementById("toolbar-subtitle").textContent).toBe("3 photos with location");
  });

  test("scopes URL to current album when set", async () => {
    /** @type {any} */ (window).L = mockLeaflet();
    /** @type {any} */ (window).currentAlbumId = 42;
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({ photos: [], has_more: false }), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);
    await loadMapPhotos();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/photos/map?limit=5000&offset=0&album_id=42",
      expect.any(Object)
    );
  });

  test("loops through pages until has_more is false", async () => {
    /** @type {any} */ (window).L = mockLeaflet();
    /** @type {any} */ (window).currentAlbumId = null;
    // Page 1: 3 photos with has_more=true → page 2: 2 photos with
    // has_more=false. The loop must concatenate both batches and stop.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            photos: [
              { id: 1, gps_lat: 0, gps_lon: 0, filename: "a.jpg" },
              { id: 2, gps_lat: 1, gps_lon: 1, filename: "b.jpg" },
              { id: 3, gps_lat: 2, gps_lon: 2, filename: "c.jpg" },
            ],
            has_more: true,
            total: 5,
            offset: 0,
            limit: 5000,
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            photos: [
              { id: 4, gps_lat: 3, gps_lon: 3, filename: "d.jpg" },
              { id: 5, gps_lat: 4, gps_lon: 4, filename: "e.jpg" },
            ],
            has_more: false,
            total: 5,
            offset: 3,
            limit: 5000,
          }),
          { status: 200, headers: { "content-type": "application/json" } }
        )
      );
    vi.stubGlobal("fetch", fetchMock);
    await loadMapPhotos();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(document.getElementById("toolbar-subtitle").textContent).toBe("5 photos with location");
  });

  test("toasts an error when fetch fails", async () => {
    /** @type {any} */ (window).L = mockLeaflet();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("boom");
      })
    );
    await loadMapPhotos();
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Couldn't load map photos"
    );
  });
});

describe("openMapPhoto", () => {
  test("opens the lightbox on the loaded photo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ id: 7, filepath: "/x.jpg" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await openMapPhoto(7);
    expect(/** @type {any} */ (window).currentGridItems).toEqual([{ id: 7, filepath: "/x.jpg" }]);
    expect(/** @type {any} */ (window).openLightbox).toHaveBeenCalledWith(0);
  });

  test("toasts when the photo can't be found", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({}), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await openMapPhoto(99);
    expect(document.querySelector("#toast-container .toast.error")?.textContent).toContain(
      "Photo not found"
    );
  });
});

describe("hideMapView", () => {
  test("calls hide('map-view')", () => {
    hideMapView();
    expect(/** @type {any} */ (window).hide).toHaveBeenCalledWith("map-view");
  });
});

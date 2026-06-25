// @ts-check
/**
 * Map view: Leaflet-backed map with markers for every photo that
 * has GPS coordinates. Loads `/api/v1/photos/map`, clusters markers
 * when MarkerCluster is available.
 *
 * Reads window.L (Leaflet from CDN) and several classic globals
 * (currentAlbumId / currentGridItems / hide / show / etc.). Bridged
 * onto window for inline onclick + classic-side callers (core.js
 * navigateTo dispatcher, albums.js sidebar tree).
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { formatDate } from "./date-format.mjs";
import { qualityLabel } from "./score-format.mjs";
import { esc } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

/** @type {any} */
let mapInstance = null;
/** @type {any} */
let _mapClusterGroup = null;

export function _getMapInstance() {
  return mapInstance;
}

/** @param {any} m */
export function _setMapInstance(m) {
  mapInstance = m;
}

export function _resetMapState() {
  mapInstance = null;
  _mapClusterGroup = null;
}

/**
 * Switch to the Map view. Sets the global view-state, hides the
 * grid + other view containers, shows the map. No-op when Leaflet
 * isn't loaded (CDN failure).
 */
export function navigateToMap() {
  /** @type {any} */
  const win = window;
  try {
    win.currentView = "map";
    win.currentViewId = null;
    const sb = document.querySelector(".sidebar");
    if (sb && sb.classList.contains("open")) win.toggleSidebar?.();

    win.hide?.("photo-grid");
    win.hide?.("people-view");
    win.hide?.("pets-view");
    win.hide?.("groups-view");
    win.hide?.("calendar-view");
    win.show?.("map-view");
    win.renderAlbumNav?.();
    win.updateToolbarTitle?.("Map", "");
    win.updateToolbarForView?.();

    if (typeof win.L === "undefined") {
      toast("Map library failed to load — check your internet connection", true); /* toast-ok: summary, not an error pattern */
      return;
    }
    loadMapPhotos();
  } catch (e) {
    console.error("navigateToMap failed:", e);
    toastError("load the map", e);
  }
}

/** Fetch /api/v1/photos/map (optionally scoped to current album) and re-render markers. */
export async function loadMapPhotos() {
  const container = document.getElementById("map-view");
  if (!container) return;

  /** @type {any} */
  const win = window;

  if (!mapInstance) {
    _initMap();
  }

  try {
    // Pagination: /api/v1/photos/map caps each response at
    // 5000 rows by default; loop until has_more is false so the map
    // shows every located photo even on big libraries. The default
    // server cap matches the photos endpoint so pages never exceed
    // 50k rows in flight.
    const params = win.currentAlbumId ? `&album_id=${win.currentAlbumId}` : "";
    const PAGE_SIZE = 5000;
    let offset = 0;
    /** @type {any[]} */
    const photos = [];
    while (true) {
      const url = `/api/v1/photos/map?limit=${PAGE_SIZE}&offset=${offset}${params}`;
      const data = await apiFetch(url);
      const page = data.photos || [];
      photos.push(...page);
      if (!data.has_more || page.length === 0) break;
      offset += page.length;
    }

    const subtitle = document.getElementById("toolbar-subtitle");
    if (subtitle) subtitle.textContent = photos.length + " photos with location";

    _renderMapMarkers(photos);
  } catch (e) {
    console.warn("Failed to load map photos:", e);
    toastError("load map photos", e);
  }
}

/**
 * Initialize the Leaflet map instance + tile layer. Idempotent —
 * detaches Leaflet from the container if it was already initialized.
 */
export function _initMap() {
  /** @type {any} */
  const win = window;
  const L = win.L;
  const el = /** @type {any} */ (document.getElementById("map-container"));
  if (!el || typeof L === "undefined") return;

  try {
    if (el._leaflet_id) {
      mapInstance = null;
      _mapClusterGroup = null;
      el.innerHTML = "";
      delete el._leaflet_id;
    }
    mapInstance = L.map(el, {
      zoomControl: true,
      attributionControl: true,
    }).setView([20, 0], 2);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
      maxZoom: 19,
      // Render a transparent 1x1 placeholder when a tile fails
      // (offline, OSM rate-limit, transient network blip) instead of
      // Leaflet's default broken-image icon. The map stays clean and
      // the user just sees missing geography rather than a row of red Xs.
      errorTileUrl:
        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=",
    }).addTo(mapInstance);

    setTimeout(() => {
      if (mapInstance) mapInstance.invalidateSize();
    }, 100);
  } catch (e) {
    console.error("Failed to initialize map:", e);
    mapInstance = null;
  }
}

/**
 * Replace the cluster group with a fresh set of markers from
 * `photos`. Auto-fits bounds; uses MarkerCluster when available.
 *
 * @param {Array<{
 *   id: number,
 *   filename?: string,
 *   gps_lat: number | null,
 *   gps_lon: number | null,
 *   thumb_hash?: string,
 *   aggregate_score?: number,
 *   date?: string | null,
 * }>} photos
 */
export function _renderMapMarkers(photos) {
  if (!mapInstance) return;

  /** @type {any} */
  const win = window;
  const L = win.L;

  if (_mapClusterGroup) {
    mapInstance.removeLayer(_mapClusterGroup);
    _mapClusterGroup = null;
  }

  if (photos.length === 0) return;

  const useCluster = typeof L.markerClusterGroup === "function";
  const group = useCluster
    ? L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 50 })
    : L.layerGroup();

  /** @type {[number, number][]} */
  const bounds = [];
  for (const p of photos) {
    const lat = p.gps_lat;
    const lon = p.gps_lon;
    if (lat == null || lon == null) continue;

    bounds.push([lat, lon]);

    const thumbUrl = p.thumb_hash ? authedSrc("/thumb/" + p.thumb_hash) : "";
    const score = p.aggregate_score || 0;
    const pct = (score * 100).toFixed(0);
    const q = qualityLabel(score);
    const dateStr = formatDate(p.date);

    let popupHtml =
      '<div class="map-popup" data-photo-id="' +
      p.id +
      '" style="cursor:pointer" data-action="openMapPhoto" data-arg0="' +
      p.id +
      '">';
    if (thumbUrl) {
      popupHtml += '<img src="' + thumbUrl + '" class="map-popup-img" loading="lazy">';
    }
    popupHtml += '<div class="map-popup-info">';
    popupHtml += '<div class="map-popup-name">' + esc(p.filename || "") + "</div>";
    if (dateStr) popupHtml += '<div class="map-popup-date">' + esc(dateStr) + "</div>";
    popupHtml +=
      '<div class="map-popup-score" style="color:' +
      q.color +
      '">' +
      pct +
      "% &middot; " +
      q.text +
      "</div>";
    popupHtml += "</div></div>";

    const marker = L.marker([lat, lon]).bindPopup(popupHtml, {
      maxWidth: 240,
      minWidth: 160,
    });
    group.addLayer(marker);
  }

  mapInstance.addLayer(group);
  _mapClusterGroup = group;

  if (bounds.length > 0) {
    if (bounds.length === 1) {
      mapInstance.setView(bounds[0], 12);
    } else {
      mapInstance.fitBounds(bounds, { padding: [40, 40] });
    }
  }
}

/**
 * Open a single photo in the lightbox by ID. Replaces the current
 * grid items with a single-item array so the lightbox can navigate.
 *
 * @param {number} photoId
 */
export async function openMapPhoto(photoId) {
  /** @type {any} */
  const win = window;
  try {
    const data = await apiFetch("/api/v1/photos/" + photoId);
    if (!data || !data.filepath) {
      toast("Photo not found", true);
      return;
    }
    win.currentGridItems = [data];
    win.openLightbox?.(0);
  } catch (e) {
    console.error("openMapPhoto failed:", e);
    toastError("open the photo", e);
  }
}

/** Hide the map view (preserves the map instance for faster re-show). */
export function hideMapView() {
  /** @type {any} */ (window).hide?.("map-view");
}

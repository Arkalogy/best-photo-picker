// @ts-check
/**
 * Lightbox info panel: EXIF + map + video info + trim controls.
 *
 * Extracted from lightbox.mjs during the v0.1 cleanup. These ten
 * functions update the "info" surfaces of the lightbox (EXIF strip,
 * inline Leaflet map, video duration / dimensions, video trim
 * controls) from a photo dict. They were ~320 LOC inside the 3085-LOC
 * lightbox.mjs and had a clean boundary — no shared state with the
 * face-overlay / face-edit / zoom / action-bar surfaces beyond the
 * window-level `_lbLeafletMap` and `_lbMapMarker` already declared
 * globally in globals.js.
 *
 * Two module-locals that travel with the functions:
 *   `_lbMapExpanded`     — expand/collapse state for the map
 *   `_lbZoomControl`     — handle to the Leaflet zoom-control toggled
 *                          alongside drag/scroll interactions
 *
 * Re-exported from lightbox.mjs so existing call sites
 * (`updateLightboxExif`, `toggleLightboxMap`, etc. on `window` via
 * the modules-bridge in index.html) keep working unchanged.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc } from "./text-format.mjs";
import { _formatDuration } from "./photos.mjs";
import { toast, toastError } from "./toast.mjs";

let _lbMapExpanded = false;


export function updateLightboxExif(p) {
  const container = document.getElementById("lb-exif");
  if (!container) return;
  const exif = p.exif;
  if (!exif || Object.keys(exif).length === 0) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");
  const rows = [];
  // Panel-cleanup item 3: one "Info" section covers camera + size, with the
  // location map attached directly below (same section, no second header).
  // Raw GPS coordinates are intentionally NOT listed — the map pin carries
  // them (and updateLightboxMap puts the numbers in the map's tooltip).
  rows.push('<div class="lb-exif-label">Info</div>');
  /** @type {Array<[string, any]>} */
  const fields = [
    ["Camera", _exifCamera(exif)],
    ["Lens", exif.lens],
    ["Focal Length", exif.focal_length ? exif.focal_length + "mm" : null],
    ["Aperture", exif.aperture ? "ƒ/" + exif.aperture : null],
    ["Shutter", exif.shutter_speed],
    ["ISO", exif.iso],
    ["Size", _exifSize(exif)],
  ];
  for (const [key, val] of fields) {
    if (val != null && val !== "") {
      rows.push(
        '<span class="lb-exif-key">' +
          esc(key) +
          "</span>" +
          '<span class="lb-exif-val">' +
          esc(String(val)) +
          "</span>"
      );
    }
  }
  container.innerHTML = rows.join("");
}

function _setMapInteractions(enabled) {
  /** @type {any} */
  const win = window;
  if (!win._lbLeafletMap) return;
  const map = win._lbLeafletMap;
  const L = win.L;
  if (enabled) {
    map.dragging.enable();
    map.scrollWheelZoom.enable();
    map.doubleClickZoom.enable();
    map.touchZoom.enable();
    map.boxZoom.enable();
    map.keyboard.enable();
    // The zoom control lives ON the map instance, never module-cached:
    // the map is destroyed/recreated when the editor swaps the panel,
    // and a control created for a dead map renders but its +/− clicks
    // are bound to torn-down internals ("map zoom doesn't work").
    if (L && !map._bppZoomControl) {
      map._bppZoomControl = L.control.zoom({ position: "topright" });
      map.addControl(map._bppZoomControl);
    }
  } else {
    map.dragging.disable();
    map.scrollWheelZoom.disable();
    map.doubleClickZoom.disable();
    map.touchZoom.disable();
    map.boxZoom.disable();
    map.keyboard.disable();
    if (map._bppZoomControl) {
      map.removeControl(map._bppZoomControl);
      map._bppZoomControl = null;
    }
  }
}

export function toggleLightboxMap() {
  /** @type {any} */
  const win = window;
  const wrap = document.getElementById("lb-map-container");
  const toggle = document.getElementById("lb-map-toggle");
  if (!wrap || !win._lbLeafletMap) return;
  _lbMapExpanded = !_lbMapExpanded;
  wrap.classList.toggle("expanded", _lbMapExpanded);
  if (toggle) toggle.textContent = _lbMapExpanded ? "Collapse" : "Expand";
  _setMapInteractions(_lbMapExpanded);
  setTimeout(() => win._lbLeafletMap.invalidateSize(), 350);
}

function _collapseLightboxMap() {
  const wrap = document.getElementById("lb-map-container");
  const toggle = document.getElementById("lb-map-toggle");
  if (_lbMapExpanded) {
    _lbMapExpanded = false;
    if (wrap) wrap.classList.remove("expanded");
    if (toggle) toggle.textContent = "Expand";
    _setMapInteractions(false);
  }
}

/**
 * @param {any} p
 */
export function updateLightboxMap(p) {
  /** @type {any} */
  const win = window;
  const wrap = document.getElementById("lb-map-container");
  if (!wrap) return;
  const exif = p.exif;
  const L = win.L;
  if (!exif || exif.gps_lat == null || exif.gps_lon == null || typeof L === "undefined") {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");
  _collapseLightboxMap();
  const lat = exif.gps_lat;
  const lon = exif.gps_lon;
  // Coordinates live here (tooltip) now that the raw GPS text row is gone
  // from the Info list — the pin is the primary representation.
  wrap.title = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;

  if (!win._lbLeafletMap) {
    win._lbLeafletMap = L.map("lb-map", {
      zoomControl: false,
      attributionControl: false,
      dragging: false,
      scrollWheelZoom: false,
      doubleClickZoom: false,
      boxZoom: false,
      keyboard: false,
      touchZoom: false,
    }).setView([lat, lon], 13);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(
      win._lbLeafletMap
    );
    win._lbMapMarker = L.marker([lat, lon]).addTo(win._lbLeafletMap);
    // NOTE: the Expand toggle is wired via data-action="toggleLightboxMap"
    // in the markup (index.html + editor-rendering's panel copy), NOT a
    // direct listener here — a listener attached to the header node dies
    // silently when the editor rebuilds the panel's innerHTML. That was
    // the "Expand doesn't work" bug.
  } else {
    win._lbLeafletMap.setView([lat, lon], 13);
    win._lbMapMarker.setLatLng([lat, lon]);
  }
  setTimeout(() => win._lbLeafletMap.invalidateSize(), 100);
}

/**
 * @param {any} p
 */
export function updateLightboxVideoInfo(p) {
  const container = document.getElementById("lb-video-info");
  if (!container) return;
  if (!p.is_video) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");
  const rows = [];
  rows.push('<div class="lb-exif-label">Video Info</div>');
  /** @type {Array<[string, any]>} */
  const fields = [
    ["Duration", p.video_duration != null ? _formatDuration(p.video_duration) : null],
    [
      "Resolution",
      p.video_width && p.video_height ? p.video_width + " × " + p.video_height : null,
    ],
    ["Frame Rate", p.video_fps ? p.video_fps.toFixed(1) + " fps" : null],
    ["Codec", p.video_codec ? p.video_codec.toUpperCase() : null],
  ];
  for (const [key, val] of fields) {
    if (val != null && val !== "") {
      rows.push(
        '<span class="lb-exif-key">' +
          esc(key) +
          "</span>" +
          '<span class="lb-exif-val">' +
          esc(String(val)) +
          "</span>"
      );
    }
  }
  container.innerHTML = rows.join("");
}

/**
 * @param {any} p
 */
export function updateLightboxTrim(p) {
  const container = document.getElementById("lb-trim");
  if (!container) return;
  if (!p.is_video || !p.video_duration || p.video_duration <= 0) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");
  const dur = p.video_duration;
  const durStr = _formatDuration(dur);
  container.innerHTML = `
    <div class="lb-trim-label">Trim Video</div>
    <div class="lb-trim-range">
      <span class="lb-trim-time" id="lb-trim-start-val">0:00</span>
      <input type="range" id="lb-trim-start" min="0" max="${dur}" step="0.1" value="0">
      <input type="range" id="lb-trim-end" min="0" max="${dur}" step="0.1" value="${dur}">
      <span class="lb-trim-time" id="lb-trim-end-val">${esc(durStr)}</span>
    </div>
    <div class="lb-trim-actions">
      <button class="lb-trim-btn" id="lb-trim-apply" data-action="lbTrimVideo">Trim</button>
      <button class="lb-trim-btn lb-trim-btn--secondary" data-action="lbTrimPreview">Preview</button>
    </div>`;

  const startInput = /** @type {HTMLInputElement | null} */ (
    document.getElementById("lb-trim-start")
  );
  const endInput = /** @type {HTMLInputElement | null} */ (
    document.getElementById("lb-trim-end")
  );
  if (!startInput || !endInput) return;
  startInput.addEventListener("input", () => {
    const sv = parseFloat(startInput.value);
    if (sv >= parseFloat(endInput.value))
      startInput.value = String(parseFloat(endInput.value) - 0.1);
    const lbl = document.getElementById("lb-trim-start-val");
    if (lbl) lbl.textContent = _formatDuration(parseFloat(startInput.value));
  });
  endInput.addEventListener("input", () => {
    const ev = parseFloat(endInput.value);
    if (ev <= parseFloat(startInput.value))
      endInput.value = String(parseFloat(startInput.value) + 0.1);
    const lbl = document.getElementById("lb-trim-end-val");
    if (lbl) lbl.textContent = _formatDuration(parseFloat(endInput.value));
  });
}

export async function lbTrimVideo() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) return;
  const p = items[win.lightboxIdx];
  const startEl = /** @type {HTMLInputElement | null} */ (
    document.getElementById("lb-trim-start")
  );
  const endEl = /** @type {HTMLInputElement | null} */ (document.getElementById("lb-trim-end"));
  if (!startEl || !endEl) return;
  const start = parseFloat(startEl.value);
  const end = parseFloat(endEl.value);
  if (start >= end) {
    toast("Invalid trim range", true);
    return;
  }

  const btn = /** @type {HTMLButtonElement | null} */ (document.getElementById("lb-trim-apply"));
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Trimming…";
  }

  const ok = await appConfirm("Trim this video? The original will be replaced.");
  if (!ok) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Trim";
    }
    return;
  }

  try {
    const data = await apiFetch("/api/v1/video/trim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filepath: p.filepath, start, end }),
    });

    if (btn) {
      btn.disabled = false;
      btn.textContent = "Trim";
    }

    if (data.duration != null) p.video_duration = data.duration;
    toast("Video trimmed!");
    const video = /** @type {HTMLVideoElement | null} */ (document.getElementById("lb-video"));
    if (video) video.src = authedSrc("/video/" + p.thumb_hash + "?t=" + Date.now());
    updateLightboxVideoInfo(p);
    updateLightboxTrim(p);
  } catch (e) {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Trim";
    }
    toastError("trim this video", e);
  }
}

export function lbTrimPreview() {
  const video = /** @type {HTMLVideoElement | null} */ (document.getElementById("lb-video"));
  if (!video) return;
  const startEl = /** @type {HTMLInputElement | null} */ (
    document.getElementById("lb-trim-start")
  );
  const endEl = /** @type {HTMLInputElement | null} */ (document.getElementById("lb-trim-end"));
  if (!startEl || !endEl) return;
  const start = parseFloat(startEl.value);
  video.currentTime = start;
  video.play().catch((e) => console.warn("Video trim preview play blocked:", e));
  const end = parseFloat(endEl.value);
  const onTime = () => {
    if (video.currentTime >= end) {
      video.pause();
      video.removeEventListener("timeupdate", onTime);
    }
  };
  video.addEventListener("timeupdate", onTime);
}

/**
 * @param {any} exif
 */
function _exifCamera(exif) {
  const parts = [];
  if (exif.camera_make) parts.push(exif.camera_make);
  if (exif.camera_model) {
    let model = exif.camera_model;
    if (exif.camera_make && model.startsWith(exif.camera_make)) {
      model = model.slice(exif.camera_make.length).trim();
    }
    parts.push(model);
  }
  return parts.length > 0 ? parts.join(" ") : null;
}

/**
 * @param {any} exif
 */
function _exifSize(exif) {
  if (!exif.width || !exif.height) return null;
  const mp = ((exif.width * exif.height) / 1e6).toFixed(1);
  return exif.width + " × " + exif.height + " (" + mp + " MP)";
}

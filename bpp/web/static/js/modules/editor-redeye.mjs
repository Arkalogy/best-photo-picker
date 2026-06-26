// @ts-check
/**
 * Red-eye correction overlay for the lightbox editor.
 *
 * State lives on the still-classic `editor.js` (`_redeyeMode` flag,
 * `editorEdits` object). We read/write both via window — when
 * editor.js itself migrates, those can become real imports.
 *
 * Bridged onto window for editor.js's inline onclick handlers.
 */

import { toast } from "./toast.mjs";

/**
 * Toggle red-eye click-to-fix mode. Mounts the overlay above the
 * lightbox image while active.
 */
export function _toggleRedeyeMode() {
  /** @type {any} */
  const win = window;
  win._redeyeMode = !win._redeyeMode;
  const btn = document.querySelector(".editor-btn-redeye");
  if (btn) btn.classList.toggle("active", win._redeyeMode);

  if (win._redeyeMode) {
    _showRedeyeOverlay();
    toast("Click on red eyes to fix them");
  } else {
    _removeRedeyeOverlay();
  }
}

/** Mount the overlay. Renders existing red-eye markers immediately. */
export function _showRedeyeOverlay() {
  _removeRedeyeOverlay();
  const wrapper = document.querySelector(".lb-img-wrapper");
  if (!wrapper) return;

  const overlay = document.createElement("div");
  overlay.id = "redeye-overlay";
  overlay.className = "redeye-overlay";
  overlay.addEventListener("click", _redeyeClick);
  wrapper.appendChild(overlay);

  _renderRedeyeMarkers();
}

export function _removeRedeyeOverlay() {
  document.getElementById("redeye-overlay")?.remove();
}

/**
 * Click handler that adds a red-eye point at normalized (0..1)
 * coordinates derived from the click position.
 *
 * @param {MouseEvent} e
 */
export function _redeyeClick(e) {
  const overlay = /** @type {HTMLElement} */ (e.currentTarget);
  const rect = overlay.getBoundingClientRect();
  const x = (e.clientX - rect.left) / rect.width;
  const y = (e.clientY - rect.top) / rect.height;

  /** @type {any} */
  const win = window;
  const edits = win.editorEdits || (win.editorEdits = {});
  if (!edits.redeye_points) edits.redeye_points = [];
  edits.redeye_points.push({
    x: Math.round(x * 1000) / 1000,
    y: Math.round(y * 1000) / 1000,
    radius: 0.03,
  });

  _renderRedeyeMarkers();
  win._refreshStylesTab?.();
}

/** Re-render every marker as a small overlay child with click-to-remove. */
export function _renderRedeyeMarkers() {
  const overlay = document.getElementById("redeye-overlay");
  if (!overlay) return;

  overlay.querySelectorAll(".redeye-marker").forEach((m) => m.remove());

  /** @type {any} */
  const win = window;
  const edits = win.editorEdits || {};
  const points = /** @type {{x:number,y:number,radius:number}[]} */ (
    edits.redeye_points || []
  );
  for (let i = 0; i < points.length; i++) {
    const pt = points[i];
    const marker = document.createElement("div");
    marker.className = "redeye-marker";
    marker.style.left = pt.x * 100 + "%";
    marker.style.top = pt.y * 100 + "%";
    marker.title = "Click to remove";
    marker.addEventListener("click", (e) => {
      e.stopPropagation();
      const cur = win.editorEdits?.redeye_points;
      if (Array.isArray(cur)) {
        cur.splice(i, 1);
        if (cur.length === 0) win.editorEdits.redeye_points = null;
      }
      _renderRedeyeMarkers();
      win._refreshStylesTab?.();
    });
    overlay.appendChild(marker);
  }
}

export function _clearRedeyePoints() {
  /** @type {any} */
  const win = window;
  if (win.editorEdits) win.editorEdits.redeye_points = null;
  _renderRedeyeMarkers();
  win._refreshStylesTab?.();
}

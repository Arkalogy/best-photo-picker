// @ts-check
/**
 * Crop overlay for the photo editor — draggable crop box with corner +
 * edge handles, aspect-ratio constraints, screen→local rotation/flip
 * compensation. Self-attaches mousedown/touchstart on the overlay only
 * when it's mounted (lazy via `_showCropOverlay`).
 *
 * Reads/writes editor state via `window` (`editorEdits`,
 * `editorCropActive`, `_cropDragging`, `_cropStartX/Y`, `_cropStartRect`,
 * `_editorAspectRatio`) — these moved to globals.js as bare `window.X`
 * declarations so the still-classic editor.js + this module can share
 * them via the global-object scope-chain fallback. Calls
 * `_renderCropControls()` (still classic in editor.js) via window.
 */

/**
 * @typedef {{
 *   crop_x?: number | null, crop_y?: number | null,
 *   crop_w?: number | null, crop_h?: number | null,
 *   rotation?: number, straighten?: number,
 *   flip_h?: boolean, flip_v?: boolean,
 * }} EditorEdits
 */

/** @returns {EditorEdits} */
function _edits() {
  /** @type {any} */
  const win = window;
  return /** @type {EditorEdits} */ (win.editorEdits || (win.editorEdits = {}));
}

export function _toggleCropOverlay() {
  /** @type {any} */
  const win = window;
  if (win.editorCropActive) {
    _applyCropFromOverlay();
    _removeCropOverlay();
    win.editorCropActive = false;
  } else {
    _showCropOverlay();
    win.editorCropActive = true;
  }
  const btn = document.getElementById("editor-crop-toggle");
  if (btn) btn.textContent = win.editorCropActive ? "Apply Crop" : "Start Crop";
}

/**
 * @param {number | "original"} ratio
 * @param {HTMLElement} [btn]
 */
export function _setAspectRatio(ratio, btn) {
  /** @type {any} */
  const win = window;
  if (ratio === "original") {
    const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
    if (img && img.naturalWidth && img.naturalHeight) {
      win._editorAspectRatio = img.naturalWidth / img.naturalHeight;
    }
  } else {
    win._editorAspectRatio = ratio;
  }
  document
    .querySelectorAll(".editor-aspect-pill")
    .forEach((p) => p.classList.remove("active"));
  if (btn) btn.classList.add("active");
  if (win.editorCropActive) _adjustCropToAspect();
}

export function _showCropOverlay() {
  _removeCropOverlay();
  const wrapper = document.querySelector(".lb-img-wrapper");
  const img = document.getElementById("lb-img");
  if (!wrapper || !img) return;

  const overlay = document.createElement("div");
  overlay.className = "crop-overlay";
  overlay.id = "crop-overlay";

  const e = _edits();
  const cx = e.crop_x != null ? e.crop_x : 0;
  const cy = e.crop_y != null ? e.crop_y : 0;
  const cw = e.crop_w != null ? e.crop_w : 1;
  const ch = e.crop_h != null ? e.crop_h : 1;

  overlay.innerHTML = `
    <div class="crop-dim crop-dim-top" style="top:0;left:0;right:0;height:${cy * 100}%"></div>
    <div class="crop-dim crop-dim-left" style="top:${cy * 100}%;left:0;width:${cx * 100}%;height:${ch * 100}%"></div>
    <div class="crop-dim crop-dim-right" style="top:${cy * 100}%;right:0;width:${(1 - cx - cw) * 100}%;height:${ch * 100}%"></div>
    <div class="crop-dim crop-dim-bottom" style="bottom:0;left:0;right:0;height:${(1 - cy - ch) * 100}%"></div>
    <div class="crop-box" id="crop-box" style="left:${cx * 100}%;top:${cy * 100}%;width:${cw * 100}%;height:${ch * 100}%">
      <div class="crop-grid"></div>
      <div class="crop-handle crop-handle-nw" data-handle="nw"></div>
      <div class="crop-handle crop-handle-ne" data-handle="ne"></div>
      <div class="crop-handle crop-handle-sw" data-handle="sw"></div>
      <div class="crop-handle crop-handle-se" data-handle="se"></div>
      <div class="crop-handle crop-handle-n" data-handle="n"></div>
      <div class="crop-handle crop-handle-s" data-handle="s"></div>
      <div class="crop-handle crop-handle-e" data-handle="e"></div>
      <div class="crop-handle crop-handle-w" data-handle="w"></div>
    </div>
  `;

  wrapper.appendChild(overlay);
  overlay.addEventListener("mousedown", _cropMouseDown);
  overlay.addEventListener("touchstart", _cropTouchStart, { passive: false });
}

export function _removeCropOverlay() {
  document.getElementById("crop-overlay")?.remove();
}

export function _adjustCropToAspect() {
  /** @type {any} */
  const win = window;
  const box = document.getElementById("crop-box");
  if (!box || !win._editorAspectRatio) return;

  const overlay = box.parentElement;
  if (!overlay) return;
  const ow = overlay.offsetWidth;
  const oh = overlay.offsetHeight;
  if (ow === 0 || oh === 0) return;

  let bw = box.offsetWidth / ow;
  let bh = box.offsetHeight / oh;
  const bx = box.offsetLeft / ow;
  const by = box.offsetTop / oh;

  const currentAspect = (bw * ow) / (bh * oh);
  const targetAspect = win._editorAspectRatio;

  if (currentAspect > targetAspect) {
    bw = (bh * oh * targetAspect) / ow;
  } else {
    bh = ((bw * ow) / targetAspect) / oh;
  }

  const nx = Math.min(bx, 1 - bw);
  const ny = Math.min(by, 1 - bh);
  _setCropBox(nx, ny, bw, bh);
}

/**
 * @param {number} cx
 * @param {number} cy
 * @param {number} cw
 * @param {number} ch
 */
export function _setCropBox(cx, cy, cw, ch) {
  const box = /** @type {HTMLElement | null} */ (document.getElementById("crop-box"));
  const overlay = /** @type {HTMLElement | null} */ (document.getElementById("crop-overlay"));
  if (!box || !overlay) return;

  cx = Math.max(0, Math.min(1 - cw, cx));
  cy = Math.max(0, Math.min(1 - ch, cy));
  cw = Math.max(0.05, Math.min(1, cw));
  ch = Math.max(0.05, Math.min(1, ch));

  box.style.left = cx * 100 + "%";
  box.style.top = cy * 100 + "%";
  box.style.width = cw * 100 + "%";
  box.style.height = ch * 100 + "%";

  const dims = overlay.querySelectorAll(".crop-dim");
  /** @type {HTMLElement} */ (dims[0]).style.height = cy * 100 + "%";
  const left = /** @type {HTMLElement} */ (dims[1]);
  left.style.top = cy * 100 + "%";
  left.style.width = cx * 100 + "%";
  left.style.height = ch * 100 + "%";
  const right = /** @type {HTMLElement} */ (dims[2]);
  right.style.top = cy * 100 + "%";
  right.style.width = (1 - cx - cw) * 100 + "%";
  right.style.height = ch * 100 + "%";
  /** @type {HTMLElement} */ (dims[3]).style.height = (1 - cy - ch) * 100 + "%";
}

export function _applyCropFromOverlay() {
  const box = /** @type {HTMLElement | null} */ (document.getElementById("crop-box"));
  const overlay = /** @type {HTMLElement | null} */ (document.getElementById("crop-overlay"));
  if (!box || !overlay) return;

  const ow = overlay.offsetWidth;
  const oh = overlay.offsetHeight;
  if (ow === 0 || oh === 0) return;

  const cx = box.offsetLeft / ow;
  const cy = box.offsetTop / oh;
  const cw = box.offsetWidth / ow;
  const ch = box.offsetHeight / oh;

  const e = _edits();
  if (cx < 0.01 && cy < 0.01 && cw > 0.98 && ch > 0.98) {
    e.crop_x = null;
    e.crop_y = null;
    e.crop_w = null;
    e.crop_h = null;
  } else {
    e.crop_x = Math.round(cx * 1000) / 1000;
    e.crop_y = Math.round(cy * 1000) / 1000;
    e.crop_w = Math.round(cw * 1000) / 1000;
    e.crop_h = Math.round(ch * 1000) / 1000;
  }
}

export function _clearCrop() {
  /** @type {any} */
  const win = window;
  const e = _edits();
  e.crop_x = null;
  e.crop_y = null;
  e.crop_w = null;
  e.crop_h = null;
  _removeCropOverlay();
  win.editorCropActive = false;
  const cropTab = document.getElementById("editor-crop-tab");
  if (cropTab && typeof win._renderCropControls === "function") {
    cropTab.innerHTML = win._renderCropControls();
  }
}

/**
 * @param {MouseEvent} e
 */
function _cropMouseDown(e) {
  /** @type {any} */
  const win = window;
  const target = /** @type {HTMLElement} */ (e.target);
  const handle = /** @type {HTMLElement | null} */ (target.closest(".crop-handle"));
  const box = target.closest(".crop-box");
  if (!handle && !box) return;
  e.preventDefault();

  win._cropDragging = handle ? handle.dataset.handle : "move";
  win._cropStartX = e.clientX;
  win._cropStartY = e.clientY;

  const cropBox = /** @type {HTMLElement | null} */ (document.getElementById("crop-box"));
  const overlay = /** @type {HTMLElement | null} */ (document.getElementById("crop-overlay"));
  if (cropBox && overlay) {
    const ow = overlay.offsetWidth;
    const oh = overlay.offsetHeight;
    win._cropStartRect = {
      x: cropBox.offsetLeft / ow,
      y: cropBox.offsetTop / oh,
      w: cropBox.offsetWidth / ow,
      h: cropBox.offsetHeight / oh,
    };
  }

  document.addEventListener("mousemove", _cropMouseMove);
  document.addEventListener("mouseup", _cropMouseUp);
}

/**
 * @param {number} sdx
 * @param {number} sdy
 */
function _screenToLocalDelta(sdx, sdy) {
  const e = _edits();
  const rot = (((e.rotation || 0) + (e.straighten || 0)) * Math.PI) / 180;
  const fh = e.flip_h ? -1 : 1;
  const fv = e.flip_v ? -1 : 1;
  const cos = Math.cos(-rot);
  const sin = Math.sin(-rot);
  return {
    dx: (sdx * cos - sdy * sin) * fh,
    dy: (sdx * sin + sdy * cos) * fv,
  };
}

/**
 * @param {{clientX: number, clientY: number}} e
 */
function _cropMouseMove(e) {
  /** @type {any} */
  const win = window;
  if (!win._cropDragging) return;
  const overlay = /** @type {HTMLElement | null} */ (document.getElementById("crop-overlay"));
  if (!overlay) return;

  const ow = overlay.offsetWidth;
  const oh = overlay.offsetHeight;
  const local = _screenToLocalDelta(e.clientX - win._cropStartX, e.clientY - win._cropStartY);
  const dx = local.dx / ow;
  const dy = local.dy / oh;

  let { x, y, w, h } = win._cropStartRect;

  if (win._cropDragging === "move") {
    x += dx;
    y += dy;
  } else {
    if (win._cropDragging.includes("n")) {
      y += dy;
      h -= dy;
    }
    if (win._cropDragging.includes("s")) h += dy;
    if (win._cropDragging.includes("w")) {
      x += dx;
      w -= dx;
    }
    if (win._cropDragging.includes("e")) w += dx;
  }

  if (win._editorAspectRatio && win._cropDragging !== "move") {
    const imgW = overlay.offsetWidth;
    const imgH = overlay.offsetHeight;
    const target = win._editorAspectRatio;
    const current = (w * imgW) / (h * imgH);
    if (current > target) {
      w = (h * imgH * target) / imgW;
    } else {
      h = ((w * imgW) / target) / imgH;
    }
  }

  w = Math.max(0.05, w);
  h = Math.max(0.05, h);
  _setCropBox(x, y, w, h);
}

function _cropMouseUp() {
  /** @type {any} */
  const win = window;
  win._cropDragging = null;
  document.removeEventListener("mousemove", _cropMouseMove);
  document.removeEventListener("mouseup", _cropMouseUp);
}

/**
 * @param {TouchEvent} e
 */
function _cropTouchStart(e) {
  /** @type {any} */
  const win = window;
  if (e.touches.length !== 1) return;
  const touch = e.touches[0];
  const target = /** @type {HTMLElement} */ (touch.target);
  const handle = /** @type {HTMLElement | null} */ (target.closest(".crop-handle"));
  const box = target.closest(".crop-box");
  if (!handle && !box) return;
  e.preventDefault();

  win._cropDragging = handle ? handle.dataset.handle : "move";
  win._cropStartX = touch.clientX;
  win._cropStartY = touch.clientY;

  const cropBox = /** @type {HTMLElement | null} */ (document.getElementById("crop-box"));
  const overlay = /** @type {HTMLElement | null} */ (document.getElementById("crop-overlay"));
  if (cropBox && overlay) {
    const ow = overlay.offsetWidth;
    const oh = overlay.offsetHeight;
    win._cropStartRect = {
      x: cropBox.offsetLeft / ow,
      y: cropBox.offsetTop / oh,
      w: cropBox.offsetWidth / ow,
      h: cropBox.offsetHeight / oh,
    };
  }

  document.addEventListener("touchmove", _cropTouchMove, { passive: false });
  document.addEventListener("touchend", _cropTouchEnd);
}

/**
 * @param {TouchEvent} e
 */
function _cropTouchMove(e) {
  /** @type {any} */
  const win = window;
  if (!win._cropDragging || e.touches.length !== 1) return;
  e.preventDefault();
  _cropMouseMove({ clientX: e.touches[0].clientX, clientY: e.touches[0].clientY });
}

function _cropTouchEnd() {
  /** @type {any} */
  const win = window;
  win._cropDragging = null;
  document.removeEventListener("touchmove", _cropTouchMove);
  document.removeEventListener("touchend", _cropTouchEnd);
}

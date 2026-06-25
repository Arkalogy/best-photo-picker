// @ts-check
/**
 * Clean Up (AI object removal) overlay for the photo editor — paint a
 * mask onto a canvas overlay, POST it to /api/v1/photos/<id>/inpaint, swap
 * in the inpainted result. Self-installs the LaMa Python package on
 * first use if missing.
 *
 * Inpaint state (`_inpaintMode`, `_inpaintBrushSize`, `_inpaintCanvas`,
 * `_inpaintCtx`, `_inpaintPainting`, `_inpaintAvailable`, `_inpaintTool`)
 * lives on `window` (declared in globals.js) so this module and the
 * still-classic editor.js share state via the global-object scope-chain
 * fallback.
 */

import { apiFetch, authEventSource } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { parseSSE } from "./format-helpers.mjs";
import { toast, toastError } from "./toast.mjs";

export function _renderRemoveControls() {
  /** @type {any} */
  const win = window;
  const tool = win._inpaintTool || "erase";
  const brushSize = win._inpaintBrushSize ?? 30;
  const available = win._inpaintAvailable;
  return `
    <div class="editor-remove-section">
      <div class="editor-section-label">Clean Up</div>
      <div class="editor-remove-tools">
        <button class="editor-remove-tool-btn${tool === "erase" ? " active" : ""}"
          data-action="_setInpaintTool" data-arg0="erase">Erase</button>
        <button class="editor-remove-tool-btn${tool === "retouch" ? " active" : ""}"
          data-action="_setInpaintTool" data-arg0="retouch">Retouch</button>
      </div>
      <p class="editor-remove-hint">Click, brush, or circle what you want to remove.</p>
      <div class="editor-slider-group">
        <div class="editor-slider-header">
          <span class="editor-slider-icon">&#9711;</span>
          <span class="editor-slider-label">Size</span>
          <span class="editor-slider-value" id="ev-inpaint-brush">${brushSize}px</span>
        </div>
        <div class="editor-slider-track-wrap">
          <input type="range" class="editor-slider" id="es-inpaint-brush"
            min="5" max="100" step="1" value="${brushSize}"
            data-oninput="_inpaintSetBrushSize"
            style="--pct: ${(((brushSize - 5) / 95) * 100).toFixed(0)}%">
        </div>
      </div>
      <div class="editor-remove-actions">
        <button class="editor-btn editor-btn-reset" data-action="_inpaintClearMask">Clear</button>
        <button class="editor-btn editor-btn-done" id="inpaint-apply-btn" data-action="_inpaintApply"${available === true ? "" : " disabled"}>Apply</button>
      </div>
      <div class="editor-remove-status" id="inpaint-status"></div>
    </div>
  `;
}

/**
 * @param {"erase" | "retouch"} tool
 */
export function _setInpaintTool(tool) {
  /** @type {any} */
  const win = window;
  win._inpaintTool = tool;
  document.querySelectorAll(".editor-remove-tool-btn").forEach((btn) => {
    btn.classList.toggle("active", (btn.textContent || "").toLowerCase() === tool);
  });
  if (tool === "retouch" && (win._inpaintBrushSize ?? 30) > 20) {
    _inpaintSetBrushSize(15);
    const slider = /** @type {HTMLInputElement | null} */ (
      document.getElementById("es-inpaint-brush")
    );
    if (slider) slider.value = "15";
  }
}

/**
 * @param {string | number} val
 */
export function _inpaintSetBrushSize(val) {
  /** @type {any} */
  const win = window;
  win._inpaintBrushSize = parseInt(String(val));
  const label = document.getElementById("ev-inpaint-brush");
  if (label) label.textContent = win._inpaintBrushSize + "px";
  const slider = /** @type {HTMLInputElement | null} */ (
    document.getElementById("es-inpaint-brush")
  );
  if (slider)
    slider.style.setProperty(
      "--pct",
      (((win._inpaintBrushSize - 5) / 95) * 100).toFixed(0) + "%"
    );
}

export function _showInpaintOverlay() {
  /** @type {any} */
  const win = window;
  _removeInpaintOverlay();
  const wrapper = document.querySelector(".lb-img-wrapper");
  const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
  if (!wrapper || !img) return;

  win._inpaintMode = true;

  const canvas = document.createElement("canvas");
  canvas.id = "inpaint-canvas";
  canvas.className = "inpaint-canvas";
  canvas.width = img.naturalWidth || img.width;
  canvas.height = img.naturalHeight || img.height;
  wrapper.appendChild(canvas);

  win._inpaintCanvas = canvas;
  win._inpaintCtx = canvas.getContext("2d");
  win._inpaintCtx.lineCap = "round";
  win._inpaintCtx.lineJoin = "round";

  canvas.addEventListener("mousedown", _inpaintStart);
  canvas.addEventListener("mousemove", _inpaintDraw);
  canvas.addEventListener("mouseup", _inpaintStop);
  canvas.addEventListener("mouseleave", _inpaintStop);
  canvas.addEventListener("click", _inpaintClick);

  canvas.addEventListener(
    "touchstart",
    (e) => {
      e.preventDefault();
      _inpaintStart(/** @type {any} */ (e.touches[0]));
    },
    { passive: false }
  );
  canvas.addEventListener(
    "touchmove",
    (e) => {
      e.preventDefault();
      _inpaintDraw(/** @type {any} */ (e.touches[0]));
    },
    { passive: false }
  );
  canvas.addEventListener("touchend", _inpaintStop);

  _checkInpaintAvailable();
}

export function _removeInpaintOverlay() {
  /** @type {any} */
  const win = window;
  document.getElementById("inpaint-canvas")?.remove();
  win._inpaintCanvas = null;
  win._inpaintCtx = null;
  win._inpaintPainting = false;
}

export async function _checkInpaintAvailable() {
  /** @type {any} */
  const win = window;
  if (win._inpaintAvailable !== null) {
    _updateInpaintStatus();
    return;
  }
  try {
    const data = await apiFetch("/api/v1/inpaint/status");
    win._inpaintAvailable = !!data.available;
  } catch (err) {
    console.warn("Inpaint status check failed:", err);
    win._inpaintAvailable = false;
  }
  _updateInpaintStatus();
}

function _updateInpaintStatus() {
  /** @type {any} */
  const win = window;
  const el = document.getElementById("inpaint-status");
  if (!el) return;
  const applyBtn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("inpaint-apply-btn")
  );
  if (win._inpaintAvailable === false) {
    el.innerHTML =
      '<span class="editor-remove-unavailable">AI removal model not available.</span>' +
      '<button class="btn btn-primary btn-sm" style="margin-top:8px" data-action="_installInpaintModel" data-arg0="this">' +
      "Download AI Model</button>";
    if (applyBtn) applyBtn.disabled = true;
  } else if (win._inpaintAvailable === true) {
    el.innerHTML = "";
    if (applyBtn) applyBtn.disabled = false;
  }
}

/**
 * @param {HTMLButtonElement} btn
 */
export async function _installInpaintModel(btn) {
  /** @type {any} */
  const win = window;
  const ok = await appConfirm(
    "Download AI removal model?",
    "This will download and install the LaMa inpainting package (~50 MB install + ~200 MB model on first use)."
  );
  if (!ok) return;
  btn.disabled = true;
  btn.textContent = "Installing…";
  try {
    const resp = await apiFetch("/api/v1/install/inpaint", { method: "POST" });
    if (resp.error) {
      toast(resp.error, true);
      btn.disabled = false;
      btn.textContent = "Download AI Model";
      return;
    }
    await new Promise((resolve, reject) => {
      const src = authEventSource("/api/v1/install/inpaint/progress");
      src.onmessage = (ev) => {
        const msg = /** @type {any} */ (parseSSE(ev.data));
        if (!msg) return;
        if (msg.type === "log") {
          btn.textContent = "Installing…";
        } else if (msg.type === "done") {
          src.close();
          resolve(undefined);
        } else if (msg.type === "error") {
          src.close();
          reject(new Error(msg.message || "Install failed"));
        }
      };
      src.onerror = () => {
        src.close();
        reject(new Error("Connection lost"));
      };
    });
    toast("AI removal model installed successfully");
    win._inpaintAvailable = null;
    await _checkInpaintAvailable();
    const applyBtn = /** @type {HTMLButtonElement | null} */ (
      document.getElementById("inpaint-apply-btn")
    );
    if (applyBtn && win._inpaintAvailable) applyBtn.disabled = false;
  } catch (e) {
    toastError("install the inpainting model", e);
    btn.disabled = false;
    btn.textContent = "Download AI Model";
  }
}

/**
 * @param {{clientX: number, clientY: number}} e
 */
function _inpaintCanvasCoords(e) {
  /** @type {any} */
  const win = window;
  const canvas = /** @type {HTMLCanvasElement | null} */ (win._inpaintCanvas);
  if (!canvas) return { x: 0, y: 0 };
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  return {
    x: (e.clientX - rect.left) * scaleX,
    y: (e.clientY - rect.top) * scaleY,
  };
}

let _inpaintDragged = false;

/**
 * @param {{clientX: number, clientY: number}} e
 */
function _inpaintStart(e) {
  /** @type {any} */
  const win = window;
  if (!win._inpaintCtx) return;
  win._inpaintPainting = true;
  _inpaintDragged = false;
  const { x, y } = _inpaintCanvasCoords(e);
  win._inpaintCtx.beginPath();
  win._inpaintCtx.moveTo(x, y);

  const canvas = /** @type {HTMLCanvasElement} */ (win._inpaintCanvas);
  const rect = canvas.getBoundingClientRect();
  const scale = canvas.width / rect.width;
  win._inpaintCtx.lineWidth = (win._inpaintBrushSize ?? 30) * scale;

  if (win._inpaintTool === "retouch") {
    win._inpaintCtx.strokeStyle = "rgba(100, 180, 255, 0.4)";
  } else {
    win._inpaintCtx.strokeStyle = "rgba(255, 60, 60, 0.5)";
  }
  win._inpaintCtx.globalCompositeOperation = "source-over";
}

/**
 * @param {{clientX: number, clientY: number}} e
 */
function _inpaintDraw(e) {
  /** @type {any} */
  const win = window;
  if (!win._inpaintPainting || !win._inpaintCtx) return;
  _inpaintDragged = true;
  const { x, y } = _inpaintCanvasCoords(e);
  win._inpaintCtx.lineTo(x, y);
  win._inpaintCtx.stroke();
}

function _inpaintStop() {
  /** @type {any} */
  const win = window;
  win._inpaintPainting = false;
}

/**
 * @param {MouseEvent} e
 */
function _inpaintClick(e) {
  /** @type {any} */
  const win = window;
  if (_inpaintDragged) return;
  if (!win._inpaintCtx || !win._inpaintCanvas) return;

  const { x, y } = _inpaintCanvasCoords(e);
  const canvas = /** @type {HTMLCanvasElement} */ (win._inpaintCanvas);
  const rect = canvas.getBoundingClientRect();
  const scale = canvas.width / rect.width;
  const radius = (win._inpaintBrushSize ?? 30) * scale;

  win._inpaintCtx.beginPath();
  win._inpaintCtx.arc(x, y, radius, 0, Math.PI * 2);
  if (win._inpaintTool === "retouch") {
    win._inpaintCtx.fillStyle = "rgba(100, 180, 255, 0.4)";
  } else {
    win._inpaintCtx.fillStyle = "rgba(255, 60, 60, 0.5)";
  }
  win._inpaintCtx.fill();
}

export function _inpaintClearMask() {
  /** @type {any} */
  const win = window;
  const canvas = /** @type {HTMLCanvasElement | null} */ (win._inpaintCanvas);
  if (!win._inpaintCtx || !canvas) return;
  win._inpaintCtx.clearRect(0, 0, canvas.width, canvas.height);
}

export async function _inpaintApply() {
  /** @type {any} */
  const win = window;
  const canvas = /** @type {HTMLCanvasElement | null} */ (win._inpaintCanvas);
  const ctx = win._inpaintCtx;
  if (!canvas || !ctx) return;
  if (win._inpaintAvailable === false) {
    toast("AI model not installed", true);
    return;
  }

  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const idx = win.lightboxIdx;
  const p = items[idx];
  if (!p || !p.id) {
    toast("No photo selected", true);
    return;
  }

  const w = canvas.width;
  const h = canvas.height;
  const imageData = ctx.getImageData(0, 0, w, h);
  const pixels = imageData.data;

  let hasPaint = false;
  for (let i = 3; i < pixels.length; i += 4) {
    if (pixels[i] > 0) {
      hasPaint = true;
      break;
    }
  }
  if (!hasPaint) {
    toast("Paint over the area to remove first", true);
    return;
  }

  const maskCanvas = document.createElement("canvas");
  maskCanvas.width = w;
  maskCanvas.height = h;
  const maskCtx = /** @type {CanvasRenderingContext2D} */ (maskCanvas.getContext("2d"));
  const maskData = maskCtx.createImageData(w, h);
  for (let i = 0; i < pixels.length; i += 4) {
    const painted = pixels[i + 3] > 0 ? 255 : 0;
    maskData.data[i] = painted;
    maskData.data[i + 1] = painted;
    maskData.data[i + 2] = painted;
    maskData.data[i + 3] = 255;
  }
  maskCtx.putImageData(maskData, 0, 0);

  const maskB64 = maskCanvas.toDataURL("image/png").split(",")[1];

  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("inpaint-apply-btn")
  );
  const origText = btn ? btn.textContent : "";
  if (btn) {
    btn.textContent = "Processing...";
    btn.disabled = true;
  }

  try {
    const data = await apiFetch(`/api/v1/photos/${p.id}/inpaint`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mask: maskB64 }),
    });

    if (data.image) {
      const img = /** @type {HTMLImageElement | null} */ (document.getElementById("lb-img"));
      if (img) img.src = "data:image/png;base64," + data.image;
      _inpaintClearMask();
      toast("Object removed");
    } else if (data.error) {
      toast(data.error, true);
    }
  } catch (e) {
    toastError("inpaint the photo", e);
  } finally {
    if (btn) {
      btn.textContent = origText;
      btn.disabled = win._inpaintAvailable === false;
    }
  }
}

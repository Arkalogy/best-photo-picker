// @ts-check
/**
 * Three small UI bootstrap helpers, called once during app startup:
 *  - `initSliders()` wires up tuning sliders (`[data-param]`) so dragging
 *    one updates its sibling label and triggers `scheduleRecompute()`.
 *  - `initTooltips()` creates a `.tooltip` overlay and binds mouseenter
 *    listeners to every `.tip[data-tip]` element.
 *  - `updateContentFilterLabel()` updates the sensitive-photo control's
 *    tooltip depending on whether automatic detection is active
 *    (`nudenetActive`: NudeNet accepted with the current license text
 *    AND its weights on disk).
 *
 * Reads `window.nudenetActive` and calls
 * `scheduleRecompute()` (now in analysis.mjs, window-bridged).
 */

import { apiFetch } from "./api-client.mjs";
import { formatVal } from "./format-helpers.mjs";
import { MODELS_CHANGED_EVENT } from "./models-changed.mjs";
import { state } from "./state.mjs";
import { scheduleRecompute } from "./analysis.mjs";

export function initSliders() {
  // DELEGATED, not per-element: tuning sliders live inside re-rendered
  // containers (the sidebar People-boost slider is rebuilt by every
  // renderAlbumNav), so listeners attached at boot die on the first
  // rebuild — the slider dragged but did nothing. A document-level
  // listener survives any innerHTML rebuild.
  document.addEventListener("input", (e) => {
    const target = /** @type {HTMLElement | null} */ (e.target);
    const el = target?.closest?.("[data-param]");
    if (!el) return;
    const sib = /** @type {HTMLElement | null} */ (el.nextElementSibling);
    const param = /** @type {HTMLElement} */ (el).dataset.param;
    const value = /** @type {HTMLInputElement} */ (el).value;
    if (sib && param) sib.textContent = formatVal(param, value);
    scheduleRecompute();
  });
}

export function initTooltips() {
  const tip = document.createElement("div");
  tip.className = "tooltip";
  document.body.appendChild(tip);

  document.querySelectorAll(".tip[data-tip]").forEach((el) => {
    const elH = /** @type {HTMLElement} */ (el);
    el.addEventListener("mouseenter", () => {
      tip.textContent = elH.dataset.tip || "";
      const rect = elH.getBoundingClientRect();
      tip.style.left = rect.right + 8 + "px";
      tip.style.top = rect.top - 4 + "px";
      tip.classList.add("visible");
      const tipRect = tip.getBoundingClientRect();
      if (tipRect.right > window.innerWidth - 8) {
        tip.style.left = rect.left - tipRect.width - 8 + "px";
      }
      if (tipRect.bottom > window.innerHeight - 8) {
        tip.style.top = window.innerHeight - tipRect.height - 8 + "px";
      }
    });
    el.addEventListener("mouseleave", () => {
      tip.classList.remove("visible");
    });
  });
}

/**
 * Pure: the sensitive-photo control's tooltip text. ``active`` means
 * automatic detection actually runs (NudeNet accepted with the current
 * license text AND its weights on disk) — not merely that it could be
 * enabled.
 *
 * @param {boolean} active
 * @returns {string}
 */
export function contentFilterTipText(active) {
  const policy =
    "Allow = sensitive photos compete for picks; Exclude = kept out of auto-picks " +
    "(still in your library). Manual includes always win.";
  if (active) {
    return `On-device nudity detection (NudeNet) is on. ${policy}`;
  }
  return (
    "Accept and download the NudeNet model in Settings → Models to turn on automatic " +
    "detection; until then only photos you flag manually are treated as " +
    `sensitive. ${policy}`
  );
}

export function updateContentFilterLabel() {
  /** @type {any} */
  const win = window;
  const tip = /** @type {HTMLElement | null} */ (
    document.getElementById("sensitive-filter-tip")
  );
  if (!tip) return;
  tip.dataset.tip = contentFilterTipText(!!win.nudenetActive);
}

/**
 * Re-read whether the content filter is active (after its license or
 * weights changed) and update the hint. A failed fetch keeps the old one.
 */
let _contentFilterRefreshSeq = 0;

export async function refreshContentFilterStatus() {
  // Changes can fire back to back (accept, then the download finishing):
  // only the newest request's answer may update the hint.
  const seq = ++_contentFilterRefreshSeq;
  try {
    // The small endpoint, not the whole /api/v1/status snapshot.
    const st = await apiFetch("/api/v1/models/content-filter");
    if (seq !== _contentFilterRefreshSeq) return;
    /** @type {any} */
    const win = window; // same global app.mjs sets at startup
    win.nudenetActive = !!st?.active;
    updateContentFilterLabel();
  } catch (err) {
    console.warn("[content filter] couldn't refresh its status:", err);
  }
}

if (typeof document !== "undefined") {
  document.addEventListener(MODELS_CHANGED_EVENT, () => void refreshContentFilterStatus());
}

// @ts-check
/**
 * Three small UI bootstrap helpers, called once during app startup:
 *  - `initSliders()` wires up tuning sliders (`[data-param]`) so dragging
 *    one updates its sibling label and triggers `scheduleRecompute()`.
 *  - `initTooltips()` creates a `.tooltip` overlay and binds mouseenter
 *    listeners to every `.tip[data-tip]` element.
 *  - `updateContentFilterLabel()` updates the sensitive-photo control's
 *    tooltip depending on whether `nudenetAvailable` is true.
 *
 * Reads `state.nudenetAvailable` (still classic) and calls
 * `scheduleRecompute()` (now in analysis.mjs, window-bridged).
 */

import { formatVal } from "./format-helpers.mjs";
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

export function updateContentFilterLabel() {
  /** @type {any} */
  const win = window;
  const tip = /** @type {HTMLElement | null} */ (
    document.getElementById("sensitive-filter-tip")
  );
  if (!tip) return;
  const policy =
    "Allow = sensitive photos compete for picks; Exclude = kept out of auto-picks " +
    "(still in your library). Manual includes always win.";
  if (win.nudenetAvailable) {
    tip.dataset.tip = `On-device nudity detection (NudeNet). ${policy}`;
  } else {
    tip.dataset.tip =
      "Install nudenet for on-device detection: pip install bppicker[nudity]. " +
      `Until then only photos you mark sensitive are flagged. ${policy}`;
  }
}

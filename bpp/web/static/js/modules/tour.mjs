// @ts-check
/**
 * Product tour overlay — guides new users through Import → Analyze → Pick →
 * Export with positioned tooltips, a clip-path backdrop cutout, and
 * keyboard control. Self-attaches global keydown + resize listeners on
 * import.
 *
 * State is module-private; classic-side callers that gated on `tourActive`
 * use `_isTourActive()`. Phase advances triggered from import-worker.mjs
 * arrive via `advanceTourToPhase(phase)` (window-bridged).
 */

import { getSetting, saveSetting } from "./settings-client.mjs";
import { toast } from "./toast.mjs";

/**
 * @typedef {{
 *   id: string,
 *   phase: number,
 *   target: string,
 *   fallback?: string,
 *   title: string,
 *   text: string,
 *   position?: 'top' | 'bottom' | 'left' | 'right',
 *   waitFor?: string,
 * }} TourStep
 */

/** @type {TourStep[]} */
export const TOUR_STEPS = [
  {
    id: "import",
    phase: 1,
    target: "#btn-import-toolbar",
    fallback: ".empty-state .btn-import-cta",
    title: "Import your photos",
    text: "Click here to import a folder of photos. The app will copy them into your library.",
    position: "bottom",
    waitFor: "import_done",
  },
  {
    id: "analyze",
    phase: 2,
    target: "#btn-analyze-toolbar",
    title: "Analyze for quality",
    text: "Click Analyze to score every photo on sharpness, lighting, faces, and composition.",
    position: "bottom",
    waitFor: "analysis_done",
  },
  {
    id: "scores",
    phase: 3,
    target: ".card:first-child",
    fallback: ".card",
    title: "Quality scores",
    text: "Each photo gets a quality score. Green means great, blue is decent, red means weak.",
    position: "top",
  },
  {
    id: "settings-scoring",
    phase: 3,
    target: "#btn-settings-toolbar",
    title: "Tune scoring weights",
    text: "Open Settings > Scoring to adjust how much sharpness, lighting, faces, and composition matter. The selection updates live.",
    position: "bottom",
  },
  {
    id: "people",
    phase: 3,
    target: "#nav-face-boost",
    fallback: ".nav-face-boost",
    title: "People in your photos",
    text: "Recognized faces appear here. Click a person to see their photos, or boost them in your selection.",
    position: "right",
  },
  {
    id: "albums",
    phase: 3,
    target: "#album-list",
    title: "Albums and smart albums",
    text: "Organize into albums. Smart albums auto-generate by date, score, duplicates, and more.",
    position: "right",
  },
  {
    id: "export",
    phase: 3,
    target: "#btn-export",
    title: "Export your picks",
    text: "When you're happy with the selection, export to a folder with a full quality report.",
    position: "bottom",
  },
  {
    id: "done",
    phase: 3,
    target: "#btn-settings-toolbar",
    title: "That's it!",
    text: "Replay this tour anytime from Settings. Right-click any photo for more options.",
    position: "bottom",
  },
];

let tourActive = false;
let tourStepIndex = 0;
/** @type {HTMLElement | null} */
let tourOverlayEl = null;

/** @returns {boolean} */
export function _isTourActive() {
  return tourActive;
}

/** Test-only: reset tour state without writing settings. */
export function _resetTourState() {
  tourActive = false;
  tourStepIndex = 0;
  if (tourOverlayEl) {
    tourOverlayEl.remove();
    tourOverlayEl = null;
  }
}

/** Test-only: read current step index. */
export function _getTourStepIndex() {
  return tourStepIndex;
}

export function startTour() {
  tourStepIndex = 0;
  tourActive = true;
  showTourStep();
}

export function endTour() {
  tourActive = false;
  saveSetting("tour_done", "true");
  removeTourOverlay();
}

export function removeTourOverlay() {
  if (tourOverlayEl) {
    tourOverlayEl.remove();
    tourOverlayEl = null;
  }
}

export function nextTourStep() {
  tourStepIndex++;
  while (tourStepIndex < TOUR_STEPS.length) {
    if (findTourTarget(TOUR_STEPS[tourStepIndex])) break;
    tourStepIndex++;
  }
  if (tourStepIndex >= TOUR_STEPS.length) {
    endTour();
    toast("Tour complete!");
  } else {
    showTourStep();
  }
}

export function prevTourStep() {
  tourStepIndex--;
  while (tourStepIndex >= 0) {
    if (findTourTarget(TOUR_STEPS[tourStepIndex])) break;
    tourStepIndex--;
  }
  if (tourStepIndex < 0) tourStepIndex = 0;
  showTourStep();
}

/**
 * @param {TourStep} step
 * @returns {HTMLElement | null}
 */
export function findTourTarget(step) {
  let el = /** @type {HTMLElement | null} */ (document.querySelector(step.target));
  if (el && el.offsetParent === null) el = null;
  if (!el && step.fallback) {
    el = /** @type {HTMLElement | null} */ (document.querySelector(step.fallback));
    if (el && el.offsetParent === null) el = null;
  }
  return el;
}

export function showTourStep() {
  const step = TOUR_STEPS[tourStepIndex];
  const target = findTourTarget(step);
  if (!target) {
    nextTourStep();
    return;
  }

  removeTourOverlay();

  const overlay = document.createElement("div");
  overlay.className = "tour-overlay";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", "Product tour");
  tourOverlayEl = overlay;

  const backdrop = document.createElement("div");
  backdrop.className = "tour-backdrop";
  overlay.appendChild(backdrop);

  const highlight = document.createElement("div");
  highlight.className = "tour-highlight";
  overlay.appendChild(highlight);

  const tooltip = document.createElement("div");
  tooltip.className = "tour-tooltip";
  tooltip.innerHTML = buildTooltipHTML(step);
  overlay.appendChild(tooltip);

  const arrow = document.createElement("div");
  arrow.className = "tour-tooltip-arrow";
  tooltip.appendChild(arrow);

  document.body.appendChild(overlay);

  positionTourElements(target, step, highlight, tooltip, arrow, backdrop);

  backdrop.addEventListener("click", (e) => e.stopPropagation());

  if (step.waitFor) {
    highlight.style.pointerEvents = "auto";
    highlight.style.cursor = "pointer";
    highlight.addEventListener("click", () => target.click());
  }
}

/**
 * @param {TourStep} step
 * @returns {string}
 */
export function buildTooltipHTML(step) {
  const total = TOUR_STEPS.length;
  const num = tourStepIndex + 1;
  const isFirst = tourStepIndex === 0;
  const isLast = tourStepIndex === total - 1;

  let btns = "";
  if (!isFirst) btns += '<button class="tour-btn" data-action="prevTourStep">Back</button>';
  if (isLast) {
    btns += '<button class="tour-btn-primary" data-action="endTour">Done</button>';
  } else {
    btns += '<button class="tour-btn-primary" data-action="nextTourStep">Next</button>';
  }

  return (
    '<div class="tour-tooltip-title">' +
    step.title +
    "</div>" +
    '<div class="tour-tooltip-text">' +
    step.text +
    "</div>" +
    '<div class="tour-tooltip-footer">' +
    '<button class="tour-skip" data-action="endTour">Skip tour</button>' +
    '<div class="tour-counter">' +
    num +
    " of " +
    total +
    "</div>" +
    '<div class="tour-tooltip-btns">' +
    btns +
    "</div>" +
    "</div>"
  );
}

/**
 * @param {HTMLElement} target
 * @param {TourStep} step
 * @param {HTMLElement} highlight
 * @param {HTMLElement} tooltip
 * @param {HTMLElement} arrow
 * @param {HTMLElement} backdrop
 */
export function positionTourElements(target, step, highlight, tooltip, arrow, backdrop) {
  const rect = target.getBoundingClientRect();
  const pad = 8;
  const hx = rect.left - pad;
  const hy = rect.top - pad;
  const hw = rect.width + pad * 2;
  const hh = rect.height + pad * 2;

  highlight.style.left = hx + "px";
  highlight.style.top = hy + "px";
  highlight.style.width = hw + "px";
  highlight.style.height = hh + "px";

  const r = 8;
  backdrop.style.clipPath =
    "polygon(" +
    "0% 0%, 0% 100%, 100% 100%, 100% 0%, 0% 0%, " +
    (hx + r) +
    "px " +
    hy +
    "px, " +
    (hx + hw - r) +
    "px " +
    hy +
    "px, " +
    (hx + hw) +
    "px " +
    (hy + r) +
    "px, " +
    (hx + hw) +
    "px " +
    (hy + hh - r) +
    "px, " +
    (hx + hw - r) +
    "px " +
    (hy + hh) +
    "px, " +
    (hx + r) +
    "px " +
    (hy + hh) +
    "px, " +
    hx +
    "px " +
    (hy + hh - r) +
    "px, " +
    hx +
    "px " +
    (hy + r) +
    "px, " +
    (hx + r) +
    "px " +
    hy +
    "px)";

  const gap = 14;
  const tw = tooltip.offsetWidth;
  const th = tooltip.offsetHeight;
  let tx, ty, arrowSide;
  const pos = step.position || "bottom";

  if (pos === "bottom") {
    tx = hx + hw / 2 - tw / 2;
    ty = hy + hh + gap;
    arrowSide = "top";
  } else if (pos === "top") {
    tx = hx + hw / 2 - tw / 2;
    ty = hy - th - gap;
    arrowSide = "bottom";
  } else if (pos === "right") {
    tx = hx + hw + gap;
    ty = hy + hh / 2 - th / 2;
    arrowSide = "left";
  } else {
    tx = hx - tw - gap;
    ty = hy + hh / 2 - th / 2;
    arrowSide = "right";
  }

  const vw = window.innerWidth;
  const vh = window.innerHeight;
  tx = Math.max(12, Math.min(tx, vw - tw - 12));
  ty = Math.max(12, Math.min(ty, vh - th - 12));

  tooltip.style.left = tx + "px";
  tooltip.style.top = ty + "px";

  arrow.style.top = arrow.style.bottom = arrow.style.left = arrow.style.right = "";
  if (arrowSide === "top") {
    arrow.style.top = "-6px";
    arrow.style.left = Math.max(16, Math.min(hx + hw / 2 - tx - 6, tw - 28)) + "px";
  } else if (arrowSide === "bottom") {
    arrow.style.bottom = "-6px";
    arrow.style.left = Math.max(16, Math.min(hx + hw / 2 - tx - 6, tw - 28)) + "px";
  } else if (arrowSide === "left") {
    arrow.style.left = "-6px";
    arrow.style.top = Math.max(16, Math.min(hy + hh / 2 - ty - 6, th - 28)) + "px";
  } else {
    arrow.style.right = "-6px";
    arrow.style.top = Math.max(16, Math.min(hy + hh / 2 - ty - 6, th - 28)) + "px";
  }
}

/**
 * Called from import-worker / analyze-worker after a phase milestone:
 * jump the tour forward to the first step in `phase` (or later).
 *
 * @param {number} phase
 */
export function advanceTourToPhase(phase) {
  if (!tourActive) return;
  const idx = TOUR_STEPS.findIndex((s) => s.phase >= phase);
  if (idx >= 0 && idx > tourStepIndex) {
    tourStepIndex = idx;
    showTourStep();
  }
}

export function maybeAutoStartTour() {
  if (getSetting("tour_done", null) !== "true") {
    setTimeout(() => startTour(), 600);
  }
}

document.addEventListener("keydown", (e) => {
  if (!tourActive) return;
  if (e.key === "Escape") {
    endTour();
    e.preventDefault();
  } else if (e.key === "ArrowRight" || e.key === "Enter") {
    if (!TOUR_STEPS[tourStepIndex].waitFor) {
      nextTourStep();
      e.preventDefault();
    }
  } else if (e.key === "ArrowLeft") {
    prevTourStep();
    e.preventDefault();
  }
});

window.addEventListener("resize", () => {
  if (!tourActive || !tourOverlayEl) return;
  const step = TOUR_STEPS[tourStepIndex];
  const target = findTourTarget(step);
  if (!target) return;
  const hl = /** @type {HTMLElement | null} */ (tourOverlayEl.querySelector(".tour-highlight"));
  const tt = /** @type {HTMLElement | null} */ (tourOverlayEl.querySelector(".tour-tooltip"));
  const ar = /** @type {HTMLElement | null} */ (
    tourOverlayEl.querySelector(".tour-tooltip-arrow")
  );
  const bd = /** @type {HTMLElement | null} */ (tourOverlayEl.querySelector(".tour-backdrop"));
  if (hl && tt && ar && bd) positionTourElements(target, step, hl, tt, ar, bd);
});

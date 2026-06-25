// @ts-check
/**
 * Contextual nudge banner system.
 *
 * Modular, data-driven hints that appear based on app state. Each
 * nudge is shown once, dismissible, and targets a container element
 * by ID. Definitions are decoupled from layout — callers just point
 * showNudge at a container and the banner renders itself.
 *
 * Persistence:
 *   DB settings key `nudge_<id>` = "dismissed" once the user closes
 *   the banner. An in-memory Set memos the same fact for the rest
 *   of the session so we don't have to re-read the cache on every
 *   call.
 *
 * Bridged onto window via index.html's module bootstrap so the
 * existing classic callers (app.js, faces.js, analysis.js, wizard.js,
 * import-worker.js, modals.js) keep working unchanged. The dismiss
 * button is rendered as inline `data-action="dismissNudge" data-arg0="id"` HTML
 * inside showNudge, so dismissNudge MUST be reachable as a global.
 */

import { getSetting, saveSetting } from "./settings-client.mjs";

/**
 * @typedef {Object} NudgeAction
 * @property {string} label
 * @property {string} fn   Name of a global function to invoke on click.
 *
 * @typedef {Object} NudgeDef
 * @property {string} message    HTML — definitions are author-controlled.
 * @property {NudgeAction} [action]
 * @property {boolean} [dismiss]
 */

/** @type {Record<string, NudgeDef>} */
export const NUDGE_DEFS = {
  analyze_photos: {
    message:
      "Your photos are imported! Click <strong>Analyze</strong> to score them for quality, faces, and composition.",
    action: { label: "Analyze Now", fn: "startReanalyze" },
    dismiss: true,
  },
  pick_people: {
    message:
      "Faces detected! Select people in the sidebar to boost photos of them in your selection.",
    action: { label: "Open People", fn: "navigateToPeople" },
    dismiss: true,
  },
  export_ready: {
    message:
      "Your best photos are selected. Export them to a folder when you're ready.",
    action: { label: "Export", fn: "showExportModal" },
    dismiss: true,
  },
};

/**
 * In-session memo of dismissed nudge IDs. Reset on page reload —
 * persistent dismissal lives in DB settings via the `nudge_<id>` key.
 *
 * Exported so tests can clear/inspect it. Production code goes through
 * dismissNudge / _isNudgeDismissed.
 *
 * @type {Set<string>}
 */
export const _nudgeDismissed = new Set();

/**
 * @param {string} id
 * @returns {string}
 */
export function _nudgeKey(id) {
  return "nudge_" + id;
}

/**
 * @param {string} id
 * @returns {boolean}
 */
export function _isNudgeDismissed(id) {
  return _nudgeDismissed.has(id) || getSetting(_nudgeKey(id), null) === "dismissed";
}

/**
 * Dismiss a nudge: memo it, persist to DB settings, animate it out.
 *
 * @param {string} id
 */
export function dismissNudge(id) {
  _nudgeDismissed.add(id);
  saveSetting(_nudgeKey(id), "dismissed");
  const el = document.getElementById("nudge-" + id);
  if (el) {
    el.classList.add("nudge-hiding");
    setTimeout(() => el.remove(), 300);
  }
}

/**
 * Show a nudge banner inside a container element. No-op when the
 * nudge is dismissed, the product tour is active, the banner is
 * already showing, the def is unknown, or the container is missing.
 *
 * @param {string} id           Nudge ID — must be a key in NUDGE_DEFS.
 * @param {string} containerId  DOM element ID to prepend the nudge into.
 */
export function showNudge(id, containerId) {
  if (_isNudgeDismissed(id)) return;
  // Suppress nudges while the tour is active. tour.mjs exposes
  // `_isTourActive()` on window; missing → no tour, treat as inactive.
  /** @type {any} */
  const win = window;
  if (typeof win._isTourActive === "function" && win._isTourActive()) return;
  if (document.getElementById("nudge-" + id)) return;

  const def = NUDGE_DEFS[id];
  if (!def) return;

  const container = document.getElementById(containerId);
  if (!container) return;

  const nudge = document.createElement("div");
  nudge.id = "nudge-" + id;
  nudge.className = "nudge-banner";
  nudge.setAttribute("role", "status");

  let html = `<span class="nudge-msg">${def.message}</span>`;
  if (def.action) {
    html += `<button class="nudge-action" data-action="_nudgeAction" data-arg0="${def.action.fn}" data-arg1="${id}">${def.action.label}</button>`;
  }
  if (def.dismiss) {
    html += `<button class="nudge-close" data-action="dismissNudge" data-arg0="${id}" title="Dismiss" aria-label="Dismiss hint">&#215;</button>`;
  }
  nudge.innerHTML = html;

  container.prepend(nudge);
  requestAnimationFrame(() => nudge.classList.add("nudge-visible"));
}

/**
 * Remove a nudge from the DOM without persisting dismissal — used
 * for state transitions (e.g. when the nudge's precondition no
 * longer holds and we want it to potentially re-appear later).
 *
 * @param {string} id
 */
export function removeNudge(id) {
  const el = document.getElementById("nudge-" + id);
  if (el) el.remove();
}

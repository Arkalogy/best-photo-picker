// @ts-check
/**
 * Rows mid-operation (a download, an uninstall) in the Settings → Models
 * picker: locked, showing progress, until the operation ends. Split from
 * modals-face-embedders.mjs.
 */

import { feState } from "./face-embedders-state.mjs";
import { esc, escapeAttr } from "./text-format.mjs";

/**
 * The operation on a row ended (done or failed): unlock it. The picker
 * reload that follows shows its real status.
 *
 * @param {string} registryId
 */
export function _clearRowBusy(registryId) {
  feState.busyRowIds.delete(registryId);
  feState.busyText.delete(registryId);
  document.querySelector(`[data-fe-busy="${_safeId(registryId)}"]`)?.remove();
}

/**
 * The rows mid-operation, one line each, for when the rows themselves
 * aren't drawn (the list reloading, failing to, or leaving one out): their
 * progress stays on screen ("nothing silent"), and _setRowStatusBusy keeps
 * it current.
 * @param {string[]} [ids]  default: every busy row
 * @returns {string}
 */
export function busyFallbackHTML(ids = [...feState.busyRowIds]) {
  return ids
    .map(
      (id) =>
        `<p class="setting-muted fe-busy-fallback" data-fe-busy="${escapeAttr(id)}">` +
        `<span class="fe-spinner"></span>${esc(feState.busyText.get(id) || "Working…")}</p>`,
    )
    .join("");
}

/**
 * Registry ids are simple slugs (e.g. "opencv_yunet"); CSS.escape is the
 * correct guard but is absent under jsdom, so fall back to a quote-strip.
 * @param {string} registryId
 */
function _safeId(registryId) {
  return typeof CSS !== "undefined" && CSS.escape
    ? CSS.escape(registryId)
    : String(registryId).replace(/["\\]/g, "");
}

/**
 * Replace a row's status cell with a transient busy indicator while a
 * long operation (download) is in flight. Honors the "nothing should
 * be silent" rule — a multi-second download must show progress in-row,
 * not just a fire-and-forget toast.
 *
 * @param {string} registryId
 * @param {string} text  e.g. "Downloading…"
 * @returns {boolean} whether it's on screen (its row, or its own line)
 */
export function _setRowStatusBusy(registryId, text) {
  // Marks the row as in-flight so subsequent ⋯ opens and action
  // clicks bounce off the busy gate until the operation ends
  // (_clearRowBusy) — a reload meanwhile keeps it locked. Without
  // this, the bug screenshot showed an open menu
  // mid-download offering Redownload + Uninstall + Use this model
  // simultaneously — any of which would corrupt the operation.
  feState.busyRowIds.add(registryId);
  feState.busyText.set(registryId, text);
  const safeId = _safeId(registryId);
  const cell = document.querySelector(
    `tr[data-entry-id="${safeId}"] .fe-cell-status`,
  );
  const busyHTML = `<span class="fe-spinner"></span>${esc(text)}`;
  // The row isn't drawn (reloading / failed / left out): its own line.
  const line = cell ? null : document.querySelector(`[data-fe-busy="${safeId}"]`);
  if (cell) cell.innerHTML = `<span class="fe-status fe-status-busy">${busyHTML}</span>`;
  else if (line) line.innerHTML = busyHTML;
  // Close the menu if it was open when the busy state started.
  const openMenu = document.getElementById("fe-ovr-" + registryId);
  if (openMenu) openMenu.classList.remove("open", "fe-overflow-menu--up");
  return !!(cell || line);
}

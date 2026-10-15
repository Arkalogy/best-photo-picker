// @ts-check
/**
 * Status + Size cells for the Settings → Models picker rows. Pure
 * renderers over the derived row state from
 * ``face-embedders-rowstate.mjs``.
 *
 * Split out of ``face-embedders-rowstate.mjs`` for the 500-LOC cap.
 */

import { ROW_LIFECYCLE } from "./face-embedders-rowstate.mjs";
import { _formatBytes } from "./format-helpers.mjs";
import { escapeAttr, esc } from "./text-format.mjs";

// ── License badge ───────────────────────────────────────────────────

/**
 * License badge for a picker row. Three cases, matching MODEL_POLICY.md:
 * commercial-use restriction → "Restricted"; a click-through that only
 * acknowledges attribution terms (Apache-2.0 etc.) → "Attribution"; no
 * click-through at all → "Permissive".
 *
 * @param {{commercial_use_restriction_known?: boolean, requires_explicit_ack?: boolean}} entry
 * @returns {{text: string, cls: string}}
 */
export function licenseBadge(entry) {
  if (entry.commercial_use_restriction_known === true) {
    return { text: "Restricted", cls: "fe-license-restricted" };
  }
  if (entry.requires_explicit_ack === true) {
    return { text: "Attribution", cls: "fe-license-attribution" };
  }
  return { text: "Permissive", cls: "fe-license-permissive" };
}

// ── Status cell ─────────────────────────────────────────────────────

//: Lifecycle → status badge mapping. One row per lifecycle value.
//: When you add a new lifecycle, add an entry here.
const _STATUS_BY_LIFECYCLE = {
  [ROW_LIFECYCLE.RUNNING]: {
    main: "Running",
    sub: null,
    cls: "fe-status-active",
    tip: "This is your pick AND its weights are on this device. It's what runs at analyze time.",
  },
  [ROW_LIFECYCLE.PICKED_NEEDS_DOWNLOAD]: {
    main: "Can’t run",
    sub: "needs download",
    cls: "fe-status-warn",
    tip: "This is your pick, but its weights aren't on this device. Open ⋯ → Download to make it run. Until then a substitute model is doing the work.",
  },
  [ROW_LIFECYCLE.PICKED_NEEDS_LICENSE]: {
    main: "Can’t run",
    sub: "needs license",
    cls: "fe-status-warn",
    tip: "This is your pick, but its license isn't accepted (or was withdrawn). Open ⋯ → Review the license to enable it. Until then a substitute model is doing the work.",
  },
  [ROW_LIFECYCLE.NEEDS_LICENSE]: {
    main: "Needs license",
    sub: null,
    cls: "fe-status-warn",
    tip: "Weights are on this device, but the license hasn't been accepted (or was withdrawn). Open ⋯ → Review the license to enable use.",
  },
  [ROW_LIFECYCLE.READY]: {
    main: "Ready",
    sub: null,
    cls: "fe-status-ok",
    tip: "Downloaded and ready. Open ⋯ → “Use this model” to pick it.",
  },
  [ROW_LIFECYCLE.BACKUP_AVAILABLE]: {
    main: "Backup",
    sub: "auto-fallback",
    cls: "fe-status-muted",
    tip: "Downloaded and available. The runtime only uses this if the primary for its kind can't load — the picker doesn't expose a manual switch.",
  },
  [ROW_LIFECYCLE.NEEDS_RUNTIME]: {
    main: "Needs runtime",
    sub: null,
    cls: "fe-status-warn",
    tip: "Weights are downloaded, but a runtime package is missing — open ⋯ → “Install runtime…”.",
  },
  [ROW_LIFECYCLE.PARTIAL]: {
    main: "Incomplete",
    sub: "missing files",
    cls: "fe-status-warn",
    tip: "Some model files are missing — open ⋯ → “Redownload” to fix.",
  },
  [ROW_LIFECYCLE.NOT_DOWNLOADED]: {
    main: "Not downloaded",
    sub: null,
    cls: "fe-status-muted",
    tip: "Available to download — not yet on this device.",
  },
};

//: Stale acceptance (accepted under an older license text). Overrides
//: the lifecycle badge for any license-blocked row: the actionable
//: fact is "re-accept", whatever the download state.
const _REACCEPT_STATUS = {
  main: "Re-accept needed",
  sub: "license terms updated",
  cls: "fe-status-warn",
  tip: "You accepted an earlier version of this model’s license terms. The terms text has been updated, so the model is switched off until you accept again. Open ⋯ → “Re-accept the model’s license”.",
};

//: Accepted, but the use context is Commercial and the separate-rights
//: assertion wasn't made — the load gate refuses it.
const _COMMERCIAL_RIGHTS_STATUS = {
  main: "Needs commercial-use rights",
  sub: "use context is Commercial",
  cls: "fe-status-warn",
  tip: "Your use context is set to Commercial, and this model’s license restricts commercial use. It stays off until you confirm you hold separate rights. Open ⋯ → “Confirm your commercial-use rights”, or change the use context.",
};

//: Withdrawn or legally blocked by the registry: accepting can't enable it.
const _UNAVAILABLE_STATUS = {
  main: "Not available",
  sub: "withdrawn",
  cls: "fe-status-muted",
  tip: "This model has been withdrawn or blocked in the model registry, so it can’t be used or downloaded. Files already on this device can be uninstalled.",
};

/**
 * Pure: the status badge descriptor for a derived row state.
 *
 * @param {{lifecycle: string, needsReaccept?: boolean, needsCommercialRights?: boolean, unavailable?: boolean}} state
 * @returns {{main: string, sub: string | null, cls: string, tip: string}}
 */
export function _statusFor(state) {
  if (state.unavailable) return _UNAVAILABLE_STATUS;
  if (state.needsCommercialRights) return _COMMERCIAL_RIGHTS_STATUS;
  if (state.needsReaccept) return _REACCEPT_STATUS;
  return _STATUS_BY_LIFECYCLE[state.lifecycle];
}

/**
 * Plain-English status label for the Status column. Drives entirely
 * off the row's lifecycle — no flag-juggling, no cross-row guessing.
 *
 * @param {ReturnType<typeof import("./face-embedders-rowstate.mjs")._deriveRowState>} state
 */
export function _statusCell(state) {
  const s = _statusFor(state);
  const tipAttr = s.tip ? ` title="${escapeAttr(s.tip)}"` : "";
  const subHtml = s.sub
    ? `<span class="fe-status-sub">${esc(s.sub)}</span>`
    : "";
  return `<span class="fe-status ${s.cls}"${tipAttr}><span class="fe-status-main">${esc(s.main)}</span>${subHtml}</span>`;
}

/**
 * Render the Size column cell. Three sources, in priority order:
 *
 *   1. Actual size from install state (file on disk, known bytes).
 *   2. Registered expected download size for catalog entries. The
 *      "~" estimate marker drops once the catalog entry is on disk
 *      (state.onDisk is true even if install.size_bytes is 0,
 *      because catalog entries report on-disk via catalog_on_disk
 *      rather than a populated install record).
 *   3. Em-dash when neither is known.
 *
 * @param {ReturnType<typeof import("./face-embedders-rowstate.mjs")._deriveRowState>} state
 */
export function _sizeCell(state) {
  if (state.sizeBytes > 0) {
    return `<span class="fe-cell-size-val">${_formatBytes(state.sizeBytes)}</span>`;
  }
  if (state.expectedSize > 0) {
    // When the catalog entry's weights are on disk, the size is no
    // longer an estimate — render with the normal size class so it
    // visually matches the installable rows. The italic
    // "fe-cell-size-expected" style is reserved for pre-download
    // estimates ("~121.7 MB"), where the italic + "~" together
    // signal "this is what you would download, not what you have."
    const cls = state.onDisk ? "fe-cell-size-val" : "fe-cell-size-expected";
    const prefix = state.onDisk ? "" : "~";
    return `<span class="${cls}" title="Download size">${prefix}${_formatBytes(state.expectedSize)}</span>`;
  }
  return `<span class="fe-cell-size-empty">—</span>`;
}


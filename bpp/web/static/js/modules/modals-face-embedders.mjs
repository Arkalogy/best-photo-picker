// @ts-check
/**
 * Unified model picker — registry catalog + install state.
 *
 * Renders ``/api/v1/model-registry/entries`` into the Settings → Models
 * tab, grouped by **kind** (face embedder, face detector, semantic
 * search, pet detector, nudity classifier, inpainter) and within each
 * kind by license posture (permissive vs restricted). Restricted entries
 * open the canonical click-through dialog before they're usable. Each
 * row also joins to ``/api/v1/models`` (the file-install layer) so the
 * picker shows download status, file size, and install / uninstall /
 * redownload controls in the same place as the legal-posture metadata.
 *
 * The file name still says "face-embedders" because that's how the
 * module is imported from index.html — the rebuild expanded its scope
 * but kept the module path stable for the importer. A rename PR is
 * fine but out of scope.
 *
 * What this module does NOT do:
 *
 * * Drive the actual face_embedder dispatcher (which model produces
 *   embeddings at analyze time). That's a separate sprint — the
 *   `face_embedding_method` setting still controls dispatch.
 * * Manage per-entry plugin lifecycle for non-bundled models. The
 *   install panel passes through to the legacy ``/api/v1/models``
 *   download endpoints; only registry-catalog entries that match
 *   a legacy feature get install controls.
 */

import { registerAction } from "./action-registry.mjs";
import { apiFetch } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import {
  _acceptanceGateSatisfied,
  closeFaceEmbedderAcceptance,
  confirmFaceEmbedderAcceptance,
  openFaceEmbedderAcceptance,
  revokeFaceEmbedderAcceptance,
} from "./face-embedders-acceptance.mjs";
import {
  ensureCatalogWeights,
  installFaceEmbedderViaHint,
  redownloadFaceEmbedderEntry,
  setActiveFaceEmbedder,
  setFaceEmbedderUseContext,
  uninstallFaceEmbedderEntry,
} from "./face-embedders-actions.mjs";
import { KIND_LABELS, KIND_ORDER, LEGACY_FEATURE_MAP } from "./face-embedders-catalog.mjs";
import {
  _feInstallGlobalHandlers,
  _feLicenseInfoToggle,
  _feOverflowToggle,
} from "./face-embedders-popover.mjs";
import {
  ROW_LIFECYCLE,
  _buildActionMenu,
  _buildMenuFromState,
  _deriveRowState,
  _renderMenuItem,
} from "./face-embedders-rowstate.mjs";
import { _sizeCell, _statusCell, licenseBadge } from "./face-embedders-cells.mjs";
import {
  _collectAcceptedIds,
  _collectCatalogEntryIds,
  _collectReacceptIds,
  damagedLogNoticeHTML,
  loadFailureNoticeHTML,
  _collectRequiresAckIds,
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
  feState,
} from "./face-embedders-state.mjs";
import { _clearRowBusy, _setRowStatusBusy, busyFallbackHTML } from "./face-embedders-busy.mjs";
import { _formatBytes } from "./format-helpers.mjs";
import { MODELS_CHANGED_EVENT } from "./models-changed.mjs";
import { escapeAttr, esc } from "./text-format.mjs";
import { toast } from "./toast.mjs";
import { USE_CONTEXT_OPTIONS } from "./use-context-options.mjs";

// Re-export so the existing test/import path (modals-face-embedders.mjs)
// keeps working after the state + acceptance + actions + rowstate split.
export {
  _clearRowBusy,
  _setRowStatusBusy,
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
  _acceptanceGateSatisfied,
  closeFaceEmbedderAcceptance,
  confirmFaceEmbedderAcceptance,
  openFaceEmbedderAcceptance,
  revokeFaceEmbedderAcceptance,
  ensureCatalogWeights,
  redownloadFaceEmbedderEntry,
  setActiveFaceEmbedder,
  uninstallFaceEmbedderEntry,
  ROW_LIFECYCLE,
  _buildActionMenu,
  _deriveRowState,
  _feOverflowToggle,
  _feLicenseInfoToggle,
};

/**
 * Fetch + render the unified picker into ``#face-embedder-picker``.
 * Called whenever Settings → Models shows: the tab switch, which
 * ``showSettings`` also goes through.
 */
export async function loadFaceEmbedderPicker() {
  const container = document.getElementById("face-embedder-picker");
  if (!container) return;
  _feInstallGlobalHandlers();
  const load = ++_loadCount; // a newer load (another click) wins
  // A reload keeps the rows on screen (locked) until the new ones arrive:
  // emptying the list first would collapse it and lose the user's place.
  if (container.querySelector("tr[data-entry-id]")) container.setAttribute("aria-busy", "true");
  else container.innerHTML = '<p class="setting-muted">Loading…</p>' + busyFallbackHTML();
  try {
    // These three only refine the rows: if one fails the picker still
    // draws, says what it couldn't load, and marks the rows that needed
    // it "Unknown" (not a confident "needs license" / "not downloaded").
    const [registry, useCtx, [install, method, license]] = await Promise.all([
      apiFetch("/api/v1/model-registry/entries"),
      apiFetch("/api/v1/model-registry/use-context"),
      Promise.allSettled([
        apiFetch("/api/v1/models"),
        apiFetch("/api/v1/settings"),
        apiFetch("/api/v1/model-registry/acceptance/list"),
      ]),
    ]);
    if (load !== _loadCount) return; // out of date: the newer load draws
    /** @type {(r: PromiseSettledResult<any>) => any} */
    const value = (r) => (r.status === "fulfilled" ? r.value : null);
    const [legacy, settings, acceptances] = [value(install), value(method), value(license)];
    /** @type {(r: PromiseSettledResult<any>) => string} */
    const why = (r) => (r.status === "rejected" ? r.reason?.message || String(r.reason) : "");
    /** @type {Array<[string, PromiseSettledResult<any>]>} */
    const named = [
      ["install status", install],
      ["the face model in use", method],
      ["your accepted licenses", license],
    ];
    const failures = named.flatMap(([what, r]) => (why(r) ? [{ what, why: why(r) }] : []));
    feState.unknown = { install: why(install), method: why(method), license: why(license) };
    feState.currentUseContext = useCtx?.use_context || "unspecified";
    // (Settings didn't load: no guess — embedder rows read "Unknown".)
    feState.activeFaceMethod = settings ? settings.face_embedding_method || "sface" : null;
    feState.installState = _buildInstallStateMap(legacy);
    feState.requiresAckIds = _collectRequiresAckIds(registry.groups || []);
    feState.acceptedIds = _collectAcceptedIds(acceptances);
    // The list is the truth once it loads; until then, what was accepted
    // or withdrawn on this page stands.
    if (acceptances) feState.decided = new Map();
    for (const [id, ok] of feState.decided) if (ok) feState.acceptedIds.add(id);
    // The server's load-gate decision per row: picks the acceptance
    // dialog's mode (e.g. ask for commercial-use rights).
    feState.loadStates = new Map();
    feState.entries = new Map();
    for (const group of registry.groups || []) {
      for (const e of group.entries || []) {
        feState.entries.set(e.id, e);
        if (e.load_state) feState.loadStates.set(e.id, e.load_state);
      }
    }
    feState.reacceptIds = _collectReacceptIds(acceptances);
    feState.catalogEntryIds = _collectCatalogEntryIds(registry.groups || []);
    container.innerHTML =
      loadFailureNoticeHTML(failures) +
      damagedLogNoticeHTML(acceptances) +
      _renderUnifiedPickerHTML(registry.groups || []);
    // Rows still mid-operation (a reload from elsewhere: another row's
    // change, Retry) stay locked and keep showing their progress; each
    // operation unlocks its own row when it ends (_clearRowBusy).
    // (One the new list doesn't draw keeps a line of its own.)
    const undrawn = [...feState.busyRowIds].filter(
      (id) => !_setRowStatusBusy(id, feState.busyText.get(id) || "Working…"),
    );
    container.insertAdjacentHTML("beforeend", busyFallbackHTML(undrawn));
  } catch (err) {
    if (load !== _loadCount) return;
    // An operation still running is said, not hidden (its progress comes
    // back with the list).
    const busy = feState.busyRowIds.size
      ? " A model download or removal is still running (below)."
      : "";
    const reason = String(err.message || err).replace(/\.+$/, "");
    container.innerHTML =
      `<p class="setting-muted">Couldn't load the model list: ${esc(reason)}.${busy} ` +
      `<button class="modal-btn modal-btn-secondary" data-action="loadFaceEmbedderPicker">Retry</button></p>` +
      busyFallbackHTML();
  } finally {
    if (load === _loadCount) container.removeAttribute("aria-busy");
  }
}

/** Counts picker loads: only the newest draws. */
let _loadCount = 0;

/**
 * Build registry-id → install-state map by joining the legacy
 * /api/v1/models feature list against ``LEGACY_FEATURE_MAP``.
 *
 * @param {Array<object> | null} legacyFeatures
 * @returns {Map<string, object>}
 */
function _buildInstallStateMap(legacyFeatures) {
  const map = new Map();
  if (!Array.isArray(legacyFeatures)) return map;
  // Index legacy features by label for the join.
  const byLabel = new Map();
  for (const f of legacyFeatures) {
    if (f && f.label) byLabel.set(f.label, f);
  }
  for (const [registryId, link] of Object.entries(LEGACY_FEATURE_MAP)) {
    if (link.legacy_label && byLabel.has(link.legacy_label)) {
      map.set(registryId, byLabel.get(link.legacy_label));
    }
  }
  return map;
}

/**
 * Pivot the per-license groups returned by the API into per-kind
 * groups for rendering, with permissive vs restricted as a
 * sub-grouping within each kind.
 *
 * @param {Array<{title: string, subtitle: string, entries: Array<object>}>} groups
 */
function _pivotByKind(groups) {
  /** @type {Map<string, {permissive: Array<object>, restricted: Array<object>}>} */
  const byKind = new Map();
  for (const group of groups) {
    for (const entry of group.entries || []) {
      const kind = entry.kind || "other";
      if (!byKind.has(kind)) byKind.set(kind, { permissive: [], restricted: [] });
      const bucket = byKind.get(kind);
      if (!bucket) continue;
      if (entry.requires_explicit_ack === true) {
        bucket.restricted.push(entry);
      } else {
        bucket.permissive.push(entry);
      }
    }
  }
  return byKind;
}

/**
 * Render the unified picker HTML.
 *
 * @param {Array<{title: string, subtitle: string, entries: Array<object>}>} groups
 */
function _renderUnifiedPickerHTML(groups) {
  if (!groups.length) {
    return '<p class="setting-muted">No models registered.</p>';
  }
  const byKind = _pivotByKind(groups);
  if (byKind.size === 0) {
    return '<p class="setting-muted">No models registered.</p>';
  }
  let html = `<div class="fe-picker-header">${_useContextControl()}</div>
    <p class="fe-scope-note">
      Installed models are shared across all your libraries. Only the
      <strong>active</strong> model (highlighted) is chosen per library.
    </p>`;

  const orderedKinds = [
    ...KIND_ORDER.filter((k) => byKind.has(k)),
    ...[...byKind.keys()].filter((k) => !KIND_ORDER.includes(k)),
  ];

  for (const kind of orderedKinds) {
    const bucket = byKind.get(kind);
    if (!bucket) continue;
    const label = KIND_LABELS[kind] || kind;
    const rows = [...bucket.permissive, ...bucket.restricted]
      .map((e) => _renderRow(e))
      .join("");
    html += `<div class="fe-kind">
      <div class="fe-kind-title">${esc(label)}</div>
      <table class="fe-table">
        <thead>
          <tr>
            <th class="fe-th-name">Model</th>
            <th class="fe-th-license">License</th>
            <th class="fe-th-status">Status</th>
            <th class="fe-th-size">Size</th>
            <th class="fe-th-action"></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }
  return html;
}

/**
 * Render one <tr> row for a registry entry.
 *
 * @param {object} entry
 */
function _renderRow(entry) {
  const isDefault = entry.default_for_kind === true;
  const install = feState.installState.get(entry.id);

  // Single source of truth: derive the row's full state once and
  // pass it to every cell renderer. Status, size, menu, and the
  // .fe-row-active class all key off this object — no flag-juggling
  // per cell, no chance of the halves disagreeing.
  const state = _deriveRowState(entry, install);

  const { text: licenseText, cls: licenseClass, tip: licenseTip } = licenseBadge(entry);
  const licenseDetail = entry.license_summary
    ? `<button class="fe-license-tip"
              data-action="_feLicenseInfoToggle"
              data-arg0="${escapeAttr(entry.id)}"
              data-arg1="${escapeAttr(entry.license_summary)}"
              aria-label="License details">ⓘ</button>`
    : "";

  const defaultMarker = isDefault
    ? `<span class="fe-name-default">Default</span>`
    : "";

  const menu = _buildMenuFromState(entry, state);
  const actionCell = menu.length
    ? `<div class="fe-overflow">
        <button class="fe-overflow-trigger" data-action="_feOverflowToggle"
                data-arg0="${escapeAttr(entry.id)}" aria-label="Actions" title="Actions">⋯</button>
        <div class="fe-overflow-menu" id="fe-ovr-${escapeAttr(entry.id)}">${menu.map(_renderMenuItem).join("")}</div>
      </div>`
    : `<span class="fe-actions-empty">—</span>`;

  // The row's "active" highlight reflects RUNNING — not just the
  // setting match. A picked-but-blocked row is NOT visually highlighted
  // as the active model because it's not the active model at runtime.
  const rowActive = state.lifecycle === ROW_LIFECYCLE.RUNNING;
  return `<tr class="fe-row${rowActive ? " fe-row-active" : ""}" data-entry-id="${escapeAttr(entry.id)}">
    <td class="fe-cell-name">
      <div class="fe-name-line">
        <span class="fe-name">${esc(entry.display_name)}</span>
        ${defaultMarker}
      </div>
    </td>
    <td class="fe-cell-license">
      <span class="${licenseClass}" title="${escapeAttr(licenseTip)}">${licenseText}</span>
      ${licenseDetail}
    </td>
    <td class="fe-cell-status">${_statusCell(state)}</td>
    <td class="fe-cell-size">${_sizeCell(state)}</td>
    <td class="fe-cell-action">${actionCell}</td>
  </tr>`;
}


/**
 * Render the install-state pill (Ready / Not downloaded / Fallback / …).
 *
 * @param {object} install
 */
function _renderInstallStateBadge(install) {
  const status = install.status || "unknown";
  const label = {
    ready: "installed",
    fallback: "fallback active",
    partial: "incomplete",
    missing: "not downloaded",
    no_library: "needs library",
    unknown: "status unknown",
  }[status] || status;
  const cssStatus = ["ready", "fallback"].includes(status)
    ? "ok"
    : status === "partial" || status === "no_library"
      ? "partial"
      : "missing";
  const size = install.size_bytes > 0 ? ` · ${_formatBytes(install.size_bytes)}` : "";
  return `<span class="fe-badge fe-badge-install fe-install-${cssStatus}">${esc(label)}${esc(size)}</span>`;
}

/**
 * Render install / uninstall controls for entries that link to a
 * legacy feature.
 *
 * @param {object} entry
 * @param {object} install
 */
function _renderInstallControlsHTML(entry, install) {
  const installHint = install.install_hint;
  if (install.status === "missing" && installHint) {
    return `<button class="modal-btn modal-btn-secondary fe-install-btn"
                    data-action="installFaceEmbedderViaHint"
                    data-arg0="${escapeAttr(installHint)}"
                    data-arg1="${escapeAttr(entry.display_name)}">Install</button>`;
  }
  const buttons = [];
  if (install.status === "ready") {
    buttons.push(`<button class="modal-btn modal-btn-secondary fe-uninstall-btn"
                  data-action="uninstallFaceEmbedderEntry"
                  data-arg0="${escapeAttr(entry.id)}"
                  data-arg1="${escapeAttr(install.label || entry.display_name)}">Uninstall</button>`);
    buttons.push(`<button class="modal-btn modal-btn-secondary fe-redownload-btn"
                  data-action="redownloadFaceEmbedderEntry"
                  data-arg0="${escapeAttr(entry.id)}"
                  data-arg1="${escapeAttr(install.label || entry.display_name)}">Redownload</button>`);
  }
  return buttons.join(" ");
}

/**
 * "Use context" selector. Lets the user declare how they're using
 * Best Photo Picker (personal / research / commercial / unspecified). Drives
 * the commercial-mode hard-block on restricted models. Posts to
 * /api/v1/model-registry/use-context on change.
 */
function _useContextControl() {
  // Three radio cards — same UI as the wizard's Use-Context step
  // so users see a familiar control after onboarding. Both renderers
  // import USE_CONTEXT_OPTIONS from use-context-options.mjs; the
  // copy can't drift because there's only one definition.
  const cards = USE_CONTEXT_OPTIONS
    .map(
      (o) => `<button class="onb-context-card${
        o.value === feState.currentUseContext ? " selected" : ""
      }" data-action="setFaceEmbedderUseContext"
        data-arg0="${escapeAttr(o.value)}">
        <div class="onb-context-card-title">${esc(o.title)}</div>
        <div class="onb-context-card-desc">${esc(o.desc)}</div>
      </button>`,
    )
    .join("");
  return `<div class="fe-use-context-block">
    <div class="fe-use-context-label">How are you using Best Photo Picker?</div>
    <div class="fe-use-context-desc">
      Applies to all libraries on this device. Stored locally only.
    </div>
    <div class="onb-context-cards fe-use-context-cards">${cards}</div>
  </div>`;
}

/**
 * Test seam: render a single row so unit tests can assert the action
 * controls produced for each install state without standing up the
 * full picker + network mocks.
 *
 * @param {object} entry
 * @returns {string}
 */
export function _renderRowForTests(entry) {
  return _renderRow(entry);
}

registerAction("loadFaceEmbedderPicker", loadFaceEmbedderPicker);
registerAction("openFaceEmbedderAcceptance", openFaceEmbedderAcceptance);
registerAction("closeFaceEmbedderAcceptance", closeFaceEmbedderAcceptance);
registerAction("confirmFaceEmbedderAcceptance", confirmFaceEmbedderAcceptance);
registerAction("revokeFaceEmbedderAcceptance", revokeFaceEmbedderAcceptance);
registerAction("installFaceEmbedderViaHint", installFaceEmbedderViaHint);
registerAction("uninstallFaceEmbedderEntry", uninstallFaceEmbedderEntry);
registerAction("redownloadFaceEmbedderEntry", redownloadFaceEmbedderEntry);
registerAction("ensureCatalogWeights", ensureCatalogWeights);
registerAction("setFaceEmbedderUseContext", setFaceEmbedderUseContext);
registerAction("setActiveFaceEmbedder", setActiveFaceEmbedder);
registerAction("_feOverflowToggle", _feOverflowToggle);
registerAction("_feLicenseInfoToggle", _feLicenseInfoToggle);

if (typeof document !== "undefined") {
  // A license, use context or model file changed: re-render the picker
  // if it's on screen (it reloads anyway each time Settings opens).
  document.addEventListener(MODELS_CHANGED_EVENT, () => {
    if (document.getElementById("settings-overlay")?.classList.contains("visible")) {
      void loadFaceEmbedderPicker();
    }
  });
}

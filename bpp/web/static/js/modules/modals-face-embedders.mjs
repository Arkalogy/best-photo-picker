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
  _sizeCell,
  _statusCell,
} from "./face-embedders-rowstate.mjs";
import {
  _collectAcceptedIds,
  _collectCatalogEntryIds,
  _collectRequiresAckIds,
  _resetFaceEmbedderStateForTests,
  _seedFaceEmbedderStateForTests,
  feState,
} from "./face-embedders-state.mjs";
import { _formatBytes } from "./format-helpers.mjs";
import { escapeAttr, esc } from "./text-format.mjs";
import { toast } from "./toast.mjs";
import { USE_CONTEXT_OPTIONS } from "./use-context-options.mjs";

// Re-export so the existing test/import path (modals-face-embedders.mjs)
// keeps working after the state + acceptance + actions + rowstate split.
export {
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
 * Join table: registry id → legacy feature label and per-feature
 * toggle key (when applicable). Used to overlay install state on the
 * registry picker. Transitional — when each model's file-install
 * logic lands in the registry layer this mapping goes away.
 *
 * Keys NOT in this map render without install controls (registry-
 * catalog-only entries). Values without a ``toggle_key`` render
 * without an enable toggle.
 */
const _LEGACY_FEATURE_MAP = {
  // Face embedders
  sface_yunet: { legacy_label: "Face recognition", toggle_key: null },
  dlib_face_recognition_resnet_v1: {
    legacy_label: "Face recognition",
    toggle_key: null,
  },
  insightface_buffalo_s: { legacy_label: null, toggle_key: null },
  // Face detectors
  insightface_scrfd_25g: {
    legacy_label: "SCRFD face detection",
    toggle_key: "model_scrfd",
  },
  opencv_yunet: {
    legacy_label: "Face detection (fallback)",
    toggle_key: null,
  },
  // Semantic search
  openai_clip_vit_b32_onnx: {
    legacy_label: "Smart search & dedup",
    toggle_key: null,
  },
  // Pet detection
  ultralytics_yolov11n_pets: {
    legacy_label: "Pet detection",
    toggle_key: null,
  },
  // Nudity classifier
  nudenet_320n: { legacy_label: "Content filter", toggle_key: null },
  // Inpainting
  lama_inpaint_research: {
    legacy_label: "AI object removal",
    toggle_key: null,
  },
};

const _KIND_LABELS = {
  face_embedder: "Face embedders",
  face_detector: "Face detectors",
  semantic_search: "Semantic search",
  pet_detector: "Pet detection",
  nudity_classifier: "Content filter",
  inpainter: "AI object removal",
};

/**
 * Fetch + render the unified picker into ``#face-embedder-picker``.
 * Called from ``loadModelsList`` whenever Settings → Models opens.
 */
export async function loadFaceEmbedderPicker() {
  const container = document.getElementById("face-embedder-picker");
  if (!container) return;
  _feInstallGlobalHandlers();
  container.innerHTML = '<p class="setting-muted">Loading…</p>';
  try {
    const [registry, useCtx, legacy, settings, acceptances] =
      await Promise.all([
        apiFetch("/api/v1/model-registry/entries"),
        apiFetch("/api/v1/model-registry/use-context"),
        apiFetch("/api/v1/models").catch(() => null),
        apiFetch("/api/v1/settings").catch(() => null),
        apiFetch("/api/v1/model-registry/acceptance/list").catch(
          () => null,
        ),
      ]);
    feState.currentUseContext = useCtx?.use_context || "unspecified";
    feState.activeFaceMethod = settings?.face_embedding_method || "sface";
    feState.installState = _buildInstallStateMap(legacy);
    feState.requiresAckIds = _collectRequiresAckIds(registry.groups || []);
    feState.acceptedIds = _collectAcceptedIds(acceptances);
    feState.catalogEntryIds = _collectCatalogEntryIds(registry.groups || []);
    // The reload reflects post-operation truth; clear the busy gate so
    // freshly-completed operations don't keep their row locked out.
    feState.busyRowIds = new Set();
    container.innerHTML = _renderUnifiedPickerHTML(registry.groups || []);
  } catch (err) {
    container.innerHTML = `<p class="setting-muted">Could not load model registry: ${esc(err.message || String(err))}</p>`;
  }
}

/**
 * Build registry-id → install-state map by joining the legacy
 * /api/v1/models feature list against ``_LEGACY_FEATURE_MAP``.
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
  for (const [registryId, link] of Object.entries(_LEGACY_FEATURE_MAP)) {
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

  // Stable kind ordering for the UI.
  const kindOrder = [
    "face_embedder",
    "face_detector",
    "semantic_search",
    "pet_detector",
    "nudity_classifier",
    "inpainter",
  ];
  const orderedKinds = [
    ...kindOrder.filter((k) => byKind.has(k)),
    ...[...byKind.keys()].filter((k) => !kindOrder.includes(k)),
  ];

  for (const kind of orderedKinds) {
    const bucket = byKind.get(kind);
    if (!bucket) continue;
    const label = _KIND_LABELS[kind] || kind;
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
  const isRestricted = entry.requires_explicit_ack === true;
  const isDefault = entry.default_for_kind === true;
  const install = feState.installState.get(entry.id);

  // Single source of truth: derive the row's full state once and
  // pass it to every cell renderer. Status, size, menu, and the
  // .fe-row-active class all key off this object — no flag-juggling
  // per cell, no chance of the halves disagreeing.
  const state = _deriveRowState(entry, install);

  const licenseText = isRestricted ? "Restricted" : "Permissive";
  const licenseClass = isRestricted ? "fe-license-restricted" : "fe-license-permissive";
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
      <span class="${licenseClass}">${licenseText}</span>
      ${licenseDetail}
    </td>
    <td class="fe-cell-status">${_statusCell(state)}</td>
    <td class="fe-cell-size">${_sizeCell(state)}</td>
    <td class="fe-cell-action">${actionCell}</td>
  </tr>`;
}

/**
 * Replace a row's status cell with a transient busy indicator while a
 * long operation (download) is in flight. The picker reload at the end
 * of the operation restores the real status. Honors the "nothing should
 * be silent" rule — a multi-second download must show progress in-row,
 * not just a fire-and-forget toast.
 *
 * @param {string} registryId
 * @param {string} text  e.g. "Downloading…"
 */
export function _setRowStatusBusy(registryId, text) {
  // Marks the row as in-flight so subsequent ⋯ opens and action
  // clicks bounce off the busy gate until ``loadFaceEmbedderPicker``
  // clears it. Without this, the bug screenshot showed an open menu
  // mid-download offering Redownload + Uninstall + Use this model
  // simultaneously — any of which would corrupt the operation.
  feState.busyRowIds.add(registryId);
  // Registry ids are simple slugs (e.g. "opencv_yunet"); CSS.escape is the
  // correct guard but is absent under jsdom, so fall back to a quote-strip.
  const safeId =
    typeof CSS !== "undefined" && CSS.escape
      ? CSS.escape(registryId)
      : String(registryId).replace(/["\\]/g, "");
  const cell = document.querySelector(
    `tr[data-entry-id="${safeId}"] .fe-cell-status`,
  );
  if (cell) {
    cell.innerHTML = `<span class="fe-status fe-status-busy"><span class="fe-spinner"></span>${esc(text)}</span>`;
  }
  // Close the menu if it was open when the busy state started.
  const openMenu = document.getElementById("fe-ovr-" + registryId);
  if (openMenu) openMenu.classList.remove("open", "fe-overflow-menu--up");
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

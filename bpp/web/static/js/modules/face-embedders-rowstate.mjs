// @ts-check
/**
 * Row-state derivation + menu/cell building for the Settings → Models
 * picker — the single source of truth for "what state is this row in?"
 * and "what menu items + status/size cells does it get?".
 *
 * Split out of ``modals-face-embedders.mjs`` for the 500-LOC cap. This
 * is a leaf (reads ``feState``, builds data + small HTML) with no
 * back-calls into the picker module, so there is no import cycle.
 */

import { FACE_EMBEDDER_METHOD_VALUE, LEGACY_FEATURE_MAP } from "./face-embedders-catalog.mjs";
import { _withoutGuesses } from "./face-embedders-guesses.mjs";
import { licenseBlocker, licenseFacts } from "./face-embedders-license.mjs";
import { feState } from "./face-embedders-state.mjs";
import { _formatBytes } from "./format-helpers.mjs";
import { escapeAttr, esc } from "./text-format.mjs";

// Status + size cells live in face-embedders-cells.mjs (500-LOC cap).

/**
 * Map registry id → ``face_embedding_method`` setting value. Only
 * face_embedder entries have a setting value because the analyze
 * dispatcher only reads one — non-face entries pass through.
 */

// ── Row state — single source of truth ──────────────────────────────
//
// Every picker row's user-visible state is one of these lifecycle
// values. Status cell label, action menu items, and size cell all
// derive from this single enum so the row halves can't disagree.
// Adding a new state means adding one enum value and its mapping in
// the three consumers — not editing each consumer separately.

/**
 * @typedef {(
 *   "running"
 *   | "picked-needs-download"
 *   | "picked-needs-license"
 *   | "needs-license"
 *   | "ready"
 *   | "backup-available"
 *   | "needs-runtime"
 *   | "partial"
 *   | "not-downloaded"
 *   | "unknown"
 * )} RowLifecycle
 */

export const ROW_LIFECYCLE = Object.freeze({
  /** Picked in settings AND on disk AND license accepted. */
  RUNNING: "running",
  /** Picked but weights aren't on disk. Runtime falls back to a substitute. */
  PICKED_NEEDS_DOWNLOAD: "picked-needs-download",
  /** Picked, weights on disk, license missing or revoked. */
  PICKED_NEEDS_LICENSE: "picked-needs-license",
  /** Not picked, weights on disk, but license missing — can't be used as-is. */
  NEEDS_LICENSE: "needs-license",
  /**
   * Face-embedder row, on disk, license clear, NOT the active method.
   * The user can switch to it via "Use this model" (the menu surfaces
   * that action because face_embedding_method is a real setting).
   */
  READY: "ready",
  /**
   * Non-embedder row, on disk, license clear, NOT the kind's default.
   * Distinct from READY because the picker doesn't expose a switch
   * for non-embedder kinds (no setting names which one runs). The
   * server marks what runs as primary_for_kind; other rows are
   * available only as auto-fallback if the primary can't load. The
   * menu drops "Use this model" entirely for these rows so the user
   * doesn't expect an action that has no wiring.
   */
  BACKUP_AVAILABLE: "backup-available",
  /** Weights on disk but Python runtime package is missing. */
  NEEDS_RUNTIME: "needs-runtime",
  /** Some files on disk, some missing. */
  PARTIAL: "partial",
  /** Weights aren't on this device (and aren't being downloaded right now). */
  NOT_DOWNLOADED: "not-downloaded",
  /**
   * Something this row's status depends on couldn't be loaded (install
   * state, the acceptance list, the active face method): the picker says
   * so above the list rather than guess.
   */
  UNKNOWN: "unknown",
});

/**
 * Derive the full row state from raw inputs + module state.
 *
 * Single source of truth for "what state is this row in?" Reads
 * module-level state (``feState.activeFaceMethod``, ``feState.acceptedIds``) so
 * callers don't have to pass them. The returned object is shaped
 * for the three consumers: ``_statusCell``, ``_buildActionMenu``,
 * and ``_sizeCell``.
 *
 * @param {object} entry  registry entry as returned by /entries
 * @param {object | undefined} install  install state from /api/v1/models
 * @returns {{
 *   lifecycle: RowLifecycle,
 *   installUnknown: boolean,
 *   licenseUnknown: boolean,
 *   methodUnknown: boolean,
 *   isFaceEmbedder: boolean,
 *   methodValue: string | undefined,
 *   isPicked: boolean,
 *   restricted: boolean,
 *   accepted: boolean,
 *   licenseBlocked: boolean,
 *   needsReaccept: boolean,
 *   needsCommercialRights: boolean,
 *   unavailable: boolean,
 *   onDisk: boolean,
 *   isCatalog: boolean,
 *   isCatalogEntry: boolean,
 *   hasDownloadable: boolean,
 *   files: Array<object>,
 *   installLabel: string,
 *   installStatus: string | undefined,
 *   installHint: string | undefined,
 *   sizeBytes: number,
 *   expectedSize: number,
 * }}
 */
export function _deriveRowState(entry, install) {
  const isFaceEmbedder = entry?.kind === "face_embedder";
  // The server says which face_embedding_method value picks the entry
  // (a plugin embedder's too); the built-in table answers for an entry
  // that arrived without it.
  const methodValue = entry?.method_value ?? FACE_EMBEDDER_METHOD_VALUE[entry?.id];
  // "isPicked" — this row is the one currently producing results
  // for its kind. Two paths:
  //   * face_embedder: dispatched by setting (face_embedding_method
  //     names exactly one). The picked entry is "running" when its
  //     weights are on disk.
  //   * Other kinds (face_detector, pet, content filter, …): no
  //     user-pickable setting. The server says which entry the runtime
  //     uses (``primary_for_kind``: the kind's default, or — for a kind
  //     with no default, like the content filter, pet detector and
  //     object removal — the entry its loader selects). Treat that as
  //     picked so the row reads "Running" once its weights arrive, not
  //     "Backup". One signal, decided server-side.
  //   * Supporting models that run together with the default (the
  //     BlazeFace detectors next to YuNet) are in use too, not backups.
  const isPicked =
    (isFaceEmbedder &&
      methodValue !== undefined &&
      methodValue === feState.activeFaceMethod) ||
    (!isFaceEmbedder &&
      (entry?.primary_for_kind === true || entry?.runs_alongside_default === true));

  // The picker's map is the source for the row's registry facts (accept /
  // withdraw update it); an entry the map doesn't hold is read as given,
  // as is a load-gate decision the map has none of.
  const live = /** @type {any} */ (feState.entries.get(entry?.id ?? "") ?? entry);
  const restricted = live?.requires_explicit_ack === true;
  const loadState = live?.load_state ?? entry?.load_state;
  const {
    accepted,
    licenseBlocked,
    needsReaccept,
    needsCommercialRights,
    unavailable,
    licenseUnknown,
  } = licenseFacts(entry?.id, restricted, loadState);

  const files = Array.isArray(install?.files) ? install.files : [];
  const hasDownloadable = files.some((f) => f && f.name);
  const filesOnDisk = files.some((f) => f && f.exists && f.name);
  const isCatalog = !install;
  // Authoritative "weights come via the catalog ensure-weights endpoint"
  // signal from the backend. Independent of whether a (fileless) legacy
  // install record is attached — LaMa / NudeNet have BOTH a legacy row
  // (empty file list) AND catalog fetch, so the old `!install` inference
  // misrouted them and dropped their Download action.
  const isCatalogEntry = live?.is_catalog_entry === true;
  const catalogOnDisk = entry?.catalog_on_disk === true;
  // OR, not "isCatalog && catalogOnDisk": catalog_on_disk is the real
  // on-disk truth for catalog entries and is false for non-catalog ones,
  // so consult it regardless of whether a legacy record is attached.
  // The old guard ignored it whenever a fileless legacy row existed,
  // leaving LaMa / NudeNet stuck at "Not downloaded" even once fetched.
  // Weights shipped inside an installed library (the Haar cascade in
  // opencv-python) are always present.
  const onDisk = filesOnDisk || catalogOnDisk || entry?.ships_with_runtime === true;

  // Lifecycle: exactly one value. Order matters — the picked-row
  // states take priority over the not-picked equivalents because
  // they carry the cross-row narrative ("you picked this, here's
  // why it can't run").
  // What this row needs but the last load couldn't fetch: its install
  // record (rows linked to a legacy feature — catalog ones too: that
  // record says "needs runtime" / "incomplete"); its acceptance (when the
  // server sends no load state); whether it's the face model in use.
  const legacyLabel = entry?.legacy_label ?? LEGACY_FEATURE_MAP[entry?.id]?.legacy_label;
  const installUnknown = !!feState.unknown.install && !!legacyLabel;
  const methodUnknown = !!feState.unknown.method && isFaceEmbedder && methodValue !== undefined;
  const unknown = installUnknown || methodUnknown || licenseUnknown;

  /** @type {RowLifecycle} */
  let lifecycle;
  if (unknown) {
    lifecycle = ROW_LIFECYCLE.UNKNOWN;
  } else if (isPicked && onDisk && !licenseBlocked) {
    lifecycle = ROW_LIFECYCLE.RUNNING;
  } else if (isPicked && licenseBlocked) {
    // License gate takes priority over the download gate — license
    // blocks regardless of whether weights are present.
    lifecycle = ROW_LIFECYCLE.PICKED_NEEDS_LICENSE;
  } else if (isPicked && !onDisk) {
    lifecycle = ROW_LIFECYCLE.PICKED_NEEDS_DOWNLOAD;
  } else if (onDisk && licenseBlocked) {
    lifecycle = ROW_LIFECYCLE.NEEDS_LICENSE;
  } else if (install?.status === "partial") {
    // PARTIAL wins over READY because install.status="partial" means
    // SOME files exist but the model can't actually load. The binary
    // onDisk predicate fires true when any file exists; we trust the
    // install layer's explicit partial signal here.
    lifecycle = ROW_LIFECYCLE.PARTIAL;
  } else if (install?.status === "no_library") {
    lifecycle = ROW_LIFECYCLE.NEEDS_RUNTIME;
  } else if (onDisk) {
    // For face_embedder rows the user has a setting-driven switch
    // ("Use this model" → face_embedding_method) → READY signals the
    // model is pickable. For other kinds (face_detector, content
    // filter, etc.) there's no user switch — the server says which
    // row runs (primary_for_kind), and other rows can only run as
    // auto-fallback. Reading those as READY misleads the user into
    // looking for an activate action that doesn't exist; mark them
    // BACKUP_AVAILABLE so the status + menu reflect reality.
    lifecycle = isFaceEmbedder
      ? ROW_LIFECYCLE.READY
      : ROW_LIFECYCLE.BACKUP_AVAILABLE;
  } else {
    lifecycle = ROW_LIFECYCLE.NOT_DOWNLOADED;
  }

  return {
    lifecycle,
    installUnknown,
    licenseUnknown,
    methodUnknown,
    isFaceEmbedder,
    methodValue,
    isPicked,
    restricted,
    accepted,
    licenseBlocked,
    needsReaccept,
    needsCommercialRights,
    unavailable,
    onDisk,
    isCatalog,
    isCatalogEntry,
    hasDownloadable,
    files,
    installLabel: install?.label || entry?.display_name || "",
    installStatus: install?.status,
    installHint: install?.install_hint,
    sizeBytes:
      install && typeof install.size_bytes === "number"
        ? install.size_bytes
        : 0,
    expectedSize:
      typeof entry?.expected_download_size_bytes === "number"
        ? entry.expected_download_size_bytes
        : 0,
  };
}

/**
 * Build the row's ⋯-menu items from its derived state. Each lifecycle
 * value implies a fixed set of items + which ones are enabled; this
 * function is just the lookup. No flag-juggling, no cross-row guesses.
 *
 * Items, in order:
 *   1. License row (restricted entries only) — always clickable so the
 *      user can re-read the terms at any time.
 *   2. Download / Redownload — gated on license; chooses between
 *      installable redownload (legacy /api/v1/models/redownload) and
 *      catalog ensure-weights endpoint based on state.isCatalog.
 *   3. Use this model (face embedders only) — gated on the row being
 *      RUNNING-eligible. "In use ✓" shows only when actually RUNNING;
 *      a picked-but-blocked row gets a disabled "Use this model" with
 *      the specific blocker spelled out in the sub-line.
 *   4. Uninstall — enabled when weights are on disk.
 *
 * @param {object} entry  registry entry
 * @param {ReturnType<typeof _deriveRowState>} state
 * @returns {Array<object>} menu-item descriptors for _renderMenuItem
 */
export function _buildMenuFromState(entry, state) {
  /** @type {Array<object>} */
  const items = [];
  const downloadBlocked = state.licenseBlocked;
  const lc = state.lifecycle;

  // 1. License — restricted entries only. ALWAYS clickable (hiding it
  //    after acceptance was the earlier regression).
  if (state.restricted) {
    items.push({
      // Make ownership unambiguous: the license belongs to the
       // upstream model (InsightFace, Ultralytics AGPL, etc.), not to
       // bpp. "Review & accept license" read like we were asking for
       // consent to OUR license; "the model's license" pins it.
       // Once accepted, the item states the fact ("accepted ✓") but
       // stays clickable so the user can re-read the terms any time.
      label: state.unavailable && !state.accepted
        ? "View the model’s license"
        : state.needsCommercialRights
          ? "Confirm your commercial-use rights for this model"
          : state.accepted
            ? "Model’s license accepted"
            : state.needsReaccept
              ? "Re-accept the model’s license (terms updated)"
              : "Review & accept the model’s license",
      action: "openFaceEmbedderAcceptance",
      // Withdrawn / blocked: read-only — accepting couldn't enable it.
      args: state.unavailable ? [entry.id, "view"] : [entry.id],
      enabled: true,
      // ✓ only when the license is fully satisfied.
      done: state.accepted && !state.needsCommercialRights && !state.unavailable,
    });
  }

  // 2. Download / Redownload. Catalog entries route through a
  //    different action; installable entries hit the legacy
  //    redownload endpoint.
  const acceptFirst = licenseBlocker(state).short;
  const downloadSub = downloadBlocked ? acceptFirst : null;
  if (state.hasDownloadable) {
    items.push({
      label: state.onDisk ? "Redownload" : "Download",
      sub: downloadSub,
      needs: ["install", "license"],
      action: "redownloadFaceEmbedderEntry",
      args: [entry.id, state.installLabel],
      enabled: !downloadBlocked,
    });
  } else if (lc === ROW_LIFECYCLE.NEEDS_RUNTIME && state.installHint) {
    items.push({
      label: "Install runtime…",
      sub: state.installHint,
      needs: ["install"],
      action: "installFaceEmbedderViaHint",
      args: [state.installHint, entry.display_name],
      enabled: true,
    });
  } else if (state.isCatalogEntry || state.isCatalog) {
    // Catalog entry (LaMa, NudeNet, buffalo_s): weights fetched via the
    // ensure-weights endpoint. Primary signal is the backend's
    // is_catalog_entry flag — that's what catches LaMa / NudeNet, which
    // ALSO carry a fileless legacy feature row (so isCatalog=!install is
    // false for them). The `|| isCatalog` keeps the legacy no-install
    // path (buffalo_s) working unchanged.
    const sizeLabel = state.expectedSize
      ? ` (~${_formatBytes(state.expectedSize)})`
      : "";
    items.push({
      label: state.onDisk ? "Redownload" : `Download${sizeLabel}`,
      sub: downloadSub,
      needs: ["install", "license"],
      action: "ensureCatalogWeights",
      args: [entry.id, entry.display_name],
      enabled: !downloadBlocked,
    });
  }

  // 3. Use this model — face embedders only. Maps directly to
  //    lifecycle: only RUNNING earns "In use ✓"; every other state
  //    shows a disabled "Use this model" with the specific blocker.
  if (state.isFaceEmbedder && state.methodValue) {
    if (lc === ROW_LIFECYCLE.RUNNING) {
      items.push({ label: "In use", enabled: false, done: true });
    } else {
      let useSub = null;
      if (state.licenseBlocked) {
        useSub = acceptFirst;
      } else if (!state.onDisk) {
        // "Download to activate" when the picked row is the one
        // missing weights — emphasizes that the click completes a
        // selection, vs "Download it first" for an alternative the
        // user hasn't selected yet.
        useSub = state.isPicked ? "Download to activate" : "Download it first";
      }
      items.push({
        label: "Use this model",
        sub: useSub,
        // ("Download it first" may be wrong; switching away may not be.)
        needs: ["install", "license", "method"],
        action: "setActiveFaceEmbedder",
        args: [state.methodValue, entry.display_name, entry.id],
        enabled: !state.licenseBlocked && state.onDisk,
      });
    }
  }

  // 4. Uninstall — enabled when weights are on disk.
  items.push({
    label: "Uninstall",
    sub: state.onDisk ? null : "Not installed",
    needs: ["install"],
    action: "uninstallFaceEmbedderEntry",
    args: [entry.id, state.installLabel],
    enabled: state.onDisk,
    danger: true,
  });

  return _withoutGuesses(items, state);
}

/**
 * Build the action menu for a row. Public entry point — derives state
 * once via :func:`_deriveRowState` then defers to
 * :func:`_buildMenuFromState`. Trailing arguments are accepted (and
 * ignored) for backward compatibility with the test surface; derived
 * state is the single source of truth.
 *
 * @param {object} entry
 * @param {object} install
 * @param {...any} _legacyArgs  isActive / isFaceEmbedder / methodValue, ignored
 * @returns {Array<object>}
 */
export function _buildActionMenu(entry, install, ..._legacyArgs) {
  return _buildMenuFromState(entry, _deriveRowState(entry, install));
}

/**
 * Render one ⋯-menu item from a descriptor produced by
 * ``_buildActionMenu``. Disabled items carry no ``data-action`` (so a
 * click is a true no-op) and a muted sub-line explaining why they're
 * unavailable; completed items get a ✓.
 *
 * @param {object} item
 * @returns {string}
 */
export function _renderMenuItem(item) {
  const cls = [
    "fe-menu-item",
    item.danger && item.enabled ? "fe-menu-danger" : "",
    item.done ? "fe-menu-done" : "",
  ]
    .filter(Boolean)
    .join(" ");
  const check = item.done ? ' <span class="fe-menu-check">✓</span>' : "";
  const labelHtml = `<span class="fe-menu-label">${esc(item.label)}${check}</span>`;
  const subHtml = item.sub
    ? `<span class="fe-menu-sub">${esc(item.sub)}</span>`
    : "";
  if (!item.enabled) {
    return `<button class="${cls}" disabled>${labelHtml}${subHtml}</button>`;
  }
  const args = (item.args || [])
    .map((a, i) => `data-arg${i}="${escapeAttr(String(a))}"`)
    .join(" ");
  return `<button class="${cls}" data-action="${escapeAttr(item.action)}" ${args}>${labelHtml}${subHtml}</button>`;
}

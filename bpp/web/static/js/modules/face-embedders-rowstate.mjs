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

import { feState } from "./face-embedders-state.mjs";
import { _formatBytes } from "./format-helpers.mjs";
import { escapeAttr, esc } from "./text-format.mjs";

/**
 * Map registry id → ``face_embedding_method`` setting value. Only
 * face_embedder entries have a setting value because the analyze
 * dispatcher only reads one — non-face entries pass through.
 */
const _FACE_EMBEDDER_METHOD_VALUE = {
  sface_yunet: "sface",
  dlib_face_recognition_resnet_v1: "dlib",
  insightface_buffalo_s: "buffalo_s",
};

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
   * runtime uses default_for_kind as primary; non-default rows are
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
 *   isFaceEmbedder: boolean,
 *   methodValue: string | undefined,
 *   isPicked: boolean,
 *   restricted: boolean,
 *   accepted: boolean,
 *   licenseBlocked: boolean,
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
  const methodValue = _FACE_EMBEDDER_METHOD_VALUE[entry?.id];
  // "isPicked" — this row is the one currently producing results
  // for its kind. Two paths:
  //   * face_embedder: dispatched by setting (face_embedding_method
  //     names exactly one). The picked entry is "running" when its
  //     weights are on disk.
  //   * Other kinds (face_detector, pet, content filter, …): no
  //     user-pickable setting. The entry marked default_for_kind in
  //     the registry is THE one the runtime uses. Treat that as
  //     picked so DEFAULT-marked rows read "Running" instead of
  //     the misleading "Ready" once their weights arrive.
  const isPicked =
    (isFaceEmbedder &&
      methodValue !== undefined &&
      methodValue === feState.activeFaceMethod) ||
    (!isFaceEmbedder && entry?.default_for_kind === true);

  const restricted = entry?.requires_explicit_ack === true;
  const accepted = feState.acceptedIds.has(entry?.id);
  const licenseBlocked = restricted && !accepted;

  const files = Array.isArray(install?.files) ? install.files : [];
  const hasDownloadable = files.some((f) => f && f.name);
  const filesOnDisk = files.some((f) => f && f.exists && f.name);
  const isCatalog = !install;
  // Authoritative "weights come via the catalog ensure-weights endpoint"
  // signal from the backend. Independent of whether a (fileless) legacy
  // install record is attached — LaMa / NudeNet have BOTH a legacy row
  // (empty file list) AND catalog fetch, so the old `!install` inference
  // misrouted them and dropped their Download action.
  const isCatalogEntry = entry?.is_catalog_entry === true;
  const catalogOnDisk = entry?.catalog_on_disk === true;
  // OR, not "isCatalog && catalogOnDisk": catalog_on_disk is the real
  // on-disk truth for catalog entries and is false for non-catalog ones,
  // so consult it regardless of whether a legacy record is attached.
  // The old guard ignored it whenever a fileless legacy row existed,
  // leaving LaMa / NudeNet stuck at "Not downloaded" even once fetched.
  const onDisk = filesOnDisk || catalogOnDisk;

  // Lifecycle: exactly one value. Order matters — the picked-row
  // states take priority over the not-picked equivalents because
  // they carry the cross-row narrative ("you picked this, here's
  // why it can't run").
  /** @type {RowLifecycle} */
  let lifecycle;
  if (isPicked && onDisk && !licenseBlocked) {
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
    // filter, etc.) there's no user switch — the runtime picks the
    // default_for_kind row, and non-default rows can only run as
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
    isFaceEmbedder,
    methodValue,
    isPicked,
    restricted,
    accepted,
    licenseBlocked,
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

/**
 * Plain-English status label for the Status column. Drives entirely
 * off the row's lifecycle — no flag-juggling, no cross-row guessing.
 *
 * @param {ReturnType<typeof _deriveRowState>} state
 */
export function _statusCell(state) {
  const s = _STATUS_BY_LIFECYCLE[state.lifecycle];
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
 * @param {ReturnType<typeof _deriveRowState>} state
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
      label: state.accepted
        ? "Model’s license accepted"
        : "Review & accept the model’s license",
      action: "openFaceEmbedderAcceptance",
      args: [entry.id],
      enabled: true,
      done: state.accepted,
    });
  }

  // 2. Download / Redownload. Catalog entries route through a
  //    different action; installable entries hit the legacy
  //    redownload endpoint.
  const downloadSub = downloadBlocked ? "Accept the license first" : null;
  if (state.hasDownloadable) {
    items.push({
      label: state.onDisk ? "Redownload" : "Download",
      sub: downloadSub,
      action: "redownloadFaceEmbedderEntry",
      args: [entry.id, state.installLabel],
      enabled: !downloadBlocked,
    });
  } else if (lc === ROW_LIFECYCLE.NEEDS_RUNTIME && state.installHint) {
    items.push({
      label: "Install runtime…",
      sub: state.installHint,
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
        useSub = "Accept the license first";
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
    action: "uninstallFaceEmbedderEntry",
    args: [entry.id, state.installLabel],
    enabled: state.onDisk,
    danger: true,
  });

  return items;
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

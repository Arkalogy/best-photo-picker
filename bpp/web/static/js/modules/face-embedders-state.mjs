// @ts-check
/**
 * Shared mutable state for the Settings → Models picker, plus the
 * collectors that populate it and the unit-test seams that reset/seed
 * it.
 *
 * Split out of ``modals-face-embedders.mjs`` (the 1800+ line monster)
 * so the picker's render core, acceptance dialog, and action handlers
 * can each live in their own module while sharing one source of truth.
 *
 * State lives on a single exported object (``feState``) rather than as
 * exported ``let`` bindings: ES-module imports are read-only, so an
 * importer can't reassign an exported ``let``. Mutating a property of a
 * shared object works across modules — every consumer reads and writes
 * ``feState.<field>``.
 */

/**
 * @typedef {object} FeState
 * @property {object | null} currentDraft  Open acceptance draft, or null.
 * @property {string} currentUseContext    Declared use-context value.
 * @property {string} activeFaceMethod     Active face_embedding_method.
 * @property {Map<string, object>} installState  registry id → /api/v1/models install state.
 * @property {Set<string>} requiresAckIds   Ids needing click-through before use.
 * @property {Set<string>} acceptedIds      Ids the user has accepted.
 * @property {Set<string>} catalogEntryIds  Ids fetched via the catalog endpoint.
 * @property {Set<string>} busyRowIds       Ids mid-operation (download/uninstall).
 * @property {number | null} pendingScrollRestore  Parent scroll to restore post-render.
 */

/** @type {FeState} */
export const feState = {
  currentDraft: null,
  currentUseContext: "unspecified",
  activeFaceMethod: "sface",
  installState: new Map(),
  requiresAckIds: new Set(),
  acceptedIds: new Set(),
  catalogEntryIds: new Set(),
  busyRowIds: new Set(),
  pendingScrollRestore: null,
};

/**
 * Walk the picker groups and collect every entry id with
 * ``requires_explicit_ack=true``. Used by the ``setActiveFaceEmbedder``
 * pre-gate.
 *
 * @param {Array<{entries?: Array<object>}>} groups
 * @returns {Set<string>}
 */
export function _collectRequiresAckIds(groups) {
  const out = new Set();
  for (const g of groups || []) {
    for (const e of g.entries || []) {
      if (e && e.requires_explicit_ack && e.id) out.add(e.id);
    }
  }
  return out;
}

/**
 * Collect every entry id flagged ``is_catalog_entry`` by the backend.
 * Drives catalog-vs-legacy routing in the Download / Uninstall handlers
 * (which only receive a registry id, not the full entry).
 *
 * @param {Array<{entries?: Array<object>}>} groups
 * @returns {Set<string>}
 */
export function _collectCatalogEntryIds(groups) {
  const out = new Set();
  for (const g of groups || []) {
    for (const e of g.entries || []) {
      if (e && e.is_catalog_entry === true && e.id) out.add(e.id);
    }
  }
  return out;
}

/**
 * Build the accepted-ids set from the ``/acceptance/list`` payload.
 * Treats a missing or malformed payload as "no acceptances on file"
 * — the pre-gate then redirects the user to the dialog, which is
 * the safe failure mode.
 *
 * The log is append-only and oldest-first, so for each model the LATEST
 * row decides current status: an ``event === "revoke"`` withdrawal
 * supersedes an earlier acceptance (legacy rows have no ``event`` and
 * default to "accept").
 *
 * @param {{acceptances?: Array<{model_id?: string, event?: string}>} | null} payload
 * @returns {Set<string>}
 */
export function _collectAcceptedIds(payload) {
  /** @type {Map<string, string>} */
  const latestEvent = new Map();
  const rows = (payload && payload.acceptances) || [];
  for (const a of rows) {
    if (a && a.model_id) latestEvent.set(a.model_id, a.event || "accept");
  }
  const out = new Set();
  for (const [modelId, event] of latestEvent) {
    if (event === "accept") out.add(modelId);
  }
  return out;
}

/**
 * Test seam: clear the picker state so unit tests start from a known
 * empty baseline. Not used in production code.
 */
export function _resetFaceEmbedderStateForTests() {
  feState.requiresAckIds = new Set();
  feState.acceptedIds = new Set();
  feState.catalogEntryIds = new Set();
  feState.installState = new Map();
  feState.busyRowIds = new Set();
  feState.currentUseContext = "unspecified";
  feState.activeFaceMethod = "sface";
  feState.currentDraft = null;
  feState.pendingScrollRestore = null;
}

/**
 * Test seam: seed the picker state directly so unit tests can exercise
 * the picker without standing up the network sequence.
 *
 * @param {{requiresAckIds?: string[], acceptedIds?: string[],
 *          catalogEntryIds?: string[],
 *          installState?: Record<string, object>, activeFaceMethod?: string,
 *          useContext?: string}} state
 */
export function _seedFaceEmbedderStateForTests(state) {
  feState.requiresAckIds = new Set(state.requiresAckIds || []);
  feState.acceptedIds = new Set(state.acceptedIds || []);
  feState.catalogEntryIds = new Set(state.catalogEntryIds || []);
  if (state.installState) {
    feState.installState = new Map(Object.entries(state.installState));
  }
  if (state.activeFaceMethod) feState.activeFaceMethod = state.activeFaceMethod;
  if (state.useContext) feState.currentUseContext = state.useContext;
}

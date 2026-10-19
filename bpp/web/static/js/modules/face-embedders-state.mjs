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
 * @property {string | null} activeFaceMethod  Active face_embedding_method (null: it couldn't be loaded).
 * @property {Map<string, object>} installState  registry id → /api/v1/models install state.
 * @property {Set<string>} requiresAckIds   Ids needing click-through before use.
 * @property {Set<string>} acceptedIds      Ids accepted under the CURRENT license text.
 * @property {Set<string>} reacceptIds      Ids accepted under an OLDER text — need re-accepting.
 * @property {Set<string>} catalogEntryIds  Ids fetched via the catalog endpoint.
 * @property {Map<string, object>} entries  Registry entries by id, as last loaded.
 * @property {Set<string>} busyRowIds       Ids mid-operation (download/uninstall).
 * @property {Map<string, string>} busyText  Their in-row status ("Downloading… 40%").
 * @property {Map<string, string>} loadStates  Id → the server's load-gate decision
 *   (``ok`` / ``needs_license`` / ``needs_reaccept`` /
 *   ``needs_commercial_rights`` / ``blocked``).
 * @property {{install: string, license: string, method: string}} unknown
 *   What the last load couldn't fetch (install state, the acceptance list,
 *   the active face method) — why, or "" when it loaded: rows that need
 *   it read "Unknown".
 * @property {Map<string, boolean>} decided  Id → accepted (true) or withdrawn
 *   (false) on this page: known even when the acceptance list doesn't
 *   load; cleared once it does.
 */

import { esc } from "./text-format.mjs";

/** @type {FeState} */
export const feState = {
  currentDraft: null,
  currentUseContext: "unspecified",
  activeFaceMethod: "sface",
  installState: new Map(),
  requiresAckIds: new Set(),
  acceptedIds: new Set(),
  reacceptIds: new Set(),
  catalogEntryIds: new Set(),
  entries: new Map(),
  busyRowIds: new Set(),
  busyText: new Map(),
  loadStates: new Map(),
  /** What the last load couldn't fetch (why): rows needing it read "Unknown". */
  unknown: { install: "", license: "", method: "" },
  decided: new Map(),
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
      // (=== true: the row's own rule, so the dialog can't disagree with it.)
      if (e && e.requires_explicit_ack === true && e.id) out.add(e.id);
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
 * Latest row per model from the ``/acceptance/list`` payload. The log
 * is append-only and oldest-first, so the LAST row per model decides
 * current status.
 *
 * @param {{acceptances?: Array<{model_id?: string, event?: string, current?: boolean, needs_reaccept?: boolean}>} | null} payload
 * @returns {Map<string, {event: string, current: boolean, reaccept: boolean}>}
 */
function _latestRowByModel(payload) {
  /** @type {Map<string, {event: string, current: boolean, reaccept: boolean}>} */
  const latest = new Map();
  const rows = (payload && payload.acceptances) || [];
  for (const a of rows) {
    if (!a || !a.model_id) continue;
    // ``current`` is the server's version-aware verdict (acceptance of
    // the CURRENT license text). Absent (older server) → trust the
    // event, matching the previous behaviour.
    const stale = a.current === false || a.needs_reaccept === true;
    // ``needs_reaccept`` is the server's verdict that an OLDER text was
    // accepted. Not current but not stale-text either (e.g. a damaged log
    // line after it) is "no license", not "re-accept".
    const reaccept = a.needs_reaccept === undefined ? stale : a.needs_reaccept === true;
    latest.set(a.model_id, { event: a.event || "accept", current: !stale, reaccept });
  }
  return latest;
}

/**
 * Build the accepted-ids set from the ``/acceptance/list`` payload.
 * Treats a missing or malformed payload as "no acceptances on file"
 * — the pre-gate then redirects the user to the dialog, which is
 * the safe failure mode.
 *
 * Only a latest row that is an ``accept`` of the CURRENT license text
 * counts: a ``revoke`` withdrawal supersedes an earlier acceptance
 * (legacy rows have no ``event`` and default to "accept"), and an
 * acceptance of an older text (``current: false``) is NOT accepted —
 * the server's load gate refuses it, so showing it as accepted would
 * hide a feature that has silently stopped.
 *
 * @param {{acceptances?: Array<{model_id?: string, event?: string, current?: boolean, needs_reaccept?: boolean}>} | null} payload
 * @returns {Set<string>}
 */
export function _collectAcceptedIds(payload) {
  const out = new Set();
  for (const [modelId, row] of _latestRowByModel(payload)) {
    if (row.event === "accept" && row.current) out.add(modelId);
  }
  return out;
}

/**
 * Ids whose latest row is an acceptance of an OLDER license text —
 * the picker shows these as "Re-accept needed".
 *
 * @param {{acceptances?: Array<{model_id?: string, event?: string, current?: boolean, needs_reaccept?: boolean}>} | null} payload
 * @returns {Set<string>}
 */
export function _collectReacceptIds(payload) {
  const out = new Set();
  for (const [modelId, row] of _latestRowByModel(payload)) {
    if (row.event === "accept" && row.reaccept) out.add(modelId);
  }
  return out;
}

/**
 * A notice for Settings → Models when a damaged line in the license
 * record (e.g. after a power cut) made earlier acceptances read as
 * withdrawn — otherwise those models would just flip to "needs license"
 * with no reason given.
 *
 * @param {{withdrawn_by_damage?: unknown} | null} payload
 * @returns {string}
 */
export function damagedLogNoticeHTML(payload) {
  const ids = payload && Array.isArray(payload.withdrawn_by_damage) ? payload.withdrawn_by_damage : [];
  if (ids.length === 0) return "";
  const n = ids.length === 1 ? "1 model" : `${ids.length} models`;
  return (
    `<p class="setting-muted fe-damaged-log-notice" role="status">Part of your license record ` +
    `couldn't be read (for example after a power cut), so ${n} you accepted before it ` +
    `now need${ids.length === 1 ? "s" : ""} to be accepted again.</p>`
  );
}

/**
 * Say what the picker couldn't load (and why), with a Retry: the rows
 * that needed it read "Unknown" rather than a confident wrong status.
 * @param {Array<{what: string, why: string}>} failures
 * @returns {string} HTML ("" when nothing failed)
 */
export function loadFailureNoticeHTML(failures) {
  if (!failures.length) return "";
  const parts = failures.map((f) => `${esc(f.what)} (${esc(f.why)})`).join(", ");
  return (
    `<p class="setting-muted fe-load-failure-notice" role="status">Couldn't load ${parts} — ` +
    `models marked “Unknown” may be fine. ` +
    `<button class="modal-btn modal-btn-secondary" data-action="loadFaceEmbedderPicker">Retry</button></p>`
  );
}

/**
 * Test seam: clear the picker state so unit tests start from a known
 * empty baseline. Not used in production code.
 */
export function _resetFaceEmbedderStateForTests() {
  feState.requiresAckIds = new Set();
  feState.acceptedIds = new Set();
  feState.reacceptIds = new Set();
  feState.catalogEntryIds = new Set();
  feState.entries = new Map();
  feState.installState = new Map();
  feState.busyRowIds = new Set();
  feState.busyText = new Map();
  feState.loadStates = new Map();
  feState.currentUseContext = "unspecified";
  feState.activeFaceMethod = "sface";
  feState.currentDraft = null;
  feState.unknown = { install: "", license: "", method: "" };
  feState.decided = new Map();
}

/**
 * Test seam: seed the picker state directly so unit tests can exercise
 * the picker without standing up the network sequence.
 *
 * @param {{requiresAckIds?: string[], acceptedIds?: string[],
 *          reacceptIds?: string[], catalogEntryIds?: string[],
 *          entries?: Record<string, object>,
 *          installState?: Record<string, object>, activeFaceMethod?: string,
 *          useContext?: string}} state
 */
export function _seedFaceEmbedderStateForTests(state) {
  feState.requiresAckIds = new Set(state.requiresAckIds || []);
  feState.acceptedIds = new Set(state.acceptedIds || []);
  feState.reacceptIds = new Set(state.reacceptIds || []);
  feState.catalogEntryIds = new Set(state.catalogEntryIds || []);
  if (state.entries) {
    feState.entries = new Map(Object.entries(state.entries));
    // Mirror the picker's load: the load-gate map is filled from the
    // same entries, so seeded tests run the path production runs.
    for (const [id, e] of feState.entries) {
      if (e && e.load_state) feState.loadStates.set(id, e.load_state);
    }
  }
  if (state.installState) {
    feState.installState = new Map(Object.entries(state.installState));
  }
  if (state.activeFaceMethod) feState.activeFaceMethod = state.activeFaceMethod;
  if (state.useContext) feState.currentUseContext = state.useContext;
}

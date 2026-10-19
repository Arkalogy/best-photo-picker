// @ts-check
/**
 * A picker row's license facts, from the server's load-gate decision and
 * the acceptance list. Split from face-embedders-rowstate.mjs.
 */

import { feState } from "./face-embedders-state.mjs";

/**
 * A row's license facts — the row status and the "switch to this model"
 * check both decide from these, so they can't disagree.
 *
 * @param {string | undefined} id
 * @param {boolean} restricted  it needs an explicit acceptance
 * @param {string | undefined} loadState  the server's load-gate decision
 * @returns {{accepted: boolean, licenseBlocked: boolean, needsReaccept: boolean,
 *   needsCommercialRights: boolean, unavailable: boolean, licenseUnknown: boolean}}
 */
export function licenseFacts(id, restricted, loadState) {
  // (The server letting a restricted model load — or asking only for the
  // commercial-use rights — means it was accepted: known even when the
  // acceptance list didn't load.)
  const accepted =
    feState.acceptedIds.has(id) ||
    (restricted && (loadState === "ok" || loadState === "needs_commercial_rights"));
  // The server's load-gate decision, when it sends one, is the truth:
  // it also covers commercial use without the rights assertion and
  // withdrawn / legally blocked entries, which the acceptance list
  // can't see. Older servers: fall back to the acceptance list.
  const licenseBlocked = loadState ? loadState !== "ok" : restricted && !accepted;
  return {
    accepted,
    licenseBlocked,
    // Accepted once, but under an older license text — the load gate
    // refuses it, so it is NOT accepted; flag it so the row says
    // "re-accept" instead of a generic "needs license".
    needsReaccept: loadState
      ? loadState === "needs_reaccept"
      : licenseBlocked && feState.reacceptIds.has(id),
    // Accepted, but the use context is Commercial and the separate-rights
    // assertion wasn't made — the dialog (in Commercial mode) asks for it.
    needsCommercialRights: loadState === "needs_commercial_rights",
    // Withdrawn or legally blocked: accepting can't make it run.
    unavailable: loadState === "blocked",
    // The acceptance list didn't load, the server sent no load state, and
    // it wasn't accepted or withdrawn on this page: whether it may load —
    // and whether you accepted it — is unknown.
    licenseUnknown:
      !!feState.unknown.license && restricted && !loadState && !feState.decided.has(id ?? ""),
  };
}

/**
 * Why a row can't run or download, in the three places that say it
 * (one wording each): the menu's sub-line (``short``), and the
 * "Couldn't switch" toast's reason and fix.
 *
 * @param {{unavailable?: boolean, needsCommercialRights?: boolean, needsReaccept?: boolean}} state
 * @returns {{short: string, reason: string, fix: string}}
 */
export function licenseBlocker(state) {
  if (state.unavailable) {
    return {
      short: "No longer available",
      reason: "it has been withdrawn",
      fix: "pick another model",
    };
  }
  if (state.needsCommercialRights) {
    return {
      short: "Confirm commercial-use rights first",
      reason: "your commercial-use rights for it aren't confirmed",
      fix: "confirm them in its ⋯ menu",
    };
  }
  if (state.needsReaccept) {
    return {
      short: "Re-accept the license first",
      reason: "its license terms changed",
      fix: "re-accept them in its ⋯ menu",
    };
  }
  return {
    short: "Accept the license first",
    reason: "its license isn't accepted",
    fix: "accept it in its ⋯ menu",
  };
}

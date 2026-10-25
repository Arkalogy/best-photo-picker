// @ts-check
/**
 * Face-embedder picker action handlers: install-runtime hint, uninstall,
 * redownload, catalog ensure-weights, set-use-context, set-active.
 *
 * Split out of ``modals-face-embedders.mjs`` for the 500-LOC cap. Shares
 * picker state via ``feState`` and calls back into the picker
 * (``loadFaceEmbedderPicker`` / ``_setRowStatusBusy``) and the acceptance
 * dialog (``openFaceEmbedderAcceptance``). The import cycle with the
 * picker module is safe — all cross-referenced symbols are hoisted
 * function declarations invoked at runtime, not at module-init time.
 */

import { apiFetch } from "./api-client.mjs";
import {
  DOWNLOAD_STALLED_TEXT,
  downloadProgressText,
  downloadRetryText,
  waitForDownloadJob,
} from "./download-jobs.mjs";
import { appConfirm } from "./dialogs.mjs";
import { openFaceEmbedderAcceptance } from "./face-embedders-acceptance.mjs";
import { licenseBlocker, licenseFacts } from "./face-embedders-license.mjs";
import { feState, isCatalogEntry, loadStateOf, requiresAck } from "./face-embedders-state.mjs";
import {
  _clearRowBusy,
  _setRowStatusBusy,
  loadFaceEmbedderPicker,
} from "./modals-face-embedders.mjs";
import { notifyModelsChanged } from "./models-changed.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * A row's operation ended: unlock it. Done, it announced the change (the
 * "models changed" reload redraws it); failed, redraw its real status here.
 * @param {string} registryId
 * @param {boolean} succeeded
 */
function _endRowOperation(registryId, succeeded) {
  _clearRowBusy(registryId);
  if (!succeeded) void loadFaceEmbedderPicker();
}

/**
 * The refused switch, why (its load-gate decision), and what to do.
 * @param {string} displayName
 * @param {{unavailable: boolean, needsCommercialRights: boolean, needsReaccept: boolean}} row
 * @returns {string}
 */
function _blockedMessage(displayName, row) {
  const { reason, fix } = licenseBlocker(row);
  return `Couldn't switch to ${displayName}: ${reason} — ${fix}.`;
}

// ── Install controls — delegate to the legacy /api/v1/models endpoints
// ── until the per-entry install logic lands in the registry layer. ──

/**
 * @param {string} installHint  e.g. ``pip install bppicker[faces]``
 * @param {string} displayName
 */
export function installFaceEmbedderViaHint(installHint, displayName) {
  toast(
    `${displayName} needs an extra runtime package. Run \`${installHint}\` ` +
      `in your terminal, then reopen Settings → Models.`,
  );
}

/**
 * The /api/v1/models/uninstall and /redownload endpoints expect the
 * ModelRegistry file name (e.g. "SFace recognition", "CLIP visual"),
 * NOT the feature label (e.g. "Face recognition", "Smart search &
 * dedup"). The install state's `files` array carries those names —
 * one feature can map to multiple model files.
 *
 * @param {string} registryId
 * @param {string} legacyLabel
 */
export async function uninstallFaceEmbedderEntry(registryId, legacyLabel) {
  if (feState.busyRowIds.has(registryId)) {
    toast(
      "This model is busy with another operation. Wait for it to finish.",
    );
    return;
  }
  const install = feState.installState.get(registryId);
  const files = (install?.files || []).filter((f) => f && f.exists && f.name);
  // Catalog entries (LaMa, NudeNet, buffalo_s) have weights cached
  // locally but no manifest file list — they uninstall via the
  // catalog-specific endpoint. Detected by the backend's
  // is_catalog_entry flag, NOT by "no install record": LaMa / NudeNet
  // DO have a (fileless) legacy install row, so the old `!install`
  // check misfired and reported them as "not installed".
  const isCatalogPath = isCatalogEntry(registryId) || !install;
  if (files.length === 0 && !isCatalogPath) {
    toast(`${legacyLabel} is not installed.`, "error");
    return;
  }
  const ok = await appConfirm(
    `Uninstall ${legacyLabel}?`,
    "This model's saved files will be deleted. You can download them again later.",
    { okLabel: "Uninstall", okClass: "danger" },
  );
  if (!ok) return;
  // In-row progress (not just a toast) so a multi-file delete shows
  // status, matching redownload/ensure-weights. Unlocked when it ends
  // (_endRowOperation) — a reload meanwhile keeps the row locked.
  _setRowStatusBusy(registryId, "Uninstalling…");
  let succeeded = false;
  try {
    if (isCatalogPath) {
      await apiFetch("/api/v1/face-embedders/uninstall-weights", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ registry_id: registryId }),
      });
    } else {
      for (const f of files) {
        await apiFetch("/api/v1/models/uninstall", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: f.name }),
        });
      }
    }
    toast(`${legacyLabel} uninstalled.`);
    notifyModelsChanged();
    succeeded = true;
  } catch (err) {
    toastError("uninstall the model", err);
  } finally {
    _endRowOperation(registryId, succeeded);
  }
}

/**
 * @param {string} registryId
 * @param {string} legacyLabel
 */
export async function redownloadFaceEmbedderEntry(registryId, legacyLabel) {
  if (feState.busyRowIds.has(registryId)) {
    toast(
      "This model is busy with another operation. Wait for it to finish.",
    );
    return;
  }
  const install = feState.installState.get(registryId);
  const files = (install?.files || []).filter((f) => f && f.name);
  if (files.length === 0) {
    toast(`${legacyLabel} has no downloadable files.`, "error");
    return;
  }
  // Each file downloads as a background job; the row shows live progress
  // (not just a fire-and-forget toast) while we poll it.
  const total = files.length;
  _setRowStatusBusy(
    registryId,
    total > 1 ? `Downloading… (0/${total})` : "Downloading…",
  );
  toast(`Downloading ${legacyLabel}…`);
  let succeeded = false;
  try {
    let done = 0;
    for (const f of files) {
      const prefix = total > 1 ? `(${done + 1}/${total}) ` : "";
      const started = await apiFetch("/api/v1/models/redownload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: f.name }),
      });
      await waitForDownloadJob(started?.job, {
        onProgress: (snap) =>
          _setRowStatusBusy(registryId, prefix + downloadProgressText(snap)),
        onStall: () => _setRowStatusBusy(registryId, prefix + DOWNLOAD_STALLED_TEXT),
        onRetry: (err) => _setRowStatusBusy(registryId, prefix + downloadRetryText(err)),
      });
      done += 1;
    }
    toast(`${legacyLabel} downloaded.`);
    notifyModelsChanged();
    succeeded = true;
  } catch (err) {
    // Surface the real reason (the backend now returns it); the redraw
    // below restores the true status.
    toastError("download the model", err);
  } finally {
    _endRowOperation(registryId, succeeded);
  }
}

/**
 * Explicit Download step for catalog entries (no install wiring in
 * the legacy ModelRegistry — currently only buffalo_s). Bound to the
 * picker's "Download (~SIZE)" menu item. The row shows live progress
 * while the backend's ``ensure-weights`` job runs. Replaces the silent
 * fetch-on-first-use behaviour that violated the "Nothing should be
 * silent" rule.
 *
 * @param {string} registryId
 * @param {string} displayName
 */
export async function ensureCatalogWeights(registryId, displayName) {
  if (feState.busyRowIds.has(registryId)) {
    toast(
      "This model is busy with another operation. Wait for it to finish.",
    );
    return;
  }
  _setRowStatusBusy(registryId, "Downloading…");
  toast(`Downloading ${displayName}…`);
  let succeeded = false;
  try {
    const started = await apiFetch("/api/v1/face-embedders/ensure-weights", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ registry_id: registryId }),
    });
    const snap = await waitForDownloadJob(started?.job, {
      onProgress: (s) => _setRowStatusBusy(registryId, downloadProgressText(s)),
      onStall: () => _setRowStatusBusy(registryId, DOWNLOAD_STALLED_TEXT),
      onRetry: (err) => _setRowStatusBusy(registryId, downloadRetryText(err)),
    });
    const sizeBytes = (snap.result && snap.result.size_bytes) || 0;
    const sizeMb = sizeBytes / (1024 * 1024);
    toast(
      sizeMb > 0
        ? `${displayName} downloaded (${sizeMb.toFixed(1)} MB).`
        : `${displayName} downloaded.`,
    );
    notifyModelsChanged();
    succeeded = true;
  } catch (err) {
    toastError("download the model", err);
  } finally {
    _endRowOperation(registryId, succeeded);
  }
}

/**
 * Persist the user-context declaration. Called from the
 * <select> onchange in the picker header.
 *
 * @param {string} value
 */
export async function setFaceEmbedderUseContext(value) {
  if (!value) return;
  try {
    await apiFetch("/api/v1/model-registry/use-context", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ use_context: value }),
    });
    feState.currentUseContext = value;
    // No toast on change — the dropdown is the affordance; the
    // selected option is its own confirmation. A toast on every
    // re-pick is noisy. Errors still toast.
    // Commercial mode changes which models the load gate allows: re-render
    // the picker and refresh the content-filter hint right away.
    notifyModelsChanged();
  } catch (err) {
    toastError("set the use context", err);
  }
}

/**
 * Make a face embedder the active one for analyze runs. Writes
 * face_embedding_method via the library settings PUT endpoint;
 * the existing method-change-wipes-embeddings path takes care of
 * invalidating the old vectors on next analyze.
 *
 * @param {string} methodValue   "sface" | "dlib" | "buffalo_s"
 * @param {string} displayName
 * @param {string} registryId
 */
export async function setActiveFaceEmbedder(
  methodValue,
  displayName,
  registryId,
) {
  // Restricted models must be accepted first; if the user hasn't,
  // the runtime gate raises ModelLoadBlockedError on first
  // analyze. Direct them to the dialog instead of a confusing
  // analyze-time error.
  //
  // The pre-gate uses two sets populated by ``loadFaceEmbedderPicker``
  // (the entries map and ``feState.acceptedIds``) rather than fetching
  // entry metadata on demand. The previous design called a stub
  // that always returned undefined and treated a Promise as a boolean,
  // so the gate never fired — runtime enforcement on the backend
  // caught it, but the user saw a confusing analyze-time error
  // instead of a clean "review terms first" toast.
  // The server's load-gate decision, when it sent one, is the truth;
  // otherwise the acceptance list — and if that didn't load, whether it's
  // accepted is unknown (the settings save has no license gate of its
  // own, so don't switch on a guess). From the same license facts as the
  // row's own status, so the menu and this check can't disagree.
  const row = registryId
    ? licenseFacts(registryId, requiresAck(registryId), loadStateOf(registryId))
    : null;
  if (row?.licenseUnknown || feState.unknown.method) {
    // (Which model is in use didn't load either: don't overwrite a setting
    // this page never read.)
    const [what, why] = row?.licenseUnknown
      ? ["whether its license is accepted", feState.unknown.license]
      : ["which face model is in use", feState.unknown.method];
    toast(
      `Couldn't switch to ${displayName}: the page couldn't load ${what} (${why}) — load the list again, then try again.`,
      "warning",
    );
    return;
  }
  if (row?.licenseBlocked) {
    toast(_blockedMessage(displayName, row), "error");
    return;
  }
  try {
    await apiFetch("/api/v1/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ face_embedding_method: methodValue }),
    });
    feState.activeFaceMethod = methodValue;
    toast(`${displayName} is now the active face embedder.`);
    void loadFaceEmbedderPicker();
  } catch (err) {
    toastError("set the active embedder", err);
  }
}

/**
 * Turn a model's run switch (its ``model_*`` toggle) on or off. The
 * analysis honours the switch; the picker shows an "Off" row for a model
 * switched off. Dispatched from the row menu, so ``enabled`` arrives as the
 * string "true" / "false".
 *
 * @param {string} toggleKey  e.g. ``model_pets``
 * @param {string | boolean} enabled
 * @param {string} displayName
 */
export async function setModelRunToggle(toggleKey, enabled, displayName) {
  const on = enabled === true || enabled === "true";
  try {
    await apiFetch("/api/v1/models/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key: toggleKey, enabled: on }),
    });
    toast(
      on
        ? `${displayName} will run in the next analysis.`
        : `${displayName} is skipped from now on; re-analyze to drop its results.`,
    );
    notifyModelsChanged();
  } catch (err) {
    toastError(on ? `turn ${displayName} on` : `turn ${displayName} off`, err);
  }
}

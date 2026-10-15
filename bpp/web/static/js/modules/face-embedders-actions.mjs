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
import { downloadProgressText, waitForDownloadJob } from "./download-jobs.mjs";
import { appConfirm } from "./dialogs.mjs";
import { openFaceEmbedderAcceptance } from "./face-embedders-acceptance.mjs";
import { feState } from "./face-embedders-state.mjs";
import {
  _setRowStatusBusy,
  loadFaceEmbedderPicker,
} from "./modals-face-embedders.mjs";
import { notifyModelsChanged } from "./models-changed.mjs";
import { toast, toastError } from "./toast.mjs";

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
  const isCatalogPath = feState.catalogEntryIds.has(registryId) || !install;
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
  // status, matching redownload/ensure-weights. The reload at the end
  // — on success AND in the catch — clears the busy state.
  _setRowStatusBusy(registryId, "Uninstalling…");
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
  } catch (err) {
    toastError("uninstall the model", err);
    void loadFaceEmbedderPicker();
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
      });
      done += 1;
    }
    toast(`${legacyLabel} downloaded.`);
    notifyModelsChanged();
  } catch (err) {
    // Surface the real reason (the backend now returns it) and restore
    // the true status by reloading the picker.
    toastError("download the model", err);
    void loadFaceEmbedderPicker();
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
  try {
    const started = await apiFetch("/api/v1/face-embedders/ensure-weights", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ registry_id: registryId }),
    });
    const snap = await waitForDownloadJob(started?.job, {
      onProgress: (s) => _setRowStatusBusy(registryId, downloadProgressText(s)),
    });
    const sizeBytes = (snap.result && snap.result.size_bytes) || 0;
    const sizeMb = sizeBytes / (1024 * 1024);
    toast(
      sizeMb > 0
        ? `${displayName} downloaded (${sizeMb.toFixed(1)} MB).`
        : `${displayName} downloaded.`,
    );
    notifyModelsChanged();
  } catch (err) {
    toastError("download the model", err);
    void loadFaceEmbedderPicker();
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
  // (``feState.requiresAckIds`` and ``feState.acceptedIds``) rather than fetching
  // entry metadata on demand. The previous design called a stub
  // that always returned undefined and treated a Promise as a boolean,
  // so the gate never fired — runtime enforcement on the backend
  // caught it, but the user saw a confusing analyze-time error
  // instead of a clean "review terms first" toast.
  if (
    registryId &&
    feState.requiresAckIds.has(registryId) &&
    !feState.acceptedIds.has(registryId)
  ) {
    toast(
      `${displayName} needs acceptance first — click "Review & accept…".`,
      "error",
    );
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

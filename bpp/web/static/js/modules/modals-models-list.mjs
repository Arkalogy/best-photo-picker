// @ts-check
/**
 * Settings → Models tab renderer. Walks /api/v1/models and builds a
 * row per feature with status dot, size, tip, toggle, redownload /
 * install / uninstall buttons, plus a "Download all missing" trailer.
 *
 * Extracted from modals-models.mjs during the v0.1 cleanup. Re-exported
 * from modals-models.mjs.
 */

import { apiFetch } from "./api-client.mjs";
import { _formatBytes } from "./format-helpers.mjs";
import { loadFaceEmbedderPicker } from "./modals-face-embedders.mjs";
import { toast, toastError } from "./toast.mjs";

export async function loadModelsList() {
  // Kick off the registry-driven face-embedder picker in parallel —
  // it lives in its own DOM container under the same Settings tab and
  // doesn't depend on /api/v1/models. Errors are swallowed inside the
  // module so a registry-API failure doesn't break the legacy panel.
  void loadFaceEmbedderPicker();
  const container = document.getElementById("models-list");
  if (!container) return;
  try {
    const features = await apiFetch("/api/v1/models");
    container.innerHTML = "";
    for (const f of features) {
      const row = document.createElement("div");
      row.className = "model-row";
      const isNoLib = f.status === "no_library";
      const isFallback = f.status === "fallback";
      const dot =
        f.status === "ready"
          ? "model-ok"
          : isFallback
            ? "model-partial"
            : isNoLib
              ? "model-partial"
              : f.status === "partial"
                ? "model-partial"
                : "model-missing";
      const statusLabel =
        f.status === "ready"
          ? "Ready"
          : isFallback
            ? "Fallback (degraded)"
            : isNoLib
              ? "Needs library"
              : f.status === "partial"
                ? "Incomplete"
                : "Not downloaded";
      const size = f.size_bytes > 0 ? _formatBytes(f.size_bytes) : "";
      const badge = f.bundled ? ' <span class="model-badge">bundled</span>' : "";
      const tipLines = [f.description];
      if (Array.isArray(f.files) && f.files.length > 0) {
        tipLines.push("");
        for (const fi of f.files) {
          const sizeStr = fi.size_bytes > 0 ? _formatBytes(fi.size_bytes) : "missing";
          tipLines.push(`• ${fi.name} — ${sizeStr}`);
        }
      }
      if (f.install_hint) tipLines.push("Install: " + f.install_hint);
      const tipText = tipLines.join("\n").replace(/"/g, "&quot;");
      const tip =
        '<span class="setting-info model-info" role="button" tabindex="0" title="' +
        tipText +
        '">?</span>';
      const isLibOnly = f.lib_only;
      const safeLabel = f.label.replace(/'/g, "\\'");
      const safeDesc = f.description.replace(/'/g, "\\'");
      const ready = f.status === "ready";
      const canPipInstall = !ready && f.install_key && (isNoLib || isLibOnly);
      const canDownload = (!ready || isFallback) && !isLibOnly && !isNoLib;
      const hasAnyOnDisk = f.files.some((/** @type {any} */ x) => x.exists);
      const canUninstall = hasAnyOnDisk && !isLibOnly && !isNoLib;
      let btnInner;
      if (canPipInstall) {
        btnInner =
          '<button class="model-redownload model-install-btn" title="Install ' +
          safeLabel +
          '" data-action="installPackage" data-arg0="' +
          f.install_key +
          '" data-arg1="' +
          safeLabel +
          '">Install</button>';
      } else {
        const filesJson = JSON.stringify(f.files).replace(/"/g, "&quot;");
        const btnTitle = ready ? "Already up to date" : "Download";
        const btnDisabled = !canDownload ? " disabled" : "";
        btnInner =
          '<button class="model-redownload" title="' +
          btnTitle +
          '" data-action="redownloadFeature" data-files="' +
          filesJson +
          '" data-arg0="' +
          safeLabel +
          '" data-arg1="' +
          safeDesc +
          '"' +
          btnDisabled +
          ">&#x21bb;</button>";
      }
      if (canUninstall) {
        const filesJson = JSON.stringify(f.files).replace(/"/g, "&quot;");
        btnInner +=
          '<button class="model-redownload model-uninstall" title="Delete from disk"' +
          ' data-action="uninstallFeature" data-files="' +
          filesJson +
          '" data-arg0="' +
          safeLabel +
          '">&#x2716;</button>';
      }
      const btnHtml = '<span class="model-actions">' + btnInner + "</span>";
      const sizeText = isNoLib
        ? f.install_key
          ? "Not installed"
          : f.install_hint || "needs library"
        : isLibOnly && !ready
          ? f.install_key
            ? "Not installed"
            : f.install_hint || "not installed"
          : size || statusLabel.toLowerCase();

      let toggleHtml = "";
      if (f.toggle_key) {
        const checked = f.enabled ? " checked" : "";
        const speedCls =
          f.speed_impact === "high"
            ? "model-impact-high"
            : f.speed_impact === "medium"
              ? "model-impact-med"
              : "model-impact-low";
        const speedLabel =
          f.speed_impact === "high"
            ? "Slow"
            : f.speed_impact === "medium"
              ? "Moderate"
              : "Fast";
        toggleHtml =
          '<label class="model-toggle" title="Enable/disable this model during analysis">' +
          '<input type="checkbox" data-toggle-key="' +
          f.toggle_key +
          '"' +
          checked +
          ' data-onchange="toggleModel">' +
          "</label>" +
          '<span class="model-speed-badge ' +
          speedCls +
          '" title="Speed impact: ' +
          speedLabel +
          '">' +
          speedLabel +
          "</span>";
      }

      row.innerHTML =
        (toggleHtml ||
          '<span class="model-toggle-spacer"></span><span class="model-speed-spacer"></span>') +
        '<span class="model-status ' +
        dot +
        '" title="' +
        statusLabel +
        '"></span>' +
        '<span class="model-name">' +
        f.label +
        badge +
        " " +
        tip +
        "</span>" +
        '<span class="model-spacer"></span>' +
        '<span class="model-size">' +
        sizeText +
        "</span>" +
        btnHtml;

      if (f.toggle_key && f.quality_impact) {
        const infoRow = document.createElement("div");
        infoRow.className = "model-quality-info";
        infoRow.textContent = f.quality_impact;
        row.appendChild(infoRow);
      }

      container.appendChild(row);
    }
    const missingDownloads = features.filter(
      (/** @type {any} */ f) =>
        f.status !== "ready" &&
        !f.lib_only &&
        f.status !== "no_library" &&
        f.files.some((/** @type {any} */ x) => !x.exists)
    );
    if (missingDownloads.length > 0) {
      const allBtn = document.createElement("button");
      allBtn.className = "model-download-all";
      allBtn.textContent = "Download all missing models";
      allBtn.onclick = async () => {
        allBtn.disabled = true;
        allBtn.textContent = "Downloading…";
        try {
          for (const f of missingDownloads) {
            const names = f.files
              .filter((/** @type {any} */ x) => !x.exists)
              .map((/** @type {any} */ x) => x.name);
            for (const name of names) {
              await apiFetch("/api/v1/models/redownload", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name }),
                signal: AbortSignal.timeout(180000),
              });
            }
          }
          toast("All missing models downloaded");
          loadModelsList();
        } catch (e) {
          toastError("download the model", e);
          allBtn.disabled = false;
          allBtn.textContent = "Download all missing models";
        }
      };
      container.appendChild(allBtn);
    }
  } catch {
    container.innerHTML = '<p class="setting-muted">Could not load model info.</p>';
  }
}

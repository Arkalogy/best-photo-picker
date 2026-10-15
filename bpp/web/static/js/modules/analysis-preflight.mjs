// @ts-check
/**
 * Analyze pre-flight: the ML-model download/consent gate shown before
 * an analyze run, plus the "open Models settings" action its banner
 * links to. Split out of analysis.mjs for the 500-LOC cap.
 */

import { registerAction } from "./action-registry.mjs";
import { apiFetch } from "./api-client.mjs";
import { attributionOkLabel, attributionSectionHTML } from "./attribution-consent.mjs";
import { notifyModelsChanged } from "./models-changed.mjs";
import { esc } from "./text-format.mjs";
import { toastError } from "./toast.mjs";

// Friendly capability label for blocked entries — the registry's
// display_name is technical ("Ultralytics YOLOv11n (pet detection,
// AGPL-3.0)") whereas the user just needs to know which feature is
// missing. Map the kind to the user-facing capability name.
const _KIND_LABEL = {
  face_embedder: "Face recognition",
  face_detector: "Face detection",
  face_landmarker: "Expression scoring",
  hand_landmarker: "Hand filter",
  pose_estimator: "Pose estimation",
  segmenter: "Subject segmentation",
  semantic_search: "Semantic search",
  pet_detector: "Pet detection",
  nudity_classifier: "Content filter",
  inpainter: "AI object removal",
};

/**
 * Pure: the pre-flight "Optional features available" label for one
 * blocked entry from ``/api/v1/models/pending``.
 *
 * - A non-default, research-only extra (e.g. SCRFD) reads as an
 *   optional extra, so it never implies the core capability is missing.
 * - A stale acceptance (``reason: "needs_reaccept"``) says so — the
 *   feature was on and has stopped until the user re-accepts.
 *
 * @param {{kind?: string, name?: string, reason?: string, from_plugin?: boolean,
 *          research_only?: boolean, default_for_kind?: boolean}} b
 * @returns {string}
 */
export function blockedItemLabel(b) {
  const cap = _KIND_LABEL[b.kind || ""] || b.name || "Optional model";
  // A plugin model is named as itself — its kind may match a built-in
  // capability (e.g. face_detector) that is working fine.
  let label = b.from_plugin
    ? `Plugin model: ${b.name}`
    : b.research_only && !b.default_for_kind
      ? `Extra ${cap.toLowerCase()} (optional, research-only)`
      : cap;
  if (b.reason === "needs_reaccept") label += " — license terms updated, re-accept to turn back on";
  return label;
}

/**
 * Size shown for one pending download. A plugin model's size comes from
 * its registry entry's ``expected_download_size_bytes``; 0 = not declared.
 * @param {number} mb
 * @returns {string}
 */
export function downloadSizeLabel(mb) {
  if (!mb) return "size unknown";
  return mb >= 1 ? `${Math.round(mb)} MB` : `${(mb * 1024).toFixed(0)} KB`;
}

/**
 * Total shown in the download prompt. A plugin model whose registry entry
 * declares no size counts as unknown — say so instead of understating.
 * @param {number} totalMb
 * @param {boolean} anyUnknown
 * @returns {string}
 */
export function downloadTotalLabel(totalMb, anyUnknown) {
  if (!totalMb) return anyUnknown ? "size unknown" : "0 MB";
  const known = totalMb >= 100 ? `${Math.round(totalMb)} MB` : `${totalMb.toFixed(1)} MB`;
  return anyUnknown ? `${known} + plugin models of unknown size` : known;
}

/**
 * Show the ML-model consent / download gate before analyze. Returns
 * true if the user consented (or there was nothing to consent to) and
 * analyze should proceed; false if they cancelled.
 * @returns {Promise<boolean>}
 */
export async function analyzePreflightConsent() {
  /** @type {any} */
  const win = window;
  // ML download consent.
  //
  // ML models (face detection / recognition, scoring, optional CLIP +
  // pets) are pulled from public hosts on first use and cached at
  // ~/.cache/bpp. Inference runs on-device, but we shouldn't silently
  // make network calls to GitHub / google / huggingface on someone's
  // first analyze click. Ask explicitly, and show exactly which models
  // and which hosts so the user can give informed consent.
  //
  // The consent gate fires whenever the pending list is non-empty —
  // adding a new model later (or the user enabling CLIP) triggers a
  // fresh consent for the new entries instead of relying on a stale
  // "I already agreed once" flag. No persistent flag needed.
  try {
    // The attribution bundle is the combined license step for the
    // built-in Apache / BSD-style models; a failure to load it just
    // drops that section (the models stay off, as before).
    const [data, bundle] = await Promise.all([
      apiFetch("/api/v1/models/pending"),
      apiFetch("/api/v1/model-registry/acceptance/attribution-bundle").catch((e) => {
        console.warn("Failed to load the attribution bundle:", e);
        return null;
      }),
    ]);
    const pending = (data && data.models) || [];
    const blocked = (data && data.blocked) || [];
    // Downloads that happen only if the user accepts the bundle below.
    const attributionDownloads = (data && data.attribution) || [];
    const bundleModels = (bundle && bundle.models) || [];
    if (pending.length > 0 || blocked.length > 0 || bundleModels.length > 0) {
      const totalMb = (data.total_mb || 0) + (data.attribution_mb || 0);
      const totalLabel = downloadTotalLabel(
        totalMb,
        [...pending, ...attributionDownloads].some((m) => !m.size_mb),
      );
      const row = (m, suffix) => {
        const sizeLabel = downloadSizeLabel(m.size_mb);
        return `<li>
            <span class="ml-name">${esc(m.name)}</span>
            <span class="ml-meta">${esc(sizeLabel)} · ${esc(m.host)}${suffix}</span>
          </li>`;
      };
      const rows =
        pending.map((m) => row(m, "")).join("") +
        attributionDownloads.map((m) => row(m, " · only if you accept its license below")).join("");
      const allDownloads = pending.length + attributionDownloads.length;
      // Kinds analyze actually exercises — used to split blocked
      // entries into "this affects what analysis can do" vs
      // "irrelevant to analysis." Inpainting (LaMa) is the user's
      // manual editor flow, NOT analyze, so it shouldn't appear in
      // the analyze pre-flight at all.
      const _ANALYZE_KINDS = new Set([
        "face_embedder",
        "face_detector",
        "semantic_search",
        "pet_detector",
        "nudity_classifier",
      ]);
      // Plugin models are shown whatever their kind — they're held back
      // by their license just like the built-ins, and must say so.
      const analyzeBlocked = blocked.filter((b) => _ANALYZE_KINDS.has(b.kind) || b.from_plugin);

      // Banner framing: these are OPTIONAL features the user can
      // enable, not blockers. "Needs your review before use" read
      // contradictorily with the next line saying "analysis will run
      // without it" — same sentence said the opposite things.
      // "Optional features available" matches reality: analyze runs
      // either way; the user CAN turn these on by reviewing their
      // licenses.
      const blockedBanner = analyzeBlocked.length
        ? `<div class="ml-consent-blocked">
            <p class="ml-consent-blocked-title">Optional features available</p>
            <ul class="ml-consent-blocked-list">${analyzeBlocked
              .map((b) => `<li>${esc(blockedItemLabel(b))}</li>`)
              .join("")}</ul>
            <p class="ml-consent-blocked-hint">${analyzeBlocked.some((b) => b.reason === "needs_reaccept") ? "Some license terms were updated since you accepted them — those features are off until you re-accept. " : ""}To enable, review the license in <a href="#" class="ml-consent-blocked-link" data-action="openModelsSettings">Settings → Models</a>. Analysis will run without ${analyzeBlocked.length === 1 ? "it" : "them"} for now.</p>
          </div>`
        : "";
      // Intros — drop "Required" because none of these models are
      // strictly required (analyze runs with whatever is on disk +
      // accepted, falling back when something is missing). The
      // older "Required ML models will download now" implied
      // mandatory + contradicted the cancel button.
      let downloadBody;
      if (allDownloads) {
        downloadBody = `<p class="ml-consent-intro">These models will download to your machine (${esc(totalLabel)}). Cached locally, no photos leave your machine.</p>
            <ul class="ml-consent-list">${rows}</ul>`;
      } else if (analyzeBlocked.length) {
        // Analyze runs given current state — describe that
        // honestly, the banner below offers the optional features.
        downloadBody = `<p class="ml-consent-intro">Analysis runs on your device — no photos leave your machine.</p>`;
      } else {
        downloadBody = `<p class="ml-consent-intro">Ready to analyze. Runs on your device — no photos leave your machine.</p>`;
      }
      const attributionHTML = bundleModels.length ? attributionSectionHTML(bundle) : "";
      const bodyHTML = downloadBody + attributionHTML + blockedBanner;
      const title = allDownloads ? "Download ML models?" : "Start analysis?";
      const hasAttrDownloads = attributionDownloads.length > 0;
      let okLabel = pending.length ? "Download & analyze" : "Analyze";
      if (bundleModels.length) okLabel = attributionOkLabel(false, hasAttrDownloads);
      // appConfirm renders synchronously, so the checkbox exists now.
      const answer = win.appConfirm?.(title, null, { okLabel, bodyHTML });
      let accepted = false;
      const box = /** @type {HTMLInputElement | null} */ (document.getElementById("ml-attr-ack"));
      box?.addEventListener("change", () => {
        accepted = box.checked;
        const okBtn = document.getElementById("confirm-ok");
        if (okBtn) okBtn.textContent = attributionOkLabel(accepted, hasAttrDownloads);
      });
      const ok = await answer;
      if (!ok) return false;
      if (accepted) {
        try {
          await apiFetch("/api/v1/model-registry/acceptance/attribution-bundle/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              model_ids: bundleModels.map((m) => m.id),
              checkbox_responses: { [box?.dataset.checkboxId || ""]: true },
            }),
          });
          notifyModelsChanged();
        } catch (e) {
          toastError("accept the model licenses", e);
          return false;
        }
      }
    }
  } catch (e) {
    // If the manifest endpoint is unreachable, fall back to the old
    // generic prompt so the user is still asked before downloads
    // happen — degraded UX, not silent network calls. Surface the
    // failure reason in a faint footer so a maintainer testing a
    // misconfigured build (and bug-report screenshots from users)
    // carry the breadcrumb instead of hiding it in browser DevTools.
    console.warn("Failed to load model manifest:", e);
    const errMsg = /** @type {any} */ (e)?.message || String(e);
    const bodyHTML =
      `<p>Best Photo Picker will download ML models for face detection, scoring, ` +
      `and (optional) pets / CLIP search the first time it needs them. ` +
      `Cached at ~/.cache/bpp. Runs on-device — no data leaves your machine.</p>` +
      `<p class="confirm-sub ml-consent-foot ml-consent-degraded">` +
      `Couldn't reach the model registry — using the generic prompt. ` +
      `(${esc(errMsg)})</p>`;
    const ok = await win.appConfirm?.(
      "Download ML models?",
      null,
      { okLabel: "Continue", cancelLabel: "Cancel", bodyHTML },
    );
    if (!ok) return false;
  }
  return true;
}

/**
 * Open Settings → Models from the analyze pre-flight banner. Resolves
 * the confirm dialog as cancelled so analyze doesn't start, then
 * switches to the Models tab in the Settings modal. The user can
 * accept the license there and re-trigger analyze when ready.
 */
function openModelsSettings() {
  // Resolve the pre-flight confirm as cancelled — analyze MUST NOT
  // proceed while the user navigates to fix the licensing gap.
  /** @type {any} */ (window).resolveConfirm?.(false);
  /** @type {any} */ (window).showSettings?.();
  /** @type {any} */ (window).switchSettingsTab?.("models");
}
registerAction("openModelsSettings", openModelsSettings);

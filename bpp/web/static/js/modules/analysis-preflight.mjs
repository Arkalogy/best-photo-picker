// @ts-check
/**
 * Analyze pre-flight: the ML-model download/consent gate shown before
 * an analyze run, plus the "open Models settings" action its banner
 * links to. Split out of analysis.mjs for the 500-LOC cap.
 */

import { registerAction } from "./action-registry.mjs";
import { apiFetch } from "./api-client.mjs";
import { esc } from "./text-format.mjs";

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
    const data = await apiFetch("/api/v1/models/pending");
    const pending = (data && data.models) || [];
    const blocked = (data && data.blocked) || [];
    if (pending.length > 0 || blocked.length > 0) {
      const totalMb = data.total_mb || 0;
      const totalLabel =
        totalMb >= 100 ? `${Math.round(totalMb)} MB` : `${totalMb.toFixed(1)} MB`;
      const rows = pending
        .map((m) => {
          const sizeLabel =
            m.size_mb >= 1
              ? `${Math.round(m.size_mb)} MB`
              : `${(m.size_mb * 1024).toFixed(0)} KB`;
          return `<li>
            <span class="ml-name">${esc(m.name)}</span>
            <span class="ml-meta">${esc(sizeLabel)} · ${esc(m.host)}</span>
          </li>`;
        })
        .join("");
      // Friendly capability label for blocked entries — the registry's
      // display_name is technical ("Ultralytics YOLOv11n (pet detection,
      // AGPL-3.0)") whereas the user just needs to know which feature
      // is missing. Map the kind to the user-facing capability name.
      const _KIND_LABEL = {
        face_embedder: "Face recognition",
        face_detector: "Face detection",
        semantic_search: "Semantic search",
        pet_detector: "Pet detection",
        nudity_classifier: "Content filter",
        inpainter: "AI object removal",
      };
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
      const analyzeBlocked = blocked.filter((b) => _ANALYZE_KINDS.has(b.kind));

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
              .map((b) => {
                const cap = _KIND_LABEL[b.kind] || b.name;
                return `<li>${esc(cap)}</li>`;
              })
              .join("")}</ul>
            <p class="ml-consent-blocked-hint">To enable, review the license in <a href="#" class="ml-consent-blocked-link" data-action="openModelsSettings">Settings → Models</a>. Analysis will run without ${analyzeBlocked.length === 1 ? "it" : "them"} for now.</p>
          </div>`
        : "";
      // Intros — drop "Required" because none of these models are
      // strictly required (analyze runs with whatever is on disk +
      // accepted, falling back when something is missing). The
      // older "Required ML models will download now" implied
      // mandatory + contradicted the cancel button.
      let downloadBody;
      if (pending.length) {
        downloadBody = `<p class="ml-consent-intro">These models will download to your machine (${esc(totalLabel)}). Cached locally, no photos leave your machine.</p>
            <ul class="ml-consent-list">${rows}</ul>`;
      } else if (analyzeBlocked.length) {
        // Analyze runs given current state — describe that
        // honestly, the banner below offers the optional features.
        downloadBody = `<p class="ml-consent-intro">Analysis runs on your device — no photos leave your machine.</p>`;
      } else {
        downloadBody = `<p class="ml-consent-intro">Ready to analyze. Runs on your device — no photos leave your machine.</p>`;
      }
      const bodyHTML = downloadBody + blockedBanner;
      const title = pending.length ? "Download ML models?" : "Start analysis?";
      const okLabel = pending.length ? "Download & analyze" : "Analyze";
      const ok = await win.appConfirm?.(title, null, { okLabel, bodyHTML });
      if (!ok) return false;
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

// @ts-check
/**
 * The pre-analysis dialog's combined license step for attribution-
 * licensed models (SFace, YuNet, the MediaPipe models, the Haar
 * cascade). One unticked checkbox covers all of them; ticking it
 * records one acceptance per model on the server
 * (POST /api/v1/model-registry/acceptance/attribution-bundle/confirm).
 * Restricted models are never listed here — they keep their own
 * dialog in Settings → Models.
 *
 * Pure render helpers only; analysis-preflight.mjs owns the request.
 */

import { esc, escapeAttr } from "./text-format.mjs";

/**
 * @typedef {{id: string, display_name: string, kind: string,
 *            license: string, terms_url: string}} BundleModel
 * @typedef {{models: BundleModel[], compressed_disclaimer: string,
 *            biometric_responsibility_text: string,
 *            required_checkboxes: {id: string, text: string}[]}} AttributionBundle
 */

/**
 * The dialog section listing each model, its license and terms link,
 * the attribution notice, and the single acknowledgment checkbox.
 * @param {AttributionBundle} bundle
 * @returns {string}
 */
export function attributionSectionHTML(bundle) {
  const rows = bundle.models
    .map(
      (m) => `<li>
        <span class="ml-name">${esc(m.display_name)}</span>
        <span class="ml-meta">${esc(m.license)} · <a href="${escapeAttr(m.terms_url)}" target="_blank" rel="noopener noreferrer">terms</a></span>
      </li>`,
    )
    .join("");
  const checkbox = bundle.required_checkboxes[0];
  const biometric = bundle.biometric_responsibility_text
    ? `<p class="confirm-sub">${esc(bundle.biometric_responsibility_text)}</p>`
    : "";
  return `<div class="ml-consent-attribution">
    <p class="ml-consent-blocked-title">Licenses for the built-in models</p>
    <p class="confirm-sub">${esc(bundle.compressed_disclaimer)}</p>
    <ul class="ml-consent-list">${rows}</ul>
    ${biometric}
    <label class="ml-consent-ack">
      <input type="checkbox" id="ml-attr-ack" data-checkbox-id="${escapeAttr(checkbox?.id || "")}">
      <span>${esc(checkbox?.text || "")}</span>
    </label>
    <p class="confirm-sub">Without them, Best Photo Picker can't find faces or people, or score expressions and composition. You can accept them later in Settings → Models.</p>
  </div>`;
}

/**
 * OK-button label for the current checkbox state.
 * @param {boolean} accepted  checkbox ticked
 * @param {boolean} downloads  some models download only once accepted
 * @returns {string}
 */
export function attributionOkLabel(accepted, downloads) {
  if (!accepted) return "Analyze without them";
  return downloads ? "Accept, download & analyze" : "Accept & analyze";
}

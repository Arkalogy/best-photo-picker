// @ts-check
/**
 * One signal for "a model's license or files changed" — sent after an
 * accept, revoke, download or uninstall succeeds. Settings → Models
 * reloads its picker (while open) and the sidebar's content-filter hint
 * re-reads /api/v1/models/content-filter, so neither shows a stale state
 * until a page reload.
 */

export const MODELS_CHANGED_EVENT = "bpp:models-changed";

export function notifyModelsChanged() {
  document.dispatchEvent(new CustomEvent(MODELS_CHANGED_EVENT));
}

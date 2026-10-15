// @ts-check
/**
 * Face-embedder license acceptance dialog (the click-through that gates
 * restricted models) + its scroll-restore helpers.
 *
 * Split out of ``modals-face-embedders.mjs`` for the 500-LOC cap. Shares
 * picker state via ``feState`` and calls back into the picker
 * (``loadFaceEmbedderPicker`` / ``_feInstallGlobalHandlers``) to re-render
 * after accept/revoke. The import cycle with the picker module is safe:
 * every cross-referenced symbol is a hoisted function declaration called
 * at runtime, not at module-init time.
 */

import { apiFetch } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { _feInstallGlobalHandlers } from "./face-embedders-popover.mjs";
import { feState } from "./face-embedders-state.mjs";
import { loadFaceEmbedderPicker } from "./modals-face-embedders.mjs";
import { escapeAttr, esc } from "./text-format.mjs";
import { notifyModelsChanged } from "./models-changed.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * Open the click-through acceptance dialog for a model.
 *
 * @param {string} modelId
 * @param {string} [mode] ``"view"`` opens read-only even if never accepted
 *   (a withdrawn model: accepting can't enable it).
 */
export async function openFaceEmbedderAcceptance(modelId, mode) {
  // Ensure ESC + backdrop-click handlers are wired even when the
  // dialog is opened without first going through
  // loadFaceEmbedderPicker (e.g. from the runtime-block toast).
  _feInstallGlobalHandlers();
  // Snapshot the parent Settings → Models scroll position so we can
  // restore it after the dialog closes. Without this, accepting or
  // cancelling triggers loadFaceEmbedderPicker which rewrites the
  // picker container — the scroll resets to top and the user loses
  // their place in a long list.
  _captureParentScrollForRestore();
  try {
    const draft = await apiFetch(
      `/api/v1/model-registry/acceptance/draft?model_id=${encodeURIComponent(
        modelId,
      )}&use_context=${encodeURIComponent(feState.currentUseContext)}`,
    );
    feState.currentDraft = draft;
    // Pick the dialog's mode from the row's state (see _dialogMode):
    // view-only, confirm commercial rights, review with Withdraw, or a
    // fresh acceptance.
    _renderAcceptanceDialog(draft, _dialogMode(modelId, mode));
    const overlay = document.getElementById("fe-acceptance-overlay");
    if (overlay) {
      overlay.classList.add("visible");
      overlay.setAttribute("style", "display: flex");
    }
  } catch (err) {
    toast(`Could not load acceptance dialog: ${err.message || err}`, "error");
  }
}

/**
 * Which dialog to show:
 * - ``view``: read-only, Close only (a withdrawn / blocked model —
 *   accepting couldn't enable it, and there may be nothing to withdraw).
 * - ``rights``: accepted, but refused for commercial use without the
 *   separate-rights assertion — ask for it; withdrawing stays possible.
 * - ``review``: accepted and fine — read-only, with Withdraw.
 * - ``accept``: a fresh acceptance.
 *
 * @param {string} modelId
 * @param {string} [requested]
 * @returns {"view" | "rights" | "review" | "accept"}
 */
function _dialogMode(modelId, requested) {
  if (requested === "view") return "view";
  const accepted = feState.acceptedIds.has(modelId);
  // Rights mode re-uses the acceptance on file — only if there is one.
  if (accepted && feState.loadStates.get(modelId) === "needs_commercial_rights") return "rights";
  return accepted ? "review" : "accept";
}

/**
 * @param {object} draft
 * @param {"view" | "rights" | "review" | "accept"} [mode]
 */
function _renderAcceptanceDialog(draft, mode = "accept") {
  const body = document.getElementById("fe-acceptance-body");
  if (!body) return;
  // Read-only: review / view show the terms; rights keeps the (already
  // given) acknowledgments ticked and asks only for the rights assertion.
  const alreadyAccepted = mode === "review" || mode === "view";
  const boxAttr =
    mode === "view" ? " disabled" : mode === "accept" ? "" : " checked disabled";
  const checkboxes = (draft.required_checkboxes || [])
    .map(
      (cb) => `
      <label class="fe-checkbox">
        <input type="checkbox" data-checkbox-id="${escapeAttr(cb.id)}"${boxAttr}>
        <span>${esc(cb.text)}</span>
      </label>`,
    )
    .join("");
  const acceptedBanner =
    mode === "review"
      ? `<div class="fe-accepted-banner">✓ You've accepted these terms. Shown here for your reference.</div>`
      : mode === "view"
        ? `<div class="fe-notice-banner">This model has been withdrawn or blocked in the model registry, so it can't be enabled. Its terms are shown for reference.${
            feState.acceptedIds.has(draft.model_id)
              ? ` You can still <button type="button" class="fe-link-btn" data-action="revokeFaceEmbedderAcceptance" data-arg0="${escapeAttr(draft.model_id)}">withdraw your acceptance</button>.`
              : ""
          }</div>`
        : mode === "rights"
          ? `<div class="fe-notice-banner">You've accepted these terms, but your use context is Commercial: confirm below that you hold separate rights, or <button type="button" class="fe-link-btn" data-action="revokeFaceEmbedderAcceptance" data-arg0="${escapeAttr(draft.model_id)}">withdraw your acceptance</button>.</div>`
          : "";
  const commercialBlock =
    feState.currentUseContext === "commercial"
      ? `<div class="fe-section fe-section-rights">
           <div class="fe-section-title">Separate rights assertion (commercial use)</div>
           <p>${esc(draft.separate_rights_assertion || "")}</p>
           <label class="fe-checkbox">
             <input type="checkbox" id="fe-separate-rights"${alreadyAccepted ? boxAttr : ""}>
             <span>I confirm I have separate commercial rights for this model.</span>
           </label>
           <label class="fe-rights-note">
             <span>Optional note (stored locally for your records):</span>
             <input type="text" id="fe-rights-note"
                    placeholder="Where do your rights come from?" maxlength="500"${
                      alreadyAccepted ? " disabled" : ""
                    }>
           </label>
         </div>`
      : "";

  body.innerHTML = `
    ${acceptedBanner}
    <div class="fe-acceptance-title">${esc(draft.model_display_name)}</div>
    <div class="fe-acceptance-section">
      <p class="fe-compressed-disclaimer">${esc(draft.compressed_disclaimer || "")}</p>
      <details class="fe-full-terms">
        <summary>Show full terms</summary>
        <pre class="fe-full-terms-body">${esc(draft.full_disclaimer || "")}</pre>
      </details>
    </div>
    <div class="fe-acceptance-section">
      <div class="fe-section-title">Commercial use means:</div>
      <p>${esc(draft.commercial_use_definition || "")}</p>
    </div>
    ${
      draft.biometric_responsibility_text
        ? `<div class="fe-acceptance-section">
             <div class="fe-section-title">Biometric data responsibility:</div>
             <p>${esc(draft.biometric_responsibility_text)}</p>
           </div>`
        : ""
    }
    <div class="fe-acceptance-section fe-checkbox-section">
      <div class="fe-section-title">Acknowledgments:</div>
      ${checkboxes}
    </div>
    ${commercialBlock}
    <div class="fe-acceptance-terms-link">
      License document:
      <a href="${escapeAttr(draft.terms_permalink_url || draft.terms_url || "#")}"
         target="_blank" rel="noopener noreferrer"
         title="${escapeAttr(draft.terms_permalink_url || draft.terms_url || "")}"
        >${esc(_prettifyTermsUrl(draft.terms_permalink_url || draft.terms_url))}
        <span class="fe-terms-external">↗</span></a>
      <span class="fe-terms-retrieved">checked ${esc(draft.terms_retrieved_at || "?")}</span>
    </div>`;

  // The action buttons live in static markup reused across every open, so
  // each render must fully (re)set their label / action / styling — a prior
  // review-mode override (Withdraw/Close) must never leak into a fresh
  // acceptance, and vice-versa.
  const acceptBtn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("fe-accept-btn")
  );
  const cancelBtn = /** @type {HTMLElement | null} */ (
    document.querySelector('[data-action="closeFaceEmbedderAcceptance"]')
  );
  if (acceptBtn) acceptBtn.hidden = mode === "view";
  if (mode === "view") {
    // Nothing to accept or withdraw: Close only.
    if (cancelBtn) cancelBtn.textContent = "Close";
    return;
  }
  if (mode === "review") {
    // Read-only review of an accepted model. Re-accepting is pointless;
    // the only action that makes sense is to WITHDRAW. Repurpose the
    // primary button into a destructive "Withdraw acceptance".
    if (acceptBtn) {
      acceptBtn.disabled = false;
      acceptBtn.textContent = "Withdraw acceptance";
      acceptBtn.classList.remove("modal-btn-primary");
      acceptBtn.classList.add("modal-btn-danger");
      acceptBtn.dataset.action = "revokeFaceEmbedderAcceptance";
      acceptBtn.dataset.arg0 = draft.model_id;
    }
    if (cancelBtn) cancelBtn.textContent = "Close";
    return;
  }
  // Default acceptance state — reset any prior review-mode override.
  if (acceptBtn) {
    acceptBtn.textContent = "I accept";
    acceptBtn.classList.add("modal-btn-primary");
    acceptBtn.classList.remove("modal-btn-danger");
    acceptBtn.dataset.action = "confirmFaceEmbedderAcceptance";
    delete acceptBtn.dataset.arg0;
  }
  if (cancelBtn) cancelBtn.textContent = "Cancel";
  // Gate the "I accept" button: it stays disabled until every required
  // acknowledgment (and the commercial separate-rights box, when shown)
  // is checked. confirmFaceEmbedderAcceptance still re-validates, but the
  // disabled button is the primary, visible legal gate.
  _wireAcceptanceGate();
}

/**
 * True when every required checkbox in the acceptance dialog — plus the
 * commercial separate-rights box, when the user is in commercial context
 * — is checked. With no required boxes there is nothing to gate.
 *
 * @param {ParentNode | null} [scope] defaults to the acceptance overlay
 * @returns {boolean}
 */
export function _acceptanceGateSatisfied(scope) {
  const root = scope || document.getElementById("fe-acceptance-overlay");
  if (!root) return false;
  const boxes = root.querySelectorAll(
    "input[type=checkbox][data-checkbox-id]",
  );
  for (const b of boxes) {
    if (!(/** @type {HTMLInputElement} */ (b).checked)) return false;
  }
  if (feState.currentUseContext === "commercial") {
    const rights = /** @type {HTMLInputElement | null} */ (
      root.querySelector("#fe-separate-rights")
    );
    if (!rights || !rights.checked) return false;
  }
  return true;
}

/**
 * Wire the acceptance checkboxes to the "I accept" button's disabled
 * state, and set the initial state. Called after the dialog body is
 * (re)rendered. Old listeners die with the replaced checkbox nodes; the
 * persistent accept button only ever gets its `disabled` toggled.
 */
function _wireAcceptanceGate() {
  const overlay = document.getElementById("fe-acceptance-overlay");
  if (!overlay) return;
  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("fe-accept-btn")
  );
  if (!btn) return;
  const sync = () => {
    btn.disabled = !_acceptanceGateSatisfied(overlay);
  };
  const boxes = overlay.querySelectorAll(
    "input[type=checkbox][data-checkbox-id], #fe-separate-rights",
  );
  for (const b of boxes) b.addEventListener("change", sync);
  sync();
}

/**
 * Render a long upstream-license URL as a clean "host · filename"
 * line instead of dumping the full commit-pinned URL on screen.
 * The full URL stays in the <a href> + title attr so it's still
 * one click to open and hover-visible.
 *
 * @param {string | null | undefined} url
 */
function _prettifyTermsUrl(url) {
  if (!url) return "(no link)";
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    const parts = u.pathname.split("/").filter(Boolean);
    // GitHub blob URLs look like /<org>/<repo>/blob/<sha>/<...>/<file>
    // — strip the SHA noise and surface "org/repo · file" instead.
    if (host === "github.com" && parts.length >= 5 && parts[2] === "blob") {
      const org = parts[0];
      const repo = parts[1];
      const file = parts[parts.length - 1] || "(file)";
      return `${org}/${repo} · ${file}`;
    }
    if (host === "github.com" && parts.length >= 4 && parts[2] === "tree") {
      const org = parts[0];
      const repo = parts[1];
      return `${org}/${repo}`;
    }
    // Generic fallback: host · basename
    const last = parts[parts.length - 1] || "/";
    return `${host} · ${last}`;
  } catch {
    return url;
  }
}

/** Close the acceptance dialog. */
export function closeFaceEmbedderAcceptance() {
  const overlay = document.getElementById("fe-acceptance-overlay");
  if (overlay) {
    overlay.classList.remove("visible");
    overlay.setAttribute("style", "display: none");
  }
  feState.currentDraft = null;
  // Restore the parent scroll position once the next
  // loadFaceEmbedderPicker render completes (it re-renders the
  // picker container after close, so we install a one-shot
  // listener via _restoreParentScrollOnNextRender).
  _restoreParentScrollOnNextRender();
}

/**
 * Snapshot the scroll position of the picker's nearest scrollable
 * ancestor (the Settings modal body). Stored in module-level state
 * so :func:`_restoreParentScrollOnNextRender` can restore it after
 * ``loadFaceEmbedderPicker`` rewrites the picker container.
 */
function _captureParentScrollForRestore() {
  const container = document.getElementById("face-embedder-picker");
  const scroller = _findScrollableAncestor(container);
  feState.pendingScrollRestore = scroller ? scroller.scrollTop : null;
}

/**
 * Walk up the DOM from ``el`` returning the first ancestor whose
 * computed overflow-y allows scrolling. Used to find the Settings
 * modal body the picker lives in without hardcoding a selector
 * (the surrounding HTML structure has changed over time).
 *
 * @param {Element | null} el
 * @returns {HTMLElement | null}
 */
function _findScrollableAncestor(el) {
  let node = el?.parentElement || null;
  while (node && node !== document.body) {
    const style = window.getComputedStyle(node);
    const overflowY = style.overflowY;
    if (
      (overflowY === "auto" || overflowY === "scroll") &&
      node.scrollHeight > node.clientHeight
    ) {
      return /** @type {HTMLElement} */ (node);
    }
    node = node.parentElement;
  }
  return null;
}

/**
 * Watch the picker container for the next innerHTML change (the
 * post-acceptance ``loadFaceEmbedderPicker`` reload) and restore
 * the captured parent scroll position once the new rows are in
 * the DOM. MutationObserver fires before the browser paints, so
 * the user doesn't see a flash of scroll-reset.
 */
function _restoreParentScrollOnNextRender() {
  if (feState.pendingScrollRestore == null) return;
  const target = feState.pendingScrollRestore;
  feState.pendingScrollRestore = null;
  const container = document.getElementById("face-embedder-picker");
  if (!container) return;
  const scroller = _findScrollableAncestor(container);
  if (!scroller) return;
  const observer = new MutationObserver(() => {
    scroller.scrollTop = target;
    observer.disconnect();
  });
  observer.observe(container, { childList: true, subtree: true });
  // Safety: disconnect after 2 s if no render fires (e.g. the
  // close was a cancel that didn't trigger a reload).
  setTimeout(() => observer.disconnect(), 2000);
}

/** Confirm acceptance — POSTs to /acceptance/confirm. */
export async function confirmFaceEmbedderAcceptance() {
  if (!feState.currentDraft) {
    toast("Internal error: no draft loaded", "error");
    return;
  }
  const overlay = document.getElementById("fe-acceptance-overlay");
  if (!overlay) return;

  /** @type {Record<string, boolean>} */
  const responses = {};
  const boxes = overlay.querySelectorAll(
    "input[type=checkbox][data-checkbox-id]",
  );
  for (const box of boxes) {
    const id = /** @type {HTMLElement} */ (box).dataset.checkboxId;
    if (id) responses[id] = /** @type {HTMLInputElement} */ (box).checked;
  }
  if (!Object.values(responses).every(Boolean)) {
    toast("Check every acknowledgment to accept.", "error");
    return;
  }

  let separateRights = false;
  let rightsNote = "";
  if (feState.currentUseContext === "commercial") {
    const rightsBox = /** @type {HTMLInputElement | null} */ (
      document.getElementById("fe-separate-rights")
    );
    separateRights = !!rightsBox?.checked;
    if (!separateRights) {
      toast(
        "Commercial use requires you to assert separate commercial rights.",
        "error",
      );
      return;
    }
    const noteEl = /** @type {HTMLInputElement | null} */ (
      document.getElementById("fe-rights-note")
    );
    rightsNote = noteEl?.value?.trim() || "";
  }

  try {
    const result = await apiFetch("/api/v1/model-registry/acceptance/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        model_id: feState.currentDraft.model_id,
        checkbox_responses: responses,
        separate_rights_asserted: separateRights,
        source_of_rights_note: rightsNote,
        use_context: feState.currentUseContext,
        ack_text_version: feState.currentDraft.ack_text_version,
        ack_text_sha256: feState.currentDraft.ack_text_sha256,
        ack_text_kind: feState.currentDraft.ack_text_kind || "canonical",
      }),
    });
    // Add to the in-memory accepted set so a subsequent click on
    // "Use" doesn't bounce off the pre-gate before the picker
    // re-fetches the acceptance list.
    if (feState.currentDraft.model_id) {
      feState.acceptedIds.add(feState.currentDraft.model_id);
      feState.reacceptIds.delete(feState.currentDraft.model_id);
    }
    toast(`Acceptance recorded for ${feState.currentDraft.model_display_name}.`);
    closeFaceEmbedderAcceptance();
    notifyModelsChanged();
    return result;
  } catch (err) {
    toastError("record your acceptance", err);
  }
}

/**
 * Withdraw a recorded license acceptance. The model re-gates server-side
 * (it will require re-acceptance before it can load again). The original
 * acceptance stays in the local audit log — withdrawal is append-only.
 *
 * @param {string} modelId
 */
export async function revokeFaceEmbedderAcceptance(modelId) {
  if (!modelId) return;
  const ok = await appConfirm(
    "Withdraw license acceptance?",
    "You'll need to review and accept this model's terms again before " +
      "it can be used. Your original acceptance stays in the local audit " +
      "log.",
    { okLabel: "Withdraw", okClass: "danger" },
  );
  if (!ok) return;
  try {
    await apiFetch("/api/v1/model-registry/acceptance/revoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_id: modelId }),
    });
    feState.acceptedIds.delete(modelId);
    feState.reacceptIds.delete(modelId);
    feState.loadStates.delete(modelId); // stale until the picker reloads
    toast("License acceptance withdrawn.");
    closeFaceEmbedderAcceptance();
    notifyModelsChanged();
  } catch (err) {
    toastError("withdraw your acceptance", err);
  }
}

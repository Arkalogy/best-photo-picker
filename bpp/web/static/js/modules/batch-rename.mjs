// @ts-check
/**
 * Batch rename modal — pattern-based renaming with live preview.
 *
 * The pattern uses tokens like {date}, {name}, {counter:3}; the
 * server resolves them per photo via /api/batch/rename/preview.
 *
 * Reads state.currentGridItems / state.selectedPaths (still classic
 * mutable state). Reload of the grid after apply uses
 * window.loadPhotosAndRecompute.
 */

import { apiFetch } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

/**
 * @typedef {Object} RenameMapping
 * @property {string} old_filepath
 * @property {string} new_filename
 * @property {boolean} changed
 */

/** @type {RenameMapping[]} */
let renamePreviewData = [];

export function _getRenamePreviewData() {
  return renamePreviewData;
}

/** @param {RenameMapping[]} data */
export function _setRenamePreviewData(data) {
  renamePreviewData = data;
}

/**
 * Inspect the page-level selection and return the list of photo
 * filepaths the user has marked. Empty when nothing is selected.
 *
 * @returns {string[]}
 */
export function getSelectedPhotoPaths() {
  /** @type {any} */
  const win = window;
  const sel = win.selectedPaths;
  if (sel && sel instanceof Set && sel.size > 0) return Array.from(sel);
  return [];
}

/** Open the batch-rename modal — builds it on first use, then re-uses. */
export function showBatchRenameModal() {
  /** @type {any} */
  const win = window;
  const sel = getSelectedPhotoPaths();
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const count = sel.length || items.length;
  const scope = sel.length ? `${sel.length} selected` : "all photos";

  const overlay = document.getElementById("rename-overlay");
  if (overlay) {
    overlay.classList.remove("hidden");
    const scopeEl = document.getElementById("rename-scope");
    const countEl = document.getElementById("rename-count");
    const patternEl = /** @type {HTMLInputElement | null} */ (
      document.getElementById("rename-pattern")
    );
    if (scopeEl) scopeEl.textContent = scope;
    if (countEl) countEl.textContent = `${count} photos`;
    if (patternEl) patternEl.value = "{date}_{name}";
    renamePreviewData = [];
    updateRenamePreview();
    return;
  }

  const div = document.createElement("div");
  div.className = "rename-overlay";
  div.id = "rename-overlay";
  div.onclick = (e) => {
    if (e.target === div) hideBatchRenameModal();
  };
  // ESC closes the modal. Capture phase + stopImmediatePropagation so
  // the keypress doesn't bubble to the lightbox / global keydown
  // handlers (which would otherwise also fire). Mirror of the
  // reference pattern in dialogs.mjs:46-58. Listener stays attached
  // for the lifetime of the page since the modal is built once and
  // reused — gated on visibility so it no-ops while hidden.
  document.addEventListener(
    "keydown",
    (e) => {
      if (e.key !== "Escape") return;
      if (div.classList.contains("hidden")) return;
      e.stopPropagation();
      e.stopImmediatePropagation();
      hideBatchRenameModal();
    },
    true,
  );
  div.innerHTML = `
    <div class="rename-modal" role="dialog" aria-modal="true" aria-labelledby="rename-title">
      <div class="rename-header">
        <h3 id="rename-title">Batch Rename</h3>
        <button class="rename-close" data-action="hideBatchRenameModal" aria-label="Close">&times;</button>
      </div>
      <div class="rename-body">
        <div class="rename-info">
          <span id="rename-scope">${esc(scope)}</span>
          <span class="rename-dot">&middot;</span>
          <span id="rename-count">${count} photos</span>
        </div>
        <label class="rename-label">Pattern</label>
        <input type="text" class="rename-input" id="rename-pattern" value="{date}_{name}"
          data-oninput="updateRenamePreview" autocomplete="off" spellcheck="false">
        <div class="rename-tokens">
          <button class="rename-token" data-action="insertRenameToken" data-arg0="{name}">name</button>
          <button class="rename-token" data-action="insertRenameToken" data-arg0="{date}">date</button>
          <button class="rename-token" data-action="insertRenameToken" data-arg0="{year}">year</button>
          <button class="rename-token" data-action="insertRenameToken" data-arg0="{month}">month</button>
          <button class="rename-token" data-action="insertRenameToken" data-arg0="{day}">day</button>
          <button class="rename-token" data-action="insertRenameToken" data-arg0="{counter:3}">counter</button>
        </div>
        <div class="rename-preview" id="rename-preview">
          <div class="rename-preview-empty">Enter a pattern to see preview</div>
        </div>
      </div>
      <div class="rename-footer">
        <button class="btn-secondary" data-action="hideBatchRenameModal">Cancel</button>
        <button class="btn-primary" id="rename-apply-btn" data-action="applyBatchRename">Rename</button>
      </div>
    </div>
  `;
  document.body.appendChild(div);
  updateRenamePreview();
}

export function hideBatchRenameModal() {
  document.getElementById("rename-overlay")?.classList.add("hidden");
}

/**
 * Insert a token at the cursor position in the pattern input. Pulls
 * native textarea selection state for proper caret-after-insert.
 *
 * @param {string} token
 */
export function insertRenameToken(token) {
  const input = /** @type {HTMLInputElement | null} */ (
    document.getElementById("rename-pattern")
  );
  if (!input) return;
  const start = input.selectionStart || 0;
  const end = input.selectionEnd || 0;
  const val = input.value;
  input.value = val.slice(0, start) + token + val.slice(end);
  input.selectionStart = input.selectionEnd = start + token.length;
  input.focus();
  updateRenamePreview();
}

/**
 * Re-fetch the rename preview from the server using the current
 * pattern + selection. Renders results into the preview pane.
 */
export async function updateRenamePreview() {
  const patternEl = /** @type {HTMLInputElement | null} */ (
    document.getElementById("rename-pattern")
  );
  const pattern = patternEl?.value || "";
  const preview = document.getElementById("rename-preview");
  if (!preview) return;

  if (!pattern.trim()) {
    preview.innerHTML = '<div class="rename-preview-empty">Enter a pattern to see preview</div>';
    renamePreviewData = [];
    return;
  }

  /** @type {any} */
  const win = window;
  const sel = getSelectedPhotoPaths();
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const photoIds = sel.length
    ? items.filter((p) => sel.includes(p.filepath)).map((p) => p.id).filter(Boolean)
    : items.slice(0, 20).map((p) => p.id).filter(Boolean);

  try {
    const data = await apiFetch("/api/v1/batch/rename/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pattern, photo_ids: photoIds }),
    });
    renamePreviewData = data.mapping || [];
    renderRenamePreview(renamePreviewData);
  } catch (e) {
    preview.innerHTML = '<div class="rename-preview-empty">Failed to generate preview</div>';
    toastError("preview the rename", e);
  }
}

/**
 * Render the preview table — cap at 10 visible rows, show "+N more"
 * when truncated. Hide rows where the pattern doesn't actually
 * change anything.
 *
 * @param {RenameMapping[]} mapping
 */
export function renderRenamePreview(mapping) {
  const preview = document.getElementById("rename-preview");
  if (!preview) return;

  const changed = mapping.filter((m) => m.changed);
  if (changed.length === 0) {
    preview.innerHTML = '<div class="rename-preview-empty">No changes with this pattern</div>';
    return;
  }

  const rows = changed
    .slice(0, 10)
    .map((m) => {
      const oldName = m.old_filepath.split("/").pop() || "";
      return `<div class="rename-row">
      <span class="rename-old">${esc(oldName)}</span>
      <span class="rename-arrow">&rarr;</span>
      <span class="rename-new">${esc(m.new_filename)}</span>
    </div>`;
    })
    .join("");

  const more =
    changed.length > 10 ? `<div class="rename-more">+ ${changed.length - 10} more</div>` : "";
  preview.innerHTML = rows + more;
}

/**
 * Two-step apply: fetch the full mapping, ask for confirmation, then
 * POST the apply endpoint. Reloads the grid on success.
 */
export async function applyBatchRename() {
  const patternEl = /** @type {HTMLInputElement | null} */ (
    document.getElementById("rename-pattern")
  );
  const pattern = patternEl?.value || "";
  if (!pattern.trim()) return;

  /** @type {any} */
  const win = window;
  const sel = getSelectedPhotoPaths();
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  const photoIds = sel.length
    ? items.filter((p) => sel.includes(p.filepath)).map((p) => p.id).filter(Boolean)
    : items.map((p) => p.id).filter(Boolean);

  const btn = /** @type {HTMLButtonElement | null} */ (
    document.getElementById("rename-apply-btn")
  );
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Renaming...";
  }

  try {
    const previewData = await apiFetch("/api/v1/batch/rename/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pattern, photo_ids: photoIds }),
    });

    const mapping = previewData.mapping || [];
    const changed = mapping.filter(/** @param {RenameMapping} m */ (m) => m.changed);
    if (changed.length === 0) {
      toast("No files to rename");
      return;
    }

    const confirmed = await appConfirm(`Rename ${changed.length} files?`);
    if (!confirmed) return;

    const result = await apiFetch("/api/v1/batch/rename/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mapping }),
    });

    const successes = (result.results || []).filter(/** @param {any} r */ (r) => r.success)
      .length;
    const failures = (result.results || []).filter(/** @param {any} r */ (r) => !r.success)
      .length;

    if (failures > 0) {
      toast(`Renamed ${successes} files, ${failures} failed`, true); /* toast-ok: summary, not an error pattern */
    } else {
      toast(`Renamed ${successes} files`);
    }

    hideBatchRenameModal();
    if (typeof win.loadPhotosAndRecompute === "function") {
      await win.loadPhotosAndRecompute();
    }
  } catch (e) {
    toastError("rename the files", e);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Rename";
    }
  }
}

// @ts-check
/**
 * Library picker (vault switcher) — modal for switching between
 * registered libraries, adding new ones, removing, renaming.
 *
 * Reads window.ICONS for sidebar icons. Bridged onto window because
 * the picker is opened from inline `` in the shell template
 * and from search-palette / sidebar / settings.
 */

import { apiFetch } from "./api-client.mjs";
import { appConfirm, appPrompt } from "./dialogs.mjs";
import { formatDate } from "./date-format.mjs";
import { esc, escapeAttr, escapeJsAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";

let libraryPickerOpen = false;

export function _isLibraryPickerOpen() {
  return libraryPickerOpen;
}

export function showLibraryPicker() {
  const overlay = document.getElementById("library-picker-overlay");
  if (!overlay) return;
  overlay.classList.remove("hidden");
  libraryPickerOpen = true;
  loadLibraryList();
}

export function hideLibraryPicker() {
  const overlay = document.getElementById("library-picker-overlay");
  if (!overlay) return;
  overlay.classList.add("hidden");
  libraryPickerOpen = false;
}

/** @param {string} iso */
export function formatLibDate(iso) {
  return formatDate(iso, "relative");
}

/**
 * @typedef {Object} Library
 * @property {string} path
 * @property {string} name
 * @property {boolean} [exists]
 * @property {string | null} [last_opened]
 */

/**
 * Render the list of libraries with active / missing badges and
 * per-row Rename / Remove buttons.
 *
 * @param {Library[]} libraries
 * @param {string} activePath
 */
export function renderLibraryList(libraries, activePath) {
  const container = document.getElementById("library-list");
  if (!container) return;

  /** @type {any} */
  const win = window;
  const ICONS = win.ICONS || {};

  if (libraries.length === 0) {
    container.innerHTML = `
      <div class="library-empty">
        <div class="library-empty-icon">${ICONS.folder || ""}</div>
        <div>No libraries registered yet.</div>
        <div class="library-empty-sub">The current library will be added automatically.</div>
      </div>`;
    return;
  }

  let html = "";
  for (const lib of libraries) {
    const isActive = lib.path === activePath;
    const missing = lib.exists === false;
    const activeClass = isActive ? "library-item-active" : "";
    const missingClass = missing ? "library-item-missing" : "";
    const badge = isActive ? '<span class="library-badge">Current</span>' : "";
    const missingBadge = missing
      ? '<span class="library-badge library-badge-missing">Missing</span>'
      : "";
    const lastOpened = lib.last_opened ? formatLibDate(lib.last_opened) : "";
    const clickAction = missing ? "" : `data-action="switchLibrary" data-arg0="${escapeJsAttr(lib.path)}"`;
    html += `
      <div class="library-item ${activeClass} ${missingClass}" data-path="${escapeAttr(lib.path)}">
        <div class="library-item-main" ${clickAction}>
          <div class="library-item-icon">${ICONS.folder || ""}</div>
          <div class="library-item-info">
            <div class="library-item-name">${esc(lib.name)} ${badge}${missingBadge}</div>
            <div class="library-item-path">${esc(lib.path)}</div>
            ${missing ? '<div class="library-item-date" style="color:var(--red)">Folder no longer exists</div>' : ""}
            ${!missing && lastOpened ? `<div class="library-item-date">Last opened ${lastOpened}</div>` : ""}
          </div>
        </div>
        <div class="library-item-actions">
          ${
            !missing
              ? `<button class="library-item-btn" data-stop-propagation="true" data-action="renameLibraryPrompt" data-arg0="${escapeJsAttr(lib.path)}" data-arg1="${escapeJsAttr(lib.name)}" title="Rename">
            <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11.5 2.5l2 2M2 11l7-7 2 2-7 7H2v-2z"/></svg>
          </button>`
              : ""
          }
          ${
            !isActive
              ? `<button class="library-item-btn library-item-btn-danger" data-stop-propagation="true" data-action="removeLibraryPrompt" data-arg0="${escapeJsAttr(lib.path)}" data-arg1="${escapeJsAttr(lib.name)}" title="Remove from list">
            ${missing ? "Remove" : '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><line x1="4" y1="4" x2="12" y2="12"/><line x1="12" y1="4" x2="4" y2="12"/></svg>'}
          </button>`
              : ""
          }
        </div>
      </div>`;
  }
  container.innerHTML = html;
}

/** Fetch the registered libraries + the active path, then render. */
export async function loadLibraryList() {
  const container = document.getElementById("library-list");
  if (!container) return;
  container.innerHTML = '<div class="library-loading">Loading...</div>';
  try {
    const data = await apiFetch("/api/v1/libraries");
    const activeData = await apiFetch("/api/v1/libraries/active");
    renderLibraryList(data.libraries, activeData.path);
  } catch (e) {
    console.error("loadLibraryList failed:", e);
    container.innerHTML = '<div class="library-loading">Failed to load libraries</div>';
  }
}

/**
 * Switch to a different library. Does a hard reload after the server
 * accepts the switch — workers + connections need to bounce against
 * the new DB.
 *
 * @param {string} path
 */
export async function switchLibrary(path) {
  const items = document.querySelectorAll(".library-item");
  items.forEach((el) => el.classList.add("library-item-switching"));

  try {
    const data = await apiFetch("/api/v1/libraries/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (data.error) {
      toast(data.error, true);
      items.forEach((el) => el.classList.remove("library-item-switching"));
      return;
    }
    hideLibraryPicker();
    localStorage.removeItem("bpp_nav");
    location.reload();
  } catch (e) {
    toastError("switch library", e);
    items.forEach((el) => el.classList.remove("library-item-switching"));
  }
}

/** Open the native folder picker, register the chosen folder. */
export async function addExistingLibrary() {
  try {
    const data = await apiFetch("/api/v1/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "folder" }),
    });
    if (!data.path) return;
    const addData = await apiFetch("/api/v1/libraries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: data.path }),
    });
    if (addData.error) {
      toast(addData.error, true);
      return;
    }
    loadLibraryList();
    toast("Library added");
  } catch (e) {
    toastError("add the library", e);
  }
}

/** Pick a folder, register it, then immediately switch to it. */
export async function createNewLibrary() {
  try {
    const data = await apiFetch("/api/v1/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "folder" }),
    });
    if (!data.path) return;
    const lib = await apiFetch("/api/v1/libraries", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: data.path }),
    });
    if (lib.error) {
      toast(lib.error, true);
      return;
    }
    switchLibrary(lib.path);
  } catch (e) {
    toastError("create the library", e);
  }
}

/**
 * Two-step rename: prompt for new name, then ask whether to also
 * rename the folder on disk.
 *
 * @param {string} path
 * @param {string} currentName
 */
export async function renameLibraryPrompt(path, currentName) {
  const name = await appPrompt("Rename library", {
    placeholder: "Library name",
    value: currentName,
    okLabel: "Rename",
  });
  if (!name || name === currentName) return;
  const renameFolder = await appConfirm(
    "Also rename the folder on disk?",
    `This will rename the folder from "${currentName}" to "${name}".`,
    { okLabel: "Rename folder" },
  );
  try {
    const resp = await apiFetch("/api/v1/libraries/rename", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, name, rename_folder: !!renameFolder }),
    });
    if (resp.error) {
      toast(resp.error, true);
      return;
    }
    if (resp.new_path) {
      location.reload();
      return;
    }
    loadLibraryList();
  } catch (e) {
    toastError("rename the library", e);
  }
}

/**
 * @param {string} path
 * @param {string} name
 */
export async function removeLibraryPrompt(path, name) {
  const ok = await appConfirm(
    `Remove "${name}" from the library list?`,
    "The folder and its photos will NOT be deleted from disk.",
    { okLabel: "Remove", okClass: "danger" },
  );
  if (!ok) return;
  try {
    await apiFetch("/api/v1/libraries", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    loadLibraryList();
    toast("Library removed from list");
  } catch (e) {
    toastError("remove the library", e);
  }
}

/** Update the sidebar header to show the active library's name + path. */
export async function updateLibraryName() {
  try {
    const data = await apiFetch("/api/v1/libraries/active");
    const el = /** @type {HTMLElement | null} */ (
      document.getElementById("library-name-display")
    );
    if (el && data.name) {
      el.textContent = data.name;
      el.title = data.path;
    }
  } catch {
    /* silent */
  }
}

/** Used from Settings — rename the currently-active library. */
export async function renameCurrentLibrary() {
  try {
    const data = await apiFetch("/api/v1/libraries/active");
    if (data.path && data.name) {
      await renameLibraryPrompt(data.path, data.name);
      updateLibraryName();
    }
  } catch (e) {
    toastError("load library info", e);
  }
}

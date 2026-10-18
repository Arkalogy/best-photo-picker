// @ts-check
/**
 * The action rows under the reassign picker's person list
 * (`_lbShowFaceAssignPicker` in lightbox-face-picker.mjs): New person…,
 * Dismiss person, Not a face, Manage…. Split out for the 500-LOC cap.
 */

import { apiFetch } from "./api-client.mjs";
import { esc } from "./text-format.mjs";
import { toastError } from "./toast.mjs";
import { loadAlbumList } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { CLUSTER_DISMISSED } from "./constants.mjs";
import { updateLightboxFaces } from "./lightbox.mjs";
import { _lbReassignFace, dismissPerson_cluster } from "./lightbox-face-assign.mjs";

/**
 * @typedef {object} AssignActions
 * @property {HTMLElement} actions  the container to append under the list
 * @property {HTMLElement} newPerson  "New person…" (shortcut N)
 * @property {HTMLElement | null} dismissPerson  "Dismiss person" (D), when the face has one
 * @property {HTMLElement} dismiss  "Not a face" (F)
 */

/**
 * @param {object} p
 * @param {HTMLElement} p.overlay  the picker overlay; every action removes it
 * @param {HTMLElement} p.list  the person list; "New person…" turns it into a name input
 * @param {any} p.face
 * @param {(id: any) => string | null} p.personDisplayName
 * @returns {AssignActions}
 */
export function buildAssignActions({ overlay, list, face, personDisplayName }) {
  /** @type {any} */
  const win = window;
  const actions = document.createElement("div");
  actions.style.cssText = "border-top:1px solid var(--border);padding:4px 0";

  const newPerson = document.createElement("div");
  newPerson.className = "merge-picker-item";
  newPerson.innerHTML =
    `<div class="merge-picker-avatar" style="display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--accent)">+</div>` +
    `<div class="merge-picker-info"><div class="merge-picker-name" style="color:var(--accent)">New person…</div>` +
    `<div class="merge-picker-count">Create a new person</div></div>` +
    `<span class="ctx-shortcut" style="margin-left:auto">N</span>`;
  newPerson.onclick = () => _showNewPersonInput({ overlay, list, actions, face });
  actions.appendChild(newPerson);

  /** @type {HTMLElement | null} */
  let dismissPerson = null;
  if (face.cluster_id >= 0) {
    dismissPerson = document.createElement("div");
    dismissPerson.className = "merge-picker-item";
    const clusterName = personDisplayName(face.cluster_id) || `Person ${face.cluster_id + 1}`;
    dismissPerson.innerHTML =
      `<div class="merge-picker-avatar" style="display:flex;align-items:center;justify-content:center;font-size:16px;color:var(--text3)">✕</div>` +
      `<div class="merge-picker-info"><div class="merge-picker-name" style="color:var(--danger)">Dismiss person</div>` +
      `<div class="merge-picker-count">Remove ${esc(clusterName)} from sidebar, chips, and scoring</div></div>` +
      `<span class="ctx-shortcut" style="margin-left:auto">D</span>`;
    dismissPerson.onclick = async () => {
      overlay.remove();
      await dismissPerson_cluster(face.cluster_id);
    };
    actions.appendChild(dismissPerson);
  }

  const dismiss = document.createElement("div");
  dismiss.className = "merge-picker-item";
  dismiss.innerHTML =
    `<div class="merge-picker-avatar" style="display:flex;align-items:center;justify-content:center;font-size:16px;color:var(--text3)">✕</div>` +
    `<div class="merge-picker-info"><div class="merge-picker-name" style="color:var(--danger)">Not a face</div>` +
    `<div class="merge-picker-count">Dismiss this detection only</div></div>` +
    `<span class="ctx-shortcut" style="margin-left:auto">F</span>`;
  dismiss.onclick = async () => {
    overlay.remove();
    await _lbReassignFace(face.face_id, CLUSTER_DISMISSED);
  };
  actions.appendChild(dismiss);

  // Bridge to the shared person context menu (same one the sidebar /
  // People-row chip opens). Gives the face-overlay right-click access
  // to person-level actions — Rename, Change avatar, Merge, Split,
  // Exclude from picks — without duplicating the menu here.
  if (face.cluster_id >= 0 && typeof win.showPersonCtxMenu === "function") {
    const currentName =
      face.name || personDisplayName(face.cluster_id) || `Person ${face.cluster_id + 1}`;
    const manage = document.createElement("div");
    manage.className = "merge-picker-item";
    manage.innerHTML =
      `<div class="merge-picker-avatar" style="display:flex;align-items:center;justify-content:center;font-size:16px;color:var(--text3)">&#9881;</div>` +
      `<div class="merge-picker-info"><div class="merge-picker-name">Manage ${esc(currentName)}…</div>` +
      `<div class="merge-picker-count">Rename, merge, split, exclude…</div></div>`;
    manage.onclick = (ev) => {
      overlay.remove();
      win.showPersonCtxMenu(ev, face.cluster_id);
    };
    actions.appendChild(manage);
  }

  return { actions, newPerson, dismissPerson, dismiss };
}

/**
 * "New person…": the list becomes a name input; Create (or Enter)
 * reassigns the face to a fresh cluster and names its album.
 * @param {object} p
 * @param {HTMLElement} p.overlay
 * @param {HTMLElement} p.list
 * @param {HTMLElement} p.actions
 * @param {any} p.face
 */
function _showNewPersonInput({ overlay, list, actions, face }) {
  /** @type {any} */
  const win = window;
  list.innerHTML = "";
  actions.style.display = "none";
  const nameRow = document.createElement("div");
  nameRow.style.cssText = "padding:12px;display:flex;gap:8px;align-items:center";
  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.placeholder = "Enter name…";
  nameInput.className = "merge-picker-search";
  nameInput.style.margin = "0";
  const saveBtn = document.createElement("button");
  saveBtn.textContent = "Create";
  saveBtn.className = "merge-picker-cancel";
  saveBtn.style.cssText = "background:var(--accent);color:#fff;border:none";
  async function doCreate() {
    const name = nameInput.value.trim();
    if (!name) {
      nameInput.focus();
      return;
    }
    const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
    const nextCluster =
      faceClusters.length > 0 ? Math.max(...faceClusters.map((c) => c.cluster_id)) + 1 : 0;
    overlay.remove();
    try {
      await _lbReassignFace(face.face_id, nextCluster);
      await loadAlbumList();
      const albumId = win.getPersonAlbumId?.(nextCluster);
      if (albumId) {
        await apiFetch(`/api/v1/albums/${albumId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name }),
        });
        await loadAlbumList();
        await loadFaceClusters();
      }
      const items = /** @type {any[]} */ (win.currentGridItems || []);
      if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
        updateLightboxFaces(items[win.lightboxIdx]);
      }
    } catch (err) {
      toastError("create the new person", err);
    }
  }
  saveBtn.onclick = doCreate;
  nameInput.addEventListener("keydown", (ev) => {
    // stopImmediatePropagation, not stopPropagation: the lightbox's
    // own capture-phase keydown handlers (face-edit at ~1073, global
    // lightbox at ~2671) would otherwise also fire on Esc and close
    // the whole lightbox while the user just wanted to dismiss this
    // inline name input.
    ev.stopImmediatePropagation();
    if (ev.key === "Enter") doCreate();
    if (ev.key === "Escape") overlay.remove();
  });
  nameRow.appendChild(nameInput);
  nameRow.appendChild(saveBtn);
  list.appendChild(nameRow);
  setTimeout(() => nameInput.focus(), 50);
}

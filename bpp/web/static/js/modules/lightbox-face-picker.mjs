// @ts-check
/**
 * Lightbox face person-picker overlays — the two big "choose a person"
 * UIs surfaced from the face-assign flow.
 *
 * Extracted from lightbox-face-assign.mjs during the v0.1 cleanup.
 *
 *   * _lbShowAddFacePersonPicker — front half of the add-face flow.
 *     Calls onPick(clusterId, personName, isNew) and gets out of the way.
 *   * _lbShowFaceAssignPicker — right-click an existing face overlay to
 *     reassign / dismiss / manage.
 *
 * Both modals share visual idioms (merge-picker classnames, sticky
 * "+ New person…" affordance, search box, keyboard shortcuts) but the
 * action sets differ enough that pulling out shared helpers would hurt
 * readability — so they live side-by-side here.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { esc } from "./text-format.mjs";
import { toastError } from "./toast.mjs";
import { loadAlbumList } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { CLUSTER_DISMISSED } from "./constants.mjs";
import { _lbActiveCleanups, _lbPickerItems, updateLightboxFaces } from "./lightbox.mjs";
import {
  _lbReassignFace,
  dismissPerson_cluster,
} from "./lightbox-face-assign.mjs";


/**
 * Small picker for the Add Face flow: lists existing people plus a
 * "New person…" affordance. Calls onPick(clusterId, personName, isNew)
 * when the user chooses. Esc / click outside dismisses.
 *
 * Distinct from _lbShowFaceAssignPicker (which reassigns an existing
 * face_id) and _iphShowTagPicker (which writes photo_person_tags).
 * This one is the *front half* of the create-face flow.
 *
 * @param {MouseEvent} e
 * @param {(clusterId: number, personName: string, isNew: boolean) => void} onPick
 */
export function _lbShowAddFacePersonPicker(e, onPick) {
  /** @type {any} */
  const win = window;
  document.getElementById("lb-add-face-picker")?.remove();

  const overlay = document.createElement("div");
  overlay.id = "lb-add-face-picker";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";

  const header = document.createElement("div");
  header.className = "merge-picker-header";
  header.innerHTML =
    `<div style="display:flex;align-items:center;gap:10px">` +
    `<div style="font-size:22px">&#x2795;</div>` +
    `<div>Add face — who is this?<small>Pick a person, then drag the outline onto their face</small></div></div>`;
  picker.appendChild(header);

  const search = document.createElement("input");
  search.type = "text";
  search.placeholder = "Search people…";
  search.className = "merge-picker-search";
  picker.appendChild(search);

  const list = document.createElement("div");
  list.className = "merge-picker-list";

  const personDisplayName = win.personDisplayName || (() => null);

  function cleanup() {
    overlay.remove();
    document.removeEventListener("keydown", keyHandler, true);
    _lbActiveCleanups.delete(cleanup);
  }
  _lbActiveCleanups.add(cleanup);

  /** @param {KeyboardEvent} ev */
  const keyHandler = (ev) => {
    if (ev.key === "Escape") {
      // Capture phase + stopImmediatePropagation: the lightbox's own
      // keydown (line ~2671) is on document in bubble phase and would
      // otherwise also fire on Esc and close the whole lightbox.
      ev.stopImmediatePropagation();
      ev.preventDefault();
      cleanup();
    }
  };

  /** @param {string} filter */
  function renderList(filter) {
    list.innerHTML = "";
    const q = (filter || "").toLowerCase();
    const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
    const sorted = [...faceClusters].sort((a, b) => {
      const na = personDisplayName(a.cluster_id) || "";
      const nb = personDisplayName(b.cluster_id) || "";
      if (na && !nb) return -1;
      if (!na && nb) return 1;
      return b.photo_count - a.photo_count;
    });
    for (const c of sorted) {
      const name = personDisplayName(c.cluster_id) || "Person " + (c.cluster_id + 1);
      if (q && !name.toLowerCase().includes(q)) continue;
      const item = document.createElement("div");
      item.className = "merge-picker-item";
      const rep = c.representative;
      const avatarUrl =
        rep && rep.thumb_hash
          ? authedSrc(`/api/v1/faces/crop/${rep.thumb_hash}/${rep.face_index}`)
          : "";
      item.innerHTML =
        (avatarUrl
          ? `<img class="merge-picker-avatar" src="${avatarUrl}" alt="">`
          : `<div class="merge-picker-avatar"></div>`) +
        `<div class="merge-picker-info"><div class="merge-picker-name">${esc(name)}</div>` +
        `<div class="merge-picker-count">${c.photo_count} photo${c.photo_count === 1 ? "" : "s"}</div></div>`;
      item.onclick = () => {
        cleanup();
        onPick(c.cluster_id, name, false);
      };
      list.appendChild(item);
    }
    if (list.children.length === 0) {
      const empty = document.createElement("div");
      empty.className = "merge-picker-empty";
      empty.style.padding = "12px";
      empty.style.color = "var(--text3)";
      empty.textContent = "No matches";
      list.appendChild(empty);
    }
  }

  search.addEventListener("input", () => renderList(search.value));
  search.addEventListener("keydown", (ev) => ev.stopPropagation());
  picker.appendChild(list);
  renderList("");

  // Sticky "+ New person…" affordance. Mirrors the pattern from
  // _lbShowFaceAssignPicker: click expands an inline name input,
  // saves create a fresh cluster_id locally and continues into the
  // placeholder drag flow. The new person album gets renamed after
  // POST /faces/create commits.
  const newPerson = document.createElement("div");
  newPerson.className = "merge-picker-item";
  newPerson.style.borderTop = "1px solid var(--border)";
  newPerson.innerHTML =
    `<div class="merge-picker-avatar" style="display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--accent)">+</div>` +
    `<div class="merge-picker-info"><div class="merge-picker-name" style="color:var(--accent)">New person&hellip;</div>` +
    `<div class="merge-picker-count">Create a new person for this face</div></div>`;
  newPerson.onclick = () => {
    list.innerHTML = "";
    search.style.display = "none";
    newPerson.style.display = "none";
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
    function doCreate() {
      const name = nameInput.value.trim();
      if (!name) {
        nameInput.focus();
        return;
      }
      cleanup();
      // cluster_id is unused in the new-person branch — the server
      // allocates it. Pass -1 as a sentinel so a downstream consumer
      // that forgets to check isNew will fail loudly.
      onPick(-1, name, true);
    }
    saveBtn.onclick = doCreate;
    nameInput.addEventListener("keydown", (ev) => {
      ev.stopPropagation();
      if (ev.key === "Enter") doCreate();
      if (ev.key === "Escape") {
        ev.preventDefault();
        cleanup();
      }
    });
    nameRow.appendChild(nameInput);
    nameRow.appendChild(saveBtn);
    picker.appendChild(nameRow);
    setTimeout(() => nameInput.focus(), 30);
  };
  picker.appendChild(newPerson);

  overlay.appendChild(picker);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) cleanup();
  });
  document.body.appendChild(overlay);
  document.addEventListener("keydown", keyHandler, true);
  setTimeout(() => search.focus(), 50);
}

/**
 * @param {MouseEvent} e
 * @param {any} face
 * @param {string} thumbHash
 */
export function _lbShowFaceAssignPicker(e, face, thumbHash) {
  /** @type {any} */
  const win = window;
  document.getElementById("merge-picker-overlay")?.remove();
  const personDisplayName = win.personDisplayName || (() => null);

  const currentName =
    face.name || (face.cluster_id >= 0 ? personDisplayName(face.cluster_id) : null);
  const title = currentName ? `Reassign "${esc(currentName)}" to…` : "Who is this?";

  const overlay = document.createElement("div");
  overlay.id = "merge-picker-overlay";
  overlay.className = "merge-picker-overlay";

  const picker = document.createElement("div");
  picker.className = "merge-picker";

  const header = document.createElement("div");
  header.className = "merge-picker-header";
  const cropUrl = authedSrc(`/api/v1/faces/crop/${esc(thumbHash)}/${face.face_index}`);
  header.innerHTML =
    `<div style="display:flex;align-items:center;gap:10px">` +
    `<img src="${cropUrl}" style="width:40px;height:40px;border-radius:50%;object-fit:cover">` +
    `<div>${title}<small>Choose a person</small></div></div>`;
  picker.appendChild(header);

  const search = document.createElement("input");
  search.type = "text";
  search.placeholder = "Search people…";
  search.className = "merge-picker-search";
  picker.appendChild(search);

  const list = document.createElement("div");
  list.className = "merge-picker-list";

  /**
   * @param {string} filter
   */
  function renderList(filter) {
    list.innerHTML = "";
    const q = (filter || "").toLowerCase();
    const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
    const sorted = [...faceClusters].sort((a, b) => {
      const na = personDisplayName(a.cluster_id) || "";
      const nb = personDisplayName(b.cluster_id) || "";
      if (na && !nb) return -1;
      if (!na && nb) return 1;
      return b.photo_count - a.photo_count;
    });
    let idx = 0;
    // Mutate the imported array in place — ES module imports are
    // read-only bindings, so reassigning `_lbPickerItems = []` would
    // throw "Cannot assign to import". Length-set + push is equivalent
    // and visible to lightbox.mjs through the live binding.
    _lbPickerItems.length = 0;
    for (const c of sorted) {
      const name = personDisplayName(c.cluster_id) || `Person ${c.cluster_id + 1}`;
      if (q && !name.toLowerCase().includes(q)) continue;
      const item = document.createElement("div");
      item.className = "merge-picker-item";
      const rep = c.representative;
      const avatarUrl =
        rep && rep.thumb_hash
          ? authedSrc(`/api/v1/faces/crop/${rep.thumb_hash}/${rep.face_index}`)
          : "";
      const numKey =
        idx < 9
          ? `<span class="ctx-shortcut" style="margin-left:auto">${idx + 1}</span>`
          : "";
      item.innerHTML =
        (avatarUrl
          ? `<img class="merge-picker-avatar" src="${avatarUrl}" alt="">`
          : `<div class="merge-picker-avatar"></div>`) +
        `<div class="merge-picker-info"><div class="merge-picker-name">${esc(name)}</div>` +
        `<div class="merge-picker-count">${c.photo_count} photo${c.photo_count === 1 ? "" : "s"}</div></div>` +
        numKey;
      item.onclick = async () => {
        overlay.remove();
        await _lbReassignFace(face.face_id, c.cluster_id);
      };
      _lbPickerItems.push(item);
      list.appendChild(item);
      idx++;
    }
  }

  search.addEventListener("input", () => renderList(search.value));
  search.addEventListener("keydown", (ev) => {
    ev.stopPropagation();
    if (ev.key === "Escape") {
      overlay.remove();
      return;
    }
    if (!search.value && ev.key >= "1" && ev.key <= "9") {
      const idx = parseInt(ev.key) - 1;
      if (_lbPickerItems[idx]) {
        ev.preventDefault();
        _lbPickerItems[idx].click();
        return;
      }
    }
    if (!search.value) {
      if (ev.key.toLowerCase() === "n") {
        ev.preventDefault();
        newPerson.click();
        return;
      }
      if (ev.key.toLowerCase() === "d" && dismissPerson) {
        ev.preventDefault();
        dismissPerson.click();
        return;
      }
      if (ev.key.toLowerCase() === "f") {
        ev.preventDefault();
        dismiss.click();
        return;
      }
    }
  });
  picker.appendChild(list);
  renderList("");

  const actions = document.createElement("div");
  actions.style.cssText = "border-top:1px solid var(--border);padding:4px 0";

  const newPerson = document.createElement("div");
  newPerson.className = "merge-picker-item";
  newPerson.innerHTML =
    `<div class="merge-picker-avatar" style="display:flex;align-items:center;justify-content:center;font-size:18px;color:var(--accent)">+</div>` +
    `<div class="merge-picker-info"><div class="merge-picker-name" style="color:var(--accent)">New person…</div>` +
    `<div class="merge-picker-count">Create a new person</div></div>` +
    `<span class="ctx-shortcut" style="margin-left:auto">N</span>`;
  newPerson.onclick = () => {
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
  };
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

  picker.appendChild(actions);

  const footer = document.createElement("div");
  footer.className = "merge-picker-footer";
  const cancelBtn = document.createElement("button");
  cancelBtn.className = "merge-picker-cancel";
  cancelBtn.textContent = "Cancel";
  cancelBtn.onclick = () => overlay.remove();
  footer.appendChild(cancelBtn);
  picker.appendChild(footer);

  overlay.appendChild(picker);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) overlay.remove();
  });
  document.body.appendChild(overlay);
  setTimeout(() => search.focus(), 50);
}

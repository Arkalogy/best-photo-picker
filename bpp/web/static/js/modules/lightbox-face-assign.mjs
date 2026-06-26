// @ts-check
/**
 * Lightbox face-assign flow: pick a person for a face, add a face,
 * reassign an existing face.
 *
 * Extracted from lightbox.mjs during the v0.1 cleanup. This module
 * owns the "choose a person" workflow that surfaces in three places:
 *
 *   1. _lbBeginAddFace + _lbShowAddFacePersonPicker +
 *      _lbStartAddFacePlaceholder — user wants to draw a new face
 *      box on the photo, picks the person first, then dragging the
 *      placeholder commits via the face-edit flow.
 *   2. _lbShowFaceAssignPicker — user right-clicks an unidentified
 *      face overlay, picks the person to assign it to.
 *   3. _lbReassignFace + _lbTagPersonFromMenu — programmatic assign
 *      from menu items.
 *
 * Cross-module dependencies that stay in lightbox.mjs:
 *   _lbGetFaceContainer, updateLightboxFaces. Imported back here
 *   via the existing circular-import pattern (ES modules resolve
 *   function references at call time).
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import { loadAlbumList } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { _iphShowTagPicker } from "./inspector.mjs";
import { CLUSTER_DISMISSED } from "./constants.mjs";
import {
  _lbActiveCleanups,
  _lbGetFaceContainer,
  hideLbCtxMenu,
  updateLightboxFaces,
} from "./lightbox.mjs";
import {
  _lbBeginFaceEdit,
  _lbCommitBboxUpdate,
  _lbEdit,
  _lbEndFaceEdit,
  _lbReadBoxPct,
  _lbStartDrag,
} from "./lightbox-face-edit.mjs";
import {
  _lbShowAddFacePersonPicker,
  _lbShowFaceAssignPicker,
} from "./lightbox-face-picker.mjs";
export { _lbShowAddFacePersonPicker, _lbShowFaceAssignPicker };


export function _lbBeginAddFace(e, thumbHash) {
  hideLbCtxMenu();
  // Snap the placeholder to the spot the user right-clicked (in
  // percentage-of-wrapper space, so it survives zoom/pan). If we
  // can't resolve the wrapper or the coords are outside, fall back
  // to the image-center default inside _lbStartAddFacePlaceholder.
  const initialCenter = _lbCoordsToWrapperPct(e.clientX, e.clientY);
  _lbShowAddFacePersonPicker(e, (clusterId, personName, isNew) => {
    _lbStartAddFacePlaceholder(thumbHash, clusterId, personName, !!isNew, initialCenter);
  });
}

/**
 * Convert viewport coordinates to percentage-of-image-wrapper, matching
 * the coordinate space face overlays use. Returns null if the wrapper
 * isn't mounted or the point lies outside it.
 *
 * @param {number} clientX
 * @param {number} clientY
 * @returns {{ x: number, y: number } | null}
 */
export function _lbCoordsToWrapperPct(clientX, clientY) {
  const wrapper = document.querySelector(".lb-img-wrapper");
  if (!wrapper) return null;
  const r = wrapper.getBoundingClientRect();
  if (r.width <= 0 || r.height <= 0) return null;
  const x = ((clientX - r.left) / r.width) * 100;
  const y = ((clientY - r.top) / r.height) * 100;
  if (x < 0 || x > 100 || y < 0 || y > 100) return null;
  return { x, y };
}


/**
 * Renders a dashed-amber placeholder overlay and enters edit mode on
 * it. The user drags it onto the real face, releases, and
 * _lbCommitBboxUpdate posts to /api/v1/faces/create.
 *
 * @param {string} thumbHash
 * @param {number} clusterId
 * @param {string} personName
 * @param {boolean} isNewPerson  When true, the resulting person album is renamed to personName after commit.
 * @param {{ x: number, y: number } | null} [initialCenter]  When set, center the placeholder on this point (wrapper-percent space). Used to snap the box near where the user right-clicked instead of the image middle.
 */
export function _lbStartAddFacePlaceholder(thumbHash, clusterId, personName, isNewPerson, initialCenter) {
  const container = _lbGetFaceContainer();
  if (!container) {
    toast("Open a photo first", true);
    return;
  }

  const box = document.createElement("div");
  box.className = "lb-face-overlay lb-face-overlay-placeholder";
  // Default size — ~20% × 30% of the image. Center on the right-click
  // point when we have it, else fall back to roughly image-centered.
  const W = 20;
  const H = 30;
  let def;
  if (initialCenter) {
    let x = initialCenter.x - W / 2;
    let y = initialCenter.y - H / 2;
    // Clamp so the box stays fully inside [0, 100].
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x + W > 100) x = 100 - W;
    if (y + H > 100) y = 100 - H;
    def = { x, y, w: W, h: H };
  } else {
    def = { x: 40, y: 35, w: W, h: H };
  }
  box.style.left = def.x + "%";
  box.style.top = def.y + "%";
  box.style.width = def.w + "%";
  box.style.height = def.h + "%";
  box.dataset.clusterId = String(clusterId);

  const label = document.createElement("span");
  label.className = "lb-face-overlay-label";
  label.textContent = personName;
  box.appendChild(label);

  /** @type {any} */
  const face = {
    face_id: null, // signals "placeholder" to _lbCommitBboxUpdate
    cluster_id: clusterId,
    name: personName,
    bbox_pct: { ...def },
    _isNewPerson: isNewPerson,
    _newPersonName: isNewPerson ? personName : null,
  };

  // Box mousedown → start a move-drag (no edit-mode toggle needed; the
  // box is already in edit mode after _lbBeginFaceEdit below). Skip if
  // the target is a handle (handles have their own mousedown listener).
  box.addEventListener("mousedown", (e) => {
    if (e.button !== 0) return;
    if (box.classList.contains("lb-face-overlay-busy")) return;
    if (/** @type {HTMLElement} */ (e.target).classList.contains("lb-face-handle")) return;
    e.preventDefault();
    e.stopPropagation();
    _lbStartDrag(/** @type {MouseEvent} */ (e), "move");
  });

  container.appendChild(box);
  _lbBeginFaceEdit(face, thumbHash, box, null);

  // Floating Save / Cancel toolbar — placeholders don't auto-commit, so
  // the user can drag/resize as many times as they want and only triggers
  // the server call when they're ready.
  const toolbar = document.createElement("div");
  toolbar.id = "lb-add-face-toolbar";
  toolbar.style.cssText =
    "position:absolute;bottom:14px;left:50%;transform:translateX(-50%);" +
    "display:flex;gap:8px;background:rgba(0,0,0,0.78);padding:8px 12px;" +
    "border-radius:10px;z-index:1000;box-shadow:0 6px 24px rgba(0,0,0,0.4);" +
    "pointer-events:auto";
  const cancelBtn = document.createElement("button");
  cancelBtn.type = "button";
  cancelBtn.textContent = "Cancel";
  cancelBtn.className = "merge-picker-cancel";
  cancelBtn.style.cssText = "padding:6px 14px";
  // See the saveBtn block below for why we commit on mouseup rather
  // than click — same WKWebView quirk applies to both buttons.
  let _cancelled = false;
  cancelBtn.addEventListener("mousedown", (ev) => ev.stopPropagation(), true);
  cancelBtn.addEventListener("mouseup", (ev) => {
    if (_cancelled) return;
    _cancelled = true;
    ev.stopPropagation();
    toolbar.remove();
    _lbEndFaceEdit();
    toast("Add face cancelled.");
  });
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.textContent = `Save (${personName})`;
  saveBtn.className = "merge-picker-cancel";
  saveBtn.style.cssText =
    "padding:6px 14px;background:var(--accent);color:#fff;border:none";
  // Capture-phase: outside-click handler also runs on capture and may
  // tear down edit mode if our toolbar exemption misses. Stop the
  // event before any outside listeners see it.
  saveBtn.addEventListener("mousedown", (ev) => ev.stopPropagation(), true);
  // Commit on mouseup, NOT click. In Tauri's WKWebView the click event
  // doesn't reliably fire on this floating button — mousedown and
  // mouseup both land on the button, but click never dispatches. Diagnosed
  // empirically; using mouseup sidesteps the issue and works in both
  // WKWebView and standard browsers (mouseup fires before click).
  let _committing = false;
  saveBtn.addEventListener("mouseup", async (ev) => {
    if (_committing) return;
    _committing = true;
    ev.stopPropagation();
    if (!_lbEdit) {
      toast("Edit state lost — please re-open Add face.", true);
      toolbar.remove();
      return;
    }
    const current = _lbReadBoxPct(_lbEdit.box);
    toolbar.remove();
    await _lbCommitBboxUpdate(current);
  });
  toolbar.appendChild(cancelBtn);
  toolbar.appendChild(saveBtn);
  // Mount on the image wrapper, NOT the face container. The face container
  // has `pointer-events: none` (so the image stays clickable through it)
  // and receives a zoom/pan transform — putting the toolbar inside would
  // both block all clicks and make the buttons zoom with the image.
  const wrapper = document.querySelector(".lb-img-wrapper");
  (wrapper || container).appendChild(toolbar);

  toast(`Drag the outline onto ${personName}'s face, then hit Save.`);
}

/**
 * @param {MouseEvent} e
 * @param {string} thumbHash
 */
export async function _lbTagPersonFromMenu(e, thumbHash) {
  // "Tag person" from the photo's context menu adds a *manual person tag*
  // to this photo (photo_person_tags) — it does NOT touch any existing
  // face_embeddings row. The earlier branch-on-face-count logic was
  // hijacking a wrong/phantom detection (e.g. a phantom box on a brick
  // wall) and reassigning its cluster to the chosen person, which is
  // never what "tag a person in this photo" means.
  //
  // The reassign-an-existing-face flow is still available by
  // right-clicking the face overlay itself.
  _iphShowTagPicker(e, thumbHash);
}



/**
 * @param {number} clusterId
 */
export async function dismissPerson_cluster(clusterId) {
  /** @type {any} */
  const win = window;
  const personDisplayName = win.personDisplayName || (() => null);
  const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
  const ok = await appConfirm(
    `Dismiss "${name}"? Removes from sidebar, chips, and scoring. Recoverable from Ignored.`,
    "",
    { okLabel: "Dismiss", okClass: "danger" }
  );
  if (!ok) return;
  try {
    const resp = await apiFetch("/api/v1/faces/dismiss", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cluster_id: clusterId }),
    });
    if (resp.error) {
      toast(resp.error, true);
      return;
    }
    if (resp.albums) {
      win.albumList = resp.albums;
      /** @type {any} */ (window).renderAlbumNav?.();
    }
    await loadFaceClusters();
    toast(`Dismissed "${name}"`);
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
      updateLightboxFaces(items[win.lightboxIdx]);
    }
    const pv = document.getElementById("people-view");
    if (pv && !pv.classList.contains("hidden")) win.showPeopleView?.();
  } catch (err) {
    console.error("Dismiss failed:", err);
    toastError("dismiss that face", err);
  }
}

/**
 * @param {number} faceId
 * @param {number} clusterId
 */
export async function _lbReassignFace(faceId, clusterId) {
  /** @type {any} */
  const win = window;
  const personDisplayName = win.personDisplayName || (() => null);
  try {
    const resp = await apiFetch("/api/v1/faces/reassign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ face_id: faceId, cluster_id: clusterId }),
    });
    if (resp.error) {
      toast(resp.error, true);
      return;
    }
    if (resp.albums) {
      win.albumList = resp.albums;
      /** @type {any} */ (window).renderAlbumNav?.();
    }
    await loadFaceClusters();
    if (clusterId === CLUSTER_DISMISSED) {
      toast("Face dismissed");
    } else {
      const name = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
      const extra = resp.propagated > 0 ? ` (+${resp.propagated} similar faces found)` : "";
      toast(`Assigned to ${name}${extra}`);
    }
    if (resp.warning) toast(resp.warning, true);
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
      updateLightboxFaces(items[win.lightboxIdx]);
    }
    const pv = document.getElementById("people-view");
    if (pv && !pv.classList.contains("hidden")) {
      win.showPeopleView?.();
    }
  } catch (err) {
    toastError("reassign the face", err);
  }
}

/**
 * @param {any} p
 */
export async function updateLightboxPets(p) {
  /** @type {any} */
  const win = window;
  const container = document.getElementById("lb-pets");
  if (!container) return;
  if (!p.has_cat && !p.has_dog) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");

  const ICONS = win.ICONS || {};
  try {
    const data = await apiFetch(`/api/v1/pets/detections/${p.thumb_hash}`);
    const dets = data.detections || [];
    if (dets.length === 0) {
      const pets = [];
      if (p.has_cat) pets.push("Cat");
      if (p.has_dog) pets.push("Dog");
      const chips = pets
        .map(
          (name) =>
            `<div class="lb-pet-chip"><span class="lb-pet-icon">${ICONS.paw || ""}</span><span>${name}</span></div>`
        )
        .join("");
      container.innerHTML = `<div class="lb-faces-label">Pets in photo</div><div class="lb-faces-row">${chips}</div>`;
      return;
    }
    const petDisplayName = win.petDisplayName || (() => null);
    const chips = dets
      .map((d) => {
        const label = d.class.charAt(0).toUpperCase() + d.class.slice(1);
        const albumName = petDisplayName(d.cluster_id) || label;
        return `<div class="lb-pet-chip" title="${escapeAttr(label)} (${(d.confidence * 100).toFixed(0)}%)">
        <img src="${authedSrc(`/api/v1/pets/crop/${p.thumb_hash}/${d.detection_index}`)}" class="lb-pet-crop">
        <span>${esc(albumName)}</span>
      </div>`;
      })
      .join("");
    container.innerHTML = `<div class="lb-faces-label">Pets in photo</div><div class="lb-faces-row">${chips}</div>`;
  } catch (err) {
    console.warn("Failed to load pet detections:", err);
    container.classList.add("hidden");
    container.innerHTML = "";
  }
}


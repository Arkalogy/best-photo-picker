// @ts-check
/**
 * Lightbox face surface: bottom face strip + bbox overlays on the image.
 *
 * Extracted from lightbox.mjs during the v0.1 cleanup. This module
 * owns the read+render side of faces on a photo (the mutation side
 * lives in lightbox-face-edit + lightbox-face-assign):
 *
 *   * updateLightboxFaces       — fetch /api/v1/faces/photo and render
 *     the bottom "People" chip row
 *   * _lbRenderFaceOverlays     — paint the bbox boxes on top of the
 *     image inside .lb-face-container
 *   * _lbGetFaceContainer       — DOM helper used by both renderers
 *     and by the face-edit + add-face flows (re-exported)
 *   * _lbOpenPersonAlbum        — click a chip → jump to that person's
 *     smart album
 *   * _lbFaceOverlayCtx         — right-click on an overlay → menu
 *   * _lbDismissDetectedFace    — dismiss one detection (this photo only)
 *   * _lbUntagPerson            — remove a person tag (this photo only)
 *
 * Cross-module dependencies that stay in lightbox.mjs:
 *   _lbFaceData (module-local state holding the last fetched face list)
 *   closeLightbox.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { toast, toastError } from "./toast.mjs";
import { switchAlbum } from "./albums.mjs";
import { _iphShowTagPicker } from "./inspector.mjs";
import { CLUSTER_DISMISSED, CLUSTER_UNASSIGNED } from "./constants.mjs";
import { closeLightbox } from "./lightbox.mjs";
import { _lbBeginFaceEdit, _lbStartDrag } from "./lightbox-face-edit.mjs";
import { _lbShowFaceAssignPicker } from "./lightbox-face-assign.mjs";

// Last fetched face list for the current photo. Read by
// _lbFaceOverlayCtx; written by updateLightboxFaces. Module-local
// (the only readers are within this file).
/** @type {any[]} */
let _lbFaceData = [];


export async function updateLightboxFaces(p) {
  /** @type {any} */
  const win = window;
  const container = document.getElementById("lb-faces");
  _lbFaceData = [];

  let faces = [];
  let personTags = [];
  if (p.thumb_hash) {
    try {
      const data = await apiFetch(`/api/v1/faces/photo/${p.thumb_hash}`);
      faces = data.faces || [];
      personTags = data.person_tags || [];
    } catch (err) {
      console.warn("Failed to load faces:", err);
    }
  }
  _lbFaceData = faces;

  _lbRenderFaceOverlays(faces, p.thumb_hash);

  if (!container) return;
  const activeFaces = faces.filter((f) => f.cluster_id !== null && f.cluster_id >= 0);
  const faceClusters = /** @type {any[]} */ (win.faceClusters || []);
  const personDisplayName = win.personDisplayName || (() => null);

  if (activeFaces.length === 0 && personTags.length === 0 && faceClusters.length === 0) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }
  container.classList.remove("hidden");

  // Detected-face chip — click opens that person's album, right-click
  // opens the shared person context menu (Rename, Change avatar, Merge,
  // Split, Exclude, Not a face, Dismiss). Same menu the sidebar uses,
  // so the two now feel connected. The ✕ on hover dismisses just this
  // one detection (this photo only).
  // Both click and contextmenu read data-arg0; cluster_id satisfies both.
  const chips = activeFaces
    .map((f) => {
      const name = f.name || personDisplayName(f.cluster_id) || `Person ${f.cluster_id + 1}`;
      const cropUrl = authedSrc(`/api/v1/faces/crop/${esc(p.thumb_hash)}/${f.face_index}`);
      return `<div class="lb-face-chip" data-cluster-id="${f.cluster_id}" data-action="_lbOpenPersonAlbum" data-pass-event="true" data-arg0="${f.cluster_id}" data-oncontextmenu="showPersonCtxMenu" title="Open ${escapeAttr(name)}'s photos &nbsp;·&nbsp; right-click for options">
      <img src="${cropUrl}">
      <span class="lb-face-name">${esc(name)}</span>
      <span class="lb-face-untag" title="Dismiss this detection (this photo only)" data-action="_lbDismissDetectedFace" data-pass-event="true" data-arg0="${f.face_id}" data-arg1="${escapeAttr(name)}">&#215;</span>
    </div>`;
    })
    .join("");

  const tagChips = personTags
    .map((pt) => {
      const label = pt.name || personDisplayName(pt.cluster_id) || `Person ${pt.cluster_id + 1}`;
      const cluster = faceClusters.find((c) => c.cluster_id === pt.cluster_id);
      const rep = cluster && cluster.representative;
      const avatarUrl = rep
        ? authedSrc(`/api/v1/faces/crop/${esc(rep.thumb_hash)}/${rep.face_index}`)
        : "";
      const img = avatarUrl
        ? `<img src="${avatarUrl}">`
        : `<span class="lb-face-add-icon" style="font-size:14px">&#128100;</span>`;
      return `<div class="lb-face-chip" data-cluster-id="${pt.cluster_id}" data-action="_lbOpenPersonAlbum" data-pass-event="true" data-arg0="${pt.cluster_id}" data-oncontextmenu="showPersonCtxMenu" title="${escapeAttr(label)} (tagged) &nbsp;·&nbsp; click to open ${escapeAttr(label)}'s photos">
      ${img}
      <span class="lb-face-name">${esc(label)}</span>
      <span class="lb-face-untag" title="Remove tag (this photo only)" data-action="_lbUntagPerson" data-pass-event="true" data-arg0="${p.thumb_hash}" data-arg1="${pt.cluster_id}">&#215;</span>
    </div>`;
    })
    .join("");

  const unassigned = faces.filter(
    (f) => (f.cluster_id === null || f.cluster_id < 0) && f.cluster_id !== CLUSTER_DISMISSED
  );
  const unassignedChip =
    unassigned.length > 0
      ? `<div class="lb-face-chip lb-face-unassigned" title="${unassigned.length} unidentified face${unassigned.length > 1 ? "s" : ""} — right-click on the photo to assign">` +
        `<span class="lb-face-add-icon">?</span><span class="lb-face-name">${unassigned.length} unknown</span></div>`
      : "";

  const addChip =
    faceClusters.length > 0
      ? `<div class="lb-face-chip lb-face-add" title="Tag a person" data-action="_iphShowTagPicker" data-pass-event="true" data-arg0="${p.thumb_hash}"><span class="lb-face-add-icon">+</span></div>`
      : "";

  container.innerHTML = `<div class="lb-faces-label">People</div><div class="lb-faces-row">${chips}${tagChips}${unassignedChip}${addChip}</div>`;
}

/**
 * @param {MouseEvent} e
 * @param {string} thumbHash
 * @param {number} clusterId
 */
export async function _lbUntagPerson(e, thumbHash, clusterId) {
  /** @type {any} */
  const win = window;
  e.stopPropagation();
  try {
    await apiFetch("/api/v1/faces/tag", {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path_hash: thumbHash, cluster_id: clusterId }),
    });
    toast("Person tag removed");
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
      updateLightboxFaces(items[win.lightboxIdx]);
    }
  } catch (err) {
    toastError("remove this person tag", err);
  }
}

/**
 * Click handler on a People-row chip: close the lightbox and jump to
 * that person's smart album. Mirrors the sidebar's "click person → open
 * their photos" pattern so the two affordances feel congruent.
 *
 * @param {MouseEvent} e
 * @param {number} clusterId
 */
export function _lbOpenPersonAlbum(e, clusterId) {
  e.stopPropagation();
  /** @type {any} */
  const win = window;
  const albumId = win.getPersonAlbumId?.(clusterId);
  if (!albumId) {
    toast("No album for this person yet — try refreshing People", true);
    return;
  }
  // closeLightbox(e) early-returns unless e.target is the lightbox
  // backdrop element itself (its safety check for "click on overlay
  // means close"). For programmatic closes we must pass no event.
  closeLightbox();
  switchAlbum(albumId);
}

/**
 * Bridge invoked by the global contextmenu dispatcher when the user
 * right-clicks a face overlay. The dispatcher's closest() walks up
 * from e.target and finds the face overlay (with its
 * data-oncontextmenu) before the outer lb-img-wrapper, so this fires
 * instead of the photo-level menu.
 *
 * Opens the shared person context menu — the same one the chip and
 * sidebar use — with face context, so the dropdown shows extra
 * face-scoped items (Reassign…, Dismiss this face) at the top.
 *
 * For unassigned faces (no cluster yet), fall back to the full assign
 * picker since the person menu has nothing useful to show for an
 * unidentified face.
 *
 * @param {MouseEvent} e
 * @param {number} faceId
 * @param {string} thumbHash
 */
export function _lbFaceOverlayCtx(e, faceId, thumbHash) {
  e.preventDefault();
  e.stopPropagation();
  const face = _lbFaceData.find((f) => f.face_id === faceId);
  if (!face) return;
  /** @type {any} */
  const win = window;
  if (face.cluster_id == null || face.cluster_id < 0) {
    // Unassigned face — no person menu yet, jump straight to the assign picker.
    _lbShowFaceAssignPicker(e, face, thumbHash);
    return;
  }
  win.showPersonCtxMenu?.(e, face.cluster_id, { faceId, thumbHash });
}

/**
 * ✕ handler on a detected-face chip: dismiss this specific detection
 * (this photo only). Sets cluster_id to CLUSTER_DISMISSED (-2) on the
 * single face_embeddings row — never affects other photos.
 *
 * @param {MouseEvent} e
 * @param {number} faceId
 * @param {string} personName
 */
export async function _lbDismissDetectedFace(e, faceId, personName) {
  e.stopPropagation();
  const label = personName
    ? `Dismiss ${personName}'s face in this photo only?`
    : "Dismiss this face in this photo only?";
  const ok = await appConfirm(
    label,
    "Other photos with the same person are not affected.",
    { okLabel: "Dismiss", okClass: "danger" },
  );
  if (!ok) return;
  try {
    await apiFetch("/api/v1/faces/reassign", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ face_id: faceId, cluster_id: CLUSTER_DISMISSED }),
    });
  } catch (err) {
    toastError("dismiss the face", err);
    return;
  }
  toast("Face dismissed");
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
    updateLightboxFaces(items[win.lightboxIdx]);
  }
}

export function _lbGetFaceContainer() {
  let c = document.getElementById("lb-face-container");
  if (c) return c;
  const wrapper = document.querySelector(".lb-img-wrapper");
  if (!wrapper) return null;
  c = document.createElement("div");
  c.id = "lb-face-container";
  c.className = "lb-face-container";
  wrapper.appendChild(c);
  return c;
}

/**
 * @param {any[]} faces
 * @param {string} thumbHash
 */
export function _lbRenderFaceOverlays(faces, thumbHash) {
  /** @type {any} */
  const win = window;
  document.querySelectorAll(".lb-face-overlay").forEach((el) => el.remove());

  const container = _lbGetFaceContainer();
  const img = document.getElementById("lb-img");
  if (!container || !img) return;
  const personDisplayName = win.personDisplayName || (() => null);

  for (const f of faces) {
    if (!f.bbox_pct) continue;
    if (f.cluster_id === CLUSTER_DISMISSED) continue;
    const box = document.createElement("div");
    box.className = "lb-face-overlay";
    if (f.cluster_id !== null && f.cluster_id >= 0) {
      box.classList.add("lb-face-overlay-assigned");
    }
    box.style.left = f.bbox_pct.x + "%";
    box.style.top = f.bbox_pct.y + "%";
    box.style.width = f.bbox_pct.w + "%";
    box.style.height = f.bbox_pct.h + "%";
    box.dataset.faceId = f.face_id;
    box.dataset.faceIndex = f.face_index;
    box.dataset.clusterId = String(f.cluster_id ?? CLUSTER_UNASSIGNED);
    // Route right-click through the global contextmenu dispatcher so
    // it finds *this* element first (closest()) instead of the outer
    // lb-img-wrapper, whose data-oncontextmenu would otherwise win
    // (capture-phase dispatcher walks up from e.target). Passing
    // face_id + thumb_hash via data-arg* lets the bridge fn reconstruct
    // the face object without re-fetching.
    box.setAttribute("data-oncontextmenu", "_lbFaceOverlayCtx");
    box.dataset.arg0 = String(f.face_id);
    box.dataset.arg1 = thumbHash;

    const label = document.createElement("span");
    label.className = "lb-face-overlay-label";
    if (f.cluster_id !== null && f.cluster_id >= 0) {
      label.textContent =
        f.name || personDisplayName(f.cluster_id) || `Person ${f.cluster_id + 1}`;
    } else {
      label.textContent = "Unknown";
    }
    box.appendChild(label);
    // Click → enter edit mode (8 handles, draggable). Right-click still
    // opens the person picker. While already in edit mode, mousedown on
    // the box body starts a new move-drag (otherwise the user would be
    // stuck — handles only resize).
    box.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      if (box.classList.contains("lb-face-overlay-busy")) return;
      if (/** @type {HTMLElement} */ (e.target).classList.contains("lb-face-handle")) return;
      if (box.classList.contains("lb-face-overlay-editing")) {
        e.preventDefault();
        e.stopPropagation();
        _lbStartDrag(/** @type {MouseEvent} */ (e), "move");
        return;
      }
      e.stopPropagation();
      _lbBeginFaceEdit(f, thumbHash, box, e);
    });

    container.appendChild(box);
  }
}

// ── Face bbox editor — lives in lightbox-face-edit.mjs ────────────


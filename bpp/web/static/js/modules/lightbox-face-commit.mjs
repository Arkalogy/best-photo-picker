// @ts-check
/**
 * The steps of committing a dragged face outline (`_lbCommitBboxUpdate`
 * in lightbox-face-edit.mjs): the request, the on-error revert, the
 * on-success overlay update and the toasts. Split out for the 500-LOC
 * cap. None of these touch the edit state; the caller ends the edit.
 */

import { toast } from "./toast.mjs";
import { loadAlbumList } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { CLUSTER_UNASSIGNED } from "./constants.mjs";
import { _lbShowFaceAssignPicker, updateLightboxFaces } from "./lightbox.mjs";
import { applyBboxStyle } from "./lightbox-face-overlays.mjs";
import { personDisplayName } from "./people.mjs";

/**
 * The request for the new outline. A placeholder ("Add face" flow, no
 * face_id yet) creates the face with the chosen (or new) person; a real
 * face updates its bbox. (The caller sends it: the mutating call and
 * its error toast stay in one handler.)
 * @param {any} face
 * @param {string} thumbHash
 * @param {{x: number, y: number, w: number, h: number}} bboxPct
 * @returns {{url: string, body: any}}
 */
export function bboxChangeRequest(face, thumbHash, bboxPct) {
  if (face.face_id == null) {
    // New-person mode sends `new_person_name` and lets the server
    // allocate cluster_id + create the album atomically. Existing-
    // person mode sends `cluster_id`.
    const body = face._isNewPerson
      ? { path_hash: thumbHash, new_person_name: face._newPersonName, bbox_pct: bboxPct }
      : { path_hash: thumbHash, cluster_id: face.cluster_id, bbox_pct: bboxPct };
    return { url: "/api/v1/faces/create", body };
  }
  return { url: "/api/v1/faces/update-bbox", body: { face_id: face.face_id, bbox_pct: bboxPct } };
}

/**
 * The request failed: a placeholder is discarded (the user can retry
 * "Add face…" from the menu), a real face snaps back to its pre-drag
 * position so the user can see the update was rejected. The caller
 * toasts.
 * @param {any} face
 * @param {HTMLElement} box
 */
export function revertBboxOnError(face, box) {
  if (face.face_id == null) {
    box.remove();
    return;
  }
  if (face.bbox_pct) applyBboxStyle(box, face.bbox_pct);
}

/**
 * A placeholder was created server-side: drop the synthetic overlay
 * (updateLightboxFaces renders the real one) and say so.
 * @param {any} face
 * @param {any} resp
 */
export async function finishPlaceholder(face, resp) {
  if (face._isNewPerson) {
    // Server already minted the cluster + album; refresh local copies
    // so the new person shows up in pickers and the sidebar.
    await loadAlbumList();
    await loadFaceClusters();
    toast("Face added — created person “" + (resp.person_name || face._newPersonName) + "”.");
  } else {
    toast("Face added — " + (resp.person_name || face.name || "tagged"));
  }
  refreshOpenLightboxFaces();
}

/**
 * Apply the server's answer to the overlay: the (possibly corrected)
 * outline, the cluster and the label.
 * @param {any} face
 * @param {HTMLElement} box
 * @param {any} resp
 */
export function applyCommittedBbox(face, box, resp) {
  // Apply server-corrected bbox (YuNet may have shifted it).
  if (resp.bbox_pct) {
    face.bbox_pct = resp.bbox_pct;
    applyBboxStyle(box, resp.bbox_pct);
  }
  face.cluster_id = resp.cluster_id;
  face.name = resp.person_name || null;
  box.dataset.clusterId = String(resp.cluster_id ?? CLUSTER_UNASSIGNED);

  const label = box.querySelector(".lb-face-overlay-label");
  if (label) label.textContent = resp.matched ? _who(resp) : "Unknown";
  box.classList.toggle("lb-face-overlay-assigned", !!resp.matched);
}

/**
 * The toast after a committed outline. An unassigned face offers the
 * Label action so the user can claim identity in one click; identity
 * is never auto-assigned by this endpoint.
 * @param {any} resp
 * @param {any} face
 * @param {string} thumbHash
 */
export function announceCommittedBbox(resp, face, thumbHash) {
  if (resp.matched) {
    toast(`Face outline updated — still tagged as ${_who(resp)}.`);
    return;
  }
  toast("Face outline updated — still unidentified.", null, {
    action: {
      label: "Label",
      fn: () => {
        const syntheticEvent = /** @type {any} */ ({
          stopPropagation: () => {},
          preventDefault: () => {},
        });
        _lbShowFaceAssignPicker(syntheticEvent, face, thumbHash);
      },
    },
  });
}

/** @param {any} resp */
function _who(resp) {
  return resp.person_name || personDisplayName(resp.cluster_id) || "Person " + (resp.cluster_id + 1);
}

/** Refresh the faces strip below the open photo — counts/labels may have changed. */
export function refreshOpenLightboxFaces() {
  /** @type {any} */
  const win = window;
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
    updateLightboxFaces(items[win.lightboxIdx]);
  }
}

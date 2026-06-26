// @ts-check
/**
 * Lightbox face-edit flow: drag/resize an existing face bbox.
 *
 * Extracted from lightbox.mjs during the v0.1 cleanup. This module
 * owns the edit state (`_lbEdit` + four document-level handler
 * references) and the helpers that drive the in-place bbox edit:
 *
 *   1. user right-clicks a face overlay or uses the inline tools
 *   2. _lbBeginFaceEdit enters edit mode (overlay turns into a
 *      draggable+resizable rect)
 *   3. mouse drags fire _lbOnDrag which updates the live bbox
 *   4. on mouseup, _lbOnDragEnd → _lbCommitBboxUpdate posts the
 *      new bbox to /api/v1/faces/update-bbox
 *   5. _lbEndFaceEdit tears down the document-level listeners
 *
 * The picker that opens after commit (_lbShowFaceAssignPicker) and
 * the face-container DOM helper (_lbGetFaceContainer) stay in
 * lightbox.mjs and are imported back here. ES modules support
 * the circular import because all references are functions
 * resolved at call time.
 */

import { apiFetch } from "./api-client.mjs";
import { toast, toastError } from "./toast.mjs";
import { loadAlbumList } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { CLUSTER_DISMISSED, CLUSTER_UNASSIGNED } from "./constants.mjs";
import { _lbGetFaceContainer, _lbShowFaceAssignPicker, updateLightboxFaces } from "./lightbox.mjs";

/** @type {any} */
export let _lbEdit = null;
/** @type {any} */
let _lbEditKeyHandler = null;
/** @type {any} */
let _lbEditOutsideClick = null;
/** @type {any} */
let _lbEditMouseMove = null;
/** @type {any} */
let _lbEditMouseUp = null;


export function _lbReadBoxPct(box) {
  return {
    x: parseFloat(box.style.left) || 0,
    y: parseFloat(box.style.top) || 0,
    w: parseFloat(box.style.width) || 0,
    h: parseFloat(box.style.height) || 0,
  };
}

/**
 * @param {any} face
 * @param {string} thumbHash
 * @param {HTMLElement} box
 * @param {MouseEvent | null} initialEvent  null = enter edit mode without auto-drag (used by "Add face" placeholder)
 */
export function _lbBeginFaceEdit(face, thumbHash, box, initialEvent) {
  _lbEndFaceEdit(); // cancel any prior edit

  const container = _lbGetFaceContainer();
  if (!container) return;

  box.classList.add("lb-face-overlay-editing");

  const corners = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
  for (const c of corners) {
    const h = document.createElement("div");
    h.className = "lb-face-handle " + c;
    h.dataset.handle = c;
    h.addEventListener("mousedown", (e) => {
      e.preventDefault();
      e.stopPropagation();
      _lbStartDrag(/** @type {MouseEvent} */ (e), "resize-" + c);
    });
    box.appendChild(h);
  }

  _lbEdit = {
    box,
    face,
    thumbHash,
    container,
    startBbox: _lbReadBoxPct(box),
    mode: null,
    startMouse: null,
  };

  _lbEditKeyHandler = (e) => {
    if (e.key === "Escape") {
      // Capture phase + stopImmediatePropagation: the lightbox's own
      // keydown handler is also on document (bubble phase, registered
      // at module init), so it would fire first and close the lightbox.
      // We need to intercept Esc before it gets there.
      e.stopImmediatePropagation();
      e.preventDefault();
      _lbEndFaceEdit();
    }
  };
  document.addEventListener("keydown", _lbEditKeyHandler, true);

  _lbEditOutsideClick = (e) => {
    if (!_lbEdit) return;
    const t = /** @type {Node} */ (e.target);
    if (_lbEdit.box.contains(t)) return;
    // Add-Face placeholder has a Save/Cancel toolbar that lives outside
    // the bbox. Treat clicks inside it as in-edit-mode, not "click away".
    const toolbar = document.getElementById("lb-add-face-toolbar");
    if (toolbar && toolbar.contains(t)) return;
    _lbEndFaceEdit();
  };
  document.addEventListener("mousedown", _lbEditOutsideClick, true);

  // Treat the initial mousedown as the start of a move drag. For the
  // "Add face" placeholder there's no initiating click — the user picks
  // the person first, then the placeholder appears in edit mode. They
  // grab the box themselves via the box mousedown handler.
  if (initialEvent) _lbStartDrag(initialEvent, "move");
}

/**
 * @param {MouseEvent} e
 * @param {string} mode
 */
export function _lbStartDrag(e, mode) {
  if (!_lbEdit) return;
  _lbEdit.mode = mode;
  _lbEdit.startMouse = { x: e.clientX, y: e.clientY };
  _lbEdit.startBbox = _lbReadBoxPct(_lbEdit.box);
  _lbEditMouseMove = (ev) => _lbOnDrag(ev);
  _lbEditMouseUp = (ev) => _lbOnDragEnd(ev);
  document.addEventListener("mousemove", _lbEditMouseMove);
  document.addEventListener("mouseup", _lbEditMouseUp);
}

/**
 * Pure helper: given a starting bbox in percent-space, a mouse delta in
 * percent-space, and a drag mode (move or resize-<corner>), return the
 * new clamped bbox. Exported for unit tests.
 *
 * @param {{x: number, y: number, w: number, h: number}} startBbox
 * @param {number} dxPct
 * @param {number} dyPct
 * @param {string} mode  "move" or "resize-{nw,n,ne,e,se,s,sw,w}"
 * @param {number} [minPct]  minimum width/height, default 3
 * @returns {{x: number, y: number, w: number, h: number}}
 */
export function _lbComputeNewBbox(startBbox, dxPct, dyPct, mode, minPct = 3) {
  const s = startBbox;
  let x = s.x;
  let y = s.y;
  let w = s.w;
  let h = s.h;
  if (mode === "move") {
    x += dxPct;
    y += dyPct;
  } else if (mode === "resize-nw") {
    x += dxPct;
    y += dyPct;
    w -= dxPct;
    h -= dyPct;
  } else if (mode === "resize-n") {
    y += dyPct;
    h -= dyPct;
  } else if (mode === "resize-ne") {
    y += dyPct;
    w += dxPct;
    h -= dyPct;
  } else if (mode === "resize-e") {
    w += dxPct;
  } else if (mode === "resize-se") {
    w += dxPct;
    h += dyPct;
  } else if (mode === "resize-s") {
    h += dyPct;
  } else if (mode === "resize-sw") {
    x += dxPct;
    w -= dxPct;
    h += dyPct;
  } else if (mode === "resize-w") {
    x += dxPct;
    w -= dxPct;
  }

  // Min size — don't allow the box to collapse below minPct.
  if (w < minPct) {
    if (mode.includes("w")) x = s.x + s.w - minPct;
    w = minPct;
  }
  if (h < minPct) {
    if (mode.includes("n")) y = s.y + s.h - minPct;
    h = minPct;
  }
  // For "move", clamp position so the box stays inside [0, 100] without
  // shrinking. For resize modes, leave the box's far edge anchored: when
  // the dragged edge goes off-screen, shrink instead.
  if (mode === "move") {
    if (x < 0) x = 0;
    if (y < 0) y = 0;
    if (x + w > 100) x = 100 - w;
    if (y + h > 100) y = 100 - h;
  } else {
    if (x < 0) {
      w += x;
      x = 0;
    }
    if (y < 0) {
      h += y;
      y = 0;
    }
    if (x + w > 100) w = 100 - x;
    if (y + h > 100) h = 100 - y;
  }
  if (w < minPct) w = minPct;
  if (h < minPct) h = minPct;

  return { x, y, w, h };
}

/**
 * @param {MouseEvent} e
 */
function _lbOnDrag(e) {
  if (!_lbEdit || !_lbEdit.startMouse || !_lbEdit.mode) return;
  const rect = _lbEdit.container.getBoundingClientRect();
  if (!rect.width || !rect.height) return;
  const dxPct = ((e.clientX - _lbEdit.startMouse.x) / rect.width) * 100;
  const dyPct = ((e.clientY - _lbEdit.startMouse.y) / rect.height) * 100;
  const next = _lbComputeNewBbox(_lbEdit.startBbox, dxPct, dyPct, _lbEdit.mode);
  _lbEdit.box.style.left = next.x + "%";
  _lbEdit.box.style.top = next.y + "%";
  _lbEdit.box.style.width = next.w + "%";
  _lbEdit.box.style.height = next.h + "%";
}

/**
 * @param {MouseEvent} _e
 */
async function _lbOnDragEnd(_e) {
  if (!_lbEdit) return;
  if (_lbEditMouseMove) document.removeEventListener("mousemove", _lbEditMouseMove);
  if (_lbEditMouseUp) document.removeEventListener("mouseup", _lbEditMouseUp);
  _lbEditMouseMove = null;
  _lbEditMouseUp = null;
  const finalBbox = _lbReadBoxPct(_lbEdit.box);
  const s = _lbEdit.startBbox;
  const moved =
    Math.abs(finalBbox.x - s.x) > 0.1 ||
    Math.abs(finalBbox.y - s.y) > 0.1 ||
    Math.abs(finalBbox.w - s.w) > 0.1 ||
    Math.abs(finalBbox.h - s.h) > 0.1;
  _lbEdit.mode = null;
  _lbEdit.startMouse = null;
  if (!moved) return; // no commit; stay in edit mode so user can retry
  // Add-Face placeholders defer commit until the user explicitly hits
  // Save. Otherwise the first imperfect drop commits a malformed bbox
  // and the user just sees 422 "no face detected" with nothing to redo.
  // Resize-existing flows still commit on drop — identity is sticky
  // there, so the worst case is a geometry-only update.
  if (_lbEdit.face && _lbEdit.face.face_id == null) {
    // Update startBbox so the next drag starts from the new position.
    _lbEdit.startBbox = finalBbox;
    return;
  }
  await _lbCommitBboxUpdate(finalBbox);
}

/**
 * @param {{x: number, y: number, w: number, h: number}} bboxPct
 */
export async function _lbCommitBboxUpdate(bboxPct) {
  if (!_lbEdit) return;
  const face = _lbEdit.face;
  const box = _lbEdit.box;
  const thumbHash = _lbEdit.thumbHash;
  // Placeholder = "Add face" flow. No face_id yet; POST /create with
  // the chosen cluster_id. Real face = update-bbox flow.
  const isPlaceholder = face.face_id == null;

  box.classList.remove("lb-face-overlay-editing");
  box.classList.remove("lb-face-overlay-placeholder");
  box.querySelectorAll(".lb-face-handle").forEach((el) => el.remove());
  box.classList.add("lb-face-overlay-busy");

  /** @type {any} */
  let resp;
  try {
    if (isPlaceholder) {
      // New-person mode sends `new_person_name` and lets the server
      // allocate cluster_id + create the album atomically. Existing-
      // person mode sends `cluster_id`.
      const body = face._isNewPerson
        ? {
            path_hash: thumbHash,
            new_person_name: face._newPersonName,
            bbox_pct: bboxPct,
          }
        : {
            path_hash: thumbHash,
            cluster_id: face.cluster_id,
            bbox_pct: bboxPct,
          };
      resp = await apiFetch("/api/v1/faces/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } else {
      resp = await apiFetch("/api/v1/faces/update-bbox", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ face_id: face.face_id, bbox_pct: bboxPct }),
      });
    }
  } catch (err) {
    box.classList.remove("lb-face-overlay-busy");
    const e = /** @type {Error & {status?: number}} */ (err);
    if (isPlaceholder) {
      // Placeholder POST failed — discard the synthetic overlay
      // entirely. The user can retry "Add face…" from the menu.
      box.remove();
      _lbEndFaceEdit();
      // Server explains 422 (YuNet couldn't confirm a face) and 409
      // (duplicate guard tripped — message names the existing person).
      // Show those verbatim; treat everything else as transport noise.
      const serverExplained = e.status === 422 || e.status === 409;
      toast(
        serverExplained ? e.message : "Failed to add face: " + (e.message || "unknown"),
        true,
      );
      return;
    }
    // Update-bbox failure: snap the visual bbox back to its pre-drag
    // position so the user can see the update was rejected.
    if (face.bbox_pct) {
      box.style.left = face.bbox_pct.x + "%";
      box.style.top = face.bbox_pct.y + "%";
      box.style.width = face.bbox_pct.w + "%";
      box.style.height = face.bbox_pct.h + "%";
    }
    // 422 = server explained the rejection (e.g. "No face detected…"),
    // show verbatim. Anything else = transport/server failure.
    if (e.status === 422) {
      toast(e.message, true);
    } else {
      toastError("update the face", e);
    }
    _lbEndFaceEdit();
    return;
  }

  box.classList.remove("lb-face-overlay-busy");

  if (isPlaceholder) {
    // Drop the synthetic placeholder; updateLightboxFaces will fetch
    // the new face from the server and render a real overlay.
    box.remove();
    _lbEndFaceEdit();
    /** @type {any} */
    const win = window;
    if (face._isNewPerson) {
      // Server already minted the cluster + album; refresh local copies
      // so the new person shows up in pickers and the sidebar.
      await loadAlbumList();
      await loadFaceClusters();
      toast("Face added — created person “" + (resp.person_name || face._newPersonName) + "”.");
    } else {
      toast("Face added — " + (resp.person_name || face.name || "tagged"));
    }
    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
      updateLightboxFaces(items[win.lightboxIdx]);
    }
    return;
  }

  // Apply server-corrected bbox (YuNet may have shifted it).
  if (resp.bbox_pct) {
    face.bbox_pct = resp.bbox_pct;
    box.style.left = resp.bbox_pct.x + "%";
    box.style.top = resp.bbox_pct.y + "%";
    box.style.width = resp.bbox_pct.w + "%";
    box.style.height = resp.bbox_pct.h + "%";
  }
  face.cluster_id = resp.cluster_id;
  face.name = resp.person_name || null;
  box.dataset.clusterId = String(resp.cluster_id ?? CLUSTER_UNASSIGNED);

  /** @type {any} */
  const win = window;
  const personDisplayName = win.personDisplayName || (() => null);
  const label = box.querySelector(".lb-face-overlay-label");
  if (label) {
    if (resp.matched) {
      label.textContent =
        resp.person_name ||
        personDisplayName(resp.cluster_id) ||
        "Person " + (resp.cluster_id + 1);
    } else {
      label.textContent = "Unknown";
    }
  }
  if (resp.matched) {
    box.classList.add("lb-face-overlay-assigned");
  } else {
    box.classList.remove("lb-face-overlay-assigned");
  }

  _lbEndFaceEdit();

  if (resp.matched) {
    const who =
      resp.person_name ||
      personDisplayName(resp.cluster_id) ||
      "Person " + (resp.cluster_id + 1);
    toast(`Face outline updated — still tagged as ${who}.`);
  } else {
    // Unassigned face — surface the Label action so the user can claim
    // identity in one click. Identity is never auto-assigned by this
    // endpoint; use Label or the Reassign ctx-menu action explicitly.
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

  // Refresh the faces strip below the image — counts/labels may have changed.
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx >= 0 && win.lightboxIdx < items.length) {
    updateLightboxFaces(items[win.lightboxIdx]);
  }
}

export function _lbEndFaceEdit() {
  if (!_lbEdit) return;
  const box = _lbEdit.box;
  const isPlaceholder = _lbEdit.face && _lbEdit.face.face_id == null;
  box.classList.remove("lb-face-overlay-editing");
  box.classList.remove("lb-face-overlay-busy");
  box.querySelectorAll(".lb-face-handle").forEach((el) => el.remove());
  // Discard "Add face" placeholders when edit mode ends without a
  // commit — they have no face_id, so leaving them in the DOM would
  // be a dangling synthetic element. Also drop the Save/Cancel toolbar.
  if (isPlaceholder && box.classList.contains("lb-face-overlay-placeholder")) {
    box.remove();
  }
  document.getElementById("lb-add-face-toolbar")?.remove();

  if (_lbEditKeyHandler) {
    document.removeEventListener("keydown", _lbEditKeyHandler, true);
    _lbEditKeyHandler = null;
  }
  if (_lbEditOutsideClick) {
    document.removeEventListener("mousedown", _lbEditOutsideClick, true);
    _lbEditOutsideClick = null;
  }
  if (_lbEditMouseMove) {
    document.removeEventListener("mousemove", _lbEditMouseMove);
    _lbEditMouseMove = null;
  }
  if (_lbEditMouseUp) {
    document.removeEventListener("mouseup", _lbEditMouseUp);
    _lbEditMouseUp = null;
  }
  _lbEdit = null;
}

// ── "Add face" flow ──────────────────────────────────────────────
// User picks a person, then places + sizes a bbox over an undetected
// ── (orphan JSDoc header stripped during the split — its target
//    `_lbBeginAddFace` lives in lightbox.mjs.)

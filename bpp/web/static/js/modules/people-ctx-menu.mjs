// @ts-check
/**
 * Person context menu — the shared right-click menu used by the
 * sidebar, the People view, and the lightbox.
 *
 * Extracted from people.mjs during the v0.1 cleanup. Owns the
 * ctxClusterId / ctxSource / ctxFaceContext state plus:
 *
 *   * showPersonCtxMenu — populate + position the menu
 *   * hidePersonCtxMenu — tear it down
 *   * initPersonCtxMenu — once-on-load wiring
 *
 * Re-exported from people.mjs so data-action handlers reach the
 * menu off window.
 */

import { state } from "./state.mjs";
import { appConfirm } from "./dialogs.mjs";
import { refreshLightboxIfOpen } from "./lightbox.mjs";
import {
  dismissPerson,
  excludePerson,
  includePerson,
  isClusterExcluded,
  notAFaceCluster,
  personDisplayName,
  showAvatarPicker,
  showMergePicker,
  showSplitPicker,
  startNavFaceRename,
  startPersonRename,
  startPersonRenameLightbox,
  _selectedPeople,
  getPersonAlbumId,
  mergeSelected,
} from "./people.mjs";
import { CLUSTER_DISMISSED } from "./constants.mjs";

let ctxClusterId = null;
let ctxSource = null; // "sidebar" | "people" | "lightbox"
let ctxFaceContext = null;


// state.mergeSourceId / state._dismissedCount / state._dismissedFaces declared on window in globals.js

/**
 * @param {MouseEvent} e
 * @param {number} clusterId
 * @param {{ faceId: number, thumbHash: string } | null} [faceContext]  When the
 *   menu is invoked from a specific face overlay, include face_id +
 *   thumb_hash so the face-scoped items ("Reassign this face to…",
 *   "Dismiss this face") can fire on the right face_embedding row.
 */
export function showPersonCtxMenu(e, clusterId, faceContext) {
  e.preventDefault();
  e.stopPropagation();
  ctxClusterId = clusterId;
  ctxFaceContext = faceContext || null;
  // Detect source context
  const target = /** @type {HTMLElement} */ (e.target);
  if (target.closest(".sidebar, .nav-folder")) ctxSource = "sidebar";
  else if (target.closest("#people-view, .people-grid")) ctxSource = "people";
  else ctxSource = "lightbox";
  const menu = document.getElementById("person-ctx-menu");
  // Show/hide face-scoped items based on whether a specific face was clicked.
  menu.classList.toggle("with-face", !!ctxFaceContext);
  // Context-aware exclude/include label
  const cluster = state.faceClusters.find(c => c.cluster_id === clusterId);
  const excluded = cluster && isClusterExcluded(cluster);
  const toggleItem = /** @type {HTMLElement|null} */ (
    menu.querySelector('[data-action="toggle-exclude"]')
  );
  if (toggleItem) {
    toggleItem.innerHTML = (excluded ? "Include in picks" : "Exclude from picks") + ' <span class="ctx-shortcut">X</span>';
  const boostItem = menu.querySelector('[data-action="toggle-boost"]');
  if (boostItem) {
    const boosted = /** @type {any} */ (window).selectedFaceIds?.has(clusterId);
    boostItem.innerHTML = (boosted ? "Stop boosting" : "Boost in picks") + ' <span class="ctx-shortcut">B</span>';
  }
    toggleItem.title = excluded ? "Re-include this person's photos in picks" : "Keep in face lists but never pick their photos";
  }
  // Show/hide "Merge selected into this" option
  let mergeSelItem = /** @type {HTMLElement|null} */ (
    menu.querySelector('[data-action="merge-selected"]')
  );
  if (!mergeSelItem) {
    mergeSelItem = document.createElement("div");
    mergeSelItem.className = "ctx-menu-item";
    mergeSelItem.dataset.action = "merge-selected";
    menu.insertBefore(mergeSelItem, menu.querySelector('[data-action="merge"]'));
  }
  // Count the OTHER selected people — the ones that would be absorbed into
  // the right-clicked target. mergeSelected() drops the target from the set,
  // so the right-clicked person being selected is fine (it stays as target).
  // Previously this only showed when the target was NOT selected, which hid
  // the bulk merge exactly when the user right-clicked one of their chips.
  const selCount = _selectedPeople.size;
  const othersCount = selCount - (_selectedPeople.has(clusterId) ? 1 : 0);
  if (othersCount >= 1) {
    const targetName = personDisplayName(clusterId) || `Person ${clusterId + 1}`;
    mergeSelItem.textContent = `Merge ${othersCount} selected into "${targetName}"`;
    mergeSelItem.title = "Combine all selected people into this person";
    mergeSelItem.style.display = "block";
  } else {
    mergeSelItem.style.display = "none";
  }

  menu.style.left = e.clientX + "px";
  menu.style.top = e.clientY + "px";
  menu.classList.remove("hidden");
  // Keep menu in viewport
  requestAnimationFrame(() => {
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) menu.style.left = (window.innerWidth - rect.width - 8) + "px";
    if (rect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - rect.height - 8) + "px";
  });
}

export function hidePersonCtxMenu() {
  const menu = document.getElementById("person-ctx-menu");
  menu.classList.add("hidden");
  menu.classList.remove("with-face");
  ctxClusterId = null;
  ctxFaceContext = null;
}

const _PERSON_CTX_KEYS = {
  r: "rename", a: "change-avatar", m: "merge", s: "split",
  x: "toggle-exclude", n: "not-a-face", d: "dismiss",
  // Face-scoped rows (visible only from a face overlay). The click()
  // they trigger is guarded on faceCtx, so the keys are safe no-ops
  // when the menu was opened from a person card instead.
  t: "reassign", p: "dismiss-face",
  b: "toggle-boost",
};

export function initPersonCtxMenu() {
  document.addEventListener("click", () => hidePersonCtxMenu());

  // Single-key shortcuts when context menu is open
  document.addEventListener("keydown", (e) => {
    const menu = document.getElementById("person-ctx-menu");
    if (menu.classList.contains("hidden") || ctxClusterId === null) return;
    const action = _PERSON_CTX_KEYS[e.key.toLowerCase()];
    if (action) {
      e.preventDefault();
      /** @type {HTMLElement|null} */ (menu.querySelector(`[data-action="${action}"]`))?.click();
    } else if (e.key === "Escape") {
      hidePersonCtxMenu();
    }
  });


  document.getElementById("person-ctx-menu").addEventListener("click", (e) => {
    const item = /** @type {HTMLElement|null} */ (
      /** @type {HTMLElement} */ (e.target).closest(".ctx-menu-item")
    );
    if (!item || ctxClusterId === null) return;
    const action = item.dataset.action;
    const cid = ctxClusterId;
    // Snapshot face context before hidePersonCtxMenu clears it.
    const faceCtx = ctxFaceContext;
    hidePersonCtxMenu();

    // Face-scoped actions — only valid when invoked from a face overlay.
    if (action === "reassign") {
      if (!faceCtx) return;
      // Open the existing avatar-list assign picker as a sub-flow.
      // Reconstruct a minimal face object from _lbFaceData (which holds
      // the current photo's faces) — bridged on window by lightbox.mjs.
      const cluster = state.faceClusters.find(c => c.cluster_id === cid);
      const fakeFace = {
        face_id: faceCtx.faceId,
        face_index: cluster?.representative?.face_index ?? 0,
        cluster_id: cid,
        name: personDisplayName(cid) || null,
      };
      /** @type {any} */ (window)._lbShowFaceAssignPicker?.(e, fakeFace, faceCtx.thumbHash);
      return;
    }
    if (action === "toggle-boost") {
      // Same selection set the sidebar chips use — toggleFace re-renders
      // the chip strip + schedules a recompute; re-render the people grid
      // so the card's boost ring updates too.
      /** @type {any} */ (window).toggleFace?.(cid);
      /** @type {any} */ (window).showPeopleView?.();
      return;
    }
    if (action === "dismiss-face") {
      if (!faceCtx) return;
      const personName = personDisplayName(cid) || `Person ${cid + 1}`;
      /** @type {any} */ (window).appConfirm?.(
        `Dismiss ${personName}'s face in this photo only?`,
        "Other photos with the same person are not affected.",
        { okLabel: "Dismiss", okClass: "danger" },
      ).then((ok) => {
        if (!ok) return;
        /** @type {any} */ (window)._lbReassignFace?.(faceCtx.faceId, CLUSTER_DISMISSED);
      });
      return;
    }

    if (action === "rename") {
      if (ctxSource === "sidebar") {
        const albumId = getPersonAlbumId(cid);
        const navItem = albumId && document.querySelector(`.nav-item[data-album-id="${albumId}"] .nav-face-name`);
        if (navItem) startNavFaceRename(cid, navItem);
      } else if (ctxSource === "people") {
        const label = document.getElementById(`person-label-${cid}`);
        if (label) startPersonRename(cid, label);
      } else {
        startPersonRenameLightbox(cid);
      }
    } else if (action === "merge") {
      showMergePicker(cid);
    } else if (action === "toggle-exclude") {
      const cluster = state.faceClusters.find(c => c.cluster_id === cid);
      if (cluster && isClusterExcluded(cluster)) {
        includePerson(cid).then(refreshLightboxIfOpen);
      } else {
        excludePerson(cid).then(refreshLightboxIfOpen);
      }
    } else if (action === "change-avatar") {
      showAvatarPicker(cid);
    } else if (action === "merge-selected") {
      mergeSelected(cid);
    } else if (action === "split") {
      showSplitPicker(cid);
    } else if (action === "not-a-face") {
      notAFaceCluster(cid).then(refreshLightboxIfOpen);
    } else if (action === "dismiss") {
      dismissPerson(cid).then(refreshLightboxIfOpen);
    }
  });
}

// ── Faces: split picker modal ──


// @ts-check
/**
 * People (face clusters) view, sidebar context menu, drag-to-merge,
 * multi-select, split picker, merge picker, avatar picker, dismiss/
 * restore flow, exclude/include, "review unnamed" + "review pairs"
 * wizards, person-album action bar.
 *
 * Cross-realm shared state (`mergeSourceId`, `_dismissedCount`,
 * `_dismissedFaces`) lives on `window` (declared in globals.js) so
 * toolbar.mjs's `mergeSourceId` check and faces.mjs's
 * `loadFaceClusters` writes both stay visible to this module.
 *
 * Cross-file callees still classic: `event` (legacy global access in
 * `startPersonRename` — old browser quirk), the shared-state helpers
 * (`state.albumList`, `state.faceClusters`, `state.currentView`, `state.currentAlbumId`, `photos`,
 * `state.multiSelected`, `state.selectedFaceIds`, `state.peopleFilter`, `state.peopleSort`) all
 * on `window` and accessible via the global-object scope-chain
 * fallback. Now `@ts-check`'d (2026-06-17): cross-module globals it
 * relies on are declared in tests-js/types/globals.d.ts.
 */

import { apiFetch } from "./api-client.mjs";
import { state } from "./state.mjs";
import { appConfirm } from "./dialogs.mjs";
import { esc } from "./text-format.mjs";
import { loadAlbumList, renderAlbumNav } from "./albums.mjs";
import { refreshSmartAlbums, renderFaceGallery } from "./faces.mjs";
import { navigateTo } from "./core.mjs";
import { scheduleRecompute } from "./analysis.mjs";
import { toast } from "./toast.mjs";
import { FACE_MIN_PHOTOS } from "./constants.mjs";

export function getPersonName(clusterId) {
  const album = state.albumList.find(a =>
    a.album_type === "smart_person" && a.rule && a.rule.cluster_id === clusterId
  );
  return album ? album.name : null;
}

export function getPersonAlbumId(clusterId) {
  const album = state.albumList.find(a =>
    a.album_type === "smart_person" && a.rule && a.rule.cluster_id === clusterId
  );
  return album ? album.id : null;
}

export function personDisplayName(clusterId) {
  const name = getPersonName(clusterId);
  if (!name || /^Person \d+$/.test(name)) return null;
  return name;
}







// ── Faces: pointer-based drag-and-drop merge ──
// (HTML5 drag doesn't work in Tauri/WKWebView — use pointer events instead)
let _dragState = null; // {clusterId, card, ghost, startX, startY, started}
const DRAG_THRESHOLD = 6; // px before drag starts

let _suppressClick = false; // suppress onclick after drag

export function _personPointerDown(e, clusterId) {
  // Only primary button; ignore modifier keys (selection handled in onclick)
  if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
  // e.currentTarget is `document` in the delegated path — use `this` (the card element)
  const card = (e.currentTarget && e.currentTarget !== document) ? e.currentTarget : this;
  _dragState = {
    clusterId, card, ghost: null,
    startX: e.clientX, startY: e.clientY, started: false,
    pointerId: e.pointerId,
  };
}

export function _personPointerMove(e) {
  if (!_dragState) return;
  const dx = e.clientX - _dragState.startX;
  const dy = e.clientY - _dragState.startY;

  if (!_dragState.started) {
    if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) return;
    _dragState.started = true;
    _dragState.card.classList.add("dragging");
    // Create floating ghost from the avatar image
    const avatar = _dragState.card.querySelector(".person-avatar img");
    const ghost = document.createElement("div");
    ghost.className = "person-drag-ghost";
    if (avatar) {
      const img = document.createElement("img");
      img.src = avatar.src;
      ghost.appendChild(img);
    }
    document.body.appendChild(ghost);
    _dragState.ghost = ghost;
  }

  // Position ghost at pointer
  if (_dragState.ghost) {
    _dragState.ghost.style.left = e.clientX + "px";
    _dragState.ghost.style.top = e.clientY + "px";
  }

  // Hit-test drop target (need to temporarily hide ghost for elementFromPoint)
  if (_dragState.ghost) _dragState.ghost.style.pointerEvents = "none";
  const el = document.elementFromPoint(e.clientX, e.clientY);
  if (_dragState.ghost) _dragState.ghost.style.pointerEvents = "";
  const target = el ? el.closest(".person-card") : null;
  document.querySelectorAll(".person-card.drag-over").forEach(c => c.classList.remove("drag-over"));
  if (target && target !== _dragState.card) {
    target.classList.add("drag-over");
  }
}

export async function _personPointerUp(e) {
  if (!_dragState) return;
  const state = _dragState;
  _dragState = null;

  // Clean up ghost & classes
  if (state.ghost) state.ghost.remove();
  state.card.classList.remove("dragging");
  document.querySelectorAll(".person-card.drag-over").forEach(c => c.classList.remove("drag-over"));

  if (!state.started) return; // was a click, not a drag

  // Suppress the onclick that follows pointerup
  _suppressClick = true;
  requestAnimationFrame(() => { _suppressClick = false; });

  // Find drop target
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const target = /** @type {HTMLElement|null} */ (el ? el.closest(".person-card") : null);
  if (!target || target === state.card) return;
  const targetId = Number(target.dataset.clusterId);
  if (isNaN(targetId) || targetId === state.clusterId) return;

  // If dragged card is part of a selection, merge all selected
  const mergeIds = _selectedPeople.size > 0 && _selectedPeople.has(state.clusterId)
    ? [..._selectedPeople].filter(cid => cid !== targetId)
    : [state.clusterId];
  const targetName = personDisplayName(targetId) || `Person ${targetId + 1}`;
  const label = mergeIds.length === 1
    ? `Merge "${personDisplayName(mergeIds[0]) || `Person ${mergeIds[0] + 1}`}" into "${targetName}"?`
    : `Merge ${mergeIds.length} people into "${targetName}"?`;
  if (!await appConfirm(label, {okLabel: "Merge"})) return;

  clearPersonSelection();
  await doMerge(targetId, mergeIds);
}

// Attach to document so move/up work even when pointer leaves the card
document.addEventListener("pointermove", _personPointerMove);
document.addEventListener("pointerup", _personPointerUp);

// ── Faces: multi-select ──


export function _personCardClick(e, clusterId) {
  if (clusterId === undefined) clusterId = +this.dataset.arg0;
  if (_suppressClick) return;
  if (e.ctrlKey || e.metaKey || e.shiftKey) {
    e.preventDefault();
    e.stopPropagation();
    togglePersonSelect(clusterId, e);
    return;
  }
  // Plain click: if selection exists, clear it
  if (_selectedPeople.size > 0) {
    clearPersonSelection();
    return;
  }
  navigateToPersonAlbum(clusterId);
}

export async function navigateToPersonAlbum(clusterId) {
  if (_suppressClick) return;
  let personAlbum = state.albumList.find(a =>
    a.album_type === "smart_person" && a.rule && a.rule.cluster_id === clusterId
  );
  if (!personAlbum) {
    // Create smart album on demand via refresh, then check again
    await refreshSmartAlbums();
    personAlbum = state.albumList.find(a =>
      a.album_type === "smart_person" && a.rule && a.rule.cluster_id === clusterId
    );
  }
  if (personAlbum) {
    navigateTo('album', personAlbum.id);
  } else {
    // Still no album (cluster too small) — filter library by face
    state.selectedFaceIds.clear();
    state.selectedFaceIds.add(clusterId);
    renderFaceGallery();
    navigateTo('library');
    scheduleRecompute();
  }
}



// people-view.mjs owns the cluster grid render + filter/sort helpers.
// Re-exported so data-action handlers + cross-module callers keep working.
import {
  isClusterExcluded,
  personLabelHTML,
  setPeopleFilter,
  setPeopleSort,
  showPeopleView,
} from "./people-view.mjs";
export {
  isClusterExcluded,
  personLabelHTML,
  setPeopleFilter,
  setPeopleSort,
  showPeopleView,
};

// people-merge.mjs owns multi-select, exclude/include, and the merge
// API call. The _selectedPeople set is also exported from there.
import {
  _selectedPeople,
  _updatePersonSelection,
  clearPersonSelection,
  doMerge,
  excludePerson,
  includePerson,
  mergeSelected,
  togglePersonSelect,
} from "./people-merge.mjs";
export {
  _selectedPeople,
  _updatePersonSelection,
  clearPersonSelection,
  doMerge,
  excludePerson,
  includePerson,
  mergeSelected,
  togglePersonSelect,
};

// Person-album action bar lives in people-album-bar since the v0.1
// split. Re-exported so data-action handlers reach the bar's _paXxx
// handlers off window.
import {
  _getSelectedFaceIds,
  _paDismiss,
  _paMergeWith,
  _paMoveTo,
  _paNewPerson,
  _paNotAFace,
  _paNotAFaceSelected,
  _paNotThisPerson,
  _paRename,
  _paToggleExclude,
  getPersonAlbumClusterId,
  setPersonAlbumClusterId,
  updatePersonAlbumBar,
  updatePersonPhotoSelection,
} from "./people-album-bar.mjs";
export {
  _getSelectedFaceIds,
  _paDismiss,
  _paMergeWith,
  _paMoveTo,
  _paNewPerson,
  _paNotAFace,
  _paNotAFaceSelected,
  _paNotThisPerson,
  _paRename,
  _paToggleExclude,
  updatePersonAlbumBar,
  updatePersonPhotoSelection,
};

// Face review + ambiguous-pair review wizards live in people-review
// since the v0.1 split. Re-exported so data-action handlers reach
// every wizard step off window.
import {
  _closePairReviewOverlay,
  _closeReviewOverlay,
  _pairReviewKeyHandler,
  _pairSkip,
  _pairVerdict,
  _renderPairReviewCard,
  _renderReviewCard,
  _reviewAutocomplete,
  _reviewConfirm,
  _reviewDismiss,
  _reviewInputKey,
  _reviewMergeInto,
  _reviewSelectMerge,
  _reviewShowManual,
  _reviewSkip,
  _showPairReviewOverlay,
  _showReviewOverlay,
  refreshAmbiguousPairCount,
  startFacePairReview,
  getAmbiguousPairCount,
  startFaceReview,
} from "./people-review.mjs";
export {
  _closePairReviewOverlay,
  _closeReviewOverlay,
  _pairReviewKeyHandler,
  _pairSkip,
  _pairVerdict,
  _renderPairReviewCard,
  _renderReviewCard,
  _reviewAutocomplete,
  _reviewConfirm,
  _reviewDismiss,
  _reviewInputKey,
  _reviewMergeInto,
  _reviewSelectMerge,
  _reviewShowManual,
  _reviewSkip,
  _showPairReviewOverlay,
  _showReviewOverlay,
  refreshAmbiguousPairCount,
  startFacePairReview,
  getAmbiguousPairCount,
  startFaceReview,
};

import {
  closeMergePicker,
  showAvatarPicker,
  showMergePicker,
  showSplitPicker,
  splitSelectedFaces,
} from "./people-pickers.mjs";
export {
  closeMergePicker,
  showAvatarPicker,
  showMergePicker,
  showSplitPicker,
  splitSelectedFaces,
};

import {
  deleteAllDismissed,
  deleteFacePermanently,
  dismissAllUnnamed,
  dismissPerson,
  expandDismissedSection,
  loadDismissedFaces,
  notAFaceCluster,
  restoreDismissed,
  restoreFace,
} from "./people-actions.mjs";
export {
  deleteAllDismissed,
  deleteFacePermanently,
  dismissAllUnnamed,
  dismissPerson,
  expandDismissedSection,
  loadDismissedFaces,
  notAFaceCluster,
  restoreDismissed,
  restoreFace,
};

import {
  hidePersonCtxMenu,
  initPersonCtxMenu,
  showPersonCtxMenu,
} from "./people-ctx-menu.mjs";
export { hidePersonCtxMenu, initPersonCtxMenu, showPersonCtxMenu };

// Rename + name-autocomplete flows moved to people-rename.mjs (LOC
// gate, 2026-06-12). Re-exported so import paths + the window bridge
// keep working.
export {
  personNameSuggestions,
  attachPersonNameAutocomplete,
  renamePerson,
  startNavFaceRename,
  startPersonRename,
  startPersonRenameLightbox,
} from "./people-rename.mjs";

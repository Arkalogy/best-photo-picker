// @ts-check
/**
 * Lightbox — full-screen photo viewer.
 *
 * Owns: image / video display, EXIF panel, inline Leaflet map, video
 * trim controls, face overlay rendering, face-assign / face-rename /
 * person-dismiss flows, pet chips, action bar (include/exclude/favorite/
 * enhance/edit/delete), undo stack, zoom + pan (mouse wheel / drag /
 * pinch / double-tap), keyboard shortcuts, photo context menu.
 *
 * Cross-realm shared state (`lightboxIdx`, `lbZoom`, `lbPanX`, `lbPanY`,
 * `_lbLeafletMap`, `_lbMapMarker`, `LB_ZOOM_MIN`, `LB_ZOOM_MAX`,
 * `SCORE_LABELS`) lives on `window` (declared in globals.js) so still-
 * classic people.js + the editor + compare modules all share it via the
 * global-object scope-chain fallback.
 *
 * Cross-file callees that stay classic (people.js): `personDisplayName`,
 * `getPersonAlbumId`, `showPersonCtxMenu`, `showPeopleView`,
 * `includePerson`, `excludePerson`, `notAFaceCluster`, `dismissPerson`
 * — all looked up on `window`.
 */

import { apiFetch, authedSrc } from "./api-client.mjs";
import { appConfirm, appPrompt } from "./dialogs.mjs";
import { deletePhotos } from "./deleted.mjs";
import { esc, escapeAttr } from "./text-format.mjs";
import { _formatDuration, updateCardInPlace } from "./photos.mjs";
import { formatDate } from "./date-format.mjs";
import { hideAlbumPicker } from "./photos.mjs";
import { hideBatchRenameModal } from "./batch-rename.mjs";
import { hideExportModal, hideImportModal, hideSettings } from "./modals.mjs";
import { hideLibraryPicker, _isLibraryPickerOpen } from "./library.mjs";
import { hideSearch, isSearchOpen, showSearch } from "./search.mjs";
import { isCompareOpen, openCompareFromSelection } from "./compare.mjs";
import { _isSlideshowActive } from "./slideshow.mjs";
import { loadAlbumList, switchAlbum } from "./albums.mjs";
import { loadFaceClusters } from "./faces.mjs";
import { openEditor, closeEditor } from "./editor.mjs";
import { _iphShowTagPicker } from "./inspector.mjs";
import { qualityLabel } from "./score-format.mjs";
import { resolveModal } from "./modal.mjs";
import { saveOverrides } from "./toolbar.mjs";
import { saveNavState } from "./navigation.mjs";
import { scheduleRecompute } from "./analysis.mjs";
import { setOverride, clearMultiSelect } from "./photos.mjs";
import { showExportModal } from "./modals.mjs";
import { toast } from "./toast.mjs";
import { toggleFavorite } from "./toolbar.mjs";
import { closeWizard } from "./wizard.mjs";
import { CLUSTER_DISMISSED, CLUSTER_UNASSIGNED } from "./constants.mjs";
import { updateLightboxSimilar } from "./clip.mjs";
import { updateLightboxTags } from "./tags.mjs";
import { vgrid } from "./photos.mjs";
// Info-panel surfaces (EXIF + map + video + trim) live in
// lightbox-info since the v0.1 split. Imported back here so
// openLightbox can refresh them, and re-exported below so the
// modules-bridge in index.html still exposes them on window.
import {
  lbTrimPreview,
  toggleLightboxMap,
  updateLightboxExif,
  updateLightboxMap,
  updateLightboxTrim,
  updateLightboxVideoInfo,
} from "./lightbox-info.mjs";
export {
  lbTrimPreview,
  toggleLightboxMap,
  updateLightboxExif,
  updateLightboxMap,
  updateLightboxTrim,
  updateLightboxVideoInfo,
};
// Face-edit flow (drag/resize an existing face bbox + commit) lives
// in lightbox-face-edit since the v0.1 split. Re-exported so any
// data-action handler that resolves the names off window keeps
// working. ES modules support the circular reference because
// lightbox-face-edit imports _lbGetFaceContainer +
// _lbShowFaceAssignPicker from this module at call time, not at
// module init.
import {
  _lbBeginFaceEdit,
  _lbCommitBboxUpdate,
  _lbComputeNewBbox,
  _lbEdit,
  _lbEndFaceEdit,
  _lbReadBoxPct,
  _lbStartDrag,
} from "./lightbox-face-edit.mjs";
export {
  _lbBeginFaceEdit,
  _lbCommitBboxUpdate,
  _lbComputeNewBbox,
  _lbEdit,
  _lbEndFaceEdit,
  _lbReadBoxPct,
  _lbStartDrag,
};
// Face-assign flow (add-face placeholder + person picker + reassign)
// lives in lightbox-face-assign since the v0.1 split. Re-exported so
// data-action handlers that look the names up on window keep working.
import {
  _lbBeginAddFace,
  _lbReassignFace,
  _lbShowFaceAssignPicker,
  _lbTagPersonFromMenu,
  updateLightboxPets,
} from "./lightbox-face-assign.mjs";
export {
  _lbBeginAddFace,
  _lbReassignFace,
  _lbShowFaceAssignPicker,
  _lbTagPersonFromMenu,
  updateLightboxPets,
};
// Action bar / nav / undo / close / context menu live in
// lightbox-actions since the v0.1 split. Imported back for the
// keydown handler and openLightbox's action-bar refresh, and
// re-exported so data-action handlers reach them on window.
import {
  _highlightGalleryCard,
  _lbTogglePanel,
  closeLightbox,
  lbAction,
  lbDelete,
  lbEnhance,
  lbRevertEnhance,
  lbShowInFinder,
  lbToggleFav,
  lbUndo,
  lightboxNav,
  updateLightboxActions,
} from "./lightbox-actions.mjs";
export {
  _highlightGalleryCard,
  _lbTogglePanel,
  closeLightbox,
  lbAction,
  lbDelete,
  lbEnhance,
  lbRevertEnhance,
  lbShowInFinder,
  lbToggleFav,
  lbUndo,
  lightboxNav,
  updateLightboxActions,
};
// Context menu split out of lightbox-actions (2026-06-17, 500-LOC cap).
// Re-exported here so the data-action / data-oncontextmenu bridge is unchanged.
import { hideLbCtxMenu, showLbCtxMenu } from "./lightbox-ctxmenu.mjs";
export { hideLbCtxMenu, showLbCtxMenu };
// Face overlay rendering + face chip strip + face-related ctx menus
// live in lightbox-face-overlays since the v0.1 split.
import {
  _lbDismissDetectedFace,
  _lbFaceOverlayCtx,
  _lbGetFaceContainer,
  _lbOpenPersonAlbum,
  _lbRenderFaceOverlays,
  _lbUntagPerson,
  updateLightboxFaces,
} from "./lightbox-face-overlays.mjs";
export {
  _lbDismissDetectedFace,
  _lbFaceOverlayCtx,
  _lbGetFaceContainer,
  _lbOpenPersonAlbum,
  _lbRenderFaceOverlays,
  _lbUntagPerson,
  updateLightboxFaces,
};
// Zoom + pan state, transform pipeline, wheel/mouse/touch input
// IIFEs live in lightbox-input since the v0.1 split. Imported back
// for the keydown handler + openLightbox; re-exported for the
// modules-bridge in templates/index.html.
import {
  _lbApplyTransform,
  _lbKeyZoom,
  _lbShowZoomIndicator,
  lbResetZoom,
  lbZoomAt,
} from "./lightbox-input.mjs";
export {
  _lbApplyTransform,
  _lbKeyZoom,
  _lbShowZoomIndicator,
  lbResetZoom,
  lbZoomAt,
};

/** @type {HTMLElement[]} */
export let _lbPickerItems = [];

// Defer GPS import to avoid bundling it on every load: pickers/menus that attach document-level listeners
// register their tear-down here so `closeLightbox` can drain
// them on the X-close / outside-click path. Without the registry,
// document.* keydown/click handlers leaked every time the picker
// dismissed via a non-cleanup route (e.g. user clicked the
// lightbox X with the picker still open).
/** @type {Set<() => void>} */
export const _lbActiveCleanups = new Set();

export function lbSwitchTab() {
  /** @type {any} */
  const win = window;
  if (win._lbLeafletMap) {
    setTimeout(() => win._lbLeafletMap.invalidateSize(), 0);
  }
}

// openLightbox + refreshLightboxIfOpen moved to lightbox-open.mjs in
// the v0.1 cleanup. Re-exported so back-compat (window-bridged callers
// in still-classic modules, tests) keeps working.
import { openLightbox, refreshLightboxIfOpen } from "./lightbox-open.mjs";
export { openLightbox, refreshLightboxIfOpen };

// ── Wire keyboard handler + mouse zoom + touch gestures + ctx menu init ──

document.addEventListener("keydown", (e) => {
  /** @type {any} */
  const win = window;
  if (_isSlideshowActive()) return;
  if ((e.metaKey || e.ctrlKey) && e.key === "k") {
    e.preventDefault();
    if (isSearchOpen()) hideSearch();
    else showSearch();
    return;
  }
  if (isSearchOpen()) return;
  if (isCompareOpen()) return;
  const _libPickerOpen = _isLibraryPickerOpen();
  if (_libPickerOpen && e.key === "Escape") {
    hideLibraryPicker();
    return;
  }
  if (_libPickerOpen) return;
  if (e.key === "Escape") {
    // The model-terms acceptance dialog (Settings → Models → Review &
    // accept) stacks ON TOP of the settings overlay and owns ESC while
    // it's up. This handler runs before the dialog's own ESC handler, so
    // without this guard it would close the settings overlay underneath —
    // dropping the user all the way to the home grid instead of back to
    // Settings. Yield and let the dialog's handler close just the dialog.
    if (
      document
        .getElementById("fe-acceptance-overlay")
        ?.classList.contains("visible")
    ) {
      return;
    }
    const cardMenu = document.getElementById("card-ctx-menu");
    if (cardMenu && !cardMenu.classList.contains("hidden")) {
      win.hideCardCtxMenu?.();
      return;
    }
    if (document.getElementById("album-picker-overlay")?.classList.contains("visible")) {
      hideAlbumPicker();
      return;
    }
    const multiSelected = /** @type {Set<string>} */ (win.multiSelected || new Set());
    if (multiSelected.size > 0) {
      clearMultiSelect();
      return;
    }
    if (document.getElementById("settings-overlay")?.classList.contains("visible")) {
      hideSettings();
      return;
    }
    if (document.getElementById("export-modal-overlay")?.classList.contains("visible")) {
      hideExportModal();
      return;
    }
    if (document.getElementById("import-modal-overlay")?.classList.contains("visible")) {
      hideImportModal();
      return;
    }
    if (document.getElementById("modal-overlay")?.classList.contains("visible")) {
      resolveModal(false);
      return;
    }
    if (document.getElementById("wizard-overlay")?.classList.contains("visible")) {
      closeWizard();
      return;
    }
    const renameEl = document.getElementById("rename-overlay");
    if (renameEl && !renameEl.classList.contains("hidden")) {
      hideBatchRenameModal();
      return;
    }
  }
  const items = /** @type {any[]} */ (win.currentGridItems || []);
  if (win.lightboxIdx < 0) {
    const target = /** @type {HTMLElement} */ (e.target);
    if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
    if (e.key === "e" || e.key === "E") {
      showExportModal();
      return;
    }
    if (e.key === "c" || e.key === "C") {
      const ms = /** @type {Set<string>} */ (win.multiSelected || new Set());
      if (ms.size >= 2) {
        e.preventDefault();
        openCompareFromSelection();
        return;
      }
    }
    if (e.key === "/") {
      e.preventDefault();
      showSearch();
      return;
    }
    return;
  }
  if (e.key === "Escape") {
    const ctxMenu = document.getElementById("lb-ctx-menu");
    if (ctxMenu && !ctxMenu.classList.contains("hidden")) {
      hideLbCtxMenu();
      return;
    }
    if (win.editorActive) {
      closeEditor(false);
      return;
    }
    closeLightbox();
    return;
  }
  if (win.editorActive) return;
  const target = /** @type {HTMLElement} */ (e.target);
  if (target.tagName === "INPUT" || target.tagName === "TEXTAREA") return;
  if ((e.metaKey || e.ctrlKey) && e.key === "z") {
    e.preventDefault();
    lbUndo();
    return;
  }
  e.preventDefault();
  if (e.key === "=" || e.key === "+") {
    _lbKeyZoom(1);
    return;
  }
  if (e.key === "-" || e.key === "_") {
    _lbKeyZoom(-1);
    return;
  }
  if (e.key === "0") {
    lbResetZoom();
    _lbShowZoomIndicator();
    return;
  }
  if (e.key === "ArrowRight") {
    lightboxNav(1);
    return;
  }
  if (e.key === "ArrowLeft") {
    lightboxNav(-1);
    return;
  }
  if (e.key === "ArrowUp") {
    lbAction("include");
    return;
  }
  if (e.key === "ArrowDown") {
    lbAction("exclude");
    return;
  }
  if (e.key === "f" || e.key === "F") {
    lbToggleFav();
    return;
  }
  if (e.key === "e" || e.key === "E") {
    openEditor();
    return;
  }
  if (e.key === "a" || e.key === "A") {
    const p = items[win.lightboxIdx];
    if (p && p._enhanced) lbRevertEnhance();
    else lbEnhance();
    return;
  }
  if (e.key === "d" || e.key === "D" || e.key === "Backspace" || e.key === "Delete") {
    lbDelete();
    return;
  }
  if (e.key === "t" || e.key === "T") {
    const p = items[win.lightboxIdx];
    if (p && p.thumb_hash) _lbTagPersonFromMenu(/** @type {any} */ (e), p.thumb_hash);
    return;
  }
  if (e.key === "s" || e.key === "S") {
    lbShowInFinder();
    return;
  }
  if (e.key === "n" || e.key === "N") {
    // New face — outline a face the detector missed (ctx menu: Add face…).
    const p = items[win.lightboxIdx];
    if (p && p.thumb_hash) _lbBeginAddFace(/** @type {any} */ (e), p.thumb_hash);
    return;
  }
  if (e.key === "m" || e.key === "M") {
    // Mark/unmark sensitive (ctx menu: Mark sensitive).
    /** @type {any} */ (window).lbToggleSensitive?.();
    return;
  }
});

// ── Mouse zoom/pan ──

// ── Mobile bottom-sheet init ──
// Wire the drag handle that toggles the info panel between
// collapsed (~30vh) and expanded (~80vh) at phone width. The CSS
// hides the handle on desktop, so this listener is a no-op there
// — but we still wire it unconditionally so a viewport rotation
// (landscape → portrait) doesn't need a re-init.
(() => {
  const handle = document.getElementById("lb-panel-handle");
  if (!handle) return;
  handle.addEventListener("click", _lbTogglePanel);
  // Keyboard a11y — Enter / Space activate the handle for users
  // arriving via tab focus on a phone keyboard.
  handle.addEventListener("keydown", (e) => {
    const ev = /** @type {KeyboardEvent} */ (e);
    if (ev.key === "Enter" || ev.key === " ") {
      ev.preventDefault();
      _lbTogglePanel();
    }
  });
})();

// ── Photo context menu init ──
(() => {
  /** @type {any} */
  const win = window;
  document.addEventListener("click", hideLbCtxMenu);
  const menu = document.getElementById("lb-ctx-menu");
  if (!menu) return;
  menu.addEventListener("click", (e) => {
    const target = /** @type {HTMLElement | null} */ (e.target);
    const item = target?.closest(".ctx-menu-item");
    if (!item || win.lightboxIdx < 0) return;
    const action = /** @type {HTMLElement} */ (item).dataset.action;
    hideLbCtxMenu();

    const items = /** @type {any[]} */ (win.currentGridItems || []);
    if (action === "include") lbAction("include");
    else if (action === "exclude") lbAction("exclude");
    else if (action === "favorite") lbToggleFav();
    else if (action === "edit") openEditor();
    else if (action === "enhance") {
      const p = items[win.lightboxIdx];
      if (p._enhanced) lbRevertEnhance();
      else lbEnhance();
    } else if (action === "tag-person") {
      const p = items[win.lightboxIdx];
      if (p && p.thumb_hash) _lbTagPersonFromMenu(/** @type {any} */ (e), p.thumb_hash);
    } else if (action === "add-face") {
      const p = items[win.lightboxIdx];
      if (p && p.thumb_hash) _lbBeginAddFace(/** @type {any} */ (e), p.thumb_hash);
    } else if (action === "sensitive") {
      /** @type {any} */ (window).lbToggleSensitive?.();
    } else if (action === "show-in-finder") lbShowInFinder();
    else if (action === "delete") lbDelete();
  });
})();

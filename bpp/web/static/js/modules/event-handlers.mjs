// @ts-check
/**
 * The handlers the page's own markup may name for delegated events.
 *
 * globals.js dispatches `data-oninput="name"`, `data-onerror="name"` and
 * the like to the function of that name. Several of these events need
 * no click on the element: an image that fails to load, a `<details>`
 * that opens, a pointer passing over. If markup from an opened library
 * ever reached the page, the same attributes on it would call any page
 * function. So each event honours only the names listed here: the ones
 * the app's own templates use (tests-js/event-handlers.test.mjs keeps
 * the lists equal to what the templates contain).
 *
 * Clicks (`data-action`) are not listed: they go through the action
 * registry or the window bridge, and need the owner to act on the
 * element itself (a click, or Enter / Space while it has the focus).
 * Keeping library values out of the markup is what protects those.
 */

/** @type {Readonly<Record<string, readonly string[]>>} */
export const EVENT_HANDLERS = Object.freeze({
  oninput: [
    "_editorOnZoomSlider",
    "_editorSliderChange",
    "_inpaintSetBrushSize",
    "_onSensitiveThresholdInput",
    "_reviewAutocomplete",
    "albumFilterInput",
    "applyZoom",
    "onTagInput",
    "scheduleRecompute",
    "updateClipThresholdLabel",
    "updateFaceConfidenceLabel",
    "updateFaceEmbedConfLabel",
    "updateFaceThresholdLabel",
    "updateGroupMinPhotosLabel",
    "updateMaxLongSideLabel",
    "updateMinEmbedQualityLabel",
    "updateMinFaceAreaLabel",
    "updatePetConfidenceLabel",
    "updateRenamePreview",
    "updateThumbnailSizeLabel",
    "validateClearConfirm",
    "validateImportModal",
  ],
  onchange: [
    "_editorDateChanged",
    "_onSensitiveThresholdCommit",
    "_onbSetUseContext",
    "_shareToggle",
    "activityFilterChanged",
    "loadPreset",
    "onFilterChange",
    "renderGrid",
    "syncKFromToolbar",
    "toggleCheckUpdates",
    "toggleExportQuality",
    "toggleFollowSymlinks",
    "updateExportScope",
  ],
  onkeydown: [
    "_bppCardKeydown",
    "_kbEnterCreateAlbum",
    "_kbEnterCustomRatio",
    "_kbToggleTip",
    "_reviewInputKey",
    "onTagKeydown",
    "onToolbarKKeydown",
  ],
  onmousedown: ["_showBefore", "pickTagSuggestion"],
  onmouseup: ["_hideBefore"],
  onmouseleave: ["_hideBefore"],
  ontouchstart: ["_showBefore"],
  ontouchend: ["_hideBefore"],
  onpointerdown: ["_personPointerDownDispatch"],
  oncontextmenu: [
    "_bppCardCtxMenu",
    "_lbFaceOverlayCtx",
    "_personCtxMenuDispatch",
    "_tagCardCtxMenu",
    "showAlbumMoveMenu",
    "showCardCtxMenu",
    "showLbCtxMenu",
    "showPersonCtxMenu",
    "showPetCtxMenu",
    "showSmartAlbumMenu",
    "showTagAlbumMenu",
  ],
  ondblclick: ["_bppDblclickToggle"],
  ontoggle: ["loadYearMonths"],
  onload: ["_bppThumbLoaded"],
  onerror: ["_bppThumbBroken"],
});

/**
 * The page function a delegated event may call, or null when the name
 * is not one the app's markup uses for that event.
 * @param {string} kind  the attribute, e.g. "onerror"
 * @param {string | undefined} name
 * @returns {Function | null}
 */
export function eventHandler(kind, name) {
  const allowed = EVENT_HANDLERS[kind];
  if (!name || !allowed || !allowed.includes(name)) return null;
  const fn = /** @type {any} */ (window)[name];
  return typeof fn === "function" ? fn : null;
}

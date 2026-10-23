// @ts-check
// Shared setup of the deleted suite (split for the 500-LOC cap).
import { vi } from "vitest";
import { _resetDeletedState } from "../bpp/web/static/js/modules/deleted.mjs";

/** Per-test DOM + window state (the suite's former beforeEach). */
export function setup() {
  document.body.innerHTML = `
    <div id="toast-container"></div>
    <div id="confirm-overlay">
      <div class="confirm-dialog"></div>
    </div>
    <div id="photo-grid"></div>
    <div id="deleted-count"></div>
    <div id="hidden-count"></div>
    <div id="album-picker-overlay">
      <div id="album-picker-list"></div>
      <input id="album-picker-new-name" />
    </div>
    <div id="card-ctx-menu" class="hidden">
      <div class="ctx-menu-item" id="card-ctx-fav" data-action="favorite"></div>
      <div class="ctx-menu-item" id="card-ctx-include" data-action="include"></div>
      <div class="ctx-menu-item" id="card-ctx-exclude" data-action="exclude"></div>
      <div class="ctx-menu-item" id="card-ctx-tag" data-action="tag-person"></div>
      <div class="ctx-menu-item" id="card-ctx-enhance" data-action="enhance"></div>
      <div class="ctx-menu-item" id="card-ctx-revert" data-action="revert-enhance"></div>
      <div class="ctx-menu-item" id="card-ctx-hide" data-action="hide"></div>
      <div class="ctx-menu-item" id="card-ctx-unhide" data-action="unhide"></div>
      <div class="ctx-menu-item" id="card-ctx-delete" data-action="delete"></div>
      <div class="ctx-menu-item" id="card-ctx-restore" data-action="restore"></div>
      <div class="ctx-menu-item" id="card-ctx-perm-delete" data-action="perm-delete"></div>
      <div id="card-ctx-sep1"></div>
      <div id="card-ctx-sep2"></div>
    </div>
  `;
  /** @type {any} */ (window).albumList = [];
  /** @type {any} */ (window).currentAlbumId = null;
  /** @type {any} */ (window).currentView = "library";
  /** @type {any} */ (window).currentViewId = null;
  /** @type {any} */ (window).photos = [];
  /** @type {any} */ (window).favorites = new Set();
  /** @type {any} */ (window).overrides = {};
  /** @type {any} */ (window).multiSelected = new Set();
  /** @type {any} */ (window).faceClusters = [];
  /** @type {any} */ (window).ICONS = { trash: "<i>tr</i>", hidden: "<i>hi</i>" };
  /** @type {any} */ (window).clearMultiSelect = vi.fn();
  /** @type {any} */ (window).renderAlbumNav = vi.fn();
  /** @type {any} */ (window).hide = vi.fn();
  /** @type {any} */ (window).show = vi.fn();
  /** @type {any} */ (window).updateToolbarTitle = vi.fn();
  /** @type {any} */ (window).updateToolbarForView = vi.fn();
  /** @type {any} */ (window).loadPhotosAndRecompute = vi.fn();
  /** @type {any} */ (window).toggleFavorite = vi.fn();
  /** @type {any} */ (window).setOverride = vi.fn();
  /** @type {any} */ (window).showBatchRenameModal = vi.fn();
  /** @type {any} */ (window)._iphShowTagPicker = vi.fn();
  // resolveConfirm is window-bridged from dialogs.mjs
  /** @type {any} */ (window).resolveConfirm = vi.fn();
  _resetDeletedState();
}

/** Undo setup() (the suite's former afterEach). */
export function teardown() {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
  for (const k of [
    "albumList",
    "currentAlbumId",
    "currentView",
    "currentViewId",
    "photos",
    "favorites",
    "overrides",
    "multiSelected",
    "faceClusters",
    "ICONS",
    "clearMultiSelect",
    "renderAlbumNav",
    "hide",
    "show",
    "updateToolbarTitle",
    "updateToolbarForView",
    "loadPhotosAndRecompute",
    "toggleFavorite",
    "setOverride",
    "showBatchRenameModal",
    "_iphShowTagPicker",
    "_albumPickerFilepaths",
    "resolveConfirm",
  ]) {
    delete (/** @type {any} */ (window)[k]);
  }
  _resetDeletedState();
}

/**
 * @param {object} body
 */
export function jsonResp(body) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

/**
 * Run a function that calls appConfirm, then immediately resolve the
 * confirm dialog with the given answer.
 * @param {() => Promise<any>} action
 * @param {boolean} answer
 */
export async function withConfirm(action, answer) {
  const dialogs = await import("../bpp/web/static/js/modules/dialogs.mjs");
  const promise = action();
  // Wait one microtask for the dialog to render
  await Promise.resolve();
  await Promise.resolve();
  dialogs.resolveConfirm(answer);
  return promise;
}

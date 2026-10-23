// @ts-check
/**
 * A context menu's single-key shortcut belongs to the menu alone.
 *
 * Both menus used to listen in the bubble phase, after the lightbox's
 * document handler: "d" on an open person menu dismissed the person AND
 * deleted the photo, "e" on an open card menu ran the card action AND
 * opened Export. The menus now take the key in the capture phase and stop
 * it there.
 */
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/lightbox.mjs", () => ({
  refreshLightboxIfOpen: vi.fn(),
  openLightbox: vi.fn(),
}));
vi.mock("../bpp/web/static/js/modules/people.mjs", () => ({
  dismissPerson: vi.fn(async () => {}),
  excludePerson: vi.fn(async () => {}),
  includePerson: vi.fn(async () => {}),
  isClusterExcluded: () => false,
  notAFaceCluster: vi.fn(async () => {}),
  personDisplayName: () => "Kai",
  showAvatarPicker: vi.fn(),
  showMergePicker: vi.fn(),
  showSplitPicker: vi.fn(),
  startNavFaceRename: vi.fn(),
  startPersonRename: vi.fn(),
  startPersonRenameLightbox: vi.fn(),
  _selectedPeople: new Set(),
  getPersonAlbumId: () => null,
  mergeSelected: vi.fn(),
}));

import { dismissPerson } from "../bpp/web/static/js/modules/people.mjs";
import {
  initPersonCtxMenu,
  showPersonCtxMenu,
} from "../bpp/web/static/js/modules/people-ctx-menu.mjs";
import {
  _resetCtxMenuState,
  initCardCtxMenu,
  showCardCtxMenu,
} from "../bpp/web/static/js/modules/deleted-ctx-menu.mjs";
import { state } from "../bpp/web/static/js/modules/state.mjs";

/** @type {import("vitest").Mock<(e: KeyboardEvent) => void>} */
let later;

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = `
    <div id="person-ctx-menu" class="hidden" role="menu">
      <div class="ctx-menu-item" data-action="dismiss">Dismiss</div>
      <div class="ctx-menu-item" data-action="rename">Rename</div>
    </div>
    <div id="card-ctx-menu" class="hidden" role="menu">
      <div class="ctx-menu-item" data-action="exclude" data-key="e">Exclude</div>
    </div>`;
  /** @type {any} */ (state).faceClusters = [{ cluster_id: 3, representative: {} }];
  /** @type {any} */ (window).setOverride = vi.fn();
  // Stands in for the lightbox / grid document handler (bubble phase,
  // registered before the menus are wired, as at app start).
  later = vi.fn((/** @type {KeyboardEvent} */ _e) => {});
  document.addEventListener("keydown", later);
});

afterEach(() => {
  document.removeEventListener("keydown", later);
  _resetCtxMenuState();
  document.body.innerHTML = "";
});

/** A right-click as the dispatcher hands it over. */
const rightClick = () =>
  /** @type {any} */ ({
    preventDefault() {},
    stopPropagation() {},
    target: document.body,
    clientX: 10,
    clientY: 10,
    button: 2,
    pointerType: "mouse",
  });

/** @param {string} key */
function press(key) {
  const ev = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true });
  document.body.dispatchEvent(ev);
  return ev;
}

describe("the person menu's shortcut", () => {
  test("runs the menu action only, and the other document handlers never see the key", () => {
    initPersonCtxMenu();
    showPersonCtxMenu(rightClick(), 3, null);
    press("d");
    expect(dismissPerson).toHaveBeenCalledWith(3);
    expect(later).not.toHaveBeenCalled();
  });

  test("Escape closes the menu and goes no further", () => {
    initPersonCtxMenu();
    showPersonCtxMenu(rightClick(), 3, null);
    press("Escape");
    expect(document.getElementById("person-ctx-menu")?.classList.contains("hidden")).toBe(true);
    expect(later).not.toHaveBeenCalled();
  });

  test("with the menu closed, keys pass through untouched", () => {
    initPersonCtxMenu();
    press("d");
    expect(dismissPerson).not.toHaveBeenCalled();
    expect(later).toHaveBeenCalledTimes(1);
  });
});

describe("the card menu's shortcut", () => {
  test("runs the card action only; Export never opens", () => {
    initCardCtxMenu();
    showCardCtxMenu(rightClick(), "/p/a.jpg");
    press("e");
    expect(/** @type {any} */ (window).setOverride).toHaveBeenCalledWith("/p/a.jpg", "exclude");
    expect(later).not.toHaveBeenCalled();
  });

  test("Escape with the menu closed is left to whoever owns it", () => {
    initCardCtxMenu();
    press("Escape");
    expect(later).toHaveBeenCalledTimes(1);
  });
});

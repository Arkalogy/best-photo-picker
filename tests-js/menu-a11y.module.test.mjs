// @ts-check
// Every right-click / ⋯ menu is a real menu for keyboard and screen-reader
// users: announced as one, arrow keys move through it, Enter picks, and
// focus goes back to what opened it. One helper (menu-a11y.mjs) does it.
import { beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/api-client.mjs", () => ({
  apiFetch: vi.fn(async () => ({})),
  authedSrc: (/** @type {string} */ p) => p,
  faceCropUrl: (/** @type {string} */ h, /** @type {number} */ i) => `/api/v1/faces/crop/${h}/${i}`,
}));

const { menuOpened, menuClosed } = await import("../bpp/web/static/js/modules/menu-a11y.mjs");

/** @param {string} key */
function press(key, target = document.activeElement) {
  target?.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
}

beforeEach(() => {
  document.body.innerHTML = `
    <button id="trigger">⋯</button>
    <div class="ctx-menu" id="menu">
      <div class="ctx-menu-item" id="one">One</div>
      <div class="ctx-menu-sep"></div>
      <div class="ctx-menu-item" id="two" style="display:none">Hidden</div>
      <div class="ctx-menu-item disabled" id="three">Off</div>
      <div class="ctx-menu-item" id="four">Four</div>
    </div>
  `;
  document.getElementById("trigger")?.focus();
});

describe("the menu helper", () => {
  test("announces a menu with its live items and moves focus in", () => {
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    const trigger = /** @type {HTMLElement} */ (document.getElementById("trigger"));
    menuOpened(menu, { trigger });
    expect(menu.getAttribute("role")).toBe("menu");
    expect(trigger.getAttribute("aria-haspopup")).toBe("menu");
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    const items = [...menu.querySelectorAll('[role="menuitem"]')].map((el) => el.id);
    expect(items).toEqual(["one", "four"]); // not the hidden or disabled ones
    expect(document.activeElement?.id).toBe("one");
  });

  test("arrow keys move through the live items and wrap; Home/End jump", () => {
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    menuOpened(menu, { trigger: document.getElementById("trigger") });
    press("ArrowDown");
    expect(document.activeElement?.id).toBe("four");
    press("ArrowDown");
    expect(document.activeElement?.id).toBe("one"); // wrapped
    press("ArrowUp");
    expect(document.activeElement?.id).toBe("four");
    press("Home");
    expect(document.activeElement?.id).toBe("one");
    press("End");
    expect(document.activeElement?.id).toBe("four");
  });

  test("Enter and Space activate the focused item", () => {
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    const clicked = vi.fn();
    document.getElementById("four")?.addEventListener("click", clicked);
    menuOpened(menu, { trigger: document.getElementById("trigger") });
    press("End");
    press("Enter");
    press(" ");
    expect(clicked).toHaveBeenCalledTimes(2);
  });

  test("closing puts focus back on the trigger and marks it collapsed", () => {
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    const trigger = /** @type {HTMLElement} */ (document.getElementById("trigger"));
    menuOpened(menu, { trigger });
    menuClosed(menu);
    expect(document.activeElement).toBe(trigger);
    expect(trigger.getAttribute("aria-expanded")).toBe("false");
    press("ArrowDown", menu); // the key handler is gone
    expect(document.activeElement).toBe(trigger);
  });

  test("handled keys never reach the page behind the menu", () => {
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    const leaked = vi.fn();
    document.addEventListener("keydown", leaked);
    menuOpened(menu, { trigger: document.getElementById("trigger") });
    press("ArrowDown");
    press("End");
    press("Enter");
    press("x"); // not a menu key: passes through
    document.removeEventListener("keydown", leaked);
    expect(leaked).toHaveBeenCalledTimes(1);
  });

  test("a native button item activates itself: no extra synthetic click", () => {
    document.body.innerHTML = `
      <button id="trigger">⋯</button>
      <div id="menu"><button class="fe-menu-item" id="b1">Use</button></div>`;
    const clicked = vi.fn();
    document.getElementById("b1")?.addEventListener("click", clicked);
    menuOpened(/** @type {HTMLElement} */ (document.getElementById("menu")), {
      trigger: document.getElementById("trigger"),
    });
    press("Enter");
    press(" ");
    expect(clicked).not.toHaveBeenCalled(); // the browser's own activation does it
  });

  test("separators and headers are announced as structure, not items", () => {
    document.body.innerHTML = `
      <button id="trigger">⋯</button>
      <div class="ctx-menu" id="menu">
        <div class="ctx-header">Move</div>
        <div class="ctx-item">A</div>
        <div class="ctx-menu-sep"></div>
        <div class="ctx-item">B</div></div>`;
    menuOpened(/** @type {HTMLElement} */ (document.getElementById("menu")), {
      trigger: document.getElementById("trigger"),
      items: ".ctx-item",
    });
    expect(document.querySelector(".ctx-header")?.getAttribute("role")).toBe("presentation");
    expect(document.querySelector(".ctx-menu-sep")?.getAttribute("role")).toBe("separator");
  });

  test("focus goes back to the nearest focusable ancestor of an unfocusable trigger", () => {
    document.body.innerHTML = `
      <button id="row"><span id="label">Album</span></button>
      <div class="ctx-menu" id="menu"><div class="ctx-item">A</div></div>`;
    document.getElementById("row")?.focus();
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    menuOpened(menu, { trigger: document.getElementById("label"), items: ".ctx-item" });
    menuClosed(menu);
    expect(document.activeElement?.id).toBe("row");
  });

  test("an item hidden by a stylesheet rule is not live either", () => {
    document.body.innerHTML = `
      <style>.face-only { display: none; }</style>
      <button id="trigger">⋯</button>
      <div class="ctx-menu" id="menu">
        <div class="ctx-menu-item face-only" id="hiddenByCss">Reassign this face</div>
        <div class="ctx-menu-item" id="rename">Rename</div></div>`;
    menuOpened(/** @type {HTMLElement} */ (document.getElementById("menu")), {
      trigger: document.getElementById("trigger"),
    });
    expect(document.activeElement?.id).toBe("rename");
    expect(document.getElementById("hiddenByCss")?.getAttribute("role")).toBe(null);
  });

  test("Escape closes a menu that asked for it, and goes no further", () => {
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    const trigger = /** @type {HTMLElement} */ (document.getElementById("trigger"));
    const leaked = vi.fn();
    const onEscape = vi.fn(() => menuClosed(menu));
    document.addEventListener("keydown", leaked);
    menuOpened(menu, { trigger, onEscape });
    press("Escape");
    document.removeEventListener("keydown", leaked);
    expect(onEscape).toHaveBeenCalledTimes(1);
    expect(leaked).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(trigger);
  });

  test("what opened a menu is still known after it closed (a dialog may ask)", async () => {
    const { menuTriggerOf } = await import("../bpp/web/static/js/modules/menu-a11y.mjs");
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    const trigger = document.getElementById("trigger");
    menuOpened(menu, { trigger });
    menuClosed(menu);
    expect(menuTriggerOf(menu)).toBe(trigger);
  });

  test("a menu opened from something unfocusable still works, and closing is harmless", () => {
    const menu = /** @type {HTMLElement} */ (document.getElementById("menu"));
    menuOpened(menu, { trigger: document.body });
    expect(document.activeElement?.id).toBe("one");
    menuClosed(menu);
    menuClosed(menu); // twice: nothing to undo
  });
});

describe("the app's menus use it", () => {
  test("the card context menu", async () => {
    const { showCardCtxMenu, hideCardCtxMenu } =
      await import("../bpp/web/static/js/modules/deleted-ctx-menu.mjs");
    document.body.innerHTML = `
      <div class="card" tabindex="0" id="card"></div>
      <div class="ctx-menu hidden" id="card-ctx-menu">
        <div class="ctx-menu-item" data-action="favorite" id="card-ctx-fav">Favorite</div>
        <div class="ctx-menu-item" data-action="hide" id="card-ctx-hide">Hide</div>
      </div>`;
    const card = /** @type {HTMLElement} */ (document.getElementById("card"));
    card.focus();
    // (A real right-click: button 2 — the menu treats a buttonless event
    // on a touch device as a tap that toggles selection instead.)
    const ev = new MouseEvent("contextmenu", {
      bubbles: true,
      clientX: 10,
      clientY: 10,
      button: 2,
    });
    Object.defineProperty(ev, "target", { value: card });
    showCardCtxMenu(ev, "/a.jpg");
    const menu = document.getElementById("card-ctx-menu");
    expect(menu?.getAttribute("role")).toBe("menu");
    expect(menu?.contains(document.activeElement)).toBe(true);
    hideCardCtxMenu();
    expect(document.activeElement).toBe(card);
  });

  test("the picker's ⋯ menu", async () => {
    const { _feOverflowToggle } =
      await import("../bpp/web/static/js/modules/face-embedders-popover.mjs");
    document.body.innerHTML = `
      <div class="fe-overflow">
        <button class="fe-overflow-trigger" data-arg0="m" id="t">⋯</button>
        <div class="fe-overflow-menu" id="fe-ovr-m">
          <button class="fe-menu-item" data-action="x">Use</button>
          <button class="fe-menu-item" disabled>Off</button>
        </div></div>`;
    document.getElementById("t")?.focus();
    _feOverflowToggle("m");
    const menu = document.getElementById("fe-ovr-m");
    expect(menu?.getAttribute("role")).toBe("menu");
    expect(document.getElementById("t")?.getAttribute("aria-expanded")).toBe("true");
    expect(document.activeElement?.textContent).toBe("Use");
    _feOverflowToggle("m"); // toggles closed
    expect(document.activeElement?.id).toBe("t");
    expect(document.getElementById("t")?.getAttribute("aria-expanded")).toBe("false");
  });

  test("an album menu built on the fly", async () => {
    const { showAlbumMoveMenu } = await import("../bpp/web/static/js/modules/albums-menus.mjs");
    document.body.innerHTML = `<button id="album-row">Album</button>`;
    /** @type {any} */ (window).albumList = [
      { id: 1, name: "A", album_type: "manual", parent_id: null },
      { id: 2, name: "B", album_type: "manual", parent_id: null },
    ];
    const row = /** @type {HTMLElement} */ (document.getElementById("album-row"));
    row.focus();
    const ev = new MouseEvent("contextmenu", { bubbles: true, clientX: 5, clientY: 5 });
    Object.defineProperty(ev, "target", { value: row });
    showAlbumMoveMenu(ev, 1);
    const menu = document.getElementById("album-move-menu");
    expect(menu?.getAttribute("role")).toBe("menu");
    expect(menu?.querySelectorAll('[role="menuitem"]').length).toBe(1); // "B"
    expect(menu?.contains(document.activeElement)).toBe(true);
    press("Escape");
    expect(document.getElementById("album-move-menu")).toBe(null);
    expect(document.activeElement).toBe(row);
    showAlbumMoveMenu(ev, 1);
    const again = document.getElementById("album-move-menu");
    // An action removes the menu itself: focus still goes back to the row.
    const { _dismissAlbumMenu } = await import("../bpp/web/static/js/modules/albums-menus.mjs");
    _dismissAlbumMenu(again);
    expect(document.activeElement).toBe(row);
    expect(document.getElementById("album-move-menu")).toBe(null);
  });
});

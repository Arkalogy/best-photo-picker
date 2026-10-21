// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { wireMenuActions } from "../bpp/web/static/js/modules/menu-a11y.mjs";

beforeEach(() => {
  document.body.innerHTML = "";
});
afterEach(() => {
  document.body.innerHTML = "";
  delete (/** @type {any} */ (window).fooAction);
});

describe("wireMenuActions", () => {
  test("a click on a menu item runs its data-action with coerced args", () => {
    const spy = vi.fn();
    /** @type {any} */ (window).fooAction = spy;
    const menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.innerHTML =
      '<div class="ctx-item" data-action="fooAction" data-arg0="7" data-arg1="Top"><span>Rename</span></div>';
    document.body.appendChild(menu);
    wireMenuActions(menu, { items: ".ctx-item" });
    /** @type {HTMLElement} */ (menu.querySelector("span")).click();
    expect(spy).toHaveBeenCalledWith(7, "Top");
  });

  test("an unknown action is reported, not swallowed", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const menu = document.createElement("div");
    menu.className = "ctx-menu";
    menu.innerHTML = '<div class="ctx-item" data-action="noSuchThing">x</div>';
    document.body.appendChild(menu);
    wireMenuActions(menu);
    /** @type {HTMLElement} */ (menu.querySelector(".ctx-item")).click();
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

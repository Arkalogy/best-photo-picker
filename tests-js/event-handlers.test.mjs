// @ts-check
/**
 * Delegated events other than a click call only the handlers the app's
 * own markup names for them. Markup that carries the same attributes
 * but names another page function is ignored.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import { EVENT_HANDLERS, eventHandler } from "../bpp/web/static/js/modules/event-handlers.mjs";

const win = /** @type {any} */ (window);

/** @param {string} dir @returns {string[]} */
function sources(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) return sources(full);
    return /\.(html|mjs|js)$/.test(name) ? [full] : [];
  });
}

/** Every handler name the app's markup gives each `data-on…` attribute. */
function namesInMarkup() {
  /** @type {Record<string, Set<string>>} */
  const found = {};
  const add = (/** @type {string} */ kind, /** @type {string} */ name) =>
    (found[kind] ||= new Set()).add(name);
  for (const file of [...sources("bpp/web/templates"), ...sources("bpp/web/static/js")]) {
    if (file.endsWith("event-handlers.mjs")) continue;
    const src = readFileSync(file, "utf8");
    // data-onerror="name"  ·  data-oncontextmenu="${cond ? "a" : "b"}"
    for (const m of src.matchAll(/data-(on[a-z]+)=\\?["'](\$\{[^}]*\}|[A-Za-z_$][\w$]*)/g)) {
      if (m[2].startsWith("${"))
        for (const q of m[2].matchAll(/["']([A-Za-z_$][\w$]*)["']/g)) add(m[1], q[1]);
      else add(m[1], m[2]);
    }
    // el.setAttribute("data-onerror", "name")  ·  el.dataset.onerror = "name"
    for (const m of src.matchAll(
      /setAttribute\(\s*["']data-(on[a-z]+)["']\s*,\s*["']([\w$]+)["']/g
    ))
      add(m[1], m[2]);
    for (const m of src.matchAll(/\.dataset\.(on[a-z]+)\s*=\s*["']([\w$]+)["']/g)) add(m[1], m[2]);
  }
  return Object.fromEntries(
    Object.entries(found).map(([kind, names]) => [kind, [...names].sort()])
  );
}

test("the lists are exactly the names the app's markup uses", () => {
  const listed = Object.fromEntries(
    Object.entries(EVENT_HANDLERS).map(([kind, names]) => [kind, [...names].sort()])
  );
  expect(listed).toEqual(namesInMarkup());
});

describe("eventHandler", () => {
  afterEach(() => {
    delete win._bppThumbBroken;
    delete win.toast;
  });

  test("returns the page function for a listed name", () => {
    const fn = () => {};
    win._bppThumbBroken = fn;
    expect(eventHandler("onerror", "_bppThumbBroken")).toBe(fn);
  });

  test("refuses a page function that is not listed for that event", () => {
    win.toast = () => {};
    expect(eventHandler("onerror", "toast")).toBeNull();
    expect(eventHandler("onerror", "loadYearMonths")).toBeNull(); // listed for another event
    expect(eventHandler("onerror", undefined)).toBeNull();
    expect(eventHandler("onwhatever", "toast")).toBeNull();
    expect(eventHandler("onerror", "constructor")).toBeNull();
  });
});

describe("the dispatchers in globals.js", () => {
  beforeAll(() => {
    win.eventHandler = eventHandler;
    // eslint-disable-next-line no-new-func
    new Function("window", "document", readFileSync("bpp/web/static/js/globals.js", "utf8"))(
      window,
      document
    );
  });

  beforeEach(() => {
    document.body.innerHTML = "";
    win.toast = vi.fn();
    win._shareToggle = vi.fn();
  });

  afterEach(() => {
    delete win.toast;
    delete win._shareToggle;
  });

  /** @param {string} html @param {string} type */
  function fire(html, type) {
    document.body.innerHTML = html;
    const el = /** @type {HTMLElement} */ (document.body.firstElementChild);
    el.dispatchEvent(new Event(type, { bubbles: type !== "error" && type !== "load" }));
    return el;
  }

  test("a failed image runs the app's own handler", () => {
    const el = fire('<div><img data-onerror="_bppThumbBroken"></div>', "x");
    /** @type {HTMLElement} */ (el.firstElementChild).dispatchEvent(new Event("error"));
    expect(el.classList.contains("lb-thumb-broken")).toBe(true);
  });

  test.each([
    ["error", '<img data-onerror="toast">'],
    ["load", '<img data-onload="toast">'],
    ["toggle", '<details data-ontoggle="toast"></details>'],
    ["mouseleave", '<div data-onmouseleave="toast"></div>'],
    ["pointerdown", '<div data-onpointerdown="toast"></div>'],
    ["input", '<input data-oninput="toast">'],
    ["contextmenu", '<div data-oncontextmenu="toast"></div>'],
  ])("%s on foreign markup calls nothing", (type, html) => {
    fire(html, type);
    expect(win.toast).not.toHaveBeenCalled();
  });

  test("an error event cannot stand in for a click on a backdrop", () => {
    fire(
      '<img data-onerror="_bppBackdropClose" data-backdrop-fn="_shareToggle" data-backdrop-arg="true">',
      "error"
    );
    expect(win._shareToggle).not.toHaveBeenCalled();
    // …and even called directly with a non-click event, it does nothing.
    const el = /** @type {HTMLElement} */ (document.body.firstElementChild);
    win._bppBackdropClose.call(el, { target: el, type: "error" });
    expect(win._shareToggle).not.toHaveBeenCalled();
  });

  test("given a click on the backdrop itself, it closes", () => {
    win.hideSettings = vi.fn();
    document.body.innerHTML = '<div id="ov" data-backdrop-fn="hideSettings"><p>x</p></div>';
    const el = /** @type {HTMLElement} */ (document.getElementById("ov"));
    win._bppBackdropClose.call(el, { target: el.firstElementChild, type: "click" });
    expect(win.hideSettings).not.toHaveBeenCalled(); // a click inside the dialog
    win._bppBackdropClose.call(el, { target: el, type: "click" });
    expect(win.hideSettings).toHaveBeenCalledTimes(1);
    delete win.hideSettings;
  });
});

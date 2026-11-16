// @ts-check
/**
 * Tests for the value the global `change` dispatcher hands to a
 * `data-onchange` handler.
 *
 * A checkbox reports its ticked state (a boolean). Every other control
 * reports its value. A checkbox's `value` is the string "on" whether it
 * is ticked or not, so handing that to a handler written for a boolean
 * made every switch read as "on" (LAN sharing, update check, follow
 * shortcuts could not be turned off).
 *
 * Loads globals.js via Function() once, like dispatcher.test.mjs.
 */

import { afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";
import { readFileSync } from "fs";

const globalsSource = readFileSync("bpp/web/static/js/globals.js", "utf8");

beforeAll(() => {
  new Function("window", "document", globalsSource)(window, document);
  // These tests exercise the dispatch mechanics with stand-in handler
  // names; which names the app allows is tested in event-handlers.test.mjs.
  /** @type {any} */ (window).eventHandler = (
    /** @type {string} */ _kind,
    /** @type {string} */ name
  ) => /** @type {any} */ (window)[name];
});

beforeEach(() => {
  document.body.innerHTML = "";
});

afterEach(() => {
  for (const key of Object.keys(window)) {
    if (key.startsWith("_test")) delete (/** @type {any} */ (window)[key]);
  }
});

/**
 * Put `html` in the page, wire its `data-onchange` handler to a spy,
 * let `mutate` change the control, then fire a bubbling change event.
 * @param {string} html
 * @param {(el: any) => void} mutate
 */
function changeAndSpy(html, mutate) {
  const spy = vi.fn();
  /** @type {any} */ (window)._testOnChange = spy;
  document.body.innerHTML = html;
  const el = /** @type {any} */ (document.querySelector("[data-onchange]"));
  mutate(el);
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return spy;
}

describe("change dispatcher: checkbox reports its ticked state", () => {
  test("unticking a ticked checkbox passes false", () => {
    const spy = changeAndSpy(
      '<input type="checkbox" checked data-onchange="_testOnChange">',
      (el) => {
        el.checked = false;
      }
    );
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toBe(false);
  });

  test("ticking an unticked checkbox passes true", () => {
    const spy = changeAndSpy('<input type="checkbox" data-onchange="_testOnChange">', (el) => {
      el.checked = true;
    });
    expect(spy.mock.calls[0][0]).toBe(true);
  });

  test("a checkbox with a value attribute still passes a boolean", () => {
    const spy = changeAndSpy(
      '<input type="checkbox" value="yes" checked data-onchange="_testOnChange">',
      (el) => {
        el.checked = false;
      }
    );
    expect(spy.mock.calls[0][0]).toBe(false);
  });

  test("the handler is called with the checkbox as `this` and the event second", () => {
    /** @type {any} */
    let seenThis = null;
    /** @type {any} */
    let seenEvent = null;
    /** @type {any} */ (window)._testOnChange = function (
      /** @type {any} */ _v,
      /** @type {any} */ e
    ) {
      seenThis = this;
      seenEvent = e;
    };
    document.body.innerHTML = '<input type="checkbox" id="cb" data-onchange="_testOnChange">';
    const el = /** @type {any} */ (document.getElementById("cb"));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    expect(seenThis).toBe(el);
    expect(seenEvent && seenEvent.type).toBe("change");
  });
});

describe("change dispatcher: other controls still report their value", () => {
  test("a select passes the selected option's value", () => {
    const spy = changeAndSpy(
      '<select data-onchange="_testOnChange"><option value="a">A</option><option value="b">B</option></select>',
      (el) => {
        el.value = "b";
      }
    );
    expect(spy.mock.calls[0][0]).toBe("b");
  });

  test("a number input passes its value as a string", () => {
    const spy = changeAndSpy('<input type="number" data-onchange="_testOnChange">', (el) => {
      el.value = "25";
    });
    expect(spy.mock.calls[0][0]).toBe("25");
  });

  test("a radio button passes its value, not its ticked state", () => {
    const spy = changeAndSpy(
      '<input type="radio" name="r" value="personal" data-onchange="_testOnChange">',
      (el) => {
        el.checked = true;
      }
    );
    expect(spy.mock.calls[0][0]).toBe("personal");
  });
});

describe("change dispatcher: reads the control that changed, not the wrapper", () => {
  test("radio cards inside a wrapper pass the chosen radio's value", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testOnChange = spy;
    document.body.innerHTML =
      '<div id="cards" data-onchange="_testOnChange">' +
      '<label><input type="radio" name="ctx" value="personal"></label>' +
      '<label><input type="radio" name="ctx" value="commercial"></label>' +
      "</div>";
    const radio = /** @type {any} */ (document.querySelector('input[value="commercial"]'));
    radio.checked = true;
    radio.dispatchEvent(new Event("change", { bubbles: true }));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0]).toBe("commercial");
    expect(spy.mock.contexts[0]).toBe(document.getElementById("cards"));
  });

  test("a checkbox inside a wrapper passes its ticked state", () => {
    const spy = vi.fn();
    /** @type {any} */ (window)._testOnChange = spy;
    document.body.innerHTML =
      '<div data-onchange="_testOnChange"><label><input type="checkbox" checked></label></div>';
    const box = /** @type {any} */ (document.querySelector("input"));
    box.checked = false;
    box.dispatchEvent(new Event("change", { bubbles: true }));
    expect(spy.mock.calls[0][0]).toBe(false);
  });
});

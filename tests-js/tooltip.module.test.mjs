// @ts-check
//
// Module-style tests for the tooltip helper.
//
// The module attaches `mouseover` / `mouseout` / `scroll` / `click` /
// `keydown` listeners to `document` at import time. We import once
// at the top of the test, then verify behavior by dispatching events
// + advancing fake timers.

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { _state, hide, show } from "../bpp/web/static/js/modules/tooltip.mjs";

beforeEach(() => {
  vi.useFakeTimers();
  // Stub after useFakeTimers so our version wins over vitest's fake RAF.
  // jsdom's RAF isn't tied to fake timers; fire the callback synchronously
  // so the .visible class lands inside the test tick.
  vi.stubGlobal("requestAnimationFrame", (cb) => {
    cb(0);
    return 0;
  });
  document.body.innerHTML = "";
  // The shared tooltip element is created lazily on first show.
  // Drop the cache between tests so each starts clean.
  _state.tip = null;
  _state.timer = null;
  _state.current = null;
});

afterEach(() => {
  hide();
  vi.useRealTimers();
});

const tipEl = () => /** @type {HTMLElement | null} */ (document.querySelector(".app-tooltip"));

describe("show", () => {
  test("creates a single .app-tooltip element with the supplied text", () => {
    show("Hello world", 100, 100);
    const tip = tipEl();
    expect(tip).toBeTruthy();
    expect(tip.textContent).toBe("Hello world");
    expect(tip.style.display).toBe("block");
  });

  test("re-uses the same element across calls", () => {
    show("First", 100, 100);
    const a = tipEl();
    show("Second", 200, 200);
    const b = tipEl();
    expect(a).toBe(b);
    expect(b.textContent).toBe("Second");
    // Only one tooltip element in the DOM
    expect(document.querySelectorAll(".app-tooltip")).toHaveLength(1);
  });

  test("positions slightly below + right of cursor by default", () => {
    show("x", 100, 100);
    const tip = tipEl();
    // Default offsets: x+12, y+16
    expect(tip.style.left).toBe("112px");
    expect(tip.style.top).toBe("116px");
  });

  test(".visible class lands after rAF", () => {
    show("x", 50, 50);
    expect(tipEl().classList.contains("visible")).toBe(true);
  });
});

describe("hide", () => {
  test("removes the .visible class and hides the element", () => {
    show("x", 50, 50);
    expect(tipEl().classList.contains("visible")).toBe(true);
    hide();
    expect(tipEl().classList.contains("visible")).toBe(false);
    expect(tipEl().style.display).toBe("none");
  });

  test("restores a stashed title attribute when called", () => {
    const target = document.createElement("button");
    target.title = "Click me";
    document.body.appendChild(target);
    // Simulate the mouseover handler stashing + removing the title.
    /** @type {any} */ (target)._savedTitle = target.title;
    target.removeAttribute("title");
    _state.current = target;
    hide();
    expect(target.title).toBe("Click me");
    expect(/** @type {any} */ (target)._savedTitle).toBeUndefined();
  });

  test("clears the pending show timer so the tooltip never appears", () => {
    const target = document.createElement("span");
    target.title = "tip";
    document.body.appendChild(target);
    target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, clientX: 10, clientY: 10 }));
    // Title removed by the handler; saved on the element
    expect(target.hasAttribute("title")).toBe(false);
    // Hide before the 100ms show timer fires
    hide();
    vi.advanceTimersByTime(200);
    // Tooltip never became visible
    expect(tipEl()?.classList.contains("visible")).toBeFalsy();
    // Title restored by hide
    expect(target.title).toBe("tip");
  });
});

describe("delegated mouseover", () => {
  test("schedules a show 100ms after entering an element with a title", () => {
    const target = document.createElement("div");
    target.title = "Hover me";
    document.body.appendChild(target);

    target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, clientX: 50, clientY: 50 }));
    // Native title is suppressed immediately
    expect(target.hasAttribute("title")).toBe(false);
    // Tooltip not yet visible
    expect(tipEl()?.classList.contains("visible")).toBeFalsy();
    vi.advanceTimersByTime(100);
    expect(tipEl().textContent).toBe("Hover me");
    expect(tipEl().classList.contains("visible")).toBe(true);
  });

  test("ignores elements without a title", () => {
    const target = document.createElement("div");
    document.body.appendChild(target);
    target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    vi.advanceTimersByTime(200);
    expect(tipEl()).toBeNull();
  });

  test("does not retrigger when the same element fires mouseover again", () => {
    const target = document.createElement("div");
    target.title = "Sticky";
    document.body.appendChild(target);
    target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, clientX: 0, clientY: 0 }));
    vi.advanceTimersByTime(100);
    const firstTip = tipEl();
    expect(firstTip.textContent).toBe("Sticky");

    // Re-firing on the same target should be a no-op
    target.dispatchEvent(new MouseEvent("mouseover", { bubbles: true, clientX: 0, clientY: 0 }));
    expect(tipEl()).toBe(firstTip);
    expect(firstTip.textContent).toBe("Sticky");
  });
});

describe("delegated dismissal", () => {
  test("scroll hides an active tooltip", () => {
    show("x", 10, 10);
    expect(tipEl().classList.contains("visible")).toBe(true);
    document.dispatchEvent(new Event("scroll", { bubbles: true }));
    expect(tipEl().classList.contains("visible")).toBe(false);
  });

  test("click hides an active tooltip", () => {
    show("x", 10, 10);
    document.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    expect(tipEl().classList.contains("visible")).toBe(false);
  });

  test("keydown hides an active tooltip", () => {
    show("x", 10, 10);
    document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "a" }));
    expect(tipEl().classList.contains("visible")).toBe(false);
  });
});

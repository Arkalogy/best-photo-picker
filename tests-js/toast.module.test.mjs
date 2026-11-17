// @ts-check
// Module-style tests for the toast notification helper.
//
// Uses fake timers so the auto-dismiss timeline is deterministic and
// jsdom DOM queries to assert what actually gets rendered.

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { showToast, toast, toastError } from "../bpp/web/static/js/modules/toast.mjs";

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = '<div id="toast-container"></div>';
  // requestAnimationFrame in jsdom isn't tied to fake timers, so stub it
  // to fire synchronously — that's what we want for testing the
  // .visible class application.
  vi.stubGlobal("requestAnimationFrame", (cb) => {
    cb(0);
    return 0;
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const containerToast = () =>
  /** @type {HTMLElement} */ (document.querySelector("#toast-container .toast"));

describe("toast (default)", () => {
  test("renders message into #toast-container with .toast class", () => {
    toast("Saved");
    const el = containerToast();
    expect(el).toBeTruthy();
    expect(el.className).toBe("toast visible");
    expect(el.textContent).toBe("Saved");
  });

  test("auto-dismisses at 3.75s + 0.3s removal delay", () => {
    toast("Saved");
    expect(containerToast()).toBeTruthy();

    // Just before the dismiss tick — still visible
    vi.advanceTimersByTime(3749);
    expect(containerToast().classList.contains("visible")).toBe(true);

    // At 3750ms — visible class removed
    vi.advanceTimersByTime(1);
    expect(containerToast().classList.contains("visible")).toBe(false);

    // Element still in DOM during the 300ms exit transition
    expect(containerToast()).toBeTruthy();

    // After the transition — element is gone
    vi.advanceTimersByTime(300);
    expect(document.querySelector("#toast-container .toast")).toBeNull();
  });
});

describe("toast (severity)", () => {
  test("type=true → error", () => {
    toast("Failed", true);
    expect(containerToast().classList.contains("error")).toBe(true);
  });

  test('type="error" also → error', () => {
    toast("Failed", "error");
    expect(containerToast().classList.contains("error")).toBe(true);
  });

  test('type="warning" → warning, NOT error', () => {
    toast("Heads up", "warning");
    const el = containerToast();
    expect(el.classList.contains("warning")).toBe(true);
    expect(el.classList.contains("error")).toBe(false);
  });
});

describe("toast (action)", () => {
  test("renders an action button + extends auto-dismiss to 6s", () => {
    const fn = vi.fn();
    toast("Deleted", undefined, { action: { label: "Undo", fn } });

    const btn = /** @type {HTMLElement} */ (document.querySelector(".toast .toast-action"));
    expect(btn).toBeTruthy();
    expect(btn.textContent).toBe("Undo");

    // Pre-6s tick — still visible
    vi.advanceTimersByTime(5999);
    expect(containerToast().classList.contains("visible")).toBe(true);
    vi.advanceTimersByTime(1);
    expect(containerToast().classList.contains("visible")).toBe(false);
  });

  test("clicking the action button fires fn and removes the toast", () => {
    const fn = vi.fn();
    toast("Deleted", undefined, { action: { label: "Undo", fn } });

    const btn = /** @type {HTMLElement} */ (document.querySelector(".toast .toast-action"));
    btn.click();
    expect(fn).toHaveBeenCalledOnce();
    expect(document.querySelector("#toast-container .toast")).toBeNull();
  });
});

describe("toast (no container)", () => {
  test("returns silently when #toast-container is missing", () => {
    document.body.innerHTML = "";
    expect(() => toast("orphan")).not.toThrow();
  });
});

describe("showToast (legacy variant)", () => {
  test("renders plain text when no onUndo callback", () => {
    showToast("Hello");
    const el = containerToast();
    expect(el).toBeTruthy();
    expect(el.textContent).toBe("Hello");
    expect(el.querySelector(".toast-undo")).toBeNull();
  });

  test("renders Undo button when onUndo is provided", () => {
    const undo = vi.fn();
    showToast("Deleted 5 photos", 5000, undo);
    const btn = /** @type {HTMLElement} */ (document.querySelector(".toast .toast-undo"));
    expect(btn).toBeTruthy();
    expect(btn.textContent).toBe("Undo");
  });

  test("clicking Undo fires the callback and removes the toast", () => {
    const undo = vi.fn();
    showToast("Deleted", 5000, undo);
    /** @type {HTMLElement} */ (document.querySelector(".toast .toast-undo")).click();
    expect(undo).toHaveBeenCalledOnce();
    // 300ms removal delay after the click
    vi.advanceTimersByTime(300);
    expect(document.querySelector("#toast-container .toast")).toBeNull();
  });

  test("respects custom duration", () => {
    showToast("Quick", 1000);
    vi.advanceTimersByTime(999);
    expect(containerToast().classList.contains("visible")).toBe(true);
    vi.advanceTimersByTime(1);
    expect(containerToast().classList.contains("visible")).toBe(false);
  });

  test("defaults to 3000ms duration when not specified", () => {
    showToast("Default");
    vi.advanceTimersByTime(2999);
    expect(containerToast().classList.contains("visible")).toBe(true);
    vi.advanceTimersByTime(1);
    expect(containerToast().classList.contains("visible")).toBe(false);
  });

  test("returns silently when #toast-container is missing", () => {
    document.body.innerHTML = "";
    expect(() => showToast("orphan")).not.toThrow();
  });
});

describe("toastError for an owner-only refusal", () => {
  const ownerOnly = () =>
    Object.assign(new Error("only the computer running Best Photo Picker can do this"), {
      ownerOnly: true,
    });
  const shown = () =>
    [...document.querySelectorAll("#toast-container .toast")].map((t) => t.textContent);

  test("says where to do it, and says it once for a burst of refusals", () => {
    vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
    for (let tick = 0; tick < 12; tick++) toastError("save that setting", ownerOnly());
    expect(shown()).toHaveLength(1);
    expect(shown()[0]).toContain("do it on that computer");
    expect(shown()[0]).not.toContain("try again");

    // Another action tried right away is still answered.
    toastError("change the date", ownerOnly());
    expect(shown().some((t) => t && t.includes("change the date"))).toBe(true);

    vi.setSystemTime(new Date("2026-01-01T00:00:11Z"));
    const before = shown().length;
    toastError("save that setting", ownerOnly());
    expect(shown().length).toBe(before + 1);
  });

  test("an ordinary failure is still shown every time", () => {
    toastError("save that setting", new Error("HTTP 500"));
    toastError("save that setting", new Error("HTTP 500"));
    expect(shown().filter((t) => t && t.includes("HTTP 500"))).toHaveLength(2);
    expect(shown()[0]).toContain("try again");
  });
});

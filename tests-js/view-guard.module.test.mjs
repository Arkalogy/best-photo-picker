// @ts-check
// Module-style tests for view-guard.mjs — the per-view AbortController
// + token machinery that protects loaders from stale-response writes.

import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _resetForTests,
  currentViewToken,
  installViewGuard,
  viewFetch,
  viewStillCurrent,
} from "../bpp/web/static/js/modules/view-guard.mjs";

/**
 * Build a fetch stub that resolves to a fixed JSON body after `delayMs`,
 * but honours the incoming AbortSignal — when aborted, it rejects with
 * a DOMException-like { name: "AbortError" }.
 *
 * @param {any} body
 * @param {number} [delayMs]
 */
function makeAwaitableFetch(body, delayMs = 0) {
  return vi.fn(
    (/** @type {string} */ _url, /** @type {RequestInit} */ opts) =>
      new Promise((resolve, reject) => {
        const signal = opts && opts.signal;
        const t = setTimeout(() => {
          resolve(
            new Response(JSON.stringify(body), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            })
          );
        }, delayMs);
        if (signal) {
          signal.addEventListener("abort", () => {
            clearTimeout(t);
            const err = new Error("aborted");
            err.name = "AbortError";
            reject(err);
          });
        }
      })
  );
}

describe("installViewGuard", () => {
  beforeEach(() => {
    _resetForTests();
    // @ts-ignore — recreate currentView each test so the setter re-installs cleanly
    delete window.currentView;
    // @ts-ignore
    delete window.__viewGuardInstalled;
  });

  test("bumps the view token on assignment", () => {
    // @ts-ignore
    window.currentView = "library";
    installViewGuard(window);
    const before = currentViewToken();
    // @ts-ignore
    window.currentView = "calendar";
    expect(currentViewToken()).toBe(before + 1);
  });

  test("no bump when assigning the same value", () => {
    // @ts-ignore
    window.currentView = "library";
    installViewGuard(window);
    const before = currentViewToken();
    // @ts-ignore
    window.currentView = "library";
    expect(currentViewToken()).toBe(before);
  });

  test("is idempotent — second install is a no-op", () => {
    // @ts-ignore
    window.currentView = "library";
    installViewGuard(window);
    installViewGuard(window);
    // @ts-ignore
    window.currentView = "people";
    // Single bump, not double.
    expect(currentViewToken()).toBe(1);
  });
});

describe("viewFetch", () => {
  beforeEach(() => {
    _resetForTests();
    // @ts-ignore
    delete window.currentView;
    // @ts-ignore
    delete window.__viewGuardInstalled;
    // @ts-ignore
    window.currentView = "library";
    installViewGuard(window);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  test("returns parsed JSON when the view stays current", async () => {
    vi.stubGlobal("fetch", makeAwaitableFetch({ ok: true, count: 7 }, 0));
    const result = await viewFetch("/api/v1/anything");
    expect(result).toEqual({ ok: true, count: 7 });
  });

  test("returns null when view changes mid-flight (response arrives late)", async () => {
    vi.stubGlobal("fetch", makeAwaitableFetch({ stale: true }, 20));
    const promise = viewFetch("/api/v1/calendar/months");
    // Simulate the user switching views before the fetch resolves.
    // @ts-ignore
    window.currentView = "people";
    const result = await promise;
    // Either the abort fired (AbortError → null) or the token check
    // caught the change (viewStillCurrent false → null). Both yield null.
    expect(result).toBeNull();
  });

  test("aborts the in-flight fetch when the view changes", async () => {
    const fetchStub = makeAwaitableFetch({ never: "delivered" }, 1000);
    vi.stubGlobal("fetch", fetchStub);
    const promise = viewFetch("/api/v1/calendar/months");
    // @ts-ignore
    window.currentView = "calendar";
    const result = await promise;
    expect(result).toBeNull();
    // The fetch call received our composed AbortSignal and saw it abort.
    const callOpts = fetchStub.mock.calls[0][1];
    expect(callOpts.signal).toBeDefined();
    expect(callOpts.signal.aborted).toBe(true);
  });

  test("re-throws non-abort errors so callers can render an error state", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(JSON.stringify({ error: "boom" }), {
            status: 500,
            headers: { "Content-Type": "application/json" },
          })
        )
      )
    );
    await expect(viewFetch("/api/v1/anything")).rejects.toThrow();
  });

  test("a second view switch after the first creates a fresh controller", async () => {
    vi.stubGlobal("fetch", makeAwaitableFetch({ ok: true }, 0));
    // @ts-ignore
    window.currentView = "calendar";
    const tokenA = currentViewToken();
    const first = await viewFetch("/api/v1/calendar/months");
    expect(first).toEqual({ ok: true });
    // @ts-ignore
    window.currentView = "library";
    expect(currentViewToken()).toBe(tokenA + 1);
    const second = await viewFetch("/api/v1/albums");
    expect(second).toEqual({ ok: true });
  });
});

describe("viewStillCurrent helper", () => {
  beforeEach(() => {
    _resetForTests();
    // @ts-ignore
    delete window.currentView;
    // @ts-ignore
    delete window.__viewGuardInstalled;
    // @ts-ignore
    window.currentView = "library";
    installViewGuard(window);
  });

  test("returns true for the active token, false after a switch", () => {
    const t = currentViewToken();
    expect(viewStillCurrent(t)).toBe(true);
    // @ts-ignore
    window.currentView = "people";
    expect(viewStillCurrent(t)).toBe(false);
  });
});

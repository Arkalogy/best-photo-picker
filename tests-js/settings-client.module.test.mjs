// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  _setDbSettings,
  getSetting,
  loadSettings,
  saveSetting,
} from "../bpp/web/static/js/modules/settings-client.mjs";

beforeEach(() => {
  _setDbSettings({});
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getSetting", () => {
  test("returns fallback when the key is missing", () => {
    expect(getSetting("unknown", "default")).toBe("default");
    expect(getSetting("unknown", 42)).toBe(42);
  });

  test("returns the cached value when present", () => {
    _setDbSettings({ zoom_pct: "120" });
    expect(getSetting("zoom_pct", "80")).toBe("120");
  });

  test('treats undefined and "" differently — "" is a real value', () => {
    _setDbSettings({ explicit_empty: "" });
    expect(getSetting("explicit_empty", "fallback")).toBe("");
    expect(getSetting("missing", "fallback")).toBe("fallback");
  });
});

describe("loadSettings", () => {
  test("populates the cache from /api/settings response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ a: "1", b: "2" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    await loadSettings();
    expect(getSetting("a", null)).toBe("1");
    expect(getSetting("b", null)).toBe("2");
  });

  test("swallows fetch errors and leaves the cache empty", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("network down");
      })
    );
    _setDbSettings({ stale: "x" });
    await loadSettings();
    expect(getSetting("stale", "default")).toBe("default");
  });
});

describe("saveSetting", () => {
  test("updates the cache synchronously and PUTs in the background", async () => {
    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        })
    );
    vi.stubGlobal("fetch", fetchMock);

    saveSetting("zoom_pct", 100);
    // Cache is already updated even before the fetch resolves
    expect(getSetting("zoom_pct", "80")).toBe("100");

    // Let the microtask queue drain so the catch attaches
    await Promise.resolve();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/settings",
      expect.objectContaining({
        method: "PUT",
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
        body: JSON.stringify({ zoom_pct: 100 }),
      })
    );
  });

  test("coerces value to string in the cache", () => {
    saveSetting("x", 42);
    expect(getSetting("x", null)).toBe("42");
    saveSetting("x", true);
    expect(getSetting("x", null)).toBe("true");
  });
});

// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { applyTheme, initTheme, setTheme } from "../bpp/web/static/js/modules/theme.mjs";

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  // jsdom in this Vitest setup exposes localStorage as an empty object
  // without setItem/getItem/removeItem — provide a working in-memory
  // shim so applyTheme's persist call succeeds.
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => {
      store[k] = String(v);
    },
    removeItem: (k) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
  document.body.innerHTML = `
    <button class="theme-btn" data-theme="dark"></button>
    <button class="theme-btn" data-theme="light"></button>
    <button class="theme-btn" data-theme="auto"></button>
  `;
});

afterEach(() => {
  document.body.innerHTML = "";
  vi.unstubAllGlobals();
});

describe("applyTheme", () => {
  test("sets data-theme on <html> and persists to localStorage", () => {
    applyTheme("light");
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(localStorage.getItem("bpp-theme")).toBe("light");
  });

  test("flips .active class onto the matching .theme-btn only", () => {
    applyTheme("light");
    const buttons = document.querySelectorAll(".theme-btn");
    const active = Array.from(buttons).filter((b) => b.classList.contains("active"));
    expect(active).toHaveLength(1);
    expect(/** @type {HTMLElement} */ (active[0]).dataset.theme).toBe("light");
  });

  test("invokes set_app_theme via Tauri when running in Tauri", () => {
    const invoke = vi.fn();
    vi.stubGlobal("__TAURI__", { core: { invoke } });
    applyTheme("dark");
    expect(invoke).toHaveBeenCalledWith("set_app_theme", { theme: "dark" });
  });

  test("no-ops the Tauri invoke when not in Tauri", () => {
    // No Tauri global — should not throw
    expect(() => applyTheme("dark")).not.toThrow();
  });
});

describe("setTheme", () => {
  test("persists then applies", () => {
    setTheme("auto");
    expect(localStorage.getItem("bpp-theme")).toBe("auto");
    expect(document.documentElement.getAttribute("data-theme")).toBe("auto");
  });
});

describe("initTheme", () => {
  test("applies the persisted theme", () => {
    localStorage.setItem("bpp-theme", "light");
    initTheme();
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
  });

  test('defaults to "dark" on first run', () => {
    initTheme();
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });
});

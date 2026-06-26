// @ts-check
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import {
  checkForUpdates,
  dismissUpdateBanner,
  initUpdateChecker,
  manualUpdateCheck,
  showUpdateBanner,
  toggleCheckUpdates,
  updateSettingsLabel,
} from "../bpp/web/static/js/modules/update-checker.mjs";

/** @type {Record<string, string>} */
let store;

beforeEach(() => {
  vi.useFakeTimers();
  document.body.innerHTML = `
    <div id="update-banner" class="hidden">
      <span id="update-banner-text"></span>
      <a id="update-banner-link" href=""></a>
      <button class="update-banner-dismiss"></button>
    </div>
    <input type="checkbox" id="check-updates-toggle">
    <button id="check-updates-btn">Check Now</button>
    <div id="update-status-label"></div>
    <div id="toast-container"></div>
  `;
  store = {};
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
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

const banner = () => /** @type {HTMLElement} */ (document.getElementById("update-banner"));
const bannerText = () => /** @type {HTMLElement} */ (document.getElementById("update-banner-text"));

describe("showUpdateBanner", () => {
  test("populates text + link and reveals the banner", () => {
    showUpdateBanner({
      available: true,
      latest: "1.2.3",
      url: "https://example.com/release",
      current: "1.2.0",
    });
    expect(bannerText().textContent).toBe("v1.2.3 is available");
    expect(
      /** @type {HTMLAnchorElement} */ (document.getElementById("update-banner-link")).href
    ).toContain("example.com/release");
    expect(banner().classList.contains("hidden")).toBe(false);
  });

  test("falls back to '#' href when url is missing", () => {
    showUpdateBanner({ available: true, latest: "9.9.9" });
    const link = /** @type {HTMLAnchorElement} */ (document.getElementById("update-banner-link"));
    // jsdom resolves "#" against location, ending in "#"
    expect(link.href.endsWith("#")).toBe(true);
  });
});

describe("dismissUpdateBanner", () => {
  test("hides banner and remembers the dismissed version", () => {
    bannerText().textContent = "v1.2.3 is available";
    banner().classList.remove("hidden");
    dismissUpdateBanner();
    expect(banner().classList.contains("hidden")).toBe(true);
    expect(localStorage.getItem("bpp_update_dismissed")).toBe("1.2.3");
  });

  test("no-op on banner without a recognizable version string", () => {
    bannerText().textContent = "no version here";
    banner().classList.remove("hidden");
    dismissUpdateBanner();
    expect(banner().classList.contains("hidden")).toBe(true);
    expect(localStorage.getItem("bpp_update_dismissed")).toBeNull();
  });
});

describe("toggleCheckUpdates", () => {
  test('writes "true" / "false" to localStorage', () => {
    toggleCheckUpdates(true);
    expect(localStorage.getItem("bpp_check_updates")).toBe("true");
    toggleCheckUpdates(false);
    expect(localStorage.getItem("bpp_check_updates")).toBe("false");
  });
});

describe("updateSettingsLabel", () => {
  test("'available' wording", () => {
    updateSettingsLabel({ available: true, latest: "2.0.0", current: "1.9.0" });
    expect(document.getElementById("update-status-label").textContent).toBe(
      "v2.0.0 available (you have v1.9.0)"
    );
  });

  test("'up to date' wording", () => {
    updateSettingsLabel({ available: false, current: "1.9.0" });
    expect(document.getElementById("update-status-label").textContent).toBe("Up to date (v1.9.0)");
  });
});

describe("checkForUpdates", () => {
  test("respects the disabled toggle (no fetch)", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    localStorage.setItem("bpp_check_updates", "false");
    checkForUpdates(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("force=true fetches even when disabled", () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    localStorage.setItem("bpp_check_updates", "false");
    checkForUpdates(true);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/update/check?force=1", expect.any(Object));
  });

  test("shows banner when update is available + new version", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              available: true,
              latest: "9.0.0",
              current: "1.0.0",
              url: "https://example.com",
            }),
            { status: 200, headers: { "content-type": "application/json" } }
          )
      )
    );
    checkForUpdates(false);
    // Wait for the apiFetch promise to resolve
    await vi.runOnlyPendingTimersAsync();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(banner().classList.contains("hidden")).toBe(false);
    expect(bannerText().textContent).toBe("v9.0.0 is available");
  });

  test("skips banner when the latest was already dismissed", async () => {
    localStorage.setItem("bpp_update_dismissed", "9.0.0");
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ available: true, latest: "9.0.0", current: "1.0.0" }), {
            status: 200,
            headers: { "content-type": "application/json" },
          })
      )
    );
    checkForUpdates(false);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(banner().classList.contains("hidden")).toBe(true);
  });
});

describe("manualUpdateCheck", () => {
  test("disables + relabels while checking, restores when fetch settles", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 }))
    );
    const promise = manualUpdateCheck();
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("check-updates-btn"));
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toBe("Checking…");
    await promise;
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toBe("Check Now");
  });

  test("restores the button even when the fetch fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("net");
      })
    );
    const promise = manualUpdateCheck();
    const btn = /** @type {HTMLButtonElement} */ (document.getElementById("check-updates-btn"));
    expect(btn.disabled).toBe(true);
    await promise;
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toBe("Check Now");
  });
});

describe("initUpdateChecker", () => {
  test("hydrates the toggle from localStorage and schedules a check", () => {
    localStorage.setItem("bpp_check_updates", "true");
    initUpdateChecker();
    const toggle = /** @type {HTMLInputElement} */ (
      document.getElementById("check-updates-toggle")
    );
    expect(toggle.checked).toBe(true);
    // setTimeout is queued — advance fake timers to trigger it
    expect(vi.getTimerCount()).toBeGreaterThan(0);
  });

  test("disabled toggle skips scheduling", () => {
    localStorage.setItem("bpp_check_updates", "false");
    initUpdateChecker();
    expect(vi.getTimerCount()).toBe(0);
  });
});

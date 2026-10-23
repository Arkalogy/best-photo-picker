// @ts-check
// Opening Settings loads the Models picker once: when the Models tab is
// the one showing, on open; otherwise when that tab is clicked. The
// legacy list used to load the picker on every open and the tab switch
// loaded it again — two fetches of the same install state.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

vi.mock("../bpp/web/static/js/modules/modals-face-embedders.mjs", () => ({
  loadFaceEmbedderPicker: vi.fn(async () => {}),
}));

import { loadFaceEmbedderPicker } from "../bpp/web/static/js/modules/modals-face-embedders.mjs";
import { showSettings, switchSettingsTab } from "../bpp/web/static/js/modules/modals.mjs";

const picker = vi.mocked(loadFaceEmbedderPicker);

beforeEach(() => {
  document.body.innerHTML = `
    <div id="settings-overlay">
      <button class="settings-tab active" data-tab="app">App</button>
      <button class="settings-tab" data-tab="models">Models</button>
      <div id="settings-pane-app" class="settings-tab-pane active"></div>
      <div id="settings-pane-models" class="settings-tab-pane"></div>
    </div>`;
  picker.mockClear();
  /** @type {Record<string, string>} */
  const store = {};
  vi.stubGlobal("localStorage", {
    getItem: (/** @type {string} */ k) => (k in store ? store[k] : null),
    setItem: (/** @type {string} */ k, /** @type {string} */ v) => {
      store[k] = String(v);
    },
    removeItem: (/** @type {string} */ k) => {
      delete store[k];
    },
    clear: () => {
      for (const k of Object.keys(store)) delete store[k];
    },
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response("{}", { headers: { "content-type": "application/json" } }))
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Settings → Models picker load", () => {
  test("not loaded on open while another tab shows; once when Models is clicked", () => {
    showSettings();
    expect(picker).not.toHaveBeenCalled();
    switchSettingsTab("models");
    expect(picker).toHaveBeenCalledTimes(1);
  });

  test("loaded once on open when Models is the tab already showing", () => {
    switchSettingsTab("models");
    picker.mockClear();
    showSettings();
    expect(picker).toHaveBeenCalledTimes(1);
  });

  test("opening straight onto Models (the analysis preflight) loads it once", () => {
    showSettings("models");
    expect(picker).toHaveBeenCalledTimes(1);
    expect(document.getElementById("settings-pane-models")?.classList.contains("active")).toBe(
      true
    );
  });
});
